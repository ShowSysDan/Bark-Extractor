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
# 7. Check port availability
# ---------------------------------------------------
section "Checking port availability"

DESIRED_PORT=$(grep -E '^PORT=' .env 2>/dev/null | cut -d= -f2 | tr -d '[:space:]')
DESIRED_PORT="${DESIRED_PORT:-5100}"

is_port_open() {
  if command -v ss &>/dev/null; then
    ! ss -tlnp 2>/dev/null | grep -q ":${1} "
  elif command -v lsof &>/dev/null; then
    ! lsof -i :"${1}" &>/dev/null
  elif command -v netstat &>/dev/null; then
    ! netstat -tlnp 2>/dev/null | grep -q ":${1} "
  else
    return 0  # assume open if we can't check
  fi
}

if is_port_open "$DESIRED_PORT"; then
  info "Port $DESIRED_PORT is available"
else
  warn "Port $DESIRED_PORT is already in use!"
  # Try the next 10 ports
  FOUND=""
  for TRY_PORT in $(seq $((DESIRED_PORT + 1)) $((DESIRED_PORT + 10))); do
    if is_port_open "$TRY_PORT"; then
      FOUND="$TRY_PORT"
      break
    fi
  done
  if [ -n "$FOUND" ]; then
    warn "Switching to port $FOUND"
    sed -i "s/^PORT=.*/PORT=${FOUND}/" .env
    info "Updated .env → PORT=$FOUND"
  else
    warn "Ports ${DESIRED_PORT}-$((DESIRED_PORT + 10)) all in use. Edit PORT in .env manually."
  fi
fi

ACTIVE_PORT=$(grep -E '^PORT=' .env 2>/dev/null | cut -d= -f2 | tr -d '[:space:]')
ACTIVE_PORT="${ACTIVE_PORT:-5100}"

# ---------------------------------------------------
# 8. Make BarkExtractor executable
# ---------------------------------------------------
section "Preparing BarkExtractor"
chmod +x ./BarkExtractor
info "BarkExtractor marked executable"

# ---------------------------------------------------
# 9. Install systemd service
# ---------------------------------------------------
section "Installing systemd service"

WORKDIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_USER="${SUDO_USER:-$(whoami)}"
SERVICE_FILE="/etc/systemd/system/bark-extractor.service"

if command -v systemctl &>/dev/null; then
  # Generate the service file from the template
  sed -e "s|__USER__|${SERVICE_USER}|g" \
      -e "s|__WORKDIR__|${WORKDIR}|g" \
      bark-extractor.service > /tmp/bark-extractor.service

  sudo cp /tmp/bark-extractor.service "$SERVICE_FILE"
  rm -f /tmp/bark-extractor.service
  sudo systemctl daemon-reload
  sudo systemctl enable bark-extractor.service
  sudo systemctl start bark-extractor.service
  info "Service installed, enabled, and started"
  info "  Status:  sudo systemctl status bark-extractor"
  info "  Logs:    sudo journalctl -u bark-extractor -f"
else
  warn "systemd not available – skipping service install."
  warn "Start manually:  source .venv/bin/activate && ./BarkExtractor"
fi

# ---------------------------------------------------
# Done
# ---------------------------------------------------
echo ""
echo -e "${BOLD}${GREEN}Setup complete!${RESET}"
echo ""
echo "  Bark Extractor is running as a systemd service."
echo "  Open:  http://localhost:${ACTIVE_PORT}"
echo ""
echo "  Manual start (if not using service):"
echo ""
echo "    source .venv/bin/activate"
echo "    ./BarkExtractor"
echo ""
