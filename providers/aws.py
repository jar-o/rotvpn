import os
import boto3

from providers.base import Provider
from providers.common import get_my_ip, wireguard_port


class AWSProvider(Provider):
    name = 'aws'
    ssh_user = 'ubuntu'
    defaults = {
        'size': 't3.micro',
        'ami': None,  # None = look up the newest Ubuntu 24.04 LTS AMI in the region
    }

    def __init__(self, deploy_name, config=None):
        super().__init__(deploy_name, config)
        self.resource_name = 'rotvpn-{}'.format(deploy_name)  # EC2 Name tag
        self.aws_id = os.getenv('ROT_AWS_ID')
        if self.aws_id == None:
            raise Exception("Must set ROT_AWS_* env vars! See https://github.com/jar-o/rotvpn")
        self.aws_secret = os.getenv('ROT_AWS_SECRET')
        self.aws_region = os.getenv('ROT_AWS_REGION')
        self.client = boto3.client('ec2',
            aws_access_key_id=self.aws_id,
            aws_secret_access_key=self.aws_secret,
            region_name=self.aws_region)
        self.resource = boto3.session.Session(
            aws_access_key_id=self.aws_id,
            aws_secret_access_key=self.aws_secret,
            region_name=self.aws_region,
        ).resource('ec2')
        self.key_name = 'rotvpn-aws-ecs-keypair-' + deploy_name
        self.key_fn = self.key_name + '.pem'

    def ensure_ssh_key(self):
        if os.path.exists(self.key_fn):
            print('SSH key already seems to exist ({}). Skipping generation.'.format(self.key_fn))
            return self.key_fn
        keypair = None
        try:
            keypair = self.client.create_key_pair(KeyName=self.key_name)
        except self.client.exceptions.ClientError as e:
            if 'invalidkeypair.duplicate' not in str(e).lower():
                raise(e)
            print('Key {} already exists. Going to renew.'.format(self.key_name))
        if keypair == None:
            self.client.delete_key_pair(KeyName=self.key_name)
            keypair = self.client.create_key_pair(KeyName=self.key_name)
        with open(self.key_fn, 'w') as outfile:
            outfile.write(str(keypair['KeyMaterial']))
        os.chmod(self.key_fn, 0o600)
        return self.key_fn

    def create_server(self):
        ami = self.config['ami'] or self.latest_ubuntu_ami()
        self.instance = create_ec2_instance(self.client, ami, self.config['size'], self.key_name)
        if self.instance is None:
            raise Exception('Could not launch instance (see error above).')
        self.instance_obj = self.resource.Instance(id=self.instance['InstanceId'])
        self.instance_obj.create_tags(Tags=[
            {
                'Key': 'Name',
                'Value': self.resource_name,
            }
        ])
        print('Waiting for instance...')
        self.instance_obj.wait_until_running()
        self.instance_obj.load()
        try:
            self.set_inbound_rules()
        except self.client.exceptions.ClientError as e:
            if 'invalidpermission.duplicate' not in str(e).lower():
                raise(e)
            print('Inbound firewall rules already exist. Nothing to do.')
        return self.instance_obj.public_ip_address

    def latest_ubuntu_ami(self):
        # AMI IDs are per-region, so look up Canonical's newest Ubuntu LTS image.
        images = self.client.describe_images(
            Owners=['099720109477'],  # Canonical
            Filters=[
                {'Name': 'name', 'Values': ['ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*']},
                {'Name': 'state', 'Values': ['available']},
            ])['Images']
        if not images:
            raise Exception('No Ubuntu 24.04 AMI found in {}. Pass one via --config \'{{"ami":"ami-..."}}\''.format(self.aws_region))
        newest = max(images, key=lambda i: i['CreationDate'])
        print('Using {} ({})'.format(newest['ImageId'], newest['Name']))
        return newest['ImageId']

    def set_inbound_rules(self):
        response = self.client.describe_instances()
        for reservation in response["Reservations"]:
            for instance in reservation["Instances"]:
                if instance['InstanceId'] == self.instance['InstanceId']:
                    for sg in instance['SecurityGroups']:
                        if sg['GroupName'] == 'default':
                            print('Setting SSH inbound rule on {}'.format(self.instance['InstanceId']))
                            myip = get_my_ip()
                            self.client.authorize_security_group_ingress(
                                GroupId=sg['GroupId'],
                                IpPermissions=[{
                                    'FromPort': 22,
                                    'ToPort': 22,
                                    'IpProtocol': 'tcp',
                                    'IpRanges': [
                                        {'CidrIp': '{}/32'.format(myip)}
                                    ]
                                }],
                            )
                            print('Setting Wireguard inbound rule on {} for port {}'.format(
                                self.instance['InstanceId'], wireguard_port))
                            self.client.authorize_security_group_ingress(
                                GroupId=sg['GroupId'],
                                IpPermissions=[{
                                    'FromPort': wireguard_port,
                                    'ToPort': wireguard_port,
                                    'IpProtocol': 'udp',
                                    'IpRanges': [
                                        {'CidrIp': '0.0.0.0/0'}
                                    ]
                                }],
                            )

    def remove(self):
        # Match by Name tag server-side, and include every live state so a
        # pending/stopped instance isn't orphaned (and keeps costing money).
        reservations = self.client.describe_instances(Filters=[
            {'Name': 'tag:Name', 'Values': [self.resource_name]},
            {'Name': 'instance-state-name',
             'Values': ['pending', 'running', 'stopping', 'stopped']},
        ])["Reservations"]
        found = False
        for res in reservations:
            for inst in res['Instances']:
                instid = inst['InstanceId']
                print('Instance {} found with ID {}. Terminating ...'.format(self.resource_name, instid))
                self.resource.instances.filter(InstanceIds=[instid]).terminate()
                found = True
        print('Done.' if found else 'Deploy {} not found.'.format(self.resource_name))


# Stole from AWS docs
def create_ec2_instance(ec2_client, image_id, instance_type, keypair_name):
    """Provision and launch an EC2 instance

    The method returns without waiting for the instance to reach
    a running state.

    :param image_id: ID of AMI to launch, such as 'ami-XXXX'
    :param instance_type: string, such as 't3.micro'
    :param keypair_name: string, name of the key pair
    :return Dictionary containing information about the instance. If error,
    returns None.
    """
    try:
        response = ec2_client.run_instances(ImageId=image_id,
                                            InstanceType=instance_type,
                                            KeyName=keypair_name,
                                            MinCount=1,
                                            MaxCount=1)

    except ec2_client.exceptions.ClientError as e:
        print(e)
        return None
    return response['Instances'][0]
