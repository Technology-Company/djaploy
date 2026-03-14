#!/bin/bash
set -e

# Start nginx
nginx

echo "=== djaploy test server ready ==="
echo "  SSH:   port 22 (mapped to 2222)"
echo "  Nginx: port 80 (mapped to 8080)"
echo "================================="

# Start SSH in foreground
exec /usr/sbin/sshd -D
