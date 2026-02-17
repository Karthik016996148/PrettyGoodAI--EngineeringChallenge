"""Deepgram streaming speech-to-text client.

Receives raw mu-law audio bytes from Twilio and produces text transcriptions
via a callback.  Uses the Deepgram Python SDK's async WebSocket client.
"""

import asyncio
import logging
from typing import Callable, Awaitable

from deepgram import (
    DeepgramClient,
    DeepgramClientOptions,
    LiveTranscriptionEvents,
    LiveOptions,
)

import config

logger = logging.getLogger(__name__)

# Type alias for the callback that fires when a final transcript is ready.
TranscriptCallback = Callable[[str, bool], Awaitable[None]]


class StreamingSTT:
    """Wraps Deepgram's async live-transcription WebSocket.

    Usage::

        stt = StreamingSTT(on_transcript=my_callback)
        await stt.start()
        stt.send_audio(mulaw_bytes)   # call repeatedly
        await stt.stop()
    """

    def __init__(self, on_transcript: TranscriptCallback) -> None:
        """
        Args:
            on_transcript: Async callback ``(text, is_final)`` fired for each
                transcription result.
        """
        self._on_transcript = on_transcript
        self._dg_client = DeepgramClient(
            config.DEEPGRAM_API_KEY,
            DeepgramClientOptions(options={"keepalive": "true"}),
        )
        self._connection = None
        self._running = False

    async def start(self) -> None:
        """Open the Deepgram WebSocket and begin listening for transcripts."""
        self._connection = self._dg_client.listen.asyncwebsocket.v("1")

        # Wire up event handlers
        self._connection.on(
            LiveTranscriptionEvents.Transcript, self._handle_transcript
        )
        self._connection.on(
            LiveTranscriptionEvents.Error, self._handle_error
        )
        self._connection.on(
            LiveTranscriptionEvents.Close, self._handle_close
        )

        options = LiveOptions(
            encoding="mulaw",
            sample_rate=config.MULAW_SAMPLE_RATE,
            channels=config.MULAW_CHANNELS,
            punctuate=True,
            interim_results=True,
            endpointing=300,
            utterance_end_ms="1500",
            vad_events=True,
            language="en-US",
            model="nova-2",
        )

        started = await self._connection.start(options)
        if not started:
            raise RuntimeError("Failed to start Deepgram WebSocket connection")
        self._running = True
        logger.info("Deepgram STT connection started")

    async def send_audio(self, mulaw_bytes: bytes) -> None:
        """Feed raw mu-law audio bytes into Deepgram.

        Args:
            mulaw_bytes: Raw mu-law encoded audio from Twilio.
        """
        if self._connection and self._running:
            try:
                await self._connection.send(mulaw_bytes)
            except Exception as exc:
                logger.debug("Failed to send audio to Deepgram: %s", exc)

    async def stop(self) -> None:
        """Gracefully close the Deepgram connection."""
        self._running = False
        if self._connection:
            try:
                await self._connection.finish()
            except Exception:
                logger.debug("Deepgram connection already closed")
        logger.info("Deepgram STT connection stopped")

    # ---- internal event handlers ----

    async def _handle_transcript(self, _self, result, **kwargs) -> None:
        """Called by Deepgram SDK when a transcription result arrives."""
        try:
            alt = result.channel.alternatives[0]
            text = alt.transcript.strip()
            if not text:
                return
            is_final = result.is_final
            await self._on_transcript(text, is_final)
        except (IndexError, AttributeError) as exc:
            logger.warning("Unexpected transcript shape: %s", exc)

    async def _handle_error(self, _self, error, **kwargs) -> None:
        logger.error("Deepgram error: %s", error)

    async def _handle_close(self, _self, close, **kwargs) -> None:
        self._running = False
        logger.info("Deepgram connection closed: %s", close)
