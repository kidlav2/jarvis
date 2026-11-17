#!/bin/bash
# Jarvis installer for macOS. Safe to run again: it updates what is there and keeps your settings.
set -euo pipefail
cd "$(dirname "$0")"
DIR="$PWD"
step() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }
die()  { printf '\n\033[31mОшибка:\033[0m %s\n' "$1"; exit 1; }

step "1/5 Проверяю Python 3.10–3.12"
PY=""
for v in python3.12 python3.11 python3.10 /opt/homebrew/bin/python3.12 /usr/local/bin/python3.12; do
  if command -v "$v" >/dev/null 2>&1; then PY="$(command -v "$v")"; break; fi
done
if [ -z "$PY" ]; then
  if command -v brew >/dev/null 2>&1; then
    read -r -p "Python 3.12 не найден. Установить через Homebrew? [Y/n] " a
    [ "${a:-y}" = "n" ] && die "нужен Python 3.12: https://www.python.org/downloads/macos/"
    brew install python@3.12
    PY="$(brew --prefix)/bin/python3.12"
  else
    die "нужен Python 3.12. Скачай установщик с https://www.python.org/downloads/macos/ и запусти этот файл снова."
  fi
fi
echo "    $PY"

step "2/5 Ставлю зависимости (первый раз 2–5 минут)"
[ -x .venv/bin/python ] || "$PY" -m venv .venv
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -r requirements.txt
echo "    готово"

step "3/5 Настройка"
.venv/bin/python jarvis.py --setup

step "4/5 Проверка"
.venv/bin/python jarvis.py --doctor || echo "    Есть проблемы: исправь по подсказкам выше и запусти установщик снова."

step "5/5 Автозапуск"
PLIST="$HOME/Library/LaunchAgents/local.jarvis.plist"
read -r -p "Запускать Джарвиса автоматически при входе в Mac? [Y/n] " a
if [ "${a:-y}" != "n" ]; then
  mkdir -p "$HOME/.jarvis-assistant" "$HOME/Library/LaunchAgents"
  cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>local.jarvis</string>
  <key>ProgramArguments</key><array>
    <string>$DIR/.venv/bin/python</string><string>-u</string><string>$DIR/jarvis.py</string><string>--service</string>
  </array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>$PATH</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict></plist>
PL
  launchctl bootout "gui/$(id -u)/local.jarvis" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  echo "    Джарвис запущен и будет стартовать сам. Лог: ~/.jarvis-assistant/jarvis.log"
  echo "    Если macOS спросит доступ к микрофону или камере для Python — нажми «Разрешить»."
else
  echo "    Запуск вручную:  $DIR/.venv/bin/python $DIR/jarvis.py"
fi

printf '\n\033[32mГотово.\033[0m Скажи «Привет, Джарвис».\n'
echo "Удалить: bash uninstall.sh"
