# Bundle the cloud-only VoxNote as a Windows .exe (onedir).
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
pyinstaller voxnote.spec --noconfirm

Write-Host "[4/6] Verifying bundle output..." -ForegroundColor Cyan
$bundleDir = "dist/VoxNote"
$exePath = "$bundleDir/VoxNote.exe"
if (-not (Test-Path $exePath)) {
    throw "Build failed — $exePath not found. Check PyInstaller output above for missing modules."
}

Write-Host "[5/6] Bundling config.example.json template into _internal/..." -ForegroundColor Cyan
# The live config now lives OUTSIDE the bundle at ~/.voxnote/config.json
# (utils._default_config_path when frozen), so a build update never wipes the
# user's keys/settings. We bundle config.example.json as a read-only TEMPLATE;
# on first run utils._seed_default_config copies it to ~ (empty keys -> the
# first-run banner fires). sys._MEIPASS resolves to _internal in the
# PyInstaller 6.x onedir layout, so the template is found there at runtime.
$internalDir = "$bundleDir/_internal"
if (-not (Test-Path $internalDir)) {
    # Older PyInstaller layouts put modules at bundle root — detect + adapt.
    $internalDir = $bundleDir
    Write-Host "  Note: _internal/ not found; placing template at bundle root instead." -ForegroundColor Yellow
}
Copy-Item "config.example.json" "$internalDir/config.example.json" -Force

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
Write-Host "To ship: python scripts\package_release.py --version <version>" -ForegroundColor DarkGray
Write-Host "  (packs via Python zipfile with forward-slash names + verifies no-secrets/0-backslash/integrity;" -ForegroundColor DarkGray
Write-Host "   do NOT use Compress-Archive — PS 5.1 writes backslash entry names that break extraction on macOS/7-Zip)" -ForegroundColor DarkGray
