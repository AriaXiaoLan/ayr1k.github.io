#!/usr/bin/env python3
"""Explicit integration check; no service installation or production writes.

Requires official bubblewrap, previously prepared pinned tools and a NEW scratch
directory. Runs real frozen dependency installation/build/tests in the sandbox.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from amber_sync import Engine, require
from site_builder import sandbox_build, validate_static


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tools', type=Path, required=True)
    parser.add_argument('--scratch', type=Path, required=True)
    args = parser.parse_args()
    require(args.scratch.is_absolute() and not args.scratch.exists(), 'NEW_SCRATCH_DIRECTORY_REQUIRED')
    require(shutil.which('bwrap'), 'BUBBLEWRAP_REQUIRED')
    args.scratch.mkdir(mode=0o700)
    engine = Engine({'root':str(args.scratch / 'runtime'), 'tools':str(args.tools), 'reserve_bytes':0})
    sentinel = engine.private / 'sandbox-boundary-sentinel'
    sentinel.write_text('non-secret fixture; must not be visible')
    source = args.scratch / 'source'
    shutil.copytree(Path(__file__).resolve().parents[2], source, ignore=shutil.ignore_patterns(
        '.git', '.qa', 'node_modules', 'dist', '__pycache__', '*.log'))
    # Only the disposable source is instrumented. This file never runs on the host.
    (source / 'sandbox-boundary-check.mjs').write_text('''
import assert from 'node:assert/strict';
import fs from 'node:fs';
assert.equal(fs.existsSync(__SENTINEL_PATH__),false);
assert.equal(fs.existsSync('/root/.ssh'),false);
assert.equal(process.env.AMBER_SANDBOX_SENTINEL,undefined);
assert.equal(process.env.SSH_AUTH_SOCK,undefined);
assert.equal(process.env.GITHUB_TOKEN,undefined);
assert.throws(()=>fs.writeFileSync('/usr/amber-sandbox-write-probe','fixture'));
console.log('Sandbox state isolation, read-only system and cleared environment verified');
'''.replace('__SENTINEL_PATH__', json.dumps(str(sentinel))), encoding='utf-8')
    package = json.loads((source / 'package.json').read_text())
    package['scripts']['test:mirror'] += ' && node sandbox-boundary-check.mjs'
    (source / 'package.json').write_text(json.dumps(package), encoding='utf-8')
    os.environ['AMBER_SANDBOX_SENTINEL'] = 'non-secret fixture'
    sandbox_build(engine, source)
    print('Real sandbox install/build/tests and static validation passed:', validate_static(source / 'dist/client'))


if __name__ == '__main__':
    main()
