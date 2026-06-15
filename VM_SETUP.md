# Setup na VM Google Cloud

Este guia instala o MCP Hermes/PluView em uma VM do Google Compute Engine e deixa o servidor pronto para ser usado por um cliente Hermes/MCP.

Ele assume uma VM Ubuntu ou Debian. Se a imagem for Container-Optimized OS, RHEL, Rocky ou outra distro sem `apt`, os comandos de instalacao de pacotes mudam.

## Preciso entrar como root?

Nao necessariamente.

Em VM da GCloud, o padrao e entrar como usuario comum pelo `gcloud compute ssh` e usar `sudo` quando precisar instalar pacotes ou escrever em diretorios do sistema.

Recomendado:

- entrar como usuario normal;
- instalar o agent em `/opt/plueview_agent`;
- dar permissao desse diretorio para o seu usuario;
- apontar o Hermes para `/opt/plueview_agent/venv/bin/python` e `/opt/plueview_agent/hermes_mcp.py`.

Use `/root/plueview_agent` somente se o Hermes tambem for rodar como root. Se o Hermes rodar como outro usuario, ele pode nao conseguir ler arquivos dentro de `/root`.

## 1. Entrar na VM

Pelo seu computador local:

```bash
gcloud compute ssh NOME_DA_VM --zone ZONA_DA_VM
```

Exemplo:

```bash
gcloud compute ssh plueview-agent --zone southamerica-east1-a
```

Depois de entrar, confirme o usuario:

```bash
whoami
pwd
```

## 2. Instalar pacotes do sistema

Na VM:

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip ca-certificates curl
```

Verifique:

```bash
python3 --version
git --version
```

## 3. Clonar o MCP Hermes/PluView

Crie o diretorio em `/opt` e deixe seu usuario como dono:

```bash
sudo mkdir -p /opt/plueview_agent
sudo chown -R "$USER:$USER" /opt/plueview_agent
```

Clone o repositorio:

```bash
git clone https://github.com/Gustavo-Hono/plueview_agent.git /opt/plueview_agent
cd /opt/plueview_agent
```

Se o diretorio ja existir com arquivos, use:

```bash
cd /opt/plueview_agent
git pull
```

## 4. Criar ambiente Python

```bash
cd /opt/plueview_agent
python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## 5. Criar `.env`

Se voce vai usar a ferramenta one-shot `analisar_estacao_supabase`, coloque o access token.

```bash
cat > /opt/plueview_agent/.env <<'EOF'
SUPABASE_PROJECT_REF=wlwaysvyyvrvfdngnaej
SUPABASE_ACCESS_TOKEN=COLE_SEU_ACCESS_TOKEN_AQUI
EOF

chmod 600 /opt/plueview_agent/.env
```

Se voce vai usar o Supabase MCP separado com OAuth/login pelo cliente Hermes, o token pode ficar ausente:

```bash
cat > /opt/plueview_agent/.env <<'EOF'
SUPABASE_PROJECT_REF=wlwaysvyyvrvfdngnaej
EOF

chmod 600 /opt/plueview_agent/.env
```

## 6. Testar sintaxe

```bash
cd /opt/plueview_agent
venv/bin/python -m py_compile hermes_mcp.py test_hermes_client.py
```

## 7. Testar logica local sem Supabase

```bash
cd /opt/plueview_agent
venv/bin/python test_hermes_client.py
```

Esse comando deve imprimir um SQL e um diagnostico de exemplo.

## 8. Testar MCP via stdio

```bash
cd /opt/plueview_agent
venv/bin/python - <<'PY'
import asyncio
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

async def main():
    transport = StdioTransport(
        "/opt/plueview_agent/venv/bin/python",
        ["/opt/plueview_agent/hermes_mcp.py"],
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
cd /opt/plueview_agent
venv/bin/python - <<'PY'
import asyncio
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

async def main():
    transport = StdioTransport(
        "/opt/plueview_agent/venv/bin/python",
        ["/opt/plueview_agent/hermes_mcp.py"],
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

Se voce nao colocou `SUPABASE_ACCESS_TOKEN`, pule este teste e teste pelo cliente Hermes usando Supabase MCP separado.

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
      "command": "/opt/plueview_agent/venv/bin/python",
      "args": ["/opt/plueview_agent/hermes_mcp.py"]
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
      "command": "/opt/plueview_agent/venv/bin/python",
      "args": ["/opt/plueview_agent/hermes_mcp.py"]
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
cd /opt/plueview_agent
git pull
source venv/bin/activate
pip install -r requirements.txt
```

## Alternativa: instalar em `/root`

Use esta opcao apenas se voce sabe que o Hermes vai rodar como root:

```bash
sudo -i
cd /root
git clone https://github.com/Gustavo-Hono/plueview_agent.git
cd /root/plueview_agent
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Nesse caso, a configuracao MCP precisa usar:

```json
{
  "mcpServers": {
    "hermes": {
      "command": "/root/plueview_agent/venv/bin/python",
      "args": ["/root/plueview_agent/hermes_mcp.py"]
    }
  }
}
```

## Troubleshooting

Se o MCP local nao abrir:

```bash
/opt/plueview_agent/venv/bin/python /opt/plueview_agent/hermes_mcp.py
```

Ele deve ficar parado aguardando mensagens MCP via stdio.

Se receber `Permission denied`, verifique o dono do diretorio:

```bash
sudo chown -R "$USER:$USER" /opt/plueview_agent
```

Se o Supabase nao autenticar:

- confira `SUPABASE_PROJECT_REF`;
- confira se a URL tem `read_only=true`;
- confira se o token esta correto, caso esteja usando PAT;
- prefira OAuth/login do Supabase MCP quando o cliente Hermes suportar.
