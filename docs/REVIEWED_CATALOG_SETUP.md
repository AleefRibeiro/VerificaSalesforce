# Catálogo Salesforce e configuração do AveronTools

O catálogo público contém somente informações aprovadas pelo Alef. Pesquisas novas ficam no histórico privado de quem as solicitou. Contribuições ficam pendentes até revisão; não há importação automática de relatórios privados nem dados reais ou empresas de demonstração no produto.

## Projeto confirmado

Em 8 de outubro de 2026, o painel e a consulta oficial confirmaram `AveronTools`, referência `ytqhclqdulkuwnuqtxyk`, organização `rxreodfgqwfnknokmuca`, região `us-west-2`, estado `ACTIVE_HEALTHY`. O painel mostra o plano Free. A URL pública fornecida corresponde a `https://ytqhclqdulkuwnuqtxyk.supabase.co`. A chave publishable fornecida foi preparada em um arquivo local externo aos repositórios, com a ativação desligada. Nenhuma chave privada foi lida.

O projeto já existe: não é necessário criar outro nem excluir projetos para liberar vaga. Na leitura anterior, Fiui e Plantoniza estavam pausados; GearheadEvents e painel-financeiro-pessoal estavam ativos. Plantoniza foi identificado em outra organização gerenciada pelo Vercel Marketplace, referência `pxcivzfynvgibljkknyx`. InquiPass não foi identificado. Nenhum desses recursos foi alterado por esta implementação.

## Fluxos e autorização

- `GET /catalog/companies?q=...&offset=0`: consulta pública por início do nome ou domínio; 20 registros por página, uma fonte por registro para manter a resposta limitada. A busca normaliza nomes em português e URLs. Não coleta fontes, cobra quota de pesquisa ou lê relatórios privados.
- `GET /catalog/companies/{domain}`: registro público completo com até 20 fontes revisadas preservadas. Mostra data da fonte quando conhecida e data editorial separada. Todas as respostas usam `no-store`, permitindo que uma revisão apareça na próxima leitura.
- `POST /v2/research` e `/v2/research/batch`: exigem sessão Google validada e permissão de escrita. A API verifica primeiro o catálogo; um domínio já aprovado interrompe a coleta antes de quota ou fornecedor. Um lote com registro aprovado deve ser revisto antes do envio.
- `POST /v2/catalog/contributions`: exige nome, domínio, confirmar/contestar, motivo e link HTTPS público, sem parâmetros ou fragmentos. A data publicada pode ser nula. Limites SQL: 10 envios por minuto e 20 pendentes por usuário; duplicatas pendentes são recusadas.
- `GET /v2/catalog/contributions`: somente as contribuições do UUID autenticado, sem identificadores pessoais no DTO.
- `GET /v2/catalog/moderation` e `POST /v2/catalog/moderation/{id}`: exclusivamente o UUID cadastrado para o Alef em `catalog_moderator`. A tabela aceita um único moderador. A API e cada RPC verificam a autorização, independentemente da interface e de `user_metadata`.

O backend valida o token exato no Supabase Auth, a identidade Google, AMR OAuth, sessão ativa, workspace e associação atual. Login por senha, convite, OTP ou outro provedor não concede acesso. Uma identidade Google vinculada a outra identidade OAuth é recusada até revisão da configuração. Convites podem provisionar o usuário, mas a sessão usada no app precisa ser OAuth Google. Somente as contas autorizadas do piloto podem pesquisar e contribuir; ampliar admissão exige decisão explícita.

As classificações públicas são Uso confirmado, Indício e Não confirmado. Uso confirmado exige contribuição de confirmação, fonte datada e declaração editorial de evidência direta. Aprovar contestação preserva fontes anteriores e inclui sua qualificação; rejeitar não muda o catálogo. Não confirmado nunca significa que a empresa não usa Salesforce. Datas antigas não confirmam uso atual. A aprovação publica apenas nome/domínio, status, qualificação editorial, link e datas; autor, motivo bruto e relatório privado ficam fora do catálogo.

## SQL preparado e dados privados

Aplicação remota ainda depende de aprovação específica. A ordem de revisão é `sql/averon_private_pilot.sql`, depois `sql/averon_reviewed_catalog.sql`. São sete tabelas privadas com RLS habilitada e forçada, doze funções `SECURITY INVOKER` com `search_path` fixo e sem grants para `anon`/`authenticated`. O catálogo é público por meio da API; o navegador não consulta as tabelas privadas diretamente.

Os grants propostos são limitados ao backend `service_role`, incluindo os mínimos campos de `auth.sessions` para verificar revogação. Essa role pode ignorar RLS; os predicados explícitos de workspace, UUID e moderação são obrigatórios. Nenhum usuário, workspace, associação, moderador ou empresa é semeado pelo SQL. Os UUIDs reais precisam ser fornecidos pela configuração autorizada do Auth, nunca pelo e-mail ou por metadados enviados pelo cliente.

Histórico privado: 30 dias e 128 relatórios por usuário; cache fresco de até uma hora. Leitura histórica mantém as datas originais. Contribuições e decisões editoriais são armazenadas separadamente; até 20 fontes aparecem no registro público completo. Não foi criado job, recurso pago ou exclusão automática de contribuições.

## Próximas ações que exigem aprovação específica

1. Criar/configurar um cliente OAuth Google do tipo Web no projeto GCP escolhido, somente com `openid email profile`, e as duas contas do piloto. Não pedir senha ou client secret no chat. Origem JavaScript: `https://averon-tools.vercel.app`. Callback Google para Supabase: `https://ytqhclqdulkuwnuqtxyk.supabase.co/auth/v1/callback`. Client ID/Secret entram somente nos campos seguros do provedor Google no Supabase.
2. Configurar Google no Supabase, Site URL `https://averon-tools.vercel.app` e redirect permitido exato `https://averon-tools.vercel.app/auth/callback/`. Manter inscrição pública/anônima desligada enquanto o piloto for restrito; provisionar/vincular as contas antes de permitir seu login. Validar o fluxo hospedado antes de abrir inscrições.
3. Revisar e autorizar o SQL, exposição restrita do schema `averon_private` ao backend, grants, workspace, duas associações de leitura/escrita e um único UUID moderador do Alef. Não cadastrar empresas reais antes da revisão editorial.
4. Colocar a chave privada de backend diretamente no gerenciador seguro do Railway, sem chat/Git/frontend. Configurar as sete variáveis documentadas em `SUPABASE_PILOT_SETUP.md` para este projeto. Manter `AVERON_PUBLIC_HTML_ENABLED=disabled` inicialmente. A URL existente permanece `https://verificasalesforce-production.up.railway.app`.
5. Validar Google Auth real, duas contas, revogação, PostgREST/RPC, CORS, isolamento e moderação hospedados. Autorizar o cutover do Railway para `research_pilot:create_configured_pilot --factory` antes de ativar o frontend. A fábrica preparada remove as rotas anônimas v1; a produção atual ainda usa `main:app` e não foi alterada.
6. Somente então autorizar build/publicação com `NEXT_PUBLIC_AVERON_PILOT=enabled`, e eventual habilitação da fonte pública de HTML. Custos de Railway/egress não foram quantificados nem autorizados adicionalmente.

## GitHub e Supabase

O repositório indicado é [AleefRibeiro/VerificaSalesforce](https://github.com/AleefRibeiro/VerificaSalesforce), que contém o backend e o SQL. O frontend está em Site-Averon; não deve ser conectado por suposição como dono do schema. Os dois drafts SQL estão versionados na branch local `codex/scanner-research-v2`. Ainda não existem migrations aplicadas neste projeto pela implementação.

A [integração GitHub e deployment funcionam em todos os planos](https://supabase.com/docs/guides/deployment), inclusive Free. Preview environments por branch exigem Pro. Não contratar Pro nem ativar automatic branching. A [integração oficial](https://supabase.com/docs/guides/deployment/branching/github-integration) precisa de autorização GitHub para ler commits, branches, PRs e arquivos do repositório escolhido. Antes da conexão, apresentar as permissões exatas mostradas no consentimento, limitar ao repositório aprovado e confirmar esse acesso persistente.

Antes de habilitar integração, converter os drafts revisados para migrations e configuração no diretório `supabase/` usando a CLI oficial; a CLI não está instalada neste ambiente e nenhum nome de migration foi inventado. Não ligar Deploy to production: essa opção aplica migrations e pode publicar Functions/Storage ao receber push ou merge. Conectar Git não equivale a autorizar alterações automáticas na produção. Não houve push, merge, grant OAuth, conexão de repositório ou deploy nesta etapa.

## Fontes técnicas verificadas

- [Supabase Google OAuth](https://supabase.com/docs/guides/auth/social-login/auth-google)
- [Claims JWT e AMR](https://supabase.com/docs/guides/auth/jwt-fields)
- [Row Level Security e grants](https://supabase.com/docs/guides/database/postgres/row-level-security)
- [Schemas privados no Data API](https://supabase.com/docs/guides/api/using-custom-schemas)

Os testes locais usam apenas Auth/RPC/HTML simulados, PostgreSQL em memória e um perfil temporário do Chrome. Esses testes não substituem a validação hospedada antes da ativação.
