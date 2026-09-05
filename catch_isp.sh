#!/bin/bash
# Ловим бейдж в ISP. Прошлая версия детектила через ioreg и звала wchisp один раз -
# libusb не успевал поднять устройство. Теперь опрашиваем сам wchisp в цикле.
# ТОЛЬКО ЧТЕНИЕ: info / eeprom dump. Ни erase, ни flash, ни config.
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
W="$DIR/tools/wchisp-macos-arm64/wchisp"
LOG=/tmp/isp_catch.log
: > "$LOG"
echo "жду ISP-режим до 180 секунд (опрос wchisp каждые 0.3 с)..." | tee -a "$LOG"
DEADLINE=$(( $(date +%s) + 180 ))
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  OUT="$("$W" info 2>&1)"
  if ! echo "$OUT" | grep -q "No WCH ISP USB device found"; then
    echo "" | tee -a "$LOG"
    echo ">>> ПОЙМАЛ ($(date '+%H:%M:%S'))" | tee -a "$LOG"
    echo "" | tee -a "$LOG"
    echo "=== wchisp info ===" | tee -a "$LOG"
    echo "$OUT" | tee -a "$LOG"
    echo "" | tee -a "$LOG"
    echo "=== wchisp eeprom dump ===" | tee -a "$LOG"
    "$W" eeprom dump 2>&1 | tee -a "$LOG"
    echo "" | tee -a "$LOG"
    echo "=== ГОТОВО ===" | tee -a "$LOG"
    exit 0
  fi
  sleep 0.3
done
echo "ISP-режим не пойман за 180 с" | tee -a "$LOG"
exit 1
