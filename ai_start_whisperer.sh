#!/bin/bash

# Configuration
DB_FILE="courses.db"
VENV_DIR="venv-whisper"
SERVER_PORT=9000

echo "🎧 Setting up Local Whisper Server..."

# 1. Check Prerequisites
if ! command -v python3 &> /dev/null;
    then
    echo "❌ Python3 could not be found."
    exit 1
fi

if ! command -v ffmpeg &> /dev/null;
    then
    echo "❌ ffmpeg could not be found."
    echo "   Please install it (e.g., 'brew install ffmpeg' or 'sudo apt install ffmpeg')."
    exit 1
fi

# 2. Setup Virtual Environment
if [ ! -d "$VENV_DIR" ]; 
    then
    echo "📦 Creating virtual environment ($VENV_DIR)..."
    python3 -m venv $VENV_DIR
fi

echo "📦 Installing dependencies (Flask, faster-whisper)..."
source $VENV_DIR/bin/activate
pip install flask faster-whisper

# 3. Configure SkillForge Settings
if [ -f "$DB_FILE" ]; 
    then
    echo "⚙️  Auto-configuring SkillForge ($DB_FILE)..."
    python3 -c "
import sqlite3
try:
    conn = sqlite3.connect('$DB_FILE')
    cursor = conn.cursor()
    
    # Check if table exists
    cursor.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name='user_settings'\")
    if not cursor.fetchone():
        print('   ⚠️  Table user_settings not found. Skipping config.')
    else:
        # Get all users or just ID 1
        users = cursor.execute('SELECT id FROM users').fetchall()
        for u in users:
            uid = u[0]
            print(f'   - Updating settings for User ID {uid}...')
            
            # Enable AI
            cursor.execute('INSERT INTO user_settings (user_id, key, value) VALUES (?, ?, ?) ON CONFLICT(user_id, key) DO UPDATE SET value=?')
            cursor.execute('INSERT INTO user_settings (user_id, key, value) VALUES (?, ?, ?) ON CONFLICT(user_id, key) DO UPDATE SET value=?')
            
            # Set Provider to Local
            cursor.execute('INSERT INTO user_settings (user_id, key, value) VALUES (?, ?, ?) ON CONFLICT(user_id, key) DO UPDATE SET value=?')
            cursor.execute('INSERT INTO user_settings (user_id, key, value) VALUES (?, ?, ?) ON CONFLICT(user_id, key) DO UPDATE SET value=?')
            
            # Set Whisper URL
            cursor.execute('INSERT INTO user_settings (user_id, key, value) VALUES (?, ?, ?) ON CONFLICT(user_id, key) DO UPDATE SET value=?')
            cursor.execute('INSERT INTO user_settings (user_id, key, value) VALUES (?, ?, ?) ON CONFLICT(user_id, key) DO UPDATE SET value=?')
            
        conn.commit()
        print('   ✅ Configuration updated successfully!')
except Exception as e:
    print(f'   ❌ Error updating DB: {e}')
"
else
    echo "⚠️  Database file '$DB_FILE' not found. Skipping auto-configuration."
fi

# 4. Start Server
echo "🚀 Starting Whisper Server..."
echo "   Endpoint: http://localhost:$SERVER_PORT/v1/audio/transcriptions"
echo "   Model: base (CPU)"
echo "   Press Ctrl+C to stop."
echo ""

python3 whisper_server.py
