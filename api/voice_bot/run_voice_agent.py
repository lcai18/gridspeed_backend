from __future__ import annotations

import asyncio
import os

from api.voice_bot.realtime_agent import run_voice_turn_agent


if __name__ == "__main__":
    dispatch_summary = os.environ.get("VOICE_DISPATCH_SUMMARY", "N/A")
    asyncio.run(run_voice_turn_agent(dispatch_summary=dispatch_summary))
