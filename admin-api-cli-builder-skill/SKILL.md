---
name: admin-api-cli-builder
description: Use when the user wants a small tool or CLI that logs in with an administrator account, reads project-local .env configuration, obtains an admin token or session, and then calls one or more admin-only APIs to complete batch workflows such as invite/activate/import/sync/update operations. Best for internal tooling built from curl samples, HAR files, or API snippets.
---

# Admin API CLI Builder

Build small internal tools that:

- read config from the current project `.env`
- log in with an admin account when needed
- obtain admin token/session/cookies
- call admin-only APIs
- support batch execution
- write deterministic result files

Resources:

- Intake template: see [references/intake-checklist.md](references/intake-checklist.md) when collecting inputs from the user.
- Default project conventions: see [references/default-project-shape.md](references/default-project-shape.md) when deciding file layout and output behavior.
- Copyable env template: see [assets/templates/.env.example](assets/templates/.env.example) when creating a new tool project.

## Use this skill when

- The user gives `curl` snippets, HAR files, or API docs.
- The workflow depends on an admin login, admin token, or admin cookie.
- The user wants a command-line tool instead of a UI.
- The task is repetitive: invite, activate, batch import, batch update, sync, cleanup, migration, verification.

## Default build shape

- Language: prefer Python unless the user asks otherwise.
- Config: read `.env` from the current project directory.
- Entry: one CLI script with subcommands like `run`, `invite`, `activate`, `sync`.
- Output: write fixed result files under `outputs/`.
- Auth: encapsulate login/token/session handling in one client class.

## Required inputs

Ask for or extract these before coding. Use [references/intake-checklist.md](references/intake-checklist.md) when you want a copyable checklist:

1. Environment info
- `base_url`
- `org_uuid`
- `team_uuid`
- `region_uuid`

2. Admin auth flow
- login endpoint
- whether password must be encrypted
- whether token comes from login directly or from a second OAuth/token step
- whether cookies are required in addition to `Authorization`

3. Business APIs
- endpoint list
- request method
- required headers
- request body/query/path params
- success and failure payload samples

4. Batch rules
- input count or input file format
- random data generation rules if any
- retry/polling requirements
- output fields the user wants preserved

## Working process

1. Reconstruct auth first
- Do not assume the login curl is enough.
- If a HAR is provided, inspect the full browser sequence.
- Verify whether the browser uses a different encryption cert, PKCE flow, token exchange, or cookie bootstrap.

2. Normalize config
- Put environment-dependent values in project `.env`.
- Keep command-line flags available, but let `.env` provide defaults.

3. Build a small API client
- isolate login/auth refresh/token parsing
- isolate business endpoints
- keep request headers consistent
- centralize error formatting

4. Add batch execution
- support `--count` or file-driven input
- support polling when server-side records appear asynchronously
- emit stable CSV or JSON output

5. Verify the real path
- first validate syntax locally
- then run a small real batch if the user wants execution
- if auth fails, compare against HAR/browser behavior instead of guessing

## Implementation rules

- Prefer standard library first when practical.
- If third-party dependencies are necessary, keep them minimal.
- Default output paths:
  - `outputs/run-result.csv`
  - `outputs/invite-result.csv`
  - `outputs/activate-result.csv`
- Keep secrets in `.env`, not hardcoded in code.
- Do not assume the same public key or token flow across environments.

## Common pitfalls

- Using the wrong encryption certificate source for login.
- Assuming the login response already contains the final bearer token.
- Missing cookies required by downstream APIs.
- Reading only one request from HAR instead of the whole auth chain.
- Hardcoding environment IDs in code instead of `.env`.
- Creating timestamped output files when the user wants fixed filenames.

## Expected deliverables

For each tool project, produce:

- one runnable CLI script
- one project-local `.env`
- one concise `README.md`
- deterministic output files under `outputs/`

Use [assets/templates/.env.example](assets/templates/.env.example) as the starting point for the project-local `.env`.

## Suggested command pattern

Prefer commands like:

```bash
python3 tool.py run --count 10
python3 tool.py invite --count 20
python3 tool.py activate --input outputs/invite-result.csv
```

## If the user provides only partial auth info

- inspect HAR if available
- identify login encryption, token exchange, and cookies
- verify the browser flow before declaring the password wrong
