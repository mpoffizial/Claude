"""TikTok direkt mit deinem normalen Account: Upload über TikTok Studio im Browser.

Kein Entwickler-Konto und keine App-Prüfung nötig. Du meldest dich einmal selbst im
Browserfenster an (Passwort/QR-Code bleiben im Browser – die Clipmaschine sieht sie nie).
Die Sitzung liegt in einem eigenen Browserprofil; danach lädt die Maschine jedes Video so
hoch, wie du es von Hand in TikTok Studio tun würdest.

Sicherheitsnetz gegen Doppel-Posts: Fehler *vor* dem Klick auf „Posten“ führen zu einem
neuen Versuch. Ist der Klick erfolgt, wird nie automatisch erneut gepostet – bleibt die
Bestätigung aus, gilt das Video als eingereicht (mit Screenshot zur Kontrolle).
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

from ..config import TikTokConfig
from ..models import Clip, PostMetadata, iso, utcnow
from .base import UploadError, UploadResult, Uploader, truncate_utf16

log = logging.getLogger(__name__)

BASE_URL = "https://www.tiktok.com"
UPLOAD_PATH = "/tiktokstudio/upload?from=webapp"
LOGIN_PATH = "/login"
SESSION_COOKIES = ("sessionid", "sessionid_ss", "sid_tt")
SESSION_PAUSE_SECONDS = 6 * 3600
SETUP_PAUSE_SECONDS = 3600
CONFIRM_TIMEOUT_SECONDS = 120
MAX_SCREENSHOTS = 20

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


def browser_caption(meta: PostMetadata, limit: int = 2200) -> str:
    """Einzeilige Beschreibung (Zeilenumbrüche und Hashtag-Vorschläge vertragen sich im Editor schlecht)."""
    parts = [meta.title, meta.description, meta.hashtag_text()]
    return truncate_utf16(" ".join(" ".join(p.split()) for p in parts if p), limit)


def _short(exc: Exception) -> str:
    return str(exc).strip().splitlines()[0][:200] if str(exc).strip() else exc.__class__.__name__


def _playwright():
    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError as exc:
        raise UploadError("Für den TikTok-Browser-Modus fehlt Playwright: pip install playwright",
                          pause_seconds=SETUP_PAUSE_SECONDS) from exc
    return sync_playwright, Error


class TikTokBrowserUploader(Uploader):
    name = "tiktok"
    description = "Browser-Modus (dein TikTok-Account)"

    def __init__(self, cfg: TikTokConfig, profile_dir: Path, marker_path: Path, debug_dir: Path,
                 base_url: str = BASE_URL):
        self.cfg = cfg
        self.profile_dir = Path(profile_dir)
        self.marker_path = Path(marker_path)
        self.debug_dir = Path(debug_dir)
        self.base_url = base_url.rstrip("/")
        self.sel = {**DEFAULT_SELECTORS, **(cfg.browser_selectors or {})}
        self.confirm_timeout = CONFIRM_TIMEOUT_SECONDS

    # ------------------------------------------------------------- Zustand
    def problem(self) -> str | None:
        try:
            import playwright  # noqa: F401
        except ImportError:
            return "Playwright fehlt: pip install playwright"
        if not self.marker_path.is_file():
            return "Noch nicht verbunden: python -m clipmaschine auth tiktok"
        return None

    def connected_at(self) -> float | None:
        try:
            return self.marker_path.stat().st_mtime
        except OSError:
            return None

    def _mark_connected(self) -> None:
        self.marker_path.parent.mkdir(parents=True, exist_ok=True)
        self.marker_path.write_text(json.dumps({"connected_at": iso(utcnow()), "profile": str(self.profile_dir)}),
                                    encoding="utf-8")

    def _launch(self, p, headless: bool):
        _, PlaywrightError = _playwright()
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        options = {"user_data_dir": str(self.profile_dir), "headless": headless,
                   "viewport": {"width": 1366, "height": 900}}
        candidates = []
        if self.cfg.browser_executable:
            candidates.append({"executable_path": self.cfg.browser_executable})
        elif self.cfg.browser_channel:
            candidates.append({"channel": self.cfg.browser_channel})
        candidates.append({})                      # mitgeliefertes Playwright-Chromium
        last_error = None
        for extra in candidates:
            try:
                return p.chromium.launch_persistent_context(**options, **extra)
            except PlaywrightError as exc:
                last_error = exc
                log.debug("Browserstart mit %s fehlgeschlagen: %s", extra or "Chromium", _short(exc))
        raise UploadError("Kein Browser startbar – Google Chrome installieren oder "
                          f"'python -m playwright install chromium' ausführen ({_short(last_error)})",
                          pause_seconds=SETUP_PAUSE_SECONDS)

    def _has_session(self, ctx) -> bool:
        return any(c["name"] in SESSION_COOKIES and c.get("value") for c in ctx.cookies(self.base_url))

    # ------------------------------------------------------------- Login
    def authorize(self, headless: bool = False, timeout: float = 600) -> None:
        sync_playwright, PlaywrightError = _playwright()
        print("\nEin Browserfenster öffnet sich. Melde dich dort ganz normal bei TikTok an –")
        print("am einfachsten per QR-Code: TikTok-App → Profil → ☰ → QR-Code scannen.")
        print("Das Fenster schließt sich danach von selbst.\n")
        with sync_playwright() as p:
            ctx = self._launch(p, headless=headless)
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                if not self._has_session(ctx):
                    page.goto(self.base_url + LOGIN_PATH, wait_until="domcontentloaded", timeout=90_000)
                deadline = time.monotonic() + timeout
                while not self._has_session(ctx):
                    if time.monotonic() > deadline:
                        raise UploadError("Anmeldung nicht abgeschlossen (Zeitlimit 10 Minuten)", retryable=False)
                    page.wait_for_timeout(1500)
                page.wait_for_timeout(2000)        # Cookies vollständig speichern lassen
            except PlaywrightError as exc:
                raise UploadError(f"Browserfenster wurde geschlossen – Anmeldung abgebrochen ({_short(exc)})",
                                  retryable=False) from exc
            finally:
                try:
                    ctx.close()
                except PlaywrightError:
                    pass
        self._mark_connected()
        print("✔ TikTok ist verbunden. Die Clipmaschine postet ab jetzt mit deinem Account.")

    # ------------------------------------------------------------ Upload
    def upload(self, video_path: Path, meta: PostMetadata, clip: Clip) -> UploadResult:
        sync_playwright, PlaywrightError = _playwright()
        if not self.marker_path.is_file():
            raise UploadError("TikTok ist noch nicht verbunden: python -m clipmaschine auth tiktok "
                              "(danach geht es sofort weiter)", pause_seconds=SESSION_PAUSE_SECONDS)
        caption = browser_caption(meta)
        with sync_playwright() as p:
            ctx = self._launch(p, headless=self.cfg.browser_headless)
            page = None
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                page.set_default_timeout(30_000)
                self._prepare_post(page, ctx, video_path, caption, PlaywrightError)
                return self._confirm_post(page, PlaywrightError)
            finally:
                try:
                    ctx.close()
                except PlaywrightError:
                    pass

    def _prepare_post(self, page, ctx, video_path: Path, caption: str, PlaywrightError) -> None:
        """Alles bis einschließlich Klick auf „Posten“. Fehler hier = nichts gepostet → neuer Versuch erlaubt."""
        deadline = time.monotonic() + self.cfg.browser_upload_timeout_seconds
        step = "Upload-Seite öffnen"
        try:
            page.goto(self.base_url + UPLOAD_PATH, wait_until="domcontentloaded", timeout=90_000)
            if LOGIN_PATH in page.url or not self._has_session(ctx):
                raise UploadError("TikTok-Anmeldung abgelaufen – bitte neu verbinden: python -m clipmaschine auth "
                                  "tiktok (danach geht es sofort weiter)", pause_seconds=SESSION_PAUSE_SECONDS)
            self._check_captcha(page)

            step = "Video auswählen"
            file_input = page.locator(self.sel["file_input"]).first
            file_input.wait_for(state="attached", timeout=90_000)
            file_input.set_input_files(str(video_path))

            step = "Beschreibung eintragen"
            editor = page.locator(self.sel["caption"]).first
            editor.wait_for(state="visible", timeout=max(deadline - time.monotonic(), 1) * 1000)
            self._fill_caption(page, editor, caption)

            step = "auf Upload warten"
            button = self._find_post_button(page, deadline)
            self._wait_until_enabled(page, button, deadline)

            step = "Posten"
            button.scroll_into_view_if_needed()
            button.click()
        except UploadError as exc:
            if not exc.pause_seconds:
                exc.args = (f"{exc.args[0]} – Screenshot: {self._screenshot(page, step)}",)
            raise
        except PlaywrightError as exc:
            raise UploadError(f"TikTok im Browser: Schritt „{step}“ fehlgeschlagen ({_short(exc)}) – "
                              f"Screenshot: {self._screenshot(page, step)}") from exc

    def _confirm_post(self, page, PlaywrightError) -> UploadResult:
        """Nach dem Klick: auf Bestätigung warten. Niemals erneut posten (Schutz vor Doppel-Posts)."""
        try:
            self._click_confirm_if_asked(page)
            deadline = time.monotonic() + self.confirm_timeout
            while time.monotonic() < deadline:
                if self._is_success(page):
                    page.wait_for_timeout(4000)     # TikTok die Übermittlung in Ruhe abschließen lassen
                    return UploadResult("posted")
                self._check_captcha(page, raise_on_headless=False)
                page.wait_for_timeout(1000)
            shot = self._screenshot(page, "nach-posten")
        except (PlaywrightError, UploadError) as exc:
            shot = f"{self._screenshot(page, 'nach-posten')} ({_short(exc)})"
        log.warning("TikTok hat den Post nicht eindeutig bestätigt – gilt als eingereicht, bitte kurz prüfen: %s",
                    shot)
        return UploadResult("submitted")

    # ----------------------------------------------------------- Einzelschritte
    def _fill_caption(self, page, editor, caption: str) -> None:
        def clear() -> None:
            editor.click()
            page.keyboard.press("ControlOrMeta+a")
            page.keyboard.press("Backspace")

        clear()
        page.keyboard.insert_text(caption)
        page.keyboard.insert_text(" ")              # schließt eine offene Hashtag-Vorschlagsliste
        page.wait_for_timeout(400)
        probe = caption[:25].strip()
        if probe and probe not in editor.inner_text():
            clear()                                 # Fallback: wie eine echte Tastatur tippen
            page.keyboard.type(caption + " ", delay=12)
            if probe not in editor.inner_text():
                raise UploadError("Beschreibung ließ sich nicht eintragen")

    def _button_by_texts(self, page, texts: list[str]):
        pattern = re.compile(r"^\s*(" + "|".join(re.escape(t) for t in texts) + r")\s*$", re.IGNORECASE)
        return page.get_by_role("button", name=pattern)

    def _find_post_button(self, page, deadline: float):
        while time.monotonic() < deadline:
            by_css = page.locator(self.sel["post_button"])
            if by_css.count():
                return by_css.first
            by_text = self._button_by_texts(page, self.sel["post_button_texts"])
            if by_text.count():
                return by_text.last
            self._check_captcha(page)
            page.wait_for_timeout(500)
        raise UploadError("„Posten“-Button nicht gefunden (hat TikTok die Seite geändert? → tiktok.browser_selectors)")

    def _wait_until_enabled(self, page, button, deadline: float) -> None:
        while time.monotonic() < deadline:
            disabled = (button.get_attribute("disabled") is not None
                        or button.get_attribute("aria-disabled") == "true"
                        or button.get_attribute("data-disabled") == "true")
            if not disabled and button.is_enabled():
                return
            error = self._visible_text(page, self.sel["error_texts"])
            if error:
                raise UploadError(f"TikTok meldet beim Hochladen: „{error}“")
            self._check_captcha(page)
            page.wait_for_timeout(1000)
        raise UploadError("Upload bei TikTok nicht rechtzeitig fertig geworden")

    def _click_confirm_if_asked(self, page) -> None:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            confirm = self._button_by_texts(page, self.sel["confirm_texts"])
            if confirm.count() and confirm.first.is_visible():
                confirm.first.click()
                return
            if self._is_success(page):
                return
            page.wait_for_timeout(500)

    def _is_success(self, page) -> bool:
        if any(part in page.url for part in self.sel["success_url_parts"]):
            return True
        return self._visible_text(page, self.sel["success_texts"]) is not None

    def _visible_text(self, page, texts: list[str]) -> str | None:
        for text in texts:
            matches = page.get_by_text(text, exact=False)
            try:
                if matches.count() and matches.first.is_visible():
                    return text
            except Exception:
                continue
        return None

    def _check_captcha(self, page, raise_on_headless: bool = True) -> None:
        """Sicherheitsprüfung von TikTok: im sichtbaren Browser löst du sie selbst, die Maschine wartet."""
        captcha = page.locator(self.sel["captcha"])
        if not captcha.count() or not captcha.first.is_visible():
            return
        if self.cfg.browser_headless:
            if raise_on_headless:
                raise UploadError("TikTok verlangt eine Sicherheitsprüfung – einmal 'python -m clipmaschine auth "
                                  "tiktok' ausführen und im Browser bestätigen", pause_seconds=SETUP_PAUSE_SECONDS)
            return
        log.warning("TikTok zeigt eine Sicherheitsprüfung – bitte im Browserfenster lösen (max. 5 Minuten) …")
        page.bring_to_front()
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            page.wait_for_timeout(2000)
            if not captcha.count() or not captcha.first.is_visible():
                log.info("Sicherheitsprüfung erledigt – es geht weiter")
                return
        raise UploadError("Sicherheitsprüfung nicht gelöst", pause_seconds=SETUP_PAUSE_SECONDS)

    def _screenshot(self, page, label: str) -> str:
        if page is None:
            return "(kein Screenshot)"
        try:
            self.debug_dir.mkdir(parents=True, exist_ok=True)
            safe = re.sub(r"[^A-Za-z0-9_-]+", "-", label)
            path = self.debug_dir / f"tiktok_{utcnow():%Y%m%d_%H%M%S}_{safe}.png"
            page.screenshot(path=str(path), full_page=True)
            for old in sorted(self.debug_dir.glob("tiktok_*.png"))[:-MAX_SCREENSHOTS]:
                old.unlink(missing_ok=True)
            return str(path)
        except Exception:
            return "(kein Screenshot möglich)"
