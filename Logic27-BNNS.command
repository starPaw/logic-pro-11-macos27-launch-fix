#!/bin/bash
set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ "$#" -eq 0 ]; then
  /usr/bin/env python3 "$SCRIPT_DIR/logic27_patch.py" doctor
  rc=$?
  echo
  if [ "$rc" -eq 0 ]; then
    echo "Preflight passed. Nothing was changed."
    echo "To apply from Terminal:"
    echo "  \"$SCRIPT_DIR/Logic27-BNNS.command\" apply"
  fi
  echo
  read -r -p "Press Return to close…" _
  exit "$rc"
fi

exec /usr/bin/env python3 "$SCRIPT_DIR/logic27_patch.py" "$@"
