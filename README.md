# PluView Telemetry Diagnostics

Python diagnostic tool for checking whether PluView rain-gauge/weather-station telemetry is arriving as expected.

This repository is not positioned as a general AI agent. Its core value is a set of rule-based heuristics for operational monitoring: detecting missing signals, delayed readings, gaps in telemetry, invalid sensor values, and simple anomalies that suggest the pluviometer or weather station may not be reporting correctly.

## What it demonstrates

- Python-based telemetry diagnostics
- Rule-based health checks for IoT/weather-station data
- Read-only Supabase/Postgres inspection workflow
- Operational heuristics for missing, delayed, invalid, or suspicious readings
- Scheduled monitoring entry point with optional notification support
- Separation between diagnostic rules and integration/adaptor code

## Diagnostic flow

```text
ESP32 / MQTT
    -> PluView API
    -> Supabase / Postgres
    -> diagnostic scripts
    -> short telemetry health report
```

The diagnostic logic focuses on answering practical questions such as:

- Is the pluviometer still sending data?
- When was the last valid reading?
- Are there large gaps between readings?
- Are expected telemetry fields missing?
- Are temperature, humidity, rainfall, or wind values physically suspicious?
- Is the station showing signs of an operational issue?

## Current capabilities

- Filter station readings by `stationId`
- Generate short diagnostics for a station and time window
- Detect missing or delayed weather readings
- Detect large telemetry gaps
- Detect missing weather fields
- Detect physically invalid values for temperature, humidity, and rainfall
- Detect simple weather events such as extreme temperature, heavy rainfall, strong wind, and abrupt jumps
- Evaluate battery status only when battery telemetry exists
- Mark battery as not evaluated when telemetry is unavailable
- Send scheduled diagnostic messages when notification credentials are configured

## Not implemented yet

- Compare a station with nearby stations
- Detect isolated rain by comparing neighboring stations
- Detect stuck sensors through repeated values over long periods
- Evaluate solar-panel or autonomy behavior
- Generate daily consolidated reports

## Repository structure

```text
hermes_core.py          diagnostic rules, SQL generation, telemetry evaluation
hermes_mcp.py           optional MCP adapter around the diagnostic logic
monitor_cron.py         scheduled monitoring entry point
test_hermes_client.py   local test without Supabase access
mcp.example.json        example MCP configuration, if used
VM_SETUP.md             deployment/setup notes
```

## Status

Portfolio-ready diagnostic utility v1. The project is strongest as a signal for backend automation, IoT telemetry checks, and practical reliability tooling, not as a chatbot or autonomous agent.