"""
Audio Effects Studio Engine for SS Retro Bot.
Applies studio acoustic modifications using FFmpeg filters:
- Slowed + Reverb (aesthetic atmospheric sound)
- Deep Bass Boost (+8dB sub-bass enhancement)
- 8D Spatial Surround Panning (binaural audio moving sound between ears)
- Nightcore (high-energy speed up + pitch boost)
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Optional

from mutagen.id3 import ID3, TALB, TIT2, TPE1, ID3NoHeaderError

from config import config
from services.providers.base import UnifiedTrack

logger = logging.getLogger(__name__)

EFFECT_PRESETS = {
    "slowed": {
        "name": "Slowed + Reverb",
        "emoji": "🌙",
        "filter": "asetrate=44100*0.85,aresample=44100,aecho=0.8:0.88:60:0.4",
        "tag_suffix": "[Slowed + Reverb]",
        "desc": "Aesthetic slowed tempo with atmospheric studio reverb",
    },
    "bass": {
        "name": "Bass Boosted",
        "emoji": "🔊",
        "filter": "bass=g=8:f=110:w=0.6,volume=1.1",
        "tag_suffix": "[Bass Boosted +8dB]",
        "desc": "Punchy sub-bass enhancement for headphones & cars",
    },
    "8d": {
        "name": "8D Surround",
        "emoji": "🎧",
        "filter": "apulsator=hz=0.125:amount=1",
        "tag_suffix": "[8D Surround Audio]",
        "desc": "360° spatial panning sound moving ear-to-ear",
    },
    "nightcore": {
        "name": "Nightcore",
        "emoji": "⚡",
        "filter": "asetrate=44100*1.25,aresample=44100",
        "tag_suffix": "[Nightcore / Speed Up]",
        "desc": "Fast tempo +125% party vibe with pitch boost",
    },
}


class AudioEffectsEngine:
    """Processes audio files with studio-grade FFmpeg filters."""

    def __init__(self, download_dir: Path | None = None):
        self.download_dir = download_dir or config.temp_download_dir
        self.ffmpeg_bin = shutil.which("ffmpeg")

    def _sync_process_effect(
        self, input_file: Path, effect_type: str, track: UnifiedTrack
    ) -> tuple[Optional[Path], Optional[str]]:
        """Synchronously execute FFmpeg audio filter."""
        if not self.ffmpeg_bin:
            return None, "FFmpeg is not installed or available on the system."

        preset = EFFECT_PRESETS.get(effect_type)
        if not preset:
            return None, f"Unknown audio effect preset: {effect_type}"

        if not input_file.exists() or input_file.stat().st_size == 0:
            return None, "Original audio file is missing or empty."

        self.download_dir.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex[:12]
        output_file = self.download_dir / f"fx_{effect_type}_{token}.mp3"

        cmd = [
            self.ffmpeg_bin,
            "-y",
            "-i", str(input_file),
            "-vn",
            "-af", preset["filter"],
            "-c:a", "libmp3lame",
            "-b:a", "320k",
            str(output_file),
        ]

        try:
            res = subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=120,
            )
            if res.returncode != 0:
                logger.error("FFmpeg effect error: %s", res.stderr)
                return None, f"Audio processing failed: {res.stderr[:100]}"

            if output_file.exists() and output_file.stat().st_size > 0:
                # Update ID3 tags
                self._tag_effect_mp3(output_file, track, preset["tag_suffix"])
                return output_file, None

            return None, "Audio processing did not produce a valid output file."
        except subprocess.TimeoutExpired:
            if output_file.exists():
                output_file.unlink(missing_ok=True)
            return None, "Audio processing timed out after 120s."
        except Exception as e:
            logger.error("Audio effect exception: %s", e)
            if output_file.exists():
                output_file.unlink(missing_ok=True)
            return None, f"Audio processing error: {e}"

    def _tag_effect_mp3(self, file_path: Path, track: UnifiedTrack, suffix: str) -> None:
        """Inject updated ID3 metadata reflecting the applied modification."""
        try:
            try:
                tags = ID3(str(file_path))
            except ID3NoHeaderError:
                tags = ID3()

            title = f"{track.title} {suffix}".strip()
            tags["TIT2"] = TIT2(encoding=3, text=title)
            if track.artist:
                tags["TPE1"] = TPE1(encoding=3, text=track.artist)
            if track.album:
                tags["TALB"] = TALB(encoding=3, text=f"{track.album} {suffix}")

            tags.save(str(file_path))
        except Exception as e:
            logger.warning("Could not write effect ID3 tags to %s: %s", file_path.name, e)

    async def apply_effect(
        self, input_file: Path, effect_type: str, track: UnifiedTrack
    ) -> tuple[Optional[Path], Optional[str]]:
        """Asynchronously apply an acoustic filter to an audio file."""
        return await asyncio.to_thread(self._sync_process_effect, input_file, effect_type, track)

    @staticmethod
    def cleanup(file_path: Path | None) -> None:
        """Safely delete a temporary modified audio file."""
        if file_path and file_path.exists():
            try:
                file_path.unlink()
                logger.debug("Cleaned up effect audio file: %s", file_path.name)
            except Exception as e:
                logger.warning("Failed to clean up effect file %s: %s", file_path, e)


# Global singleton instance
audio_effects_engine = AudioEffectsEngine()
