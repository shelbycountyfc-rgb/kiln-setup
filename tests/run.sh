#!/usr/bin/env bash
# Every check that needs no root and no Arch machine.  CI runs this file; so
# can anyone with Python and Tk.
set -Eeuo pipefail
cd "$(dirname "$0")/.."

echo "== compile"
python3 -m compileall -q installer panel

echo "== self-test (13 phases, dry run)"
python3 -m installer --selftest | tail -3

echo "== installer checks"
python3 tests/check-installer.py

echo "== window checks"
if [ -n "${DISPLAY:-}" ]; then
  python3 tests/check-window.py
elif command -v xvfb-run >/dev/null 2>&1; then
  xvfb-run -a python3 tests/check-window.py
else
  echo "SKIP: no display and no xvfb-run; the window checks need one of them"
fi
