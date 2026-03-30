from __future__ import annotations

import asyncio

from agent_runtime import run_text_query


async def run_demo() -> None:
    prompt = (
        "List the available skills you can use from the loaded Claude settings. "
        "If no skills are available, reply with one short sentence saying so."
    )

    for line in await run_text_query(prompt):
        print(line)


def main() -> None:
    asyncio.run(run_demo())


if __name__ == "__main__":
    main()
