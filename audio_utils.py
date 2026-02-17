"""Audio format conversion utilities for the Twilio <-> bot pipeline.

Twilio Media Streams use mu-law (G.711) audio at 8 kHz, mono, transmitted as
base64-encoded payloads inside JSON WebSocket messages.

ElevenLabs returns linear PCM audio.  We need to convert between the two.
"""

import audioop
import base64
import struct

import config


def pcm_to_mulaw(pcm_bytes: bytes, sample_width: int = 2) -> bytes:
    """Convert signed linear PCM bytes to mu-law encoded bytes.

    Args:
        pcm_bytes: Raw PCM audio (little-endian, signed).
        sample_width: Bytes per sample in the input (default 2 = 16-bit).

    Returns:
        Mu-law encoded audio bytes.
    """
    return audioop.lin2ulaw(pcm_bytes, sample_width)


def mulaw_to_pcm(mulaw_bytes: bytes, sample_width: int = 2) -> bytes:
    """Convert mu-law encoded bytes to signed linear PCM.

    Args:
        mulaw_bytes: Mu-law encoded audio bytes.
        sample_width: Desired output bytes per sample (default 2 = 16-bit).

    Returns:
        Raw PCM audio (little-endian, signed).
    """
    return audioop.ulaw2lin(mulaw_bytes, sample_width)


def encode_for_twilio(pcm_bytes: bytes, sample_width: int = 2) -> str:
    """Convert PCM audio to a base64-encoded mu-law string for Twilio.

    This is the full pipeline for sending audio back through a Twilio
    bidirectional Media Stream.

    Args:
        pcm_bytes: Raw PCM audio (little-endian, signed, 8 kHz).
        sample_width: Bytes per sample (default 2 = 16-bit PCM).

    Returns:
        Base64 string of mu-law audio ready to embed in a Twilio media message.
    """
    mulaw = pcm_to_mulaw(pcm_bytes, sample_width)
    return base64.b64encode(mulaw).decode("ascii")


def decode_from_twilio(b64_payload: str) -> bytes:
    """Decode a Twilio media payload from base64 to raw mu-law bytes.

    Args:
        b64_payload: The base64-encoded string from a Twilio ``media`` event.

    Returns:
        Raw mu-law audio bytes.
    """
    return base64.b64decode(b64_payload)


def twilio_media_message(stream_sid: str, pcm_bytes: bytes) -> dict:
    """Build a Twilio WebSocket media message dict from PCM audio.

    Args:
        stream_sid: The Twilio stream SID for the active call.
        pcm_bytes: Raw PCM audio to send (16-bit, 8 kHz, mono).

    Returns:
        Dict ready to be serialized as JSON and sent over the WebSocket.
    """
    return {
        "event": "media",
        "streamSid": stream_sid,
        "media": {
            "payload": encode_for_twilio(pcm_bytes),
        },
    }


def twilio_mark_message(stream_sid: str, name: str) -> dict:
    """Build a Twilio mark message to track when audio finishes playing.

    Args:
        stream_sid: The Twilio stream SID.
        name: A label for this mark event.

    Returns:
        Dict ready to send over the WebSocket.
    """
    return {
        "event": "mark",
        "streamSid": stream_sid,
        "mark": {"name": name},
    }


def twilio_clear_message(stream_sid: str) -> dict:
    """Build a Twilio clear message to stop all queued audio (barge-in).

    Args:
        stream_sid: The Twilio stream SID.

    Returns:
        Dict ready to send over the WebSocket.
    """
    return {
        "event": "clear",
        "streamSid": stream_sid,
    }


def chunk_audio(audio_bytes: bytes, chunk_size: int = 640) -> list[bytes]:
    """Split audio bytes into fixed-size chunks for streaming.

    Twilio expects reasonably sized media payloads.  640 bytes of mu-law
    audio = 80 ms at 8 kHz which keeps latency low.

    Args:
        audio_bytes: The full audio payload.
        chunk_size: Bytes per chunk (default 640 = 80 ms of 8 kHz audio).

    Returns:
        List of byte chunks.
    """
    return [
        audio_bytes[i : i + chunk_size]
        for i in range(0, len(audio_bytes), chunk_size)
    ]
