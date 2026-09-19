# MP4 转透明 GIF · MP4 to Transparent GIF

<div align="center">
  <b>中文</b> · <a href="README.en.md">English</a>
</div>

<div align="center">

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![GitHub release](https://img.shields.io/github/v/release/AFAP/mp4-to-gif?label=release)](https://github.com/AFAP/mp4-to-gif/releases/latest)
[![Build & Release](https://github.com/AFAP/mp4-to-gif/actions/workflows/release.yml/badge.svg)](https://github.com/AFAP/mp4-to-gif/actions/workflows/release.yml)

</div>

> **把 mp4 一键转成带透明背景的 GIF / WebP。**
> 单文件 exe，内置 ffmpeg 与 Python 运行时，双击即用；抠白底、保边缘、卡体积上限这一整套实测经验都固化在代码里。

![界面预览](screenshot/ui.png)

> 下图是用本工具把示例素材 `samples/yuejianglou.mp4` 转出来的透明 GIF（`screenshot/demo.gif`，在深色背景下看才是透明效果）：

![效果示例](screenshot/demo.gif)

## 1. 它解决了什么问题

把 AI 生成的动画视频做成聊天表情或贴纸，中间隔着一堆手工活：视频背景是白的，但 GIF 只支持 1-bit 透明 —— 直接抠会留白边、边缘锯齿；微信表情单张限 500 KB，降帧率还是降色数要反复试；网上现成的转换工具要么不处理透明，要么把线稿直接糊掉。

这个工具把整条链路做成一个双击即用的程序：

```
 mp4 素材（白底动画）
        │
        │  ① ffmpeg 抽帧 + lanczos 缩放（长边 768 抠像，再降采样到目标尺寸）
        ▼
   近白连通域抠像 ── 只抠「与画面边框连通」的白色，主体上的白色部件不受影响
        │
        │  ② 边缘色彩外推：透明区的 RGB 换成最近的不透明像素，避免缩放把白底混进描边
        │  ③ 白描边重建：沿轮廓向外补一圈纯白，深色背景下线稿才看得清
        ▼
   调色板量化（可选「只改调色板」提饱和，体积零增长）
        │
        │  ④ 体积阶梯：从高帧率往下试，装进体积上限为止
        ▼
   GIF（1-bit 透明 / 256 色）   或   WebP（真 alpha / 全彩）
```

## 2. 功能特性

- ✅ 只抠与画面边框连通的白色：主体上的白色部件（白身、白云、白墙、白装甲）不会被误抠
- ✅ 超采样抠像 + 掩膜硬化：GIF 只有 1-bit 透明，边缘依然平滑，没有锯齿
- ✅ 边缘色彩外推 + 白描边重建：深色聊天背景下没有白边，线稿和文字不糊
- ✅ 体积阶梯自动降档：填了 500 KB 就自动在帧率和色数之间找组合，不用手调
- ✅ 调色板提饱和：颜色发闷时不用加色数，体积零增长地救回来
- ✅ GIF / WebP 双格式：GIF 兼容性最好，WebP 支持真透明 + 全彩，同体积画质好得多
- ✅ 图形界面 + 命令行双模式：界面可多选文件或整个文件夹批量转，`--cli` 方便脚本调用
- ✅ 开箱即用：ffmpeg 与 Python 运行时都打包在这一个 exe 里，目标机器什么都不用装（代价是体积约 74 MB，原因见「4. 快速开始」）
- ✅ 转换后自动校验：帧数、透明索引、逐帧背景占比，日志里直接标通过 / 注意
- ✅ 纯本地处理：不联网、不上传素材、不收集任何数据

## 3. 目录结构

```
mp4-to-gif/
├── README.md                中文说明（主文档）
├── README.en.md             English readme
├── LICENSE                  MIT
├── requirements.txt         依赖（全量锁版，CI 按此安装）
├── .github/workflows/
│   └── release.yml          打 tag 自动构建 + 发布 Release
├── docs/
│   ├── usage.md             使用说明（构建时随产物一起发布）
│   └── build.md             构建、打包与 CI 说明
├── screenshot/
│   ├── ui.png               界面预览
│   └── demo.gif             效果示例（本工具的实际输出）
├── samples/
│   └── yuejianglou.mp4      示例素材（CI 冒烟测试用）
├── scripts/
│   └── build.ps1            一条命令从源码打到单文件 exe（纯 ASCII）
└── src/
    ├── engine.py            转换引擎：抽帧、抠像、调色板、阶梯、编码（经验都在注释里）
    ├── gui.py               图形界面（tkinter）+ 命令行模式（--cli）
    └── launcher.cs          单文件自解压启动器
```

`dist/`、`build/`、`.venv/` 都是本地产物，已在 `.gitignore` 中忽略，仓库里不提交任何 exe。

## 4. 快速开始

### 下载即用（推荐）

打开 [Releases](https://github.com/AFAP/mp4-to-gif/releases/latest) 页面下载 `mp4togif.exe`（约 74 MB），双击就能用。

> **为什么有 74 MB？** 为了方便使用，程序把 ffmpeg（解压后约 87 MB）和 Python 运行时（含 numpy / scipy / Pillow）一起打包进了这一个 exe。换来的是目标机器**不需要装 Python、不需要装 ffmpeg、也不需要联网下载任何组件** —— 拷到任何一台 Windows 上双击就能跑。这是用体积换省事。

一行命令下载（Windows PowerShell）：

```powershell
irm "https://github.com/AFAP/mp4-to-gif/releases/latest/download/mp4togif.exe" -OutFile "mp4togif.exe"
```

校验下载完整性：

```powershell
Get-FileHash .\mp4togif.exe -Algorithm SHA256
```

把结果和 Release 页面上 `.sha256` 文件里的值对一下即可。

### 从源码构建

```powershell
git clone https://github.com/AFAP/mp4-to-gif.git
cd mp4-to-gif
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File scripts\build.ps1
```

产物在 `dist\mp4togif.exe`。环境要求与原理见 [docs/build.md](docs/build.md)。

## 5. 使用说明

1. 双击 `mp4togif.exe`
2. 点「选择 mp4 文件…」或「选择文件夹…」把要转的视频选进来（可多选）
3. 选尺寸预设，或直接填宽 × 高
4. 填体积上限（微信表情填 `500`；不限体积填 `0`）
5. 选输出格式：`GIF` 兼容性最好；`WebP` 支持真透明 + 全彩，同体积画质明显更好（推荐）
6. 点「开始转换」，日志区实时显示进度：
   - 绿色 `✓` 成功
   - 红色 `✗` 失败
   - 橙色 `!` 需要注意（例如到了阶梯末端仍超限）
7. 输出默认放在每个视频自己所在目录下的 `gif\` 或 `webp\` 子目录，也可以在「4 保存到」里指定统一目录

**首次运行**会在 exe 旁边解压出一个 `mp4togif_运行文件\` 文件夹，之后每次启动直接复用、不再重复解压（第二次起启动很快）。那个文件夹是程序自己的运行文件，**不要删**。

> 建议放在有写权限的位置（桌面、文档、U 盘都行）。如果放在 `C:\Program Files` 这类只读位置，程序会自动把运行文件放到 `%LOCALAPPDATA%\mp4togif` 下，也能用，只是每次启动多等几秒。

界面上的「3 高级」默认收起，点「展开设置」才展开 —— 里面的参数一般不用动，每一行右侧的灰色小字写的就是这一项的调法。

完整版说明（含全部调参经验与排错）见 **[docs/usage.md](docs/usage.md)**。

## 6. 命令行速查

```
mp4togif.exe --cli <文件或文件夹> [选项]
```

| 参数 | 说明 |
|---|---|
| `--cli` | 进入命令行模式，后面跟文件或文件夹（必需） |
| `--out <目录>` | 输出目录（默认视频同级的 `gif\` 或 `webp\`） |
| `--size 240x240` | 输出尺寸 |
| `--limit 500` | 体积上限 KB（`0` = 不限） |
| `--fmt gif\|webp` | 输出格式 |
| `--prefer fps\|color` | 优先保帧率还是保色彩 |
| `--brightness 1.1` | 提亮 |
| `--saturation 1.1` | 提饱和度 |
| `--palette-sat 1.3` | 调色板饱和度（体积零增长） |
| `--threshold 244` | 抠像阈值 |
| `--keyline 2` | 白描边宽度（像素） |
| `--webp-quality 80` | WebP 质量 |
| `--keep-bg` | 不抠底、保留原背景 |
| `--log <文件>` | 日志文件（默认输出目录下的 `_转换日志.txt`） |

例：

```powershell
.\mp4togif.exe --cli D:\videos --out D:\out --size 240x240 --limit 500 --brightness 1.1
```

退出码：`0` 全部成功，`1` 有失败，`2` 没找到视频文件 —— 方便在批处理或 CI 里判错。命令行模式会在输出目录写一份 `_转换日志.txt`。

> 打包后的程序是 GUI 子系统（`--windowed`），`--cli` 模式**不往控制台打印**，结果看 `_转换日志.txt`。

## 7. 参数说明

| 参数 | 默认 | 怎么调 |
|---|---|---|
| 尺寸预设 | 微信表情 240×240 | 微信表情 240×240；竖版头像/吉祥物 400×600；网页 240×360 |
| 体积上限 | 500 KB | 微信表情平台单张限 500 KB。填 `0` = 不限，此时用阶梯里最高的一档 |
| 优先保帧率 / 保色彩 | 保帧率 | 保帧率 = 先降色数；保色彩 = 先降帧率 |
| 亮度 / 饱和度 | 1.00 / 1.00 | 像素级调整。偏暗素材亮度 1.08～1.18；偏灰素材饱和度 1.10～1.15 |
| 调色板饱和 | 1.30 | 只改调色板、像素索引不变，体积零增长。超过 1.5 会偏橘 |
| 抠像阈值 | 244 | 背景没抠净调高到 246～248；主体浅色被吃掉调低到 240 |
| 白描边宽度 | 2 | 素材本身没有白色描边的填 0 |
| WebP 质量 | 80 | WebP 专用，一般不用动 |

> 每项参数背后的取舍与实测结论，见 [docs/usage.md](docs/usage.md) 第二节。

## 8. 排错

| 现象 | 处理 |
|---|---|
| 边缘有白边 | 把「白描边宽度」调到 2～3；源视频背景不是纯白（带渐变）时抠像会不准，需要先处理源视频 |
| 主体上的白色部分被抠没了 | 说明它和背景连通了（轮廓有缺口），把「抠像阈值」调低到 240 或 238 |
| 背景没抠干净，留了一圈白 | 把「抠像阈值」调高到 246～248 |
| 颜色太暗 / 太灰 | 提「亮度」，不要只提饱和度 —— 灰调素材提饱和几乎没效果 |
| 文件超 500 KB | 直接降低体积上限值，工具会自动降档；或选「优先保色彩」让它先降帧率 |
| 能动但很顿 | 帧率被压到 6fps 了。把源视频剪短（3 秒能多拿一档帧率和色数）、放宽上限，或选「优先保帧率」 |
| 双击没反应 | 看 exe 旁边的 `单文件启动日志.txt` 和 `%TEMP%\mp4togif_boot.log`，里面记了失败原因 |
| 启动很慢 / 每次都要等 | 第一次会解压运行文件；如果每次都要解压，说明 exe 所在目录不可写，换个有写权限的位置 |
| 杀毒软件拦截 | 单文件自解压程序容易被启发式误报，可加白名单；也可从源码自行构建，或用 `src\gui.py` 直接跑 |

## 9. 安全与合规（务必阅读）

- **纯本地**：程序不发起任何网络请求，不上传素材，无遥测、无统计上报。
- **只写三个地方**：
  1. 输出目录（成品动图 + `_转换日志.txt`）；
  2. exe 旁的 `mp4togif_运行文件\`（首次运行解压出的运行文件；exe 所在目录只读时退到 `%LOCALAPPDATA%\mp4togif`）；
  3. `%TEMP%\mp4togif_boot.log`（启动诊断，每次启动追加一行，可随时删除）。
- **仅在启动失败时**才会在 exe 旁写 `单文件启动日志.txt`。
- 运行文件解压自 exe 自身尾部内嵌的 zip，**不联网下载任何组件**。
- 请从本仓库 Release 下载并核对 `.sha256`；来路不明的二次打包版本无法保证内容一致。

## 10. FAQ

**Q：转换要多久？**
A：和素材长度、目标尺寸有关，240×240 的几秒短视频一般在 20 秒上下。

**Q：会上传我的视频吗？**
A：不会。全程本地 ffmpeg 抽帧 + 本地编码，没有任何网络代码。

**Q：能转 mov / mkv / webm 吗？**
A：能。界面里选文件时支持这些格式，ffmpeg 负责解码，输出仍然是 GIF / WebP。

**Q：为什么不直接给 GIF 做真透明？**
A：GIF 格式只有 1-bit 透明（要么全透明要么不透明），这是格式本身的限制。要真半透明请用 WebP。

**Q：能转出 500 KB 以内，但画质不满意怎么办？**
A：优先保证源素材短（3 秒优于 5 秒，多出来的预算会加到色数上），或者放宽体积上限、改用 WebP。

**Q：可以商用 / 二次分发吗？**
A：代码是 MIT，随便用。转出来的动图版权取决于素材本身。

## 11. 开发与构建

| 模块 | 职责 |
|---|---|
| `src/engine.py` | 转换引擎：ffmpeg 定位、抽帧、连通域抠像、边缘外推、描边重建、调色板、体积阶梯、GIF/WebP 编码、成品校验。**所有实测经验都写在文件头和注释里** |
| `src/gui.py` | tkinter 界面 + `--cli` 命令行模式，两者共用同一套 `Options` |
| `src/launcher.cs` | 单文件自解压启动器：从 exe 尾部读 zip，解压到自身旁边，再启动主程序 |
| `scripts/build.ps1` | 三步构建：PyInstaller onedir → csc 编译启动器 → 拼接单文件，最后出 SHA-256 |

单文件的布局：

```
[launcher.exe][payload.zip][8 字节 zip 长度][16 字节 magic]
                                   └─ "MP4GIF-SFX-v1.0!"
```

启动器从**文件末尾**读这段尾巴，所以它不需要知道自己的大小。运行时把 payload 解压到 exe 旁边的 `mp4togif_运行文件\`，并写一个 `.payload` 记录长度，下次长度一致就跳过解压。

CI 流程（`.github/workflows/release.yml`）：

```
 push tag v*  ─┐
              ├─> windows-latest ─> 安装锁版依赖 ─> scripts/build.ps1
 手动触发  ───┘                                          │
                                                         ▼
                      dist/mp4togif.exe + .sha256 + usage.txt
                                                         │
                          冒烟测试（跑示例素材 + 校验透明通道）│
                                                         ▼
                                    tag 触发时发布 GitHub Release
```

**exe 一律由 GitHub Actions 构建并发布到 Release，仓库里不提交任何构建产物。** 详细说明见 [docs/build.md](docs/build.md)。

## 12. 相关文档

- [docs/usage.md](docs/usage.md) —— 完整使用说明（随 Release 一起发布）
- [docs/build.md](docs/build.md) —— 构建、打包与 CI
- [samples/](samples/) —— 示例素材
- [screenshot/](screenshot/) —— 界面预览与效果示例

## 13. License

[MIT](LICENSE) © 2026 AFAP

---

## 免责声明

本工具只做本地转码，不修改、不上传源素材。转出内容的版权与合规性由使用者自行负责；请勿用于侵犯他人著作权或违反平台规则的用途。软件按「现状」提供，作者不对转换效果或使用后果承担任何责任。
