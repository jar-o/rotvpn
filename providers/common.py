import time, os
from requests import get
import paramiko
from scp import SCPClient, SCPException

wireguard_port = 51820

# TODO it's sort of bothersome that this requires a matching edit in
# setup-ubuntu.sh. Should templatize and drive from Python side.
peer_config_download = 'peer-tunnel-configs.zip'

def get_my_ip():
    return get('https://api.ipify.org').text

def install_wireguard(ip_address, privkey_filename, peer_config_download_dest, username='root', home='/root'):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    for i in range(5):
        print("Attempting to connect {}/{} ...".format(i, 5))
        try:
            client.connect(ip_address, username=username, key_filename=os.path.abspath(privkey_filename))
        except paramiko.ssh_exception.NoValidConnectionsError:
            print('No valid connection. (Server probably not ready.)')
            time.sleep(5)
            continue
        except TimeoutError:
            print("Timeout. Hm... let's try again")
            time.sleep(5)
            continue
        scp = SCPClient(client.get_transport())
        setup_script = './aux/setup-ubuntu.sh'
        print('Installing server (running script {} remotely), takes a little time ...'.format(setup_script))
        #TODO ensure pathing
        scp.put('./aux/setup-ubuntu.sh', '{}/setup.sh'.format(home))
        if username == 'root':
            stdin, stdout, stderr = client.exec_command('{}/setup.sh'.format(home))
        else: # Assume sudo
            cmd1 = '{}/setup.sh'.format(home)
            cmd2 = 'chmod a+rw {}'.format(peer_config_download)
            cmd3 = 'mv {} {}'.format(peer_config_download, home)
            cmd =  "sudo su - root -c 'sleep 15 && {} && {} && {}'".format(cmd1, cmd2, cmd3)
            print('Running {}'.format(cmd))
            stdin, stdout, stderr = client.exec_command(cmd)
        exit_status = stdout.channel.recv_exit_status() # Blocking call
        if exit_status != 0:
            print('Error occured. Cannot continue Exit status {}'.format(exit_status))
            print('{}'.format(stdout))
            print('{}'.format(stderr))
            print('You may be able to SSH into the server and troubleshoot:')
            print('ssh -i {} {}@{}'.format(os.path.abspath(privkey_filename), username, ip_address))
            return
        # now, retrieve the generated peer configs
        try:
            os.remove(peer_config_download)
            os.remove(peer_config_download_dest)
        except FileNotFoundError:
            pass
        for j in range(10):
            try:
                scp.get('{}/{}'.format(home, peer_config_download))
            except SCPException:
                print("{} not avilable. Trying again {}/{}".format(peer_config_download, j, 10))
                time.sleep(5)
                continue
            break
        if os.path.exists(peer_config_download):
            os.rename(peer_config_download, peer_config_download_dest)
            print('Peer configs available: {}'.format(peer_config_download_dest))
            print('SUCCESS!')
        else:
            print('Something went wrong? No {} found.'.format(peer_config_download))
        break
