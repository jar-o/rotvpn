import os, time, base64, json
import boto3
from providers.common import get_my_ip, wireguard_port, install_wireguard

class AWSProvider:
    def __init__(self, deploy_name, config=None):
        self.deploy_name = 'rotvpn-{}'.format(deploy_name)
        self.aws_id = os.getenv('ROT_AWS_ID')
        if self.aws_id == None:
            raise Exception("Must set ROT_AWS_* env vars! See https://github.com/jar-o/rotvpn")
        self.aws_secret = os.getenv('ROT_AWS_SECRET')
        self.aws_region = os.getenv('ROT_AWS_REGION')
        self.client = boto3.client('ec2',
            aws_access_key_id=self.aws_id,
            aws_secret_access_key=self.aws_secret,
            region_name=self.aws_region)
        self.resource =  boto3.session.Session(
            aws_access_key_id=self.aws_id,
            aws_secret_access_key=self.aws_secret,
            aws_session_token=None,
        ).resource('ec2')
        self.key_name = 'rotvpn-aws-ecs-keypair-' + deploy_name
        self.key_fn = self.key_name + '.pem'
        if config != None:
            self.config = json.loads(config)
    def gen_ssh_keys(self):
        if os.path.exists(self.key_fn):
            print('SSH key already seems to exist ({}). Skipping generation.'.format(self.key_fn))
            return
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
    def provision(self):
        self.gen_ssh_keys()
        self.remove() # rotation
        # create the instance
        size = 't2.micro'

        if hasattr(self, 'config') and self.config != None:
            if 'size' in self.config:
                size = self.config['size']
        # Details about Ubuntu machine images here: https://cloud-images.ubuntu.com/locator/
        self.instance = create_ec2_instance(self.client, 'ami-0a7d051a1c4b54f65', size, self.key_name)
        self.instance_obj = self.resource.Instance(id=self.instance['InstanceId'])
        self.instance_obj.create_tags(Tags=[
            {
                'Key': 'Name',
                'Value': self.deploy_name,
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
        print('Instance is live: {}'.format(self.instance_obj.public_ip_address))
        install_wireguard(
            self.instance_obj.public_ip_address,
            self.key_fn,
            'peer-tunnel-configs-aws-{}.zip'.format(self.deploy_name),
            'ubuntu',
            '/home/ubuntu')
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
        reservations = self.client.describe_instances(
            Filters=[{'Name': 'instance-state-name', 'Values': ['running']}])["Reservations"]
        for res in reservations:
            for inst in res['Instances']:
                for tag in inst['Tags']:
                    instid = inst['InstanceId']
                    if tag['Key'] == 'Name' and tag['Value'] == self.deploy_name:
                        print('Instance {} found with ID {}. Terminating ...'.format(self.deploy_name, instid))
                        self.resource.instances.filter(InstanceIds = [instid]).terminate()
                        print('Done.')


# Stole from AWS docs
def create_ec2_instance(ec2_client, image_id, instance_type, keypair_name):
    """Provision and launch an EC2 instance

    The method returns without waiting for the instance to reach
    a running state.

    :param image_id: ID of AMI to launch, such as 'ami-XXXX'
    :param instance_type: string, such as 't2.micro'
    :param keypair_name: string, name of the key pair
    :return Dictionary containing information about the instance. If error,
    returns None.
    """

    # Provision and launch the EC2 instance
    # ec2_client = boto3.client('ec2')
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
