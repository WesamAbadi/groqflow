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

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gtk, Gdk, GLib, GtkLayerShell

import requests

# ── Config ────────────────────────────────────────────────────────────────────
CONFIG_FILE = os.path.expanduser("~/.config/groqflow/config.json")

def load_config():
    defaults = {
        "groq_api_key": os.environ.get("GROQ_API_KEY", ""),
        "llm_model": "llama-3.3-70b-versatile",
        "enhance_mode": "fix",
        "custom_prompt": "",
        "paste_method": "ctrl+v",
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

# ── Mode prompts ──────────────────────────────────────────────────────────────
MODE_PROMPTS = {
    "fix": (
        "You are an editor, not a chatbot. "
        "Polish the provided text: fix grammar, spelling, and punctuation; "
        "improve clarity and flow; tighten wording. "
        "Preserve the original meaning, tone, and approximate length. "
        "IMPORTANT: If the text is a question, polish the question — do NOT answer it. "
        "If it is a statement, polish the statement — do NOT respond to it. "
        "Return ONLY the polished text — no explanation, no quotes, no preamble."
    ),
    "formal": (
        "Rewrite the text in a clear, professional tone. "
        "Fix errors. Keep meaning intact. "
        "Return ONLY the rewritten text."
    ),
    "concise": (
        "Make the text shorter and punchier without losing meaning. "
        "Remove filler words and redundancies. "
        "Return ONLY the result."
    ),
    "expand": (
        "Expand the text with more detail and context. Keep the same tone. "
        "Return ONLY the expanded text."
    ),
    "bullet": (
        "Convert the text into a clean bullet-point list. "
        "Return ONLY the bullet list."
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
def enhance_text(text, config):
    api_key = config["groq_api_key"]
    if not api_key:
        raise ValueError("GROQ_API_KEY not set in ~/.config/groqflow/config.json")

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
        "temperature": 0.3,
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
    if not config["groq_api_key"]:
        config["groq_api_key"] = os.environ.get("GROQ_API_KEY", "")

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
            result = enhance_text(text, config)

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
    try:
        run()
    except Exception as e:
        log_error(f"startup error: {e}")
        subprocess.run(
            ["notify-send", "-u", "critical", "groqflow-enhance", str(e)],
            check=False,
        )
        sys.exit(1)
