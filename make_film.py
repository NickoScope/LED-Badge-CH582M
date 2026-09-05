#!/usr/bin/env python3
"""Собрать и залить ХРАНИМУЮ анимацию — играет на бейдже без компьютера.

48 кадров, четыре движения по 12. Режим 5 (animation): прошивка листает
битмап по 44 px за кадр, число кадров вычисляется из ширины.

ПРЕДЕЛ: 120 кадров делают бейдж незагружаемым (RAM при распаковке).
48 кадров это ~3 КБ - вдвое ниже опасной границы. Выше не поднимать.
"""
import os, sys, asyncio, math, argparse, traceback
from datetime import datetime
from array import array

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from badge_common import add_paths, find_badge, CHAR_FEE1, CHUNK
add_paths()

OUT = "/tmp/make_film.log"
def log(s):
    print(s, flush=True)
    with open(OUT, "a", encoding="utf-8") as f: f.write(s + "\n")
open(OUT, "w").close()

W, ROWS = 44, 11
SEG = 12                      # кадров на движение
FRAMES = SEG * 4              # 48
SAFETY_BYTES = 8000           # 48 кадров ~3 КБ; страховка от опечатки


def col(bits):
    v = 0
    for y in bits:
        if 0 <= y < ROWS:
            v |= 1 << y
    return v


def f_heart(k):
    """Пульсирующее сердце."""
    H = ["00011000110000", "00111110111110", "01111111111111", "01111111111111",
         "01111111111111", "00111111111110", "00011111111100", "00001111111000",
         "00000111110000", "00000011100000", "00000001000000"]
    beat = 0.5 + 0.5 * math.sin(k / SEG * 2 * math.pi)
    keep = 4 + int(beat * (len(H[0]) - 4))
    cols = [0] * W
    x0 = (W - len(H[0])) // 2
    for x in range(len(H[0])):
        if abs(x - len(H[0]) / 2) > keep / 2:
            continue
        v = 0
        for y in range(ROWS):
            if H[y][x] == '1':
                v |= 1 << y
        cols[x0 + x] = v
    return cols


def f_wave(k):
    """Бегущая синусоида, полный период за сегмент."""
    ph = k / SEG * 2 * math.pi
    return [col([round(5 + 4.4 * math.sin(x * 0.30 + ph))]) for x in range(W)]


def f_rings(k):
    """Волны, расходящиеся от центра."""
    cols = [0] * W
    c = W / 2.0
    for x in range(W):
        d = abs(x - c)
        h = 5.0 * abs(math.sin(d * 0.45 - k / SEG * 2 * math.pi))
        n = max(1, int(h))
        cols[x] = col(range(5 - n // 2, 5 + n // 2 + 1))
    return cols


def f_sparks(k):
    """Летящие искры с хвостами, зацикленные по сегменту."""
    cols = [0] * W
    for i in range(9):
        speed = 1.6 + (i % 4) * 0.7
        x = int((i * 5 + k * speed)) % W
        y = (i * 3 + i * i) % ROWS
        for t in range(3):
            xx = (x - t) % W
            cols[xx] |= 1 << y
        if i % 3 == 0:
            cols[x] |= 1 << ((y + 4) % ROWS)
    return cols


def build_film():
    cols = []
    for k in range(SEG): cols += f_heart(k)
    for k in range(SEG): cols += f_wave(k)
    for k in range(SEG): cols += f_rings(k)
    for k in range(SEG): cols += f_sparks(k)
    return cols


def pack_legacy(cols):
    nblocks = (len(cols) + 7) // 8
    data = bytearray()
    for b in range(nblocks):
        for y in range(ROWS):
            byte = 0
            for n in range(8):
                x = b * 8 + n
                if x < len(cols) and (cols[x] >> y) & 1:
                    byte |= 1 << (7 - n)
            data.append(byte)
    return bytes(data), nblocks


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pin', required=True)
    ap.add_argument('--speed', type=int, default=6)
    a = ap.parse_args()

    from lednamebadge import LedNameBadge
    cols = build_film()
    bmp, nblocks = pack_legacy(cols)
    h = LedNameBadge.header([nblocks], [a.speed], [0], [0], [0], 100, datetime.now())
    h[5] = 0x00
    for i in range(8):
        h[8 + i] = ((a.speed - 1) << 4) | 0x05      # режим 5 - animation
    buf = array('B'); buf.extend(h); buf.extend(bmp)
    payload = bytes(buf)
    if len(payload) % CHUNK:
        payload += b'\x00' * (CHUNK - len(payload) % CHUNK)

    log("кадров:  %d  (4 движения по %d)" % (FRAMES, SEG))
    log("ширина:  %d px, байт-столбцов %d" % (len(cols), nblocks))
    log("размер:  %d байт, пакетов %d" % (len(payload), len(payload) // CHUNK))
    if len(payload) > SAFETY_BYTES:
        log("СТОП: %d > %d — слишком много" % (len(payload), SAFETY_BYTES)); return
    log("")

    from bleak import BleakClient
    dev = await find_badge(timeout=30.0)
    if dev is None:
        log("НЕ НАЙДЕН — включи BT-PAIRING"); return
    async with BleakClient(dev, timeout=25.0) as c:
        log("подключено, MTU=%s" % getattr(c, "mtu_size", "?"))
        await c.write_gatt_char(CHAR_FEE1, a.pin.strip().encode() + b'\x00' * (CHUNK - 4), response=True)
        log("PIN отправлен")
        chunks = [payload[i:i+CHUNK] for i in range(0, len(payload), CHUNK)]
        sent = 0
        try:
            for ch in chunks:
                await c.write_gatt_char(CHAR_FEE1, ch, response=True)
                sent += 1
                if sent % 50 == 0:
                    log("  ... %d / %d" % (sent, len(chunks)))
            log("ОТПРАВЛЕНО: %d пакетов" % sent)
        except Exception as e:
            log("обрыв на %d/%d: %r" % (sent + 1, len(chunks), e))
    log("=== ГОТОВО — меню -> ANIMATION ===")

try:
    asyncio.run(main())
except Exception:
    log("ОШИБКА:\n" + traceback.format_exc())
