import paramiko
import os
import logging

# Configure basic logging to see paramiko's verbose output
logging.basicConfig(level=logging.DEBUG)
paramiko.util.log_to_file("paramiko_debug.log") # This will create a detailed log of paramiko's operations

# Your config values
SSH_PRIVATE_KEY_PATH = "/home/user/.ssh/ssh_key"
REMOTE_USER = "username"
REMOTE_HOST = "hostname"

print(f"Attempting to load key from: {SSH_PRIVATE_KEY_PATH}")
print(f"Key file exists: {os.path.exists(SSH_PRIVATE_KEY_PATH)}")

if not os.path.exists(SSH_PRIVATE_KEY_PATH):
    print("Error: Private key file not found at the specified path.")
    exit(1)

try:
    # Try loading the private key
    private_key = paramiko.RSAKey.from_private_key_file(SSH_PRIVATE_KEY_PATH)
    print("Successfully loaded private key.")

    # Try connecting
    client = paramiko.SSHClient()
    client.load_system_host_keys() # Load known_hosts
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy()) # For first connection, handle unknown hosts

    print(f"Attempting to connect to {REMOTE_HOST} as {REMOTE_USER} using the key...")
    client.connect(hostname=REMOTE_HOST, username=REMOTE_USER, pkey=private_key, timeout=10) # Added timeout

    print("Successfully connected to SSH server!")

    # Optionally run a simple command
    stdin, stdout, stderr = client.exec_command('echo "Hello from remote server!"')
    print("Remote command STDOUT:")
    print(stdout.read().decode().strip())
    if stderr.read().decode().strip():
        print("Remote command STDERR:")
        print(stderr.read().decode().strip())

    client.close()
    print("Connection closed.")

except paramiko.PasswordRequiredException:
    print("ERROR: The private key requires a passphrase. Please provide it or remove it from the key.")
except paramiko.AuthenticationException:
    print("ERROR: Authentication failed. Check username, key, and server-side authorized_keys.")
except paramiko.SSHException as e:
    print(f"ERROR: SSH connection/key loading failed: {e}")
    if "unpack requires a buffer of 4 bytes" in str(e):
        print("This error strongly suggests an issue with the SSH private key's format or encryption.")
        print("Please ensure the key is in PEM format and does not have a passphrase for automated use.")
        print("Run 'ssh-keygen -p -m PEM -f /home/username/.ssh/id_rsa_aiagent' to fix the format.")
except Exception as e:
    print(f"An unexpected error occurred: {e}")
