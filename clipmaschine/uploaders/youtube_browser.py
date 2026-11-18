"""YouTube Shorts direkt mit deinem normalen Google-Konto: Upload über YouTube Studio im Browser.

Keine Google-Cloud-Einrichtung und kein API-Audit nötig: Die Videos sind sofort öffentlich
(über die API bleiben Uploads ungeprüfter Projekte privat). Ablauf wie von Hand:
youtube.com/upload → Video wählen → Titel → Beschreibung → „Nicht für Kinder“ → Weiter ×3 →
Sichtbarkeit „Öffentlich“ → Upload abwarten → Veröffentlichen. Schutz vor Doppel-Posts: siehe browser_base.py.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Iterator

from ..models import Clip, PostMetadata
from .base import UploadError, UploadResult
from .browser_base import SETUP_PAUSE_SECONDS, BrowserUploader, _short
from .youtube import youtube_title

log = logging.getLogger(__name__)

LIMIT_PAUSE_SECONDS = 12 * 3600       # Studio-Tageslimit: rollierend, YouTube nennt keinen Zeitpunkt

DEFAULT_SELECTORS: dict = {
    "file_input": "input[type='file']",
    "create_button": "#create-icon, ytcp-button#create-icon",
    "create_texts": ["Create", "Erstellen"],
    "upload_menu_texts": ["Upload videos", "Videos hochladen"],
    "title": "#title-textarea #textbox",
    "description": "#description-textarea #textbox",
    "not_for_kids": "tp-yt-paper-radio-button[name='VIDEO_MADE_FOR_KIDS_NOT_MFK'], "
                    "[name='VIDEO_MADE_FOR_KIDS_NOT_MFK']",
    "for_kids": "tp-yt-paper-radio-button[name='VIDEO_MADE_FOR_KIDS_MFK'], [name='VIDEO_MADE_FOR_KIDS_MFK']",
    "next_button": "#next-button",
    "next_texts": ["Next", "Weiter"],
    "visibility": {
        "public": "tp-yt-paper-radio-button[name='PUBLIC'], [name='PUBLIC']",
        "unlisted": "tp-yt-paper-radio-button[name='UNLISTED'], [name='UNLISTED']",
        "private": "tp-yt-paper-radio-button[name='PRIVATE'], [name='PRIVATE']",
    },
    "done_button": "#done-button",
    "done_texts": ["Publish", "Veröffentlichen", "Save", "Speichern"],
    "progress_label": "ytcp-video-upload-progress .progress-label, .progress-label",
    "uploading_texts": ["Uploading", "Wird hochgeladen", "Upload läuft"],
    "uploaded_texts": ["Upload complete", "Uploaded", "Checks complete", "Checking", "Processing",
                       "abgeschlossen", "Hochgeladen", "Überprüfung", "Verarbeitung", "Wird verarbeitet"],
    "video_link": "a[href*='youtu.be/'], a[href*='youtube.com/shorts/'], a[href*='youtube.com/watch']",
    "create_channel_texts": ["Create channel", "Kanal erstellen", "Create a channel", "Erstelle einen Kanal"],
    "dismiss_texts": ["Got it", "Verstanden", "Not now", "Jetzt nicht", "Später", "Close", "Schließen"],
    "limit_texts": ["Daily upload limit reached", "upload limit", "Upload-Limit", "Uploadlimit",
                    "Tägliches Limit"],
    "error_texts": ["Upload failed", "Upload fehlgeschlagen", "Processing abandoned", "Verarbeitung abgebrochen",
                    "Video is too long", "Video ist zu lang"],
    "login_blocked_texts": ["browser or app may not be secure", "App ist möglicherweise nicht sicher",
                            "Couldn't sign you in", "Anmeldung nicht möglich"],
    "captcha": "iframe[src*='recaptcha']",
    "challenge_url_parts": ["/challenge/"],
    "success_url_parts": [],
    "success_selector": "ytcp-video-share-dialog, ytcp-uploads-still-processing-dialog",
    "success_texts": ["Video published", "Video veröffentlicht", "Video processing", "Video wird verarbeitet",
                      "Video saved", "Video gespeichert"],
    "confirm_texts": [],
}

_VIDEO_ID_RE = re.compile(r"(?:youtu\.be/|/shorts/|[?&]v=)([A-Za-z0-9_-]{6,})")


def youtube_description(meta: PostMetadata, clip: Clip, limit: int = 4900) -> str:
    """Beschreibung wie beim API-Upload; YouTube lehnt „<“ und „>“ ab."""
    text = "\n\n".join(filter(None, [
        meta.description,
        f"Original-Clip: {clip.url}" if clip.url else "",
        meta.hashtag_text(),
    ]))
    return text.replace("<", "").replace(">", "")[:limit]


def video_id_from_url(url: str) -> str:
    match = _VIDEO_ID_RE.search(url or "")
    return match.group(1) if match else ""


class YouTubeBrowserUploader(BrowserUploader):
    name = "youtube"
    label = "YouTube"
    description = "Browser-Modus (dein Google-Konto, YouTube Studio)"
    BASE_URL = "https://www.youtube.com"
    LOGIN_PATH = "/upload"                       # leitet ohne Anmeldung zur Google-Anmeldung weiter
    LOGIN_URL_PARTS = ("accounts.google.com", "ServiceLogin", "/signin")
    SESSION_COOKIES = ("LOGIN_INFO", "SID", "__Secure-3PSID")
    # Google verweigert die Anmeldung in Browsern, die sich als ferngesteuert ausweisen
    LAUNCH_OPTIONS = {"ignore_default_args": ["--enable-automation"],
                      "args": ["--disable-blink-features=AutomationControlled"]}
    DEFAULT_SELECTORS = DEFAULT_SELECTORS
    CONFIRM_TIMEOUT = 120
    DIRECT_DIALOG_TIMEOUT_MS = 20_000            # so lange auf das direkt geöffnete Upload-Fenster warten
    LOGIN_HINT = ("Melde dich dort ganz normal mit deinem Google-Konto an\n"
                  "(das Konto, zu dem dein YouTube-Kanal gehört).")
    LOGIN_BLOCKED_HINT = ("Bitte Google Chrome installieren und erneut versuchen – oder YouTube über die "
                          "Google-Freigabe verbinden (README, Abschnitt „YouTube Shorts per API“).")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._meta: PostMetadata | None = None
        self._clip: Clip | None = None
        self._video_url = ""

    def upload(self, video_path: Path, meta: PostMetadata, clip: Clip) -> UploadResult:
        self._meta, self._clip, self._video_url = meta, clip, ""
        return super().upload(video_path, meta, clip)

    def _post_steps(self, page, ctx, video_path: Path, caption: str, deadline: float) -> Iterator[str]:
        meta, clip = self._meta, self._clip
        if meta is None or clip is None:
            raise UploadError("interner Fehler: Upload ohne Metadaten", retryable=False)

        yield "YouTube Studio öffnen"
        page.goto(self.base_url + "/upload", wait_until="domcontentloaded", timeout=90_000)
        self._ensure_session(ctx, page)
        self._check_captcha(page)
        self._check_channel(page)

        yield "Upload-Fenster öffnen"
        file_input = self._open_upload_dialog(page)

        yield "Video auswählen"
        file_input.set_input_files(str(video_path))

        yield "Titel eintragen"
        title_box = page.locator(self.sel["title"])
        self._wait_visible(page, title_box, deadline, "Titelfeld")
        self._fill_caption(page, title_box.first, youtube_title(meta, self.cfg.add_shorts_tag))

        yield "Beschreibung eintragen"
        description = youtube_description(meta, clip)
        if description:
            box = page.locator(self.sel["description"])
            self._wait_visible(page, box, deadline, "Beschreibungsfeld")
            self._fill_caption(page, box.first, description)

        yield "Zielgruppe wählen"
        kids = page.locator(self.sel["for_kids" if self.cfg.made_for_kids else "not_for_kids"])
        self._wait_visible(page, kids, deadline, "Auswahl „Nicht speziell für Kinder“")
        kids.first.scroll_into_view_if_needed()
        kids.first.click()

        yield "Weiter"
        visibility = page.locator(self.sel["visibility"].get(self.cfg.privacy_status, self.sel["visibility"]["public"]))
        for _ in range(5):
            if self._visible(visibility):
                break
            self._remember_link(page)
            self._next_button(page, deadline).click()
            page.wait_for_timeout(1200)
        self._wait_visible(page, visibility, deadline, "Sichtbarkeits-Auswahl")

        yield "Sichtbarkeit wählen"
        visibility.first.click()

        yield "Upload abwarten"
        done = self._wait_for_upload(page, deadline)
        self._remember_link(page)

        yield "Veröffentlichen"
        done.click()

    def _confirm_post(self, page, PlaywrightError) -> UploadResult:
        result = super()._confirm_post(page, PlaywrightError)
        try:
            self._remember_link(page)
        except PlaywrightError:
            pass
        if self._video_url:
            video_id = video_id_from_url(self._video_url)
            result.remote_id = video_id
            result.url = f"https://youtube.com/shorts/{video_id}" if video_id else self._video_url
        return result

    # ----------------------------------------------------------- Einzelschritte
    def _check_channel(self, page) -> None:
        if self._visible_text(page, self.sel["create_channel_texts"]):
            raise UploadError("Dein Google-Konto hat noch keinen YouTube-Kanal – bitte einmal auf youtube.com einen "
                              "Kanal erstellen, danach geht es von selbst weiter", pause_seconds=SETUP_PAUSE_SECONDS)

    def _open_upload_dialog(self, page):
        """youtube.com/upload öffnet das Upload-Fenster direkt; sonst über „Erstellen → Videos hochladen“."""
        file_input = page.locator(self.sel["file_input"])
        try:
            file_input.first.wait_for(state="attached", timeout=self.DIRECT_DIALOG_TIMEOUT_MS)
            return file_input.first
        except Exception as exc:
            log.info("YouTube: Upload-Fenster nicht direkt offen (%s) – öffne es über „Erstellen“", _short(exc))
        for _ in range(3):
            if not self._click_if_visible(page, self.sel["dismiss_texts"], wait_seconds=1):
                break
        create = page.locator(self.sel["create_button"])
        if self._visible(create):
            create.first.click()
        else:
            self._click_if_visible(page, self.sel["create_texts"], wait_seconds=5)
        self._click_if_visible(page, self.sel["upload_menu_texts"], wait_seconds=5)
        file_input.first.wait_for(state="attached", timeout=30_000)
        return file_input.first

    def _wait_visible(self, page, locator, deadline: float, what: str) -> None:
        while time.monotonic() < deadline:
            if self._visible(locator):
                return
            self._check_limit_and_errors(page)
            self._check_captcha(page)
            page.wait_for_timeout(500)
        raise UploadError(f"{what} nicht gefunden (hat YouTube die Seite geändert? → youtube.browser_selectors)")

    def _next_button(self, page, deadline: float):
        button = page.locator(self.sel["next_button"])
        if self._visible(button):
            return button.first
        return self._find_button(page, deadline, self.sel["next_texts"], what="„Weiter“-Button")

    def _check_limit_and_errors(self, page) -> None:
        limit = self._visible_text(page, self.sel["limit_texts"])
        if limit:
            raise UploadError(f"YouTube-Tageslimit für Uploads erreicht („{limit}“) – YouTube pausiert 12 Stunden, "
                              "die anderen Plattformen laufen weiter", pause_seconds=LIMIT_PAUSE_SECONDS)
        error = self._visible_text(page, self.sel["error_texts"])
        if error:
            raise UploadError(f"YouTube meldet beim Hochladen: „{error}“")

    def _upload_finished(self, page) -> bool | None:
        """True/False laut Fortschrittsanzeige; None, wenn es keine Anzeige gibt."""
        label = page.locator(self.sel["progress_label"])
        if not label.count():
            return None
        text = " ".join(label.first.inner_text().split()).lower()
        if not text:
            return None
        if any(t.lower() in text for t in self.sel["uploading_texts"]):
            return False
        return any(t.lower() in text for t in self.sel["uploaded_texts"])

    def _wait_for_upload(self, page, deadline: float):
        """Erst veröffentlichen, wenn die Datei komplett oben ist – sonst bricht der Upload beim Schließen ab."""
        done_css = page.locator(self.sel["done_button"])
        while time.monotonic() < deadline:
            self._check_limit_and_errors(page)
            self._check_captcha(page)
            finished = self._upload_finished(page)
            button = done_css.first if done_css.count() else None
            if button is None:
                by_text = self._button_by_texts(page, self.sel["done_texts"])
                button = by_text.last if by_text.count() else None
            enabled = (button is not None and button.is_enabled() and button.get_attribute("disabled") is None
                       and button.get_attribute("aria-disabled") != "true")
            if enabled and finished is not False:
                return button
            page.wait_for_timeout(1000)
        raise UploadError("Upload bei YouTube nicht rechtzeitig fertig geworden")

    def _remember_link(self, page) -> None:
        if self._video_url:
            return
        links = page.locator(self.sel["video_link"])
        for i in range(min(links.count(), 5)):
            href = links.nth(i).get_attribute("href") or ""
            if video_id_from_url(href):
                self._video_url = href
                return
