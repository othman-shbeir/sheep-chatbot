import os
from functools import lru_cache
import uuid

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI

from app.config import Config
from app.db import (
    SessionLocal,
    add_message,
    create_summary,
    get_all_messages,
    get_latest_summary,
    get_messages_after_summary,
    get_or_create_thread,
    get_or_create_user,
    get_thread_messages_for_user,
    get_threads_for_user,
    thread_belongs_to_user,
)
from app.utils import extract_text_content

SYSTEM_PROMPT = """
You are a helpful, warm, and knowledgeable AI assistant.

You are especially strong in sheep, livestock care, sheep farming, sheep health,
breeding, feeding, productivity, disease prevention, and flock management.
When the user asks about sheep or livestock, respond with practical, breeder-friendly,
expert-level explanations.

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


def require_database():
    if not Config.DATABASE_URL:
        raise ValueError("DATABASE_URL is not set in the environment.")


def build_context_messages(thread_id: str):
    require_database()
    with SessionLocal() as session:
        latest_summary = get_latest_summary(session, thread_id)
        unsummarized_messages = get_messages_after_summary(session, thread_id)

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

    for msg in unsummarized_messages:
        if msg.role == "user":
            messages.append(HumanMessage(content=msg.content))
        elif msg.role == "assistant":
            messages.append(AIMessage(content=msg.content))
        elif msg.role == "system":
            messages.append(SystemMessage(content=msg.content))
        else:
            messages.append(AIMessage(content=msg.content))

    return messages


def maybe_compact_thread(thread_id: str, summary_trigger_count: int = 20, keep_recent_count: int = 5):
    require_database()
    with SessionLocal() as session:
        latest_summary = get_latest_summary(session, thread_id)
        unsummarized_messages = get_messages_after_summary(session, thread_id)

        if len(unsummarized_messages) <= summary_trigger_count:
            return

        messages_to_summarize = unsummarized_messages[:-keep_recent_count]
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
    user_email: str,
    user_name: str | None = None,
) -> str:
    require_database()
    thread_id = thread_id.strip()
    user_input = user_input.strip()
    user_email = user_email.strip().lower()

    if not thread_id:
        raise ValueError("thread_id is required.")

    if not user_input:
        raise ValueError("user_input is required.")

    if not user_email:
        raise ValueError("user_email is required.")

    with SessionLocal() as session:
        user = get_or_create_user(session, email=user_email, name=user_name)

        title = user_input[:60]
        if len(user_input) > 60:
            title += "..."

        get_or_create_thread(
            session=session,
            thread_id=thread_id,
            user_id=user.id,
            title=title,
        )

        add_message(
            session=session,
            thread_id=thread_id,
            role="user",
            content=user_input,
        )

        session.commit()

    context_messages = build_context_messages(thread_id)
    response = get_model_with_search().invoke(context_messages)
    assistant_text = extract_text_content(response)

    with SessionLocal() as session:
        add_message(
            session=session,
            thread_id=thread_id,
            role="assistant",
            content=assistant_text,
        )
        session.commit()

    maybe_compact_thread(thread_id)

    return assistant_text


def get_all_threads(user_email: str) -> list[dict]:
    require_database()
    user_email = user_email.strip().lower()
    with SessionLocal() as session:
        return get_threads_for_user(session, user_email)


class ThreadNotFoundError(Exception):
    pass


def get_thread_history(thread_id: str, user_id: str) -> list[dict]:
    require_database()
    thread_id = (thread_id or "").strip()
    user_id = (user_id or "").strip()

    if not thread_id:
        raise ValueError("thread_id is required.")

    if not user_id:
        raise ValueError("user_id is required.")

    try:
        user_uuid = uuid.UUID(user_id)
    except Exception as e:
        raise ValueError("user_id must be a valid UUID.") from e

    with SessionLocal() as session:
        if not thread_belongs_to_user(session, thread_id=thread_id, user_id=user_uuid):
            raise ThreadNotFoundError("Thread not found.")

        messages = get_thread_messages_for_user(session, thread_id=thread_id, user_id=user_uuid)

    return [
        {
            "id": str(msg.id),
            "role": msg.role,
            "content": msg.content,
            "message_order": msg.message_order,
            "created_at": msg.created_at.isoformat() if msg.created_at else None,
        }
        for msg in messages
    ]