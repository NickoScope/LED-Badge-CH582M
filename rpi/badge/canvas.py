"""Кадр 44x11 и отрисовка.

Кадр — список из COLS целых. Бит 0 = ВЕРХНИЙ пиксель, значимы биты 0..ROWS-1.
Такой же порядок ждёт stream_bitmap, так что перекладывать ничего не нужно.

Текст рисуем сами через Pillow. Через SimpleTextAndIcons.bitmap() из
led-name-badge-ls32 нельзя: там двоеточие — синтаксис вставки иконок, и
"23:18:35" превращается в chr(18) с падением, причём в зависимости от минуты.
Апстрим: led-name-badge-ls32 issue #22.
"""
import os

from .proto import COLS, ROWS

_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
)

# Запасной шрифт 3x5 для цифр и двоеточия — чтобы часы рисовались даже
# без единого ttf в системе. Каждая строка — 3 пикселя, сверху вниз.
_TINY = {
    "0": ("111", "101", "101", "101", "111"), "1": ("010", "110", "010", "010", "111"),
    "2": ("111", "001", "111", "100", "111"), "3": ("111", "001", "111", "001", "111"),
    "4": ("101", "101", "111", "001", "001"), "5": ("111", "100", "111", "001", "111"),
    "6": ("111", "100", "111", "101", "111"), "7": ("111", "001", "010", "010", "010"),
    "8": ("111", "101", "111", "101", "111"), "9": ("111", "101", "111", "001", "111"),
    ":": ("000", "010", "000", "010", "000"), " ": ("000", "000", "000", "000", "000"),
    "-": ("000", "000", "111", "000", "000"), ".": ("000", "000", "000", "000", "010"),
}

_font_cache = {}


def _load_font(size, path=None):
    key = (path, size)
    if key in _font_cache:
        return _font_cache[key]
    from PIL import ImageFont
    paths = [path] if path else []
    paths += [p for p in _FONT_CANDIDATES if os.path.exists(p)]
    for p in paths:
        try:
            f = ImageFont.truetype(p, size)
            _font_cache[key] = f
            return f
        except Exception:
            continue
    _font_cache[key] = None
    return None


def text_columns(text, height=ROWS, font_path=None, tight=True):
    """Текст -> список столбцов. Ширина произвольная, обрезкой займётся вызывающий."""
    font = _load_font(height, font_path)
    if font is None:
        return tiny_columns(text)
    from PIL import Image, ImageDraw
    img = Image.new("1", (len(text) * (height + 4) + 8, height + 6), 0)
    d = ImageDraw.Draw(img)
    d.text((0, 0), text, fill=1, font=font)
    px = img.load()
    cols = []
    for x in range(img.width):
        v = 0
        for y in range(min(height, img.height)):
            if px[x, y]:
                v |= 1 << y
        cols.append(v)
    if tight:
        while cols and cols[0] == 0:
            cols.pop(0)
        while cols and cols[-1] == 0:
            cols.pop()
    return cols


def tiny_columns(text, gap=1):
    """Шрифт 3x5 без внешних файлов. Цифры, двоеточие, дефис, точка, пробел."""
    cols = []
    for ch in str(text):
        g = _TINY.get(ch, _TINY[" "])
        if cols:
            cols += [0] * gap
        for x in range(3):
            v = 0
            for y in range(5):
                if g[y][x] == "1":
                    v |= 1 << (y + 3)          # по центру по высоте
            cols.append(v)
    return cols


class Canvas:
    """Холст 44x11. Все методы возвращают self — удобно цепочкой."""

    def __init__(self, cols=COLS, rows=ROWS):
        self.cols = cols
        self.rows = rows
        self.buf = [0] * cols

    def clear(self):
        self.buf = [0] * self.cols
        return self

    def px(self, x, y, on=True):
        if 0 <= x < self.cols and 0 <= y < self.rows:
            if on:
                self.buf[x] |= 1 << y
            else:
                self.buf[x] &= ~(1 << y)
        return self

    def vbar(self, x, height, from_bottom=True):
        h = max(0, min(self.rows, int(height)))
        if h and 0 <= x < self.cols:
            m = ((1 << h) - 1)
            self.buf[x] |= (m << (self.rows - h)) if from_bottom else m
        return self

    def hline(self, y, x0=0, x1=None):
        x1 = self.cols if x1 is None else x1
        for x in range(max(0, x0), min(self.cols, x1)):
            self.px(x, y)
        return self

    def blit(self, cols, x=0, mode="or"):
        for i, v in enumerate(cols):
            xx = x + i
            if 0 <= xx < self.cols:
                if mode == "set":
                    self.buf[xx] = v
                else:
                    self.buf[xx] |= v
        return self

    def text(self, s, x=0, center=False, font_path=None, tiny=False):
        cols = tiny_columns(s) if tiny else text_columns(s, self.rows, font_path)
        if center:
            x = max(0, (self.cols - len(cols)) // 2)
        return self.blit(cols, x)

    def progress(self, frac, y=None):
        """Полоса заполнения по строке — например секунды под часами."""
        y = self.rows - 1 if y is None else y
        n = int(max(0.0, min(1.0, frac)) * self.cols)
        for x in range(n):
            self.px(x, y)
        return self

    def shift_up(self, n=1):
        self.buf = [v >> n for v in self.buf]
        return self

    def frame(self):
        return list(self.buf)


class Scroller:
    """Бесконечная бегущая строка. Отдаёт по кадру за вызов."""

    def __init__(self, text, cols=COLS, gap=8, font_path=None, tiny=False):
        body = tiny_columns(text) if tiny else text_columns(text, ROWS, font_path)
        self.strip = body + [0] * max(gap, cols)
        self.cols = cols
        self.pos = 0.0

    def set_text(self, text, gap=8, font_path=None, tiny=False):
        body = tiny_columns(text) if tiny else text_columns(text, ROWS, font_path)
        self.strip = body + [0] * max(gap, self.cols)
        self.pos = 0.0

    def step(self, dx=1.0):
        self.pos = (self.pos + dx) % len(self.strip)
        p = int(self.pos)
        n = len(self.strip)
        return [self.strip[(p + i) % n] for i in range(self.cols)]
