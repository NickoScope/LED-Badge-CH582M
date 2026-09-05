#!/bin/bash
# ПРОШИВКА. Точка невозврата: config reset снимает защиту и СТИРАЕТ флеш,
# заводская прошивка исчезает безвозвратно (её нельзя было забэкапить -
# CFG_ROM_READ = 0, проверено на этом устройстве).
# Цель: badgemagic master b2137f08, вариант usb-c-2key.
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
W="$DIR/tools/wchisp-macos-arm64/wchisp"
BIN="${1:-$DIR/firmware/badgemagic-usb-c-2key.bin}"
LOG=/tmp/flash_badge.log
: > "$LOG"
echo "прошивальщик взведён, жду ISP-режим (до 240 с)" | tee -a "$LOG"
echo "файл: $BIN" | tee -a "$LOG"
shasum -a 256 "$BIN" | tee -a "$LOG"
DEADLINE=$(( $(date +%s) + 240 ))
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  if ! "$W" info 2>&1 | grep -q "No WCH ISP USB device found"; then
    echo "" | tee -a "$LOG"
    echo ">>> ISP ПОЙМАН ($(date '+%H:%M:%S')) - НАЧИНАЮ ПРОШИВКУ" | tee -a "$LOG"
    echo "" | tee -a "$LOG"
    echo "--- wchisp config reset ---" | tee -a "$LOG"
    "$W" config reset 2>&1 | tee -a "$LOG"
    echo "" | tee -a "$LOG"
    echo "--- wchisp flash ---" | tee -a "$LOG"
    "$W" flash "$BIN" 2>&1 | tee -a "$LOG"
    RC=$?
    echo "" | tee -a "$LOG"
    echo "код возврата: $RC" | tee -a "$LOG"
    echo "=== ЗАВЕРШЕНО ===" | tee -a "$LOG"
    exit $RC
  fi
  sleep 0.3
done
echo "ISP не пойман за 240 с - ничего не изменено" | tee -a "$LOG"
exit 1
