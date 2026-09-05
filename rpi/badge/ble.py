"""Транспорт BLE: поиск, соединение, переподключение, запись с учётом MTU.

На Raspberry Pi работает поверх BlueZ без всяких плясок — в отличие от macOS,
где CoreBluetooth требует подписанного Python.app с NSBluetoothAlwaysUsageDescription.
"""
import asyncio
import logging
import time

from . import proto

log = logging.getLogger("badge.ble")


class BadgeError(Exception):
    pass


class NotConnected(BadgeError):
    pass


class Badge:
    """Одно соединение с бейджем.

    Использование:
        async with Badge() as b:
            await b.stream_enter()
            await b.stream(columns)
    """

    def __init__(self, address=None, name=None, adapter=None,
                 target_fps=30.0, reconnect=True):
        self.address = address
        self.name = name
        self.adapter = adapter
        self.target_fps = float(target_fps)
        self.reconnect = reconnect

        self._client = None
        self._no_resp = False
        self._stream_cols = proto.COLS
        self._mtu = 23
        self._next_frame_at = 0.0
        self._streaming = False
        self._notify = []
        self.last_error = None

    # --- свойства ----------------------------------------------------------
    @property
    def connected(self):
        return self._client is not None and self._client.is_connected

    @property
    def mtu(self):
        return self._mtu

    @property
    def stream_cols(self):
        """Сколько столбцов уходит одной ATT-записью. 44 при пропатченной
        прошивке (MTU 128), 30 при штатной (MTU 64)."""
        return self._stream_cols

    @property
    def unacknowledged(self):
        return self._no_resp

    # --- поиск и соединение ------------------------------------------------
    async def discover(self, timeout=15.0):
        from bleak import BleakScanner
        kw = {"adapter": self.adapter} if self.adapter else {}
        found = await BleakScanner.discover(timeout=timeout, return_adv=True, **kw)
        best = None
        for addr, (dev, adv) in found.items():
            nm = adv.local_name or dev.name or ""
            uuids = [u.lower() for u in (adv.service_uuids or [])]
            if self.address and addr.lower() != self.address.lower():
                continue
            if self.name and nm != self.name:
                continue
            if proto.SVC_LEGACY in uuids or nm in proto.ADV_NAMES:
                rssi = adv.rssi if adv.rssi is not None else -999
                if best is None or rssi > best[0]:
                    best = (rssi, dev, nm)
        if best is None:
            return None
        log.info("найден %s (%s), RSSI %d", best[2], best[1].address, best[0])
        return best[1]

    async def connect(self, timeout=25.0, attempts=5):
        from bleak import BleakClient
        for n in range(1, attempts + 1):
            dev = await self.discover()
            if dev is None:
                self.last_error = "не в эфире"
                log.warning("бейдж не в эфире (попытка %d/%d)", n, attempts)
                await asyncio.sleep(3)
                continue
            try:
                cl = BleakClient(dev, timeout=timeout)
                await cl.connect()
            except Exception as e:
                self.last_error = repr(e)
                log.warning("подключение не удалось: %r (попытка %d/%d)", e, n, attempts)
                await asyncio.sleep(3)
                continue
            self._client = cl
            self._after_connect()
            self.last_error = None
            return True
        log.error("подключиться не удалось: %s. Если бейдж виден в эфире, но "
                  "соединение отваливается по тайм-ауту — его BLE-стек мог "
                  "подвиснуть после грубо оборванного клиента; помогает "
                  "перезагрузка бейджа через меню OFF", self.last_error)
        return False

    def _after_connect(self):
        self._mtu = getattr(self._client, "mtu_size", 23) or 23
        ch = None
        for s in self._client.services:
            for c in s.characteristics:
                if c.uuid == proto.CHR_NG_TX:
                    ch = c
        if ch is None:
            raise BadgeError("нет характеристики F057 — нужна прошивка badgemagic")
        self._no_resp = "write-without-response" in ch.properties
        self._stream_cols = proto.max_stream_cols(self._mtu)
        log.info("MTU=%d, запись %s, кадр %d столбцов",
                 self._mtu,
                 "без подтверждения" if self._no_resp else "с подтверждением",
                 self._stream_cols)
        if self._stream_cols < proto.COLS:
            log.warning("MTU %d мал: полный кадр уйдёт длинной записью — "
                        "медленно и рвёт связь. Правая часть панели будет "
                        "статична. См. апстрим PR #194.", self._mtu)

    async def disconnect(self):
        if self._client is not None:
            try:
                if self._streaming:
                    await self._raw_ng(proto.cmd_stream_leave())
                await self._client.disconnect()
            except Exception:
                pass
        self._client = None
        self._streaming = False

    async def __aenter__(self):
        if not await self.connect():
            raise NotConnected("не удалось подключиться к бейджу")
        return self

    async def __aexit__(self, *exc):
        await self.disconnect()

    async def _ensure(self):
        if self.connected:
            return
        if not self.reconnect:
            raise NotConnected("соединение потеряно")
        log.info("переподключаюсь...")
        self._client = None
        if not await self.connect():
            raise NotConnected("переподключиться не удалось")
        if self._streaming:
            await self._raw_ng(proto.cmd_stream_enter())

    # --- next-gen ----------------------------------------------------------
    async def _raw_ng(self, payload, response=True):
        await self._client.write_gatt_char(proto.CHR_NG_TX, payload, response=response)

    async def ng(self, payload, response=True):
        """Отправить команду next-gen. Авторизация НЕ требуется — гейт
        !authorized стоит только в legacy_ble_rx()."""
        await self._ensure()
        try:
            await self._raw_ng(payload, response)
        except Exception:
            await self._ensure()
            await self._raw_ng(payload, response)

    async def enable_notify(self, cb=None):
        def _cb(_h, data):
            b = bytes(data)
            self._notify.append(b)
            if cb:
                cb(b)
        try:
            await self._client.start_notify(proto.CHR_NG_RX, _cb)
            return True
        except Exception as e:
            log.debug("notify недоступен: %r", e)
            return False

    async def stream_enter(self):
        await self.ng(proto.cmd_stream_enter())
        self._streaming = True
        self._next_frame_at = time.monotonic()

    async def stream_leave(self):
        await self.ng(proto.cmd_stream_leave())
        self._streaming = False

    async def stream(self, columns, pace=True):
        """Один кадр прямо в фреймбуфер. Флеш не трогается.

        Темп задаём сами: при записи без подтверждения обратной связи нет,
        и цикл иначе забивает очередь стека быстрее, чем она отправляется.
        """
        await self._ensure()
        payload = proto.cmd_stream_frame(columns, self._stream_cols)
        try:
            await self._client.write_gatt_char(
                proto.CHR_NG_TX, payload, response=not self._no_resp)
        except Exception as e:
            log.warning("кадр не ушёл: %r", e)
            await self._ensure()
            return False
        if pace and self.target_fps > 0:
            self._next_frame_at += 1.0 / self.target_fps
            delay = self._next_frame_at - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            elif delay < -1.0:
                self._next_frame_at = time.monotonic()
        return True

    async def clear(self):
        """Погасить ВСЮ панель — полным кадром, даже если поток узкий."""
        await self.ng(proto.cmd_stream_frame([0] * proto.COLS, proto.COLS))

    async def brightness(self, level):
        await self.ng(proto.cmd_brightness(level))

    async def set_always_on(self, on=True, save=True):
        await self.ng(proto.cmd_ble_always_on(on))
        if save:
            await self.ng(proto.cmd_save_cfg())

    async def set_reset_after_rx(self, on=False, save=True):
        await self.ng(proto.cmd_reset_after_rx(on))
        if save:
            await self.ng(proto.cmd_save_cfg())

    async def set_name(self, name, save=True):
        await self.ng(proto.cmd_ble_name(name))
        if save:
            await self.ng(proto.cmd_save_cfg())

    # --- legacy: сохраняемая загрузка --------------------------------------
    async def upload(self, messages, pin, speeds=5, modes=0,
                     brightness=100, when=None, progress=None):
        """Записать сообщения во флеш. Заодно выставляет RTC из timestamp.

        Нужен PIN с экрана (меню -> BT-PAIRING). Код и данные уходят в ОДНОМ
        соединении: авторизация сбрасывается при разрыве.
        """
        await self._ensure()
        payload = proto.legacy_payload(messages, speeds, modes, brightness, when)
        nframes = max(len(m) for m in messages) // proto.COLS or 1
        ram = proto.frames_ram_estimate(nframes)
        if ram > 12000:
            raise BadgeError(
                "слишком длинная анимация: ~%.1f КБ RAM при распаковке. "
                "На 17.8 КБ прошивка виснет при старте и требует аппаратного "
                "входа в ISP. Безопасно до ~60 кадров. Апстрим: issue #195."
                % (ram / 1024))
        await self._client.write_gatt_char(
            proto.CHR_LEGACY, proto.pin_packet(pin), response=True)
        chunks = [payload[i:i + proto.LEGACY_CHUNK]
                  for i in range(0, len(payload), proto.LEGACY_CHUNK)]
        for i, ch in enumerate(chunks, 1):
            await self._client.write_gatt_char(proto.CHR_LEGACY, ch, response=True)
            if progress and i % 25 == 0:
                progress(i, len(chunks))
        return len(chunks)
