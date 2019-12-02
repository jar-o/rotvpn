# NOTE(jamesr) Providers MUST implement provision() and remove(). That is all.
import os, time

from Crypto.PublicKey import RSA
import digitalocean


class DigitalOceanProvider:
    endpoint = 'https://api.digitalocean.com/v2/'
    prefix = 'digitalocean'
    privkey_fn = "rotvpn-{}-private.key".format(prefix)
    pubkey_fn = "rotvpn-{}-public.key".format(prefix)
    vpn_name_prefix = 'rotvpn'
    def __init__(self, deploy_name):
        v = os.getenv('DO_TOKEN')
        if v == None:
            raise Exception("Must set DO_TOKEN env var to DigitalOcean API token! See https://cloud.digitalocean.com/account/api/tokens")
        self.deploy_name = deploy_name
        self.manager = digitalocean.Manager(token=v)
        self.name = self.vpn_name_prefix + '-' + self.deploy_name
        self.keyname = self.vpn_name_prefix + '-ssh-key'
    def toke(self):
        return os.getenv('DO_TOKEN')
    def set_creds(self, token):
        self.token = token
    def gen_ssh_keys(self):
        if os.path.exists(self.privkey_fn) and os.path.exists(self.pubkey_fn):
            print('SSH keys already seem to exist. Skipping generation.')
            return
        key = RSA.generate(2048)
        with open(self.privkey_fn, 'wb') as content_file:
            os.chmod(self.privkey_fn, 0o600)
            content_file.write(key.exportKey('PEM'))
        with open(self.pubkey_fn, 'wb') as content_file:
            self.pubkey = key.publickey().exportKey('OpenSSH')
            content_file.write(self.pubkey)
            self.pubkey = self.pubkey.decode('utf-8')
        self.add_ssh_key_to_digitalocean()
    def add_ssh_key_to_digitalocean(self):
        print('Adding public key to DigitalOcean')
        print(self.pubkey)
        #TODO check DO for existing key, and ... delete?
        # for key in self.manager.get_all_sshkeys():
        #     print(key.name)
        #     print(key.name == self.keyname)
        key = digitalocean.SSHKey(
            token=os.getenv('DO_TOKEN'),
            name=self.keyname,
            public_key=self.pubkey)
        print(key.create())
        print(key)
        self.do_keyid = key.id
    def provision(self):
        self.gen_ssh_keys()
        self.remove() # if droplet exists, we delete, and make a new one... "rotation"
        keys = self.manager.get_all_sshkeys()
        # create droplet
        droplet = digitalocean.Droplet(
            token = os.getenv('DO_TOKEN'), # TODO ROT_DO_TOKEN
            name = self.name,
            region = 'sfo2', #TODO
            image = 'ubuntu-18-04-x64',
            # size_slug = '1024mb',
            size = 's-1vcpu-1gb',
            ssh_keys = keys,
            backups = False)
        droplet.create()
        print('Droplet: {}, id={}'.format(droplet.name, droplet.id))
        self.droplet_id = droplet.id
        actions = droplet.get_actions()
        for action in actions:
            action.load()
            print(action.status)
            time.sleep(5)
            if action.status == 'completed':
                break
        while droplet.ip_address == None:
            print('Waiting for server ... IP: {}'.format(droplet.ip_address))
            droplet = self.manager.get_droplet(self.droplet_id)
            time.sleep(5)
        time.sleep(5)
        print('IP Address: {}'.format(droplet.ip_address))
    def remove(self):
        has_removed = False
        for droplet in self.manager.get_all_droplets():
            if droplet.name == self.name:
                print('{} exists. Removing.'.format(self.name))
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
            print('Deploy {} not found.'.format(self.name))
