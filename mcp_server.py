# mcp_server.py
# MCP server endpoint for DasModel.
# Exposes conversation, correction, rule, and prompt management
# as MCP tools over JSON-RPC, so external agents (e.g. Claude Desktop)
# can drive and evaluate the local model programmatically.

import json
from typing import Dict, List, Any

import db
import agent
import config


# --- Tool definitions (MCP inputSchema format) ---

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "chat",
        "description": (
            "Send a message to the local model and get a response. "
            "Creates a new conversation if conversation_id is not provided. "
            "Returns the model's response text, plus any tool calls made."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "The message to send to the model.",
                },
                "conversation_id": {
                    "type": "integer",
                    "description": "Existing conversation ID. Omit to create a new one.",
                },
                "user_id": {
                    "type": "string",
                    "description": "User identity for the conversation. Default: configured default.",
                },
            },
            "required": ["message"],
        },
    },
    {
        "name": "conversation_create",
        "description": "Start a new conversation. Returns the conversation ID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "string",
                    "description": "User identity. Default: configured default.",
                },
            },
        },
    },
    {
        "name": "conversation_get",
        "description": "Get a conversation and its turns by ID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "conversation_id": {
                    "type": "integer",
                    "description": "The conversation ID.",
                },
            },
            "required": ["conversation_id"],
        },
    },
    {
        "name": "conversation_list",
        "description": "List recent conversations with turn counts.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "string",
                    "description": "Filter by user. Omit for all users.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results. Default: 20.",
                },
            },
        },
    },
    {
        "name": "conversation_close",
        "description": "Close a conversation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "conversation_id": {
                    "type": "integer",
                    "description": "The conversation ID to close.",
                },
            },
            "required": ["conversation_id"],
        },
    },
    {
        "name": "correction_add",
        "description": (
            "Submit a correction on a model response turn. "
            "The correction is stored as an exemplar and injected into "
            "future system prompts to improve model behaviour."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "turn_id": {
                    "type": "integer",
                    "description": "The turn ID of the assistant response to correct.",
                },
                "correction": {
                    "type": "string",
                    "description": "The better response the model should have given.",
                },
                "reason": {
                    "type": "string",
                    "description": "Why the original response was wrong.",
                },
                "corrected_by": {
                    "type": "string",
                    "description": "Who submitted the correction. Default: 'user'.",
                },
            },
            "required": ["turn_id", "correction"],
        },
    },
    {
        "name": "rules_list",
        "description": "List all rules (active and inactive) with their priorities and categories.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "rule_add",
        "description": "Add a new rule to be injected into the system prompt.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "rule_text": {
                    "type": "string",
                    "description": "The rule text.",
                },
                "category": {
                    "type": "string",
                    "description": "Optional category for grouping.",
                },
                "priority": {
                    "type": "integer",
                    "description": "Priority (higher = rendered first). Default: 0.",
                },
            },
            "required": ["rule_text"],
        },
    },
    {
        "name": "rule_update",
        "description": "Update an existing rule.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "rule_id": {
                    "type": "integer",
                    "description": "The rule ID to update.",
                },
                "rule_text": {"type": "string"},
                "category": {"type": "string"},
                "priority": {"type": "integer"},
                "active": {
                    "type": "boolean",
                    "description": "Set active/inactive.",
                },
            },
            "required": ["rule_id"],
        },
    },
    {
        "name": "rule_delete",
        "description": "Delete a rule.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "rule_id": {
                    "type": "integer",
                    "description": "The rule ID to delete.",
                },
            },
            "required": ["rule_id"],
        },
    },
    {
        "name": "rule_toggle",
        "description": "Toggle a rule's active status.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "rule_id": {
                    "type": "integer",
                    "description": "The rule ID to toggle.",
                },
            },
            "required": ["rule_id"],
        },
    },
    {
        "name": "prompts_list",
        "description": "List all prompt templates.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "prompt_get",
        "description": "Get a prompt template by ID. Default ID 1 is the system prompt.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt_id": {
                    "type": "integer",
                    "description": "Prompt ID. Default: 1 (system prompt).",
                },
            },
        },
    },
    {
        "name": "prompt_update",
        "description": "Update a prompt template.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt_id": {
                    "type": "integer",
                    "description": "The prompt ID to update.",
                },
                "template": {"type": "string", "description": "Jinja2 template text."},
                "name": {"type": "string"},
                "description": {"type": "string"},
                "active": {"type": "boolean"},
            },
            "required": ["prompt_id"],
        },
    },
]

# Build lookup by name
_TOOL_MAP = {t["name"]: t for t in TOOLS}


# --- Tool handlers ---

def _handle_chat(args: Dict) -> str:
    message = args.get("message", "").strip()
    if not message:
        return json.dumps({"error": "message required"})

    user_id = args.get("user_id", config.DEFAULT_USER)
    conv_id = args.get("conversation_id")

    if not conv_id:
        conv_id = db.create_conversation(user_id)

    events = []
    for event in agent.run_agent_loop(conv_id, message, user_id):
        events.append(event)

    response_text = ""
    tool_calls = []
    for e in events:
        if e["type"] == "response":
            response_text = e["content"]
        elif e["type"] == "tool_call":
            tool_calls.append({"name": e["name"], "arguments": e["arguments"]})
        elif e["type"] == "error":
            return json.dumps({"error": e["content"], "conversation_id": conv_id})

    result = {
        "conversation_id": conv_id,
        "response": response_text,
    }
    if tool_calls:
        result["tool_calls"] = tool_calls
    return json.dumps(result)


def _handle_conversation_create(args: Dict) -> str:
    user_id = args.get("user_id", config.DEFAULT_USER)
    conv_id = db.create_conversation(user_id)
    return json.dumps({"conversation_id": conv_id})


def _handle_conversation_get(args: Dict) -> str:
    conv_id = args.get("conversation_id")
    if not conv_id:
        return json.dumps({"error": "conversation_id required"})
    conv = db.get_conversation(conv_id)
    if not conv:
        return json.dumps({"error": "Conversation not found"})
    turns = db.get_conversation_turns(conv_id)
    return json.dumps({"conversation": conv, "turns": turns})


def _handle_conversation_list(args: Dict) -> str:
    user_id = args.get("user_id")
    limit = args.get("limit", 20)
    convs = db.get_recent_conversations(user_id, limit)
    return json.dumps({"conversations": convs})


def _handle_conversation_close(args: Dict) -> str:
    conv_id = args.get("conversation_id")
    if not conv_id:
        return json.dumps({"error": "conversation_id required"})
    db.close_conversation(conv_id)
    return json.dumps({"status": "closed", "conversation_id": conv_id})


def _handle_correction_add(args: Dict) -> str:
    turn_id = args.get("turn_id")
    correction = args.get("correction", "").strip()
    if not turn_id or not correction:
        return json.dumps({"error": "turn_id and correction required"})
    reason = args.get("reason", "")
    corrected_by = args.get("corrected_by", "user")
    corr_id = db.add_correction(turn_id, correction, reason, corrected_by)
    return json.dumps({"correction_id": corr_id})


def _handle_rules_list(_args: Dict) -> str:
    return json.dumps({"rules": db.get_all_rules()})


def _handle_rule_add(args: Dict) -> str:
    rule_text = args.get("rule_text", "").strip()
    if not rule_text:
        return json.dumps({"error": "rule_text required"})
    category = args.get("category", "")
    priority = args.get("priority", 0)
    rule_id = db.add_rule(rule_text, category or None, priority)
    return json.dumps({"rule_id": rule_id})


def _handle_rule_update(args: Dict) -> str:
    rule_id = args.get("rule_id")
    if not rule_id:
        return json.dumps({"error": "rule_id required"})
    success = db.update_rule(
        rule_id,
        args.get("rule_text"),
        args.get("category"),
        args.get("priority"),
        args.get("active"),
    )
    if not success:
        return json.dumps({"error": "Rule not found or no changes"})
    return json.dumps({"status": "updated"})


def _handle_rule_delete(args: Dict) -> str:
    rule_id = args.get("rule_id")
    if not rule_id:
        return json.dumps({"error": "rule_id required"})
    if not db.delete_rule(rule_id):
        return json.dumps({"error": "Rule not found"})
    return json.dumps({"status": "deleted"})


def _handle_rule_toggle(args: Dict) -> str:
    rule_id = args.get("rule_id")
    if not rule_id:
        return json.dumps({"error": "rule_id required"})
    new_status = db.toggle_rule_active(rule_id)
    if new_status is None:
        return json.dumps({"error": "Rule not found"})
    return json.dumps({"active": new_status})


def _handle_prompts_list(_args: Dict) -> str:
    return json.dumps({"prompts": db.get_all_prompts()})


def _handle_prompt_get(args: Dict) -> str:
    prompt_id = args.get("prompt_id", 1)
    prompt = db.get_prompt(prompt_id)
    if not prompt:
        return json.dumps({"error": "Prompt not found"})
    return json.dumps({"prompt": prompt})


def _handle_prompt_update(args: Dict) -> str:
    prompt_id = args.get("prompt_id")
    if not prompt_id:
        return json.dumps({"error": "prompt_id required"})
    success = db.update_prompt(
        prompt_id,
        args.get("template"),
        args.get("name"),
        args.get("description"),
        args.get("active"),
    )
    if not success:
        return json.dumps({"error": "Prompt not found or no changes"})
    return json.dumps({"status": "updated"})


_HANDLERS = {
    "chat": _handle_chat,
    "conversation_create": _handle_conversation_create,
    "conversation_get": _handle_conversation_get,
    "conversation_list": _handle_conversation_list,
    "conversation_close": _handle_conversation_close,
    "correction_add": _handle_correction_add,
    "rules_list": _handle_rules_list,
    "rule_add": _handle_rule_add,
    "rule_update": _handle_rule_update,
    "rule_delete": _handle_rule_delete,
    "rule_toggle": _handle_rule_toggle,
    "prompts_list": _handle_prompts_list,
    "prompt_get": _handle_prompt_get,
    "prompt_update": _handle_prompt_update,
}


# --- JSON-RPC dispatch ---

def handle_jsonrpc(request_body: Dict) -> Dict:
    """Process a JSON-RPC request and return a response."""
    req_id = request_body.get("id")
    method = request_body.get("method", "")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {
                    "name": "dasmodel",
                    "version": "0.1.0",
                },
                "capabilities": {"tools": {}},
            },
        }

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": TOOLS},
        }

    if method == "tools/call":
        params = request_body.get("params", {})
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        handler = _HANDLERS.get(tool_name)
        if not handler:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32601,
                    "message": f"Tool not found: {tool_name}",
                },
            }

        try:
            result_text = handler(arguments)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": result_text}],
                },
            }
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32000, "message": str(e)},
            }

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {
            "code": -32601,
            "message": f"Method not supported: {method}",
        },
    }
