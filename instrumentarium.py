#!/usr/bin/env python3
"""
Instrumentarium - VST & Plugin Supreme Companion & Manager (PyQt6 GUI)
Deep integration with KDE Plasma / Qt native styles, ~/.wine-ableton prefix,
and Cross-Platform VSTPack (.vstpack) Export/Import for Linux & Windows.

Features:
- Native KDE Plasma 6 & Breeze Integration: Adapts dynamically to system colors,
  dark/light themes, accent colors, and freedesktop system icons.
- Main Dashboard: Grouped product view with vendor/manufacturer detection,
  format badges (VST2, VST3, CLAP, AAX), version, size, and standalone launcher.
- Cross-Platform Stack Export / Migration Engine (.vstpack):
  Full backup of binaries, AppData/Roaming, Documents, Presets, and Windows Registry.
  Includes automated self-installers for both Windows (install_windows.bat) and Linux (install_linux.sh).
- Stack Import / Restore Engine: 1-click restore for new machines or clean formats.
- Batch Installer & Drop Zone: Drag & drop queue for .exe installers, .vst3, .dll,
  and compressed archives (.rar, .zip, .7z) with auto-unpacking and silent mode.
"""

import sys
import os
import glob
import json
import shutil
import subprocess
import tempfile
import time
import zipfile
import re
from pathlib import Path
from PyQt6 import QtCore, QtGui, QtWidgets

try:
    import pefile
    HAS_PEFILE = True
except ImportError:
    HAS_PEFILE = False


def get_wine_prefix() -> str:
    if os.environ.get("ABLETON_WINEPREFIX"):
        return os.path.abspath(os.path.expanduser(os.environ["ABLETON_WINEPREFIX"]))
    if os.environ.get("WINEPREFIX"):
        return os.path.abspath(os.path.expanduser(os.environ["WINEPREFIX"]))
    return os.path.expanduser("~/.wine-ableton")


def get_wine_root() -> str:
    if os.environ.get("ABLETON_WINE_ROOT") and os.path.isfile(os.path.join(os.environ["ABLETON_WINE_ROOT"], "bin", "wine")):
        return os.path.abspath(os.path.expanduser(os.environ["ABLETON_WINE_ROOT"]))
    
    opt_dir = os.path.expanduser("~/.local/opt")
    candidates = glob.glob(os.path.join(opt_dir, "wine-d2d1-nspa-*"))
    valid = []
    for c in candidates:
        if "rollback" in c or "failed" in c:
            continue
        if os.path.isfile(os.path.join(c, "bin", "wine")):
            valid.append(c)
    
    if valid:
        valid.sort(key=lambda p: [int(n) for n in re.findall(r'\d+', os.path.basename(p))])
        return valid[-1]
    
    sys_wine = shutil.which("wine")
    if sys_wine:
        return os.path.dirname(os.path.dirname(sys_wine))
    return ""


def human_readable_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def get_system_icon(name: str, fallback_emoji: str = "") -> QtGui.QIcon:
    if QtGui.QIcon.hasThemeIcon(name):
        return QtGui.QIcon.fromTheme(name)
    # Check local asset directory
    for path in [
        os.path.join(os.path.dirname(__file__), "assets", f"{name}.svg"),
        os.path.join(os.path.dirname(__file__), "assets", f"{name}.png"),
        os.path.expanduser(f"~/.local/share/icons/hicolor/scalable/apps/{name}.svg"),
        os.path.expanduser(f"~/.local/share/icons/hicolor/256x256/apps/{name}.png"),
    ]:
        if os.path.isfile(path):
            return QtGui.QIcon(path)
    return QtGui.QIcon()


def get_pe_metadata(file_path: str) -> dict:
    meta = {
        "vendor": "Desconocido",
        "product": os.path.splitext(os.path.basename(file_path))[0],
        "version": "-",
        "description": ""
    }
    
    binary_path = file_path
    if os.path.isdir(file_path):
        for candidate in [
            os.path.join(file_path, "Contents", "x86_64-win", os.path.basename(file_path)),
            os.path.join(file_path, "Contents", "x86_64-win", os.path.basename(file_path)[:-1] if file_path.endswith("/") else os.path.basename(file_path)),
        ]:
            if os.path.isfile(candidate):
                binary_path = candidate
                break
        else:
            found_bin = False
            for root, _, files in os.walk(file_path):
                for f in files:
                    if f.lower().endswith((".vst3", ".dll")):
                        binary_path = os.path.join(root, f)
                        found_bin = True
                        break
                if found_bin:
                    break

    modinfo = os.path.join(file_path, "Contents", "Resources", "moduleinfo.json") if os.path.isdir(file_path) else ""
    if modinfo and os.path.isfile(modinfo):
        try:
            with open(modinfo, "r", encoding="utf-8", errors="ignore") as f:
                data = json.load(f)
                if "Vendor" in data:
                    meta["vendor"] = data["Vendor"]
                if "Name" in data:
                    meta["product"] = data["Name"]
                if "Version" in data:
                    meta["version"] = data["Version"]
                return meta
        except Exception:
            pass

    if not HAS_PEFILE or not os.path.isfile(binary_path):
        return meta

    try:
        pe = pefile.PE(binary_path, fast_load=True)
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_RESOURCE']])
        if hasattr(pe, 'FileInfo'):
            for file_info in pe.FileInfo:
                for fi in file_info:
                    if hasattr(fi, 'StringTable'):
                        for st in fi.StringTable:
                            for k, v in st.entries.items():
                                key = k.decode('utf-8', errors='ignore').strip()
                                val = v.decode('utf-8', errors='ignore').strip()
                                if key == "CompanyName" and val:
                                    cleaned = re.split(r"[/,;]", val)[0].strip()
                                    meta["vendor"] = cleaned if cleaned else val
                                elif key == "ProductName" and val:
                                    meta["product"] = val
                                elif key == "FileVersion" and val:
                                    meta["version"] = val
                                elif key == "FileDescription" and val:
                                    meta["description"] = val
    except Exception:
        pass

    return meta


def extract_archive(archive_path: str, target_dir: str) -> bool:
    ext = os.path.splitext(archive_path)[1].lower()
    try:
        if ext in [".zip", ".vstpack"]:
            try:
                with zipfile.ZipFile(archive_path, 'r') as zf:
                    zf.extractall(target_dir)
                return True
            except Exception:
                pass
            if shutil.which("unzip"):
                subprocess.run(["unzip", "-q", "-o", archive_path, "-d", target_dir], check=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
        elif ext == ".rar":
            if shutil.which("unrar"):
                subprocess.run(["unrar", "x", "-p-", "-inul", "-y", archive_path, target_dir + "/"], check=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
            elif shutil.which("7z"):
                subprocess.run(["7z", "x", "-p-", "-y", f"-o{target_dir}", archive_path], check=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
            elif shutil.which("unar"):
                subprocess.run(["unar", "-p", "-", "-q", "-f", "-o", target_dir, archive_path], check=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
        elif ext in [".7z", ".tar", ".gz", ".xz", ".bz2", ".tgz"]:
            if shutil.which("bsdtar"):
                subprocess.run(["bsdtar", "-xf", archive_path, "-C", target_dir], check=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
            elif shutil.which("tar") and ext != ".7z":
                subprocess.run(["tar", "-xf", archive_path, "-C", target_dir], check=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
            elif shutil.which("7z"):
                subprocess.run(["7z", "x", "-p-", "-y", f"-o{target_dir}", archive_path], check=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
    except Exception:
        pass

    for tool in ["7z", "7za", "unar"]:
        if shutil.which(tool):
            try:
                if tool in ["7z", "7za"]:
                    subprocess.run([tool, "x", "-p-", "-y", f"-o{target_dir}", archive_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, check=True)
                else:
                    subprocess.run([tool, "-p", "-", "-q", "-f", "-o", target_dir, archive_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, check=True)
                return True
            except Exception:
                continue

    return False


def take_prefix_snapshot(prefix: str, wine_root: str = "") -> dict:
    """Takes a snapshot of the prefix filesystem and registry state."""
    if wine_root and os.path.isfile(os.path.join(wine_root, "bin", "wineserver")):
        try:
            env = os.environ.copy()
            env["WINEPREFIX"] = prefix
            subprocess.run([os.path.join(wine_root, "bin", "wineserver"), "-w"],
                           env=env, timeout=2, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    files = {}
    drive_c = os.path.join(prefix, "drive_c")
    if os.path.isdir(drive_c):
        for root, _, filenames in os.walk(drive_c):
            for f in filenames:
                fp = os.path.join(root, f)
                try:
                    files[fp] = os.path.getmtime(fp)
                except Exception:
                    pass

    reg_state = {}
    for reg_name in ["system.reg", "user.reg"]:
        reg_path = os.path.join(prefix, reg_name)
        if os.path.exists(reg_path):
            try:
                with open(reg_path, "r", encoding="utf-8", errors="ignore") as rf:
                    reg_state[reg_name] = set(rf.read().splitlines())
            except Exception:
                reg_state[reg_name] = set()

    return {"files": files, "reg": reg_state}


def compute_snapshot_diff(before: dict, prefix: str, wine_root: str = "") -> dict:
    """Computes exact new files and registry entries created during an installation."""
    if wine_root and os.path.isfile(os.path.join(wine_root, "bin", "wineserver")):
        try:
            env = os.environ.copy()
            env["WINEPREFIX"] = prefix
            subprocess.run([os.path.join(wine_root, "bin", "wineserver"), "-w"],
                           env=env, timeout=2, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    new_files = []
    drive_c = os.path.join(prefix, "drive_c")
    before_files = before.get("files", {})

    if os.path.isdir(drive_c):
        for root, _, filenames in os.walk(drive_c):
            for f in filenames:
                fp = os.path.join(root, f)
                if any(k in fp.lower() for k in ["/temp/", "/cache/", "throttle_store", ".log"]):
                    continue
                if fp not in before_files:
                    new_files.append(fp)
                else:
                    try:
                        if os.path.getmtime(fp) > before_files[fp]:
                            new_files.append(fp)
                    except Exception:
                        pass

    new_reg_lines = []
    before_reg = before.get("reg", {})

    for reg_name in ["user.reg", "system.reg"]:
        reg_path = os.path.join(prefix, reg_name)
        old_lines = before_reg.get(reg_name, set())
        if os.path.exists(reg_path):
            try:
                with open(reg_path, "r", encoding="utf-8", errors="ignore") as rf:
                    lines = rf.readlines()

                current_section = None
                section_has_new = False
                section_buffer = []

                for line in lines:
                    line_str = line.strip()
                    if line_str.startswith("[") and line_str.endswith("]"):
                        if current_section and section_has_new:
                            new_reg_lines.extend(section_buffer)
                        current_section = line_str
                        section_buffer = [line]
                        section_has_new = False
                    elif current_section:
                        section_buffer.append(line)
                        if line.rstrip('\r\n') not in old_lines and not line_str.startswith('"#time"='):
                            section_has_new = True

                if current_section and section_has_new:
                    new_reg_lines.extend(section_buffer)
            except Exception:
                pass

    return {
        "new_files": new_files,
        "new_reg_lines": new_reg_lines
    }


class ArchiveExtractorWorker(QtCore.QThread):
    extracted_items = QtCore.pyqtSignal(list)
    status_update = QtCore.pyqtSignal(str)
    temp_dir_created = QtCore.pyqtSignal(str)

    def __init__(self, archives):
        super().__init__()
        self.archives = archives

    def run(self):
        found = []
        for arch in self.archives:
            name = os.path.basename(arch)
            self.status_update.emit(f"Descomprimiendo: {name}...")
            temp_out = tempfile.mkdtemp(prefix="ableton_vst_extract_")
            self.temp_dir_created.emit(temp_out)
            success = extract_archive(arch, temp_out)
            if success:
                manifest = os.path.join(temp_out, "manifest.json")
                if os.path.isfile(manifest):
                    found.append(manifest)
                else:
                    for root, dirs, files in os.walk(temp_out):
                        for d in dirs:
                            if d.lower().endswith(".vst3"):
                                found.append(os.path.join(root, d))
                        dirs[:] = [d for d in dirs if not d.lower().endswith((".vst3", ".clap"))]
                        for f in files:
                            fl = f.lower()
                            if fl.endswith(".exe"):
                                found.append(os.path.join(root, f))
                            elif fl.endswith(".vst3"):
                                found.append(os.path.join(root, f))
                            elif fl.endswith(".dll") and not fl.startswith("api-ms") and not fl.startswith("vcruntime"):
                                found.append(os.path.join(root, f))
                            elif fl.endswith(".clap"):
                                found.append(os.path.join(root, f))
            else:
                self.status_update.emit(f"Error al descomprimir: {name}")
        self.extracted_items.emit(found)


class InstallWorker(QtCore.QThread):
    progress = QtCore.pyqtSignal(int, int, str)
    item_status = QtCore.pyqtSignal(int, str, str)
    log_message = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal()

    def __init__(self, items, wine_prefix, wine_root, silent_mode=False):
        super().__init__()
        self.items = items
        self.wine_prefix = wine_prefix
        self.wine_root = wine_root
        self.silent_mode = silent_mode
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        vst3_dest = os.path.join(self.get_drive_c(), "Program Files", "Common Files", "VST3")
        vst2_dest = os.path.join(self.get_drive_c(), "Program Files", "VSTPlugins")
        clap_dest = os.path.join(self.get_drive_c(), "Program Files", "Common Files", "CLAP")
        os.makedirs(vst3_dest, exist_ok=True)
        os.makedirs(vst2_dest, exist_ok=True)
        os.makedirs(clap_dest, exist_ok=True)

        wine_bin = os.path.join(self.wine_root, "bin", "wine")
        wineserver_bin = os.path.join(self.wine_root, "bin", "wineserver")

        env = os.environ.copy()
        for k in ["WINELOADER", "WINEDLLPATH", "WINEDLLOVERRIDES", "WINEARCH", "WINEESYNC", "WINEFSYNC"]:
            env.pop(k, None)
        env["WINEPREFIX"] = self.wine_prefix
        env["WINESERVER"] = wineserver_bin
        env["PATH"] = f"{os.path.join(self.wine_root, 'bin')}:{env.get('PATH', '')}"
        env["WINEDEBUG"] = "-all"

        total = len(self.items)
        for idx, item in enumerate(self.items):
            if self._is_cancelled:
                break
            
            row = item['row']
            file_path = item['path']
            filename = os.path.basename(file_path)
            self.progress.emit(idx + 1, total, filename)
            self.item_status.emit(row, "Instalando...", "#d97706")
            self.log_message.emit(f"\n--- [{idx+1}/{total}] Procesando: {filename} ---")

            try:
                # 0. VSTPack Manifest Restore
                if filename == "manifest.json" and os.path.isfile(file_path):
                    self.log_message.emit(f"Restaurando paquete de migración .vstpack...")
                    base_dir = os.path.dirname(file_path)
                    drive_src = os.path.join(base_dir, "drive_c")
                    if os.path.isdir(drive_src):
                        for root, _, files in os.walk(drive_src):
                            rel = os.path.relpath(root, drive_src)
                            # Match target user folder dynamically
                            rel_parts = rel.split(os.sep)
                            if len(rel_parts) >= 2 and rel_parts[0].lower() == "users":
                                # Replace source username with current username
                                rel_parts[1] = os.environ.get("USER", "kes")
                                rel = os.path.join(*rel_parts)

                            dest_folder = os.path.join(self.get_drive_c(), rel)
                            os.makedirs(dest_folder, exist_ok=True)
                            for f in files:
                                s = os.path.join(root, f)
                                d = os.path.join(dest_folder, f)
                                shutil.copy2(s, d)
                        self.log_message.emit("✓ Archivos del paquete restaurados en drive_c.")

                    reg_src = os.path.join(base_dir, "registry.reg")
                    if os.path.isfile(reg_src):
                        self.log_message.emit(f"Inyectando claves de registro ({reg_src})...")
                        subprocess.run([wine_bin, "regedit", reg_src], env=env)
                        self.log_message.emit("✓ Registro de Windows restaurado.")
                        
                    # Convert manifest.json to vst_tracker receipts to remember dependencies
                    try:
                        import json, time, re
                        with open(file_path, "r", encoding="utf-8") as mf:
                            manifest_data = json.load(mf)
                        
                        tracker_dir = os.path.join(self.wine_prefix, ".vst_tracker")
                        os.makedirs(tracker_dir, exist_ok=True)
                        
                        for prod in manifest_data.get("products", []):
                            stem = re.sub(r'[^a-zA-Z0-9_]', '_', prod.get("name", "Unknown"))
                            receipt_path = os.path.join(tracker_dir, f"{stem}_vstpack_restore_{int(time.time())}.json")
                            
                            abs_files = []
                            for f in prod.get("files", []):
                                rel_f = f
                                rel_parts = rel_f.split(os.sep)
                                if len(rel_parts) >= 2 and rel_parts[0].lower() == "users":
                                    rel_parts[1] = os.environ.get("USER", "kes")
                                    rel_f = os.path.join(*rel_parts)
                                abs_files.append(os.path.join(self.get_drive_c(), rel_f))
                                
                            receipt_data = {
                                "installer": "VSTPack Restore",
                                "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                "new_files": abs_files,
                                "reg_lines_count": 0,
                                "reg_diff": []
                            }
                            with open(receipt_path, "w", encoding="utf-8") as rpf:
                                json.dump(receipt_data, rpf, indent=2)
                    except Exception as e:
                        pass

                    self.item_status.emit(row, "✓ Stack Restaurado", "#16a34a")
                    continue

                # 1. VST3 File or Bundle
                if file_path.lower().endswith(".vst3"):
                    dest = os.path.join(vst3_dest, filename)
                    if os.path.exists(dest):
                        if os.path.isdir(dest):
                            shutil.rmtree(dest)
                        else:
                            os.remove(dest)
                    if os.path.isdir(file_path):
                        shutil.copytree(file_path, dest)
                    else:
                        shutil.copy2(file_path, dest)
                    self.item_status.emit(row, "✓ VST3 Instalado", "#16a34a")
                    self.log_message.emit(f"Copiado VST3 a: {dest}")

                # 2. VST2 DLL
                elif file_path.lower().endswith(".dll"):
                    dest = os.path.join(vst2_dest, filename)
                    if os.path.exists(dest):
                        if os.path.isdir(dest):
                            shutil.rmtree(dest)
                        else:
                            os.remove(dest)
                    shutil.copy2(file_path, dest)
                    self.item_status.emit(row, "✓ VST2 Instalado", "#16a34a")
                    self.log_message.emit(f"Copiado VST2 DLL a: {dest}")

                # 3. CLAP Plugin
                elif file_path.lower().endswith(".clap"):
                    dest = os.path.join(clap_dest, filename)
                    if os.path.exists(dest):
                        if os.path.isdir(dest):
                            shutil.rmtree(dest)
                        else:
                            os.remove(dest)
                    if os.path.isdir(file_path):
                        shutil.copytree(file_path, dest)
                    else:
                        shutil.copy2(file_path, dest)
                    self.item_status.emit(row, "✓ CLAP Instalado", "#16a34a")
                    self.log_message.emit(f"Copiado CLAP a: {dest}")

                # 4. Windows Executable Installer (.exe)
                elif file_path.lower().endswith(".exe"):
                    args = [wine_bin, file_path]
                    self.log_message.emit(f"Tomando snapshot de estado del prefijo antes de instalar...")
                    snap_before = take_prefix_snapshot(self.wine_prefix, self.wine_root)

                    if self.silent_mode:
                        self.log_message.emit(f"Modo silencioso activado. Analizando firmas del instalador...")
                        installer_type = "unknown"
                        try:
                            with open(file_path, "rb") as ef:
                                head = ef.read(1024 * 512)
                                if b"Inno Setup" in head:
                                    installer_type = "inno"
                                elif b"Nullsoft" in head or b"NSIS" in head:
                                    installer_type = "nsis"
                                elif b"InstallShield" in head:
                                    installer_type = "installshield"
                        except Exception:
                            pass
                        
                        silent_args = []
                        if installer_type == "inno":
                            silent_args = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-"]
                        elif installer_type == "nsis":
                            silent_args = ["/S"]
                        elif installer_type == "installshield":
                            silent_args = ["/s", "/v\"/qn\""]
                        else:
                            # Generic fallback for unknown
                            silent_args = ["/S", "/q"]

                        self.log_message.emit(f"Firma detectada: {installer_type.upper()}. Flags: {' '.join(silent_args)}")
                        cmd_silent = [file_path] + silent_args if self.is_windows else [wine_bin, file_path] + silent_args
                        proc = subprocess.run(cmd_silent, env=env, cwd=os.path.dirname(file_path))
                        
                        if proc.returncode != 0:
                            self.log_message.emit(f"Instalación silenciosa con fallos (código {proc.returncode}). Ejecutando modo interactivo de respaldo...")
                            cmd_inter = [file_path] if self.is_windows else args
                            proc = subprocess.run(cmd_inter, env=env, cwd=os.path.dirname(file_path))
                    else:
                        self.log_message.emit(f"Lanzando instalador: {file_path}")
                        cmd_inter = [file_path] if self.is_windows else args
                        proc = subprocess.run(cmd_inter, env=env, cwd=os.path.dirname(file_path))

                    # Compute Diff & Save Tracked Receipt
                    diff = compute_snapshot_diff(snap_before, self.wine_prefix, self.wine_root)
                    tracker_dir = os.path.join(self.wine_prefix, ".vst_tracker")
                    os.makedirs(tracker_dir, exist_ok=True)
                    
                    stem = re.sub(r'[^a-zA-Z0-9_]', '_', os.path.splitext(filename)[0])
                    receipt_path = os.path.join(tracker_dir, f"{stem}.json")
                    receipt_data = {
                        "installer": filename,
                        "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "new_files": diff["new_files"],
                        "reg_lines_count": len(diff["new_reg_lines"]),
                        "reg_diff": diff["new_reg_lines"]
                    }
                    with open(receipt_path, "w", encoding="utf-8") as rpf:
                        json.dump(receipt_data, rpf, indent=2)

                    num_files = len(diff["new_files"])
                    num_regs = len(diff["new_reg_lines"])
                    self.log_message.emit(f"✓ [Tracking] Se registraron {num_files} archivo(s) nuevos y {num_regs} entrada(s) de registro asociadas a este instalador.")

                    if proc.returncode == 0:
                        self.item_status.emit(row, "✓ Completado", "#16a34a")
                        self.log_message.emit(f"Instalador {filename} finalizado con éxito.")
                    else:
                        self.item_status.emit(row, f"Código: {proc.returncode}", "#f59e0b")
                        self.log_message.emit(f"Instalador {filename} salió con código {proc.returncode}.")

                else:
                    self.item_status.emit(row, "Omitido", "#64748b")
                    self.log_message.emit(f"Formato no compatible omitido: {filename}")

            except Exception as e:
                self.item_status.emit(row, f"Error: {str(e)[:25]}", "#dc2626")
                self.log_message.emit(f"Error al procesar {filename}: {e}")

        self.finished.emit()


class ExportWorker(QtCore.QThread):
    progress = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal(bool, str)

    def __init__(self, target_archive, products, wine_prefix, wine_root):
        super().__init__()
        self.target_archive = target_archive
        self.products = products
        self.wine_prefix = wine_prefix
        self.wine_root = wine_root

    def run(self):
        try:
            temp_dir = tempfile.mkdtemp(prefix="vstpack_build_")
            drive_dest = os.path.join(temp_dir, "drive_c")
            os.makedirs(drive_dest, exist_ok=True)

            manifest = {
                "format_version": "2.0",
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "source_os": "Linux (Wine)",
                "compatibility": ["Linux (Instrumentarium)", "Windows 10/11 (Native)"],
                "source_prefix": self.wine_prefix,
                "products": []
            }

            keys_to_export = set()
            tracker_dir = os.path.join(self.wine_prefix, ".vst_tracker")
            receipts = []
            if os.path.isdir(tracker_dir):
                for rf in os.listdir(tracker_dir):
                    if rf.endswith(".json"):
                        try:
                            with open(os.path.join(tracker_dir, rf), "r", encoding="utf-8") as f:
                                receipts.append(json.load(f))
                        except Exception:
                            pass

            for p in self.products:
                self.progress.emit(f"Exportando: {p['name']}...")
                p_entry = {
                    "name": p["name"],
                    "vendor": p["vendor"],
                    "formats": p["formats"],
                    "version": p["version"],
                    "files": []
                }
                
                # Match tracker receipts
                p_files_set = set(p["files"])
                for r in receipts:
                    r_files = r.get("new_files", [])
                    if any(f in p_files_set for f in r_files):
                        p["files"].extend([f for f in r_files if f not in p_files_set])
                        for line in r.get("reg_diff", []):
                            line = line.strip()
                            if line.startswith("[") and line.endswith("]"):
                                path = line[1:-1]
                                parts = path.split("\\")
                                if len(parts) >= 2 and parts[0] == "Software":
                                    vendor = parts[1]
                                    if vendor.lower() not in ["classes", "microsoft", "wine", "wow6432node"]:
                                        keys_to_export.add(f"Software\\{vendor}")
                                    else:
                                        keys_to_export.add(path)

                if p["vendor"] and p["vendor"] != "Desconocido":
                    keys_to_export.add(f"Software\\{p['vendor']}")

                for fpath in p["files"]:
                    if os.path.exists(fpath):
                        rel = os.path.relpath(fpath, self.get_drive_c())
                        # Skip if it's outside drive_c (e.g. symlinks leading outside)
                        if rel.startswith(".."):
                            continue
                        dest_file = os.path.join(drive_dest, rel)
                        os.makedirs(os.path.dirname(dest_file), exist_ok=True)
                        if os.path.isdir(fpath):
                            if os.path.exists(dest_file):
                                shutil.rmtree(dest_file)
                            shutil.copytree(fpath, dest_file)
                        else:
                            shutil.copy2(fpath, dest_file)
                        if rel not in p_entry["files"]:
                            p_entry["files"].append(rel)

                if p["vendor"] and p["vendor"] != "Desconocido":
                    for sub in [
                        os.path.join("users", os.environ.get("USER", "kes"), "AppData", "Roaming", p["vendor"]),
                        os.path.join("users", os.environ.get("USER", "kes"), "AppData", "Local", p["vendor"]),
                        os.path.join("users", os.environ.get("USER", "kes"), "Documents", p["vendor"]),
                        os.path.join("ProgramData", p["vendor"]),
                    ]:
                        src_sub = os.path.join(self.get_drive_c(), sub)
                        if os.path.exists(src_sub):
                            dest_sub = os.path.join(drive_dest, sub)
                            os.makedirs(os.path.dirname(dest_sub), exist_ok=True)
                            if os.path.isdir(src_sub):
                                if not os.path.exists(dest_sub):
                                    shutil.copytree(src_sub, dest_sub)
                            else:
                                shutil.copy2(src_sub, dest_sub)

                manifest["products"].append(p_entry)

            # Export Registry for collected keys
            reg_file = os.path.join(temp_dir, "registry.reg")
            with open(reg_file, "w", encoding="utf-8") as rf:
                rf.write("Windows Registry Editor Version 5.00\n\n")

            wine_bin = os.path.join(self.wine_root, "bin", "wine")
            env = os.environ.copy()
            env["WINEPREFIX"] = self.wine_prefix
            env["WINEDEBUG"] = "-all"

            for base_key in keys_to_export:
                self.progress.emit(f"Exportando registro: {base_key}...")
                k_clean = re.sub(r'[^a-zA-Z0-9_]', '_', base_key)
                for hive in ["HKEY_CURRENT_USER", "HKEY_LOCAL_MACHINE"]:
                    full_key = f"{hive}\\{base_key}"
                    sub_reg_name = f"reg_{k_clean}_{hive}.reg"
                    try:
                        subprocess.run(
                            [wine_bin, "regedit", "/E", sub_reg_name, full_key],
                            env=env,
                            cwd=temp_dir,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=3
                        )
                    except Exception:
                        pass

                    sub_reg = os.path.join(temp_dir, sub_reg_name)
                    if os.path.isfile(sub_reg) and os.path.getsize(sub_reg) > 50:
                        try:
                            with open(sub_reg, "r", encoding="utf-16") as srf:
                                lines = srf.readlines()
                        except UnicodeError:
                            with open(sub_reg, "r", encoding="utf-8", errors="ignore") as srf:
                                lines = srf.readlines()

                        content = "".join([l for l in lines if not l.strip().startswith("Windows Registry Editor")])
                        with open(reg_file, "a", encoding="utf-8") as rf:
                            rf.write(f"\n; --- {full_key} ---\n" + content + "\n")

            # Write manifest.json
            with open(os.path.join(temp_dir, "manifest.json"), "w", encoding="utf-8") as mf:
                json.dump(manifest, mf, indent=2)

            # ----------------------------------------------------
            # CROSS-PLATFORM: Generate Windows & Linux Installers
            # ----------------------------------------------------
            # 1. Windows batch installer (install_windows.bat)
            bat_content = """@echo off
title Instrumentarium VSTPack - Windows Installer
echo ========================================================
echo   Instrumentarium VSTPack - Instalador de Plugins para Windows
echo ========================================================
echo.

:: 1. Copiar VST3
if exist "drive_c\\Program Files\\Common Files\\VST3" (
    echo [1/6] Copiando plugins VST3...
    if not exist "%CommonProgramFiles%\\VST3" mkdir "%CommonProgramFiles%\\VST3"
    xcopy /E /I /Y "drive_c\\Program Files\\Common Files\\VST3\\*" "%CommonProgramFiles%\\VST3\\" >nul
    echo   [OK] VST3 instalados en %CommonProgramFiles%\\VST3
)

:: 2. Copiar VST2 (x64)
if exist "drive_c\\Program Files\\VSTPlugins" (
    echo [2/6] Copiando plugins VST2 (x64)...
    if not exist "%ProgramFiles%\\VSTPlugins" mkdir "%ProgramFiles%\\VSTPlugins"
    xcopy /E /I /Y "drive_c\\Program Files\\VSTPlugins\\*" "%ProgramFiles%\\VSTPlugins\\" >nul
    echo   [OK] VST2 instalados en %ProgramFiles%\\VSTPlugins
)

:: 3. Copiar CLAP
if exist "drive_c\\Program Files\\Common Files\\CLAP" (
    echo [3/6] Copiando plugins CLAP...
    if not exist "%CommonProgramFiles%\\CLAP" mkdir "%CommonProgramFiles%\\CLAP"
    xcopy /E /I /Y "drive_c\\Program Files\\Common Files\\CLAP\\*" "%CommonProgramFiles%\\CLAP\\" >nul
    echo   [OK] CLAP instalados en %CommonProgramFiles%\\CLAP
)

:: 4. Copiar ProgramData
if exist "drive_c\\ProgramData" (
    echo [4/6] Copiando datos compartidos a ProgramData...
    xcopy /E /I /Y "drive_c\\ProgramData\\*" "%ProgramData%\\" >nul
    echo   [OK] ProgramData actualizado.
)

:: 5. Copiar datos de usuario (AppData / Documents)
echo [5/6] Copiando presets y configuraciones de usuario...
for /D %%U in ("drive_c\\users\\*") do (
    if exist "%%U\\AppData\\Roaming" (
        xcopy /E /I /Y "%%U\\AppData\\Roaming\\*" "%APPDATA%\\" >nul
    )
    if exist "%%U\\AppData\\Local" (
        xcopy /E /I /Y "%%U\\AppData\\Local\\*" "%LOCALAPPDATA%\\" >nul
    )
    if exist "%%U\\Documents" (
        xcopy /E /I /Y "%%U\\Documents\\*" "%USERPROFILE%\\Documents\\" >nul
    )
)
echo   [OK] AppData y Documents actualizados.

:: 6. Inyectar Registro de Windows
if exist "registry.reg" (
    echo [6/6] Importando claves y licencias del Registro...
    regedit.exe /s "registry.reg"
    echo   [OK] Registro de Windows actualizado exitosamente.
)

echo.
echo ========================================================
echo   Instalacion completada con exito en Windows!
echo ========================================================
echo Abre Ableton Live, ve a Preferencias ^> Plug-ins y reescanea.
pause
"""
            with open(os.path.join(temp_dir, "install_windows.bat"), "w", encoding="latin-1", errors="ignore") as bf:
                bf.write(bat_content)

            # 2. Linux Shell installer (install_linux.sh)
            sh_content = """#!/usr/bin/env bash
# Instrumentarium VSTPack - Standalone Linux Restore Script
set -euo pipefail

PREFIX="${ABLETON_WINEPREFIX:-${WINEPREFIX:-$HOME/.wine-ableton}}"
here="$(cd "$(dirname "$0")" && pwd)"

echo "========================================================"
echo "  Instrumentarium VSTPack - Restaurador de Plugins para Linux"
echo "  Prefijo destino: $PREFIX"
echo "========================================================"

mkdir -p "$PREFIX/drive_c"

if [ -d "$here/drive_c" ]; then
    echo "[1/2] Copiando archivos del stack a $PREFIX/drive_c/..."
    cp -ru "$here/drive_c/." "$PREFIX/drive_c/"
    echo "  [OK] Archivos restaurados."
fi

if [ -f "$here/registry.reg" ]; then
    echo "[2/2] Inyectando claves del Registro en Wine..."
    WINE_ROOT="$(ls -d "$HOME/.local/opt"/wine-d2d1-nspa-* 2>/dev/null | grep -v 'rollback' | sort -V | tail -1 || true)"
    WINE_BIN="${WINE_ROOT:-/usr}/bin/wine"
    if [ -x "$WINE_BIN" ]; then
        WINEPREFIX="$PREFIX" WINEDEBUG=-all "$WINE_BIN" regedit "$here/registry.reg"
        echo "  [OK] Registro actualizado."
    else
        echo "  [!] No se pudo invocar wine automáticamente para inyectar registry.reg."
    fi
fi

echo "========================================================"
echo "  Restauracion finalizada con exito en Linux!"
echo "========================================================"
"""
            sh_path = os.path.join(temp_dir, "install_linux.sh")
            with open(sh_path, "w", encoding="utf-8") as sf:
                sf.write(sh_content)
            os.chmod(sh_path, 0o755)

            # 3. Readme instructions
            readme_content = """# Instrumentarium VSTPack - Paquete de Plugins Multiplataforma

Este paquete contiene plugins VST2, VST3, CLAP, presets, datos de usuario y registros de Windows exportados desde Instrumentarium.

## Cómo instalar en Windows:
1. Descomprime este archivo `.vstpack` (o cámbiale la extensión a `.zip`).
2. Haz doble clic en `install_windows.bat`.
3. Abre Ableton Live en Windows y ve a Preferencias > Plug-ins > Rescan.

## Cómo instalar en Linux:
1. Arrastra este archivo `.vstpack` directamente a la aplicación `instrumentarium`.
   O ejecuta en terminal: `./install_linux.sh`
2. Abre Ableton Live en Linux y mantén presionado Alt + Rescan.
"""
            with open(os.path.join(temp_dir, "README.txt"), "w", encoding="utf-8") as rmf:
                rmf.write(readme_content)

            self.progress.emit("Comprimiendo paquete multiplataforma .vstpack...")

            # Compress as standard ZIP container (100% native on Windows & Linux)
            # If target ends with .tar.zst or .tar.gz, use tar
            if self.target_archive.endswith((".tar.zst", ".tar.gz")):
                if self.target_archive.endswith(".zst") and shutil.which("zstd"):
                    subprocess.run(["tar", "-C", temp_dir, "-I", "zstd", "-cf", self.target_archive, "."], check=True)
                else:
                    subprocess.run(["tar", "-C", temp_dir, "-czf", self.target_archive, "."], check=True)
            else:
                # Default: Standard ZIP archive (fully readable by Windows Explorer & 7-Zip)
                zip_path = self.target_archive if self.target_archive.endswith((".zip", ".vstpack")) else f"{self.target_archive}.vstpack"
                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                    for root, _, files in os.walk(temp_dir):
                        for f in files:
                            full_f = os.path.join(root, f)
                            rel_f = os.path.relpath(full_f, temp_dir)
                            zf.write(full_f, rel_f)
                self.target_archive = zip_path

            self.finished.emit(True, self.target_archive)
        except Exception as e:
            self.finished.emit(False, str(e))
        finally:
            if 'temp_dir' in locals() and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)


class DropArea(QtWidgets.QFrame):
    files_dropped = QtCore.pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setObjectName("DropArea")

    def dragEnterEvent(self, event: QtGui.QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QtGui.QDropEvent):
        paths = []
        for url in event.mimeData().urls():
            p = url.toLocalFile()
            if p:
                paths.append(p)
        if paths:
            self.files_dropped.emit(paths)


class VSTInstallerApp(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Instrumentarium - Gestor de Plugins VST")
        self.setWindowIcon(get_system_icon("instrumentarium", "🎛️"))
        self.is_windows = (sys.platform == "win32")
        if self.is_windows:
            self.wine_prefix = os.environ.get("SystemDrive", "C:") + "\\"
            self.wine_root = ""
        else:
            self.wine_prefix = get_wine_prefix()
            self.wine_root = get_wine_root()

        self.queue_items = []
        self.temp_dirs = []
        self.installed_products = []
        self.worker = None
        self.extractor_workers = []
        self.export_worker = None

        self.setup_ui()
        self.apply_kde_native_theme()
        self.refresh_installed_ecosystem()

    def setup_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        main_layout = QtWidgets.QVBoxLayout(central)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(10)

        # Header: Info Bar
        info_group = QtWidgets.QFrame()
        info_group.setObjectName("HeaderFrame")
        info_layout = QtWidgets.QHBoxLayout(info_group)
        info_layout.setContentsMargins(14, 10, 14, 10)

        v_info = QtWidgets.QVBoxLayout()
        h_pref = QtWidgets.QHBoxLayout()
        lbl_prefix_title = QtWidgets.QLabel("<b>Prefijo Wine:</b>")
        self.lbl_prefix_val = QtWidgets.QLabel(self.wine_prefix)
        self.lbl_prefix_val.setObjectName("AccentLabel")
        h_pref.addWidget(lbl_prefix_title)
        h_pref.addWidget(self.lbl_prefix_val)
        h_pref.addStretch()

        h_wine = QtWidgets.QHBoxLayout()
        lbl_wine_title = QtWidgets.QLabel("<b>Runtime Wine:</b>")
        wine_display = os.path.basename(self.wine_root) if self.wine_root else "No detectado"
        self.lbl_wine_val = QtWidgets.QLabel(wine_display)
        self.lbl_wine_val.setObjectName("SecondaryAccentLabel")
        h_wine.addWidget(lbl_wine_title)
        h_wine.addWidget(self.lbl_wine_val)
        h_wine.addStretch()

        v_info.addLayout(h_pref)
        v_info.addLayout(h_wine)
        info_layout.addLayout(v_info)

        info_layout.addStretch()

        btn_help = QtWidgets.QPushButton("Detección en Ableton Live")
        btn_help.setIcon(get_system_icon("help-about", "❓"))
        btn_help.clicked.connect(self.show_live_help)
        info_layout.addWidget(btn_help)

        main_layout.addWidget(info_group)

        # Tabs
        self.tabs = QtWidgets.QTabWidget()
        self.tab_dashboard = QtWidgets.QWidget()
        self.tab_install = QtWidgets.QWidget()
        self.tab_backup = QtWidgets.QWidget()

        self.setup_dashboard_tab()
        self.setup_install_tab()
        self.setup_backup_tab()

        self.tabs.addTab(self.tab_dashboard, get_system_icon("audio-card", "🎛️"), "Plugins Instalados")
        self.tabs.addTab(self.tab_install, get_system_icon("system-software-install", "📥"), "Instalador por Lotes")
        self.tabs.addTab(self.tab_backup, get_system_icon("document-save", "💾"), "Migración & Respaldo")
        self.tabs.currentChanged.connect(self.on_tab_changed)

        main_layout.addWidget(self.tabs)

    # ----------------------------------------------------
    # TAB 1: DASHBOARD / INSTALLED PRODUCTS
    # ----------------------------------------------------
    def setup_dashboard_tab(self):
        layout = QtWidgets.QVBoxLayout(self.tab_dashboard)
        layout.setContentsMargins(6, 10, 6, 6)
        layout.setSpacing(8)

        # Prefix Chooser
        prefix_bar = QtWidgets.QHBoxLayout()
        prefix_lbl = QtWidgets.QLabel("Prefix/Entorno:")
        self.cmb_prefix = QtWidgets.QComboBox()
        self.cmb_prefix.setEditable(True)
        self.cmb_prefix.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
        
        if self.is_windows:
            self.cmb_prefix.addItem(self.wine_prefix)
            self.cmb_prefix.setEnabled(False)
        else:
            self.cmb_prefix.addItem(self.wine_prefix)
            try:
                home = os.path.expanduser("~")
                for d in os.listdir(home):
                    p = os.path.join(home, d)
                    if os.path.isdir(p) and d.startswith(".wine") and p != self.wine_prefix:
                        self.cmb_prefix.addItem(p)
            except:
                pass
            
        self.btn_browse_prefix = QtWidgets.QPushButton("Examinar...")
        self.btn_browse_prefix.clicked.connect(self.browse_prefix_action)
        if self.is_windows:
            self.btn_browse_prefix.setEnabled(False)
            
        self.cmb_prefix.currentTextChanged.connect(self.change_prefix_action)
            
        prefix_bar.addWidget(prefix_lbl)
        prefix_bar.addWidget(self.cmb_prefix)
        prefix_bar.addWidget(self.btn_browse_prefix)
        layout.addLayout(prefix_bar)

        # Filter & Top Controls
        top_bar = QtWidgets.QHBoxLayout()
        self.txt_filter = QtWidgets.QLineEdit()
        self.txt_filter.setPlaceholderText("Filtrar plugins por nombre o fabricante...")
        self.txt_filter.setClearButtonEnabled(True)
        self.txt_filter.textChanged.connect(self.filter_products_table)
        top_bar.addWidget(self.txt_filter)

        btn_open_vst3 = QtWidgets.QPushButton("Carpeta VST3")
        btn_open_vst3.setIcon(get_system_icon("folder-open", "📂"))
        btn_open_vst3.clicked.connect(lambda: self.open_folder_in_fm(os.path.join(self.get_drive_c(), "Program Files", "Common Files", "VST3")))
        
        btn_open_vst2 = QtWidgets.QPushButton("Carpeta VST2")
        btn_open_vst2.setIcon(get_system_icon("folder-open", "📂"))
        btn_open_vst2.clicked.connect(lambda: self.open_folder_in_fm(os.path.join(self.get_drive_c(), "Program Files", "VSTPlugins")))
        
        btn_refresh = QtWidgets.QPushButton("Actualizar")
        btn_refresh.setIcon(get_system_icon("view-refresh", "🔄"))
        btn_refresh.clicked.connect(self.refresh_installed_ecosystem)

        top_bar.addWidget(btn_open_vst3)
        top_bar.addWidget(btn_open_vst2)
        top_bar.addWidget(btn_refresh)
        layout.addLayout(top_bar)

        # Products Table
        self.prod_table = QtWidgets.QTableWidget()
        self.prod_table.setColumnCount(6)
        self.prod_table.setHorizontalHeaderLabels(["Producto / Plugin", "Fabricante", "Formatos", "Standalone", "Versión", "Tamaño Total"])
        self.prod_table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.prod_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.prod_table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.prod_table.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.prod_table.horizontalHeader().setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.prod_table.horizontalHeader().setSectionResizeMode(5, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.prod_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.prod_table.setAlternatingRowColors(True)
        layout.addWidget(self.prod_table)

        # Bottom Actions Bar
        bottom_bar = QtWidgets.QHBoxLayout()
        self.lbl_prod_count = QtWidgets.QLabel("0 plugins detectados.")

        self.btn_start_capture = QtWidgets.QPushButton("🔴 Capturar Cambios Manuales (Cracks/Licencias)")
        self.btn_start_capture.setToolTip("Inicia el rastreo para capturar licencias o parches manuales aplicados después de la instalación.")
        self.btn_start_capture.clicked.connect(self.start_manual_capture)

        self.btn_stop_capture = QtWidgets.QPushButton("⏹️ Finalizar y Asignar Captura")
        self.btn_stop_capture.setVisible(False)
        self.btn_stop_capture.clicked.connect(self.stop_manual_capture)

        btn_export_single = QtWidgets.QPushButton("Exportar Seleccionado (.vstpack)")
        btn_export_single.setIcon(get_system_icon("document-save", "📦"))
        btn_export_single.clicked.connect(self.export_selected_product)

        btn_delete_product = QtWidgets.QPushButton("Eliminar Producto")
        btn_delete_product.setIcon(get_system_icon("edit-delete", "🗑️"))
        btn_delete_product.clicked.connect(self.delete_selected_product)

        bottom_bar.addWidget(self.lbl_prod_count)
        bottom_bar.addSpacing(10)
        bottom_bar.addWidget(self.btn_start_capture)
        bottom_bar.addWidget(self.btn_stop_capture)
        bottom_bar.addStretch()
        bottom_bar.addWidget(btn_export_single)
        bottom_bar.addWidget(btn_delete_product)
        layout.addLayout(bottom_bar)

    # ----------------------------------------------------
    # TAB 2: BATCH INSTALLER & DROP ZONE
    # ----------------------------------------------------
    def setup_install_tab(self):
        layout = QtWidgets.QVBoxLayout(self.tab_install)
        layout.setContentsMargins(6, 10, 6, 6)
        layout.setSpacing(8)

        # Drop Zone
        self.drop_frame = DropArea()
        drop_layout = QtWidgets.QVBoxLayout(self.drop_frame)
        drop_lbl = QtWidgets.QLabel("Arrastra instaladores (.exe), paquetes (.vstpack), bundles (.vst3), DLLs (.dll) o archivos (.rar, .zip, .7z) aquí")
        drop_lbl.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        drop_layout.addWidget(drop_lbl)
        self.drop_frame.files_dropped.connect(self.add_files)
        layout.addWidget(self.drop_frame)

        # Action Toolbar
        bar_layout = QtWidgets.QHBoxLayout()
        self.btn_add_files = QtWidgets.QPushButton("Agregar Archivos...")
        self.btn_add_files.setIcon(get_system_icon("list-add", "➕"))
        self.btn_add_files.clicked.connect(self.select_files)
        
        self.btn_add_folder = QtWidgets.QPushButton("Agregar Carpeta...")
        self.btn_add_folder.setIcon(get_system_icon("folder-open", "📁"))
        self.btn_add_folder.clicked.connect(self.select_folder)
        
        self.btn_clear = QtWidgets.QPushButton("Limpiar Lista")
        self.btn_clear.setIcon(get_system_icon("edit-clear", "🗑️"))
        self.btn_clear.clicked.connect(self.clear_list)

        bar_layout.addWidget(self.btn_add_files)
        bar_layout.addWidget(self.btn_add_folder)
        bar_layout.addWidget(self.btn_clear)
        bar_layout.addStretch()

        self.chk_silent = QtWidgets.QCheckBox("Modo Rápido / Silencioso (/SILENT /S)")
        self.chk_silent.setToolTip("Intenta ejecutar instaladores sin pedir confirmación continua")
        bar_layout.addWidget(self.chk_silent)

        layout.addLayout(bar_layout)

        # Queue Table
        self.table = QtWidgets.QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["Nombre", "Tipo", "Ruta de Origen", "Estado"])
        self.table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table)

        # Log Console
        self.log_text = QtWidgets.QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(85)
        layout.addWidget(self.log_text)

        # Progress bar
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        # Bottom Bar
        bottom_layout = QtWidgets.QHBoxLayout()
        self.lbl_status = QtWidgets.QLabel("Listo. Agrega plugins o instaladores a la cola.")

        self.btn_start = QtWidgets.QPushButton("Instalar Todo en Ableton")
        self.btn_start.setIcon(get_system_icon("system-software-install", "🚀"))
        self.btn_start.setFixedHeight(36)
        self.btn_start.setObjectName("PrimaryActionButton")
        self.btn_start.clicked.connect(self.start_installation)

        bottom_layout.addWidget(self.lbl_status)
        bottom_layout.addStretch()
        bottom_layout.addWidget(self.btn_start)
        layout.addLayout(bottom_layout)

    # ----------------------------------------------------
    # TAB 3: MIGRATION & BACKUP CENTER
    # ----------------------------------------------------
    def setup_backup_tab(self):
        layout = QtWidgets.QVBoxLayout(self.tab_backup)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(14)

        intro_frame = QtWidgets.QFrame()
        intro_frame.setObjectName("IntroFrame")
        intro_v = QtWidgets.QVBoxLayout(intro_frame)
        
        t_lbl = QtWidgets.QLabel("<b>Centro de Migración y Respaldo Multiplataforma (.vstpack)</b>")
        t_lbl.setObjectName("AccentHeading")
        d_lbl = QtWidgets.QLabel(
            "Los paquetes <code>.vstpack</code> son 100% compatibles tanto con <b>Linux (Wine)</b> como con <b>Windows nativo</b>.<br>"
            "Incluyen instaladores automáticos (<code>install_windows.bat</code> y <code>install_linux.sh</code>), restaurando plugins, presets y claves de registro en cualquier PC."
        )
        d_lbl.setWordWrap(True)
        
        intro_v.addWidget(t_lbl)
        intro_v.addWidget(d_lbl)
        layout.addWidget(intro_frame)

        cards_h = QtWidgets.QHBoxLayout()
        cards_h.setSpacing(14)

        # Card 1: Full Export
        card_export = QtWidgets.QFrame()
        card_export.setObjectName("CardFrame")
        exp_v = QtWidgets.QVBoxLayout(card_export)
        
        exp_title = QtWidgets.QLabel("<b>1. Crear Respaldo Completo</b>")
        exp_desc = QtWidgets.QLabel("Exporta todos los plugins instalados, librerías, configuraciones y registros en un archivo <code>.vstpack</code> compatible con Windows y Linux.")
        exp_desc.setWordWrap(True)
        
        btn_full_export = QtWidgets.QPushButton("Exportar Todo el Stack (.vstpack)")
        btn_full_export.setIcon(get_system_icon("document-save", "📦"))
        btn_full_export.setFixedHeight(36)
        btn_full_export.clicked.connect(self.export_all_products)

        exp_v.addWidget(exp_title)
        exp_v.addWidget(exp_desc)
        exp_v.addStretch()
        exp_v.addWidget(btn_full_export)
        cards_h.addWidget(card_export)

        # Card 2: Restore
        card_import = QtWidgets.QFrame()
        card_import.setObjectName("CardFrame")
        imp_v = QtWidgets.QVBoxLayout(card_import)

        imp_title = QtWidgets.QLabel("<b>2. Restaurar Respaldo</b>")
        imp_desc = QtWidgets.QLabel("Carga un archivo <code>.vstpack</code> generado en Linux o Windows. Restaurará automáticamente los archivos y fusionará el registro.")
        imp_desc.setWordWrap(True)

        btn_restore = QtWidgets.QPushButton("Restaurar Paquete .vstpack")
        btn_restore.setIcon(get_system_icon("archive-extract", "📥"))
        btn_restore.setFixedHeight(36)
        btn_restore.clicked.connect(self.import_backup_package)

        imp_v.addWidget(imp_title)
        imp_v.addWidget(imp_desc)
        imp_v.addStretch()
        imp_v.addWidget(btn_restore)
        cards_h.addWidget(card_import)

        layout.addLayout(cards_h)

        self.export_status_lbl = QtWidgets.QLabel("")
        self.export_status_lbl.setObjectName("AccentLabel")
        layout.addWidget(self.export_status_lbl)
        layout.addStretch()

    # ----------------------------------------------------
    # SCANNING & PRODUCT AGGREGATION
    # ----------------------------------------------------

    def start_manual_capture(self):
        QtWidgets.QMessageBox.information(self, "Captura Manual", "Se tomará una instantánea del estado actual del sistema (Registro y Archivos).\n\nLuego abre tu plugin, introduce tu licencia o aplica tu crack, y al terminar presiona 'Finalizar y Asignar Captura'.")
        self.lbl_prod_count.setText("Tomando snapshot base... Por favor espera.")
        QtWidgets.QApplication.processEvents()
        
        self.manual_snapshot = take_prefix_snapshot(self.wine_prefix, self.wine_root)
        
        self.btn_start_capture.setVisible(False)
        self.btn_stop_capture.setVisible(True)
        self.lbl_prod_count.setText("🔴 ESCUCHANDO... Aplica tus cambios (Cracks/Licencias) y presiona Finalizar.")
        self.lbl_prod_count.setStyleSheet("color: #ef4444; font-weight: bold;")

    def stop_manual_capture(self):
        self.lbl_prod_count.setText("Analizando diferencias... Por favor espera.")
        self.lbl_prod_count.setStyleSheet("")
        QtWidgets.QApplication.processEvents()

        diff = compute_snapshot_diff(self.manual_snapshot, self.wine_prefix, self.wine_root)
        self.manual_snapshot = None
        self.btn_start_capture.setVisible(True)
        self.btn_stop_capture.setVisible(False)

        num_files = len(diff["new_files"])
        num_regs = len(diff["new_reg_lines"])

        if num_files == 0 and num_regs == 0:
            QtWidgets.QMessageBox.information(self, "Sin Cambios", "No se detectaron nuevos archivos ni entradas en el registro.")
            self.refresh_installed_ecosystem()
            return

        products_names = [p["name"] for p in self.installed_products]
        if not products_names:
            QtWidgets.QMessageBox.warning(self, "Error", "No hay plugins detectados a los cuales asignar estos cambios.")
            self.refresh_installed_ecosystem()
            return

        item, ok = QtWidgets.QInputDialog.getItem(
            self, 
            "Asignar Captura", 
            f"Se atraparon {num_files} archivos modificados y {num_regs} líneas de registro.\n\nSelecciona a qué plugin pertenecen para adjuntarlos:", 
            products_names, 
            0, 
            False
        )

        if ok and item:
            tracker_dir = os.path.join(self.wine_prefix, ".vst_tracker")
            os.makedirs(tracker_dir, exist_ok=True)
            stem = re.sub(r'[^a-zA-Z0-9_]', '_', item)
            import time
            receipt_path = os.path.join(tracker_dir, f"{stem}_manual_patch_{int(time.time())}.json")
            receipt_data = {
                "installer": "Manual Patch/Crack/License",
                "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "new_files": diff["new_files"],
                "reg_lines_count": num_regs,
                "reg_diff": diff["new_reg_lines"]
            }
            import json
            with open(receipt_path, "w", encoding="utf-8") as rpf:
                json.dump(receipt_data, rpf, indent=2)
            
            QtWidgets.QMessageBox.information(self, "¡Atrapados!", f"Los cambios manuales fueron inyectados en el ADN de '{item}'.\n\nAl exportar a .vstpack, tu parche/licencia irá incluido automáticamente.")
            
        self.refresh_installed_ecosystem()

    def browse_prefix_action(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Seleccionar Carpeta del Prefix de Wine", os.path.expanduser("~"))
        if d:
            if self.cmb_prefix.findText(d) == -1:
                self.cmb_prefix.addItem(d)
            self.cmb_prefix.setCurrentText(d)

    def change_prefix_action(self, text):
        if text and os.path.isdir(text):
            self.wine_prefix = text
            os.environ["WINEPREFIX"] = text
            self.refresh_installed_ecosystem()
            

    def get_drive_c(self):
        if hasattr(self, "is_windows") and self.is_windows:
            return os.environ.get("SystemDrive", "C:") + "\\"
        return os.path.join(self.wine_prefix, "drive_c")

    def refresh_installed_ecosystem(self):
        vst3_dir = os.path.join(self.get_drive_c(), "Program Files", "Common Files", "VST3")
        vst2_dir = os.path.join(self.get_drive_c(), "Program Files", "VSTPlugins")
        clap_dir = os.path.join(self.get_drive_c(), "Program Files", "Common Files", "CLAP")

        standalone_map = {}
        prog_files = os.path.join(self.get_drive_c(), "Program Files")
        
        if os.path.exists(prog_files):
            for root, _, files in os.walk(prog_files):
                for f in files:
                    if f.lower().endswith(".exe") and not any(k in f.lower() for k in ["unins", "setup", "install", "update", "helper", "crash"]):
                        standalone_map[f.lower().replace(".exe", "")] = os.path.join(root, f)

        raw_items = []
        if os.path.exists(vst3_dir):
            for it in os.listdir(vst3_dir):
                full = os.path.join(vst3_dir, it)
                if it.lower().endswith(".vst3") or os.path.isdir(full):
                    raw_items.append({"path": full, "fmt": "VST3", "file": it})

        if os.path.exists(vst2_dir):
            for it in os.listdir(vst2_dir):
                full = os.path.join(vst2_dir, it)
                if it.lower().endswith(".dll"):
                    raw_items.append({"path": full, "fmt": "VST2", "file": it})

        if os.path.exists(clap_dir):
            for it in os.listdir(clap_dir):
                full = os.path.join(clap_dir, it)
                if it.lower().endswith(".clap") or os.path.isdir(full):
                    raw_items.append({"path": full, "fmt": "CLAP", "file": it})

        products = {}
        for item in raw_items:
            meta = get_pe_metadata(item["path"])
            base_name = meta["product"] if meta["product"] and meta["product"] != "-" else os.path.splitext(item["file"])[0]
            
            group_key = re.sub(r"(_x64|_x86|2|FX|\.vst3|\.dll)$", "", base_name, flags=re.IGNORECASE).strip()
            if not group_key:
                group_key = base_name

            if group_key not in products:
                products[group_key] = {
                    "name": base_name if "2" in base_name else group_key,
                    "vendor": meta["vendor"],
                    "version": meta["version"],
                    "formats": set(),
                    "files": [],
                    "size": 0,
                    "standalone": None
                }

            p = products[group_key]
            p["formats"].add(item["fmt"])
            p["files"].append(item["path"])
            if meta["vendor"] != "Desconocido" and p["vendor"] == "Desconocido":
                p["vendor"] = meta["vendor"]
            if meta["version"] != "-" and p["version"] == "-":
                p["version"] = meta["version"]

            try:
                if os.path.isfile(item["path"]):
                    p["size"] += os.path.getsize(item["path"])
                else:
                    p["size"] += sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fn in os.walk(item["path"]) for f in fn)
            except Exception:
                pass

            for k_exe, exe_path in standalone_map.items():
                if group_key.lower() in k_exe or k_exe in group_key.lower():
                    p["standalone"] = exe_path
                    break

        self.installed_products = []
        for k, v in sorted(products.items()):
            v["formats"] = sorted(list(v["formats"]))
            self.installed_products.append(v)

        self.render_products_table(self.installed_products)

    def render_products_table(self, product_list):
        self.prod_table.setRowCount(0)
        pal = self.palette()
        accent_color = pal.highlight().color()

        for p in product_list:
            row = self.prod_table.rowCount()
            self.prod_table.insertRow(row)

            name_item = QtWidgets.QTableWidgetItem(p["name"])
            name_item.setFont(QtGui.QFont(self.font().family(), 10, QtGui.QFont.Weight.Bold))

            vendor_item = QtWidgets.QTableWidgetItem(p["vendor"])
            vendor_item.setForeground(accent_color)

            fmt_str = "  ".join([f"[{f}]" for f in p["formats"]])
            fmt_item = QtWidgets.QTableWidgetItem(fmt_str)
            fmt_item.setFont(QtGui.QFont(self.font().family(), 9, QtGui.QFont.Weight.Bold))

            if p["standalone"]:
                standalone_widget = QtWidgets.QWidget()
                s_layout = QtWidgets.QHBoxLayout(standalone_widget)
                s_layout.setContentsMargins(2, 2, 2, 2)
                btn_launch = QtWidgets.QPushButton(f"▶ {os.path.basename(p['standalone'])}")
                btn_launch.setIcon(get_system_icon("media-playback-start", "▶"))
                btn_launch.clicked.connect(lambda _, exe=p["standalone"]: self.launch_standalone(exe))
                s_layout.addWidget(btn_launch)
                self.prod_table.setCellWidget(row, 3, standalone_widget)
            else:
                self.prod_table.setItem(row, 3, QtWidgets.QTableWidgetItem("-"))

            ver_item = QtWidgets.QTableWidgetItem(p["version"])
            size_item = QtWidgets.QTableWidgetItem(human_readable_size(p["size"]))

            self.prod_table.setItem(row, 0, name_item)
            self.prod_table.setItem(row, 1, vendor_item)
            self.prod_table.setItem(row, 2, fmt_item)
            self.prod_table.setItem(row, 4, ver_item)
            self.prod_table.setItem(row, 5, size_item)

        self.lbl_prod_count.setText(f"{len(product_list)} productos ({sum(len(p['files']) for p in product_list)} formatos/archivos) detectados.")

    def filter_products_table(self, query):
        query = query.lower().strip()
        if not query:
            self.render_products_table(self.installed_products)
            return

        filtered = []
        for p in self.installed_products:
            if query in p["name"].lower() or query in p["vendor"].lower() or any(query in f.lower() for f in p["formats"]):
                filtered.append(p)
        self.render_products_table(filtered)

    def launch_standalone(self, exe_path):
        wine_bin = os.path.join(self.wine_root, "bin", "wine")
        env = os.environ.copy()
        env["WINEPREFIX"] = self.wine_prefix
        env["WINEDEBUG"] = "-all"
        subprocess.Popen([wine_bin, exe_path], env=env, cwd=os.path.dirname(exe_path))

    def delete_selected_product(self):
        row = self.prod_table.currentRow()
        if row < 0 or row >= len(self.installed_products):
            QtWidgets.QMessageBox.information(self, "Seleccionar", "Selecciona un producto de la lista para eliminar.")
            return

        p = self.installed_products[row]
        files_str = "\n".join([f" • {f}" for f in p["files"]])
        confirm = QtWidgets.QMessageBox.question(
            self,
            "Confirmar Eliminación",
            f"¿Estás seguro de eliminar el producto '{p['name']}' ({p['vendor']})?\n\nSe eliminarán los siguientes archivos:\n{files_str}",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No
        )
        if confirm == QtWidgets.QMessageBox.StandardButton.Yes:
            files_to_delete = set(p["files"])
            tracker_dir = os.path.join(self.wine_prefix, ".vst_tracker")
            receipts_to_delete = []
            if os.path.isdir(tracker_dir):
                import json
                for rf in os.listdir(tracker_dir):
                    if rf.endswith(".json"):
                        rpath = os.path.join(tracker_dir, rf)
                        try:
                            with open(rpath, "r", encoding="utf-8") as rf_fd:
                                rdata = json.load(rf_fd)
                                r_files = rdata.get("new_files", [])
                                if any(f_ in files_to_delete for f_ in r_files):
                                    files_to_delete.update(r_files)
                                    receipts_to_delete.append(rpath)
                        except Exception:
                            pass

            for f_ in files_to_delete:
                try:
                    if os.path.isdir(f_):
                        shutil.rmtree(f_)
                    elif os.path.isfile(f_):
                        os.remove(f_)
                except Exception:
                    pass
                    
            for rpath in receipts_to_delete:
                try:
                    os.remove(rpath)
                except Exception:
                    pass

            self.refresh_installed_ecosystem()

    # ----------------------------------------------------
    # STACK EXPORT & MIGRATION (.vstpack)
    # ----------------------------------------------------
    def export_selected_product(self):
        row = self.prod_table.currentRow()
        if row < 0 or row >= len(self.installed_products):
            QtWidgets.QMessageBox.information(self, "Seleccionar", "Selecciona un producto de la lista para exportar.")
            return
        p = self.installed_products[row]
        self._prompt_and_export([p], f"{p['name']}_stack.vstpack")

    def export_all_products(self):
        if not self.installed_products:
            QtWidgets.QMessageBox.information(self, "Sin plugins", "No hay plugins detectados para exportar.")
            return
        stamp = time.strftime("%Y%m%d", time.localtime())
        self._prompt_and_export(self.installed_products, f"ableton_vst_ecosystem_{stamp}.vstpack")

    def _prompt_and_export(self, products_to_export, default_filename):
        dest, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Guardar Paquete de Migración VST",
            os.path.join(os.path.expanduser("~"), default_filename),
            "Ableton VST Package (*.vstpack *.zip *.tar.zst)"
        )
        if not dest:
            return

        self.export_status_lbl.setText("Generando paquete de migración multiplataforma...")
        
        # Prevent concurrent exports
        for btn in self.tab_dashboard.findChildren(QtWidgets.QPushButton) + self.tab_backup.findChildren(QtWidgets.QPushButton):
            if "Exportar" in btn.text():
                btn.setEnabled(False)

        self.export_worker = ExportWorker(dest, products_to_export, self.wine_prefix, self.wine_root)
        self.export_worker.progress.connect(lambda msg: self.export_status_lbl.setText(msg))
        self.export_worker.finished.connect(self.on_export_finished)
        self.export_worker.start()

    def on_export_finished(self, success, result):
        for btn in self.tab_dashboard.findChildren(QtWidgets.QPushButton) + self.tab_backup.findChildren(QtWidgets.QPushButton):
            if "Exportar" in btn.text():
                btn.setEnabled(True)

        if success:
            self.export_status_lbl.setText(f"✓ Paquete exportado exitosamente en: {result}")
            QtWidgets.QMessageBox.information(
                self,
                "Exportación Exitosa",
                f"El paquete multiplataforma ha sido creado con éxito:\n\n{result}\n\n"
                "Características de compatibilidad:\n"
                " • En Linux: Arrastra el archivo a esta aplicación o ejecuta install_linux.sh.\n"
                " • En Windows: Descomprime el archivo y haz doble clic en 'install_windows.bat'.\n\n"
                "Incluye binarios VST2/VST3/CLAP, presets, datos de AppData y claves de registro de Windows."
            )
        else:
            self.export_status_lbl.setText(f"Error al exportar: {result}")
            QtWidgets.QMessageBox.critical(self, "Error de Exportación", f"No se pudo crear el paquete:\n{result}")

    def import_backup_package(self):
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "Seleccionar Paquete de Migración",
            os.path.expanduser("~"),
            "Paquetes de Migración (*.vstpack *.zip *.tar.zst);;Todos los archivos (*)"
        )
        if files:
            self.tabs.setCurrentIndex(1)
            self.add_files(files)

    # ----------------------------------------------------
    # BATCH INSTALLER ACTIONS
    # ----------------------------------------------------
    def select_files(self):
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "Seleccionar Plugins e Instaladores",
            os.path.expanduser("~"),
            "Plugins e Instaladores (*.exe *.vst3 *.dll *.clap *.vstpack *.zip *.rar *.7z);;Todos los archivos (*)"
        )
        if files:
            self.add_files(files)

    def select_folder(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Seleccionar Carpeta con Plugins",
            os.path.expanduser("~")
        )
        if folder:
            self.add_files([folder])

    def add_files(self, paths):
        archives_to_unpack = []
        for path in paths:
            if not os.path.exists(path):
                continue

            fl = path.lower()
            if os.path.isdir(path):
                if fl.endswith(".vst3"):
                    self._append_item(path, "VST3 Bundle")
                elif fl.endswith(".clap"):
                    self._append_item(path, "CLAP Plugin")
                else:
                    for root, dirs, files in os.walk(path):
                        for d in dirs:
                            if d.lower().endswith(".vst3"):
                                self._append_item(os.path.join(root, d), "VST3 Bundle")
                            elif d.lower().endswith(".clap"):
                                self._append_item(os.path.join(root, d), "CLAP Plugin")
                        dirs[:] = [d for d in dirs if not d.lower().endswith((".vst3", ".clap"))]
                        for f in files:
                            fl_sub = f.lower()
                            subpath = os.path.join(root, f)
                            if fl_sub.endswith(".exe"):
                                self._append_item(subpath, "Instalador EXE")
                            elif fl_sub.endswith(".vst3"):
                                self._append_item(subpath, "VST3 Plugin")
                            elif fl_sub.endswith(".dll") and not fl_sub.startswith("api-ms") and not fl_sub.startswith("vcruntime"):
                                self._append_item(subpath, "VST2 DLL")
                            elif fl_sub.endswith((".zip", ".rar", ".7z", ".vstpack")):
                                archives_to_unpack.append(subpath)
            else:
                if fl.endswith(".exe"):
                    self._append_item(path, "Instalador EXE")
                elif fl.endswith(".vst3"):
                    self._append_item(path, "VST3 Plugin")
                elif fl.endswith(".dll"):
                    self._append_item(path, "VST2 DLL")
                elif fl.endswith(".clap"):
                    self._append_item(path, "CLAP Plugin")
                elif fl.endswith("manifest.json"):
                    self._append_item(path, "Paquete VSTPack")
                elif fl.endswith((".zip", ".rar", ".7z", ".vstpack", ".tar.gz", ".tar.bz2", ".tar.xz")):
                    archives_to_unpack.append(path)

        if archives_to_unpack:
            self.lbl_status.setText(f"Descomprimiendo {len(archives_to_unpack)} archivo(s) comprimido(s)...")
            worker = ArchiveExtractorWorker(archives_to_unpack)
            self.extractor_workers.append(worker)
            worker.status_update.connect(lambda msg: self.log_text.appendPlainText(msg))
            worker.temp_dir_created.connect(self.temp_dirs.append)
            
            def on_extracted(items, w=worker):
                self.on_archives_extracted(items)
                if w in self.extractor_workers:
                    self.extractor_workers.remove(w)
            
            worker.extracted_items.connect(on_extracted)
            worker.start()

    def on_archives_extracted(self, items):
        if items:
            self.add_files(items)
            self.log_text.appendPlainText(f"✓ Se extrajeron e identificaron {len(items)} plugins/instaladores.")
        else:
            self.log_text.appendPlainText("No se encontraron instaladores o plugins compatibles dentro del archivo.")
        self.lbl_status.setText(f"{len(self.queue_items)} elementos en la cola.")

    def _append_item(self, path, item_type):
        for item in self.queue_items:
            if item['path'] == path:
                return

        row = self.table.rowCount()
        self.table.insertRow(row)

        name_item = QtWidgets.QTableWidgetItem(os.path.basename(path))
        type_item = QtWidgets.QTableWidgetItem(item_type)
        path_item = QtWidgets.QTableWidgetItem(path)
        status_item = QtWidgets.QTableWidgetItem("Pendiente")

        self.table.setItem(row, 0, name_item)
        self.table.setItem(row, 1, type_item)
        self.table.setItem(row, 2, path_item)
        self.table.setItem(row, 3, status_item)

        self.queue_items.append({
            'path': path,
            'type': item_type,
            'row': row
        })
        self.lbl_status.setText(f"{len(self.queue_items)} elementos en la cola.")

    def clear_list(self):
        self.table.setRowCount(0)
        self.queue_items.clear()
        
        for d in self.temp_dirs:
            if os.path.isdir(d):
                shutil.rmtree(d, ignore_errors=True)
        self.temp_dirs.clear()

        self.lbl_status.setText("Lista vaciada.")
        self.progress_bar.setVisible(False)
        self.log_text.clear()

    def start_installation(self):
        if not self.queue_items:
            QtWidgets.QMessageBox.information(self, "Lista vacía", "Agrega archivos o instaladores antes de continuar.")
            return

        if not self.wine_root or not os.path.exists(os.path.join(self.wine_root, "bin", "wine")):
            QtWidgets.QMessageBox.critical(self, "Error de Wine", f"No se encontró el runtime de Wine en:\n{self.wine_root}")
            return

        self.btn_start.setEnabled(False)
        self.btn_add_files.setEnabled(False)
        self.btn_add_folder.setEnabled(False)
        self.btn_clear.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setMaximum(len(self.queue_items))
        self.progress_bar.setValue(0)

        silent = self.chk_silent.isChecked()
        self.worker = InstallWorker(self.queue_items, self.wine_prefix, self.wine_root, silent_mode=silent)
        self.worker.progress.connect(self.on_progress)
        self.worker.item_status.connect(self.on_item_status)
        self.worker.log_message.connect(lambda msg: self.log_text.appendPlainText(msg))
        self.worker.finished.connect(self.on_finished)
        self.worker.start()

    def on_progress(self, current, total, name):
        self.progress_bar.setValue(current)
        self.lbl_status.setText(f"Instalando [{current}/{total}]: {name}")

    def on_item_status(self, row, status_text, color_hex):
        item = self.table.item(row, 3)
        if item:
            item.setText(status_text)
            item.setForeground(QtGui.QColor(color_hex))

    def on_finished(self):
        self.btn_start.setEnabled(True)
        self.btn_add_files.setEnabled(True)
        self.btn_add_folder.setEnabled(True)
        self.btn_clear.setEnabled(True)
        self.lbl_status.setText("✓ Todos los plugins han sido procesados.")
        self.refresh_installed_ecosystem()
        QtWidgets.QMessageBox.information(
            self,
            "Instalación Completa",
            "La instalación por lotes ha finalizado con éxito.\n\n"
            "Consejo para Ableton Live:\n"
            "Abre Ableton Live > Preferencias > Plug-ins y mantén presionada la tecla 'Alt' (Option) al hacer clic en 'Rescan' para forzar un re-escaneo completo."
        )

    def on_tab_changed(self, index):
        if index == 0:
            self.refresh_installed_ecosystem()

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            QtWidgets.QMessageBox.warning(self, "Operación en progreso", "Hay una instalación en progreso. Por favor espera a que termine antes de cerrar.")
            event.ignore()
            return
            
        if self.export_worker and self.export_worker.isRunning():
            QtWidgets.QMessageBox.warning(self, "Operación en progreso", "Hay una exportación en progreso. Por favor espera a que termine antes de cerrar.")
            event.ignore()
            return
            
        if self.extractor_workers and any(w.isRunning() for w in self.extractor_workers):
            QtWidgets.QMessageBox.warning(self, "Operación en progreso", "Hay extracciones de archivos en progreso. Por favor espera a que terminen antes de cerrar.")
            event.ignore()
            return

        for d in self.temp_dirs:
            if os.path.isdir(d):
                shutil.rmtree(d, ignore_errors=True)
        event.accept()

    def open_folder_in_fm(self, folder_path):
        os.makedirs(folder_path, exist_ok=True)
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(folder_path))

    def show_live_help(self):
        msg = (
            "<h3>Cómo hacer que Ableton Live detecte tus plugins:</h3>"
            "<ol>"
            "<li>Abre Ableton Live.</li>"
            "<li>Ve a <b>Options / Settings > Preferences > Plug-ins</b>.</li>"
            "<li>Asegúrate de que estén activadas las opciones:"
            "<ul>"
            "<li><b>Use VST2 Plug-in System Folders</b>: ON</li>"
            "<li><b>Use VST3 Plug-in System Folders</b>: ON</li>"
            "<li><b>Use VST2 Plug-in Custom Folder</b>: ON (apuntando a <code>C:\\Program Files\\VSTPlugins</code>)</li>"
            "</ul></li>"
            "<li><b>TRUCO PARA FORZAR RE-ESCANEO:</b> Mantén presionada la tecla <b>Alt</b> (u <i>Option</i>) en tu teclado y haz clic en el botón <b>Rescan</b>. Esto forzará a Live a re-evaluar todos los plugins nuevos o modificados.</li>"
            "</ol>"
        )
        QtWidgets.QMessageBox.information(self, "Guía de Detección de Plugins", msg)

    def apply_kde_native_theme(self):
        available_styles = QtWidgets.QStyleFactory.keys()
        if "Breeze" in available_styles:
            QtWidgets.QApplication.setStyle("Breeze")
        elif "Fusion" in available_styles:
            QtWidgets.QApplication.setStyle("Fusion")

        pal = self.palette()
        accent = pal.highlight().color().name()
        win_bg = pal.window().color().name()
        base_bg = pal.base().color().name()
        text_fg = pal.text().color().name()
        mid_color = pal.mid().color().name()

        is_dark = pal.window().color().lightness() < 128
        border_color = "#334155" if is_dark else "#cbd5e1"
        card_bg = "#161b22" if is_dark else "#f1f5f9"
        header_bg = "#0f172a" if is_dark else "#e2e8f0"
        drop_bg = "#1e293b" if is_dark else "#f8fafc"

        self.setStyleSheet(f"""
            QFrame#HeaderFrame {{
                background-color: {header_bg};
                border: 1px solid {border_color};
                border-radius: 8px;
            }}
            QFrame#CardFrame, QFrame#IntroFrame {{
                background-color: {card_bg};
                border: 1px solid {border_color};
                border-radius: 8px;
            }}
            QFrame#DropArea {{
                border: 2px dashed {mid_color};
                border-radius: 8px;
                background-color: {drop_bg};
            }}
            QFrame#DropArea:hover {{
                border-color: {accent};
            }}
            QLabel#AccentLabel {{
                color: {accent};
                font-family: monospace;
                font-weight: bold;
            }}
            QLabel#SecondaryAccentLabel {{
                color: {accent};
                font-family: monospace;
            }}
            QLabel#AccentHeading {{
                color: {accent};
                font-size: 15px;
            }}
            QPlainTextEdit {{
                background-color: {base_bg};
                color: {text_fg};
                border: 1px solid {border_color};
                border-radius: 4px;
                font-family: monospace;
            }}
        """)


def main():
    app = QtWidgets.QApplication(sys.argv)
    
    app.setApplicationName("Instrumentarium")
    app.setDesktopFileName("instrumentarium")
    app.setOrganizationName("AbletonLinux")
    
    window = VSTInstallerApp()
    if len(sys.argv) > 1:
        window.add_files(sys.argv[1:])
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
