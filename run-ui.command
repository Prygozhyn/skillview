#!/bin/bash
# Double-click to start Skillview. Double-click-to-run is a macOS Finder
# convention for .command files specifically — on Linux this only runs via
# `bash run-ui.command` or `./run-ui.command`, same as any other script.
#
# --enable-updates is on by default here: this is the personal launcher, not
# the bare `python3 app.py` a stranger cloning the public repo would run.
# That command stays gated behind the flag on its own — this script just
# always supplies it for you.
cd "$(dirname "$0")"
PORT=$(python3 -c "import json,os,pathlib; c=pathlib.Path('config.json'); print(int(os.environ.get('PORT') or (json.loads(c.read_text()).get('port', 8477) if c.exists() else 8477)))")

python3 app.py --enable-updates &
SERVER_PID=$!

# Wait for the server to actually answer before opening a tab — opening
# immediately races the server startup and can point the browser at a
# connection error instead of the page.
for i in $(seq 1 50); do
  curl -sf "http://localhost:${PORT}/" >/dev/null 2>&1 && break
  sleep 0.1
done

# A cache-busting query string, not just the bare URL: `open -a "Google
# Chrome" url` reuses an existing tab already sitting on that exact URL
# instead of loading it fresh, so a tab left open from a previous run keeps
# showing whatever ui.html looked like back then, forever. Making the URL
# different each launch forces a real navigation every time.
URL="http://localhost:${PORT}/?t=$(date +%s)"

# Prefer Chrome specifically, since that's the browser wanted for this flow.
# `open` is macOS-only; `xdg-open` is its Linux equivalent but a headless box
# has neither and no browser to open anyway — fall back to printing the URL.
if command -v open >/dev/null 2>&1; then
  if [ -d "/Applications/Google Chrome.app" ]; then
    open -a "Google Chrome" "$URL"
  else
    open "$URL"
  fi
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$URL"
else
  echo "Open ${URL} in a browser once the server starts."
fi

wait "$SERVER_PID"
