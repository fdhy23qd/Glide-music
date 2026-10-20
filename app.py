"""
Glide Music — музыкальный плеер экосистемы Glide для Linux и Windows.

Одна программа на обе системы; отличия — только в системном плеере (media_session.py):
MPRIS на Linux, SMTC на Windows. Мини-плеер поверх всех окон с горячей клавишей — главное
для Windows (на Linux тот же мини-плеер есть, а «поверх всех» и клавиши решает композитор).
"""

import json
import os
import random
import sys
from pathlib import Path

from PyQt6.QtCore import QByteArray, QDir, QEvent, QPoint, QSettings, QSize, Qt, QTimer, QUrl
from PyQt6.QtGui import (QBrush, QColor, QFileSystemModel, QFont, QFontDatabase, QIcon,
                         QKeySequence, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
                         QShortcut, QTransform)
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QDialog, QFileDialog,
                             QGraphicsOpacityEffect, QHBoxLayout, QKeySequenceEdit, QLabel,
                             QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu,
                             QPushButton, QSizeGrip, QSizePolicy, QSlider, QToolBar, QToolButton,
                             QTreeView, QVBoxLayout, QWidget)
from PyQt6.QtCore import QPropertyAnimation

from artwork_loader import ArtworkLoader, cover_file
from discord_rpc import DiscordRPC
from library import LibraryScanner, is_audio
from media_session import create_media_session
from mini_player import IS_WINDOWS, MiniPlayer
from track_tags import AUDIO_EXTS, read_track_info

PATH_ROLE = Qt.ItemDataRole.UserRole
SEARCH_ROLE = Qt.ItemDataRole.UserRole + 1


def resource_path(relative_path):
    """Путь к ресурсу с учётом распаковки PyInstaller во временную папку."""
    base_path = getattr(sys, "_MEIPASS", os.path.abspath(os.path.dirname(__file__)))
    return os.path.join(base_path, relative_path)


# ---------- дизайн Glide: токены как в Glide Shell и Glide Messenger ----------
def glide_tokens():
    t = {"bg": "#0f1115", "surface": "#1b1f26", "surfaceLow": "#1f242c", "surfaceHigh": "#232831",
         "surfaceHigher": "#2c3340", "surfacePressed": "#353d4b", "outline": "#2e3a4a",
         "text": "#f5f7fa", "text2": "#9aa5b5", "muted": "#6b7686", "accent": "#7b61ff"}
    # на Linux — акцент, выбранный в настройках Glide Shell
    cfg = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "glide-shell" / "config.json"
    try:
        accent = json.loads(cfg.read_text(encoding="utf-8")).get("panel", {}).get("accent", "")
        if QColor(accent).isValid() and accent.startswith("#"):
            t["accent"] = accent
    except (OSError, ValueError, AttributeError):
        pass
    a = QColor(t["accent"])
    t["accentSoft"] = f"rgba({a.red()}, {a.green()}, {a.blue()}, 0.18)"
    t["accentLine"] = f"rgba({a.red()}, {a.green()}, {a.blue()}, 0.55)"
    t["accentLight"] = a.lighter(112).name()
    t["accentPressed"] = a.darker(115).name()
    t["accentText"] = "#0f1115" if (0.299 * a.red() + 0.587 * a.green() + 0.114 * a.blue()) / 255 > 0.62 else "#ffffff"
    families = set(QFontDatabase.families())
    t["font"] = "Inter" if "Inter" in families else "Segoe UI"
    return t


def tinted_icon(name, size=18, color="#F5F5F5"):
    """SVG из icons/ → иконка нужного цвета (перекраска через SourceIn)."""
    base = QPixmap(size, size)
    base.fill(Qt.GlobalColor.transparent)
    renderer = QSvgRenderer(resource_path(f"icons/{name}.svg"))
    if renderer.isValid():
        p = QPainter(base)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(p)
        p.end()
    tinted = QPixmap(base.size())
    tinted.fill(Qt.GlobalColor.transparent)
    p = QPainter(tinted)
    p.drawPixmap(0, 0, base)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    p.fillRect(tinted.rect(), QColor(color))
    p.end()
    return QIcon(tinted)


def rounded_pixmap(pixmap, radius=8):
    size = pixmap.size()
    rounded = QPixmap(size)
    rounded.fill(Qt.GlobalColor.transparent)
    painter = QPainter(rounded)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(0, 0, size.width(), size.height(), radius, radius)
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, pixmap)
    painter.end()
    return rounded


def display_text(info):
    return f"{info['title']} — {info['artist']}" if info.get("artist") else info["title"]


class GradientLabel(QLabel):
    """Заголовок с переливом. Перелив — только под курсором: постоянная анимация 55 кадров/с
    ела процессор просто так."""

    def __init__(self, text, colors, parent=None):
        super().__init__(text, parent)
        self.color_start = QColor(colors["text"])
        self.color_end = QColor(colors["text2"])
        self.offset = 0
        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self.animate_gradient)
        self.click_count = 0
        self.bubble = None
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Нажмите 5 раз, чтобы увидеть секрет")

    def enterEvent(self, e):
        self.timer.start()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.timer.stop()
        super().leaveEvent(e)

    def animate_gradient(self):
        self.offset = (self.offset + 5) % (self.width() + 200)
        self.update()

    def mousePressEvent(self, event):
        self.click_count += 1
        if self.click_count >= 5:
            self.show_easter_egg()
            self.click_count = 0
        super().mousePressEvent(event)

    def show_easter_egg(self):
        main_window = self.window()
        if main_window is None:
            return
        if self.bubble is not None:
            self.bubble.deleteLater()
        self.bubble = QLabel("Glide Music любит вас!", main_window)
        self.bubble.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.bubble.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.bubble.setStyleSheet("""
            QLabel { background: rgba(250, 250, 250, 0.95); color: #111111;
                     border: 1px solid rgba(17, 17, 17, 0.12); border-radius: 12px;
                     padding: 10px 16px; font-weight: 700; font-size: 12px; }
        """)
        self.bubble.adjustSize()
        g = self.mapToGlobal(self.rect().topLeft())
        self.bubble.move(g.x() + self.width() // 2 - self.bubble.width() // 2, g.y() - self.bubble.height() - 10)
        effect = QGraphicsOpacityEffect(self.bubble)
        self.bubble.setGraphicsEffect(effect)
        self._bubble_anim = QPropertyAnimation(effect, b"opacity")
        self._bubble_anim.setDuration(400)
        self._bubble_anim.setStartValue(0.0)
        self._bubble_anim.setEndValue(1.0)
        self._bubble_anim.start()
        self.bubble.show()
        QTimer.singleShot(2600, self.bubble.hide)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        rect = self.rect()
        gradient = QLinearGradient(0, 0, rect.width(), 0)
        gradient.setSpread(QLinearGradient.Spread.RepeatSpread)
        gradient.setColorAt(0.0, self.color_start)
        gradient.setColorAt(0.5, self.color_end)
        gradient.setColorAt(1.0, self.color_start)
        brush = QBrush(gradient)
        transform = QTransform()
        transform.translate(-self.offset, 0)
        brush.setTransform(transform)
        pen = QPen()
        pen.setBrush(brush)
        painter.setPen(pen)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.text())


class SettingsDialog(QDialog):
    """Настройки: мини-плеер, горячая клавиша, продолжение с места остановки."""

    def __init__(self, window):
        super().__init__(window)
        self.window_ = window
        c = window.c
        s = window.settings
        self.setWindowTitle("Настройки — Glide Music")
        self.setMinimumWidth(420)
        self.setStyleSheet(window.styleSheet() + f"""
            QDialog {{ background: {c['bg']}; }}
            QLabel#hint {{ color: {c['text2']}; font-size: 12px; }}
            QLabel#section {{ color: {c['muted']}; font-size: 11px; font-weight: 700; }}
            QCheckBox {{ color: {c['text']}; font-size: 13px; spacing: 10px; }}
            QKeySequenceEdit, QLineEdit {{ background: {c['surface']}; color: {c['text']};
                border: 1px solid {c['outline']}; border-radius: 10px; padding: 6px 10px; }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(12)

        sec = QLabel("МИНИ-ПЛЕЕР")
        sec.setObjectName("section")
        lay.addWidget(sec)
        self.show_mini = QCheckBox("Показывать мини-плеер поверх всех окон")
        self.show_mini.setChecked(window.mini.isVisible())
        lay.addWidget(self.show_mini)
        self.auto_mini = QCheckBox("Показывать его, когда главное окно свёрнуто")
        self.auto_mini.setChecked(s.value("mini/auto", IS_WINDOWS, type=bool))
        lay.addWidget(self.auto_mini)

        row = QHBoxLayout()
        lbl = QLabel("Горячая клавиша — свернуть / развернуть:")
        row.addWidget(lbl, 1)
        self.hotkey = QKeySequenceEdit(QKeySequence(s.value("mini/hotkey", "Ctrl+Alt+M" if IS_WINDOWS else "")))
        self.hotkey.setMaximumSequenceLength(1)
        self.hotkey.setFixedWidth(150)
        row.addWidget(self.hotkey)
        clear = QPushButton("Сбросить")
        clear.setObjectName("btnAdd")
        clear.clicked.connect(self.hotkey.clear)
        row.addWidget(clear)
        lay.addLayout(row)
        hint = QLabel("Работает в любой программе (Windows)." if IS_WINDOWS else
                      "На Linux глобальные клавиши назначает композитор — например, в niri: "
                      "bind на «playerctl -p GlideMusic play-pause». Мини-плеер работает и без них.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        if not IS_WINDOWS:
            self.hotkey.setEnabled(False)

        sec2 = QLabel("ПРОСЛУШИВАНИЕ")
        sec2.setObjectName("section")
        lay.addWidget(sec2)
        self.resume = QCheckBox("Открываться на последнем треке и месте")
        self.resume.setChecked(s.value("resume", True, type=bool))
        lay.addWidget(self.resume)
        self.discord = QCheckBox("Показывать трек в Discord")
        self.discord.setChecked(s.value("discord", True, type=bool))
        lay.addWidget(self.discord)

        self.status = QLabel("")
        self.status.setObjectName("hint")
        lay.addWidget(self.status)
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("Отмена")
        cancel.setObjectName("btnAdd")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Сохранить")
        ok.setObjectName("btnPrimary")
        ok.clicked.connect(self.save)
        buttons.addWidget(cancel)
        buttons.addWidget(ok)
        lay.addLayout(buttons)

    def save(self):
        s = self.window_.settings
        s.setValue("mini/auto", self.auto_mini.isChecked())
        s.setValue("resume", self.resume.isChecked())
        if self.discord.isChecked() != s.value("discord", True, type=bool):
            s.setValue("discord", self.discord.isChecked())
            self.status.setText("Discord — после перезапуска Glide Music")
        self.window_.set_mini_visible(self.show_mini.isChecked())
        text = self.hotkey.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
        s.setValue("mini/hotkey", text)
        if IS_WINDOWS and text and not self.window_.mini.register_hotkey(text):
            self.status.setText(f"Клавиша {text} занята другой программой — выберите другую.")
            return
        if IS_WINDOWS and not text:
            self.window_.mini.unregister_hotkey()
        self.accept()


class GlideMusicModern(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Glide Music")
        self.setGeometry(100, 100, 960, 620)
        self.setAcceptDrops(True)
        self.c = glide_tokens()
        self.settings = QSettings("Glide", "GlideMusic")

        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.player.setAudioOutput(self.audio_output)

        # системный плеер: MPRIS (Linux) / SMTC (Windows) — медиаклавиши и «сейчас играет»
        self.session = create_media_session(lambda: self.audio_output.volume())
        cmd = self.session.commands
        cmd.play.connect(self.play)
        cmd.pause.connect(self.pause)
        cmd.toggle.connect(self.toggle_playback)
        cmd.stop.connect(self.stop)
        cmd.next.connect(self.next_track)
        cmd.previous.connect(self.prev_track)
        cmd.seek.connect(self.seek_to)
        cmd.raise_window.connect(self.bring_to_front)
        cmd.set_shuffle.connect(lambda on: on != self.is_shuffled and self.toggle_shuffle())
        cmd.set_loop.connect(self.set_loop_mode)

        # Discord Rich Presence — можно выключить в настройках (и в тестах: GLIDE_MUSIC_NO_DISCORD=1)
        self.discord_on = self.settings.value("discord", True, type=bool) and not os.environ.get("GLIDE_MUSIC_NO_DISCORD")
        self.discord_rpc = DiscordRPC(client_id="1539734149970002093")
        if self.discord_on:
            self.discord_rpc.start()
        # Discord сам время не тикает — пока трек играет, обновляем раз в 15 с
        self.discord_update_timer = QTimer(self)
        self.discord_update_timer.setInterval(15000)
        self.discord_update_timer.timeout.connect(self.update_discord_presence)

        self.current_playlist = []     # пути в порядке списка
        self.row_of = {}               # путь → строка
        self.info = {}                 # путь → теги
        self.current_track_row = -1
        self.current_path = None
        self.queue = []                # «играть следующим»
        self.history = []              # сыгранные пути — «назад» и перемешивание без повторов
        self._last_volume = 100
        self._pending_seek = None
        self._restore_path = None
        self.library_root = None

        self.scanner = LibraryScanner()
        self.scanner.files_found.connect(self.on_files_found)
        self.scanner.tags_ready.connect(self.on_tags_ready)
        self._scan_gen = 0

        self.artwork_loader = ArtworkLoader()
        self._art_request_id = 0
        self.artwork_loader.artwork_ready.connect(self.on_artwork_ready)

        self.is_shuffled = self.settings.value("shuffle", False, type=bool)
        loop = self.settings.value("loop", "")
        self.is_looped = loop if loop in ("one", "all") else False

        self.init_ui()
        self.apply_glide_theme()
        self.connect_signals()
        self.init_toolbar()
        self.init_shortcuts()
        self._apply_modes()

        self.mini = MiniPlayer(self.settings, tinted_icon, self.c)
        self.mini.toggle_requested.connect(self.toggle_playback)
        self.mini.next_requested.connect(self.next_track)
        self.mini.previous_requested.connect(self.prev_track)
        self.mini.open_main_requested.connect(self.bring_to_front)
        if self.settings.value("mini/visible", False, type=bool):
            self.mini.show()
        self.btn_mini.setProperty("active", self.mini.isVisible())
        if IS_WINDOWS:
            self.mini.register_hotkey(self.settings.value("mini/hotkey", "Ctrl+Alt+M"))

        geometry = self.settings.value("geometry")
        if isinstance(geometry, QByteArray):
            self.restoreGeometry(geometry)

        if self.settings.value("resume", True, type=bool):
            self._restore_path = self.settings.value("last/path", "") or None

        saved_root = self.settings.value("library_root", "")
        if saved_root and Path(saved_root).is_dir():
            self.set_library_root(saved_root)
        else:
            self.update_folder_panel_visibility()
            self.load_saved_library()

        saved_volume = int(self.settings.value("volume", 100))
        self.volume_slider.setValue(saved_volume)
        self.set_volume(saved_volume)

        self.save_timer = QTimer(self)   # место в треке — раз в 5 с, а не на каждом шаге
        self.save_timer.setInterval(5000)
        self.save_timer.timeout.connect(self.save_position)
        self.save_timer.start()

    # ---------- воспроизведение ----------
    def play(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PausedState or self.current_path:
            self.player.play()
        elif self.track_list.count() > 0:
            self.load_track(0)

    def pause(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()

    def stop(self):
        self.player.stop()

    def toggle_playback(self):
        state = self.player.playbackState()
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        elif self.current_path:
            self.player.play()
        elif self.track_list.count() > 0:
            self.load_track(max(0, self.track_list.currentRow()))

    def seek_to(self, ms):
        self.player.setPosition(max(0, int(ms)))
        self.session.set_position(ms, seeked=True)

    def load_track(self, row, autoplay=True, position=0):
        if not 0 <= row < len(self.current_playlist):
            return
        path = self.current_playlist[row]
        self.current_path = path
        self.current_track_row = row
        self.track_list.setCurrentRow(row)
        self._pending_seek = position or None
        self.player.setSource(QUrl.fromLocalFile(path))
        if autoplay:
            self.player.play()
        info = self.info.get(path) or read_track_info(path)
        self.info[path] = info
        self._show_now_playing(info)
        self.session.set_track(info["title"], info["artist"], info.get("album", ""), info.get("duration_ms", 0))
        self.mark_current_track(row)
        self.request_artwork(path, info)
        if not self.history or self.history[-1] != path:
            self.history.append(path)
            self.history = self.history[-500:]
        self.settings.setValue("last/path", path)
        self.on_playback_state()

    def _show_now_playing(self, info):
        self.now_playing_title.setText(info["title"])
        self.now_playing_artist.setText(info["artist"] or info.get("album") or "")
        self.mini.set_track(info["title"], info["artist"])

    def play_selected_track(self):
        row = self.track_list.currentRow()
        if row >= 0:
            self.load_track(row)

    def _shuffle_pick(self):
        played = set(self.history)
        rows = [r for r, p in enumerate(self.current_playlist) if p not in played]
        if not rows:   # всё сыграно — начинаем новый круг
            self.history = self.history[-1:]
            rows = [r for r in range(len(self.current_playlist)) if r != self.current_track_row] or [0]
        return random.choice(rows)

    def next_track(self, from_end=False):
        count = len(self.current_playlist)
        if count <= 0:
            return
        while self.queue:
            path = self.queue.pop(0)
            if path in self.row_of:
                self.update_queue_header()
                self.load_track(self.row_of[path])
                return
        self.update_queue_header()
        if self.is_shuffled:
            row = self._shuffle_pick()
        else:
            row = self.current_track_row + 1
            if row >= count:
                if from_end and self.is_looped != "all":
                    self.player.stop()
                    return
                row = 0
        self.load_track(row)

    def prev_track(self):
        # в первые 3 секунды — предыдущий трек, дальше — к началу текущего (как в большинстве плееров)
        if self.player.position() > 3000 or not self.current_playlist:
            self.player.setPosition(0)
            self.session.set_position(0, seeked=True)
            return
        if len(self.history) > 1:
            self.history.pop()
            path = self.history.pop()
            if path in self.row_of:
                self.load_track(self.row_of[path])
                return
        row = self.current_track_row - 1
        if row < 0:
            row = len(self.current_playlist) - 1 if self.is_looped == "all" else 0
        self.load_track(row)

    def handle_media_status(self, status):
        if status in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia):
            if self._pending_seek:
                self.player.setPosition(self._pending_seek)
                self._pending_seek = None
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            if self.is_looped == "one":
                self.player.setPosition(0)
                self.player.play()
            else:
                self.next_track(from_end=True)
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            self.now_playing_artist.setText("Не удалось открыть файл")

    def on_playback_state(self, *_):
        state = self.player.playbackState()
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.btn_play.setIcon(tinted_icon("PAUSE_ICON_SVG" if playing else "PLAY_ICON_SVG", 22, self.c["accentText"]))
        self.btn_play.setIconSize(QSize(22, 22))
        status = "Playing" if playing else "Paused" if state == QMediaPlayer.PlaybackState.PausedState else "Stopped"
        self.session.set_status(status)
        self.mini.set_playing(playing)
        self.update_discord_presence()

    def update_discord_presence(self):
        if not self.discord_on:
            return
        state = self.player.playbackState()
        if state == QMediaPlayer.PlaybackState.StoppedState:
            self.discord_rpc.clear()
            self.discord_update_timer.stop()
            return
        info = self.info.get(self.current_path) or {}
        self.discord_rpc.update_track(
            title=info.get("title") or self.now_playing_title.text(),
            artist=info.get("artist") or "",
            position_ms=self.player.position(),
            duration_ms=self.player.duration(),
            playing=(state == QMediaPlayer.PlaybackState.PlayingState),
        )
        if state == QMediaPlayer.PlaybackState.PlayingState:
            if not self.discord_update_timer.isActive():
                self.discord_update_timer.start()
        else:
            self.discord_update_timer.stop()

    # ---------- режимы ----------
    def _apply_modes(self):
        self._set_mode(self.btn_shuffle, "SHUFFLE_ICON_SVG", self.is_shuffled,
                       "Перемешать: включено" if self.is_shuffled else "Перемешать")
        icon = "LOOP_ONE_ICON_SVG" if self.is_looped == "one" else "LOOP_ICON_SVG"
        tip = {"one": "Повтор: один трек", "all": "Повтор: весь список"}.get(self.is_looped, "Повтор: выключен")
        self._set_mode(self.btn_loop, icon, bool(self.is_looped), tip)
        if hasattr(self, "session"):
            self.session.set_modes(self.is_shuffled, self.is_looped or "")

    def _set_mode(self, button, icon, active, tip):
        button.setIcon(tinted_icon(icon, 16, self.c["accent"] if active else self.c["text2"]))
        button.setIconSize(QSize(16, 16))
        button.setProperty("active", active)
        button.style().unpolish(button)
        button.style().polish(button)
        button.setToolTip(tip)

    def toggle_shuffle(self):
        self.is_shuffled = not self.is_shuffled
        self.settings.setValue("shuffle", self.is_shuffled)
        self._apply_modes()

    def toggle_loop(self):
        self.set_loop_mode({False: "one", "one": "all", "all": False}[self.is_looped] or "")

    def set_loop_mode(self, mode):
        self.is_looped = mode if mode in ("one", "all") else False
        self.settings.setValue("loop", self.is_looped or "")
        self._apply_modes()

    # ---------- громкость и позиция ----------
    def set_volume(self, value):
        self.audio_output.setVolume(value / 100)
        self.settings.setValue("volume", value)
        self.vol_icon.setIcon(tinted_icon("MUTE_ICON_SVG" if value == 0 else "VOLUME_ICON_SVG", 16, self.c["text"]))
        self.vol_icon.setIconSize(QSize(16, 16))

    def toggle_mute(self):
        if self.volume_slider.value() > 0:
            self._last_volume = self.volume_slider.value()
            self.volume_slider.setValue(0)
        else:
            self.volume_slider.setValue(self._last_volume or 70)

    def set_position(self, position):
        self.seek_to(position)

    def update_progress(self, position):
        if not self.progress_slider.isSliderDown():
            self.progress_slider.setValue(position)
        self.time_current.setText(self.format_time(position))
        self.session.set_position(position)
        self.mini.set_progress(position, self.player.duration())

    def update_duration(self, duration):
        self.progress_slider.setRange(0, duration)
        self.time_total.setText(self.format_time(duration))
        self.session.set_length(duration)
        self.update_discord_presence()

    def save_position(self):
        if self.current_path and self.settings.value("resume", True, type=bool):
            self.settings.setValue("last/path", self.current_path)
            self.settings.setValue("last/position", self.player.position())

    @staticmethod
    def format_time(ms):
        total_seconds = max(ms, 0) // 1000
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours}:{minutes:02}:{seconds:02}" if hours else f"{minutes}:{seconds:02}"

    # ---------- список треков ----------
    def _make_item(self, path):
        info = self.info.get(path) or self.scanner.cached(path)
        if info is None:   # тегов ещё нет — из имени файла, сканер скоро пришлёт настоящие
            stem = Path(path).stem
            info = {"title": stem, "artist": "", "album": "", "duration_ms": 0}
        else:
            self.info[path] = info
        item = QListWidgetItem(display_text(info))
        item.setData(PATH_ROLE, path)
        item.setData(SEARCH_ROLE, " ".join([info["title"], info["artist"], info.get("album", ""), Path(path).name]).lower())
        item.setToolTip(path)
        return item

    def set_tracks(self, paths):
        """Показывает список целиком; теги, которых нет в кэше, дочитываются в фоне."""
        playing = self.current_path
        self.current_playlist = list(paths)
        self.row_of = {p: i for i, p in enumerate(self.current_playlist)}
        self.track_list.setUpdatesEnabled(False)
        self.track_list.clear()
        for p in self.current_playlist:
            self.track_list.addItem(self._make_item(p))
        self.track_list.setUpdatesEnabled(True)
        self.current_track_row = self.row_of.get(playing, -1)
        self.mark_current_track(self.current_track_row)
        self.filter_tracks(self.search_input.text())
        self.update_library_header()
        missing = [p for p in self.current_playlist if p not in self.info]
        if missing:
            self._scan_gen = self.scanner.scan_files(missing)
        self._maybe_restore()

    def _maybe_restore(self):
        """Первый показ списка после запуска — встаём на последний трек (на паузе)."""
        path, self._restore_path = self._restore_path, None
        if path and path in self.row_of and not self.current_path:
            pos = int(self.settings.value("last/position", 0) or 0)
            self.load_track(self.row_of[path], autoplay=False, position=pos)
            self.track_list.scrollToItem(self.track_list.item(self.row_of[path]),
                                         QAbstractItemView.ScrollHint.PositionAtCenter)

    def on_files_found(self, gen, paths):
        if gen == self._scan_gen:
            self.set_tracks(paths)

    def on_tags_ready(self, gen, batch):
        for path, info in batch:
            self.info[path] = info
            row = self.row_of.get(path)
            if row is None:
                continue
            item = self.track_list.item(row)
            item.setText(display_text(info))
            item.setData(SEARCH_ROLE, " ".join([info["title"], info["artist"], info.get("album", ""), Path(path).name]).lower())
            if path == self.current_path:
                self._show_now_playing(info)
        if self.search_input.text():
            self.filter_tracks(self.search_input.text())

    def add_tracks(self):
        last_path = self.settings.value("last_folder", "")
        patterns = " ".join(f"*{e}" for e in sorted(AUDIO_EXTS))
        files, _ = QFileDialog.getOpenFileNames(self, "Выберите аудиофайлы", last_path, f"Аудио ({patterns})")
        if files:
            self.settings.setValue("last_folder", str(Path(files[0]).parent))
            self._add_files(files)

    def _add_files(self, files):
        new = [f for f in files if f not in self.row_of and is_audio(f)]
        if not new:
            return
        self.set_tracks(self.current_playlist + new)
        if not self.library_root:
            self.settings.setValue("library", self.current_playlist)

    def mark_current_track(self, row):
        play_icon = tinted_icon("PLAY_ICON_SVG", 14, self.c["accent"])
        empty = QIcon()
        for i in range(self.track_list.count()):
            item = self.track_list.item(i)
            current = i == row
            font = item.font()
            font.setBold(current)
            item.setFont(font)
            item.setIcon(play_icon if current else empty)
            item.setForeground(QColor(self.c["accent"] if current else self.c["text"]))

    def play_next(self):
        for item in self.track_list.selectedItems():
            path = item.data(PATH_ROLE)
            if path not in self.queue:
                self.queue.append(path)
        self.update_queue_header()

    def update_queue_header(self):
        self.queue_label.setText(f"Далее в очереди: {len(self.queue)}" if self.queue else "")
        self.queue_label.setVisible(bool(self.queue))

    # ---------- папки ----------
    def choose_library_folder(self):
        start_dir = self.library_root or self.settings.value("last_folder", "")
        folder = QFileDialog.getExistingDirectory(self, "Выберите папку с музыкой", start_dir)
        if folder:
            self.set_library_root(folder)

    def set_library_root(self, folder):
        self.library_root = folder
        self.settings.setValue("library_root", folder)
        self.folder_model.setRootPath(folder)
        root_index = self.folder_model.index(folder)
        # корень — родитель выбранной папки: сама папка видна узлом, по ней можно щёлкнуть
        self.folder_tree.setRootIndex(root_index.parent())
        self.folder_tree.setCurrentIndex(root_index)
        self.folder_tree.expand(root_index)
        self.update_folder_panel_visibility()
        self.load_tracks_from_folder(folder)

    def update_folder_panel_visibility(self):
        has_root = bool(self.library_root)
        self.folder_tree.setVisible(has_root)
        self.folder_placeholder.setVisible(not has_root)

    def on_folder_tree_selected(self, index):
        if self.folder_model.isDir(index):
            self.load_tracks_from_folder(self.folder_model.filePath(index))

    def load_tracks_from_folder(self, folder_path):
        """Обход папки и теги — в фоне; окно не замирает даже на десятках тысяч файлов."""
        self.header_label.setText("Библиотека · ищу музыку…")
        self._scan_gen = self.scanner.scan_folder(folder_path)

    def load_saved_library(self):
        saved = self.settings.value("library", [])
        if isinstance(saved, str):
            saved = [saved] if saved else []
        self.set_tracks([p for p in (saved or []) if p and Path(p).exists()])

    def update_library_header(self):
        has_tracks = self.track_list.count() > 0
        self.track_list.setVisible(has_tracks)
        self.empty_state.setVisible(not has_tracks)
        self.header_label.setText(f"Библиотека · {len(self.current_playlist)}" if has_tracks else "Библиотека")

    def filter_tracks(self, text):
        needle = text.strip().lower()
        for i in range(self.track_list.count()):
            item = self.track_list.item(i)
            item.setHidden(bool(needle) and needle not in (item.data(SEARCH_ROLE) or ""))

    def delete_selected_tracks(self):
        rows = {i.row() for i in self.track_list.selectedIndexes()}
        if not rows:
            return
        playing = self.current_path
        paths = [p for r, p in enumerate(self.current_playlist) if r not in rows]
        self.queue = [p for p in self.queue if p in paths]
        self.update_queue_header()
        if playing not in paths:
            self.player.stop()
            self.current_path = None
            self.now_playing_title.setText("Ничего не играет")
            self.now_playing_artist.setText("Glide Music")
            self.set_cover_pixmap(None)
        self.set_tracks(paths)
        if not self.library_root:
            self.settings.setValue("library", self.current_playlist)

    def show_track_context_menu(self, position):
        if not self.track_list.selectedItems():
            return
        menu = QMenu(self)
        play_next = menu.addAction("Играть следующим")
        open_folder = menu.addAction("Показать в папке")
        menu.addSeparator()
        remove = menu.addAction("Убрать из списка")
        chosen = menu.exec(self.track_list.mapToGlobal(position))
        if chosen == play_next:
            self.play_next()
        elif chosen == remove:
            self.delete_selected_tracks()
        elif chosen == open_folder:
            from PyQt6.QtGui import QDesktopServices
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self.track_list.selectedItems()[0].data(PATH_ROLE)).parent)))

    # ---------- обложка ----------
    def request_artwork(self, path, info):
        self._art_request_id = self.artwork_loader.request(info["title"], info["artist"], path)
        self.set_cover_pixmap(None)

    def set_cover_pixmap(self, pixmap):
        if pixmap is None:
            self.art_thumbnail.setPixmap(tinted_icon("MUSIC_ICON_SVG", 26, self.c["text2"]).pixmap(26, 26))
            self.mini.set_cover(None)
            return
        scaled = pixmap.scaled(self.art_thumbnail.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                               Qt.TransformationMode.SmoothTransformation)
        self.art_thumbnail.setPixmap(rounded_pixmap(scaled, radius=12))
        self.mini.set_cover(pixmap)

    def on_artwork_ready(self, data, request_id, source):
        if request_id != self._art_request_id:
            return   # трек уже переключили — обложка не от него
        pixmap = QPixmap()
        if not data or not pixmap.loadFromData(data):
            self.set_cover_pixmap(None)
            return
        self.set_cover_pixmap(pixmap)
        # системному плееру — файл из кэша (MPRIS: file://; SMTC: https-ссылка, если есть, или файл)
        self.session.set_cover(cover_file(data), source if source.startswith("https://") else "")

    # ---------- мини-плеер и окно ----------
    def set_mini_visible(self, visible):
        self.settings.setValue("mini/visible", visible)
        if visible:
            self.mini.show()
        else:
            self.mini.hide()
        self.btn_mini.setProperty("active", visible)
        self.btn_mini.style().unpolish(self.btn_mini)
        self.btn_mini.style().polish(self.btn_mini)

    def bring_to_front(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def open_settings(self):
        SettingsDialog(self).exec()

    def changeEvent(self, e):
        if e.type() == QEvent.Type.WindowStateChange and self.isMinimized():
            if self.settings.value("mini/auto", IS_WINDOWS, type=bool) and not self.mini.isVisible():
                self.mini.show_expanded()
        super().changeEvent(e)

    def closeEvent(self, e):
        self.save_position()
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("mini/visible", self.mini.isVisible())
        self.scanner.cache.save()
        self.mini.unregister_hotkey()
        self.mini.close()
        self.session.close()
        if self.discord_on:
            self.discord_rpc.shutdown()
        super().closeEvent(e)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        folders = [p for p in paths if Path(p).is_dir()]
        if folders:
            self.set_library_root(folders[0])
        else:
            self._add_files(paths)

    def toggle_maximize(self):
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def eventFilter(self, obj, event):
        # окно без рамки: тянуть — за пустое место шапки; через композитор (работает и в Wayland)
        if obj is self.toolbar_spacer:
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                handle = self.windowHandle()
                if handle is not None and handle.startSystemMove():
                    return True
                self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                return True
            if event.type() == QEvent.Type.MouseButtonDblClick:
                self.toggle_maximize()
                return True
            if (event.type() == QEvent.Type.MouseMove and (event.buttons() & Qt.MouseButton.LeftButton)
                    and hasattr(self, "_drag_pos")):
                self.move(event.globalPosition().toPoint() - self._drag_pos)
                return True
        return super().eventFilter(obj, event)

    # ---------- интерфейс ----------
    def connect_signals(self):
        self.btn_shuffle.clicked.connect(self.toggle_shuffle)
        self.btn_loop.clicked.connect(self.toggle_loop)
        self.player.mediaStatusChanged.connect(self.handle_media_status)
        self.player.playbackStateChanged.connect(self.on_playback_state)
        self.btn_add.clicked.connect(self.add_tracks)
        self.btn_choose_folder.clicked.connect(self.choose_library_folder)
        self.btn_play.clicked.connect(self.toggle_playback)
        self.btn_next.clicked.connect(lambda: self.next_track())
        self.btn_prev.clicked.connect(self.prev_track)
        self.btn_mini.clicked.connect(lambda: self.set_mini_visible(not self.mini.isVisible()))
        self.btn_settings.clicked.connect(self.open_settings)
        self.track_list.itemDoubleClicked.connect(self.play_selected_track)
        self.volume_slider.valueChanged.connect(self.set_volume)
        self.vol_icon.clicked.connect(self.toggle_mute)
        self.progress_slider.sliderMoved.connect(self.set_position)
        self.player.positionChanged.connect(self.update_progress)
        self.player.durationChanged.connect(self.update_duration)
        self.search_input.textChanged.connect(self.filter_tracks)

    def init_shortcuts(self):
        # пробел — только в списке, чтобы не мешать вводу в поиске
        play = QShortcut(QKeySequence(Qt.Key.Key_Space), self.track_list)
        play.setContext(Qt.ShortcutContext.WidgetShortcut)
        play.activated.connect(self.toggle_playback)
        enter = QShortcut(QKeySequence(Qt.Key.Key_Return), self.track_list)
        enter.setContext(Qt.ShortcutContext.WidgetShortcut)
        enter.activated.connect(self.play_selected_track)
        QShortcut(QKeySequence("Ctrl+Right"), self, activated=lambda: self.next_track())
        QShortcut(QKeySequence("Ctrl+Left"), self, activated=self.prev_track)
        QShortcut(QKeySequence("Ctrl+M"), self, activated=self.toggle_mute)
        QShortcut(QKeySequence("Ctrl+F"), self, activated=lambda: self.search_input.setFocus())
        QShortcut(QKeySequence("Ctrl+Shift+M"), self, activated=lambda: self.set_mini_visible(not self.mini.isVisible()))
        QShortcut(QKeySequence("Ctrl+,"), self, activated=self.open_settings)
        delete = QShortcut(QKeySequence(Qt.Key.Key_Delete), self.track_list)
        delete.setContext(Qt.ShortcutContext.WidgetShortcut)
        delete.activated.connect(self.delete_selected_tracks)

    def init_toolbar(self):
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.toolbar_spacer = spacer
        self.toolbar = QToolBar()
        self.toolbar.setObjectName("tool_bar")
        self.toolbar.setMovable(False)
        self.toolbar.setFixedHeight(46)
        self.addToolBar(self.toolbar)
        self.title_label = GradientLabel("Glide Music", self.c)
        self.title_label.setFont(QFont(self.c["font"], 13, QFont.Weight.DemiBold))
        self.title_label.setMinimumWidth(170)
        self.toolbar.addWidget(self.title_label)
        self.toolbar.addWidget(spacer)
        spacer.installEventFilter(self)
        for name, slot, tip in (("MIN_ICON_SVG", self.showMinimized, "Свернуть"),
                                ("MAX_ICON_SVG", self.toggle_maximize, "Развернуть"),
                                ("CLOSE_ICON_SVG", self.close, "Закрыть")):
            btn = QToolButton()
            btn.setObjectName("winBtn" if name != "CLOSE_ICON_SVG" else "winClose")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(tip)
            btn.setIcon(tinted_icon(name, 14, self.c["text"]))
            btn.setIconSize(QSize(14, 14))
            btn.setFixedSize(34, 30)   # иначе тулбар сжимает кнопку до 18 px и от значка остаётся точка
            btn.clicked.connect(slot)
            self.toolbar.addWidget(btn)

    def _icon_button(self, name, icon, size, tip, obj="controlBtn", color=None):
        b = QPushButton()
        b.setObjectName(obj)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setToolTip(tip)
        b.setIcon(tinted_icon(icon, size, color or self.c["text"]))
        b.setIconSize(QSize(size, size))
        setattr(self, name, b)
        return b

    def init_ui(self):
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.main_layout = QVBoxLayout(self.central_widget)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        # === библиотека ===
        self.content_widget = QWidget()
        self.content_widget.setObjectName("contentWidget")
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(24, 14, 24, 16)
        self.content_layout.setSpacing(14)

        header = QHBoxLayout()
        header.setSpacing(10)
        self.header_label = QLabel("Библиотека")
        self.header_label.setObjectName("headerTitle")
        self.queue_label = QLabel("")
        self.queue_label.setObjectName("queueLabel")
        self.queue_label.setVisible(False)
        self.search_input = QLineEdit()
        self.search_input.setObjectName("searchInput")
        self.search_input.setPlaceholderText("Название, исполнитель, альбом… (Ctrl+F)")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setFixedWidth(280)
        self.btn_add = QPushButton("Добавить")
        self.btn_add.setObjectName("btnAdd")
        self.btn_add.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_add.setToolTip("Добавить аудиофайлы (или перетащите файлы и папки в окно)")
        self.btn_add.setIcon(tinted_icon("ADD_ICON_SVG", 14, self.c["text"]))
        header.addWidget(self.header_label)
        header.addWidget(self.queue_label)
        header.addStretch()
        header.addWidget(self.search_input)
        header.addWidget(self.btn_add)
        self.content_layout.addLayout(header)

        body = QHBoxLayout()
        body.setSpacing(14)
        column = QVBoxLayout()
        self.track_list = QListWidget()
        self.track_list.setObjectName("modernList")
        self.track_list.setUniformItemSizes(True)   # быстрее на больших списках
        self.track_list.setIconSize(QSize(14, 14))
        self.track_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.track_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.track_list.customContextMenuRequested.connect(self.show_track_context_menu)
        column.addWidget(self.track_list)
        self.empty_state = QLabel("Библиотека пуста\n\nНажмите «Добавить», выберите папку с музыкой справа\nили перетащите файлы в окно")
        self.empty_state.setObjectName("emptyState")
        self.empty_state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_state.setVisible(False)
        column.addWidget(self.empty_state)
        body.addLayout(column, 3)

        self.folder_panel = QWidget()
        self.folder_panel.setObjectName("folderPanel")
        self.folder_panel.setFixedWidth(240)
        fp = QVBoxLayout(self.folder_panel)
        fp.setContentsMargins(12, 12, 12, 12)
        fp.setSpacing(10)
        fh = QHBoxLayout()
        title = QLabel("Папки")
        title.setObjectName("folderHeaderTitle")
        self.btn_choose_folder = QToolButton()
        self.btn_choose_folder.setObjectName("btnChooseFolder")
        self.btn_choose_folder.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_choose_folder.setText("Выбрать")
        self.btn_choose_folder.setToolTip("Выбрать папку с музыкой")
        fh.addWidget(title)
        fh.addStretch()
        fh.addWidget(self.btn_choose_folder)
        fp.addLayout(fh)
        self.folder_model = QFileSystemModel()
        self.folder_model.setFilter(QDir.Filter.NoDotAndDotDot | QDir.Filter.Dirs)
        self.folder_tree = QTreeView()
        self.folder_tree.setObjectName("folderTree")
        self.folder_tree.setModel(self.folder_model)
        self.folder_tree.setHeaderHidden(True)
        for column_no in range(1, self.folder_model.columnCount()):
            self.folder_tree.hideColumn(column_no)
        self.folder_tree.setAnimated(True)
        self.folder_tree.setUniformRowHeights(True)
        self.folder_tree.clicked.connect(self.on_folder_tree_selected)
        fp.addWidget(self.folder_tree)
        self.folder_placeholder = QLabel("Выберите папку с музыкой —\nвсе треки из неё и подпапок\nпоявятся в списке")
        self.folder_placeholder.setObjectName("folderPlaceholder")
        self.folder_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.folder_placeholder.setWordWrap(True)
        fp.addWidget(self.folder_placeholder)
        body.addWidget(self.folder_panel, 0)
        self.content_layout.addLayout(body)
        self.main_layout.addWidget(self.content_widget)

        # === нижняя панель ===
        self.bottom_bar = QWidget()
        self.bottom_bar.setObjectName("bottomBar")
        self.bottom_bar.setFixedHeight(104)
        bottom = QHBoxLayout(self.bottom_bar)
        bottom.setContentsMargins(20, 12, 14, 12)
        bottom.setSpacing(18)

        info_box = QHBoxLayout()
        info_box.setSpacing(12)
        self.art_thumbnail = QLabel()
        self.art_thumbnail.setObjectName("artThumbnail")
        self.art_thumbnail.setFixedSize(60, 60)
        self.art_thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        text_box = QVBoxLayout()
        text_box.setSpacing(2)
        text_box.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self.now_playing_title = QLabel("Ничего не играет")
        self.now_playing_title.setObjectName("trackTitle")
        self.now_playing_artist = QLabel("Glide Music")
        self.now_playing_artist.setObjectName("trackArtist")
        text_box.addWidget(self.now_playing_title)
        text_box.addWidget(self.now_playing_artist)
        info_box.addWidget(self.art_thumbnail)
        info_box.addLayout(text_box)
        self.info_widget = QWidget()
        self.info_widget.setObjectName("infoWidget")
        self.info_widget.setFixedWidth(270)
        self.info_widget.setLayout(info_box)
        bottom.addWidget(self.info_widget)

        center = QVBoxLayout()
        center.setAlignment(Qt.AlignmentFlag.AlignCenter)
        controls = QHBoxLayout()
        controls.setAlignment(Qt.AlignmentFlag.AlignCenter)
        controls.setSpacing(14)
        controls.addWidget(self._icon_button("btn_shuffle", "SHUFFLE_ICON_SVG", 16, "Перемешать", "modeBtn"))
        controls.addWidget(self._icon_button("btn_prev", "PREV_ICON_SVG", 18, "Предыдущий (Ctrl+←)"))
        controls.addWidget(self._icon_button("btn_play", "PLAY_ICON_SVG", 22, "Играть / пауза (пробел)", "playBtn", self.c["accentText"]))
        controls.addWidget(self._icon_button("btn_next", "NEXT_ICON_SVG", 18, "Следующий (Ctrl+→)"))
        controls.addWidget(self._icon_button("btn_loop", "LOOP_ICON_SVG", 16, "Повтор", "modeBtn"))
        progress = QHBoxLayout()
        self.time_current = QLabel("0:00")
        self.time_current.setObjectName("timeLabel")
        self.progress_slider = QSlider(Qt.Orientation.Horizontal)
        self.progress_slider.setObjectName("progressSlider")
        self.time_total = QLabel("0:00")
        self.time_total.setObjectName("timeLabel")
        progress.addWidget(self.time_current)
        progress.addWidget(self.progress_slider)
        progress.addWidget(self.time_total)
        center.addLayout(controls)
        center.addLayout(progress)
        bottom.addLayout(center)

        right = QHBoxLayout()
        right.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        right.setSpacing(6)
        self.vol_icon = self._icon_button("vol_icon", "VOLUME_ICON_SVG", 16, "Без звука (Ctrl+M)", "smallBtn")
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setObjectName("volumeSlider")
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setFixedWidth(100)
        right.addWidget(self.vol_icon)
        right.addWidget(self.volume_slider)
        right.addSpacing(6)
        right.addWidget(self._icon_button("btn_mini", "MINI_ICON_SVG", 16, "Мини-плеер поверх окон (Ctrl+Shift+M)", "smallBtn"))
        right.addWidget(self._icon_button("btn_settings", "SETTINGS_ICON_SVG", 16, "Настройки (Ctrl+,)", "smallBtn"))
        self.vol_widget = QWidget()
        self.vol_widget.setLayout(right)
        self.vol_widget.setFixedWidth(232)
        bottom.addWidget(self.vol_widget)

        self.size_grip = QSizeGrip(self.bottom_bar)
        self.size_grip.setFixedSize(14, 14)
        bottom.addWidget(self.size_grip, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)
        self.main_layout.addWidget(self.bottom_bar)
        self.set_cover_pixmap(None) if hasattr(self, "mini") else self.art_thumbnail.setPixmap(
            tinted_icon("MUSIC_ICON_SVG", 26, self.c["text2"]).pixmap(26, 26))

    def apply_glide_theme(self):
        c = self.c
        self.setStyleSheet(f"""
        QMainWindow {{ background: {c['bg']}; }}
        QWidget {{ font-family: '{c['font']}'; }}
        QWidget#contentWidget {{ background: transparent; }}
        QToolBar#tool_bar {{ background: transparent; border: none; padding: 6px 10px 0 14px; spacing: 2px; }}
        QToolButton#winBtn, QToolButton#winClose {{ background: transparent; border: none; border-radius: 8px; padding: 7px; }}
        QToolButton#winBtn:hover {{ background: {c['surfaceHigher']}; }}
        QToolButton#winBtn:pressed {{ background: {c['surfacePressed']}; }}
        QToolButton#winClose:hover {{ background: #ef5b5b; }}
        QLabel {{ color: {c['text']}; }}
        QLabel#headerTitle {{ font-size: 22px; font-weight: 800; color: {c['text']}; }}
        QLabel#queueLabel {{ color: {c['accent']}; background: {c['accentSoft']}; border-radius: 10px; padding: 3px 10px; font-size: 12px; }}
        QLabel#trackTitle {{ font-size: 14px; font-weight: 700; color: {c['text']}; }}
        QLabel#trackArtist {{ font-size: 12px; color: {c['text2']}; }}
        QLabel#timeLabel {{ font-size: 11px; color: {c['text2']}; min-width: 34px; }}
        QLabel#artThumbnail {{ background: {c['surfaceHigh']}; border: 1px solid {c['outline']}; border-radius: 14px; }}
        QLabel#emptyState {{ color: {c['text2']}; font-size: 14px; }}
        QLabel#folderHeaderTitle {{ font-size: 13px; font-weight: 700; color: {c['text']}; }}
        QLabel#folderPlaceholder {{ color: {c['text2']}; font-size: 12px; padding-top: 24px; }}

        QLineEdit#searchInput {{ background: {c['surface']}; color: {c['text']}; border: 1px solid {c['outline']};
            border-radius: 14px; padding: 7px 12px; font-size: 13px; selection-background-color: {c['accent']}; }}
        QLineEdit#searchInput:hover {{ border-color: {c['muted']}; }}
        QLineEdit#searchInput:focus {{ border: 2px solid {c['accent']}; padding: 6px 11px; }}

        QPushButton#btnAdd, QToolButton#btnChooseFolder {{ background: {c['surfaceHigh']}; color: {c['text']}; border: none;
            border-radius: 14px; padding: 7px 14px; font-size: 13px; font-weight: 600; }}
        QPushButton#btnAdd:hover, QToolButton#btnChooseFolder:hover {{ background: {c['surfaceHigher']}; }}
        QPushButton#btnAdd:pressed, QToolButton#btnChooseFolder:pressed {{ background: {c['surfacePressed']}; }}
        QPushButton#btnPrimary {{ background: {c['accent']}; color: {c['accentText']}; border: none; border-radius: 14px;
            padding: 7px 16px; font-size: 13px; font-weight: 700; }}
        QPushButton#btnPrimary:hover {{ background: {c['accentLight']}; }}
        QPushButton#btnPrimary:pressed {{ background: {c['accentPressed']}; }}

        QListWidget#modernList {{ background: {c['surface']}; color: {c['text']}; border: 1px solid {c['outline']};
            border-radius: 18px; padding: 6px; font-size: 13px; outline: none; }}
        QListWidget#modernList::item {{ padding: 9px 10px; border-radius: 10px; margin: 1px 0; }}
        QListWidget#modernList::item:hover {{ background: {c['surfaceLow']}; }}
        QListWidget#modernList::item:selected {{ background: {c['accentSoft']}; color: {c['text']}; }}
        QListWidget#modernList::item:selected:hover {{ background: {c['accentLine']}; }}

        QWidget#folderPanel {{ background: {c['surface']}; border: 1px solid {c['outline']}; border-radius: 18px; }}
        QTreeView#folderTree {{ background: transparent; color: {c['text']}; border: none; font-size: 13px; outline: none; }}
        QTreeView#folderTree::item {{ padding: 5px 4px; border-radius: 8px; }}
        QTreeView#folderTree::item:hover {{ background: {c['surfaceLow']}; }}
        QTreeView#folderTree::item:selected {{ background: {c['accentSoft']}; color: {c['text']}; }}

        QWidget#bottomBar {{ background: {c['surface']}; border-top: 1px solid {c['outline']}; }}
        QPushButton#controlBtn, QPushButton#smallBtn, QPushButton#modeBtn {{ background: transparent; border: none;
            border-radius: 18px; min-width: 36px; min-height: 36px; max-width: 36px; max-height: 36px; }}
        QPushButton#controlBtn:hover, QPushButton#smallBtn:hover, QPushButton#modeBtn:hover {{ background: {c['surfaceHigher']}; }}
        QPushButton#controlBtn:pressed, QPushButton#smallBtn:pressed, QPushButton#modeBtn:pressed {{ background: {c['surfacePressed']}; }}
        QPushButton#modeBtn[active="true"], QPushButton#smallBtn[active="true"] {{ background: {c['accentSoft']}; }}
        QPushButton#modeBtn[active="true"]:hover, QPushButton#smallBtn[active="true"]:hover {{ background: {c['accentLine']}; }}
        QPushButton#playBtn {{ background: {c['accent']}; border: none; border-radius: 24px;
            min-width: 48px; min-height: 48px; max-width: 48px; max-height: 48px; }}
        QPushButton#playBtn:hover {{ background: {c['accentLight']}; }}
        QPushButton#playBtn:pressed {{ background: {c['accentPressed']}; }}

        QSlider::groove:horizontal {{ height: 4px; background: {c['surfaceHigher']}; border-radius: 2px; }}
        QSlider::sub-page:horizontal {{ background: {c['accent']}; border-radius: 2px; }}
        QSlider::handle:horizontal {{ background: {c['text']}; width: 12px; height: 12px; margin: -4px 0; border-radius: 6px; }}
        QSlider::handle:horizontal:hover {{ width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}

        QScrollBar:vertical {{ background: transparent; width: 10px; margin: 4px 2px; }}
        QScrollBar::handle:vertical {{ background: {c['surfaceHigher']}; border-radius: 3px; min-height: 30px; }}
        QScrollBar::handle:vertical:hover {{ background: {c['muted']}; }}
        QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
        QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

        QMenu {{ background: {c['surfaceHigh']}; color: {c['text']}; border: 1px solid {c['outline']}; border-radius: 12px; padding: 6px; }}
        QMenu::item {{ padding: 8px 18px; border-radius: 8px; }}
        QMenu::item:selected {{ background: {c['surfaceHigher']}; }}
        QMenu::separator {{ height: 1px; background: {c['outline']}; margin: 4px 8px; }}
        QToolTip {{ background: {c['surfaceHigh']}; color: {c['text']}; border: 1px solid {c['outline']}; border-radius: 8px; padding: 5px 8px; }}
        """)


def utf8_console():
    """Консоль Windows (cp1252/cp866) не печатает кириллицу — сообщения падали бы с UnicodeEncodeError."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass   # в собранном --windowed exe потоков нет вовсе


if __name__ == "__main__":
    utf8_console()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setApplicationName("Glide Music")
    app.setDesktopFileName("glide-music")
    window = GlideMusicModern()
    window.show()
    sys.exit(app.exec())
