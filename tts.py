"""ElevenLabs text-to-speech client.

Converts text to mu-law audio ready for Twilio using ElevenLabs streaming API.
Uses ``ulaw_8000`` output format so ElevenLabs returns mu-law directly - no
local PCM-to-mulaw conversion is needed.
"""

import logging
from typing import AsyncIterator

import httpx

import config

logger = logging.getLogger(__name__)

ELEVENLABS_STREAM_URL = (
    "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"
)

# ElevenLabs natively supports mu-law at 8 kHz - perfect for Twilio.
OUTPUT_FORMAT = "ulaw_8000"
MODEL_ID = "eleven_flash_v2_5"


async def synthesize(text: str) -> bytes:
    """Convert text to mu-law audio bytes using ElevenLabs streaming API.

    Args:
        text: The text to speak.

    Returns:
        Complete mu-law audio bytes at 8 kHz.
    """
    url = ELEVENLABS_STREAM_URL.format(voice_id=config.ELEVENLABS_VOICE_ID)

    headers = {
        "xi-api-key": config.ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
    }

    payload = {
        "text": text,
        "model_id": MODEL_ID,
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
            "style": 0.0,
            "use_speaker_boost": True,
        },
    }

    params = {"output_format": OUTPUT_FORMAT}

    chunks: list[bytes] = []
    async with httpx.AsyncClient(timeout=30.0) as client:
        async with client.stream(
            "POST", url, headers=headers, json=payload, params=params
        ) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                chunks.append(chunk)

    audio = b"".join(chunks)
    logger.debug("TTS produced %d bytes of ulaw audio for: %.40s...", len(audio), text)
    return audio


async def synthesize_streaming(text: str) -> AsyncIterator[bytes]:
    """Yield mu-law audio chunks as they arrive from ElevenLabs.

    This allows sending audio to Twilio before the full TTS response is ready,
    reducing perceived latency.

    Args:
        text: The text to speak.

    Yields:
        Chunks of mu-law audio bytes.
    """
    url = ELEVENLABS_STREAM_URL.format(voice_id=config.ELEVENLABS_VOICE_ID)

    headers = {
        "xi-api-key": config.ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
    }

    payload = {
        "text": text,
        "model_id": MODEL_ID,
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
            "style": 0.0,
            "use_speaker_boost": True,
        },
    }

    params = {"output_format": OUTPUT_FORMAT}

    async with httpx.AsyncClient(timeout=30.0) as client:
        async with client.stream(
            "POST", url, headers=headers, json=payload, params=params
        ) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                yield chunk
