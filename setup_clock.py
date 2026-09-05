import sys, os, asyncio, traceback
from datetime import datetime
from array import array
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from badge_common import add_paths, find_badge
add_paths()
OUT = "/tmp/setup_clock.log"
def w(s):
    print(s, flush=True)
    with open(OUT, "a", encoding="utf-8") as f: f.write(s + "\n")
open(OUT, "w").close()

ADDR = os.environ.get("BADGE_ADDR")  # не задан -> автопоиск по сервису FEE0
LEGACY = "0000fee1-0000-1000-8000-00805f9b34fb"
NG_WRITE = "0000f057-0000-1000-8000-00805f9b34fb"
NG_NOTIFY = "0000f056-0000-1000-8000-00805f9b34fb"
CHUNK = 16

# Байты команд взяты ИЗ КОДА src/ngctrl.c, а не из BadgeBLE.md:
#   ng_parse: val[0] = команда -> power_setting(val+1)
#   power_setting: val[0] = 0x01 -> cfg_reset_rx(&val[1])
#   cfg_reset_rx: badge_cfg.reset_rx = !!state[0]
# Значит [0x01,0x01,0x00] => reset_rx = 0 => ребута после приёма НЕТ.
# В BadgeBLE.md это описано наоборот - документация расходится с кодом.
CMD_NO_RESET_AFTER_RX = bytes([0x01, 0x01, 0x00])
CMD_BLE_ALWAYS_ON     = bytes([0x04, 0x00, 0x01])   # ble_setting -> cfg_ble_alwayon(1)
CMD_SAVE_CFG          = bytes([0x06])

def build_message(text, speed=5, mode=0, brightness=100, when=None):
    from lednamebadge import LedNameBadge, SimpleTextAndIcons
    creator = SimpleTextAndIcons()
    bitmaps = [creator.bitmap(text)]
    lengths = [b[1] for b in bitmaps]
    when = when or datetime.now()
    sp = min(max(speed, 1), 8)
    h = LedNameBadge.header(lengths, [sp], [0], [0], [0], brightness, when)
    h[5] = 0x00
    for i in range(8):
        h[8 + i] = ((sp - 1) << 4) | (mode & 0x0F)
    buf = array('B'); buf.extend(h)
    for b in bitmaps: buf.extend(b[0])
    return bytes(buf), when

try:
    from bleak import BleakClient, BleakScanner
    async def main():
        w("ищу LED Badge Magic...")
        dev = await find_badge(timeout=30.0, addr=ADDR)
        if dev is None:
            w("НЕ НАЙДЕН - включи BT-PAIRING в меню")
            return
        notes = []
        def on_note(_, d):
            notes.append(bytes(d))
            w("   ответ <- %s" % bytes(d).hex(" "))
        async with BleakClient(dev, timeout=25.0) as c:
            w("подключено")
            chars = {ch.uuid for s in c.services for ch in s.characteristics}
            has_ng = NG_WRITE in chars
            w("next-gen профиль F057: %s" % ("есть" if has_ng else "НЕТ"))

            if has_ng:
                try:
                    await c.start_notify(NG_NOTIFY, on_note)
                except Exception as e:
                    w("   notify недоступен: %r" % e)
                w("")
                w("ШАГ 1. Отключаю ребут после приёма  [01 01 00]")
                await c.write_gatt_char(NG_WRITE, CMD_NO_RESET_AFTER_RX, response=True)
                await asyncio.sleep(0.4)
                w("ШАГ 2. Включаю always-on BLE        [04 00 01]")
                await c.write_gatt_char(NG_WRITE, CMD_BLE_ALWAYS_ON, response=True)
                await asyncio.sleep(0.4)
                w("ШАГ 3. Сохраняю конфиг во флеш      [06]")
                await c.write_gatt_char(NG_WRITE, CMD_SAVE_CFG, response=True)
                await asyncio.sleep(0.8)

            payload, when = build_message("NICKO")
            w("")
            w("ШАГ 4. Отправляю сообщение с меткой времени %s" % when.strftime("%Y-%m-%d %H:%M:%S"))
            chunks = [payload[i:i+CHUNK] for i in range(0, len(payload), CHUNK)]
            sent = 0
            try:
                for ch in chunks:
                    await c.write_gatt_char(LEGACY, ch, response=True)
                    sent += 1
                w("   отправлено %d/%d пакетов - RTC выставлен" % (sent, len(chunks)))
            except Exception as e:
                if sent >= len(chunks) - 1:
                    w("   разрыв после %d/%d - вероятно принято" % (sent, len(chunks)))
                else:
                    w("   ОБРЫВ на %d/%d: %r" % (sent, len(chunks), e))
        w("=== ГОТОВО ===")
    asyncio.run(main())
except Exception:
    w("ОШИБКА:\n" + traceback.format_exc())
