import sys
from discord_rpc import DiscordRPC
from track_tags import read_track_tags
from artwork_loader import ArtworkLoader
import asyncio
import threading
from pathlib import Path

# MPRIS — это D-Bus, а D-Bus есть только на Linux (freedesktop-стандарт).
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
                             QSizePolicy, QLineEdit, QSizeGrip)
from PyQt6.QtCore import Qt, QUrl, QSettings, QTimer, QPropertyAnimation, QEvent
from PyQt6.QtGui import (QFont, QColor, QPainter, QLinearGradient, QBrush, QPen,
                         QTransform, QPixmap, QPainterPath, QShortcut, QKeySequence)
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
import random


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
        # [span_0](start_span)Настройки градиента[span_0](end_span)
        self.color_start = QColor("#8A2BE2")
        self.color_end = QColor("#007BFF")
        self.offset = 0
        
        # [span_1](start_span)Таймер анимации градиента[span_1](end_span)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.animate_gradient)
        self.timer.start(20)

        # --- Логика пасхалки ---
        self.click_count = 0 
        self.bubble = None 
        # -----------------------

        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def animate_gradient(self):
        self.offset += 4 # Увеличиваем смещение
        # Сброс для бесконечного цикла
        if self.offset > self.width():
            self.offset = 0
        self.update()

    def mousePressEvent(self, event):
        # Считаем клики
        self.click_count += 1
        
        # Если нажали 5 раз (или больше)
        if self.click_count >= 5:
            self.show_easter_egg()
            self.click_count = 0 # Сбрасываем счетчик
        
        super().mousePressEvent(event)

    def show_easter_egg(self):
        main_window =self.window()
        # Если облачко уже есть, удаляем старое
        if self.bubble:
            self.bubble.deleteLater()

        # Создаем "облачко"
        self.bubble = QLabel("(⁠~⁠￣⁠³⁠￣⁠)⁠~ Glide Music любит вас! (⁠ʃ⁠ƪ⁠＾⁠3⁠＾⁠）", main_window)
        self.bubble.setStyleSheet("""
            QLabel {
                background-color: white;
                color: #8A2BE2;
                border: 2px solid #007BFF;
                border-radius: 15px;
                padding: 10px;
                font-weight: bold;
            }
        """)
        self.bubble.adjustSize()
        
        # Позиционируем над текстом
        self.bubble.move(self.width() // 2 - self.bubble.width() // 2 + 130, 35)
        
        # Эффект прозрачности для анимации
        opacity_effect = QGraphicsOpacityEffect(self.bubble)
        self.bubble.setGraphicsEffect(opacity_effect)
        
        # Анимация появления
        self.anim = QPropertyAnimation(opacity_effect, b"opacity")
        self.anim.setDuration(500)
        self.anim.setStartValue(0)
        self.anim.setEndValue(1)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        
        # Таймер, чтобы облачко исчезло через 3 секунды
        QTimer.singleShot(3000, self.bubble.hide)
        
        self.bubble.show()
        self.anim.start()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        
        rect = self.rect()
        
        # Градиент
        gradient = QLinearGradient(0, 0, rect.width(), 0)
        gradient.setSpread(QLinearGradient.Spread.RepeatSpread) 
        gradient.setColorAt(0.0, self.color_start)
        gradient.setColorAt(0.5, self.color_end)
        gradient.setColorAt(1.0, self.color_start)

        # Применяем трансформацию К КИСТИ
        brush = QBrush(gradient)
        transform = QTransform()
        transform.translate(-self.offset, 0) # Смещение влево дает движение вправо
        brush.setTransform(transform)

        # Настраиваем перо
        pen = QPen()
        pen.setBrush(brush)
        painter.setPen(pen)

        # Отрисовка текста
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.text())

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

        self.audio_output.setVolume(0.7)
        self.current_playlist = []
        self.current_track_row = -1
        self._last_volume = 70

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

        saved_volume = int(self.settings.value("volume", 70))
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
            self.btn_play.setText("⏸")
        elif self.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            if self.track_list.count() > 0:
                self.track_list.setCurrentRow(0)
                self.play_selected_track()
        self.update_mpris_playback_status()

    def pause(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.btn_play.setText("▶")
        self.update_mpris_playback_status()

    def stop(self):
        self.player.stop()
        self.btn_play.setText("▶")
        self.update_mpris_playback_status()

    def update_mpris_metadata(self, title, artist, duration=0):
        if self.mpris_interface is None:
            return
        self.mpris_interface.set_track_info(title, artist, duration)
        self.update_mpris_playback_status()

    def update_mpris_playback_status(self):
        if self.mpris_interface is None:
            return
        state = self.player.playbackState()
        if state == QMediaPlayer.PlaybackState.PlayingState:
            status = "Playing"
        elif state == QMediaPlayer.PlaybackState.PausedState:
            status = "Paused"
        else:
            status = "Stopped"
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
        self.toolbar_spacer = spacer  # пустая зона тулбара — за неё окно можно таскать
        self.toolbar = QToolBar()
        self.toolbar.setObjectName("tool_bar")
        self.toolbar.setMovable(False)
        self.toolbar.setFixedHeight(45)
        self.addToolBar(self.toolbar)

    # Левый заголовок
        self.title_label = GradientLabel("Glide Music")
        self.title_label.setFont(QFont("Segoe UI", 14))
        self.title_label.setMinimumWidth(200)

        self.toolbar.addWidget(self.title_label)
        self.toolbar.addWidget(spacer)
        # Окно безрамочное (FramelessWindowHint) — штатной "полоски заголовка"
        # для перетаскивания у него нет, поэтому пустую часть тулбара
        # делаем перетаскиваемой вручную через eventFilter ниже.
        self.toolbar_spacer.installEventFilter(self)

    # === КНОПКИ ОКНА ===
        self.btn_min = QToolButton()
        self.btn_max = QToolButton()
        self.btn_close = QToolButton()

        self.btn_min.setText("-")
        self.btn_max.setText("□")
        self.btn_close.setText("x")

        for btn in [self.btn_min, self.btn_max, self.btn_close]:
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet("""
            QToolButton {
                background: transparent;
                color: #a1a1aa;
                border: none;
                font-size: 14px;
                padding: 6px;
            }
            QToolButton:hover {
                color: #ffffff;
            }
        """)

    # действия
        self.btn_min.clicked.connect(self.showMinimized)
        self.btn_max.clicked.connect(self.toggle_maximize)
        self.btn_close.clicked.connect(self.close)

        self.toolbar.addWidget(self.btn_min)
        self.toolbar.addWidget(self.btn_max)
        self.toolbar.addWidget(self.btn_close)

    def toggle_maximize(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def eventFilter(self, obj, event):
        # Перетаскивание безрамочного окна за пустую зону тулбара +
        # даблклик по ней — стандартное поведение "разворачивать/восстанавливать"
        if obj is getattr(self, "toolbar_spacer", None):
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                handle = self.windowHandle()
                if handle is not None:
                    handle.startSystemMove()
                return True
            if event.type() == QEvent.Type.MouseButtonDblClick:
                self.toggle_maximize()
                return True
        return super().eventFilter(obj, event)

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
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(30, 30, 30, 20)
        self.content_layout.setSpacing(15)

        # Заголовок с поиском и кнопкой добавления
        self.header_layout = QHBoxLayout()
        self.header_layout.setSpacing(12)
        self.header_label = QLabel("Library")
        self.header_label.setObjectName("headerTitle")

        self.search_input = QLineEdit()
        self.search_input.setObjectName("searchInput")
        self.search_input.setPlaceholderText("Поиск по названию... (Ctrl+F)")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setFixedWidth(220)

        self.btn_add = QPushButton("+ Add Tracks")
        self.btn_add.setObjectName("btnAdd")
        self.btn_add.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_add.setFixedWidth(120)
        self.btn_add.setToolTip("Добавить аудиофайлы в библиотеку")

        self.header_layout.addWidget(self.header_label)
        self.header_layout.addStretch()
        self.header_layout.addWidget(self.search_input)
        self.header_layout.addWidget(self.btn_add)
        self.content_layout.addLayout(self.header_layout)

        # Список треков
        self.track_list = QListWidget()
        self.track_list.setObjectName("modernList")
        self.content_layout.addWidget(self.track_list)

        # Плейсхолдер для пустой библиотеки (показывается вместо списка)
        self.empty_state = QLabel("🎵\nБиблиотека пуста\nНажми «+ Add Tracks», чтобы добавить музыку")
        self.empty_state.setObjectName("emptyState")
        self.empty_state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_state.setVisible(False)
        self.content_layout.addWidget(self.empty_state)

        self.main_layout.addWidget(self.content_widget)

        # === НИЖНЯЯ ПАНЕЛЬ: УПРАВЛЕНИЕ ===
        self.bottom_bar = QWidget()
        self.bottom_bar.setObjectName("bottomBar")
        self.bottom_bar.setFixedHeight(100)
        self.bottom_layout = QHBoxLayout(self.bottom_bar)
        self.bottom_layout.setContentsMargins(30, 10, 30, 15)

        # Левая секция: Обложка + инфо о треке
        self.info_layout = QHBoxLayout()
        self.info_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self.info_layout.setSpacing(12)

        self.art_thumbnail = QLabel("🎵")
        self.art_thumbnail.setObjectName("artThumbnail")
        self.art_thumbnail.setFixedSize(56, 56)
        self.art_thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)

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
        self.info_widget.setFixedWidth(240)
        self.info_widget.setLayout(self.info_layout)
        self.bottom_layout.addWidget(self.info_widget)

        # Центральная секция: Кнопки и прогресс
        self.center_layout = QVBoxLayout()
        self.center_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.controls_layout = QHBoxLayout()
        self.controls_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.controls_layout.setSpacing(20)

                # Кнопки режимов
        self.btn_shuffle = QPushButton("⇄")
        self.btn_shuffle.setObjectName("modeBtn")
        self.btn_shuffle.setToolTip("Перемешать (Shuffle)")
        self.btn_loop = QPushButton("↻")
        self.btn_loop.setObjectName("modeBtn")
        self.btn_loop.setToolTip("Повтор: выключен")
        
        # Добавляем их в controls_layout (последовательность: Shuffle, Prev, Play, Next, Loop)
        self.controls_layout.insertWidget(0, self.btn_shuffle)
        self.controls_layout.addWidget(self.btn_loop)

        # Настройка курсора для всех новых кнопок
        self.btn_shuffle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_loop.setCursor(Qt.CursorShape.PointingHandCursor)
        
        self.btn_prev = QPushButton("⏮")
        self.btn_play = QPushButton("▶")
        self.btn_next = QPushButton("⏭")

        self.btn_prev.setToolTip("Предыдущий трек (Ctrl+←)")
        self.btn_play.setToolTip("Играть / Пауза (Space)")
        self.btn_next.setToolTip("Следующий трек (Ctrl+→)")
        
        for btn in [self.btn_prev, self.btn_play, self.btn_next]:
            btn.setObjectName("controlBtn")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedSize(40, 40)
            self.controls_layout.addWidget(btn)
            
        self.btn_play.setFixedSize(50, 50)
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
        self.vol_icon = QPushButton("🔊")
        self.vol_icon.setObjectName("volIcon")
        self.vol_icon.setFlat(True)
        self.vol_icon.setCursor(Qt.CursorShape.PointingHandCursor)
        self.vol_icon.setFixedWidth(24)
        self.vol_icon.setToolTip("Выключить/включить звук (Ctrl+M)")
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setObjectName("volumeSlider")
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(70)
        self.volume_slider.setFixedWidth(100)
        
        self.volume_layout.addWidget(self.vol_icon)
        self.volume_layout.addWidget(self.volume_slider)
        
        self.vol_widget = QWidget()
        self.vol_widget.setFixedWidth(200)
        self.vol_widget.setLayout(self.volume_layout)
        self.bottom_layout.addWidget(self.vol_widget)

        # Ручка для изменения размера безрамочного окна (у него нет
        # системной рамки, а значит и штатных зон ресайза по краям)
        self.size_grip = QSizeGrip(self.bottom_bar)
        self.size_grip.setFixedSize(16, 16)
        self.bottom_layout.addWidget(
            self.size_grip, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom
        )

        self.main_layout.addWidget(self.bottom_bar)

    def apply_modern_theme(self):
        # QSS: Глубокий черный фон, отсутствие рамок, акценты циана/бирюзы
        qss = """
        QPushButton#modeBtn {
            background-color: transparent;
            color: #71717a;
            font-size: 18px;
            border: none;
        }
        QPushButton#modeBtn:hover {
            color: #ffffff;
        }
        /* Стиль для активного состояния (будем применять через setStyleSheet динамически) */
        .active {
            color: #00e5ff !important;
        }
        QMainWindow, #tool_bar {
            background-color: #09090b;
        }
        QLabel {
            color: #fafafa;
            font-family: 'Segoe UI', sans-serif;
        }
        QLabel#headerTitle {
            font-size: 26px;
            font-weight: 800;
            color: #ffffff;
        }
        QLabel#trackTitle {
            font-size: 15px;
            font-weight: bold;
        }
        QLabel#trackArtist {
            font-size: 12px;
            color: #a1a1aa;
        }
        QLabel#timeLabel {
            font-size: 11px;
            color: #a1a1aa;
        }

        /* Обложка трека в нижней панели */
        QLabel#artThumbnail {
            background-color: #18181b;
            border: 1px solid #27272a;
            border-radius: 8px;
            font-size: 20px;
            color: #52525b;
        }

        /* Поиск по библиотеке */
        QLineEdit#searchInput {
            background-color: #18181b;
            color: #fafafa;
            border: 1px solid #27272a;
            border-radius: 6px;
            padding: 6px 10px;
            font-size: 13px;
        }
        QLineEdit#searchInput:focus {
            border-color: #00e5ff;
        }

        /* Плейсхолдер пустой библиотеки */
        QLabel#emptyState {
            color: #71717a;
            font-size: 14px;
            padding-top: 60px;
        }
        
        /* Стилизация списка треков */
        QListWidget#modernList {
            background-color: transparent;
            color: #e4e4e7;
            border: none;
            font-size: 14px;
            outline: none;
        }
        QListWidget#modernList::item {
            padding: 12px;
            border-radius: 8px;
            margin-bottom: 4px;
        }
        QListWidget#modernList::item:hover {
            background-color: #18181b;
        }
        QListWidget#modernList::item:selected {
            background-color: #27272a;
            color: #00e5ff;
            font-weight: bold;
        }

        /* Кнопка добавления */
        QPushButton#btnAdd {
            background-color: #18181b;
            color: #fafafa;
            border: 1px solid #27272a;
            border-radius: 6px;
            padding: 8px;
            font-size: 13px;
            font-weight: bold;
        }
        QPushButton#btnAdd:hover {
            background-color: #27272a;
            border-color: #00e5ff;
            color: #00e5ff;
        }

        /* Нижняя панель */
        QWidget#bottomBar {
            background-color: #121214;
            border-top: 1px solid #27272a;
        }

        /* Кнопки управления */
        QPushButton#controlBtn {
            background-color: transparent;
            color: #a1a1aa;
            border: none;
            font-size: 18px;
        }
        QPushButton#controlBtn:hover {
            color: #ffffff;
        }
        QPushButton#playBtn {
            background-color: #ffffff;
            color: #000000;
            border-radius: 25px;
            font-size: 20px;
            padding-left: 2px; /* Выравнивание иконки play */
        }
        QPushButton#playBtn:hover {
            background-color: #00e5ff;
        }

        /* Иконка громкости (кликабельна — mute/unmute) */
        QPushButton#volIcon {
            background-color: transparent;
            border: none;
            font-size: 14px;
        }

        /* Ползунки */
        QSlider::groove:horizontal {
            height: 4px;
            background: #27272a;
            border-radius: 2px;
        }
        QSlider::sub-page:horizontal {
            background: #00e5ff;
            border-radius: 2px;
        }
        QSlider::handle:horizontal {
            background: #ffffff;
            width: 12px;
            height: 12px;
            margin: -4px 0;
            border-radius: 6px;
        }
        QSlider::handle:horizontal:hover {
            background: #00e5ff;
            width: 14px;
            height: 14px;
            margin: -5px 0;
            border-radius: 7px;
        }
        
        /* Скроллбар для списка */
        QScrollBar:vertical {
            border: none;
            background: transparent;
            width: 8px;
            margin: 0px;
        }
        QScrollBar::handle:vertical {
            background: #27272a;
            min-height: 20px;
            border-radius: 4px;
        }
        QScrollBar::handle:vertical:hover {
            background: #3f3f46;
        }

        /* Всплывающие подсказки */
        QToolTip {
            background-color: #18181b;
            color: #fafafa;
            border: 1px solid #27272a;
            border-radius: 4px;
            padding: 4px 8px;
        }
        """
        self.setStyleSheet(qss)

    def toggle_shuffle(self):
        self.is_shuffled = not self.is_shuffled
        self.btn_shuffle.setStyleSheet("color: #00e5ff;" if self.is_shuffled else "color: #71717a;")

    def toggle_loop(self):
        if not self.is_looped:
            self.is_looped = 'one'
            self.btn_loop.setText("🔂")
            self.btn_loop.setStyleSheet("color: #00e5ff;")
            self.btn_loop.setToolTip("Повтор: один трек")
        elif self.is_looped == 'one':
            self.is_looped = 'all'
            self.btn_loop.setText("🔁")
            self.btn_loop.setStyleSheet("color: #00e5ff;")
            self.btn_loop.setToolTip("Повтор: весь плейлист")
        else:
            self.is_looped = False
            self.btn_loop.setText("↻")
            self.btn_loop.setStyleSheet("color: #71717a;")
            self.btn_loop.setToolTip("Повтор: выключен")

    def handle_media_status(self, status):
        # Если трек закончился
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            if self.is_looped == 'one':
                self.player.play()
            elif self.is_looped == 'all' or self.track_list.currentRow() < self.track_list.count() - 1:
                self.next_track()
            else:
                self.btn_play.setText("▶")
                self.update_mpris_playback_status()

    # Перепишем next_track с учетом рандомизатора
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

            self.update_library_header()

    def play_selected_track(self):
        selected_row = self.track_list.currentRow()
        if selected_row >= 0:
            file_path = self.current_playlist[selected_row]
            self.player.setSource(QUrl.fromLocalFile(file_path))
            self.player.play()
            self.btn_play.setText("⏸")
            
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
            self.btn_play.setText("▶")
        elif self.player.playbackState() == QMediaPlayer.PlaybackState.PausedState:
            self.player.play()
            self.btn_play.setText("⏸")
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
        if value == 0:
            icon = "🔇"
        elif value < 40:
            icon = "🔈"
        elif value < 75:
            icon = "🔉"
        else:
            icon = "🔊"
        self.vol_icon.setText(icon)

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
        seconds = (ms // 1000) % 60
        minutes = (ms // 60000) % 60
        return f"{minutes}:{seconds:02}"

    def mark_current_track(self, row):
        """Визуально отличает играющий сейчас трек от просто выделенного."""
        accent = QColor("#00e5ff")
        default_color = QColor("#e4e4e7")
        for i in range(self.track_list.count()):
            item = self.track_list.item(i)
            font = item.font()
            is_current = (i == row)
            font.setBold(is_current)
            item.setFont(font)
            item.setForeground(accent if is_current else default_color)
            has_marker = item.text().startswith("▶ ")
            if is_current and not has_marker:
                item.setText(f"▶ {item.text()}")
            elif not is_current and has_marker:
                item.setText(item.text()[2:])

    def request_artwork(self, title, artist):
        self.art_thumbnail.setPixmap(QPixmap())
        self.art_thumbnail.setText("🎵")
        self._art_request_id = self.artwork_loader.request(title, artist)

    def on_artwork_ready(self, data, request_id):
        # Трек уже могли переключить, пока обложка грузилась — отбрасываем устаревший ответ
        if request_id != self._art_request_id:
            return
        pixmap = QPixmap()
        if not pixmap.loadFromData(data):
            return
        scaled = pixmap.scaled(
            56, 56, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation
        )
        self.art_thumbnail.setText("")
        self.art_thumbnail.setPixmap(rounded_pixmap(scaled, 8))

    def filter_tracks(self, text):
        query = text.strip().lower()
        for i in range(self.track_list.count()):
            item = self.track_list.item(i)
            item.setHidden(bool(query) and query not in item.text().lower())

    def update_library_header(self):
        count = self.track_list.count()
        if count == 0:
            self.header_label.setText("Library")
            self.track_list.setVisible(False)
            self.empty_state.setVisible(True)
        else:
            self.header_label.setText(f"Library · {count} tracks")
            self.track_list.setVisible(True)
            self.empty_state.setVisible(False)
    
    def closeEvent(self, event):
        if hasattr(self, "discord_rpc"):
            self.discord_rpc.shutdown()
        super().closeEvent(event)

    def load_saved_library(self):
        last_folder = self.settings.value("last_folder")
        if last_folder and Path(last_folder).exists():
            # Сканируем папку на наличие аудиофайлов
            valid_extensions = ('.mp3', '.wav', '.ogg', '.m4a')
            files = [str(f) for f in Path(last_folder).iterdir() if f.suffix.lower() in valid_extensions]
            
            if files:
                self.current_playlist = files
                for f_path in files:
                    self.track_list.addItem(Path(f_path).name)
                self.now_playing_artist.setText(f"Loaded: {Path(last_folder).name}")

        self.update_library_header()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = GlideMusicModern()
    window.show()
    sys.exit(app.exec())