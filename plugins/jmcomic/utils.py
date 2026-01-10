import asyncio
import re
import shutil
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import List
from uuid import uuid4

try:
    from PIL import Image
except Exception:  # pragma: no cover - 仅运行时提示
    Image = None

try:
    import img2pdf  # 占用更小内存的合成方案
except Exception:  # pragma: no cover - 缺依赖时回退 Pillow
    img2pdf = None


def clean_error_text(err: Exception) -> str:
    """
    Remove local filesystem details from error text.
    """
    text = str(err)
    redact_roots = [
        Path(__file__).resolve().parents[2],
        Path.cwd(),
        Path.home(),
    ]
    for root in redact_roots:
        try:
            root_str = str(root)
        except Exception:
            continue
        if root_str:
            text = text.replace(root_str, "")
    cleaned = text.replace("//", "/").strip()
    return cleaned or err.__class__.__name__

def safe_filename(name: str, default: str) -> str:
    """
    Sanitize filename to avoid characters invalid for most filesystems.
    """
    cleaned = re.sub(r'[\\/:*?"<>|]', "_", (name or "").strip())
    cleaned = cleaned.strip(". ")
    return cleaned or default


def gather_images(album_dir: Path) -> List[Path]:
    """
    收集专辑下的图片路径，按路径排序。
    """
    exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
    files = [
        p for p in album_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in exts
    ]
    return sorted(files, key=lambda p: p.as_posix())


def merge_to_pdf(img_paths: List[Path], pdf_path: Path, timing: dict[str, float] | None = None):
    """
    将图片列表合成为 PDF。
    """
    if not img_paths:
        raise RuntimeError("没有找到可以合成 PDF 的图片")

    if img2pdf is None:
        raise RuntimeError("img2pdf 未安装，请先运行: pip install img2pdf")

    direct_exts = {".jpg", ".jpeg", ".png"}
    can_direct = all(p.suffix.lower() in direct_exts for p in img_paths)

    if can_direct:
        merge_start = time.perf_counter()
        with open(pdf_path, "wb") as f:
            f.write(img2pdf.convert([str(p) for p in img_paths]))
        if timing is not None:
            timing["合成PDF"] = time.perf_counter() - merge_start
        return

    # 回退路径：将非 JPG/PNG 的图片先转成 JPG 再封装
    if Image is None:
        raise RuntimeError("Pillow 未安装，无法转换非 JPG/PNG 图片，请先安装 pillow")

    tmp_dir = Path(tempfile.mkdtemp(prefix="jm_pdf_"))
    good_files: List[str] = []
    bad_files: List[str] = []

    convert_start = time.perf_counter()
    for idx, p in enumerate(img_paths, start=1):
        try:
            with Image.open(p) as im:
                out = tmp_dir / f"{idx:05d}.jpg"
                im.convert("RGB").save(out, format="JPEG")
                good_files.append(str(out))
        except Exception:
            bad_files.append(p.name)
    if timing is not None:
        timing["转换格式"] = time.perf_counter() - convert_start

    if not good_files:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise RuntimeError(f"所有图片都无法识别，坏图: {', '.join(bad_files)}")

    if bad_files:
        bad_log = pdf_path.with_suffix(".bad.txt")
        bad_log.write_text("\n".join(bad_files), encoding="utf-8")

    try:
        merge_start = time.perf_counter()
        with open(pdf_path, "wb") as f:
            f.write(img2pdf.convert(good_files))
        if timing is not None:
            timing["合成PDF"] = time.perf_counter() - merge_start
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def merge_long_images(
    img_paths: List[Path],
    album_id: str,
    base_dir: Path,
    batch_size: int = 10,
    max_width: int | None = None,  # 已弃用，保持兼容
    workers: int = 2,
    read_chunk: int = 5,
    io_workers: int | None = None,
    quality: int = 80,
) -> tuple[List[Path], Path]:
    """
    将图片分批合成长图，返回生成的长图路径列表和所在目录。
    仅合并长宽一致的图片；以出现次数最多的尺寸为主尺寸，仅保留主尺寸页面，封面（第一张）例外；后续尺寸不同的（疑似广告）直接丢弃。
    read_chunk/io_workers 控制单次并行读取数量，避免一次性把所有图片读入内存。
    """
    if Image is None:
        raise RuntimeError("Pillow 未安装，无法生成长图，请先安装 pillow")

    output_dir = Path(base_dir) / f"long_{album_id}_{uuid4().hex[:6]}"
    output_dir.mkdir(parents=True, exist_ok=True)
    long_imgs: List[Path] = []

    def _build_long(batch_paths: List[Path], index: int, width: int, height: int) -> Path | None:
        if not batch_paths:
            return None
        total_h = height * len(batch_paths)
        canvas = Image.new("RGB", (width, total_h), (255, 255, 255))

        def _load_and_convert(path: Path):
            try:
                with Image.open(path) as im:
                    # convert 返回新的对象，确保文件句柄及时关闭
                    converted = im.convert("RGB")
                return converted
            except Exception:
                return None

        load_workers = max(1, min(read_chunk, io_workers or read_chunk))
        load_pool = ThreadPoolExecutor(max_workers=load_workers)
        y = 0
        try:
            chunk_size = max(1, read_chunk)
            for i in range(0, len(batch_paths), chunk_size):
                chunk = batch_paths[i : i + chunk_size]
                # 并行读取 + 转换，内存占用被 chunk_size 限制
                imgs = list(load_pool.map(_load_and_convert, chunk))
                for img in imgs:
                    if img is None:
                        y += height
                        continue
                    canvas.paste(img, (0, y))
                    y += img.height
        finally:
            load_pool.shutdown(wait=True)

        out_path = output_dir / f"long_{index:03d}.jpg"
        canvas.save(out_path, format="JPEG", quality=quality)
        return out_path

    executor = ThreadPoolExecutor(max_workers=max(1, workers))
    futures = []

    # 收集尺寸信息
    metas: List[tuple[Path, int, int]] = []
    size_count: dict[tuple[int, int], int] = {}
    for p in img_paths:
        try:
            with Image.open(p) as im:
                sz = (im.width, im.height)
                metas.append((p, *sz))
                size_count[sz] = size_count.get(sz, 0) + 1
        except Exception:
            continue

    if not metas:
        executor.shutdown(wait=True)
        return long_imgs, output_dir

    primary_size = max(size_count.items(), key=lambda kv: kv[1])[0]
    batches: list[tuple[List[Path], tuple[int, int]]] = []
    batch_index = 1

    # 封面（第一张）保留，即便尺寸不同
    first_path, fw, fh = metas[0]
    if (fw, fh) != primary_size:
        batches.append(([first_path], (fw, fh)))

    # 主尺寸列表
    primary_paths = [p for p, w, h in metas if (w, h) == primary_size]

    for i in range(0, len(primary_paths), batch_size):
        batch = primary_paths[i : i + batch_size]
        if batch:
            batches.append((batch, primary_size))

    for paths, sz in batches:
        futures.append(executor.submit(_build_long, paths, batch_index, sz[0], sz[1]))
        batch_index += 1

    try:
        for fut in futures:
            out_path = fut.result()
            if out_path:
                long_imgs.append(out_path)
    finally:
        executor.shutdown(wait=True)

    return long_imgs, output_dir


async def delayed_cleanup(paths: List[Path], delay_seconds: int):
    """
    延迟删除下载目录（可传入多个路径），避免上传后立即被删。
    """
    try:
        await asyncio.sleep(delay_seconds)
        seen = set()
        unique_paths = []
        for p in paths:
            key = p.resolve()
            if key in seen:
                continue
            seen.add(key)
            unique_paths.append(p)

        for path in unique_paths:
            try:
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=False)
                elif path.exists():
                    path.unlink()
            except Exception as e:
                print(f"[jmcomic] 清理 {path.name} 失败：{clean_error_text(e)}")
    except Exception as e:
        print(f"[jmcomic] 清理任务失败：{clean_error_text(e)}")
