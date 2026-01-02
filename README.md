# SkillForge

SkillForge is a self-hosted, personal learning management system (LMS) designed to help you organize, watch, and master your video course collections locally. Think of it as your own private Udemy or Netflix for learning running on your machine.

## 🌟 Features

### Core Learning
-   **Course Organization**: Automatically scans your `courses` directory and organizes content into courses and modules based on folder structure.
-   **Smart Progress Tracking**: Tracks video completion and exact watch time. Resumes playback exactly where you left off.
-   **Certificates**: Automatically generates a downloadable PDF certificate of completion when you finish a course.

### Enhanced Player
-   **Time-Based Tracking**: Circular progress indicators showing actual time learned vs total duration.
-   **Interactive Player**: Playback speed control, cinema mode, and auto-play next video.
-   **Subtitles**: Automatically detects and loads `.srt` or `.vtt` subtitles.
-   **Notes & Bookmarks**: Take markdown notes per video and bookmark specific timestamps for quick reference.

### Advanced Tools
-   **📊 Analytics Dashboard**: Visualizes your learning habits with GitHub-style heatmaps, streak counters, and total watch stats.
-   **📂 Resource Vault**: A centralized, searchable library of all non-video files (PDFs, ZIPs, code) across all your courses.
-   **📑 Playlists**: Create custom learning paths (e.g., "Weekend Study", "React Path") by combining videos from different courses.
-   **🧠 Interactive Quizzes**: Supports `quiz.json` files in course folders to render native multiple-choice tests.
-   **🔍 Deep Search**: Search across video titles and *inside* your personal notes to find concepts instantly.

### System
-   **Multi-User**: separate progress and notes for different family members.
-   **Backup & Restore**: Export all your data (progress, notes, playlists) to a JSON file for safekeeping or migration.

## 🚀 Installation & Usage

### Option A: Docker (Recommended)

1.  **Prepare Directories**:
    Ensure you have a `courses` folder and an empty database file.
    ```bash
    mkdir courses
    touch courses.db
    ```

2.  **Run with Docker**:
    ```bash
    docker compose up -d
    ```

3.  **Access**:
    Open `http://localhost:5001` in your browser.

### Option B: Manual Installation (Python)

1.  **Clone the Repository**
    ```bash
    git clone <repository_url>
    cd SkillForge
    ```

2.  **Install Dependencies**
    ```bash
    pip install -r requirements.txt
    ```

3.  **Start the Application**:
    Run the startup script:
    ```bash
    ./start.sh
    ```
    *(Alternatively, run `python3 app.py` manually)*.

## 📁 Content Structure

Place your course content in the `courses/` directory. The system supports nested folders which are treated as modules.

**Standard Video Course:**
```text
courses/
└── Advanced Python/
    ├── cover.png (Optional thumbnail)
    ├── 01_Intro/
    │   ├── 01_Welcome.mp4
    │   ├── 01_Welcome.srt (Optional subtitle)
    │   └── resources.pdf
    └── 02_Deep_Dive/
        ├── 01_Memory.mp4
        └── quiz.json (Optional quiz)
```

**Quiz Format (`quiz.json`):**
```json
{
  "title": "Module 1 Review",
  "questions": [
    {
      "question": "What is 2+2?",
      "options": ["3", "4", "5"],
      "answer": 1
    }
  ]
}
```

## 🛠 Tech Stack

-   **Backend**: Python (Flask, SQLite)
-   **Frontend**: HTML5, CSS3 (Variables for Dark Mode), JavaScript (Vanilla)
-   **Media**: HTML5 Video API, WebVTT
-   **PDF Generation**: ReportLab

## 📄 License

This project is for personal use.