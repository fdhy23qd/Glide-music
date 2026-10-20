"""
Проверка Glide Music без окна: QT_QPA_PLATFORM=offscreen python tests/smoke_test.py ПАПКА_С_МУЗЫКОЙ [снимки]

Нужны отдельные настройки и кэш (XDG_CONFIG_HOME, XDG_CACHE_HOME — на Linux; на Windows
QSettings и %LOCALAPPDATA% подменяет сам тест), GLIDE_MUSIC_NO_DISCORD=1.
Ожидается папка с треками из tests/make_test_music.py (теги, встроенные обложки).
"""

import os
import subprocess
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

from PyQt6.QtCore import QSettings, QTimer
from PyQt6.QtWidgets import QApplication

failed = 0


def report(name, ok, detail=""):
    global failed
    print(f"{'OK  ' if ok else 'FAIL'} {name}{'  — ' + detail if detail and not ok else ''}", flush=True)
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


def mpris(*args):
    try:
        names = subprocess.run(["playerctl", "-l"], capture_output=True, text=True, timeout=3).stdout.split()
    except (OSError, subprocess.TimeoutExpired):
        return None
    mine = [n for n in names if n.startswith("GlideMusic.instance") and n.endswith(str(os.getpid()))] or \
           [n for n in names if n == "GlideMusic"]
    if not mine:
        return None
    return subprocess.run(["playerctl", "-p", mine[0], *args], capture_output=True, text=True, timeout=3).stdout.strip()


def main():
    music = Path(sys.argv[1])
    shots = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    s = QSettings("Glide", "GlideMusic")
    s.setValue("library_root", str(music))
    s.setValue("volume", 0)
    s.remove("last/path")
    s.sync()

    app = QApplication(sys.argv)
    import app as gm
    w = gm.GlideMusicModern()
    w.show()

    # 1. список — названия из тегов, фоном
    report("список из папки", wait(app, lambda: w.track_list.count() >= 5), f"строк {w.track_list.count()}")
    texts = [w.track_list.item(i).text() for i in range(w.track_list.count())]
    wait(app, lambda: any("Красная песня" in w.track_list.item(i).text() for i in range(w.track_list.count())))
    texts = [w.track_list.item(i).text() for i in range(w.track_list.count())]
    report("названия из тегов, не из имён файлов", "Красная песня — Группа Тест" in texts and "Зелёная песня — Группа Тест" in texts, str(texts))
    report("имя «Исполнитель - Название» без тегов", "Группа крови — Кино" in texts, str(texts))

    # 2. воспроизведение, обложка из файла
    row = texts.index("Красная песня — Группа Тест")
    w.load_track(row)
    report("играет", wait(app, lambda: w.player.playbackState() == w.player.PlaybackState.PlayingState), str(w.player.playbackState()))
    report("строка «сейчас играет»", w.now_playing_title.text() == "Красная песня" and w.now_playing_artist.text() == "Группа Тест")
    got = wait(app, lambda: w.art_thumbnail.pixmap() is not None and w.art_thumbnail.pixmap().width() > 40)
    img = w.art_thumbnail.pixmap().toImage() if got else None
    color = img.pixelColor(img.width() // 2, img.height() // 2).getRgb()[:3] if img is not None else None
    report("встроенная обложка (красная)", color is not None and color[0] > 180 and color[1] < 100, str(color))

    # 3. системный плеер
    if sys.platform.startswith("linux"):
        wait(app, lambda: (mpris("metadata", "title") or "") == "Красная песня", 5)
        report("MPRIS: название", mpris("metadata", "title") == "Красная песня", str(mpris("metadata", "title")))
        report("MPRIS: альбом", mpris("metadata", "album") == "Цвета", str(mpris("metadata", "album")))
        art = mpris("metadata", "mpris:artUrl") or ""
        report("MPRIS: обложка файлом", art.startswith("file://") and Path(art[7:]).exists(), art)
        before = w.current_track_row
        mpris("next")
        report("MPRIS: «вперёд» листает список", wait(app, lambda: w.current_track_row != before, 4))
        mpris("previous")
        report("MPRIS: «назад» возвращает", wait(app, lambda: w.current_track_row == before, 4))
    else:
        report(f"системный плеер: {w.session.name or 'нет'}", w.session.name == "SMTC", type(w.session).__name__)

    # 4. очередь «играть следующим»
    last = w.track_list.count() - 1
    w.track_list.clearSelection()
    w.track_list.item(last).setSelected(True)
    w.play_next()
    w.next_track()
    report("очередь: следующим — выбранный", wait(app, lambda: w.current_track_row == last, 3), f"строка {w.current_track_row}")

    # 5. мини-плеер
    w.set_mini_visible(True)
    app.processEvents()
    report("мини-плеер показан", w.mini.isVisible())
    report("мини-плеер: трек", w.mini.title.text() != "Glide Music", w.mini.title.text())
    w.mini.toggle_collapsed()
    wait(app, lambda: w.mini.anim.state() != w.mini.anim.State.Running, 2)
    report("мини-плеер свёрнут в полоску", w.mini.height() <= 16, str(w.mini.size()))
    if shots:
        shots.mkdir(parents=True, exist_ok=True)
        w.mini.grab().save(str(shots / "mini-strip.png"))
    w.mini.toggle_collapsed()
    wait(app, lambda: w.mini.anim.state() != w.mini.anim.State.Running, 2)
    report("мини-плеер развёрнут", w.mini.height() >= 80, str(w.mini.size()))
    if shots:
        wait(app, lambda: False, 0.5)
        w.mini.grab().save(str(shots / "mini.png"))
        w.grab().save(str(shots / "main.png"))

    # 6. память: тот же трек и место после перезапуска
    w.player.setPosition(2500)
    wait(app, lambda: w.player.position() >= 2400, 3)
    path = w.current_path
    w.close()
    app.processEvents()
    w2 = gm.GlideMusicModern()
    ok = wait(app, lambda: w2.current_path == path, 6)
    report("после перезапуска — тот же трек", ok, f"{w2.current_path} != {path}")
    report("…и на паузе, без автозапуска", w2.player.playbackState() != w2.player.PlaybackState.PlayingState)
    report("…с того же места", wait(app, lambda: w2.player.position() >= 2000, 4), str(w2.player.position()))
    w2.close()

    print(f"\nпровалено: {failed}")
    QTimer.singleShot(0, app.quit)
    app.exec()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
