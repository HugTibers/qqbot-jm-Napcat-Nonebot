import asyncio
import random
import shutil
import time
from pathlib import Path
from typing import Dict, List, Tuple

from nonebot.adapters.onebot.v11 import Bot, MessageEvent, MessageSegment
from nonebot.log import logger

from .jmcomic_client import OPTION, download_album_with_retry, load_album_dir
from .utils import clean_error_text, delayed_cleanup, gather_images, merge_to_pdf, safe_filename

# 配置
ENABLED = True  # 功能总开关，False 时不响应任何指令
ALLOWED_GROUPS = {}  # 仅允许的群号列表，空表示不限制
CLEANUP_DELAY_SECONDS = 600
MAX_CONCURRENT = 2
API_TIMEOUT = 60  # 默认接口超时秒数（用于短时操作）
MAX_PDF_NAME_LENGTH = 30
MAX_IMAGES_PER_PDF = 200
MAX_PDF_SIZE_MB = 50
TIMING_ENABLED = False  # 计时功能开关

_download_queue: List[Dict] = []
_running_jobs: List[Dict] = []
_queue_lock = asyncio.Lock()
_upload_lock = asyncio.Lock()


def _format_timing_text(timing: Dict[str, float]) -> str:
    order = ["下载漫画", "转换格式", "合成PDF", "上传漫画", "发送消息"]
    parts = []
    for key in order:
        if key in timing:
            parts.append(f"{key}: {timing[key]:.2f}s")
    return "耗时 | " + "，".join(parts) if parts else ""

def _is_upload_timeout(err_text: str) -> bool:
    text = err_text.lower()
    return ("timeout" in text or "networkerror" in text or "network error" in text) and ("upload" in text)


def _should_retry_with_simple_name(err_text: str) -> bool:
    text = err_text.lower()
    return "rich media transfer failed" in text or "retcode=1200" in text


def _calc_upload_timeout(pdf_path: Path) -> float:
    """根据 PDF 大小动态增加超时，减少误报。"""
    size_mb = pdf_path.stat().st_size / (1024 * 1024)
    if size_mb <= 100:
        return API_TIMEOUT
    return min(300.0, API_TIMEOUT + (size_mb - 100) * 0.5)

def _clamp_filename_length(filename: str, max_len: int) -> str:
    """
    Limit filename (including suffix) length for platforms with short name caps.
    """
    if len(filename) <= max_len:
        return filename
    suffix = Path(filename).suffix
    if suffix and len(suffix) < max_len:
        base = filename[: -len(suffix)]
        max_base_len = max_len - len(suffix)
        if max_base_len <= 0:
            return filename[:max_len]
        return f"{base[:max_base_len]}{suffix}"
    return filename[:max_len]


def _final_pdf_name(base_name: str, idx: int, total: int) -> str:
    if total <= 1:
        return f"{base_name}.pdf"
    return f"{base_name}_part{idx:03d}of{total}.pdf"


def _split_images(imgs: List[Path], max_per_pdf: int) -> List[List[Path]]:
    if max_per_pdf <= 0 or len(imgs) <= max_per_pdf:
        return [imgs]
    return [imgs[i : i + max_per_pdf] for i in range(0, len(imgs), max_per_pdf)]


def _build_pdfs_with_limits(
    imgs: List[Path],
    pdf_dir: Path,
    base_name: str,
    timing: Dict[str, float] | None,
) -> List[Dict]:
    max_bytes = MAX_PDF_SIZE_MB * 1024 * 1024
    batches = _split_images(imgs, MAX_IMAGES_PER_PDF)
    tmp_paths: List[Path] = []

    def _emit_batch(batch: List[Path]):
        tmp_path = pdf_dir / f"tmp_{len(tmp_paths):03d}.pdf"
        merge_to_pdf(batch, tmp_path, timing)
        size = tmp_path.stat().st_size
        if size <= max_bytes or len(batch) <= 1:
            tmp_paths.append(tmp_path)
            return
        tmp_path.unlink(missing_ok=True)
        mid = len(batch) // 2
        _emit_batch(batch[:mid])
        _emit_batch(batch[mid:])

    for batch in batches:
        _emit_batch(batch)

    total = len(tmp_paths)
    final_paths: List[Dict] = []
    for idx, tmp_path in enumerate(tmp_paths, start=1):
        name = _final_pdf_name(base_name, idx, total)
        name = _clamp_filename_length(name, MAX_PDF_NAME_LENGTH)
        final_path = pdf_dir / name
        if final_path.exists():
            final_path.unlink()
        tmp_path.replace(final_path)
        final_paths.append({"path": final_path, "part_idx": idx, "part_total": total})

    return final_paths


async def _upload_pdf_with_retry(
    bot: Bot,
    target: Dict,
    pdf_path: Path,
    album_id: str,
    fallback_base_name: str | None = None,
    part_idx: int | None = None,
    part_total: int | None = None,
) -> Tuple[str | None, str | None]:
    upload_err = None
    upload_warn = None
    target_desc = (
        f"group {target.get('group_id')}"
        if target.get("message_type") == "group"
        else f"user {target.get('user_id')}"
    )
    try:
        upload_timeout = _calc_upload_timeout(pdf_path)
        await _call_with_timeout(
            _upload_pdf(bot, target, pdf_path=pdf_path),
            "上传PDF",
            timeout=upload_timeout,
        )
    except Exception as e:
        err_text = str(e)
        if _should_retry_with_simple_name(err_text):
            base_name = fallback_base_name or f"JM_{album_id}"
            retry_name = _final_pdf_name(base_name, part_idx or 1, part_total or 1)
            retry_path = pdf_path.with_name(_clamp_filename_length(retry_name, MAX_PDF_NAME_LENGTH))
            try:
                if retry_path != pdf_path:
                    shutil.copy2(pdf_path, retry_path)
                upload_timeout = _calc_upload_timeout(retry_path)
                await _call_with_timeout(
                    _upload_pdf(bot, target, pdf_path=retry_path),
                    "上传PDF(重试)",
                    timeout=upload_timeout,
                )
                upload_warn = "原文件名疑似不被支持，已改用简化文件名重试"
            except Exception as retry_err:
                retry_text = str(retry_err)
                if _is_upload_timeout(retry_text):
                    upload_warn = "上传耗时较长"
                    logger.warning(f"上传 {album_id} 到 {target_desc} 可能成功但接口超时：{retry_err}")
                else:
                    upload_err = clean_error_text(retry_err)
            finally:
                try:
                    if retry_path.exists():
                        retry_path.unlink()
                except Exception:
                    pass
        elif _is_upload_timeout(err_text):
            upload_warn = "上传耗时较长"
            logger.warning(f"上传 {album_id} 到 {target_desc} 可能成功但接口超时：{e}")
        else:
            upload_err = clean_error_text(e)
    return upload_err, upload_warn


async def _call_with_timeout(coro, desc: str, timeout: float | None = None):
    try:
        return await asyncio.wait_for(coro, timeout or API_TIMEOUT)
    except asyncio.TimeoutError:
        to = timeout or API_TIMEOUT
        raise RuntimeError(f"{desc} 超时（>{to}s）")


async def _send_text(bot: Bot, job: Dict, text: str):
    try:
        if job["message_type"] == "group":
            await bot.call_api("send_group_msg", group_id=job["group_id"], message=text)
        else:
            await bot.call_api("send_private_msg", user_id=job["user_id"], message=text)
    except Exception as e:
        logger.warning(f"发送通知失败: {e}")


def _should_block_event(bot: Bot, event: MessageEvent) -> bool:
    if event.message_type != "group":
        return False
    if ALLOWED_GROUPS and event.group_id not in ALLOWED_GROUPS:
        return True
    mentioned = False
    try:
        mentioned = bool(getattr(event, "to_me", False) or event.is_tome())
    except Exception:
        mentioned = bool(getattr(event, "to_me", False))
    if not mentioned:
        for seg in event.message:
            if seg.type != "at":
                continue
            qq = seg.data.get("qq") or seg.data.get("id") or seg.data.get("uid")
            if str(qq) in ("all", str(bot.self_id)):
                mentioned = True
                break
    return not mentioned


async def _upload_pdf(bot: Bot, target, pdf_path: Path):
    name = pdf_path.name
    if target["message_type"] == "group":
        return await bot.call_api("upload_group_file", group_id=target["group_id"], file=str(pdf_path), name=name)
    return await bot.call_api("upload_private_file", user_id=target["user_id"], file=str(pdf_path), name=name)


async def _run_job(bot: Bot, job: Dict):
    album_id = job["album_id"]
    pdf_path: Path | None = None
    pdf_jobs: List[Dict] = []
    cleanup_targets: List[Path] = []
    cover_path: Path | None = None
    timing: Dict[str, float] | None = {} if TIMING_ENABLED else None
    try:
        t_download = time.perf_counter() if TIMING_ENABLED else None
        await download_album_with_retry(album_id, OPTION)
        album, photo_dirs = await asyncio.to_thread(load_album_dir, album_id)
        raw_title = getattr(album, "title", None) or getattr(album, "oname", None)
        comic_name = safe_filename(str(raw_title or ""), f"JM_{album_id}")
        imgs: List[Path] = []
        for d in photo_dirs:
            imgs.extend(await asyncio.to_thread(gather_images, d))
        if not imgs:
            raise RuntimeError("没有找到可以合成 PDF 的图片")
        cover_path = imgs[0]
        pdf_dir = Path(OPTION.dir_rule.base_dir) / f"pdf_{album_id}"
        pdf_dir.mkdir(parents=True, exist_ok=True)
        pdf_jobs = []
        for d in photo_dirs:
            chapter_imgs = await asyncio.to_thread(gather_images, d)
            if not chapter_imgs:
                continue
            chapter_name = safe_filename(d.name, "chapter")
            base_name = f"{comic_name}_{chapter_name}"
            fallback_base = f"JM_{album_id}_{chapter_name}"
            chapter_pdfs = await asyncio.to_thread(_build_pdfs_with_limits, chapter_imgs, pdf_dir, base_name, timing)
            for entry in chapter_pdfs:
                entry["fallback_base_name"] = fallback_base
            pdf_jobs.extend(chapter_pdfs)
        if TIMING_ENABLED and timing is not None and t_download is not None:
            timing["下载漫画"] = time.perf_counter() - t_download
        cleanup_targets = [pdf_dir, *photo_dirs]
    except Exception as e:
        await _send_text(bot, job, f"{album_id} 下载或生成 PDF 失败：{clean_error_text(e)}")
        await _finish_job(bot, job)
        return

    upload_err = None
    upload_warn = None

    try:
        upload_start = time.perf_counter() if TIMING_ENABLED else None
        async with _upload_lock:
            target = (
                {"message_type": "group", "group_id": job["group_id"]}
                if job.get("message_type") == "group" and job.get("group_id")
                else {"message_type": "private", "user_id": job["user_id"]}
            )
            for idx, entry in enumerate(pdf_jobs, start=1):
                pdf_path = entry["path"]
                part_desc = f"{idx}/{len(pdf_jobs)}" if len(pdf_jobs) > 1 else None
                upload_err, upload_warn = await _upload_pdf_with_retry(
                    bot,
                    target,
                    pdf_path,
                    album_id,
                    fallback_base_name=entry.get("fallback_base_name"),
                    part_idx=entry.get("part_idx"),
                    part_total=entry.get("part_total"),
                )
                if upload_err:
                    if part_desc:
                        upload_err = f"第 {part_desc} 份：{upload_err}"
                    break
    except Exception as e:
        upload_err = clean_error_text(e)
    finally:
        if TIMING_ENABLED and timing is not None and upload_start is not None:
            timing["上传漫画"] = time.perf_counter() - upload_start

    asyncio.create_task(delayed_cleanup(cleanup_targets, CLEANUP_DELAY_SECONDS))

    if upload_err is None:
        if (
            cover_path
            and job.get("message_type") == "group"
            and job.get("group_id")
            and cover_path.exists()
        ):
            try:
                await _call_with_timeout(
                    bot.call_api(
                        "send_group_msg",
                        group_id=job["group_id"],
                        message=MessageSegment.image(str(cover_path)),
                    ),
                    "发送封面",
                )
            except Exception as e:
                logger.warning(f"发送封面失败：{e}")
    else:
        msg = f"PDF 已生成：{album_id}\n发送失败：{upload_err}"
        send_start = time.perf_counter() if TIMING_ENABLED else None
        await _send_text(bot, job, msg)
        if TIMING_ENABLED and timing is not None and send_start is not None:
            timing["发送消息"] = time.perf_counter() - send_start
            timing_msg = _format_timing_text(timing)
            if timing_msg:
                await _send_text(bot, job, timing_msg)

    await _finish_job(bot, job)


async def _finish_job(bot: Bot, job: Dict):
    async with _queue_lock:
        if job in _running_jobs:
            _running_jobs.remove(job)
    if _download_queue and len(_running_jobs) < MAX_CONCURRENT:
        nxt = _download_queue.pop(0)
        _running_jobs.append(nxt)
        asyncio.create_task(_run_job(bot, nxt))


async def enqueue_job(bot: Bot, event: MessageEvent, album_id: str) -> Dict:
    job = {
        "album_id": album_id,
        "message_type": event.message_type,
        "group_id": getattr(event, "group_id", None),
        "user_id": event.user_id,
    }
    async with _queue_lock:
        if len(_download_queue) >= 20:
            return {"status": "full", "limit": 20, "queued": len(_download_queue)}
        if len(_running_jobs) < MAX_CONCURRENT:
            _running_jobs.append(job)
            asyncio.create_task(_run_job(bot, job))
            return {"status": "started", "ahead": 0}
        ahead = len(_download_queue)
        _download_queue.append(job)
        return {"status": "queued", "ahead": ahead}


async def cancel_job(album_id: str) -> Tuple[int, bool, int]:
    async with _queue_lock:
        running_same = any(j["album_id"] == album_id for j in _running_jobs)
        before = len(_download_queue)
        _download_queue[:] = [j for j in _download_queue if j["album_id"] != album_id]
        removed = before - len(_download_queue)
        queued_len = len(_download_queue)
    return removed, running_same, queued_len


async def queue_snapshot() -> Tuple[List[str], List[str]]:
    async with _queue_lock:
        running_ids = [j["album_id"] for j in _running_jobs]
        queued_ids = [j["album_id"] for j in _download_queue]
    return running_ids, queued_ids


__all__ = [
    "ALLOWED_GROUPS",
    "CLEANUP_DELAY_SECONDS",
    "MAX_CONCURRENT",
    "API_TIMEOUT",
    "MAX_PDF_NAME_LENGTH",
    "should_block_event",
    "enqueue_job",
    "cancel_job",
    "queue_snapshot",
]

# 对外暴露的阻断判断
def should_block_event(bot: Bot, event: MessageEvent) -> bool:
    return _should_block_event(bot, event)
