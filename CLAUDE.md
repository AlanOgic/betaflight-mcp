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

Config is env vars only (`config/settings.py`): `BETAFLIGHT_PORT` (unset = `connect` auto-detects the single Betaflight FC over USB, see `serial_conn.is_betaflight_port`), `BETAFLIGHT_BAUD` (115200), `BETAFLIGHT_TIMEOUT` (2.0 s), `BETAFLIGHT_EEPROM_TIMEOUT` (5.0 s, read timeout for the EEPROM-write ack), `BETAFLIGHT_MAX_SAMPLING_S` (60 s, upper bound of the RC sampling tools' `duration_s`, since they block the server while sampling). Betaflight Configurator must be closed, since both can't hold the port.

## Architecture

The flow is layered. Each layer only talks to the one below it:

```
main.py (FastMCP)  →  server/tools.py (MCP_TOOLS registry)  →  betaflight/commands.py (BetaflightCommands)
                                                             →  betaflight/msp.py (MSPProtocol)  →  betaflight/serial_conn.py (pyserial)
```

- **`main.py`**: iterates `MCP_TOOLS` and calls `app.add_tool(fn, name, description, annotations)`. FastMCP builds each tool's input schema **only from the Python function signature**: `Annotated[..., Field(description=..., ge=..., le=...)]` gives the parameter descriptions and bounds clients see, and out-of-schema calls are rejected with a `ToolError` before the function runs.
- **`server/tools.py`**: `tool_*` functions plus the `MCP_TOOLS` dict (`fn`, `description`, `annotations`). Shared parameter types (`RcBaseline`, `PidAxis`, `_duration()`, `_pid_gain()`) and the MCP `ToolAnnotations` presets (`_READ_ONLY`, `_CONNECTION`, `_FC_WRITE`, `_FC_REBOOT`) live at the top of the file. The connection state lives in module globals `_conn`, `_msp` and `_bf`. They are only replaced under `_state_lock`. `tool_connect` first closes any previous connection, then accepts the device only if it answers MSP, reports variant `BTFL` and API ≥ `MIN_API_VERSION` (`_identify`); otherwise it closes the port and returns `success: False`. `_close_connection()` closes the port inside `msp.transaction()`, so an in-flight request finishes first. A successful `tool_reboot_fc` also closes the connection, because the USB port re-enumerates. `_get_bf()` raises if the server isn't connected. By convention a tool returns a dict, and on failure returns `{"error": ...}` (for writes: `{"success": False, "errors": [...]}` on validation failure, `{"success": False, "error": ...}` when the FC doesn't ack) instead of raising.
- **`server/validators.py`**: bounds checks run before writes (`validate_pid`, `validate_rates`). They return `{"errors", "warnings"}`. Valid PID axes come from `commands.PID_AXES` (firmware 4.x order: roll, pitch, yaw, level, mag; PID_ITEM_COUNT=5). Hard limits mirror firmware constants (PID_GAIN_MAX=250). `validate_rates(current, updates)` takes the `get_rates()` view because units and limits depend on `rates_type`; it rejects values above the per-type firmware limit or below its resolution (a non-zero value that would round to 0 usually means units from another rates type) and warns above 1800 °/s at full stick (configurator threshold) or when a value gets rounded. Warnings are passed through to the tool result.
- **`betaflight/commands.py`**: `BetaflightCommands` holds one method per MSP command. Parsing mirrors betaflight-configurator's `MSPHelper.js`. `_DataReader` reads fields in sequence and **returns 0 when it reads past the end**, so optional or newer fields degrade silently. Gate version-dependent fields with `self._api_gte(major, minor)` / `_api_lt`, or with `d.remaining` checks. `self.api_version` is set only by `get_api_version()`. Higher-level RC analysis also lives here (`measure_rc_noise`, `detect_rc_mapping`, `detect_rc_channel_move`, which poll MSP_RC for a duration) along with `RC_CHANNEL_NAMES`.
- **`betaflight/msp.py`**: MSP framing. It sends **v1** (`$M<`, XOR checksum) when `cmd <= 255` and **v2** (`$X<`, CRC8/DVB-S2) otherwise, and parses both kinds of response. `request(cmd, payload, timeout=None)` is the only transaction entry point: it holds an `RLock`, sends, then returns the first reply whose `cmd` matches, skipping up to `MAX_STALE_FRAMES` late replies to other commands. Every reply carries `ok` (`>` = ack, `!` = FC error). On a bad preamble it resyncs byte by byte (up to `MAX_RESYNC_BYTES`). It reads in chunks: 3-byte preamble+direction, then v1 meta (2) or v2 header (5), then payload+crc; tests mirror that split.
- **`betaflight/rates.py`**: firmware knowledge for the 5 rates types (`RatesType`): raw↔display scaling identical to the configurator, per-type raw limits (`ratesSettingLimits`, which the firmware only enforces at boot, not on MSP writes), configurator labels, and `max_rate_dps()` (the `fc/rc.c` curves at full stick, clamped by `rate_limit`).
- **`betaflight/msp_fields.py`**: table-driven MSP payloads. A `Field` has offset, width, sign, firmware range, optional enum labels, `count` (arrays, CLI value `"a,b,c"`) and `legacy_u8_offset` (a second, legacy u8 copy of the same value). `read`/`to_raw`/`patch` are shared by `pid_advanced` and `filter_config`; `BetaflightCommands._patch_and_write()` does the length-preserving read-modify-write.
- **`betaflight/filter_config.py`**: MSP_FILTER_CONFIG table keyed by CLI names. Bytes 37-38 are deprecated padding (an older parser read one byte there and shifted every later field). `gyro_lpf1_static_hz` is the u16 at 20-21, with a legacy u8 copy at byte 0 that `patch` keeps consistent. API 1.48 appends `rpm_filter_fade_range_hz`, `rpm_filter_q` and `rpm_filter_weights`. Writes from API 1.47. The firmware runs `validateAndFixGyroConfig()` after a write, so always report the read-back values.
- **`betaflight/pid_advanced.py`**: table of the MSP_PID_ADVANCED fields keyed by Betaflight CLI names (offset, width, sign, firmware range, enum labels). Byte layout is identical from 4.5.2 to 2026.6.2; bytes 39-43 are `d_min_*` up to API 1.46 and `d_max_*` from API 1.47 (`D_MAX_API`). Writes are allowed from API 1.47 (`WRITE_MIN_API`), where ranges were verified against `cli/settings.c`. `simplified_pids_mode` lives in MSP_SIMPLIFIED_TUNING (byte 0) and is written first, so the firmware stops recomputing PIDs from the sliders (a CLI `batch end` re-applies simplified tuning).
- **`betaflight/msp_codes.py`**: `MSPCodes` constants. Many codes are defined but not wired to tools yet.

### Write semantics

- Reads go through `BetaflightCommands._req()` (returns `None` on an error reply). Writes go through `_write()`, which returns `True` only when the FC acks. Never call `msp.send_command()` directly. Tool wrappers turn a `False` write into `{"success": False, "error": ...}`. The FC refuses `MSP_EEPROM_WRITE` while armed. `set_pid_values` patches a copy of the exact MSP_PID bytes it read and refuses to write if fewer than 5 axes came back (the firmware zeroes any missing bytes). `set_rates(updates, expected_rates_type)` works the same way on `MSP_RC_TUNING` (byte offsets in `_RC_TUNING_OFFSETS`): per-axis fields only, and it refuses if the FC's rates type changed since validation or if a field is absent from that firmware's payload.
- **Arming guard:** every write method (`set_pid_values`, `set_rates`, `save_config`, `reboot_fc`, `set_motor`) calls `_require_disarmed()` first, inside its `msp.transaction()`. It reads bit 0 of the MSP_STATUS(_EX) mode flags (BOXARM is always the first active box, see firmware `msp_box.c`) and raises `WriteBlockedError` when the FC is armed or the state is unreadable. Tools map that to `{"success": False, "error": ...}` via `_guarded_write()`. `set_raw_rc` is deliberately unguarded: MSP RC override is meant to be used while armed. Tests that aren't about the guard get a disarmed FC from the autouse fixture in `tests/conftest.py`; mark a test `@pytest.mark.arming_guard` to exercise the real guard.
- Writes are not persisted until `save_config` (MSP_EEPROM_WRITE) is called. Tool descriptions tell the LLM this.
- `set_motor` / `set_raw_rc` exist in `BetaflightCommands` but aren't exposed as tools, for safety.

### Adding a tool

1. Add the parser/command method to `BetaflightCommands` (MSP code from `MSPCodes`).
2. Add a `tool_*` wrapper in `server/tools.py` that uses `_get_bf()` and returns `result or {"error": ...}`.
3. Type every parameter with `Annotated[..., Field(description=..., bounds)]` (`tests/test_tool_schemas.py` fails on a parameter without a description), then register it in `MCP_TOOLS` with a description and the matching `annotations` preset.
4. Add tests. The tool count and tool tables in `README.md` are maintained by hand.

## Testing pattern

Tests build a real `MSPProtocol` + `BetaflightCommands` on a `MagicMock` connection. They feed response frames through `conn.read.side_effect`, split to match how `read_response` reads, e.g. `[frame[:3], frame[3:5], frame[5:]]` for v1. Tool-level tests swap in `server.tools._bf` temporarily and restore it afterwards. See `tests/test_snapshot_rc_delta.py` for the helpers (`v1_frame`, `make_rc_frame`).

## Conventions

- Code comments, docstrings and user-facing tool strings/descriptions are in **French**. Follow this in new code.
- Aligned `=` / `:` column formatting is used throughout, so keep it when editing nearby lines.
- `NOTE.md` is an early design note (French). Its project tree is outdated (`mcp/` is now `server/`).
