#!/bin/bash
if command -v python3 &>/dev/null; then
    echo "Starting SkillForge..."
    # Open browser after delay
    (sleep 2 && open "http://localhost:5001") &
    python3 app.py
else
    echo "Python3 is not installed."
fi
