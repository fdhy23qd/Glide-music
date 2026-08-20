"""
Discord Rich Presence для Glide Music.

pypresence.Presence.update()/connect() — блокирующие вызовы (пишут в
unix-socket / named pipe Discord-клиента). Поэтому, как и mpris_bridge
в mpris.py, вся работа с pypresence идёт в отдельном демоне-потоке,
а из GUI-потока (и откуда угодно ещё) в него просто кладутся задачи
через queue.Queue — это потокобезопасно "из коробки".

Установка зависимости:
    pip install pypresence

Получить client_id:
    1. https://discord.com/developers/applications -> New Application
    2. Скопировать "Application ID" — это и есть client_id
    3. Rich Presence -> Art Assets -> загрузить картинку-заглушку
       с ключом, который передашь в large_image (используется как
       fallback, если обложку трека найти не удалось — см. album_art.py)

large_image поддерживает не только ключи загруженных ассетов, но и
произвольный внешний https-URL — Discord подставит картинку прямо
по ссылке (это относится только к large_image, small_image так
не умеет). Этим пользуется album_art.py для подстановки обложки трека.
"""

import threading
import queue

from pypresence import Presence, DiscordNotFound, PipeClosed

from album_art import get_artwork_url


class DiscordRPC:

    def __init__(self, client_id, large_image="glide_logo"):
        self.client_id = str(client_id)
        self.large_image = large_image

        self._queue = queue.Queue()
        self._rpc = None
        self._connected = False

        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    # ---------------- публичные методы (вызывать из любого потока) ----------------

    def update_track(self, title, artist, position_ms=0, duration_ms=0, playing=True):
        self._queue.put(("update", {
            "title": title or "Glide Music",
            "artist": artist or "",
            "position_ms": int(position_ms or 0),
            "duration_ms": int(duration_ms or 0),
            "playing": bool(playing),
        }))

    def clear(self):
        self._queue.put(("clear", None))

    def shutdown(self):
        self._queue.put(("stop", None))

    # ---------------- рабочий поток ----------------

    def _connect(self):
        try:
            self._rpc = Presence(self.client_id)
            self._rpc.connect()
            self._connected = True
        except Exception as e:
            # Discord может быть просто не запущен — это не ошибка, а норма
            self._connected = False

    def _run(self):
        self._connect()

        while True:
            try:
                action, data = self._queue.get(timeout=5)
            except queue.Empty:
                # Периодически пробуем переподключиться, если Discord
                # запустили уже после старта плеера
                if not self._connected:
                    self._connect()
                continue

            if action == "stop":
                if self._connected:
                    try:
                        self._rpc.close()
                    except Exception:
                        pass
                return

            if not self._connected:
                self._connect()
                if not self._connected:
                    continue

            try:
                if action == "clear":
                    self._rpc.clear()
                elif action == "update":
                    self._do_update(data)
            except (PipeClosed, BrokenPipeError, DiscordNotFound):
                self._connected = False
            except Exception as e:
                print("Discord RPC: обновление не удалось:", e)

    def _do_update(self, data):
        state_text = _format_time(data["position_ms"])
        if data["duration_ms"] > 0:
            state_text += f" / {_format_time(data['duration_ms'])}"
        if data["artist"]:
            state_text = f"{data['artist']} • {state_text}"

        # Сетевой запрос — мы уже в фоновом потоке, GUI это не блокирует.
        # Результат кэшируется в album_art.py по (title, artist), так что
        # на периодических обновлениях (раз в 15 сек, трек тот же) сеть
        # заново не дёргается.
        artwork_url = get_artwork_url(data["title"], data["artist"])
        large_image = artwork_url or self.large_image
        large_text = f"{data['artist']} — {data['title']}" if data["artist"] else data["title"]

        self._rpc.update(
            details=(data["title"] or "Ничего не играет")[:128],
            state=state_text[:128],
            large_image=large_image,
            large_text=(large_text or "Glide Music")[:128],
            small_image="play" if data["playing"] else "pause",
            small_text="Играет" if data["playing"] else "Пауза",
        )


def _format_time(ms):
    total_seconds = int(ms // 1000)
    minutes, seconds = divmod(total_seconds, 60)
    return f"{minutes}:{seconds:02}"