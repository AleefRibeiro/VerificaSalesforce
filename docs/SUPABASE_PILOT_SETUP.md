# Configuração do piloto privado ScannerSalesforce com Supabase

O piloto está preparado e testado localmente. Nenhuma configuração remota, usuário, segredo ou grant real foi criado; nenhum crédito de fornecedor foi consumido. A URL pública continua na versão já publicada. Este checklist descreve a ativação futura, que depende de recurso Averon exclusivo, custo confirmado e autorização de configuração e rollout.

O inventário confirmado é uma organização Free, `AleefRibeiro's Org` (`rxreodfgqwfnknokmuca`), com GearheadEvents ativo e Fiui INACTIVE; nenhum Averon. A escolha dessa organização está pendente com o usuário. Não reutilizar, pausar, excluir ou modificar nenhum dos dois projetos para acomodar o piloto.

## Projeto e custo antes de criar

1. Resolver com o usuário a organização do projeto Averon separado.
2. Confirmar uma vaga efetivamente gratuita e o plano da organização antes de provisionar **Supabase Free com Postgres padrão**. Não presumir disponibilidade a partir do status INACTIVE do Fiui; não aceitar upgrade, compute pago ou add-on.
3. Confirmar o custo/limite de consumo do serviço Railway VerificaSalesforce já existente antes do rollout. O aumento de CPU/egress não foi quantificado; não prometer custo total zero.
4. Registrar o ID/URL exatos do novo recurso Averon e revisar o escopo da configuração. Sem gratuidade confirmada, manter a preparação fechada e apresentar a limitação.

O [Free custa US$0/mês](https://supabase.com/pricing), inclui 500 MB de banco e 50 mil usuários ativos mensais, limita a dois projetos ativos e pausa depois de uma semana de inatividade. Isso não confirma um slot disponível nesta conta. Apollo e TheirStack continuam fora do piloto e desativados.

## Auth e usuários autorizados

A revisão Google/histórico posterior está em [GOOGLE_OAUTH_HISTORY_SETUP.md](GOOGLE_OAUTH_HISTORY_SETUP.md) e substitui a proposta inicial de login por senha e conta única. O piloto agora prevê Alef e um amigo, com histórico privado por usuário confirmado por Alef; OAuth/configuração/provisionamento continuam pendentes.

No novo projeto, preparar Google OAuth somente após autorização e verificação de suas duas contas, com cadastros públicos e usuários anônimos desabilitados. Não solicitar senhas Google em chat nem criar OAuth, SSO, SMS ou integração SMTP automaticamente. O serviço padrão de e-mail é best effort e limitado a dois envios por hora; confirmar que atende ao fluxo de teste antes de enviar mensagens. [Auth por senha](https://supabase.com/docs/guides/auth/passwords).

Recomendação de configuração do piloto: token de acesso com validade de 600 segundos. Controles Pro de timeout/sessão única não são requisito e não devem ser contratados. A API consulta o usuário no Auth para verificar o token e depois confere sessão ativa e membership no banco; assinatura não é substituída por decodificação local. A autorização ignora `user_metadata` e permissões declaradas pelo cliente.

O `session_id` verificado é correlacionado com `auth.sessions.id`; sessão ausente, `not_after` expirado, membership desativado ou workspace desativado negam acesso. [Sessões](https://supabase.com/docs/guides/auth/sessions) e [modelo oficial atual](https://github.com/supabase/auth/blob/master/internal/models/sessions.go). A checagem tem prazo de dois segundos por requisição; latência real deve ser validada no novo projeto.

## Banco privado e permissões

Revisar [sql/averon_private_pilot.sql](../sql/averon_private_pilot.sql) no projeto **Averon novo**, antes de aplicar qualquer DDL/grant. O draft é transacional, não idempotente e não contém seed de usuário/workspace. Não rodá-lo nos projetos existentes.

O schema `averon_private` contém workspaces, memberships, relatórios/cache e eventos de quota. Todas as tabelas ativam e forçam RLS. `anon` e `authenticated` não têm acesso ao schema/tabelas/RPCs. Os sete RPCs usam `SECURITY INVOKER`, `search_path` fixo e filtros explícitos de workspace e usuário; somente o backend com chave secreta pode chamá-los. A chave secreta usa `service_role` e ignora RLS, por isso os filtros nos RPCs e a verificação de sessão no backend são controles obrigatórios. [Chaves e privilégios](https://supabase.com/docs/guides/getting-started/api-keys).

Depois da aprovação do SQL, adicionar `averon_private` aos schemas expostos do Data API para os RPCs do backend, **sem expor `auth` e sem grants ao navegador**. Não copiar o exemplo de grants públicos da documentação. [Schema customizado](https://supabase.com/docs/guides/api/using-custom-schemas).

Inserir, somente com autorização, um workspace Averon com UUID próprio e `active=true`; criar membership para cada UUID Auth real confirmado de Alef e do amigo, no mesmo workspace, `active=true`, permissões `research:read` e `research:write`. Um usuário tem um workspace no piloto. Não derivar membership de e-mail enviado pelo navegador ou metadata editável.

A leitura de `auth.sessions` pelo backend recebe apenas `id`, `user_id` e `not_after`, para revogação; nenhum dado dessas linhas chega ao frontend. O resto do Auth permanece gerido pelo provedor.

## Configuração do backend futuro

O factory [research_pilot.create_configured_pilot](../research_pilot.py) aceita configuração do ambiente somente quando chamado explicitamente. O `research_pilot:app` atual permanece fechado; a produção Railway continua `main:app`.

| Variável | Valor depois de recurso e configuração aprovados |
| --- | --- |
| `AVERON_PILOT_ENABLED` | `enabled`; ausente/desabilitada mantém acesso fechado |
| `AVERON_SUPABASE_REF` | Ref real de 20 caracteres do projeto Averon novo |
| `AVERON_SUPABASE_PUBLISHABLE_KEY` | Chave `sb_publishable_…` desse projeto |
| `AVERON_SUPABASE_SECRET_KEY` | Chave `sb_secret_…`, somente no gerenciador seguro Railway; nunca em `NEXT_PUBLIC_*` |
| `AVERON_WORKSPACE_ID` | UUID do workspace privado previamente autorizado |
| `AVERON_CORS_ORIGINS` | JSON exato `["https://averon-tools.vercel.app"]`; sem wildcard, porta, path ou outras origens |
| `AVERON_PUBLIC_HTML_ENABLED` | Começar com `disabled`; `enabled` somente após aprovação da coleta e validação do piloto |

Chaves modernas vão em `apikey`; o token do usuário vai em `Authorization` somente para o Auth. Não usar chave secreta como Bearer. [Contrato de chaves](https://supabase.com/docs/guides/getting-started/api-keys). O transporte aceita apenas o endpoint do projeto configurado, valida TLS, rejeita redirects/proxies, limita JSON a 1 MiB, duração total a três segundos e até quatro requisições Supabase simultâneas por processo. Falha de Auth nega a sessão; falha de armazenamento/quota responde 503 fixo, sem fallback para memória ou erros brutos.

Quando o rollout for autorizado, o entrypoint futuro é `uvicorn research_pilot:create_configured_pilot --factory --host 0.0.0.0 --port "$PORT" --workers 1 --no-access-log`. O comando não foi executado nesta etapa. Confirmar o serviço VerificaSalesforce exato e manter um worker no piloto.

Esse app fornece `/health` e as rotas privadas `/v2`; **não fornece `/scan` nem `/scan/status`**. O corte de compatibilidade com a v1 anônima deve ser aceito no rollout. Nenhuma rota da API publicada foi alterada agora.

## Configuração do frontend futuro

Na branch `codex/averon-tools`, o runtime aceita somente quatro valores públicos. Ausentes, mantém a interface fechada e não cria o SDK. O SDK 2.117.3 é carregado após ação explícita de login Google ou retorno ao callback iniciado; sessão fica em memória, sem persistência de tokens, login automático ou signup. Apenas a tentativa/verifier PKCE atravessa o redirect em sessionStorage por até cinco minutos. Logout invalida o acesso local antes da revogação remota; uma conclusão atrasada de login/operação não restaura acesso ou relatórios.

| Variável | Valor depois de Auth/API validados |
| --- | --- |
| `NEXT_PUBLIC_AVERON_PILOT` | `enabled` |
| `NEXT_PUBLIC_AVERON_SUPABASE_REF` | A mesma ref Averon exclusiva do backend |
| `NEXT_PUBLIC_AVERON_SUPABASE_PUBLISHABLE_KEY` | Somente `sb_publishable_…`; nunca a chave secreta |
| `NEXT_PUBLIC_AVERON_RESEARCH_API_ORIGIN` | `https://verificasalesforce-production.up.railway.app`, após confirmar o rollout no serviço existente |

O prebuild rejeita nomes inesperados no namespace público Averon, chave de tipo secreto, origem inválida e configuração incompleta. Usar variáveis do ambiente de build aprovadas, sem copiar `.env` de outro projeto; arquivos `.env*` ficam fora do upload Vercel. Os valores públicos entram no export, portanto ativá-los requer novo build/publicação, **ainda não autorizados nesta etapa**. Credenciais não devem ser enviadas em chat ou lidas de sessões locais.

## Fontes, armazenamento e validação antes de liberar

O piloto tem somente `public_html`: uma página raiz HTTPS, redirects restritos ao mesmo domínio/www, sem scripts executados ou recursos externos buscados. Uma referência técnica não confirma contrato, licença ou uso interno. Datas originais desconhecidas continuam nulas; ausência de sinal fica inconclusiva. Não habilitar Apollo/TheirStack ou fazer benchmark em empresas sem autorização específica.

Cache tem até uma hora de validade. Histórico preserva datas originais por até 30 dias e 128 relatórios por usuário, com payload de até 512 KiB. Expiração lógica bloqueia leituras históricas vencidas; remoção física é oportunista ao salvar outro relatório do mesmo owner, sem job/cron provisionado. Quota é compartilhada no Postgres: dez alvos por janela móvel de 60 segundos, inclusive hits de cache; lote máximo cinco. Locks transacionais serializam cobrança por workspace e gravação por owner.

Depois das configurações autorizadas, validar login permitido/negado, token expirado, logout/revogação, membership sem permissão, workspace desativado, isolamento de relatórios, expiração, quota e CORS real. Validar PostgREST/schema/grants e latência da checagem no recurso novo. Os testes locais não substituem esse fluxo hospedado, teste de concorrência com múltiplas conexões ou Safari do iPhone.

Só então aprovar coleta mínima de domínios explícitos, revisar manualmente as evidências e ativar o frontend. Nenhuma consulta real é substituída por fixtures ou por resultado inventado.

## Testes locais

Passaram 50 testes v2 e 31 novos testes de Auth/piloto/transporte/armazenamento, três testes de contrato FastAPI, 37 testes frontend, lint/tipos/build/export e 13 checks de SQL no PGlite 0.5.8. O PostgreSQL do teste existe somente em memória, com roles/sessões/linhas sintéticas, sem conexão remota e destruído ao finalizar; nenhum grant externo foi aplicado.

No frontend, o QA do export padrão fechou 15 checks e 14 capturas em Chrome isolado, sem erros JavaScript ou requisições ao Scanner observados. O login real, os grants/PostgREST hospedados e a coleta real continuam sem validação até existir recurso autorizado. Logs da etapa ficam em `Site-Averon/evidence/pilot-*`; a evidência da publicação anterior permanece intacta.
