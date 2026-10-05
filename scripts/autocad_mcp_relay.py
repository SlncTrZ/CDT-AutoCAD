"""Private SSH stdio relay to the existing interactive AutoCAD supervisor.
Credentials stay in RAM; this pilot refuses all non-read tools.
"""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.request

LIMIT = 8 * 1024 * 1024
ALLOWED = {"help", "system_status", "system_capabilities", "native_integrity_status", "document_info"}


class Relay:
    def __init__(self):
        import base64
        script = r"""$ErrorActionPreference='Stop';$s=Get-Content -LiteralPath (Join-Path $env:LOCALAPPDATA 'CDT-AutoCAD\secrets\auth-token.dpapi') -Raw | ConvertTo-SecureString;$p=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($s);try{[Console]::Out.Write([Runtime.InteropServices.Marshal]::PtrToStringBSTR($p))}finally{[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($p)}"""
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand",
             base64.b64encode(script.encode("utf-16-le")).decode()],
            capture_output=True, timeout=10,
        )
        if result.returncode or not result.stdout:
            raise RuntimeError("credential_unavailable")
        self.secret = result.stdout.decode("utf-8").strip()
        self.session = None
        self.protocol = "2025-11-25"
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def send(self, payload):
        headers = {"Authorization": "Bearer " + self.secret,
                   "Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream"}
        if self.session:
            headers["Mcp-Session-Id"] = self.session
            headers["MCP-Protocol-Version"] = self.protocol
        req = urllib.request.Request("http://127.0.0.1:8000/mcp", json.dumps(payload).encode(), headers)
        with self.opener.open(req, timeout=15) as response:
            self.session = response.headers.get("Mcp-Session-Id", self.session)
            raw = response.read(LIMIT + 1)
        if len(raw) > LIMIT:
            raise RuntimeError("response_too_large")
        if not raw:
            return None
        text = raw.decode("utf-8")
        if text.lstrip().startswith(("{", "[")):
            value = json.loads(text)
        else:
            data = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
            value = next((json.loads(line) for line in data
                          if isinstance(json.loads(line), dict) and json.loads(line).get("id") == payload.get("id")), None)
        if isinstance(value, dict) and payload.get("method") == "initialize":
            self.protocol = value.get("result", {}).get("protocolVersion", self.protocol)
        return value

    def initialize(self):
        value = self.send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": self.protocol, "capabilities": {},
            "clientInfo": {"name": "slnctrz-lifecycle", "version": "0.1"}}})
        if not value or "error" in value:
            raise RuntimeError("initialize_failed")
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def call(self, name):
        if name not in ALLOWED:
            raise RuntimeError("tool_not_allowed")
        self.initialize()
        response = self.send({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                              "params": {"name": name, "arguments": {}}})
        result = (response or {}).get("result", {})
        if "error" in (response or {}) or result.get("isError"):
            raise RuntimeError("read_probe_failed")
        value = result.get("structuredContent")
        if value is None:
            value = next((json.loads(c["text"]) for c in result.get("content", [])
                          if c.get("type") == "text"), {})
        if not isinstance(value, dict):
            raise RuntimeError("read_probe_invalid")
        return value


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "probe" and sys.argv[2] in ALLOWED:
        try:
            print(json.dumps(Relay().call(sys.argv[2])))
        except Exception:
            print(json.dumps({"ok": False, "code": "PROVIDER_PROBE_UNAVAILABLE"}))
            return 1
        return 0
    if len(sys.argv) != 2 or sys.argv[1] != "stdio":
        return 2
    relay = None
    for raw in sys.stdin.buffer:
        if len(raw) > LIMIT:
            return 2
        try:
            payload = json.loads(raw)
            ident = payload.get("id")
            if payload.get("method") == "server/discover":
                result = {"jsonrpc": "2.0", "id": ident,
                          "error": {"code": -32601, "message": "Legacy MCP relay"}}
            elif payload.get("method") == "tools/call" and payload.get("params", {}).get("name") not in ALLOWED:
                result = {"jsonrpc": "2.0", "id": ident,
                          "error": {"code": -32601, "message": "Tool outside read-only pilot"}}
            else:
                relay = relay or Relay()
                result = relay.send(payload)
                if result and payload.get("method") == "tools/list":
                    result["result"]["tools"] = [t for t in result["result"].get("tools", []) if t["name"] in ALLOWED]
            if result is not None:
                print(json.dumps(result), flush=True)
        except Exception:
            if 'payload' in locals() and "id" in payload:
                print(json.dumps({"jsonrpc": "2.0", "id": payload["id"],
                                  "error": {"code": -32000, "message": "Provider transport unavailable"}}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
