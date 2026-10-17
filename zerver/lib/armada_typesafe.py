"""What Armada asks TypeSafe's Jev about a chat message that does not
mention the agent, and how it reads the answer
(https://docs.typesafe.ai/api).

This module imports nothing outside the standard library, so that
tools/armada/typesafe_addressing_eval, which measures it on labelled
chats from a machine without the Zulip environment, sends exactly what
the server sends.
"""

from collections.abc import Mapping, Sequence
from typing import Any, Final

ARMADA_TYPESAFE_QUESTION_ID: Final = "addressee"
ARMADA_TYPESAFE_AGENT_OPTION: Final = "agent"
ARMADA_TYPESAFE_PEOPLE_OPTION: Final = "people"


def armada_typesafe_agent_context(agent_name: str) -> str:
    """What Jev is told about the agent, so that it knows which requests
    the agent can plausibly take."""
    return (
        "This is a team chat called Armada, where people and an AI coding agent named "
        f"{agent_name} work together. Each channel is a git repository, and each topic is a "
        f"branch with its own working copy where {agent_name} works. {agent_name} writes and "
        "changes code, runs commands and tests, trains and evaluates models, browses the web "
        "and takes screenshots, makes plots and documents, answers questions about the code, "
        f"and posts updates in threads. People address {agent_name} by @-mentioning it, by "
        "replying to what it just said, or by asking for work in a topic where it's been "
        "working. People also talk to each other in the same topics, about the same work, "
        f"often right after {agent_name} speaks."
    )


def build_armada_typesafe_addressing_request(
    *,
    agent_name: str,
    model: str,
    channel: str,
    topic: str,
    agent_run_in_progress: bool,
    recent_messages: Sequence[Mapping[str, Any]],
    new_message_sender: str,
    new_message: str,
) -> dict[str, Any]:
    """recent_messages are the topic's messages before the new one, oldest
    first, as ArmadaRecentMessage dicts (sender, sender_is_bot, content)."""
    state = {
        "channel": channel,
        "topic": topic,
        "agent_run_in_progress": agent_run_in_progress,
        "recent_messages": [
            {
                "sender": str(message["sender"]),
                "is_bot": bool(message["sender_is_bot"]),
                "text": str(message["content"]),
            }
            for message in recent_messages
        ],
        "new_message": {"sender": new_message_sender, "text": new_message},
    }
    # The description of the agent goes with the question rather than in
    # the state: TypeSafe's guide keeps the state for the content judged,
    # and on the labelled chats it separated the two answers best there.
    # TypeSafe's notes on jev-1.13 say it leans towards a Choice's first
    # option, so the safe answer comes first: a run nobody asked for is the
    # worse mistake.
    question = {
        "type": "choice",
        "instructions": {
            "context": armada_typesafe_agent_context(agent_name),
            "question": (
                f"Is `new_message` addressed to {agent_name}, so that {agent_name} should "
                f"reply (a request, a question, or anything said to {agent_name}, such as a "
                "greeting or a follow-up), or is it meant for the people in the conversation?"
            ),
        },
        "criteria": {
            ARMADA_TYPESAFE_PEOPLE_OPTION: (
                "Meant for the people: talk between people, a question, request or greeting "
                f"to a person, a remark about {agent_name}, or thanks and acknowledgements that "
                f"need no reply, even to {agent_name}."
            ),
            ARMADA_TYPESAFE_AGENT_OPTION: (
                f"Addressed to {agent_name}, so that it should reply: a request, a question, a "
                f"greeting, a follow-up or correction to what {agent_name} just said or did, or "
                f"anything else said to {agent_name}."
            ),
        },
    }
    return {
        "model": model,
        "state": state,
        "questions": {ARMADA_TYPESAFE_QUESTION_ID: question},
    }


def armada_typesafe_agent_probability(answer: object) -> float:
    """The probability Jev gives the agent's option, from its answer to the
    request above; ValueError if the answer has none."""
    answers = answer.get("answers") if isinstance(answer, dict) else None
    choice = answers.get(ARMADA_TYPESAFE_QUESTION_ID) if isinstance(answers, dict) else None
    probabilities = choice.get("probabilities") if isinstance(choice, dict) else None
    probability = (
        probabilities.get(ARMADA_TYPESAFE_AGENT_OPTION) if isinstance(probabilities, dict) else None
    )
    if isinstance(probability, bool) or not isinstance(probability, int | float):
        raise ValueError(f"no probability for {ARMADA_TYPESAFE_AGENT_OPTION!r} in the answer")
    if not 0 <= probability <= 1:
        raise ValueError(f"the probability for {ARMADA_TYPESAFE_AGENT_OPTION!r} is {probability}")
    return float(probability)
