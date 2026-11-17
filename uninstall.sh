#!/bin/bash
# Removes Jarvis autostart and, if you agree, its files. Your config and keys are kept unless you say otherwise.
cd "$(dirname "$0")"
launchctl bootout "gui/$(id -u)/local.jarvis" 2>/dev/null && echo "Автозапуск остановлен." || true
rm -f "$HOME/Library/LaunchAgents/local.jarvis.plist"
read -r -p "Удалить установленные библиотеки (.venv)? [y/N] " a
[ "$a" = "y" ] && rm -rf .venv && echo "Библиотеки удалены."
read -r -p "Удалить память и логи Джарвиса (~/.jarvis-assistant)? [y/N] " a
[ "$a" = "y" ] && rm -rf "$HOME/.jarvis-assistant" && echo "Память и логи удалены."
echo "Готово. Папку с программой можно удалить вручную."
