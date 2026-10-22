"""Bootstrap cell: start the UI and read the user's task."""

from bootstraps.terminal.context import initialize_context
from bootstraps.terminal.terminal import Terminal

terminal = Terminal(get_ipython()).start()  # noqa: F821 -- provided by IPython.

task = input()
build_context = initialize_context(globals())
response = call_me(  # noqa: F821 -- core binds call_me.
    input=[*build_context(), {"role": "user", "content": "Start the user's task and submit the next Python cell."}]
)
