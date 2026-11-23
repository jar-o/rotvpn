"""Local WireGuard key and config generation.

All key material is generated on the workstation. The server only ever receives
its own private key plus the peers' public/preshared keys (as wg0.conf); client
private keys never leave this machine.
"""
import base64
import os
import zipfile

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

WIREGUARD_PORT = 51820
IPV4_PREFIX = '10.200.200'
IPV6_PREFIX = 'fd86:ea04:1111'
DEFAULT_PEERS = 10


def gen_keypair():
    """Return (private, public) as base64 strings, the format `wg` uses."""
    priv = X25519PrivateKey.generate()
    priv_raw = priv.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption())
    pub_raw = priv.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw)
    return base64.b64encode(priv_raw).decode(), base64.b64encode(pub_raw).decode()


def gen_psk():
    return base64.b64encode(os.urandom(32)).decode()


class WireGuardConfig:
    """A server keypair plus N peers. Generate once per provision (rotation)."""

    def __init__(self, num_peers=DEFAULT_PEERS):
        self.server_priv, self.server_pub = gen_keypair()
        self.peers = []
        for n in range(1, num_peers + 1):
            priv, pub = gen_keypair()
            self.peers.append({
                'name': 'peer{}'.format(n),
                'host': n + 1,  # .1 is the server
                'priv': priv,
                'pub': pub,
                'psk': gen_psk(),
            })

    def server_config(self):
        lines = [
            '[Interface]',
            'PrivateKey = {}'.format(self.server_priv),
            'Address = {}.1/24, {}::1/64'.format(IPV4_PREFIX, IPV6_PREFIX),
            'ListenPort = {}'.format(WIREGUARD_PORT),
        ]
        # NAT/forwarding is handled by nftables (see aux/setup.sh), not PostUp.
        for p in self.peers:
            lines += [
                '',
                '[Peer]',
                'PublicKey = {}'.format(p['pub']),
                'PresharedKey = {}'.format(p['psk']),
                'AllowedIPs = {}.{}/32, {}::{}/128'.format(
                    IPV4_PREFIX, p['host'], IPV6_PREFIX, p['host']),
            ]
        return '\n'.join(lines) + '\n'

    def client_configs(self, endpoint_ip):
        """Return {filename: contents} for every peer."""
        out = {}
        for p in self.peers:
            out['{}-tunnel.conf'.format(p['name'])] = '\n'.join([
                '[Interface]',
                'Address = {}.{}/24, {}::{}/64'.format(
                    IPV4_PREFIX, p['host'], IPV6_PREFIX, p['host']),
                'PrivateKey = {}'.format(p['priv']),
                'DNS = {}.1, {}::1'.format(IPV4_PREFIX, IPV6_PREFIX),
                '',
                '[Peer]',
                'PublicKey = {}'.format(self.server_pub),
                'PresharedKey = {}'.format(p['psk']),
                'AllowedIPs = 0.0.0.0/0, ::/0',
                'Endpoint = {}:{}'.format(endpoint_ip, WIREGUARD_PORT),
                '',
            ])
        return out

    def write_zip(self, endpoint_ip, dest):
        if os.path.exists(dest):
            os.remove(dest)
        with zipfile.ZipFile(dest, 'w', zipfile.ZIP_DEFLATED) as z:
            for fn, contents in self.client_configs(endpoint_ip).items():
                z.writestr(fn, contents)
        os.chmod(dest, 0o600)
