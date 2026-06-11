# groqflow

Speech-to-text and text enhancement on Sway, via [Groq](https://console.groq.com).

```
Super+X       →  speak → transcribe → paste
Super+Z       →  fix highlighted text
Super+Shift+F →  formal prose
Super+Ctrl+C  →  concise rewrite
```

## What

Two tools that run on a hotkey, show a small overlay pill in the corner, and disappear when done.

**`groqflow-stt`** — press the key, speak, it transcribes via Groq Whisper and types the result at your cursor.

**`groqflow-enhance`** — highlight text, press the key, it sends the selection to a Groq LLM and replaces it with the improved version.

Modes:

| Argument | Effect |
|----------|--------|
| *(none)* | Fix grammar and spelling |
| `formal` | Professional tone |
| `concise` | Shorter and punchier |
| `expand` | Add detail |
| `bullet` | Bullet-point list |
| `custom` | Uses your prompt from config |

## Install

Requires Fedora + Sway.

### 1. Dependencies

```
sudo dnf install -y python3-gobject gtk-layer-shell wl-clipboard wtype python3-pip
pip3 install --user sounddevice numpy requests
```

### 2. Run the installer

```
git clone https://github.com/WesamAbadi/groqflow.git
cd groqflow
bash install.sh
```

It copies the scripts to `~/.local/bin/`, creates a config file, and asks if you want the Sway keybindings appended.

### 3. API key

Get a key at https://console.groq.com (free tier works). Add it to `~/.config/groqflow/config.json` or set `GROQ_API_KEY` in your environment.

## Config

`~/.config/groqflow/config.json`

| Key | Default | Notes |
|-----|---------|-------|
| `groq_api_key` | `""` | |
| `whisper_model` | `whisper-large-v3-turbo` | |
| `llm_model` | `llama-3.3-70b-versatile` | |
| `sample_rate` | `16000` | |
| `silence_threshold` | `0.01` | RMS — lower is more sensitive |
| `silence_duration` | `1.5` | Seconds of quiet before auto-stop |
| `max_duration` | `30` | Max recording length, seconds |
| `paste_method` | `wtype` | `wtype` for direct input, `clipboard` for Ctrl+V |
| `enhance_mode` | `fix` | Default mode |
| `custom_prompt` | `""` | Used when mode is `custom` |

## Troubleshooting

**No speech detected** — check your mic in `pactl info`, or lower `silence_threshold` in config.

**Pill doesn't appear** — make sure `gtk-layer-shell` is installed (`rpm -q gtk-layer-shell`).

**Selection empty** — highlight text first, then press the key. Wayland PRIMARY selection is set on mouse release.

**`wtype` doesn't work in browsers** — switch `paste_method` to `clipboard` in config.
