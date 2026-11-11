![Fibona Agent](assets/fibona-header.png)

<p align="center">
  <a href="https://fibona.ai/" title="Website"><img src="assets/social-icons/website.svg" width="36" height="28" alt="Website"></a>
  <a href="https://x.com/fibonaai" title="X"><img src="assets/social-icons/x.svg" width="36" height="28" alt="X"></a>
  <a href="https://github.com/FibonaAI" title="GitHub"><img src="assets/social-icons/github.svg" width="36" height="28" alt="GitHub"></a>
  <a href="https://www.youtube.com/@FibonaAI" title="YouTube"><img src="assets/social-icons/youtube.svg" width="36" height="28" alt="YouTube"></a>
</p>

Fibona Agent is **a minimal, general-purpose, self-improving agent** that writes its own code as it runs.

It starts with only a tiny, general-purpose core, which we define as its "self." From this core, it can build its entire system step by step, becoming whatever it needs to be to get the task done. When something goes wrong, it can repair itself and return to a working state autonomously.

[<img src="assets/readme-icons/project.svg" width="16" height="16" align="absmiddle" alt=""> Project Page](https://fibona.ai/projects/fibona-agent) · [<img src="assets/readme-icons/video.svg" width="16" height="16" align="absmiddle" alt=""> Video Intro](https://fibona.ai/projects/fibona-agent#film) · [<img src="assets/readme-icons/report.svg" width="16" height="16" align="absmiddle" alt=""> Tech Report](https://fibona.ai/blog/fibona-agent) · [<img src="assets/readme-icons/demo.svg" width="16" height="16" align="absmiddle" alt=""> Online Demo](https://fibona.ai/fibona-agent)

## Demos

Try the [online demo](https://fibona.ai/fibona-agent) in your browser.

<table width="840" align="center">
  <tr>
    <th width="20%" align="center">iPhone GR21</th>
    <th width="40%" align="center">Console to desktop</th>
    <th width="40%" align="center">Trading terminal</th>
  </tr>
  <tr>
    <td align="center" valign="top">
      <video src="https://github.com/user-attachments/assets/30e6b458-4983-4e24-8ef9-fd18da004f81" controls></video>
    </td>
    <td align="center" valign="top">
      <video src="https://github.com/user-attachments/assets/c8f12473-2f83-4b60-a80e-a63f0ade37d4" controls></video>
    </td>
    <td align="center" valign="top">
      <video src="https://github.com/user-attachments/assets/81d97485-18b2-42ef-995e-0e61afb3d448" controls></video>
    </td>
  </tr>
</table>

[iOS source and build instructions](experiments/ios/README.md)

[Fibona Agent in Chrome: extension source and installation](experiments/chrome/README.md)

## Quick start

Requires Python 3.12+ and `uv`.

1. Create a `.env` file in the project root:

   ```dotenv
   FIBONA_API_KEY=your-api-key
   FIBONA_BASE_URL=https://api.openai.com/v1
   FIBONA_MODEL=gpt-6-astra
   ```

   Set `FIBONA_MODEL` to your model ID; it defaults to `gpt-6-astra`.
   Set `FIBONA_BASE_URL` to use an OpenAI-compatible gateway.
   `call_me` uses the [OpenAI Responses API](https://developers.openai.com/api/reference/resources/responses) in stateless mode:
   it sends the full context each round with `store=False`. To continue across calls, include the earlier
   input and returned `r.output` in the next `input`; `previous_response_id` is not supported.
   To receive encrypted reasoning for replay, pass `include=["reasoning.encrypted_content"]`.
   The complete official Responses API reference is available offline in the REPL as
   `openai_responses_api_docs` (page name → Markdown), including `methods/create`, `streaming-events`,
   and `websocket-events`. The reference also documents stateful features that `call_me` does not support.

2. Choose a startup mode and run it from the project root:

   - Start the terminal UI from the project root:

     ```bash
     uv run --env-file .env fibona.py --boot-mode terminal
     ```

     See the [terminal bootstrap README](bootstraps/terminal/README.md) for its design and controls.

   - Or start in the minimal mode and let the agent build its own chat TUI:

     ```bash
     uv run --env-file .env fibona.py --boot-mode minimal \
       "Build and launch a premium chat TUI in this terminal.
       I want to use it to chat with you.
       The TUI should feature refined dark colors, generous spacing, elegant rendering,
       clear activity status, and multiline input. Press Enter to send messages.
       Keep me updated as you build it."
     ```

     Omit the task to enter it interactively.

   - To define your own startup behavior, write your bootstrap cell in `./your_custom_bootstrap.py`, then run:

     ```bash
     uv run --env-file .env fibona.py --boot-mode ./your_custom_bootstrap.py
     ```

Use `--reasoning-effort high` to set the default for `call_me`, and
`--lowest-reasoning-effort medium` to set its minimum. Both default to `medium` and accept
`none`, `minimal`, `low`, `medium`, `high`, `xhigh`, or `max`. Requests below the minimum
are raised to it, including the default. The selected model must support the chosen effort.

Each run automatically records cells and every model request, response, or error (including intermediate tool calls) in `logs/<timestamp>-<run-id>.jsonl`.

For usage analysis, use the `api_request`, `api_response`, and `api_error` events. Each SDK call has a unique
`api_call_id`; `request_id` links calls belonging to the same logical operation, and `api_call_index` identifies
its internal rounds. `api_request.request` contains the complete parameters passed to the SDK;
`api_response.response` contains the complete SDK response JSON, without content truncation or logical-round merging.
`api_stream_event.stream_event` records each consumed streaming event, in order, including text/tool-call deltas
and terminal responses. HTTP error bodies, when supplied by the SDK, are retained in `api_error.response_body`.
These are SDK payloads, not a raw HTTP wire capture; authentication headers and API keys are not recorded.
`api_response` also records the original per-call `usage` (including provider-specific cache,
reasoning, and cost fields when present), response/model/status, provider request ID, and elapsed seconds before
logical usage is merged. Streaming usage is recorded when a completed, incomplete, or failed response event is consumed.
An `api_incomplete` event, or a request without a terminal event, means usage is unknown, not zero. SDK-internal
HTTP retries are not separate API-call events. Do not add these usage records to the legacy `model_response`
logical summaries: doing so would double-count usage. Provider cost fields can be absent; no price estimate is
inserted into the raw usage.

## Use the core as a library

From another `uv` project, add a local checkout:

```bash
uv add /path/to/fibona-agent
```

Core executes the Python source supplied to `Agent.run` as the bootstrap cell in a persistent session.
Cells share variables and call `call_me` when they need the model to choose the next cell.
Configure the client, model, working directory and input/output in your application:

```python
import os
import sys

from openai import OpenAI
from core.agent import Agent, Mind, INSTRUCTIONS
from core.env import IPythonEnv

bootstrap_cell = """
task = input("Your task: ")
call_me(input=task)
"""

with OpenAI(
    api_key=os.environ["FIBONA_API_KEY"],
    base_url=os.getenv("FIBONA_BASE_URL", "https://api.openai.com/v1"),
) as client:
    mind = Mind(client=client, model=os.getenv("FIBONA_MODEL", "gpt-6-astra"), instructions=INSTRUCTIONS)
    env = IPythonEnv(stdout=sys.stdout, stderr=sys.stderr, read_input=input)
    Agent(mind, cwd=".").run(bootstrap_cell, env=env)
```

## Development

Enable Ruff checks before each commit:

```bash
uv run pre-commit install
```

The hooks run Ruff lint and format checks on staged files. Errors block the commit.

To check all tracked files manually:

```bash
uv run pre-commit run --all-files
```

## Citation

If you use Fibona Agent in your research, please cite it as follows:

```bibtex
@misc{fibonaagent2026,
  title        = {{Fibona Agent: Self-Improving Agent Writing Itself From Scratch}},
  author       = {{FibonaAI Team}},
  year         = {2026},
  howpublished = {GitHub repository},
  url          = {https://github.com/FibonaAI/fibona-agent}
}
```

## License

This project is licensed under the [MIT License](LICENSE).
