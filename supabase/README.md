# AveronTools migrations

CLI 2.120.0 generated both migration files. Their SQL is copied from the reviewed
private pilot and catalog drafts, authorized on 2026-10-08. It creates no users,
memberships, workspace, moderator or company records.

`config.toml` is for local development. It does not configure hosted Google OAuth
and must not be pushed as a replacement for the project's full hosted config.
The private schema has no grants for `anon` or `authenticated`; only the trusted
backend uses its RPCs with explicit user/workspace predicates.

The approved target is `ytqhclqdulkuwnuqtxyk` (AveronTools). Check its migration
history before applying files. An interrupted operation is not proof of success.
When applying via MCP, record the versions returned by the remote migration
history and reconcile local names before subsequent CLI pushes. Do not apply the
same SQL a second time or fabricate migration history entries.

The GitHub integration's working directory is `.`. Keep automatic branching and
Deploy to production disabled. This repository may be connected without enabling
automatic migration deployment or buying the Pro plan.

Never put OAuth client secrets, backend secret keys, user sessions or pilot emails
in this directory. Provision the approved accounts through Supabase Auth and
assign their real UUIDs separately. See `docs/REVIEWED_CATALOG_SETUP.md`.
