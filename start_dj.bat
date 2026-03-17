@echo off
cd /d "C:\Users\mariu\SpotifyDJ"

echo Starting Spotify DJ...
"C:\Users\mariu\AppData\Local\Python\pythoncore-3.14-64\python.exe" "C:\Users\mariu\SpotifyDJ\src\spotify_dj.py"

echo DJ exited at %DATE% %TIME% >> "C:\Users\mariu\SpotifyDJ\data\dj_crash.log"