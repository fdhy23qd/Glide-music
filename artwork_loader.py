"""
Загрузка обложки трека для отображения в интерфейсе (не путать с
album_art.get_artwork_url, который используется в discord_rpc.py —
там просто нужен URL, а здесь нужны реальные байты картинки,
поэтому запрос идёт в отдельном потоке, а готовый результат
прилетает в GUI-поток через Qt-сигнал (тот же принцип, что и
PlayerBridge в mpris.py — с GUI можно работать только из GUI-потока).
"""

import threading

from PyQt6.QtCore import QObject, pyqtSignal

from album_art import get_artwork_url, fetch_image_bytes


class ArtworkLoader(QObject):

    # (image_bytes, request_id) — request_id нужен, чтобы UI-слот
    # мог отбросить устаревший ответ, если трек уже успели переключить
    artwork_ready = pyqtSignal(bytes, int)

    def __init__(self):
        super().__init__()
        self._request_id = 0
        self._lock = threading.Lock()

    def request(self, title, artist):
        """
        Запускает поиск+загрузку обложки в фоне. Возвращает id запроса —
        сравни его с тем, что придёт в artwork_ready, чтобы понять,
        актуален ли ещё результат.
        """
        with self._lock:
            self._request_id += 1
            request_id = self._request_id

        threading.Thread(
            target=self._fetch,
            args=(title, artist, request_id),
            daemon=True,
        ).start()

        return request_id

    def _fetch(self, title, artist, request_id):
        url = get_artwork_url(title, artist)
        if not url:
            return

        data = fetch_image_bytes(url)
        if not data:
            return

        self.artwork_ready.emit(data, request_id)
