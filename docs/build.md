# 构建说明

日常使用不需要看这里，想改代码重新打包时才用得上。
正式发布的 exe **一律由 GitHub Actions 构建**，本地构建只用于开发验证。

## 一条命令

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build.ps1
```

装了 PowerShell 7 的话也可以用：

```powershell
pwsh -NoProfile -File scripts/build.ps1
```

跑完会在 `dist\mp4togif.exe` 生成单文件版，同时输出：

```
dist\mp4togif.exe
dist\mp4togif.exe.sha256      SHA-256 校验和
dist\usage.txt                 从 docs\usage.md 复制
```

可选参数：`-OutDir <目录>` 换输出目录，`-Python <路径>` 指定解释器（默认用 `.venv`，没有就找 PATH 里的 `python`）。

## 它做了什么

```
src\gui.py    ──PyInstaller(onedir)──>  build\onedir\mp4togif\  ──zip──┐
                                                                          ├─> dist\mp4togif.exe
src\launcher.cs ──csc(.NET 自带)─────>  build\launcher.exe  ──────────────┘
```

中间产物都在 `build\`（跑完自动删掉），最终产物只在 `dist\`，两者都被 `.gitignore` 忽略。

单文件的布局是：

```
[launcher.exe][payload.zip][8 字节 zip 长度][16 字节 magic]
                                    └─ "MP4GIF-SFX-v1.0!"
```

启动器从**文件末尾**读这段尾巴，所以它不需要知道自己的大小。
运行时把 payload 解压到 exe 旁边的 `mp4togif_运行文件\`，
写一个 `.payload` 记录长度；下次长度一致就跳过解压。

带参数启动时（例如 `--cli`），启动器会等主程序结束并**把它的退出码原样返回**，方便脚本和 CI 判错；
不带参数则是普通 GUI 启动，立刻返回、不等待。

## 为什么不用 PyInstaller 的 --onefile

它的引导器**每次启动**都要把自己解压到系统临时目录：

- 慢：每次 10 秒以上
- 容易被拦：实测在某些受限环境下 `fopen` 会被拒（`Failed to extract VCRUNTIME140.dll`）
- 在临时目录留一堆 `_MEIxxxx` 垃圾

自己写的启动器只解压一次，而且解压到 exe 自己旁边。

## 环境要求

- **Python 3.10**（与 `requirements.txt` 里的锁版一致）
- **.NET Framework 4.x 的 `csc.exe`**（Windows 自带，脚本会自己找 `Framework64` / `Framework` 两个路径）

准备构建环境：

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

> 没有外网的机器上重建环境：在能上网的机器上
> `pip download -r requirements.txt --platform win_amd64 --python-version 3.10 --only-binary=:all: -d wheels`，
> 再把 `wheels` 目录拷过来 `pip install --no-index --find-links wheels -r requirements.txt`。
> 重建后**不要删** `.venv`（它已被 gitignore，不进仓库）。

## 为什么 build.ps1 是纯 ASCII

Windows PowerShell 5.1 在文件**没有 UTF-8 BOM** 时按系统 ANSI 代码页（简体中文机器上是 CP936）解析 `.ps1`；
而本仓库所有文本文件一律 UTF-8 无 BOM，所以脚本里只要出现一个中文字面量，
在 5.1 下就会变成乱码 —— 表现是 exe 名字和路径全错，而且不报错。

因此：

- `scripts/build.ps1` **保持纯 ASCII**，脚本开头有自检，混进非 ASCII 字节会直接报错退出；
- 程序名写在脚本开头的两个常量里，不要再往里加中文：

```powershell
$appName       = 'mp4togif'      # -> mp4togif.exe, build\onedir\mp4togif\
$usageFileName = 'usage.txt'     # -> dist\usage.txt, copied from docs\usage.md
```

- **产物名一律 ASCII**：GitHub 会把非 ASCII 的 Release 附件名**静默改写成 `default.txt`**
  （实测 `usage.txt` 上传后名字就没了），所以发布附件只能是 `usage.txt` 这类名字。

`appName` 同时决定 PyInstaller 的 `--name`、产物 exe 名和运行文件夹名。
改名字时要同步 `src/launcher.cs` 里的这几个常量：

| launcher.cs 常量 | 值 | 说明 |
|---|---|---|
| `Magic` | `MP4GIF-SFX-v1.0!` | 必须与 `build.ps1` 里的 `$magicText` 一致，正好 16 字节 |
| `AppExeName` | `mp4togif.exe` | = `appName + ".exe"` |
| `DataDirName` | `mp4togif_运行文件` | 首次运行解压出来的运行文件夹 |

`launcher.cs` 本身是 UTF-8 无 BOM 且含中文，实测 .NET Framework 自带的 csc 能正确识别 UTF-8，不必额外指定 `/codepage`。

## 文件说明

| 文件 | 作用 |
|---|---|
| `src\engine.py` | 转换引擎：抽帧、抠白底、调色板、体积阶梯、GIF/WebP 编码、成品校验。**所有经验都在这个文件的注释里** |
| `src\gui.py` | 界面（tkinter）+ 命令行模式（`--cli`） |
| `src\launcher.cs` | 单文件自解压启动器 |
| `scripts\build.ps1` | 四步构建脚本（onedir → 启动器 → 拼接 → 校验和） |
| `requirements.txt` | 全量锁版依赖，CI 按此安装 |

## 命令行模式

打包后也能跑，方便批量和脚本调用：

```powershell
.\dist\mp4togif.exe --cli <文件或文件夹> --out <目录> --size 240x240 --limit 500
```

退出码 `0` = 全部成功，`1` = 有失败，`2` = 没找到视频文件。
完整参数见 `docs\usage.md` 第四节。

## CI：GitHub Actions

`.github/workflows/release.yml`，两种触发方式：

| 触发 | 行为 |
|---|---|
| 推 tag `v*` | 构建 → 冒烟测试 → 上传 artifact → 发布 GitHub Release（含 exe + `.sha256` + `usage.txt`，自动生成变更说明） |
| 手动 `Run workflow` | 只构建和冒烟测试，不发布 Release |

冒烟测试这一步会真的跑一遍转换：把 `samples\yuejianglou.mp4` 转成 GIF，
断言退出码为 0，并用 `engine.check_gif()` 校验透明通道是否可用且逐帧稳定。
测试前会把 PATH 里带 `ffmpeg.exe` 的目录摘掉，**确保验证的是 exe 内置的 ffmpeg**，而不是 runner 上装的那个。

发版流程：

```powershell
git tag v1.0.0
git push origin v1.0.0
```

仓库里**永远不提交 exe**，README 顶部的 Release / CI 徽章会在 CI 跑通后自动亮起。
