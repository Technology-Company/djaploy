"""
Local inventory — targets the Docker container running on localhost:2222.
"""

from djaploy.config import HostConfig

hosts = [
    HostConfig(
        "local-server",
        ssh_hostname="localhost",
        ssh_port=2222,
        ssh_user="deploy",
        app_user="app",
        env="local",
        services=["helloworld"],
    ),
]
