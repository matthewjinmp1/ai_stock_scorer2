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

WATCHED_FILES=(server.py index.html styles.css app.js run.html run.js)

signature() {
  python3 -c 'import os, sys
parts = []
for name in sys.argv[1:]:
    if os.path.exists(name):
        stat = os.stat(name)
        parts.append(f"{name}:{stat.st_mtime_ns}:{stat.st_size}")
print("|".join(parts))' "${WATCHED_FILES[@]}"
}

SERVER_PID=""

stop_server() {
  if [[ -n "${SERVER_PID}" ]] && kill -0 "${SERVER_PID}" 2>/dev/null; then
    kill "${SERVER_PID}" 2>/dev/null || true
    wait "${SERVER_PID}" 2>/dev/null || true
  fi
}

trap 'stop_server; exit 0' INT TERM EXIT

last_signature="$(signature)"

while true; do
  python3 server.py &
  SERVER_PID="$!"
  echo "Watching for changes. Press Ctrl-C to stop."

  while kill -0 "${SERVER_PID}" 2>/dev/null; do
    sleep 1
    current_signature="$(signature)"
    if [[ "${current_signature}" != "${last_signature}" ]]; then
      last_signature="${current_signature}"
      echo "Change detected. Restarting server..."
      stop_server
      break
    fi
  done

  if ! kill -0 "${SERVER_PID}" 2>/dev/null; then
    wait "${SERVER_PID}" 2>/dev/null || true
  fi
done
