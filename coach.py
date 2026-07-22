"""AI narrative layer (STUB).

Design rule: feed EXTRACTED FEATURES, never raw position dumps. Cheap + accurate.
The model returns (a) a plain-English explanation and (b) a suggested "better play"
described as a direction/target the renderer can draw as the green path.
"""
from __future__ import annotations
import json
from detect_deaths import DeathAnalysis


def build_prompt(a: DeathAnalysis) -> str:
    return (
        "You are a Dota 2 coach. Given ONE death's features, explain the mistake in "
        "2-3 sentences, then give a concrete better play. Respond as JSON: "
        '{"explanation": str, "better_play": str, '
        '"suggest_target": "retreat|ward|group|farm_safe"}.\n\n'
        f"Death label (heuristic): {a.label}\n"
        f"Time: {a.time:.0f}s\n"
        f"Features: {json.dumps(a.features)}"
    )


def get_feedback(a: DeathAnalysis) -> dict:
    """STUB — returns a heuristic message. Swap in the Claude call below."""
    # from anthropic import Anthropic
    # msg = Anthropic().messages.create(
    #     model="claude-sonnet-5", max_tokens=400,
    #     messages=[{"role": "user", "content": build_prompt(a)}])
    # return json.loads(msg.content[0].text)
    f = a.features
    return {
        "explanation": f"{a.label}: nearest ally was {f['nearest_ally']} away and "
                       f"{f['gankers_were_far']} enemies had rotated unseen.",
        "better_play": "Hold back until you have vision or a teammate nearby.",
        "suggest_target": "retreat",
    }
