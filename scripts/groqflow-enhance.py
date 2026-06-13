#!/usr/bin/env python3
"""
groqflow-enhance — Fix / enhance highlighted text using Groq LLM.
Floats a minimal overlay pill (bottom-right), reads Wayland PRIMARY selection,
sends it to Groq, then types the result back replacing the selection.

Deps: python3-gobject (gi), requests, gtk-layer-shell, wl-clipboard, wtype
"""

import os
import sys
import json
import time
import threading
import subprocess
import atexit

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gtk, Gdk, GLib, GtkLayerShell

import requests

# ── Single-instance lock ──────────────────────────────────────────────────────
LOCK_FILE = "/tmp/groqflow-enhance.lock"

def acquire_lock():
    """Acquire the singleton lock. If another instance is running, exit."""
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE) as f:
                pid = int(f.read().strip())
            os.kill(pid, 0)  # Check if still alive
            sys.exit(0)
        except (ValueError, ProcessLookupError, FileNotFoundError):
            try:
                os.remove(LOCK_FILE)
            except FileNotFoundError:
                pass
    with open(LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))

def release_lock():
    """Remove the lock file only if we own it."""
    try:
        if os.path.exists(LOCK_FILE):
            with open(LOCK_FILE) as f:
                if int(f.read().strip()) == os.getpid():
                    os.remove(LOCK_FILE)
    except (ValueError, FileNotFoundError):
        pass

atexit.register(release_lock)

# ── Config ────────────────────────────────────────────────────────────────────
CONFIG_FILE = os.path.expanduser("~/.config/groqflow/config.json")

DEFAULTS = {
    "llm_model": "llama-3.3-70b-versatile",
    "enhance_mode": "fix",
    "custom_prompt": "",
    "paste_method": "ctrl+v",
}

def load_config():
    config = dict(DEFAULTS)
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                user = json.load(f)
            # Only merge non-sensitive settings from config file
            for k in DEFAULTS:
                if k in user:
                    config[k] = user[k]
        except Exception:
            pass
    return config

CONFIG = load_config()

# ── Mode prompts ──────────────────────────────────────────────────────────────
MODE_PROMPTS = {
    "fix": (
        "You are a text-editing engine. Your sole function is to polish input text. "
        "You have no personality. You do not engage in conversation. "
        "Fix grammar, spelling, and punctuation. Improve clarity and flow. "
        "Preserve the original meaning, tone, and approximate length. "
        "CRITICAL: NEVER answer, respond to, or comment on the text. "
        "If the text is a question, polish it — do NOT answer it. "
        "If it is a statement, polish it — do NOT respond to it. "
        "DO NOT add introductions, explanations, summaries, or pleasantries. "
        "Output ONLY the polished text. Nothing else. No quotes, no preamble, no suffix.\n"
        "Example input: 'wht is the meaning of life'\n"
        "Example output: 'What is the meaning of life?'"
    ),
    "formal": (
        "You are a text-editing engine. Your sole function is to rewrite text formally. "
        "Rewrite the text in a clear, professional tone. Fix errors. Keep meaning intact. "
        "CRITICAL: NEVER answer, respond to, or comment on the text. "
        "Output ONLY the rewritten text. Nothing else.\n"
        "Example input: 'hey can u check this out'\n"
        "Example output: 'Could you please review this?'"
    ),
    "concise": (
        "You are a text-editing engine. Your sole function is to condense text. "
        "Make the text shorter and punchier without losing meaning. "
        "Remove filler words and redundancies. "
        "CRITICAL: NEVER answer, respond to, or comment on the text. "
        "Output ONLY the condensed text. Nothing else.\n"
        "Example input: 'I just wanted to let you know that I think we should maybe consider\n"
        "Example output: 'We should consider'"
    ),
    "expand": (
        "You are a text-editing engine. Your sole function is to expand text. "
        "Expand the text with more detail and context. Keep the same tone. "
        "CRITICAL: NEVER answer, respond to, or comment on the text. "
        "Output ONLY the expanded text. Nothing else."
    ),
    "bullet": (
        "You are a text-editing engine. Your sole function is to convert text to bullet points. "
        "Convert the text into a clean bullet-point list. "
        "CRITICAL: NEVER answer, respond to, or comment on the text. "
        "Output ONLY the bullet list. Nothing else."
    ),
    "custom": None,
}

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
LOG_FILE = os.path.join(LOG_DIR, "enhance-error.log")

def log_error(msg: str):
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}")
    except Exception:
        pass

# ── Overlay pill (gtk-layer-shell) ────────────────────────────────────────────
class StatusPill(Gtk.Window):
    def __init__(self, mode: str):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.set_decorated(False)
        self.set_app_paintable(True)

        # gtk-layer-shell
        try:
            GtkLayerShell.init_for_window(self)
            GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
            GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.LEFT, True)
            GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.BOTTOM, True)
            GtkLayerShell.set_margin(self, GtkLayerShell.Edge.LEFT, 24)
            GtkLayerShell.set_margin(self, GtkLayerShell.Edge.BOTTOM, 24)
            GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)
            GtkLayerShell.set_namespace(self, "groqflow-enhance")
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


# ── Clipboard helpers ─────────────────────────────────────────────────────────
def get_selection():
    try:
        r = subprocess.run(
            ["wl-paste", "--primary", "--no-newline"],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout
    except Exception:
        pass
    try:
        r = subprocess.run(
            ["wl-paste", "--no-newline"],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout
    except Exception:
        pass
    return ""


def replace_selection(new_text, paste_method):
    subprocess.run(["wl-copy", "--", new_text], check=True)
    time.sleep(0.08)
    if paste_method == "wtype":
        subprocess.run(["wtype", "--", new_text], check=False)
    else:
        subprocess.run(["wtype", "-M", "ctrl", "-k", "v", "-m", "ctrl"], check=False)


# ── Groq LLM call ─────────────────────────────────────────────────────────────
def enhance_text(text, api_key, config):
    if not api_key:
        raise ValueError("GROQ_API_KEY not set. Export it in your shell environment.")

    mode = config.get("enhance_mode", "fix")
    system_prompt = MODE_PROMPTS.get(mode)
    if mode == "custom" or system_prompt is None:
        system_prompt = config.get("custom_prompt") or MODE_PROMPTS["fix"]

    payload = {
        "model": config["llm_model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
        "temperature": 0.2,
        "max_tokens": 2048,
    }
    resp = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"].strip()


# ── Main ──────────────────────────────────────────────────────────────────────
def run():
    config = CONFIG
    api_key = os.environ.get("GROQ_API_KEY", "")

    if len(sys.argv) > 1:
        mode_arg = sys.argv[1].lower()
        if mode_arg in MODE_PROMPTS:
            config["enhance_mode"] = mode_arg

    mode = config.get("enhance_mode", "fix")
    pill = StatusPill(mode)

    def worker():
        try:
            pill.set_text("Reading…")
            text = get_selection()
            if not text.strip():
                pill.set_text("No selection")
                pill.auto_close(3.0)
                return

            short_in = text[:45] + ("…" if len(text) > 45 else "")
            pill.set_text("Enhancing…")
            result = enhance_text(text, api_key, config)

            if not result:
                pill.set_text("No response")
                pill.auto_close(2.5)
                return

            pill.set_text("Replacing…")
            replace_selection(result.rstrip(), config["paste_method"])

            short_out = result[:48] + ("…" if len(result) > 48 else "")
            pill.set_text("Done")
            pill.auto_close(2.0)

        except Exception as e:
            msg = str(e)[:60]
            log_error(f"worker error: {msg}")
            pill.set_text("Error")
            pill.auto_close(4.0)

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    Gtk.main()


if __name__ == "__main__":
    acquire_lock()
    try:
        run()
    except Exception as e:
        log_error(f"startup error: {e}")
        subprocess.run(
            ["notify-send", "-u", "critical", "groqflow-enhance", str(e)],
            check=False,
        )
        sys.exit(1)
