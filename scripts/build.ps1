# Build a single-file self-extracting exe from source.
#
#   powershell -ExecutionPolicy Bypass -File scripts\build.ps1     # Windows PowerShell 5.1
#   pwsh -NoProfile -File scripts/build.ps1                        # PowerShell 7+
#
# Steps:
#   1) PyInstaller --onedir            -> the payload directory
#   2) csc (ships with .NET Framework) -> the self-extracting launcher
#   3) concat  [launcher][payload.zip][8-byte zip length][16-byte magic]
#
# Why not PyInstaller --onefile: its bootloader unpacks itself into the system temp
# directory on EVERY launch - slow (10 s+) and easy for security software to block.
# Our launcher unpacks once, next to the exe (falling back to %LOCALAPPDATA% when
# that folder is read-only).
#
# This file is deliberately ASCII-only. Windows PowerShell 5.1 decodes .ps1 files
# with the ANSI code page unless they carry a UTF-8 BOM, and this repo keeps every
# text file BOM-free, so a non-ASCII literal here would be mangled at parse time
# (mojibake exe name, mojibake paths). The guard below enforces the rule.
#
# Keep the magic in sync with src/launcher.cs:
#   Magic = "MP4GIF-SFX-v1.0!"  (exactly 16 bytes)
# Keep the names in sync with src/launcher.cs, which derives them from appName:
#   DataDirName = appName + <localized suffix>
#   AppExeName  = appName + ".exe"

[CmdletBinding()]
param(
    # Python interpreter to build with. Default: .venv\Scripts\python.exe, then PATH.
    [string]$Python = '',
    # Where the finished single-file exe goes. Default: <repo>\dist
    [string]$OutDir = ''
)

$ErrorActionPreference = 'Stop'

# Guard the rule above: a single non-ASCII byte in this file would be parsed as
# mojibake by Windows PowerShell 5.1 and silently break names and paths.
if ($PSCommandPath -and (Test-Path $PSCommandPath)) {
    foreach ($b in [IO.File]::ReadAllBytes($PSCommandPath)) {
        if ($b -gt 127) {
            throw 'scripts/build.ps1 must stay ASCII-only'
        }
    }
}

$scriptDir = $PSScriptRoot
if (-not $scriptDir) { $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path }
$root = Split-Path -Parent $scriptDir
$src  = Join-Path $root 'src'
$work = Join-Path $root 'build'
$magicText = 'MP4GIF-SFX-v1.0!'      # must be exactly 16 bytes

# ------------------------------------------------------------------ names
# Everything the build emits must have an ASCII name: GitHub silently renames a
# release asset with a non-ASCII name to "default.txt" on upload.
$appName       = 'mp4togif'      # -> mp4togif.exe, build\onedir\mp4togif\
$usageFileName = 'usage.txt'     # -> dist\usage.txt, copied from docs\usage.md

# ------------------------------------------------------------------ toolchain
function Resolve-PythonPath {
    param([string]$Explicit)
    if ($Explicit) {
        if (-not (Test-Path $Explicit)) { throw "python not found: $Explicit" }
        return (Resolve-Path $Explicit).Path
    }
    $venv = Join-Path $root '.venv\Scripts\python.exe'
    if (Test-Path $venv) { return $venv }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    throw 'No Python interpreter. Create .venv first (see docs/build.md) or pass -Python <path>.'
}

function Resolve-CscPath {
    $cands = @(
        (Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'),
        (Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe')
    )
    foreach ($c in $cands) { if (Test-Path $c) { return $c } }
    $cmd = Get-Command csc.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    throw 'csc.exe (.NET Framework 4.x) not found; cannot compile the launcher.'
}

$py  = Resolve-PythonPath $Python
$csc = Resolve-CscPath

if (-not $OutDir) { $OutDir = Join-Path $root 'dist' }
$outDir = [IO.Path]::GetFullPath($OutDir)

Write-Host "python : $py"
Write-Host "csc    : $csc"
Write-Host "app    : $appName"

Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $work | Out-Null

# ---------------------------------------------------------- 1) onedir payload
Write-Host '[1/4] PyInstaller onedir ...'
& $py -m PyInstaller --noconfirm --clean --windowed --onedir `
  --name $appName --paths $src --collect-binaries imageio_ffmpeg `
  --hidden-import scipy.ndimage `
  --exclude-module scipy.optimize --exclude-module scipy.spatial --exclude-module scipy.interpolate `
  --exclude-module scipy.stats --exclude-module scipy.integrate --exclude-module scipy.fft `
  --exclude-module scipy.io --exclude-module scipy.cluster --exclude-module scipy.odr `
  --exclude-module scipy.datasets --exclude-module scipy.misc --exclude-module scipy.differentiate `
  --exclude-module matplotlib --exclude-module PyQt5 --exclude-module PySide2 --exclude-module pandas `
  --distpath (Join-Path $work 'onedir') --workpath (Join-Path $work 'obj') `
  --specpath $work (Join-Path $src 'gui.py')
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed (exit $LASTEXITCODE)" }

$appDir = Join-Path (Join-Path $work 'onedir') $appName
$appExe = Join-Path $appDir ($appName + '.exe')
if (-not (Test-Path $appExe)) { throw "onedir build failed: $appExe not found" }

# ------------------------------------------------------------- 2) launcher
Write-Host '[2/4] compile launcher ...'
$launcher = Join-Path $work 'launcher.exe'
& $csc /nologo /target:winexe /optimize+ /out:$launcher `
  /r:System.dll /r:System.Core.dll /r:System.IO.Compression.dll /r:System.IO.Compression.FileSystem.dll `
  /r:System.Windows.Forms.dll /r:System.Drawing.dll (Join-Path $src 'launcher.cs')
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $launcher)) { throw 'launcher compile failed' }

# ------------------------------------------------------- 3) single-file exe
Write-Host '[3/4] pack single-file exe ...'
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = Join-Path $work 'payload.zip'
[System.IO.Compression.ZipFile]::CreateFromDirectory(
  $appDir, $zip, [System.IO.Compression.CompressionLevel]::Optimal, $false)

New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$exeName = $appName + '.exe'
$out = Join-Path $outDir $exeName
$lb = [IO.File]::ReadAllBytes($launcher)
$zb = [IO.File]::ReadAllBytes($zip)
$lenb = [BitConverter]::GetBytes([int64]$zb.Length)
$mb = [Text.Encoding]::ASCII.GetBytes($magicText)
if ($mb.Length -ne 16) { throw "magic must be 16 bytes, got $($mb.Length)" }
$fs = [IO.File]::Create($out)
try {
  $fs.Write($lb, 0, $lb.Length)
  $fs.Write($zb, 0, $zb.Length)
  $fs.Write($lenb, 0, 8)
  $fs.Write($mb, 0, 16)
} finally { $fs.Close() }

# ------------------------------------------------- 4) checksum + usage doc
Write-Host '[4/4] checksum + usage doc ...'
$hash = (Get-FileHash -Algorithm SHA256 -Path $out).Hash.ToLower()
$shaPath = "$out.sha256"
$shaLine = "$hash  $exeName`n"
# UTF-8 without BOM: PowerShell 5.1's -Encoding UTF8 would add a BOM, and a .sha256
# file must stay byte-clean for sha256sum and PowerShell alike.
[IO.File]::WriteAllText($shaPath, $shaLine, (New-Object Text.UTF8Encoding $false))

$usageSrc = Join-Path $root 'docs\usage.md'
if ($usageFileName -and (Test-Path $usageSrc)) {
  Copy-Item $usageSrc (Join-Path $outDir $usageFileName) -Force
}

Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
$mbSize = (Get-Item $out).Length / 1MB
Write-Host ('built {0}  {1:N1} MB' -f $out, $mbSize)
Write-Host "sha256 $hash"
