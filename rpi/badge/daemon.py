"""Демон: единолично держит BLE-соединение и отдаёт HTTP API.

Bluetooth-канал к бейджу один, поэтому владелец должен быть один. Всё
остальное — CLI, MCP-сервер, скрипты, Home Assistant — ходит сюда по HTTP.
"""
import asyncio
import logging
import time

from aiohttp import web

from . import proto
from .ble import Badge
from .canvas import Canvas
from .sources import REGISTRY

log = logging.getLogger("badge.daemon")


class Daemon:
    def __init__(self, address=None, adapter=None, fps=30.0):
        self.badge = Badge(address=address, adapter=adapter, target_fps=fps)
        self.source = None
        self.source_name = None
        self.task = None
        self.stop_ev = asyncio.Event()
        self.frames = 0
        self.started = time.time()
        self.last_error = None
        self.lock = asyncio.Lock()

    # --- жизненный цикл потока ---------------------------------------------
    async def _loop(self, src):
        async def out(frame):
            ok = await self.badge.stream(frame)
            if ok:
                self.frames += 1
        try:
            await src.run(out, self.stop_ev)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.last_error = repr(e)
            log.exception("источник упал")

    async def set_source(self, name, **opts):
        if name not in REGISTRY:
            raise web.HTTPBadRequest(
                reason="неизвестный источник %r; есть: %s"
                       % (name, ", ".join(sorted(REGISTRY))))
        async with self.lock:
            await self._stop_locked()
            src = REGISTRY[name](**opts)
            self.source = src
            self.source_name = name
            self.stop_ev = asyncio.Event()
            if not self.badge.connected:
                await self.badge.connect()
            await self.badge.clear()
            await self.badge.stream_enter()
            self.task = asyncio.create_task(self._loop(src))
        return True

    async def _stop_locked(self):
        self.stop_ev.set()
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):
                pass
            self.task = None
        self.source = None
        self.source_name = None

    async def stop(self):
        async with self.lock:
            await self._stop_locked()
            if self.badge.connected:
                try:
                    await self.badge.stream_leave()
                except Exception:
                    pass

    def status(self):
        return {
            "connected": self.badge.connected,
            "mtu": self.badge.mtu,
            "stream_cols": self.badge.stream_cols,
            "panel_cols": proto.COLS,
            "unacknowledged_writes": self.badge.unacknowledged,
            "full_width": self.badge.stream_cols >= proto.COLS,
            "source": self.source_name,
            "frames_sent": self.frames,
            "uptime_s": round(time.time() - self.started, 1),
            "target_fps": self.badge.target_fps,
            "last_error": self.last_error,
            "sources_available": sorted(REGISTRY),
        }


# --- HTTP -------------------------------------------------------------------
def build_app(d):
    routes = web.RouteTableDef()

    @routes.get("/status")
    async def status(r):
        return web.json_response(d.status())

    @routes.post("/source")
    async def set_source(r):
        body = await r.json()
        name = body.pop("name", None)
        if not name:
            raise web.HTTPBadRequest(reason="нужно поле name")
        await d.set_source(name, **body)
        return web.json_response({"ok": True, "source": name})

    @routes.post("/text")
    async def text(r):
        body = await r.json()
        s = body.get("text", "")
        scroll = bool(body.get("scroll", len(s) > 7))
        await d.set_source("scroll" if scroll else "text",
                           text=s, speed=float(body.get("speed", 1.0)))
        return web.json_response({"ok": True, "text": s, "scroll": scroll})

    @routes.post("/clock")
    async def clock(r):
        body = await r.json() if r.can_read_body else {}
        await d.set_source("clock", format=body.get("format", "%H:%M"),
                           seconds_bar=body.get("seconds_bar", True))
        return web.json_response({"ok": True})

    @routes.post("/frame")
    async def frame(r):
        """Один кадр напрямую: {"columns": [44 числа]}. Поток при этом не нужен."""
        body = await r.json()
        cols = [int(v) & 0x07FF for v in body.get("columns", [])]
        if not d.badge.connected:
            await d.badge.connect()
        if d.source is None:
            await d.badge.stream_enter()
        await d.badge.stream(cols, pace=False)
        return web.json_response({"ok": True})

    @routes.post("/brightness")
    async def brightness(r):
        body = await r.json()
        lvl = int(body.get("level", 3))
        if not d.badge.connected:
            await d.badge.connect()
        await d.badge.brightness(lvl)
        return web.json_response({"ok": True, "level": max(0, min(3, lvl))})

    @routes.post("/stop")
    async def stop(r):
        await d.stop()
        return web.json_response({"ok": True})

    @routes.post("/clear")
    async def clear(r):
        await d.stop()
        if not d.badge.connected:
            await d.badge.connect()
        await d.badge.stream_enter()
        await d.badge.clear()
        await d.badge.stream_leave()
        return web.json_response({"ok": True})

    @routes.post("/config")
    async def config(r):
        body = await r.json()
        if not d.badge.connected:
            await d.badge.connect()
        done = []
        if "always_on" in body:
            await d.badge.set_always_on(bool(body["always_on"]), save=False)
            done.append("always_on")
        if "reset_after_rx" in body:
            await d.badge.set_reset_after_rx(bool(body["reset_after_rx"]), save=False)
            done.append("reset_after_rx")
        if "name" in body:
            await d.badge.set_name(str(body["name"]), save=False)
            done.append("name")
        if done and body.get("save", True):
            await d.badge.ng(proto.cmd_save_cfg())
            done.append("saved")
        return web.json_response({"ok": True, "applied": done})

    @routes.post("/upload")
    async def upload(r):
        """Записать во флеш — играет без хоста. Нужен PIN с экрана бейджа."""
        body = await r.json()
        pin = body.get("pin")
        if not pin:
            raise web.HTTPBadRequest(
                reason="нужен pin: на бейдже меню -> BT-PAIRING, 4 цифры с экрана")
        msgs = body.get("messages")
        if not msgs:
            text = body.get("text", "")
            msgs = [Canvas(cols=max(proto.COLS, 1)).text(text).frame()] if text else None
            if body.get("text"):
                from .canvas import text_columns
                msgs = [text_columns(body["text"])]
        if not msgs:
            raise web.HTTPBadRequest(reason="нужно поле text или messages")
        await d.stop()
        if not d.badge.connected:
            await d.badge.connect()
        try:
            n = await d.badge.upload(msgs, pin,
                                     speeds=int(body.get("speed", 5)),
                                     modes=int(body.get("mode", 0)),
                                     brightness=int(body.get("brightness", 100)))
        except Exception as e:
            raise web.HTTPBadRequest(reason=str(e))
        return web.json_response({"ok": True, "packets": n,
                                  "note": "RTC выставлен из timestamp заголовка"})

    app = web.Application()
    app.add_routes(routes)
    return app


async def run(host="127.0.0.1", port=8477, address=None,
              adapter=None, fps=30.0, source=None, source_opts=None):
    d = Daemon(address=address, adapter=adapter, fps=fps)
    if not await d.badge.connect():
        log.warning("бейдж не найден — API поднят, подключусь при первом запросе")
    if source:
        await d.set_source(source, **(source_opts or {}))
    app = build_app(d)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    log.info("HTTP API на http://%s:%d", host, port)
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await d.stop()
        await d.badge.disconnect()
        await runner.cleanup()
