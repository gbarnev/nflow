#!/usr/bin/env bash
set -euo pipefail

CONTAINER="${N8N_CONTAINER:-n8n-n8n-1}"
HOST="${N8N_HOST:-}"          # e.g. user@my-vps.com
REMOTE_TMP="/tmp/nflow-sync"

usage() {
    cat <<'EOF'
n8n-sync — Export/import n8n credentials and workflows via Docker.

Usage:
  n8n-sync export-creds  [output_file]         Export all credentials
  n8n-sync import-creds  <credentials.json>    Import credentials
  n8n-sync import-workflow <workflow.json>      Import a workflow
  n8n-sync deploy <workflow.json> [creds.json]  Import creds (if given) + workflow

Environment:
  N8N_HOST        SSH destination for remote VPS (e.g. user@my-vps.com)
                  Leave empty for local Docker.
  N8N_CONTAINER   Docker container name (default: n8n-n8n-1)

Examples:
  # Export credentials from remote VPS
  N8N_HOST=root@my-vps.com n8n-sync export-creds credentials.json

  # Full deploy to remote VPS
  nflow api.nflow -o api.json
  N8N_HOST=root@my-vps.com n8n-sync deploy api.json api-credentials.json

  # Deploy with linked credentials (no creds file needed)
  nflow api.nflow -c credentials.json -o api.json
  N8N_HOST=root@my-vps.com n8n-sync deploy api.json

  # Local Docker (no N8N_HOST)
  n8n-sync export-creds credentials.json
EOF
    exit 1
}

# --- transport layer: local docker vs SSH ---

run_docker() {
    if [[ -n "$HOST" ]]; then
        ssh "$HOST" docker "$@"
    else
        docker "$@"
    fi
}

copy_to_container() {
    local src="$1" dest="$2"
    if [[ -n "$HOST" ]]; then
        scp -q "$src" "$HOST:$REMOTE_TMP/_upload"
        ssh "$HOST" docker cp "$REMOTE_TMP/_upload" "$dest"
    else
        docker cp "$src" "$dest"
    fi
}

copy_from_container() {
    local src="$1" dest="$2"
    if [[ -n "$HOST" ]]; then
        ssh "$HOST" docker cp "$src" "$REMOTE_TMP/_download"
        scp -q "$HOST:$REMOTE_TMP/_download" "$dest"
    else
        docker cp "$src" "$dest"
    fi
}

# --- helpers ---

ensure_container() {
    if ! run_docker inspect "$CONTAINER" &>/dev/null; then
        local where="locally"
        [[ -n "$HOST" ]] && where="on $HOST"
        echo "Error: container '$CONTAINER' not found $where." >&2
        echo "Set N8N_CONTAINER to override." >&2
        exit 1
    fi
}

setup_tmp() {
    run_docker exec -u node "$CONTAINER" mkdir -p "$REMOTE_TMP"
    [[ -n "$HOST" ]] && ssh "$HOST" mkdir -p "$REMOTE_TMP"
}

cleanup_tmp() {
    run_docker exec -u node "$CONTAINER" rm -rf "$REMOTE_TMP"
    [[ -n "$HOST" ]] && ssh "$HOST" rm -rf "$REMOTE_TMP"
}

# --- commands ---

export_creds() {
    local out="${1:-credentials.json}"
    ensure_container
    setup_tmp
    run_docker exec -u node "$CONTAINER" n8n export:credentials --all --output="$REMOTE_TMP/creds.json"
    copy_from_container "$CONTAINER:$REMOTE_TMP/creds.json" "$out"
    cleanup_tmp
    echo "Exported credentials → $out"
}

import_creds() {
    local file="$1"
    [[ -f "$file" ]] || { echo "Error: file not found: $file" >&2; exit 1; }
    ensure_container
    setup_tmp
    copy_to_container "$file" "$CONTAINER:$REMOTE_TMP/creds.json"
    run_docker exec -u node "$CONTAINER" n8n import:credentials --input="$REMOTE_TMP/creds.json"
    cleanup_tmp
    echo "Imported credentials ← $file"
}

import_workflow() {
    local file="$1"
    [[ -f "$file" ]] || { echo "Error: file not found: $file" >&2; exit 1; }
    ensure_container
    setup_tmp
    copy_to_container "$file" "$CONTAINER:$REMOTE_TMP/workflow.json"
    run_docker exec -u node "$CONTAINER" n8n import:workflow --input="$REMOTE_TMP/workflow.json"
    cleanup_tmp
    echo "Imported workflow ← $file"
}

deploy() {
    local workflow="$1"
    local creds="${2:-}"
    [[ -f "$workflow" ]] || { echo "Error: file not found: $workflow" >&2; exit 1; }
    if [[ -n "$creds" ]]; then
        import_creds "$creds"
    fi
    import_workflow "$workflow"
    echo "Deploy complete."
}

[[ $# -ge 1 ]] || usage

case "$1" in
    export-creds)   export_creds "${2:-}" ;;
    import-creds)   [[ $# -ge 2 ]] || usage; import_creds "$2" ;;
    import-workflow) [[ $# -ge 2 ]] || usage; import_workflow "$2" ;;
    deploy)         [[ $# -ge 2 ]] || usage; deploy "$2" "${3:-}" ;;
    -h|--help|help) usage ;;
    *)              echo "Unknown command: $1" >&2; usage ;;
esac
