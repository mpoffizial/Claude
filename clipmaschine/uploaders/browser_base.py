"""Gemeinsame Basis für Uploads mit deinem normalen Account über die Website (TikTok, Instagram).

Du meldest dich einmal selbst im Browserfenster an (Passwort/QR-Code bleiben im Browser – die
Clipmaschine sieht sie nie). Die Sitzung liegt in einem eigenen Browserprofil; danach lädt die
Maschine jedes Video so hoch, wie du es von Hand tun würdest.

Sicherheitsnetz gegen Doppel-Posts: Fehler *vor* dem Klick auf „Posten/Teilen“ führen zu einem
neuen Versuch. Ist der Klick erfolgt, wird nie automatisch erneut gepostet – bleibt die
Bestätigung aus, gilt das Video als eingereicht (mit Screenshot zur Kontrolle).
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Iterator

from ..models import Clip, PostMetadata, iso, utcnow
from .base import UploadError, UploadResult, Uploader, truncate_utf16

log = logging.getLogger(__name__)

SESSION_PAUSE_SECONDS = 6 * 3600
SETUP_PAUSE_SECONDS = 3600
CONFIRM_TIMEOUT_SECONDS = 120
MAX_SCREENSHOTS = 20


def browser_caption(meta: PostMetadata, limit: int = 2200, max_hashtags: int | None = None) -> str:
    """Einzeilige Beschreibung (Zeilenumbrüche und Hashtag-Vorschläge vertragen sich im Editor schlecht)."""
    parts = [meta.title, meta.description, meta.hashtag_text(max_hashtags)]
    return truncate_utf16(" ".join(" ".join(p.split()) for p in parts if p), limit)


def _short(exc: Exception | None) -> str:
    text = str(exc).strip() if exc else ""
    return text.splitlines()[0][:200] if text else (exc.__class__.__name__ if exc else "")


def _playwright():
    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError as exc:
        raise UploadError("Für den Browser-Modus fehlt Playwright: pip install playwright",
                          pause_seconds=SETUP_PAUSE_SECONDS) from exc
    return sync_playwright, Error


class BrowserUploader(Uploader):
    """Unterklassen setzen die Konstanten und implementieren `_post_steps`."""

    label = ""                                   # z. B. "TikTok"
    BASE_URL = ""
    START_PATH = "/"
    LOGIN_PATH = "/login"
    SESSION_COOKIES: tuple[str, ...] = ()
    DEFAULT_SELECTORS: dict = {}
    LOGIN_HINT = "Melde dich dort ganz normal an."
    MAX_HASHTAGS: int | None = None
    CONFIRM_TIMEOUT = CONFIRM_TIMEOUT_SECONDS

    def __init__(self, cfg, profile_dir: Path, marker_path: Path, debug_dir: Path, base_url: str | None = None):
        self.cfg = cfg
        self.profile_dir = Path(profile_dir)
        self.marker_path = Path(marker_path)
        self.debug_dir = Path(debug_dir)
        self.base_url = (base_url or self.BASE_URL).rstrip("/")
        self.sel = {**self.DEFAULT_SELECTORS, **(getattr(cfg, "browser_selectors", None) or {})}
        self.confirm_timeout = self.CONFIRM_TIMEOUT

    # ------------------------------------------------------------- Zustand
    def problem(self) -> str | None:
        try:
            import playwright  # noqa: F401
        except ImportError:
            return "Playwright fehlt: pip install playwright"
        if not self.marker_path.is_file():
            return f"Noch nicht verbunden: python -m clipmaschine auth {self.name}"
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
        return any(c["name"] in self.SESSION_COOKIES and c.get("value") for c in ctx.cookies(self.base_url))

    def _ensure_session(self, ctx, page) -> None:
        if self.LOGIN_PATH in page.url or not self._has_session(ctx):
            raise UploadError(f"{self.label}-Anmeldung abgelaufen – bitte neu verbinden: python -m clipmaschine "
                              f"auth {self.name} (danach geht es sofort weiter)", pause_seconds=SESSION_PAUSE_SECONDS)

    # ------------------------------------------------------------- Login
    def authorize(self, headless: bool = False, timeout: float = 600) -> None:
        sync_playwright, PlaywrightError = _playwright()
        print(f"\nEin Browserfenster öffnet sich. {self.LOGIN_HINT}")
        print("Das Fenster schließt sich danach von selbst.\n")
        with sync_playwright() as p:
            ctx = self._launch(p, headless=headless)
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                if not self._has_session(ctx):
                    page.goto(self.base_url + self.LOGIN_PATH, wait_until="domcontentloaded", timeout=90_000)
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
        print(f"✔ {self.label} ist verbunden. Die Clipmaschine postet ab jetzt mit deinem Account.")

    # ------------------------------------------------------------ Upload
    def upload(self, video_path: Path, meta: PostMetadata, clip: Clip) -> UploadResult:
        sync_playwright, PlaywrightError = _playwright()
        if not self.marker_path.is_file():
            raise UploadError(f"{self.label} ist noch nicht verbunden: python -m clipmaschine auth {self.name} "
                              "(danach geht es sofort weiter)", pause_seconds=SESSION_PAUSE_SECONDS)
        caption = browser_caption(meta, max_hashtags=self.MAX_HASHTAGS)
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

    def _post_steps(self, page, ctx, video_path: Path, caption: str, deadline: float) -> Iterator[str]:
        """Generator: liefert vor jedem Schritt dessen Namen; der letzte Schritt klickt auf „Posten“."""
        raise NotImplementedError

    def _prepare_post(self, page, ctx, video_path: Path, caption: str, PlaywrightError) -> None:
        """Alles bis einschließlich Klick auf „Posten“. Fehler hier = nichts gepostet → neuer Versuch erlaubt."""
        deadline = time.monotonic() + self.cfg.browser_upload_timeout_seconds
        step = "Start"
        try:
            for step in self._post_steps(page, ctx, video_path, caption, deadline):
                log.debug("%s: %s", self.label, step)
        except UploadError as exc:
            if not exc.pause_seconds:
                exc.args = (f"{exc.args[0]} – Screenshot: {self._screenshot(page, step)}",)
            raise
        except PlaywrightError as exc:
            raise UploadError(f"{self.label} im Browser: Schritt „{step}“ fehlgeschlagen ({_short(exc)}) – "
                              f"Screenshot: {self._screenshot(page, step)}") from exc

    def _confirm_post(self, page, PlaywrightError) -> UploadResult:
        """Nach dem Klick: auf Bestätigung warten. Niemals erneut posten (Schutz vor Doppel-Posts)."""
        try:
            self._click_confirm_if_asked(page)
            deadline = time.monotonic() + self.confirm_timeout
            while time.monotonic() < deadline:
                if self._is_success(page):
                    page.wait_for_timeout(4000)     # Übermittlung in Ruhe abschließen lassen
                    return UploadResult("posted")
                self._check_captcha(page, raise_on_headless=False)
                page.wait_for_timeout(1000)
            shot = self._screenshot(page, "nach-posten")
        except (PlaywrightError, UploadError) as exc:
            shot = f"{self._screenshot(page, 'nach-posten')} ({_short(exc)})"
        log.warning("%s hat den Post nicht eindeutig bestätigt – gilt als eingereicht, bitte kurz prüfen: %s",
                    self.label, shot)
        return UploadResult("submitted")

    # ----------------------------------------------------------- Helfer
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

    @staticmethod
    def _pattern(texts: list[str]) -> re.Pattern:
        return re.compile(r"^\s*(" + "|".join(re.escape(t) for t in texts) + r")\s*$", re.IGNORECASE)

    def _button_by_texts(self, page, texts: list[str]):
        return page.get_by_role("button", name=self._pattern(texts))

    @staticmethod
    def _visible(locator) -> bool:
        try:
            return bool(locator.count()) and locator.first.is_visible()
        except Exception:
            return False

    def _find_button(self, page, deadline: float, texts: list[str], css: str | None = None, what: str = "Button"):
        while time.monotonic() < deadline:
            if css:
                by_css = page.locator(css)
                if by_css.count():
                    return by_css.first
            by_text = self._button_by_texts(page, texts)
            if by_text.count():
                return by_text.last
            self._check_captcha(page)
            page.wait_for_timeout(500)
        raise UploadError(f"{what} nicht gefunden (hat {self.label} die Seite geändert? → "
                          f"{self.name}.browser_selectors)")

    def _wait_until_enabled(self, page, button, deadline: float) -> None:
        while time.monotonic() < deadline:
            disabled = (button.get_attribute("disabled") is not None
                        or button.get_attribute("aria-disabled") == "true"
                        or button.get_attribute("data-disabled") == "true")
            if not disabled and button.is_enabled():
                return
            error = self._visible_text(page, self.sel.get("error_texts", []))
            if error:
                raise UploadError(f"{self.label} meldet beim Hochladen: „{error}“")
            self._check_captcha(page)
            page.wait_for_timeout(1000)
        raise UploadError(f"Upload bei {self.label} nicht rechtzeitig fertig geworden")

    def _click_if_visible(self, page, texts: list[str], wait_seconds: float = 3) -> bool:
        """Klickt einen Button bzw. Text aus `texts`, falls er innerhalb von `wait_seconds` erscheint."""
        if not texts:
            return False
        deadline = time.monotonic() + wait_seconds
        pattern = self._pattern(texts)
        while True:
            for locator in (page.get_by_role("button", name=pattern), page.get_by_text(pattern)):
                if self._visible(locator):
                    locator.first.click()
                    return True
            if time.monotonic() >= deadline:
                return False
            page.wait_for_timeout(300)

    def _click_confirm_if_asked(self, page) -> None:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if self._is_success(page):
                return
            confirm = self._button_by_texts(page, self.sel.get("confirm_texts") or ["\u0000"])
            if self._visible(confirm):
                confirm.first.click()
                return
            page.wait_for_timeout(500)

    def _is_success(self, page) -> bool:
        if any(part in page.url for part in self.sel.get("success_url_parts", [])):
            return True
        selector = self.sel.get("success_selector")
        if selector and self._visible(page.locator(selector)):
            return True
        return self._visible_text(page, self.sel.get("success_texts", [])) is not None

    def _visible_text(self, page, texts: list[str]) -> str | None:
        for text in texts:
            if self._visible(page.get_by_text(text, exact=False)):
                return text
        return None

    def _challenge_present(self, page) -> bool:
        if any(part in page.url for part in self.sel.get("challenge_url_parts", [])):
            return True
        css = self.sel.get("captcha")
        return bool(css) and self._visible(page.locator(css))

    def _check_captcha(self, page, raise_on_headless: bool = True) -> None:
        """Sicherheitsprüfung: im sichtbaren Browser löst du sie selbst, die Maschine wartet."""
        if not self._challenge_present(page):
            return
        if self.cfg.browser_headless:
            if raise_on_headless:
                raise UploadError(f"{self.label} verlangt eine Sicherheitsprüfung – einmal 'python -m clipmaschine "
                                  f"auth {self.name}' ausführen und im Browser bestätigen",
                                  pause_seconds=SETUP_PAUSE_SECONDS)
            return
        log.warning("%s zeigt eine Sicherheitsprüfung – bitte im Browserfenster erledigen (max. 5 Minuten) …",
                    self.label)
        page.bring_to_front()
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            page.wait_for_timeout(2000)
            if not self._challenge_present(page):
                log.info("Sicherheitsprüfung erledigt – es geht weiter")
                return
        raise UploadError("Sicherheitsprüfung nicht erledigt", pause_seconds=SETUP_PAUSE_SECONDS)

    def _screenshot(self, page, label: str) -> str:
        if page is None:
            return "(kein Screenshot)"
        try:
            self.debug_dir.mkdir(parents=True, exist_ok=True)
            safe = re.sub(r"[^A-Za-z0-9_-]+", "-", label)
            prefix = self.name or "browser"
            path = self.debug_dir / f"{prefix}_{utcnow():%Y%m%d_%H%M%S}_{safe}.png"
            page.screenshot(path=str(path), full_page=True)
            for old in sorted(self.debug_dir.glob(f"{prefix}_*.png"))[:-MAX_SCREENSHOTS]:
                old.unlink(missing_ok=True)
            return str(path)
        except Exception:
            return "(kein Screenshot möglich)"
