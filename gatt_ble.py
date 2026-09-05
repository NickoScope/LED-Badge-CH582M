import os, sys, asyncio, traceback
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from badge_common import add_paths, find_badge
add_paths()
OUT = "/tmp/blegatt.txt"
def w(s):
    with open(OUT, "a", encoding="utf-8") as f: f.write(s + "\n")
open(OUT, "w").close()

ADDR = os.environ.get("BADGE_ADDR")  # не задан -> автопоиск по сервису FEE0
try:
    from bleak import BleakClient
    async def main():
        w("ищу бейдж в эфире...")
        async with BleakClient(ADDR, timeout=25.0) as c:
            w("ПОДКЛЮЧЕНО: %s" % c.is_connected)
            for s in c.services:
                w("")
                w("SERVICE %s  (%s)" % (s.uuid, s.description))
                for ch in s.characteristics:
                    w("   CHAR %s  props=%s  (%s)"
                      % (ch.uuid, ",".join(ch.properties), ch.description))
                    for d in ch.descriptors:
                        w("      DESC %s" % d.uuid)
        w("=== ГОТОВО ===")
    asyncio.run(main())
except Exception:
    w("ОШИБКА:\n" + traceback.format_exc())
