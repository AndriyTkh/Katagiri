# Tool Contract: connection_status

**Stability**: stable once merged (constitution VII — additive-only afterwards).
**Ledger**: D-39 (filed before implementation).

## Registration

- ToolSpec appended via new `_INFRA_007_SPECS` fragment in `src/katagiri/tool_registry.py`
  (concatenated into `TOOL_SPECS`); congruence row appears in `tests/test_mcp_tools.py`.
- Thin adapter `@server.tool(name="connection_status", ...)` in `src/katagiri/mcp_server.py`
  adapter region, `redact()`-wrapped, delegating to a plain logic function.

## Arguments

None.

## Output (JSON object)

```json
{
  "status": "ok",
  "katagiri_version": "0.x.y",
  "python_version": "3.12.x",
  "transport": "stdio",
  "entry_point": "katagiri.mcp_server (python -m) | .venv\\Scripts\\katagiri-mcp.exe",
  "pid": 12345,
  "cwd": "C:\\ProjectsC\\RandomPr\\Katagiri",
  "config_path": "C:\\Users\\me\\AppData\\Local\\Katagiri\\config.toml",
  "config_exists": true,
  "db_path": "C:\\Users\\me\\AppData\\Local\\Katagiri\\katagiri.db",
  "db_available": true,
  "log_file_path": "C:\\Users\\me\\AppData\\Local\\Katagiri\\logs\\katagiri.log",
  "client_info": {"name": "claude-code", "version": "2.x"},
  "secrets": {"obsidian_api_token": "set", "mokuro_shared_secret": "unset"},
  "changed_anything": false
}
```

## Behavioral guarantees

1. Never raises for missing config, unreachable/locked DB, or absent client identity —
   these surface as `config_exists: false`, `db_available: false`,
   `client_info: {"name": "unknown", "version": ""}`.
2. No secret **values** anywhere in the response; secret-bearing config fields appear
   only in the `secrets` presence map.
3. Read-only: `changed_anything` is always `false`; no file, DB, or network mutation.
4. Paths reflect the answering process's actual resolution (env overrides honored), so
   two instances launched from different sandboxes return different maps.
5. Response time < 5 s (in practice: milliseconds; DB check is a bounded open attempt).
