"""
Pixiv 服务层 - API 调用封装
"""

import os
from pathlib import Path
from typing import List, Optional

from pixivpy3 import AppPixivAPI

# 配置
REFRESH_TOKEN_FILE = Path(__file__).parent / "my_refresh_token.txt"
DOWNLOAD_DIR = Path(__file__).parent / "downloads"

# 确保下载目录存在
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

# 全局 API 实例
_api: Optional[AppPixivAPI] = None
_last_auth_time: float = 0


def _do_auth(api: AppPixivAPI) -> bool:
    """执行认证，返回是否成功"""
    global _last_auth_time
    import time
    if REFRESH_TOKEN_FILE.exists():
        token = REFRESH_TOKEN_FILE.read_text().strip()
        try:
            api.auth(refresh_token=token)
            _last_auth_time = time.time()
            return True
        except Exception as e:
            print(f"[pixiv] 认证失败: {e}")
            return False
    else:
        raise RuntimeError("未找到 refresh_token，请先运行 get_refresh_token.py")


def _get_api(force_reauth: bool = False) -> AppPixivAPI:
    """获取已认证的 API 实例"""
    global _api, _last_auth_time
    import time
    
    if _api is None:
        _api = AppPixivAPI()
        _do_auth(_api)
    elif force_reauth or (time.time() - _last_auth_time > 3000):
        # token 通常 1 小时过期，这里 50 分钟刷新一次
        print("[pixiv] 刷新 token...")
        _do_auth(_api)
    
    return _api


def refresh_auth():
    """强制刷新认证"""
    global _api
    if _api is not None:
        _do_auth(_api)
        return True
    return False


def pixiv_search(keyword: str, limit: int = 10) -> List:
    """
    搜索作品
    :param keyword: 搜索关键词
    :param limit: 返回数量限制
    :return: 作品列表
    """
    api = _get_api()
    result = api.search_illust(keyword, search_target='partial_match_for_tags')
    
    # 检查是否需要重新认证
    if not result.illusts and hasattr(result, 'error') and result.error:
        print(f"[pixiv] 搜索失败，尝试刷新 token: {result.error}")
        api = _get_api(force_reauth=True)
        result = api.search_illust(keyword, search_target='partial_match_for_tags')
    
    return result.illusts[:limit] if result.illusts else []


def pixiv_ranking(mode: str = "day", limit: int = 10) -> List:
    """
    获取排行榜
    :param mode: day/week/month
    :param limit: 返回数量限制
    :return: 作品列表
    """
    api = _get_api()
    result = api.illust_ranking(mode)
    
    # 检查是否需要重新认证
    if not result.illusts and hasattr(result, 'error') and result.error:
        print(f"[pixiv] 排行榜失败，尝试刷新 token: {result.error}")
        api = _get_api(force_reauth=True)
        result = api.illust_ranking(mode)
    
    return result.illusts[:limit] if result.illusts else []


def pixiv_detail(illust_id: str):
    """
    获取作品详情
    :param illust_id: 作品 ID
    :return: 作品对象
    """
    api = _get_api()
    result = api.illust_detail(int(illust_id))
    return result.illust if hasattr(result, 'illust') else None


def get_download_path(illust_id: str) -> Path:
    """获取作品下载路径"""
    return DOWNLOAD_DIR / str(illust_id)


def pixiv_download(illust_id: str) -> dict:
    """
    下载作品
    :param illust_id: 作品 ID
    :return: {"success": bool, "images": [...], "title": str, "count": int, "path": str}
    """
    api = _get_api()
    
    try:
        # 获取作品详情
        result = api.illust_detail(int(illust_id))
        
        # 检查是否有错误（可能是 token 过期）
        if hasattr(result, 'error') and result.error:
            error_msg = str(result.error)
            if 'invalid_grant' in error_msg.lower() or 'token' in error_msg.lower():
                # token 过期，尝试刷新后重试
                print(f"[pixiv] Token 可能过期，尝试刷新: {error_msg}")
                api = _get_api(force_reauth=True)
                result = api.illust_detail(int(illust_id))
        
        if not hasattr(result, 'illust') or result.illust is None:
            # 仍然失败，检查具体错误
            if hasattr(result, 'error') and result.error:
                return {"success": False, "error": f"API 错误: {result.error}"}
            return {"success": False, "error": "作品不存在或已被删除"}
        
        illust = result.illust
        title = illust.title
        
        # 创建下载目录
        save_dir = get_download_path(illust_id)
        save_dir.mkdir(parents=True, exist_ok=True)
        
        # 获取图片 URL
        image_urls = []
        if illust.page_count == 1:
            # 单图
            image_urls.append(illust.meta_single_page.get('original_image_url') or illust.image_urls.large)
        else:
            # 多图
            for page in illust.meta_pages:
                url = page.image_urls.get('original') or page.image_urls.get('large')
                if url:
                    image_urls.append(url)
        
        # 下载图片
        downloaded = []
        for i, url in enumerate(image_urls):
            filename = f"{i+1:03d}{Path(url).suffix}"
            filepath = save_dir / filename
            
            if not filepath.exists():
                api.download(url, path=str(save_dir), name=filename)
            
            if filepath.exists():
                downloaded.append(str(filepath))
        
        return {
            "success": True,
            "images": downloaded,
            "title": title,
            "count": len(downloaded),
            "path": str(save_dir)
        }
    
    except Exception as e:
        return {"success": False, "error": str(e)}

