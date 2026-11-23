import importlib

# name -> (module, class). Imported lazily so you only need the SDK for the
# provider you actually use (e.g. no boto3 required for DigitalOcean).
_REGISTRY = {
    'digitalocean': ('providers.digitalocean', 'DigitalOceanProvider'),
    'aws': ('providers.aws', 'AWSProvider'),
    'hetzner': ('providers.hetzner', 'HetznerProvider'),
    'vultr': ('providers.vultr', 'VultrProvider'),
}


def available():
    return sorted(_REGISTRY)


def get_provider(name, deploy_name, config=None):
    try:
        module, cls = _REGISTRY[name.lower()]
    except KeyError:
        raise Exception('Unknown provider {!r}. Available: {}'.format(name, ', '.join(available())))
    return getattr(importlib.import_module(module), cls)(deploy_name, config)
