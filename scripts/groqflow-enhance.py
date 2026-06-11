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

# ── Mode prompts ──────────────────────────────────────────────────────────────
MODE_PROMPTS = {
    "fix": (
        "You are a silent text editor. Fix grammar, spelling, and punctuation. "
        "Keep the same tone, style, and length. "
        "Return ONLY the corrected text — no explanation, no quotes, no preamble."
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
#dot.processing {{
    background-color: {PALETTE['accent']};
}}
#dot.done {{
    background-color: {PALETTE['ok']};
}}
#dot.warning {{
    background-color: {PALETTE['warning']};
}}
#progress {{
    min-height: 2px;
    border-radius: 1px;
    margin-top: 8px;
    background-color: {PALETTE['border']};
}}
#progress-fill {{
    min-height: 2px;
    border-radius: 1px;
    background-color: {PALETTE['accent']};
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

        # gtk-layer-shell: proper Wayland overlay, no window decorations
        try:
            GtkLayerShell.init_for_window(self)
            GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
            GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.RIGHT, True)
            GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.BOTTOM, True)
            GtkLayerShell.set_margin(self, GtkLayerShell.Edge.RIGHT, 24)
            GtkLayerShell.set_margin(self, GtkLayerShell.Edge.BOTTOM, 24)
            GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)
            GtkLayerShell.set_namespace(self, "groqflow-enhance")
        except Exception as e:
            log_error(f"layer-shell init failed: {e}")
            # Fallback: try to position manually (less reliable on Wayland)
            self.set_keep_above(True)
            self.set_skip_taskbar_hint(True)
            self.set_skip_pager_hint(True)
            self.connect("realize", self._fallback_position)

        self.set_default_size(280, 58)

        # Transparency
        screen = Gdk.Screen.get_default()
        visual = screen.get_rgba_visual()
        if visual:
            self.set_visual(visual)

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS.encode())
        Gtk.StyleContext.add_provider_for_screen(
            screen, provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.set_name("pill")

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        row.set_valign(Gtk.Align.CENTER)

        self.dot = Gtk.Label(label="")
        self.dot.set_name("dot")
        row.pack_start(self.dot, False, False, 0)

        text_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.status_label = Gtk.Label(label=f"Enhancing ({mode})…")
        self.status_label.set_name("status-label")
        self.status_label.set_halign(Gtk.Align.START)
        self.hint_label = Gtk.Label(label="")
        self.hint_label.set_name("hint-label")
        self.hint_label.set_halign(Gtk.Align.START)
        text_col.pack_start(self.status_label, False, False, 0)
        text_col.pack_start(self.hint_label, False, False, 0)
        row.pack_start(text_col, True, True, 0)

        outer.pack_start(row, True, True, 0)

        # Indeterminate progress bar
        prog_bg = Gtk.Box()
        prog_bg.set_name("progress")
        self.prog_fill = Gtk.Box()
        self.prog_fill.set_name("progress-fill")
        self.prog_fill.set_size_request(0, 2)
        prog_bg.add(self.prog_fill)
        outer.pack_start(prog_bg, False, False, 0)

        self.add(outer)
        self._anim_pos = 0.0
        self._anim_running = False

        self.show_all()

    def _fallback_position(self, widget):
        """Manual positioning fallback if layer-shell is unavailable."""
        try:
            display = Gdk.Display.get_default()
            gdk_window = self.get_window()
            if gdk_window and display:
                monitor = display.get_monitor_at_window(gdk_window)
                if monitor:
                    geo = monitor.get_geometry()
                    gdk_window.move(geo.width - 280 - 24, geo.height - 58 - 24)
        except Exception:
            pass

    def start_progress(self):
        self._anim_running = True
        GLib.timeout_add(40, self._tick_progress)

    def _tick_progress(self):
        if not self._anim_running:
            return False
        self._anim_pos = (self._anim_pos + 0.015) % 1.3
        alloc = self.get_allocation()
        W = max(alloc.width - 36, 100)
        pulse = abs(((self._anim_pos % 1.0) - 0.5) * 2)
        self.prog_fill.set_size_request(int(W * 0.15 + W * 0.35 * pulse), 2)
        return True

    def set_state(self, state: str, hint: str = ""):
        GLib.idle_add(self._apply_state, state, hint)

    def _apply_state(self, state, hint):
        labels = {
            "reading":    "Reading selection…",
            "processing": "Thinking…",
            "pasting":    "Replacing text…",
            "done":       "Done ✓",
            "error":      "Error",
        }
        self.status_label.set_text(labels.get(state, state))
        self.hint_label.set_text(hint)
        ctx = self.dot.get_style_context()
        for cls in ["processing", "done", "warning"]:
            ctx.remove_class(cls)
        if state in ("reading", "processing", "pasting"):
            ctx.add_class("processing")
            if not self._anim_running:
                self.start_progress()
        elif state == "done":
            ctx.add_class("done")
            self._anim_running = False
            self.prog_fill.set_size_request(0, 0)
        elif state == "error":
            ctx.add_class("warning")
            self._anim_running = False
        return False

    def auto_close(self, delay=1.5):
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
            pill.set_state("reading", "from PRIMARY selection")
            text = get_selection()
            if not text.strip():
                pill.set_state("error", "nothing selected — highlight text first")
                pill.auto_close(3.0)
                return

            short_in = text[:45] + ("…" if len(text) > 45 else "")
            pill.set_state("processing", short_in)
            result = enhance_text(text, config)

            if not result:
                pill.set_state("error", "empty response from model")
                pill.auto_close(2.5)
                return

            pill.set_state("pasting", "replacing selection…")
            replace_selection(result, config["paste_method"])

            short_out = result[:48] + ("…" if len(result) > 48 else "")
            pill.set_state("done", short_out)
            pill.auto_close(2.0)

        except Exception as e:
            msg = str(e)[:60]
            log_error(f"worker error: {msg}")
            pill.set_state("error", msg)
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
