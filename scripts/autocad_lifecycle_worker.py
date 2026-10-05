"""Fixed lifecycle commands for the existing truon interactive AutoCAD task.
No shell/task/path is accepted from an Agent; no CAD mutation is performed.
"""
from __future__ import annotations

import base64
import json
import subprocess
import sys

from autocad_mcp_relay import Relay

TASK = "CDT-AutoCAD-RC3-Supervisor"
TASK_ACTION_SHA = "95F74F181F3037F674FED9360D52FD4A18F7A6375B34BC32E04A07C3A815A3C1"
LAUNCHER_SHA = "65C6B4ACC8D503B2E93B6FBCC6BBDC57C5914BDB00C5C765048FC8EAE5E8C5DE"


def powershell(script):
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand",
         base64.b64encode(script.encode("utf-16-le")).decode()],
        capture_output=True, timeout=10,
    )
    if result.returncode:
        raise RuntimeError("windows_control_unavailable")
    return json.loads(result.stdout.decode("utf-8-sig"))


def inventory():
    return powershell(r"""$ErrorActionPreference='Stop';
    $t=Get-ScheduledTask -TaskName 'CDT-AutoCAD-RC3-Supervisor';
    $actions=@($t.Actions);
    $action=$actions[0];
    $actionText=[string]$action.Execute + "\n" + [string]$action.Arguments + "\n" + [string]$action.WorkingDirectory;
    $actionHash=([BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($actionText)))).Replace('-','');
    $acad=@(Get-Process acad -ErrorAction SilentlyContinue|Select-Object Id,SessionId,@{n='created';e={$_.StartTime.ToUniversalTime().ToString('o')}});
    $tcp=@(Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue|Select-Object LocalAddress,OwningProcess);
    $launcher=Join-Path $env:LOCALAPPDATA 'CDT-AutoCAD\run-supervisor-rc3.ps1';
    $guide=Join-Path $env:LOCALAPPDATA 'CDT-AutoCAD\v0.4.0rc3-bdd306c8\docs\TOOL_GUIDE.md';
    $content=[IO.File]::ReadAllText($guide).Replace("`r`n","`n");
    $hash=([BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($content)))).Replace('-','').ToLowerInvariant();
    $active=$false;try{$active=((quser 2>$null|Out-String) -match 'truon\s+console\s+1\s+Active')}catch{}
    [pscustomobject]@{action_count=$actions.Count;action_sha=$actionHash;user=$env:USERNAME;interactive_session=$active;task_state=[string]$t.State;task_user=$t.Principal.UserId;task_logon=[string]$t.Principal.LogonType;task_runlevel=[string]$t.Principal.RunLevel;launcher_sha=(Get-FileHash $launcher -Algorithm SHA256).Hash;installed=(Test-Path 'C:\Program Files\Autodesk\AutoCAD 2027\acad.exe');application=@($acad);listeners=@($tcp);expected_contract_hash=$hash}|ConvertTo-Json -Depth 5
    """)


def status():
    value = inventory()
    value["engine"] = "autocad"
    value["provider_running"] = bool(value["listeners"])
    if value["provider_running"]:
        try:
            relay = Relay()
            raw = relay.call("system_status")
            help_value = Relay().call("help")
            value["native"] = {
                k: raw.get(k) for k in ("backend", "provider_version", "contract_version",
                                       "timeout_uncertain", "integrity_uncertain", "transaction_depth",
                                       "pending_recovery_count", "uncertain_call_running")
            }
            value["native"]["runtime"] = {k: raw.get("runtime", {}).get(k) for k in ("ready", "state")}
            value["native"]["bridge"] = {k: raw.get("native_bridge", {}).get(k) for k in ("ready", "bridge_version", "session_id")}
            value["contract_hash"] = help_value.get("contract_hash")
            value["contract_version"] = help_value.get("contract_version")
            value["mcp_discoverable"] = True
        except Exception:
            value["mcp_discoverable"] = False
    else:
        value["mcp_discoverable"] = False
    return value


def main(action):
    if action == "status":
        return status()
    if action == "start":
        observed = inventory()
        if observed["user"].lower() != "truon" or not observed["interactive_session"]:
            return {"ok": False, "code": "USER_SESSION_REQUIRED"}
        if (observed["task_user"].lower().split('\\')[-1] != "truon"
                or observed["task_logon"] != "Interactive"
                or observed["task_runlevel"] != "Limited"
                or observed["launcher_sha"] != LAUNCHER_SHA
                or observed.get("action_count") != 1
                or observed.get("action_sha") != TASK_ACTION_SHA):
            return {"ok": False, "code": "LAUNCHER_IDENTITY_MISMATCH"}
        if not observed["installed"]:
            return {"ok": False, "code": "APPLICATION_NOT_INSTALLED"}
        if observed["listeners"]:
            return {"ok": True, "state": "ALREADY_RUNNING"}
        if observed["task_state"] == "Disabled":
            return {"ok": False, "code": "TASK_DISABLED"}
        powershell("$ErrorActionPreference='Stop';Start-ScheduledTask -TaskName 'CDT-AutoCAD-RC3-Supervisor';@{ok=$true;state='START_REQUESTED'}|ConvertTo-Json")
        return {"ok": True, "state": "START_REQUESTED",
                "user_application_preexisting": bool(observed["application"])}
    if action == "stop":
        # The existing launcher has no certified remote drain/shutdown contract.
        # Ending its task can terminate child CAD processes and lose user work.
        return {"ok": False, "state": "BLOCKED", "code": "NATIVE_STOP_NOT_CERTIFIED",
                "reason": "Existing interactive launcher lacks verified drain and ownership-safe shutdown; no process was terminated."}
    return {"ok": False, "code": "INVALID_ACTION"}


if __name__ == "__main__":
    try:
        if len(sys.argv) != 2 or sys.argv[1] not in {"status", "start", "stop"}:
            raise ValueError("invalid_action")
        print(json.dumps(main(sys.argv[1])))
    except Exception:
        print(json.dumps({"ok": False, "code": "WINDOWS_CONTROL_UNAVAILABLE"}))
        sys.exit(1)
