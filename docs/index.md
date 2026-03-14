# nflow

**A compact DSL that compiles `.nflow` files into valid n8n workflow JSON.**

Write ~50 lines of declarative syntax instead of ~5,000 lines of n8n JSON.

```nflow
WORKFLOW "My API" active

CREDENTIAL @pg = postgres "Production DB"

TRIGGER webhook AS "Incoming" { path: "/api", method: POST }

NODE "postgres" @pg AS "Query Users" {
  operation: "executeQuery",
  query: "SELECT * FROM users WHERE active = true"
}

NODE "respondToWebhook" AS "Respond" {
  respondWith: "allIncomingItems"
}

"Incoming" -> "Query Users" -> "Respond"
```

```bash
nflow api.nflow -o api.json
```

## Documentation

| Page | Description |
|------|-------------|
| [Language Reference](NFLOW.md) | Full grammar — keywords, connections, settings, examples |
| [Node Catalog](NODE-CATALOG.md) | Compact index of all 547 nodes with credentials and operations |
| [Node Reference](nodes/index.md) | Per-node pages with full parameter details |

## Quick Start

```bash
# Install
pip install .

# Compile a workflow
nflow workflow.nflow -o workflow.json

# Syntax check only
nflow workflow.nflow --validate

# Link existing n8n credentials
nflow workflow.nflow -c credentials.json -o workflow.json
```

## Two Ways to Define Nodes

**Ergonomic keywords** for common nodes — concise, hand-crafted syntax:

```nflow
TRIGGER webhook AS "Hook" { path: "/data", method: POST }
SET "Config" { apiUrl: "https://api.example.com" } +passthrough
HTTP GET {{ $json.apiUrl }}/items @auth AS "Fetch"
IF "Has Data?" { conditions: AND [{{ $json.items }} arrayNotEmpty] }
```

**Generic `NODE` keyword** for any of the 500+ n8n nodes:

```nflow
NODE "postgres" @pg AS "Query" { operation: "executeQuery", query: "SELECT 1" }
NODE "slack" @slack AS "Notify" { resource: "message", operation: "post", channelId: "#alerts", text: "Done" }
NODE "redis" @redis AS "Cache" { operation: "set", key: "result", value: {{ JSON.stringify($json) }} }
```

Browse the [Node Catalog](NODE-CATALOG.md) to find any node, then open its page for full parameter details.
