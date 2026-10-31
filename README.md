# 🎧 SS Retro Bot
### Multi-Provider Music Discovery and Authorized Full-Track Delivery Platform for Telegram

**SS Retro Bot** is an enterprise-grade Telegram music discovery and authorized audio delivery bot built with **Python 3.11+**, **python-telegram-bot 22.x**, **aiosqlite**, **httpx**, and **mutagen**.

Inspired by the conversational simplicity and high-performance search of reference bots like `@vkmusic_bot` and `@songsuperzxbot`, SS Retro Bot offers a comprehensive discovery engine across global, Indian regional, and indie catalogues while strictly adhering to legitimate audio licensing, Creative Commons, and Public Domain standards.

---

## 🌟 Key Capabilities

- **Multi-Provider Concurrent Search Engine**: Searches across active providers simultaneously with `asyncio`, applying token-based relevance ranking, duplicate detection, and intelligent caching.
- **Universal Multi-Genre & Multi-Language Coverage**:
  - **Indian Music**: Hindi, Bollywood, Odia, Bengali, Punjabi, Telugu, Tamil, Malayalam, Kannada, Bhajans & Devotional.
  - **International**: Pop, Rock, Rap, Hip-Hop, Synthwave, Electronic, Classical, Instrumental, Remixes, Indie.
- **Authorized Full Audio Delivery**: Delivers complete, genuine full-length MP3 files directly via Telegram's `sendAudio` with artwork, title, artist, album, and duration tags.
- **Telegram `file_id` Cache**: Delivered audio tracks are automatically indexed by Telegram `file_id`, enabling subsequent deliveries with zero server bandwidth and zero latency.
- **Mutagen Audio Validation**: Inspects incoming audio streams for duration, bitrate, and corrupted headers, rejecting empty or oversized files (>49MB Telegram bot upload limit).
- **Conversational & Inline Mode**: Type `@SSRetroBot <query>` anywhere on Telegram for instant track cards and sharing, or talk directly in private chat.
- **User Library & Favorites**: Personal SQLite-backed favorites (`/favorites`), recent history (`/recent`), search history, and customizable results per page (`/settings`).

---

## 📂 Project Architecture

```
D:\SS Retro Bot/
│
├── bot.py                     # Application entrypoint & Telegram runner
├── config.py                  # Environment settings & secret redaction
├── database.py                # Asynchronous SQLite storage (aiosqlite)
├── requirements.txt           # Core project dependencies
├── .env                       # Local environment secrets (excluded from Git)
├── .env.example               # Template environment configuration
├── .gitignore                 # Excludes .venv, .env, DB, logs, and downloads
├── README.md                  # Comprehensive platform documentation
│
├── handlers/                  # Telegram Bot API Handlers
│   ├── __init__.py
│   ├── commands.py            # /start, /help, /top, /recent, /favorites, /settings
│   ├── music.py               # /search, /song, /artist, /album & plain text search
│   ├── callbacks.py           # Interactive inline buttons, pagination & audio download
│   ├── inline.py              # Inline search (@SSRetroBot query)
│   └── admin.py               # Admin diagnostics & telemetry (/admin)
│
├── services/                  # Business Logic & Infrastructure
│   ├── __init__.py
│   ├── search_engine.py       # Concurrent search aggregator, deduplication & ranking
│   ├── downloader.py          # Secure streaming audio downloader & ID3 tagger
│   ├── audio_validator.py     # Mutagen stream & integrity verification
│   ├── telegram_delivery.py   # sendAudio delivery with Telegram file_id cache
│   └── providers/             # Modular Music Providers
│       ├── __init__.py
│       ├── base.py            # Abstract BaseMusicProvider & UnifiedTrack model
│       ├── registry.py        # Central ProviderRegistry
│       ├── internet_archive.py# Public domain, live, and regional open audio (Full Audio)
│       ├── itunes.py          # Apple iTunes global catalog (Metadata Discovery)
│       ├── jamendo.py         # Jamendo Creative Commons music catalog (Full Audio)
│       ├── musicbrainz.py     # Open music encyclopedia metadata
│       ├── spotify.py         # Spotify Web API metadata adapter
│       └── massivemusic.py    # 7digital / MassiveMusic commercial B2B adapter
│
├── utils/                     # Formatting & Security Utilities
│   ├── __init__.py
│   ├── formatting.py          # HTML escaping, duration & size formatters
│   ├── logging_config.py      # Structured logs with secret redaction filter
│   └── security.py            # Filename sanitization, query cleaning & rate limiting
│
├── tests/                     # Automated Test Suites (34+ automated tests)
│   ├── conftest.py            # Pytest configuration & sys.path setup
│   ├── test_config.py         # Config validation & secret redaction tests
│   ├── test_database.py       # SQLite CRUD, caching, & metrics tests
│   ├── test_providers.py      # Provider normalization & mock tests
│   ├── test_search_engine.py  # Deduplication, pagination, & timeout tests
│   ├── test_audio_validator.py# Mutagen validation & size limit tests
│   ├── test_downloader.py     # License check, streaming & cleanup tests
│   ├── test_telegram_delivery.py# Caching & sendAudio delivery tests
│   ├── test_handlers.py       # Command parsing & formatting tests
│   ├── test_live_apis.py      # Real-world public API network tests
│   └── test_real_workflows.py # 15-step end-to-end user workflow test
│
└── data/                      # Persistent storage & temp files
    ├── bot.db                 # SQLite production database
    └── temp_downloads/        # Ephemeral audio download storage (auto-cleaned)
```

---

## 🔌 Music Providers & Licensing Matrix

| Provider | Type | Full Audio Delivery | Redelivery Permission | Required Credentials | Status |
| :--- | :--- | :---: | :---: | :--- | :--- |
| **Internet Archive** | Open Audio / Archive | **YES** | Public Domain / CC | None (Open Public API) | **Active** |
| **Apple iTunes** | Global Catalogue | No (30s sample) | Metadata Only | None (Open Public API) | **Active** |
| **MusicBrainz** | Open Encyclopedia | No | Metadata Only | Compliant User-Agent | **Active** |
| **Jamendo** | CC Independent Music | **YES** | Creative Commons | `JAMENDO_CLIENT_ID` | **Active when key provided** |
| **Spotify Web API** | Commercial Streaming | No | Metadata Only | `SPOTIFY_CLIENT_ID` & `SECRET` | Optional |
| **MassiveMusic / 7digital** | Commercial B2B | Licensed | Commercial Partner Tier | `MASSIVEMUSIC_API_KEY` & `SECRET`| Commercial Partner |

### Audio Delivery Policy
- When a track has authorized full audio (Jamendo, Archive.org open audio), the bot sends the complete file via Telegram's native `sendAudio`.
- For commercial streaming providers (iTunes, Spotify, MusicBrainz), the bot displays complete track metadata, HD album art, and direct links to listen on authorized platforms, clearly explaining that full streaming is licensed through official music services.
- **Never**: DRM bypass, stream ripping, preview padding, or unauthorized conversions.

---

## 🚀 Installation & Local Setup

### 1. Prerequisites
- **Python 3.11+** (Windows 11, Linux, or macOS)
- A Telegram Bot Token from [@BotFather](https://t.me/BotFather)

### 2. Activate Virtual Environment
Inside the project root:
```powershell
# Windows PowerShell
.\.venv\Scripts\Activate.ps1

# Linux / macOS
source .venv/bin/activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure Environment Variables
Copy `.env.example` to `.env`:
```powershell
Copy-Item .env.example .env
```
Open `.env` and fill in your credentials:
```ini
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrSTUvwxYZ
BOT_USERNAME=SSRetroBot
ADMIN_USER_IDS=123456789

# Optional: Jamendo Client ID (Free from https://developer.jamendo.com)
JAMENDO_CLIENT_ID=your_jamendo_client_id_here
```

### 5. Run Startup Self-Check
Verify your configuration, database, and providers without launching polling:
```bash
python bot.py --check
```

### 6. Start the Bot
```bash
python bot.py
```

---

## 🧪 Automated Testing

SS Retro Bot includes a comprehensive automated test suite covering unit tests, mocked providers, live external APIs, and full end-to-end user workflows:

```powershell
# Run all automated tests
pytest tests -v
```

All 34 test cases test:
1. `test_config.py`: Token verification, secret redaction, path resolution.
2. `test_database.py`: Asynchronous SQLite storage, search history, favorites, settings, cache.
3. `test_providers.py`: Provider registration, normalization, unconfigured provider isolation.
4. `test_search_engine.py`: Parallel search aggregation, deduplication, relevance scoring, pagination.
5. `test_audio_validator.py`: Mutagen stream inspection, duration verification, 49MB file limit enforcement.
6. `test_downloader.py`: License verification, stream abort on size limits, ID3 tagging, file cleanup.
7. `test_telegram_delivery.py`: Telegram `file_id` cache hit delivery, new download delivery, error handling.
8. `test_handlers.py`: Command execution, rate limiting, HTML escaping.
9. `test_live_apis.py`: Actual live HTTP queries to iTunes, Internet Archive, and MusicBrainz.
10. `test_real_workflows.py`: Automated validation of the 15-step end-to-end user journey.

---

## 📋 Available Commands & Interactions

| Command | Description |
| :--- | :--- |
| `/start` | Displays interactive welcome card with quick genre & regional discovery buttons |
| `/help` | Complete user manual and audio delivery licensing policy |
| `/search <query>` | Multi-provider search with interactive pagination (`/search Tum Hi Ho`) |
| `/song <title>` | Dedicated song title search shortcut |
| `/artist <name>` | Search tracks by artist (`/artist Arijit Singh`) |
| `/album <name>` | Search albums across providers |
| `/top` | Explore trending and top featured music |
| `/recent` | View your recently selected and delivered audio tracks |
| `/favorites` | Browse your saved music collection |
| `/settings` | Customize results per page (3, 5, 8) and preferred language filters |
| `/admin` | Administrative system diagnostics, telemetry counters, and provider health |

### Conversational Search
Users do not need to type `/search` every time. Sending any plain text message (e.g. `Tum Hi Ho`, `Move`, `old Odia songs`) automatically triggers a multi-provider search!

### Inline Search
Type `@SSRetroBot <query>` in any Telegram chat to search and share track cards or audio files directly into group or private chats.

---

## 🔒 Security & Reliability

- **Secret Redaction Filter**: Any tokens, client secrets, or credentials accidentally formatted into log messages are intercepted and replaced with `[REDACTED_SECRET]`.
- **Sliding-Window Rate Limiter**: Protects against rapid-fire spam, API hammering, and duplicate requests.
- **Safe Filename Sanitization**: Strips dangerous cross-platform characters and prevents directory traversal attacks.
- **Streaming Size Enforcement**: Downloads stream chunk-by-chunk and abort immediately if content exceeds the 49MB limit, protecting disk space.
- **Zero Orphaned Files**: Temporary audio files are guaranteed to be cleaned up in `finally` blocks immediately after transmission.
