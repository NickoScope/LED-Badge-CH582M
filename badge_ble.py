#!/usr/bin/env python3
"""
badge_ble - отправка на LED-бейдж по Bluetooth LE, БЕЗ USB-кабеля.

Зачем: при подключённом USB штатная прошивка уходит в экран зарядки, и обойти его
можно только физической кнопкой (KEY - обычный GPIO, программно не нажимается).
По BLE кабель не нужен вообще - бейдж работает от батареи, зарядки нет,
экрана зарядки нет.

Устройство: имя LSLED, service 0000fee0, characteristic 0000fee1 (write).
Полезная нагрузка - тот же протокол 'wang', что и по USB HID.
"""
import sys, os, asyncio, argparse, traceback
from array import array
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from badge_common import add_paths, find_badge
add_paths()

OUT = "/tmp/badge_ble.log"
def log(s):
    print(s, flush=True)
    with open(OUT, "a", encoding="utf-8") as f:
        f.write(s + "\n")

ADDR = os.environ.get("BADGE_ADDR")  # не задан -> автопоиск по сервису FEE0
CHAR = "0000fee1-0000-1000-8000-00805f9b34fb"
CHUNK = 16


def fix_brightness(h, brightness):
    """Байт 5 - ИНДЕКС уровня яркости, не битовая маска.

    Панель имеет 4 уровня: 0x00=100%, 0x10=75%, 0x20=50%, 0x30=25%.
    lednamebadge.py шлёт 0x40 для 25% - это индекс 4 в таблице из 4 элементов,
    на CH582/CH583 портит изображение вместо затемнения.
    Источник: github.com/martinbogo/badge-studio PROTOCOL.md (реверс на CH582/CH583).
    """
    if brightness <= 25:
        h[5] = 0x30
    elif brightness <= 50:
        h[5] = 0x20
    elif brightness <= 75:
        h[5] = 0x10
    else:
        h[5] = 0x00
    return h


def build(messages, speed=5, mode=4, brightness=100, blink=0, ants=0, when=None):
    from lednamebadge import LedNameBadge, SimpleTextAndIcons
    creator = SimpleTextAndIcons()
    bitmaps = [creator.bitmap(m) for m in messages]
    lengths = [b[1] for b in bitmaps]
    n = len(bitmaps)
    when = when or datetime.now()
    sp = min(max(speed, 1), 8)
    h = LedNameBadge.header(lengths, [sp] * n, [0] * n, [blink] * n, [ants] * n, brightness, when)
    fix_brightness(h, brightness)
    for i in range(8):
        h[8 + i] = ((sp - 1) << 4) | (mode & 0x0F)
    buf = array('B')
    buf.extend(h)
    for b in bitmaps:
        buf.extend(b[0])
    return bytes(buf)


async def send_ble(payload, addr=ADDR, retries=3):
    from bleak import BleakClient
    for attempt in range(1, retries + 1):
        try:
            async with BleakClient(addr, timeout=25.0) as c:
                if not c.is_connected:
                    raise RuntimeError("не подключилось")
                total = len(payload)
                for i in range(0, total, CHUNK):
                    await c.write_gatt_char(CHAR, payload[i:i + CHUNK], response=True)
                log("  отправлено %d байт (%d пакетов по %d)"
                    % (total, (total + CHUNK - 1) // CHUNK, CHUNK))
                return True
        except Exception as e:
            log("  попытка %d/%d не удалась: %r" % (attempt, retries, e))
            await asyncio.sleep(2)
    return False


async def one_shot(args, when=None):
    """Один цикл: найти в эфире -> подключиться -> записать -> ожидать разрыв.

    Бейдж ПЕРЕЗАГРУЖАЕТСЯ после каждого принятого сообщения (сохраняет во флеш).
    По USB это видно как смена HID-пути, по BLE - как обрыв соединения и пауза
    в рекламе на 10-30 с. Поэтому:
      * постоянное соединение невозможно;
      * разрыв сразу после последнего пакета - ШТАТНОЕ событие, не ошибка;
      * поиск в эфире должен быть терпеливым (окно тишины после ребута).
    """
    from bleak import BleakClient
    dev = await find_badge(timeout=40.0, addr=ADDR)
    if dev is None:
        return None
    now = when or datetime.now()
    text = now.strftime(args.format) if args.clock else None
    msgs = ([text] * 8 if args.fill8 else [text]) if args.clock else \
           (args.message * 8 if (args.fill8 and len(args.message) == 1) else args.message)
    payload = build(msgs, args.speed, args.mode, args.brightness, args.blink, args.ants, now)
    # ОБЯЗАТЕЛЬНО: дополнить нулями до кратности 16.
    # legacy_ble_rx() отвергает любой пакет длиной != LEGACY_TRANSFER_WIDTH,
    # а завершение передачи (и RTC_InitTime) наступает на ПОСЛЕДНЕМ пакете.
    if len(payload) % CHUNK:
        payload = payload + b'\x00' * (CHUNK - len(payload) % CHUNK)
    chunks = [payload[i:i + CHUNK] for i in range(0, len(payload), CHUNK)]
    sent = 0
    try:
        async with BleakClient(dev, timeout=25.0) as c:
            if getattr(args, 'pin', None):
                pin = str(args.pin).strip()
                if len(pin) != 4 or not pin.isdigit():
                    log("  PIN должен быть ровно 4 цифры, получено: %r" % pin)
                    return None
                auth = pin.encode('ascii') + b'\x00' * (CHUNK - 4)
                await c.write_gatt_char(CHAR, auth, response=True)
                log("  PIN %s отправлен" % pin)
            for ch in chunks:
                await c.write_gatt_char(CHAR, ch, response=True)
                sent += 1
    except Exception as e:
        # разрыв на последнем пакете почти наверняка означает, что сообщение принято
        # и устройство ушло в ребут - считаем успехом
        # Раньше здесь разрыв на последнем пакете считался успехом - это скрывало
        # реальную ошибку (последний пакет был короче 16 байт и отвергался).
        log("  ОШИБКА на пакете %d/%d: %r" % (sent + 1, len(chunks), e))
        if sent < len(chunks):
            return None
    return text or "сообщение"


async def clock_loop(args):
    import time as _t
    fails = 0
    while True:
        now = _t.time()
        target = (now // args.interval + 1) * args.interval
        lead = 35.0          # запас на ребут бейджа и возобновление рекламы
        await asyncio.sleep(max(2.0, target - lead - now))
        res = await one_shot(args)
        if res:
            fails = 0
            log("%s -> OK" % res)
        else:
            fails += 1
            log("такт пропущен (не найден в эфире), подряд: %d" % fails)
            if fails >= 5:
                log("бейдж не отвечает 5 тактов подряд - возможно, уснул или разряжен")
                fails = 0


async def run(args):
    if args.clock:
        log("Часы по BLE: формат %s, интервал %d с, режим %d." % (args.format, args.interval, args.mode))
        log("Бейдж перезагружается после каждой записи - разрывы связи штатны.")
        await clock_loop(args)
    else:
        res = await one_shot(args)
        log("Результат: %s" % ("отправлено" if res else "НЕ ОТПРАВЛЕНО"))


def main():
    p = argparse.ArgumentParser(description='Отправка на LED-бейдж по BLE (без USB).')
    p.add_argument('-m', '--mode', type=int, default=4, help='режим 0..8 (по умолчанию 4)')
    p.add_argument('-s', '--speed', type=int, default=5)
    p.add_argument('-B', '--brightness', type=int, default=100)
    p.add_argument('-b', '--blink', type=int, default=0)
    p.add_argument('-a', '--ants', type=int, default=0)
    p.add_argument('--fill8', action='store_true', help='продублировать во все 8 слотов')
    p.add_argument('--clock', action='store_true', help='режим часов')
    p.add_argument('--format', default='%H:%M')
    p.add_argument('--interval', type=int, default=60)
    p.add_argument('--addr', default=ADDR,
                   help='BLE-адрес бейджа. По умолчанию автопоиск по сервису FEE0 '
                        '(можно задать переменной BADGE_ADDR).')
    p.add_argument('--pin', default=None,
                   help='4-значный код с экрана при включённой защите (режим BT-PAIRING). '
                        'Альтернатива - долгое нажатие KEY1 на бейдже (bypass).')
    p.add_argument('message', nargs='*')
    args = p.parse_args()
    globals()['ADDR'] = args.addr
    if not args.clock and not args.message:
        p.error('нужно сообщение или --clock')
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        log("Остановлено.")
    except Exception:
        log("ОШИБКА:\n" + traceback.format_exc())


if __name__ == '__main__':
    main()
