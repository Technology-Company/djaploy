"""
Local inventory — targets a Debian 13 test server.
"""

from pathlib import Path

from djaploy.config import BorgBackupConfig, HostConfig

_keys_dir = Path(__file__).resolve().parent.parent.parent.parent / "keys"

hosts = [
    HostConfig(
        "test-server",
        ssh_hostname="localhost",
        ssh_user="deploy",
        ssh_port=2222,
        ssh_key=str(_keys_dir / "id_ed25519"),
        app_user="app",
        env="local",
        services=["helloworld"],
        borg_backup=BorgBackupConfig(
            # Local repo (no SSH) — for testing
            repo_path="/home/app/borg-repo",
            passphrase="test-passphrase",
            databases=["db.sqlite3"],
            db_path="/home/app/apps/helloworld/shared",
            media_path="/home/app/apps/helloworld/shared/helloworld/public/media",
            backup_media=True,
            schedule="0 2 * * *",
        ),
    ),
]
