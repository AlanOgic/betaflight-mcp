# Betaflight MCP Server

A [Model Context Protocol](https://modelcontextprotocol.io/) server that bridges any MCP-compatible client (LLM, Alexa skill, custom agent...) with a Betaflight flight controller over USB serial using the MSP protocol.

## Architecture

```
┌─────────────────┐     MCP Protocol     ┌──────────────────────────┐
│   MCP Client    │ ◄──────────────────► │   MCP Server (Python)    │
│ (LLM/Skill/...) │                      │   betaflight_mcp/        │
└─────────────────┘                      └──────────┬───────────────┘
                                                     │ MSP Protocol
                                                     │ (pyserial / USB UART)
                                          ┌──────────▼───────────────┐
                                          │   Flight Controller       │
                                          │   (Betaflight Firmware)   │
                                          └──────────────────────────┘
```

## Requirements

- Python 3.13+
- A Betaflight flight controller connected via USB
- Betaflight firmware configured at 115200 baud

## Installation

```bash
git clone https://github.com/your-username/betaflight-mcp.git
cd betaflight-mcp
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux / macOS
source .venv/bin/activate

pip install -r requirements.txt
```

## Usage

```bash
python main.py
```

The server communicates over **stdio** (MCP standard), making it directly usable with any MCP-compatible client.

To find your flight controller's serial port:

```bash
# Linux
ls /dev/ttyUSB*

# Windows — check Device Manager or:
python -c "import serial.tools.list_ports; [print(p) for p in serial.tools.list_ports.comports()]"
```

## Available MCP Tools

| Tool | MSP Command | Description |
|------|-------------|-------------|
| `list_serial_ports` | — | List available serial ports |
| `connect` | — | Open serial connection to FC |
| `disconnect` | — | Close serial connection |
| `get_fc_status` | MSP_STATUS (101) | Arming flags, cycle time |
| `get_imu_data` | MSP_RAW_IMU (102) | Gyro & accelerometer data |
| `get_battery` | MSP_ANALOG (110) | Voltage, current, mAh |
| `get_pid_values` | MSP_PID (112) | P/I/D values per axis |
| `set_pid_values` | MSP_SET_PID (202) | Write P/I/D values |
| `get_rates` | MSP_RC_TUNING (111) | Rates, expo, throttle curve |
| `set_rates` | MSP_SET_RC_TUNING (204) | Write rates |
| `get_modes` | MSP_MODE_RANGES (34) | Active RC modes |
| `save_config` | MSP_EEPROM_WRITE (250) | Persist config to EEPROM |
| `reboot_fc` | MSP_REBOOT (68) | Reboot the flight controller |

## Safety

> **Warning** — Never arm the flight controller via MCP without physical safety measures in place (props off, FC secured). Always call `save_config` after any `set_*` operation to persist changes.

## Project Structure

```
betaflight-mcp/
├── main.py              # Entry point
├── mcp/
│   ├── server.py        # MCP server loop (stdio)
│   └── tools.py         # MCP tool definitions
├── betaflight/
│   ├── msp.py           # MSP protocol (encode/decode)
│   ├── serial_conn.py   # Serial port management (pyserial)
│   └── commands.py      # High-level commands
└── config/
    └── settings.py      # Port, baudrate, timeouts
```

## Roadmap

- [ ] Full MSP_PID response parsing
- [ ] MCP Server authentication
- [ ] Automatic reconnection
- [ ] Blackbox / OSD tools
- [ ] Unit tests with mock serial

## License

MIT — see [LICENSE](LICENSE).
