"""FastAPI server handling Twilio webhooks and bidirectional media streams.

This is the central hub that:
1. Serves TwiML telling Twilio to connect a bidirectional WebSocket.
2. Receives inbound audio from Twilio, pipes it to Deepgram for STT.
3. Sends transcriptions to the LLM conversation engine for a response.
4. Synthesizes the response via ElevenLabs TTS.
5. Streams the synthesized audio back to Twilio.
"""

import asyncio
import base64
import json
import logging
import threading
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import PlainTextResponse

import audio_utils
import config
from call_manager import generate_twiml
from conversation import ConversationEngine
from scenarios import get_scenario
from stt import StreamingSTT
from transcript import TranscriptRecorder
from tts import synthesize_streaming

logger = logging.getLogger(__name__)

app = FastAPI(title="PrettyGoodAI Voice Bot")

# Registry of active calls so main.py can track completion.
# Uses threading.Event (not asyncio.Event) because the server runs on a
# different thread from the main orchestrator.
active_calls: dict[str, threading.Event] = {}
# Maps call_sid -> TranscriptRecorder for retrieval after the call.
call_transcripts: dict[str, TranscriptRecorder] = {}


# ---- HTTP endpoints -------------------------------------------------------

@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.api_route("/twiml", methods=["GET", "POST"])
async def twiml_endpoint(request: Request) -> PlainTextResponse:
    """Twilio hits this URL when the outbound call connects.

    We return TwiML that tells Twilio to open a bidirectional media
    stream back to our ``/media-stream`` WebSocket endpoint.
    """
    scenario = request.query_params.get("scenario", "simple_scheduling")
    xml = generate_twiml(scenario)
    return PlainTextResponse(content=xml, media_type="text/xml")


@app.api_route("/call-status", methods=["POST"])
async def call_status(request: Request) -> PlainTextResponse:
    """Receive call status callbacks from Twilio."""
    form = await request.form()
    call_sid = form.get("CallSid", "")
    status = form.get("CallStatus", "")
    logger.info("Call status: SID=%s status=%s", call_sid, status)

    if status == "completed" and call_sid in active_calls:
        active_calls[call_sid].set()

    return PlainTextResponse("OK")


# ---- WebSocket media stream ------------------------------------------------

@app.websocket("/media-stream")
async def media_stream(websocket: WebSocket) -> None:
    """Handle a bidirectional Twilio Media Stream.

    This is the core of the voice bot.  Audio flows:
        Twilio -> (mulaw) -> Deepgram STT -> text -> LLM -> text
              -> ElevenLabs TTS -> (mulaw) -> Twilio
    """
    await websocket.accept()
    logger.info("WebSocket connection accepted")

    # Scenario will be extracted from the Twilio 'start' message's
    # customParameters (set via <Parameter> in the TwiML).
    scenario_name: str = "simple_scheduling"
    scenario = None
    engine = None
    recorder = TranscriptRecorder(scenario_name=scenario_name)
    recorder.start()

    # State for this call
    stream_sid: str = ""
    call_sid: str = ""

    # Track whether the bot is currently speaking (to avoid overlapping).
    is_speaking = asyncio.Event()
    # Track accumulated agent speech for batching into a single response.
    agent_text_buffer: list[str] = []
    agent_text_lock = asyncio.Lock()
    # Silence timer: if the agent stops talking, respond after a pause.
    silence_task: asyncio.Task | None = None
    # Flag to signal the conversation loop to end.
    call_active = True
    # Count of conversation turns for a safety limit.
    turn_count = 0
    MAX_TURNS = 16
    # Track if we've sent the opening line
    sent_opening = False

    async def on_transcript(text: str, is_final: bool) -> None:
        """Called when Deepgram produces a transcript of the agent's speech."""
        nonlocal silence_task, sent_opening

        if not is_final:
            return

        logger.debug("Agent said (final): %s", text)

        async with agent_text_lock:
            agent_text_buffer.append(text)

        # Cancel any existing silence timer and start a new one.
        if silence_task and not silence_task.done():
            silence_task.cancel()

        # Wait for a pause in the agent's speech before responding.
        silence_task = asyncio.create_task(_silence_timeout())

    async def _silence_timeout() -> None:
        """After the agent stops speaking for a beat, generate a response."""
        nonlocal turn_count, call_active, sent_opening

        try:
            # Wait for the agent to stop talking. 2.5s of silence = their turn is done.
            # Longer than typical to avoid cutting off mid-sentence.
            await asyncio.sleep(2.5)
        except asyncio.CancelledError:
            return

        # Collect all buffered agent text.
        async with agent_text_lock:
            if not agent_text_buffer:
                return
            full_agent_text = " ".join(agent_text_buffer)
            agent_text_buffer.clear()

        recorder.add("agent", full_agent_text)

        if engine is None:
            logger.warning("Engine not initialized yet, skipping response")
            return

        # Check if conversation should end (check after first 2 exchanges).
        if turn_count >= 2:
            try:
                should_end = await engine.should_hang_up(full_agent_text)
                if should_end or turn_count >= MAX_TURNS:
                    # Say goodbye and end.
                    goodbye = "Alright, thank you so much. Bye bye."
                    recorder.add("patient", goodbye)
                    await _speak(goodbye)
                    await asyncio.sleep(2)
                    call_active = False
                    return
            except Exception as exc:
                logger.warning("Hang-up check failed: %s", exc)

        # Generate patient response.
        turn_count += 1

        if not sent_opening:
            patient_text = await engine.get_opening_line()
            sent_opening = True
        else:
            patient_text = await engine.respond_to_agent(full_agent_text)

        recorder.add("patient", patient_text)

        # Speak the response.
        await _speak(patient_text)

    async def _speak(text: str) -> None:
        """Synthesize text and stream audio back to Twilio."""
        if not stream_sid:
            logger.warning("Cannot speak - no stream_sid yet")
            return

        is_speaking.set()
        try:
            chunk_buffer = bytearray()
            async for audio_chunk in synthesize_streaming(text):
                chunk_buffer.extend(audio_chunk)
                # Send in 640-byte frames (80ms of mulaw audio at 8kHz)
                while len(chunk_buffer) >= 640:
                    frame = bytes(chunk_buffer[:640])
                    chunk_buffer = chunk_buffer[640:]
                    payload = base64.b64encode(frame).decode("ascii")
                    msg = {
                        "event": "media",
                        "streamSid": stream_sid,
                        "media": {"payload": payload},
                    }
                    try:
                        await websocket.send_json(msg)
                    except Exception:
                        return

            # Send any remaining audio.
            if chunk_buffer:
                payload = base64.b64encode(bytes(chunk_buffer)).decode("ascii")
                msg = {
                    "event": "media",
                    "streamSid": stream_sid,
                    "media": {"payload": payload},
                }
                try:
                    await websocket.send_json(msg)
                except Exception:
                    return

            # Send a mark so we know when playback finishes.
            mark_msg = audio_utils.twilio_mark_message(stream_sid, f"turn_{turn_count}")
            try:
                await websocket.send_json(mark_msg)
            except Exception:
                pass

        except Exception as exc:
            logger.error("TTS/streaming error: %s", exc)
        finally:
            is_speaking.clear()

    # --- Set up Deepgram STT ---
    stt = StreamingSTT(on_transcript=on_transcript)
    try:
        await stt.start()
    except Exception as exc:
        logger.error("Failed to start Deepgram: %s", exc)
        await websocket.close()
        return

    # --- Main receive loop ---
    try:
        while call_active:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=120)
            except asyncio.TimeoutError:
                logger.warning("WebSocket receive timeout - ending call")
                break

            data: dict[str, Any] = json.loads(raw)
            event = data.get("event", "")

            if event == "connected":
                logger.info("Twilio media stream connected")

            elif event == "start":
                meta = data.get("start", {})
                stream_sid = meta.get("streamSid", "")
                call_sid = meta.get("callSid", "")
                custom = meta.get("customParameters", {})
                scenario_name = custom.get("scenario", "simple_scheduling")

                # Now that we know the scenario, initialize everything.
                scenario = get_scenario(scenario_name)
                recorder.scenario_name = scenario_name
                recorder.call_sid = call_sid
                engine = ConversationEngine(
                    system_prompt=scenario.system_prompt,
                    scenario_name=scenario_name,
                )

                logger.info(
                    "Stream started: streamSid=%s callSid=%s scenario=%s",
                    stream_sid,
                    call_sid,
                    scenario_name,
                )
                # Register this call for tracking.
                if call_sid:
                    active_calls.setdefault(call_sid, threading.Event())
                    call_transcripts[call_sid] = recorder

            elif event == "media":
                payload = data.get("media", {}).get("payload", "")
                if payload:
                    audio_bytes = base64.b64decode(payload)
                    await stt.send_audio(audio_bytes)

            elif event == "mark":
                mark_name = data.get("mark", {}).get("name", "")
                logger.debug("Mark received: %s", mark_name)

            elif event == "stop":
                logger.info("Twilio media stream stopped")
                break

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    except Exception as exc:
        logger.error("WebSocket error: %s", exc)
    finally:
        # Clean up.
        try:
            await stt.stop()
        except Exception as exc:
            logger.debug("STT stop error: %s", exc)

        if silence_task and not silence_task.done():
            silence_task.cancel()

        # Save transcript.
        try:
            path = recorder.save(call_sid=call_sid)
            logger.info("Call complete. Transcript: %s", path)
        except Exception as exc:
            logger.error("Failed to save transcript: %s", exc)

        # Signal that this call is done.
        if call_sid and call_sid in active_calls:
            active_calls[call_sid].set()
