#!/bin/bash
# Установка на Raspberry Pi 5 (Raspberry Pi OS / Debian).
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

echo "==> системные пакеты"
sudo apt-get update -qq
sudo apt-get install -y python3-venv python3-pip bluez

echo "==> venv"
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

echo "==> доступ к Bluetooth без sudo"
# bleak ходит через D-Bus к BlueZ; пользователь должен быть в группе bluetooth
sudo usermod -aG bluetooth "$USER" || true

echo
echo "Готово. Проверка:"
echo "  .venv/bin/python badgectl scan"
echo "  .venv/bin/python badgectl info"
echo
echo "Служба:"
echo "  sudo cp badge-daemon.service /etc/systemd/system/"
echo "  sudo systemctl enable --now badge-daemon"
echo
echo "Если группа bluetooth добавлена только что — перелогинься."
