#!/usr/bin/env python3
"""Где именно обрывается кадр: пять блоков-меток по всей ширине.

Блоки по 4 столбца начинаются со столбцов 0, 9, 18, 27, 36.
Граница первого ATT-фрагмента при MTU=64 приходится на столбец 29,
то есть на середину четвёртого блока. Если хвост теряется - блок 4
окажется обрезанным, блока 5 не будет вовсе.
"""
import os, sys, asyncio, traceback
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from badge_common import add_paths, find_badge, NG_WRITE
add_paths()

OUT = "/tmp/diag_blocks.log"
def log(s):
    print(s, flush=True)
    with open(OUT, "a", encoding="utf-8") as f: f.write(s + "\n")
open(OUT, "w").close()

PANEL = 44
ALL = 0x07FF
STARTS = [0, 9, 18, 27, 36]


def blocks_cols():
    cols = [0] * PANEL
    for b, st in enumerate(STARTS):
        for x in range(st, min(st + 4, PANEL)):
            cols[x] = ALL
    return cols


def payload(cols, n):
    b = bytearray([0x03])
    for i in range(n):
        v = cols[i] if i < len(cols) else 0
        b += bytes((v & 0xFF, (v >> 8) & 0xFF))
    return bytes(b)


async def main():
    from bleak import BleakClient
    dev = await find_badge(timeout=30.0)
    if dev is None:
        log("НЕ НАЙДЕН - включи BT-PAIRING"); return
    async with BleakClient(dev, timeout=25.0) as c:
        log("подключено, MTU=%s" % getattr(c, "mtu_size", "?"))
        await c.write_gatt_char(NG_WRITE, bytes((0x02, 0x00)), response=True)
        cols = blocks_cols()

        log("")
        log("ТЕСТ A: пять блоков, ОДНА длинная запись на все 44 столбца")
        log("        блоки начинаются со столбцов 0, 9, 18, 27, 36")
        log("        >>> СМОТРИ 8 СЕКУНД, СЧИТАЙ БЛОКИ СЛЕВА НАПРАВО <<<")
        await c.write_gatt_char(NG_WRITE, payload(cols, PANEL), response=True)
        await asyncio.sleep(8)

        log("")
        log("ТЕСТ B: то же, но только 30 столбцов - одной короткой посылкой")
        log("        ожидание: блоки 1,2,3 целиком, блок 4 обрезан, блока 5 нет")
        log("        >>> СМОТРИ 8 СЕКУНД <<<")
        await c.write_gatt_char(NG_WRITE, payload([0] * PANEL, 30), response=True)
        await asyncio.sleep(1)
        await c.write_gatt_char(NG_WRITE, payload(cols, 30), response=True)
        await asyncio.sleep(8)

        await c.write_gatt_char(NG_WRITE, bytes((0x02, 0x01)), response=True)
        log("")
        log("=== ГОТОВО ===")

try:
    asyncio.run(main())
except Exception:
    log("ОШИБКА:\n" + traceback.format_exc())
