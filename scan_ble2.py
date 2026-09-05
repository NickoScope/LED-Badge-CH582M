import sys, os, asyncio, traceback
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from badge_common import add_paths, find_badge
add_paths()
OUT = "/tmp/blescan.txt"

def w(s):
    with open(OUT, "a", encoding="utf-8") as f:
        f.write(s + "\n")

open(OUT, "w").close()
try:
    from bleak import BleakScanner

    async def main():
        w("сканирую 12 секунд...")
        devs = await BleakScanner.discover(timeout=12.0, return_adv=True)
        if not devs:
            w("НИЧЕГО НЕ НАЙДЕНО")
            return
        rows = []
        for addr, (d, adv) in devs.items():
            name = adv.local_name or d.name or ""
            uuids = ",".join(adv.service_uuids or [])
            rows.append((adv.rssi if adv.rssi is not None else -999, addr, name, uuids))
        rows.sort(reverse=True)
        w("%-38s %-22s %5s  %s" % ("ADDR", "NAME", "RSSI", "SERVICE UUIDS"))
        for rssi, addr, name, uuids in rows:
            low = (name + " " + uuids).lower()
            mark = "   <<=== КАНДИДАТ" if ("fee0" in low or "led" in low or "badge" in low or "ls32" in low) else ""
            w("%-38s %-22s %5d  %s%s" % (addr, name[:22], rssi, uuids[:58], mark))
        w("ВСЕГО: %d" % len(rows))

    asyncio.run(main())
    w("=== ГОТОВО ===")
except Exception:
    w("ОШИБКА:\n" + traceback.format_exc())
