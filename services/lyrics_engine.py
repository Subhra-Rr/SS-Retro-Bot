"""
Universal Song Lyrics Engine for SS Retro Bot.
Provides instant synchronized and plain song lyrics across Indian (Hindi, Bollywood,
Odia, Punjabi, Bengali, Tamil, Telugu), Retro Classics, and Global International hits.
Uses a multi-tier fallback cascade:
1. Genius Multi-Search & Container Extractor
2. LRCLIB Open Lyrics API (Plain & Synchronized LRC)
3. Lyrics.ovh REST API
"""

from __future__ import annotations

import html
import logging
import re
from typing import Optional, Tuple
from urllib.parse import quote

import httpx

from config import config
from utils.formatting import escape_html

logger = logging.getLogger(__name__)

GENIUS_SEARCH_URL = "https://genius.com/api/search/multi"
LRCLIB_SEARCH_URL = "https://lrclib.net/api/search"
LYRICS_OVH_URL = "https://api.lyrics.ovh/v1"

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
}


def smart_clean_track(title: str, artist: str | None = None) -> Tuple[str, str]:
    """
    Clean YouTube titles, channel names, movie noise, and release tags to uncover
    the pure canonical song title and performer for maximum lyrics match rate.
    """
    if not title:
        return "", ""

    t = title

    # Strip bracketed content (e.g. [Official Video], (From "Movie"), (Original))
    t = re.sub(r"\(.*?official.*?\)", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\[.*?official.*?\]", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\(.*?video.*?\)", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\[.*?video.*?\]", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\(.*?audio.*?\)", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\(.*?lyric.*?\)", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\[.*?lyric.*?\]", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\(.*?original.*?\)", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\(.*?soundtrack.*?\)", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\(.*?remaster.*?\)", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\(from [\"'].*?[\"']\)", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\b(ft|feat|featuring)\b.*", "", t, flags=re.IGNORECASE)
    t = re.sub(
        r"\b(full video|full song|audio song|official video|lyrical video|remix|slowed|reverb|4k|hd|mp3)\b.*",
        "",
        t,
        flags=re.IGNORECASE,
    )

    # If title has " - ", extract clean primary component
    core_title = t
    extracted_artist = artist or ""
    if " - " in t:
        parts = t.split(" - ")
        if len(parts) >= 2:
            p0 = parts[0].strip()
            p1 = parts[1].strip()
            # If p0 looks like artist and p1 like title or vice versa
            if not extracted_artist:
                extracted_artist = p0
                core_title = p1
            else:
                core_title = p0
        else:
            core_title = parts[0].strip()

    # Filter out YouTube channel labels acting as fake artist
    channel_blacklist = {
        "vevo", "t-series", "zee music", "sony music", "saregama",
        "tips", "yrf", "bollywood", "classics", "records", "music",
        "official", "studios", "entertainment", "channel",
    }
    if any(b in extracted_artist.lower() for b in channel_blacklist):
        extracted_artist = ""

    # Clean whitespace and trailing punctuation
    clean_title = re.sub(r"\s+", " ", core_title).strip(" -|.,_#")
    clean_art = re.sub(r"\s+", " ", extracted_artist).strip(" -|.,_#")

    return clean_title, clean_art


class LyricsEngine:
    """Coordinates multi-source lyrics discovery and beautiful formatting."""

    def __init__(self) -> None:
        self._cache: dict[str, str] = {}

    async def get_lyrics(self, title: str, artist: str | None = None) -> Optional[str]:
        """
        Fetch lyrics using smart cascading multi-source strategy:
        1. In-memory cache
        2. Genius Multi-Search & HTML Scraper
        3. LRCLIB Search (Plain & Synced LRC)
        4. Lyrics.ovh REST API
        """
        clean_title, clean_artist = smart_clean_track(title, artist)
        if not clean_title:
            return None

        cache_key = f"{clean_title.lower()}|{clean_artist.lower()}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        # Candidate queries in order of specificity
        queries = []
        if clean_artist:
            queries.append(f"{clean_title} {clean_artist}")
        queries.append(clean_title)

        timeout = httpx.Timeout(config.search_timeout_seconds, connect=4.0)

        async with httpx.AsyncClient(timeout=timeout, headers=BROWSER_HEADERS, follow_redirects=True) as client:
            for q in queries:
                # Tier 1: Genius Search & Extraction (Best for Hindi/Bollywood, Odia, Western)
                lyrics = await self._fetch_from_genius(client, q)
                if lyrics:
                    formatted = self._format_lyrics(clean_title, clean_artist or "Artist", lyrics, source="Genius")
                    self._cache[cache_key] = formatted
                    return formatted

                # Tier 2: LRCLIB (Open repository for western & viral tracks)
                lyrics = await self._fetch_from_lrclib(client, q)
                if lyrics:
                    formatted = self._format_lyrics(clean_title, clean_artist or "Artist", lyrics, source="LRCLIB")
                    self._cache[cache_key] = formatted
                    return formatted

            # Tier 3: Lyrics.ovh (Fallback if artist is known)
            if clean_artist:
                lyrics = await self._fetch_from_lyrics_ovh(client, clean_artist, clean_title)
                if lyrics:
                    formatted = self._format_lyrics(clean_title, clean_artist, lyrics, source="Lyrics.ovh")
                    self._cache[cache_key] = formatted
                    return formatted

        return None

    async def _fetch_from_genius(self, client: httpx.AsyncClient, query: str) -> Optional[str]:
        """Query Genius multi-search and extract lyrics from the song page."""
        try:
            params = {"per_page": "5", "q": query}
            res = await client.get(GENIUS_SEARCH_URL, params=params)
            if res.status_code != 200:
                return None

            data = res.json()
            sections = data.get("response", {}).get("sections", [])
            song_url = None
            for s in sections:
                if s.get("type") == "song":
                    for hit in s.get("hits", [])[:3]:
                        res_obj = hit.get("result", {})
                        u = res_obj.get("url")
                        if u and "lyrics" in u:
                            song_url = u
                            break
                if song_url:
                    break

            if not song_url:
                return None

            # Fetch page HTML
            page_res = await client.get(song_url)
            if page_res.status_code != 200:
                return None

            page_html = page_res.text
            # Extract content from Genius data-lyrics-container divs
            matches = re.findall(r'<div[^>]*data-lyrics-container="true"[^>]*>(.*?)</div>', page_html, re.DOTALL)
            if matches:
                raw = "\n".join(matches)
                raw = re.sub(r"<br\s*/?>", "\n", raw)
                clean = re.sub(r"<[^>]+>", "", raw)
                clean = html.unescape(clean).strip()
                if len(clean) > 40:
                    return clean
        except Exception as e:
            logger.debug("Genius lyrics fetch notice for '%s': %s", query, e)
        return None

    async def _fetch_from_lrclib(self, client: httpx.AsyncClient, query: str) -> Optional[str]:
        """Query LRCLIB open repository, parsing plainLyrics or converting syncedLyrics."""
        try:
            params = {"q": query}
            res = await client.get(LRCLIB_SEARCH_URL, params=params)
            if res.status_code != 200:
                return None

            data = res.json()
            if isinstance(data, list) and data:
                for item in data[:5]:
                    plain = item.get("plainLyrics")
                    if plain and len(plain.strip()) > 40:
                        return plain.strip()

                    # Convert synchronized LRC timestamps [mm:ss.xx] to plain text
                    synced = item.get("syncedLyrics")
                    if synced and len(synced.strip()) > 40:
                        converted = re.sub(r"\[\d+:\d+\.\d+\]", "", synced).strip()
                        if len(converted) > 40:
                            return converted
        except Exception as e:
            logger.debug("LRCLIB lyrics fetch notice for '%s': %s", query, e)
        return None

    async def _fetch_from_lyrics_ovh(self, client: httpx.AsyncClient, artist: str, title: str) -> Optional[str]:
        """Query Lyrics.ovh REST API."""
        try:
            url = f"{LYRICS_OVH_URL}/{quote(artist)}/{quote(title)}"
            res = await client.get(url)
            if res.status_code == 200:
                data = res.json()
                lyrics = data.get("lyrics")
                if lyrics and len(lyrics.strip()) > 40:
                    return lyrics.strip()
        except Exception as e:
            logger.debug("Lyrics.ovh fetch notice for '%s': %s", title, e)
        return None

    def _format_lyrics(self, title: str, artist: str, lyrics: str, source: str = "Studio") -> str:
        """Format lyrics into an elegant Telegram HTML view with length truncation safeguard."""
        # Strip excessive blank lines
        clean_lyrics = re.sub(r"\n{3,}", "\n\n", lyrics.strip())

        # Telegram message length limit is 4096 chars
        if len(clean_lyrics) > 3400:
            clean_lyrics = clean_lyrics[:3350] + "\n\n... [Lyrics truncated for length]"

        header = (
            f"📜 <b>Lyrics — {escape_html(title)}</b>\n"
            f"👤 <i>{escape_html(artist)}</i>\n\n"
            f"<blockquote>{escape_html(clean_lyrics)}</blockquote>\n\n"
            f"🎧 <i>Verified by SS Retro Bot Lyrics Engine • [{source}]</i>"
        )
        return header

    def get_genius_url(self, title: str, artist: str | None = None) -> str:
        """
        Build a high-precision, direct search link to https://genius.com for any track.
        Dynamically strips movie labels, junk release tags, and video artifacts so Genius
        immediately opens the exact verified song page.
        """
        if not title:
            return "https://genius.com"

        t = title
        # Strip bracketed content (e.g. (Lyrical), [Official Video], (Original))
        t = re.sub(r"\(.*?\)", "", t)
        t = re.sub(r"\[.*?\]", "", t)
        t = re.sub(r"\{.*?\}", "", t)

        # If title has " - " or " | ", isolate core song title
        if " - " in t:
            t = t.split(" - ")[0].strip()
        if " | " in t:
            t = t.split(" | ")[0].strip()

        # Remove video/audio filler words (e.g. "Nit Khair Manga Song" -> "Nit Khair Manga")
        t = re.sub(
            r"\b(song|video|audio|lyric|lyrics|lyrical|official|full|original|mp3|remix|slowed|reverb)\b",
            "",
            t,
            flags=re.IGNORECASE,
        )
        clean_title = re.sub(r"\s+", " ", t).strip(" -|.,_#")

        clean_artist = artist or ""
        # If artist has multiple pipes (e.g. RAID | Ajay Devgn | Ileana D'Cruz | Rahat Fateh Ali Khan),
        # the singer is typically the last element
        if " | " in clean_artist:
            clean_artist = clean_artist.split(" | ")[-1].strip()
        elif " - " in clean_artist:
            clean_artist = clean_artist.split(" - ")[0].strip()

        channel_blacklist = {
            "vevo", "t-series", "zee music", "sony music", "saregama",
            "tips", "yrf", "bollywood", "classics", "records", "music",
            "official", "studios", "entertainment", "channel",
        }
        if any(b in clean_artist.lower() for b in channel_blacklist):
            clean_artist = ""

        q = f"{clean_title} {clean_artist}".strip() if clean_artist else clean_title
        from urllib.parse import quote_plus
        return f"https://genius.com/search?q={quote_plus(q)}"


# Global singleton instance
lyrics_engine = LyricsEngine()
