#!/usr/bin/env bash
# =============================================================
#  Bark Extractor – Installation Script
#  Prerequisites: Python 3.9+, pip, internet access
# =============================================================

set -e

BOLD="\033[1m"
GREEN="\033[0;32m"
YELLOW="\033[1;33m"
RED="\033[0;31m"
RESET="\033[0m"

info()    { echo -e "${GREEN}[✓]${RESET} $*"; }
warn()    { echo -e "${YELLOW}[!]${RESET} $*"; }
error()   { echo -e "${RED}[✗]${RESET} $*" >&2; exit 1; }
section() { echo -e "\n${BOLD}==> $*${RESET}"; }

section "Bark Extractor Setup"

# ---------------------------------------------------
# 1. System packages – FFmpeg
# ---------------------------------------------------
section "Installing FFmpeg"

if command -v ffmpeg &>/dev/null; then
  info "FFmpeg already installed: $(ffmpeg -version 2>&1 | head -1)"
else
  if command -v apt-get &>/dev/null; then
    sudo apt-get update -qq
    sudo apt-get install -y ffmpeg
  elif command -v brew &>/dev/null; then
    brew install ffmpeg
  elif command -v dnf &>/dev/null; then
    sudo dnf install -y ffmpeg
  elif command -v pacman &>/dev/null; then
    sudo pacman -S --noconfirm ffmpeg
  else
    warn "Could not detect package manager. Please install FFmpeg manually:"
    warn "  https://ffmpeg.org/download.html"
    warn "Then re-run this script."
  fi
fi

# ---------------------------------------------------
# 2. Python version check
# ---------------------------------------------------
section "Checking Python version"

PYTHON=$(command -v python3 || command -v python || true)
if [ -z "$PYTHON" ]; then
  error "Python 3.9+ is required but not found in PATH."
fi

PY_VER=$($PYTHON -c 'import sys; print("%d.%d" % sys.version_info[:2])')
PY_MAJOR=$($PYTHON -c 'import sys; print(sys.version_info.major)')
PY_MINOR=$($PYTHON -c 'import sys; print(sys.version_info.minor)')

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 9 ]; }; then
  error "Python 3.9+ required (found $PY_VER)."
fi

info "Python $PY_VER found at $PYTHON"

# ---------------------------------------------------
# 3. Virtual environment
# ---------------------------------------------------
section "Setting up virtual environment"

if [ ! -d ".venv" ]; then
  $PYTHON -m venv .venv
  info "Virtual environment created in .venv/"
else
  info "Virtual environment already exists"
fi

# Activate
# shellcheck disable=SC1091
source .venv/bin/activate

# ---------------------------------------------------
# 4. Python dependencies
# ---------------------------------------------------
section "Installing Python dependencies"

pip install --upgrade pip -q
pip install -r requirements.txt
info "Dependencies installed"

# Also ensure yt-dlp is up to date in the venv
pip install --upgrade yt-dlp -q
info "yt-dlp updated to latest version"

# ---------------------------------------------------
# 4b. Bundled yt-dlp binary
# ---------------------------------------------------
section "Checking bundled yt-dlp binary"

if [ -f "./yt-dlp" ]; then
  chmod +x ./yt-dlp
  info "Bundled yt-dlp marked executable: $(./yt-dlp --version 2>/dev/null || echo '(version check failed)')"
else
  warn "Bundled yt-dlp not found. Downloading from GitHub..."
  curl -L --retry 3 --retry-delay 2 \
    -o ./yt-dlp \
    "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp"
  chmod +x ./yt-dlp
  info "yt-dlp downloaded: $(./yt-dlp --version 2>/dev/null || echo '(version check failed)')"
fi

# ---------------------------------------------------
# 5. Create directories
# ---------------------------------------------------
section "Creating required directories"
mkdir -p downloads sessions
info "directories created: downloads/, sessions/"

# ---------------------------------------------------
# 6. .env file
# ---------------------------------------------------
section "Configuring environment"

if [ ! -f ".env" ]; then
  cp .env.example .env
  info ".env created from .env.example"
  warn "Review .env and adjust paths if needed."
else
  info ".env already exists – skipping"
fi

# ---------------------------------------------------
# Done
# ---------------------------------------------------
echo ""
echo -e "${BOLD}${GREEN}Setup complete!${RESET}"
echo ""
echo "  Start Bark Extractor with:"
echo ""
echo "    source .venv/bin/activate"
echo "    python app.py"
echo ""
echo "  Then open:  http://localhost:5100"
echo ""
