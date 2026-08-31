"""
osu! Calorie Tracker Overlay
============================

Fun overlay that tracks osu!-style key presses globally and estimates calories.

Install requirement:
    pip install pynput

Run:
    python calorie.py
"""

import tkinter as tk
from tkinter import filedialog, messagebox
import threading
import time

from pynput import keyboard


DEFAULT_CALORIES_PER_PRESS = 0.03
DEFAULT_TRACKED_KEYS = {"z", "x"}
UPDATE_INTERVAL_MS = 200


class TrackerState:
    def __init__(self):
        self.lock = threading.Lock()
        self.tracked_keys = set(DEFAULT_TRACKED_KEYS)
        self.reset()

    def reset(self):
        with self.lock:
            self.key_counts = {}
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

    def set_tracked_keys(self, keys):
        normalized = {k.strip().lower() for k in keys if k.strip()}
        if not normalized:
            normalized = set(DEFAULT_TRACKED_KEYS)

        with self.lock:
            self.tracked_keys = normalized
            self.key_counts = {k: self.key_counts.get(k, 0) for k in self.tracked_keys}

    def is_tracked_key(self, key_char):
        with self.lock:
            return key_char in self.tracked_keys

    def register_key(self, key_char):
        with self.lock:
            if not self.running or key_char not in self.tracked_keys:
                return
            self.key_counts[key_char] = self.key_counts.get(key_char, 0) + 1

    def snapshot(self):
        with self.lock:
            ordered_keys = sorted(self.tracked_keys)
            counts = {k: self.key_counts.get(k, 0) for k in ordered_keys}
            total = sum(counts.values())

            if self.running:
                elapsed = self.elapsed_paused + (time.time() - self.start_time)
            else:
                elapsed = self.elapsed_paused

            return {
                "counts": counts,
                "total": total,
                "elapsed": elapsed,
                "running": self.running,
            }


state = TrackerState()


def on_press(key):
    try:
        char = key.char.lower()
    except AttributeError:
        return

    if state.is_tracked_key(char):
        state.register_key(char)


listener = keyboard.Listener(on_press=on_press)
listener.daemon = True
listener.start()


class CalorieTrackerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("osu! Calorie Tracker Overlay")
        self._configure_initial_window()
        self.root.attributes("-topmost", True)

        self.title_text_var = tk.StringVar(value="🔥 osu! Calorie Tracker 🔥")
        self.cal_per_press_var = tk.StringVar(value=str(DEFAULT_CALORIES_PER_PRESS))
        self.keys_var = tk.StringVar(value=", ".join(sorted(DEFAULT_TRACKED_KEYS)))
        self.image_path_var = tk.StringVar(value="")
        self.overlay_alpha_var = tk.DoubleVar(value=0.95)
        self.always_on_top_var = tk.BooleanVar(value=True)
        self.borderless_var = tk.BooleanVar(value=False)
        self.cool_text_var = tk.StringVar(value="Keep tapping and burn those pixels!")

        self.bg_color_var = tk.StringVar(value="#15182a")
        self.panel_color_var = tk.StringVar(value="#232946")
        self.accent_color_var = tk.StringVar(value="#ff4d8d")
        self.text_color_var = tk.StringVar(value="#f4f4ff")

        self.overlay_image = None
        self.key_labels = {}

        self._build_widgets()
        self.apply_theme()
        self._tick()

    def _configure_initial_window(self):
        preferred_width = 420
        preferred_height = 640
        min_width = 360
        min_height = 420
        margin = 80

        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()

        width = min(preferred_width, max(min_width, screen_width - margin))
        height = min(preferred_height, max(min_height, screen_height - margin))
        x = max((screen_width - width) // 2, 0)
        y = max((screen_height - height) // 2, 0)

        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.minsize(min(min_width, width), min(min_height, height))
        self.root.resizable(True, True)

    def _build_widgets(self):
        self.main = tk.Frame(self.root, bd=2, relief="ridge")
        self.main.pack(fill="both", expand=True, padx=8, pady=8)

        self.title_label = tk.Label(
            self.main,
            textvariable=self.title_text_var,
            font=("Segoe UI", 16, "bold"),
        )
        self.title_label.pack(pady=(8, 4))

        self.image_label = tk.Label(
            self.main,
            text="No anime image selected yet",
            font=("Segoe UI", 10, "italic"),
            width=42,
            height=10,
            relief="groove",
        )
        self.image_label.pack(padx=10, pady=6)

        self.cool_text_label = tk.Label(
            self.main,
            textvariable=self.cool_text_var,
            font=("Segoe UI", 10, "bold"),
            wraplength=360,
            justify="center",
        )
        self.cool_text_label.pack(pady=(0, 8))

        settings = tk.LabelFrame(self.main, text="Customization")
        settings.pack(fill="x", padx=10, pady=6)

        self._add_labeled_entry(settings, "Overlay title", self.title_text_var)
        self._add_labeled_entry(settings, "Cool text", self.cool_text_var)
        self._add_labeled_entry(settings, "Calories / key", self.cal_per_press_var)
        self._add_labeled_entry(settings, "Tracked keys (comma)", self.keys_var)
        self._add_labeled_entry(settings, "Image path", self.image_path_var)

        image_controls = tk.Frame(settings)
        image_controls.pack(fill="x", padx=6, pady=3)
        tk.Button(image_controls, text="Browse Image", command=self.browse_image).pack(
            side="left"
        )
        tk.Button(image_controls, text="Load Image", command=self.load_image).pack(
            side="left", padx=6
        )

        color_frame = tk.Frame(settings)
        color_frame.pack(fill="x", padx=6, pady=4)
        self._add_color_input(color_frame, "BG", self.bg_color_var, 0)
        self._add_color_input(color_frame, "Panel", self.panel_color_var, 1)
        self._add_color_input(color_frame, "Accent", self.accent_color_var, 2)
        self._add_color_input(color_frame, "Text", self.text_color_var, 3)

        toggle_frame = tk.Frame(settings)
        toggle_frame.pack(fill="x", padx=6, pady=4)
        tk.Checkbutton(
            toggle_frame,
            text="Always on top",
            variable=self.always_on_top_var,
            command=self.apply_window_flags,
        ).pack(side="left")
        tk.Checkbutton(
            toggle_frame,
            text="Borderless overlay",
            variable=self.borderless_var,
            command=self.apply_window_flags,
        ).pack(side="left", padx=8)

        alpha_frame = tk.Frame(settings)
        alpha_frame.pack(fill="x", padx=6, pady=4)
        tk.Label(alpha_frame, text="Overlay opacity").pack(side="left")
        tk.Scale(
            alpha_frame,
            from_=0.35,
            to=1.0,
            resolution=0.05,
            orient="horizontal",
            variable=self.overlay_alpha_var,
            command=lambda _x: self.apply_window_flags(),
            length=180,
        ).pack(side="left", padx=8)

        tk.Button(
            settings,
            text="Apply customization",
            command=self.apply_customization,
        ).pack(pady=5)

        stats = tk.LabelFrame(self.main, text="Session Stats")
        stats.pack(fill="x", padx=10, pady=6)

        self.keys_stat_label = self._make_stat_row(stats, "Tracked keys")
        self.total_label = self._make_stat_row(stats, "Total presses")
        self.time_label = self._make_stat_row(stats, "Session time")
        self.rate_label = self._make_stat_row(stats, "Presses / min")

        cal_frame = tk.Frame(self.main)
        cal_frame.pack(pady=8)

        self.cal_title = tk.Label(
            cal_frame,
            text="Calories burned",
            font=("Segoe UI", 11),
        )
        self.cal_title.pack()

        self.calories_var = tk.StringVar(value="0.00")
        self.calories_label = tk.Label(
            cal_frame,
            textvariable=self.calories_var,
            font=("Segoe UI", 30, "bold"),
        )
        self.calories_label.pack()

        btn_frame = tk.Frame(self.main)
        btn_frame.pack(pady=8)
        tk.Button(btn_frame, text="Start", command=self.start).grid(row=0, column=0, padx=4)
        tk.Button(btn_frame, text="Pause", command=self.stop).grid(row=0, column=1, padx=4)
        tk.Button(btn_frame, text="Reset", command=self.reset).grid(row=0, column=2, padx=4)

        self.status_var = tk.StringVar(value="Stopped")
        self.status_label = tk.Label(
            self.main,
            textvariable=self.status_var,
            font=("Segoe UI", 10, "italic"),
        )
        self.status_label.pack(pady=(2, 8))

    def _add_labeled_entry(self, parent, label, variable):
        row = tk.Frame(parent)
        row.pack(fill="x", padx=6, pady=3)
        tk.Label(row, text=label, width=18, anchor="w").pack(side="left")
        tk.Entry(row, textvariable=variable).pack(side="left", fill="x", expand=True)

    def _add_color_input(self, parent, label, variable, column):
        frame = tk.Frame(parent)
        frame.grid(row=0, column=column, padx=3)
        tk.Label(frame, text=label).pack()
        tk.Entry(frame, textvariable=variable, width=8).pack()

    def _make_stat_row(self, parent, name):
        row = tk.Frame(parent)
        row.pack(fill="x", padx=8, pady=2)
        tk.Label(row, text=f"{name}:", width=16, anchor="w").pack(side="left")
        var = tk.StringVar(value="0")
        tk.Label(row, textvariable=var, anchor="e").pack(side="right")
        return var

    def browse_image(self):
        selected = filedialog.askopenfilename(
            title="Select anime image",
            filetypes=[
                ("Image files", "*.png *.gif *.ppm *.pgm"),
                ("PNG", "*.png"),
                ("GIF", "*.gif"),
                ("All files", "*.*"),
            ],
        )
        if selected:
            self.image_path_var.set(selected)

    def load_image(self):
        path = self.image_path_var.get().strip()
        if not path:
            self.overlay_image = None
            self.image_label.config(image="", text="No anime image selected yet")
            return

        try:
            image = tk.PhotoImage(file=path)
            self.overlay_image = image
            self.image_label.config(image=image, text="")
        except tk.TclError as exc:
            messagebox.showerror(
                "Image load failed",
                f"Couldn't load this image.\nUse PNG/GIF/PPM/PGM.\n\n{exc}",
            )

    def apply_window_flags(self):
        self.root.attributes("-topmost", self.always_on_top_var.get())
        self.root.overrideredirect(self.borderless_var.get())
        self.root.attributes("-alpha", self.overlay_alpha_var.get())

    def apply_theme(self):
        bg = self.bg_color_var.get().strip() or "#15182a"
        panel = self.panel_color_var.get().strip() or "#232946"
        accent = self.accent_color_var.get().strip() or "#ff4d8d"
        text = self.text_color_var.get().strip() or "#f4f4ff"

        self.root.configure(bg=bg)
        self.main.configure(bg=panel, highlightbackground=accent, highlightcolor=accent)

        for widget in self.main.winfo_children():
            if isinstance(widget, tk.LabelFrame):
                widget.configure(bg=panel, fg=text)
                for child in widget.winfo_children():
                    self._apply_widget_colors(child, panel, text, accent)
            else:
                self._apply_widget_colors(widget, panel, text, accent)

        self.title_label.configure(fg=accent)
        self.calories_label.configure(fg=accent)

    def _apply_widget_colors(self, widget, bg, text, accent):
        if isinstance(widget, tk.Frame):
            widget.configure(bg=bg)
            for child in widget.winfo_children():
                self._apply_widget_colors(child, bg, text, accent)
            return

        if isinstance(widget, tk.Label):
            widget.configure(bg=bg, fg=text)
        elif isinstance(widget, tk.Entry):
            widget.configure(
                bg="#0d1020", fg=text, insertbackground=text, highlightbackground=accent
            )
        elif isinstance(widget, tk.Button):
            widget.configure(bg=accent, fg="#ffffff", activebackground="#ff7aad")
        elif isinstance(widget, tk.Checkbutton):
            widget.configure(
                bg=bg,
                fg=text,
                selectcolor="#0d1020",
                activebackground=bg,
                activeforeground=text,
            )
        elif isinstance(widget, tk.Scale):
            widget.configure(bg=bg, fg=text, troughcolor="#0d1020", highlightbackground=bg)

    def apply_customization(self):
        key_names = [part.strip() for part in self.keys_var.get().split(",")]
        state.set_tracked_keys(key_names)
        self.apply_window_flags()
        self.apply_theme()
        self.load_image()

    def start(self):
        state.start()
        self.status_var.set("Tracking... go click osu! and play")

    def stop(self):
        state.stop()
        self.status_var.set("Paused")

    def reset(self):
        was_running = state.snapshot()["running"]
        state.reset()
        if was_running:
            state.start()
            self.status_var.set("Tracking... go click osu! and play")
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

    def _tick(self):
        snap = state.snapshot()

        keys_summary = ", ".join(
            f"{key.upper()}={count}" for key, count in snap["counts"].items()
        )
        self.keys_stat_label.set(keys_summary or "None")
        self.total_label.set(str(snap["total"]))

        elapsed = snap["elapsed"]
        minutes, seconds = divmod(int(elapsed), 60)
        self.time_label.set(f"{minutes:02d}:{seconds:02d}")

        presses_per_min = (snap["total"] / elapsed * 60) if elapsed > 0 else 0
        self.rate_label.set(f"{presses_per_min:.1f}")

        calories = snap["total"] * self._get_cal_per_press()
        self.calories_var.set(f"{calories:.2f}")

        self.root.after(UPDATE_INTERVAL_MS, self._tick)


if __name__ == "__main__":
    root = tk.Tk()
    app = CalorieTrackerApp(root)
    root.mainloop()
