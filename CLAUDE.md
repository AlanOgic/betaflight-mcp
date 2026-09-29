# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An MCP server (Python, FastMCP) that exposes tools to read and configure a Betaflight flight controller over USB serial using the MSP protocol. Target: Betaflight 4.x, API ≥ 1.40. Python ≥ 3.10.

## Commands

```bash
# Setup (pytest is not in requirements.txt; CI installs it separately)
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt pytest

# Tests (no FC required, serial is mocked). CI runs this on Python 3.10–3.13
python -m pytest tests/ -v
python -m pytest tests/test_msp.py -v                      # one file
python -m pytest tests/test_validators.py -k "pid" -v      # one test / filter

# Run the server (stdio)
BETAFLIGHT_PORT=/dev/ttyACM0 python main.py

# Interactive inspector (needs `pip install "mcp[cli]"`)
mcp dev main.py
```

No linter or type checker is configured.

Config is env vars only (`config/settings.py`): `BETAFLIGHT_PORT`, `BETAFLIGHT_BAUD` (115200), `BETAFLIGHT_TIMEOUT` (2.0 s), `BETAFLIGHT_EEPROM_TIMEOUT` (5.0 s, read timeout for the EEPROM-write ack). Betaflight Configurator must be closed, since both can't hold the port.

## Architecture

The flow is layered. Each layer only talks to the one below it:

```
main.py (FastMCP)  →  server/tools.py (MCP_TOOLS registry)  →  betaflight/commands.py (BetaflightCommands)
                                                             →  betaflight/msp.py (MSPProtocol)  →  betaflight/serial_conn.py (pyserial)
```

- **`main.py`**: iterates `MCP_TOOLS` and calls `app.add_tool(fn, name, description)`. FastMCP infers each tool's input schema from the **Python function signature** (type hints and defaults), not from the `parameters` dict.
- **`server/tools.py`**: `tool_*` functions plus the `MCP_TOOLS` dict (`fn`, `description`, `parameters`, optional `required`). The connection state lives in module globals `_conn`, `_msp` and `_bf`. `tool_connect` creates them and calls `get_api_version()`. `_get_bf()` raises if the server isn't connected. By convention a tool returns a dict, and on failure returns `{"error": ...}` (for writes: `{"success": False, "errors": [...]}` on validation failure, `{"success": False, "error": ...}` when the FC doesn't ack) instead of raising.
- **`server/server.py`**: a standalone JSON-RPC stdio fallback that doesn't depend on the MCP SDK. It is the only consumer of the `parameters`/`required` metadata. Keep that metadata in sync with the function signatures so both entry points stay consistent.
- **`server/validators.py`**: bounds checks run before writes (`validate_pid`, `validate_rates`). They return `{"errors", "warnings"}`. Hard limits mirror firmware constants (PID_GAIN_MAX=250, RC rates ≤ 2.55, expo ≤ 1.0). Warnings are soft thresholds that are passed through to the tool result.
- **`betaflight/commands.py`**: `BetaflightCommands` holds one method per MSP command. Parsing mirrors betaflight-configurator's `MSPHelper.js`. `_DataReader` reads fields in sequence and **returns 0 when it reads past the end**, so optional or newer fields degrade silently. Gate version-dependent fields with `self._api_gte(major, minor)` / `_api_lt`, or with `d.remaining` checks. `self.api_version` is set only by `get_api_version()`. Higher-level RC analysis also lives here (`measure_rc_noise`, `detect_rc_mapping`, `detect_rc_channel_move`, which poll MSP_RC for a duration) along with `RC_CHANNEL_NAMES`.
- **`betaflight/msp.py`**: MSP framing. It sends **v1** (`$M<`, XOR checksum) when `cmd <= 255` and **v2** (`$X<`, CRC8/DVB-S2) otherwise, and parses both kinds of response. `request(cmd, payload, timeout=None)` is the only transaction entry point: it holds an `RLock`, sends, then returns the first reply whose `cmd` matches, skipping up to `MAX_STALE_FRAMES` late replies to other commands. Every reply carries `ok` (`>` = ack, `!` = FC error). On a bad preamble it resyncs byte by byte (up to `MAX_RESYNC_BYTES`). It reads in chunks: 3-byte preamble+direction, then v1 meta (2) or v2 header (5), then payload+crc; tests mirror that split.
- **`betaflight/msp_codes.py`**: `MSPCodes` constants. Many codes are defined but not wired to tools yet.

### Write semantics

- Reads go through `BetaflightCommands._req()` (returns `None` on an error reply). Writes go through `_write()`, which returns `True` only when the FC acks. Never call `msp.send_command()` directly. Tool wrappers turn a `False` write into `{"success": False, "error": ...}`. The FC refuses `MSP_EEPROM_WRITE` while armed. `set_rates` does a read-modify-write: `get_rates()`, merge, then re-encode the full `MSP_SET_RC_TUNING` payload.
- Writes are not persisted until `save_config` (MSP_EEPROM_WRITE) is called. Tool descriptions tell the LLM this.
- `set_motor` / `set_raw_rc` exist in `BetaflightCommands` but aren't exposed as tools, for safety.

### Adding a tool

1. Add the parser/command method to `BetaflightCommands` (MSP code from `MSPCodes`).
2. Add a `tool_*` wrapper in `server/tools.py` that uses `_get_bf()` and returns `result or {"error": ...}`.
3. Register it in `MCP_TOOLS` with a description and `parameters` that match the signature.
4. Add tests. The tool count and tool tables in `README.md` are maintained by hand.

## Testing pattern

Tests build a real `MSPProtocol` + `BetaflightCommands` on a `MagicMock` connection. They feed response frames through `conn.read.side_effect`, split to match how `read_response` reads, e.g. `[frame[:3], frame[3:5], frame[5:]]` for v1. Tool-level tests swap in `server.tools._bf` temporarily and restore it afterwards. See `tests/test_snapshot_rc_delta.py` for the helpers (`v1_frame`, `make_rc_frame`).

## Conventions

- Code comments, docstrings and user-facing tool strings/descriptions are in **French**. Follow this in new code.
- Aligned `=` / `:` column formatting is used throughout, so keep it when editing nearby lines.
- `NOTE.md` is an early design note (French). Its project tree is outdated (`mcp/` is now `server/`).
