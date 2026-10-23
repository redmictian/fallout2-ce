"""Initialize only container-local tmpfs state before starting sshd."""
import os
from pathlib import Path

Path("/run/sshd").mkdir(mode=0o755, exist_ok=True)
Path("/home/nixboy").mkdir(mode=0o700, exist_ok=True)
os.chown("/home/nixboy", 1000, 1000)
os.execv("/usr/sbin/sshd", ["/usr/sbin/sshd", "-D", "-e"])
