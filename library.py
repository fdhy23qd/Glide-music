"""
Фонотека без подвисаний: обход папки и чтение тегов идут в фоновом потоке,
результат приходит в GUI-поток Qt-сигналами пачками. Теги кэшируются на диске
(путь + размер + время изменения), поэтому повторный запуск с большой
библиотекой мгновенный.

Папки данных — свои для каждой системы:
    Linux:   ~/.cache/glide-music, ~/.local/share/glide-music
    Windows: %LOCALAPPDATA%\\GlideMusic\\cache, %LOCALAPPDATA%\\GlideMusic
"""

import json
import os
import sys
import threading
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from track_tags import AUDIO_EXTS, read_track_info


def app_dir(kind="data"):
    """kind: data | cache. Папка создаётся при первом обращении."""
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "GlideMusic"
        path = base / "cache" if kind == "cache" else base
    elif kind == "cache":
        path = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "glide-music"
    else:
        path = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "glide-music"
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return path


def is_audio(name):
    return Path(name).suffix.lower() in AUDIO_EXTS


class TagCache:
    """Теги по пути файла; запись считается свежей, пока не изменились размер и mtime."""

    def __init__(self):
        self._file = app_dir("cache") / "tags.json"
        self._lock = threading.Lock()
        self._dirty = False
        try:
            self._data = json.loads(self._file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._data = {}

    @staticmethod
    def _stamp(path):
        try:
            st = os.stat(path)
            return [st.st_size, int(st.st_mtime)]
        except OSError:
            return None

    def get(self, path):
        with self._lock:
            entry = self._data.get(path)
        if entry and entry.get("stamp") == self._stamp(path):
            return entry["info"]
        return None

    def put(self, path, info):
        stamp = self._stamp(path)
        if stamp is None:
            return
        with self._lock:
            self._data[path] = {"stamp": stamp, "info": info}
            self._dirty = True

    def save(self):
        with self._lock:
            if not self._dirty:
                return
            data = json.dumps(self._data, ensure_ascii=False)
            self._dirty = False
        tmp = self._file.with_suffix(".tmp")
        try:
            tmp.write_text(data, encoding="utf-8")
            os.replace(tmp, self._file)
        except OSError:
            pass


class LibraryScanner(QObject):
    """
    scan_folder(folder) / scan_files(paths) → files_found(gen, paths) сразу после обхода,
    затем tags_ready(gen, [(path, info), …]) пачками. gen растёт с каждым запросом —
    ответы устаревшего запроса GUI отбрасывает (переключили папку, пока читали теги).
    """

    files_found = pyqtSignal(int, list)
    tags_ready = pyqtSignal(int, list)
    finished = pyqtSignal(int)

    BATCH = 80

    def __init__(self, cache=None):
        super().__init__()
        self.cache = cache or TagCache()
        self._gen = 0
        self._lock = threading.Lock()

    def _next_gen(self):
        with self._lock:
            self._gen += 1
            return self._gen

    def current(self, gen):
        with self._lock:
            return gen == self._gen

    def scan_folder(self, folder):
        gen = self._next_gen()
        threading.Thread(target=self._run, args=(gen, folder, None), daemon=True).start()
        return gen

    def scan_files(self, paths):
        gen = self._next_gen()
        threading.Thread(target=self._run, args=(gen, None, list(paths)), daemon=True).start()
        return gen

    def cached(self, path):
        return self.cache.get(path)

    def _run(self, gen, folder, paths):
        if folder is not None:
            paths = []
            for root, dirs, files in os.walk(folder):
                if not self.current(gen):
                    return
                dirs.sort(key=str.lower)
                for name in sorted(files, key=str.lower):
                    if is_audio(name):
                        paths.append(str(Path(root) / name))
            self.files_found.emit(gen, paths)
        batch = []
        for path in paths:
            if not self.current(gen):
                break
            if self.cache.get(path) is not None:
                continue   # уже было в кэше — GUI взял его сам при показе списка
            info = read_track_info(path)
            self.cache.put(path, info)
            batch.append((path, info))
            if len(batch) >= self.BATCH:
                self.tags_ready.emit(gen, batch)
                batch = []
        if batch:
            self.tags_ready.emit(gen, batch)
        self.cache.save()
        self.finished.emit(gen)
