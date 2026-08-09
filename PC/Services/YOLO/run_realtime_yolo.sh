#!/bin/bash
# Run the YOLO realtime viewer using the venv Python.
cd "$(dirname "$0")"
./.venv/bin/python realtime_yolo.py "$@"
