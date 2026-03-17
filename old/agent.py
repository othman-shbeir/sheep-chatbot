import os
import re
import json
import requests
from bs4 import BeautifulSoup
from typing import Annotated, Literal, List

from dotenv import load_dotenv
from typing_extensions import TypedDict

from langgraph.graph import START, END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.checkpoint.memory import MemorySaver

from langchain_core.messages import (
    SystemMessage,
    HumanMessage,
    AIMessage,
    BaseMessage,
    ToolMessage,
)
from langchain_core.tools import tool
from langchain_community.tools import DuckDuckGoSearchResults
from langchain_google_genai import ChatGoogleGenerativeAI

load_dotenv()

# Global dictionary to track threads and previews
tracked_threads = {}


# LLM
llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash",
                             api_key=os.getenv("GOOGLE_API_KEY"))


# Helpers
def remove_think_tags(text: str) -> str:
    """Remove any <think>...</think> blocks from model output."""
    if not isinstance(text, str):
        return text
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def get_latest_user_message(messages: List[BaseMessage]) -> str:
    """Return the content of the latest HumanMessage in the conversation."""
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            return msg.content
    return ""


def safe_get(url: str, timeout: int = 10) -> requests.Response:
    """Fetch a URL safely using a browser-like user agent."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    }
    response = requests.get(url, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response


def clean_html_to_text(html: str) -> str:
    """Convert HTML to readable plain text."""
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "noscript", "header", "footer", "svg"]):
        tag.decompose()

    text = soup.get_text(separator=" ", strip=True)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def split_into_chunks(text: str, max_chars: int = 500) -> List[str]:
    """Split long text into sentence-like chunks."""
    chunks = re.split(r"(?<=[\.\!\?\n])\s+", text)
    cleaned = [c.strip() for c in chunks if c.strip()]

    merged = []
    current = ""

    for chunk in cleaned:
        if len(current) + len(chunk) + 1 <= max_chars:
            current = f"{current} {chunk}".strip()
        else:
            if current:
                merged.append(current)
            current = chunk

    if current:
        merged.append(current)

    return merged


def rank_chunks_by_query(chunks: List[str], query: str, top_k: int = 8) -> List[str]:
    """Select the most query-relevant chunks using simple keyword matching."""
    query_words = [w.lower() for w in re.findall(r"\w+", query) if len(w) > 2]
    if not query_words:
        return chunks[:top_k]

    scored = []
    for chunk in chunks:
        lower_chunk = chunk.lower()
        score = sum(1 for word in query_words if word in lower_chunk)
        if score > 0:
            scored.append((score, chunk))

    scored.sort(key=lambda x: x[0], reverse=True)

    if scored:
        return [chunk for _, chunk in scored[:top_k]]

    return chunks[:top_k]


def parse_ddg_output(raw_results) -> List[dict]:
    """
    Normalize DuckDuckGoSearchResults output into a list of dicts.
    It may already be a list, or a string representation of a list.
    """
    if isinstance(raw_results, list):
        return raw_results

    if isinstance(raw_results, str):
        try:
            parsed = json.loads(raw_results)
            if isinstance(parsed, list):
                return parsed
        except Exception:
            pass

        try:
            import ast

            parsed = ast.literal_eval(raw_results)
            if isinstance(parsed, list):
                return parsed
        except Exception:
            pass

    return []


# State
class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    domain_status: str


# Search backend
duckduckgo_search = DuckDuckGoSearchResults(output_format="list")


# Tools
@tool
def add(a: int, b: int) -> int:
    """Add two integers and return the result."""
    return a + b


@tool
def multiply(a: int, b: int) -> int:
    """Multiply two integers and return the result."""
    return a * b


@tool
def divide(a: int, b: int) -> float:
    """Divide a by b and return the result. Raises an error if b is zero."""
    if b == 0:
        raise ValueError("Cannot divide by zero.")
    return a / b


@tool
def web_search(query: str) -> str:
    """
    Search the web for relevant pages and return a concise list of candidate results.
    Use this first when current or factual information is needed.
    """
    results = duckduckgo_search.invoke(query)
    normalized = parse_ddg_output(results)

    if not normalized:
        return "No search results found."

    lines = []
    for i, item in enumerate(normalized[:5], start=1):
        title = item.get("title", "No title")
        link = item.get("link", "No link")
        snippet = item.get("snippet", "No snippet")
        lines.append(
            f"{i}. Title: {title}\n" f"   URL: {link}\n" f"   Snippet: {snippet}"
        )

    return "\n\n".join(lines)


@tool
def fetch_page(url: str) -> str:
    """
    Fetch a webpage from a URL and return cleaned visible text content.
    Use this after identifying a promising URL from search results.
    """
    try:
        response = safe_get(url, timeout=10)
        text = clean_html_to_text(response.text)
        return text[:12000] if text else "No readable text found on the page."
    except Exception as e:
        return f"Failed to fetch page: {str(e)}"


@tool
def search_and_extract(query: str) -> str:
    """
    Search the web, fetch the most relevant result pages, and extract the text most relevant to the query.
    Use this when you need evidence for a factual answer.
    """
    try:
        raw_results = duckduckgo_search.invoke(query)
        results = parse_ddg_output(raw_results)

        if not results:
            return "No search results found."

        collected_sections = []

        for item in results[:3]:
            title = item.get("title", "No title")
            url = item.get("link", "")
            if not url:
                continue

            try:
                response = safe_get(url, timeout=10)
                page_text = clean_html_to_text(response.text)

                if not page_text:
                    continue

                chunks = split_into_chunks(page_text, max_chars=500)
                top_chunks = rank_chunks_by_query(chunks, query, top_k=5)

                section = (
                    f"Source Title: {title}\n"
                    f"Source URL: {url}\n"
                    f"Relevant Extract:\n"
                    + "\n".join(f"- {chunk}" for chunk in top_chunks[:5])
                )
                collected_sections.append(section)

            except Exception:
                continue

        if not collected_sections:
            return "Search results were found, but no readable evidence could be extracted."

        return "\n\n" + "\n\n".join(collected_sections[:3])

    except Exception as e:
        return f"Search-and-extract failed: {str(e)}"


tools = [add, multiply, divide, web_search, fetch_page, search_and_extract]
llm_with_tools = llm.bind_tools(tools)


# System Prompt
sys_msg = SystemMessage(content="""
You are a specialized assistant focused on sheep, goats, and related livestock topics.

Allowed domain:
1. Sheep and goats
2. Farming, breeding, and management
3. Veterinary medicine for sheep/goats
4. Housing, hygiene, and environmental conditions
5. Nutrition, feed, and fattening
6. Diseases, symptoms, treatment guidance, and prevention
7. Statistics and performance metrics
8. Financial and economic aspects of sheep/goat farming
9. Terminology ONLY when clearly related to sheep, goats, or livestock

Important instructions:
- For in-domain questions, provide strong specialist support.
- For out-of-domain questions, provide only a short and helpful answer.
- For factual, scientific, or current questions, prefer tool-based evidence.
- When web evidence is needed, first use web_search or search_and_extract.
- You may use fetch_page if you need to inspect a specific URL in more detail.
- Do not pretend to know facts you have not verified when evidence is needed.
- If evidence is insufficient or conflicting, say so clearly.
- Always answer in the same language as the user's latest message.
- Never mix languages in the final answer.
- If the user writes in Arabic, answer fully in Arabic only.
- Be clear, accurate, and practical.
""")


# Domain Guard
def domain_guard(state: AgentState):
    user_text = get_latest_user_message(state["messages"])
    text = user_text.lower()

    livestock_keywords = [
        "sheep",
        "goat",
        "goats",
        "lamb",
        "ewe",
        "ram",
        "livestock",
        "wool",
        "lambing",
        "grazing",
        "herd",
        "mutton",
        "chevon",
        "ruminant",
        "غنم",
        "أغنام",
        "ماعز",
        "خروف",
        "نعجة",
        "كبش",
        "صوف",
        "رعي",
        "قطيع",
        "تسمين",
        "حظيرة",
        "ماشية",
    ]

    if any(word in text for word in livestock_keywords):
        return {"domain_status": "IN_DOMAIN"}

    prompt = f"""
You are a strict domain classifier for a livestock assistant.

Your job is to determine whether the user's message is related to:
- sheep
- goats
- sheep/goat farming
- livestock management specifically involving sheep or goats

Return exactly one label:
IN_DOMAIN
OUT_OF_DOMAIN

IN_DOMAIN includes:
- sheep or goats directly
- breeding, feeding, veterinary care, housing
- productivity, milk, meat, wool, economics
- farming terminology when clearly tied to sheep/goats

OUT_OF_DOMAIN includes:
- programming
- politics
- general math
- cooking
- unrelated animals
- general world knowledge
- technology topics unrelated to livestock
- health topics unrelated to livestock

Rules:
- If the message mentions sheep or goats directly, choose IN_DOMAIN.
- If clearly about sheep/goat farming, choose IN_DOMAIN.
- Otherwise choose OUT_OF_DOMAIN.
- When uncertain, choose OUT_OF_DOMAIN.
- Output exactly one label only.

User message:
{user_text}

Label:
"""
    response = llm.invoke(prompt)
    decision = remove_think_tags(response.content).strip().upper()
    final_decision = "IN_DOMAIN" if decision == "IN_DOMAIN" else "OUT_OF_DOMAIN"
    return {"domain_status": final_decision}


# Out-of-domain instruction
def out_of_domain_instruction(state: AgentState):
    instruction = SystemMessage(content="""
The latest user question is OUT OF DOMAIN.

Instructions for the next answer:
1. Provide a short, clear, and helpful answer to the user's question.
2. If the question requires factual or current information, use tools first.
3. Keep the main answer concise and easy to read.

At the END of the response, add a friendly and polite note that:
- explains that this chatbot is designed mainly for sheep, goats, and livestock farming topics
- sounds warm, welcoming, and helpful
- gently invites the user to ask about sheep or goats

After the note, suggest 2 to 3 short example questions related to sheep or goat farming.

Tone:
- warm
- polite
- customer-friendly
- inviting
- never dismissive
- clear and gentle

Always write in the same language as the user.
Never mix languages.
Vary the example questions.
""")
    return {"messages": [instruction]}


# Routers
def route_after_domain_guard(
    state: AgentState,
) -> Literal["reasoner", "out_of_domain_instruction"]:
    if state.get("domain_status") == "IN_DOMAIN":
        return "reasoner"
    return "out_of_domain_instruction"


def route_after_reasoner(state: AgentState) -> Literal["tools", "translator"]:
    last_message = state["messages"][-1]
    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        return "tools"
    return "translator"


# Reasoner
def reasoner(state: AgentState):
    context_instruction = SystemMessage(content="""
You are answering within an ongoing conversation.

Rules:
- Interpret the latest question using the full conversation context.
- Resolve follow-up references like "it", "that", "they", or "per day".
- If factual, scientific, medical, or current information is needed, use tools.
- Prefer search_and_extract for evidence-based answers.
- Use fetch_page only when you need a specific page in more detail.
- Be concise but useful.
- Do not invent facts.
""")

    response = llm_with_tools.invoke([sys_msg, context_instruction] + state["messages"])
    return {"messages": [response]}


# Translator / Localizer
def translator(state: AgentState):
    user_text = get_latest_user_message(state["messages"])
    last_answer = remove_think_tags(state["messages"][-1].content)

    translation_prompt = f"""
You are a final answer localization assistant.

Task:
- Rewrite the assistant's draft answer in the exact same language as the user's message.
- Preserve meaning exactly.
- Do not add new facts.
- Do not add new sources.
- Preserve URLs exactly as they are.
- Make the final answer natural, clear, and fluent.

<user_message>
{user_text}
</user_message>

<draft_answer>
{last_answer}
</draft_answer>

Final answer:
"""
    response = llm.invoke(translation_prompt)
    cleaned = remove_think_tags(response.content)
    return {"messages": [AIMessage(content=cleaned)]}


# Build Graph
builder = StateGraph(AgentState)

builder.add_node("domain_guard", domain_guard)
builder.add_node("out_of_domain_instruction", out_of_domain_instruction)
builder.add_node("reasoner", reasoner)
builder.add_node("tools", ToolNode(tools))
builder.add_node("translator", translator)

builder.add_edge(START, "domain_guard")
builder.add_conditional_edges("domain_guard", route_after_domain_guard)
builder.add_edge("out_of_domain_instruction", "reasoner")
builder.add_conditional_edges("reasoner", route_after_reasoner)
builder.add_edge("tools", "reasoner")
builder.add_edge("translator", END)

memory = MemorySaver()
graph = builder.compile(checkpointer=memory)

# Public Function
def run_agent(query: str, thread_id: str = "default") -> str:
    config = {"configurable": {"thread_id": thread_id}}
    
    # Store preview if this is a new thread
    if thread_id not in tracked_threads:
        preview = query[:40] + "..." if len(query) > 40 else query
        tracked_threads[thread_id] = {
            "id": thread_id,
            "preview": preview
        }
    
    out = graph.invoke({"messages": [HumanMessage(content=query)]}, config=config)
    return out["messages"][-1].content


def get_all_threads() -> list:
    """Return a list of tracked threads."""
    return list(tracked_threads.values())


def get_thread_history(thread_id: str) -> list:
    """Extract full conversation history for a given thread_id."""
    config = {"configurable": {"thread_id": thread_id}}
    state = graph.get_state(config)
    
    if not state or "messages" not in state.values:
        return []
    
    history_out = []
    for msg in state.values["messages"]:
        if isinstance(msg, HumanMessage):
             history_out.append({"role": "user", "content": msg.content})
        elif isinstance(msg, AIMessage):
             history_out.append({"role": "assistant", "content": msg.content})
             
    return history_out
