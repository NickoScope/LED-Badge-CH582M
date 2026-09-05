"""Общее для всех BLE-скриптов: пути и поиск устройства.

Раньше в каждом файле были захардкожены абсолютный путь к venv и UUID
конкретного бейджа. Здесь и то и другое вычисляется.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# UUID сервиса и характеристик прошивки (одинаковы у заводской и badgemagic)
SERVICE_FEE0 = "0000fee0-0000-1000-8000-00805f9b34fb"
CHAR_FEE1 = "0000fee1-0000-1000-8000-00805f9b34fb"
NG_SERVICE = "0000f055-0000-1000-8000-00805f9b34fb"
NG_WRITE = "0000f057-0000-1000-8000-00805f9b34fb"
NG_NOTIFY = "0000f056-0000-1000-8000-00805f9b34fb"

# Имена, под которыми бейдж рекламируется
KNOWN_NAMES = ("LSLED", "LED Badge Magic")

CHUNK = 16          # legacy_ble_rx отвергает любой пакет иной длины
USB_REPORT = 64     # размер HID-отчёта


def add_paths():
    """Подключить venv с bleak и клон led-name-badge-ls32."""
    for v in ("312", ""):
        d = os.path.join(HERE, ".venv%s" % v, "lib")
        if not os.path.isdir(d):
            continue
        for py in sorted(os.listdir(d)):
            sp = os.path.join(d, py, "site-packages")
            if os.path.isdir(sp) and sp not in sys.path:
                sys.path.insert(0, sp)
    ls32 = os.path.join(HERE, "led-name-badge-ls32")
    if os.path.isdir(ls32) and ls32 not in sys.path:
        sys.path.insert(0, ls32)


async def find_badge(timeout=30.0, addr=None):
    """Найти бейдж: по явному адресу, по BADGE_ADDR, иначе по сервису/имени.

    Возвращает объект устройства bleak или None.
    """
    from bleak import BleakScanner

    addr = addr or os.environ.get("BADGE_ADDR")
    if addr:
        return await BleakScanner.find_device_by_address(addr, timeout=timeout)

    found = await BleakScanner.discover(timeout=min(timeout, 15.0), return_adv=True)
    for _, (dev, adv) in found.items():
        uuids = [u.lower() for u in (adv.service_uuids or [])]
        name = adv.local_name or dev.name or ""
        if SERVICE_FEE0 in uuids or name in KNOWN_NAMES:
            return dev
    return None
