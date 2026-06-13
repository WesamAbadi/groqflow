#!/usr/bin/env bash
# groqflow installer for Fedora + Sway
# Run: bash install.sh
set -euo pipefail

BOLD="\033[1m"
GREEN="\033[32m"
YELLOW="\033[33m"
RED="\033[31m"
RESET="\033[0m"

info()    { echo -e "${BOLD}→${RESET} $*"; }
success() { echo -e "${GREEN}✓${RESET} $*"; }
warn()    { echo -e "${YELLOW}!${RESET} $*"; }
error()   { echo -e "${RED}✗${RESET} $*" >&2; }
die()     { error "$*"; exit 1; }

echo ""
echo -e "${BOLD}groqflow — AI voice & text on Sway${RESET}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# ── 1. System deps ─────────────────────────────────────────────────────────
info "Installing system dependencies via dnf…"
sudo dnf install -y \
    python3-gobject \
    python3-pip \
    wl-clipboard \
    wtype \
    gtk-layer-shell \
    libnotify \
    python3-devel \
    portaudio-devel \
    gcc \
    2>/dev/null || warn "Some dnf packages may have failed — continuing"

# ── 2. Python deps ─────────────────────────────────────────────────────────
info "Installing Python packages…"
pip3 install --user --quiet \
    sounddevice \
    numpy \
    requests \
    2>/dev/null || die "pip3 install failed"

success "Dependencies installed"

# ── 3. Make scripts executable ─────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/scripts"
chmod +x "$SCRIPT_DIR/groqflow-stt.py" "$SCRIPT_DIR/groqflow-enhance.py"
success "Scripts made executable"

# ── 4. API key ─────────────────────────────────────────────────────────────
echo ""
if [ -z "${GROQ_API_KEY:-}" ]; then
    warn "GROQ_API_KEY not set in your environment."
    echo "  Add this to your shell profile (~/.bashrc, ~/.zshrc, etc.):"
    echo ""
    echo "    export GROQ_API_KEY=\"your-key-here\""
    echo ""
    echo "  Get a free key at: https://console.groq.com"
else
    success "GROQ_API_KEY is set"
fi

# ── 5. Sway keybindings ────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}Add these keybindings to your Sway config${RESET}"
echo "  (~/.config/sway/config or ~/.config/sway/config.d/groqflow.conf)"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "# ── groqflow ──────────────────────────────────────────────────────"
echo "# Super+X   →  record speech and paste transcription"
echo "bindsym --no-repeat \$mod+x exec \$SHELL -i -c \"$SCRIPT_DIR/groqflow-stt.py\""
echo ""
echo "# Super+Z   →  fix/enhance highlighted text"
echo "bindsym --no-repeat \$mod+z exec \$SHELL -i -c \"$SCRIPT_DIR/groqflow-enhance.py\""
echo ""
echo "# Super+Shift+F  →  enhance as formal prose"
echo "bindsym \$mod+Shift+f exec \$SHELL -i -c \"$SCRIPT_DIR/groqflow-enhance.py formal\""
echo ""
echo "# Super+Ctrl+C   →  make concise"
echo "bindsym \$mod+Ctrl+c exec \$SHELL -i -c \"$SCRIPT_DIR/groqflow-enhance.py concise\""
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo -e "${GREEN}${BOLD}groqflow ready!${RESET}"
echo "  Run: swaymsg reload"
echo ""
