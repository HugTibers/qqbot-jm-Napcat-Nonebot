import asyncio
from pathlib import Path
from typing import List, Tuple

from nonebot.log import logger

import jmcomic


OPTION_PATH = Path(__file__).resolve().parents[2] / "option.yml"
OPTION = jmcomic.create_option_by_file(str(OPTION_PATH))


def load_album_dir(album_id: str, option=OPTION) -> Tuple[jmcomic.JmAlbumDetail, List[Path]]:
    """
    根据 option 规则获取专辑详情和其所有章节的存储目录。
    """
    client = option.new_jm_client()
    album = client.get_album_detail(album_id)
    seen = set()
    photo_dirs: List[Path] = []
    for photo in album:
        photo_dir = Path(option.decide_image_save_dir(photo))
        if photo_dir in seen:
            continue
        seen.add(photo_dir)
        photo_dirs.append(photo_dir)
    return album, photo_dirs


async def download_album_with_retry(album_id: str, option=OPTION, retries: int = 2, wait_seconds: float = 2.0):
    """
    遇到“部分下载失败”时自动重试，避免偶发的单图拉取失败。
    """
    max_attempts = retries + 1
    last_err = None

    for attempt in range(1, max_attempts + 1):
        try:
            logger.info(f"jmcomic 下载 {album_id}，第 {attempt}/{max_attempts} 次尝试")
            await asyncio.to_thread(jmcomic.download_album, album_id, option)
            return
        except Exception as e:
            last_err = e
            msg = str(e)
            logger.warning(f"jmcomic 下载 {album_id} 第 {attempt} 次失败：{msg}")
            partial_failed = "部分下载失败" in msg
            if attempt >= max_attempts or not partial_failed:
                raise last_err
            await asyncio.sleep(wait_seconds)
