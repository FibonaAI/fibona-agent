# Fibona on ESP32 (Experimental)

Fibona's cell loop running on an ESP32 under MicroPython: the program on the board organizes its own work, decides when to call the model, and runs the cells the model writes for it. To understand Fibona itself, start with the [Fibona Agent repository](https://github.com/FibonaAI/fibona-agent).

Tested on a classic ESP32 (ESP32-D0WD-V3, 4 MB flash, no PSRAM).

## Usage

Requires an ESP32 board on USB and [`uv`](https://docs.astral.sh/uv/).

There are two modes; they run the same loop and differ only in how model requests travel.

| | Bridge mode | Standalone mode |
| --- | --- | --- |
| Model requests | Over USB serial, sent on by this computer | Over Wi-Fi, straight from the board |
| API key lives on | This computer | The board |
| Starts with | `make bridge TASK="..."` | Every power-up |
| Output | This terminal | The board's serial console |

1. Create `config.py` in this directory:

   ```bash
   cp config.example.py config.py
   ```

   Then fill it in:

   | Setting | Used in |
   | --- | --- |
   | `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `FIBONA_MODEL` | Both modes |
   | `MAX_OUTPUT`, `MIN_FREE`: history limits, see [Memory](#memory) | Both modes |
   | `WIFI_SSID`, `WIFI_PASSWORD`, `TASK` | Standalone mode |

   The board must be on a 2.4 GHz network. If your serial port is not `/dev/cu.usbserial-0001`, add `PORT=...` to every `make` command (`/dev/ttyUSB0` on Linux, for example).

2. Flash the firmware:

   ```bash
   make flash
   ```

   This **erases the whole board**, then writes MicroPython with Fibona built in. It downloads the firmware from the GitHub release for `VERSION` unless `build/` already has it.

3. Choose a mode and run it from this directory:

   - Bridge mode:

     ```bash
     make bridge TASK="Report the free RAM and CPU frequency, then quit."
     ```

     Cells and their output appear in this terminal. The run ends when the agent calls `quit()`.

   - Standalone mode:

     ```bash
     make standalone
     ```

     This copies `config.py` to the board, plus `standalone.py` saved as `main.py`. From then on the board joins Wi-Fi and runs `TASK` at every power-up, with no computer attached. To watch it:

     ```bash
     uvx mpremote connect /dev/cu.usbserial-0001 repl   # Ctrl-] or Ctrl-X to quit
     ```

### Limitations

- Standalone mode has not been tested end to end yet.
- HTTPS certificates are not verified in standalone mode.
- With no PSRAM, about 100 KB of RAM is left for the agent's work.
- Cells cannot ask for user input.

## Development

Building the firmware also needs [Docker](https://www.docker.com/).

### Files

| File | Role |
| --- | --- |
| `fibona.py` | The agent: `Mind`, `Memory`, `Slot`, `MicroPythonEnv`, both transports, and the bootstrap cell |
| `bridge.py` | Bridge mode on the computer: sends `fibona.py` to the board and forwards its model requests |
| `standalone.py` | Standalone mode on the board: joins Wi-Fi and calls `fibona.main()` |
| `manifest.py` | Firmware manifest: freezes `fibona.py` into MicroPython |
| `config.example.py` | Template for `config.py` |
| `Makefile` | `bridge`, `flash`, `standalone`, `firmware` |

### Build the firmware

```bash
make firmware   # → build/fibona-esp32-$(VERSION).bin
make flash      # Uses the local build when it exists.
```

`make firmware` clones MicroPython `v1.29.0` into `build/micropython` and builds it with ESP-IDF `v5.5.2` in Docker, with `fibona.py` frozen in. The first build pulls a multi-gigabyte image; later builds are incremental.

### Iterate

Bridge mode sends the local `fibona.py` to the board on every run, so changes take effect without rebuilding or reflashing. Rebuild the firmware only for standalone mode, which imports the frozen copy.

Run the host-side loop checks with `python3 -m unittest -v test_fibona`. These simulate MicroPython APIs; they do not exercise the board or network.

### How it differs from the Python core

- Cells run with `exec` in one persistent namespace, instead of IPython.
- Output is captured with `os.dupterm`, so `print`, `help()` and `sys.stdout` all reach `history`.
- The bootstrap cell carries the context helpers (`task_state`, `variables()`, `build_context()`) of the terminal bootstrap, with fewer recent cells shown in full.
- If the repair cell fails too, the model is unreachable and the run stops instead of retrying.

### Memory

Each cell's output is kept in `history` only up to its last `MAX_OUTPUT` characters. After every cell, the oldest records are dropped until at least `MIN_FREE` bytes of RAM are free for the next model request. Variables that cells keep are never dropped; cells manage those themselves.

### Notes

- Opening the serial port resets the board, so commands wait three seconds for MicroPython to boot.
- `bridge.py` writes to the board in small paced chunks to avoid overflowing its input buffer, so large responses take a second or two to arrive.
