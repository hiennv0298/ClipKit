# Build the Windows downloads:
#   dist\ClipKit\                     app folder (ClipKitApp.exe, clipkit.exe, Python runtime, ffmpeg)
#   dist\ClipKit-<ver>-portable.zip   unzip-and-run version
#   dist\ClipKit-Setup-<ver>.exe      installer (needs Inno Setup 6: winget install JRSoftware.InnoSetup)
#
#   powershell -ExecutionPolicy Bypass -File scripts\build.ps1 [-DownloadFfmpeg] [-SkipInstaller]
#
# -DownloadFfmpeg  bundle the gyan.dev essentials build (checked against its SHA-256)
#                  instead of copying ffmpeg/ffprobe found on PATH. CI uses this.
# ASCII-only on purpose (Windows PowerShell 5.1 reads BOM-less files as ANSI).

$ErrorActionPreference = "Stop"
$DownloadFfmpeg = ($args -contains "-DownloadFfmpeg")
$SkipInstaller = ($args -contains "-SkipInstaller")
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

# 1. Clean build environment
$venv = Join-Path $Root ".venv-build"
$py = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $py)) { python -m venv $venv }
& $py -m pip install --quiet --upgrade pip
& $py -m pip install --quiet . "pyinstaller>=6.10"
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
$version = (& $py -c "import clipkit; print(clipkit.__version__)").Trim()
Write-Host "Building ClipKit $version"

# 2. PyInstaller (one folder: faster start-up than one-file, and AV scanners like it better).
#    The spec builds clipkit.exe (command line) and ClipKitApp.exe (app window) side by side.
& $py -m PyInstaller --noconfirm --clean --distpath dist --workpath build scripts\clipkit.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
$app = Join-Path $Root "dist\ClipKit"

# 3. Bundle ffmpeg + ffprobe next to the exe (clipkit.deps looks in <exe dir>\ffmpeg)
$ffDir = Join-Path $app "ffmpeg"
New-Item -ItemType Directory -Force $ffDir | Out-Null
if ($DownloadFfmpeg) {
    $zipUrl = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
    $zip = Join-Path $env:TEMP "clipkit-ffmpeg.zip"
    Invoke-WebRequest $zipUrl -OutFile $zip -UseBasicParsing
    $expected = ((Invoke-WebRequest "$zipUrl.sha256" -UseBasicParsing).Content.Trim() -split "\s+")[0].ToLower()
    $actual = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
    if ($expected -ne $actual) { throw "ffmpeg SHA-256 mismatch: $actual vs $expected" }
    $tmp = Join-Path $env:TEMP "clipkit-ffmpeg"
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    Expand-Archive $zip $tmp
    Get-ChildItem $tmp -Recurse -Include ffmpeg.exe, ffprobe.exe | Copy-Item -Destination $ffDir
    Remove-Item $zip, $tmp -Recurse -Force
} else {
    foreach ($tool in "ffmpeg", "ffprobe") {
        $src = (Get-Command $tool -ErrorAction SilentlyContinue).Source
        if (-not $src) { throw "$tool not on PATH; install it or use -DownloadFfmpeg" }
        Copy-Item $src -Destination (Join-Path $ffDir "$tool.exe")
    }
}
Copy-Item README.md, LICENSE -Destination $app

# 4. Smoke test the frozen app
& (Join-Path $app "clipkit.exe") doctor
if ($LASTEXITCODE -ne 0) { throw "clipkit.exe doctor failed" }
if (-not (Test-Path (Join-Path $app "ClipKitApp.exe"))) { throw "ClipKitApp.exe missing" }
if (-not (Get-ChildItem $app -Recurse -Filter index.html | Where-Object { $_.FullName -match "webui" })) { throw "web UI files missing" }

# 5. Portable zip
$portable = Join-Path $Root "dist\ClipKit-$version-portable.zip"
Remove-Item $portable -ErrorAction SilentlyContinue
# Antivirus often keeps freshly copied exes open for a few seconds: retry.
for ($try = 1; $try -le 6; $try++) {
    try { Compress-Archive -Path $app -DestinationPath $portable -Force -ErrorAction Stop; break }
    catch {
        if ($try -eq 6) { throw }
        Write-Host "Files still locked, retrying in 10 s ($try/5) ..."
        Start-Sleep -Seconds 10
    }
}
Write-Host "Portable: $portable"

# 6. Installer
if ($SkipInstaller) { return }
$iscc = @(
    (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source,
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $iscc) {
    Write-Warning "Inno Setup not found, skipped the installer. Install it with: winget install JRSoftware.InnoSetup"
    return
}
& $iscc "/DAppVersion=$version" (Join-Path $Root "installer\clipkit.iss")
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
Write-Host "Installer: $(Join-Path $Root "dist\ClipKit-Setup-$version.exe")"
