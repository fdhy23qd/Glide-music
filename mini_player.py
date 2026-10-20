"""
Мини-плеер: маленькая карточка поверх всех окон — обложка, трек, «назад / пауза / вперёд»,
тонкая полоса прогресса. Щелчок (или касание) по ручке сверху сворачивает его в узкую
полоску, щелчок по полоске — разворачивает. Тянуть — переносить. Положение и состояние
запоминаются.

Глобальная горячая клавиша (только Windows): RegisterHotKey на окно мини-плеера — Windows
присылает ему WM_HOTKEY, даже когда фокус в другой программе. Клавиша сворачивает и
разворачивает плеер (если он скрыт — показывает).
"""

import sys

from PyQt6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRect, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QKeySequence, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QLayout, QPushButton, QVBoxLayout, QWidget

IS_WINDOWS = sys.platform.startswith("win")

CARD = QSize(340, 84)
STRIP = QSize(150, 12)
WM_HOTKEY = 0x0312
HOTKEY_ID = 0x4D50   # «MP»

def _user32():
    """user32 с объявленными типами: без argtypes ctypes передаёт HWND (указатель) как 32-битный int —
    на 64-битной Windows это access violation прямо при запуске."""
    import ctypes
    from ctypes import wintypes
    u = ctypes.WinDLL("user32", use_last_error=True)
    u.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    u.RegisterHotKey.restype = wintypes.BOOL
    u.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    u.UnregisterHotKey.restype = wintypes.BOOL
    return u


# Qt-клавиша → виртуальный код Windows
_VK = {Qt.Key.Key_Space: 0x20, Qt.Key.Key_Left: 0x25, Qt.Key.Key_Up: 0x26, Qt.Key.Key_Right: 0x27,
       Qt.Key.Key_Down: 0x28, Qt.Key.Key_Home: 0x24, Qt.Key.Key_End: 0x23, Qt.Key.Key_PageUp: 0x21,
       Qt.Key.Key_PageDown: 0x22, Qt.Key.Key_Insert: 0x2D, Qt.Key.Key_Delete: 0x2E, Qt.Key.Key_Pause: 0x13,
       Qt.Key.Key_Comma: 0xBC, Qt.Key.Key_Period: 0xBE, Qt.Key.Key_Minus: 0xBD, Qt.Key.Key_Equal: 0xBB,
       Qt.Key.Key_Slash: 0xBF, Qt.Key.Key_Semicolon: 0xBA, Qt.Key.Key_QuoteLeft: 0xC0}


def hotkey_to_windows(text):
    """«Ctrl+Alt+M» → (модификаторы, виртуальный код) для RegisterHotKey или None."""
    seq = QKeySequence(text or "")
    if seq.isEmpty():
        return None
    combo = seq[0]
    key, mods = combo.key(), combo.keyboardModifiers()
    m = 0x4000   # MOD_NOREPEAT: удержание не повторяет
    if mods & Qt.KeyboardModifier.AltModifier:
        m |= 0x1
    if mods & Qt.KeyboardModifier.ControlModifier:
        m |= 0x2
    if mods & Qt.KeyboardModifier.ShiftModifier:
        m |= 0x4
    if mods & Qt.KeyboardModifier.MetaModifier:
        m |= 0x8
    k = key.value if hasattr(key, "value") else int(key)
    if Qt.Key.Key_A.value <= k <= Qt.Key.Key_Z.value or Qt.Key.Key_0.value <= k <= Qt.Key.Key_9.value:
        vk = k   # у букв и цифр коды совпадают с ASCII
    elif Qt.Key.Key_F1.value <= k <= Qt.Key.Key_F24.value:
        vk = 0x70 + (k - Qt.Key.Key_F1.value)
    else:
        vk = _VK.get(Qt.Key(k))
    if vk is None:
        return None
    return m, vk


class MiniPlayer(QWidget):
    toggle_requested = pyqtSignal()
    next_requested = pyqtSignal()
    previous_requested = pyqtSignal()
    open_main_requested = pyqtSignal()

    def __init__(self, settings, icon_for, colors):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("Glide Music — мини-плеер")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)   # не отбирает фокус
        self.settings = settings
        self.icon_for = icon_for          # (имя svg, размер, цвет) → QIcon
        self.c = colors                   # токены Glide из app.py
        self.collapsed = settings.value("mini/collapsed", False, type=bool)
        self.position = 0
        self.duration = 0
        self.playing = False
        self._press = None
        self._moved = False
        self._hotkey_registered = False

        self.cover = QLabel(self)
        self.cover.setFixedSize(56, 56)
        self.title = QLabel("Glide Music", self)
        self.artist = QLabel("", self)
        self.title.setFont(QFont(self.c["font"], 10, QFont.Weight.Bold))
        self.artist.setFont(QFont(self.c["font"], 9))
        self.title.setStyleSheet(f"color: {self.c['text']}; background: transparent;")
        self.artist.setStyleSheet(f"color: {self.c['text2']}; background: transparent;")

        self.btn_prev = self._button("PREV_ICON_SVG", self.previous_requested.emit, "Предыдущий")
        self.btn_play = self._button("PLAY_ICON_SVG", self.toggle_requested.emit, "Играть / пауза")
        self.btn_next = self._button("NEXT_ICON_SVG", self.next_requested.emit, "Следующий")

        text = QVBoxLayout()
        text.setSpacing(1)
        text.addStretch()
        text.addWidget(self.title)
        text.addWidget(self.artist)
        text.addStretch()
        row = QHBoxLayout(self)
        # без ограничения: иначе раскладка с кнопками не даёт окну сжаться в полоску
        row.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        row.setContentsMargins(14, 16, 12, 12)
        row.setSpacing(10)
        row.addWidget(self.cover)
        row.addLayout(text, 1)
        for b in (self.btn_prev, self.btn_play, self.btn_next):
            row.addWidget(b)
        self._children = [self.cover, self.title, self.artist, self.btn_prev, self.btn_play, self.btn_next]

        self.anim = QPropertyAnimation(self, b"geometry", self)
        self.anim.setDuration(180)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.anim.finished.connect(self._after_anim)

        self.set_cover(None)
        self._apply_state(animate=False)
        pos = settings.value("mini/pos")
        if isinstance(pos, QPoint):
            self.move(pos)

    # ---- кнопки ----
    def _button(self, icon, slot, tip):
        b = QPushButton(self)
        b.setFixedSize(34, 34)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setToolTip(tip)
        b.setIcon(self.icon_for(icon, 16, self.c["text"]))
        b.setIconSize(QSize(16, 16))
        b.clicked.connect(slot)
        b.setStyleSheet(f"""
            QPushButton {{ background: transparent; border: none; border-radius: 17px; }}
            QPushButton:hover {{ background: {self.c['surfaceHigher']}; }}
            QPushButton:pressed {{ background: {self.c['surfacePressed']}; }}
        """)
        return b

    # ---- состояние от главного окна ----
    def set_track(self, title, artist):
        self.title.setText(self._elide(self.title, title or "Glide Music", 170))
        self.artist.setText(self._elide(self.artist, artist or "", 170))
        self.setToolTip(f"{title} — {artist}" if artist else (title or ""))

    def _elide(self, label, text, width):
        return label.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, width)

    def set_cover(self, pixmap):
        size = self.cover.size()
        if pixmap is None or pixmap.isNull():
            pm = QPixmap(size)
            pm.fill(QColor(self.c["surfaceHigh"]))
        else:
            pm = pixmap.scaled(size, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                               Qt.TransformationMode.SmoothTransformation)
        rounded = QPixmap(size)
        rounded.fill(Qt.GlobalColor.transparent)
        p = QPainter(rounded)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(0, 0, size.width(), size.height(), 12, 12)
        p.setClipPath(path)
        p.drawPixmap(0, 0, pm)
        p.end()
        self.cover.setPixmap(rounded)

    def set_playing(self, playing):
        self.playing = playing
        self.btn_play.setIcon(self.icon_for("PAUSE_ICON_SVG" if playing else "PLAY_ICON_SVG", 16, self.c["text"]))

    def set_progress(self, position, duration):
        self.position, self.duration = position, duration
        self.update(0, self.height() - 6, self.width(), 6) if not self.collapsed else self.update()

    # ---- свернуть / развернуть ----
    def toggle_collapsed(self):
        self.collapsed = not self.collapsed
        self.settings.setValue("mini/collapsed", self.collapsed)
        self._apply_state(animate=True)

    def _apply_state(self, animate):
        g = self.geometry()
        center_x = g.x() + g.width() // 2
        size = STRIP if self.collapsed else CARD
        target = QRect(center_x - size.width() // 2, g.y(), size.width(), size.height())
        for w in self._children:
            w.setVisible(False)   # на время анимации — пусто; содержимое покажем, если развернулись
        if animate and self.isVisible():
            self.anim.stop()
            self.anim.setStartValue(g)
            self.anim.setEndValue(target)
            self.anim.start()
        else:
            self.setGeometry(target)
            self._after_anim()

    def _after_anim(self):
        for w in self._children:
            w.setVisible(not self.collapsed)
        self.setCursor(Qt.CursorShape.PointingHandCursor if self.collapsed else Qt.CursorShape.ArrowCursor)
        self.update()

    def show_expanded(self):
        if self.collapsed:
            self.toggle_collapsed()
        self.show()

    # ---- мышь и касание: щелчок — свернуть/развернуть, перетаскивание — перенести ----
    def _on_handle(self, pos):
        return self.collapsed or pos.y() < 14   # полоска целиком или ручка вверху карточки

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._press = e.position().toPoint()
            self._moved = False

    def mouseMoveEvent(self, e):
        if self._press is not None and not self._moved and (e.position().toPoint() - self._press).manhattanLength() > 5:
            self._moved = True
            handle = self.windowHandle()
            if handle is not None and handle.startSystemMove():
                self._press = None

    def mouseReleaseEvent(self, e):
        if self._press is not None and not self._moved and self._on_handle(self._press):
            self.toggle_collapsed()
        self._press = None
        self.settings.setValue("mini/pos", self.pos())

    def mouseDoubleClickEvent(self, e):
        if not self.collapsed and not self._on_handle(e.position().toPoint()):
            self.open_main_requested.emit()

    def moveEvent(self, e):
        super().moveEvent(e)
        if not self.anim.state() == QPropertyAnimation.State.Running:
            self.settings.setValue("mini/pos", self.pos())

    # ---- рисование ----
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect().adjusted(1, 1, -1, -1)
        radius = r.height() / 2 if self.collapsed else 18
        bg = QColor(self.c["surface"])
        bg.setAlphaF(0.97)
        p.setPen(QPen(QColor(self.c["outline"]), 1))
        p.setBrush(bg)
        p.drawRoundedRect(r, radius, radius)
        frac = self.position / self.duration if self.duration > 0 else 0
        accent = QColor(self.c["accent"])
        p.setPen(Qt.PenStyle.NoPen)
        if self.collapsed:
            # полоска: прогресс внутри
            inner = r.adjusted(3, 3, -3, -3)
            track = QColor(self.c["surfaceHigher"])
            p.setBrush(track)
            p.drawRoundedRect(inner, inner.height() / 2, inner.height() / 2)
            if frac > 0:
                done = QRect(inner.x(), inner.y(), max(inner.height(), int(inner.width() * frac)), inner.height())
                p.setBrush(accent if self.playing else QColor(self.c["text2"]))
                p.drawRoundedRect(done, inner.height() / 2, inner.height() / 2)
        else:
            # ручка сверху: щелчок — свернуть
            p.setBrush(QColor(self.c["text2"]))
            p.drawRoundedRect(QRect(r.center().x() - 18, r.y() + 5, 36, 4), 2, 2)
            # прогресс снизу
            y = r.bottom() - 4
            p.setBrush(QColor(self.c["surfaceHigher"]))
            p.drawRoundedRect(QRect(r.x() + 16, y, r.width() - 32, 3), 1.5, 1.5)
            if frac > 0:
                p.setBrush(accent)
                p.drawRoundedRect(QRect(r.x() + 16, y, int((r.width() - 32) * frac), 3), 1.5, 1.5)
        p.end()

    # ---- глобальная горячая клавиша (Windows) ----
    def register_hotkey(self, text):
        """Назначает клавишу; True — получилось. Пустая строка — снять."""
        self.unregister_hotkey()
        if not IS_WINDOWS or not text:
            return False
        parsed = hotkey_to_windows(text)
        if parsed is None:
            return False
        mods, vk = parsed
        hwnd = int(self.winId())   # окно создаётся здесь, даже если скрыто
        ok = bool(_user32().RegisterHotKey(hwnd, HOTKEY_ID, mods, vk))
        self._hotkey_registered = ok
        return ok

    def unregister_hotkey(self):
        if IS_WINDOWS and self._hotkey_registered:
            _user32().UnregisterHotKey(int(self.winId()), HOTKEY_ID)
        self._hotkey_registered = False

    def nativeEvent(self, event_type, message):
        if IS_WINDOWS and event_type == b"windows_generic_MSG":
            import ctypes.wintypes
            msg = ctypes.wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                self.on_hotkey()
                return True, 0
        return super().nativeEvent(event_type, message)

    def on_hotkey(self):
        if not self.isVisible():
            self.show_expanded()
        else:
            self.toggle_collapsed()
