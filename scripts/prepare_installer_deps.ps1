# Prepare FFmpeg for TubeDub_Setup.exe build
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$dest = Join-Path $root "tools\ffmpeg"
New-Item -ItemType Directory -Force -Path $dest | Out-Null

function Find-Bin($name) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

$copied = @()
foreach ($bin in @("ffmpeg", "ffprobe")) {
    $src = Find-Bin $bin
    if (-not $src) {
        Write-Warning "$bin not found in PATH"
        continue
    }
    $target = Join-Path $dest "$bin.exe"
    Copy-Item -Path $src -Destination $target -Force
    $copied += $bin
    Write-Host "OK: $src -> $target"
}

if ($copied.Count -lt 2) {
    Write-Host "Install FFmpeg: winget install Gyan.FFmpeg"
    exit 1
}

Write-Host "FFmpeg ready in tools\ffmpeg\"

$isccPaths = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
)
$iscc = $isccPaths | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($iscc) {
    Write-Host "Inno Setup: $iscc"
} else {
    Write-Host "Inno Setup 6 missing - winget install JRSoftware.InnoSetup"
}

python -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -eq 0) { Write-Host "PyInstaller: OK" } else { Write-Host "PyInstaller: pip install pyinstaller" }

Write-Host "Build via Settings -> Owner panel -> Create installer"
