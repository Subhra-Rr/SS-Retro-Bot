"""
Provider Registry and discovery container.
Manages provider lifecycles, configuration statuses, and capability queries.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from services.providers.base import BaseMusicProvider
from services.providers.internet_archive import InternetArchiveProvider
from services.providers.itunes import ITunesProvider
from services.providers.jamendo import JamendoProvider
from services.providers.massivemusic import MassiveMusicProvider
from services.providers.musicbrainz import MusicBrainzProvider
from services.providers.spotify import SpotifyProvider

logger = logging.getLogger(__name__)


class ProviderRegistry:
    """Central registry for music discovery and delivery providers."""

    def __init__(self):
        self._providers: Dict[str, BaseMusicProvider] = {}
        self._register_default_providers()

    def _register_default_providers(self) -> None:
        """Instantiate and register all known provider adapters."""
        defaults = [
            InternetArchiveProvider(),
            ITunesProvider(),
            JamendoProvider(),
            SpotifyProvider(),
            MusicBrainzProvider(),
            MassiveMusicProvider(),
        ]
        for provider in defaults:
            self.register(provider)

    def register(self, provider: BaseMusicProvider) -> None:
        """Register a provider instance."""
        self._providers[provider.name.lower()] = provider
        configured_status = "READY" if provider.is_configured() else "DORMANT (unconfigured)"
        logger.info("Registered provider [%s] - Status: %s", provider.name, configured_status)

    def get_provider(self, name: str) -> Optional[BaseMusicProvider]:
        """Look up provider by case-insensitive name."""
        return self._providers.get(name.lower())

    def get_all(self) -> List[BaseMusicProvider]:
        """Get all registered providers."""
        return list(self._providers.values())

    def get_configured(self) -> List[BaseMusicProvider]:
        """Get list of active, properly configured providers."""
        return [p for p in self._providers.values() if p.is_configured()]

    def get_audio_providers(self) -> List[BaseMusicProvider]:
        """Get list of configured providers capable of authorized full audio delivery."""
        return [p for p in self.get_configured() if p.can_deliver_full_audio]


# Global singleton registry
registry = ProviderRegistry()
