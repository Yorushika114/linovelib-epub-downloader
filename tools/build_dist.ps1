<#
.SYNOPSIS
    构建免安装分发包：内置嵌入式 Python 运行时 + WPF 自包含发布。

.DESCRIPTION
    产出一个可整体拷贝到任意 Windows 10/11 机器上双击即用的目录，目标机器
    无需安装 Python、.NET SDK/Runtime，无需联网装依赖。

    设计依据：docs/superpowers/specs/2026-09-10-portable-runtime-design.md

    关键点：
    - 嵌入式 Python 用 python.org 的 embeddable 包（自带 stdlib、可整体搬运）。
      不使用 venv：venv 的 pyvenv.cfg 硬编码基础解释器路径且不含 stdlib，
      无法跨机器搬运。
    - PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1：代码走系统 Edge（channel="msedge"），
      不拖入约 1.4GB 的 playwright 浏览器。
    - WPF 用自包含发布，目标机器无需 .NET。分发版的「轻小说下载器.exe」即
      WPF 发布产物；源码启动器（.NET 应用）只在开发仓库使用，不进分发包。

.PARAMETER OutDir
    输出目录，默认为仓库根下的 dist/。

.PARAMETER Force
    已存在的 runtime 目录也重建（默认复用，便于反复调试非运行时部分）。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools/build_dist.ps1

.NOTES
    本文件必须以「UTF-8 with BOM」保存。Windows PowerShell 5.1 对无 BOM 的
    .ps1 按系统 ANSI 代码页读取，脚本内的中文会乱码进而引发解析错误。
    若用编辑器另存，请确认保留 BOM。
#>

[CmdletBinding()]
param(
    [string]$OutDir,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

# 嵌入式 Python 版本：固定在脚本中并在输出中打印，避免开发机与分发版
# 出现隐形版本差异。
$PyVersion = '3.10.11'

$Root = Split-Path -Parent $PSScriptRoot
if (-not $OutDir) { $OutDir = Join-Path $Root 'dist' }

function Write-Step($msg) {
    Write-Host "==> $msg" -ForegroundColor Cyan
}

function Get-DirSizeMB($path) {
    if (-not (Test-Path $path)) { return 0 }
    $bytes = (Get-ChildItem $path -Recurse -File -ErrorAction SilentlyContinue |
              Measure-Object -Property Length -Sum).Sum
    if (-not $bytes) { return 0 }
    return [math]::Round($bytes / 1MB, 1)
}

Write-Host "构建免安装分发包" -ForegroundColor Green
Write-Host "  仓库根目录 : $Root"
Write-Host "  输出目录   : $OutDir"
Write-Host "  Python 版本: $PyVersion"

# ---------------------------------------------------------------- 1. 前置检查
$DistExe = Join-Path $OutDir '轻小说下载器.exe'
$RuntimeDir = Join-Path $OutDir 'runtime\python'

if (Test-Path $DistExe) {
    Write-Step "清理旧的发布产物"
    Get-ChildItem $OutDir -File | Remove-Item -Force -ErrorAction SilentlyContinue
}

# ------------------------------------------------------- 2. WPF 自包含发布
Write-Step "发布 WPF（自包含，目标机器无需 .NET）"
$Proj = Join-Path $Root 'wpf\LinovelibDesktop\LinovelibDesktop.csproj'
dotnet publish $Proj `
    -c Release `
    -r win-x64 `
    --self-contained true `
    -o $OutDir `
    --nologo `
    --verbosity:quiet
if ($LASTEXITCODE -ne 0) { throw "dotnet publish 失败（退出码 $LASTEXITCODE）" }

# 发布产物中的 LinovelibDesktop.exe 即分发版入口，改名为「轻小说下载器.exe」。
$PublishedExe = Join-Path $OutDir 'LinovelibDesktop.exe'
if (-not (Test-Path $PublishedExe)) { throw "未找到发布产物 $PublishedExe" }
Move-Item -Force $PublishedExe $DistExe
Write-Host "    入口: $(Split-Path -Leaf $DistExe)"

# ------------------------------------------------- 3. 嵌入式 Python 运行时
if ((Test-Path $RuntimeDir) -and -not $Force) {
    Write-Step "复用已存在的 runtime/（如需重建请加 -Force）"
} else {
    Write-Step "准备嵌入式 Python $PyVersion"
    if (Test-Path $RuntimeDir) { Remove-Item -Recurse -Force $RuntimeDir }
    New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null

    $ZipUrl = "https://www.python.org/ftp/python/$PyVersion/python-$PyVersion-embed-amd64.zip"
    $ZipPath = Join-Path $env:TEMP "python-$PyVersion-embed-amd64.zip"
    Write-Host "    下载 $ZipUrl"
    Invoke-WebRequest -Uri $ZipUrl -OutFile $ZipPath -UseBasicParsing
    Expand-Archive -Path $ZipPath -DestinationPath $RuntimeDir -Force
    Remove-Item $ZipPath -Force -ErrorAction SilentlyContinue

    # embeddable 默认不启用 site，pip 与 site-packages 都不可用。
    # 必须改写 ._pth：显式列出 site-packages 并启用 import site。
    Write-Step "配置 python310._pth（启用 site + site-packages）"
    $Pth = Join-Path $RuntimeDir 'python310._pth'
    if (-not (Test-Path $Pth)) { throw "未找到 $Pth（Python 版本与文件名不匹配？）" }
    @(
        'python310.zip',
        '.',
        'Lib\site-packages',
        '',
        'import site'
    ) | Set-Content -Path $Pth -Encoding ASCII

    # bootstrap pip
    Write-Step "安装 pip"
    $GetPip = Join-Path $RuntimeDir 'get-pip.py'
    Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile $GetPip -UseBasicParsing
    $PyExe = Join-Path $RuntimeDir 'python.exe'
    & $PyExe $GetPip --no-warn-script-location --disable-pip-version-check
    if ($LASTEXITCODE -ne 0) { throw "pip 安装失败" }
    Remove-Item $GetPip -Force -ErrorAction SilentlyContinue
}

# --------------------------------------------------------- 4. 安装项目依赖
Write-Step "安装项目依赖（跳过 playwright 浏览器下载，走系统 Edge）"
$PyExe = Join-Path $RuntimeDir 'python.exe'
$Req = Join-Path $Root 'requirements.txt'
$env:PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD = '1'
& $PyExe -m pip install -r $Req --no-warn-script-location --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { throw "依赖安装失败" }
Remove-Item Env:\PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD -ErrorAction SilentlyContinue

# ------------------------------------------------------------ 5. 拷贝源码
Write-Step "拷贝源码"
foreach ($item in @('linovelib', 'comic')) {
    $src = Join-Path $Root $item
    $dst = Join-Path $OutDir $item
    if (Test-Path $dst) { Remove-Item -Recurse -Force $dst }
    Copy-Item -Recurse -Force $src $dst
    # 去掉 __pycache__，避免把开发机的字节码带进分发版。
    Get-ChildItem $dst -Recurse -Directory -Filter '__pycache__' -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
}
foreach ($f in @('main.py', 'wpf_bridge.py', 'launcher.py', 'requirements.txt', 'README.md')) {
    $src = Join-Path $Root $f
    if (Test-Path $src) { Copy-Item -Force $src $OutDir }
}

# 命令行入口：源码仓库的 download.bat 直接调用裸 `python`，拷进分发版会在无
# Python 的机器上失败——那正是本次要解决的问题。故分发版另生成一份 bat，优先用
# 内置运行时，找不到才回退 PATH 上的 python。
# 注意：bat 内容全是 ASCII，避免代码页问题；chcp 65001 仅为正确显示 Python 输出。
Write-Step "生成命令行入口 download.bat"
$DistBat = @'
@echo off
chcp 65001 >nul
title 轻小说/漫画 下载器

set "PY=%~dp0runtime\python\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" "%~dp0launcher.py"
pause
'@
Set-Content -Path (Join-Path $OutDir 'download.bat') -Value $DistBat -Encoding ASCII

# WPF 源码保留在分发包内（设计 §4）：分发版跑的是已编译的 exe，源码仅作可读可改
# 的参考。排除 bin/obj，避免把开发机的中间产物带进去。
$WpfSrc = Join-Path $Root 'wpf\LinovelibDesktop'
$WpfDst = Join-Path $OutDir 'wpf\LinovelibDesktop'
if (Test-Path $WpfSrc) {
    if (Test-Path $WpfDst) { Remove-Item -Recurse -Force $WpfDst }
    New-Item -ItemType Directory -Force -Path $WpfDst | Out-Null
    Get-ChildItem $WpfSrc -Recurse -File |
        Where-Object { $_.FullName -notmatch '\\(bin|obj)\\' } |
        ForEach-Object {
            $rel = $_.FullName.Substring($WpfSrc.Length).TrimStart('\')
            $target = Join-Path $WpfDst $rel
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
            Copy-Item -Force $_.FullName $target
        }
}

# --------------------------------------------------------------- 6. 验证
Write-Step "验证嵌入式运行时可用"
& $PyExe -c "import bs4, lxml, PIL, ebooklib, requests, playwright; print('    依赖导入 OK')"
if ($LASTEXITCODE -ne 0) { throw "运行时依赖导入失败" }

& $PyExe -c "import sys; print('    Python:', sys.version.split()[0])"

# --------------------------------------------------------------- 7. 报告
Write-Host ""
Write-Host "构建完成" -ForegroundColor Green
Write-Host ("  {0,-22} {1,8} MB" -f 'WPF 自包含发布', (Get-DirSizeMB ($OutDir + '\*')))
Write-Host ("  {0,-22} {1,8} MB" -f '嵌入式 Python 运行时', (Get-DirSizeMB $RuntimeDir))
Write-Host ("  {0,-22} {1,8} MB" -f '分发包总计', (Get-DirSizeMB $OutDir))
Write-Host ""
Write-Host "下一步：把整个 $OutDir 目录拷贝到目标机器，双击「轻小说下载器.exe」。"
