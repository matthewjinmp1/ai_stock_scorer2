#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
PROJECT_DIR="$(pwd)"

PORT="3001"
export PORT

WATCHED_FILES=(server.py index.html styles.css app.js run.html run.js result.html result.js)

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

kill_port_server() {
  if ! command -v lsof >/dev/null 2>&1; then
    return
  fi

  local pids
  pids="$(lsof -tiTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -z "${pids}" ]]; then
    return
  fi

  echo "Port ${PORT} is already in use. Stopping existing process..."
  while read -r pid; do
    if [[ -n "${pid}" ]] && [[ "${pid}" != "$$" ]]; then
      local command parent_pid parent_command
      command="$(ps -o command= -p "${pid}" 2>/dev/null || true)"
      parent_pid="$(ps -o ppid= -p "${pid}" 2>/dev/null | tr -d ' ' || true)"
      parent_command=""
      if [[ -n "${parent_pid}" ]] && [[ "${parent_pid}" != "$$" ]]; then
        parent_command="$(ps -o command= -p "${parent_pid}" 2>/dev/null || true)"
      fi

      if [[ "${command}" == *"${PROJECT_DIR}/server.py"* ]] || {
        [[ "${command}" == *"python"* ]] && [[ "${command}" == *"server.py"* ]] && [[ "${parent_command}" == *"${PROJECT_DIR}/start_server.sh"* ]]
      }; then
        if [[ "${parent_command}" == *"${PROJECT_DIR}/start_server.sh"* ]]; then
          kill "${parent_pid}" 2>/dev/null || true
        fi
        kill "${pid}" 2>/dev/null || true
      elif [[ "${parent_command}" == *"${PROJECT_DIR}/start_server.sh"* ]]; then
        kill "${parent_pid}" 2>/dev/null || true
        kill "${pid}" 2>/dev/null || true
      else
        echo "Port ${PORT} is used by another app:"
        echo "  PID ${pid}: ${command}"
        echo "Not killing it. Stop that app first, then rerun ./start_server.sh."
        exit 1
      fi
    fi
  done <<< "${pids}"
  sleep 0.5
}

stop_server() {
  if [[ -n "${SERVER_PID}" ]] && kill -0 "${SERVER_PID}" 2>/dev/null; then
    kill "${SERVER_PID}" 2>/dev/null || true
    wait "${SERVER_PID}" 2>/dev/null || true
  fi
}

trap 'stop_server; exit 0' INT TERM EXIT

last_signature="$(signature)"

while true; do
  kill_port_server
  python3 server.py &
  SERVER_PID="$!"
  echo "Serving only on http://localhost:${PORT}"
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
