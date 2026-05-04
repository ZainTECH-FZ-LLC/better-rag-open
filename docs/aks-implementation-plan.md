# AKS Deployment Implementation Plan for better-rag (Dev + Prod)

## Summary
Create a detailed, implementation-ready AKS deployment plan for the better-rag system, based on `c:\Users\PC\.claude\plans\glowing-inventing-sprout.md`. The plan targets Dev and Prod environments, uses managed PaaS data services, Helm + kubectl, Azure Application Gateway Ingress Controller (AGIC), Workload Identity, Azure Container Registry (ACR), Azure Monitor/Container Insights, and private AKS with private endpoints.

## Source Context
Key runtime details are derived from the current system plan, including:
1. Workloads: FastAPI API, Celery workers (query, docgen, ingestion), Celery beat, Open WebUI.
2. Data services: Postgres with pgvector, Neo4j, Redis, Azure Blob Storage.
3. Concurrency and resource intent: query workers with gevent, docgen workers with prefork, ingestion workers with prefork.
4. Redis DB layout and LangGraph checkpoints.

## Target Architecture Mapping
AKS workloads:
1. `api` (FastAPI, uvicorn workers).
2. `query-worker` (Celery, gevent pool).
3. `docgen-worker` (Celery, prefork pool).
4. `ingestion-worker` (Celery, prefork pool).
5. `celery-beat`.
6. `open-webui`.

Managed services:
1. Azure Database for PostgreSQL Flexible Server (pgvector).
2. Azure Cache for Redis.
3. Neo4j managed (Aura) or VM with private endpoint.
4. Azure Blob Storage.
5. Azure Document Intelligence.
6. Azure OpenAI.

Ingress and identity:
1. Azure Application Gateway Ingress Controller (AGIC).
2. Workload Identity with Managed Identities.

## Environments
1. Dev and Prod use separate resource groups and namespaces.
2. Namespaces: `betterrag-dev`, `betterrag-prod`.
3. Environment-specific Helm values files drive resource sizing and endpoints.
4. Promotion workflow: Dev deploy validates, then the same chart version is promoted to Prod.

## Infrastructure Provisioning (Azure)
1. Resource groups:
   1. `rg-betterrag-dev`
   2. `rg-betterrag-prod`
2. Virtual network and subnets:
   1. `aks-subnet`
   2. `appgw-subnet`
   3. `private-endpoints-subnet`
3. AKS cluster settings:
   1. Private API server.
   2. Azure CNI.
   3. OIDC issuer enabled.
   4. Workload Identity enabled.
4. ACR:
   1. Create ACR per environment or shared ACR.
   2. Grant AKS pull permissions (AcrPull).
5. PaaS services:
   1. Postgres Flexible Server with `pgvector` extension enabled.
   2. Azure Cache for Redis.
   3. Azure Storage account (Blob).
   4. Neo4j Aura or VM with private endpoint.
6. Private endpoints:
   1. Postgres, Redis, Storage, and Neo4j (if supported).
   2. Configure private DNS zones and link to VNet.

## Cluster Add-ons
1. Application Gateway and AGIC:
   1. Create Application Gateway in `appgw-subnet`.
   2. Install AGIC with managed identity access.
2. Observability:
   1. Enable Azure Monitor / Container Insights.
   2. Create Log Analytics workspace per environment.
3. TLS:
   1. Store certificates in Azure Key Vault.
   2. Configure App Gateway listener to use Key Vault cert.

## Container Images
1. Two images:
   1. `betterrag-api` for API + query worker.
   2. `betterrag-docgen` for docgen + ingestion workers (LibreOffice, Node.js, Poppler).
2. Build and push to ACR.
3. Tag strategy:
   1. Semver plus build SHA.
   2. Separate tags per environment.

## Helm Chart Structure
Create a single chart with environment values:
1. `charts/betterrag/Chart.yaml`
2. `charts/betterrag/templates/deployment-api.yaml`
3. `charts/betterrag/templates/deployment-query-worker.yaml`
4. `charts/betterrag/templates/deployment-docgen-worker.yaml`
5. `charts/betterrag/templates/deployment-ingestion-worker.yaml`
6. `charts/betterrag/templates/deployment-celery-beat.yaml`
7. `charts/betterrag/templates/deployment-open-webui.yaml`
8. `charts/betterrag/templates/service-api.yaml`
9. `charts/betterrag/templates/service-open-webui.yaml`
10. `charts/betterrag/templates/ingress.yaml`
11. `charts/betterrag/templates/configmap.yaml`
12. `charts/betterrag/templates/secret.yaml` (or External Secrets template)
13. `charts/betterrag/templates/hpa.yaml`
14. `charts/betterrag/values-dev.yaml`
15. `charts/betterrag/values-prod.yaml`

Namespace strategy:
1. Create namespaces ahead of Helm install or as part of Helm chart with `namespace` manifests.

## Configuration and Secrets
Key environment variables (derived from system plan):
1. Postgres:
   1. `PGVECTOR_HOST`, `PGVECTOR_DB`, `PGVECTOR_USER`, `PGVECTOR_PASSWORD`
   2. `PGVECTOR_POOL_SIZE`, `PGVECTOR_POOL_MAX_OVERFLOW`
2. Redis:
   1. `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`
   2. `REDIS_STREAMS_URL`
   3. `REDIS_CACHE_URL`
   4. `LANGGRAPH_CHECKPOINT_REDIS_URL`
3. Neo4j:
   1. `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`
4. Azure services:
   1. `AZURE_STORAGE_ACCOUNT`, `AZURE_STORAGE_CONTAINER`
   2. `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT`
   3. `AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT`
5. Graph API:
   1. `GRAPH_TENANT_ID`
   2. `GRAPH_CLIENT_ID` (if required by non-MI service)
6. LLM and worker scaling:
   1. `LLM_RATE_LIMIT_RPM`, `LLM_CONCURRENT_REQUESTS`
   2. `QUERY_WORKER_CONCURRENCY`, `DOCGEN_WORKER_CONCURRENCY`, `INGESTION_WORKER_CONCURRENCY`

Workload Identity:
1. Create a user-assigned managed identity per environment.
2. Bind identity to Kubernetes service accounts for API and workers.
3. Grant identity access to Storage, Document Intelligence, OpenAI, and Graph API.

Secrets storage:
1. Prefer Key Vault with CSI driver or External Secrets.
2. Avoid inline Kubernetes Secrets for long-lived credentials.

## Ingress and TLS
1. Ingress routes:
   1. `/` to `open-webui` service.
   2. `/api` to `api` service.
2. Health probes:
   1. `api`: `/healthz`
   2. `open-webui`: `/health`
3. TLS:
   1. App Gateway listener uses Key Vault certificate.
   2. Enforce HTTPS redirect.

## Scaling and Resources
Use system-plan sizing as baseline:
1. API: 2 CPU, 2 to 4 GB RAM, 3 replicas.
2. Query worker: 4 CPU, 8 GB RAM, 3 replicas, gevent pool 10.
3. Docgen worker: 4 CPU, 8 GB RAM, 2 replicas, prefork pool 2.
4. Ingestion worker: 4 CPU, 8 GB RAM, 1 replica, prefork pool 4.
5. Open WebUI: 2 CPU, 4 GB RAM, 3 replicas.

HPA:
1. Enable HPA for API and query workers.
2. Use CPU-based scaling first, add custom metrics for queue depth later.

## CI/CD
Pipeline stages:
1. Build and test.
2. Build images.
3. Push to ACR.
4. Helm deploy to Dev.
5. Run validation checks.
6. Manual approval gate.
7. Helm deploy to Prod.

## Runbooks and Operations
1. Database migrations:
   1. Run as Kubernetes Job per deployment.
   2. Ensure only one replica runs migrations.
2. Rollbacks:
   1. Helm rollback to prior release.
3. Secrets rotation:
   1. Rotate Key Vault secrets.
   2. Restart pods to pick up new values.
4. Backups:
   1. PostgreSQL automated backups.
   2. Redis persistence not required, treat as cache.
   3. Neo4j backup per managed or VM approach.

## Validation Checklist
1. Connectivity:
   1. API can connect to Postgres, Redis, Neo4j, Blob.
2. Auth and RBAC:
   1. Graph API access with Workload Identity.
   2. RBAC enforcement on query results.
3. Query pipeline:
   1. HyDE -> vector -> graph -> rerank works end-to-end.
4. Doc generation:
   1. Generate DOCX, PPTX, XLSX and validate output.
5. Observability:
   1. Logs and metrics visible in Azure Monitor.
   2. Alerts triggered for key failures.

## Acceptance Criteria
1. Dev and Prod are deployed and reachable via HTTPS.
2. Workload Identity grants access without client secrets.
3. Ingestion, retrieval, and doc-gen flows succeed end-to-end.
4. Logs and metrics appear in Azure Monitor with alert rules.

## Assumptions and Defaults
1. Neo4j runs in Aura or VM with private endpoint.
2. Certificates are stored in Key Vault and used by App Gateway.
3. Helm is the deployment mechanism for workloads.
