import asyncio
from bleak import BleakScanner

async def main():
    print("сканирую 12 секунд...")
    devs = await BleakScanner.discover(timeout=12.0, return_adv=True)
    if not devs:
        print("НИЧЕГО НЕ НАЙДЕНО (проверь, включён ли Bluetooth и есть ли доступ)")
        return
    rows = []
    for addr, (d, adv) in devs.items():
        name = adv.local_name or d.name or ""
        uuids = ",".join(adv.service_uuids or [])
        rows.append((adv.rssi if adv.rssi is not None else -999, addr, name, uuids))
    rows.sort(reverse=True)
    print("%-38s %-24s %5s  %s" % ("ADDR", "NAME", "RSSI", "SERVICE UUIDS"))
    for rssi, addr, name, uuids in rows:
        mark = ""
        low = (name + " " + uuids).lower()
        if "fee0" in low or "led" in low or "badge" in low or "ls32" in low:
            mark = "   <<=== КАНДИДАТ"
        print("%-38s %-24s %5d  %s%s" % (addr, name[:24], rssi, uuids[:60], mark))

asyncio.run(main())
