#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# MilTrack — Deploy to Databricks Apps
# =============================================================================
#
# Prerequisites:
#   1. Databricks CLI installed: pip install databricks-cli
#   2. CLI configured:           databricks configure --host https://e2-demo-field-eng.cloud.databricks.com
#   3. Secret scope created:     (see Step 1 below)
#   4. Frontend built:           cd frontend && bun run build
#
# Usage:
#   chmod +x deploy.sh
#   ./deploy.sh
# =============================================================================

APP_NAME="miltrack"
DB_USER=$(databricks current-user me --output json 2>/dev/null | (python3 -c "import sys,json; print(json.load(sys.stdin).get('userName',''))" 2>/dev/null || jq -r '.userName // empty' 2>/dev/null) || true)
WORKSPACE_PATH="/Workspace/Users/${DB_USER:-mees.benninga@databricks.com}/${APP_NAME}"
SECRET_SCOPE="miltrack-secrets"

echo "================================================"
echo "  MilTrack — Databricks Apps Deployment"
echo "================================================"
echo ""

# --- Step 0: Build frontend ---
echo "[1/5] Building frontend..."
if [ -d "frontend" ]; then
  cd frontend
  if command -v bun &>/dev/null; then
    bun run build
  elif command -v npm &>/dev/null; then
    npm run build
  else
    echo "  WARN: Neither bun nor npm found. Ensure frontend/dist/ exists."
  fi
  cd ..
fi

if [ ! -f "frontend/dist/index.html" ]; then
  echo "  ERROR: frontend/dist/index.html not found. Build the frontend first."
  exit 1
fi
echo "  OK: frontend/dist/ ready"
echo ""

# --- Step 1: Create secret scope and store secrets ---
echo "[2/5] Setting up secrets..."

if ! databricks secrets list-scopes --output json 2>/dev/null | grep -q "\"${SECRET_SCOPE}\""; then
  echo "  Creating secret scope '${SECRET_SCOPE}'..."
  databricks secrets create-scope "${SECRET_SCOPE}" || true
else
  echo "  Secret scope '${SECRET_SCOPE}' already exists"
fi

if [ -f .env ]; then
  BRAVE_KEY=$(grep "^BRAVE_SEARCH_API_KEY=" .env | cut -d'=' -f2-)
  if [ -n "${BRAVE_KEY}" ] && [ "${BRAVE_KEY}" != "your-brave-api-key" ]; then
    echo "  Storing Brave API key in secret scope..."
    databricks secrets put-secret "${SECRET_SCOPE}" "brave-api-key" --string-value "${BRAVE_KEY}"
    echo "  OK: brave-api-key stored"
  fi
  UCDP_TOKEN=$(grep "^UCDP_ACCESS_TOKEN=" .env | cut -d'=' -f2-)
  if [ -n "${UCDP_TOKEN}" ] && [ "${UCDP_TOKEN}" != "your-ucdp-token" ]; then
    echo "  Storing UCDP token in secret scope..."
    databricks secrets put-secret "${SECRET_SCOPE}" "ucdp-access-token" --string-value "${UCDP_TOKEN}"
    echo "  OK: ucdp-access-token stored"
  fi
  FA_KEY=$(grep "^FLIGHTAWARE_API_KEY=" .env | cut -d'=' -f2-)
  if [ -n "${FA_KEY}" ] && [ "${FA_KEY}" != "your-flightaware-key" ]; then
    echo "  Storing FlightAware API key in secret scope..."
    databricks secrets put-secret "${SECRET_SCOPE}" "flightaware-api-key" --string-value "${FA_KEY}"
    echo "  OK: flightaware-api-key stored"
  fi
else
  echo "  WARN: No .env file found. Set secrets manually:"
  echo "    databricks secrets put-secret ${SECRET_SCOPE} brave-api-key"
  echo "    databricks secrets put-secret ${SECRET_SCOPE} ucdp-access-token"
fi
echo ""

# --- Step 2: Sync source code to workspace ---
echo "[3/5] Syncing source code to workspace..."
echo "  Target: ${WORKSPACE_PATH}"

databricks workspace mkdirs "${WORKSPACE_PATH}" 2>/dev/null || true

databricks sync . "${WORKSPACE_PATH}" \
  --watch=false \
  --exclude "__pycache__" \
  --exclude "node_modules" \
  --exclude ".venv" \
  --exclude ".git" \
  --exclude ".env"
echo "  OK: Code synced"

# CRITICAL: databricks sync respects .gitignore, so frontend/dist/ is EXCLUDED.
# The backend serves from frontend/dist/ — we must upload it explicitly.
echo "  Uploading frontend/dist/ (excluded from sync by .gitignore)..."
databricks workspace import-dir ./frontend/dist "${WORKSPACE_PATH}/frontend/dist" --overwrite
echo "  OK: frontend/dist/ uploaded"
echo ""

# --- Step 3: Create or update the app ---
echo "[4/5] Creating/updating app '${APP_NAME}'..."

if databricks apps get "${APP_NAME}" &>/dev/null; then
  echo "  App already exists"
else
  echo "  Creating new app..."
  databricks apps create "${APP_NAME}" \
    --description "Live military aircraft tracker & conflict monitor with AI intelligence"
fi

# Configure app resources (secrets) programmatically — no manual UI needed
echo "  Configuring app resources (brave_api_key, ucdp_access_token, flightaware_api_key)..."
RESOURCES_JSON=$(cat <<EOF
{
  "resources": [
    {
      "name": "brave_api_key",
      "secret": {
        "scope": "${SECRET_SCOPE}",
        "key": "brave-api-key",
        "permission": "READ"
      }
    },
    {
      "name": "ucdp_access_token",
      "secret": {
        "scope": "${SECRET_SCOPE}",
        "key": "ucdp-access-token",
        "permission": "READ"
      }
    },
    {
      "name": "flightaware_api_key",
      "secret": {
        "scope": "${SECRET_SCOPE}",
        "key": "flightaware-api-key",
        "permission": "READ"
      }
    }
  ]
}
EOF
)
if databricks apps update "${APP_NAME}" --json "${RESOURCES_JSON}" 2>/dev/null; then
  echo "  OK: App resources configured"
else
  echo "  WARN: Could not update app resources via CLI. If secrets fail, add them manually in Apps > ${APP_NAME} > Settings > Resources"
fi
echo ""

# --- Step 4: Deploy ---
echo "[5/5] Deploying..."
databricks apps deploy "${APP_NAME}" \
  --source-code-path "${WORKSPACE_PATH}"

echo ""
echo "================================================"
echo "  Deployment initiated!"
echo "================================================"
echo ""
echo "  Monitor: databricks apps get ${APP_NAME}"
echo "  Logs:    databricks apps get-deployment ${APP_NAME} --deployment-id latest"
echo ""
echo "  Your app will be available at:"
echo "  https://<workspace-id>-apps.cloud.databricks.com/miltrack"
echo ""
