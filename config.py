"""
Configuration for DasModel agent runtime.
Loads from config.toml in the project root.
"""

import tomllib
from pathlib import Path
from typing import List, Dict

_CONFIG_PATH = Path(__file__).parent / "config.toml"


def _load() -> dict:
    """Load and return the TOML config."""
    if not _CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"Configuration file not found: {_CONFIG_PATH}\n"
            f"Copy config.toml.example to config.toml and edit to suit."
        )
    with open(_CONFIG_PATH, "rb") as f:
        return tomllib.load(f)


_cfg = _load()

# --- Ollama ---
OLLAMA_URL: str = _cfg.get("ollama", {}).get("url", "http://localhost:11434")
OLLAMA_MODEL: str = _cfg.get("ollama", {}).get("model", "qwen3:8b")
OLLAMA_API_KEY: str = _cfg.get("ollama", {}).get("api_key", "")

# --- Server ---
HOST: str = _cfg.get("server", {}).get("host", "0.0.0.0")
PORT: int = _cfg.get("server", {}).get("port", 5003)
DEBUG: bool = _cfg.get("server", {}).get("debug", False)

# --- Database ---
DB_PATH: str = _cfg.get("database", {}).get("path", "dasmodel.db")

# --- Agent ---
MAX_TOOL_ITERATIONS: int = _cfg.get("agent", {}).get("max_tool_iterations", 10)
DEFAULT_USER: str = _cfg.get("agent", {}).get("default_user", "user")

# --- Static assets ---
BOOTSTRAP_CSS: str = _cfg.get("static", {}).get(
    "bootstrap_css", "/static/vendor/bootstrap/css/bootstrap.min.css"
)
BOOTSTRAP_JS: str = _cfg.get("static", {}).get(
    "bootstrap_js", "/static/vendor/bootstrap/js/bootstrap.bundle.min.js"
)

# --- MCP servers ---
MCP_SERVERS: List[Dict[str, str]] = _cfg.get("mcp", [])

# --- Shell tool ---
SHELL: Dict = _cfg.get("shell", {"enabled": False})

# --- MCP server (expose DasModel as an MCP server) ---
MCP_SERVER_ENABLED: bool = _cfg.get("mcp_server", {}).get("enabled", False)
