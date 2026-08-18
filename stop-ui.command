#!/bin/bash
# Double-click to stop Skillview. Companion to run-ui.command.
cd "$(dirname "$0")"
PORT=$(python3 -c "import json,os,pathlib; c=pathlib.Path('config.json'); print(int(os.environ.get('PORT') or (json.loads(c.read_text()).get('port', 8477) if c.exists() else 8477)))")

# Target the exact PID bound to Skillview's port, never a name match — a
# broader match like `pkill -f app.py` can catch an unrelated project's
# same-named script on a different port.
PID=$(lsof -ti "tcp:${PORT}" -sTCP:LISTEN)

if [ -z "$PID" ]; then
  echo "Skillview isn't running on port ${PORT}."
else
  kill "$PID"
  echo "Stopped Skillview (pid ${PID}, port ${PORT})."
fi

read -p "Press Return to close this window..."
