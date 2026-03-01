<!-- mcp-name: io.github.soundbytelabs/debugger -->

# sbl-debugger

Embedded debug MCP server for ARM Cortex-M targets. Gives AI coding assistants direct control of GDB and OpenOCD — attach to hardware, set breakpoints, step through code, inspect registers and memory, all within a conversation.

Part of the [Sound Byte Labs](https://github.com/soundbytelabs) embedded tooling suite, alongside [sbl-probe](https://github.com/soundbytelabs/sbl-probe-mcp) for serial I/O.

## Installation

Create a virtual environment and install the package:

```bash
python3 -m venv .venv
source .venv/bin/activate

# Install
pip install -e .

# Or with test dependencies
pip install -e ".[dev]"
```

### System Requirements

- `gdb-multiarch` (GDB with ARM target support)
- `openocd` (0.12.0+ recommended)
- SWD debug probe (ST-LINK, CMSIS-DAP, etc.)

On Debian/Ubuntu/Raspberry Pi OS:

```bash
sudo apt install gdb-multiarch openocd
```

## MCP Configuration

Register the server in your MCP client's config. For most clients, add to `.mcp.json` in your project root:

```json
{
  "mcpServers": {
    "sbl-debugger": {
      "type": "stdio",
      "command": "/absolute/path/to/.venv/bin/python",
      "args": ["-m", "sbl_debugger"]
    }
  }
}
```

> **Important:** Use the absolute path to the Python binary inside your virtual environment.
> For example: `/home/you/sbl-debugger-mcp/.venv/bin/python`

Restart your MCP client and the tools are available immediately.

## Tools

### Session Management

| Tool | Description |
|------|-------------|
| `debug_attach` | Attach to a target — launches OpenOCD + GDB, connects via SWD |
| `debug_detach` | Cleanly shut down a debug session |
| `debug_sessions` | List all active debug sessions |
| `debug_status` | Get current target state (halted/running, stop reason, current frame) |
| `debug_targets` | List available predefined target profiles |

### Execution Control

| Tool | Description |
|------|-------------|
| `halt` | Halt a running target |
| `continue_execution` | Resume execution |
| `wait_for_halt` | Block until target stops (breakpoint hit, etc.) |
| `step` | Step source lines (into functions) |
| `step_over` | Step source lines (over function calls) |
| `step_out` | Step out of current function |
| `step_instruction` | Step machine instructions |
| `run_to` | Run to a location (sets temporary breakpoint + continues) |
| `reset` | Reset the target (halt or run after reset) |

### Inspection

| Tool | Description |
|------|-------------|
| `read_registers` | Read CPU registers (all core regs or specific subset) |
| `write_register` | Write a CPU register |
| `read_memory` | Read memory (hex, u8, u16, u32 formats) |
| `write_memory` | Write to memory |
| `backtrace` | Get the call stack |
| `read_locals` | List local variables in current frame |
| `print_expr` | Evaluate a C/C++ expression in target context |
| `disassemble` | Disassemble at an address or current PC |

### Breakpoints

| Tool | Description |
|------|-------------|
| `breakpoint_set` | Set breakpoint by function name, file:line, or address |
| `breakpoint_delete` | Delete a breakpoint |
| `breakpoint_list` | List all breakpoints and watchpoints |
| `watchpoint_set` | Set a hardware data watchpoint (write/read/access) |

### Peripheral Registers (SVD)

| Tool | Description |
|------|-------------|
| `list_peripherals` | List SVD peripherals with base addresses and register counts |
| `list_registers` | Show all registers and bitfield definitions for a peripheral |
| `read_peripheral_register` | Read a register from hardware and decode all bitfields |
| `read_peripheral` | Read all registers of a peripheral with decoded bitfields |

> Requires `cecrops` (optional dependency) and `SBL_HW_PATH` environment variable pointing to sbl-hardware.
> Install with: `pip install -e ".[svd]"`

### Snapshot & Advanced

| Tool | Description |
|------|-------------|
| `debug_snapshot` | Full target state in one call (frame, registers, backtrace, locals, source) |
| `load` | Flash firmware to the target |
| `monitor` | Send raw OpenOCD monitor commands |

## Target Profiles

Predefined profiles eliminate the need to remember OpenOCD configs:

| Profile | Hardware | Debug Probe |
|---------|----------|-------------|
| `daisy` | Electrosmith Daisy Seed (STM32H750, Cortex-M7) | ST-LINK |
| `pico` | Raspberry Pi Pico (RP2040, Cortex-M0+) | CMSIS-DAP Debug Probe |
| `pico2` | Raspberry Pi Pico 2 (RP2350, Cortex-M33) | CMSIS-DAP Debug Probe |
| `custom` | Any target | Provide `interface` and `target_cfg` explicitly |

Custom targets work with any OpenOCD-supported hardware:

```
debug_attach(target="custom", interface="jlink.cfg", target_cfg="stm32f4x.cfg", elf="firmware.elf")
```

## Architecture

```
sbl_debugger/
├── server.py          # FastMCP server, tool wiring
├── targets.py         # Target profiles (daisy, pico, pico2)
├── session/
│   ├── manager.py     # Thread-safe session registry
│   └── session.py     # DebugSession (owns OpenOCD + GDB)
├── process/
│   ├── openocd.py     # OpenOCD subprocess lifecycle
│   └── ports.py       # GDB server port allocation
├── bridge/
│   ├── mi.py          # GDB/MI wrapper (pygdbmi + lock)
│   └── types.py       # FrameInfo, StopEvent, MiResult
├── svd/
│   ├── peripheral_db.py  # SVD lookup/decode (wraps cecrops Device)
│   └── loader.py         # SBL_HW_PATH resolution, cecrops import guard
└── tools/
    ├── session.py     # attach, detach, sessions, status, targets
    ├── execution.py   # halt, continue, step, reset
    ├── inspection.py  # registers, memory, backtrace, locals
    ├── breakpoints.py # breakpoint/watchpoint management
    ├── snapshot.py    # combined state dump
    ├── advanced.py    # load (flash), monitor (raw OpenOCD)
    └── peripheral.py  # SVD peripheral register decoding
```

Key design decisions:

- **Managed subprocesses** — OpenOCD and GDB are launched, monitored, and cleaned up by the server. One `debug_attach` call does everything.
- **GDB/MI via pygdbmi** — structured command/response interface, no string parsing of GDB CLI output
- **Single command lock** — one MI command at a time prevents response interleaving
- **Explicit polling** — `wait_for_halt` and `debug_status` check target state on demand, matching MCP's request/response model
- **Error dicts, not exceptions** — tools return `{"error": "..."}` instead of crashing the server

## Running Tests

```bash
pytest                    # 275 tests (all mocked, no hardware needed)
pytest -v                 # verbose
pytest tests/test_tools.py  # just tool tests
```

## Dependencies

- `mcp` — Official Python MCP SDK (FastMCP)
- `pygdbmi` — GDB Machine Interface protocol
- `cecrops` — SVD register definitions (optional, for peripheral tools)
- Python >= 3.11
