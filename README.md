# PluView Agent

Python MCP reliability agent for diagnosing IoT weather stations using Supabase data.

The agent was built around a practical monitoring problem: weather stations can stop reporting, send delayed readings, produce invalid sensor values, or show operational anomalies that need to be summarized quickly. PluView Agent turns raw station telemetry into short diagnostic reports that can be used by operators or connected AI tools.

## What it demonstrates

- Python agent design around the Model Context Protocol (MCP)
- Read-only Supabase/Postgres diagnostic workflow
- Operational rules for IoT telemetry quality
- Sensor-health checks for missing, delayed, invalid, or suspicious readings
- Cron-based automation with optional Telegram notifications
- Clear separation between business rules and MCP adapter code

## Architecture

```text
ESP32 / MQTT
    -> PluView API
    -> Supabase / Postgres
    -> Supabase MCP
    -> PluView Agent MCP
    -> short station diagnostic
```

The Supabase MCP is responsible for database access. PluView Agent is responsible for domain rules: which tables to inspect, which SQL should be generated, how to detect anomalies, and how to format the diagnostic result.

## Current capabilities

- Query station readings by `stationId`
- Generate short diagnostics for a station and time window
- Detect missing or delayed weather readings
- Detect large telemetry gaps
- Detect missing weather fields
- Detect physically invalid values for temperature, humidity, and rainfall
- Detect simple weather events such as extreme temperature, heavy rainfall, strong wind, and abrupt jumps
- Evaluate battery status only when battery telemetry exists
- Mark battery as not evaluated when telemetry is unavailable
- Send scheduled diagnostic messages through Telegram when configured

## Not implemented yet

- Compare a station with nearby stations
- Detect isolated rain against neighboring stations
- Detect stuck sensors through repeated values over long periods
- Evaluate solar-panel or autonomy behavior
- Generate daily consolidated reports

## Repository structure

```text
hermes_core.py          business rules, SQL generation, Supabase MCP client, diagnostics
hermes_mcp.py           local MCP adapter
monitor_cron.py         scheduled monitoring entry point
mcp.example.json        example MCP client configuration
test_hermes_client.py   local test without Supabase access
VM_SETUP.md             deployment/setup notes
```

## MCP tools

- `sql_diagnostico_estacao`: generates a read-only SQL query for a station and time window
- `diagnosticar_resultado_supabase`: receives Supabase JSON output and returns a diagnostic
- `analisar_estacao_supabase`: attempts to call Supabase MCP directly and return the diagnostic in one step

## Status

Portfolio-ready reliability agent v1. The project is intentionally focused on operational diagnostics instead of generic chatbot behavior, which makes it a stronger signal for applied AI, IoT monitoring, and backend automation roles.