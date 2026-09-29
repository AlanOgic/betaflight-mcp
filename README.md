# Betaflight MCP Server

A [Model Context Protocol](https://modelcontextprotocol.io/) server that exposes 26 tools to read and configure a Betaflight flight controller over USB serial using the MSP protocol.

> **MCP is not Claude-specific.** Any MCP-compatible client works: Claude Desktop, Cursor, Cline, Continue, custom LLM agents, Alexa skills, or any application using the MCP SDK.

---

## How it works

```
┌────────────────────────┐    MCP (stdio or SSE)    ┌──────────────────────────┐
│    Any MCP Client      │ ◄──────────────────────► │  betaflight-mcp server   │
│  Claude / Cursor / ... │                           │  Python · FastMCP        │
└────────────────────────┘                           └──────────┬───────────────┘
                                                                │  MSP protocol
                                                                │  pyserial · USB
                                                     ┌──────────▼───────────────┐
                                                     │   Flight Controller      │
                                                     │   Betaflight 4.x         │
                                                     └──────────────────────────┘
```

The server translates MCP tool calls into MSP (MultiWii Serial Protocol) frames, sends them to the FC over USB serial, parses the binary response and returns structured JSON.

---

## Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| Python | ≥ 3.10 | 3.12+ recommended |
| Betaflight firmware | ≥ 4.0 (API ≥ 1.40) | On any F4/F7/H7 FC |
| USB cable | — | FC connected to host machine |

---

## Installation

```bash
git clone https://github.com/your-username/betaflight-mcp.git
cd betaflight-mcp

python -m venv .venv

# Linux / macOS
source .venv/bin/activate

# Windows
.venv\Scripts\activate

pip install -r requirements.txt
```

**Linux — serial port permissions** (one-time setup):
```bash
sudo usermod -aG dialout $USER
# then log out and back in
```

---

## Configuration

All settings are controlled via **environment variables** — no config file to edit.

| Variable | Default | Description |
|----------|---------|-------------|
| `BETAFLIGHT_PORT` | `/dev/ttyUSB0` (Linux) · `COM3` (Windows) | Serial port of the FC |
| `BETAFLIGHT_BAUD` | `115200` | Baud rate (must match Betaflight config) |
| `BETAFLIGHT_TIMEOUT` | `2.0` | Serial read timeout in seconds |
| `BETAFLIGHT_EEPROM_TIMEOUT` | `5.0` | Read timeout for the `save_config` acknowledgement (the FC blocks while writing flash) |

Set them inline or in your shell:
```bash
# inline
BETAFLIGHT_PORT=/dev/ttyACM0 python main.py

# or export
export BETAFLIGHT_PORT=/dev/ttyACM0
export BETAFLIGHT_BAUD=115200
python main.py
```

### Find your FC's serial port

```bash
# Linux
ls /dev/ttyUSB* /dev/ttyACM*

# macOS
ls /dev/tty.usbmodem* /dev/tty.usbserial*

# Windows — PowerShell
[System.IO.Ports.SerialPort]::getportnames()

# Or use the built-in tool (any platform):
python -c "
from server.tools import tool_list_serial_ports
import json; print(json.dumps(tool_list_serial_ports(), indent=2))
"
```

> **Important:** close Betaflight Configurator before starting the MCP server. Both cannot hold the serial port open at the same time.

---

## Client setup

### Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS)
or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "betaflight": {
      "command": "/absolute/path/to/.venv/bin/python",
      "args": ["/absolute/path/to/betaflight-mcp/main.py"],
      "env": {
        "BETAFLIGHT_PORT": "/dev/ttyACM0",
        "BETAFLIGHT_BAUD": "115200"
      }
    }
  }
}
```

Restart Claude Desktop. The 26 tools appear automatically in the tool picker.

### Claude Code (CLI)

```bash
claude mcp add -s user \
  -e BETAFLIGHT_PORT=/dev/ttyACM0 \
  -e BETAFLIGHT_BAUD=115200 \
  -- betaflight \
  /absolute/path/to/.venv/bin/python \
  /absolute/path/to/betaflight-mcp/main.py
```

> **Note :** le `--` doit être placé **avant le nom** (`betaflight`), pas après. Sans lui, le parser variadique de `-e` consomme les arguments suivants.
>
> Scopes disponibles : `local` (projet courant), `project` (`.mcp.json` versionné), `user` (global, tous les projets). Vérifier avec `claude mcp list`.

### Cursor

Add to `.cursor/mcp.json` in your project (or `~/.cursor/mcp.json` globally):

```json
{
  "mcpServers": {
    "betaflight": {
      "command": "/absolute/path/to/.venv/bin/python",
      "args": ["/absolute/path/to/betaflight-mcp/main.py"],
      "env": {
        "BETAFLIGHT_PORT": "/dev/ttyACM0"
      }
    }
  }
}
```

### Cline / Continue (VS Code)

Same JSON format as above, placed in the respective extension's MCP server config.

### Custom agent (SSE / HTTP transport)

For any client that talks to an HTTP endpoint instead of launching a subprocess:

```bash
# Start server in SSE mode (default port 8000)
BETAFLIGHT_PORT=/dev/ttyACM0 python -c "
from main import app
app.run(transport='sse', host='127.0.0.1', port=8000)
"
```

Then point your client at `http://127.0.0.1:8000/sse`.

### MCP SDK (Python / TypeScript)

```python
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

server_params = StdioServerParameters(
    command="python",
    args=["/path/to/betaflight-mcp/main.py"],
    env={"BETAFLIGHT_PORT": "/dev/ttyACM0"},
)

async with stdio_client(server_params) as (read, write):
    async with ClientSession(read, write) as session:
        await session.initialize()
        result = await session.call_tool("connect", {"port": "/dev/ttyACM0"})
        result = await session.call_tool("get_fc_status", {})
```

---

## Available tools

All read operations are safe at any time. Write operations (`set_*`) require calling `save_config` afterwards to persist changes to EEPROM.

### Connection

| Tool | Parameters | Description |
|------|-----------|-------------|
| `list_serial_ports` | — | List all serial ports on the host |
| `connect` | `port`, `baudrate` | Open serial connection to the FC |
| `disconnect` | — | Close the connection |

### FC identity

| Tool | MSP | Description |
|------|-----|-------------|
| `get_board_info` | BOARD_INFO · FC_VARIANT · FC_VERSION · API_VERSION | Firmware variant (BTFL), version, target name, MCU type |

### Telemetry

| Tool | MSP | Description |
|------|-----|-------------|
| `get_fc_status` | STATUS_EX (150) | Cycle time, CPU load, arming flags, profiles |
| `get_imu_data` | RAW_IMU (102) | Accelerometer (g), gyroscope (°/s), magnetometer |
| `get_attitude` | ATTITUDE (108) | Roll / pitch / yaw in degrees |
| `get_altitude` | ALTITUDE (109) | Altitude (m) and variometer (cm/s) |
| `get_rc` | RC (105) | All RC channel values (µs) |
| `get_motors` | MOTOR (104) | Motor outputs (µs) |

### Battery

| Tool | MSP | Description |
|------|-----|-------------|
| `get_battery` | ANALOG (110) | Voltage (V), current (A), mAh drawn, RSSI |
| `get_battery_state` | BATTERY_STATE (130) | Cell count, capacity, state (OK / WARNING / CRITICAL) |
| `get_voltage_meters` | VOLTAGE_METERS (128) | All voltage meters |
| `get_current_meters` | CURRENT_METERS (129) | All current meters |

### PID tuning

| Tool | MSP | Parameters | Description |
|------|-----|-----------|-------------|
| `get_pid_values` | PID (112) | — | P/I/D per axis (roll, pitch, yaw, level, mag) |
| `set_pid_values` | SET_PID (202) | `axis`, `p`, `i`, `d` | Write P/I/D for one axis (0–250); other axes are preserved |
| `get_rates` | RC_TUNING (111) | — | Per-axis rates in the configurator units of the active rates type (Betaflight, Raceflight, KISS, Actual, Quick), full-stick max °/s, throttle curve |
| `set_rates` | SET_RC_TUNING (204) | `{roll,pitch,yaw}_{rc_rate,rate,expo}`, `throttle_mid`, `throttle_expo` | Per-axis update in the active rates type's units, checked against firmware limits; returns read-back values |
| `get_pid_advanced` | PID_ADVANCED (94) | — | Feedforward, anti-gravity, TPA, D-Max, iterm relax |

### Configuration

| Tool | MSP | Description |
|------|-----|-------------|
| `get_modes` | MODE_RANGES (34) | AUX switch assignments (box_id, channel, µs range) |
| `get_feature_config` | FEATURE_CONFIG (36) | Enabled features (AIRMODE, GPS, LED_STRIP…) |
| `get_advanced_config` | ADVANCED_CONFIG (90) | ESC protocol (DSHOT), gyro/PID denominators, PWM rate |
| `get_filter_config` | FILTER_CONFIG (92) | Gyro/Dterm lowpass, notch filters, RPM filter |
| `get_sensor_config` | SENSOR_CONFIG (96) | Accelerometer, barometer, magnetometer hardware |

### System

| Tool | MSP | Description |
|------|-----|-------------|
| `save_config` | EEPROM_WRITE (250) | Persist current config to EEPROM — **always call after set_*** |
| `reboot_fc` | SET_REBOOT (68) | Reboot the flight controller |

---

## Testing

### Unit tests (no FC required)

```bash
python -m pytest tests/test_msp.py -v
# 34 tests — MSP v1/v2 framing, CRC, all response parsers
```

### Interactive inspector (no FC required)

```bash
pip install "mcp[cli]"   # one-time
mcp dev main.py
# opens http://localhost:5173 — call any tool from the browser
```

### End-to-end with a real FC

```bash
# Quick smoke test
BETAFLIGHT_PORT=/dev/ttyACM0 python3 - <<'EOF'
import json
from server.tools import tool_connect, tool_get_fc_status, tool_get_battery, tool_disconnect

print(json.dumps(tool_connect("/dev/ttyACM0"), indent=2))
print(json.dumps(tool_get_fc_status(), indent=2))
print(json.dumps(tool_get_battery(), indent=2))
tool_disconnect()
EOF
```

---

## Project structure

```
betaflight-mcp/
├── main.py                      # Entry point — FastMCP app, tool registration
├── requirements.txt
├── pyproject.toml
│
├── betaflight/
│   ├── msp_codes.py             # All MSP command codes (v1 + v2)
│   ├── msp.py                   # MSP v1 / v2 framing, CRC8/DVB-S2, serial I/O
│   ├── commands.py              # Response parsers (DataReader), all 25+ commands
│   └── serial_conn.py           # pyserial wrapper
│
├── server/
│   ├── tools.py                 # MCP tool functions + MCP_TOOLS registry
│   └── server.py                # Standalone JSON-RPC server (fallback, no SDK dep)
│
├── config/
│   └── settings.py              # Env-var based configuration
│
├── tests/
│   └── test_msp.py              # 34 unit tests (mock serial)
│
└── examples/
    └── claude_desktop_config.json
```

---

## Safety

> ⚠ **Never arm the FC via MCP with propellers attached.**

- All `set_*` operations are **not** automatically saved — always call `save_config` to persist
- `set_motor` sends raw PWM values directly to ESCs — only use with props off and FC in motor-test mode
- The server has no authentication — only expose SSE transport on trusted networks

---

## Troubleshooting

**`SerialException: [Errno 16] Device or resource busy`**
→ Close Betaflight Configurator (or any other app using the port).

**`SerialException: [Errno 2] No such file or directory`**
→ Wrong port. Run `list_serial_ports` or `ls /dev/ttyUSB* /dev/ttyACM*` to find the correct one.

**`connect` succeeds but all `get_*` return `None`**
→ Check baudrate. Betaflight default is 115200. Verify in Betaflight Configurator → Ports tab → USB VCP.

**Timeout on read (`BETAFLIGHT_TIMEOUT=2.0`)**
→ Increase timeout: `BETAFLIGHT_TIMEOUT=5.0`. Can happen on busy systems or slow FCs.

**Permission denied on Linux**
→ `sudo usermod -aG dialout $USER`, then log out and back in.

**`api_version` shows `0.0` after connect**
→ The FC didn't respond to `MSP_API_VERSION`. Check that the FC is powered (USB provides power but some FCs need a battery for full boot).

---

## License

MIT — see [LICENSE](LICENSE).
