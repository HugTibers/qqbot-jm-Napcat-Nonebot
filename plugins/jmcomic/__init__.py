import re

from nonebot import on_regex
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent

from .service import MAX_CONCURRENT, cancel_job, enqueue_job, queue_snapshot, should_block_event
import plugins.jmcomic.service as jm_service

# 命令解析：包含 jm123 即可触发，自动提取文本中的所有 jm+数字
jm_forward_cmd = on_regex(r"(?i)jm\s*\d+", flags=re.IGNORECASE, priority=10, block=True)
queue_cmd = on_regex(r"^jm队列$|^jmqueue$", flags=re.IGNORECASE, priority=10, block=True)
remove_cmd = on_regex(r"^jm(?:取消|删除)\s*(\d+)$", flags=re.IGNORECASE, priority=10, block=True)
help_cmd = on_regex(r"^jm帮助$|^jmhelp$", flags=re.IGNORECASE, priority=10, block=True)
toggle_cmd = on_regex(r"^jm(?:开启|关闭|start|stop)$", flags=re.IGNORECASE, priority=10, block=True)


@jm_forward_cmd.handle()
async def _(bot: Bot, event: MessageEvent):
    text = event.get_plaintext().strip()
    ids = []
    seen = set()
    for album_id in re.findall(r"jm\s*(\d+)", text, flags=re.IGNORECASE):
        if album_id not in seen:
            seen.add(album_id)
            ids.append(album_id)
    if not ids:
        await jm_forward_cmd.finish()
    if not jm_service.ENABLED:
        await jm_forward_cmd.finish()
    if should_block_event(bot, event):
        await jm_forward_cmd.finish()
    responses = []
    for album_id in ids:
        result = await enqueue_job(bot, event, album_id)
        if result["status"] == "full":
            responses.append(f"队列已满（最多 {result['limit']} 个），请稍后再试")
            break
        if result["status"] == "started":
            msg = f"收到，将下载 JM{album_id}"
        else:
            msg = f"收到，将下载 JM{album_id}（已排队，前面还有 {result['ahead']} 个任务）"
        responses.append(msg)
    await jm_forward_cmd.finish(Message("\n".join(responses)))


@queue_cmd.handle()
async def _(bot: Bot, event: MessageEvent):
    if not jm_service.ENABLED:
        await queue_cmd.finish()
    if should_block_event(bot, event):
        await queue_cmd.finish()
    running_ids, queued_ids = await queue_snapshot()
    if not running_ids and not queued_ids:
        await queue_cmd.finish(Message("当前队列为空"))
    msg_lines = [
        f"下载中({len(running_ids)}/{MAX_CONCURRENT}): " + (", ".join(running_ids) if running_ids else "无"),
        f"排队中: " + (", ".join(queued_ids) if queued_ids else "无"),
    ]
    await queue_cmd.finish(Message("\n".join(msg_lines)))


@help_cmd.handle()
async def _(bot: Bot, event: MessageEvent):
    if not jm_service.ENABLED:
        await help_cmd.finish()
    if should_block_event(bot, event):
        await help_cmd.finish()
    lines = [
        "jm 指令：",
        "1) jm<id>  生成并发送 PDF",
        "2) jm队列 / jmqueue   查看下载中和排队任务",
        "3) jm取消<id> / jm删除<id>   取消等待队列中的任务",
    ]
    await help_cmd.finish(Message("\n".join(lines)))


@remove_cmd.handle()
async def _(bot: Bot, event: MessageEvent):
    text = event.get_plaintext().strip()
    match = re.match(r"^jm(?:取消|删除)\s*(\d+)$", text, flags=re.IGNORECASE)
    if not match:
        await remove_cmd.finish()
    if not jm_service.ENABLED:
        await remove_cmd.finish()
    if should_block_event(bot, event):
        await remove_cmd.finish()
    album_id = match.group(1)
    removed, running_same, queued_len = await cancel_job(album_id)
    if removed:
        await remove_cmd.finish(Message(f"JM{album_id} 已从队列移除，当前排队 {queued_len} 个"))
    elif running_same:
        await remove_cmd.finish(Message(f"JM{album_id} 已在下载中，无法取消"))
    else:
        await remove_cmd.finish(Message(f"JM{album_id} 不在等待队列中"))


@toggle_cmd.handle()
async def _(bot: Bot, event: MessageEvent):
    if should_block_event(bot, event):
        await toggle_cmd.finish()
    text = event.get_plaintext().strip().lower()
    if "开启" in text or "start" in text:
        jm_service.ENABLED = True
        await toggle_cmd.finish(Message("jm 功能已开启"))
    else:
        jm_service.ENABLED = False
        await toggle_cmd.finish(Message("jm 功能已关闭"))

