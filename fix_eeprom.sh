#!/bin/bash
# ВОССТАНОВЛЕНИЕ: стереть EEPROM, где лежит слишком длинная анимация,
# из-за которой load_bmlist() падает на malloc и бейдж виснет на заставке.
# Кодовый флеш (прошивка) НЕ ТРОГАЕТСЯ - ни flash, ни config reset здесь нет.
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
W="$DIR/tools/wchisp-macos-arm64/wchisp"
LOG=/tmp/fix_eeprom.log
: > "$LOG"
echo "жду ISP до 300 с..." | tee -a "$LOG"
DEADLINE=$(( $(date +%s) + 300 ))
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  if ! "$W" info 2>&1 | grep -q "No WCH ISP USB device found"; then
    echo "" | tee -a "$LOG"
    echo ">>> ISP ПОЙМАН ($(date '+%H:%M:%S'))" | tee -a "$LOG"
    "$W" info 2>&1 | grep -E "Chip:|UID" | tee -a "$LOG"
    echo "" | tee -a "$LOG"
    echo "--- стираю EEPROM ---" | tee -a "$LOG"
    "$W" eeprom erase 2>&1 | tail -5 | tee -a "$LOG"
    echo "" | tee -a "$LOG"
    echo "--- контроль: первые строки дампа ---" | tee -a "$LOG"
    "$W" eeprom dump 2>&1 | grep -E "^0000:|^0016:" | tee -a "$LOG"
    echo "" | tee -a "$LOG"
    echo "--- сброс устройства ---" | tee -a "$LOG"
    "$W" reset 2>&1 | tail -2 | tee -a "$LOG"
    echo "=== ГОТОВО ===" | tee -a "$LOG"
    exit 0
  fi
  sleep 0.3
done
echo "ISP не появился за 300 с - ничего не изменено" | tee -a "$LOG"
exit 1
