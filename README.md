# nflow

A compact DSL that compiles `.nflow` files into valid [n8n](https://n8n.io) workflow JSON.
Describe your automations in ~50 lines instead of ~5,000.

## Installation

```bash
pip install .
```

Or run directly without installing:

```bash
python3 -m nflow input.nflow -o output.json
```

## Quick Start

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

Compile it:

```bash
nflow hello.nflow -o hello.json
```

Import `hello.json` into n8n.

## Usage

```
nflow <input.nflow> [options]

Options:
  -o, --output FILE   Write output to FILE (default: stdout)
  --stdin             Read source from stdin
  --validate          Check syntax without producing output
  --compact           Emit compact JSON (no indentation)
  -q, --quiet         Suppress informational messages
  -V, --version       Show version and exit
```

## Examples

### Webhook + API + Conditional

```
WORKFLOW "API Processor" active

CREDENTIAL @api = httpHeaderAuth "My API Key"

TRIGGER webhook AS "Incoming" { path: "/hook", method: POST }
HTTP POST https://api.example.com/items @api AS "Create Item" {
  jsonBody: {{ JSON.stringify($json) }}
}
IF "Success?" { conditions: AND [{{ $json.status }} equals "ok"] }
SET "Done" { result: "created" }

"Incoming" -> "Create Item" -> "Success?"
"Success?" -> TRUE -> "Done"
```

### AI Agent with Tools

```
WORKFLOW "AI Assistant" active

CREDENTIAL @gemini = googlePalmApi "Gemini Key"

TRIGGER chat AS "Chat" { public: true, title: "Assistant" }
AGENT "Bot" { systemMessage: "You are a helpful assistant." }
LLM gemini @gemini AS "Gemini" { model: "models/gemini-2.5-flash" }
MEMORY buffer AS "Memory" { contextWindowLength: 30 }
TOOL wikipedia AS "wikipedia"

"Chat" -> "Bot"
"Gemini" -> LLM -> "Bot"
"Memory" -> MEMORY -> "Bot"
"wikipedia" -> TOOL -> "Bot"
```

## Language Reference

See [docs/NFLOW.md](docs/NFLOW.md) for the full grammar, all node types, and connection syntax.

## Development

```bash
# Run tests
pytest -v

# Compile an example
nflow examples/agent.nflow -o agent.json
```

Zero external dependencies — just Python 3.10+.
