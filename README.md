# Verifica Salesforce (Scanner Passivo)

Scanner em Python para detectar evidências públicas de uso de Salesforce no ecossistema web de um domínio/URL.

O foco é **análise passiva e não invasiva**: somente conteúdo público carregado normalmente por navegador.

## Funcionalidades

- Normalização da URL de entrada
- Coleta de HTML inicial via `requests`
- Extração de:
  - scripts externos
  - scripts inline
  - iframes
  - links
- Download limitado de JavaScripts externos para inspeção
- Leitura de recursos públicos:
  - `/robots.txt`
  - `/sitemap.xml`
- Discovery interno por padrão:
  - usa `robots.txt` + `sitemap.xml` para descobrir URLs públicas
  - faz crawling interno raso com limites de segurança
- Renderização headless com Playwright para capturar:
  - requests de rede
  - cadeia de redirecionamento
  - cookies visíveis
  - HTML renderizado
- Detecção por padrões Salesforce (força bruta **não** é usada)
- Engine de decisão com score + regras determinísticas:
  - `Confirmado`
  - `Forte indício`
  - `Possível`
  - `Possível (Marketing Cloud)` / `Possível (Commerce Cloud)`
  - `Indício fraco / revisar manualmente`
  - `Nenhum sinal encontrado`
- Relatório no terminal + exportação JSON

## Estrutura do projeto

```text
.
├── main.py
├── requirements.txt
├── README.md
└── salesforce_scanner
    ├── __init__.py
    ├── analyzer.py
    ├── fetcher.py
    ├── patterns.py
    ├── report.py
    └── scorer.py
```

## Requisitos

- Python 3.11+
- Dependências do `requirements.txt`

## Instalação

1. Criar e ativar ambiente virtual (opcional, recomendado):

```bash
python3 -m venv .venv
source .venv/bin/activate
```

2. Instalar dependências Python:

```bash
pip install -r requirements.txt
```

3. Instalar navegadores do Playwright (obrigatório para etapa headless):

```bash
python -m playwright install chromium
```

Se preferir instalar todos os navegadores suportados:

```bash
python -m playwright install
```

## Uso rápido

```bash
python main.py https://empresa.com.br
```

Com verbose e JSON customizado:

```bash
python main.py https://empresa.com.br --verbose --json-output resultado_empresa.json
```

Sem Playwright (modo reduzido):

```bash
python main.py https://empresa.com.br --skip-playwright
```

## Modo em massa (bulk)

Rodar em várias URLs e gerar um TXT único resumido:

```bash
python bulk_scan.py --input-file results/massa_urls.txt --output-txt results/massa_resumo.txt
```

Formato do resumo:

- `Empresa`
- `Site`
- `% Salesforce` (confiança estimada)
- `Classificação`
- `Status` (`ok`, `ok com ressalvas`, ou `inacessível ...`)

## Opções de linha de comando

- `url` (posicional): URL/domínio alvo
- `--json-output`: caminho do JSON de saída (padrão: `scan_result.json`)
- `--verbose`: logs intermediários
- `--max-scripts`: limite de scripts externos baixados (padrão: `80`)
- `--no-discovery`: desabilita a descoberta de URLs internas (ligada por padrão)
- `--discovery-max-pages`: máximo de páginas no discovery (padrão: `25`)
- `--discovery-max-depth`: profundidade máxima do discovery (padrão: `1`)
- `--discovery-max-sitemaps`: máximo de sitemaps processados (padrão: `10`)
- `--discovery-max-subdomains`: máximo de subdomínios públicos adicionados via CT logs (padrão: `40`)
- `--max-requests`: limite de requests observados via Playwright (padrão: `250`)
- `--http-timeout`: timeout HTTP em segundos (padrão: `12`)
- `--playwright-timeout-ms`: timeout do Playwright em ms (padrão: `20000`)
- `--skip-playwright`: desabilita renderização no navegador

## Engine de score e decisão

O score é calculado por `pattern_key` + `source_type` com:

- peso base por pattern
- multiplicador por fonte (rede/redirect > script_url/iframe > script_content > html > cookie/robots/sitemap)
- cap por pattern para evitar inflação

Depois do score, há regras determinísticas para confirmação, por exemplo:

- `service.force.com` em fonte forte de URL/rede
- `embeddedservice` + domínio `force.com`/`salesforce*`
- `liveagent` + domínio `force.com`/`salesforce*`

Patterns principais (ajustáveis em `salesforce_scanner/patterns.py`):

- `*.force.com`: +40
- `service.force.com`: +50
- `lightning.force.com`: +50
- `*.salesforce-scrt.com`: +50
- subdomínio `salesforce-*`: +40
- `embeddedservice`: +45
- `liveagent` / `salesforceliveagent`: +40
- `pardot`: +35
- `exacttarget` / `mc.exacttarget`: +35
- `marketingcloud` / `marketingcloudapps`: +30
- `demandware` / `commerce cloud`: +30
- `salesforce`: sinal fraco (não confirma sozinho)
- `visualforce`: +25
- `sales cloud` / `service cloud` / `health cloud`: +25
- `experience cloud` / `siteforce`: +30

Classificação usa score + sinais fortes + confirmação cruzada + produtos inferidos:

- `Confirmado`
- `Forte indício`
- `Possível`
- `Possível (Marketing Cloud)` / `Possível (Commerce Cloud)`
- `Indício fraco / revisar manualmente`
- `Nenhum sinal encontrado`

## Exemplo de execução

```bash
python main.py https://www.salesforce.com --verbose --json-output out/salesforce.json
```

Exemplo de trechos de saída no terminal:

```text
Score             : 95
Classificação     : Confirmado
Salesforce detect.: True
- [network_request] https://service.force.com/... -> Request de rede para recurso com indicador Salesforce: Domínio service.force.com encontrado
```

Exemplo de estrutura do JSON gerado:

```json
{
  "input_url": "https://empresa.com.br",
  "normalized_url": "https://empresa.com.br",
  "final_url": "https://www.empresa.com.br",
  "score": 82,
  "classification": "Confirmado",
  "salesforce_detected": true,
  "evidence": [
    {
      "type": "network_request",
      "value": "https://service.force.com/...",
      "reason": "Request de rede para recurso com indicador Salesforce: Domínio service.force.com encontrado"
    }
  ],
  "domains_found": [
    "service.force.com",
    "example.marketingcloudapps.com"
  ],
  "checked_resources": [
    "html_initial",
    "html_rendered",
    "scripts",
    "robots.txt",
    "sitemap.xml",
    "network_requests",
    "cookies"
  ]
}
```

## Limites e segurança

- Não executa brute force
- Não tenta autenticação
- Não acessa áreas privadas
- Não explora vulnerabilidades
- Faz apenas análise de conteúdo público
- Usa limites de coleta para evitar comportamento agressivo

## Ajustes rápidos

- Adicionar/editar padrões e pesos: `salesforce_scanner/patterns.py`
- Alterar regras de classificação: `salesforce_scanner/scorer.py`
- Ajustar limites via CLI

## Testes

Rodar testes unitários:

```bash
python -m unittest discover -s tests -p "test_*.py" -v
```

Cobertura atual dos testes:

- score por múltiplas fontes com cap por pattern
- deduplicação por domínio para fontes de URL
- confirmação determinística (`embeddedservice + force_domain`)
- cenário apenas Marketing Cloud
- cenário apenas sinal fraco
- inferência de produto (Commerce Cloud)
