# Bootstraps

This directory contains startup programs for Fibona. Each program defines the agent's initial environment, which it can inspect and change as it runs.

Programs follow the `bootstraps/<name>/init.py` layout. Fibona executes `init.py` as the bootstrap cell; supporting code and documentation live alongside it.

Run an included bootstrap with `fibona.py --boot-mode minimal` or `fibona.py --boot-mode terminal`. Any other `--boot-mode` value is treated as a Python file path, for example `fibona.py --boot-mode ./your_custom_bootstrap.py`. Relative paths resolve from the current directory; absolute paths and `~` are also supported.

- [minimal](minimal/init.py) reads a task from the console and calls the model.
- [terminal](terminal/README.md) sets up a terminal UI and the agent's working context.
