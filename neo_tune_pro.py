import os
import sys
import glob
import json
import math
import socket
import threading
import time
import ctypes
import platform
import tempfile
import shutil
import subprocess
import webbrowser
import csv
import io
import base64
import urllib.request
import urllib.error
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from datetime import datetime
from PIL import Image, ImageDraw
import pystray

# Third-party imports
import customtkinter as ctk
import psutil

# Windows-specific modules
if platform.system() == "Windows":
    import winreg
    from ctypes import wintypes, windll, byref, POINTER, c_ulong, c_void_p, c_uint, Structure
    from ctypes.wintypes import HANDLE, DWORD, BOOL, LPVOID, LPCWSTR, UINT

# Configure CustomTkinter appearance
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class FSNode:
    """A node in the scanned filesystem tree (file or directory)."""
    __slots__ = ("name", "path", "size", "is_dir", "children", "file_count", "parent", "error")

    def __init__(self, name, path, size=0, is_dir=False, parent=None, file_count=0):
        self.name = name
        self.path = path
        self.size = size
        self.is_dir = is_dir
        self.children = []
        self.file_count = file_count
        self.parent = parent
        self.error = None


class PCTuneUpApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("⚡ NEO TUNE - PC Optimization Suite")
        self.geometry("1200x700")
        self.minsize(1000, 600)

        # Window icon (works from source and PyInstaller bundle)
        try:
            base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
            ico = os.path.join(base, "neotune_ico.ico")
            if platform.system() == "Windows" and os.path.exists(ico):
                self.iconbitmap(ico)
        except Exception:
            pass

        # For tray, bandwidth + startup
        self.tray_icon = None
        self.tray_started = False
        self._running = True
        # Live bandwidth state (bytes/sec)
        self._bw_down = 0.0
        self._bw_up = 0.0
        self._bw_history = deque([0.0] * 60, maxlen=60)
        self._bw_total_down = 0
        self._bw_total_up = 0
        self._tray_bw_text = "Network: idle"
        self.startup_enabled = self.check_startup_registry()  # or startup folder
        self.setup_tray()
        self.start_tray()
        self.start_bandwidth_monitor()

        # AI advisor / Doctor config (local-first; Anthropic optional)
        self.doctor_win = None
        self.doctor_findings = []
        self.ai_provider_var = ctk.StringVar(value="Ollama")
        self.ai_endpoint_var = ctk.StringVar(value="http://localhost:11434")
        self.ai_model_var = ctk.StringVar(value="llama3.2")
        self.ai_key_var = ctk.StringVar(value="")  # session only, never written to disk

        # Disk scanner state
        self.scan_win = None
        self.scan_root = None
        self.scan_nodes = {}
        self.scan_loaded = set()
        self._scan_stop = threading.Event()
        self._scanning = False
        self._scan_files = 0
        self._scan_bytes = 0
        self._scan_current = ""

        # Configure grid layout
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # Sidebar frame
        self.sidebar = ctk.CTkFrame(self, width=250, corner_radius=0, fg_color="#0A0A0A")
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_propagate(False)

        # Main content frame
        self.main_frame = ctk.CTkFrame(self, corner_radius=0, fg_color="#0F0F12")
        self.main_frame.grid(row=0, column=1, sticky="nsew", padx=0, pady=0)
        self.main_frame.grid_columnconfigure(0, weight=1)
        self.main_frame.grid_rowconfigure(0, weight=0)  # Header
        self.main_frame.grid_rowconfigure(1, weight=1)  # Metrics

        # High-tech header
        self.create_header()

        # System metrics card (live)
        self.create_metrics_dashboard()

        # Log output area
        self.create_log_area()

        # Sidebar buttons
        self.create_sidebar_buttons()

        # Admin check
        self.is_admin = self.check_admin()
        if not self.is_admin:
            self.log_message("⚠️ Run as Administrator for full features (Prefetch, DNS flush, memory cleanup)")

        # Start live system metrics update
        self.update_system_metrics()

        # Handle closing event -> minimize to tray
        self.protocol("WM_DELETE_WINDOW", self.hide_window)

    # ---------------------- SYSTEM TRAY + BANDWIDTH ----------------------
    def make_bandwidth_icon(self, down_bps=0.0, up_bps=0.0):
        """Render a 64x64 tray icon whose two bars reflect live down/up speed."""
        size = 64
        img = Image.new('RGBA', (size, size), (10, 10, 14, 255))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([2, 2, size - 3, size - 3], radius=10,
                            outline=(0, 255, 204, 255), width=2)

        def bar_height(bps):
            if bps <= 0:
                return 0.0
            # Log scale: ~1 KB/s (10^3) floor -> ~10 MB/s (10^7) full
            v = (math.log10(bps + 1) - 3) / (7 - 3)
            return max(0.0, min(1.0, v))

        max_bar = size - 24
        base_y = size - 12
        dh = int(bar_height(down_bps) * max_bar)
        uh = int(bar_height(up_bps) * max_bar)

        # Download bar (left, green) + upload bar (right, orange)
        d.rectangle([16, base_y - dh, 27, base_y], fill=(0, 230, 150, 255))
        d.rectangle([37, base_y - uh, 48, base_y], fill=(255, 150, 50, 255))
        # Down arrow over the left bar
        d.polygon([(16, 9), (27, 9), (21, 16)], fill=(0, 230, 150, 255))
        # Up arrow over the right bar
        d.polygon([(37, 16), (48, 16), (42, 9)], fill=(255, 150, 50, 255))
        return img

    def setup_tray(self):
        """Create a system tray icon with menu + live bandwidth row."""
        image = self.make_bandwidth_icon(0, 0)

        def on_show(icon, item):
            self.after(0, self.show_window)

        def on_network(icon, item):
            self.after(0, self.open_network_center)

        def on_exit(icon, item):
            self.after(0, self.quit_app)

        menu = pystray.Menu(
            pystray.MenuItem(lambda item: self._tray_bw_text, lambda *a: None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Show NEO TUNE", on_show, default=True),
            pystray.MenuItem("Network Center", on_network),
            pystray.MenuItem("Exit", on_exit),
        )
        self.tray_icon = pystray.Icon("neo_tune", image, "NEO TUNE", menu)

    def start_tray(self):
        """Run the tray icon persistently (so the bandwidth meter is always visible)."""
        if self.tray_icon is not None and not self.tray_started:
            self.tray_started = True
            threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def start_bandwidth_monitor(self):
        threading.Thread(target=self._bandwidth_loop, daemon=True).start()

    def _bandwidth_loop(self):
        try:
            prev = psutil.net_io_counters()
        except Exception:
            return
        while self._running:
            time.sleep(1)
            try:
                cur = psutil.net_io_counters()
            except Exception:
                continue
            down = max(0, cur.bytes_recv - prev.bytes_recv)
            up = max(0, cur.bytes_sent - prev.bytes_sent)
            prev = cur
            self._bw_down = down
            self._bw_up = up
            self._bw_total_down = cur.bytes_recv
            self._bw_total_up = cur.bytes_sent
            self._bw_history.append(down)
            self._tray_bw_text = f"\u2193 {self._fmt_rate(down)}    \u2191 {self._fmt_rate(up)}"
            if self.tray_icon is not None and self.tray_started:
                try:
                    self.tray_icon.icon = self.make_bandwidth_icon(down, up)
                    self.tray_icon.title = f"NEO TUNE  \u2022  {self._tray_bw_text}"
                    self.tray_icon.update_menu()
                except Exception:
                    pass

    def _fmt_rate(self, bps):
        b = float(bps)
        for unit in ['B/s', 'KB/s', 'MB/s', 'GB/s']:
            if b < 1024.0:
                return f"{b:.0f} {unit}" if unit == 'B/s' else f"{b:.1f} {unit}"
            b /= 1024.0
        return f"{b:.1f} TB/s"

    def show_window(self):
        self.deiconify()
        self.lift()
        self.focus_force()

    def hide_window(self):
        # Tray already runs persistently; just hide the main window.
        self.withdraw()

    def quit_app(self):
        self._running = False
        if self.tray_icon:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
        self.destroy()
        sys.exit(0)

    # ---------------------- STARTUP MANAGEMENT ----------------------
    def check_startup_registry(self):
        """Check if the app is in HKCU Run registry"""
        if platform.system() != "Windows":
            return False
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ)
            value, _ = winreg.QueryValueEx(key, "NeoTune")
            winreg.CloseKey(key)
            return value == sys.executable + ' "' + __file__ + '"'
        except:
            return False

    def toggle_startup(self, enable):
        """Enable or disable run on Windows startup"""
        if platform.system() != "Windows":
            self.log_message("Startup setting only available on Windows")
            return
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
            if enable:
                app_path = sys.executable + ' "' + __file__ + '"'
                winreg.SetValueEx(key, "NeoTune", 0, winreg.REG_SZ, app_path)
                self.log_message("✅ Enabled run on startup")
            else:
                winreg.DeleteValue(key, "NeoTune")
                self.log_message("❌ Disabled run on startup")
            winreg.CloseKey(key)
            self.startup_enabled = enable
        except Exception as e:
            self.log_message(f"⚠️ Startup toggle error: {e}")

    # ---------------------- UTILITIES WINDOW ----------------------
    def open_utilities(self, initial_tab=None):
        """Create a new window with advanced utilities"""
        util_win = ctk.CTkToplevel(self)
        util_win.title("🛠️ NEO UTILITIES")
        util_win.geometry("850x600")
        util_win.attributes('-topmost', True)
        util_win.grab_set()  # modal behavior

        # Notebook style tabs using CTkFrame
        tabview = ctk.CTkTabview(util_win, width=800, height=500)
        tabview.pack(padx=20, pady=20, fill="both", expand=True)

        tabview.add("💾 Memory & Storage")
        tabview.add("🌐 Network Tools")
        tabview.add("🔌 USB & Devices")
        tabview.add("⚙️ Extras")

        if initial_tab:
            try:
                tabview.set(initial_tab)
            except Exception:
                pass

        # ---- TAB 1: Memory & Storage ----
        tab1 = tabview.tab("💾 Memory & Storage")
        ctk.CTkLabel(tab1, text="RAM Optimization", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=10)
        btn_ram_trim = ctk.CTkButton(tab1, text="🧠 Trim Current Process Memory", command=self.trim_memory)
        btn_ram_trim.pack(pady=5)
        btn_ram_system = ctk.CTkButton(tab1, text="🔄 System Memory Cleanup (Working Set)", command=self.system_memory_cleanup)
        btn_ram_system.pack(pady=5)

        ctk.CTkLabel(tab1, text="Disk Cleanup", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(20,10))
        btn_temp = ctk.CTkButton(tab1, text="🧹 Quick Clean Temp Files", command=self.quick_clean)
        btn_temp.pack(pady=5)

        # ---- TAB 2: Network Tools ----
        tab2 = tabview.tab("🌐 Network Tools")
        ctk.CTkLabel(tab2, text="IP Configuration", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=10)
        btn_release = ctk.CTkButton(tab2, text="📡 IP Release", command=self.ip_release)
        btn_release.pack(pady=5)
        btn_renew = ctk.CTkButton(tab2, text="🔄 IP Renew", command=self.ip_renew)
        btn_renew.pack(pady=5)
        btn_flushdns = ctk.CTkButton(tab2, text="🚿 Flush DNS Cache", command=self.flush_dns)
        btn_flushdns.pack(pady=5)

        ctk.CTkLabel(tab2, text="DNS Jumper", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(20,10))
        dns_frame = ctk.CTkFrame(tab2)
        dns_frame.pack(pady=5, fill="x", padx=20)
        dns_options = ["Automatic (DHCP)", "Google (8.8.8.8 / 8.8.4.4)", "Cloudflare (1.1.1.1)", "OpenDNS (208.67.222.222)"]
        self.dns_var = ctk.StringVar(value=dns_options[0])
        dns_menu = ctk.CTkOptionMenu(dns_frame, values=dns_options, variable=self.dns_var)
        dns_menu.pack(side="left", padx=10)
        btn_set_dns = ctk.CTkButton(dns_frame, text="Apply DNS", command=self.apply_dns)
        btn_set_dns.pack(side="left", padx=10)

        ctk.CTkLabel(tab2, text="Advanced", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(20, 10))
        btn_netcenter = ctk.CTkButton(
            tab2, text="🌐 Open Network Center  (IP • DNS • Proxy • VPN • Bandwidth)",
            fg_color="#0E7C66", hover_color="#0A5E4D", command=self.open_network_center)
        btn_netcenter.pack(pady=5)

        # ---- TAB 3: USB & Devices ----
        tab3 = tabview.tab("🔌 USB & Devices")
        ctk.CTkLabel(tab3, text="USB Vaccine (Anti-Autorun)", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=10)
        btn_vaccine = ctk.CTkButton(tab3, text="💉 Apply USB Vaccine", command=self.usb_vaccine)
        btn_vaccine.pack(pady=5)
        ctk.CTkLabel(tab3, text="Safely Remove USB Drive", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(20,10))
        self.usb_drives_var = ctk.StringVar()
        self.refresh_usb_list()
        usb_menu = ctk.CTkOptionMenu(tab3, values=self.usb_drives, variable=self.usb_drives_var)
        usb_menu.pack(pady=5)
        btn_eject = ctk.CTkButton(tab3, text="⏏️ Eject Selected USB", command=self.eject_usb)
        btn_eject.pack(pady=5)
        btn_refresh_usb = ctk.CTkButton(tab3, text="⟳ Refresh USB List", command=self.refresh_usb_list)
        btn_refresh_usb.pack(pady=5)

        # ---- TAB 4: Extras ----
        tab4 = tabview.tab("⚙️ Extras")
        ctk.CTkLabel(tab4, text="Desktop & System", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=10)
        btn_repair_icons = ctk.CTkButton(tab4, text="🖼️ Repair Desktop Icons", command=self.repair_desktop_icons)
        btn_repair_icons.pack(pady=5)
        btn_startup = ctk.CTkButton(tab4, text="🚀 Open Startup Manager", command=self.open_startup_manager)
        btn_startup.pack(pady=5)

        # Startup toggle checkbox
        self.startup_var = ctk.BooleanVar(value=self.startup_enabled)
        chk_startup = ctk.CTkCheckBox(tab4, text="Run NEO TUNE on Windows startup", variable=self.startup_var,
                                      command=lambda: self.toggle_startup(self.startup_var.get()))
        chk_startup.pack(pady=10)

    # ---------------------- UTILITY IMPLEMENTATIONS ----------------------
    def trim_memory(self):
        """Trim working set of current process"""
        try:
            if platform.system() == "Windows":
                # SetProcessWorkingSetSize(-1, -1, -1) to trim
                kernel32 = ctypes.windll.kernel32
                kernel32.SetProcessWorkingSetSize(-1, -1, -1)
                self.log_message("✅ Current process memory trimmed")
            else:
                self.log_message("⚠️ Memory trimming only supported on Windows")
        except Exception as e:
            self.log_message(f"❌ Memory trim error: {e}")

    def system_memory_cleanup(self):
        """Call EmptyWorkingSet on all processes using psutil (requires admin)"""
        if not self.is_admin:
            self.log_message("⚠️ System memory cleanup requires administrator rights")
            return
        try:
            kernel32 = ctypes.windll.kernel32
            psapi = ctypes.windll.psapi
            count = 0
            for proc in psutil.process_iter(['pid']):
                try:
                    pid = proc.info['pid']
                    if pid == 0:
                        continue
                    hProcess = kernel32.OpenProcess(0x0010, False, pid)  # PROCESS_SET_QUOTA
                    if hProcess:
                        psapi.EmptyWorkingSet(hProcess)
                        kernel32.CloseHandle(hProcess)
                        count += 1
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            self.log_message(f"✅ System memory working set cleaned ({count} processes)")
        except Exception as e:
            self.log_message(f"❌ System memory cleanup error: {e}")

    def repair_desktop_icons(self):
        """Rebuild icon cache by deleting IconCache.db and restarting explorer"""
        try:
            if platform.system() != "Windows":
                self.log_message("Icon repair only available on Windows")
                return
            # Kill explorer.exe temporarily (will restart)
            subprocess.run("taskkill /f /im explorer.exe", shell=True, capture_output=True)
            # Delete icon cache
            icon_cache = os.path.join(os.environ['LOCALAPPDATA'], 'IconCache.db')
            if os.path.exists(icon_cache):
                os.remove(icon_cache)
            # Also delete thumbnail cache
            thumb_cache = os.path.join(os.environ['LOCALAPPDATA'], 'Microsoft', 'Windows', 'Explorer', 'thumbcache_*.db')
            for f in glob.glob(thumb_cache):
                try:
                    os.remove(f)
                except:
                    pass
            # Restart explorer
            subprocess.Popen("explorer.exe", shell=True)
            self.log_message("✅ Desktop icon cache rebuilt, explorer restarted")
        except Exception as e:
            self.log_message(f"❌ Icon repair error: {e}")

    def ip_release(self):
        self._run_ip_command("ipconfig /release", "IP released")

    def ip_renew(self):
        self._run_ip_command("ipconfig /renew", "IP renewed")

    def flush_dns(self):
        self._run_ip_command("ipconfig /flushdns", "DNS cache flushed")

    def _run_ip_command(self, command, success_msg):
        try:
            subprocess.run(command, shell=True, check=True, capture_output=True)
            self.log_message(f"✅ {success_msg}")
        except subprocess.CalledProcessError as e:
            self.log_message(f"❌ Command failed: {e.stderr.decode()}")

    def apply_dns(self):
        """Set DNS for the active network adapter"""
        choice = self.dns_var.get()
        if choice == "Automatic (DHCP)":
            dns1 = dns2 = ""
            cmd = "netsh interface ip set dns name=\"Ethernet\" source=dhcp"
            cmd2 = "netsh interface ip set dns name=\"Wi-Fi\" source=dhcp"
        elif choice == "Google (8.8.8.8 / 8.8.4.4)":
            dns1, dns2 = "8.8.8.8", "8.8.4.4"
        elif choice == "Cloudflare (1.1.1.1)":
            dns1, dns2 = "1.1.1.1", "1.0.0.1"
        elif choice == "OpenDNS (208.67.222.222)":
            dns1, dns2 = "208.67.222.222", "208.67.220.220"
        else:
            return

        try:
            if choice == "Automatic (DHCP)":
                subprocess.run(cmd, shell=True, check=True)
                subprocess.run(cmd2, shell=True, check=True)
            else:
                # Get all network adapters and set DNS
                result = subprocess.run("netsh interface show interface", shell=True, capture_output=True, text=True)
                lines = result.stdout.splitlines()
                for line in lines:
                    if "Connected" in line and ("Ethernet" in line or "Wi-Fi" in line):
                        parts = line.split()
                        if len(parts) >= 4:
                            adapter_name = " ".join(parts[3:])
                            subprocess.run(f'netsh interface ip set dns name="{adapter_name}" static {dns1}', shell=True, check=True)
                            subprocess.run(f'netsh interface ip add dns name="{adapter_name}" {dns2} index=2', shell=True, check=True)
            self.log_message(f"✅ DNS set to: {choice}")
        except Exception as e:
            self.log_message(f"❌ DNS change error: {e}")

    def usb_vaccine(self):
        """Disable autorun.inf for all drives and create protected folder"""
        try:
            # Disable AutoRun completely via registry
            key_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\Explorer"
            key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path)
            winreg.SetValueEx(key, "NoDriveTypeAutoRun", 0, winreg.REG_DWORD, 0xFF)
            winreg.CloseKey(key)
            self.log_message("✅ USB Vaccine: AutoRun disabled via registry")

            # Additionally, create a read-only AUTORUN.INF folder on all removable drives
            for partition in psutil.disk_partitions():
                if 'removable' in partition.opts.lower():
                    drive = partition.device
                    autorun_path = os.path.join(drive, "AUTORUN.INF")
                    if not os.path.exists(autorun_path):
                        os.mkdir(autorun_path)
                    # Set system, hidden, read-only attributes
                    ctypes.windll.kernel32.SetFileAttributesW(autorun_path, 0x07)  # SYSTEM|HIDDEN|READONLY
                    self.log_message(f"💉 Vaccinated drive {drive}")
        except Exception as e:
            self.log_message(f"❌ USB Vaccine error: {e}")

    def refresh_usb_list(self):
        """Update list of removable drives"""
        self.usb_drives = []
        for part in psutil.disk_partitions():
            if 'removable' in part.opts.lower():
                self.usb_drives.append(part.device)
        if not self.usb_drives:
            self.usb_drives = ["No USB drives found"]
        self.usb_drives_var.set(self.usb_drives[0])

    def eject_usb(self):
        """Safely eject selected USB drive using PowerShell"""
        drive = self.usb_drives_var.get()
        if "No USB" in drive:
            self.log_message("No USB drive selected")
            return
        try:
            # PowerShell eject method
            ps_script = f'''
            $driveLetter = "{drive[0]}"
            $volume = Get-WmiObject -Class Win32_Volume | Where-Object {{ $_.DriveLetter -eq $driveLetter }}
            if ($volume -ne $null) {{
                $volume.Dismount($true, $false)
                Write-Host "Ejecting $driveLetter..."
            }}
            '''
            result = subprocess.run(["powershell", "-Command", ps_script], capture_output=True, text=True)
            if result.returncode == 0:
                self.log_message(f"⏏️ USB drive {drive} ejected safely")
            else:
                self.log_message(f"⚠️ Eject failed: {result.stderr}")
        except Exception as e:
            self.log_message(f"❌ Eject error: {e}")

    # ======================================================================
    #                          NETWORK CENTER
    # ======================================================================
    # Curated catalog of reputable free public DNS resolvers.
    DNS_CATALOG = [
        ("Cloudflare",        "1.1.1.1",        "1.0.0.1"),
        ("Google",            "8.8.8.8",        "8.8.4.4"),
        ("Quad9",             "9.9.9.9",        "149.112.112.112"),
        ("OpenDNS",           "208.67.222.222", "208.67.220.220"),
        ("AdGuard DNS",       "94.140.14.14",   "94.140.15.15"),
        ("CleanBrowsing",     "185.228.168.9",  "185.228.169.9"),
        ("Comodo Secure",     "8.26.56.26",     "8.20.247.20"),
        ("DNS.WATCH",         "84.200.69.80",   "84.200.70.40"),
        ("Verisign",          "64.6.64.6",      "64.6.65.6"),
        ("Level3",            "4.2.2.1",        "4.2.2.2"),
        ("Cloudflare Family", "1.1.1.3",        "1.0.0.3"),
        ("AdGuard NoFilter",  "94.140.14.140",  "94.140.14.141"),
    ]

    PROXY_SOURCES = [
        "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
        "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
        "https://api.proxyscrape.com/v2/?request=getproxies&protocol=http&timeout=8000&country=all&ssl=all&anonymity=all",
        "https://www.proxy-list.download/api/v1/get?type=http",
    ]

    def open_network_center(self):
        if getattr(self, "net_win", None) is not None:
            try:
                if self.net_win.winfo_exists():
                    self.net_win.lift()
                    self.net_win.focus_force()
                    return
            except Exception:
                pass

        win = ctk.CTkToplevel(self)
        self.net_win = win
        win.title("🌐 NEO NETWORK CENTER")
        win.geometry("920x680")
        win.attributes('-topmost', True)
        win.after(400, lambda: win.attributes('-topmost', False))

        tabview = ctk.CTkTabview(win, width=880, height=620)
        tabview.pack(padx=16, pady=16, fill="both", expand=True)
        tabview.add("📡 IP Info")
        tabview.add("🛡️ DNS Changer")
        tabview.add("🧦 Free Proxy")
        tabview.add("🔒 Free VPN")
        tabview.add("📈 Bandwidth")

        self._build_ip_tab(tabview.tab("📡 IP Info"))
        self._build_dns_tab(tabview.tab("🛡️ DNS Changer"))
        self._build_proxy_tab(tabview.tab("🧦 Free Proxy"))
        self._build_vpn_tab(tabview.tab("🔒 Free VPN"))
        self._build_bandwidth_tab(tabview.tab("📈 Bandwidth"))

    # ----------------------- shared HTTP helper -----------------------
    def _net_http_get(self, url, timeout=10, proxy=None):
        if proxy:
            handler = urllib.request.ProxyHandler({"http": f"http://{proxy}", "https": f"http://{proxy}"})
            opener = urllib.request.build_opener(handler)
        else:
            opener = urllib.request.build_opener()
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (NeoTune NetworkCenter)"})
        with opener.open(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "ignore")

    def get_public_ip(self):
        for url in ("https://api.ipify.org", "https://ifconfig.me/ip", "https://icanhazip.com"):
            try:
                ip = self._net_http_get(url, timeout=6).strip()
                if ip:
                    return ip
            except Exception:
                continue
        return "Unavailable (offline?)"

    def _connected_adapters(self):
        """Return the names of connected network adapters."""
        if platform.system() != "Windows":
            try:
                return list(psutil.net_if_addrs().keys())
            except Exception:
                return ["eth0"]
        names = []
        try:
            result = subprocess.run("netsh interface show interface", shell=True,
                                    capture_output=True, text=True)
            for line in result.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 4 and parts[0] in ("Enabled", "Disabled") and "Connected" in line:
                    names.append(" ".join(parts[3:]))
        except Exception:
            pass
        if not names:
            names = ["Wi-Fi", "Ethernet"]
        return names

    # ============================ IP INFO TAB ============================
    def _build_ip_tab(self, tab):
        top = ctk.CTkFrame(tab, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=(12, 6))
        ctk.CTkLabel(top, text="IP Configuration", font=ctk.CTkFont(size=18, weight="bold")).pack(side="left")

        btnbar = ctk.CTkFrame(tab, fg_color="transparent")
        btnbar.pack(fill="x", padx=12, pady=4)
        ctk.CTkButton(btnbar, text="🔄 Refresh", width=90, command=lambda: self._refresh_ip_info(box)).pack(side="left", padx=4)
        ctk.CTkButton(btnbar, text="📡 Release", width=90, command=self.ip_release).pack(side="left", padx=4)
        ctk.CTkButton(btnbar, text="🔁 Renew", width=90, command=self.ip_renew).pack(side="left", padx=4)
        ctk.CTkButton(btnbar, text="🚿 Flush DNS", width=100, command=self.flush_dns).pack(side="left", padx=4)
        ctk.CTkButton(btnbar, text="🌍 Copy Public IP", width=130,
                      command=lambda: self._copy_to_clipboard(self.get_public_ip())).pack(side="left", padx=4)

        box = ctk.CTkTextbox(tab, font=ctk.CTkFont(family="Consolas", size=12), fg_color="#050508")
        box.pack(fill="both", expand=True, padx=12, pady=12)
        self._refresh_ip_info(box)

    def _refresh_ip_info(self, box):
        box.configure(state="normal")
        box.delete("1.0", "end")
        box.insert("end", "Gathering network information...\n")
        box.configure(state="disabled")

        def work():
            lines = []
            public_ip = self.get_public_ip()
            lines.append("╔══════════════ NEO NETWORK SNAPSHOT ══════════════")
            lines.append(f"║ Public IP : {public_ip}")
            lines.append(f"║ Hostname  : {socket.gethostname()}")
            lines.append("╠═══════════════════ ADAPTERS ═════════════════════")
            try:
                addrs = psutil.net_if_addrs()
                stats = psutil.net_if_stats()
                for name, addr_list in addrs.items():
                    st = stats.get(name)
                    if st is not None and not st.isup:
                        continue
                    ipv4 = [a.address for a in addr_list if a.family == socket.AF_INET]
                    macs = [a.address for a in addr_list
                            if a.family == getattr(psutil, "AF_LINK", -1) or str(a.family).endswith("AF_LINK")]
                    if not ipv4:
                        continue
                    speed = f"{st.speed} Mbps" if st and st.speed else "n/a"
                    lines.append(f"║ ▸ {name}")
                    lines.append(f"║     IPv4 : {', '.join(ipv4)}")
                    if macs:
                        lines.append(f"║     MAC  : {macs[0]}")
                    lines.append(f"║     Link : {speed}")
            except Exception as e:
                lines.append(f"║ (adapter enum error: {e})")
            lines.append("╚═══════════════════════════════════════════════════")

            if platform.system() == "Windows":
                try:
                    out = subprocess.run("ipconfig /all", shell=True, capture_output=True, text=True)
                    lines.append("\n──────────────── ipconfig /all ────────────────\n")
                    lines.append(out.stdout.strip())
                except Exception as e:
                    lines.append(f"\nipconfig error: {e}")
            else:
                try:
                    out = subprocess.run(["ip", "addr"], capture_output=True, text=True)
                    lines.append("\n──────────────── ip addr ────────────────\n")
                    lines.append(out.stdout.strip())
                except Exception:
                    pass

            text = "\n".join(lines)
            self.after(0, lambda: self._fill_box(box, text))

        threading.Thread(target=work, daemon=True).start()

    def _fill_box(self, box, text):
        try:
            box.configure(state="normal")
            box.delete("1.0", "end")
            box.insert("1.0", text)
            box.configure(state="disabled")
        except Exception:
            pass

    def _copy_to_clipboard(self, text):
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.log_message(f"📋 Copied to clipboard: {text}")
        except Exception as e:
            self.log_message(f"⚠️ Clipboard error: {e}")

    # =========================== DNS CHANGER TAB ===========================
    def _build_dns_tab(self, tab):
        head = ctk.CTkFrame(tab, fg_color="transparent")
        head.pack(fill="x", padx=12, pady=(12, 4))
        ctk.CTkLabel(head, text="Free Public DNS", font=ctk.CTkFont(size=18, weight="bold")).pack(side="left")

        ctrl = ctk.CTkFrame(tab, fg_color="transparent")
        ctrl.pack(fill="x", padx=12, pady=4)
        ctk.CTkLabel(ctrl, text="Adapter:").pack(side="left", padx=(0, 6))
        self.dns_adapter_var = ctk.StringVar()
        adapters = self._connected_adapters()
        self.dns_adapter_menu = ctk.CTkOptionMenu(ctrl, values=adapters, variable=self.dns_adapter_var, width=180)
        self.dns_adapter_var.set(adapters[0])
        self.dns_adapter_menu.pack(side="left", padx=4)
        ctk.CTkButton(ctrl, text="⚡ Test Latency", width=120,
                      command=self._test_all_dns).pack(side="left", padx=4)
        ctk.CTkButton(ctrl, text="↩ Reset to Auto (DHCP)", width=170, fg_color="#7C4A0E",
                      hover_color="#5E380A", command=self._reset_dns_dhcp).pack(side="left", padx=4)

        if not self.is_admin:
            ctk.CTkLabel(tab, text="⚠️ Run as Administrator to apply DNS changes.",
                         text_color="#FFAA55", font=ctk.CTkFont(size=11)).pack(anchor="w", padx=14)

        self.dns_list_frame = ctk.CTkScrollableFrame(tab, fg_color="#0C0C12")
        self.dns_list_frame.pack(fill="both", expand=True, padx=12, pady=10)
        self.dns_rows = {}
        self._render_dns_rows()

    def _render_dns_rows(self, latencies=None):
        for w in self.dns_list_frame.winfo_children():
            w.destroy()
        self.dns_rows = {}
        catalog = list(self.DNS_CATALOG)
        if latencies:
            catalog.sort(key=lambda c: latencies.get(c[1]) if latencies.get(c[1]) is not None else 9e9)

        for name, p, s in catalog:
            row = ctk.CTkFrame(self.dns_list_frame, fg_color="#15151F")
            row.pack(fill="x", pady=3, padx=4)
            ctk.CTkLabel(row, text=name, width=150, anchor="w",
                         font=ctk.CTkFont(size=13, weight="bold")).pack(side="left", padx=8, pady=6)
            ctk.CTkLabel(row, text=f"{p}  /  {s}", width=240, anchor="w",
                         font=ctk.CTkFont(family="Consolas", size=12), text_color="#9FE8D0").pack(side="left", padx=4)
            lat = latencies.get(p) if latencies else None
            if lat is None:
                lat_text, color = "—", "#778"
            else:
                lat_text = f"{lat:.0f} ms"
                color = "#5FE08F" if lat < 40 else ("#E8D05F" if lat < 100 else "#E88F5F")
            lbl = ctk.CTkLabel(row, text=lat_text, width=70, text_color=color)
            lbl.pack(side="left", padx=4)
            self.dns_rows[p] = lbl
            ctk.CTkButton(row, text="Apply", width=70,
                          command=lambda pp=p, ss=s, nn=name: self._apply_dns_choice(nn, pp, ss)).pack(side="right", padx=8)

    def _test_all_dns(self):
        self.log_message("⚡ Testing DNS latency...")

        def work():
            results = {}
            with ThreadPoolExecutor(max_workers=8) as ex:
                futs = {ex.submit(self._dns_udp_latency, p): p for _, p, _ in self.DNS_CATALOG}
                for f in futs:
                    p = futs[f]
                    try:
                        results[p] = f.result()
                    except Exception:
                        results[p] = None
            self.after(0, lambda: self._render_dns_rows(results))
            fastest = min((c for c in self.DNS_CATALOG if results.get(c[1]) is not None),
                          key=lambda c: results[c[1]], default=None)
            if fastest:
                self.after(0, lambda: self.log_message(
                    f"🏆 Fastest DNS: {fastest[0]} ({fastest[1]}) — {results[fastest[1]]:.0f} ms"))

        threading.Thread(target=work, daemon=True).start()

    def _dns_udp_latency(self, ip, qname="www.google.com"):
        """Send a real UDP DNS query and measure round-trip time in ms."""
        try:
            tid = b'\x12\x34'
            header = tid + b'\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00'
            q = b''.join(bytes([len(p)]) + p.encode() for p in qname.split('.')) + b'\x00'
            packet = header + q + b'\x00\x01\x00\x01'
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(2)
            t0 = time.time()
            s.sendto(packet, (ip, 53))
            s.recvfrom(512)
            s.close()
            return (time.time() - t0) * 1000.0
        except Exception:
            return None

    def _apply_dns_choice(self, name, dns1, dns2):
        if platform.system() != "Windows":
            self.log_message("DNS change only implemented on Windows")
            return
        if not self.is_admin:
            self.log_message("⚠️ Administrator rights required to change DNS")
            return
        adapter = self.dns_adapter_var.get()

        def work():
            try:
                subprocess.run(f'netsh interface ip set dns name="{adapter}" static {dns1}',
                               shell=True, check=True, capture_output=True)
                subprocess.run(f'netsh interface ip add dns name="{adapter}" {dns2} index=2',
                               shell=True, check=True, capture_output=True)
                self.after(0, lambda: self.log_message(f"✅ DNS on '{adapter}' → {name} ({dns1}, {dns2})"))
                subprocess.run("ipconfig /flushdns", shell=True, capture_output=True)
            except subprocess.CalledProcessError as e:
                err = e.stderr.decode(errors="ignore") if e.stderr else str(e)
                self.after(0, lambda: self.log_message(f"❌ DNS apply failed: {err.strip()}"))

        threading.Thread(target=work, daemon=True).start()

    def _reset_dns_dhcp(self):
        if not self.is_admin:
            self.log_message("⚠️ Administrator rights required to reset DNS")
            return
        adapter = self.dns_adapter_var.get()

        def work():
            try:
                subprocess.run(f'netsh interface ip set dns name="{adapter}" source=dhcp',
                               shell=True, check=True, capture_output=True)
                self.after(0, lambda: self.log_message(f"↩ DNS on '{adapter}' reset to automatic (DHCP)"))
            except subprocess.CalledProcessError as e:
                err = e.stderr.decode(errors="ignore") if e.stderr else str(e)
                self.after(0, lambda: self.log_message(f"❌ DNS reset failed: {err.strip()}"))

        threading.Thread(target=work, daemon=True).start()

    # ============================ FREE PROXY TAB ============================
    def _build_proxy_tab(self, tab):
        head = ctk.CTkFrame(tab, fg_color="transparent")
        head.pack(fill="x", padx=12, pady=(12, 4))
        ctk.CTkLabel(head, text="Free HTTP Proxies", font=ctk.CTkFont(size=18, weight="bold")).pack(side="left")

        ctk.CTkLabel(tab, text="⚠️ Free public proxies are untrusted — never use them for banking, "
                              "passwords, or sensitive data.", text_color="#FFAA55",
                     font=ctk.CTkFont(size=11), wraplength=820, justify="left").pack(anchor="w", padx=14, pady=(0, 4))

        ctrl = ctk.CTkFrame(tab, fg_color="transparent")
        ctrl.pack(fill="x", padx=12, pady=4)
        ctk.CTkLabel(ctrl, text="Test:").pack(side="left", padx=(0, 4))
        self.proxy_count_var = ctk.StringVar(value="40")
        ctk.CTkOptionMenu(ctrl, values=["20", "40", "80", "150"], variable=self.proxy_count_var, width=80).pack(side="left", padx=4)
        ctk.CTkButton(ctrl, text="🔎 Fetch & Test Proxies", width=170, command=self._fetch_test_proxies).pack(side="left", padx=4)
        ctk.CTkButton(ctrl, text="🧹 Clear System Proxy", width=160, fg_color="#7C4A0E",
                      hover_color="#5E380A", command=self._clear_system_proxy).pack(side="left", padx=4)

        self.proxy_status = ctk.CTkLabel(tab, text="Idle.", font=ctk.CTkFont(size=11), text_color="#88AACC")
        self.proxy_status.pack(anchor="w", padx=14, pady=2)

        self.proxy_list_frame = ctk.CTkScrollableFrame(tab, fg_color="#0C0C12")
        self.proxy_list_frame.pack(fill="both", expand=True, padx=12, pady=10)

    def _fetch_test_proxies(self):
        for w in self.proxy_list_frame.winfo_children():
            w.destroy()
        self.proxy_status.configure(text="Fetching proxy lists...")

        def work():
            raw = set()
            for src in self.PROXY_SOURCES:
                try:
                    text = self._net_http_get(src, timeout=12)
                    for line in text.splitlines():
                        line = line.strip()
                        if ":" in line and line.count(".") == 3:
                            hostport = line.split()[0]
                            host = hostport.split(":")[0]
                            if all(part.isdigit() for part in host.split(".")):
                                raw.add(hostport)
                except Exception:
                    continue
            limit = int(self.proxy_count_var.get())
            candidates = list(raw)[:limit]
            if not candidates:
                self.after(0, lambda: self.proxy_status.configure(
                    text="No proxies fetched (check internet connection)."))
                return
            self.after(0, lambda: self.proxy_status.configure(
                text=f"Testing {len(candidates)} proxies (this can take a moment)..."))

            working = []
            with ThreadPoolExecutor(max_workers=20) as ex:
                futs = {ex.submit(self._test_proxy, hp): hp for hp in candidates}
                for f in futs:
                    hp = futs[f]
                    try:
                        ms = f.result()
                    except Exception:
                        ms = None
                    if ms is not None:
                        working.append((hp, ms))
            working.sort(key=lambda x: x[1])
            self.after(0, lambda: self._render_proxy_rows(working, len(candidates)))

        threading.Thread(target=work, daemon=True).start()

    def _test_proxy(self, hostport, timeout=6):
        proxy = {"http": f"http://{hostport}", "https": f"http://{hostport}"}
        opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxy))
        req = urllib.request.Request("http://www.google.com/generate_204",
                                     headers={"User-Agent": "Mozilla/5.0"})
        t0 = time.time()
        try:
            opener.open(req, timeout=timeout).read(50)
            return (time.time() - t0) * 1000.0
        except Exception:
            return None

    def _render_proxy_rows(self, working, tested):
        for w in self.proxy_list_frame.winfo_children():
            w.destroy()
        self.proxy_status.configure(text=f"✅ {len(working)} working / {tested} tested.")
        if not working:
            ctk.CTkLabel(self.proxy_list_frame, text="No working proxies right now — try again.").pack(pady=10)
            return
        for hp, ms in working:
            row = ctk.CTkFrame(self.proxy_list_frame, fg_color="#15151F")
            row.pack(fill="x", pady=3, padx=4)
            ctk.CTkLabel(row, text=hp, width=200, anchor="w",
                         font=ctk.CTkFont(family="Consolas", size=12)).pack(side="left", padx=8, pady=6)
            color = "#5FE08F" if ms < 800 else ("#E8D05F" if ms < 2000 else "#E88F5F")
            ctk.CTkLabel(row, text=f"{ms:.0f} ms", width=80, text_color=color).pack(side="left", padx=4)
            ctk.CTkButton(row, text="Use", width=60,
                          command=lambda h=hp: self._set_system_proxy(h)).pack(side="right", padx=4)
            ctk.CTkButton(row, text="Copy", width=60, fg_color="#333",
                          command=lambda h=hp: self._copy_to_clipboard(h)).pack(side="right", padx=4)

    def _set_system_proxy(self, hostport):
        if platform.system() != "Windows":
            self.log_message("System proxy setting only implemented on Windows")
            return
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                 r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
                                 0, winreg.KEY_SET_VALUE)
            winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, hostport)
            winreg.CloseKey(key)
            self._refresh_inet_settings()
            self.log_message(f"✅ System proxy enabled → {hostport}")
        except Exception as e:
            self.log_message(f"❌ Set proxy error: {e}")

    def _clear_system_proxy(self):
        if platform.system() != "Windows":
            return
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                 r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
                                 0, winreg.KEY_SET_VALUE)
            winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 0)
            winreg.CloseKey(key)
            self._refresh_inet_settings()
            self.log_message("🧹 System proxy disabled")
        except Exception as e:
            self.log_message(f"❌ Clear proxy error: {e}")

    def _refresh_inet_settings(self):
        try:
            INTERNET_OPTION_SETTINGS_CHANGED = 39
            INTERNET_OPTION_REFRESH = 37
            wininet = ctypes.windll.wininet
            wininet.InternetSetOptionW(0, INTERNET_OPTION_SETTINGS_CHANGED, 0, 0)
            wininet.InternetSetOptionW(0, INTERNET_OPTION_REFRESH, 0, 0)
        except Exception:
            pass

    # ============================ FREE VPN TAB ============================
    def _build_vpn_tab(self, tab):
        head = ctk.CTkFrame(tab, fg_color="transparent")
        head.pack(fill="x", padx=12, pady=(12, 4))
        ctk.CTkLabel(head, text="Free VPN (VPN Gate)", font=ctk.CTkFont(size=18, weight="bold")).pack(side="left")

        ctk.CTkLabel(tab, text="Powered by the public VPN Gate project (Univ. of Tsukuba). "
                              "Connecting requires the free OpenVPN Community client. "
                              "Free volunteer servers are untrusted — avoid sensitive logins.",
                     text_color="#FFAA55", font=ctk.CTkFont(size=11), wraplength=820,
                     justify="left").pack(anchor="w", padx=14, pady=(0, 4))

        ctrl = ctk.CTkFrame(tab, fg_color="transparent")
        ctrl.pack(fill="x", padx=12, pady=4)
        ctk.CTkButton(ctrl, text="🌐 Fetch Free VPN Servers", width=190,
                      command=self._fetch_vpngate).pack(side="left", padx=4)
        ctk.CTkButton(ctrl, text="⬇ Get OpenVPN", width=130, fg_color="#0E5C7C", hover_color="#0A4659",
                      command=lambda: webbrowser.open("https://openvpn.net/community-downloads/")).pack(side="left", padx=4)
        self.vpn_status = ctk.CTkLabel(ctrl, text="", font=ctk.CTkFont(size=11), text_color="#88AACC")
        self.vpn_status.pack(side="left", padx=8)

        self.vpn_list_frame = ctk.CTkScrollableFrame(tab, fg_color="#0C0C12")
        self.vpn_list_frame.pack(fill="both", expand=True, padx=12, pady=10)

    def _fetch_vpngate(self):
        for w in self.vpn_list_frame.winfo_children():
            w.destroy()
        self.vpn_status.configure(text="Fetching server list...")

        def work():
            try:
                data = self._net_http_get("https://www.vpngate.net/api/iphone/", timeout=20)
            except Exception as e:
                msg = str(e)
                self.after(0, lambda m=msg: self.vpn_status.configure(text=f"Fetch failed: {m}"))
                return
            rows, header = [], None
            for row in csv.reader(io.StringIO(data)):
                if not row or row[0].startswith('*'):
                    continue
                if row[0] == '#HostName':
                    header = [h.lstrip('#') for h in row]
                    continue
                if header and len(row) >= len(header):
                    entry = dict(zip(header, row))
                    if entry.get("OpenVPN_ConfigData_Base64"):
                        rows.append(entry)

            def score(e):
                try:
                    return int(e.get("Score", "0"))
                except Exception:
                    return 0
            rows.sort(key=score, reverse=True)
            self.after(0, lambda: self._render_vpn_rows(rows[:25]))

        threading.Thread(target=work, daemon=True).start()

    def _render_vpn_rows(self, rows):
        for w in self.vpn_list_frame.winfo_children():
            w.destroy()
        self.vpn_status.configure(text=f"{len(rows)} servers")
        if not rows:
            ctk.CTkLabel(self.vpn_list_frame, text="No servers found — try again.").pack(pady=10)
            return
        hdr = ctk.CTkFrame(self.vpn_list_frame, fg_color="transparent")
        hdr.pack(fill="x", padx=4)
        for txt, w in (("Country", 150), ("IP", 130), ("Ping", 70), ("Speed", 100), ("Sessions", 80)):
            ctk.CTkLabel(hdr, text=txt, width=w, anchor="w",
                         font=ctk.CTkFont(size=11, weight="bold"), text_color="#88AACC").pack(side="left", padx=4)

        for e in rows:
            row = ctk.CTkFrame(self.vpn_list_frame, fg_color="#15151F")
            row.pack(fill="x", pady=3, padx=4)
            try:
                speed_mbps = f"{int(e.get('Speed', '0')) / 1_000_000:.1f} Mbps"
            except Exception:
                speed_mbps = "n/a"
            ctk.CTkLabel(row, text=e.get("CountryLong", "?"), width=150, anchor="w",
                         font=ctk.CTkFont(size=12)).pack(side="left", padx=4, pady=6)
            ctk.CTkLabel(row, text=e.get("IP", "?"), width=130, anchor="w",
                         font=ctk.CTkFont(family="Consolas", size=11)).pack(side="left", padx=4)
            ctk.CTkLabel(row, text=f"{e.get('Ping', '?')} ms", width=70, anchor="w").pack(side="left", padx=4)
            ctk.CTkLabel(row, text=speed_mbps, width=100, anchor="w", text_color="#9FE8D0").pack(side="left", padx=4)
            ctk.CTkLabel(row, text=e.get("NumVpnSessions", "?"), width=80, anchor="w").pack(side="left", padx=4)
            ctk.CTkButton(row, text="Connect", width=80,
                          command=lambda en=e: self._connect_vpn(en)).pack(side="right", padx=4)
            ctk.CTkButton(row, text="Save .ovpn", width=90, fg_color="#333",
                          command=lambda en=e: self._save_ovpn_only(en)).pack(side="right", padx=4)

    def _save_ovpn(self, entry):
        cfg = base64.b64decode(entry["OpenVPN_ConfigData_Base64"]).decode("utf-8", "ignore")
        folder = os.path.join(os.path.expanduser("~"), "NeoTune_VPN")
        os.makedirs(folder, exist_ok=True)
        safe_ip = entry.get("IP", "server").replace(".", "_")
        path = os.path.join(folder, f"vpngate_{entry.get('CountryShort', 'XX')}_{safe_ip}.ovpn")
        with open(path, "w", encoding="utf-8") as f:
            f.write(cfg)
        return path

    def _save_ovpn_only(self, entry):
        def work():
            try:
                path = self._save_ovpn(entry)
                self.after(0, lambda: self.log_message(f"💾 Saved VPN config → {path}"))
                try:
                    os.startfile(os.path.dirname(path))
                except Exception:
                    pass
            except Exception as e:
                msg = str(e)
                self.after(0, lambda m=msg: self.log_message(f"❌ Save .ovpn error: {m}"))
        threading.Thread(target=work, daemon=True).start()

    def _find_openvpn(self):
        candidates = [
            r"C:\Program Files\OpenVPN\bin\openvpn-gui.exe",
            r"C:\Program Files (x86)\OpenVPN\bin\openvpn-gui.exe",
            r"C:\Program Files\OpenVPN\bin\openvpn.exe",
            r"C:\Program Files (x86)\OpenVPN\bin\openvpn.exe",
        ]
        for c in candidates:
            if os.path.exists(c):
                return c
        return shutil.which("openvpn")

    def _connect_vpn(self, entry):
        def work():
            try:
                path = self._save_ovpn(entry)
            except Exception as e:
                msg = str(e)
                self.after(0, lambda m=msg: self.log_message(f"❌ VPN config error: {m}"))
                return
            exe = self._find_openvpn()
            if not exe:
                self.after(0, lambda: self.log_message(
                    "⚠️ OpenVPN not installed. Opened download page — install the OpenVPN "
                    f"Community client, then connect: {path}"))
                webbrowser.open("https://openvpn.net/community-downloads/")
                try:
                    os.startfile(os.path.dirname(path))
                except Exception:
                    pass
                return
            try:
                if exe.lower().endswith("openvpn-gui.exe"):
                    cfg_dir = os.path.join(os.path.dirname(os.path.dirname(exe)), "config")
                    try:
                        os.makedirs(cfg_dir, exist_ok=True)
                        dest = os.path.join(cfg_dir, os.path.basename(path))
                        shutil.copy(path, dest)
                        subprocess.Popen([exe, "--connect", os.path.basename(path)])
                        self.after(0, lambda: self.log_message(
                            f"🔒 Launching OpenVPN GUI → {entry.get('CountryLong','?')} "
                            f"({entry.get('IP','?')}). Approve the UAC prompt."))
                    except PermissionError:
                        subprocess.Popen([exe])
                        self.after(0, lambda: self.log_message(
                            "🔒 OpenVPN GUI opened. Import the config from your NeoTune_VPN "
                            "folder (config copy needs admin)."))
                else:
                    subprocess.Popen(["powershell", "Start-Process", f'"{exe}"',
                                      "-ArgumentList", f"'--config \"{path}\"'", "-Verb", "RunAs"])
                    self.after(0, lambda: self.log_message(
                        f"🔒 Connecting via OpenVPN CLI → {entry.get('IP','?')} (approve UAC)."))
            except Exception as e:
                msg = str(e)
                self.after(0, lambda m=msg: self.log_message(f"❌ VPN launch error: {m}"))

        threading.Thread(target=work, daemon=True).start()

    # ============================ BANDWIDTH TAB ============================
    def _build_bandwidth_tab(self, tab):
        head = ctk.CTkFrame(tab, fg_color="transparent")
        head.pack(fill="x", padx=12, pady=(12, 4))
        ctk.CTkLabel(head, text="Live Bandwidth Monitor", font=ctk.CTkFont(size=18, weight="bold")).pack(side="left")
        ctk.CTkLabel(tab, text="Real-time download/upload throughput. The same meter lives in your "
                              "system tray icon (hover for exact numbers).",
                     font=ctk.CTkFont(size=11), text_color="#88AACC", wraplength=820,
                     justify="left").pack(anchor="w", padx=14, pady=(0, 6))

        stats = ctk.CTkFrame(tab, fg_color="transparent")
        stats.pack(fill="x", padx=12, pady=6)
        self.bw_down_lbl = ctk.CTkLabel(stats, text="↓ 0 B/s", font=ctk.CTkFont(family="Consolas", size=22, weight="bold"), text_color="#00E696")
        self.bw_down_lbl.pack(side="left", padx=20)
        self.bw_up_lbl = ctk.CTkLabel(stats, text="↑ 0 B/s", font=ctk.CTkFont(family="Consolas", size=22, weight="bold"), text_color="#FF9632")
        self.bw_up_lbl.pack(side="left", padx=20)
        self.bw_total_lbl = ctk.CTkLabel(stats, text="Session: ↓ 0 B  ↑ 0 B", font=ctk.CTkFont(size=12), text_color="#9FB0C0")
        self.bw_total_lbl.pack(side="right", padx=20)

        self.bw_canvas = tk.Canvas(tab, height=220, bg="#050508", highlightthickness=0)
        self.bw_canvas.pack(fill="both", expand=True, padx=14, pady=12)
        self._refresh_bandwidth_panel()

    def _refresh_bandwidth_panel(self):
        if not getattr(self, "bw_canvas", None):
            return
        try:
            if not self.bw_canvas.winfo_exists():
                return
        except Exception:
            return

        self.bw_down_lbl.configure(text=f"↓ {self._fmt_rate(self._bw_down)}")
        self.bw_up_lbl.configure(text=f"↑ {self._fmt_rate(self._bw_up)}")
        self.bw_total_lbl.configure(
            text=f"Session: ↓ {self.format_size(self._bw_total_down)}  ↑ {self.format_size(self._bw_total_up)}")

        c = self.bw_canvas
        c.delete("all")
        w = c.winfo_width() or 800
        h = c.winfo_height() or 220
        hist = list(self._bw_history)
        peak = max(hist) if hist and max(hist) > 0 else 1
        # gridlines
        for i in range(1, 4):
            y = h * i / 4
            c.create_line(0, y, w, y, fill="#15151F")
        c.create_text(6, 8, anchor="nw", fill="#445", text=f"peak {self._fmt_rate(peak)}", font=("Consolas", 9))
        # area plot of download history
        n = len(hist)
        if n > 1:
            step = w / (n - 1)
            pts = []
            for i, v in enumerate(hist):
                x = i * step
                y = h - (v / peak) * (h - 20) - 6
                pts.append((x, y))
            flat = [coord for p in pts for coord in p]
            c.create_line(*flat, fill="#00E696", width=2, smooth=True)
            poly = [0, h] + flat + [w, h]
            c.create_polygon(*poly, fill="#0A3A2A", outline="", stipple="gray50")

        self.after(1000, self._refresh_bandwidth_panel)

    # ======================================================================

    # ======================================================================
    #                            NEO DOCTOR
    #   Deterministic audit engine + guided fixes + local AI advisor.
    #   The checks engine establishes ground truth (psutil/registry/PowerShell).
    #   The LLM only EXPLAINS/PRIORITIZES findings — it never reads raw system
    #   state and never executes fixes. Fixes run from a fixed whitelist.
    # ======================================================================
    SEVERITY_STYLE = {
        "critical": ("#FF4D4D", "🔴", 25),
        "warn":     ("#FFB020", "🟠", 10),
        "info":     ("#4DA6FF", "🔵", 3),
        "ok":       ("#3DD68C", "🟢", 0),
    }
    DOCTOR_CONFIRM = {"run_sfc", "schedule_chkdsk", "winsock_reset"}

    def _finding(self, fid, category, title, severity, detail, fix_action=None, fix_label=None):
        return {"id": fid, "category": category, "title": title, "severity": severity,
                "detail": detail, "fix_action": fix_action, "fix_label": fix_label}

    def _ps(self, script, timeout=30):
        """Run a PowerShell command silently; return (stdout, returncode)."""
        flags = 0x08000000 if platform.system() == "Windows" else 0  # CREATE_NO_WINDOW
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                               capture_output=True, text=True, timeout=timeout, creationflags=flags)
            return (r.stdout or "").strip(), r.returncode
        except Exception:
            return "", -1

    # --------------------------- AUDIT ENGINE ---------------------------
    def run_audit(self):
        findings = []
        checks = (self._chk_disk, self._chk_memory, self._chk_temp, self._chk_startup,
                  self._chk_proxy, self._chk_uac, self._chk_dirty, self._chk_hosts,
                  self._chk_security_probe)
        for chk in checks:
            try:
                res = chk()
                if res is None:
                    continue
                if isinstance(res, list):
                    findings.extend([f for f in res if f])
                else:
                    findings.append(res)
            except Exception as e:
                findings.append(self._finding(
                    "err_" + chk.__name__, "Audit", f"Check error: {chk.__name__}", "info", str(e)))
        return findings

    def _chk_disk(self):
        out = []
        for part in psutil.disk_partitions(all=False):
            opts = (part.opts or "").lower()
            if "cdrom" in opts or part.fstype == "":
                continue
            try:
                u = psutil.disk_usage(part.mountpoint)
            except Exception:
                continue
            free_pct = 100 - u.percent
            if free_pct < 10:
                sev, act, lbl = "critical", "deep_clean", "Deep Clean"
            elif free_pct < 20:
                sev, act, lbl = "warn", "quick_clean", "Quick Clean"
            else:
                sev, act, lbl = "ok", None, None
            out.append(self._finding(
                f"disk_{part.device}", "Storage", f"Drive {part.device} free space", sev,
                f"{self.format_size(u.free)} free of {self.format_size(u.total)} ({free_pct:.0f}% free)",
                act, lbl))
        return out

    def _chk_memory(self):
        vm = psutil.virtual_memory()
        p = vm.percent
        if p > 90:
            sev, act, lbl = "critical", "system_memory_cleanup", "System Memory Cleanup"
        elif p > 80:
            sev, act, lbl = "warn", "trim_memory", "Trim Memory"
        else:
            sev, act, lbl = "ok", None, None
        return self._finding("memory", "Memory", "RAM usage", sev,
                             f"{p:.0f}% used — {self.format_size(vm.available)} available of "
                             f"{self.format_size(vm.total)}", act, lbl)

    def _chk_temp(self):
        size = 0
        for d in self.get_temp_locations():
            try:
                size += self.get_folder_size(d)
            except Exception:
                pass
        gb = size / (1024 ** 3)
        if gb > 5:
            sev, act, lbl = "critical", "deep_clean", "Deep Clean"
        elif gb > 2:
            sev, act, lbl = "warn", "quick_clean", "Quick Clean"
        else:
            sev, act, lbl = "ok", "quick_clean" if size > 0 else None, "Quick Clean" if size > 0 else None
        return self._finding("temp", "Storage", "Temporary files", sev,
                             f"{self.format_size(size)} of cleanable temp data", act, lbl)

    def _chk_startup(self):
        count = 0
        if platform.system() == "Windows":
            try:
                folder = os.path.join(os.environ.get('APPDATA', ''), 'Microsoft', 'Windows',
                                      'Start Menu', 'Programs', 'Startup')
                if os.path.isdir(folder):
                    count += len([f for f in os.listdir(folder) if not f.lower().endswith('.ini')])
            except Exception:
                pass
            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    key = winreg.OpenKey(hive, r"Software\Microsoft\Windows\CurrentVersion\Run")
                    i = 0
                    while True:
                        winreg.EnumValue(key, i)
                        count += 1
                        i += 1
                except Exception:
                    pass
        if count > 20:
            sev = "critical"
        elif count > 12:
            sev = "warn"
        else:
            sev = "ok"
        act = "open_startup_manager" if sev != "ok" else None
        lbl = "Startup Manager" if act else None
        return self._finding("startup", "Boot", "Startup program load", sev,
                             f"{count} programs configured to launch at startup", act, lbl)

    def _chk_proxy(self):
        if platform.system() != "Windows":
            return None
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                 r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            server = ""
            try:
                server, _ = winreg.QueryValueEx(key, "ProxyServer")
            except Exception:
                pass
            winreg.CloseKey(key)
            if enabled:
                return self._finding("proxy", "Network", "System proxy active", "warn",
                                     f"A system-wide proxy is set ({server or 'unknown'}). If you did not "
                                     "set this intentionally, it can break or intercept your connection.",
                                     "clear_system_proxy", "Clear Proxy")
            return self._finding("proxy", "Network", "System proxy", "ok", "No system proxy configured.")
        except Exception:
            return None

    def _chk_uac(self):
        if platform.system() != "Windows":
            return None
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                 r"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System")
            val, _ = winreg.QueryValueEx(key, "EnableLUA")
            winreg.CloseKey(key)
            if val == 1:
                return self._finding("uac", "Security", "User Account Control (UAC)", "ok", "UAC is enabled.")
            return self._finding("uac", "Security", "User Account Control (UAC)", "warn",
                                 "UAC is disabled, which lowers protection against unwanted changes. "
                                 "Re-enable it in Windows Security settings (manual).")
        except Exception:
            return None

    def _chk_dirty(self):
        if platform.system() != "Windows":
            return None
        try:
            r = subprocess.run("fsutil dirty query C:", shell=True, capture_output=True, text=True,
                               creationflags=0x08000000)
            txt = (r.stdout or "").lower()
            if "is dirty" in txt and "not dirty" not in txt:
                return self._finding("dirty", "Storage", "Disk dirty bit (C:)", "warn",
                                     "Windows flagged drive C: as dirty — a disk check is recommended.",
                                     "schedule_chkdsk", "Schedule CHKDSK")
            return self._finding("dirty", "Storage", "Disk integrity flag (C:)", "ok",
                                 "Drive C: is not flagged dirty.")
        except Exception:
            return None

    def _chk_hosts(self):
        if platform.system() != "Windows":
            return None
        try:
            hosts = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'),
                                 'System32', 'drivers', 'etc', 'hosts')
            if not os.path.exists(hosts):
                return None
            redirects = []
            with open(hosts, "r", errors="ignore") as f:
                for line in f:
                    s = line.strip()
                    if not s or s.startswith("#"):
                        continue
                    parts = s.split()
                    if len(parts) >= 2 and not parts[0].startswith(("127.", "::1", "0.0.0.0")):
                        redirects.append(s)
            if redirects:
                return self._finding("hosts", "Security", "Hosts file redirects", "info",
                                     f"{len(redirects)} active redirect entr"
                                     f"{'y' if len(redirects) == 1 else 'ies'} in the hosts file. "
                                     "Review them if you did not add them (manual).")
            return self._finding("hosts", "Security", "Hosts file", "ok", "No suspicious redirects found.")
        except Exception:
            return None

    def _security_probe(self):
        """One combined PowerShell call for Defender/Firewall/SMART/PnP/reboot state."""
        if platform.system() != "Windows":
            return {}
        script = (
            "$o=[ordered]@{};"
            "try{$m=Get-MpComputerStatus;$o.RtpEnabled=$m.RealTimeProtectionEnabled;"
            "$o.SigAge=$m.AntivirusSignatureAge;$o.AmEnabled=$m.AntivirusEnabled}catch{};"
            "try{$o.Firewall=@(Get-NetFirewallProfile|ForEach-Object{\"$($_.Name)=$($_.Enabled)\"})}catch{};"
            "try{$o.DiskHealth=@(Get-PhysicalDisk|Select-Object -ExpandProperty HealthStatus)}catch{};"
            "try{$o.PnpErrors=@(Get-PnpDevice -PresentOnly|Where-Object{$_.Status -eq 'Error'}).Count}catch{};"
            "try{$o.PendingReboot=(Test-Path 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\"
            "Component Based Servicing\\RebootPending')}catch{};"
            "$o|ConvertTo-Json -Compress"
        )
        out, _ = self._ps(script, timeout=40)
        try:
            return json.loads(out) if out else {}
        except Exception:
            return {}

    def _chk_security_probe(self):
        data = self._security_probe()
        if not data:
            if platform.system() == "Windows":
                return self._finding("secprobe", "Security", "Security probe", "info",
                                     "Could not read Defender/Firewall status (PowerShell unavailable "
                                     "or blocked).")
            return None
        out = []
        rtp = data.get("RtpEnabled")
        if rtp is False:
            out.append(self._finding("def_rtp", "Security", "Defender real-time protection", "critical",
                                     "Windows Defender real-time protection is OFF — your PC is exposed.",
                                     "enable_defender_rt", "Turn On Protection"))
        elif rtp is True:
            out.append(self._finding("def_rtp", "Security", "Defender real-time protection", "ok",
                                     "Real-time protection is on."))
        age = data.get("SigAge")
        if isinstance(age, (int, float)):
            if age > 7:
                out.append(self._finding("def_sig", "Security", "Antivirus definitions", "warn",
                                         f"Virus definitions are {int(age)} days old.",
                                         "update_defender_sigs", "Update Definitions"))
            else:
                out.append(self._finding("def_sig", "Security", "Antivirus definitions", "ok",
                                         f"Definitions are current ({int(age)} day(s) old)."))
        fw = data.get("Firewall") or []
        off = [p.split("=")[0] for p in fw if p.lower().endswith("false")]
        if off:
            out.append(self._finding("firewall", "Security", "Windows Firewall", "warn",
                                     f"Firewall is OFF for: {', '.join(off)}.",
                                     "enable_firewall", "Enable Firewall"))
        elif fw:
            out.append(self._finding("firewall", "Security", "Windows Firewall", "ok",
                                     "Firewall is enabled on all profiles."))
        health = data.get("DiskHealth") or []
        bad = [h for h in health if str(h).lower() != "healthy"]
        if bad:
            out.append(self._finding("smart", "Storage", "Physical disk health", "critical",
                                     f"A drive reports health status: {', '.join(map(str, bad))}. "
                                     "Back up important data now."))
        elif health:
            out.append(self._finding("smart", "Storage", "Physical disk health", "ok",
                                     "All physical disks report Healthy."))
        pnp = data.get("PnpErrors")
        if isinstance(pnp, (int, float)) and pnp > 0:
            out.append(self._finding("pnp", "Devices", "Device/driver errors", "info",
                                     f"{int(pnp)} device(s) report an error in Device Manager. "
                                     "Update or reinstall their drivers (manual)."))
        if data.get("PendingReboot"):
            out.append(self._finding("reboot", "System", "Pending reboot", "info",
                                     "Windows has updates waiting for a restart to finish installing."))
        return out

    def _health_score(self, findings):
        penalty = sum(self.SEVERITY_STYLE.get(f["severity"], (None, None, 0))[2] for f in findings)
        return max(0, min(100, 100 - penalty))

    # --------------------------- FIX EXECUTOR ---------------------------
    def _doctor_fix(self, action_id):
        fixers = {
            "quick_clean": self.quick_clean,
            "deep_clean": self.deep_clean,
            "analyze_space": self.analyze_space,
            "trim_memory": self.trim_memory,
            "system_memory_cleanup": self.system_memory_cleanup,
            "flush_dns": self.flush_dns,
            "open_startup_manager": self.open_startup_manager,
            "clear_system_proxy": self._clear_system_proxy,
            "enable_defender_rt": self._fix_enable_defender_rt,
            "update_defender_sigs": self._fix_update_defender_sigs,
            "defender_quickscan": self._fix_defender_quickscan,
            "enable_firewall": self._fix_enable_firewall,
            "run_sfc": self._fix_run_sfc,
            "schedule_chkdsk": self._fix_schedule_chkdsk,
            "winsock_reset": self._fix_winsock_reset,
        }
        fn = fixers.get(action_id)
        if not fn:
            self.log_message(f"⚠️ Unknown fix action: {action_id}")
            return
        fn()

    def _doctor_fix_clicked(self, action_id, label):
        if action_id in self.DOCTOR_CONFIRM:
            try:
                ok = messagebox.askyesno("NEO DOCTOR — Confirm",
                                         f"Run: {label}?\n\nThis may take several minutes or require a reboot.",
                                         parent=self.doctor_win)
            except Exception:
                ok = True
            if not ok:
                return
        self._doctor_fix(action_id)
        if action_id not in self.DOCTOR_CONFIRM:
            self.after(2500, self._doctor_refresh)

    def _run_admin_ps(self, script, success_msg, timeout=180):
        if not self.is_admin:
            self.log_message("⚠️ This fix requires Administrator rights")
            return
        self.log_message(f"🔧 {success_msg} — working...")

        def w():
            out, rc = self._ps(script, timeout=timeout)
            ok = rc == 0
            self.after(0, lambda: self.log_message(
                f"✅ {success_msg}" if ok else f"⚠️ {success_msg} (code {rc}) {out[:200]}"))
        threading.Thread(target=w, daemon=True).start()

    def _run_admin_cmd(self, cmd, success_msg, timeout=120):
        if not self.is_admin:
            self.log_message("⚠️ This fix requires Administrator rights")
            return
        self.log_message(f"🔧 {success_msg} — working...")

        def w():
            try:
                r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout,
                                   creationflags=0x08000000)
                ok = r.returncode == 0
                tail = ((r.stdout or "") + (r.stderr or "")).strip()[-200:]
                self.after(0, lambda: self.log_message(
                    f"✅ {success_msg}" if ok else f"⚠️ {success_msg} (code {r.returncode}) {tail}"))
            except Exception as e:
                emsg = str(e)
                self.after(0, lambda m=emsg: self.log_message(f"❌ {success_msg} error: {m}"))
        threading.Thread(target=w, daemon=True).start()

    def _fix_enable_defender_rt(self):
        self._run_admin_ps("Set-MpPreference -DisableRealtimeMonitoring $false",
                           "Defender real-time protection enabled")

    def _fix_update_defender_sigs(self):
        self._run_admin_ps("Update-MpSignature", "Defender definitions updated", timeout=300)

    def _fix_defender_quickscan(self):
        self._run_admin_ps("Start-MpScan -ScanType QuickScan", "Defender quick scan started", timeout=600)

    def _fix_enable_firewall(self):
        self._run_admin_cmd("netsh advfirewall set allprofiles state on",
                            "Windows Firewall enabled on all profiles")

    def _fix_winsock_reset(self):
        self._run_admin_cmd("netsh winsock reset", "Winsock catalog reset (reboot required)")

    def _fix_schedule_chkdsk(self):
        self._run_admin_cmd("echo Y| chkdsk C: /f /r", "CHKDSK scheduled for next reboot")

    def _fix_run_sfc(self):
        if not self.is_admin:
            self.log_message("⚠️ sfc /scannow requires Administrator rights")
            return
        self.log_message("🔧 Running sfc /scannow — this can take several minutes...")

        def w():
            try:
                r = subprocess.run("sfc /scannow", shell=True, capture_output=True, text=True,
                                   timeout=1800, creationflags=0x08000000)
                tail = (r.stdout or "").replace("\x00", "").strip()[-300:]
                self.after(0, lambda: self.log_message(f"🔧 SFC complete (code {r.returncode}). {tail}"))
            except Exception as e:
                emsg = str(e)
                self.after(0, lambda m=emsg: self.log_message(f"❌ SFC error: {m}"))
        threading.Thread(target=w, daemon=True).start()

    # --------------------------- AI ADVISOR -----------------------------
    def _doctor_ai_prompt(self, findings):
        lines = []
        for f in findings:
            if f["severity"] == "ok":
                continue
            fix = f" [available fix: {f['fix_label']}]" if f.get("fix_label") else ""
            lines.append(f"- ({f['severity'].upper()}) {f['title']}: {f['detail']}{fix}")
        if not lines:
            return "All diagnostic checks passed. No issues detected."
        return "Diagnostic findings collected by the app:\n" + "\n".join(lines)

    DOCTOR_SYSTEM = (
        "You are NEO DOCTOR, a Windows PC health assistant inside the NEO TUNE app. "
        "You are given a list of diagnostic findings already collected by the app's checks engine. "
        "Explain the important issues in plain, friendly language for a non-technical user, "
        "prioritize them from most to least urgent, and briefly say what each recommended fix does. "
        "Only use the findings provided — do not invent system details, specs, or issues. "
        "Be concise: a short prioritized list, then one closing sentence on overall PC health."
    )

    def _rule_based_summary(self, findings):
        groups = {"critical": [], "warn": [], "info": []}
        for f in findings:
            if f["severity"] in groups:
                groups[f["severity"]].append(f)
        if not any(groups.values()):
            return "✅ No issues found — your PC looks healthy. Everything checked came back green."
        parts, headers = [], {"critical": "🔴 Critical — fix these first:",
                              "warn": "🟠 Warnings — worth addressing:",
                              "info": "🔵 For your information:"}
        for sev in ("critical", "warn", "info"):
            if groups[sev]:
                parts.append(headers[sev])
                for f in groups[sev]:
                    tip = f"   → {f['fix_label']}" if f.get("fix_label") else ""
                    parts.append(f"   • {f['title']} — {f['detail']}{tip}")
                parts.append("")
        parts.append("Tip: use the Fix buttons on the cards above, or press "
                     "'🧠 Explain & Prioritize' for an AI walkthrough.")
        return "\n".join(parts).strip()

    def _ai_chat(self, system_text, user_text):
        provider = self.ai_provider_var.get()
        endpoint = self.ai_endpoint_var.get().rstrip("/")
        model = self.ai_model_var.get().strip()
        try:
            if provider == "Ollama":
                body = json.dumps({"model": model, "stream": False,
                                   "messages": [{"role": "system", "content": system_text},
                                                {"role": "user", "content": user_text}]}).encode()
                req = urllib.request.Request(endpoint + "/api/chat", data=body,
                                             headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=180) as r:
                    data = json.loads(r.read())
                return data.get("message", {}).get("content", "").strip(), None
            elif provider == "LM Studio":
                body = json.dumps({"model": model, "stream": False, "temperature": 0.3, "max_tokens": 800,
                                   "messages": [{"role": "system", "content": system_text},
                                                {"role": "user", "content": user_text}]}).encode()
                req = urllib.request.Request(endpoint + "/v1/chat/completions", data=body,
                                             headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=180) as r:
                    data = json.loads(r.read())
                return data["choices"][0]["message"]["content"].strip(), None
            elif provider == "Anthropic":
                key = self.ai_key_var.get().strip()
                if not key:
                    return None, "No Anthropic API key set (Settings field is empty)."
                body = json.dumps({"model": model, "max_tokens": 800, "system": system_text,
                                   "messages": [{"role": "user", "content": user_text}]}).encode()
                req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body,
                                             headers={"Content-Type": "application/json",
                                                      "x-api-key": key,
                                                      "anthropic-version": "2023-06-01"})
                with urllib.request.urlopen(req, timeout=120) as r:
                    data = json.loads(r.read())
                return "".join(b.get("text", "") for b in data.get("content", [])).strip(), None
            return None, f"Unknown provider: {provider}"
        except Exception as e:
            return None, str(e)

    def _ai_ping(self):
        provider = self.ai_provider_var.get()
        endpoint = self.ai_endpoint_var.get().rstrip("/")
        try:
            if provider == "Ollama":
                self._net_http_get(endpoint + "/api/tags", timeout=6)
                return True, "Ollama is reachable."
            if provider == "LM Studio":
                self._net_http_get(endpoint + "/v1/models", timeout=6)
                return True, "LM Studio is reachable."
            key = self.ai_key_var.get().strip()
            return (bool(key), "Anthropic key is set (validated on first request)."
                    if key else "Anthropic: no API key set.")
        except Exception as e:
            return False, f"{provider} not reachable: {e}"

    def _ai_test_clicked(self):
        def w():
            ok, msg = self._ai_ping()
            self.after(0, lambda: self.log_message(("✅ " if ok else "⚠️ ") + msg))
        threading.Thread(target=w, daemon=True).start()

    def _ai_load_models(self):
        provider = self.ai_provider_var.get()
        endpoint = self.ai_endpoint_var.get().rstrip("/")

        def w():
            names = []
            try:
                if provider == "Ollama":
                    d = json.loads(self._net_http_get(endpoint + "/api/tags", timeout=8))
                    names = [m["name"] for m in d.get("models", [])]
                elif provider == "LM Studio":
                    d = json.loads(self._net_http_get(endpoint + "/v1/models", timeout=8))
                    names = [m["id"] for m in d.get("data", [])]
                else:
                    self.after(0, lambda: self.log_message("ℹ️ Model list isn't available for Anthropic — "
                                                           "type the model name manually."))
                    return
            except Exception as e:
                emsg = str(e)
                self.after(0, lambda m=emsg: self.log_message(f"⚠️ Could not load models: {m}"))
                return
            if names:
                self.after(0, lambda: self.ai_model_combo.configure(values=names))
                self.after(0, lambda: self.ai_model_var.set(names[0]))
                self.after(0, lambda: self.log_message(f"📥 Loaded {len(names)} model(s) from {provider}"))
            else:
                self.after(0, lambda: self.log_message(f"ℹ️ No models found on {provider}"))
        threading.Thread(target=w, daemon=True).start()

    def _on_ai_provider_change(self, provider):
        endpoints = {"Ollama": "http://localhost:11434", "LM Studio": "http://localhost:1234",
                     "Anthropic": "https://api.anthropic.com"}
        models = {"Ollama": "llama3.2", "LM Studio": "local-model", "Anthropic": "claude-sonnet-4-6"}
        self.ai_endpoint_var.set(endpoints.get(provider, ""))
        self.ai_model_var.set(models.get(provider, ""))
        try:
            if provider == "Anthropic":
                self.ai_key_entry.configure(state="normal")
            else:
                self.ai_key_entry.configure(state="disabled")
        except Exception:
            pass

    def _explain_and_prioritize(self):
        findings = self.doctor_findings
        if not findings:
            self.log_message("Run an audit first.")
            return
        prompt = self._doctor_ai_prompt(findings)
        provider = self.ai_provider_var.get()
        self._set_ai_box(f"🧠 Asking {provider} to analyze {len([f for f in findings if f['severity'] != 'ok'])} "
                         "issue(s)...\n")

        def w():
            text, err = self._ai_chat(self.DOCTOR_SYSTEM, prompt)
            if err or not text:
                fallback = self._rule_based_summary(findings)
                msg = (f"⚠️ AI provider unavailable ({err}).\n"
                       "Showing NEO DOCTOR's built-in analysis instead:\n\n" + fallback)
                self.after(0, lambda: self._set_ai_box(msg))
            else:
                self.after(0, lambda: self._set_ai_box("🧠 " + provider + " analysis:\n\n" + text))
        threading.Thread(target=w, daemon=True).start()

    def _set_ai_box(self, text):
        try:
            self.doctor_ai_box.configure(state="normal")
            self.doctor_ai_box.delete("1.0", "end")
            self.doctor_ai_box.insert("1.0", text)
            self.doctor_ai_box.configure(state="disabled")
        except Exception:
            pass

    # --------------------------- DOCTOR WINDOW --------------------------
    def open_doctor(self):
        if getattr(self, "doctor_win", None) is not None:
            try:
                if self.doctor_win.winfo_exists():
                    self.doctor_win.lift()
                    self.doctor_win.focus_force()
                    return
            except Exception:
                pass

        win = ctk.CTkToplevel(self)
        self.doctor_win = win
        win.title("🩺 NEO DOCTOR — Health & Security Audit")
        win.geometry("960x740")
        win.attributes('-topmost', True)
        win.after(400, lambda: win.attributes('-topmost', False))

        # Header row: title + score + run button
        header = ctk.CTkFrame(win, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(14, 6))
        ctk.CTkLabel(header, text="🩺 NEO DOCTOR", font=ctk.CTkFont(size=22, weight="bold"),
                     text_color="#00FFCC").pack(side="left")
        self.doctor_score_lbl = ctk.CTkLabel(header, text="—", font=ctk.CTkFont(size=22, weight="bold"))
        self.doctor_score_lbl.pack(side="right", padx=10)
        ctk.CTkLabel(header, text="Health Score:", font=ctk.CTkFont(size=12),
                     text_color="#9FB0C0").pack(side="right")
        ctk.CTkButton(header, text="🔍 Run Audit", width=110, command=self._doctor_refresh).pack(side="right", padx=12)

        if not self.is_admin:
            ctk.CTkLabel(win, text="⚠️ Run NEO TUNE as Administrator to apply security/system fixes.",
                         text_color="#FFAA55", font=ctk.CTkFont(size=11)).pack(anchor="w", padx=18)

        # AI advisor settings row
        ai_bar = ctk.CTkFrame(win, fg_color="#0C0C12")
        ai_bar.pack(fill="x", padx=16, pady=8)
        ctk.CTkLabel(ai_bar, text="AI:", font=ctk.CTkFont(size=12, weight="bold")).pack(side="left", padx=(10, 4), pady=8)
        prov = ctk.CTkOptionMenu(ai_bar, values=["Ollama", "LM Studio", "Anthropic"],
                                 variable=self.ai_provider_var, width=110, command=self._on_ai_provider_change)
        prov.pack(side="left", padx=4)
        self.ai_model_combo = ctk.CTkComboBox(ai_bar, values=[self.ai_model_var.get()],
                                              variable=self.ai_model_var, width=180)
        self.ai_model_combo.pack(side="left", padx=4)
        ctk.CTkButton(ai_bar, text="↻", width=32, command=self._ai_load_models).pack(side="left", padx=2)
        ctk.CTkButton(ai_bar, text="Test", width=56, command=self._ai_test_clicked).pack(side="left", padx=4)
        ctk.CTkButton(ai_bar, text="🧠 Explain & Prioritize", width=170, fg_color="#0E7C66",
                      hover_color="#0A5E4D", command=self._explain_and_prioritize).pack(side="right", padx=8)

        ep_bar = ctk.CTkFrame(win, fg_color="transparent")
        ep_bar.pack(fill="x", padx=16, pady=(0, 4))
        ctk.CTkLabel(ep_bar, text="Endpoint:", font=ctk.CTkFont(size=11),
                     text_color="#9FB0C0").pack(side="left", padx=(10, 4))
        ctk.CTkEntry(ep_bar, textvariable=self.ai_endpoint_var, width=240).pack(side="left", padx=4)
        ctk.CTkLabel(ep_bar, text="API key (Anthropic):", font=ctk.CTkFont(size=11),
                     text_color="#9FB0C0").pack(side="left", padx=(12, 4))
        self.ai_key_entry = ctk.CTkEntry(ep_bar, textvariable=self.ai_key_var, width=220, show="•",
                                         state="disabled")
        self.ai_key_entry.pack(side="left", padx=4)

        # Findings list
        self.doctor_list = ctk.CTkScrollableFrame(win, fg_color="#0C0C12", height=300)
        self.doctor_list.pack(fill="both", expand=True, padx=16, pady=(8, 6))

        # AI / summary output
        ctk.CTkLabel(win, text="Analysis", font=ctk.CTkFont(size=12, weight="bold"),
                     text_color="#9FB0C0").pack(anchor="w", padx=18)
        self.doctor_ai_box = ctk.CTkTextbox(win, height=150, font=ctk.CTkFont(family="Consolas", size=12),
                                            fg_color="#050508")
        self.doctor_ai_box.pack(fill="x", padx=16, pady=(2, 14))
        self.doctor_ai_box.configure(state="disabled")

        self._on_ai_provider_change(self.ai_provider_var.get())
        self._doctor_refresh()

    def _doctor_refresh(self):
        if not getattr(self, "doctor_win", None):
            return
        try:
            if not self.doctor_win.winfo_exists():
                return
        except Exception:
            return
        self.doctor_score_lbl.configure(text="…", text_color="#88AACC")
        for w in self.doctor_list.winfo_children():
            w.destroy()
        ctk.CTkLabel(self.doctor_list, text="Running diagnostics…",
                     font=ctk.CTkFont(size=13)).pack(pady=24)

        def work():
            findings = self.run_audit()
            self.doctor_findings = findings
            self.after(0, lambda: self._doctor_render(findings))
        threading.Thread(target=work, daemon=True).start()

    def _doctor_render(self, findings):
        try:
            if not self.doctor_win.winfo_exists():
                return
        except Exception:
            return
        score = self._health_score(findings)
        color = "#3DD68C" if score >= 80 else ("#FFB020" if score >= 50 else "#FF4D4D")
        self.doctor_score_lbl.configure(text=f"{score}/100", text_color=color)

        for w in self.doctor_list.winfo_children():
            w.destroy()

        order = {"critical": 0, "warn": 1, "info": 2, "ok": 3}
        for f in sorted(findings, key=lambda x: order.get(x["severity"], 9)):
            c, emoji, _ = self.SEVERITY_STYLE.get(f["severity"], ("#888", "•", 0))
            card = ctk.CTkFrame(self.doctor_list, fg_color="#15151F", border_width=1, border_color="#23232F")
            card.pack(fill="x", pady=4, padx=4)
            left = ctk.CTkFrame(card, fg_color="transparent")
            left.pack(side="left", fill="x", expand=True, padx=10, pady=8)
            ctk.CTkLabel(left, text=f"{emoji}  {f['title']}", anchor="w",
                         font=ctk.CTkFont(size=14, weight="bold"), text_color=c).pack(anchor="w")
            ctk.CTkLabel(left, text=f"{f['category']} · {f['detail']}", anchor="w", justify="left",
                         font=ctk.CTkFont(size=11), text_color="#AEB9C6", wraplength=640).pack(anchor="w", pady=(2, 0))
            if f.get("fix_action"):
                ctk.CTkButton(card, text=f"Fix · {f['fix_label']}", width=150, fg_color=c, text_color="#0A0A0A",
                              hover_color="#FFFFFF",
                              command=lambda a=f["fix_action"], l=f["fix_label"]: self._doctor_fix_clicked(a, l)
                              ).pack(side="right", padx=10, pady=8)

        self._set_ai_box(self._rule_based_summary(findings))

    # ======================================================================

    # ======================================================================
    #                          TOP PROCESSES MONITOR
    #   Opened from the CPU dashboard card. Lists processes by CPU or RAM,
    #   refreshes live, and can end a task (with a protected-process guard).
    # ======================================================================
    PROTECTED_PROCS = {
        "system", "system idle process", "registry", "memory compression",
        "csrss.exe", "wininit.exe", "winlogon.exe", "services.exe",
        "lsass.exe", "smss.exe", "explorer.exe", "dwm.exe",
    }

    def open_top_processes(self):
        if getattr(self, "proc_win", None) is not None:
            try:
                if self.proc_win.winfo_exists():
                    self.proc_win.lift()
                    self.proc_win.focus_force()
                    return
            except Exception:
                pass

        win = ctk.CTkToplevel(self)
        self.proc_win = win
        win.title("📈 Top Processes")
        win.geometry("700x580")
        win.attributes('-topmost', True)
        win.after(400, lambda: win.attributes('-topmost', False))

        bar = ctk.CTkFrame(win, fg_color="transparent")
        bar.pack(fill="x", padx=14, pady=(12, 6))
        ctk.CTkLabel(bar, text="📈 Top Processes", font=ctk.CTkFont(size=18, weight="bold"),
                     text_color="#00FFCC").pack(side="left")
        self._proc_sort = "CPU"
        seg = ctk.CTkSegmentedButton(bar, values=["CPU", "RAM"], command=self._set_proc_sort, width=160)
        seg.set("CPU")
        seg.pack(side="right", padx=6)
        ctk.CTkLabel(bar, text="Sort by:", text_color="#9FB0C0").pack(side="right", padx=(0, 4))

        self._setup_tree_style()
        wrap = tk.Frame(win, bg="#0C0C12")
        wrap.pack(fill="both", expand=True, padx=14, pady=8)
        wrap.grid_rowconfigure(0, weight=1)
        wrap.grid_columnconfigure(0, weight=1)

        self.proc_tree = ttk.Treeview(wrap, columns=("pid", "cpu", "mem"), style="Neo.Treeview",
                                      selectmode="browse")
        self.proc_tree.heading("#0", text="Process", anchor="w")
        self.proc_tree.heading("pid", text="PID", anchor="e")
        self.proc_tree.heading("cpu", text="CPU %", anchor="e")
        self.proc_tree.heading("mem", text="Memory", anchor="e")
        self.proc_tree.column("#0", width=320, anchor="w")
        self.proc_tree.column("pid", width=80, anchor="e", stretch=False)
        self.proc_tree.column("cpu", width=90, anchor="e", stretch=False)
        self.proc_tree.column("mem", width=120, anchor="e", stretch=False)
        self.proc_tree.grid(row=0, column=0, sticky="nsew")
        vsb = ttk.Scrollbar(wrap, orient="vertical", command=self.proc_tree.yview)
        self.proc_tree.configure(yscrollcommand=vsb.set)
        vsb.grid(row=0, column=1, sticky="ns")

        actions = ctk.CTkFrame(win, fg_color="transparent")
        actions.pack(fill="x", padx=14, pady=(2, 6))
        ctk.CTkButton(actions, text="⟳ Refresh", width=100, command=self._refresh_processes).pack(side="left", padx=4)
        ctk.CTkButton(actions, text="⛔ End Task", width=110, fg_color="#7C2A2A",
                      hover_color="#5E1F1F", command=self._end_selected_process).pack(side="right", padx=4)
        self.proc_status = ctk.CTkLabel(win, text="Priming CPU counters…", font=ctk.CTkFont(size=11),
                                        text_color="#88AACC", anchor="w")
        self.proc_status.pack(fill="x", padx=16, pady=(0, 10))

        # Prime per-process CPU counters (first reading is otherwise 0)
        try:
            for p in psutil.process_iter():
                try:
                    p.cpu_percent(None)
                except Exception:
                    pass
            psutil.cpu_percent(None)
        except Exception:
            pass

        self._proc_running = True
        win.protocol("WM_DELETE_WINDOW", self._close_proc_win)
        self.after(900, self._refresh_processes)

    def _close_proc_win(self):
        self._proc_running = False
        try:
            self.proc_win.destroy()
        except Exception:
            pass

    def _set_proc_sort(self, value):
        self._proc_sort = value
        self._refresh_processes()

    def _refresh_processes(self):
        if not getattr(self, "_proc_running", False):
            return
        try:
            if not self.proc_win.winfo_exists():
                return
        except Exception:
            return
        ncpu = psutil.cpu_count() or 1
        rows = []
        for p in psutil.process_iter(['pid', 'name', 'memory_info']):
            try:
                cpu = p.cpu_percent(None) / ncpu
                mi = p.info.get('memory_info')
                mem = mi.rss if mi else 0
                rows.append((p.info['pid'], p.info.get('name') or '?', cpu, mem))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            except Exception:
                continue
        rows.sort(key=lambda r: (r[3] if self._proc_sort == "RAM" else r[2]), reverse=True)
        top = rows[:60]

        sel = self.proc_tree.focus()
        self.proc_tree.delete(*self.proc_tree.get_children())
        for pid, name, cpu, mem in top:
            iid = str(pid)
            self.proc_tree.insert("", "end", iid=iid, text=f"  {name}",
                                  values=(pid, f"{cpu:.1f}", self.format_size(mem)))
        if sel and self.proc_tree.exists(sel):
            self.proc_tree.selection_set(sel)
            self.proc_tree.focus(sel)
        try:
            total_cpu = psutil.cpu_percent(None)
            self.proc_status.configure(
                text=f"{len(rows)} processes · system CPU {total_cpu:.0f}% · sorted by {self._proc_sort} "
                     "· updates every 2s")
        except Exception:
            pass
        self.after(2000, self._refresh_processes)

    def _end_selected_process(self):
        iid = self.proc_tree.focus()
        if not iid:
            messagebox.showinfo("End Task", "Select a process first.", parent=self.proc_win)
            return
        try:
            pid = int(iid)
            p = psutil.Process(pid)
            name = p.name()
        except Exception as e:
            messagebox.showerror("End Task", f"Process unavailable: {e}", parent=self.proc_win)
            return
        if pid in (0, 4) or name.lower() in self.PROTECTED_PROCS:
            messagebox.showerror("Protected process",
                                 f"'{name}' is a critical system process and cannot be ended from here.",
                                 parent=self.proc_win)
            return
        if not messagebox.askyesno("End Task",
                                   f"End process '{name}' (PID {pid})?\n\n"
                                   "Any unsaved work in that program will be lost.",
                                   icon="warning", parent=self.proc_win):
            return
        try:
            p.terminate()
            try:
                p.wait(timeout=3)
            except Exception:
                p.kill()
            self.log_message(f"⛔ Ended process: {name} (PID {pid})")
        except psutil.AccessDenied:
            self.log_message(f"⚠️ Access denied ending {name} (PID {pid})")
            messagebox.showwarning("Access denied",
                                   f"Could not end '{name}'. Try running NEO TUNE as Administrator.",
                                   parent=self.proc_win)
        except Exception as e:
            self.log_message(f"❌ End task error: {e}")
        self._refresh_processes()

    # ======================================================================
    #                      ADVANCED DISK SCANNER  (Ridnacs-style)
    #   Recursively measures file/folder sizes into a sortable tree with
    #   percentage bars, and can delete to Recycle Bin or permanently.
    #   Destructive actions require confirmation and are blocked on critical
    #   system paths.
    # ======================================================================
    def open_disk_scanner(self, scan_path=None):
        if getattr(self, "scan_win", None) is not None:
            try:
                if self.scan_win.winfo_exists():
                    self.scan_win.lift()
                    self.scan_win.focus_force()
                    if scan_path:
                        self.scan_path_var.set(scan_path)
                        self._start_scan()
                    return
            except Exception:
                pass

        win = ctk.CTkToplevel(self)
        self.scan_win = win
        win.title("🔬 NEO DISK SCANNER")
        win.geometry("1000x720")
        win.attributes('-topmost', True)
        win.after(400, lambda: win.attributes('-topmost', False))

        # --- Path / scan controls ---
        top = ctk.CTkFrame(win, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=(14, 6))
        self.scan_path_var = ctk.StringVar(value=os.path.expanduser("~"))
        ctk.CTkEntry(top, textvariable=self.scan_path_var, width=420).pack(side="left", padx=(0, 6))
        ctk.CTkButton(top, text="📁 Browse", width=90, command=self._scan_browse).pack(side="left", padx=3)
        self.scan_btn = ctk.CTkButton(top, text="🔍 Scan", width=90, command=self._start_scan)
        self.scan_btn.pack(side="left", padx=3)
        self.scan_stop_btn = ctk.CTkButton(top, text="⏹ Stop", width=80, fg_color="#7C2A2A",
                                           hover_color="#5E1F1F", command=self._stop_scan, state="disabled")
        self.scan_stop_btn.pack(side="left", padx=3)

        # Drive quick-buttons
        drives = ctk.CTkFrame(win, fg_color="transparent")
        drives.pack(fill="x", padx=16, pady=(0, 4))
        ctk.CTkLabel(drives, text="Drives:", font=ctk.CTkFont(size=11), text_color="#9FB0C0").pack(side="left", padx=(0, 6))
        try:
            for part in psutil.disk_partitions(all=False):
                if "cdrom" in (part.opts or "").lower() or part.fstype == "":
                    continue
                dev = part.device
                ctk.CTkButton(drives, text=dev, width=54, height=26, fg_color="#1A1A2A",
                              command=lambda d=part.mountpoint: self._scan_set_and_go(d)).pack(side="left", padx=2)
        except Exception:
            pass

        # --- Progress / status ---
        self.scan_progress = ctk.CTkProgressBar(win, height=6)
        self.scan_progress.pack(fill="x", padx=16, pady=(4, 2))
        self.scan_progress.set(0)
        self.scan_status_lbl = ctk.CTkLabel(win, text="Idle. Pick a folder or drive and press Scan.",
                                            font=ctk.CTkFont(size=11), text_color="#88AACC", anchor="w")
        self.scan_status_lbl.pack(fill="x", padx=18)

        # --- Tree ---
        self._setup_tree_style()
        tree_wrap = tk.Frame(win, bg="#0C0C12")
        tree_wrap.pack(fill="both", expand=True, padx=16, pady=8)
        tree_wrap.grid_rowconfigure(0, weight=1)
        tree_wrap.grid_columnconfigure(0, weight=1)

        self.scan_tree = ttk.Treeview(tree_wrap, columns=("size", "pct", "items"),
                                      style="Neo.Treeview", selectmode="browse")
        self.scan_tree.heading("#0", text="Name", anchor="w")
        self.scan_tree.heading("size", text="Size", anchor="e")
        self.scan_tree.heading("pct", text="% of parent", anchor="w")
        self.scan_tree.heading("items", text="Files", anchor="e")
        self.scan_tree.column("#0", width=440, anchor="w")
        self.scan_tree.column("size", width=110, anchor="e", stretch=False)
        self.scan_tree.column("pct", width=190, anchor="w", stretch=False)
        self.scan_tree.column("items", width=80, anchor="e", stretch=False)
        self.scan_tree.grid(row=0, column=0, sticky="nsew")

        vsb = ttk.Scrollbar(tree_wrap, orient="vertical", command=self.scan_tree.yview)
        hsb = ttk.Scrollbar(tree_wrap, orient="horizontal", command=self.scan_tree.xview)
        self.scan_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        self.scan_tree.bind("<<TreeviewOpen>>", self._on_scan_open)
        self.scan_tree.bind("<<TreeviewSelect>>", self._on_scan_select)
        self.scan_tree.bind("<Double-1>", self._on_scan_doubleclick)
        self.scan_tree.bind("<Delete>", lambda e: self._scan_delete(False))
        self.scan_tree.bind("<Button-3>", self._scan_right_click)

        # Right-click context menu
        self.scan_menu = tk.Menu(self.scan_tree, tearoff=0, bg="#15151F", fg="#CCDDEE",
                                 activebackground="#1F6F5C", activeforeground="#FFFFFF",
                                 bd=0, font=("Segoe UI", 10))
        self.scan_menu.add_command(label="🤖  Ask assistant: what is this? Safe to delete?",
                                   command=self._scan_ask_assistant)
        self.scan_menu.add_separator()
        self.scan_menu.add_command(label="📂  Open", command=self._scan_open_item)
        self.scan_menu.add_command(label="🗂  Reveal in Explorer", command=self._scan_reveal)
        self.scan_menu.add_separator()
        self.scan_menu.add_command(label="🗑  Delete (Recycle Bin)", command=lambda: self._scan_delete(False))
        self.scan_menu.add_command(label="⚠  Delete Permanently", command=lambda: self._scan_delete(True))

        # --- Selected info + actions ---
        self.scan_sel_lbl = ctk.CTkLabel(win, text="No selection", font=ctk.CTkFont(family="Consolas", size=11),
                                         text_color="#AEB9C6", anchor="w")
        self.scan_sel_lbl.pack(fill="x", padx=18, pady=(2, 2))

        actions = ctk.CTkFrame(win, fg_color="transparent")
        actions.pack(fill="x", padx=16, pady=(2, 12))
        ctk.CTkButton(actions, text="📂 Open", width=90, command=self._scan_open_item).pack(side="left", padx=3)
        ctk.CTkButton(actions, text="🗂 Reveal in Explorer", width=150, command=self._scan_reveal).pack(side="left", padx=3)
        self.scan_total_lbl = ctk.CTkLabel(actions, text="", font=ctk.CTkFont(size=12, weight="bold"),
                                           text_color="#00FFCC")
        self.scan_total_lbl.pack(side="left", padx=16)
        ctk.CTkButton(actions, text="🗑 Delete (Recycle Bin)", width=180, fg_color="#0E7C66",
                      hover_color="#0A5E4D", command=lambda: self._scan_delete(False)).pack(side="right", padx=3)
        ctk.CTkButton(actions, text="⚠ Delete Permanently", width=170, fg_color="#7C2A2A",
                      hover_color="#5E1F1F", command=lambda: self._scan_delete(True)).pack(side="right", padx=3)

        if scan_path:
            self.scan_path_var.set(scan_path)
            self._start_scan()

    def _setup_tree_style(self):
        try:
            style = ttk.Style()
            style.theme_use("clam")
            style.configure("Neo.Treeview", background="#0C0C12", fieldbackground="#0C0C12",
                            foreground="#CCDDEE", rowheight=24, borderwidth=0, font=("Segoe UI", 10))
            style.configure("Neo.Treeview.Heading", background="#15151F", foreground="#9FB0C0",
                            relief="flat", font=("Segoe UI", 10, "bold"))
            style.map("Neo.Treeview", background=[("selected", "#1F6F5C")],
                      foreground=[("selected", "#FFFFFF")])
            style.map("Neo.Treeview.Heading", background=[("active", "#23233A")])
        except Exception:
            pass

    def _scan_browse(self):
        d = filedialog.askdirectory(parent=self.scan_win, initialdir=self.scan_path_var.get() or os.path.expanduser("~"))
        if d:
            self.scan_path_var.set(d)

    def _scan_set_and_go(self, path):
        self.scan_path_var.set(path)
        self._start_scan()

    # ----------------------------- scan engine -----------------------------
    def _start_scan(self):
        if self._scanning:
            self.log_message("A scan is already running.")
            return
        root_path = self.scan_path_var.get().strip()
        if not root_path or not os.path.isdir(root_path):
            messagebox.showwarning("Scan", "Please choose a valid folder or drive to scan.", parent=self.scan_win)
            return
        self._scan_stop.clear()
        self._scanning = True
        self._scan_files = 0
        self._scan_bytes = 0
        self._scan_current = ""
        self.scan_btn.configure(state="disabled")
        self.scan_stop_btn.configure(state="normal")
        self.scan_tree.delete(*self.scan_tree.get_children())
        self.scan_nodes = {}
        self.scan_loaded = set()
        try:
            self.scan_progress.configure(mode="indeterminate")
            self.scan_progress.start()
        except Exception:
            pass
        self._poll_scan_progress()

        def work():
            try:
                sys.setrecursionlimit(20000)
            except Exception:
                pass
            root = self._walk(root_path, None)
            self.after(0, lambda: self._scan_done(root))

        threading.Thread(target=work, daemon=True).start()

    def _stop_scan(self):
        self._scan_stop.set()
        self.scan_status_lbl.configure(text="Stopping scan…")

    def _walk(self, path, parent):
        node = FSNode(name=(os.path.basename(path.rstrip("\\/")) or path), path=path, is_dir=True, parent=parent)
        total = 0
        fcount = 0
        try:
            with os.scandir(path) as it:
                for entry in it:
                    if self._scan_stop.is_set():
                        break
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            child = self._walk(entry.path, node)
                            node.children.append(child)
                            total += child.size
                            fcount += child.file_count
                        else:
                            sz = entry.stat(follow_symlinks=False).st_size
                            f = FSNode(name=entry.name, path=entry.path, size=sz,
                                       is_dir=False, parent=node, file_count=1)
                            node.children.append(f)
                            total += sz
                            fcount += 1
                            self._scan_files += 1
                            self._scan_bytes += sz
                            self._scan_current = entry.path
                    except (PermissionError, OSError):
                        continue
        except (PermissionError, OSError) as e:
            node.error = str(e)
        node.size = total
        node.file_count = fcount
        return node

    def _poll_scan_progress(self):
        if not self._scanning:
            return
        try:
            cur = self._scan_current
            if len(cur) > 70:
                cur = "…" + cur[-67:]
            self.scan_status_lbl.configure(
                text=f"Scanning… {self._scan_files:,} files · {self.format_size(self._scan_bytes)}   {cur}")
        except Exception:
            pass
        self.after(200, self._poll_scan_progress)

    def _scan_done(self, root):
        self._scanning = False
        try:
            self.scan_progress.stop()
            self.scan_progress.configure(mode="determinate")
            self.scan_progress.set(1.0)
        except Exception:
            pass
        self.scan_btn.configure(state="normal")
        self.scan_stop_btn.configure(state="disabled")
        try:
            if not self.scan_win.winfo_exists():
                return
        except Exception:
            return
        self.scan_root = root
        self.scan_nodes = {}
        self.scan_loaded = set()
        self.scan_tree.insert("", "end", iid=root.path, text=f"📁 {root.path}",
                              values=(self.format_size(root.size), self._make_bar(1.0), f"{root.file_count:,}"),
                              open=True)
        self.scan_nodes[root.path] = root
        self._insert_children(root)
        stopped = " (stopped early)" if self._scan_stop.is_set() else ""
        self.scan_status_lbl.configure(
            text=f"Done{stopped}: {root.file_count:,} files · {self.format_size(root.size)} total")
        self._update_scan_totals()
        self.log_message(f"🔬 Scanned {root.path}: {self.format_size(root.size)} in {root.file_count:,} files")

    # --------------------------- tree population ---------------------------
    def _make_bar(self, frac):
        frac = max(0.0, min(1.0, frac))
        filled = int(round(frac * 10))
        return "█" * filled + "░" * (10 - filled) + f" {frac * 100:5.1f}%"

    def _insert_node(self, parent_iid, child, parent_size):
        frac = (child.size / parent_size) if parent_size else 0.0
        icon = "📁" if child.is_dir else "📄"
        items = f"{child.file_count:,}" if child.is_dir else ""
        self.scan_tree.insert(parent_iid, "end", iid=child.path, text=f"{icon} {child.name}",
                              values=(self.format_size(child.size), self._make_bar(frac), items))
        self.scan_nodes[child.path] = child
        if child.is_dir and child.children:
            self.scan_tree.insert(child.path, "end", iid=child.path + "\x00dummy", text="…")

    def _insert_children(self, node):
        for c in self.scan_tree.get_children(node.path):
            self.scan_tree.delete(c)
        for child in sorted(node.children, key=lambda x: x.size, reverse=True):
            self._insert_node(node.path, child, node.size)
        self.scan_loaded.add(node.path)

    def _on_scan_open(self, event):
        iid = self.scan_tree.focus()
        node = self.scan_nodes.get(iid)
        if not node or not node.is_dir or iid in self.scan_loaded:
            return
        self._insert_children(node)

    def _on_scan_select(self, event):
        iid = self.scan_tree.focus()
        node = self.scan_nodes.get(iid)
        if not node:
            self.scan_sel_lbl.configure(text="No selection")
            return
        kind = "Folder" if node.is_dir else "File"
        extra = f" · {node.file_count:,} files" if node.is_dir else ""
        self.scan_sel_lbl.configure(text=f"{kind}: {node.path}   [{self.format_size(node.size)}{extra}]")

    def _on_scan_doubleclick(self, event):
        iid = self.scan_tree.identify_row(event.y)
        node = self.scan_nodes.get(iid)
        if node and not node.is_dir:
            self._open_path(node.path)

    def _update_scan_totals(self):
        if self.scan_root:
            self.scan_total_lbl.configure(
                text=f"Total: {self.format_size(self.scan_root.size)} · {self.scan_root.file_count:,} files")

    # ------------------------------ open / reveal ------------------------------
    def _selected_node(self):
        iid = self.scan_tree.focus()
        return self.scan_nodes.get(iid)

    def _open_path(self, path):
        try:
            if platform.system() == "Windows":
                os.startfile(path)
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            self.log_message(f"⚠️ Could not open: {e}")

    def _scan_open_item(self):
        node = self._selected_node()
        if node:
            self._open_path(node.path)

    def _scan_reveal(self):
        node = self._selected_node()
        if not node:
            return
        try:
            if platform.system() == "Windows":
                if node.is_dir:
                    os.startfile(node.path)
                else:
                    subprocess.Popen(f'explorer /select,"{node.path}"')
            else:
                self._open_path(node.path if node.is_dir else os.path.dirname(node.path))
        except Exception as e:
            self.log_message(f"⚠️ Reveal error: {e}")

    # ------------------------------ delete logic ------------------------------
    def _path_risk(self, path):
        """Return 'blocked' (never delete), 'danger' (warn hard), or 'ok'."""
        try:
            p = os.path.abspath(path).rstrip("\\/").lower()
        except Exception:
            return "blocked"
        if platform.system() != "Windows":
            return "blocked" if (not p or len(p) < 2) else "ok"
        if len(p) <= 2 and p.endswith(":"):  # "c:" -> drive root
            return "blocked"
        sysdrive = os.environ.get("SystemDrive", "C:").rstrip("\\/").lower()
        sysroot = os.environ.get("SystemRoot", sysdrive + "\\windows").rstrip("\\/").lower()
        blocked = {sysdrive, sysroot, sysroot + "\\system32",
                   sysdrive + "\\program files", sysdrive + "\\program files (x86)",
                   sysdrive + "\\programdata", sysdrive + "\\users"}
        if p in blocked:
            return "blocked"
        for d in (sysroot, sysdrive + "\\program files", sysdrive + "\\program files (x86)",
                  sysdrive + "\\programdata"):
            if p == d or p.startswith(d + "\\"):
                return "danger"
        return "ok"

    def _recycle_path(self, path):
        if platform.system() == "Windows":
            esc = path.replace("'", "''")
            ps = ("Add-Type -AssemblyName Microsoft.VisualBasic;"
                  f"$p='{esc}';"
                  "if (Test-Path -LiteralPath $p -PathType Container) "
                  "{[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory("
                  "$p,'OnlyErrorDialogs','SendToRecycleBin')} "
                  "else {[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile("
                  "$p,'OnlyErrorDialogs','SendToRecycleBin')}")
            out, rc = self._ps(ps, timeout=300)
            return rc == 0, out
        # Non-Windows fallback: try send2trash, else report unsupported
        try:
            import send2trash
            send2trash.send2trash(path)
            return True, ""
        except Exception as e:
            return False, f"Recycle not supported here ({e}). Use permanent delete."

    def _permanent_delete(self, path):
        try:
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
            return True, ""
        except Exception as e:
            return False, str(e)

    def _scan_delete(self, permanent):
        node = self._selected_node()
        if not node:
            messagebox.showinfo("Delete", "Select a file or folder first.", parent=self.scan_win)
            return
        risk = self._path_risk(node.path)
        if risk == "blocked":
            messagebox.showerror("Blocked",
                                 f"NEO TUNE will not delete a protected system location:\n\n{node.path}",
                                 parent=self.scan_win)
            return

        kind = "folder" if node.is_dir else "file"
        where = "PERMANENTLY DELETE" if permanent else "move to the Recycle Bin"
        msg = (f"{where} this {kind}?\n\n{node.path}\n"
               f"Size: {self.format_size(node.size)}"
               f"{(', ' + format(node.file_count, ',') + ' files') if node.is_dir else ''}")
        if risk == "danger":
            msg += ("\n\n⚠️ This is inside a Windows/system location. Deleting it can damage Windows "
                    "or installed programs. Only continue if you are certain.")
        if permanent:
            msg += "\n\nThis CANNOT be undone."
        if not messagebox.askyesno("Confirm delete", msg, icon="warning", parent=self.scan_win):
            return
        if permanent or risk == "danger":
            if not messagebox.askyesno("Final confirmation",
                                       "Are you absolutely sure you want to proceed?",
                                       icon="warning", parent=self.scan_win):
                return

        self.log_message(f"🗑️ Deleting: {node.path}")

        def work():
            ok, err = (self._permanent_delete(node.path) if permanent else self._recycle_path(node.path))
            self.after(0, lambda: self._after_delete(node, ok, err, permanent))
        threading.Thread(target=work, daemon=True).start()

    def _purge_iid(self, path):
        for key in [k for k in self.scan_nodes if k == path or k.startswith(path + os.sep)
                    or k.startswith(path + "\\")]:
            self.scan_nodes.pop(key, None)
            self.scan_loaded.discard(key)

    def _refresh_ancestor_cells(self, node):
        n = node
        while n is not None:
            try:
                if self.scan_tree.exists(n.path):
                    parent_size = n.parent.size if n.parent else n.size
                    frac = (n.size / parent_size) if parent_size else 1.0
                    if n.parent is None:
                        frac = 1.0
                    self.scan_tree.item(n.path, values=(
                        self.format_size(n.size), self._make_bar(frac),
                        f"{n.file_count:,}" if n.is_dir else ""))
            except Exception:
                pass
            n = n.parent

    def _after_delete(self, node, ok, err, permanent):
        if not ok:
            self.log_message(f"❌ Delete failed: {str(err)[:200]}")
            try:
                messagebox.showerror("Delete failed", str(err)[:500] or "Unknown error", parent=self.scan_win)
            except Exception:
                pass
            return
        self.log_message(f"✅ {'Deleted' if permanent else 'Moved to Recycle Bin'}: {node.path}")
        parent = node.parent
        freed, fc = node.size, node.file_count
        if parent and node in parent.children:
            parent.children.remove(node)
        n = parent
        while n is not None:
            n.size -= freed
            n.file_count -= fc
            n = n.parent
        self._purge_iid(node.path)
        try:
            if self.scan_tree.exists(node.path):
                self.scan_tree.delete(node.path)
        except Exception:
            pass
        # Refresh siblings' percentages against the new parent size
        if parent is not None and parent.path in self.scan_loaded:
            self._insert_children(parent)
        self._refresh_ancestor_cells(parent)
        self._update_scan_totals()

    # ------------------------- ASK ASSISTANT -------------------------
    FILE_ASSISTANT_SYSTEM = (
        "You are NEO TUNE's file assistant inside a disk cleanup tool. You are given metadata about ONE "
        "file or folder (name, path, size, type, location). You CANNOT see its contents. Explain briefly "
        "what it most likely is and whether it is generally safe to delete to free disk space. "
        "Rules: NEVER recommend deleting Windows, System32, Program Files, ProgramData, or boot files — say "
        "keep them. Treat cache/temp/log folders as usually safe to clear. Treat personal documents and media "
        "as 'caution — not automatically recoverable'. Be honest about uncertainty; do not invent specifics "
        "about this user's data. Answer in 3 short lines: 1) What it is. 2) Safe to delete? Yes / Caution / No "
        "— one-line why. 3) A short tip (e.g., prefer the Recycle Bin)."
    )

    def _scan_right_click(self, event):
        iid = self.scan_tree.identify_row(event.y)
        if iid:
            self.scan_tree.selection_set(iid)
            self.scan_tree.focus(iid)
            self._on_scan_select(None)
        try:
            self.scan_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.scan_menu.grab_release()

    def _location_hint(self, path):
        p = path.lower()
        hints = [
            ("\\appdata\\local\\temp", "Windows user temp folder"),
            ("\\windows\\temp", "Windows system temp folder"),
            ("\\downloads\\", "your Downloads folder"),
            ("\\inetcache", "browser/internet cache"),
            ("\\appdata\\local", "app cache/data for installed programs"),
            ("\\appdata\\roaming", "app settings for installed programs"),
            ("\\program files", "installed program files"),
            ("\\windows\\", "core Windows files"),
            ("$recycle.bin", "the Recycle Bin"),
            ("\\onedrive", "OneDrive synced files"),
            ("\\documents\\", "your Documents"),
            ("\\desktop\\", "your Desktop"),
            ("\\pictures\\", "your Pictures"),
            ("\\videos\\", "your Videos"),
        ]
        for tok, desc in hints:
            if tok in p:
                return desc
        return ""

    def _file_context(self, node):
        ctx = {
            "name": node.name,
            "path": node.path,
            "type": "folder" if node.is_dir else "file",
            "size": self.format_size(node.size),
            "ext": (os.path.splitext(node.name)[1].lower() if not node.is_dir else ""),
            "file_count": node.file_count if node.is_dir else 1,
            "risk": self._path_risk(node.path),
            "location_hint": self._location_hint(node.path),
        }
        if node.is_dir and node.children:
            top = sorted(node.children, key=lambda x: x.size, reverse=True)[:6]
            ctx["top_children"] = [f"{c.name} ({self.format_size(c.size)})" for c in top]
        return ctx

    def _heuristic_verdict(self, node, ctx):
        name = node.name.lower()
        ext = ctx["ext"]
        path = node.path.lower()
        if ctx["risk"] == "blocked":
            return ("This is a core Windows/system location. It is NOT safe to delete — removing it can "
                    "break Windows. NEO TUNE blocks deletion here.")
        sys_tokens = ["\\windows\\", "\\system32\\", "\\program files", "\\programdata\\",
                      "system volume information", "$recycle.bin", "$windows.~", "\\boot"]
        if any(t in path for t in sys_tokens):
            if ext in (".dll", ".sys", ".exe", ".drv", ".cat", ".mui", ".winmd", ".msi"):
                return ("This looks like a Windows or installed-program component. Deleting it may break "
                        "Windows or an app — keep it unless you know exactly what it is.")
            if any(t in path for t in ["\\temp\\", "\\cache", "\\logs", "\\inetcache", "\\windows\\temp"]):
                return ("This looks like temporary/cache data inside a system area — usually safe to clear, "
                        "and Windows will rebuild it.")
            return "This sits in a Windows/program area. Be cautious; prefer keeping system/program files."
        cache_tokens = ["cache", "gpucache", "code cache", "temp", "tmp", "logs", "crash",
                        "crashpad", "minidump", "thumbnail", "webcache", "inetcache"]
        if node.is_dir and any(t in name for t in cache_tokens):
            return ("This looks like a cache/temporary folder. Generally safe to delete to free space — the "
                    "app or Windows will recreate it when needed.")
        if ext in (".tmp", ".temp", ".log", ".bak", ".old", ".chk", ".dmp", ".etl", ".cab"):
            return "This looks like a temporary, log, or backup file. Usually safe to delete to free space."
        if name in ("node_modules", "__pycache__", ".gradle", ".m2", "build", "dist") or name.endswith(".egg-info"):
            return ("This is re-generatable build/dependency data — you can rebuild or reinstall it. Safe to "
                    "delete to free space; it regenerates when you next build.")
        if "\\downloads\\" in path:
            if ext in (".iso", ".zip", ".7z", ".rar", ".msi", ".exe"):
                return "Downloaded installer/archive. Safe to delete once installed or no longer needed."
            return "This is in Downloads. Safe to delete if you no longer need it — it won't come back on its own."
        if ext in (".jpg", ".jpeg", ".png", ".gif", ".mp4", ".mov", ".mp3", ".pdf",
                   ".docx", ".xlsx", ".pptx", ".txt"):
            return ("This looks like a personal document or media file. Only delete if you're sure — it is NOT "
                    "automatically recoverable (only via the Recycle Bin).")
        kind = "folder" if node.is_dir else "file"
        return (f"I can't identify this {kind} from its name alone. If you didn't create it and it isn't in a "
                "system/program area, it's likely user data or app data — review it before deleting, and use "
                "the Recycle Bin so you can undo.")

    def _file_ai_prompt(self, ctx):
        risk_desc = {"blocked": "protected system path (deletion blocked by the app)",
                     "danger": "inside a Windows/system or installed-program area",
                     "ok": "a normal user-area path"}.get(ctx["risk"], ctx["risk"])
        lines = [f"Name: {ctx['name']}", f"Path: {ctx['path']}", f"Type: {ctx['type']}",
                 f"Size: {ctx['size']}", f"Extension: {ctx['ext'] or '(none)'}",
                 f"Contains files: {ctx['file_count']}",
                 f"App safety classification: {ctx['risk']} ({risk_desc})"]
        if ctx.get("location_hint"):
            lines.append(f"Location hint: {ctx['location_hint']}")
        if ctx.get("top_children"):
            lines.append("Largest items inside: " + ", ".join(ctx["top_children"]))
        return "File/folder metadata:\n" + "\n".join(lines)

    def _open_assistant_window(self):
        win = getattr(self, "assistant_win", None)
        try:
            if win is not None and win.winfo_exists():
                win.lift()
                win.focus_force()
                return win
        except Exception:
            pass
        win = ctk.CTkToplevel(self.scan_win or self)
        self.assistant_win = win
        win.title("🤖 File Assistant")
        win.geometry("640x540")
        win.attributes('-topmost', True)
        win.after(400, lambda: win.attributes('-topmost', False))
        self.assistant_header = ctk.CTkLabel(win, text="", font=ctk.CTkFont(family="Consolas", size=12),
                                             text_color="#9FE8D0", justify="left", wraplength=600, anchor="w")
        self.assistant_header.pack(fill="x", padx=16, pady=(14, 6))
        self.assistant_box = ctk.CTkTextbox(win, font=ctk.CTkFont(size=13), fg_color="#050508", wrap="word")
        self.assistant_box.pack(fill="both", expand=True, padx=16, pady=6)
        self.assistant_box.configure(state="disabled")
        ctk.CTkButton(win, text="Close", width=100, command=win.destroy).pack(pady=(2, 12))
        return win

    def _assistant_update(self, header, body):
        try:
            if not self.assistant_win.winfo_exists():
                return
        except Exception:
            return
        self.assistant_header.configure(text=header)
        self.assistant_box.configure(state="normal")
        self.assistant_box.delete("1.0", "end")
        self.assistant_box.insert("1.0", body)
        self.assistant_box.configure(state="disabled")

    def _scan_ask_assistant(self):
        node = self._selected_node()
        if not node:
            messagebox.showinfo("Assistant", "Right-click a file or folder first.", parent=self.scan_win)
            return
        ctx = self._file_context(node)
        risk_banner = {
            "blocked": "⛔ Protected system location — NEO TUNE blocks deletion here.",
            "danger": "⚠️ Inside a Windows/system area — deleting can break Windows or installed apps.",
            "ok": "",
        }.get(ctx["risk"], "")
        heuristic = self._heuristic_verdict(node, ctx)
        icon = "📁" if node.is_dir else "📄"
        extra = f" · {node.file_count:,} files" if node.is_dir else ""
        header = f"{icon}  {node.name}\n{node.path}\n{ctx['size']}{extra}"

        self._open_assistant_window()
        provider = self.ai_provider_var.get()
        intro = (risk_banner + "\n\n" if risk_banner else "")
        self._assistant_update(header, intro + "🧩 Quick read (offline):\n" + heuristic +
                               f"\n\n🤖 Asking {provider} for a closer look…")

        prompt = self._file_ai_prompt(ctx)

        def work():
            text, err = self._ai_chat(self.FILE_ASSISTANT_SYSTEM, prompt)
            final = intro
            if err or not text:
                final += ("🧩 Assistant (offline heuristic):\n" + heuristic +
                          f"\n\n(ℹ️ AI provider unavailable: {err}. Start Ollama/LM Studio, or set a provider "
                          "in NEO DOCTOR, for a richer answer.)")
            else:
                final += ("🤖 " + provider + " assistant:\n" + text +
                          "\n\n———\n🧩 Offline heuristic: " + heuristic)
            final += ("\n\n⚠️ Advisory only — always verify before deleting. The Recycle Bin is reversible; "
                      "permanent delete is not.")
            self.after(0, lambda: self._assistant_update(header, final))

        threading.Thread(target=work, daemon=True).start()

    # ======================================================================

    # ---------------------- ORIGINAL UI ELEMENTS ----------------------
    def create_header(self):
        header_frame = ctk.CTkFrame(self.main_frame, height=80, corner_radius=0, fg_color="#0A0A0E")
        header_frame.grid(row=0, column=0, sticky="ew", padx=0, pady=0)
        header_frame.grid_propagate(False)

        title = ctk.CTkLabel(
            header_frame,
            text="⚡ NEO TUNE ⚡",
            font=ctk.CTkFont(family="Orbitron", size=32, weight="bold"),
            text_color="#00FFCC"
        )
        title.pack(side="left", padx=30, pady=20)

        subtitle = ctk.CTkLabel(
            header_frame,
            text="Apex Performance Optimization Suite",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color="#8899AA"
        )
        subtitle.pack(side="left", padx=15)

        version_badge = ctk.CTkLabel(
            header_frame,
            text="v4.0 | NET CORE",
            font=ctk.CTkFont(family="Orbitron", size=10),
            text_color="#FF3366",
            corner_radius=15,
            fg_color="#1A1A2E",
            padx=10
        )
        version_badge.pack(side="right", padx=30, pady=20)

    def create_metrics_dashboard(self):
        dashboard = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        dashboard.grid(row=1, column=0, sticky="n", padx=25, pady=20)
        dashboard.grid_columnconfigure((0,1,2), weight=1)

        self.cpu_card = self.create_metric_card(
            dashboard, "🖥️ CPU CORE", "0%", 0, 0,
            on_click=self.open_top_processes, hint="▸ Top Processes")
        self.ram_card = self.create_metric_card(
            dashboard, "💾 RAM ALLOC", "0%", 0, 1,
            on_click=lambda: self.open_utilities("💾 Memory & Storage"), hint="▸ Memory Tools")
        self.disk_card = self.create_metric_card(
            dashboard, "💿 DISK I/O (C:)", "0%", 0, 2,
            on_click=lambda: self.open_disk_scanner(os.environ.get("SystemDrive", "C:") + "\\"),
            hint="▸ Disk Scanner")

        info_frame = ctk.CTkFrame(dashboard, fg_color="transparent")
        info_frame.grid(row=1, column=0, columnspan=3, pady=10, sticky="ew")
        self.sys_info_label = ctk.CTkLabel(
            info_frame,
            text="🔹 SYSTEM: OPTIMIZED | 🔹 TEMP: SCANNING | 🔹 THREADS: ACTIVE",
            font=ctk.CTkFont(family="Consolas", size=11),
            text_color="#66FFCC"
        )
        self.sys_info_label.pack()

    def create_metric_card(self, parent, title, value, row, col, on_click=None, hint=None):
        card = ctk.CTkFrame(parent, width=250, height=162, corner_radius=20, fg_color="#14141E", border_width=1, border_color="#2A2A3A")
        card.grid(row=row, column=col, padx=15, pady=10, sticky="n")
        card.grid_propagate(False)
        card._base_border = "#2A2A3A"

        title_label = ctk.CTkLabel(card, text=title, font=ctk.CTkFont(family="Orbitron", size=14, weight="bold"), text_color="#AABBCC")
        title_label.pack(pady=(14, 4))

        value_label = ctk.CTkLabel(card, text=value, font=ctk.CTkFont(family="Digital-7", size=34, weight="bold"), text_color="#00FFCC")
        value_label.pack(pady=(2, 4))

        progress = ctk.CTkProgressBar(card, width=200, height=8, corner_radius=4, progress_color="#00FFCC")
        progress.set(0.0)
        progress.pack(pady=4)

        hint_label = None
        if hint:
            hint_label = ctk.CTkLabel(card, text=hint, font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"), text_color="#5C6B7A")
            hint_label.pack(pady=(3, 0))

        card.value_label = value_label
        card.progress = progress
        card.hint_label = hint_label
        if on_click:
            self._make_card_clickable(card, on_click)
        return card

    def _make_card_clickable(self, card, callback):
        def bind_recursive(widget):
            try:
                widget.configure(cursor="hand2")
            except Exception:
                pass
            try:
                widget.bind("<Button-1>", lambda e: callback())
                widget.bind("<Enter>", lambda e: self._card_hover(card, True))
                widget.bind("<Leave>", lambda e: self._card_hover(card, False))
            except Exception:
                pass
            for child in widget.winfo_children():
                bind_recursive(child)
        bind_recursive(card)

    def _card_hover(self, card, hovering):
        try:
            card.configure(border_color="#00FFCC" if hovering else getattr(card, "_base_border", "#2A2A3A"),
                           border_width=2 if hovering else 1)
            if getattr(card, "hint_label", None):
                card.hint_label.configure(text_color="#00FFCC" if hovering else "#5C6B7A")
        except Exception:
            pass

    def create_log_area(self):
        self.log_frame = ctk.CTkFrame(self.main_frame, corner_radius=15, fg_color="#0C0C12", border_width=1, border_color="#2A2A3A")
        self.log_frame.grid(row=2, column=0, sticky="nsew", padx=25, pady=(0,25))
        self.log_frame.grid_rowconfigure(0, weight=1)
        self.log_frame.grid_columnconfigure(0, weight=1)

        log_header = ctk.CTkLabel(
            self.log_frame,
            text="⚡ SYSTEM LOG TERMINAL ⚡",
            font=ctk.CTkFont(family="Orbitron", size=12, weight="bold"),
            text_color="#00FFCC"
        )
        log_header.pack(anchor="w", padx=15, pady=(10,5))

        self.log_text = ctk.CTkTextbox(self.log_frame, font=ctk.CTkFont(family="Consolas", size=11), fg_color="#050508", corner_radius=10)
        self.log_text.pack(fill="both", expand=True, padx=15, pady=(0,15))

        self.status_bar = ctk.CTkFrame(self.log_frame, height=30, fg_color="transparent")
        self.status_bar.pack(fill="x", padx=15, pady=(0,10))

        self.status_label = ctk.CTkLabel(self.status_bar, text="✓ System Ready", font=ctk.CTkFont(family="Consolas", size=10), text_color="#88FFAA")
        self.status_label.pack(side="left")

        self.progress_bar = ctk.CTkProgressBar(self.status_bar, width=300, height=6, corner_radius=3, progress_color="#00FFCC")
        self.progress_bar.pack(side="right")
        self.progress_bar.set(0.0)

    def create_sidebar_buttons(self):
        logo = ctk.CTkLabel(
            self.sidebar,
            text="⚡ NEO\nTUNE",
            font=ctk.CTkFont(family="Orbitron", size=28, weight="bold"),
            text_color="#00FFCC",
            justify="center"
        )
        logo.pack(pady=(40, 30))

        ctk.CTkFrame(self.sidebar, height=2, fg_color="#2A2A3A").pack(fill="x", padx=20, pady=10)

        buttons = [
            ("🔍 QUICK CLEAN", self.quick_clean),
            ("🧹 DEEP CLEAN", self.deep_clean),
            ("📊 ANALYZE SPACE", self.analyze_space),
            ("🩺 NEO DOCTOR", self.open_doctor),
            ("🔬 DISK SCANNER", self.open_disk_scanner),
            ("🛠️ UTILITIES", self.open_utilities),
            ("🌐 NETWORK CENTER", self.open_network_center),
            ("🚀 STARTUP MANAGER", self.open_startup_manager),
            ("💻 SYSTEM INFO", self.show_system_info),
            ("❄️ EXIT", self.quit_app)
        ]

        for text, cmd in buttons:
            btn = ctk.CTkButton(
                self.sidebar,
                text=text,
                command=cmd,
                height=45,
                corner_radius=8,
                fg_color="transparent",
                text_color="#CCDDEE",
                hover_color="#1F1F2F",
                anchor="w",
                font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold")
            )
            btn.pack(fill="x", padx=20, pady=5)

        gauge_frame = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        gauge_frame.pack(side="bottom", pady=30)
        ctk.CTkLabel(
            gauge_frame,
            text="SYSTEM THRUST",
            font=ctk.CTkFont(family="Orbitron", size=10),
            text_color="#8899AA"
        ).pack()
        self.thrust_progress = ctk.CTkProgressBar(gauge_frame, width=180, height=8, corner_radius=4, progress_color="#FF3366")
        self.thrust_progress.set(0.75)
        self.thrust_progress.pack(pady=5)

    def update_system_metrics(self):
        try:
            cpu_percent = psutil.cpu_percent(interval=0.5)
            ram_percent = psutil.virtual_memory().percent
            disk_usage = psutil.disk_usage('C:')
            disk_percent = disk_usage.percent

            self.cpu_card.value_label.configure(text=f"{cpu_percent:.1f}%")
            self.cpu_card.progress.set(cpu_percent / 100.0)

            self.ram_card.value_label.configure(text=f"{ram_percent:.1f}%")
            self.ram_card.progress.set(ram_percent / 100.0)

            self.disk_card.value_label.configure(text=f"{disk_percent:.1f}%")
            self.disk_card.progress.set(disk_percent / 100.0)

            if cpu_percent > 80:
                self.cpu_card.value_label.configure(text_color="#FF5555")
            else:
                self.cpu_card.value_label.configure(text_color="#00FFCC")

            if ram_percent > 80:
                self.ram_card.value_label.configure(text_color="#FF5555")
            else:
                self.ram_card.value_label.configure(text_color="#00FFCC")

        except Exception as e:
            self.log_message(f"Metrics error: {e}")

        self.after(2000, self.update_system_metrics)

    def log_message(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        formatted = f"[{timestamp}] {message}\n"
        self.log_text.insert("end", formatted)
        self.log_text.see("end")
        self.update_idletasks()

    def update_progress(self, value, status_text=None):
        self.progress_bar.set(value)
        if status_text:
            self.status_label.configure(text=status_text)
        self.update_idletasks()

    def check_admin(self):
        if platform.system() == "Windows":
            try:
                return ctypes.windll.shell32.IsUserAnAdmin()
            except:
                return False
        return True

    def run_with_progress(self, task_func, task_name="Operation"):
        def task_wrapper():
            try:
                self.update_progress(0.1, f"Starting {task_name}...")
                self.log_message(f"🚀 {task_name} initiated")
                result = task_func()
                self.update_progress(1.0, f"{task_name} completed!")
                self.log_message(f"✅ {task_name} finished successfully")
                if isinstance(result, str):
                    self.log_message(result)
                self.after(100, lambda: self.update_progress(0.0, "Ready"))
            except Exception as e:
                self.log_message(f"❌ Error in {task_name}: {str(e)}")
                self.update_progress(0.0, "Error occurred")
        thread = threading.Thread(target=task_wrapper, daemon=True)
        thread.start()

    # ---------- CLEANING FUNCTIONS (unchanged) ----------
    def get_temp_locations(self):
        temp_dirs = [os.environ.get('TEMP', tempfile.gettempdir()), os.environ.get('TMP', '')]
        if platform.system() == "Windows":
            system_temp = os.path.join(os.environ.get('SystemRoot', 'C:\\Windows'), 'Temp')
            temp_dirs.append(system_temp)
        return [d for d in temp_dirs if d and os.path.exists(d)]

    def get_prefetch_path(self):
        if platform.system() == "Windows":
            return os.path.join(os.environ.get('SystemRoot', 'C:\\Windows'), 'Prefetch')
        return None

    def get_recent_path(self):
        if platform.system() == "Windows":
            return os.path.join(os.environ.get('APPDATA', ''), 'Microsoft', 'Windows', 'Recent')
        return None

    def get_browser_cache_paths(self):
        caches = []
        if platform.system() != "Windows":
            return caches
        local_app_data = os.environ.get('LOCALAPPDATA', '')
        app_data = os.environ.get('APPDATA', '')
        chrome_cache = os.path.join(local_app_data, 'Google', 'Chrome', 'User Data', 'Default', 'Cache')
        chrome_code = os.path.join(local_app_data, 'Google', 'Chrome', 'User Data', 'Default', 'Code Cache')
        if os.path.exists(chrome_cache):
            caches.append(chrome_cache)
        if os.path.exists(chrome_code):
            caches.append(chrome_code)
        edge_cache = os.path.join(local_app_data, 'Microsoft', 'Edge', 'User Data', 'Default', 'Cache')
        edge_code = os.path.join(local_app_data, 'Microsoft', 'Edge', 'User Data', 'Default', 'Code Cache')
        if os.path.exists(edge_cache):
            caches.append(edge_cache)
        if os.path.exists(edge_code):
            caches.append(edge_code)
        firefox_profiles = glob.glob(os.path.join(app_data, 'Mozilla', 'Firefox', 'Profiles', '*'))
        for profile in firefox_profiles:
            cache2 = os.path.join(profile, 'cache2')
            if os.path.exists(cache2):
                caches.append(cache2)
        return caches

    def delete_files_in_dir(self, directory, pattern='*.*', recursive=False):
        if not os.path.exists(directory):
            return 0, 0
        deleted_count = 0
        freed_size = 0
        try:
            if recursive:
                shutil.rmtree(directory, ignore_errors=True)
                return 0, 0
            else:
                for file_path in glob.glob(os.path.join(directory, pattern)):
                    try:
                        size = os.path.getsize(file_path)
                        os.remove(file_path)
                        deleted_count += 1
                        freed_size += size
                    except Exception:
                        continue
        except Exception as e:
            self.log_message(f"⚠️ Error cleaning {directory}: {e}")
        return deleted_count, freed_size

    def clean_temp_files(self):
        total_freed = 0
        for temp_dir in self.get_temp_locations():
            if os.path.exists(temp_dir):
                count, freed = self.delete_files_in_dir(temp_dir)
                total_freed += freed
                self.log_message(f"🧹 Temp: {temp_dir} - freed {self.format_size(freed)}")
        return total_freed

    def empty_recycle_bin(self):
        if platform.system() != "Windows":
            return False
        try:
            windll.shell32.SHEmptyRecycleBinW(None, None, 0)
            self.log_message("🗑️ Recycle Bin emptied successfully")
            return True
        except Exception as e:
            self.log_message(f"⚠️ Recycle bin clean error: {e}")
            return False

    def clean_recent_documents(self):
        recent_path = self.get_recent_path()
        if recent_path and os.path.exists(recent_path):
            count, freed = self.delete_files_in_dir(recent_path)
            self.log_message(f"📄 Recent documents: {count} items removed, freed {self.format_size(freed)}")
            return freed
        return 0

    def clean_prefetch(self):
        if not self.is_admin:
            self.log_message("⚠️ Skipping Prefetch (requires admin rights)")
            return 0
        prefetch = self.get_prefetch_path()
        if prefetch and os.path.exists(prefetch):
            count, freed = self.delete_files_in_dir(prefetch)
            self.log_message(f"⚡ Prefetch: {count} files cleared, freed {self.format_size(freed)}")
            return freed
        return 0

    def clean_browser_caches(self):
        total_freed = 0
        for cache_dir in self.get_browser_cache_paths():
            if os.path.exists(cache_dir):
                before = self.get_folder_size(cache_dir)
                try:
                    shutil.rmtree(cache_dir, ignore_errors=True)
                    self.log_message(f"🌐 Browser cache: {cache_dir} - cleared {self.format_size(before)}")
                    total_freed += before
                except Exception as e:
                    self.log_message(f"⚠️ Browser cache error {cache_dir}: {e}")
        return total_freed

    def get_folder_size(self, folder):
        total = 0
        if not os.path.exists(folder):
            return 0
        for dirpath, dirnames, filenames in os.walk(folder):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                try:
                    total += os.path.getsize(fp)
                except:
                    pass
        return total

    def format_size(self, size_bytes):
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.2f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.2f} TB"

    def quick_clean(self):
        def quick_job():
            self.update_progress(0.2, "Cleaning temp files...")
            freed_temp = self.clean_temp_files()
            self.update_progress(0.5, "Emptying recycle bin...")
            self.empty_recycle_bin()
            self.update_progress(0.8, "Cleaning recent docs...")
            freed_recent = self.clean_recent_documents()
            total = freed_temp + freed_recent
            self.log_message(f"🎯 Quick Clean complete: Total freed {self.format_size(total)}")
            return f"Quick Clean freed {self.format_size(total)}"
        self.run_with_progress(quick_job, "Quick Clean")

    def deep_clean(self):
        def deep_job():
            self.update_progress(0.1, "Deep cleaning started...")
            freed_temp = self.clean_temp_files()
            self.update_progress(0.3, "Emptying recycle bin...")
            self.empty_recycle_bin()
            self.update_progress(0.45, "Cleaning recent...")
            freed_recent = self.clean_recent_documents()
            self.update_progress(0.6, "Cleaning prefetch...")
            freed_prefetch = self.clean_prefetch()
            self.update_progress(0.75, "Cleaning browser caches...")
            freed_browser = self.clean_browser_caches()
            total = freed_temp + freed_recent + freed_prefetch + freed_browser
            self.log_message(f"🔥 Deep Clean complete: Total freed {self.format_size(total)}")
            return f"Deep Clean freed {self.format_size(total)}"
        self.run_with_progress(deep_job, "Deep Clean")

    def analyze_space(self):
        def analyze_job():
            self.update_progress(0.2, "Analyzing temp files...")
            temp_size = sum(self.get_folder_size(d) for d in self.get_temp_locations() if os.path.exists(d))
            self.update_progress(0.4, "Analyzing recent...")
            recent_size = self.get_folder_size(self.get_recent_path()) if self.get_recent_path() else 0
            self.update_progress(0.6, "Analyzing browser caches...")
            browser_size = sum(self.get_folder_size(c) for c in self.get_browser_cache_paths() if os.path.exists(c))
            self.update_progress(0.8, "Analyzing prefetch...")
            prefetch_size = self.get_folder_size(self.get_prefetch_path()) if self.get_prefetch_path() and self.is_admin else 0
            total = temp_size + recent_size + browser_size + prefetch_size
            self.log_message("\n📊 ANALYSIS RESULTS:")
            self.log_message(f"   Temp files: {self.format_size(temp_size)}")
            self.log_message(f"   Recent docs: {self.format_size(recent_size)}")
            self.log_message(f"   Browser cache: {self.format_size(browser_size)}")
            self.log_message(f"   Prefetch: {self.format_size(prefetch_size)}")
            self.log_message(f"   ⭐ TOTAL CLEANABLE SPACE: {self.format_size(total)}")
            return "Analysis complete."
        self.run_with_progress(analyze_job, "Space Analysis")

    # ======================================================================
    #                          STARTUP MANAGER  (enable / disable)
    #   Uses Windows' StartupApproved keys — the same mechanism Task Manager
    #   uses — so disabling is fully reversible and never deletes the entry.
    # ======================================================================
    STARTUP_APPROVED = {
        "hkcu_run":    (r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run", "HKCU"),
        "hklm_run":    (r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run", "HKLM"),
        "hkcu_folder": (r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder", "HKCU"),
        "hklm_folder": (r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder", "HKLM"),
    }

    def _sa_hive(self, kind):
        return winreg.HKEY_LOCAL_MACHINE if kind.startswith("hklm") else winreg.HKEY_CURRENT_USER

    def _startup_state(self, kind, value_name):
        """True if enabled, False if disabled (per StartupApproved). Default enabled."""
        subkey, _ = self.STARTUP_APPROVED[kind]
        try:
            k = winreg.OpenKey(self._sa_hive(kind), subkey)
            data, _ = winreg.QueryValueEx(k, value_name)
            winreg.CloseKey(k)
            if data and len(data) >= 1:
                return data[0] != 0x03   # 0x03 == disabled; 0x02/0x06 == enabled
            return True
        except FileNotFoundError:
            return True
        except OSError:
            return True

    def _set_startup_state(self, kind, value_name, enable):
        subkey, _ = self.STARTUP_APPROVED[kind]
        k = winreg.CreateKeyEx(self._sa_hive(kind), subkey, 0, winreg.KEY_SET_VALUE | winreg.KEY_READ)
        if enable:
            data = bytes([0x02]) + b"\x00" * 11
        else:
            # 0x03 + 3 zero bytes + 8-byte FILETIME (100ns since 1601) of disable time
            ft = int((time.time() + 11644473600) * 10_000_000)
            data = bytes([0x03, 0, 0, 0]) + ft.to_bytes(8, "little")
        winreg.SetValueEx(k, value_name, 0, winreg.REG_BINARY, data)
        winreg.CloseKey(k)

    def _enumerate_startup(self):
        items = []
        for kind in ("hkcu_run", "hklm_run"):
            hive = self._sa_hive(kind)
            try:
                k = winreg.OpenKey(hive, r"Software\Microsoft\Windows\CurrentVersion\Run")
                i = 0
                while True:
                    name, value, _ = winreg.EnumValue(k, i)
                    i += 1
                    items.append({"label": f"Registry ({self.STARTUP_APPROVED[kind][1]})",
                                  "name": name, "cmd": str(value), "kind": kind, "vname": name,
                                  "enabled": self._startup_state(kind, name)})
                winreg.CloseKey(k)
            except OSError:
                pass
        user_folder = os.path.join(os.environ.get('APPDATA', ''), 'Microsoft', 'Windows',
                                   'Start Menu', 'Programs', 'Startup')
        common_folder = os.path.join(os.environ.get('ProgramData', ''), 'Microsoft', 'Windows',
                                     'Start Menu', 'Programs', 'StartUp')
        for folder, kind, lab in ((user_folder, "hkcu_folder", "Startup Folder"),
                                  (common_folder, "hklm_folder", "Startup Folder (All Users)")):
            if folder and os.path.isdir(folder):
                for item in os.listdir(folder):
                    if item.lower().endswith('.ini'):
                        continue
                    items.append({"label": lab, "name": item, "cmd": os.path.join(folder, item),
                                  "kind": kind, "vname": item,
                                  "enabled": self._startup_state(kind, item)})
        return items

    def _toggle_startup_entry(self, item, enable):
        kind = item["kind"]
        if kind.startswith("hklm") and not self.is_admin:
            self.log_message("⚠️ Changing all-users (HKLM) startup entries requires Administrator")
            messagebox.showwarning("Administrator required",
                                   "Toggling all-users startup entries needs NEO TUNE to run as Administrator.",
                                   parent=self.startup_win)
            return False
        try:
            self._set_startup_state(kind, item["vname"], enable)
            item["enabled"] = enable
            self.log_message(f"{'✅ Enabled' if enable else '⏸️ Disabled'} startup: {item['name']}")
            return True
        except PermissionError:
            self.log_message(f"⚠️ Permission denied toggling '{item['name']}' (run as admin)")
            return False
        except Exception as e:
            self.log_message(f"❌ Startup toggle error: {e}")
            return False

    def _remove_startup_entry(self, item):
        kind = item["kind"]
        if kind.startswith("hklm") and not self.is_admin:
            messagebox.showwarning("Administrator required",
                                   "Removing all-users startup entries needs Administrator rights.",
                                   parent=self.startup_win)
            return
        if not messagebox.askyesno("Remove startup entry",
                                   f"Remove '{item['name']}' from startup?\n\n"
                                   "Registry entries are deleted; Startup-folder shortcuts go to the "
                                   "Recycle Bin. (Disabling instead is reversible.)",
                                   icon="warning", parent=self.startup_win):
            return
        try:
            if kind.endswith("run"):
                k = winreg.OpenKey(self._sa_hive(kind),
                                   r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
                winreg.DeleteValue(k, item["vname"])
                winreg.CloseKey(k)
                try:  # also clear its StartupApproved record
                    subkey, _ = self.STARTUP_APPROVED[kind]
                    k2 = winreg.OpenKey(self._sa_hive(kind), subkey, 0, winreg.KEY_SET_VALUE)
                    winreg.DeleteValue(k2, item["vname"])
                    winreg.CloseKey(k2)
                except OSError:
                    pass
            else:
                ok, err = self._recycle_path(item["cmd"])
                if not ok:
                    raise RuntimeError(err)
            self.log_message(f"🗑️ Removed from startup: {item['name']}")
        except Exception as e:
            self.log_message(f"❌ Remove startup error: {e}")
        self._render_startup()

    def _open_startup_location(self, item):
        try:
            if item["kind"].endswith("folder"):
                subprocess.Popen(f'explorer /select,"{item["cmd"]}"')
            else:
                cmd = item["cmd"]
                exe = cmd.split('"')[1] if cmd.startswith('"') and '"' in cmd[1:] else cmd.split()[0]
                folder = os.path.dirname(exe.strip('"'))
                if folder and os.path.isdir(folder):
                    self._open_path(folder)
                else:
                    self._copy_to_clipboard(item["cmd"])
        except Exception as e:
            self.log_message(f"⚠️ Open location error: {e}")

    def open_startup_manager(self):
        if platform.system() != "Windows":
            self.log_message("Startup manager only available on Windows")
            return
        if getattr(self, "startup_win", None) is not None:
            try:
                if self.startup_win.winfo_exists():
                    self.startup_win.lift()
                    self.startup_win.focus_force()
                    self._render_startup()
                    return
            except Exception:
                pass

        win = ctk.CTkToplevel(self)
        self.startup_win = win
        win.title("🚀 Startup Manager")
        win.geometry("780x580")
        win.attributes('-topmost', True)
        win.after(400, lambda: win.attributes('-topmost', False))

        head = ctk.CTkFrame(win, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(14, 4))
        ctk.CTkLabel(head, text="🚀 Startup Applications", font=ctk.CTkFont(size=18, weight="bold"),
                     text_color="#00FFCC").pack(side="left")
        ctk.CTkButton(head, text="⟳ Refresh", width=90, command=self._render_startup).pack(side="right")

        self.startup_count_lbl = ctk.CTkLabel(win, text="", font=ctk.CTkFont(size=11),
                                              text_color="#9FB0C0", anchor="w")
        self.startup_count_lbl.pack(fill="x", padx=18)
        if not self.is_admin:
            ctk.CTkLabel(win, text="⚠️ Run as Administrator to toggle all-users (HKLM) entries.",
                         text_color="#FFAA55", font=ctk.CTkFont(size=11)).pack(anchor="w", padx=18)

        self.startup_list_frame = ctk.CTkScrollableFrame(win, fg_color="#0C0C12")
        self.startup_list_frame.pack(fill="both", expand=True, padx=16, pady=10)

        ctk.CTkLabel(win, text="Flip a switch to enable/disable an item at Windows startup "
                              "(reversible and Task-Manager compatible). Use 🗑 to remove it entirely.",
                     font=ctk.CTkFont(size=10), text_color="#88AAFF", wraplength=720,
                     justify="left").pack(padx=16, pady=(0, 10))
        self._render_startup()

    def _render_startup(self):
        if not getattr(self, "startup_list_frame", None):
            return
        try:
            if not self.startup_list_frame.winfo_exists():
                return
        except Exception:
            return
        for w in self.startup_list_frame.winfo_children():
            w.destroy()
        items = self._enumerate_startup()
        en = sum(1 for it in items if it["enabled"])
        self.startup_count_lbl.configure(
            text=f"{len(items)} entries · {en} enabled · {len(items) - en} disabled")
        if not items:
            ctk.CTkLabel(self.startup_list_frame, text="No startup entries found.").pack(pady=20)
            return
        for it in items:
            row = ctk.CTkFrame(self.startup_list_frame, fg_color="#15151F")
            row.pack(fill="x", pady=3, padx=4)
            var = ctk.BooleanVar(value=it["enabled"])

            def on_toggle(it=it, var=var):
                want = var.get()
                if not self._toggle_startup_entry(it, want):
                    var.set(not want)  # revert on failure

            ctk.CTkSwitch(row, text="", variable=var, command=on_toggle, width=44,
                          progress_color="#00E696").pack(side="left", padx=(10, 6), pady=8)
            info = ctk.CTkFrame(row, fg_color="transparent")
            info.pack(side="left", fill="x", expand=True, pady=4)
            ctk.CTkLabel(info, text=it["name"], font=ctk.CTkFont(size=13, weight="bold"),
                         anchor="w").pack(anchor="w")
            cmd = it["cmd"]
            cmd = (cmd[:80] + "…") if len(cmd) > 80 else cmd
            ctk.CTkLabel(info, text=f"{it['label']}  ·  {cmd}", font=ctk.CTkFont(size=10),
                         text_color="#8FA0B0", anchor="w").pack(anchor="w")
            ctk.CTkButton(row, text="🗑", width=36, fg_color="#3A2020", hover_color="#5E1F1F",
                          command=lambda it=it: self._remove_startup_entry(it)).pack(side="right", padx=(4, 10), pady=8)
            ctk.CTkButton(row, text="📂", width=36, fg_color="#1A1A2A",
                          command=lambda it=it: self._open_startup_location(it)).pack(side="right", padx=4, pady=8)

    def show_system_info(self):
        info_win = ctk.CTkToplevel(self)
        info_win.title("💻 System Information")
        info_win.geometry("600x400")
        info_win.attributes('-topmost', True)

        text_box = ctk.CTkTextbox(info_win, font=ctk.CTkFont(family="Consolas", size=12))
        text_box.pack(fill="both", expand=True, padx=20, pady=20)

        try:
            uname = platform.uname()
            info = f"""
╔══════════════════ NEO TUNE SYSTEM REPORT ══════════════════╗
║ System: {uname.system} {uname.release}
║ Node: {uname.node}
║ Version: {uname.version}
║ Machine: {uname.machine}
║ Processor: {uname.processor}
║─────────────────────────────────────────────────────────────
║ CPU Cores: {psutil.cpu_count(logical=True)} (Logical) / {psutil.cpu_count(logical=False)} (Physical)
║ RAM Total: {self.format_size(psutil.virtual_memory().total)}
║ RAM Available: {self.format_size(psutil.virtual_memory().available)}
║ Disk C: Total {self.format_size(psutil.disk_usage('C:').total)} Free {self.format_size(psutil.disk_usage('C:').free)}
║─────────────────────────────────────────────────────────────
║ OS Boot Time: {datetime.fromtimestamp(psutil.boot_time()).strftime('%Y-%m-%d %H:%M:%S')}
║ Python Version: {sys.version.split()[0]}
║ Admin Rights: {'Yes' if self.is_admin else 'No'}
╚═════════════════════════════════════════════════════════════╝
            """
            text_box.insert("1.0", info)
        except Exception as e:
            text_box.insert("1.0", f"Error gathering info: {e}")
        text_box.configure(state="disabled")


if __name__ == "__main__":
    required = ['customtkinter', 'psutil', 'pystray', 'PIL']
    missing = [pkg for pkg in required if not __import__('importlib').util.find_spec(pkg)]
    if missing:
        print("Missing required packages. Install using:")
        print(f"pip install {' '.join(missing)}")
        sys.exit(1)

    app = PCTuneUpApp()
    app.mainloop()