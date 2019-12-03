# NOTE(jamesr) Providers MUST implement provision() and remove(). That is all.
import os, time, base64, json

from Crypto.PublicKey import RSA
import paramiko
from scp import SCPClient, SCPException
import digitalocean

peer_config_download = 'peer-tunnel-configs.zip'

class DigitalOceanProvider:
    endpoint = 'https://api.digitalocean.com/v2/'
    prefix = 'digitalocean'
    privkey_fn = "rotvpn-{}-private.key".format(prefix)
    pubkey_fn = "rotvpn-{}-public.key".format(prefix)
    vpn_name_prefix = 'rotvpn'
    def __init__(self, deploy_name, config=None):
        v = os.getenv('DO_TOKEN')
        if v == None:
            raise Exception("Must set DO_TOKEN env var to DigitalOcean API token! See https://cloud.digitalocean.com/account/api/tokens")
        self.deploy_name = deploy_name
        self.manager = digitalocean.Manager(token=v)
        self.name = '-'.join([self.vpn_name_prefix, self.deploy_name])
        self.keyname = '-'.join([self.vpn_name_prefix, self.deploy_name, 'ssh-key'])
        if config != None:
            self.config = json.loads(config)
    def gen_ssh_keys(self):
        if os.path.exists(self.privkey_fn) and os.path.exists(self.pubkey_fn):
            print('SSH keys already seem to exist. Skipping generation.')
            with open(self.privkey_fn, 'r') as content_file:
                self.privkey = content_file.read()
            with open(self.pubkey_fn, 'r') as content_file:
                self.pubkey = content_file.read()
            return
        key = RSA.generate(2048)
        with open(self.privkey_fn, 'wb') as content_file:
            os.chmod(self.privkey_fn, 0o600)
            k = key.exportKey('PEM')
            content_file.write(k)
            self.privkey = k.decode('utf-8')
        with open(self.pubkey_fn, 'wb') as content_file:
            k = key.publickey().exportKey('OpenSSH')
            content_file.write(k)
            self.pubkey = k.decode('utf-8')
        self.__add_ssh_key_to_digitalocean()
    def __add_ssh_key_to_digitalocean(self):
        print('Adding public key {} to DigitalOcean'.format(self.keyname))
        # check for existing key, and delete
        for key in self.manager.get_all_sshkeys():
            if key.name == self.keyname:
                key.destroy()
        key = digitalocean.SSHKey(
            token=os.getenv('DO_TOKEN'),
            name=self.keyname,
            public_key=self.pubkey)
        key.create()
    def provision(self):
        self.gen_ssh_keys()
        self.remove() # if droplet exists, we delete, and make a new one... "rotation"
        keys = self.manager.get_all_sshkeys()
        # create droplet
        size = 's-1vcpu-1gb'
        region = 'sfo2'
        if hasattr(self, 'config') and self.config != None:
            if 'size' in self.config:
                size = self.config['size']
            if 'region' in self.config:
                region = self.config['region']
        droplet = digitalocean.Droplet(
            token = os.getenv('DO_TOKEN'),
            name = self.name,
            region = region,
            image = 'ubuntu-18-04-x64',
            size = size,
            ssh_keys = keys,
            backups = False)
        print('Creating droplet ...')
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
        print('IP Address: {}'.format(droplet.ip_address))
        self.ip_address = droplet.ip_address
        self.__install_wireguard()
    def __install_wireguard(self):
        # NOTE there is no API specific way of telling if the SSH daemon is
        # ready. We just have to try in a loop
        time.sleep(10)
        key = paramiko.RSAKey.from_private_key_file(self.privkey_fn)
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.get_host_keys().add(self.deploy_name, 'ssh-rsa', key)
        for i in range(5):
            print("Attempting to connect {}/{} ...".format(i, 5))
            try:
                client.connect(self.ip_address, username='root')
            except paramiko.ssh_exception.NoValidConnectionsError:
                print('No valid connection. (Server probably not ready.)')
                time.sleep(5)
                continue
            scp = SCPClient(client.get_transport())
            print('Installing server (running script remotely, takes a little time ...')
            #TODO ensure pathing
            scp.put('./aux/setup-ubuntu.sh', '/root/setup.sh')
            stdin, stdout, stderr = client.exec_command('/root/setup.sh')
            exit_status = stdout.channel.recv_exit_status() # Blocking call
            if exit_status != 0:
                print('Error occured. Cannot continue Exit status {}'.format(exit_status))
                return
            # now, retrieve the generated peer configs
            try:
                os.remove(peer_config_download)
            except FileNotFoundError:
                pass
            for j in range(10):
                try:
                    scp.get('/root/{}'.format(peer_config_download))
                except SCPException:
                    print("{} not avilable. Trying again.".format(peer_config_download))
                    time.sleep(5)
                    continue
            else:
                print('Peer configs available: {}'.format(peer_config_download))
                break
            break
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
