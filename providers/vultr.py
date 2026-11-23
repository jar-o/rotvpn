import os, time

import requests

from providers.base import Provider
from providers.common import ensure_local_ssh_keypair

API = 'https://api.vultr.com/v2'


class VultrProvider(Provider):
    name = 'vultr'
    ssh_user = 'root'
    defaults = {
        'region': 'sjc',
        'size': 'vc2-1c-1gb',                # Vultr "plan"
        'image': 'Ubuntu 24.04 LTS x64',     # OS name (looked up via /os) or a numeric os_id
    }
    privkey_fn = 'rotvpn-{}-private.key'.format(name)
    pubkey_fn = 'rotvpn-{}-public.key'.format(name)

    def __init__(self, deploy_name, config=None):
        super().__init__(deploy_name, config)
        key = os.getenv('ROT_VULTR_API_KEY')
        if key is None:
            raise Exception("Must set ROT_VULTR_API_KEY env var to a Vultr API key! "
                            "(Also allow your current IP under the key's access control.) "
                            "See https://github.com/jar-o/rotvpn")
        self.session = requests.Session()
        self.session.headers['Authorization'] = 'Bearer {}'.format(key)
        self.label = 'rotvpn-{}'.format(deploy_name)
        self.keyname = 'rotvpn-{}-ssh-key'.format(deploy_name)

    def _call(self, method, path, **kw):
        r = self.session.request(method, API + path, timeout=30, **kw)
        if r.status_code >= 400:
            raise Exception('Vultr API {} {} failed ({}): {}'.format(method, path, r.status_code, r.text))
        return r.json() if r.content else {}

    def _list(self, path, key, params=None):
        """Follow Vultr's cursor pagination, yielding every item."""
        params = dict(params or {}, per_page=100)
        while True:
            page = self._call('GET', path, params=params)
            for item in page.get(key, []):
                yield item
            nxt = (page.get('meta') or {}).get('links', {}).get('next')
            if not nxt:
                return
            params['cursor'] = nxt

    def ensure_ssh_key(self):
        pub = ensure_local_ssh_keypair(self.privkey_fn, self.pubkey_fn)
        for k in self._list('/ssh-keys', 'ssh_keys'):
            if k['name'] != self.keyname:
                continue
            if k['ssh_key'].split()[:2] == pub.split()[:2]:
                self.ssh_key_id = k['id']
                return self.privkey_fn
            print('Replacing stale SSH key {} on Vultr'.format(self.keyname))
            self._call('DELETE', '/ssh-keys/{}'.format(k['id']))
        print('Adding public key {} to Vultr'.format(self.keyname))
        res = self._call('POST', '/ssh-keys', json={'name': self.keyname, 'ssh_key': pub})
        self.ssh_key_id = res['ssh_key']['id']
        return self.privkey_fn

    def _resolve_os_id(self):
        image = self.config['image']
        if isinstance(image, int) or str(image).isdigit():
            return int(image)
        names = []
        for o in self._list('/os', 'os'):
            if o['name'].lower() == str(image).lower():
                return o['id']
            names.append(o['name'])
        close = [n for n in names if 'ubuntu' in n.lower() or 'debian' in n.lower()]
        raise Exception('No Vultr OS named {!r}. Pass an exact name or numeric id via --config. '
                        'Some options: {}'.format(image, ', '.join(close)))

    def create_server(self):
        body = {
            'region': self.config['region'],
            'plan': self.config['size'],
            'os_id': self._resolve_os_id(),
            'label': self.label,
            'hostname': self.label,
            'sshkey_id': [self.ssh_key_id],
            'backups': 'disabled',
            'enable_ipv6': False,
            'tags': ['rotvpn'],
        }
        print('Creating Vultr instance ...')
        inst = self._call('POST', '/instances', json=body)['instance']
        print('Instance: {}, id={}'.format(inst['label'], inst['id']))
        # main_ip is "0.0.0.0" until the instance is allocated.
        for _ in range(120):
            inst = self._call('GET', '/instances/{}'.format(inst['id']))['instance']
            print('status={} power={} server={} ip={}'.format(
                inst['status'], inst['power_status'], inst['server_status'], inst['main_ip']))
            if inst['status'] == 'active' and inst['main_ip'] not in ('', '0.0.0.0'):
                return inst['main_ip']
            time.sleep(5)
        raise Exception('Timed out waiting for Vultr instance {} to become active'.format(inst['id']))

    def remove(self):
        found = False
        # The label filter may be a substring match, so compare exactly.
        for inst in self._list('/instances', 'instances', {'label': self.label}):
            if inst['label'] != self.label:
                continue
            print('{} exists (id {}). Removing.'.format(self.label, inst['id']))
            self._call('DELETE', '/instances/{}'.format(inst['id']))
            found = True
        print('Done.' if found else 'Deploy {} not found.'.format(self.label))
