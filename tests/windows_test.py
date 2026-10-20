"""
Проверка того, что есть только на Windows (запускается в CI на windows-latest):
    python tests/windows_test.py ПАПКА_С_МУЗЫКОЙ ПАПКА_ДЛЯ_СНИМКОВ
SMTC (плашка «Сейчас играет»), глобальная горячая клавиша мини-плеера, «поверх всех окон».
"""

import ctypes
import ctypes.wintypes
import os
import sys

for _s in (sys.stdout, sys.stderr):   # консоль Windows — не UTF-8
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("GLIDE_MUSIC_NO_DISCORD", "1")

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication

failed = 0


def report(name, ok, detail=""):
    global failed
    print(f"{'OK  ' if ok else 'FAIL'} {name}{'  — ' + str(detail) if detail and not ok else ''}", flush=True)
    if not ok:
        failed += 1


def wait(app, cond, timeout=8.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def main():
    music = Path(sys.argv[1])
    shots = Path(sys.argv[2])
    shots.mkdir(parents=True, exist_ok=True)
    s = QSettings("Glide", "GlideMusic")
    s.clear()
    s.setValue("library_root", str(music))
    s.setValue("volume", 0)
    s.sync()

    app = QApplication(sys.argv)
    import app as gm
    from mini_player import HOTKEY_ID, WM_HOTKEY, hotkey_to_windows

    w = gm.GlideMusicModern()
    w.show()

    # ---- SMTC ----
    report("системный плеер — SMTC", w.session.name == "SMTC", type(w.session).__name__)
    wait(app, lambda: w.track_list.count() >= 5)
    texts = [w.track_list.item(i).text() for i in range(w.track_list.count())]
    wait(app, lambda: any("Красная песня" in w.track_list.item(i).text() for i in range(w.track_list.count())))
    texts = [w.track_list.item(i).text() for i in range(w.track_list.count())]
    report("названия из тегов", "Красная песня — Группа Тест" in texts, texts)
    w.load_track(texts.index("Красная песня — Группа Тест"))
    if w.session.name == "SMTC":
        props = w.session.updater.music_properties
        report("SMTC: название", props.title == "Красная песня", props.title)
        report("SMTC: исполнитель", props.artist == "Группа Тест", props.artist)
        report("SMTC: альбом", props.album_title == "Цвета", props.album_title)
        w.player.play()
        w.session.set_status("Playing")
        report("SMTC: статус «играет»", int(w.session.smtc.playback_status) == int(w.session._Status.PLAYING),
               w.session.smtc.playback_status)
        got = wait(app, lambda: w.session.updater.thumbnail is not None, 6)
        report("SMTC: обложка из файла", got)
        w.session.set_cover(None, "https://is1-ssl.mzstatic.com/image/thumb/Music/v4/00/00/00/x/600x600bb.jpg")
        report("SMTC: обложка по ссылке", w.session.updater.thumbnail is not None)
        report("SMTC: кнопки включены", w.session.smtc.is_next_enabled and w.session.smtc.is_play_enabled)
        # нажатие кнопки системы → команда окну
        before = w.current_track_row
        w.session.commands.next.emit()
        report("SMTC: «вперёд» листает список", wait(app, lambda: w.current_track_row != before, 3))

    # ---- мини-плеер: поверх всех окон и горячая клавиша ----
    w.set_mini_visible(True)
    wait(app, lambda: w.mini.isVisible(), 2)
    hwnd = int(w.mini.winId())
    exstyle = ctypes.windll.user32.GetWindowLongW(hwnd, -20)   # GWL_EXSTYLE
    report("мини-плеер поверх всех окон (WS_EX_TOPMOST)", bool(exstyle & 0x8), hex(exstyle))
    report("разбор клавиши Ctrl+Alt+M", hotkey_to_windows("Ctrl+Alt+M") == (0x4000 | 0x1 | 0x2, ord("M")),
           hotkey_to_windows("Ctrl+Alt+M"))
    report("разбор клавиши Ctrl+Shift+F9", hotkey_to_windows("Ctrl+Shift+F9") == (0x4000 | 0x2 | 0x4, 0x78),
           hotkey_to_windows("Ctrl+Shift+F9"))
    ok = w.mini.register_hotkey("Ctrl+Alt+Shift+F11")
    report("RegisterHotKey", ok, ctypes.GetLastError())
    collapsed = w.mini.collapsed
    ctypes.windll.user32.PostMessageW(hwnd, WM_HOTKEY, HOTKEY_ID, 0)   # как будто нажали клавишу
    report("WM_HOTKEY сворачивает/разворачивает", wait(app, lambda: w.mini.collapsed != collapsed, 3))
    wait(app, lambda: w.mini.anim.state() != w.mini.anim.State.Running, 2)
    w.mini.grab().save(str(shots / ("mini-strip.png" if w.mini.collapsed else "mini.png")))
    ctypes.windll.user32.PostMessageW(hwnd, WM_HOTKEY, HOTKEY_ID, 0)
    wait(app, lambda: w.mini.collapsed == collapsed, 3)
    wait(app, lambda: w.mini.anim.state() != w.mini.anim.State.Running, 2)
    wait(app, lambda: False, 0.6)
    w.mini.grab().save(str(shots / ("mini-strip.png" if w.mini.collapsed else "mini.png")))
    w.grab().save(str(shots / "main.png"))
    w.mini.unregister_hotkey()
    # снятая клавиша снова свободна
    again = ctypes.windll.user32.RegisterHotKey(None, 77, 0x4000 | 0x1 | 0x2 | 0x4, 0x7A)
    report("UnregisterHotKey освобождает клавишу", bool(again))
    ctypes.windll.user32.UnregisterHotKey(None, 77)
    w.close()
    s.clear()
    print(f"\nпровалено: {failed}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
