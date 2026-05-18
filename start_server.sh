#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [[ -z "${PORT:-}" ]]; then
  PORT="$(python3 -c 'import socket
for port in range(3000, 3021):
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            continue
        print(port)
        break
else:
    raise SystemExit("No free port found between 3000 and 3020")')"
fi

export PORT
python3 server.py
