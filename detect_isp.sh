#!/bin/bash
# ТОЛЬКО ОБНАРУЖЕНИЕ. Никаких config/flash/erase - лишь чтение info.
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
W="$DIR/tools/wchisp-macos-arm64/wchisp"
LOG=/tmp/detect_isp.log
: > "$LOG"
echo "жду ISP до 120 с - долгое нажатие KEY2 на бейдже" | tee -a "$LOG"
DEADLINE=$(( $(date +%s) + 120 ))
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  OUT="$("$W" info 2>&1)"
  if ! echo "$OUT" | grep -q "No WCH ISP USB device found"; then
    echo "" | tee -a "$LOG"
    echo ">>> ISP-УСТРОЙСТВО ВИДНО ($(date '+%H:%M:%S'))" | tee -a "$LOG"
    echo "$OUT" | grep -E "Chip:|UID|BTVER" | tee -a "$LOG"
    echo "" | tee -a "$LOG"
    echo "ВЫВОД: кабель передаёт данные, порт исправен." | tee -a "$LOG"
    echo "       Значит USB не поднимается именно в рабочем режиме -" | tee -a "$LOG"
    echo "       ровно то, что описано в issue #152." | tee -a "$LOG"
    exit 0
  fi
  sleep 0.3
done
echo "ISP не появился за 120 с" | tee -a "$LOG"
echo "ВЫВОД: скорее всего кабель без линий данных либо порт неисправен." | tee -a "$LOG"
exit 1
