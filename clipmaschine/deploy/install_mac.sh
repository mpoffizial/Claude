#!/bin/bash
# ================================================================
#  Clipmaschine - Installation auf dem Mac (macOS 12 oder neuer)
#
#  Im Terminal einfuegen und Enter druecken:
#    curl -fsSL https://raw.githubusercontent.com/mpoffizial/Claude/claude/blissful-turing-83j3hp/clipmaschine/deploy/install_mac.sh | bash
#
#  Was passiert:
#    1. Homebrew, Python und ffmpeg werden bei Bedarf installiert
#       (Homebrew fragt einmal nach deinem Mac-Passwort).
#    2. Die Clipmaschine kommt auf den Schreibtisch. Wird der Schreibtisch mit iCloud
#       synchronisiert, kommt sie nach ~/Clipmaschine (sonst Sync-Konflikte) und auf
#       dem Schreibtisch liegen nur die Starter. Updates behalten Einstellungen und Logins.
#    3. Auf dem Schreibtisch: "Clipmaschine starten" und "Clipmaschine aktualisieren".
#    4. Start - beim ersten Mal mit Einrichtungs-Assistent und Anmeldung bei TikTok/Instagram.
# ================================================================
set -eo pipefail

REPO="mpoffizial/Claude"
BRANCH="claude/blissful-turing-83j3hp"
ZIP_URL="${CLIPMASCHINE_ZIP_URL:-https://codeload.github.com/$REPO/zip/refs/heads/$BRANCH}"
SELF_URL="https://raw.githubusercontent.com/$REPO/$BRANCH/clipmaschine/deploy/install_mac.sh"
HOME_DIR="${CLIPMASCHINE_HOME:-$HOME}"
DESKTOP="$HOME_DIR/Desktop"
TEST_MODE="${CLIPMASCHINE_TEST:-}"
# Optional eine fertige Voreinstellung als eigene, getrennte Maschine installieren, z. B.:
#   curl -fsSL <diese URL> | CLIPMASCHINE_PRESET=gta6 bash
PRESET="$(printf '%s' "${CLIPMASCHINE_PRESET:-}" | tr -cd 'a-z0-9')"
FOLDER="Clipmaschine"
TITLE="Clipmaschine"
if [ -n "$PRESET" ]; then
  PRESET_UPPER="$(printf '%s' "$PRESET" | tr '[:lower:]' '[:upper:]')"
  FOLDER="Clipmaschine-$PRESET_UPPER"
  TITLE="$PRESET_UPPER-Clipmaschine"
fi

step() { printf '\n\033[1;36m==> %s\033[0m\n' "$1"; }
ok() { printf '    \033[32mOK\033[0m  %s\n' "$1"; }
fail() {
  printf '\n\033[1;31mFEHLER: %s\033[0m\n' "$1"
  printf '\033[33mKopiere diese Meldung und schick sie mir - ich behebe das.\033[0m\n'
  exit 1
}
trap 'fail "Unerwarteter Fehler in Zeile $LINENO"' ERR

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

load_brew() {
  for brew_bin in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    if [ -x "$brew_bin" ]; then
      eval "$("$brew_bin" shellenv)"
      return 0
    fi
  done
  return 1
}

printf '\033[1;35mClipmaschine - Installation\033[0m\n'
if [ "$(uname -s)" != "Darwin" ] && [ -z "$TEST_MODE" ]; then
  fail "Dieses Skript ist fuer macOS. Fuer Windows gibt es install_windows.ps1."
fi

if [ -z "$TEST_MODE" ]; then
  step "Pruefe Homebrew, Python und ffmpeg ..."
  command -v brew >/dev/null 2>&1 || load_brew || true
  if ! command -v brew >/dev/null 2>&1; then
    step "Homebrew wird installiert (fragt einmal nach deinem Mac-Passwort) ..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" < /dev/tty ||
      fail "Homebrew-Installation fehlgeschlagen"
    load_brew || fail "Homebrew nicht gefunden - Terminal schliessen, neu oeffnen und den Befehl erneut ausfuehren."
  fi
  ok "Homebrew"
  if ! find_python >/dev/null; then
    brew install python@3.12 || fail "Python-Installation fehlgeschlagen"
  fi
  ok "Python ($(find_python))"
  if ! command -v ffmpeg >/dev/null 2>&1; then
    brew install ffmpeg || fail "ffmpeg-Installation fehlgeschlagen"
  fi
  ok "ffmpeg"
fi

INSTALL_BASE="$DESKTOP"
VIA_ICLOUD=""
if [ -d "$HOME_DIR/Library/Mobile Documents/com~apple~CloudDocs/Desktop" ]; then
  INSTALL_BASE="$HOME_DIR"
  VIA_ICLOUD=1
fi
TARGET="$INSTALL_BASE/$FOLDER"
START="$TARGET/clipmaschine/deploy/start_mac.command"

step "Lade die neueste Version ..."
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
curl -fsSL "$ZIP_URL" -o "$TMP_DIR/clipmaschine.zip" || fail "Download fehlgeschlagen - Internetverbindung pruefen"
unzip -q "$TMP_DIR/clipmaschine.zip" -d "$TMP_DIR/x" || fail "Entpacken fehlgeschlagen"
SRC="$(find "$TMP_DIR/x" -mindepth 1 -maxdepth 1 -type d | head -n 1)/clipmaschine"
[ -f "$SRC/__main__.py" ] || fail "Download unvollstaendig - bitte erneut versuchen"
if [ -n "$PRESET" ] && [ ! -f "$SRC/presets/$PRESET.yaml" ]; then
  fail "Die Voreinstellung '$PRESET' gibt es nicht"
fi
ok "heruntergeladen"

step "Installiere nach $TARGET ..."
if [ -n "$VIA_ICLOUD" ]; then
  echo "    (Dein Schreibtisch wird mit iCloud synchronisiert - die Dateien kommen deshalb nach"
  echo "     $TARGET, auf dem Schreibtisch liegen die Starter.)"
fi
mkdir -p "$TARGET"
rm -rf "$TARGET/clipmaschine"                       # nur Programmcode, keine Daten
cp -R "$SRC" "$TARGET/clipmaschine"
chmod +x "$TARGET/clipmaschine/deploy/start_mac.command" "$TARGET/clipmaschine/deploy/install_mac.sh"
if [ -n "$PRESET" ]; then
  printf '%s\n' "$PRESET" > "$TARGET/preset.txt"
fi
ok "Dateien aktualisiert (config.yaml, .env, Logins und Daten bleiben erhalten)"

step "Lege die Starter auf den Schreibtisch ..."
mkdir -p "$DESKTOP"
cat > "$DESKTOP/$TITLE starten.command" <<EOF
#!/bin/bash
exec "$START"
EOF
cat > "$DESKTOP/$TITLE aktualisieren.command" <<EOF
#!/bin/bash
curl -fsSL "$SELF_URL" | CLIPMASCHINE_PRESET="$PRESET" bash
echo
read -r -p "Enter zum Schliessen " _
EOF
chmod +x "$DESKTOP/$TITLE starten.command" "$DESKTOP/$TITLE aktualisieren.command"
ok "\"$TITLE starten\" und \"$TITLE aktualisieren\""

if [ -n "${CLIPMASCHINE_NO_START:-}" ]; then
  printf '\n\033[32mFertig - installiert in %s\033[0m\n' "$TARGET"
  exit 0
fi
if [ -n "$(find "$TARGET/clipmaschine_data/heartbeat.json" -mmin -10 2>/dev/null)" ]; then
  printf '\n\033[33mDie Clipmaschine laeuft bereits. Fuer die neue Version ihr Terminal-Fenster schliessen\n'
  printf 'und per Doppelklick auf "%s starten" neu starten.\033[0m\n' "$TITLE"
  exit 0
fi
step "Starte die Clipmaschine ..."
if command -v open >/dev/null 2>&1; then
  open "$START"
else
  "$START" &
fi
printf '\n\033[32mFertig! Im neuen Terminal-Fenster fuehrt dich der Assistent durch die Einrichtung\n'
printf '(Twitch-Kanaele, dann Anmeldung bei TikTok und Instagram).\033[0m\n'
