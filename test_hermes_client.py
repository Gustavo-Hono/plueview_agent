import json

from hermes_core import build_station_diagnostic_sql, diagnose_payload, extract_payload


SAMPLE_SUPABASE_RESULT = [
    {
        "hermes_payload": {
            "stationId": 1,
            "hours": 24,
            "weather": [
                {
                    "measured_at": "2026-06-11T12:00:00+00:00",
                    "temperatura": 22.0,
                    "umidade": 72,
                    "quantidadeChuva": 0,
                    "velocidadeVento": 2.5,
                },
                {
                    "measured_at": "2026-06-11T13:00:00+00:00",
                    "temperatura": 23.0,
                    "umidade": 70,
                    "quantidadeChuva": 0,
                    "velocidadeVento": 2.7,
                },
                {
                    "measured_at": "2026-06-11T18:30:00+00:00",
                    "temperatura": 37.0,
                    "umidade": 31,
                    "quantidadeChuva": 26.4,
                    "velocidadeVento": 22.0,
                },
            ],
            "iot": [
                {
                    "measured_at": "2026-06-11T12:00:00+00:00",
                    "battery": 82,
                    "ConsumoTemperatura": 1.1,
                    "ConsumoUmidade": 1.0,
                    "ConsumoPluviometro": 1.2,
                },
                {
                    "measured_at": "2026-06-11T13:00:00+00:00",
                    "battery": 80,
                    "ConsumoTemperatura": 0,
                    "ConsumoUmidade": 1.0,
                    "ConsumoPluviometro": 1.2,
                },
                {
                    "measured_at": "2026-06-11T18:30:00+00:00",
                    "battery": 61,
                    "ConsumoTemperatura": 0,
                    "ConsumoUmidade": 1.0,
                    "ConsumoPluviometro": 1.2,
                },
            ],
        }
    }
]


def main() -> None:
    print("SQL:")
    print(build_station_diagnostic_sql(1, 24, 1000))

    payload = extract_payload(json.dumps(SAMPLE_SUPABASE_RESULT))
    print("\nDiagnostico:")
    print(diagnose_payload(payload, station_id=1, hours=24))


if __name__ == "__main__":
    main()
