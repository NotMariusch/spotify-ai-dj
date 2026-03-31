"""
dj_voice.py — Global push-to-talk voice input for SpotifyDJ.

Press F13+V (Right Ctrl + V) once to start listening, again to stop.
Works globally — even in fullscreen games.
Uses Google Speech Recognition (free, no API key needed).

Run this alongside spotify_dj.py in a separate terminal.

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
import numpy as np
import sounddevice as sd
import speech_recognition as sr
import keyboard
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
INPUT_FILE = BASE_DIR / "data" / "dj_input.txt"

SAMPLE_RATE = 16000
CHANNELS = 1

recording = False
frames = []
stream = None
frames_lock = threading.Lock()


def send_command(text: str):
    with open(INPUT_FILE, "w", encoding="utf-8") as f:
        f.write(text)


def audio_to_wav(audio: np.ndarray) -> str:
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    with wave.open(tmp.name, 'wb') as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(audio.tobytes())
    return tmp.name


def transcribe(wav_path: str) -> str | None:
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
    frames = []
    print("\n  Recording... (press F13+V again to stop)")

    def callback(indata, frame_count, time_info, status):
        if recording:
            with frames_lock:
                frames.append(indata.copy())

    stream = sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype='int16',
        callback=callback
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
        return

    audio = np.concatenate(captured, axis=0)
    wav_path = audio_to_wav(audio)

    print("  Transcribing...")
    try:
        text = transcribe(wav_path)
    finally:
        os.unlink(wav_path)

    if text:
        print(f"  You said: \"{text}\"")
        send_command(f"ai:{text}")
        print("  -> Sent to AI DJ.")
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

    keyboard.add_hotkey('f13+v', on_hotkey, suppress=True)

    print("  Listening for F13+V...")
    print()

    try:
        keyboard.wait()
    except KeyboardInterrupt:
        print("\nClosing voice mode.")
        sys.exit(0)


if __name__ == "__main__":
    main()