"""Entry point: start the server and run all test scenarios.

Usage::

    python main.py                  # Run all 12 scenarios sequentially
    python main.py --scenario simple_scheduling   # Run one scenario
    python main.py --list           # List available scenarios
    python main.py --analyze        # Only run the post-call analyzer on existing transcripts
"""

import argparse
import asyncio
import logging
import sys
import threading

import uvicorn

import config
from analyzer import analyze_all_transcripts
from call_manager import make_call, hang_up
from scenarios import SCENARIOS, list_scenario_names
from server import active_calls, app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Timeout for a single call (seconds).  If the call hasn't finished by
# this time we assume it's stuck and move on.
CALL_TIMEOUT = 120  # 2 minutes
# Pause between consecutive calls to avoid hammering the test line.
INTER_CALL_DELAY = 10  # seconds


def start_server() -> None:
    """Run the FastAPI/uvicorn server in a background thread."""
    uvi_config = uvicorn.Config(
        app,
        host=config.SERVER_HOST,
        port=config.SERVER_PORT,
        log_level="info",
    )
    server = uvicorn.Server(uvi_config)
    server.run()


async def run_scenario(scenario_name: str) -> str | None:
    """Initiate a call for one scenario and wait for it to complete.

    Args:
        scenario_name: The scenario identifier.

    Returns:
        The Twilio Call SID, or None if the call failed.
    """
    logger.info("=" * 60)
    logger.info("STARTING SCENARIO: %s", scenario_name)
    logger.info("=" * 60)

    try:
        call_sid = make_call(scenario_name)
    except Exception as exc:
        logger.error("Failed to initiate call for %s: %s", scenario_name, exc)
        return None

    # Wait for the call to finish (signalled by the WebSocket handler or
    # the status callback).  active_calls uses threading.Event because
    # the server runs on a separate thread.
    done_event = active_calls.setdefault(call_sid, threading.Event())

    # Poll the threading.Event in an async-friendly way.
    elapsed = 0.0
    poll_interval = 1.0
    while elapsed < CALL_TIMEOUT:
        if done_event.is_set():
            logger.info("Scenario %s completed (SID=%s)", scenario_name, call_sid)
            break
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
    else:
        logger.warning(
            "Scenario %s timed out after %ds - hanging up", scenario_name, CALL_TIMEOUT
        )
        hang_up(call_sid)
        await asyncio.sleep(2)

    # Clean up.
    active_calls.pop(call_sid, None)
    return call_sid


async def run_all(scenario_names: list[str]) -> None:
    """Run scenarios sequentially with pauses between them."""
    logger.info("Will run %d scenario(s): %s", len(scenario_names), scenario_names)

    # Give the server a moment to start up.
    await asyncio.sleep(3)

    completed = []
    for i, name in enumerate(scenario_names, 1):
        logger.info("--- Scenario %d/%d: %s ---", i, len(scenario_names), name)
        sid = await run_scenario(name)
        if sid:
            completed.append((name, sid))

        if i < len(scenario_names):
            logger.info("Waiting %ds before next call...", INTER_CALL_DELAY)
            await asyncio.sleep(INTER_CALL_DELAY)

    logger.info("=" * 60)
    logger.info("ALL SCENARIOS COMPLETE: %d/%d succeeded", len(completed), len(scenario_names))
    for name, sid in completed:
        logger.info("  %s -> %s", name, sid)
    logger.info("=" * 60)
    logger.info("Transcripts saved in: transcripts/")
    logger.info("Run 'python main.py --analyze' to generate a bug report.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="PrettyGoodAI Voice Bot - test an AI medical office agent"
    )
    parser.add_argument(
        "--scenario",
        type=str,
        help="Run a single scenario by name",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all available scenarios and exit",
    )
    parser.add_argument(
        "--analyze",
        action="store_true",
        help="Run the post-call analyzer on existing transcripts",
    )
    args = parser.parse_args()

    # --list: just print scenario names and exit.
    if args.list:
        print("Available scenarios:")
        for s in SCENARIOS:
            print(f"  {s.name:25s} - {s.description}")
        sys.exit(0)

    # --analyze: run analyzer only.
    if args.analyze:
        asyncio.run(analyze_all_transcripts())
        sys.exit(0)

    # Validate config.
    missing = config.validate()
    if missing:
        logger.error("Missing required configuration: %s", ", ".join(missing))
        logger.error("Copy .env.example to .env and fill in the values.")
        sys.exit(1)

    # Determine which scenarios to run.
    if args.scenario:
        names = [args.scenario]
        if args.scenario not in list_scenario_names():
            logger.error("Unknown scenario: %s", args.scenario)
            logger.error("Available: %s", list_scenario_names())
            sys.exit(1)
    else:
        names = list_scenario_names()

    # Start the FastAPI server in a background thread.
    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()
    logger.info("Server starting on %s:%d", config.SERVER_HOST, config.SERVER_PORT)

    # Run the scenarios.
    try:
        asyncio.run(run_all(names))
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        logger.info("Done.")


if __name__ == "__main__":
    main()
