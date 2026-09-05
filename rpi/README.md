# badge-rpi — LED-бейдж CH582M с Raspberry Pi 5

Управление бейджем по Bluetooth LE: поток кадров, запись во флеш, настройки.
Три способа обращения — командная строка, HTTP API и **MCP-сервер**.

На Pi всё проще, чем на macOS: `bleak` работает поверх BlueZ напрямую, без
подписанных копий интерпретатора и плясок с разрешениями.

## Установка

```bash
./install.sh
.venv/bin/python badgectl scan
.venv/bin/python badgectl info
```

`info` покажет главное — согласованный MTU и ширину кадра:

```json
{"mtu": 128, "stream_cols": 44, "full_width": true, "unacknowledged_writes": true}
```

**44 столбца** — прошивка с патчем MTU, вся панель обновляется одной посылкой.
**30 столбцов** — штатная прошивка: полный кадр не влезает в ATT-запись, правая
часть панели останется статичной. Код это определяет сам и пишет предупреждение.
Апстрим: PR #194.

## Командная строка

```bash
badgectl scan                             # найти в эфире
badgectl info                             # MTU, ширина кадра
badgectl stream clock                     # часы потоком
badgectl stream scroll -o text=ПРИВЕТ -o speed=2
badgectl stream sysinfo                   # загрузка и температура Pi
badgectl stream image -o path=anim.gif -o fps=12
journalctl -f | badgectl stream stdin     # ЛЮБОЙ поток строк
badgectl bright 2                         # яркость 0..3
badgectl send "NICKO" --pin 1234          # ЗАПИСЬ ВО ФЛЕШ, играет без Pi
badgectl config --always-on 1             # держать BLE всегда
badgectl daemon --source clock            # демон с HTTP API
```

## HTTP API

Демон единолично владеет BLE-каналом — он один, и делить его нельзя.
Все остальные клиенты ходят сюда.

```bash
curl localhost:8477/status
curl -X POST localhost:8477/text       -d '{"text":"ПРИВЕТ","scroll":true}'
curl -X POST localhost:8477/clock      -d '{}'
curl -X POST localhost:8477/source     -d '{"name":"sysinfo"}'
curl -X POST localhost:8477/brightness -d '{"level":2}'
curl -X POST localhost:8477/frame      -d '{"columns":[2047,0,2047,...]}'
curl -X POST localhost:8477/upload     -d '{"pin":"1234","text":"HELLO"}'
curl -X POST localhost:8477/config     -d '{"always_on":true}'
curl -X POST localhost:8477/clear
```

## MCP-сервер

```bash
.venv/bin/python badgectl daemon &        # сначала демон
```

Регистрация:

```json
{
  "mcpServers": {
    "badge": {
      "command": "/home/pi/badge/rpi/.venv/bin/python",
      "args": ["/home/pi/badge/rpi/badge_mcp.py"],
      "env": {"BADGE_API": "http://127.0.0.1:8477"}
    }
  }
}
```

Десять инструментов: `badge_status`, `badge_show_text`, `badge_show_clock`,
`badge_set_source`, `badge_send_frame`, `badge_set_brightness`, `badge_clear`,
`badge_stop`, `badge_upload_text`, `badge_configure`.

## Служба

```bash
sudo cp badge-daemon.service /etc/systemd/system/
sudo systemctl enable --now badge-daemon
journalctl -u badge-daemon -f
```

## Что надо знать про это железо

**Поток и запись — разные вещи.** `stream` пишет прямо в фреймбуфер: износа
флеша нет, но нужен работающий Pi. `send`/`upload` кладёт во флеш: играет
само, Pi не нужен, но это запись в память и она конечна.

**Запись во флеш требует PIN** — четыре цифры с экрана: меню -> `BT-PAIRING`.
Код генерируется заново при каждом входе и сбрасывается при разрыве связи,
поэтому PIN и данные уходят в одном соединении. Потоку и настройкам код не нужен.

**Загрузка заодно выставляет часы.** Прошивка берёт время из поля timestamp
заголовка — другого пути к RTC нет.

**После загрузки бейдж выключает рекламу** и не виден в эфире до перезагрузки
или до входа в `BT-PAIRING`, даже с включённым always-on.

**Длина сохранённой анимации ограничена ОЗУ, а не памятью.** Больше ~60 кадров
роняет прошивку при старте, и восстановление требует аппаратного входа в ISP.
`upload` откажется грузить заведомо опасное. Апстрим: issue #195.

**Темп задаёт клиент.** При записи без подтверждения обратной связи нет: без
ограничения цикл забивает очередь стека. В `Badge.stream()` это уже сделано.

**Яркости всего 4 уровня**, плавного диммирования у железа нет.

## Формат кадра

44 столбца по 11 значащих бит. **Бит 0 — верхний пиксель.** Кадр целиком:
`1 + 44*2 = 89` байт, поэтому и нужен MTU не меньше 92.

```python
from badge.canvas import Canvas
frame = Canvas().text("21:45", center=True).progress(0.5).frame()
```

## Свой источник

```python
from badge.sources import Source, source

@source("mysrc")
class MySource(Source):
    def frame(self, t):
        from badge.canvas import Canvas
        return Canvas().text("%.1f" % t, center=True).frame()
```

Дальше `badgectl stream mysrc` и `badge_set_source` в MCP подхватят его сами.
