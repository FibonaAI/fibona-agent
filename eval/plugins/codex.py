"""Harbor Codex adapter with reachable package mirrors during installation."""

from harbor.agents.installed.codex import Codex as HarborCodex
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from plugins.mirrors import configure_package_mirrors


class Codex(HarborCodex):
    async def install(self, environment: BaseEnvironment) -> None:
        await configure_package_mirrors(self, environment)
        await super().install(environment)

    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        # Docker's image USER can differ from Harbor's unset default_user.
        # The parent adapter needs an explicit user to chown uploaded auth.json.
        user = environment.default_user
        if user is None:
            result = await self.exec_as_agent(environment, command="id -u")
            uid = (result.stdout or "").strip()
            if result.return_code != 0 or not uid.isdecimal():
                raise RuntimeError("Cannot determine the Codex container user")
            user = int(uid)

        with environment.with_default_user(user):
            await super().run(instruction, environment, context)
