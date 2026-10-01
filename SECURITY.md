# Security

## Scope and threat model

The local stack (`make up`) is a **single-developer environment**. It has no TLS and no Kafka
authentication, so it is built to be unreachable from the network rather than safe on it:

- Every published port is bound to `127.0.0.1`. Nothing listens on the LAN.
- Secrets are random per machine (`make env` → `deploy/.env`, mode 600, gitignored). Compose
  refuses to start without them, so there are no default passwords.

Hardening for real deployments (TLS everywhere, Kafka SASL, a cloud secret manager, network
policies, image signing) is work package **A8**.

## Controls in place

| Area | Control |
| --- | --- |
| Knowledge API | Random API key from `deploy/.env`; answers only from retrieved sources, with citations validated server-side; the Claude key (optional) stays in `deploy/.env` |
| Agent API | Two key sets: agent keys (investigate, read traces) and approver keys (decide). An agent key can't decide |
| Agent | Seven tools, all read-only except `drain_node`, which only files a request; tool allowlist per run; arguments validated before any query; results size-capped; step, token and time budgets. The model runs locally (Ollama, 127.0.0.1 only), so incident data never leaves the machine |
| Ingest API | API key (constant-time compare, fails closed with no keys); 10 MB body limit checked before parsing (also for chunked uploads); schema validation with unknown fields rejected; bounded in-flight events (429); errors don't expose internal details |
| Database | Services use least-privilege roles: `telemetry_writer` (SELECT/INSERT on telemetry tables), `grafana_reader` (SELECT only, read-only transactions, 30 s statement timeout), `incident_detector` (SELECT on telemetry, read/write on `incidents` only), `knowledge_service` (read/write on `kb_chunks` only), `incident_agent` (SELECT on telemetry and incidents, writes its own traces, INSERT-only on `approval_requests`), `approval_service` (UPDATE of the decision columns of `approval_requests` only). A trigger makes decisions final and rejects self-approval. The superuser is used only by migrations. Role passwords come from the environment, never from SQL files. |
| Grafana | Anonymous access off (it would let anyone send raw SQL to the datasource); random admin password; sign-up disabled; template variables in SQL always use `${var:sqlstring}` escaping |
| Containers | Non-root user; base images pinned by digest; Debian security updates applied at build time |
| Supply chain | `uv.lock` pins every dependency; GitHub Actions pinned to commit SHAs; Dependabot for Python, Actions, Docker; the trace download is checksum-verified |
| CI | gitleaks (full history), pip-audit, Trivy config scan, and a Trivy image scan that fails on fixable HIGH/CRITICAL vulnerabilities |

## Review log

**2026-10-01, before A3.** Scanned with gitleaks, pip-audit, Trivy (images and config) and
bandit, plus a manual review of configuration and code paths.

| Finding | Severity | Status |
| --- | --- | --- |
| Anonymous Grafana users could run arbitrary SQL as the Postgres superuser (verified) | Critical | Fixed: anonymous off, read-only `grafana_reader` role |
| All ports (DB, Kafka, Redpanda admin, Grafana, Prometheus, API) reachable from the LAN (verified) | High | Fixed: bound to 127.0.0.1 |
| Default credentials (`postgres/postgres`, `admin/admin`) | High | Fixed: generated secrets, required by Compose |
| Consumer connected as superuser | Medium | Fixed: `telemetry_writer` role |
| Dashboard template variables interpolated into SQL unescaped | Medium | Fixed: `sqlstring` formatting |
| No request body limit; unauthenticated clients could make the API parse huge bodies | Medium | Fixed: 413 above 10 MB |
| 7 fixable HIGH CVEs in the base image (OpenSSL, PCRE2) | Medium | Fixed: security updates at build time; CI gate |
| Mutable tags for actions and base images | Medium | Fixed: SHA/digest pins plus Dependabot |
| 503 responses included broker error text | Low | Fixed: generic message, details logged |
| No secrets in git history or tree; no vulnerable Python dependencies | n/a | Verified clean |

**2026-10-01, A5 (agent).** pip-audit clean; Trivy finds 0 fixable HIGH/CRITICAL in the agent
image. Design review of the agent's permissions:

| Risk | Mitigation |
| --- | --- |
| Prompt injection through tool results (BMC messages, runbook text) steering the model | The model has no way to act: its only write is a pending request, decided by a person with a different key and database role |
| Model approving its own request | `incident_agent` has no UPDATE on requests; trigger rejects deciders named `agent*` or equal to the requester (integration test) |
| Model citing sources it never saw | Citations are checked against chunks returned in the same run; rejected otherwise |
| Runaway loops or huge tool output | Step, token, per-call and per-run time budgets; results capped at 12k characters |

**Accepted for now (local-only stack, addressed in A8):**
- The agent container holds the `approval_service` password for its decision endpoint; the agent
  loop never uses it. Splitting approvals into their own service fits A7 (UI) or A8.
- The MCP server runs over stdio for local clients only; it has no authentication of its own.
- Kafka, the Redpanda admin API and Prometheus have no authentication (localhost-only).
- `/metrics` on ingest is unauthenticated (localhost-only; NetworkPolicy in A8).
- 44 HIGH base-image CVEs with no Debian fix yet (util-linux and login tools). The services never
  call these binaries and run as non-root. The CI gate fails as soon as a fix ships.
- The ingest API key defaults to `dev-key` in `deploy/.env` for replayer convenience. It only
  lets someone with local access write telemetry.
- Inside the database container, loopback connections are trusted (the image default). Host and
  network connections require scram-sha-256 passwords.
- The repository is private, and branch protection needs a paid plan for private repos.

## Reporting

This is a personal portfolio project. Report issues privately to the repository owner.
