# VerificaSalesforce Backend (API + Scanner)

Backend em Python para detecção passiva de evidências públicas de Salesforce.

O projeto roda como API HTTP (FastAPI) para deploy no Railway, e também mantém utilitários de CLI para uso local.

## Estrutura

```text
.
├── main.py                      # API FastAPI (Railway entrypoint)
├── scanner_cli.py               # CLI local para scan único
├── bulk_scan.py                 # Execução em massa (resumo TXT/JSON)
├── requirements.txt
├── README.md
├── tests
│   └── test_engine.py
└── salesforce_scanner
    ├── __init__.py
    ├── analyzer.py
    ├── engine.py                # run_scan(url) central
    ├── fetcher.py
    ├── patterns.py
    ├── report.py
    └── scorer.py
```

## Requisitos

- Python 3.11+
- Dependências do `requirements.txt`

## Instalação

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

## Rodando localmente (API)

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

### Health check

```bash
curl http://localhost:8000/health
```

Resposta:

```json
{"status":"ok"}
```

### Scan

```bash
curl -X POST http://localhost:8000/scan \
  -H "Content-Type: application/json" \
  -d '{"url":"https://www.einstein.br"}'
```

Exemplo de resposta:

```json
{
  "input_url": "https://www.einstein.br",
  "final_url": "https://www.einstein.br",
  "score": 82,
  "classification": "Confirmado",
  "salesforce_detected": true,
  "products": ["Service Cloud"],
  "evidence": [
    {
      "type": "network_request",
      "value": "https://service.force.com/...",
      "reason": "Request de rede para recurso com indicador Salesforce",
      "pattern_key": "service_force_domain"
    }
  ]
}
```

## Regras de validação de URL na API

A rota `/scan` aceita apenas URLs públicas `http/https` e bloqueia:

- `localhost`
- `127.0.0.1`
- `0.0.0.0`
- `::1`
- domínios locais/internos (`.localhost`, `.local`, `.internal`)
- IPs privados, loopback, link-local, reservados e não especificados

## CORS

Ativo por padrão com `allow_origins=["*"]`.

Para restringir no futuro, use variável de ambiente:

```bash
CORS_ALLOW_ORIGINS=https://averon.cloud,https://www.averon.cloud
```

## Deploy no Railway

1. Suba o repositório no GitHub.
2. Crie um projeto no Railway a partir do repo.
3. Configure o start command:

```bash
uvicorn main:app --host 0.0.0.0 --port $PORT
```

4. Garanta que o build use `pip install -r requirements.txt`.
5. (Opcional) Configure `CORS_ALLOW_ORIGINS`.

## Scanner via CLI (opcional)

Scan único:

```bash
python scanner_cli.py https://empresa.com.br --json-output scan_result.json
```

Bulk scan:

```bash
python bulk_scan.py \
  --input-file results/massa_urls.txt \
  --output-txt results/massa_resumo.txt \
  --output-json results/massa_resumo.json
```

## Testes

```bash
python -m unittest discover -s tests -p "test_*.py" -v
```

## Observações

- Não usa brute force
- Não tenta autenticação
- Não acessa áreas privadas
- Analisa somente conteúdo publicamente acessível
- Sem banco de dados, sem persistência e sem fila: recebe, analisa, retorna
