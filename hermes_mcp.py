from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

from fastmcp import FastMCP

from hermes_core import (
    DEFAULT_LIMIT,
    analyze_station,
    build_station_diagnostic_sql,
    diagnose_payload,
    extract_payload,
)


SERVER_NAME = "Hermes Supabase Diagnostics"
SERVER_VERSION = "0.1.0"

mcp = FastMCP(SERVER_NAME)


@mcp.tool
def sql_diagnostico_estacao(station_id: int = 1, horas: int = 24, limite: int = DEFAULT_LIMIT) -> str:
    """Gera o SQL read-only para buscar leituras da estacao no Supabase."""

    return build_station_diagnostic_sql(station_id, horas, limite)


@mcp.tool
def diagnosticar_resultado_supabase(resultado_json: str, station_id: int = 1, horas: int = 24) -> str:
    """Gera diagnostico curto a partir do JSON retornado pelo execute_sql do Supabase MCP."""

    payload = extract_payload(resultado_json)
    return diagnose_payload(payload, station_id=station_id, hours=horas)


@mcp.tool
async def analisar_estacao_supabase(station_id: int = 1, horas: int = 24, limite: int = DEFAULT_LIMIT) -> str:
    """Consulta o Supabase MCP em modo read-only e diagnostica a estacao."""

    return await analyze_station(station_id=station_id, hours=horas, limit=limite)


@mcp.prompt
def analisar_estacao_prompt(station_id: int = 1, horas: int = 24) -> str:
    return (
        "Use o MCP do Supabase em modo read-only para executar o SQL gerado por "
        f"`sql_diagnostico_estacao(station_id={station_id}, horas={horas})`. "
        "Depois envie o JSON retornado para `diagnosticar_resultado_supabase` e "
        "responda apenas com o diagnostico curto."
    )


def tool_definitions() -> list[dict[str, Any]]:
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


def prompt_definitions() -> list[dict[str, Any]]:
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


def call_tool_sync(name: str, arguments: dict[str, Any]) -> str:
    if name == "sql_diagnostico_estacao":
        return build_station_diagnostic_sql(
            arguments.get("station_id", 1),
            arguments.get("horas", 24),
            arguments.get("limite", DEFAULT_LIMIT),
        )

    if name == "diagnosticar_resultado_supabase":
        raw_result = arguments.get("resultado_json")
        if not isinstance(raw_result, str):
            raw_result = json.dumps(raw_result)
        payload = extract_payload(raw_result)
        return diagnose_payload(
            payload,
            station_id=arguments.get("station_id", 1),
            hours=arguments.get("horas", 24),
        )

    if name == "analisar_estacao_supabase":
        return asyncio.run(
            analyze_station(
                station_id=arguments.get("station_id", 1),
                hours=arguments.get("horas", 24),
                limit=arguments.get("limite", DEFAULT_LIMIT),
            )
        )

    raise ValueError(f"Ferramenta desconhecida: {name}")


def jsonrpc_result(message_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def jsonrpc_error(message_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}}


def handle_stdio_message(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    message_id = message.get("id")
    params = message.get("params") if isinstance(message.get("params"), dict) else {}

    if message_id is None and method and method.startswith("notifications/"):
        return None

    try:
        if method == "initialize":
            return jsonrpc_result(
                message_id,
                {
                    "protocolVersion": params.get("protocolVersion", "2025-11-25"),
                    "capabilities": {
                        "tools": {"listChanged": False},
                        "prompts": {"listChanged": False},
                    },
                    "serverInfo": {
                        "name": "hermes-supabase-diagnostics",
                        "version": SERVER_VERSION,
                    },
                },
            )

        if method == "ping":
            return jsonrpc_result(message_id, {})

        if method == "tools/list":
            return jsonrpc_result(message_id, {"tools": tool_definitions()})

        if method == "tools/call":
            text = call_tool_sync(str(params.get("name", "")), params.get("arguments") or {})
            return jsonrpc_result(
                message_id,
                {
                    "content": [{"type": "text", "text": text}],
                    "isError": False,
                },
            )

        if method == "prompts/list":
            return jsonrpc_result(message_id, {"prompts": prompt_definitions()})

        if method == "prompts/get":
            arguments = params.get("arguments") or {}
            station_id = arguments.get("station_id", 1)
            horas = arguments.get("horas", 24)
            return jsonrpc_result(
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
            return jsonrpc_result(message_id, {key: []})

        return jsonrpc_error(message_id, -32601, f"Metodo nao suportado: {method}")
    except Exception as exc:
        return jsonrpc_error(message_id, -32603, str(exc))


def run_stdio_server() -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
            response = handle_stdio_message(message)
        except Exception as exc:
            response = jsonrpc_error(None, -32700, str(exc))
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
