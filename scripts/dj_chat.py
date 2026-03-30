"""
dj_chat.py — Terminal chat interface for SpotifyDJ AI mode.

Run this in a separate terminal while spotify_dj.py is running.
Type your request and hit Enter — the DJ will pick artists based on what you said.

Usage:
    python scripts/dj_chat.py
"""

import os
import sys
from pathlib import Path

# Resolve dj_input.txt relative to project root (one level up from scripts/)
BASE_DIR = Path(__file__).resolve().parent.parent
INPUT_FILE = BASE_DIR / "data" / "dj_input.txt"


def send_command(text: str):
    with open(INPUT_FILE, "w", encoding="utf-8") as f:
        f.write(text)


def main():
    print("=" * 50)
    print("  SpotifyDJ — AI Chat Mode")
    print("=" * 50)
    print("  Type what you want to hear and press Enter.")
    print("  Examples:")
    print("    play some current K-Pop hits")
    print("    I want chill anime music")
    print("    give me hype rap")
    print()
    print("  Other commands: skip, ban, quit, 1-5 (modes)")
    print("  Type 'exit' to close this chat window only.")
    print("=" * 50)
    print()

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nClosing chat.")
            sys.exit(0)

        if not user_input:
            continue

        if user_input.lower() == "exit":
            print("Closing chat window. DJ keeps running.")
            sys.exit(0)

        # Pass-through commands go directly
        if user_input in ("skip", "ban", "quit") or user_input in ("1", "2", "3", "4", "5"):
            send_command(user_input)
            print(f"  → Sent: {user_input}")
            continue

        # Everything else is treated as an AI request
        send_command(f"ai:{user_input}")
        print(f"  → Asking AI DJ... (check your DJ terminal for output)")
        print()


if __name__ == "__main__":
    main()