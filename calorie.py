import tkinter as tk
from tkinter import filedialog, messagebox
from PIL import Image, ImageTk
import threading
import time
import os

from pynput import keyboard


# ============================================================
# SETTINGS
# ============================================================

DEFAULT_CALORIES_PER_PRESS = 0.03
DEFAULT_TRACKED_KEYS = {"x", "z"}

SETUP_WIDTH = 440
SETUP_HEIGHT = 700

# Maximum size of the overlay image
MAX_OVERLAY_WIDTH = 750
MAX_OVERLAY_HEIGHT = 650

UPDATE_INTERVAL_MS = 100


# ============================================================
# TRACKER STATE
# ============================================================

class TrackerState:

    def __init__(self):
        self.lock = threading.Lock()

        self.tracked_keys = set(
            DEFAULT_TRACKED_KEYS
        )

        self.reset()

    def reset(self):

        with self.lock:

            self.key_counts = {
                key: 0
                for key in self.tracked_keys
            }

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

                self.elapsed_paused += (
                    time.time() - self.start_time
                )

                self.running = False

    def set_tracked_keys(self, keys):

        normalized = {
            key.strip().lower()
            for key in keys
            if key.strip()
        }

        if not normalized:

            normalized = set(
                DEFAULT_TRACKED_KEYS
            )

        with self.lock:

            self.tracked_keys = normalized

            self.key_counts = {
                key: self.key_counts.get(
                    key,
                    0
                )
                for key in normalized
            }

    def is_tracked_key(self, key):

        with self.lock:
            return key in self.tracked_keys

    def register_key(self, key):

        with self.lock:

            if not self.running:
                return

            if key not in self.tracked_keys:
                return

            self.key_counts[key] = (
                self.key_counts.get(key, 0) + 1
            )

    def snapshot(self):

        with self.lock:

            counts = {
                key: self.key_counts.get(
                    key,
                    0
                )
                for key in sorted(
                    self.tracked_keys
                )
            }

            total = sum(
                counts.values()
            )

            if self.running:

                elapsed = (
                    self.elapsed_paused
                    + time.time()
                    - self.start_time
                )

            else:

                elapsed = self.elapsed_paused

            return {
                "counts": counts,
                "total": total,
                "elapsed": elapsed,
                "running": self.running
            }


state = TrackerState()


# ============================================================
# KEYBOARD LISTENER
# ============================================================

def normalize_key(key):

    try:

        if key.char:
            return key.char.lower()

    except AttributeError:
        pass

    name = getattr(
        key,
        "name",
        None
    )

    if name:
        return name.lower()

    return None


def on_press(key):

    normalized = normalize_key(key)

    if normalized is None:
        return

    if state.is_tracked_key(
        normalized
    ):

        state.register_key(
            normalized
        )


listener = keyboard.Listener(
    on_press=on_press
)

listener.daemon = True
listener.start()


# ============================================================
# MAIN APPLICATION
# ============================================================

class CalorieTrackerApp:

    def __init__(self, root):

        self.root = root

        self.root.title(
            "Rhythm Calorie Tracker Overlay"
        )

        self.root.geometry(
            f"{SETUP_WIDTH}x{SETUP_HEIGHT}"
        )

        # Minimum size is ONLY for the setup UI.
        self.root.minsize(
            360,
            500
        )

        # ----------------------------------------------------
        # VARIABLES
        # ----------------------------------------------------

        self.title_text_var = tk.StringVar(
            value="🔥 Rhythm Calorie Tracker 🔥"
        )

        self.cool_text_var = tk.StringVar(
            value="Keep tapping and burn those pixels!"
        )

        self.cal_per_press_var = tk.StringVar(
            value=str(
                DEFAULT_CALORIES_PER_PRESS
            )
        )

        self.keys_var = tk.StringVar(
            value=", ".join(
                sorted(
                    DEFAULT_TRACKED_KEYS
                )
            )
        )

        self.image_path_var = tk.StringVar(
            value=""
        )

        self.image_display_var = tk.StringVar(
            value="None selected"
        )

        self.overlay_alpha_var = tk.DoubleVar(
            value=0.95
        )

        self.always_on_top_var = tk.BooleanVar(
            value=True
        )

        self.borderless_var = tk.BooleanVar(
            value=False
        )

        self.bg_color_var = tk.StringVar(
            value="#15182a"
        )

        self.panel_color_var = tk.StringVar(
            value="#232946"
        )

        self.accent_color_var = tk.StringVar(
            value="#ff4d8d"
        )

        self.text_color_var = tk.StringVar(
            value="#f4f4ff"
        )

        # ----------------------------------------------------
        # IMAGE STATE
        # ----------------------------------------------------

        self.original_image = None

        self.preview_image = None

        self.overlay_pil_image = None
        self.overlay_photo = None

        self.overlay_width = 0
        self.overlay_height = 0

        # ----------------------------------------------------
        # OVERLAY STATE
        # ----------------------------------------------------

        self.overlay_mode = False

        self.overlay_canvas = None

        self.stats_hidden = False

        self.overlay_drag_x = 0
        self.overlay_drag_y = 0

        # ----------------------------------------------------
        # SCROLLBAR STATE
        # ----------------------------------------------------

        self.scroll_thumb = None
        self.scroll_drag_start = 0

        # ----------------------------------------------------
        # BUILD
        # ----------------------------------------------------

        self._build_setup_ui()

        self.apply_theme()

        self._tick()

        # ESC = exit overlay
        self.root.bind(
            "<Escape>",
            self._on_escape_key
        )

        # F8 = hide/show overlay stats
        self.root.bind(
            "<F8>",
            self._toggle_overlay_stats
        )


    # ========================================================
    # SCROLLABLE UI
    # ========================================================

    def _build_setup_ui(self):

        self.setup_container = tk.Frame(
            self.root
        )

        self.setup_container.pack(
            fill="both",
            expand=True
        )

        # Main scrolling canvas
        self.scroll_canvas = tk.Canvas(
            self.setup_container,
            highlightthickness=0,
            bd=0
        )

        self.scroll_canvas.pack(
            side="left",
            fill="both",
            expand=True
        )

        # Thin scrollbar
        self.scrollbar_canvas = tk.Canvas(
            self.setup_container,
            width=8,
            highlightthickness=0,
            bd=0
        )

        self.scrollbar_canvas.pack(
            side="right",
            fill="y",
            padx=(0, 4)
        )

        self.scroll_frame = tk.Frame(
            self.scroll_canvas
        )

        self.canvas_window = (
            self.scroll_canvas.create_window(
                0,
                0,
                window=self.scroll_frame,
                anchor="nw"
            )
        )

        self.scroll_frame.bind(
            "<Configure>",
            self._update_scroll_region
        )

        self.scroll_canvas.bind(
            "<Configure>",
            self._resize_scroll_frame
        )

        self.scroll_canvas.bind(
            "<Enter>",
            self._bind_mousewheel
        )

        self.scroll_canvas.bind(
            "<Leave>",
            self._unbind_mousewheel
        )

        self.scrollbar_canvas.bind(
            "<Button-1>",
            self._scrollbar_click
        )

        self.scrollbar_canvas.bind(
            "<B1-Motion>",
            self._scrollbar_drag
        )

        self._build_setup_contents()


    # ========================================================
    # CUSTOM SCROLLBAR
    # ========================================================

    def _draw_scrollbar(self):

        if not self.scrollbar_canvas:
            return

        height = (
            self.scrollbar_canvas.winfo_height()
        )

        if height <= 1:
            return

        self.scrollbar_canvas.delete(
            "all"
        )

        bbox = self.scroll_canvas.bbox(
            "all"
        )

        if not bbox:
            return

        content_height = (
            bbox[3] - bbox[1]
        )

        visible_height = (
            self.scroll_canvas.winfo_height()
        )

        # No scrollbar needed
        if content_height <= visible_height:
            self.scroll_thumb = None
            return

        thumb_height = max(
            35,
            int(
                height
                * visible_height
                / content_height
            )
        )

        first, last = (
            self.scroll_canvas.yview()
        )

        available = (
            height - thumb_height
        )

        thumb_y = (
            first * available
        )

        self.scroll_thumb = (
            self.scrollbar_canvas.create_rectangle(
                1,
                thumb_y,
                7,
                thumb_y + thumb_height,
                fill=self.accent_color_var.get(),
                outline=""
            )
        )


    def _scrollbar_click(self, event):

        if self.scroll_thumb is None:
            return

        coords = (
            self.scrollbar_canvas.coords(
                self.scroll_thumb
            )
        )

        if not coords:
            return

        y1 = coords[1]
        y2 = coords[3]

        if y1 <= event.y <= y2:

            self.scroll_drag_start = (
                event.y - y1
            )

            return

        height = (
            self.scrollbar_canvas.winfo_height()
        )

        thumb_height = (
            y2 - y1
        )

        available = (
            height - thumb_height
        )

        if available <= 0:
            return

        ratio = (
            event.y
            - thumb_height / 2
        ) / available

        ratio = max(
            0,
            min(1, ratio)
        )

        self.scroll_canvas.yview_moveto(
            ratio
        )

        self._draw_scrollbar()


    def _scrollbar_drag(self, event):

        if not hasattr(
            self,
            "scroll_drag_start"
        ):
            return

        if self.scroll_thumb is None:
            return

        height = (
            self.scrollbar_canvas.winfo_height()
        )

        coords = (
            self.scrollbar_canvas.coords(
                self.scroll_thumb
            )
        )

        if not coords:
            return

        thumb_height = (
            coords[3] - coords[1]
        )

        available = (
            height - thumb_height
        )

        if available <= 0:
            return

        new_y = (
            event.y
            - self.scroll_drag_start
        )

        ratio = (
            new_y / available
        )

        ratio = max(
            0,
            min(1, ratio)
        )

        self.scroll_canvas.yview_moveto(
            ratio
        )

        self._draw_scrollbar()


    def _update_scroll_region(
        self,
        event=None
    ):

        self.scroll_canvas.configure(
            scrollregion=(
                self.scroll_canvas.bbox(
                    "all"
                )
            )
        )

        self.root.after_idle(
            self._draw_scrollbar
        )


    def _resize_scroll_frame(
        self,
        event
    ):

        self.scroll_canvas.itemconfigure(
            self.canvas_window,
            width=event.width
        )

        self.root.after_idle(
            self._draw_scrollbar
        )


    def _bind_mousewheel(
        self,
        event=None
    ):

        self.root.bind_all(
            "<MouseWheel>",
            self._mousewheel
        )


    def _unbind_mousewheel(
        self,
        event=None
    ):

        self.root.unbind_all(
            "<MouseWheel>"
        )


    def _mousewheel(self, event):

        self.scroll_canvas.yview_scroll(
            int(-event.delta / 120),
            "units"
        )

        self._draw_scrollbar()


    # ========================================================
    # SETUP CONTENT
    # ========================================================

    def _build_setup_contents(self):

        self.main = tk.Frame(
            self.scroll_frame,
            bd=2,
            relief="ridge"
        )

        self.main.pack(
            fill="both",
            expand=True,
            padx=8,
            pady=8
        )

        # ----------------------------------------------------
        # TITLE
        # ----------------------------------------------------

        self.title_label = tk.Label(
            self.main,
            textvariable=self.title_text_var,
            font=(
                "Segoe UI",
                18,
                "bold"
            )
        )

        self.title_label.pack(
            pady=(12, 8)
        )

        # ----------------------------------------------------
        # IMAGE PREVIEW
        # ----------------------------------------------------

        self.image_frame = tk.Frame(
            self.main,
            width=390,
            height=190,
            relief="groove",
            bd=2
        )

        self.image_frame.pack_propagate(
            False
        )

        self.image_label = tk.Label(
            self.image_frame,
            text="No image selected yet",
            font=(
                "Segoe UI",
                10,
                "italic"
            )
        )

        self.image_label.place(
            relx=0.5,
            rely=0.5,
            anchor="center"
        )

        self.image_frame.pack(
            padx=10,
            pady=6
        )

        # ----------------------------------------------------
        # COOL TEXT
        # ----------------------------------------------------

        self.cool_text_label = tk.Label(
            self.main,
            textvariable=self.cool_text_var,
            font=(
                "Segoe UI",
                10,
                "bold"
            ),
            wraplength=380
        )

        self.cool_text_label.pack(
            pady=(2, 10)
        )

        # ----------------------------------------------------
        # CUSTOMIZATION
        # ----------------------------------------------------

        self.settings_frame = tk.LabelFrame(
            self.main,
            text="Customization"
        )

        self.settings_frame.pack(
            fill="x",
            padx=10,
            pady=6
        )

        self._add_labeled_entry(
            self.settings_frame,
            "Overlay title",
            self.title_text_var
        )

        self._add_labeled_entry(
            self.settings_frame,
            "Cool text",
            self.cool_text_var
        )

        self._add_labeled_entry(
            self.settings_frame,
            "Calories / key",
            self.cal_per_press_var
        )

        self._add_labeled_entry(
            self.settings_frame,
            "Tracked keys",
            self.keys_var
        )

        # ----------------------------------------------------
        # IMAGE FILE
        # ----------------------------------------------------

        row = tk.Frame(
            self.settings_frame
        )

        row.pack(
            fill="x",
            padx=6,
            pady=4
        )

        tk.Label(
            row,
            text="Image file",
            width=18,
            anchor="w"
        ).pack(
            side="left"
        )

        tk.Label(
            row,
            textvariable=self.image_display_var,
            wraplength=220
        ).pack(
            side="left",
            fill="x",
            expand=True
        )

        image_buttons = tk.Frame(
            self.settings_frame
        )

        image_buttons.pack(
            fill="x",
            padx=6,
            pady=5
        )

        tk.Button(
            image_buttons,
            text="Browse Image",
            command=self.browse_image
        ).pack(
            side="left"
        )

        tk.Button(
            image_buttons,
            text="Load Image",
            command=self.load_image
        ).pack(
            side="left",
            padx=6
        )

        tk.Button(
            image_buttons,
            text="Remove Image",
            command=self.remove_image
        ).pack(
            side="left"
        )

        # ----------------------------------------------------
        # COLORS
        # ----------------------------------------------------

        color_frame = tk.Frame(
            self.settings_frame
        )

        color_frame.pack(
            fill="x",
            padx=6,
            pady=5
        )

        self._add_color_input(
            color_frame,
            "BG",
            self.bg_color_var,
            0
        )

        self._add_color_input(
            color_frame,
            "Panel",
            self.panel_color_var,
            1
        )

        self._add_color_input(
            color_frame,
            "Accent",
            self.accent_color_var,
            2
        )

        self._add_color_input(
            color_frame,
            "Text",
            self.text_color_var,
            3
        )

        # ----------------------------------------------------
        # OPTIONS
        # ----------------------------------------------------

        options = tk.Frame(
            self.settings_frame
        )

        options.pack(
            fill="x",
            padx=6,
            pady=5
        )

        tk.Checkbutton(
            options,
            text="Always on top",
            variable=self.always_on_top_var,
            command=self.apply_window_flags
        ).pack(
            side="left"
        )

        tk.Checkbutton(
            options,
            text="Borderless",
            variable=self.borderless_var,
            command=self.apply_window_flags
        ).pack(
            side="left",
            padx=10
        )

        # ----------------------------------------------------
        # OPACITY
        # ----------------------------------------------------

        alpha_frame = tk.Frame(
            self.settings_frame
        )

        alpha_frame.pack(
            fill="x",
            padx=6,
            pady=5
        )

        tk.Label(
            alpha_frame,
            text="Window opacity"
        ).pack(
            side="left"
        )

        tk.Scale(
            alpha_frame,
            from_=0.35,
            to=1.0,
            resolution=0.05,
            orient="horizontal",
            variable=self.overlay_alpha_var,
            command=lambda _: (
                self.apply_window_flags()
            ),
            length=190
        ).pack(
            side="left",
            padx=8
        )

        # ----------------------------------------------------
        # APPLY
        # ----------------------------------------------------

        tk.Button(
            self.settings_frame,
            text="Apply Customization",
            command=self.apply_customization
        ).pack(
            pady=8
        )

        # ----------------------------------------------------
        # SESSION STATS
        # ----------------------------------------------------

        self.stats_frame = tk.LabelFrame(
            self.main,
            text="Session Stats"
        )

        self.stats_frame.pack(
            fill="x",
            padx=10,
            pady=6
        )

        self.keys_stat_label = (
            self._make_stat_row(
                self.stats_frame,
                "Tracked keys"
            )
        )

        self.total_label = (
            self._make_stat_row(
                self.stats_frame,
                "Total presses"
            )
        )

        self.time_label = (
            self._make_stat_row(
                self.stats_frame,
                "Session time"
            )
        )

        self.rate_label = (
            self._make_stat_row(
                self.stats_frame,
                "Presses / min"
            )
        )

        # ----------------------------------------------------
        # CALORIES
        # ----------------------------------------------------

        cal_frame = tk.Frame(
            self.main
        )

        cal_frame.pack(
            pady=10
        )

        tk.Label(
            cal_frame,
            text="Calories burned",
            font=(
                "Segoe UI",
                11
            )
        ).pack()

        self.calories_var = tk.StringVar(
            value="0.00"
        )

        tk.Label(
            cal_frame,
            textvariable=self.calories_var,
            font=(
                "Segoe UI",
                32,
                "bold"
            )
        ).pack()

        # ----------------------------------------------------
        # BUTTONS
        # ----------------------------------------------------

        buttons = tk.Frame(
            self.main
        )

        buttons.pack(
            pady=10
        )

        tk.Button(
            buttons,
            text="Start",
            command=self.start
        ).grid(
            row=0,
            column=0,
            padx=4
        )

        tk.Button(
            buttons,
            text="Pause",
            command=self.stop
        ).grid(
            row=0,
            column=1,
            padx=4
        )

        tk.Button(
            buttons,
            text="Reset",
            command=self.reset
        ).grid(
            row=0,
            column=2,
            padx=4
        )

        tk.Button(
            buttons,
            text="SHOW OVERLAY",
            command=self.enter_overlay_mode
        ).grid(
            row=0,
            column=3,
            padx=8
        )

        # ----------------------------------------------------
        # STATUS
        # ----------------------------------------------------

        self.status_var = tk.StringVar(
            value="Stopped"
        )

        self.status_label = tk.Label(
            self.main,
            textvariable=self.status_var,
            font=(
                "Segoe UI",
                10,
                "italic"
            )
        )

        self.status_label.pack(
            pady=(2, 12)
        )


    # ========================================================
    # UI HELPERS
    # ========================================================

    def _add_labeled_entry(
        self,
        parent,
        label,
        variable
    ):

        row = tk.Frame(parent)

        row.pack(
            fill="x",
            padx=6,
            pady=4
        )

        tk.Label(
            row,
            text=label,
            width=18,
            anchor="w"
        ).pack(
            side="left"
        )

        tk.Entry(
            row,
            textvariable=variable
        ).pack(
            side="left",
            fill="x",
            expand=True
        )


    def _add_color_input(
        self,
        parent,
        label,
        variable,
        column
    ):

        frame = tk.Frame(parent)

        frame.grid(
            row=0,
            column=column,
            padx=3
        )

        tk.Label(
            frame,
            text=label
        ).pack()

        tk.Entry(
            frame,
            textvariable=variable,
            width=8
        ).pack()


    def _make_stat_row(
        self,
        parent,
        name
    ):

        row = tk.Frame(parent)

        row.pack(
            fill="x",
            padx=8,
            pady=3
        )

        tk.Label(
            row,
            text=f"{name}:",
            width=16,
            anchor="w"
        ).pack(
            side="left"
        )

        var = tk.StringVar(
            value="0"
        )

        tk.Label(
            row,
            textvariable=var
        ).pack(
            side="right"
        )

        return var


    # ========================================================
    # IMAGE LOADING
    # ========================================================

    def browse_image(self):

        selected = filedialog.askopenfilename(
            title="Select image",
            filetypes=[
                (
                    "Image files",
                    "*.png *.jpg *.jpeg *.gif *.bmp *.webp"
                ),
                (
                    "All files",
                    "*.*"
                )
            ]
        )

        if selected:

            self.image_path_var.set(
                selected
            )

            self.image_display_var.set(
                os.path.basename(
                    selected
                )
            )


    def load_image(self):

        path = (
            self.image_path_var.get()
            .strip()
        )

        if not path:

            messagebox.showwarning(
                "No image",
                "Please select an image first."
            )

            return

        try:

            image = Image.open(path)

            # Fully decode the image
            image.load()

            # Convert to RGB so weird image modes,
            # transparency, palettes, etc. don't cause
            # unexpected rendering problems.
            image = image.convert("RGB")

            self.original_image = (
                image.copy()
            )

            # ------------------------------------------------
            # PREVIEW
            # ------------------------------------------------

            preview = image.copy()

            preview.thumbnail(
                (386, 186),
                Image.Resampling.LANCZOS
            )

            self.preview_image = (
                ImageTk.PhotoImage(
                    preview
                )
            )

            self.image_label.configure(
                image=self.preview_image,
                text=""
            )

            # ------------------------------------------------
            # OVERLAY IMAGE
            # ------------------------------------------------

            self._prepare_overlay_image(
                image
            )

            self.status_var.set(
                "Image loaded successfully."
            )

        except Exception as e:

            messagebox.showerror(
                "Image load failed",
                f"Couldn't load that image.\n\n{e}"
            )


    def _prepare_overlay_image(
        self,
        image
    ):

        original_width, original_height = (
            image.size
        )

        if (
            original_width <= 0
            or original_height <= 0
        ):

            raise ValueError(
                "The image has invalid dimensions."
            )

        # ----------------------------------------------------
        # SINGLE SCALE FACTOR
        #
        # This preserves aspect ratio.
        # ----------------------------------------------------

        scale = min(
            MAX_OVERLAY_WIDTH / original_width,
            MAX_OVERLAY_HEIGHT / original_height,
            1.0
        )

        new_width = max(
            1,
            round(
                original_width * scale
            )
        )

        new_height = max(
            1,
            round(
                original_height * scale
            )
        )

        # ----------------------------------------------------
        # ACTUAL OVERLAY IMAGE
        # ----------------------------------------------------

        resized = image.resize(
            (
                new_width,
                new_height
            ),
            Image.Resampling.LANCZOS
        )

        self.overlay_pil_image = (
            resized
        )

        self.overlay_width = (
            new_width
        )

        self.overlay_height = (
            new_height
        )

        # Keep PhotoImage referenced
        self.overlay_photo = (
            ImageTk.PhotoImage(
                resized
            )
        )


    def remove_image(self):

        self.image_path_var.set(
            ""
        )

        self.image_display_var.set(
            "None selected"
        )

        self.original_image = None
        self.preview_image = None
        self.overlay_pil_image = None
        self.overlay_photo = None

        self.image_label.configure(
            image="",
            text="No image selected yet"
        )


    # ========================================================
    # THEME
    # ========================================================

    def apply_theme(self):

        bg = self.bg_color_var.get()
        panel = self.panel_color_var.get()
        accent = self.accent_color_var.get()
        text = self.text_color_var.get()

        self.root.configure(
            bg=bg
        )

        self.scroll_canvas.configure(
            bg=bg
        )

        self.scrollbar_canvas.configure(
            bg=bg
        )

        self.scroll_frame.configure(
            bg=bg
        )

        self.main.configure(
            bg=panel
        )

        self.image_frame.configure(
            bg=panel
        )

        self.image_label.configure(
            bg=panel,
            fg=text
        )

        self.title_label.configure(
            bg=panel,
            fg=accent
        )

        self.cool_text_label.configure(
            bg=panel,
            fg=text
        )

        self.status_label.configure(
            bg=panel,
            fg=text
        )

        self._apply_widget_colors(
            self.main,
            panel,
            text,
            accent
        )

        self._draw_scrollbar()


    def _apply_widget_colors(
        self,
        widget,
        bg,
        text,
        accent
    ):

        for child in widget.winfo_children():

            if isinstance(
                child,
                tk.LabelFrame
            ):

                child.configure(
                    bg=bg,
                    fg=text
                )

            elif isinstance(
                child,
                tk.Frame
            ):

                child.configure(
                    bg=bg
                )

            elif isinstance(
                child,
                tk.Label
            ):

                child.configure(
                    bg=bg,
                    fg=text
                )

            elif isinstance(
                child,
                tk.Entry
            ):

                child.configure(
                    bg="#0d1020",
                    fg=text,
                    insertbackground=text
                )

            elif isinstance(
                child,
                tk.Button
            ):

                child.configure(
                    bg=accent,
                    fg="white",
                    activebackground="#ff7aad",
                    activeforeground="white"
                )

            elif isinstance(
                child,
                tk.Checkbutton
            ):

                child.configure(
                    bg=bg,
                    fg=text,
                    selectcolor="#0d1020",
                    activebackground=bg,
                    activeforeground=text
                )

            elif isinstance(
                child,
                tk.Scale
            ):

                child.configure(
                    bg=bg,
                    fg=text,
                    troughcolor="#0d1020",
                    highlightbackground=bg
                )

            self._apply_widget_colors(
                child,
                bg,
                text,
                accent
            )


    # ========================================================
    # WINDOW FLAGS
    # ========================================================

    def apply_window_flags(self):

        self.root.attributes(
            "-topmost",
            self.always_on_top_var.get()
        )

        if not self.overlay_mode:

            self.root.overrideredirect(
                self.borderless_var.get()
            )

            self.root.attributes(
                "-alpha",
                self.overlay_alpha_var.get()
            )


    # ========================================================
    # CUSTOMIZATION
    # ========================================================

    def apply_customization(self):

        keys = [
            key.strip()
            for key in (
                self.keys_var.get().split(",")
            )
            if key.strip()
        ]

        state.set_tracked_keys(
            keys
        )

        self.apply_theme()

        if self.image_path_var.get().strip():

            self.load_image()

        self.status_var.set(
            "Customization applied."
        )


    # ========================================================
    # TRACKING CONTROLS
    # ========================================================

    def start(self):

        state.start()

        self.status_var.set(
            "Tracking..."
        )


    def stop(self):

        state.stop()

        self.status_var.set(
            "Paused"
        )


    def reset(self):

        was_running = (
            state.snapshot()["running"]
        )

        state.reset()

        if was_running:

            state.start()

            self.status_var.set(
                "Tracking..."
            )

        else:

            self.status_var.set(
                "Stopped"
            )


    def _get_cal_per_press(self):

        try:

            value = float(
                self.cal_per_press_var.get()
            )

            return max(
                0,
                value
            )

        except ValueError:

            return DEFAULT_CALORIES_PER_PRESS


    # ========================================================
    # ENTER OVERLAY
    # ========================================================

    def enter_overlay_mode(self):

        # Apply latest settings
        self.apply_customization()

        if self.overlay_pil_image is None:

            messagebox.showwarning(
                "No image",
                "Load an image before opening the overlay."
            )

            return

        # ----------------------------------------------------
        # Recreate PhotoImage
        # ----------------------------------------------------

        self.overlay_photo = (
            ImageTk.PhotoImage(
                self.overlay_pil_image
            )
        )

        self.overlay_width = (
            self.overlay_pil_image.width
        )

        self.overlay_height = (
            self.overlay_pil_image.height
        )

        if self.overlay_width <= 0:
            return

        if self.overlay_height <= 0:
            return

        self.overlay_mode = True

        self.stats_hidden = False

        # ----------------------------------------------------
        # REMOVE NORMAL UI
        # ----------------------------------------------------

        self.setup_container.pack_forget()

        # ----------------------------------------------------
        # BORDERLESS OVERLAY
        # ----------------------------------------------------

        self.root.overrideredirect(
            True
        )

        self.root.attributes(
            "-topmost",
            self.always_on_top_var.get()
        )

        self.root.attributes(
            "-alpha",
            self.overlay_alpha_var.get()
        )

        # ====================================================
        # IMPORTANT FIX
        #
        # The normal setup UI has a minimum of 360x500.
        #
        # We REMOVE that minimum before creating the overlay.
        #
        # Otherwise Tkinter can keep the window huge and
        # create the exact black/transparent region you were
        # seeing.
        # ====================================================

        self.root.minsize(
            1,
            1
        )

        # ====================================================
        # WINDOW = EXACT IMAGE SIZE
        # ====================================================

        self.root.geometry(
            f"{self.overlay_width}x"
            f"{self.overlay_height}"
        )

        self.root.update_idletasks()

        # ----------------------------------------------------
        # CENTER WINDOW
        # ----------------------------------------------------

        screen_width = (
            self.root.winfo_screenwidth()
        )

        screen_height = (
            self.root.winfo_screenheight()
        )

        x = (
            screen_width
            - self.overlay_width
        ) // 2

        y = (
            screen_height
            - self.overlay_height
        ) // 2

        self.root.geometry(
            f"{self.overlay_width}x"
            f"{self.overlay_height}"
            f"+{x}+{y}"
        )

        self._build_overlay()


    # ========================================================
    # BUILD OVERLAY
    # ========================================================

    def _build_overlay(self):

        if self.overlay_canvas is not None:

            self.overlay_canvas.destroy()

        # ----------------------------------------------------
        # EXACT IMAGE SIZE
        # ----------------------------------------------------

        width = (
            self.overlay_pil_image.width
        )

        height = (
            self.overlay_pil_image.height
        )

        self.overlay_width = width
        self.overlay_height = height

        # ====================================================
        # FORCE WINDOW TO IMAGE SIZE AGAIN
        # ====================================================

        self.root.geometry(
            f"{width}x{height}"
        )

        self.root.update_idletasks()

        # ====================================================
        # CANVAS = EXACT IMAGE SIZE
        # ====================================================

        self.overlay_canvas = tk.Canvas(
            self.root,

            width=width,
            height=height,

            highlightthickness=0,
            bd=0,
            relief="flat",

            # The background should never be visible because
            # the image covers the ENTIRE canvas.
            bg="black"
        )

        self.overlay_canvas.pack(
            fill=None,
            expand=False
        )

        # ====================================================
        # IMAGE
        # ====================================================

        self.overlay_canvas.create_image(
            0,
            0,
            image=self.overlay_photo,
            anchor="nw"
        )

        # ====================================================
        # COMPACT STATS CARD
        # ====================================================

        card_width = min(
            405,
            max(
                250,
                width - 30
            )
        )

        card_height = 118

        card_x = 20

        # Put the card near the bottom of the IMAGE,
        # not the bottom of some oversized window.
        card_y = (
            height
            - card_height
            - 20
        )

        if card_y < 10:

            card_y = 10

        card_x2 = (
            card_x + card_width
        )

        card_y2 = (
            card_y + card_height
        )

        # ====================================================
        # STATS BACKGROUND
        # ====================================================

        self.overlay_canvas.create_rectangle(
            card_x,
            card_y,
            card_x2,
            card_y2,

            fill="#10121d",

            outline="#d8d8df",
            width=1
        )

        # ====================================================
        # NOW TRACKING
        # ====================================================

        self.overlay_canvas.create_text(
            card_x + 13,
            card_y + 11,

            text="NOW TRACKING",

            anchor="nw",

            fill="#ffffff",

            font=(
                "Segoe UI",
                9,
                "bold"
            )
        )

        # ====================================================
        # KEYS
        # ====================================================

        self.overlay_canvas.create_text(
            card_x + 13,
            card_y + 36,

            text="KEYS",

            anchor="nw",

            fill="#8b8e9b",

            font=(
                "Segoe UI",
                8,
                "bold"
            )
        )

        self.overlay_keys_text = (
            self.overlay_canvas.create_text(
                card_x + 13,
                card_y + 53,

                text="",

                anchor="nw",

                fill="#ffffff",

                font=(
                    "Segoe UI",
                    11,
                    "bold"
                )
            )
        )

        # ====================================================
        # PRESSES
        # ====================================================

        presses_x = (
            card_x + 175
        )

        self.overlay_canvas.create_text(
            presses_x,
            card_y + 36,

            text="PRESSES",

            anchor="nw",

            fill="#8b8e9b",

            font=(
                "Segoe UI",
                8,
                "bold"
            )
        )

        self.overlay_total_text = (
            self.overlay_canvas.create_text(
                presses_x,
                card_y + 53,

                text="0",

                anchor="nw",

                fill="#ffffff",

                font=(
                    "Segoe UI",
                    13,
                    "bold"
                )
            )
        )

        # ====================================================
        # CALORIES
        # ====================================================

        self.overlay_canvas.create_text(
            card_x + 13,
            card_y + 83,

            text="CALORIES",

            anchor="nw",

            fill="#8b8e9b",

            font=(
                "Segoe UI",
                8,
                "bold"
            )
        )

        self.overlay_calories_text = (
            self.overlay_canvas.create_text(
                card_x + 77,
                card_y + 81,

                text="0.00",

                anchor="nw",

                fill=self.accent_color_var.get(),

                font=(
                    "Segoe UI",
                    12,
                    "bold"
                )
            )
        )

        # ====================================================
        # TIME
        # ====================================================

        time_x = (
            card_x + 175
        )

        self.overlay_canvas.create_text(
            time_x,
            card_y + 83,

            text="TIME",

            anchor="nw",

            fill="#8b8e9b",

            font=(
                "Segoe UI",
                8,
                "bold"
            )
        )

        self.overlay_time_text = (
            self.overlay_canvas.create_text(
                time_x + 40,
                card_y + 81,

                text="00:00",

                anchor="nw",

                fill="#ffffff",

                font=(
                    "Segoe UI",
                    12,
                    "bold"
                )
            )
        )

        # ====================================================
        # DRAGGING
        # ====================================================

        self.overlay_canvas.bind(
            "<ButtonPress-1>",
            self._overlay_drag_start
        )

        self.overlay_canvas.bind(
            "<B1-Motion>",
            self._overlay_drag
        )


    # ========================================================
    # DRAG OVERLAY
    # ========================================================

    def _overlay_drag_start(
        self,
        event
    ):

        self.overlay_drag_x = event.x
        self.overlay_drag_y = event.y


    def _overlay_drag(
        self,
        event
    ):

        x = (
            self.root.winfo_x()
            + event.x
            - self.overlay_drag_x
        )

        y = (
            self.root.winfo_y()
            + event.y
            - self.overlay_drag_y
        )

        self.root.geometry(
            f"{self.overlay_width}x"
            f"{self.overlay_height}"
            f"+{x}+{y}"
        )


    # ========================================================
    # HIDE / SHOW STATS
    # ========================================================

    def _toggle_overlay_stats(
        self,
        event=None
    ):

        if not self.overlay_mode:
            return

        if self.overlay_canvas is None:
            return

        self.stats_hidden = (
            not self.stats_hidden
        )

        items = (
            self.overlay_canvas.find_all()
        )

        # First item is the image.
        # Everything else is the stats.
        for item in items[1:]:

            self.overlay_canvas.itemconfigure(
                item,
                state=(
                    "hidden"
                    if self.stats_hidden
                    else "normal"
                )
            )


    # ========================================================
    # ESCAPE
    # ========================================================

    def _on_escape_key(
        self,
        event=None
    ):

        if self.overlay_mode:

            self.exit_overlay_mode()


    # ========================================================
    # EXIT OVERLAY
    # ========================================================

    def exit_overlay_mode(self):

        if not self.overlay_mode:
            return

        self.overlay_mode = False

        # ----------------------------------------------------
        # Destroy overlay canvas
        # ----------------------------------------------------

        if self.overlay_canvas is not None:

            self.overlay_canvas.destroy()

            self.overlay_canvas = None

        # ----------------------------------------------------
        # Restore normal window
        # ----------------------------------------------------

        self.root.overrideredirect(
            self.borderless_var.get()
        )

        self.root.attributes(
            "-alpha",
            1.0
        )

        # ====================================================
        # RESTORE SETUP MINIMUM
        # ====================================================

        self.root.minsize(
            360,
            500
        )

        self.root.geometry(
            f"{SETUP_WIDTH}x"
            f"{SETUP_HEIGHT}"
        )

        # ----------------------------------------------------
        # Show setup UI
        # ----------------------------------------------------

        self.setup_container.pack(
            fill="both",
            expand=True
        )

        self.status_var.set(
            "Tracking..."
        )

        self.root.after_idle(
            self._draw_scrollbar
        )


    # ========================================================
    # UPDATE OVERLAY STATS
    # ========================================================

    def _update_overlay_stats(
        self,
        snap
    ):

        if not self.overlay_mode:
            return

        if self.overlay_canvas is None:
            return

        if not hasattr(
            self,
            "overlay_keys_text"
        ):
            return

        # ----------------------------------------------------
        # KEYS
        # ----------------------------------------------------

        keys_text = "  ".join(
            f"{key.upper()} {count}"
            for key, count
            in snap["counts"].items()
        )

        self.overlay_canvas.itemconfigure(
            self.overlay_keys_text,
            text=(
                keys_text
                if keys_text
                else "NONE"
            )
        )

        # ----------------------------------------------------
        # PRESSES
        # ----------------------------------------------------

        self.overlay_canvas.itemconfigure(
            self.overlay_total_text,
            text=str(
                snap["total"]
            )
        )

        # ----------------------------------------------------
        # TIME
        # ----------------------------------------------------

        minutes, seconds = divmod(
            int(
                snap["elapsed"]
            ),
            60
        )

        self.overlay_canvas.itemconfigure(
            self.overlay_time_text,
            text=(
                f"{minutes:02d}:"
                f"{seconds:02d}"
            )
        )

        # ----------------------------------------------------
        # CALORIES
        # ----------------------------------------------------

        calories = (
            snap["total"]
            * self._get_cal_per_press()
        )

        self.overlay_canvas.itemconfigure(
            self.overlay_calories_text,
            text=f"{calories:.2f}"
        )


    # ========================================================
    # MAIN UPDATE LOOP
    # ========================================================

    def _tick(self):

        snap = state.snapshot()

        # ----------------------------------------------------
        # SETUP STATS
        # ----------------------------------------------------

        keys_text = ", ".join(
            f"{key.upper()}={count}"
            for key, count
            in snap["counts"].items()
        )

        self.keys_stat_label.set(
            keys_text
            if keys_text
            else "None"
        )

        self.total_label.set(
            str(
                snap["total"]
            )
        )

        minutes, seconds = divmod(
            int(
                snap["elapsed"]
            ),
            60
        )

        self.time_label.set(
            f"{minutes:02d}:"
            f"{seconds:02d}"
        )

        if snap["elapsed"] > 0:

            rate = (
                snap["total"]
                / snap["elapsed"]
                * 60
            )

        else:

            rate = 0

        self.rate_label.set(
            f"{rate:.1f}"
        )

        calories = (
            snap["total"]
            * self._get_cal_per_press()
        )

        self.calories_var.set(
            f"{calories:.2f}"
        )

        # ----------------------------------------------------
        # OVERLAY STATS
        # ----------------------------------------------------

        self._update_overlay_stats(
            snap
        )

        # ----------------------------------------------------
        # NEXT UPDATE
        # ----------------------------------------------------

        self.root.after(
            UPDATE_INTERVAL_MS,
            self._tick
        )


# ============================================================
# START PROGRAM
# ============================================================

if __name__ == "__main__":

    root = tk.Tk()

    app = CalorieTrackerApp(
        root
    )

    root.mainloop()
