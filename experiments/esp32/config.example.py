# Copy to config.py and fill in.
# Bridge mode: stays on the computer, and bridge.py reads the API settings.
# Standalone mode: `make standalone` copies it to the board, which also needs Wi-Fi and a task.
OPENAI_API_KEY = ""
OPENAI_BASE_URL = "https://api.openai.com/v1"
FIBONA_MODEL = "gpt-6-astra"
WIFI_SSID = ""
WIFI_PASSWORD = ""
TASK = ""

# History limits, read in both modes.
# ponytail: fixed limits; tune if a request plus its response needs more RAM.
MAX_OUTPUT = 2000  # Characters of each cell's output kept in history (the tail).
MIN_FREE = 50000  # Bytes of RAM to keep free for the next model request.
