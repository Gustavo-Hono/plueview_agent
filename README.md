# Hermes MCP para diagnostico PluView + Supabase

Este diretorio contem um servidor MCP local chamado Hermes para diagnosticar estacoes PluView usando dados ja salvos no Supabase.

O Hermes nao coleta MQTT e nao substitui a API PluView. O fluxo esperado e:

```text
ESP32/MQTT -> PluView API -> Supabase/Postgres -> Supabase MCP -> Hermes MCP -> diagnostico curto
```

Use este README para entender a arquitetura e implementar o MCP no cliente Hermes.

## O que foi criado

- `hermes_mcp.py`: servidor MCP local de diagnostico.
- `mcp.example.json`: exemplo de configuracao para adicionar no Hermes.
- `test_hermes_client.py`: teste local sem acessar Supabase.

O servidor expoe tres ferramentas:

- `sql_diagnostico_estacao`: gera uma consulta SQL read-only para a estacao e janela informadas.
- `diagnosticar_resultado_supabase`: recebe o JSON retornado pelo Supabase MCP e gera o diagnostico.
- `analisar_estacao_supabase`: tenta chamar o Supabase MCP diretamente e devolver o diagnostico em uma unica ferramenta.

Tambem expoe o prompt:

- `analisar_estacao_prompt`: instrucao pronta para o Hermes seguir o fluxo com Supabase MCP.

## Por que usar dois MCPs

O Supabase MCP deve ficar responsavel por acessar o banco. Ele ja oferece `execute_sql`, autenticacao com Supabase e modo read-only.

O Hermes MCP deve ficar responsavel pela regra de negocio:

- quais tabelas ler;
- qual SQL usar;
- como detectar falha de sensor;
- como detectar queda de bateria;
- como detectar evento climatico estranho;
- como formatar um diagnostico curto.

Assim o Hermes nao precisa guardar credencial direta do Postgres nem instalar driver de banco local.

## Tabelas usadas

O diagnostico usa as tabelas do schema Prisma atual:

- `public.weather_data`
- `public."DataPlueView"`

Campos meteorologicos:

- `weather_data."stationId"`
- `weather_data."dataMedicao"`
- `weather_data.temperatura`
- `weather_data.umidade`
- `weather_data."quantidadeChuva"`
- `weather_data."velocidadeVento"`
- `weather_data."direcaoVento"`
- `weather_data.latitude`
- `weather_data.longitude`

Campos IoT/brutos:

- `"DataPlueView"."stationId"`
- `"DataPlueView".time`
- `"DataPlueView".battery`
- `"DataPlueView"."ConsumoPluviometro"`
- `"DataPlueView"."ConsumoVelocidadeVento"`
- `"DataPlueView"."ConsumoDirecaoVento"`
- `"DataPlueView"."ConsumoTemperatura"`
- `"DataPlueView"."ConsumoUmidade"`

## Requisitos

Ja existe um ambiente Python em:

```bash
agent/venv
```

Entao, para rodar o Hermes MCP local, nao precisa instalar nada agora.

Para acessar Supabase pelo MCP oficial, voce precisa de:

- `SUPABASE_PROJECT_REF`: id/ref do projeto Supabase.

`SUPABASE_ACCESS_TOKEN` nao e obrigatorio no fluxo normal. O Supabase MCP remoto usa login/OAuth pelo cliente MCP quando o cliente suporta esse fluxo. Use token manual apenas quando:

- o cliente MCP nao suporta login/OAuth;
- voce estiver em CI ou ambiente sem navegador;
- voce quiser passar autenticacao por header manualmente.

O Supabase MCP remoto oficial e:

```text
https://mcp.supabase.com/mcp
```

Use sempre:

```text
read_only=true
features=database
```

## Implementacao no Hermes

### 1. Abra a configuracao MCP do Hermes

No Hermes, procure pela area de configuracao de MCP servers. Dependendo da instalacao, isso pode ser um arquivo como:

- `.mcp.json`
- `mcp.json`
- configuracao de ferramentas/MCP dentro das settings do Hermes

Se o Hermes aceitar o formato `mcpServers`, use o exemplo abaixo.

### 2. Configure Supabase MCP e Hermes MCP

Exemplo com caminhos relativos ao diretorio raiz deste workspace:

```json
{
  "mcpServers": {
    "supabase": {
      "type": "http",
      "url": "https://mcp.supabase.com/mcp?project_ref=${SUPABASE_PROJECT_REF}&read_only=true&features=database"
    },
    "hermes": {
      "command": "agent/venv/bin/python",
      "args": ["agent/hermes_mcp.py"]
    }
  }
}
```

O mesmo exemplo esta em:

```text
agent/mcp.example.json
```

Se o Hermes roda a partir de outro diretorio, prefira caminho absoluto:

```json
{
  "mcpServers": {
    "supabase": {
      "type": "http",
      "url": "https://mcp.supabase.com/mcp?project_ref=${SUPABASE_PROJECT_REF}&read_only=true&features=database"
    },
    "hermes": {
      "command": "/home/pinguimsurfante/Área de trabalho/plueview/agent/venv/bin/python",
      "args": ["/home/pinguimsurfante/Área de trabalho/plueview/agent/hermes_mcp.py"]
    }
  }
}
```

Se o Hermes nao aceitar variaveis `${...}` dentro do JSON, substitua pelos valores reais:

```text
${SUPABASE_PROJECT_REF} -> ref do projeto Supabase
```

Se o Hermes nao suportar OAuth/login do Supabase MCP e exigir autenticacao manual, use a variante com header:

```json
{
  "mcpServers": {
    "supabase": {
      "type": "http",
      "url": "https://mcp.supabase.com/mcp?project_ref=${SUPABASE_PROJECT_REF}&read_only=true&features=database",
      "headers": {
        "Authorization": "Bearer ${SUPABASE_ACCESS_TOKEN}"
      }
    }
  }
}
```

### 3. Reinicie ou recarregue as ferramentas MCP

Depois de salvar a configuracao, reinicie o Hermes ou use a opcao de recarregar MCP servers.

O Hermes deve mostrar dois servidores:

- `supabase`
- `hermes`

E o servidor `hermes` deve mostrar estas ferramentas:

- `sql_diagnostico_estacao`
- `diagnosticar_resultado_supabase`
- `analisar_estacao_supabase`

## Como usar no Hermes

Prompt recomendado:

```text
Analise a estacao 1 nas ultimas 24 horas.
Use o Supabase MCP em modo read-only para ler weather_data e DataPlueView.
Veja se houve falha de sensor, queda de bateria ou evento climatico estranho.
Gere um diagnostico curto.
```

Fluxo que o Hermes deve executar:

1. Chamar `hermes.sql_diagnostico_estacao` com:

```json
{
  "station_id": 1,
  "horas": 24
}
```

2. Executar o SQL retornado usando `supabase.execute_sql`.

3. Enviar o resultado do Supabase para `hermes.diagnosticar_resultado_supabase`.

4. Responder ao usuario com o diagnostico curto.

## Uso one-shot

Se o Hermes conseguir chamar servidores MCP HTTP com header de autenticacao a partir de uma ferramenta local, voce pode usar:

```text
hermes.analisar_estacao_supabase(station_id=1, horas=24)
```

Para isso, deixe estas variaveis disponiveis no ambiente que inicia o Hermes MCP:

```bash
export SUPABASE_PROJECT_REF="seu-project-ref"
export SUPABASE_MCP_URL="https://mcp.supabase.com/mcp?project_ref=${SUPABASE_PROJECT_REF}&read_only=true&features=database"
```

Se estiver usando autenticacao manual por PAT, adicione tambem:

```bash
export SUPABASE_ACCESS_TOKEN="seu-token"
```

O modo recomendado ainda e o fluxo com duas ferramentas, porque deixa o acesso ao banco explicitamente no Supabase MCP.

## Como testar localmente

Teste de sintaxe:

```bash
agent/venv/bin/python -m py_compile agent/hermes_mcp.py agent/test_hermes_client.py
```

Teste local sem Supabase:

```bash
cd agent
venv/bin/python test_hermes_client.py
```

Teste MCP via stdio:

```bash
agent/venv/bin/python - <<'PY'
import asyncio
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

async def main():
    transport = StdioTransport(
        "agent/venv/bin/python",
        ["agent/hermes_mcp.py"],
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

## SQL gerado

O SQL e somente leitura. Ele:

- filtra `weather_data."stationId"`;
- filtra `weather_data."dataMedicao"` pela janela em horas;
- filtra `"DataPlueView"."stationId"`;
- filtra `"DataPlueView".time` pela janela em horas;
- devolve um unico campo `hermes_payload` em JSON.

Exemplo resumido:

```sql
SELECT jsonb_build_object(
  'stationId', 1,
  'hours', 24,
  'generatedAt', now(),
  'weather', ...,
  'iot', ...
) AS hermes_payload;
```

## Regras do diagnostico

O Hermes gera um diagnostico curto com estes blocos:

- `Base`: quantidade de leituras meteorologicas e IoT.
- `Sensor`: lacunas, campos ausentes, valores fisicamente invalidos e consumo de sensor zerado/negativo.
- `Bateria`: bateria ausente, baixa, critica, queda acumulada e queda brusca entre leituras.
- `Clima`: temperatura extrema, umidade extrema, pico de chuva, chuva acumulada alta, vento forte e saltos bruscos.
- `Conclusao`: `operacao normal`, `monitorar` ou `requer verificacao operacional`.

Limiares atuais:

- lacuna meteorologica: `>= 3h`;
- bateria baixa: `< 35%`;
- bateria critica: `< 20%`;
- queda acumulada de bateria: `>= 15 pontos percentuais`;
- queda brusca de bateria: `>= 10 pontos entre leituras`;
- temperatura fisicamente suspeita: `< -20 C` ou `> 60 C`;
- temperatura extrema de evento climatico: `<= -5 C` ou `>= 45 C`;
- umidade valida: `0%` a `100%`;
- umidade extrema: `< 10%` ou `> 98%`;
- pico de chuva: `>= 20 mm` em uma leitura;
- chuva acumulada alta: `>= 50 mm`;
- vento forte: `>= 20 m/s`;
- salto termico: `>= 8 C` com taxa `>= 4 C/h`;
- variacao brusca de umidade: `>= 35 pontos` em ate `2h`.

## Exemplo de resposta

```text
Diagnostico curto - estacao 1 (ultimas 24h)
Base: 3 leituras meteo, 3 leituras IoT
Sensor: lacuna de 5.5h entre leituras meteorologicas; consumo zerado/negativo recorrente no sensor de temperatura
Bateria: queda acumulada de 21 pontos percentuais; queda brusca de 19 pontos entre leituras
Clima: pico de chuva de 26.4 mm em uma leitura; vento forte detectado (22.0 m/s)
Conclusao: requer verificacao operacional.
```

## Seguranca

Use o Supabase MCP sempre com:

```text
read_only=true
features=database
project_ref=<seu-project-ref>
```

Evite conectar o Hermes com permissao ampla em producao. Se precisar usar dados reais, prefira:

- projeto escopado por `project_ref`;
- login/OAuth pelo Supabase MCP, quando suportado pelo cliente;
- token com menor permissao possivel, apenas quando autenticacao manual for necessaria;
- aprovacao manual das chamadas MCP no Hermes;
- revisao do SQL antes de executar.

## Troubleshooting

### Hermes nao mostra o servidor `hermes`

Verifique se o caminho do Python e do script esta correto:

```bash
agent/venv/bin/python agent/hermes_mcp.py
```

Esse comando fica aguardando mensagens MCP via stdio; isso e esperado.

### Hermes nao mostra o Supabase MCP

Verifique:

- se a URL contem `read_only=true`;
- se `SUPABASE_PROJECT_REF` foi preenchido;
- se o cliente Hermes iniciou o fluxo de login/OAuth do Supabase MCP;
- se, no modo manual com header, o cliente Hermes aceita headers e `SUPABASE_ACCESS_TOKEN` esta valido.

### `execute_sql` nao aparece

Garanta que a URL do Supabase MCP tem:

```text
features=database
```

### Diagnostico vem sem dados

Confirme no Supabase se existem registros recentes para a estacao:

```sql
SELECT count(*)
FROM public.weather_data
WHERE "stationId" = 1
  AND "dataMedicao" >= now() - interval '24 hours';

SELECT count(*)
FROM public."DataPlueView"
WHERE "stationId" = 1
  AND time >= now() - interval '24 hours';
```

### Caminho com espaco no nome

Como o workspace esta em `Área de trabalho`, alguns clientes podem falhar com caminhos relativos. Nesse caso use o exemplo com caminho absoluto.
