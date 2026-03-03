# MilTrack Databricks Apps Deployment — Troubleshooting Report

**Date:** March 2, 2026  
**Status:** Resolved — AI Intel, Conflict Events, SITREP, and Live Feed all operational

---

## Executive Summary

MilTrack was successfully deployed to Databricks Apps at `miltrack-1444828305810485.aws.databricksapps.com`. The deployment encountered six major issues that prevented the AI-powered features from working. Each was diagnosed and fixed. This report documents what failed, why, and how it was resolved.

---

## Issue 1: Config File Conflict — `app.yml` Overriding `app.yaml`

### What Failed
- The app showed "NEWS" instead of "AI INTEL"
- Conflict events stayed "RAW — AI PROCESSING..."
- SITREP never generated
- "How it works" explainers were missing

### Why It Failed
Two config files existed in the project: `app.yaml` (full config with env vars) and `app.yml` (minimal config). Databricks Apps loaded `app.yml`, which contained only:

```yaml
command: [uvicorn, backend.app:app]
env:
  - name: UVICORN_WORKERS
    value: "1"
```

This meant the app ran **without**:
- `BRAVE_SEARCH_API_KEY` → Intel pipeline disabled
- `DATABRICKS_ENDPOINT_URL` / `DATABRICKS_HOST` → LLM calls impossible
- `DATABRICKS_LLM_MODEL` → No model fallback chain

### How It Was Fixed
- Deleted `app.yml` from the project and workspace
- Ensured only `app.yaml` exists with the full configuration

---

## Issue 2: AI Gateway Unreachable — ConnectError

### What Failed
- `/api/debug/llm-test` returned `{"ok": false, "error": "", "type": "ConnectError"}`
- App container could not establish a TCP connection to the AI Gateway

### Why It Failed
The app was configured to call the AI Gateway at:
`https://1444828305810485.ai-gateway.cloud.databricks.com/mlflow/v1/chat/completions`

The Databricks Apps container runs in a serverless compute environment. Outbound connections to `*.ai-gateway.cloud.databricks.com` were not reachable from the app's network context — likely due to internal routing or DNS resolution within the Databricks infrastructure. The same URL worked from a user's machine (curl succeeded) but failed from inside the app container.

### How It Was Fixed
- Switched from `DATABRICKS_ENDPOINT_URL` (AI Gateway) to `DATABRICKS_HOST` (workspace URL)
- LLM calls now use: `https://e2-demo-field-eng.cloud.databricks.com/serving-endpoints/{model}/invocations`
- The workspace host is reachable from the app container; the Foundation Model API format (messages, max_tokens) is the same

---

## Issue 3: Frontend Build Not Deployed — `frontend/dist/` Excluded

### What Failed
- Stale or missing frontend UI
- "How it works" sections sometimes absent
- Inconsistent UI after redeployments

### Why It Failed
`deploy.sh` uses `databricks sync` to push code to the workspace. `databricks sync` respects `.gitignore`. Because `frontend/dist/` is in `.gitignore` (build output should not be committed to git), it was **never synced** to the workspace. The backend serves static files from `frontend/dist/`, so the app was either serving an old cached build or failing to serve the frontend.

### How It Was Fixed
- Added an explicit step after sync in `deploy.sh`:
  ```bash
  databricks workspace import-dir ./frontend/dist "${WORKSPACE_PATH}/frontend/dist" --overwrite
  ```
- The frontend is built before sync; `import-dir` uploads the fresh build regardless of `.gitignore`

---

## Issue 4: Service Principal Cannot Call Foundation Model APIs

### What Failed
- LLM calls failed when using service principal (M2M OAuth) authentication
- Permissions dialog for AI Gateway showed: "Permissions for System endpoints, including databricks-claude-opus-4-6, will soon be managed via Unity Catalog"

### Why It Failed
Databricks Apps assign a service principal to each app. The service principal's OAuth token was used for LLM calls. Foundation Model API system endpoints (e.g. `databricks-claude-opus-4-6`) are currently in transition — permissions are being migrated to Unity Catalog. As a result, the service principal could not be granted `Can Query` permission on these endpoints.

### How It Was Fixed
- Use a Personal Access Token (PAT) instead of service principal for LLM calls
- Set `DATABRICKS_TOKEN` in `app.yaml` with the user's PAT
- Updated `_get_auth_headers()` in `backend/intel.py` to prefer `DATABRICKS_TOKEN` over SDK auth when present

---

## Issue 5: Secret Injection Unreliable — `valueFrom`

### What Failed
- `BRAVE_SEARCH_API_KEY` was not consistently injected via `valueFrom: brave_api_key`
- Intel pipeline reported "not configured" even after secret scope and resource were set up

### Why It Failed
The `valueFrom` mechanism in `app.yaml` references a Databricks resource (secret scope + key). The injection was unreliable — possibly due to timing, permission, or resource configuration. The app started before the secret was available, or the resource was not correctly bound.

### How It Was Fixed
- Hardcoded `BRAVE_SEARCH_API_KEY` as a direct `value` in `app.yaml` for the demo workspace
- For production, consider using `valueFrom` with verified resource setup, or a dedicated secrets management approach

---

## Issue 6: Rate Limit Exceeded — 429 REQUEST_LIMIT_EXCEEDED

### What Failed
- `/api/debug/llm-test` returned `status_code: 429` with:
  `"Exceeded workspace output tokens per minute rate limit for databricks-claude-opus-4-6"`

### Why It Failed
The workspace has a per-minute token limit for Foundation Model APIs. The app runs multiple LLM pipelines in parallel (Intel, Conflict enrichment, SITREP), each making multiple requests. `databricks-claude-opus-4-6` hit its limit quickly.

### How It Was Fixed
- Implemented a 5-model fallback chain in `DATABRICKS_LLM_MODEL`
- On 429, the code immediately tries the next model instead of waiting
- Order: `databricks-claude-sonnet-4-6` → `databricks-meta-llama-3-1-70b-instruct` → `databricks-gemini-2-5-flash` → `databricks-claude-sonnet-4-5` → `databricks-claude-opus-4-6`
- Different models use different rate limit pools; fallback spreads load

---

## Additional Fixes

- **`--host` and `--port`:** Removed temporarily; re-added per Databricks Docs best practices. Apps must listen on `0.0.0.0` and use the port specified by the platform.
- **Health endpoint:** Added `/api/health` and `/api/debug/llm-test` for deployment diagnostics.
- **Deploy script:** Fixed secret scope creation to handle "already exists" gracefully; added explicit `frontend/dist` upload.

---

## Final Configuration Summary

| Component | Resolution |
|-----------|------------|
| Config file | Single `app.yaml`; `app.yml` removed |
| LLM endpoint | Workspace URL; `DATABRICKS_HOST` + `/serving-endpoints/{model}/invocations` |
| Auth | Service principal (Apps); PAT (`DATABRICKS_TOKEN`) for local dev only |
| Brave API key | Injected via `valueFrom: brave_api_key` from Secret Scope |
| Frontend | Explicit `import-dir` of `frontend/dist` after sync |
| Rate limits | 5-model fallback chain; automatic switch on 429 |

---

## Recommendations for Future Deployments

1. **Secrets:** Use `valueFrom` with Databricks resources for production; ensure the app resource is correctly configured and the service principal has access.
2. **Rate limits:** Contact Databricks account team to request a higher FMAPI rate limit tier if needed.
3. **Monitoring:** Keep `/api/health` and `/api/debug/llm-test` for quick diagnostics; consider removing or protecting them in production.
4. **Single config:** Avoid having both `app.yaml` and `app.yml`; use one format consistently.
