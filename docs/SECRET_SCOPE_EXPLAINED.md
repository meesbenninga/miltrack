# How the Databricks Secret Scope Works

A personal reference for understanding how MilTrack stores and uses API keys securely.

---

## 1. What is a Secret Scope?

A **secret scope** is a Databricks-managed container for storing sensitive values (API keys, tokens, etc.). It lives in Databricks' infrastructure, not in your code or repo.

Think of it as a secure key-value store:

```
miltrack-secrets (scope)
├── brave-api-key      → (your Brave Search API key)
└── ucdp-access-token  → (your UCDP API token)
```

---

## 2. How Secrets Get Into the Scope

`deploy.sh` reads from `.env` and writes into the scope:

```bash
# deploy.sh reads .env
BRAVE_KEY=$(grep "^BRAVE_SEARCH_API_KEY=" .env | cut -d'=' -f2-)

# Stores it in Databricks (not in your repo)
databricks secrets put-secret miltrack-secrets brave-api-key --string-value "${BRAVE_KEY}"
```

So the scope is populated from your local `.env` during deploy.

---

## 3. How the App Gets the Secrets

The app doesn't read the scope directly. It gets values via **app resources**:

| Step | What happens |
|------|--------------|
| 1 | `deploy.sh` runs `databricks apps update` with a resources config that says: "App resource `brave_api_key` = secret `miltrack-secrets` / `brave-api-key`" |
| 2 | `app.yaml` has `valueFrom: brave_api_key` for `BRAVE_SEARCH_API_KEY` |
| 3 | When the app starts, Databricks looks up `brave_api_key` → finds the secret in the scope → injects its value into the env var `BRAVE_SEARCH_API_KEY` |
| 4 | Your Python code uses `os.getenv("BRAVE_SEARCH_API_KEY")` and gets the real key |

---

## 4. Visual Flow

```
.env (local, gitignored)
    │
    │  deploy.sh reads
    ▼
databricks secrets put-secret miltrack-secrets brave-api-key
    │
    │  stored in Databricks
    ▼
miltrack-secrets scope
    │
    │  app resource "brave_api_key" points to this secret
    │  app.yaml: valueFrom: brave_api_key
    ▼
App process gets env var BRAVE_SEARCH_API_KEY
    │
    │  os.getenv("BRAVE_SEARCH_API_KEY")
    ▼
Your code uses the key for API calls
```

---

## 5. Where Does the Scope Live?

The scope lives in **Databricks' cloud infrastructure**, not on your machine or in your repo.

- **Workspace-scoped** — It's tied to your Databricks workspace (e.g. `e2-demo-field-eng.cloud.databricks.com`).
- **Databricks-managed storage** — Databricks stores the encrypted values in their own infrastructure (AWS/Azure/GCP depending on your deployment).
- **Access via API/CLI** — You interact with it through the Databricks API or CLI (`databricks secrets ...`), which talks to that backend.

So `miltrack-secrets` is a Databricks workspace resource, like notebooks or jobs — it exists in the cloud for that workspace, not in your local project.

---

## 6. Why Use a Scope Instead of Putting Keys in Code?

- **Keys never live in source code or `app.yaml`** — they're injected at runtime
- **`.env` stays local and gitignored** — never committed
- **Databricks manages storage and injection** — encrypted, access-controlled
- **Permissions** — you control who can read the scope and who can use the app
