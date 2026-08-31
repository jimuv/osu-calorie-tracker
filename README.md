# osu-calorie-tracker

Fun rhythm-game calorie tracker overlay based on your key presses.

## Features
- Global key capture using `pynput` (counts keys while your game is focused)
- Overlay mode (`Always on top`) so it stays visible while you play
- Optional borderless mode for stream-like HUD feel
- Custom tracked keys (default `z, x`)
- Custom calories-per-key value
- Custom title text + cool text line
- Optional anime image panel (PNG/GIF/PPM/PGM)
- Theme customization (background, panel, accent, text)
- Adjustable overlay opacity

## Setup
```bash
pip install pynput
python calorie.py
```

## Usage
1. Launch the app.
2. Configure title, keys, colors, opacity, and image in the setup UI.
3. Click **Show Overlay** to switch into the final overlay view (background + key/calorie stats only).
4. Press **Esc** to return to the setup UI at any time.
5. Press **Start**, switch to your rhythm game, and play (overlay mode auto-starts if needed).
6. Toggle **Always on top** to keep the overlay visible over other windows.

## If keys are not counting
- Make sure you pressed **Start**.
- If you changed tracked keys, click **Apply customization** after editing them.
- Run your game and this app with the same privilege level (for example, both normal or both admin).
