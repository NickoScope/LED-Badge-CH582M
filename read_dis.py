import os, sys, asyncio, traceback
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from badge_common import add_paths, find_badge
add_paths()
OUT = "/tmp/badge_dis.txt"
def w(s):
    print(s, flush=True)
    with open(OUT, "a", encoding="utf-8") as f: f.write(s + "\n")
open(OUT, "w").close()

ADDR = os.environ.get("BADGE_ADDR")  # не задан -> автопоиск по сервису FEE0
NAMES = {
    "00002a23": "System ID",
    "00002a24": "Model Number",
    "00002a25": "Serial Number",
    "00002a26": "Firmware Revision",
    "00002a27": "Hardware Revision",
    "00002a28": "Software Revision",
    "00002a29": "Manufacturer Name",
    "00002a2a": "IEEE Reg. Cert.",
    "00002a50": "PnP ID",
}
try:
    from bleak import BleakClient, BleakScanner
    async def main():
        w("ищу LSLED в эфире...")
        dev = await find_badge(timeout=30.0, addr=ADDR)
        if dev is None:
            w("НЕ НАЙДЕН — бейдж не в BT-режиме. Нажми кнопку ещё раз.")
            return
        w("найден, подключаюсь...")
        async with BleakClient(dev, timeout=25.0) as c:
            w("ПОДКЛЮЧЕНО")
            w("")
            for uuid16, label in NAMES.items():
                full = "%s-0000-1000-8000-00805f9b34fb" % uuid16
                try:
                    raw = await c.read_gatt_char(full)
                    try:
                        txt = raw.decode("utf-8").strip("\x00").strip()
                    except Exception:
                        txt = ""
                    hexs = raw.hex(" ")
                    w("%-20s : %-30s   hex: %s" % (label, repr(txt) if txt else "(не текст)", hexs))
                except Exception as e:
                    w("%-20s : ОШИБКА ЧТЕНИЯ %r" % (label, e))
            w("")
            w("--- пробую прочитать FEE1 (назначение read/notify не документировано) ---")
            try:
                raw = await c.read_gatt_char("0000fee1-0000-1000-8000-00805f9b34fb")
                w("FEE1 read -> %d байт, hex: %s" % (len(raw), raw.hex(" ")))
            except Exception as e:
                w("FEE1 read -> ОШИБКА %r" % e)
        w("=== ГОТОВО ===")
    asyncio.run(main())
except Exception:
    w("ОШИБКА:\n" + traceback.format_exc())
