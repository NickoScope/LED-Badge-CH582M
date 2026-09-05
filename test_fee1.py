import sys, os, asyncio, traceback
from datetime import datetime
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from badge_common import add_paths, find_badge
add_paths()
OUT = "/tmp/fee1_test.txt"
def w(s):
    print(s, flush=True)
    with open(OUT, "a", encoding="utf-8") as f: f.write(s + "\n")
open(OUT, "w").close()

ADDR = os.environ.get("BADGE_ADDR")  # не задан -> автопоиск по сервису FEE0
CHAR = "0000fee1-0000-1000-8000-00805f9b34fb"
CHUNK = 16
TARGET_MODE = 0          # гипотеза: первый байт чтения = номер режима
TARGET_SPEED = 5

def build(messages, speed, mode, brightness=100):
    from lednamebadge import LedNameBadge, SimpleTextAndIcons
    from array import array
    creator = SimpleTextAndIcons()
    bitmaps = [creator.bitmap(m) for m in messages]
    lengths = [b[1] for b in bitmaps]
    n = len(bitmaps)
    sp = min(max(speed, 1), 8)
    h = LedNameBadge.header(lengths, [sp]*n, [0]*n, [0]*n, [0]*n, brightness, datetime.now())
    h[5] = 0x00
    for i in range(8):
        h[8 + i] = ((sp - 1) << 4) | (mode & 0x0F)
    buf = array('B'); buf.extend(h)
    for b in bitmaps: buf.extend(b[0])
    return bytes(buf)

try:
    from bleak import BleakClient, BleakScanner

    async def main():
        w("ищу LSLED...")
        dev = await find_badge(timeout=30.0, addr=ADDR)
        if dev is None:
            w("НЕ НАЙДЕН - нажми кнопку ещё раз")
            return
        notes = []
        def on_notify(_, data):
            notes.append(bytes(data))
            w("  NOTIFY <- %s" % bytes(data).hex(" "))
        async with BleakClient(dev, timeout=25.0) as c:
            w("ПОДКЛЮЧЕНО")
            base = await c.read_gatt_char(CHAR)
            w("")
            w("ШАГ 1. Чтение ДО записи      : %s" % base.hex(" "))
            w("       (последний отправленный режим в прошлой сессии был 4)")
            try:
                await c.start_notify(CHAR, on_notify)
                w("       notify включён")
            except Exception as e:
                w("       notify недоступен: %r" % e)

            payload = build(["MODE0"], TARGET_SPEED, TARGET_MODE)
            chunks = [payload[i:i+CHUNK] for i in range(0, len(payload), CHUNK)]
            w("")
            w("ШАГ 2. Пишу сообщение с режимом %d (%d пакетов)" % (TARGET_MODE, len(chunks)))
            sent = 0
            try:
                for ch in chunks:
                    await c.write_gatt_char(CHAR, ch, response=True)
                    sent += 1
                w("       отправлено %d/%d пакетов" % (sent, len(chunks)))
            except Exception as e:
                w("       обрыв на %d/%d: %r" % (sent, len(chunks), e))

            w("")
            w("ШАГ 3. Чтение ПОСЛЕ записи")
            for attempt in range(3):
                try:
                    await asyncio.sleep(0.5)
                    after = await c.read_gatt_char(CHAR)
                    w("       попытка %d: %s" % (attempt+1, after.hex(" ")))
                    w("")
                    if after[0] == TARGET_MODE:
                        w("  >>> ГИПОТЕЗА ПОДТВЕРЖДЕНА: первый байт = %d = режим" % after[0])
                    elif after == base:
                        w("  >>> НЕ ПОДТВЕРЖДЕНА: значение не изменилось (%s)" % after.hex(" "))
                    else:
                        w("  >>> ИЗМЕНИЛОСЬ, но не на режим: было %02x, стало %02x" % (base[0], after[0]))
                    break
                except Exception as e:
                    w("       попытка %d не удалась: %r" % (attempt+1, e))
            if notes:
                w("")
                w("NOTIFY получено пакетов: %d" % len(notes))
        w("=== ГОТОВО ===")
    asyncio.run(main())
except Exception:
    w("ОШИБКА:\n" + traceback.format_exc())
