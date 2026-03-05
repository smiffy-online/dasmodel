"""
Database layer for DasModel.
SQLite-backed conversation persistence, rules, corrections, and prompts.
"""

import sqlite3
import json
from datetime import datetime, timezone
from typing import List, Dict, Optional
from pathlib import Path

import config


def _now() -> str:
    """Current UTC timestamp as ISO string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def get_connection() -> sqlite3.Connection:
    """Get a database connection with row factory enabled."""
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Initialise the database from schema.sql if tables don't exist."""
    schema_path = Path(__file__).parent / "schema.sql"
    if not schema_path.exists():
        raise FileNotFoundError(f"Schema file not found: {schema_path}")

    conn = get_connection()
    with open(schema_path) as f:
        conn.executescript(f.read())
    conn.close()


# --- Rules ---

def get_active_rules() -> List[Dict]:
    """Active rules ordered by priority (highest first)."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT rule_text, category FROM model_rules "
        "WHERE active = 1 ORDER BY priority DESC, id"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_rules() -> List[Dict]:
    """All rules (active and inactive)."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, rule_text, category, priority, active, created_at "
        "FROM model_rules ORDER BY priority DESC, id"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_rule(rule_id: int) -> Optional[Dict]:
    """Single rule by ID."""
    conn = get_connection()
    row = conn.execute(
        "SELECT id, rule_text, category, priority, active, created_at "
        "FROM model_rules WHERE id = ?", (rule_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def add_rule(rule_text: str, category: str = None, priority: int = 0) -> int:
    """Add a rule. Returns rule ID."""
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO model_rules (rule_text, category, priority) VALUES (?, ?, ?)",
        (rule_text, category, priority)
    )
    conn.commit()
    rule_id = cur.lastrowid
    conn.close()
    return rule_id


def update_rule(rule_id: int, rule_text: str = None, category: str = None,
                priority: int = None, active: bool = None) -> bool:
    """Update a rule. Only updates fields that are not None."""
    updates, params = [], []

    if rule_text is not None:
        updates.append("rule_text = ?")
        params.append(rule_text)
    if category is not None:
        updates.append("category = ?")
        params.append(category or None)
    if priority is not None:
        updates.append("priority = ?")
        params.append(priority)
    if active is not None:
        updates.append("active = ?")
        params.append(1 if active else 0)

    if not updates:
        return False

    params.append(rule_id)
    conn = get_connection()
    cur = conn.execute(
        f"UPDATE model_rules SET {', '.join(updates)} WHERE id = ?", params
    )
    conn.commit()
    changed = cur.rowcount > 0
    conn.close()
    return changed


def delete_rule(rule_id: int) -> bool:
    """Delete a rule."""
    conn = get_connection()
    cur = conn.execute("DELETE FROM model_rules WHERE id = ?", (rule_id,))
    conn.commit()
    changed = cur.rowcount > 0
    conn.close()
    return changed


def toggle_rule_active(rule_id: int) -> Optional[bool]:
    """Toggle a rule's active status. Returns new status or None."""
    conn = get_connection()
    row = conn.execute("SELECT active FROM model_rules WHERE id = ?", (rule_id,)).fetchone()
    if not row:
        conn.close()
        return None
    new_status = 0 if row["active"] else 1
    conn.execute("UPDATE model_rules SET active = ? WHERE id = ?", (new_status, rule_id))
    conn.commit()
    conn.close()
    return bool(new_status)


# --- Conversations ---

def create_conversation(user_id: str, title: str = None) -> int:
    """Create a new conversation. Returns conversation ID."""
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO conversations (user_id, title) VALUES (?, ?)",
        (user_id, title)
    )
    conn.commit()
    conv_id = cur.lastrowid
    conn.close()
    return conv_id


def get_conversation(conversation_id: int) -> Optional[Dict]:
    """Get conversation by ID."""
    conn = get_connection()
    row = conn.execute(
        "SELECT id, user_id, title, started_at, closed_at "
        "FROM conversations WHERE id = ?", (conversation_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def close_conversation(conversation_id: int):
    """Mark a conversation as closed."""
    conn = get_connection()
    conn.execute(
        "UPDATE conversations SET closed_at = ? WHERE id = ?",
        (_now(), conversation_id)
    )
    conn.commit()
    conn.close()


def get_recent_conversations(user_id: str = None, limit: int = 20) -> List[Dict]:
    """Recent conversations with turn counts."""
    conn = get_connection()
    if user_id:
        rows = conn.execute(
            "SELECT c.id, c.user_id, c.title, c.started_at, c.closed_at, "
            "COUNT(t.id) as turn_count "
            "FROM conversations c LEFT JOIN turns t ON t.conversation_id = c.id "
            "WHERE c.user_id = ? GROUP BY c.id ORDER BY c.started_at DESC LIMIT ?",
            (user_id, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT c.id, c.user_id, c.title, c.started_at, c.closed_at, "
            "COUNT(t.id) as turn_count "
            "FROM conversations c LEFT JOIN turns t ON t.conversation_id = c.id "
            "GROUP BY c.id ORDER BY c.started_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- Turns ---

def add_turn(conversation_id: int, role: str, content: str,
             tool_calls: List[Dict] = None, tool_call_id: str = None) -> int:
    """Add a turn to a conversation. Returns turn ID."""
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO turns (conversation_id, role, content, tool_calls, tool_call_id) "
        "VALUES (?, ?, ?, ?, ?)",
        (conversation_id, role, content,
         json.dumps(tool_calls) if tool_calls else None,
         tool_call_id)
    )
    conn.commit()
    turn_id = cur.lastrowid
    conn.close()
    return turn_id


def get_conversation_turns(conversation_id: int, limit: int = 50) -> List[Dict]:
    """Turns for a conversation in chronological order."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, role, content, tool_calls, tool_call_id, created_at "
        "FROM turns WHERE conversation_id = ? "
        "ORDER BY created_at DESC LIMIT ?",
        (conversation_id, limit)
    ).fetchall()
    conn.close()
    result = [dict(r) for r in reversed(rows)]
    for r in result:
        if r["tool_calls"] and isinstance(r["tool_calls"], str):
            r["tool_calls"] = json.loads(r["tool_calls"])
    return result


def get_context_window(conversation_id: int, limit: int = 20) -> List[Dict]:
    """Recent turns formatted for the LLM context window."""
    turns = get_conversation_turns(conversation_id, limit)
    messages = []
    for turn in turns:
        msg = {"role": turn["role"], "content": turn["content"]}
        if turn["tool_calls"]:
            msg["tool_calls"] = turn["tool_calls"]
        if turn["tool_call_id"]:
            msg["tool_call_id"] = turn["tool_call_id"]
        messages.append(msg)
    return messages


# --- Corrections ---

def add_correction(turn_id: int, correction: str,
                   reason: str = None, corrected_by: str = "user") -> int:
    """Add a correction to a turn. Returns correction ID."""
    conn = get_connection()
    row = conn.execute("SELECT content FROM turns WHERE id = ?", (turn_id,)).fetchone()
    original = row["content"] if row else None

    cur = conn.execute(
        "INSERT INTO corrections (turn_id, original_content, correction, reason, corrected_by) "
        "VALUES (?, ?, ?, ?, ?)",
        (turn_id, original, correction, reason, corrected_by)
    )
    conn.commit()
    corr_id = cur.lastrowid
    conn.close()
    return corr_id


def search_exemplars(query: str, user_id: str = None, limit: int = 5) -> List[Dict]:
    """Search corrections for relevant exemplars."""
    conn = get_connection()
    sql = """
        SELECT
            (SELECT t2.content FROM turns t2
             WHERE t2.conversation_id = t.conversation_id
               AND t2.role = 'user' AND t2.created_at < t.created_at
             ORDER BY t2.created_at DESC LIMIT 1) AS user_message,
            c.original_content AS bad_response,
            c.correction AS good_response,
            c.reason
        FROM corrections c
        JOIN turns t ON c.turn_id = t.id
        JOIN conversations conv ON t.conversation_id = conv.id
        WHERE t.role = 'assistant'
    """
    params = []
    if user_id:
        sql += " AND conv.user_id = ?"
        params.append(user_id)
    if query:
        sql += " AND (c.correction LIKE ? OR c.original_content LIKE ?)"
        like = f"%{query}%"
        params.extend([like, like])
    sql += " ORDER BY c.created_at DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- Prompts ---

def get_prompt(prompt_id: int = 1) -> Optional[Dict]:
    """Get prompt template by ID. Default is system prompt (id=1)."""
    conn = get_connection()
    row = conn.execute(
        "SELECT id, name, template, description, active, created_at, updated_at "
        "FROM prompts WHERE id = ?", (prompt_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_prompts() -> List[Dict]:
    """All prompt templates."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, name, template, description, active, created_at, updated_at "
        "FROM prompts ORDER BY id"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_prompt(name: str, template: str, description: str = None) -> int:
    """Create a new prompt template."""
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO prompts (name, template, description) VALUES (?, ?, ?)",
        (name, template, description)
    )
    conn.commit()
    prompt_id = cur.lastrowid
    conn.close()
    return prompt_id


def update_prompt(prompt_id: int, template: str = None, name: str = None,
                  description: str = None, active: bool = None) -> bool:
    """Update a prompt template."""
    updates, params = [], []
    if template is not None:
        updates.append("template = ?")
        params.append(template)
    if name is not None:
        updates.append("name = ?")
        params.append(name)
    if description is not None:
        updates.append("description = ?")
        params.append(description or None)
    if active is not None:
        updates.append("active = ?")
        params.append(1 if active else 0)
    if not updates:
        return False
    updates.append("updated_at = ?")
    params.append(_now())
    params.append(prompt_id)

    conn = get_connection()
    cur = conn.execute(
        f"UPDATE prompts SET {', '.join(updates)} WHERE id = ?", params
    )
    conn.commit()
    changed = cur.rowcount > 0
    conn.close()
    return changed


def delete_prompt(prompt_id: int) -> bool:
    """Delete a prompt. Cannot delete id=1 (system prompt)."""
    if prompt_id == 1:
        return False
    conn = get_connection()
    cur = conn.execute("DELETE FROM prompts WHERE id = ?", (prompt_id,))
    conn.commit()
    changed = cur.rowcount > 0
    conn.close()
    return changed
