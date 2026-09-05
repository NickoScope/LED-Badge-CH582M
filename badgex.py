#!/usr/bin/env python3
"""
badgex - расширенный отправщик для LED-бейджа 0416:5020 (CH583).

Зачем поверх lednamebadge.py:
  * штатный CLI зажимает номер режима в 0..8 (LedNameBadge.header -> _prepare_iterable),
    а протокол кладёт режим в младший полубайт h[8+i] = 16*speed + mode, т.е. допускает 0..15.
    Режимы 9 (Smooth) и 10 (Rotate) есть в приложении вендора и, по doc/instructions.txt,
    "Stays on, even if the cable is connected" - то есть НЕ уходят в заставку зарядки.
  * режим часов: периодически перезаписывает время.

Источник раскладки заголовка: led-name-badge-ls32/lednamebadge.py, LedNameBadge.header().
"""
import argparse
import os
import sys
import time
from array import array
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'led-name-badge-ls32'))
from lednamebadge import LedNameBadge, SimpleTextAndIcons  # noqa: E402

MODE_NAMES = {
    0: 'scroll-left', 1: 'scroll-right', 2: 'scroll-up', 3: 'scroll-down',
    4: 'still-centered', 5: 'animation', 6: 'drop-down', 7: 'curtain', 8: 'laser',
    9: 'smooth - НЕ РАБОТАЕТ на CH583 (пустой экран)',
    10: 'rotate - НЕ ПРОВЕРЕН, вероятно тоже не работает',
}


def fix_brightness(h, brightness):
    """Байт 5 - ИНДЕКС уровня яркости, не битовая маска.

    Панель имеет 4 уровня: 0x00=100%, 0x10=75%, 0x20=50%, 0x30=25%.
    lednamebadge.py шлёт 0x40 для 25% - это индекс 4 в таблице из 4 элементов,
    на CH582/CH583 портит изображение вместо затемнения.
    Источник: github.com/martinbogo/badge-studio PROTOCOL.md (реверс на CH582/CH583).
    """
    if brightness <= 25:
        h[5] = 0x30
    elif brightness <= 50:
        h[5] = 0x20
    elif brightness <= 75:
        h[5] = 0x10
    else:
        h[5] = 0x00
    return h


def build(messages, speed=5, mode=4, brightness=100, blink=0, ants=0, when=None):
    """Собрать буфер: 64-байтный заголовок + битмапы."""
    creator = SimpleTextAndIcons()
    bitmaps = [creator.bitmap(m) for m in messages]
    lengths = [b[1] for b in bitmaps]
    n = len(bitmaps)

    when = when or datetime.now()
    h = LedNameBadge.header(lengths, [min(max(speed, 1), 8)] * n, [0] * n,
                            [blink] * n, [ants] * n, brightness, when)

    # Ключевая правка: кладём режим напрямую, минуя зажим 0..8.
    fix_brightness(h, brightness)
    speed_bits = min(max(speed, 1), 8) - 1
    for i in range(8):
        h[8 + i] = (speed_bits << 4) | (mode & 0x0F)

    buf = array('B')
    buf.extend(h)
    for b in bitmaps:
        buf.extend(b[0])
    # lednamebadge шлёт range(len(buf)/64) отчётов - неполный хвост ОТБРАСЫВАЕТСЯ.
    # Дополняем нулями до кратности 64, иначе теряются последние столбцы битмапа.
    if len(buf) % 64:
        buf.extend([0] * (64 - len(buf) % 64))
    return buf


def send(buf, method='hidapi'):
    LedNameBadge.write(buf, method, 'auto')


def device_present():
    """Есть ли бейдж на шине прямо сейчас."""
    try:
        import pyhidapi
        pyhidapi.hid_init()
        return len(pyhidapi.hid_enumerate(0x0416, 0x5020)) > 0
    except Exception:
        return False


def wait_for_device(timeout=60, poll=1.0):
    """Ждать возвращения устройства. После каждой записи прошивка
    перезагружается и переэнумерируется - в этом окне устройства нет."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if device_present():
            return True
        time.sleep(poll)
    return False


def send_retry(buf, method='hidapi', attempts=5, settle=2.0):
    """Отправить с повтором. lednamebadge при ненайденном устройстве делает
    sys.exit - перехватываем SystemExit, иначе цикл часов умирает."""
    for n in range(1, attempts + 1):
        if not wait_for_device(timeout=30):
            print('  устройство не появилось на шине (попытка %d/%d)' % (n, attempts), flush=True)
            continue
        try:
            send(buf, method)
            time.sleep(settle)  # даём прошивке перезагрузиться
            return True
        except SystemExit:
            print('  запись не прошла, устройство исчезло (попытка %d/%d)' % (n, attempts), flush=True)
        except Exception as e:
            print('  ошибка записи: %r (попытка %d/%d)' % (e, n, attempts), flush=True)
        time.sleep(settle)
    return False


def main():
    p = argparse.ArgumentParser(description='Расширенный отправщик для LED-бейджа (режимы 0..15, часы).')
    p.add_argument('-m', '--mode', type=int, default=4,
                   help='режим 0..15. Рабочие на CH583: 0..8. По умолчанию 4 (still-centered). '
                        'Режим 9 проверен - даёт пустой экран, не использовать.')
    p.add_argument('-s', '--speed', type=int, default=5, help='скорость 1..8')
    p.add_argument('-B', '--brightness', type=int, default=100, help='яркость 25/50/75/100')
    p.add_argument('-b', '--blink', type=int, default=0)
    p.add_argument('-a', '--ants', type=int, default=0)
    p.add_argument('--fill8', action='store_true',
                   help='продублировать сообщение во все 8 слотов - чтобы было видно и в режиме M1-8')
    p.add_argument('--clock', action='store_true', help='режим часов: обновлять время бесконечно')
    p.add_argument('--format', default='%H:%M', help='формат времени для --clock (по умолчанию %%H:%%M)')
    p.add_argument('--interval', type=int, default=60,
                   help='секунд между обновлениями часов (по умолчанию 60)')
    p.add_argument('message', nargs='*', help='до 8 сообщений; картинка - :путь/к/файлу.png:')
    args = p.parse_args()

    if args.mode > 8:
        print('ВНИМАНИЕ: режим %d на CH583 проверен и даёт ПУСТОЙ экран. Рабочие: 0..8.' % args.mode)

    if args.clock:
        print('Часы: формат %s, обновление раз в %d с, режим %d (%s).'
              % (args.format, args.interval, args.mode, MODE_NAMES.get(args.mode, '?')))
        print('ВНИМАНИЕ: каждое обновление - запись во флеш бейджа. Ctrl+C для остановки.')
        try:
            while True:
                now = datetime.now()
                text = now.strftime(args.format)
                msgs = [text] * 8 if args.fill8 else [text]
                ok = send_retry(build(msgs, args.speed, args.mode, args.brightness,
                                      args.blink, args.ants, now))
                print('  %s -> %s' % (text, 'отправлено' if ok else 'ПРОПУЩЕНО'), flush=True)
                # спим до начала следующего интервала, чтобы не уплывать
                time.sleep(max(1, args.interval - (time.time() % args.interval)))
        except KeyboardInterrupt:
            print('\nОстановлено.')
        return

    if not args.message:
        p.error('нужно хотя бы одно сообщение (или --clock)')

    print('Режим %d (%s), скорость %d, яркость %d'
          % (args.mode, MODE_NAMES.get(args.mode, 'недокументированный'), args.speed, args.brightness))
    msgs = args.message * 8 if (args.fill8 and len(args.message) == 1) else args.message
    ok = send_retry(build(msgs, args.speed, args.mode, args.brightness, args.blink, args.ants))
    print('Результат:', 'отправлено' if ok else 'НЕ ОТПРАВЛЕНО')


if __name__ == '__main__':
    main()
