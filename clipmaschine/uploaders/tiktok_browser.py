"""TikTok direkt mit deinem normalen Account: Upload über TikTok Studio im Browser.

Kein Entwickler-Konto und keine App-Prüfung nötig – Details zum Ablauf und zum Schutz vor
Doppel-Posts stehen in browser_base.py.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Iterator

from .base import ReviewResult, UploadError
from .browser_base import (CONFIRM_TIMEOUT_SECONDS, SESSION_PAUSE_SECONDS, BrowserUploader,  # noqa: F401
                           _playwright, _short, browser_caption)

UPLOAD_PATH = "/tiktokstudio/upload?from=webapp"
CONTENT_PATH = "/tiktokstudio/content"

# Sucht die Zeile des Videos in der Beitragsliste: vom Beschreibungstext so weit nach oben,
# bis ein zweites Video dazukäme (oder der Block zu groß wird), und gibt deren Text zurück.
ROW_TEXT_JS = """([probe, maxChars]) => {
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let node, el = null, own = '';
  while ((node = walker.nextNode())) {
    if (node.nodeValue && node.nodeValue.includes(probe)) { el = node.parentElement; own = node.nodeValue; break; }
  }
  if (!el) return null;
  const videos = (e) => new Set(Array.from(e.querySelectorAll('a[href*="/video/"]'))
                                     .map((a) => a.getAttribute('href'))).size;
  let row = el;
  for (let i = 0; i < 12 && row.parentElement && row.parentElement !== document.body; i++) {
    const parent = row.parentElement;
    if (videos(parent) > 1 || (parent.innerText || '').length > maxChars) break;
    row = parent;
  }
  return {row: row.innerText || '', own: own.trim()};
}"""


def classify_review(row_text: str | None, own_text: str, sel: dict) -> ReviewResult:
    """Wertet den Text einer Beitragszeile aus. `own_text` (die eigene Beschreibung) wird herausgerechnet,
    damit Wörter aus dem Clip-Titel keinen Fehlalarm auslösen."""
    if row_text is None:
        return ReviewResult("unknown", "Video in der Beitragsliste nicht gefunden")
    if own_text:
        row_text = row_text.replace(own_text, "\n")
    lines = [line.strip() for line in row_text.splitlines() if line.strip()]
    for marker in sel["restricted_texts"]:
        for line in lines:
            if marker.lower() in line.lower():
                return ReviewResult("restricted", line[:200])
    for marker in sel["pending_texts"]:
        if any(marker.lower() in line.lower() for line in lines):
            return ReviewResult("pending", marker)
    return ReviewResult("ok")

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
    # Hinweise in der Beitragsliste, dass ein Video nicht (mehr) weiterempfohlen wird
    "restricted_texts": ["For You feed", "Für dich", "Für-dich", "not eligible", "ineligible", "not recommended",
                         "nicht empfohlen", "Empfehlung", "recommendation", "unoriginal", "nicht original",
                         "violation", "Verstoß", "restricted", "eingeschränkt"],
    "pending_texts": ["Under review", "Wird überprüft", "In Prüfung", "Processing", "Wird verarbeitet"],
    "row_max_chars": 800,
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
    supports_review = True

    def review_posts(self, probes: dict[str, str]) -> dict[str, ReviewResult]:
        """Öffnet die Beitragsliste in TikTok Studio und liest den Status der zuletzt geposteten Videos."""
        sync_playwright, PlaywrightError = _playwright()
        with sync_playwright() as p:
            ctx = self._launch(p, headless=self.cfg.browser_headless)
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                page.goto(self.base_url + CONTENT_PATH, wait_until="domcontentloaded", timeout=90_000)
                self._ensure_session(ctx, page)
                self._check_captcha(page)
                page.wait_for_timeout(4000)                 # Liste nachladen lassen
                results = {}
                for clip_id, probe in probes.items():
                    found = page.evaluate(ROW_TEXT_JS, [probe, int(self.sel["row_max_chars"])])
                    results[clip_id] = (classify_review(found["row"], found["own"], self.sel) if found
                                        else classify_review(None, "", self.sel))
                if any(r.status == "restricted" for r in results.values()):
                    self._screenshot(page, "nicht-empfohlen")
                return results
            except PlaywrightError as exc:
                raise UploadError(f"TikTok-Beitragsliste nicht lesbar ({_short(exc)})") from exc
            finally:
                try:
                    ctx.close()
                except PlaywrightError:
                    pass

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

