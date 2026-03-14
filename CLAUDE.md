# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

nflow is a compact DSL (domain-specific language) that compiles `.nflow` files into valid n8n workflow JSON. It turns ~50 lines of declarative syntax into ~5,000 lines of n8n-compatible JSON. When credentials are declared, the compiler also generates a separate credentials JSON file for import into n8n.

The compiler supports two modes of operation:
- **Ergonomic keywords** (`TRIGGER`, `HTTP`, `GSHEET`, etc.) for common nodes with hand-crafted DSL syntax.
- **Generic `NODE` keyword** for any of the 500+ n8n nodes, using the node registry for type-aware parameter serialization and automatic version resolution.

## Project Structure

```
src/nflow/
  __init__.py                — Public API re-exports
  __main__.py                — python -m nflow entry point
  compiler.py                — The entire compiler (tokenizer → parser → emitter)
tests/
  test_compiler.py           — Unit + integration tests (pytest)
examples/
  agent.nflow                — AI agent with tools
  reddit.nflow               — Reddit API with credentials
scripts/
  n8n-sync.sh                — Export/import credentials & workflows via Docker
  extract-node-registry.py   — Extract node definitions from n8n source into node-registry.json
docs/
  NFLOW.md                   — Full language grammar and reference
node-registry.json           — Extracted n8n node definitions (547 nodes, generated)
```

## Commands

```bash
# Run the compiler
nflow input.nflow -o output.json
nflow input.nflow -c creds.json -o output.json  # link existing n8n credentials
nflow input.nflow --validate       # syntax check only
nflow --stdin -o output.json
python3 -m nflow input.nflow -o output.json  # without install

# Docker: export/import credentials & workflows (see scripts/n8n-sync.sh)
n8n-sync export-creds credentials.json
n8n-sync deploy workflow.json credentials.json

# Regenerate node registry from n8n source (requires built n8n repo)
python3 scripts/extract-node-registry.py /path/to/n8n-repo -o node-registry.json --include-credentials --stats

# Run all tests
pytest -v

# Run a single test
pytest -v -k "test_name"

# Install as CLI tool
pip install .
```

## Architecture

The compiler (`src/nflow/compiler.py`) follows a pipeline: **tokenize → parse → emit JSON**.

- **Tokenizer** (`tokenize_lines`): Splits source into logical lines, handling line continuations, multi-line `{ }` blocks, and triple-backtick code blocks.
- **Parser** (`N8nFDLParser`): Single-class parser that processes logical lines top-to-bottom. Each line starts with a keyword (`WORKFLOW`, `CREDENTIAL`, `TRIGGER`, `SET`, `HTTP`, `CODE`, `IF`, `FILTER`, `MERGE`, `SWITCH`, `GSHEET`, `GDRIVE`, `AGENT`, `LLM`, `MEMORY`, `TOOL`, `NODE`, `NOOP`, `NOTE`, `POSITION`, `DATETIME`, `LIMIT`, `LOOP`) or is a connection line (`"A" -> "B"`).
- **Node model** (`Node` dataclass): Each parsed node becomes a `Node` with name, type, parameters, position, and metadata. Connections are stored as `Connection` named tuples.
- **Emitter** (`to_n8n_json`): Converts parsed nodes and connections into n8n's workflow JSON schema, resolving credential references, generating UUIDs, and computing auto-layout positions. Also generates a credentials JSON file (`_build_credentials`) with deterministic 16-char alphanumeric IDs derived from credential names (`generate_credential_id`).
- **Node Registry** (`NodeRegistry`): Lazily loads `node-registry.json` to provide n8n node metadata (parameter types, versions, credentials) for the generic `NODE` keyword and as a fallback for version resolution across all nodes.
- **Type Serialization** (`serialize_param`, `serialize_node_params`): Converts DSL values into the correct n8n JSON format based on property type from the registry (e.g. `resourceLocator` → `{__rl: true, mode: ..., value: ...}`).
- **Error handling** (`NflowError`): All parse errors include source line numbers. Connection validation catches references to non-existent nodes. Exit codes: 0 success, 1 parse error, 2 file/IO error.

Key helpers: `parse_kv_block` (recursive JSON-like block parser), `parse_condition_line`/`parse_conditions_block` (IF/FILTER condition parsing), `smart_split` (comma splitting respecting nested brackets/quotes), `generate_credential_id` (deterministic credential IDs).

## Node Registry

The node registry (`node-registry.json`) is extracted from the n8n source code and contains metadata for all 547 n8n nodes (418 core + 111 AI/LangChain + 18 tool nodes). It is used by:

1. **`Node._default_version()`** — Resolves `typeVersion` for any node, falling back from hardcoded values to registry lookup.
2. **`parse_node()` (the `NODE` keyword)** — Looks up the node type, wires credentials, and runs type-aware parameter serialization.
3. **`serialize_node_params()`** — Transforms DSL parameter values into the JSON structures n8n expects based on the property type (resourceLocator, collection, fixedCollection, etc.).

### Regenerating the registry

The registry is generated from a built n8n repo using `scripts/extract-node-registry.py`:

```bash
# 1. Build n8n (one-time)
cd /path/to/n8n && pnpm install && pnpm build

# 2. Extract registry
python3 scripts/extract-node-registry.py /path/to/n8n -o node-registry.json --include-credentials --stats
```

The script reads `dist/types/nodes.json` from both `packages/nodes-base` and `packages/@n8n/nodes-langchain`, filters out UI-only fields (`displayName`, `description`, `icon`, `displayOptions`, `routing`, etc.), keeps only compiler-relevant fields (`name`, `type`, `default`, `required`, `values`, `modes`, `children`), and outputs a compact registry (~5MB, ~2MB compact).

### Parameter type serialization map

The finite set of n8n property types that produce meaningful JSON output:

| Property Type | JSON Output |
|---|---|
| `string` | Raw string, or `"={{ expr }}"` for expressions |
| `number` | Number |
| `boolean` | Boolean |
| `options` | One of the `option[].value` strings |
| `multiOptions` | Array of `option[].value` strings |
| `json` | Raw JSON string or object |
| `collection` | `{ key: value }` object from sub-properties |
| `fixedCollection` | `{ group: { field: val } }` or `{ group: [{ field: val }] }` if multipleValues |
| `resourceLocator` | `{ __rl: true, mode: string, value: string }` |
| `filter` | n8n filter condition structure |
| `assignmentCollection` | `{ assignments: [{ name, value, type }] }` |

UI-only types (`notice`, `callout`, `button`, `icon`, `curlImport`) produce no JSON output.

## DSL Reference

See `docs/NFLOW.md` for the full language grammar and examples. Node types include: TRIGGER, SET, HTTP, CODE, IF, FILTER, MERGE, SWITCH, GSHEET, GDRIVE, AGENT, LLM, MEMORY, TOOL, NODE, NOOP, NOTE, DATETIME, LIMIT, LOOP. Connections use `->` with optional branch labels (TRUE/FALSE, OK/ERR) and AI routing keywords (LLM, TOOL, MEMORY).

## Test Structure

Tests in `tests/test_compiler.py` use pytest and are organized by component: tokenizer, helpers (`parse_value`, `smart_split`, `parse_kv_block`, etc.), individual node parsers, connections, and full integration tests. Integration tests parse the example `.nflow` files from `examples/`. Tests mock `uuid.uuid4` for deterministic output.
