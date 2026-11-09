"""Instagram direkt mit deinem normalen Account: Reels über instagram.com im Browser posten.

Kein Business-Konto und keine Meta-Entwickler-App nötig. Ablauf wie von Hand:
Erstellen → Beitrag → Video wählen → (Hinweis „als Reel geteilt“ → OK) → Zuschnitt „Original“ →
Weiter → Weiter → Bildunterschrift → Teilen. Schutz vor Doppel-Posts: siehe browser_base.py.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterator

from .browser_base import BrowserUploader, _short

log = logging.getLogger(__name__)

DEFAULT_SELECTORS: dict = {
    "create_button": ("svg[aria-label='New post'], svg[aria-label='Neuer Beitrag'], "
                      "svg[aria-label='Create'], svg[aria-label='Erstellen']"),
    "create_texts": ["Create", "Erstellen", "New post", "Neuer Beitrag"],
    "create_post_texts": ["Post", "Beitrag"],
    "file_input": "input[type='file']",
    "reels_notice_texts": ["OK"],
    "crop_button": "svg[aria-label='Select crop'], svg[aria-label='Zuschnitt auswählen']",
    "crop_original_texts": ["Original", "9:16"],
    "next_texts": ["Next", "Weiter"],
    "caption": "div[contenteditable='true'][role='textbox'], div[contenteditable='true']",
    "share_texts": ["Share", "Teilen"],
    "dismiss_texts": ["Not Now", "Not now", "Jetzt nicht"],
    "captcha": "iframe[src*='captcha'], iframe[src*='recaptcha']",
    "challenge_url_parts": ["/challenge/", "/two_factor"],
    "success_url_parts": [],
    "success_selector": "img[alt='Animated checkmark'], img[alt='Animiertes Häkchen']",
    "success_texts": ["Your reel has been shared", "Your post has been shared", "Reel shared", "Post shared",
                      "Dein Reel wurde geteilt", "Dein Beitrag wurde geteilt", "Reel geteilt", "Beitrag geteilt"],
    "confirm_texts": [],
    "error_texts": ["couldn't be shared", "konnte nicht geteilt werden"],
}


class InstagramBrowserUploader(BrowserUploader):
    name = "instagram"
    label = "Instagram"
    description = "Browser-Modus (dein Instagram-Account)"
    BASE_URL = "https://www.instagram.com"
    LOGIN_PATH = "/accounts/login"
    SESSION_COOKIES = ("sessionid",)
    DEFAULT_SELECTORS = DEFAULT_SELECTORS
    MAX_HASHTAGS = 30
    CONFIRM_TIMEOUT = 300           # Instagram verarbeitet das Video erst nach „Teilen“ – Fenster offen lassen
    LOGIN_HINT = ("Melde dich dort ganz normal bei Instagram an\n"
                  "(Benutzername und Passwort, ggf. Bestätigungscode).")

    def _post_steps(self, page, ctx, video_path: Path, caption: str, deadline: float) -> Iterator[str]:
        yield "Instagram öffnen"
        page.goto(self.base_url + "/", wait_until="domcontentloaded", timeout=90_000)
        self._ensure_session(ctx, page)
        self._check_captcha(page)
        self._dismiss_popups(page)

        yield "„Erstellen“ öffnen"
        create = self._find_button(page, deadline, self.sel["create_texts"], css=self.sel["create_button"],
                                   what="„Erstellen“-Button")
        create.click()
        # Untermenü „Beitrag“ anklicken, falls es erscheint. Nicht nach dem Datei-Feld entscheiden:
        # das kann schon versteckt in der Seite stecken, bevor das Fenster offen ist.
        self._click_if_visible(page, self.sel["create_post_texts"], wait_seconds=4)

        yield "Video auswählen"
        file_input = page.locator(self.sel["file_input"])
        file_input.first.wait_for(state="attached", timeout=60_000)
        file_input.first.set_input_files(str(video_path))
        self._click_if_visible(page, self.sel["reels_notice_texts"], wait_seconds=6)       # „als Reel geteilt“

        yield "Zuschnitt wählen"
        self._choose_original_crop(page)

        yield "Weiter"
        caption_box = page.locator(self.sel["caption"])
        for _ in range(4):
            if self._visible(caption_box):
                break
            self._find_button(page, deadline, self.sel["next_texts"], what="„Weiter“-Button").click()
            page.wait_for_timeout(1500)

        yield "Bildunterschrift eintragen"
        caption_box.first.wait_for(state="visible", timeout=60_000)
        self._fill_caption(page, caption_box.first, caption)

        yield "Teilen"
        share = self._find_button(page, deadline, self.sel["share_texts"], what="„Teilen“-Button")
        share.click()

    # ----------------------------------------------------------- Einzelschritte
    def _dismiss_popups(self, page) -> None:
        """„Benachrichtigungen aktivieren?“ / „Login-Infos speichern?“ wegklicken."""
        for _ in range(3):
            if not self._click_if_visible(page, self.sel["dismiss_texts"], wait_seconds=2):
                return
            page.wait_for_timeout(500)

    def _choose_original_crop(self, page) -> None:
        """Instagram schneidet Videos sonst evtl. quadratisch zu – Hochformat behalten."""
        try:
            crop = page.locator(self.sel["crop_button"])
            if not crop.count():
                return
            crop.first.click()
            page.wait_for_timeout(500)
            if not self._click_if_visible(page, self.sel["crop_original_texts"], wait_seconds=3):
                log.info("Instagram: Zuschnitt-Auswahl nicht gefunden – Standard wird genutzt")
            page.wait_for_timeout(300)
        except Exception as exc:
            log.info("Instagram: Zuschnitt nicht änderbar (%s) – Standard wird genutzt", _short(exc))
