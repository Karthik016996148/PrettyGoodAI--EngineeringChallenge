"""Post-call transcript analyzer.

Reads all saved transcripts and uses GPT-4o to identify bugs, quality issues,
and areas for improvement in the AI agent's responses.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from openai import AsyncOpenAI

import config

logger = logging.getLogger(__name__)

TRANSCRIPTS_DIR = Path("transcripts")
REPORTS_DIR = Path("reports")

client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)

ANALYSIS_SYSTEM_PROMPT = """\
You are a QA analyst reviewing transcripts of phone calls between patients \
and an AI medical office receptionist. Your job is to identify bugs, quality \
issues, and areas for improvement.

For each transcript, analyze and report on:

1. **Incorrect Information**: Any factually wrong statements, made-up details, \
   or hallucinated data (e.g., inventing appointment times, wrong office info).

2. **Comprehension Failures**: Times the agent failed to understand what the \
   patient was asking or saying.

3. **Logic Errors**: Contradictions, nonsensical responses, or broken \
   conversation flow.

4. **Triage Issues**: For medical symptom calls, did the agent respond \
   appropriately? Did it escalate urgent symptoms correctly?

5. **Awkward Phrasing**: Unnatural language, robotic responses, or phrasing \
   a human receptionist would never use.

6. **Edge Case Handling**: How did the agent handle confused patients, \
   language mixing, interruptions, or multi-part requests?

7. **Missing Capabilities**: Things a patient would reasonably expect \
   (e.g., "Can you transfer me?") that the agent couldn't handle.

Rate each issue as:
- CRITICAL: Would cause real harm or major patient frustration
- MODERATE: Noticeable quality issue but not harmful
- MINOR: Small polish issue

Be specific. Quote the exact text from the transcript when citing issues.
If a call went well with no issues, say so explicitly.
"""

SUMMARY_SYSTEM_PROMPT = """\
You are a QA analyst writing the executive summary of a bug report for an AI \
medical office receptionist system. You have individual analyses of multiple \
test calls. Write a concise summary that:

1. Lists the most important bugs found across all calls, grouped by severity.
2. Identifies patterns (issues that appeared in multiple calls).
3. Highlights what the agent did well.
4. Gives a brief overall quality assessment.

Format the output as clean Markdown suitable for a bug report document.
"""


async def analyze_transcript(transcript_data: dict) -> str:
    """Analyze a single transcript and return the analysis text.

    Args:
        transcript_data: The parsed JSON data from a transcript file.

    Returns:
        The analysis text from the LLM.
    """
    scenario = transcript_data.get("scenario", "unknown")
    turns = transcript_data.get("transcript", [])

    # Format the transcript for the LLM.
    formatted = f"Scenario: {scenario}\n\n"
    for turn in turns:
        speaker = turn["speaker"].upper()
        text = turn["text"]
        ts = turn.get("timestamp", 0)
        formatted += f"[{ts:.1f}s] {speaker}: {text}\n"

    resp = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": ANALYSIS_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Please analyze this call transcript:\n\n{formatted}"
                ),
            },
        ],
        temperature=0.3,
        max_tokens=1500,
    )

    return resp.choices[0].message.content.strip()


async def generate_summary(analyses: list[tuple[str, str]]) -> str:
    """Generate an overall summary from individual call analyses.

    Args:
        analyses: List of (scenario_name, analysis_text) tuples.

    Returns:
        The summary text.
    """
    combined = ""
    for scenario, analysis in analyses:
        combined += f"\n\n## Call: {scenario}\n\n{analysis}"

    resp = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Here are the individual call analyses. Write an "
                    f"executive summary bug report:\n{combined}"
                ),
            },
        ],
        temperature=0.3,
        max_tokens=2000,
    )

    return resp.choices[0].message.content.strip()


async def analyze_all_transcripts() -> Path:
    """Analyze all transcripts in the transcripts directory.

    Returns:
        Path to the generated bug report.
    """
    REPORTS_DIR.mkdir(exist_ok=True)

    transcript_files = sorted(TRANSCRIPTS_DIR.glob("*.json"))
    if not transcript_files:
        logger.warning("No transcript files found in %s", TRANSCRIPTS_DIR)
        report_path = REPORTS_DIR / "bug_report.md"
        report_path.write_text(
            "# Bug Report\n\nNo transcripts found to analyze.\n",
            encoding="utf-8",
        )
        return report_path

    logger.info("Analyzing %d transcript(s)...", len(transcript_files))

    analyses: list[tuple[str, str]] = []

    for tf in transcript_files:
        logger.info("Analyzing: %s", tf.name)
        data = json.loads(tf.read_text(encoding="utf-8"))
        scenario = data.get("scenario", tf.stem)

        try:
            analysis = await analyze_transcript(data)
            analyses.append((scenario, analysis))
            logger.info("  -> Analysis complete for %s", scenario)
        except Exception as exc:
            logger.error("  -> Failed to analyze %s: %s", scenario, exc)
            analyses.append((scenario, f"Analysis failed: {exc}"))

    # Generate summary.
    logger.info("Generating summary...")
    try:
        summary = await generate_summary(analyses)
    except Exception as exc:
        logger.error("Summary generation failed: %s", exc)
        summary = f"Summary generation failed: {exc}"

    # Build the report.
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    report_lines = [
        f"# Bug Report - PrettyGoodAI Voice Bot Testing",
        f"",
        f"Generated: {timestamp}",
        f"Total calls analyzed: {len(analyses)}",
        f"",
        f"---",
        f"",
        f"## Executive Summary",
        f"",
        summary,
        f"",
        f"---",
        f"",
        f"## Individual Call Analyses",
        f"",
    ]

    for scenario, analysis in analyses:
        report_lines.extend([
            f"### {scenario}",
            f"",
            analysis,
            f"",
            f"---",
            f"",
        ])

    report_text = "\n".join(report_lines)
    report_path = REPORTS_DIR / "bug_report.md"
    report_path.write_text(report_text, encoding="utf-8")

    logger.info("Bug report saved: %s", report_path)
    return report_path
