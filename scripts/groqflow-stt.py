#!/usr/bin/env python3
"""
groqflow-stt — Speech-to-text via Groq Whisper
Floats a minimal overlay pill (bottom-right), records mic, transcribes, pastes.

Deps: python3-gobject (gi), sounddevice, numpy, requests, gtk-layer-shell
      wl-clipboard (wl-copy/wl-paste), wtype
"""

import os
import sys
import json
import time
import queue
import threading
import subprocess
import wave
import io
import signal

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gtk, Gdk, GLib, GtkLayerShell

import sounddevice as sd
import numpy as np
import requests

# ── Config ────────────────────────────────────────────────────────────────────
CONFIG_FILE = os.path.expanduser("~/.config/groqflow/config.json")

def load_config():
    defaults = {
        "groq_api_key": os.environ.get("GROQ_API_KEY", ""),
        "whisper_model": "whisper-large-v3-turbo",
        "sample_rate": 16000,
        "silence_threshold": 0.01,
        "silence_duration": 1.5,
        "max_duration": 30,
        "paste_method": "wtype",
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                user = json.load(f)
            defaults.update(user)
        except Exception:
            pass
    return defaults

CONFIG = load_config()

# ── Colours ───────────────────────────────────────────────────────────────────
PALETTE = {
    "bg":       "#0e0e10",
    "border":   "#2a2a2e",
    "text":     "#e8e8ec",
    "muted":    "#6b6b78",
    "accent":   "#7c6af7",
    "hot":      "#f25c7c",
    "ok":       "#56d89e",
    "warning":  "#f0a040",
}

CSS = f"""
window {{
    background-color: {PALETTE['bg']};
    border-radius: 12px;
}}
#pill {{
    background-color: {PALETTE['bg']};
    border: 1px solid {PALETTE['border']};
    border-radius: 12px;
    padding: 10px 18px;
}}
#status-label {{
    color: {PALETTE['text']};
    font-family: "JetBrains Mono", "Fira Code", monospace;
    font-size: 13px;
    font-weight: 500;
    letter-spacing: 0.03em;
}}
#hint-label {{
    color: {PALETTE['muted']};
    font-family: "JetBrains Mono", "Fira Code", monospace;
    font-size: 10px;
    margin-top: 2px;
}}
#dot {{
    min-width: 8px;
    min-height: 8px;
    border-radius: 4px;
    background-color: {PALETTE['accent']};
    margin-right: 10px;
}}
#dot.recording {{
    background-color: {PALETTE['hot']};
}}
#dot.done {{
    background-color: {PALETTE['ok']};
}}
#dot.warning {{
    background-color: {PALETTE['warning']};
}}
#wave-bar {{
    min-height: 3px;
    border-radius: 2px;
    background-color: {PALETTE['accent']};
    margin-top: 8px;
    transition: all 100ms;
}}
"""

# ── Error logging ─────────────────────────────────────────────────────────────
LOG_DIR = os.path.expanduser("~/.cache/groqflow")
LOG_FILE = os.path.join(LOG_DIR, "stt-error.log")

def log_error(msg: str):
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}")
    except Exception:
        pass

# ── Overlay pill (gtk-layer-shell) ────────────────────────────────────────────
class StatusPill(Gtk.Window):
    def __init__(self):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.set_decorated(False)
        self.set_app_paintable(True)

        # gtk-layer-shell: proper Wayland overlay, no window decorations
        try:
            GtkLayerShell.init_for_window(self)
            GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
            GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.RIGHT, True)
            GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.BOTTOM, True)
            GtkLayerShell.set_margin(self, GtkLayerShell.Edge.RIGHT, 24)
            GtkLayerShell.set_margin(self, GtkLayerShell.Edge.BOTTOM, 24)
            GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)
            GtkLayerShell.set_namespace(self, "groqflow-stt")
        except Exception as e:
            log_error(f"layer-shell init failed: {e}")
            # Fallback: try to position manually (less reliable on Wayland)
            self.set_keep_above(True)
            self.set_skip_taskbar_hint(True)
            self.set_skip_pager_hint(True)
            self.connect("realize", self._fallback_position)

        self.set_default_size(260, 56)

        # Transparency
        screen = Gdk.Screen.get_default()
        visual = screen.get_rgba_visual()
        if visual:
            self.set_visual(visual)

        # CSS
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS.encode())
        Gtk.StyleContext.add_provider_for_screen(
            screen,
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        # Layout
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.set_name("pill")

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        row.set_valign(Gtk.Align.CENTER)

        self.dot = Gtk.Label(label="")
        self.dot.set_name("dot")
        row.pack_start(self.dot, False, False, 0)

        text_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.status_label = Gtk.Label(label="Ready")
        self.status_label.set_name("status-label")
        self.status_label.set_halign(Gtk.Align.START)
        self.hint_label = Gtk.Label(label="")
        self.hint_label.set_name("hint-label")
        self.hint_label.set_halign(Gtk.Align.START)
        text_col.pack_start(self.status_label, False, False, 0)
        text_col.pack_start(self.hint_label, False, False, 0)
        row.pack_start(text_col, True, True, 0)

        outer.pack_start(row, True, True, 0)

        # Wave bar (audio level indicator)
        self.wave = Gtk.DrawingArea()
        self.wave.set_name("wave-bar")
        self.wave.set_size_request(-1, 3)
        self.wave.connect("draw", self._draw_wave)
        outer.pack_start(self.wave, False, False, 0)

        self.add(outer)
        self._level = 0.0

        self.show_all()
        self.wave.hide()

    def _fallback_position(self, widget):
        """Manual positioning fallback if layer-shell is unavailable."""
        try:
            display = Gdk.Display.get_default()
            gdk_window = self.get_window()
            if gdk_window and display:
                monitor = display.get_monitor_at_window(gdk_window)
                if monitor:
                    geo = monitor.get_geometry()
                    gdk_window.move(geo.width - 260 - 24, geo.height - 56 - 24)
        except Exception:
            pass

    def _draw_wave(self, widget, cr):
        alloc = widget.get_allocation()
        W, H = alloc.width, alloc.height
        r, g, b = 0x7c/255, 0x6a/255, 0xf7/255
        fill = max(0.0, min(1.0, self._level * 6))
        cr.set_source_rgba(r, g, b, 0.25)
        cr.rectangle(0, 0, W, H)
        cr.fill()
        cr.set_source_rgba(r, g, b, 0.9)
        cr.rectangle(0, 0, W * fill, H)
        cr.fill()
        return False

    def set_state(self, state: str, hint: str = ""):
        GLib.idle_add(self._apply_state, state, hint)

    def _apply_state(self, state, hint):
        labels = {
            "idle":       "Ready",
            "listening":  "Listening…",
            "processing": "Transcribing…",
            "done":       "Pasted ✓",
            "error":      "Error",
        }
        dot_classes = {
            "idle":       [],
            "listening":  ["recording"],
            "processing": [],
            "done":       ["done"],
            "error":      ["warning"],
        }
        self.status_label.set_text(labels.get(state, state))
        self.hint_label.set_text(hint)
        ctx = self.dot.get_style_context()
        for cls in ["recording", "done", "warning"]:
            ctx.remove_class(cls)
        for cls in dot_classes.get(state, []):
            ctx.add_class(cls)
        if state == "listening":
            self.wave.show()
        else:
            self.wave.hide()
        return False

    def set_level(self, level: float):
        self._level = level
        GLib.idle_add(self.wave.queue_draw)

    def auto_close(self, delay=1.8):
        GLib.timeout_add(int(delay * 1000), self._close)

    def _close(self):
        Gtk.main_quit()
        return False


# ── Recording ─────────────────────────────────────────────────────────────────
class Recorder:
    def __init__(self, config, pill: StatusPill):
        self.config = config
        self.pill = pill
        self.audio_q: queue.Queue = queue.Queue()
        self.stop_flag = threading.Event()
        self.frames = []
        self.sr = config["sample_rate"]

    def _callback(self, indata, frames, time_info, status):
        chunk = indata[:, 0].copy()
        level = float(np.abs(chunk).mean())
        self.pill.set_level(level)
        self.audio_q.put((chunk, level))

    def record(self):
        self.pill.set_state("listening", "speak now — silence stops")
        silence_count = 0
        silence_frames = int(
            self.config["silence_duration"] * self.sr / 1024
        )
        max_frames = int(self.config["max_duration"] * self.sr / 1024)
        total = 0
        got_speech = False

        with sd.InputStream(
            samplerate=self.sr,
            channels=1,
            dtype="float32",
            blocksize=1024,
            callback=self._callback,
        ):
            while not self.stop_flag.is_set():
                try:
                    chunk, level = self.audio_q.get(timeout=0.1)
                except queue.Empty:
                    continue
                self.frames.append(chunk)
                total += 1
                if level > self.config["silence_threshold"]:
                    got_speech = True
                    silence_count = 0
                elif got_speech:
                    silence_count += 1
                    if silence_count >= silence_frames:
                        break
                if total >= max_frames:
                    break

        if not self.frames or not got_speech:
            return None
        return np.concatenate(self.frames)

    def to_wav_bytes(self, audio):
        pcm = (audio * 32767).astype(np.int16)
        with io.BytesIO() as f:
            with wave.open(f, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(self.sr)
                wf.writeframes(pcm.tobytes())
            return f.getvalue()

    def stop(self):
        self.stop_flag.set()


# ── Groq transcription ────────────────────────────────────────────────────────
def transcribe(audio_bytes, config):
    api_key = config["groq_api_key"]
    if not api_key:
        raise ValueError("GROQ_API_KEY not set. Add it to ~/.config/groqflow/config.json")
    resp = requests.post(
        "https://api.groq.com/openai/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {api_key}"},
        files={"file": ("audio.wav", audio_bytes, "audio/wav")},
        data={
            "model": config["whisper_model"],
            "response_format": "text",
            "language": "en",
        },
        timeout=30,
    )
    resp.raise_for_status()
    text = resp.text.strip()
    if text.startswith("{"):
        data = json.loads(text)
        text = data.get("text", "").strip()
    return text


# ── Paste helpers ─────────────────────────────────────────────────────────────
def paste_text(text, method):
    subprocess.run(
        ["wl-copy", "--", text],
        check=True,
        input=None,
    )
    time.sleep(0.05)

    if method == "wtype":
        subprocess.run(["wtype", "--", text], check=False)
    else:
        subprocess.run(["wtype", "-M", "ctrl", "-P", "v", "-m", "ctrl"], check=False)


# ── Main flow ─────────────────────────────────────────────────────────────────
def run():
    config = CONFIG
    if not config["groq_api_key"]:
        config["groq_api_key"] = os.environ.get("GROQ_API_KEY", "")

    pill = StatusPill()

    # Signal handlers MUST be in the main thread
    recorder_ref = [None]

    def _sig(signum, frame):
        if recorder_ref[0]:
            recorder_ref[0].stop()

    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)

    def worker():
        rec = Recorder(config, pill)
        recorder_ref[0] = rec

        try:
            audio = rec.record()
            if audio is None:
                pill.set_state("error", "no speech detected")
                pill.auto_close(2.0)
                return

            pill.set_state("processing", config["whisper_model"])
            wav = rec.to_wav_bytes(audio)
            text = transcribe(wav, config)

            if not text:
                pill.set_state("error", "empty transcription")
                pill.auto_close(2.0)
                return

            paste_text(text, config["paste_method"])
            short = text[:48] + ("…" if len(text) > 48 else "")
            pill.set_state("done", short)
            pill.auto_close(2.0)

        except Exception as e:
            msg = str(e)[:60]
            log_error(f"worker error: {msg}")
            pill.set_state("error", msg)
            pill.auto_close(3.5)

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    Gtk.main()


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        log_error(f"startup error: {e}")
        subprocess.run(
            ["notify-send", "-u", "critical", "groqflow-stt", str(e)],
            check=False,
        )
        sys.exit(1)
