#!/usr/bin/env python3
"""Демо потокового вывода на бейдж через next-gen профиль badgemagic.

Кадр пишется ПРЯМО В ФРЕЙМБУФЕР (stream_bitmap -> tmos_memcpy(fb, ...)),
флеш не трогается вовсе - в отличие от обычной загрузки через 'wang'.
Авторизация для next-gen профиля не требуется, гейт !authorized стоит
только в legacy_ble_rx().

Формат кадра: 44 слова по 16 бит, little-endian.
Одно слово = один столбец, бит 0 = ВЕРХНИЙ пиксель, используются биты 0..10.
"""
import os, sys, asyncio, math, random, time, traceback
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from badge_common import add_paths, find_badge, NG_WRITE
add_paths()

OUT = "/tmp/stream_demo.log"
def log(s):
    print(s, flush=True)
    with open(OUT, "a", encoding="utf-8") as f: f.write(s + "\n")
open(OUT, "w").close()

PANEL_COLS, ROWS = 44, 11
# Сцены рисуют ВСЕГДА полную ширину панели.
COLS = PANEL_COLS
# Ширина быстрой посылки: 1 + 2*FAST_COLS должно влезать в ATT-пакет (61 байт при MTU 64).
FAST_COLS = int(os.environ.get("BADGE_STREAM_COLS", "30"))
CMD_STREAM_SET = 0x02
CMD_STREAM_BM  = 0x03


def frame_bytes(cols, n=None):
    """n слов -> 2n байт little-endian."""
    n = COLS if n is None else n
    b = bytearray()
    for i in range(n):
        v = cols[i] & 0x07FF if i < len(cols) else 0
        b += bytes((v & 0xFF, (v >> 8) & 0xFF))
    return bytes(b)


def text_cols(text):
    """Отрисовать текст шрифтом 5x7 из led-name-badge-ls32 в столбцы."""
    from lednamebadge import SimpleTextAndIcons
    bm, n = SimpleTextAndIcons().bitmap(text)
    out = []
    for x in range(n * 8):
        blk, bit = x // 8, 7 - (x % 8)
        v = 0
        for y in range(ROWS):
            if (bm[blk * ROWS + y] >> bit) & 1:
                v |= 1 << y
        out.append(v)
    return out


def bar(h):
    """Столбик высотой h снизу."""
    h = max(0, min(ROWS, int(h)))
    return 0 if h == 0 else ((1 << h) - 1) << (ROWS - h)


# ---------------- сцены ----------------

def scene_clock(t, st):
    s = datetime.now().strftime("%H:%M:%S")
    c = text_cols(s)
    off = max(0, (len(c) - COLS) // 2)
    return c[off:off + COLS]


def scene_spectrum(t, st):
    nb = COLS // 2
    if "lv" not in st:
        st["lv"] = [0.0] * nb
        st["ph"] = [random.random() * 6.28 for _ in range(nb)]
    lv, ph = st["lv"], st["ph"]
    for i in range(nb):
        # низкие частоты выше и медленнее - похоже на реальный спектр
        target = (math.sin(t * (1.1 + i * 0.13) + ph[i]) * 0.5 + 0.5)
        target *= (1.0 - i / 30.0) * ROWS
        if random.random() < 0.25:
            target += random.random() * 3
        lv[i] = max(target, lv[i] - 0.55)          # медленный спад
    cols = []
    for i in range(nb):
        cols += [bar(lv[i])] * 2                    # каждый столбик шириной 2
    return cols


def scene_wave(t, st):
    cols = []
    for x in range(COLS):
        y = 5 + 4.6 * math.sin(x * 0.28 + t * 4.5) * math.sin(t * 1.3)
        v = 1 << max(0, min(ROWS - 1, int(round(y))))
        v |= 1 << max(0, min(ROWS - 1, int(round(y)) - 1)) if int(y) % 3 else 0
        cols.append(v)
    return cols


def scene_scroll(t, st):
    if "c" not in st:
        st["c"] = text_cols("  NICKO * CH582M * BADGEMAGIC * ")
    c = st["c"]
    off = int(t * 26) % len(c)
    return [c[(off + i) % len(c)] for i in range(COLS)]


def scene_rain(t, st):
    if "d" not in st:
        st["d"] = [[random.uniform(-ROWS, 0), random.uniform(3, 11)] for _ in range(COLS)]
        st["last"] = t
    dt = max(0.0, t - st["last"]); st["last"] = t
    cols = []
    for x in range(COLS):
        d = st["d"][x]
        d[0] += d[1] * dt
        if d[0] - 4 > ROWS:
            d[0] = random.uniform(-6, 0); d[1] = random.uniform(3, 11)
        v = 0
        for k in range(4):                      # хвост из 4 пикселей
            y = int(d[0]) - k
            if 0 <= y < ROWS and (k == 0 or random.random() > 0.28):
                v |= 1 << y
        cols.append(v)
    return cols


def scene_heart(t, st):
    H = ["0001100011000",
         "0111110111110",
         "1111111111111",
         "1111111111111",
         "1111111111111",
         "0111111111110",
         "0011111111100",
         "0001111111000",
         "0000111110000",
         "0000011100000",
         "0000001000000"]
    beat = 0.55 + 0.45 * abs(math.sin(t * 2.6))
    w = len(H[0])
    cols = [0] * COLS
    x0 = (COLS - w) // 2
    for x in range(w):
        v = 0
        for y in range(ROWS):
            if H[y][x] == '1':
                v |= 1 << y
        cols[x0 + x] = v
    if beat < 0.75:                             # «сжатие» — гасим края
        for i in list(range(x0, x0 + 2)) + list(range(x0 + w - 2, x0 + w)):
            cols[i] = 0
    return cols


SCENES = [
    ("ЧАСЫ с секундами", scene_clock,    6),
    ("СПЕКТР",           scene_spectrum, 8),
    ("ОСЦИЛЛОГРАФ",      scene_wave,     6),
    ("БЕГУЩАЯ СТРОКА",   scene_scroll,   9),
    ("МАТРИЦА",          scene_rain,     7),
    ("СЕРДЦЕ",           scene_heart,    5),
]




async def bench_one(c, ncols, secs, no_resp):
    import time as _t
    cols = [bar((i % 11) + 1) for i in range(ncols)]
    payload = bytes((CMD_STREAM_BM,)) + frame_bytes(cols, ncols)
    n, t0 = 0, _t.time()
    while _t.time() - t0 < secs:
        await c.write_gatt_char(NG_WRITE, payload, response=not no_resp)
        n += 1
    return n / (_t.time() - t0)


async def connect():
    """Подключиться к бейджу, вернуть (client, no_resp) или (None, None)."""
    from bleak import BleakClient
    dev = await find_badge(timeout=30.0)
    if dev is None:
        return None, None
    c = BleakClient(dev, timeout=25.0)
    await c.connect()
    ch = None
    for srv in c.services:
        for x in srv.characteristics:
            if x.uuid == NG_WRITE:
                ch = x
    if ch is None:
        await c.disconnect()
        return None, None
    no_resp = "write-without-response" in ch.properties
    await c.write_gatt_char(NG_WRITE, bytes((CMD_STREAM_SET, 0x00)), response=True)
    return c, no_resp


async def main():
    # fast30 - только левые 30 столбцов, быстро и стабильно
    # full44 - вся панель, но длинной записью: медленно и рвётся
    # hybrid - быстрые кадры в 30 столбцов + полный кадр раз в FULL_EVERY,
    #          чтобы правые 14 столбцов тоже обновлялись
    mode = os.environ.get("BADGE_STREAM_MODE", "hybrid")
    FULL_EVERY = int(os.environ.get("BADGE_FULL_EVERY", "6"))
    log("режим: %s, быстрый кадр %d столбцов, панель %d%s"
        % (mode, FAST_COLS, PANEL_COLS,
           ", полный кадр каждый %d-й" % FULL_EVERY if mode == "hybrid" else ""))

    c, no_resp = await connect()
    if c is None:
        log("НЕ НАЙДЕН или нет профиля F057. Включи BT-PAIRING и повтори.")
        return
    log("подключено, MTU=%s, запись %s" %
        (getattr(c, "mtu_size", "?"), "без подтверждения" if no_resp else "с подтверждением"))

    await c.write_gatt_char(NG_WRITE,
            bytes((CMD_STREAM_BM,)) + frame_bytes([0] * PANEL_COLS, PANEL_COLS),
            response=True)
    log("панель очищена целиком\n")

    if os.environ.get("BADGE_BENCH"):
        log("-> ЗАМЕР")
        log("   30 столбцов: %.1f fps" % await bench_one(c, 30, 4.0, no_resp))
        log("   44 столбца:  %.1f fps" % await bench_one(c, PANEL_COLS, 4.0, no_resp))
        log("")

    total, drops, t0 = 0, 0, time.time()
    for name, fn, dur in SCENES:
        st, n, s0 = {}, 0, time.time()
        log("-> %s (%d с)" % (name, dur))
        while time.time() - s0 < dur:
            t = time.time() - s0
            cols = fn(t, st)
            full = (mode == "full44") or (mode == "hybrid" and n % FULL_EVERY == 0)
            ncols = PANEL_COLS if full else FAST_COLS
            payload = bytes((CMD_STREAM_BM,)) + frame_bytes(cols, ncols)
            try:
                await c.write_gatt_char(NG_WRITE, payload, response=not no_resp)
                n += 1
            except Exception as e:
                drops += 1
                log("   обрыв на %d кадре (%r) - переподключаюсь" % (n, e))
                try:
                    await c.disconnect()
                except Exception:
                    pass
                await asyncio.sleep(3)
                c, no_resp = await connect()
                if c is None:
                    log("   переподключиться не удалось, останавливаюсь")
                    return
                log("   соединение восстановлено")
        el = max(0.001, time.time() - s0)
        total += n
        log("   %d кадров за %.1f с = %.1f fps" % (n, el, n / el))

    try:
        await c.write_gatt_char(NG_WRITE, bytes((CMD_STREAM_SET, 0x01)), response=True)
        log("\nрежим стриминга выключен")
        await c.disconnect()
    except Exception:
        log("\n(связь потеряна на выходе)")
    el = time.time() - t0
    log("ИТОГО: %d кадров за %.1f с, средний %.1f fps, обрывов: %d"
        % (total, el, total / max(0.001, el), drops))


try:
    asyncio.run(main())
except Exception:
    log("ОШИБКА:\n" + traceback.format_exc())
