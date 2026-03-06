#!/usr/bin/env node
// DasModel MCP STDIO Proxy
// Bridges Claude Desktop (stdio) to a DasModel /mcp/ endpoint (HTTP).
//
// Usage:
//   node server/index.js [url]
//
// URL resolution (first match wins):
//   1. CLI argument:  node server/index.js http://my-host:5003/mcp/
//   2. Environment:   DASMODEL_URL=http://my-host:5003/mcp/
//   3. Default:       http://localhost:5003/mcp/

import { stdin, stdout, stderr } from "process";
import { spawn } from "child_process";
import readline from "readline";

const DEFAULT_URL = "http://localhost:5003/mcp/";
const REMOTE_URL = process.argv[2] || process.env.DASMODEL_URL || DEFAULT_URL;

stderr.write(`[DasModel MCP] Starting STDIO proxy -> ${REMOTE_URL}\n`);

// Tools cache
let toolsCache = null;

// Setup line-based stdin reading
const rl = readline.createInterface({
  input: stdin,
  output: process.stdout,
  terminal: false,
});

rl.on("line", async (line) => {
  if (!line.trim()) return;

  try {
    const request = JSON.parse(line);
    stderr.write(`[DasModel MCP] <- ${request.method} (id: ${request.id})\n`);

    let response;

    if (request.method === "initialize") {
      response = {
        jsonrpc: "2.0",
        id: request.id,
        result: {
          protocolVersion: "2024-11-05",
          capabilities: {
            tools: {},
          },
          serverInfo: {
            name: "dasmodel-mcp",
            version: "0.1.0",
          },
        },
      };

      // Fetch tools from remote server
      try {
        const toolsResult = await callRemote("tools/list", {});
        toolsCache = toolsResult.tools;
        stderr.write(`[DasModel MCP] Cached ${toolsCache.length} tools from remote\n`);
      } catch (err) {
        stderr.write(`[DasModel MCP] ERROR: Failed to fetch tools during initialize\n`);
        stderr.write(`[DasModel MCP] ERROR: ${err.message}\n`);
        toolsCache = [];
      }
    } else if (request.method === "tools/list") {
      if (!toolsCache) {
        try {
          const toolsResult = await callRemote("tools/list", {});
          toolsCache = toolsResult.tools;
          stderr.write(`[DasModel MCP] Fetched ${toolsCache.length} tools on demand\n`);
        } catch (err) {
          stderr.write(`[DasModel MCP] ERROR: Failed to fetch tools: ${err.message}\n`);
          toolsCache = [];
        }
      }
      response = {
        jsonrpc: "2.0",
        id: request.id,
        result: {
          tools: toolsCache,
        },
      };
    } else if (request.method === "tools/call") {
      try {
        stderr.write(`[DasModel MCP] tools/call: ${request.params?.name || 'unknown'}\n`);
        const result = await callRemote("tools/call", request.params);
        response = {
          jsonrpc: "2.0",
          id: request.id,
          result,
        };
      } catch (err) {
        stderr.write(`[DasModel MCP] ERROR: tools/call failed for ${request.params?.name || 'unknown'}\n`);
        stderr.write(`[DasModel MCP] ERROR: ${err.message}\n`);
        response = {
          jsonrpc: "2.0",
          id: request.id,
          error: {
            code: -32603,
            message: err.message,
          },
        };
      }
    } else if (request.method === "notifications/initialized") {
      stderr.write("[DasModel MCP] Client initialized\n");
      return;
    } else {
      response = {
        jsonrpc: "2.0",
        id: request.id,
        error: {
          code: -32601,
          message: `Method not found: ${request.method}`,
        },
      };
    }

    stdout.write(JSON.stringify(response) + "\n");
    stderr.write(`[DasModel MCP] -> response (id: ${request.id})\n`);
  } catch (err) {
    stderr.write(`[DasModel MCP] ERROR: Failed to process request\n`);
    stderr.write(`[DasModel MCP] ERROR: ${err.message}\n`);
  }
});

async function callRemote(method, params = {}) {
  const payload = {
    jsonrpc: "2.0",
    id: Date.now(),
    method,
    params,
  };

  const payloadJson = JSON.stringify(payload);

  try {
    stderr.write(`[DasModel MCP] Calling ${method} at ${REMOTE_URL}\n`);

    const curlOutput = await new Promise((resolve, reject) => {
      const curl = spawn('curl', [
        '--no-progress-meter',
        '-X', 'POST',
        '-H', 'Content-Type: application/json',
        '-d', '@-',
        REMOTE_URL
      ]);

      let stdout_data = '';
      let stderr_data = '';

      curl.stdout.on('data', (data) => {
        stdout_data += data.toString();
      });

      curl.stderr.on('data', (data) => {
        stderr_data += data.toString();
      });

      curl.on('close', (code) => {
        if (stderr_data.trim()) {
          stderr.write(`[DasModel MCP] curl stderr: ${stderr_data}\n`);
        }
        if (code !== 0) {
          reject(new Error(`curl exited with code ${code}: ${stderr_data}`));
        } else {
          resolve(stdout_data);
        }
      });

      curl.on('error', (err) => {
        reject(err);
      });

      curl.stdin.write(payloadJson);
      curl.stdin.end();
    });

    if (!curlOutput || curlOutput.trim() === '') {
      throw new Error('Empty response from DasModel server');
    }

    let data;
    try {
      data = JSON.parse(curlOutput);
    } catch (parseErr) {
      stderr.write(`[DasModel MCP] ERROR: Invalid JSON: ${curlOutput.substring(0, 500)}\n`);
      throw new Error(`Invalid JSON response: ${parseErr.message}`);
    }

    if (data.error) {
      throw new Error(data.error.message || "Remote error");
    }

    stderr.write(`[DasModel MCP] Response received successfully\n`);
    return data.result;
  } catch (err) {
    stderr.write(`[DasModel MCP] ERROR: ${method} failed: ${err.message}\n`);
    throw new Error(`Request failed: ${err.message}`);
  }
}

rl.on("error", (err) => {
  stderr.write(`[DasModel MCP] ERROR: Readline error: ${err.message}\n`);
});

process.on("uncaughtException", (err) => {
  stderr.write(`[DasModel MCP] FATAL: ${err.message}\n`);
  process.exit(1);
});

process.on("unhandledRejection", (reason) => {
  stderr.write(`[DasModel MCP] FATAL: Unhandled rejection: ${reason}\n`);
});

stderr.write("[DasModel MCP] Ready\n");
