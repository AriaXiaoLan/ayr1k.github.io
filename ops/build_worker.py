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
import traceback
import uuid

from amber_sync import (SyncError, atomic_json, confined, digest, locked, log,
                        require, sync_tree)
from site_builder import COMMIT, copy_static, extract_source, isolated_build, source_pins, validate_static, install_dependencies

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
    require(result.get('schema') == 2 and result.get('id') == job_id and
            result.get('commit') == commit, 'BUILD_RESULT_IDENTITY_MISMATCH')
    if result.get('ok') is not True:
        code = result.get('code', '')
        require(re.fullmatch(r'[A-Z_]{1,80}', code), 'INVALID_BUILD_FAILURE_CODE')
        raise SyncError(code)
    require(result.get('pins') == pins, 'BUILD_RESULT_PINS_MISMATCH')
    network=result.get('network',{})
    online,offline=network.get('install',{}),network.get('build',{})
    require(online.get('phase')=='install' and offline.get('phase')=='offline' and
            online.get('direct_network_blocked') is True and offline.get('direct_network_blocked') is True and
            online.get('registry_proxy')=='registry.npmjs.org:443' and
            offline.get('ip_socket_families_blocked') is True and offline.get('proxy_mounted') is False and
            online.get('namespace') and offline.get('namespace') and
            online['namespace']!=offline['namespace'], 'BUILD_NETWORK_RECEIPT_MISSING')
    static = confined(result_root, job_id + '/site')
    require(static_hashes(static) == result.get('files'), 'BUILD_RESULT_INTEGRITY')
    # Copy out of the other identity's storage, then recheck every byte. Never
    # publish a tree still controlled by a build process or containing links.
    candidate = engine.private / 'staging' / uuid.uuid4().hex
    copy_static(static, candidate)
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
    request = {'schema':2, 'id':job_id, 'commit':commit, 'source_sha256':digest(archive_path), 'pins':pins}
    atomic_json(requests / 'request.json', request, 0o640)
    started = time.monotonic()
    deadline = started + engine.config.get('builder_timeout_seconds', 1320)
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
        # The prior invocation may still be marked failed while systemd.path
        # processes this new request. Allow it time to start the new job.
        if state == 'failed' and time.monotonic() - started >= 10:
            raise SyncError('BUILD_SERVICE_FAILED')
        time.sleep(1)
    raise SyncError('BUILD_SERVICE_TIMEOUT')


def job_context():
    root,requests,tools=Path('/build'),Path('/requests'),Path('/tools')
    marker=Path('/.amber-build-jail')
    require(os.name=='posix' and os.getuid()!=0 and marker.is_file() and marker.stat().st_uid==0 and
            marker.read_text().strip()=='AMBER build jail v1','SYSTEMD_BUILD_JAIL_REQUIRED')
    request=json.loads((requests/'request.json').read_text())
    job_id,commit=request.get('id',''),request.get('commit','')
    require(request.get('schema')==2 and ID.fullmatch(job_id) and COMMIT.fullmatch(commit),
            'INVALID_BUILD_REQUEST')
    result_dir=confined(root,'results/'+job_id)
    result_dir.mkdir(parents=True,mode=0o750)
    return root,requests,tools,request,result_dir


def failed_result(result_dir,request,exc):
    code=str(exc) if isinstance(exc,SyncError) else 'BUILD_WORKER_INTERNAL_ERROR'
    frames=[{'file':Path(f.filename).name,'line':f.lineno,'function':f.name}
            for f in traceback.extract_tb(exc.__traceback__)[-6:]]
    log('build_failed',id=request['id'],code=code,error_type=type(exc).__name__,frames=frames)
    atomic_json(result_dir/'result.json',{'schema':2,'id':request['id'],'commit':request['commit'],
                                       'ok':False,'code':code},0o640)
    raise SyncError(code)


def install_once():
    root,requests,tools,request,result_dir=job_context()
    if (result_dir/'result.json').exists() or (result_dir/'installed.json').exists(): return
    try:
        require(shutil.disk_usage(root).free>=4*1024**3,'INSUFFICIENT_BUILD_DISK_SPACE')
        archive=confined(requests,'jobs/'+request['id']+'/source.tar')
        require(archive.stat().st_size<=512*1024**2 and digest(archive)==request['source_sha256'],
                'BUILD_SOURCE_ARCHIVE_INTEGRITY')
        work=confined(root,'work/'+request['id'])
        require(not work.exists(),'INCOMPLETE_BUILD_JOB_REQUIRES_NEW_ID')
        work.mkdir(parents=True,mode=0o750)
        source=work/'source'
        source.mkdir(mode=0o750)
        extract_source(archive.read_bytes(),source)
        pins=source_pins(source)
        require(pins==request['pins'],'BUILD_SOURCE_PINS_MISMATCH')
        network=install_dependencies(source,tools)
        require(source_pins(source)==pins,'INSTALL_CHANGED_DOWNLOAD_CATALOG')
        atomic_json(result_dir/'installed.json',{'schema':2,'id':request['id'],'commit':request['commit'],
            'source_sha256':request['source_sha256'],'pins':pins,'lock_sha256':digest(source/'pnpm-lock.yaml'),
            'network':network},0o640)
    except Exception as exc:
        failed_result(result_dir,request,exc)


def work_once():
    root,requests,tools,request,result_dir=job_context()
    if (result_dir/'result.json').exists(): return
    try:
        installed=json.loads((result_dir/'installed.json').read_text())
        require(installed.get('schema')==2 and installed.get('id')==request['id'] and
                installed.get('commit')==request['commit'] and
                installed.get('source_sha256')==request['source_sha256'],'INSTALL_RECEIPT_MISMATCH')
        source=confined(root,'work/'+request['id']+'/source')
        pins=source_pins(source)
        require(pins==request['pins'] and pins==installed['pins'] and
                digest(source/'pnpm-lock.yaml')==installed['lock_sha256'],'INSTALLED_SOURCE_CHANGED')
        network=isolated_build(source,tools)
        require(source_pins(source)==pins,'BUILD_CHANGED_DOWNLOAD_CATALOG')
        static=source/'dist/client'
        files=static_hashes(static)
        destination=result_dir/'site'
        copy_static(static,destination)
        require(static_hashes(destination)==files,'BUILD_OUTPUT_COPY_INTEGRITY')
        sync_tree(destination)
        atomic_json(result_dir/'result.json',{'schema':2,'id':request['id'],'commit':request['commit'],
            'ok':True,'files':files,'pins':pins,'network':{'install':installed['network'],'build':network}},0o640)
    except Exception as exc:
        failed_result(result_dir,request,exc)


if __name__=='__main__':
    try:
        require(len(sys.argv)==2 and sys.argv[1] in ('install','build'),'EXPECTED_FIXED_BUILD_PHASE')
        with locked(Path('/build/worker.lock')):
            (install_once if sys.argv[1]=='install' else work_once)()
    except Exception as exc:
        log('build_worker_fatal',code=str(exc) if isinstance(exc,SyncError) else type(exc).__name__)
        sys.exit(1)
