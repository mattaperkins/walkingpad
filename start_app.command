#!/bin/zsh
set -e

SCRIPT_PATH="${0:A}"
APP_DIR="${SCRIPT_PATH:h}"

cd "$APP_DIR"

if [[ ! -x venv/bin/python ]]; then
  echo "Virtual environment not found. Run this first:"
  echo "  cd \"$APP_DIR\" && python3 -m venv venv && venv/bin/python -m pip install -r requirements.txt"
  exit 1
fi

exec venv/bin/python run.py
