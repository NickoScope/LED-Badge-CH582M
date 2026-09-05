#!/bin/bash
# Установка всего необходимого. macOS, Apple Silicon.
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

echo "==> 1/5 hidapi (для USB HID)"
brew list --versions hidapi >/dev/null 2>&1 || brew install hidapi

echo "==> 2/5 python-окружения"
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q pyhidapi pyusb pillow
PY312="$(command -v python3.12 || echo /opt/homebrew/bin/python3.12)"
"$PY312" -m venv .venv312
.venv312/bin/pip install -q --upgrade pip
.venv312/bin/pip install -q bleak pillow

echo "==> 3/5 led-name-badge-ls32 (сборка битмапов и заголовка)"
[ -d led-name-badge-ls32 ] || git clone -q --depth 1 \
  https://github.com/fossasia/led-name-badge-ls32.git

echo "==> 4/5 подписанная копия Python.app для CoreBluetooth"
# Без NSBluetoothAlwaysUsageDescription macOS убивает процесс (Abort trap: 6, TCC).
# Запуск обязательно через LaunchServices, иначе TCC винит родительский процесс.
SRC="$("$PY312" -c 'import sys,os;print(os.path.join(sys.base_prefix,"Resources","Python.app"))')"
rm -rf BLEPython.app
cp -R "$SRC" BLEPython.app
/usr/libexec/PlistBuddy -c \
  "Add :NSBluetoothAlwaysUsageDescription string 'Управление LED-бейджем по BLE'" \
  BLEPython.app/Contents/Info.plist
/usr/libexec/PlistBuddy -c \
  "Set :CFBundleIdentifier local.ledbadge.blepython" BLEPython.app/Contents/Info.plist
codesign --force --deep --sign - BLEPython.app

echo "==> 5/5 wchisp (только для перепрошивки)"
mkdir -p tools
if [ ! -x tools/wchisp-macos-arm64/wchisp ]; then
  curl -sSL -o /tmp/wchisp.tar.gz \
    https://github.com/ch32-rs/wchisp/releases/download/v0.3.0/wchisp-v0.3.0-macos-arm64.tar.gz
  tar -xzf /tmp/wchisp.tar.gz -C tools
  xattr -d com.apple.quarantine tools/wchisp-macos-arm64/wchisp 2>/dev/null || true
fi

echo
echo "Готово. Дальше:"
echo "  ./badge  -m 0 'текст'        # USB HID, заводская прошивка"
echo "  ./badgeble -m 0 'текст'      # BLE"
echo "  ./synctime <PIN>             # синхронизация часов (badgemagic)"
echo
echo "Прошивку badgemagic качать отдельно - см. README, раздел про перепрошивку."
