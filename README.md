# groqflow

Minimal, blazing-fast AI voice & text tools for **Fedora Sway**.  
Two tools. Two hotkeys. No GUI. Just a tiny floating pill that appears, does its job, and vanishes.

```
Super+X   →  record voice → transcribe (Groq Whisper) → paste at cursor
Super+Z   →  fix highlighted text (grammar, clarity) → replace in place
Super+Shift+F →  rewrite as formal prose
Super+Ctrl+C  →  make concise
```

---

## What it does

### `groqflow-stt` — Speech to text
Press the hotkey. Speak. A minimal floating status pill appears in the corner showing waveform activity. Silence for ~1.5 s auto-stops the recording. Groq Whisper transcribes it. Text is typed at your cursor. Works in **any** app — terminal, browser, editor, chat.

### `groqflow-enhance` — Text enhancer  
Highlight text anywhere. Press the hotkey. The pill appears, sends your text to a Groq LLM, and types the result back over your selection. Works with any Wayland app that exposes PRIMARY selection.

Modes (pass as argument or set in config):

| Mode | Command | Effect |
|------|---------|--------|
| `fix` | `groqflow-enhance` | Fix grammar & spelling |
| `formal` | `groqflow-enhance formal` | Rewrite professionally |
| `concise` | `groqflow-enhance concise` | Make shorter & punchier |
| `expand` | `groqflow-enhance expand` | Add more detail |
| `bullet` | `groqflow-enhance bullet` | Convert to bullet list |
| `custom` | set in config | Your own system prompt |

---

## Install

### 1. Prerequisites

```bash
sudo dnf install -y \
    python3-gobject \
    gtk-layer-shell \
    wl-clipboard \
    wtype \
    python3-pip

pip3 install --user sounddevice numpy requests
```

### 2. Run installer

```bash
git clone … groqflow   # or unzip the folder
cd groqflow
bash install.sh
```

The installer will:
- Copy scripts to `~/.local/bin/`
- Create `~/.config/groqflow/config.json`
- Offer to append Sway keybindings
- Prompt for your Groq API key

### 3. Get a Groq API key

Free at **https://console.groq.com** — takes 30 seconds.

Add it to `~/.config/groqflow/config.json`:

```json
{
  "groq_api_key": "gsk_..."
}
```

Or set the env var: `export GROQ_API_KEY=gsk_...` in your shell rc.

### 4. Sway keybindings

```
# ~/.config/sway/config
bindsym --no-repeat $mod+x exec groqflow-stt
bindsym --no-repeat $mod+z exec groqflow-enhance
bindsym $mod+Shift+f exec groqflow-enhance formal
bindsym $mod+Ctrl+c exec groqflow-enhance concise
```

Then `swaymsg reload`.

---

## Config reference

`~/.config/groqflow/config.json`

| Key | Default | Description |
|-----|---------|-------------|
| `groq_api_key` | `""` | Your Groq API key |
| `whisper_model` | `whisper-large-v3-turbo` | STT model (fastest) |
| `llm_model` | `llama-3.3-70b-versatile` | LLM for text enhancement |
| `sample_rate` | `16000` | Microphone sample rate (Hz) |
| `silence_threshold` | `0.01` | RMS level below which = silence |
| `silence_duration` | `1.5` | Seconds of silence before auto-stop |
| `max_duration` | `30` | Max recording length (seconds) |
| `paste_method` | `wtype` | How to inject text: `wtype` or `clipboard` |
| `enhance_mode` | `fix` | Default enhance mode |
| `custom_prompt` | `""` | Used when `enhance_mode` is `custom` |

### Tuning silence detection

If it cuts off too early → increase `silence_duration` or lower `silence_threshold`.  
If it waits too long after you stop → decrease `silence_duration`.  
Noisy mic → increase `silence_threshold` (try `0.02`–`0.05`).

### Paste method

- **`wtype`** (default) — types text character by character via Wayland. Works everywhere including web inputs. Requires `wtype`.
- **`clipboard`** — copies to clipboard and sends Ctrl+V. Faster for long text but requires the target app to accept paste.

---

## Troubleshooting

**"no speech detected"**  
→ Check mic permissions. Run `pactl info` to see default source.  
→ Lower `silence_threshold` in config.

**Text doesn't paste in browser**  
→ Browsers sometimes block `wtype`. Try `"paste_method": "clipboard"` in config, or use `xdotool` if on X11.

**Overlay pill doesn't appear**  
→ Ensure `python3-gobject` is installed: `python3 -c "import gi"`  
→ Ensure `gtk-layer-shell` is installed: `rpm -q gtk-layer-shell`  
→ The pill uses gtk-layer-shell to float above windows without stealing focus.

**`wl-paste --primary` returns empty**  
→ Highlight text *before* pressing the shortcut. The PRIMARY selection is set on mouse-release.

---

## Architecture

```
groqflow-stt
  sounddevice (mic) → numpy float32 array
  → wave bytes → Groq /audio/transcriptions (whisper-large-v3-turbo)
  → text → wl-copy + wtype → cursor

groqflow-enhance
  wl-paste --primary → text
  → Groq /chat/completions (llama-3.3-70b-versatile)
  → enhanced text → wl-copy + wtype → replaces selection

Both scripts use gtk-layer-shell to anchor a minimal overlay
pill in the bottom-right corner (no window decorations, no focus stealing).
Auto-closes after 1.5–4 s depending on outcome.
```

---

## Models used

| Task | Model | Why |
|------|-------|-----|
| STT | `whisper-large-v3-turbo` | Fastest Whisper on Groq (~0.3 s) |
| LLM | `llama-3.3-70b-versatile` | Best quality/speed on Groq free tier |

Both are free on Groq's generous free tier (rate limits apply for heavy use).
