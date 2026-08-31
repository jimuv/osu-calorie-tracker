# osu-calorie-tracker

Fun osu! calorie tracker overlay based on your key presses.

## Features
- Global key capture using `pynput` (counts keys while osu! is focused)
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
2. Press **Start**, switch to osu!, and play.
3. Use **Customization** section to apply your own style and key mapping.
4. Toggle **Always on top** to keep the overlay visible over other windows.

## If keys are not counting
- Make sure you pressed **Start**.
- If you changed tracked keys, click **Apply customization** after editing them.
- Run osu! and this app with the same privilege level (for example, both normal or both admin).
