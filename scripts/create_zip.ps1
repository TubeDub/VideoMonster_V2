# Создание ZIP-архива исходников (без cache/models/output)
$ErrorActionPreference = "Stop"
$src = Split-Path -Parent $PSScriptRoot
$dest = Join-Path $src "output\VideoMonster_V2_ready.zip"
$temp = Join-Path $env:TEMP "vm2_zip_staging"

$excludeDirs = @(
    '__pycache__', '.git', '.github', 'node_modules', '.venv', 'venv',
    'dist', 'build', 'cache', 'models', 'output', 'uploads', 'projects',
    '.pytest_cache', '.ruff_cache', '.cursor'
)

if (Test-Path $temp) { Remove-Item $temp -Recurse -Force }
New-Item -ItemType Directory -Path $temp | Out-Null

Get-ChildItem $src -Force | Where-Object {
    $_.Name -notin $excludeDirs
} | ForEach-Object {
    Copy-Item $_.FullName -Destination (Join-Path $temp $_.Name) -Recurse -Force
}

Get-ChildItem $temp -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
@('license.json', '.env') | ForEach-Object {
    Get-ChildItem $temp -Recurse -Filter $_ -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
}

New-Item -ItemType Directory -Force -Path (Join-Path $src "output") | Out-Null
if (Test-Path $dest) { Remove-Item $dest -Force }
Compress-Archive -Path (Join-Path $temp '*') -DestinationPath $dest -Force
Remove-Item $temp -Recurse -Force

$size = (Get-Item $dest).Length
Write-Host "Created: $dest"
Write-Host "Size: $([math]::Round($size/1MB, 2)) MB"
