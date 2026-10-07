# Averon: motor privado de pesquisa v2

Checkpoint local de 7 de outubro de 2026. Implementação separada da API publicada.

## Repositórios e produção identificados

- Site original: `/Users/alefribeiro/Documents/Projects/Averon/Averon Cloud/averon`, remoto `https://github.com/AleefRibeiro/Site-Averon.git`, base institucional `9bf48deea215ff2dc86d099453b406b1e6c59461`. Produção identificada: `https://averon.cloud/`.
- API original: `/Users/alefribeiro/Documents/Projects/Averon/VerificaSalesforce`, remoto `https://github.com/AleefRibeiro/VerificaSalesforce.git`. O checkout original contém a branch `feat/domain-first-discovery`, preservada.
- Esta implementação parte de `origin/main` (`d455c0d51eba631934000e4c8f088a880537a262`), branch local `codex/scanner-research-v2`, worktree `/Users/alefribeiro/Documents/Codex/2026-10-07/task/scanner-research-worktree`.
- Railway identificado pelo pai: projeto Scanner Salesforce / serviço VerificaSalesforce. URL existente `https://verificasalesforce-production.up.railway.app`; o `/health` foi consultado anteriormente, sem POST de scanner.
- O processo publicado continua `main:app`. Não há importação da v2 por `main.py`, modificação de configuração Railway, push, deploy ou alteração de DNS neste trabalho.

## O que funciona localmente

`research_api.py` fornece uma aplicação FastAPI separada:

| Rota | Resultado e acesso |
| --- | --- |
| `GET /health` | Estado genérico, sem pesquisa/dados de empresas |
| `GET /v2/access` | Sessão validada e `research:read`; informa validade, permissões e fontes configuradas para o workspace, sem coletar ou retornar identidade/chaves |
| `POST /v2/research` | Um domínio explícito; exige sessão validada e `research:write` |
| `POST /v2/research/batch` | 1–5 entradas; deduplica domínio/escopo/id; exige `research:write` |
| `GET /v2/reports/{id}` | Relatório do próprio workspace, dentro do TTL; exige `research:read` |

A aplicação padrão responde **401 a toda rota /v2**, mesmo com um Bearer arbitrário. Não existe login/OAuth simulado, chave administrativa fixa, leitura de claims sem validação, permissão por header ou ativação de fonte em JSON do cliente. A configuração de CORS permanece fechada. Documentação HTTP e OpenAPI públicas estão desativadas.

O endpoint de acesso sinaliza configuração disponível, não sucesso garantido de uma coleta. Exige fonte habilitada na política, workspace permitido e chave presente quando necessária. Sessão somente de leitura não pode habilitar a operação de pesquisa. O frontend preparado verifica esse contrato antes de enviar POST e permanece sem runtime conectado.

O input aceita domínio/URL HTTPS, nome opcional e escopo/id opcional. O domínio é explícito: não há busca aproximada por marca, expansão para subsidiária/grupo, CT logs ou descoberta de subdomínios. Caminho/query da URL de entrada são descartados; a coleta pública lê somente a raiz. O nome é um rótulo e auxiliar de matching; `legal_identity_verified=false`. Nomes conflitantes para o mesmo domínio/escopo no lote produzem 422 antes de coleta. Relações jurídicas exigem evidência e resolução futuras.

Cada relatório contém status, motivos/limitações, produtos, URL da origem, URL intermediária quando disponível, domínio/escopo atribuído e datas. `checked_at` é a hora de consulta; **não substitui** `published_at`, `first_seen` ou `last_seen`. Datas ausentes continuam `null`. Erros e fontes desativadas aparecem explicitamente e produzem cobertura parcial.

## Classificação conservadora

- **Confirmado publicamente:** referência técnica direta ou declaração oficial, recente e atribuída ao domínio/escopo. Uma referência HTML confirma aquela referência pública; não comprova runtime, contrato ou licença interna.
- **Forte indício:** ao menos duas origens deduplicadas com força média/alta, sem confirmação direta. Não é uma probabilidade numérica.
- **Possível:** sinal contextual atual que ainda requer revisão.
- **Histórico:** publicação antiga (janela padrão 180 dias) ou contexto de saída/migração.
- **Inconclusivo, Erro ou Ambiguidade:** sem evidência elegível, coleta falha ou matching inseguro. Ausência de sinal nunca vira “não usa”.

Vagas para clientes não são atribuídas à consultoria. Consultoria/recrutador sem atribuição clara fica com relação desconhecida. Marketing Cloud/Pardot não vira Sales Cloud; “Salesforce” genérico não determina produto. Evidência de filial/domínio/cliente não é promovida a grupo. URL canônica elimina parâmetros de rastreio; conteúdo republicado também é deduplicado. Duplicatas conservam contexto de cliente/saída em preferência ao texto positivo. As evidências originais permanecem nos resultados das fontes para revisão.

A heurística de contexto é determinística e limitada a padrões em português/inglês. Não é IA, NLP abrangente nem uma promessa de acurácia. Ainda falta benchmark real, autorizado e revisado manualmente, medindo precisão/recall por classe, produto, idioma e escopo. Os testes sintéticos demonstram regras e controles, não desempenho em empresas reais.

## Adaptadores reais, todos desativados

`ProviderPolicy()` usa `enabled=false`, allowlist de workspaces vazia e chave ausente. As chaves são injetadas pelo servidor no objeto, não carregadas de arquivos/env nesta implementação e nunca entram em repr, resultado ou log. Habilitar exige revisão/autorização posterior da fonte, licença, custo e workspace. `revision` deve mudar ao alterar configuração para invalidar cache.

| Fonte | Contrato implementado | Limitação |
| --- | --- | --- |
| TheirStack | `POST https://api.theirstack.com/v1/jobs/search`, Bearer, domínio exato, regex de descrição, page 0, limit ≤5, sem totais | Data original da vaga preservada; republicação não cria data fresca/origem independente. Domínio divergente sinaliza ambiguidade. |
| Apollo | `GET https://api.apollo.io/api/v1/organizations/enrich`, `x-api-key`, query domain/name | Matching exige `primary_domain` exato. Retém apenas id/nome/domínio/indústria e produtos; descarta contatos, CRM e dados de parent/subsidiárias. Sem data tecnológica neste contrato, o sinal permanece inelegível para uso atual. |
| HTML público | Uma página raiz, HTTPS; no máximo dois redirects dentro do domínio exato ou www | Examina referências script/iframe a recursos Salesforce/Pardot; não busca os recursos externos, não roda browser/headless e não faz varredura de segurança. |

Fontes oficiais verificadas em **2026-10-07**, sem chave ou consumo de endpoint de pesquisa:

- [TheirStack: OpenAPI oficial, incluindo o esquema Bearer e os endpoints](https://api.theirstack.com/openapi.json).
- [Apollo: organization enrichment](https://docs.apollo.io/reference/organization-enrichment), [autenticação](https://docs.apollo.io/reference/authentication) e [OpenAPI oficial](https://docs.apollo.io/openapi/apollo-rest-api.json).

A documentação Apollo informa consumo de crédito por organização; nenhuma chamada de enriquecimento foi realizada. Nenhum health de fornecedor pago ou teste de chave foi executado. As respostas dos testes são objetos sintéticos locais.

## Limites e transporte

- Corpo de POST ≤32 KiB e envio ≤5 s, depois da validação de sessão; lote máximo 5; quota 10 alvos/minuto/workspace, inclusive hits de cache.
- Máximo 2 coletas simultâneas/processo e 128 tarefas pendentes; máximo 3 coletores, 10 evidências retidas por coletor. Adaptadores pagos fazem uma chamada por fonte/alvo, page 0, até 5 registros, sem retries automáticos.
- HTML ≤512 KiB, deadline 10 s incluindo DNS/redirects, DNS ≤3 s por lookup, TLS com SNI/hostname verificados, timer interrompe socket em deadline; zero bypass de certificado.
- Todos os IPs retornados pelo DNS devem ser públicos. Bloqueia loopback, RFC1918, link-local/metadata, multicast, IPv4 mapeado privado e redes de transição IPv6/NAT64. A conexão é fixada ao IP validado para evitar nova resolução/rebinding.
- Fornecedores usam hosts/endpoints fixos, TLS validado, sem redirects ou proxies de ambiente, timeout 10 s e JSON ≤1 MiB; o serviço também aplica timeout de 12 s por coleta.

## Auth e armazenamento: configuração mínima pendente

O contrato `SessionVerifier.verify(token)` deve retornar um `Principal` **somente após** validação autoritativa: sujeito, workspace derivado no servidor, permissões e expiração com timezone. O middleware rejeita resultado inválido, expirado, falha ou timeout. Para JWT/OIDC reais, ainda será necessário validar assinatura/algoritmo/issuer/audience/expiração, política de revogação e associação do usuário ao workspace. O cliente não pode escolher o workspace efetivo.

Opções a decidir com o pai:

1. Frontend estático Vercel + provedor de sessão existente aprovado + validação de token na Railway. Configurar issuer/audience/JWKS ou introspecção, mapping/ACL de workspace e allowlist CORS exata.
2. Frontend com backend de sessão/BFF e cookies seguros. Isso requer planejar a mudança do export estático atual e controles de CSRF; não foi implementado nem provisionado.

Não foi escolhido/provisionado provedor de auth, criada grant, segredo ou integração. Nenhum projeto Supabase existente (Gearhead/Fiui) foi acessado ou reutilizado.

`ResearchStore` define cache/save/get-report por workspace. `MemoryStore` funciona, guarda cópias isoladas e até 128 entradas de cache + 128 relatórios/processo. TTL 1 h para coleta completa, 60 s para fontes desativadas/erro/ambiguidade. Relatórios expirados e de outro workspace retornam 404. Não há persistência durável após restart nem compartilhamento entre workers.

Para produção será necessário decidir armazenamento privado autorizado (por exemplo banco dedicado já aprovado) com isolamento de workspace, retenção/exclusão, índices por workspace/chave/TTL e quota/lock compartilhados. SQLite exigiria volume e revisão de concorrência; serviço novo/banco/custo não foram autorizados. Até lá, memória/quota funcionam apenas em um processo e **não justificam escala horizontal de produção**.

Configuração mínima antes de ativar pesquisa: verificador real + ACL; storage/quota adequados ao número de workers; política e revisão de cada fonte; chaves no gerenciador seguro para fontes pagas, somente após autorização de custos; CORS/origem e UI privada; benchmark real autorizado. Nenhuma dessas decisões é substituída por um token de teste.

## Verificação local

Executado no Python 3.13.9 disponível, usando dependências já instaladas, sem instalar pacotes novos:

```sh
rtk proxy python3 -m unittest discover -s tests -p 'test_research_v2.py' -v
rtk proxy python3 -m compileall -q research_v2 research_api.py tests/test_research_v2.py
```

50 testes offline passaram: os 47 anteriores mais três casos de acesso fechado, minimização de resposta e disponibilidade por workspace sem coleta. O checkpoint anterior está em `evidence/research-v2-tests.txt`; o atual, em `evidence/research-v2-access-tests.txt`. Fixtures em `tests/test_research_v2.py` são explicitamente sintéticas. `ResearchService` rejeita evidência marcada sintética por padrão; o modo sintético é exclusivo de teste e `create_app` recusa servi-lo.

`tests/export_frontend_contract.py` exporta respostas reais do TestClient/FastAPI para três testes do cliente TypeScript: acesso, domínio único e lote. Usa sessão de teste e coletor vazio, somente dentro de teste, sem fornecedor/rede. Os payloads temporários não entram no frontend público. Nenhum resultado de empresa é fabricado.

Nenhuma chamada a domínio de empresa, provedor pago ou API de scan foi feita nesta etapa. Não houve QA visual porque o navegador interno não estava disponível e o Chrome está vedado antes de 19h BRT. No frontend, testes/lint/TypeScript/build/export/HTTP locais já passaram; interação, hidratação, tema e responsividade continuam pendentes de QA visual permitido.

O pedido de publicação do frontend em uma URL Vercel aguarda conta/projeto, finalidade de uso e plano aplicável; domínio/DNS ficaram para depois. Rollout da API v2 permanece separado e pendente de auth/storage/fontes reais autorizados. A API antiga anônima ainda existe em produção; **a v2 não corrige o acesso da v1 publicada até um rollout autorizado**.
