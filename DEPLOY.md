# Deploying MilTrack to Databricks Apps

Deploy MilTrack as a Databricks App on `e2-demo-field-eng.cloud.databricks.com`.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Databricks Apps (Medium: 2 vCPU, 6GB RAM)              │
│                                                         │
│  ┌──────────────────────────────────────┐               │
│  │  FastAPI (uvicorn)                   │               │
│  │  - /api/* endpoints                  │               │
│  │  - Static frontend (React)           │               │
│  │  - Background tasks (news, intel,    │               │
│  │    strikes, SITREP)                  │               │
│  └──────────┬───────────────────────────┘               │
│             │                                           │
│  Auth: Service Principal (M2M OAuth)                    │
│  Access: Any workspace user via Databricks SSO          │
└─────────────┼───────────────────────────────────────────┘
              │
     ┌────────┴────────────────────────────────────┐
     │           External API Calls                 │
     ├──────────────────────────────────────────────┤
     │ Databricks AI Gateway → Claude/Sonnet/Llama  │
     │ Brave Search API → News articles             │
     │ Jina Reader API → Article extraction (free)  │
     │ adsb.lol → Military aircraft ADS-B           │
     │ GDELT Project → Conflict events (free)       │
     │ OpenStreetMap Overpass → Military bases       │
     └──────────────────────────────────────────────┘
```

## Authentication

| Context | Method | How it works |
|---------|--------|--------------|
| **Databricks APIs** | Service Principal (M2M OAuth) | Databricks Apps auto-injects `DATABRICKS_CLIENT_ID` + `DATABRICKS_CLIENT_SECRET`. The Databricks SDK handles token generation and refresh. No PAT needed. |
| **App access** | Databricks SSO | Any workspace user with "Can Use" permission accesses the app through their normal Databricks login. No separate auth system. |
| **External APIs** | API keys via Secrets | Brave Search key stored in a Databricks Secret Scope, injected as env var at runtime. Never in source code. |
| **Local dev** | Personal Access Token | `.env` file with `DATABRICKS_TOKEN` for local testing. The SDK auto-detects this. |

## Prerequisites

1. **Databricks CLI** (v0.205+):
   ```bash
   pip install databricks-cli
   databricks configure --host https://e2-demo-field-eng.cloud.databricks.com
   ```

2. **Frontend built**:
   ```bash
   cd frontend && bun run build   # or: npm run build
   ```

## Step-by-Step Deployment

### 1. Create the Secret Scope

Store sensitive API keys securely — never in code or `app.yaml`.

```bash
# Create a scope for MilTrack secrets
databricks secrets create-scope miltrack-secrets

# Store the Brave Search API key
echo -n "BSAItDrXVWrs8SjHT4X3Y2nwsjMgPB3" | \
  databricks secrets put-secret miltrack-secrets brave-api-key --binary-file /dev/stdin
```

### 2. Create the App

In the Databricks UI:

1. **Sidebar** → **New** → **App** → **Create a custom app**
2. **Name**: `miltrack`
3. **Description**: `Live military aircraft tracker & conflict monitor with AI intelligence`
4. Click **Next: Configure**:
   - **Compute size**: **Medium** (2 vCPU, 6GB — sufficient since LLM runs on AI Gateway)
   - **Resources**: Configured automatically by `deploy.sh` via `databricks apps update` (no manual UI step)
   - **Auth**: App uses its service principal (no PAT). Grant the SP "Can Query" on AI Gateway / Foundation Model endpoints.
5. **Permissions**: Grant **Can Use** to the users/groups who should access the app
6. Click **Create app**

Or via CLI:
```bash
databricks apps create miltrack \
  --description "Live military aircraft tracker & conflict monitor with AI intelligence"
```

### 3. Sync Source Code

```bash
# Get your workspace username
ME=$(databricks current-user me --output json | python3 -c "import sys,json; print(json.load(sys.stdin)['userName'])")

# Sync project files (excludes .env, .git, node_modules, etc.)
databricks sync . "/Workspace/Users/${ME}/miltrack" \
  --watch=false \
  --exclude "__pycache__" \
  --exclude "node_modules" \
  --exclude ".venv" \
  --exclude ".git" \
  --exclude ".env"
```

### 4. Grant Service Principal Access to AI Gateway

The app's auto-provisioned service principal needs permission to call the AI Gateway:

1. Go to the app's **Authorization** tab and copy the **Service Principal ID**
2. Navigate to the AI Gateway endpoint settings
3. Grant the service principal **Can Query** permission

### 5. Deploy

```bash
databricks apps deploy miltrack \
  --source-code-path "/Workspace/Users/${ME}/miltrack"
```

Monitor deployment:
```bash
databricks apps get miltrack
```

### 6. Access the App

Your app URL will be:
```
https://<workspace-id>-apps.cloud.databricks.com/miltrack/
```

Any workspace user with the "Can Use" permission can access it through their Databricks SSO login.

## Compute Sizing

**Medium (2 vCPU, 6GB RAM)** is recommended because:

- The app is **I/O-bound**, not CPU-bound — it makes async HTTP calls to external APIs
- **LLM inference** runs on the Databricks AI Gateway (not on the app's compute)
- **In-memory caches** are small: ~100 strike events, ~30 intel articles, ~200 aircraft
- **Background tasks** are lightweight async loops with long sleep intervals (5min–2hrs)

Only upgrade to Large (4 vCPU, 12GB) if you see memory pressure from very large GDELT response batches.

## Cost Breakdown

| Component | Cost | Frequency |
|-----------|------|-----------|
| **App compute** | 0.5 DBU/hour (Medium) | Always on |
| **AI Gateway** (Claude Opus) | Per-token (workspace rate) | ~6 calls/2hrs |
| **Brave Search API** | $5/1k queries | ~4 queries/2hrs |
| **Other APIs** | Free | Continuous |

## Updating the App

After making code changes:

```bash
# Rebuild frontend if UI changed
cd frontend && bun run build && cd ..

# Re-sync and redeploy
databricks sync . "/Workspace/Users/${ME}/miltrack" --watch=false \
  --exclude "__pycache__" --exclude "node_modules" --exclude ".venv" --exclude ".git" --exclude ".env"

databricks apps deploy miltrack \
  --source-code-path "/Workspace/Users/${ME}/miltrack"
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| App can't reach AI Gateway | Grant service principal "Can Query" on the endpoint |
| App can't reach external APIs | Check workspace network policies allow egress to `api.search.brave.com`, `r.jina.ai`, `api.adsb.lol`, `data.gdeltproject.org` |
| 401 Unauthorized on LLM calls | Verify SDK auth: check app logs for "Databricks SDK auth initialized" |
| Secrets not available | Confirm resource key in app matches `valueFrom` in `app.yaml` |
| Frontend not loading | Ensure `frontend/dist/` was included in workspace sync |
