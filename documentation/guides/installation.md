# Installation Guide

ARIA ships as a single Docker image. There is no Python installation required on the target machine — only Docker (local) or a Kubernetes cluster (production).

Two deployment paths are covered here:

- **Docker** — run ARIA on a local machine or a single VM
- **Kubernetes** — run ARIA on a GKE cluster (or any Kubernetes distribution)

Both paths share the same image and the same `conf.yaml` configuration file. What changes is how that config and the required secrets are injected at runtime.

---

## Prerequisites

### Both paths

- A `conf.yaml` — copy `conf_template.yaml` from the project root and fill in your values
- Credentials for the services ARIA connects to (ServiceNow, Slack, and an LLM provider) — see `.env.example` for the full variable list

### Docker path

- Docker ≥ 24 installed on the target machine

### Kubernetes path

- A running Kubernetes cluster (GKE, EKS, AKS, or self-managed)
- `kubectl` configured to point at the target cluster
- A container registry the cluster can pull from (GCR, Artifact Registry, ECR, GHCR)

---

## Prepare your conf.yaml

Start from the project template:

```bash
cp conf_template.yaml conf.yaml
```

Minimum fields to fill in before ARIA will start:

```yaml
servicenow:
  instance: dev382816           # your ServiceNow instance subdomain
  user: admin

slack:
  channel_id: C0123456789       # target notification channel

llm:
  provider: anthropic           # anthropic | vertex_ai | claude_code (local dev only)
  model: claude-sonnet-4-6

runtime:
  vault_backend: env            # env | gcp (see Vault backend section below)
  log_dir: /var/log/aria
```

`conf.yaml` is **never baked into the image** — it is always injected at runtime as a volume mount or ConfigMap. This means the same image works across environments without a rebuild.

---

## Path 1 — Docker (local machine or VM)

### 1. Build the image

From the project root:

```bash
docker build -t aria:latest .
```

### 2. Run the container

```bash
docker run -d \
  --name aria \
  -p 8000:8000 \
  -v /absolute/path/to/your/conf.yaml:/etc/aria/conf.yaml:ro \
  -v aria_logs:/var/log/aria \
  -e ARIA_CONFIG_PATH=/etc/aria/conf.yaml \
  -e ARIA_LOG_DIR=/var/log/aria \
  -e SNOW_PASSWORD=<your-password> \
  -e ANTHROPIC_API_KEY=<your-key> \
  -e SLACK_BOT_TOKEN=<your-token> \
  aria:latest
```

The `conf.yaml` is mounted read-only. Secrets are passed as environment variables (not stored in the config file).

### 3. Verify

```bash
curl http://localhost:8000/api/v1/health
# Expected: {"status": "ok"}
```

### Optional: Docker Compose

If you prefer Compose, a ready-to-use file is available under `deployment/monolithic/`:

```bash
cd deployment/monolithic
cp conf.yaml.example conf.yaml   # fill in your values

export SNOW_PASSWORD=...
export ANTHROPIC_API_KEY=...
export SLACK_BOT_TOKEN=...

docker compose up -d
docker compose logs -f aria
```

---

## Path 2 — Kubernetes

### 1. Build and push the image

```bash
# Replace <registry> and <project> with your values
docker build -t <registry>/<project>/aria:latest .
docker push <registry>/<project>/aria:latest
```

For GKE with Artifact Registry:

```bash
docker build -t europe-west1-docker.pkg.dev/<project>/aria/aria:latest .
docker push europe-west1-docker.pkg.dev/<project>/aria/aria:latest
```

### 2. Create the namespace and ConfigMap

```bash
kubectl create namespace aria

kubectl create configmap aria-config \
  --from-file=conf.yaml=./conf.yaml \
  --namespace aria
```

### 3. Create the secrets

```bash
kubectl create secret generic aria-secrets \
  --from-literal=SNOW_PASSWORD=<your-password> \
  --from-literal=ANTHROPIC_API_KEY=<your-key> \
  --from-literal=SLACK_BOT_TOKEN=<your-token> \
  --namespace aria
```

For GCP deployments using Vertex AI and Secret Manager, grant the pod's service account the necessary IAM roles instead of mounting API keys — see the [LLM provider section](#llm-provider-selection) below.

### 4. Deploy

Save the following as `aria-deployment.yaml` and adjust the image path, project ID, and replica count:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: aria
  namespace: aria
spec:
  replicas: 1
  selector:
    matchLabels:
      app: aria
  template:
    metadata:
      labels:
        app: aria
    spec:
      # For GCP: bind a Kubernetes service account to a GCP service account via Workload Identity
      # serviceAccountName: aria-sa
      containers:
        - name: aria
          image: <registry>/<project>/aria:latest
          ports:
            - containerPort: 8000
          env:
            - name: ARIA_CONFIG_PATH
              value: /etc/aria/conf.yaml
            - name: ARIA_LOG_DIR
              value: /var/log/aria
          envFrom:
            - secretRef:
                name: aria-secrets
          volumeMounts:
            - name: config
              mountPath: /etc/aria
              readOnly: true
            - name: logs
              mountPath: /var/log/aria
          livenessProbe:
            httpGet:
              path: /api/v1/health
              port: 8000
            initialDelaySeconds: 15
            periodSeconds: 30
          readinessProbe:
            httpGet:
              path: /api/v1/health
              port: 8000
            initialDelaySeconds: 5
            periodSeconds: 10
      volumes:
        - name: config
          configMap:
            name: aria-config
        - name: logs
          emptyDir: {}
---
apiVersion: v1
kind: Service
metadata:
  name: aria
  namespace: aria
spec:
  selector:
    app: aria
  ports:
    - port: 80
      targetPort: 8000
  type: ClusterIP
```

Apply and verify:

```bash
kubectl apply -f aria-deployment.yaml
kubectl rollout status deployment/aria -n aria
kubectl get pods -n aria
```

### 5. Access the API

From within the cluster, ARIA is reachable at `http://aria.aria.svc.cluster.local/api/v1/health`.

To expose it externally, add an Ingress resource pointing to the `aria` Service on port 80.

---

## LLM provider selection

Set `llm.provider` in `conf.yaml` or override with the `ARIA_LLM_PROVIDER` environment variable:

| Provider | `llm.provider` value | Auth required | Recommended for |
|---|---|---|---|
| Anthropic API | `anthropic` (default) | `ANTHROPIC_API_KEY` env var | Any non-GCP deployment |
| GCP Vertex AI | `vertex_ai` | ADC via Workload Identity — no API key in pod | GKE, Cloud Run |
| Claude Code CLI | `claude_code` | Local Claude subscription | Local dev only — **not for production** |

For Vertex AI, the model name in `conf.yaml` selects the model family:

```yaml
llm:
  provider: vertex_ai
  model: claude-sonnet@20250201   # Claude-on-Vertex
  # model: gemini-2.0-flash       # Gemini
```

The pod's service account must have `roles/aiplatform.user` on the GCP project. With Workload Identity, no API key or credential file is needed in the container.

---

## Vault / secrets backend

ARIA can read secrets from different backends depending on the environment. Set `runtime.vault_backend` in `conf.yaml` or override with `ARIA_VAULT_BACKEND`:

| Backend | Value | How secrets are supplied |
|---|---|---|
| Environment variables | `env` (default) | Pass secrets as `-e KEY=value` in `docker run` or as a Kubernetes Secret |
| GCP Secret Manager | `gcp` | ADC — needs `GCP_PROJECT_ID` env var; pod service account needs `roles/secretmanager.secretAccessor` |
| HashiCorp Vault | `hashicorp` | `VAULT_TOKEN` and `VAULT_ADDR` env vars |
| AWS Secrets Manager | `aws` | `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` env vars (or instance profile) |
| Azure Key Vault | `azure` | Azure SDK credential chain |

For local and staging deployments, `env` (the default) is the simplest option. For production GCP deployments, `gcp` removes the need to manage credentials manually — the pod authenticates via Workload Identity.
