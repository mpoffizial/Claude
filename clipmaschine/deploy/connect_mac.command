#!/bin/bash
# ================================================================
#  Konten verbinden (Mac) - einfach doppelklicken.
#  Oeffnet ein Browserfenster, in dem du dich ganz normal bei
#  YouTube, TikTok oder Instagram anmeldest. Danach postet die
#  Clipmaschine dort von selbst (kein Neustart noetig).
#  Auch nutzen, wenn eine Anmeldung abgelaufen ist.
# ================================================================
cd "$(dirname "$0")/../.." || exit 1
ROOT="$(pwd)"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"     # Homebrew (Apple Silicon / Intel)
VPY="$ROOT/.venv/bin/python"

pause_and_exit() {
  echo
  read -r -p "Enter zum Schliessen " _ || true
  exit "${1:-1}"
}

if [ ! -x "$VPY" ] || [ ! -f "config.yaml" ]; then
  echo "Die Clipmaschine ist hier noch nicht eingerichtet."
  echo "Bitte zuerst den Starter auf dem Schreibtisch doppelklicken - der Assistent verbindet die Konten gleich mit."
  pause_and_exit 1
fi

echo "Pruefe Pakete ..."
"$VPY" -m pip install --disable-pip-version-check -q -r clipmaschine/requirements.txt >/dev/null 2>&1 || true
if [ "$(uname -s)" = "Darwin" ] && [ ! -d "/Applications/Google Chrome.app" ] && [ ! -d "$HOME/Applications/Google Chrome.app" ]; then
  "$VPY" -m playwright install chromium >/dev/null 2>&1 || true
fi

PLATFORMS="${1:-}"
if [ -z "$PLATFORMS" ]; then
  echo
  echo "Welches Konto moechtest du verbinden?"
  echo "  1) YouTube"
  echo "  2) TikTok"
  echo "  3) Instagram"
  echo "  4) alle drei nacheinander"
  read -r -p "Nummer eingeben und Enter druecken: " choice || pause_and_exit 1
  case "$choice" in
    1) PLATFORMS="youtube" ;;
    2) PLATFORMS="tiktok" ;;
    3) PLATFORMS="instagram" ;;
    4) PLATFORMS="youtube tiktok instagram" ;;
    *) echo "Ungueltige Auswahl."; pause_and_exit 1 ;;
  esac
fi

failed=""
for platform in $PLATFORMS; do
  echo
  echo "=== $platform ==="
  "$VPY" -m clipmaschine auth "$platform" || failed="$failed $platform"
done

echo
if [ -z "$failed" ]; then
  echo "Fertig. Die laufende Clipmaschine postet ab sofort auch dort - kein Neustart noetig."
  pause_and_exit 0
fi
echo "Nicht verbunden:$failed - einfach erneut doppelklicken und noch einmal versuchen."
pause_and_exit 1
