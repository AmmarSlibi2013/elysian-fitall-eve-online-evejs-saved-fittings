#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

echo
echo "Elysian FitALL"
echo "Adds a saved fitting for every single ship to all characters."
echo

free_kb="$(df -Pk . | awk 'NR==2 {print $4}')"
if [ "${free_kb:-0}" -lt 1048576 ]; then
  echo "FitALL needs at least 1GB free disk space for setup."
  exit 1
fi

have_python() {
  command -v python3 >/dev/null 2>&1 && python3 - <<'PY'
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY
}

install_python_linux() {
  if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update
    sudo apt-get install -y python3 python3-venv python3-pip libxcb-cursor0 libxkbcommon-x11-0 libegl1
  elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y python3 python3-pip python3-devel qt6-qtbase-gui
  elif command -v yum >/dev/null 2>&1; then
    sudo yum install -y python3 python3-pip
  elif command -v pacman >/dev/null 2>&1; then
    sudo pacman -Sy --needed python python-pip
  elif command -v zypper >/dev/null 2>&1; then
    sudo zypper install -y python3 python3-pip python3-venv
  elif command -v apk >/dev/null 2>&1; then
    sudo apk add python3 py3-pip py3-virtualenv
  else
    echo "No supported Linux package manager was found. Install Python 3.10+, then run Install.sh again."
    exit 1
  fi
}

install_python_macos() {
  if command -v brew >/dev/null 2>&1; then
    brew install python
  else
    pkg="/tmp/python-3.12.10-macos11.pkg"
    curl -L "https://www.python.org/ftp/python/3.12.10/python-3.12.10-macos11.pkg" -o "$pkg"
    sudo installer -pkg "$pkg" -target /
  fi
}

if ! have_python; then
  echo "Python 3.10+ was not found. Installing Python now..."
  case "$(uname -s)" in
    Darwin) install_python_macos ;;
    Linux) install_python_linux ;;
    *) echo "Unsupported OS. Install Python 3.10+, then run Install.sh again."; exit 1 ;;
  esac
fi

if ! have_python; then
  echo "Python 3.10+ is still not available on PATH."
  exit 1
fi

python3 -m venv .venv
".venv/bin/python" -m pip install --upgrade pip
".venv/bin/python" -m pip install -r requirements.txt

evejs_path=""
if [ -f "config/evejs.path" ]; then
  evejs_path="$(cat config/evejs.path)"
fi

while true; do
  if [ -z "$evejs_path" ]; then
    printf "Paste your EVE JS folder path, then press Enter: "
    IFS= read -r evejs_path
  fi
  evejs_path="${evejs_path%\"}"
  evejs_path="${evejs_path#\"}"
  if ".venv/bin/python" - "$evejs_path" <<'PY'
import pathlib
import sys
import fitall
fitall.configure_evejs_root(pathlib.Path(sys.argv[1]))
fitall.ensure_evejs_runtime_ready()
print("EVE JS ready:", fitall.REPO_ROOT)
PY
  then
    break
  fi
  echo "That folder was not an EVE JS checkout with the expected database files."
  evejs_path=""
done

echo
echo "Launching Elysian FitALL..."
".venv/bin/python" desktop_app.py
