import os

from hcloud import Client
from hcloud.images import Image
from hcloud.locations import Location
from hcloud.server_types import ServerType

from providers.base import Provider
from providers.common import ensure_local_ssh_keypair


class HetznerProvider(Provider):
    name = 'hetzner'
    ssh_user = 'root'
    defaults = {
        'region': 'nbg1',            # Hetzner "location"
        'size': None,                # None = cheapest current x86 type in the region
        'image': 'ubuntu-24.04',
    }
    privkey_fn = 'rotvpn-{}-private.key'.format(name)
    pubkey_fn = 'rotvpn-{}-public.key'.format(name)

    def __init__(self, deploy_name, config=None):
        super().__init__(deploy_name, config)
        token = os.getenv('ROT_HCLOUD_TOKEN')
        if token is None:
            raise Exception("Must set ROT_HCLOUD_TOKEN env var to a Hetzner Cloud API token (read & write)! "
                            "See https://github.com/jar-o/rotvpn")
        self.client = Client(token=token)
        self.server_name = 'rotvpn-{}'.format(deploy_name)
        self.keyname = 'rotvpn-{}-ssh-key'.format(deploy_name)

    def ensure_ssh_key(self):
        pub = ensure_local_ssh_keypair(self.privkey_fn, self.pubkey_fn)
        # Always make sure the provider has *this* key, even if the local files
        # already existed (the key may have been deleted from the console).
        existing = self.client.ssh_keys.get_by_name(self.keyname)
        if existing is not None:
            if existing.public_key.split()[:2] == pub.split()[:2]:
                self.ssh_key = existing
                return self.privkey_fn
            print('Replacing stale SSH key {} on Hetzner'.format(self.keyname))
            existing.delete()
        print('Adding public key {} to Hetzner'.format(self.keyname))
        self.ssh_key = self.client.ssh_keys.create(name=self.keyname, public_key=pub)
        return self.privkey_fn

    def _pick_server_type(self, location):
        """Cheapest non-deprecated x86 type that is priced in this location."""
        best = None
        for st in self.client.server_types.get_all():
            if st.architecture != 'x86' or st.deprecation:
                continue
            for p in st.prices or []:
                if p.get('location') == location:
                    cost = float(p['price_monthly']['net'])
                    if best is None or cost < best[0]:
                        best = (cost, st.name)
        if best is None:
            raise Exception('No x86 server type found for location {!r}. Set one with '
                            '--config \'{{"size":"..."}}\''.format(location))
        print('Using server type {} (~{:.2f}/month net)'.format(best[1], best[0]))
        return best[1]

    def create_server(self):
        region = self.config['region']
        size = self.config['size'] or self._pick_server_type(region)
        print('Creating Hetzner server ...')
        resp = self.client.servers.create(
            name=self.server_name,
            server_type=ServerType(name=size),
            image=Image(name=self.config['image']),
            location=Location(name=region),
            ssh_keys=[self.ssh_key],
            labels={'rotvpn': self.deploy_name},
        )
        resp.action.wait_until_finished()
        for a in resp.next_actions or []:
            a.wait_until_finished()
        server = self.client.servers.get_by_name(self.server_name)
        return server.public_net.ipv4.ip

    def remove(self):
        server = self.client.servers.get_by_name(self.server_name)
        if server is None:
            print('Deploy {} not found.'.format(self.server_name))
            return
        print('{} exists. Removing.'.format(self.server_name))
        server.delete().wait_until_finished()
        print('Done.')
