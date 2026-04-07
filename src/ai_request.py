"""
ai_request.py — Claude-powered artist selection for SpotifyDJ.

Called by spotify_dj.py when it reads an "ai:..." command from dj_input.txt.
Returns a list of artist names to play, which the DJ then searches Spotify for.
"""

import json
import os
import urllib.request
import urllib.error


def ask_claude(user_request: str) -> list[str]:
    """
    Send the user's request to Claude API.
    Returns a list of artist name strings (5-8 artists).
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not set in .env")

    system_prompt = (
        "You are the music brain of a Spotify DJ application. "
        "Your only job is to return a JSON array of artist name strings — nothing else. "
        "No explanation, no preamble, no markdown, no code fences. Just a raw JSON array.\n\n"
        "Rules:\n"
        "- If the user names one or more specific artists, return ONLY those exact artists. "
        "Do not add similar artists or recommendations. Just the ones they named.\n"
        "- If the request is a vibe, mood, genre, or activity (e.g. 'chill anime music', "
        "'current pop hits', 'something hype'), return 5 to 8 artists that best fit.\n"
        "- Genre accuracy is critical. Artists must strictly match the genre or style requested. "
        "Do not include artists from adjacent or loosely related genres. "
        "For example: a K-Pop request must return K-Pop acts only, not indie pop or Western pop. "
        "An anime music request must return Japanese artists who make anime openings/endings, "
        "not general J-Pop or artists from unrelated genres.\n"
        "- Only return artists with a well-established presence on Spotify. "
        "No extremely obscure acts that are unlikely to have a proper catalog.\n"
        "- Match artist names as accurately as possible — correct minor spelling or "
        "speech-recognition errors (e.g. 'huntr x' → 'HUNTR/X' or best match on Spotify).\n"
        "- Never include song titles, only artist names.\n"
        "- Output must be valid JSON, e.g.: [\"Artist One\", \"Artist Two\", \"Artist Three\"]"
    )

    user_prompt = (
        f"User request: {user_request}\n\n"
        "Return a JSON array of artist names that best fit this request."
    )

    payload = json.dumps({
        "model": "claude-sonnet-4-6",
        "max_tokens": 1000,
        "system": system_prompt,
        "messages": [
            {"role": "user", "content": user_prompt}
        ]
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type":      "application/json",
            "x-api-key":         api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST"
    )

    data = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as e:
            if e.code == 529 and attempt < 2:
                import time
                print(f"  Claude overloaded (529), retrying in 3s...")
                time.sleep(3)
                continue
            body = e.read().decode("utf-8")
            raise ValueError(f"Claude API {e.code}: {body}")
    if data is None:
        raise ValueError("Claude API failed after 3 attempts")

    raw = data["content"][0]["text"].strip()

    # Strip accidental code fences if Claude adds them despite instructions
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    artists = json.loads(raw)
    if not isinstance(artists, list):
        raise ValueError(f"Claude returned non-list: {raw}")

    return [str(a).strip() for a in artists if a]


def resolve_artists_to_ids(sp, artist_names: list[str], known_artists: dict) -> list[tuple[str, str]]:
    """
    Given a list of artist name strings, return (name, spotify_id) pairs.
    Checks the known library first, falls back to Spotify search.
    Returns only artists that could be resolved.
    """
    # Build a lowercase lookup of known artists for fast matching
    known_lower = {k.lower(): (k, v) for k, v in known_artists.items()}

    resolved = []
    for name in artist_names:
        lower = name.lower()

        # Check known library first
        if lower in known_lower:
            canonical, artist_id = known_lower[lower]
            print(f"  AI artist (known): {canonical}")
            resolved.append((canonical, artist_id))
            continue

        # Search Spotify for unknown artists
        try:
            results = sp.search(q=f"artist:{name}", type="artist", limit=1)
            items = results.get("artists", {}).get("items", [])
            if items:
                found = items[0]
                print(f"  AI artist (found on Spotify): {found['name']} (searched: {name})")
                resolved.append((found["name"], found["id"]))
            else:
                print(f"  AI artist (not found on Spotify): {name} — skipping")
        except Exception as e:
            print(f"  AI artist search failed for '{name}': {e}")

    return resolved