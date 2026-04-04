"""
dj_voice.py — Global push-to-talk voice input for SpotifyDJ.

Press F13+V (Right Ctrl + V) once to start listening, again to stop.
Works globally — even in fullscreen games.
Uses Google Speech Recognition (free, no API key needed).

Run this alongside spotify_dj.py or dj_server.py in a separate terminal.

Usage:
    python scripts/dj_voice.py

Requirements:
    pip install SpeechRecognition sounddevice numpy keyboard
"""

import sys
import os
import time
import threading
import tempfile
import wave
import json as _json
import urllib.request
import platform
import numpy as np
import sounddevice as sd
import speech_recognition as sr
from pathlib import Path

IS_LINUX = platform.system() == "Linux"
if not IS_LINUX:
    import keyboard

BASE_DIR   = Path(__file__).resolve().parent.parent
INPUT_FILE = BASE_DIR / "data" / "dj_input.txt"

SAMPLE_RATE = 16000
CHANNELS    = 1

recording    = False
frames       = []
stream       = None
frames_lock  = threading.Lock()


def send_command(text: str):
    with open(INPUT_FILE, "w", encoding="utf-8") as f:
        f.write(text)


def log_to_server(msg: str):
    """Send a log message to the dashboard server (best-effort, silent on failure)."""
    try:
        payload = _json.dumps({"msg": msg}).encode("utf-8")
        req = urllib.request.Request(
            "http://127.0.0.1:5001/voice_log",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        urllib.request.urlopen(req, timeout=1)
    except Exception:
        pass  # server not running — just print to terminal


def audio_to_wav(audio: np.ndarray) -> str:
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    with wave.open(tmp.name, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(audio.tobytes())
    return tmp.name


def transcribe(wav_path: str):
    r = sr.Recognizer()
    with sr.AudioFile(wav_path) as source:
        audio = r.record(source)
    try:
        return r.recognize_google(audio)
    except sr.UnknownValueError:
        print("  Could not understand audio.")
        return None
    except sr.RequestError as e:
        print(f"  Speech recognition error: {e}")
        return None


def start_recording():
    global recording, frames, stream
    recording = True
    frames    = []
    msg = "  Recording... (press F13+V again to stop)"
    print(msg)
    log_to_server("🎙 Recording...")

    def callback(indata, frame_count, time_info, status):
        if recording:
            with frames_lock:
                frames.append(indata.copy())

    stream = sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype="int16",
        callback=callback,
    )
    stream.start()


def stop_recording():
    global recording, stream
    recording = False
    if stream:
        stream.stop()
        stream.close()
        stream = None

    with frames_lock:
        captured = list(frames)

    if not captured:
        print("  No audio captured.")
        log_to_server("  No audio captured.")
        return

    audio    = np.concatenate(captured, axis=0)
    wav_path = audio_to_wav(audio)

    print("  Transcribing...")
    log_to_server("  Transcribing...")
    try:
        text = transcribe(wav_path)
    finally:
        os.unlink(wav_path)

    if text:
        print(f'  You said: "{text}"')
        log_to_server(f'You said: "{text}"')
        send_command(f"ai:{text}")
        print("  -> Sent to AI DJ.")
        log_to_server("→ Sent to AI DJ.")
    print()


def on_hotkey():
    global recording
    if not recording:
        start_recording()
    else:
        threading.Thread(target=stop_recording, daemon=True).start()


def main():
    print("=" * 50)
    print("  SpotifyDJ - Voice Mode")
    print("=" * 50)
    print("  F13+V (Right Ctrl + V) - toggle recording")
    print()
    print("  Works globally in games and other apps.")
    print("  Press Ctrl+C in this window to exit.")
    print("=" * 50)
    print()

    if IS_LINUX:
        # On Linux, keyboard library requires root. Instead we watch
        # dj_input.txt for "voice-toggle" written by dj_hotkey_linux.py
        print("  Listening for voice-toggle command (via dj_hotkey_linux.py)...")
        print()
        try:
            while True:
                if INPUT_FILE.exists():
                    try:
                        content = INPUT_FILE.read_text().strip()
                        if content == "voice-toggle":
                            INPUT_FILE.unlink()
                            on_hotkey()
                    except Exception:
                        pass
                time.sleep(0.1)
        except KeyboardInterrupt:
            print("\nClosing voice mode.")
            sys.exit(0)
    else:
        keyboard.add_hotkey("f13+v", on_hotkey, suppress=True)
        print("  Listening for F13+V...")
        print()
        try:
            keyboard.wait()
        except KeyboardInterrupt:
            print("\nClosing voice mode.")
            sys.exit(0)


if __name__ == "__main__":
    main()
