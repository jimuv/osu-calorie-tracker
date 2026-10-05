#!/usr/bin/env python3
"""
Rhythm Calorie Tracker - single-file edition
============================================

Everything lives in this one file for now, but it is organised in the same
sections as the module tree you'd split it into later:

    1. SETTINGS / CONSTANTS            -> core/config.py
    2. HELPERS                         -> core/utils.py
    3. DATA MODELS                     -> core/config.py, core/session.py
    4. ACTIVITY MODELS                 -> core/activity.py
    5. STATS ENGINE + TRACKING SESSION -> core/statistics.py, core/tracker.py
    6. INPUT (keyboard, hotkeys)       -> input/keyboard.py, input/hotkeys.py
    7. SERVICES (persistence, images)  -> services/persistence.py, image_manager.py
    8. UI (scroll frame, overlay, history window)
    9. APPLICATION CONTROLLER + setup window
   10. main()

Requirements:  pip install pillow pynput
Run:           python rhythm_tracker.py

Everything the app saves (config.json, profiles.json, history.json,
screenshots/) goes in  ~/.rhythm_tracker/  (override with the
RHYTHM_TRACKER_HOME environment variable).

Default global hotkeys (editable in the app, e.g. "f6" or "ctrl+f7"):
    F6 start/pause   F7 reset   F8 hide stats   F9 show setup
    F10 toggle overlay   F11 screenshot

Custom activity formulas can use:  presses, minutes, seconds, rate
and the functions min, max, abs, sqrt, log, round.   e.g.  presses * 0.03
"""

from __future__ import annotations

import ast
import csv
import json
import math
import operator
import os
import queue
import re
import sys
import threading
import time
import traceback
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

import tkinter as tk
from tkinter import filedialog, messagebox

from PIL import Image, ImageOps, ImageTk

try:
    from pynput import keyboard as pynput_keyboard
    PYNPUT_ERROR = None
except Exception as _exc:  # pynput missing or no usable backend
    pynput_keyboard = None
    PYNPUT_ERROR = _exc


# ============================================================
# 1. SETTINGS / CONSTANTS
# ============================================================

APP_NAME = "Rhythm Calorie Tracker"
APP_VERSION = "1.5"

DATA_DIR = Path(
    os.environ.get("RHYTHM_TRACKER_HOME") or (Path.home() / ".rhythm_tracker")
)
CONFIG_PATH = DATA_DIR / "config.json"
PROFILES_PATH = DATA_DIR / "profiles.json"
HISTORY_PATH = DATA_DIR / "history.json"
SCREENSHOT_DIR = DATA_DIR / "screenshots"

DEFAULT_TITLE = "🔥 Rhythm Calorie Tracker 🔥"
DEFAULT_COOL_TEXT = "Keep tapping and burn those pixels!"
DEFAULT_CALORIES_PER_PRESS = 0.03
DEFAULT_CALORIES_PER_MINUTE = 5.0
DEFAULT_FORMULA = "presses * 0.03"
DEFAULT_TRACKED_KEYS = ["x", "z"]

# Sanity limits so a typo can't produce "1,000,000 calories".
MAX_CAL_PER_PRESS = 10.0
MAX_CAL_PER_MINUTE = 50.0

SETUP_WIDTH = 460
SETUP_HEIGHT = 780
MIN_SETUP_WIDTH = 360
MIN_SETUP_HEIGHT = 500

PREVIEW_SIZE = (386, 186)
MAX_OVERLAY_WIDTH = 750
MAX_OVERLAY_HEIGHT = 650

UPDATE_INTERVAL_MS = 100

# Stats engine
PEAK_WINDOWS = (10, 60, 300)          # seconds; first one doubles as "current rate"
RATE_WINDOW = PEAK_WINDOWS[0]
PAUSE_GAP_SECONDS = 2.0               # a gap longer than this counts as a pause
KEY_REPEAT_STALE_SECONDS = 1.5        # a "held" key older than this is a fresh press
MAX_EVENT_LOG = 100_000               # in-memory key event log per session
MAX_STORED_SESSIONS = 2000
INTENSITY_BANDS = ((1, "Idle"), (120, "Light"), (240, "Moderate"), (360, "High"))

# Theme extras (not user-configurable)
FIELD_BG = "#0d1020"

# Overlay card look
CARD_BG = "#10121d"
CARD_OUTLINE = "#d8d8df"
CARD_LABEL = "#8b8e9b"
CARD_TEXT = "#ffffff"
CARD_INNER_PAD = 13
CARD_ROW_HEIGHT = 40
CARD_HEADER_HEIGHT = 22
MIN_CARD_WIDTH = 250

UI_FONT = "Segoe UI"
MONO_FONT = "Consolas"

ACTIVITY_MODELS = {
    "fixed": "Fixed per press",
    "per_minute": "Calories per minute",
    "custom": "Custom formula",
    "off": "Disabled",
}

INPUT_MODES = {
    "every_keydown": "Every key-down event (incl. OS repeats)",
    "ignore_repeats": "Ignore key repeats (recommended)",
    "physical_debounced": "Physical presses only (debounced)",
    "down_and_up": "Count key-down + key-up",
}

OVERLAY_LAYOUTS = {
    "minimal": "Minimal",
    "compact": "Compact",
    "full": "Full",
    "custom": "Custom",
}

STAT_LABELS = {
    "keys": "KEYS",
    "presses": "PRESSES",
    "rate": "RATE",
    "peak": "PEAK (10s)",
    "activity": "ACTIVITY*",
    "time": "TIME",
    "consistency": "CONSISTENCY",
}

LAYOUT_PRESETS = {
    "compact": ["keys", "presses", "time"],
    "full": ["keys", "presses", "rate", "activity", "time"],
}

HOTKEY_ACTIONS = {
    "start_pause": "Start / Pause",
    "reset": "Reset",
    "toggle_stats": "Hide / show stats",
    "show_setup": "Show setup",
    "toggle_overlay": "Toggle overlay",
    "screenshot": "Screenshot",
}

DEFAULT_HOTKEYS = {
    "start_pause": "f6",
    "reset": "f7",
    "toggle_stats": "f8",
    "show_setup": "f9",
    "toggle_overlay": "f10",
    "screenshot": "f11",
}


# ============================================================
# 2. HELPERS
# ============================================================

def log(message: str) -> None:
    print(f"[rhythm-tracker] {message}", file=sys.stderr)


def clamp_float(value, lo: float, hi: float, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(number) or math.isinf(number):
        return default
    return max(lo, min(hi, number))


def clamp_int(value, lo: int, hi: int, default: int) -> int:
    return int(round(clamp_float(value, lo, hi, default)))


def read_json(path: Path, default):
    """Read JSON; a missing file gives `default`, a corrupt one is set aside."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return default
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        log(f"{path.name} is corrupt ({exc}); moving it aside and starting fresh.")
        try:
            os.replace(path, path.with_suffix(path.suffix + ".corrupt"))
        except OSError:
            pass
        return default
    except OSError as exc:
        log(f"Couldn't read {path}: {exc}")
        return default


def atomic_write_json(path: Path, data) -> bool:
    """Write JSON via a temp file so a crash can't leave a half-written file."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
        return True
    except OSError as exc:
        log(f"Couldn't save {path}: {exc}")
        return False


def dataclass_from_dict(cls, data):
    """Build a dataclass from a dict, ignoring unknown keys."""
    if not isinstance(data, dict):
        return cls()
    names = {f.name for f in fields(cls)}
    try:
        return cls(**{k: v for k, v in data.items() if k in names})
    except TypeError:
        return cls()


def format_duration(seconds: float) -> str:
    total = int(max(0, seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_duration_long(seconds: float) -> str:
    total = int(max(0, seconds))
    hours, rem = divmod(total, 3600)
    minutes = rem // 60
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m"
    return f"{total}s"


def format_rate(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:.0f}/min"


def lighten(color: str, amount: float = 0.25) -> str:
    """Lighten a #rrggbb colour; anything else is returned unchanged."""
    match = re.fullmatch(r"#([0-9a-fA-F]{6})", color or "")
    if not match:
        return color
    value = match.group(1)
    channels = [int(value[i:i + 2], 16) for i in (0, 2, 4)]
    channels = [int(c + (255 - c) * amount) for c in channels]
    return "#{:02x}{:02x}{:02x}".format(*channels)


# ---- keys -------------------------------------------------------------

KEY_ALIASES = {
    "ctrl_l": "ctrl", "ctrl_r": "ctrl", "control": "ctrl",
    "shift_l": "shift", "shift_r": "shift",
    "alt_l": "alt", "alt_r": "alt", "alt_gr": "alt", "option": "alt",
    "cmd_l": "cmd", "cmd_r": "cmd", "win": "cmd", "super": "cmd",
    "return": "enter", "escape": "esc", "spacebar": "space",
}
MODIFIER_KEYS = {"ctrl", "shift", "alt", "cmd"}


def canonical_key(name: str) -> str:
    name = name.strip().lower()
    return KEY_ALIASES.get(name, name)


def normalize_key(key) -> Optional[str]:
    """pynput key object -> canonical lowercase name ('z', 'space', 'f6'...)."""
    char = getattr(key, "char", None)
    if char:
        if 1 <= ord(char[0]) <= 26:          # ctrl+letter arrives as a control code
            char = chr(ord(char[0]) + 96)
        return "space" if char.isspace() else char.lower()
    name = getattr(key, "name", None)
    return canonical_key(name) if name else None


def _expand_key_token(token: str) -> list:
    token = canonical_key(token)
    match = re.fullmatch(r"([a-z0-9])-([a-z0-9])", token)
    if match:
        a, b = match.groups()
        if a.isdigit() == b.isdigit() and a <= b:
            return [chr(c) for c in range(ord(a), ord(b) + 1)]
    return [token] if token else []


def normalize_keys(items) -> list:
    """Clean a list of key names: lowercase, aliases, a-z ranges, no dupes."""
    seen, result = set(), []
    for item in items:
        if not isinstance(item, str):
            continue
        for key in _expand_key_token(item):
            if key not in seen:
                seen.add(key)
                result.append(key)
    return result


def parse_key_list(text: str) -> list:
    return normalize_keys(t for t in re.split(r"[,;\s]+", text.lower()) if t)


def format_key_list(keys) -> str:
    """['a','b','c','x'] -> 'a-c, x'  (runs of 3+ become ranges)."""
    singles = sorted({k for k in keys if len(k) == 1 and k.isalnum()})
    others = [k for k in keys if not (len(k) == 1 and k.isalnum())]
    parts, i = [], 0
    while i < len(singles):
        j = i
        while j + 1 < len(singles) and ord(singles[j + 1]) == ord(singles[j]) + 1:
            j += 1
        if j - i >= 2:
            parts.append(f"{singles[i]}-{singles[j]}")
        else:
            parts.extend(singles[i:j + 1])
        i = j + 1
    return ", ".join(parts + others)


def parse_hotkey(text) -> Optional[tuple]:
    """'ctrl+f7' -> (frozenset({'ctrl'}), 'f7'); None if invalid."""
    parts = [canonical_key(p) for p in str(text).split("+") if p.strip()]
    if not parts:
        return None
    *mods, key = parts
    if key in MODIFIER_KEYS or any(m not in MODIFIER_KEYS for m in mods):
        return None
    return frozenset(mods), key


# ============================================================
# 3. DATA MODELS
# ============================================================

@dataclass
class Profile:
    name: str = "Rhythm Game"
    tracked_keys: list = field(default_factory=lambda: list(DEFAULT_TRACKED_KEYS))
    calories_per_press: float = DEFAULT_CALORIES_PER_PRESS
    title: str = DEFAULT_TITLE
    cool_text: str = DEFAULT_COOL_TEXT
    image_path: Optional[str] = None
    activity_model: str = "fixed"
    calories_per_minute: float = DEFAULT_CALORIES_PER_MINUTE
    custom_formula: str = DEFAULT_FORMULA
    input_mode: str = "ignore_repeats"

    def __post_init__(self):
        self.name = (str(self.name).strip() or "Custom")[:60]
        keys = self.tracked_keys if isinstance(self.tracked_keys, (list, tuple, set)) else []
        self.tracked_keys = normalize_keys(keys) or list(DEFAULT_TRACKED_KEYS)
        self.calories_per_press = clamp_float(
            self.calories_per_press, 0.0, MAX_CAL_PER_PRESS, DEFAULT_CALORIES_PER_PRESS)
        self.calories_per_minute = clamp_float(
            self.calories_per_minute, 0.0, MAX_CAL_PER_MINUTE, DEFAULT_CALORIES_PER_MINUTE)
        self.title = str(self.title)
        self.cool_text = str(self.cool_text)
        self.custom_formula = str(self.custom_formula)
        self.image_path = str(self.image_path) if self.image_path else None
        if self.activity_model not in ACTIVITY_MODELS:
            self.activity_model = "fixed"
        if self.input_mode not in INPUT_MODES:
            self.input_mode = "ignore_repeats"


def builtin_profiles() -> list:
    return [
        Profile(name="Rhythm Game", tracked_keys=["z", "x"], calories_per_press=0.03),
        Profile(
            name="Typing",
            tracked_keys=["a-z", "space"],
            calories_per_press=0.001,
            title="⌨️ Typing Tracker ⌨️",
            cool_text="Every keystroke counts!",
        ),
        Profile(
            name="WASD Gaming",
            tracked_keys=["w", "a", "s", "d"],
            calories_per_press=0.02,
            title="🎮 Movement Tracker 🎮",
            cool_text="Strafe harder.",
        ),
    ]


@dataclass
class Theme:
    background: str = "#15182a"
    panel: str = "#232946"
    accent: str = "#ff4d8d"
    text: str = "#f4f4ff"

    def __post_init__(self):
        defaults = Theme.__dataclass_fields__
        for name in ("background", "panel", "accent", "text"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                setattr(self, name, defaults[name].default)


@dataclass
class OverlayConfig:
    layout: str = "full"                    # minimal | compact | full | custom
    custom_stats: list = field(
        default_factory=lambda: ["keys", "presses", "rate", "activity", "time"])
    opacity: float = 0.95
    always_on_top: bool = True
    borderless: bool = False                # borderless *setup* window
    card_x: Optional[int] = None            # None = bottom-left default
    card_y: Optional[int] = None
    window_x: Optional[int] = None          # None = centred on screen
    window_y: Optional[int] = None
    padding: int = 20
    card_width: int = 405
    corner_radius: int = 12

    def __post_init__(self):
        if self.layout not in OVERLAY_LAYOUTS:
            self.layout = "full"
        stats = self.custom_stats if isinstance(self.custom_stats, list) else []
        self.custom_stats = [s for s in stats if s in STAT_LABELS] or ["presses"]
        self.opacity = clamp_float(self.opacity, 0.35, 1.0, 0.95)
        self.always_on_top = bool(self.always_on_top)
        self.borderless = bool(self.borderless)
        self.padding = clamp_int(self.padding, 0, 80, 20)
        self.card_width = clamp_int(self.card_width, MIN_CARD_WIDTH, 700, 405)
        self.corner_radius = clamp_int(self.corner_radius, 0, 40, 12)
        for name in ("card_x", "card_y", "window_x", "window_y"):
            value = getattr(self, name)
            setattr(self, name, int(value) if isinstance(value, (int, float)) else None)


@dataclass
class AppConfig:
    profile: Profile = field(default_factory=Profile)
    theme: Theme = field(default_factory=Theme)
    overlay: OverlayConfig = field(default_factory=OverlayConfig)
    hotkeys: dict = field(default_factory=lambda: dict(DEFAULT_HOTKEYS))
    autosave_sessions: bool = True
    debounce_ms: int = 20

    @classmethod
    def from_dict(cls, data: dict) -> "AppConfig":
        hotkeys = dict(DEFAULT_HOTKEYS)
        raw = data.get("hotkeys")
        if isinstance(raw, dict):
            for action in DEFAULT_HOTKEYS:
                value = raw.get(action)
                if isinstance(value, str) and value.strip():
                    hotkeys[action] = value.strip().lower()
        return cls(
            profile=dataclass_from_dict(Profile, data.get("profile")),
            theme=dataclass_from_dict(Theme, data.get("theme")),
            overlay=dataclass_from_dict(OverlayConfig, data.get("overlay")),
            hotkeys=hotkeys,
            autosave_sessions=bool(data.get("autosave_sessions", True)),
            debounce_ms=clamp_int(data.get("debounce_ms", 20), 0, 200, 20),
        )

    def to_dict(self) -> dict:
        return {
            "profile": asdict(self.profile),
            "theme": asdict(self.theme),
            "overlay": asdict(self.overlay),
            "hotkeys": dict(self.hotkeys),
            "autosave_sessions": self.autosave_sessions,
            "debounce_ms": self.debounce_ms,
        }


@dataclass
class Session:
    """One tracking session (what gets saved to history)."""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    started_at: Optional[str] = None        # ISO, local time
    ended_at: Optional[str] = None
    duration: float = 0.0                   # active seconds (pauses excluded)
    key_counts: dict = field(default_factory=dict)
    total: int = 0
    profile: str = ""
    activity: Optional[float] = None
    peak_rates: dict = field(default_factory=dict)   # {"10": presses/min, ...}
    per_minute: list = field(default_factory=list)   # presses in each active minute
    consistency: Optional[float] = None

    def __post_init__(self):
        self.total = clamp_int(self.total, 0, 10**12, 0)
        self.duration = clamp_float(self.duration, 0.0, 1e9, 0.0)
        if not isinstance(self.key_counts, dict):
            self.key_counts = {}
        if not isinstance(self.per_minute, list):
            self.per_minute = []
        if not isinstance(self.peak_rates, dict):
            self.peak_rates = {}

    @property
    def avg_rate(self) -> float:
        return self.total / self.duration * 60 if self.duration > 0 else 0.0

    def started_dt(self) -> Optional[datetime]:
        try:
            return datetime.fromisoformat(self.started_at)
        except (TypeError, ValueError):
            return None


@dataclass(frozen=True)
class KeyEvent:
    """An entry in the in-memory event log: seconds into the session + key."""
    t: float
    key: str


@dataclass(frozen=True)
class InputEvent:
    """What an input source emits (keyboard today, controller/MIDI later)."""
    key: str
    kind: str = "down"                      # "down" | "up"
    timestamp: float = 0.0                  # time.monotonic()
    repeat: bool = False                    # OS auto-repeat of a held key


# ============================================================
# 4. ACTIVITY MODELS  (calories are just one possible "activity" estimate)
# ============================================================

_BINOPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv,
}
_UNARY = {ast.USub: operator.neg, ast.UAdd: operator.pos}
_FUNCS = {"min": min, "max": max, "abs": abs, "sqrt": math.sqrt, "log": math.log, "round": round}
FORMULA_VARIABLES = ("presses", "minutes", "seconds", "rate")


def _check_formula_node(node) -> None:
    if isinstance(node, ast.BinOp):
        if type(node.op) not in _BINOPS:
            raise ValueError("operator not allowed")
        _check_formula_node(node.left)
        _check_formula_node(node.right)
    elif isinstance(node, ast.UnaryOp):
        if type(node.op) not in _UNARY:
            raise ValueError("operator not allowed")
        _check_formula_node(node.operand)
    elif isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ValueError("only plain numbers are allowed")
    elif isinstance(node, ast.Name):
        if node.id not in FORMULA_VARIABLES:
            raise ValueError(
                f"unknown name '{node.id}' (use: {', '.join(FORMULA_VARIABLES)})")
    elif isinstance(node, ast.Call):
        if (not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS
                or node.keywords):
            raise ValueError("only min, max, abs, sqrt, log and round are allowed")
        for arg in node.args:
            _check_formula_node(arg)
    else:
        raise ValueError("unsupported expression")


def compile_formula(text: str):
    try:
        tree = ast.parse(text.strip(), mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"syntax error ({exc.msg})")
    _check_formula_node(tree.body)
    return tree.body


def _eval_formula_node(node, env):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return env[node.id]
    if isinstance(node, ast.UnaryOp):
        return _UNARY[type(node.op)](_eval_formula_node(node.operand, env))
    if isinstance(node, ast.BinOp):
        left = _eval_formula_node(node.left, env)
        right = _eval_formula_node(node.right, env)
        if isinstance(node.op, ast.Pow) and abs(right) > 16:
            raise ValueError("exponent too large")
        return _BINOPS[type(node.op)](left, right)
    if isinstance(node, ast.Call):
        args = [_eval_formula_node(a, env) for a in node.args]
        return _FUNCS[node.func.id](*args)
    raise ValueError("unsupported expression")


class ActivityModel:
    """Turns Stats into an 'activity' number (kcal*). None = disabled."""
    enabled = True

    def estimate(self, stats: "Stats") -> Optional[float]:
        raise NotImplementedError


class FixedPerPress(ActivityModel):
    def __init__(self, per_press: float):
        self.per_press = per_press

    def estimate(self, stats):
        return stats.total * self.per_press


class PerMinute(ActivityModel):
    def __init__(self, per_minute: float):
        self.per_minute = per_minute

    def estimate(self, stats):
        return stats.elapsed / 60.0 * self.per_minute


class CustomFormula(ActivityModel):
    def __init__(self, formula: str):
        self._tree = compile_formula(formula)       # raises ValueError if bad

    def estimate(self, stats):
        env = {
            "presses": stats.total,
            "minutes": stats.elapsed / 60.0,
            "seconds": stats.elapsed,
            "rate": stats.avg_rate,
        }
        try:
            value = float(_eval_formula_node(self._tree, env))
        except (ArithmeticError, ValueError, TypeError):
            return 0.0
        if math.isnan(value) or math.isinf(value):
            return 0.0
        return max(0.0, value)


class Disabled(ActivityModel):
    enabled = False

    def estimate(self, stats):
        return None


def build_activity_model(profile: Profile) -> ActivityModel:
    """Raises ValueError for an invalid custom formula."""
    kind = profile.activity_model
    if kind == "per_minute":
        return PerMinute(profile.calories_per_minute)
    if kind == "custom":
        return CustomFormula(profile.custom_formula)
    if kind == "off":
        return Disabled()
    return FixedPerPress(profile.calories_per_press)


# ============================================================
# 5. STATS ENGINE + TRACKING SESSION  (no Tkinter in here)
# ============================================================

@dataclass
class KeyStat:
    key: str
    count: int
    share: float            # % of all tracked input
    rate: float             # presses / minute (session average)


@dataclass
class Stats:
    counts: dict
    total: int
    elapsed: float
    running: bool
    avg_rate: float
    recent_rate: float                      # last RATE_WINDOW seconds
    peaks: dict                             # {window_seconds: presses/min or None}
    avg_interval: Optional[float]
    consistency: Optional[float]            # 0-100
    pauses: int
    intensity: str
    keys: list                              # [KeyStat], busiest first
    per_minute: list


def intensity_label(rate: float) -> str:
    for limit, name in INTENSITY_BANDS:
        if rate < limit:
            return name
    return "Extreme"


class StatsEngine:
    """Online statistics: O(1) work per key press."""

    def __init__(self):
        self.reset()

    def reset(self):
        self._windows = {w: deque() for w in PEAK_WINDOWS}
        self._peak_counts = {w: 0 for w in PEAK_WINDOWS}
        self._per_minute = []
        self._last_t = None
        self._n = 0                         # Welford accumulators for intervals
        self._mean = 0.0
        self._m2 = 0.0
        self._pauses = 0

    def pause(self):
        self._last_t = None                 # don't count a pause as an interval

    def record(self, t: float, elapsed: float):
        idx = int(elapsed // 60)
        if idx >= len(self._per_minute):
            self._per_minute.extend([0] * (idx + 1 - len(self._per_minute)))
        self._per_minute[idx] += 1

        for window, dq in self._windows.items():
            dq.append(t)
            cutoff = t - window
            while dq and dq[0] <= cutoff:
                dq.popleft()
            if len(dq) > self._peak_counts[window]:
                self._peak_counts[window] = len(dq)

        if self._last_t is not None:
            gap = max(0.0, t - self._last_t)
            if gap > PAUSE_GAP_SECONDS:
                self._pauses += 1
            else:
                self._n += 1
                delta = gap - self._mean
                self._mean += delta / self._n
                self._m2 += delta * (gap - self._mean)
        self._last_t = t

    def compute(self, counts: dict, elapsed: float, now: float, running: bool) -> Stats:
        ordered = {k: counts[k] for k in sorted(counts)}
        total = sum(ordered.values())
        avg_rate = total / elapsed * 60 if elapsed > 0 else 0.0

        dq = self._windows[RATE_WINDOW]
        cutoff = now - RATE_WINDOW
        while dq and dq[0] <= cutoff:
            dq.popleft()
        recent = len(dq) * 60 / max(1.0, min(float(RATE_WINDOW), elapsed))

        # A peak only means something once the session is at least that long.
        peaks = {
            w: (self._peak_counts[w] * 60 / w if elapsed >= w else None)
            for w in PEAK_WINDOWS
        }

        avg_interval = self._mean if self._n > 0 else None
        consistency = None
        if self._n >= 5 and self._mean > 0:
            cv = math.sqrt(self._m2 / self._n) / self._mean
            consistency = max(0.0, 100.0 * (1.0 - min(1.0, cv)))

        keys = [
            KeyStat(
                key=k,
                count=c,
                share=(c / total * 100) if total else 0.0,
                rate=(c / elapsed * 60) if elapsed > 0 else 0.0,
            )
            for k, c in ordered.items()
        ]
        keys.sort(key=lambda ks: (-ks.count, ks.key))

        return Stats(
            counts=ordered, total=total, elapsed=elapsed, running=running,
            avg_rate=avg_rate, recent_rate=recent, peaks=peaks,
            avg_interval=avg_interval, consistency=consistency,
            pauses=self._pauses, intensity=intensity_label(recent),
            keys=keys, per_minute=list(self._per_minute),
        )


class TrackingSession:
    """The tracking engine. Thread-safe; knows nothing about the UI or pynput."""

    def __init__(self, tracked_keys=None):
        self._lock = threading.RLock()
        self._stats = StatsEngine()
        self._tracked = set(normalize_keys(tracked_keys or DEFAULT_TRACKED_KEYS))
        self._clear()

    # ---- lifecycle ----------------------------------------------------

    def _clear(self):
        self._counts = {k: 0 for k in self._tracked}
        self._elapsed_before = 0.0
        self._run_started = None
        self._started_at = None
        self._session_id = uuid.uuid4().hex[:12]
        self.events = deque(maxlen=MAX_EVENT_LOG)
        self._stats.reset()

    @property
    def running(self) -> bool:
        return self._run_started is not None

    def _begin_run(self):
        self._run_started = time.monotonic()
        if self._started_at is None:
            self._started_at = datetime.now()

    def start(self):
        with self._lock:
            if not self.running:
                self._begin_run()

    def pause(self):
        with self._lock:
            if self.running:
                self._elapsed_before += time.monotonic() - self._run_started
                self._run_started = None
                self._stats.pause()

    def toggle(self):
        with self._lock:
            if self.running:
                self.pause()
            else:
                self.start()

    def reset(self):
        """Clear everything. Keeps running if it was running."""
        with self._lock:
            was_running = self.running
            self._clear()
            if was_running:
                self._begin_run()

    # ---- configuration --------------------------------------------------

    def set_tracked_keys(self, keys):
        normalized = set(normalize_keys(keys)) or set(DEFAULT_TRACKED_KEYS)
        with self._lock:
            self._tracked = normalized
            self._counts = {k: self._counts.get(k, 0) for k in normalized}

    # ---- input ------------------------------------------------------------

    def register_input(self, event: InputEvent):
        with self._lock:
            if self._run_started is None or event.key not in self._tracked:
                return
            now = event.timestamp or time.monotonic()
            elapsed = max(0.0, self._elapsed_before + (now - self._run_started))
            self._counts[event.key] = self._counts.get(event.key, 0) + 1
            self.events.append(KeyEvent(round(elapsed, 3), event.key))
            self._stats.record(now, elapsed)

    # ---- output -------------------------------------------------------------

    def _elapsed(self, now: float) -> float:
        if self._run_started is None:
            return self._elapsed_before
        return self._elapsed_before + (now - self._run_started)

    def get_stats(self) -> Stats:
        with self._lock:
            now = time.monotonic()
            return self._stats.compute(
                dict(self._counts), self._elapsed(now), now, self.running)

    def has_data(self) -> bool:
        with self._lock:
            return sum(self._counts.values()) > 0

    def to_session(self, profile_name: str, activity: Optional[float]) -> Session:
        with self._lock:
            stats = self.get_stats()
            started = self._started_at or datetime.now()
            return Session(
                id=self._session_id,
                started_at=started.isoformat(timespec="seconds"),
                ended_at=datetime.now().isoformat(timespec="seconds"),
                duration=round(stats.elapsed, 2),
                key_counts=dict(stats.counts),
                total=stats.total,
                profile=profile_name,
                activity=None if activity is None else round(activity, 2),
                peak_rates={
                    str(w): (None if v is None else round(v, 1))
                    for w, v in stats.peaks.items()
                },
                per_minute=list(stats.per_minute),
                consistency=(None if stats.consistency is None
                             else round(stats.consistency, 1)),
            )


# ============================================================
# 6. INPUT  (sources -> hotkeys + repeat filter -> tracker)
# ============================================================

class InputSource:
    """Base class for anything that produces InputEvents."""
    name = "source"

    def __init__(self):
        self.emit: Callable[[InputEvent], None] = lambda event: None

    def start(self):
        raise NotImplementedError

    def stop(self):
        pass


class KeyboardInput(InputSource):
    """Global keyboard listener (pynput) with OS key-repeat detection."""
    name = "keyboard"

    def __init__(self):
        super().__init__()
        self._listener = None
        self._held = {}                     # identity -> (name, last key-down time)

    def start(self):
        if pynput_keyboard is None:
            raise RuntimeError(f"pynput is not available: {PYNPUT_ERROR}")
        if self._listener is not None:
            return
        self._listener = pynput_keyboard.Listener(
            on_press=self._on_press, on_release=self._on_release)
        self._listener.daemon = True
        self._listener.start()

    def stop(self):
        if self._listener is not None:
            self._listener.stop()
            self._listener = None

    @staticmethod
    def _identity(key, name: str):
        ident = getattr(key, "vk", None)
        if ident is None:
            ident = getattr(getattr(key, "value", None), "vk", None)
        return ident if ident is not None else name

    def _on_press(self, key):
        name = normalize_key(key)
        if name is None:
            return
        ident = self._identity(key, name)
        now = time.monotonic()
        held = self._held.get(ident)
        # OS auto-repeat keeps firing every few tens of ms once it starts, so a
        # "held" key we haven't heard from in a while means we missed its
        # key-up (Win+L, alt-tab...) and this is a genuine new press.
        repeat = held is not None and now - held[1] < KEY_REPEAT_STALE_SECONDS
        self._held[ident] = (name, now)
        self.emit(InputEvent(name, "down", now, repeat))

    def _on_release(self, key):
        name = normalize_key(key)
        if name is None:
            return
        self._held.pop(self._identity(key, name), None)
        # Shift can change what a key reports between press and release.
        for ident in [i for i, (n, _t) in self._held.items() if n == name]:
            del self._held[ident]
        self.emit(InputEvent(name, "up", time.monotonic(), False))


class InputFilter:
    """Decides which events count, based on the selected input mode."""

    def __init__(self, mode: str = "ignore_repeats", debounce_ms: int = 20):
        self.mode = mode
        self.debounce_ms = debounce_ms
        self._last_down = {}

    def configure(self, mode: str, debounce_ms: int):
        self.mode = mode if mode in INPUT_MODES else "ignore_repeats"
        self.debounce_ms = debounce_ms

    def accept(self, event: InputEvent) -> bool:
        if event.kind == "up":
            return self.mode == "down_and_up"
        if self.mode == "every_keydown":
            return True
        if event.repeat:
            return False
        if self.mode == "physical_debounced":
            last = self._last_down.get(event.key)
            if last is not None and (event.timestamp - last) * 1000.0 < self.debounce_ms:
                return False
            self._last_down[event.key] = event.timestamp
        return True


class HotkeyManager:
    """Matches global hotkeys (with optional modifiers) to action names."""

    def __init__(self, on_action: Callable[[str], None]):
        self._on_action = on_action
        self._bindings = {}                 # (frozenset(mods), key) -> action
        self._mods = set()

    def set_bindings(self, mapping: dict) -> list:
        """Returns the actions whose hotkey was invalid or a duplicate."""
        new, failed = {}, []
        for action, text in mapping.items():
            parsed = parse_hotkey(text)
            if parsed is None or parsed in new:
                failed.append(action)
                continue
            new[parsed] = action
        self._bindings = new
        return failed

    def process(self, event: InputEvent):
        if event.key in MODIFIER_KEYS:
            if event.kind == "down":
                self._mods.add(event.key)
            else:
                self._mods.discard(event.key)
            return
        if event.kind != "down" or event.repeat:
            return
        action = self._bindings.get((frozenset(self._mods), event.key))
        if action:
            self._on_action(action)


class InputManager:
    """Owns the input sources and routes their events."""

    def __init__(self, sink: Callable[[InputEvent], None],
                 on_hotkey: Callable[[str], None]):
        self._sink = sink
        self.filter = InputFilter()
        self.hotkeys = HotkeyManager(on_hotkey)
        self.sources = []

    def add_source(self, source: InputSource):
        source.emit = self._dispatch
        self.sources.append(source)

    def start(self) -> list:
        errors = []
        for source in self.sources:
            try:
                source.start()
            except Exception as exc:
                errors.append(f"{source.name}: {exc}")
        return errors

    def stop(self):
        for source in self.sources:
            try:
                source.stop()
            except Exception:
                pass

    def _dispatch(self, event: InputEvent):
        # Runs on the listener thread; an exception here would kill the listener.
        try:
            self.hotkeys.process(event)
            if self.filter.accept(event):
                self._sink(event)
        except Exception:
            log(traceback.format_exc())


# ============================================================
# 7. SERVICES  (persistence + images)
# ============================================================

class ConfigStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> AppConfig:
        data = read_json(self.path, {})
        return AppConfig.from_dict(data if isinstance(data, dict) else {})

    def save(self, config: AppConfig) -> bool:
        return atomic_write_json(self.path, config.to_dict())


class ProfileStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> dict:
        data = read_json(self.path, None)
        profiles = {}
        items = data.get("profiles") if isinstance(data, dict) else None
        if isinstance(items, list):
            for item in items:
                profile = dataclass_from_dict(Profile, item)
                profiles[profile.name] = profile
        if not profiles:
            profiles = {p.name: p for p in builtin_profiles()}
            self.save(profiles)
        return profiles

    def save(self, profiles: dict) -> bool:
        return atomic_write_json(
            self.path, {"profiles": [asdict(p) for p in profiles.values()]})


class SessionHistory:
    def __init__(self, path: Path):
        self.path = path
        self.sessions = []
        self.load()

    def load(self):
        data = read_json(self.path, {})
        items = data.get("sessions") if isinstance(data, dict) else None
        self.sessions = []
        for item in items or []:
            session = dataclass_from_dict(Session, item)
            if session.started_dt() is not None:
                self.sessions.append(session)

    def _save(self) -> bool:
        return atomic_write_json(
            self.path,
            {"version": 1, "sessions": [asdict(s) for s in self.sessions]})

    def add(self, session: Session) -> bool:
        self.sessions.append(session)
        del self.sessions[:-MAX_STORED_SESSIONS]
        return self._save()

    def clear(self):
        self.sessions = []
        self._save()

    def since(self, start: datetime) -> list:
        return [s for s in self.sessions if s.started_dt() >= start]

    @staticmethod
    def summarize(sessions) -> dict:
        duration = sum(s.duration for s in sessions)
        total = sum(s.total for s in sessions)
        return {
            "sessions": len(sessions),
            "duration": duration,
            "total": total,
            "rate": total / duration * 60 if duration > 0 else 0.0,
        }

    def daily(self, days: int = 7) -> list:
        today = date.today()
        result = []
        for offset in range(days - 1, -1, -1):
            day = today - timedelta(days=offset)
            subset = [s for s in self.sessions if s.started_dt().date() == day]
            summary = self.summarize(subset)
            summary["date"] = day
            summary["label"] = day.strftime("%a")
            result.append(summary)
        return result

    def export_csv(self, path: str):
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow([
                "id", "started_at", "ended_at", "duration_s", "profile",
                "total_presses", "avg_rate_per_min", "activity_kcal", "key_counts"])
            for s in self.sessions:
                writer.writerow([
                    s.id, s.started_at, s.ended_at, s.duration, s.profile,
                    s.total, round(s.avg_rate, 1),
                    "" if s.activity is None else s.activity,
                    json.dumps(s.key_counts),
                ])


class ImageManager:
    """Loads the user's image and produces preview + overlay versions."""

    def __init__(self):
        self.path = None
        self.original = None
        self.overlay_image = None
        self.preview_photo = None
        self.overlay_photo = None

    @property
    def loaded(self) -> bool:
        return self.overlay_image is not None

    @property
    def overlay_size(self) -> tuple:
        return self.overlay_image.size if self.overlay_image else (0, 0)

    def load(self, path: str):
        image = Image.open(path)
        image.load()
        try:
            image = ImageOps.exif_transpose(image)
        except Exception:
            pass
        # RGB avoids surprises from palettes / transparency.
        image = image.convert("RGB")
        width, height = image.size
        if width <= 0 or height <= 0:
            raise ValueError("The image has invalid dimensions.")

        # One scale factor keeps the aspect ratio; never upscale.
        scale = min(MAX_OVERLAY_WIDTH / width, MAX_OVERLAY_HEIGHT / height, 1.0)
        size = (max(1, round(width * scale)), max(1, round(height * scale)))
        overlay = image.resize(size, Image.Resampling.LANCZOS)
        preview = image.copy()
        preview.thumbnail(PREVIEW_SIZE, Image.Resampling.LANCZOS)

        # Commit only after everything above succeeded.
        self.path = path
        self.original = image
        self.overlay_image = overlay
        self.preview_photo = ImageTk.PhotoImage(preview)
        self.overlay_photo = ImageTk.PhotoImage(overlay)

    def clear(self):
        self.path = None
        self.original = None
        self.overlay_image = None
        self.preview_photo = None
        self.overlay_photo = None


# ============================================================
# 8. UI COMPONENTS
# ============================================================

class ScrollableFrame(tk.Frame):
    """A frame that scrolls vertically, with a thin custom scrollbar."""

    def __init__(self, parent):
        super().__init__(parent)
        self._thumb_color = "#ff4d8d"
        self._thumb_box = None
        self._grab = 0.0
        self._first, self._last = 0.0, 1.0

        self.bar = tk.Canvas(self, width=8, highlightthickness=0, bd=0)
        self.bar.pack(side="right", fill="y", padx=(0, 4))
        self.canvas = tk.Canvas(self, highlightthickness=0, bd=0,
                                yscrollcommand=self._on_yscroll)
        self.canvas.pack(side="left", fill="both", expand=True)

        self.body = tk.Frame(self.canvas)
        self._window = self.canvas.create_window(0, 0, window=self.body, anchor="nw")

        self.body.bind("<Configure>", self._on_body_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.bar.bind("<Configure>", lambda e: self._draw_bar())
        self.bar.bind("<Button-1>", self._bar_press)
        self.bar.bind("<B1-Motion>", self._bar_drag)

        self.bind_all("<MouseWheel>", self._on_wheel, add="+")
        self.bind_all("<Button-4>", self._on_wheel, add="+")
        self.bind_all("<Button-5>", self._on_wheel, add="+")

    def set_colors(self, bg: str, thumb: str):
        self._thumb_color = thumb
        self.configure(bg=bg)
        self.canvas.configure(bg=bg)
        self.bar.configure(bg=bg)
        self.body.configure(bg=bg)
        self._draw_bar()

    def refresh_scrollbar(self):
        self.after_idle(self._draw_bar)

    # ---- layout -------------------------------------------------------------

    def _on_body_configure(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self.canvas.itemconfigure(self._window, width=event.width)

    def _on_yscroll(self, first, last):
        self._first, self._last = float(first), float(last)
        self._draw_bar()

    # ---- scrollbar ------------------------------------------------------------

    def _draw_bar(self):
        self.bar.delete("all")
        self._thumb_box = None
        height = self.bar.winfo_height()
        visible = self._last - self._first
        if height <= 1 or visible >= 0.999:
            return
        thumb_h = max(35, int(height * visible))
        available = height - thumb_h
        progress = self._first / (1.0 - visible)
        top = progress * available
        self.bar.create_rectangle(
            1, top, 7, top + thumb_h, fill=self._thumb_color, outline="")
        self._thumb_box = (top, top + thumb_h)

    def _bar_press(self, event):
        if self._thumb_box is None:
            return
        top, bottom = self._thumb_box
        if top <= event.y <= bottom:
            self._grab = event.y - top
        else:
            self._grab = (bottom - top) / 2
            self._bar_drag(event)

    def _bar_drag(self, event):
        if self._thumb_box is None:
            return
        thumb_h = self._thumb_box[1] - self._thumb_box[0]
        available = self.bar.winfo_height() - thumb_h
        if available <= 0:
            return
        progress = max(0.0, min(1.0, (event.y - self._grab) / available))
        visible = self._last - self._first
        self.canvas.yview_moveto(progress * (1.0 - visible))

    # ---- mouse wheel ------------------------------------------------------------

    def _pointer_inside(self, event) -> bool:
        try:
            widget = self.winfo_containing(event.x_root, event.y_root)
        except (KeyError, tk.TclError):
            return False
        while widget is not None:
            if widget is self:
                return True
            widget = getattr(widget, "master", None)
        return False

    def _on_wheel(self, event):
        if not self.winfo_ismapped() or not self._pointer_inside(event):
            return
        if self._last - self._first >= 0.999:
            return
        if getattr(event, "num", None) == 4:
            step = -1
        elif getattr(event, "num", None) == 5:
            step = 1
        else:
            magnitude = max(1, abs(event.delta) // 120)
            step = -magnitude if event.delta > 0 else magnitude
        self.canvas.yview_scroll(step, "units")


def draw_rounded_rect(canvas, x1, y1, x2, y2, radius, **kwargs):
    r = max(0, min(radius, (x2 - x1) // 2, (y2 - y1) // 2))
    points = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


def nice_ceiling(value: float) -> float:
    if value <= 0:
        return 1.0
    magnitude = 10 ** math.floor(math.log10(value))
    for step in (1, 2, 2.5, 5, 10):
        if value <= step * magnitude:
            return step * magnitude
    return 10 * magnitude


def draw_bar_chart(canvas, values, labels, bar_color, text_color, grid_color,
                   show_values=True, left=38):
    """Small dependency-free bar chart used by the live graph and history."""
    canvas.delete("all")
    # Before the canvas is mapped winfo_width() is 1; fall back to the requested
    # size in pixels (cget("width") can be "10c", which isn't a pixel count).
    width = canvas.winfo_width()
    if width <= 1:
        width = canvas.winfo_reqwidth()
    height = canvas.winfo_height()
    if height <= 1:
        height = canvas.winfo_reqheight()
    top, bottom, right = 14, height - 22, 10
    peak = nice_ceiling(max(list(values) + [1]))

    for fraction in (0, 0.5, 1):
        y = bottom - fraction * (bottom - top)
        canvas.create_line(left, y, width - right, y, fill=grid_color)
        canvas.create_text(left - 4, y, text=f"{peak * fraction:g}", anchor="e",
                           fill=text_color, font=(UI_FONT, 7))
    if not values:
        return

    slot = (width - left - right) / len(values)
    pad = min(6, slot * 0.2)
    step = max(1, len(labels) // 8)
    for i, value in enumerate(values):
        x0 = left + i * slot + pad
        x1 = left + (i + 1) * slot - pad
        bar_h = (value / peak) * (bottom - top)
        if value > 0:
            canvas.create_rectangle(x0, bottom - bar_h, x1, bottom, fill=bar_color, outline="")
            if show_values and slot >= 30:
                canvas.create_text((x0 + x1) / 2, bottom - bar_h - 7, text=f"{value:.0f}",
                                   fill=text_color, font=(UI_FONT, 7))
        if i % step == 0:
            canvas.create_text((x0 + x1) / 2, bottom + 10, text=labels[i],
                               fill=text_color, font=(UI_FONT, 8))


# ---- overlay layout + view ---------------------------------------------------

@dataclass
class OverlayCell:
    stat: str
    label: Optional[str]
    x: int
    y: int
    value_font: int = 12
    template: str = "{}"


@dataclass
class OverlayLayout:
    padding: int = 20
    card_width: int = 405
    card_height: int = 118
    corner_radius: int = 12
    header: Optional[str] = None
    cells: list = field(default_factory=list)


def compute_layout(cfg: OverlayConfig, activity_enabled: bool,
                   available_width: Optional[int] = None) -> OverlayLayout:
    layout = OverlayLayout(padding=cfg.padding, corner_radius=cfg.corner_radius)

    if cfg.layout == "minimal":
        layout.card_width = min(cfg.card_width, 230)
        layout.card_height = 46
        layout.cells = [OverlayCell("presses", None, 14, 12, value_font=14,
                                    template="{} presses")]
        return layout

    names = LAYOUT_PRESETS.get(cfg.layout) or cfg.custom_stats
    names = [n for n in names
             if n in STAT_LABELS and (activity_enabled or n != "activity")] or ["presses"]

    width = cfg.card_width
    if available_width:
        # Narrow image: shrink the card to fit instead of overflowing.
        width = max(MIN_CARD_WIDTH, min(width, available_width - 2 * cfg.padding))

    inner = CARD_INNER_PAD
    col_width = (width - 2 * inner) // 2
    y = inner
    if cfg.layout in ("full", "custom"):
        layout.header = "NOW TRACKING"
        y += CARD_HEADER_HEIGHT

    col = 0
    for name in names:
        if name == "keys":                  # keys always get a full-width row
            if col:
                y += CARD_ROW_HEIGHT
                col = 0
            layout.cells.append(OverlayCell(name, STAT_LABELS[name], inner, y, 11))
            y += CARD_ROW_HEIGHT
        else:
            layout.cells.append(
                OverlayCell(name, STAT_LABELS[name], inner + col * col_width, y, 12))
            col += 1
            if col == 2:
                col = 0
                y += CARD_ROW_HEIGHT
    if col:
        y += CARD_ROW_HEIGHT

    layout.card_width = width
    layout.card_height = y + inner - 8
    return layout


class OverlayView:
    """Draws the image + stats card on a canvas. Knows nothing about tracking."""

    def __init__(self, root, theme: Theme, layout: OverlayLayout, size: tuple,
                 image_photo, image_size: tuple, card_pos: tuple,
                 on_card_moved, on_window_moved, on_context_menu):
        self.root = root
        self.layout = layout
        self.size = size
        self.photo = image_photo            # keep a reference
        self.stats_hidden = False
        self._on_card_moved = on_card_moved
        self._on_window_moved = on_window_moved
        self._value_ids = {}
        self._last_text = {}
        self._mode = None
        self._moved = False
        self._grab = (0, 0)

        width, height = size
        self.canvas = tk.Canvas(root, width=width, height=height, highlightthickness=0,
                                bd=0, relief="flat", bg=theme.background)
        self.canvas.pack(fill=None, expand=False)

        if image_photo is not None:
            iw, ih = image_size
            self.canvas.create_image((width - iw) // 2, (height - ih) // 2,
                                     image=image_photo, anchor="nw")

        if card_pos is None:
            card_pos = (layout.padding,
                        height - layout.card_height - layout.padding)
        self.card_x, self.card_y = self._clamp_card(*card_pos)
        self._draw_card(theme)

        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._motion)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Button-3>", on_context_menu)
        self.canvas.bind("<Button-2>", on_context_menu)

    # ---- drawing ------------------------------------------------------------------

    def _draw_card(self, theme: Theme):
        c, lay = self.canvas, self.layout
        x, y = self.card_x, self.card_y
        tags = ("stats", "card")
        draw_rounded_rect(c, x, y, x + lay.card_width, y + lay.card_height,
                          lay.corner_radius, fill=CARD_BG, outline=CARD_OUTLINE,
                          width=1, tags=tags)
        if lay.header:
            c.create_text(x + CARD_INNER_PAD, y + 11, text=lay.header, anchor="nw",
                          fill=CARD_TEXT, font=(UI_FONT, 9, "bold"), tags=("stats",))
        for cell in lay.cells:
            cx, cy = x + cell.x, y + cell.y
            value_y = cy
            if cell.label:
                c.create_text(cx, cy, text=cell.label, anchor="nw", fill=CARD_LABEL,
                              font=(UI_FONT, 8, "bold"), tags=("stats",))
                value_y = cy + 15
            color = theme.accent if cell.stat == "activity" else CARD_TEXT
            self._value_ids[cell.stat] = c.create_text(
                cx, value_y, text="", anchor="nw", fill=color,
                font=(UI_FONT, cell.value_font, "bold"), tags=("stats",))

    def update(self, values: dict):
        for cell in self.layout.cells:
            text = cell.template.format(values.get(cell.stat, ""))
            if self._last_text.get(cell.stat) != text:
                self._last_text[cell.stat] = text
                self.canvas.itemconfigure(self._value_ids[cell.stat], text=text)

    def toggle_stats(self):
        self.stats_hidden = not self.stats_hidden
        self.canvas.itemconfigure("stats", state="hidden" if self.stats_hidden else "normal")

    def destroy(self):
        self.canvas.destroy()

    # ---- dragging (card moves inside the image, anything else moves the window) -----

    def _clamp_card(self, x, y):
        width, height = self.size
        x = max(0, min(x, max(0, width - self.layout.card_width)))
        y = max(0, min(y, max(0, height - self.layout.card_height)))
        return int(x), int(y)

    def _in_card(self, x, y) -> bool:
        return (self.card_x <= x <= self.card_x + self.layout.card_width
                and self.card_y <= y <= self.card_y + self.layout.card_height)

    def _press(self, event):
        self._moved = False
        if not self.stats_hidden and self._in_card(event.x, event.y):
            self._mode = "card"
            self._grab = (event.x - self.card_x, event.y - self.card_y)
        else:
            self._mode = "window"
            self._grab = (event.x, event.y)

    def _motion(self, event):
        if self._mode == "card":
            nx, ny = self._clamp_card(event.x - self._grab[0], event.y - self._grab[1])
            dx, dy = nx - self.card_x, ny - self.card_y
            if dx or dy:
                self.canvas.move("stats", dx, dy)
                self.card_x, self.card_y = nx, ny
                self._moved = True
        elif self._mode == "window":
            x = self.root.winfo_x() + event.x - self._grab[0]
            y = self.root.winfo_y() + event.y - self._grab[1]
            self.root.geometry(f"{self.size[0]}x{self.size[1]}+{x}+{y}")
            self._moved = True

    def _release(self, _event):
        if self._moved:
            if self._mode == "card":
                self._on_card_moved(self.card_x, self.card_y)
            elif self._mode == "window":
                self._on_window_moved(self.root.winfo_x(), self.root.winfo_y())
        self._mode = None


class HistoryWindow(tk.Toplevel):
    """Today / this-week summaries, a 7-day graph and a list of sessions."""

    def __init__(self, app: "CalorieTrackerApp"):
        super().__init__(app.root)
        self.app = app
        self.title("Session History")
        self.geometry("540x680")
        self.minsize(460, 540)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._build()
        self.refresh()

    def _build(self):
        th = self.app.config.theme
        self.configure(bg=th.panel)

        def label(text, **kw):
            return tk.Label(self, text=text, bg=th.panel, fg=kw.pop("fg", th.text), **kw)

        label("SESSION HISTORY", font=(UI_FONT, 14, "bold"), fg=th.accent).pack(pady=(12, 6))
        self.summary_label = label("", font=(MONO_FONT, 10), justify="left", anchor="w")
        self.summary_label.pack(fill="x", padx=16)

        label("Avg presses/min - last 7 days", font=(UI_FONT, 9, "bold"),
              anchor="w").pack(fill="x", padx=16, pady=(10, 2))
        self.graph = tk.Canvas(self, height=170, highlightthickness=0, bg=FIELD_BG)
        self.graph.pack(fill="x", padx=16)
        self.graph.bind("<Configure>", lambda e: self._draw_graph())

        label("Recent sessions", font=(UI_FONT, 9, "bold"),
              anchor="w").pack(fill="x", padx=16, pady=(10, 2))
        holder = tk.Frame(self, bg=th.panel)
        holder.pack(fill="both", expand=True, padx=16)
        scrollbar = tk.Scrollbar(holder)
        scrollbar.pack(side="right", fill="y")
        self.listbox = tk.Listbox(
            holder, font=(MONO_FONT, 9), bg=FIELD_BG, fg=th.text, bd=0,
            highlightthickness=0, selectbackground=th.accent,
            yscrollcommand=scrollbar.set, activestyle="none")
        self.listbox.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self.listbox.yview)

        buttons = tk.Frame(self, bg=th.panel)
        buttons.pack(pady=10)
        for text, command in (("Refresh", self.refresh), ("Export CSV", self.export_csv),
                              ("Clear history", self.clear_history), ("Close", self.close)):
            tk.Button(buttons, text=text, command=command, bg=th.accent, fg="white",
                      activebackground=lighten(th.accent), activeforeground="white"
                      ).pack(side="left", padx=4)

    def refresh(self):
        history = self.app.history
        now = datetime.now()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week = today - timedelta(days=today.weekday())
        blocks = []
        for title, since in (("Today", today), ("This week", week)):
            s = history.summarize(history.since(since))
            blocks.append(
                f"{title}\n" + "-" * 38 + "\n"
                f"  Sessions:       {s['sessions']}\n"
                f"  Total time:     {format_duration_long(s['duration'])}\n"
                f"  Total presses:  {s['total']:,}\n"
                f"  Avg rate:       {s['rate']:.0f}/min")
        self.summary_label.configure(text="\n\n".join(blocks))

        self.listbox.delete(0, "end")
        for s in reversed(history.sessions[-200:]):
            start = s.started_dt()
            self.listbox.insert(
                "end",
                f"{start:%b %d  %I:%M %p}  {format_duration_long(s.duration):>7}  "
                f"{s.total:>8,} presses  {s.avg_rate:>4.0f}/min  {s.profile}")
        if not history.sessions:
            self.listbox.insert("end", "No sessions yet - they're saved on Reset / exit.")
        self._draw_graph()

    def _draw_graph(self):
        th = self.app.config.theme
        days = self.app.history.daily(7)
        draw_bar_chart(self.graph, [d["rate"] for d in days], [d["label"] for d in days],
                       th.accent, th.text, "#2b3050")

    def export_csv(self):
        path = filedialog.asksaveasfilename(
            parent=self, title="Export history", defaultextension=".csv",
            filetypes=[("CSV", "*.csv")], initialfile="rhythm_tracker_history.csv")
        if not path:
            return
        try:
            self.app.history.export_csv(path)
            messagebox.showinfo("Exported", f"Saved to:\n{path}", parent=self)
        except OSError as exc:
            messagebox.showerror("Export failed", str(exc), parent=self)

    def clear_history(self):
        if messagebox.askyesno("Clear history", "Delete ALL saved sessions?", parent=self):
            self.app.history.clear()
            self.refresh()

    def close(self):
        self.app.history_window = None
        self.destroy()


# ============================================================
# 9. APPLICATION CONTROLLER + SETUP WINDOW
# ============================================================

def read_number(var: tk.StringVar, lo: float, hi: float, default: float,
                label: str, warnings: list) -> float:
    """Parse + clamp a numeric entry, writing the cleaned value back."""
    raw = var.get().strip()
    try:
        value = float(raw)
        if math.isnan(value) or math.isinf(value):
            raise ValueError
    except ValueError:
        warnings.append(f"{label}: '{raw}' isn't a number, using {default:g}.")
        value = default
    else:
        if value < lo or value > hi:
            clamped = max(lo, min(hi, value))
            warnings.append(f"{label} limited to {lo:g}-{hi:g}.")
            value = clamped
    var.set(f"{value:g}")
    return value


class CalorieTrackerApp:

    def __init__(self):
        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.geometry(f"{SETUP_WIDTH}x{SETUP_HEIGHT}")
        self.root.minsize(MIN_SETUP_WIDTH, MIN_SETUP_HEIGHT)   # setup UI only

        # services
        self.config_store = ConfigStore(CONFIG_PATH)
        self.profile_store = ProfileStore(PROFILES_PATH)
        self.history = SessionHistory(HISTORY_PATH)
        self.config = self.config_store.load()
        self.profiles = self.profile_store.load()
        self.images = ImageManager()

        # core
        self.tracker = TrackingSession(self.config.profile.tracked_keys)
        try:
            self.activity_model = build_activity_model(self.config.profile)
        except ValueError:
            self.activity_model = FixedPerPress(self.config.profile.calories_per_press)

        # input (listener thread -> queue -> Tk thread)
        self.ui_events = queue.Queue()
        self.input = InputManager(self.tracker.register_input, self.ui_events.put)
        self.input.add_source(KeyboardInput())

        # ui state
        self.overlay_mode = False
        self.overlay_view = None
        self.history_window = None
        self._setup_geometry = None
        self._graph_cache = None
        self._closing = False

        self._init_vars()
        self._build_setup_ui()
        self.apply_theme()

        self.root.bind("<Escape>", self._on_escape_key)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.input.filter.configure(self.config.profile.input_mode, self.config.debounce_ms)
        self.input.hotkeys.set_bindings(self.config.hotkeys)
        errors = self.input.start()
        if errors:
            self.set_status("Keyboard tracking unavailable - see warning.")
            self.root.after(400, lambda: messagebox.showwarning(
                "Keyboard listener",
                "Couldn't start the global keyboard listener:\n\n"
                + "\n".join(errors) + "\n\nInstall it with:  pip install pynput"))

        if self.config.profile.image_path:
            error = self._try_load_image(self.config.profile.image_path)
            if error:
                self.set_status(error)

        self._tick()

    def run(self):
        self.root.mainloop()

    # ========================================================
    # VARIABLES
    # ========================================================

    def _init_vars(self):
        p, o, t = self.config.profile, self.config.overlay, self.config.theme

        self.profile_choice_var = tk.StringVar(value=p.name)
        self.profile_name_var = tk.StringVar(value=p.name)

        self.title_text_var = tk.StringVar(value=p.title)
        self.cool_text_var = tk.StringVar(value=p.cool_text)
        self.image_path_var = tk.StringVar(value=p.image_path or "")
        self.image_display_var = tk.StringVar(
            value=os.path.basename(p.image_path) if p.image_path else "None selected")

        self.keys_var = tk.StringVar(value=format_key_list(p.tracked_keys))
        self.input_mode_var = tk.StringVar(value=p.input_mode)
        self.debounce_var = tk.StringVar(value=str(self.config.debounce_ms))
        self.autosave_var = tk.BooleanVar(value=self.config.autosave_sessions)

        self.activity_model_var = tk.StringVar(value=p.activity_model)
        self.cal_per_press_var = tk.StringVar(value=f"{p.calories_per_press:g}")
        self.cal_per_min_var = tk.StringVar(value=f"{p.calories_per_minute:g}")
        self.formula_var = tk.StringVar(value=p.custom_formula)

        self.layout_var = tk.StringVar(value=o.layout)
        self.custom_stat_vars = {
            name: tk.BooleanVar(value=name in o.custom_stats) for name in STAT_LABELS}
        self.card_width_var = tk.StringVar(value=str(o.card_width))
        self.overlay_alpha_var = tk.DoubleVar(value=o.opacity)
        self.always_on_top_var = tk.BooleanVar(value=o.always_on_top)
        self.borderless_var = tk.BooleanVar(value=o.borderless)

        self.hotkey_vars = {
            action: tk.StringVar(value=self.config.hotkeys[action])
            for action in HOTKEY_ACTIONS}

        self.bg_color_var = tk.StringVar(value=t.background)
        self.panel_color_var = tk.StringVar(value=t.panel)
        self.accent_color_var = tk.StringVar(value=t.accent)
        self.text_color_var = tk.StringVar(value=t.text)

        self.status_var = tk.StringVar(value="Stopped")
        self.calories_var = tk.StringVar(value="0.00")
        self.activity_note_var = tk.StringVar(value="")
        self.key_stats_var = tk.StringVar(value="No presses yet.")

    # ========================================================
    # SETUP UI  (split into small builders)
    # ========================================================

    def _build_setup_ui(self):
        self.setup_container = ScrollableFrame(self.root)
        self.setup_container.pack(fill="both", expand=True)

        self.main = tk.Frame(self.setup_container.body, bd=2, relief="ridge")
        self.main.pack(fill="both", expand=True, padx=8, pady=8)

        self._build_header()
        self._build_image_preview()
        self._build_controls()
        self._build_live_stats()
        self._build_key_stats()
        self._build_profile_section()
        self._build_customization_section()
        self._build_tracking_section()
        self._build_activity_section()
        self._build_overlay_section()
        self._build_hotkeys_section()
        self._build_status()

    def _section(self, title: str) -> tk.LabelFrame:
        frame = tk.LabelFrame(self.main, text=title)
        frame.pack(fill="x", padx=10, pady=6)
        return frame

    def _build_header(self):
        self.title_label = tk.Label(
            self.main, textvariable=self.title_text_var, font=(UI_FONT, 18, "bold"))
        self.title_label.pack(pady=(12, 8))

    def _build_image_preview(self):
        self.image_frame = tk.Frame(self.main, width=390, height=190, relief="groove", bd=2)
        self.image_frame.pack_propagate(False)
        self.image_label = tk.Label(
            self.image_frame, text="No image selected yet", font=(UI_FONT, 10, "italic"))
        self.image_label.place(relx=0.5, rely=0.5, anchor="center")
        self.image_frame.pack(padx=10, pady=6)

        self.cool_text_label = tk.Label(
            self.main, textvariable=self.cool_text_var, font=(UI_FONT, 10, "bold"),
            wraplength=380)
        self.cool_text_label.pack(pady=(2, 10))

    def _build_controls(self):
        buttons = tk.Frame(self.main)
        buttons.pack(pady=(2, 6))
        tk.Button(buttons, text="Start", command=self.start, width=9).grid(row=0, column=0, padx=4, pady=3)
        tk.Button(buttons, text="Pause", command=self.stop, width=9).grid(row=0, column=1, padx=4, pady=3)
        tk.Button(buttons, text="Reset", command=self.reset, width=9).grid(row=0, column=2, padx=4, pady=3)
        tk.Button(buttons, text="SHOW OVERLAY", command=self.enter_overlay_mode,
                  width=16).grid(row=1, column=0, columnspan=2, padx=4, pady=3)
        tk.Button(buttons, text="History", command=self.open_history,
                  width=9).grid(row=1, column=2, padx=4, pady=3)

    def _build_live_stats(self):
        cal_frame = tk.Frame(self.main)
        cal_frame.pack(pady=(4, 2))
        tk.Label(cal_frame, text="Activity (estimated)", font=(UI_FONT, 11)).pack()
        self.activity_value_label = tk.Label(
            cal_frame, textvariable=self.calories_var, font=(UI_FONT, 32, "bold"))
        self.activity_value_label.pack()
        tk.Label(cal_frame, textvariable=self.activity_note_var,
                 font=(UI_FONT, 8, "italic")).pack()

        self.stats_frame = tk.LabelFrame(self.main, text="Session Stats")
        self.stats_frame.pack(fill="x", padx=10, pady=6)
        self.stat_vars = {}
        for key, name in (
            ("keys", "Tracked keys"), ("total", "Total presses"),
            ("time", "Session time"), ("avg_rate", "Presses / min (avg)"),
            ("rate", "Current rate (10s)"), ("peaks", "Peak 10s / 1m / 5m"),
            ("interval", "Avg interval"), ("consistency", "Consistency"),
            ("intensity", "Intensity"),
        ):
            self.stat_vars[key] = self._make_stat_row(self.stats_frame, name)

        graph_frame = tk.LabelFrame(self.main, text="Presses per minute (this session)")
        graph_frame.pack(fill="x", padx=10, pady=6)
        self.graph_canvas = tk.Canvas(graph_frame, height=90, highlightthickness=0)
        self.graph_canvas.pack(fill="x", padx=6, pady=6)

    def _build_key_stats(self):
        frame = tk.LabelFrame(self.main, text="Key Statistics")
        frame.pack(fill="x", padx=10, pady=6)
        tk.Label(frame, textvariable=self.key_stats_var, font=(MONO_FONT, 9),
                 justify="left", anchor="w").pack(fill="x", padx=8, pady=6)

    def _build_profile_section(self):
        frame = self._section("Profiles")

        row = tk.Frame(frame)
        row.pack(fill="x", padx=6, pady=4)
        tk.Label(row, text="Profile", width=18, anchor="w").pack(side="left")
        self.profile_menu = tk.OptionMenu(row, self.profile_choice_var, *self._profile_names())
        self.profile_menu.pack(side="left", fill="x", expand=True)
        tk.Button(row, text="Load", command=self.load_selected_profile).pack(side="right", padx=(6, 0))
        self._refresh_profile_menu()

        self._add_labeled_entry(frame, "Profile name", self.profile_name_var)
        buttons = tk.Frame(frame)
        buttons.pack(fill="x", padx=6, pady=5)
        tk.Button(buttons, text="Save Profile", command=self.save_profile).pack(side="left")
        tk.Button(buttons, text="Delete Profile", command=self.delete_profile).pack(side="left", padx=6)

    def _build_customization_section(self):
        self.settings_frame = self._section("Customization")
        frame = self.settings_frame

        self._add_labeled_entry(frame, "Overlay title", self.title_text_var)
        self._add_labeled_entry(frame, "Cool text", self.cool_text_var)

        row = tk.Frame(frame)
        row.pack(fill="x", padx=6, pady=4)
        tk.Label(row, text="Image file", width=18, anchor="w").pack(side="left")
        tk.Label(row, textvariable=self.image_display_var, wraplength=220,
                 anchor="w", justify="left").pack(side="left", fill="x", expand=True)

        image_buttons = tk.Frame(frame)
        image_buttons.pack(fill="x", padx=6, pady=5)
        tk.Button(image_buttons, text="Browse Image", command=self.browse_image).pack(side="left")
        tk.Button(image_buttons, text="Load Image", command=self.load_image).pack(side="left", padx=6)
        tk.Button(image_buttons, text="Remove Image", command=self.remove_image).pack(side="left")

        color_frame = tk.Frame(frame)
        color_frame.pack(fill="x", padx=6, pady=5)
        for column, (name, var) in enumerate((
            ("BG", self.bg_color_var), ("Panel", self.panel_color_var),
            ("Accent", self.accent_color_var), ("Text", self.text_color_var),
        )):
            self._add_color_input(color_frame, name, var, column)

        options = tk.Frame(frame)
        options.pack(fill="x", padx=6, pady=5)
        tk.Checkbutton(options, text="Always on top", variable=self.always_on_top_var,
                       command=self.apply_window_flags).pack(side="left")
        tk.Checkbutton(options, text="Borderless", variable=self.borderless_var,
                       command=self.apply_window_flags).pack(side="left", padx=10)

        alpha_frame = tk.Frame(frame)
        alpha_frame.pack(fill="x", padx=6, pady=5)
        tk.Label(alpha_frame, text="Window opacity").pack(side="left")
        tk.Scale(alpha_frame, from_=0.35, to=1.0, resolution=0.05, orient="horizontal",
                 variable=self.overlay_alpha_var, length=190,
                 command=lambda _: self.apply_window_flags()).pack(side="left", padx=8)

        tk.Button(frame, text="Apply Customization",
                  command=self.apply_customization).pack(pady=8)

    def _build_tracking_section(self):
        frame = self._section("Tracking")
        self._add_labeled_entry(frame, "Tracked keys", self.keys_var)
        tk.Label(frame, text="Comma separated. Ranges work: a-z, 0-9. Names: space, shift, enter...",
                 font=(UI_FONT, 8, "italic"), wraplength=380, justify="left",
                 anchor="w").pack(fill="x", padx=8)
        tk.Label(frame, text="Input mode", anchor="w").pack(fill="x", padx=8, pady=(6, 0))
        self._add_radio_group(frame, self.input_mode_var, INPUT_MODES, columns=1)
        self._add_labeled_entry(frame, "Debounce (ms)", self.debounce_var)
        tk.Checkbutton(frame, text="Save sessions to history on reset / exit",
                       variable=self.autosave_var).pack(anchor="w", padx=6, pady=4)

    def _build_activity_section(self):
        frame = self._section("Activity Model")
        self._add_radio_group(frame, self.activity_model_var, ACTIVITY_MODELS, columns=2)
        self._add_labeled_entry(frame, "Calories / key", self.cal_per_press_var)
        self._add_labeled_entry(frame, "Calories / minute", self.cal_per_min_var)
        self._add_labeled_entry(frame, "Formula", self.formula_var)
        tk.Label(
            frame,
            text="Formula variables: presses, minutes, seconds, rate\n"
                 "*Activity is an estimate from your configured model.",
            font=(UI_FONT, 8, "italic"), justify="left", anchor="w",
        ).pack(fill="x", padx=8, pady=(0, 4))

    def _build_overlay_section(self):
        frame = self._section("Overlay Layout")
        self._add_radio_group(frame, self.layout_var, OVERLAY_LAYOUTS, columns=4)
        tk.Label(frame, text="Custom layout shows:", anchor="w").pack(fill="x", padx=8, pady=(6, 0))
        grid = tk.Frame(frame)
        grid.pack(fill="x", padx=6, pady=2)
        for i, (name, label) in enumerate(STAT_LABELS.items()):
            tk.Checkbutton(grid, text=label.rstrip("*").capitalize(),
                           variable=self.custom_stat_vars[name]).grid(
                row=i // 3, column=i % 3, sticky="w", padx=4)
        self._add_labeled_entry(frame, "Card width", self.card_width_var)
        buttons = tk.Frame(frame)
        buttons.pack(fill="x", padx=6, pady=5)
        tk.Button(buttons, text="Reset card position",
                  command=self.reset_card_position).pack(side="left")
        tk.Label(frame, text="In the overlay: drag the card to move it, drag the image to move "
                             "the window, right-click for a menu.",
                 font=(UI_FONT, 8, "italic"), wraplength=380, justify="left",
                 anchor="w").pack(fill="x", padx=8, pady=(0, 4))

    def _build_hotkeys_section(self):
        frame = self._section("Global Hotkeys")
        for action, label in HOTKEY_ACTIONS.items():
            self._add_labeled_entry(frame, label, self.hotkey_vars[action])
        note = "Examples: f6, ctrl+f7, shift+f10. Applied with 'Apply Customization'."
        if pynput_keyboard is None:
            note = "pynput isn't available, so global hotkeys and tracking are disabled."
        tk.Label(frame, text=note, font=(UI_FONT, 8, "italic"), wraplength=380,
                 justify="left", anchor="w").pack(fill="x", padx=8, pady=(0, 4))

    def _build_status(self):
        self.status_label = tk.Label(
            self.main, textvariable=self.status_var, font=(UI_FONT, 10, "italic"),
            wraplength=400)
        self.status_label.pack(pady=(6, 12))

    # ========================================================
    # UI HELPERS
    # ========================================================

    def _add_labeled_entry(self, parent, label, variable):
        row = tk.Frame(parent)
        row.pack(fill="x", padx=6, pady=4)
        tk.Label(row, text=label, width=18, anchor="w").pack(side="left")
        tk.Entry(row, textvariable=variable).pack(side="left", fill="x", expand=True)

    def _add_color_input(self, parent, label, variable, column):
        frame = tk.Frame(parent)
        frame.grid(row=0, column=column, padx=3)
        tk.Label(frame, text=label).pack()
        tk.Entry(frame, textvariable=variable, width=8).pack()

    def _add_radio_group(self, parent, variable, options: dict, columns: int):
        grid = tk.Frame(parent)
        grid.pack(fill="x", padx=6, pady=2)
        for i, (value, text) in enumerate(options.items()):
            tk.Radiobutton(grid, text=text, value=value, variable=variable,
                           anchor="w").grid(row=i // columns, column=i % columns,
                                            sticky="w", padx=4)

    def _make_stat_row(self, parent, name):
        row = tk.Frame(parent)
        row.pack(fill="x", padx=8, pady=2)
        tk.Label(row, text=f"{name}:", width=19, anchor="w").pack(side="left")
        var = tk.StringVar(value="—")
        tk.Label(row, textvariable=var, anchor="e").pack(side="right")
        return var

    def set_status(self, text: str):
        self.status_var.set(text)

    def _run_state_text(self) -> str:
        return "Tracking..." if self.tracker.running else "Paused"

    # ========================================================
    # THEME
    # ========================================================

    def apply_theme(self):
        th = self.config.theme
        self.root.configure(bg=th.background)
        self.setup_container.set_colors(th.background, th.accent)
        self.main.configure(bg=th.panel)
        self.image_frame.configure(bg=th.panel)
        self._apply_widget_colors(self.main, th.panel, th.text, th.accent)
        # Overrides must come after the recursive pass.
        self.title_label.configure(fg=th.accent)
        self.activity_value_label.configure(fg=th.accent)
        self.graph_canvas.configure(bg=FIELD_BG)
        self._graph_cache = None

    def _apply_widget_colors(self, widget, bg, text, accent):
        hover = lighten(accent)
        for child in widget.winfo_children():
            if isinstance(child, tk.LabelFrame):
                child.configure(bg=bg, fg=text)
            elif isinstance(child, tk.Frame):
                child.configure(bg=bg)
            elif isinstance(child, tk.Label):
                child.configure(bg=bg, fg=text)
            elif isinstance(child, tk.Entry):
                child.configure(bg=FIELD_BG, fg=text, insertbackground=text)
            elif isinstance(child, tk.Menubutton):          # OptionMenu
                child.configure(bg=FIELD_BG, fg=text, activebackground=accent,
                                activeforeground="white", highlightthickness=0,
                                relief="flat")
            elif isinstance(child, tk.Menu):
                child.configure(bg=FIELD_BG, fg=text, activebackground=accent,
                                activeforeground="white", bd=0)
            elif isinstance(child, tk.Button):
                child.configure(bg=accent, fg="white", activebackground=hover,
                                activeforeground="white")
            elif isinstance(child, (tk.Checkbutton, tk.Radiobutton)):
                child.configure(bg=bg, fg=text, selectcolor=FIELD_BG,
                                activebackground=bg, activeforeground=text)
            elif isinstance(child, tk.Scale):
                child.configure(bg=bg, fg=text, troughcolor=FIELD_BG,
                                highlightbackground=bg)
            self._apply_widget_colors(child, bg, text, accent)

    def _valid_color(self, value: str) -> bool:
        try:
            self.root.winfo_rgb(value)
            return True
        except tk.TclError:
            return False

    # ========================================================
    # WINDOW FLAGS
    # ========================================================

    def apply_window_flags(self):
        self.root.attributes("-topmost", self.always_on_top_var.get())
        if not self.overlay_mode:
            self.root.overrideredirect(self.borderless_var.get())
            self.root.attributes("-alpha", self.overlay_alpha_var.get())

    # ========================================================
    # IMAGE LOADING
    # ========================================================

    def browse_image(self):
        selected = filedialog.askopenfilename(
            title="Select image",
            filetypes=[("Image files", "*.png *.jpg *.jpeg *.gif *.bmp *.webp"),
                       ("All files", "*.*")])
        if selected:
            self.image_path_var.set(selected)
            self.image_display_var.set(os.path.basename(selected))
            self.load_image()

    def _try_load_image(self, path: str) -> Optional[str]:
        """Returns an error message, or None on success."""
        try:
            self.images.load(path)
        except Exception as exc:
            self.images.clear()
            self._show_no_image()
            return f"Couldn't load image ({exc})."
        self.image_label.configure(image=self.images.preview_photo, text="")
        self.image_display_var.set(os.path.basename(path))
        return None

    def load_image(self):
        path = self.image_path_var.get().strip()
        if not path:
            messagebox.showwarning("No image", "Please select an image first.")
            return
        error = self._try_load_image(path)
        if error:
            messagebox.showerror("Image load failed", error)
        else:
            self.set_status("Image loaded successfully.")
            self._save_config_with_image(path)

    def _save_config_with_image(self, path: Optional[str]):
        self.config.profile = replace(self.config.profile, image_path=path)
        self._save_config()

    def remove_image(self):
        self.image_path_var.set("")
        self.image_display_var.set("None selected")
        self.images.clear()
        self._show_no_image()
        self._save_config_with_image(None)

    def _show_no_image(self):
        self.image_label.configure(image="", text="No image selected yet")

    # ========================================================
    # READING SETTINGS FROM THE UI
    # ========================================================

    def _read_profile_from_ui(self, warnings: list) -> Profile:
        keys = parse_key_list(self.keys_var.get())
        if not keys:
            warnings.append("No valid keys entered, using defaults.")
            keys = list(DEFAULT_TRACKED_KEYS)
        cpp = read_number(self.cal_per_press_var, 0, MAX_CAL_PER_PRESS,
                          DEFAULT_CALORIES_PER_PRESS, "Calories/key", warnings)
        cpm = read_number(self.cal_per_min_var, 0, MAX_CAL_PER_MINUTE,
                          DEFAULT_CALORIES_PER_MINUTE, "Calories/minute", warnings)
        self.keys_var.set(format_key_list(keys))
        return Profile(
            name=self.profile_name_var.get(),
            tracked_keys=keys,
            calories_per_press=cpp,
            title=self.title_text_var.get(),
            cool_text=self.cool_text_var.get(),
            image_path=self.image_path_var.get().strip() or None,
            activity_model=self.activity_model_var.get(),
            calories_per_minute=cpm,
            custom_formula=self.formula_var.get().strip() or DEFAULT_FORMULA,
            input_mode=self.input_mode_var.get(),
        )

    def _read_overlay_from_ui(self, warnings: list):
        o = self.config.overlay
        width = int(read_number(self.card_width_var, MIN_CARD_WIDTH, 700,
                                o.card_width, "Card width", warnings))
        custom = [n for n, v in self.custom_stat_vars.items() if v.get()]
        self.config.overlay = replace(
            o,
            layout=self.layout_var.get(),
            custom_stats=custom or ["presses"],
            opacity=self.overlay_alpha_var.get(),
            always_on_top=self.always_on_top_var.get(),
            borderless=self.borderless_var.get(),
            card_width=width,
        )

    def _read_hotkeys_from_ui(self, warnings: list):
        mapping = {a: v.get().strip().lower() for a, v in self.hotkey_vars.items()}
        failed = self.input.hotkeys.set_bindings(mapping)
        for action in failed:                       # revert bad / duplicate entries
            self.hotkey_vars[action].set(self.config.hotkeys[action])
            warnings.append(f"Hotkey for '{HOTKEY_ACTIONS[action]}' is invalid or a duplicate.")
        accepted = {a: (self.config.hotkeys[a] if a in failed else mapping[a])
                    for a in mapping}
        if failed:
            self.input.hotkeys.set_bindings(accepted)
        self.config.hotkeys = accepted

    def _read_theme_from_ui(self, warnings: list):
        old = self.config.theme
        values = {}
        for name, var, label in (
            ("background", self.bg_color_var, "BG"),
            ("panel", self.panel_color_var, "Panel"),
            ("accent", self.accent_color_var, "Accent"),
            ("text", self.text_color_var, "Text"),
        ):
            value = var.get().strip()
            if not self._valid_color(value):
                warnings.append(f"{label} colour '{value}' isn't valid.")
                value = getattr(old, name)
                var.set(value)
            values[name] = value
        self.config.theme = Theme(**values)

    # ========================================================
    # CUSTOMIZATION
    # ========================================================

    def apply_customization(self, quiet: bool = False):
        warnings = []
        profile = self._read_profile_from_ui(warnings)

        try:
            self.activity_model = build_activity_model(profile)
        except ValueError as exc:
            warnings.append(f"Formula problem: {exc}. Keeping the previous model.")

        self.config.profile = profile
        self.tracker.set_tracked_keys(profile.tracked_keys)

        debounce = int(read_number(self.debounce_var, 0, 200, 20, "Debounce", warnings))
        self.config.debounce_ms = debounce
        self.config.autosave_sessions = self.autosave_var.get()
        self.input.filter.configure(profile.input_mode, debounce)

        self._read_overlay_from_ui(warnings)
        self._read_hotkeys_from_ui(warnings)
        self._read_theme_from_ui(warnings)
        self.apply_theme()

        if profile.image_path != self.images.path:
            if profile.image_path:
                error = self._try_load_image(profile.image_path)
                if error:
                    warnings.append(error)
            else:
                self.images.clear()
                self._show_no_image()

        self._save_config()
        message = "Customization applied."
        if warnings:
            message += " " + " ".join(warnings)
        self.set_status(message)

    def _save_config(self):
        if not self.config_store.save(self.config):
            self.set_status("Couldn't save settings (check folder permissions).")

    # ========================================================
    # PROFILES
    # ========================================================

    def _profile_names(self) -> list:
        return list(self.profiles) or ["(none)"]

    def _refresh_profile_menu(self):
        menu = self.profile_menu["menu"]
        menu.delete(0, "end")
        for name in self._profile_names():
            menu.add_command(label=name,
                             command=lambda n=name: self.profile_choice_var.set(n))

    def _load_profile_into_ui(self, p: Profile):
        self.profile_name_var.set(p.name)
        self.title_text_var.set(p.title)
        self.cool_text_var.set(p.cool_text)
        self.keys_var.set(format_key_list(p.tracked_keys))
        self.input_mode_var.set(p.input_mode)
        self.activity_model_var.set(p.activity_model)
        self.cal_per_press_var.set(f"{p.calories_per_press:g}")
        self.cal_per_min_var.set(f"{p.calories_per_minute:g}")
        self.formula_var.set(p.custom_formula)
        if p.image_path:                    # a profile without an image keeps the current one
            self.image_path_var.set(p.image_path)
            self.image_display_var.set(os.path.basename(p.image_path))

    def load_selected_profile(self):
        name = self.profile_choice_var.get()
        profile = self.profiles.get(name)
        if profile is None:
            messagebox.showwarning("No profile", "Pick a saved profile first.")
            return
        if self.tracker.has_data() and not messagebox.askyesno(
                "Load profile",
                "Loading a profile ends the current session "
                "(it will be saved to history). Continue?"):
            return
        self._archive_session()
        self.tracker.reset()
        self._load_profile_into_ui(profile)
        self.apply_customization()
        self.set_status(f"Loaded profile '{name}'. " + self.status_var.get())

    def save_profile(self):
        self.apply_customization()
        profile = replace(self.config.profile)
        if not self.profile_name_var.get().strip():
            messagebox.showwarning("Name needed", "Give the profile a name first.")
            return
        self.profiles[profile.name] = profile
        if self.profile_store.save(self.profiles):
            self.set_status(f"Profile '{profile.name}' saved.")
        else:
            self.set_status("Couldn't save profiles (check folder permissions).")
        self._refresh_profile_menu()
        self.profile_choice_var.set(profile.name)

    def delete_profile(self):
        name = self.profile_choice_var.get()
        if name not in self.profiles:
            return
        if not messagebox.askyesno("Delete profile", f"Delete profile '{name}'?"):
            return
        del self.profiles[name]
        self.profile_store.save(self.profiles)
        self._refresh_profile_menu()
        self.profile_choice_var.set(next(iter(self.profiles), "(none)"))
        self.set_status(f"Profile '{name}' deleted.")

    # ========================================================
    # TRACKING CONTROLS
    # ========================================================

    def start(self):
        self.tracker.start()
        self.set_status("Tracking...")

    def stop(self):
        self.tracker.pause()
        self.set_status("Paused")

    def toggle_tracking(self):
        self.tracker.toggle()
        self.set_status(self._run_state_text())

    def reset(self):
        saved = self._archive_session()
        self.tracker.reset()
        state = "Tracking..." if self.tracker.running else "Stopped"
        self.set_status(("Session saved to history. " if saved else "") + state)

    def _archive_session(self) -> bool:
        """Save the current session to history if it has data. Returns True if saved."""
        if not self.config.autosave_sessions or not self.tracker.has_data():
            return False
        stats = self.tracker.get_stats()
        session = self.tracker.to_session(
            self.config.profile.name, self.activity_model.estimate(stats))
        saved = self.history.add(session)
        if self.history_window is not None:
            self.history_window.refresh()
        return saved

    def open_history(self):
        if self.history_window is not None:
            self.history_window.lift()
            self.history_window.refresh()
        else:
            self.history_window = HistoryWindow(self)

    # ========================================================
    # HOTKEY ACTIONS
    # ========================================================

    def _run_hotkey_action(self, action: str):
        if action == "start_pause":
            self.toggle_tracking()
        elif action == "reset":
            self.reset()
        elif action == "toggle_stats":
            self.toggle_overlay_stats()
        elif action == "show_setup":
            if self.overlay_mode:
                self.exit_overlay_mode()
            self.root.deiconify()
            self.root.lift()
        elif action == "toggle_overlay":
            if self.overlay_mode:
                self.exit_overlay_mode()
            else:
                self.enter_overlay_mode()
        elif action == "screenshot":
            self.take_screenshot()

    def _drain_ui_events(self):
        while True:
            try:
                action = self.ui_events.get_nowait()
            except queue.Empty:
                return
            self._run_hotkey_action(action)

    def take_screenshot(self):
        try:
            from PIL import ImageGrab
            self.root.update_idletasks()
            x, y = self.root.winfo_rootx(), self.root.winfo_rooty()
            w, h = self.root.winfo_width(), self.root.winfo_height()
            image = ImageGrab.grab(bbox=(x, y, x + w, y + h))
            SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
            path = SCREENSHOT_DIR / f"tracker_{datetime.now():%Y%m%d_%H%M%S}.png"
            image.save(path)
            self.set_status(f"Screenshot saved: {path}")
        except Exception as exc:
            self.set_status(f"Screenshot failed: {exc}")

    # ========================================================
    # OVERLAY
    # ========================================================

    def enter_overlay_mode(self):
        if self.overlay_mode:
            return
        self.apply_customization(quiet=True)

        cfg = self.config.overlay
        img_w, img_h = self.images.overlay_size
        layout = compute_layout(cfg, self.activity_model.enabled, img_w or None)
        canvas_w = max(img_w, layout.card_width + 2 * layout.padding)
        canvas_h = max(img_h, layout.card_height + 2 * layout.padding)

        self._setup_geometry = self.root.geometry()
        self.overlay_mode = True
        self.setup_container.pack_forget()

        self.root.overrideredirect(True)
        self.root.attributes("-topmost", cfg.always_on_top)
        self.root.attributes("-alpha", cfg.opacity)

        # The setup UI has a minimum size; drop it or Tk keeps the overlay
        # window oversized (the black/transparent region from the old version).
        self.root.minsize(1, 1)
        self.root.geometry(f"{canvas_w}x{canvas_h}")
        self.root.update_idletasks()

        x, y = self._overlay_window_position(canvas_w, canvas_h)
        self.root.geometry(f"{canvas_w}x{canvas_h}+{x}+{y}")

        card_pos = None
        if cfg.card_x is not None and cfg.card_y is not None:
            card_pos = (cfg.card_x, cfg.card_y)

        self.overlay_view = OverlayView(
            self.root, self.config.theme, layout, (canvas_w, canvas_h),
            self.images.overlay_photo if self.images.loaded else None,
            (img_w, img_h), card_pos,
            on_card_moved=self._on_card_moved,
            on_window_moved=self._on_window_moved,
            on_context_menu=self._show_overlay_menu,
        )

    def _overlay_window_position(self, width: int, height: int) -> tuple:
        cfg = self.config.overlay
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        if cfg.window_x is not None and cfg.window_y is not None:
            x = max(0, min(cfg.window_x, max(0, screen_w - width)))
            y = max(0, min(cfg.window_y, max(0, screen_h - height)))
            return x, y
        return (screen_w - width) // 2, (screen_h - height) // 2

    def exit_overlay_mode(self):
        if not self.overlay_mode:
            return
        self.overlay_mode = False

        if self.overlay_view is not None:
            self.overlay_view.destroy()
            self.overlay_view = None

        self.root.overrideredirect(self.borderless_var.get())
        self.root.attributes("-alpha", 1.0)
        self.root.attributes("-topmost", self.always_on_top_var.get())
        self.root.minsize(MIN_SETUP_WIDTH, MIN_SETUP_HEIGHT)
        self.root.geometry(self._setup_geometry or f"{SETUP_WIDTH}x{SETUP_HEIGHT}")

        self.setup_container.pack(fill="both", expand=True)
        self.setup_container.refresh_scrollbar()
        self.set_status(self._run_state_text())

    def toggle_overlay_stats(self):
        if self.overlay_mode and self.overlay_view is not None:
            self.overlay_view.toggle_stats()

    def _on_escape_key(self, _event=None):
        if self.overlay_mode:
            self.exit_overlay_mode()

    def _on_card_moved(self, x: int, y: int):
        self.config.overlay = replace(self.config.overlay, card_x=x, card_y=y)
        self._save_config()

    def _on_window_moved(self, x: int, y: int):
        self.config.overlay = replace(self.config.overlay, window_x=x, window_y=y)
        self._save_config()

    def reset_card_position(self):
        self.config.overlay = replace(self.config.overlay, card_x=None, card_y=None)
        self._save_config()
        self.set_status("Card position reset to the default.")

    def _show_overlay_menu(self, event):
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Pause" if self.tracker.running else "Start",
                         command=self.toggle_tracking)
        menu.add_command(label="Reset", command=self.reset)
        menu.add_command(label="Hide / show stats", command=self.toggle_overlay_stats)
        menu.add_command(label="Back to setup", command=self.exit_overlay_mode)
        menu.add_separator()
        menu.add_command(label="Quit", command=self.on_close)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _overlay_values(self, stats: Stats, activity: Optional[float]) -> dict:
        if len(stats.keys) <= 4:
            keys_text = "  ".join(
                f"{k.upper()} {stats.counts[k]:,}" for k in stats.counts) or "NONE"
        else:
            keys_text = f"{len(stats.counts)} keys"
        return {
            "keys": keys_text,
            "presses": f"{stats.total:,}",
            "rate": f"{stats.recent_rate:.0f}/min",
            "peak": format_rate(stats.peaks[RATE_WINDOW]),
            "activity": "—" if activity is None else f"{activity:.2f} kcal",
            "time": format_duration(stats.elapsed),
            "consistency": ("—" if stats.consistency is None
                            else f"{stats.consistency:.0f}%"),
        }

    # ========================================================
    # UPDATE LOOP
    # ========================================================

    @staticmethod
    def _format_key_stats(stats: Stats, max_rows: int = 8) -> str:
        if stats.total == 0:
            return "No presses yet."
        lines = []
        for ks in stats.keys[:max_rows]:
            bar = "█" * round(ks.share / 100 * 14)
            lines.append(f"{ks.key.upper()[:7]:<7} {bar:<14} {ks.share:5.1f}%  "
                         f"{ks.count:>7,}  {ks.rate:>4.0f}/min")
        if len(stats.keys) > max_rows:
            lines.append(f"... +{len(stats.keys) - max_rows} more keys")
        return "\n".join(lines)

    def _update_setup_stats(self, stats: Stats, activity: Optional[float]):
        v = self.stat_vars
        if len(stats.counts) <= 8:
            keys_text = ", ".join(f"{k.upper()}={c:,}" for k, c in stats.counts.items())
        else:
            keys_text = f"{len(stats.counts)} keys"
        v["keys"].set(keys_text or "None")
        v["total"].set(f"{stats.total:,}")
        v["time"].set(format_duration(stats.elapsed))
        v["avg_rate"].set(f"{stats.avg_rate:.1f}")
        v["rate"].set(f"{stats.recent_rate:.0f}/min")
        v["peaks"].set(" / ".join(
            "—" if stats.peaks[w] is None else f"{stats.peaks[w]:.0f}"
            for w in PEAK_WINDOWS))
        v["interval"].set("—" if stats.avg_interval is None
                          else f"{stats.avg_interval * 1000:.0f} ms")
        v["consistency"].set("—" if stats.consistency is None
                             else f"{stats.consistency:.0f}%")
        v["intensity"].set(stats.intensity)

        if activity is None:
            self.calories_var.set("—")
            self.activity_note_var.set("Activity tracking is disabled")
        else:
            self.calories_var.set(f"{activity:.2f}")
            self.activity_note_var.set("kcal* - estimated from your activity model")

        self.key_stats_var.set(self._format_key_stats(stats))

        recent = tuple(stats.per_minute[-30:])
        if recent != self._graph_cache:
            self._graph_cache = recent
            th = self.config.theme
            first = max(0, len(stats.per_minute) - 30)
            labels = [str(first + i + 1) for i in range(len(recent))]
            draw_bar_chart(self.graph_canvas, list(recent), labels,
                           th.accent, th.text, "#2b3050")

    def _tick(self):
        if self._closing:
            return
        try:
            self._drain_ui_events()
            stats = self.tracker.get_stats()
            activity = self.activity_model.estimate(stats)
            if self.overlay_mode:
                if self.overlay_view is not None:
                    self.overlay_view.update(self._overlay_values(stats, activity))
            else:
                self._update_setup_stats(stats, activity)
        except Exception:
            log(traceback.format_exc())
        finally:
            if not self._closing:
                self.root.after(UPDATE_INTERVAL_MS, self._tick)

    # ========================================================
    # SHUTDOWN
    # ========================================================

    def on_close(self):
        if self._closing:
            return
        self._closing = True
        try:
            self._archive_session()
            self._save_config()
        finally:
            self.input.stop()
            self.root.destroy()


# ============================================================
# 10. START PROGRAM
# ============================================================

def main():
    app = CalorieTrackerApp()
    app.run()


if __name__ == "__main__":
    main()
