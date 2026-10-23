"""Telegram group music bot - GitHub Actions edition.

القيم الثابتة (الايديات ونص ستارت) تعدّلها في قسم CONFIGURATION.
التوكن والجلسة تأتي من GitHub Secrets:
    BOT_TOKEN       توكن البوت
    SESSION_STRING  جلسة الحساب المساعد (Telethon StringSession)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import os
import re
import signal
import sys
import time
from html import escape as html_escape
from pathlib import Path
from typing import Any

from pytgcalls import PyTgCalls
from pytgcalls.types import AudioQuality, MediaStream
from telethon import Button, TelegramClient, events
from telethon.errors import FloodWaitError, MessageNotModifiedError
from telethon.sessions import StringSession

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("music-bot")


# ============================== CONFIGURATION ==============================
API_ID = 38720187
API_HASH = "a5c27bc42b391f32db86befcabc68094"

# ايدي كروب الصوت (مثل -1001234567890) وايدي كروب اليوتيوب المساعد.
TARGET_CHAT_ID = -1004476764647
YOUTUBE_CHAT_ID = -1004402681303

# اختياري: رابط الكروب العام. إذا تركته فارغًا يحاول البوت استخراجه.
TARGET_CHAT_LINK = ""
DEVELOPER_URL = ""
# اسم الزر الذي يظهر تحت رسالة (انتهى التشغيل). يفتح رابط الكروب.
END_BUTTON_TEXT = "منار الحق"

# كليشة ستارت الثابتة.
START_TEXT = (
    "أهلًا بك في بوت ميوزك\n"
    "رد على فيديو أو ملف صوتي بكلمة شغل للتشغيل."
)
# أزرار ستارت الثابتة: ("الاسم", "الرابط")
START_BUTTONS: list[tuple[str, str]] = [
    # ("قناة البوت", "https://t.me/example"),
]

MAX_MEDIA_MB = 1024  # أقصى حجم للتحميل (1GB)؛ 0 = بلا حد
# أقصى مدة انتظار لرد بوت اليوتيوب (ثواني) قبل أن يقول لا يوجد بحث.
YOUTUBE_WAIT_SECONDS = 60
# سرعة التحميل بطريقة FastTelethon: اتصالات خام اضافية بنفس مفتاح جلسة البوت
# (بدون تسجيل دخول جديد، فلا يحدث Flood). تُفتح للملفات الكبيرة فقط وتُغلق بعد
# انتهاء التحميل، وعند أي مشكلة يرجع التحميل تلقائيا للاتصال العادي.
FAST_MAX_CONNECTIONS = 8       # أقصى عدد اتصالات اضافية للملف الواحد (0 = تعطيل)
FAST_MB_PER_CONNECTION = 16    # اتصال اضافي لكل 16MB من حجم الملف
# عدد الطلبات المتوازية على اتصال البوت الأساسي.
STREAMS_PER_CONNECTION = 6
# حجم القطعة التي يتقاسمها المحمّلون (ميغابايت).
CHUNK_MB = 8
# أقصى حجم لكاش الملفات المحمّلة؛ الأقدم يُحذف عند التجاوز.
CACHE_MAX_MB = 4096
# مدة التشغيل قبل الإغلاق الذاتي (ثواني) ليعيد الـworkflow تشغيله.
MAX_RUNTIME_SECONDS = int(os.environ.get("MAX_RUNTIME_SECONDS", "20400"))
# ============================================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
SESSION_STRING = os.environ.get("SESSION_STRING", "").strip()

DATA_DIR = Path("data")
NOTICE_PATH = DATA_DIR / "restart_notice.json"
DOWNLOAD_DIR = Path("downloads")
CACHE_DIR = DOWNLOAD_DIR / "cache"
THUMB_DIR = DOWNLOAD_DIR / "thumbs"
for _dir in (DATA_DIR, DOWNLOAD_DIR, CACHE_DIR, THUMB_DIR):
    _dir.mkdir(parents=True, exist_ok=True)

DOWNLOAD_CHUNK_SIZE = 1024 * 1024

PLAY_WORDS = {"شغل", "تشغيل", "play", "/شغل", "/تشغيل", "/play"}
STOP_WORDS = {"ايقاف", "إيقاف", "وقف", "stop", "/ايقاف", "/إيقاف", "/وقف", "/stop"}
SKIP_WORDS = {"تخطي", "التالي", "skip", "/تخطي", "/التالي", "/skip"}
WAIT_WORDS = {
    "الانتظار", "الأنتظار", "الانتضار", "الأنتضار", "انتظار", "انتضار",
    "/الانتظار", "/الأنتظار", "/الانتضار", "/الأنتضار", "/انتظار", "/انتضار",
    "حالة", "حاله", "الحالة", "الحاله",
    "/حالة", "/حاله", "/الحالة", "/الحاله", "status", "/status",
}
CLEAR_QUEUE_WORDS = {
    "مسح حالة", "مسح حاله", "مسح الحالة", "مسح الحاله",
    "/مسح حالة", "/مسح حاله", "/مسح الحالة", "/مسح الحاله",
}
START_WORDS = {"/start", "start"}
RESET_WORDS = {
    "اعادة تشغيل", "إعادة تشغيل", "اعاده تشغيل", "إعاده تشغيل",
    "/اعادة تشغيل", "/إعادة تشغيل", "تحديث", "/تحديث", "ريست", "/ريست",
    "ريستارت", "/ريستارت", "restart", "/restart", "reset", "/reset",
    "update", "/update",
}

MEDIA_EXTENSIONS = {
    ".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg", ".opus", ".wma",
    ".mp4", ".m4v", ".webm", ".mkv", ".avi", ".mov",
}


def clean_command(text: str) -> str:
    parts = text.strip().split(maxsplit=1)
    return parts[0].lower() if parts else ""


def normalized_text(text: str) -> str:
    return " ".join(text.strip().lower().split())


def format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, remaining = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{remaining:02d}"
    return f"{minutes:02d}:{remaining:02d}"


def media_from_message(message: Any) -> Any | None:
    """Return audio/video media, including documents with a generic MIME."""
    for attr in ("video", "audio", "voice"):
        value = getattr(message, attr, None)
        if value:
            return value

    document = getattr(message, "document", None)
    if not document:
        return None

    mime = (getattr(document, "mime_type", "") or "").lower()
    file_obj = getattr(message, "file", None)
    name = (getattr(file_obj, "name", "") or "").lower()
    extension = (getattr(file_obj, "ext", "") or "").lower()
    if (
        mime.startswith("video/")
        or mime.startswith("audio/")
        or Path(name).suffix in MEDIA_EXTENSIONS
        or extension in MEDIA_EXTENSIONS
    ):
        return document
    return None


def media_filename(message: Any) -> str:
    message_file = getattr(message, "file", None)
    name = getattr(message_file, "name", None) or f"media_{message.id}.bin"
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", name)[:120]


def media_title(message: Any) -> str:
    """Readable name: audio tag title, else file name without extension."""
    file_obj = getattr(message, "file", None)
    title = str(getattr(file_obj, "title", "") or "").strip()
    if not title:
        name = str(getattr(file_obj, "name", "") or "").strip()
        title = Path(name).stem if name else ""
        title = re.sub(r"[_]+", " ", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title or "ملف صوتي"


def media_size(media: Any) -> int:
    return int(getattr(media, "size", 0) or getattr(media, "file_size", 0) or 0)


YOUTUBE_ID_RE = re.compile(
    r"(?:youtu\.be/|youtube(?:-nocookie)?\.com/"
    r"(?:watch\?(?:[^\s&]*&)*v=|shorts/|embed/|live/|v/))"
    r"([A-Za-z0-9_-]{11})",
    re.I,
)


def youtube_id_from_message(message: Any) -> str | None:
    """Find a YouTube video id in the helper's reply (text, links, buttons)."""
    texts = [str(getattr(message, "raw_text", "") or "")]
    with contextlib.suppress(Exception):
        for entity, text in message.get_entities_text() or []:
            texts.append(str(getattr(entity, "url", "") or text or ""))
    with contextlib.suppress(Exception):
        for row in message.buttons or []:
            for button in row:
                texts.append(str(getattr(button, "url", "") or ""))
    for text in texts:
        match = YOUTUBE_ID_RE.search(text)
        if match:
            return match.group(1)
    return None


def fetch_url_bytes(url: str, timeout: float = 8.0, limit: int = 5_000_000) -> bytes | None:
    """Blocking GET of a small image (call it through asyncio.to_thread)."""
    import urllib.request

    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                return None
            if not response.headers.get_content_type().startswith("image/"):
                return None
            data = response.read(limit)
    except Exception:
        return None
    return data if len(data) > 2048 else None  # tiny files are placeholders


def control_buttons() -> list[list[Any]]:
    return [
        [Button.inline("الانتظار", b"music_status")],
        [
            Button.inline("إيقاف", b"music_stop"),
            Button.inline("تخطي", b"music_skip"),
        ],
    ]


def start_buttons() -> list[list[Any]] | None:
    rows: list[list[Any]] = []
    row: list[Any] = []
    for label, url in START_BUTTONS:
        row.append(Button.url(label[:64], url))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    if DEVELOPER_URL:
        rows.append([Button.url("مطور البوت", DEVELOPER_URL)])
    return rows or None


def progress_bar(fraction: float, width: int = 12) -> str:
    filled = int(max(0.0, min(1.0, fraction)) * width)
    return "▰" * filled + "▱" * (width - filled)


async def report_progress(
    progress: Any, state: dict[str, Any], total: int, label: str
) -> None:
    """Edit the progress message every ~4s: percent, size, speed, time left."""
    started = time.monotonic()
    last_text = ""
    samples: list[tuple[float, int]] = [(started, 0)]
    while True:
        await asyncio.sleep(4)
        now = time.monotonic()
        done = state["done"]
        samples.append((now, done))
        del samples[:-3]  # keep the last ~12 seconds
        t0, d0 = samples[0]
        speed = (done - d0) / max(now - t0, 0.001)
        done_mb = done / 1048576
        if total:
            fraction = done / total
            remaining = (total - done) / speed if speed > 0 else 0
            text = (
                f"{label}\n"
                f"{progress_bar(fraction)} {fraction * 100:.0f}%\n"
                f"الحجم : {done_mb:.1f} / {total / 1048576:.1f} MB\n"
                f"السرعة : {speed / 1048576:.1f} MB/s\n"
                f"المتبقي : {format_duration(remaining)}"
            )
        else:
            text = (
                f"{label}\n"
                f"تم تحميل : {done_mb:.1f} MB\n"
                f"السرعة : {speed / 1048576:.1f} MB/s"
            )
        if text != last_text:
            with contextlib.suppress(Exception):
                await progress.edit(text)
            last_text = text


@contextlib.asynccontextmanager
async def download_progress(
    progress: Any, total: int, label: str = "جار التحميل"
):
    """Yield a counter dict; while open, the progress message is updated."""
    state: dict[str, Any] = {"done": 0}
    task = (
        asyncio.create_task(report_progress(progress, state, total, label))
        if progress is not None
        else None
    )
    try:
        yield state
    finally:
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task


class RawSender:
    """Extra raw MTProto connection to the file's data center.

    Same idea as FastTelethon: it reuses the bot's auth key (file on the same
    DC) or one export/import of the authorization (file on another DC). It
    never logs the bot in again, so it cannot cause ImportBotAuthorization
    flood waits.
    """

    streams = 1  # one request in flight per raw connection

    def __init__(self, client: Any, sender: Any, location: Any) -> None:
        self.client = client
        self.sender = sender
        self.location = location

    async def read(self, offset: int, limit: int) -> bytes:
        from telethon.tl.functions.upload import GetFileRequest

        result = await self.client._call(
            self.sender,
            GetFileRequest(self.location, offset=offset, limit=limit),
        )
        return result.bytes

    async def disconnect(self) -> None:
        await self.sender.disconnect()


async def open_raw_senders(
    client: Any, message: Any, count: int, opened: list[RawSender]
) -> None:
    """Open `count` raw connections for the media of `message`.

    Every sender is appended to `opened` as soon as it exists, so the caller
    can always close them, even when this function fails half way.
    """
    from telethon import utils
    from telethon.network import MTProtoSender
    from telethon.tl.alltlobjects import LAYER
    from telethon.tl.functions import InvokeWithLayerRequest
    from telethon.tl.functions.auth import (
        ExportAuthorizationRequest,
        ImportAuthorizationRequest,
    )

    dc_id, location = utils.get_input_location(message.media)
    dc_id = dc_id or client.session.dc_id
    auth_key = client.session.auth_key if client.session.dc_id == dc_id else None
    dc = await client._get_dc(dc_id)

    async def make() -> None:
        nonlocal auth_key
        sender = MTProtoSender(auth_key, loggers=client._log)
        opened.append(RawSender(client, sender, location))
        await sender.connect(
            client._connection(
                dc.ip_address,
                dc.port,
                dc.id,
                loggers=client._log,
                proxy=client._proxy,
                local_addr=getattr(client, "_local_addr", None),
            )
        )
        if not auth_key:
            auth = await client(ExportAuthorizationRequest(dc_id))
            client._init_request.query = ImportAuthorizationRequest(
                id=auth.id, bytes=auth.bytes
            )
            await sender.send(InvokeWithLayerRequest(LAYER, client._init_request))
            auth_key = sender.auth_key

    # For another DC the first sender exports the authorization once; the
    # others are created afterwards and simply reuse its key.
    await make()
    rest = [asyncio.create_task(make()) for _ in range(count - 1)]
    try:
        await asyncio.gather(*rest)
    except BaseException:
        for task in rest:
            task.cancel()
        await asyncio.gather(*rest, return_exceptions=True)
        raise


async def close_raw_senders(senders: list[RawSender]) -> None:
    if senders:
        await asyncio.gather(
            *(asyncio.wait_for(s.disconnect(), 5) for s in senders),
            return_exceptions=True,
        )
        senders.clear()


async def parallel_download(
    clients: list[Any],
    message: Any,
    destination: Path,
    size: int,
    state: dict[str, Any] | None = None,
    streams: int = 3,
) -> None:
    """Download media in chunks shared by many connections (work stealing).

    Every client is a separate connection to Telegram; each runs `streams`
    workers that keep taking the next chunk, so faster connections simply
    do more of the work. A failed chunk is retried, possibly by another
    connection.
    """
    destination.unlink(missing_ok=True)
    media = message.media

    if size <= 0:
        # Unknown size: a single stream.
        with destination.open("wb") as output:
            async for chunk in clients[0].iter_download(
                media, request_size=DOWNLOAD_CHUNK_SIZE
            ):
                output.write(chunk)
                if state is not None:
                    state["done"] += len(chunk)
        return

    with destination.open("wb") as preallocate:
        preallocate.truncate(size)

    chunk_bytes = CHUNK_MB * 1024 * 1024
    work: asyncio.Queue[tuple[int, int, int]] = asyncio.Queue()
    for offset in range(0, size, chunk_bytes):
        work.put_nowait((offset, min(chunk_bytes, size - offset), 0))

    total_chunks = work.qsize()
    finished = [0]  # chunks fully written; used to detect holes in the file

    fd = os.open(destination, os.O_WRONLY)

    async def fetch(
        client: Any, offset: int, length: int, counter: list[int]
    ) -> None:
        got = 0
        if isinstance(client, RawSender):
            # Offsets stay 1 MB aligned, as upload.getFile requires.
            while got < length:
                data = await client.read(offset + got, DOWNLOAD_CHUNK_SIZE)
                if not data:
                    break
                if len(data) < min(DOWNLOAD_CHUNK_SIZE, length - got):
                    raise RuntimeError("قراءة ناقصة من الخادم.")
                data = data[: length - got]
                os.pwrite(fd, data, offset + got)
                got += len(data)
                counter[0] += len(data)
                if state is not None:
                    state["done"] += len(data)
        else:
            # Telethon's limit counts requests, not bytes.
            async for data in client.iter_download(
                media,
                offset=offset,
                limit=math.ceil(length / DOWNLOAD_CHUNK_SIZE),
                request_size=DOWNLOAD_CHUNK_SIZE,
            ):
                data = data[: length - got]
                os.pwrite(fd, data, offset + got)
                got += len(data)
                counter[0] += len(data)
                if state is not None:
                    state["done"] += len(data)
                if got >= length:
                    break
        if got != length:
            raise RuntimeError(f"تحميل ناقص ({got}/{length} بايت).")

    async def worker(client: Any) -> None:
        failures = 0
        while True:
            try:
                offset, length, attempt = work.get_nowait()
            except asyncio.QueueEmpty:
                return
            counter = [0]
            try:
                await fetch(client, offset, length, counter)
                finished[0] += 1
                failures = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if state is not None:
                    state["done"] -= counter[0]
                failures += 1
                retire = client is not clients[0] and failures >= 2
                if retire:
                    # A broken extra connection stops; others finish the job.
                    work.put_nowait((offset, length, attempt))
                    log.warning("Download connection retired: %s", exc)
                    return
                if attempt >= 3:
                    raise
                log.warning("Chunk %s failed (%s); retrying", offset, exc)
                work.put_nowait((offset, length, attempt + 1))
                await asyncio.sleep(1)

    tasks = [
        asyncio.create_task(worker(client))
        for client in clients
        for _ in range(getattr(client, "streams", streams))
    ]
    try:
        await asyncio.gather(*tasks)
        # A retired connection puts its chunk back in the queue; if every other
        # worker had already finished, nobody would pick it up and the file
        # would silently keep a hole of zeros. Fail instead (caller falls back).
        if finished[0] != total_chunks:
            raise RuntimeError(f"تحميل ناقص ({finished[0]}/{total_chunks} قطعة).")
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    finally:
        os.close(fd)


class FatalError(RuntimeError):
    """Configuration/session problem: restarting will not help."""


# Session of the bot account, kept in memory so that in-process restarts
# do not log the bot in again (each login counts against a flood limit).
_BOT_SESSION = ""


class GroupMusicBot:
    def __init__(self, stop_event: asyncio.Event) -> None:
        # connection_retries=-1: reconnect forever instead of giving up.
        self.bot = TelegramClient(
            StringSession(_BOT_SESSION),
            API_ID,
            API_HASH,
            flood_sleep_threshold=60,
            connection_retries=-1,
            retry_delay=3,
        )
        self.user = TelegramClient(
            StringSession(SESSION_STRING),
            API_ID,
            API_HASH,
            flood_sleep_threshold=60,
            connection_retries=-1,
            retry_delay=3,
        )
        # Consecutive failures of the fast (raw connections) download path;
        # after 3 it is switched off for the rest of the run.
        self.fast_failures = 0
        # Media forwarded from the assistant account to the bot, so the bot's
        # fast download can fetch files found by the assistant account.
        self.forward_waiters: dict[int, asyncio.Future[Any]] = {}
        self.bot_username: str = ""
        self.user_id: int = 0
        self.background: list[asyncio.Task[None]] = []
        self.calls: PyTgCalls | None = None
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.player_task: asyncio.Task[None] | None = None
        self.current: dict[str, Any] | None = None
        self.advance_event = asyncio.Event()
        self.leave_when_empty = False
        self.download_locks: dict[str, asyncio.Lock] = {}
        self.youtube_cache: dict[str, tuple[Path, str, Path | None]] = {}
        self.target_chat_link: str | None = None
        self.target_chat_photo_path = DATA_DIR / "target_chat_photo.jpg"
        self.stop_event = stop_event
        self.restart_event = asyncio.Event()
        self.enqueue_lock = asyncio.Lock()
        self.youtube_lock = asyncio.Lock()

    # ------------------------------------------------------------ helpers
    async def get_target_chat_link(self) -> str | None:
        if TARGET_CHAT_LINK.strip():
            return TARGET_CHAT_LINK.strip()
        if self.target_chat_link is not None:
            return self.target_chat_link or None

        self.target_chat_link = ""
        try:
            entity = await self.bot.get_entity(TARGET_CHAT_ID)
            username = str(getattr(entity, "username", "") or "").strip()
            if username:
                self.target_chat_link = f"https://t.me/{username}"
            else:
                raw_id = str(abs(TARGET_CHAT_ID))
                if raw_id.startswith("100"):
                    self.target_chat_link = f"https://t.me/c/{raw_id[3:]}/1"
        except Exception:
            log.exception("Could not resolve the voice group link")
        return self.target_chat_link or None

    async def get_target_chat_photo(self) -> Path | None:
        path = self.target_chat_photo_path
        if path.exists() and path.stat().st_size > 0:
            return path
        try:
            downloaded = await self.bot.download_profile_photo(
                TARGET_CHAT_ID, file=str(path)
            )
            candidate = Path(downloaded) if downloaded else path
            if candidate.exists() and candidate.stat().st_size > 0:
                if candidate != path:
                    candidate.replace(path)
                return path
        except Exception:
            log.exception("Could not download the voice group photo")
        return None

    async def requester_info(self, event: Any) -> tuple[int | None, str]:
        name = "المشرف"
        try:
            sender = await event.get_sender()
            parts = [
                str(getattr(sender, "first_name", "") or "").strip(),
                str(getattr(sender, "last_name", "") or "").strip(),
            ]
            name = " ".join(p for p in parts if p) or str(
                getattr(sender, "username", "") or ""
            ).strip()
        except Exception:
            log.exception("Could not resolve the playback requester")
        return getattr(event, "sender_id", None), name or "المشرف"

    async def playback_text(
        self,
        track: dict[str, Any],
        state: str,
        duration: float | None = None,
        ended: bool = False,
        queued_count: int | None = None,
    ) -> str:
        if duration is None:
            duration = track.get("duration")
        group_link = await self.get_target_chat_link()
        title = html_escape(str(track.get("title") or "ملف صوتي"))
        duration_value = html_escape(
            format_duration(duration) if duration is not None else "جارٍ الحساب..."
        )
        if group_link:
            safe_link = html_escape(group_link, quote=True)
            title = f'<a href="{safe_link}">{title}</a>'
            duration_value = f'<a href="{safe_link}">{duration_value}</a>'
        requester_name = html_escape(str(track.get("requester_name") or "المشرف"))
        requester_id = track.get("requester_id")
        requester_value = (
            f'<a href="tg://user?id={int(requester_id)}">{requester_name}</a>'
            if requester_id
            else requester_name
        )
        header = "<blockquote>▷ ꧁مَنَارُ الحَقّ꧂ </blockquote>\n"
        if queued_count is not None:
            return (
                header
                + f"تمت الإضافة إلى قائمة التشغيل : {title}\n"
                f"عدد الأصوات في الانتظار: {queued_count}\n"
                f"مدته : {duration_value}\n"
                f"بواسطة : {requester_value}"
            )
        state_html = (
            f"<blockquote>{html_escape(state)}</blockquote>"
            if ended
            else html_escape(state)
        )
        return (
            header
            + f"{state_html}\n"
            f"العنوان : {title}\n"
            f"المدة : {duration_value}\n"
            f"بواسطة : {requester_value}"
        )

    async def is_admin(self, user_id: int | None) -> bool:
        if not user_id:
            return False
        try:
            permissions = await self.bot.get_permissions(TARGET_CHAT_ID, user_id)
            return bool(permissions.is_admin or permissions.is_creator)
        except Exception:
            log.exception("Could not check group admin status")
            return False

    # ----------------------------------------------------------- download
    async def download_media(self, message: Any, progress: Any = None) -> Path:
        media = media_from_message(message)
        if media is None:
            raise ValueError("الرسالة لا تحتوي على ملف صوتي أو فيديو.")

        size = media_size(media)
        if MAX_MEDIA_MB and size > MAX_MEDIA_MB * 1024 * 1024:
            raise ValueError("مساحة الفيديو أعلى من الحجم المتوقع.")

        cache_key = str(getattr(media, "id", None) or message.id)
        suffix = Path(media_filename(message)).suffix or ".media"
        cached_path = CACHE_DIR / f"{cache_key}{suffix}"
        lock = self.download_locks.setdefault(cache_key, asyncio.Lock())

        async with lock:
            # Same file requested again during this run: play from cache.
            if cached_path.exists() and cached_path.stat().st_size > 0:
                log.info("Using cached media %s", cached_path)
                return cached_path

            started = time.monotonic()
            temporary = DOWNLOAD_DIR / f".{cache_key}.part"
            try:
                async with download_progress(progress, size) as state:
                    await self.transfer(message, temporary, size, state)
                temporary.replace(cached_path)
                self.trim_cache(keep=cached_path)
                log.info(
                    "Downloaded %s (%.1f MB) in %.2fs",
                    cache_key,
                    size / 1024 / 1024,
                    time.monotonic() - started,
                )
                return cached_path
            finally:
                temporary.unlink(missing_ok=True)

    async def transfer(
        self, message: Any, destination: Path, size: int, state: dict[str, Any]
    ) -> None:
        """Download `message` fast.

        Big files get extra raw connections that reuse the bot's auth key (no
        new login). On any problem it falls back to the plain bot connection,
        and after 3 failures in a row the fast path is switched off.
        """
        opened: list[RawSender] = []
        want = 0
        if FAST_MAX_CONNECTIONS and self.fast_failures < 3 and size > 0:
            want = min(
                FAST_MAX_CONNECTIONS, size // (FAST_MB_PER_CONNECTION * 1024 * 1024)
            )
        try:
            if want >= 1:
                try:
                    await asyncio.wait_for(
                        open_raw_senders(self.bot, message, want, opened), 30
                    )
                    log.info("Fast download: %s extra connections", len(opened))
                except Exception as exc:
                    self.fast_failures += 1
                    log.warning(
                        "Extra connections unavailable (%s); using the main one", exc
                    )
                    await close_raw_senders(opened)
            try:
                await parallel_download(
                    [self.bot, *opened],
                    message,
                    destination,
                    size,
                    state,
                    STREAMS_PER_CONNECTION,
                )
                if opened:
                    self.fast_failures = 0
            except Exception:
                if not opened:
                    raise
                self.fast_failures += 1
                log.exception("Fast download failed; retrying on the main connection")
                state["done"] = 0
                await parallel_download([self.bot], message, destination, size, state, 4)
        finally:
            await close_raw_senders(opened)

    def trim_cache(self, keep: Path) -> None:
        """Delete the oldest cached files when the cache gets too large."""
        try:
            limit = CACHE_MAX_MB * 1024 * 1024
            files = [f for f in CACHE_DIR.iterdir() if f.is_file()]
            total = sum(f.stat().st_size for f in files)
            if total <= limit:
                return
            in_use = {Path(t["path"]) for t in list(self.queue._queue)}
            if self.current:
                in_use.add(Path(self.current["path"]))
            in_use.add(keep)
            for file in sorted(files, key=lambda f: f.stat().st_mtime):
                if total <= limit:
                    break
                if file in in_use:
                    continue
                size = file.stat().st_size
                file.unlink(missing_ok=True)
                total -= size
        except Exception:
            log.exception("Cache trim failed")

    async def watchdog(self) -> None:
        """Make sure the Telegram connections stay up."""
        while True:
            await asyncio.sleep(30)
            for client in (self.bot, self.user):
                if not client.is_connected():
                    log.warning("Connection lost; reconnecting")
                    with contextlib.suppress(Exception):
                        await client.connect()

    async def media_duration(self, path: Path) -> float:
        try:
            process = await asyncio.create_subprocess_exec(
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            output, _ = await asyncio.wait_for(process.communicate(), 20)
        except (OSError, asyncio.TimeoutError):
            return 3600.0
        try:
            return max(1.0, float(output.decode().strip()))
        except (ValueError, UnicodeDecodeError):
            return 3600.0

    async def forward_to_bot(self, response: Any) -> tuple[Any, Any] | None:
        """Forward the helper's media to the bot (server-side, instant).

        The bot then holds its own reference to the file and can download it
        over all of its connections, which is much faster than the assistant
        account's single connection. Returns (bot_message, forwarded) or None
        (for example when the helper's messages cannot be forwarded).
        """
        media = media_from_message(response)
        doc_id = getattr(media, "id", None)
        if not doc_id or not self.bot_username:
            return None
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self.forward_waiters[doc_id] = future
        try:
            forwarded = await self.user.forward_messages(
                self.bot_username, response
            )
            bot_message = await asyncio.wait_for(future, 10)
            return bot_message, forwarded
        except Exception as exc:
            log.warning("Fast path unavailable (%s); using the account", exc)
            return None
        finally:
            self.forward_waiters.pop(doc_id, None)

    async def youtube_thumbnail(self, response: Any, cache_key: str) -> Path | None:
        """Picture for the playback card.

        The YouTube thumbnail when the helper's reply contains the video link,
        otherwise the cover embedded in the audio file. None when neither exists.
        """
        path = THUMB_DIR / f"{cache_key}.jpg"
        if path.exists() and path.stat().st_size > 0:
            return path
        try:
            video_id = youtube_id_from_message(response)
            if video_id:
                for name in ("maxresdefault", "hq720", "hqdefault"):
                    data = await asyncio.to_thread(
                        fetch_url_bytes,
                        f"https://i.ytimg.com/vi/{video_id}/{name}.jpg",
                    )
                    if data:
                        path.write_bytes(data)
                        log.info("Thumbnail from YouTube: %s (%s)", video_id, name)
                        return path
            saved = await self.user.download_media(response, file=str(path), thumb=-1)
            if saved and Path(saved).exists() and Path(saved).stat().st_size > 0:
                log.info("Thumbnail taken from the audio file")
                return Path(saved)
            log.info("No thumbnail found for this result")
        except Exception:
            log.exception("Could not get a thumbnail")
        return None

    async def download_youtube_audio(
        self, response: Any, query: str, progress: Any = None
    ) -> tuple[Path, str, Path | None] | None:
        media = media_from_message(response)
        if media is None:
            return None

        cache_key = str(getattr(media, "id", None) or response.id)
        original_name = media_filename(response)
        suffix = Path(original_name).suffix or ".media"
        cached_path = CACHE_DIR / f"{cache_key}{suffix}"
        lock = self.download_locks.setdefault(cache_key, asyncio.Lock())

        async with lock:
            title = media_title(response)
            if title == "ملف صوتي":
                title = query
            if cached_path.exists() and cached_path.stat().st_size > 0:
                photo = None
                with contextlib.suppress(Exception):
                    photo = await asyncio.wait_for(
                        self.youtube_thumbnail(response, cache_key), 15
                    )
                return cached_path, title, photo

            temporary = DOWNLOAD_DIR / f".youtube_{cache_key}.part"
            fast = None
            # The picture is fetched while the audio downloads.
            thumb_task = asyncio.create_task(
                self.youtube_thumbnail(response, cache_key)
            )
            try:
                total = media_size(media)
                fast = await self.forward_to_bot(response)
                async with download_progress(progress, total) as state:
                    done = False
                    if fast is not None:
                        try:
                            await self.transfer(fast[0], temporary, total, state)
                            done = True
                        except Exception:
                            log.exception(
                                "Fast download failed; using the account"
                            )
                            state["done"] = 0
                    if not done:
                        await parallel_download(
                            [self.user], response, temporary, total, state, 4
                        )
                if temporary.stat().st_size == 0:
                    return None
                temporary.replace(cached_path)
                self.trim_cache(keep=cached_path)
                photo = None
                with contextlib.suppress(Exception):  # incl. timeout
                    photo = await asyncio.wait_for(thumb_task, 15)
                return cached_path, title, photo
            finally:
                if not thumb_task.done():
                    thumb_task.cancel()
                temporary.unlink(missing_ok=True)
                if fast is not None:
                    # Remove the forwarded copy from the bot's chat.
                    with contextlib.suppress(Exception):
                        await self.user.delete_messages(
                            self.bot_username, [fast[1].id], revoke=True
                        )

    async def wait_for_youtube_audio(self, request: Any) -> Any | None:
        """Wait for the helper's media answering *this* request only."""
        deadline = time.monotonic() + YOUTUBE_WAIT_SECONDS
        while time.monotonic() < deadline:
            async for response in self.user.iter_messages(
                YOUTUBE_CHAT_ID, min_id=request.id, limit=10
            ):
                if response.id <= request.id or response.out:
                    continue
                reply_to = getattr(response, "reply_to_msg_id", None)
                if reply_to and reply_to != request.id:
                    continue  # answer to another request
                if media_from_message(response) is not None:
                    return response
            await asyncio.sleep(3.0)
        return None

    async def request_youtube_audio(
        self, query: str, progress: Any = None
    ) -> tuple[Path, str, Path | None] | None:
        if not YOUTUBE_CHAT_ID:
            log.error("YouTube helper group is not configured")
            return None

        key = normalized_text(query)

        def cached_result() -> tuple[Path, str, Path | None] | None:
            cached = self.youtube_cache.get(key)
            if cached and cached[0].exists() and cached[0].stat().st_size > 0:
                log.info("YouTube query served from cache: %s", key)
                return cached
            return None

        if (hit := cached_result()) is not None:
            return hit

        try:
            # Only one search in the helper group at a time, so answers
            # can never be mixed up between requests.
            async with self.youtube_lock:
                if (hit := cached_result()) is not None:
                    return hit
                request = await self.user.send_message(
                    YOUTUBE_CHAT_ID, f"يوت {query}"
                )
                response = await self.wait_for_youtube_audio(request)
            if response is None:
                return None
            result = await self.download_youtube_audio(response, query, progress)
            if result:
                self.youtube_cache[key] = result
            return result
        except Exception:
            log.exception("YouTube helper request failed")
            return None

    # ------------------------------------------------------ status message
    async def send_playback_status(
        self,
        chat_id: int,
        track: dict[str, Any],
        state: str,
        queued_count: int | None = None,
    ) -> Any:
        text = await self.playback_text(track, state, queued_count=queued_count)
        # A message that was only added to the queue carries no buttons.
        buttons = None if queued_count is not None else control_buttons()
        # The track's own picture (YouTube search) first, then the group's photo.
        photo = track.get("photo")
        if photo:
            with contextlib.suppress(Exception):
                return await self.bot.send_file(
                    chat_id, photo, caption=text,
                    parse_mode="html", buttons=buttons,
                )
        photo = await self.get_target_chat_photo()
        if photo:
            with contextlib.suppress(Exception):
                return await self.bot.send_file(
                    chat_id, photo, caption=text,
                    parse_mode="html", buttons=buttons,
                )
        return await self.bot.send_message(
            chat_id, text, parse_mode="html",
            buttons=buttons, link_preview=False,
        )

    async def end_buttons(self) -> list[list[Any]]:
        url = await self.get_target_chat_link() or DEVELOPER_URL
        return [[Button.url(END_BUTTON_TEXT, url)]] if url else []

    async def edit_status(
        self, status_message: Any, text: str, buttons: Any = None
    ) -> Any:
        if buttons is None:
            buttons = control_buttons()
        try:
            await self.bot.edit_message(
                status_message.chat_id, status_message.id, text,
                parse_mode="html", buttons=buttons, link_preview=False,
            )
            return status_message
        except MessageNotModifiedError:
            return status_message
        except Exception as exc:
            log.warning("Could not edit playback message (%s); resending", exc)
            with contextlib.suppress(Exception):
                return await self.bot.send_message(
                    status_message.chat_id, text, parse_mode="html",
                    buttons=buttons, link_preview=False,
                )
            return status_message

    # -------------------------------------------------------------- player
    async def player_loop(self) -> None:
        while True:
            track = await self.queue.get()
            self.current = track
            status_message = track["status_message"]
            failed = False
            try:
                if self.calls is None:
                    raise RuntimeError("Assistant account is not connected.")
                self.advance_event.clear()
                await self.calls.play(
                    TARGET_CHAT_ID,
                    # No conversion: the file is played as it is. Video files
                    # keep their video, audio files play as audio.
                    MediaStream(
                        str(track["path"]),
                        audio_parameters=AudioQuality.STUDIO,
                        video_flags=MediaStream.Flags.AUTO_DETECT,
                    ),
                )
                duration = track.get("duration") or await self.media_duration(
                    track["path"]
                )
                track["duration"] = duration
                track["status_message"] = await self.edit_status(
                    status_message,
                    await self.playback_text(track, "بدأ التشغيل"),
                )
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(
                        self.advance_event.wait(), timeout=duration + 1
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                failed = True
                log.exception("Could not play %s", track["path"])
                track["status_message"] = await self.edit_status(
                    status_message,
                    "تعذر تشغيل الصوت. تأكد من إضافة الحساب المساعد وفتح المحادثة الصوتية.",
                )
            finally:
                self.current = None
                self.queue.task_done()
                if not failed:
                    # Finished (or skipped): mark the message as ended.
                    with contextlib.suppress(Exception, asyncio.CancelledError):
                        await self.edit_status(
                            track["status_message"],
                            await self.playback_text(
                                track, "• انتهى التشغيل", ended=True
                            ),
                            buttons=await self.end_buttons(),
                        )
                # Nothing left: leave the stage instead of staying in it.
                if self.queue.empty():
                    self.leave_when_empty = False
                    if self.calls is not None:
                        with contextlib.suppress(Exception, asyncio.CancelledError):
                            await self.calls.leave_call(TARGET_CHAT_ID)

    async def ensure_player(self) -> None:
        if self.player_task is None or self.player_task.done():
            self.player_task = asyncio.create_task(self.player_loop())

    async def advance(self) -> bool:
        if self.current is None:
            return False
        self.leave_when_empty = self.queue.empty()
        self.advance_event.set()
        return True

    async def status_text(self) -> str:
        waiting = self.queue.qsize()
        if self.current:
            return (
                f"التشغيل الآن:\n{self.current['title']}\n"
                f"قائمة الانتظار: {waiting} ملف"
            )
        return f"لا يوجد تشغيل\nقائمة الانتظار: {waiting} ملف"

    async def clear_waiting(self) -> int:
        removed = 0
        while True:
            try:
                track = self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            self.queue.task_done()
            removed += 1
            with contextlib.suppress(Exception):
                await self.edit_status(
                    track["status_message"],
                    "تم مسح الملف من قائمة الانتظار.",
                    buttons=[],
                )
        return removed

    async def reset_playback(self, progress: Any) -> None:
        """Full in-process restart: everything is closed and rebuilt.

        Rebuilding only the call engine on a live connection can hang, so
        the whole bot (connections + call engine) is restarted instead.
        The queue is dropped; downloaded files stay cached on disk.
        """
        if self.restart_event.is_set():
            return
        log.info("Restart requested from the group")
        with contextlib.suppress(Exception):
            NOTICE_PATH.write_text(
                json.dumps(
                    {
                        "chat_id": progress.chat_id,
                        "message_id": progress.id,
                        "time": time.time(),
                    }
                ),
                encoding="utf-8",
            )
        self.restart_event.set()

    async def announce_restart(self) -> None:
        """After a restart, turn the 'restarting' message into 'done'."""
        try:
            if not NOTICE_PATH.exists():
                return
            notice = json.loads(NOTICE_PATH.read_text(encoding="utf-8"))
            NOTICE_PATH.unlink(missing_ok=True)
            if time.time() - float(notice.get("time", 0)) > 600:
                return
            await self.bot.edit_message(
                int(notice["chat_id"]),
                int(notice["message_id"]),
                "تمت إعادة التشغيل. يمكنك التشغيل الآن.",
            )
        except Exception:
            log.exception("Could not announce the restart")

    async def enqueue(
        self,
        event: Any,
        progress: Any,
        path: Path,
        title: str,
        photo: Path | None = None,
    ) -> None:
        # ffprobe on a local file is instant, so the duration is already
        # known when the first message appears.
        duration = await self.media_duration(path)
        requester_id, requester_name = await self.requester_info(event)
        track: dict[str, Any] = {
            "path": path,
            "duration": duration,
            "status_message": None,
            "title": title,
            "photo": photo,
            "requester_id": requester_id,
            "requester_name": requester_name,
        }
        with contextlib.suppress(Exception):
            await progress.delete()

        # One at a time: two requests in the same second get consecutive
        # positions, so the second one is queued instead of "playing".
        async with self.enqueue_lock:
            busy = self.current is not None or not self.queue.empty()
            if busy:
                track["status_message"] = await self.send_playback_status(
                    event.chat_id,
                    track,
                    "",
                    queued_count=self.queue.qsize() + 1,
                )
            else:
                track["status_message"] = await self.send_playback_status(
                    event.chat_id, track, "جار التشغيل"
                )
            await self.queue.put(track)
            await self.ensure_player()

    # ------------------------------------------------------------ handlers
    async def send_start(self, event: Any) -> None:
        await event.respond(
            START_TEXT or "مرحبًا.", buttons=start_buttons(), link_preview=False
        )

    async def handle_group_message(self, event: Any) -> None:
        text = (event.raw_text or "").strip()
        command = clean_command(text)
        full_command = normalized_text(text)

        known = (
            full_command in CLEAR_QUEUE_WORDS
            or full_command in WAIT_WORDS
            or command in STOP_WORDS
            or command in SKIP_WORDS
            or command in START_WORDS
            or command in PLAY_WORDS
            or full_command in RESET_WORDS
        )
        if not known:
            return
        if not await self.is_admin(event.sender_id):
            return

        if full_command in RESET_WORDS:
            progress = await event.reply("جار إعادة التشغيل ... (نحو 20 ثانية)")
            await self.reset_playback(progress)
            return

        if full_command in CLEAR_QUEUE_WORDS:
            removed = await self.clear_waiting()
            await event.reply(
                f"تم مسح قائمة الانتظار.\nعدد الأصوات التي تم مسحها: {removed}"
            )
            return

        if command in STOP_WORDS or command in SKIP_WORDS:
            advanced = await self.advance()
            await event.reply(
                "تم الانتقال للملف التالي."
                if advanced and not self.leave_when_empty
                else "تم إيقاف التشغيل ومغادرة الاستيج."
                if advanced
                else "لا يوجد ملف يعمل حاليًا."
            )
            return

        if full_command in WAIT_WORDS:
            await event.reply(await self.status_text(), buttons=control_buttons())
            return

        if command in START_WORDS:
            await self.send_start(event)
            return

        parts = text.split(maxsplit=1)
        youtube_query = parts[1].strip() if len(parts) > 1 else ""
        if youtube_query:
            progress = await event.reply("جار البحث عن المقطع وتحميله ...")
            result = await self.request_youtube_audio(youtube_query, progress)
            if result is None:
                await progress.edit("لا يوجد بحث بهذا الاسم.")
                return
            path, title, photo = result
            await self.enqueue(event, progress, path, title, photo)
            return

        reply = await event.message.get_reply_message()
        if not reply or not media_from_message(reply):
            await event.reply("يجب أن ترد بكلمة شغل على فيديو أو ملف صوتي.")
            return

        progress = await event.reply("جار التحميل ...")
        try:
            path = await self.download_media(reply, progress)
        except ValueError as exc:
            await progress.edit(str(exc))
            return
        except Exception as exc:
            log.exception("Download failed")
            await progress.edit(
                "فشل تحميل الملف.\n"
                f"السبب: {type(exc).__name__}: {str(exc)[:200]}"
            )
            return
        try:
            await self.enqueue(event, progress, path, media_title(reply))
        except Exception as exc:
            log.exception("Enqueue failed")
            await progress.edit(
                "تم التحميل لكن تعذرت إضافته للتشغيل.\n"
                f"السبب: {type(exc).__name__}: {str(exc)[:200]}"
            )

    async def _callback_handler(self, event: Any) -> None:
        if event.chat_id != TARGET_CHAT_ID:
            return
        if not await self.is_admin(event.sender_id):
            await event.answer("هذا الزر للمشرفين فقط.", alert=True)
            return

        action = event.data
        if action in {b"music_stop", b"music_skip"}:
            advanced = await self.advance()
            await event.answer(
                "تم الانتقال للملف التالي."
                if advanced and not self.leave_when_empty
                else "تم إيقاف التشغيل ومغادرة الاستيج."
                if advanced
                else "لا يوجد ملف يعمل حاليًا.",
                alert=False,
            )
        elif action == b"music_status":
            await event.answer(await self.status_text(), alert=True)

    async def callback_handler(self, event: Any) -> None:
        try:
            await self._callback_handler(event)
        except Exception:
            log.exception("Unhandled callback error for action=%r", event.data)
            with contextlib.suppress(Exception):
                await event.answer("تعذر تنفيذ العملية.", alert=True)

    async def new_message_handler(self, event: Any) -> None:
        try:
            if event.is_private:
                if self.forward_waiters and event.sender_id == self.user_id:
                    doc_id = getattr(media_from_message(event.message), "id", None)
                    waiter = self.forward_waiters.get(doc_id)
                    if waiter is not None and not waiter.done():
                        waiter.set_result(event.message)
                        return
                if clean_command(event.raw_text or "") in START_WORDS:
                    await self.send_start(event)
            elif event.chat_id == TARGET_CHAT_ID:
                await self.handle_group_message(event)
        except Exception:
            log.exception(
                "Unhandled message error chat=%s sender=%s",
                event.chat_id, event.sender_id,
            )

    # ------------------------------------------------------------ lifecycle
    async def shutdown(self) -> None:
        """Fast, quiet cleanup. GitHub gives only a few seconds on cancel."""
        log.info("Shutting down...")

        async def quiet(coro: Any, timeout: float = 3.0) -> None:
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await asyncio.wait_for(coro, timeout)

        for task in self.background:
            task.cancel()
        if self.background:
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await asyncio.wait_for(
                    asyncio.gather(*self.background, return_exceptions=True), 2
                )
        if self.player_task and not self.player_task.done():
            self.player_task.cancel()
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await asyncio.wait_for(self.player_task, 2)
        if self.calls:
            await quiet(self.calls.leave_call(TARGET_CHAT_ID), 2)
            await quiet(self.calls.stop(), 2)
        await asyncio.gather(
            quiet(self.bot.disconnect(), 2),
            quiet(self.user.disconnect(), 2),
        )
        log.info("Shutdown complete.")

    async def run(self, seconds: float) -> bool:
        """Run until stop/time limit. Returns True when a restart was asked."""
        self.bot.add_event_handler(
            self.new_message_handler, events.NewMessage(incoming=True)
        )
        self.bot.add_event_handler(self.callback_handler, events.CallbackQuery())

        restart = False
        try:
            await self.user.connect()
            if not await self.user.is_user_authorized():
                raise FatalError("SESSION_STRING غير صالحة أو منتهية.")
            self.calls = PyTgCalls(self.user)
            await self.calls.start()
            await self.bot.start(bot_token=BOT_TOKEN)
            global _BOT_SESSION
            with contextlib.suppress(Exception):
                _BOT_SESSION = self.bot.session.save()
            with contextlib.suppress(Exception):
                self.bot_username = str(
                    getattr(await self.bot.get_me(), "username", "") or ""
                )
                self.user_id = int(getattr(await self.user.get_me(), "id", 0))
            self.background = [asyncio.create_task(self.watchdog())]
            await self.announce_restart()
            log.info(
                "Music bot is running; group=%s youtube=%s; auto-stop in %ds",
                TARGET_CHAT_ID, YOUTUBE_CHAT_ID, int(seconds),
            )
            waiters = {
                asyncio.create_task(self.stop_event.wait()),
                asyncio.create_task(self.restart_event.wait()),
            }
            done, pending = await asyncio.wait(
                waiters, timeout=seconds, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            if self.stop_event.is_set():
                log.info("Stop signal received.")
            elif self.restart_event.is_set():
                restart = True
                log.info("Restarting the bot as requested.")
            else:
                log.info("Runtime limit reached; restarting via workflow.")
        finally:
            try:
                await asyncio.wait_for(self.shutdown(), 25)
            except Exception:
                log.warning("Shutdown took too long; continuing")
        return restart


async def main() -> None:
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def on_loop_error(_loop: Any, context: dict[str, Any]) -> None:
        log.error(
            "Background error: %s",
            context.get("message"),
            exc_info=context.get("exception"),
        )

    loop.set_exception_handler(on_loop_error)
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop_event.set)

    started = time.monotonic()
    while not stop_event.is_set():
        remaining = MAX_RUNTIME_SECONDS - (time.monotonic() - started)
        if remaining <= 20:
            break
        try:
            restarted = await GroupMusicBot(stop_event).run(remaining)
            if restarted:
                continue  # restart requested from the group
            break  # time limit reached or stop requested
        except FatalError:
            raise
        except FloodWaitError as exc:
            wait = int(getattr(exc, "seconds", 60)) + 5
            left = MAX_RUNTIME_SECONDS - (time.monotonic() - started)
            log.warning("FloodWait: Telegram asks to wait %ss", wait)
            if wait >= left - 20:
                break
            await asyncio.sleep(wait)
        except Exception:
            # Never go down before the scheduled time: start over.
            log.exception("Bot crashed; restarting in 15 seconds")
            await asyncio.sleep(15)


def validate_configuration() -> None:
    if API_ID <= 0 or not API_HASH:
        raise RuntimeError("ضع API_ID و API_HASH في قسم CONFIGURATION.")
    if not BOT_TOKEN:
        raise RuntimeError("المتغير BOT_TOKEN غير موجود (GitHub Secret).")
    if not SESSION_STRING:
        raise RuntimeError("المتغير SESSION_STRING غير موجود (GitHub Secret).")
    if not TARGET_CHAT_ID:
        raise RuntimeError("ضع TARGET_CHAT_ID في قسم CONFIGURATION.")
    if not YOUTUBE_CHAT_ID:
        log.warning("YOUTUBE_CHAT_ID فارغ: أمر (شغل + اسم) لن يعمل.")


if __name__ == "__main__":
    try:
        validate_configuration()
        asyncio.run(main())
    except (FatalError, RuntimeError) as exc:
        log.error("%s", exc)
        sys.exit(1)
