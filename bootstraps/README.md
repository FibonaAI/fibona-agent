# Bootstraps

This directory contains startup programs for Fibona. Each program defines the agent's initial environment, which it can inspect and change as it runs.

Programs follow the `bootstraps/<name>/init.py` layout. Fibona executes `init.py` as the bootstrap cell; supporting code and documentation live alongside it.

Run an included bootstrap with `fibona.py --boot-mode minimal` or `fibona.py --boot-mode terminal`. Any other `--boot-mode` value is treated as a Python file path, for example `fibona.py --boot-mode ./your_custom_bootstrap.py`. Relative paths resolve from the current directory; absolute paths and `~` are also supported.

- [minimal](minimal/init.py) joins the remaining command-line arguments with spaces as the task and calls the model, as shown in the [build-your-own chat TUI example](../README.md#quick-start). If the task is omitted or empty, it prompts in the console.
- [terminal](terminal/README.md) sets up a terminal UI and the agent's working context.

The CLI parses its own options and exposes the remaining arguments as the `argv` list in the IPython session. This list excludes the script name and the options consumed by the CLI. The `minimal` bootstrap joins this list with spaces as the task text, without another argument parser. Custom bootstraps can interpret the list themselves. The terminal UI reads tasks from its own input box.
