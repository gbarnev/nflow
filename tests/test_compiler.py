"""Tests for nflow parser — covers tokenizer, helpers, node parsers, connections, and integration."""

import json
from pathlib import Path

import pytest
from unittest.mock import patch

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"

from nflow import (
    tokenize_lines,
    parse_value,
    smart_split,
    parse_kv_block,
    find_unquoted,
    wrap_expr,
    unquote_name,
    extract_block,
    extract_as_name,
    extract_flags,
    parse_condition_line,
    parse_conditions_block,
    N8nFDLParser,
    Node,
    Connection,
    QUOTED_NAME,
)


# =========================================================================
# Tokenizer
# =========================================================================

class TestTokenizer:
    def test_blank_lines_stripped(self):
        src = "\n\nSET \"A\" { x: 1 }\n\n\nSET \"B\" { y: 2 }\n"
        lines = tokenize_lines(src)
        assert len(lines) == 2

    def test_comments_stripped(self):
        src = '// header\nSET "A" { x: 1 }\n// middle\nSET "B" { y: 2 }\n// end'
        lines = tokenize_lines(src)
        assert len(lines) == 2
        assert lines[0].startswith('SET "A"')
        assert lines[1].startswith('SET "B"')

    def test_multiline_brace_block(self):
        src = 'SET "Config" {\n  a: 1,\n  b: 2\n}'
        lines = tokenize_lines(src)
        assert len(lines) == 1
        assert 'a: 1' in lines[0]
        assert 'b: 2' in lines[0]

    def test_multiline_triple_backtick(self):
        src = 'CODE "Transform" ```\nconst x = 1;\nreturn x;\n```'
        lines = tokenize_lines(src)
        assert len(lines) == 1
        assert 'const x = 1;' in lines[0]
        assert 'return x;' in lines[0]

    def test_inline_triple_backtick(self):
        src = 'CODE "T" ```return 1;```'
        lines = tokenize_lines(src)
        assert len(lines) == 1
        assert '```return 1;```' in lines[0]

    def test_line_continuation_backslash(self):
        src = 'HTTP GET \\\nhttps://example.com'
        lines = tokenize_lines(src)
        assert len(lines) == 1
        assert 'https://example.com' in lines[0]

    def test_comments_inside_brace_block_skipped(self):
        src = 'SET "A" {\n  x: 1,\n  // comment\n  y: 2\n}'
        lines = tokenize_lines(src)
        assert len(lines) == 1
        assert '// comment' not in lines[0]

    def test_empty_source(self):
        assert tokenize_lines("") == []
        assert tokenize_lines("\n\n\n") == []
        assert tokenize_lines("// only comments\n// more") == []

    def test_single_line_preserved(self):
        src = 'WORKFLOW "Test" active'
        lines = tokenize_lines(src)
        assert lines == ['WORKFLOW "Test" active']


# =========================================================================
# Expression helpers
# =========================================================================

class TestWrapExpr:
    def test_expression_gets_equals_prefix(self):
        assert wrap_expr("{{ $json.field }}") == "={{ $json.field }}"

    def test_already_prefixed(self):
        assert wrap_expr("={{ $json.field }}") == "={{ $json.field }}"

    def test_plain_string_unchanged(self):
        assert wrap_expr("hello") == "hello"

    def test_non_string_passthrough(self):
        assert wrap_expr(42) == 42


class TestUnquoteName:
    def test_basic(self):
        assert unquote_name("hello") == "hello"

    def test_escaped_double_quote(self):
        assert unquote_name('Set \\"processing\\" status') == 'Set "processing" status'

    def test_escaped_single_quote(self):
        assert unquote_name("it\\'s") == "it's"


# =========================================================================
# parse_value
# =========================================================================

class TestParseValue:
    def test_empty(self):
        assert parse_value("") == ""

    def test_true(self):
        assert parse_value("true") is True

    def test_false(self):
        assert parse_value("false") is False

    def test_integer(self):
        assert parse_value("42") == 42

    def test_float(self):
        assert parse_value("3.14") == 3.14

    def test_expression(self):
        assert parse_value("{{ $json.x }}") == "={{ $json.x }}"

    def test_triple_backtick_code(self):
        assert parse_value("```return 1;```") == "return 1;"

    def test_single_backtick_code(self):
        assert parse_value("`return 1;`") == "return 1;"

    def test_double_quoted_string(self):
        assert parse_value('"hello"') == "hello"

    def test_single_quoted_string(self):
        assert parse_value("'hello'") == "hello"

    def test_empty_array(self):
        assert parse_value("[]") == []

    def test_array_of_strings(self):
        result = parse_value('["a", "b", "c"]')
        assert result == ["a", "b", "c"]

    def test_nested_dict(self):
        result = parse_value('{ x: 1, y: 2 }')
        assert result == {"x": 1, "y": 2}

    def test_bare_word(self):
        assert parse_value("everyMinute") == "everyMinute"


# =========================================================================
# smart_split
# =========================================================================

class TestSmartSplit:
    def test_simple(self):
        assert smart_split("a, b, c") == ["a", " b", " c"]

    def test_respects_braces(self):
        result = smart_split('{ a: 1, b: 2 }, c')
        assert len(result) == 2
        assert "a: 1, b: 2" in result[0]

    def test_respects_brackets(self):
        result = smart_split('[1, 2, 3], x')
        assert len(result) == 2

    def test_respects_quotes(self):
        result = smart_split('"a, b", c')
        assert len(result) == 2
        assert result[0] == '"a, b"'

    def test_respects_expressions(self):
        result = smart_split('{{ $json.a, $json.b }}, c')
        assert len(result) == 2

    def test_respects_triple_backticks(self):
        result = smart_split('```a, b```, c')
        assert len(result) == 2

    def test_respects_single_backtick(self):
        result = smart_split('`a, b`, c')
        assert len(result) == 2

    def test_empty(self):
        assert smart_split("") == []


# =========================================================================
# parse_kv_block
# =========================================================================

class TestParseKvBlock:
    def test_simple(self):
        result = parse_kv_block('{ x: 1, y: "hello" }')
        assert result == {"x": 1, "y": "hello"}

    def test_nested(self):
        result = parse_kv_block('{ body: { name: "test" } }')
        assert result == {"body": {"name": "test"}}

    def test_expression_value(self):
        result = parse_kv_block('{ id: {{ $json.id }} }')
        assert result["id"] == "={{ $json.id }}"

    def test_empty(self):
        assert parse_kv_block("{}") == {}
        assert parse_kv_block("") == {}

    def test_quoted_key(self):
        result = parse_kv_block('{ "X-Custom": "value" }')
        assert result["X-Custom"] == "value"

    def test_array_value(self):
        result = parse_kv_block('{ items: ["a", "b"] }')
        assert result["items"] == ["a", "b"]

    def test_boolean_value(self):
        result = parse_kv_block("{ active: true, disabled: false }")
        assert result["active"] is True
        assert result["disabled"] is False


# =========================================================================
# find_unquoted
# =========================================================================

class TestFindUnquoted:
    def test_basic(self):
        assert find_unquoted("key: value", ":") == 3

    def test_colon_inside_quotes(self):
        assert find_unquoted('"http://x": val', ":") == 10

    def test_colon_inside_expression(self):
        # The : inside {{ }} should be skipped
        assert find_unquoted("{{ a:b }}: val", ":") == 9

    def test_not_found(self):
        assert find_unquoted("no colon here", ":") == -1


# =========================================================================
# extract_block, extract_as_name, extract_flags
# =========================================================================

class TestExtractBlock:
    def test_with_block(self):
        prefix, block = extract_block('SET "Config" { x: 1 }')
        assert prefix == 'SET "Config"'
        assert block == "{ x: 1 }"

    def test_without_block(self):
        prefix, block = extract_block('NOOP "Forward"')
        assert prefix == 'NOOP "Forward"'
        assert block == ""


class TestExtractAsName:
    def test_double_quotes(self):
        rest, name = extract_as_name('HTTP GET https://x AS "My Node"')
        assert name == "My Node"
        assert "AS" not in rest

    def test_no_as(self):
        rest, name = extract_as_name('HTTP GET https://x')
        assert name == ""
        assert rest == "HTTP GET https://x"

    def test_escaped_quotes(self):
        rest, name = extract_as_name(r'GSHEET UPDATE @gs AS "Set \"processing\" status"')
        assert name == 'Set "processing" status'


class TestExtractFlags:
    def test_passthrough(self):
        rest, flags = extract_flags('SET "Config" +passthrough')
        assert flags["passthrough"] is True
        assert "+passthrough" not in rest

    def test_disabled(self):
        rest, flags = extract_flags("LLM openai disabled")
        assert flags["disabled"] is True
        assert "disabled" not in rest

    def test_onerror(self):
        rest, flags = extract_flags("HTTP GET url onError:continue")
        assert flags["onError"] == "continue"
        assert "onError" not in rest

    def test_no_flags(self):
        rest, flags = extract_flags("SET something")
        assert flags == {}
        assert rest == "SET something"

    def test_combined_flags(self):
        rest, flags = extract_flags('SET "X" +passthrough onError:continue')
        assert flags["passthrough"] is True
        assert flags["onError"] == "continue"

    def test_execute_once(self):
        rest, flags = extract_flags('HTTP GET url +once')
        assert flags["executeOnce"] is True
        assert "+once" not in rest

    def test_always_output_data(self):
        rest, flags = extract_flags('SET "X" +always')
        assert flags["alwaysOutputData"] is True
        assert "+always" not in rest

    def test_retry_on_fail_flag(self):
        rest, flags = extract_flags('HTTP GET url +retry')
        assert flags["retryOnFail"] is True
        assert "+retry" not in rest

    def test_retry_with_max_tries(self):
        rest, flags = extract_flags('HTTP GET url retry:5')
        assert flags["retryOnFail"] is True
        assert flags["maxTries"] == 5
        assert "retry:" not in rest

    def test_wait_between_tries(self):
        rest, flags = extract_flags('HTTP GET url +retry wait:2000')
        assert flags["retryOnFail"] is True
        assert flags["waitBetweenTries"] == 2000

    def test_notes_double_quotes(self):
        rest, flags = extract_flags('HTTP GET url notes:"Check auth first"')
        assert flags["notes"] == "Check auth first"
        assert "notes:" not in rest

    def test_notes_single_quotes(self):
        rest, flags = extract_flags("HTTP GET url notes:'My note'")
        assert flags["notes"] == "My note"

    def test_all_settings_combined(self):
        rest, flags = extract_flags(
            'HTTP GET url +once +always +retry onError:continue notes:"test"'
        )
        assert flags["executeOnce"] is True
        assert flags["alwaysOutputData"] is True
        assert flags["retryOnFail"] is True
        assert flags["onError"] == "continue"
        assert flags["notes"] == "test"


# =========================================================================
# Condition parsing
# =========================================================================

class TestParseConditionLine:
    def test_equals(self):
        cond = parse_condition_line('{{ $json.status }} equals "active"')
        assert cond["leftValue"] == "={{ $json.status }}"
        assert cond["rightValue"] == "active"
        assert cond["operator"]["type"] == "string"
        assert cond["operator"]["operation"] == "equals"

    def test_not_empty_unary(self):
        cond = parse_condition_line("{{ $json.id }} notEmpty")
        assert cond["operator"]["operation"] == "notEmpty"
        assert cond["operator"].get("singleValue") is True
        assert cond["rightValue"] == ""

    def test_num_equals(self):
        cond = parse_condition_line("{{ $json.error.status }} numEquals 409")
        assert cond["operator"]["type"] == "number"
        assert cond["operator"]["operation"] == "equals"
        assert cond["rightValue"] == 409

    def test_contains(self):
        cond = parse_condition_line('{{ $json.text }} contains "run"')
        assert cond["operator"]["operation"] == "contains"
        assert cond["rightValue"] == "run"

    def test_array_not_empty(self):
        cond = parse_condition_line("{{ $json.items }} arrayNotEmpty")
        assert cond["operator"]["type"] == "array"
        assert cond["operator"]["operation"] == "notEmpty"
        assert cond["operator"]["singleValue"] is True

    def test_is_true(self):
        cond = parse_condition_line("{{ $json.flag }} isTrue")
        assert cond["operator"]["type"] == "boolean"
        assert cond["operator"]["operation"] == "true"

    def test_exists(self):
        cond = parse_condition_line("{{ $json.event }} exists")
        assert cond["operator"]["operation"] == "exists"
        assert cond["operator"]["singleValue"] is True

    def test_gt(self):
        cond = parse_condition_line("{{ $json.count }} gt 5")
        assert cond["operator"]["type"] == "number"
        assert cond["operator"]["operation"] == "gt"
        assert cond["rightValue"] == 5

    def test_regex(self):
        cond = parse_condition_line('{{ $json.email }} regex ".*@test.com"')
        assert cond["operator"]["operation"] == "regex"
        assert cond["rightValue"] == ".*@test.com"

    def test_fallback(self):
        cond = parse_condition_line("{{ $json.something }}")
        assert cond["operator"]["operation"] == "notEmpty"
        assert cond["operator"]["singleValue"] is True


class TestParseConditionsBlock:
    def test_and_conditions(self):
        block = {"conditions": 'AND [{{ $json.a }} equals "x", {{ $json.b }} notEmpty]'}
        result = parse_conditions_block(block)
        conds = result["conditions"]["conditions"]
        assert len(conds) == 2
        assert result["conditions"]["combinator"] == "and"

    def test_or_conditions(self):
        block = {"conditions": 'OR [{{ $json.x }} equals "a"]'}
        result = parse_conditions_block(block)
        assert result["conditions"]["combinator"] == "or"

    def test_default_and(self):
        block = {"conditions": '[{{ $json.a }} notEmpty]'}
        result = parse_conditions_block(block)
        assert result["conditions"]["combinator"] == "and"

    def test_options_structure(self):
        block = {"conditions": 'AND [{{ $json.a }} equals "x"]'}
        result = parse_conditions_block(block)
        opts = result["conditions"]["options"]
        assert opts["caseSensitive"] is True
        assert opts["version"] == 3


# =========================================================================
# Node class
# =========================================================================

class TestNode:
    def test_to_dict_basic(self):
        node = Node("Test", "n8n-nodes-base.set", {"x": 1})
        d = node.to_dict()
        assert d["name"] == "Test"
        assert d["type"] == "n8n-nodes-base.set"
        assert d["parameters"] == {"x": 1}
        assert "id" in d
        assert "position" in d

    def test_to_dict_with_credentials(self):
        creds = {"httpHeaderAuth": {"id": "123", "name": "My Auth"}}
        node = Node("Test", "n8n-nodes-base.httpRequest", {}, credentials=creds)
        d = node.to_dict()
        assert d["credentials"] == creds

    def test_to_dict_onerror(self):
        node = Node("Test", "n8n-nodes-base.httpRequest", {}, flags={"onError": "continue"})
        d = node.to_dict()
        assert d["onError"] == "continueErrorOutput"

    def test_to_dict_disabled(self):
        node = Node("Test", "n8n-nodes-base.set", {}, flags={"disabled": True})
        d = node.to_dict()
        assert d["disabled"] is True

    def test_to_dict_onerror_output(self):
        node = Node("Test", "n8n-nodes-base.httpRequest", {}, flags={"onError": "output"})
        d = node.to_dict()
        assert d["onError"] == "continueRegularOutput"

    def test_to_dict_onerror_stop(self):
        node = Node("Test", "n8n-nodes-base.httpRequest", {}, flags={"onError": "stop"})
        d = node.to_dict()
        assert d["onError"] == "stopWorkflow"

    def test_to_dict_execute_once(self):
        node = Node("Test", "n8n-nodes-base.httpRequest", {}, flags={"executeOnce": True})
        d = node.to_dict()
        assert d["executeOnce"] is True

    def test_to_dict_always_output_data(self):
        node = Node("Test", "n8n-nodes-base.httpRequest", {}, flags={"alwaysOutputData": True})
        d = node.to_dict()
        assert d["alwaysOutputData"] is True

    def test_to_dict_retry_on_fail(self):
        node = Node("Test", "n8n-nodes-base.httpRequest", {},
                     flags={"retryOnFail": True, "maxTries": 5, "waitBetweenTries": 2000})
        d = node.to_dict()
        assert d["retryOnFail"] is True
        assert d["maxTries"] == 5
        assert d["waitBetweenTries"] == 2000

    def test_to_dict_retry_no_extras(self):
        node = Node("Test", "n8n-nodes-base.httpRequest", {}, flags={"retryOnFail": True})
        d = node.to_dict()
        assert d["retryOnFail"] is True
        assert "maxTries" not in d
        assert "waitBetweenTries" not in d

    def test_to_dict_notes(self):
        node = Node("Test", "n8n-nodes-base.httpRequest", {},
                     flags={"notes": "Remember to check auth"})
        d = node.to_dict()
        assert d["notes"] == "Remember to check auth"
        assert d["notesInFlow"] is True

    def test_to_dict_no_notes_key_without_flag(self):
        node = Node("Test", "n8n-nodes-base.httpRequest", {})
        d = node.to_dict()
        assert "notes" not in d
        assert "notesInFlow" not in d

    def test_default_version(self):
        node = Node("T", "n8n-nodes-base.httpRequest", {})
        d = node.to_dict()
        assert d["typeVersion"] == 4.4

    def test_default_version_unknown_type(self):
        node = Node("T", "some.custom.type", {})
        d = node.to_dict()
        assert d["typeVersion"] == 1

    def test_explicit_version(self):
        node = Node("T", "n8n-nodes-base.set", {}, type_version=5.0)
        d = node.to_dict()
        assert d["typeVersion"] == 5.0


# =========================================================================
# Parser — Node types
# =========================================================================

class TestParseWorkflow:
    def test_basic(self):
        p = N8nFDLParser()
        p.parse_workflow('WORKFLOW "My Flow" active')
        assert p.workflow_name == "My Flow"
        assert p.active is True

    def test_inactive(self):
        p = N8nFDLParser()
        p.parse_workflow('WORKFLOW "Inactive Flow"')
        assert p.workflow_name == "Inactive Flow"
        assert p.active is False


class TestParseCredential:
    def test_basic(self):
        p = N8nFDLParser()
        p.parse_credential('CREDENTIAL @myapi = httpHeaderAuth "My API Key"')
        assert "myapi" in p.credentials
        assert p.credentials["myapi"]["type"] == "httpHeaderAuth"
        assert p.credentials["myapi"]["name"] == "My API Key"

    def test_bearer_auth(self):
        p = N8nFDLParser()
        p.parse_credential('CREDENTIAL @bearer = httpBearerAuth "Bearer Auth Token"')
        assert p.credentials["bearer"]["type"] == "httpBearerAuth"

    def test_oauth(self):
        p = N8nFDLParser()
        p.parse_credential('CREDENTIAL @gsheets = googleSheetsOAuth2Api "Google Sheets Account"')
        assert p.credentials["gsheets"]["type"] == "googleSheetsOAuth2Api"


class TestParseTrigger:
    def test_manual(self):
        p = N8nFDLParser()
        p.parse_trigger('TRIGGER manual AS "Run Manually"')
        assert len(p.nodes) == 1
        assert p.nodes[0].name == "Run Manually"
        assert p.nodes[0].type == "n8n-nodes-base.manualTrigger"

    def test_manual_default_name(self):
        p = N8nFDLParser()
        p.parse_trigger("TRIGGER manual")
        assert p.nodes[0].name == "When clicking 'Execute workflow'"

    def test_webhook(self):
        p = N8nFDLParser()
        p.parse_trigger('TRIGGER webhook AS "Hook" { path: "/my-hook", method: POST }')
        node = p.nodes[0]
        assert node.type == "n8n-nodes-base.webhook"
        assert node.parameters["path"] == "/my-hook"
        assert node.parameters["httpMethod"] == "POST"

    def test_webhook_defaults(self):
        p = N8nFDLParser()
        p.parse_trigger('TRIGGER webhook AS "Hook"')
        node = p.nodes[0]
        assert node.parameters["path"] == "/webhook"
        assert node.parameters["httpMethod"] == "POST"

    def test_cron(self):
        p = N8nFDLParser()
        p.parse_trigger('TRIGGER cron AS "Schedule" { expression: "0 * * * *" }')
        node = p.nodes[0]
        assert node.type == "n8n-nodes-base.scheduleTrigger"
        assert node.parameters["rule"]["interval"][0]["expression"] == "0 * * * *"

    def test_gsheets_update(self):
        p = N8nFDLParser()
        p.parse_trigger('TRIGGER gsheets_update AS "Sheet Trigger" { doc: "https://docs.google.com/x", sheet: "Sheet1", event: rowUpdate, watch: ["trigger"], poll: everyMinute }')
        node = p.nodes[0]
        assert node.type == "n8n-nodes-base.googleSheetsTrigger"
        assert node.parameters["documentId"]["value"] == "https://docs.google.com/x"
        assert node.parameters["sheetName"]["value"] == "Sheet1"
        assert node.parameters["options"]["columnsToWatch"] == ["trigger"]

    def test_chat_trigger(self):
        p = N8nFDLParser()
        p.parse_trigger('TRIGGER chat AS "Chat" { public: true, title: "Bot", subtitle: "Ask me", initialMessages: "Hi!", responseMode: "lastNode" }')
        node = p.nodes[0]
        assert node.type == "@n8n/n8n-nodes-langchain.chatTrigger"
        assert node.parameters["public"] is True
        assert node.parameters["initialMessages"] == "Hi!"
        assert node.parameters["options"]["title"] == "Bot"
        assert node.parameters["options"]["subtitle"] == "Ask me"
        assert node.parameters["options"]["responseMode"] == "lastNode"


class TestParseSet:
    def test_basic(self):
        p = N8nFDLParser()
        p.parse_set('SET "Config" { apiUrl: "https://api.com", count: 5 }')
        node = p.nodes[0]
        assert node.name == "Config"
        assert node.type == "n8n-nodes-base.set"
        assignments = node.parameters["assignments"]["assignments"]
        assert len(assignments) == 2
        names = {a["name"] for a in assignments}
        assert "apiUrl" in names
        assert "count" in names

    def test_passthrough(self):
        p = N8nFDLParser()
        # +passthrough must appear in the prefix (before the block)
        p.parse_set('SET "Config" +passthrough { x: 1 }')
        node = p.nodes[0]
        assert node.parameters["includeOtherFields"] is True

    def test_expression_value(self):
        p = N8nFDLParser()
        p.parse_set('SET "Extract" { id: {{ $json.event.id }} }')
        node = p.nodes[0]
        assignments = node.parameters["assignments"]["assignments"]
        assert assignments[0]["value"] == "={{ $json.event.id }}"

    def test_assignment_types(self):
        p = N8nFDLParser()
        p.parse_set('SET "Types" { flag: true, count: 42, label: "x" }')
        assignments = {a["name"]: a for a in p.nodes[0].parameters["assignments"]["assignments"]}
        assert assignments["flag"]["type"] == "boolean"
        assert assignments["count"]["type"] == "number"
        assert assignments["label"]["type"] == "string"


class TestParseHttp:
    def test_simple_get(self):
        p = N8nFDLParser()
        p.parse_http('HTTP GET https://api.example.com/items @myauth AS "Get Items"')
        node = p.nodes[0]
        assert node.name == "Get Items"
        assert node.parameters["method"] == "GET"
        assert node.parameters["url"] == "https://api.example.com/items"

    def test_post_with_body(self):
        p = N8nFDLParser()
        p.parse_http('HTTP POST https://api.com/items @auth AS "Create" { body: { name: "test" } }')
        node = p.nodes[0]
        assert node.parameters["method"] == "POST"
        assert node.parameters["sendBody"] is True
        assert "bodyParameters" in node.parameters

    def test_json_body(self):
        p = N8nFDLParser()
        p.parse_http('HTTP POST https://api.com @auth AS "Post" { jsonBody: {{ JSON.stringify($json) }} }')
        node = p.nodes[0]
        assert node.parameters["specifyBody"] == "json"
        assert node.parameters["sendBody"] is True

    def test_query_params(self):
        p = N8nFDLParser()
        p.parse_http('HTTP GET https://api.com @auth AS "Search" { query: { page: 1 } }')
        node = p.nodes[0]
        assert node.parameters["sendQuery"] is True
        qp = node.parameters["queryParameters"]["parameters"]
        assert qp[0]["name"] == "page"

    def test_headers(self):
        p = N8nFDLParser()
        p.parse_http('HTTP GET https://api.com AS "Req" { headers: { "X-Custom": "value" } }')
        node = p.nodes[0]
        assert node.parameters["sendHeaders"] is True
        hp = node.parameters["headerParameters"]["parameters"]
        assert hp[0]["name"] == "X-Custom"
        assert hp[0]["value"] == "value"

    def test_onerror_continue(self):
        p = N8nFDLParser()
        p.parse_http('HTTP GET https://api.com @auth AS "Req" onError:continue')
        node = p.nodes[0]
        d = node.to_dict()
        assert d["onError"] == "continueErrorOutput"

    def test_no_credential(self):
        p = N8nFDLParser()
        p.parse_http('HTTP GET https://api.com AS "Req"')
        node = p.nodes[0]
        assert node.credentials == {}

    def test_expression_url(self):
        p = N8nFDLParser()
        p.parse_http('HTTP PUT {{ $json.upload_url }} AS "Upload"')
        node = p.nodes[0]
        assert node.parameters["url"] == "={{ $json.upload_url }}"

    def test_default_name(self):
        p = N8nFDLParser()
        p.parse_http("HTTP DELETE https://api.com")
        assert p.nodes[0].name == "HTTP DELETE"


class TestParseCode:
    def test_inline_single_backtick(self):
        p = N8nFDLParser()
        p.parse_code('CODE "Transform" `return $input.all();`')
        node = p.nodes[0]
        assert node.name == "Transform"
        assert node.parameters["jsCode"] == "return $input.all();"

    def test_multiline_triple_backtick(self):
        src = 'CODE "Complex" ```\nconst x = 1;\nreturn x;\n```'
        lines = tokenize_lines(src)
        p = N8nFDLParser()
        p.parse_code(lines[0])
        assert "const x = 1;" in p.nodes[0].parameters["jsCode"]

    def test_default_name(self):
        p = N8nFDLParser()
        p.parse_code("CODE `return 1;`")
        assert p.nodes[0].name == "Code"


class TestParseFilter:
    def test_basic(self):
        p = N8nFDLParser()
        p.parse_filter('FILTER "Active Only" { conditions: AND [{{ $json.status }} equals "active"] }')
        node = p.nodes[0]
        assert node.name == "Active Only"
        assert node.type == "n8n-nodes-base.filter"
        conds = node.parameters["conditions"]["conditions"]
        assert len(conds) == 1
        assert conds[0]["operator"]["operation"] == "equals"

    def test_multiple_conditions(self):
        p = N8nFDLParser()
        p.parse_filter('FILTER "F" { conditions: AND [{{ $json.a }} notEmpty, {{ $json.b }} exists] }')
        conds = p.nodes[0].parameters["conditions"]["conditions"]
        assert len(conds) == 2


class TestParseIf:
    def test_basic(self):
        p = N8nFDLParser()
        p.parse_if('IF "Is Admin?" { conditions: AND [{{ $json.role }} equals "admin"] }')
        node = p.nodes[0]
        assert node.name == "Is Admin?"
        assert node.type == "n8n-nodes-base.if"

    def test_or_combinator(self):
        p = N8nFDLParser()
        p.parse_if('IF "Check" { conditions: OR [{{ $json.x }} equals "a", {{ $json.y }} equals "b"] }')
        result = p.nodes[0].parameters
        assert result["conditions"]["combinator"] == "or"


class TestParseMerge:
    def test_combine_by_position(self):
        p = N8nFDLParser()
        p.parse_merge('MERGE "Combine All" { mode: combine, by: position, inputs: 3 }')
        node = p.nodes[0]
        assert node.name == "Combine All"
        assert node.type == "n8n-nodes-base.merge"
        assert node.parameters["mode"] == "combine"
        assert node.parameters["combineBy"] == "combineByPosition"
        assert node.parameters["numberInputs"] == 3

    def test_choose_branch(self):
        p = N8nFDLParser()
        p.parse_merge('MERGE "Pick" { mode: chooseBranch, useInput: 2 }')
        node = p.nodes[0]
        assert node.parameters["mode"] == "chooseBranch"
        assert node.parameters["useDataOfInput"] == 2

    def test_append(self):
        p = N8nFDLParser()
        p.parse_merge('MERGE "Append All" { mode: append }')
        assert p.nodes[0].parameters["mode"] == "append"


class TestParseGsheet:
    def test_read(self):
        p = N8nFDLParser()
        p.parse_gsheet('GSHEET READ @gsheets AS "Get Rows" { doc: "https://docs.google.com/x", sheet: "Sheet1" }')
        node = p.nodes[0]
        assert node.name == "Get Rows"
        assert node.type == "n8n-nodes-base.googleSheets"
        assert node.parameters["documentId"]["value"] == "https://docs.google.com/x"
        assert node.parameters["sheetName"]["value"] == "Sheet1"
        # READ maps to getAll which is the default, so no 'operation' key
        assert "operation" not in node.parameters

    def test_update_with_values(self):
        p = N8nFDLParser()
        p.parse_credential('CREDENTIAL @gs = googleSheetsOAuth2Api "Sheets"')
        p.parse_gsheet('GSHEET UPDATE @gs AS "Write" { doc: "https://x", sheet: "S1", match: ["ID"], values: { ID: {{ $json.ID }}, status: "done" } }')
        node = p.nodes[0]
        assert node.parameters["operation"] == "update"
        cols = node.parameters["columns"]
        assert cols["matchingColumns"] == ["ID"]
        assert "ID" in cols["value"]
        assert cols["value"]["status"] == "done"

    def test_default_credentials(self):
        p = N8nFDLParser()
        p.parse_gsheet('GSHEET READ AS "Get" { doc: "https://x", sheet: "S" }')
        node = p.nodes[0]
        assert "googleSheetsOAuth2Api" in node.credentials


class TestParseGdrive:
    def test_download(self):
        p = N8nFDLParser()
        p.parse_credential('CREDENTIAL @gd = googleDriveOAuth2Api "Drive"')
        p.parse_gdrive('GDRIVE DOWNLOAD @gd AS "Get File" { fileId: {{ $json.fileId }} }')
        node = p.nodes[0]
        assert node.name == "Get File"
        assert node.type == "n8n-nodes-base.googleDrive"
        assert node.parameters["operation"] == "download"
        assert node.parameters["fileId"]["value"] == "={{ $json.fileId }}"


class TestParseAgent:
    def test_basic(self):
        p = N8nFDLParser()
        p.parse_agent('AGENT "My Agent" { systemMessage: "You are helpful." }')
        node = p.nodes[0]
        assert node.name == "My Agent"
        assert node.type == "@n8n/n8n-nodes-langchain.agent"
        assert node.parameters["options"]["systemMessage"] == "You are helpful."

    def test_no_system_message(self):
        p = N8nFDLParser()
        p.parse_agent('AGENT "Simple Agent"')
        node = p.nodes[0]
        assert node.parameters == {"options": {}}


class TestParseLlm:
    def test_gemini(self):
        p = N8nFDLParser()
        p.parse_credential('CREDENTIAL @gem = googlePalmApi "Gemini Cred"')
        p.parse_llm('LLM gemini @gem AS "Gemini" { model: "models/gemini-2.5-flash", temperature: 0 }')
        node = p.nodes[0]
        assert node.type == "@n8n/n8n-nodes-langchain.lmChatGoogleGemini"
        assert node.parameters["modelName"] == "models/gemini-2.5-flash"
        assert node.parameters["options"]["temperature"] == 0

    def test_openai(self):
        p = N8nFDLParser()
        p.parse_llm('LLM openai AS "GPT" { model: "gpt-4.1-mini", temperature: 0 }')
        node = p.nodes[0]
        assert node.type == "@n8n/n8n-nodes-langchain.lmChatOpenAi"
        # OpenAI uses __rl model selector
        assert node.parameters["model"]["__rl"] is True
        assert node.parameters["model"]["value"] == "gpt-4.1-mini"

    def test_anthropic(self):
        p = N8nFDLParser()
        p.parse_llm('LLM anthropic AS "Claude" { model: "claude-sonnet-4-20250514" }')
        node = p.nodes[0]
        assert node.type == "@n8n/n8n-nodes-langchain.lmChatAnthropic"

    def test_disabled(self):
        p = N8nFDLParser()
        p.parse_llm('LLM openai AS "Backup" disabled { model: "gpt-4.1-mini" }')
        node = p.nodes[0]
        d = node.to_dict()
        assert d["disabled"] is True

    def test_default_credentials_when_no_alias(self):
        p = N8nFDLParser()
        p.parse_llm('LLM openai AS "GPT" { model: "gpt-4" }')
        node = p.nodes[0]
        assert "openAiApi" in node.credentials


class TestParseMemory:
    def test_buffer(self):
        p = N8nFDLParser()
        p.parse_memory('MEMORY buffer AS "Chat Memory" { contextWindowLength: 30 }')
        node = p.nodes[0]
        assert node.name == "Chat Memory"
        assert node.type == "@n8n/n8n-nodes-langchain.memoryBufferWindow"
        assert node.parameters["contextWindowLength"] == 30

    def test_default_type(self):
        p = N8nFDLParser()
        p.parse_memory('MEMORY AS "Mem"')
        # defaults to buffer when the second part after MEMORY is "AS"
        # Actually this would make mem_type = "as" which is not in map → defaults to buffer
        node = p.nodes[0]
        assert node.type == "@n8n/n8n-nodes-langchain.memoryBufferWindow"


class TestParseTool:
    def test_http_tool(self):
        p = N8nFDLParser()
        p.parse_tool('TOOL http AS "get_data" { url: "https://api.com", description: "Gets data.", optimizeResponse: true, fields: "data" }')
        node = p.nodes[0]
        assert node.name == "get_data"
        assert node.type == "n8n-nodes-base.httpRequestTool"
        assert node.parameters["url"] == "https://api.com"
        assert node.parameters["toolDescription"] == "Gets data."
        assert node.parameters["optimizeResponse"] is True
        assert node.parameters["fields"] == "data"
        assert node.parameters["fieldsToInclude"] == "selected"

    def test_wikipedia_tool(self):
        p = N8nFDLParser()
        p.parse_tool('TOOL wikipedia AS "wikipedia"')
        node = p.nodes[0]
        assert node.type == "@n8n/n8n-nodes-langchain.toolWikipedia"

    def test_code_tool(self):
        p = N8nFDLParser()
        p.parse_tool("""TOOL code AS "calc" { description: "Calculates.", schemaExample: '{"x": 1}', code: ```\nreturn 42;\n``` }""")
        node = p.nodes[0]
        assert node.type == "@n8n/n8n-nodes-langchain.toolCode"
        assert node.parameters["description"] == "Calculates."
        assert node.parameters["specifyInputSchema"] is True

    def test_datetime_tool(self):
        p = N8nFDLParser()
        p.parse_tool('TOOL datetime AS "days" { operation: "getTimeBetweenDates", description: "Days between dates." }')
        node = p.nodes[0]
        assert node.type == "n8n-nodes-base.dateTimeTool"
        assert node.parameters["operation"] == "getTimeBetweenDates"
        assert node.parameters["descriptionType"] == "manual"

    def test_crypto_tool(self):
        p = N8nFDLParser()
        p.parse_tool('TOOL crypto AS "make_password" { action: "generate", description: "Generate password.", encodingType: "base64" }')
        node = p.nodes[0]
        assert node.type == "n8n-nodes-base.cryptoTool"
        assert node.parameters["action"] == "generate"
        assert node.parameters["encodingType"] == "base64"
        assert node.parameters["dataPropertyName"] == "make_password"

    def test_rss_tool(self):
        p = N8nFDLParser()
        p.parse_tool('TOOL rss AS "feed" { url: "https://example.com/rss", description: "Blog posts." }')
        node = p.nodes[0]
        assert node.type == "n8n-nodes-base.rssFeedReadTool"


class TestParseNoop:
    def test_basic(self):
        p = N8nFDLParser()
        p.parse_noop('NOOP "Forward Data"')
        assert p.nodes[0].name == "Forward Data"
        assert p.nodes[0].type == "n8n-nodes-base.noOp"

    def test_default_name(self):
        p = N8nFDLParser()
        p.parse_noop("NOOP")
        assert p.nodes[0].name == "No Operation"


class TestParseNote:
    def test_basic(self):
        p = N8nFDLParser()
        p.parse_note('NOTE "Reminder" { content: "Check this section", color: 4 }')
        node = p.nodes[0]
        assert node.name == "Reminder"
        assert node.type == "n8n-nodes-base.stickyNote"
        assert node.parameters["content"] == "Check this section"
        assert node.parameters["color"] == 4


class TestParsePosition:
    def test_basic(self):
        p = N8nFDLParser()
        p.parse_position('POSITION "My Node" (100, 200)')
        assert p.positions["My Node"] == [100, 200]

    def test_negative_coords(self):
        p = N8nFDLParser()
        p.parse_position('POSITION "Node" (-50, -100)')
        assert p.positions["Node"] == [-50, -100]


# =========================================================================
# Connection parsing
# =========================================================================

class TestParseConnectionLine:
    def test_simple(self):
        p = N8nFDLParser()
        p.parse_connection_line('"A" -> "B"')
        assert len(p.connections) == 1
        assert p.connections[0].source == "A"
        assert p.connections[0].target == "B"

    def test_chain(self):
        p = N8nFDLParser()
        p.parse_connection_line('"A" -> "B" -> "C"')
        assert len(p.connections) == 2
        assert p.connections[0].source == "A"
        assert p.connections[0].target == "B"
        assert p.connections[1].source == "B"
        assert p.connections[1].target == "C"

    def test_true_branch(self):
        p = N8nFDLParser()
        p.parse_connection_line('"Check" -> TRUE -> "Yes"')
        assert len(p.connections) == 1
        assert p.connections[0].source == "Check"
        assert p.connections[0].target == "Yes"
        assert p.connections[0].source_output == 0

    def test_false_branch(self):
        p = N8nFDLParser()
        p.parse_connection_line('"Check" -> FALSE -> "No"')
        assert len(p.connections) == 1
        assert p.connections[0].source == "Check"
        assert p.connections[0].target == "No"
        assert p.connections[0].source_output == 1

    def test_ok_branch(self):
        p = N8nFDLParser()
        p.parse_connection_line('"API" -> OK -> "Handle"')
        assert p.connections[0].source_output == 0

    def test_err_branch(self):
        p = N8nFDLParser()
        p.parse_connection_line('"API" -> ERR -> "Error"')
        assert p.connections[0].source_output == 1

    def test_fan_out(self):
        p = N8nFDLParser()
        p.parse_connection_line('"Source" -> "A", "B", "C"')
        assert len(p.connections) == 3
        targets = {c.target for c in p.connections}
        assert targets == {"A", "B", "C"}

    def test_target_input_slot(self):
        p = N8nFDLParser()
        p.parse_connection_line('"A" -> "Merge":2')
        assert p.connections[0].target == "Merge"
        assert p.connections[0].target_input == 2

    def test_ai_llm_connection(self):
        p = N8nFDLParser()
        p.parse_connection_line('"Gemini" -> LLM -> "Agent"')
        assert len(p.connections) == 1
        assert p.connections[0].source == "Gemini"
        assert p.connections[0].target == "Agent"
        assert p.connections[0].connection_type == "ai_languageModel"

    def test_ai_tool_connection(self):
        p = N8nFDLParser()
        p.parse_connection_line('"get_joke" -> TOOL -> "Agent"')
        assert p.connections[0].connection_type == "ai_tool"

    def test_ai_memory_connection(self):
        p = N8nFDLParser()
        p.parse_connection_line('"Memory" -> MEMORY -> "Agent"')
        assert p.connections[0].connection_type == "ai_memory"

    def test_true_to_multiple_targets(self):
        p = N8nFDLParser()
        p.parse_connection_line('"If" -> TRUE -> "A":1, "B"')
        assert len(p.connections) == 2
        c0 = p.connections[0]
        assert c0.source == "If"
        assert c0.target == "A"
        assert c0.target_input == 1
        assert c0.source_output == 0
        c1 = p.connections[1]
        assert c1.target == "B"
        assert c1.source_output == 0


# =========================================================================
# Unique name deduplication
# =========================================================================

class TestUniqueName:
    def test_no_conflict(self):
        p = N8nFDLParser()
        assert p._unique_name("A") == "A"

    def test_conflict(self):
        p = N8nFDLParser()
        p._unique_name("A")
        assert p._unique_name("A") == "A 1"

    def test_multiple_conflicts(self):
        p = N8nFDLParser()
        p._unique_name("A")
        p._unique_name("A")
        assert p._unique_name("A") == "A 2"


# =========================================================================
# Credential resolution
# =========================================================================

class TestResolveCredential:
    def test_known_credential(self):
        p = N8nFDLParser()
        p.credentials["myapi"] = {"type": "httpHeaderAuth", "name": "My API Key", "id": "abc"}
        result = p._resolve_credential("@myapi")
        assert "httpHeaderAuth" in result
        assert result["httpHeaderAuth"]["name"] == "My API Key"

    def test_unknown_credential_fallback(self):
        p = N8nFDLParser()
        result = p._resolve_credential("@unknown")
        assert "httpHeaderAuth" in result
        assert result["httpHeaderAuth"]["name"] == "unknown"


# =========================================================================
# Full parse (integration)
# =========================================================================

class TestFullParse:
    def test_minimal_workflow(self):
        src = '''
WORKFLOW "Test" active
TRIGGER manual AS "Start"
SET "Config" { x: 1 }
"Start" -> "Config"
'''
        p = N8nFDLParser()
        result = p.parse(src)
        assert result["name"] == "Test"
        assert result["active"] is True
        assert len(result["nodes"]) == 2
        assert "Start" in result["connections"]
        conns = result["connections"]["Start"]["main"][0]
        assert conns[0]["node"] == "Config"

    def test_if_branching(self):
        src = '''
WORKFLOW "Branch Test"
IF "Check" { conditions: AND [{{ $json.x }} equals "yes"] }
SET "Yes" { result: "true" }
SET "No" { result: "false" }
"Check" -> TRUE -> "Yes"
"Check" -> FALSE -> "No"
'''
        p = N8nFDLParser()
        result = p.parse(src)
        conns = result["connections"]["Check"]["main"]
        # output 0 -> Yes
        assert conns[0][0]["node"] == "Yes"
        # output 1 -> No
        assert conns[1][0]["node"] == "No"

    def test_merge_with_input_slots(self):
        src = '''
WORKFLOW "Merge Test"
SET "A" { x: 1 }
SET "B" { y: 2 }
MERGE "Combine" { mode: combine, by: position, inputs: 2 }
"A" -> "Combine":0
"B" -> "Combine":1
'''
        p = N8nFDLParser()
        result = p.parse(src)
        a_conns = result["connections"]["A"]["main"][0]
        assert a_conns[0]["index"] == 0
        b_conns = result["connections"]["B"]["main"][0]
        assert b_conns[0]["index"] == 1

    def test_ai_agent_wiring(self):
        src = '''
WORKFLOW "AI Test"
TRIGGER chat AS "Chat"
AGENT "Bot" { systemMessage: "You help." }
LLM gemini AS "Gemini" { model: "models/gemini-2.5-flash" }
MEMORY buffer AS "Mem" { contextWindowLength: 10 }
TOOL wikipedia AS "wiki"

"Chat" -> "Bot"
"Gemini" -> LLM -> "Bot"
"Mem" -> MEMORY -> "Bot"
"wiki" -> TOOL -> "Bot"
'''
        p = N8nFDLParser()
        result = p.parse(src)
        conns = result["connections"]

        # Main connection
        assert conns["Chat"]["main"][0][0]["node"] == "Bot"

        # AI connections
        assert conns["Gemini"]["ai_languageModel"][0][0]["node"] == "Bot"
        assert conns["Mem"]["ai_memory"][0][0]["node"] == "Bot"
        assert conns["wiki"]["ai_tool"][0][0]["node"] == "Bot"

    def test_onerror_routing(self):
        src = '''
WORKFLOW "Error Test"
HTTP GET https://api.com AS "API" onError:continue
SET "OK" { status: "ok" }
SET "Fail" { status: "fail" }
"API" -> OK -> "OK"
"API" -> ERR -> "Fail"
'''
        p = N8nFDLParser()
        result = p.parse(src)
        api_node = next(n for n in result["nodes"] if n["name"] == "API")
        assert api_node["onError"] == "continueErrorOutput"
        conns = result["connections"]["API"]["main"]
        assert conns[0][0]["node"] == "OK"
        assert conns[1][0]["node"] == "Fail"

    def test_fan_out(self):
        src = '''
WORKFLOW "Fan Test"
SET "Source" { x: 1 }
SET "A" { a: 1 }
SET "B" { b: 1 }
SET "C" { c: 1 }
"Source" -> "A", "B", "C"
'''
        p = N8nFDLParser()
        result = p.parse(src)
        conns = result["connections"]["Source"]["main"][0]
        targets = {c["node"] for c in conns}
        assert targets == {"A", "B", "C"}

    def test_workflow_output_structure(self):
        src = 'WORKFLOW "Structure Test" active'
        p = N8nFDLParser()
        result = p.parse(src)
        assert "name" in result
        assert "nodes" in result
        assert "connections" in result
        assert "active" in result
        assert "settings" in result
        assert "versionId" in result
        assert "meta" in result
        assert "id" in result
        assert "tags" in result
        assert "pinData" in result
        assert result["settings"]["executionOrder"] == "v1"

    def test_disabled_node(self):
        src = '''
WORKFLOW "Disabled"
LLM openai AS "Backup" disabled { model: "gpt-4" }
'''
        p = N8nFDLParser()
        result = p.parse(src)
        node = result["nodes"][0]
        assert node["disabled"] is True


# =========================================================================
# Unified options: { ... } support across node types
# =========================================================================

class TestUnifiedOptions:
    """Test that options: { ... } in DSL blocks merges into params['options']."""

    def test_http_options(self):
        p = N8nFDLParser()
        p.parse_http(
            'HTTP POST https://api.com AS "Req" { '
            'body: { x: 1 }, options: { timeout: 10000, proxy: "http://p:3821" } }'
        )
        opts = p.nodes[0].parameters['options']
        assert opts['timeout'] == 10000
        assert opts['proxy'] == 'http://p:3821'

    def test_http_options_with_nested_values(self):
        p = N8nFDLParser()
        p.parse_http(
            'HTTP GET https://api.com AS "Req" { options: { allowUnauthorizedCerts: true } }'
        )
        assert p.nodes[0].parameters['options']['allowUnauthorizedCerts'] is True

    def test_set_options(self):
        p = N8nFDLParser()
        p.parse_set('SET "Config" { apiUrl: "https://x.com", options: { dotNotation: true } }')
        node = p.nodes[0]
        assert node.parameters['options']['dotNotation'] is True
        names = [a['name'] for a in node.parameters['assignments']['assignments']]
        assert 'options' not in names
        assert 'apiUrl' in names

    def test_gsheet_options(self):
        p = N8nFDLParser()
        p.parse_gsheet(
            'GSHEET READ @gs AS "Rows" { '
            'doc: "https://docs.google.com/x", sheet: "Sheet1", '
            'options: { locale: "en", autoRecalc: "ON_CHANGE" } }'
        )
        opts = p.nodes[0].parameters['options']
        assert opts['locale'] == 'en'
        assert opts['autoRecalc'] == 'ON_CHANGE'

    def test_gdrive_options(self):
        p = N8nFDLParser()
        p.parse_gdrive('GDRIVE DOWNLOAD AS "File" { fileId: "abc", options: { fileName: "out.pdf" } }')
        assert p.nodes[0].parameters['options']['fileName'] == 'out.pdf'

    def test_merge_options(self):
        p = N8nFDLParser()
        p.parse_merge('MERGE "Combine" { mode: append, options: { fuzzy: true } }')
        assert p.nodes[0].parameters['options']['fuzzy'] is True

    def test_filter_options(self):
        p = N8nFDLParser()
        p.parse_filter(
            'FILTER "Active" { conditions: AND [{{ $json.x }} notEmpty], '
            'options: { looseTypeValidation: true } }'
        )
        assert p.nodes[0].parameters['options']['looseTypeValidation'] is True

    def test_if_options(self):
        p = N8nFDLParser()
        p.parse_if(
            'IF "Check" { conditions: AND [{{ $json.x }} equals "y"], '
            'options: { looseTypeValidation: true } }'
        )
        assert p.nodes[0].parameters['options']['looseTypeValidation'] is True

    def test_agent_options_merge_with_system_message(self):
        p = N8nFDLParser()
        p.parse_agent(
            'AGENT "Bot" { systemMessage: "You are helpful", '
            'options: { maxIterations: 10 } }'
        )
        opts = p.nodes[0].parameters['options']
        assert opts['systemMessage'] == 'You are helpful'
        assert opts['maxIterations'] == 10

    def test_llm_options_merge_with_temperature(self):
        p = N8nFDLParser()
        p.parse_llm(
            'LLM openai AS "GPT" { model: "gpt-4", temperature: 0.5, '
            'options: { topP: 0.9 } }'
        )
        opts = p.nodes[0].parameters['options']
        assert opts['temperature'] == 0.5
        assert opts['topP'] == 0.9

    def test_memory_options(self):
        p = N8nFDLParser()
        p.parse_memory(
            'MEMORY buffer AS "Mem" { contextWindowLength: 20, options: { sessionKey: "chat_id" } }'
        )
        assert p.nodes[0].parameters['options']['sessionKey'] == 'chat_id'

    def test_tool_http_options(self):
        p = N8nFDLParser()
        p.parse_tool(
            'TOOL http AS "api_call" { url: "https://api.com", '
            'description: "Call API", options: { timeout: 5000 } }'
        )
        assert p.nodes[0].parameters['options']['timeout'] == 5000

    def test_trigger_webhook_options(self):
        p = N8nFDLParser()
        p.parse_trigger(
            'TRIGGER webhook AS "Hook" { path: "/hook", method: POST, '
            'options: { rawBody: true } }'
        )
        assert p.nodes[0].parameters['options']['rawBody'] is True

    def test_no_options_unchanged(self):
        """Nodes without options: block should still have empty options."""
        p = N8nFDLParser()
        p.parse_http('HTTP GET https://api.com AS "Req"')
        assert p.nodes[0].parameters['options'] == {}

    def test_options_explicit_overrides_shorthand(self):
        """Explicit options: { temperature: X } overrides shorthand temperature: Y."""
        p = N8nFDLParser()
        p.parse_llm(
            'LLM openai AS "GPT" { model: "gpt-4", temperature: 0.5, '
            'options: { temperature: 0.9 } }'
        )
        assert p.nodes[0].parameters['options']['temperature'] == 0.9


# =========================================================================
# Integration: parse real .nflow files
# =========================================================================

class TestIntegrationAgentFile:
    """Test parsing the test_agent.nflow file end-to-end."""

    @pytest.fixture
    def parsed(self):
        with open(EXAMPLES_DIR / "agent.nflow") as f:
            src = f.read()
        return N8nFDLParser().parse(src)

    def test_workflow_name(self, parsed):
        assert parsed["name"] == "Your First AI Agent"
        assert parsed["active"] is True

    def test_node_count(self, parsed):
        # chat trigger + agent + 2 LLMs + memory + 6 tools = 11
        assert len(parsed["nodes"]) == 11

    def test_has_chat_trigger(self, parsed):
        triggers = [n for n in parsed["nodes"] if "chatTrigger" in n["type"]]
        assert len(triggers) == 1
        assert triggers[0]["name"] == "Example Chat Window"

    def test_has_agent(self, parsed):
        agents = [n for n in parsed["nodes"] if "agent" in n["type"]]
        assert len(agents) == 1
        assert agents[0]["name"] == "Your First AI Agent"

    def test_has_llms(self, parsed):
        llms = [n for n in parsed["nodes"] if "lmChat" in n["type"]]
        assert len(llms) == 2  # Gemini + OpenAI

    def test_openai_disabled(self, parsed):
        openai = next(n for n in parsed["nodes"] if n["name"] == "OpenAI")
        assert openai.get("disabled") is True

    def test_has_tools(self, parsed):
        tool_types = {"httpRequestTool", "toolWikipedia", "toolCode",
                      "dateTimeTool", "cryptoTool", "rssFeedReadTool"}
        node_types = {n["type"].split(".")[-1] for n in parsed["nodes"]}
        for t in tool_types:
            assert t in node_types, f"Missing tool type: {t}"

    def test_main_connection(self, parsed):
        conns = parsed["connections"]
        assert "Example Chat Window" in conns
        assert conns["Example Chat Window"]["main"][0][0]["node"] == "Your First AI Agent"

    def test_ai_connections(self, parsed):
        conns = parsed["connections"]
        assert "Gemini" in conns
        assert "ai_languageModel" in conns["Gemini"]
        assert conns["Gemini"]["ai_languageModel"][0][0]["node"] == "Your First AI Agent"

        assert "Simple Memory" in conns
        assert "ai_memory" in conns["Simple Memory"]

        # All tools should connect to the agent via ai_tool
        tool_names = ["get_a_joke", "days_from_now", "wikipedia",
                      "create_password", "calculate_loan_payment", "n8n_blog_rss_feed"]
        for name in tool_names:
            assert name in conns, f"Missing connection for tool: {name}"
            assert "ai_tool" in conns[name]


