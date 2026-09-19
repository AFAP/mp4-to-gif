"""MP4 → 透明 GIF / WebP 转换引擎。

把「把 mp4 做成透明动图」这套经验固化下来，关键决策都是实测出来的：

1. 抠像只抠**与画面边框连通**的近白区域。角色身上的白色部件（白身鸽子、白云、
   白装甲、白墙）被轮廓或阴影包住，不与边框连通，因此不会被误抠。
2. 阈值不能太高：生成的视频背景白会漂移（有的帧 250–255，有的漂到 247–250），
   取 244 兼顾两边；太高会漏抠，太低会吃进浅色主体。
3. 抠像要在高分辨率做（超采样），掩膜降采样后再按 0.5 阈值硬化 —— GIF 只有
   1-bit 透明，这样边缘才是亚像素平滑的，否则是锯齿。
4. 边缘色彩外推：把透明区的 RGB 换成最近的不透明像素颜色，避免缩放时把白底
   混进黑色描边形成白边。
5. 白色描边会被一起抠掉（描边也是白的、与背景连通），需要沿轮廓向外重建一圈纯白，
   否则深色背景下深色描边和文字看不清。
6. **绝对不要加像素级模糊**：会把黑色描边糊成碎点，线稿直接毁掉。
7. 颜色发闷不要靠加色数/降帧率解决 —— 用**只改调色板**的方式提饱和度：索引不变，
   体积零增长。实测 1.30 最合适，1.5 偏橘。
8. 体积上限下，帧率和色数只能二选一：阶梯从高帧率往下试，直到装进上限。
   时长越短能负担的色数越高（3 秒能上 64 色，5 秒只能 24 色）。
9. WebP 支持真 alpha 和 24 位色，同体积下质量远好于 GIF（约 10 倍效率）。
10. 灰调素材（石头、白墙）靠**提亮度**而不是提饱和度，而且提亮后大片像素趋白，
    文件反而更小。
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageEnhance
from scipy import ndimage

# ----------------------------------------------------------------------------
# ffmpeg 定位
# ----------------------------------------------------------------------------

_FFMPEG_CACHE: str | None = None


def find_ffmpeg() -> str:
    """按优先级找 ffmpeg：程序目录 → PATH → imageio-ffmpeg 自带。"""
    global _FFMPEG_CACHE
    if _FFMPEG_CACHE:
        return _FFMPEG_CACHE

    here = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, 'frozen', False) else __file__))
    for cand in (
        os.path.join(here, 'ffmpeg.exe'),
        os.path.join(getattr(sys, '_MEIPASS', here), 'ffmpeg.exe'),
        shutil.which('ffmpeg'),
        shutil.which('ffmpeg.exe'),
    ):
        if cand and os.path.exists(cand):
            _FFMPEG_CACHE = cand
            return cand

    try:
        import imageio_ffmpeg
        cand = imageio_ffmpeg.get_ffmpeg_exe()
        if cand and os.path.exists(cand):
            _FFMPEG_CACHE = cand
            return cand
    except Exception:
        pass
    raise RuntimeError('找不到 ffmpeg，请把它放在程序同一目录，或加入 PATH。')


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   check=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


# ----------------------------------------------------------------------------
# 参数
# ----------------------------------------------------------------------------

# 体积阶梯：帧率优先，色数不低于 20。想更艳就整体上移色数，想更流畅就上移帧率。
LADDER_FPS = [
    (12, 32), (12, 24), (10, 32), (10, 24),
    (8, 32), (8, 24), (6, 32), (6, 24),
]
LADDER_COLOR = [
    (10, 128), (10, 96), (10, 64), (8, 128), (8, 96),
    (8, 64), (6, 128), (6, 96), (6, 64), (6, 48), (6, 32),
]


@dataclass
class Options:
    out_width: int = 240
    out_height: int = 240
    limit_kb: float = 500.0            # <=0 表示不限
    prefer: str = 'fps'                # 'fps' 帧率优先 | 'color' 色彩优先
    threshold: int = 244               # 近白判据 min(R,G,B) >= threshold
    keyline: int = 2                   # 重建的白色描边宽度（像素）
    work_long_edge: int = 768          # 抠像时的高分辨率长边
    palette_mode: str = 'full'         # full | opaque
    palette_sat: float = 1.30          # 只改调色板的饱和度（体积零增长）
    brightness: float = 1.0            # 像素级提亮（灰调素材用）
    saturation: float = 1.0            # 像素级提饱和度
    contrast: float = 1.0
    keep_background: bool = False      # True = 不抠底，保留原背景
    webp_quality: int = 80
    webp_fps: int = 0                  # 0 = 用源帧率（上限 24）


@dataclass
class Result:
    src: str
    out: str = ''
    ok: bool = False
    message: str = ''
    kb: float = 0.0
    frames: int = 0
    fps: int = 0
    colors: int = 0
    detail: dict = field(default_factory=dict)


# ----------------------------------------------------------------------------
# 抽帧与抠像
# ----------------------------------------------------------------------------

def find_ffprobe() -> str | None:
    """ffprobe 通常和 ffmpeg 同目录；imageio-ffmpeg 不带 ffprobe，此时返回 None。"""
    ff = find_ffmpeg()
    cand = os.path.join(os.path.dirname(ff), 'ffprobe' + ('.exe' if os.name == 'nt' else ''))
    if os.path.exists(cand):
        return cand
    return shutil.which('ffprobe')


def probe(mp4: str) -> dict:
    """读视频基本信息。优先 ffprobe，没有就用 ffmpeg 的 stderr 解析。"""
    import re
    info = {'width': 0, 'height': 0, 'fps': 24.0, 'frames': 0, 'duration': 0.0}
    fp = find_ffprobe()
    if fp:
        try:
            out = subprocess.run(
                [fp, '-v', 'error', '-select_streams', 'v:0',
                 '-show_entries', 'stream=width,height,r_frame_rate,nb_frames',
                 '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1', mp4],
                capture_output=True, text=True,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout
            d = {}
            for line in out.splitlines():
                if '=' in line:
                    k, v = line.split('=', 1)
                    d[k.strip()] = v.strip()
            num, _, den = d.get('r_frame_rate', '24/1').partition('/')
            info.update(width=int(d.get('width', 0) or 0), height=int(d.get('height', 0) or 0),
                        fps=(float(num) / float(den or 1)) if num else 24.0,
                        frames=int(d.get('nb_frames', 0) or 0),
                        duration=float(d.get('duration', 0) or 0))
            if info['width']:
                return info
        except Exception:
            pass

    # 兜底：解析 ffmpeg -i 的输出
    try:
        ff = find_ffmpeg()
        txt = subprocess.run([ff, '-hide_banner', '-i', mp4], capture_output=True, text=True,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stderr
        m = re.search(r'Duration:\s*(\d+):(\d+):(\d+\.?\d*)', txt)
        if m:
            h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
            info['duration'] = h * 3600 + mi * 60 + s
        m = re.search(r'Video:.*?,\s*(\d+)x(\d+)', txt)
        if m:
            info['width'], info['height'] = int(m.group(1)), int(m.group(2))
        m = re.search(r'(\d+(?:\.\d+)?)\s*fps', txt)
        if m:
            info['fps'] = float(m.group(1))
        if info['duration'] and info['fps']:
            info['frames'] = int(round(info['duration'] * info['fps']))
    except Exception:
        pass
    return info


def extract_frames(mp4: str, out_dir: str, fps: float, size: tuple[int, int]) -> list[str]:
    """按目标帧率和尺寸抽帧（lanczos）。"""
    ff = find_ffmpeg()
    shutil.rmtree(out_dir, ignore_errors=True)
    os.makedirs(out_dir, exist_ok=True)
    w, h = size
    _run([ff, '-y', '-loglevel', 'error', '-i', mp4,
          '-vf', f'fps={fps},scale={w}:{h}:flags=lanczos',
          os.path.join(out_dir, 'f_%05d.png')])
    return sorted(glob.glob(os.path.join(out_dir, 'f_*.png')))


def key_background(rgb: np.ndarray, thresh: int) -> np.ndarray:
    """返回不透明掩膜：只抠与画面边框连通的近白区域。"""
    mn = rgb.min(axis=2)
    near_white = mn >= thresh
    if not near_white.any():
        return np.ones(mn.shape, dtype=bool)
    lab, n = ndimage.label(near_white)
    if n == 0:
        return np.ones(mn.shape, dtype=bool)
    border = np.unique(np.concatenate([lab[0, :], lab[-1, :], lab[:, 0], lab[:, -1]]))
    border = border[border != 0]
    return ~np.isin(lab, border)


def extend_colors(rgb: np.ndarray, opaque: np.ndarray) -> np.ndarray:
    """把透明像素的 RGB 换成最近的不透明像素颜色，避免缩放时把白底混进描边。"""
    if opaque.all():
        return rgb
    _, idx = ndimage.distance_transform_edt(~opaque, return_indices=True)
    return rgb[idx[0], idx[1]]


def dilate(mask: np.ndarray, iterations: int) -> np.ndarray:
    out = mask.copy()
    for _ in range(max(0, iterations)):
        p = np.pad(out, 1, mode='edge')
        out = (p[1:-1, 1:-1] | p[:-2, 1:-1] | p[2:, 1:-1] |
               p[1:-1, :-2] | p[1:-1, 2:] | p[:-2, :-2] |
               p[:-2, 2:] | p[2:, :-2] | p[2:, 2:])
    return out


def frame_to_rgba(path: str, size: tuple[int, int], opt: Options,
                  transparent: bool = True) -> Image.Image:
    """一帧 → 目标尺寸的 RGBA。"""
    ow, oh = size
    rgb = np.asarray(Image.open(path).convert('RGB'))
    if transparent:
        opaque = key_background(rgb, opt.threshold)
        filled = extend_colors(rgb, opaque)
        small_rgb = np.asarray(Image.fromarray(filled, 'RGB').resize((ow, oh), Image.LANCZOS))
        small_mask = np.asarray(
            Image.fromarray((opaque * 255).astype(np.uint8), 'L').resize((ow, oh), Image.LANCZOS),
            dtype=np.float32) / 255.0
        alpha = np.where(small_mask >= 0.5, 255, 0).astype(np.uint8)
        if opt.keyline > 0:
            solid = alpha > 0
            ring = dilate(solid, opt.keyline) & ~solid
            small_rgb = small_rgb.copy()
            small_rgb[ring] = 255
            alpha = alpha.copy()
            alpha[ring] = 255
    else:
        small_rgb = np.asarray(Image.open(path).convert('RGB').resize((ow, oh), Image.LANCZOS))
        alpha = np.full((oh, ow), 255, dtype=np.uint8)
    return Image.fromarray(np.dstack([small_rgb, alpha]), 'RGBA')


def _grade(im: Image.Image, opt: Options) -> Image.Image:
    rgb = im.convert('RGB')
    if opt.brightness != 1.0:
        rgb = ImageEnhance.Brightness(rgb).enhance(opt.brightness)
    if opt.saturation != 1.0:
        rgb = ImageEnhance.Color(rgb).enhance(opt.saturation)
    if opt.contrast != 1.0:
        rgb = ImageEnhance.Contrast(rgb).enhance(opt.contrast)
    return Image.merge('RGBA', (*rgb.split(), im.getchannel('A')))


# ----------------------------------------------------------------------------
# 调色板
# ----------------------------------------------------------------------------

def build_palette_full(prepped: list[Image.Image], colors: int) -> Image.Image:
    """整图统计调色板：体积小、但透明区占掉色位，颜色偏闷。"""
    w, h = prepped[0].size
    cat = Image.new('RGB', (w, h * len(prepped)))
    for k, im in enumerate(prepped[:64]):
        cat.paste(im, (0, k * h))
    return cat.quantize(colors=colors)


def build_palette_opaque(masks: list[np.ndarray], prepped: list[Image.Image], colors: int) -> Image.Image:
    """只统计不透明像素：色差小一半，但体积约 +40%。"""
    rng = np.random.default_rng(0)
    step = max(1, len(prepped) // 16)
    samples = []
    for im, m in list(zip(prepped, masks))[::step]:
        px = np.asarray(im).reshape(-1, 3)[~m.reshape(-1)]
        if len(px) == 0:
            continue
        if len(px) > 20000:
            px = px[rng.choice(len(px), 20000, replace=False)]
        samples.append(px)
    if not samples:
        return build_palette_full(prepped, colors)
    allpx = np.concatenate(samples)
    side = int(np.ceil(np.sqrt(len(allpx))))
    allpx = np.vstack([allpx, np.zeros((side * side - len(allpx), 3), np.uint8)])
    return Image.fromarray(allpx.reshape(side, side, 3), 'RGB').quantize(colors=colors)


def boost_palette(palette: list[int], k: float) -> list[int]:
    """只给调色板提饱和度，索引不变 —— 体积零增长。"""
    if k == 1.0:
        return palette
    arr = np.asarray(palette, dtype=np.float32).reshape(-1, 3)
    n = max(1, len(arr) - 1)          # 最后一项是透明占位
    head = arr[:n]
    gray = head.mean(axis=1, keepdims=True)
    arr[:n] = np.clip(gray + (head - gray) * k, 0, 255)
    return arr.astype(np.uint8).flatten().tolist()


# ----------------------------------------------------------------------------
# 编码
# ----------------------------------------------------------------------------

def _subsample(frames: list, fps: int, base_fps: float) -> list:
    step = max(1, int(round(base_fps / fps))) if fps else 1
    n = int(round(len(frames) * fps / base_fps)) if fps else len(frames)
    return frames[::step][:max(1, n)]


def encode_gif(frames: list[Image.Image], out: str, fps: int, opt: Options) -> float:
    prepped = [_grade(f, opt) for f in frames]
    masks = [np.asarray(f.getchannel('A')) < 128 for f in prepped]
    if opt.palette_mode == 'opaque':
        gpal = build_palette_opaque(masks, prepped, 255)
    else:
        gpal = build_palette_full(prepped, 255)
    palette = boost_palette(gpal.getpalette()[:255 * 3], opt.palette_sat) + [255, 0, 255]
    pils = []
    for im, m in zip(prepped, masks):
        q = im.convert('RGB').quantize(palette=gpal, dither=Image.Dither.NONE)
        idx = np.asarray(q).copy()
        idx[m] = 255
        p = Image.fromarray(idx, 'P')
        p.putpalette(palette)
        pils.append(p)
    pils[0].save(out, save_all=True, append_images=pils[1:], transparency=255,
                 duration=int(round(1000 / fps)), loop=0, optimize=True, disposal=2)
    return os.path.getsize(out) / 1024


def encode_webp(frames: list[Image.Image], out: str, fps: int, opt: Options) -> float:
    prepped = [_grade(f, opt) for f in frames]
    prepped[0].save(out, save_all=True, append_images=prepped[1:], format='WEBP',
                    lossless=False, quality=opt.webp_quality, method=4,
                    duration=int(round(1000 / fps)), loop=0)
    return os.path.getsize(out) / 1024


# ----------------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------------

def convert(mp4: str, out_dir: str, opt: Options, fmt: str = 'gif',
            log=lambda s: None, base_fps: float | None = None) -> Result:
    stem = os.path.splitext(os.path.basename(mp4))[0]
    ext = '.webp' if fmt == 'webp' else '.gif'
    out = os.path.join(out_dir, stem + ext)
    os.makedirs(out_dir, exist_ok=True)

    info = probe(mp4)
    src_fps = base_fps or info['fps'] or 24.0
    long_edge = max(opt.out_width, opt.out_height)
    work = (max(2, int(round(opt.work_long_edge * opt.out_width / long_edge / 2) * 2)),
            max(2, int(round(opt.work_long_edge * opt.out_height / long_edge / 2) * 2)))

    tmp = tempfile.mkdtemp(prefix='mp4gif_')
    try:
        raw = extract_frames(mp4, tmp, src_fps, work)
        if not raw:
            return Result(mp4, ok=False, message='抽帧失败，视频可能损坏或没有视频轨')
        log(f'  抽帧 {len(raw)} 帧 @{work[0]}x{work[1]}')
        keyed = [frame_to_rgba(p, (opt.out_width, opt.out_height), opt,
                               transparent=not opt.keep_background) for p in raw]

        if fmt == 'webp':
            fps = opt.webp_fps or int(round(min(src_fps, 24)))
            frames = _subsample(keyed, fps, src_fps)
            kb = encode_webp(frames, out, fps, opt)
            return Result(mp4, out=out, ok=True, kb=kb, frames=len(frames), fps=fps,
                          colors=0, message=f'WebP {len(frames)}帧 {fps}fps q{opt.webp_quality}')

        ladder = LADDER_COLOR if opt.prefer == 'color' else LADDER_FPS
        tried = []
        for fps, colors in ladder:
            frames = _subsample(keyed, fps, src_fps)
            kb = _encode_gif_colors(frames, out, fps, opt, colors)
            tried.append((fps, colors, round(kb, 1)))
            if opt.limit_kb <= 0 or kb <= opt.limit_kb:
                return Result(mp4, out=out, ok=True, kb=kb, frames=len(frames), fps=fps,
                              colors=colors, message=f'GIF {len(frames)}帧 {fps}fps {colors}色',
                              detail={'tried': tried})
        fps, colors, kb = tried[-1]
        frames = _subsample(keyed, fps, src_fps)
        return Result(mp4, out=out, ok=True, kb=kb, frames=len(frames), fps=fps, colors=colors,
                      message=f'GIF {len(frames)}帧 {fps}fps {colors}色（已到阶梯末端仍超限）',
                      detail={'tried': tried})
    except Exception as e:                       # noqa: BLE001
        return Result(mp4, ok=False, message=f'{type(e).__name__}: {e}')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _encode_gif_colors(frames: list[Image.Image], out: str, fps: int, opt: Options,
                       colors: int) -> float:
    """按指定色数编码 GIF。"""
    prepped = [_grade(f, opt) for f in frames]
    masks = [np.asarray(f.getchannel('A')) < 128 for f in prepped]
    if opt.palette_mode == 'opaque':
        gpal = build_palette_opaque(masks, prepped, colors)
    else:
        gpal = build_palette_full(prepped, colors)
    palette = boost_palette(gpal.getpalette()[:colors * 3], opt.palette_sat) + [255, 0, 255]
    pils = []
    for im, m in zip(prepped, masks):
        q = im.convert('RGB').quantize(palette=gpal, dither=Image.Dither.NONE)
        idx = np.asarray(q).copy()
        idx[m] = colors
        p = Image.fromarray(idx, 'P')
        p.putpalette(palette)
        pils.append(p)
    pils[0].save(out, save_all=True, append_images=pils[1:], transparency=colors,
                 duration=int(round(1000 / fps)), loop=0, optimize=True, disposal=2)
    return os.path.getsize(out) / 1024


def check_gif(path: str) -> dict:
    """校验成品：透明通道是否可用、逐帧透明比例是否稳定。"""
    im = Image.open(path)
    n = getattr(im, 'n_frames', 1)
    tidx = im.info.get('transparency') if im.format == 'GIF' else None
    fr = []
    for i in range(n):
        im.seek(i)
        a = np.asarray(im.convert('RGBA').getchannel('A'))
        fr.append(float((a < 128).mean() * 100))
    stable = (max(fr) - min(fr)) < 25 if fr else False
    return {'frames': n, 'transparency': tidx, 'min_bg': min(fr) if fr else 0,
            'max_bg': max(fr) if fr else 0, 'transparent': (min(fr) > 5) if fr else False,
            'stable': stable, 'size': im.size}
