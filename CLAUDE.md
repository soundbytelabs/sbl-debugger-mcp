# sbl-debugger MCP Server — Agent Guide

ARM Cortex-M hardware debugger via GDB + OpenOCD. Attach to a target, flash firmware, set breakpoints, step through code, inspect registers/memory/peripherals.

## Tool Overview

| Tool | Purpose |
|------|---------|
| `debug_attach` | Connect to target (starts OpenOCD + GDB) |
| `debug_detach` | Disconnect and clean up |
| `debug_sessions` | List active sessions |
| `debug_status` | Check target state (halted/running) |
| `debug_snapshot` | **Complete state in one call** — frame, registers, backtrace, locals, source |
| `load` | Flash ELF to target |
| `halt` | Stop execution (GDB interrupt, falls back to OpenOCD TCL) |
| `continue_execution` | Resume execution |
| `wait_for_halt` | Block until target stops (breakpoint, watchpoint, etc.) |
| `step` / `step_over` / `step_out` / `step_instruction` | Step through code |
| `run_to` | Run to a specific function/line/address |
| `reset` | Reset target (optionally halt after reset) |
| `breakpoint_set` / `breakpoint_delete` / `breakpoint_list` | Manage breakpoints |
| `watchpoint_set` | Hardware watchpoint on memory/variable |
| `read_registers` / `write_register` | CPU register access |
| `read_memory` / `write_memory` | Raw memory access |
| `read_locals` | Local variables in current frame |
| `print_expr` | Evaluate C/C++ expression (struct fields, array indexing, pointer deref) |
| `backtrace` | Call stack |
| `disassemble` | Disassemble at address or current PC |
| `list_peripherals` / `list_registers` / `read_peripheral_register` / `read_peripheral` | SVD-decoded peripheral inspection |
| `monitor` | Raw OpenOCD command (escape hatch) |

## Common Workflows

### Basic Debug Session
```
debug_attach(target="daisy", elf="/path/to/firmware.elf")
load(name="daisy")                    # Flash firmware
continue_execution(name="daisy")      # Start running
halt(name="daisy")                    # Stop to inspect
debug_snapshot(name="daisy")          # See everything at once
continue_execution(name="daisy")      # Resume
debug_detach(name="daisy")            # Clean up
```

### Inspect Running Firmware
```
halt(name="daisy")
debug_snapshot(name="daisy")          # Frame, registers, locals, backtrace, source
print_expr(name="daisy", expression="s_filter.cutoff_")
print_expr(name="daisy", expression="s_osc.phase_.value_")
continue_execution(name="daisy")
```

### Breakpoint Debugging
```
load(name="daisy")
breakpoint_set(name="daisy", location="audio_callback")
continue_execution(name="daisy")
wait_for_halt(name="daisy")           # Blocks until breakpoint hit
debug_snapshot(name="daisy")          # Inspect at breakpoint
step_over(name="daisy", count=3)      # Step through code
debug_snapshot(name="daisy")          # Check state after stepping
continue_execution(name="daisy")
```

### Step Through Startup
```
reset(name="daisy", halt=True)
step(name="daisy")                    # Step into Reset_Handler
step_over(name="daisy", count=10)     # Step through init code
debug_snapshot(name="daisy")
```

### Peripheral Inspection
```
list_peripherals(name="daisy", filter="GPIO|RCC|SAI")
read_peripheral_register(name="daisy", peripheral="RCC", register="AHB4ENR")
read_peripheral(name="daisy", peripheral="GPIOB")   # Full register dump with bitfields
```

## Key Tips

- **Use `debug_snapshot` instead of separate calls.** It returns frame + registers + backtrace + locals + source context in one round-trip. Only use individual tools when you need specific things snapshot doesn't provide (like `print_expr` for complex expressions or `read_memory` for raw memory).

- **Use `print_expr` for structured data.** It evaluates C/C++ expressions: `my_struct.field`, `array[i]`, `*ptr`, `(int)register_value & 0xFF`. Much more useful than reading raw memory.

- **Build with `-g` for full symbols.** Without debug info, you get addresses but no variable names, source lines, or struct field access.

- **Predefined targets:** `daisy` (STM32H750/Cortex-M7), `pico` (RP2040/Cortex-M0+), `pico2` (RP2350/Cortex-M33). Use `debug_targets()` to list them.

## TCL Fallback and Resync

When `halt` can't stop the target via GDB's `-exec-interrupt` (common when the target is stuck in an ISR or tight loop), it falls back to OpenOCD's TCL port which halts at the SWD level. After TCL fallback:

- The response will include `"method": "openocd_tcl"` and possibly a `"warning"` about GDB desync.
- The server automatically attempts to resync GDB via `monitor halt` (which goes through GDB to OpenOCD, updating GDB's internal state).
- If `continue_execution` returns `"state": "halted"` with a warning, the target didn't actually resume. Call `continue_execution` again — the retry logic will attempt resync.

## Peripheral Tools

Requires `SBL_HW_PATH` environment variable pointing to sbl-hardware repository (for SVD files via cecrops).

- `list_peripherals` — browse available peripherals (regex filter supported)
- `list_registers` — SVD metadata only (no target read), shows register names and field definitions
- `read_peripheral_register` — read one register, decode all bitfields
- `read_peripheral` — bulk read all registers of a peripheral (fast for compact peripherals)
