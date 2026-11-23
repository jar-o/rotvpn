import os, time

from Crypto.PublicKey import RSA
import digitalocean

from providers.base import Provider


class DigitalOceanProvider(Provider):
    name = 'digitalocean'
    ssh_user = 'root'
    defaults = {
        'size': 's-1vcpu-1gb',
        'region': 'sfo2',
        'image': 'ubuntu-24-04-x64',
    }
    privkey_fn = "rotvpn-{}-private.key".format(name)
    pubkey_fn = "rotvpn-{}-public.key".format(name)
    vpn_name_prefix = 'rotvpn'

    def __init__(self, deploy_name, config=None):
        super().__init__(deploy_name, config)
        self.token = os.getenv('ROT_DO_TOKEN')
        if self.token == None:
            raise Exception("Must set ROT_DO_TOKEN env var to DigitalOcean API token! See https://cloud.digitalocean.com/account/api/tokens")
        self.manager = digitalocean.Manager(token=self.token)
        self.droplet_name = '-'.join([self.vpn_name_prefix, self.deploy_name])
        self.keyname = '-'.join([self.vpn_name_prefix, self.deploy_name, 'ssh-key'])

    def ensure_ssh_key(self):
        if os.path.exists(self.privkey_fn) and os.path.exists(self.pubkey_fn):
            print('SSH keys already seem to exist. Skipping generation.')
            with open(self.pubkey_fn, 'r') as content_file:
                self.pubkey = content_file.read()
            return self.privkey_fn
        key = RSA.generate(2048)
        with open(self.privkey_fn, 'wb') as content_file:
            os.chmod(self.privkey_fn, 0o600)
            content_file.write(key.exportKey('PEM'))
        with open(self.pubkey_fn, 'wb') as content_file:
            k = key.publickey().exportKey('OpenSSH')
            content_file.write(k)
            self.pubkey = k.decode('utf-8')
        self._add_ssh_key_to_digitalocean()
        return self.privkey_fn

    def _add_ssh_key_to_digitalocean(self):
        print('Adding public key {} to DigitalOcean'.format(self.keyname))
        # check for existing key, and delete
        for key in self.manager.get_all_sshkeys():
            if key.name == self.keyname:
                key.destroy()
        key = digitalocean.SSHKey(
            token=self.token,
            name=self.keyname,
            public_key=self.pubkey)
        key.create()

    def create_server(self):
        droplet = digitalocean.Droplet(
            token = self.token,
            name = self.droplet_name,
            region = self.config['region'],
            image = self.config['image'],
            size = self.config['size'],
            ssh_keys = self.manager.get_all_sshkeys(),
            backups = False)
        print('Creating droplet ...')
        droplet.create()
        print('Droplet: {}, id={}'.format(droplet.name, droplet.id))
        droplet_id = droplet.id
        actions = droplet.get_actions()
        for action in actions:
            action.load()
            print(action.status)
            time.sleep(5)
            if action.status == 'completed':
                break
        while droplet.ip_address == None:
            print('Waiting for server ... IP: {}'.format(droplet.ip_address))
            droplet = self.manager.get_droplet(droplet_id)
            time.sleep(5)
        return droplet.ip_address

    def remove(self):
        has_removed = False
        for droplet in self.manager.get_all_droplets():
            if droplet.name == self.droplet_name:
                print('{} exists. Removing.'.format(self.droplet_name))
                print(droplet)
                droplet.destroy()
                actions = droplet.get_actions()
                for action in actions:
                    action.load()
                    print(action.status)
                    time.sleep(5)
                    if action.status == 'completed':
                        break
                has_removed = True
        if has_removed == False:
            print('Deploy {} not found.'.format(self.droplet_name))
