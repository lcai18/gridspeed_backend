from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import Any

import websockets
from openai import AsyncOpenAI

from api.voice_bot.rest_client import post_chatgpt_response

APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:8000").rstrip("/")
OPENAI_MODEL = os.environ.get("VOICE_AGENT_MODEL", "gpt-4.1-mini")
TURN_SILENCE_SEC = float(os.environ.get("VOICE_TURN_SILENCE_SEC", "0.7"))
IGNORE_PARTIALS = os.environ.get("VOICE_IGNORE_PARTIALS", "true").lower() == "true"
SUMMARY_EVERY_TURNS = int(os.environ.get("VOICE_SUMMARY_EVERY_TURNS", "4"))
LAST_TURNS_TO_KEEP = int(os.environ.get("VOICE_LAST_TURNS_TO_KEEP", "2"))
PRINT_RAW_NONTRANSCRIPT = os.environ.get("VOICE_PRINT_RAW_NONTRANSCRIPT", "false").lower() == "true"

SYSTEM_PROMPT_TEMPLATE = """You are a voice assistant responsible for contacting vendors to call for maintenance requests.
For this current maintenance request, these are the details: {dispatch_summary}
Let the vendor know the details of the maintenance request and ask for a time when they can come to perform the maintenance.
When they have given you a time, confirm the time back to them.
"""


@dataclass
class TurnState:
    buffer: list[str] = field(default_factory=list)
    silence_task: asyncio.Task | None = None
    gpt_task: asyncio.Task | None = None
    turn_index: int = 0
    history: list[dict[str, str]] = field(default_factory=list)
    summary: str = ""


class VoiceTurnAgent:
    def __init__(self, dispatch_summary: str = "N/A"):
        self.client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        self.state = TurnState()
        self.system_prompt = SYSTEM_PROMPT_TEMPLATE.format(dispatch_summary=dispatch_summary)

    @staticmethod
    def is_final_transcript(msg: dict[str, Any]) -> bool:
        raw = msg.get("raw")
        if isinstance(raw, dict) and raw.get("message_type") == "committed_transcript":
            return True
        return bool(msg.get("is_final"))

    async def call_gpt(self, messages: list[dict[str, str]]) -> str:
        response = await self.client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            temperature=0.3,
        )
        return (response.choices[0].message.content or "").strip()

    def build_context_messages(self, user_text: str) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = [{"role": "system", "content": self.system_prompt}]

        if self.state.summary:
            messages.append({"role": "system", "content": f"Conversation summary:\n{self.state.summary}"})

        if self.state.history:
            tail_count = LAST_TURNS_TO_KEEP * 2
            messages.extend(self.state.history[-tail_count:])

        messages.append({"role": "user", "content": user_text})
        return messages

    async def summarize_history(self) -> None:
        if not self.state.history:
            return

        summary_messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": "Create an AI-condensed rolling summary of the conversation for future context. Keep it concise and preserve key commitments, dates, and next actions.",
            }
        ]

        if self.state.summary:
            summary_messages.append({"role": "system", "content": f"Current summary:\n{self.state.summary}"})

        summary_messages.append({
            "role": "system",
            "content": f"Subsequent {SUMMARY_EVERY_TURNS} turns of conversation:",
        })
        summary_messages.extend(self.state.history)

        response = await self.client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=summary_messages,
            temperature=0.2,
        )
        self.state.summary = (response.choices[0].message.content or "").strip()

        tail_count = LAST_TURNS_TO_KEEP * 2
        self.state.history = self.state.history[-tail_count:]

    async def commit_turn_after_silence(self) -> None:
        await asyncio.sleep(TURN_SILENCE_SEC)

        user_turn = " ".join(self.state.buffer).strip()
        self.state.buffer.clear()
        if not user_turn:
            return

        self.state.turn_index += 1
        turn_id = self.state.turn_index

        if self.state.gpt_task and not self.state.gpt_task.done():
            self.state.gpt_task.cancel()

        async def run_gpt() -> None:
            try:
                messages = self.build_context_messages(user_turn)
                answer = await self.call_gpt(messages)

                await asyncio.to_thread(post_chatgpt_response, answer, {"turn_id": turn_id})

                self.state.history.extend([
                    {"role": "user", "content": user_turn},
                    {"role": "assistant", "content": answer},
                ])

                if turn_id % SUMMARY_EVERY_TURNS == 0:
                    await self.summarize_history()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                print(f"Voice GPT error on turn {turn_id}: {exc}")

        self.state.gpt_task = asyncio.create_task(run_gpt())

    def reset_silence_timer(self) -> None:
        if self.state.silence_task and not self.state.silence_task.done():
            self.state.silence_task.cancel()
        self.state.silence_task = asyncio.create_task(self.commit_turn_after_silence())

    async def handle_transcript_message(self, msg: dict[str, Any]) -> None:
        text = (msg.get("text") or "").strip()
        if not text:
            return

        final = self.is_final_transcript(msg)
        if not final and IGNORE_PARTIALS:
            return

        if final:
            self.state.buffer.append(text)
            self.reset_silence_timer()

    async def listen(self) -> None:
        uri = f"{APP_BASE_URL}/ws/client".replace("http://", "ws://").replace("https://", "wss://")

        async with websockets.connect(uri) as ws:
            await ws.send("hi")

            while True:
                raw = await ws.recv()
                msg = json.loads(raw)
                src = msg.get("source")
                event = msg.get("event")

                if src == "eleven" and event == "transcript":
                    await self.handle_transcript_message(msg)
                elif event in {"error", "closed"}:
                    print(msg)
                elif PRINT_RAW_NONTRANSCRIPT:
                    print("OTHER:", msg)


async def run_voice_turn_agent(dispatch_summary: str = "N/A") -> None:
    agent = VoiceTurnAgent(dispatch_summary=dispatch_summary)
    await agent.listen()
