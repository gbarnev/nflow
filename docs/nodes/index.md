# Node Reference

Per-node documentation for all 547 n8n nodes available via the `NODE` keyword.

**Looking for a specific node?** Use the search bar above, or browse the [Node Catalog](../NODE-CATALOG.md) for a compact table of all nodes.

## How to Read a Node Page

Each page contains:

- **Credentials** — which credential type to use and the `CREDENTIAL` declaration
- **Operations** — available `resource` and `operation` values
- **Parameters** — full table with types, defaults, and valid options
- **Children** — nested parameters for `collection` and `fixedCollection` types
- **Example** — a ready-to-use nflow snippet

## NODE Syntax

```nflow
NODE "<type>" @credential AS "Display Name" [+once] [+retry] [onError:continue] {
  param1: "value",
  param2: {{ $json.field }},
  options: { key: "value" }
}
```

- Short names work: `NODE "postgres"` = `NODE "n8n-nodes-base.postgres"`
- `@credential` references a `CREDENTIAL` declared at the top of your `.nflow` file
- All [node settings](../NFLOW.md) apply: `+once`, `+retry`, `retry:N`, `wait:N`, `onError:continue|output|stop`, `disabled`, `notes:"..."`
