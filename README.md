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
   OPENAI_API_KEY=your-api-key
   FIBONA_MODEL=gpt-6-astra

   # Optional: use a custom API endpoint.
   # OPENAI_BASE_URL=https://your-provider.example/v1
   ```

   Set `FIBONA_MODEL` to a model supported by your provider. If omitted, it defaults to `gpt-6-astra`.

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

The hooks run Ruff lint and format checks on staged files, and the tests in `tests/` when Python files change. Errors block the commit.

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
