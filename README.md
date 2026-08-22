# Instrumentarium

Instrumentarium is a supreme VST plugin companion and manager for Linux and Wine.

It solves the inherent difficulties of installing, managing, and preserving Windows VST plugins under Linux by providing a unified PyQt6 graphical interface. It handles automated unattended installation for Windows installers (.exe, .msi) and seamless drag-and-drop support for compressed VST bundles (.zip, .rar, .7z, .tar.gz).

Furthermore, Instrumentarium introduces a groundbreaking "Snapshot Tracking" engine. Every time a plugin is installed, Instrumentarium performs a cryptographic diff of the Wine prefix to capture all hidden files, AppData presets, and Windows Registry keys created by the installer.

This allows users to perfectly package an entire plugin ecosystem into a cross-platform `.vstpack` archive. A `.vstpack` can be imported back into Instrumentarium on another Linux machine, or executed natively on Windows via an included batch script, effectively cloning the exact post-installation state without missing dependencies, licenses, or presets.

## Features

- **Batch Installer**: Drag and drop multiple installers or compressed bundles to install them sequentially.
- **Smart Silent Mode**: Automatically detects the installer engine (Inno Setup, Nullsoft NSIS, InstallShield) and passes the correct flags to execute installations silently in the background.
- **State Snapshot Tracking**: Automatically captures files and registry keys created during installation to map them directly to the installed VST.
- **Manual Capture Mode**: Enter a listening mode to capture post-installation manual modifications (such as applying a crack, replacing a `.dll`, or typing a serial license) and assign them to a plugin's receipt.
- **Clean Uninstaller**: Uses the captured snapshot data to purge not only the `.dll` but all hidden AppData and tracked registries to keep your prefix clean.
- **Cross-Platform Exporting**: Export an exact 1:1 clone of a plugin (including licenses and presets) into a `.vstpack`.

## Requirements

- Python 3
- PyQt6 (`python3 -m pip install PyQt6`)
- pefile (`python3 -m pip install pefile`)
- Wine (configured in `~/.wine-ableton` or specified via `WINEPREFIX`)
- Standard extraction utilities (`unzip`, `unrar`, `7z`, `tar`, `bsdtar`)

## Usage

Simply launch the GUI:

```bash
./instrumentarium
```

You can drag and drop `.exe`, `.msi`, `.dll`, `.vst3`, `.clap` or compressed archives directly into the interface.
