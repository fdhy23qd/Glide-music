"""Диагностика на Windows: где падает создание окна мини-плеера. Каждый случай — в своём процессе."""
import subprocess
import sys

CASES = {
    "plain": """
from PyQt6.QtWidgets import QApplication, QWidget
from PyQt6.QtCore import Qt
app = QApplication([])
w = QWidget(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
w.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
print(int(w.winId()) != 0)
""",
    "nativeEvent": """
from PyQt6.QtWidgets import QApplication, QWidget
from PyQt6.QtCore import Qt
class W(QWidget):
    def nativeEvent(self, t, m):
        return super().nativeEvent(t, m)
app = QApplication([])
w = W(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
print(int(w.winId()) != 0)
""",
    "nativeEvent+MSG": """
import ctypes.wintypes
from PyQt6.QtWidgets import QApplication, QWidget
from PyQt6.QtCore import Qt
class W(QWidget):
    def nativeEvent(self, t, m):
        msg = ctypes.wintypes.MSG.from_address(int(m))
        return super().nativeEvent(t, m)
app = QApplication([])
w = W(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
print(int(w.winId()) != 0)
""",
    "filter": """
import ctypes.wintypes
from PyQt6.QtWidgets import QApplication, QWidget
from PyQt6.QtCore import Qt, QAbstractNativeEventFilter
class F(QAbstractNativeEventFilter):
    def nativeEventFilter(self, t, m):
        msg = ctypes.wintypes.MSG.from_address(int(m))
        return False, 0
app = QApplication([])
f = F(); app.installNativeEventFilter(f)
w = QWidget(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
w.show(); app.processEvents()
print(int(w.winId()) != 0)
""",
}

for name, code in CASES.items():
    r = subprocess.run([sys.executable, "-X", "faulthandler", "-c", code], capture_output=True, text=True, timeout=60)
    tail = (r.stdout + r.stderr).strip().splitlines()[-3:]
    print(f"{name:18} код {r.returncode}  {' | '.join(tail)}", flush=True)
