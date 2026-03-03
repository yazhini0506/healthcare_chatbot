"""
agent.py – Gemini-First Architecture

Flow for EVERY message:
  1. Gemini classifies the user's intent (what DB query is needed?)
  2. We run the exact DB query (accurate, no hallucination)
  3. Gemini generates a natural answer grounded in the DB results
  4. Intent detection → consent → lead collection → IMMEDIATE email
"""

import os
import re
import uuid
import json
import logging
from datetime import datetime
from dotenv import load_dotenv

import google.generativeai as genai
from database import (
    create_conversation, save_message, get_conversation_history,
    get_knowledge_base, update_intent_tags, save_lead, mark_email_sent,
    get_conversation_messages, get_db_connection, search_products,
    get_products_by_category, get_all_products,
)
from email_service import send_lead_email

load_dotenv()
logger = logging.getLogger(__name__)

# ── Gemini setup ──────────────────────────────────────────────────────────────
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
_model = None
if GEMINI_API_KEY and GEMINI_API_KEY not in ("your_gemini_api_key_here", ""):
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        _model = genai.GenerativeModel(
            "gemini-1.5-flash",
            generation_config={"temperature": 0.2, "max_output_tokens": 1200},
        )
        logger.info("Gemini loaded.")
    except Exception as e:
        logger.warning(f"Gemini init failed: {e}")
else:
    logger.warning("No GEMINI_API_KEY – using rule-based fallback.")

# ── Intent signals ────────────────────────────────────────────────────────────
INTENT_SIGNALS = {
    "Bulk Purchase":            ["bulk", "wholesale", "large order", "volume discount",
                                 "1000 units", "quantity", "buy", "buying", "purchase",
                                 "purchasing", "want to buy", "interested in buying",
                                 "place order", "order products", "procurement",
                                 "i want to order", "place a order", "place an order"],
    "Dealership Interest":      ["dealer", "dealership", "become a dealer", "reseller",
                                 "authorised dealer", "become dealer", "want to become",
                                 "become a reseller"],
    "Distribution Partnership": ["distributor", "distribution", "exclusive territory",
                                 "regional distributor", "supply chain", "logistics"],
    "Product Enquiry":          ["product", "catalogue", "catalog", "what do you sell",
                                 "what products", "interested in", "looking for",
                                 "i am interested", "interested to"],
    "Pricing Request":          ["price", "pricing", "cost", "quote", "quotation",
                                 "how much", "rate", "charges", "tariff"],
    "Partnership Request":      ["partner", "partnership", "collaboration", "white label",
                                 "OEM", "tie up", "tie-up", "agreement", "contract"],
}
# ONLY these high-intent business categories trigger lead collection.
# 'Product Enquiry' and 'Pricing Request' are excluded so browsing remains friction-free.
SALES_INTENTS = {
    "Bulk Purchase", "Dealership Interest", "Distribution Partnership", "Partnership Request"
}

# ── Lead fields ───────────────────────────────────────────────────────────────
LEAD_FIELDS = [
    {"key": "company_name",    "question": "To connect you with our sales team — what is your **company name**?"},
    {"key": "contact_name",    "question": "What is your **full name and designation**? (e.g. Priya Sharma, Purchase Manager)",
     "combined_keys": ["contact_name", "designation"]},
    {"key": "territory",       "question": "Which **territory / region** are you targeting? (e.g. South India, UAE)"},
    {"key": "product_interest","question": "Which **product categories or specific products** are you interested in?"},
    {"key": "expected_volume", "question": "What is your **expected monthly order volume** or approximate purchase value?"},
    {"key": "email",           "question": "Please share your **business email address**."},
    {"key": "phone",           "question": "And your **phone / WhatsApp number** (with country code)?"},
]

_sessions: dict[str, dict] = {}

def _sess(conv_id: str) -> dict:
    if conv_id not in _sessions:
        _sessions[conv_id] = {
            "intent_tags": [], "collecting_lead": False, "consent_given": False,
            "awaiting_consent": False, "lead_field_index": 0,
            "lead_data": {}, "lead_complete": False,
        }
    return _sessions[conv_id]

# ── DB helpers ────────────────────────────────────────────────────────────────

def _get_all_categories() -> list[tuple]:
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT category, COUNT(*) as cnt FROM products GROUP BY category ORDER BY cnt DESC"
    ).fetchall()
    conn.close()
    return [(r["category"], r["cnt"]) for r in rows]

def _list_by_category(category: str, limit: int = 30) -> list[dict]:
    return get_products_by_category(category)[:limit]

def _list_all_products(limit: int = 100) -> list[dict]:
    return get_all_products(limit=limit)

def _search_product(query: str) -> list[dict]:
    return search_products(query, limit=10)

def _get_kb(msg: str) -> str:
    stop = {"the","a","an","is","are","do","you","i","we","can","what","how","tell","me","about","any"}
    words = [w for w in re.findall(r"\b\w{3,}\b", msg.lower()) if w not in stop][:5]
    chunks = get_knowledge_base(query_terms=words or None, limit=3)
    return "\n---\n".join(f"[{c['category']}] {c['content']}" for c in chunks)

# ── Gemini: Classify intent ───────────────────────────────────────────────────

def _classify_with_gemini(user_message: str) -> dict:
    """
    Ask Gemini what type of DB query the user's message requires.
    Returns a dict with 'type' and optional 'category' / 'query'.
    Falls back to rule-based on failure.
    """
    all_cats = [c for c, _ in _get_all_categories()]
    cat_list = "\n".join(f"- {c}" for c in all_cats)

    classifier_prompt = f"""You are a query router for a healthcare products database.

AVAILABLE PRODUCT CATEGORIES:
{cat_list}

USER MESSAGE: "{user_message}"

Based on the user's message, decide which database operation is needed.
Respond ONLY with a valid JSON object — no markdown, no explanation.

Return one of:
{{"type": "list_categories"}}                              -> user wants to see all categories
{{"type": "list_all"}}                                     -> user wants to see all products
{{"type": "list_by_category", "category": "<name>"}}       -> user wants products from a specific category (use EXACT name from list provided above, including ampersands like '&')
{{"type": "product_search", "query": "<product name>"}}    -> user is asking about a specific named product
{{"type": "general"}}                                      -> general question, no specific product/category lookup needed

Examples:
"list medical devices" → {{"type": "list_by_category", "category": "Medical Device"}}
"what medicines do you have" → {{"type": "list_by_category", "category": "Medicine"}}
"do you sell ECG machines" → {{"type": "product_search", "query": "ECG machine"}}
"tell me about paracetamol" → {{"type": "product_search", "query": "paracetamol"}}
"what categories are available" → {{"type": "list_categories"}}
"how do I become a dealer" → {{"type": "general"}}
"I want to buy surgical gloves" → {{"type": "product_search", "query": "surgical gloves"}}
"show all respiratory products" → {{"type": "list_by_category", "category": "Respiratory & Allergy"}}"""

    if _model is None:
        return _rule_based_classify(user_message, all_cats)

    try:
        resp = _model.generate_content(classifier_prompt)
        raw  = resp.text.strip()
        # Strip any markdown code fences
        raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
        result = json.loads(raw)
        logger.info(f"Gemini classified: {result}")
        return result
    except Exception as e:
        logger.warning(f"Classifier failed ({e}), falling back to rules.")
        return _rule_based_classify(user_message, all_cats)


# Function to normalize words for better fuzzy matching (plurals to singular)
def normalize_text(t: str) -> str:
    if not t: return ""
    return (t.lower()
            .replace("equipments","equipment").replace("devices","device")
            .replace("medicines","medicine").replace("diagnostics","diagnostic")
            .replace("surgicals","surgical").replace("vitamins","vitamin")
            .replace("hospitals","hospital").replace("rehabilitations","rehabilitation")
            .replace("tablets", "tablet").replace("capsules", "capsule")
            .replace("machines", "machine").replace("bottles", "bottle")
            .replace("&", "")) # Remove ampersands for cleaner matching

def _rule_based_classify(msg: str, all_cats: list) -> dict:
    """Simple rule-based fallback when Gemini is unavailable."""
    ml = msg.lower()

    # ── Category listing ──────────────────────────────────────────────────────
    if re.search(r"\bcategor", ml) or "types of product" in ml:
        return {"type": "list_categories"}

    # ── All products ──────────────────────────────────────────────────────────
    # ONLY triggers if "all" is specified. Otherwise it should fall through to category/search.
    if re.search(r"\ball\b", ml) and re.search(r"\b(product|list|show)\b", ml):
        return {"type": "list_all"}

    # Strip noise (including common typos like 'liist', 'lisst')
    noise = {"list","liist","lisst","llist","show","give","tell","me","all","the","a","an",
             "products","product","items","available","please","here","now","are","is",
             "of","in","under","from","for","what","do","you","have","u",
             # Additional noise
             "their","them","they","this","that","these","those",
             "can","could","would","should","does","did","will",
             "it","its","too","also","there","where","when","how",
             "any","some","get","got","see","want","need","know",
             "yes","no","not","but","and","or","so","very","just"}
    cleaned = " ".join(w for w in ml.split() if w not in noise)

    cleaned_user_words = set(normalize_text(ml).split())

    # Fuzzy match against real DB category names
    best_cat, best_score = None, 0
    for cat in all_cats:
        cwords = set(normalize_text(cat).split())
        score  = len(cwords & cleaned_user_words)
        if score > best_score:
            best_score, best_cat = score, cat

    if best_score > 0 and re.search(r"\b(list|liist|show|give|display|what|have|sell|u have)\b", ml):
        return {"type": "list_by_category", "category": best_cat}

    # ── Specific product search — capture full name after trigger word ────────
    # Use greedy match (.+) to get the FULL product name, not just first word
    m = re.search(
        r"\b(about|what is|tell me|search|find|do you have|details?|info on|describe)\s+(?:the\s+)?(.+)",
        ml
    )
    if m:
        query = re.sub(r"[?.!]+$", "", m.group(2)).strip()
        if len(query) >= 3:
            return {"type": "product_search", "query": query}

    # ── Business intent guard — these are NEVER product searches ─────────────────
    # Return 'general' before the last-resort product search fires
    business_signals = [
        "dealer", "dealership", "distributor", "distribution", "partner",
        "partnership", "reseller", "resell", "franchise", "become a",
        "place a order", "place an order", "i want to order", "place order",
        "bulk order", "bulk purchase", "bulk buy", "wholesale",
        "how much", "pricing", "quotation", "quote", "price list",
        "collaborate", "collaboration", "oem", "white label",
        "territory", "exclusive", "authorised", "authorized",
        # purchasing/buying intent
        "i am interested", "interested in buying", "want to buy",
        "want to purchase", "looking to buy", "looking to purchase",
        "i need to buy", "wish to buy", "wish to purchase",
        "procurement", "sourcing", "tender",
    ]
    if any(sig in ml for sig in business_signals):
        return {"type": "general"}

    # ── Last resort: try product search with meaningful words ───────────────
    # This catches bare product names like "Zemplar" or "ECG machine"
    meaningful = " ".join(w for w in ml.split() if w not in noise and len(w) > 2)
    # Also singularize the meaningful chunk for better matching
    meaningful = normalize_text(meaningful)
    
    if len(meaningful.split()) >= 1 and len(meaningful) >= 4:
        return {"type": "product_search", "query": meaningful}

    return {"type": "general"}

# ── Gemini: Generate answer from DB data ─────────────────────────────────────

def _format_db_answer(qtype: str, db_result, user_message: str) -> str:
    """Format DB results into a readable response — used when Gemini is unavailable."""
    if qtype == "list_categories":
        cats = db_result or []
        if not cats:
            return "Our product catalogue is currently being updated. Please check back shortly."
        lines = ["Here are all the **product categories** in our catalogue:\n"]
        for i, (cat, cnt) in enumerate(cats, 1):
            lines.append(f"{i}. **{cat}** — {cnt} product{'s' if cnt != 1 else ''}")
        lines.append("\nAsk me to **list products in [category]** or search a **specific product by name**.")
        return "\n".join(lines)

    if qtype in ("list_by_category", "product_search", "list_all"):
        products = db_result or []
        if not products:
            return ("No products found. Try:\n"
                    "• `list the categories` — to see all categories\n"
                    "• `list [category name]` — e.g., `list Hospital Equipment`")
        
        title = "ALL PRDOUCTS" if qtype == "list_all" else f"Found **{len(products)} product(s)**"
        lines = [f"**{title}**:\n"]
        for i, p in enumerate(products, 1):
            line = f"{i}. **{p['product_name']}** ({p.get('category','—')})"
            if p.get("manufacturer"):
                line += f"  \n   *By: {p['manufacturer']}*"
            if p.get("description"):
                desc = p["description"]
                line += f"  \n   {desc[:150]}{'…' if len(desc) > 150 else ''}"
            lines.append(line)
        lines.append("\nWould you like to place a bulk order or enquire about any of these?")
        return "\n".join(lines)

    # ── General / contextual response ─────────────────────────────────────────────
    ml = user_message.lower()

    # Greetings
    if re.search(r"\b(hi|hello|hey|good morning|good afternoon|good evening|namaste)\b", ml):
        return ("Hello! 👋 Welcome to **HealthBot** — your healthcare products assistant.\n\n"
                "I can help you with:\n"
                "• **Exploring products** — medicines, devices, equipment\n"
                "• **Bulk purchase enquiries**\n"
                "• **Dealer / distributor programs**\n"
                "• **Partnership opportunities**\n\n"
                "What can I help you with today?")

    # Buying / purchasing intent
    if any(w in ml for w in ["buy", "buying", "purchase", "purchasing", "interested in",
                              "want to", "looking to", "need to", "wish to", "procure"]):
        return ("🛒 **Great! We’d love to help you purchase.**\n\n"
                "We carry products across these categories:\n"
                "• Diagnostic Equipment • Medical Devices • Medicines\n"
                "• Surgical Equipment • Vitamins & Supplements\n"
                "• Respiratory & Allergy • Hospital Equipment\n\n"
                "Tell me which **category or product name** you’re interested in "
                "and I’ll show you exactly what we have.\n"
                "Or type **list all products** to see everything we offer.")

    # Partnership / dealer / distributor
    if any(w in ml for w in ["dealer", "distributor", "partner", "franchise", "resell"]):
        return ("We offer **three partnership tiers**:\n"
                "1. 🏢 **Authorised Reseller** – 50 units/month minimum\n"
                "2. 🏞️ **Regional Distributor** – Exclusive territory, 500 units/month\n"
                "3. 🏆 **National Distributor** – Dedicated account manager + co-marketing\n\n"
                "Would you like me to connect you with our sales team?")

    # Pricing
    if any(w in ml for w in ["price", "cost", "quote", "quotation", "how much", "rate", "charges"]):
        return ("💰 **Pricing depends on product and order volume.**\n\n"
                "We offer:\n"
                "• Volume discounts from **10% for 1,000+ units**\n"
                "• Custom quotes for tenders and government orders\n"
                "• Flexible payment terms for registered distributors\n\n"
                "Which product are you looking to quote for? I’ll connect you with our sales team.")

    # Order / place order
    if any(w in ml for w in ["order", "ordering", "place"]):
        return ("📦 **Ready to place an order?**\n\n"
                "Please tell me:\n"
                "1. Which **product(s)** you need\n"
                "2. Approximate **quantity**\n"
                "3. Your **delivery location**\n\n"
                "I’ll connect you with a sales representative right away.")

    # Default help — give useful suggestions, not a static menu
    return ("🧠 I understand you’re looking for healthcare products or business opportunities.\n\n"
            "Here’s what I can do for you:\n"
            "• **list the categories** — see all product categories with counts\n"
            "• **list [category]** — e.g. \u2018list medical devices\u2019\n"
            "• **[product name]** — just type a product name to search\n"
            "• **I want to become a dealer** — for partnership info\n"
            "• **bulk order** — for volume pricing\n\n"
            "What would you like to know?")


def _answer_with_gemini(user_message: str, db_text: str, kb_context: str,
                        history: list, qtype: str = "general", db_result=None) -> str:
    """Generate answer. Uses DB-formatted response when Gemini is unavailable."""
    if _model is None:
        return _format_db_answer(qtype, db_result, user_message)

    sys_prompt = f"""You are HealthBot, an expert assistant for a healthcare products manufacturer/distributor.

IMPORTANT RULES:
1. If the user message is gibberish, nonsense, or contains no identifiable words related to healthcare, ordering, or business, politely ask them to rephrase or retype their question.
2. Answer ONLY using the database results and knowledge base below — no hallucination.
3. When listing products, use a numbered list with product name, manufacturer, and description.
4. Be professional, friendly, and concise.
5. After listing products, ask if the user wants to place a bulk order.

DATABASE RESULTS (source of truth):
{db_text}

KNOWLEDGE BASE:
{kb_context}"""

    try:
        chat_history = [
            {"role": "user" if m["role"] == "user" else "model", "parts": [m["content"]]}
            for m in history[-6:]
        ]
        chat_obj = _model.start_chat(history=chat_history)
        return chat_obj.send_message(f"{sys_prompt}\n\nUser: {user_message}").text.strip()
    except Exception as e:
        logger.error(f"Gemini answer error: {e}")
        return _format_db_answer(qtype, db_result, user_message)

# ── Format DB results ─────────────────────────────────────────────────────────

def _db_data_to_text(query_type: str, result: object) -> str:
    if query_type == "list_categories":
        cats = result  # list of (name, count) tuples
        if not cats:
            return "No product categories found in the database."
        return "PRODUCT CATEGORIES:\n" + "\n".join(f"- {c} ({n} products)" for c, n in cats)

    if query_type in ("list_all", "list_by_category", "product_search"):
        products = result  # list of product dicts
        if not products:
            return "No products found matching this query."
        lines = [f"PRODUCTS ({len(products)} found):"]
        for p in products:
            line = f"\n• {p['product_name']} | Category: {p['category']}"
            if p.get("manufacturer"):
                line += f" | By: {p['manufacturer']}"
            if p.get("description"):
                line += f"\n  {p['description'][:200]}"
            lines.append(line)
        return "\n".join(lines)

    return "No specific database results for this query."

# ── Intent detection ──────────────────────────────────────────────────────────

def _detect_intent(text: str) -> list[str]:
    tl = text.lower()
    return [i for i, kws in INTENT_SIGNALS.items() if any(k in tl for k in kws)]

# ── Lead handling ─────────────────────────────────────────────────────────────

def _process_lead_field(session: dict, conv_id: str, user_input: str) -> str:
    idx   = session["lead_field_index"]
    field = LEAD_FIELDS[idx]
    if field.get("combined_keys"):
        parts = re.split(r"[,\-–|/]", user_input, maxsplit=1)
        session["lead_data"]["contact_name"] = parts[0].strip()
        session["lead_data"]["designation"]  = parts[1].strip() if len(parts) > 1 else ""
    else:
        session["lead_data"][field["key"]] = user_input.strip()
    session["lead_field_index"] += 1
    if session["lead_field_index"] >= len(LEAD_FIELDS):
        return _finalize_lead(session, conv_id)
    return LEAD_FIELDS[session["lead_field_index"]]["question"]


def _finalize_lead(session: dict, conv_id: str) -> str:
    lead = dict(session["lead_data"])
    lead["intent_tags"] = ", ".join(session["intent_tags"])
    lead_id  = save_lead(conv_id, lead)
    summary  = "\n".join(
        f"{'User' if m['role']=='user' else 'Bot'}: {m['content'][:200]}"
        for m in get_conversation_messages(conv_id)[-20:]
    )
    ok       = send_lead_email(lead, summary, session["intent_tags"])
    if ok:
        mark_email_sent(lead_id)
    session.update({"lead_complete": True, "collecting_lead": False})
    name = lead.get("contact_name", "there")
    return (
        f"✅ **Thank you, {name}!**\n\n"
        + ("✅ Our sales team has been **notified by email** and will contact you within 24 hours.\n\n"
           if ok else "📋 Your enquiry has been recorded. Our sales team will reach out shortly.\n\n")
        + f"**Summary:**\n"
        f"- Company: {lead.get('company_name','—')}\n"
        f"- Territory: {lead.get('territory','—')}\n"
        f"- Product Interest: {lead.get('product_interest','—')}\n"
        f"- Volume: {lead.get('expected_volume','—')}\n\n"
        f"Is there anything else I can help you with?"
    )

# ── Main chat handler ─────────────────────────────────────────────────────────

def chat(conv_id: str, user_message: str) -> dict:
    session = _sess(conv_id)
    create_conversation(conv_id)
    save_message(conv_id, "user", user_message)

    # ── Lead field collection ──────────────────────────────────────────────
    if session["collecting_lead"]:
        reply = _process_lead_field(session, conv_id, user_message)
        save_message(conv_id, "assistant", reply)
        return _pack(reply, session, conv_id)

    # ── Consent response ───────────────────────────────────────────────────
    if session["awaiting_consent"]:
        ml = user_message.lower().strip()
        # Use word boundaries (\b) to avoid matching 'no' inside 'diagnostic' or 'ya' inside 'Priya'
        if re.search(r"\b(yes|sure|ok|okay|proceed|go ahead|absolutely|agree|yep|ya)\b", ml):
            session.update({"consent_given": True, "collecting_lead": True, "awaiting_consent": False})
            reply = LEAD_FIELDS[0]["question"]
            save_message(conv_id, "assistant", reply)
            return _pack(reply, session, conv_id)
        elif re.search(r"\b(no|nope|not now|nah|later|cancel|skip|don't|dont)\b", ml):
            session["awaiting_consent"] = False
            reply = "No problem! Feel free to keep asking about our products and services. 😊"
            save_message(conv_id, "assistant", reply)
            return _pack(reply, session, conv_id)
        else:
            # Not a yes/no — treat as a new query, exit consent mode
            session["awaiting_consent"] = False
            # Fall through to normal query processing below

    # ── STEP 1: Gemini classifies the question ─────────────────────────────
    intent_class = _classify_with_gemini(user_message)
    qtype = intent_class.get("type", "general")

    # ── STEP 2: Execute DB query based on classification ───────────────────
    db_result = None
    if qtype == "list_categories":
        db_result = _get_all_categories()

    elif qtype == "list_all":
        db_result = _list_all_products(limit=100)
        qtype     = "list_all"

    elif qtype == "list_by_category":
        category  = intent_class.get("category", "")
        db_result = _list_by_category(category)

    elif qtype == "product_search":
        query     = intent_class.get("query", user_message)
        db_result = _search_product(query)

        # If combined query returns nothing, try each meaningful word individually
        if not db_result:
            stop = {"the","a","an","and","or","is","are","in","of",
                    "for","to","with","their","this","that","have"}
            words = [w.strip("()?,.") for w in query.lower().split()
                     if len(w.strip("()?,.")) >= 4 and w.strip("()?,.") not in stop]
            for word in words[:6]:
                hits = _search_product(word)
                if hits:
                    db_result = hits
                    logger.info(f"Product fallback match on word '{word}': {len(hits)} results")
                    break

        # If still nothing found, treat as general question (don't show 'No products found')
        if not db_result:
            # Try one last time with normalized (singular) query
            norm_query = normalize_text(query)
            if norm_query != query:
                db_result = _search_product(norm_query)

        # If still nothing found, keep type as 'product_search' but pass empty list
        # This allows Gemini/Formatting to say "No products found" instead of Help Menu
        if not db_result:
            logger.info(f"Product search for '{query}' returned 0 results.")

    db_text  = _db_data_to_text(qtype, db_result) if db_result is not None else "No specific DB query needed."
    kb_text  = _get_kb(user_message)
    history  = get_conversation_history(conv_id, limit=6)

    # ── STEP 3: Gemini generates a natural answer using DB data ────────────
    reply = _answer_with_gemini(user_message, db_text, kb_text, history,
                                qtype=qtype, db_result=db_result)

    # ── Intent detection ───────────────────────────────────────────────────
    current_intents = _detect_intent(user_message)
    for tag in current_intents:
        if tag not in session["intent_tags"]:
            session["intent_tags"].append(tag)
    update_intent_tags(conv_id, session["intent_tags"])

    # ── Offer lead collection for sales intents ────────────────────────────
    # Only trigger if the CURRENT message indicates a business intent (friction-free browsing)
    if (set(current_intents) & SALES_INTENTS
            and not session["consent_given"]
            and not session["lead_complete"]
            and not session["awaiting_consent"]):
        reply += (
            "\n\n---\n🤝 **Interested in a partnership or bulk order?** "
            "Would you like me to connect you with our sales team? "
            "I just need a few details. **Do you consent?** (Yes / No)"
        )
        session["awaiting_consent"] = True

    save_message(conv_id, "assistant", reply)
    return _pack(reply, session, conv_id)


def _pack(reply: str, session: dict, conv_id: str) -> dict:
    return {
        "reply":           reply,
        "intent_tags":     session["intent_tags"],
        "lead_collecting": session["collecting_lead"],
        "lead_complete":   session["lead_complete"],
        "conv_id":         conv_id,
    }

def new_conversation() -> str:
    return str(uuid.uuid4())
