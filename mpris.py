from dbus_next.aio import MessageBus
from dbus_next.service import ServiceInterface, method, dbus_property, signal, PropertyAccess
from dbus_next import Variant
from PyQt6.QtCore import QObject, pyqtSignal, Qt
import asyncio


class PlayerBridge(QObject):
    """
    Живёт в GUI-потоке (создаётся ДО запуска mpris-потока).
    D-Bus методы (Play/Pause/Seek...) прилетают из потока dbus_next —
    здесь они через Qt-сигналы с QueuedConnection перекидываются
    на GUI-поток, где только и можно безопасно трогать QMediaPlayer.
    """

    _play = pyqtSignal()
    _pause = pyqtSignal()
    _play_pause = pyqtSignal()
    _stop = pyqtSignal()
    _seek = pyqtSignal(int)          # смещение в микросекундах
    _set_position = pyqtSignal(int)  # позиция в микросекундах

    def __init__(self, player):
        super().__init__()
        self.player = player
        self._play.connect(self._do_play, Qt.ConnectionType.QueuedConnection)
        self._pause.connect(self._do_pause, Qt.ConnectionType.QueuedConnection)
        self._play_pause.connect(self._do_play_pause, Qt.ConnectionType.QueuedConnection)
        self._stop.connect(self._do_stop, Qt.ConnectionType.QueuedConnection)
        self._seek.connect(self._do_seek, Qt.ConnectionType.QueuedConnection)
        self._set_position.connect(self._do_set_position, Qt.ConnectionType.QueuedConnection)

    # --- вызывается из потока dbus_next ---
    def request_play(self):
        self._play.emit()

    def request_pause(self):
        self._pause.emit()

    def request_play_pause(self):
        self._play_pause.emit()

    def request_stop(self):
        self._stop.emit()

    def request_seek(self, offset_us):
        self._seek.emit(int(offset_us))

    def request_set_position(self, position_us):
        self._set_position.emit(int(position_us))

    def get_volume(self):
        try:
            return float(self.player.audioOutput().volume())
        except Exception:
            return 1.0

    # --- выполняется в GUI-потоке ---
    def _do_play(self):
        self.player.play()

    def _do_pause(self):
        self.player.pause()

    def _do_play_pause(self):
        if self.player.playbackState() == self.player.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _do_stop(self):
        self.player.stop()

    def _do_seek(self, offset_us):
        new_pos_ms = self.player.position() + offset_us // 1000
        self.player.setPosition(max(0, new_pos_ms))

    def _do_set_position(self, position_us):
        self.player.setPosition(position_us // 1000)


class MPRISRoot(ServiceInterface):
    def __init__(self):
        super().__init__("org.mpris.MediaPlayer2")

    @dbus_property(access=PropertyAccess.READ)
    def Identity(self) -> "s":
        return "Glide Music"

    @dbus_property(access=PropertyAccess.READ)
    def CanQuit(self) -> "b":
        return False

    @dbus_property(access=PropertyAccess.READ)
    def CanRaise(self) -> "b":
        return False

    @dbus_property(access=PropertyAccess.READ)
    def HasTrackList(self) -> "b":
        return False

    @dbus_property(access=PropertyAccess.READ)
    def SupportedUriSchemes(self) -> "as":
        return ["file"]

    @dbus_property(access=PropertyAccess.READ)
    def SupportedMimeTypes(self) -> "as":
        return ["audio/mpeg", "audio/x-wav", "audio/ogg", "audio/mp4"]

    @dbus_property(access=PropertyAccess.READ)
    def DesktopEntry(self) -> "s":
        return "glide-music"

    @method()
    def Quit(self):
        pass

    @method()
    def Raise(self):
        pass


class MPRISPlayer(ServiceInterface):

    def __init__(self, bridge, loop):
        super().__init__("org.mpris.MediaPlayer2.Player")
        self.bridge = bridge
        self.loop = loop
        self.title = "Glide Music"
        self.artist = ["Glide Music"]
        self.length = 0
        self._status = "Stopped"
        self._position = 0

    @method()
    def Play(self):
        self.bridge.request_play()

    @method()
    def Pause(self):
        self.bridge.request_pause()

    @method()
    def PlayPause(self):
        self.bridge.request_play_pause()

    @method()
    def Stop(self):
        self.bridge.request_stop()

    @method()
    def Next(self):
        pass

    @method()
    def Previous(self):
        pass

    @method()
    def Seek(self, offset: "x"):
        self.bridge.request_seek(offset)

    @method()
    def SetPosition(self, track_id: "o", position: "x"):
        self.bridge.request_set_position(position)

    @method()
    def OpenUri(self, uri: "s"):
        pass

    @signal()
    def Seeked(self) -> "x":
        return int(self._position) * 1000

    def _playback_state_name(self):
        return self._status

    @dbus_property(access=PropertyAccess.READ)
    def PlaybackStatus(self) -> "s":
        return self._status

    @dbus_property(access=PropertyAccess.READ)
    def Metadata(self) -> "a{sv}":
        return {
            "mpris:trackid": Variant("o", "/org/mpris/MediaPlayer2/TrackList/0"),
            "xesam:title": Variant("s", self.title),
            "xesam:artist": Variant("as", self.artist),
            "mpris:length": Variant("x", int(self.length) * 1000),
        }

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
        return self.bridge.get_volume()

    @dbus_property(access=PropertyAccess.READ)
    def CanGoNext(self) -> "b":
        return False

    @dbus_property(access=PropertyAccess.READ)
    def CanGoPrevious(self) -> "b":
        return False

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

    # --- эти методы вызываются из GUI-потока (из app.py) ---

    def set_track_info(self, title, artist, duration=0):
        self.title = title or "Glide Music"
        self.artist = [artist] if isinstance(artist, str) else artist or ["Glide Music"]
        self.length = int(duration or 0)
        self._notify({"Metadata": self.Metadata})

    def set_playback_status(self, status):
        if status == self._status:
            return
        self._status = status
        self._notify({"PlaybackStatus": status})

    def set_position(self, position):
        self._position = int(position or 0)
        # По спеке MPRIS позиция не шлётся через PropertiesChanged —
        # клиенты сами поллят Position или ждут сигнал Seeked.

    def _notify(self, changed_props):
        # emit_properties_changed трогает asyncio-объекты (message bus),
        # привязанные к loop mpris-потока. Вызывать его напрямую из
        # GUI-потока нельзя — планируем через call_soon_threadsafe.
        self.loop.call_soon_threadsafe(
            lambda: self.emit_properties_changed(changed_props)
        )


async def start_mpris(bridge):

    loop = asyncio.get_running_loop()

    bus = await MessageBus().connect()

    await bus.request_name(
        "org.mpris.MediaPlayer2.GlideMusic"
    )

    root = MPRISRoot()
    player_interface = MPRISPlayer(bridge, loop)

    bus.export(
        "/org/mpris/MediaPlayer2",
        root
    )

    bus.export(
        "/org/mpris/MediaPlayer2",
        player_interface
    )

    return player_interface
