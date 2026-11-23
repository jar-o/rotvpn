import paramiko
import qrcode
import io, time, os
import zipfile

from os import listdir
from os.path import isfile
from requests import get
from scp import SCPClient

from providers.wireguard import WireGuardConfig, WIREGUARD_PORT

wireguard_port = WIREGUARD_PORT

setup_script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'aux', 'setup.sh')

def get_my_ip():
    return get('https://api.ipify.org').text

def unzip_file(zip_path, dest_path):
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(dest_path)

def gen_qr_code(input_path, output_path):
    with open(input_path, 'rb') as f:
        input_data = f.read()
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(input_data)
    qr.make(fit=True)
    img = qr.make_image(fill='black', back_color='white')
    img.save(output_path)

def extract_configs_and_generate_qr_codes(zip_path):
    out_path = zip_path + '.extracted'
    unzip_file(zip_path, out_path)
    conf_files = [os.path.join(out_path, f) for f in listdir(out_path) if isfile(os.path.join(out_path, f))]
    for f in conf_files:
        of = f + '.png'
        gen_qr_code(f, of)
        print(f"QR code generated: {f} -> {of}")

def install_wireguard(ip_address, privkey_filename, server_config, username='root'):
    """Upload setup.sh and the server's wg0.conf, run the setup. Returns True on success."""
    privkey_filename = os.path.abspath(privkey_filename)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    max_tries = 12
    for i in range(1, max_tries + 1):
        print("Attempting to connect {}/{} ...".format(i, max_tries))
        try:
            client.connect(ip_address, username=username, key_filename=privkey_filename, timeout=15)
            break
        except (paramiko.ssh_exception.NoValidConnectionsError,
                paramiko.ssh_exception.SSHException,
                TimeoutError, OSError) as e:
            print('Not ready yet ({}). Retrying ...'.format(type(e).__name__))
            time.sleep(5)
    else:
        print('Could not connect to {} over SSH.'.format(ip_address))
        return False

    # Non-root users (e.g. ubuntu on AWS) land in their home dir; use relative paths.
    sudo = '' if username == 'root' else 'sudo '
    scp = SCPClient(client.get_transport())
    scp.put(setup_script_path, 'rotvpn-setup.sh')
    with io.BytesIO(server_config.encode()) as fl:
        scp.putfo(fl, 'rotvpn-wg0.conf', mode='0600')
    print('Installing server (running setup.sh remotely), takes a little time ...')
    stdin, stdout, stderr = client.exec_command(
        '{0}bash rotvpn-setup.sh rotvpn-wg0.conf; rc=$?; rm -f rotvpn-setup.sh; exit $rc'.format(sudo))
    out = stdout.read().decode(errors='replace')
    err = stderr.read().decode(errors='replace')
    exit_status = stdout.channel.recv_exit_status()
    client.close()
    if exit_status != 0:
        print('Error occurred. Cannot continue. Exit status {}'.format(exit_status))
        print(out)
        print(err)
        print('You may be able to SSH into the server and troubleshoot:')
        print('ssh -i {} {}@{}'.format(privkey_filename, username, ip_address))
        return False
    return True


def provision_wireguard(ip_address, privkey_filename, peer_config_dest, username='root'):
    """Generate keys locally, set up the server, write client configs + QR codes."""
    wg = WireGuardConfig()
    if not install_wireguard(ip_address, privkey_filename, wg.server_config(), username):
        return False
    wg.write_zip(ip_address, peer_config_dest)
    print('Peer configs available: {}'.format(peer_config_dest))
    extract_configs_and_generate_qr_codes(peer_config_dest)
    print('SUCCESS!')
    return True
