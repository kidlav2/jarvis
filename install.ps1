# Jarvis installer for Windows. Safe to run again: updates what is there and keeps your settings.
# Easiest start: double-click install.cmd
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$Dir = $PSScriptRoot
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
function Step($t) { Write-Host "`n==> $t" -ForegroundColor Cyan }
function Die($t)  { Write-Host "`nОшибка: $t" -ForegroundColor Red; Read-Host "Нажми Enter"; exit 1 }

Step "1/5 Проверяю Python 3.12"
$py = $null
if (Get-Command py -ErrorAction SilentlyContinue) {
    foreach ($v in "3.12", "3.11", "3.10") { try { & py "-$v" -c "1" 2>$null } catch {}; if ($LASTEXITCODE -eq 0) { $py = @("py", "-$v"); break } }
}
if (-not $py) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        $a = Read-Host "Python 3.12 не найден. Установить через winget? [Y/n]"
        if ($a -eq "n") { Die "нужен Python 3.12 с python.org (галочка Add python.exe to PATH)" }
        winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
        $py = @("py", "-3.12")
    } else { Die "нужен Python 3.12: https://www.python.org/downloads/windows/ (галочка Add python.exe to PATH), затем запусти установщик снова" }
}
Write-Host "    $($py -join ' ')"

Step "2/5 Ставлю зависимости (первый раз 2-5 минут)"
if (-not (Test-Path .venv\Scripts\python.exe)) { & $py[0] $py[1] -m venv .venv }
$vpy = Join-Path $Dir ".venv\Scripts\python.exe"
& $vpy -m pip install -q --upgrade pip
& $vpy -m pip install -q -r requirements.txt
if ($LASTEXITCODE -ne 0) { Die "не удалось поставить зависимости, см. сообщения выше" }
Write-Host "    готово"

Step "3/5 Настройка"
& $vpy jarvis.py --setup

Step "4/5 Проверка"
& $vpy jarvis.py --doctor
if ($LASTEXITCODE -ne 0) { Write-Host "    Есть проблемы: исправь по подсказкам выше и запусти установщик снова." -ForegroundColor Yellow }

Step "5/5 Автозапуск"
$startup = [Environment]::GetFolderPath("Startup")
$vbs = Join-Path $startup "Jarvis.vbs"
$a = Read-Host "Запускать Джарвиса автоматически при входе в Windows? [Y/n]"
if ($a -ne "n") {
    $pyw = Join-Path $Dir ".venv\Scripts\pythonw.exe"
    $script = Join-Path $Dir "jarvis.py"
    $content = "Set sh = CreateObject(""WScript.Shell"")`r`nsh.CurrentDirectory = ""$Dir""`r`nsh.Run """"""$pyw"""" """"$script"""" --service"", 0, False"
    Set-Content -Path $vbs -Value $content -Encoding Unicode
    Start-Process wscript.exe -ArgumentList "`"$vbs`""
    Write-Host "    Джарвис запущен в фоне и будет стартовать сам. Лог: $env:USERPROFILE\.jarvis-assistant\jarvis.log"
} else {
    Write-Host "    Запуск вручную: $vpy jarvis.py"
}
Write-Host "`nГотово. Скажи «Привет, Джарвис»." -ForegroundColor Green
Write-Host "Удалить: uninstall.cmd"
Read-Host "Нажми Enter, чтобы закрыть окно"
