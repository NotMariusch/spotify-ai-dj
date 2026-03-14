import os
import time
import json
import random
import traceback
import datetime
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv


# =====================
# SPOTIFY CONFIG
# =====================



load_dotenv()

CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI")

SCOPE = "user-modify-playback-state user-read-playback-state"


# =====================
# SETTINGS
# =====================

AUTO_INTERVAL = 900
SMOOTH_THRESHOLD = 8000

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MEMORY_FILE = os.path.join(BASE_DIR, "data", "dj_memory.json")
INPUT_FILE = os.path.join(BASE_DIR, "data", "dj_input.txt")
MEMORY_VERSION = 1

current_pool = None
auto_mode = None
last_auto_switch = time.time()


# =====================
# ARTISTS
# =====================

ARTISTS = {
    # US RAP
    "Juice WRLD": "4MCBfE4596Uoi2O4DtmEMz",
    "XXXTENTACION": "15UsOTVnJzReFVN1VCnxy4",
    "Ski Mask": "2rhFzFmezpnW82MNqEKVry",
    "A Boogie": "31W5EY0aAly4Qieq6OFu6I",

    # GERMAN
    "tj_beastboy": "7l8dcABCTyZKrkskt53Z2u",
    "Sierra Kidd": "0U7ti3mwGrBNlKNE4YlbfT",

    # KPOP
    "LE SSERAFIM": "4SpbR6yFEvexJuaBpgAU5p",
    "BLACKPINK": "41MozSoPIsD1dJM0CLPjZF",
    "NewJeans": "6HvZYsbFfjnjFrWF950C9d",
    "K/DA": "4gOc8TsQed9eqnqJct2c5v",
    "aespa": "6YVMFz59CuY7ngCxTxjpxE",

    # ANIME / JAPAN
    "Ado": "6mEQK9m2krja6X1cfsAjfl",
    "YOASOBI": "64tJ2EAv1R6UaZqc4iOCyj",
    "Kenshi Yonezu": "4UK2Lzi6fBfUi9rpDt6cik",
    "BABYMETAL": "630wzNP2OL7fl4Xl0GnMWq",
    "LiSA": "0blbVefuxOGltDBa00dspv",

    # GLOBAL SUPPORT
    "Joji": "6jJ0s89eD6GaHleKKya26X",
    "The Weeknd": "1Xyo4u8uXC1ZmMpatF05PJ"
}


HYPE_POOL = [
    "Juice WRLD",
    "XXXTENTACION",
    "Ski Mask",
    "A Boogie"
]

TJ_POOL = [
    "tj_beastboy",
    "Sierra Kidd"
]

KPOP_POOL = [
    "LE SSERAFIM",
    "BLACKPINK",
    "NewJeans",
    "K/DA",
    "aespa"
]

ANIME_POOL = [
    "Ado",
    "YOASOBI",
    "Kenshi Yonezu",
    "BABYMETAL",
    "LiSA"
]

GLOBAL_POOL = list(ARTISTS.keys())


# =====================
# MEMORY SYSTEM
# =====================

def default_memory():
    return {
        "version": MEMORY_VERSION,
        "modes": {
            "hype": {},
            "tj": {},
            "night": {},
            "anime": {},
            "global": {}
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
    with open(MEMORY_FILE,"w",encoding="utf-8") as f:
        json.dump(mem,f,indent=2)


def load_memory():

    if not os.path.exists(MEMORY_FILE):
        mem = default_memory()
        save_memory(mem)
        return mem

    try:
        with open(MEMORY_FILE,"r",encoding="utf-8") as f:
            data=json.load(f)

        data=upgrade_memory(data)
        save_memory(data)
        return data

    except Exception:
        print("Memory corrupted → rebuilding")
        mem=default_memory()
        save_memory(mem)
        return mem


memory = load_memory()


def get_mode_memory(mode):
    return memory["modes"].setdefault(mode,{})


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
        )
    )

    device_id = sp.devices()["devices"][0]["id"]
    return sp, device_id


sp, device_id = connect()


def safe_play(**kwargs):
    global sp, device_id

    try:
        sp.start_playback(device_id=device_id, **kwargs)
    except:
        sp, device_id = connect()
        sp.start_playback(device_id=device_id, **kwargs)


# =====================
# SELECTION LOGIC
# =====================

def weighted_choice(pool, mode):

    weights = get_mode_memory(mode)

    total = 0
    scored = []

    for artist in pool:
        w = weights.get(artist,1.0)
        scored.append((artist,w))
        total += w

    r=random.uniform(0,total)
    upto=0

    for artist,w in scored:
        if upto+w>=r:
            return artist
        upto+=w


# =====================
# PLAY FUNCTIONS
# =====================

def play_artist(name,mode):

    artist_id = ARTISTS[name]

    print(f"DJ → {name}")

    safe_play(context_uri=f"spotify:artist:{artist_id}")


def play_from_pool(pool,mode):

    artist = weighted_choice(pool,mode)
    play_artist(artist,mode)


def play_global_mix():

    mode="global"

    artist = weighted_choice(GLOBAL_POOL,mode)
    artist_id = ARTISTS[artist]

    print(f"Global DJ → {artist}")

    try:
        recs = sp.recommendations(
            seed_artists=[artist_id],
            limit=40
        )

        tracks=[t["uri"] for t in recs["tracks"]]

        if not tracks:
            raise Exception()

        safe_play(uris=tracks)

    except:
        print("Fallback → Artist Radio")
        play_artist(artist,mode)


# =====================
# TRANSITION CHECK
# =====================

def ready_for_transition():

    try:
        pb = sp.current_playback()

        if not pb or not pb["is_playing"]:
            return False

        remaining = pb["item"]["duration_ms"] - pb["progress_ms"]

        return remaining < SMOOTH_THRESHOLD

    except Exception:
        # Spotify temporarily unreachable
        return False


# =====================
# MAIN DJ LOOP
# =====================

def run_dj():

    global current_pool,auto_mode,last_auto_switch

    while True:

        if os.path.exists(INPUT_FILE):

            with open(INPUT_FILE) as f:
                choice = f.read().strip()

            os.remove(INPUT_FILE)

            last_auto_switch=time.time()

            if choice=="1":
                auto_mode="hype"
                current_pool=HYPE_POOL
                play_from_pool(current_pool,"hype")

            elif choice=="2":
                auto_mode="tj"
                current_pool=TJ_POOL
                play_from_pool(current_pool,"tj")

            elif choice=="3":
                auto_mode="kpop"
                current_pool=KPOP_POOL
                play_from_pool(current_pool,"kpop")

            elif choice=="4":
                auto_mode="anime"
                current_pool=ANIME_POOL
                play_from_pool(current_pool,"anime")

            elif choice=="5":
                auto_mode="global"
                play_global_mix()

        if time.time()-last_auto_switch>AUTO_INTERVAL:

            if ready_for_transition():

                print("Smooth DJ transition")

                if auto_mode=="global":
                    play_global_mix()

                elif current_pool:
                    play_from_pool(current_pool,auto_mode)

                last_auto_switch=time.time()

        time.sleep(5)


# =====================
# CRASH SAFE RUNNER
# =====================

while True:
    try:
        run_dj()

    except Exception:

        CRASH_LOG = os.path.join(BASE_DIR, "data", "dj_crash.log")

        with open(CRASH_LOG,"a",encoding="utf-8") as log:
            log.write(
                f"\n\n[{datetime.datetime.now()}]\n"
            )
            log.write(traceback.format_exc())

        time.sleep(5)