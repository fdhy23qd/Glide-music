"""
Метаданные аудиофайлов: название, исполнитель, альбом, длительность и
встроенная обложка. Работает на Linux и Windows одинаково (mutagen).

Форматы: MP3 (ID3), FLAC, M4A/AAC/ALAC (MP4), OGG Vorbis, Opus, WAV.

Если тегов нет — название берётся из имени файла; имя вида
«Исполнитель - Название» разбирается на две части.

Установка:
    pip install mutagen
"""

import base64
from pathlib import Path

try:
    from mutagen import File as MutagenFile
    _MUTAGEN_AVAILABLE = True
except ImportError:
    _MUTAGEN_AVAILABLE = False

UNKNOWN_ARTIST = ""   # нет исполнителя: в интерфейсе — просто название
AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".mp4", ".aac", ".ogg", ".oga", ".opus", ".wav"}


def _from_filename(path):
    stem = Path(path).stem
    # «Кино - Группа крови» → исполнитель «Кино», название «Группа крови»
    for sep in (" - ", " – ", " — "):
        if sep in stem:
            artist, title = stem.split(sep, 1)
            if artist.strip() and title.strip():
                return title.strip(), artist.strip()
    return stem, UNKNOWN_ARTIST


def _first(tags, key):
    try:
        values = tags.get(key)
    except Exception:
        return None
    if not values:
        return None
    value = values[0] if isinstance(values, list) else values
    return str(value).strip() or None


def read_track_info(file_path):
    """
    {title, artist, album, duration_ms} для файла. Никогда не бросает исключений:
    битый файл или нет mutagen — значения из имени файла.
    """
    title, artist = _from_filename(file_path)
    info = {"title": title, "artist": artist, "album": "", "duration_ms": 0}
    if not _MUTAGEN_AVAILABLE:
        return info
    try:
        audio = MutagenFile(file_path, easy=True)
    except Exception:
        return info
    if audio is None:
        return info
    try:
        info["duration_ms"] = int((audio.info.length or 0) * 1000)
    except Exception:
        pass
    tags = audio.tags
    if not tags:
        return info
    tag_title = _first(tags, "title")
    tag_artist = _first(tags, "artist") or _first(tags, "albumartist")
    if tag_title:
        # название из тегов; исполнитель — из тегов, а если его нет, то и из имени файла не берём
        info["title"] = tag_title
        info["artist"] = tag_artist or UNKNOWN_ARTIST
    elif tag_artist:
        info["artist"] = tag_artist
    info["album"] = _first(tags, "album") or ""
    return info


def read_track_tags(file_path):
    """(title, artist) — прежний интерфейс; исполнителя нет — «Local File»."""
    info = read_track_info(file_path)
    return info["title"], info["artist"] or "Local File"


def read_embedded_cover(file_path):
    """Байты встроенной обложки (JPEG/PNG) или None."""
    if not _MUTAGEN_AVAILABLE:
        return None
    try:
        audio = MutagenFile(file_path)
    except Exception:
        return None
    if audio is None:
        return None
    try:
        # FLAC: отдельный блок картинок
        pictures = getattr(audio, "pictures", None)
        if pictures:
            return _prefer_front(pictures)
        tags = audio.tags
        if tags is None:
            return None
        # MP3 / WAV с ID3: кадры APIC
        if hasattr(tags, "getall"):
            frames = tags.getall("APIC")
            if frames:
                front = [f for f in frames if getattr(f, "type", 0) == 3]
                return (front or frames)[0].data
        # MP4 / M4A: covr
        covr = tags.get("covr") if hasattr(tags, "get") else None
        if covr:
            return bytes(covr[0])
        # OGG Vorbis / Opus: METADATA_BLOCK_PICTURE в base64
        blocks = tags.get("metadata_block_picture") if hasattr(tags, "get") else None
        if blocks:
            from mutagen.flac import Picture
            pics = []
            for b in blocks:
                try:
                    pics.append(Picture(base64.b64decode(b)))
                except Exception:
                    continue
            if pics:
                return _prefer_front(pics)
    except Exception:
        return None
    return None


def _prefer_front(pictures):
    front = [p for p in pictures if getattr(p, "type", 0) == 3]   # 3 — лицевая сторона обложки
    return (front or pictures)[0].data
