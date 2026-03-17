# Spotify AI DJ

Personal autonomous Spotify DJ that learns listening behavior and plays music automatically.

## Features

- Autonomous Spotify playback
- Multiple DJ modes
- Automatic smooth transitions
- Crash-safe background runner
- Persistent learning memory
- Hotkey control via AutoHotkey

## Modes

F13 + 1 → Hype  
F13 + 2 → German trap  
F13 + 3 → KPOP 
F13 + 4 → JPOP 
F13 + 5 → Global AI DJ

## Setup

1 Install Python

2 Install dependencies


pip install -r requirements.txt


3 Create `.env`


SPOTIFY_CLIENT_ID=YOUR_ID
SPOTIFY_CLIENT_SECRET=YOUR_SECRET
SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/callback


4 Run


python src/spotify_dj.py
