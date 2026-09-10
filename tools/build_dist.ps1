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
# WPF 先发布到暂存目录再整体搬入：这样 dist 里的陈旧文件会被彻底清掉（只删顶层
# 文件会留下上次的 runtime/ 等目录），且发布产物的体积可以单独量准。
$StageDir = Join-Path $OutDir '.publish_stage'

if (Test-Path $StageDir) { Remove-Item -Recurse -Force $StageDir }

# ------------------------------------------------------- 2. WPF 自包含发布
Write-Step "发布 WPF（自包含，目标机器无需 .NET）"
$Proj = Join-Path $Root 'wpf\LinovelibDesktop\LinovelibDesktop.csproj'
dotnet publish $Proj `
    -c Release `
    -r win-x64 `
    --self-contained true `
    -o $StageDir `
    --nologo `
    --verbosity:quiet
if ($LASTEXITCODE -ne 0) { throw "dotnet publish 失败（退出码 $LASTEXITCODE）" }

# 发布产物中的 LinovelibDesktop.exe 即分发版入口，改名为「轻小说下载器.exe」。
$PublishedExe = Join-Path $StageDir 'LinovelibDesktop.exe'
if (-not (Test-Path $PublishedExe)) { throw "未找到发布产物 $PublishedExe" }
Move-Item -Force $PublishedExe (Join-Path $StageDir '轻小说下载器.exe')

# 量准 WPF 发布产物的体积（此时暂存目录里只有它）。
$WpfSizeMB = Get-DirSizeMB $StageDir

# 清掉 dist 里的旧发布产物，为搬运新产物腾位置。
# 必须保留 runtime/（重建代价高，留着可复用）与 download/、_tmp_dl/（可能已有用户
# 下载的成品，误删等于毁数据），以及刚发布好的暂存目录本身。
$KeepNames = @('runtime', 'download', '_tmp_dl', '.publish_stage')
Get-ChildItem $OutDir -Force | Where-Object { $KeepNames -notcontains $_.Name } |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem $StageDir -Force | Move-Item -Destination $OutDir -Force
Remove-Item -Recurse -Force $StageDir -ErrorAction SilentlyContinue
Write-Host "    入口: 轻小说下载器.exe"

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

    # embeddable 默认不启用 site，pip 与 site-packages 都不可用；且 ._pth 一旦存在，
    # Python 就**不再**自动把脚本所在目录加进 sys.path。后者是致命的：WPF 以
    # `python <root>\wpf_bridge.py` 启动桥接，而 wpf_bridge.py 要 `import main`，
    # 缺了项目根就会 ModuleNotFoundError: No module named 'main'。
    # 故必须显式加上项目根 `..\..`（相对 runtime\python\ 即分发包根）。
    Write-Step "配置 python310._pth（启用 site + site-packages + 项目根）"
    $Pth = Join-Path $RuntimeDir 'python310._pth'
    if (-not (Test-Path $Pth)) { throw "未找到 $Pth（Python 版本与文件名不匹配？）" }
    @(
        'python310.zip',
        '.',
        'Lib\site-packages',
        '..\..',
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

# 自检脚本随包发布，用户可在目标机器上自行复验（设计 §10 的验收项）。
# 必须放进分发版内：脚本按 __file__ 向上定位根目录，从仓库里跑会去校验仓库而不是
# 分发版——那样「检查通过」是假象（实测踩过：PROJECT_ROOT 打出了仓库路径）。
$VerifyDst = Join-Path $OutDir 'tools'
New-Item -ItemType Directory -Force -Path $VerifyDst | Out-Null
Copy-Item -Force (Join-Path $Root 'tools\verify_dist.py') $VerifyDst

# 清掉分发包里的字节码目录。根目录那份来自上一次在 dist 内运行 Python，上面的
# Copy-Item -Force 不会删它；它含开发机路径，且可能被优先加载而掩盖真正的源码问题。
foreach ($pyc in @("$OutDir\__pycache__", "$VerifyDst\__pycache__")) {
    if (Test-Path $pyc) { Remove-Item -Recurse -Force $pyc -ErrorAction SilentlyContinue }
}

# 命令行入口：源码仓库的 download.bat 直接调用裸 `python`，拷进分发版会在无
# Python 的机器上失败——那正是本次要解决的问题。故分发版另生成一份 bat，优先用
# 内置运行时，找不到才回退 PATH 上的 python。
# 编码同仓库根的同名文件：UTF-8（无 BOM）。首行 chcp 65001 先生效，cmd 逐行读取
# 后续内容时才不会把中文 title 解成乱码；写成 ASCII 反而会让标题变成 ???。
# 报告用的「分发包体积」必须排除 download/ 与 _tmp_dl/：它们是 $KeepNames 刻意保留的
# 用户数据（见上文，误删等于毁数据），会把用户已下载的成品算进来。开发机常直接在 dist
# 内运行，这两个目录会累积到 GB 级——若不排除，「分发包总计」将取决于开发机有没有跑过
# 应用，恰好与「分发包应该多大」相反。故单列，并从总计里扣掉。
function Get-DistPayloadMB($outDir, $excludeNames) {
    $bytes = (Get-ChildItem $outDir -Recurse -File -Force -ErrorAction SilentlyContinue |
              Where-Object {
                  $rel = $_.FullName.Substring($outDir.Length).TrimStart('\')
                  $top = $rel.Split('\')[0]
                  $excludeNames -notcontains $top
              } |
              Measure-Object -Property Length -Sum).Sum
    if (-not $bytes) { return 0 }
    return [math]::Round($bytes / 1MB, 1)
}

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
# 用 .NET API 写以精确控制编码——Set-Content -Encoding UTF8 会带 BOM。
[System.IO.File]::WriteAllText(
    (Join-Path $OutDir 'download.bat'),
    ($DistBat -replace "`r?`n", "`r`n"),
    [System.Text.UTF8Encoding]::new($false))

# WPF 源码保留在分发包内（设计 §4）：分发版跑的是已编译的 exe，源码仅作可读可改
# 的参考。排除 MSBuild 中间产物，避免把开发机的构建状态带进去。
#
# 过滤必须覆盖 artifacts/：MSBuild 的中间目录名可以是自定义的（如
# wpf-release-verify-obj），按「目录段恰好叫 bin/obj」去匹配会漏掉它们。实测
# artifacts/wpf-release-verify-obj/.../*.FileListAbsolute.txt 被原样拷进了分发版，
# 而该文件记录的正是上一次构建的产物**绝对路径**——等于随包泄漏开发机路径。
# 这与 .gitignore 对 artifacts/ 的处理保持一致。
$WpfSrc = Join-Path $Root 'wpf\LinovelibDesktop'
$WpfDst = Join-Path $OutDir 'wpf\LinovelibDesktop'
if (Test-Path $WpfSrc) {
    if (Test-Path $WpfDst) { Remove-Item -Recurse -Force $WpfDst }
    New-Item -ItemType Directory -Force -Path $WpfDst | Out-Null
    Get-ChildItem $WpfSrc -Recurse -File |
        Where-Object { $_.FullName -notmatch '\\(bin|obj|artifacts)\\' } |
        ForEach-Object {
            $rel = $_.FullName.Substring($WpfSrc.Length).TrimStart('\')
            $target = Join-Path $WpfDst $rel
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
            Copy-Item -Force $_.FullName $target
        }
}

# --------------------------------------------------------------- 6. 验证
# 这一步是本次的核心保障：仅验证「依赖能导入」是不够的（那样跑得过，但应用一启动
# 就 No module named 'main'）。必须按应用的**真实调用方式**跑一遍——WPF 是用
# `python <root>\wpf_bridge.py` 启动桥接的，而脚本目录能否进 sys.path 取决于 ._pth
# 的配置。故这里实际调用一次桥接的 --help。
Write-Step "验证嵌入式运行时可用"
& $PyExe -c "import bs4, lxml, PIL, ebooklib, requests, playwright; print('    依赖导入 OK')"
if ($LASTEXITCODE -ne 0) { throw "运行时依赖导入失败" }

& $PyExe -c "import sys; print('    Python:', sys.version.split()[0])"

# 出厂闸门：跑 tools/verify_dist.py。检查逻辑放在 Python 里而非此处内联，因为
# PowerShell 5.1 会把子进程的 stderr 包成终止性 ErrorRecord（argparse 的 --help 正
# 走 stderr），且子进程输出是 GBK 而非 UTF-8——内联比对中文必然误判。详见该脚本头注释。
Write-Step "端到端自检：按应用真实方式验证"
# -B：不要写 .pyc。自检会 import 项目模块，否则会在分发版根目录重新生成
# __pycache__（第 5 步刚清掉的那份），把开发机路径带进包里。
& $PyExe -B (Join-Path $OutDir 'tools\verify_dist.py')
if ($LASTEXITCODE -ne 0) { throw "分发版自检未通过（详见上方输出）" }

# 自检若因故仍留下字节码（例如漏了 -B），在此兜底清掉，保证产物干净。
foreach ($pyc in @("$OutDir\__pycache__", "$OutDir\tools\__pycache__")) {
    if (Test-Path $pyc) { Remove-Item -Recurse -Force $pyc -ErrorAction SilentlyContinue }
}

# --------------------------------------------------------------- 7. 报告
Write-Host ""
Write-Host "构建完成" -ForegroundColor Green
Write-Host ("  {0,-22} {1,8} MB" -f 'WPF 自包含发布', $WpfSizeMB)
Write-Host ("  {0,-22} {1,8} MB" -f '嵌入式 Python 运行时', (Get-DirSizeMB $RuntimeDir))
$UserData = @('download', '_tmp_dl')
$PayloadMB = Get-DistPayloadMB $OutDir $UserData
Write-Host ("  {0,-22} {1,8} MB" -f '分发包总计', $PayloadMB)
# 单列用户数据：它们就在分发目录里，但不算「包」的一部分（见 Get-DistPayloadMB 注释）。
foreach ($name in $UserData) {
    $p = Join-Path $OutDir $name
    if (Test-Path $p) {
        Write-Host ("  {0,-22} {1,8} MB  （用户数据，不计入）" -f "$name/", (Get-DirSizeMB $p))
    }
}
Write-Host ""
Write-Host "下一步：把整个 $OutDir 目录拷贝到目标机器，双击「轻小说下载器.exe」。"
