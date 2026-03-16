import os
import re
from typing import Annotated, Literal

from typing_extensions import TypedDict

from langgraph.graph import START, END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import InMemorySaver

from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_google_genai import ChatGoogleGenerativeAI



# Global dictionary to track threads and previews
tracked_threads = {}

# Config / LLM
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
if not GOOGLE_API_KEY:
    raise ValueError("Missing GOOGLE_API_KEY or GEMINI_API_KEY environment variable.")

llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    api_key=GOOGLE_API_KEY,
    temperature=0.0,
    max_retries=2,
)

llm_with_google_search = llm.bind_tools([{"google_search": {}}])

checkpointer = InMemorySaver()


# Helpers
def remove_think_tags(text: str) -> str:
    if not isinstance(text, str):
        return str(text)
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def get_latest_user_message(messages: list) -> str:
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            if isinstance(msg.content, str):
                return msg.content
            return str(msg.content)
    return ""


def get_recent_human_messages(messages: list, limit: int = 6) -> list[str]:
    collected = []
    for msg in messages:
        if isinstance(msg, HumanMessage):
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            if content.strip():
                collected.append(content.strip())
    return collected[-limit:]


def normalize_text(text: str) -> str:
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
    if not isinstance(text, str) or not text.strip():
        return "EN"

    arabic_chars = re.findall(r"[\u0600-\u06FF]", text)
    english_chars = re.findall(r"[A-Za-z]", text)
    return "AR" if len(arabic_chars) > len(english_chars) else "EN"


def detect_arabic_style_hint(text: str) -> str:
    if not isinstance(text, str) or not text.strip():
        return "EN"

    arabic_chars = re.findall(r"[\u0600-\u06FF]", text)
    english_chars = re.findall(r"[A-Za-z]", text)

    if len(english_chars) > len(arabic_chars):
        return "EN"

    normalized = normalize_text(text)

    colloquial_markers = [
        "بدك", "بدكش", "شو", "ليش", "هيك", "رح", "راح", "هلق", "لسه",
        "مشان", "عشان", "بدنا", "خليني", "يعني", "تمام", "اوك", "هاذ", "هاد"
    ]
    formal_markers = [
        "يمكن", "ينبغي", "يفضل", "من المهم", "على سبيل المثال",
        "بشكل عام", "عادة", "لذلك", "اليك", "بالطبع"
    ]

    colloquial_score = sum(1 for w in colloquial_markers if w in normalized)
    formal_score = sum(1 for w in formal_markers if w in normalized)

    if colloquial_score >= 2:
        return "COLLOQUIAL_AR"
    if formal_score >= 2:
        return "FORMAL_AR"
    return "SIMPLE_AR"


def extract_text_from_ai_message(ai_message: AIMessage) -> str:
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


def is_term_meaning_question(text: str) -> bool:
    if not isinstance(text, str):
        return False

    normalized = normalize_text(text)

    patterns = [
        r"^ما معنى\s+.+\??$",
        r"^ما معني\s+.+\??$",
        r"^شو يعني\s+.+\??$",
        r"^وش يعني\s+.+\??$",
        r"^يعني ايش\s+.+\??$",
        r"^what does .+ mean\??$",
        r"^meaning of .+$",
    ]

    return any(re.match(p, normalized) for p in patterns)


def is_general_but_mappable_to_livestock(text: str) -> bool:
    if not isinstance(text, str):
        return False

    normalized = normalize_text(text)

    patterns = [
        r"^ما معنى\s+.+\??$",
        r"^ما معني\s+.+\??$",
        r"^ما هو\s+.+\??$",
        r"^ما هي\s+.+\??$",
        r"^ما المقصود ب\s*.+\??$",
        r"^ما المقصود\s+.+\??$",
        r"^شو يعني\s+.+\??$",
        r"^وش يعني\s+.+\??$",
        r"^يعني ايش\s+.+\??$",
        r"^ما تعريف\s+.+\??$",
        r"^عرف\s+.+\??$",
        r"^what does .+ mean\??$",
        r"^what is .+\??$",
        r"^meaning of .+$",
        r"^define .+$",
    ]

    return any(re.match(p, normalized) for p in patterns)


def is_context_dependent_question(text: str) -> bool:
    if not isinstance(text, str):
        return False

    normalized = normalize_text(text)

    short_patterns = [
        r"^ما معناه\??$",
        r"^ما معناها\??$",
        r"^ما معنى هذا\??$",
        r"^ما معنى ذلك\??$",
        r"^وماذا يعني\??$",
        r"^وش يعني\??$",
        r"^شو يعني\??$",
        r"^وما الافضل\??$",
        r"^ما الافضل\??$",
        r"^وهل هذا طبيعي\??$",
        r"^هل هذا طبيعي\??$",
        r"^وكم مدتها\??$",
        r"^كم مدتها\??$",
        r"^ومتى يبدا\??$",
        r"^متى يبدا\??$",
        r"^ومتى تبدا\??$",
        r"^متى تبدا\??$",
        r"^وما سببه\??$",
        r"^ما سببه\??$",
        r"^وما سببها\??$",
        r"^ما سببها\??$",
        r"^وماذا بعد\??$",
        r"^ماذا بعد\??$",
        r"^وما الحل\??$",
        r"^ما الحل\??$",
        r"^what does that mean\??$",
        r"^what about that\??$",
        r"^is that normal\??$",
        r"^what is better\??$",
        r"^how long does it last\??$",
        r"^when does it start\??$",
    ]

    if any(re.match(p, normalized) for p in short_patterns):
        return True

    reference_markers = [
        "هذا", "هذه", "ذلك", "هاي", "هاد", "هاذ", "هيك",
        "it", "that", "this", "they", "those",
    ]

    word_count = len(normalized.split())
    if word_count <= 4 and any(m in normalized for m in reference_markers):
        return True

    return False


def infer_livestock_context_from_history(messages: list) -> str:
    recent = get_recent_human_messages(messages, limit=6)
    if not recent:
        return ""

    joined = " || ".join(recent)
    text = normalize_text(joined)

    topic_map = [
        (["حمل", "عشار", "دافع", "ملقحه", "فارغه", "pregnant", "pregnancy", "gestation"],
         "pregnancy / reproductive status in sheep or goats"),
        (["تلقيح", "تزاوج", "خصوبه", "بلوغ", "fertility", "breeding", "mating"],
         "breeding and reproduction in sheep or goats"),
        (["فطام", "رضاعه", "حليب", "لبا", "weaning", "colostrum", "milk"],
         "milk, suckling, and weaning in lambs or kids"),
        (["تسمين", "علف", "تغذيه", "feed", "feeding", "ration", "growth"],
         "feeding, growth, and fattening in sheep or goats"),
        (["اسنان", "تسنين", "عمر", "dentition", "age"],
         "age estimation and dentition in sheep or goats"),
        (["مرض", "اعراض", "علاج", "طفيليات", "التهاب", "disease", "symptoms", "treatment"],
         "health and disease in sheep or goats"),
        (["ولاده", "تعسر الولاده", "lambing", "kidding"],
         "lambing or kidding"),
        (["انتاج", "انتاجيه", "اداء", "صوف", "حليب", "milk", "wool", "performance"],
         "productivity and production performance"),
        (["نعجه", "نعاج", "خروف", "اغنام", "ماعز", "عنز", "sheep", "goat", "ewe", "ram"],
         "general sheep/goat livestock context"),
    ]

    for keywords, summary in topic_map:
        if any(k in text for k in keywords):
            return summary

    return ""


def livestock_context_priority_hint(text: str) -> str:
    normalized = normalize_text(text)

    concept_map = {
        "دافع": "pregnancy or breeder usage for ewes/goats",
        "عشار": "pregnant ewe/doe breeder terminology",
        "فارغه": "non-pregnant female in breeder usage",
        "ملقحه": "bred or successfully mated female",
        "خصوبه": "fertility in reproduction and breeding performance",
        "انتاج": "milk, meat, wool, growth, or reproductive output in livestock",
        "انتاجيه": "practical animal productivity",
        "فطام": "weaning stage in lambs or kids",
        "تسمين": "fattening for growth and market readiness",
        "نمو": "body growth and development in lambs/kids",
        "دوره": "breeding, reproductive, or production cycle",
        "موسم": "breeding season, lambing season, or production season",
        "تغذيه": "feeding management and ration quality",
        "علف": "feed type, ration, or feeding purpose",
        "ضرع": "udder structure, health, or milk relevance",
        "ولاده": "lambing or kidding context",
        "حليب": "milk production and lactation context",
        "ضعف": "weight loss, poor body condition, disease, feeding deficiency, or production weakness",
        "عزل": "separation for breeding, health, feeding, or management",
        "تلقيح": "mating or breeding context",
        "بلوغ": "puberty and breeding readiness",
        "عمر": "age in production, breeding, dentition, or management terms",
        "مراحل الحياه": "livestock life stages or production stages",
        "حياه": "livestock life stages or production stages",
        "اسنان": "dentition-based age estimation",
        "تسنين": "age estimation by teeth",
        "اداء": "growth, reproduction, milk, meat, or overall production performance",
        "انتاج الحليب": "milk yield and lactation performance",
        "الحاله": "body condition, production status, or reproductive status",
    }

    for key, hint in concept_map.items():
        if key in normalized:
            return hint

    return "general sheep/goat practical interpretation"


def question_likely_needs_search(user_text: str, domain_status: str) -> bool:
    text = normalize_text(user_text)

    if is_general_but_mappable_to_livestock(user_text) and domain_status == "IN_DOMAIN":
        return False

    markers = [
        "كم", "تكلفه", "سعر", "رسوم", "تاشيره", "فيزا", "موعد", "متي", "اين",
        "افضل", "اعراض", "علاج", "مرض", "تحصين", "لقاح", "جرعه", "نسبه", "وزن",
        "فتره الحمل", "التسنين", "الفطام", "الخصوبه", "حراره", "نسبه نفوق",
        "how much", "cost", "price", "fee", "visa", "current", "latest", "when",
        "where", "symptoms", "treatment", "disease", "vaccine", "dose", "weight",
        "gestation", "dentition", "weaning", "fertility", "temperature",
    ]

    if domain_status == "OUT_OF_DOMAIN":
        return True

    return any(m in text for m in markers)


def topic_continuation_hint(user_text: str, domain_status: str) -> str:
    text = normalize_text(user_text)

    if is_general_but_mappable_to_livestock(user_text):
        if any(w in text for w in ["دافع", "عشار", "فارغه", "ملقحه", "معنى", "مقصود", "تعريف"]):
            return (
                "At the end, suggest continuing with closely related breeder terms "
                "or distinctions between similar sheep/goat expressions."
            )

        if any(w in text for w in ["خصوبه", "تلقيح", "حمل", "عشار", "بلوغ", "دوره", "موسم"]):
            return (
                "At the end, suggest continuing with breeding signs, pregnancy stages, "
                "or related reproductive terms."
            )

        if any(w in text for w in ["حليب", "ضرع", "انتاج", "انتاجيه", "اداء"]):
            return (
                "At the end, suggest continuing with factors that affect milk or production performance."
            )

        if any(w in text for w in ["علف", "تغذيه", "تسمين", "نمو"]):
            return (
                "At the end, suggest continuing with feed planning, growth stages, "
                "or common feeding mistakes."
            )

        if any(w in text for w in ["اسنان", "تسنين", "عمر"]):
            return (
                "At the end, suggest continuing with age estimation examples in sheep/goats."
            )

        if any(w in text for w in ["ضعف", "مرض", "اعراض", "علاج"]):
            return (
                "At the end, suggest continuing with possible causes, warning signs, "
                "or when veterinary help is needed."
            )

        if any(w in text for w in ["حياه", "مراحل", "مرحله", "بلوغ", "فطام"]):
            return (
                "At the end, suggest continuing with life stages, production stages, "
                "or age-specific care in sheep/goats."
            )

        return "At the end, suggest one natural continuation that stays in the same livestock topic."

    if domain_status == "IN_DOMAIN":
        return "At the end, suggest one natural next question that stays in the same exact sheep/goat topic."

    return "At the end, keep the closing brief and natural."


# =========================================================
# State Definition
# =========================================================
class AgentState(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    domain_status: Literal["IN_DOMAIN", "OUT_OF_DOMAIN"]


# =========================================================
# Main System Prompt
# =========================================================
sys_msg = SystemMessage(content="""
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

General livestock interpretation rules:
- If the user asks a broad, general, conceptual, or dictionary-like question, do not default immediately to a generic definition.
- First consider whether the question can be interpreted meaningfully in sheep/goat/livestock context.
- If yes, answer with the livestock meaning or livestock interpretation first.
- Then, if useful, briefly mention the general language meaning or general concept meaning second.
- This applies not only to breeder terms, but also to broad concepts such as:
  - production
  - fertility
  - growth
  - feeding
  - weaning
  - season
  - cycle
  - weakness
  - milk
  - age
  - development
  - performance
  - management
- Prefer practical sheep/goat interpretation over abstract wording whenever that is reasonably possible.
- If the livestock meaning varies by breeder usage, region, or production system, say so clearly.

Narrative continuity rules:
- Maintain continuity with the current topic.
- End each answer with one natural suggestion that continues the same topic.
- The continuation should feel like a helpful next step in the same conversation, not a random topic jump.
- For example:
  - terms -> offer related terms or distinctions
  - pregnancy -> offer signs, care, or stages
  - dentition -> offer age-estimation examples
  - feeding -> offer practical rations or common mistakes
  - production -> offer factors affecting productivity
  - life stages -> offer stage-specific care or examples

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
""")


# =========================================================
# Nodes
# =========================================================
def domain_guard(state: AgentState):
    user_text = get_latest_user_message(state["messages"]).strip()
    text = normalize_text(user_text)
    prior_context = infer_livestock_context_from_history(state["messages"])

    direct_in_domain_keywords = [
        "sheep", "goat", "goats", "lamb", "lambs", "ewe", "ewes", "ram", "rams",
        "kid", "kids", "wether", "does", "buck",
        "lambing", "kidding", "wool", "fleece", "udder", "colostrum",
        "weaning", "creep feed", "creep feeding", "flushing", "drenching",
        "ewe lamb", "goat kid", "orphan lamb", "mastitis", "foot rot",
        "small ruminant", "small ruminants",
        "غنم", "اغنام", "ماعز", "معزه", "عنز", "عنزه", "خروف", "خراف",
        "نعجه", "نعاج", "كبش", "كباش", "حمل", "حملان", "جدي", "جديان",
        "ولاده", "فطام", "رضاعه", "تسمين", "رعي", "قطيع", "حظيره", "صوف",
        "لبا", "ضرع", "تلقيح", "تزاوج", "خصوبه", "مواليد",
        "تسنين", "طفيليات", "طفيلي", "التهاب الضرع", "تعسر الولاده",
        "عشار", "دافع", "فارغه", "ملقحه", "انتاج", "انتاجيه", "حليب", "علف",
        "تغذيه", "بلوغ", "اداء", "موسم", "دوره", "عمر", "اسنان"
    ]

    if any(keyword in text for keyword in direct_in_domain_keywords):
        return {"domain_status": "IN_DOMAIN"}

    if is_context_dependent_question(user_text) and prior_context:
        return {"domain_status": "IN_DOMAIN"}

    classifier_prompt = f"""
You are a STRICT domain classifier for a specialized assistant.

Determine whether the user's latest message should be treated as:
IN_DOMAIN
or
OUT_OF_DOMAIN

The assistant specializes in:
- sheep
- goats
- sheep/goat farming
- sheep/goat health
- sheep/goat feeding
- sheep/goat breeding
- sheep/goat production
- sheep/goat management
- sheep/goat terminology
- sheep/goat age estimation and dentition
- breeder expressions used for sheep/goats
- broad questions that can reasonably be interpreted in sheep/goat context

Important context rule:
- If the latest user message is short, referential, or depends on previous context,
  and the recent conversation was about sheep/goats, treat it as IN_DOMAIN.

Recent inferred livestock context:
{prior_context if prior_context else "None"}

Latest user message:
{user_text}

Rules:
- If sheep or goats are explicitly mentioned, choose IN_DOMAIN.
- If sheep/goat context is strongly implied, choose IN_DOMAIN.
- If the message is a short follow-up that refers to a previous sheep/goat topic, choose IN_DOMAIN.
- If the question can reasonably be interpreted in sheep/goat production, breeding, feeding, age, or management context, prefer IN_DOMAIN.
- If it is unrelated general knowledge with no sheep/goat context, choose OUT_OF_DOMAIN.
- If uncertain, choose OUT_OF_DOMAIN.

Output exactly one label only.
"""
    response = llm.invoke(classifier_prompt)
    decision = extract_text_from_ai_message(response).upper()
    return {"domain_status": "IN_DOMAIN" if decision == "IN_DOMAIN" else "OUT_OF_DOMAIN"}


def reasoner(state: AgentState):
    user_text = get_latest_user_message(state["messages"]).strip()
    domain_status = state.get("domain_status", "OUT_OF_DOMAIN")
    use_search = question_likely_needs_search(user_text, domain_status)

    general_livestock_mapping = is_general_but_mappable_to_livestock(user_text)
    context_priority_hint = livestock_context_priority_hint(user_text)
    continuation_hint = topic_continuation_hint(user_text, domain_status)

    prior_context = infer_livestock_context_from_history(state["messages"])
    context_dependent = is_context_dependent_question(user_text)

    if domain_status == "OUT_OF_DOMAIN":
        behavior_block = """
Out-of-domain instructions:
- Answer the user's actual question first.
- Keep the main answer brief, direct, and polite.
- If the question is factual, current, practical, or verifiable, use Google Search grounding.
- After the main answer, add only a short note that you mainly specialize in sheep, goats, and livestock farming.
- Then suggest 1 or 2 short example sheep/goat questions.
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

    interpretation_block = f"""
General-to-livestock interpretation rules:
- If the user's question is broad, general, conceptual, or phrased in a dictionary-like way, do not default immediately to a generic definition.
- First check whether the question can be interpreted usefully in sheep/goat/livestock context.
- For this question, prioritize this livestock interpretation first:
  {context_priority_hint}
- If a livestock interpretation is reasonable, answer with that first.
- Then, if useful, briefly mention the general language or general concept meaning second.
- Use phrasing such as:
  - "إذا كنت تقصدها في سياق الأغنام أو الماعز..."
  - "في تربية الأغنام والماعز يُقصد بها غالبًا..."
  - "أما بالمعنى العام..."
- If the livestock usage varies between breeders, regions, or production systems, say so clearly.
- Prefer practical livestock explanation over abstract dictionary wording.
""" if general_livestock_mapping else ""

    context_memory_block = f"""
Context continuity rules:
- Use the recent conversation to resolve short follow-up questions.
- If the latest message is short, referential, or depends on previous context, interpret it through the previous sheep/goat topic.
- Current short-follow-up status: {"YES" if context_dependent else "NO"}
- Recent inferred livestock context: {prior_context if prior_context else "None"}
- If the user says things like:
  - "ما معناه؟"
  - "وما الأفضل؟"
  - "وهل هذا طبيعي؟"
  - "كم مدتها؟"
  then do not restart from zero; continue the same livestock topic naturally.
"""

    search_block = """
Search grounding rules:
- If this question is factual, current, external, or verifiable, rely on Google Search grounding.
- Do not present uncertain current facts as fixed truths.
- If search-grounded information can vary by country, region, system, or time, mention that briefly.
"""

    continuity_block = f"""
Conversation continuity rules:
- Maintain narrative continuity with the current topic.
- Do not end with a random or generic follow-up.
- {continuation_hint}
"""

    context_instruction = SystemMessage(content=f"""
You are answering within an ongoing conversation.

Current domain status: {domain_status}
Google Search grounding enabled for this call: {"YES" if use_search else "NO"}

Conversation rules:
- Interpret the latest user message using the conversation context.
- Resolve short follow-ups naturally.
- Answer in the same language as the user.
- Keep a consistent, warm, practical tone.

{behavior_block}

{interpretation_block}

{context_memory_block}

{search_block}

{continuity_block}
""")

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

Important preservation rules:
- If the draft distinguishes between:
  1. a sheep/goat or breeder meaning
  2. a general language meaning
  preserve that distinction clearly.
- If the draft interprets a broad question first through livestock context, preserve that order clearly.
- If the draft ends with a topic-related continuation suggestion, preserve it.
- Do not replace a topic-specific continuation with a generic closing.

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


# =========================================================
# Routers
# =========================================================
def route_after_domain_guard(state: AgentState) -> Literal["reasoner"]:
    return "reasoner"


# =========================================================
# Build Graph
# =========================================================
builder = StateGraph(AgentState)

builder.add_node("domain_guard", domain_guard)
builder.add_node("reasoner", reasoner)
builder.add_node("final_voice_localizer", final_voice_localizer)

builder.add_edge(START, "domain_guard")
builder.add_conditional_edges("domain_guard", route_after_domain_guard)
builder.add_edge("reasoner", "final_voice_localizer")
builder.add_edge("final_voice_localizer", END)

graph = builder.compile(checkpointer=checkpointer)


# =========================================================
# Runner
# =========================================================
def run_agent(query: str, thread_id: str = "default") -> str:
    """
    Run the agent on a new query using LangGraph memory.
    Messages from the same thread_id will share short-term memory.
    """
    try:
        if thread_id not in tracked_threads:
            preview = query[:40] + "..." if len(query) > 40 else query
            tracked_threads[thread_id] = {
                "id": thread_id,
                "preview": preview,
            }
        
        out = graph.invoke(
            {
                "messages": [HumanMessage(content=query)],
                "domain_status": "OUT_OF_DOMAIN",
            },
            config={
                "configurable": {
                    "thread_id": thread_id
                }
            },
        )

        messages_out = out.get("messages", [])
        if not messages_out:
            return "Sorry, no response was generated."

        last_message = messages_out[-1]
        content = getattr(last_message, "content", "")

        if isinstance(content, str) and content.strip():
            return content.strip()

        final_text = extract_text_from_ai_message(last_message)
        if final_text:
            return final_text

        return "Sorry, the assistant returned an empty response."

    except Exception as e:
        return f"An internal error occurred while generating the response: {e}"
    



def get_all_threads() -> list:
    """Return tracked conversation threads for UI listing."""
    return list(tracked_threads.values())


def get_thread_history(thread_id: str) -> list:
    """Return full conversation history for a given thread_id."""
    config = {"configurable": {"thread_id": thread_id}}
    state = graph.get_state(config)

    if not state or "messages" not in state.values:
        return []

    history_out = []
    for msg in state.values["messages"]:
        if isinstance(msg, HumanMessage):
            history_out.append({"role": "user", "content": msg.content})
        elif isinstance(msg, AIMessage):
            history_out.append(
                {"role": "assistant", "content": extract_text_from_ai_message(msg)}
            )

    return history_out