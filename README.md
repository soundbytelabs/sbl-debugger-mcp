<!-- mcp-name: io.github.soundbytelabs/debugger -->

# sbl-debugger

Embedded debug MCP server for ARM Cortex-M targets. Gives Claude direct control of GDB and OpenOCD — attach to hardware, set breakpoints, step through code, inspect registers and memory, all within a conversation.

Part of the [Sound Byte Labs](https://github.com/soundbytelabs) embedded tooling suite, alongside [sbl-probe](https://github.com/soundbytelabs/mcp-sbl-probe) for serial I/O.

## Quick Start

```bash
# Install (editable, into SBL venv)
pip install -e .

# Or with test dependencies
pip install -e ".[dev]"
```

Register in `.mcp.json` at your workspace root:

```json
{
  "mcpServers": {
    "sbl-debugger": {
      "type": "stdio",
      "command": "/path/to/venv/bin/python",
      "args": ["-m", "sbl_debugger"]
    }
  }
}
```

Restart Claude Code and the tools are available immediately.

### System Requirements

- `gdb-multiarch` (GDB with ARM target support)
- `openocd` (0.12.0+ recommended)
- SWD debug probe (ST-LINK, CMSIS-DAP, etc.)

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
| `daisy` | Daisy Seed (STM32H750, Cortex-M7) | ST-LINK V3 |
| `pico` | Raspberry Pi Pico (RP2040, Cortex-M0+) | CMSIS-DAP Debug Probe |
| `pico2` | Raspberry Pi Pico 2 (RP2350, Cortex-M33) | CMSIS-DAP Debug Probe |
| `custom` | Any target | Provide `interface` and `target_cfg` explicitly |

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
└── tools/
    ├── session.py     # attach, detach, sessions, status, targets
    ├── execution.py   # halt, continue, step, reset
    ├── inspection.py  # registers, memory, backtrace, locals
    ├── breakpoints.py # breakpoint/watchpoint management
    ├── snapshot.py    # combined state dump
    └── advanced.py    # load (flash), monitor (raw OpenOCD)
```

Key design decisions:

- **Managed subprocesses** — OpenOCD and GDB are launched, monitored, and cleaned up by the server. One `debug_attach` call does everything.
- **GDB/MI via pygdbmi** — structured command/response interface, no string parsing of GDB CLI output
- **Single command lock** — one MI command at a time prevents response interleaving
- **Explicit polling** — `wait_for_halt` and `debug_status` check target state on demand, matching MCP's request/response model
- **Error dicts, not exceptions** — tools return `{"error": "..."}` instead of crashing the server

## Running Tests

```bash
pytest                    # 198 tests (all mocked, no hardware needed)
pytest -v                 # verbose
pytest tests/test_tools.py  # just tool tests
```

## Dependencies

- `mcp` — Official Python MCP SDK (FastMCP)
- `pygdbmi` — GDB Machine Interface protocol
- Python >= 3.11
