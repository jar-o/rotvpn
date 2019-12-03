import sys, argparse
from providers import digitalocean


def get_provider(provider, deploy_name, config):
    if provider.lower() == "digitalocean":
        return digitalocean.DigitalOceanProvider(deploy_name, config)
    raise Exception('Could not match provider {}'.format(provider))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--provider', default='digitalocean', help='Specify the provider, i.e. digitalocean')
    parser.add_argument('--name', help="A name for your deploy, like 'mycoolvpn'. Lets you have multiple deploys for a provider.")
    parser.add_argument('--do', default='provision', help='Provision or remove your VPN: --do provision | --do remove')
    parser.add_argument('--config', help='Optional JSON config for your provider')
    args = parser.parse_args()

    if args.name == None:
        print("Must provide a name for your rotvpn deploy. E.g. my-cool-vpn")
        sys.exit(7)

    provider = get_provider(args.provider, args.name, args.config)

    if args.do == 'remove':
        provider.remove()
    else:
        provider.provision()
