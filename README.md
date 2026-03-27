# VerificaSalesforce Backend

API FastAPI para detecção passiva de evidências públicas de Salesforce em sites.

Este repositório é o backend do scanner e está pronto para deploy no Railway.

## O que a API faz

- Recebe uma URL pública (`http`/`https`)
- Executa análise passiva (sem autenticação, sem brute force, sem exploração)
- Retorna JSON com score, classificação, produtos inferidos e evidências
- Não salva em banco (stateless)

## Estrutura do projeto

```text
.
├── main.py                      # API FastAPI (entrypoint Railway)
├── scanner_cli.py               # CLI para scan único local
├── bulk_scan.py                 # execução em massa (TXT/JSON)
├── requirements.txt
├── tests/
└── salesforce_scanner/
    ├── analyzer.py
    ├── engine.py                # run_scan(url)
    ├── fetcher.py
    ├── patterns.py
    ├── report.py
    └── scorer.py
```

## Endpoints

### `GET /health`

Retorno esperado:

```json
{"status":"ok"}
```

### `POST /scan`

Payload:

```json
{"url":"https://empresa.com.br"}
```

Resposta (exemplo):

```json
{
  "input_url": "https://empresa.com.br",
  "final_url": "https://www.empresa.com.br",
  "score": 82,
  "classification": "Confirmado",
  "salesforce_detected": true,
  "products": ["Service Cloud", "Marketing Cloud"],
  "evidence": [
    {
      "type": "network_request",
      "value": "https://service.force.com/...",
      "reason": "Request de rede para recurso com indicador Salesforce"
    }
  ]
}
```

### `GET /scan/status`

Retorna se já existe uma análise em andamento (útil para UX no frontend):

```json
{
  "status": "ok",
  "scan_in_progress": false,
  "retry_after_seconds": 8
}
```

### Resposta `429` de concorrência

A API processa **uma análise por vez**.
Se outra requisição chegar enquanto uma análise está rodando, retorna `429` com:

- header `Retry-After`
- body `details.retry_after_seconds`

Isso permite mostrar no site: `Já existe uma análise em andamento. Tentando novamente em Xs...`.

## Testando agora no Railway

1. Copie a URL pública do seu serviço no Railway (ex.: `https://verificasalesforce-production.up.railway.app`).
2. No terminal, defina a variável:

```bash
API_URL="https://SEU-SERVICO.up.railway.app"
```

3. Teste saúde da API:

```bash
curl -sS "$API_URL/health"
```

4. Teste scan:

```bash
curl -sS -X POST "$API_URL/scan" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://www.einstein.br"}'
```

5. Teste com URL inválida (para validar proteção):

```bash
curl -sS -X POST "$API_URL/scan" \
  -H "Content-Type: application/json" \
  -d '{"url":"http://127.0.0.1"}'
```

Você deve receber erro `400` com mensagem de URL inválida.

## Teste rápido via navegador (frontend)

No console do navegador:

```javascript
fetch("https://SEU-SERVICO.up.railway.app/scan", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ url: "https://www.alelo.com.br/" })
}).then(r => r.json()).then(console.log)
```

## CORS

Atualmente está aberto para facilitar testes:

- `allow_origins=["*"]`

Para produção com domínio específico, configure a variável:

```bash
CORS_ALLOW_ORIGINS=https://averon.cloud,https://www.averon.cloud
```

Também é possível ajustar o tempo de espera sugerido para fila:

```bash
SCAN_RETRY_AFTER_SECONDS=8
```

## Deploy no Railway

- Start command:

```bash
uvicorn main:app --host 0.0.0.0 --port $PORT
```

- Build/install:

```bash
pip install -r requirements.txt
python -m playwright install --with-deps chromium
```

## Rodando localmente

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install --with-deps chromium
uvicorn main:app --host 0.0.0.0 --port 8000
```

Testes locais:

```bash
curl -sS http://localhost:8000/health
curl -sS -X POST http://localhost:8000/scan -H "Content-Type: application/json" -d '{"url":"https://www.igua.com.br/"}'
```

## CLI (opcional)

Scan único:

```bash
python scanner_cli.py https://empresa.com.br --json-output scan_result.json
```

Scan em massa:

```bash
python bulk_scan.py \
  --input-file results/massa_urls.txt \
  --output-txt results/massa_resumo.txt \
  --output-json results/massa_resumo.json
```

## Segurança e limites

- Sem brute force
- Sem autenticação
- Sem exploração de falhas
- Somente conteúdo publicamente acessível
- Sem banco de dados/persistência

## Testes automatizados

```bash
python -m unittest discover -s tests -p "test_*.py" -v
```
