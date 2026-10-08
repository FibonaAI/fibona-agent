# Fibona Agent

Fibona Agent is **a minimal, general-purpose, self-improving agent** that writes its own code as it runs.

It starts with only a tiny, general-purpose core, which we define as its "self." From this core, it can build its entire system step by step, becoming whatever it needs to be to get the task done. When something goes wrong, it can repair itself and return to a working state autonomously.

## Quick start

Requires Python 3.12+ and `uv`.

1. Create a `.env` file in the project root:

   ```dotenv
   OPENAI_API_KEY=your-api-key
   FIBONA_MODEL=gpt-6-astra

   # Optional: use a custom API endpoint.
   # OPENAI_BASE_URL=https://your-provider.example/v1
   ```

   Set `FIBONA_MODEL` to a model supported by your provider. If omitted, it defaults to `gpt-6-astra`.
2. Start the terminal from the project root:

   ```bash
   uv run --extra tui terminal.py
   ```

   `uv` automatically creates `.venv` and installs the dependencies, including the optional TUI dependencies selected by `--extra tui`.
   No manual environment setup or activation is needed.

   Enter your task in the TUI after it opens.

   Press **Enter** to send a message and **Ctrl+C** to exit.

   Cells and outputs are automatically saved to `~/.fibona/<session_id>/session.ipynb`.
   Click `notebook` in the status bar to copy its path.
3. Or start from scratch and let the agent build its own TUI:

   ```bash
   uv run --env-file .env fibona.py \
     "Build and launch a premium chat TUI in this terminal:
   refined dark colors, generous spacing, elegant Markdown and code rendering,
   clear activity status, and multiline input. Press Enter to send messages.
   Let me chat with you through it. Keep me updated as you build it."
   ```

## Use the core as a library

From another `uv` project, add a local checkout without the `tui` extra:

```bash
uv add /path/to/fibona-agent
```

This installs the core dependencies without Textual or python-dotenv.
The core does not load `.env`; configure the client, model, working directory, and input/output in your application:

```python
import sys

from openai import OpenAI
from core.agent import Agent, Mind, INSTRUCTIONS
from core.env import IPythonEnv

with OpenAI(api_key="your-api-key") as client:
    mind = Mind(client=client, model="gpt-6-astra", instructions=INSTRUCTIONS)
    env = IPythonEnv(stdout=sys.stdout, stderr=sys.stderr, read_input=input)
    Agent(mind, cwd=".").run("Your task here", env=env)
```

Use a model supported by your provider. Pass `base_url` to `OpenAI` for a custom API endpoint.
The working directory specified by `cwd` must already exist.

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
