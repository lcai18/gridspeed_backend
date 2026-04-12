from __future__ import annotations

import asyncio
import base64
import json
import os
import urllib.error
import urllib.request
import uuid
from typing import Set

from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse
import websockets

from api.supabase.supabase_client import get_supabase

load_dotenv()

ELEVEN_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVEN_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL")
DEFAULT_TO_NUMBER = os.getenv("VOICE_DEFAULT_TO_NUMBER", "+14046443252")
DEFAULT_FROM_NUMBER = os.getenv("VOICE_DEFAULT_FROM_NUMBER", "+18886444317")

print(PUBLIC_BASE_URL)

router = APIRouter()

twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

CLIENTS: Set[WebSocket] = set()
CLIENTS_LOCK = asyncio.Lock()
LAST_CALL_SID: str | None = None
CALL_SID_LOCK = asyncio.Lock()
AUDIO_CACHE: dict[str, bytes] = {}
AUDIO_CACHE_LOCK = asyncio.Lock()
LAST_DISPATCH_CONTEXT: dict = {}
DISPATCH_CONTEXT_LOCK = asyncio.Lock()


@router.get("/supabase/health")
async def supabase_health():
    sb = get_supabase()

    # This avoids needing your tables to exist yet.
    # If Storage is enabled in your Supabase project, this should return a list.
    try:
        buckets = sb.storage.list_buckets()
        return {"ok": True, "bucket_count": len(buckets)}
    except Exception as e:
        return {"ok": False, "error": repr(e)}


async def broadcast(payload: dict) -> None:
    """
    Send a JSON message to every connected /ws/client subscriber.
    Removes dead connections.
    """
    msg = json.dumps(payload)
    async with CLIENTS_LOCK:
        clients = list(CLIENTS)

    dead = []
    for client_ws in clients:
        try:
            await client_ws.send_text(msg)
        except Exception:
            dead.append(client_ws)

    if dead:
        async with CLIENTS_LOCK:
            for d in dead:
                CLIENTS.discard(d)


async def set_last_call_sid(call_sid: str | None) -> None:
    global LAST_CALL_SID
    async with CALL_SID_LOCK:
        LAST_CALL_SID = call_sid


async def get_last_call_sid() -> str | None:
    async with CALL_SID_LOCK:
        return LAST_CALL_SID


def eleven_tts_url() -> str:
    return f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVEN_VOICE_ID}"


def synthesize_elevenlabs_audio(text: str) -> bytes:
    if not ELEVEN_API_KEY:
        raise RuntimeError("Missing ELEVENLABS_API_KEY")

    payload = json.dumps({
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
    }).encode("utf-8")

    request = urllib.request.Request(
        eleven_tts_url(),
        data=payload,
        headers={
            "xi-api-key": ELEVEN_API_KEY,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        },
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


async def cache_audio_bytes(audio_bytes: bytes) -> str:
    audio_id = uuid.uuid4().hex
    async with AUDIO_CACHE_LOCK:
        AUDIO_CACHE[audio_id] = audio_bytes
    return audio_id


async def pop_audio_bytes(audio_id: str) -> bytes | None:
    async with AUDIO_CACHE_LOCK:
        return AUDIO_CACHE.pop(audio_id, None)


@router.websocket("/ws/client")
async def ws_client(ws: WebSocket):
    """
    Your notebook connects here to receive live transcript events.
    """
    await ws.accept()
    async with CLIENTS_LOCK:
        CLIENTS.add(ws)

    try:
        # Keep the socket open; ignore any incoming messages
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        async with CLIENTS_LOCK:
            CLIENTS.discard(ws)


@router.post("/chatgpt-response")
async def chatgpt_response(request: Request):
    payload = await request.json()
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(status_code=400, detail="Missing or empty text field")

    metadata = payload.get("metadata") or {}

    await broadcast({
        "source": "chatgpt",
        "event": "response",
        "text": text,
        "metadata": metadata,
    })

    try:
        audio_bytes = await asyncio.to_thread(synthesize_elevenlabs_audio, text)
        audio_base64 = base64.b64encode(audio_bytes).decode("utf-8")
        audio_id = await cache_audio_bytes(audio_bytes)
        await broadcast({
            "source": "eleven",
            "event": "tts",
            "text": text,
            "metadata": metadata,
            "audio_base64": audio_base64,
            "audio_id": audio_id,
        })
        call_sid = await get_last_call_sid()
        if call_sid and PUBLIC_BASE_URL:
            twiml = VoiceResponse()
            twiml.play(f"{PUBLIC_BASE_URL}/tts/{audio_id}")
            twiml.redirect(f"{PUBLIC_BASE_URL}/voice", method="POST")

            await asyncio.to_thread(
                twilio_client.calls(call_sid).update,
                twiml=str(twiml),
            )

        else:
            if not call_sid:
                print("No active call SID available for playback.")
            if not PUBLIC_BASE_URL:
                print("PUBLIC_BASE_URL not set; cannot play audio to phone.")
    except Exception as exc:
        print("ElevenLabs TTS failed:", repr(exc))

    return {"status": "ok"}


@router.get("/tts/{audio_id}")
async def get_tts_audio(audio_id: str):
    audio_bytes = await pop_audio_bytes(audio_id)
    if audio_bytes is None:
        return Response(status_code=404)
    return Response(audio_bytes, media_type="audio/mpeg")


# ----------------------------
# Outbound call trigger
# ----------------------------
@router.post("/call-me")
async def call_me(request: Request):
    payload = {}
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    to_number = payload.get("to") or DEFAULT_TO_NUMBER
    from_number = payload.get("from") or DEFAULT_FROM_NUMBER
    context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    work_order_id = payload.get("work_order_id")

    if not PUBLIC_BASE_URL:
        raise HTTPException(status_code=500, detail="PUBLIC_BASE_URL is required to place calls")

    async with DISPATCH_CONTEXT_LOCK:
        LAST_DISPATCH_CONTEXT.clear()
        LAST_DISPATCH_CONTEXT.update({
            "work_order_id": work_order_id,
            "context": context,
        })

    call = twilio_client.calls.create(
        to=to_number,
        from_=from_number,
        url=f"{PUBLIC_BASE_URL}/voice",
    )
    print("Outbound call initiated. SID:", call.sid)

    await broadcast({
        "source": "dispatch",
        "event": "call_initiated",
        "sid": call.sid,
        "work_order_id": work_order_id,
        "context": context,
    })

    return {
        "status": "calling",
        "sid": call.sid,
        "to": to_number,
        "from": from_number,
        "work_order_id": work_order_id,
    }


# ----------------------------
# Twilio webhook (TwiML) Connects
# ----------------------------
# This is like the control plane
@router.post("/voice")
async def voice(_: Request):
    print(">>> /voice HIT <<<")
    twiml = VoiceResponse()
    twiml.say("Hi. Start speaking after the beep. beeeeeep")
    twiml.pause(length=1)

    connect = twiml.connect()
    connect.stream(url="wss://tereasa-unscabbed-leonard.ngrok-free.dev/ws/twilio")

    return Response(str(twiml), media_type="text/xml")


def eleven_realtime_stt_url() -> str:
    return (
        "wss://api.elevenlabs.io/v1/speech-to-text/realtime"
        "?model_id=scribe_v2_realtime"
        "&language_code=en"
        "&audio_format=ulaw_8000"
        "&commit_strategy=vad"
    )


def extract_text_and_final(msg: dict) -> tuple[str, bool] | None:
    """
    Eleven's realtime schema can vary by version. This is a best-effort extractor.
    After you see a real message, you can tighten this to the exact fields.

    Returns (text, is_final) or None if no transcript text found.
    """
    text = (
        msg.get("text")
        or msg.get("transcript")
        or msg.get("data", {}).get("text")
        or msg.get("data", {}).get("transcript")
        or ""
    )
    if not text:
        return None

    is_final = bool(
        msg.get("is_final")
        or msg.get("final")
        or msg.get("data", {}).get("is_final")
        or msg.get("data", {}).get("final")
        or msg.get("type") in {"final", "transcript_final"}
    )
    return text, is_final


# ----------------------------
# Twilio Media Stream WS
# ----------------------------
# This is like the data plane
@router.websocket("/ws/twilio")
async def ws_twilio(ws: WebSocket):
    await ws.accept()
    print("Twilio websocket connected")

    url = eleven_realtime_stt_url()
    print("Eleven WS URL:", url)

    call_sid = None
    stream_sid = None

    try:
        async with websockets.connect(
            url,
            additional_headers=[("xi-api-key", ELEVEN_API_KEY)],
        ) as eleven_ws:
            print("Connected to ElevenLabs")

            # Some realtime APIs expect a start/init; harmless if ignored
            await eleven_ws.send(json.dumps({"message_type": "start"}))

            async def forward_twilio_audio_to_eleven():
                nonlocal call_sid, stream_sid
                while True:
                    raw = await ws.receive_text()
                    msg = json.loads(raw)

                    event = msg.get("event")

                    if event == "start":
                        start = msg.get("start", {})
                        call_sid = start.get("callSid")
                        stream_sid = start.get("streamSid")
                        print("Twilio start:", {"callSid": call_sid, "streamSid": stream_sid})
                        await set_last_call_sid(call_sid)
                        await broadcast({
                            "source": "twilio",
                            "event": "start",
                            "call_sid": call_sid,
                            "stream_sid": stream_sid,
                        })

                    elif event == "media":
                        await eleven_ws.send(json.dumps({
                            "message_type": "input_audio_chunk",
                            "audio_base_64": msg["media"]["payload"],
                        }))

                    elif event == "stop":
                        print("Twilio stop received")
                        await set_last_call_sid(None)
                        await broadcast({
                            "source": "twilio",
                            "event": "stop",
                            "call_sid": call_sid,
                            "stream_sid": stream_sid,
                        })
                        break

            async def read_eleven_transcripts():
                try:
                    first = await eleven_ws.recv()
                    # send first message to notebook too (often contains config/error)
                    try:
                        first_msg = json.loads(first)
                    except Exception:
                        first_msg = {"raw": first}

                    await broadcast({
                        "source": "eleven",
                        "event": "first_message",
                        "call_sid": call_sid,
                        "stream_sid": stream_sid,
                        "message": first_msg,
                    })
                    print("Eleven first msg:", first)

                    while True:
                        raw = await eleven_ws.recv()
                        msg = json.loads(raw)

                        extracted = extract_text_and_final(msg)
                        if extracted is not None:
                            text, is_final = extracted
                            await broadcast({
                                "source": "eleven",
                                "event": "transcript",
                                "call_sid": call_sid,
                                "stream_sid": stream_sid,
                                "text": text,
                                "is_final": is_final,
                                "raw": msg,
                            })
                        else:
                            # still forward non-transcript messages for debugging
                            await broadcast({
                                "source": "eleven",
                                "event": "message",
                                "call_sid": call_sid,
                                "stream_sid": stream_sid,
                                "raw": msg,
                            })

                except websockets.ConnectionClosed as e:
                    print("Eleven WS closed:", e.code, e.reason)
                    await broadcast({
                        "source": "eleven",
                        "event": "closed",
                        "call_sid": call_sid,
                        "stream_sid": stream_sid,
                        "code": e.code,
                        "reason": e.reason,
                    })

            await asyncio.gather(
                forward_twilio_audio_to_eleven(),
                read_eleven_transcripts(),
            )

    except WebSocketDisconnect:
        print("Twilio websocket disconnected")
    except Exception as e:
        print("Error in ws_twilio:", repr(e))
        await broadcast({
            "source": "server",
            "event": "error",
            "call_sid": call_sid,
            "stream_sid": stream_sid,
            "error": repr(e),
        })
        try:
            await ws.close()
        except Exception:
            pass
