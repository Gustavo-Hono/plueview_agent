# Setup na VM

Este guia instala o MCP Hermes/PluView em uma VM Linux e deixa pronto para conectar no cliente Hermes.

Os comandos abaixo assumem Ubuntu/Debian e instalacao em `/root/plueview_agent`.

## 1. Entrar na VM

```bash
ssh root@IP_DA_VM
```

Se voce entra com outro usuario, vire root:

```bash
sudo -i
```

## 2. Instalar pacotes do sistema

```bash
apt update
apt install -y git python3 python3-venv python3-pip ca-certificates curl
```

Verifique:

```bash
python3 --version
git --version
```

## 3. Clonar o MCP Hermes/PluView

```bash
cd /root
git clone https://github.com/Gustavo-Hono/plueview_agent.git
cd /root/plueview_agent
```

## 4. Criar ambiente Python

```bash
python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## 5. Criar `.env`

```bash
cat > /root/plueview_agent/.env <<'EOF'
SUPABASE_PROJECT_REF=wlwaysvyyvrvfdngnaej

# Opcional: use se o cliente Hermes nao suportar login/OAuth do Supabase MCP
# ou se quiser usar a ferramenta one-shot analisar_estacao_supabase.
SUPABASE_ACCESS_TOKEN=COLE_SEU_ACCESS_TOKEN_AQUI
EOF

chmod 600 /root/plueview_agent/.env
```

Se voce for usar OAuth/login do Supabase MCP direto pelo cliente Hermes, o token pode ficar comentado ou ausente.

## 6. Testar sintaxe

```bash
cd /root/plueview_agent
venv/bin/python -m py_compile hermes_mcp.py test_hermes_client.py
```

## 7. Testar logica local sem Supabase

```bash
cd /root/plueview_agent
venv/bin/python test_hermes_client.py
```

Esse comando deve imprimir um SQL e um diagnostico de exemplo.

## 8. Testar MCP via stdio

```bash
cd /root/plueview_agent
venv/bin/python - <<'PY'
import asyncio
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

async def main():
    transport = StdioTransport(
        "/root/plueview_agent/venv/bin/python",
        ["/root/plueview_agent/hermes_mcp.py"],
        keep_alive=False,
    )
    async with Client(transport, timeout=10, init_timeout=10) as client:
        tools = await client.list_tools()
        print([tool.name for tool in tools])
        result = await client.call_tool(
            "sql_diagnostico_estacao",
            {"station_id": 1, "horas": 24},
        )
        print(result.content[0].text.splitlines()[0])

asyncio.run(main())
PY
```

Saida esperada:

```text
['sql_diagnostico_estacao', 'diagnosticar_resultado_supabase', 'analisar_estacao_supabase']
WITH weather AS (
```

## 9. Testar Supabase real pelo Hermes MCP

Este teste usa `SUPABASE_ACCESS_TOKEN` do `.env`.

```bash
cd /root/plueview_agent
venv/bin/python - <<'PY'
import asyncio
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

async def main():
    transport = StdioTransport(
        "/root/plueview_agent/venv/bin/python",
        ["/root/plueview_agent/hermes_mcp.py"],
        keep_alive=False,
    )
    async with Client(transport, timeout=120, init_timeout=10) as client:
        result = await client.call_tool(
            "analisar_estacao_supabase",
            {"station_id": 1, "horas": 24, "limite": 100},
            timeout=120,
        )
        print(result.content[0].text)

asyncio.run(main())
PY
```

## 10. Configurar no cliente Hermes

No Hermes, adicione estes MCP servers.

Modo recomendado, com Supabase MCP separado:

```json
{
  "mcpServers": {
    "supabase": {
      "type": "http",
      "url": "https://mcp.supabase.com/mcp?project_ref=wlwaysvyyvrvfdngnaej&read_only=true&features=database"
    },
    "hermes": {
      "command": "/root/plueview_agent/venv/bin/python",
      "args": ["/root/plueview_agent/hermes_mcp.py"]
    }
  }
}
```

Se o Hermes nao suportar OAuth/login do Supabase MCP, use header manual:

```json
{
  "mcpServers": {
    "supabase": {
      "type": "http",
      "url": "https://mcp.supabase.com/mcp?project_ref=wlwaysvyyvrvfdngnaej&read_only=true&features=database",
      "headers": {
        "Authorization": "Bearer ${SUPABASE_ACCESS_TOKEN}"
      }
    },
    "hermes": {
      "command": "/root/plueview_agent/venv/bin/python",
      "args": ["/root/plueview_agent/hermes_mcp.py"]
    }
  }
}
```

Alternativa: usar apenas o MCP local `hermes` e chamar `analisar_estacao_supabase`.
Nesse caso, o `.env` precisa ter `SUPABASE_ACCESS_TOKEN`.

## 11. Prompt de teste no Hermes

```text
Analise a estacao 1 nas ultimas 24 horas.
Use o Supabase MCP em modo read-only para ler weather_data e DataPlueView.
Veja se houve falha de sensor, queda de bateria ou evento climatico estranho.
Gere um diagnostico curto.
```

## 12. Atualizar na VM

```bash
cd /root/plueview_agent
git pull
source venv/bin/activate
pip install -r requirements.txt
```

## Troubleshooting

Se o MCP local nao abrir:

```bash
/root/plueview_agent/venv/bin/python /root/plueview_agent/hermes_mcp.py
```

Ele deve ficar parado aguardando mensagens MCP via stdio.

Se o Supabase nao autenticar:

- confira `SUPABASE_PROJECT_REF`;
- confira se a URL tem `read_only=true`;
- confira se o token esta correto, caso esteja usando PAT;
- prefira OAuth/login do Supabase MCP quando o cliente Hermes suportar.
