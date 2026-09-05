#!/usr/bin/env python3
"""Ровно ли доезжает кадр до правого края - по одному вопросу на кадр.

Каждый кадр зажигает ТОЛЬКО две колонки. Ответ - горят они или нет.
Все кадры одинаковой длины (44 столбца), значит условия идентичны,
и разница в результате может быть только в самих колонках.
"""
import os, sys, asyncio, traceback
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from badge_common import add_paths, find_badge, NG_WRITE
add_paths()

OUT = "/tmp/diag_edge.log"
def log(s):
    print(s, flush=True)
    with open(OUT, "a", encoding="utf-8") as f: f.write(s + "\n")
open(OUT, "w").close()

PANEL, ALL = 44, 0x07FF


def only(pairs):
    cols = [0] * PANEL
    for x in pairs:
        cols[x] = ALL
    b = bytearray([0x03])
    for v in cols:
        b += bytes((v & 0xFF, (v >> 8) & 0xFF))
    return bytes(b)


STEPS = [
    ("A", (20, 21), "середина панели - контроль, заведомо в первом ATT-фрагменте"),
    ("B", (30, 31), "сразу ЗА границей фрагмента - горят ли, значит хвост доезжает"),
    ("C", (38, 39), "столбцы пина T, который различается между ревизиями платы"),
    ("D", (42, 43), "самые правые - те, что у тебя не горят"),
]


async def main():
    from bleak import BleakClient
    dev = await find_badge(timeout=30.0)
    if dev is None:
        log("НЕ НАЙДЕН - включи BT-PAIRING"); return
    async with BleakClient(dev, timeout=25.0) as c:
        log("подключено, MTU=%s" % getattr(c, "mtu_size", "?"))
        await c.write_gatt_char(NG_WRITE, bytes((0x02, 0x00)), response=True)
        log("")
        log("Четыре кадра по 6 секунд. В каждом горят РОВНО ДВЕ колонки.")
        log("Запомни для каждого: горит или нет.")
        log("")
        for name, pair, why in STEPS:
            log(">>> %s: колонки %d и %d - %s" % (name, pair[0], pair[1], why))
            await c.write_gatt_char(NG_WRITE, only(pair), response=True)
            await asyncio.sleep(6)
        log("")
        log(">>> E: все 44 - последний кадр, посчитай тёмные места")
        await c.write_gatt_char(NG_WRITE, only(range(PANEL)), response=True)
        await asyncio.sleep(8)
        await c.write_gatt_char(NG_WRITE, bytes((0x02, 0x01)), response=True)
        log("")
        log("=== ГОТОВО ===")

try:
    asyncio.run(main())
except Exception:
    log("ОШИБКА:\n" + traceback.format_exc())
