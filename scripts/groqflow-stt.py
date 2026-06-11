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
    "text":     "#d4d4d8",
}
CSS = f"""
#pill {{
    background-color: {PALETTE['bg']};
    border-radius: 6px;
    padding: 6px 16px;
}}
#text {{
    color: {PALETTE['text']};
    font-family: "JetBrains Mono", "Fira Code", monospace;
    font-size: 11px;
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
            GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.LEFT, True)
            GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.BOTTOM, True)
            GtkLayerShell.set_margin(self, GtkLayerShell.Edge.LEFT, 24)
            GtkLayerShell.set_margin(self, GtkLayerShell.Edge.BOTTOM, 24)
            GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)
            GtkLayerShell.set_namespace(self, "groqflow-stt")
        except Exception as e:
            log_error(f"layer-shell init failed: {e}")
            self.set_keep_above(True)
            self.set_skip_taskbar_hint(True)
            self.set_skip_pager_hint(True)
            self.connect("realize", self._fallback_position)

        # Transparency
        screen = Gdk.Screen.get_default()
        visual = screen.get_rgba_visual()
        if visual:
            self.set_visual(visual)

        # CSS
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS.encode())
        Gtk.StyleContext.add_provider_for_screen(
            screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        # Wrapper box for CSS #pill styling
        pill = Gtk.Box()
        pill.set_name("pill")

        self.label = Gtk.Label(label="")
        self.label.set_name("text")
        self.label.set_halign(Gtk.Align.CENTER)
        pill.add(self.label)
        self.add(pill)

        self._last_text = ""
        self._debounce_id = 0
        self._shown = False

    def _fallback_position(self, widget):
        try:
            display = Gdk.Display.get_default()
            gdk_window = self.get_window()
            if gdk_window and display:
                monitor = display.get_monitor_at_window(gdk_window)
                if monitor:
                    geo = monitor.get_geometry()
                    label_h = self.label.get_allocation().height
                    header_height = label_h + 14 if label_h > 0 else 28
                    gdk_window.move(24, geo.height - header_height - 24)
        except Exception:
            pass

    def set_text(self, text: str):
        """Set label text (debounced via idle_add for thread safety)."""
        self._last_text = text
        GLib.idle_add(self._schedule_debounce)

    def _schedule_debounce(self):
        if self._debounce_id:
            GLib.source_remove(self._debounce_id)
        self._debounce_id = GLib.timeout_add(120, self._apply_text)
        return False

    def _apply_text(self):
        self.label.set_text(self._last_text)
        if not self._shown:
            self.show_all()
            self._shown = True
        self._debounce_id = 0
        return False

    def auto_close(self, delay=1.0):
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
        
        self.audio_q.put((chunk, level))

    def record(self):
        self.pill.set_text("Listening…")
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
                pill.set_text("No speech")
                pill.auto_close(2.0)
                return

            pill.set_text("Transcribing…")
            wav = rec.to_wav_bytes(audio)
            text = transcribe(wav, config)

            if not text:
                pill.set_text("No result")
                pill.auto_close(2.0)
                return

            paste_text(text, config["paste_method"])
            short = text[:48] + ("…" if len(text) > 48 else "")
            pill.set_text("Done")
            pill.auto_close(2.0)

        except Exception as e:
            msg = str(e)[:60]
            log_error(f"worker error: {msg}")
            pill.set_text("Error")
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
