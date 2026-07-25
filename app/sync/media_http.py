"""Прямая синхронизация медиа-файлов с AnkiWeb через HTTP.

Обходит Rust-бэкенд ``col.sync_media``, который в нашей среде падает
с ``BackendIOError: Failed to create file in '<col>': File exists``.
Реализует протокол media sync v3 (zstd + JSON) напрямую через HTTP,
по референсу ``_anki_repo/rslib/src/sync/http_server/handlers.rs``
и ``_anki_repo/rslib/src/sync/http_client/protocol.rs``.

Эндпоинты (sync v11, zstd):
- ``POST {endpoint}/msync/begin``         — получить server_usn
- ``POST {endpoint}/msync/mediaChanges`` — список изменений (camelCase JSON)
- ``POST {endpoint}/msync/downloadFiles`` — скачать zip с файлами

Заголовок: ``Anki-Sync: {"v": 11, "k": <hostkey>, "c": <client_ver>, "s": <session>}``
Body: zstd-сжатый JSON.
Ответ: zstd-сжатый, с заголовком ``Anki-Original-Size``.

Важно: на все запросы в рамках одной ``sync_media_direct`` сессии
используется **один и тот же** ``session_key`` — AnkiWeb использует его
для отслеживания сессии.
"""

from __future__ import annotations

import json
import logging
import random
import string
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any

import zstandard as zstd

logger = logging.getLogger(__name__)

DEFAULT_ENDPOINT = "https://sync.ankiweb.net/"
SYNC_VERSION = 11
ORIGINAL_SIZE_HEADER = "anki-original-size"
SYNC_HEADER_NAME = "anki-sync"
USER_AGENT = "kindlanki/0.1"

# Расширения изображений, которые имеет смысл показывать на e-ink Kindle.
# Аудио, видео, шрифты и прочее отфильтровываем — они не отображаются
# на Kindle и только занимают место на диске.
IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {".jpg", ".jpeg", ".png", ".gif", ".webp"}
)


def _endpoint(value: str | None) -> str:
    """Возвращает sync-endpoint (``sync20.ankiweb.net`` и т.п.)."""

    return (value or DEFAULT_ENDPOINT).rstrip("/") + "/"


def _compress(data: bytes) -> bytes:
    """Сжимает ``data`` через zstd (формат, совместимый с сервером AnkiWeb)."""

    cctx = zstd.ZstdCompressor()
    return cctx.compress(data)


def _decompress(data: bytes) -> bytes:
    """Распаковывает zstd-данные от сервера AnkiWeb."""

    dctx = zstd.ZstdDecompressor()
    return dctx.decompress(data, max_output_size=512 * 1024 * 1024)


def _make_session_key() -> str:
    """Генерирует псевдо-случайный session_key (формат как у AnkiDroid).

    AnkiDroid использует ``rand::random::<u32>`` плюс base-N кодирование
    (см. ``_anki_repo/rslib/src/sync/http_client/mod.rs:109-113``).
    Здесь — простой аналог: 16 случайных ASCII-символов.
    """

    alphabet = string.ascii_letters + string.digits
    return "".join(random.choice(alphabet) for _ in range(16))


def _post_json(
    endpoint: str,
    method: str,
    host_key: str,
    payload: dict | list,
    session_key: str,
) -> bytes:
    """POST ``method`` на ``endpoint`` с zstd-сжатым JSON-payload.

    Args:
        endpoint: базовый URL (например, ``https://sync20.ankiweb.net/``).
        method: имя метода (``begin``, ``mediaChanges``, ``downloadFiles``).
        host_key: hostKey пользователя.
        payload: данные, сериализуемые в JSON.
        session_key: единый session_key на всю media-sync сессию.

    Returns:
        Сырое (zstd-сжатое) тело ответа. Используйте ``_decode_response``
        для распаковки и проверки ошибок.

    Raises:
        SyncHttpError: при сетевой ошибке или HTTP 4xx/5xx.
    """

    # Media sync живёт на ``/msync/*`` (см.
    # ``_anki_repo/rslib/src/sync/http_server/mod.rs:248-249``),
    # коллекция — на ``/sync/*``.
    url = urllib.parse.urljoin(endpoint, f"msync/{method}")
    body = _compress(json.dumps(payload).encode("utf-8"))
    header = {
        "v": SYNC_VERSION,
        "k": host_key,
        "c": USER_AGENT,
        "s": session_key,
    }
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/octet-stream",
            SYNC_HEADER_NAME: json.dumps(header),
            "User-Agent": USER_AGENT,
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        body_bytes = exc.read()[:500]
        try:
            body_text = body_bytes.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            body_text = repr(body_bytes)
        logger.warning(
            "AnkiWeb %s failed: status=%d url=%s body=%s headers=%s",
            method,
            exc.code,
            url,
            body_text,
            dict(exc.headers.items()) if exc.headers else None,
        )
        raise SyncHttpError(
            f"AnkiWeb returned {exc.code} for {method}: {body_text or exc.reason}"
        ) from exc
    except urllib.error.URLError as exc:
        raise SyncHttpError(f"Network error during {method}: {exc.reason}") from exc


def _decode_response(raw: bytes) -> dict | list | bytes:
    """Декодирует zstd-ответ от сервера, проверяет обёртку ``JsonResult``.

    Media-sync (``/msync/*``) оборачивает JSON-ответы в ``JsonResult`` —
    ``untagged`` enum: ``{"data": <T>, "err": ""}`` (Ok) или
    ``{"err": "..."}`` (Err). ``downloadFiles`` возвращает сырой zip.

    Args:
        raw: zstd-сжатое тело ответа.

    Returns:
        Декодированный JSON-объект (для media sync) или сырые байты
        (для downloadFiles).
    """

    try:
        decompressed = _decompress(raw)
    except zstd.ZstdError:
        # Не zstd — возможно, уже распаковано или не zstd вообще.
        return raw

    try:
        wrapper = json.loads(decompressed)
    except json.JSONDecodeError:
        # Не JSON — это zip (downloadFiles) или другой бинарь.
        return raw

    # ``JsonResult`` (см. ``_anki_repo/rslib/src/sync/media/protocol.rs:71-80``):
    #   - Ok:   ``{"data": <T>, "err": ""}``
    #   - Err:  ``{"err": "..."}``
    if isinstance(wrapper, dict):
        if "err" in wrapper and isinstance(wrapper["err"], str) and wrapper["err"]:
            raise SyncHttpError(f"AnkiWeb sync error: {wrapper['err']}")
        if "data" in wrapper:
            return wrapper["data"]
    return wrapper


class SyncHttpError(RuntimeError):
    """Сбой HTTP-запроса к AnkiWeb sync-эндпоинту."""


def _media_dir(data_dir: Path) -> Path:
    """Возвращает путь к директории медиа-файлов коллекции."""

    return data_dir / "collection.media"


def _is_image(fname: str) -> bool:
    """True, если ``fname`` имеет расширение изображения.

    Проверка case-insensitive; смотрим на **последнее** расширение,
    чтобы ``foo.tar.gz`` отсеять как ``.gz`` (не изображение), а
    ``image.JPG.bak`` — как ``.bak``. AnkiWeb всегда хранит имена в NFC
    и с одним расширением, так что в реальности это простая проверка.
    """

    suffix = Path(fname).suffix.lower()
    return suffix in IMAGE_EXTENSIONS


def _extract_zip(zip_bytes: bytes, target_dir: Path) -> list[str]:
    """Распаковывает zip с медиа в ``target_dir``.

    Формат zip (см. ``_anki_repo/rslib/src/sync/media/zip.rs:29-48``):
    - файлы названы по индексу в виде строки: ``"0"``, ``"1"``, ...;
    - в zip есть ``_meta`` — JSON-словарь ``{idx_str: real_name}``,
      где ``real_name`` — настоящее имя файла.

    Args:
        zip_bytes: содержимое zip от сервера.
        target_dir: куда распаковать (например, ``/data/collection.media``).

    Returns:
        Список распакованных имён файлов.
    """

    target_dir.mkdir(parents=True, exist_ok=True)
    extracted: list[str] = []

    with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
        meta: dict[str, str] = {}
        if "_meta" in zf.namelist():
            with zf.open("_meta") as f:
                meta = json.loads(f.read().decode("utf-8"))

        for info in zf.infolist():
            if info.filename == "_meta" or info.is_dir():
                continue
            real_name = meta.get(info.filename, info.filename)
            # Sanitize: не даём уйти из target_dir через «..».
            if ".." in Path(real_name).parts:
                logger.warning("Skipping suspicious media filename: %s", real_name)
                continue
            target = target_dir / real_name
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as dst:
                dst.write(src.read())
            extracted.append(real_name)

    return extracted


def sync_media_direct(
    *,
    host_key: str,
    endpoint: str | None,
    data_dir: Path,
    last_usn_path: Path,
    batch_limit: int = 25,
    image_only: bool = True,
    progress_callback=None,
) -> dict:
    """Качает media-файлы с AnkiWeb и сохраняет в ``data_dir/collection.media``.

    Args:
        host_key: валидный hostKey пользователя.
        endpoint: URL sync-сервера (``sync20.ankiweb.net`` и т.п.) или
            ``None`` для default.
        data_dir: каталог с ``collection.anki21``; сюда же пишется
            ``collection.media/``.
        last_usn_path: путь к файлу, где хранится последний
            обработанный ``server_usn`` (используется для incremental
            sync на следующий раз).
        batch_limit: макс. файлов за один ``downloadFiles``.
        image_only: если True (по умолчанию), скачивать только файлы с
            расширениями ``.jpg``, ``.jpeg``, ``.png``, ``.gif``, ``.webp``.
            Аудио, видео, шрифты и прочее пропускаются — они не
            отображаются на e-ink Kindle.
        progress_callback: опциональный колбэк ``(phase, current, total,
            downloaded) -> None``. ``phase`` — ``"mediaChanges"`` или
            ``"downloadFiles"``; ``current``/``total`` — прогресс в
            текущей фазе; ``downloaded`` — сколько файлов уже скачано.
            Используется для отображения прогресс-бара в UI.

    Returns:
        Словарь с результатом: ``{"downloaded": N, "total": N,
        "skipped": N, "last_usn": N, "endpoint": "..."}``.
    """

    base = _endpoint(endpoint)
    media_dir = _media_dir(data_dir)
    # Один session_key на всю сессию — AnkiWeb отслеживает состояние
    # по нему. См. ``_anki_repo/rslib/src/sync/http_client/mod.rs:41``.
    session_key = _make_session_key()

    logger.info("Media sync (direct HTTP): endpoint=%s", base)

    # 1. begin
    raw = _post_json(base, "begin", host_key, {"v": USER_AGENT}, session_key)
    begin = _decode_response(raw)
    if not isinstance(begin, dict):
        raise SyncHttpError(f"Unexpected begin response: {begin!r}")
    server_usn = int(begin["usn"])
    logger.info("Media sync: server_usn=%s", server_usn)

    # 2. mediaChanges (incremental). Сервер возвращает до 1000 файлов за
    #    раз, начиная с ``usn > after_usn`` (см.
    #    ``_anki_repo/rslib/src/sync/media/database/server/entry/changes.sql``).
    #    ``MediaChange`` сериализуется через ``#[derive(Serialize_tuple)]`` —
    #    это массив ``[fname, usn, sha1]``, а не объект.
    last_usn = 0
    if last_usn_path.exists():
        try:
            last_usn = int(last_usn_path.read_text().strip())
        except ValueError:
            last_usn = 0

    all_files: list[tuple[str, str]] = []  # (fname, sha1)
    skipped_non_image = 0
    while True:
        raw = _post_json(
            base, "mediaChanges", host_key, {"lastUsn": last_usn}, session_key
        )
        changes = _decode_response(raw)
        if not isinstance(changes, list):
            raise SyncHttpError(f"Unexpected mediaChanges response: {changes!r}")
        if not changes:
            break
        for c in changes:
            if not isinstance(c, list) or len(c) < 3:
                logger.warning("Skipping malformed media change entry: %r", c)
                continue
            fname, _entry_usn, sha1 = c[0], c[1], c[2]
            if not sha1:
                continue
            if image_only and not _is_image(fname):
                skipped_non_image += 1
                continue
            all_files.append((fname, sha1))
        # ``usn`` последней записи — это next ``last_usn`` для пагинации.
        last_usn = int(changes[-1][1])
        logger.info(
            "Media sync: %d entries in this batch, next last_usn=%d (total so far: %d)",
            len(changes),
            last_usn,
            len(all_files),
        )
        if progress_callback is not None:
            # На этом этапе ``total`` ещё неизвестен (будет = ``len(all_files)``
            # после последнего батча). Сообщаем прогресс относительно
            # уже увиденного; UI прибавит индикатор "неопределено".
            try:
                progress_callback(
                    "mediaChanges",
                    int(last_usn),
                    max(int(last_usn), int(server_usn)),
                    0,
                )
            except Exception:  # noqa: BLE001
                logger.exception("progress_callback raised during mediaChanges")
        if len(changes) < 1000:
            # Меньше лимита — последний батч.
            break

    # Полное количество файлов теперь известно — сообщаем финальное
    # значение ``total`` для фазы downloadFiles.
    if progress_callback is not None:
        try:
            progress_callback(
                "mediaChanges",
                int(server_usn),
                int(server_usn),
                0,
            )
        except Exception:  # noqa: BLE001
            logger.exception("progress_callback raised at mediaChanges end")

    # 3. downloadFiles — пачками по batch_limit. Сервер возвращает zip
    #    с распакованными файлами.
    downloaded: list[str] = []
    total_files = len(all_files)
    for i in range(0, total_files, batch_limit):
        batch = [f for f, _ in all_files[i : i + batch_limit]]
        if not batch:
            continue
        logger.info(
            "Downloading media batch %d-%d/%d (sample: %r)",
            i,
            i + len(batch),
            len(all_files),
            batch[:3],
        )
        # Логируем полный payload первого запроса для отладки 400.
        if i == 0:
            try:
                debug_payload = json.dumps({"files": batch})[:500]
                logger.debug("downloadFiles payload: %s", debug_payload)
            except Exception:  # noqa: BLE001
                pass
        raw = _post_json(
            base, "downloadFiles", host_key, {"files": batch}, session_key
        )
        # Сервер оборачивает zip в zstd, как и остальные ответы.
        if raw[:4] == b"\x28\xb5\x2f\xfd":  # zstd magic number
            zip_bytes = _decompress(raw)
        else:
            zip_bytes = raw
        downloaded.extend(_extract_zip(zip_bytes, media_dir))

        if progress_callback is not None:
            try:
                progress_callback(
                    "downloadFiles",
                    min(i + len(batch), total_files),
                    max(total_files, 1),
                    len(downloaded),
                )
            except Exception:  # noqa: BLE001
                logger.exception("progress_callback raised during downloadFiles")

    # Сохраняем last_usn для следующего incremental sync.
    last_usn_path.parent.mkdir(parents=True, exist_ok=True)
    last_usn_path.write_text(str(server_usn))

    logger.info(
        "Media sync complete: %d files downloaded, %d non-image skipped, last_usn=%s",
        len(downloaded),
        skipped_non_image,
        server_usn,
    )

    return {
        "downloaded": len(downloaded),
        "total": len(all_files),
        "skipped": skipped_non_image,
        "last_usn": server_usn,
        "endpoint": base,
    }

