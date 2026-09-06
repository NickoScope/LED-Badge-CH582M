"""Источники кадров. Любой поток данных -> кадры для бейджа.

Источник — асинхронный генератор кадров. Кадр это список столбцов
(бит 0 = верхний пиксель), длиной COLS.
"""
import asyncio
import math
import os
import time
from datetime import datetime

from .canvas import Canvas, Scroller
from .proto import COLS, ROWS

REGISTRY = {}


def source(name):
    def deco(cls):
        REGISTRY[name] = cls
        return cls
    return deco


class Source:
    """База. Наследник реализует frame(t) или переопределяет run().

    keys — состояние кнопок бейджа (badge.ble.Keys), демон подставляет его
    перед запуском; None, если ввода нет. Опрос: keys.take("KEY1") — сколько
    раз нажали с прошлого кадра, keys.is_down("KEY2") — держат ли сейчас,
    keys.held_for("KEY2") — как долго. Ожидание: await keys.queue.get().
    """
    name = "source"
    keys = None

    def __init__(self, **opts):
        self.opts = opts
        self.t0 = time.monotonic()

    def frame(self, t):
        raise NotImplementedError

    async def run(self, out, stop):
        """out(frame) — корутина отправки. stop — asyncio.Event."""
        while not stop.is_set():
            await out(self.frame(time.monotonic() - self.t0))


@source("clock")
class Clock(Source):
    """Часы хоста. В отличие от встроенного CLOCK MODE не зависят от RTC бейджа
    и не сбиваются при его перезагрузке."""
    name = "clock"

    def frame(self, t):
        now = datetime.now()
        fmt = self.opts.get("format", "%H:%M")
        c = Canvas().text(now.strftime(fmt), center=True,
                          tiny=self.opts.get("tiny", False))
        if self.opts.get("seconds_bar", True):
            c.progress(now.second / 60.0)
        return c.frame()


@source("text")
class Text(Source):
    """Статичный текст по центру."""
    name = "text"

    def frame(self, t):
        return Canvas().text(self.opts.get("text", ""), center=True).frame()


@source("scroll")
class Scroll(Source):
    """Бегущая строка. Текст можно менять на ходу через .set_text()."""
    name = "scroll"

    def __init__(self, **opts):
        super().__init__(**opts)
        self.sc = Scroller(opts.get("text", ""), gap=opts.get("gap", 8))
        self.speed = float(opts.get("speed", 1.0))

    def set_text(self, s):
        self.sc.set_text(s, gap=self.opts.get("gap", 8))

    def frame(self, t):
        return self.sc.step(self.speed)


@source("sysinfo")
class SysInfo(Source):
    """Загрузка CPU, память и температура Pi — бегущей строкой."""
    name = "sysinfo"

    def __init__(self, **opts):
        super().__init__(**opts)
        self.sc = Scroller("", gap=10)
        self.last = 0.0

    def _read(self):
        try:
            load = os.getloadavg()[0]
        except Exception:
            load = 0.0
        temp = ""
        for p in ("/sys/class/thermal/thermal_zone0/temp",):
            try:
                with open(p) as f:
                    temp = " %.0fC" % (int(f.read().strip()) / 1000.0)
                break
            except Exception:
                pass
        mem = ""
        try:
            with open("/proc/meminfo") as f:
                d = {}
                for line in f:
                    k, v = line.split(":", 1)
                    d[k] = int(v.split()[0])
            used = 100 - 100 * d.get("MemAvailable", 0) / max(1, d.get("MemTotal", 1))
            mem = " MEM %.0f%%" % used
        except Exception:
            pass
        return "LOAD %.2f%s%s" % (load, mem, temp)

    def frame(self, t):
        if t - self.last > 5.0:
            self.sc.set_text(self._read(), gap=10)
            self.last = t
        return self.sc.step(float(self.opts.get("speed", 1.0)))


@source("stdin")
class Stdin(Source):
    """Строки из stdin бегущей строкой. Можно пайпить что угодно:

        journalctl -f | badgectl stream stdin
    """
    name = "stdin"

    def __init__(self, **opts):
        super().__init__(**opts)
        self.sc = Scroller(opts.get("text", "..."), gap=10)

    async def run(self, out, stop):
        import sys
        loop = asyncio.get_running_loop()
        q = asyncio.Queue()

        def reader():
            line = sys.stdin.readline()
            if line:
                loop.call_soon_threadsafe(q.put_nowait, line.rstrip("\n"))

        loop.add_reader(sys.stdin.fileno(), reader)
        try:
            while not stop.is_set():
                while not q.empty():
                    self.sc.set_text(q.get_nowait(), gap=10)
                await out(self.sc.step(float(self.opts.get("speed", 1.0))))
        finally:
            try:
                loop.remove_reader(sys.stdin.fileno())
            except Exception:
                pass


@source("spectrum")
class Spectrum(Source):
    """Полосы. С микрофоном, если есть sounddevice, иначе синтетические."""
    name = "spectrum"

    def __init__(self, **opts):
        super().__init__(**opts)
        self.n = COLS // 2
        self.lv = [0.0] * self.n
        self.stream = None
        self.buf = None
        if opts.get("mic", True):
            try:
                import numpy as np
                import sounddevice as sd
                self.np = np
                self.buf = np.zeros(1024, dtype="float32")

                def cb(indata, frames, tinfo, status):
                    self.buf = indata[:, 0].copy()

                self.stream = sd.InputStream(channels=1, samplerate=16000,
                                             blocksize=1024, callback=cb)
                self.stream.start()
            except Exception:
                self.stream = None

    def _levels(self, t):
        if self.stream is not None:
            np = self.np
            spec = np.abs(np.fft.rfft(self.buf * np.hanning(len(self.buf))))
            bands = np.array_split(spec[1:len(spec) // 2], self.n)
            return [float(np.log1p(b.mean()) * 1.6) for b in bands]
        return [(math.sin(t * (1.1 + i * 0.13) + i) * 0.5 + 0.5)
                * (1 - i / (self.n * 1.5)) * ROWS for i in range(self.n)]

    def frame(self, t):
        want = self._levels(t)
        c = Canvas()
        for i in range(self.n):
            self.lv[i] = max(want[i], self.lv[i] - 0.5)
            h = int(max(0, min(ROWS, self.lv[i])))
            c.vbar(i * 2, h).vbar(i * 2 + 1, h)
        return c.frame()


@source("image")
class ImageSrc(Source):
    """Кадры из файла: PNG/GIF. Анимированный GIF листается по кадрам."""
    name = "image"

    def __init__(self, **opts):
        super().__init__(**opts)
        from PIL import Image, ImageSequence
        im = Image.open(opts["path"])
        self.frames = []
        for fr in ImageSequence.Iterator(im):
            g = fr.convert("L").resize((COLS, ROWS))
            px = g.load()
            cols = []
            for x in range(COLS):
                v = 0
                for y in range(ROWS):
                    if px[x, y] > 127:
                        v |= 1 << y
                cols.append(v)
            self.frames.append(cols)
        self.fps = float(opts.get("fps", 10))

    def frame(self, t):
        return self.frames[int(t * self.fps) % len(self.frames)]


@source("mqtt")
class Mqtt(Source):
    """Текст из MQTT-топика бегущей строкой. Удобно для Home Assistant."""
    name = "mqtt"

    def __init__(self, **opts):
        super().__init__(**opts)
        self.sc = Scroller(opts.get("text", ""), gap=10)
        import paho.mqtt.client as mqtt
        self.cli = mqtt.Client()
        if opts.get("username"):
            self.cli.username_pw_set(opts["username"], opts.get("password"))
        self.cli.on_message = lambda c, u, m: self.sc.set_text(
            m.payload.decode("utf-8", "replace"), gap=10)
        self.cli.connect(opts.get("host", "localhost"), int(opts.get("port", 1883)), 30)
        self.cli.subscribe(opts.get("topic", "badge/text"))
        self.cli.loop_start()

    def frame(self, t):
        return self.sc.step(float(self.opts.get("speed", 1.0)))


@source("platformer")
class Platformer(Source):
    """Две минуты из жизни бегущего человечка: трубы, ямы, монеты, флаг.

    Мир прокручивается влево, герой сидит на месте по горизонтали и сам решает,
    когда прыгать. Всё детерминировано от позиции в мире, так что уровень
    воспроизводим и не требует памяти под историю.
    """
    name = "platformer"

    GROUND = ROWS - 1
    HERO_X = 7

    RUN = (("010", "111", "010", "101", "101"),
           ("010", "111", "010", "110", "011"))
    JUMP = ("111", "010", "111", "101", "100")

    def __init__(self, **opts):
        super().__init__(**opts)
        self.speed = float(opts.get("speed", 11.0))   # столбцов в секунду
        # auto=1 — старый режим: герой прыгает сам, демонстрация без кнопок.
        # Иначе прыжок по KEY1; отпустил рано — прыжок короче. Если кнопок
        # нет (старая прошивка, keys is None) — тоже автопрыжок.
        self.auto = str(opts.get("auto", "0")).lower() in ("1", "true", "yes", "on")
        self.runs = 0
        self.best = 0
        self.last = 0.0
        self.dead_until = 0.0
        self._reset_run()

    def _reset_run(self):
        self.wx = 0.0
        self.y = 0.0            # высота над землёй
        self.vy = 0.0
        self.coins = 0
        self.taken = set()

    # --- опора и препятствия под героем -------------------------------------
    def _floor(self, hx):
        """Высота опоры под героем: 0 — земля, h — крыша трубы (стоять можно
        и на краю, поэтому максимум по трём столбцам), None — центр героя
        над ямой: край ямы ногами не зацепить."""
        if self._gap(hx + 1):
            return None
        return max(self._pipe_h(c) for c in range(hx, hx + 3))

    def _blocked(self, hx):
        """Труба на пути, а герой ниже её крыши — мир дальше не едет."""
        return max(self._pipe_h(c) for c in range(hx, hx + 3)) > self.y + 0.001

    def _dead_frame(self, t):
        c = Canvas()
        c.text("COINS %d" % self.coins, center=True, tiny=True)
        if int(t * 4) % 2:
            c.hline(ROWS - 1)
        return c.frame()

    # --- мир ---------------------------------------------------------------
    def _pipe_h(self, wx):
        """Труба высотой 2..4 примерно каждые 19 столбцов."""
        if wx < 24 or wx % 19 not in (0, 1, 2):
            return 0
        return 2 + (wx // 19) % 3

    def _gap(self, wx):
        """Яма шириной 3 — редко. 43 и 19 взаимно просты, поэтому труба
        иногда попадает прямо на яму (475, 646, 817…); такую яму не ставим,
        иначе герой упирался бы в трубу, стоя над пустотой. Не ставим её и
        когда труба кончается за 4–8 столбцов до ямы: естественный прыжок
        через трубу приземлял бы ровно в яму (1849, 2021, 2193 — перебор
        состояний показал, что это ловушка с окном в пару столбцов). Труба
        вплотную перед ямой или сразу за ней — честное препятствие."""
        if not (wx > 40 and (wx // 43) % 4 == 3 and wx % 43 in (0, 1, 2)):
            return False
        base = wx - wx % 43
        over = any(self._pipe_h(c) for c in range(base, base + 3))
        trap = any(self._pipe_h(c) for c in range(base - 8, base - 3))
        return not (over or trap)

    def _coin(self, wx):
        return wx > 12 and wx % 13 == 6

    def _flag(self, wx):
        """Флаг в конце круга — каждые ~330 столбцов."""
        return wx % 331 in (0, 1)

    def _obstacle_ahead(self, wx, look=7):
        for d in range(2, look):
            if self._pipe_h(wx + d) or self._gap(wx + d):
                return d
        return 0

    # --- кадр --------------------------------------------------------------
    def frame(self, t):
        dt = min(0.08, max(0.0, t - self.last))
        self.last = t
        # Ручной режим — с первого реального нажатия; до него герой играет
        # сам, так что с прошивкой без событий он не застынет у первой трубы.
        keys = None if self.auto else self.keys
        manual = keys is not None and getattr(keys, "total", 1) > 0
        # нажатия снимаем каждый кадр: скопившиеся в полёте или на экране
        # смерти не должны выстреливать «чужим» прыжком при приземлении
        jump = bool(keys.take("KEY1")) if manual else False

        if self.dead_until:
            if t < self.dead_until:
                return self._dead_frame(t)
            self.dead_until = 0.0
            self._reset_run()

        # горизонталь: мир едет, пока герой не упёрся носом в трубу
        new_wx = self.wx + self.speed * dt
        if self._blocked(int(new_wx) + self.HERO_X):
            new_wx = self.wx           # упёрся; в auto герой прыгнет ещё раз
        self.wx = new_wx
        wx0 = int(self.wx)
        hx = wx0 + self.HERO_X

        # вертикаль
        floor = self._floor(hx)
        y_prev = self.y
        on_ground = floor is not None and self.vy <= 0 and abs(self.y - floor) < 0.001
        if on_ground:
            self.y = float(floor)
            self.vy = 0.0
            if manual:
                if jump:
                    self.vy = 16.0
            else:
                d = self._obstacle_ahead(hx)
                if d:
                    need = self._pipe_h(hx + d)
                    self.vy = 16.0 if need >= 3 or self._gap(hx + d) else 14.0
                else:
                    # монеты висят на высоте 5 — за ними тоже подпрыгиваем
                    for k in range(2, 6):
                        if self._coin(hx + k) and (hx + k) not in self.taken:
                            self.vy = 15.0
                            break
        else:
            self.vy -= 26.0 * dt
            if manual and self.vy > 10.2 and not keys.is_down("KEY1"):
                self.vy = 10.2         # отпустил рано — прыжок короче, но
                                       # самую низкую трубу (h=2) ещё берёт:
                                       # sqrt(2*26*2) = 10.2
            elif not manual and floor is None and self.vy < 0:
                self.vy = 16.0         # автопилот не умирает: над ямой
                                       # отталкивается от воздуха
        self.y += self.vy * dt
        # приземление только сверху: кто провалился ниже опоры, назад не
        # выкарабкается — иначе герой цеплялся за дальний край ямы
        if floor is not None and self.vy < 0 and y_prev >= floor - 1e-9 and self.y <= floor:
            self.y = float(floor)
            self.vy = 0.0
        if self.y < -6.0:              # провалился в яму
            self.runs += 1
            self.best = max(self.best, self.coins)
            self.dead_until = t + 1.5
            return self._dead_frame(t)

        c = Canvas()

        # облака — параллакс, вдвое медленнее
        for sx in range(COLS):
            cwx = int(self.wx * 0.5) + sx
            if cwx % 23 in (0, 1, 2, 3) and (cwx // 23) % 2 == 0:
                c.px(sx, 0)
            if cwx % 23 in (1, 2) and (cwx // 23) % 2 == 0:
                c.px(sx, 1)

        # земля, трубы, ямы, монеты, флаг
        for sx in range(COLS):
            wx = wx0 + sx
            if not self._gap(wx):
                c.px(sx, self.GROUND)
            h = self._pipe_h(wx)
            for k in range(h):
                c.px(sx, self.GROUND - 1 - k)
            if self._coin(wx) and wx not in self.taken:
                c.px(sx, self.GROUND - 5)
            if self._flag(wx):
                for y in range(2, self.GROUND):
                    c.px(sx, y)
                c.px(sx + 1, 2).px(sx + 2, 2).px(sx + 1, 3)

        # герой
        hy = int(round(self.y))
        top = self.GROUND - 4 - hy
        sprite = self.JUMP if hy > 0 else self.RUN[int(t * 9) % 2]
        for row, bits in enumerate(sprite):
            for col, ch in enumerate(bits):
                if ch == "1":
                    c.px(self.HERO_X + col, top + row)

        # подбор монеты
        hero_wx = wx0 + self.HERO_X
        for d in range(0, 3):
            w = hero_wx + d
            if self._coin(w) and w not in self.taken and top <= self.GROUND - 5 <= top + 4:
                self.taken.add(w)
                self.coins += 1
                for yy in range(2, 6):
                    c.px(self.HERO_X + d, yy)

        return c.frame()
