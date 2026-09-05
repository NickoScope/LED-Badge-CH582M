#!/usr/bin/env python3
"""Диагностика: доходит ли ХВОСТ длинной записи до фреймбуфера.

Гипотеза: write_handler в ng.c игнорирует offset, поэтому второй фрагмент
длинной записи разбирается как новая команда (её код = старший байт столбца,
обычно 0x00 = next_packet, пустышка). Тогда столбцы после ~29-го не пишутся.
"""
import os, sys, asyncio, time, traceback
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from badge_common import add_paths, find_badge, NG_WRITE
add_paths()

OUT = "/tmp/diag_width.log"
def log(s):
    print(s, flush=True)
    with open(OUT, "a", encoding="utf-8") as f: f.write(s + "\n")
open(OUT, "w").close()

PANEL, ROWS = 44, 11
ALL = 0x07FF          # все 11 строк


def payload(ncols, val):
    b = bytearray([0x03])
    for _ in range(ncols):
        b += bytes((val & 0xFF, (val >> 8) & 0xFF))
    return bytes(b)


async def main():
    from bleak import BleakClient
    dev = await find_badge(timeout=30.0)
    if dev is None:
        log("НЕ НАЙДЕН - включи BT-PAIRING"); return
    async with BleakClient(dev, timeout=25.0) as c:
        log("подключено, MTU=%s" % getattr(c, "mtu_size", "?"))
        await c.write_gatt_char(NG_WRITE, bytes((0x02, 0x00)), response=True)

        log("")
        log("ТЕСТ 1: гашу всё - 30 столбцов (одна посылка, 61 байт)")
        await c.write_gatt_char(NG_WRITE, payload(30, 0), response=True)
        await asyncio.sleep(2)

        log("ТЕСТ 2: ЗАЖИГАЮ ВСЕ 44 столбца одной длинной записью (89 байт)")
        log("        >>> СМОТРИ НА ЭКРАН 6 СЕКУНД <<<")
        log("        если горят не все 44 - хвост длинной записи теряется")
        await c.write_gatt_char(NG_WRITE, payload(PANEL, ALL), response=True)
        await asyncio.sleep(6)

        log("")
        log("ТЕСТ 3: гашу 30 столбцов - должна остаться гореть только правая часть")
        log("        >>> СМОТРИ 6 СЕКУНД <<<")
        log("        сколько столбцов справа осталось гореть - столько дошло из хвоста")
        await c.write_gatt_char(NG_WRITE, payload(30, 0), response=True)
        await asyncio.sleep(6)

        await c.write_gatt_char(NG_WRITE, bytes((0x02, 0x01)), response=True)
        log("")
        log("=== ГОТОВО ===")

try:
    asyncio.run(main())
except Exception:
    log("ОШИБКА:\n" + traceback.format_exc())
