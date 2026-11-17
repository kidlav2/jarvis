# Jarvis для Windows: зависимости и автозапуск при входе. Запуск: правый клик -> "Выполнить с помощью PowerShell"
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Get-Command py -ErrorAction SilentlyContinue)) { Write-Host "Нужен Python 3.12 с python.org (галочка Add to PATH)"; exit 1 }
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -q --upgrade pip
.\.venv\Scripts\python.exe -m pip install -q -r requirements.txt
if (-not (Test-Path config.json)) { Copy-Item config.example.json config.json }
Write-Host "Готово. Открой config.json и впиши своё имя (owner_name) и город."
Write-Host "Проверка:  .\.venv\Scripts\python.exe jarvis.py --test"
$a = Read-Host "Запускать Джарвиса автоматически при входе в Windows? [y/N]"
if ($a -eq "y") {
  $startup = [Environment]::GetFolderPath("Startup")
  $logdir = Join-Path $env:USERPROFILE ".jarvis-assistant"; New-Item -ItemType Directory -Force $logdir | Out-Null
  $cmd = "@echo off`r`ncd /d `"$PSScriptRoot`"`r`n`".venv\Scripts\pythonw.exe`" -u jarvis.py >> `"$logdir\jarvis.log`" 2>&1"
  Set-Content -Path (Join-Path $startup "Jarvis.cmd") -Value $cmd -Encoding ASCII
  Write-Host "Автозапуск включён. Лог: $logdir\jarvis.log"
}
