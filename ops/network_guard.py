"""Fail-closed namespace/family checks, plus bounded integration probe receipts."""
import json
import os
from pathlib import Path
import socket

from amber_sync import require


def proxy_denials():
    denied=[]
    for label,authority in (
        ('host-loopback','127.0.0.1:443'),('rfc1918','10.0.0.1:443'),
        ('link-local-metadata','169.254.169.254:80'),('cgnat-metadata','100.88.222.5:80'),
        ('ipv6-loopback','[::1]:443'),('ipv6-metadata','[fd00:ec2::254]:443'),
        ('unapproved-domain','example.invalid:443'),('wrong-port','registry.npmjs.org:80')):
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:
            connection.settimeout(10)
            connection.connect('/proxy/egress.sock')
            connection.sendall(('CONNECT '+authority+' HTTP/1.1\r\n\r\n').encode())
            require(connection.recv(128).startswith(b'HTTP/1.1 403 '),'REGISTRY_PROXY_DENIAL_REQUIRED')
        denied.append(label)
    return denied


def network_guard(source,phase):
    require(phase in ('install','offline'),'INVALID_BUILD_PHASE')
    namespace=os.readlink('/proc/self/ns/net')
    if phase=='install':
        # If systemd could not create a private namespace, do not fall back to
        # host networking. It must contain only its own loopback interface.
        require({name for _,name in socket.if_nameindex()}=={'lo'},'INSTALL_NETWORK_NAMESPACE_REQUIRED')
        denials=proxy_denials()
    else:
        # A previous unit may leave an EMPTY mountpoint in the shared jail.
        # Reject an actual mount or socket, without mistaking that directory for egress.
        require(not os.path.ismount('/proxy') and not Path('/proxy/egress.sock').exists(),
                'OFFLINE_PROXY_MOUNT_PRESENT')
        for family in (socket.AF_INET,socket.AF_INET6):
            try:
                connection=socket.socket(family,socket.SOCK_STREAM)
            except OSError:
                continue
            connection.close()
            require(False,'OFFLINE_SOCKET_FAMILY_NOT_BLOCKED')
    probes=source/'.amber-network-probes.json'
    results=[]
    if probes.exists():
        data=json.loads(probes.read_text())
        require(data['host_netns']!=namespace,'HOST_NETWORK_NAMESPACE_EXPOSED')
        require(isinstance(data['targets'],list) and len(data['targets'])<=12,'INVALID_NETWORK_PROBES')
        for target in data['targets']:
            # Test fixtures supply literal IPs only; never resolve attacker names.
            import ipaddress
            ip=ipaddress.ip_address(target['ip'])
            port=target['port']
            require(type(port) is int and 1<=port<=65535,'INVALID_NETWORK_PROBE_PORT')
            family=socket.AF_INET if ip.version==4 else socket.AF_INET6
            blocked=False
            try:
                with socket.socket(family,socket.SOCK_STREAM) as connection:
                    connection.settimeout(1)
                    blocked=connection.connect_ex((str(ip),port))!=0
            except OSError:
                blocked=True
            require(blocked,'BUILD_NETWORK_TARGET_REACHABLE')
            results.append({'label':target['label'],'blocked':True})
    return {'phase':phase,'namespace':namespace,'direct_network_blocked':True,'probes':results,
            'proxy_denials':denials if phase=='install' else [],
            'proxy_mounted':phase=='install','ip_socket_families_blocked':phase=='offline'}
