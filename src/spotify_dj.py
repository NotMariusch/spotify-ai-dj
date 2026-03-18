import os
import time
import json
import random
import traceback
import datetime
import re
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv
from pathlib import Path
from collections import deque

# =====================
# SPOTIFY CONFIG
# =====================

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

CLIENT_ID     = os.getenv("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
REDIRECT_URI  = os.getenv("SPOTIFY_REDIRECT_URI")

SCOPE = "user-modify-playback-state user-read-playback-state"

# =====================
# SETTINGS
# =====================

# AUTO_INTERVAL and SMOOTH_THRESHOLD removed -- smooth transition replaced
# by track_finished() which resumes immediately when a track ends naturally.

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MEMORY_FILE        = os.path.join(BASE_DIR, "data", "dj_memory.json")
INPUT_FILE         = os.path.join(BASE_DIR, "data", "dj_input.txt")
CACHE_FILE         = os.path.join(BASE_DIR, "data", "track_cache.json")
DISCOVERED_FILE    = os.path.join(BASE_DIR, "data", "discovered_artists.json")
RECENT_FILE        = os.path.join(BASE_DIR, "data", "recent_tracks.json")
LOCK_FILE          = os.path.join(BASE_DIR, "data", "dj.lock")
BANNED_FILE        = os.path.join(BASE_DIR, "data", "banned_tracks.json")
CACHE_TTL_DAYS     = 7

MEMORY_VERSION = 1

current_pool = None
auto_mode    = None

# Tracks what's currently playing so we can judge it when the next track starts.
now_playing = {
    "artist":      None,   # artist name string
    "mode":        None,   # mode string ("american_rap", "kpop", etc.)
    "duration":    0,      # track duration_ms
    "started":     0.0,    # time.time() when playback started
    "progress_ms": 0,      # last known Spotify progress_ms, updated each poll
                            # used by judge_last_track() to avoid wall-clock drift
    "uri":         None,   # Spotify URI of the track the DJ started
                            # used by track_finished() to verify Spotify is still
                            # playing the right track before acting on its state
    "is_trial":    False,  # True if the current artist is a non-graduated discovery
}

# Prevents the pause detection message from printing every 5s loop iteration.
_pause_logged = False

track_disk_cache = {}

# Raised by fetch_artist_tracks_by_id when Spotify returns 429, so callers
# can react immediately instead of sleeping the full Retry-After inline.
class RateLimitError(Exception):
    def __init__(self, retry_after):
        self.retry_after = retry_after

# Artists that had no cache entry AND couldn't be fetched due to rate limiting.
# They stay in their pools but are skipped during play until successfully fetched.
uncached_artists = set()

# =====================
# DISK CACHE
# =====================

def load_track_cache():
    global track_disk_cache
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                track_disk_cache = json.load(f)
        except Exception:
            track_disk_cache = {}

def save_track_cache():
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(track_disk_cache, f, indent=2)

load_track_cache()

# =====================
# FILTER SETTINGS
# =====================

TRACK_KEYWORDS_WORD = [
    "remix", "live", "edit", "clean", "demo", "acoustic",
    "instrumental", "karaoke", "nightcore", "extended",
    "censored", "remaster", "remastered", "reverb",
]

TRACK_KEYWORDS_SUBSTR = [
    "sped up", "sped-up", "speed up", "spedup",
    "slowed reverb", "slowed",
    "radio edit",
]

ALBUM_KEYWORDS_WORD = [
    "tour", "concert", "arena", "stadium", "festival", "anniversary",
]

LANGUAGE_VERSION_PATTERN = re.compile(
    r"(japanese|english|chinese|mandarin|spanish|french|german|thai|vietnamese)"
    r"\s*(ver\.?|version|edit)",
    re.IGNORECASE
)

ALBUM_KEYWORDS_SUBSTR = [
    "tokyo dome",
    "world tour",
]

recent_titles  = deque(maxlen=30)
recent_artists = deque(maxlen=10)

def load_recent():
    """Restore recent_titles from disk so repeats are avoided across restarts."""
    if os.path.exists(RECENT_FILE):
        try:
            with open(RECENT_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for t in data.get("titles", []):
                recent_titles.append(t)
            for a in data.get("artists", []):
                recent_artists.append(a)
        except Exception:
            pass  # corrupt file — start fresh, not a big deal

def save_recent():
    """Persist recent_titles to disk."""
    with open(RECENT_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "titles":  list(recent_titles),
            "artists": list(recent_artists),
        }, f)

load_recent()

# =====================
# BAN SYSTEM
# =====================

banned_track_ids = set()

def load_banned():
    """Load banned track IDs from disk into the in-memory set."""
    global banned_track_ids
    if os.path.exists(BANNED_FILE):
        try:
            with open(BANNED_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            banned_track_ids = set(data.get("track_ids", []))
            print(f"  Loaded {len(banned_track_ids)} banned track(s).")
        except Exception:
            banned_track_ids = set()

def save_banned():
    with open(BANNED_FILE, "w", encoding="utf-8") as f:
        json.dump({"track_ids": list(banned_track_ids)}, f, indent=2)

def ban_current_track():
    """
    Ban whatever is currently playing:
    1. Look up the current track via Spotify API
    2. Add its ID to banned_track_ids and save
    3. Apply a weight penalty to the artist
    4. Skip to the next track immediately
    """
    try:
        pb = sp.current_playback()
        if not pb or not pb.get("item"):
            print("  Ban: nothing is currently playing.")
            return

        track    = pb["item"]
        track_id = track["id"]
        name     = track["name"]

        if track_id in banned_track_ids:
            print(f"  Ban: '{name}' is already banned.")
        else:
            banned_track_ids.add(track_id)
            save_banned()
            print(f"  Banned: '{name}' (ID: {track_id})")

        # Weight penalty — banning is a stronger dislike signal than just skipping
        artist = now_playing.get("artist")
        mode   = now_playing.get("mode")
        if artist and mode:
            update_weight(artist, mode, WEIGHT_PUNISH)

        # Skip immediately — play next track in current mode
        if auto_mode == "global":
            play_global_mix(interrupted=True)
        elif current_pool:
            play_from_pool(current_pool, auto_mode, interrupted=True)

    except Exception as e:
        print(f"  Ban failed: {e}")

load_banned()

# =====================
# INSTANCE LOCK
# =====================

def acquire_lock():
    """
    Write our PID to the lock file. If a lock file already exists and the
    PID in it belongs to a running process, exit immediately so we don't
    run two instances at once.
    """
    import sys
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE, "r") as f:
                existing_pid = int(f.read().strip())
            # Check if that process is still alive
            import ctypes
            handle = ctypes.windll.kernel32.OpenProcess(0x0400, False, existing_pid)
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                print(f"DJ is already running (PID {existing_pid}). Exiting.")
                sys.exit(0)
        except Exception:
            pass  # stale or unreadable lock — safe to overwrite

    with open(LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))

def release_lock():
    """Remove the lock file on clean exit."""
    try:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
    except Exception:
        pass

import atexit
acquire_lock()
atexit.register(release_lock)

# =====================
# ARTISTS
# =====================

ARTISTS = {
    "Juice WRLD":    "4MCBfE4596Uoi2O4DtmEMz",
    "XXXTENTACION":  "15UsOTVnJzReFVN1VCnxy4",
    "Ski Mask":      "2rhFzFmezpnW82MNqEKVry",
    "A Boogie":      "31W5EY0aAly4Qieq6OFu6I",
    "tj_beastboy":   "7l8dcABCTyZKrkskt53Z2u",
    "Sierra Kidd":   "0U7ti3mwGrBNlKNE4YlbfT",
    "LE SSERAFIM":   "4SpbR6yFEvexJuaBpgAU5p",
    "BLACKPINK":     "41MozSoPIsD1dJM0CLPjZF",
    "NewJeans":      "6HvZYsbFfjnjFrWF950C9d",
    "K/DA":          "4gOc8TsQed9eqnqJct2c5v",
    "aespa":         "6YVMFz59CuY7ngCxTxjpxE",
    "Ado":           "6mEQK9m2krja6X1cfsAjfl",
    "YOASOBI":       "64tJ2EAv1R6UaZqc4iOCyj",
    "Kenshi Yonezu": "4UK2Lzi6fBfUi9rpDt6cik",
    "BABYMETAL":     "630wzNP2OL7fl4Xl0GnMWq",
    "Aimer":         "0bAsR2unSRpn6BQPEnNlZm",
    "Joji":          "6jJ0s89eD6GaHleKKya26X",
    "The Weeknd":    "1Xyo4u8uXC1ZmMpatF05PJ",
}

AMERICAN_RAP_POOL = ["Juice WRLD", "XXXTENTACION", "Ski Mask", "A Boogie"]
GERMAN_TRAP_POOL  = ["tj_beastboy", "Sierra Kidd"]
KPOP_POOL         = ["LE SSERAFIM", "BLACKPINK", "NewJeans", "K/DA", "aespa"]
JPOP_POOL         = ["Ado", "YOASOBI", "Kenshi Yonezu", "BABYMETAL", "Aimer"]

GLOBAL_POOL = list(ARTISTS.keys())

# Pool lookup by mode name — used by the discovery system to know
# which pool to add a graduated artist into.
MODE_POOLS = {
    "american_rap": AMERICAN_RAP_POOL,
    "german_trap":  GERMAN_TRAP_POOL,
    "kpop":         KPOP_POOL,
    "jpop":         JPOP_POOL,
    "global":       GLOBAL_POOL,
}

# =====================
# MEMORY SYSTEM
# =====================

def default_memory():
    return {
        "version": MEMORY_VERSION,
        "modes": {
            "american_rap":  {},
            "german_trap":   {},
            "kpop":          {},
            "jpop":          {},
            "global":        {},
        }
    }

def upgrade_memory(data):
    if "version" not in data:
        return default_memory()
    if data["version"] < MEMORY_VERSION:
        print("Upgrading DJ memory...")
        data["version"] = MEMORY_VERSION
    return data

def save_memory(mem):
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(mem, f, indent=2)

def load_memory():
    if not os.path.exists(MEMORY_FILE):
        mem = default_memory()
        save_memory(mem)
        return mem
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        data = upgrade_memory(data)
        save_memory(data)
        return data
    except Exception:
        print("Memory corrupted, rebuilding...")
        mem = default_memory()
        save_memory(mem)
        return mem

memory = load_memory()

def get_mode_memory(mode):
    return memory["modes"].setdefault(mode, {})

# =====================
# WEIGHT SYSTEM
# =====================

WEIGHT_BOOST  =  0.15  # reward for playing 80%+ of a track
WEIGHT_PUNISH = -0.10  # penalty for switching away in first 25%
WEIGHT_MAX    =  3.0   # ceiling — stops one artist dominating forever
WEIGHT_MIN    =  0.2   # floor — keeps every artist in the rotation

# Weight threshold that triggers a discovery search for a mode
DISCOVERY_TRIGGER_WEIGHT = 2.0

def update_weight(artist, mode, delta):
    weights = get_mode_memory(mode)
    current = weights.get(artist, 1.0)
    updated = max(WEIGHT_MIN, min(WEIGHT_MAX, current + delta))
    weights[artist] = round(updated, 3)
    save_memory(memory)
    print(f"  weight [{mode}] {artist}: {current:.3f} -> {updated:.3f}")

    # Check if this boost pushed the artist past the discovery threshold
    if delta > 0 and updated >= DISCOVERY_TRIGGER_WEIGHT:
        queue_discovery(artist, mode)

def judge_last_track(interrupted=False):
    """
    Called before every new track starts. Judges whether the previous
    track counts as completed or cut short, and updates weights.

    interrupted=True  — caller knows it was cut short (manual mode switch)
    interrupted=False — use time-based judgement (auto-transition)
    """
    artist = now_playing["artist"]
    mode   = now_playing["mode"]

    if not artist or not mode:
        return

    if interrupted:
        update_weight(artist, mode, WEIGHT_PUNISH)
        return

    duration = now_playing["duration"]
    if duration == 0:
        update_weight(artist, mode, WEIGHT_BOOST * 0.5)
        return

    # Use Spotify-reported progress_ms to judge how far the track got.
    # If progress_ms is still 0, no poll ran before the track ended --
    # we have no reliable data, so make no weight change rather than
    # guessing with wall-clock time which can lie when paused.
    progress = now_playing["progress_ms"]
    if progress == 0:
        return  # no data -- skip judgement entirely
    fraction = progress / duration

    if fraction >= 0.80:
        update_weight(artist, mode, WEIGHT_BOOST)
        if now_playing.get("is_trial"):
            record_trial_play(artist)
    elif fraction < 0.25:
        update_weight(artist, mode, WEIGHT_PUNISH)
    # 25–80%: ambiguous, no change

def set_now_playing(artist, mode, duration_ms, uri=None):
    now_playing["artist"]      = artist
    now_playing["mode"]        = mode
    now_playing["duration"]    = duration_ms
    now_playing["started"]     = time.time()
    now_playing["progress_ms"] = 0  # reset so previous track's value doesn't bleed in
    now_playing["uri"]         = uri

# =====================
# DISCOVERY SYSTEM
# =====================

# Pending discoveries: list of (seed_artist, mode) tuples to process
# at the next track boundary so we never interrupt playback.
_discovery_queue = []

# How many clean tracks a candidate needs to pass the quality bar
DISCOVERY_MIN_TRACKS = 5

# Trial plays needed before an artist graduates to permanent
TRIAL_GRADUATION = 5

def load_discovered():
    """
    Load discovered_artists.json.
    Structure:
    {
      "artist_name": {
        "id":          "spotify_artist_id",
        "mode":        "kpop",
        "trial_plays": 3,
        "graduated":   false
      },
      ...
    }
    """
    if not os.path.exists(DISCOVERED_FILE):
        return {}
    try:
        with open(DISCOVERED_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_discovered(data):
    with open(DISCOVERED_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

discovered_artists = load_discovered()

def all_known_ids():
    """Return a set of all artist IDs we already know about (permanent + discovered)."""
    ids = set(ARTISTS.values())
    for entry in discovered_artists.values():
        ids.add(entry["id"])
    return ids

def inject_discovered_into_pools():
    """
    At startup, add any previously discovered (including graduated) artists
    into the live pools and ARTISTS dict so they work like normal artists.
    """
    for name, entry in discovered_artists.items():
        if name not in ARTISTS:
            ARTISTS[name] = entry["id"]
        mode = entry.get("mode", "global")
        pool = MODE_POOLS.get(mode, GLOBAL_POOL)
        if name not in pool:
            pool.append(name)
        if name not in GLOBAL_POOL:
            GLOBAL_POOL.append(name)
    print(f"  Loaded {len(discovered_artists)} discovered artist(s) into pools.")

def queue_discovery(seed_artist, mode):
    """Queue a discovery search to run at the next track boundary."""
    entry = (seed_artist, mode)
    if entry not in _discovery_queue:
        print(f"  Discovery queued: find artists similar to {seed_artist} for [{mode}]")
        _discovery_queue.append(entry)

def run_pending_discoveries():
    """
    Process one queued discovery per call so we don't stall playback.
    Called at each track boundary before the new track starts.
    """
    if not _discovery_queue:
        return
    seed_artist, mode = _discovery_queue.pop(0)
    try:
        discover_new_artist(seed_artist, mode)
    except Exception as e:
        print(f"  Discovery error: {e}")

def discover_new_artist(seed_artist, mode):
    """
    Use Spotify recommendations seeded from seed_artist to find a new
    artist we don't already know, verify they have enough clean tracks,
    and add them to the discovered pool for trial.
    """
    seed_id = ARTISTS.get(seed_artist) or discovered_artists.get(seed_artist, {}).get("id")
    if not seed_id:
        print(f"  Discovery: can't find ID for {seed_artist}, skipping.")
        return

    known_ids = all_known_ids()

    print(f"  Discovery: searching for artists similar to {seed_artist}...")

    results = sp.recommendations(
        seed_artists=[seed_id],
        limit=50,
        market="DE"
    )

    # Collect candidate artists from recommendation results,
    # skipping anyone we already know.
    candidates = {}
    for track in results.get("tracks", []):
        for artist in track.get("artists", []):
            a_id   = artist["id"]
            a_name = artist["name"]
            if a_id not in known_ids and a_name not in candidates:
                candidates[a_name] = a_id

    if not candidates:
        print(f"  Discovery: no new candidates found for {seed_artist}.")
        return

    # Shuffle candidates so we don't always pick the most popular one
    candidate_list = list(candidates.items())
    random.shuffle(candidate_list)

    for name, artist_id in candidate_list:
        print(f"  Discovery: trying candidate '{name}'...")
        tracks = fetch_artist_tracks_by_id(name, artist_id)
        filtered = select_best_tracks(tracks)

        if len(filtered) < DISCOVERY_MIN_TRACKS:
            print(f"    Only {len(filtered)} clean tracks, skipping.")
            continue

        # Candidate passes — add to discovered pool
        discovered_artists[name] = {
            "id":          artist_id,
            "mode":        mode,
            "trial_plays": 0,
            "graduated":   False,
        }
        save_discovered(discovered_artists)

        # Add to live pools immediately so they can be picked
        ARTISTS[name] = artist_id
        pool = MODE_POOLS.get(mode, GLOBAL_POOL)
        if name not in pool:
            pool.append(name)
        if name not in GLOBAL_POOL:
            GLOBAL_POOL.append(name)

        # Initialise weight at 1.0 in the mode memory
        weights = get_mode_memory(mode)
        weights.setdefault(name, 1.0)
        save_memory(memory)

        print(f"  Discovery: added '{name}' to [{mode}] pool for trial "
              f"({len(filtered)} clean tracks).")
        return

    print(f"  Discovery: all candidates failed quality check for {seed_artist}.")

def record_trial_play(artist):
    """
    Called when a discovered (non-graduated) artist's track plays through 80%+.
    Increments trial_plays and graduates the artist if the threshold is reached.
    """
    if artist not in discovered_artists:
        return
    entry = discovered_artists[artist]
    if entry.get("graduated"):
        return

    entry["trial_plays"] += 1
    print(f"  Trial play {entry['trial_plays']}/{TRIAL_GRADUATION} for '{artist}'")

    if entry["trial_plays"] >= TRIAL_GRADUATION:
        entry["graduated"] = True
        save_discovered(discovered_artists)
        print(f"  '{artist}' has GRADUATED — permanently added to [{entry['mode']}] pool.")
    else:
        save_discovered(discovered_artists)

# =====================
# SPOTIFY CONNECT
# =====================

def connect():
    sp = spotipy.Spotify(
        auth_manager=SpotifyOAuth(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            redirect_uri=REDIRECT_URI,
            scope=SCOPE
        ),
        retries=0,        # disable Spotipy's internal retry/backoff so 429s
        backoff_factor=0, # raise immediately as SpotifyException instead of blocking
    )
    for attempt in range(5):
        devices = sp.devices().get("devices", [])
        if devices:
            return sp, devices[0]["id"]
        print(f"No active Spotify device found (attempt {attempt + 1}/5). "
              f"Open Spotify and start playing something, then wait...")
        time.sleep(10)
    raise RuntimeError(
        "Could not find an active Spotify device after 5 attempts. "
        "Make sure Spotify is open on at least one device."
    )

sp, device_id = connect()

# =====================
# DEBUG MODE
#
# Windows CMD:        set DJ_DEBUG=1 && python spotify_dj.py
# Windows PowerShell: $env:DJ_DEBUG="1"; python spotify_dj.py
# =====================

DEBUG_MODE = os.getenv("DJ_DEBUG", "0") == "1"

def safe_play(**kwargs):
    if DEBUG_MODE:
        print(f"[DEBUG] Would play: {kwargs}")
        return

    global sp, device_id

    try:
        sp.start_playback(device_id=device_id, **kwargs)
    except spotipy.exceptions.SpotifyException as e:
        if e.http_status == 429:
            wait = int(e.headers.get("Retry-After", 30))
            print(f"Rate limit hit in safe_play. Waiting {wait}s...")
            time.sleep(wait)
            sp.start_playback(device_id=device_id, **kwargs)  # retry after wait
            return
        print(f"Playback error ({e.http_status}), reconnecting...")
        sp, device_id = connect()
        sp.start_playback(device_id=device_id, **kwargs)

# =====================
# FILTER FUNCTIONS
# =====================

def is_alternate_version(track):
    track_text = track["name"].lower()
    track_text = track_text.replace("–", "-").replace("_", " ").replace(".", " ")
    track_text = " ".join(track_text.split())

    album_text = track["album"]["name"].lower()
    album_text = album_text.replace("–", "-").replace("_", " ").replace(".", " ")
    album_text = " ".join(album_text.split())

    if LANGUAGE_VERSION_PATTERN.search(track["name"]):
        return True
    for kw in TRACK_KEYWORDS_WORD:
        if re.search(rf"\b{re.escape(kw)}\b", track_text):
            return True
    for kw in TRACK_KEYWORDS_SUBSTR:
        if kw in track_text:
            return True
    for kw in ALBUM_KEYWORDS_WORD:
        if re.search(rf"\b{re.escape(kw)}\b", album_text):
            return True
    for kw in ALBUM_KEYWORDS_SUBSTR:
        if kw in album_text:
            return True
    return False

def normalize_title(title):
    title = title.lower()
    title = re.sub(r"\(.*?\)", "", title)
    title = re.sub(r"\[.*?\]", "", title)
    title = title.split("-")[0]
    return title.strip()

# =====================
# SELECTION LOGIC
# =====================

def weighted_choice(pool, mode):
    weights = get_mode_memory(mode)
    scored  = [(a, weights.get(a, 1.0)) for a in pool]
    total   = sum(w for _, w in scored)
    r       = random.uniform(0, total)
    upto    = 0
    for artist, w in scored:
        upto += w
        if upto >= r:
            return artist
    return pool[-1]

# =====================
# TRACK FETCHING + CACHE
# =====================

artist_cache = {}

def fetch_artist_tracks_by_id(artist_name, artist_id):
    """
    Fetch tracks for any artist given their name and Spotify ID.
    Used by both get_artist_tracks (permanent artists) and the
    discovery system (candidates we don't have in ARTISTS yet).
    Results are cached to disk the same way as permanent artists.
    """
    # Check disk cache first
    entry = track_disk_cache.get(artist_name)
    if entry:
        age_days = (time.time() - entry["fetched_at"]) / 86400
        if age_days < CACHE_TTL_DAYS:
            return entry["tracks"]

    if artist_name in artist_cache:
        return artist_cache[artist_name]

    tracks = []
    seen   = set()

    try:
        response = sp._get(
            f"artists/{artist_id}/albums",
            params={
                "market":         "DE",
                "include_groups": "album,single",
                "limit":          50,
            }
        )
        for album in response.get("items", []):
            if album["id"] in seen:
                continue
            seen.add(album["id"])
            for t in sp.album_tracks(album["id"], limit=50)["items"]:
                t["album"] = album
                t.setdefault("popularity", 0)
                t.setdefault("explicit", False)
                tracks.append(t)

        artist_cache[artist_name] = tracks
        track_disk_cache[artist_name] = {
            "fetched_at": time.time(),
            "tracks":     tracks,
        }
        save_track_cache()
        return tracks

    except Exception as e:
        # Spotipy sometimes raises rate limit errors as SpotifyException with
        # http_status 429, but can also surface them as a plain Exception with
        # the message text below — catch both.
        msg = str(e).lower()
        is_rate_limit = (
            (hasattr(e, "http_status") and e.http_status == 429) or
            "rate" in msg or
            "request limit" in msg or
            "retry will occur" in msg
        )
        if is_rate_limit:
            # Try to extract Retry-After from the message (e.g. "...after: 22347 s")
            import re as _re
            match = _re.search(r"(\d+)\s*s", msg)
            wait = int(match.group(1)) if match else (
                int(e.headers.get("Retry-After", 30)) if hasattr(e, "headers") and e.headers else 30
            )
            print(f"  Rate limit hit fetching {artist_name}. Retry-After: {wait}s.")
            raise RateLimitError(wait)
        print(f"Track fetch failed for {artist_name}: {e}")
        return []

def get_artist_tracks(artist_name):
    """Fetch tracks for a permanent artist (looks up ID from ARTISTS dict)."""
    artist_id = ARTISTS[artist_name]
    try:
        return fetch_artist_tracks_by_id(artist_name, artist_id)
    except RateLimitError:
        uncached_artists.add(artist_name)
        print(f"  {artist_name} marked as uncached due to rate limit.")
        return []

# =====================
# PREWARM CACHE
# =====================

def prewarm_cache(artist_names, delay=2.0):
    """
    Fetch and cache all artists at startup. After this runs,
    get_artist_tracks() makes zero API calls for 7 days.

    If a rate limit is hit mid-prewarm, stops early and marks the
    remaining artists in uncached_artists so they're skipped during
    play rather than causing a hang. They'll be retried on next startup
    once the ban expires.
    """
    print("Pre-warming track cache...")
    for name in artist_names:
        entry = track_disk_cache.get(name)
        if entry:
            age_days = (time.time() - entry["fetched_at"]) / 86400
            if age_days < CACHE_TTL_DAYS:
                print(f"  ✓ {name} (cached)")
                continue
        print(f"  Fetching {name}...")
        try:
            get_artist_tracks(name)
        except RateLimitError as e:
            print(f"  Rate limited during prewarm (Retry-After: {e.retry_after}s).")
            print(f"  Stopping prewarm early — cached artists will play normally.")
            # Mark this artist and all remaining ones as uncached
            remaining = artist_names[artist_names.index(name):]
            for skipped in remaining:
                if not track_disk_cache.get(skipped):
                    uncached_artists.add(skipped)
                    print(f"  ⚠ {skipped} (uncached — will be skipped until fetched)")
            break
        time.sleep(delay + random.uniform(0, 1.5))
    print("Cache ready.\n")

# =====================
# TRACK SELECTION
# =====================

def select_best_tracks(tracks):
    grouped = {}
    for t in tracks:
        if t["album"]["album_type"] in ["compilation", "appears_on"]:
            continue
        if any(w in t["album"]["name"].lower() for w in [
            "tour", "live", "concert", "arena", "dome", "stadium", "festival"
        ]):
            continue
        if is_alternate_version(t):
            continue

        title     = normalize_title(t["name"])
        score_new = (1 if t.get("explicit", False) else 0) * 3 \
                  + (1 if t["album"]["album_type"] == "single" else 0) * 2 \
                  + t.get("popularity", 0)

        if title not in grouped:
            grouped[title] = (t, score_new)
            continue

        _, score_old = grouped[title]
        if score_new > score_old:
            grouped[title] = (t, score_new)

    return [t for t, _ in grouped.values() if not is_alternate_version(t)]

# =====================
# PLAY FUNCTIONS
# =====================

def play_artist(name, mode, pool=None, _depth=0, interrupted=True):
    max_depth = len(pool) if pool else 1
    if _depth >= max_depth:
        print("  All artists in pool exhausted, skipping this cycle.")
        return

    # Before doing anything else, judge the previous track and
    # process any pending discovery searches.
    if _depth == 0:
        judge_last_track(interrupted=interrupted)
        run_pending_discoveries()

    # Skip artists that have no cache and couldn't be fetched at startup
    # due to rate limiting. Try another artist from the pool instead.
    if name in uncached_artists:
        print(f"  Skipping {name} (uncached, rate limited at startup)")
        if pool:
            candidates = [a for a in pool if a != name and a not in uncached_artists]
            if candidates:
                play_artist(random.choice(candidates), mode, pool,
                            _depth=_depth + 1, interrupted=interrupted)
            else:
                print("  All pool artists are uncached, nothing to play.")
        return

    print(f"DJ -> {name}")
    time.sleep(1)

    try:
        tracks = get_artist_tracks(name)
        print(f"  fetched: {len(tracks)}")

        tracks = select_best_tracks(tracks)
        print(f"  after filter: {len(tracks)}")

        # Note: explicit preference is already handled in select_best_tracks()
        # scoring (+3 points). Filtering the whole pool to explicit-only here
        # breaks artists like BLACKPINK who rarely release explicit tracks.

        tracks = [t for t in tracks if normalize_title(t["name"]) not in recent_titles]
        print(f"  after recent filter: {len(tracks)}")

        tracks = [t for t in tracks if t.get("id") not in banned_track_ids]
        print(f"  after ban filter: {len(tracks)}")

        if not tracks:
            raise Exception("No tracks left after filtering")

        chosen = random.choice(tracks)
        recent_titles.append(normalize_title(chosen["name"]))
        recent_artists.append(chosen["artists"][0]["name"])
        save_recent()

        safe_play(uris=[chosen["uri"]])

        # Record what's now playing for weight judgement next time
        set_now_playing(
            artist      = name,
            mode        = mode,
            duration_ms = chosen.get("duration_ms", 0),
            uri         = chosen.get("uri"),
        )

        # If this is a trial artist, check if they earned a trial play
        # (We check fraction >= 0.80 lazily on next track via judge_last_track,
        # so here we just hook into that via a flag in now_playing)
        now_playing["is_trial"] = (
            name in discovered_artists and
            not discovered_artists[name].get("graduated", False)
        )

    except Exception as e:
        print(f"  play_artist failed: {e}")
        if pool:
            candidates = [a for a in pool if a != name]
            if candidates:
                play_artist(random.choice(candidates), mode, pool,
                            _depth=_depth + 1, interrupted=False)
        else:
            time.sleep(2)

def play_from_pool(pool, mode, interrupted=True):
    play_artist(weighted_choice(pool, mode), mode, pool, interrupted=interrupted)

def play_global_mix(interrupted=True):
    artist = weighted_choice(GLOBAL_POOL, "global")
    print(f"Global DJ -> {artist}")
    play_artist(artist, "global", GLOBAL_POOL, interrupted=interrupted)

# =====================
# TRANSITION CHECK
# =====================

# Fraction of track that must have played before the DJ considers it finished.
# Stops a manual pause from being mistaken for a natural track end.
# 0.85 = track must have reached at least 85% before the DJ auto-advances.
TRACK_COMPLETE_THRESHOLD = 0.85

# Seconds after set_now_playing during which URI mismatches are ignored.
# Spotify briefly reports the old/queued track URI in the transition window
# between safe_play being called and the new track actually starting.
URI_GRACE_SECS = 10

def track_finished():
    """
    Returns True only when the current track has played far enough to be
    considered naturally finished (>= TRACK_COMPLETE_THRESHOLD of its duration)
    AND Spotify is no longer playing.

    This cleanly separates a manual pause (progress low, stopped early) from
    a natural track end (progress near 100%, then stopped). A paused track
    will never trigger an auto-advance regardless of how long it sits paused.
    Resuming via keyboard or Spotify app works normally since the DJ never
    interferes with a paused track.
    """
    global _pause_logged
    if not auto_mode:
        return False
    try:
        pb = sp.current_playback()
        if pb and pb.get("is_playing") and pb.get("item"):
            current_uri = pb["item"].get("uri")
            if current_uri != now_playing["uri"]:
                # URI mismatch -- but ignore it during the grace period after
                # set_now_playing, when Spotify briefly reports the old/queued
                # track URI while transitioning to the new track.
                if time.time() - now_playing["started"] < URI_GRACE_SECS:
                    return False  # still in transition window, not a real skip
                # Grace period elapsed -- this is a genuine hardware skip.
                if now_playing["uri"] is not None:
                    judge_last_track(interrupted=True)
                    skipped_to_artist = pb["item"]["artists"][0]["name"]
                    pool = current_pool if auto_mode != "global" else GLOBAL_POOL
                    if pool and skipped_to_artist not in pool:
                        # Spotify skipped to an artist outside the current mode.
                        # Ignore the foreign track and immediately pick a proper one.
                        print(f"  Hardware skip landed on '{skipped_to_artist}' "
                              f"(not in [{auto_mode}]) -- taking back control.")
                        if auto_mode == "global":
                            play_global_mix(interrupted=False)
                        else:
                            play_from_pool(pool, auto_mode, interrupted=False)
                    else:
                        # Skipped to a valid artist -- sync and let it play.
                        print(f"  Hardware skip detected -- syncing to: {pb['item']['name']}")
                        set_now_playing(
                            artist      = skipped_to_artist,
                            mode        = now_playing["mode"],
                            duration_ms = pb["item"]["duration_ms"],
                            uri         = current_uri,
                        )
                return False
            now_playing["progress_ms"] = pb["progress_ms"]
            if _pause_logged:
                print("  Playback resumed.")
                _pause_logged = False
            return False
        # Spotify is not playing -- check if the track got far enough
        duration = now_playing["duration"]
        progress = now_playing["progress_ms"]
        # progress_ms is 0 immediately after a track starts, before the first
        # Spotify poll returns. Skip pause detection until we have real data.
        if duration == 0 or progress == 0:
            return False
        fraction = progress / duration
        if fraction < TRACK_COMPLETE_THRESHOLD:
            if not _pause_logged:
                print(f"  Paused at {progress / 1000:.0f}s / {duration / 1000:.0f}s "
                      f"({fraction * 100:.0f}%) -- waiting for manual resume.")
                _pause_logged = True
            return False
        return True
    except Exception:
        return False

# =====================
# MAIN DJ LOOP
# =====================

def run_dj():
    global current_pool, auto_mode

    while True:
        if os.path.exists(INPUT_FILE):
            try:
                with open(INPUT_FILE) as f:
                    choice = f.read().strip()
                os.remove(INPUT_FILE)
            except Exception:
                time.sleep(1)
                continue

            if choice == "quit":
                print("DJ shutting down via hotkey.")
                import sys
                sys.exit(0)

            elif choice == "ban":
                ban_current_track()

            elif choice == "1":
                auto_mode    = "american_rap"
                current_pool = AMERICAN_RAP_POOL
                play_from_pool(current_pool, "american_rap", interrupted=True)
            elif choice == "2":
                auto_mode    = "german_trap"
                current_pool = GERMAN_TRAP_POOL
                play_from_pool(current_pool, "german_trap", interrupted=True)
            elif choice == "3":
                auto_mode    = "kpop"
                current_pool = KPOP_POOL
                play_from_pool(current_pool, "kpop", interrupted=True)
            elif choice == "4":
                auto_mode    = "jpop"
                current_pool = JPOP_POOL
                play_from_pool(current_pool, "jpop", interrupted=True)
            elif choice == "5":
                auto_mode = "global"
                play_global_mix(interrupted=True)

        # Resume when the current track has naturally finished.
        # track_finished() only returns True when progress reached 85%+
        # before stopping, so manual pauses are never mistaken for track ends.
        if track_finished():
            print("Track finished -- playing next")
            if auto_mode == "global":
                play_global_mix(interrupted=False)
            elif current_pool:
                play_from_pool(current_pool, auto_mode, interrupted=False)

        time.sleep(5)

# =====================
# STARTUP
# =====================

# Load previously discovered artists into pools before prewarming,
# so their tracks get cached alongside permanent artists.
inject_discovered_into_pools()
prewarm_cache(GLOBAL_POOL)

# =====================
# CRASH SAFE RUNNER
# =====================

while True:
    try:
        run_dj()
    except Exception:
        CRASH_LOG = os.path.join(BASE_DIR, "data", "dj_crash.log")
        with open(CRASH_LOG, "a", encoding="utf-8") as log:
            log.write(f"\n\n[{datetime.datetime.now()}]\n")
            log.write(traceback.format_exc())
        time.sleep(5)