"""Upload-Ziele: TikTok (API oder Browser mit deinem Account), YouTube Shorts, Instagram Reels."""

from __future__ import annotations

from ..config import AppConfig
from ..oauth import TokenStore
from .base import UploadError, UploadResult, Uploader
from .instagram import InstagramUploader
from .tiktok import TikTokUploader
from .tiktok_browser import TikTokBrowserUploader
from .youtube import YouTubeUploader

PLATFORMS = ("tiktok", "youtube", "instagram")


def make_uploader(cfg: AppConfig, platform: str) -> Uploader:
    if platform == "tiktok":
        if cfg.tiktok_method == "browser":
            return TikTokBrowserUploader(
                cfg.tiktok,
                profile_dir=cfg.data_dir / "browser" / "tiktok",
                marker_path=cfg.tokens_dir / "tiktok_browser.json",
                debug_dir=cfg.data_dir / "logs" / "tiktok_screenshots",
            )
        return TikTokUploader(cfg.tiktok, TokenStore(cfg.tokens_dir / "tiktok.json"))
    if platform == "youtube":
        return YouTubeUploader(cfg.youtube, cfg.tokens_dir / "youtube.json")
    if platform == "instagram":
        return InstagramUploader(cfg.instagram, token_store=TokenStore(cfg.tokens_dir / "instagram.json"))
    raise ValueError(f"Unbekannte Plattform: {platform}")


def enabled_uploaders(cfg: AppConfig) -> list[Uploader]:
    return [make_uploader(cfg, p) for p in PLATFORMS if getattr(cfg, p).enabled]


__all__ = ["PLATFORMS", "UploadError", "UploadResult", "Uploader", "enabled_uploaders", "make_uploader"]
