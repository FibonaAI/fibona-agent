"""Bootstrap cell: start the UI and arrange cell 1 to read the user's task."""

from boot.terminal.terminal import Terminal

terminal = Terminal(get_ipython()).start()  # noqa: F821 -- provided by IPython.

USER_CELL = """
from boot.terminal.context import initialize_context

task = input()
build_context = initialize_context(globals())
response = call_me(
    input=[*build_context(), {"role": "user", "content": "Start the user's task and submit the next Python cell."}]
)
""".strip()

# Use the existing callback carried by call_me's partial binding, without waking the model.
call_me.keywords["submit"](USER_CELL, "Read the task and call the model.")  # noqa: F821 -- core binds call_me.
