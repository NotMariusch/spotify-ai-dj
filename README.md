# Spotify AI DJ

A local automated Spotify DJ that runs as a background service on Windows. It selects artists from curated pools, filters tracks, learns your listening preferences over time, and discovers new artists similar to ones you already enjoy.

## Project Structure

```
SpotifyDJ/
├── src/
│   └── spotify_dj.py           # Main DJ script
├── scripts/
│   └── dj_hotkey.ahk           # AutoHotkey hotkey script
├── data/                        # Runtime data — all gitignored except .gitkeep
│   ├── track_cache.json         # Cached artist tracks (7 day TTL)
│   ├── dj_memory.json           # Artist weights per mode
│   ├── recent_tracks.json       # Recently played titles (persists across restarts)
│   ├── discovered_artists.json  # Trial and graduated discovered artists
│   ├── dj.lock                  # Instance lock file (prevents duplicate processes)
│   ├── dj_input.txt             # Hotkey input (written by AHK, read by DJ)
│   ├── banned_tracks.json       # Permanently banned track IDs
│   └── dj_crash.log             # Crash and exit log
├── start_dj.bat                 # Launch with visible console (for testing)
├── start_dj_hidden.vbs          # Launch hidden in background (normal use)
├── .env                         # Spotify API credentials — never committed
├── .gitignore
├── README.md
└── requirements.txt
```

## Features

- **5 modes** switchable via hotkeys at any time
- **Smart track filtering** — removes remixes, live versions, sped-up/slowed, language alternate versions, concert recordings, and other alternates automatically
- **Weight system** — tracks play-through rate per artist per mode and adjusts selection probability over time. Artists you consistently listen through get picked more often; artists you skip get picked less
- **Artist discovery** — when an artist's weight crosses a threshold, the DJ searches Spotify recommendations for similar artists, quality-checks their catalog, and adds passing candidates to the pool for a trial period. Artists that earn enough play-throughs are permanently saved
- **Continuous playback** — plays songs back to back automatically, picking a new weighted-random artist from the active mode’s pool after each track. Pausing in Spotify stops the DJ without auto-resuming — resume manually via your keyboard, Spotify app, or switch modes to start a new track
- **Persistent track cache** — fetches each artist's catalog once and caches it for 7 days, keeping API calls near zero during normal use
- **Persistent recent history** — remembers recently played tracks across restarts to avoid immediate repeats
- **Hardware skip detection** — detects when you skip a track via your keyboard and applies the correct weight penalty. If the skip lands on an artist outside the current mode, the DJ immediately takes back control and picks a proper track
- **Duplicate instance protection** — lock file prevents two instances running simultaneously
- **Crash recovery** — automatically restarts after unhandled exceptions and logs all crashes and exits to `data/dj_crash.log`

## Setup

**1. Clone the repo:**
```
git clone https://github.com/NotMariusch/spotify-ai-dj
cd spotify-ai-dj
```

**2. Create the data folder placeholder** (if it doesn't exist after cloning):
```
type nul > data\.gitkeep
```

**3. Install dependencies:**
```
pip install -r requirements.txt
```

**4. Create a `.env` file in the project root:**
```
SPOTIFY_CLIENT_ID=your_client_id
SPOTIFY_CLIENT_SECRET=your_client_secret
SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/callback
```

**5. Register a Spotify app:**
- Go to [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard)
- Create an app and add `http://127.0.0.1:8888/callback` as a redirect URI
- Copy the Client ID and Secret into your `.env`

**6. Install AutoHotkey v2** and run `scripts/dj_hotkey.ahk` (can be set to run on startup)

**7. Open Spotify and start playing anything on your target device, then run:**
```
start_dj.bat
```
The first run fetches and caches all artists (~1 minute with rate-limit delays). After that, subsequent startups load from cache instantly.

> **Note:** Spotify enforces strict API rate limits. If you hit a limit during the first fetch (startup will print a warning and stop early), the DJ will still run using whichever artists were cached successfully. The missing artists will be retried automatically on the next startup once the ban window expires (can be several hours). Do not delete `track_cache.json` between runs unless necessary.


**8. For normal background use:**
```
start_dj_hidden.vbs
```
This launches the DJ with no visible window. The VBS also prevents duplicate instances from starting.

## Hotkeys

Requires AutoHotkey v2 running `scripts/dj_hotkey.ahk`. Right Ctrl is remapped to F13 to avoid conflicts in games.

| Hotkey | Mode | Artists |
|--------|------|---------|
| F13 + 1 | American Rap | Juice WRLD, XXXTENTACION, Ski Mask, A Boogie |
| F13 + 2 | German Trap | tj_beastboy, Sierra Kidd |
| F13 + 3 | K-Pop | LE SSERAFIM, BLACKPINK, NewJeans, K/DA, aespa |
| F13 + 4 | J-Pop | Ado, YOASOBI, Kenshi Yonezu, BABYMETAL, Aimer |
| F13 + 5 | Global | All artists, weighted random |
| F13 + 6 | Ban current track permanently | |
| F13 + 7 | Quit DJ cleanly | |

## How the Weight System Works

Each artist starts at weight `1.0` per mode. At every track boundary the DJ judges the previous track:

| Condition | Weight change |
|-----------|--------------|
| Played 80%+ naturally | +0.15 |
| Skipped, banned, or switched mode in first 25% | -0.10 |
| Switched between 25–80% | No change |

Weights are clamped between `0.2` (floor) and `3.0` (ceiling). `weighted_choice()` uses these as probabilities so higher-weight artists get picked more often without ever fully excluding lower-weight ones.

**To reset all weights:** delete `data/dj_memory.json`

## How Artist Discovery Works

When an artist's weight reaches `2.0` in any mode, a discovery search is queued for that mode. At the next track boundary the DJ calls Spotify's recommendations API seeded with that artist, filters candidates to those with at least 5 clean (non-alternate) tracks available in DE, and adds the first passing candidate to the pool as a trial artist at weight `1.0`.

Trial artists need 5 play-throughs of 80%+ to graduate. Graduated artists are permanently saved to `data/discovered_artists.json` and reloaded into pools on every restart.

## Data Files Reference

| File | Purpose | Reset by deleting? |
|------|---------|-------------------|
| `track_cache.json` | Cached artist track lists | Yes — refetched on next startup |
| `dj_memory.json` | Artist weights per mode | Yes — all weights reset to 1.0. Also delete if you rename modes, as old mode keys become orphaned |
| `recent_tracks.json` | Recently played titles | Yes — repeat protection clears |
| `discovered_artists.json` | Discovered/graduated artists | Yes — discovery history lost |
| `banned_tracks.json` | Permanently banned track IDs | Yes — all bans cleared |
| `dj_crash.log` | Crash and exit log | Yes — safe to delete anytime |