#!/bin/bash
# Прошивка варианта usb-c-2key-tb6: пин T = B6, J и K дефолтные.
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
W="$DIR/tools/wchisp-macos-arm64/wchisp"
BIN="$DIR/firmware/badgemagic-usb-c-2key-tb6.bin"
LOG=/tmp/flash_tb6.log
: > "$LOG"
echo "жду ISP до 240 с - долгое нажатие KEY2" | tee -a "$LOG"
shasum -a 256 "$BIN" | tee -a "$LOG"
DEADLINE=$(( $(date +%s) + 240 ))
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  if ! "$W" info 2>&1 | grep -q "No WCH ISP USB device found"; then
    echo "" | tee -a "$LOG"; echo ">>> ISP ПОЙМАН ($(date '+%H:%M:%S'))" | tee -a "$LOG"
    "$W" config reset 2>&1 | tail -2 | tee -a "$LOG"
    "$W" flash "$BIN" 2>&1 | grep -E "Chip:|Erase|written|Verify|reset" | tee -a "$LOG"
    echo "код возврата: $?" | tee -a "$LOG"
    exit 0
  fi
  sleep 0.3
done
echo "ISP не пойман за 240 с - ничего не изменено" | tee -a "$LOG"; exit 1
