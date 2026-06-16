# ARIA — Deployment Guide

ARIA ships as a single Docker image. This guide covers four deployment patterns.
All patterns use the same image; what changes is how config and secrets are injected.

---

## Prerequisites

- Docker ≥ 24
- A `conf.yaml` (copy `conf_template.yaml` from the project root and fill in values)
- Secrets in environment variables or a vault backend (see `.env.example`)

---

## Pattern 1 — Docker CLI (quickstart)

```bash
docker build -t aria:latest .

docker run -d \
  --name aria \
  -p 8000:8000 \
  -v /path/to/your/conf.yaml:/etc/aria/conf.yaml:ro \
  -v aria_logs:/var/log/aria \
  -e ARIA_CONFIG_PATH=/etc/aria/conf.yaml \
  -e ARIA_LOG_DIR=/var/log/aria \
  -e SNOW_PASSWORD=<your-password> \
  -e ANTHROPIC_API_KEY=<your-key> \
  -e SLACK_BOT_TOKEN=<your-token> \
  aria:latest

# Verify
curl http://localhost:8000/api/v1/health
```

---

## Pattern 2 — docker compose (monolithic)

```bash
cd deployment/monolithic

# Copy and fill in the config
cp conf.yaml.example conf.yaml
# Edit conf.yaml with your ServiceNow, GCP, and Slack settings

# Set secrets in environment (or a .env file in this directory)
export SNOW_PASSWORD=...
export ANTHROPIC_API_KEY=...
export SLACK_BOT_TOKEN=...

docker compose up -d

# Tail logs
docker compose logs -f aria
```

The compose file bind-mounts `./conf.yaml` to `/etc/aria/conf.yaml` inside the container and uses a named volume for log persistence.

---

## Pattern 3 — Cloud Run

```bash
# Build and push
docker build -t gcr.io/<project>/aria:latest .
docker push gcr.io/<project>/aria:latest

# Deploy (secrets via Secret Manager — set runtime.vault_backend: gcp in conf.yaml)
gcloud run deploy aria \
  --image gcr.io/<project>/aria:latest \
  --region europe-west1 \
  --set-env-vars ARIA_CONFIG_PATH=/etc/aria/conf.yaml \
  --set-env-vars ARIA_LLM_PROVIDER=vertex_ai \
  --set-env-vars GCP_PROJECT_ID=<project> \
  --set-secrets SNOW_PASSWORD=aria-snow-password:latest \
  --set-secrets SLACK_BOT_TOKEN=aria-slack-bot-token:latest \
  --memory 2Gi \
  --cpu 2 \
  --no-allow-unauthenticated

# For conf.yaml: mount via a Cloud Storage FUSE volume or bake into the image at build time.
# The simplest option for Cloud Run is to set ARIA_CONFIG_PATH and pass config as env vars
# (every conf.yaml key has an env var fallback — see conf_template.yaml for the mapping).
```

---

## Pattern 4 — GKE (ConfigMap pattern)

Create a ConfigMap from your `conf.yaml`:

```bash
kubectl create configmap aria-config \
  --from-file=conf.yaml=./conf.yaml \
  --namespace aria
```

Deployment snippet (adjust image, replicas, and secret names to your cluster):

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
      serviceAccountName: aria-sa   # must have roles/aiplatform.user + roles/secretmanager.secretAccessor
      containers:
        - name: aria
          image: gcr.io/<project>/aria:latest
          ports:
            - containerPort: 8000
          env:
            - name: ARIA_CONFIG_PATH
              value: /etc/aria/conf.yaml
            - name: ARIA_LOG_DIR
              value: /var/log/aria
            - name: ARIA_LLM_PROVIDER
              value: vertex_ai
            - name: GCP_PROJECT_ID
              value: <project>
            - name: ARIA_VAULT_BACKEND
              value: gcp
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
      volumes:
        - name: config
          configMap:
            name: aria-config
        - name: logs
          emptyDir: {}
```

Apply:

```bash
kubectl apply -f aria-deployment.yaml
kubectl rollout status deployment/aria -n aria
```

---

## LLM provider selection

Set `llm.provider` in `conf.yaml` or override with `ARIA_LLM_PROVIDER`:

| Provider | Value | Auth | Use case |
|---|---|---|---|
| Anthropic API | `anthropic` | `ANTHROPIC_API_KEY` | Default — any non-GCP deployment |
| GCP Vertex AI | `vertex_ai` | ADC (no API key) | GKE, Cloud Run — recommended for GCP |
| Claude Code CLI | `claude_code` | Local subscription | Local dev only — **not for production** (#84) |

For Vertex AI, the model ID in `conf.yaml` selects the model family:
- Claude-on-Vertex: `claude-sonnet@20250201`
- Gemini: `gemini-2.0-flash`, `gemini-2.5-pro`

---

## Vault backend selection

Set `runtime.vault_backend` in `conf.yaml` or override with `ARIA_VAULT_BACKEND`:

| Backend | Value | Auth |
|---|---|---|
| Environment variables | `env` (default) | None — reads from process env |
| GCP Secret Manager | `gcp` | ADC — needs `GCP_PROJECT_ID` env var |
| HashiCorp Vault | `hashicorp` | `VAULT_TOKEN` env var |
| AWS Secrets Manager | `aws` | `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` |
| Azure Key Vault | `azure` | Azure SDK credential chain |
