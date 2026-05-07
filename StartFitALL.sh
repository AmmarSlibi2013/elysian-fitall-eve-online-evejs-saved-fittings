#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
  ./Install.sh
  exit $?
fi

".venv/bin/python" desktop_app.py
