from __future__ import annotations

import argparse
import asyncio
import json
import os
import urllib.parse
import urllib.request
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


def send_telegram_message(message: str) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": message}).encode("utf-8")
    try:
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=10) as response:
            response.read()
    except Exception as exc:
        print(f"Erro ao enviar mensagem para o Telegram: {exc}")


def send_discord_message(message: str) -> None:
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook_url:
        return

    payload = json.dumps({"content": message}).encode("utf-8")
    headers = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
    try:
        req = urllib.request.Request(webhook_url, data=payload, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=10) as response:
            response.read()
    except Exception as exc:
        print(f"Erro ao enviar mensagem para o Discord: {exc}")


def build_notification_message(status: str, station_id: int, alerts: list[str], diagnosis: str) -> str:
    header = f"[{status}] Estacao {station_id}"
    if alerts:
        body = "Alertas:\n" + "\n".join(f"- {alert}" for alert in alerts)
    else:
        body = "Sem alertas ativos."
    return f"{header}\n\n{body}\n\n{diagnosis}"


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

    notification = build_notification_message(status, args.station_id, alerts, diagnosis)
    send_telegram_message(notification)
    send_discord_message(notification)

    return 2 if alerts else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
