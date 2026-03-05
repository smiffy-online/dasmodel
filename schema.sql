-- DasModel SQLite schema
-- Conversation persistence, training corrections, and prompt management
-- for a local LLM agent runtime with MCP tool access.

-- ============================================================
-- RULES: Hard boundaries rendered into every system prompt
-- ============================================================
CREATE TABLE IF NOT EXISTS model_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_text TEXT NOT NULL,
    category TEXT,
    priority INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    active INTEGER DEFAULT 1
);

-- ============================================================
-- CONVERSATIONS: Units of interaction
-- ============================================================
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    title TEXT,
    started_at TEXT DEFAULT (datetime('now')),
    closed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(user_id);

-- ============================================================
-- TURNS: Individual messages within conversations
-- ============================================================
CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system', 'tool')),
    content TEXT NOT NULL,
    tool_calls TEXT,       -- JSON array of tool calls (if assistant requested tools)
    tool_call_id TEXT,     -- references the tool_call this is responding to
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_turns_conversation ON turns(conversation_id);

-- ============================================================
-- CORRECTIONS: Feedback on model outputs for training
-- ============================================================
CREATE TABLE IF NOT EXISTS corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    turn_id INTEGER NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
    original_content TEXT,
    correction TEXT NOT NULL,
    reason TEXT,
    corrected_by TEXT DEFAULT 'user',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_corrections_turn ON corrections(turn_id);

-- ============================================================
-- PROMPTS: Jinja2 templates for system prompt assembly
-- ============================================================
CREATE TABLE IF NOT EXISTS prompts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    template TEXT NOT NULL,
    description TEXT,
    active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- Default system prompt (id=1)
INSERT OR IGNORE INTO prompts (id, name, template, description) VALUES (
    1,
    'system_prompt',
    'You are a helpful AI assistant running locally.

Current time: {{ time.local }} ({{ time.timezone }})
Date: {{ time.date }}

User: {{ user.name }}

{% if rules %}## Rules
{% for rule in rules %}- {{ rule.rule_text }}
{% endfor %}{% endif %}

{% if exemplars %}## Examples of good responses
{% for ex in exemplars %}User: {{ ex.user_message }}
Bad: {{ ex.bad_response }}
Good: {{ ex.good_response }}
{% endfor %}{% endif %}

{% if tools %}## Available tools
{% for tool in tools %}- {{ tool.name }}: {{ tool.description }}
{% endfor %}{% endif %}',
    'Main system prompt template'
);
