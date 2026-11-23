import json
from abc import ABC, abstractmethod

from providers.common import provision_wireguard


class Provider(ABC):
    """Base class for cloud providers.

    To add a provider, subclass this, set `name` (and `ssh_user` / `defaults`
    if needed), implement the three abstract methods, and register the class in
    providers/__init__.py. The shared flow (SSH key -> rotate old server ->
    create server -> WireGuard setup -> client configs) lives in provision().
    """

    # Registry key. Also used in the peer config zip filename.
    name = None
    # Login user for the provisioned image (root on most VPS providers).
    ssh_user = 'root'
    # Per-provider settings that --config JSON can override.
    defaults = {}

    def __init__(self, deploy_name, config=None):
        self.deploy_name = deploy_name
        self.config = dict(self.defaults)
        if config:
            self.config.update(json.loads(config))

    @abstractmethod
    def ensure_ssh_key(self):
        """Make sure an SSH keypair exists locally and is registered with the
        provider. Return the path to the private key file."""

    @abstractmethod
    def create_server(self):
        """Create the server and block until it has a public IP. Return the IP
        as a string. (SSH readiness is handled by the caller's retry loop.)"""

    @abstractmethod
    def remove(self):
        """Delete the server for this deploy name, if it exists."""

    def provision(self):
        key_fn = self.ensure_ssh_key()
        self.remove()  # rotation: always start from a fresh server
        ip = self.create_server()
        print('Server is live: {}'.format(ip))
        dest = 'peer-tunnel-configs-{}-{}.zip'.format(self.name, self.deploy_name)
        return provision_wireguard(ip, key_fn, dest, self.ssh_user)
