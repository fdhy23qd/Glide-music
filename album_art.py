"""
Поиск обложки трека по названию и автору.

Используется iTunes Search API — открытый, без ключа и авторизации,
подходит для такого объёма запросов. Возвращает прямую https-ссылку
на обложку.

Discord RPC для large_image принимает не только ключ ассета,
загруженного в Developer Portal, но и произвольный внешний https-URL
(официально задокументированное поведение — правда, только для
large_image, small_image так не умеет). Поэтому найденную ссылку
можно просто передавать в discord_rpc.py напрямую.

Результаты кэшируются в памяти по (title, artist), чтобы не дёргать
API на каждое периодическое обновление presence — трек между ними
обычно не меняется.
"""

import json
import urllib.parse
import urllib.request

_CACHE = {}
_TIMEOUT = 4
_NO_ARTIST_MARKERS = {"local file", "неизвестный исполнитель"}


def get_artwork_url(title, artist):
    """
    Возвращает URL обложки (str) или None, если найти не удалось
    (нет сети, нет результатов, iTunes недоступен и т.п.) —
    в этом случае вызывающий код должен подставить дефолтную картинку.
    """
    key = (_norm(title), _norm(artist))
    if key in _CACHE:
        return _CACHE[key]

    url = _fetch_artwork_url(title, artist)
    _CACHE[key] = url
    return url


def _norm(text):
    return (text or "").strip().lower()


def _fetch_artwork_url(title, artist):
    parts = []
    if title:
        parts.append(title)
    if artist and _norm(artist) not in _NO_ARTIST_MARKERS:
        parts.append(artist)

    query = " ".join(parts).strip()
    if not query:
        return None

    params = urllib.parse.urlencode({
        "term": query,
        "media": "music",
        "entity": "song",
        "limit": 1,
    })
    request_url = f"https://itunes.apple.com/search?{params}"

    try:
        req = urllib.request.Request(
            request_url, headers={"User-Agent": "GlideMusic/1.0"}
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = json.load(resp)
    except Exception:
        return None

    results = data.get("results") or []
    if not results:
        return None

    artwork = results[0].get("artworkUrl100")
    if not artwork:
        return None

    # iTunes по умолчанию отдаёт превью 100x100 — подменяем размер
    # в самом URL на более крупный, файл при этом существует на CDN
    return artwork.replace("100x100bb", "600x600bb")


def fetch_image_bytes(url, timeout=_TIMEOUT):
    """
    Скачивает изображение по URL и возвращает его как bytes,
    либо None, если не получилось (нет сети, 404, таймаут и т.п.).
    """
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "GlideMusic/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception:
        return None