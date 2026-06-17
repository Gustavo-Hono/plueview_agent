from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from dotenv import load_dotenv
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport


DEFAULT_SUPABASE_MCP_URL = "https://mcp.supabase.com/mcp"
DEFAULT_LIMIT = 1000
MAX_STATION_ID = 1_000_000
MAX_WINDOW_HOURS = 24 * 31
MAX_LIMIT = 10_000

SENSOR_FIELDS = {
    "ConsumoPluviometro": "pluviometro",
    "ConsumoVelocidadeVento": "velocidade do vento",
    "ConsumoDirecaoVento": "direcao do vento",
    "ConsumoTemperatura": "temperatura",
    "ConsumoUmidade": "umidade",
}

logging.getLogger("mcp.client.streamable_http").setLevel(logging.ERROR)


def load_env() -> None:
    load_dotenv(Path(__file__).with_name(".env"))


load_env()


def positive_int(name: str, value: int, *, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} precisa ser inteiro") from exc

    if parsed <= 0 or parsed > maximum:
        raise ValueError(f"{name} precisa estar entre 1 e {maximum}")
    return parsed


def build_supabase_mcp_url() -> str:
    configured = os.getenv("SUPABASE_MCP_URL", "").strip()
    if configured:
        return merge_query(configured, {"read_only": "true", "features": "database"})

    raw_project_ref = os.getenv("SUPABASE_PROJECT_REF", "").strip()
    if is_url(raw_project_ref):
        return merge_query(raw_project_ref, {"read_only": "true", "features": "database"})

    project_ref = configured_project_ref()
    params: dict[str, str] = {"read_only": "true", "features": "database"}
    if project_ref:
        params["project_ref"] = project_ref

    return merge_query(DEFAULT_SUPABASE_MCP_URL, params)


def is_url(value: str) -> bool:
    parsed = urlparse(value)
    return bool(parsed.scheme and parsed.netloc)


def project_ref_from_value(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if not is_url(value):
        return value
    parsed = urlparse(value)
    return (parse_qs(parsed.query).get("project_ref") or [""])[0].strip()


def configured_project_ref() -> str:
    configured = os.getenv("SUPABASE_PROJECT_ID", "") or os.getenv("SUPABASE_PROJECT_REF", "")
    return project_ref_from_value(configured)


def merge_query(url: str, params: dict[str, str]) -> str:
    parsed = urlparse(url)
    current = parse_qs(parsed.query)
    for key, value in params.items():
        current.setdefault(key, [value])

    query = urlencode({key: values[-1] for key, values in current.items()})
    return urlunparse(parsed._replace(query=query))


def supabase_headers() -> dict[str, str]:
    token = os.getenv("SUPABASE_ACCESS_TOKEN", "").strip()
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def build_station_diagnostic_sql(station_id: int, hours: int, limit: int = DEFAULT_LIMIT) -> str:
    station_id = positive_int("station_id", station_id, maximum=MAX_STATION_ID)
    hours = positive_int("horas", hours, maximum=MAX_WINDOW_HOURS)
    limit = positive_int("limite", limit, maximum=MAX_LIMIT)

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
      AND "dataMedicao" >= now() - interval '{hours} hours'
    ORDER BY "dataMedicao" DESC
    LIMIT {limit}
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
      AND time >= now() - interval '{hours} hours'
    ORDER BY time DESC
    LIMIT {limit}
  ) recent_iot
  ORDER BY measured_at ASC
)
SELECT jsonb_build_object(
  'stationId', {station_id},
  'hours', {hours},
  'generatedAt', now(),
  'weather', COALESCE((SELECT jsonb_agg(to_jsonb(weather)) FROM weather), '[]'::jsonb),
  'iot', COALESCE((SELECT jsonb_agg(to_jsonb(iot)) FROM iot), '[]'::jsonb)
) AS hermes_payload;
""".strip()


def tool_schema_props(tool: Any) -> dict[str, Any]:
    schema = getattr(tool, "inputSchema", None) or getattr(tool, "input_schema", None)
    if schema is None and hasattr(tool, "model_dump"):
        schema = tool.model_dump().get("inputSchema")
    if not isinstance(schema, dict):
        return {}
    props = schema.get("properties")
    return props if isinstance(props, dict) else {}


def select_execute_sql_tool(tools: list[Any]) -> tuple[str, dict[str, Any]]:
    for tool in tools:
        name = getattr(tool, "name", "")
        if name == "execute_sql" or name.endswith("_execute_sql"):
            return name, tool_schema_props(tool)
    available = ", ".join(getattr(tool, "name", "?") for tool in tools)
    raise RuntimeError(f"Ferramenta execute_sql nao encontrada no Supabase MCP. Disponiveis: {available}")


def execute_sql_arguments(props: dict[str, Any], sql: str) -> dict[str, Any]:
    args: dict[str, Any] = {}

    if "query" in props:
        args["query"] = sql
    elif "sql" in props:
        args["sql"] = sql
    else:
        args["query"] = sql

    project_id = configured_project_ref()
    if project_id:
        for key in ("project_id", "project_ref", "projectId"):
            if key in props:
                args[key] = project_id
                break

    return args


async def execute_supabase_sql(sql: str) -> Any:
    transport = StreamableHttpTransport(
        build_supabase_mcp_url(),
        headers=supabase_headers(),
        auth=os.getenv("SUPABASE_MCP_AUTH") or None,
    )
    async with Client(transport, timeout=60) as client:
        tools = await client.list_tools()
        tool_name, props = select_execute_sql_tool(list(tools))
        result = await client.call_tool(tool_name, execute_sql_arguments(props, sql), timeout=60)
    return result_to_python(result)


async def analyze_station(station_id: int = 1, hours: int = 24, limit: int = DEFAULT_LIMIT) -> str:
    sql = build_station_diagnostic_sql(station_id, hours, limit)
    result = await execute_supabase_sql(sql)
    payload = extract_payload(result)
    return diagnose_payload(payload, station_id=station_id, hours=hours)


def result_to_python(result: Any) -> Any:
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

    return parse_jsonish_text("\n".join(texts))


def parse_jsonish_text(text: str) -> Any:
    stripped = text.strip()
    for candidate in (stripped, first_json_block(stripped)):
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return {"text": text}


def first_json_block(text: str) -> str | None:
    match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
    return match.group(1) if match else None


def extract_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = parse_jsonish_text(value)

    if isinstance(value, dict):
        if "hermes_payload" in value and isinstance(value["hermes_payload"], dict):
            return value["hermes_payload"]
        if "weather" in value or "iot" in value:
            return value
        if "data" in value:
            return extract_payload(value["data"])
        if "result" in value:
            return extract_payload(value["result"])
        if "rows" in value:
            return extract_payload(value["rows"])

    if isinstance(value, list):
        if not value:
            return {"weather": [], "iot": []}
        if len(value) == 1 and isinstance(value[0], dict):
            return extract_payload(value[0])
        return {"weather": value, "iot": []}

    raise ValueError("Nao consegui encontrar hermes_payload/weather/iot no resultado informado.")


def payload_rows(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = payload.get(key) or []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def parse_time(value: Any) -> datetime | None:
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


def row_timestamp(row: dict[str, Any]) -> datetime | None:
    for key in ("measured_at", "dataMedicao", "time", "timestamp"):
        parsed = parse_time(row.get(key))
        if parsed:
            return parsed
    return None


def numeric_value(row: dict[str, Any], key: str) -> float | None:
    value = row.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def sort_by_time(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    earliest = datetime.min.replace(tzinfo=timezone.utc)
    return sorted(rows, key=lambda row: row_timestamp(row) or earliest)


def hours_between(start: datetime, end: datetime) -> float:
    return max((end - start).total_seconds() / 3600, 0.0)


def format_hours(hours: float) -> str:
    if hours < 1:
        return f"{round(hours * 60)}min"
    return f"{hours:.1f}h"


def max_gap_hours(rows: list[dict[str, Any]]) -> float:
    times = [row_timestamp(row) for row in rows]
    times = sorted(time for time in times if time is not None)
    if len(times) < 2:
        return 0.0
    return max(hours_between(prev, curr) for prev, curr in zip(times, times[1:]))


def detect_sensor_issues(weather: list[dict[str, Any]], iot: list[dict[str, Any]]) -> list[str]:
    issues: list[str] = []

    if not weather:
        issues.append("sem leituras meteorologicas no periodo")
    else:
        gap = max_gap_hours(weather)
        if gap >= 3:
            issues.append(f"lacuna de {format_hours(gap)} entre leituras meteorologicas (Possível mau contato ou perda de sinal)")

        total = len(weather)
        missing_temp = sum(1 for r in weather if numeric_value(r, "temperatura") is None)
        missing_hum = sum(1 for r in weather if numeric_value(r, "umidade") is None)
        missing_rain = sum(1 for r in weather if numeric_value(r, "quantidadeChuva") is None)

        if missing_temp == total and total > 0:
            issues.append("sensor de temperatura sem dados (Possível defeito ou desconectado)")
        elif 0 < missing_temp < total:
            issues.append(f"temperatura ausente em {missing_temp}/{total} leituras (Possível mau contato)")

        if missing_hum == total and total > 0:
            issues.append("sensor de umidade sem dados (Possível defeito ou desconectado)")
        elif 0 < missing_hum < total:
            issues.append(f"umidade ausente em {missing_hum}/{total} leituras (Possível mau contato)")

        if missing_rain == total and total > 0:
            issues.append("pluviometro sem dados (Possível defeito ou desconectado)")
        elif 0 < missing_rain < total:
            issues.append(f"chuva ausente em {missing_rain}/{total} leituras (Possível mau contato)")

        invalid_temp = [
            row
            for row in weather
            if (temp := numeric_value(row, "temperatura")) is not None and (temp < -20 or temp > 60)
        ]
        invalid_humidity = [
            row
            for row in weather
            if (humidity := numeric_value(row, "umidade")) is not None and not 0 <= humidity <= 100
        ]
        invalid_rain = [
            row
            for row in weather
            if (rain := numeric_value(row, "quantidadeChuva")) is not None and rain < 0
        ]
        if invalid_temp:
            issues.append(f"{len(invalid_temp)} leituras com temperatura fora da faixa (Possível defeito no sensor)")
        if invalid_humidity:
            issues.append(f"{len(invalid_humidity)} leituras com umidade fora de 0-100% (Possível defeito no sensor)")
        if invalid_rain:
            issues.append(f"{len(invalid_rain)} leituras de chuva negativas (Possível defeito no sensor)")

    for field, label in SENSOR_FIELDS.items():
        present = [row for row in iot if row.get(field) is not None]
        if not present:
            continue
        zero_or_negative = [row for row in present if (numeric_value(row, field) or 0) <= 0]
        if zero_or_negative:
            total_pres = len(present)
            total_zero = len(zero_or_negative)
            if total_zero >= max(2, total_pres // 3):
                if total_zero == total_pres:
                    issues.append(f"consumo zerado permanente no sensor de {label} (Possível defeito ou desconectado)")
                else:
                    issues.append(f"consumo zerado intermitente em {total_zero}/{total_pres} leituras no sensor de {label} (Possível mau contato)")

    return issues


def detect_battery_issues(iot: list[dict[str, Any]]) -> list[str]:
    battery_rows = [row for row in sort_by_time(iot) if numeric_value(row, "battery") is not None]
    if not battery_rows:
        return ["nao avaliada (sem telemetria de bateria)"]

    values = [numeric_value(row, "battery") for row in battery_rows]
    values = [value for value in values if value is not None]
    latest = values[-1]
    first = values[0]
    minimum = min(values)
    maximum = max(values)
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
    if minimum < 0 or maximum > 100:
        issues.append("valor de bateria fora de 0-100%")

    return issues or [f"estavel, ultima leitura {latest:.0f}%"]


def detect_weather_issues(weather: list[dict[str, Any]]) -> list[str]:
    rows = sort_by_time(weather)
    if not rows:
        return ["sem base meteorologica para avaliar clima"]

    issues: list[str] = []
    temps = [value for row in rows if (value := numeric_value(row, "temperatura")) is not None]
    humidities = [value for row in rows if (value := numeric_value(row, "umidade")) is not None]
    rains = [value for row in rows if (value := numeric_value(row, "quantidadeChuva")) is not None]
    winds = [value for row in rows if (value := numeric_value(row, "velocidadeVento")) is not None]

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
        previous_time = row_timestamp(previous)
        current_time = row_timestamp(current)
        if not previous_time or not current_time:
            continue
        delta_hours = max(hours_between(previous_time, current_time), 0.1)

        previous_temp = numeric_value(previous, "temperatura")
        current_temp = numeric_value(current, "temperatura")
        if previous_temp is not None and current_temp is not None:
            delta_temp = abs(current_temp - previous_temp)
            if delta_temp >= 8 and delta_temp / delta_hours >= 4:
                issues.append(f"salto termico de {delta_temp:.1f} C em {format_hours(delta_hours)}")
                break

    for previous, current in zip(rows, rows[1:]):
        previous_time = row_timestamp(previous)
        current_time = row_timestamp(current)
        if not previous_time or not current_time:
            continue
        delta_hours = max(hours_between(previous_time, current_time), 0.1)

        previous_humidity = numeric_value(previous, "umidade")
        current_humidity = numeric_value(current, "umidade")
        if previous_humidity is not None and current_humidity is not None:
            delta_humidity = abs(current_humidity - previous_humidity)
            if delta_humidity >= 35 and delta_hours <= 2:
                issues.append(f"variacao brusca de umidade ({delta_humidity:.0f} pontos em {format_hours(delta_hours)})")
                break

    return issues or ["sem evento climatico estranho nos limiares configurados"]


def latest_age_hours(rows: list[dict[str, Any]]) -> float | None:
    times = [row_timestamp(row) for row in rows]
    times = [time for time in times if time is not None]
    if not times:
        return None
    return hours_between(max(times), datetime.now(timezone.utc))


def compact_issues(items: list[str], *, limit: int = 2) -> str:
    if not items:
        return "OK"
    selected = items[:limit]
    remainder = len(items) - len(selected)
    suffix = f"; +{remainder} alerta(s)" if remainder else ""
    return "; ".join(selected) + suffix


def diagnose_payload(payload: dict[str, Any], station_id: int | None = None, hours: int | None = None) -> str:
    weather = sort_by_time(payload_rows(payload, "weather"))
    iot = sort_by_time(payload_rows(payload, "iot"))

    station = station_id or payload.get("stationId") or payload.get("station_id") or "?"
    window = hours or payload.get("hours") or payload.get("horas") or "?"

    sensor_issues = detect_sensor_issues(weather, iot)
    battery_issues = detect_battery_issues(iot)
    climate_issues = detect_weather_issues(weather)

    weather_age = latest_age_hours(weather)
    iot_age = latest_age_hours(iot)
    freshness: list[str] = []
    if weather_age is not None and weather_age >= 2:
        freshness.append(f"meteo ha {format_hours(weather_age)}")
    if iot_age is not None and iot_age >= 2:
        freshness.append(f"IoT ha {format_hours(iot_age)}")

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
        f"Sensor: {compact_issues(sensor_issues)}",
        f"Bateria: {compact_issues(battery_issues)}",
        f"Clima: {compact_issues(climate_issues)}",
        f"Conclusao: {conclusion}.",
    ]
    return "\n".join(lines)
