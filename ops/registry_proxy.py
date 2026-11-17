#!/usr/bin/env python3
"""Socket-activated, credential-free CONNECT proxy for one public npm registry.

Installers have no external network device. Their only egress is this filesystem
Unix socket. Resolve once, reject EVERY non-global/local result, then connect to
the validated numeric address: no redirect, arbitrary host, or DNS rebinding.
"""
from contextlib import contextmanager
import ipaddress
import os
from pathlib import Path
import re
import select
import socket
import socketserver
import struct
import threading
import time

HOST = 'registry.npmjs.org'
MAX_CONNECTIONS = 24
MAX_BYTES = 256 * 1024 * 1024
TRANSITION = tuple(ipaddress.ip_network(n) for n in (
    '64:ff9b::/96','64:ff9b:1::/48','2001::/32','2002::/16','::ffff:0:0/96'))


class ProxyDenied(Exception):
    pass


def local_addresses():
    result = {'127.0.0.1','::1'}
    # Read interface addresses without shell commands or additional privileges.
    import fcntl
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as query:
        for _,name in socket.if_nameindex():
            try:
                data=fcntl.ioctl(query.fileno(),0x8915,struct.pack('256s',name.encode()[:15]))
                result.add(socket.inet_ntoa(data[20:24]))
            except OSError:
                pass  # An IPv6-only interface has no SIOCGIFADDR value.
    ipv6=Path('/proc/net/if_inet6')
    if ipv6.exists():
        for line in ipv6.read_text().splitlines():
            result.add(str(ipaddress.IPv6Address(int(line.split()[0],16))))
    return {ipaddress.ip_address(ip) for ip in result}


def public_registry_addresses(resolver=None, local=None):
    resolver=resolver or socket.getaddrinfo
    local=local_addresses() if local is None else {ipaddress.ip_address(ip) for ip in local}
    records=resolver(HOST,443,type=socket.SOCK_STREAM)
    addresses=[]
    for family,kind,protocol,_,address in records:
        if family not in (socket.AF_INET,socket.AF_INET6):
            raise ProxyDenied('UNSUPPORTED_ADDRESS')
        ip=ipaddress.ip_address(address[0])
        if not ip.is_global or ip in local or getattr(ip,'ipv4_mapped',None) is not None or \
                (ip.version==6 and any(ip in network for network in TRANSITION)):
            raise ProxyDenied('NON_PUBLIC_OR_LOCAL_DESTINATION')
        item=(family,kind,protocol,address)
        if item not in addresses: addresses.append(item)
    if not addresses or len(addresses)>32: raise ProxyDenied('INVALID_DNS_RESULT')
    return sorted(addresses,key=lambda a:(a[0]!=socket.AF_INET,a[3][0]))


def validate_connect(header):
    try:
        line=header.split(b'\r\n',1)[0].decode('ascii')
    except UnicodeDecodeError:
        raise ProxyDenied('INVALID_REQUEST') from None
    if not re.fullmatch(r'CONNECT registry\.npmjs\.org:443 HTTP/1\.[01]',line):
        raise ProxyDenied('REGISTRY_CONNECT_ONLY')


def connect_registry():
    # All DNS answers are validated BEFORE opening any upstream socket.
    for family,kind,protocol,address in public_registry_addresses():
        connection=socket.socket(family,kind,protocol)
        connection.settimeout(10)
        try:
            connection.connect(address)  # numeric sockaddr; never resolve again
            return connection
        except OSError:
            connection.close()
    raise ProxyDenied('REGISTRY_UNREACHABLE')


def relay(left,right,initial=b''):
    left.settimeout(30)
    right.settimeout(30)
    if initial: right.sendall(initial)
    deadline=time.monotonic()+600
    transferred=len(initial)
    while time.monotonic()<deadline:
        readable,_,_=select.select([left,right],[],[],30)
        if not readable: return
        for source in readable:
            data=source.recv(65536)
            if not data: return
            transferred+=len(data)
            if transferred>MAX_BYTES: return
            (right if source is left else left).sendall(data)


class ConnectHandler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(15)
        try:
            header=b''
            while b'\r\n\r\n' not in header:
                chunk=self.request.recv(4096)
                if not chunk: return
                header+=chunk
                if len(header)>8192: raise ProxyDenied('REQUEST_TOO_LARGE')
            head,initial=header.split(b'\r\n\r\n',1)
            validate_connect(head)
            with connect_registry() as upstream:
                self.request.sendall(b'HTTP/1.1 200 Connection Established\r\n\r\n')
                relay(self.request,upstream,initial)
        except (ProxyDenied,OSError,ValueError):
            # Never log URLs, query strings, headers, tokens or raw error text.
            try: self.request.sendall(b'HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n')
            except OSError: pass


class BoundedThreads(socketserver.ThreadingMixIn):
    daemon_threads=True
    block_on_close=False

    def process_request(self,request,address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try: super().process_request(request,address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self,request,address):
        try: super().process_request_thread(request,address)
        finally: self.slots.release()


# Policy tests can be imported on Windows; serving ALWAYS requires Unix sockets.
class RegistryServer(BoundedThreads,getattr(socketserver,'UnixStreamServer',socketserver.TCPServer)):
    def __init__(self,fd):
        if not hasattr(socketserver,'UnixStreamServer'): raise ProxyDenied('UNIX_SOCKET_SERVER_REQUIRED')
        self.slots=threading.BoundedSemaphore(MAX_CONNECTIONS)
        super().__init__('',ConnectHandler,bind_and_activate=False)
        self.socket.close()
        self.socket=socket.socket(fileno=fd)
        self.server_address=self.socket.getsockname()


class BridgeHandler(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as upstream:
                upstream.settimeout(10)
                upstream.connect('/proxy/egress.sock')
                relay(self.request,upstream)
        except OSError: pass


class BridgeServer(BoundedThreads,socketserver.TCPServer):
    def __init__(self):
        self.slots=threading.BoundedSemaphore(MAX_CONNECTIONS)
        super().__init__(('127.0.0.1',0),BridgeHandler)


@contextmanager
def install_bridge():
    # This listener is inside the installer's PRIVATE loopback namespace.
    with BridgeServer() as server:
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try: yield 'http://127.0.0.1:'+str(server.server_address[1])
        finally:
            server.shutdown()
            thread.join(timeout=5)


if __name__=='__main__':
    if os.environ.get('LISTEN_PID')!=str(os.getpid()) or os.environ.get('LISTEN_FDS')!='1':
        raise SystemExit('SOCKET_ACTIVATION_REQUIRED')
    with RegistryServer(3) as server: server.serve_forever()
