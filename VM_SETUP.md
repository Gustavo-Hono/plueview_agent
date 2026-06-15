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

O Hermes em si nao precisa ser instalado na raiz da VM. O que precisa ficar em um caminho estavel e o agent MCP, por isso este guia usa `/opt/plueview_agent`.

## Como o monitoramento funciona

Existem dois modos:

- Por demanda: o usuario pergunta no Hermes, e o Hermes chama o MCP local.
- Automatico: o cron da VM roda `monitor_cron.py` periodicamente e grava diagnosticos em log.

Como o ESP32 envia dados meteorologicos a cada 1 hora, a recomendacao e:

- rodar o cron a cada 1 hora;
- considerar alerta quando a ultima leitura tiver mais de `2.5h`;
- analisar uma janela de `6h`.

Isso detecta falha depois de aproximadamente 2 envios perdidos, mas evita perder tempo por desalinhamento do cron. Se voce rodar literalmente a cada 2 horas, uma falha logo depois do cron pode demorar perto de 4 horas para aparecer.

## Organizacao do codigo

- `hermes_core.py`: SQL, integracao com Supabase MCP, parsing do resultado e regras de diagnostico.
- `hermes_mcp.py`: servidor/adaptador MCP para o Hermes.
- `monitor_cron.py`: script chamado pelo cron para monitorar sem interacao humana.
- `test_hermes_client.py`: smoke test local sem depender do Supabase.

Para manutencao, altere limiares e regras de diagnostico no `hermes_core.py`. O `hermes_mcp.py` deve continuar simples, apenas expondo as ferramentas MCP.

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

Use `SUPABASE_PROJECT_REF` para o ref puro do projeto. Se preferir informar a URL completa, use:

```bash
SUPABASE_MCP_URL=https://mcp.supabase.com/mcp?project_ref=wlwaysvyyvrvfdngnaej&read_only=true&features=database
```

## 6. Testar sintaxe

```bash
cd /opt/plueview_agent
venv/bin/python -m py_compile hermes_core.py hermes_mcp.py monitor_cron.py test_hermes_client.py
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

## 10. Testar monitor automatico local

Este teste simula o que o cron vai executar. Ele precisa de `SUPABASE_ACCESS_TOKEN` no `.env`, porque cron nao consegue fazer login/OAuth interativo.

```bash
cd /opt/plueview_agent
venv/bin/python monitor_cron.py --station-id 1 --window-hours 6 --stale-hours 2.5
```

Saida esperada quando estiver tudo normal:

```text
[2026-..] station=1 status=OK
Diagnostico curto - estacao 1 (ultimas 6h)
...
```

Se houver atraso, falta de dados ou diagnostico suspeito, a saida tera `status=ALERT` e o processo sai com codigo `2`.

## 11. Configurar cron automatico

Crie um diretorio de logs:

```bash
mkdir -p /opt/plueview_agent/logs
```

Abra o crontab do usuario atual:

```bash
crontab -e
```

Adicione esta linha para rodar a cada 1 hora:

```cron
0 * * * * cd /opt/plueview_agent && /opt/plueview_agent/venv/bin/python /opt/plueview_agent/monitor_cron.py --station-id 1 --window-hours 6 --stale-hours 2.5 >> /opt/plueview_agent/logs/monitor.log 2>&1
```

Essa configuracao e a recomendada para ESP32 enviando a cada 1 hora. O cron verifica de hora em hora, mas so alerta quando a ultima leitura estiver atrasada mais de `2.5h`.

Se voce quiser rodar literalmente a cada 2 envios do ESP32, use:

```cron
0 */2 * * * cd /opt/plueview_agent && /opt/plueview_agent/venv/bin/python /opt/plueview_agent/monitor_cron.py --station-id 1 --window-hours 6 --stale-hours 2.5 >> /opt/plueview_agent/logs/monitor.log 2>&1
```

Eu prefiro a versao de 1 em 1 hora com `--stale-hours 2.5`, porque detecta mais cedo quando o cron e o envio do ESP32 nao estao perfeitamente alinhados.

Ver logs:

```bash
tail -n 80 /opt/plueview_agent/logs/monitor.log
```

Ver se o cron foi instalado:

```bash
crontab -l
```

Importante: essa versao grava diagnosticos em log. Para enviar alerta por Telegram, email, Discord ou Slack, adicione um notifier depois; o cron ja separa `OK` de `ALERT`.

## 12. Configurar no cliente Hermes

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

## 13. Prompt de teste no Hermes

```text
Analise a estacao 1 nas ultimas 24 horas.
Use o Supabase MCP em modo read-only para ler weather_data pelo stationId.
Veja se houve falha de sensor ou evento climatico estranho. Se houver telemetria de bateria, avalie tambem queda de bateria.
Gere um diagnostico curto.
```

## 14. Atualizar na VM

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
