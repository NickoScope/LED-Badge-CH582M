#!/usr/bin/env python3
"""Вернуть настройки после стирания EEPROM.

Команды next-gen профиля (F055/F057), авторизация не требуется - гейт !authorized
стоит только в legacy_ble_rx(). Байты взяты из src/ngctrl.c, а не из BadgeBLE.md:
документация описывает power_setting наоборот.
"""
import os, sys, asyncio, traceback
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from badge_common import add_paths, find_badge, NG_WRITE, NG_NOTIFY
add_paths()

OUT = "/tmp/restore_cfg.log"
def log(s):
    print(s, flush=True)
    with open(OUT, "a", encoding="utf-8") as f: f.write(s + "\n")
open(OUT, "w").close()

CMDS = [
    ("ребут после приёма — ВЫКЛ", bytes((0x01, 0x01, 0x00))),  # power_setting -> cfg_reset_rx(0)
    ("always-on BLE — ВКЛ",       bytes((0x04, 0x00, 0x01))),  # ble_setting -> cfg_ble_alwayon(1)
    ("сохранить во флеш",         bytes((0x06,))),             # save_cfg
]


async def main():
    from bleak import BleakClient
    log("ищу бейдж...")
    dev = await find_badge(timeout=40.0)
    if dev is None:
        log("НЕ НАЙДЕН — включи BT-PAIRING в меню"); return
    async with BleakClient(dev, timeout=25.0) as c:
        log("подключено, MTU=%s" % getattr(c, "mtu_size", "?"))
        try:
            await c.start_notify(NG_NOTIFY, lambda _, d: log("   ответ <- %s" % bytes(d).hex()))
        except Exception as e:
            log("   notify недоступен: %r" % e)
        log("")
        for name, payload in CMDS:
            await c.write_gatt_char(NG_WRITE, payload, response=True)
            log("%-30s [%s]" % (name, payload.hex(" ")))
            await asyncio.sleep(0.6)
    log("")
    log("=== ГОТОВО — настройки сохранены ===")

try:
    asyncio.run(main())
except Exception:
    log("ОШИБКА:\n" + traceback.format_exc())
