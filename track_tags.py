"""
Чтение метаданных (название, автор) из аудиофайлов.

Использует mutagen (EasyID3-совместимый интерфейс), который понимает
MP3, WAV(RIFF INFO), OGG Vorbis и MP4/M4A — то есть все форматы,
которые Glide Music уже открывает через QFileDialog.

Установка:
    pip install mutagen
"""

from pathlib import Path

try:
    from mutagen import File as MutagenFile
    _MUTAGEN_AVAILABLE = True
except ImportError:
    _MUTAGEN_AVAILABLE = False


def read_track_tags(file_path):
    """
    Возвращает (title, artist) для файла.

    Если тег есть в файле — берётся он. Если тега нет (или mutagen
    не смог его прочитать, или библиотека не установлена) —
    title = имя файла без расширения, artist = "Local File".
    """
    filename = Path(file_path).stem
    fallback_title = filename[:30] + ("..." if len(filename) > 30 else "")
    fallback_artist = "Local File"

    if not _MUTAGEN_AVAILABLE:
        return fallback_title, fallback_artist

    try:
        audio = MutagenFile(file_path, easy=True)
    except Exception:
        return fallback_title, fallback_artist

    if audio is None or not audio.tags:
        return fallback_title, fallback_artist

    tags = audio.tags

    title = _first_tag(tags, "title")
    artist = _first_tag(tags, "artist")

    title_text = title.strip() if title else fallback_title
    if title_text and len(title_text) > 60:
        title_text = title_text[:60] + "..."

    artist_text = artist.strip() if artist else fallback_artist

    return title_text, artist_text


def _first_tag(tags, key):
    """easy=True возвращает списки строк ['значение'] либо ничего."""
    try:
        values = tags.get(key)
    except Exception:
        return None
    if not values:
        return None
    return values[0]
