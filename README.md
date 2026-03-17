# nflow

**Make LLMs write n8n workflows.** A compact DSL that compiles to valid [n8n](https://n8n.io) workflow JSON — readable by humans, writable by AI.

n8n workflows are stored as deeply nested JSON with UUIDs, pixel coordinates, type versions, and boilerplate that no one should write by hand. nflow replaces all of that with a clean, declarative syntax that LLMs can generate reliably and humans can actually review.

## The Problem

Ask an LLM to build an n8n workflow and you'll get something like this:

```json
{
  "nodes": [
    {
      "parameters": {
        "operation": "executeQuery",
        "query": "=SELECT * FROM users WHERE email = '{{ $json.body.email }}'"
      },
      "type": "n8n-nodes-base.postgres",
      "typeVersion": 2.6,
      "position": [250, 0],
      "id": "e399715a-7b1c-4359-9a81-edde0342e404",
      "name": "Lookup User",
      "credentials": {
        "postgres": { "id": "HsfjGSPzhjRrKA9e", "name": "Postgres Production" }
      }
    }
  ]
}
```

Multiply that by every node, every connection, every credential — for a 10-node workflow you're looking at 300+ lines of JSON. LLMs hallucinate node types, forget required fields, break the connection schema, and can't compute layout positions. The result almost never imports cleanly.

## The Solution

The same node in nflow:

```
NODE "postgres" @pg AS "Lookup User" {
  operation: "executeQuery",
  query: "SELECT * FROM users WHERE email = '{{ $json.body.email }}'"
}
```

The compiler handles UUIDs, positions, type versions, credential wiring, and the full n8n JSON schema. An LLM only needs to describe *what* the workflow does — nflow takes care of *how* n8n needs it formatted.

## Why LLMs Love It

- **Flat structure** — no nested JSON to hallucinate. One keyword per node, connections are just arrows.
- **No boilerplate** — no UUIDs, no `[x, y]` positions, no `typeVersion` lookups. The compiler fills those in.
- **547 nodes supported** — a built-in registry knows every n8n node type, its parameters, and their correct JSON serialization.
- **Forgiveness built in** — string/number/boolean coercion, expression detection, and smart defaults mean minor LLM mistakes still compile.
- **Fits in context** — a 10-node workflow is ~40 lines of nflow vs ~300 lines of JSON. That's less context used and more room for reasoning.

## Quick Start

Install:

```bash
git clone https://github.com/gbarnev/nflow.git
cd nflow
pip install .
```

Create `hello.nflow`:

```
WORKFLOW "Hello World" active

TRIGGER manual AS "Start"
SET "Greeting" { message: "Hello from nflow!" }
HTTP POST https://httpbin.org/post AS "Send" {
  jsonBody: {{ JSON.stringify($json) }}
}

"Start" -> "Greeting" -> "Send"
```

Compile:

```bash
nflow hello.nflow -o hello.json
```

Or run without installing:

```bash
python3 -m nflow hello.nflow -o hello.json
```

Deploy to n8n (self-hosted via Docker):

```bash
# Copy n8n-sync.sh to your PATH
cp scripts/n8n-sync.sh /usr/local/bin/n8n-sync

# Import the workflow (local Docker)
n8n-sync deploy hello.json

# Or deploy to a remote VPS
N8N_HOST=root@my-vps.com n8n-sync deploy hello.json
```

## Credentials

Workflows that use external services need credentials. nflow supports two approaches depending on whether you're starting fresh or linking to credentials that already exist in your n8n instance.

### Option 1: Generate and import credentials

When you declare `CREDENTIAL` lines in your `.nflow` file, the compiler automatically generates a separate credentials JSON file alongside the workflow:

```
CREDENTIAL @pg = postgres "Postgres Production"
CREDENTIAL @slack = slackOAuth2Api "Slack Bot"
```

```bash
nflow api.nflow -o api.json
# produces api.json (workflow) + api-credentials.json (credentials)
```

On a self-hosted n8n instance, import both using `n8n-sync`:

```bash
n8n-sync deploy api.json api-credentials.json
```

This imports the credentials first, then the workflow. After import, open n8n and fill in the actual secrets (API keys, tokens, passwords) for each credential — nflow generates the credential shells with the correct types and IDs, but not the secrets themselves.

### Option 2: Link existing n8n credentials

If credentials already exist in your n8n instance (e.g. you've configured them through the UI), you can export them and pass the file to the compiler with `-c`. This makes nflow use the real credential IDs from your instance instead of generating new ones:

```bash
# Export credentials from your n8n instance
n8n-sync export-creds credentials.json

# Compile with linked credentials — matches by name
nflow api.nflow -c credentials.json -o api.json
```

The compiler matches `CREDENTIAL` declarations to exported credentials by name. The resulting workflow JSON will contain the correct credential IDs, so it wires up to your existing credentials on import with no extra steps.

```bash
# No credentials file needed in deploy — they're already in n8n
n8n-sync deploy api.json
```

## Examples

### AI Agent with Tools

An agent with memory, LLM, and multiple tools — 30 lines instead of a wall of JSON:

```
WORKFLOW "AI Assistant" active

CREDENTIAL @gemini = googlePalmApi "Gemini Key"

TRIGGER chat AS "Chat" { public: true, title: "Assistant" }
AGENT "Bot" { systemMessage: "You are a helpful assistant." }
LLM gemini @gemini AS "Gemini" { model: "models/gemini-2.5-flash" }
MEMORY buffer AS "Memory" { contextWindowLength: 30 }
TOOL wikipedia AS "Wiki"
TOOL http AS "Jokes" {
  url: "https://v2.jokeapi.dev/joke/Any?type=single",
  description: "Gets a random joke."
}

"Chat" -> "Bot"
"Gemini" -> LLM -> "Bot"
"Memory" -> MEMORY -> "Bot"
"Wiki" -> TOOL -> "Bot"
"Jokes" -> TOOL -> "Bot"
```

### Webhook API with Postgres, Redis, Slack

```
WORKFLOW "API Processor" active

CREDENTIAL @pg = postgres "Postgres Production"
CREDENTIAL @redis = redis "Redis Cache"
CREDENTIAL @slack = slackOAuth2Api "Slack Bot"

TRIGGER webhook AS "API Request" { path: "/process", method: POST }
NODE "postgres" @pg AS "Lookup User" {
  operation: "executeQuery",
  query: "SELECT * FROM users WHERE email = '{{ $json.body.email }}'"
}
NODE "redis" @redis AS "Check Cache" {
  operation: "get",
  key: {{ "user:" + $json.id }}
}
IF "Cache Hit?" { conditions: AND [{{ $json.cachedData }} notEmpty] }
NODE "slack" @slack AS "Notify Slack" {
  resource: "message", operation: "post",
  channelId: "#alerts",
  text: {{ "New user: " + $json.email }}
}

"API Request" -> "Lookup User" -> "Check Cache" -> "Cache Hit?"
"Cache Hit?" -> FALSE -> "Notify Slack"
```

### Data Pipeline with Scheduling

```
WORKFLOW "Hourly ETL" active

CREDENTIAL @pg = postgres "Analytics DB"

TRIGGER cron AS "Every Hour" { expression: "0 * * * *" }
NODE "postgres" @pg AS "Fetch Orders" {
  operation: "executeQuery",
  query: "SELECT * FROM orders WHERE created_at > NOW() - INTERVAL '1 hour'"
}
NODE "removeDuplicates" AS "Deduplicate" {
  operation: "removeDuplicateInputItems",
  compare: "selectedFields",
  fieldsToCompare: "order_id"
}
NODE "sort" AS "Sort" {
  type: "simple",
  sortFieldsUi: { sortField: [{ fieldName: "amount", order: "descending" }] }
}
NODE "convertToFile" AS "Export CSV" {
  operation: "csv",
  binaryPropertyName: "report_csv"
}

"Every Hour" -> "Fetch Orders" -> "Deduplicate" -> "Sort" -> "Export CSV"
```

See the [`examples/`](examples/) folder for more: multi-branch DevOps alerting, MongoDB REST APIs, Google Sheets sync, and more.

## Usage

```
nflow <input.nflow> [options]

Options:
  -o, --output FILE   Write output to FILE (default: stdout)
  -c, --creds  FILE   Link existing n8n credentials by name
  --stdin             Read source from stdin
  --validate          Check syntax without producing output
  --compact           Emit compact JSON (no indentation)
  -q, --quiet         Suppress informational messages
  -V, --version       Show version and exit
```

## What It Supports

**20+ ergonomic keywords** for common patterns — `TRIGGER`, `SET`, `HTTP`, `CODE`, `IF`, `FILTER`, `MERGE`, `SWITCH`, `GSHEET`, `GDRIVE`, `AGENT`, `LLM`, `MEMORY`, `TOOL`, `LOOP`, `DATETIME`, `LIMIT`, and more.

**Generic `NODE` keyword** for all 547 n8n node types — Postgres, Redis, Slack, Telegram, MongoDB, S3, Stripe, anything. The node registry handles type-aware parameter serialization automatically.

**Connections** with branch labels (`TRUE`/`FALSE`, `OK`/`ERR`) and AI routing (`LLM`, `TOOL`, `MEMORY`).

**Credentials** declared once, referenced anywhere with `@name`.

## Language Reference

See [docs/NFLOW.md](docs/NFLOW.md) for the full grammar, all node types, and connection syntax.

## Development

```bash
pytest -v                                # run tests
nflow examples/agent.nflow -o agent.json # compile an example
```

Zero external dependencies — just Python 3.10+.
