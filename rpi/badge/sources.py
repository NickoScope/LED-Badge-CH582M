"""Источники кадров. Любой поток данных -> кадры для бейджа.

Источник — асинхронный генератор кадров. Кадр это список столбцов
(бит 0 = верхний пиксель), длиной COLS.
"""
import asyncio
import math
import os
import time
from datetime import datetime

from .canvas import Canvas, Scroller, text_columns
from .proto import COLS, ROWS

REGISTRY = {}


def source(name):
    def deco(cls):
        REGISTRY[name] = cls
        return cls
    return deco


class Source:
    """База. Наследник реализует frame(t) или переопределяет run()."""
    name = "source"

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
