import os
import sqlite3
import re
from flask import Flask, render_template, jsonify, send_from_directory, request, abort, redirect, url_for, flash, send_file
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import uuid
import datetime
import base64
import io
import json
import requests
import subprocess
import shutil
import time
from reportlab.lib.pagesizes import landscape, A4
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.platypus import Paragraph, Frame
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from tinytag import TinyTag

# AI Import
try:
    from google import genai
except ImportError:
    genai = None

app = Flask(__name__)
app.secret_key = 'skillforge_secret_key_change_this_in_production'  # Required for sessions

# --- Configuration ---
basedir = os.path.abspath(os.path.dirname(__file__))
DB_FILE = os.path.join(basedir, "courses.db")
COURSES_DIR = "courses"

# --- Login Manager Setup ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# --- User Model ---
class User(UserMixin):
    def __init__(self, id, username, name, address, profile_pic=None, is_admin=False):
        self.id = id
        self.username = username
        self.name = name
        self.address = address
        self.profile_pic = profile_pic
        self.is_admin = is_admin

@login_manager.user_loader
def load_user(user_id):
    conn = get_db_connection()
    user_row = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    conn.close()
    if user_row:
        # Check for profile_pic column in Row
        pic = user_row['profile_pic'] if 'profile_pic' in user_row.keys() else None
        return User(user_row['id'], user_row['username'], user_row['name'], user_row['address'], profile_pic=pic)
    return None

@app.template_filter('format_time')
def format_time(seconds):
    if not seconds: return "00:00"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"

@app.template_filter('format_duration_human')
def format_duration_human(seconds):
    if not seconds: return "0m"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}h {m}m"
    return f"{m}m"

# --- Subtitle Helper ---
def srt_to_vtt(content):
    """Simple regex-based SRT to VTT converter"""
    vtt = ["WEBVTT\n\n"]
    # Normalize line endings
    content = content.replace('\r\n', '\n').replace('\r', '\n')
    
    # Split by double newlines to get blocks
    blocks = content.strip().split('\n\n')
    
    for block in blocks:
        lines = block.split('\n')
        if len(lines) >= 2:
            # Check if first line looks like an index
            if lines[0].isdigit():
                timestamp_line_idx = 1
            else:
                timestamp_line_idx = 0
            
            if timestamp_line_idx < len(lines):
                ts = lines[timestamp_line_idx]
                ts = ts.replace(',', '.') # SRT uses comma, VTT uses dot
                
                payload = lines[timestamp_line_idx+1:]
                
                vtt.append(f"{ts}\n")
                vtt.extend([l + '\n' for l in payload])
                vtt.append('\n')
                
    return "".join(vtt)

def parse_subtitle_to_json(content):
    """Parses SRT, VTT or JSON content into a list of subtitle objects."""
    content = content.replace('\r\n', '\n').replace('\r', '\n').strip()
    
    # Check if content is actually JSON (or contains JSON after WEBVTT)
    json_str = ""
    if content.startswith('[') or content.startswith('{'):
        json_str = content
    elif 'WEBVTT' in content and '[' in content:
        json_str = content.split('[', 1)[1]
        json_str = '[' + json_str.rsplit(']', 1)[0] + ']'
    
    if json_str:
        try:
            # Try to load as-is first
            data = json.loads(json_str)
        except:
            # If it fails, maybe it's truncated. Try to fix a truncated list.
            try:
                # Find last valid closing brace for an object
                last_brace = json_str.rfind('}')
                if last_brace != -1:
                    fixed_json = json_str[:last_brace+1] + ']'
                    data = json.loads(fixed_json)
                else:
                    data = []
            except:
                data = []
        
        if data:
            transcript = []
            for item in data:
                try:
                    start = item.get('start', item.get('start_time', 0))
                    text = item.get('text', '')
                    transcript.append({'start': float(start), 'text': text})
                except:
                    continue
            return transcript

    blocks = content.split('\n\n')
    transcript = []
    
    for block in blocks:
        lines = block.split('\n')
        if len(lines) >= 2:
            timestamp_line = lines[1] if lines[0].isdigit() else lines[0]
            if '-->' in timestamp_line:
                start_time_str = timestamp_line.split('-->')[0].strip().replace(',', '.')
                
                # Convert "HH:MM:SS.mmm" or "MM:SS.mmm" to seconds
                parts = start_time_str.split(':')
                seconds = 0
                if len(parts) == 3: # HH:MM:SS.mmm
                    seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
                elif len(parts) == 2: # MM:SS.mmm
                    seconds = int(parts[0]) * 60 + float(parts[1])
                
                text = " ".join(lines[lines.index(timestamp_line)+1:])
                transcript.append({'start': seconds, 'text': text})
    return transcript

# --- Achievements Configuration ---
ACHIEVEMENTS = {
    'first_steps': {
        'title': 'First Steps',
        'description': 'Watch your first video segment.',
        'icon': '🌱'
    },
    'dedicated_learner': {
        'title': 'Dedicated Learner',
        'description': 'Accumulate 1 hour of watch time.',
        'icon': '🎓'
    },
    'knowledge_sponge': {
        'title': 'Knowledge Sponge',
        'description': 'Accumulate 10 hours of watch time.',
        'icon': '🧠'
    },
    'night_owl': {
        'title': 'Night Owl',
        'description': 'Watch a video between 12 AM and 4 AM.',
        'icon': '🦉'
    },
    'early_bird': {
        'title': 'Early Bird',
        'description': 'Watch a video between 5 AM and 8 AM.',
        'icon': '🐦'
    },
    'on_fire': {
        'title': 'On Fire',
        'description': 'Maintain a 3-day learning streak.',
        'icon': '🔥'
    },
    'unstoppable': {
        'title': 'Unstoppable',
        'description': 'Maintain a 7-day learning streak.',
        'icon': '🚀'
    },
    'weekend_warrior': {
        'title': 'Weekend Warrior',
        'description': 'Learn on both Saturday and Sunday.',
        'icon': '📅'
    }
}

def check_new_achievements(conn, user_id):
    """Checks for new achievements for the user and returns a list of newly unlocked ones."""
    new_unlocks = []
    
    # Get existing achievements
    existing = {row['achievement_id'] for row in conn.execute('SELECT achievement_id FROM user_achievements WHERE user_id = ?', (user_id,)).fetchall()}
    
    # 1. Check Totals
    total_stats = conn.execute('SELECT SUM(seconds_watched) as t FROM daily_activity WHERE user_id=?', (user_id,)).fetchone()
    total_time = total_stats['t'] or 0
    
    if 'first_steps' not in existing and total_time > 10:
        new_unlocks.append('first_steps')
        
    if 'dedicated_learner' not in existing and total_time >= 3600:
        new_unlocks.append('dedicated_learner')
        
    if 'knowledge_sponge' not in existing and total_time >= 36000:
        new_unlocks.append('knowledge_sponge')

    # 2. Check Time of Day (using current server time as proxy)
    now = datetime.datetime.now()
    hour = now.hour
    if 'night_owl' not in existing and 0 <= hour < 4:
        new_unlocks.append('night_owl')
    if 'early_bird' not in existing and 5 <= hour < 8:
        new_unlocks.append('early_bird')

    # 3. Check Streaks
    activity_rows = conn.execute('SELECT date, seconds_watched FROM daily_activity WHERE user_id=? ORDER BY date DESC', (user_id,)).fetchall()
    streak = 0
    check_date = datetime.date.today()
    activity_map = {row['date']: row['seconds_watched'] for row in activity_rows}
    
    while True:
        date_str = check_date.strftime("%Y-%m-%d")
        if date_str in activity_map and activity_map[date_str] > 0:
            streak += 1
            check_date -= datetime.timedelta(days=1)
        else:
            # If today has no activity yet, don't break streak from yesterday
            if date_str == datetime.date.today().strftime("%Y-%m-%d") and streak == 0:
                 check_date -= datetime.timedelta(days=1)
                 continue
            break
            
    if 'on_fire' not in existing and streak >= 3:
        new_unlocks.append('on_fire')
    if 'unstoppable' not in existing and streak >= 7:
        new_unlocks.append('unstoppable')

    # 4. Check Weekend Warrior
    # Need to check if they have activity on a Sat and Sun in the same week? 
    # Or just "Ever watched on Sat AND Ever watched on Sun"? Let's do the latter for simplicity, or check recent history.
    # Let's do "Watched on a Sat and a Sun" (anytime).
    if 'weekend_warrior' not in existing:
        has_sat = False
        has_sun = False
        # Limit to last 30 days to save perf? Or just iterate all.
        for row in activity_rows:
            d = datetime.datetime.strptime(row['date'], "%Y-%m-%d")
            if d.weekday() == 5: has_sat = True
            if d.weekday() == 6: has_sun = True
            if has_sat and has_sun:
                new_unlocks.append('weekend_warrior')
                break

    # Persist new unlocks
    for ach_id in new_unlocks:
        conn.execute('INSERT INTO user_achievements (user_id, achievement_id) VALUES (?, ?)', (user_id, ach_id))
        
    # Return enriched objects
    result = []
    for aid in new_unlocks:
        item = ACHIEVEMENTS[aid].copy()
        item['id'] = aid
        result.append(item)
        
    return result

# --- Helper Functions ---

def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    
    # 1. Basic Tables
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS courses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            folder_name TEXT NOT NULL UNIQUE,
            is_favorite BOOLEAN DEFAULT 0,
            description TEXT,
            alternate_title TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        
        CREATE TABLE IF NOT EXISTS modules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id INTEGER,
            title TEXT NOT NULL,
            order_index INTEGER,
            FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            module_id INTEGER,
            title TEXT NOT NULL,
            filename TEXT NOT NULL,
            path TEXT NOT NULL,
            order_index INTEGER,
            duration REAL DEFAULT 0,
            FOREIGN KEY (module_id) REFERENCES modules (id) ON DELETE CASCADE
        );
        
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            name TEXT,
            address TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        
        CREATE TABLE IF NOT EXISTS video_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            video_path TEXT NOT NULL,
            content TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
            UNIQUE(user_id, video_path)
        );

        CREATE TABLE IF NOT EXISTS course_progress (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            course_id INTEGER NOT NULL,
            last_video_path TEXT,
            last_video_title TEXT,
            last_video_timestamp REAL DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
            UNIQUE(user_id, course_id)
        );

        CREATE TABLE IF NOT EXISTS watched_videos (
            user_id INTEGER,
            course_id INTEGER,
            video_path TEXT,
            PRIMARY KEY (user_id, course_id, video_path),
            FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS video_progress (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            video_path TEXT NOT NULL,
            watched_time REAL DEFAULT 0,
            is_completed BOOLEAN DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
            UNIQUE(user_id, video_path)
        );

        CREATE TABLE IF NOT EXISTS bookmarks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            course_id INTEGER,
            video_path TEXT NOT NULL,
            video_title TEXT,
            timestamp REAL NOT NULL,
            note TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        );
        
        CREATE TABLE IF NOT EXISTS daily_activity (
            user_id INTEGER,
            date TEXT NOT NULL, -- YYYY-MM-DD
            seconds_watched REAL DEFAULT 0,
            videos_completed INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, date),
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS playlists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            title TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS playlist_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            playlist_id INTEGER,
            video_path TEXT,
            video_title TEXT,
            course_id INTEGER,
            order_index INTEGER,
            FOREIGN KEY (playlist_id) REFERENCES playlists (id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS user_achievements (
            user_id INTEGER,
            achievement_id TEXT,
            unlocked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, achievement_id),
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS flashcards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            course_id INTEGER,
            video_path TEXT,
            front TEXT NOT NULL,
            back TEXT NOT NULL,
            next_review_date TEXT, -- YYYY-MM-DD
            interval INTEGER DEFAULT 1,
            ease_factor REAL DEFAULT 2.5,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS user_settings (
            user_id INTEGER,
            key TEXT NOT NULL,
            value TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, key),
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS ai_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            provider TEXT,
            model TEXT,
            action TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS quiz_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            course_id INTEGER,
            correct_answers INTEGER,
            total_questions INTEGER,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS video_mastery (
            user_id INTEGER,
            video_path TEXT NOT NULL,
            score INTEGER DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, video_path),
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS video_chapters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_path TEXT NOT NULL,
            timestamp REAL NOT NULL,
            title TEXT NOT NULL,
            UNIQUE(video_path, timestamp)
        );

        CREATE TABLE IF NOT EXISTS video_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            video_path TEXT NOT NULL,
            image_path TEXT NOT NULL,
            timestamp REAL NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS user_xp (
            user_id INTEGER PRIMARY KEY,
            total_xp INTEGER DEFAULT 0,
            level INTEGER DEFAULT 1,
            daily_goal_mins INTEGER DEFAULT 30,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        );
    ''')

    # Migrations
    try:
        conn.execute('SELECT item_type FROM videos LIMIT 1')
    except sqlite3.OperationalError:
        print("Migrating: Adding item_type to videos...")
        conn.execute("ALTER TABLE videos ADD COLUMN item_type TEXT DEFAULT 'video'")

    try:
        conn.execute('SELECT profile_pic FROM users LIMIT 1')
    except sqlite3.OperationalError:
        print("Migrating: Adding profile_pic to users...")
        conn.execute("ALTER TABLE users ADD COLUMN profile_pic TEXT")

    conn.commit()
    conn.close()

def natural_sort_key(s):
    return [int(text) if text.isdigit() else text.lower()
            for text in re.split('([0-9]+)', s)]

def scan_courses():
    if not os.path.exists(COURSES_DIR):
        os.makedirs(COURSES_DIR)
    conn = get_db_connection()
    cursor = conn.cursor()
    for folder_name in os.listdir(COURSES_DIR):
        course_path = os.path.join(COURSES_DIR, folder_name)
        if os.path.isdir(course_path):
            cursor.execute('SELECT id FROM courses WHERE folder_name = ?', (folder_name,))
            course = cursor.fetchone()
            if not course:
                cursor.execute('INSERT INTO courses (title, folder_name) VALUES (?, ?)', (folder_name, folder_name))
                course_id = cursor.lastrowid
                scan_course_content(cursor, course_id, course_path)
    conn.commit()
    conn.close()

def scan_course_content(cursor, course_id, course_path):
    cursor.execute('SELECT path, duration FROM videos JOIN modules ON videos.module_id = modules.id WHERE modules.course_id = ?', (course_id,))
    existing_durations = {row['path']: row['duration'] for row in cursor.fetchall()}

    cursor.execute('DELETE FROM modules WHERE course_id = ?', (course_id,))
    for root, dirs, files in os.walk(course_path):
        if root == course_path:
            module_name = "General"
        else:
            module_name = os.path.basename(root)
            
        video_files = [f for f in files if f.lower().endswith(('.mp4', '.mkv', '.webm', '.mov'))]
        video_files.sort(key=natural_sort_key)
        
        # Check for Quiz
        quiz_file = None
        if 'quiz.json' in files:
            quiz_file = 'quiz.json'
            
        if video_files or quiz_file:
            cursor.execute('INSERT INTO modules (course_id, title, order_index) VALUES (?, ?, ?)', (course_id, module_name, 0))
            module_id = cursor.lastrowid
            
            # Insert Videos
            for idx, vf in enumerate(video_files):
                full_path = os.path.join(root, vf)
                rel_file_path = os.path.relpath(full_path, COURSES_DIR)
                
                duration = existing_durations.get(rel_file_path, 0)
                if duration == 0:
                    try:
                        tag = TinyTag.get(full_path)
                        duration = tag.duration or 0
                    except Exception as e:
                        print(f"Error reading duration for {full_path}: {e}")
                        duration = 0

                cursor.execute('INSERT INTO videos (module_id, title, filename, path, order_index, duration, item_type) VALUES (?, ?, ?, ?, ?, ?, ?)', 
                               (module_id, vf, vf, rel_file_path, idx, duration, 'video'))
            
            # Insert Quiz (at end of module)
            if quiz_file:
                full_path = os.path.join(root, quiz_file)
                rel_file_path = os.path.relpath(full_path, COURSES_DIR)
                # Order index = len(video_files)
                cursor.execute('INSERT INTO videos (module_id, title, filename, path, order_index, duration, item_type) VALUES (?, ?, ?, ?, ?, ?, ?)', 
                               (module_id, "📝 Quiz: " + module_name, quiz_file, rel_file_path, len(video_files), 0, 'quiz'))

def get_current_user_id():
    return current_user.id if current_user.is_authenticated else None

def get_course_stats(conn, course_id, user_id):
    stats = conn.execute('''
        SELECT 
            COUNT(v.id) as total_videos,
            SUM(v.duration) as total_duration
        FROM videos v 
        JOIN modules m ON v.module_id = m.id 
        WHERE m.course_id = ?
    ''', (course_id,)).fetchone()
    
    total_videos = stats['total_videos']
    total_duration = stats['total_duration'] or 0
    
    watched_count = 0
    watched_time = 0
    
    if user_id:
        w_stats = conn.execute('''
            SELECT 
                COUNT(DISTINCT vp.video_path) as count,
                SUM(CASE WHEN vp.is_completed THEN v.duration ELSE vp.watched_time END) as time
            FROM video_progress vp
            JOIN videos v ON vp.video_path = v.path
            JOIN modules m ON v.module_id = m.id
            WHERE m.course_id = ? AND vp.user_id = ?
        ''', (course_id, user_id)).fetchone()
        
        if w_stats and w_stats['count']:
            watched_count = w_stats['count']
            watched_time = w_stats['time'] or 0
        
        if watched_count == 0:
             legacy = conn.execute('SELECT COUNT(*) as count FROM watched_videos WHERE course_id = ? AND user_id = ?', (course_id, user_id)).fetchone()
             if legacy and legacy['count'] > 0:
                 watched_count = legacy['count']
    else:
        legacy = conn.execute('SELECT COUNT(*) as count FROM watched_videos WHERE course_id = ? AND user_id IS NULL', (course_id,)).fetchone()
        watched_count = legacy['count']
        
    if total_duration > 0:
        percentage = int((watched_time / total_duration) * 100)
        if percentage == 0 and watched_count > 0:
             percentage = int((watched_count / total_videos) * 100)
    elif total_videos > 0:
        percentage = int((watched_count / total_videos) * 100)
    else:
        percentage = 0
        
    return {
        'total_videos': total_videos,
        'watched_count': watched_count,
        'percentage': min(percentage, 100),
        'total_duration': total_duration,
        'watched_time': watched_time
    }

# --- Routes ---

@app.route('/')
def index():
    scan_courses()
    conn = get_db_connection()
    user_id = get_current_user_id()
    
    continue_watching = []
    if user_id:
        cw_rows = conn.execute('''
            SELECT v.title as video_title, v.path as video_path, v.duration, v.order_index,
                   c.title as course_title, c.id as course_id,
                   vp.watched_time, vp.updated_at
            FROM video_progress vp
            JOIN videos v ON vp.video_path = v.path
            JOIN modules m ON v.module_id = m.id
            JOIN courses c ON m.course_id = c.id
            WHERE vp.user_id = ? AND vp.is_completed = 0 AND vp.watched_time > 0
            ORDER BY vp.updated_at DESC
            LIMIT 4
        ''', (user_id,)).fetchall()
        
        for row in cw_rows:
            item = dict(row)
            pct = int((item['watched_time'] / item['duration'] * 100)) if item['duration'] > 0 else 0
            item['percentage'] = pct
            
            course_folder_row = conn.execute('SELECT folder_name FROM courses WHERE id=?', (item['course_id'],)).fetchone()
            if course_folder_row:
                course_folder = os.path.join(COURSES_DIR, course_folder_row['folder_name'])
                thumb_url = None
                for ext in ['jpg', 'png', 'jpeg', 'webp']:
                    if os.path.exists(os.path.join(course_folder, f"cover.{ext}")):
                         thumb_url = f"/course_file/{item['course_id']}/cover.{ext}"
                         break
                    if os.path.exists(os.path.join(course_folder, f"banner.{ext}")):
                         thumb_url = f"/course_file/{item['course_id']}/banner.{ext}"
                         break
                item['thumbnail'] = thumb_url
            continue_watching.append(item)

    if user_id:
        query = '''
            SELECT c.*, p.last_video_title, p.last_video_path 
            FROM courses c 
            LEFT JOIN course_progress p ON c.id = p.course_id AND p.user_id = ?
            ORDER BY c.is_favorite DESC, c.title ASC
        '''
        params = (user_id,)
    else:
        query = '''
            SELECT c.*, p.last_video_title, p.last_video_path 
            FROM courses c 
            LEFT JOIN course_progress p ON c.id = p.course_id AND p.user_id IS NULL
            ORDER BY c.is_favorite DESC, c.title ASC
        '''
        params = ()

    # Daily Review (Spaced Repetition) logic
    daily_review = []
    if user_id:
        review_rows = conn.execute('''
            SELECT v.title as video_title, v.path as video_path, 
                   c.title as course_title, c.id as course_id,
                   m.score as mastery_score, m.updated_at
            FROM video_mastery m
            JOIN videos v ON m.video_path = v.path
            JOIN modules mod ON v.module_id = mod.id
            JOIN courses c ON mod.course_id = c.id
            WHERE m.user_id = ? AND m.score > 0 AND m.score < 4
            ORDER BY m.updated_at ASC
            LIMIT 3
        ''', (user_id,)).fetchall()
        daily_review = [dict(r) for r in review_rows]

    courses_rows = conn.execute(query, params).fetchall()
    
    courses_data = []
    for row in courses_rows:
        course = dict(row)
        stats = get_course_stats(conn, course['id'], user_id)
        course.update(stats)
        
        course_folder = os.path.join(COURSES_DIR, course['folder_name'])
        thumb_url = None
        for ext in ['jpg', 'png', 'jpeg', 'webp']:
            if os.path.exists(os.path.join(course_folder, f"cover.{ext}")):
                 thumb_url = f"/course_file/{course['id']}/cover.{ext}"
                 break
            if os.path.exists(os.path.join(course_folder, f"banner.{ext}")):
                 thumb_url = f"/course_file/{course['id']}/banner.{ext}"
                 break
        
        course['thumbnail'] = thumb_url
        courses_data.append(course)
        
    conn.close()
    return render_template('index.html', courses=courses_data, continue_watching=continue_watching, daily_review=daily_review)

@app.route('/search')
def search_page():
    q = request.args.get('q', '').strip()
    results = {'videos': [], 'notes': [], 'transcripts': []}
    
    if q:
        conn = get_db_connection()
        user_id = get_current_user_id()
        
        v_rows = conn.execute('''
            SELECT v.title, v.path, c.title as course_title, c.id as course_id 
            FROM videos v
            JOIN modules m ON v.module_id = m.id
            JOIN courses c ON m.course_id = c.id
            WHERE v.title LIKE ?
            LIMIT 20
        ''', (f'%{q}%',)).fetchall()
        results['videos'] = [dict(r) for r in v_rows]
        
        if user_id:
            n_rows = conn.execute('''
                SELECT vn.content, vn.video_path, v.title as video_title, c.title as course_title, c.id as course_id
                FROM video_notes vn
                JOIN videos v ON vn.video_path = v.path
                JOIN modules m ON v.module_id = m.id
                JOIN courses c ON m.course_id = c.id
                WHERE vn.user_id = ? AND vn.content LIKE ?
                LIMIT 20
            ''', (user_id, f'%{q}%')).fetchall()
            results['notes'] = [dict(r) for r in n_rows]
            
        # Global Transcript Search
        results['transcripts'] = search_all_transcripts(q, conn)
        conn.close()
        
    return render_template('search.html', query=q, results=results)

def search_all_transcripts(query, conn):
    matches = []
    query_lower = query.lower()
    
    # We'll use the DB to find all videos, then check their subtitle files
    videos = conn.execute('''
        SELECT v.path, v.title as video_title, c.title as course_title, c.id as course_id 
        FROM videos v
        JOIN modules m ON v.module_id = m.id
        JOIN courses c ON m.course_id = c.id
    ''').fetchall()
    
    for v in videos:
        full_path = os.path.join(COURSES_DIR, v['path'])
        base_path = os.path.splitext(full_path)[0]
        
        sub_file = None
        for ext in ['.vtt', '.srt']:
            if os.path.exists(base_path + ext):
                sub_file = base_path + ext
                break
        
        if sub_file:
            try:
                with open(sub_file, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                    if query_lower in content.lower():
                        # Find specific timestamps
                        # This is a bit heavy, but works for personal collections
                        blocks = content.strip().split('\n\n')
                        for block in blocks:
                            if query_lower in block.lower():
                                lines = block.split('\n')
                                if len(lines) >= 2:
                                    ts_line = lines[1] if lines[0].isdigit() else lines[0]
                                    if '-->' in ts_line:
                                        time_str = ts_line.split('-->')[0].strip().replace(',', '.')
                                        # Convert to seconds for jumping
                                        parts = time_str.split(':')
                                        seconds = 0
                                        if len(parts) == 3: # HH:MM:SS.mmm
                                            seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
                                        elif len(parts) == 2: # MM:SS.mmm
                                            seconds = int(parts[0]) * 60 + float(parts[1])
                                        
                                        text = " ".join(lines[lines.index(ts_line)+1:])
                                        matches.append({
                                            'course_id': v['course_id'],
                                            'course_title': v['course_title'],
                                            'video_title': v['video_title'],
                                            'video_path': v['path'],
                                            'timestamp': seconds,
                                            'timestamp_str': time_str.split('.')[0],
                                            'snippet': text
                                        })
                                        if len(matches) >= 30: return matches # Cap results
            except:
                pass
    return matches


@app.route('/subtitle/<path:video_path>')
def serve_subtitle(video_path):
    full_path = os.path.join(COURSES_DIR, video_path)
    base_path = os.path.splitext(full_path)[0]
    
    vtt_path = base_path + ".vtt"
    srt_path = base_path + ".srt"
    
    if os.path.exists(vtt_path):
        return send_file(vtt_path, mimetype='text/vtt')
    elif os.path.exists(srt_path):
        with open(srt_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
            vtt_content = srt_to_vtt(content)
            return vtt_content, 200, {'Content-Type': 'text/vtt'}
            
    return "", 404

@app.route('/api/transcript/<path:video_path>')
def get_transcript(video_path):
    full_path = os.path.join(COURSES_DIR, video_path)
    base_path = os.path.splitext(full_path)[0]
    
    vtt_path = base_path + ".vtt"
    srt_path = base_path + ".srt"
    
    content = ""
    if os.path.exists(vtt_path):
        with open(vtt_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
    elif os.path.exists(srt_path):
        with open(srt_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
    
    if content:
        return jsonify(parse_subtitle_to_json(content))
    return jsonify([])

@app.route('/settings')
@login_required
def settings():
    conn = get_db_connection()
    user_id = get_current_user_id()
    
    # Get API Key & Model & AI Enabled Status
    settings_rows = conn.execute("SELECT key, value FROM user_settings WHERE user_id=? AND key IN ('gemini_api_key', 'gemini_model', 'local_model', 'ai_features_enabled', 'ai_provider', 'local_ai_url', 'local_whisper_url')", (user_id,)).fetchall()
    settings_map = {row['key']: row['value'] for row in settings_rows}
    
    api_key = settings_map.get('gemini_api_key', '')
    gemini_model = settings_map.get('gemini_model', 'gemini-2.0-flash')
    local_model = settings_map.get('local_model', '')
    
    # Sanitize Gemini Model (Only remove local models with slashes like 'TheBloke/...')
    if '/' in gemini_model:
        gemini_model = 'gemini-2.0-flash'
    
    ai_enabled = settings_map.get('ai_features_enabled', 'true') == 'true'
    ai_provider = settings_map.get('ai_provider', 'gemini')
    local_ai_url = settings_map.get('local_ai_url', 'http://localhost:1234/v1/chat/completions')
    local_whisper_url = settings_map.get('local_whisper_url', 'http://localhost:9000/v1/audio/transcriptions')
    
    # Quiz Stats for Settings
    quiz_stats = conn.execute('SELECT SUM(correct_answers) as c, SUM(total_questions) as t FROM quiz_stats WHERE user_id=?', (user_id,)).fetchone()
    quiz_correct = quiz_stats['c'] or 0
    quiz_total = quiz_stats['t'] or 0

    # XP/Goal Settings
    xp_row = conn.execute('SELECT daily_goal_mins FROM user_xp WHERE user_id=?', (user_id,)).fetchone()
    daily_goal = xp_row['daily_goal_mins'] if xp_row else 30

    courses_query = conn.execute('SELECT id, title, description, alternate_title FROM courses ORDER BY title').fetchall()
    
    courses_data = []
    for c in courses_query:
        stats = get_course_stats(conn, c['id'], user_id)
        courses_data.append({
            'id': c['id'],
            'title': c['title'],
            'description': c['description'],
            'alternate_title': c['alternate_title'],
            'total_videos': stats['total_videos'],
            'watched_count': stats['watched_count'],
            'percentage': stats['percentage']
        })
    conn.close()
    return render_template('settings.html', courses=courses_data, api_key=api_key, gemini_model=gemini_model, local_model=local_model, ai_enabled=ai_enabled, ai_provider=ai_provider, local_ai_url=local_ai_url, local_whisper_url=local_whisper_url, quiz_correct=quiz_correct, quiz_total=quiz_total, daily_goal=daily_goal)

@app.route('/course/<int:course_id>')
def player(course_id):
    conn = get_db_connection()
    user_id = get_current_user_id()
    
    course = conn.execute('SELECT * FROM courses WHERE id = ?', (course_id,)).fetchone()
    if not course:
        abort(404)
    modules = conn.execute('SELECT * FROM modules WHERE course_id = ? ORDER BY title', (course_id,)).fetchall()
    structure = []
    
    course_root = os.path.join(COURSES_DIR, course['folder_name'])
    
    vp_map = {}
    mastery_map = {}
    if user_id:
        vp_rows = conn.execute('''
            SELECT vp.video_path, vp.watched_time, vp.is_completed 
            FROM video_progress vp 
            JOIN videos v ON vp.video_path = v.path 
            JOIN modules m ON v.module_id = m.id 
            WHERE m.course_id = ? AND vp.user_id = ?
        ''', (course_id, user_id)).fetchall()
        for r in vp_rows:
            vp_map[r['video_path']] = {'watched_time': r['watched_time'], 'is_completed': r['is_completed']}

        # Fetch mastery scores
        m_rows = conn.execute('SELECT video_path, score FROM video_mastery WHERE user_id = ?', (user_id,)).fetchall()
        for r in m_rows:
            mastery_map[r['video_path']] = r['score']

    for module in modules:
        videos = conn.execute('SELECT * FROM videos WHERE module_id = ? ORDER BY order_index', (module['id'],)).fetchall()
        mod_dict = dict(module)
        mod_dict['videos'] = []
        for v in videos:
            v_dict = dict(v)
            prog = vp_map.get(v['path'], {'watched_time': 0, 'is_completed': False})
            v_dict['watched_time'] = prog['watched_time']
            v_dict['is_completed'] = prog['is_completed']
            v_dict['mastery_score'] = mastery_map.get(v['path'], 0)
            mod_dict['videos'].append(v_dict)
        
        res_files = []
        if module['title'] == "General":
             mod_path = course_root
        else:
             mod_path = os.path.join(course_root, module['title'])
             
        if os.path.exists(mod_path):
             for f in os.listdir(mod_path):
                 if os.path.isfile(os.path.join(mod_path, f)):
                     if not f.lower().endswith(('.mp4', '.mkv', '.webm', '.mov', '.ds_store', '.vtt', '.srt')):
                         rel = os.path.relpath(os.path.join(mod_path, f), course_root)
                         res_files.append({'name': f, 'path': rel})
        
        mod_dict['resources'] = res_files
        structure.append(mod_dict)
        
    structure.sort(key=lambda x: natural_sort_key(x['title']))
    
    if user_id:
        progress = conn.execute('SELECT * FROM course_progress WHERE course_id = ? AND user_id = ?', (course_id, user_id)).fetchone()
        watched_rows = conn.execute('SELECT video_path FROM watched_videos WHERE course_id = ? AND user_id = ?', (course_id, user_id)).fetchall()
    else:
        progress = conn.execute('SELECT * FROM course_progress WHERE course_id = ? AND user_id IS NULL', (course_id,)).fetchone()
        watched_rows = conn.execute('SELECT video_path FROM watched_videos WHERE course_id = ? AND user_id IS NULL', (course_id,)).fetchall()

    last_played_path = progress['last_video_path'] if progress else None
    last_timestamp = progress['last_video_timestamp'] if progress and progress['last_video_timestamp'] else 0
    watched_paths = [row['video_path'] for row in watched_rows]
    
    total_videos = conn.execute('SELECT COUNT(*) as count FROM videos v JOIN modules m ON v.module_id = m.id WHERE m.course_id = ?', (course_id,)).fetchone()['count']
    is_completed = (len(watched_paths) >= total_videos and total_videos > 0)
    
    # Check if AI is enabled
    ai_setting = conn.execute("SELECT value FROM user_settings WHERE user_id=? AND key='ai_features_enabled'", (user_id,)).fetchone()
    ai_enabled = ai_setting['value'] == 'true' if ai_setting else True

    conn.close()
    return render_template('player.html', course=course, structure=structure, 
                           last_played_path=last_played_path, 
                           last_timestamp=last_timestamp,
                           watched_paths=watched_paths, 
                           is_completed=is_completed,
                           ai_enabled=ai_enabled)

@app.route('/media/<path:filename>')
def serve_media(filename):
    return send_from_directory(COURSES_DIR, filename)

@app.route('/course_file/<int:course_id>/<path:filename>')
def serve_course_file(course_id, filename):
    conn = get_db_connection()
    course = conn.execute('SELECT folder_name FROM courses WHERE id = ?', (course_id,)).fetchone()
    conn.close()
    if not course:
        abort(404)
    return send_from_directory(os.path.join(COURSES_DIR, course['folder_name']), filename)

# --- Auth Routes ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        conn = get_db_connection()
        user = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        conn.close()
        
        if user and check_password_hash(user['password_hash'], password):
            user_obj = User(user['id'], user['username'], user['name'], user['address'])
            login_user(user_obj)
            return redirect(url_for('index'))
        else:
            flash('Invalid username or password')
            
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        name = request.form['name']
        address = request.form['address']
        
        conn = get_db_connection()
        try:
            hashed_pw = generate_password_hash(password)
            conn.execute('INSERT INTO users (username, password_hash, name, address) VALUES (?, ?, ?, ?)',
                         (username, hashed_pw, name, address))
            conn.commit()
            conn.close()
            flash('Registration successful! Please login.')
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            conn.close()
            flash('Username already exists.')
            
    return render_template('register.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

# --- API Routes ---

def award_xp(user_id, amount):
    if not user_id: return
    conn = get_db_connection()
    # Ensure user has record
    conn.execute('INSERT OR IGNORE INTO user_xp (user_id, total_xp, level) VALUES (?, 0, 1)', (user_id,))
    conn.execute('UPDATE user_xp SET total_xp = total_xp + ? WHERE user_id = ?', (amount, user_id))
    
    # Simple Level Up logic: 1000 XP per level
    row = conn.execute('SELECT total_xp FROM user_xp WHERE user_id = ?', (user_id,)).fetchone()
    new_level = (row['total_xp'] // 1000) + 1
    conn.execute('UPDATE user_xp SET level = ? WHERE user_id = ?', (new_level, user_id))
    
    conn.commit()
    conn.close()

@app.route('/api/save_progress', methods=['POST'])
def save_progress():
    data = request.json
    user_id = get_current_user_id()
    
    timestamp = data.get('timestamp', 0)
    video_path = data['video_path']
    course_id = data['course_id']
    
    conn = get_db_connection()
    
    conn.execute('''
        INSERT INTO course_progress (user_id, course_id, last_video_path, last_video_title, last_video_timestamp, updated_at)
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id, course_id) DO UPDATE SET
            last_video_path = excluded.last_video_path,
            last_video_title = excluded.last_video_title,
            last_video_timestamp = excluded.last_video_timestamp,
            updated_at = CURRENT_TIMESTAMP
        WHERE (user_id IS ? OR (user_id IS NULL AND ? IS NULL))
    ''', (user_id, course_id, video_path, data['video_title'], timestamp, user_id, user_id))
    
    video_row = conn.execute('SELECT duration FROM videos WHERE path = ? LIMIT 1', (video_path,)).fetchone()
    duration = video_row['duration'] if video_row else 0
    
    is_completed = False
    if duration > 0 and (timestamp / duration) >= 0.90:
        is_completed = True
        
    was_completed = False
    prev_prog = conn.execute('SELECT is_completed FROM video_progress WHERE user_id=? AND video_path=?', (user_id, video_path)).fetchone()
    if prev_prog and prev_prog['is_completed']:
        was_completed = True

    conn.execute('''
        INSERT INTO video_progress (user_id, video_path, watched_time, is_completed, updated_at)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id, video_path) DO UPDATE SET
            watched_time = MAX(video_progress.watched_time, excluded.watched_time),
            is_completed = (excluded.is_completed OR video_progress.is_completed),
            updated_at = CURRENT_TIMESTAMP
    ''', (user_id, video_path, timestamp, is_completed))
    
    today = datetime.date.today().strftime("%Y-%m-%d")
    completed_inc = 1 if (is_completed and not was_completed) else 0
    seconds_inc = 5 
    
    conn.execute('''
        INSERT INTO daily_activity (user_id, date, seconds_watched, videos_completed)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id, date) DO UPDATE SET
            seconds_watched = seconds_watched + excluded.seconds_watched,
            videos_completed = videos_completed + excluded.videos_completed
    ''', (user_id, today, seconds_inc, completed_inc))
    
    if is_completed:
        conn.execute('''
            INSERT OR IGNORE INTO watched_videos (user_id, course_id, video_path) 
            VALUES (?, ?, ?)
        ''', (user_id, course_id, video_path))
    
    # Check Achievements
    new_badges = check_new_achievements(conn, user_id)

    conn.commit()
    conn.close()

    # Award XP
    if user_id:
        # 1 XP per second watched (approx) + 100 for completion
        xp_gain = seconds_inc * 2
        if is_completed and not was_completed:
            xp_gain += 100
        award_xp(user_id, xp_gain)

    return jsonify({"status": "success", "is_completed": is_completed, "new_achievements": new_badges})


@app.route('/api/get_note', methods=['GET'])
def get_note():
    user_id = get_current_user_id()
    video_path = request.args.get('video_path')
    
    if not user_id:
         return jsonify({"content": ""})

    conn = get_db_connection()
    row = conn.execute('SELECT content FROM video_notes WHERE user_id = ? AND video_path = ?', (user_id, video_path)).fetchone()
    conn.close()
    
    return jsonify({"content": row['content'] if row else ""})

@app.route('/api/save_note', methods=['POST'])
def save_note():
    data = request.json
    user_id = get_current_user_id()
    if not user_id:
        return jsonify({"status": "error", "message": "Login required"}), 401
        
    conn = get_db_connection()
    conn.execute('''
        INSERT INTO video_notes (user_id, video_path, content, updated_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id, video_path) DO UPDATE SET
            content = excluded.content,
            updated_at = CURRENT_TIMESTAMP
    ''', (user_id, data['video_path'], data['content']))
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})

@app.route('/api/save_bookmark', methods=['POST'])
@login_required
def save_bookmark():
    data = request.json
    user_id = current_user.id
    conn = get_db_connection()
    conn.execute('''
        INSERT INTO bookmarks (user_id, course_id, video_path, video_title, timestamp, note)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (user_id, data['course_id'], data['video_path'], data['video_title'], data['timestamp'], data.get('note', '')))
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})

@app.route('/api/get_bookmarks', methods=['GET'])
@login_required
def get_bookmarks():
    course_id = request.args.get('course_id')
    user_id = current_user.id
    conn = get_db_connection()
    
    if course_id:
        rows = conn.execute('SELECT * FROM bookmarks WHERE user_id = ? AND course_id = ? ORDER BY timestamp', (user_id, course_id)).fetchall()
    else:
        rows = conn.execute('SELECT * FROM bookmarks WHERE user_id = ? ORDER BY created_at DESC', (user_id,)).fetchall()
    
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/delete_bookmark', methods=['POST'])
@login_required
def delete_bookmark():
    bookmark_id = request.json['id']
    user_id = current_user.id
    conn = get_db_connection()
    conn.execute('DELETE FROM bookmarks WHERE id = ? AND user_id = ?', (bookmark_id, user_id))
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})

@app.route('/resources')
@login_required
def resources_page():
    conn = get_db_connection()
    courses_rows = conn.execute('SELECT id, folder_name FROM courses').fetchall()
    conn.close()
    
    # Map folder_name -> {id, folder_name}
    course_map = {row['folder_name']: {'id': row['id'], 'folder_name': row['folder_name']} for row in courses_rows}
    
    resources = []
    for root, dirs, files in os.walk(COURSES_DIR):
        for f in files:
            if not f.lower().endswith(('.mp4', '.mkv', '.webm', '.mov', '.ds_store', '.db', '.py', '.sh', '.vtt', '.srt')):
                full_path = os.path.join(root, f)
                rel_path = os.path.relpath(full_path, COURSES_DIR)
                parts = rel_path.split(os.sep)
                course_folder_name = parts[0] if len(parts) > 0 else "Unknown"
                
                course = course_map.get(course_folder_name)
                
                if course:
                    course_folder_path = os.path.join(COURSES_DIR, course['folder_name'])
                    rel_path_in_course = os.path.relpath(full_path, course_folder_path)
                    resources.append({
                        'name': f,
                        'path': rel_path_in_course,
                        'course_name': course_folder_name,
                        'course_id': course['id'],
                        'size': os.path.getsize(full_path)
                    })
    return render_template('resources.html', resources=resources)

@app.route('/api/save_quiz_result', methods=['POST'])
@login_required
def save_quiz_result():
    data = request.json
    user_id = current_user.id
    correct = data.get('correct')
    total = data.get('total')
    course_id = data.get('course_id')
    
    if correct is None or total is None:
        return jsonify({"status": "error", "message": "Missing data"}), 400
        
    conn = get_db_connection()
    conn.execute('INSERT INTO quiz_stats (user_id, course_id, correct_answers, total_questions) VALUES (?, ?, ?, ?)',
                 (user_id, course_id, correct, total))
    conn.commit()
    conn.close()
    
    # Award XP: 50 per correct answer
    award_xp(user_id, correct * 50)
    
    return jsonify({"status": "success"})

@app.route('/api/save_mastery', methods=['POST'])
@login_required
def save_mastery():
    data = request.json
    user_id = current_user.id
    video_path = data.get('video_path')
    score = data.get('score')
    
    if not video_path or score is None:
        return jsonify({"status": "error", "message": "Missing data"}), 400
        
    conn = get_db_connection()
    conn.execute('''
        INSERT INTO video_mastery (user_id, video_path, score, updated_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id, video_path) DO UPDATE SET
            score = excluded.score,
            updated_at = CURRENT_TIMESTAMP
    ''', (user_id, video_path, score))
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})

@app.route('/api/save_goal', methods=['POST'])
@login_required
def save_goal():
    data = request.json
    user_id = current_user.id
    goal = data.get('daily_goal', 30)
    
    conn = get_db_connection()
    conn.execute('INSERT OR IGNORE INTO user_xp (user_id, daily_goal_mins) VALUES (?, ?)', (user_id, goal))
    conn.execute('UPDATE user_xp SET daily_goal_mins = ? WHERE user_id = ?', (goal, user_id))
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})

@app.route('/api/update_profile_pic', methods=['POST'])
@login_required
def update_profile_pic():
    if 'file' not in request.files:
        return jsonify({"status": "error", "message": "No file"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"status": "error", "message": "No selected file"}), 400
    
    try:
        # Save file
        filename = f"profile_{current_user.id}_{uuid.uuid4().hex[:8]}.jpg"
        filepath = os.path.join("static", "snapshots", filename)
        file.save(filepath)
        
        img_url = "/" + filepath.replace("\\", "/")
        
        conn = get_db_connection()
        conn.execute('UPDATE users SET profile_pic = ? WHERE id = ?', (img_url, current_user.id))
        conn.commit()
        conn.close()
        
        return jsonify({"status": "success", "image_url": img_url})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500



@app.route('/api/save_snapshot', methods=['POST'])
@login_required
def save_snapshot():
    data = request.json
    user_id = current_user.id
    video_path = data.get('video_path')
    img_data = data.get('image') # Base64
    timestamp = data.get('timestamp', 0)
    
    if not img_data:
        return jsonify({"status": "error", "message": "No image data"}), 400
        
    # Decode base64
    try:
        header, encoded = img_data.split(",", 1)
        data_bytes = base64.b64decode(encoded)
        
        filename = f"snap_{user_id}_{uuid.uuid4().hex[:8]}.jpg"
        filepath = os.path.join("static", "snapshots", filename)
        
        with open(filepath, "wb") as f:
            f.write(data_bytes)
            
        img_url = "/" + filepath.replace("\\", "/")
        
        conn = get_db_connection()
        conn.execute('INSERT INTO video_snapshots (user_id, video_path, image_path, timestamp) VALUES (?, ?, ?, ?)',
                     (user_id, video_path, img_url, timestamp))
        conn.commit()
        conn.close()
        
        return jsonify({"status": "success", "image_url": img_url})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500



@app.route('/analytics')
@login_required
def analytics_page():
    user_id = current_user.id
    conn = get_db_connection()
    
    total_time = conn.execute('SELECT SUM(watched_time) FROM video_progress WHERE user_id=?', (user_id,)).fetchone()[0] or 0
    total_completed = conn.execute('SELECT COUNT(*) FROM video_progress WHERE user_id=? AND is_completed=1', (user_id,)).fetchone()[0] or 0
    
    activity_rows = conn.execute('SELECT date, seconds_watched, videos_completed FROM daily_activity WHERE user_id=?', (user_id,)).fetchall()
    activity_data = {row['date']: {'seconds': row['seconds_watched'], 'count': row['videos_completed'], 'ai_count': 0} for row in activity_rows}
    
    # Add AI activity to heatmap data
    try:
        ai_daily_rows = conn.execute("SELECT date(timestamp) as d, COUNT(*) as c FROM ai_logs WHERE user_id=? GROUP BY date(timestamp)", (user_id,)).fetchall()
        for row in ai_daily_rows:
            d_str = row['d']
            if d_str in activity_data:
                activity_data[d_str]['ai_count'] = row['c']
            else:
                activity_data[d_str] = {'seconds': 0, 'count': 0, 'ai_count': row['c']}
    except sqlite3.OperationalError:
        pass

    streak = 0
    check_date = datetime.date.today()
    while True:
        date_str = check_date.strftime("%Y-%m-%d")
        if date_str in activity_data and activity_data[date_str]['seconds'] > 0:
            streak += 1
            check_date -= datetime.timedelta(days=1)
        else:
            if date_str == datetime.date.today().strftime("%Y-%m-%d") and streak == 0:
                 check_date -= datetime.timedelta(days=1)
                 continue
            break
            
    # Achievements Data
    user_achievements = {row['achievement_id']: row['unlocked_at'] for row in conn.execute('SELECT * FROM user_achievements WHERE user_id=?', (user_id,)).fetchall()}
    
    achievements_list = []
    for aid, data in ACHIEVEMENTS.items():
        item = data.copy()
        item['id'] = aid
        item['unlocked'] = aid in user_achievements
        item['unlocked_at'] = user_achievements.get(aid)
        achievements_list.append(item)
        
    # AI Analytics
    try:
        ai_total = conn.execute('SELECT COUNT(*) as c FROM ai_logs WHERE user_id=?', (user_id,)).fetchone()['c']
        ai_by_provider = dict(conn.execute('SELECT provider, COUNT(*) as c FROM ai_logs WHERE user_id=? GROUP BY provider', (user_id,)).fetchall())
        ai_by_action = dict(conn.execute('SELECT action, COUNT(*) as c FROM ai_logs WHERE user_id=? GROUP BY action', (user_id,)).fetchall())
    except sqlite3.OperationalError:
        ai_total = 0
        ai_by_provider = {}
        ai_by_action = {}

    # Quiz Analytics
    quiz_stats = conn.execute('SELECT SUM(correct_answers) as c, SUM(total_questions) as t FROM quiz_stats WHERE user_id=?', (user_id,)).fetchone()
    quiz_correct = quiz_stats['c'] or 0
    quiz_total = quiz_stats['t'] or 0

    # Mastery Analytics
    mastery_stats = conn.execute('SELECT AVG(score) as avg_score, COUNT(*) as total_rated FROM video_mastery WHERE user_id=?', (user_id,)).fetchone()
    avg_mastery = round(mastery_stats['avg_score'] or 0, 1)
    total_rated = mastery_stats['total_rated'] or 0

    # XP & Level Analytics
    xp_row = conn.execute('SELECT * FROM user_xp WHERE user_id=?', (user_id,)).fetchone()
    if not xp_row:
        user_xp_data = {'total_xp': 0, 'level': 1, 'daily_goal_mins': 30, 'next_level_xp': 1000, 'current_level_base': 0, 'pct': 0}
    else:
        lvl = xp_row['level']
        total = xp_row['total_xp']
        base = (lvl - 1) * 1000
        target = lvl * 1000
        progress = total - base
        pct = int((progress / 1000) * 100)
        user_xp_data = {
            'total_xp': total,
            'level': lvl,
            'daily_goal_mins': xp_row['daily_goal_mins'],
            'next_level_xp': target,
            'current_level_base': base,
            'pct': pct
        }
        
    conn.close()
    return render_template('analytics.html', 
                           total_time=total_time, 
                           total_completed=total_completed, 
                           activity_data=activity_data, 
                           streak=streak,
                           achievements=achievements_list,
                           ai_total=ai_total,
                           ai_by_provider=ai_by_provider,
                           ai_by_action=ai_by_action,
                           quiz_correct=quiz_correct,
                           quiz_total=quiz_total,
                           avg_mastery=avg_mastery,
                           total_rated=total_rated,
                           user_xp=user_xp_data)

# --- Playlist Routes ---

@app.route('/api/create_playlist', methods=['POST'])
@login_required
def create_playlist():
    title = request.json.get('title')
    user_id = current_user.id
    if not title:
        return jsonify({"status": "error", "message": "Title required"}), 400
    
    conn = get_db_connection()
    conn.execute('INSERT INTO playlists (user_id, title) VALUES (?, ?)', (user_id, title))
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})

@app.route('/api/get_playlists', methods=['GET'])
@login_required
def get_playlists():
    user_id = current_user.id
    conn = get_db_connection()
    rows = conn.execute('SELECT * FROM playlists WHERE user_id = ? ORDER BY created_at DESC', (user_id,)).fetchall()
    playlists = [dict(r) for r in rows]
    
    # Enrich with item count
    for pl in playlists:
        count = conn.execute('SELECT COUNT(*) as c FROM playlist_items WHERE playlist_id = ?', (pl['id'],)).fetchone()['c']
        pl['item_count'] = count
        
    conn.close()
    return jsonify(playlists)

@app.route('/api/add_to_playlist', methods=['POST'])
@login_required
def add_to_playlist():
    data = request.json
    playlist_id = data.get('playlist_id')
    video_path = data.get('video_path')
    video_title = data.get('video_title')
    course_id = data.get('course_id')
    
    conn = get_db_connection()
    # Get current max order
    max_order = conn.execute('SELECT MAX(order_index) as m FROM playlist_items WHERE playlist_id = ?', (playlist_id,)).fetchone()['m']
    new_order = (max_order if max_order is not None else -1) + 1
    
    conn.execute('''
        INSERT INTO playlist_items (playlist_id, video_path, video_title, course_id, order_index)
        VALUES (?, ?, ?, ?, ?)
    ''', (playlist_id, video_path, video_title, course_id, new_order))
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})

@app.route('/api/remove_from_playlist', methods=['POST'])
@login_required
def remove_from_playlist():
    item_id = request.json.get('item_id')
    conn = get_db_connection()
    conn.execute('DELETE FROM playlist_items WHERE id = ?', (item_id,))
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})

@app.route('/playlist/<int:playlist_id>')
@login_required
def playlist_player(playlist_id):
    conn = get_db_connection()
    user_id = current_user.id
    
    playlist = conn.execute('SELECT * FROM playlists WHERE id = ? AND user_id = ?', (playlist_id, user_id)).fetchone()
    if not playlist:
        abort(404)
        
    items = conn.execute('SELECT * FROM playlist_items WHERE playlist_id = ? ORDER BY order_index', (playlist_id,)).fetchall()
    
    # Transform items to match player structure (simple flat list masquerading as module)
    video_list = []
    for item in items:
        # Get duration and progress
        v_info = conn.execute('SELECT duration, item_type FROM videos WHERE path = ?', (item['video_path'],)).fetchone()
        duration = v_info['duration'] if v_info else 0
        item_type = v_info['item_type'] if v_info and 'item_type' in v_info.keys() else 'video'
        
        prog = conn.execute('SELECT watched_time, is_completed FROM video_progress WHERE user_id = ? AND video_path = ?', (user_id, item['video_path'])).fetchone()
        
        video_list.append({
            'title': item['video_title'],
            'path': item['video_path'],
            'duration': duration,
            'item_type': item_type,
            'watched_time': prog['watched_time'] if prog else 0,
            'is_completed': prog['is_completed'] if prog else False,
            'course_id': item['course_id'] # Needed for linking back
        })
        
    structure = [{'title': playlist['title'], 'videos': video_list, 'resources': []}]
    
    conn.close()
    
    # Reuse player template with slight adjustment (we might need a flag `is_playlist`)
    return render_template('player.html', course={'title': playlist['title'], 'id': 0}, structure=structure, 
                           last_played_path=None, last_timestamp=0, watched_paths=[], is_completed=False, is_playlist=True)

# --- Backup/Restore Routes ---

@app.route('/api/backup')
@login_required
def backup_data():
    user_id = current_user.id
    conn = get_db_connection()
    
    data = {
        'version': 1,
        'user': dict(conn.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()),
        'progress': [dict(r) for r in conn.execute('SELECT * FROM video_progress WHERE user_id=?', (user_id,)).fetchall()],
        'course_progress': [dict(r) for r in conn.execute('SELECT * FROM course_progress WHERE user_id=?', (user_id,)).fetchall()],
        'notes': [dict(r) for r in conn.execute('SELECT * FROM video_notes WHERE user_id=?', (user_id,)).fetchall()],
        'bookmarks': [dict(r) for r in conn.execute('SELECT * FROM bookmarks WHERE user_id=?', (user_id,)).fetchall()],
        'playlists': [dict(r) for r in conn.execute('SELECT * FROM playlists WHERE user_id=?', (user_id,)).fetchall()],
        'playlist_items': [dict(r) for r in conn.execute('SELECT pi.* FROM playlist_items pi JOIN playlists p ON pi.playlist_id = p.id WHERE p.user_id=?', (user_id,)).fetchall()],
        'activity': [dict(r) for r in conn.execute('SELECT * FROM daily_activity WHERE user_id=?', (user_id,)).fetchall()]
    }
    
    conn.close()
    
    return jsonify(data)

@app.route('/api/restore', methods=['POST'])
@login_required
def restore_data():
    if 'file' not in request.files:
        return jsonify({"status": "error", "message": "No file part"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"status": "error", "message": "No selected file"}), 400
        
    try:
        data = json.load(file)
        user_id = current_user.id
        conn = get_db_connection()
        
        # Restore Progress
        for p in data.get('progress', []):
            conn.execute('''
                INSERT INTO video_progress (user_id, video_path, watched_time, is_completed, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, video_path) DO UPDATE SET
                    watched_time = excluded.watched_time,
                    is_completed = excluded.is_completed,
                    updated_at = excluded.updated_at
            ''', (user_id, p['video_path'], p['watched_time'], p['is_completed'], p['updated_at']))
            
        # Restore Notes
        for n in data.get('notes', []):
            conn.execute('''
                INSERT INTO video_notes (user_id, video_path, content, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, video_path) DO UPDATE SET
                    content = excluded.content,
                    updated_at = excluded.updated_at
            ''', (user_id, n['video_path'], n['content'], n['created_at'], n['updated_at']))
            
        # Restore Bookmarks
        for b in data.get('bookmarks', []):
            conn.execute('''
                INSERT INTO bookmarks (user_id, course_id, video_path, video_title, timestamp, note, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (user_id, b['course_id'], b['video_path'], b['video_title'], b['timestamp'], b['note'], b['created_at']))
            
        # Restore Activity
        for a in data.get('activity', []):
            conn.execute('''
                INSERT INTO daily_activity (user_id, date, seconds_watched, videos_completed)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, date) DO UPDATE SET
                    seconds_watched = MAX(daily_activity.seconds_watched, excluded.seconds_watched),
                    videos_completed = MAX(daily_activity.videos_completed, excluded.videos_completed)
            ''', (user_id, a['date'], a['seconds_watched'], a['videos_completed']))

        conn.commit()
        conn.close()
        return jsonify({"status": "success", "message": "Restore complete (partial/merge)"})
        
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# --- Flashcard Routes ---

@app.route('/api/add_flashcard', methods=['POST'])
@login_required
def add_flashcard():
    data = request.json
    user_id = current_user.id
    today = datetime.date.today().strftime("%Y-%m-%d")
    
    conn = get_db_connection()
    conn.execute('''
        INSERT INTO flashcards (user_id, course_id, video_path, front, back, next_review_date)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (user_id, data['course_id'], data['video_path'], data['front'], data['back'], today))
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})
        
@app.route('/api/review_flashcard', methods=['POST'])
@login_required
def review_flashcard():
    # SM-2 inspired simplified algorithm
    card_id = request.json.get('id')
    quality = request.json.get('quality') # 0=forgot, 3=hard, 4=good, 5=easy
    
    conn = get_db_connection()
    card = conn.execute('SELECT * FROM flashcards WHERE id = ?', (card_id,)).fetchone()
    
    if not card:
        conn.close()
        return jsonify({"status": "error"}), 404
        
    interval = card['interval']
    ease = card['ease_factor']
    
    if quality < 3:
        interval = 1
    else:
        if interval == 1:
            interval = 6 if quality > 3 else 3
        else:
            interval = int(interval * ease)
            
        ease = ease + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
        if ease < 1.3: ease = 1.3
        
    next_date = (datetime.date.today() + datetime.timedelta(days=interval)).strftime("%Y-%m-%d")
    
    conn.execute('''
        UPDATE flashcards 
        SET next_review_date = ?, interval = ?, ease_factor = ? 
        WHERE id = ?
    ''', (next_date, interval, ease, card_id))
    conn.commit()
    conn.close()
    
    return jsonify({"status": "success"})

@app.route('/study')
@login_required
def study_page():
    user_id = current_user.id
    today = datetime.date.today().strftime("%Y-%m-%d")
    
    conn = get_db_connection()
    # Get due cards
    cards = conn.execute('''
        SELECT * FROM flashcards 
        WHERE user_id = ? AND next_review_date <= ? 
        ORDER BY next_review_date
    ''', (user_id, today)).fetchall()
    
    due_count = len(cards)
    cards_list = [dict(c) for c in cards]
    conn.close()
    
    return render_template('study.html', cards=cards_list, due_count=due_count)

@app.route('/quiz/<path:quiz_file>')
@login_required
def serve_quiz(quiz_file):
    # Securely serve quiz.json
    full_path = os.path.join(COURSES_DIR, quiz_file)
    if os.path.exists(full_path) and quiz_file.endswith('.json'):
        return send_file(full_path)
    abort(404)

@app.route('/api/toggle_favorite', methods=['POST'])
def toggle_favorite():
    conn = get_db_connection()
    conn.execute('UPDATE courses SET is_favorite = NOT is_favorite WHERE id = ?', (request.json['course_id'],))
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})

@app.route('/api/get_course_videos/<int:course_id>')
@login_required
def get_course_videos_api(course_id):
    conn = get_db_connection()
    user_id = current_user.id
    
    modules = conn.execute('SELECT * FROM modules WHERE course_id = ? ORDER BY order_index', (course_id,)).fetchall()
    
    vp_map = {}
    vp_rows = conn.execute('SELECT video_path, watched_time, is_completed FROM video_progress WHERE user_id = ?', (user_id,)).fetchall()
    for r in vp_rows:
        vp_map[r['video_path']] = {'watched_time': r['watched_time'], 'is_completed': r['is_completed']}
        
    structure = []
    for module in modules:
        videos = conn.execute('SELECT * FROM videos WHERE module_id = ? ORDER BY order_index', (module['id'],)).fetchall()
        mod_dict = dict(module)
        mod_dict['videos'] = []
        for v in videos:
            v_dict = dict(v)
            prog = vp_map.get(v['path'], {'watched_time': 0, 'is_completed': False})
            v_dict['watched_time'] = prog['watched_time']
            v_dict['is_completed'] = prog['is_completed']
            mod_dict['videos'].append(v_dict)
        structure.append(mod_dict)
        
    conn.close()
    return jsonify({"structure": structure})

@app.route('/api/reset_progress', methods=['POST'])
def reset_progress():
    data = request.json
    course_id = data.get('course_id')
    video_path = data.get('video_path')
    user_id = get_current_user_id()
    conn = get_db_connection()
    
    if course_id == 'all':
        if user_id:
            conn.execute('DELETE FROM course_progress WHERE user_id = ?', (user_id,))
            conn.execute('DELETE FROM watched_videos WHERE user_id = ?', (user_id,))
            conn.execute('DELETE FROM video_progress WHERE user_id = ?', (user_id,))
            conn.execute('DELETE FROM daily_activity WHERE user_id = ?', (user_id,))
            conn.execute('DELETE FROM user_xp WHERE user_id = ?', (user_id,))
            conn.execute('DELETE FROM user_achievements WHERE user_id = ?', (user_id,))
            conn.execute('DELETE FROM quiz_stats WHERE user_id = ?', (user_id,))
            conn.execute('DELETE FROM video_mastery WHERE user_id = ?', (user_id,))
            conn.execute('DELETE FROM bookmarks WHERE user_id = ?', (user_id,))
            conn.execute('DELETE FROM video_notes WHERE user_id = ?', (user_id,))
            conn.execute('DELETE FROM flashcards WHERE user_id = ?', (user_id,))
        else:
            conn.execute('DELETE FROM course_progress WHERE user_id IS NULL')
            conn.execute('DELETE FROM watched_videos WHERE user_id IS NULL')
            conn.execute('DELETE FROM video_progress WHERE user_id IS NULL')
            
    elif video_path:
        if user_id:
            conn.execute('DELETE FROM video_progress WHERE user_id = ? AND video_path = ?', (user_id, video_path))
            conn.execute('DELETE FROM watched_videos WHERE user_id = ? AND video_path = ?', (user_id, video_path))
            # Also clear from course_progress if it was the last video
            conn.execute('''
                UPDATE course_progress SET last_video_path = NULL, last_video_title = NULL, last_video_timestamp = 0
                WHERE user_id = ? AND last_video_path = ?
            ''', (user_id, video_path))
        else:
            conn.execute('DELETE FROM video_progress WHERE user_id IS NULL AND video_path = ?', (video_path,))
            conn.execute('DELETE FROM watched_videos WHERE user_id IS NULL AND video_path = ?', (video_path,))
            conn.execute('''
                UPDATE course_progress SET last_video_path = NULL, last_video_title = NULL, last_video_timestamp = 0
                WHERE user_id IS NULL AND last_video_path = ?
            ''', (video_path,))
    
    elif course_id:
        if user_id:
            conn.execute('DELETE FROM course_progress WHERE course_id = ? AND user_id = ?', (course_id, user_id))
            conn.execute('''
                DELETE FROM video_progress 
                WHERE user_id = ? AND video_path IN (
                    SELECT v.path FROM videos v 
                    JOIN modules m ON v.module_id = m.id 
                    WHERE m.course_id = ?
                )
            ''', (user_id, course_id))
            conn.execute('DELETE FROM watched_videos WHERE course_id = ? AND user_id = ?', (course_id, user_id))
        else:
            conn.execute('DELETE FROM course_progress WHERE course_id = ? AND user_id IS NULL', (course_id,))
            conn.execute('''
                DELETE FROM video_progress 
                WHERE user_id IS NULL AND video_path IN (
                    SELECT v.path FROM videos v 
                    JOIN modules m ON v.module_id = m.id 
                    WHERE m.course_id = ?
                )
            ''', (course_id,))
            conn.execute('DELETE FROM watched_videos WHERE course_id = ? AND user_id IS NULL', (course_id,))

    conn.commit()
    conn.close()
    return jsonify({"status": "success"})

@app.route('/api/update_course_description', methods=['POST'])
def update_course_description():
    data = request.json
    course_id = data.get('course_id')
    description = data.get('description')
    alternate_title = data.get('alternate_title')
    
    if not course_id:
        return jsonify({"status": "error", "message": "Missing id"}), 400
    conn = get_db_connection()
    conn.execute('UPDATE courses SET description = ?, alternate_title = ? WHERE id = ?', (description, alternate_title, course_id))
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})

@app.route('/api/save_settings', methods=['POST'])
@login_required
def save_settings():
    data = request.json
    user_id = current_user.id
    key = data.get('key')
    value = data.get('value')
    
    if not key:
        return jsonify({"status": "error", "message": "Key required"}), 400
        
    conn = get_db_connection()
    conn.execute('''
        INSERT INTO user_settings (user_id, key, value, updated_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id, key) DO UPDATE SET
            value = excluded.value,
            updated_at = excluded.updated_at
    ''', (user_id, key, value))
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})

def call_ai_service(provider, api_key, model_name, local_url, system_instruction, user_prompt, user_id=None, action="unknown"):
    response_text = ""
    
    if provider == 'gemini':
        if not api_key:
            raise Exception("Gemini API Key missing.")
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model_name,
            contents=[system_instruction, user_prompt]
        )
        response_text = response.text
    else: # local
        if not local_url:
            raise Exception("Local AI URL missing.")
        
        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.7,
            "stream": False
        }
        
        response = requests.post(local_url, json=payload, timeout=60)
        response.raise_for_status()
        resp_data = response.json()
        
        if 'choices' in resp_data and len(resp_data['choices']) > 0:
            response_text = resp_data['choices'][0]['message']['content']
        else:
            response_text = str(resp_data)

    # Log Usage
    if user_id:
        try:
            conn = get_db_connection()
            conn.execute('INSERT INTO ai_logs (user_id, provider, model, action) VALUES (?, ?, ?, ?)', 
                         (user_id, provider, model_name, action))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Error logging AI usage: {e}")
            
    return response_text

@app.route('/api/ai_chat', methods=['POST'])
@login_required
def ai_chat():
    if not genai:
        return jsonify({"status": "error", "message": "Google GenAI library not installed."}), 500
        
    user_id = current_user.id
    
    conn = get_db_connection()
    settings_rows = conn.execute("SELECT key, value FROM user_settings WHERE user_id=? AND key IN ('gemini_api_key', 'ai_features_enabled', 'ai_provider', 'local_ai_url', 'gemini_model', 'local_model')", (user_id,)).fetchall()
    settings = {row['key']: row['value'] for row in settings_rows}
    conn.close()
    
    if settings.get('ai_features_enabled', 'true') != 'true':
         return jsonify({"status": "error", "message": "AI features are disabled in settings."}), 403
         
    prompt = request.json.get('prompt')
    provider = settings.get('ai_provider', 'gemini')
    model_name = request.json.get('model', settings.get('gemini_model' if provider == 'gemini' else 'local_model', 'gemini-2.0-flash')) 
    api_key = settings.get('gemini_api_key')
    local_url = settings.get('local_ai_url')
    
    try:
        response_text = call_ai_service(provider, api_key, model_name, local_url, "You are a helpful assistant.", prompt, user_id, "chat_simple")
        return jsonify({"status": "success", "response": response_text})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/get_models', methods=['GET'])
@login_required
def get_models():
    if not genai:
         return jsonify({"status": "error", "message": "Google GenAI library not installed"}), 500

    user_id = current_user.id
    conn = get_db_connection()
    row = conn.execute("SELECT value FROM user_settings WHERE user_id=? AND key='gemini_api_key'", (user_id,)).fetchone()
    conn.close()
    
    if not row or not row['value']:
        # Return a basic default list if no key is saved yet, so the UI isn't empty
        defaults = ["gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-pro"]
        return jsonify({"status": "default", "models": defaults})
        
    try:
        client = genai.Client(api_key=row['value'])
        models = []
        # list() returns an iterator of Model objects
        print("DEBUG: Fetching models...")
        for m in client.models.list():
            # Debug log to see what we are getting
            # print(f"Found model: {m.name}") 
            
            # Heuristic: Check for 'generateContent' OR just 'gemini' in name
            # The SDK might differ in attribute names
            methods = getattr(m, 'supported_generation_methods', [])
            actions = getattr(m, 'supported_actions', [])
            
            name = m.name.split('/')[-1] if '/' in m.name else m.name
            
            if (methods and 'generateContent' in methods) or (actions and 'generateContent' in actions) or 'gemini' in name.lower():
                models.append(name)
        
        # Simple sort to keep similar families together
        models.sort(reverse=True)
        print(f"DEBUG: Returning {len(models)} models.")
        return jsonify({"status": "success", "models": models})
    except Exception as e:
        print(f"DEBUG: Error fetching models: {e}")
        return jsonify({"status": "error", "message": str(e)})

def convert_to_vtt(content):
    """Clean and convert AI output (VTT or JSON) to valid WebVTT format."""
    content = content.strip()
    # Remove markdown blocks
    if content.startswith("```"):
        content = content.split('\n', 1)[1].rsplit('\n', 1)[0].replace('webvtt', '').replace('json', '').strip()
    
    # If it's JSON, convert to VTT string
    if content.startswith('[') or content.startswith('{'):
        try:
            data = json.loads(content)
            vtt = ["WEBVTT\n\n"]
            for item in data:
                start_sec = float(item.get('start', item.get('start_time', 0)))
                end_sec = float(item.get('end', item.get('end_time', start_sec + 2)))
                
                def format_vtt_ts(s):
                    hrs = int(s // 3600)
                    mins = int((s % 3600) // 60)
                    secs = int(s % 60)
                    mils = int((s - int(s)) * 1000)
                    return f"{hrs:02d}:{mins:02d}:{secs:02d}.{mils:03d}"
                
                ts = f"{format_vtt_ts(start_sec)} --> {format_vtt_ts(end_sec)}"
                vtt.append(f"{ts}\n{item.get('text', '')}\n")
            return "\n".join(vtt)
        except:
            pass
            
    # If it's already VTT or fallback
    if not content.startswith("WEBVTT"):
        content = "WEBVTT\n\n" + content
    return content

def get_or_generate_transcript(video_path, user_id):
    """Retrieves existing transcript or generates one if missing and AI is enabled."""
    full_path = os.path.join(COURSES_DIR, video_path)
    base_path = os.path.splitext(full_path)[0]
    
    # 1. Try to find existing
    transcript_text = ""
    for ext in ['.vtt', '.srt']:
        if os.path.exists(base_path + ext):
            with open(base_path + ext, 'r', encoding='utf-8', errors='ignore') as f:
                transcript_text = f.read()
            break
            
    if transcript_text:
        return transcript_text

    # 2. If not found, attempt generation
    conn = get_db_connection()
    settings_rows = conn.execute("SELECT key, value FROM user_settings WHERE user_id=? AND key IN ('gemini_api_key', 'ai_features_enabled', 'ai_provider', 'local_whisper_url', 'gemini_model', 'local_model')", (user_id,)).fetchall()
    settings = {row['key']: row['value'] for row in settings_rows}
    conn.close()
    
    if settings.get('ai_features_enabled', 'true') != 'true':
         raise Exception("No transcript found and AI features are disabled.")
    
    provider = settings.get('ai_provider', 'gemini')
    api_key = settings.get('gemini_api_key')
    model_name = settings.get('gemini_model' if provider == 'gemini' else 'local_model', 'gemini-1.5-flash')
    local_whisper_url = settings.get('local_whisper_url')

    if provider == 'gemini':
        if not genai: raise Exception("Google GenAI library not installed.")
        if not api_key: raise Exception("Gemini API Key missing.")
        
        client = genai.Client(api_key=api_key)
        file_ref = client.files.upload(file=full_path)
        
        while True:
            file_info = client.files.get(name=file_ref.name)
            if file_info.state.name == "ACTIVE": break
            elif file_info.state.name == "FAILED": raise Exception("Gemini failed to process video.")
            time.sleep(2)
        
        prompt = "Generate a transcript for this video in WebVTT format. Output ONLY the WebVTT text, starting with 'WEBVTT'."
        response = client.models.generate_content(model=model_name, contents=[file_ref, prompt])
        vtt_content = convert_to_vtt(response.text)
        
        with open(base_path + ".vtt", 'w', encoding='utf-8') as f: f.write(vtt_content)
        return vtt_content
        
    elif provider == 'local':
        if not local_whisper_url: raise Exception("Local Whisper URL not configured.")
        if not shutil.which('ffmpeg'): raise Exception("ffmpeg not found.")
        
        audio_path = base_path + ".wav"
        subprocess.run(['ffmpeg', '-i', full_path, '-vn', '-acodec', 'pcm_s16le', '-ar', '16000', '-ac', '1', audio_path, '-y'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        with open(audio_path, 'rb') as audio_file:
            files = {'file': (os.path.basename(audio_path), audio_file, 'audio/wav')}
            resp = requests.post(local_whisper_url, files=files, data={'response_format': 'vtt'}, timeout=300)
            resp.raise_for_status()
            content = convert_to_vtt(resp.text)
            with open(base_path + ".vtt", 'w', encoding='utf-8') as f: f.write(content)
        
        if os.path.exists(audio_path): os.remove(audio_path)
        return content
    
    raise Exception("Transcript not found and generation failed.")


@app.route('/api/ai_chat_context', methods=['POST'])
@login_required
def ai_chat_context():
    if not genai:
        return jsonify({"status": "error", "message": "Google GenAI library not installed."}), 500
        
    user_id = current_user.id
    data = request.json
    video_path = data.get('video_path')
    context_type = data.get('context_type') # chat, summarize, flashcards, quiz
    prompt = data.get('prompt', '')
    
    # 1. Get Settings
    conn = get_db_connection()
    settings_rows = conn.execute("SELECT key, value FROM user_settings WHERE user_id=? AND key IN ('gemini_api_key', 'gemini_model', 'local_model', 'ai_features_enabled', 'ai_provider', 'local_ai_url')", (user_id,)).fetchall()
    settings = {row['key']: row['value'] for row in settings_rows}
    conn.close()
    
    if settings.get('ai_features_enabled', 'true') != 'true':
         return jsonify({"status": "error", "message": "AI features are disabled in settings."}), 403
    
    provider = settings.get('ai_provider', 'gemini')
    api_key = settings.get('gemini_api_key')
    model_name = settings.get('gemini_model' if provider == 'gemini' else 'local_model', 'gemini-2.0-flash')
    local_url = settings.get('local_ai_url')
    
    # 2. Get Transcript (Automated)
    try:
        transcript_text = get_or_generate_transcript(video_path, user_id)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
        
    # Limit transcript size for context window if super large (rough check)
    if len(transcript_text) > 100000:
        transcript_text = transcript_text[:100000] + "...(truncated)"

    # 3. Construct System Prompt based on Action
    system_instruction = f"You are an expert tutor helper. You have access to the transcript of the video the user is watching.\n\nTRANSCRIPT:\n{transcript_text}\n\n"
    
    final_prompt = ""
    # ... (same action logic) ...

    
    if context_type == 'chat':
        system_instruction += "Answer the user's question based strictly on the transcript provided. Be concise and helpful."
        final_prompt = prompt
        
    elif context_type == 'summarize':
        system_instruction += "Summarize the key learning points of this video in a concise bullet-point format. Use Markdown."
        final_prompt = "Summarize this video."
        
    elif context_type == 'flashcards':
        system_instruction += """
        Create 3-5 high-quality flashcards based on the key concepts in this video.
        Return ONLY valid JSON in the following format:
        [
            {"front": "Question", "back": "Answer"},
            {"front": "Question", "back": "Answer"}
        ]
        Do not add any markdown formatting (like ```json) around the output. Just the raw JSON array.
        """
        final_prompt = "Generate flashcards."
        
    elif context_type == 'quiz':
        system_instruction += """
        Create a short 3-question multiple choice quiz based on the video.
        Return ONLY valid JSON in the following format:
        {
            "title": "Video Quiz",
            "questions": [
                {
                    "question": "Question text?",
                    "options": ["Option A", "Option B", "Option C", "Option D"],
                    "answer": 0  // Index of correct option (0-3)
                }
            ]
        }
        Do not add any markdown formatting.
        """
        final_prompt = "Generate quiz."

    elif context_type == 'chapters':
        system_instruction += """
        Analyze the transcript and identify the main topic changes.
        Create a list of 5-8 chapters with titles and timestamps.
        Return ONLY valid JSON in the following format:
        [
            {"timestamp": 0, "title": "Introduction"},
            {"timestamp": 125, "title": "Setup Logic"}
        ]
        Do not add markdown formatting. Ensure timestamps are in SECONDS (integers or floats).
        """
        final_prompt = "Generate video chapters."

    elif context_type == 'glossary':
        system_instruction += """
        Extract the key technical terms and jargon from this video transcript.
        List them alphabetically with a brief, simple definition for each.
        Format the output as a Markdown list:
        **Term**: Definition
        """
        final_prompt = "Generate a glossary of terms."

    elif context_type == 'polish_notes':
        system_instruction += """
        The user has written some rough notes for this video.
        Your task is to rewrite and format them to be cleaner, clearer, and better structured (using Markdown).
        - Correct typos.
        - Use bullet points and bold headings.
        - Preserve any timestamps (e.g. [12:30]) exactly as they are.
        - If the user's note is vague, you can use the transcript context to clarify it slightly, but do not hallucinate new topics.
        """
        final_prompt = f"Here are my rough notes:\n{prompt}\n\nPlease polish them."

    # 4. Call AI Service
    try:
        ai_text = call_ai_service(provider, api_key, model_name, local_url, system_instruction, final_prompt, user_id, context_type)
        ai_text = ai_text.strip()
        
        # Post-process for specific actions
        if context_type == 'flashcards':
            # Clean potential markdown
            clean_text = ai_text.replace('```json', '').replace('```', '').strip()
            try:
                cards = json.loads(clean_text)
                
                # Save to DB
                conn = get_db_connection()
                count = 0
                today = datetime.date.today().strftime("%Y-%m-%d")
                
                # We need course_id. Retrieve it from video_path.
                vid_row = conn.execute('''
                    SELECT v.id, m.course_id 
                    FROM videos v 
                    JOIN modules m ON v.module_id = m.id 
                    WHERE v.path = ?
                ''', (video_path,)).fetchone()
                
                course_id = vid_row['course_id'] if vid_row else None
                
                if course_id:
                    for card in cards:
                        conn.execute('''
                            INSERT INTO flashcards (user_id, course_id, video_path, front, back, next_review_date)
                            VALUES (?, ?, ?, ?, ?, ?)
                        ''', (user_id, course_id, video_path, card['front'], card['back'], today))
                        count += 1
                    conn.commit()
                conn.close()
                return jsonify({"status": "success", "flashcards_count": count})
                
            except json.JSONDecodeError:
                return jsonify({"status": "error", "message": "Failed to parse AI response as JSON."})
                
        elif context_type == 'quiz':
             # Clean potential markdown
            clean_text = ai_text.replace('```json', '').replace('```', '').strip()
            return jsonify({"status": "success", "response": clean_text, "is_json": True})

        elif context_type == 'chapters':
            # Clean potential markdown
            clean_text = ai_text.replace('```json', '').replace('```', '').strip()
            try:
                chapters = json.loads(clean_text)
                conn = get_db_connection()
                # Clear old
                conn.execute('DELETE FROM video_chapters WHERE video_path = ?', (video_path,))
                for ch in chapters:
                    conn.execute('INSERT INTO video_chapters (video_path, timestamp, title) VALUES (?, ?, ?)',
                                 (video_path, ch['timestamp'], ch['title']))
                conn.commit()
                conn.close()
                return jsonify({"status": "success", "response": "Chapters generated"})
            except:
                return jsonify({"status": "error", "message": "Failed to parse chapters JSON"})

        return jsonify({"status": "success", "response": ai_text})
        
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/ai_course_chat', methods=['POST'])
@login_required
def ai_course_chat():
    if not genai:
        return jsonify({"status": "error", "message": "Google GenAI library not installed."}), 500
        
    user_id = current_user.id
    data = request.json
    course_id = data.get('course_id')
    prompt = data.get('prompt')
    
    conn = get_db_connection()
    settings_rows = conn.execute("SELECT key, value FROM user_settings WHERE user_id=? AND key IN ('gemini_api_key', 'gemini_model', 'ai_features_enabled', 'ai_provider', 'local_ai_url')", (user_id,)).fetchall()
    settings = {row['key']: row['value'] for row in settings_rows}
    
    if settings.get('ai_features_enabled', 'true') != 'true':
         conn.close()
         return jsonify({"status": "error", "message": "AI features are disabled."}), 403
    
    api_key = settings.get('gemini_api_key')
    model_name = settings.get('gemini_model', 'gemini-2.0-flash')
    provider = settings.get('ai_provider', 'gemini')
    local_url = settings.get('local_ai_url')

    # Get Course Context
    course = conn.execute('SELECT title FROM courses WHERE id=?', (course_id,)).fetchone()
    modules = conn.execute('SELECT id, title FROM modules WHERE course_id=? ORDER BY order_index', (course_id,)).fetchall()
    
    context_text = f"Course Title: {course['title']}\nCurriculum:\n"
    for mod in modules:
        context_text += f"\nModule: {mod['title']}\n"
        videos = conn.execute('SELECT title, path FROM videos WHERE module_id=? ORDER BY order_index', (mod['id'],)).fetchall()
        for v in videos:
            context_text += f"- {v['title']}\n"
            # Try to get a tiny snippet of transcript for keywords
            base_path = os.path.splitext(os.path.join(COURSES_DIR, v['path']))[0]
            for ext in ['.vtt', '.srt']:
                if os.path.exists(base_path + ext):
                    try:
                        with open(base_path + ext, 'r', encoding='utf-8', errors='ignore') as f:
                            snippet = f.read()[:500] # Just first 500 chars for keywords
                            context_text += f"  (Content Keywords: {snippet.replace('WEBVTT','').strip()[:200]}...)\n"
                    except: pass
                    break
    
    conn.close()

    system_instruction = f"""
    You are a course mentor. You have the curriculum and content snippets for the entire course.
    COURSE CONTEXT:
    {context_text}
    
    Answer the student's question about where to find information or general summaries across the whole course.
    Be encouraging and specific about module names or video titles.
    """

    try:
        response_text = call_ai_service(provider, api_key, model_name, local_url, system_instruction, prompt, user_id, "global_course_chat")
        return jsonify({"status": "success", "response": response_text})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/generate_course_bible', methods=['POST'])
@login_required
def generate_course_bible():
    if not genai:
        return jsonify({"status": "error", "message": "Google GenAI library not installed."}), 500
        
    user_id = current_user.id
    course_id = request.json.get('course_id')
    
    conn = get_db_connection()
    settings_rows = conn.execute("SELECT key, value FROM user_settings WHERE user_id=? AND key IN ('gemini_api_key', 'gemini_model', 'ai_features_enabled', 'ai_provider', 'local_ai_url')", (user_id,)).fetchall()
    settings = {row['key']: row['value'] for row in settings_rows}
    
    if settings.get('ai_features_enabled', 'true') != 'true':
         conn.close()
         return jsonify({"status": "error", "message": "AI features are disabled."}), 403
    
    api_key = settings.get('gemini_api_key')
    model_name = settings.get('gemini_model', 'gemini-2.0-flash')
    provider = settings.get('ai_provider', 'gemini')
    local_url = settings.get('local_ai_url')

    # Aggregrate all notes
    course = conn.execute('SELECT title FROM courses WHERE id=?', (course_id,)).fetchone()
    notes_rows = conn.execute('''
        SELECT v.title as video_title, n.content 
        FROM video_notes n
        JOIN videos v ON n.video_path = v.path
        JOIN modules m ON v.module_id = m.id
        WHERE n.user_id = ? AND m.course_id = ?
    ''', (user_id, course_id)).fetchall()
    conn.close()

    if not notes_rows:
        return jsonify({"status": "error", "message": "No notes found for this course. Write some notes first!"}), 400

    all_notes_text = f"Course: {course['title']}\n\n"
    for row in notes_rows:
        all_notes_text += f"### Video: {row['video_title']}\n{row['content']}\n\n"

    system_instruction = "You are a professional editor and technical writer."
    final_prompt = f"""
    Below are all the personal notes I've taken for the course "{course['title']}".
    Please transform these raw notes into a comprehensive "Course Bible" or "Master Study Guide".
    - Organize by logical themes or modules.
    - Use clean Markdown formatting (Headings, Bold text, Bullet points).
    - Summarize redundant points.
    - Add an "Executive Summary" at the beginning.
    - Preserve any code snippets or critical technical terms.
    
    NOTES:
    {all_notes_text}
    """

    try:
        response_text = call_ai_service(provider, api_key, model_name, local_url, system_instruction, final_prompt, user_id, "course_bible")
        return jsonify({"status": "success", "bible": response_text})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500





@app.route('/api/ai_plan_course', methods=['POST'])
@login_required
def ai_plan_course():
    if not genai:
        return jsonify({"status": "error", "message": "Google GenAI library not installed."}), 500
        
    user_id = current_user.id
    data = request.json
    course_id = data.get('course_id')
    hours = data.get('hours_per_week', 5)
    
    # 1. Get Settings
    conn = get_db_connection()
    settings_rows = conn.execute("SELECT key, value FROM user_settings WHERE user_id=? AND key IN ('gemini_api_key', 'gemini_model', 'local_model', 'ai_features_enabled', 'ai_provider', 'local_ai_url')", (user_id,)).fetchall()
    settings = {row['key']: row['value'] for row in settings_rows}
    
    if settings.get('ai_features_enabled', 'true') != 'true':
         conn.close()
         return jsonify({"status": "error", "message": "AI features are disabled in settings."}), 403
    
    api_key = settings.get('gemini_api_key')
    provider = settings.get('ai_provider', 'gemini')
    model_name = settings.get('gemini_model' if provider == 'gemini' else 'local_model', 'gemini-2.0-flash')
    local_url = settings.get('local_ai_url')
    
    # ... (course structure logic) ...
    course = conn.execute('SELECT title FROM courses WHERE id=?', (course_id,)).fetchone()
    modules = conn.execute('SELECT id, title FROM modules WHERE course_id=? ORDER BY order_index', (course_id,)).fetchall()
    
    syllabus_text = f"Course: {course['title']}\n"
    total_seconds = 0
    
    for mod in modules:
        syllabus_text += f"\nModule: {mod['title']}\n"
        videos = conn.execute('SELECT title, duration FROM videos WHERE module_id=? ORDER BY order_index', (mod['id'],)).fetchall()
        for v in videos:
            dur = int(v['duration'])
            total_seconds += dur
            syllabus_text += f"- {v['title']} ({dur // 60} mins)\n"
    
    conn.close()

    # 3. Construct Prompt
    system_instruction = "You are an expert curriculum designer."
    final_prompt = f"""
    Here is the syllabus for the course "{course['title']}":
    
    {syllabus_text}
    
    Total Duration: {total_seconds // 3600} hours {((total_seconds % 3600) // 60)} minutes.
    
    The student has {hours} hours per week available to study.
    
    Please create a structured Study Plan.
    - Break it down by Week (Week 1, Week 2, etc.).
    - Group videos logically based on the time constraint.
    - Include time for practice/review in your calculation (assume 1.5x video duration for actual study time).
    - Format output in clear Markdown.
    """

    # 4. Call AI Service
    try:
        plan_text = call_ai_service(provider, api_key, model_name, local_url, system_instruction, final_prompt, user_id, "course_plan")
        return jsonify({"status": "success", "plan": plan_text})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/generate_transcript', methods=['POST'])
@login_required
def generate_transcript():
    user_id = current_user.id
    data = request.json
    video_path = data.get('video_path')
    
    # 1. Get Settings
    conn = get_db_connection()
    settings_rows = conn.execute("SELECT key, value FROM user_settings WHERE user_id=? AND key IN ('gemini_api_key', 'ai_features_enabled', 'ai_provider', 'local_whisper_url', 'gemini_model', 'local_model')", (user_id,)).fetchall()
    settings = {row['key']: row['value'] for row in settings_rows}
    conn.close()
    
    if settings.get('ai_features_enabled', 'true') != 'true':
         return jsonify({"status": "error", "message": "AI features are disabled."}), 403
    
    provider = settings.get('ai_provider', 'gemini')
    api_key = settings.get('gemini_api_key')
    model_name = settings.get('gemini_model' if provider == 'gemini' else 'local_model', 'gemini-1.5-flash')
    local_whisper_url = settings.get('local_whisper_url')

    # 2. Locate File
    full_path = os.path.join(COURSES_DIR, video_path)
    if not os.path.exists(full_path):
        return jsonify({"status": "error", "message": "File not found."}), 404
        
    base_path = os.path.splitext(full_path)[0]
    vtt_path = base_path + ".vtt"

    # 3. Process based on Provider
    try:
        if provider == 'gemini':
            if not genai:
                return jsonify({"status": "error", "message": "Google GenAI library not installed."}), 500
            if not api_key:
                return jsonify({"status": "error", "message": "Gemini API Key missing."}), 400
                
            client = genai.Client(api_key=api_key)
            file_ref = client.files.upload(file=full_path)
            
            # Wait for processing
            print(f"DEBUG: Uploaded {full_path}, waiting for processing...")
            while True:
                file_info = client.files.get(name=file_ref.name)
                if file_info.state.name == "ACTIVE":
                    break
                elif file_info.state.name == "FAILED":
                    return jsonify({"status": "error", "message": "Gemini failed to process the video file."}), 500
                print("DEBUG: Still processing...")
                time.sleep(2)
            
            prompt = "Generate a transcript for this video in WebVTT format. Output ONLY the WebVTT text, starting with 'WEBVTT'. No conversational text."
            
            response = client.models.generate_content(
                model=gemini_model,
                contents=[file_ref, prompt]
            )
            
            vtt_content = convert_to_vtt(response.text)
                
            with open(vtt_path, 'w', encoding='utf-8') as f:
                f.write(vtt_content)
            
            # Log Usage
            try:
                conn = get_db_connection()
                conn.execute('INSERT INTO ai_logs (user_id, provider, model, action) VALUES (?, ?, ?, ?)', 
                             (user_id, 'gemini', gemini_model, 'transcribe_video'))
                conn.commit()
                conn.close()
            except: pass

            return jsonify({"status": "success", "message": "Transcript generated with Gemini!"})
            
        elif provider == 'local':
            if not local_whisper_url:
                return jsonify({"status": "error", "message": "Local Whisper URL not configured."}), 400
                
            # Check ffmpeg
            if not shutil.which('ffmpeg'):
                return jsonify({"status": "error", "message": "ffmpeg not found. Please install it to use local transcription (e.g. 'brew install ffmpeg')."}), 500
            
            # Extract Audio
            audio_path = base_path + ".wav"
            subprocess.run([
                'ffmpeg', '-i', full_path, '-vn', 
                '-acodec', 'pcm_s16le', '-ar', '16000', '-ac', '1', 
                audio_path, '-y'
            ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            # Send to Whisper
            with open(audio_path, 'rb') as audio_file:
                # OpenAI Whisper API format
                files = {'file': (os.path.basename(audio_path), audio_file, 'audio/wav')}
                data = {'response_format': 'vtt'} # Request VTT directly
                
                resp = requests.post(local_whisper_url, files=files, data=data, timeout=300)
                resp.raise_for_status()
                
                content = convert_to_vtt(resp.text)
                
                with open(vtt_path, 'w', encoding='utf-8') as f:
                    f.write(content)
            
            # Cleanup wav
            if os.path.exists(audio_path):
                os.remove(audio_path)
                
            # Log Usage
            try:
                conn = get_db_connection()
                conn.execute('INSERT INTO ai_logs (user_id, provider, model, action) VALUES (?, ?, ?, ?)', 
                             (user_id, 'local', 'whisper', 'transcribe_video'))
                conn.commit()
                conn.close()
            except: pass

            return jsonify({"status": "success", "message": "Transcript generated locally!"})
            
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/certificate/<int:course_id>')
@login_required
def download_certificate(course_id):
    conn = get_db_connection()
    user_id = current_user.id
    
    course = conn.execute('SELECT * FROM courses WHERE id = ?', (course_id,)).fetchone()
    if not course:
        abort(404)
        
    stats = get_course_stats(conn, course_id, user_id)
    total_videos = stats['total_videos']
    watched_count = stats['watched_count']
    total_duration = stats['total_duration']
    
    conn.close()
    
    if watched_count < total_videos and total_videos > 0:
        if request.args.get('preview') != 'true':
            flash("You must complete the course to download the certificate.")
            return redirect(url_for('player', course_id=course_id))

    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=landscape(A4))
    width, height = landscape(A4)
    
    p.setStrokeColor(colors.HexColor("#5022c3"))
    p.setLineWidth(5)
    p.rect(30, 30, width-60, height-60)
    p.setLineWidth(1)
    p.setStrokeColor(colors.black)
    p.rect(40, 40, width-80, height-80)
    
    p.setFont("Helvetica-Bold", 40)
    p.drawCentredString(width / 2, height - 120, "CERTIFICATE OF COMPLETION")
    
    p.setFont("Helvetica", 16)
    p.drawCentredString(width / 2, height - 160, "This is to certify that")
    
    p.setFont("Helvetica-BoldOblique", 32)
    p.setFillColor(colors.HexColor("#5022c3"))
    p.drawCentredString(width / 2, height - 210, current_user.name)
    p.setFillColor(colors.black)
    
    p.setFont("Helvetica", 16)
    p.drawCentredString(width / 2, height - 250, "has successfully completed the course")
    
    p.setFont("Helvetica-Bold", 28)
    alt_title = course['alternate_title']
    title_text = alt_title if alt_title else course['title']
    p.drawCentredString(width / 2, height - 290, title_text)

    if total_duration > 0:
        hours = int(total_duration // 3600)
        minutes = int((total_duration % 3600) // 60)
        duration_text = f"Duration: {hours}h {minutes}m"
        p.setFont("Helvetica", 14)
        p.setFillColor(colors.grey)
        p.drawCentredString(width / 2, height - 315, duration_text)
        p.setFillColor(colors.black)
    
    desc_text = course['description'] if course['description'] else "This course covers advanced topics and practical skills."
    desc_clean = re.sub('<[^<]+?>', '', desc_text)
    
    styles = getSampleStyleSheet()
    styleN = ParagraphStyle(
        'Normal',
        parent=styles['Normal'],
        fontSize=12,
        alignment=TA_CENTER,
        leading=16
    )
    
    frame = Frame(100, height - 420, width - 200, 100, showBoundary=0)
    story = [Paragraph(desc_clean[:300] + ("..." if len(desc_clean) > 300 else ""), styleN)]
    frame.addFromList(story, p)
    
    today = datetime.date.today().strftime("%d %B %Y")
    cert_id = str(uuid.uuid4()).split('-')[0].upper()
    
    p.setFont("Helvetica", 12)
    p.drawString(100, 100, f"Date of Completion: {today}")
    p.drawString(100, 80, f"Issued by: SkillForge - Organize. Learn. Master.")
    
    p.drawString(width - 300, 100, f"Certificate ID: {cert_id}")
    p.drawString(width - 300, 80, "Signature: _______________________")
    
    try:
        p.drawImage("static/images/logo.png", width/2 - 40, 105, width=80, preserveAspectRatio=True, mask='auto')
    except:
        pass

    p.showPage()
    p.save()
    
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name=f"Certificate_{course['title'][:20]}.pdf", mimetype='application/pdf')

# Ensure DB is initialized on startup
with app.app_context():
    init_db()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=True)