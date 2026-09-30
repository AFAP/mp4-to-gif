"""engine 的回归测试。

这些用例是踩过坑之后钉下来的，改动抽帧／调色板／抠像时先跑一遍：

    python -m pytest tests -q

其中 test_tail_frames_survive_conversion 是端到端用例，需要 ffmpeg（找不到就跳过）。

    python -m pytest tests -q -k "not conversion"      # 只跑快的
"""
import os
import shutil
import subprocess
import tempfile

import numpy as np
import pytest
from PIL import Image

import engine

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------
# 抽帧：必须覆盖整段，首尾都在
# ---------------------------------------------------------------------------

def test_subsample_keeps_both_ends():
    """73 帧 @24fps 抽任何一档，都必须包含源视频的第一帧和最后一帧。

    旧实现是 frames[::step][:n]，从头截断：10fps 只剩前 59 帧，结尾 0.6 秒动作
    整段不见，GIF 播完跳回第一帧时会顿一下。
    """
    frames = list(range(73))
    for fps in (12, 10, 8, 6):
        got = engine._subsample(frames, fps, 24.0)
        assert got[0] == 0, f'{fps}fps 丢了第一帧'
        assert got[-1] == 72, f'{fps}fps 丢了最后一帧，止于源帧 {got[-1] + 1}'


def test_subsample_frame_count():
    """帧数要跟着「源帧数 × 目标帧率 ÷ 源帧率」走。"""
    frames = list(range(73))
    for fps, want in ((12, 36), (10, 30), (8, 24), (6, 18)):
        assert len(engine._subsample(frames, fps, 24.0)) == want


def test_subsample_is_monotonic():
    got = engine._subsample(list(range(73)), 10, 24.0)
    assert got == sorted(got)
    assert len(set(got)) == len(got)


def test_subsample_edge_cases():
    assert engine._subsample([], 10, 24.0) == []
    assert engine._subsample(list(range(3)), 0, 24.0) == list(range(3))     # fps=0 原样返回
    assert engine._subsample(list(range(5)), 24, 24.0) == list(range(5))    # n >= 源帧数
    assert engine._subsample(list(range(5)), 48, 24.0) == list(range(5))    # n > 源帧数
    assert engine._subsample(list(range(10)), 1, 24.0) == [0]               # 只留一帧时取第一帧


# ---------------------------------------------------------------------------
# 调色板：每一项都要提到饱和
# ---------------------------------------------------------------------------

def test_boost_palette_touches_every_color():
    """透明占位色是调用方在 boost 之后才 append 的，所以这里不能跳过最后一项。"""
    pal = [200, 100, 100, 0, 200, 0, 10, 20, 30, 240, 240, 10]
    out = engine.boost_palette(pal, 2.0)
    assert len(out) == len(pal)
    for i in range(0, len(pal), 3):
        assert out[i:i + 3] != pal[i:i + 3], f'第 {i // 3} 个颜色没被处理'


def test_boost_palette_identity_and_clip():
    pal = [10, 20, 30, 200, 200, 200]
    assert engine.boost_palette(pal, 1.0) == pal
    out = engine.boost_palette(pal, 3.0)
    assert len(out) == len(pal)
    assert min(out) >= 0 and max(out) <= 255


# ---------------------------------------------------------------------------
# 抠像：只抠与边框连通的近白区域
# ---------------------------------------------------------------------------

def _canvas(w=60, h=60):
    """白底 + 中间一个黑色方块。"""
    rgb = np.full((h, w, 3), 255, dtype=np.uint8)
    rgb[20:40, 20:40] = 0
    return rgb


def test_key_background_removes_border_white():
    opaque = engine.key_background(_canvas(), 244)
    assert not opaque[0, 0], '角落的白色背景应该被抠掉'
    assert opaque[30, 30], '黑色方块必须留下'


def test_key_background_keeps_enclosed_white():
    """被轮廓包住的白色（白身鸽子、白云、白字描边）不能抠掉。"""
    rgb = _canvas()
    rgb[24:36, 24:36] = 255           # 黑方块里挖一块白，与边框不连通
    opaque = engine.key_background(rgb, 244)
    assert opaque[30, 30], '被包住的白块被误抠了'


def test_key_background_all_white_is_fully_transparent():
    opaque = engine.key_background(np.full((20, 20, 3), 250, dtype=np.uint8), 244)
    assert not opaque.any()


def test_key_background_no_near_white_keeps_everything():
    opaque = engine.key_background(np.full((20, 20, 3), 100, dtype=np.uint8), 244)
    assert opaque.all()


# ---------------------------------------------------------------------------
# 白描边
# ---------------------------------------------------------------------------

def test_keyline_ring_is_white(tmp_path):
    src = tmp_path / 'f.png'
    Image.fromarray(_canvas(200, 200), 'RGB').save(src)
    opt = engine.Options(out_width=60, out_height=60, keyline=2)
    rgba = np.asarray(engine.frame_to_rgba(str(src), (60, 60), opt))
    alpha = rgba[:, :, 3] > 0
    assert not alpha.all(), '背景应该被抠掉'
    # 描边那一圈必须是纯白的不透明像素：原来这里是背景白，会被抠成透明
    ring = alpha & (rgba[:, :, :3].min(axis=2) >= 250)
    assert ring.sum() > 0, '没有重建出白色描边'


def test_keyline_zero_keeps_hard_edge(tmp_path):
    src = tmp_path / 'f.png'
    Image.fromarray(_canvas(200, 200), 'RGB').save(src)
    opt = engine.Options(out_width=60, out_height=60, keyline=0)
    rgba = np.asarray(engine.frame_to_rgba(str(src), (60, 60), opt))
    assert (rgba[:, :, 3] > 0).any()


# ---------------------------------------------------------------------------
# 版本号
# ---------------------------------------------------------------------------

def test_version_file_is_semver():
    p = os.path.join(REPO, 'VERSION')
    assert os.path.exists(p), 'VERSION 文件是版本的唯一来源，不能少'
    v = open(p, encoding='utf-8').read().strip()
    parts = v.split('.')
    assert len(parts) == 3 and all(x.isdigit() for x in parts), f'VERSION 格式不对：{v}'


def test_gui_reports_the_same_version():
    try:
        import gui
    except ImportError as e:                      # 没有 tkinter 的环境直接跳过
        pytest.skip(f'import gui failed: {e}')
    want = open(os.path.join(REPO, 'VERSION'), encoding='utf-8').read().strip()
    assert gui.app_version() == want


# ---------------------------------------------------------------------------
# 端到端：GIF 必须播到源视频的最后一帧
# ---------------------------------------------------------------------------

def _make_test_video(path: str, frames: int = 73, fps: int = 24) -> bool:
    """白底 + 从左往右匀速移动的黑方块，正好 frames 帧。

    帧图用 PIL 画好再交给 ffmpeg 编码：lavfi 的 color 源没有时间戳，
    drawbox 里带 t 的表达式会求值成 NaN，方块根本画不出来（静默失败，
    编码出来是一段纯白视频，测试就假失败了）。
    """
    try:
        ff = engine.find_ffmpeg()
    except RuntimeError:
        return False

    work = tempfile.mkdtemp(prefix='moving_')
    try:
        blank = np.full((320, 320, 3), 255, dtype=np.uint8)
        for i in range(frames):
            rgb = blank.copy()
            x = int(round(10 + (260 / (frames - 1)) * i))
            rgb[140:180, x:x + 40] = 0
            Image.fromarray(rgb, 'RGB').save(os.path.join(work, f'{i:04d}.png'))
        cmd = [ff, '-y', '-loglevel', 'error',
               '-framerate', str(fps), '-i', os.path.join(work, '%04d.png'),
               '-frames:v', str(frames), '-pix_fmt', 'yuv420p',
               '-c:v', 'libx264', '-crf', '18', path]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return False
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return os.path.exists(path) and os.path.getsize(path) > 0


def _on_white(im):
    a = np.asarray(im.convert('RGB'), dtype=np.float32)
    al = np.asarray(im.getchannel('A'), dtype=np.float32)[:, :, None] / 255
    return a * al + 255.0 * (1 - al)


def _gif_frames(path):
    im = Image.open(path)
    out = []
    for i in range(im.n_frames):
        im.seek(i)
        out.append(_on_white(im.convert('RGBA')))
    return out


def test_tail_frames_survive_conversion(tmp_path):
    """整条链路：73 帧源 → 10fps GIF 30 帧，最后一帧要对上源视频的最后一帧。

    这是这次 bug 的正面回归：旧实现下 GIF 停在源帧 59（方块还没走到右边），
    末帧离源帧 59 更近；修好之后应该离源帧 73 更近。
    """
    mp4 = str(tmp_path / 'moving.mp4')
    if not _make_test_video(mp4):
        pytest.skip('本机没有可用的 ffmpeg（或无法编码测试视频）')

    src = engine.extract_frames(mp4, str(tmp_path / 'src'), 24.0, (240, 240))
    assert len(src) == 73, f'测试视频应该正好 73 帧，拿到 {len(src)}'
    src_small = [np.asarray(Image.open(p).convert('RGB'), dtype=np.float32) for p in src]

    opt = engine.Options(out_width=240, out_height=240, limit_kb=0,
                         palette_sat=1.35, ladder=((10, 64),))
    r = engine.convert(mp4, str(tmp_path / 'out'), opt)
    assert r.ok, r.message
    assert (r.fps, r.frames) == (10, 30), f'应该落在 10fps/30 帧，实际 {r.fps}/{r.frames}'

    got = _gif_frames(r.out)
    assert len(got) == 30

    first_gap = float(np.abs(got[0] - src_small[0]).mean())
    last_at_end = float(np.abs(got[-1] - src_small[72]).mean())
    last_at_59 = float(np.abs(got[-1] - src_small[58]).mean())
    assert last_at_end < last_at_59, (
        'GIF 的最后一帧没对上源视频的最后一帧：'
        f'离源帧73 {last_at_end:.2f}，离源帧59 {last_at_59:.2f}（尾巴被截掉了？）')
    assert first_gap < last_at_59, f'GIF 第一帧和源帧 1 差太多：{first_gap:.2f}'
