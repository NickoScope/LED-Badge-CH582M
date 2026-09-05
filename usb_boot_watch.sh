#!/bin/bash
# Чистое наблюдение: НИ ОДНОЙ команды устройству, только ioreg 4 раза в секунду.
# Ждём появления любого USB-устройства до 5 минут, затем пишем всё,
# что происходит на шине, ещё 120 секунд.
LOG=/tmp/usb_boot.log
: > "$LOG"
snap() { ioreg -c IOUSBHostDevice -r -l -w 0 2>/dev/null \
         | grep -E '"(USB Product Name|USB Vendor Name|idVendor|idProduct)"' \
         | sed 's/^ *//' | sort -u | tr '\n' ' '; }
n() { ioreg -c IOUSBHostDevice -r -l -w 0 2>/dev/null | grep -c '"idVendor"'; }

echo "$(date '+%H:%M:%S')  МОНИТОР ЗАПУЩЕН — шина пуста, жду до 5 минут" | tee -a "$LOG"
DEADLINE=$(( $(date +%s) + 300 ))
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  [ "$(n)" -gt 0 ] && break
  sleep 0.25
done
if [ "$(n)" -eq 0 ]; then
  echo "$(date '+%H:%M:%S')  за 5 минут на шине ничего не появилось" | tee -a "$LOG"
  exit 1
fi

T0=$(date +%s); PREV=""
echo "$(date '+%H:%M:%S')  >>> ЧТО-ТО ПОЯВИЛОСЬ, пишу 120 секунд" | tee -a "$LOG"
while [ $(( $(date +%s) - T0 )) -lt 120 ]; do
  S="$(snap)"
  if [ "$S" != "$PREV" ]; then
    if [ -z "$S" ]; then
      echo "  +$(( $(date +%s) - T0 ))с  шина ПУСТА" | tee -a "$LOG"
    else
      echo "  +$(( $(date +%s) - T0 ))с  $S" | tee -a "$LOG"
    fi
    PREV="$S"
  fi
  sleep 0.25
done
echo "$(date '+%H:%M:%S')  === ГОТОВО ===" | tee -a "$LOG"
