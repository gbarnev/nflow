#!/usr/bin/env python3
"""
Generate node reference documentation from node-registry.json.

Produces two outputs:
  1. docs/NODE-CATALOG.md  — compact index (~2K lines), one line per node.
     This is what an AI reads to know what exists and where to look.
  2. docs/nodes/<name>.md  — one file per node with full parameter tables,
     nested children, and usage examples. An AI opens only the file it needs.

Usage:
    python3 scripts/generate-node-reference.py [node-registry.json]
    python3 scripts/generate-node-reference.py --stats
"""

import json
import re
import sys
import argparse
from pathlib import Path
from collections import defaultdict

# ── Constants ─────────────────────────────────────────────────────────────────

ERGONOMIC_KEYWORDS = {
    "n8n-nodes-base.manualTrigger": "TRIGGER manual",
    "n8n-nodes-base.webhook": "TRIGGER webhook",
    "n8n-nodes-base.scheduleTrigger": "TRIGGER cron",
    "n8n-nodes-base.googleSheetsTrigger": "TRIGGER gsheets_update",
    "n8n-nodes-base.formTrigger": "TRIGGER form",
    "n8n-nodes-base.set": "SET",
    "n8n-nodes-base.httpRequest": "HTTP",
    "n8n-nodes-base.code": "CODE",
    "n8n-nodes-base.filter": "FILTER",
    "n8n-nodes-base.if": "IF",
    "n8n-nodes-base.switch": "SWITCH",
    "n8n-nodes-base.merge": "MERGE",
    "n8n-nodes-base.dateTime": "DATETIME",
    "n8n-nodes-base.limit": "LIMIT",
    "n8n-nodes-base.splitInBatches": "LOOP",
    "n8n-nodes-base.googleSheets": "GSHEET",
    "n8n-nodes-base.googleDrive": "GDRIVE",
    "n8n-nodes-base.noOp": "NOOP",
    "n8n-nodes-base.stickyNote": "NOTE",
    "@n8n/n8n-nodes-langchain.agent": "AGENT",
    "@n8n/n8n-nodes-langchain.lmChatGoogleGemini": "LLM gemini",
    "@n8n/n8n-nodes-langchain.lmChatOpenAi": "LLM openai",
    "@n8n/n8n-nodes-langchain.lmChatAnthropic": "LLM anthropic",
    "@n8n/n8n-nodes-langchain.lmChatOllama": "LLM ollama",
    "@n8n/n8n-nodes-langchain.memoryBufferWindow": "MEMORY buffer",
    "n8n-nodes-base.chatTrigger": "TRIGGER chat",
}

GROUP_ORDER = ["trigger", "input", "output", "transform", "organization", "schedule"]
GROUP_LABELS = {
    "trigger": "Triggers",
    "input": "Input / Read",
    "output": "Output / Write",
    "transform": "Transform / Process",
    "organization": "Organization",
    "schedule": "Schedule",
}

SKIP_PROP_TYPES = {"notice", "callout", "hidden", "credentials", "credentialsSelect"}

PREFERRED_OPS = [
    "executeQuery", "get", "send", "sendMessage", "post", "create",
    "find", "getAll", "execute", "list", "lookup", "search",
]

# ── Helpers ───────────────────────────────────────────────────────────────────

def short_name(full_name: str) -> str:
    if full_name.startswith("n8n-nodes-base."):
        return full_name[len("n8n-nodes-base."):]
    if full_name.startswith("@n8n/n8n-nodes-langchain."):
        return full_name[len("@n8n/n8n-nodes-langchain."):]
    return full_name


def node_filename(full_name: str) -> str:
    """Filesystem-safe filename: n8n-nodes-base.postgres -> postgres.md"""
    return short_name(full_name) + ".md"


def make_cred_alias(cred_name: str) -> str:
    alias = cred_name
    for suffix in ("OAuth2Api", "OAuth2", "Api", "Credentials", "Auth"):
        if alias.endswith(suffix) and len(alias) > len(suffix):
            alias = alias[:-len(suffix)]
            break
    words = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)", alias)
    if words:
        alias = words[0].lower()
        if len(alias) < 3 and len(words) > 1:
            alias = (words[0] + words[1]).lower()
    return alias[:16]


def make_display_name(sname: str) -> str:
    parts = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\d|$)|\d+", sname)
    if not parts:
        return sname.title()
    stop_words = {"and", "or", "to", "in", "of", "the", "by", "for", "with"}
    words = []
    for p in parts:
        if p.lower() in stop_words and words:
            words.append(p.lower())
        else:
            words.append(p.capitalize())
    return " ".join(words)


def format_default(val) -> str:
    if val is None:
        return ""
    if isinstance(val, bool):
        return str(val).lower()
    if isinstance(val, str):
        if val == "":
            return '""'
        if len(val) > 40:
            return f'"{val[:37]}..."'
        return f'"{val}"'
    if isinstance(val, dict):
        return "{}" if not val else "{...}"
    if isinstance(val, list):
        return "[]" if not val else "[...]"
    return str(val)


def format_options(values, max_show=8) -> str:
    if not values:
        return ""
    shown = values[:max_show]
    result = ", ".join(f"`{v}`" for v in shown)
    if len(values) > max_show:
        result += f", ... ({len(values)} total)"
    return result


def dedupe_properties(properties: list[dict]) -> list[dict]:
    seen = set()
    result = []
    for p in properties:
        if p.get("type") in SKIP_PROP_TYPES:
            continue
        name = p["name"]
        if name in seen:
            continue
        seen.add(name)
        result.append(p)
    return result


def get_all_operations(entry: dict) -> tuple[list[str] | None, list[str], dict[str | None, list[str]]]:
    """Merge all resource/operation values across duplicate properties.

    Returns (resources, all_operations_deduped, resource_ops_map).
    resource_ops_map maps resource_value -> [operations].
    If operations have no resource constraint, maps None -> [operations].
    """
    resources = None
    all_ops: list[str] = []
    resource_ops: dict[str | None, list[str]] = {}

    for p in entry.get("properties", []):
        if p["name"] == "resource" and p.get("type") == "options" and p.get("values"):
            resources = p["values"]
        elif p["name"] == "operation" and p.get("type") == "options" and p.get("values"):
            do = p.get("displayOptions", {})
            res_constraint = do.get("resource", [])
            ops = p["values"]
            all_ops.extend(ops)
            if res_constraint:
                for r in res_constraint:
                    resource_ops.setdefault(r, []).extend(ops)
            else:
                resource_ops.setdefault(None, []).extend(ops)

    seen: set[str] = set()
    unique_ops = []
    for op in all_ops:
        if op not in seen:
            seen.add(op)
            unique_ops.append(op)

    return resources, unique_ops, resource_ops


def group_props_by_context(properties: list[dict]) -> tuple[dict[tuple, list[dict]], list[dict]]:
    """Group properties by (resource, operation) from displayOptions.

    Returns (grouped, common) where:
    - grouped[(resource|None, operation)] = [properties]
    - common = properties without operation constraints
    """
    grouped: dict[tuple, list[dict]] = defaultdict(list)
    common: list[dict] = []

    for p in properties:
        if p["name"] in ("resource", "operation"):
            continue
        do = p.get("displayOptions", {})
        op_list = do.get("operation", [])
        res_list = do.get("resource", [])

        if op_list:
            for o in op_list:
                for r in (res_list or [None]):
                    grouped[(r, o)].append(p)
        else:
            common.append(p)

    return dict(grouped), common


# ── Param table rendering ────────────────────────────────────────────────────

def render_param_table(properties: list[dict]) -> list[str]:
    if not properties:
        return []
    lines = [
        "| Parameter | Type | Default | Details |",
        "|-----------|------|---------|---------|",
    ]
    for p in properties:
        name = p["name"]
        ptype = p.get("type", "?")
        default = format_default(p.get("default"))
        details_parts = []

        if p.get("required"):
            details_parts.append("**required**")
        if ptype == "options" and p.get("values"):
            details_parts.append(format_options(p["values"]))
        elif ptype == "multiOptions" and p.get("values"):
            details_parts.append("multi: " + format_options(p["values"]))
        elif ptype == "resourceLocator" and p.get("modes"):
            details_parts.append("modes: " + ", ".join(f"`{m}`" for m in p["modes"]))
        if ptype == "collection" and p.get("children"):
            child_names = [c["name"] for c in p["children"][:6]]
            suffix = ", ..." if len(p["children"]) > 6 else ""
            details_parts.append("keys: " + ", ".join(f"`{n}`" for n in child_names) + suffix)
        elif ptype == "fixedCollection" and p.get("children"):
            group_names = [c["name"] for c in p["children"]]
            details_parts.append("groups: " + ", ".join(f"`{n}`" for n in group_names))

        details = "; ".join(details_parts) if details_parts else ""
        lines.append(f"| `{name}` | {ptype} | {default.replace('|','\\|')} | {details.replace('|','\\|')} |")
    return lines


def render_children_detail(properties: list[dict]) -> list[str]:
    lines = []
    for p in properties:
        ptype = p.get("type", "")
        children = p.get("children", [])
        if ptype in ("collection", "fixedCollection") and children:
            lines.append("")
            lines.append(f"**`{p['name']}`** children:")
            lines.append("")
            for group in children:
                group_children = group.get("children", [])
                if group.get("type") == "group" and group_children:
                    lines.append(f"*`{group['name']}`* group:")
                    lines.append("")
                    lines.extend(render_param_table(group_children))
                    lines.append("")
            if not any(g.get("type") == "group" for g in children):
                filterable = [c for c in children if c.get("type") not in SKIP_PROP_TYPES]
                if filterable:
                    lines.extend(render_param_table(filterable))
                    lines.append("")
    return lines


# ── Example generation ────────────────────────────────────────────────────────

def generate_example(full_name: str, entry: dict) -> str:
    sname = short_name(full_name)
    props = dedupe_properties(entry.get("properties", []))

    creds = entry.get("credentials", [])
    cred_str = ""
    if creds:
        cred_str = f" @{make_cred_alias(creds[0]['name'])}"

    resources, all_ops, _ = get_all_operations(entry)
    example_params = []

    if resources:
        example_params.append(f'  resource: "{resources[0]}"')
    if all_ops:
        op = all_ops[0]
        for pref in PREFERRED_OPS:
            if pref in all_ops:
                op = pref
                break
        example_params.append(f'  operation: "{op}"')

    added = {"resource", "operation", "authentication", "returnAll"}
    for p in props:
        if p["name"] in added:
            continue
        if p.get("required") and p.get("type") in ("string", "number", "json"):
            if p["type"] == "string":
                example_params.append(f'  {p["name"]}: {{{{ $json.{p["name"]} }}}}')
            elif p["type"] == "number":
                example_params.append(f'  {p["name"]}: {p.get("default") or 0}')
            else:
                example_params.append(f'  {p["name"]}: "{p.get("default") or "{}"}"')
            added.add(p["name"])
            if len(example_params) >= 5:
                break

    if not example_params:
        for p in props:
            if p["name"] in added:
                continue
            if p.get("type") == "string" and p.get("default") == "":
                example_params.append(f'  {p["name"]}: "value"')
                added.add(p["name"])
                if len(example_params) >= 3:
                    break

    name_display = make_display_name(sname)
    if example_params:
        return f'NODE "{sname}"{cred_str} AS "{name_display}" {{\n' + ",\n".join(example_params) + "\n}"
    return f'NODE "{sname}"{cred_str} AS "{name_display}"'


# ── Per-node page generation ─────────────────────────────────────────────────

def generate_node_page(full_name: str, entry: dict) -> str:
    sname = short_name(full_name)
    version = entry.get("version", "?")
    ergonomic = ERGONOMIC_KEYWORDS.get(full_name)
    lines: list[str] = []

    lines.append(f"# {make_display_name(sname)}")
    lines.append("")
    lines.append(f"**Node:** `{sname}` · **Full type:** `{full_name}` · **Version:** {version}")

    if ergonomic:
        lines.append("")
        lines.append(f"> Ergonomic keyword available: `{ergonomic}` — see [NFLOW.md](../NFLOW.md) for shorter syntax.")

    lines.append("")

    # Credentials
    creds = entry.get("credentials", [])
    if creds:
        cred_lines = []
        for c in creds:
            alias = make_cred_alias(c["name"])
            cred_lines.append(f"`{c['name']}` (alias: `@{alias}`)")
        lines.append("## Credentials")
        lines.append("")
        lines.append(" / ".join(cred_lines))
        lines.append("")
        lines.append("```nflow")
        first = creds[0]["name"]
        lines.append(f'CREDENTIAL @{make_cred_alias(first)} = {first} "My {make_display_name(sname)}"')
        lines.append("```")
        lines.append("")

    # Operations & Parameters
    props = entry.get("properties", [])
    resources, all_ops, resource_ops = get_all_operations(entry)

    has_ops = bool(resources or all_ops)
    grouped, common = group_props_by_context(props) if has_ops else ({}, [])

    if has_ops and grouped:
        lines.append("## Operations")
        lines.append("")

        if resources:
            for res in resources:
                res_ops = resource_ops.get(res, [])
                if not res_ops:
                    continue
                lines.append(f"### Resource: `{res}`")
                lines.append("")
                for op in res_ops:
                    op_props = grouped.get((res, op), [])
                    lines.append(f"#### `{op}`")
                    lines.append("")
                    if op_props:
                        lines.extend(render_param_table(op_props))
                    else:
                        lines.append("No additional parameters.")
                    lines.append("")
        else:
            ops_list = resource_ops.get(None, all_ops)
            for op in ops_list:
                op_props = grouped.get((None, op), [])
                lines.append(f"### `{op}`")
                lines.append("")
                if op_props:
                    lines.extend(render_param_table(op_props))
                else:
                    lines.append("No additional parameters.")
                lines.append("")

        common_deduped = dedupe_properties(common)
        if common_deduped:
            lines.append("## Common Parameters")
            lines.append("")
            lines.extend(render_param_table(common_deduped))
            lines.append("")

        # Children detail for all complex params (deduped by name)
        all_complex: list[dict] = []
        seen_names: set[str] = set()
        for plist in grouped.values():
            for p in plist:
                if p["name"] not in seen_names and p.get("type") in ("collection", "fixedCollection"):
                    seen_names.add(p["name"])
                    all_complex.append(p)
        for p in common_deduped:
            if p["name"] not in seen_names and p.get("type") in ("collection", "fixedCollection"):
                seen_names.add(p["name"])
                all_complex.append(p)

        children_lines = render_children_detail(all_complex)
        if children_lines:
            lines.append("## Parameter Details")
            lines.extend(children_lines)

    elif has_ops:
        # Has operations but no displayOptions data — flat fallback
        lines.append("## Operations")
        lines.append("")
        if resources:
            lines.append(f"**Resources:** {', '.join(f'`{r}`' for r in resources)}")
        if all_ops:
            lines.append(f"**Operations:** {', '.join(f'`{o}`' for o in all_ops)}")
        lines.append("")

        deduped = dedupe_properties(props)
        if deduped:
            lines.append("## Parameters")
            lines.append("")
            lines.extend(render_param_table(deduped))
            lines.append("")
            children_lines = render_children_detail(deduped)
            if children_lines:
                lines.extend(children_lines)

    else:
        # No operations — simple parameter table
        deduped = dedupe_properties(props)
        if deduped:
            lines.append("## Parameters")
            lines.append("")
            lines.extend(render_param_table(deduped))
            lines.append("")
            children_lines = render_children_detail(deduped)
            if children_lines:
                lines.extend(children_lines)

    # Example
    lines.append("## Example")
    lines.append("")
    lines.append("```nflow")
    lines.append(generate_example(full_name, entry))
    lines.append("```")
    lines.append("")

    return "\n".join(lines)


# ── Catalog generation ────────────────────────────────────────────────────────

def generate_catalog(registry: dict) -> str:
    nodes = registry.get("nodes", {})

    by_group: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    for full_name, entry in sorted(nodes.items()):
        groups = entry.get("group", ["transform"])
        primary_group = groups[0] if groups else "transform"
        by_group[primary_group].append((full_name, entry))

    lines: list[str] = []

    lines.append("# nflow Node Catalog")
    lines.append("")
    lines.append("Compact index of all n8n nodes available via the `NODE` keyword.")
    lines.append("For full parameter details, open the linked node page.")
    lines.append("")
    lines.append("## NODE Syntax")
    lines.append("")
    lines.append("```nflow")
    lines.append('NODE "<type>" @credential AS "Name" { param: "value" }')
    lines.append("```")
    lines.append("")
    lines.append('Short names work: `NODE "postgres"` = `NODE "n8n-nodes-base.postgres"`')
    lines.append("")

    # TOC
    lines.append("## Contents")
    lines.append("")
    total = 0
    all_groups = list(GROUP_ORDER) + [k for k in by_group if k not in GROUP_ORDER]
    for gk in all_groups:
        gn = by_group.get(gk, [])
        if not gn:
            continue
        label = GROUP_LABELS.get(gk, gk.title())
        lines.append(f"- [{label}](#{label.lower().replace(' / ', '-').replace(' ', '-')}) ({len(gn)})")
        total += len(gn)
    lines.append("")
    lines.append(f"**{total} nodes total**")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Per-group tables
    for gk in all_groups:
        gn = by_group.get(gk, [])
        if not gn:
            continue
        label = GROUP_LABELS.get(gk, gk.title())
        lines.append(f"## {label}")
        lines.append("")
        lines.append("| Node | Credential | Operations | Details |")
        lines.append("|------|-----------|------------|---------|")

        for full_name, entry in gn:
            sname = short_name(full_name)
            fname = node_filename(full_name)
            ergonomic = ERGONOMIC_KEYWORDS.get(full_name)

            # Credential column
            creds = entry.get("credentials", [])
            cred_col = ", ".join(f"`@{make_cred_alias(c['name'])}`" for c in creds[:2])
            if not cred_col:
                cred_col = "—"

            # Operations column
            resources, all_ops, _ = get_all_operations(entry)
            ops_parts = []
            if resources:
                ops_parts.append(", ".join(resources[:4]))
                if len(resources) > 4:
                    ops_parts[-1] += "..."
            if all_ops:
                ops = all_ops[:6]
                ops_parts.append(", ".join(ops))
                if len(all_ops) > 6:
                    ops_parts[-1] += f"... ({len(all_ops)})"
            ops_col = " · ".join(ops_parts) if ops_parts else "—"

            # Details column
            ergo_note = f" ⚡ `{ergonomic}`" if ergonomic else ""
            link = f"[`{sname}`](nodes/{fname})"

            lines.append(f"| {link} | {cred_col} | {ops_col} | v{entry.get('version','?')}{ergo_note} |")

        lines.append("")

    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Generate nflow node reference docs")
    parser.add_argument("registry", nargs="?", default="node-registry.json",
                        help="Path to node-registry.json")
    parser.add_argument("--catalog", default="docs/NODE-CATALOG.md",
                        help="Output catalog file")
    parser.add_argument("--nodes-dir", default="docs/nodes",
                        help="Output directory for per-node pages")
    parser.add_argument("--stats", action="store_true")
    args = parser.parse_args()

    registry_path = Path(args.registry)
    if not registry_path.exists():
        print(f"Error: {registry_path} not found", file=sys.stderr)
        sys.exit(1)

    with open(registry_path) as f:
        registry = json.load(f)

    nodes = registry.get("nodes", {})

    # Generate catalog
    catalog = generate_catalog(registry)
    catalog_path = Path(args.catalog)
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    with open(catalog_path, "w") as f:
        f.write(catalog)
    catalog_lines = catalog.count("\n") + 1
    print(f"Catalog: {catalog_path} ({catalog_lines:,} lines, {len(catalog):,} bytes)")

    # Generate per-node pages
    nodes_dir = Path(args.nodes_dir)
    nodes_dir.mkdir(parents=True, exist_ok=True)
    page_count = 0
    total_bytes = 0
    for full_name, entry in sorted(nodes.items()):
        page = generate_node_page(full_name, entry)
        page_path = nodes_dir / node_filename(full_name)
        with open(page_path, "w") as f:
            f.write(page)
        page_count += 1
        total_bytes += len(page)

    print(f"Pages:   {nodes_dir}/ ({page_count} files, {total_bytes:,} bytes total)")

    if args.stats:
        from collections import Counter
        groups = Counter()
        for entry in nodes.values():
            for g in entry.get("group", ["unknown"]):
                groups[g] += 1
        for g, c in groups.most_common():
            print(f"  {GROUP_LABELS.get(g, g)}: {c}")


if __name__ == "__main__":
    main()
