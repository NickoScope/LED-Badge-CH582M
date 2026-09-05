#!/usr/bin/env python3
"""Измеритель максимальной длины сохранённой анимации.

Генерирует анимацию из N кадров, где КАЖДЫЙ кадр показывает свой номер.
Достаточно посмотреть, до какого числа бейдж досчитает - это и есть предел.

Ограничения, которые мы ищем:
  * EEPROM: data_flatSave пишет с нуля, 32 КБ, БЕЗ проверки границ;
    в конце той же памяти лежат badge_cfg и рекорд игры.
  * RAM: load_bmlist держит все слоты распакованными, 2 байта на столбец.
"""
import os, sys, asyncio, argparse, traceback
from datetime import datetime
from array import array

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from badge_common import add_paths, find_badge, CHAR_FEE1, CHUNK
add_paths()

OUT = "/tmp/anim_test.log"
def log(s):
    print(s, flush=True)
    with open(OUT, "a", encoding="utf-8") as f: f.write(s + "\n")
open(OUT, "w").close()

PANEL, ROWS = 44, 11
SAFETY_BYTES = 16000     # дальше рискуем затереть badge_cfg (нет проверки границ)


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


def build_columns(frames):
    """Каждый кадр - номер по центру, плюс рамка сверху и снизу для видимости границ."""
    cols = []
    for i in range(1, frames + 1):
        f = [0] * PANEL
        d = text_cols(str(i))
        x0 = max(0, (PANEL - len(d)) // 2)
        for k, v in enumerate(d):
            if x0 + k < PANEL:
                f[x0 + k] = v
        # маркер: первый и последний столбец кадра подсвечены целиком
        f[0] = 0x07FF
        f[PANEL - 1] = 0x07FF
        cols += f
    return cols


def pack_legacy(cols):
    """Столбцы (бит 0 = верх) -> формат legacy: по 11 байт на байт-столбец, MSB слева."""
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


def build_payload(frames, speed, mode=5):
    from lednamebadge import LedNameBadge
    cols = build_columns(frames)
    bmp, nblocks = pack_legacy(cols)
    h = LedNameBadge.header([nblocks], [speed], [0], [0], [0], 100, datetime.now())
    h[5] = 0x00
    for i in range(8):
        h[8 + i] = ((speed - 1) << 4) | (mode & 0x0F)
    buf = array('B'); buf.extend(h); buf.extend(bmp)
    p = bytes(buf)
    if len(p) % CHUNK:
        p += b'\x00' * (CHUNK - len(p) % CHUNK)
    return p, nblocks


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--frames', type=int, default=120)
    ap.add_argument('--pin', required=True)
    ap.add_argument('--speed', type=int, default=4)
    a = ap.parse_args()

    payload, nblocks = build_payload(a.frames, a.speed)
    log("кадров:        %d" % a.frames)
    log("байт-столбцов: %d  (ширина %d px)" % (nblocks, nblocks * 8))
    log("размер:        %d байт  (заголовок 64 + данные %d)" % (len(payload), len(payload) - 64))
    log("пакетов по 16: %d" % (len(payload) // CHUNK))
    if len(payload) > SAFETY_BYTES:
        log("СТОП: %d > %d байт - риск затереть badge_cfg, в data_flatSave нет проверки границ"
            % (len(payload), SAFETY_BYTES))
        return
    log("оценка времени: ~%.0f с" % (len(payload) / CHUNK / 12))
    log("")

    from bleak import BleakClient
    dev = await find_badge(timeout=30.0)
    if dev is None:
        log("НЕ НАЙДЕН"); return
    async with BleakClient(dev, timeout=25.0) as c:
        log("подключено, MTU=%s" % getattr(c, "mtu_size", "?"))
        pin = a.pin.strip()
        await c.write_gatt_char(CHAR_FEE1, pin.encode() + b'\x00' * (CHUNK - 4), response=True)
        log("PIN %s отправлен" % pin)
        chunks = [payload[i:i + CHUNK] for i in range(0, len(payload), CHUNK)]
        sent = 0
        try:
            for i, ch in enumerate(chunks):
                await c.write_gatt_char(CHAR_FEE1, ch, response=True)
                sent += 1
                if sent % 100 == 0:
                    log("  ... %d / %d пакетов" % (sent, len(chunks)))
            log("ОТПРАВЛЕНО ПОЛНОСТЬЮ: %d пакетов" % sent)
        except Exception as e:
            log("ОБРЫВ на пакете %d/%d: %r" % (sent + 1, len(chunks), e))
            if sent >= len(chunks) - 1:
                log("(последний пакет - вероятно принято)")
    log("=== ГОТОВО ===")
    log("Смотри на бейдж: меню -> ANIMATION. До какого числа считает?")

try:
    asyncio.run(main())
except Exception:
    log("ОШИБКА:\n" + traceback.format_exc())
