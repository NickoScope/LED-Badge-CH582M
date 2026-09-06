"""Командная строка."""
import argparse
import asyncio
import json
import logging
import statistics
import sys
import time

from . import proto
from .ble import Badge
from .canvas import text_columns
from .daemon import run as run_daemon
from .sources import REGISTRY


def _log(v):
    logging.basicConfig(
        level=logging.DEBUG if v else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S")


async def cmd_scan(a):
    b = Badge(adapter=a.adapter)
    dev = await b.discover(timeout=a.timeout)
    print(json.dumps({"found": bool(dev),
                      "address": getattr(dev, "address", None),
                      "name": getattr(dev, "name", None)}, ensure_ascii=False))
    return 0 if dev else 1


async def cmd_info(a):
    async with Badge(address=a.address, adapter=a.adapter) as b:
        print(json.dumps({
            "mtu": b.mtu, "stream_cols": b.stream_cols,
            "panel_cols": proto.COLS,
            "full_width": b.stream_cols >= proto.COLS,
            "unacknowledged_writes": b.unacknowledged,
        }, ensure_ascii=False, indent=2))
    return 0


async def cmd_stream(a):
    if a.source not in REGISTRY:
        print("нет такого источника: %s\nесть: %s"
              % (a.source, ", ".join(sorted(REGISTRY))), file=sys.stderr)
        return 2
    opts = dict(kv.split("=", 1) for kv in a.opt) if a.opt else {}
    src = REGISTRY[a.source](**opts)
    stop = asyncio.Event()
    # Обрыв на полуслове подвешивает BLE-стек бейджа: он продолжает
    # рекламироваться, но перестаёт принимать подключения. Успеваем выйти
    # из режима стриминга и разорвать связь по-человечески.
    import signal
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    async with Badge(address=a.address, adapter=a.adapter, target_fps=a.fps) as b:
        await b.clear()
        await b.stream_enter()
        src.keys = b.keys                 # ввод источнику: keys.take("KEY1") и т.д.
        b.keys.on_exit = stop.set         # долгий KEY2 на бейдже — конец потока
        n = 0

        async def out(frame):
            nonlocal n
            if await b.stream(frame):
                n += 1
                if a.verbose and n % 100 == 0:
                    print("кадров: %d" % n, file=sys.stderr)
        try:
            await src.run(out, stop)
        except KeyboardInterrupt:
            pass
        finally:
            if not b.keys.badge_exited:
                await b.stream_leave()
    return 0


async def cmd_keys(a):
    """Монитор кнопок: печатает фронты, зажигает половины панели в ответ.

    Число в конце строки — хостовая часть круга: от прихода события до
    записанного ответного кадра. Радиочасть (опрос кнопки 20 мс плюс
    интервал соединения в обе стороны) сюда не входит, её видно только
    по экрану.
    """
    stop = asyncio.Event()
    import signal
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    lat = []
    async with Badge(address=a.address, adapter=a.adapter) as b:
        await b.clear()
        await b.stream_enter()
        print("жду события: KEY1 — левая половина, KEY2 — правая; "
              "долгий KEY2 на бейдже — выход, Ctrl-C — тоже", file=sys.stderr)
        t_prev = None
        try:
            while not stop.is_set():
                try:
                    t, key, down = await asyncio.wait_for(b.keys.queue.get(), 0.25)
                except asyncio.TimeoutError:
                    if b.keys.badge_exited:
                        break
                    continue
                k1, k2 = b.keys.is_down("KEY1"), b.keys.is_down("KEY2")
                cols = [0x07FF if (x < proto.COLS // 2 and k1) or
                        (x >= proto.COLS // 2 and k2) else 0
                        for x in range(proto.COLS)]
                t0 = time.monotonic()
                ok = await b.stream(cols, pace=False)
                ms = (time.monotonic() - t0) * 1000
                if ok:
                    lat.append(ms)
                gap = "" if t_prev is None else "  +%4.0f мс" % ((t - t_prev) * 1000)
                t_prev = t
                print("%s  %-4s %-9s%s   кадр в ответ за %.1f мс"
                      % (time.strftime("%H:%M:%S"), key,
                         "нажата" if down else "отпущена", gap, ms))
            if b.keys.badge_exited:
                print("бейдж вышел из стриминга сам (долгий KEY2)")
        finally:
            if lat:
                print("хостовая часть круга, событие -> кадр записан: "
                      "медиана %.1f мс, макс %.1f мс, n=%d"
                      % (statistics.median(lat), max(lat), len(lat)), file=sys.stderr)
            if b.connected and not b.keys.badge_exited:
                await b.stream_leave()
    return 0


async def cmd_send(a):
    """Разовая запись во флеш — играет без хоста."""
    cols = text_columns(a.text)
    async with Badge(address=a.address, adapter=a.adapter) as b:
        n = await b.upload([cols], a.pin, speeds=a.speed, modes=a.mode,
                           brightness=a.brightness,
                           progress=lambda i, t: print("  %d/%d" % (i, t), file=sys.stderr))
    print(json.dumps({"ok": True, "packets": n}, ensure_ascii=False))
    return 0


async def cmd_bright(a):
    async with Badge(address=a.address, adapter=a.adapter) as b:
        await b.brightness(a.level)
    return 0


async def cmd_config(a):
    async with Badge(address=a.address, adapter=a.adapter) as b:
        if a.always_on is not None:
            await b.set_always_on(a.always_on, save=False)
        if a.reset_after_rx is not None:
            await b.set_reset_after_rx(a.reset_after_rx, save=False)
        if a.name:
            await b.set_name(a.name, save=False)
        await b.ng(proto.cmd_save_cfg())
    print("сохранено")
    return 0


async def cmd_daemon(a):
    opts = dict(kv.split("=", 1) for kv in a.opt) if a.opt else {}
    await run_daemon(host=a.host, port=a.port, address=a.address,
                     adapter=a.adapter, fps=a.fps,
                     source=a.source, source_opts=opts)
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="badgectl", description="LED-бейдж CH582M по Bluetooth LE")
    p.add_argument("-a", "--address", help="BLE-адрес, иначе автопоиск")
    p.add_argument("--adapter", help="адаптер BlueZ, например hci0")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="найти бейдж в эфире")
    s.add_argument("--timeout", type=float, default=15.0)
    s.set_defaults(fn=cmd_scan)

    s = sub.add_parser("info", help="MTU, ширина кадра, режим записи")
    s.set_defaults(fn=cmd_info)

    s = sub.add_parser("stream", help="поток кадров из источника")
    s.add_argument("source", choices=sorted(REGISTRY))
    s.add_argument("--fps", type=float, default=30.0)
    s.add_argument("-o", "--opt", action="append", metavar="KEY=VALUE")
    s.set_defaults(fn=cmd_stream)

    s = sub.add_parser("keys", help="события кнопок в стриминге: монитор и замер")
    s.set_defaults(fn=cmd_keys)

    s = sub.add_parser("send", help="записать текст во флеш (нужен PIN)")
    s.add_argument("text")
    s.add_argument("--pin", required=True, help="4 цифры: меню -> BT-PAIRING")
    s.add_argument("--mode", type=int, default=0, choices=range(0, 9))
    s.add_argument("--speed", type=int, default=5, choices=range(1, 9))
    s.add_argument("--brightness", type=int, default=100)
    s.set_defaults(fn=cmd_send)

    s = sub.add_parser("bright", help="яркость 0..3")
    s.add_argument("level", type=int, choices=range(0, 4))
    s.set_defaults(fn=cmd_bright)

    s = sub.add_parser("config", help="настройки бейджа")
    s.add_argument("--always-on", dest="always_on", type=lambda v: v == "1",
                   choices=[True, False], metavar="0|1")
    s.add_argument("--reset-after-rx", dest="reset_after_rx",
                   type=lambda v: v == "1", choices=[True, False], metavar="0|1")
    s.add_argument("--name")
    s.set_defaults(fn=cmd_config, always_on=None, reset_after_rx=None, name=None)

    s = sub.add_parser("daemon", help="демон с HTTP API (для MCP и прочих клиентов)")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8477)
    s.add_argument("--fps", type=float, default=30.0)
    s.add_argument("--source", help="стартовый источник")
    s.add_argument("-o", "--opt", action="append", metavar="KEY=VALUE")
    s.set_defaults(fn=cmd_daemon)

    a = p.parse_args(argv)
    _log(a.verbose)
    try:
        return asyncio.run(a.fn(a))
    except KeyboardInterrupt:
        return 130
