"""Central configuration loaded from environment variables."""

import os
from dotenv import load_dotenv

load_dotenv()


# --- Twilio ---
TWILIO_ACCOUNT_SID: str = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN: str = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE_FROM: str = os.environ.get("TWILIO_PHONE_FROM", "")
TARGET_PHONE_NUMBER: str = os.environ.get("TARGET_PHONE_NUMBER", "+18054398008")

# --- Deepgram ---
DEEPGRAM_API_KEY: str = os.environ.get("DEEPGRAM_API_KEY", "")

# --- ElevenLabs ---
ELEVENLABS_API_KEY: str = os.environ.get("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID: str = os.environ.get("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")

# --- OpenAI ---
OPENAI_API_KEY: str = os.environ.get("OPENAI_API_KEY", "")

# --- Server ---
SERVER_HOST: str = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT: int = int(os.environ.get("SERVER_PORT", "8765"))
NGROK_DOMAIN: str = os.environ.get("NGROK_DOMAIN", "")

# --- Audio constants ---
MULAW_SAMPLE_RATE: int = 8000
MULAW_CHANNELS: int = 1
MULAW_SAMPLE_WIDTH: int = 1  # 8-bit mulaw

# --- Derived ---
WS_URL: str = f"wss://{NGROK_DOMAIN}/media-stream"


def validate() -> list[str]:
    """Return a list of missing required config values."""
    required = {
        "TWILIO_ACCOUNT_SID": TWILIO_ACCOUNT_SID,
        "TWILIO_AUTH_TOKEN": TWILIO_AUTH_TOKEN,
        "TWILIO_PHONE_FROM": TWILIO_PHONE_FROM,
        "DEEPGRAM_API_KEY": DEEPGRAM_API_KEY,
        "ELEVENLABS_API_KEY": ELEVENLABS_API_KEY,
        "OPENAI_API_KEY": OPENAI_API_KEY,
        "NGROK_DOMAIN": NGROK_DOMAIN,
    }
    return [k for k, v in required.items() if not v]
