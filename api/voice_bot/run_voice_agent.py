from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
from pathlib import Path

from api.supabase.db_helpers import get_work_order, list_recent_email_messages_for_work_order
from api.voice_bot.realtime_agent import run_voice_turn_agent
from api.voice_bot.rest_client import trigger_dispatch_call


def _safe_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _load_work_order_context(work_order_id: str, *, message_limit: int = 12) -> tuple[dict, list[dict]]:
    work_order = get_work_order(work_order_id)
    if work_order is None:
        raise ValueError(f"Work order not found: {work_order_id}")

    recent_messages = list_recent_email_messages_for_work_order(work_order_id, limit=message_limit)
    ordered_messages = list(reversed(recent_messages))
    return work_order, ordered_messages


def build_dispatch_summary_from_work_order(work_order_id: str, *, message_limit: int = 12) -> str:
    work_order, ordered_messages = _load_work_order_context(
        work_order_id,
        message_limit=message_limit,
    )

    conversation_lines: list[str] = []
    for idx, message in enumerate(ordered_messages, start=1):
        direction = (message.get("direction") or "unknown").lower()
        speaker = "Tenant" if direction == "inbound" else "PM Team"
        created_at = message.get("created_at") or "unknown_time"
        body = (message.get("body") or "").strip()
        if body:
            conversation_lines.append(f"{idx}. [{created_at}] {speaker}: {body}")

    work_order_snapshot = {
        "id": work_order.get("id"),
        "title": work_order.get("title"),
        "description": work_order.get("description"),
        "summary": work_order.get("summary"),
        "priority": work_order.get("priority"),
        "severity": work_order.get("severity"),
        "issue_category": work_order.get("issue_category"),
        "likely_trade": work_order.get("likely_trade"),
        "status": work_order.get("status"),
    }

    lines: list[str] = [
        "Work order context (from Supabase)",
        _safe_json(work_order_snapshot),
        "",
        "Recent email conversation context:",
    ]

    if conversation_lines:
        lines.extend(conversation_lines)
    else:
        lines.append("No email messages found for this work order.")

    return "\n".join(lines)




def build_dispatch_call_context(work_order_id: str, *, message_limit: int = 12) -> dict:
    work_order, ordered_messages = _load_work_order_context(work_order_id, message_limit=message_limit)

    message_context = []
    for message in ordered_messages:
        direction = (message.get("direction") or "unknown").lower()
        speaker = "tenant" if direction == "inbound" else "pm_team"
        message_context.append({
            "speaker": speaker,
            "created_at": message.get("created_at"),
            "body": message.get("body"),
        })

    return {
        "work_order_summary": work_order.get("summary"),
        "title": work_order.get("title"),
        "description": work_order.get("description"),
        "likely_trade": work_order.get("likely_trade"),
        "priority": work_order.get("priority"),
        "severity": work_order.get("severity"),
        "issue_category": work_order.get("issue_category"),
        "status": work_order.get("status"),
        "recent_messages": message_context,
    }

def _write_temp_context_file(dispatch_summary: str, work_order_id: str | None) -> Path:
    work_order_fragment = work_order_id or "manual"
    temp_file = tempfile.NamedTemporaryFile(
        mode="w",
        prefix=f"voice-dispatch-{work_order_fragment}-",
        suffix=".txt",
        delete=False,
        encoding="utf-8",
    )
    with temp_file:
        temp_file.write(dispatch_summary)
    return Path(temp_file.name)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the voice turn agent with dispatch context")
    parser.add_argument(
        "--work-order-id",
        help="Load dispatch context from Supabase using this work_order_id",
    )
    parser.add_argument(
        "--context-file",
        help="Path to a text file whose contents will be injected as the dispatch summary",
    )
    parser.add_argument(
        "--message-limit",
        type=int,
        default=12,
        help="Max recent email messages to include when --work-order-id is used (default: 12)",
    )
    parser.add_argument(
        "--write-temp-context",
        action="store_true",
        help="Write the resolved dispatch context to a temporary file before starting",
    )
    parser.add_argument(
        "--skip-call",
        action="store_true",
        help="Do not trigger /call-me before listening (default is to trigger when --work-order-id is set)",
    )
    parser.add_argument("--to", help="Phone number to call")
    parser.add_argument("--from-number", help="Twilio number to call from")
    return parser.parse_args()


def resolve_dispatch_summary(args: argparse.Namespace) -> tuple[str, Path | None]:
    if args.context_file and args.work_order_id:
        raise ValueError("Use either --context-file or --work-order-id, not both")

    if args.context_file:
        context_path = Path(args.context_file)
        return context_path.read_text(encoding="utf-8"), None

    if args.work_order_id:
        dispatch_summary = build_dispatch_summary_from_work_order(
            args.work_order_id,
            message_limit=args.message_limit,
        )
        if args.write_temp_context:
            temp_path = _write_temp_context_file(dispatch_summary, args.work_order_id)
            return dispatch_summary, temp_path
        return dispatch_summary, None

    return os.environ.get("VOICE_DISPATCH_SUMMARY", "N/A"), None


def maybe_trigger_call(args: argparse.Namespace) -> None:
    if args.skip_call or not args.work_order_id:
        return

    dispatch_context = build_dispatch_call_context(
        args.work_order_id,
        message_limit=args.message_limit,
    )
    call_response = trigger_dispatch_call(
        work_order_id=args.work_order_id,
        dispatch_context=dispatch_context,
        to=args.to,
        from_number=args.from_number,
    )
    print(f"Triggered outbound call: {call_response}")


if __name__ == "__main__":
    cli_args = parse_args()
    summary, temp_context_path = resolve_dispatch_summary(cli_args)

    if temp_context_path is not None:
        print(f"Wrote dispatch context to temp file: {temp_context_path}")

    maybe_trigger_call(cli_args)

    asyncio.run(run_voice_turn_agent(dispatch_summary=summary))
