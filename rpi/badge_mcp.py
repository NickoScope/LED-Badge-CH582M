#!/usr/bin/env python3
"""MCP-сервер для LED-бейджа CH582M.

Тонкий клиент демона: BLE-канал к бейджу один, поэтому им владеет badge-daemon,
а этот сервер ходит в него по HTTP. Запускать демон отдельно:

    badgectl daemon

Адрес демона берётся из BADGE_API (по умолчанию http://127.0.0.1:8477).
"""
import json
import os
from typing import List, Optional

import httpx
from pydantic import BaseModel, ConfigDict, Field

# В mcp 2.x FastMCP переименован в MCPServer. Поддерживаем обе версии,
# чтобы пакет ставился на Pi независимо от того, какая подтянется.
try:
    from mcp.server.mcpserver import MCPServer as _Server   # mcp >= 2
except ImportError:                                          # pragma: no cover
    from mcp.server.fastmcp import FastMCP as _Server        # mcp 1.x

mcp = _Server("badge_mcp")

API = os.environ.get("BADGE_API", "http://127.0.0.1:8477")
TIMEOUT = float(os.environ.get("BADGE_API_TIMEOUT", "30"))

PANEL_COLS = 44
PANEL_ROWS = 11

_STRICT = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")


async def _call(method: str, path: str, body: Optional[dict] = None) -> str:
    """Запрос к демону. Ошибки возвращаются текстом с подсказкой, что делать."""
    url = API + path
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.request(method, url, json=body)
    except httpx.ConnectError:
        return json.dumps({
            "error": "демон не отвечает на %s" % API,
            "fix": "запусти его командой: badgectl daemon",
        }, ensure_ascii=False)
    except httpx.TimeoutException:
        return json.dumps({
            "error": "демон не ответил за %.0f с" % TIMEOUT,
            "hint": "долгие операции — это upload; проверь badge_status",
        }, ensure_ascii=False)
    if r.status_code >= 400:
        return json.dumps({
            "error": r.reason_phrase or ("HTTP %d" % r.status_code),
            "status": r.status_code,
        }, ensure_ascii=False)
    return json.dumps(r.json(), ensure_ascii=False)


# --- состояние --------------------------------------------------------------
@mcp.tool(
    name="badge_status",
    annotations={"title": "Состояние бейджа", "readOnlyHint": True,
                 "destructiveHint": False, "idempotentHint": True,
                 "openWorldHint": False},
)
async def badge_status() -> str:
    """Состояние бейджа и демона.

    Возвращает: подключён ли бейдж, согласованный MTU, ширину кадра в столбцах
    (44 при пропатченной прошивке, 30 при штатной), текущий источник,
    число отправленных кадров, последнюю ошибку и список доступных источников.

    Начинай с этого инструмента: поле full_width показывает, обновляется ли
    вся панель или только левая часть.
    """
    return await _call("GET", "/status")


# --- показ ------------------------------------------------------------------
class ShowTextInput(BaseModel):
    """Текст на экран."""
    model_config = _STRICT
    text: str = Field(..., description="Что показать, например 'ПРИВЕТ' или 'CPU 42%'",
                      min_length=1, max_length=200)
    scroll: Optional[bool] = Field(
        default=None,
        description="Бегущей строкой. По умолчанию само: длинный текст бежит, короткий стоит")
    speed: float = Field(default=1.0, description="Скорость прокрутки, столбцов за кадр",
                         ge=0.1, le=8.0)


@mcp.tool(
    name="badge_show_text",
    annotations={"title": "Показать текст", "readOnlyHint": False,
                 "destructiveHint": False, "idempotentHint": True,
                 "openWorldHint": False},
)
async def badge_show_text(params: ShowTextInput) -> str:
    """Показать текст на бейдже потоком с хоста.

    Панель 44x11, помещается около семи символов. Более длинный текст лучше
    пускать бегущей строкой. Ничего не пишется во флеш — показ живёт, пока
    работает демон.
    """
    body = {"text": params.text, "speed": params.speed}
    if params.scroll is not None:
        body["scroll"] = params.scroll
    return await _call("POST", "/text", body)


class ShowClockInput(BaseModel):
    """Часы на экран."""
    model_config = _STRICT
    format: str = Field(default="%H:%M", description="Формат strftime, например '%H:%M' или '%d.%m'",
                        min_length=1, max_length=32)
    seconds_bar: bool = Field(default=True, description="Полоса секунд по нижней строке")


@mcp.tool(
    name="badge_show_clock",
    annotations={"title": "Показать часы", "readOnlyHint": False,
                 "destructiveHint": False, "idempotentHint": True,
                 "openWorldHint": False},
)
async def badge_show_clock(params: ShowClockInput) -> str:
    """Часы, считаемые хостом.

    Не путать со встроенным режимом CLOCK MODE: тот идёт от RTC чипа и сбивается
    при каждой перезагрузке бейджа. Этот показ всегда точен, но требует демона.
    """
    return await _call("POST", "/clock",
                       {"format": params.format, "seconds_bar": params.seconds_bar})


class SetSourceInput(BaseModel):
    """Переключение источника кадров."""
    model_config = _STRICT
    name: str = Field(..., description="Имя источника: clock, text, scroll, sysinfo, "
                                       "stdin, spectrum, image, mqtt")
    options: Optional[dict] = Field(
        default=None,
        description="Параметры источника, например {'text': 'HELLO', 'speed': 2} "
                    "или {'path': '/home/pi/anim.gif', 'fps': 12}")


@mcp.tool(
    name="badge_set_source",
    annotations={"title": "Выбрать источник кадров", "readOnlyHint": False,
                 "destructiveHint": False, "idempotentHint": True,
                 "openWorldHint": False},
)
async def badge_set_source(params: SetSourceInput) -> str:
    """Переключить источник потока.

    sysinfo показывает загрузку и температуру Pi, spectrum — уровень с микрофона,
    image листает PNG или GIF, mqtt выводит текст из топика, stdin — строки,
    которые пишут демону в стандартный ввод.

    Полный список доступных источников есть в badge_status.
    """
    body = dict(params.options or {})
    body["name"] = params.name
    return await _call("POST", "/source", body)


class FrameInput(BaseModel):
    """Готовый кадр."""
    model_config = _STRICT
    columns: List[int] = Field(
        ..., description="44 числа, по одному на столбец. Бит 0 — ВЕРХНИЙ пиксель, "
                         "значимы биты 0..10. Например 0x07FF — столбец горит целиком",
        min_length=1, max_length=44)


@mcp.tool(
    name="badge_send_frame",
    annotations={"title": "Отправить кадр", "readOnlyHint": False,
                 "destructiveHint": False, "idempotentHint": True,
                 "openWorldHint": False},
)
async def badge_send_frame(params: FrameInput) -> str:
    """Отправить один готовый кадр прямо в фреймбуфер.

    Кадр — 44 столбца по 11 значащих бит, бит 0 сверху. Пишется мимо флеша,
    износа нет. Удобно для собственной графики: рисуешь кадры сам и шлёшь.
    """
    return await _call("POST", "/frame", {"columns": params.columns})


class BrightnessInput(BaseModel):
    """Яркость."""
    model_config = _STRICT
    level: int = Field(..., description="0 самый тусклый, 3 самый яркий. Уровней всего 4",
                       ge=0, le=3)


@mcp.tool(
    name="badge_set_brightness",
    annotations={"title": "Яркость", "readOnlyHint": False, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": False},
)
async def badge_set_brightness(params: BrightnessInput) -> str:
    """Яркость панели, четыре уровня.

    Плавного диммирования у железа нет: развёртка гасит светодиоды на такте
    state > level, поэтому переходы между уровнями заметны ступенями.
    """
    return await _call("POST", "/brightness", {"level": params.level})


@mcp.tool(
    name="badge_clear",
    annotations={"title": "Погасить экран", "readOnlyHint": False,
                 "destructiveHint": False, "idempotentHint": True,
                 "openWorldHint": False},
)
async def badge_clear() -> str:
    """Остановить поток и погасить всю панель."""
    return await _call("POST", "/clear")


@mcp.tool(
    name="badge_stop",
    annotations={"title": "Остановить поток", "readOnlyHint": False,
                 "destructiveHint": False, "idempotentHint": True,
                 "openWorldHint": False},
)
async def badge_stop() -> str:
    """Остановить поток, оставив на экране последний кадр."""
    return await _call("POST", "/stop")


# --- запись во флеш ---------------------------------------------------------
class UploadInput(BaseModel):
    """Сохраняемая загрузка."""
    model_config = _STRICT
    pin: str = Field(..., description="Четыре цифры с экрана бейджа. Получить: "
                                      "меню -> BT-PAIRING -> KEY1 долго",
                     min_length=4, max_length=4, pattern=r"^\d{4}$")
    text: str = Field(..., description="Текст для записи во флеш", min_length=1, max_length=200)
    mode: int = Field(default=0, description="0 влево, 1 вправо, 2 вверх, 3 вниз, "
                                             "4 статично, 5 анимация, 6 сверху, "
                                             "7 шторка, 8 лазер", ge=0, le=8)
    speed: int = Field(default=5, description="Скорость 1..8", ge=1, le=8)
    brightness: int = Field(default=100, description="Яркость в процентах: 25, 50, 75 или 100",
                            ge=25, le=100)


@mcp.tool(
    name="badge_upload_text",
    annotations={"title": "Записать текст во флеш", "readOnlyHint": False,
                 "destructiveHint": True, "idempotentHint": False,
                 "openWorldHint": False},
)
async def badge_upload_text(params: UploadInput) -> str:
    """Записать текст в память бейджа — играет без компьютера.

    В отличие от показа потоком это ЗАПИСЬ ВО ФЛЕШ: перезаписывает то, что там
    лежало, и расходует ресурс памяти. Зато после этого демон не нужен.

    Побочный эффект: заодно выставляются часы бейджа — прошивка берёт время
    из поля timestamp заголовка, другого пути к RTC нет.

    Нужен свежий PIN: код генерируется при каждом входе в BT-PAIRING и
    сбрасывается при разрыве связи.
    """
    return await _call("POST", "/upload", {
        "pin": params.pin, "text": params.text, "mode": params.mode,
        "speed": params.speed, "brightness": params.brightness,
    })


class ConfigInput(BaseModel):
    """Настройки бейджа."""
    model_config = _STRICT
    always_on: Optional[bool] = Field(
        default=None, description="Держать BLE всегда включённым. Иначе бейдж виден "
                                  "только в режиме BT-PAIRING")
    reset_after_rx: Optional[bool] = Field(
        default=None, description="Перезагружаться после приёма сообщения. "
                                  "Выключено — часы переживают загрузку")
    name: Optional[str] = Field(default=None, description="Имя устройства в эфире, до 20 символов",
                                max_length=20)
    save: bool = Field(default=True, description="Сохранить во флеш, чтобы пережило перезагрузку")


@mcp.tool(
    name="badge_configure",
    annotations={"title": "Настройки бейджа", "readOnlyHint": False,
                 "destructiveHint": False, "idempotentHint": True,
                 "openWorldHint": False},
)
async def badge_configure(params: ConfigInput) -> str:
    """Изменить настройки бейджа: постоянный BLE, ребут после приёма, имя.

    Настройки живут в отдельной памяти и переживают даже перепрошивку.
    Авторизация для них не нужна — PIN требуется только записи во флеш.
    """
    body = {k: v for k, v in {
        "always_on": params.always_on,
        "reset_after_rx": params.reset_after_rx,
        "name": params.name,
        "save": params.save,
    }.items() if v is not None}
    if len(body) <= 1:
        return json.dumps({"error": "нечего менять",
                           "hint": "укажи always_on, reset_after_rx или name"},
                          ensure_ascii=False)
    return await _call("POST", "/config", body)


if __name__ == "__main__":
    mcp.run()
