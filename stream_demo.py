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
CMD_MISC       = 0x08      # misc
MISC_BRIGHT    = 0x01      # cfg_led_brightness, уровень 0..3
BRI_MAX        = 3         # BRIGHTNESS_LEVELS = 4, 0 - самый тусклый


def frame_bytes(cols, n=None):
    """n слов -> 2n байт little-endian."""
    n = COLS if n is None else n
    b = bytearray()
    for i in range(n):
        v = cols[i] & 0x07FF if i < len(cols) else 0
        b += bytes((v & 0xFF, (v >> 8) & 0xFF))
    return bytes(b)


def text_cols(t):
    """Отрисовать текст в столбцы, ПОСИМВОЛЬНО.

    Через SimpleTextAndIcons.bitmap() нельзя: двоеточие там - синтаксис вставки
    иконок, и "23:18:35" превращается в "2", chr(18), "5" -> IndexError.
    Падение зависит от минуты, поэтому часы ломались через раз.
    bitmap_char() обходит этот разбор.
    """
    from lednamebadge import SimpleTextAndIcons
    creator = SimpleTextAndIcons()
    out = []
    for ch in t:
        b, n = creator.bitmap_char(ch)
        for blk in range(n):
            for x in range(8):
                v = 0
                for y in range(ROWS):
                    if (b[blk * ROWS + y] >> (7 - x)) & 1:
                        v |= 1 << y
                out.append(v)
    return out


def text_cols_tight(t, gap=1):
    """То же, но без пустых столбцов внутри глифов.

    bitmap_char отдаёт байт-столбец на символ, то есть 8 px, хотя шрифт 5x7.
    Для HH:MM:SS это 64 px при панели 44 - не влезает. Здесь каждый глиф
    обрезается по краям и склеивается через gap пустых столбцов.
    """
    out = []
    for ch in t:
        g = text_cols(ch)
        while g and g[0] == 0:
            g.pop(0)
        while g and g[-1] == 0:
            g.pop()
        if not g:                      # пробел
            g = [0, 0]
        if out:
            out += [0] * gap
        out += g
    return out


def bar(h):
    """Столбик высотой h снизу."""
    h = max(0, min(ROWS, int(h)))
    return 0 if h == 0 else ((1 << h) - 1) << (ROWS - h)


# ---------------- сцены ----------------

def scene_clock(t, st):
    """HH:MM по центру, секунды - растущей полосой по нижней строке.

    HH:MM:SS даже плотной отрисовкой это 52 столбца при панели 44,
    поэтому секунды показываем иначе.
    """
    now = datetime.now()
    c = text_cols_tight(now.strftime("%H:%M"))
    cols = [0] * COLS
    x0 = max(0, (COLS - len(c)) // 2)
    for i, v in enumerate(c):
        if x0 + i < COLS:
            cols[x0 + i] = v << 1        # сдвиг вверх, освобождаем нижнюю строку
    filled = int(now.second / 60.0 * COLS)
    for x in range(filled):
        cols[x] |= 1 << (ROWS - 1)       # нижняя строка
    return cols


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


def scene_brightness(t, st):
    """Показывает текущий уровень и просит его сменить через st['_bri'].

    Уровень меняется раз в 1.5 с по кругу 0 -> 1 -> 2 -> 3.
    Рисунок специально плотный: на заливке разница уровней видна лучше всего.
    """
    lvl = int(t / 1.5) % 4
    st["_bri"] = lvl
    cols = [0] * COLS
    # слева - шкала: столько столбцов-полос, каков уровень
    for i in range(lvl + 1):
        for x in range(i * 3, i * 3 + 2):
            if x < COLS:
                cols[x] = 0x07FF
    # справа - крупная цифра уровня
    d = text_cols(str(lvl))
    x0 = COLS - len(d) - 2
    for i, v in enumerate(d):
        if 0 <= x0 + i < COLS:
            cols[x0 + i] = v
    # середина - плотная сетка, на ней разница яркости заметнее
    for x in range(16, min(COLS - len(d) - 4, COLS)):
        cols[x] = 0x0555 if x % 2 else 0x02AA
    return cols


def heart_brightness(t, st):
    """Пульс яркости в такт биению сердца."""
    beat = abs(math.sin(t * 2.6))
    return int(round(beat * BRI_MAX))


def fade_in(t, st):
    """Плавное нарастание в первые 1.2 с сцены."""
    return min(BRI_MAX, int(t / 0.4))


# (имя, кадр, секунды, функция яркости или None)
SCENES = [
    ("ЯРКОСТЬ 0-3",      scene_brightness, 7, None),
    ("ЧАСЫ с секундами", scene_clock,      6, fade_in),
    ("СПЕКТР",           scene_spectrum,   8, None),
    ("ОСЦИЛЛОГРАФ",      scene_wave,       6, None),
    ("БЕГУЩАЯ СТРОКА",   scene_scroll,     9, None),
    ("МАТРИЦА",          scene_rain,       7, fade_in),
    ("СЕРДЦЕ",           scene_heart,      5, heart_brightness),
]




async def set_bri(c, lvl, cur):
    """Отправить уровень яркости, если он изменился. Возвращает новый текущий."""
    lvl = max(0, min(BRI_MAX, int(lvl)))
    if lvl == cur:
        return cur
    await c.write_gatt_char(NG_WRITE, bytes((CMD_MISC, MISC_BRIGHT, lvl)), response=True)
    return lvl


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
    mode = os.environ.get("BADGE_STREAM_MODE", "full44")
    target_fps = float(os.environ.get("BADGE_FPS", "30"))
    loop = os.environ.get("BADGE_LOOP", "1") != "0"   # по умолчанию крутим бесконечно
    FULL_EVERY = int(os.environ.get("BADGE_FULL_EVERY", "6"))
    log("режим: %s, быстрый кадр %d столбцов, панель %d%s, цель %.0f fps"
        % (mode, FAST_COLS, PANEL_COLS,
           ", полный кадр каждый %d-й" % FULL_EVERY if mode == "hybrid" else "",
           target_fps))

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
    cur_bri = await set_bri(c, BRI_MAX, None)
    cycle = 0
    while True:
      cycle += 1
      if loop:
        log("")
        log("========== КРУГ %d ==========" % cycle)
      for name, fn, dur, brifn in SCENES:
          st, n, s0 = {}, 0, time.time()
          log("-> %s (%d с)%s" % (name, dur, "  [яркость]" if brifn or name.startswith("ЯРКОСТЬ") else ""))
          while time.time() - s0 < dur:
              t = time.time() - s0
              cols = fn(t, st)
              want = st.pop("_bri", None)
              if want is None and brifn:
                  want = brifn(t, st)
              if want is not None:
                  try:
                      cur_bri = await set_bri(c, want, cur_bri)
                  except Exception:
                      pass
              full = (mode == "full44") or (mode == "hybrid" and n % FULL_EVERY == 0)
              ncols = PANEL_COLS if full else FAST_COLS
              payload = bytes((CMD_STREAM_BM,)) + frame_bytes(cols, ncols)
              try:
                  await c.write_gatt_char(NG_WRITE, payload, response=not no_resp)
                  n += 1
                  if no_resp:
                      # без подтверждения нет и торможения - задаём темп сами,
                      # иначе очередь стека забивается и поток встаёт
                      nxt = s0 + n / target_fps
                      delay = nxt - time.time()
                      if delay > 0:
                          await asyncio.sleep(delay)
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

      el = time.time() - t0
      log("--- круг %d закончен: всего %d кадров за %.0f с, средний %.1f fps, обрывов %d"
          % (cycle, total, el, total / max(0.001, el), drops))
      if not loop:
          break

    try:
        await set_bri(c, BRI_MAX, cur_bri)
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
