"""LLM-driven conversation engine.

Manages the patient-side dialogue by keeping a running chat history and
generating contextually appropriate responses based on a scenario prompt.
Uses OpenAI GPT-4o-mini for fast, cheap inference.
"""

import logging
from dataclasses import dataclass, field

from openai import AsyncOpenAI

import config

logger = logging.getLogger(__name__)

client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)

MODEL = "gpt-4o-mini"
TEMPERATURE = 0.8
MAX_TOKENS = 150  # keep responses short and natural


@dataclass
class ConversationEngine:
    """Manages one phone-call conversation for a specific scenario.

    Attributes:
        system_prompt: The scenario-specific system prompt that tells the LLM
            how to behave as a patient.
        messages: The running OpenAI chat-format message list.
        scenario_name: Human-readable scenario label.
    """

    system_prompt: str
    scenario_name: str = ""
    messages: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.messages = [{"role": "system", "content": self.system_prompt}]

    async def get_opening_line(self) -> str:
        """Generate what the patient says first when the agent picks up.

        Returns:
            The patient's opening line as text.
        """
        # Add a user message representing the agent's greeting (we haven't
        # heard it yet, so we simulate the prompt).
        self.messages.append(
            {
                "role": "user",
                "content": (
                    "[The medical office AI agent has just answered the phone "
                    "and greeted you. Respond naturally as the patient. "
                    "Keep it brief - one or two sentences.]"
                ),
            }
        )
        response = await self._complete()
        return response

    async def respond_to_agent(self, agent_text: str) -> str:
        """Generate the patient's response to what the agent just said.

        Args:
            agent_text: The transcribed text of what the AI agent said.

        Returns:
            The patient's next line as text.
        """
        self.messages.append({"role": "user", "content": agent_text})
        response = await self._complete()
        return response

    async def should_hang_up(self, agent_text: str) -> bool:
        """Determine if the conversation has naturally concluded.

        Uses a quick LLM check to see if the agent has said goodbye or
        the interaction is complete.

        Args:
            agent_text: The latest thing the agent said.

        Returns:
            True if the patient should hang up.
        """
        check_messages = [
            {
                "role": "system",
                "content": (
                    "You are analyzing a phone conversation between a patient "
                    "and a medical office AI. Based on what the agent just "
                    "said, determine if the conversation is naturally winding "
                    "down. Answer 'yes' if ANY of these are true:\n"
                    "- The agent said goodbye, take care, or similar\n"
                    "- The agent confirmed everything is done/set\n"
                    "- The agent asked 'is there anything else' (meaning main task is done)\n"
                    "- The agent said they can't help further\n"
                    "- The conversation has clearly concluded\n"
                    "Respond with ONLY 'yes' or 'no'."
                ),
            },
            {
                "role": "user",
                "content": f"Agent said: \"{agent_text}\"",
            },
        ]

        resp = await client.chat.completions.create(
            model=MODEL,
            messages=check_messages,
            temperature=0.0,
            max_tokens=5,
        )
        answer = resp.choices[0].message.content.strip().lower()
        return answer.startswith("yes")

    async def _complete(self) -> str:
        """Run chat completion and append the assistant response to history."""
        resp = await client.chat.completions.create(
            model=MODEL,
            messages=self.messages,
            temperature=TEMPERATURE,
            max_tokens=MAX_TOKENS,
        )
        text = resp.choices[0].message.content.strip()
        self.messages.append({"role": "assistant", "content": text})
        logger.debug("[%s] Patient says: %s", self.scenario_name, text)
        return text

    def get_full_history(self) -> list[dict]:
        """Return the full message history (excluding system prompt)."""
        return [m for m in self.messages if m["role"] != "system"]
