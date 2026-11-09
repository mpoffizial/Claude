#!/bin/bash
# ================================================================
#  Clipmaschine fuer den Mac - einfach doppelklicken.
#  Erster Start: richtet Python-Umgebung und Pakete ein und startet
#  den Einrichtungs-Assistenten (inkl. TikTok-/Instagram-Anmeldung).
#  Danach: laeuft dauerhaft und startet nach einem Absturz neu.
#  Der Mac geht dabei nicht in den Ruhezustand (caffeinate).
# ================================================================
cd "$(dirname "$0")/../.." || exit 1
ROOT="$(pwd)"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"     # Homebrew (Apple Silicon / Intel)

pause_and_exit() {
  echo
  read -r -p "Enter zum Schliessen " _ || true
  exit "${1:-1}"
}

find_python() {
  for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1 &&
       "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg fehlt. Installieren mit:   brew install ffmpeg"
  pause_and_exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
  echo "Erster Start: richte die Python-Umgebung ein ..."
  PY="$(find_python)" || { echo "Python 3.10 oder neuer fehlt. Installieren mit:   brew install python@3.12"; pause_and_exit 1; }
  "$PY" -m venv .venv || { echo "Python-Umgebung konnte nicht angelegt werden."; pause_and_exit 1; }
fi
VPY="$ROOT/.venv/bin/python"

echo "Pruefe Pakete ..."
if ! "$VPY" -m pip install --disable-pip-version-check -q -r clipmaschine/requirements.txt; then
  echo "Die Pakete konnten nicht installiert werden (Internetverbindung pruefen)."
  pause_and_exit 1
fi
# yt-dlp aktuell halten - Twitch aendert seine Seite oefter
"$VPY" -m pip install --disable-pip-version-check -q --upgrade yt-dlp >/dev/null 2>&1 || true

# Ohne Google Chrome einmalig den Browser fuer TikTok/Instagram laden
if [ "$(uname -s)" = "Darwin" ] && [ ! -d "/Applications/Google Chrome.app" ] && [ ! -d "$HOME/Applications/Google Chrome.app" ]; then
  "$VPY" -m playwright install chromium || true
fi

if [ ! -f "config.yaml" ]; then
  "$VPY" -m clipmaschine setup --no-start || pause_and_exit 1
fi

KEEP_AWAKE=""
if command -v caffeinate >/dev/null 2>&1; then
  KEEP_AWAKE="caffeinate -is"      # kein Ruhezustand, solange die Maschine laeuft
fi
trap 'echo; echo "Clipmaschine beendet."; exit 0' INT

while true; do
  # shellcheck disable=SC2086  # KEEP_AWAKE soll in Woerter zerfallen
  $KEEP_AWAKE "$VPY" -m clipmaschine run ${CLIPMASCHINE_RUN_ARGS:-}
  code=$?
  if [ "$code" = "2" ] || [ "$code" = "3" ]; then
    echo "Konfigurationsfehler oder die Maschine laeuft bereits - kein automatischer Neustart."
    pause_and_exit "$code"
  fi
  if [ -n "${CLIPMASCHINE_ONCE:-}" ]; then
    exit "$code"
  fi
  echo
  echo "Clipmaschine wurde beendet (Code $code) - Neustart in 30 Sekunden ... (Ctrl+C zum Abbrechen)"
  sleep 30
done
