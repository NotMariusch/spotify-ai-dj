@echo off
cd /d "C:\Users\mariu\SpotifyDJ"

echo Starting SpotifyDJ Dashboard...
echo Open http://127.0.0.1:5001 in your browser.
echo.

"C:\Users\mariu\AppData\Local\Python\pythoncore-3.14-64\python.exe" "C:\Users\mariu\SpotifyDJ\src\dj_server.py"

echo DJ exited at %DATE% %TIME% >> "C:\Users\mariu\SpotifyDJ\data\dj_crash.log"
pause