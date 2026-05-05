#!/usr/bin/env python3
"""
Flask application for DasModel agent runtime.
Web UI and API for chatting with Ollama models (local or cloud),
with MCP tool access and a training/correction feedback loop.
"""

from datetime import datetime

from flask import Flask, render_template, request, jsonify, Response, stream_with_context
import json

import config
import db
import agent

app = Flask(__name__, template_folder="templates", static_folder="static")


@app.before_request
def ensure_db():
    """Initialize the database connection before handling any request."""
    if not hasattr(app, "_db_initialised"):
        db.init_db()
        app._db_initialised = True


@app.context_processor
def inject_globals():
    """Make config values available to all templates."""
    return {
        "model_name": config.OLLAMA_MODEL,
        "bootstrap_css": config.BOOTSTRAP_CSS,
        "bootstrap_js": config.BOOTSTRAP_JS,
    }


# --- Web pages ---

@app.route("/")
def index():
    """Redirect to conversations page."""
    return render_template("chat.html")


@app.route("/conversations")
def conversations_page():
    """List recent conversations."""
    convs = db.get_recent_conversations(limit=50)
    return render_template("conversations.html", conversations=convs)


@app.route("/rules")
def rules_page():
    """Page for managing rules."""
    return render_template("rules.html")


@app.route("/prompts")
def prompts_page():
    """Page for managing prompts."""
    return render_template("prompts.html")

@app.route("/skills")
def skills_page():
    """Page for managing skills."""
    return render_template("skills.html")


# --- Conversation API ---

@app.route("/api/conversation", methods=["POST"])
def create_conversation():
    """Create a new conversation and return its ID"""
    data = request.json or {}
    user_id = data.get("user_id", config.DEFAULT_USER)
    conv_id = db.create_conversation(user_id)
    return jsonify({"conversation_id": conv_id})


@app.route("/api/conversation/<int:conv_id>")
def get_conversation(conv_id: int):
    """Get conversation details and turns"""
    conv = db.get_conversation(conv_id)
    if not conv:
        return jsonify({"error": "Conversation not found"}), 404
    turns = db.get_conversation_turns(conv_id)
    return jsonify({"conversation": conv, "turns": turns})


@app.route("/api/conversation/<int:conv_id>/close", methods=["POST"])
def close_conversation(conv_id: int):
    """Close a conversation"""
    db.close_conversation(conv_id)
    return jsonify({"status": "closed"})


@app.route("/api/conversations")
def list_conversations():
    """List recent conversations for a user"""
    user_id = request.args.get("user_id")
    limit = int(request.args.get("limit", 20))
    convs = db.get_recent_conversations(user_id, limit)
    return jsonify({"conversations": convs})


# --- Chat API ---

@app.route("/api/chat", methods=["POST"])
def chat_stream():
    """Send a message to the agent and stream back responses as Server-Sent Events (SSE)"""
    data = request.json or {}
    conv_id = data.get("conversation_id")
    message = data.get("message", "").strip()
    user_id = data.get("user_id", config.DEFAULT_USER)

    if not conv_id:
        return jsonify({"error": "conversation_id required"}), 400
    if not message:
        return jsonify({"error": "message required"}), 400

    def generate():
        for event in agent.run_agent_loop(conv_id, message, user_id):
            yield f"data: {json.dumps(event)}\n\n"
        yield 'data: {"type": "done"}\n\n'

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/chat/sync", methods=["POST"])
def chat_sync():
    """Send a message to the agent and get the full response synchronously (for testing/debugging)"""
    data = request.json or {}
    conv_id = data.get("conversation_id")
    message = data.get("message", "").strip()
    user_id = data.get("user_id", config.DEFAULT_USER)

    if not conv_id:
        return jsonify({"error": "conversation_id required"}), 400
    if not message:
        return jsonify({"error": "message required"}), 400

    response = agent.chat(conv_id, message, user_id)
    return jsonify({"response": response})


# --- Corrections API ---

@app.route("/api/turn/<int:turn_id>/correct", methods=["POST"])
def add_correction(turn_id: int):
    """Add a correction for a specific turn"""
    data = request.json or {}
    correction = data.get("correction", "").strip()
    reason = data.get("reason", "")
    corrected_by = data.get("corrected_by", "user")
    if not correction:
        return jsonify({"error": "correction required"}), 400
    corr_id = db.add_correction(turn_id, correction, reason, corrected_by)
    return jsonify({"correction_id": corr_id})


# --- Rules API ---

@app.route("/api/rules")
def list_rules():
    """List all rules"""
    return jsonify({"rules": db.get_all_rules()})

@app.route("/api/rules/<int:rule_id>")
def get_rule(rule_id: int):
    """Get details of a specific rule"""
    rule = db.get_rule(rule_id)
    if not rule:
        return jsonify({"error": "Rule not found"}), 404
    return jsonify({"rule": rule})

@app.route("/api/rules", methods=["POST"])
def add_rule():
    """Add a new rule"""
    data = request.json or {}
    rule_text = data.get("rule_text", "").strip()
    if not rule_text:
        return jsonify({"error": "rule_text required"}), 400
    rule_id = db.add_rule(rule_text, data.get("category", "").strip() or None,
                          int(data.get("priority", 0)))
    return jsonify({"rule_id": rule_id})

@app.route("/api/rules/<int:rule_id>", methods=["PUT"])
def update_rule(rule_id: int):
    """Update an existing rule"""
    data = request.json or {}
    rule_text = data.get("rule_text")
    if rule_text is not None:
        rule_text = rule_text.strip()
        if not rule_text:
            return jsonify({"error": "rule_text cannot be empty"}), 400
    success = db.update_rule(rule_id, rule_text, data.get("category"),
                             data.get("priority"), data.get("active"))
    if not success:
        return jsonify({"error": "Rule not found or no changes"}), 404
    return jsonify({"status": "updated"})

@app.route("/api/rules/<int:rule_id>", methods=["DELETE"])
def delete_rule(rule_id: int):
    """Delete a rule"""
    if not db.delete_rule(rule_id):
        return jsonify({"error": "Rule not found"}), 404
    return jsonify({"status": "deleted"})

@app.route("/api/rules/<int:rule_id>/toggle", methods=["POST"])
def toggle_rule(rule_id: int):
    """Toggle a rule's active status"""
    new_status = db.toggle_rule_active(rule_id)
    if new_status is None:
        return jsonify({"error": "Rule not found"}), 404
    return jsonify({"active": new_status})


# --- Prompts API ---

@app.route("/api/prompts")
def list_prompts():
    """List all prompts"""
    return jsonify({"prompts": db.get_all_prompts()})

@app.route("/api/prompts/<int:prompt_id>")
def get_prompt(prompt_id: int):
    """Get details of a specific prompt"""
    prompt = db.get_prompt(prompt_id)
    if not prompt:
        return jsonify({"error": "Prompt not found"}), 404
    return jsonify({"prompt": prompt})

@app.route("/api/prompts", methods=["POST"])
def create_prompt():
    """Create a new prompt"""
    data = request.json or {}
    name = data.get("name", "").strip()
    template = data.get("template", "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    if not template:
        return jsonify({"error": "template required"}), 400
    try:
        prompt_id = db.create_prompt(name, template, data.get("description", "").strip() or None)
        return jsonify({"prompt_id": prompt_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/api/prompts/<int:prompt_id>", methods=["PUT"])
def update_prompt(prompt_id: int):
    """Update an existing prompt"""
    data = request.json or {}
    template = data.get("template")
    if template is not None:
        template = template.strip()
        if not template:
            return jsonify({"error": "template cannot be empty"}), 400
    name = data.get("name")
    if name is not None:
        name = name.strip()
        if not name:
            return jsonify({"error": "name cannot be empty"}), 400
    success = db.update_prompt(prompt_id, template, name, data.get("description"), data.get("active"))
    if not success:
        return jsonify({"error": "Prompt not found or no changes"}), 404
    return jsonify({"status": "updated"})

@app.route("/api/prompts/<int:prompt_id>", methods=["DELETE"])
def delete_prompt(prompt_id: int):
    """Delete a prompt (except system prompt)"""
    if prompt_id == 1:
        return jsonify({"error": "Cannot delete system prompt"}), 400
    if not db.delete_prompt(prompt_id):
        return jsonify({"error": "Prompt not found"}), 404
    return jsonify({"status": "deleted"})

@app.route("/api/prompts/preview", methods=["POST"])
def preview_prompt():
    """Render a prompt template with example data for preview/testing"""
    from datetime import timezone as tz
    from jinja2 import Template, TemplateError

    data = request.json or {}
    template_str = data.get("template", "")
    if not template_str:
        return jsonify({"error": "template required"}), 400
    try:
        now = datetime.now()
        rendered = Template(template_str).render(
            time={"local": now.strftime("%H:%M"), "timezone": "UTC",
                  "date": now.strftime("%A, %d %B %Y"),
                  "utc": datetime.now(tz.utc).strftime("%H:%M UTC")},
            user={"name": "Example User"},
            rules=db.get_active_rules()[:3],
            exemplars=[],
            tools=[{"name": "search_notes", "description": "Search through notes"},
                   {"name": "create_note", "description": "Create a new note"}],
        )
        return jsonify({"rendered": rendered})
    except TemplateError as e:
        return jsonify({"error": f"Template error: {e}"}), 400
    except Exception as e:
        return jsonify({"error": f"Error: {e}"}), 400

# --- Skills API ---

@app.route("/api/skills")
def list_skills():
    """List all skills"""
    return jsonify({"skills": db.get_all_skills()})

@app.route("/api/skills/<int:skill_id>")
def get_skill(skill_id: int):
    """Get details of a specific skill"""
    skill = db.get_skill(skill_id)
    if not skill:
        return jsonify({"error": "Skill not found"}), 404
    return jsonify({"skill": skill})

@app.route("/api/skills", methods=["POST"])
def create_skill():
    """Create a new skill"""
    data = request.json or {}
    name = data.get("name", "").strip()
    template = data.get("template", "").strip()
    description = data.get("description", "").strip()
    argument_hint = data.get("argument_hint", "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    if not description:
        return jsonify({"error": "description required"}), 400
    if not argument_hint:
        return jsonify({"error": "argument_hint required"}), 400
    if not template:
        return jsonify({"error": "template required"}), 400
    try:
        skill_id = db.create_skill(name, description, argument_hint, template)
        return jsonify({"skill_id": skill_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/api/skills/<int:skill_id>", methods=["PUT"])
def update_skill(skill_id: int):
    """Update an existing skill"""
    data = request.json or {}
    template = data.get("template")
    if template is not None:
        template = template.strip()
        if not template:
            return jsonify({"error": "template cannot be empty"}), 400
    name = data.get("name")
    if name is not None:
        name = name.strip()
        if not name:
            return jsonify({"error": "name cannot be empty"}), 400
    success = db.update_skill(skill_id, template, name, data.get("description"), data.get("active"))
    if not success:
        return jsonify({"error": "Skill not found or no changes"}), 404
    return jsonify({"status": "updated"})

@app.route("/api/skills/<int:skill_id>", methods=["DELETE"])
def delete_skill(skill_id: int):
    """Delete a skill"""
    if not db.delete_skill(skill_id):
        return jsonify({"error": "Skill not found"}), 404
    return jsonify({"status": "deleted"})

@app.route("/api/skills/<int:skill_id>/toggle", methods=["POST"])
def toggle_skill(skill_id: int):
    """Toggle a skill's active status"""
    new_status = db.toggle_skill_active(skill_id)
    if new_status is None:
        return jsonify({"error": "Skill not found"}), 404
    return jsonify({"active": new_status})


# --- MCP server endpoint ---

if config.MCP_SERVER_ENABLED:
    import mcp_server

    @app.route("/mcp/", methods=["POST"])
    def mcp_endpoint():
        """Endpoint for MCP tool calls from the agent"""
        data = request.json
        if not data:
            return jsonify({"jsonrpc": "2.0", "id": None,
                            "error": {"code": -32700, "message": "Parse error"}}), 400
        response = mcp_server.handle_jsonrpc(data)
        return jsonify(response)



# --- Health ---

@app.route("/health")
def health():
    """Health check endpoint"""
    return jsonify({"status": "healthy", "model": config.OLLAMA_MODEL})


if __name__ == "__main__":
    app.run(host=config.HOST, port=config.PORT, debug=config.DEBUG)
