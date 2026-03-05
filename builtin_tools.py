"""
Built-in tools for DasModel.

These run locally on the same machine as the agent, not via MCP.
Currently provides shell execution. Add further built-in tools here.
"""

import subprocess
import json
import os
from typing import Dict, List, Any, Optional

import config


# --- Shell tool ---

SHELL_TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "shell",
        "description": (
            "Execute a shell command on the local machine. "
            "Returns stdout, stderr, and exit code. "
            "Use for file operations, system administration, "
            "running scripts, installing packages, and general automation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute (passed to /bin/sh -c)",
                },
                "working_directory": {
                    "type": "string",
                    "description": (
                        "Working directory for the command. "
                        "Defaults to the configured working directory."
                    ),
                },
            },
            "required": ["command"],
        },
    },
}


def execute_shell(arguments: Dict[str, Any]) -> str:
    """Execute a shell command and return structured output."""
    command = arguments.get("command", "").strip()
    if not command:
        return json.dumps({"error": "No command provided"})

    shell_cfg = config.SHELL
    if not shell_cfg.get("enabled", False):
        return json.dumps({"error": "Shell tool is disabled in config.toml"})

    # Working directory: argument > config > cwd
    cwd = arguments.get("working_directory") or shell_cfg.get("working_directory") or os.getcwd()

    # Validate working directory exists
    if not os.path.isdir(cwd):
        return json.dumps({"error": f"Working directory does not exist: {cwd}"})

    # Check allowed directories if configured
    allowed_dirs = shell_cfg.get("allowed_directories", [])
    if allowed_dirs:
        real_cwd = os.path.realpath(cwd)
        if not any(real_cwd.startswith(os.path.realpath(d)) for d in allowed_dirs):
            return json.dumps({
                "error": f"Working directory {cwd} is outside allowed directories",
                "allowed": allowed_dirs,
            })

    timeout = shell_cfg.get("timeout", 30)

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )

        output = {
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

        # Truncate very long output to avoid blowing up the context window
        max_output = shell_cfg.get("max_output_chars", 50000)
        for key in ("stdout", "stderr"):
            if len(output[key]) > max_output:
                output[key] = output[key][:max_output] + f"\n... (truncated at {max_output} chars)"

        return json.dumps(output)

    except subprocess.TimeoutExpired:
        return json.dumps({
            "error": f"Command timed out after {timeout} seconds",
            "command": command,
        })
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


# --- Built-in tool registry ---

BUILTIN_TOOLS: Dict[str, Dict] = {}
BUILTIN_DEFINITIONS: List[Dict] = []


def _register():
    """Register all built-in tools based on config."""
    BUILTIN_TOOLS.clear()
    BUILTIN_DEFINITIONS.clear()

    if config.SHELL.get("enabled", False):
        BUILTIN_TOOLS["shell"] = execute_shell
        BUILTIN_DEFINITIONS.append(SHELL_TOOL_DEFINITION)


def get_builtin_tools() -> List[Dict]:
    """Get tool definitions for all enabled built-in tools."""
    if not BUILTIN_DEFINITIONS:
        _register()
    return BUILTIN_DEFINITIONS


def call_builtin(tool_name: str, arguments: Dict) -> Optional[str]:
    """Execute a built-in tool. Returns None if tool not found."""
    if not BUILTIN_TOOLS:
        _register()
    handler = BUILTIN_TOOLS.get(tool_name)
    if handler:
        return handler(arguments)
    return None


def is_builtin(tool_name: str) -> bool:
    """Check if a tool name is a built-in tool."""
    if not BUILTIN_TOOLS:
        _register()
    return tool_name in BUILTIN_TOOLS
