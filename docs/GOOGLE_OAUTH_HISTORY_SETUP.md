# Google OAuth e histórico privado — preparação local

Checkpoint de 8 de outubro de 2026, posterior ao piloto inicial. Código preparado nas branches locais; URL publicada preservada. Não houve criação de OAuth client/projeto, configuração remota, usuário/segredo/grant real, consulta de empresa ou publicação. GearheadEvents/Fiui permanecem fora do escopo.

Alef pediu login Google e histórico para ele e um amigo. Alef confirmou que cada pessoa verá somente o próprio histórico. A implementação usa **históricos privados por UUID Auth imutável**, inclusive quando os dois pertencem ao mesmo workspace. Não existe compartilhamento entre Alef e o amigo.

## Menor próximo passo

Resolver a organização e confirmar capacidade/custo do projeto Supabase Averon exclusivo antes de autorizar sua criação. Em seguida, autorizar especificamente configuração OAuth e provisionamento das duas contas. O usuário precisará indicar os dois e-mails Google autorizados e escolher o projeto Google Cloud que controlará o OAuth; não enviar senhas, tokens ou Client Secret em chat.

Ainda não existe projeto Averon/ref/callback Supabase confirmado. Portanto, nenhum Client ID, Client Secret, provider Google, redirect allowlist ou membership real foi configurado ou validado. Não reutilizar recursos de outras aplicações. Os requisitos anteriores de Free/Railway constam em [SUPABASE_PILOT_SETUP.md](SUPABASE_PILOT_SETUP.md).

## Configurações futuras exatas

1. No recurso Google Cloud autorizado: Google Auth Platform com nome do app e contato do responsável; audience **External**, adequado ao amigo fora de uma organização Workspace. Para o piloto, manter **Testing** e registrar os dois e-mails em Test users. Solicitar somente `openid email profile`. Não habilitar APIs Drive/Gmail, billing ou scopes sensíveis. A autorização do app depende da membership no backend; a lista Google não substitui esse controle. [Consentimento e scopes](https://developers.google.com/workspace/guides/configure-oauth-consent).
2. Criar, somente após autorização, um OAuth client do tipo **Web application**. Origem JavaScript: `https://averon-tools.vercel.app`. URI de redirect Google: `https://<ref-Averon-nova>.supabase.co/auth/v1/callback`, copiada do provider Google no novo Supabase. Essa URI é do Supabase e difere do callback do frontend. Guardar Client ID/Secret diretamente nos campos seguros do provider Google no Supabase. Não colocar segredo Google no frontend, Git ou Railway. [Configuração oficial Supabase/Google](https://supabase.com/docs/guides/auth/social-login/auth-google).
3. Supabase Auth Site URL: `https://averon-tools.vercel.app`; Redirect URLs: **somente** `https://averon-tools.vercel.app/auth/callback/` para esse rollout. Não usar wildcard de preview, `next` externo ou domínio institucional antigo. [Redirects](https://supabase.com/docs/guides/auth/redirect-urls).
4. Manter signup público e usuários anônimos desligados. Provisionar/convidar somente as duas contas autorizadas por fluxo seguro do provedor, validar seus e-mails e o vínculo Google ao UUID Auth correto. O Supabase faz ligação automática de identidades com o mesmo e-mail; isso precisa ser validado nas duas contas. Se signup desabilitado impedir um login, revisar o provisionamento autorizado, sem abrir cadastros globais. [Identity linking](https://supabase.com/docs/guides/auth/auth-identity-linking).
5. Revisar o draft atualizado [SQL privado](../sql/averon_private_pilot.sql) no projeto Averon novo. Não aplicá-lo em projeto existente: nunca foi aplicado e não é uma migração de dados. Expor apenas `averon_private` para os sete RPCs privilegiados, sem expor `auth` ou dar acesso de tabelas/RPCs ao browser. Criar uma membership por **UUID Auth verificado** das duas pessoas, no workspace autorizado, com `research:read`/`research:write`; negar todas as demais.
6. As sete variáveis backend e quatro públicas frontend permanecem as descritas no [checklist do piloto](SUPABASE_PILOT_SETUP.md). Não há Client Secret Google no código do app. CORS permanece exatamente `https://averon-tools.vercel.app`. Ativação requer novo build/export e rollout explicitamente autorizados; o export fechado continua como padrão local.

## Callback, sessão e cancelamento

O botão inicia o SDK somente após ação humana, com `signInWithOAuth`, PKCE e `prompt=select_account`. Não solicita `access_type=offline`, refresh token Google, One Tap ou acesso a serviços Google. A rota estática `/auth/callback/` funciona no export para `public_html`; nenhum servidor Next.js é necessário.

Somente o verifier PKCE e timestamp da tentativa atravessam o redirect em `sessionStorage`, com validade local de cinco minutos. Sessões Supabase ficam no armazenamento em memória do SDK; tokens do provider Google são descartados nesse adapter. Cada instância usa um canal aleatório do SDK para evitar compartilhamento por BroadcastChannel entre abas. A troca de código é manual, ocorre uma vez, exige a tentativa/verifier locais e remove parâmetros/fragmentos da URL antes de carregar o SDK. [PKCE](https://supabase.com/docs/guides/auth/sessions/pkce-flow).

Após callback, a navegação é fixa para `/scanner-salesforce/pesquisa/` pelo router do cliente, preservando a sessão somente nessa execução do browser. Recarregar exige novo login. Login recebido não concede acesso: API verifica Auth, sessão ativa e membership em cada operação. Cancelamento/logout bloqueiam imediatamente o acesso local; logout mantém a sessão SDK apenas até concluir a revogação remota, antes de descartá-la. Respostas atrasadas não restauram acesso. O logout encerra a sessão do app nesse navegador, sem encerrar a conta Google.

## Histórico e isolamento

`/v2/history?offset=0` lista até 20 registros; `/v2/history/{uuid}` abre um relatório. Apenas `subject`/workspace do principal verificado definem o owner. Nenhum owner é aceito do body/query e nenhuma identidade interna é retornada. Cache, single-flight, registros e leituras são separados por `(workspace, user_id)`; RPCs também exigem membership ativa para esse owner.

Cache continua válido por até uma hora. Histórico mantém até **30 dias e 128 relatórios por usuário**, payload até 512 KiB, ordenação estável por `saved_at`/UUID e paginação limitada. Pesquisas novas do mesmo domínio preservam relatórios anteriores; hit de cache reutiliza o mesmo registro e não cria uma coleta nova. Abrir histórico não coleta HTML nem cobra quota de pesquisa; datas e conclusões originais permanecem intactas e a UI mostra aviso de histórico, sem afirmar condição atual. Limite de pesquisa continua dez alvos/minuto **compartilhado por workspace**, inclusive cache.

Expiração histórica é lógica; limpeza física ocorre ao salvar outro relatório do mesmo owner, sem cron/job provisionado. Logout/limpar apagam a visualização local, sem apagar histórico no banco. Um usuário não lê cache, relatórios ou histórico do outro mesmo conhecendo UUID/domínio. Tabelas continuam com RLS forçada e sem privilégios browser; chave secreta backend bypassa RLS, tornando obrigatórios os filtros de owner nos RPCs e a autorização da API.

## Antes de ativar

Validar nas duas contas reais: consentimento mínimo, contas não autorizadas negadas, callback/redirect exatos, reload e interrupção, logout/revogação, troca de usuário, histórico persistindo em novo login, isolamento no mesmo workspace, registros antigos apresentados como históricos, limites/quota e Auth/PostgREST reais. Testar também múltiplas conexões Postgres e Safari/iPhone. A verificação offline usa somente fixtures e não demonstra configuração hospedada ou identidade Google real. Publicação e coleta real continuam pendentes de autorização.

Ícone do botão: asset oficial Google, hospedado localmente, sem script externo: [origem](https://developers.google.com/static/identity/images/g-logo.png) e [diretrizes](https://developers.google.com/identity/branding-guidelines).

## Verificação local concluída

Passaram 50 testes v2, 31 testes do piloto e nove de isolamento/histórico (90 backend), 55 frontend, cinco de contrato FastAPI/TypeScript, 15 checks SQL em memória e 11 checks no Chrome com quatro capturas. Duas identidades fictícias usam o SDK real com HTTP interceptado, sem rede real Google/Supabase; login, callback, logout, troca de conta, histórico salvo, reload e cancelamento foram exercitados. Lint/tipos/build/export final fechado passaram. Evidências atuais: `Site-Averon/evidence/google-*` e cópias backend `evidence/google-*`. Os recibos do piloto anterior permanecem intactos.
