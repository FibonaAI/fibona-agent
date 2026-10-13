![Fibona Agent](assets/fibona-header.png)

<p align="center">
  <a href="https://fibona.ai/blog/fibona-agent"><img src="https://img.shields.io/badge/BLOG-5287D4?style=for-the-badge&amp;logo=rss&amp;logoColor=white" alt="Blog"></a>
  <a href="https://x.com/fibonaai"><img src="https://img.shields.io/badge/X-000000?style=for-the-badge&amp;logo=x&amp;logoColor=white" alt="X"></a>
  <a href="https://fibona.ai/"><img src="https://img.shields.io/badge/WEBSITE-AA4E35?style=for-the-badge" alt="Website"></a>
</p>

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

2. Start the terminal UI from the project root (recommended):

   ```bash
   uv run --env-file .env fibona.py --boot-mode terminal
   ```
   See the [terminal bootstrap README](bootstraps/terminal/README.md) for its design and controls.

For the most minimal starting point, try `minimal`. It only reads a task from the console and send the task to the agent core:

```bash
uv run --env-file .env fibona.py --boot-mode minimal
```

To define your own startup behavior, write your bootstrap cell in `./your_custom_bootstrap.py`, then run:

```bash
uv run --env-file .env fibona.py --boot-mode ./your_custom_bootstrap.py
```

`--boot-mode` selects the included `terminal` or `minimal` bootstrap. Any other value is treated as a Python file path (relative to the current directory, absolute, or starting with `~`).

## Use the core as a library

From another `uv` project, add a local checkout:

```bash
uv add /path/to/fibona-agent
```

Core executes the Python source supplied to `Agent.run` as the bootstrap cell in a persistent session.
Cells share variables and call `call_me` when they need the model to choose the next cell.
Configure the client, model, working directory and input/output in your application:

```python
import sys

from openai import OpenAI
from core.agent import Agent, Mind, INSTRUCTIONS
from core.env import IPythonEnv

bootstrap_cell = """
task = input("Your task: ")
call_me(input=task)
"""

with OpenAI() as client:
    mind = Mind(client=client, model="gpt-6-astra", instructions=INSTRUCTIONS)
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
