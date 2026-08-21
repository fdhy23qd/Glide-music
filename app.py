import sys
from discord_rpc import DiscordRPC
from track_tags import read_track_tags
from artwork_loader import ArtworkLoader
import asyncio
import threading
from pathlib import Path

# MPRIS — это D-Bus, а D-Бus есть только на Linux (freedesktop-стандарт).
# На Windows/macOS модуля/шины попросту нет, поэтому импортируем его
# только там, где он в принципе может работать. Дальше по коду везде,
# где нужен mpris_interface/mpris_bridge, есть проверка на None —
# так что при MPRIS_AVAILABLE = False плеер просто не регистрируется
# в MPRIS и работает как обычно.
MPRIS_AVAILABLE = sys.platform.startswith("linux")
if MPRIS_AVAILABLE:
    try:
        from mpris import start_mpris, PlayerBridge
    except Exception as e:
        print("MPRIS module unavailable:", e)
        MPRIS_AVAILABLE = False
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QPushButton, QSlider, QLabel, QListWidget,
                             QFileDialog, QToolBar, QGraphicsOpacityEffect, QToolButton,
                             QSizePolicy, QLineEdit, QSizeGrip, QAbstractItemView, QMenu)
from PyQt6.QtCore import Qt, QUrl, QSettings, QTimer, QPropertyAnimation, QEvent, QSize, QByteArray
from PyQt6.QtGui import (QFont, QColor, QPainter, QLinearGradient, QBrush, QPen,
                         QTransform, QPixmap, QPainterPath, QShortcut, QKeySequence, QIcon)
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
import random
import os
# ВАЖНО: в присланном файле эти константы (SVG-разметка иконок) нигде не
# определялись и не импортировались — из-за этого падал уже init_toolbar()
# при самом первом запуске (NameError). Замени "icons" на реальное имя
# модуля с этими константами в твоём проекте.

def resource_path(relative_path):
    """Возвращает путь к ресурсу, учитывая распаковку PyInstaller во временную папку"""
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

def svg_icon(svg_xml: str, size=24, color="#F5F5F5") -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer = QSvgRenderer(QByteArray(svg_xml.replace("{color}", color).encode("utf-8")))
    if renderer.isValid():
        renderer.render(painter)
    painter.end()
    return QIcon(pixmap)


def svg_pixmap(svg_xml: str, size=24, color="#F5F5F5") -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer = QSvgRenderer(QByteArray(svg_xml.replace("{color}", color).encode("utf-8")))
    if renderer.isValid():
        renderer.render(painter)
    painter.end()
    return pixmap


def rounded_pixmap(pixmap, radius=8):
    """Возвращает копию pixmap со скруглёнными углами (для обложек треков)."""
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


class GradientLabel(QLabel):
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.color_start = QColor("#F5F5F5")
        self.color_end = QColor("#9CA3AF")
        self.offset = 0

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.animate_gradient)
        self.timer.start(18)

        self.click_count = 0
        self.bubble = None

        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Нажмите 5 раз, чтобы увидеть секрет")

    def animate_gradient(self):
        self.offset += 3
        if self.offset > self.width() + 200:
            self.offset = 0
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
            self.bubble = None

        self.bubble = QLabel("Glide Music любит вас!", main_window)
        self.bubble.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.bubble.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.bubble.setStyleSheet("""
            QLabel {
                background: rgba(250, 250, 250, 0.95);
                color: #111111;
                border: 1px solid rgba(17, 17, 17, 0.12);
                border-radius: 12px;
                padding: 10px 16px;
                font-weight: 700;
                font-size: 12px;
                letter-spacing: 0.3px;
            }
        """)
        self.bubble.adjustSize()

        # bubble — отдельное окно верхнего уровня (флаг Tool), поэтому
        # .move() двигает его в ГЛОБАЛЬНЫХ координатах экрана. Раньше здесь
        # координаты сначала переводились в локальные (mapFromGlobal
        # относительно main_window), а затем передавались в move() как
        # если бы они были глобальными — баблл улетал в угол экрана,
        # то есть визуально пасхалка "не работала".
        label_global = self.mapToGlobal(self.rect().topLeft())
        bubble_x = label_global.x() + self.width() // 2 - self.bubble.width() // 2
        bubble_y = label_global.y() - self.bubble.height() - 10
        self.bubble.move(bubble_x, bubble_y)

        opacity_effect = QGraphicsOpacityEffect(self.bubble)
        self.bubble.setGraphicsEffect(opacity_effect)

        animation = QPropertyAnimation(opacity_effect, b"opacity")
        animation.setDuration(400)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.start()

        self.bubble.raise_()
        self.bubble.show()
        QTimer.singleShot(2600, self.bubble.hide)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
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

    def setText(self, text):
        super().setText(text)
        self.update()


class GlideMusicModern(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Glide Music")
        self.setGeometry(100, 100, 900, 600)
        
        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.player.setAudioOutput(self.audio_output)
        # Bridge создаётся здесь, в GUI-потоке, чтобы Qt-сигналы из него
        # доставлялись в GUI-поток даже когда emit() вызывается из
        # отдельного mpris-потока (см. mpris.py -> PlayerBridge).
        self.mpris_interface = None
        if MPRIS_AVAILABLE:
            self.mpris_bridge = PlayerBridge(self.player)
            self.start_mpris_thread()
        else:
            self.mpris_bridge = None

        # TODO: подставь свой Application ID из discord.com/developers/applications
        self.discord_rpc = DiscordRPC(client_id="1539734149970002093")
        self.discord_rpc.start()
        # Discord не тикает время сам (мы шлём статичный текст "текущее/длина"),
        # поэтому пока трек играет — обновляем его раз в 15 секунд.
        # Чаще не стоит: у локального Rich Presence есть неофициальный
        # рекомендованный лимит частоты обновлений.
        self.discord_update_timer = QTimer(self)
        self.discord_update_timer.setInterval(15000)
        self.discord_update_timer.timeout.connect(self.update_discord_presence)

        self.audio_output.setVolume(1.0)
        self.current_playlist = []
        self.current_track_row = -1
        self._last_volume = 100

        self.artwork_loader = ArtworkLoader()
        self._art_request_id = 0

        self.is_shuffled = False
        self.is_looped = False
        
        self.settings = QSettings("Glide","GlideMusic")

        self.init_ui()
        self.apply_modern_theme()
        self.connect_signals()
        self.init_toolbar()
        self.init_shortcuts()

        self.load_saved_library()

        saved_volume = int(self.settings.value("volume", 100))
        self.volume_slider.setValue(saved_volume)
        self.set_volume(saved_volume)
    def start_mpris_thread(self):

        def run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            try:
                self.mpris_interface = loop.run_until_complete(
                    start_mpris(self.mpris_bridge)
                )
            except Exception as e:
                print("MPRIS start failed:", e)
                return

            loop.run_forever()

        self.mpris_interface = None
        self.mpris_thread = threading.Thread(
            target=run,
            daemon=True
        )

        self.mpris_thread.start()

    def play(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PausedState:
            self.player.play()
            self.set_button_icon(self.btn_play, "icons/PAUSE_ICON_SVG.svg", size=22, color="#111111")
        elif self.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            if self.track_list.count() > 0:
                self.track_list.setCurrentRow(0)
                self.play_selected_track()
        self.update_mpris_playback_status()

    def pause(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.set_button_icon(self.btn_play, "icons/PLAY_ICON_SVG.svg", size=22, color="#111111")
        self.update_mpris_playback_status()

    def stop(self):
        self.player.stop()
        self.set_button_icon(self.btn_play, "icons/PLAY_ICON_SVG.svg", size=22, color="#111111")
        self.update_mpris_playback_status()

    def update_mpris_metadata(self, title, artist, duration=0):
        if self.mpris_interface is None:
            return
        self.mpris_interface.set_track_info(title, artist, duration)
        self.update_mpris_playback_status()

    def update_mpris_playback_status(self):
        state = self.player.playbackState()
        if state == QMediaPlayer.PlaybackState.PlayingState:
            status = "Playing"
        elif state == QMediaPlayer.PlaybackState.PausedState:
            status = "Paused"
        else:
            status = "Stopped"
        # MPRIS обновляем, только если он вообще доступен (Linux/D-Bus).
        # Discord — независимо от этого, он не имеет отношения к MPRIS.
        if self.mpris_interface is not None:
            self.mpris_interface.set_playback_status(status)
        self.update_discord_presence()

    def update_discord_presence(self):
        if not hasattr(self, "discord_rpc"):
            return
        state = self.player.playbackState()
        if state == QMediaPlayer.PlaybackState.StoppedState:
            self.discord_rpc.clear()
            self.discord_update_timer.stop()
            return
        self.discord_rpc.update_track(
            title=self.now_playing_title.text(),
            artist=self.now_playing_artist.text(),
            position_ms=self.player.position(),
            duration_ms=self.player.duration(),
            playing=(state == QMediaPlayer.PlaybackState.PlayingState),
        )
        if state == QMediaPlayer.PlaybackState.PlayingState:
            if not self.discord_update_timer.isActive():
                self.discord_update_timer.start()
        else:
            self.discord_update_timer.stop()

    def init_toolbar(self):
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.toolbar_spacer = spacer
        self.toolbar = QToolBar()
        self.toolbar.setObjectName("tool_bar")
        self.toolbar.setMovable(False)
        self.toolbar.setFixedHeight(45)
        self.addToolBar(self.toolbar)

        self.title_label = GradientLabel("Glide Music")
        self.title_label.setFont(QFont("Segoe UI", 14))
        self.title_label.setMinimumWidth(200)

        self.toolbar.addWidget(self.title_label)
        self.toolbar.addWidget(spacer)
        self.toolbar_spacer.installEventFilter(self)

        self.btn_min = QToolButton()
        self.btn_max = QToolButton()
        self.btn_close = QToolButton()

        # init_toolbar грузил эти три иконки через svg_icon(), которая ждёт
        # СЫРУЮ SVG-разметку строкой, а не путь к файлу — из-за этого
        # QSvgRenderer получал невалидный XML и рисовал пустоту, кнопки
        # выглядели как исчезнувшие. Остальные иконки в проекте уже
        # грузятся как файлы через QIcon(path) — приводим и эти три к тому же.
        self.btn_min.setIcon(QIcon(QPixmap(resource_path("icons/MIN_ICON_SVG.svg")).scaled(
            12, 12, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)))
        self.btn_max.setIcon(QIcon(QPixmap(resource_path("icons/MAX_ICON_SVG.svg")).scaled(
            12, 12, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)))
        self.btn_close.setIcon(QIcon(QPixmap(resource_path("icons/CLOSE_ICON_SVG.svg")).scaled(
            12, 12, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)))

        for btn in [self.btn_min, self.btn_max, self.btn_close]:
            btn.setText("")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet("""
            QToolButton {
                background: transparent;
                color: #e5e7eb;
                border: none;
                padding: 6px;
            }
            QToolButton:hover {
                color: #7B61FF;
            }
        """)
            btn.setIconSize(QSize(12, 12))

        self.btn_min.clicked.connect(self.showMinimized)
        self.btn_max.clicked.connect(self.toggle_maximize)
        self.btn_close.clicked.connect(self.close)

        self.toolbar.addWidget(self.btn_min)
        self.toolbar.addWidget(self.btn_max)
        self.toolbar.addWidget(self.btn_close)

    def set_button_icon(self, button, svg_path, size=18, color="#F5F5F5"):
        button.setText("")

        # 1. Создаем прозрачную базу нужного размера
        base = QPixmap(size, size)
        base.fill(Qt.GlobalColor.transparent)

        # 2. Рендерим SVG из файла с помощью QSvgRenderer, используя resource_path для EXE
        renderer = QSvgRenderer(resource_path(svg_path))
        if renderer.isValid():
            painter = QPainter(base)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            renderer.render(painter)
            painter.end()
        else:
            print(f"Ошибка загрузки SVG: {svg_path}")

        # 3. Перекрашиваем через SourceIn (исправлен вызов QPixmap с размером base)
        tinted = QPixmap(base.size())
        tinted.fill(Qt.GlobalColor.transparent)
        painter = QPainter(tinted)
        painter.drawPixmap(0, 0, base)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(tinted.rect(), QColor(color))
        painter.end()

        button.setIcon(QIcon(tinted))
        button.setIconSize(QSize(size, size))

    def _set_mode_button_active(self, button, active):
        # Единая подсветка для кнопок Shuffle/Loop: подсвечиваем не только
        # иконку, но и саму кнопку (фон + рамка), чтобы включённое
        # состояние было видно даже мельком, а не только по цвету иконки.
        if active:
            button.setStyleSheet("""
                QPushButton#modeBtn {
                    background: rgba(123, 97, 255, 0.18);
                    border: 1px solid rgba(123, 97, 255, 0.55);
                    border-radius: 10px;
                }
            """)
        else:
            button.setStyleSheet("")

    def toggle_shuffle(self):
        self.is_shuffled = not self.is_shuffled
        color = "#7B61FF" if self.is_shuffled else "#A1A1AA"
        self.set_button_icon(self.btn_shuffle, "icons/SHUFFLE_ICON_SVG.svg", size=16, color=color)
        self._set_mode_button_active(self.btn_shuffle, self.is_shuffled)
        self.btn_shuffle.setToolTip("Перемешать: включено" if self.is_shuffled else "Перемешать (Shuffle)")

    def toggle_loop(self):
        if not self.is_looped:
            self.is_looped = 'one'
            self.set_button_icon(self.btn_loop, "icons/LOOP_ONE_ICON_SVG.svg", size=18, color="#7B61FF")
            self.btn_loop.setToolTip("Повтор: один трек")
            self._set_mode_button_active(self.btn_loop, True)
        elif self.is_looped == 'one':
            self.is_looped = 'all'
            self.set_button_icon(self.btn_loop, "icons/LOOP_ICON_SVG.svg", size=18, color="#7B61FF")
            self.btn_loop.setToolTip("Повтор: весь плейлист")
            self._set_mode_button_active(self.btn_loop, True)
        else:
            self.is_looped = False
            self.set_button_icon(self.btn_loop, "icons/LOOP_ICON_SVG.svg", size=18, color="#a1a1aa")
            self.btn_loop.setToolTip("Повтор: выключен")
            self._set_mode_button_active(self.btn_loop, False)

    def handle_media_status(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            if self.is_looped == 'one':
                self.player.play()
            elif self.is_looped == 'all' or self.track_list.currentRow() < self.track_list.count() - 1:
                self.next_track()
            else:
                self.set_button_icon(self.btn_play, "icons/PLAY_ICON_SVG.svg", size=22, color="#111111")
                self.update_mpris_playback_status()

    def next_track(self):
        count = self.track_list.count()
        if count <= 0: return

        if self.is_shuffled:
            next_row = random.randint(0, count - 1)
        else:
            next_row = (self.track_list.currentRow() + 1) % count
        
        self.track_list.setCurrentRow(next_row)
        self.play_selected_track()

    def connect_signals(self):
        self.btn_shuffle.clicked.connect(self.toggle_shuffle)
        self.btn_loop.clicked.connect(self.toggle_loop)
        self.player.mediaStatusChanged.connect(self.handle_media_status)
        self.player.playbackStateChanged.connect(self.update_mpris_playback_status)
        self.btn_add.clicked.connect(self.add_tracks)
        self.btn_play.clicked.connect(self.toggle_playback)
        self.btn_next.clicked.connect(self.next_track)
        self.btn_prev.clicked.connect(self.prev_track)
        self.track_list.itemDoubleClicked.connect(self.play_selected_track)
        self.volume_slider.valueChanged.connect(self.set_volume)
        self.vol_icon.clicked.connect(self.toggle_mute)
        self.progress_slider.sliderMoved.connect(self.set_position)
        self.player.positionChanged.connect(self.update_progress)
        self.player.durationChanged.connect(self.update_duration)
        self.search_input.textChanged.connect(self.filter_tracks)
        self.artwork_loader.artwork_ready.connect(self.on_artwork_ready)

    def add_tracks(self):
        # Достаем последний путь для удобства открытия диалога
        last_path = self.settings.value("last_folder", "")
        
        files, _ = QFileDialog.getOpenFileNames(
            self, "Выберите аудиофайлы", last_path, "Audio Files (*.mp3 *.wav *.ogg *.m4a)"
        )
        
        if files:
            # Сохраняем путь к папке первого выбранного файла
            folder_path = str(Path(files[0]).parent)
            self.settings.setValue("last_folder", folder_path)
            
            for file in files:
                if file not in self.current_playlist:
                    self.current_playlist.append(file)
                    self.track_list.addItem(Path(file).name)

            # Раньше плейлист нигде не сохранялся при добавлении треков —
            # load_saved_library() при следующем запуске находил пустой
            # список, и все добавленные треки терялись после закрытия.
            self.settings.setValue("library", self.current_playlist)
            self.update_library_header()

    def play_selected_track(self):
        selected_row = self.track_list.currentRow()
        if selected_row >= 0:
            file_path = self.current_playlist[selected_row]
            self.player.setSource(QUrl.fromLocalFile(file_path))
            self.player.play()
            self.set_button_icon(self.btn_play, "icons/PAUSE_ICON_SVG.svg", size=22, color="#111111")

            title_text, artist_text = read_track_tags(file_path)
            self.now_playing_title.setText(title_text)
            self.now_playing_artist.setText(artist_text)
            self.update_mpris_metadata(title_text, artist_text, self.player.duration())
            self.update_mpris_playback_status()

            self.current_track_row = selected_row
            self.mark_current_track(selected_row)
            self.request_artwork(title_text, artist_text)

    def toggle_playback(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.set_button_icon(self.btn_play, "icons/PLAY_ICON_SVG.svg", size=22, color="#111111")
        elif self.player.playbackState() == QMediaPlayer.PlaybackState.PausedState:
            self.player.play()
            self.set_button_icon(self.btn_play, "icons/PAUSE_ICON_SVG.svg", size=22, color="#111111")
        else:
            if self.track_list.count() > 0:
                self.track_list.setCurrentRow(0)
                self.play_selected_track()
        self.update_mpris_playback_status()

    def prev_track(self):
        current_row = self.track_list.currentRow()
        if current_row > 0:
            self.track_list.setCurrentRow(current_row - 1)
            self.play_selected_track()

    def set_volume(self, value):
        self.audio_output.setVolume(value / 100)
        self.settings.setValue("volume", value)
        # Раньше здесь были три ветки (value<40 / <75 / else) с абсолютно
        # одинаковым результатом — мёртвый код. Оставлены только два
        # реальных состояния: без звука / со звуком.
        icon = "icons/MUTE_ICON_SVG.svg" if value == 0 else "icons/VOLUME_ICON_SVG.svg"
        self.set_button_icon(self.vol_icon, icon, size=16, color="#f5f5f5")

    def toggle_mute(self):
        if self.volume_slider.value() > 0:
            self._last_volume = self.volume_slider.value()
            self.volume_slider.setValue(0)
        else:
            self.volume_slider.setValue(self._last_volume or 70)

    def set_position(self, position):
        self.player.setPosition(position)

    def update_progress(self, position):
        if not self.progress_slider.isSliderDown():
            self.progress_slider.setValue(position)
        self.time_current.setText(self.format_time(position))
        if self.mpris_interface is not None:
            self.mpris_interface.set_position(position)

    def update_duration(self, duration):
        self.progress_slider.setRange(0, duration)
        self.time_total.setText(self.format_time(duration))
        if self.mpris_interface is not None:
            self.mpris_interface.set_track_info(
                self.now_playing_title.text(),
                self.now_playing_artist.text(),
                duration
            )
        self.update_discord_presence()

    def format_time(self, ms):
        # Раньше minutes = (ms // 60000) % 60 — для треков длиннее часа
        # (например, длинных миксов/подкастов) минуты зацикливались через
        # 59 и час "исчезал". Теперь при длительности от часа показываем ч:мм:сс.
        total_seconds = max(ms, 0) // 1000
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours}:{minutes:02}:{seconds:02}"
        return f"{minutes}:{seconds:02}"

    def mark_current_track(self, row):
        accent = QColor("#F5F5F5")
        default_color = QColor("#E5E7EB")
        for i in range(self.track_list.count()):
            item = self.track_list.item(i)
            font = item.font()
            is_current = (i == row)
            font.setBold(is_current)
            item.setFont(font)
            item.setForeground(accent if is_current else default_color)
            has_marker = item.text().startswith("• ")
            if is_current and not has_marker:
                item.setText(f"• {item.text()}")
            elif not is_current and has_marker:
                item.setText(item.text()[2:])

    def init_shortcuts(self):
        # Play/Pause на пробел — только пока фокус на списке треков,
        # чтобы не мешать вводу текста в поле поиска
        play_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Space), self.track_list)
        play_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        play_shortcut.activated.connect(self.toggle_playback)

        QShortcut(QKeySequence("Ctrl+Right"), self, activated=self.next_track)
        QShortcut(QKeySequence("Ctrl+Left"), self, activated=self.prev_track)
        QShortcut(QKeySequence("Ctrl+M"), self, activated=self.toggle_mute)
        QShortcut(QKeySequence("Ctrl+F"), self, activated=lambda: self.search_input.setFocus())

        delete_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Delete), self.track_list)
        delete_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        delete_shortcut.activated.connect(self.delete_selected_tracks)

    def init_ui(self):
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        
        # Главный слой без отступов, чтобы нижняя панель прилегала к краям
        self.main_layout = QVBoxLayout(self.central_widget)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        # === ВЕРХНЯЯ ЧАСТЬ: КОНТЕНТ ===
        self.content_widget = QWidget()
        self.content_widget.setObjectName("contentWidget")
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(26, 26, 26, 18)
        self.content_layout.setSpacing(16)

        self.header_layout = QHBoxLayout()
        self.header_layout.setSpacing(12)
        self.header_label = QLabel("Library")
        self.header_label.setObjectName("headerTitle")

        self.search_input = QLineEdit()
        self.search_input.setObjectName("searchInput")
        self.search_input.setPlaceholderText("Поиск по названию... (Ctrl+F)")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setFixedWidth(230)

        self.btn_add = QPushButton("Add Tracks")
        self.btn_add.setObjectName("btnAdd")
        self.btn_add.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_add.setFixedWidth(126)
        self.btn_add.setToolTip("Добавить аудиофайлы в библиотеку")
        self.btn_add.setIcon(QIcon(QPixmap(resource_path("icons/ADD_ICON_SVG.svg")).scaled(
            14, 14, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)))
        self.btn_add.setIconSize(QSize(14, 14))

        self.header_layout.addWidget(self.header_label)
        self.header_layout.addStretch()
        self.header_layout.addWidget(self.search_input)
        self.header_layout.addWidget(self.btn_add)
        self.content_layout.addLayout(self.header_layout)

        self.track_list = QListWidget()
        self.track_list.setObjectName("modernList")
        self.track_list.setAlternatingRowColors(False)
        self.track_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.track_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.track_list.customContextMenuRequested.connect(self.show_track_context_menu)
        self.content_layout.addWidget(self.track_list)

        self.empty_state = QLabel("Библиотека пуста\nНажми «Add Tracks», чтобы добавить музыку")
        self.empty_state.setObjectName("emptyState")
        self.empty_state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_state.setVisible(False)
        self.content_layout.addWidget(self.empty_state)

        self.main_layout.addWidget(self.content_widget)

        # === НИЖНЯЯ ПАНЕЛЬ: УПРАВЛЕНИЕ ===
        self.bottom_bar = QWidget()
        self.bottom_bar.setObjectName("bottomBar")
        self.bottom_bar.setFixedHeight(110)
        self.bottom_layout = QHBoxLayout(self.bottom_bar)
        self.bottom_layout.setContentsMargins(20, 12, 20, 14)
        self.bottom_layout.setSpacing(18)

        # Левая секция: Обложка + инфо о треке
        self.info_layout = QHBoxLayout()
        self.info_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self.info_layout.setSpacing(12)

        self.art_thumbnail = QLabel()
        self.art_thumbnail.setObjectName("artThumbnail")
        self.art_thumbnail.setFixedSize(58, 58)
        self.art_thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.art_thumbnail.setPixmap(QPixmap(resource_path("icons/MUSIC_ICON_SVG.svg")).scaled(
                28, 28, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            ))

        self.info_text_layout = QVBoxLayout()
        self.info_text_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self.now_playing_title = QLabel("Not Playing")
        self.now_playing_title.setObjectName("trackTitle")
        self.now_playing_artist = QLabel("Glide Ecosystem")
        self.now_playing_artist.setObjectName("trackArtist")

        self.info_text_layout.addWidget(self.now_playing_title)
        self.info_text_layout.addWidget(self.now_playing_artist)

        self.info_layout.addWidget(self.art_thumbnail)
        self.info_layout.addLayout(self.info_text_layout)

        self.info_widget = QWidget()
        self.info_widget.setObjectName("infoWidget")
        self.info_widget.setFixedWidth(260)
        self.info_widget.setLayout(self.info_layout)
        self.bottom_layout.addWidget(self.info_widget)

        # Центральная секция: Кнопки и прогресс
        self.center_layout = QVBoxLayout()
        self.center_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.controls_layout = QHBoxLayout()
        self.controls_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.controls_layout.setSpacing(18)

                # Кнопки режимов
        self.btn_shuffle = QPushButton()
        self.btn_shuffle.setObjectName("modeBtn")
        self.set_button_icon(self.btn_shuffle, "icons/SHUFFLE_ICON_SVG.svg", size=16, color="#A1A1AA")
        self.btn_shuffle.setToolTip("Перемешать (Shuffle)")
        self.btn_loop = QPushButton()
        self.btn_loop.setObjectName("modeBtn")
        self.set_button_icon(self.btn_loop, "icons/LOOP_ICON_SVG.svg", size=16, color="#A1A1AA")
        self.btn_loop.setToolTip("Повтор: выключен")
        
        self.controls_layout.insertWidget(0, self.btn_shuffle)
        self.controls_layout.addWidget(self.btn_loop)

        # Настройка курсора для всех новых кнопок
        self.btn_shuffle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_loop.setCursor(Qt.CursorShape.PointingHandCursor)
        
        self.btn_prev = QPushButton()
        self.btn_play = QPushButton()
        self.btn_next = QPushButton()
        self.set_button_icon(self.btn_prev, "icons/PREV_ICON_SVG.svg", size=18, color="#F5F5F5")
        self.set_button_icon(self.btn_play, "icons/PLAY_ICON_SVG.svg", size=22, color="#111111")
        self.set_button_icon(self.btn_next, "icons/NEXT_ICON_SVG.svg", size=18, color="#F5F5F5")

        self.btn_prev.setToolTip("Предыдущий трек (Ctrl+←)")
        self.btn_play.setToolTip("Играть / Пауза (Space)")
        self.btn_next.setToolTip("Следующий трек (Ctrl+→)")
        
        for btn in [self.btn_prev, self.btn_play, self.btn_next]:
            btn.setObjectName("controlBtn")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedSize(40, 40)
            self.controls_layout.addWidget(btn)
            
        self.btn_play.setFixedSize(52, 52)
        self.btn_play.setObjectName("playBtn")

        # Прогресс бар
        self.progress_layout = QHBoxLayout()
        self.time_current = QLabel("0:00")
        self.time_current.setObjectName("timeLabel")
        self.progress_slider = QSlider(Qt.Orientation.Horizontal)
        self.progress_slider.setObjectName("progressSlider")
        self.time_total = QLabel("0:00")
        self.time_total.setObjectName("timeLabel")
        
        self.progress_layout.addWidget(self.time_current)
        self.progress_layout.addWidget(self.progress_slider)
        self.progress_layout.addWidget(self.time_total)

        self.center_layout.addLayout(self.controls_layout)
        self.center_layout.addLayout(self.progress_layout)
        self.bottom_layout.addLayout(self.center_layout)

        # Правая секция: Громкость
        self.volume_layout = QHBoxLayout()
        self.volume_layout.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.vol_icon = QPushButton()
        self.vol_icon.setObjectName("volIcon")
        self.vol_icon.setFlat(True)
        self.vol_icon.setCursor(Qt.CursorShape.PointingHandCursor)
        self.set_button_icon(self.vol_icon, "icons/VOLUME_ICON_SVG.svg", size=16, color="#F5F5F5")
        self.vol_icon.setFixedWidth(26)
        self.vol_icon.setToolTip("Выключить/включить звук (Ctrl+M)")
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setObjectName("volumeSlider")
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(100)
        self.volume_slider.setFixedWidth(110)
        
        self.volume_layout.addWidget(self.vol_icon)
        self.volume_layout.addWidget(self.volume_slider)

        self.vol_widget = QWidget()
        self.vol_widget.setObjectName("volWidget")
        self.vol_widget.setFixedWidth(170)
        self.vol_widget.setLayout(self.volume_layout)
        self.bottom_layout.addWidget(self.vol_widget)

        self.size_grip = QSizeGrip(self.bottom_bar)
        self.size_grip.setFixedSize(16, 16)
        self.bottom_layout.addWidget(
            self.size_grip, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom
        )

        self.main_layout.addWidget(self.bottom_bar)

    def apply_modern_theme(self):
        qss = """
        QMainWindow {
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 #0a0a0a, stop:0.5 #121212, stop:1 #0a0a0a);
        }

        QWidget#contentWidget {
            background: transparent;
        }

        QToolBar#tool_bar {
            background: transparent;
            border: none;
            padding: 8px 12px 0 12px;
        }

        QPushButton#modeBtn {
            background: rgba(255, 255, 255, 0.03);
            color: #a1a1aa;
            border: 1px solid rgba(255, 255, 255, 0.04);
            border-radius: 10px;
            min-width: 30px;
            min-height: 30px;
            font-size: 16px;
        }
        QPushButton#modeBtn:hover {
            color: #7B61FF;
            background: rgba(255, 255, 255, 0.06);
            border-color: rgba(255, 255, 255, 0.12);
        }

        QLabel {
            color: #f5f5f5;
            font-family: 'Segoe UI', sans-serif;
        }
        QLabel#headerTitle {
            font-size: 27px;
            font-weight: 800;
            color: #7B61FF;
            letter-spacing: 0.02em;
        }
        QLabel#trackTitle {
            font-size: 15px;
            font-weight: 700;
            color: #7B61FF;
        }
        QLabel#trackArtist {
            font-size: 12px;
            color: #a1a1aa;
        }
        QLabel#timeLabel {
            font-size: 11px;
            color: #a1a1aa;
        }

        QLabel#artThumbnail {
            background: rgba(255, 255, 255, 0.04);
            border: 1px solid rgba(255, 255, 255, 0.12);
            border-radius: 12px;
            box-shadow: 0 8px 20px rgba(0, 0, 0, 0.18);
        }

        QLineEdit#searchInput {
            background: rgba(255, 255, 255, 0.04);
            color: #f5f5f5;
            border: 1px solid rgba(255, 255, 255, 0.09);
            border-radius: 10px;
            padding: 8px 12px;
            font-size: 13px;
            selection-background-color: rgba(255, 255, 255, 0.16);
        }
        QLineEdit#searchInput:focus {
            border-color: rgba(255, 255, 255, 0.22);
            box-shadow: 0 0 0 3px rgba(255, 255, 255, 0.06);
        }

        QLabel#emptyState {
            color: #a1a1aa;
            font-size: 14px;
            padding-top: 50px;
        }

        QListWidget#modernList {
            background: rgba(255, 255, 255, 0.02);
            color: #f5f5f5;
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 16px;
            padding: 8px;
            font-size: 14px;
            outline: none;
        }
        QListWidget#modernList::item {
            padding: 11px 12px;
            border-radius: 10px;
            margin-bottom: 4px;
            color: #f5f5f5;
        }
        QListWidget#modernList::item:hover {
            background: rgba(255, 255, 255, 0.04);
        }
        QListWidget#modernList::item:selected {
            background: rgba(255, 255, 255, 0.08);
            color: #7B61FF;
            font-weight: 700;
            border: 1px solid rgba(255, 255, 255, 0.12);
        }

        QPushButton#btnAdd {
            background: rgba(255, 255, 255, 0.04);
            color: #f5f5f5;
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 10px;
            padding: 8px 12px;
            font-size: 13px;
            font-weight: 700;
        }
        QPushButton#btnAdd:hover {
            background: rgba(255, 255, 255, 0.08);
            border-color: rgba(255, 255, 255, 0.18);
        }

        QWidget#bottomBar {
            background: rgba(17, 17, 17, 0.96);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 18px 18px 0 0;
            box-shadow: 0 -12px 28px rgba(0, 0, 0, 0.25);
        }

        QWidget#infoWidget, QWidget#volWidget {
            background: transparent;
        }

        QPushButton#controlBtn {
            background: rgba(255, 255, 255, 0.03);
            color: #d4d4d4;
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 12px;
            font-size: 18px;
        }
        QPushButton#controlBtn:hover {
            color: #7B61FF;
            background: rgba(255, 255, 255, 0.06);
        }
        QPushButton#playBtn {
            background: #f5f5f5;
            color: #111111;
            border: none;
            border-radius: 16px;
            font-size: 20px;
            font-weight: 800;
            box-shadow: 0 10px 20px rgba(255, 255, 255, 0.08);
        }
        QPushButton#playBtn:hover {
            background: #7B61FF;
        }

        QPushButton#volIcon {
            background: transparent;
            border: none;
            font-size: 15px;
            color: #d4d4d4;
        }

        QSlider::groove:horizontal {
            height: 5px;
            background: rgba(255, 255, 255, 0.12);
            border-radius: 3px;
        }
        QSlider::sub-page:horizontal {
            background: rgba(255, 255, 255, 0.9);
            border-radius: 3px;
        }
        QSlider::handle:horizontal {
            background: #7B61FF;
            width: 12px;
            height: 12px;
            margin: -4px 0;
            border-radius: 6px;
            border: 2px solid rgba(255, 255, 255, 0.3);
        }
        QSlider::handle:horizontal:hover {
            background: #7B61FF;
            border-color: rgba(255, 255, 255, 0.8);
        }

        QScrollBar:vertical {
            border: none;
            background: transparent;
            width: 9px;
            margin: 6px 0 6px 0;
        }
        QScrollBar::handle:vertical {
            background: rgba(255, 255, 255, 0.18);
            min-height: 20px;
            border-radius: 5px;
        }
        QScrollBar::handle:vertical:hover {
            background: rgba(255, 255, 255, 0.32);
        }

        QToolTip {
            background: rgba(17, 17, 17, 0.95);
            color: #f5f5f5;
            border: 1px solid rgba(255, 255, 255, 0.12);
            border-radius: 8px;
            padding: 5px 8px;
        }
        """
        self.setStyleSheet(qss)

    # --- Ранее вызывались, но нигде не были определены (AttributeError при первом же запуске) ---

    def load_saved_library(self):
        saved = self.settings.value("library", [])
        if isinstance(saved, str):
            saved = [saved] if saved else []
        for file_path in saved or []:
            if file_path and Path(file_path).exists() and file_path not in self.current_playlist:
                self.current_playlist.append(file_path)
                self.track_list.addItem(Path(file_path).name)
        self.update_library_header()

    def update_library_header(self):
        has_tracks = self.track_list.count() > 0
        self.track_list.setVisible(has_tracks)
        self.empty_state.setVisible(not has_tracks)
        self.header_label.setText(f"Library ({len(self.current_playlist)})" if has_tracks else "Library")

    def filter_tracks(self, text):
        needle = text.strip().lower()
        for i in range(self.track_list.count()):
            item = self.track_list.item(i)
            item.setHidden(bool(needle) and needle not in item.text().lower())

    def delete_selected_tracks(self):
        rows = sorted({index.row() for index in self.track_list.selectedIndexes()}, reverse=True)
        if not rows:
            return

        playing_path = None
        if 0 <= self.current_track_row < len(self.current_playlist):
            playing_path = self.current_playlist[self.current_track_row]

        for row in rows:
            self.track_list.takeItem(row)
            del self.current_playlist[row]

        if playing_path in self.current_playlist:
            self.current_track_row = self.current_playlist.index(playing_path)
        else:
            self.current_track_row = -1
            self.player.stop()
            self.now_playing_title.setText("Not Playing")
            self.now_playing_artist.setText("Glide Ecosystem")
            self.art_thumbnail.setPixmap(QPixmap(resource_path("icons/MUSIC_ICON_SVG.svg")).scaled(
                28, 28, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            ))

        self.settings.setValue("library", self.current_playlist)
        self.update_library_header()

    def show_track_context_menu(self, position):
        if not self.track_list.selectedItems():
            return
        menu = QMenu(self)
        remove_action = menu.addAction("Удалить из библиотеки")
        chosen = menu.exec(self.track_list.mapToGlobal(position))
        if chosen == remove_action:
            self.delete_selected_tracks()

    def toggle_maximize(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def request_artwork(self, title, artist):
        # NOTE: artwork_loader.py не входил в присланный файл — сигнатура
        # вызова здесь предполагаемая. Свериться с реальным ArtworkLoader.
        self._art_request_id += 1
        self.artwork_loader.request(title, artist)

    def on_artwork_ready(self, data):
        # artwork_loader.artwork_ready отдаёт bytes (сырые данные картинки),
        # а не QPixmap — раньше код сразу звал pixmap.isNull() на bytes,
        # отсюда AttributeError и падение.
        pixmap = QPixmap()
        if not data or not pixmap.loadFromData(data):
            self.art_thumbnail.setPixmap(QPixmap(resource_path("icons/MUSIC_ICON_SVG.svg")).scaled(
                28, 28, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            ))
            return
        scaled = pixmap.scaled(
            self.art_thumbnail.size(),
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.art_thumbnail.setPixmap(rounded_pixmap(scaled, radius=10))

    def eventFilter(self, obj, event):
        # Перетаскивание окна за пустое место тулбара — нужно, т.к. окно
        # безрамочное (FramelessWindowHint) и штатной area для drag нет.
        if obj is self.toolbar_spacer:
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                return True
            if event.type() == QEvent.Type.MouseMove and (event.buttons() & Qt.MouseButton.LeftButton) and hasattr(self, "_drag_pos"):
                self.move(event.globalPosition().toPoint() - self._drag_pos)
                return True
        return super().eventFilter(obj, event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = GlideMusicModern()
    window.show()
    sys.exit(app.exec())
