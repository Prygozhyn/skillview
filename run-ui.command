#!/bin/bash
# Double-click to start Skillview.
cd "$(dirname "$0")"
open http://localhost:8477
exec python3 app.py
