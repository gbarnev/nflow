# Write nflow

Write `.nflow` files — a compact DSL that compiles into valid n8n workflow JSON. Turns ~50 lines of declarative syntax into ~5,000 lines of n8n-compatible JSON.

**Documentation:**
- [Language Reference](https://gbarnev.github.io/nflow/NFLOW/) — full grammar, all keywords, settings, examples
- [Node Catalog](https://gbarnev.github.io/nflow/NODE-CATALOG/) — index of all 547 n8n nodes with credentials and operations
- [Node Reference](https://gbarnev.github.io/nflow/nodes/) — per-node pages with full parameter details (e.g. [Airtable Trigger](https://gbarnev.github.io/nflow/nodes/airtableTrigger/))

## File Structure

Every `.nflow` file follows this order:

1. **WORKFLOW** declaration (name, optional `active`)
2. **CREDENTIAL** declarations (reusable auth aliases)
3. **Node definitions** (triggers, logic, integrations)
4. **Connections** (wiring nodes together with `->`)

```nflow
WORKFLOW "My Automation" active

CREDENTIAL @myapi = httpHeaderAuth "My API Key"

TRIGGER webhook AS "Hook" { path: "/hook", method: POST }
SET "Config" { baseUrl: "https://api.example.com" } +passthrough
HTTP POST {{ $json.baseUrl }}/items @myapi AS "Create Item" {
  jsonBody: {{ JSON.stringify($json) }}
}

"Hook" -> "Config" -> "Create Item"
```

## Keywords Quick Reference

| Keyword | Syntax | Purpose |
|---|---|---|
| `WORKFLOW` | `WORKFLOW "Name" [active]` | Declare workflow |
| `CREDENTIAL` | `CREDENTIAL @alias = type "Name"` | Reusable auth |
| `TRIGGER` | `TRIGGER type AS "Name" { ... }` | Entry point (manual, webhook, cron, chat, form, gsheets_update) |
| `SET` | `SET "Name" { k: v } [+passthrough]` | Assign variables |
| `HTTP` | `HTTP METHOD url @cred AS "Name" { ... }` | API request |
| `CODE` | `` CODE "Name" `code` `` | JavaScript transform |
| `IF` | `IF "Name" { conditions: AND/OR [...] }` | Branch TRUE/FALSE |
| `FILTER` | `FILTER "Name" { conditions: AND/OR [...] }` | Keep matching items |
| `SWITCH` | `SWITCH "Name" { rules: [...] }` | N-way routing |
| `MERGE` | `MERGE "Name" { mode: ... }` | Combine branches |
| `DATETIME` | `DATETIME "Name" { operation: ... }` | Date/time ops |
| `LIMIT` | `LIMIT "Name" { maxItems: N }` | Keep first/last N |
| `LOOP` | `LOOP "Name" { batchSize: N }` | Loop in batches |
| `GSHEET` | `GSHEET OP @cred AS "Name" { ... }` | Google Sheets |
| `GDRIVE` | `GDRIVE OP @cred AS "Name" { ... }` | Google Drive |
| `NODE` | `NODE "type" @cred AS "Name" { ... }` | Any n8n node (500+) |
| `AGENT` | `AGENT "Name" { systemMessage: "..." }` | AI agent hub |
| `LLM` | `LLM provider @cred AS "Name" { ... }` | Language model |
| `MEMORY` | `MEMORY type AS "Name" { ... }` | Conversation memory |
| `TOOL` | `TOOL type AS "Name" { ... }` | Agent tool |
| `NOOP` | `NOOP "Name"` | Passthrough |
| `NOTE` | `NOTE "Name" { content: ... }` | Sticky note |

For full syntax of each keyword, see the [Language Reference](https://gbarnev.github.io/nflow/NFLOW/).

## Connections

```nflow
"A" -> "B" -> "C"                          // sequential
"Source" -> "Target A", "Target B"          // fan-out
"Check" -> TRUE -> "Yes"                    // IF branch
"Check" -> FALSE -> "No"
"Call" -> OK -> "Success"                   // error routing
"Call" -> ERR -> "Handle Error"
"Switch" -> 0 -> "First"                   // numeric output
"Switch" -> 1 -> "Second"
"Branch A" -> "Merge":0                     // merge input slot
"Branch B" -> "Merge":1
"Loop" -> DONE -> "Finished"               // loop routing
"Loop" -> LOOP -> "Process"
"Process" -> "Loop"
"Gemini" -> LLM -> "Agent"                 // AI connections
"Memory" -> MEMORY -> "Agent"
"tool" -> TOOL -> "Agent"
```

AI routing keywords: `LLM`, `TOOL`, `MEMORY`, `OUTPUT_PARSER`, `RETRIEVER`, `EMBEDDING`, `DOCUMENT`, `TEXT_SPLITTER`, `VECTOR_STORE`.

## Node Settings (inline flags)

Place between keyword/name and `{`:

| Setting | Syntax | Effect |
|---|---|---|
| Execute once | `+once` | Run once per execution |
| Always output data | `+always` | Output even on empty |
| Retry on fail | `+retry` or `retry:N` | Retry (optional max) |
| Wait between retries | `wait:N` | Milliseconds |
| Error handling | `onError:continue/output/stop` | Route errors |
| Inline note | `notes:"text"` | Canvas note |
| Disable node | `disabled` | Present but inactive |

```nflow
HTTP POST https://api.com @api AS "Critical" +once retry:3 wait:1000 onError:continue {
  jsonBody: {{ JSON.stringify($json) }}
}
```

## Condition Operators

For IF, FILTER, and SWITCH:

**String**: `equals`, `notEquals`, `contains`, `notContains`, `startsWith`, `endsWith`, `empty`, `notEmpty`, `exists`, `notExists`, `regex`
**Number**: `gt`, `gte`, `lt`, `lte`, `numEquals`
**Boolean**: `isTrue`, `isFalse`
**Array**: `arrayEmpty`, `arrayNotEmpty`

Unary (no right-hand value): `empty`, `notEmpty`, `exists`, `notExists`, `isTrue`, `isFalse`, `arrayEmpty`, `arrayNotEmpty`.

```nflow
IF "Check" { conditions: AND [
  {{ $json.status }} equals "active",
  {{ $json.count }} gt 0,
  {{ $json.tags }} arrayNotEmpty
]}
```

## Expressions & Values

`{{ ... }}` for n8n expressions (compiled to `={{ ... }}`):

```nflow
{{ $json.field }}
{{ $('Config').item.json.baseUrl }}
{{ JSON.stringify($json) }}
{{ "Hello " + $json.name }}
```

Value types: `"strings"`, `42` (numbers), `true`/`false`, `[arrays]`, `{ objects }`, `` `code` ``.

## NODE — Generic Node

Use for any n8n node not covered by a dedicated keyword. If no dot in type, `n8n-nodes-base.` is auto-prefixed. Browse the [Node Catalog](https://gbarnev.github.io/nflow/NODE-CATALOG/) for all 547 nodes, and open individual [Node Reference](https://gbarnev.github.io/nflow/nodes/) pages for full parameters.

```nflow
NODE "postgres" @pg AS "Query" { operation: "executeQuery", query: "SELECT * FROM users" }
NODE "slack" @slack AS "Alert" { resource: "message", operation: "post", channelId: "#alerts", text: "Done" }
NODE "redis" @redis AS "Cache" { operation: "set", key: "k", value: {{ JSON.stringify($json) }}, ttl: 3600 }
NODE "respondToWebhook" AS "Respond" { respondWith: "json", responseBody: {{ JSON.stringify($json) }} }
```

## Complete Examples

### Webhook API with DB + cache

```nflow
WORKFLOW "API" active

CREDENTIAL @pg = postgres "Postgres Production"
CREDENTIAL @redis = redis "Redis Cache"

TRIGGER webhook AS "Request" { path: "/process", method: POST }

NODE "postgres" @pg AS "Lookup User" {
  operation: "executeQuery",
  query: "SELECT * FROM users WHERE email = '{{ $json.body.email }}' LIMIT 1"
}

NODE "redis" @redis AS "Check Cache" {
  operation: "get",
  key: {{ "user:" + $json.id }}
}

IF "Cache Hit?" { conditions: AND [{{ $json.cachedData }} notEmpty] }

NODE "respondToWebhook" AS "Response" { respondWith: "json", responseBody: {{ JSON.stringify($json) }} }

"Request" -> "Lookup User" -> "Check Cache" -> "Cache Hit?"
"Cache Hit?" -> TRUE -> "Response"
"Cache Hit?" -> FALSE -> ...
```

### AI Agent with tools

```nflow
WORKFLOW "AI Assistant" active

CREDENTIAL @gemini = googlePalmApi "Gemini"

TRIGGER chat AS "Chat" { public: true, title: "Assistant", responseMode: "lastNode" }
AGENT "Bot" { systemMessage: "You are a helpful assistant." }
LLM gemini @gemini AS "Gemini" { model: "models/gemini-2.5-flash", temperature: 0 }
MEMORY buffer AS "Memory" { contextWindowLength: 30 }
TOOL wikipedia AS "wiki"
TOOL http AS "jokes" { url: "https://jokeapi.dev/joke/Any", description: "Gets a joke.", optimizeResponse: true, fields: "joke" }

"Chat" -> "Bot"
"Gemini" -> LLM -> "Bot"
"Memory" -> MEMORY -> "Bot"
"wiki" -> TOOL -> "Bot"
"jokes" -> TOOL -> "Bot"
```

### SWITCH routing with fallback

```nflow
SWITCH "Router" { rules: [
  AND [{{ $json.type }} equals "email"],
  AND [{{ $json.type }} equals "sms"]
]}
"Router" -> 0 -> "Handle Email"
"Router" -> 1 -> "Handle SMS"
"Router" -> 2 -> "Unknown Type"
```

The rule count + 1 output index is the fallback (no match).

## Critical Syntax Rules

1. **Node names must be quoted**: `AS "My Node"` and `"My Node"` in connections
2. **Credential aliases use @**: `@myapi` in both `CREDENTIAL` declarations and node definitions
3. **Blocks use `{ key: value }` syntax**: comma-separated, colon after key, nested objects supported
4. **Connections use `->` between quoted names**: `"A" -> "B"`
5. **Keywords are UPPERCASE**: `WORKFLOW`, `TRIGGER`, `SET`, `HTTP`, `NODE`, etc.
6. **Multi-line blocks**: `{ }` can span lines; the compiler auto-joins them
7. **Comments**: `// single-line` only
8. **One statement per logical line**: no semicolons; use `\` for line continuation
9. **Define all nodes first, then connections at the end**
10. **`options: { ... }`** inside any block passes through to n8n's `parameters.options`
