"""Publication allocation planning and guarded writes to NEW staging files only."""
import io
import os
from pathlib import Path

from amber_sync import SyncError, require

MARGIN = 1024 * 1024  # Leave additional headroom for directory/journal metadata.
CHUNK = 1024 * 1024


class PublicationSpace:
    def __init__(self, engine):
        self.engine = engine
        self.block = os.statvfs(engine.root).f_frsize if hasattr(os, 'statvfs') else 4096
        self.block = max(self.block, 4096)

    def allocated(self, size):
        return ((size + self.block - 1) // self.block) * self.block

    def check(self, required=0):
        self.engine.space(required + MARGIN)

    def directory(self, path):
        if not path.exists():
            self.check(self.block)
            path.mkdir()
            self.check()

    def _write(self, source, destination, size):
        self.check(self.allocated(size))
        require(not destination.exists() and not destination.is_symlink(), 'STAGING_FILE_EXISTS')
        with destination.open('xb') as output:
            try:
                # Reserve the entire file before writing. No delayed allocation
                # can later consume the system reserve behind these checks.
                require(hasattr(os, 'posix_fallocate'), 'SPACE_RESERVATION_UNSUPPORTED')
                if size:
                    try:
                        os.posix_fallocate(output.fileno(), 0, size)
                    except OSError:
                        raise SyncError('PUBLICATION_RESERVATION_FAILED') from None
                self.check()
                remaining = size
                while remaining:
                    data = source.read(min(CHUNK, remaining))
                    require(data, 'STATIC_SOURCE_SHRANK')
                    self.check()
                    output.write(data)
                    remaining -= len(data)
                    self.check()
                require(not source.read(1), 'STATIC_SOURCE_GREW')
                output.flush()
                os.fsync(output.fileno())
                self.check()
            except BaseException:
                # Release only the allocation created by THIS copy. The source
                # and all active/previous/managed files are never truncated.
                output.truncate(0)
                output.flush()
                os.fsync(output.fileno())
                raise

    def copy(self, source, destination, size):
        with source.open('rb') as handle:
            self._write(handle, destination, size)

    def write(self, data, destination):
        self._write(io.BytesIO(data), destination, len(data))
