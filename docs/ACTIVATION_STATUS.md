# Ativação AveronTools — 8 de outubro de 2026

**Retomada às 23:49 UTC de 8/10, verificação às 00:27 UTC de 9/10:** Alef
autorizou retomar a aplicação SQL. A nova chamada MCP não devolveu confirmação e
foi interrompida. As leituras finais ainda mostram zero migrations e zero
tabelas em `averon_private`. A alternativa no SQL Editor não confirmou inserção;
nenhuma query foi executada. O processo próprio de teclado foi encerrado, sem
gravação pendente. A autorização SQL já está registrada; falta concluir sua
execução por um caminho autorizado disponível.

Foi preparado fora do Git `artifacts/averon-approved-atomic-migrations.sql`,
transação que executa os dois arquivos revisados e registra suas versões e SQL
originais juntos, usando o formato da CLI oficial 2.120.0. Quatro checks adicionais
passaram no PGlite: versões corretas, conteúdo íntegro, sete tabelas/doze RPCs e
recusa de reaplicação. Esse arquivo de recuperação não foi executado no banco e
não deve ser passado a `apply_migration`, que gerencia o próprio histórico; pelo
MCP/CLI normal, utilizar os dois arquivos originais. O estado da retomada está em
`../artifacts/averon-resume-results.json`, a partir da raiz do checkout.

Alef autorizou as quatro etapas: Google OAuth, banco/grants/associações,
configuração e validação do Railway, e integração Supabase–GitHub.

## Configuração persistida e conferida

- Supabase AveronTools: `ytqhclqdulkuwnuqtxyk`, plano Free.
- Integração GitHub conectada a `AleefRibeiro/VerificaSalesforce`, diretório `.`.
  Após recarregar, o painel mostrou a integração habilitada, com Deploy to
  production, Automatic branching e Supabase changes only desligados. A
  autorização GitHub da organização já existia; nenhum novo grant foi concedido.
- Auth Site URL: `https://averon-tools.vercel.app`. Um único redirect permitido:
  `https://averon-tools.vercel.app/auth/callback/`. Ambos conferidos em nova leitura.
- Inscrição pública desligada; o endpoint público `/auth/v1/settings` confirmou
  `disable_signup=true`. A interface mostrou login anônimo e vinculação manual
  desligados. O endpoint público não informa o estado anônimo; não inferir esse
  valor de um campo ausente.
- Access token expiry: 600 segundos, confirmado após nova navegação. O intervalo
  de reutilização de refresh token permanece em 10 segundos. Sem recurso Pro.
- Google permanece desligado. Nenhum cliente OAuth ou usuário foi criado.
- Railway: projeto `4627e89b-c14f-45ba-a58a-46c9d23d8092`, serviço
  `23bcc336-7160-4ae9-a296-c07a434e4233`, ambiente production
  `710ea8f9-5450-46a8-98ea-938a9bd7113e`.
- Seis variáveis públicas/configuração foram adicionadas com `skipDeploys=true`:
  `AVERON_PILOT_ENABLED=disabled`, ref e publishable do AveronTools,
  `AVERON_WORKSPACE_ID=a2b34109-4dcb-4aa7-a670-f6a614d2b92d`,
  `AVERON_CORS_ORIGINS=["https://averon-tools.vercel.app"]` e
  `AVERON_PUBLIC_HTML_ENABLED=disabled`. A leitura do serviço confirmou os seis
  nomes. A chave privada não foi lida, criada ou inserida.

## Migrations prontas, aplicação interrompida

A CLI oficial 2.120.0 foi baixada somente em `/tmp`; SHA256 do pacote verificado
com o checksum publicado: `3b8546cc61aeabab6fd1f68edc7f664ebdfa96bdd6a9b18d8d612708f430ae28`.
A CLI gerou:

- `supabase/migrations/20261008160647_averon_private_pilot.sql`
- `supabase/migrations/20261008160657_averon_reviewed_catalog.sql`

Ambas correspondem ao SQL revisado, exceto o comentário de autorização. TOML
validado; seeds, inscrição pública e usuários anônimos locais desligados. O
config local não substitui a configuração hospedada e não foi enviado ao Auth.
Os 29 checks PGlite do SQL passaram novamente, sem conexão remota.

A chamada `supabase_apply_migration` foi cancelada. As leituras subsequentes
confirmaram `migrations=[]` e nenhuma tabela em `averon_private`. Não repetir
uma aplicação sem conferir o histórico. Foi solicitada confirmação para retomar
essa chamada cancelada. Nenhum grant/tabela/workspace/membership/moderador remoto
foi criado. O UUID de workspace acima é reservado para a configuração; ainda
não existe como linha no banco.

O branch `codex/scanner-research-v2` foi publicado no GitHub, sem merge.
O commit da preparação de migrations é
`afefa2735c42452e21459c50faa187bc4930aef4`.
O diretório `supabase/` está nesse branch; ainda não foi incorporado ao main.

## Informações e acesso que faltam

1. Projeto Google Cloud escolhido e os dois e-mails Google autorizados. A conta
   atual tinha outro projeto selecionado; não foi encontrado um projeto Averon
   na lista completa. Nenhum projeto GCP foi modificado. Foi perguntado se deve ser criado um
   projeto AveronTools sem faturamento/APIs pagas ou usado outro indicado por Alef.
2. Retomada da aplicação SQL cancelada. Depois, expor `averon_private` somente
   para os RPCs do backend, conferir grants/RLS, criar o workspace e associar os
   UUIDs Auth reais das duas contas, com Alef como único moderador. Não usar UUIDs
   ou empresas fictícias no banco remoto.
3. Chave privada `sb_secret_…` diretamente no gerenciador seguro Railway, com o
   nome `AVERON_SUPABASE_SECRET_KEY`; nunca em chat, Git, frontend ou arquivos de
   evidência. A autorização de configuração já existe; a credencial continua
   necessária. Não ler sessões/credenciais locais.
4. Validar Google Auth, sessão revogada, duas contas, PostgREST/RPC, isolamento,
   CORS e moderação hospedados antes de liberar o frontend.

O Railway continua no deployment `81ad0bec-506e-4bec-b5e7-b099f9ae44c2`, SUCCESS,
source main, start `uvicorn main:app --host 0.0.0.0 --port $PORT`. Nenhum cutover
ou novo deploy foi feito enquanto faltam banco, segredo e OAuth. O frontend
continua no commit local `c7bf379f6fa60474a349551ebfb2426e8bdedee7`, sem nova
publicação. O checkout institucional continua limpo no main
`9bf48deea215ff2dc86d099453b406b1e6c59461`.

Evidências locais fora do Git: `../artifacts/averon-activation-readbacks.json`,
`../artifacts/averon-public-auth-status.json`,
`../artifacts/activation-catalog-sql-check.json` e
`../artifacts/activation-pilot-sql-check.json`, a partir da raiz do checkout.
Esses caminhos são do workspace da tarefa e não serão publicados no repositório.
