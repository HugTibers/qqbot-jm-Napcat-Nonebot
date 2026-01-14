"""
Pixiv 插件 - QQ Bot
命令：
  pixiv 搜索/find <关键词> - 搜索作品
  pixiv 下载/download <ID> - 下载指定作品
  pixiv 排行/ranking [day/week/month] - 获取排行榜
  pixiv 帮助/help - 显示帮助
"""

import asyncio
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from nonebot import on_regex
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, MessageSegment
from nonebot.exception import FinishedException

from plugins.plugin_switcher import is_plugin_enabled

from .service import (
    pixiv_search,
    pixiv_download,
    pixiv_ranking,
    pixiv_detail,
    get_download_path,
)

# 搜索结果缓存 (user_key -> list of illusts)
_search_cache: Dict[Tuple[str, Optional[int]], List] = {}

# 命令匹配
pixiv_cmd = on_regex(
    r"^pixiv\s+.+",
    flags=re.IGNORECASE,
    priority=10,
    block=True
)

# 快捷下载命令：px123 or px 123
px_quick_cmd = on_regex(
    r"^px\s*\d+",
    flags=re.IGNORECASE,
    priority=10,
    block=True
)


def _cache_key(event: MessageEvent) -> Tuple[str, Optional[int]]:
    """生成用户缓存键"""
    user_id = str(event.user_id)
    group_id = getattr(event, "group_id", None)
    return (user_id, group_id)


def _should_block_event(bot: Bot, event: MessageEvent) -> bool:
    """检查是否应该阻止事件"""
    return not is_plugin_enabled("pixiv")


def _parse_command(text: str) -> dict:
    """
    解析命令
    返回: {"action": "search/download/ranking/help", "args": ...}
    """
    text = text.strip()
    # 移除 pixiv 前缀
    text = re.sub(r"^pixiv\s+", "", text, flags=re.IGNORECASE)
    
    # 搜索
    match = re.match(r"^(?:搜索|find|search)\s+(.+)$", text, flags=re.IGNORECASE)
    if match:
        return {"action": "search", "keyword": match.group(1).strip()}
    
    # 下载 - 支持 ID 或序号
    match = re.match(r"^(?:下载|download|dl)\s+(\d+)$", text, flags=re.IGNORECASE)
    if match:
        return {"action": "download", "id": match.group(1)}
    
    # 排行榜
    match = re.match(r"^(?:排行|ranking|rank)(?:\s+(day|week|month))?$", text, flags=re.IGNORECASE)
    if match:
        mode = match.group(1) or "day"
        return {"action": "ranking", "mode": mode.lower()}
    
    # 详情
    match = re.match(r"^(?:详情|detail|info)\s+(\d+)$", text, flags=re.IGNORECASE)
    if match:
        return {"action": "detail", "id": match.group(1)}
    
    # 帮助
    if re.match(r"^(?:帮助|help)$", text, flags=re.IGNORECASE):
        return {"action": "help"}
    
    return {"action": "unknown"}


def _format_illust(idx: int, illust) -> str:
    """格式化作品信息"""
    return (
        f"{idx}. [{illust.title}]\n"
        f"   作者: {illust.user.name}\n"
        f"   ID: {illust.id}"
    )


@pixiv_cmd.handle()
async def _(bot: Bot, event: MessageEvent):
    if _should_block_event(bot, event):
        await pixiv_cmd.finish()
    
    text = event.get_plaintext().strip()
    cmd = _parse_command(text)
    action = cmd.get("action")
    
    if action == "help":
        await _handle_help(bot, event)
    elif action == "search":
        await _handle_search(bot, event, cmd["keyword"])
    elif action == "download":
        await _handle_download(bot, event, cmd["id"])
    elif action == "ranking":
        await _handle_ranking(bot, event, cmd["mode"])
    elif action == "detail":
        await _handle_detail(bot, event, cmd["id"])
    else:
        await pixiv_cmd.finish(Message("未知命令，输入 pixiv 帮助 查看用法"))


@px_quick_cmd.handle()
async def _(bot: Bot, event: MessageEvent):
    """处理快捷下载命令 px123"""
    if _should_block_event(bot, event):
        await px_quick_cmd.finish()
    
    text = event.get_plaintext().strip()
    # 提取 ID
    match = re.search(r"px\s*(\d+)", text, flags=re.IGNORECASE)
    if not match:
        await px_quick_cmd.finish()
    
    illust_id = match.group(1)
    await _handle_download_quick(bot, event, illust_id)


async def _handle_download_quick(bot: Bot, event: MessageEvent, illust_id: str):
    """快捷下载处理"""
    await bot.send(event, f"📥 正在下载作品 {illust_id}...")
    
    try:
        result = await asyncio.to_thread(pixiv_download, illust_id)
    except Exception as e:
        await px_quick_cmd.finish(Message(f"❌ 下载失败: {e}"))
        return
    
    if not result["success"]:
        await px_quick_cmd.finish(Message(f"❌ 下载失败: {result['error']}"))
        return
    
    # 发送图片
    for img_path in result["images"][:9]:
        try:
            img_seg = MessageSegment.image(f"file://{img_path}")
            await bot.send(event, img_seg)
        except Exception as e:
            await bot.send(event, f"发送图片失败: {e}")
    
    msg = f"✅ 下载完成: {result['title']}\n共 {result['count']} 张图片"
    if result["count"] > 9:
        msg += f"\n（只显示前 9 张）"
    await px_quick_cmd.finish(Message(msg))


async def _handle_help(bot: Bot, event: MessageEvent):
    """处理帮助命令"""
    help_text = """Pixiv 插件指令：
1. pixiv 搜索 <关键词> - 搜索作品
2. pixiv 下载 <ID或序号> - 下载作品
3. pixiv 排行 [day/week/month] - 排行榜
4. pixiv 详情 <ID> - 查看作品详情
5. px<ID> - 快捷下载（如 px123456）

示例：
  pixiv 搜索 初音ミク
  pixiv 下载 12345678
  px139700179"""
    await pixiv_cmd.finish(Message(help_text))


async def _handle_search(bot: Bot, event: MessageEvent, keyword: str):
    """处理搜索命令"""
    await bot.send(event, f"🔍 正在搜索: {keyword}")
    
    try:
        illusts = await asyncio.to_thread(pixiv_search, keyword, limit=10)
    except Exception as e:
        await pixiv_cmd.finish(Message(f"❌ 搜索失败: {e}"))
        return
    
    if not illusts:
        await pixiv_cmd.finish(Message(f"未找到与 '{keyword}' 相关的作品"))
        return
    
    # 缓存搜索结果
    key = _cache_key(event)
    _search_cache[key] = illusts
    
    lines = [f"🎨 搜索 '{keyword}' 结果 ({len(illusts)} 条):"]
    for i, illust in enumerate(illusts, 1):
        lines.append(_format_illust(i, illust))
    lines.append("\n💡 输入 pixiv 下载 <序号或ID> 下载作品")
    
    await pixiv_cmd.finish(Message("\n".join(lines)))


async def _handle_download(bot: Bot, event: MessageEvent, id_or_idx: str):
    """处理下载命令"""
    illust_id = id_or_idx
    key = _cache_key(event)
    
    # 如果是小数字，可能是序号
    if int(id_or_idx) <= 20 and key in _search_cache:
        idx = int(id_or_idx) - 1
        if 0 <= idx < len(_search_cache[key]):
            illust_id = str(_search_cache[key][idx].id)
    
    await bot.send(event, f"📥 正在下载作品 {illust_id}...")
    
    try:
        result = await asyncio.to_thread(pixiv_download, illust_id)
    except Exception as e:
        await pixiv_cmd.finish(Message(f"❌ 下载失败: {e}"))
        return
    
    if not result["success"]:
        await pixiv_cmd.finish(Message(f"❌ 下载失败: {result['error']}"))
        return
    
    # 发送图片
    for img_path in result["images"][:9]:  # 最多发 9 张
        try:
            img_seg = MessageSegment.image(f"file://{img_path}")
            await bot.send(event, img_seg)
        except Exception as e:
            await bot.send(event, f"发送图片失败: {e}")
    
    msg = f"✅ 下载完成: {result['title']}\n共 {result['count']} 张图片"
    if result["count"] > 9:
        msg += f"\n（只显示前 9 张，全部图片保存在: {result['path']}）"
    await pixiv_cmd.finish(Message(msg))


async def _handle_ranking(bot: Bot, event: MessageEvent, mode: str):
    """处理排行榜命令"""
    mode_names = {"day": "日榜", "week": "周榜", "month": "月榜"}
    await bot.send(event, f"📊 正在获取{mode_names.get(mode, '日榜')}...")
    
    try:
        illusts = await asyncio.to_thread(pixiv_ranking, mode, limit=10)
    except Exception as e:
        await pixiv_cmd.finish(Message(f"❌ 获取排行榜失败: {e}"))
        return
    
    if not illusts:
        await pixiv_cmd.finish(Message("获取排行榜失败"))
        return
    
    # 缓存结果
    key = _cache_key(event)
    _search_cache[key] = illusts
    
    lines = [f"🏆 Pixiv {mode_names.get(mode, '日榜')} Top 10:"]
    for i, illust in enumerate(illusts, 1):
        lines.append(_format_illust(i, illust))
    lines.append("\n💡 输入 pixiv 下载 <序号> 下载作品")
    
    await pixiv_cmd.finish(Message("\n".join(lines)))


async def _handle_detail(bot: Bot, event: MessageEvent, illust_id: str):
    """处理详情命令"""
    try:
        illust = await asyncio.to_thread(pixiv_detail, illust_id)
    except Exception as e:
        await pixiv_cmd.finish(Message(f"❌ 获取详情失败: {e}"))
        return
    
    if not illust:
        await pixiv_cmd.finish(Message(f"未找到作品 {illust_id}"))
        return
    
    info = f"""📖 作品详情:
标题: {illust.title}
作者: {illust.user.name} (ID: {illust.user.id})
ID: {illust.id}
类型: {illust.type}
页数: {illust.page_count}
收藏数: {illust.total_bookmarks}
浏览数: {illust.total_view}
标签: {', '.join([t.name for t in illust.tags[:5]])}

💡 输入 pixiv 下载 {illust.id} 下载此作品"""
    
    await pixiv_cmd.finish(Message(info))
