"""TikTok direkt mit deinem normalen Account: Upload über TikTok Studio im Browser.

Kein Entwickler-Konto und keine App-Prüfung nötig – Details zum Ablauf und zum Schutz vor
Doppel-Posts stehen in browser_base.py.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Iterator

from .browser_base import (CONFIRM_TIMEOUT_SECONDS, SESSION_PAUSE_SECONDS, BrowserUploader,  # noqa: F401
                           browser_caption)

UPLOAD_PATH = "/tiktokstudio/upload?from=webapp"

DEFAULT_SELECTORS: dict = {
    "file_input": "input[type='file']",
    "caption": "div[contenteditable='true']",
    "post_button": "button[data-e2e='post_video_button']",
    "post_button_texts": ["Post", "Posten", "Veröffentlichen", "Publish"],
    "confirm_texts": ["Post now", "Jetzt posten", "Jetzt veröffentlichen", "Trotzdem posten"],
    "captcha": ("#captcha-verify-image, #captcha_container, .captcha-verify-container, "
                "div[class*='captcha_verify'], iframe[src*='captcha']"),
    "success_url_parts": ["/tiktokstudio/content"],
    "success_texts": ["Your video has been uploaded", "Your video is being uploaded", "Manage your posts",
                      "Dein Video wurde", "Dein Video wird", "Beiträge verwalten"],
    "error_texts": ["Couldn't upload", "Upload failed", "Hochladen fehlgeschlagen",
                    "Video konnte nicht hochgeladen werden"],
}


class TikTokBrowserUploader(BrowserUploader):
    name = "tiktok"
    label = "TikTok"
    description = "Browser-Modus (dein TikTok-Account)"
    BASE_URL = "https://www.tiktok.com"
    LOGIN_PATH = "/login"
    SESSION_COOKIES = ("sessionid", "sessionid_ss", "sid_tt")
    DEFAULT_SELECTORS = DEFAULT_SELECTORS
    LOGIN_HINT = ("Melde dich dort ganz normal bei TikTok an –\n"
                  "am einfachsten per QR-Code: TikTok-App → Profil → ☰ → QR-Code scannen.")

    def _post_steps(self, page, ctx, video_path: Path, caption: str, deadline: float) -> Iterator[str]:
        yield "Upload-Seite öffnen"
        page.goto(self.base_url + UPLOAD_PATH, wait_until="domcontentloaded", timeout=90_000)
        self._ensure_session(ctx, page)
        self._check_captcha(page)

        yield "Video auswählen"
        file_input = page.locator(self.sel["file_input"]).first
        file_input.wait_for(state="attached", timeout=90_000)
        file_input.set_input_files(str(video_path))

        yield "Beschreibung eintragen"
        editor = page.locator(self.sel["caption"]).first
        editor.wait_for(state="visible", timeout=max(deadline - time.monotonic(), 1) * 1000)
        self._fill_caption(page, editor, caption)

        yield "auf Upload warten"
        button = self._find_button(page, deadline, self.sel["post_button_texts"], css=self.sel["post_button"],
                                   what="„Posten“-Button")
        self._wait_until_enabled(page, button, deadline)

        yield "Posten"
        button.scroll_into_view_if_needed()
        button.click()

