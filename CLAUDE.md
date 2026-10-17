# 鸿雁 · 工作邮箱 — project guide

A Windows desktop client for the company Coremail server `https://newmail.inspur.com/coremail/`.
Python 3.12 + PySide6 (Qt WebEngine). The web UI talks to a Qt-free backend through QWebChannel.

## Layout

| Path | What |
| --- | --- |
| `coremail_desktop/app/` | Daily-use app: `backend.py` (Qt-free), `cache.py` (AES-GCM/DPAPI SQLite), `shell.py` (Qt host, tray, bridge), `push.py` client in `protocol/`, `web/` (HTML/CSS/JS UI), `demo.py` |
| `coremail_desktop/protocol/` | Verified Coremail transport/adapter (`coremail.py`), push channel (`push.py`) |
| `coremail_desktop/{crypto,mime,compose,outbox,drafts,storage}/` | S/MIME, MIME parsing/sanitizing, compose/encrypted send, durable outbox, DPAPI records |
| `coremail_desktop/ui/` | Old Qt Widgets UI (`--classic`) and shared `messages.py` (error-code → Chinese text) |
| `probe/` | Original connectivity probe; its transport is imported by the protocol layer |
| `tests/` | unittest suite |
| `packaging/` | PyInstaller spec, `build.ps1` (build + checks + Inno Setup installer), `hongyan.iss` |
| `tools/` | Dev tools: `build_brand_icon.py`, `screenshot_app.py`, `render_ui_preview.py` |
| `tools/verify/` | Real-account verifiers (read-only or send-to-self) |
| `tools/probes/` | Live server shape probes |
| `tools/research/` | Reverse-engineering / log-analysis scripts, Ghidra scripts |
| `tools/legacy/` | Early root scripts (`run_probe.cmd`, `inspect_*.py`) |
| `docs/research/` | 01–06 reverse-engineering reports |
| `docs/releases/` | Dated progress/verification notes |
| `analysis/` | Evidence data written by probes (JSON is git-ignored) — scripts write here, keep the path |
| `HongyanData/`, `build/`, `dist/`, `.tools/`, `.venv/` | Local/generated, git-ignored |

## Commands

```powershell
.venv\Scripts\python.exe -u -m unittest discover -s tests      # full suite (needs the tool sandbox disabled: Qt WebEngine)
run_desktop.cmd                                                  # launch (from Explorer — see machine limits)
.venv\Scripts\python.exe -m coremail_desktop --demo             # synthetic data, no network
powershell -File packaging\build.ps1                            # package + checks + installer
.venv\Scripts\python.exe -m tools.verify.verify_daily_real      # real-account check (password dialog)
```

## Rules

- Real-account work is read-only or send-to-self only. The user types passwords in a local dialog; never read `PASSWD.txt`, `.pfx` passwords or official-client tokens. Record only shapes, counts and booleans — never mail content or addresses.
- Probe any new server API read-only first and save the shape to `analysis/`. Verified API facts live in the release notes; do not trust `docs/research/06-待验证问题.md` "unverified" labels without checking them.
- Encryption: colleagues (account's own domain) encrypted by default, external recipients off by default; never downgrade to plaintext silently.
- Errors cross to the UI only as fixed codes (`ui/messages.py`); never echo server or exception text.
- Match the existing compact code style. Commit only when asked; never push.

## Machine limits (this PC)

- Python cannot write the user profile (AppData, Documents, TEMP) → data falls back to `HongyanData/` beside the program; tests/tools must keep temp files inside the repo and clean them up.
- Chromium sandbox fails → app relaunches in compatibility mode automatically.
- Processes started from Claude are further restricted: let the user launch the app from Explorer for real checks. Qt runs from tools need `QTWEBENGINE_DISABLE_SANDBOX=1` in the driver only.
- Inline shell heredocs mangle backslashes — write edit scripts to files.
