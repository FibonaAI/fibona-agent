# Boot

This directory contains startup programs for Fibona. Each program defines the agent's initial environment, which it can inspect and change as it runs.

Programs follow the `boot/<name>/init.py` layout. Fibona executes `init.py` as the bootstrap cell; supporting code and documentation live alongside it.

Run an included bootstrap with `fibona.py --bootstrap-mode <name>`, or supply a custom bootstrap cell with `fibona.py --bootstrap-file <path>`.

- [minimal](minimal/init.py) reads a task from the console and calls the model.
- [terminal](terminal/README.md) sets up a terminal UI and the agent's working context.
