"""
MPRIS2 (Linux, D-Bus): медиаклавиши (playerctl), остров Glide Shell, плееры KDE/GNOME видят
Glide Music — название, исполнитель, альбом, обложка, позиция.

Команды с шины (Play, Next, Seek…) приходят из потока dbus_next и уходят в GUI-поток
Qt-сигналами MediaCommands (см. media_session.py). Изменения из GUI на шину — через
loop.call_soon_threadsafe: объекты шины принадлежат asyncio-циклу mpris-потока.
"""

import asyncio
import os

from dbus_next import NameFlag, RequestNameReply, Variant
from dbus_next.aio import MessageBus
from dbus_next.service import PropertyAccess, ServiceInterface, dbus_property, method, signal


class MPRISRoot(ServiceInterface):
    def __init__(self, commands):
        super().__init__("org.mpris.MediaPlayer2")
        self.commands = commands

    @dbus_property(access=PropertyAccess.READ)
    def Identity(self) -> "s":
        return "Glide Music"

    @dbus_property(access=PropertyAccess.READ)
    def CanQuit(self) -> "b":
        return False

    @dbus_property(access=PropertyAccess.READ)
    def CanRaise(self) -> "b":
        return True

    @dbus_property(access=PropertyAccess.READ)
    def HasTrackList(self) -> "b":
        return False

    @dbus_property(access=PropertyAccess.READ)
    def SupportedUriSchemes(self) -> "as":
        return ["file"]

    @dbus_property(access=PropertyAccess.READ)
    def SupportedMimeTypes(self) -> "as":
        return ["audio/mpeg", "audio/flac", "audio/x-wav", "audio/ogg", "audio/opus", "audio/mp4"]

    @dbus_property(access=PropertyAccess.READ)
    def DesktopEntry(self) -> "s":
        return "glide-music"

    @method()
    def Quit(self):
        pass

    @method()
    def Raise(self):
        self.commands.raise_window.emit()


class MPRISPlayer(ServiceInterface):

    def __init__(self, commands, loop, volume_getter):
        super().__init__("org.mpris.MediaPlayer2.Player")
        self.commands = commands
        self.loop = loop
        self.volume_getter = volume_getter
        self.title = "Glide Music"
        self.artist = ["Glide Music"]
        self.album = ""
        self.length = 0          # мс
        self.art_url = ""        # file:// или https:// обложки; пусто — нет
        self.track_no = 0
        self._status = "Stopped"
        self._position = 0       # мс

    # ---- команды с шины → GUI ----
    @method()
    def Play(self):
        self.commands.play.emit()

    @method()
    def Pause(self):
        self.commands.pause.emit()

    @method()
    def PlayPause(self):
        self.commands.toggle.emit()

    @method()
    def Stop(self):
        self.commands.stop.emit()

    @method()
    def Next(self):
        self.commands.next.emit()

    @method()
    def Previous(self):
        self.commands.previous.emit()

    @method()
    def Seek(self, offset: "x"):
        self.commands.seek.emit(max(0, self._position + int(offset) // 1000))

    @method()
    def SetPosition(self, track_id: "o", position: "x"):
        if track_id == self._track_id():   # по спецификации: позиция для другого трека — игнорировать
            self.commands.seek.emit(int(position) // 1000)

    @method()
    def OpenUri(self, uri: "s"):
        pass

    @signal()
    def Seeked(self) -> "x":
        return int(self._position) * 1000

    # ---- свойства ----
    def _track_id(self):
        return f"/org/glide/music/track/{self.track_no}"

    @dbus_property(access=PropertyAccess.READ)
    def PlaybackStatus(self) -> "s":
        return self._status

    @dbus_property(access=PropertyAccess.READ)
    def Metadata(self) -> "a{sv}":
        meta = {
            "mpris:trackid": Variant("o", self._track_id()),
            "xesam:title": Variant("s", self.title),
            "xesam:artist": Variant("as", self.artist),
            "mpris:length": Variant("x", int(self.length) * 1000),
        }
        if self.album:
            meta["xesam:album"] = Variant("s", self.album)
        if self.art_url:   # обложка для системных плееров (остров Glide Shell, KDE, GNOME)
            meta["mpris:artUrl"] = Variant("s", self.art_url)
        return meta

    @dbus_property(access=PropertyAccess.READ)
    def Position(self) -> "x":
        return int(self._position) * 1000

    @dbus_property(access=PropertyAccess.READ)
    def Rate(self) -> "d":
        return 1.0

    @dbus_property(access=PropertyAccess.READ)
    def MinimumRate(self) -> "d":
        return 1.0

    @dbus_property(access=PropertyAccess.READ)
    def MaximumRate(self) -> "d":
        return 1.0

    @dbus_property(access=PropertyAccess.READ)
    def Volume(self) -> "d":
        try:
            return float(self.volume_getter())
        except Exception:
            return 1.0

    @dbus_property(access=PropertyAccess.READ)
    def CanGoNext(self) -> "b":
        return True

    @dbus_property(access=PropertyAccess.READ)
    def CanGoPrevious(self) -> "b":
        return True

    @dbus_property(access=PropertyAccess.READ)
    def CanPlay(self) -> "b":
        return True

    @dbus_property(access=PropertyAccess.READ)
    def CanPause(self) -> "b":
        return True

    @dbus_property(access=PropertyAccess.READ)
    def CanSeek(self) -> "b":
        return True

    @dbus_property(access=PropertyAccess.READ)
    def CanControl(self) -> "b":
        return True

    # ---- из GUI-потока ----
    def set_track(self, title, artist, album, duration_ms):
        self.title = title or "Glide Music"
        self.artist = [artist] if artist else []
        self.album = album or ""
        self.length = int(duration_ms or 0)
        self.art_url = ""   # новый трек — старая обложка ему не подходит
        self.track_no += 1
        self._notify({"Metadata": self.Metadata})

    def set_length(self, duration_ms):
        if int(duration_ms or 0) != self.length:
            self.length = int(duration_ms or 0)
            self._notify({"Metadata": self.Metadata})

    def set_art_url(self, url):
        if url == self.art_url:
            return
        self.art_url = url or ""
        self._notify({"Metadata": self.Metadata})

    def set_playback_status(self, status):
        if status == self._status:
            return
        self._status = status
        self._notify({"PlaybackStatus": status})

    def set_position(self, position_ms, seeked=False):
        self._position = int(position_ms or 0)
        # обычный ход времени клиенты считают сами; о перемотке сообщаем сигналом Seeked
        if seeked:
            self.loop.call_soon_threadsafe(self.Seeked)

    def _notify(self, changed_props):
        self.loop.call_soon_threadsafe(lambda: self.emit_properties_changed(changed_props))


async def start_mpris(commands, volume_getter):
    loop = asyncio.get_running_loop()
    bus = await MessageBus().connect()
    # имя занято (второй экземпляр) — по спецификации MPRIS добавляем .instance<pid>
    reply = await bus.request_name("org.mpris.MediaPlayer2.GlideMusic", NameFlag.DO_NOT_QUEUE)
    if reply != RequestNameReply.PRIMARY_OWNER:
        await bus.request_name(f"org.mpris.MediaPlayer2.GlideMusic.instance{os.getpid()}", NameFlag.DO_NOT_QUEUE)
    root = MPRISRoot(commands)
    player = MPRISPlayer(commands, loop, volume_getter)
    bus.export("/org/mpris/MediaPlayer2", root)
    bus.export("/org/mpris/MediaPlayer2", player)
    return player
