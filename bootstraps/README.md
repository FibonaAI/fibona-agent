# Bootstraps

This directory contains startup programs for Fibona. Each program defines the agent's initial environment, which it can inspect and change as it runs.

Programs follow the `bootstraps/<name>/init.py` layout. Fibona executes `init.py` as the bootstrap cell; supporting code and documentation live alongside it.

Use `--boot-mode terminal`, `--boot-mode minimal`, or `--boot-mode ./your_custom_bootstrap.py`.

- [terminal](terminal/README.md) sets up a terminal UI and working context.
- [minimal](minimal/init.py) sends the command-line task to the model, or prompts if omitted.

Remaining CLI arguments are available as `argv` in the IPython session.
