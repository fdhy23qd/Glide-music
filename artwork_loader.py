"""
Обложка трека для интерфейса, системного плеера (MPRIS / Windows SMTC) и мини-плеера.

Порядок: обложка, встроенная в сам файл (mutagen) → поиск по названию и исполнителю
в iTunes (album_art.py). Работа идёт в отдельном потоке, результат прилетает в
GUI-поток Qt-сигналом (с GUI можно работать только из GUI-потока).

Готовая картинка кладётся в кэш на диске (cover_file) — системным плеерам нужен
файл или URL, а не байты.
"""

import hashlib
import threading

from PyQt6.QtCore import QObject, pyqtSignal

from album_art import get_artwork_url, fetch_image_bytes
from library import app_dir
from track_tags import read_embedded_cover

MAX_COVERS = 80


def cover_file(data):
    """Сохраняет байты обложки в кэш (не больше MAX_COVERS файлов) и возвращает путь или None."""
    if not data:
        return None
    folder = app_dir("cache") / "covers"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        ext = ".png" if data[:8] == b"\x89PNG\r\n\x1a\n" else ".jpg"
        path = folder / (hashlib.sha1(data).hexdigest()[:16] + ext)
        if not path.exists():
            path.write_bytes(data)
            covers = sorted(folder.iterdir(), key=lambda p: p.stat().st_mtime)
            for old in covers[:-MAX_COVERS]:
                old.unlink(missing_ok=True)
        return path
    except OSError:
        return None


class ArtworkLoader(QObject):

    # (байты, id запроса, откуда: https-ссылка iTunes | "embedded" | "" — обложки нет)
    # id нужен, чтобы GUI отбросил устаревший ответ, если трек уже переключили
    artwork_ready = pyqtSignal(bytes, int, str)

    def __init__(self):
        super().__init__()
        self._request_id = 0
        self._lock = threading.Lock()

    def request(self, title, artist, path=None):
        """Запускает поиск в фоне и возвращает id запроса."""
        with self._lock:
            self._request_id += 1
            request_id = self._request_id
        threading.Thread(target=self._fetch, args=(title, artist, path, request_id), daemon=True).start()
        return request_id

    def _fetch(self, title, artist, path, request_id):
        if path:
            data = read_embedded_cover(path)
            if data:
                self.artwork_ready.emit(data, request_id, "embedded")
                return
        url = get_artwork_url(title, artist)
        data = fetch_image_bytes(url) if url else None
        self.artwork_ready.emit(data or b"", request_id, (url or "") if data else "")
