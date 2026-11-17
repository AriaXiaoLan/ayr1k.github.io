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
import socket
import socketserver
import sys
import threading

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
    # Positive control: a HOST loopback listener is reachable by the validator.
    # Both installation and build must fail to reach this exact live listener.
    class HostControl(socketserver.BaseRequestHandler):
        def handle(self): self.request.sendall(b'host-control')
    control=socketserver.ThreadingTCPServer(('127.0.0.1',0),HostControl)
    control.daemon_threads=True
    threading.Thread(target=control.serve_forever,daemon=True).start()
    port=control.server_address[1]
    with socket.create_connection(('127.0.0.1',port),timeout=2) as client:
        require(client.recv(32)==b'host-control','HOST_NETWORK_POSITIVE_CONTROL_FAILED')
    targets=[{'label':'host-loopback','ip':'127.0.0.1','port':port},
             {'label':'rfc1918','ip':'10.0.0.1','port':80},
             {'label':'link-local-metadata','ip':'169.254.169.254','port':80},
             {'label':'cgnat-metadata','ip':'100.88.222.5','port':80},
             {'label':'ipv6-loopback','ip':'::1','port':port},
             {'label':'ipv6-metadata','ip':'fd00:ec2::254','port':80},
             {'label':'public-ip','ip':'1.1.1.1','port':443}]
    (source/'.amber-network-probes.json').write_text(json.dumps({
        'host_netns':os.readlink('/proc/self/ns/net'),'targets':targets}))
    (source/'.pnpmfile.cjs').write_text("throw new Error('pnpmfile must not execute during online installation')")
    (source / 'sandbox-boundary-check.mjs').write_text("""
import assert from 'node:assert/strict';
import fs from 'node:fs';
import net from 'node:net';
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
const targets=JSON.parse(fs.readFileSync('.amber-network-probes.json')).targets;
for(const target of targets){
 const blocked=await new Promise(resolve=>{
  const connection=net.createConnection({host:target.ip,port:target.port});
  connection.once('connect',()=>{connection.destroy();resolve(false);});
  connection.once('error',()=>resolve(true));
  connection.setTimeout(1500,()=>{connection.destroy();resolve(true);});
 });
 assert.equal(blocked,true,'offline child could reach '+target.label);
}
assert.equal(fs.existsSync('/proxy/egress.sock'),false);
console.log('Independent UID/filesystem/clean environment and offline Node socket denial verified');
""".replace('__SENTINEL_PATH__', json.dumps(str(sentinel))).replace('__CALLER_UID__',str(os.getuid())), encoding='utf-8')
    package = json.loads((source / 'package.json').read_text())
    package['scripts']['test:mirror'] += ' && node sandbox-boundary-check.mjs'
    package['scripts']['preinstall'] = 'node -e "throw new Error(\'online lifecycle script executed\')"'
    (source / 'package.json').write_text(json.dumps(package), encoding='utf-8')
    os.environ['AMBER_SANDBOX_SENTINEL'] = 'non-secret fixture'
    try:
        static = request_build(engine, source, 'c' * 40)
        request=json.loads(Path('/srv/amber-web-requests/request.json').read_text())
        result=json.loads((Path('/srv/amber-web-build/results')/request['id']/'result.json').read_text())
        for stage in ('install','build'):
            require(len(result['network'][stage]['probes'])==len(targets),'NETWORK_PROBE_RECEIPTS_MISSING')
        require(len(result['network']['install']['proxy_denials'])==8,'PROXY_DENIAL_RECEIPTS_MISSING')
        print('REAL_NETWORK_ISOLATION_EVIDENCE',json.dumps(result['network'],sort_keys=True))
        print('Real restricted registry install + offline build/tests + UID/filesystem/environment isolation passed:',validate_static(static))
    finally:
        control.shutdown()
        control.server_close()


if __name__ == '__main__':
    main()
