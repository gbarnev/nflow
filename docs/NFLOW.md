# nflow — A compact language for n8n workflows

**nflow** compiles human-readable `.nflow` files into valid n8n workflow JSON. Describe your automations in ~50 lines instead of ~5,000.

```
python3 nflow.py my_workflow.nflow output.json
```

---

## Quick Start

```nflow
WORKFLOW "My First Automation" active

CREDENTIAL @myapi = httpHeaderAuth "My API Key"

TRIGGER webhook AS "Incoming Hook" { path: "/hook", method: POST }
SET "Config" { baseUrl: "https://api.example.com" } +passthrough
HTTP POST {{ $json.baseUrl }}/items @myapi AS "Create Item" {
  jsonBody: {{ JSON.stringify($json) }}
}
IF "Success?" { conditions: AND [{{ $json.status }} equals "ok"] }

// Connections
"Incoming Hook" -> "Config" -> "Create Item" -> "Success?"
"Success?" -> TRUE -> ...
"Success?" -> FALSE -> ...
```

---

## 1. Workflow & Credentials

```nflow
WORKFLOW "Name" [active]

CREDENTIAL @alias = credentialType "Display Name"
// e.g.
CREDENTIAL @myapi = httpHeaderAuth "My API Key"
CREDENTIAL @bearer = httpBearerAuth "Bearer Auth Token"
CREDENTIAL @gsheets = googleSheetsOAuth2Api "Google Sheets Account"
```

When compiling with `-o`, a separate `<name>-credentials.json` file is generated alongside the workflow JSON. Credential IDs are deterministic (derived from the credential name), so re-compiling always produces the same IDs.

**Import order:** import the credentials file first, then the workflow.

### Linking existing n8n credentials

If you already have credentials in n8n, export them and pass the file with `-c`:

```bash
nflow input.nflow -c credentials.json -o output.json
```

The compiler matches `CREDENTIAL` declarations by name against the exported file and uses the real n8n IDs. No credentials file is generated for linked credentials — just import the workflow directly.

### Deploying to n8n (Docker)

Use `scripts/n8n-sync.sh` to export/import credentials and workflows. Set `N8N_HOST` for a remote VPS:

```bash
# Export credentials from remote VPS
N8N_HOST=root@my-vps.com ./scripts/n8n-sync.sh export-creds credentials.json

# Compile with linked credentials, then deploy
nflow api.nflow -c credentials.json -o api.json
N8N_HOST=root@my-vps.com ./scripts/n8n-sync.sh deploy api.json

# Or compile without linking (generates api-credentials.json), deploy both
nflow api.nflow -o api.json
N8N_HOST=root@my-vps.com ./scripts/n8n-sync.sh deploy api.json api-credentials.json
```

| Variable | Default | Purpose |
|----------|---------|---------|
| `N8N_HOST` | *(empty = local Docker)* | SSH destination, e.g. `root@my-vps.com` |
| `N8N_CONTAINER` | `n8n-n8n-1` | Docker container name |

---

## 2. Node Types

### TRIGGER — Entry points

```nflow
TRIGGER manual AS "Run Manually"

TRIGGER gsheets_update AS "Sheet Trigger" {
  doc: "https://docs.google.com/spreadsheets/d/.../edit",
  sheet: "Sheet1",
  event: rowUpdate,
  watch: ["trigger"],
  poll: everyMinute
}

TRIGGER webhook AS "Webhook" { path: "/my-hook", method: POST }
TRIGGER webhook AS "Streaming Hook" { method: POST, responseMode: streaming }
TRIGGER webhook AS "Last Node" { responseMode: lastNode, options: { rawBody: true } }
TRIGGER cron AS "Schedule" { expression: "0 * * * *" }
TRIGGER form @basicauth AS "My Form" {
  formTitle: "Contact Us",
  formDescription: "Fill out this form",
  authentication: basicAuth,
  formFields: { values: [
    {},
    { fieldType: "email" },
    { fieldType: "number" }
  ]}
}
```

### SET — Assign variables

```nflow
SET "Config" { apiUrl: "https://...", sheetName: "data" } +passthrough
SET "Extract ID" { id: {{ $json.event.id }} }
```

`+passthrough` keeps all upstream fields (like n8n's "Include Other Fields").

### HTTP — API requests

```nflow
// Simple
HTTP GET https://api.example.com/items @myauth AS "Get Items"

// With body, query, headers
HTTP POST https://api.example.com/items @myauth AS "Create" onError:continue {
  body: { name: {{ $json.name }}, type: "default" },
  query: { page: 1 },
  headers: { "X-Custom": "value" }
}

// Raw JSON body
HTTP POST https://api.example.com/items @myauth AS "Create" {
  jsonBody: {{ JSON.stringify($json) }}
}
```

### CODE — JavaScript

```nflow
// Inline (single backticks)
CODE "Transform" `return $input.all().map(i => ({json: i.json.data}));`

// Multi-line (triple backticks)
CODE "Complex Logic" ```
const items = $input.all();
for (const item of items) {
  item.json.processed = true;
}
return items;
```

### FILTER — Drop non-matching items (single output)

```nflow
FILTER "Active Only" { conditions: AND [
  {{ $json.status }} equals "active",
  {{ $json.id }} notEmpty
]}
```

### IF — Conditional branch (TRUE / FALSE outputs)

```nflow
IF "Is Admin?" { conditions: AND [{{ $json.role }} equals "admin"] }
IF "Has Data?" { conditions: OR [{{ $json.items }} arrayNotEmpty] }
```

**Available operators:** `equals`, `notEquals`, `contains`, `notContains`, `startsWith`, `endsWith`, `empty`, `notEmpty`, `exists`, `notExists`, `gt`, `gte`, `lt`, `lte`, `numEquals`, `isTrue`, `isFalse`, `regex`, `arrayEmpty`, `arrayNotEmpty`

### SWITCH — N-way conditional routing

```nflow
// Each rule is a routing output; reuses IF/FILTER condition syntax
SWITCH "Route By Type" { rules: [
  AND [{{ $json.type }} equals "email"],
  AND [{{ $json.type }} equals "sms"],
  AND [{{ $json.type }} equals "push"]
]}

// Rules with multiple conditions per branch
SWITCH "Complex Route" { rules: [
  AND [{{ $json.type }} equals "email", {{ $json.priority }} gt 5],
  OR [{{ $json.active }} isTrue, {{ $json.admin }} isTrue]
], looseTypeValidation: true, options: { ignoreCase: true } }

// Connect outputs by index
"Route By Type" -> 0 -> "Handle Email"
"Route By Type" -> 1 -> "Handle SMS"
"Route By Type" -> 2 -> "Handle Push"
```

### MERGE — Combine branches

```nflow
MERGE "Combine All" { mode: combine, by: position, inputs: 3 }
MERGE "Pick Branch" { mode: chooseBranch, useInput: 2 }
MERGE "Append" { mode: append }
```

### DATETIME — Date & time operations

```nflow
DATETIME "Extract Week" { operation: extractDate, part: week }
DATETIME "Format Date" { operation: formatDate, date: {{ $json.created }}, format: "MM/DD/YYYY" }
DATETIME "Current Date" { operation: getCurrentDate, includeCurrentTime: true }
DATETIME "Time Between" {
  operation: getTimeBetweenDates,
  startDate: {{ $json.start }},
  endDate: {{ $json.end }},
  units: ["day", "hour"]
}
DATETIME "Add 7 Days" { operation: addToDate, duration: 7, timeUnit: days }
```

**Operations:** `addToDate`, `extractDate`, `formatDate`, `getCurrentDate`, `getTimeBetweenDates`, `roundDate`, `subtractFromDate`

### LIMIT — Keep first/last N items

```nflow
LIMIT "First 10" { maxItems: 10 }
LIMIT "Last 5" { maxItems: 5, keep: lastItems }
```

### LOOP — Loop Over Items (Split in Batches)

```nflow
LOOP "Process Items" { batchSize: 1 }
LOOP "Batch of 10" { batchSize: 10 }

// Output 0 = done (all processed), Output 1 = loop body (current batch)
"Process Items" -> LOOP -> "Do Work"
"Do Work" -> "Process Items"
"Process Items" -> DONE -> "All Finished"
```

### GSHEET — Google Sheets

```nflow
GSHEET READ @gsheets AS "Get Rows" {
  doc: "https://docs.google.com/.../edit",
  sheet: "Sheet1"
}

GSHEET UPDATE @gsheets AS "Write Status" {
  doc: {{ $('Config').item.json.DocUrl }},
  sheet: {{ $('Config').item.json.SheetName }},
  match: ["ID"],
  values: { ID: {{ $json.ID }}, status: "done" }
}
```

### GDRIVE — Google Drive

```nflow
GDRIVE DOWNLOAD @gdrive AS "Get File" { fileId: {{ $json.fileId }} }
```

### NODE — Generic node (any n8n node type)

The `NODE` keyword lets you use **any** of the 500+ n8n nodes, even those without a dedicated DSL keyword. The compiler looks up the node type in the registry to resolve the correct version and serialize parameters.

```nflow
// Postgres query
NODE "n8n-nodes-base.postgres" @pg AS "Query DB" {
  operation: "executeQuery",
  query: "SELECT * FROM users WHERE active = true"
}

// Redis cache
NODE "n8n-nodes-base.redis" @redis AS "Cache Result" {
  operation: "set",
  key: "users_cache",
  value: {{ JSON.stringify($json) }},
  expire: true,
  ttl: 3600
}

// Respond to webhook
NODE "n8n-nodes-base.respondToWebhook" AS "Respond" {
  respondWith: "json",
  responseBody: {{ JSON.stringify($json) }}
}

// Slack message
NODE "n8n-nodes-base.slack" @slack AS "Send Alert" {
  resource: "message",
  operation: "post",
  channel: "#alerts",
  text: {{ "Alert: " + $json.message }}
}

// Google Calendar event
NODE "n8n-nodes-base.googleCalendar" @gcal AS "Create Event" {
  resource: "event",
  operation: "create",
  calendarId: "primary",
  start: "2025-01-01T10:00:00",
  end: "2025-01-01T11:00:00"
}

// Short form: if no dot in type name, "n8n-nodes-base." is assumed
NODE "redis" @redis AS "Get Key" { operation: "get", key: "mykey" }

// All standard settings work: credentials, flags, options
NODE "n8n-nodes-base.httpRequest" @api AS "Fetch" +retry onError:continue {
  method: "GET",
  url: "https://api.example.com/data",
  options: { timeout: 5000 }
}
```

The `NODE` keyword supports all standard features: `@credential` references, `AS "Name"`, flags (`+once`, `+retry`, `onError:`, `disabled`, `notes:`), and `options: { }` blocks.

**When to use NODE vs ergonomic keywords:** Use `HTTP`, `GSHEET`, `TRIGGER`, etc. for their concise syntax when available. Use `NODE` for any n8n node not covered by a dedicated keyword (Postgres, Redis, Slack, Notion, Jira, Airtable, etc.).

> **Full parameter reference for all 547 nodes:** See [NODE-CATALOG.md](NODE-CATALOG.md) for the compact index, or open [nodes/](nodes/index.md) for detailed parameters and examples.

### NOOP — Passthrough / convergence point

```nflow
NOOP "Forward Data"
```

### NOTE — Sticky note (visual only)

```nflow
NOTE "Reminder" { content: "This section handles tickets", color: 4 }
```

---

## 2b. AI Agent Nodes

nflow has first-class support for n8n's AI/LangChain nodes.

### AGENT — The AI agent hub

```nflow
AGENT "My Agent" {
  systemMessage: "You are a helpful assistant..."
}
```

### LLM — Language model provider

```nflow
LLM gemini @gemini AS "Gemini" { model: "models/gemini-2.5-flash", temperature: 0 }
LLM openai @openai AS "GPT-4" { model: "gpt-4.1-mini", temperature: 0 }
LLM anthropic @claude AS "Claude" { model: "claude-sonnet-4-20250514", temperature: 0 }
LLM ollama @ollama AS "Local LLM" { model: "llama3" }

// Disable a node (present but inactive)
LLM openai AS "Backup LLM" disabled { model: "gpt-4.1-mini" }
```

### MEMORY — Conversation memory

```nflow
MEMORY buffer AS "Chat Memory" { contextWindowLength: 30 }
```

### TOOL — Agent tools / superpowers

```nflow
// HTTP tool (fetch external API)
TOOL http AS "get_a_joke" {
  url: "https://jokeapi.dev/joke/Any",
  description: "Gets a random joke.",
  optimizeResponse: true,
  fields: "joke"
}

// Wikipedia lookup
TOOL wikipedia AS "wikipedia"

// Code tool (custom JS logic)
TOOL code AS "calculate_loan" {
  description: "Calculates monthly loan payment.",
  schemaExample: '{"amount": 250000, "rate": 6.5, "years": 30}',
  code: ```
const monthly = query.amount * (query.rate / 1200);
return JSON.stringify({ payment: monthly.toFixed(2) });
```
}

// RSS feed reader
TOOL rss AS "blog_feed" {
  url: "https://example.com/rss",
  description: "Gets latest blog posts."
}

// Crypto / password generator
TOOL crypto AS "make_password" {
  action: "generate",
  description: "Generate a secure password.",
  encodingType: "base64"
}

// Date/time calculator
TOOL datetime AS "days_until" {
  operation: "getTimeBetweenDates",
  description: "Days between now and a date."
}
```

### TRIGGER chat — Chat interface trigger

```nflow
TRIGGER chat AS "Chat Window" {
  public: true,
  title: "My AI Assistant",
  subtitle: "Ask me anything!",
  initialMessages: "Hi there! 👋",
  responseMode: "lastNode"
}
```

---

## 3. Connections

```nflow
// Sequential
"A" -> "B" -> "C"

// Branching (IF nodes or onError)
"Check" -> TRUE -> "Yes Path"
"Check" -> FALSE -> "No Path"
"API Call" -> OK -> "Handle Response"
"API Call" -> ERR -> "Handle Error"

// Parallel fan-out (one source → multiple targets)
"Source" -> "Target A", "Target B", "Target C"

// Merge input targeting (which input slot)
"Branch A" -> "Merge":0
"Branch B" -> "Merge":1
"Branch C" -> "Merge":2

// Numeric output routing (for Switch and other N-output nodes)
"Switch" -> 0 -> "First Branch"
"Switch" -> 1 -> "Second Branch"
"Switch" -> 2 -> "Third Branch"

// Loop routing (DONE = all processed, LOOP = current batch)
"Loop" -> DONE -> "All Finished"
"Loop" -> LOOP -> "Process Item"
"Process Item" -> "Loop"

// AI connections (LLM, memory, tools wired to an agent)
"Gemini" -> LLM -> "My Agent"
"Chat Memory" -> MEMORY -> "My Agent"
"get_a_joke" -> TOOL -> "My Agent"
"wikipedia" -> TOOL -> "My Agent"
```

**AI routing keywords:** `LLM` (ai_languageModel), `TOOL` (ai_tool), `MEMORY` (ai_memory), `OUTPUT_PARSER`, `RETRIEVER`, `EMBEDDING`, `DOCUMENT`, `TEXT_SPLITTER`, `VECTOR_STORE`

---

## 4. Node Options

Any node that supports n8n options can include an `options: { ... }` block. Options are passed through directly to the n8n node's `parameters.options` object.

```nflow
// HTTP with timeout and proxy
HTTP POST https://api.example.com @myapi AS "Create" {
  body: { name: "test" },
  options: { timeout: 10000, proxy: "http://myproxy:3821", allowUnauthorizedCerts: true }
}

// Google Sheets with locale
GSHEET READ @gsheets AS "Get Rows" {
  doc: "https://docs.google.com/.../edit",
  sheet: "Sheet1",
  options: { locale: "en", autoRecalc: "ON_CHANGE" }
}

// Merge with reset
MERGE "Batch" { mode: append, options: { reset: true } }

// Agent with extra options alongside systemMessage
AGENT "Bot" { systemMessage: "You are helpful", options: { maxIterations: 10 } }

// SET with dotNotation
SET "Config" { apiUrl: "https://..." } +passthrough
// options: { dotNotation: true } can be added to any SET block
```

Shorthand keys like `systemMessage` (AGENT), `temperature` (LLM), and `title`/`subtitle` (TRIGGER chat) continue to work. If the same key appears in both the shorthand and `options:`, the `options:` value takes precedence.

---

## 5. Node Settings

Any node can carry inline settings that control n8n execution behavior. Boolean settings use a `+` prefix; value settings use `key:value` or `key:"value"` syntax.

```nflow
// Execute once, always output data, retry on fail
HTTP POST https://api.example.com @myapi AS "Create" +once +always +retry {
  body: { name: "test" }
}

// Retry with explicit max-tries and wait
HTTP GET https://api.example.com @myapi AS "Fetch" retry:5 wait:2000

// Error handling variants
HTTP GET https://api.example.com AS "Safe Call" onError:continue   // → continueErrorOutput
HTTP GET https://api.example.com AS "Keep Going" onError:output    // → continueRegularOutput
HTTP GET https://api.example.com AS "Strict" onError:stop          // → stopWorkflow

// Inline notes (displayed on the canvas)
CODE "Transform" +once notes:"Normalize upstream payload" `return $input.all();`

// Combine everything
HTTP POST https://api.example.com @myapi AS "Critical" +once +always retry:3 wait:1000 onError:continue notes:"Auth required" {
  jsonBody: {{ JSON.stringify($json) }}
}
```

| Setting | Syntax | n8n JSON output |
|---|---|---|
| Execute once | `+once` | `executeOnce: true` |
| Always output data | `+always` | `alwaysOutputData: true` |
| Retry on fail | `+retry` | `retryOnFail: true` |
| Retry with max tries | `retry:N` | `retryOnFail: true, maxTries: N` |
| Wait between retries | `wait:N` | `waitBetweenTries: N` (ms) |
| Error → error output | `onError:continue` | `onError: "continueErrorOutput"` |
| Error → regular output | `onError:output` | `onError: "continueRegularOutput"` |
| Error → stop workflow | `onError:stop` | `onError: "stopWorkflow"` |
| Inline notes | `notes:"text"` | `notes: "text", notesInFlow: true` |
| Disable node | `disabled` | `disabled: true` |

---

## 6. Layout (optional)

```nflow
POSITION "Node Name" (100, 200)
```

Auto-calculated if omitted.

---

## 7. Comments

```nflow
// Single-line comments anywhere
```

---

## Reference: Full Grammar

| Element | Syntax | Purpose |
|---|---|---|
| **Workflow** | | |
| `WORKFLOW` | `WORKFLOW "Name" [active]` | Declare workflow |
| `CREDENTIAL` | `CREDENTIAL @alias = type "Name"` | Reusable auth |
| **Triggers** | | |
| `TRIGGER` | `TRIGGER type AS "Name" { ... }` | Entry point (manual, webhook, cron, gsheets_update, chat, form) |
| **Data Nodes** | | |
| `SET` | `SET "Name" { k: v } [+passthrough]` | Assign variables |
| `HTTP` | `HTTP METHOD url @cred AS "Name" { ... }` | API request |
| `CODE` | `CODE "Name" \`...\`` | JavaScript transform |
| `FILTER` | `FILTER "Name" { conditions: ... }` | Keep matching items |
| `IF` | `IF "Name" { conditions: ... }` | Branch TRUE/FALSE |
| `SWITCH` | `SWITCH "Name" { rules: [...] }` | N-way conditional routing |
| `MERGE` | `MERGE "Name" { mode: ... }` | Combine branches |
| `DATETIME` | `DATETIME "Name" { operation: ... }` | Date & time operations |
| `LIMIT` | `LIMIT "Name" { maxItems: N }` | Keep first/last N items |
| `LOOP` | `LOOP "Name" { batchSize: N }` | Loop over items in batches |
| `GSHEET` | `GSHEET OP @cred AS "Name" { ... }` | Google Sheets |
| `GDRIVE` | `GDRIVE OP @cred AS "Name" { ... }` | Google Drive |
| `NODE` | `NODE "type" @cred AS "Name" { ... }` | Any n8n node (generic, 500+ supported) |
| `NOOP` | `NOOP "Name"` | Passthrough |
| `NOTE` | `NOTE "Name" { content: ... }` | Sticky note |
| **AI Agent Nodes** | | |
| `AGENT` | `AGENT "Name" { systemMessage: "..." }` | AI agent hub |
| `LLM` | `LLM provider @cred AS "Name" { ... }` | Language model (gemini, openai, anthropic, ollama) |
| `MEMORY` | `MEMORY type AS "Name" { ... }` | Conversation memory (buffer, postgres, redis) |
| `TOOL` | `TOOL type AS "Name" { ... }` | Agent tool (http, code, wikipedia, rss, crypto, datetime) |
| **Connections** | | |
| `->` | `"A" -> "B"` | Connect nodes |
| `-> TRUE/FALSE ->` | `"If" -> TRUE -> "B"` | Branch output |
| `-> OK/ERR ->` | `"Http" -> ERR -> "B"` | Error routing |
| `-> N ->` | `"Switch" -> 0 -> "B"` | Numeric output index |
| `-> DONE/LOOP ->` | `"Loop" -> DONE -> "B"` | Loop routing (done/body) |
| `-> LLM/TOOL/MEMORY ->` | `"Gemini" -> LLM -> "Agent"` | AI connection |
| `"Node":N` | `"A" -> "Merge":1` | Target input slot |
| **Node Settings** | | |
| `+once` | `HTTP GET url +once` | Execute once per run |
| `+always` | `SET "X" +always { ... }` | Always output data |
| `+retry` / `retry:N` | `HTTP GET url +retry` or `retry:5` | Retry on fail (optional max tries) |
| `wait:N` | `HTTP GET url retry:3 wait:2000` | Wait between retries (ms) |
| `onError:X` | `onError:continue` / `output` / `stop` | Error handling mode |
| `notes:"…"` | `notes:"Check auth"` | Inline note (shown on canvas) |
| `disabled` | `LLM openai AS "X" disabled { ... }` | Disable a node |
| **Other** | | |
| `options` | `options: { key: val }` | n8n node options (any node) |
| `{{ expr }}` | `{{ $json.field }}` | n8n expression |
| `//` | `// comment` | Comment |

---

## 8. Node Registry

nflow includes a **node registry** (`node-registry.json`) that contains metadata for all 547 built-in n8n nodes. This enables the generic `NODE` keyword and provides automatic version resolution for all nodes.

### What the registry contains

For each n8n node, the registry stores only what the compiler needs:

- **version** — the latest `typeVersion` number
- **group** — whether it's a trigger, transform, etc.
- **inputs/outputs** — connection types (main, ai_tool, ai_memory, etc.)
- **credentials** — which credential types the node accepts
- **properties** — every parameter with its name, type, default value, valid options, and nested children

UI-only fields (`displayName`, `description`, `icon`, `placeholder`, `displayOptions`, `routing`) are stripped to keep the registry compact.

### How the compiler uses it

1. **Version resolution** — When emitting `typeVersion`, the compiler checks hardcoded values first (for ergonomic keywords), then falls back to the registry. This means even a `NODE "n8n-nodes-base.notion"` call gets the correct version without any hardcoding.

2. **Type-aware serialization** — The `NODE` keyword uses registry property types to serialize DSL values into the correct JSON format. For example, a `resourceLocator` property is automatically wrapped as `{"__rl": true, "mode": "url", "value": "..."}`.

3. **Graceful fallback** — If `node-registry.json` is not present, the compiler still works for all ergonomic keywords. Only the `NODE` keyword benefits from the registry; it is not required for existing syntax.

### Regenerating the registry

The registry is extracted from a built n8n repository:

```bash
# 1. Build n8n (requires the n8n source repo)
cd /path/to/n8n
pnpm install
pnpm build --filter=n8n-nodes-base...
pnpm build --filter=@n8n/n8n-nodes-langchain...

# 2. Extract into nflow project
python3 scripts/extract-node-registry.py /path/to/n8n \
  -o node-registry.json \
  --include-credentials \
  --stats
```

This reads `dist/types/nodes.json` from both `packages/nodes-base` (core nodes) and `packages/@n8n/nodes-langchain` (AI nodes), filters to compiler-relevant fields, keeps only the latest version of each node, and writes the registry.

### Parameter type mapping

The registry tracks n8n property types. The compiler serializes each type as follows:

| n8n Property Type | JSON Output Format |
|---|---|
| `string` | Raw string, or `"={{ expr }}"` for expressions |
| `number` | Number |
| `boolean` | `true` / `false` |
| `options` | One of the allowed value strings |
| `multiOptions` | Array of value strings |
| `json` | Raw JSON string or object |
| `collection` | `{ key: value, ... }` object |
| `fixedCollection` | `{ groupName: { ... } }` or `{ groupName: [{ ... }] }` |
| `resourceLocator` | `{ __rl: true, mode: "url"/"id"/"list", value: "..." }` |
| `filter` | n8n filter condition structure |
| `assignmentCollection` | `{ assignments: [{ name, value, type }] }` |
