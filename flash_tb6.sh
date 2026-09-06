#!/bin/bash
# Прошивка варианта usb-c-2key-tb6: пин T = B6, J и K дефолтные.
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
W="$DIR/tools/wchisp-macos-arm64/wchisp"
BIN="${1:-$DIR/firmware/badgemagic-usb-c-2key-tb6-mtu128.bin}"
LOG=/tmp/flash_tb6.log
: > "$LOG"
echo "жду ISP до 240 с - долгое нажатие KEY2" | tee -a "$LOG"
shasum -a 256 "$BIN" | tee -a "$LOG"
DEADLINE=$(( $(date +%s) + 240 ))
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  if ! "$W" info 2>&1 | grep -q "No WCH ISP USB device found"; then
    echo "" | tee -a "$LOG"; echo ">>> ISP ПОЙМАН ($(date '+%H:%M:%S'))" | tee -a "$LOG"
    "$W" config reset 2>&1 | tail -2 | tee -a "$LOG"
    # wchisp после "Device reset" может ждать ответа от чипа, который уже
    # ушёл в прошивку, и не выходить никогда (см. CLAUDE.md). Сторож
    # добивает его через 30 с; запись к этому моменту давно завершена,
    # и лог это покажет. grep без буфера — чтобы строки шли сразу.
    "$W" flash "$BIN" 2>&1 | grep --line-buffered -E "Chip:|Erase|written|Verify|reset|rror" | tee -a "$LOG" &
    PIPE=$!
    for _ in $(seq 1 100); do kill -0 "$PIPE" 2>/dev/null || break; sleep 0.3; done
    if kill -0 "$PIPE" 2>/dev/null; then
      pkill -TERM -f "wchisp flash" 2>/dev/null; sleep 1
      pkill -KILL -f "wchisp flash" 2>/dev/null
      echo "wchisp не вышел за 30 с и снят сторожем; итог ниже — только по строке wchisp" | tee -a "$LOG"
    fi
    wait "$PIPE" 2>/dev/null
    # Судим строго по строке самого wchisp, чтобы никакой наш текст не совпал.
    if grep -qE '\[INFO\] Verify OK' "$LOG"; then
      echo "ПРОШИТО: верификация wchisp прошла" | tee -a "$LOG"; exit 0
    fi
    echo "верификации wchisp в логе НЕТ — прошивка не подтверждена, смотри $LOG" | tee -a "$LOG"; exit 2
  fi
  sleep 0.3
done
echo "ISP не пойман за 240 с - ничего не изменено" | tee -a "$LOG"; exit 1
