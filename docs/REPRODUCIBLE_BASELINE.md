# Reproducible Baseline — CDT-AutoCAD

> Baseline: MP0-T00 · Version: 1 · Updated: 2026-09-19 +07:00
> Canonical resolver workflow: **`cdt_autocad.dependency_lock` + pip 26.1.2 `pip lock` → LF-normalized PEP 751 platform lock → install from that lock**.

## 1. Decision

`pyproject.toml` remains the abstract dependency contract. Exact resolution is owned by PEP 751 lock artifacts generated with one canonical resolver workflow. The repository generator derives runtime dependencies, all optional dependency groups, the `dev` group and build-system requirements from `pyproject.toml`, invokes pinned pip `26.1.2`, then normalizes the emitted lock text to LF so Git identity does not depend on the generating OS:

- `pylock.windows.toml` — CPython 3.12 / Windows x64 primary AutoCAD runtime, generated with `.[com,render]` + dependency group `dev`;
- `pylock.linux.toml` — CPython 3.12 / Linux x86_64 CI/gateway runtime, generated with the same extras/group; Windows-only markers are naturally absent.

Two platform files are necessary because pip explicitly guarantees each generated lock only for the Python version/platform on which it was resolved. They are not separate dependency authorities: both are generated from the same `pyproject.toml` by the same pinned resolver command. `uv.lock` is explicitly **not** a release dependency authority in this repository; it is local resolver residue and is ignored.

Do not install project/runtime dependencies from ad-hoc `pip install -e .`, `.deps` contents, a global Python environment, `uv.lock` or an unpinned requirements list and then attach release evidence to that environment.

## 2. Resolver/tool version

Lock generation baseline uses:

```text
Python: 3.12
pip lock tool: 26.1.2
lock format: PEP 751 pylock.toml, lock-version 1.0
```

`pip lock` is marked experimental by pip 26.1.2. Therefore the **tool version is part of the build provenance**. A future pip-lock version change requires regenerating both platform locks, reviewing dependency deltas and recording the change; it is not a transparent upgrade.

## 3. Canonical lock generation

Windows primary target, from repository root with pip `26.1.2` active:

```powershell
$env:PYTHONPATH='src'
python -m cdt_autocad.dependency_lock --repo .
```

Linux CI/gateway, with pip `26.1.2` active:

```bash
PYTHONPATH=src python3 -m cdt_autocad.dependency_lock --repo .
```

The generator chooses the platform lock name, derives resolver inputs from `pyproject.toml`, refuses a different pip version and rewrites generated CRLF/LF to canonical LF. Generation must occur from the same committed `pyproject.toml`. If a dependency declaration changes—or a deliberate re-resolution is requested—both platform locks must be regenerated, dependency deltas reviewed and acceptance rerun before replacement.

## 4. Clean locked install

Create an isolated environment/target and install only from the platform lock. Example Windows venv:

```powershell
python -m venv <temporary-venv>
<temporary-venv>\Scripts\python -m pip install pip==26.1.2
<temporary-venv>\Scripts\python -m pip install -r pylock.windows.toml
```

Linux equivalent:

```bash
python3 -m venv <temporary-venv>
<temporary-venv>/bin/python -m pip install pip==26.1.2
<temporary-venv>/bin/python -m pip install -r pylock.linux.toml
```

A locked-install acceptance run must report the interpreter/platform and verify the key direct dependencies plus project import before its result is used as release evidence.

## 5. Runtime/build provenance manifest

`src/cdt_autocad/provenance.py` generates a JSON manifest that binds. The historical `0.8.2-mp7` maintenance acceptance additionally proved that the Release/x64 candidate hash equaled the bridge DLL observed loaded in AutoCAD Session 1; historical runs lacking a runtime DLL hash are intentionally not rebound retroactively. Current D18 bridge/runtime provenance is recorded in `docs/CURRENT_CHECKPOINT.md` and `docs/LIVE_ACCEPTANCE.md`.

The manifest binds:

- Git HEAD and whether the working tree is dirty;
- SHA-256 of the tracked binary diff;
- content/path-sensitive source-tree SHA-256 for Python provider + native bridge source + pyproject + both locks;
- provider version;
- selected platform lock path/hash and exact package set;
- bridge DLL path/hash when a candidate or installed DLL is supplied;
- AutoCAD certification target and observed `acad.exe` process/session state where Windows can query it;
- Python version, executable, platform, machine and hostname.

Example on Linux:

```bash
PYTHONPATH=src python3 -m cdt_autocad.provenance \
  --output <evidence-dir>/runtime-linux.json
```

Example on the primary Windows host with an installed bridge artifact:

```powershell
$env:PYTHONPATH='<repo-root>\src'
python -m cdt_autocad.provenance `
  --dll "$env:APPDATA\Autodesk\ApplicationPlugins\CDT.AutoCAD.Bridge.bundle\Contents\Windows\CDT.AutoCAD.Bridge.dll" `
  --output <evidence-dir>\runtime-windows.json
```

Provenance output is a maintainer-local observation record, not a published artifact. Published certification claims must be distilled into the canonical current-status/live-acceptance documents rather than making raw manifests part of the public contract.

A manifest records **what was observed**, not certification. A DLL hash that differs from accepted evidence must remain explicitly unaccepted until the corresponding native gate passes.

## 6. Baseline drift found while establishing MP0-T00

Before platform locks were introduced, the Windows global Python environment was not a valid reproducible project environment: observed `pytest-asyncio 1.4.0` exceeded the project constraint `<1`, `pillow 12.2.0` exceeded the COM extra constraint `<12`, and `ezdxf` was absent. Those observations are evidence against using global-package state as a release baseline; they are not dependency defects in the locked environment.

The Windows lock resolves `pytest-asyncio 0.26.0`, `pillow 11.3.0`, `pywin32 312` and the project runtime/development transitive graph. The Linux lock excludes Windows-only `pywin32` by platform marker.

## 7. Acceptance and change policy

MP0-T00 reproducibility is accepted only when:

1. both platform locks parse and satisfy project direct constraints;
2. clean locked installs succeed on Linux and the primary Windows target;
3. project/runtime smoke imports pass from those clean installs;
4. provenance manifests bind exact lock/source/build/DLL/runtime identities;
5. future evidence references the manifest/hash corresponding to the tested runtime;
6. dependency updates are deliberate: change `pyproject.toml` → regenerate both locks → inspect diff → run locked smoke/regression gates → record evidence.

MP-8 rechecks SBOM/license/dependency risk and release artifacts against these same lock/build identities. MP-8 is not the first point where dependency reproducibility is established.

## 8. B2 measured closure — 2026-09-15/16

B2 is **CLOSED / EXACT-LOCK PASS** at release-provenance checkpoint `bcb5c661bc6e01961525cd08c2ac5e0c0714b790`.

Two independent fresh reconstructions were created per platform from the canonical lock only; each pair reproduced the same package map and `pip freeze --all` output and passed the same gates:

| Platform | Canonical lock SHA-256 | Locked packages | Fresh A | Fresh B |
| --- | --- | ---: | --- | --- |
| Linux x86_64 / Python 3.12.3 | `e7fe668b159c56233be4d008600fe80dd0778689a58b48ab55cecc670c45bf06` | 88 | 405 passed / 11 skipped + Ruff PASS | 405 passed / 11 skipped + Ruff PASS |
| Windows x64 `.171` / Python 3.12.0 | `acbbc28bc07d26c2e07c76ab5d24f2e474945ba10cc14a0c9e94cca29867869e` | 89 | 404 passed / 12 skipped + Ruff PASS | 404 passed / 12 skipped + Ruff PASS |

Both platform manifests bind the same clean Git HEAD and source tree:

```text
git_head = bcb5c661bc6e01961525cd08c2ac5e0c0714b790
git_dirty = false
tracked_diff_sha256 = e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
source_tree_sha256 = 3f4b45b13aa8ab4a60f92c02b99c97eff1aca8baf8a9ac84246b41e5d36db3d8
provider_version = 0.4.0rc2
```

Windows provenance additionally binds the installed native bridge and observed AutoCAD process:

```text
bridge_sha256 = 18740fc6cc35a4e9117efed29fc98bd9c2d4836b77140d751e8d1628abd75a43
autocad_pid = 7888
autocad_session = 1
```

The machine-readable manifests used for this historical closure remain maintainer-local evidence; the hashes and verdict above are the public audit summary. A future source, dependency, bridge or runtime identity change requires a fresh provenance run rather than reusing this closure record.
