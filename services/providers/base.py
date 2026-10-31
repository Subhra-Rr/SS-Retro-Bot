"""
Base provider abstraction and unified track data structures.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional


@dataclass
class UnifiedTrack:
    """Standardized representation of a music track across all providers."""

    id: str
    provider_name: str
    title: str
    artist: str
    album: Optional[str] = None
    duration: Optional[int] = None  # in seconds
    release_date: Optional[str] = None
    cover_url: Optional[str] = None
    audio_url: Optional[str] = None
    has_full_audio: bool = False
    license_type: Optional[str] = None
    allow_redistribution: bool = False
    genre: Optional[str] = None
    language: Optional[str] = None
    external_url: Optional[str] = None
    telegram_file_id: Optional[str] = None
    file_size: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert track instance into a serializable dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> UnifiedTrack:
        """Construct track instance from dictionary."""
        valid_keys = cls.__dataclass_fields__.keys()
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)


class BaseMusicProvider(ABC):
    """Abstract base class for all legitimate music search and delivery providers."""

    name: str = "BaseProvider"
    can_deliver_full_audio: bool = False

    @abstractmethod
    def is_configured(self) -> bool:
        """Check if required API credentials or service prerequisites are satisfied."""
        pass

    @abstractmethod
    async def search(
        self, query: str, limit: int = 10, offset: int = 0
    ) -> List[UnifiedTrack]:
        """
        Execute search query and return normalized track list.
        Must handle errors gracefully and never raise unhandled exceptions to the caller.
        """
        pass

    @abstractmethod
    async def get_track(self, track_id: str) -> Optional[UnifiedTrack]:
        """Fetch full details for a single track by its ID."""
        pass

    async def get_top_tracks(self, limit: int = 10) -> List[UnifiedTrack]:
        """Fetch featured, trending, or top tracks from provider catalog."""
        return []
