#!/usr/bin/env python3
"""Prepare exact public tool versions in a NEW, unprivileged directory.

An administrator reviews and installs this result read-only under /opt. This
script never changes system packages or security settings and never runs npm.
"""
import base64
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import platform
import sys
import tarfile
import urllib.parse
import urllib.request

from amber_sync import SyncError, atomic_json, confined, require
from site_builder import NODE, PNPM


def allowed(url):
    parsed = urllib.parse.urlsplit(url)
    require(parsed.scheme == "https" and not parsed.username and not parsed.password and
            parsed.port in (None, 443) and parsed.hostname in ("nodejs.org", "registry.npmjs.org"),
            "TOOL_HOST_REFUSED")


class Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        allowed(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def get(url, limit):
    allowed(url)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), Redirect())
    with opener.open(url, timeout=30) as response:
        data = response.read(limit + 1)
        require(len(data) <= limit, "TOOL_DOWNLOAD_TOO_LARGE")
        return data


def extract_pnpm(data, destination):
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        total = 0
        for member in archive.getmembers():
            path = PurePosixPath(member.name)
            require(path.parts and path.parts[0] == "package" and ".." not in path.parts and
                    "\\" not in member.name and (member.isdir() or member.isfile()), "UNSAFE_TOOL_ARCHIVE")
            if len(path.parts) == 1:
                continue
            total += member.size
            require(total < 512 * 1024 ** 2, "TOOL_ARCHIVE_TOO_LARGE")
            target = confined(destination, str(Path(*path.parts[1:])))
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as source, target.open("xb") as output:
                    output.write(source.read())


def prepare(output):
    arch = {"x86_64": "x64", "aarch64": "arm64"}.get(platform.machine())
    require(platform.system() == "Linux" and arch, "LINUX_X64_OR_ARM64_REQUIRED")
    require(output.is_absolute() and not output.exists(), "NEW_ABSOLUTE_TOOL_DIRECTORY_REQUIRED")
    output.mkdir(parents=True, mode=0o700)
    filename = f"node-v{NODE}-linux-{arch}.tar.xz"
    base = f"https://nodejs.org/dist/v{NODE}/"
    sums = get(base + "SHASUMS256.txt", 1024 ** 2).decode()
    matches = [line.split()[0] for line in sums.splitlines() if line.split()[-1] == filename]
    require(len(matches) == 1, "NODE_CHECKSUM_MISSING")
    node = get(base + filename, 100 * 1024 ** 2)
    require(hashlib.sha256(node).hexdigest() == matches[0], "NODE_CHECKSUM_MISMATCH")
    executable = output / "node/bin/node"
    executable.parent.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(node), mode="r:xz") as archive:
        member = archive.getmember(filename.removesuffix(".tar.xz") + "/bin/node")
        require(member.isfile() and member.size < 200 * 1024 ** 2, "INVALID_NODE_EXECUTABLE")
        executable.write_bytes(archive.extractfile(member).read())
    executable.chmod(0o755)
    metadata = json.loads(get(f"https://registry.npmjs.org/pnpm/{PNPM}", 2 * 1024 ** 2))
    require(metadata.get("name") == "pnpm" and metadata.get("version") == PNPM, "PNPM_IDENTITY_MISMATCH")
    dist = metadata["dist"]
    require(dist["tarball"] == f"https://registry.npmjs.org/pnpm/-/pnpm-{PNPM}.tgz" and
            dist["integrity"].startswith("sha512-"), "PNPM_INTEGRITY_MISSING")
    pnpm = get(dist["tarball"], 50 * 1024 ** 2)
    expected = base64.b64decode(dist["integrity"][7:], validate=True)
    require(hashlib.sha512(pnpm).digest() == expected, "PNPM_INTEGRITY_MISMATCH")
    destination = output / "pnpm"
    destination.mkdir()
    extract_pnpm(pnpm, destination)
    package = json.loads((destination / "package.json").read_text())
    require(package["name"] == "pnpm" and package["version"] == PNPM, "PNPM_PACKAGE_MISMATCH")
    atomic_json(output / "tool-receipt.json", {"node": NODE, "pnpm": PNPM, "arch": arch,
                "node_sha256": matches[0], "pnpm_integrity": dist["integrity"]}, 0o644)
    print("Tools prepared; administrator must inspect and install them read-only.")


if __name__ == "__main__":
    try:
        require(len(sys.argv) == 2, "EXPECTED_NEW_OUTPUT_DIRECTORY")
        prepare(Path(sys.argv[1]))
    except Exception as exc:
        print(str(exc) if isinstance(exc, SyncError) else type(exc).__name__, file=sys.stderr)
        sys.exit(1)
