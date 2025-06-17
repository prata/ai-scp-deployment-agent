import os
import time
import logging
import socket
import configparser
import tempfile
import shutil
import paramiko
from datetime import datetime
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# --- Configuration Loading ---
config = configparser.ConfigParser()
config_file_path = os.path.join(os.path.dirname(__file__), 'config.ini')

if not os.path.exists(config_file_path):
    print(f"Error: config.ini not found at {config_file_path}")
    exit(1)

config.read(config_file_path)
LOCAL_WATCH_DIRECTORY = config['Agent']['local_watch_directory']
PROCESSED_DIRECTORY = config['Agent']['processed_directory']
FAILED_DIRECTORY = config['Agent']['failed_directory']
LOG_FILE = config['Agent']['log_file']
LOG_LEVEL = config['Agent'].get('log_level', 'INFO').upper()
REMOTE_USER = config['Remote']['remote_user']
REMOTE_HOST = config['Remote']['remote_host']
REMOTE_UPLOAD_DIRECTORY = config['Remote']['remote_upload_directory']
REMOTE_BUILD_SCRIPT = config['Remote']['remote_build_script']
SSH_PRIVATE_KEY_PATH = config['Remote'].get('ssh_private_key_path')
REMOTE_PASSWORD = config['Remote'].get('remote_password')

# Ensure directories exist
for d in [LOCAL_WATCH_DIRECTORY, PROCESSED_DIRECTORY, FAILED_DIRECTORY, os.path.dirname(LOG_FILE)]:
    os.makedirs(d, exist_ok=True)

# Logging Setup
numeric_log_level = getattr(logging, LOG_LEVEL, logging.INFO)
logging.basicConfig(level=numeric_log_level, format='%(asctime)s - %(levelname)s - %(message)s', handlers=[
    logging.FileHandler(LOG_FILE),
    logging.StreamHandler()
])
logger = logging.getLogger(__name__)

# --- Helper Functions ---
def preflight_check(host, port=22, timeout=5):
    try:
        with socket.create_connection((host, port), timeout):
            logger.info(f"Preflight check succeeded: {host}:{port} reachable.")
            return True
    except Exception as e:
        logger.error(f"Preflight check failed for {host}:{port}: {e}")
        return False

def atomic_move(src, dest_dir):
    os.makedirs(dest_dir, exist_ok=True)
    temp_dest = os.path.join(dest_dir, f".{os.path.basename(src)}.tmp")
    final_dest = os.path.join(dest_dir, os.path.basename(src))
    shutil.move(src, temp_dest)
    os.rename(temp_dest, final_dest)

# Private Key Loader
def load_private_key(key_path):
    if not os.path.exists(key_path):
        raise FileNotFoundError(f"SSH private key file not found: {key_path}")
    key_loaders = [(paramiko.Ed25519Key, "Ed25519"), (paramiko.ECDSAKey, "ECDSA"), (paramiko.RSAKey, "RSA")]
    for key_class, key_name in key_loaders:
        try:
            key = key_class.from_private_key_file(key_path)
            logger.debug(f"Loaded key as {key_name} from {key_path}")
            return key
        except paramiko.SSHException as e:
            logger.debug(f"Failed to load key as {key_name}: {e}")
    raise paramiko.SSHException(f"Could not load key from {key_path}. Tried Ed25519, ECDSA, and RSA.")

# SSH Client

def get_ssh_client():
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        if SSH_PRIVATE_KEY_PATH:
            private_key = load_private_key(SSH_PRIVATE_KEY_PATH)
            client.connect(hostname=REMOTE_HOST, username=REMOTE_USER, pkey=private_key)
        elif REMOTE_PASSWORD:
            client.connect(hostname=REMOTE_HOST, username=REMOTE_USER, password=REMOTE_PASSWORD)
        else:
            raise ValueError("No SSH credentials provided.")
        return client
    except Exception as e:
        logger.error(f"SSH connection failed: {e}")
        raise

def upload_file_via_scp(local_path, remote_path):
    client, sftp = None, None
    try:
        client = get_ssh_client()
        sftp = client.open_sftp()
        sftp.put(local_path, remote_path)
        logger.info(f"Uploaded {local_path} to {REMOTE_HOST}:{remote_path}")
        return True
    except Exception as e:
        logger.error(f"SCP upload failed: {e}")
        return False
    finally:
        if sftp: sftp.close()
        if client: client.close()

def run_remote_script(script_path):
    client = None
    try:
        client = get_ssh_client()
        stdin, stdout, stderr = client.exec_command(script_path)
        out, err = stdout.read().decode(), stderr.read().decode()
        exit_status = stdout.channel.recv_exit_status()
        if out: logger.info(f"Remote STDOUT:\n{out}")
        if err: logger.warning(f"Remote STDERR:\n{err}")
        if exit_status != 0:
            logger.error(f"Remote script failed with exit code {exit_status}")
            return False
        return True
    except Exception as e:
        logger.error(f"Remote script execution failed: {e}")
        return False
    finally:
        if client: client.close()

# --- Watchdog Handler ---
class MarkdownEventHandler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory: return
        file_path = event.src_path
        file_name = os.path.basename(file_path)
        if not file_name.endswith(".md"):
            logger.debug(f"Ignored non-markdown file: {file_name}")
            return
        logger.info(f"Detected new file: {file_name}")
        time.sleep(1)  # Allow file to settle
        try:
            current_date = datetime.now().strftime("%d%b%Y").upper()
            title = os.path.splitext(file_name)[0].replace('_', ' ').title()
            front_matter = f"---\ntitle: {title}\nauthor: Alex\ndate: {current_date}\n---\n\n"
            with open(file_path, 'r', encoding='utf-8') as f:
                original = f.read()
            with tempfile.NamedTemporaryFile(mode='w+', delete=False, encoding='utf-8', suffix='.md') as temp:
                temp.write(front_matter + original)
                temp_path = temp.name
            remote_file = os.path.join(REMOTE_UPLOAD_DIRECTORY, f"{current_date}_{file_name}")
            if not preflight_check(REMOTE_HOST):
                raise Exception("Preflight SSH check failed.")
            if not upload_file_via_scp(temp_path, remote_file):
                raise Exception("SCP upload failed.")
            if not run_remote_script(REMOTE_BUILD_SCRIPT):
                raise Exception("Remote script execution failed.")
            atomic_move(file_path, PROCESSED_DIRECTORY)
            logger.info(f"Processed and moved: {file_name}")
        except Exception as e:
            logger.error(f"Processing failed: {e}")
            atomic_move(file_path, FAILED_DIRECTORY)
        finally:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)

# --- Main Logic ---
def main():
    logger.info("Starting production AI Agent")
    event_handler = MarkdownEventHandler()
    observer = Observer()
    observer.schedule(event_handler, LOCAL_WATCH_DIRECTORY, recursive=False)
    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()

if __name__ == "__main__":
    main()
