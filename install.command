#!/bin/bash
# Двойной клик в Finder запускает установку Джарвиса.
cd "$(dirname "$0")"
bash install.sh
read -r -p "Нажми Enter, чтобы закрыть окно…" _
