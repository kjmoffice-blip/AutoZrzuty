#!/usr/bin/env python3
"""AutoZrzuty 1.0.1 — pojedynczy plik aplikacji (Python 3.10+).

Instalacja: python -m pip install -r requirements.txt
Uruchomienie: python autozrzuty.py
Opcjonalnie --config /pełna/ścieżka/config.json (również przy testowaniu).

GUI działa wyłącznie w głównym wątku. Jeden pracownik szereguje zrzuty,
sterowanie sesją i porządki. PDF oraz powiadomienia mają osobne wątki.
Żaden callback pynput/pystray nie dotyka tkinter — używa kolejki zdarzeń.
"""
from __future__ import annotations

import argparse
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime
import ctypes
import json
import math
import os
from pathlib import Path
import queue
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import uuid

if sys.version_info < (3, 10):
    raise SystemExit("AutoZrzuty wymaga Pythona 3.10 lub nowszego.")

try:
    import mss
    from PIL import Image, ImageDraw, ImageTk
    from reportlab.pdfgen import canvas as pdfcanvas
except ImportError as exc:
    raise SystemExit(f"Brak biblioteki: {exc}. Zainstaluj requirements.txt.") from exc

APP = "AutoZrzuty"
VERSION = "1.0.1"
BG, PANEL, FIELD = "#101722", "#1b2533", "#263346"
TEXT, MUTED, GREEN, BLUE, RED = "#edf2f7", "#a9bbcf", "#24b898", "#68adff", "#ef737b"
FILE_RE = re.compile(r"^screenshot_\d{8}_\d{6}_\d+\.jpg$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
INTERVALS = {"5 sekund": 5, "10 sekund": 10, "30 sekund": 30,
             "60 sekund": 60, "5 minut": 300}
LIMITS = {str(n): n for n in (30, 50, 100, 200, 500)}


def capture_api():
    """Nowe MSS, z kompatybilnym wywołaniem dla starszych wydań."""
    factory = getattr(mss, "MSS", None) or mss.mss
    return factory()


def default_config_path() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", str(Path.home())))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    return base / APP / "config.json"


@dataclass
class Settings:
    folder: str = field(default_factory=lambda: str(Path.home() / "Zrzuty ekranu"))
    interval: float = 10.0
    limit: int = 100
    quality: int = 90
    monitor: dict | None = None  # geometria; nie niestabilny numer monitora
    mode: str = "monitor"
    region: dict | None = None
    hotkey: str = "<f8>"
    stop_hotkey: str = "<shift>+<f8>"
    retention_enabled: bool = False
    retention_days: int = 30
    idle_enabled: bool = False
    idle_seconds: int = 60
    close_to_tray: bool = True

    def validate(self):
        if not isinstance(self.folder, str) or not self.folder.strip():
            raise ValueError("Wybierz folder docelowy.")
        if not math.isfinite(float(self.interval)) or not 0.1 <= self.interval <= 86400:
            raise ValueError("Interwał musi mieścić się w zakresie 0,1–86400 sekund.")
        if not 1 <= self.limit <= 1_000_000:
            raise ValueError("Limit musi mieścić się w zakresie 1–1000000.")
        if not 1 <= self.quality <= 100:
            raise ValueError("Jakość JPG musi mieścić się w zakresie 1–100.")
        if not 1 <= self.retention_days <= 36500:
            raise ValueError("Liczba dni musi mieścić się w zakresie 1–36500.")
        if not 1 <= self.idle_seconds <= 86400:
            raise ValueError("Próg bezczynności musi mieścić się w zakresie 1–86400 s.")
        if self.mode not in ("monitor", "active", "region"):
            raise ValueError("Nieprawidłowy tryb zrzutu.")
        for key in ("retention_enabled", "idle_enabled", "close_to_tray"):
            if not isinstance(getattr(self, key), bool):
                raise ValueError(f"Nieprawidłowa opcja: {key}.")
        for rect in (self.monitor, self.region):
            if rect is not None:
                if not isinstance(rect, dict) or any(
                    type(rect.get(k)) is not int for k in ("left", "top", "width", "height")
                ) or rect["width"] < 1 or rect["height"] < 1:
                    raise ValueError("Nieprawidłowy zapis geometrii monitora/regionu.")
        if self.mode == "region" and self.region is None:
            raise ValueError("Najpierw zaznacz region przyciskiem »Wybierz region«.")
        for key in (self.hotkey, self.stop_hotkey):
            if not isinstance(key, str) or not key.strip():
                raise ValueError("Skrót klawiaturowy nie może być pusty.")


def load_settings(path: Path) -> tuple[Settings, str | None]:
    try:
        if not path.exists():
            return Settings(), None
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Konfiguracja musi być obiektem JSON.")
        allowed = Settings.__dataclass_fields__
        settings = Settings(**{k: v for k, v in data.items() if k in allowed})
        settings.validate()
        return settings, None
    except (OSError, ValueError, TypeError) as exc:
        return Settings(), f"Nie udało się odczytać ustawień; użyto domyślnych: {exc}"


def atomic_text(path: Path, contents: str):
    """Plik tymczasowy na tym samym dysku; stary plik przetrwa błąd zapisu."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".autozrzuty-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class InstanceLock:
    """Blokada jednego procesu dla danego config.json; zwalniana także po awarii."""
    def __init__(self, config: Path):
        config.parent.mkdir(parents=True, exist_ok=True)
        self.stream = open(str(config) + ".lock", "a+b")
        try:
            if sys.platform == "win32":
                import msvcrt
                self.stream.seek(0)
                self.stream.write(b"0")
                self.stream.flush()
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.stream.close()
            raise RuntimeError("Program z tym plikiem konfiguracji jest już uruchomiony.")

    def close(self):
        self.stream.close()


def geometry(monitor: dict) -> dict:
    return {k: int(monitor[k]) for k in ("left", "top", "width", "height")}


def monitors_and_primary(sct) -> tuple[list[dict], int]:
    monitors = [geometry(m) for m in sct.monitors[1:]]
    if not monitors:
        raise RuntimeError("Nie znaleziono żadnego monitora.")
    primary = getattr(sct, "primary_monitor", None)
    if primary is not None:
        try:
            return monitors, monitors.index(geometry(primary))
        except (ValueError, TypeError, KeyError):
            pass
    # Główny ekran w Windows/macOS ma początek (0,0).
    return monitors, next((i for i, m in enumerate(monitors)
                           if m["left"] == 0 and m["top"] == 0), 0)


def contained(rect: dict, monitor: dict) -> bool:
    return (rect["left"] >= monitor["left"] and rect["top"] >= monitor["top"]
            and rect["left"] + rect["width"] <= monitor["left"] + monitor["width"]
            and rect["top"] + rect["height"] <= monitor["top"] + monitor["height"])


def screen_permission(request=False):
    if sys.platform == "darwin":
        import Quartz
        if hasattr(Quartz, "CGPreflightScreenCaptureAccess") and not Quartz.CGPreflightScreenCaptureAccess():
            if request and hasattr(Quartz, "CGRequestScreenCaptureAccess"):
                if Quartz.CGRequestScreenCaptureAccess():
                    return
            recipient = "AutoZrzuty" if getattr(sys, "frozen", False) else "Pythona/Terminala"
            raise PermissionError(f"macOS: włącz Nagrywanie ekranu i dźwięku systemowego dla {recipient} "
                                  "w Ustawieniach systemowych → Prywatność i ochrona. "
                                  "Następnie ponownie uruchom program.")


def active_window_rect() -> dict:
    """Zwraca widoczny obszar aktywnego okna, nie obraz zasłoniętego okna."""
    if sys.platform == "win32":
        import pygetwindow
        window = pygetwindow.getActiveWindow()
        if window is None or window.isMinimized or window.width <= 0 or window.height <= 0:
            raise RuntimeError("Brak aktywnego, widocznego okna.")
        return dict(left=window.left, top=window.top, width=window.width, height=window.height)
    if sys.platform == "darwin":
        import Quartz
        from AppKit import NSWorkspace
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            raise RuntimeError("Brak aktywnej aplikacji.")
        pid = app.processIdentifier()
        # W miarę dostępności AX pobierz dokładnie skupione okno (także gdy
        # aplikacja ma wiele okien). Brak Dostępności nie blokuje zrzutów ekranu.
        try:
            import ApplicationServices as AX
            obj = AX.AXUIElementCreateApplication(pid)
            error, win = AX.AXUIElementCopyAttributeValue(obj, "AXFocusedWindow", None)
            if error == 0:
                e1, position = AX.AXUIElementCopyAttributeValue(win, "AXPosition", None)
                e2, size = AX.AXUIElementCopyAttributeValue(win, "AXSize", None)
                if e1 == e2 == 0:
                    ok1, point = AX.AXValueGetValue(position, AX.kAXValueCGPointType, None)
                    ok2, dims = AX.AXValueGetValue(size, AX.kAXValueCGSizeType, None)
                    if ok1 and ok2 and dims.width > 0 and dims.height > 0:
                        return dict(left=math.floor(point.x), top=math.floor(point.y),
                                    width=math.ceil(dims.width), height=math.ceil(dims.height))
        except (ImportError, AttributeError, TypeError):
            pass
        windows = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
            Quartz.kCGNullWindowID) or []
        for win in windows:  # kolejność od przodu; tylko okna aktywnej aplikacji
            if win.get("kCGWindowOwnerPID") == pid and win.get("kCGWindowLayer") == 0:
                bounds = win.get("kCGWindowBounds", {})
                if bounds.get("Width", 0) > 1 and bounds.get("Height", 0) > 1:
                    return dict(left=math.floor(bounds["X"]), top=math.floor(bounds["Y"]),
                                width=math.ceil(bounds["Width"]), height=math.ceil(bounds["Height"]))
        raise RuntimeError("Aktywna aplikacja nie ma widocznego okna.")
    # PyGetWindow nie obsługuje Linux. Użyj udokumentowanego EWMH w X11.
    if os.environ.get("XDG_SESSION_TYPE") == "wayland":
        raise RuntimeError("Aktywne okno nie jest dostępne pod Wayland. Użyj sesji X11.")
    from Xlib import X, display
    connection = display.Display()
    try:
        prop = connection.screen().root.get_full_property(
            connection.intern_atom("_NET_ACTIVE_WINDOW"), X.AnyPropertyType)
        if not prop or not len(prop.value) or not prop.value[0]:
            raise RuntimeError("Menedżer okien nie udostępnia aktywnego okna (EWMH).")
        win = connection.create_resource_object("window", int(prop.value[0]))
        dims = win.get_geometry()
        pos = connection.screen().root.translate_coords(win, 0, 0)
        return dict(left=pos.x, top=pos.y, width=dims.width, height=dims.height)
    finally:
        connection.close()


class Activity:
    def __init__(self):
        self.last = time.monotonic()
        self.lock = threading.Lock()
        self.trusted = False

    def touch(self, *args):
        with self.lock:
            self.last = time.monotonic()

    def seconds(self) -> float:
        if sys.platform == "darwin":
            import Quartz
            return float(Quartz.CGEventSourceSecondsSinceLastEventType(
                Quartz.kCGEventSourceStateCombinedSessionState, Quartz.kCGAnyInputEventType))
        if sys.platform == "win32":
            from ctypes import wintypes
            class LASTINPUTINFO(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]
            info = LASTINPUTINFO()
            info.cbSize = ctypes.sizeof(info)
            if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
                raise RuntimeError("Nie udało się odczytać czasu bezczynności.")
            now = ctypes.windll.kernel32.GetTickCount()
            return ((now - info.dwTime) & 0xffffffff) / 1000.0
        if not self.trusted:
            raise RuntimeError("Wykrywanie bezczynności wymaga działających listenerów pynput.")
        with self.lock:
            return time.monotonic() - self.last


class CaptureStore:
    """Rejestr własnych JPG. Porządki nie wyszukują przypadkowych plików użytkownika."""
    def __init__(self, folder: str):
        self.root = Path(folder).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        database = self.root / ".autozrzuty.sqlite3"
        if database.is_symlink():
            raise RuntimeError("Rejestr AutoZrzuty nie może być dowiązaniem symbolicznym.")
        self.db = sqlite3.connect(database, timeout=5)
        self.db.execute("CREATE TABLE IF NOT EXISTS captures "
                        "(path TEXT PRIMARY KEY, created REAL, mtime INTEGER, size INTEGER)")
        self.db.commit()

    def close(self):
        self.db.close()

    def save(self, image: Image.Image, quality: int, sequence: int) -> Path:
        stamp = datetime.now()
        folder = self.root / stamp.strftime("%Y-%m-%d")
        if folder.is_symlink():
            raise RuntimeError("Podfolder daty nie może być dowiązaniem symbolicznym.")
        folder.mkdir(exist_ok=True)
        # 'xb' nie nadpisze istniejącego JPG, także przy kilku sesjach w sekundzie.
        while True:
            target = folder / f"screenshot_{stamp:%Y%m%d_%H%M%S}_{sequence:04d}.jpg"
            try:
                stream = target.open("xb")
                break
            except FileExistsError:
                sequence += 1
        try:
            with stream:
                image.save(stream, format="JPEG", quality=quality, subsampling=0)
                stream.flush()
                os.fsync(stream.fileno())
            stat = target.stat()
            self.db.execute("INSERT INTO captures VALUES (?, ?, ?, ?)",
                            (target.relative_to(self.root).as_posix(), time.time(),
                             stat.st_mtime_ns, stat.st_size))
            self.db.commit()
            return target
        except Exception:
            self.db.rollback()
            target.unlink(missing_ok=True)
            raise

    def cleanup(self, days: int, protected: list[Path], now: float | None = None) -> tuple[int, int]:
        cutoff = (time.time() if now is None else now) - days * 86400
        preserve = {p.absolute() for p in protected}
        removed, skipped = 0, 0
        for rel, created, mtime, size in self.db.execute(
                "SELECT path, created, mtime, size FROM captures WHERE created < ?", (cutoff,)).fetchall():
            parts = Path(rel).parts
            if len(parts) != 2 or not DATE_RE.fullmatch(parts[0]) or not FILE_RE.fullmatch(parts[1]):
                skipped += 1
                continue
            target = self.root / parts[0] / parts[1]
            if target.absolute() in preserve or target.parent.is_symlink() or target.is_symlink():
                skipped += 1
                continue
            try:
                stat = target.stat()
                if stat.st_mtime_ns != mtime or stat.st_size != size or not target.is_file():
                    skipped += 1
                    continue
                target.unlink()
                removed += 1
            except FileNotFoundError:
                pass
            except OSError:
                skipped += 1
                continue
            self.db.execute("DELETE FROM captures WHERE path = ?", (rel,))
        self.db.commit()
        return removed, skipped


def export_pdf(files: list[Path], target: Path):
    if not files:
        raise ValueError("Bieżąca sesja nie zawiera zrzutów.")
    if target.resolve() in {p.resolve() for p in files}:
        raise ValueError("PDF nie może zastąpić zrzutu JPG.")
    missing = [p.name for p in files if not p.is_file()]
    if missing:
        raise FileNotFoundError("Brak plików sesji: " + ", ".join(missing[:5]))
    fd, tmp = tempfile.mkstemp(prefix=".autozrzuty-pdf-", suffix=".pdf", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            pdf = pdfcanvas.Canvas(stream, pageCompression=1)
            pdf.setTitle("Zrzuty ekranu — bieżąca sesja")
            for path in files:
                with Image.open(path) as image:
                    width, height = image.size
                # Zachowaj proporcje i rozdzielczość; JPEG jest osadzany bez
                # ponownego kodowania. Duże ekrany mieszczą się na stronie.
                scale = min(0.75, 14400 / max(width, height))
                width, height = width * scale, height * scale
                pdf.setPageSize((width, height))
                pdf.drawImage(str(path), 0, 0, width=width, height=height)
                pdf.showPage()
            pdf.save()
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


class Engine(threading.Thread):
    """Szeregowanie gwarantuje dokładny limit i brak równoległych zapisów JPG."""
    def __init__(self, events: queue.Queue, activity: Activity):
        super().__init__(name="zrzuty", daemon=True)
        self.events, self.activity = events, activity
        self.commands = queue.Queue()
        self.state = "stopped"
        self.settings: Settings | None = None
        self.files: list[Path] = []
        self.session_id = ""
        self.store: CaptureStore | None = None
        self.sct = None
        self.deadline = 0.0
        self.last_cleanup = 0.0
        self.idle_skipped = False
        self.export_protected: list[Path] = []

    def emit(self, kind, value=None):
        self.events.put((kind, value))

    def command(self, name, value=None):
        self.commands.put((name, value))

    def set_state(self, state):
        self.state = state
        self.emit("state", (state, len(self.files), self.settings.limit if self.settings else 0))

    def new_session(self, settings: Settings):
        settings.validate()
        screen_permission()
        if settings.idle_enabled:
            self.activity.seconds()  # nie rozpoczynaj sesji z niedziałającym filtrem
        if self.store:
            self.store.close()
            self.store = None
        self.store = CaptureStore(settings.folder)
        self.settings = settings
        self.files = []
        self.session_id = uuid.uuid4().hex
        self.idle_skipped = False
        self.emit("session", self.session_id)
        self.last_cleanup = 0
        self.cleanup_if_due()

    def cleanup_if_due(self):
        if self.settings.retention_enabled and (not self.last_cleanup or
                time.monotonic() - self.last_cleanup >= 86400):
            deleted, skipped = self.store.cleanup(
                self.settings.retention_days, self.files + self.export_protected)
            self.last_cleanup = time.monotonic()
            self.emit("log", f"Porządki: usunięto {deleted} starych JPG; pominięto {skipped} chronionych/zmienionych.")

    def capture(self, manual=False):
        if len(self.files) >= self.settings.limit:
            self.set_state("completed")
            return
        try:
            if self.settings.idle_enabled and self.activity.seconds() >= self.settings.idle_seconds:
                if not self.idle_skipped or manual:
                    self.emit("log", "Pominięto zrzut: brak aktywności. Limit nie został zwiększony.")
                self.idle_skipped = True
                return
            if self.idle_skipped:
                self.emit("log", "Wykryto aktywność — wznowiono zrzuty.")
            self.idle_skipped = False
            screen_permission()
            self.cleanup_if_due()
            # Odświeżenie MSS wykrywa odłączone/nowe monitory.
            if self.sct:
                self.sct.close()
            self.sct = capture_api()
            monitors, primary = monitors_and_primary(self.sct)
            settings = self.settings
            if settings.mode == "active":
                rect = active_window_rect()
                desktop = geometry(self.sct.monitors[0])
                left, top = max(rect["left"], desktop["left"]), max(rect["top"], desktop["top"])
                right = min(rect["left"] + rect["width"], desktop["left"] + desktop["width"])
                bottom = min(rect["top"] + rect["height"], desktop["top"] + desktop["height"])
                if right <= left or bottom <= top:
                    raise RuntimeError("Aktywne okno znajduje się poza ekranem.")
                rect = dict(left=left, top=top, width=right-left, height=bottom-top)
            else:
                monitor = settings.monitor or monitors[primary]
                if monitor not in monitors:
                    raise RuntimeError("Wybrany monitor został odłączony lub zmienił rozdzielczość. "
                                       "Zatrzymaj sesję i odśwież listę monitorów.")
                rect = monitor if settings.mode == "monitor" else settings.region
                if not rect or not contained(rect, monitor):
                    raise RuntimeError("Region jest poza wybranym monitorem. Zaznacz go ponownie.")
            shot = self.sct.grab(rect)
            image = Image.frombytes("RGB", shot.size, shot.rgb)
            try:
                path = self.store.save(image, settings.quality, len(self.files) + 1)
                self.files.append(path)
                image.thumbnail((450, 255), Image.Resampling.LANCZOS)
                self.emit("saved", (path, image.copy(), len(self.files), settings.limit))
            finally:
                image.close()
            if len(self.files) >= settings.limit:
                self.set_state("completed")
                self.emit("limit", len(self.files))
        except Exception as exc:
            # Błąd zatrzymuje harmonogram; użytkownik decyduje o ponowieniu.
            self.set_state("stopped")
            self.emit("error", f"Nie udało się wykonać zrzutu: {exc}")

    def handle(self, command, value) -> bool:
        if command == "quit":
            return False
        if command == "start":
            if self.state == "paused":
                self.set_state("running")
                self.deadline = time.monotonic() + self.settings.interval
                self.emit("log", "Wznowiono sesję.")
            elif self.state != "running":
                self.new_session(value)
                self.set_state("running")
                self.emit("log", "Rozpoczęto nową sesję.")
                self.capture()
                self.deadline = time.monotonic() + self.settings.interval
        elif command == "pause" and self.state == "running":
            self.set_state("paused")
            self.emit("log", "Sesja wstrzymana.")
        elif command == "stop":
            self.set_state("stopped")
            self.emit("log", "Sesja zatrzymana. Pliki nadal są dostępne do eksportu.")
        elif command == "manual":
            if self.state == "completed":
                self.emit("error", "Osiągnięto limit. Użyj Start, aby rozpocząć nową sesję.")
            else:
                # Po zmianie ustawień w stanie Stop ręczny zrzut rozpoczyna nową sesję.
                if not self.settings or (self.state == "stopped" and value != self.settings):
                    self.new_session(value)
                    self.set_state("stopped")
                self.capture(manual=True)
            self.emit("manual_done")
        elif command == "export":
            self.export_protected = list(self.files)
            self.emit("export_ready", (list(self.files), value))
        elif command == "export_done":
            self.export_protected = []
        return True

    def run(self):
        try:
            while True:
                timeout = max(0.0, self.deadline - time.monotonic()) if self.state == "running" else None
                try:
                    command, value = self.commands.get(timeout=timeout)
                except queue.Empty:
                    self.capture()
                    # Brak nadrabiania kolejką: czas zapisu nie powoduje lawiny zrzutów.
                    self.deadline = time.monotonic() + self.settings.interval
                    continue
                try:
                    if not self.handle(command, value):
                        break
                except Exception as exc:
                    self.set_state("stopped")
                    self.emit("manual_done")
                    self.emit("error", str(exc))
        finally:
            if self.sct:
                self.sct.close()
            if self.store:
                self.store.close()
            self.emit("worker_closed")


class InputHooks:
    """Skróty pynput bez autorepeat i bez zmian GUI w listenerze."""
    def __init__(self, events: queue.Queue, activity: Activity):
        self.events, self.activity = events, activity
        self.keyboard = self.mouse = None
        self.hotkeys = []
        self.down = set()
        self.last_trigger = 0.0
        self.binding_signature = None
        self.ready = False
        self.module = None

    @staticmethod
    def normalize(value: str) -> str:
        value = value.strip().lower().replace(" ", "")
        parts = value.split("+")
        return "+".join(p if p.startswith("<") else
                        (f"<{p}>" if re.fullmatch(r"f\d+|ctrl|shift|alt|cmd|space|esc|tab|enter", p) else p)
                        for p in parts)

    def configure(self, start: str, stop: str):
        from pynput import keyboard
        self.module = keyboard
        signature = (self.normalize(start), self.normalize(stop))
        if signature == self.binding_signature:
            return
        keys1 = keyboard.HotKey.parse(self.normalize(start))
        keys2 = keyboard.HotKey.parse(self.normalize(stop))
        # Nie dopuszczaj skrótów zawartych w sobie (np. F8 i Shift+F8
        # obsługujemy przez preferowanie bardziej szczegółowego skrótu).
        if set(keys1) == set(keys2):
            raise ValueError("Skróty sterowania i Stop muszą się różnić.")
        with self.activity.lock:
            self.hotkeys = [(set(keys2), "stop"), (set(keys1), "toggle")]
            self.hotkeys.sort(key=lambda pair: len(pair[0]), reverse=True)
            self.down.clear()
            self.binding_signature = signature

    def press(self, key):
        self.activity.touch()
        key = self.keyboard.canonical(key)
        with self.activity.lock:
            if key in self.down:
                return
            self.down.add(key)
            for combo, action in self.hotkeys:
                if combo <= self.down:
                    now = time.monotonic()
                    if now - self.last_trigger > 0.25:
                        self.last_trigger = now
                        self.events.put(("hotkey", action))
                    break

    def release(self, key):
        self.activity.touch()
        with self.activity.lock:
            self.down.discard(self.keyboard.canonical(key))

    def start(self):
        from pynput import keyboard, mouse
        self.keyboard = keyboard.Listener(on_press=self.press, on_release=self.release)
        self.mouse = mouse.Listener(on_move=self.activity.touch, on_click=self.activity.touch,
                                    on_scroll=self.activity.touch)
        if not getattr(self.keyboard, "IS_TRUSTED", True):
            recipient = "AutoZrzuty" if getattr(sys, "frozen", False) else "Pythona lub Terminala"
            raise PermissionError("Globalne skróty wymagają uprawnień Dostępność/Monitorowanie "
                                  f"wprowadzania dla {recipient}. Nadaj je i uruchom ponownie.")
        self.keyboard.start()
        self.mouse.start()
        self.ready = True
        self.activity.trusted = True

    def alive(self):
        return self.ready and self.keyboard.is_alive() and self.mouse.is_alive()

    def stop(self):
        for listener in (self.keyboard, self.mouse):
            if listener:
                listener.stop()
        self.ready = False
        self.activity.trusted = False


def notify_system(events: queue.Queue, count: int):
    title, text = APP, f"Osiągnięto limit: {count} zrzutów. Sesja zakończona."
    try:
        if sys.platform == "darwin":
            # NSUserNotification używane przez plyer wymaga identyfikatora bundle;
            # sam plik .py go nie ma. Natywne osascript nie wymaga pakowania .app.
            script = 'on run argv\ndisplay notification (item 2 of argv) with title (item 1 of argv)\nend run'
            subprocess.run(["/usr/bin/osascript", "-e", script, title, text],
                           check=True, capture_output=True, timeout=10)
        else:
            from plyer import notification
            notification.notify(title=title, message=text, app_name=APP, timeout=8)
        events.put(("log", "Wysłano powiadomienie systemowe (widoczność zależy od ustawień systemu)."))
    except Exception as exc:
        events.put(("log", f"Nie udało się wysłać powiadomienia systemowego: {exc}"))


class RegionPicker(tk.Toplevel):
    """Zaznaczenie na obrazie monitora: działa także przy ujemnych współrzędnych
    i różnicy pomiędzy pikselami Retina a współrzędnymi pulpitu MSS."""
    def __init__(self, root, image: Image.Image, monitor: dict, accept):
        super().__init__(root)
        self.title("Zaznacz region — przeciągnij myszą")
        self.configure(bg=BG)
        self.transient(root)
        self.monitor, self.accept, self.source = monitor, accept, image
        self.photo = None
        self.area = None
        self.selection = None
        self.anchor = None
        self.offset = (0, 0)
        self.draw_size = (1, 1)
        width = min(1100, self.winfo_screenwidth() - 80)
        height = min(760, self.winfo_screenheight() - 120)
        self.geometry(f"{width}x{height}")
        ttk.Label(self, text="Przeciągnij prostokąt na obrazie monitora. Esc anuluje.").pack(pady=12)
        self.canvas = tk.Canvas(self, bg=FIELD, highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True, padx=16)
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=16, pady=12)
        self.description = ttk.Label(bar, text="Nie zaznaczono regionu.")
        self.description.pack(side="left")
        self.confirm = ttk.Button(bar, text="Użyj regionu", command=self.commit, state="disabled")
        self.confirm.pack(side="right", padx=8)
        ttk.Button(bar, text="Anuluj", command=self.cancel).pack(side="right")
        self.canvas.bind("<Configure>", self.redraw)
        self.canvas.bind("<ButtonPress-1>", self.begin)
        self.canvas.bind("<B1-Motion>", self.drag)
        self.canvas.bind("<ButtonRelease-1>", self.finish)
        self.bind("<Escape>", lambda e: self.cancel())
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.grab_set()

    def redraw(self, event=None):
        w, h = max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height())
        image = self.source.copy()
        image.thumbnail((w, h), Image.Resampling.LANCZOS)
        self.draw_size = image.size
        self.offset = ((w-image.width)//2, (h-image.height)//2)
        self.photo = ImageTk.PhotoImage(image)
        image.close()
        self.canvas.delete("all")
        self.canvas.create_image(*self.offset, anchor="nw", image=self.photo)
        self.selection = self.anchor = self.area = None
        self.confirm.configure(state="disabled")

    def point(self, event):
        x, y = self.offset
        w, h = self.draw_size
        return max(x, min(event.x, x+w)), max(y, min(event.y, y+h))

    def begin(self, event):
        self.anchor = self.point(event)
        if self.selection:
            self.canvas.delete(self.selection)
        self.selection = self.canvas.create_rectangle(*self.anchor, *self.anchor,
                                                       outline=GREEN, width=3)
        self.confirm.configure(state="disabled")

    def drag(self, event):
        if self.anchor:
            self.canvas.coords(self.selection, *self.anchor, *self.point(event))

    def finish(self, event):
        if not self.anchor:
            return
        end = self.point(event)
        x1, x2 = sorted((self.anchor[0], end[0]))
        y1, y2 = sorted((self.anchor[1], end[1]))
        ox, oy = self.offset
        dw, dh = self.draw_size
        m = self.monitor
        left = max(0, math.floor((x1-ox) * m["width"] / dw))
        top = max(0, math.floor((y1-oy) * m["height"] / dh))
        right = min(m["width"], math.ceil((x2-ox) * m["width"] / dw))
        bottom = min(m["height"], math.ceil((y2-oy) * m["height"] / dh))
        if right-left < 2 or bottom-top < 2:
            self.description.configure(text="Zaznacz większy prostokąt.")
            return
        self.area = dict(left=m["left"]+left, top=m["top"]+top,
                         width=right-left, height=bottom-top)
        self.description.configure(text=f"Region: {right-left} × {bottom-top} (współrzędne pulpitu)")
        self.confirm.configure(state="normal")

    def commit(self):
        if self.area:
            self.accept(self.area)
            self.cancel()

    def cancel(self):
        self.source.close()
        self.grab_release()
        self.destroy()


class Application:
    def __init__(self, root: tk.Tk, config: Path):
        self.root, self.config_path = root, config
        self.settings, warning = load_settings(config)
        self.events = queue.Queue()
        self.activity = Activity()
        self.hooks = InputHooks(self.events, self.activity)
        self.engine = Engine(self.events, self.activity)
        self.state, self.count, self.session_id = "stopped", 0, ""
        self.logs = deque(maxlen=15)
        self.monitors: list[dict] = []
        self.editable = []
        self.photo = None
        self.tray = None
        self.tray_ready = False
        self.manual_busy = self.pdf_busy = self.region_busy = self.closing = False
        self.monitor_busy = False
        self.pdf_thread = None
        self.save_job = None
        self.last_health = 0.0
        self.modal = False
        self.verify_mode = False
        self.build_ui()
        self.fill_settings()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.bind("<Unmap>", self.on_unmap)
        self.root.bind_all("<ButtonPress>", self.activity.touch, add="+")
        self.root.bind_all("<KeyPress>", self.activity.touch, add="+")
        self.root.report_callback_exception = self.callback_error
        if sys.platform == "darwin":
            # Command+Q ma naprawdę zamknąć program, także przy opcji tray.
            self.root.createcommand("tk::mac::Quit", self.quit)
        self.engine.start()
        self.root.after(50, self.poll)
        self.root.after(100, self.setup_hooks)
        self.root.after(150, self.refresh_monitors)
        self.setup_tray()
        self.log("Gotowy. Pierwszy zrzut po Start jest natychmiastowy.")
        if warning:
            self.log(warning)
        self.update_controls()
        for variable in self.persist_vars:
            variable.trace_add("write", self.schedule_save)

    def build_ui(self):
        root = self.root
        root.title(f"{APP} — automatyczne zrzuty ekranu")
        root.configure(bg=BG)
        width = min(1080, root.winfo_screenwidth()-60)
        height = min(860, root.winfo_screenheight()-100)
        root.geometry(f"{width}x{height}")
        root.minsize(740, 560)
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=TEXT, font=("Helvetica", 11))
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Title.TLabel", font=("Helvetica", 23, "bold"))
        style.configure("TLabelframe", background=BG, bordercolor=FIELD)
        style.configure("TLabelframe.Label", background=BG, foreground=BLUE,
                        font=("Helvetica", 11, "bold"))
        style.configure("TButton", background=FIELD, foreground=TEXT, padding=(12, 8), borderwidth=0)
        style.map("TButton", background=[("active", "#354c65"), ("disabled", "#202b3a")],
                  foreground=[("disabled", "#627187")])
        style.configure("Start.TButton", background=GREEN, foreground=BG)
        style.map("Start.TButton", background=[("active", "#49d6b6"), ("disabled", "#204438")])
        style.configure("Pause.TButton", background="#cda75c", foreground=BG)
        style.map("Pause.TButton", background=[("active", "#e8c17b"), ("disabled", "#453c2b")])
        style.configure("Stop.TButton", background=RED, foreground=BG)
        style.map("Stop.TButton", background=[("active", "#ff99a0"), ("disabled", "#482c35")])
        for name in ("TEntry", "TCombobox", "TSpinbox"):
            style.configure(name, fieldbackground=FIELD, background=FIELD,
                            foreground=TEXT, insertcolor=TEXT, padding=5)
            style.map(name, fieldbackground=[("readonly", FIELD), ("disabled", PANEL)],
                      foreground=[("readonly", TEXT), ("disabled", MUTED)],
                      selectbackground=[("!disabled", "#34577c")], selectforeground=[("!disabled", TEXT)])
        style.configure("TCheckbutton", background=BG, foreground=TEXT)
        style.map("TCheckbutton", background=[("active", BG)], foreground=[("disabled", MUTED)])
        style.configure("TRadiobutton", background=BG, foreground=TEXT)
        style.map("TRadiobutton", background=[("active", BG)], foreground=[("disabled", MUTED)])
        style.configure("Horizontal.TScale", background=BG, troughcolor=FIELD)
        style.configure("Horizontal.TProgressbar", background=GREEN, troughcolor=FIELD)
        root.option_add("*TCombobox*Listbox.background", FIELD)
        root.option_add("*TCombobox*Listbox.foreground", TEXT)
        root.option_add("*TCombobox*Listbox.selectBackground", "#34577c")
        header = ttk.Frame(root, padding=(22, 16, 22, 8))
        header.pack(fill="x")
        ttk.Label(header, text=APP, style="Title.TLabel").pack(side="left")
        ttk.Label(header, text="JPG • wiele ekranów • eksport PDF", style="Muted.TLabel").pack(side="left", padx=18)
        ttk.Button(header, text="O programie", command=self.about).pack(side="right")
        # Pionowe przewijanie udostępnia wszystkie opcje na mniejszych ekranach.
        shell = ttk.Frame(root)
        shell.pack(fill="both", expand=True, padx=20)
        self.scroller = tk.Canvas(shell, bg=BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(shell, orient="vertical", command=self.scroller.yview)
        self.scroller.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.scroller.pack(side="left", fill="both", expand=True)
        body = ttk.Frame(self.scroller)
        window = self.scroller.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda e: self.scroller.configure(scrollregion=self.scroller.bbox("all")))
        layout = {"stacked": None}
        def resize_body(event):
            self.scroller.itemconfigure(window, width=event.width)
            stacked = event.width < 960
            if layout["stacked"] == stacked:
                return
            layout["stacked"] = stacked
            left.grid_configure(row=0, column=0, columnspan=2 if stacked else 1,
                                padx=(0, 0) if stacked else (0, 14))
            right.grid_configure(row=1 if stacked else 0, column=0 if stacked else 1,
                                 columnspan=2 if stacked else 1)
        self.scroller.bind("<Configure>", resize_body)
        self.scroller.bind("<MouseWheel>", lambda e: self.scroller.yview_scroll(
            -1 if e.delta > 0 else 1, "units"))
        body.columnconfigure(0, weight=6)
        body.columnconfigure(1, weight=5)
        left, right = ttk.Frame(body), ttk.Frame(body)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        right.grid(row=0, column=1, sticky="nsew")

        self.folder = tk.StringVar()
        self.interval_choice, self.interval_custom = tk.StringVar(), tk.StringVar()
        self.limit_choice, self.limit_custom = tk.StringVar(), tk.StringVar()
        self.quality = tk.DoubleVar(value=90)
        self.quality_label = tk.StringVar(value="90")
        self.monitor_choice, self.mode = tk.StringVar(), tk.StringVar(value="monitor")
        self.region = None
        self.region_label = tk.StringVar(value="Region nie został wybrany.")
        self.hotkey, self.stop_hotkey = tk.StringVar(), tk.StringVar()
        self.retention_enabled, self.idle_enabled, self.close_to_tray = (
            tk.BooleanVar(), tk.BooleanVar(), tk.BooleanVar())
        self.retention_days, self.idle_seconds = tk.StringVar(), tk.StringVar()
        self.status = tk.StringVar(value="Gotowy")
        self.counter = tk.StringVar(value="0 zrzutów")
        self.notice = tk.StringVar(value="Ustaw parametry i kliknij Start.")
        self.persist_vars = [self.folder, self.interval_choice, self.interval_custom,
                             self.limit_choice, self.limit_custom, self.quality,
                             self.monitor_choice, self.mode, self.hotkey, self.stop_hotkey,
                             self.retention_enabled, self.idle_enabled, self.close_to_tray,
                             self.retention_days, self.idle_seconds]

        destination = self.card(left, "Zapis", 0)
        destination.columnconfigure(0, weight=1)
        self.add_edit(ttk.Entry(destination, textvariable=self.folder)).grid(row=0, column=0, sticky="ew")
        self.add_edit(ttk.Button(destination, text="Wybierz folder", command=self.choose_folder)).grid(row=0, column=1, padx=(8, 0))
        ttk.Label(destination, text="Format: JPG     Jakość:").grid(row=1, column=0, sticky="w", pady=(12, 0))
        ttk.Label(destination, textvariable=self.quality_label).grid(row=1, column=1, pady=(12, 0))
        self.add_edit(ttk.Scale(destination, from_=1, to=100, variable=self.quality,
                               command=lambda v: self.quality_label.set(str(round(float(v)))))).grid(
                                   row=2, column=0, columnspan=2, sticky="ew", pady=8)
        ttk.Label(destination, text="Każda data otrzymuje podfolder YYYY-MM-DD.", style="Muted.TLabel").grid(
            row=3, column=0, columnspan=2, sticky="w")
        schedule = self.card(left, "Harmonogram", 1)
        schedule.columnconfigure(1, weight=1)
        ttk.Label(schedule, text="Interwał").grid(row=0, column=0, sticky="w", padx=(0, 12))
        self.interval_box = self.add_edit(ttk.Combobox(schedule, textvariable=self.interval_choice,
            values=list(INTERVALS) + ["Własny"], width=14, state="readonly"), "readonly")
        self.interval_box.grid(row=0, column=1, sticky="ew")
        self.interval_entry = self.add_edit(ttk.Entry(schedule, textvariable=self.interval_custom, width=8))
        self.interval_entry.grid(row=0, column=2, padx=(8, 4))
        ttk.Label(schedule, text="s").grid(row=0, column=3)
        ttk.Label(schedule, text="Limit").grid(row=1, column=0, sticky="w", pady=10)
        self.limit_box = self.add_edit(ttk.Combobox(schedule, textvariable=self.limit_choice,
            values=list(LIMITS) + ["Własny"], width=14, state="readonly"), "readonly")
        self.limit_box.grid(row=1, column=1, sticky="ew", pady=10)
        self.limit_entry = self.add_edit(ttk.Entry(schedule, textvariable=self.limit_custom, width=8))
        self.limit_entry.grid(row=1, column=2, padx=(8, 4))
        ttk.Label(schedule, text="JPG").grid(row=1, column=3)
        self.interval_box.bind("<<ComboboxSelected>>", lambda e: self.update_controls())
        self.limit_box.bind("<<ComboboxSelected>>", lambda e: self.update_controls())
        ttk.Label(schedule, text="Ręczny zrzut wlicza się do limitu sesji.", style="Muted.TLabel").grid(
            row=2, column=0, columnspan=4, sticky="w")
        source = self.card(left, "Obszar zrzutu", 2)
        source.columnconfigure(0, weight=1)
        self.monitor_box = self.add_edit(ttk.Combobox(source, textvariable=self.monitor_choice,
                                                    state="readonly", width=28), "readonly")
        self.monitor_box.grid(row=0, column=0, sticky="ew")
        self.refresh_button = self.add_edit(ttk.Button(source, text="Odśwież", command=self.refresh_monitors))
        self.refresh_button.grid(row=0, column=1, padx=(8, 0))
        for row, (value, text) in enumerate((("monitor", "Cały wybrany monitor"),
                ("active", "Zrzut tylko aktywnego okna"), ("region", "Zrzut wybranego regionu")), 1):
            self.add_edit(ttk.Radiobutton(source, text=text, variable=self.mode, value=value,
                                          command=self.update_controls)).grid(row=row, column=0, sticky="w", pady=4)
        self.region_button = self.add_edit(ttk.Button(source, text="Wybierz region", command=self.choose_region))
        self.region_button.grid(row=3, column=1, padx=(8, 0))
        ttk.Label(source, textvariable=self.region_label, style="Muted.TLabel", wraplength=420).grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(6, 0))
        options = self.card(left, "Ustawienia", 3)
        options.columnconfigure(1, weight=1)
        ttk.Label(options, text="Start / Pauza").grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.add_edit(ttk.Entry(options, textvariable=self.hotkey, width=16)).grid(row=0, column=1, sticky="ew")
        ttk.Label(options, text="Stop").grid(row=1, column=0, sticky="w", pady=8)
        self.add_edit(ttk.Entry(options, textvariable=self.stop_hotkey, width=16)).grid(row=1, column=1, sticky="ew", pady=8)
        ttk.Label(options, text="Np. F8 albo <ctrl>+<alt>+s. Zmiany zapisują się automatycznie.",
                  style="Muted.TLabel", wraplength=430).grid(row=2, column=0, columnspan=3, sticky="w")
        self.add_edit(ttk.Checkbutton(options, text="Usuwaj własne zrzuty starsze niż", variable=self.retention_enabled,
                                     command=self.update_controls)).grid(row=3, column=0, sticky="w", pady=(12, 5))
        self.retention_entry = self.add_edit(ttk.Entry(options, textvariable=self.retention_days, width=7))
        self.retention_entry.grid(row=3, column=1, sticky="w", padx=6)
        ttk.Label(options, text="dni").grid(row=3, column=2)
        self.add_edit(ttk.Checkbutton(options, text="Pomijaj po bezczynności dłuższej niż", variable=self.idle_enabled,
                                     command=self.update_controls)).grid(row=4, column=0, sticky="w", pady=5)
        self.idle_entry = self.add_edit(ttk.Entry(options, textvariable=self.idle_seconds, width=7))
        self.idle_entry.grid(row=4, column=1, sticky="w", padx=6)
        ttk.Label(options, text="s").grid(row=4, column=2)
        self.add_edit(ttk.Checkbutton(options, text="Zamknięcie okna ukrywa do zasobnika", variable=self.close_to_tray)).grid(
            row=5, column=0, columnspan=3, sticky="w", pady=(8, 0))

        session = self.card(right, "Bieżąca sesja", 0)
        self.state_label = ttk.Label(session, textvariable=self.status, font=("Helvetica", 18, "bold"), foreground=BLUE)
        self.state_label.pack(anchor="w")
        ttk.Label(session, textvariable=self.counter, style="Muted.TLabel").pack(anchor="w", pady=8)
        self.progress = ttk.Progressbar(session, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(0, 12))
        self.notice_label = ttk.Label(session, textvariable=self.notice, wraplength=410)
        self.notice_label.pack(anchor="w", fill="x")
        preview = self.card(right, "Ostatni zrzut", 1)
        self.preview_label = ttk.Label(preview, text="Podgląd pojawi się po zapisaniu JPG.", anchor="center")
        self.preview_label.pack(fill="both", expand=True, ipady=75)
        self.last_file = ttk.Label(preview, text="—", style="Muted.TLabel", wraplength=410)
        self.last_file.pack(fill="x", pady=(8, 0))
        self.pdf_button = ttk.Button(right, text="Eksportuj sesję do PDF", command=self.choose_pdf)
        self.pdf_button.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        logbox = self.card(right, "Log — ostatnie 15 operacji", 3)
        self.log_text = tk.Text(logbox, height=12, width=36, bg=PANEL, fg=MUTED,
                                font=("Helvetica", 10), wrap="word", borderwidth=0,
                                highlightthickness=0, state="disabled", padx=6, pady=6)
        self.log_text.pack(fill="both", expand=True)
        if sys.platform == "darwin":
            permissions = ttk.Frame(root, padding=(20, 4))
            permissions.pack(fill="x")
            ttk.Label(permissions, text="Uprawnienia macOS:", style="Muted.TLabel").pack(side="left")
            ttk.Button(permissions, text="Zezwól na zrzuty", command=self.request_screen_access).pack(side="left", padx=8)
            ttk.Button(permissions, text="Ustawienia skrótów", command=self.request_input_access).pack(side="left")
        footer = ttk.Frame(root, padding=(20, 14))
        footer.pack(fill="x")
        self.start_button = ttk.Button(footer, text="Start", command=self.start, style="Start.TButton")
        self.pause_button = ttk.Button(footer, text="Pauza", command=self.pause, style="Pause.TButton")
        self.stop_button = ttk.Button(footer, text="Stop", command=self.stop, style="Stop.TButton")
        self.now_button = ttk.Button(footer, text="Zrób teraz", command=self.manual)
        for button in (self.start_button, self.pause_button, self.stop_button, self.now_button):
            button.pack(side="left", padx=(0, 8))
        ttk.Button(footer, text="Zakończ", command=self.quit).pack(side="right")
        ttk.Button(footer, text="Do zasobnika", command=self.minimize).pack(side="right", padx=8)

    def card(self, parent, title, row):
        frame = ttk.LabelFrame(parent, text=title, padding=12)
        frame.grid(row=row, column=0, sticky="ew", pady=(0, 12))
        parent.columnconfigure(0, weight=1)
        return frame

    def add_edit(self, widget, normal="normal"):
        self.editable.append((widget, normal))
        return widget

    def fill_settings(self):
        s = self.settings
        self.folder.set(s.folder)
        self.interval_choice.set(next((k for k, v in INTERVALS.items() if v == s.interval), "Własny"))
        self.interval_custom.set(str(s.interval).rstrip("0").rstrip(".") if s.interval % 1 else str(int(s.interval)))
        self.limit_choice.set(str(s.limit) if str(s.limit) in LIMITS else "Własny")
        self.limit_custom.set(str(s.limit))
        self.quality.set(s.quality)
        self.quality_label.set(str(s.quality))
        self.mode.set(s.mode)
        self.region = s.region
        self.update_region_label()
        self.hotkey.set(s.hotkey)
        self.stop_hotkey.set(s.stop_hotkey)
        for key in ("retention_enabled", "retention_days", "idle_enabled", "idle_seconds", "close_to_tray"):
            getattr(self, key).set(getattr(s, key))

    def read_settings(self) -> Settings:
        choice = self.interval_choice.get()
        interval = INTERVALS[choice] if choice in INTERVALS else float(self.interval_custom.get().replace(",", "."))
        choice = self.limit_choice.get()
        limit = LIMITS[choice] if choice in LIMITS else int(self.limit_custom.get())
        index = self.monitor_box.current()
        monitor = self.monitors[index] if 0 <= index < len(self.monitors) else self.settings.monitor
        s = Settings(folder=self.folder.get().strip(), interval=interval, limit=limit,
                     quality=round(self.quality.get()), monitor=monitor, mode=self.mode.get(),
                     region=self.region, hotkey=InputHooks.normalize(self.hotkey.get()),
                     stop_hotkey=InputHooks.normalize(self.stop_hotkey.get()),
                     retention_enabled=self.retention_enabled.get(), retention_days=int(self.retention_days.get()),
                     idle_enabled=self.idle_enabled.get(), idle_seconds=int(self.idle_seconds.get()),
                     close_to_tray=self.close_to_tray.get())
        s.validate()
        if s.mode != "active" and monitor is None:
            raise ValueError("Nie znaleziono monitora. Kliknij Odśwież.")
        if s.mode == "region" and not contained(s.region, monitor):
            raise ValueError("Region jest poza wybranym monitorem. Zaznacz go ponownie.")
        return s

    def persist(self, settings: Settings):
        if self.hooks.module:
            self.hooks.configure(settings.hotkey, settings.stop_hotkey)
        atomic_text(self.config_path, json.dumps(asdict(settings), ensure_ascii=False, indent=2) + "\n")
        self.settings = settings

    def schedule_save(self, *args):
        if self.closing:
            return
        if self.save_job:
            self.root.after_cancel(self.save_job)
        self.save_job = self.root.after(800, self.autosave)

    def autosave(self):
        self.save_job = None
        try:
            self.persist(self.read_settings())
        except (ValueError, TypeError, tk.TclError):
            pass  # użytkownik może właśnie wpisywać liczbę / zaznaczać region
        except Exception as exc:
            self.log(f"Błąd zapisu konfiguracji: {exc}")

    def checked_settings(self):
        try:
            settings = self.read_settings()
            if not self.verify_mode:
                screen_permission(request=True)
            self.persist(settings)
            return settings
        except Exception as exc:
            self.error(f"Sprawdź ustawienia: {exc}")
            return None

    def request_screen_access(self):
        try:
            screen_permission(request=True)
            self.notice.set("Zgoda na zrzuty jest aktywna. Możesz kliknąć Start.")
            self.log("macOS potwierdził dostęp do nagrywania ekranu.")
        except Exception as exc:
            self.error(str(exc))
            subprocess.Popen(["/usr/bin/open", "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"])

    def request_input_access(self):
        if sys.platform == "darwin":
            try:
                import ApplicationServices as AX
                AX.AXIsProcessTrustedWithOptions({AX.kAXTrustedCheckOptionPrompt: True})
                subprocess.Popen(["/usr/bin/open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"])
                self.notice.set("Włącz Dostępność dla AutoZrzuty, a następnie zamknij i otwórz program ponownie.")
            except Exception as exc:
                self.error(f"Nie udało się otworzyć ustawień uprawnień: {exc}")

    def setup_hooks(self):
        try:
            try:
                self.hooks.configure(self.settings.hotkey, self.settings.stop_hotkey)
            except ValueError as exc:
                self.log(f"Błędny zapis skrótu: {exc}. Tymczasowo użyto F8 / Shift+F8.")
                self.hooks.configure("<f8>", "<shift>+<f8>")
            self.hooks.start()
            self.log("Włączono globalne skróty i obserwację aktywności (bez zapisywania klawiszy).")
        except Exception as exc:
            self.hooks.stop()
            self.log(f"Globalne skróty niedostępne: {exc}")
            self.notice.set("Skróty wymagają uprawnień. Przyciski w oknie nadal działają; szczegóły w logu.")

    def setup_tray(self):
        try:
            import pystray
            image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle((4, 12, 60, 54), radius=8, fill=GREEN)
            draw.rectangle((15, 6, 33, 16), fill=GREEN)
            draw.ellipse((20, 22, 44, 46), fill=BG)
            menu = pystray.Menu(
                pystray.MenuItem("Pokaż okno", lambda icon, item: self.events.put(("show", None))),
                pystray.MenuItem("Start / Pauza / Wznów", lambda icon, item: self.events.put(("hotkey", "toggle"))),
                pystray.MenuItem("Stop", lambda icon, item: self.events.put(("hotkey", "stop"))),
                pystray.MenuItem("Zrób teraz", lambda icon, item: self.events.put(("manual", None))),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Zakończ", lambda icon, item: self.events.put(("quit", None))))
            self.tray = pystray.Icon(APP, image, APP, menu)
            def ready(icon):
                # Setup pystray działa w osobnym wątku; wszystkie operacje Cocoa
                # (także pokazywanie ikony) wykonuje główny wątek w poll().
                self.events.put(("tray_ready", None))
            # macOS wymaga głównego wątku i wspólnej pętli Cocoa z Tk.
            self.tray.run_detached(setup=ready)
        except Exception as exc:
            self.log(f"Zasobnik niedostępny; okno pozostanie dostępne: {exc}")
            self.tray = None

    def refresh_monitors(self):
        if self.monitor_busy or self.state in ("running", "paused", "starting"):
            return
        self.monitor_busy = True
        self.update_controls()
        def work():
            try:
                with capture_api() as sct:
                    self.events.put(("monitors", monitors_and_primary(sct)))
            except Exception as exc:
                self.events.put(("monitors_error", str(exc)))
        threading.Thread(target=work, name="monitory", daemon=True).start()

    def apply_monitors(self, monitors, primary):
        old = self.settings.monitor
        self.monitors = monitors
        labels = [f"Monitor {i+1}{' — główny' if i == primary else ''}  •  {m['width']} × {m['height']}"
                  for i, m in enumerate(monitors)]
        self.monitor_box.configure(values=labels)
        selected = monitors.index(old) if old in monitors else primary
        self.monitor_box.current(selected)
        if old and old not in monitors:
            self.log("Zapisany monitor jest niedostępny. Wybrano monitor główny.")
        self.monitor_busy = False
        self.log(f"Monitory: {len(monitors)}; główny: {primary+1}.")
        self.update_controls()

    def choose_folder(self):
        self.modal = True
        self.update_controls()
        try:
            folder = filedialog.askdirectory(parent=self.root, title="Wybierz folder docelowy",
                initialdir=self.folder.get() if Path(self.folder.get()).is_dir() else str(Path.home()))
            if folder:
                self.folder.set(folder)
        finally:
            self.modal = False
            self.update_controls()

    def update_region_label(self):
        if self.region:
            r = self.region
            self.region_label.set(f"Region: {r['width']} × {r['height']}, początek ({r['left']}, {r['top']}).")
        else:
            self.region_label.set("Region nie został wybrany.")

    def choose_region(self):
        if self.region_busy or self.state in ("running", "paused", "starting"):
            return
        index = self.monitor_box.current()
        if not 0 <= index < len(self.monitors):
            self.error("Najpierw wybierz dostępny monitor.")
            return
        monitor = dict(self.monitors[index])
        self.region_busy = True
        self.update_controls()
        self.notice.set("Przygotowuję obraz monitora do zaznaczenia regionu…")
        def work():
            try:
                screen_permission()
                with capture_api() as sct:
                    shot = sct.grab(monitor)
                    image = Image.frombytes("RGB", shot.size, shot.rgb)
                self.events.put(("region_image", (image, monitor)))
            except Exception as exc:
                self.events.put(("region_error", str(exc)))
        threading.Thread(target=work, name="wybór-regionu", daemon=True).start()

    def accept_region(self, rect):
        self.region = rect
        self.mode.set("region")
        self.update_region_label()
        self.schedule_save()
        self.notice.set("Region wybrany. Możesz rozpocząć sesję.")
        self.log(self.region_label.get())

    def start(self):
        if self.closing or self.region_busy or self.modal or self.manual_busy or self.state in ("running", "starting"):
            return
        if self.state == "paused":
            self.engine.command("start")
        else:
            settings = self.checked_settings()
            if not settings:
                return
            self.engine.command("start", settings)
        self.state = "starting"
        self.notice.set("Uruchamianie…")
        self.update_controls()

    def pause(self):
        if self.state == "paused":
            self.start()
        elif self.state == "running":
            self.engine.command("pause")

    def stop(self):
        if not self.closing:
            self.engine.command("stop")

    def manual(self):
        if self.closing or self.manual_busy or self.region_busy or self.modal or self.state in ("starting", "completed"):
            return
        settings = self.settings if self.state in ("running", "paused") else self.checked_settings()
        if settings:
            self.manual_busy = True
            self.engine.command("manual", settings)
            self.update_controls()

    def toggle(self):
        if self.modal:
            return
        if self.state == "running":
            self.pause()
        elif self.state in ("stopped", "paused", "completed"):
            self.start()

    def choose_pdf(self):
        if self.pdf_busy or self.count == 0:
            return
        self.modal = True
        self.update_controls()
        try:
            target = filedialog.asksaveasfilename(parent=self.root, title="Zapisz PDF z bieżącej sesji",
                initialdir=self.settings.folder, initialfile=f"sesja_{datetime.now():%Y%m%d_%H%M%S}.pdf",
                defaultextension=".pdf", filetypes=[("Dokument PDF", "*.pdf")])
        finally:
            self.modal = False
            self.update_controls()
        if target:
            self.pdf_busy = True
            self.engine.command("export", Path(target))
            self.update_controls()
            self.notice.set("Eksportuję zrzuty dostępne w chwili rozpoczęcia eksportu…")

    def begin_export(self, files, target):
        def work():
            try:
                export_pdf(files, target)
                self.events.put(("pdf_done", (target, len(files))))
            except Exception as exc:
                self.events.put(("pdf_error", str(exc)))
            finally:
                self.engine.command("export_done")
        self.pdf_thread = threading.Thread(target=work, name="eksport-pdf", daemon=True)
        self.pdf_thread.start()

    def update_controls(self):
        locked = self.closing or self.region_busy or self.modal or self.manual_busy or self.state in ("running", "paused", "starting")
        for widget, normal in self.editable:
            widget.configure(state="disabled" if locked else normal)
        self.interval_entry.configure(state="normal" if not locked and self.interval_choice.get() == "Własny" else "disabled")
        self.limit_entry.configure(state="normal" if not locked and self.limit_choice.get() == "Własny" else "disabled")
        self.retention_entry.configure(state="normal" if not locked and self.retention_enabled.get() else "disabled")
        self.idle_entry.configure(state="normal" if not locked and self.idle_enabled.get() else "disabled")
        self.refresh_button.configure(state="disabled" if locked or self.monitor_busy else "normal")
        self.region_button.configure(state="disabled" if locked or self.monitor_busy else "normal")
        busy = self.closing or self.region_busy or self.modal or self.manual_busy or self.state == "starting"
        self.start_button.configure(text="Wznów" if self.state == "paused" else "Start",
                                    state="disabled" if busy or self.state == "running" else "normal")
        self.pause_button.configure(text="Wznów" if self.state == "paused" else "Pauza",
                                    state="normal" if not busy and self.state in ("running", "paused") else "disabled")
        self.stop_button.configure(state="normal" if not self.closing and self.state in ("running", "paused", "starting") else "disabled")
        self.now_button.configure(state="disabled" if busy or self.state == "completed" else "normal")
        self.pdf_button.configure(state="normal" if self.count and not self.pdf_busy and not self.closing else "disabled")
        self.pdf_button.configure(text="Eksport trwa…" if self.pdf_busy else "Eksportuj sesję do PDF")

    def log(self, text):
        self.logs.append(f"{datetime.now():%H:%M:%S}  {text}")
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.insert("end", "\n".join(self.logs))
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def error(self, text):
        self.notice.set(text)
        self.notice_label.configure(foreground=RED)
        self.log(f"BŁĄD: {text}")
        if not self.closing:
            self.root.bell()

    def callback_error(self, exc, value, traceback):
        self.error(f"Błąd interfejsu: {value}")

    def show(self):
        if not self.closing:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()

    def minimize(self):
        if self.tray_ready and self.tray and self.tray.visible:
            self.root.withdraw()
            self.log("Okno ukryte do zasobnika. Zrzuty działają dalej.")
        else:
            self.root.iconify()
            self.log("Zasobnik niedostępny — zminimalizowano do Docka/paska zadań.")

    def on_unmap(self, event):
        if event.widget is self.root and not self.closing:
            if self.root.state() == "iconic" and self.tray_ready:
                self.root.after(50, self.minimize)

    def on_close(self):
        if self.close_to_tray.get():
            self.minimize()
        else:
            self.quit()

    def about(self):
        messagebox.showinfo("O programie", f"{APP} {VERSION}\n\n"
            "Automatyczne zrzuty JPG, region, aktywne okno, wiele monitorów i PDF.\n"
            "Wszystkie dane pozostają na komputerze. Bez telemetrii.\n\n"
            "F8: Start / Pauza / Wznów. Shift+F8: Stop (domyślnie).\n"
            "Aktywne okno oznacza jego widoczny obszar na ekranie.\n"
            "Na Macu wymagane jest Nagrywanie ekranu; skróty mogą wymagać Dostępności.\n\n"
            f"Konfiguracja:\n{self.config_path}\n\n"
            "Program open source na licencji MIT.\n"
            "Biblioteki i licencje opisano w README.", parent=self.root)

    def poll(self):
        # Limit na obrót chroni GUI przed zalaniem kolejką przy krótkim interwale.
        for _ in range(60):
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self.log(value)
            elif kind == "error":
                self.error(value)
            elif kind == "session":
                self.session_id, self.count = value, 0
                self.counter.set("0 zrzutów")
                self.progress["value"] = 0
            elif kind == "state":
                state, count, limit = value
                self.state, self.count = state, count
                labels = {"running": "Nagrywanie", "paused": "Pauza", "stopped": "Zatrzymano", "completed": "Sesja zakończona"}
                self.status.set(labels[state])
                self.state_label.configure(foreground={"running": GREEN, "paused": "#e8c17b",
                                                       "completed": GREEN}.get(state, BLUE))
                self.counter.set(f"{count} / {limit} zrzutów" if limit else "0 zrzutów")
                self.notice_label.configure(foreground=TEXT)
                self.notice.set({"running": "Zrzuty działają. Ustawienia odblokują się po Stop.",
                    "paused": "Sesja wstrzymana. Wznów kontynuuje tę samą sesję.",
                    "stopped": "Zatrzymano. Start rozpocznie nową sesję.",
                    "completed": "Osiągnięto limit. Możesz wyeksportować sesję do PDF."}[state])
                self.update_controls()
            elif kind == "saved":
                path, image, count, limit = value
                self.photo = ImageTk.PhotoImage(image)
                image.close()
                self.preview_label.configure(image=self.photo, text="", padding=0)
                self.preview_label.pack_configure(ipady=0)
                self.last_file.configure(text=path.name)
                self.count = count
                self.counter.set(f"{count} / {limit} zrzutów")
                self.progress["maximum"], self.progress["value"] = limit, count
                self.log(f"Zapisano JPG: {path.name}")
                self.update_controls()
            elif kind == "limit":
                self.log(f"Osiągnięto limit {value}. Sesja zakończona.")
                threading.Thread(target=notify_system, args=(self.events, value), daemon=True).start()
                self.show()
                # Komunikat inline nie blokuje eksportu i dalszego sterowania.
                self.notice.set(f"Gotowe! Osiągnięto limit {value} zrzutów. Zapisano je w folderze docelowym.")
            elif kind == "manual_done":
                self.manual_busy = False
                self.update_controls()
            elif kind == "hotkey" and not self.closing:
                self.stop() if value == "stop" else self.toggle()
            elif kind == "manual" and not self.closing:
                self.manual()
            elif kind == "show":
                self.show()
            elif kind == "quit":
                self.quit()
            elif kind == "tray_ready":
                try:
                    self.tray.visible = True
                    self.tray_ready = True
                    self.log("Zasobnik systemowy jest gotowy.")
                except Exception as exc:
                    self.log(f"Nie można wyświetlić zasobnika: {exc}")
            elif kind == "monitors":
                self.apply_monitors(*value)
            elif kind == "monitors_error":
                self.monitor_busy = False
                self.error(f"Błąd monitorów: {value}")
                self.update_controls()
            elif kind == "region_image":
                self.region_busy = False
                if self.closing:
                    value[0].close()
                else:
                    self.modal = True
                    picker = RegionPicker(self.root, *value, self.accept_region)
                    def picker_closed(event):
                        if event.widget is picker:
                            self.modal = False
                            self.update_controls()
                    picker.bind("<Destroy>", picker_closed)
                    self.update_controls()
            elif kind == "region_error":
                self.region_busy = False
                self.error(value)
                self.update_controls()
            elif kind == "export_ready":
                self.begin_export(*value)
            elif kind == "pdf_done":
                self.pdf_busy = False
                path, count = value
                self.notice.set(f"Zapisano PDF: {count} stron, {path}")
                self.log(f"Eksport PDF: {count} stron → {path.name}")
                self.update_controls()
            elif kind == "pdf_error":
                self.pdf_busy = False
                self.error(f"Eksport PDF nie powiódł się: {value}")
                self.update_controls()
        if not self.closing and time.monotonic() - self.last_health > 3:
            self.last_health = time.monotonic()
            if self.hooks.ready and not self.hooks.alive():
                self.hooks.stop()
                self.error("Listener pynput zakończył pracę. Skróty globalne są niedostępne; uruchom program ponownie.")
        self.root.after(50, self.poll)

    def quit(self):
        if self.closing:
            return
        try:
            self.persist(self.read_settings())
        except Exception as exc:
            self.log(f"Ostatnie poprawne ustawienia zachowano; nowe nie zostały zapisane: {exc}")
        self.closing = True
        if self.save_job:
            self.root.after_cancel(self.save_job)
        self.hooks.stop()
        self.engine.command("quit")
        self.status.set("Zamykanie…")
        self.notice.set("Czekam na dokończenie bieżącego zapisu i eksportu PDF.")
        self.update_controls()
        self.root.after(100, self.finish_quit)

    def finish_quit(self):
        if self.engine.is_alive() or self.pdf_busy or (self.pdf_thread and self.pdf_thread.is_alive()):
            self.root.after(100, self.finish_quit)
            return
        if self.tray:
            try:
                self.tray.visible = False
                self.tray.stop()
            except Exception:
                pass
        self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description="Automatyczne zrzuty ekranu JPG z GUI tkinter.")
    parser.add_argument("--config", type=Path, default=default_config_path(),
                        help="Ścieżka do config.json (domyślnie w katalogu ustawień użytkownika).")
    parser.add_argument("--verify-build", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if sys.platform == "win32":
        # Przed utworzeniem Tk i przed importem bibliotek aktywnego okna.
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except (AttributeError, OSError):
                pass
    root = tk.Tk()
    try:
        lock = InstanceLock(args.config.expanduser().resolve())
    except (OSError, RuntimeError) as exc:
        root.withdraw()
        messagebox.showerror(APP, str(exc), parent=root)
        root.destroy()
        return 1
    try:
        ui = Application(root, args.config.expanduser().resolve())
        if args.verify_build:
            import check_bundle_runtime
            check_bundle_runtime.schedule(ui, args.verify_build)
        root.mainloop()
    finally:
        lock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
