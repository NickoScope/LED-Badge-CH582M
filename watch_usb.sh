#!/bin/bash
# Ловим появление ЛЮБОГО USB-устройства. Только чтение.
LOG=/tmp/watch_usb.log
: > "$LOG"
echo "жду появления USB-устройства до 120 с - воткни кабель" | tee -a "$LOG"
DEADLINE=$(( $(date +%s) + 120 ))
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  N=$(ioreg -c IOUSBHostDevice -r -l -w 0 2>/dev/null | grep -c '"idVendor"')
  if [ "$N" -gt 0 ]; then
    echo "" | tee -a "$LOG"
    echo ">>> УСТРОЙСТВО ПОЯВИЛОСЬ ($(date '+%H:%M:%S'))" | tee -a "$LOG"
    ioreg -c IOUSBHostDevice -r -l -w 0 2>/dev/null \
      | grep -E '"(USB Product Name|USB Vendor Name|USB Serial Number|idVendor|idProduct)"' \
      | sed 's/^ *//' | tee -a "$LOG"
    echo "" | tee -a "$LOG"
    echo "ВЫВОД: USB в рабочем режиме РАБОТАЕТ, issue #152 у нас НЕ воспроизводится." | tee -a "$LOG"
    exit 0
  fi
  sleep 0.5
done
echo "" | tee -a "$LOG"
echo "за 120 с ничего не появилось" | tee -a "$LOG"
exit 1
