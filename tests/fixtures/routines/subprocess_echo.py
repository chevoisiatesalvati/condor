"""Minimal routine for subprocess pool tests."""

import asyncio

from pydantic import BaseModel

RUN_IN_SUBPROCESS = True


class Config(BaseModel):
    delay_sec: float = 0.05
    message: str = "hello"


async def run(config: Config, context) -> str:
    await asyncio.sleep(config.delay_sec)
    return f"echo:{config.message}"
