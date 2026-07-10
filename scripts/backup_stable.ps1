# Резервная копия эталонного состояния TubeDub
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$stamp = Get-Date -Format "yyyyMMdd_HHmm"
$dest = Join-Path $root "output\backups\TubeDub_stable_$stamp.zip"
New-Item -ItemType Directory -Force -Path (Split-Path $dest) | Out-Null

$exclude = @('__pycache__', '.git', 'node_modules', '.venv', 'venv', 'dist', 'build', 'output', 'uploads', '.cursor')
$items = Get-ChildItem -Path $root -Force | Where-Object { $exclude -notcontains $_.Name }

Compress-Archive -Path ($items | ForEach-Object { $_.FullName }) -DestinationPath $dest -Force
Write-Host "Backup: $dest"
