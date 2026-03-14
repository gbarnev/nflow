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
TRIGGER cron AS "Schedule" { expression: "0 * * * *" }
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

### MERGE — Combine branches

```nflow
MERGE "Combine All" { mode: combine, by: position, inputs: 3 }
MERGE "Pick Branch" { mode: chooseBranch, useInput: 2 }
MERGE "Append" { mode: append }
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

// AI connections (LLM, memory, tools wired to an agent)
"Gemini" -> LLM -> "My Agent"
"Chat Memory" -> MEMORY -> "My Agent"
"get_a_joke" -> TOOL -> "My Agent"
"wikipedia" -> TOOL -> "My Agent"
```

**AI routing keywords:** `LLM` (ai_languageModel), `TOOL` (ai_tool), `MEMORY` (ai_memory), `OUTPUT_PARSER`, `RETRIEVER`, `EMBEDDING`, `DOCUMENT`, `TEXT_SPLITTER`, `VECTOR_STORE`

---

## 4. Layout (optional)

```nflow
POSITION "Node Name" (100, 200)
```

Auto-calculated if omitted.

---

## 5. Comments

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
| `TRIGGER` | `TRIGGER type AS "Name" { ... }` | Entry point (manual, webhook, cron, gsheets_update, chat) |
| **Data Nodes** | | |
| `SET` | `SET "Name" { k: v } [+passthrough]` | Assign variables |
| `HTTP` | `HTTP METHOD url @cred AS "Name" { ... }` | API request |
| `CODE` | `CODE "Name" \`...\`` | JavaScript transform |
| `FILTER` | `FILTER "Name" { conditions: ... }` | Keep matching items |
| `IF` | `IF "Name" { conditions: ... }` | Branch TRUE/FALSE |
| `MERGE` | `MERGE "Name" { mode: ... }` | Combine branches |
| `GSHEET` | `GSHEET OP @cred AS "Name" { ... }` | Google Sheets |
| `GDRIVE` | `GDRIVE OP @cred AS "Name" { ... }` | Google Drive |
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
| `-> LLM/TOOL/MEMORY ->` | `"Gemini" -> LLM -> "Agent"` | AI connection |
| `"Node":N` | `"A" -> "Merge":1` | Target input slot |
| **Other** | | |
| `{{ expr }}` | `{{ $json.field }}` | n8n expression |
| `//` | `// comment` | Comment |
| `disabled` | `LLM openai AS "X" disabled { ... }` | Disable a node |
