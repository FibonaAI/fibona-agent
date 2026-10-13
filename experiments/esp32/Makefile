PORT ?= /dev/cu.usbserial-0001
# The board resets when the port opens; wait for MicroPython to boot before sending commands.
MP = uvx mpremote connect $(PORT) sleep 3
ESPTOOL = uvx --from esptool esptool --port $(PORT) --baud 115200

VERSION ?= 0.1.0
MICROPYTHON = v1.29.0
IDF_IMAGE = espressif/idf:v5.5.2
BIN ?= build/fibona-esp32-$(VERSION).bin
RELEASE_URL = https://github.com/FibonaAI/fibona-agent/releases/download/esp32-v$(VERSION)/fibona-esp32-$(VERSION).bin

.PHONY: bridge flash standalone firmware

bridge:  # make bridge TASK="..."  (bridge mode: model requests go through this computer)
	uv run --with pyserial python bridge.py $(PORT) fibona.py "$$TASK"

flash:  # Erase the board and write the firmware: local BIN if built, else the release for VERSION.
	test -f $(BIN) || curl -fL --create-dirs -o $(BIN) $(RELEASE_URL)
	$(ESPTOOL) erase-flash
	$(ESPTOOL) write-flash 0x1000 $(BIN)

# Standalone mode: copy config.py to the board, and standalone.py as main.py so it runs the task at every boot.
# To watch its output: uvx mpremote connect $(PORT) repl  (Ctrl-] or Ctrl-X to quit)
standalone:
	$(MP) cp config.py :config.py + cp standalone.py :main.py

firmware:  # MicroPython with fibona.py frozen in, built with ESP-IDF in Docker.
	test -d build/micropython || git clone --depth 1 -b $(MICROPYTHON) https://github.com/micropython/micropython build/micropython
	docker run --rm -v $(CURDIR)/build/micropython:/mp -v $(CURDIR):/fibona -w /mp/ports/esp32 $(IDF_IMAGE) bash -c \
	  "git config --global --add safe.directory '*' && make -C ../../mpy-cross && \
	   make BOARD=ESP32_GENERIC FROZEN_MANIFEST=/fibona/manifest.py submodules all"
	cp build/micropython/ports/esp32/build-ESP32_GENERIC/firmware.bin $(BIN)
