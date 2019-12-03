"""

Guides:

https://craighuther.com/2019/05/14/wireguard-setup-and-installation/
https://www.ckn.io/blog/2017/11/14/wireguard-vpn-typical-setup/
https://www.stavros.io/posts/how-to-configure-wireguard/
https://emanuelduss.ch/2018/09/wireguard-vpn-road-warrior-setup/
"""

import sys, argparse
from providers import digitalocean


def get_provider(provider, deploy_name):
    if provider.lower() == "digitalocean":
        return digitalocean.DigitalOceanProvider(deploy_name)
    raise Exception('Could not match provider {}'.format(provider))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--provider', nargs='?', default='digitalocean', help='Specify the provider, i.e. digitalocean')
    parser.add_argument('--name', help="A name for your deploy, like 'mycoolvpn'. Lets you have multiple deploys for a provider.")
    parser.add_argument('--do', nargs='?', default='provision', help='Provision or remove your VPN: --do provision | --do remove')
    args = parser.parse_args()
    if args.name == None:
        print("Must provide a name for your rotvpn deploy. E.g. my-cool-vpn")
        sys.exit(7)

    provider = get_provider(args.provider, args.name)

    if args.do == 'remove':
        provider.remove()
    else:
        provider.provision()
