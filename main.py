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
    return render_template("chat.html")


@app.route("/conversations")
def conversations_page():
    convs = db.get_recent_conversations(limit=50)
    return render_template("conversations.html", conversations=convs)


@app.route("/rules")
def rules_page():
    return render_template("rules.html")


@app.route("/prompts")
def prompts_page():
    return render_template("prompts.html")


# --- Conversation API ---

@app.route("/api/conversation", methods=["POST"])
def create_conversation():
    data = request.json or {}
    user_id = data.get("user_id", config.DEFAULT_USER)
    conv_id = db.create_conversation(user_id)
    return jsonify({"conversation_id": conv_id})


@app.route("/api/conversation/<int:conv_id>")
def get_conversation(conv_id: int):
    conv = db.get_conversation(conv_id)
    if not conv:
        return jsonify({"error": "Conversation not found"}), 404
    turns = db.get_conversation_turns(conv_id)
    return jsonify({"conversation": conv, "turns": turns})


@app.route("/api/conversation/<int:conv_id>/close", methods=["POST"])
def close_conversation(conv_id: int):
    db.close_conversation(conv_id)
    return jsonify({"status": "closed"})


@app.route("/api/conversations")
def list_conversations():
    user_id = request.args.get("user_id")
    limit = int(request.args.get("limit", 20))
    convs = db.get_recent_conversations(user_id, limit)
    return jsonify({"conversations": convs})


# --- Chat API ---

@app.route("/api/chat", methods=["POST"])
def chat_stream():
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
    return jsonify({"rules": db.get_all_rules()})

@app.route("/api/rules/<int:rule_id>")
def get_rule(rule_id: int):
    rule = db.get_rule(rule_id)
    if not rule:
        return jsonify({"error": "Rule not found"}), 404
    return jsonify({"rule": rule})

@app.route("/api/rules", methods=["POST"])
def add_rule():
    data = request.json or {}
    rule_text = data.get("rule_text", "").strip()
    if not rule_text:
        return jsonify({"error": "rule_text required"}), 400
    rule_id = db.add_rule(rule_text, data.get("category", "").strip() or None,
                          int(data.get("priority", 0)))
    return jsonify({"rule_id": rule_id})

@app.route("/api/rules/<int:rule_id>", methods=["PUT"])
def update_rule(rule_id: int):
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
    if not db.delete_rule(rule_id):
        return jsonify({"error": "Rule not found"}), 404
    return jsonify({"status": "deleted"})

@app.route("/api/rules/<int:rule_id>/toggle", methods=["POST"])
def toggle_rule(rule_id: int):
    new_status = db.toggle_rule_active(rule_id)
    if new_status is None:
        return jsonify({"error": "Rule not found"}), 404
    return jsonify({"active": new_status})


# --- Prompts API ---

@app.route("/api/prompts")
def list_prompts():
    return jsonify({"prompts": db.get_all_prompts()})

@app.route("/api/prompts/<int:prompt_id>")
def get_prompt(prompt_id: int):
    prompt = db.get_prompt(prompt_id)
    if not prompt:
        return jsonify({"error": "Prompt not found"}), 404
    return jsonify({"prompt": prompt})

@app.route("/api/prompts", methods=["POST"])
def create_prompt():
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
    if prompt_id == 1:
        return jsonify({"error": "Cannot delete system prompt"}), 400
    if not db.delete_prompt(prompt_id):
        return jsonify({"error": "Prompt not found"}), 404
    return jsonify({"status": "deleted"})

@app.route("/api/prompts/preview", methods=["POST"])
def preview_prompt():
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


# --- MCP server endpoint ---

if config.MCP_SERVER_ENABLED:
    import mcp_server

    @app.route("/mcp/", methods=["POST"])
    def mcp_endpoint():
        data = request.json
        if not data:
            return jsonify({"jsonrpc": "2.0", "id": None,
                            "error": {"code": -32700, "message": "Parse error"}}), 400
        response = mcp_server.handle_jsonrpc(data)
        return jsonify(response)


# --- Health ---

@app.route("/health")
def health():
    return jsonify({"status": "healthy", "model": config.OLLAMA_MODEL})


if __name__ == "__main__":
    app.run(host=config.HOST, port=config.PORT, debug=config.DEBUG)
