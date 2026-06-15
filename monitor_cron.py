from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone

from hermes_core import (
    build_station_diagnostic_sql,
    diagnose_payload,
    execute_supabase_sql,
    extract_payload,
    latest_age_hours,
    payload_rows,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Monitor cron para estacoes PluView.")
    parser.add_argument("--station-id", type=int, default=1)
    parser.add_argument("--window-hours", type=int, default=6)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--stale-hours", type=float, default=2.5)
    parser.add_argument("--require-iot", action="store_true")
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    sql = build_station_diagnostic_sql(args.station_id, args.window_hours, args.limit)
    result = await execute_supabase_sql(sql)
    payload = extract_payload(result)

    weather = payload_rows(payload, "weather")
    iot = payload_rows(payload, "iot")
    weather_age = latest_age_hours(weather)
    iot_age = latest_age_hours(iot)
    diagnosis = diagnose_payload(payload, station_id=args.station_id, hours=args.window_hours)

    alerts: list[str] = []
    if not weather:
        alerts.append("sem leituras meteorologicas na janela monitorada")
    elif weather_age is not None and weather_age > args.stale_hours:
        alerts.append(f"ultima leitura meteorologica ha {weather_age:.1f}h")

    if args.require_iot:
        if not iot:
            alerts.append("sem leituras IoT/bateria na janela monitorada")
        elif iot_age is not None and iot_age > args.stale_hours:
            alerts.append(f"ultima leitura IoT/bateria ha {iot_age:.1f}h")

    if "Conclusao: requer verificacao operacional" in diagnosis:
        alerts.append("diagnostico requer verificacao operacional")
    elif "Conclusao: monitorar" in diagnosis:
        alerts.append("diagnostico recomenda monitoramento")

    status = "ALERT" if alerts else "OK"
    timestamp = datetime.now(timezone.utc).isoformat()
    print(f"[{timestamp}] station={args.station_id} status={status}")
    if alerts:
        print("Alertas:")
        for alert in alerts:
            print(f"- {alert}")
    print(diagnosis)

    return 2 if alerts else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
