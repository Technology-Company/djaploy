"""
Local inventory — targets a Debian 13 test server.
"""

from pathlib import Path

from djaploy.config import HostConfig

_keys_dir = Path(__file__).resolve().parent.parent.parent.parent / "keys"

hosts = [
    HostConfig(
        "test-server",
        ssh_hostname="37.27.31.22",
        ssh_user="root",
        ssh_key=str(_keys_dir / "id_ed25519"),
        app_user="app",
        env="local",
        services=["helloworld"],
    ),
]
