#!/usr/bin/env python3
"""Verify the installed, independently jailed worker from an unprivileged account.

Requires administrator-provisioned units/tools/request directories and a NEW
scratch directory. Never installs services or changes system security settings.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from amber_sync import Engine, require
from build_worker import request_build
from site_builder import validate_static


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scratch', type=Path, required=True)
    args = parser.parse_args()
    require(os.name == 'posix' and os.getuid() != 0, 'UNPRIVILEGED_TEST_ACCOUNT_REQUIRED')
    require(args.scratch.is_absolute() and not args.scratch.exists(), 'NEW_SCRATCH_DIRECTORY_REQUIRED')
    args.scratch.mkdir(mode=0o700)
    engine = Engine({'root':str(args.scratch / 'runtime'), 'reserve_bytes':0,
        'builder_requests':'/srv/amber-web-requests', 'builder_results':'/srv/amber-web-build/results'})
    sentinel = engine.private / 'sandbox-boundary-sentinel'
    sentinel.write_text('non-secret fixture; must not be visible')
    source = args.scratch / 'source'
    shutil.copytree(Path(__file__).resolve().parents[2], source, ignore=shutil.ignore_patterns(
        '.git', '.qa', 'node_modules', 'dist', '__pycache__', '*.log'))
    (source / 'sandbox-boundary-check.mjs').write_text("""
import assert from 'node:assert/strict';
import fs from 'node:fs';
assert.notEqual(process.getuid(),0);
assert.notEqual(process.getuid(),__CALLER_UID__);
assert.equal(fs.existsSync(__SENTINEL_PATH__),false);
assert.equal(fs.existsSync('/srv/amber-sync'),false);
assert.equal(fs.existsSync('/etc/letsencrypt'),false);
assert.equal(fs.existsSync('/root/.ssh'),false);
assert.equal(process.env.AMBER_SANDBOX_SENTINEL,undefined);
assert.equal(process.env.SSH_AUTH_SOCK,undefined);
assert.equal(process.env.GITHUB_TOKEN,undefined);
assert.throws(()=>fs.writeFileSync('/usr/amber-sandbox-write-probe','fixture'));
assert.throws(()=>fs.writeFileSync('/requests/request.json','fixture'));
console.log('Independent UID, jailed filesystem, read-only requests and clean environment verified');
""".replace('__SENTINEL_PATH__', json.dumps(str(sentinel))).replace('__CALLER_UID__',str(os.getuid())), encoding='utf-8')
    package = json.loads((source / 'package.json').read_text())
    package['scripts']['test:mirror'] += ' && node sandbox-boundary-check.mjs'
    (source / 'package.json').write_text(json.dumps(package), encoding='utf-8')
    os.environ['AMBER_SANDBOX_SENTINEL'] = 'non-secret fixture'
    static = request_build(engine, source, 'c' * 40)
    print('Real worker install/build/tests, UID/filesystem/environment isolation and static validation passed:', validate_static(static))


if __name__ == '__main__':
    main()
