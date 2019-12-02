"""

Guides:

https://craighuther.com/2019/05/14/wireguard-setup-and-installation/
https://www.ckn.io/blog/2017/11/14/wireguard-vpn-typical-setup/
https://www.stavros.io/posts/how-to-configure-wireguard/
https://emanuelduss.ch/2018/09/wireguard-vpn-road-warrior-setup/

TODO SCP from Python (fetch client configs)
https://pypi.org/project/scp/

ssh -i rotvpn-digitalocean-private.key -o "StrictHostKeyChecking no" root@<IP ADDRESS>
scp -i rotvpn-digitalocean-private.key root@<IP ADDRESS>:~/peer-tunnel-configs.zip peer-tunnel-configs.zip

"""


import os
import time

from Crypto.PublicKey import RSA
import digitalocean

# TODO put in own file
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
        self.remove() # if droplet exists, we delete, and make a new one: "rotation"
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
        print(droplet)
        actions = droplet.get_actions()
        for action in actions:
            action.load()
            # Once it shows complete, droplet is up and running
            print(action.status)
            time.sleep(5)
            if action.status == 'completed':
                break
        # TODO wait for server up
        print('IP Address: {}'.format(droplet.ip_address))
    def remove(self):
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

# TODO factory, args, i.e.
# rotvpn --provider digitalocean --deploy vpn1 [--provision|--remove] 
rv = DigitalOceanProvider('jamesdev')
rv.provision()
#rv.remove()


# the server script... generates the server config + 10 peer configs
"""
#### Ubuntu 18.04


#### Installation

add-apt-repository -y ppa:wireguard/wireguard
apt install wireguard -y
apt install zip -y
apt install unbound unbound-host -y


#### Wireguard

umask 077 && wg genkey | tee privatekey | wg pubkey > publickey

srvaddr=$(hostname -I | awk '{print $1}')
fn='/etc/wireguard/wg0.conf'
echo '[Interface]' > $fn
echo "PrivateKey = $(cat privatekey)" >> $fn
echo "Address = 10.200.200.1/24" >> $fn
echo 'ListenPort = 51820' >> $fn
echo 'PostUp = iptables -A FORWARD -i wg0 -j ACCEPT; iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE; ip6tables -A FORWARD -i wg0 -j ACCEPT; ip6tables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
PostDown = iptables -D FORWARD -i wg0 -j ACCEPT; iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE; ip6tables -D FORWARD -i wg0 -j ACCEPT; ip6tables -t nat -D POSTROUTING -o eth0 -j MASQUERADE
SaveConfig = true' >> $fn

rm -f peer-tunnel-configs.zip
for i in `seq 2 11`; do
        prefix="peer${i}"
        umask 077 && wg genpsk > client-preshared
        umask 077 && wg genkey | tee client-privatekey | wg pubkey > client-pubkey
        # add to wireguard server config file
        echo '' >> $fn
        echo '[Peer]' >> $fn
        echo "PublicKey = $(cat client-pubkey)" >> $fn
        echo "PresharedKey = $(cat client-preshared)" >> $fn
        echo "AllowedIPs = 10.200.200.${i}/32" >> $fn
        # create the client tunnel file
        clifn="$prefix-tunnel.conf"
        echo "[Interface]" >> $clifn
        echo "Address = 10.200.200.${i}/24" >> $clifn
        echo "PrivateKey = $(cat client-privatekey)" >> $clifn
        echo "DNS = 10.200.200.1" >> $clifn      #opendns
        echo "" >> $clifn
        echo "[Peer]" >> $clifn
        echo "PublicKey = $(cat publickey)" >> $clifn
        echo "PresharedKey = $(cat client-preshared)" >> $clifn
        echo "AllowedIPs = 0.0.0.0/0,::/0" >> $clifn
        echo "Endpoint = $srvaddr:51820" >> $clifn
        rm client-preshared
        rm client-privatekey
        rm client-pubkey
done
zip peer-tunnel-configs.zip peer*.conf
rm peer*.conf


#### DNS Server

curl -o /var/lib/unbound/root.hints https://www.internic.net/domain/named.cache
chown unbound:unbound /var/lib/unbound/root.hints

cat > /etc/unbound/unbound.conf <<- EOM
# Unbound configuration file for Debian.
#
# See the unbound.conf(5) man page.
#
# See /usr/share/doc/unbound/examples/unbound.conf for a commented
# reference config file.
#
# The following line includes additional configuration files from the
# /etc/unbound/unbound.conf.d directory.
include: "/etc/unbound/unbound.conf.d/*.conf"

server:
  num-threads: 4

  #Enable logs
  verbosity: 1

  #list of Root DNS Server
  root-hints: "/var/lib/unbound/root.hints"

  #Respond to DNS requests on wireguard interface
  interface: 10.200.200.1
  max-udp-size: 3072

  #Authorized IPs to access the DNS Server
  access-control: 0.0.0.0/0                 refuse
  access-control: 127.0.0.1                 allow
  access-control: 10.200.200.0/24         allow

  #not allowed to be returned for public internet  names
  private-address: 10.200.200.0/24

  # Hide DNS Server info
  hide-identity: yes
  hide-version: yes

  #Limit DNS Fraud and use DNSSEC
  harden-glue: yes
  harden-dnssec-stripped: yes
  harden-referral-path: yes

  #Add an unwanted reply threshold to clean the cache and avoid when possible a DNS Poisoning
  unwanted-reply-threshold: 10000000

  #Have the validator print validation failures to the log.
  val-log-level: 1

  #Minimum lifetime of cache entries in seconds
  cache-min-ttl: 1800

  #Maximum lifetime of cached entries
  cache-max-ttl: 14400
  prefetch: yes
  prefetch-key: yes
EOM

systemctl disable systemd-resolved
systemctl stop systemd-resolved
systemctl enable unbound
systemctl restart unbound


#### Firewall, etc

sysctl -w net.ipv4.ip_forward=1

iptables -A INPUT -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
iptables -A FORWARD -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
iptables -A INPUT -p udp -m udp --dport 51820 -m conntrack --ctstate NEW -j ACCEPT
iptables -A INPUT -s 10.200.200.0/24 -p tcp -m tcp --dport 53 -m conntrack --ctstate NEW -j ACCEPT
iptables -A INPUT -s 10.200.200.0/24 -p udp -m udp --dport 53 -m conntrack --ctstate NEW -j ACCEPT
iptables -A OUTPUT -p udp -m udp --sport 51820 -j ACCEPT
ufw allow 22/tcp
ufw allow 51820/udp
echo "y" | ufw enable


#### Teh end

wg-quick up wg0
systemctl enable wg-quick@wg0
wg show
"""
