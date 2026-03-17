import os
from typing import Annotated, TypedDict

from dotenv import load_dotenv

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage

from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import InMemorySaver

load_dotenv()

tracked_threads = {}

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


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
if not GOOGLE_API_KEY:
    raise ValueError("GOOGLE_API_KEY is not set in the environment.")

llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    api_key=GOOGLE_API_KEY,
    temperature=0.0,
    max_retries=2,
)

model_with_search = llm.bind_tools([{"google_search": {}}])


def ensure_system_message(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Prepend system prompt if not already present."""
    if messages and isinstance(messages[0], SystemMessage):
        return messages
    return [SystemMessage(content=SYSTEM_PROMPT)] + messages


def normalize_ai_output(response: BaseMessage) -> AIMessage:
    """Ensure the returned object is an AIMessage."""
    if isinstance(response, AIMessage):
        return response

    content = getattr(response, "content", str(response))
    return AIMessage(content=content)


def extract_text_content(message: BaseMessage) -> str:
    """Safely extract readable text from model output."""
    content = getattr(message, "content", "")

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text" and "text" in item:
                    parts.append(item["text"])
                elif "text" in item:
                    parts.append(str(item["text"]))
            else:
                parts.append(str(item))
        return "\n".join(parts).strip()

    return str(content)


def chatbot_node(state: AgentState) -> dict:
    messages = ensure_system_message(state["messages"])
    response = model_with_search.invoke(messages)
    ai_message = normalize_ai_output(response)
    return {"messages": [ai_message]}


builder = StateGraph(AgentState)
builder.add_node("chatbot", chatbot_node)
builder.add_edge(START, "chatbot")
builder.add_edge("chatbot", END)

checkpointer = InMemorySaver()
graph = builder.compile(checkpointer=checkpointer)


def run_agent(user_input: str, thread_id: str = "default") -> str:
    """Run one turn of the agent using thread-based memory."""
    if thread_id not in tracked_threads:
        preview = user_input[:40] + "..." if len(user_input) > 40 else user_input
        tracked_threads[thread_id] = {
            "id": thread_id,
            "preview": preview,
        }

    config = {"configurable": {"thread_id": thread_id}}

    result = graph.invoke(
        {"messages": [HumanMessage(content=user_input)]},
        config=config,
    )

    final_message = result["messages"][-1]
    return extract_text_content(final_message)


def get_all_threads() -> list:
    """Return tracked conversation threads for UI listing."""
    return list(tracked_threads.values())


def get_thread_history(thread_id: str):
    """Return clean thread message history."""
    config = {"configurable": {"thread_id": thread_id}}
    snapshot = graph.get_state(config)

    if not snapshot or not snapshot.values:
        return []

    messages = snapshot.values.get("messages", [])
    history = []

    for msg in messages:
        role = "assistant"
        if isinstance(msg, HumanMessage):
            role = "user"
        elif isinstance(msg, SystemMessage):
            role = "system"

        history.append({
            "role": role,
            "content": extract_text_content(msg)
        })

    return history