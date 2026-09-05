import os, sys, asyncio, traceback
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from badge_common import add_paths, find_badge
add_paths()
OUT = "/tmp/fee1_read.txt"
def w(s):
    print(s, flush=True)
    with open(OUT, "a", encoding="utf-8") as f: f.write(s + "\n")
open(OUT, "w").close()
ADDR = os.environ.get("BADGE_ADDR")  # не задан -> автопоиск по сервису FEE0
CHAR = "0000fee1-0000-1000-8000-00805f9b34fb"
try:
    from bleak import BleakClient, BleakScanner
    async def main():
        w("жду появления бейджа в эфире (до 120 с) - нажми кнопку...")
        dev = None
        for i in range(12):
            dev = await find_badge(timeout=10.0, addr=ADDR)
            if dev:
                w("найден на %d-й попытке" % (i + 1))
                break
        if dev is None:
            w("НЕ ПОЯВИЛСЯ за 120 с")
            return
        async with BleakClient(dev, timeout=25.0) as c:
            raw = await c.read_gatt_char(CHAR)
            w("")
            w("FEE1 read = %s" % raw.hex(" "))
            w("первый байт = 0x%02x = %d" % (raw[0], raw[0]))
            w("")
            w("ДО записи было : 04  (режим 4 был последним)")
            w("Записали режим : 0")
            if raw[0] == 0:
                w(">>> ГИПОТЕЗА ПОДТВЕРЖДЕНА: FEE1 на чтение отдаёт номер текущего режима")
            elif raw[0] == 4:
                w(">>> НЕ ПОДТВЕРЖДЕНА: значение не изменилось, режим тут ни при чём")
            else:
                w(">>> ИЗМЕНИЛОСЬ на 0x%02x - это не номер режима, нужна ещё проверка" % raw[0])
        w("=== ГОТОВО ===")
    asyncio.run(main())
except Exception:
    w("ОШИБКА:\n" + traceback.format_exc())
