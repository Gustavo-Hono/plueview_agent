from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from fastmcp import Client, FastMCP
from fastmcp.client.transports import StreamableHttpTransport


DEFAULT_SUPABASE_MCP_URL = "https://mcp.supabase.com/mcp"
DEFAULT_LIMIT = 1000

mcp = FastMCP("Hermes Supabase Diagnostics")


def _positive_int(name: str, value: int, *, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} precisa ser inteiro") from exc

    if parsed <= 0 or parsed > maximum:
        raise ValueError(f"{name} precisa estar entre 1 e {maximum}")
    return parsed


def _supabase_mcp_url() -> str:
    configured = os.getenv("SUPABASE_MCP_URL")
    if configured:
        return configured

    project_ref = os.getenv("SUPABASE_PROJECT_REF", "").strip()
    params: dict[str, str] = {"read_only": "true", "features": "database"}
    if project_ref:
        params["project_ref"] = project_ref

    return _merge_query(DEFAULT_SUPABASE_MCP_URL, params)


def _merge_query(url: str, params: dict[str, str]) -> str:
    parsed = urlparse(url)
    current = parse_qs(parsed.query)
    for key, value in params.items():
        current.setdefault(key, [value])

    query = urlencode({key: values[-1] for key, values in current.items()})
    return urlunparse(parsed._replace(query=query))


def _supabase_headers() -> dict[str, str]:
    token = os.getenv("SUPABASE_ACCESS_TOKEN", "").strip()
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def _build_sql(station_id: int, horas: int, limite: int) -> str:
    station_id = _positive_int("station_id", station_id, maximum=1_000_000)
    horas = _positive_int("horas", horas, maximum=24 * 31)
    limite = _positive_int("limite", limite, maximum=10_000)

    return f"""
WITH weather AS (
  SELECT *
  FROM (
    SELECT
      id,
      "stationId",
      "dataMedicao" AS measured_at,
      temperatura,
      umidade,
      "quantidadeChuva",
      "velocidadeVento",
      "direcaoVento"::text AS "direcaoVento",
      latitude,
      longitude
    FROM public.weather_data
    WHERE "stationId" = {station_id}
      AND "dataMedicao" >= now() - interval '{horas} hours'
    ORDER BY "dataMedicao" DESC
    LIMIT {limite}
  ) recent_weather
  ORDER BY measured_at ASC
),
iot AS (
  SELECT *
  FROM (
    SELECT
      id,
      "stationId",
      time AS measured_at,
      battery,
      "ConsumoPluviometro",
      "ConsumoVelocidadeVento",
      "ConsumoDirecaoVento",
      "ConsumoTemperatura",
      "ConsumoUmidade"
    FROM public."DataPlueView"
    WHERE "stationId" = {station_id}
      AND time >= now() - interval '{horas} hours'
    ORDER BY time DESC
    LIMIT {limite}
  ) recent_iot
  ORDER BY measured_at ASC
)
SELECT jsonb_build_object(
  'stationId', {station_id},
  'hours', {horas},
  'generatedAt', now(),
  'weather', COALESCE((SELECT jsonb_agg(to_jsonb(weather)) FROM weather), '[]'::jsonb),
  'iot', COALESCE((SELECT jsonb_agg(to_jsonb(iot)) FROM iot), '[]'::jsonb)
) AS hermes_payload;
""".strip()


def _tool_schema_props(tool: Any) -> dict[str, Any]:
    schema = getattr(tool, "inputSchema", None) or getattr(tool, "input_schema", None)
    if schema is None and hasattr(tool, "model_dump"):
        schema = tool.model_dump().get("inputSchema")
    if not isinstance(schema, dict):
        return {}
    props = schema.get("properties")
    return props if isinstance(props, dict) else {}


def _select_execute_sql_tool(tools: list[Any]) -> tuple[str, dict[str, Any]]:
    for tool in tools:
        name = getattr(tool, "name", "")
        if name == "execute_sql" or name.endswith("_execute_sql"):
            return name, _tool_schema_props(tool)
    available = ", ".join(getattr(tool, "name", "?") for tool in tools)
    raise RuntimeError(f"Ferramenta execute_sql nao encontrada no Supabase MCP. Disponiveis: {available}")


def _execute_sql_arguments(props: dict[str, Any], sql: str) -> dict[str, Any]:
    args: dict[str, Any] = {}

    if "query" in props:
        args["query"] = sql
    elif "sql" in props:
        args["sql"] = sql
    else:
        args["query"] = sql

    project_id = os.getenv("SUPABASE_PROJECT_ID") or os.getenv("SUPABASE_PROJECT_REF")
    if project_id:
        for key in ("project_id", "project_ref", "projectId"):
            if key in props:
                args[key] = project_id
                break

    return args


async def _execute_supabase_sql(sql: str) -> Any:
    transport = StreamableHttpTransport(
        _supabase_mcp_url(),
        headers=_supabase_headers(),
        auth=os.getenv("SUPABASE_MCP_AUTH") or None,
    )
    async with Client(transport, timeout=60) as client:
        tools = await client.list_tools()
        tool_name, props = _select_execute_sql_tool(list(tools))
        result = await client.call_tool(tool_name, _execute_sql_arguments(props, sql), timeout=60)
    return _result_to_python(result)


def _result_to_python(result: Any) -> Any:
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return structured

    data = getattr(result, "data", None)
    if data is not None:
        return data

    content = getattr(result, "content", None) or []
    texts: list[str] = []
    for item in content:
        text = getattr(item, "text", None)
        if text:
            texts.append(text)

    if not texts:
        if hasattr(result, "model_dump"):
            return result.model_dump()
        return result

    return _parse_jsonish_text("\n".join(texts))


def _parse_jsonish_text(text: str) -> Any:
    stripped = text.strip()
    for candidate in (stripped, _first_json_block(stripped)):
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return {"text": text}


def _first_json_block(text: str) -> str | None:
    match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
    return match.group(1) if match else None


def _extract_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = _parse_jsonish_text(value)

    if isinstance(value, dict):
        if "hermes_payload" in value and isinstance(value["hermes_payload"], dict):
            return value["hermes_payload"]
        if "weather" in value or "iot" in value:
            return value
        if "data" in value:
            return _extract_payload(value["data"])
        if "result" in value:
            return _extract_payload(value["result"])
        if "rows" in value:
            return _extract_payload(value["rows"])

    if isinstance(value, list):
        if not value:
            return {"weather": [], "iot": []}
        if len(value) == 1 and isinstance(value[0], dict):
            return _extract_payload(value[0])
        return {"weather": value, "iot": []}

    raise ValueError("Nao consegui encontrar hermes_payload/weather/iot no resultado informado.")


def _rows(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = payload.get(key) or []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value:
        return None

    raw = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _timestamp(row: dict[str, Any]) -> datetime | None:
    for key in ("measured_at", "dataMedicao", "time", "timestamp"):
        parsed = _parse_time(row.get(key))
        if parsed:
            return parsed
    return None


def _num(row: dict[str, Any], key: str) -> float | None:
    value = row.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sort_by_time(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda row: _timestamp(row) or datetime.min.replace(tzinfo=timezone.utc))


def _hours_between(start: datetime, end: datetime) -> float:
    return max((end - start).total_seconds() / 3600, 0.0)


def _fmt_hours(hours: float) -> str:
    if hours < 1:
        return f"{round(hours * 60)}min"
    return f"{hours:.1f}h"


def _max_gap(rows: list[dict[str, Any]]) -> float:
    times = [_timestamp(row) for row in rows]
    times = [time for time in times if time is not None]
    if len(times) < 2:
        return 0.0
    return max(_hours_between(prev, curr) for prev, curr in zip(times, times[1:]))


def _detect_sensor_issues(weather: list[dict[str, Any]], iot: list[dict[str, Any]]) -> list[str]:
    issues: list[str] = []

    if not weather:
        issues.append("sem leituras meteorologicas no periodo")
    else:
        gap = _max_gap(weather)
        if gap >= 3:
            issues.append(f"lacuna de {_fmt_hours(gap)} entre leituras meteorologicas")

        missing_required = sum(
            1
            for row in weather
            if _num(row, "temperatura") is None
            or _num(row, "umidade") is None
            or _num(row, "quantidadeChuva") is None
        )
        if missing_required:
            issues.append(f"{missing_required} leituras com campo meteorologico obrigatorio ausente")

        invalid_temp = [row for row in weather if (temp := _num(row, "temperatura")) is not None and (temp < -20 or temp > 60)]
        invalid_humidity = [row for row in weather if (humidity := _num(row, "umidade")) is not None and not 0 <= humidity <= 100]
        invalid_rain = [row for row in weather if (rain := _num(row, "quantidadeChuva")) is not None and rain < 0]
        if invalid_temp:
            issues.append(f"{len(invalid_temp)} temperaturas fora da faixa fisica esperada")
        if invalid_humidity:
            issues.append(f"{len(invalid_humidity)} umidades fora de 0-100%")
        if invalid_rain:
            issues.append(f"{len(invalid_rain)} leituras de chuva negativas")

    sensor_fields = {
        "ConsumoPluviometro": "pluviometro",
        "ConsumoVelocidadeVento": "velocidade do vento",
        "ConsumoDirecaoVento": "direcao do vento",
        "ConsumoTemperatura": "temperatura",
        "ConsumoUmidade": "umidade",
    }
    for field, label in sensor_fields.items():
        present = [row for row in iot if row.get(field) is not None]
        if not present:
            continue
        zero_or_negative = [row for row in present if (_num(row, field) or 0) <= 0]
        if len(zero_or_negative) >= max(2, len(present) // 3):
            issues.append(f"consumo zerado/negativo recorrente no sensor de {label}")

    return issues


def _detect_battery_issues(iot: list[dict[str, Any]]) -> list[str]:
    battery_rows = [row for row in _sort_by_time(iot) if _num(row, "battery") is not None]
    if not battery_rows:
        return ["sem telemetria de bateria no periodo"]

    values = [_num(row, "battery") for row in battery_rows]
    values = [value for value in values if value is not None]
    latest = values[-1]
    first = values[0]
    minimum = min(values)
    drop = first - latest

    issues: list[str] = []
    if latest < 20:
        issues.append(f"bateria critica em {latest:.0f}%")
    elif latest < 35:
        issues.append(f"bateria baixa em {latest:.0f}%")
    if drop >= 15:
        issues.append(f"queda acumulada de {drop:.0f} pontos percentuais")

    max_step = 0.0
    for previous, current in zip(values, values[1:]):
        max_step = max(max_step, previous - current)
    if max_step >= 10:
        issues.append(f"queda brusca de {max_step:.0f} pontos entre leituras")
    if minimum < 0 or minimum > 100:
        issues.append("valor de bateria fora de 0-100%")

    return issues or [f"estavel, ultima leitura {latest:.0f}%"]


def _detect_weather_issues(weather: list[dict[str, Any]]) -> list[str]:
    rows = _sort_by_time(weather)
    if not rows:
        return ["sem base meteorologica para avaliar clima"]

    issues: list[str] = []
    temps = [value for row in rows if (value := _num(row, "temperatura")) is not None]
    humidities = [value for row in rows if (value := _num(row, "umidade")) is not None]
    rains = [value for row in rows if (value := _num(row, "quantidadeChuva")) is not None]
    winds = [value for row in rows if (value := _num(row, "velocidadeVento")) is not None]

    if temps and (max(temps) >= 45 or min(temps) <= -5):
        issues.append(f"temperatura extrema ({min(temps):.1f} a {max(temps):.1f} C)")
    if humidities and (min(humidities) < 10 or max(humidities) > 98):
        issues.append(f"umidade extrema ({min(humidities):.0f}% a {max(humidities):.0f}%)")
    if rains:
        rain_total = sum(max(rain, 0) for rain in rains)
        rain_peak = max(rains)
        if rain_peak >= 20:
            issues.append(f"pico de chuva de {rain_peak:.1f} mm em uma leitura")
        elif rain_total >= 50:
            issues.append(f"chuva acumulada alta ({rain_total:.1f} mm)")
    if winds and max(winds) >= 20:
        issues.append(f"vento forte detectado ({max(winds):.1f} m/s)")

    for previous, current in zip(rows, rows[1:]):
        previous_time = _timestamp(previous)
        current_time = _timestamp(current)
        if not previous_time or not current_time:
            continue
        delta_hours = max(_hours_between(previous_time, current_time), 0.1)

        previous_temp = _num(previous, "temperatura")
        current_temp = _num(current, "temperatura")
        if previous_temp is not None and current_temp is not None:
            delta_temp = abs(current_temp - previous_temp)
            if delta_temp >= 8 and delta_temp / delta_hours >= 4:
                issues.append(f"salto termico de {delta_temp:.1f} C em {_fmt_hours(delta_hours)}")
                break

    for previous, current in zip(rows, rows[1:]):
        previous_time = _timestamp(previous)
        current_time = _timestamp(current)
        if not previous_time or not current_time:
            continue
        delta_hours = max(_hours_between(previous_time, current_time), 0.1)

        previous_humidity = _num(previous, "umidade")
        current_humidity = _num(current, "umidade")
        if previous_humidity is not None and current_humidity is not None:
            delta_humidity = abs(current_humidity - previous_humidity)
            if delta_humidity >= 35 and delta_hours <= 2:
                issues.append(f"variacao brusca de umidade ({delta_humidity:.0f} pontos em {_fmt_hours(delta_hours)})")
                break

    return issues or ["sem evento climatico estranho nos limiares configurados"]


def _latest_age(rows: list[dict[str, Any]]) -> float | None:
    times = [_timestamp(row) for row in rows]
    times = [time for time in times if time is not None]
    if not times:
        return None
    return _hours_between(max(times), datetime.now(timezone.utc))


def _compact(items: list[str], *, limit: int = 2) -> str:
    if not items:
        return "OK"
    selected = items[:limit]
    remainder = len(items) - len(selected)
    suffix = f"; +{remainder} alerta(s)" if remainder else ""
    return "; ".join(selected) + suffix


def _diagnose_payload(payload: dict[str, Any], station_id: int | None = None, horas: int | None = None) -> str:
    weather = _rows(payload, "weather")
    iot = _rows(payload, "iot")
    weather = _sort_by_time(weather)
    iot = _sort_by_time(iot)

    station = station_id or payload.get("stationId") or payload.get("station_id") or "?"
    window = horas or payload.get("hours") or payload.get("horas") or "?"

    sensor_issues = _detect_sensor_issues(weather, iot)
    battery_issues = _detect_battery_issues(iot)
    climate_issues = _detect_weather_issues(weather)

    weather_age = _latest_age(weather)
    iot_age = _latest_age(iot)
    freshness: list[str] = []
    if weather_age is not None and weather_age >= 2:
        freshness.append(f"meteo ha {_fmt_hours(weather_age)}")
    if iot_age is not None and iot_age >= 2:
        freshness.append(f"IoT ha {_fmt_hours(iot_age)}")

    severe = any(
        "critica" in issue
        or "sem leituras" in issue
        or "fora" in issue
        or "lacuna" in issue
        or "queda brusca" in issue
        for issue in [*sensor_issues, *battery_issues, *climate_issues]
    )
    has_alert = severe or any(
        "baixa" in issue
        or "queda acumulada" in issue
        or "extrema" in issue
        or "forte" in issue
        or "pico" in issue
        or "salto" in issue
        or "brusca" in issue
        for issue in [*sensor_issues, *battery_issues, *climate_issues]
    )
    conclusion = "requer verificacao operacional" if severe else "monitorar" if has_alert else "operacao normal"

    lines = [
        f"Diagnostico curto - estacao {station} (ultimas {window}h)",
        f"Base: {len(weather)} leituras meteo, {len(iot)} leituras IoT"
        + (f"; atraso: {', '.join(freshness)}" if freshness else ""),
        f"Sensor: {_compact(sensor_issues)}",
        f"Bateria: {_compact(battery_issues)}",
        f"Clima: {_compact(climate_issues)}",
        f"Conclusao: {conclusion}.",
    ]
    return "\n".join(lines)


@mcp.tool
def sql_diagnostico_estacao(station_id: int = 1, horas: int = 24, limite: int = DEFAULT_LIMIT) -> str:
    """Gera o SQL read-only para buscar leituras da estacao no Supabase."""

    return _build_sql(station_id, horas, limite)


@mcp.tool
def diagnosticar_resultado_supabase(resultado_json: str, station_id: int = 1, horas: int = 24) -> str:
    """Gera diagnostico curto a partir do JSON retornado pelo execute_sql do Supabase MCP."""

    payload = _extract_payload(resultado_json)
    return _diagnose_payload(payload, station_id=station_id, horas=horas)


@mcp.tool
async def analisar_estacao_supabase(station_id: int = 1, horas: int = 24, limite: int = DEFAULT_LIMIT) -> str:
    """Consulta o Supabase MCP em modo read-only e diagnostica a estacao."""

    sql = _build_sql(station_id, horas, limite)
    result = await _execute_supabase_sql(sql)
    payload = _extract_payload(result)
    return _diagnose_payload(payload, station_id=station_id, horas=horas)


@mcp.prompt
def analisar_estacao_prompt(station_id: int = 1, horas: int = 24) -> str:
    return (
        f"Use o MCP do Supabase em modo read-only para executar o SQL gerado por "
        f"`sql_diagnostico_estacao(station_id={station_id}, horas={horas})`. "
        "Depois envie o JSON retornado para `diagnosticar_resultado_supabase` e "
        "responda apenas com o diagnostico curto."
    )


def _tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "name": "sql_diagnostico_estacao",
            "description": "Gera SQL read-only para buscar leituras da estacao no Supabase.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "station_id": {"type": "integer", "default": 1},
                    "horas": {"type": "integer", "default": 24},
                    "limite": {"type": "integer", "default": DEFAULT_LIMIT},
                },
            },
        },
        {
            "name": "diagnosticar_resultado_supabase",
            "description": "Gera diagnostico curto a partir do JSON retornado pelo Supabase MCP.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "resultado_json": {"type": "string"},
                    "station_id": {"type": "integer", "default": 1},
                    "horas": {"type": "integer", "default": 24},
                },
                "required": ["resultado_json"],
            },
        },
        {
            "name": "analisar_estacao_supabase",
            "description": "Consulta o Supabase MCP read-only e diagnostica a estacao.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "station_id": {"type": "integer", "default": 1},
                    "horas": {"type": "integer", "default": 24},
                    "limite": {"type": "integer", "default": DEFAULT_LIMIT},
                },
            },
        },
    ]


def _prompt_definitions() -> list[dict[str, Any]]:
    return [
        {
            "name": "analisar_estacao_prompt",
            "description": "Instrucao para analisar uma estacao usando Supabase MCP e Hermes.",
            "arguments": [
                {"name": "station_id", "description": "ID da estacao", "required": False},
                {"name": "horas", "description": "Janela em horas", "required": False},
            ],
        }
    ]


def _call_tool_sync(name: str, arguments: dict[str, Any]) -> str:
    if name == "sql_diagnostico_estacao":
        return _build_sql(
            arguments.get("station_id", 1),
            arguments.get("horas", 24),
            arguments.get("limite", DEFAULT_LIMIT),
        )

    if name == "diagnosticar_resultado_supabase":
        raw_result = arguments.get("resultado_json")
        if not isinstance(raw_result, str):
            raw_result = json.dumps(raw_result)
        payload = _extract_payload(raw_result)
        return _diagnose_payload(
            payload,
            station_id=arguments.get("station_id", 1),
            horas=arguments.get("horas", 24),
        )

    if name == "analisar_estacao_supabase":
        station_id = arguments.get("station_id", 1)
        horas = arguments.get("horas", 24)
        limite = arguments.get("limite", DEFAULT_LIMIT)
        sql = _build_sql(station_id, horas, limite)
        result = asyncio_run(_execute_supabase_sql(sql))
        payload = _extract_payload(result)
        return _diagnose_payload(payload, station_id=station_id, horas=horas)

    raise ValueError(f"Ferramenta desconhecida: {name}")


def asyncio_run(awaitable: Any) -> Any:
    import asyncio

    return asyncio.run(awaitable)


def _jsonrpc_result(message_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def _jsonrpc_error(message_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}}


def _handle_stdio_message(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    message_id = message.get("id")
    params = message.get("params") if isinstance(message.get("params"), dict) else {}

    if message_id is None and method and method.startswith("notifications/"):
        return None

    try:
        if method == "initialize":
            return _jsonrpc_result(
                message_id,
                {
                    "protocolVersion": params.get("protocolVersion", "2025-11-25"),
                    "capabilities": {
                        "tools": {"listChanged": False},
                        "prompts": {"listChanged": False},
                    },
                    "serverInfo": {
                        "name": "hermes-supabase-diagnostics",
                        "version": "0.1.0",
                    },
                },
            )

        if method == "ping":
            return _jsonrpc_result(message_id, {})

        if method == "tools/list":
            return _jsonrpc_result(message_id, {"tools": _tool_definitions()})

        if method == "tools/call":
            text = _call_tool_sync(str(params.get("name", "")), params.get("arguments") or {})
            return _jsonrpc_result(
                message_id,
                {
                    "content": [{"type": "text", "text": text}],
                    "isError": False,
                },
            )

        if method == "prompts/list":
            return _jsonrpc_result(message_id, {"prompts": _prompt_definitions()})

        if method == "prompts/get":
            arguments = params.get("arguments") or {}
            station_id = arguments.get("station_id", 1)
            horas = arguments.get("horas", 24)
            return _jsonrpc_result(
                message_id,
                {
                    "description": "Instrucao de diagnostico Hermes.",
                    "messages": [
                        {
                            "role": "user",
                            "content": {
                                "type": "text",
                                "text": analisar_estacao_prompt(station_id=station_id, horas=horas),
                            },
                        }
                    ],
                },
            )

        if method in {"resources/list", "resources/templates/list"}:
            key = "resourceTemplates" if method == "resources/templates/list" else "resources"
            return _jsonrpc_result(message_id, {key: []})

        return _jsonrpc_error(message_id, -32601, f"Metodo nao suportado: {method}")
    except Exception as exc:
        return _jsonrpc_error(message_id, -32603, str(exc))


def run_stdio_server() -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
            response = _handle_stdio_message(message)
        except Exception as exc:
            response = _jsonrpc_error(None, -32700, str(exc))
        if response is None:
            continue
        sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
        sys.stdout.flush()


def main() -> None:
    transport = os.getenv("HERMES_MCP_TRANSPORT", "stdio")
    if transport == "stdio":
        run_stdio_server()
        return

    host = os.getenv("HERMES_MCP_HOST", "127.0.0.1")
    port = int(os.getenv("HERMES_MCP_PORT", "8000"))
    path = os.getenv("HERMES_MCP_PATH", "/mcp")
    mcp.run(transport=transport, host=host, port=port, path=path, show_banner=False, log_level="ERROR")


if __name__ == "__main__":
    main()
