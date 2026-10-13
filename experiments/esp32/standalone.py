"""Standalone mode: join Wi-Fi and run the task in config.py, with no computer attached."""

import time

import config
import network

import fibona

wlan = network.WLAN(network.STA_IF)
wlan.active(True)
wlan.connect(config.WIFI_SSID, config.WIFI_PASSWORD)
for _ in range(100):
    if wlan.isconnected():
        break
    time.sleep_ms(200)
else:
    raise OSError("Wi-Fi connect failed, status %d" % wlan.status())

fibona.main(
    [config.TASK],
    fibona.http_transport(config.OPENAI_BASE_URL, config.OPENAI_API_KEY, config.FIBONA_MODEL),
    config.MAX_OUTPUT,
    config.MIN_FREE,
)
