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
import psycopg2
from psycopg2.extras import execute_batch

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
# DATABASE (PostgreSQL - Render)
# =====================================================

DATABASE_URL = os.environ.get("DATABASE_URL")

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)


# =====================================================
# CONFIG
# =====================================================

LINKWISE_FEED_SHOPPING = "https://affiliate.linkwi.se/feeds/1.2/CD28160/programs-joined/columns-product_id,model_name,product_name,description,category,brand_name,tracking_url,thumb_url,image_url,in_stock,availability,valid_from,valid_to,on_sale,currency,price,full_price,discount,city,times_bought,longitude,latitude,address,size,colour,custom,extra_images,variations/catinc-0/catex-0/proginc-11532-726,12858-2366,13987-2681,13208-2081,12125-1139,11920-1064,12218-1239,13306-2056,13527-2303,13806-2653,11036-369,12761-1652,14114-2761,11593-815,12560-1466,13990-2713,11834-955,11983-1078,13962-2677,12011-1042,13640-2370,11442-602,138-2273,12174-1176,12315-1323,13779-2538,13535-2262,13941-2644,12802-1676,14123-2770,10784-281,13240-2087,12471-1412,11388-564,11609-771,10553-1827,469-299,13026-1874,13993-2692,13754-2454,12056-1106,11432-621,11307-622,11641-847,12071-1114,12615-1512,12321-1361,11754-880,13604-2421,12569-1461,11537-2451,13775-2623/progex-0/feed.xml"

LINKWISE_FEED_TRAVEL = "https://affiliate.linkwi.se/feeds/1.2/CD28160/programs-joined/columns-product_id,model_name,product_name,description,category,brand_name,tracking_url,thumb_url,image_url,in_stock,availability,valid_from,valid_to,on_sale,currency,price,full_price,discount,city,times_bought,longitude,latitude,address,size,colour,custom,extra_images,variations/catinc-0/catex-0/proginc-177-478,205-67/progex-0/feed.xml"

CONFIDENCE_THRESHOLD = 7.5


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
        role = "Χρήστης" if msg.get("isUser") else "Βοηθός"
        text = msg.get("text")
        if text:
            texts.append(f"{role}: {text}")
    return "\n".join(texts)


def get_last_user_text(history):
    for msg in reversed(history):
        if msg.get("isUser") and msg.get("text"):
            return msg.get("text")
    return ""
 # =====================================================
# DATABASE PRODUCT FETCH
# =====================================================

def fetch_products_from_db(mode, profile, limit=2000):

    conn = get_db_connection()
    cur = conn.cursor()

    query = """
        SELECT title, description, brand, category, price, url
        FROM products
        WHERE mode = %s
    """

    params = [mode]

    # Budget filter (SQL level)
    if profile.get("budget_max"):
        query += " AND price <= %s"
        params.append(profile["budget_max"])

    if profile.get("budget_min"):
        query += " AND price >= %s"
        params.append(profile["budget_min"])

    # Model tokens (strong filter)
    for token in profile.get("model_tokens", []):
        query += " AND LOWER(title) LIKE %s"
        params.append(f"%{token.lower()}%")

    query += " LIMIT %s"
    params.append(limit)

    cur.execute(query, params)
    rows = cur.fetchall()

    cur.close()
    conn.close()

    results = []

    for r in rows:
        results.append({
            "title": r[0],
            "description": r[1],
            "brand": r[2],
            "category": r[3],
            "price": r[4],
            "url": r[5],
        })

    return results


# =====================================================
# DETERMINISTIC SCORING ENGINE (DB VERSION)
# =====================================================

def score_products(products, profile):

    scored = []

    for p in products:

        score = 0
        searchable = f"{p['title']} {p['category']} {p['brand']}".lower()

        # Descriptive overlap
        for word in profile.get("descriptive_tokens", []):
            if word.lower() in searchable:
                score += 3

        # Numeric overlap
        product_numbers = extract_numbers(searchable)
        for num in profile.get("numeric_tokens", []):
            if num in product_numbers:
                score += 5

        scored.append({
            **p,
            "decision_score": round(score, 2)
        })

    scored.sort(key=lambda x: x["decision_score"], reverse=True)

    return scored


# =====================================================
# AI FINAL VALIDATION (TOP 15 ONLY)
# =====================================================

def ai_validate_top(conversation, profile, candidates):

    top = candidates[:15]

    prompt = f"""
Είσαι Decision Engine.

Συνομιλία:
{conversation}

Decision Profile:
{json.dumps(profile, ensure_ascii=False)}

Υποψήφια προϊόντα:
{json.dumps(top, ensure_ascii=False)}

Δώσε ΜΟΝΟ JSON:

[
  {{"url": "...", "confidence": 0-10}}
]

Confidence = πόσο τέλειο match είναι.
"""

    try:
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": prompt}],
            temperature=0.1
        )

        return json.loads(completion.choices[0].message.content.strip())
    except:
        return []


# =====================================================
# CONFIDENCE FILTER
# =====================================================

def apply_confidence(products, ai_scores):

    if not ai_scores:
        return []

    validated_urls = []

    for item in ai_scores:
        if item.get("confidence", 0) >= CONFIDENCE_THRESHOLD:
            validated_urls.append(item.get("url"))

    return validated_urls


# =====================================================
# GENERATE RECOMMENDATIONS – DATABASE VERSION
# =====================================================

def generate_recommendations(mode, conversation):

    print("ENTERED UNIVERSAL DECISION ENGINE (DB)", flush=True)

    # 1️⃣ Build decision profile
    profile = build_decision_profile(conversation)
    print("DECISION PROFILE:", profile, flush=True)

    # 2️⃣ Fetch candidates from PostgreSQL
    candidates = fetch_products_from_db(mode, profile, limit=3000)

    print("DB CANDIDATES:", len(candidates), flush=True)

    if not candidates:
        return {
            "reply": "Δεν βρήκα αρκετά σχετικές επιλογές. Μπορείς να μου δώσεις λίγες ακόμη λεπτομέρειες;",
            "links": [],
            "showButton": False
        }

    # 3️⃣ Deterministic scoring
    scored = score_products(candidates, profile)

    if not scored:
        return {
            "reply": "Δεν βρήκα αρκετά σχετικές επιλογές. Μπορείς να μου δώσεις λίγες ακόμη λεπτομέρειες;",
            "links": [],
            "showButton": False
        }

    # 4️⃣ AI validation only on top 15
    ai_scores = ai_validate_top(conversation, profile, scored)

    validated_urls = apply_confidence(scored, ai_scores)

    if not validated_urls:
        return {
            "reply": "Βρήκα κάποιες επιλογές αλλά χρειάζομαι λίγο πιο συγκεκριμένες πληροφορίες για να είμαι απόλυτα ακριβής.",
            "links": [],
            "showButton": False
        }

    # 5️⃣ Keep only validated products
    final_products = [
        p for p in scored
        if p["url"] in validated_urls
    ]

    # 6️⃣ Take top 5
    top_results = final_products[:5]

    links = [
        {
            "title": f"{item.get('title')} – {item.get('price')}€",
            "url": item.get("url")
        }
        for item in top_results
    ]

    reply_text = (
        "Βρήκα τις πιο σχετικές επιλογές για εσένα 👇"
        if mode == "shopping"
        else "Βρήκα τις πιο σχετικές επιλογές για το ταξίδι σου 👇"
    )

    return {
        "reply": reply_text,
        "links": links,
        "showButton": True
    }

# =====================================================
# ROUTE
# =====================================================
@app.route("/chat", methods=["POST"])
def chat():

    data = request.json or {}

    history = data.get("history", [])
    mode = data.get("mode", "shopping")
    ask_for_options = data.get("askOptions", False)

    username = data.get("userName") or "φίλε"

    conversation = full_conversation(history)

    # -------------------------------------------------
    # FIRST MESSAGE
    # -------------------------------------------------
    if not history:
        return jsonify({
            "reply": f"Καλώς ήρθες ξανά {username} 👋\n\nΤι θα ήθελες να βρούμε σήμερα;",
            "showButton": False
        })

    # -------------------------------------------------
    # USER PRESSED FLOATING BUTTON
    # -------------------------------------------------
    if ask_for_options:
        return jsonify(generate_recommendations(mode, conversation))

    # -------------------------------------------------
    # CONVERSATIONAL FLOW
    # -------------------------------------------------

    total_user = len([m for m in history if m.get("isUser")])
    total_links_shown = len([
        m for m in history
        if isinstance(m.get("links"), list) and len(m.get("links")) > 0
    ])

    # If no links shown yet → continue advisor mode
    if total_links_shown == 0:

        # Advisor asks 1–2 clarification questions
        if total_user <= 2:
            return jsonify({
                "reply": ai_single_question(conversation),
                "showButton": False
            })

        # After 2 user messages → user controls decision
        return jsonify({
            "reply": ai_single_question(conversation),
            "showButton": False
        })

    # If links already shown → continue advisor refinement
    return jsonify({
        "reply": ai_single_question(conversation),
        "showButton": False
    })

create_products_table()
# =====================================================
# RUN (NO PRELOAD ANYMORE)
# =====================================================

port = int(os.environ.get("PORT", 10000))
app.run(host="0.0.0.0", port=port)