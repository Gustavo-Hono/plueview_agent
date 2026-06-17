from __future__ import annotations

import argparse
import asyncio
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import httpx

from hermes_core import (
    build_station_diagnostic_sql,
    diagnose_payload,
    execute_supabase_sql,
    extract_payload,
    latest_age_hours,
    payload_rows,
    sort_by_time,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Monitor cron para estacoes PluView.")
    parser.add_argument("--station-id", type=int, default=1)
    parser.add_argument("--window-hours", type=int, default=6)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--stale-hours", type=float, default=2.5)
    parser.add_argument("--require-iot", action="store_true")
    return parser.parse_args()


def load_hermes_keys() -> dict[str, str]:
    keys = {}
    # Procura no .env local e no global do Hermes
    paths = [
        Path(__file__).parent / ".env",
        Path.home() / ".hermes" / ".env"
    ]
    for path in paths:
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            keys[k.strip()] = v.strip().strip('"').strip("'")
            except Exception as e:
                print(f"Erro ao ler chaves de {path}: {e}")
    return keys


async def call_llm_for_diagnosis(prompt: str, keys: dict[str, str]) -> str:
    detected_keys = [k for k in keys.keys() if "KEY" in k or "TOKEN" in k]
    print(f"Chaves de API detectadas no ambiente: {detected_keys}")
    
    # 1. Se tiver NVIDIA_API_KEY
    if "NVIDIA_API_KEY" in keys:
        key = keys["NVIDIA_API_KEY"]
        url = "https://integrate.api.nvidia.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json"
        }
        body = {
            "model": "nvidia/llama-3.3-nemotron-super-49b-v1.5",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": 1000
        }
        print("Enviando requisição para Nvidia API...")
        try:
            async with httpx.AsyncClient() as client:
                res = await client.post(url, json=body, headers=headers, timeout=25)
                print(f"Resposta Nvidia: HTTP {res.status_code}")
                if res.status_code == 200:
                    data = res.json()
                    content = data.get("choices", [{}])[0].get("message", {}).get("content")
                    if content is None:
                        print(f"Aviso: Resposta vazia da Nvidia API. JSON retornado: {data}")
                        return ""
                    return str(content).strip()
                else:
                    print(f"Erro Nvidia API ({res.status_code}): {res.text}")
        except Exception as e:
            print(f"Erro ao chamar Nvidia: {e}")

    # 2. Se tiver GEMINI_API_KEY ou GOOGLE_API_KEY
    gemini_key = keys.get("GEMINI_API_KEY") or keys.get("GOOGLE_API_KEY")
    if gemini_key:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key}"
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2}
        }
        print("Enviando requisição para Gemini API...")
        try:
            async with httpx.AsyncClient() as client:
                res = await client.post(url, json=body, timeout=25)
                print(f"Resposta Gemini: HTTP {res.status_code}")
                if res.status_code == 200:
                    data = res.json()
                    candidates = data.get("candidates", [])
                    if not candidates:
                        print(f"Aviso: Nenhum candidato de resposta retornado pelo Gemini (pode ter sido bloqueado por segurança). JSON: {data}")
                        return ""
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if not parts:
                        print(f"Aviso: Resposta sem partes de texto. finishReason: {candidates[0].get('finishReason')}. JSON: {data}")
                        return ""
                    content = parts[0].get("text")
                    if content is None:
                        return ""
                    return str(content).strip()
                else:
                    print(f"Erro Gemini API ({res.status_code}): {res.text}")
        except Exception as e:
            print(f"Erro ao chamar Gemini: {e}")

    if not keys:
        print("Aviso: Nenhuma chave de API de IA (NVIDIA_API_KEY ou GEMINI_API_KEY) foi encontrada nos arquivos .env.")

    return ""


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


def build_notification_message(
    status: str,
    station_id: int,
    alerts: list[str],
    diagnosis: str,
    latest_weather: dict[str, Any] | None,
    ai_diagnosis: str | None = None,
) -> str:
    emoji = "🚨 [ALERT]" if alerts else "✅ [OK]"
    header = f"{emoji} Estação {station_id}"

    if latest_weather:
        temp = latest_weather.get("temperatura")
        umid = latest_weather.get("umidade")
        chuva = latest_weather.get("quantidadeChuva")

        temp_str = f"{temp:.1f}°C" if temp is not None else "N/A"
        umid_str = f"{umid:.1f}%" if umid is not None else "N/A"
        chuva_str = f"{chuva:.1f} mm" if chuva is not None else "N/A"

        val_block = f"🌡️ Temp: {temp_str} | 💧 Umid: {umid_str} | 🌧️ Chuva: {chuva_str}"
    else:
        val_block = "Nenhum dado meteorológico recente disponível."

    if alerts:
        body = "Alertas:\n" + "\n".join(f"- {alert}" for alert in alerts)
    else:
        body = "Sem alertas ativos."

    diag_section = ai_diagnosis if ai_diagnosis else diagnosis

    return f"{header}\n{val_block}\n\n{body}\n\n{diag_section}"


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

    ai_diagnosis = None
    if alerts:
        keys = load_hermes_keys()
        if keys:
            # Filtra os dados apenas para o essencial para economizar tokens
            clima_simplificado = [
                {
                    "data": r.get("measured_at") or r.get("dataMedicao"),
                    "t": r.get("temperatura"),
                    "u": r.get("umidade"),
                    "c": r.get("quantidadeChuva")
                }
                for r in weather[:6]
            ]
            iot_simplificado = [
                {
                    "data": r.get("measured_at") or r.get("time"),
                    "bateria": r.get("battery"),
                    "pluv": r.get("ConsumoPluviometro"),
                    "temp": r.get("ConsumoTemperatura"),
                    "umid": r.get("ConsumoUmidade")
                }
                for r in iot[:6]
            ]
            alerts_str = "\n".join(f"- {a}" for a in alerts)
            prompt = f"""Você é o Hermes, agente de diagnóstico inteligente das estações meteorológicas PluView.
Houve um alerta na Estação {args.station_id}.

Alertas identificados pelo sistema:
{alerts_str}

Dados de Clima (últimas leituras):
{json.dumps(clima_simplificado, indent=2)}

Dados de Telemetria/IoT (consumo dos sensores e bateria):
{json.dumps(iot_simplificado, indent=2)}

Por favor:
1. Analise se o problema parece ser um "Possível mau contato" (oscilações, falhas intermitentes) ou um "Possível defeito permanente no sensor/placa" (valores fixos absurdos, consumo zerado recorrente, falta de dados total).
2. Escreva um diagnóstico direto em português contendo recomendações curtas de verificação física para o operador da estação.
3. Seja breve, técnico e profissional (máximo 4-5 linhas).
"""
            print("Chamando IA do Hermes para gerar diagnóstico...")
            ai_res = await call_llm_for_diagnosis(prompt, keys)
            if ai_res and ai_res.strip().lower() not in ("none", "null", ""):
                ai_diagnosis = ai_res
                print(f"Diagnóstico da IA:\n{ai_diagnosis}")

    weather_sorted = sort_by_time(weather)
    latest_weather = weather_sorted[-1] if weather_sorted else None

    notification = build_notification_message(
        status, args.station_id, alerts, diagnosis, latest_weather, ai_diagnosis
    )
    send_telegram_message(notification)
    send_discord_message(notification)

    return 2 if alerts else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
