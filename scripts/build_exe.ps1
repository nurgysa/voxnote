# Bundle the cloud-only Audio Transcriber as a Windows .exe (onedir).
#
# Prereqs:
#   1. .venv-build/ activated with cloud-only requirements.txt + requirements-build.txt installed
#   2. vendor/ffmpeg/ffmpeg.exe + vendor/ffmpeg/ffprobe.exe present (download from
#      https://www.gyan.dev/ffmpeg/builds/ — "release essentials" build)
#
# Run from repo root:
#   .\scripts\build_exe.ps1

$ErrorActionPreference = "Stop"

Write-Host "[1/6] Cleaning previous build outputs..." -ForegroundColor Cyan
if (Test-Path "build") { Remove-Item -Recurse -Force "build" }
if (Test-Path "dist") { Remove-Item -Recurse -Force "dist" }

Write-Host "[2/6] Verifying vendored ffmpeg binaries..." -ForegroundColor Cyan
foreach ($name in @("ffmpeg.exe", "ffprobe.exe")) {
    $path = "vendor/ffmpeg/$name"
    if (-not (Test-Path $path)) {
        throw "Missing $path. Download from https://www.gyan.dev/ffmpeg/builds/ (release essentials build) and extract ffmpeg.exe + ffprobe.exe into vendor/ffmpeg/."
    }
}
$ffmpegSize = [math]::Round((Get-Item "vendor/ffmpeg/ffmpeg.exe").Length / 1MB, 1)
$ffprobeSize = [math]::Round((Get-Item "vendor/ffmpeg/ffprobe.exe").Length / 1MB, 1)
Write-Host "  ffmpeg.exe  = $ffmpegSize MB" -ForegroundColor DarkGray
Write-Host "  ffprobe.exe = $ffprobeSize MB" -ForegroundColor DarkGray

Write-Host "[3/6] Running PyInstaller..." -ForegroundColor Cyan
pyinstaller audio_transcriber.spec --noconfirm

Write-Host "[4/6] Verifying bundle output..." -ForegroundColor Cyan
$bundleDir = "dist/AudioTranscriber"
$exePath = "$bundleDir/AudioTranscriber.exe"
if (-not (Test-Path $exePath)) {
    throw "Build failed — $exePath not found. Check PyInstaller output above for missing modules."
}

Write-Host "[5/6] Seeding starter config.json into _internal/..." -ForegroundColor Cyan
# utils.load_config() reads config.json from beside utils.py, which lives at
# _internal/utils.py in the PyInstaller 6.x onedir layout. Copy
# config.example.json there as config.json so first launch has a populated
# config (default cloud_provider, empty API keys for the first-run banner
# in Task 7 to detect).
$internalDir = "$bundleDir/_internal"
if (-not (Test-Path $internalDir)) {
    # Older PyInstaller layouts put modules at bundle root — detect + adapt.
    $internalDir = $bundleDir
    Write-Host "  Note: _internal/ not found; seeding config.json at bundle root instead." -ForegroundColor Yellow
}
Copy-Item "config.example.json" "$internalDir/config.json" -Force

Write-Host "[6/6] Verifying bundle size..." -ForegroundColor Cyan
$bundleSize = [math]::Round((Get-ChildItem -Recurse $bundleDir | Measure-Object -Sum Length).Sum / 1MB, 0)
Write-Host ("  Bundle size: {0} MB" -f $bundleSize) -ForegroundColor DarkGray
if ($bundleSize -gt 500) {
    Write-Warning "Bundle larger than expected (target 150-300 MB). A heavy lib may have slipped in via a transitive dep. Inspect the PyInstaller Analysis warnings above."
} elseif ($bundleSize -lt 80) {
    Write-Warning "Bundle smaller than expected — verify the bundle actually launches (missing transitive deps would surface as ImportError at runtime, not build-time)."
}

Write-Host ""
Write-Host "Done. Smoke-test the bundle:" -ForegroundColor Green
Write-Host "  .\$exePath" -ForegroundColor White
Write-Host ""
Write-Host "To ship: Compress-Archive -Path $bundleDir -DestinationPath dist/AudioTranscriber-<version>.zip" -ForegroundColor DarkGray
