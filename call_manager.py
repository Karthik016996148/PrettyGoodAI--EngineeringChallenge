"""Twilio call initiation and management.

Handles making outbound calls and providing TwiML instructions that connect
the call audio to our WebSocket server via bidirectional Media Streams.
"""

import logging

from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse, Connect

import config

logger = logging.getLogger(__name__)

_client: Client | None = None


def _get_client() -> Client:
    """Lazy-init the Twilio REST client."""
    global _client
    if _client is None:
        _client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
    return _client


def make_call(scenario_name: str) -> str:
    """Initiate an outbound call to the test line.

    The call is configured to connect to our WebSocket server for
    bidirectional media streaming, passing the scenario name as a
    query parameter so the server knows which persona to use.

    Args:
        scenario_name: The scenario identifier (passed to the WS server).

    Returns:
        The Twilio Call SID.
    """
    client = _get_client()

    # The TwiML URL tells Twilio what to do when the call connects.
    # We point it at our FastAPI server's /twiml endpoint.
    twiml_url = f"https://{config.NGROK_DOMAIN}/twiml?scenario={scenario_name}"

    call = client.calls.create(
        to=config.TARGET_PHONE_NUMBER,
        from_=config.TWILIO_PHONE_FROM,
        url=twiml_url,
        status_callback=f"https://{config.NGROK_DOMAIN}/call-status",
        status_callback_event=["initiated", "ringing", "answered", "completed"],
        status_callback_method="POST",
        record=False,
        timeout=60,
    )

    logger.info(
        "Call initiated: SID=%s, scenario=%s, to=%s",
        call.sid,
        scenario_name,
        config.TARGET_PHONE_NUMBER,
    )
    return call.sid


def generate_twiml(scenario_name: str) -> str:
    """Generate TwiML that connects the call to our WebSocket.

    This is served by the FastAPI ``/twiml`` endpoint when Twilio asks
    what to do with the call.

    Args:
        scenario_name: Passed as a query parameter to the WebSocket URL
            so the server knows which patient persona to use.

    Returns:
        TwiML XML string.
    """
    response = VoiceResponse()

    connect = Connect()
    ws_url = f"wss://{config.NGROK_DOMAIN}/media-stream"
    stream = connect.stream(url=ws_url)
    stream.parameter(name="scenario", value=scenario_name)
    response.append(connect)

    twiml_str = str(response)
    logger.debug("Generated TwiML: %s", twiml_str)
    return twiml_str


def hang_up(call_sid: str) -> None:
    """Terminate a call in progress.

    Args:
        call_sid: The Twilio Call SID to hang up.
    """
    client = _get_client()
    try:
        client.calls(call_sid).update(status="completed")
        logger.info("Call hung up: SID=%s", call_sid)
    except Exception as exc:
        logger.warning("Failed to hang up call %s: %s", call_sid, exc)
