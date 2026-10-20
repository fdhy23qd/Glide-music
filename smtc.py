"""
Windows: System Media Transport Controls — плашка «Сейчас играет» у громкости и на экране
блокировки, медиаклавиши клавиатуры и гарнитуры. Аналог MPRIS на Linux.

Без окна и без упаковки приложения SMTC получают через Windows.Media.Playback.MediaPlayer
с выключенным CommandManager: мы сами говорим системе, что играет, и принимаем нажатия.

Зависимости (только Windows, см. requirements.txt):
    winrt-runtime, winrt-Windows.Media, winrt-Windows.Media.Playback,
    winrt-Windows.Storage, winrt-Windows.Storage.Streams, winrt-Windows.Foundation
"""

import asyncio
import datetime
import threading


class SmtcSession:
    """Интерфейс как у MprisSession (media_session.py). Нажатия кнопок → commands (Qt-сигналы)."""

    def __init__(self, commands):
        from winrt.windows.media import (MediaPlaybackStatus, MediaPlaybackType,
                                         SystemMediaTransportControlsButton,
                                         SystemMediaTransportControlsTimelineProperties)
        from winrt.windows.media.playback import MediaPlayer

        self._Status = MediaPlaybackStatus
        self._Button = SystemMediaTransportControlsButton
        self._Timeline = SystemMediaTransportControlsTimelineProperties
        self.commands = commands
        self._player = MediaPlayer()
        self._player.command_manager.is_enabled = False   # кнопками управляем сами
        self.smtc = self._player.system_media_transport_controls
        self.smtc.is_play_enabled = True
        self.smtc.is_pause_enabled = True
        self.smtc.is_next_enabled = True
        self.smtc.is_previous_enabled = True
        self.smtc.is_stop_enabled = True
        self.smtc.is_enabled = True
        self._token = self.smtc.add_button_pressed(self._on_button)
        self._seek_token = self.smtc.add_playback_position_change_requested(self._on_seek)
        self.updater = self.smtc.display_updater
        self.updater.type = MediaPlaybackType.MUSIC
        self._duration = 0
        self._art_gen = 0

    # ---- кнопки системы → GUI (событие приходит из потока WinRT, Qt-сигнал доставит в GUI) ----
    def _on_button(self, sender, args):
        b = args.button
        if b == self._Button.PLAY:
            self.commands.play.emit()
        elif b == self._Button.PAUSE:
            self.commands.pause.emit()
        elif b == self._Button.NEXT:
            self.commands.next.emit()
        elif b == self._Button.PREVIOUS:
            self.commands.previous.emit()
        elif b == self._Button.STOP:
            self.commands.stop.emit()

    def _on_seek(self, sender, args):
        try:
            self.commands.seek.emit(int(args.requested_playback_position.total_seconds() * 1000))
        except Exception:
            pass

    # ---- из GUI ----
    def set_track(self, title, artist, album, duration_ms):
        props = self.updater.music_properties
        props.title = title or ""
        props.artist = artist or ""
        props.album_title = album or ""
        self.updater.thumbnail = None
        self._art_gen += 1
        self.updater.update()
        self.set_length(duration_ms)

    def set_length(self, duration_ms):
        self._duration = int(duration_ms or 0)
        self.set_position(0)

    def set_status(self, status):
        self.smtc.playback_status = {
            "Playing": self._Status.PLAYING, "Paused": self._Status.PAUSED,
        }.get(status, self._Status.STOPPED)

    def set_position(self, position_ms, seeked=False):
        if self._duration <= 0:
            return
        tl = self._Timeline()
        tl.start_time = datetime.timedelta(0)
        tl.min_seek_time = datetime.timedelta(0)
        tl.end_time = datetime.timedelta(milliseconds=self._duration)
        tl.max_seek_time = datetime.timedelta(milliseconds=self._duration)
        tl.position = datetime.timedelta(milliseconds=int(position_ms or 0))
        self.smtc.update_timeline_properties(tl)

    def set_cover(self, path, url):
        """Обложка: https-ссылка (iTunes) — сразу; иначе файл из кэша (асинхронно, StorageFile)."""
        from winrt.windows.foundation import Uri
        from winrt.windows.storage.streams import RandomAccessStreamReference
        if url and url.startswith("https://"):
            self.updater.thumbnail = RandomAccessStreamReference.create_from_uri(Uri(url))
            self.updater.update()
            return
        if not path:
            return
        gen = self._art_gen
        threading.Thread(target=self._cover_from_file, args=(str(path), gen), daemon=True).start()

    def _cover_from_file(self, path, gen):
        from winrt.windows.storage import StorageFile
        from winrt.windows.storage.streams import RandomAccessStreamReference
        try:
            file = asyncio.run(_get_file(StorageFile, path))
            if gen != self._art_gen:   # трек уже сменился
                return
            self.updater.thumbnail = RandomAccessStreamReference.create_from_file(file)
            self.updater.update()
        except Exception as e:
            print("SMTC: обложка из файла не встала:", e)

    def close(self):
        try:
            self.smtc.remove_button_pressed(self._token)
            self.smtc.remove_playback_position_change_requested(self._seek_token)
            self.smtc.is_enabled = False
        except Exception:
            pass


async def _get_file(StorageFile, path):
    return await StorageFile.get_file_from_path_async(path)
