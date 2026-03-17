import re
from typing import Annotated, Literal, List

from dotenv import load_dotenv
from typing_extensions import TypedDict

import os
import re
from typing import Annotated, Literal

from typing_extensions import TypedDict

from langgraph.graph import START, END, StateGraph
from langgraph.graph.message import add_messages

from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.checkpoint.memory import MemorySaver


load_dotenv()

# Global dictionary to track threads and previews
tracked_threads = {}


# Base model
llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    api_key=os.getenv("GOOGLE_API_KEY"),
    temperature=0.0,
    max_retries=2,
)

# Same model, but with Gemini built-in Google Search enabled
llm_with_google_search = llm.bind_tools([{"google_search": {}}])


# Helpers
def remove_think_tags(text: str) -> str:
    """Remove any <think>...</think> blocks if present."""
    if not isinstance(text, str):
        return str(text)
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def get_latest_user_message(messages: list) -> str:
    """Extract the latest HumanMessage content."""
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            if isinstance(msg.content, str):
                return msg.content
            return str(msg.content)
    return ""


def normalize_text(text: str) -> str:
    """Light normalization for Arabic/English matching."""
    if not isinstance(text, str):
        return ""

    text = text.lower().strip()
    text = re.sub(r"[أإآا]", "ا", text)
    text = re.sub(r"ى", "ي", text)
    text = re.sub(r"ة", "ه", text)
    text = re.sub(r"ـ", "", text)
    text = re.sub(r"\s+", " ", text)
    return text


def detect_language_hint(text: str) -> str:
    """Very lightweight language hint."""
    if not isinstance(text, str) or not text.strip():
        return "EN"

    arabic_chars = re.findall(r"[\u0600-\u06FF]", text)
    english_chars = re.findall(r"[A-Za-z]", text)
    return "AR" if len(arabic_chars) > len(english_chars) else "EN"


def detect_arabic_style_hint(text: str) -> str:
    """
    Very lightweight Arabic style hint.
    Returns:
    - FORMAL_AR
    - SIMPLE_AR
    - COLLOQUIAL_AR
    - EN
    """
    if not isinstance(text, str) or not text.strip():
        return "EN"

    arabic_chars = re.findall(r"[\u0600-\u06FF]", text)
    english_chars = re.findall(r"[A-Za-z]", text)

    if len(english_chars) > len(arabic_chars):
        return "EN"

    normalized = normalize_text(text)

    colloquial_markers = [
        "بدك",
        "بدكش",
        "شو",
        "ليش",
        "هيك",
        "رح",
        "راح",
        "هلق",
        "لسه",
        "مشان",
        "عشان",
        "بدنا",
        "خليني",
        "يعني",
        "تمام",
        "اوك",
        "هاذ",
        "هاد",
    ]
    formal_markers = [
        "يمكن",
        "ينبغي",
        "يفضل",
        "من المهم",
        "على سبيل المثال",
        "بشكل عام",
        "عادة",
        "لذلك",
        "اليك",
        "بالطبع",
    ]

    colloquial_score = sum(1 for w in colloquial_markers if w in normalized)
    formal_score = sum(1 for w in formal_markers if w in normalized)

    if colloquial_score >= 2:
        return "COLLOQUIAL_AR"
    if formal_score >= 2:
        return "FORMAL_AR"
    return "SIMPLE_AR"


def question_likely_needs_search(user_text: str, domain_status: str) -> bool:
    """
    Heuristic: use Google Search for:
    - out-of-domain factual/current questions
    - in-domain questions requiring precision/current facts
    """
    text = normalize_text(user_text)

    markers = [
        # Arabic
        "كم",
        "تكلفه",
        "سعر",
        "رسوم",
        "تاشيره",
        "فيزا",
        "موعد",
        "متى",
        "اين",
        "افضل",
        "اعراض",
        "علاج",
        "مرض",
        "تحصين",
        "لقاح",
        "جرعه",
        "نسبه",
        "وزن",
        "فتره الحمل",
        "التسنين",
        "الفطام",
        "الخصوبه",
        # English
        "how much",
        "cost",
        "price",
        "fee",
        "visa",
        "current",
        "latest",
        "when",
        "where",
        "symptoms",
        "treatment",
        "disease",
        "vaccine",
        "dose",
        "weight",
        "gestation",
        "dentition",
        "weaning",
        "fertility",
    ]

    if domain_status == "OUT_OF_DOMAIN":
        return True

    return any(m in text for m in markers)


def extract_text_from_ai_message(ai_message: AIMessage) -> str:
    """
    Robustly extract text from Gemini/LangChain AIMessage.
    """
    content = getattr(ai_message, "content", "")

    if isinstance(content, str):
        return remove_think_tags(content).strip()

    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
            else:
                parts.append(str(item))
        return remove_think_tags("\n".join(p for p in parts if p).strip())

    return remove_think_tags(str(content)).strip()


# State Definition
class AgentState(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    domain_status: Literal["IN_DOMAIN", "OUT_OF_DOMAIN"]


# Main System Prompt
sys_msg = SystemMessage(
    content="""
You are a specialized livestock assistant focused ONLY on sheep, goats, and closely related sheep/goat farming topics.

Your role:
- Provide accurate, practical, domain-specific help about sheep and goats.
- Answer like a knowledgeable livestock specialist, not like a generic encyclopedia.
- Prefer practical farming, husbandry, breeding, feeding, and management perspectives.
- Your answers should feel natural, helpful, and familiar to farmers and livestock keepers.

Allowed domain:
1. Sheep and goats
2. Sheep/goat farming and husbandry
3. Breeding, reproduction, lambing, kidding, and fertility
4. Feeding, nutrition, grazing, fattening, and feed management
5. Housing, hygiene, welfare, and environmental conditions
6. Health, disease prevention, parasites, common illness signs, and general veterinary guidance
7. Growth, production, milk, meat, wool, performance, and economics
8. Age estimation, dentition, production stages, and livestock terminology directly related to sheep/goats
9. Farm management decisions directly related to sheep/goat production

Out-of-domain behavior:
- If the question is outside sheep/goat specialization, still answer the user's actual question briefly and politely.
- For factual or current information, use Google Search grounding when helpful.
- Keep the answer short and direct.
- After the answer, add only a short polite note that this assistant mainly specializes in sheep, goats, and livestock farming topics.
- Then suggest 1 or 2 short example sheep/goat questions.

Core answering style:
- Friendly
- Clear
- Practical
- Conversational
- Natural
- Never robotic

Accuracy rules:
- Do not invent facts.
- For current or verifiable information, rely on grounded search results.
- If the information varies or may change, say so briefly.
- For serious animal health issues, advise consulting a veterinarian.

Language rules:
- Always answer in the same language as the user.
- Never mix languages in the final answer.
"""
)


# Nodes
def domain_guard(state: AgentState):
    user_text = get_latest_user_message(state["messages"]).strip()
    text = normalize_text(user_text)

    direct_in_domain_keywords = [
        "sheep",
        "goat",
        "goats",
        "lamb",
        "lambs",
        "ewe",
        "ewes",
        "ram",
        "rams",
        "kid",
        "kids",
        "wether",
        "does",
        "buck",
        "lambing",
        "kidding",
        "wool",
        "fleece",
        "udder",
        "colostrum",
        "weaning",
        "creep feed",
        "creep feeding",
        "flushing",
        "drenching",
        "ewe lamb",
        "goat kid",
        "orphan lamb",
        "mastitis",
        "foot rot",
        "small ruminant",
        "small ruminants",
        "غنم",
        "اغنام",
        "ماعز",
        "معزه",
        "عنز",
        "عنزه",
        "خروف",
        "خراف",
        "نعجه",
        "نعاج",
        "كبش",
        "كباش",
        "حمل",
        "حملان",
        "جدي",
        "جديان",
        "ولاده",
        "فطام",
        "رضاعه",
        "تسمين",
        "رعي",
        "قطيع",
        "حظيره",
        "صوف",
        "لبا",
        "ضرع",
        "تلقيح",
        "تزاوج",
        "خصوبه",
        "مواليد",
        "تسنين",
        "طفيليات",
        "طفيلي",
        "التهاب الضرع",
        "تعسر الولاده",
        "شاة",
        "شياه",
        "معزه",
        "ماشيه",
        "ماشية"
    ]

    if any(keyword in text for keyword in direct_in_domain_keywords):
        return {"domain_status": "IN_DOMAIN"}

    classifier_prompt = f"""
You are a STRICT domain classifier for a specialized assistant.

Determine whether the user's message is about:
- sheep
- goats
- sheep/goat farming
- sheep/goat health
- sheep/goat feeding
- sheep/goat breeding
- sheep/goat production
- sheep/goat farm management
- sheep/goat terminology
- sheep/goat age estimation or dentition

Return exactly one label:

IN_DOMAIN
OUT_OF_DOMAIN

Rules:
- If sheep or goats are explicitly mentioned, choose IN_DOMAIN.
- If sheep/goat context is strongly implied, choose IN_DOMAIN.
- If only generally about farming, animals, agriculture, or veterinary topics without clear sheep/goat context, choose OUT_OF_DOMAIN.
- If uncertain, choose OUT_OF_DOMAIN.
- Output exactly one label only.

<user_message>
{user_text}
</user_message>

Label:
"""
    response = llm.invoke(classifier_prompt)
    decision = extract_text_from_ai_message(response).upper()
    return {
        "domain_status": "IN_DOMAIN" if decision == "IN_DOMAIN" else "OUT_OF_DOMAIN"
    }


def reasoner(state: AgentState):
    user_text = get_latest_user_message(state["messages"]).strip()
    domain_status = state.get("domain_status", "OUT_OF_DOMAIN")
    use_search = question_likely_needs_search(user_text, domain_status)

    if domain_status == "OUT_OF_DOMAIN":
        behavior_block = """
Out-of-domain instructions:
- Answer the user's actual question first.
- Keep the main answer brief, direct, and polite.
- If the question is factual, current, practical, or verifiable, use Google Search grounding.
- After the main answer, add only a short note that you mainly specialize in sheep, goats, and livestock farming.
- Then suggest 1 short example sheep/goat question.
- Do not let the specialization note become longer than the main answer.
"""
    else:
        behavior_block = """
In-domain instructions:
- Answer like a practical sheep/goat advisor.
- Prefer useful livestock interpretation over generic textbook wording.
- For health, feeding, reproduction, age estimation, management, productivity, or other precision-sensitive questions, use Google Search grounding when helpful.
- Keep the answer accurate, practical, and natural.
"""

    search_block = """
Search grounding rules:
- If this question is factual, current, external, or verifiable, rely on Google Search grounding.
- Do not present uncertain current facts as fixed truths.
- If search-grounded information can vary by country, region, system, or time, mention that briefly.
"""

    context_instruction = SystemMessage(
        content=f"""
You are answering within an ongoing conversation.

Current domain status: {domain_status}
Google Search grounding enabled for this call: {"YES" if use_search else "NO"}

Conversation rules:
- Interpret the latest user message using the conversation context.
- Resolve short follow-ups naturally.
- Answer in the same language as the user.
- Keep a consistent, warm, practical tone.

{behavior_block}

{search_block}
"""
    )

    prompt_messages = [sys_msg, context_instruction] + state["messages"]

    active_model = llm_with_google_search if use_search else llm
    response = active_model.invoke(prompt_messages)

    # print("=== REASONER DEBUG ===")
    # print("content:", getattr(response, "content", None))
    # print("content_blocks:", getattr(response, "content_blocks", None))
    # print("additional_kwargs:", getattr(response, "additional_kwargs", None))
    # print("response_metadata:", getattr(response, "response_metadata", None))
    # print("======================")

    return {"messages": [response]}


def final_voice_localizer(state: AgentState):
    user_text = get_latest_user_message(state["messages"])
    last_message = state["messages"][-1]
    draft_answer = extract_text_from_ai_message(last_message).strip()

    if not draft_answer:
        return {"messages": [AIMessage(content="Sorry, no answer was generated.")]}

    user_lang = detect_language_hint(user_text)
    style_hint = detect_arabic_style_hint(user_text)

    if user_lang == "AR":
        if style_hint == "COLLOQUIAL_AR":
            style_instruction = """
- Rewrite the answer in natural Arabic matching the user's friendly colloquial tone.
- Keep it clear and readable, not slang-heavy.
- Make it sound warm, human, and smooth.
- Prefer practical phrasing over stiff formal Arabic.
"""
        elif style_hint == "FORMAL_AR":
            style_instruction = """
- Rewrite the answer in clear, polished Arabic.
- Keep it warm and natural, but more formal than colloquial speech.
- Avoid slang.
"""
        else:
            style_instruction = """
- Rewrite the answer in simple, natural Arabic.
- Keep it friendly, clear, and practical.
- Avoid heavy formal wording and avoid slang-heavy phrasing.
"""
    else:
        style_instruction = """
- Rewrite the answer in natural English matching the user's tone.
- Keep it clear, warm, and natural.
- Avoid unnecessary stiffness.
"""

    localization_prompt = f"""
You are a final answer voice-localizer.

Your job:
- Rewrite the draft answer so it matches the user's language, tone, and style.
- Preserve the meaning exactly.
- Do not add new facts.
- Do not remove important details.
- Do not change numbers.
- Keep it natural and human.

Additional style instructions:
{style_instruction}

<user_message>
{user_text}
</user_message>

<draft_answer>
{draft_answer}
</draft_answer>

Final rewritten answer:
"""
    response = llm.invoke(localization_prompt)
    final_text = extract_text_from_ai_message(response).strip()

    return {"messages": [AIMessage(content=final_text or draft_answer)]}


# Routers
def route_after_domain_guard(
    state: AgentState,
) -> Literal["reasoner"]:
    return "reasoner"


# Build Graph
builder = StateGraph(AgentState)

builder.add_node("domain_guard", domain_guard)
builder.add_node("reasoner", reasoner)
builder.add_node("final_voice_localizer", final_voice_localizer)

builder.add_edge(START, "domain_guard")
builder.add_conditional_edges("domain_guard", route_after_domain_guard)
builder.add_edge("reasoner", "final_voice_localizer")
builder.add_edge("final_voice_localizer", END)


memory = MemorySaver()
graph = builder.compile(checkpointer=memory)


# Public Function
def run_agent(query: str, thread_id: str = "default") -> str:
    config = {"configurable": {"thread_id": thread_id}}

    # Store preview if this is a new thread
    if thread_id not in tracked_threads:
        preview = query[:40] + "..." if len(query) > 40 else query
        tracked_threads[thread_id] = {"id": thread_id, "preview": preview}

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
