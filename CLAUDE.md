# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

nflow is a compact DSL (domain-specific language) that compiles `.nflow` files into valid n8n workflow JSON. It turns ~50 lines of declarative syntax into ~5,000 lines of n8n-compatible JSON. When credentials are declared, the compiler also generates a separate credentials JSON file for import into n8n.

## Project Structure

```
src/nflow/
  __init__.py       — Public API re-exports
  __main__.py       — python -m nflow entry point
  compiler.py       — The entire compiler (tokenizer → parser → emitter)
tests/
  test_compiler.py  — Unit + integration tests (pytest)
examples/
  agent.nflow       — AI agent with tools
  reddit.nflow      — Reddit API with credentials
scripts/
  n8n-sync.sh       — Export/import credentials & workflows via Docker
docs/
  NFLOW.md          — Full language grammar and reference
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
- **Parser** (`N8nFDLParser`): Single-class parser that processes logical lines top-to-bottom. Each line starts with a keyword (`WORKFLOW`, `CREDENTIAL`, `TRIGGER`, `SET`, `HTTP`, `CODE`, `IF`, `FILTER`, `MERGE`, `GSHEET`, `GDRIVE`, `AGENT`, `LLM`, `MEMORY`, `TOOL`, `NOOP`, `NOTE`, `POSITION`) or is a connection line (`"A" -> "B"`).
- **Node model** (`Node` dataclass): Each parsed node becomes a `Node` with name, type, parameters, position, and metadata. Connections are stored as `Connection` named tuples.
- **Emitter** (`to_n8n_json`): Converts parsed nodes and connections into n8n's workflow JSON schema, resolving credential references, generating UUIDs, and computing auto-layout positions. Also generates a credentials JSON file (`_build_credentials`) with deterministic 16-char alphanumeric IDs derived from credential names (`generate_credential_id`).
- **Error handling** (`NflowError`): All parse errors include source line numbers. Connection validation catches references to non-existent nodes. Exit codes: 0 success, 1 parse error, 2 file/IO error.

Key helpers: `parse_kv_block` (recursive JSON-like block parser), `parse_condition_line`/`parse_conditions_block` (IF/FILTER condition parsing), `smart_split` (comma splitting respecting nested brackets/quotes), `generate_credential_id` (deterministic credential IDs).

## DSL Reference

See `docs/NFLOW.md` for the full language grammar and examples. Node types include: TRIGGER, SET, HTTP, CODE, IF, FILTER, MERGE, GSHEET, GDRIVE, AGENT, LLM, MEMORY, TOOL, NOOP, NOTE. Connections use `->` with optional branch labels (TRUE/FALSE, OK/ERR) and AI routing keywords (LLM, TOOL, MEMORY).

## Test Structure

Tests in `tests/test_compiler.py` use pytest and are organized by component: tokenizer, helpers (`parse_value`, `smart_split`, `parse_kv_block`, etc.), individual node parsers, connections, and full integration tests. Integration tests parse the example `.nflow` files from `examples/`. Tests mock `uuid.uuid4` for deterministic output.
