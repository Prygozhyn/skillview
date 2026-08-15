#!/bin/bash
# Double-click to start Skillview. Double-click-to-run is a macOS Finder
# convention for .command files specifically — on Linux this only runs via
# `bash run-ui.command` or `./run-ui.command`, same as any other script.
cd "$(dirname "$0")"
# `open` is macOS-only. `xdg-open` is its Linux desktop equivalent, but a
# headless box (very plausibly where this runs) has neither and no browser to
# open anyway — fall back to just printing the URL rather than failing.
if command -v open >/dev/null 2>&1; then
  open http://localhost:8477
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open http://localhost:8477
else
  echo "Open http://localhost:8477 in a browser once the server starts."
fi
exec python3 app.py
