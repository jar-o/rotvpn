#### Ubuntu 18.04


#### Installation


echo "nameserver 8.8.8.8" | tee /etc/resolv.conf > /dev/null

add-apt-repository -y universe
apt install wireguard -y
apt install zip -y
apt install unbound unbound-host -y


#### Wireguard

umask 077 && wg genkey | tee privatekey | wg pubkey > publickey

publicaddr=$(dig +short myip.opendns.com @resolver1.opendns.com)
ipv6_prefix='fd86:ea04:1111'
fn='/etc/wireguard/wg0.conf'
echo '[Interface]' > $fn
echo "PrivateKey = $(cat privatekey)" >> $fn
echo "Address = 10.200.200.1/24, ${ipv6_prefix}::1/64" >> $fn
echo 'ListenPort = 51820' >> $fn
echo 'PostUp = iptables -A FORWARD -i wg0 -j ACCEPT; iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE; ip6tables -A FORWARD -i wg0 -j ACCEPT; ip6tables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
PostDown = iptables -D FORWARD -i wg0 -j ACCEPT; iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE; ip6tables -D FORWARD -i wg0 -j ACCEPT; ip6tables -t nat -D POSTROUTING -o eth0 -j MASQUERADE
SaveConfig = true' >> $fn

rm -f peer-tunnel-configs.zip
for i in `seq 2 11`; do
        j=$(expr $i - 1)
        prefix="peer${j}"
        umask 077 && wg genpsk > client-preshared
        umask 077 && wg genkey | tee client-privatekey | wg pubkey > client-pubkey
        # add to wireguard server config file
        echo '' >> $fn
        echo '[Peer]' >> $fn
        echo "PublicKey = $(cat client-pubkey)" >> $fn
        echo "PresharedKey = $(cat client-preshared)" >> $fn
        echo "AllowedIPs = 10.200.200.${i}/32, ${ipv6_prefix}::${i}/128" >> $fn
        # create the client tunnel file
        clifn="$prefix-tunnel.conf"
        echo "[Interface]" >> $clifn
        echo "Address = 10.200.200.${i}/24, ${ipv6_prefix}::${i}/64" >> $clifn
        echo "PrivateKey = $(cat client-privatekey)" >> $clifn
        echo "DNS = 10.200.200.1" >> $clifn
        echo "" >> $clifn
        echo "[Peer]" >> $clifn
        echo "PublicKey = $(cat publickey)" >> $clifn
        echo "PresharedKey = $(cat client-preshared)" >> $clifn
        echo "AllowedIPs = 0.0.0.0/0,::/0" >> $clifn
        echo "Endpoint = $publicaddr:51820" >> $clifn
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
sysctl -w net.ipv6.conf.all.forwarding=1

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
