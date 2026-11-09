# Kiln wallpapers

Three 2560x1440 PNGs, drawn by `iso/wallpapers/make-wallpapers.py` with nothing
but the Python standard library — no downloads, no attribution to carry, and
nothing the medium needs a network for.

| File | Look |
| --- | --- |
| `kiln-001-aurora.png` | deep blue-black, cyan glow above, ember glow below, horizon line |
| `kiln-002-grid.png` | perspective floor running to a glowing horizon |
| `kiln-003-ember.png` | warm core ringed in cyan on near-black, fine dot grid |

They are copied to `/usr/share/backgrounds/kiln` in the new system by
`installer/xfce.py`, which also points Xfce and the login screen at the first
one. Regenerate them from the repository root with:

```bash
python3 iso/wallpapers/make-wallpapers.py
```

The images are the work of this project and released under CC0-1.0: use them
for anything.
