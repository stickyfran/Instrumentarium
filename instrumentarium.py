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



def get_drive_c_path(wine_prefix: str, is_windows: bool = False) -> str:
    if is_windows or sys.platform == "win32":
        return os.environ.get("SystemDrive", "C:") + "\\\\"
    return os.path.join(wine_prefix, "drive_c")


def get_wine_users(drive_c: str) -> list:
    users = []
    users_dir = os.path.join(drive_c, "users")
    if os.path.isdir(users_dir):
        for u in os.listdir(users_dir):
            if u.lower() not in ["public", "default", "default user", "all users"] and os.path.isdir(os.path.join(users_dir, u)):
                users.append(u)
    if not users:
        users = [os.environ.get("USER", os.environ.get("USERNAME", "user"))]
    return users


def get_primary_wine_user(drive_c: str) -> str:
    users = get_wine_users(drive_c)
    host_user = os.environ.get("USER", os.environ.get("USERNAME", ""))
    for u in users:
        if u.lower() == host_user.lower():
            return u
    return users[0] if users else "user"


def is_product_tracked(product: dict, wine_prefix: str) -> bool:
    tracker_dir = os.path.join(wine_prefix, ".vst_tracker")
    if not os.path.isdir(tracker_dir):
        return False
    
    p_files_set = set(product.get("files", []))
    p_name_clean = re.sub(r'[^a-zA-Z0-9_]', '_', product.get("name", "")).lower()
    
    try:
        for rf in os.listdir(tracker_dir):
            if rf.endswith(".json"):
                rf_stem = os.path.splitext(rf)[0].lower()
                name_match = (rf_stem == p_name_clean or rf_stem.startswith(f"{p_name_clean}_"))
                if p_name_clean and name_match:
                    return True
                try:
                    with open(os.path.join(tracker_dir, rf), "r", encoding="utf-8") as f_rf:
                        rdata = json.load(f_rf)
                        r_files = set(rdata.get("new_files", []))
                        if p_files_set.intersection(r_files):
                            return True
                        for pf in p_files_set:
                            pf_dir = pf if pf.endswith(os.sep) else pf + os.sep
                            for f_ in r_files:
                                f_dir = f_ if f_.endswith(os.sep) else f_ + os.sep
                                if f_.startswith(pf_dir) or pf.startswith(f_dir):
                                    return True
                except Exception:
                    pass
    except Exception:
        pass
    return False


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


def should_ignore_snapshot_path(fp: str) -> bool:
    """Filters out transient installer caches and tracker receipts from tracking diffs."""
    norm = fp.replace("\\", "/").lower()
    if "/.vst_tracker/" in norm or norm.endswith("/.vst_tracker") or ".vst_tracker" in norm.split("/"):
        return True
    if "/windows/temp/" in norm or "/appdata/local/temp/" in norm or "/users/temp/" in norm:
        return True
    if "/inetcache/" in norm or "/crashdumps/" in norm or "/cryptneturlcache/" in norm:
        return True
    if norm.endswith(".tmp") or norm.endswith(".log") or norm.endswith("wineserver.pid") or norm.endswith("wine_server_lock"):
        return True
    if "/throttle_store/" in norm or norm.endswith("throttle_store"):
        return True
    return False


def sync_wineserver(wine_root: str, prefix: str, timeout: int = 10):
    """Flushes wine registry state to disk and waits for background wine processes."""
    if wine_root and os.path.isfile(os.path.join(wine_root, "bin", "wineserver")):
        try:
            env = os.environ.copy()
            env["WINEPREFIX"] = prefix
            ws = os.path.join(wine_root, "bin", "wineserver")
            subprocess.run([ws, "-w"], env=env, timeout=timeout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(0.3)
        except Exception:
            pass


def walk_prefix_files(drive_c: str):
    """Walks all prefix files including symlinked user directories (Documents, Music, etc.), yielding normalized virtual paths under drive_c."""
    if not os.path.isdir(drive_c):
        return

    # 1. Standard walk of drive_c
    for root, _, filenames in os.walk(drive_c):
        for f in filenames:
            yield os.path.join(root, f)

    # 2. Dynamically walk all symlinked user folders (Documents, Music, etc.) to capture presets installed outside drive_c root
    users = get_wine_users(drive_c)
    home_real = os.path.realpath(os.path.expanduser("~"))
    for u in users:
        u_dir = os.path.join(drive_c, "users", u)
        if os.path.isdir(u_dir):
            try:
                for entry in os.listdir(u_dir):
                    user_sub = os.path.join(u_dir, entry)
                    if os.path.islink(user_sub) and os.path.isdir(user_sub):
                        real_target = os.path.realpath(user_sub)
                        # Avoid walking whole home directory if user symlinked ~ itself
                        if real_target == home_real:
                            continue
                        for root, _, filenames in os.walk(real_target):
                            rel_inside = os.path.relpath(root, real_target)
                            for f in filenames:
                                if rel_inside == ".":
                                    virtual_fp = os.path.join(user_sub, f)
                                else:
                                    virtual_fp = os.path.join(user_sub, rel_inside, f)
                                yield virtual_fp
            except Exception:
                pass


def take_prefix_snapshot(prefix: str, wine_root: str = "") -> dict:
    """Takes a snapshot of the prefix filesystem and registry state."""
    sync_wineserver(wine_root, prefix, timeout=10)

    files = {}
    drive_c = get_drive_c_path(prefix)
    if os.path.isdir(drive_c):
        for fp in walk_prefix_files(drive_c):
            if should_ignore_snapshot_path(fp):
                continue
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
    sync_wineserver(wine_root, prefix, timeout=10)

    new_files = []
    drive_c = get_drive_c_path(prefix)
    before_files = before.get("files", {})

    if os.path.isdir(drive_c):
        for fp in walk_prefix_files(drive_c):
            if should_ignore_snapshot_path(fp):
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

    def __init__(self, items, wine_prefix, wine_root, silent_mode=False, is_windows=False):
        super().__init__()
        self.items = items
        self.wine_prefix = wine_prefix
        self.wine_root = wine_root
        self.silent_mode = silent_mode
        self.is_windows = is_windows
        self._is_cancelled = False

    def get_drive_c(self):
        return get_drive_c_path(self.wine_prefix, getattr(self, "is_windows", False))

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
                    # Detect target wine user dynamically in the destination prefix
                    target_wine_user = get_primary_wine_user(self.get_drive_c())

                    if os.path.isdir(drive_src):
                        for root, _, files in os.walk(drive_src):
                            rel = os.path.relpath(root, drive_src)
                            # Match target user folder dynamically
                            rel_parts = rel.split(os.sep)
                            if len(rel_parts) >= 2 and rel_parts[0].lower() == "users":
                                rel_parts[1] = target_wine_user
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
                                    rel_parts[1] = target_wine_user
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

                    # Track installed VST3
                    tracker_dir = os.path.join(self.wine_prefix, ".vst_tracker")
                    os.makedirs(tracker_dir, exist_ok=True)
                    stem = re.sub(r'[^a-zA-Z0-9_]', '_', os.path.splitext(filename)[0])
                    receipt_path = os.path.join(tracker_dir, f"{stem}_vst3_{int(time.time())}.json")
                    tracked_files = []
                    if os.path.isdir(dest):
                        for root, _, flist in os.walk(dest):
                            for f in flist:
                                tracked_files.append(os.path.join(root, f))
                        if not tracked_files:
                            tracked_files.append(dest)
                    else:
                        tracked_files.append(dest)

                    with open(receipt_path, "w", encoding="utf-8") as rpf:
                        json.dump({
                            "installer": filename,
                            "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            "new_files": tracked_files,
                            "reg_lines_count": 0,
                            "reg_diff": []
                        }, rpf, indent=2)

                    self.item_status.emit(row, "✓ VST3 Instalado", "#16a34a")
                    self.log_message.emit(f"Copiado VST3 a: {dest} (Rastreado)")

                # 2. VST2 DLL
                elif file_path.lower().endswith(".dll"):
                    dest = os.path.join(vst2_dest, filename)
                    if os.path.exists(dest):
                        if os.path.isdir(dest):
                            shutil.rmtree(dest)
                        else:
                            os.remove(dest)
                    shutil.copy2(file_path, dest)

                    # Track installed VST2 DLL
                    tracker_dir = os.path.join(self.wine_prefix, ".vst_tracker")
                    os.makedirs(tracker_dir, exist_ok=True)
                    stem = re.sub(r'[^a-zA-Z0-9_]', '_', os.path.splitext(filename)[0])
                    receipt_path = os.path.join(tracker_dir, f"{stem}_vst2_{int(time.time())}.json")
                    with open(receipt_path, "w", encoding="utf-8") as rpf:
                        json.dump({
                            "installer": filename,
                            "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            "new_files": [dest],
                            "reg_lines_count": 0,
                            "reg_diff": []
                        }, rpf, indent=2)

                    self.item_status.emit(row, "✓ VST2 Instalado", "#16a34a")
                    self.log_message.emit(f"Copiado VST2 DLL a: {dest} (Rastreado)")

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

                    # Track installed CLAP
                    tracker_dir = os.path.join(self.wine_prefix, ".vst_tracker")
                    os.makedirs(tracker_dir, exist_ok=True)
                    stem = re.sub(r'[^a-zA-Z0-9_]', '_', os.path.splitext(filename)[0])
                    receipt_path = os.path.join(tracker_dir, f"{stem}_clap_{int(time.time())}.json")
                    tracked_files = []
                    if os.path.isdir(dest):
                        for root, _, flist in os.walk(dest):
                            for f in flist:
                                tracked_files.append(os.path.join(root, f))
                        if not tracked_files:
                            tracked_files.append(dest)
                    else:
                        tracked_files.append(dest)

                    with open(receipt_path, "w", encoding="utf-8") as rpf:
                        json.dump({
                            "installer": filename,
                            "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            "new_files": tracked_files,
                            "reg_lines_count": 0,
                            "reg_diff": []
                        }, rpf, indent=2)

                    self.item_status.emit(row, "✓ CLAP Instalado", "#16a34a")
                    self.log_message.emit(f"Copiado CLAP a: {dest} (Rastreado)")

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
                    receipt_path = os.path.join(tracker_dir, f"{stem}_exe_{int(time.time())}.json")
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


def extract_wine_registry_keys(wine_prefix: str, keys_to_export: set, receipts: list = None, is_windows: bool = False) -> str:
    """Extracts Windows Registry keys cleanly in pure Python from Wine's user.reg and system.reg,
    and merges any captured diffs from receipts, with ZERO Wine GUI popups or processes."""
    output_lines = ["Windows Registry Editor Version 5.00\n"]
    seen_sections = set()

    # 1. First add registry diffs from receipts
    if receipts:
        for r in receipts:
            reg_diff = r.get("reg_diff", [])
            if reg_diff:
                for line in reg_diff:
                    line_clean = line.strip()
                    if line_clean.startswith("[") and line_clean.endswith("]"):
                        sec_header = line_clean[1:-1]
                        if not sec_header.startswith("HKEY_"):
                            sec_header = f"HKEY_CURRENT_USER\\{sec_header}"
                        sec_header = re.sub(r'\\+', r'\\', sec_header)
                        if sec_header not in seen_sections:
                            seen_sections.add(sec_header)
                            output_lines.append(f"\n[{sec_header}]\n")
                    elif line_clean and not line_clean.startswith("Windows Registry Editor"):
                        output_lines.append(line + ("\n" if not line.endswith("\n") else ""))

    if is_windows:
        return "".join(output_lines)

    # 2. Extract matching keys from user.reg (HKCU) and system.reg (HKLM)
    user_reg = os.path.join(wine_prefix, "user.reg")
    system_reg = os.path.join(wine_prefix, "system.reg")

    def parse_and_extract(reg_path, default_hive):
        if not os.path.isfile(reg_path):
            return
        
        targets = [k.replace("/", "\\").lower() for k in keys_to_export if k]
        section_matches = False
        current_full_sec = None

        def match_target(sec_l, target_key):
            sub = target_key
            if sub.startswith("software\\"):
                sub = sub[len("software\\"):]
            if not sub or sub in ["classes", "microsoft", "wine", "wow6432node"]:
                return False
            if sec_l == target_key or sec_l.startswith(target_key + "\\"):
                return True
            wow_target = f"software\\wow6432node\\{sub}"
            if sec_l == wow_target or sec_l.startswith(wow_target + "\\"):
                return True
            return False

        try:
            with open(reg_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped.startswith("[") and "]" in stripped:
                        raw_sec = stripped[1:stripped.rfind("]")].replace("/", "\\")
                        raw_sec_clean = raw_sec.split("]")[0].strip()
                        current_full_sec = f"{default_hive}\\{raw_sec_clean}" if not raw_sec_clean.startswith("HKEY_") else raw_sec_clean
                        current_full_sec = re.sub(r'\\+', r'\\', current_full_sec)
                        sec_lower = raw_sec_clean.lower()

                        section_matches = any(match_target(sec_lower, t) for t in targets)

                        if section_matches:
                            if current_full_sec not in seen_sections:
                                seen_sections.add(current_full_sec)
                                output_lines.append(f"\n[{current_full_sec}]\n")
                            else:
                                section_matches = False
                    elif section_matches:
                        if stripped.startswith("#") or stripped.startswith(";"):
                            continue
                        if stripped:
                            output_lines.append(line if line.endswith("\n") else line + "\n")
                        elif output_lines and output_lines[-1] != "\n":
                            output_lines.append("\n")
        except Exception:
            pass

    parse_and_extract(user_reg, "HKEY_CURRENT_USER")
    parse_and_extract(system_reg, "HKEY_LOCAL_MACHINE")

    return "".join(output_lines)


def create_vstpack_bundle(target_archive, products, wine_prefix, wine_root, is_windows=False):
    temp_dir = tempfile.mkdtemp(prefix="vstpack_build_")
    try:
        drive_dest = os.path.join(temp_dir, "drive_c")
        os.makedirs(drive_dest, exist_ok=True)

        manifest = {
            "version": "1.0",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "source_os": "Windows (Native)" if is_windows else "Linux (Wine)",
            "compatibility": ["Linux (Instrumentarium)", "Windows 10/11 (Native)"],
            "products": []
        }

        keys_to_export = set()
        tracker_dir = os.path.join(wine_prefix, ".vst_tracker")
        receipts = []
        if os.path.isdir(tracker_dir):
            for rf in os.listdir(tracker_dir):
                if rf.endswith(".json"):
                    try:
                        with open(os.path.join(tracker_dir, rf), "r", encoding="utf-8") as f:
                            receipts.append(json.load(f))
                    except Exception:
                        pass

        drive_c_src = get_drive_c_path(wine_prefix, is_windows)

        for p in products:
            p_entry = {
                "name": p["name"],
                "vendor": p["vendor"],
                "formats": p["formats"],
                "version": p["version"],
                "files": []
            }
            
            p_files_set = set(p.get("files", []))
            p_name_clean = re.sub(r'[^a-zA-Z0-9_]', '_', p.get("name", "")).lower()

            for r in receipts:
                r_files = r.get("new_files", [])
                r_installer = r.get("installer", "").lower()
                match = any(f in p_files_set for f in r_files) or (p_name_clean and p_name_clean in r_installer)
                if not match:
                    for pf in p_files_set:
                        pf_dir = pf if pf.endswith(os.sep) else pf + os.sep
                        for rf in r_files:
                            rf_dir = rf if rf.endswith(os.sep) else rf + os.sep
                            if rf.startswith(pf_dir) or pf.startswith(rf_dir):
                                match = True
                                break
                        if match:
                            break

                if match:
                    for f in r_files:
                        if f not in p["files"] and os.path.exists(f):
                            p["files"].append(f)
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

            candidate_folders = set()
            if p.get("vendor") and p["vendor"] != "Desconocido":
                candidate_folders.add(p["vendor"])
            if p.get("name"):
                candidate_folders.add(p["name"])
                # Also add name without spaces or trailing numbers
                clean_n = re.sub(r'[^a-zA-Z0-9]', '', p["name"])
                if clean_n:
                    candidate_folders.add(clean_n)

            for cand in candidate_folders:
                keys_to_export.add(f"Software\\{cand}")

            copied_dirs = set()
            # Sort so directories are processed before individual files
            sorted_files = sorted(set(p.get("files", [])), key=lambda x: (len(x), x))

            for fpath in sorted_files:
                if not os.path.exists(fpath):
                    continue

                fpath_abs = os.path.abspath(fpath)
                # If this file is inside an already copied directory bundle, register it without re-copying
                if any(fpath_abs.startswith(cdir + os.sep) for cdir in copied_dirs):
                    rel = os.path.relpath(fpath, drive_c_src)
                    if not rel.startswith("..") and rel not in p_entry["files"]:
                        p_entry["files"].append(rel)
                    continue

                rel = os.path.relpath(fpath, drive_c_src)
                if rel.startswith(".."):
                    continue

                dest_file = os.path.join(drive_dest, rel)
                os.makedirs(os.path.dirname(dest_file), exist_ok=True)

                if os.path.isdir(fpath):
                    if os.path.exists(dest_file):
                        shutil.rmtree(dest_file, ignore_errors=True)
                    shutil.copytree(fpath, dest_file, symlinks=False)
                    copied_dirs.add(fpath_abs)
                    for root_s, _, fn_list in os.walk(dest_file):
                        for fn in fn_list:
                            rel_f = os.path.relpath(os.path.join(root_s, fn), drive_dest)
                            if rel_f not in p_entry["files"]:
                                p_entry["files"].append(rel_f)
                else:
                    if os.path.exists(dest_file):
                        try:
                            os.chmod(dest_file, 0o666)
                            os.remove(dest_file)
                        except Exception:
                            pass
                    shutil.copy2(fpath, dest_file)
                    if rel not in p_entry["files"]:
                        p_entry["files"].append(rel)

            wine_users = get_wine_users(drive_c_src)
            for cand in candidate_folders:
                for u_name in wine_users:
                    for sub in [
                        os.path.join("users", u_name, "AppData", "Roaming", cand),
                        os.path.join("users", u_name, "AppData", "Local", cand),
                        os.path.join("users", u_name, "Documents", cand),
                    ]:
                        src_sub = os.path.join(drive_c_src, sub)
                        if os.path.exists(src_sub):
                            dest_sub = os.path.join(drive_dest, sub)
                            os.makedirs(os.path.dirname(dest_sub), exist_ok=True)
                            if os.path.isdir(src_sub):
                                if not os.path.exists(dest_sub):
                                    shutil.copytree(src_sub, dest_sub, symlinks=False)
                                for root_s, _, fn_list in os.walk(dest_sub):
                                    for fn in fn_list:
                                        rel_f = os.path.relpath(os.path.join(root_s, fn), drive_dest)
                                        if rel_f not in p_entry["files"]:
                                            p_entry["files"].append(rel_f)
                            else:
                                if os.path.exists(dest_sub):
                                    try:
                                        os.chmod(dest_sub, 0o666)
                                        os.remove(dest_sub)
                                    except Exception:
                                        pass
                                shutil.copy2(src_sub, dest_sub)
                                rel_f = os.path.relpath(dest_sub, drive_dest)
                                if rel_f not in p_entry["files"]:
                                    p_entry["files"].append(rel_f)

                for sub in [
                    os.path.join("users", "Public", "Documents", cand),
                    os.path.join("ProgramData", cand),
                ]:
                    src_sub = os.path.join(drive_c_src, sub)
                    if os.path.exists(src_sub):
                        dest_sub = os.path.join(drive_dest, sub)
                        os.makedirs(os.path.dirname(dest_sub), exist_ok=True)
                        if os.path.isdir(src_sub):
                            if not os.path.exists(dest_sub):
                                shutil.copytree(src_sub, dest_sub, symlinks=False)
                            for root_s, _, fn_list in os.walk(dest_sub):
                                for fn in fn_list:
                                    rel_f = os.path.relpath(os.path.join(root_s, fn), drive_dest)
                                    if rel_f not in p_entry["files"]:
                                        p_entry["files"].append(rel_f)
                        else:
                            if os.path.exists(dest_sub):
                                try:
                                    os.chmod(dest_sub, 0o666)
                                    os.remove(dest_sub)
                                except Exception:
                                    pass
                            shutil.copy2(src_sub, dest_sub)
                            rel_f = os.path.relpath(dest_sub, drive_dest)
                            if rel_f not in p_entry["files"]:
                                p_entry["files"].append(rel_f)

            manifest["products"].append(p_entry)

        # Export Registry
        reg_file = os.path.join(temp_dir, "registry.reg")
        reg_content = extract_wine_registry_keys(wine_prefix, keys_to_export, receipts, is_windows)
        with open(reg_file, "w", encoding="utf-8") as rf:
            rf.write(reg_content)

        # Write manifest.json
        with open(os.path.join(temp_dir, "manifest.json"), "w", encoding="utf-8") as mf:
            json.dump(manifest, mf, indent=2)

        # 1. install_windows.bat
        bat_content = r"""@echo off
title Instrumentarium VSTPack - Windows Installer
echo ========================================================
echo   Instrumentarium VSTPack - Instalador de Plugins para Windows
echo ========================================================
echo.

:: 1. Copiar VST3
if exist "drive_c\Program Files\Common Files\VST3" (
    echo [1/6] Copiando plugins VST3...
    if not exist "%CommonProgramFiles%\VST3" mkdir "%CommonProgramFiles%\VST3"
    xcopy /E /I /Y "drive_c\Program Files\Common Files\VST3\*" "%CommonProgramFiles%\VST3\" >nul
    echo   [OK] VST3 instalados en %CommonProgramFiles%\VST3
)

:: 2. Copiar VST2 (x64)
if exist "drive_c\Program Files\VSTPlugins" (
    echo [2/6] Copiando plugins VST2 (x64)...
    if not exist "%ProgramFiles%\VSTPlugins" mkdir "%ProgramFiles%\VSTPlugins"
    xcopy /E /I /Y "drive_c\Program Files\VSTPlugins\*" "%ProgramFiles%\VSTPlugins\" >nul
    echo   [OK] VST2 instalados en %ProgramFiles%\VSTPlugins
)

:: 3. Copiar CLAP
if exist "drive_c\Program Files\Common Files\CLAP" (
    echo [3/6] Copiando plugins CLAP...
    if not exist "%CommonProgramFiles%\CLAP" mkdir "%CommonProgramFiles%\CLAP"
    xcopy /E /I /Y "drive_c\Program Files\Common Files\CLAP\*" "%CommonProgramFiles%\CLAP\" >nul
    echo   [OK] CLAP instalados en %CommonProgramFiles%\CLAP
)

:: 4. Copiar ProgramData
if exist "drive_c\ProgramData" (
    echo [4/6] Copiando datos compartidos a ProgramData...
    xcopy /E /I /Y "drive_c\ProgramData\*" "%ProgramData%\" >nul
    echo   [OK] ProgramData actualizado.
)

:: 5. Copiar datos de usuario (AppData / Documents / Public Documents)
echo [5/6] Copiando presets, librerias y configuraciones de usuario...
if exist "drive_c\users\Public\Documents" (
    if not exist "%PUBLIC%\Documents" mkdir "%PUBLIC%\Documents"
    xcopy /E /I /Y "drive_c\users\Public\Documents\*" "%PUBLIC%\Documents\" >nul
)
for /D %%U in ("drive_c\users\*") do (
    if /I not "%%~nxU"=="Public" if /I not "%%~nxU"=="Default" if /I not "%%~nxU"=="Default User" if /I not "%%~nxU"=="All Users" (
        if exist "%%U\AppData\Roaming" (
            xcopy /E /I /Y "%%U\AppData\Roaming\*" "%APPDATA%\" >nul
        )
        if exist "%%U\AppData\Local" (
            xcopy /E /I /Y "%%U\AppData\Local\*" "%LOCALAPPDATA%\" >nul
        )
        if exist "%%U\Documents" (
            xcopy /E /I /Y "%%U\Documents\*" "%USERPROFILE%\Documents\" >nul
        )
    )
)
echo   [OK] Presets, samples y librerias de usuario actualizados.

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

        # 2. install_linux.sh
        sh_content = r"""#!/usr/bin/env bash
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
    
    # 1. Copiar carpetas del sistema (Program Files, ProgramData, etc.)
    for item in "$here/drive_c"/*; do
        b="$(basename "$item")"
        if [ "$b" != "users" ]; then
            cp -ru "$item" "$PREFIX/drive_c/"
        fi
    done

    # 2. Mapear datos de usuario (AppData, Documents) al usuario activo del prefijo destino
    TARGET_USER="$(ls "$PREFIX/drive_c/users" 2>/dev/null | grep -viE '^(public|default|all users|default user)$' | head -n 1 || echo "${USER:-user}")"
    if [ -d "$here/drive_c/users" ]; then
        for udir in "$here/drive_c/users"/*; do
            if [ -d "$udir" ]; then
                ubase="$(basename "$udir")"
                if [ "$ubase" = "Public" ] || [ "$ubase" = "Default" ] || [ "$ubase" = "default user" ] || [ "$ubase" = "All Users" ]; then
                    mkdir -p "$PREFIX/drive_c/users/$ubase"
                    cp -ru "$udir/." "$PREFIX/drive_c/users/$ubase/"
                else
                    mkdir -p "$PREFIX/drive_c/users/$TARGET_USER"
                    cp -ru "$udir/." "$PREFIX/drive_c/users/$TARGET_USER/"
                fi
            fi
        done
    fi
    echo "  [OK] Archivos y datos de usuario restaurados."
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

        # 3. Readme
        readme_content = f"""# Instrumentarium VSTPack: {', '.join(p['name'] for p in products)}

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

        zip_path = target_archive if target_archive.endswith((".zip", ".vstpack")) else f"{target_archive}.vstpack"
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for root, _, files in os.walk(temp_dir):
                for f in files:
                    full_f = os.path.join(root, f)
                    rel_f = os.path.relpath(full_f, temp_dir)
                    zf.write(full_f, rel_f)

        return zip_path
    finally:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)


class ExportWorker(QtCore.QThread):
    progress = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal(bool, str)

    def __init__(self, target_path, products, wine_prefix, wine_root, mode="monolithic", is_windows=False):
        super().__init__()
        self.target_path = target_path
        self.products = products
        self.wine_prefix = wine_prefix
        self.wine_root = wine_root
        self.mode = mode
        self.is_windows = is_windows

    def run(self):
        try:
            if self.mode == "individual_folder":
                os.makedirs(self.target_path, exist_ok=True)
                total = len(self.products)
                exported_names = []
                for idx, p in enumerate(self.products, start=1):
                    clean_name = re.sub(r'[/\:*?"<>|]', '_', p["name"]).strip()
                    self.progress.emit(f"Exportando ({idx}/{total}): {p['name']}.vstpack...")
                    target_file = os.path.join(self.target_path, f"{clean_name}.vstpack")
                    create_vstpack_bundle(target_file, [p], self.wine_prefix, self.wine_root, self.is_windows)
                    exported_names.append(f"{clean_name}.vstpack")

                summary_path = os.path.join(self.target_path, "backup_manifest.json")
                with open(summary_path, "w", encoding="utf-8") as sf:
                    json.dump({
                        "backup_date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "total_packages": total,
                        "packages": exported_names
                    }, sf, indent=2)

                self.finished.emit(True, self.target_path)
            else:
                self.progress.emit("Generando paquete consolidado...")
                res_path = create_vstpack_bundle(self.target_path, self.products, self.wine_prefix, self.wine_root, self.is_windows)
                self.finished.emit(True, res_path)
        except Exception as e:
            self.finished.emit(False, str(e))


def format_file_size(size_bytes: int) -> str:
    """Formats bytes into human readable string (KB, MB, GB)."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def get_product_detailed_info(product: dict, wine_prefix: str) -> dict:
    """Gathers all binaries, receipts, documents/preset files, AppData, and registry keys for a product."""
    drive_c = get_drive_c_path(wine_prefix)
    p_files = list(product.get("files", []))
    p_files_set = set(p_files)
    tracker_dir = os.path.join(wine_prefix, ".vst_tracker")
    receipts = []
    p_name_clean = re.sub(r'[^a-zA-Z0-9_]', '_', product.get("name", "")).lower()

    # Find matching receipts
    if os.path.isdir(tracker_dir):
        for rf in os.listdir(tracker_dir):
            if rf.endswith(".json"):
                rpath = os.path.join(tracker_dir, rf)
                try:
                    with open(rpath, "r", encoding="utf-8") as rf_fd:
                        rdata = json.load(rf_fd)
                        r_files = rdata.get("new_files", [])
                        rf_stem = os.path.splitext(rf)[0].lower()
                        name_match = (rf_stem == p_name_clean or rf_stem.startswith(f"{p_name_clean}_"))
                        match = any(f_ in p_files_set for f_ in r_files) or (p_name_clean and name_match)
                        if not match:
                            for pf in p_files_set:
                                pf_dir = pf if pf.endswith(os.sep) else pf + os.sep
                                if any(rf_.startswith(pf_dir) for rf_ in r_files):
                                    match = True
                                    break
                        if match:
                            receipts.append({"filename": rf, "data": rdata})
                            for f in r_files:
                                if f not in p_files_set and os.path.exists(f):
                                    p_files.append(f)
                                    p_files_set.add(f)
                except Exception:
                    pass

    # Check candidate folders in Documents, AppData, ProgramData
    candidate_folders = set()
    if product.get("vendor") and product["vendor"] != "Desconocido":
        candidate_folders.add(product["vendor"])
    if product.get("name"):
        candidate_folders.add(product["name"])
        clean_n = re.sub(r'[^a-zA-Z0-9]', '', product["name"])
        if clean_n:
            candidate_folders.add(clean_n)

    wine_users = get_wine_users(drive_c)
    for cand in candidate_folders:
        for u_name in wine_users:
            for sub in [
                os.path.join("users", u_name, "AppData", "Roaming", cand),
                os.path.join("users", u_name, "AppData", "Local", cand),
                os.path.join("users", u_name, "Documents", cand),
            ]:
                src_sub = os.path.join(drive_c, sub)
                if os.path.exists(src_sub):
                    if os.path.isdir(src_sub):
                        for root_s, _, fn_list in os.walk(src_sub):
                            for fn in fn_list:
                                full_fp = os.path.join(root_s, fn)
                                if full_fp not in p_files_set:
                                    p_files.append(full_fp)
                                    p_files_set.add(full_fp)
                    elif src_sub not in p_files_set:
                        p_files.append(src_sub)
                        p_files_set.add(src_sub)

        for sub in [
            os.path.join("users", "Public", "Documents", cand),
            os.path.join("ProgramData", cand),
        ]:
            src_sub = os.path.join(drive_c, sub)
            if os.path.exists(src_sub):
                if os.path.isdir(src_sub):
                    for root_s, _, fn_list in os.walk(src_sub):
                        for fn in fn_list:
                            full_fp = os.path.join(root_s, fn)
                            if full_fp not in p_files_set:
                                p_files.append(full_fp)
                                p_files_set.add(full_fp)
                elif src_sub not in p_files_set:
                    p_files.append(src_sub)
                    p_files_set.add(src_sub)

    # Classify files with sizes
    classified_files = []
    for fp in p_files:
        if os.path.exists(fp):
            try:
                sz = os.path.getsize(fp) if not os.path.isdir(fp) else 0
            except Exception:
                sz = 0

            # Relative path
            rel = os.path.relpath(fp, drive_c) if not os.path.relpath(fp, drive_c).startswith("..") else fp

            # Category
            norm = fp.replace("\\", "/").lower()
            if ".vst3" in norm:
                cat = "Binario VST3"
            elif ".dll" in norm:
                cat = "Binario VST2 (DLL)"
            elif ".clap" in norm:
                cat = "Binario CLAP"
            elif ".exe" in norm:
                cat = "Standalone / Utilidad"
            elif "/documents/" in norm or "/documentos/" in norm:
                cat = "Presets / Librería (Documentos)"
            elif "/appdata/roaming/" in norm or "/appdata/local/" in norm:
                cat = "Configuración / Datos (AppData)"
            elif "/programdata/" in norm:
                cat = "Datos Compartidos (ProgramData)"
            else:
                cat = "Otro Archivo"

            classified_files.append({
                "path": fp,
                "relpath": rel,
                "size": sz,
                "category": cat
            })

    # Registry keys
    reg_lines = []
    for r in receipts:
        reg_lines.extend(r["data"].get("reg_diff", []))

    return {
        "product": product,
        "files": classified_files,
        "receipts": receipts,
        "reg_lines": reg_lines,
        "is_tracked": len(receipts) > 0,
        "total_size": sum(f["size"] for f in classified_files)
    }


def inspect_vstpack_data(vstpack_path: str) -> dict:
    """Extracts manifest, file list with uncompressed sizes, and registry contents from a .vstpack archive."""
    with zipfile.ZipFile(vstpack_path, "r") as z:
        manifest = {}
        if "manifest.json" in z.namelist():
            try:
                manifest = json.loads(z.read("manifest.json").decode("utf-8"))
            except Exception:
                pass

        reg_content = ""
        if "registry.reg" in z.namelist():
            try:
                reg_content = z.read("registry.reg").decode("utf-8", errors="ignore")
            except Exception:
                pass

        files_info = []
        for info in z.infolist():
            if info.is_dir() or info.filename in ["manifest.json", "registry.reg", "install_linux.sh", "install_windows.bat"]:
                continue
            files_info.append({
                "path": info.filename,
                "size": info.file_size,
                "compressed_size": info.compress_size
            })

        return {
            "path": vstpack_path,
            "manifest": manifest,
            "reg_content": reg_content,
            "files": files_info,
            "total_size": sum(f["size"] for f in files_info)
        }


class ProductDetailsDialog(QtWidgets.QDialog):
    """Inspects all tracked files, presets, AppData, registry keys, and installation receipts for a product or .vstpack package."""
    def __init__(self, parent=None, product=None, vstpack_path=None, wine_prefix="", wine_root="", is_windows=False):
        super().__init__(parent)
        self.product = product
        self.vstpack_path = vstpack_path
        self.wine_prefix = wine_prefix
        self.wine_root = wine_root
        self.is_windows = is_windows
        self.setMinimumSize(900, 640)
        self.resize(980, 700)

        self.main_layout = QtWidgets.QVBoxLayout(self)
        self.main_layout.setContentsMargins(14, 14, 14, 14)
        self.main_layout.setSpacing(10)

        if self.vstpack_path:
            self.setWindowTitle(f"Inspección de Paquete: {os.path.basename(self.vstpack_path)}")
            self.init_from_vstpack()
        else:
            p_name = self.product.get("name", "Plugin") if self.product else "Plugin"
            self.setWindowTitle(f"Detalles y ADN del Plugin: {p_name}")
            self.init_from_product()

    def clear_layout(self):
        while self.main_layout.count():
            item = self.main_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
            elif item.layout():
                while item.layout().count():
                    sub = item.layout().takeAt(0)
                    if sub.widget():
                        sub.widget().deleteLater()

    def refresh_from_product(self):
        self.clear_layout()
        self.init_from_product()
        if self.parent() and hasattr(self.parent(), "refresh_installed_ecosystem"):
            self.parent().refresh_installed_ecosystem()

    def remove_multiple_files_from_dna(self, file_paths):
        if not file_paths:
            return
        p_name = self.product.get("name", "Plugin") if self.product else "Plugin"
        confirm = QtWidgets.QMessageBox.question(
            self,
            "Desvincular Archivos del ADN",
            f"¿Deseas desvincular los {len(file_paths)} archivo(s) seleccionados del ADN de '{p_name}'?\n\n"
            "Nota: Los archivos físicos se conservarán intactos en tu disco. Solo se quitarán del tracking y de futuras exportaciones a .vstpack.",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No
        )
        if confirm != QtWidgets.QMessageBox.StandardButton.Yes:
            return

        to_remove_set = set(file_paths)
        tracker_dir = os.path.join(self.wine_prefix, ".vst_tracker")
        modified_count = 0
        if os.path.isdir(tracker_dir):
            for rf in os.listdir(tracker_dir):
                if rf.endswith(".json"):
                    rpath = os.path.join(tracker_dir, rf)
                    try:
                        with open(rpath, "r", encoding="utf-8") as rf_fd:
                            rdata = json.load(rf_fd)
                        orig_files = rdata.get("new_files", [])
                        new_files = [f for f in orig_files if f not in to_remove_set]
                        if len(new_files) != len(orig_files):
                            rdata["new_files"] = new_files
                            with open(rpath, "w", encoding="utf-8") as rf_fd:
                                json.dump(rdata, rf_fd, indent=2)
                            modified_count += 1
                    except Exception:
                        pass

        QtWidgets.QMessageBox.information(self, "Archivos Desvinculados", f"Se desvincularon {len(file_paths)} archivo(s) del ADN en {modified_count} recibo(s).")
        self.refresh_from_product()

    def remove_multiple_from_dna(self, selected_items):
        if not selected_items:
            return

        files_to_remove = set()
        receipts_to_remove = set()
        reg_lines_to_remove = set()

        for item in selected_items:
            meta = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
            if isinstance(meta, dict):
                m_type = meta.get("type")
                if m_type == "receipt":
                    receipts_to_remove.add(meta.get("filename"))
                elif m_type == "file":
                    files_to_remove.add((meta.get("filename"), meta.get("path")))
                elif m_type == "reg_line":
                    reg_lines_to_remove.add((meta.get("filename"), meta.get("line")))
                elif m_type == "files_category":
                    for c_idx in range(item.childCount()):
                        ch = item.child(c_idx)
                        ch_meta = ch.data(0, QtCore.Qt.ItemDataRole.UserRole)
                        if isinstance(ch_meta, dict) and ch_meta.get("path"):
                            files_to_remove.add((ch_meta.get("filename"), ch_meta.get("path")))
                elif m_type == "reg_category":
                    for c_idx in range(item.childCount()):
                        ch = item.child(c_idx)
                        ch_meta = ch.data(0, QtCore.Qt.ItemDataRole.UserRole)
                        if isinstance(ch_meta, dict) and ch_meta.get("line"):
                            reg_lines_to_remove.add((ch_meta.get("filename"), ch_meta.get("line")))

        total_elements = len(files_to_remove) + len(reg_lines_to_remove) + len(receipts_to_remove)
        if total_elements == 0:
            QtWidgets.QMessageBox.information(self, "Seleccionar", "Selecciona uno o varios recibos, archivos o líneas de registro para desvincular.")
            return

        p_name = self.product.get("name", "Plugin") if self.product else "Plugin"
        desc_parts = []
        if receipts_to_remove:
            desc_parts.append(f"• {len(receipts_to_remove)} recibo(s) completo(s)")
        if files_to_remove:
            desc_parts.append(f"• {len(files_to_remove)} archivo(s) individual(es)")
        if reg_lines_to_remove:
            desc_parts.append(f"• {len(reg_lines_to_remove)} entrada(s) de registro")

        confirm_msg = (
            f"¿Deseas desvincular del ADN de '{p_name}' los siguientes {total_elements} elementos seleccionados?\n\n"
            + "\n".join(desc_parts) + "\n\n"
            "Nota: Los archivos físicos en tu disco y el registro de Wine permanecerán intactos; "
            "solo se desvincularán del ADN y de futuras exportaciones a .vstpack."
        )

        confirm = QtWidgets.QMessageBox.question(
            self,
            "Desvincular Múltiples Elementos del ADN",
            confirm_msg,
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No
        )
        if confirm != QtWidgets.QMessageBox.StandardButton.Yes:
            return

        tracker_dir = os.path.join(self.wine_prefix, ".vst_tracker")
        if not os.path.isdir(tracker_dir):
            return

        # 1. Delete full receipts
        deleted_receipts_count = 0
        for rf_name in receipts_to_remove:
            rpath = os.path.join(tracker_dir, rf_name)
            if os.path.isfile(rpath):
                try:
                    os.remove(rpath)
                    deleted_receipts_count += 1
                except Exception:
                    pass

        # 2. Modify individual files & reg lines in remaining receipts
        files_by_receipt = {}
        for rf_name, fp in files_to_remove:
            if rf_name not in receipts_to_remove:
                files_by_receipt.setdefault(rf_name, set()).add(fp)

        reg_by_receipt = {}
        for rf_name, rline in reg_lines_to_remove:
            if rf_name not in receipts_to_remove:
                reg_by_receipt.setdefault(rf_name, set()).add(rline)

        all_target_receipts = set(files_by_receipt.keys()).union(set(reg_by_receipt.keys()))
        modified_receipts_count = 0

        for rf_name in all_target_receipts:
            rpath = os.path.join(tracker_dir, rf_name)
            if os.path.isfile(rpath):
                try:
                    with open(rpath, "r", encoding="utf-8") as rf_fd:
                        rdata = json.load(rf_fd)

                    modified = False
                    if rf_name in files_by_receipt:
                        to_del = files_by_receipt[rf_name]
                        new_files_list = [f for f in rdata.get("new_files", []) if f not in to_del]
                        if len(new_files_list) != len(rdata.get("new_files", [])):
                            rdata["new_files"] = new_files_list
                            modified = True

                    if rf_name in reg_by_receipt:
                        to_del_r = reg_by_receipt[rf_name]
                        new_reg_list = [r for r in rdata.get("reg_diff", []) if r not in to_del_r]
                        if len(new_reg_list) != len(rdata.get("reg_diff", [])):
                            rdata["reg_diff"] = new_reg_list
                            rdata["reg_lines_count"] = len(new_reg_list)
                            modified = True

                    if modified:
                        with open(rpath, "w", encoding="utf-8") as rf_fd:
                            json.dump(rdata, rf_fd, indent=2)
                        modified_receipts_count += 1
                except Exception:
                    pass

        QtWidgets.QMessageBox.information(
            self,
            "Desvinculación Completada",
            f"Se desvincularon correctamente los elementos seleccionados.\n\n"
            f"• Recibos eliminados: {deleted_receipts_count}\n"
            f"• Recibos actualizados: {modified_receipts_count}"
        )
        self.refresh_from_product()

    def remove_file_from_dna(self, file_path):
        if file_path:
            self.remove_multiple_files_from_dna([file_path])

    def remove_receipt_from_dna(self, receipt_filename):
        if not receipt_filename:
            return
        p_name = self.product.get("name", "Plugin") if self.product else "Plugin"
        confirm = QtWidgets.QMessageBox.question(
            self,
            "Eliminar Recibo de ADN",
            f"¿Estás seguro de eliminar el recibo histórico '{receipt_filename}' del ADN de '{p_name}'?\n\n"
            "Se desvincularán todos los archivos y entradas de registro capturadas en esta sesión.\n\n"
            "(Los archivos físicos en disco NO se borrarán).",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No
        )
        if confirm != QtWidgets.QMessageBox.StandardButton.Yes:
            return

        rpath = os.path.join(self.wine_prefix, ".vst_tracker", receipt_filename)
        if os.path.isfile(rpath):
            try:
                os.remove(rpath)
                QtWidgets.QMessageBox.information(self, "Recibo Eliminado", f"El recibo '{receipt_filename}' fue eliminado con éxito del ADN.")
            except Exception as e:
                QtWidgets.QMessageBox.critical(self, "Error al Eliminar", f"No se pudo eliminar el recibo:\n{e}")
        self.refresh_from_product()

    def remove_reg_line_from_dna(self, reg_line):
        if not reg_line:
            return
        confirm = QtWidgets.QMessageBox.question(
            self,
            "Desvincular Entrada de Registro",
            f"¿Deseas desvincular la siguiente entrada de registro del ADN?\n\n{reg_line}\n\n(No se modificará el registro actual de Wine/Windows, solo el ADN guardado).",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No
        )
        if confirm != QtWidgets.QMessageBox.StandardButton.Yes:
            return

        tracker_dir = os.path.join(self.wine_prefix, ".vst_tracker")
        modified_count = 0
        if os.path.isdir(tracker_dir):
            for rf in os.listdir(tracker_dir):
                if rf.endswith(".json"):
                    rpath = os.path.join(tracker_dir, rf)
                    try:
                        with open(rpath, "r", encoding="utf-8") as rf_fd:
                            rdata = json.load(rf_fd)
                        if reg_line in rdata.get("reg_diff", []):
                            rdata["reg_diff"].remove(reg_line)
                            rdata["reg_lines_count"] = len(rdata["reg_diff"])
                            with open(rpath, "w", encoding="utf-8") as rf_fd:
                                json.dump(rdata, rf_fd, indent=2)
                            modified_count += 1
                    except Exception:
                        pass

        QtWidgets.QMessageBox.information(self, "Registro Desvinculado", f"Se quitó la clave del ADN en {modified_count} recibo(s).")
        self.refresh_from_product()

    def init_from_product(self):
        data = get_product_detailed_info(self.product, self.wine_prefix)
        p = data["product"]
        files = data["files"]
        receipts = data["receipts"]
        reg_lines = data["reg_lines"]

        # Header card
        header_card = QtWidgets.QFrame()
        header_card.setObjectName("HeaderFrame")
        h_layout = QtWidgets.QHBoxLayout(header_card)
        h_layout.setContentsMargins(14, 12, 14, 12)

        v_title = QtWidgets.QVBoxLayout()
        lbl_name = QtWidgets.QLabel(f"<h2>{p.get('name', 'Plugin')}</h2>")
        lbl_name.setTextFormat(QtCore.Qt.TextFormat.RichText)

        vendor = p.get('vendor', 'Desconocido')
        ver = p.get('version', '')
        fmt_str = " • ".join(p.get('formats', ['VST3']))
        total_sz = format_file_size(data["total_size"])
        status_text = f"✓ Rastreado ({len(receipts)} recibos en ADN)" if data["is_tracked"] else "⚠️ No Rastreado (Solo binario base)"
        status_color = "#16a34a" if data["is_tracked"] else "#f59e0b"

        meta_lbl = QtWidgets.QLabel(
            f"<b>Fabricante:</b> {vendor}  |  "
            f"<b>Formatos:</b> {fmt_str}  |  "
            f"<b>Versión:</b> {ver if ver else 'N/A'}  |  "
            f"<b>Tamaño:</b> {total_sz}  |  "
            f"<b>Estado:</b> <span style='color: {status_color}; font-weight: bold;'>{status_text}</span>"
        )
        meta_lbl.setTextFormat(QtCore.Qt.TextFormat.RichText)
        v_title.addWidget(lbl_name)
        v_title.addWidget(meta_lbl)
        h_layout.addLayout(v_title)
        h_layout.addStretch()

        self.main_layout.addWidget(header_card)

        # Tab Widget
        tabs = QtWidgets.QTabWidget()

        # Tab 1: Files
        tab_files = QtWidgets.QWidget()
        tf_layout = QtWidgets.QVBoxLayout(tab_files)
        tf_layout.setContentsMargins(6, 8, 6, 6)

        search_h = QtWidgets.QHBoxLayout()
        txt_search = QtWidgets.QLineEdit()
        txt_search.setPlaceholderText("🔍 Filtrar archivos, presets o extensiones (.SerumPreset, .vst3, etc.)...")
        txt_search.setClearButtonEnabled(True)
        search_h.addWidget(txt_search)
        tf_layout.addLayout(search_h)

        table_files = QtWidgets.QTableWidget()
        table_files.setColumnCount(4)
        table_files.setHorizontalHeaderLabels(["Ruta Relativa en Paquete (Agnóstica)", "Categoría / Ubicación", "Tamaño", "Ruta Absoluta en este Sistema"])
        table_files.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        table_files.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        table_files.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        table_files.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.Interactive)
        table_files.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        table_files.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        table_files.setAlternatingRowColors(True)

        table_files.setRowCount(len(files))
        for r_idx, f_info in enumerate(files):
            item_rel = QtWidgets.QTableWidgetItem(f_info["relpath"])
            item_cat = QtWidgets.QTableWidgetItem(f_info["category"])
            item_sz = QtWidgets.QTableWidgetItem(format_file_size(f_info["size"]))
            item_sz.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
            item_abs = QtWidgets.QTableWidgetItem(f_info["path"])

            table_files.setItem(r_idx, 0, item_rel)
            table_files.setItem(r_idx, 1, item_cat)
            table_files.setItem(r_idx, 2, item_sz)
            table_files.setItem(r_idx, 3, item_abs)

        def filter_files_list(text):
            query = text.lower()
            for row in range(table_files.rowCount()):
                match = False
                for col in [0, 1, 3]:
                    it = table_files.item(row, col)
                    if it and query in it.text().lower():
                        match = True
                        break
                table_files.setRowHidden(row, not match)

        txt_search.textChanged.connect(filter_files_list)

        # Context menu for Table Files
        table_files.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        def show_files_menu(pos):
            selected_rows = table_files.selectionModel().selectedRows()
            if not selected_rows:
                return

            menu = QtWidgets.QMenu(self)
            if len(selected_rows) == 1:
                abs_fp = table_files.item(selected_rows[0].row(), 3).text()
                act_open = menu.addAction(get_system_icon("folder-open", "📂"), "Abrir Carpeta Contenedora")
                act_copy = menu.addAction(get_system_icon("edit-copy", "📋"), "Copiar Ruta Absoluta")
                menu.addSeparator()
                act_unlink = menu.addAction(get_system_icon("edit-delete", "🗑️"), "Quitar este Archivo del ADN (No borrará de disco)")

                action = menu.exec(table_files.viewport().mapToGlobal(pos))
                if action == act_open:
                    target = abs_fp if os.path.isdir(abs_fp) else os.path.dirname(abs_fp)
                    if os.path.exists(target):
                        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(target))
                elif action == act_copy:
                    QtWidgets.QApplication.clipboard().setText(abs_fp)
                    QtWidgets.QMessageBox.information(self, "Copiado", f"Ruta copiada al portapapeles:\n{abs_fp}")
                elif action == act_unlink:
                    self.remove_multiple_files_from_dna([abs_fp])
            else:
                paths = [table_files.item(r.row(), 3).text() for r in selected_rows if table_files.item(r.row(), 3)]
                act_copy = menu.addAction(get_system_icon("edit-copy", "📋"), f"Copiar {len(paths)} Rutas Absolutas")
                menu.addSeparator()
                act_unlink = menu.addAction(get_system_icon("edit-delete", "🗑️"), f"Quitar los {len(paths)} Archivos Seleccionados del ADN...")

                action = menu.exec(table_files.viewport().mapToGlobal(pos))
                if action == act_copy:
                    QtWidgets.QApplication.clipboard().setText("\n".join(paths))
                    QtWidgets.QMessageBox.information(self, "Copiado", f"Se copiaron {len(paths)} rutas al portapapeles.")
                elif action == act_unlink:
                    self.remove_multiple_files_from_dna(paths)

        table_files.customContextMenuRequested.connect(show_files_menu)
        tf_layout.addWidget(table_files)

        # File actions bar
        f_actions = QtWidgets.QHBoxLayout()
        lbl_file_count = QtWidgets.QLabel(f"Total: {len(files)} archivo(s) registrados.")
        lbl_file_count.setObjectName("SecondaryAccentLabel")
        f_actions.addWidget(lbl_file_count)
        f_actions.addStretch()

        btn_open_folder = QtWidgets.QPushButton("Abrir Carpeta Contenedora")
        btn_open_folder.setIcon(get_system_icon("folder-open", "📂"))
        def open_selected_file_folder():
            rows = table_files.selectionModel().selectedRows()
            if rows:
                row = rows[0].row()
                abs_path = table_files.item(row, 3).text()
                target = abs_path if os.path.isdir(abs_path) else os.path.dirname(abs_path)
                if os.path.exists(target):
                    QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(target))
            elif files:
                target = os.path.dirname(files[0]["path"])
                if os.path.exists(target):
                    QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(target))

        btn_open_folder.clicked.connect(open_selected_file_folder)

        btn_copy_paths = QtWidgets.QPushButton("Copiar Rutas")
        btn_copy_paths.setIcon(get_system_icon("edit-copy", "📋"))
        def copy_paths_action():
            rows = table_files.selectionModel().selectedRows()
            if rows:
                lines = [table_files.item(r.row(), 3).text() for r in rows]
            else:
                lines = [f["path"] for f in files]
            QtWidgets.QApplication.clipboard().setText("\n".join(lines))
            QtWidgets.QMessageBox.information(self, "Copiado", f"Se copiaron {len(lines)} ruta(s) al portapapeles.")

        btn_copy_paths.clicked.connect(copy_paths_action)

        btn_unlink_file = QtWidgets.QPushButton("Quitar Seleccionados del ADN")
        btn_unlink_file.setIcon(get_system_icon("edit-delete", "🗑️"))
        btn_unlink_file.setToolTip("Quita los archivos seleccionados del ADN y de los paquetes .vstpack (sin borrar archivos de disco).")
        def unlink_selected_files():
            rows = table_files.selectionModel().selectedRows()
            if rows:
                paths = [table_files.item(r.row(), 3).text() for r in rows if table_files.item(r.row(), 3)]
                self.remove_multiple_files_from_dna(paths)
            else:
                QtWidgets.QMessageBox.information(self, "Seleccionar", "Selecciona uno o más archivos de la tabla para desvincularlos del ADN.")

        btn_unlink_file.clicked.connect(unlink_selected_files)

        f_actions.addWidget(btn_open_folder)
        f_actions.addWidget(btn_copy_paths)
        f_actions.addWidget(btn_unlink_file)
        tf_layout.addLayout(f_actions)
        tabs.addTab(tab_files, get_system_icon("folder", "📁"), f"Archivos ({len(files)})")

        # Tab 2: Registry
        tab_reg = QtWidgets.QWidget()
        tr_layout = QtWidgets.QVBoxLayout(tab_reg)
        tr_layout.setContentsMargins(6, 8, 6, 6)

        txt_reg = QtWidgets.QPlainTextEdit()
        txt_reg.setReadOnly(True)
        if reg_lines:
            txt_reg.setPlainText("\n".join(reg_lines))
        else:
            txt_reg.setPlainText("; No se registraron claves de registro personalizadas para este plugin.\n; (Las claves genéricas del fabricante se exportan automáticamente al generar .vstpack).")

        tr_layout.addWidget(txt_reg)

        reg_actions = QtWidgets.QHBoxLayout()
        lbl_reg_count = QtWidgets.QLabel(f"Total: {len(reg_lines)} línea(s) de registro capturadas.")
        lbl_reg_count.setObjectName("SecondaryAccentLabel")
        reg_actions.addWidget(lbl_reg_count)
        reg_actions.addStretch()

        btn_copy_reg = QtWidgets.QPushButton("Copiar Registro")
        btn_copy_reg.setIcon(get_system_icon("edit-copy", "📋"))
        btn_copy_reg.clicked.connect(lambda: (QtWidgets.QApplication.clipboard().setText(txt_reg.toPlainText()), QtWidgets.QMessageBox.information(self, "Copiado", "Contenido del registro copiado al portapapeles.")))
        reg_actions.addWidget(btn_copy_reg)
        tr_layout.addLayout(reg_actions)

        tabs.addTab(tab_reg, get_system_icon("preferences-system", "🔑"), f"Registro ({len(reg_lines)})")

        # Tab 3: Receipts / History Tree
        tab_hist = QtWidgets.QWidget()
        th_layout = QtWidgets.QVBoxLayout(tab_hist)
        th_layout.setContentsMargins(6, 8, 6, 6)
        th_layout.setSpacing(8)

        hist_top_bar = QtWidgets.QHBoxLayout()
        txt_hist_search = QtWidgets.QLineEdit()
        txt_hist_search.setPlaceholderText("🔍 Filtrar en el historial de ADN (archivos, claves de registro, eventos, fechas)...")
        txt_hist_search.setClearButtonEnabled(True)
        hist_top_bar.addWidget(txt_hist_search)

        btn_expand_all = QtWidgets.QPushButton("Desplegar Todo")
        btn_expand_all.setIcon(get_system_icon("view-list-tree", "📂"))
        btn_collapse_all = QtWidgets.QPushButton("Colapsar Todo")
        btn_collapse_all.setIcon(get_system_icon("view-list-details", "📁"))
        hist_top_bar.addWidget(btn_expand_all)
        hist_top_bar.addWidget(btn_collapse_all)
        th_layout.addLayout(hist_top_bar)

        tree_hist = QtWidgets.QTreeWidget()
        tree_hist.setHeaderLabels(["Evento / Recibo de ADN", "Tipo / Ubicación", "Tamaño / Detalle"])
        tree_hist.header().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        tree_hist.header().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        tree_hist.header().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        tree_hist.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        tree_hist.setAlternatingRowColors(True)

        drive_c = get_drive_c_path(self.wine_prefix)

        for rc in receipts:
            r_data = rc["data"]
            r_installer = r_data.get("installer", "Instalador / Parche")
            r_date = r_data.get("installed_at", "N/A")
            r_files = r_data.get("new_files", [])
            r_reg = r_data.get("reg_diff", [])
            rf_name = rc.get("filename", "receipt.json")

            # Top-level node for the receipt
            root_item = QtWidgets.QTreeWidgetItem(tree_hist)
            root_item.setIcon(0, get_system_icon("document-save", "📜"))
            root_item.setText(0, f"Recibo: {r_installer} ({rf_name})")
            root_item.setText(1, "Evento Registrado")
            root_item.setText(2, f"Fecha: {r_date}")
            root_item.setFont(0, QtGui.QFont("", -1, QtGui.QFont.Weight.Bold))
            root_item.setData(0, QtCore.Qt.ItemDataRole.UserRole, {"type": "receipt", "filename": rf_name})

            # Child node 1: Files
            files_node = QtWidgets.QTreeWidgetItem(root_item)
            files_node.setIcon(0, get_system_icon("folder", "📁"))
            files_node.setText(0, f"Archivos Registrados en este Evento ({len(r_files)})")
            files_node.setText(1, "Lista de Archivos")
            files_node.setText(2, f"{len(r_files)} archivos")
            files_node.setFont(0, QtGui.QFont("", -1, QtGui.QFont.Weight.DemiBold))
            files_node.setData(0, QtCore.Qt.ItemDataRole.UserRole, {"type": "files_category", "filename": rf_name})

            for fp in r_files:
                f_item = QtWidgets.QTreeWidgetItem(files_node)
                rel = os.path.relpath(fp, drive_c) if not os.path.relpath(fp, drive_c).startswith("..") else fp
                norm = fp.replace("\\", "/").lower()
                if ".vst3" in norm:
                    cat = "Binario VST3"
                    f_item.setIcon(0, get_system_icon("audio-card", "🎹"))
                elif ".dll" in norm:
                    cat = "Binario VST2"
                    f_item.setIcon(0, get_system_icon("application-x-executable", "⚙️"))
                elif ".clap" in norm:
                    cat = "Binario CLAP"
                    f_item.setIcon(0, get_system_icon("audio-card", "🎹"))
                elif "/documents/" in norm or "/documentos/" in norm:
                    cat = "Presets / Librería"
                    f_item.setIcon(0, get_system_icon("document-open", "📚"))
                elif "/appdata/" in norm:
                    cat = "Config / Licencia"
                    f_item.setIcon(0, get_system_icon("preferences-system", "⚙️"))
                else:
                    cat = "Archivo de Soporte"
                    f_item.setIcon(0, get_system_icon("text-x-generic", "📄"))

                try:
                    sz = format_file_size(os.path.getsize(fp)) if os.path.exists(fp) and not os.path.isdir(fp) else "N/A"
                except Exception:
                    sz = "N/A"

                f_item.setText(0, rel)
                f_item.setText(1, cat)
                f_item.setText(2, sz)
                f_item.setData(0, QtCore.Qt.ItemDataRole.UserRole, {"type": "file", "path": fp, "filename": rf_name})

            # Child node 2: Registry
            if r_reg:
                reg_node = QtWidgets.QTreeWidgetItem(root_item)
                reg_node.setIcon(0, get_system_icon("preferences-system", "🔑"))
                reg_node.setText(0, f"Registro de Windows Capturado ({len(r_reg)} líneas)")
                reg_node.setText(1, "Claves de Registro")
                reg_node.setText(2, f"{len(r_reg)} líneas")
                reg_node.setFont(0, QtGui.QFont("", -1, QtGui.QFont.Weight.DemiBold))
                reg_node.setData(0, QtCore.Qt.ItemDataRole.UserRole, {"type": "reg_category", "filename": rf_name})

                for r_line in r_reg:
                    rl_item = QtWidgets.QTreeWidgetItem(reg_node)
                    rl_item.setIcon(0, get_system_icon("text-plain", "🔑"))
                    rl_item.setText(0, r_line.strip())
                    rl_item.setText(1, "Entrada de Registro")
                    rl_item.setText(2, "")
                    rl_item.setData(0, QtCore.Qt.ItemDataRole.UserRole, {"type": "reg_line", "line": r_line, "filename": rf_name})

        btn_expand_all.clicked.connect(tree_hist.expandAll)
        btn_collapse_all.clicked.connect(tree_hist.collapseAll)

        def filter_hist_tree(text):
            query = text.lower()
            for i in range(tree_hist.topLevelItemCount()):
                root = tree_hist.topLevelItem(i)
                root_match = query in root.text(0).lower() or query in root.text(2).lower()
                any_child_match = False
                for c_idx in range(root.childCount()):
                    child = root.child(c_idx)
                    child_match = query in child.text(0).lower() or query in child.text(1).lower()
                    sub_child_match = False
                    for sc_idx in range(child.childCount()):
                        sc = child.child(sc_idx)
                        sc_match = query in sc.text(0).lower() or query in sc.text(1).lower()
                        sc.setHidden(not sc_match if query else False)
                        if sc_match:
                            sub_child_match = True
                    child.setHidden(not (child_match or sub_child_match) if query else False)
                    if child_match or sub_child_match:
                        any_child_match = True
                root.setHidden(not (root_match or any_child_match) if query else False)
                if query and (root_match or any_child_match):
                    root.setExpanded(True)

        txt_hist_search.textChanged.connect(filter_hist_tree)

        def on_tree_double_clicked(item, col):
            meta = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
            if isinstance(meta, dict) and meta.get("type") == "file":
                fp = meta.get("path")
                if fp and os.path.exists(fp):
                    target = fp if os.path.isdir(fp) else os.path.dirname(fp)
                    QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(target))

        tree_hist.itemDoubleClicked.connect(on_tree_double_clicked)

        # Context menu for History Tree
        tree_hist.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        def show_hist_tree_menu(pos):
            selected_items = tree_hist.selectedItems()
            if not selected_items:
                return

            menu = QtWidgets.QMenu(self)
            if len(selected_items) == 1:
                item = selected_items[0]
                meta = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
                if isinstance(meta, dict):
                    m_type = meta.get("type")
                    if m_type == "receipt":
                        rf_name = meta.get("filename")
                        act_del_rc = menu.addAction(get_system_icon("edit-delete", "🗑️"), f"Eliminar Recibo '{rf_name}' del ADN...")
                        menu.addSeparator()
                        act_copy = menu.addAction(get_system_icon("edit-copy", "📋"), "Copiar Nombre de Recibo")
                        action = menu.exec(tree_hist.viewport().mapToGlobal(pos))
                        if action == act_del_rc:
                            self.remove_multiple_from_dna([item])
                        elif action == act_copy:
                            QtWidgets.QApplication.clipboard().setText(rf_name)

                    elif m_type == "file":
                        fp = meta.get("path")
                        act_open = menu.addAction(get_system_icon("folder-open", "📂"), "Abrir Carpeta Contenedora")
                        act_copy = menu.addAction(get_system_icon("edit-copy", "📋"), "Copiar Ruta Absoluta")
                        menu.addSeparator()
                        act_unlink = menu.addAction(get_system_icon("edit-delete", "🗑️"), "Quitar este Archivo del ADN (No borrará de disco)...")
                        action = menu.exec(tree_hist.viewport().mapToGlobal(pos))
                        if action == act_open:
                            target = fp if os.path.isdir(fp) else os.path.dirname(fp)
                            if os.path.exists(target):
                                QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(target))
                        elif action == act_copy:
                            QtWidgets.QApplication.clipboard().setText(fp)
                        elif action == act_unlink:
                            self.remove_multiple_from_dna([item])

                    elif m_type == "reg_line":
                        r_line = meta.get("line")
                        act_copy = menu.addAction(get_system_icon("edit-copy", "📋"), "Copiar Entrada de Registro")
                        menu.addSeparator()
                        act_del_reg = menu.addAction(get_system_icon("edit-delete", "🗑️"), "Quitar esta Entrada de Registro del ADN...")
                        action = menu.exec(tree_hist.viewport().mapToGlobal(pos))
                        if action == act_copy:
                            QtWidgets.QApplication.clipboard().setText(r_line)
                        elif action == act_del_reg:
                            self.remove_multiple_from_dna([item])

                    elif m_type in ["files_category", "reg_category"]:
                        act_del_cat = menu.addAction(get_system_icon("edit-delete", "🗑️"), f"Quitar todo este grupo ({item.childCount()} elementos) del ADN...")
                        action = menu.exec(tree_hist.viewport().mapToGlobal(pos))
                        if action == act_del_cat:
                            self.remove_multiple_from_dna([item])
            else:
                act_unlink_batch = menu.addAction(get_system_icon("edit-delete", "🗑️"), f"Quitar los {len(selected_items)} Elementos Seleccionados del ADN...")
                menu.addSeparator()
                act_copy_batch = menu.addAction(get_system_icon("edit-copy", "📋"), "Copiar Rutas / Textos Seleccionados")

                action = menu.exec(tree_hist.viewport().mapToGlobal(pos))
                if action == act_unlink_batch:
                    self.remove_multiple_from_dna(selected_items)
                elif action == act_copy_batch:
                    texts = []
                    for it in selected_items:
                        meta = it.data(0, QtCore.Qt.ItemDataRole.UserRole)
                        if isinstance(meta, dict) and meta.get("path"):
                            texts.append(meta["path"])
                        elif isinstance(meta, dict) and meta.get("line"):
                            texts.append(meta["line"])
                        else:
                            texts.append(it.text(0))
                    QtWidgets.QApplication.clipboard().setText("\n".join(texts))
                    QtWidgets.QMessageBox.information(self, "Copiado", f"Se copiaron {len(texts)} elementos al portapapeles.")

        tree_hist.customContextMenuRequested.connect(show_hist_tree_menu)
        th_layout.addWidget(tree_hist)

        # Bottom actions for Tab 3
        hist_actions = QtWidgets.QHBoxLayout()
        lbl_hist_info = QtWidgets.QLabel(f"Total: {len(receipts)} sesión(es) de captura registradas.")
        lbl_hist_info.setObjectName("SecondaryAccentLabel")
        hist_actions.addWidget(lbl_hist_info)
        hist_actions.addStretch()

        btn_open_hist_folder = QtWidgets.QPushButton("Abrir Carpeta")
        btn_open_hist_folder.setIcon(get_system_icon("folder-open", "📂"))
        def open_selected_hist_file():
            item = tree_hist.currentItem()
            if item:
                meta = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
                if isinstance(meta, dict) and meta.get("type") == "file":
                    fp = meta.get("path")
                    if fp and os.path.exists(fp):
                        target = fp if os.path.isdir(fp) else os.path.dirname(fp)
                        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(target))
                        return
            tracker_dir = os.path.join(self.wine_prefix, ".vst_tracker")
            if os.path.exists(tracker_dir):
                QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(tracker_dir))
        btn_open_hist_folder.clicked.connect(open_selected_hist_file)

        btn_unlink_hist_item = QtWidgets.QPushButton("Quitar Seleccionados del ADN")
        btn_unlink_hist_item.setIcon(get_system_icon("edit-delete", "🗑️"))
        btn_unlink_hist_item.setToolTip("Quita los elementos seleccionados (recibos completos, archivos o registros) del ADN del plugin.")
        def unlink_selected_tree_items():
            selected_items = tree_hist.selectedItems()
            if not selected_items:
                QtWidgets.QMessageBox.information(self, "Seleccionar", "Selecciona uno o más elementos en el árbol (con Ctrl o Shift) para desvincularlos del ADN.")
                return
            self.remove_multiple_from_dna(selected_items)

        btn_unlink_hist_item.clicked.connect(unlink_selected_tree_items)

        btn_copy_hist_item = QtWidgets.QPushButton("Copiar Texto / Rutas")
        btn_copy_hist_item.setIcon(get_system_icon("edit-copy", "📋"))
        def copy_selected_hist_text():
            selected_items = tree_hist.selectedItems()
            if not selected_items:
                return
            texts = []
            for it in selected_items:
                meta = it.data(0, QtCore.Qt.ItemDataRole.UserRole)
                if isinstance(meta, dict) and meta.get("path"):
                    texts.append(meta["path"])
                elif isinstance(meta, dict) and meta.get("line"):
                    texts.append(meta["line"])
                else:
                    texts.append(it.text(0))
            QtWidgets.QApplication.clipboard().setText("\n".join(texts))
            QtWidgets.QMessageBox.information(self, "Copiado", f"Copiado al portapapeles:\n" + "\n".join(texts[:10]) + (f"\n... y {len(texts)-10} más" if len(texts) > 10 else ""))
        btn_copy_hist_item.clicked.connect(copy_selected_hist_text)

        hist_actions.addWidget(btn_open_hist_folder)
        hist_actions.addWidget(btn_copy_hist_item)
        hist_actions.addWidget(btn_unlink_hist_item)
        th_layout.addLayout(hist_actions)

        tabs.addTab(tab_hist, get_system_icon("history", "📜"), f"Historial ADN ({len(receipts)})")

        self.main_layout.addWidget(tabs)

        # Footer actions
        bottom_h = QtWidgets.QHBoxLayout()
        btn_export = QtWidgets.QPushButton("Exportar a .vstpack")
        btn_export.setIcon(get_system_icon("document-save", "📦"))
        btn_export.clicked.connect(self.export_from_dialog)

        btn_close = QtWidgets.QPushButton("Cerrar")
        btn_close.clicked.connect(self.accept)

        bottom_h.addStretch()
        bottom_h.addWidget(btn_export)
        bottom_h.addWidget(btn_close)
        self.main_layout.addLayout(bottom_h)

    def export_from_dialog(self):
        if self.parent() and hasattr(self.parent(), "export_selected_product"):
            self.accept()
            self.parent().export_selected_product()

    def init_from_vstpack(self):
        data = inspect_vstpack_data(self.vstpack_path)
        manifest = data["manifest"]
        files = data["files"]
        reg_content = data["reg_content"]
        prods = manifest.get("products", [])

        # Header card
        header_card = QtWidgets.QFrame()
        header_card.setObjectName("HeaderFrame")
        h_layout = QtWidgets.QHBoxLayout(header_card)
        h_layout.setContentsMargins(14, 12, 14, 12)

        v_title = QtWidgets.QVBoxLayout()
        pkg_name = os.path.basename(self.vstpack_path)
        lbl_name = QtWidgets.QLabel(f"<h2>Paquete: {pkg_name}</h2>")
        lbl_name.setTextFormat(QtCore.Qt.TextFormat.RichText)

        prods_names = ", ".join([p.get("name", "Plugin") for p in prods]) if prods else "Plugins VST"
        created_at = manifest.get("created_at", "N/A")
        total_sz = format_file_size(data["total_size"])
        pkg_sz = format_file_size(os.path.getsize(self.vstpack_path)) if os.path.exists(self.vstpack_path) else "N/A"

        meta_lbl = QtWidgets.QLabel(
            f"<b>Contenido:</b> {prods_names}  |  "
            f"<b>Tamaño Comprimido:</b> {pkg_sz}  |  "
            f"<b>Descomprimido:</b> {total_sz}  |  "
            f"<b>Creado:</b> {created_at}"
        )
        meta_lbl.setTextFormat(QtCore.Qt.TextFormat.RichText)
        v_title.addWidget(lbl_name)
        v_title.addWidget(meta_lbl)
        h_layout.addLayout(v_title)
        h_layout.addStretch()

        self.main_layout.addWidget(header_card)

        # Tab Widget
        tabs = QtWidgets.QTabWidget()

        # Tab 1: Files
        tab_files = QtWidgets.QWidget()
        tf_layout = QtWidgets.QVBoxLayout(tab_files)
        tf_layout.setContentsMargins(6, 8, 6, 6)

        search_h = QtWidgets.QHBoxLayout()
        txt_search = QtWidgets.QLineEdit()
        txt_search.setPlaceholderText("🔍 Filtrar archivos o presets dentro del paquete...")
        txt_search.setClearButtonEnabled(True)
        search_h.addWidget(txt_search)
        tf_layout.addLayout(search_h)

        table_files = QtWidgets.QTableWidget()
        table_files.setColumnCount(3)
        table_files.setHorizontalHeaderLabels(["Ruta de Destino en Paquete", "Tamaño Descomprimido", "Tamaño Comprimido"])
        table_files.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        table_files.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        table_files.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        table_files.setAlternatingRowColors(True)

        table_files.setRowCount(len(files))
        for r_idx, f_info in enumerate(files):
            item_p = QtWidgets.QTableWidgetItem(f_info["path"])
            item_sz = QtWidgets.QTableWidgetItem(format_file_size(f_info["size"]))
            item_sz.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
            item_csz = QtWidgets.QTableWidgetItem(format_file_size(f_info["compressed_size"]))
            item_csz.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)

            table_files.setItem(r_idx, 0, item_p)
            table_files.setItem(r_idx, 1, item_sz)
            table_files.setItem(r_idx, 2, item_csz)

        def filter_pkg_files(text):
            query = text.lower()
            for row in range(table_files.rowCount()):
                it = table_files.item(row, 0)
                table_files.setRowHidden(row, not (it and query in it.text().lower()))

        txt_search.textChanged.connect(filter_pkg_files)
        tf_layout.addWidget(table_files)
        tabs.addTab(tab_files, get_system_icon("folder", "📁"), f"Archivos a Instalar ({len(files)})")

        # Tab 2: Registry
        tab_reg = QtWidgets.QWidget()
        tr_layout = QtWidgets.QVBoxLayout(tab_reg)
        tr_layout.setContentsMargins(6, 8, 6, 6)

        txt_reg = QtWidgets.QPlainTextEdit()
        txt_reg.setReadOnly(True)
        txt_reg.setPlainText(reg_content if reg_content else "; No hay entradas de registro en este paquete.")
        tr_layout.addWidget(txt_reg)
        tabs.addTab(tab_reg, get_system_icon("preferences-system", "🔑"), "Registro a Fusionar")

        # Tab 3: Manifest JSON
        tab_man = QtWidgets.QWidget()
        tm_layout = QtWidgets.QVBoxLayout(tab_man)
        tm_layout.setContentsMargins(6, 8, 6, 6)

        txt_man = QtWidgets.QPlainTextEdit()
        txt_man.setReadOnly(True)
        txt_man.setPlainText(json.dumps(manifest, indent=2))
        tm_layout.addWidget(txt_man)
        tabs.addTab(tab_man, get_system_icon("text-x-generic", "📄"), "Manifiesto JSON")

        self.main_layout.addWidget(tabs)

        # Footer
        bottom_h = QtWidgets.QHBoxLayout()
        btn_install = QtWidgets.QPushButton("Instalar este Paquete")
        btn_install.setIcon(get_system_icon("system-software-install", "📥"))
        btn_install.clicked.connect(self.install_from_dialog)

        btn_close = QtWidgets.QPushButton("Cerrar")
        btn_close.clicked.connect(self.accept)

        bottom_h.addStretch()
        bottom_h.addWidget(btn_install)
        bottom_h.addWidget(btn_close)
        self.main_layout.addLayout(bottom_h)

    def install_from_dialog(self):
        if self.parent() and hasattr(self.parent(), "add_files"):
            self.accept()
            self.parent().add_files([self.vstpack_path])
            self.parent().tabs.setCurrentIndex(1)


class ManualCaptureReviewDialog(QtWidgets.QDialog):
    """Displays exact files and registry lines captured during manual capture mode with checkbox selection."""
    def __init__(self, parent=None, diff=None, products=None, wine_prefix=""):
        super().__init__(parent)
        self.diff = diff or {"new_files": [], "new_reg_lines": []}
        self.products = products or []
        self.wine_prefix = wine_prefix
        self.selected_target = None
        self.filtered_diff = {"new_files": [], "new_reg_lines": []}
        self.setMinimumSize(880, 620)
        self.resize(960, 680)
        self.setWindowTitle("Revisión y Selección de Cambios Capturados - ADN de Plugin")
        self.setup_ui()

    def setup_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        num_files = len(self.diff.get("new_files", []))
        num_regs = len(self.diff.get("new_reg_lines", []))

        # Header card
        header_card = QtWidgets.QFrame()
        header_card.setObjectName("HeaderFrame")
        h_layout = QtWidgets.QHBoxLayout(header_card)
        h_layout.setContentsMargins(14, 12, 14, 12)

        v_title = QtWidgets.QVBoxLayout()
        lbl_title = QtWidgets.QLabel("<h2>🔴 Revisión de Cambios Capturados</h2>")
        lbl_title.setTextFormat(QtCore.Qt.TextFormat.RichText)

        meta_lbl = QtWidgets.QLabel(
            f"Se atraparon <b>{num_files} archivo(s) nuevos/modificados</b> y <b>{num_regs} línea(s) de registro</b>.<br>"
            "<b>Marca o desmarca</b> los elementos que deseas incluir en el ADN del plugin antes de guardar."
        )
        meta_lbl.setTextFormat(QtCore.Qt.TextFormat.RichText)
        v_title.addWidget(lbl_title)
        v_title.addWidget(meta_lbl)
        h_layout.addLayout(v_title)
        h_layout.addStretch()

        layout.addWidget(header_card)

        # Tabs
        tabs = QtWidgets.QTabWidget()

        # Tab 1: Files with Checkboxes
        tab_files = QtWidgets.QWidget()
        tf_layout = QtWidgets.QVBoxLayout(tab_files)
        tf_layout.setContentsMargins(6, 8, 6, 6)

        # Search and batch selection bar
        sel_bar = QtWidgets.QHBoxLayout()
        txt_search = QtWidgets.QLineEdit()
        txt_search.setPlaceholderText("🔍 Filtrar archivos capturados...")
        txt_search.setClearButtonEnabled(True)

        btn_select_all = QtWidgets.QPushButton("☑️ Marcar Todos")
        btn_deselect_all = QtWidgets.QPushButton("⬜ Desmarcar Todos")
        btn_invert = QtWidgets.QPushButton("🔄 Invertir")

        sel_bar.addWidget(txt_search)
        sel_bar.addWidget(btn_select_all)
        sel_bar.addWidget(btn_deselect_all)
        sel_bar.addWidget(btn_invert)
        tf_layout.addLayout(sel_bar)

        self.table_files = QtWidgets.QTableWidget()
        self.table_files.setColumnCount(3)
        self.table_files.setHorizontalHeaderLabels(["[☑] Ruta del Archivo Capturado", "Ubicación / Tipo", "Tamaño"])
        self.table_files.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.table_files.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.table_files.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.table_files.setAlternatingRowColors(True)

        raw_files = self.diff.get("new_files", [])
        self.table_files.setRowCount(len(raw_files))
        for r_idx, fp in enumerate(raw_files):
            norm = fp.replace("\\", "/").lower()
            if "/documents/" in norm or "/documentos/" in norm:
                cat = "Presets / Librería (Documentos)"
            elif "/appdata/" in norm:
                cat = "Configuración / Licencia (AppData)"
            elif "/program files/" in norm or "/common files/" in norm:
                cat = "Binario de Plugin / Soporte"
            elif "/programdata/" in norm:
                cat = "Datos Compartidos (ProgramData)"
            else:
                cat = "Otro Archivo Modificado"

            try:
                sz = format_file_size(os.path.getsize(fp)) if os.path.exists(fp) and not os.path.isdir(fp) else "N/A"
            except Exception:
                sz = "N/A"

            it_p = QtWidgets.QTableWidgetItem(fp)
            it_p.setFlags(QtCore.Qt.ItemFlag.ItemIsUserCheckable | QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsSelectable)
            it_p.setCheckState(QtCore.Qt.CheckState.Checked)
            it_p.setData(QtCore.Qt.ItemDataRole.UserRole, fp)

            it_c = QtWidgets.QTableWidgetItem(cat)
            it_s = QtWidgets.QTableWidgetItem(sz)
            it_s.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)

            self.table_files.setItem(r_idx, 0, it_p)
            self.table_files.setItem(r_idx, 1, it_c)
            self.table_files.setItem(r_idx, 2, it_s)

        def set_all_files(state):
            for row in range(self.table_files.rowCount()):
                if not self.table_files.isRowHidden(row):
                    it = self.table_files.item(row, 0)
                    if it:
                        it.setCheckState(state)
            update_file_count_lbl()

        def invert_files():
            for row in range(self.table_files.rowCount()):
                if not self.table_files.isRowHidden(row):
                    it = self.table_files.item(row, 0)
                    if it:
                        new_state = QtCore.Qt.CheckState.Unchecked if it.checkState() == QtCore.Qt.CheckState.Checked else QtCore.Qt.CheckState.Checked
                        it.setCheckState(new_state)
            update_file_count_lbl()

        btn_select_all.clicked.connect(lambda: set_all_files(QtCore.Qt.CheckState.Checked))
        btn_deselect_all.clicked.connect(lambda: set_all_files(QtCore.Qt.CheckState.Unchecked))
        btn_invert.clicked.connect(invert_files)

        def filter_captured_files(text):
            query = text.lower()
            for row in range(self.table_files.rowCount()):
                it = self.table_files.item(row, 0)
                self.table_files.setRowHidden(row, not (it and query in it.text().lower()))

        txt_search.textChanged.connect(filter_captured_files)
        tf_layout.addWidget(self.table_files)

        # File actions
        f_actions = QtWidgets.QHBoxLayout()
        self.lbl_files_cnt = QtWidgets.QLabel(f"Seleccionados: {len(raw_files)} de {len(raw_files)} archivos.")
        self.lbl_files_cnt.setObjectName("SecondaryAccentLabel")
        f_actions.addWidget(self.lbl_files_cnt)
        f_actions.addStretch()

        def update_file_count_lbl():
            chk = 0
            for r in range(self.table_files.rowCount()):
                it = self.table_files.item(r, 0)
                if it and it.checkState() == QtCore.Qt.CheckState.Checked:
                    chk += 1
            self.lbl_files_cnt.setText(f"Seleccionados: {chk} de {self.table_files.rowCount()} archivos.")

        self.table_files.itemChanged.connect(lambda it: update_file_count_lbl() if it.column() == 0 else None)

        btn_open = QtWidgets.QPushButton("Abrir Carpeta")
        btn_open.setIcon(get_system_icon("folder-open", "📂"))
        def open_cap_folder():
            rows = self.table_files.selectionModel().selectedRows()
            if rows:
                p = self.table_files.item(rows[0].row(), 0).text()
                target = p if os.path.isdir(p) else os.path.dirname(p)
                if os.path.exists(target):
                    QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(target))
        btn_open.clicked.connect(open_cap_folder)

        btn_copy = QtWidgets.QPushButton("Copiar Rutas")
        btn_copy.setIcon(get_system_icon("edit-copy", "📋"))
        btn_copy.clicked.connect(lambda: (QtWidgets.QApplication.clipboard().setText("\n".join(raw_files)), QtWidgets.QMessageBox.information(self, "Copiado", f"Se copiaron {len(raw_files)} rutas al portapapeles.")))

        f_actions.addWidget(btn_open)
        f_actions.addWidget(btn_copy)
        tf_layout.addLayout(f_actions)

        tabs.addTab(tab_files, get_system_icon("folder", "📁"), f"Archivos ({len(raw_files)})")

        # Tab 2: Registry with Checkboxes
        tab_reg = QtWidgets.QWidget()
        tr_layout = QtWidgets.QVBoxLayout(tab_reg)
        tr_layout.setContentsMargins(6, 8, 6, 6)

        reg_sel_bar = QtWidgets.QHBoxLayout()
        txt_reg_search = QtWidgets.QLineEdit()
        txt_reg_search.setPlaceholderText("🔍 Filtrar entradas de registro...")
        txt_reg_search.setClearButtonEnabled(True)

        btn_select_all_r = QtWidgets.QPushButton("☑️ Marcar Todos")
        btn_deselect_all_r = QtWidgets.QPushButton("⬜ Desmarcar Todos")
        btn_invert_r = QtWidgets.QPushButton("🔄 Invertir")

        reg_sel_bar.addWidget(txt_reg_search)
        reg_sel_bar.addWidget(btn_select_all_r)
        reg_sel_bar.addWidget(btn_deselect_all_r)
        reg_sel_bar.addWidget(btn_invert_r)
        tr_layout.addLayout(reg_sel_bar)

        self.table_reg = QtWidgets.QTableWidget()
        self.table_reg.setColumnCount(2)
        self.table_reg.setHorizontalHeaderLabels(["[☑] Entrada de Registro Capturada", "Tipo"])
        self.table_reg.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.table_reg.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.table_reg.setAlternatingRowColors(True)

        raw_reg = self.diff.get("new_reg_lines", [])
        self.table_reg.setRowCount(len(raw_reg))
        for r_idx, r_line in enumerate(raw_reg):
            line_str = r_line.strip()
            r_type = "Sección de Clave" if line_str.startswith("[") and line_str.endswith("]") else "Valor de Registro"
            it_r = QtWidgets.QTableWidgetItem(line_str)
            it_r.setFlags(QtCore.Qt.ItemFlag.ItemIsUserCheckable | QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsSelectable)
            it_r.setCheckState(QtCore.Qt.CheckState.Checked)
            it_r.setData(QtCore.Qt.ItemDataRole.UserRole, r_line)

            it_t = QtWidgets.QTableWidgetItem(r_type)
            self.table_reg.setItem(r_idx, 0, it_r)
            self.table_reg.setItem(r_idx, 1, it_t)

        def set_all_reg(state):
            for row in range(self.table_reg.rowCount()):
                if not self.table_reg.isRowHidden(row):
                    it = self.table_reg.item(row, 0)
                    if it:
                        it.setCheckState(state)
            update_reg_count_lbl()

        def invert_reg():
            for row in range(self.table_reg.rowCount()):
                if not self.table_reg.isRowHidden(row):
                    it = self.table_reg.item(row, 0)
                    if it:
                        new_state = QtCore.Qt.CheckState.Unchecked if it.checkState() == QtCore.Qt.CheckState.Checked else QtCore.Qt.CheckState.Checked
                        it.setCheckState(new_state)
            update_reg_count_lbl()

        btn_select_all_r.clicked.connect(lambda: set_all_reg(QtCore.Qt.CheckState.Checked))
        btn_deselect_all_r.clicked.connect(lambda: set_all_reg(QtCore.Qt.CheckState.Unchecked))
        btn_invert_r.clicked.connect(invert_reg)

        def filter_captured_reg(text):
            query = text.lower()
            for row in range(self.table_reg.rowCount()):
                it = self.table_reg.item(row, 0)
                self.table_reg.setRowHidden(row, not (it and query in it.text().lower()))

        txt_reg_search.textChanged.connect(filter_captured_reg)
        tr_layout.addWidget(self.table_reg)

        r_actions = QtWidgets.QHBoxLayout()
        self.lbl_reg_cnt = QtWidgets.QLabel(f"Seleccionadas: {len(raw_reg)} de {len(raw_reg)} líneas.")
        self.lbl_reg_cnt.setObjectName("SecondaryAccentLabel")
        r_actions.addWidget(self.lbl_reg_cnt)
        r_actions.addStretch()

        def update_reg_count_lbl():
            chk = 0
            for r in range(self.table_reg.rowCount()):
                it = self.table_reg.item(r, 0)
                if it and it.checkState() == QtCore.Qt.CheckState.Checked:
                    chk += 1
            self.lbl_reg_cnt.setText(f"Seleccionadas: {chk} de {self.table_reg.rowCount()} líneas.")

        self.table_reg.itemChanged.connect(lambda it: update_reg_count_lbl() if it.column() == 0 else None)

        btn_copy_r = QtWidgets.QPushButton("Copiar Registro")
        btn_copy_r.setIcon(get_system_icon("edit-copy", "📋"))
        btn_copy_r.clicked.connect(lambda: (QtWidgets.QApplication.clipboard().setText("\n".join(raw_reg)), QtWidgets.QMessageBox.information(self, "Copiado", "Registro copiado al portapapeles.")))
        r_actions.addWidget(btn_copy_r)
        tr_layout.addLayout(r_actions)

        tabs.addTab(tab_reg, get_system_icon("preferences-system", "🔑"), f"Registro ({len(raw_reg)})")

        layout.addWidget(tabs)

        # Assignment card
        assign_card = QtWidgets.QFrame()
        assign_card.setObjectName("CardFrame")
        a_layout = QtWidgets.QHBoxLayout(assign_card)
        a_layout.setContentsMargins(12, 10, 12, 10)

        lbl_assign = QtWidgets.QLabel("<b>Vincular cambios al plugin:</b>")
        self.cmb_target = QtWidgets.QComboBox()
        self.cmb_target.setEditable(True)
        self.cmb_target.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)

        for p in self.products:
            self.cmb_target.addItem(p.get("name", "Plugin"))

        a_layout.addWidget(lbl_assign)
        a_layout.addWidget(self.cmb_target)
        layout.addWidget(assign_card)

        # Footer buttons
        bottom_h = QtWidgets.QHBoxLayout()
        btn_save = QtWidgets.QPushButton("✓ Asignar e Inyectar en ADN del Plugin")
        btn_save.setIcon(get_system_icon("dialog-ok-apply", "✓"))
        btn_save.setFixedHeight(34)
        btn_save.clicked.connect(self.on_accept_assignment)

        btn_cancel = QtWidgets.QPushButton("Descartar Captura")
        btn_cancel.setIcon(get_system_icon("dialog-cancel", "❌"))
        btn_cancel.setFixedHeight(34)
        btn_cancel.clicked.connect(self.reject)

        bottom_h.addStretch()
        bottom_h.addWidget(btn_save)
        bottom_h.addWidget(btn_cancel)
        layout.addLayout(bottom_h)

    def on_accept_assignment(self):
        target = self.cmb_target.currentText().strip()
        if not target:
            QtWidgets.QMessageBox.warning(self, "Nombre Requerido", "Por favor ingresa o selecciona el nombre del plugin al que deseas asignar estos cambios.")
            return

        checked_files = []
        for r in range(self.table_files.rowCount()):
            it = self.table_files.item(r, 0)
            if it and it.checkState() == QtCore.Qt.CheckState.Checked:
                fp = it.data(QtCore.Qt.ItemDataRole.UserRole)
                if fp:
                    checked_files.append(fp)

        checked_reg = []
        for r in range(self.table_reg.rowCount()):
            it = self.table_reg.item(r, 0)
            if it and it.checkState() == QtCore.Qt.CheckState.Checked:
                r_line = it.data(QtCore.Qt.ItemDataRole.UserRole)
                if r_line:
                    checked_reg.append(r_line)

        if not checked_files and not checked_reg:
            QtWidgets.QMessageBox.warning(self, "Sin Selección", "No has marcado ningún archivo ni entrada de registro para asignar. Marca al menos un elemento o presiona 'Descartar Captura'.")
            return

        self.selected_target = target
        self.filtered_diff = {
            "new_files": checked_files,
            "new_reg_lines": checked_reg
        }
        self.accept()


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
        self.manual_snapshot = None
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
        self.prod_table.setColumnCount(7)
        self.prod_table.setHorizontalHeaderLabels(["Producto / Plugin", "Fabricante", "Formatos", "Historial / Tracking", "Standalone", "Versión", "Tamaño Total"])
        self.prod_table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.prod_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.prod_table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.prod_table.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.prod_table.horizontalHeader().setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.prod_table.horizontalHeader().setSectionResizeMode(5, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.prod_table.horizontalHeader().setSectionResizeMode(6, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.prod_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.prod_table.setAlternatingRowColors(True)
        self.prod_table.cellDoubleClicked.connect(lambda row, col: self.view_selected_product_details())
        self.prod_table.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.prod_table.customContextMenuRequested.connect(self.show_prod_context_menu)
        layout.addWidget(self.prod_table)

        # Bottom Actions Bar
        bottom_bar = QtWidgets.QHBoxLayout()
        self.lbl_prod_count = QtWidgets.QLabel("0 plugins detectados.")

        self.btn_view_details = QtWidgets.QPushButton("🔍 Ver Archivos / Historial")
        self.btn_view_details.setToolTip("Inspecciona todos los archivos, presets, carpetas en Documentos, registro y recibos del plugin seleccionado.")
        self.btn_view_details.setIcon(get_system_icon("document-properties", "🔍"))
        self.btn_view_details.clicked.connect(self.view_selected_product_details)

        self.btn_install_pack = QtWidgets.QPushButton("📦 Instalar Pack (.vstpack)")
        self.btn_install_pack.setToolTip("Seleccionar e instalar uno o varios paquetes .vstpack en el entorno actual.")
        self.btn_install_pack.setIcon(get_system_icon("archive-extract", "📦"))
        self.btn_install_pack.clicked.connect(self.install_pack_action)

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
        bottom_bar.addWidget(self.btn_view_details)
        bottom_bar.addWidget(self.btn_install_pack)
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

        # Card 3: Inspect
        card_inspect = QtWidgets.QFrame()
        card_inspect.setObjectName("CardFrame")
        ins_v = QtWidgets.QVBoxLayout(card_inspect)

        ins_title = QtWidgets.QLabel("<b>3. Inspeccionar Paquete</b>")
        ins_desc = QtWidgets.QLabel("Examina el contenido interno de cualquier <code>.vstpack</code>: consulta su manifiesto, binarios, presets y claves de registro sin instalarlo.")
        ins_desc.setWordWrap(True)

        btn_inspect = QtWidgets.QPushButton("Inspeccionar Archivo .vstpack")
        btn_inspect.setIcon(get_system_icon("document-properties", "🔍"))
        btn_inspect.setFixedHeight(36)
        btn_inspect.clicked.connect(self.inspect_vstpack_file_action)

        ins_v.addWidget(ins_title)
        ins_v.addWidget(ins_desc)
        ins_v.addStretch()
        ins_v.addWidget(btn_inspect)
        cards_h.addWidget(card_inspect)

        layout.addLayout(cards_h)

        self.export_status_lbl = QtWidgets.QLabel("")
        self.export_status_lbl.setObjectName("AccentLabel")
        layout.addWidget(self.export_status_lbl)
        layout.addStretch()

    # ----------------------------------------------------
    # SCANNING & PRODUCT AGGREGATION
    # ----------------------------------------------------

    def view_selected_product_details(self):
        row = self.prod_table.currentRow()
        if row < 0 or row >= len(self.installed_products):
            QtWidgets.QMessageBox.information(self, "Seleccionar Plugin", "Por favor selecciona un plugin de la tabla para ver sus detalles y ADN.")
            return

        p = self.installed_products[row]
        dlg = ProductDetailsDialog(
            parent=self,
            product=p,
            wine_prefix=self.wine_prefix,
            wine_root=self.wine_root,
            is_windows=self.is_windows
        )
        dlg.exec()

    def inspect_vstpack_file_action(self):
        vstpack_file, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Seleccionar Paquete VSTPack para Inspeccionar",
            os.path.expanduser("~"),
            "Paquetes VSTPack (*.vstpack *.zip);;Todos los archivos (*)"
        )
        if vstpack_file and os.path.isfile(vstpack_file):
            dlg = ProductDetailsDialog(
                parent=self,
                vstpack_path=vstpack_file,
                wine_prefix=self.wine_prefix,
                wine_root=self.wine_root,
                is_windows=self.is_windows
            )
            dlg.exec()

    def show_prod_context_menu(self, pos):
        item = self.prod_table.itemAt(pos)
        if not item:
            return
        row = item.row()
        self.prod_table.selectRow(row)
        if row < 0 or row >= len(self.installed_products):
            return
        p = self.installed_products[row]

        menu = QtWidgets.QMenu(self)
        act_details = menu.addAction(get_system_icon("document-properties", "🔍"), "Ver Detalles / Archivos Instalados")
        act_export = menu.addAction(get_system_icon("document-save", "📦"), f"Exportar '{p['name']}' a .vstpack")
        menu.addSeparator()
        act_folder = menu.addAction(get_system_icon("folder-open", "📂"), "Abrir Carpeta Contenedora")
        act_delete = menu.addAction(get_system_icon("edit-delete", "🗑️"), "Eliminar Plugin...")

        action = menu.exec(self.prod_table.viewport().mapToGlobal(pos))
        if action == act_details:
            self.view_selected_product_details()
        elif action == act_export:
            self.export_selected_product()
        elif action == act_folder:
            if p.get("files"):
                f = p["files"][0]
                target = f if os.path.isdir(f) else os.path.dirname(f)
                if os.path.exists(target):
                    self.open_folder_in_fm(target)
        elif action == act_delete:
            self.delete_selected_product()

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
        if not hasattr(self, "manual_snapshot") or self.manual_snapshot is None:
            self.btn_start_capture.setVisible(True)
            self.btn_stop_capture.setVisible(False)
            self.lbl_prod_count.setText("No hay captura en curso.")
            self.lbl_prod_count.setStyleSheet("")
            QtWidgets.QMessageBox.warning(self, "Sin Captura Activa", "No se encontró una captura iniciada. Presiona '🔴 Capturar Cambios Manuales' antes de finalizar.")
            return

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

        review_dlg = ManualCaptureReviewDialog(
            parent=self,
            diff=diff,
            products=self.installed_products,
            wine_prefix=self.wine_prefix
        )

        if review_dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            item = review_dlg.selected_target
            diff_to_save = getattr(review_dlg, "filtered_diff", diff)
            num_saved_files = len(diff_to_save.get("new_files", []))
            num_saved_regs = len(diff_to_save.get("new_reg_lines", []))

            if item:
                tracker_dir = os.path.join(self.wine_prefix, ".vst_tracker")
                os.makedirs(tracker_dir, exist_ok=True)
                stem = re.sub(r'[^a-zA-Z0-9_]', '_', item)
                receipt_path = os.path.join(tracker_dir, f"{stem}_manual_patch_{int(time.time())}.json")
                receipt_data = {
                    "installer": "Manual Patch/Crack/License",
                    "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "new_files": diff_to_save.get("new_files", []),
                    "reg_lines_count": num_saved_regs,
                    "reg_diff": diff_to_save.get("new_reg_lines", [])
                }
                with open(receipt_path, "w", encoding="utf-8") as rpf:
                    json.dump(receipt_data, rpf, indent=2)

                QtWidgets.QMessageBox.information(
                    self, 
                    "¡ADN Inyectado con Éxito!", 
                    f"Los {num_saved_files} archivo(s) seleccionados y {num_saved_regs} línea(s) de registro fueron inyectados en el ADN de '{item}'.\n\n"
                    "Al exportar a .vstpack, tus parches, librerías y licencias irán incluidos automáticamente."
                )

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
            try:
                det_info = get_product_detailed_info(v, self.wine_prefix)
                v["size"] = det_info["total_size"]
                v["total_files_count"] = len(det_info["files"])
                v["receipts_count"] = len(det_info["receipts"])
                v["is_tracked"] = det_info["is_tracked"]
            except Exception:
                v["total_files_count"] = len(v.get("files", []))
                v["receipts_count"] = 0
                v["is_tracked"] = is_product_tracked(v, self.wine_prefix)
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
            name_item.setData(QtCore.Qt.ItemDataRole.UserRole, p)
            name_item.setFont(QtGui.QFont(self.font().family(), 10, QtGui.QFont.Weight.Bold))

            vendor_item = QtWidgets.QTableWidgetItem(p["vendor"])
            vendor_item.setForeground(accent_color)

            fmt_str = "  ".join([f"[{f}]" for f in p["formats"]])
            fmt_item = QtWidgets.QTableWidgetItem(fmt_str)
            fmt_item.setFont(QtGui.QFont(self.font().family(), 9, QtGui.QFont.Weight.Bold))

            tracked = p.get("is_tracked", is_product_tracked(p, self.wine_prefix))
            rc_cnt = p.get("receipts_count", 0)
            tf_cnt = p.get("total_files_count", len(p.get("files", [])))

            if tracked:
                track_text = f"✓ Rastreado ({rc_cnt} ADN)" if rc_cnt else "✓ Rastreado (Listo)"
                track_item = QtWidgets.QTableWidgetItem(track_text)
                track_item.setForeground(QtGui.QColor("#16a34a"))
                track_item.setFont(QtGui.QFont(self.font().family(), 9, QtGui.QFont.Weight.Bold))
                track_item.setToolTip(f"Instalación registrada por Instrumentarium.\n• {rc_cnt} sesión(es) de captura / recibo(s)\n• {tf_cnt:,} archivos asociados (binarios, presets, AppData)")
            else:
                track_item = QtWidgets.QTableWidgetItem("⚠️ No Rastreado")
                track_item.setForeground(QtGui.QColor("#eab308"))
                track_item.setToolTip("Plugin detectado sin historial de instalación. Para exportar a .vstpack, realiza una 'Captura Manual'.")

            if p["standalone"]:
                standalone_widget = QtWidgets.QWidget()
                s_layout = QtWidgets.QHBoxLayout(standalone_widget)
                s_layout.setContentsMargins(2, 2, 2, 2)
                btn_launch = QtWidgets.QPushButton(f"▶ {os.path.basename(p['standalone'])}")
                btn_launch.setIcon(get_system_icon("media-playback-start", "▶"))
                btn_launch.clicked.connect(lambda _, exe=p["standalone"]: self.launch_standalone(exe))
                s_layout.addWidget(btn_launch)
                self.prod_table.setCellWidget(row, 4, standalone_widget)
            else:
                self.prod_table.setItem(row, 4, QtWidgets.QTableWidgetItem("-"))

            ver_item = QtWidgets.QTableWidgetItem(p["version"])
            size_str = format_file_size(p["size"])
            size_item = QtWidgets.QTableWidgetItem(size_str)
            size_item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
            size_item.setToolTip(f"Tamaño total del ecosistema: {size_str}\nIncluye binarios VST, librerías/presets en Documentos y datos en AppData ({tf_cnt:,} archivos en total).")

            self.prod_table.setItem(row, 0, name_item)
            self.prod_table.setItem(row, 1, vendor_item)
            self.prod_table.setItem(row, 2, fmt_item)
            self.prod_table.setItem(row, 3, track_item)
            self.prod_table.setItem(row, 5, ver_item)
            self.prod_table.setItem(row, 6, size_item)

        total_files_all = sum(p.get('total_files_count', len(p.get('files', []))) for p in product_list)
        self.lbl_prod_count.setText(f"{len(product_list)} plugin(s) detectados ({total_files_all:,} archivos registrados en el ecosistema).")

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

    def _get_selected_product(self):
        row = self.prod_table.currentRow()
        if row < 0:
            return None
        item = self.prod_table.item(row, 0)
        if not item:
            return None
        p = item.data(QtCore.Qt.ItemDataRole.UserRole)
        if isinstance(p, dict):
            return p
        name = item.text()
        for prod in self.installed_products:
            if prod.get("name") == name:
                return prod
        return None

    def launch_standalone(self, exe_path):
        if getattr(self, "is_windows", False):
            subprocess.Popen([exe_path], cwd=os.path.dirname(exe_path))
        else:
            wine_bin = os.path.join(self.wine_root, "bin", "wine")
            env = os.environ.copy()
            env["WINEPREFIX"] = self.wine_prefix
            env["WINEDEBUG"] = "-all"
            subprocess.Popen([wine_bin, exe_path], env=env, cwd=os.path.dirname(exe_path))

    def delete_selected_product(self):
        p = self._get_selected_product()
        if not p:
            QtWidgets.QMessageBox.information(self, "Seleccionar", "Selecciona un producto de la lista para eliminar.")
            return

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
            p_name_clean = re.sub(r'[^a-zA-Z0-9_]', '_', p["name"]).lower()
            if os.path.isdir(tracker_dir):
                for rf in os.listdir(tracker_dir):
                    if rf.endswith(".json"):
                        rpath = os.path.join(tracker_dir, rf)
                        try:
                            with open(rpath, "r", encoding="utf-8") as rf_fd:
                                rdata = json.load(rf_fd)
                                r_files = rdata.get("new_files", [])
                                rf_stem = os.path.splitext(rf)[0].lower()
                                name_match = (rf_stem == p_name_clean or rf_stem.startswith(f"{p_name_clean}_"))
                                match = any(f_ in files_to_delete for f_ in r_files) or (p_name_clean and name_match)
                                if not match:
                                    for pf in files_to_delete:
                                        pf_dir = pf if pf.endswith(os.sep) else pf + os.sep
                                        if any(rf_.startswith(pf_dir) for rf_ in r_files):
                                            match = True
                                            break
                                if match:
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
    def install_pack_action(self):
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "Seleccionar Paquetes de Plugins (.vstpack)",
            os.path.expanduser("~"),
            "Paquetes Instrumentarium (*.vstpack *.zip);;Todos los archivos (*)"
        )
        if files:
            self.tabs.setCurrentIndex(1)
            self.add_files(files)

    # ----------------------------------------------------
    # STACK EXPORT & MIGRATION (.vstpack)
    # ----------------------------------------------------
    def export_selected_product(self):
        p = self._get_selected_product()
        if not p:
            QtWidgets.QMessageBox.information(self, "Seleccionar", "Selecciona un producto de la lista para exportar.")
            return

        if not is_product_tracked(p, self.wine_prefix):
            QtWidgets.QMessageBox.warning(
                self,
                "Plugin No Rastreado",
                f"El plugin '<b>{p['name']}</b>' no fue instalado ni registrado a través de Instrumentarium.<br><br>"
                "Al no disponer del historial de instalación original (claves del Registro de Windows, licencias y archivos en AppData), "
                "<b>no se permite su exportación a <code>.vstpack</code></b> para evitar respaldos incompletos o defectuosos.<br><br>"
                "<b>¿Cómo habilitar su exportación?</b><br>"
                "• Pulsa el botón <b>'🔴 Capturar Cambios Manuales'</b> para registrar sus datos y licencias activas.<br>"
                "• O reinstala el plugin utilizando Instrumentarium."
            )
            return

        clean_name = re.sub(r'[/\:*?"<>|]', '_', p['name']).strip()
        dest, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            f"Exportar Paquete Individual: {p['name']}",
            os.path.join(os.path.expanduser("~"), f"{clean_name}.vstpack"),
            "Paquete VSTPack (*.vstpack *.zip)"
        )
        if not dest:
            return

        self._start_export_worker(dest, [p], mode="monolithic")

    def export_all_products(self):
        if not self.installed_products:
            QtWidgets.QMessageBox.information(self, "Sin plugins", "No hay plugins detectados en este prefijo.")
            return

        tracked_products = [p for p in self.installed_products if is_product_tracked(p, self.wine_prefix)]
        untracked_count = len(self.installed_products) - len(tracked_products)

        if not tracked_products:
            QtWidgets.QMessageBox.warning(
                self,
                "Sin Plugins Rastreados",
                "Ninguno de los plugins detectados fue instalado ni registrado con Instrumentarium.<br><br>"
                "Para poder exportar respaldos completos con registro y licencias, instala tus plugins mediante Instrumentarium "
                "o utiliza el botón <b>'🔴 Capturar Cambios Manuales'</b> en la pestaña principal."
            )
            return

        # Prompt user: Folder with Individual Packs (Default/Recommended) vs Monolithic File
        msg_box = QtWidgets.QMessageBox(self)
        msg_box.setWindowTitle("Modo de Respaldo - Instrumentarium")

        warn_text = ""
        if untracked_count > 0:
            warn_text = f"<br><br><small style='color: #f59e0b;'>⚠️ Nota: Se detectaron {untracked_count} plugin(s) sin historial que serán omitidos por seguridad.</small>"

        msg_box.setText(
            f"<h3>Elige el formato de la Copia de Seguridad</h3>"
            f"Se exportarán <b>{len(tracked_products)} plugin(s) rastreados</b>.{warn_text}<br><br>"
            "<b>1. Carpeta con Paquetes Individuales (Por Defecto / Recomendado):</b><br>"
            "Crea una carpeta fechada que contiene un archivo <code>.vstpack</code> independiente para cada plugin.<br><br>"
            "<b>2. Archivo Monolítico Único:</b><br>"
            "Empaqueta todo el stack completo en un único archivo consolidado."
        )

        btn_folder = msg_box.addButton("Carpeta con Packs Individuales (Recomendado)", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        btn_single = msg_box.addButton("Archivo Único Consolidado", QtWidgets.QMessageBox.ButtonRole.ActionRole)
        btn_cancel = msg_box.addButton("Cancelar", QtWidgets.QMessageBox.ButtonRole.RejectRole)
        msg_box.setDefaultButton(btn_folder)

        msg_box.exec()
        clicked = msg_box.clickedButton()

        if clicked == btn_cancel or clicked is None:
            return

        stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())

        if clicked == btn_folder:
            base_dir = QtWidgets.QFileDialog.getExistingDirectory(
                self,
                "Seleccionar Carpeta para Guardar el Respaldo",
                os.path.expanduser("~")
            )
            if not base_dir:
                return
            target_folder = os.path.join(base_dir, f"Instrumentarium_Backup_{stamp}")
            os.makedirs(target_folder, exist_ok=True)
            self._start_export_worker(target_folder, tracked_products, mode="individual_folder")
        else:
            dest, _ = QtWidgets.QFileDialog.getSaveFileName(
                self,
                "Guardar Archivo de Respaldo Completo",
                os.path.join(os.path.expanduser("~"), f"Instrumentarium_Full_Backup_{stamp}.vstpack"),
                "Paquete VSTPack (*.vstpack *.zip)"
            )
            if not dest:
                return
            self._start_export_worker(dest, tracked_products, mode="monolithic")

    def _start_export_worker(self, target_path, products_to_export, mode="monolithic"):
        self.export_status_lbl.setText("Generando paquete de migración...")

        # Prevent concurrent exports
        for btn in self.tab_dashboard.findChildren(QtWidgets.QPushButton) + self.tab_backup.findChildren(QtWidgets.QPushButton):
            if "Exportar" in btn.text():
                btn.setEnabled(False)

        self.export_worker = ExportWorker(target_path, products_to_export, self.wine_prefix, self.wine_root, mode=mode, is_windows=self.is_windows)
        self.export_worker.progress.connect(lambda msg: self.export_status_lbl.setText(msg))
        self.export_worker.finished.connect(self.on_export_finished)
        self.export_worker.start()

    def on_export_finished(self, success, result):
        for btn in self.tab_dashboard.findChildren(QtWidgets.QPushButton) + self.tab_backup.findChildren(QtWidgets.QPushButton):
            if "Exportar" in btn.text():
                btn.setEnabled(True)

        if success:
            self.export_status_lbl.setText(f"✓ Respaldo exportado exitosamente en: {result}")
            QtWidgets.QMessageBox.information(
                self,
                "Exportación Exitosa",
                f"El respaldo ha sido generado con éxito:\n\n{result}\n\n"
                "Características de compatibilidad:\n"
                " • En Linux: Arrastra los paquetes .vstpack a Instrumentarium o ejecuta install_linux.sh.\n"
                " • En Windows: Descomprime los paquetes y ejecuta install_windows.bat.\n\n"
                "Todos los paquetes incluyen binarios VST2/VST3/CLAP, presets, datos de AppData y claves de registro de Windows."
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
        self.worker = InstallWorker(self.queue_items, self.wine_prefix, self.wine_root, silent_mode=silent, is_windows=getattr(self, 'is_windows', False))
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
