# Security Policy

CDT-AutoCAD controls a live CAD application and can mutate engineering data. Security reports are therefore handled as correctness and data-integrity reports, not only as conventional web vulnerabilities.

## Supported version

| Version | Security support |
| --- | --- |
| `0.4.0rc1` | Supported operational RC |
| Older snapshots / historical checkpoints | Not supported unless reproduced on the current contract |

The primary certified live lane is AutoCAD 2027 full on Windows x64. A behavior reproduced only on another AutoCAD release, backend, or unsupported capability should identify that environment explicitly.

## Reporting a vulnerability

Do not publish an exploitable issue, credential, private DWG, customer file, recovery journal, or sensitive path in a public issue.

Preferred reporting path:

1. use GitHub private vulnerability/security-advisory reporting for this repository when available;
2. otherwise contact the repository owner `SlncTrZ` privately through GitHub before sharing reproduction material;
3. provide the smallest synthetic fixture that reproduces the problem.

There is no contractual response-time SLA. High-impact data-integrity or arbitrary-execution issues are treated as release blockers until understood and contained.

## High-priority security classes

Reports are especially important when they involve:

- authentication or authorization bypass;
- filesystem/path containment escape;
- arbitrary shell, AutoLISP, macro, C# or free-text AutoCAD command execution;
- mutation of the wrong document/session;
- PID/fingerprint identity confusion or state-drift bypass;
- false `COMMITTED_VERIFIED` / `ROLLED_BACK_VERIFIED` claims;
- unsafe retry after timeout or unknown completion;
- native IPC spoofing, replay, cross-user or cross-session execution;
- recovery journal/checkpoint corruption that permits unsafe continuation;
- secret or private drawing-data disclosure;
- unbounded request, metadata, queue, snapshot or artifact growth.

The active threat model is `docs/THREAT_MODEL.md`. Materially new risky capabilities require a threat-model delta before public promotion.

## Disclosure and test-data rules

Use synthetic drawings wherever possible. Remove credentials, customer/project names, private paths and proprietary drawing content from reports. Do not test destructive cases against production/customer drawings; use disposable fixtures and preserve before/after evidence.

## Security boundary

Third-party libraries, Autodesk software and managed assemblies remain under their own security and support processes. CDT-AutoCAD security claims apply only to the exact provider/backend/version/scope supported by project evidence.
