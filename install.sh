#!/bin/bash
# Jarvis для macOS: ставит Python-зависимости и (по желанию) автозапуск при входе.
set -e
cd "$(dirname "$0")"
PY=""
for v in python3.12 python3.11 python3.10; do command -v $v >/dev/null && { PY=$v; break; }; done
[ -n "$PY" ] || { echo "Нужен Python 3.10–3.12: https://www.python.org/downloads/ (или brew install python@3.12)"; exit 1; }
$PY -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
[ -f config.json ] || cp config.example.json config.json
echo "Готово. Открой config.json и впиши своё имя (owner_name) и город."
echo "Проверка:  .venv/bin/python jarvis.py --test"
echo "Запуск:    .venv/bin/python jarvis.py"
read -p "Запускать Джарвиса автоматически при входе в Mac? [y/N] " a
if [ "$a" = "y" ]; then
  P=~/Library/LaunchAgents/local.jarvis.plist
  cat > "$P" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>local.jarvis</string>
<key>ProgramArguments</key><array><string>$PWD/.venv/bin/python</string><string>-u</string><string>$PWD/jarvis.py</string></array>
<key>EnvironmentVariables</key><dict><key>PATH</key><string>$PATH</string></dict>
<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
<key>StandardOutPath</key><string>$HOME/.jarvis-assistant/jarvis.log</string><key>StandardErrorPath</key><string>$HOME/.jarvis-assistant/jarvis.log</string>
</dict></plist>
PL
  mkdir -p ~/.jarvis-assistant; launchctl bootstrap gui/$(id -u) "$P" && echo "Автозапуск включён. Лог: ~/.jarvis-assistant/jarvis.log"
fi
