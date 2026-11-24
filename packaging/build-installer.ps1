param(
    [Parameter(Mandatory)][string]$FFmpegDir,
    [Parameter(Mandatory)][string]$ISCC
)
$ErrorActionPreference = 'Stop'
$ffmpeg = (Resolve-Path -LiteralPath $FFmpegDir).Path
$compiler = (Resolve-Path -LiteralPath $ISCC).Path
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
$version = & $python -c 'import sys, tomllib; print(tomllib.load(open(sys.argv[1], ""rb""))[""project""][""version""])' (Join-Path $root 'pyproject.toml')
if ($LASTEXITCODE -ne 0 -or $version -notmatch '^\d+\.\d+\.\d+$') { throw 'Cannot read installer version from pyproject.toml.' }

$model = Join-Path $root 'ModelASR\faster-whisper-small'
if (-not (Test-Path -LiteralPath $model -PathType Container)) {
    $cache = Join-Path $root 'ModelASR\models--Systran--faster-whisper-small'
    $revision = (Get-Content -LiteralPath (Join-Path $cache 'refs\main') -Raw).Trim()
    if ($revision -notmatch '^[0-9a-f]{40}$') { throw 'Invalid faster-whisper-small snapshot revision.' }
    $model = Join-Path $cache "snapshots\$revision"
}

$work = Join-Path $root 'build\installer'
$payload = Join-Path $work 'payload'
$media = New-Item -ItemType Directory -Force (Join-Path $payload 'media')
$asr = New-Item -ItemType Directory -Force (Join-Path $payload 'ModelASR\faster-whisper-small')
foreach ($file in 'bin\ffmpeg.exe', 'bin\ffprobe.exe', 'LICENSE', 'README.txt') {
    Copy-Item -LiteralPath (Join-Path $ffmpeg $file) -Destination $media.FullName -Force
}
foreach ($file in 'config.json', 'model.bin', 'tokenizer.json', 'vocabulary.txt') {
    Copy-Item -LiteralPath (Join-Path $model $file) -Destination $asr.FullName -Force
}

$previous = $env:SUBSHORT_CPU_BUILD
try {
    $env:SUBSHORT_CPU_BUILD = '1'
    & $python -m PyInstaller --noconfirm --workpath (Join-Path $work 'pyinstaller') --distpath $payload (Join-Path $PSScriptRoot 'subshort.spec')
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }
} finally {
    $env:SUBSHORT_CPU_BUILD = $previous
}
& $compiler "/DAppVersion=$version" (Join-Path $PSScriptRoot 'subshort.iss')
if ($LASTEXITCODE -ne 0) { throw 'Inno Setup failed.' }
