"""
osu! Calorie Tracker
=====================

Tracks how many times you press Z and X (osu!'s standard hit keys)
and converts that into an estimated calorie count.

The key listener runs globally, so it keeps counting even while
osu! (or any other window) is focused -- you don't need to click
back into this app.

Install requirement:
    pip install pynput

Run:
    python osu_calorie_tracker.py
"""

import tkinter as tk
from tkinter import ttk
import threading
import time

from pynput import keyboard


# ============================================================
# SETTINGS
# ============================================================

# Estimated calories burned per key press.
# This is arbitrary/for-fun -- tweak it in the app itself
# with the "cal per press" box, or change the default here.
DEFAULT_CALORIES_PER_PRESS = 0.03

# Which keys count as "hits"
TRACKED_KEYS = {"z", "x"}

UPDATE_INTERVAL_MS = 200  # how often the GUI refreshes


# ============================================================
# TRACKER STATE (shared between listener thread and GUI thread)
# ============================================================

class TrackerState:

    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        with self.lock:
            self.z_count = 0
            self.x_count = 0
            self.start_time = None
            self.elapsed_paused = 0.0
            self.running = False

    def start(self):
        with self.lock:
            if not self.running:
                self.running = True
                self.start_time = time.time()

    def stop(self):
        with self.lock:
            if self.running:
                self.elapsed_paused += time.time() - self.start_time
                self.running = False

    def register_key(self, key_char):
        with self.lock:
            if not self.running:
                return
            if key_char == "z":
                self.z_count += 1
            elif key_char == "x":
                self.x_count += 1

    def snapshot(self):
        with self.lock:
            total = self.z_count + self.x_count

            if self.running:
                elapsed = self.elapsed_paused + (time.time() - self.start_time)
            else:
                elapsed = self.elapsed_paused

            return {
                "z": self.z_count,
                "x": self.x_count,
                "total": total,
                "elapsed": elapsed,
                "running": self.running,
            }


state = TrackerState()


# ============================================================
# GLOBAL KEY LISTENER
# ============================================================

def on_press(key):
    try:
        char = key.char.lower()
    except AttributeError:
        return

    if char in TRACKED_KEYS:
        state.register_key(char)


listener = keyboard.Listener(on_press=on_press)
listener.daemon = True
listener.start()


# ============================================================
# GUI
# ============================================================

class CalorieTrackerApp:

    def __init__(self, root):
        self.root = root
        self.root.title("osu! Calorie Tracker")
        self.root.geometry("380x420")
        self.root.resizable(False, False)

        self._build_widgets()
        self._tick()

    # --------------------------------------------------------
    # WIDGETS
    # --------------------------------------------------------

    def _build_widgets(self):

        pad = {"padx": 10, "pady": 6}

        title = ttk.Label(
            self.root,
            text="osu! Calorie Tracker",
            font=("Segoe UI", 16, "bold")
        )
        title.pack(pady=(15, 5))

        # ---- Calories per press setting ----

        settings_frame = ttk.Frame(self.root)
        settings_frame.pack(**pad)

        ttk.Label(settings_frame, text="Calories per key press:").pack(side="left")

        self.cal_per_press_var = tk.StringVar(
            value=str(DEFAULT_CALORIES_PER_PRESS)
        )
        cal_entry = ttk.Entry(
            settings_frame,
            textvariable=self.cal_per_press_var,
            width=8
        )
        cal_entry.pack(side="left", padx=5)

        # ---- Stats ----

        stats_frame = ttk.LabelFrame(self.root, text="Session Stats")
        stats_frame.pack(fill="x", **pad)

        self.z_label = self._make_stat_row(stats_frame, "Z presses:")
        self.x_label = self._make_stat_row(stats_frame, "X presses:")
        self.total_label = self._make_stat_row(stats_frame, "Total presses:")
        self.time_label = self._make_stat_row(stats_frame, "Session time:")
        self.rate_label = self._make_stat_row(stats_frame, "Presses / min:")

        # ---- Calories display ----

        cal_frame = ttk.Frame(self.root)
        cal_frame.pack(pady=15)

        ttk.Label(
            cal_frame,
            text="Calories burned",
            font=("Segoe UI", 11)
        ).pack()

        self.calories_var = tk.StringVar(value="0.00")
        ttk.Label(
            cal_frame,
            textvariable=self.calories_var,
            font=("Segoe UI", 28, "bold")
        ).pack()

        # ---- Buttons ----

        btn_frame = ttk.Frame(self.root)
        btn_frame.pack(pady=10)

        self.start_btn = ttk.Button(
            btn_frame, text="Start", command=self.start
        )
        self.start_btn.grid(row=0, column=0, padx=5)

        self.stop_btn = ttk.Button(
            btn_frame, text="Pause", command=self.stop
        )
        self.stop_btn.grid(row=0, column=1, padx=5)

        self.reset_btn = ttk.Button(
            btn_frame, text="Reset", command=self.reset
        )
        self.reset_btn.grid(row=0, column=2, padx=5)

        # ---- Status ----

        self.status_var = tk.StringVar(value="Stopped")
        self.status_label = ttk.Label(
            self.root,
            textvariable=self.status_var,
            font=("Segoe UI", 10, "italic")
        )
        self.status_label.pack(pady=(5, 0))

        note = ttk.Label(
            self.root,
            text="Tracks Z / X presses even while osu! is focused.",
            font=("Segoe UI", 8),
            foreground="gray"
        )
        note.pack(pady=(10, 0))

    def _make_stat_row(self, parent, label_text):
        row = ttk.Frame(parent)
        row.pack(fill="x", padx=8, pady=2)

        ttk.Label(row, text=label_text, width=16, anchor="w").pack(side="left")

        value_var = tk.StringVar(value="0")
        ttk.Label(row, textvariable=value_var, anchor="e").pack(side="right")

        return value_var

    # --------------------------------------------------------
    # ACTIONS
    # --------------------------------------------------------

    def start(self):
        state.start()
        self.status_var.set("Tracking... (switch to osu! now)")

    def stop(self):
        state.stop()
        self.status_var.set("Paused")

    def reset(self):
        was_running = state.snapshot()["running"]
        state.reset()
        if was_running:
            state.start()
            self.status_var.set("Tracking... (switch to osu! now)")
        else:
            self.status_var.set("Stopped")

    def _get_cal_per_press(self):
        try:
            value = float(self.cal_per_press_var.get())
            if value < 0:
                return DEFAULT_CALORIES_PER_PRESS
            return value
        except ValueError:
            return DEFAULT_CALORIES_PER_PRESS

    # --------------------------------------------------------
    # LIVE UPDATE LOOP
    # --------------------------------------------------------

    def _tick(self):
        snap = state.snapshot()

        self.z_label.set(str(snap["z"]))
        self.x_label.set(str(snap["x"]))
        self.total_label.set(str(snap["total"]))

        elapsed = snap["elapsed"]
        minutes, seconds = divmod(int(elapsed), 60)
        self.time_label.set(f"{minutes:02d}:{seconds:02d}")

        presses_per_min = (
            (snap["total"] / elapsed * 60) if elapsed > 0 else 0
        )
        self.rate_label.set(f"{presses_per_min:.1f}")

        cal_per_press = self._get_cal_per_press()
        calories = snap["total"] * cal_per_press
        self.calories_var.set(f"{calories:.2f}")

        self.root.after(UPDATE_INTERVAL_MS, self._tick)


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    root = tk.Tk()
    app = CalorieTrackerApp(root)
    root.mainloop()