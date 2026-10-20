"""
Системный плеер — одинаково для окна на обеих системах:
    Linux   — MPRIS (D-Bus): медиаклавиши, остров Glide Shell, плееры KDE/GNOME;
    Windows — SMTC: плашка «Сейчас играет», медиаклавиши, экран блокировки.

Окно знает только MediaSession: set_track / set_status / set_position / set_cover и
сигналы commands (play, next, seek…). Не получилось поднять ни то, ни другое — NullSession,
плеер работает как обычно.
"""

import asyncio
import sys
import threading

from PyQt6.QtCore import QObject, pyqtSignal


class MediaCommands(QObject):
    """Команды от системы. Создаётся в GUI-потоке: сигналы из других потоков доставятся в него."""
    play = pyqtSignal()
    pause = pyqtSignal()
    toggle = pyqtSignal()
    stop = pyqtSignal()
    next = pyqtSignal()
    previous = pyqtSignal()
    seek = pyqtSignal(int)          # абсолютная позиция, мс
    raise_window = pyqtSignal()


class NullSession:
    name = ""

    def __init__(self, commands):
        self.commands = commands

    def set_track(self, title, artist, album, duration_ms): pass
    def set_length(self, duration_ms): pass
    def set_status(self, status): pass
    def set_position(self, position_ms, seeked=False): pass
    def set_cover(self, path, url): pass
    def close(self): pass


class MprisSession(NullSession):
    """MPRIS в своём потоке с asyncio-циклом. Пока шина не поднялась, состояние копится и
    применяется после подключения."""
    name = "MPRIS"

    def __init__(self, commands, volume_getter):
        super().__init__(commands)
        self._player = None
        self._pending = []
        self._lock = threading.Lock()
        from mpris import start_mpris

        def run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                player = loop.run_until_complete(start_mpris(commands, volume_getter))
            except Exception as e:
                print("MPRIS не запустился:", e)
                return
            with self._lock:
                self._player = player
                pending, self._pending = self._pending, []
            for fn in pending:
                fn(player)
            loop.run_forever()

        threading.Thread(target=run, daemon=True).start()

    def _call(self, fn):
        with self._lock:
            if self._player is None:
                self._pending.append(fn)
                return
            player = self._player
        fn(player)

    def set_track(self, title, artist, album, duration_ms):
        self._call(lambda p: p.set_track(title, artist, album, duration_ms))

    def set_length(self, duration_ms):
        self._call(lambda p: p.set_length(duration_ms))

    def set_status(self, status):
        self._call(lambda p: p.set_playback_status(status))

    def set_position(self, position_ms, seeked=False):
        with self._lock:
            player = self._player
        if player is not None:   # позицию не копим: важна только текущая
            player.set_position(position_ms, seeked)

    def set_cover(self, path, url):
        # своему файлу — file://, его читает и остров Glide Shell без сети
        art = path.as_uri() if path else (url or "")
        self._call(lambda p: p.set_art_url(art))


def create_media_session(volume_getter):
    commands = MediaCommands()
    if sys.platform.startswith("linux"):
        try:
            return MprisSession(commands, volume_getter)
        except Exception as e:   # нет dbus-next и т.п.
            print("MPRIS недоступен:", e)
    elif sys.platform.startswith("win"):
        try:
            from smtc import SmtcSession
            session = SmtcSession(commands)
            session.name = "SMTC"
            return session
        except Exception as e:   # нет пакетов winrt или старая Windows
            print("SMTC недоступен:", e)
    return NullSession(commands)
