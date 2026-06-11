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

# ── 3. Install scripts ─────────────────────────────────────────────────────
INSTALL_DIR="$HOME/.local/bin"
CONFIG_DIR="$HOME/.config/groqflow"
mkdir -p "$INSTALL_DIR" "$CONFIG_DIR"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/scripts"

info "Installing scripts to $INSTALL_DIR…"
install -m755 "$SCRIPT_DIR/groqflow-stt.py"     "$INSTALL_DIR/groqflow-stt"
install -m755 "$SCRIPT_DIR/groqflow-enhance.py" "$INSTALL_DIR/groqflow-enhance"

# Wrapper shebang fix — make them directly executable
sed -i '1s|.*|#!/usr/bin/env python3|' "$INSTALL_DIR/groqflow-stt"
sed -i '1s|.*|#!/usr/bin/env python3|' "$INSTALL_DIR/groqflow-enhance"

success "Scripts installed"

# ── 4. Config ──────────────────────────────────────────────────────────────
if [ ! -f "$CONFIG_DIR/config.json" ]; then
    info "Creating config at $CONFIG_DIR/config.json…"
    cat > "$CONFIG_DIR/config.json" << 'EOF'
{
  "groq_api_key": "",
  "whisper_model": "whisper-large-v3-turbo",
  "llm_model": "llama-3.3-70b-versatile",
  "sample_rate": 16000,
  "silence_threshold": 0.01,
  "silence_duration": 1.5,
  "max_duration": 30,
  "paste_method": "wtype",
  "enhance_mode": "fix"
}
EOF
    success "Config created — edit $CONFIG_DIR/config.json to add your GROQ_API_KEY"
else
    warn "Config already exists at $CONFIG_DIR/config.json — skipping"
fi

# ── 5. Sway keybindings ────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}Add these keybindings to your Sway config${RESET}"
echo "  (~/.config/sway/config or ~/.config/sway/config.d/groqflow.conf)"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
cat << 'SWAYCONF'
# ── groqflow ──────────────────────────────────────────────────────
# Super+X   →  record speech and paste transcription
bindsym --no-repeat $mod+x exec groqflow-stt

# Super+Z   →  fix/enhance highlighted text (default: fix grammar)
bindsym --no-repeat $mod+z exec groqflow-enhance

# Super+Shift+F  →  enhance as formal prose
bindsym $mod+Shift+f exec groqflow-enhance formal

# Super+Ctrl+C   →  make concise
bindsym $mod+Ctrl+c exec groqflow-enhance concise
SWAYCONF
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Offer to append automatically
read -rp "Append these bindings to ~/.config/sway/config now? [y/N] " reply
if [[ "${reply,,}" == "y" ]]; then
    SWAY_CONF="$HOME/.config/sway/config"
    if [ -f "$SWAY_CONF" ]; then
        cat >> "$SWAY_CONF" << 'EOF'

# ── groqflow ──────────────────────────────────────────────────────
bindsym --no-repeat $mod+x exec groqflow-stt
bindsym --no-repeat $mod+z exec groqflow-enhance
bindsym $mod+Shift+f exec groqflow-enhance formal
bindsym $mod+Ctrl+c exec groqflow-enhance concise
EOF
        success "Bindings appended — run: swaymsg reload"
    else
        warn "$SWAY_CONF not found — add bindings manually"
    fi
fi

# ── 6. API key prompt ──────────────────────────────────────────────────────
echo ""
CURRENT_KEY=$(python3 -c "import json,os; d=json.load(open(os.path.expanduser('~/.config/groqflow/config.json'))); print(d.get('groq_api_key',''))" 2>/dev/null || echo "")
if [ -z "$CURRENT_KEY" ]; then
    warn "No GROQ_API_KEY set yet."
    read -rsp "Paste your Groq API key (or press Enter to skip): " apikey
    echo ""
    if [ -n "$apikey" ]; then
        python3 - "$apikey" << 'EOF'
import json, sys, os
f = os.path.expanduser("~/.config/groqflow/config.json")
d = json.load(open(f))
d["groq_api_key"] = sys.argv[1]
json.dump(d, open(f, "w"), indent=2)
print("Key saved.")
EOF
        success "API key saved to config"
    fi
else
    success "API key already configured"
fi

echo ""
echo -e "${GREEN}${BOLD}groqflow installed!${RESET}"
echo ""
echo "  groqflow-stt        →  speak and paste"
echo "  groqflow-enhance    →  fix highlighted text"
echo "  groqflow-enhance formal   →  make it formal"
echo "  groqflow-enhance concise  →  make it concise"
echo ""
echo "  Config: $CONFIG_DIR/config.json"
echo "  Get a free Groq API key at: https://console.groq.com"
echo ""
