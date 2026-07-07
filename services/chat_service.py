import asyncio
from ai.router import ask

async def chat_ai(user, prompt):
    return await asyncio.to_thread(
        ask,
        user.provider,
        user.model,
        prompt
    )
