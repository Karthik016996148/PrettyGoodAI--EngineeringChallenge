"""Transcript recording and persistence.

Accumulates utterances from both sides of a call and saves the result
as a structured JSON file.
"""

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

TRANSCRIPTS_DIR = Path("transcripts")


@dataclass
class Utterance:
    """A single utterance in the conversation."""

    speaker: str          # "agent" or "patient"
    text: str
    timestamp: float      # seconds since call start


@dataclass
class TranscriptRecorder:
    """Accumulates utterances and writes them to disk.

    Usage::

        recorder = TranscriptRecorder(scenario_name="simple_scheduling")
        recorder.start()
        recorder.add("agent", "Hello, how can I help you?")
        recorder.add("patient", "I'd like to schedule an appointment.")
        path = recorder.save(call_sid="CA123...")
    """

    scenario_name: str
    call_sid: str = ""
    utterances: list[Utterance] = field(default_factory=list)
    _start_time: float = 0.0

    def start(self) -> None:
        """Mark the beginning of the conversation."""
        self._start_time = time.time()

    def add(self, speaker: str, text: str) -> None:
        """Record an utterance.

        Args:
            speaker: Either ``"agent"`` or ``"patient"``.
            text: The transcribed/generated text.
        """
        elapsed = time.time() - self._start_time if self._start_time else 0.0
        utt = Utterance(speaker=speaker, text=text, timestamp=round(elapsed, 2))
        self.utterances.append(utt)
        logger.info("[%s] %s: %s", self.scenario_name, speaker.upper(), text)

    def save(self, call_sid: str = "") -> Path:
        """Write the transcript to a JSON file and return the path.

        Args:
            call_sid: Optional Twilio call SID for metadata.

        Returns:
            The ``Path`` to the saved JSON file.
        """
        if call_sid:
            self.call_sid = call_sid

        TRANSCRIPTS_DIR.mkdir(exist_ok=True)

        duration = (
            time.time() - self._start_time if self._start_time else 0.0
        )

        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filename = f"{self.scenario_name}_{ts}.json"
        path = TRANSCRIPTS_DIR / filename

        data = {
            "call_id": self.call_sid,
            "scenario": self.scenario_name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "duration_seconds": round(duration, 1),
            "transcript": [
                {
                    "speaker": u.speaker,
                    "text": u.text,
                    "timestamp": u.timestamp,
                }
                for u in self.utterances
            ],
            "metadata": {
                "twilio_call_sid": self.call_sid,
                "total_turns": len(self.utterances),
            },
        }

        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("Transcript saved: %s (%d turns, %.1fs)", path, len(self.utterances), duration)
        return path

    def to_readable_text(self) -> str:
        """Format the transcript as human-readable text for analysis."""
        lines = [f"=== Scenario: {self.scenario_name} ===\n"]
        for u in self.utterances:
            label = "AGENT" if u.speaker == "agent" else "PATIENT"
            lines.append(f"[{u.timestamp:6.1f}s] {label}: {u.text}")
        return "\n".join(lines)
