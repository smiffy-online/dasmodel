"""
Agent loop for DasModel.

Handles LLM calls via Ollama (or any OpenAI-compatible API)
and tool execution via MCP. Supports multiple MCP servers,
streaming and synchronous modes.
"""

import json
import httpx
from datetime import datetime, timezone
from typing import List, Dict, Generator

from jinja2 import Template, TemplateError

import config
import db
import builtin_tools


def get_user_context(user_id: str) -> Dict[str, str]:
    """User context for prompt templating."""
    return {"name": user_id}


# --- System prompt ---

def build_system_prompt(user_id: str, tools: List[Dict] = None) -> str:
    """Build system prompt from the Jinja2 template in the database."""
    prompt_row = db.get_prompt(1)

    if not prompt_row:
        return f"You are a helpful AI assistant for {user_id}."

    try:
        template = Template(prompt_row["template"])

        now = datetime.now()
        time_context = {
            "local": now.strftime("%H:%M"),
            "timezone": "UTC",
            "date": now.strftime("%A, %d %B %Y"),
            "utc": datetime.now(timezone.utc).strftime("%H:%M UTC"),
        }

        rules = db.get_active_rules()
        exemplars = db.search_exemplars("", user_id, limit=5)

        tool_list = []
        if tools:
            for t in tools:
                func = t.get("function", {})
                tool_list.append({
                    "name": func.get("name", "unknown"),
                    "description": func.get("description", ""),
                })

        return template.render(
            time=time_context,
            user=get_user_context(user_id),
            rules=rules,
            exemplars=exemplars,
            tools=tool_list,
        )

    except TemplateError as e:
        print(f"Template error: {e}")
        return f"You are a helpful AI assistant for {user_id}."


# --- MCP tool integration ---

# Maps tool_name -> MCP server URL (populated by get_available_tools)
_tool_server_map: Dict[str, str] = {}


def get_available_tools() -> List[Dict]:
    """
    Fetch tools from all configured MCP servers and merge with built-in tools.
    Returns tools in Ollama/OpenAI function-calling format.
    Builds an internal map of tool_name -> server_url for MCP routing.
    """
    _tool_server_map.clear()
    all_tools = list(builtin_tools.get_builtin_tools())

    for server in config.MCP_SERVERS:
        server_url = server.get("url", "")
        server_name = server.get("name", server_url)

        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.post(
                    server_url,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/list",
                    },
                )
                result = response.json()

                if "error" in result:
                    print(f"MCP error ({server_name}): {result['error']}")
                    continue

                mcp_tools = result.get("result", {}).get("tools", [])

                for tool in mcp_tools:
                    tool_name = tool["name"]
                    _tool_server_map[tool_name] = server_url
                    all_tools.append({
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "description": tool.get("description", ""),
                            "parameters": tool.get("inputSchema", {
                                "type": "object",
                                "properties": {},
                            }),
                        },
                    })

                print(f"MCP ({server_name}): {len(mcp_tools)} tools loaded")

        except Exception as e:
            print(f"MCP unavailable ({server_name}): {e}")

    return all_tools


def call_tool(tool_name: str, arguments: Dict) -> str:
    """Execute a tool — built-in first, then MCP."""
    # Built-in tools take priority
    if builtin_tools.is_builtin(tool_name):
        return builtin_tools.call_builtin(tool_name, arguments)

    # MCP tools
    server_url = _tool_server_map.get(tool_name)
    if not server_url:
        return f"Tool not found: {tool_name}"

    try:
        with httpx.Client(timeout=60.0) as client:
            response = client.post(
                server_url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": tool_name,
                        "arguments": arguments,
                    },
                },
            )
            result = response.json()

            if "error" in result:
                return f"Tool error: {result['error']}"

            content = result.get("result", {}).get("content", [])
            if content:
                texts = [
                    c.get("text", str(c))
                    for c in content
                    if c.get("type") == "text"
                ]
                return "\n".join(texts) if texts else json.dumps(content)

            return json.dumps(result.get("result", {}))

    except Exception as e:
        return f"Tool execution failed: {e}"


# --- Ollama ---

def call_ollama(
    messages: List[Dict],
    tools: List[Dict] = None,
    stream: bool = False,
) -> Dict:
    """Call Ollama with messages and optional tools."""
    payload = {
        "model": config.OLLAMA_MODEL,
        "messages": messages,
        "stream": stream,
    }

    if tools:
        payload["tools"] = tools

    headers = {"Content-Type": "application/json"}
    if config.OLLAMA_API_KEY:
        headers["Authorization"] = f"Bearer {config.OLLAMA_API_KEY}"

    with httpx.Client(timeout=120.0) as client:
        response = client.post(
            f"{config.OLLAMA_URL}/api/chat",
            json=payload,
            headers=headers,
        )
        return response.json()


# --- Agent loop ---

def run_agent_loop(
    conversation_id: int,
    user_message: str,
    user_id: str = None,
) -> Generator[Dict, None, None]:
    """
    Run the agent loop for a conversation.

    Yields events:
    - {"type": "tool_call", "name": "...", "arguments": {...}}
    - {"type": "tool_result", "name": "...", "result": "..."}
    - {"type": "response", "content": "..."}
    - {"type": "error", "content": "..."}
    """
    user_id = user_id or config.DEFAULT_USER
    db.add_turn(conversation_id, "user", user_message)

    tools = get_available_tools()

    messages = [
        {"role": "system", "content": build_system_prompt(user_id, tools)}
    ]
    messages.extend(db.get_context_window(conversation_id))

    iteration = 0
    while iteration < config.MAX_TOOL_ITERATIONS:
        iteration += 1

        try:
            response = call_ollama(messages, tools)
            message = response.get("message", {})
            tool_calls = message.get("tool_calls", [])

            if tool_calls:
                messages.append({
                    "role": "assistant",
                    "content": message.get("content", ""),
                    "tool_calls": tool_calls,
                })
                for tool_call in tool_calls:
                    func = tool_call.get("function", {})
                    tool_name = func.get("name", "unknown")
                    arguments = func.get("arguments", {})

                    yield {"type": "tool_call", "name": tool_name, "arguments": arguments}
                    result = call_tool(tool_name, arguments)
                    yield {"type": "tool_result", "name": tool_name, "result": result}

                    messages.append({"role": "tool", "content": result})

                continue

            else:
                content = message.get("content", "")
                db.add_turn(conversation_id, "assistant", content)
                yield {"type": "response", "content": content}
                return

        except Exception as e:
            yield {"type": "error", "content": str(e)}
            return

    yield {"type": "error", "content": "Maximum tool iterations reached"}


def chat(conversation_id: int, user_message: str, user_id: str = None) -> str:
    """Synchronous chat. Returns the final response text."""
    final = ""
    for event in run_agent_loop(conversation_id, user_message, user_id):
        if event["type"] == "response":
            final = event["content"]
        elif event["type"] == "error":
            final = f"Error: {event['content']}"
    return final


def start_conversation(user_id: str = None) -> int:
    """Start a new conversation and return its ID."""
    return db.create_conversation(user_id or config.DEFAULT_USER)
