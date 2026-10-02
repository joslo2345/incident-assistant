# Installing the Incident Assistant

For a platform team installing the system into their own Kubernetes cluster. Two paths:

| Path | What it gives you | Status |
| --- | --- | --- |
| **A. Local Kubernetes (kind)** | The whole system on a laptop, with NetworkPolicies enforced | Tested end to end from an empty Docker: 1 min 45 s to all pods ready |
| **B. Azure (AKS)** | Terraform for network, AKS, managed Postgres, registry, Key Vault, GitHub OIDC; the same chart | Written, `terraform validate`d, Trivy-scanned and schema-checked; **not yet applied to a subscription** |

The chart (`deploy/helm/incident-assistant`) is the same in both. What changes is where images,
secrets and Postgres come from.

## What gets installed

| Component | Kind | Port | Talks to |
| --- | --- | --- | --- |
| web (React UI + nginx) | Deployment | 8080 (svc 80) | agent |
| ingest (telemetry API) | Deployment | 8000 | redpanda |
| consumer | Deployment | 9101 metrics | redpanda, Postgres |
| detector (1 replica) | Deployment | 9102 metrics | Postgres |
| knowledge (search + RAG) | Deployment | 8001 | Postgres |
| agent (investigations, people API) | Deployment | 8002 | Postgres, knowledge, model endpoint |
| slack-bot (optional) | Deployment | - | agent, Postgres, Slack |
| timescaledb (if in-cluster) | StatefulSet | 5432 | - |
| redpanda | StatefulSet | 9092 | - |
| migrate, topics, kb-ingest | Jobs, per release revision | - | Postgres, redpanda |

Every pod runs as non-root with a read-only root filesystem, no Linux capabilities and the
runtime seccomp profile, with requests, limits, and readiness and liveness probes. Services wait
for their dependencies (init containers) instead of crash-looping. NetworkPolicies deny all
ingress by default and allow only the edges in the table; data stores get no egress beyond DNS.

**Secrets** (one Kubernetes Secret, default name `incident-assistant-secrets`): `POSTGRES_PASSWORD`,
`CONSUMER_DB_PASSWORD`, `GRAFANA_DB_PASSWORD`, `DETECTOR_DB_PASSWORD`, `KNOWLEDGE_DB_PASSWORD`,
`AGENT_DB_PASSWORD`, `APPROVAL_DB_PASSWORD`, `KNOWLEDGE_API_KEY`, `AGENT_API_KEY`,
`SLACK_BOT_API_KEY`, `INGEST_API_KEYS`; optional `ANTHROPIC_API_KEY`, `SLACK_BOT_TOKEN`,
`SLACK_APP_TOKEN`. Values never go in Helm values or Git: create the Secret yourself (path A) or
let External Secrets fill it from Key Vault (path B).

**Model endpoint**: any OpenAI-compatible server (`model.baseUrl`, `model.name`), or Anthropic
(`model.provider: anthropic` plus `ANTHROPIC_API_KEY`). The evaluated configuration is Qwen3
30B-A3B on Ollama (docs/RESULTS.md).

## A. Local Kubernetes (tested)

Prerequisites: Docker (8 GB for the Docker VM), `kind`, `kubectl`, `helm`, `uv`, Ollama with the
model (`make model`).

```sh
make env              # random local secrets in deploy/.env (gitignored)
make kind-up          # kind cluster + Calico (enforces NetworkPolicy)
make kind-install     # build images, load them, create the Secret, helm install --wait
scripts/k8s_smoke.sh  # accounts, replay 6 h with 3 faults, detect them in the cluster
```

Open http://localhost:3001, sign in as `alice` (password `DEMO_APPROVER_PASSWORD` in
`deploy/.env`), choose the `smoke-v3-residual` run, open the thermal incident and press
**Investigate now**: the agent in the cluster calls Ollama on the host and files a drain request
you can approve. `node web/scripts/screenshots.mjs` drives the same flow in a browser.

Measured on an M3 Pro (Docker VM: 8 GB, 12 CPUs): cluster + Calico + image build + install in
**1 min 45 s**; all pods ready with **0 restarts**; ingest accepted 138,048 events at
**41,600/s**; all 3 injected faults detected in the cluster; the browser flow (investigate,
approve, audit, follow-up, viewer without approve buttons) passed.

Teardown: `make kind-down` (deletes the cluster and its data).

## B. Azure (written and validated, not yet applied)

Prerequisites: an Azure subscription with Owner (role assignments are created), `az` logged in,
Docker (Terraform runs from its official image via `scripts/terraform.sh`), `helm`, and a
storage account for Terraform state.

1. **Infrastructure**

   ```sh
   cp infra/azure/terraform.tfvars.example infra/azure/terraform.tfvars   # edit it
   scripts/terraform.sh infra/azure init \
     -backend-config="resource_group_name=<state rg>" -backend-config="storage_account_name=<sa>" \
     -backend-config="container_name=tfstate" -backend-config="key=incident-assistant.tfstate"
   scripts/terraform.sh infra/azure apply
   scripts/terraform.sh infra/azure output github_variables
   ```

   Creates: VNet, AKS (Free tier control plane, 1 × Standard_B4ms, Calico, workload identity,
   Entra RBAC, no local accounts, API server limited to `admin_ip_ranges`), Postgres Flexible
   Server (private, TLS required, TimescaleDB + pgvector allow-listed), ACR (no admin user),
   Key Vault (RBAC, purge protection, closed except your IPs and the AKS subnet) with every
   generated secret, an identity for External Secrets, and an Entra app that GitHub Actions uses
   through OIDC (no client secret).

2. **External Secrets**: install the operator (its Helm chart, namespace `external-secrets`), fill
   in `infra/azure/k8s/cluster-secret-store.yaml` from the outputs and apply it.

3. **Deploy**: put the `github_variables` output in the repo's `production` environment as
   variables, then run the **Deploy to AKS** workflow. It builds and scans the images, pushes
   them to ACR, and runs `helm upgrade --install` with `deploy/helm/values-azure.yaml` through
   `az aks command invoke`, so the API server stays closed to the internet.

4. **Model**: point `model.baseUrl` at an endpoint the cluster can reach. For demos without GPU
   spend, the laptop's Ollama over a private tunnel (e.g. the Tailscale Kubernetes operator's
   egress proxy); for production, vLLM on a GPU node pool.

**Estimated cost** (Azure list prices, westus2, pay-as-you-go, from the Azure Retail Prices
API on 2026-10-02; not a measured bill):

| Resource | Price | Per day |
| --- | --- | --- |
| AKS control plane, Free tier | $0 | $0.00 |
| AKS node, Standard_B4ms Linux | $0.166/h | $3.98 |
| Node OS disk, P10 | $17.92/month | $0.60 |
| Postgres Flexible, B1ms compute | $0.017/h | $0.41 |
| Container Registry, Basic | $0.1666/day | $0.17 |
| Public IP, Standard static | $0.005/h | $0.12 |
| **Total** | | **≈ $5.30/day** |

Not priced: Postgres storage (32 GB), load-balancer rules and data transfer, and Key Vault
operations (cents). A budget with alerts at 50/80/100% is created when `budget_alert_email` is set.

Teardown: `scripts/terraform.sh infra/azure destroy`. Key Vault has purge protection: its name
is held for 7 days after deletion.

**Not verified until it runs against a real subscription:** the migrations on Azure Postgres
(the admin isn't a superuser; extension creation and role grants should work, but untested),
External Secrets syncing from Key Vault, `az aks command invoke` with this chart, and ingress
TLS. Treat path B as a reviewed starting point, not a tested install.
