from utils import normalize_text
from utils import web_search_context
from utils import get_last_user_text

import json
import re

def get_db_connection():
    return psycopg2.connect(os.environ["DATABASE_URL"])

def create_products_table():
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id SERIAL PRIMARY KEY,
            title TEXT,
            description TEXT,
            category TEXT,
            brand TEXT,
            price NUMERIC,
            url TEXT,
            normalized TEXT
        );
    """)

    conn.commit()
    cur.close()
    conn.close()

    print("Products table ready.")   

def resolve_final_category(search_text, categories):
    ai_category = ai_resolve_category(search_text, categories)

    normalized = normalize_category(ai_category)

    fallback = map_category(search_text)

    if not normalized or normalized == "other":
        return fallback

    if normalized not in categories:
        return fallback

    return normalized    


def normalize_category(cat):
    if not cat:
        return None

    c = cat.lower().strip()

    mapping = {
        "phone": "smartphones",
        "smartphone": "smartphones",
        "mobile": "smartphones",
        "iphone": "smartphones",

        "book": "books",
        "toy": "toys",
        "game": "toys",

        "fitness": "sports",

        "instrument": "music",

        "bag": "bags",

        "clothing": "fashion",

        "tv": "electronics",
        "audio": "electronics",

        "coffee": "appliances"
    }

    return mapping.get(c, c)

# =====================================================
# COLORS
# =====================================================
def remove_color_tokens(tokens):

    colors = {
        "white","black","silver","gold","blue","red","green","yellow",
        "λευκο","ασπρο","μαυρο","χρυσο","ασημι","μπλε","κοκκινο"
    }

    return [t for t in tokens if t not in colors]

# =====================================================
# AI EXTRACT SEARCH INTENT
# ===================================================== 

def ai_extract_search_intent(conversation, client):

    user_texts = [
        m.get("text", "")
        for m in conversation
        if isinstance(m, dict) and m.get("isUser")
    ]

    full_text = " ".join(user_texts)

    prompt = f"""
You are an advanced AI shopping intent engine.

Your job is to understand what PRODUCT the user wants to BUY.

Conversation:
{full_text}

Extract ONLY the real product attributes.

Keep ONLY:

• product type
• brand
• model
• color
• material
• size
• capacity
• numeric specifications
• key product descriptors

REMOVE:

• explanations
• usage descriptions
• sentences
• questions
• irrelevant words

IMPORTANT:

Return BOTH English and Greek search queries to improve database matching.

Examples:

User: θέλω καναπέ γωνιακό μαύρο υφασμάτινο
search_keywords_en: corner sofa black fabric
search_keywords_gr: γωνιακός καναπές μαύρος υφασμάτινος

User: θέλω iphone 16 pro 256gb άσπρο
search_keywords_en: iphone 16 pro 256gb white
search_keywords_gr: iphone 16 pro 256gb άσπρο

User: φακό για canon r100 για φεγγάρι
search_keywords_en: canon r100 lens
search_keywords_gr: φακός canon r100

User: ποδήλατο για ανηφόρες
search_keywords_en: mountain bike
search_keywords_gr: ποδήλατο βουνού

Also detect budget if mentioned.

Return ONLY JSON.

JSON FORMAT:

{{
"intent_type":"product_search | product_question | knowledge_question",
"category":"main product category",
"brand":"brand if detected",
"model":"model if detected",
"attributes":[list of attributes],
"search_keywords_en":"clean product search query in English",
"search_keywords_gr":"clean product search query in Greek",
"budget_max":number or null
}}
"""

    try:

        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"system","content":prompt}],
            temperature=0
        )

        result = completion.choices[0].message.content.strip()

        try:
            data = json.loads(result)
        except:
            return {
                "intent_type":"product_search",
                "category":None,
                "brand":None,
                "model":None,
                "attributes":[],
                "search_keywords_en":"",
                "search_keywords_gr":"",
                "budget_max":None
            }

        # safety fallback
        if "search_keywords_en" not in data:
            data["search_keywords_en"] = ""

        if "search_keywords_gr" not in data:
            data["search_keywords_gr"] = ""

        return data

    except Exception as e:

        print("AI INTENT ERROR:", e, flush=True)

        return {
            "intent_type":"product_search",
            "category":None,
            "brand":None,
            "model":None,
            "attributes":[],
            "search_keywords_en":"",
            "search_keywords_gr":"",
            "budget_max":None
        }

# =====================================================
# AI INTENT ENGINE
# =====================================================

def ai_extract_intent(conversation, client):

    prompt = f"""
Διάβασε τη συνομιλία και βρες τι προϊόν ψάχνει ο χρήστης.

Συνομιλία:
{full_conversation(conversation)}

Επέστρεψε ΜΟΝΟ JSON:

{{
 "product_type": "...",
 "keywords": "...",
 "budget_max": number or null
}}
"""

    try:
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": prompt}],
            temperature=0
        )

        result = completion.choices[0].message.content.strip()

        return json.loads(result)

    except:
        return {
            "product_type": None,
            "keywords": None,
            "budget_max": None
        }   
# =====================================================
# BUILD DECISION
# =====================================================    


def build_decision_profile(conversation):

    # =========================================
    # 1️⃣ Συλλογή ΟΛΗΣ της συζήτησης (μόνο user)
    # =========================================

    if isinstance(conversation, list):

        user_texts = [
            m.get("text", "")
            for m in conversation
            if isinstance(m, dict) and m.get("isUser")
        ]

        full_text = " ".join(user_texts)

    elif isinstance(conversation, str):
        full_text = conversation

    else:
        full_text = str(conversation)

    full_text = normalize_text(full_text)

    # =========================================
    # 2️⃣ Profile base
    # =========================================

    profile = {
        "mode": "shopping",
        "budget_min": None,
        "budget_max": None,
        "query_text": full_text,
        "brands": [],
        "descriptive_tokens": [],
        "numeric_tokens": []
    }

    # =========================================
    # 3️⃣ Budget extraction (κρατάμε το μεγαλύτερο)
    # =========================================

    budgets = re.findall(r"(?:μεχρι|εως)?\s*(\d+)\s*(?:ευρω|euro|€)", full_text)
    if budgets:
        profile["budget_max"] = max(int(b) for b in budgets)

    # =========================================
    # 4️⃣ Numeric tokens (π.χ 128gb, 4k κλπ)
    # =========================================

    profile["numeric_tokens"] = re.findall(r"\d+", full_text)

    # =========================================
    # 5️⃣ Token extraction
    # =========================================

    stopwords = {
        "θελω","ψαχνω","να","και","μεχρι","ευρω","το","την","ενα","μια",
        "μου","για","που","σε","θα","με","στο","στη","εως","τι","ειναι",
        "κατι","καποιο","καποια","εχει","να","απο"
    }

    tokens = re.findall(r"\b[a-zA-Zα-ωΑ-Ω0-9]+\b", full_text)

    clean_tokens = [
        t for t in tokens
        if t not in stopwords 
    ]

    profile["descriptive_tokens"] = list(set(clean_tokens))

    # =========================================
    # 6️⃣ Brand detection (χωρίς stopwords)
    # =========================================

    # Τα brands ΔΕΝ είναι όλες οι λέξεις.
    # Είναι λέξεις που δεν είναι stopwords και δεν είναι περιγραφικές κοινές.

    possible_brands = [
        t for t in clean_tokens
        if t not in stopwords
    ]

    profile["brands"] = list(set(possible_brands))

    return profile
# =====================================================
# AI SINGLE QUESTION
# =====================================================
def ai_advisor_response(conversation):

    conversation_text = full_conversation(conversation)

    web_info = web_search_context(conversation_text)

    prompt = f"""
Είσαι προσωπικός σύμβουλος αγορών.

Ο ρόλος σου είναι να βοηθάς τον χρήστη να βρει το ιδανικό προϊόν.

Σκέψου πρώτα τι πραγματικά θέλει να αγοράσει ο χρήστης
και ποια πληροφορία λείπει.

Think step-by-step about the user's real buying intent.

Συνομιλία:
{conversation_text}

Πληροφορίες από internet:
{web_info}

Κανόνες:

- Μην λες καλησπέρα ή καλωσόρισμα
- Κράτα την απάντηση σύντομη (2-3 προτάσεις)
- Μίλα φυσικά σαν άνθρωπος
- Κάνε ΜΙΑ έξυπνη ερώτηση αν λείπει πληροφορία
- Μην κάνεις μεγάλες αναλύσεις
- Βοήθησε τον χρήστη να καταλήξει σε επιλογή

Απάντησε σαν advisor.
"""

    completion = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "system", "content": prompt}],
        temperature=0.4
    )

    return {
        "reply": completion.choices[0].message.content.strip(),
        "links": [],
        "showButton": False
    }

def get_db_categories():

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT DISTINCT category80
        FROM products
        WHERE category80 IS NOT NULL
        LIMIT 200
    """)

    rows = cur.fetchall()

    cur.close()
    conn.close()

    categories =  [r[0] for r in rows if r[0]]
    return categories

# =====================================================
# AI RESOLVE CATEGORY
# =====================================================        
def ai_resolve_category(user_query, categories):

    prompt = f"""
User query: "{user_query}"

Available categories:
{", ".join(categories)}

IMPORTANT:
- Return ONLY ONE category from the list above
- Do NOT create new categories
- If unsure, return "other"

Answer:
"""

    completion = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role":"user","content":prompt}],
        temperature=0
    )

    return completion.choices[0].message.content.strip() 

# =====================================================
# DATABASE PRODUCT FETCH
# =====================================================

def fetch_products_from_db(mode, profile, limit=40):

    conn = get_db_connection()
    cur = conn.cursor()

    # ------------------------------------
    # BUILD SEARCH QUERY
    # ------------------------------------

    search_query = (
        (profile.get("search_keywords_en") or "") + " " +
        (profile.get("search_keywords_gr") or "") + " " +
        (profile.get("query_text") or "")
    ).strip()

    if not search_query:
        cur.close()
        conn.close()
        return []

    print("FINAL SEARCH QUERY:", search_query, flush=True)

    # ------------------------------------
    # BASE SQL (FULL TEXT SEARCH)
    # ------------------------------------

    sql = """
    SELECT
        title,
        description,
        brand,
        product_type,
        price,
        url,
        ts_rank(search_vector, to_tsquery('simple', %s)) AS rank
    FROM products
    WHERE
        search_vector @@ to_tsquery('simple', %s)
        AND in_stock = true
    """
    tokens = search_query.split()
    search_query = " | ".join(tokens)
    params = [search_query, search_query]

    # ------------------------------------
    # CATEGORY (GET ONLY - NO FILTER)
    # ------------------------------------

    category = profile.get("category")

    # ------------------------------------
    # BUDGET FILTER
    # ------------------------------------

    budget = profile.get("budget_max")

    if budget:
        sql += " AND price <= %s"
        params.append(budget)

    # ------------------------------------
    # ORDER BY (BOOST CATEGORY + RELEVANCE + PRICE)
    # ------------------------------------

    sql += """
    ORDER BY
        (category80 = %s) DESC,
        rank DESC,
        price ASC
    LIMIT %s
    """

    # IMPORTANT: params σειρά = ίδια με τα %s
    params.append(category if category else "")
    params.append(limit)

    # ------------------------------------
    # EXECUTE
    # ------------------------------------

    cur.execute(sql, params)
    rows = cur.fetchall()

    cur.close()
    conn.close()

    products = []

    for r in rows:
        products.append({
            "title": r[0],
            "description": r[1],
            "brand": r[2],
            "category": r[3],
            "price": float(r[4]) if r[4] else 0,
            "url": r[5]
        })

    print("DB RESULTS:", len(products), flush=True)

    return products

# =====================================================
# DETERMINISTIC SCORING ENGINE (DB VERSION)
# =====================================================


def score_products(products, profile):

    scored = []

    for p in products:

        score = 0

        title = (p["title"] or "").lower()
        desc = (p["description"] or "").lower()
        brand = (p["brand"] or "").lower()

        # ---------------------------------
        # Brand match
        # ---------------------------------

        for b in profile.get("brands", []):
            if b.lower() in brand:
                score += 8

        # ---------------------------------
        # Title tokens
        # ---------------------------------

        for token in profile.get("descriptive_tokens", []):
            if token in title:
                score += 6

        # ---------------------------------
        # Description tokens
        # ---------------------------------

        for token in profile.get("descriptive_tokens", []):
            if token in desc:
                score += 3

        # ---------------------------------
        # Numeric features
        # ---------------------------------


        numbers = extract_numbers(title + " " + desc)

        for num in profile.get("numeric_tokens", []):
            if num in numbers:
                score += 6
        # ---------------------------------
        # Budget proximity
        # ---------------------------------

        if profile.get("budget_max") and p.get("price"):

            diff = profile["budget_max"] - float(p["price"])

            if diff >= 0:
                score += 4 - (diff / profile["budget_max"])

        scored.append({
            **p,
            "decision_score": round(score, 2)
        })

    scored.sort(key=lambda x: x["decision_score"], reverse=True)

    return scored    


def generate_recommendations(mode, conversation, user_id, client):

    print("ENTERED AI INTENT ENGINE", flush=True)

    intent = ai_extract_search_intent(conversation, client)
    intent_type = intent.get("intent_type", "product_search")
    keywords_en = intent.get("search_keywords_en", "")
    keywords_gr = intent.get("search_keywords_gr", "")

    use_fallback = False

    if not keywords_en and not keywords_gr:
        print("FALLBACK TO BUILD DECISION PROFILE", flush=True)
        profile = build_decision_profile(conversation)
        use_fallback = True

    # =========================
    # SHOPPING SEARCH ENGINE
    # =========================
    
    print("AI INTENT:", intent, flush=True)

    last_user = get_last_user_text(conversation)

    decision_prompt = f"""
User message:
{last_user}

Does this question require internet search to answer correctly?

Answer only:
YES
or
NO
"""

    decision = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": decision_prompt}],
        temperature=0
    )

    needs_web = "YES" in decision.choices[0].message.content.upper()

    web_info = ""

    if needs_web:
        web_info = web_search_context(full_conversation(conversation))

    # -----------------------------------------
    # KNOWLEDGE QUESTION
    # -----------------------------------------

    if intent_type == "knowledge_question":

        web_info = web_search_context(full_conversation(conversation))

        prompt = f"""
Ο χρήστης έκανε ερώτηση γνώσης.

Συνομιλία:
{full_conversation(conversation)}

Web πληροφορίες:
{web_info}

Απάντησε σαν expert σύμβουλος τεχνολογίας.
Αν υπάρχει νέο μοντέλο προϊόντος πες ποιο είναι το τελευταίο.
"""

        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"system","content":prompt}],
            temperature=0.3
        )

        return {
            "reply": completion.choices[0].message.content.strip(),
            "links": [],
            "showButton": False
        }

    # -----------------------------------------
    # BUILD SEARCH QUERY
    # -----------------------------------------

    keywords_en = intent.get("search_keywords_en", "")
    keywords_gr = intent.get("search_keywords_gr", "")
    category = intent.get("category", "")

    search_parts = []

    if keywords_en:
        search_parts.append(keywords_en)

    if keywords_gr:
        search_parts.append(keywords_gr)

    if category:
        search_parts.append(category)

    search_text = " ".join(search_parts).strip()

    print("AI FINAL SEARCH QUERY:", search_text, flush=True)

    # -----------------------------------------
    # AI CATEGORY RESOLUTION
    # -----------------------------------------

    categories = get_db_categories()

    resolved_category = resolve_final_category(
        search_text,
        categories
    )

    print("RESOLVED CATEGORY:", resolved_category, flush=True)

    if not use_fallback:
        profile = {
            "query_text": search_text,
            "descriptive_tokens": search_text.split(),
            "numeric_tokens": re.findall(r"\d+", search_text),
            "budget_max": intent.get("budget_max"),
            "category": resolved_category
        }

    candidates = fetch_products_from_db(mode, profile, limit=40)

    print("DB CANDIDATES:", len(candidates), flush=True)

    exact_match = False

    for p in candidates:
        if search_text.lower() in p["title"].lower():
            exact_match = True
            break

    similar_found = False

    if candidates and not exact_match:
        similar_found = True

    # -----------------------------------------
    # RELAXED SEARCH
    # -----------------------------------------

    if not candidates:

        print("TRYING SIMILAR PRODUCTS SEARCH", flush=True)

        tokens = search_text.split()

        relaxed_tokens = remove_color_tokens(tokens)

        if not relaxed_tokens:
            relaxed_tokens = tokens

        relaxed_query = " ".join(relaxed_tokens)

        relaxed_profile = {
            "query_text": relaxed_query,
            "descriptive_tokens": relaxed_query.split(),
            "numeric_tokens": [],
            "budget_max": intent.get("budget_max"),
            "category": intent.get("category")
        }

        candidates = fetch_products_from_db(mode, relaxed_profile, limit=40)

        print("RELAXED RESULTS:", len(candidates), flush=True)

    # -----------------------------------------
    # FALLBACK WEB SEARCH
    # -----------------------------------------

    if candidates:

        similar_found = True
    else:
        similar_found = False

    if not candidates:

        print("NO DB RESULTS — USING WEB SEARCH", flush=True)

        web_info = web_search_context(search_text)

        prompt = f"""
Ο χρήστης θέλει να αγοράσει προϊόν.

Search query:
{search_text}

Web πληροφορίες:
{web_info}

Δώσε σύντομη συμβουλή αγοράς.
"""

        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": prompt}],
            temperature=0.4
        )

        reply = completion.choices[0].message.content.strip()

        return {
            "reply": reply,
            "links": [],
            "showButton": True
        }

    # -----------------------------------------
    # SCORING
    # -----------------------------------------

    scored = score_products(candidates, profile)

    if not scored:
        return {
            "reply": "Χρειάζομαι λίγο πιο συγκεκριμένες πληροφορίες.",
            "links": [],
            "showButton": False
        }

    top_results = scored[:5]

    products_context = "\n".join(
        [f"{i+1}. {p['title']} – {p['price']}€" for i,p in enumerate(top_results)]
    )

    message_note = ""

    if similar_found:
        message_note = "Δεν βρήκα ακριβώς το ίδιο προϊόν, αλλά αυτά είναι τα πιο κοντινά διαθέσιμα."

    explanation_prompt = f"""
    Είσαι προσωπικός σύμβουλος αγορών.

    {message_note}

    Ο χρήστης ψάχνει:
    {profile["query_text"]}

    Βρέθηκαν αυτά τα προϊόντα:

    {products_context}

    Κανόνες:

    - Κράτα την απάντηση σύντομη (2 προτάσεις)
    - Μίλα φυσικά σαν άνθρωπος
    - Πες ποιο ξεχωρίζει
    """


    completion = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "system", "content": explanation_prompt}],
        temperature=0.4
    )

    advisor_reply = completion.choices[0].message.content.strip()

    links = [
        {
            "title": f"{item['title']} – {item['price']}€",
            "url": item["url"]
        }
        for item in top_results
    ]

    print("RETURNING PRODUCTS:", len(links), flush=True)

    return {
        "reply": advisor_reply + "\n\nΠαρακάτω υπάρχουν και άλλες επιλογές 👇",
        "links": links,
        "showButton": True
    }