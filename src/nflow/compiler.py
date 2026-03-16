#!/usr/bin/env python3
"""
nflow — A compact language for describing n8n workflows.
Parses .nflow files and outputs valid n8n workflow JSON.

Usage:
    nflow <input.nflow> [-o output.json] [--validate] [--compact]
    nflow --stdin [-o output.json]
"""

import json
import hashlib
import uuid
import re
import sys
from typing import Any

__version__ = "1.0.0"


class NflowError(Exception):
    """Error raised during nflow parsing/compilation."""
    def __init__(self, message, line_num=None):
        self.line_num = line_num
        if line_num is not None:
            super().__init__(f"line {line_num}: {message}")
        else:
            super().__init__(message)


def generate_id():
    return str(uuid.uuid4())


def generate_credential_id(name: str) -> str:
    """Deterministic 16-char alphanumeric ID from credential name, matching n8n's format."""
    alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
    digest = hashlib.sha256(name.encode('utf-8')).digest()
    return ''.join(alphabet[b % len(alphabet)] for b in digest[:16])


def generate_condition_id():
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Tokenizer – splits DSL source into logical lines (handling continuations,
# multi-line code blocks, and multi-line `{ ... }` blocks).
# ---------------------------------------------------------------------------

def tokenize_with_lines(source: str) -> list[tuple[int, str]]:
    """Split source into (1-based line number, logical line) tuples."""
    raw_lines = source.split('\n')
    logical: list[tuple[int, str]] = []
    i = 0
    while i < len(raw_lines):
        line = raw_lines[i]
        stripped = line.strip()
        start = i + 1

        if not stripped or stripped.startswith('//'):
            i += 1
            continue

        if '```' in stripped:
            block = stripped
            if block.count('```') < 2:
                i += 1
                while i < len(raw_lines):
                    block += '\n' + raw_lines[i]
                    if '```' in raw_lines[i] and block.count('```') >= 2:
                        i += 1
                        break
                    i += 1
            else:
                i += 1
            logical.append((start, block))
            continue

        if '{' in stripped and stripped.count('{') > stripped.count('}'):
            block = stripped
            depth = block.count('{') - block.count('}')
            i += 1
            while i < len(raw_lines) and depth > 0:
                next_line = raw_lines[i].strip()
                if not next_line or next_line.startswith('//'):
                    i += 1
                    continue
                block += ' ' + next_line
                depth += next_line.count('{') - next_line.count('}')
                i += 1
            logical.append((start, block))
            continue

        while stripped.endswith('\\'):
            i += 1
            if i < len(raw_lines):
                stripped = stripped[:-1] + ' ' + raw_lines[i].strip()

        logical.append((start, stripped))
        i += 1

    return logical


def tokenize_lines(source: str) -> list[str]:
    """Split source into logical lines, joining continuations and blocks."""
    return [line for _, line in tokenize_with_lines(source)]


# ---------------------------------------------------------------------------
# Expression helpers
# ---------------------------------------------------------------------------

# Pattern for matching a quoted string that may contain escaped quotes
QUOTED_NAME = r'"((?:[^"\\]|\\.)*)"'


def unquote_name(s: str) -> str:
    """Unescape a name extracted via QUOTED_NAME pattern."""
    return s.replace('\\"', '"').replace("\\'", "'")


def wrap_expr(val: str) -> str:
    """Convert {{ expr }} to ={{ expr }} for n8n."""
    if isinstance(val, str) and '{{' in val:
        val = val.strip()
        # Prefix with = for n8n expression syntax
        if not val.startswith('='):
            return '=' + val
    return val


def parse_value(s: str) -> Any:
    """Parse a value token: number, boolean, string, expression, or list."""
    s = s.strip()
    if not s:
        return ""

    # Boolean
    if s == 'true':
        return True
    if s == 'false':
        return False

    # Number
    try:
        if '.' in s:
            return float(s)
        return int(s)
    except ValueError:
        pass

    # Expression {{ ... }} or ={{ ... }}
    if s.startswith('{{'):
        return wrap_expr(s)
    if s.startswith('={{'):
        return s

    # Code block with triple backticks
    if s.startswith('```') and s.endswith('```'):
        return s[3:-3].strip()

    # Code block with single backticks
    if s.startswith('`') and s.endswith('`') and not s.startswith('```'):
        return s[1:-1].strip()

    # Quoted string
    if s.startswith('"') and s.endswith('"'):
        return s[1:-1].replace('\\"', '"')
    if s.startswith("'") and s.endswith("'"):
        return s[1:-1].replace("\\'", "'")

    # Array [...] 
    if s.startswith('[') and s.endswith(']'):
        inner = s[1:-1].strip()
        if not inner:
            return []
        items = smart_split(inner, ',')
        return [parse_value(x.strip()) for x in items]

    # Nested dict { ... }
    if s.startswith('{') and s.endswith('}'):
        return parse_kv_block(s)

    return s


def smart_split(s: str, delimiter: str = ',') -> list[str]:
    """Split by delimiter, respecting braces, brackets, quotes, backticks, and {{ }}."""
    parts = []
    current = []
    depth_brace = 0
    depth_bracket = 0
    depth_expr = 0
    in_quote = None
    in_backtick = False  # inside ``` ... ``` block
    i = 0
    while i < len(s):
        ch = s[i]

        # Handle backtick code blocks
        if not in_quote and s[i:i+3] == '```':
            in_backtick = not in_backtick
            current.append('`')
            current.append('`')
            current.append('`')
            i += 3
            continue

        if in_backtick:
            current.append(ch)
            i += 1
            continue

        if in_quote:
            current.append(ch)
            if ch == in_quote and (i == 0 or s[i-1] != '\\'):
                in_quote = None
            i += 1
            continue

        if ch in ('"', "'"):
            in_quote = ch
            current.append(ch)
            i += 1
            continue

        # Single backtick (not triple)
        if ch == '`' and s[i:i+3] != '```':
            # Find matching backtick
            current.append(ch)
            i += 1
            while i < len(s) and s[i] != '`':
                current.append(s[i])
                i += 1
            if i < len(s):
                current.append(s[i])
                i += 1
            continue

        if s[i:i+2] == '{{':
            depth_expr += 1
            current.append('{')
            current.append('{')
            i += 2
            continue
        if s[i:i+2] == '}}':
            depth_expr -= 1
            current.append('}')
            current.append('}')
            i += 2
            continue

        if ch == '{':
            depth_brace += 1
        elif ch == '}':
            depth_brace -= 1
        elif ch == '[':
            depth_bracket += 1
        elif ch == ']':
            depth_bracket -= 1

        if ch == delimiter and depth_brace == 0 and depth_bracket == 0 and depth_expr == 0:
            parts.append(''.join(current))
            current = []
        else:
            current.append(ch)
        i += 1

    if current:
        parts.append(''.join(current))
    return parts


def parse_kv_block(s: str) -> dict:
    """Parse a `{ key: value, key2: value2 }` block into a dict."""
    s = s.strip()
    if s.startswith('{'):
        s = s[1:]
    if s.endswith('}'):
        s = s[:-1]
    s = s.strip()
    if not s:
        return {}

    result = {}
    pairs = smart_split(s, ',')
    for pair in pairs:
        pair = pair.strip()
        if not pair:
            continue
        # Split on first ':'
        colon_idx = find_unquoted(pair, ':')
        if colon_idx == -1:
            continue
        key = pair[:colon_idx].strip().strip('"').strip("'")
        val = pair[colon_idx + 1:].strip()
        result[key] = parse_value(val)
    return result


def find_unquoted(s: str, ch: str) -> int:
    """Find first occurrence of ch outside quotes and expressions."""
    in_quote = None
    depth = 0
    for i, c in enumerate(s):
        if in_quote:
            if c == in_quote and (i == 0 or s[i-1] != '\\'):
                in_quote = None
            continue
        if c in ('"', "'"):
            in_quote = c
            continue
        if s[i:i+2] == '{{':
            depth += 1
            continue
        if s[i:i+2] == '}}':
            depth -= 1
            continue
        if c == ch and depth == 0:
            return i
    return -1


# ---------------------------------------------------------------------------
# Parse individual block content from a line for different node params
# ---------------------------------------------------------------------------

def extract_block(line: str) -> tuple[str, str]:
    """Split a line into the part before/after `{...}` and the block content.

    Returns (prefix + suffix, block) where suffix is any text after the
    matching closing brace (e.g. flags like +passthrough).
    """
    idx = find_unquoted(line, '{')
    if idx == -1:
        return line, ''
    prefix = line[:idx].strip()

    # Find the matching closing brace
    depth = 0
    in_quote = None
    end = len(line)
    i = idx
    while i < len(line):
        c = line[i]
        if in_quote:
            if c == in_quote and (i == 0 or line[i - 1] != '\\'):
                in_quote = None
            i += 1
            continue
        if c in ('"', "'"):
            in_quote = c
            i += 1
            continue
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                end = i + 1
                break
        i += 1

    block = line[idx:end].strip()
    suffix = line[end:].strip()
    if suffix:
        prefix = prefix + ' ' + suffix
    return prefix, block


def extract_as_name(prefix: str) -> tuple[str, str]:
    """Extract AS 'Name' from prefix, return (remaining, name).
    Handles escaped quotes like AS "Set \"processing\" status"
    """
    # Match AS " ... " allowing escaped quotes inside
    m = re.search(r'\bAS\s+"((?:[^"\\]|\\.)*)"', prefix)
    if m:
        name = m.group(1).replace('\\"', '"')
        remaining = prefix[:m.start()].strip() + ' ' + prefix[m.end():].strip()
        return remaining.strip(), name
    # Try single quotes
    m = re.search(r"\bAS\s+'((?:[^'\\]|\\.)*)'", prefix)
    if m:
        name = m.group(1).replace("\\'", "'")
        remaining = prefix[:m.start()].strip() + ' ' + prefix[m.end():].strip()
        return remaining.strip(), name
    return prefix, ''


def extract_flags(prefix: str) -> tuple[str, dict]:
    """Extract known flags like +passthrough, +once, +always, +retry,
    onError:X, retry:N, wait:N, notes:"...", disabled."""
    flags = {}
    if '+passthrough' in prefix:
        flags['passthrough'] = True
        prefix = prefix.replace('+passthrough', '').strip()
    for flag_name, flag_key in (('+once', 'executeOnce'), ('+always', 'alwaysOutputData'),
                                 ('+retry', 'retryOnFail')):
        if flag_name in prefix:
            flags[flag_key] = True
            prefix = prefix.replace(flag_name, '', 1).strip()
    if 'disabled' in prefix.split():
        flags['disabled'] = True
        prefix = re.sub(r'\bdisabled\b', '', prefix).strip()
    m = re.search(r'onError:(\w+)', prefix)
    if m:
        flags['onError'] = m.group(1)
        prefix = prefix[:m.start()].strip() + ' ' + prefix[m.end():].strip()
        prefix = prefix.strip()
    m = re.search(r'retry:(\d+)', prefix)
    if m:
        flags['retryOnFail'] = True
        flags['maxTries'] = int(m.group(1))
        prefix = prefix[:m.start()].strip() + ' ' + prefix[m.end():].strip()
        prefix = prefix.strip()
    m = re.search(r'wait:(\d+)', prefix)
    if m:
        flags['waitBetweenTries'] = int(m.group(1))
        prefix = prefix[:m.start()].strip() + ' ' + prefix[m.end():].strip()
        prefix = prefix.strip()
    m = re.search(r'notes:"([^"]*)"', prefix)
    if not m:
        m = re.search(r"notes:'([^']*)'", prefix)
    if m:
        flags['notes'] = m.group(1)
        prefix = prefix[:m.start()].strip() + ' ' + prefix[m.end():].strip()
        prefix = prefix.strip()
    return prefix, flags


# ---------------------------------------------------------------------------
# Condition parser
# ---------------------------------------------------------------------------

OPERATORS = {
    'equals': ('string', 'equals'),
    'notEquals': ('string', 'notEquals'),
    'contains': ('string', 'contains'),
    'notContains': ('string', 'notContains'),
    'startsWith': ('string', 'startsWith'),
    'endsWith': ('string', 'endsWith'),
    'empty': ('string', 'empty'),
    'notEmpty': ('string', 'notEmpty'),
    'exists': ('string', 'exists'),
    'notExists': ('string', 'notExists'),
    'gt': ('number', 'gt'),
    'gte': ('number', 'gte'),
    'lt': ('number', 'lt'),
    'lte': ('number', 'lte'),
    'numEquals': ('number', 'equals'),
    'isTrue': ('boolean', 'true'),
    'isFalse': ('boolean', 'false'),
    'regex': ('string', 'regex'),
    'arrayEmpty': ('array', 'empty'),
    'arrayNotEmpty': ('array', 'notEmpty'),
}


def parse_condition_line(cond_str: str) -> dict:
    """Parse a single condition like: {{ $json.field }} contains 'value'"""
    cond_str = cond_str.strip().rstrip(',')

    # Try to match: <left> <operator> <right>
    # or: <left> <operator> (unary)
    for op_name in sorted(OPERATORS.keys(), key=len, reverse=True):
        # Look for the operator as a word boundary
        pattern = re.compile(r'(.+?)\s+' + re.escape(op_name) + r'(?:\s+(.+))?$')
        m = pattern.match(cond_str)
        if m:
            left = m.group(1).strip()
            right = m.group(2)
            op_type, op_operation = OPERATORS[op_name]

            cond = {
                'id': generate_condition_id(),
                'leftValue': wrap_expr(left),
                'rightValue': parse_value(right) if right else '',
                'operator': {
                    'type': op_type,
                    'operation': op_operation,
                }
            }
            # Unary operators
            if op_name in ('empty', 'notEmpty', 'exists', 'notExists',
                           'isTrue', 'isFalse', 'arrayEmpty', 'arrayNotEmpty'):
                cond['operator']['singleValue'] = True
                cond['rightValue'] = ''

            return cond

    # Fallback: treat entire thing as expression notEmpty
    return {
        'id': generate_condition_id(),
        'leftValue': wrap_expr(cond_str),
        'rightValue': '',
        'operator': {'type': 'string', 'operation': 'notEmpty', 'singleValue': True}
    }


def parse_conditions_block(block: dict) -> dict:
    """Parse { conditions: AND/OR [...] } into n8n condition structure."""
    conditions_raw = block.get('conditions', '')
    if isinstance(conditions_raw, str):
        # Parse "AND [...]" or "OR [...]"
        conditions_raw = conditions_raw.strip()
        combinator = 'and'
        if conditions_raw.upper().startswith('OR'):
            combinator = 'or'
            conditions_raw = conditions_raw[2:].strip()
        elif conditions_raw.upper().startswith('AND'):
            combinator = 'and'
            conditions_raw = conditions_raw[3:].strip()

        # Parse the array of conditions
        if conditions_raw.startswith('['):
            conditions_raw = conditions_raw[1:]
        if conditions_raw.endswith(']'):
            conditions_raw = conditions_raw[:-1]

        cond_strings = smart_split(conditions_raw, ',')
        parsed_conditions = [parse_condition_line(c) for c in cond_strings if c.strip()]

        return {
            'conditions': {
                'options': {
                    'caseSensitive': False,
                    'leftValue': '',
                    'typeValidation': 'strict',
                    'version': 3
                },
                'conditions': parsed_conditions,
                'combinator': combinator
            },
            'options': {}
        }

    return {'conditions': conditions_raw, 'options': {}}


# ---------------------------------------------------------------------------
# Node registry — loads extracted n8n node definitions for type-aware
# parameter serialization and version resolution.
# ---------------------------------------------------------------------------

_REGISTRY_FILENAMES = ('node-registry.json',)


def _find_registry_path() -> str | None:
    """Search for node-registry.json in common locations.

    Lookup order:
    1. Bundled package data via importlib.resources (works for all install modes)
    2. Current working directory (allows local overrides)
    """
    import os
    from importlib import resources
    for name in _REGISTRY_FILENAMES:
        try:
            ref = resources.files('nflow').joinpath(name)
            pkg_path = str(ref)
            if os.path.isfile(pkg_path):
                return pkg_path
        except Exception:
            pass
        cwd_path = os.path.join(os.getcwd(), name)
        if os.path.isfile(cwd_path):
            return os.path.abspath(cwd_path)
    return None


class NodeRegistry:
    """Provides n8n node metadata: versions, properties, credentials.

    Loaded lazily from node-registry.json (generated by extract-node-registry.py).
    Falls back gracefully when the registry file is unavailable.
    """

    def __init__(self):
        self._nodes: dict[str, dict] = {}
        self._loaded = False

    def load(self, path: str | None = None):
        """Load registry from file. No-op if already loaded or file missing."""
        if self._loaded:
            return
        self._loaded = True
        if path is None:
            path = _find_registry_path()
        if path is None:
            return
        try:
            with open(path) as f:
                data = json.load(f)
            self._nodes = data.get('nodes', {})
        except (OSError, json.JSONDecodeError):
            pass

    def get_node(self, node_type: str) -> dict | None:
        """Get registry entry for a node type (e.g. 'n8n-nodes-base.set')."""
        self.load()
        return self._nodes.get(node_type)

    def get_version(self, node_type: str) -> float | None:
        """Get the latest version number for a node type."""
        entry = self.get_node(node_type)
        if entry:
            return entry.get('version')
        return None

    def get_property(self, node_type: str, param_name: str) -> dict | None:
        """Look up a specific property definition by name."""
        entry = self.get_node(node_type)
        if not entry:
            return None
        for prop in entry.get('properties', []):
            if prop.get('name') == param_name:
                return prop
        return None

    @property
    def available(self) -> bool:
        self.load()
        return bool(self._nodes)


# Singleton registry instance
_registry = NodeRegistry()


def get_registry() -> NodeRegistry:
    return _registry


# ---------------------------------------------------------------------------
# Parameter type serialization — converts DSL values into n8n JSON based
# on the property type from the registry.
# ---------------------------------------------------------------------------

# Types that are purely for UI display and produce no JSON output.
_UI_ONLY_PARAM_TYPES = frozenset({
    'notice', 'callout', 'button', 'icon', 'curlImport',
})


def serialize_param(value: Any, prop: dict | None) -> Any:
    """Serialize a parsed DSL value according to the n8n property type.

    Args:
        value: The value parsed from the DSL (via parse_value / parse_kv_block).
        prop: The property definition from the registry (or None for pass-through).

    Returns:
        The value transformed into the JSON format n8n expects.
    """
    if prop is None:
        return _serialize_passthrough(value)

    ptype = prop.get('type', 'string')

    if ptype in _UI_ONLY_PARAM_TYPES:
        return None

    if ptype == 'resourceLocator':
        return _serialize_resource_locator(value, prop)
    if ptype == 'fixedCollection':
        return _serialize_fixed_collection(value, prop)
    if ptype == 'collection':
        return _serialize_collection(value, prop)
    if ptype == 'assignmentCollection':
        return _serialize_assignment_collection(value)
    if ptype == 'filter':
        return value
    if ptype in ('string', 'json', 'dateTime', 'color'):
        return wrap_expr(str(value)) if isinstance(value, str) else value
    if ptype == 'number':
        if isinstance(value, (int, float)):
            return value
        try:
            return float(value) if '.' in str(value) else int(value)
        except (ValueError, TypeError):
            return wrap_expr(str(value)) if isinstance(value, str) else value
    if ptype == 'boolean':
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            if value.lower() in ('true', '1', 'yes'):
                return True
            if value.lower() in ('false', '0', 'no'):
                return False
            return wrap_expr(value)
        return bool(value)
    if ptype in ('options', 'multiOptions', 'hidden', 'credentials',
                 'credentialsSelect', 'workflowSelector'):
        return value
    if ptype == 'resourceMapper':
        return value

    return _serialize_passthrough(value)


def _serialize_passthrough(value: Any) -> Any:
    """Default: wrap expressions, pass everything else through."""
    if isinstance(value, str):
        return wrap_expr(value)
    return value


def _serialize_resource_locator(value: Any, prop: dict) -> dict:
    """Convert a DSL value into n8n's resourceLocator format.

    DSL users can write a plain string (URL, ID) or a dict with mode+value.
    """
    if isinstance(value, dict) and '__rl' in value:
        return value

    modes = prop.get('modes', ['id'])

    if isinstance(value, dict) and 'mode' in value and 'value' in value:
        return {'__rl': True, 'mode': value['mode'], 'value': wrap_expr(str(value['value']))}

    str_val = wrap_expr(str(value)) if isinstance(value, str) else str(value)

    # Heuristic: if it looks like a URL, use url mode; otherwise use id
    mode = 'id'
    if 'url' in modes and isinstance(value, str) and ('http://' in value or 'https://' in value):
        mode = 'url'
    elif 'list' in modes:
        mode = 'list'

    return {'__rl': True, 'mode': mode, 'value': str_val}


def _serialize_fixed_collection(value: Any, prop: dict) -> Any:
    """Serialize a value for fixedCollection type.

    fixedCollections have named groups, each containing an array of items
    (if multipleValues) or a single item.
    """
    if not isinstance(value, dict):
        return value
    return value


def _serialize_collection(value: Any, prop: dict) -> Any:
    """Serialize a value for collection type (key-value options)."""
    if not isinstance(value, dict):
        return value
    result = {}
    children = {c['name']: c for c in prop.get('children', []) if 'name' in c}
    for k, v in value.items():
        child_prop = children.get(k)
        result[k] = serialize_param(v, child_prop)
    return result


def _serialize_assignment_collection(value: Any) -> Any:
    """Serialize SET-style assignments."""
    if isinstance(value, dict) and 'assignments' in value:
        return value
    if isinstance(value, dict):
        assignments = []
        for k, v in value.items():
            a = {
                'id': generate_id(),
                'name': k,
                'value': v if not isinstance(v, str) or not v.startswith('=') else v,
                'type': 'string',
            }
            if isinstance(v, bool):
                a['type'] = 'boolean'
            elif isinstance(v, (int, float)):
                a['type'] = 'number'
            assignments.append(a)
        return {'assignments': assignments}
    return value


def serialize_node_params(params: dict, node_type: str) -> dict:
    """Serialize all parameters for a node using registry type information.

    Falls back to pass-through for unknown nodes/properties.
    """
    registry = get_registry()
    entry = registry.get_node(node_type)
    if not entry:
        return params

    prop_map = {p['name']: p for p in entry.get('properties', [])}
    result = {}
    for k, v in params.items():
        prop = prop_map.get(k)
        serialized = serialize_param(v, prop)
        if serialized is not None:
            result[k] = serialized

    _promote_datamode_to_columns(result, prop_map)
    return result

def _promote_datamode_to_columns(result: dict, prop_map: dict) -> None:
    """Convert legacy dataMode/fieldsUi params into the resourceMapper columns format.

    n8n v4.5+ nodes (e.g. googleSheets) use a ``columns`` resourceMapper
    parameter instead of the older ``dataMode`` + ``fieldsUi`` pair.  When the
    DSL author writes the legacy names via the NODE keyword, this helper folds
    them into the ``columns`` structure that n8n actually expects.
    """
    if 'dataMode' not in result:
        return
    columns_prop = prop_map.get('columns')
    if not columns_prop or columns_prop.get('type') != 'resourceMapper':
        return
    if 'columns' in result:
        return

    data_mode = result.pop('dataMode')
    fields_ui = result.pop('fieldsUi', None)

    columns: dict[str, Any] = {
        'mappingMode': data_mode,
        'value': {},
        'matchingColumns': [],
        'schema': [],
    }

    if data_mode == 'defineBelow' and fields_ui:
        field_values = (
            fields_ui.get('fieldValues')
            or fields_ui.get('values')
            or []
        )
        for entry in field_values:
            fid = entry.get('fieldId') or entry.get('column') or entry.get('columnName', '')
            fval = entry.get('fieldValue', '')
            if isinstance(fval, str):
                fval = wrap_expr(fval)
            columns['value'][fid] = fval

        columns['schema'] = [
            {
                'id': col_name, 'displayName': col_name,
                'required': False, 'defaultMatch': False,
                'display': True, 'type': 'string', 'canBeUsedToMatch': True,
            }
            for col_name in columns['value']
        ]

    result['columns'] = columns


# ---------------------------------------------------------------------------
# Node builders
# ---------------------------------------------------------------------------

class Node:
    """Represents a parsed n8n node."""
    def __init__(self, name: str, node_type: str, parameters: dict,
                 credentials: dict = None, flags: dict = None,
                 type_version: float = None):
        self.id = generate_id()
        self.name = name
        self.type = node_type
        self.parameters = parameters
        self.credentials = credentials or {}
        self.flags = flags or {}
        self.type_version = type_version
        self.position = [0, 0]

    def to_dict(self) -> dict:
        d = {
            'parameters': self.parameters,
            'type': self.type,
            'typeVersion': self.type_version or self._default_version(),
            'position': self.position,
            'id': self.id,
            'name': self.name,
        }
        if self.credentials:
            d['credentials'] = self.credentials
        if self.flags.get('onError'):
            onerr = self.flags['onError']
            onerr_map = {'continue': 'continueErrorOutput',
                         'output': 'continueRegularOutput',
                         'stop': 'stopWorkflow'}
            d['onError'] = onerr_map.get(onerr, onerr)
        if self.flags.get('executeOnce'):
            d['executeOnce'] = True
        if self.flags.get('alwaysOutputData'):
            d['alwaysOutputData'] = True
        if self.flags.get('retryOnFail'):
            d['retryOnFail'] = True
            if 'maxTries' in self.flags:
                d['maxTries'] = self.flags['maxTries']
            if 'waitBetweenTries' in self.flags:
                d['waitBetweenTries'] = self.flags['waitBetweenTries']
        if 'notes' in self.flags:
            d['notes'] = self.flags['notes']
            d['notesInFlow'] = True
        if self.flags.get('disabled'):
            d['disabled'] = True
        return d

    def _default_version(self):
        # Hardcoded overrides for nodes with custom DSL keywords.
        # These take precedence over the registry to ensure backwards
        # compatibility when no registry is present.
        _HARDCODED = {
            'n8n-nodes-base.httpRequest': 4.4,
            'n8n-nodes-base.code': 2,
            'n8n-nodes-base.set': 3.4,
            'n8n-nodes-base.if': 2.3,
            'n8n-nodes-base.filter': 2.3,
            'n8n-nodes-base.merge': 3.2,
            'n8n-nodes-base.switch': 3.4,
            'n8n-nodes-base.dateTime': 2,
            'n8n-nodes-base.limit': 1,
            'n8n-nodes-base.splitInBatches': 3,
            'n8n-nodes-base.formTrigger': 2.5,
            'n8n-nodes-base.googleSheets': 4.7,
            'n8n-nodes-base.googleSheetsTrigger': 1,
            'n8n-nodes-base.googleDrive': 3,
            'n8n-nodes-base.noOp': 1,
            'n8n-nodes-base.stickyNote': 1,
            'n8n-nodes-base.manualTrigger': 1,
            'n8n-nodes-base.webhook': 2.1,
            'n8n-nodes-base.scheduleTrigger': 1.3,
            '@n8n/n8n-nodes-langchain.agent': 3.1,
            '@n8n/n8n-nodes-langchain.chatTrigger': 1.4,
            '@n8n/n8n-nodes-langchain.lmChatGoogleGemini': 1,
            '@n8n/n8n-nodes-langchain.lmChatOpenAi': 1.3,
            '@n8n/n8n-nodes-langchain.lmChatAnthropic': 1.3,
            '@n8n/n8n-nodes-langchain.memoryBufferWindow': 1.3,
            '@n8n/n8n-nodes-langchain.toolWikipedia': 1,
            '@n8n/n8n-nodes-langchain.toolCode': 1.3,
            'n8n-nodes-base.httpRequestTool': 4.2,
            'n8n-nodes-base.dateTimeTool': 2,
            'n8n-nodes-base.cryptoTool': 1,
            'n8n-nodes-base.rssFeedReadTool': 1.2,
        }
        if self.type in _HARDCODED:
            return _HARDCODED[self.type]
        # Fall back to registry
        reg_version = get_registry().get_version(self.type)
        if reg_version is not None:
            return reg_version
        return 1


class Connection:
    """Represents a connection between nodes."""
    def __init__(self, source: str, target: str,
                 source_output: int = 0, target_input: int = 0,
                 connection_type: str = 'main', line_num: int = None):
        self.source = source
        self.target = target
        self.source_output = source_output
        self.target_input = target_input
        self.connection_type = connection_type
        self.line_num = line_num


# ---------------------------------------------------------------------------
# Main Parser
# ---------------------------------------------------------------------------

KNOWN_KEYWORDS = frozenset({
    'WORKFLOW', 'CREDENTIAL', 'TRIGGER', 'SET', 'HTTP', 'CODE',
    'FILTER', 'IF', 'MERGE', 'SWITCH', 'GSHEET', 'GDRIVE', 'AGENT', 'LLM',
    'MEMORY', 'TOOL', 'NOOP', 'NOTE', 'POSITION', 'DATETIME', 'LIMIT', 'LOOP',
    'NODE',
})


class N8nFDLParser:
    def __init__(self):
        self.workflow_name = "My Workflow"
        self.active = False
        self.credentials: dict[str, dict] = {}  # alias -> {type, name, id}
        self.nodes: list[Node] = []
        self.connections: list[Connection] = []
        self.positions: dict[str, list[int]] = {}
        self.node_names: set[str] = set()
        self._current_line: int | None = None
        self._external_creds: dict[str, dict] = {}  # name -> {id, type}
        self._linked_cred_names: set[str] = set()

    def load_credentials(self, path: str):
        """Load an existing n8n credentials JSON file for ID reuse by name."""
        with open(path) as f:
            data = json.load(f)
        for entry in data:
            name = entry.get('name', '')
            if name:
                self._external_creds[name] = {
                    'id': entry['id'],
                    'type': entry.get('type', ''),
                }

    def _unique_name(self, name: str) -> str:
        """Ensure node names are unique."""
        if name not in self.node_names:
            self.node_names.add(name)
            return name
        i = 1
        while f"{name} {i}" in self.node_names:
            i += 1
        unique = f"{name} {i}"
        self.node_names.add(unique)
        return unique

    def _resolve_credential(self, alias: str) -> dict:
        """Resolve @alias to credential config."""
        alias = alias.lstrip('@')
        if alias in self.credentials:
            cred = self.credentials[alias]
            cred_type = cred['type']
            return {
                cred_type: {
                    'id': cred.get('id', generate_id()),
                    'name': cred['name']
                }
            }
        # Fallback: create a placeholder
        return {
            'httpHeaderAuth': {
                'id': generate_id(),
                'name': alias
            }
        }

    def _build_http_auth(self, cred_alias: str) -> tuple[dict, dict]:
        """Build authentication params and credentials for HTTP node."""
        if not cred_alias:
            return {}, {}
        cred_alias = cred_alias.lstrip('@')
        cred = self.credentials.get(cred_alias, {})
        cred_type = cred.get('type', 'httpHeaderAuth')

        auth_params = {
            'authentication': 'genericCredentialType',
            'genericAuthType': cred_type,
        }
        credentials = self._resolve_credential(cred_alias)
        return auth_params, credentials

    @staticmethod
    def _apply_options(block: dict, params: dict):
        """Merge user-provided options: { ... } from the DSL block into params['options']."""
        user_opts = block.get('options')
        if isinstance(user_opts, dict) and user_opts:
            if 'options' not in params:
                params['options'] = {}
            params['options'].update(user_opts)

    # --- Parse individual statement types ---

    def parse_workflow(self, line: str):
        m = re.match(r'WORKFLOW\s+' + QUOTED_NAME + r'(.*)$', line)
        if m:
            self.workflow_name = unquote_name(m.group(1))
            rest = m.group(2).strip()
            if 'active' in rest:
                self.active = True

    def parse_credential(self, line: str):
        # CREDENTIAL @alias = type "Name"
        m = re.match(r'CREDENTIAL\s+@(\w+)\s*=\s*(\w+)\s+' + QUOTED_NAME, line)
        if m:
            alias, cred_type, cred_name = m.group(1), m.group(2), unquote_name(m.group(3))
            ext = self._external_creds.get(cred_name)
            if ext:
                cred_id = ext['id']
                self._linked_cred_names.add(cred_name)
            else:
                cred_id = generate_credential_id(cred_name)
            self.credentials[alias] = {
                'type': cred_type,
                'name': cred_name,
                'id': cred_id
            }

    def parse_trigger(self, line: str):
        prefix, block_str = extract_block(line)
        prefix, name = extract_as_name(prefix)
        prefix, flags = extract_flags(prefix)
        block = parse_kv_block(block_str) if block_str else {}

        # Extract credential alias from prefix (e.g. TRIGGER form @cred ...)
        trig_cred_alias = ''
        cred_match = re.search(r'@(\w+)', prefix)
        if cred_match:
            trig_cred_alias = '@' + cred_match.group(1)
            prefix = prefix[:cred_match.start()] + prefix[cred_match.end():]
            prefix = prefix.strip()

        # Determine trigger type
        trigger_type = prefix.replace('TRIGGER', '').strip().lower()

        if trigger_type == 'manual':
            name = name or 'When clicking \'Execute workflow\''
            node = Node(name, 'n8n-nodes-base.manualTrigger', {})

        elif trigger_type in ('gsheets_update', 'gsheets_row_update', 'googlesheets'):
            name = name or 'Google Sheets Trigger'
            params = {
                'pollTimes': {'item': [{'mode': block.get('poll', 'everyMinute')}]},
                'event': block.get('event', 'rowUpdate'),
                'options': {}
            }
            if block.get('doc'):
                params['documentId'] = {'__rl': True, 'value': wrap_expr(str(block['doc'])), 'mode': 'url'}
            if block.get('sheet'):
                params['sheetName'] = {'__rl': True, 'value': wrap_expr(str(block['sheet'])), 'mode': 'name'}
            if block.get('watch'):
                params['options']['columnsToWatch'] = block['watch'] if isinstance(block['watch'], list) else [block['watch']]
                params['includeInOutput'] = 'both'
            self._apply_options(block, params)

            creds = {}
            cred_alias = block.get('credential', '')
            if cred_alias:
                creds = self._resolve_credential(cred_alias)
            else:
                creds = {'googleSheetsTriggerOAuth2Api': {'id': generate_id(), 'name': 'Google Sheets Trigger account'}}

            node = Node(name, 'n8n-nodes-base.googleSheetsTrigger', params, creds)

        elif trigger_type in ('webhook', 'hook'):
            name = name or 'Webhook'
            params = {
                'httpMethod': block.pop('method', block.pop('httpMethod', 'GET')),
                'path': block.pop('path', generate_id()),
                'options': {}
            }
            # Pass through remaining block params (responseMode, authentication, etc.)
            for k, v in block.items():
                if k != 'options' and k not in params:
                    params[k] = v
            self._apply_options(block, params)
            credentials = {}
            if trig_cred_alias:
                credentials = self._resolve_credential(trig_cred_alias)
            node = Node(name, 'n8n-nodes-base.webhook', params,
                        credentials=credentials, flags=flags)

        elif trigger_type in ('cron', 'schedule'):
            name = name or 'Schedule Trigger'
            params = {'rule': {'interval': [{'field': 'cronExpression', 'expression': block.get('expression', '0 * * * *')}]}}
            node = Node(name, 'n8n-nodes-base.scheduleTrigger', params)

        elif trigger_type == 'chat':
            name = name or 'Chat Trigger'
            params = {
                'options': {}
            }
            if block.get('public'):
                params['public'] = block['public']
            if block.get('initialMessages'):
                params['initialMessages'] = block['initialMessages']
            opts = params['options']
            if block.get('title'):
                opts['title'] = block['title']
            if block.get('subtitle'):
                opts['subtitle'] = block['subtitle']
            if block.get('responseMode'):
                opts['responseMode'] = block['responseMode']
            if block.get('inputPlaceholder'):
                opts['inputPlaceholder'] = block['inputPlaceholder']
            if block.get('showWelcomeScreen') is not None:
                opts['showWelcomeScreen'] = block['showWelcomeScreen']
            if block.get('customCss'):
                opts['customCss'] = block['customCss']
            self._apply_options(block, params)

            node = Node(name, '@n8n/n8n-nodes-langchain.chatTrigger', params)

        elif trigger_type == 'form':
            name = name or 'On form submission'
            params = {'options': {}}
            # Pass through all block params (formTitle, formDescription, formFields, authentication, etc.)
            for k, v in block.items():
                if k != 'options':
                    params[k] = v
            self._apply_options(block, params)

            credentials = {}
            if trig_cred_alias:
                credentials = self._resolve_credential(trig_cred_alias)
            node = Node(name, 'n8n-nodes-base.formTrigger', params,
                        credentials=credentials, flags=flags)

        else:
            name = name or f'Trigger ({trigger_type})'
            node = Node(name, f'n8n-nodes-base.{trigger_type}', block)

        self.nodes.append(node)

    def parse_set(self, line: str):
        prefix, block_str = extract_block(line)
        prefix, name = extract_as_name(prefix)
        prefix, flags = extract_flags(prefix)

        if not name:
            # Try to get name from prefix: SET "Name" { ... }
            m = re.match(r'SET\s+' + QUOTED_NAME, prefix)
            if m:
                name = unquote_name(m.group(1))
            else:
                name = 'Set'

        block = parse_kv_block(block_str) if block_str else {}

        assignments = []
        for key, val in block.items():
            if key == 'options':
                continue
            a = {
                'id': generate_id(),
                'name': key,
                'value': val if not isinstance(val, str) or not val.startswith('=') else val,
                'type': 'string'
            }
            if isinstance(val, bool):
                a['type'] = 'boolean'
            elif isinstance(val, (int, float)):
                a['type'] = 'number'
            assignments.append(a)

        params = {
            'assignments': {'assignments': assignments},
            'options': {}
        }
        if flags.get('passthrough'):
            params['includeOtherFields'] = True
        self._apply_options(block, params)

        node = Node(self._unique_name(name), 'n8n-nodes-base.set', params, flags=flags)
        self.nodes.append(node)

    def parse_http(self, line: str):
        prefix, block_str = extract_block(line)
        prefix, name = extract_as_name(prefix)
        prefix, flags = extract_flags(prefix)

        # Parse: HTTP METHOD url @cred
        # We need to handle URLs with {{ expr }} which contain spaces
        # Strategy: extract method first, then find @cred, rest is URL
        tokens = prefix.split()
        # tokens[0] = 'HTTP'
        method = tokens[1].upper() if len(tokens) > 1 else 'GET'

        # Rebuild the rest after HTTP METHOD
        rest = prefix[len(tokens[0]):].strip()
        rest = rest[len(method):].strip()

        # Extract @credential
        cred_alias = ''
        cred_match = re.search(r'\s@(\w+)(?:\s|$)', rest)
        if cred_match:
            cred_alias = '@' + cred_match.group(1)
            rest = rest[:cred_match.start()] + rest[cred_match.end():]
            rest = rest.strip()

        url = rest.strip()

        if not name:
            name = f'HTTP {method}'

        block = parse_kv_block(block_str) if block_str else {}

        params: dict[str, Any] = {
            'method': method,
            'url': wrap_expr(url) if url else '',
            'options': {}
        }

        auth_params, credentials = self._build_http_auth(cred_alias)
        params.update(auth_params)

        # Body
        if block.get('jsonBody'):
            params['sendBody'] = True
            params['specifyBody'] = 'json'
            params['jsonBody'] = wrap_expr(str(block['jsonBody']))
        elif block.get('body'):
            params['sendBody'] = True
            body_data = block['body']
            if isinstance(body_data, dict):
                # Use bodyParameters for key-value pairs (n8n default form)
                params['bodyParameters'] = {
                    'parameters': [
                        {'name': ('=' + k if '{{' in str(v) else k),
                         'value': wrap_expr(str(v))}
                        for k, v in body_data.items()
                    ]
                }
            elif isinstance(body_data, str):
                params['specifyBody'] = 'json'
                params['jsonBody'] = wrap_expr(body_data)
            else:
                params['specifyBody'] = 'json'
                params['jsonBody'] = wrap_expr(str(body_data))

        # Query parameters
        if block.get('query'):
            params['sendQuery'] = True
            q = block['query']
            if isinstance(q, dict):
                params['queryParameters'] = {
                    'parameters': [
                        {'name': k, 'value': wrap_expr(str(v))} for k, v in q.items()
                    ]
                }

        # Headers
        if block.get('headers'):
            params['sendHeaders'] = True
            h = block['headers']
            if isinstance(h, dict):
                params['headerParameters'] = {
                    'parameters': [
                        {'name': k, 'value': wrap_expr(str(v))} for k, v in h.items()
                    ]
                }

        self._apply_options(block, params)

        _HTTP_HANDLED_KEYS = frozenset({
            'body', 'jsonBody', 'query', 'headers', 'options',
        })
        for k, v in block.items():
            if k not in _HTTP_HANDLED_KEYS and k not in params:
                params[k] = wrap_expr(str(v)) if isinstance(v, str) else v

        node = Node(self._unique_name(name), 'n8n-nodes-base.httpRequest', params,
                     credentials=credentials, flags=flags)
        self.nodes.append(node)

    def parse_code(self, line: str):
        # CODE "Name" [python] [+each] `code` or ```code```
        code = ''
        code_start = len(line)

        triple = re.search(r'```([\s\S]*?)```', line)
        if triple:
            code = triple.group(1).strip()
            code_start = triple.start()
        else:
            name_m = re.match(r'CODE\s+' + QUOTED_NAME, line)
            after = line[name_m.end():] if name_m else line[4:]
            bt = re.search(r'`([^`]*)`', after)
            if bt:
                code = bt.group(1).strip()
                code_start = (name_m.end() if name_m else 4) + bt.start()
            else:
                code = after.strip().strip('`')

        prefix = line[:code_start].strip()

        name_m = re.match(r'CODE\s+' + QUOTED_NAME, prefix)
        if name_m:
            name = unquote_name(name_m.group(1))
            middle = prefix[name_m.end():].strip()
        else:
            name = 'Code'
            middle = prefix.replace('CODE', '', 1).strip()

        language = 'javaScript'
        mode = 'runOnceForAllItems'
        for token in middle.split():
            if token.lower() == 'python':
                language = 'python'
            elif token == '+each':
                mode = 'runOnceForEachItem'

        code_key = 'pythonCode' if language == 'python' else 'jsCode'
        params: dict[str, Any] = {code_key: code}
        if mode != 'runOnceForAllItems':
            params['mode'] = mode
        if language != 'javaScript':
            params['language'] = language

        node = Node(self._unique_name(name), 'n8n-nodes-base.code', params)
        self.nodes.append(node)

    def parse_filter(self, line: str):
        prefix, block_str = extract_block(line)
        m = re.match(r'FILTER\s+' + QUOTED_NAME, prefix)
        name = unquote_name(m.group(1)) if m else 'Filter'

        block = parse_kv_block(block_str) if block_str else {}
        params = parse_conditions_block(block)
        self._apply_options(block, params)
        if 'looseTypeValidation' in block:
            params['looseTypeValidation'] = block['looseTypeValidation']

        node = Node(self._unique_name(name), 'n8n-nodes-base.filter', params)
        self.nodes.append(node)

    def parse_if(self, line: str):
        prefix, block_str = extract_block(line)
        m = re.match(r'IF\s+' + QUOTED_NAME, prefix)
        name = unquote_name(m.group(1)) if m else 'If'

        block = parse_kv_block(block_str) if block_str else {}
        params = parse_conditions_block(block)
        self._apply_options(block, params)
        if 'looseTypeValidation' in block:
            params['looseTypeValidation'] = block['looseTypeValidation']

        node = Node(self._unique_name(name), 'n8n-nodes-base.if', params)
        self.nodes.append(node)

    def parse_merge(self, line: str):
        prefix, block_str = extract_block(line)
        m = re.match(r'MERGE\s+' + QUOTED_NAME, prefix)
        name = unquote_name(m.group(1)) if m else 'Merge'

        block = parse_kv_block(block_str) if block_str else {}

        params: dict[str, Any] = {'options': {}}
        mode = block.get('mode', 'combine')
        params['mode'] = mode

        if mode == 'combine':
            by_val = block.get('by', 'combineByPosition')
            # Map shorthand to n8n values
            by_map = {'position': 'combineByPosition', 'fields': 'combineByFields', 'all': 'combineAll'}
            params['combineBy'] = by_map.get(by_val, by_val)
            if block.get('inputs'):
                params['numberInputs'] = int(block['inputs'])
        elif mode == 'chooseBranch':
            if block.get('useInput'):
                params['useDataOfInput'] = int(block['useInput'])
        elif mode == 'append':
            pass

        self._apply_options(block, params)

        node = Node(self._unique_name(name), 'n8n-nodes-base.merge', params)
        self.nodes.append(node)

    def parse_gsheet(self, line: str):
        prefix, block_str = extract_block(line)
        prefix, name = extract_as_name(prefix)

        # GSHEET READ|UPDATE|APPEND @cred
        parts = prefix.split()
        operation = parts[1].lower() if len(parts) > 1 else 'getAll'
        cred_alias = ''
        for p in parts:
            if p.startswith('@'):
                cred_alias = p

        op_map = {'read': 'read', 'update': 'update', 'append': 'appendOrUpdate',
                   'getall': 'read', 'get': 'read'}
        n8n_op = op_map.get(operation, operation)

        if not name:
            name = f'Google Sheets {operation.title()}'

        block = parse_kv_block(block_str) if block_str else {}

        params: dict[str, Any] = {}
        if n8n_op != 'read':
            params['operation'] = n8n_op

        if block.get('doc'):
            params['documentId'] = {'__rl': True, 'value': wrap_expr(str(block['doc'])), 'mode': 'url'}
        if block.get('sheet'):
            params['sheetName'] = {'__rl': True, 'value': wrap_expr(str(block['sheet'])), 'mode': 'name'}

        if block.get('values') and isinstance(block['values'], dict):
            columns: dict[str, Any] = {
                'mappingMode': 'defineBelow',
                'value': {},
            }
            for k, v in block['values'].items():
                columns['value'][k] = wrap_expr(str(v)) if isinstance(v, str) else v

            if block.get('match'):
                match_cols = block['match'] if isinstance(block['match'], list) else [block['match']]
                columns['matchingColumns'] = match_cols

            columns['schema'] = [
                {
                    'id': col_name, 'displayName': col_name,
                    'required': False, 'defaultMatch': False,
                    'display': True, 'type': 'string', 'canBeUsedToMatch': True
                }
                for col_name in columns['value'].keys()
            ]
            params['columns'] = columns

        params['options'] = {}
        self._apply_options(block, params)

        credentials = {}
        if cred_alias:
            credentials = self._resolve_credential(cred_alias)
        else:
            credentials = {'googleSheetsOAuth2Api': {'id': generate_id(), 'name': 'Google Sheets account'}}

        node = Node(self._unique_name(name), 'n8n-nodes-base.googleSheets', params, credentials)
        self.nodes.append(node)

    def parse_gdrive(self, line: str):
        prefix, block_str = extract_block(line)
        prefix, name = extract_as_name(prefix)

        parts = prefix.split()
        operation = parts[1].lower() if len(parts) > 1 else 'download'
        cred_alias = ''
        for p in parts:
            if p.startswith('@'):
                cred_alias = p

        if not name:
            name = f'Google Drive {operation.title()}'

        block = parse_kv_block(block_str) if block_str else {}

        params: dict[str, Any] = {'operation': operation, 'options': {}}
        if block.get('fileId'):
            params['fileId'] = {'__rl': True, 'value': wrap_expr(str(block['fileId'])), 'mode': 'id'}
        self._apply_options(block, params)

        credentials = {}
        if cred_alias:
            credentials = self._resolve_credential(cred_alias)
        else:
            credentials = {'googleDriveOAuth2Api': {'id': generate_id(), 'name': 'Google Drive account'}}

        node = Node(self._unique_name(name), 'n8n-nodes-base.googleDrive', params, credentials)
        self.nodes.append(node)

    # --- AI / LangChain node parsers ---

    def parse_agent(self, line: str):
        """Parse: AGENT "Name" { systemMessage: "..." }"""
        prefix, block_str = extract_block(line)
        m = re.match(r'AGENT\s+' + QUOTED_NAME, prefix)
        name = unquote_name(m.group(1)) if m else 'AI Agent'

        block = parse_kv_block(block_str) if block_str else {}

        params: dict[str, Any] = {'options': {}}
        if block.get('systemMessage'):
            params['options']['systemMessage'] = block['systemMessage']
        self._apply_options(block, params)

        node = Node(self._unique_name(name), '@n8n/n8n-nodes-langchain.agent', params)
        self.nodes.append(node)

    def parse_llm(self, line: str):
        """Parse: LLM provider @cred AS "Name" [disabled] { model: "...", temperature: 0 }"""
        prefix, block_str = extract_block(line)
        prefix, name = extract_as_name(prefix)
        prefix, flags = extract_flags(prefix)

        # Extract credential
        cred_alias = ''
        cred_match = re.search(r'@(\w+)', prefix)
        if cred_match:
            cred_alias = cred_match.group(1)
            prefix = prefix[:cred_match.start()] + prefix[cred_match.end():]

        # Extract provider: LLM <provider>
        parts = prefix.split()
        provider = parts[1].lower() if len(parts) > 1 else 'openai'

        if not name:
            name = provider.title()

        block = parse_kv_block(block_str) if block_str else {}

        # Map provider to n8n node type and credential type
        provider_map = {
            'gemini': {
                'type': '@n8n/n8n-nodes-langchain.lmChatGoogleGemini',
                'cred_type': 'googlePalmApi',
                'model_key': 'modelName',
            },
            'openai': {
                'type': '@n8n/n8n-nodes-langchain.lmChatOpenAi',
                'cred_type': 'openAiApi',
                'model_key': 'model',
                'model_rl': True,
            },
            'anthropic': {
                'type': '@n8n/n8n-nodes-langchain.lmChatAnthropic',
                'cred_type': 'anthropicApi',
                'model_key': 'model',
                'model_rl': True,
            },
            'ollama': {
                'type': '@n8n/n8n-nodes-langchain.lmChatOllama',
                'cred_type': 'ollamaApi',
                'model_key': 'model',
            },
        }

        pinfo = provider_map.get(provider, provider_map['openai'])
        n8n_type = pinfo['type']

        params: dict[str, Any] = {'options': {}}

        # Model
        model_val = block.get('model', '')
        if model_val:
            if pinfo.get('model_rl'):
                params[pinfo['model_key']] = {
                    '__rl': True, 'mode': 'list', 'value': model_val
                }
            else:
                params[pinfo['model_key']] = model_val

        if 'temperature' in block:
            params['options']['temperature'] = block['temperature']
        self._apply_options(block, params)

        # Credentials
        credentials = {}
        if cred_alias:
            credentials = self._resolve_credential(cred_alias)
        elif pinfo.get('cred_type'):
            credentials = {
                pinfo['cred_type']: {
                    'id': generate_id(),
                    'name': f'{pinfo["cred_type"]} Credential'
                }
            }

        node = Node(self._unique_name(name), n8n_type, params,
                     credentials=credentials, flags=flags)
        self.nodes.append(node)

    def parse_memory(self, line: str):
        """Parse: MEMORY type AS "Name" { contextWindowLength: 30 }"""
        prefix, block_str = extract_block(line)
        prefix, name = extract_as_name(prefix)

        parts = prefix.split()
        mem_type = parts[1].lower() if len(parts) > 1 else 'buffer'

        if not name:
            name = 'Memory'

        block = parse_kv_block(block_str) if block_str else {}

        mem_type_map = {
            'buffer': '@n8n/n8n-nodes-langchain.memoryBufferWindow',
            'postgres': '@n8n/n8n-nodes-langchain.memoryPostgresChat',
            'redis': '@n8n/n8n-nodes-langchain.memoryRedisChat',
            'zep': '@n8n/n8n-nodes-langchain.memoryZep',
            'motorhead': '@n8n/n8n-nodes-langchain.memoryMotorhead',
            'xata': '@n8n/n8n-nodes-langchain.memoryXata',
        }
        n8n_type = mem_type_map.get(mem_type, mem_type_map['buffer'])

        params: dict[str, Any] = {}
        for k, v in block.items():
            if k == 'options':
                continue
            if k == 'contextWindowLength':
                params[k] = int(v)
            else:
                params[k] = v
        self._apply_options(block, params)

        node = Node(self._unique_name(name), n8n_type, params)
        self.nodes.append(node)

    def parse_tool(self, line: str):
        """Parse: TOOL type AS "Name" { ... }"""
        prefix, block_str = extract_block(line)
        prefix, name = extract_as_name(prefix)
        prefix, flags = extract_flags(prefix)

        # Extract credential if present
        cred_alias = ''
        cred_match = re.search(r'@(\w+)', prefix)
        if cred_match:
            cred_alias = cred_match.group(1)
            prefix = prefix[:cred_match.start()] + prefix[cred_match.end():]

        parts = prefix.split()
        tool_type = parts[1].lower() if len(parts) > 1 else 'http'

        if not name:
            name = f'Tool ({tool_type})'

        block = parse_kv_block(block_str) if block_str else {}

        tool_type_map = {
            'http': 'n8n-nodes-base.httpRequestTool',
            'code': '@n8n/n8n-nodes-langchain.toolCode',
            'wikipedia': '@n8n/n8n-nodes-langchain.toolWikipedia',
            'datetime': 'n8n-nodes-base.dateTimeTool',
            'crypto': 'n8n-nodes-base.cryptoTool',
            'rss': 'n8n-nodes-base.rssFeedReadTool',
            'calculator': '@n8n/n8n-nodes-langchain.toolCalculator',
            'wolframalpha': '@n8n/n8n-nodes-langchain.toolWolframAlpha',
            'serp': '@n8n/n8n-nodes-langchain.toolSerpApi',
        }
        n8n_type = tool_type_map.get(tool_type, tool_type_map['http'])

        params: dict[str, Any] = {}

        # Common tool fields
        if block.get('description'):
            params['toolDescription'] = block['description']
        if block.get('url'):
            params['url'] = block['url']

        # HTTP tool specifics
        if tool_type == 'http':
            if block.get('optimizeResponse'):
                params['optimizeResponse'] = True
            if block.get('fields'):
                params['fields'] = block['fields']
                params['fieldsToInclude'] = 'selected'
            params['options'] = {}

        # Code tool specifics
        elif tool_type == 'code':
            if block.get('code'):
                params['jsCode'] = block['code']
            if block.get('description'):
                params['description'] = block['description']
                del params['toolDescription']
            if block.get('schemaExample'):
                params['jsonSchemaExample'] = block['schemaExample']
                params['specifyInputSchema'] = True

        # DateTime tool
        elif tool_type == 'datetime':
            if block.get('operation'):
                params['operation'] = block['operation']
            if block.get('outputFieldName'):
                params['outputFieldName'] = block['outputFieldName']
            params['options'] = {}
            if block.get('description'):
                params['descriptionType'] = 'manual'

        # Crypto tool
        elif tool_type == 'crypto':
            if block.get('action'):
                params['action'] = block['action']
            if block.get('encodingType'):
                params['encodingType'] = block['encodingType']
            if block.get('stringLength'):
                params['stringLength'] = wrap_expr(str(block['stringLength'])) \
                    if isinstance(block['stringLength'], str) else block['stringLength']
            if block.get('dataPropertyName'):
                params['dataPropertyName'] = block['dataPropertyName']
            else:
                params['dataPropertyName'] = name.replace(' ', '_').lower()

        # RSS tool
        elif tool_type == 'rss':
            params['options'] = {}

        # Wikipedia has no params
        elif tool_type == 'wikipedia':
            pass

        self._apply_options(block, params)

        # Credentials
        credentials = {}
        if cred_alias:
            credentials = self._resolve_credential(cred_alias)

        node = Node(self._unique_name(name), n8n_type, params,
                     credentials=credentials, flags=flags)
        self.nodes.append(node)

    def parse_node(self, line: str):
        """Generic node handler: NODE "type" @cred AS "Name" [flags] { params }

        Supports any n8n node type using the registry for version resolution
        and type-aware parameter serialization.

        Examples:
            NODE "n8n-nodes-base.redis" @redis AS "Get Key" { operation: "get", key: "mykey" }
            NODE "n8n-nodes-base.postgres" @pg AS "Query" { operation: "executeQuery", query: "SELECT 1" }
        """
        prefix, block_str = extract_block(line)
        prefix, name = extract_as_name(prefix)
        prefix, flags = extract_flags(prefix)

        # Extract credential alias
        cred_alias = ''
        cred_match = re.search(r'@(\w+)', prefix)
        if cred_match:
            cred_alias = cred_match.group(1)
            prefix = prefix[:cred_match.start()] + prefix[cred_match.end():]
            prefix = prefix.strip()

        # Extract the node type: NODE "n8n-nodes-base.something" ...
        m = re.match(r'NODE\s+' + QUOTED_NAME, prefix)
        if not m:
            # Try unquoted: NODE n8n-nodes-base.something
            m = re.match(r'NODE\s+(\S+)', prefix)
        if not m:
            raise NflowError("NODE requires a type name", self._current_line)

        node_type = m.group(1)

        # If no package prefix, assume n8n-nodes-base
        if '.' not in node_type:
            node_type = f'n8n-nodes-base.{node_type}'

        if not name:
            registry = get_registry()
            entry = registry.get_node(node_type)
            name = entry.get('defaultName', node_type) if entry else node_type

        block = parse_kv_block(block_str) if block_str else {}

        # Use registry-aware serialization for parameters
        params: dict[str, Any] = {}
        options_block = block.pop('options', None)
        for k, v in block.items():
            params[k] = v

        params = serialize_node_params(params, node_type)

        if isinstance(options_block, dict) and options_block:
            params['options'] = options_block

        # Credentials
        credentials = {}
        if cred_alias:
            credentials = self._resolve_credential(cred_alias)

        node = Node(self._unique_name(name), node_type, params,
                     credentials=credentials, flags=flags)
        self.nodes.append(node)

    def parse_noop(self, line: str):
        m = re.match(r'NOOP\s+' + QUOTED_NAME, line)
        name = unquote_name(m.group(1)) if m else 'No Operation'
        node = Node(self._unique_name(name), 'n8n-nodes-base.noOp', {})
        self.nodes.append(node)

    def parse_note(self, line: str):
        prefix, block_str = extract_block(line)
        m = re.match(r'NOTE\s+' + QUOTED_NAME, prefix)
        name = unquote_name(m.group(1)) if m else 'Sticky Note'
        block = parse_kv_block(block_str) if block_str else {}
        params = {
            'content': block.get('content', ''),
            'height': block.get('height', 160),
            'width': block.get('width', 240),
            'color': block.get('color', 4),
        }
        node = Node(self._unique_name(name), 'n8n-nodes-base.stickyNote', params)
        self.nodes.append(node)

    def parse_datetime(self, line: str):
        """Parse: DATETIME "Name" { operation: extractDate, part: week, ... }"""
        prefix, block_str = extract_block(line)
        prefix, name = extract_as_name(prefix)
        prefix, flags = extract_flags(prefix)
        if not name:
            m = re.match(r'DATETIME\s+' + QUOTED_NAME, prefix)
            name = unquote_name(m.group(1)) if m else 'Date & Time'
        block = parse_kv_block(block_str) if block_str else {}

        params: dict[str, Any] = {'options': {}}
        for k, v in block.items():
            if k != 'options':
                params[k] = v
        self._apply_options(block, params)

        node = Node(self._unique_name(name), 'n8n-nodes-base.dateTime', params, flags=flags)
        self.nodes.append(node)

    def parse_limit(self, line: str):
        """Parse: LIMIT "Name" { maxItems: 10, keep: lastItems }"""
        prefix, block_str = extract_block(line)
        prefix, name = extract_as_name(prefix)
        prefix, flags = extract_flags(prefix)
        if not name:
            m = re.match(r'LIMIT\s+' + QUOTED_NAME, prefix)
            name = unquote_name(m.group(1)) if m else 'Limit'
        block = parse_kv_block(block_str) if block_str else {}

        params: dict[str, Any] = {}
        for k, v in block.items():
            if k != 'options':
                params[k] = v
        self._apply_options(block, params)

        node = Node(self._unique_name(name), 'n8n-nodes-base.limit', params, flags=flags)
        self.nodes.append(node)

    def parse_switch(self, line: str):
        """Parse: SWITCH "Name" { rules: [AND [...], AND [...]], options: { ignoreCase: true } }"""
        prefix, block_str = extract_block(line)
        prefix, name = extract_as_name(prefix)
        prefix, flags = extract_flags(prefix)
        if not name:
            m = re.match(r'SWITCH\s+' + QUOTED_NAME, prefix)
            name = unquote_name(m.group(1)) if m else 'Switch'
        block = parse_kv_block(block_str) if block_str else {}

        params: dict[str, Any] = {'options': {}}

        rules_raw = block.get('rules', [])
        if isinstance(rules_raw, list):
            rule_values = []
            for rule in rules_raw:
                if isinstance(rule, str):
                    rule_values.append(self._parse_switch_rule(rule))
                elif isinstance(rule, dict):
                    rule_values.append(rule)
            params['rules'] = {'values': rule_values}
        elif isinstance(rules_raw, dict):
            params['rules'] = rules_raw

        skip_keys = {'rules', 'options'}
        for k, v in block.items():
            if k not in skip_keys:
                params[k] = v
        self._apply_options(block, params)

        node = Node(self._unique_name(name), 'n8n-nodes-base.switch', params, flags=flags)
        self.nodes.append(node)

    @staticmethod
    def _parse_switch_rule(rule_str: str) -> dict:
        """Parse a single SWITCH rule string like AND [{{ $json.x }} equals "y"]"""
        rule_str = rule_str.strip()
        combinator = 'and'
        if rule_str.upper().startswith('OR'):
            combinator = 'or'
            rule_str = rule_str[2:].strip()
        elif rule_str.upper().startswith('AND'):
            combinator = 'and'
            rule_str = rule_str[3:].strip()

        if rule_str.startswith('['):
            rule_str = rule_str[1:]
        if rule_str.endswith(']'):
            rule_str = rule_str[:-1]

        cond_strings = smart_split(rule_str, ',')
        parsed_conditions = [parse_condition_line(c) for c in cond_strings if c.strip()]

        return {
            'conditions': {
                'options': {
                    'caseSensitive': False,
                    'leftValue': '',
                    'typeValidation': 'loose',
                    'version': 3
                },
                'conditions': parsed_conditions,
                'combinator': combinator
            }
        }

    def parse_loop(self, line: str):
        """Parse: LOOP "Name" { batchSize: 1 }"""
        prefix, block_str = extract_block(line)
        prefix, name = extract_as_name(prefix)
        prefix, flags = extract_flags(prefix)
        if not name:
            m = re.match(r'LOOP\s+' + QUOTED_NAME, prefix)
            name = unquote_name(m.group(1)) if m else 'Loop Over Items'
        block = parse_kv_block(block_str) if block_str else {}

        params: dict[str, Any] = {'options': {}}
        if 'batchSize' in block:
            val = block['batchSize']
            params['batchSize'] = val if isinstance(val, str) and val.startswith('=') else int(val)
        skip_keys = {'batchSize', 'options'}
        for k, v in block.items():
            if k not in skip_keys:
                params[k] = v
        self._apply_options(block, params)

        node = Node(self._unique_name(name), 'n8n-nodes-base.splitInBatches', params, flags=flags)
        self.nodes.append(node)

    def parse_position(self, line: str):
        m = re.match(r'POSITION\s+' + QUOTED_NAME + r'\s*\(\s*(-?\d+)\s*,\s*(-?\d+)\s*\)', line)
        if m:
            self.positions[unquote_name(m.group(1))] = [int(m.group(2)), int(m.group(3))]

    def parse_connection(self, line: str):
        """Parse connection lines like:
        "A" -> "B" -> "C"
        "A" -> "B", "C"
        "A" -> TRUE -> "B"
        "A" -> FALSE -> "B"
        "A" -> OK -> "B"
        "A" -> ERR -> "B"
        "A" -> "B":2
        """
        # Tokenize by ->
        segments = [s.strip() for s in line.split('->')]

        for i in range(len(segments) - 1):
            left_raw = segments[i]
            right_raw = segments[i + 1]

            # Determine source output index
            source_output = 0

            # Check if left_raw is a routing keyword (TRUE/FALSE/OK/ERR)
            if left_raw.upper() in ('TRUE', 'OK'):
                source_output = 0
                # The actual source is the segment before this
                continue  # handled below
            elif left_raw.upper() in ('FALSE', 'ERR'):
                source_output = 1
                continue

            # Check if right_raw starts with a routing keyword
            if right_raw.upper() in ('TRUE', 'OK', 'FALSE', 'ERR'):
                # This is "Source" -> TRUE/FALSE -> "Target"
                # We'll handle it when we get to the next segment
                continue

            # Check if left segment ended with a routing keyword
            source_name = self._extract_node_name(left_raw)

            # Check if previous segment was a routing keyword
            if i > 0:
                prev = segments[i - 1].strip()
                if i >= 2:
                    prev_prev = segments[i - 2].strip().upper()
                if left_raw.upper() in ('TRUE', 'OK'):
                    source_output = 0
                    source_name = self._extract_node_name(segments[i - 1])
                elif left_raw.upper() in ('FALSE', 'ERR'):
                    source_output = 1
                    source_name = self._extract_node_name(segments[i - 1])

            # Parse targets (may be comma-separated)
            targets = smart_split(right_raw, ',')
            for t in targets:
                t = t.strip()
                if t.upper() in ('TRUE', 'OK', 'FALSE', 'ERR'):
                    continue
                target_input = 0
                # Check for :N suffix
                m = re.match(QUOTED_NAME + r'\s*:\s*(\d+)', t)
                if m:
                    target_name = unquote_name(m.group(1))
                    target_input = int(m.group(2))
                else:
                    target_name = self._extract_node_name(t)

                if source_name and target_name:
                    self.connections.append(Connection(source_name, target_name,
                                                       source_output, target_input))

    def _extract_node_name(self, s: str) -> str:
        """Extract a node name from a segment (strip quotes)."""
        s = s.strip()
        m = re.match(QUOTED_NAME, s)
        if m:
            return unquote_name(m.group(1))
        # Unquoted name
        s = s.strip()
        # Remove :N suffix
        s = re.sub(r':\d+$', '', s)
        return s if s else ''

    # --- Connection parser that handles the full routing syntax ---

    # Map of AI routing keywords to n8n connection types
    AI_CONNECTION_TYPES = {
        'LLM': 'ai_languageModel',
        'TOOL': 'ai_tool',
        'MEMORY': 'ai_memory',
        'OUTPUT_PARSER': 'ai_outputParser',
        'RETRIEVER': 'ai_retriever',
        'EMBEDDING': 'ai_embedding',
        'DOCUMENT': 'ai_document',
        'TEXT_SPLITTER': 'ai_textSplitter',
        'VECTOR_STORE': 'ai_vectorStore',
    }

    def parse_connection_line(self, line: str):
        """Robust connection parser that handles:
        "A" -> "B"
        "A" -> "B", "C"
        "A" -> TRUE -> "B"         (branch output 0)
        "A" -> FALSE -> "B"        (branch output 1)
        "A" -> OK -> "B"           (success output 0)
        "A" -> ERR -> "B"          (error output 1)
        "A" -> LLM -> "B"          (ai_languageModel connection)
        "A" -> TOOL -> "B"         (ai_tool connection)
        "A" -> MEMORY -> "B"       (ai_memory connection)
        "A" -> "B":2               (target input slot)
        """
        parts = [p.strip() for p in line.split('->')]

        i = 0
        while i < len(parts) - 1:
            source_part = parts[i]
            next_part = parts[i + 1]

            source_name = self._extract_node_name(source_part)
            source_output = 0

            # Check if next_part is a routing keyword
            upper = next_part.upper()

            if upper in ('TRUE', 'OK', 'DONE'):
                source_output = 0
                if i + 2 < len(parts):
                    target_part = parts[i + 2]
                    self._add_connections_to_targets(source_name, source_output, target_part)
                    i += 2
                    continue
                i += 2
                continue
            elif upper in ('FALSE', 'ERR', 'LOOP'):
                source_output = 1
                if i + 2 < len(parts):
                    target_part = parts[i + 2]
                    self._add_connections_to_targets(source_name, source_output, target_part)
                    i += 2
                    continue
                i += 2
                continue
            elif upper.isdigit():
                source_output = int(upper)
                if i + 2 < len(parts):
                    target_part = parts[i + 2]
                    self._add_connections_to_targets(source_name, source_output, target_part)
                    i += 2
                    continue
                i += 2
                continue
            elif upper in self.AI_CONNECTION_TYPES:
                conn_type = self.AI_CONNECTION_TYPES[upper]
                if i + 2 < len(parts):
                    target_part = parts[i + 2]
                    self._add_connections_to_targets(
                        source_name, 0, target_part, connection_type=conn_type)
                    i += 2
                    continue
                i += 2
                continue
            else:
                # Direct connection
                self._add_connections_to_targets(source_name, source_output, next_part)
                i += 1

    def _add_connections_to_targets(self, source: str, source_output: int,
                                     target_str: str, connection_type: str = 'main'):
        """Add connections from source to one or more comma-separated targets."""
        targets = smart_split(target_str, ',')
        for t in targets:
            t = t.strip()
            if not t:
                continue
            target_input = 0
            m = re.match(QUOTED_NAME + r'\s*:\s*(\d+)', t)
            if m:
                target_name = unquote_name(m.group(1))
                target_input = int(m.group(2))
            else:
                target_name = self._extract_node_name(t)
            if source and target_name:
                self.connections.append(Connection(source, target_name,
                                                   source_output, target_input,
                                                   connection_type,
                                                   self._current_line))

    # --- Main parse method ---

    def parse(self, source: str) -> dict:
        numbered_lines = tokenize_with_lines(source)

        for line_num, line in numbered_lines:
            self._current_line = line_num
            first_word = line.split()[0].upper() if line.split() else ''

            try:
                if first_word == 'WORKFLOW':
                    self.parse_workflow(line)
                elif first_word == 'CREDENTIAL':
                    self.parse_credential(line)
                elif first_word == 'TRIGGER':
                    self.parse_trigger(line)
                elif first_word == 'SET':
                    self.parse_set(line)
                elif first_word == 'HTTP':
                    self.parse_http(line)
                elif first_word == 'CODE':
                    self.parse_code(line)
                elif first_word == 'FILTER':
                    self.parse_filter(line)
                elif first_word == 'IF':
                    self.parse_if(line)
                elif first_word == 'SWITCH':
                    self.parse_switch(line)
                elif first_word == 'MERGE':
                    self.parse_merge(line)
                elif first_word == 'DATETIME':
                    self.parse_datetime(line)
                elif first_word == 'LIMIT':
                    self.parse_limit(line)
                elif first_word == 'LOOP':
                    self.parse_loop(line)
                elif first_word == 'GSHEET':
                    self.parse_gsheet(line)
                elif first_word == 'GDRIVE':
                    self.parse_gdrive(line)
                elif first_word == 'AGENT':
                    self.parse_agent(line)
                elif first_word == 'LLM':
                    self.parse_llm(line)
                elif first_word == 'MEMORY':
                    self.parse_memory(line)
                elif first_word == 'TOOL':
                    self.parse_tool(line)
                elif first_word == 'NODE':
                    self.parse_node(line)
                elif first_word == 'NOOP':
                    self.parse_noop(line)
                elif first_word == 'NOTE':
                    self.parse_note(line)
                elif first_word == 'POSITION':
                    self.parse_position(line)
                elif '->' in line and '"' in line:
                    self.parse_connection_line(line)
                elif first_word:
                    raise NflowError(
                        f"unknown keyword '{first_word}'", line_num)
            except NflowError:
                raise
            except Exception as e:
                raise NflowError(str(e), line_num) from e

        self._validate_connections()
        self._auto_layout()

        self._credentials_json = self._build_credentials()
        return self._build_workflow()

    def get_credentials(self) -> list[dict]:
        """Return the credentials JSON list after parse() has been called."""
        return getattr(self, '_credentials_json', [])

    def _validate_connections(self):
        """Check that all connections reference existing nodes."""
        node_names = {n.name for n in self.nodes}
        for conn in self.connections:
            if conn.source not in node_names:
                raise NflowError(
                    f"connection references unknown node '{conn.source}'",
                    conn.line_num)
            if conn.target not in node_names:
                raise NflowError(
                    f"connection references unknown node '{conn.target}'",
                    conn.line_num)

    def _auto_layout(self):
        """Simple auto-layout: place nodes in a grid based on order, applying
        manual positions where specified."""
        x, y = 0, 0
        col_width = 250
        row_height = 200

        # Build a dependency graph to determine ordering
        node_map = {n.name: n for n in self.nodes}
        placed = set()

        for i, node in enumerate(self.nodes):
            if node.name in self.positions:
                node.position = self.positions[node.name]
            else:
                node.position = [x, y]
                x += col_width
                if x > col_width * 6:
                    x = 0
                    y += row_height

    def _build_workflow(self) -> dict:
        """Build the final n8n workflow JSON."""
        # Build connections dict
        # Structure: { "NodeName": { "main": [[...]], "ai_tool": [[...]], ... } }
        conn_dict: dict[str, dict] = {}

        for c in self.connections:
            if c.source not in conn_dict:
                conn_dict[c.source] = {}

            ctype = c.connection_type
            if ctype not in conn_dict[c.source]:
                conn_dict[c.source][ctype] = []

            slots = conn_dict[c.source][ctype]
            # Ensure enough output slots
            while len(slots) <= c.source_output:
                slots.append([])

            slots[c.source_output].append({
                'node': c.target,
                'type': ctype,
                'index': c.target_input
            })

        return {
            'name': self.workflow_name,
            'nodes': [n.to_dict() for n in self.nodes],
            'pinData': {},
            'connections': conn_dict,
            'active': self.active,
            'settings': {
                'executionOrder': 'v1',
                'binaryMode': 'separate',
                'availableInMCP': False
            },
            'versionId': generate_id(),
            'meta': {
                'templateCredsSetupCompleted': True,
                'instanceId': generate_id()
            },
            'id': generate_id()[:16],
            'tags': []
        }

    def _build_credentials(self) -> list[dict]:
        """Build n8n-compatible credentials JSON for import.
        Only includes credentials NOT linked from an external file."""
        now = "2000-01-01T00:00:00.000Z"
        result = []
        for cred in self.credentials.values():
            if cred['name'] in self._linked_cred_names:
                continue
            result.append({
                'id': cred['id'],
                'name': cred['name'],
                'type': cred['type'],
                'data': {},
                'createdAt': now,
                'updatedAt': now,
            })
        return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse

    ap = argparse.ArgumentParser(
        prog='nflow',
        description='Compile .nflow files to n8n workflow JSON.',
    )
    ap.add_argument('input', nargs='?', help='input .nflow file')
    ap.add_argument('-o', '--output', metavar='FILE',
                    help='write output to FILE (default: stdout)')
    ap.add_argument('--stdin', action='store_true',
                    help='read source from stdin')
    ap.add_argument('--validate', action='store_true',
                    help='check syntax without producing output')
    ap.add_argument('--compact', action='store_true',
                    help='emit compact JSON (no indentation)')
    ap.add_argument('-c', '--credentials', metavar='FILE',
                    help='existing n8n credentials JSON to link by name')
    ap.add_argument('-q', '--quiet', action='store_true',
                    help='suppress informational messages')
    ap.add_argument('-V', '--version', action='version',
                    version=f'%(prog)s {__version__}')

    args = ap.parse_args()

    if not args.input and not args.stdin:
        ap.error('provide an input file or use --stdin')

    try:
        if args.stdin:
            source = sys.stdin.read()
        else:
            with open(args.input) as f:
                source = f.read()
    except FileNotFoundError:
        print(f"nflow: file not found: {args.input}", file=sys.stderr)
        sys.exit(2)
    except OSError as e:
        print(f"nflow: {e}", file=sys.stderr)
        sys.exit(2)

    try:
        compiler = N8nFDLParser()
        if args.credentials:
            compiler.load_credentials(args.credentials)
        workflow = compiler.parse(source)
    except NflowError as e:
        print(f"nflow: {e}", file=sys.stderr)
        sys.exit(1)

    if args.validate:
        if not args.quiet:
            print("OK")
        sys.exit(0)

    indent = None if args.compact else 2
    output = json.dumps(workflow, indent=indent, ensure_ascii=False)

    if args.output:
        with open(args.output, 'w') as f:
            f.write(output)
        if not args.quiet:
            print(f"Wrote {args.output}")
    else:
        print(output)

    credentials = compiler.get_credentials()
    if credentials and args.output:
        import os
        base, ext = os.path.splitext(args.output)
        creds_path = f"{base}-credentials{ext}"
        creds_output = json.dumps(credentials, indent=indent, ensure_ascii=False)
        with open(creds_path, 'w') as f:
            f.write(creds_output)
        if not args.quiet:
            print(f"Wrote {creds_path}")


if __name__ == '__main__':
    main()
