"""Протокол LED-бейджа CH582M. Только упаковка байтов, без ввода-вывода.

Всё здесь проверено на живом устройстве 2026-09-05. Ссылки на исходники
прошивки: fossasia/badgemagic-firmware @ b2137f08.
"""
from datetime import datetime

# --- GATT -------------------------------------------------------------------
SVC_LEGACY = "0000fee0-0000-1000-8000-00805f9b34fb"
CHR_LEGACY = "0000fee1-0000-1000-8000-00805f9b34fb"   # write, 16 байт РОВНО
SVC_NG     = "0000f055-0000-1000-8000-00805f9b34fb"
CHR_NG_TX  = "0000f057-0000-1000-8000-00805f9b34fb"   # команды
CHR_NG_RX  = "0000f056-0000-1000-8000-00805f9b34fb"   # notify, код ответа

ADV_NAMES = ("LSLED", "LED Badge Magic")

# --- панель -----------------------------------------------------------------
COLS = 44
ROWS = 11
LEGACY_CHUNK = 16          # legacy_ble_rx отвергает пакет иной длины
HID_REPORT = 64
SLOTS = 8

# --- next-gen команды (src/ngctrl.c) ---------------------------------------
NG_NEXT_PACKET   = 0x00
NG_POWER         = 0x01
NG_STREAM_SET    = 0x02
NG_STREAM_BITMAP = 0x03
NG_BLE           = 0x04
NG_SPLASH        = 0x05
NG_SAVE_CFG      = 0x06
NG_FALLBACK_CFG  = 0x07
NG_MISC          = 0x08

BRIGHTNESS_LEVELS = 4      # 0 тусклее всего, 3 ярче всего

MODES = {
    "scroll-left": 0, "scroll-right": 1, "scroll-up": 2, "scroll-down": 3,
    "fixed": 4, "animation": 5, "snowflake": 6, "picture": 7, "laser": 8,
}
# Режимы 9 и 10 из вендорского приложения на CH582M дают ПУСТОЙ экран.


# --- next-gen: готовые пакеты ----------------------------------------------
def cmd_stream_enter():        return bytes((NG_STREAM_SET, 0x00))
def cmd_stream_leave():        return bytes((NG_STREAM_SET, 0x01))
def cmd_save_cfg():            return bytes((NG_SAVE_CFG,))
def cmd_fallback_cfg():        return bytes((NG_FALLBACK_CFG,))


def cmd_brightness(level):
    """0..3. Прерывание развёртки гасит светодиоды при state > level."""
    level = max(0, min(BRIGHTNESS_LEVELS - 1, int(level)))
    return bytes((NG_MISC, 0x01, level))


def cmd_splash_speed(ms):
    ms = max(10, int(ms))
    return bytes((NG_MISC, 0x00, ms & 0xFF, (ms >> 8) & 0xFF))


def cmd_reset_after_rx(enabled):
    """ВНИМАНИЕ: BadgeBLE.md описывает это наоборот. По коду ngctrl.c
    cfg_reset_rx() делает badge_cfg.reset_rx = !!state[0], то есть
    [01 01 01] ВКЛЮЧАЕТ перезагрузку после приёма. Апстрим: issue #191."""
    return bytes((NG_POWER, 0x01, 0x01 if enabled else 0x00))


def cmd_poweroff():            return bytes((NG_POWER, 0x00))
def cmd_reset():               return bytes((NG_POWER, 0x02))


def cmd_ble_always_on(enabled):
    return bytes((NG_BLE, 0x00, 0x01 if enabled else 0x00))


def cmd_ble_name(name):
    b = name.encode("utf-8")[:20]
    return bytes((NG_BLE, 0x01)) + b


def cmd_stream_frame(columns, ncols=None):
    """Кадр: слова по 16 бит little-endian, бит 0 = ВЕРХНИЙ пиксель.

    stream_bitmap копирует min(COLS*2, len) байт всегда от fb[0], поэтому
    частичный кадр обновляет только левую часть панели.
    """
    n = COLS if ncols is None else max(1, min(COLS, int(ncols)))
    out = bytearray((NG_STREAM_BITMAP,))
    for i in range(n):
        v = (columns[i] if i < len(columns) else 0) & 0x07FF
        out += bytes((v & 0xFF, v >> 8))
    return bytes(out)


def max_stream_cols(mtu):
    """Сколько столбцов влезает в ОДНУ ATT-запись при данном MTU.

    Полезная нагрузка записи = mtu - 3, минус байт команды, по 2 байта на столбец.
    При MTU 64 это 30 столбцов; при 128 - все 44.
    Кадр больше уходит длинной записью: втрое медленнее, плюс накопление
    в пуле BLE_BUFF_NUM вплоть до обрыва связи. Апстрим: issue #192, PR #194.
    """
    return max(1, min(COLS, ((max(23, int(mtu)) - 3) - 1) // 2))


# --- legacy: заголовок 'wang' ----------------------------------------------
_BRIGHT_BYTE = ((25, 0x30), (50, 0x20), (75, 0x10))   # ИНДЕКС уровня, не маска


def legacy_header(lengths, speeds, modes, blinks=0, ants=0,
                  brightness=100, when=None):
    """64 байта. lengths - в БАЙТ-СТОЛБЦАХ (по 8 px), big endian.

    Поле timestamp не декоративное: legacyctrl.c вызывает из него RTC_InitTime,
    то есть загрузка сообщения ЗАОДНО ВЫСТАВЛЯЕТ ЧАСЫ. Это единственный путь
    к RTC - в legacy_usb_rx() такого вызова нет.
    """
    def seq(v, lo, hi):
        v = list(v) if isinstance(v, (list, tuple)) else [v]
        v = [max(lo, min(hi, int(x))) for x in v]
        return (v + [v[-1]] * SLOTS)[:SLOTS]

    speeds = seq(speeds, 1, 8)
    modes = seq(modes, 0, 8)
    blinks = seq(blinks, 0, 1)
    ants = seq(ants, 0, 1)
    lengths = list(lengths)[:SLOTS]

    h = bytearray(64)
    h[0:4] = b"wang"
    h[5] = 0x00
    for thr, val in _BRIGHT_BYTE:
        if brightness <= thr:
            h[5] = val
            break
    for i in range(SLOTS):
        h[6] |= blinks[i] << i
        h[7] |= ants[i] << i
        h[8 + i] = ((speeds[i] - 1) << 4) | modes[i]
    for i, n in enumerate(lengths):
        h[16 + 2 * i] = (n >> 8) & 0xFF
        h[17 + 2 * i] = n & 0xFF
    t = when or datetime.now()
    h[38:44] = bytes((t.year % 100, t.month, t.day, t.hour, t.minute, t.second))
    return bytes(h)


def pack_bitmap(columns):
    """Столбцы (бит 0 = верх) -> legacy: по ROWS байт на байт-столбец, MSB слева."""
    nblocks = (len(columns) + 7) // 8
    data = bytearray()
    for b in range(nblocks):
        for y in range(ROWS):
            byte = 0
            for n in range(8):
                x = b * 8 + n
                if x < len(columns) and (columns[x] >> y) & 1:
                    byte |= 1 << (7 - n)
            data.append(byte)
    return bytes(data), nblocks


def legacy_payload(messages, speeds=5, modes=0, brightness=100, when=None):
    """messages - список списков столбцов. Возвращает готовый буфер.

    Дополняется нулями до кратности 16: legacy_ble_rx() отвергает пакет иной
    длины, а завершение передачи (и установка RTC) наступает на ПОСЛЕДНЕМ пакете.
    """
    blobs, lens = [], []
    for cols in messages[:SLOTS]:
        blob, n = pack_bitmap(cols)
        blobs.append(blob)
        lens.append(n)
    body = b"".join(blobs)
    buf = legacy_header(lens, speeds, modes, brightness=brightness, when=when) + body
    if len(buf) % LEGACY_CHUNK:
        buf += b"\x00" * (LEGACY_CHUNK - len(buf) % LEGACY_CHUNK)
    return buf


def pin_packet(pin):
    """4 ASCII-цифры в собственном 16-байтном пакете, ДО данных.

    Код действует до разрыва связи (legacy_reset_auth в peripheral.c),
    поэтому PIN и данные обязаны уйти в одном соединении.
    """
    p = str(pin).strip()
    if len(p) != 4 or not p.isdigit():
        raise ValueError("PIN должен быть ровно 4 цифры, получено: %r" % pin)
    return p.encode("ascii") + b"\x00" * (LEGACY_CHUNK - 4)


def frames_ram_estimate(nframes):
    """Оценка пика RAM при распаковке. 120 кадров (~17.8 КБ) роняют загрузку."""
    cols = nframes * COLS
    blocks = (cols + 7) // 8
    return blocks * ROWS + blocks * 8 * 2


# --- события кнопок --------------------------------------------------------
# Прошивка форка (ветка feat-button-events) во время стриминга шлёт по F056
# два байта: 0xE0 <код>. KEY1 = 0x01, KEY2 = 0x02, старший бит = отпускание.
# 0xE0 0x00 — бейдж вышел из стриминга сам (долгий KEY2), хост сбрасывает
# состояние клавиш. Код возврата команды всегда однобайтовый — не спутать.
EV_PREFIX = 0xE0
EV_RELEASE = 0x80
EV_EXIT = 0x00
KEY_CODES = {0x01: "KEY1", 0x02: "KEY2"}


def parse_notify(data):
    """Уведомление с F056 -> словарь с полем type: status | key | exit | unknown."""
    b = bytes(data)
    if len(b) == 2 and b[0] == EV_PREFIX:
        code = b[1]
        if code == EV_EXIT:
            return {"type": "exit"}
        key = KEY_CODES.get(code & (0xFF ^ EV_RELEASE))
        if key:
            return {"type": "key", "key": key,
                    "down": not (code & EV_RELEASE), "code": code}
        return {"type": "unknown", "raw": b.hex()}
    if len(b) == 1:
        return {"type": "status", "code": b[0]}
    return {"type": "unknown", "raw": b.hex()}
