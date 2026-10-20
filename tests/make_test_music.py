"""Тестовая фонотека: python tests/make_test_music.py ПАПКА
Треки с тегами и встроенными обложками разных форматов + файл без тегов «Исполнитель - Название».
ffmpeg — системный или из pip-пакета imageio-ffmpeg (на Windows-машинах CI его нет)."""
import base64
import shutil
import subprocess
import sys
from pathlib import Path


def ffmpeg():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def cover(path, rgb):
    from PyQt6.QtGui import QColor, QGuiApplication, QImage
    app = QGuiApplication.instance() or QGuiApplication([])
    img = QImage(300, 300, QImage.Format.Format_RGB32)
    img.fill(QColor(*rgb))
    img.save(str(path))


def main():
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    ff = ffmpeg()
    tmp = out / "_covers"
    tmp.mkdir(exist_ok=True)
    for name, rgb in (("red", (220, 60, 60)), ("green", (50, 180, 90)), ("blue", (60, 110, 230)), ("gold", (230, 170, 40))):
        cover(tmp / f"{name}.png", rgb)
    base = [ff, "-hide_banner", "-v", "error", "-y"]

    def run(args):
        subprocess.run(base + args, check=True)
    run(["-f", "lavfi", "-i", "sine=f=440:d=6", "-i", str(tmp / "red.png"), "-map", "0", "-map", "1",
         "-c:a", "libmp3lame", "-q:a", "6", "-c:v", "png", "-disposition:v", "attached_pic", "-id3v2_version", "3",
         "-metadata", "title=Красная песня", "-metadata", "artist=Группа Тест", "-metadata", "album=Цвета", str(out / "01 track.mp3")])
    run(["-f", "lavfi", "-i", "sine=f=550:d=6", "-i", str(tmp / "green.png"), "-map", "0", "-map", "1",
         "-c:a", "flac", "-c:v", "png", "-disposition:v", "attached_pic",
         "-metadata", "title=Зелёная песня", "-metadata", "artist=Группа Тест", "-metadata", "album=Цвета", str(out / "02 track.flac")])
    run(["-f", "lavfi", "-i", "sine=f=660:d=6", "-i", str(tmp / "blue.png"), "-map", "0", "-map", "1",
         "-c:a", "aac", "-c:v", "png", "-disposition:v", "attached_pic",
         "-metadata", "title=Синяя песня", "-metadata", "artist=Другой Артист", str(out / "03 track.m4a")])
    run(["-f", "lavfi", "-i", "sine=f=770:d=6", "-c:a", "libopus",
         "-metadata", "title=Опус без обложки", "-metadata", "artist=Опус Бэнд", str(out / "04 track.opus")])
    ogg = out / "Кино - Группа крови.ogg"
    run(["-f", "lavfi", "-i", "sine=f=880:d=6", "-c:a", "libvorbis", str(ogg)])
    from mutagen.flac import Picture
    from mutagen.oggvorbis import OggVorbis
    a = OggVorbis(str(ogg))
    p = Picture()
    p.type, p.mime, p.data = 3, "image/png", (tmp / "gold.png").read_bytes()
    a["metadata_block_picture"] = [base64.b64encode(p.write()).decode()]
    a.save()
    shutil.rmtree(tmp)
    print("готово:", sorted(x.name for x in out.iterdir()))


if __name__ == "__main__":
    main()
