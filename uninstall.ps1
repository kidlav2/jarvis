# Removes Jarvis autostart and, if you agree, its files.
Set-Location $PSScriptRoot
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$vbs = Join-Path ([Environment]::GetFolderPath("Startup")) "Jarvis.vbs"
if (Test-Path $vbs) { Remove-Item $vbs; Write-Host "Автозапуск убран." }
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
    Where-Object { $_.CommandLine -like "*jarvis.py*" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Host "Джарвис остановлен." }
if ((Read-Host "Удалить установленные библиотеки (.venv)? [y/N]") -eq "y") { Remove-Item -Recurse -Force .venv; Write-Host "Библиотеки удалены." }
if ((Read-Host "Удалить память и логи (~\.jarvis-assistant)? [y/N]") -eq "y") { Remove-Item -Recurse -Force (Join-Path $env:USERPROFILE ".jarvis-assistant"); Write-Host "Память и логи удалены." }
Write-Host "Готово. Папку с программой можно удалить вручную."
Read-Host "Нажми Enter"
