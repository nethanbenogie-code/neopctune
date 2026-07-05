# Building NEO TUNE to NeoTune.exe

PyInstaller does **not** cross-compile. A Windows `.exe` must be built **on a Windows PC**
(your ThinkCentre is perfect). These three files must sit in the **same folder**:

```
neo_tune_pro.py      <- the app
NeoTune.spec         <- the build recipe (hardened)
neotune_ico.ico      <- the icon (also bundled at runtime)
build_neotune.bat    <- one-click builder
```

## Easiest way (one click)

1. Put the four files above in one folder.
2. Double-click **`build_neotune.bat`**.
3. When it finishes, your app is **`dist\NeoTune.exe`**.

The batch file installs the dependencies, cleans old builds, and runs PyInstaller for you.

## Manual way

```bat
python -m pip install pyinstaller customtkinter psutil pystray pillow
pyinstaller --noconfirm --clean NeoTune.spec
```

Result: `dist\NeoTune.exe` — a single, self-contained file you can copy anywhere.

## What the spec already handles

- **Bundles CustomTkinter's theme/font assets** via `collect_data_files('customtkinter')`.
  This is the #1 cause of "it builds but crashes instantly" — the exe can't find its
  theme `.json`. Verified: 4 theme files + 3 fonts get collected.
- **Bundles the tray backend** (`pystray._win32`) via `collect_submodules('pystray')`.
- **Embeds the icon** (`neotune_ico.ico`) for the exe and the window/taskbar.
- **`uac_admin=True`** — the exe requests Administrator on launch, because the DNS changer,
  Defender toggles, firewall, SFC/CHKDSK fixes, prefetch cleanup, etc. need elevation.
  (If you'd rather it launch without the UAC prompt, set `uac_admin=False` in `NeoTune.spec`
  and re-build; the app will still run but admin-only fixes will be skipped with a warning.)

## Notes & gotchas

- **First launch is slow.** A one-file build unpacks to a temp folder on each start — normal.
  If you want faster startup, change to a one-folder build (ask and I'll give you that spec).
- **Antivirus false positives.** PyInstaller apps that touch the registry, `netsh`, and
  Defender commonly trip heuristic AV (especially with UPX). If that happens:
  - Set `upx=True` -> `upx=False` in the spec and rebuild, and/or
  - Add an exclusion, and ideally **code-sign** the exe for distribution to clients.
- **Build on the lowest Windows version you support.** An exe built on Win11 runs on Win11/10;
  building on Win10 maximizes compatibility.
- **Python 3.10–3.12** recommended. Very new 3.13 sometimes lags PyInstaller hook support.

## If a build error mentions a missing module

Add it to `hiddenimports` in `NeoTune.spec`, e.g.:

```python
hiddenimports = ['psutil', 'PIL', 'customtkinter', 'the_missing_module']
```

then rebuild.
