"""Bootstrap cell: read the user's task and ask the model to choose the next cell."""

task = input("Your task: ")
call_me(input=task)  # noqa: F821 -- core binds call_me.
