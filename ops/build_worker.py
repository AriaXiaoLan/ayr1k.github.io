#!/usr/bin/env python3
"""One-way request/result protocol for a separate, systemd-jailed build identity.

The sync account cannot start privileged commands. A root-owned systemd.path
watches an exact request file and starts this fixed worker as amber-build.
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import time
import uuid

from amber_sync import (SyncError, atomic_json, confined, digest, locked, log,
                        require, sync_tree)
from site_builder import COMMIT, extract_source, isolated_build, source_pins, validate_static

ID = re.compile(r"[a-f0-9]{32}\Z")


def static_hashes(directory):
    validate_static(directory)
    return {p.relative_to(directory).as_posix(): digest(p)
            for p in sorted(directory.rglob('*')) if p.is_file()}


def service_state():
    try:
        result = subprocess.run(['systemctl', 'is-active', 'amber-web-build.service'],
            capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        raise SyncError('BUILD_SERVICE_UNAVAILABLE') from None
    value = result.stdout.strip()
    require(value in ('active', 'activating', 'deactivating', 'inactive', 'failed'),
            'BUILD_SERVICE_UNAVAILABLE')
    return value


def accept_result(engine, result_root, job_id, commit, pins):
    result_path = confined(result_root, job_id + '/result.json')
    result = json.loads(result_path.read_text())
    require(result.get('schema') == 1 and result.get('id') == job_id and
            result.get('commit') == commit, 'BUILD_RESULT_IDENTITY_MISMATCH')
    if result.get('ok') is not True:
        code = result.get('code', '')
        require(re.fullmatch(r'[A-Z_]{1,80}', code), 'INVALID_BUILD_FAILURE_CODE')
        raise SyncError(code)
    require(result.get('pins') == pins, 'BUILD_RESULT_PINS_MISMATCH')
    static = confined(result_root, job_id + '/site')
    require(static_hashes(static) == result.get('files'), 'BUILD_RESULT_INTEGRITY')
    # Copy out of the other identity's storage, then recheck every byte. Never
    # publish a tree still controlled by a build process or containing links.
    candidate = engine.private / 'staging' / uuid.uuid4().hex
    shutil.copytree(static, candidate, symlinks=False)
    require(static_hashes(candidate) == result['files'], 'BUILD_RESULT_CHANGED_DURING_COPY')
    return candidate


def request_build(engine, source, commit):
    require(COMMIT.fullmatch(commit), 'INVALID_BUILD_COMMIT')
    requests = Path(engine.config['builder_requests'])
    results = Path(engine.config['builder_results'])
    for root in (requests, results):
        require(root.is_absolute() and root.is_dir() and not root.is_symlink(), 'BUILDER_NOT_PROVISIONED')
    job_id = uuid.uuid4().hex
    job = confined(requests, 'jobs/' + job_id)
    job.mkdir(parents=True, mode=0o750)
    # Administrator provisions the request root setgid to amber-build. Do not
    # change groups or grant new permissions from this unprivileged process.
    require(job.stat().st_gid == requests.stat().st_gid, 'REQUEST_GROUP_NOT_INHERITED')
    archive_path = job / 'source.tar'
    with tarfile.open(archive_path, mode='w') as archive:
        for path in sorted(source.rglob('*')):
            require(not path.is_symlink() and (path.is_file() or path.is_dir()), 'SOURCE_SPECIAL_FILE')
            archive.add(path, arcname=path.relative_to(source).as_posix(), recursive=False)
    archive_path.chmod(0o640)
    sync_tree(job)
    pins = source_pins(source)
    request = {'schema':1, 'id':job_id, 'commit':commit, 'source_sha256':digest(archive_path), 'pins':pins}
    atomic_json(requests / 'request.json', request, 0o640)
    deadline = time.monotonic() + engine.config.get('builder_timeout_seconds', 900)
    result_path = confined(results, job_id + '/result.json')
    while time.monotonic() < deadline:
        state = service_state()
        if result_path.is_file() and state in ('inactive', 'failed'):
            # Wait until systemd has killed all descendants of the build unit.
            # A failed service can report its fixed error code, never success.
            if state == 'failed':
                result = json.loads(result_path.read_text())
                require(result.get('ok') is not True, 'BUILD_SERVICE_FAILED_AFTER_RESULT')
            return accept_result(engine, results, job_id, commit, pins)
        if state == 'failed':
            raise SyncError('BUILD_SERVICE_FAILED')
        time.sleep(1)
    raise SyncError('BUILD_SERVICE_TIMEOUT')


def work_once():
    root, requests, tools = Path('/build'), Path('/requests'), Path('/tools')
    marker = Path('/.amber-build-jail')
    require(os.name == 'posix' and os.getuid() != 0 and marker.is_file() and
            marker.stat().st_uid == 0 and marker.read_text().strip() == 'AMBER build jail v1',
            'SYSTEMD_BUILD_JAIL_REQUIRED')
    request = json.loads((requests / 'request.json').read_text())
    job_id, commit = request.get('id', ''), request.get('commit', '')
    require(request.get('schema') == 1 and ID.fullmatch(job_id) and COMMIT.fullmatch(commit),
            'INVALID_BUILD_REQUEST')
    result_dir = confined(root, 'results/' + job_id)
    result_dir.mkdir(parents=True, mode=0o750)
    result_path = result_dir / 'result.json'
    if result_path.exists():
        return  # An already completed immutable job is never rebuilt/overwritten.
    try:
        require(shutil.disk_usage(root).free >= 4 * 1024 ** 3, 'INSUFFICIENT_BUILD_DISK_SPACE')
        archive = confined(requests, 'jobs/' + job_id + '/source.tar')
        require(archive.stat().st_size <= 512 * 1024 ** 2 and digest(archive) == request['source_sha256'],
                'BUILD_SOURCE_ARCHIVE_INTEGRITY')
        work = confined(root, 'work/' + job_id)
        work.mkdir(parents=True, mode=0o750)
        source = work / 'source'
        source.mkdir(mode=0o750)
        extract_source(archive.read_bytes(), source)
        pins = source_pins(source)
        require(pins == request['pins'], 'BUILD_SOURCE_PINS_MISMATCH')
        isolated_build(source, tools)
        require(source_pins(source) == pins, 'BUILD_CHANGED_DOWNLOAD_CATALOG')
        static = source / 'dist/client'
        files = static_hashes(static)
        destination = result_dir / 'site'
        shutil.copytree(static, destination, symlinks=False)
        require(static_hashes(destination) == files, 'BUILD_OUTPUT_COPY_INTEGRITY')
        for path in destination.rglob('*'):
            path.chmod(0o750 if path.is_dir() else 0o640)
        destination.chmod(0o750)
        sync_tree(destination)
        result = {'schema':1, 'id':job_id, 'commit':commit, 'ok':True, 'files':files, 'pins':pins}
    except Exception as exc:
        code = str(exc) if isinstance(exc, SyncError) else 'BUILD_WORKER_INTERNAL_ERROR'
        result = {'schema':1, 'id':job_id, 'commit':commit, 'ok':False, 'code':code}
        log('build_failed', id=job_id, code=code)
    atomic_json(result_path, result, 0o640)
    require(result['ok'], result.get('code', 'BUILD_FAILED'))


if __name__ == '__main__':
    try:
        with locked(Path('/build/worker.lock')):
            work_once()
    except Exception as exc:
        log('build_worker_fatal', code=str(exc) if isinstance(exc, SyncError) else type(exc).__name__)
        sys.exit(1)
