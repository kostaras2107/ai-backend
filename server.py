from flask import Flask, request, jsonify
from flask_cors import CORS
from openai import OpenAI
import os
import json
import requests
import urllib.parse
import firebase_admin
from firebase_admin import credentials, firestore
import time
import xml.etree.ElementTree as ET
import re
import unicodedata
import psycopg2
from memory_engine import load_user_memory
from psycopg2.extras import execute_batch
USER_PROFILES = {}

def normalize_destination(city):

    if not city:
        return city

    city = unicodedata.normalize('NFD', city)
    city = ''.join(c for c in city if unicodedata.category(c) != 'Mn')

    replacements = {
        "σαρτονη": "santorini",
        "σανρτινη": "santorini",
        "σαντορινη": "santorini",
        "καβαλα": "kavala",
        "θασος": "thasos",
        "χανια": "chania",
        "ροδος": "rhodes",
        "κρητη": "crete"
    }

    if city in replacements:
        return replacements[city]

    city = unicodedata.normalize('NFD', city)
    city = city.encode('ascii', 'ignore').decode("utf-8")

    return city

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



# =====================================================
# CONFIG
# =====================================================

LINKWISE_FEED_SHOPPING = "https://affiliate.linkwi.se/feeds/1.2/CD28160/programs-joined/columns-product_id,model_name,product_name,description,category,brand_name,tracking_url,thumb_url,image_url,in_stock,availability,valid_from,valid_to,on_sale,currency,price,full_price,discount,city,times_bought,longitude,latitude,address,size,colour,custom,extra_images,variations/catinc-0/catex-0/proginc-11532-726,12858-2366,13987-2681,13208-2081,12125-1139,11920-1064,12218-1239,13306-2056,13527-2303,13806-2653,11036-369,12761-1652,14114-2761,11593-815,12560-1466,13990-2713,11834-955,11983-1078,13962-2677,12011-1042,13640-2370,11442-602,138-2273,12174-1176,12315-1323,13779-2538,13535-2262,13941-2644,12802-1676,14123-2770,10784-281,13240-2087,12471-1412,11388-564,11609-771,10553-1827,469-299,13026-1874,13993-2692,13754-2454,12056-1106,11432-621,11307-622,11641-847,12071-1114,12615-1512,12321-1361,11754-880,13604-2421,12569-1461,11537-2451,13775-2623/progex-0/feed.xml"

LINKWISE_FEED_TRAVEL = "https://affiliate.linkwi.se/feeds/1.2/CD28160/programs-joined/columns-product_id,model_name,product_name,description,category,brand_name,tracking_url,thumb_url,image_url,in_stock,availability,valid_from,valid_to,on_sale,currency,price,full_price,discount,city,times_bought,longitude,latitude,address,size,colour,custom,extra_images,variations/catinc-0/catex-0/proginc-177-478,205-67/progex-0/feed.xml"

EXPEDIA_AFFILIATE_BASE = "https://expedia.com/affiliate/QFZxpYq"


# =====================================================
# FLASK INIT
# =====================================================

app = Flask(__name__)
CORS(app)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


# =====================================================
# FIREBASE INIT
# =====================================================

if not firebase_admin._apps:
    firebase_config = json.loads(os.environ["FIREBASE_KEY"])
    cred = credentials.Certificate(firebase_config)
    firebase_admin.initialize_app(cred)

db = firestore.client()


# =====================================================
# TEXT HELPERS
# =====================================================

def normalize_text(text):
    if not text:
        return ""
    text = str(text).lower()
    text = unicodedata.normalize("NFD", text)
    text = text.encode("ascii", "ignore").decode("utf-8")
    return text


def tokenize(text):
    text = normalize_text(text)
    return re.findall(r'\w+', text)


def extract_numbers(text):
    return re.findall(r'\d+', str(text))


# =====================================================
# CONVERSATION HELPERS
# =====================================================

def full_conversation(history):

    texts = []

    for msg in history:
        if msg.get("isUser") and msg.get("text"):
            texts.append(msg.get("text"))

    return " ".join(texts)


def get_last_user_text(history):
    for msg in reversed(history):
        if msg.get("isUser") and msg.get("text"):
            return msg.get("text")
    return ""

# =====================================================
# TRAVEL DESTINATION EXTRACTOR
# =====================================================

def extract_destination(text):

    text = text.lower()

    remove_words = [
        "hotel","hotels","ξενοδοχειο","ξενοδοχεια",
        "διαμονη","stay","accommodation"
    ]

    for w in remove_words:
        text = text.replace(w,"")

    return text.strip()
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



# =====================================================
# BUILD DECISION
# =====================================================    


import re
import unicodedata


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
# =====================================================
# WEB SEARCH
# =====================================================

def web_search_context(query):

    url = "https://google.serper.dev/search"

    payload = {
        "q": query,
        "num": 10
    }

    headers = {
        "X-API-KEY": os.getenv("SERPER_API_KEY"),
        "Content-Type": "application/json"
    }

    try:
        r = requests.post(url, json=payload, headers=headers)
        print("SERPER STATUS:", r.status_code, flush=True)
        print("SERPER RESPONSE:", r.text[:500], flush=True)
        data = r.json()

        snippets = []

        for result in data.get("organic", [])[:10]:
            snippets.append(result.get("snippet", ""))

        return "\n".join(snippets)

    except:
        return ""


# =====================================================
# EXTRACT TRAVEL
# =====================================================
def extract_travel_filters(text):

    text = text.lower()

    meal_plan = None
    amenities = []
    adults = None

    # breakfast
    if "πρωινό" in text or "breakfast" in text:
        meal_plan = "FREE_BREAKFAST"

    # wifi
    if "wifi" in text or "ιντερνετ" in text:
        amenities.append("WIFI")

    # pool
    if "πισίνα" in text or "pool" in text:
        amenities.append("POOL")

    # adults
    import re

    match = re.search(r'(\d+)\s*(persons|people|άτομα)', text.lower())

    if match:
        adults = int(match.group(1))
    else:
        adults = None

    return {
        "meal_plan": meal_plan,
        "amenities": amenities,
        "adults": adults
    }


import urllib.parse
from datetime import datetime


# =====================================================
# BUILD EXPEDIA
# =====================================================
def build_expedia_search_url(
    destination,
    region_id=None,
    latlong=None,
    checkin=None,
    checkout=None,
    adults=2,
    children_ages=None,
    rooms=1,
    meal_plan=None,
    amenities=None,
    budget_total=None
):

    base_url = "https://www.expedia.com/Hotel-Search"

    # -----------------------------
    # Normalize date
    # -----------------------------
    def normalize_date(d):

        if not d:
            return None

        try:
            dt = datetime.fromisoformat(d)
            return dt.strftime("%Y-%m-%d")
        except:
            try:
                dt = datetime.strptime(d, "%d-%m-%Y")
                return dt.strftime("%Y-%m-%d")
            except:
                try:
                    dt = datetime.strptime(d, "%Y-%m-%d")
                    return dt.strftime("%Y-%m-%d")
                except:
                    return None

    checkin = normalize_date(checkin)
    checkout = normalize_date(checkout)

    print("CHECKIN FROM AI:", checkin, flush=True)
    print("CHECKOUT FROM AI:", checkout, flush=True)

    import unicodedata

    destination = unicodedata.normalize('NFD', destination)
    destination = destination.encode('ascii', 'ignore').decode('utf-8')

    params = {
        "destination": destination,
        "startDate": checkin,
        "endDate": checkout,
        "adults": adults,
        "rooms": rooms,
        "sort": "RECOMMENDED",
        "categorySearch": "any_option",
        "useRewards": "false"
    }
    if children_ages:
        children_param = ",".join([f"1_{age}"
    for age in children_ages])
        params["children"] = children_param

    params = {k: v for k, v in params.items() if v}

    query = urllib.parse.urlencode(params)

    # -----------------------------
    # Meal plan
    # -----------------------------
    if meal_plan:
        query += f"&mealPlan={meal_plan}"

    # -----------------------------
    # Amenities
    # -----------------------------
    if amenities:
        for a in amenities:
            query += f"&amenities={a}"

    # -----------------------------
    # Budget
    # -----------------------------
    if budget_total and checkin and checkout:

        try:
            d1 = datetime.strptime(checkin, "%Y-%m-%d")
            d2 = datetime.strptime(checkout, "%Y-%m-%d")

            nights = (d2 - d1).days

            if nights <= 0:
                nights = 1

            total_budget = int(budget_total) * nights

            min_price = int(total_budget * 0.7)
            max_price = int(total_budget * 1.3)

            query += f"&price={min_price}&price={max_price}"

        except Exception as e:
            print("Budget error:", e, flush=True)

    search_url = f"{base_url}?{query}"

    affiliate_url = search_url + "&affcid=US.DIRECT.PHG.1100l422474.0"

    print("EXPEDIA FINAL URL:", affiliate_url, flush=True)

    return affiliate_url

# =====================================================
# TRAVEL INSPIRATION
# =====================================================
def is_travel_inspiration(text):

    text = text.lower()

    keywords = [
        "προορισμ",
        "που να παω",
        "που να ταξιδ",
        "τι προτειν",
        "ιδεα ταξιδ",
        "inspiration"
    ]

    for k in keywords:
        if k in text:
            return True

    return False

# =====================================================
# TRAVEL AI ADVISOR
# =====================================================
def travel_ai_advisor(user_text):

    prompt = f"""
Είσαι έμπειρος travel advisor.

Ο χρήστης ζητά ιδέες για ταξίδι ή προορισμό.

Μήνυμα χρήστη:
{user_text}

Πρότεινε 2-3 προορισμούς που ταιριάζουν στο αίτημα.

Για κάθε προορισμό γράψε σύντομα γιατί αξίζει.

Παραδείγματα αιτημάτων:
- ήσυχο νησί
- οικονομικό weekend κοντά στην Αθήνα
- προορισμός με φύση
- ρομαντικό ταξίδι
- ξενοδοχείο με πισίνα

Κράτα την απάντηση σύντομη και χρήσιμη.
Στο τέλος της απάντησης ρώτα πάντα:

"Σου αρέσει κάποιο από αυτά τα μέρη να δούμε; Αν ναι ποιο;"
"""

    completion = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role":"system","content":prompt}],
        temperature=0.6
    )

    return completion.choices[0].message.content.strip()   

# =====================================================
# AI DETECT ADVISOR
# =====================================================    
def ai_detect_travel_intent(text):

    prompt = f"""
Classify the travel intent of the user.

User message:
{text}

Return ONLY one of the following values:

hotel_search
destination_inspiration
travel_question
other
"""

    completion = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role":"system","content":prompt}],
        temperature=0
    )

    return completion.choices[0].message.content.strip().lower()         
# =====================================================
# AI REALTIME AI ADVISOR
# =====================================================

def realtime_ai_advisor(conversation):

    conversation_text = full_conversation(conversation)

    web_context = ""

    prompt = f"""
Είσαι ο AI σύμβουλος του GorealAI για αγορές και ταξίδια.

Αν ο χρήστης ψάχνει για ξενοδοχείο ή ταξίδι, πρώτα πρέπει να συλλέξεις
τις απαραίτητες πληροφορίες πριν προτείνεις αποτελέσματα.

Απαραίτητες πληροφορίες για ξενοδοχεία:
- προορισμός
- ημερομηνία check-in
- ημερομηνία check-out
- αριθμός ατόμων

Προαιρετικά:
- budget
- παροχές (wifi, πρωινό, πισίνα)

Κανόνες:
- Κάνε ΜΟΝΟ μία ερώτηση κάθε φορά.
- Αν λείπουν οι ημερομηνίες, πάντα ρώτα για check-in και check-out.
- Μην εφευρίσκεις ονόματα ξενοδοχείων.
- Μην προτείνεις ξενοδοχεία πριν δημιουργηθεί το search link.

Χρησιμοποίησε την πρόσφατη πληροφορία από το internet για να απαντήσεις.

Συνομιλία:
{conversation_text}

Πληροφορίες από web:
{web_context}

Απάντησε φυσικά σαν expert σύμβουλος.
Αν ο χρήστης ρωτά για το τελευταίο μοντέλο προϊόντος,
πες ποιο είναι το πιο πρόσφατο που κυκλοφορεί.
"""

    completion = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role":"system","content":prompt}],
        temperature=0.3
    )

    return completion.choices[0].message.content.strip()

# =====================================================
# AI INTENT ENGINE
# =====================================================

def ai_extract_intent(conversation):

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
# AI EXTRACT SEARCH INTENT
# ===================================================== 

def ai_extract_search_intent(conversation):

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
# AI EXTRACT TRAVEL
# =====================================================
def ai_extract_travel_intent(conversation):

    user_text = full_conversation(conversation)

    prompt = f"""
You are an expert AI travel advisor.

Your job is not just to search hotels, but to understand the user's travel intent and recommend the best destinations and hotels like a professional travel consultant.

Always analyze the user's message and extract as many signals as possible.

Understand the following from the user request if possible:

• destination (city / island / country)
• proximity requests (near a city, near Athens, near Corinth etc)
• travel style (romantic, quiet, nature, luxury, family, party, adventure)
• environment preferences (sea, beach, mountains, nature)
• amenities (pool, wifi, breakfast, spa, beachfront)
• budget (cheap, budget, mid-range, luxury)
• dates or trip duration
• number of adults
• atmosphere (quiet, vibrant, traditional, luxury)

User request:
{user_text}

Your job is to understand the user's travel plans before searching.

User request:
{user_text}

Extract travel booking information from the conversation.

IMPORTANT:
The destination MUST always be returned in LATIN characters
compatible with Expedia URLs.

Return ONLY the city name in lowercase.

Correct examples:
santorini
skopelos
athens
rome
paris
derveni
xylokastro

Do NOT include country names.

Examples:
Patras, Greece -> patras
Athens -> athens
Σκόπελος -> skopelos
Σαντορίνη -> santorini
Ξυλόκαστρο -> xylokastro
Δερβένι -> derveni

Do NOT guess similar cities.

Return ONLY JSON.

Dates must be in YYYY-MM-DD format.

If the user does not specify the year assume the current year 2026.
Never return past dates.

If the user only gives one date assume it is the check-in date.

If checkout is missing return null.

If the user mentions children:
extract the number of children and their ages.

Examples:

"2 adults and 1 child age 5"

{{
"adults":2,
"children":1,
"children_ages":[5],
"rooms":1
}}

If children ages are unknown return:

"children": number
"children_ages": []
"rooms":1

JSON format:

{{
"destination": "city",
"checkin": "YYYY-MM-DD",
"checkout": "YYYY-MM-DD",
"adults": number,
"children": number or null,
"children_ages": [number],
"rooms": number,
"meal_plan": "FREE_BREAKFAST or null",
"amenities": ["WIFI","POOL"],
"budget_per_night": number or null
}}

Rules:

If information is missing return null.

Understand natural language such as:

People:
- "2 persons"
- "for two"
- "couple"
- "family"
- "3 adults"

Budget:
- "around 60 euros"
- "max 100"
- "cheap"
- "budget hotel"
- "up to 80"
- "under 120"

Amenities:
- "with breakfast"
- "breakfast included"
- "with wifi"
- "with pool"
- "with parking"
- "spa hotel"
- "sea view"
- "pet friendly"

Map these to:

breakfast -> FREE_BREAKFAST

Amenities examples:
wifi -> WIFI
pool -> POOL
parking -> PARKING
spa -> SPA
gym -> FITNESS_CENTER
sea view -> OCEAN_VIEW

Understand both English and Greek language.

Examples:

User: ξενοδοχείο στο ναύπλιο με πισίνα 10 με 12 ιουνίου για 2 άτομα

{{
"destination": "nafplio",
"checkin": "2026-06-10",
"checkout": "2026-06-12",
"adults": 2,
"meal_plan": null,
"amenities": ["POOL"],
"budget_per_night": null
}}

User: hotel in patras 10 may to 13 may with breakfast

{{
"destination": "patras",
"checkin": "2026-05-10",
"checkout": "2026-05-13",
"adults": 2,
"meal_plan": "FREE_BREAKFAST",
"amenities": [],
"budget_per_night": null
}}

User: cheap hotel in xylokastro with wifi around 70

{{
"destination": "xylokastro",
"checkin": null,
"checkout": null,
"adults": 2,
"meal_plan": null,
"amenities": ["WIFI"],
"budget_per_night": 70
}}
"""

    completion = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role":"system","content":prompt}],
        temperature=0
    )

    try:
        return json.loads(completion.choices[0].message.content)
    except:
        return {}          

# =====================================================
# AI RESOLVE CATEGORY
# =====================================================        
def ai_resolve_category(user_query, categories):

    prompt = f"""
User wants to buy:

{user_query}

Available shop categories:

{categories}

Choose the SINGLE category that best matches the product.

Return ONLY the category name.
"""

    completion = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role":"user","content":prompt}],
        temperature=0
    )

    return completion.choices[0].message.content.strip()  


# =====================================================
# DETECT CATEGORY
# =====================================================
def detect_main_category(profile):

    conn = get_db_connection()
    cur = conn.cursor()

    query_text = profile.get("query_text", "")

    sql = """
        SELECT category, COUNT(*) as cnt
        FROM products
        WHERE search_vector @@ to_tsquery('simple', %s)
        GROUP BY category
        ORDER BY cnt DESC
        LIMIT 1;
    """

    cur.execute(sql, (query_text,))
    result = cur.fetchone()

    cur.close()
    conn.close()

    if result:
        return result[0]

    return None 


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
    # CATEGORY FILTER
    # ------------------------------------

    category = profile.get("category")

    if category:
        sql += """
        AND category80 = %s
        """
        params.append(category)

    # ------------------------------------
    # BUDGET FILTER
    # ------------------------------------

    budget = profile.get("budget_max")

    if budget:
        sql += " AND price <= %s"
        params.append(budget)

    # ------------------------------------
    # ORDER BY RELEVANCE + PRICE
    # ------------------------------------

    sql += """
    ORDER BY
        rank DESC,
        price ASC
    LIMIT %s
    """

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
# DETECT DESTINATION NAME
# =====================================================
def detect_destination_name(text):

    words = text.strip().split()

    if len(words) <= 2:
        return text.strip().lower()

    return None    
# =====================================================
# GENERATE RECOMMENDATIONS – DATABASE VERSION
# =====================================================

def generate_recommendations(mode, conversation):

    print("ENTERED AI INTENT ENGINE", flush=True)

    intent = ai_extract_search_intent(conversation)
    intent_type = intent.get("intent_type", "product_search")

    user_text = get_last_user_text(conversation)

    # =========================
    # EXPEDIA HOTEL SEARCH
    # =========================

    
    user_id = "default_user"
    profile = USER_PROFILES.setdefault(user_id, {})   

    travel = ai_extract_travel_intent(conversation)

    user_text = get_last_user_text(conversation).lower()

        # ignore default adults
        if travel.get("adults") == 2 and not any(x in user_text for x in ["1","2","3","4","ατομ","άτομ","people"]):
            travel["adults"] = None

        # children fix
        if travel.get("children") is None:
            if any(x in user_text for x in ["όχι","οχι","no","χωρίς","δεν"]):
                travel["children"] = 0

        # amenities fix
        if travel.get("amenities") is None:
            if "όχι" in user_text or "οχι" in user_text or "δεν" in user_text:
                travel["amenities"] = []

        destination = travel.get("destination") or profile.get("destination")
        checkin = travel.get("checkin") or profile.get("checkin")
        checkout = travel.get("checkout") or profile.get("checkout")
        adults = travel.get("adults") if travel.get("adults") is not None else profile.get("adults")
        children = travel.get("children") if travel.get("children") is not None else profile.get("children")
        meal_plan = travel.get("meal_plan") or profile.get("meal_plan")
        amenities = travel.get("amenities") or profile.get("amenities")
        budget = travel.get("budget_per_night") or profile.get("budget_per_night")

        children_ages = travel.get("children_ages") or []
        rooms = travel.get("rooms") or 1

        profile["destination"] = destination
        profile["checkin"] = checkin
        profile["checkout"] = checkout
        profile["adults"] = adults
        profile["children"] = children
        profile["meal_plan"] = meal_plan
        profile["amenities"] = amenities
        profile["budget_per_night"] = budget

        user_text = get_last_user_text(conversation)
        filters = extract_travel_filters(user_text)

        if filters["meal_plan"]:
            meal_plan = filters["meal_plan"]

        if filters["amenities"]:
            amenities = filters["amenities"]

        if filters["adults"]:
            adults = filters["adults"]

        if not destination:
            return {
                "reply": "Σε ποια πόλη θέλεις να ταξιδέψεις;",
                "links": [],
                "showButton": False
            }

        if not checkin or not checkout or checkin.strip()=="" or checkout.strip()=="":
            return {
                "reply": "Ποιες ημερομηνίες σκέφτεσαι για το ταξίδι;",
                "links": [],
                "showButton": False
            }

        expedia_link = build_expedia_search_url(
            destination=destination,
            checkin=checkin,
            checkout=checkout,
            adults=adults,
            children_ages=children_ages,
            rooms=rooms,
            meal_plan=meal_plan,
            amenities=amenities,
            budget_total=budget
        )

        links = [{
            "title": f"Ξενοδοχεία στο {destination}",
            "url": expedia_link
        }]

        return {
            "reply": f"Βρήκα επιλογές για {destination}. Δες τα ξενοδοχεία εδώ 👇",
            "links": links,
            "showButton": True
        }

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

    resolved_category = ai_resolve_category(
        search_text,
        categories
    )

    print("RESOLVED CATEGORY:", resolved_category, flush=True)

    profile = {
        "query_text": search_text,
        "descriptive_tokens": search_text.split(),
        "numeric_tokens": [],
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
@app.route("/chat", methods=["POST","OPTIONS"])
    

def chat():

    if request.method == "OPTIONS":
            return jsonify({"status": "ok"})
    data = request.json or {}
    user_id = data.get("userId", "anonymous")

    history = data.get("history", [])
    db.collection("chat_sessions").document(user_id).set({
        "history": history
    })
    mode = data.get("mode", "shopping")
    ask_for_options = data.get("askOptions", False)

    username = data.get("userName") or "φίλε"
    name = f" {username}" if username else ""

    if len(history) <= 1:

        if mode == "travel":

            welcome_text = f"""
            
            Καλώς ήρθες ξανά {username} ✈️

            Πες μου σε ποια πόλη θέλεις να ταξιδέψεις και θα σου βρω ξενοδοχεία.

            Μπορείς να γράψεις π.χ.

            • ξενοδοχείο Πάτρα
            • ξενοδοχείο Σαντορίνη
            • ξενοδοχείο Αθήνα κέντρο
            """

        elif mode == "services":
            welcome_text = f"""Καλώς ήρθες ξανά {username} 🔧

        Πες μου τι επαγγελματία χρειάζεσαι.

        Μπορείς να γράψεις π.χ.

        • υδραυλικός Χαλάνδρι
        • ηλεκτρολόγος Αθήνα
        • μάστορας για πλακάκια
        """

        else:
            welcome_text = f"""Καλώς ήρθες ξανά {username} 👋

        Πες μου τι θέλεις να αγοράσεις και θα σου βρω τις καλύτερες επιλογές.

        Μπορείς να γράψεις π.χ.

        • iPhone 16 Pro 256GB
        • καναπές γωνιακός έως 700€
        • Sony PlayStation 5 Slim
        """    
        return jsonify({
            "reply": welcome_text,
            "links": [],
            "showbutton": False
            
        })

    total_user = len([
        m for m in history
        if isinstance(m, dict) and m.get("isUser") is True
    ])

    total_links = len([
        m for m in history
        if isinstance(m.get("links"), list) and m.get("links")
    ])

    # -----------------------------------------
    # FLOATING BUTTON
    # -----------------------------------------

    if ask_for_options:

        response = generate_recommendations(mode, history)

        links = response.get("links", [])
        hotels = response.get("hotels", [])

        if (isinstance(links, list) and len(links) > 0) or (isinstance(hotels, list) and len(hotels) > 0):

            if response.get("reply"):
                response["reply"] += "\n\nΑν δεν βρήκες αυτό που θέλεις συνεχίζουμε 👌"

            return jsonify(response)

        return jsonify({
            "reply": "Δεν βρήκα ακόμη τις κατάλληλες επιλογές.",
            "links": [],
            "showButton": False
        })

    # -----------------------------------------
    # BEFORE LINKS
    # -----------------------------------------

    if total_links == 0:  
  
        if mode == "travel":  

            user_text = get_last_user_text(history)

            intent_type = ai_detect_travel_intent(user_text)

            possible_destination = detect_destination_name(user_text)

            if intent_type == "destination_inspiration" and not possible_destination:

                advice = travel_ai_advisor(user_text)

                return jsonify({
                    "reply": advice,
                    "links": [],
                    "showButton": False
                })
    
            travel = ai_extract_travel_intent(history)  
            print("TRAVEL AI OUTPUT:", travel, flush=True)  
    
            destination = normalize_destination(travel.get("destination"))  
            checkin = travel.get("checkin")  
            checkout = travel.get("checkout")  
            adults = travel.get("adults")  
            budget = travel.get("budget_per_night")  
            rooms = travel.get("rooms")
            amenities = travel.get("amenities")  
            children = travel.get("children")
            children_ages = travel.get("children_ages") or []
    
            last_user = get_last_user_text(history).lower()  
    
            if amenities is None:  
    
                if "πρωιν" in last_user or "breakfast" in last_user:  
                    amenities = ["FREE_BREAKFAST"]  
    
                elif "wifi" in last_user:  
                    amenities = ["WIFI"]  
    
                elif "πισιν" in last_user or "pool" in last_user:  
                    amenities = ["POOL"]  
    
            # ---------------------------------
            # Check what information is missing
            # ---------------------------------

            missing = []

            if destination is None:
                missing.append("destination")

            if checkin is None or checkout is None:
                missing.append("dates")

            if adults is None:
                missing.append("adults")

            if children is None:
                missing.append("children")

            if children and not children_ages:
                missing.append("children_ages")

            if budget is None:
                missing.append("budget")

            if amenities is None:
                missing.append("amenities")

            # ---------------------------------
            # Ask ONLY the missing information
            # ---------------------------------

            if missing:

                if "destination" in missing:
                    return jsonify({
                        "reply": f"Σε ποια πόλη θα ήθελες να ταξιδέψεις{name};",
                        "links": [],
                        "showButton": False
                    })

                if "dates" in missing:
                    return jsonify({
                        "reply": "Ποιες ημερομηνίες σκέφτεσαι για το ταξίδι σου;",
                        "links": [],
                        "showButton": False
                    })

                if "adults" in missing:
                    return jsonify({
                        "reply": "Για πόσα άτομα θα έιναι η κράτηση στο ξενοδοχείο;",
                        "links": [],
                        "showButton": False
                    })

                if "children" in missing:
                    return jsonify({
                        "reply": "Για το ταξίδι που σκέφτεσαι θα υπάρχουν και παιδιά; Αν ναι πες μου σε παρακαλώ πόσα;",
                        "links": [],
                        "showButton": False
                    })   

                if "children_ages" in missing:
                    return jsonify({
                        "reply": "Τι ηλικίες έχουν τα παιδιά;",
                        "links": [],
                        "showButton": False
                    })     

                if "budget" in missing:
                    return jsonify({
                        "reply": f"{name} τι budget περίπου έχεις στο μυαλό σου;",
                        "links": [],
                        "showButton": False
                    })

                if "amenities" in missing:
                    return jsonify({
                        "reply": "Θέλεις κάποιες συγκεκριμένες παροχές όπως πρωινό, wifi ή πισίνα;",
                        "links": [],
                        "showButton": False
                    })

            return jsonify({
                "reply": "",
                "links": [],
                "showButton": True
            })

        intent = ai_extract_search_intent(history) or {}

        intent_score = 0

        if intent.get("category"):
            intent_score += 2

        if intent.get("budget_max"):
            intent_score += 1

        if intent.get("search_keywords_en") or intent.get("search_keywords_gr"):
            intent_score += 2

        print("INTENT SCORE:", intent_score, flush=True)

        if mode != "travel" and intent_score >= 6:
            return jsonify({
                "reply": "",
                "links": [],
                "showButton": True
            })

        if mode != "travel" and total_user >= 4:
            return jsonify({
                "reply": "",
                "links": [],
                "showButton": True
            })

        return jsonify(ai_advisor_response(history))

    # -----------------------------------------
    # AFTER LINKS
    # -----------------------------------------

    if total_links > 0:

        last_links_index = -1

        for i in range(len(history) - 1, -1, -1):
            if isinstance(history[i].get("links"), list) and len(history[i].get("links")) > 0:
                last_links_index = i
                break

        user_after_links = 0

        for msg in history[last_links_index + 1:]:
            if msg.get("isUser"):
                user_after_links += 1

        print("USER AFTER LINKS:", user_after_links, flush=True)

        if user_after_links >= 2:
            return jsonify({
                "reply": "",
                "links": [],
                "showButton": True
            })

        return jsonify(ai_advisor_response(history))