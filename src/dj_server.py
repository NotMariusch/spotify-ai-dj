"""
dj_server.py — Dashboard server for SpotifyDJ.

Streams DJ output to the browser dashboard in real time via websockets.
Also handles commands from the dashboard (skip, ban, mode switches, AI requests).

Usage:
    python src/dj_server.py

Then open http://127.0.0.1:5001 in your browser.

Requirements:
    pip install flask flask-socketio spotipy python-dotenv
"""

import os
import sys
import subprocess
import threading
import queue
import time
import re
from dotenv import load_dotenv
from pathlib import Path
from flask import Flask, request
from flask_socketio import SocketIO, emit

BASE_DIR   = Path(__file__).resolve().parent.parent
INPUT_FILE = BASE_DIR / "data" / "dj_input.txt"
DJ_SCRIPT  = BASE_DIR / "src" / "spotify_dj.py"

load_dotenv(BASE_DIR / ".env")

app = Flask(__name__)
app.config["SECRET_KEY"] = "spotifydj-dashboard"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

dj_log_queue    = queue.Queue()
voice_log_queue = queue.Queue()

state = {
    "current_artist": "—",
    "current_track":  "—",
    "mode":           "—",
    "recording":      False,
    "is_playing":     False,
}

dj_process = None

# ── DJ process ──

def parse_dj_line(line: str):
    line = line.strip()
    if not line:
        return
    m = re.match(r"^(?:Global )?DJ -> (.+)$", line)
    if m:
        state["current_artist"] = m.group(1).strip()
    if re.search(r"AI request:", line):
        state["current_artist"] = "AI Mode"
        state["mode"] = "AI"
    if line.startswith("NOW_PLAYING:"):
        state["current_track"] = line[len("NOW_PLAYING:"):]
    if line.startswith("IS_PLAYING:"):
        state["is_playing"] = line[len("IS_PLAYING:"):] == "true"


def launch_dj():
    global dj_process
    python = sys.executable
    print(f"[server] Python: {python}")
    print(f"[server] Launching DJ: {DJ_SCRIPT}")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"]       = "1"
    env["PYTHONUNBUFFERED"] = "1"
    dj_process = subprocess.Popen(
        [python, str(DJ_SCRIPT)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(BASE_DIR),
        env=env,
    )
    print(f"[server] DJ PID: {dj_process.pid}")

    def reader():
        for line in dj_process.stdout:
            line = line.rstrip("\n")
            parse_dj_line(line)
            if not line.startswith(("NOW_PLAYING:", "IS_PLAYING:")):
                dj_log_queue.put(line)
        dj_process.wait()
        dj_log_queue.put("[DJ process exited]")

    threading.Thread(target=reader, daemon=True).start()


# ── Broadcast loop ──

def broadcast_loop():
    while True:
        try:
            # DJ log
            lines = []
            while not dj_log_queue.empty():
                try:
                    lines.append(dj_log_queue.get_nowait())
                except queue.Empty:
                    break
            if lines:
                socketio.emit("dj_log", {"lines": lines}, namespace="/")

            # Voice log
            vlines = []
            while not voice_log_queue.empty():
                try:
                    vlines.append(voice_log_queue.get_nowait())
                except queue.Empty:
                    break
            if vlines:
                socketio.emit("voice_log", {"lines": vlines}, namespace="/")

            socketio.emit("state", state, namespace="/")
        except Exception as e:
            print(f"[broadcast] error: {e}")

        time.sleep(0.25)


# ── Routes ──

@app.route("/")
def index():
    with open(BASE_DIR / "src" / "dj_dashboard.html", encoding="utf-8") as f:
        return f.read()

@app.route("/voice_log", methods=["POST"])
def voice_log_endpoint():
    data = request.get_json(silent=True) or {}
    msg  = data.get("msg", "")
    if msg:
        voice_log_queue.put(msg)
    return "", 204


# ── Websocket events ──

def send_command(text: str):
    with open(INPUT_FILE, "w", encoding="utf-8") as f:
        f.write(text)


@socketio.on("command")
def handle_command(data):
    cmd = data.get("cmd", "").strip()
    if not cmd:
        return

    mode_labels = {"1": "American Rap", "2": "German Trap", "3": "K-Pop", "4": "J-Pop", "5": "Global"}

    if cmd == "ai-mode":
        return  # UI-only, no action

    if cmd in ("skip", "ban", "quit") or cmd in ("1", "2", "3", "4", "5"):
        send_command(cmd)
        if cmd in mode_labels:
            state["mode"]           = mode_labels[cmd]
            state["current_artist"] = "—"
            state["current_track"]  = "—"
            voice_log_queue.put(f"→ Switched to {mode_labels[cmd]} mode")
        else:
            voice_log_queue.put(f"→ Command sent: {cmd}")

    elif cmd.startswith("ai:"):
        req = cmd[3:].strip()
        send_command(cmd)
        state["mode"]           = "AI"
        state["current_artist"] = "AI Mode"
        voice_log_queue.put(f"→ AI request: \"{req}\"")

    else:
        send_command(f"ai:{cmd}")
        state["mode"]           = "AI"
        state["current_artist"] = "AI Mode"
        voice_log_queue.put(f"→ AI request: \"{cmd}\"")


@socketio.on("connect")
def on_connect():
    emit("state", state)


# ── Entry point ──

if __name__ == "__main__":
    launch_dj()
    print("[server] Dashboard at http://127.0.0.1:5001")
    threading.Thread(target=broadcast_loop, daemon=True).start()
    socketio.run(app, host="127.0.0.1", port=5001, debug=False, use_reloader=False, allow_unsafe_werkzeug=True)