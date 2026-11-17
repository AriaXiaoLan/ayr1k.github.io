import ipaddress
from pathlib import Path
import socket
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import registry_proxy as proxy


def answer(ip):
    family=socket.AF_INET6 if ':' in ip else socket.AF_INET
    address=(ip,443,0,0) if family==socket.AF_INET6 else (ip,443)
    return family,socket.SOCK_STREAM,6,'',address


class RegistryPolicyTests(unittest.TestCase):
    def test_only_exact_registry_connect_on_443_is_accepted(self):
        proxy.validate_connect(b'CONNECT registry.npmjs.org:443 HTTP/1.1\r\nHost: ignored')
        for request in (
            'GET https://registry.npmjs.org/x HTTP/1.1',
            'CONNECT 127.0.0.1:443 HTTP/1.1','CONNECT 169.254.169.254:80 HTTP/1.1',
            'CONNECT [::1]:443 HTTP/1.1','CONNECT 100.88.222.5:443 HTTP/1.1',
            'CONNECT registry.npmjs.org:80 HTTP/1.1',
            'CONNECT registry.npmjs.org.evil.invalid:443 HTTP/1.1',
            'CONNECT user@registry.npmjs.org:443 HTTP/1.1',
            'CONNECT registry.npmjs.org:443@127.0.0.1:80 HTTP/1.1'):
            with self.subTest(request=request), self.assertRaises(proxy.ProxyDenied):
                proxy.validate_connect(request.encode())

    def test_dns_private_metadata_local_and_transition_results_fail_closed(self):
        for ip in ('127.0.0.1','10.0.0.1','172.16.0.1','192.168.0.1','169.254.169.254',
                   '100.88.222.5','::1','fd00:ec2::254','fe80::1','::ffff:169.254.169.254',
                   '64:ff9b::a9fe:a9fe','64:ff9b:1::a00:1','2002:7f00:1::1','2001::a9fe:a9fe'):
            with self.subTest(ip=ip), self.assertRaises(proxy.ProxyDenied):
                proxy.public_registry_addresses(resolver=lambda *a,**kw:[answer(ip)],local=[])
        with self.assertRaises(proxy.ProxyDenied):
            proxy.public_registry_addresses(resolver=lambda *a,**kw:[answer('93.184.216.34')],local=['93.184.216.34'])

    def test_mixed_dns_answer_is_rejected_before_any_upstream_connect(self):
        with patch.object(proxy.socket,'getaddrinfo',return_value=[answer('93.184.216.34'),answer('10.0.0.1')]), \
             patch.object(proxy,'local_addresses',return_value=set()), patch.object(proxy.socket,'socket') as opened:
            with self.assertRaises(proxy.ProxyDenied): proxy.connect_registry()
            opened.assert_not_called()

    def test_validated_numeric_address_is_used_without_second_resolution(self):
        with patch.object(proxy.socket,'getaddrinfo',return_value=[answer('93.184.216.34')]) as resolve, \
             patch.object(proxy,'local_addresses',return_value=set()), patch.object(proxy.socket,'socket') as opened:
            proxy.connect_registry()
            resolve.assert_called_once()
            opened.return_value.connect.assert_called_once_with(('93.184.216.34',443))


if __name__=='__main__': unittest.main()
