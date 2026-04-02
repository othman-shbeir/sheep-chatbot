import uuid
from functools import lru_cache

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI

from app.config import Config
from app.db import (
    SessionLocal,
    add_message,
    create_summary,
    get_latest_summary,
    get_messages_after_order,
    get_or_create_thread,
    get_user_by_id,
)
from app.utils import extract_stream_text_content, extract_text_content
from app.core.logging_config import get_logger

logger = get_logger("system", __name__)

SYSTEM_PROMPT = """
You are a helpful, warm, and knowledgeable AI assistant.

You are especially strong in sheep, livestock care, sheep farming, sheep health,
breeding, feeding, productivity, disease prevention, and flock management 
so if the user asks you anything in general like the meaning of a word or how to do something with a specific word like 'pregnants' only with out mentioning the sheeps,
 you have to put it in the context of the sheeps(for example if the user asks about 'pregnants' you have to answer about 'pregnant sheep').
When the user asks about sheep or livestock, respond with practical, breeder-friendly,
expert-level explanations.

also if the user asks in arabic:
"كيف يمكنني التعامل مع الحيل فهو يقصد النعاج الحيل"
also if the user asks in arabic:
"كيف يمكنني التعامل مع الدفيع فهو يقصد النعاج الدفيع يعني الحوامل وهكذا"

However, you are NOT restricted to sheep topics.
You can also answer general questions normally and helpfully.

Response style rules:
1. Start with a short, warm, natural opening line when appropriate.
2. Answer clearly, accurately, and in an organized way.
3. Use a smooth, guided, narrative style instead of dry encyclopedia phrasing.
4. When useful, add a practical note, breeder tip, warning, or insight.
5. Do not stop at the direct answer only.
6. When it feels natural and useful, end with ONE very friendly follow-up question that is directly connected to the same topic.
7. The follow-up question must feel natural, helpful, and specific, not generic.
8. Avoid robotic wording, abrupt endings, or forced questions.
9. Never refuse only because the topic is outside sheep. Just answer normally.
10. If search results are available and relevant, ground the answer in them.
11. If the question is about sheep/livestock, your answer should feel especially practical and domain-aware.
12. Do not force a follow-up question for short factual replies, coding help, or direct task completion.

Examples of good follow-up behavior:
- Life stages of sheep -> ask whether the user wants age estimation by teeth.
- Feeding lambs -> ask whether the user wants a feeding schedule by age.
- Disease symptoms -> ask whether the user wants prevention steps or urgent warning signs.
- Breeding -> ask whether the user wants mating signs or pregnancy care.
"""

SUMMARY_PROMPT_TEMPLATE = """
You are summarizing a conversation thread for long-term memory.

Write a compact but useful summary that preserves:
- important user preferences
- important facts the user shared
- unresolved questions
- important context needed to continue the conversation naturally
- key task progress or decisions

Be concise but informative.
Do not invent details.

Conversation to summarize:
{conversation_text}
"""


def require_database():
    if not Config.DATABASE_URL:
        raise ValueError("DATABASE_URL is not set in the environment.")


@lru_cache(maxsize=1)
def get_llm() -> ChatGoogleGenerativeAI:
    google_api_key = Config.GOOGLE_API_KEY
    if not google_api_key:
        raise ValueError("GOOGLE_API_KEY is not set in the environment.")

    return ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        api_key=google_api_key,
        temperature=0.0,
        max_retries=2,
    )


@lru_cache(maxsize=1)
def get_model_with_search():
    return get_llm().bind_tools([{"google_search": {}}])


def _normalize_required_text(value: str, field_name: str) -> str:
    normalized = (value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} is required.")
    return normalized


def _parse_user_uuid(user_id: str) -> tuple[str, uuid.UUID]:
    normalized_user_id = _normalize_required_text(user_id, "user_id")

    try:
        user_uuid = uuid.UUID(normalized_user_id)
    except ValueError as e:
        raise ValueError("user_id must be a valid UUID.") from e

    return normalized_user_id, user_uuid


def _build_thread_title(user_input: str, *, max_length: int = 60) -> str:
    if len(user_input) <= max_length:
        return user_input
    return f"{user_input[:max_length]}..."


def _to_prompt_message(message):
    if message.role == "user":
        return HumanMessage(content=message.content)
    if message.role == "assistant":
        return AIMessage(content=message.content)
    if message.role == "system":
        return SystemMessage(content=message.content)
    return AIMessage(content=message.content)


def _prepare_thread_turn(
    user_input: str,
    thread_id: str,
    user_id: str,
    *,
    operation_name: str,
) -> tuple[str, str, str]:
    require_database()
    normalized_thread_id = _normalize_required_text(thread_id, "thread_id")
    normalized_user_input = _normalize_required_text(user_input, "user_input")
    normalized_user_id, user_uuid = _parse_user_uuid(user_id)

    with SessionLocal() as session:
        user = get_user_by_id(session, user_uuid)
        if not user:
            logger.warning("%s failed: User ID %s not found", operation_name, user_uuid)
            raise ValueError("User not found.")

        logger.info("Loaded User %s from DB for %s", user_uuid, operation_name)

        get_or_create_thread(
            session=session,
            thread_id=normalized_thread_id,
            user_id=user.id,
            title=_build_thread_title(normalized_user_input),
        )

        add_message(
            session=session,
            thread_id=normalized_thread_id,
            role="user",
            content=normalized_user_input,
        )
        session.commit()

    return normalized_user_input, normalized_thread_id, normalized_user_id


def _store_assistant_message(thread_id: str, assistant_text: str) -> None:
    with SessionLocal() as session:
        add_message(
            session=session,
            thread_id=thread_id,
            role="assistant",
            content=assistant_text,
        )
        session.commit()


def build_context_messages(thread_id: str):
    require_database()
    with SessionLocal() as session:
        latest_summary = get_latest_summary(session, thread_id)
        covered_until_order = latest_summary.covered_until_order if latest_summary else None
        unsummarized_messages = get_messages_after_order(
            session,
            thread_id,
            covered_until_order,
        )

    messages = [SystemMessage(content=SYSTEM_PROMPT)]

    if latest_summary:
        messages.append(
            SystemMessage(
                content=(
                    "Conversation memory summary:\n"
                    f"{latest_summary.summary_text}\n\n"
                    "Use this as background context from earlier conversation."
                )
            )
        )

    messages.extend(_to_prompt_message(msg) for msg in unsummarized_messages)

    return messages


def maybe_compact_thread(
    thread_id: str,
    summary_trigger_count: int | None = None,
    keep_recent_count: int | None = None,
):
    require_database()
    summary_trigger_count = (
        Config.SUMMARY_TRIGGER_COUNT
        if summary_trigger_count is None
        else summary_trigger_count
    )
    keep_recent_count = (
        Config.KEEP_RECENT_COUNT if keep_recent_count is None else keep_recent_count
    )

    if summary_trigger_count < 1:
        raise ValueError("summary_trigger_count must be greater than or equal to 1.")

    if keep_recent_count < 0:
        raise ValueError("keep_recent_count must be greater than or equal to 0.")

    with SessionLocal() as session:
        latest_summary = get_latest_summary(session, thread_id)
        covered_until_order = latest_summary.covered_until_order if latest_summary else None
        unsummarized_messages = get_messages_after_order(
            session,
            thread_id,
            covered_until_order,
        )

        if len(unsummarized_messages) <= summary_trigger_count:
            return

        messages_to_summarize = (
            unsummarized_messages[:-keep_recent_count]
            if keep_recent_count
            else unsummarized_messages
        )
        if not messages_to_summarize:
            return

        conversation_lines = [f"{msg.role.upper()}: {msg.content}" for msg in messages_to_summarize]
        conversation_text = "\n".join(conversation_lines)

        if latest_summary:
            conversation_text = (
                f"Previous summary:\n{latest_summary.summary_text}\n\n"
                f"New messages:\n{conversation_text}"
            )

        prompt = SUMMARY_PROMPT_TEMPLATE.format(conversation_text=conversation_text)
        summary_response = get_llm().invoke([HumanMessage(content=prompt)])
        summary_text = extract_text_content(summary_response)

        last_msg = messages_to_summarize[-1]

        create_summary(
            session=session,
            thread_id=thread_id,
            summary_text=summary_text,
            covered_until_message_id=last_msg.id,
            covered_until_order=last_msg.message_order,
            model_name="gemini-2.5-flash",
            metadata_json={"strategy": "rolling_summary"},
        )

        session.commit()


def run_agent(
    user_input: str,
    thread_id: str,
    user_id: str,
) -> str:
    user_input, thread_id, _ = _prepare_thread_turn(
        user_input,
        thread_id,
        user_id,
        operation_name="run_agent",
    )

    context_messages = build_context_messages(thread_id)
    response = get_model_with_search().invoke(context_messages)
    assistant_text = extract_text_content(response)

    _store_assistant_message(thread_id, assistant_text)
    maybe_compact_thread(thread_id)

    return assistant_text


def run_agent_stream(
    user_input: str,
    thread_id: str,
    user_id: str,
):
    user_input, thread_id, _ = _prepare_thread_turn(
        user_input,
        thread_id,
        user_id,
        operation_name="run_agent_stream",
    )

    context_messages = build_context_messages(thread_id)

    assistant_parts: list[str] = []
    for chunk in get_model_with_search().stream(context_messages):
        delta = extract_stream_text_content(chunk)
        if not delta:
            continue
        assistant_parts.append(delta)
        yield delta

    assistant_text = "".join(assistant_parts).strip()

    _store_assistant_message(thread_id, assistant_text)
    maybe_compact_thread(thread_id)

