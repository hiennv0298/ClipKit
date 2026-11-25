# ClipKit installer for the command line (no admin rights needed).
#
#   Install from GitHub:   irm https://raw.githubusercontent.com/hiennv0298/ClipKit/main/scripts/install.ps1 | iex
#   Install from a clone:  powershell -ExecutionPolicy Bypass -File scripts\install.ps1
#   Uninstall:             powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -Uninstall
#
# What it does:
#   - creates a private Python environment in %LOCALAPPDATA%\ClipKit\venv
#   - installs ClipKit into it (from this clone, or from GitHub without needing git)
#   - adds a `clipkit` command in %LOCALAPPDATA%\ClipKit\bin and puts that folder on the user PATH
#   - adds a "ClipKit" Start menu shortcut that opens the app window
# Optional environment variables: CLIPKIT_REPO (owner/name), CLIPKIT_REF (branch or tag).
# The file is kept ASCII-only so Windows PowerShell 5.1 reads it correctly.

$ErrorActionPreference = "Stop"
$Repo = if ($env:CLIPKIT_REPO) { $env:CLIPKIT_REPO } else { "hiennv0298/ClipKit" }
$Ref = if ($env:CLIPKIT_REF) { $env:CLIPKIT_REF } else { "main" }
$Uninstall = ($args -contains "-Uninstall")
$Root = Join-Path $env:LOCALAPPDATA "ClipKit"
$Venv = Join-Path $Root "venv"
$Bin = Join-Path $Root "bin"
$Shortcut = Join-Path ([Environment]::GetFolderPath("Programs")) "ClipKit.lnk"

function Set-UserPath([string]$Dir, [bool]$Add) {
    $current = [Environment]::GetEnvironmentVariable("Path", "User")
    $parts = @($current -split ";" | Where-Object { $_ -and ($_.TrimEnd("\") -ne $Dir.TrimEnd("\")) })
    if ($Add) { $parts += $Dir }
    [Environment]::SetEnvironmentVariable("Path", ($parts -join ";"), "User")
}

if ($Uninstall) {
    Remove-Item $Venv, $Bin, $Shortcut -Recurse -Force -ErrorAction SilentlyContinue
    Set-UserPath $Bin $false
    Write-Host "ClipKit removed. Whisper models and FFmpeg are still in $Root; delete that folder to free the space."
    return
}

# 1. Find Python >= 3.10
$python = $null
foreach ($cand in @(@("py", "-3"), @("python"), @("python3"))) {
    $exe = Get-Command $cand[0] -ErrorAction SilentlyContinue
    if (-not $exe) { continue }
    $probe = @($cand | Select-Object -Skip 1) + @("-c", "import sys; print(sys.version_info >= (3, 10))")
    $ok = & $exe.Source @probe 2>$null
    if ($ok -eq "True") { $python = @($exe.Source) + @($cand | Select-Object -Skip 1); break }
}
if (-not $python) {
    Write-Host "Python 3.10 or newer is required. Install it with the command below, reopen PowerShell and run this again:" -ForegroundColor Yellow
    Write-Host "    winget install Python.Python.3.12"
    return
}

# 2. Private environment
New-Item -ItemType Directory -Force $Root, $Bin | Out-Null
$vpy = Join-Path $Venv "Scripts\python.exe"
if (-not (Test-Path $vpy)) {
    Write-Host "Creating a Python environment in $Venv ..."
    $venvArgs = @($python | Select-Object -Skip 1) + @("-m", "venv", $Venv)
    & $python[0] @venvArgs
}
& $vpy -m pip install --quiet --upgrade pip

# 3. Install ClipKit: from this clone if we are inside one, else from GitHub
$local = if ($PSScriptRoot) { Split-Path $PSScriptRoot -Parent } else { $null }
if ($local -and (Test-Path (Join-Path $local "pyproject.toml"))) {
    $spec = $local
} else {
    $spec = "https://github.com/$Repo/archive/$Ref.zip"
}
Write-Host "Installing ClipKit from $spec (the first time takes a few minutes) ..."
& $vpy -m pip install --quiet --upgrade $spec
if ($LASTEXITCODE -ne 0) { throw "pip install failed." }

# 4. `clipkit` command on PATH and a Start menu shortcut for the window
Set-Content -Path (Join-Path $Bin "clipkit.cmd") -Encoding ASCII -Value "@`"$Venv\Scripts\clipkit.exe`" %*"
Set-UserPath $Bin $true
$env:Path = "$env:Path;$Bin"
$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut($Shortcut)
$lnk.TargetPath = Join-Path $Venv "Scripts\clipkit-gui.exe"
$ico = (& $vpy -c "import clipkit, pathlib; print(pathlib.Path(clipkit.__file__).parent / 'webui' / 'clipkit.ico')").Trim()
if (Test-Path $ico) { $lnk.IconLocation = "$ico,0" }
$lnk.Description = "Cut long videos into captioned short clips"
$lnk.Save()

& (Join-Path $Venv "Scripts\clipkit.exe") doctor
Write-Host ""
Write-Host "Done! Open ClipKit from the Start menu, or open a new terminal and type:" -ForegroundColor Green
Write-Host '    clipkit run "https://www.youtube.com/watch?v=..." -n 5'
