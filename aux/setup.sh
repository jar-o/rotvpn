#!/usr/bin/env bash
# rotvpn server setup. Works on current Debian (12/13) and Ubuntu (22.04/24.04+).
# Usage (as root): setup.sh /path/to/wg0.conf
# wg0.conf is generated locally by rotvpn; this script only installs and wires
# things up. It generates no keys.
set -euo pipefail

WG_CONF_SRC="${1:?usage: setup.sh /path/to/wg0.conf}"
WG_PORT=51820
V4_SERVER=10.200.200.1
V4_NET=10.200.200.0/24
V6_SERVER=fd86:ea04:1111::1
V6_NET=fd86:ea04:1111::/64

export DEBIAN_FRONTEND=noninteractive
APT="apt-get -o DPkg::Lock::Timeout=300 -yq"

# First-boot housekeeping (cloud-init / unattended upgrades) can hold apt locks.
command -v cloud-init >/dev/null && cloud-init status --wait >/dev/null 2>&1 || true

#### Packages
$APT update
$APT install wireguard-tools nftables unbound unattended-upgrades

#### Automatic security updates
cat > /etc/apt/apt.conf.d/20auto-upgrades <<EOF
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF

#### WireGuard
install -d -m 700 /etc/wireguard
install -m 600 "$WG_CONF_SRC" /etc/wireguard/wg0.conf
shred -u "$WG_CONF_SRC" 2>/dev/null || rm -f "$WG_CONF_SRC"

#### Forwarding
cat > /etc/sysctl.d/99-rotvpn.conf <<EOF
net.ipv4.ip_forward=1
net.ipv6.conf.all.forwarding=1
EOF
sysctl --system >/dev/null

#### DNS (unbound, reachable only from inside the tunnel)
# Not every provider names the NIC eth0 (ens5, enp1s0, ...), so ask the kernel.
WAN_IF=$(ip -o route get 1.1.1.1 | sed -n 's/.* dev \([^ ]*\).*/\1/p')
[ -n "$WAN_IF" ] || { echo "could not detect WAN interface" >&2; exit 1; }
echo "WAN interface: $WAN_IF"

cat > /etc/unbound/unbound.conf.d/rotvpn.conf <<EOF
server:
  interface: $V4_SERVER
  interface: $V6_SERVER
  # wg0 doesn't exist yet when unbound first starts
  ip-freebind: yes
  access-control: 0.0.0.0/0 refuse
  access-control: ::/0 refuse
  access-control: $V4_NET allow
  access-control: $V6_NET allow
  private-address: $V4_NET
  hide-identity: yes
  hide-version: yes
  harden-glue: yes
  harden-dnssec-stripped: yes
  harden-referral-path: yes
  val-log-level: 1
  cache-min-ttl: 1800
  cache-max-ttl: 14400
  prefetch: yes
  prefetch-key: yes
EOF
systemctl enable unbound
systemctl restart unbound

#### Firewall + NAT (nftables)
cat > /etc/nftables.conf <<EOF
#!/usr/sbin/nft -f
flush ruleset

table inet filter {
  chain input {
    type filter hook input priority 0; policy drop;
    ct state established,related accept
    ct state invalid drop
    iif lo accept
    meta l4proto { icmp, ipv6-icmp } accept
    tcp dport 22 accept
    udp dport $WG_PORT accept
    iifname "wg0" udp dport 53 accept
    iifname "wg0" tcp dport 53 accept
  }
  chain forward {
    type filter hook forward priority 0; policy drop;
    ct state established,related accept
    iifname "wg0" oifname "$WAN_IF" accept
  }
}

table inet rotvpn_nat {
  chain postrouting {
    type nat hook postrouting priority 100; policy accept;
    oifname "$WAN_IF" masquerade
  }
}
EOF
systemctl enable nftables
systemctl restart nftables

#### Start WireGuard
systemctl enable --now wg-quick@wg0
wg show
