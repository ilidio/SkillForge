# SkillForge

SkillForge is a self-hosted, personal learning management system (LMS) designed to help you organize, watch, and master your video course collections locally. Think of it as your own private Udemy or Netflix for learning, running entirely on your machine.

## 🌟 Features

### 🧠 AI-Powered Learning (Superpowers)
SkillForge transforms passive watching into active learning using cutting-edge AI. Choose between **Gemini Cloud** (Google) or **Local AI** (Fully Offline).

-   **🤖 Ask the Video**: Contextual chat window. Ask specific questions about the lesson and get answers based on the transcript.
-   **✨ Auto-Generate Transcripts**: Missing subtitles? One click extracts audio and generates high-quality WebVTT transcripts using AI.
-   **📝 One-Click Summaries**: Instantly generate concise Markdown summaries of any video.
-   **🧠 Auto-Flashcards**: AI extracts key concepts and automatically adds them to your Study deck.
-   **🎓 Instant Quizzes**: Generates interactive multiple-choice tests from video content to verify your knowledge.
-   **✨ Note Polisher**: Turns messy, rough bullet points into clean, structured, professional notes while preserving your timestamps.
-   **📅 Smart Course Planner**: Provide your weekly availability (e.g., "5 hours/week") and the AI builds a custom schedule for the entire course.
-   **📖 Instant Glossary**: Automatically identifies and defines technical jargon used in the video.
-   **📊 AI Analytics**: Track your AI usage, including requests by provider, model, and action.

### Core Learning & Organization
-   **Folder-Based Auto-Sync**: Simply drop folders into `courses/`. SkillForge scans them and builds your curriculum automatically.
-   **Smart Progress Tracking**: Remembers exactly where you left off in every video.
-   **Time-Based Analytics**: Visualize learning time with heatmaps, streaks, and completion percentages.
-   **Custom Playlists**: Combine videos from different courses into specialized learning paths.
-   **Resource Vault**: Centralized access to all PDFs, ZIPs, and code files attached to your courses.
-   **Certificates**: Generate and download professional PDF certificates of completion.

## 🚀 Installation & Usage

### Option A: Manual Installation (Recommended for AI)

1.  **Clone the Repository**:
    ```bash
    git clone <repository_url>
    cd SkillForge
    ```

2.  **Install Dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

3.  **Start the Application**:
    ```bash
    ./start.sh
    ```

### Option B: Docker

1.  **Run with Docker Compose**:
    ```bash
    docker compose up -d
    ```

## 🤖 Setting Up AI Features

SkillForge is flexible. Configure your "Brain" in the **Settings** page:

### 1. Gemini Cloud (Cloud-based)
-   Go to **Settings**.
-   Select **Provider: Gemini**.
-   Enter your **Google Gemini API Key**.
-   Click **Refresh (↻)** to pull the latest models (e.g., `gemini-2.0-flash`).

### 2. Local AI & Offline Transcription (Privacy-focused)
You can run a local AI server for chat and a local Whisper server for transcription.

**Easy Setup Script**:
We provide a script to setup a local Transcription Server automatically:
1.  Open a new terminal tab.
2.  Run: `./ai_start_whisperer.sh`
3.  The script will install `faster-whisper`, start the server on port 9000, and **auto-configure** SkillForge to use it.
4.  Restart SkillForge to apply the new settings.

## 📁 Content Structure (Critical)

SkillForge uses your folder hierarchy to define the UI. No manual database entry required!

```text
courses/
│
├── JavaScript_Mastery/            <-- Course Title
│   ├── cover.png                  <-- Thumbnail (optional)
│   │
│   ├── 01_Basics/                 <-- Module 1
│   │   ├── 01_Variables.mp4       <-- Video (sorted by name)
│   │   ├── 01_Variables.vtt       <-- Subtitles (AI can generate these!)
│   │   └── reference.pdf          <-- Resource
│   │
│   └── 02_Functions/              <-- Module 2
│       ├── 01_Intro.mp4
│       └── quiz.json              <-- Custom Quiz (optional)
```

## 🛠 Tech Stack

-   **Backend**: Python (Flask, SQLite)
-   **AI Engine**: Google GenAI (Gemini), Faster-Whisper (Local), Requests
-   **Frontend**: HTML5, Vanilla JS, CSS Variables (Dark/Light mode)
-   **Processing**: FFmpeg (for local audio extraction)
-   **PDFs**: ReportLab

## 🔒 Security & Privacy
SkillForge is designed to be private. Your `courses.db` (containing progress and API keys) and your `courses/` folder are ignored by Git to prevent accidental data leaks.

---
*SkillForge - Organize. Learn. Master.*
