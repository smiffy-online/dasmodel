# DasModel

An agent interface for Ollama models — local or cloud — with MCP tool integration, shell access, and a training feedback loop.

DasModel connects to any model served by [Ollama](https://ollama.ai), whether running on your own hardware or via [Ollama Cloud](https://ollama.com/cloud), and gives it the ability to use tools through the [Model Context Protocol](https://modelcontextprotocol.io) and execute shell commands on the host machine. It includes a web UI for chat, conversation history, prompt template management, and a correction system that turns human feedback into training exemplars.

## Why

Cloud AI agents are powerful but expensive. Local models are free to run but lack tool access and need guidance. DasModel bridges the gap: connect to any Ollama model, give it real tools and a shell, then evaluate its outputs, correct mistakes, and watch those corrections automatically improve future responses. Over time, you build confidence in which tasks the model handles reliably — and which still need human oversight.

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌──────────────────┐
│   Web UI    │────▶│  Flask API   │────▶│  Ollama          │
│ (Bootstrap) │◀────│  (agent.py)  │◀────│  local or cloud  │
└─────────────┘     └──────┬───────┘     └──────────────────┘
                           │
                    ┌──────▼───────┐
                    │ MCP Server(s)│    ┌────────────┐
                    │   (tools)    │    │   Shell    │
                    └──────────────┘    │  (built-in)│
                                       └────────────┘
```

The agent loop:

1. User sends a message (web UI or API)
2. Agent builds a system prompt from the Jinja2 template, injecting active rules and relevant correction exemplars
3. Agent sends the message, conversation history, and available tools to Ollama
4. If the model requests tool calls, the agent executes them via MCP and feeds results back
5. Repeats until the model produces a final response (max iterations configurable)
6. Everything is logged: conversations, turns, tool calls

When a response is wrong, add a correction. Corrections are stored alongside the original, and the corrected version is injected as an exemplar into future system prompts — a lightweight RLHF-like loop without fine-tuning infrastructure.

## Quick start

### 1. Clone and set up

Requires Python 3.11+ (for `tomllib`).

```bash
git clone https://github.com/smiffy-online/dasmodel.git
cd dasmodel
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Install Bootstrap

Download [Bootstrap](https://getbootstrap.com/docs/5.3/getting-started/download/) and extract into the static directory:

```
static/
  vendor/
    bootstrap/
      css/
        bootstrap.min.css
      js/
        bootstrap.bundle.min.js
```

The paths are configurable in `config.toml` under `[static]`.

### 3. Configure

Edit `config.toml` to set your Ollama URL, model name, and MCP server(s):

```toml
[ollama]
url = "http://localhost:11434"
model = "qwen3:8b"

[[mcp]]
name = "my-tools"
url = "http://localhost:5001"
```

### 4. Run

```bash
source venv/bin/activate   # if not already active
python main.py
```

Open [http://localhost:5003](http://localhost:5003). The database is created automatically on first request.

No database servers. No build tools. No node_modules. One `pip install` and you're running.

## Configuration

All settings live in `config.toml`:

```toml
[ollama]
url = "http://localhost:11434"    # Ollama API endpoint
model = "qwen3:8b"                # Model name as known to Ollama
# api_key = ""                    # Required for Ollama Cloud direct API

[server]
host = "0.0.0.0"
port = 5003
debug = false

[database]
path = "dasmodel.db"              # SQLite file

[agent]
max_tool_iterations = 10          # Max tool-call rounds per message
default_user = "user"             # Default user identity

[static]
bootstrap_css = "/static/vendor/bootstrap/css/bootstrap.min.css"
bootstrap_js = "/static/vendor/bootstrap/js/bootstrap.bundle.min.js"

[shell]
enabled = false                       # Enable for local command execution
working_directory = "."               # Default cwd for commands
timeout = 30                          # Seconds before command is killed
max_output_chars = 50000              # Truncate long output
# allowed_directories = ["/home/user/projects"]  # Optional restriction

# MCP servers — add as many as needed
[[mcp]]
name = "misti"
url = "http://localhost:5001"

# [[mcp]]
# name = "filesystem"
# url = "http://localhost:5002"
```

### Multiple MCP servers

DasModel queries all configured MCP servers for available tools and routes tool calls to the correct server automatically. Remove all `[[mcp]]` entries to run without tool access — the model still works for conversation, it just can't call tools.

### Without Ollama locally

DasModel works with any Ollama instance — local, remote on your network, or Ollama Cloud.

**Ollama Cloud via local client (simplest):** Update to Ollama v0.12+, sign in, then use cloud model names:

```bash
ollama login
```

```toml
[ollama]
url = "http://localhost:11434"
model = "qwen3-coder:480b-cloud"
```

Cloud models are proxied through your local Ollama — DasModel talks to localhost as usual.

**Ollama Cloud direct API:** Point directly at `ollama.com` with an API key. Create a key at [ollama.com](https://ollama.com), then:

```toml
[ollama]
url = "https://ollama.com"
model = "qwen3-coder:480b-cloud"
api_key = "your-api-key-here"
```

**Remote Ollama on your network:** Point at any machine running Ollama:

```toml
[ollama]
url = "http://my-gpu-box:11434"
model = "qwen3:8b"
```

## Web UI

Four pages, all Bootstrap 5 with dark theme:

- **Chat** — conversational interface with SSE streaming, tool-call visualisation
- **Conversations** — browse and resume past conversations
- **Rules** — manage hard rules injected into every system prompt (toggle on/off, set priorities)
- **Prompts** — edit the Jinja2 system prompt template with live preview

## API

### Chat

```
POST /api/chat              # SSE stream (tool_call, tool_result, response, error, done)
POST /api/chat/sync         # Synchronous JSON response
POST /api/conversation      # Create conversation
GET  /api/conversation/<id> # Get conversation with turns
```

### Corrections

```
POST /api/turn/<id>/correct
```

Body: `{"correction": "better response", "reason": "why", "corrected_by": "user"}`

### Rules and Prompts

Full CRUD at `/api/rules` and `/api/prompts`.

## Training loop

When you correct a foundation model, the correction doesn't persist — next conversation, same mistakes. DasModel changes that.

1. Model produces a response (stored as a turn)
2. If wrong, submit a correction via the API
3. The correction stores original (bad) and corrected (good) responses
4. On future conversations, relevant corrections are injected into the system prompt as exemplars
5. The model sees "when asked X, don't say Y, say Z instead"

This is in-context learning, not fine-tuning. It works because small models follow examples well, even when they struggle with open-ended reasoning. The corrections accumulate — every mistake the model makes and you fix becomes permanent institutional knowledge.

## How I use this

I run Claude as my primary coding agent, but it costs real money per conversation. DasModel lets me offload work to a local model running through Ollama — but I need to know what it can handle reliably before I trust it with real tasks.

So I use Claude as a supervisor. It sends tasks to the local model via `/api/chat/sync`, evaluates the responses, and submits corrections when they're wrong. The local model improves with each correction. Over time, I build a picture of what works: "note search and summarisation — reliable," "complex refactoring — not yet."

Once I'm confident in a task category, I dispatch it directly — no cloud model in the loop, no token cost. The three-way visibility helps: Claude talks to the model via the API, I watch the conversation in real time through the web UI, and every turn is logged for later review. The supervision has an exit condition.

## Stack

- **Python 3.11+** with Flask
- **SQLite** (zero-config, portable)
- **Ollama** for LLM inference (local or cloud)
- **MCP** for tool access (multiple servers)
- **Built-in shell tool** for local command execution
- **Bootstrap 5** for the web UI
- **Jinja2** for system prompt templating



## Shell tool

DasModel includes a built-in shell tool that gives the model direct command execution on the local machine — the same capability that makes cloud coding agents useful. It is disabled by default and must be explicitly enabled in `config.toml`:

```toml
[shell]
enabled = true
working_directory = "/home/user/projects"
timeout = 30
max_output_chars = 50000
# Optional: restrict to specific directories
# allowed_directories = ["/home/user/projects", "/tmp"]
```

When enabled, the model can execute arbitrary shell commands and receive stdout, stderr, and exit codes. Safety controls include configurable timeouts, output truncation (to avoid blowing up the context window), and optional directory restrictions that prevent the model from operating outside designated paths.

**Warning: The shell tool gives an LLM the ability to execute arbitrary commands on your machine. Language models are unpredictable — they may run destructive commands, misinterpret instructions, or behave in ways you did not intend. This software is provided "as is", without warranty of any kind. You enable shell execution entirely at your own risk. Use directory restrictions, run in a sandboxed environment, and never grant access to systems you cannot afford to break.**


## Licence

MIT. See [LICENCE](LICENCE).
