#!/usr/bin/env python3
"""Оффлайн-проверки без бейджа: разбор уведомлений, состояние кнопок,
платформер с искусственным игроком. Запуск из rpi/:

    python3 test_offline.py

Нужны только зависимости пакета (aiohttp для импорта демона).
"""
import asyncio
import sys

sys.path.insert(0, ".")
from badge import proto                      # noqa: E402
from badge.ble import Keys                   # noqa: E402
from badge.sources import Platformer         # noqa: E402
from badge.daemon import Daemon              # noqa: E402


class FakeKeys:
    """Минимум, который читает источник: take(), is_down(), total."""
    def __init__(self, total=1):
        self.n = 0
        self.held = False
        self.total = total      # 0 — кнопок ещё не трогали: автопилот

    def take(self, key):
        n, self.n = self.n, 0
        return n

    def is_down(self, key):
        return self.held


def test_parse_notify():
    assert proto.parse_notify(b"\x00") == {"type": "status", "code": 0}
    assert proto.parse_notify(b"\xff") == {"type": "status", "code": 255}
    assert proto.parse_notify(b"\xe0\x01") == {"type": "key", "key": "KEY1",
                                               "down": True, "code": 1}
    e = proto.parse_notify(b"\xe0\x82")
    assert e["key"] == "KEY2" and e["down"] is False
    assert proto.parse_notify(b"\xe0\x00") == {"type": "exit"}
    assert proto.parse_notify(b"\xe0\x07")["type"] == "unknown"
    assert proto.parse_notify(b"\x01\x02\x03")["type"] == "unknown"


async def test_keys():
    k = Keys()
    k.feed(proto.parse_notify(b"\xe0\x01"), t=1.0)
    assert k.is_down("KEY1") and k.presses["KEY1"] == 1 and k.queue.qsize() == 1
    assert abs(k.held_for("KEY1", now=1.35) - 0.35) < 1e-9
    k.feed(proto.parse_notify(b"\xe0\x81"), t=1.4)
    assert not k.is_down("KEY1") and k.take("KEY1") == 1 and k.take("KEY1") == 0
    assert k.take_releases("KEY1") == 1
    assert k.feed(proto.parse_notify(b"\x00")) is False
    fired = []
    k.on_exit = lambda: fired.append(1)
    k.feed(proto.parse_notify(b"\xe0\x02"), t=2.0)
    k.feed(proto.parse_notify(b"\xe0\x00"), t=2.1)
    assert k.badge_exited and not k.is_down("KEY2") and fired == [1]
    assert k.queue.empty()
    snap = k.snapshot()
    assert snap["events_total"] == 3 and snap["badge_exited"]
    assert len(k.recent(2)) == 2 and k.recent(0) == [] and k.recent(-1) == []
    k.reset()
    assert not k.badge_exited
    # переполнение очереди: старое вытесняется, исключений нет
    k2 = Keys(maxlen=4)
    for i in range(10):
        k2.feed({"type": "key", "key": "KEY1", "down": bool(i % 2)}, t=float(i))
    assert k2.queue.qsize() == 4 and len(k2.events) == 4


def play(p, seconds, player=None):
    for i in range(1, int(30 * seconds)):
        t = i / 30
        if player:
            player(p, t)
        p.frame(t)


def test_platformer():
    # без ввода: упор перед первой трубой (столбец 38), жив
    p = Platformer(); f = FakeKeys(); p.keys = f
    play(p, 6)
    assert p.runs == 0 and p.y == 0.0 and int(p.wx) + p.HERO_X == 35, p.wx
    # один прыжок из упора перелетает трубу
    f.n = 1; f.held = True
    for i in range(30):
        if i == 8:
            f.held = False
        p.frame(6.0 + i / 30)
    assert int(p.wx) + p.HERO_X > 41

    # игрок: жмёт за 3..6 столбцов до препятствия, держит 0.25 с
    p = Platformer(); f = FakeKeys(); p.keys = f; hold = [0.0]

    def player(p, t):
        hx = int(p.wx) + p.HERO_X
        d = p._obstacle_ahead(hx)
        if p.y <= 0.001 and 3 <= d <= 6 and not f.held:
            f.n += 1; f.held = True; hold[0] = t + 0.25
        if f.held and t >= hold[0]:
            f.held = False
    play(p, 40, player)
    assert p.wx > 300 and p.runs == 0, (p.wx, p.runs)

    # автопилот и старая прошивка (keys=None): три минуты без смертей
    for opts, keys in (({"auto": 1}, FakeKeys()), ({}, None)):
        p = Platformer(**opts); p.keys = keys
        play(p, 180)
        assert p.runs == 0 and p.wx > 1500, (opts, p.wx, p.runs)

    # яма: без прыжка падение, с прыжком перелёт
    p = Platformer(); f = FakeKeys(); p.keys = f
    gap = next(w for w in range(41, 400) if p._gap(w))
    p.wx = gap - p.HERO_X - 6
    play(p, 2)
    assert p.runs == 1, (gap, p.wx, p.y)
    p = Platformer(); f = FakeKeys(); p.keys = f; p.wx = gap - p.HERO_X - 6
    st = {"jumped": False, "hu": 0.0}

    def jumper(p, t):
        hx = int(p.wx) + p.HERO_X
        if not st["jumped"] and p.y <= 0.001 and gap - hx <= 5:
            f.n = 1; f.held = True; st["jumped"] = True; st["hu"] = t + 0.3
        if st["jumped"] and f.held and t >= st["hu"]:
            f.held = False
    play(p, 3, jumper)
    assert p.runs == 0 and int(p.wx) + p.HERO_X > gap + 3, (p.wx, p.runs)

    # с крыши трубы сходит на землю
    p = Platformer(); p.keys = FakeKeys(); p.wx = 38 - p.HERO_X
    p.y = float(p._pipe_h(38)); p.vy = 0.0
    play(p, 1.3)
    assert p.runs == 0 and p.y == 0.0 and int(p.wx) + p.HERO_X > 41, (p.wx, p.y)

    # труба никогда не стоит НАД ямой (43 и 19 взаимно просты), а ямы есть
    p = Platformer()
    bad = [w for w in range(41, 4000) if p._gap(w) and p._pipe_h(w)]
    assert not bad, bad[:5]
    gap_cols = [w for w in range(41, 4000) if p._gap(w)]
    n_gaps = len(gap_cols) // 3
    assert n_gaps >= 8, n_gaps      # факт после правил «над ямой» и «ловушка»: 9
    # ...и труба не кончается за 4-8 столбцов до ямы (ловушка после прыжка)
    for w in gap_cols:
        if w % 43 == 0:
            assert not any(p._pipe_h(c) for c in range(w - 8, w - 3)), w

    # нажатие в полёте не выстреливает при приземлении
    p = Platformer(); f = FakeKeys(); p.keys = f
    f.n = 1; f.held = True; p.frame(0.05)         # прыжок
    f.held = False
    for i in range(2, 8):
        p.frame(i / 30)
    f.n = 3                                        # три тапа в воздухе
    landed = False
    for i in range(8, 90):
        p.frame(i / 30)
        if p.y == 0.0 and p.vy == 0.0:
            landed = True
            p.frame((i + 1) / 30)
            assert p.vy == 0.0, "скопившиеся нажатия дали прыжок"
            break
    assert landed

    # кнопок не трогали (total=0) -> автопилот, герой не стоит у трубы
    p = Platformer(); p.keys = FakeKeys(total=0)
    play(p, 10)
    assert p.wx > 80 and p.runs == 0

    # короткий прыжок (отпустили сразу) берёт самую низкую трубу h=2,
    # но заметно ниже полного (апекс 4.9): срез vy до 10.2 плюс полкадра
    p = Platformer(); f = FakeKeys(); p.keys = f
    f.n = 1; f.held = True; p.frame(0.05); f.held = False
    top = 0.0
    for i in range(2, 60):
        p.frame(i / 30); top = max(top, p.y)
    assert 2.0 <= top <= 3.5, top

    # смерть -> экран COINS -> рестарт
    p = Platformer(); p.keys = FakeKeys(); p.y = -7; p.vy = -1; p.wx = 100
    fr = p.frame(0.05)
    assert len(fr) == proto.COLS and p.runs == 1
    p.frame(2.0)
    assert p.dead_until == 0 and p.wx < 1.0


def test_daemon_status():
    st = Daemon().status()
    assert "keys" in st and st["keys"]["events_total"] == 0


if __name__ == "__main__":
    test_parse_notify(); print("parse_notify OK")
    asyncio.run(test_keys()); print("Keys OK")
    test_platformer(); print("Platformer OK")
    test_daemon_status(); print("Daemon.status OK")
    print("ВСЕ ОФФЛАЙН-ПРОВЕРКИ ПРОШЛИ")
