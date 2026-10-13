"""Run a script on the board and serve its model requests over USB serial.

The board prints `@@model <json body>` and reads one JSON line back: the Responses API result,
or {"error": ...}. Usage: python bridge.py PORT FILE [ARG...] (reads the API key from config.py);
the args reach the script as ARGV, and config.py's MAX_OUTPUT and MIN_FREE as names of their own.
"""

import json
import sys
import time
import urllib.error
import urllib.request

import config
import serial

REQ = b"@@model "


def call(body):
    """Send one model request for the board, with the API key that stays on this computer."""
    body.setdefault("model", config.FIBONA_MODEL)
    request = urllib.request.Request(
        config.OPENAI_BASE_URL + "/responses",
        json.dumps(body).encode(),
        {"Authorization": "Bearer " + config.OPENAI_API_KEY, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        return {"error": {"status": error.code, "body": error.read().decode(errors="replace")}}
    except OSError as error:
        return {"error": {"message": str(error)}}


def send(data):
    # ponytail: paced writes so the board's small stdin buffer keeps up; raise baud if replies get big.
    for i in range(0, len(data), 64):
        s.write(data[i : i + 64])
        time.sleep(0.01)


# Stage 1: start. Enter the board's raw REPL and send the script, prefixed with the names it expects.
# Raw REPL runs the script as __main__, so fibona.py takes its bridge-mode entry.
port, path, *argv = sys.argv[1:]
s = serial.Serial(port, 115200, timeout=1)
time.sleep(3)  # Opening the port resets the board; let it boot.
s.reset_input_buffer()
s.write(b"\r\x03\x03\x01")  # Interrupt, then enter raw REPL.
s.read_until(b"raw REPL; CTRL-B to exit\r\n>")
with open(path, "rb") as f:
    prelude = "ARGV = %r\nMAX_OUTPUT = %d\nMIN_FREE = %d\n" % (
        [a for a in argv if a],
        config.MAX_OUTPUT,
        config.MIN_FREE,
    )
    send(prelude.encode() + f.read() + b"\x04")
if s.read(2) != b"OK":
    sys.exit("board did not accept the script")

# Stage 2: run. The serial link carries both the program's output and its model requests, one per line:
# a line starting with REQ is a request, answered with one JSON line; any other line is shown as output.
# Stage 3 starts when raw REPL ends the run with: stdout, \x04, stderr, \x04, ">".
out, buf = b"", b""
while not out.endswith(b"\x04>"):
    buf += s.read_until(b"\n")
    if buf.startswith(REQ) and buf.endswith(b"\n"):
        send(json.dumps(call(json.loads(buf[len(REQ) :]))).encode() + b"\n")
        buf = b""
    elif buf.endswith(b"\n") or buf.endswith(b"\x04>"):
        out += buf
        if b"\x04" not in out:  # Before the first \x04 it is stdout: show it live.
            sys.stdout.write(buf.decode(errors="replace"))
            sys.stdout.flush()
        buf = b""

# Stage 3: finish. Leave raw REPL, print the rest of stdout and any traceback, exit 1 if the script failed.
s.write(b"\x02")  # Back to the normal REPL.

stdout_tail, _, err = out.partition(b"\x04")
sys.stdout.write(stdout_tail[stdout_tail.rfind(b"\n") + 1 :].decode(errors="replace"))
err = err.removesuffix(b"\x04>").decode(errors="replace")
sys.stderr.write(err)
sys.exit(1 if err else 0)
