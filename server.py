from flask import Flask, request, jsonify
from flask_cors import CORS
from openai import OpenAI
import os
import json
import requests
import urllib.parse
import firebase_admin
from firebase_admin import credentials, firestore, messaging
import time
import xml.etree.ElementTree as ET
import re
import unicodedata
from city_lookup import fix_city_name
from city_utils import resolve_destination
import psycopg2
from memory_engine import load_user_memory
from psycopg2.extras import execute_batch
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timezone, timedelta
USER_PROFILES_SHOPPING = {}
USER_PROFILES_TRAVEL = {}
IMAGE_USAGE = {}

from shopping import (
    generate_recommendations,
    ai_extract_search_intent,
    build_profile_from_intent,
    is_profile_complete_ai,
    generate_next_question_ai
)


from travel import (
    ai_extract_travel_intent,
    travel_guide_ai,
    travel_followup_questions,
    ai_detect_travel_intent,
    travel_ai_advisor,
    generate_travel_recommendations,
    build_expedia_search_url,
    build_agoda_search_url
)

from city_utils import full_conversation, get_last_user_text, normalize_text_ai
from city_utils import web_search_context
from city_utils import GREEK_NUMBERS

from services import (
    ai_extract_service_intent,
    ai_detect_profession_from_problem,
    search_google_places,
    log_professional_click,
    ai_needs_clarification
)



import pandas as pd
import random

travel_df = pd.read_csv("travel_feed.csv")

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
# Idempotency cache για ai-memory (αποτρέπει διπλές εγγραφές)
_memory_idempotency_cache = set()


# ═══════════════════════════════════════════════════
# APScheduler — Reminders + Request expiry
# ═══════════════════════════════════════════════════

def send_reminders():
    """Κάθε λεπτό — στέλνει FCM push για υπενθυμίσεις."""
    try:
        now = datetime.now(timezone.utc)
        snap = db.collection('reminders').where('sent', '==', False).stream()
        for doc in snap:
            data = doc.to_dict()
            reminder_time = data.get('reminderTime')
            if reminder_time is None:
                continue
            if hasattr(reminder_time, 'timestamp'):
                rt = datetime.fromtimestamp(reminder_time.timestamp(), tz=timezone.utc)
            else:
                rt = reminder_time
            if rt.tzinfo is None:
                rt = rt.replace(tzinfo=timezone.utc)
            if abs((rt - now).total_seconds()) > 120:
                continue
            user_id = data.get('userId', '')
            summary = data.get('summary', 'Υπενθύμιση')
            user_doc = db.collection('users').document(user_id).get()
            if not user_doc.exists:
                doc.reference.update({'sent': True})
                continue
            fcm_token = user_doc.to_dict().get('fcmToken')
            if fcm_token:
                try:
                    msg = messaging.Message(
                        notification=messaging.Notification(
                            title='⏰ Υπενθύμιση GorealAI', body=summary),
                        android=messaging.AndroidConfig(
                            priority='high',
                            notification=messaging.AndroidNotification(
                                channel_id='gorealai_reminders', sound='default')),
                        apns=messaging.APNSConfig(
                            payload=messaging.APNSPayload(
                                aps=messaging.Aps(sound='default', badge=1))),
                        token=fcm_token,
                    )
                    messaging.send(msg)
                    print(f"✅ Reminder sent to {user_id}: {summary}", flush=True)
                except Exception as e:
                    print(f"❌ FCM error: {e}", flush=True)
            doc.reference.update({'sent': True})
    except Exception as e:
        print(f"REMINDER ERROR: {e}", flush=True)


def process_expired_requests():
    """Κάθε λεπτό — ελέγχει αιτήματα που έληξαν και τρέχει AI φιλτράρισμα."""
    try:
        now = datetime.now(timezone.utc)
        snap = db.collection('requests').where('status', '==', 'active').stream()
        for doc in snap:
            data = doc.to_dict()
            expires_at = data.get('expiresAt')
            if expires_at is None:
                continue
            if hasattr(expires_at, 'timestamp'):
                et = datetime.fromtimestamp(expires_at.timestamp(), tz=timezone.utc)
            else:
                et = expires_at
            if et.tzinfo is None:
                et = et.replace(tzinfo=timezone.utc)
            if now < et:
                continue  # Δεν έληξε ακόμα

            request_id = doc.id
            user_id = data.get('userId', '')
            criteria = data.get('criteria', 'cheap')

            print(f"⏰ Request expired: {request_id}", flush=True)

            # Φόρτωσε προσφορές
            offers_snap = db.collection('offers').where('requestId', '==', request_id).stream()
            offers = [o.to_dict() | {'id': o.id} for o in offers_snap]

            if offers:
                # AI φιλτράρισμα
                top3 = ai_filter_offers(offers, criteria, data.get('description', ''))
                doc.reference.update({
                    'status': 'completed',
                    'topOffers': top3,
                    'completedAt': firestore.SERVER_TIMESTAMP,
                })
                print(f"✅ {len(top3)} top offers selected for {request_id}", flush=True)
            else:
                doc.reference.update({'status': 'no_offers'})

            # Push notification στον χρήστη
            user_doc = db.collection('users').document(user_id).get()
            if user_doc.exists:
                fcm_token = user_doc.to_dict().get('fcmToken')
                if fcm_token:
                    try:
                        count = len(offers)
                        body = f"Έλαβες {count} προσφορές! Δες τις 3 καλύτερες." if count > 0 else "Δεν ήρθαν προσφορές αυτή τη φορά."
                        msg = messaging.Message(
                            notification=messaging.Notification(
                                title='⏰ Ο χρόνος έληξε!', body=body),
                            data={'requestId': request_id, 'type': 'request_expired'},
                            android=messaging.AndroidConfig(priority='high'),
                            token=fcm_token,
                        )
                        messaging.send(msg)
                    except Exception as e:
                        print(f"FCM error: {e}", flush=True)

    except Exception as e:
        print(f"REQUEST EXPIRY ERROR: {e}", flush=True)


def ai_filter_offers(offers, criteria, description):
    """AI επιλέγει τις 3 καλύτερες προσφορές βάσει κριτηρίου."""
    try:
        criteria_map = {
            'cheap': 'lowest price',
            'value': 'best value for money (price/quality ratio)',
            'fast': 'fastest availability'
        }
        criteria_text = criteria_map.get(criteria, 'best overall')

        offers_text = "\n".join([
            f"Offer {i+1}: {o.get('professionalName','?')} | Price: {o.get('price','?')}€ | "
            f"Available: {o.get('availableFrom','?')} | Rating: {o.get('rating','?')} | "
            f"Message: {o.get('message','')[:100]}"
            for i, o in enumerate(offers)
        ])

        prompt = f"""
You are an AI assistant helping a user choose the best professional for their job.

User's request: "{description}"
Selection criteria: {criteria_text}

Offers received:
{offers_text}

Select the TOP 3 offers based on the criteria. 
Return ONLY a JSON array of offer indices (0-based), ordered best first.
Example: [2, 0, 4]
"""
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=50
        )
        result = completion.choices[0].message.content.strip()
        result = result.replace("```json", "").replace("```", "").strip()
        indices = json.loads(result)
        top3 = []
        for idx in indices[:3]:
            if 0 <= idx < len(offers):
                offer = offers[idx].copy()
                offer['rank'] = len(top3) + 1
                top3.append(offer)
        return top3
    except Exception as e:
        print(f"AI FILTER ERROR: {e}", flush=True)
        # Fallback: sort by price
        sorted_offers = sorted(offers, key=lambda x: float(x.get('price', 9999)))
        for i, o in enumerate(sorted_offers[:3]):
            o['rank'] = i + 1
        return sorted_offers[:3]


# Ξεκίνα APScheduler
_scheduler = BackgroundScheduler(timezone="Europe/Athens")
_scheduler.add_job(send_reminders, 'interval', minutes=1, id='reminders')
_scheduler.add_job(process_expired_requests, 'interval', minutes=1, id='requests')
_scheduler.start()
print("✅ Scheduler started (reminders + requests)", flush=True)


def clean_text(t):
                t = unicodedata.normalize('NFD', t)
                t = ''.join(c for c in t if unicodedata.category(c) != 'Mn')
                return t.lower()




# =====================================================
# VOCATIVE NAME
# =====================================================
def vocative_name(name):

    if not name:
        return ""

    name = name.strip()

    # Αν τελειώνει σε ς → το αφαιρούμε
    if name.endswith("ς"):
        return name[:-1]

    return name

def clean_reply(text):
    """Αφαιρεί το 'Assistant:' prefix αν το βάλει το OpenAI model."""
    if not text:
        return text
    import re
    text = re.sub(r'^(Assistant|AI|Bot)\s*:\s*', '', text.strip(), flags=re.IGNORECASE)
    return text.strip()

def save_search_history(db, user_id, query, mode):
    """Αποθηκεύει μόνο το τελικό αποτέλεσμα αναζήτησης στο ιστορικό."""
    try:
        if not query or len(query.strip()) < 2:
            return
        db.collection("search_history").add({
            "userId": user_id,
            "query": query.strip(),
            "mode": mode,
            "createdAt": firestore.SERVER_TIMESTAMP
        })
        print(f"📚 HISTORY SAVED: {query}", flush=True)
    except Exception as e:
        print(f"HISTORY SAVE ERROR: {e}", flush=True)

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

    return clean_reply(completion.choices[0].message.content.strip())


@app.route("/reset-profile", methods=["POST"])
def reset_profile():
    data = request.json
    user_id = data.get("userId", "anonymous")
    mode = data.get("mode", "")

    if mode == "travel":
        USER_PROFILES_TRAVEL.pop(user_id, None)
    elif mode == "shopping":
        USER_PROFILES_SHOPPING.pop(user_id, None)
    elif mode == "services":
        USER_PROFILES_SERVICES.pop(user_id, None)

    print(f"🔄 RESET PROFILE: {user_id} mode={mode}", flush=True)
    return jsonify({"status": "ok"})


# =====================================================
# GOOGLE VISION HELPER
# =====================================================

def google_lens_analyze(image_base64):
    """
    Στέλνει εικόνα στο Google Lens μέσω SerpApi.
    Επιστρέφει product title, visual matches με τιμές και URLs.
    """
    import base64, os, tempfile
    try:
        serpapi_key = os.environ.get("SERPAPI_KEY")
        if not serpapi_key:
            print("⚠️ SERPAPI_KEY not set", flush=True)
            return None

        # Αποθήκευσε την εικόνα προσωρινά ως αρχείο
        image_bytes = base64.b64decode(image_base64)
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp.write(image_bytes)
            tmp_path = tmp.name

        # SerpApi Google Lens request
        params = {
            "engine": "google_lens",
            "api_key": serpapi_key,
            "image_path": tmp_path,
            "hl": "el",
            "country": "gr"
        }

        from serpapi import GoogleSearch
        search = GoogleSearch(params)
        result = search.get_dict()

        # Καθάρισε το temp file
        import os as _os
        try: _os.unlink(tmp_path)
        except: pass

        print(f"✅ LENS RAW KEYS: {list(result.keys())}", flush=True)

        # Εξαγωγή αποτελεσμάτων
        visual_matches = result.get("visual_matches", [])
        knowledge_graph = result.get("knowledge_graph", {})
        text_results = result.get("text_in_image", [])

        # Product title από knowledge graph ή πρώτο visual match
        product_title = knowledge_graph.get("title", "")
        if not product_title and visual_matches:
            product_title = visual_matches[0].get("title", "")

        # Κείμενο από εικόνα (brand name, model number)
        ocr_text = " ".join([t.get("text", "") for t in text_results[:3]]) if text_results else ""

        print(f"✅ LENS: title='{product_title}', matches={len(visual_matches)}, ocr='{ocr_text[:50]}'", flush=True)

        return {
            "product_title": product_title,
            "visual_matches": visual_matches[:5],
            "ocr_text": ocr_text,
            "knowledge_graph": knowledge_graph
        }

    except Exception as e:
        print(f"LENS EXCEPTION: {e}", flush=True)
        return None


def build_smart_query_from_lens(lens_data, user_text=""):
    """
    Φτιάχνει search query από τα Google Lens αποτελέσματα.
    Προτεραιότητα: product_title > visual match title > ocr_text
    """
    if not lens_data:
        return None

    product_title = lens_data.get("product_title", "")
    visual_matches = lens_data.get("visual_matches", [])
    ocr_text = lens_data.get("ocr_text", "")

    query = ""

    if product_title:
        query = product_title
    elif visual_matches:
        # Βρες τον πιο συχνό τίτλο από τα visual matches
        titles = [m.get("title", "") for m in visual_matches if m.get("title")]
        if titles:
            query = titles[0]
    elif ocr_text:
        query = ocr_text.strip().split('\n')[0][:60]

    # Αν ο χρήστης έγραψε κάτι → πρόσθεσε context
    if user_text and user_text.lower() not in query.lower():
        query = f"{query} {user_text}".strip()

    print(f"🔍 LENS QUERY: '{query}'", flush=True)
    return query if query else None


@app.route("/analyze-image", methods=["POST"])
def analyze_image():
    try:
        data = request.json
        image_base64 = data.get("image")
        user_text = data.get("text", "")
        mode = data.get("mode", "shopping")
        user_id = data.get("userId", "anonymous")

        # 🔥 Reset shopping profile για νέα φωτογραφία
        if user_id in USER_PROFILES_SHOPPING:
            USER_PROFILES_SHOPPING[user_id] = {}
            print("🔥 RESET SHOPPING PROFILE", flush=True)

        if not image_base64:
            return jsonify({"error": "no_image"}), 400

        usage = IMAGE_USAGE.get(user_id, 0)
        if usage >= 100:
            return jsonify({
                "error": "limit_reached",
                "message": "Έχεις χρησιμοποιήσει τις 10 δωρεάν αναλύσεις 😕\nΑναβάθμισε για €3.99/μήνα!"
            }), 200

        IMAGE_USAGE[user_id] = usage + 1
        remaining = 10 - IMAGE_USAGE[user_id]

        # 🔥 Shopping/Services mode από request
        shopping_mode_from_request = data.get("shopping_mode", None)
        if shopping_mode_from_request:
            USER_PROFILES_SHOPPING.setdefault(user_id, {})["shopping_mode"] = shopping_mode_from_request

        services_mode_from_request = data.get("services_mode", None)
        if services_mode_from_request:
            USER_PROFILES_SERVICES.setdefault(user_id, {})["services_mode"] = services_mode_from_request

        # =====================================================
        # 🔥 GOOGLE LENS (SerpApi) - Βήμα 1: Αναγνώριση
        # =====================================================
        lens_data = google_lens_analyze(image_base64)
        vision_query = build_smart_query_from_lens(lens_data, user_text) if lens_data else None

        print(f"LENS QUERY: {vision_query}", flush=True)

        if mode == "shopping":
            if vision_query:
                # =====================================================
                # 🔥 LENS → GPT clean → SERPER - Βήμα 2: Αναζήτηση
                # =====================================================
                from shopping import search_products_serper

                # GPT-4o-mini καθαρίζει το query
                enhance_prompt = f"""
Το Google Lens αναγνώρισε αυτό από φωτογραφία: "{vision_query}"
{f'Ο χρήστης έγραψε επίσης: "{user_text}"' if user_text else ''}

Φτιάξε το καλύτερο search query για να βρεις τιμές στο ελληνικό e-commerce.
Κανόνες:
- Αν είναι brand + model → κράτα το ακριβώς
- Αν είναι γενική κατηγορία → κράτα το
- Μέγιστο 5 λέξεις
- Απάντα ΜΟΝΟ το query, χωρίς εισαγωγικά
"""
                enhance = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": enhance_prompt}],
                    max_tokens=30,
                    temperature=0
                )
                final_query = enhance.choices[0].message.content.strip().strip('"')
                print(f"FINAL QUERY: {final_query}", flush=True)

                # Serper search
                products = search_products_serper(final_query, None)

                result = {
                    "product_name": vision_query,
                    "search_query": final_query,
                }

                # 🔥 Help mode
                current_shopping_mode = USER_PROFILES_SHOPPING.get(user_id, {}).get("shopping_mode", "buy")
                if current_shopping_mode == "help":
                    USER_PROFILES_SHOPPING.setdefault(user_id, {})["image_analysis"] = {
                        "product_name": vision_query,
                        "search_query": final_query,
                        "user_text": user_text
                    }
                    if user_text:
                        result["reply"] = None
                    else:
                        result["reply"] = f"Είδα **{vision_query}** 👀 Τι ακριβώς ψάχνεις;"
                else:
                    # Buy mode → αμέσως αποτελέσματα
                    if products:
                        result["direct_products"] = products
                        result["reply"] = f"Αναγνώρισα: **{vision_query}** 🎯 Βρήκα {len(products)} αποτελέσματα!"
                    else:
                        result["reply"] = f"Αναγνώρισα: **{vision_query}** 🎯"

            else:
                # Fallback σε OpenAI Vision αν το Lens απέτυχε
                print("⚠️ LENS FALLBACK TO OPENAI", flush=True)
                from shopping import ai_analyze_image_shopping
                result = ai_analyze_image_shopping(image_base64, user_text, client)

                current_shopping_mode = USER_PROFILES_SHOPPING.get(user_id, {}).get("shopping_mode", "buy")
                if current_shopping_mode == "help":
                    USER_PROFILES_SHOPPING.setdefault(user_id, {})["image_analysis"] = {
                        "product_name": result.get("product_name", ""),
                        "search_query": result.get("search_query", ""),
                        "user_text": user_text
                    }
                    if user_text:
                        result["reply"] = None
                    else:
                        result["reply"] = f"Είδα **{result.get('product_name', 'το προϊόν')}** 👀 Τι ακριβώς ψάχνεις;"

        else:
            # Services mode
            if vision_query:
                result = {
                    "profession": vision_query,
                    "problem": vision_data.get("labels", [""])[0] if vision_data else "",
                    "vision_labels": vision_data.get("labels", [])[:5] if vision_data else [],
                }
                current_services_mode = USER_PROFILES_SERVICES.get(user_id, {}).get("services_mode", "find")
                if current_services_mode == "help":
                    USER_PROFILES_SERVICES.setdefault(user_id, {})["image_analysis"] = {
                        "problem": result.get("problem", ""),
                        "profession": result.get("profession", ""),
                        "user_text": user_text
                    }
                    if user_text:
                        result["reply"] = None
                    else:
                        result["reply"] = f"Είδα τη φωτογραφία 👀 Πες μου λίγο περισσότερα για το πρόβλημα;"
            else:
                from services import ai_analyze_image_services
                result = ai_analyze_image_services(image_base64, user_text, client)
                current_services_mode = USER_PROFILES_SERVICES.get(user_id, {}).get("services_mode", "find")
                if current_services_mode == "help":
                    USER_PROFILES_SERVICES.setdefault(user_id, {})["image_analysis"] = {
                        "problem": result.get("problem", ""),
                        "profession": result.get("profession", ""),
                        "user_text": user_text
                    }
                    if user_text:
                        result["reply"] = None
                    else:
                        result["reply"] = f"Είδα τη φωτογραφία 👀 Πες μου λίγο περισσότερα για το πρόβλημα;"

        print("RAW AI RESULT:", result, flush=True)
        result["remaining"] = remaining
        return jsonify(result)

    except Exception as e:
        print("ANALYZE IMAGE ERROR:", e, flush=True)
        return jsonify({"error": "analysis_failed"}), 500

def handle_travel(data, client):

    history = data.get("history", [])

    user_text = ""

    if history:
        last = history[-1]

        if isinstance(last, dict):
            user_text = (
                last.get("content")
                or last.get("text")
                or last.get("message")
                or ""
            )
        elif isinstance(last, str):
            user_text = last
    user_text = user_text.lower().strip()        
    user_id = data.get("userId", "anonymous")
    username = data.get("userName", "")

    # ✅ FIX session reset
    if data.get("new_session"):
        USER_PROFILES_TRAVEL[user_id] = {}
        print("RESET TRAVEL PROFILE", flush=True)
    
    # 🔥 ΝΕΟ - Αν ήρθαμε από guide mode με destination
    initial_dest = data.get("initialDestination", "")
    print("INITIAL DEST RECEIVED:", repr(initial_dest), flush=True)  # 🔥 ΝΕΟ
    if initial_dest:
        USER_PROFILES_TRAVEL[user_id] = {}
        resolved = resolve_destination(initial_dest, client)
        dest_name = resolved.get("name") or initial_dest
        USER_PROFILES_TRAVEL[user_id]["destination"] = dest_name
        USER_PROFILES_TRAVEL[user_id]["destination_id"] = resolved.get("city_id")
        USER_PROFILES_TRAVEL[user_id]["mode"] = "hotel"
        print("🔥 INITIAL DESTINATION SET:", dest_name, flush=True)

    profile = USER_PROFILES_TRAVEL.setdefault(user_id, {})

    print("TRAVEL PROFILE BEFORE:", profile, flush=True)

    # 🔥 BULK EXTRACT - Εξάγει όλες τις πληροφορίες μαζί από κάθε μήνυμα
    if len(history) > 1 and not initial_dest:
        extracted = ai_extract_travel_intent(history, client)

        if not profile.get("destination") and extracted.get("destination"):
            profile["destination"] = extracted.get("destination")

        if not profile.get("checkin") and extracted.get("checkin"):
            profile["checkin"] = extracted.get("checkin")

        if not profile.get("checkout") and extracted.get("checkout"):
            profile["checkout"] = extracted.get("checkout")

        if profile.get("adults") is None and extracted.get("adults") is not None:
            profile["adults"] = extracted.get("adults")

        if profile.get("children") is None and extracted.get("children") is not None:
            profile["children"] = extracted.get("children")

        if not profile.get("children_ages") and extracted.get("children_ages"):
            profile["children_ages"] = extracted.get("children_ages")

        if not profile.get("amenities") and extracted.get("amenities"):
            profile["amenities"] = extracted.get("amenities")

        if not profile.get("budget_per_night") and extracted.get("budget_per_night"):
            profile["budget_per_night"] = extracted.get("budget_per_night")

        print("🔥 BULK EXTRACTED:", extracted, flush=True)
        print("🔥 PROFILE AFTER BULK:", profile, flush=True)



    # ============================
    # CONFIRM DESTINATION STEP
    # ============================

    if profile.get("awaiting_confirmation") and profile.get("mode") == "inspiration":
        text = user_text.lower()

        if text in ["ναι", "yes", "ok", "οκ", "nai"]:

            profile["destination"] = profile.get("suggested_destination")
            profile["mode"] = "hotel"

            profile.pop("awaiting_confirmation", None)
            profile.pop("suggested_destination", None)

        else:
            profile.pop("awaiting_confirmation", None)

    name = vocative_name(username)
    name = f" {name}" if name else ""

    if len(history) <= 1 and not initial_dest:
        return jsonify({
            "reply": f"""Γεια σου **{name.strip()}**. Πού θες να πας;

    - Εύρεση ξενοδοχείου & σύγκριση
    - Πρόταση προορισμού με AI
    - Πληροφορίες για ένα μέρος""",
            "links": [],
            "showButton": False
        })

    user_text = get_last_user_text(history).lower()
    text_clean = clean_text(user_text)
    
    children = profile.get("children")
    children_ages = profile.get("children_ages", [])
    adults = profile.get("adults")
    amenities = profile.get("amenities")

    number = None

    if re.fullmatch(r"\d+", text_clean):
        number = int(text_clean)
    else:
        for w, val in GREEK_NUMBERS.items():
            if w == text_clean:
                number = val
                break
    
    if number is not None:
        awaiting = profile.get("awaiting")

        if awaiting == "adults":
            profile["adults"] = number
            profile.pop("awaiting", None)

        elif awaiting == "children":
            profile["children"] = number
            profile.pop("awaiting", None)

        elif awaiting == "budget":
            profile["budget_per_night"] = number
            profile.pop("awaiting", None)

    # =========================
    # SMART PARSERS
    # =========================
    
    from travel import extract_destination

    if profile.get("awaiting") == "destination":

        cleaned = extract_destination(user_text)

        resolved = resolve_destination(cleaned, client)

        profile["destination"] = resolved.get("name")
        profile["destination_id"] = resolved.get("city_id")

        profile.pop("awaiting", None)

    if profile.get("awaiting") == "dates":
        ai_dates = ai_extract_travel_intent(history, client)
        if ai_dates.get("checkin") and ai_dates.get("checkout"):
            profile["checkin"] = ai_dates.get("checkin")
            profile["checkout"] = ai_dates.get("checkout")
            profile.pop("awaiting", None)

    if adults is None and profile.get("awaiting") == "adults":

        if any(x in text_clean for x in [
            # Ζευγάρι
            "εγω και η κοπελα μου", "εγω και η γυναικα μου",
            "εγω και ο αντρας μου", "εγω και ο φιλος μου",
            "εγω και η φιλη μου", "εγω και ο συντροφος μου",
            "εγω και η συντροφος μου", "με την κοπελα μου",
            "με τη γυναικα μου", "με τον αντρα μου",
            "couple", "for two", "δυο ατομα", "2 ατομα",
            "ζευγαρι", "ζευγάρι", "μαζι με", "μαζί με",
            "εμεις οι δυο", "εμείς οι δύο",
        ]):
            profile["adults"] = 2
            profile.pop("awaiting", None)

        elif any(x in text_clean for x in [
            "μονος", "μόνος", "μονη", "μόνη",
            "solo", "alone", "μονο εγω", "μόνο εγώ",
            "ενα ατομο", "1 ατομο", "εγω μονος",
        ]):
            profile["adults"] = 1
            profile.pop("awaiting", None)

        elif "παρεα" in text_clean:
            profile["adults"] = 2
            profile.pop("awaiting", None)

        nums = re.findall(r"\d+", text_clean)
        if nums:
            profile["adults"] = int(nums[0])
            profile.pop("awaiting", None)
        else:
            for w, val in GREEK_NUMBERS.items():
                if w in text_clean:
                    profile["adults"] = val
                    profile.pop("awaiting", None)

    if profile.get("awaiting") == "children":
        nums = re.findall(r"\d+", text_clean)
        if nums:
            profile["children"] = int(nums[0])
            profile.pop("awaiting", None)
        else:
            for w, val in GREEK_NUMBERS.items():
                if w in text_clean:
                    profile["children"] = val
                    profile.pop("awaiting", None)

    if profile.get("awaiting") == "children_ages":
        nums = re.findall(r"\d+", text_clean)
        if nums:
            profile["children_ages"] = [int(n) for n in nums]
            profile.pop("awaiting", None)
        else:
            words = text_clean.split()
            for w, val in GREEK_NUMBERS.items():
                if w in words:
                    profile["children_ages"] = [val]
                    profile.pop("awaiting", None)
                    break

    if profile.get("awaiting") == "children":

        if any(x in text_clean for x in ["χωρις παιδια","οχι","δεν εχω παιδια","no children"]):
            profile["children"] = 0
            profile["children_ages"] = []
            profile.pop("awaiting", None)

    if profile.get("awaiting") == "budget":
        nums = re.findall(r"\d+", text_clean)
        if nums:
            profile["budget_per_night"] = int(nums[0])
            profile.pop("awaiting", None)

    if profile.get("awaiting") == "amenities":

        if any(x in text_clean for x in ["οχι", "όχι", "no", "χωρις", "χωρίς", "δεν"]):
            profile["amenities"] = []
            profile.pop("awaiting", None)

            return handle_travel(data, client)

        elif any(x in text_clean for x in [
            "ολα", "όλα", "και τα 3", "και τα τρια",
            "τα παντα", "όλα τα amenities", "βαλε ολα",
            "yes all", "all", "ολες", "όλες"
        ]):
            profile["amenities"] = ["FREE_BREAKFAST", "WIFI", "POOL"]
            profile.pop("awaiting", None)

        else:
            selected = []

            if "πρωιν" in text_clean or "breakfast" in text_clean:
                selected.append("FREE_BREAKFAST")

            if "wifi" in text_clean:
                selected.append("WIFI")

            if "πισιν" in text_clean or "pool" in text_clean:
                selected.append("POOL")

            if selected:
                profile["amenities"] = list(set(selected))
                profile.pop("awaiting", None)


    # -----------------------------
    # BUTTON MODES
    # -----------------------------
    if user_text == "hotel_mode":
        profile["mode"] = "hotel"
        # 🔥 ΜΗΝ σβήνεις το destination αν έχει ήδη οριστεί
        if not profile.get("destination"):
            profile["destination"] = None
        profile["awaiting"] = None

        pass

    if user_text == "inspiration_mode":
        profile["mode"] = "inspiration"
        profile["already_suggested"] = []      # 🔥 reset λίστας προτάσεων
        profile["inspiration_query"] = ""      # 🔥 θα γεμίσει στο επόμενο μήνυμα
        return jsonify({
            "reply": "Πες μου τι έχεις στο μυαλό σου 😊 Θες κάτι κοντά; ρομαντικό; θάλασσα;",
            "links": [],
            "showButton": False
        })
    if user_text == "guide_mode":
        profile["mode"] = "guide"
        return jsonify({
            "reply": "Πες μου για ποιο μέρος θέλεις πληροφορίες 😊\n\nΠ.χ. 'Τζουμέρκα τι αξίζει να δω' ή 'Ναύπλιο που να φάω'",
            "links": [],
            "showButton": False
        })
    mode = profile.get("mode")

    # ===============================
    # ✅ CONFIRMATION HANDLER (ΒΑΛΤΟ ΕΔΩ)
    # ===============================
    if profile.get("awaiting_nearest"):
        if any(x in user_text for x in ["δειξε μου", "δείξε μου", "ναι", "yes"]):
            profile.pop("awaiting_nearest", None)
            failed = profile.get("failed_destination", "")
            
            # AI βρίσκει πλησιέστερη πόλη
            nearest_prompt = f"""
            The user was looking for hotels near: {failed}

            Find the nearest town or city to {failed} that has hotels.

            Rules:
            - Must be geographically CLOSE to {failed} (same region/area)
            - Must have hotels available
            - Do NOT suggest distant major cities unless {failed} is very remote
            - Prefer small/medium nearby towns over large distant cities

            Return ONLY the city name in English.
            """
            completion = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": nearest_prompt}],
                max_tokens=20
            )
            nearest_city = completion.choices[0].message.content.strip().lower()
            resolved = resolve_destination(nearest_city, client)
            
            profile["suggested_destination"] = resolved.get("name")
            profile["destination_id"] = resolved.get("city_id")
            profile["awaiting_confirmation"] = True
            
            return jsonify({
                "reply": f"Το πλησιέστερο μέρος με ξενοδοχεία είναι **{resolved.get('name').title()}** 😊\n\nΘες να σου δείξω τα καλύτερα ξενοδοχεία εκεί; Γράψε 'ναι' 😉",
                "links": [],
                "showButton": False
            })

    if profile.get("awaiting_confirmation"):

        text = user_text.lower()

        if any(x in text for x in ["ναι", "yes", "ok", "οκ", "nai"]):

            # ✅ Ο χρήστης αποδέχτηκε → πάμε hotel flow
            raw_dest = profile.get("suggested_destination")
            resolved = resolve_destination(raw_dest, client)
            profile["destination"] = resolved.get("name")
            profile["destination_id"] = resolved.get("city_id")
            profile["mode"] = "hotel"
            profile.pop("awaiting_confirmation", None)

        else:
            # ❌ Ο χρήστης απέρριψε → προτείνουμε ΑΛΛΟ μέρος με τις ΙΔΙΕΣ προτιμήσεις
            profile.pop("awaiting_confirmation", None)
            profile.pop("suggested_destination", None)

            # Κρατάμε τη λίστα με τα ήδη προτεινόμενα
            already_suggested = profile.get("already_suggested", [])

            # Χρησιμοποιούμε το αρχικό αίτημα του χρήστη
            inspiration_query = profile.get("inspiration_query", user_text)

            # 🔥 Ζητάμε νέα πρόταση με διαφορετικό μέρος
            reply = travel_ai_advisor(inspiration_query, client, context, already_suggested, conversation=history)

            # Εξάγουμε το νέο προτεινόμενο μέρος
            match = re.search(r"👉\s*(.+)", reply)
            if match:
                suggested = match.group(1).strip()
                already_suggested.append(suggested)
                profile["already_suggested"] = already_suggested
                profile["suggested_destination"] = suggested
                profile["awaiting_confirmation"] = True

            return jsonify({
                "reply": reply,
                "links": [],
                "showButton": False
            })

    # 🔥 ΑΝ ΕΙΝΑΙ HOTEL → ΠΑΝΤΑ FLOW (ΚΟΒΕΙ ΤΟ AI)
    if mode == "hotel":
        pass

    # 🔥 INSPIRATION MODE
    elif mode == "inspiration":

        # ============================
        # ΕΛΕΓΧΟΣ ΑΝ ΑΛΛΑΖΕΙ ΤΟΠΟΘΕΣΙΑ
        # ============================
        new_location_prompt = f"""
Το προηγούμενο αίτημα του χρήστη ήταν: "{profile.get('inspiration_query', '')}"
Το νέο μήνυμα είναι: "{user_text}"

Αναφέρει ο χρήστης ΝΕΙΑ συγκεκριμένη τοποθεσία/περιοχή/χώρα που διαφέρει από το προηγούμενο;

Παραδείγματα που είναι ΝΕΑ τοποθεσία:
- "πες μου για Ιταλία" → YES
- "θέλω κάτι στην Εύβοια" → YES
- "βρες μου στην Κρήτη" → YES

Παραδείγματα που ΔΕΝ είναι νέα τοποθεσία:
- "πες μου άλλο" → NO
- "προτεινέ μου κάτι άλλο" → NO
- "δεν μου αρέσει αυτό" → NO
- "κάτι πιο ρομαντικό" → NO

Απάντησε ΜΟΝΟ YES ή NO.
"""
        check = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": new_location_prompt}],
            temperature=0,
            max_tokens=5
        )
        has_new_location = "YES" in check.choices[0].message.content.strip().upper()

        if has_new_location:
            # 🔥 Νέα τοποθεσία → ανανέωσε inspiration_query και καθάρισε already_suggested
            profile["inspiration_query"] = user_text
            profile["already_suggested"] = []
            print("🔥 NEW LOCATION DETECTED - Reset inspiration_query:", user_text, flush=True)
        else:
            # 🔥 Ίδια τοποθεσία → κράτα το αρχικό αίτημα
            if not profile.get("inspiration_query"):
                profile["inspiration_query"] = user_text
            print("🔥 SAME LOCATION - Using:", profile.get("inspiration_query"), flush=True)

        inspiration_query = profile.get("inspiration_query", user_text)
        already_suggested = profile.get("already_suggested", [])
        context = profile.get("destination") or profile.get("suggested_destination")

        # 🔥 Περνάμε το αρχικό αίτημα στο AI ώστε να παραμένει στην ίδια περιοχή
        reply = travel_ai_advisor(inspiration_query, client, context, already_suggested)

        match = re.search(r"👉\s*(.+)", reply)

        if match:
            suggested = match.group(1).strip()

            # 🔥 ΕΛΕΓΧΟΣ ΑΝ ΥΠΑΡΧΕΙ ΣΤΟ CITY INDEX
            test_resolve = resolve_destination(suggested, client)

            if not test_resolve.get("city_id"):
                profile["awaiting_nearest"] = True
                profile["failed_destination"] = suggested

                return jsonify({
                    "reply": reply + f"\n\n⚠️ Δυστυχώς δεν μπορώ να βρω ξενοδοχεία στο **{suggested}** 😕\n\nΘέλεις να δω το πλησιέστερο μέρος με ξενοδοχεία; Γράψε 'δείξε μου'",
                    "links": [],
                    "showButton": False
                })

            # Βρέθηκε → κανονική ροή
            already_suggested.append(suggested)
            profile["already_suggested"] = already_suggested
            profile["suggested_destination"] = suggested
            profile["awaiting_confirmation"] = True

            return jsonify({
                "reply": reply,
                "links": [],
                "showButton": False
            })
            
    elif mode == "guide":
    

        # 🔥 ΠΡΩΤΑ έλεγξε αν περιμένουμε "ναι" για hotel switch
        if profile.get("awaiting_hotel_switch"):
            if any(x in user_text for x in ["ναι", "yes", "nai", "ok", "ναί"]):
                destination = profile.get("switch_destination", "")
                profile.clear()
                profile["mode"] = "hotel"
                profile["destination"] = destination
                profile["awaiting"] = "dates"
                return jsonify({
                    "reply": "",
                    "links": [],
                    "showButton": False,
                    "switchToTravel": True,
                    "suggestedDestination": destination
                })

        # 🔥 Εξάγουμε το μέρος από το μήνυμα αν δεν το έχουμε ήδη
        if True:
            location_prompt = f"""
    Διάβασε αυτό το μήνυμα:
    "{user_text}"

    Αν αναφέρεται σε συγκεκριμένο μέρος/πόλη/περιοχή, επέστρεψε ΜΟΝΟ το όνομα του μέρους.
    Αν δεν αναφέρεται μέρος, επέστρεψε "NONE".

    Παραδείγματα:
    "αξιοθέατα στα Μετέωρα" → Μετέωρα
    "που να φάω στη Ρόδο" → Ρόδος
    "τι να κάνω εκεί" → NONE
    """
            loc_response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": location_prompt}],
                max_tokens=20,
                temperature=0
            )
            location = loc_response.choices[0].message.content.strip()
            
            if location != "NONE":
                profile["guide_location"] = location
                print("GUIDE LOCATION SET:", location, flush=True)

        # Μετά συνέχισε κανονικά
        guide_location = profile.get("guide_location", "")
        location = profile.get("guide_location", "")

        reply = travel_guide_ai(user_text, client, location, conversation=history)

        # 🔥 Detect hotel intent
        if "HOTEL_INTENT: true" in reply:
            reply = reply.replace("HOTEL_INTENT: true", "").strip()
            reply = reply.replace("HOTEL_INTENT: false", "").strip()  # 🔥 ΝΕΟ
            location = profile.get("guide_location", "")
            profile["awaiting_hotel_switch"] = True
            profile["switch_destination"] = location
            return jsonify({
                "reply": reply + f"\n\nΘες να σε πάω στην καρτέλα **Βρες Ξενοδοχείο** για {location}; Γράψε 'ναι' 😊",
                "links": [],
                "showButton": False,
                "awaitingHotelSwitch": True,
                "suggestedDestination": location
            })

        reply = reply.replace("HOTEL_INTENT: true", "").strip()
        reply = reply.replace("HOTEL_INTENT: false", "").strip()

        return jsonify({
            "reply": reply,
            "links": [],
            "showButton": False
        })
    # =========================
    # BUILD VALUES
    # =========================

    destination = profile.get("destination")
    destination_id = profile.get("destination_id")
    checkin = profile.get("checkin")
    checkout = profile.get("checkout")
    adults = profile.get("adults")
    children = profile.get("children")
    budget = profile.get("budget_per_night")
    amenities = profile.get("amenities")
    children_ages = profile.get("children_ages", [])


    # -----------------------------------------
    # FULL COMPLETENESS CHECK (ALL FIELDS)
    # -----------------------------------------

    required_fields = [
        "destination",
        "checkin",
        "checkout",
        "adults",
        "children",
        "budget_per_night",
        "amenities"
    ]

    missing = []

    for f in required_fields:
        value = profile.get(f)

        # 👉 SPECIAL FIX για children
        if f == "children":
            if value is None:
                missing.append(f)

        # 🔥 SPECIAL FIX για amenities
        elif f == "amenities":
            if value is None:
                missing.append(f)

        else:
            if not value:
                missing.append(f)

    # 👉 children ages special case
    if (profile.get("children") or 0) > 0 and not profile.get("children_ages"):
        missing.append("children_ages")

    print("TRAVEL MISSING:", missing, flush=True)

    # ✅ ΑΝ ΕΙΝΑΙ ΠΛΗΡΕΣ → ΑΜΕΣΑ LINKS
    if not missing:
        destination = profile.get('destination', '')
        if isinstance(destination, dict):
            destination = destination.get('name', '') or destination.get('city', '') or str(destination)
        profile['destination'] = destination
        expedia_url = build_expedia_search_url(
            destination=profile.get("destination"),
            checkin=profile.get("checkin"),
            checkout=profile.get("checkout"),
            adults=profile.get("adults"),
            children_ages=profile.get("children_ages", []),
            rooms=1,
            amenities=profile.get("amenities"),
            budget_total=profile.get("budget_per_night")
        )
        agoda_url = build_agoda_search_url(
            destination=profile.get("destination"),
            destination_id=profile.get("destination_id"),
            checkin=profile.get("checkin"),
            checkout=profile.get("checkout"),
            adults=profile.get("adults"),
            children=profile.get("children", 0),
            children_ages=profile.get("children_ages", []),
            rooms=1,
            amenities=profile.get("amenities"),
            budget=profile.get("budget_per_night")
        )
        links = [
            {"title": f"✈️ Expedia — {destination.title()}", "url": expedia_url},
            {"title": f"🏨 Agoda — {destination.title()}", "url": agoda_url},
        ]
        # 🔥 Αποθήκευσε στο ιστορικό
        save_search_history(db, user_id, f"Ξενοδοχεία {destination}", "travel")
        return jsonify({
            "reply": "Τέλεια 👌 Βρήκα τις καλύτερες επιλογές για σένα!",
            "links": links,
            "showButton": False
        })

    if missing:

        if "destination" in missing:
            profile["awaiting"] = "destination"
            return jsonify({"reply": f"Γράψε μου{name} ελεύθερα - προορισμό, ημερομηνίες, άτομα, budget. Όσα ξέρεις και θα σε βοηθήσω εγώ ώστε να βρούμε την καλύτερη επιλογή!","links": [],"showButton": False})

        if "checkin" in missing or "checkout" in missing:
            profile["awaiting"] = "dates"
            return jsonify({"reply": "Ποιες ημερομηνίες σκέφτεσαι για το ταξίδι σου;","links": [],"showButton": False})

        if "adults" in missing:
            profile["awaiting"] = "adults"
            return jsonify({"reply": "Για πόσoυς ενήλικες θα έιναι η κράτηση στο ξενοδοχείο;","links": [],"showButton": False})

        if "children" in missing:
            profile["awaiting"] = "children"
            return jsonify({"reply": "Για το ταξίδι που σκέφτεσαι θα υπάρχουν και παιδιά; Αν ναι πες μου σε παρακαλώ πόσα;","links": [],"showButton": False})

        if "children_ages" in missing:
            profile["awaiting"] = "children_ages"

            if children == 1:
                question = "Τι ηλικία έχει το παιδί;"
            else:
                question = "Τι ηλικίες έχουν τα παιδιά;"

            return jsonify({
                "reply": question,
                "links": [],
                "showButton": False
            })

        if "budget_per_night" in missing:
            profile["awaiting"] = "budget"
            return jsonify({"reply": f"{name} Τι budget ανα βράδυ έχεις περίπου στο μυαλό σου;","links": [],"showButton": False})

        if "amenities" in missing:
            profile["awaiting"] = "amenities"
            return jsonify({"reply": "Θέλεις κάποιες συγκεκριμένες παροχές όπως πρωινό, wifi ή πισίνα;","links": [],"showButton": False})

    profile.pop("awaiting", None)
    USER_PROFILES_TRAVEL[user_id] = profile

    print("TRAVEL PROFILE AFTER:", profile, flush=True)
    print("FINAL RETURN -> SHOW BUTTON TRUE", flush=True)

    destination = profile.get('destination', '')
    if isinstance(destination, dict):
        destination = destination.get('name', '') or destination.get('city', '') or str(destination)
    profile['destination'] = destination
    expedia_url = build_expedia_search_url(
        destination=profile.get("destination"),
        checkin=profile.get("checkin"),
        checkout=profile.get("checkout"),
        adults=profile.get("adults"),
        children_ages=profile.get("children_ages", []),
        rooms=1,
        amenities=profile.get("amenities"),
        budget_total=profile.get("budget_per_night")
    )
    agoda_url = build_agoda_search_url(
        destination=profile.get("destination"),
        destination_id=profile.get("destination_id"),
        checkin=profile.get("checkin"),
        checkout=profile.get("checkout"),
        adults=profile.get("adults"),
        children=profile.get("children", 0),
        children_ages=profile.get("children_ages", []),
        rooms=1,
        amenities=profile.get("amenities"),
        budget=profile.get("budget_per_night")
    )
    links = [
        {"title": f"✈️ Expedia — {destination.title()}", "url": expedia_url},
        {"title": f"🏨 Agoda — {destination.title()}", "url": agoda_url},
    ]
    # 🔥 Αποθήκευσε στο ιστορικό
    save_search_history(db, user_id, f"Ξενοδοχεία {destination}", "travel")
    return jsonify({
        "reply": "Τέλεια 👌 Βρήκα τις καλύτερες επιλογές για σένα!",
        "links": links,
        "showButton": False
    })

# =====================================================
# ΠΡΟΣΘΕΣΕ ΑΥΤΟ ΣΤΟ server.py
# Βάλτο ΠΡΙΝ το @app.route("/chat") (γύρω στη γραμμή 685)
# =====================================================

def handle_shopping(data, client):

    history = data.get("history", [])
    user_id = data.get("userId", "anonymous")
    username = data.get("userName", "")

    # Reset session
    if data.get("new_session") or len(history) <= 1:
        USER_PROFILES_SHOPPING[user_id] = {}
        print("RESET SHOPPING PROFILE", flush=True)

    profile = USER_PROFILES_SHOPPING.setdefault(user_id, {})

    name = vocative_name(username)
    name = f" {name}" if name else ""

    user_text = get_last_user_text(history).lower()

    # ============================
    # WELCOME
    # ============================

    if len(history) <= 1:
        return jsonify({
            "reply": f"""Γεια σου **{name.strip()}**. Τι ψάχνεις σήμερα;

            - Αγορά συγκεκριμένου προϊόντος
            - Σύγκριση τιμών & επιλογών με AI""",
            "links": [],
            "showButton": False
        })

    # ============================
    # SHOPPING MODE BUTTONS
    # ============================
    if user_text == "θέλω να αγοράσω":
        profile["shopping_mode"] = "buy"
        return jsonify({
            "reply": f"Τέλεια{name}! Τι θέλεις να αγοράσεις; Γράψε μου το προϊόν που ψάχνεις 🛒",
            "links": [],
            "showButton": False
        })

    if user_text == "χρειάζομαι βοήθεια":
        profile["shopping_mode"] = "help"
        return jsonify({
            "reply": f"Με χαρά{name}! Πες μου λίγα λόγια για το τι χρειάζεσαι και θα σε βοηθήσω να βρεις το κατάλληλο προϊόν 😊\n\nΠ.χ. 'Θέλω laptop για φοιτητή με budget 600€'",
            "links": [],
            "showButton": False
        })

    # ============================
    # INTENT DETECTION
    # ============================
    # 🔥 Αν έχει image_analysis → συνδύασε με το history
    image_analysis = profile.get("image_analysis", {})
    if image_analysis:
        base_product = image_analysis.get("product_name") or image_analysis.get("search_query", "")
        user_modification = image_analysis.get("user_text", "")
        
        enhanced_history = [{
            "role": "system",
            "content": f"Ο χρήστης έστειλε φωτογραφία προϊόντος: {base_product}."
        }] + list(history) + ([{
            "role": "user",
            "content": user_modification
        }] if user_modification else [])
        
        intent = ai_extract_search_intent(enhanced_history, client)
    else:
        intent = ai_extract_search_intent(history, client)
    intent_type = intent.get("intent_type", "product_search")
    shopping_mode = profile.get("shopping_mode", "buy")

    print("SHOPPING INTENT:", intent_type, "MODE:", shopping_mode, flush=True)

    # ============================
    # KNOWLEDGE QUESTION
    # ============================
    if intent_type == "knowledge_question":
        web_info = web_search_context(full_conversation(history))

        prompt = f"""
Ο χρήστης κάνει ερώτηση γνώσης για προϊόν.

Συνομιλία:
{full_conversation(history)}

Web πληροφορίες:
{web_info}

Κανόνες:
- Πες το πιο πρόσφατο μοντέλο
- ΜΗΝ μαντεύεις
- Απάντα σύντομα στα ελληνικά
"""
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": prompt}],
            temperature=0.3
        )
        return jsonify({
            "reply": clean_reply(completion.choices[0].message.content.strip()),
            "links": [],
            "showButton": False
        })

    # ============================
    # HELP MODE → AI βοηθάει να αποφασίσει
    # ============================
    if shopping_mode == "help":

        # 🔥 RESET αν ο χρήστης αλλάζει κάτι
        if profile.get("help_ready") and not any(x in user_text for x in ["ναι", "yes", "nai", "ok", "οκ", "ναί"]):
            profile.pop("help_ready", None)
            profile.pop("search_query", None)
            print("🔥 HELP RESET - user changed preference", flush=True)

        # 🔥 Image context - ΠΑΝΤΑ εδω, οχι μεσα στο if
        image_context = ""
        if profile.get("image_analysis"):
            img = profile["image_analysis"]
            image_context = f"""
Ο χρήστης έστειλε φωτογραφία.
Το AI αναγνώρισε: {img.get('product_name', '')}
Ο χρήστης έγραψε: {img.get('user_text', '')}
"""

        # ΕΛΕΓΧΟΣ ΑΝ ΕΧΕΙ ΑΡΚΕΤΕΣ ΠΛΗΡΟΦΟΡΙΕΣ
        check_prompt = f"""
Διάβασε αυτή τη συνομιλία:
{full_conversation(history)}

{image_context}

Έχεις αρκετές πληροφορίες για να ψάξεις το προϊόν;

ΚΑΝΟΝΕΣ:
- Αν ο χρήστης ζητά ΣΥΓΚΕΚΡΙΜΕΝΟ προϊόν (π.χ. "Delta Vitaline Pudding σε γεύση κακάο", "Nike Air Max 90 λευκό") → YES αμέσως, δεν χρειάζεται budget
- Αν ζητά ΚΑΤΗΓΟΡΙΑ προϊόντος (π.χ. "θέλω ψυγείο", "θέλω laptop", "θέλω κουρτίνες") → χρειάζεται budget → NO
- Αν έχει budget (έστω "ό,τι βρεθεί") ΚΑΙ 1 χαρακτηριστικό → YES
- Αν είναι ασαφές τι ακριβώς θέλει → NO

Απάντησε ΜΟΝΟ YES ή NO.
"""
        check = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": check_prompt}],
            temperature=0
        )
        has_enough = "YES" in check.choices[0].message.content.strip().upper()

        # ============================
        # ΑΝ ΔΕΝ ΕΧΕΙ ΑΡΚΕΤΑ → ΡΩΤΑ
        # ============================
        if not has_enough:
            question_prompt = f"""
Είσαι expert σύμβουλος αγορών.

Συνομιλία μέχρι τώρα:
{full_conversation(history)}
{image_context}

Χρειάζεσαι να μάθεις:Τι είδος προϊόντος (αν δεν ξέρεις ακόμα)Budget (αν πει "δεν ξέρω" → ΟΚ, πήγαινε παρακάτω)Ένα χαρακτηριστικό (χρώμα, μέγεθος, χρήση κτλ)
ΚΑΝΟΝΕΣ:
- Κάνε ΜΟΝΟ 1 ερώτηση για αυτό που λείπει
- ΜΗΝ ξαναρωτάς αυτό που έχει ήδη απαντηθεί
- Αν έχει πει "δεν ξέρω" για budget → ΟΚ, ρώτα άλλο
- Μίλα φυσικά σαν φίλος
- Απάντα στα ελληνικά
"""
            question = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "system", "content": question_prompt}],
                temperature=0.4
            )
            return jsonify({
                "reply": clean_reply(question.choices[0].message.content.strip()),
                "links": [],
                "showButton": False
            })

        # ============================
        # ΕΧΕΙ ΑΡΚΕΤΑ → AI ΠΡΟΤΕΙΝΕΙ ΣΥΓΚΕΚΡΙΜΕΝΟ ΠΡΟΪΟΝ
        # ============================

        if not profile.get("help_ready"):

            query = intent.get("search_keywords_gr") or intent.get("search_keywords_en") or user_text
            max_price = intent.get("budget_max")
            profile["budget_max"] = max_price

            from shopping import search_products_serper

            # 🔥 ΒΗΜΑ 1: AI αποφασίζει το σωστό query
            query_prompt = f"""
Ο χρήστης θέλει:
{full_conversation(history)}

{image_context}

Το αρχικό query είναι: {query}

Φτιάξε το καλύτερο search query:
- Αν είναι παλιό μοντέλο → βάλε το σύγχρονο αντίστοιχο
- Αν ζητά γεύση/χρώμα/παραλλαγή → συμπεριέλαβέ το
- Επέστρεψε ΜΟΝΟ το query, μέγιστο 6 λέξεις
"""
            q_completion = client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": query_prompt}],
                temperature=0
            )
            smart_query = q_completion.choices[0].message.content.strip()
            print(f"SMART QUERY: {smart_query}", flush=True)
            profile["search_query"] = smart_query

            # 🔥 ΒΗΜΑ 2: Serper με το smart query
            serper_results = search_products_serper(smart_query, max_price)

            # 🔥 ΒΗΜΑ 3: AI απαντά βάσει αποτελεσμάτων
            product_prompt = f"""
Είσαι ένας εξαιρετικά έξυπνος expert σύμβουλος αγορών στην Ελλάδα με βαθιά γνώση προϊόντων.

{image_context}

Ο χρήστης θέλει:
{full_conversation(history)}

Αποτελέσματα από αναζήτηση:
{serper_results}

ΚΑΝΟΝΕΣ ΣΚΕΨΗΣ:
- Αν ο χρήστης ζητά παλιό μοντέλο → αναγνώρισέ το και πρότεινε το σύγχρονο αντίστοιχο
- Αν ζητά συγκεκριμένη γεύση/χρώμα/παραλλαγή → ψάξε ΑΥΤΟ ακριβώς
- Αν το budget δεν επαρκεί → πες το ειλικρινά και πρότεινε εναλλακτική
- Αν βρήκες καλές επιλογές → πες ΜΟΝΟ "Βρήκα επιλογές που ταιριάζουν 👌 Να στις δείξω;"
- ΜΗΝ αναφέρεις ονόματα προϊόντων ή τιμές
- Μίλα φυσικά, ζεστά, σαν έξυπνος φίλος που ξέρει πολύ καλά την αγορά
- Αν δεν βρήκες → πες το ειλικρινά και πρότεινε εναλλακτική
"""
            completion = client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "system", "content": product_prompt}],
                temperature=0.4
            )

            reply = completion.choices[0].message.content.strip()

            if serper_results:
                profile["help_ready"] = True
                return jsonify({
                    "reply": reply,
                    "links": [],
                    "showButton": False
                })
            else:
                return jsonify({
                    "reply": reply,
                    "links": [],
                    "showButton": False
                })
        # ============================
        # ΧΡΗΣΤΗΣ ΕΓΡΑΨΕ "ΝΑΙ" → FLOATING
        # ============================
        if any(x in user_text for x in ["ναι", "yes", "nai", "ok", "οκ", "ναί"]):
            profile.pop("help_ready", None)
            query = profile.get("search_query", "")
            encoded = urllib.parse.quote(query)
            links = [
                {"title": "🔍 Αποτελέσματα στο Skroutz", "url": f"https://www.skroutz.gr/search?keyphrase={encoded}"},
                {"title": "🔍 Αποτελέσματα στο BestPrice", "url": f"https://www.bestprice.gr/search?q={encoded}"},
            ]
            return jsonify({
                "reply": "Τέλεια! Δες τις καλύτερες επιλογές:",
                "links": links,
                "showButton": False
            })

        # Αν πει κάτι άλλο → συνεχίζει
        question_prompt = f"""
Ο χρήστης είπε κάτι μετά την πρότασή σου:
{full_conversation(history)}

Απάντα φυσικά και ρώτα αν θέλει να δει τιμές.
Απάντα στα ελληνικά.
"""
        question = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": question_prompt}],
            temperature=0.4
        )
        return jsonify({
            "reply": clean_reply(question.choices[0].message.content.strip()),
            "links": [],
            "showButton": False
        })
    # ============================
    # BUY MODE → ξέρει τι θέλει
    # ============================
    if shopping_mode == "buy" or intent_type == "product_search":

        query = intent.get("search_keywords_gr") or intent.get("search_keywords_en") or user_text

        if not query or query == "θέλω να αγοράσω":
            return jsonify({
                "reply": "Τι θέλεις να αγοράσεις;",
                "links": [],
                "showButton": False
            })

        profile["search_query"] = query
        max_price = intent.get("budget_max")
        encoded = urllib.parse.quote(query)

        from shopping import search_products_serper
        products = search_products_serper(query, max_price)

        if products:
            links = []
            for p in products:
                links.append({
                    "title": p.get("title", ""),
                    "url": p.get("link", ""),
                    "image": p.get("imageUrl", ""),
                    "price": p.get("price", ""),
                    "source": p.get("source", ""),
                })
            # 🔥 Αποθήκευσε στο ιστορικό
            save_search_history(db, user_id, query, "shopping")
            return jsonify({
                "reply": "Βρήκα αυτό που ψάχνεις! 👇",
                "links": links,
                "showButton": False
            })
        else:
            # Fallback → Skroutz + Google Shopping
            links = [
                {"title": f"🔍 Skroutz — {query}", "url": f"https://www.skroutz.gr/search?keyphrase={encoded}"},
                {"title": f"🔍 Google Shopping — {query}", "url": f"https://www.google.com/search?q={encoded}&tbm=shop"},
            ]
            # 🔥 Αποθήκευσε στο ιστορικό
            save_search_history(db, user_id, query, "shopping")
            return jsonify({
                "reply": "Δες τις καλύτερες τιμές 👇",
                "links": links,
                "showButton": False
            })

USER_PROFILES_SERVICES = {}

def handle_services(data, client):

    history = data.get("history", [])
    user_id = data.get("userId", "anonymous")
    username = data.get("userName", "")

    # Reset session
    if data.get("new_session") or len(history) <= 1:
        USER_PROFILES_SERVICES[user_id] = {}
        print("RESET SERVICES PROFILE", flush=True)

    profile = USER_PROFILES_SERVICES.setdefault(user_id, {})

    name = vocative_name(username)
    name = f" {name}" if name else ""

    user_text = get_last_user_text(history).lower()

    # ============================
    # WELCOME
    # ============================
    if len(history) <= 1:
        return jsonify({
            "reply": f"""Γεια σου **{name.strip()}**. Τι χρειάζεσαι σήμερα;

            - Ηλεκτρολόγος, υδραυλικός, γιατρός…
            - Δεν ξέρεις ποιον χρειάζεσαι;""",
            "links": [],
            "showButton": False
        })

    # ============================
    # MODE BUTTONS
    # ============================
    if user_text == "find_professional":
        profile["services_mode"] = "find"
        return jsonify({
            "reply": f"Τέλεια{name}! Πες μου τι επαγγελματία ψάχνεις και σε ποια περιοχή 😊\n\nΠ.χ. 'Ηλεκτρολόγο στο Χαλάνδρι' ή 'Παιδίατρο στη Θεσσαλονίκη'",
            "links": [],
            "showButton": False
        })

    if user_text == "help_professional":
        profile["services_mode"] = "help"
        return jsonify({
            "reply": f"Κανένα πρόβλημα{name}! Περίγραψέ μου το πρόβλημά σου και θα καταλάβω ποιον επαγγελματία χρειάζεσαι 🔍\n\nΠ.χ. 'Έχει χαλάσει η αντλία του νερού μου' ή 'Χρειάζομαι βοήθεια με την ηλεκτρική εγκατάσταση'",
            "links": [],
            "showButton": False
        })

    services_mode = profile.get("services_mode", "find")

    # ============================
    # HELP MODE → AI καταλαβαίνει το πρόβλημα
    # ============================

    if services_mode == "help":
        if not profile.get("profession"):

            # 🔥 Image context αν υπάρχει
            image_context = ""
            if profile.get("image_analysis"):
                img = profile["image_analysis"]
                image_context = f"""
Ο χρήστης έστειλε φωτογραφία.
Πρόβλημα που αναγνωρίστηκε: {img.get('problem', '')}
Πιθανός επαγγελματίας: {img.get('profession', '')}
Ο χρήστης έγραψε: {img.get('user_text', '')}
"""

            conversation_text = full_conversation(history)

            # 1️⃣ Clarification check
            clarification = ai_needs_clarification(conversation_text, client)
            if clarification.get("needs_clarification"):
                return jsonify({
                    "reply": clarification.get("question"),
                    "links": [],
                    "showButton": False
                })

            # 2️⃣ Detect profession με τα παραδείγματα
            profession = ai_detect_profession_from_problem(conversation_text, client)

            if profession:
                profile["profession"] = profession
                profile["services_mode"] = "find"
                return jsonify({
                    "reply": f"Κατάλαβα! Χρειάζεσαι **{profession}** 😊\n\nΣε ποια περιοχή είσαι;",
                    "links": [],
                    "showButton": False
                })

            # 3️⃣ Αν δεν βρήκε → συνέχισε conversation με history
            profession_prompt = f"""
    Είσαι ένας έξυπνος βοηθός που βρίσκει τον κατάλληλο επαγγελματία με βάση το πρόβλημα του χρήστη.

    {image_context}

    Ολόκληρη η συνομιλία μέχρι τώρα:
    {conversation_text}

    ΣΤΟΧΟΣ:
    Να βρεις τον κατάλληλο επαγγελματία το συντομότερο δυνατό, με το λιγότερο back-and-forth.

    ΚΑΝΟΝΕΣ ΑΞΙΟΛΟΓΗΣΗΣ:
    - Διάβασε ΟΛΗ τη συνομιλία — κάθε μήνυμα δίνει πληροφορίες
    - Αν το πρόβλημα ανήκει σαφώς σε κατηγορία → βρες αμέσως επαγγελματία
    - Μην περιμένεις τέλεια περιγραφή — εκτίμησε βάσει αυτών που έχεις

    ΚΑΝΟΝΕΣ ΓΙΑ ΕΡΩΤΗΣΕΙΣ:
    - ΜΗΝ ρωτάς τεχνικές ερωτήσεις που ο χρήστης δεν μπορεί να ξέρει
    (π.χ. "είναι μπαταρία ή κινητήρας;", "είναι ηλεκτρολογικό ή μηχανολογικό;")
    - ΜΗΝ ρωτάς αυτό που ήδη ξέρεις από τη συνομιλία
    - Ρώτα ΜΟΝΟ αν δεν μπορείς να αποφασίσεις σε ποια κατηγορία ανήκει
    - Μέγιστο 1 ερώτηση — σύντομη και απλή στα ελληνικά

    ΠΑΡΑΔΕΙΓΜΑΤΑ ΑΠΟΦΑΣΕΩΝ:
    - "δεν παίρνει μπροστά" / "γυρνάω κλειδί" → PROFESSION: Μηχανικός Αυτοκινήτων
    - "έχω θέμα με το αμάξι" + "δεν παίρνει μπροστά" → PROFESSION: Μηχανικός Αυτοκινήτων
    - "βούλωσε η τουαλέτα" → PROFESSION: Υδραυλικός
    - "δεν ανάβουν τα φώτα" → PROFESSION: Ηλεκτρολόγος
    - "έχω θέμα" (μόνο αυτό) → ρώτα τι ακριβώς
    - "έχω πρόβλημα στο σπίτι" → ρώτα σε ποιον χώρο / τι είδους

    ΑΠΑΝΤΗΣΗ:
    - Αν βρεις επαγγελματία → απάντησε ΜΟΝΟ: PROFESSION: [επάγγελμα στα ελληνικά]
    - Αν χρειάζεσαι μία ερώτηση → απάντησε φυσικά στα ελληνικά σαν φίλος, ΜΗΝ βάλεις PROFESSION
    - ΜΗΝ εξηγείς τη σκέψη σου, ΜΗΝ δίνεις λίστες, ΜΗΝ λες "καταλαβαίνω ότι..."
    """
            prof_response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": profession_prompt}],
                max_tokens=100,
                temperature=0.3
            )
            result = prof_response.choices[0].message.content.strip()

            if "PROFESSION:" in result:
                profession = result.split("PROFESSION:")[-1].strip()
                profile["profession"] = profession
                profile["services_mode"] = "find"
                return jsonify({
                    "reply": f"Κατάλαβα! Χρειάζεσαι **{profession}** 😊\n\nΣε ποια περιοχή είσαι;",
                    "links": [],
                    "showButton": False
                })
            else:
                return jsonify({
                    "reply": result,
                    "links": [],
                    "showButton": False
                })
    # ============================
    # FIND MODE → Ψάχνει επαγγελματία
    # ============================
    if services_mode == "find":

        # 🔥 FIX: Αν ο χρήστης πατά "Θέλω επαγγελματία" → reset profession/location
        if user_text in ["find_professional", "θέλω επαγγελματία", "θελω επαγγελματια"]:
            profile.pop("profession", None)
            profile.pop("location", None)

       # 🔥 Χρησιμοποιούμε απευθείας το user_text + profile
        extract_prompt = f"""
Εξάγαγε επάγγελμα και περιοχή από αυτό το μήνυμα:
"{user_text}"

Αποθηκευμένο επάγγελμα από πριν: "{profile.get('profession', '')}"

ΚΑΝΟΝΕΣ:
- Μετέτρεψε κλητική/αιτιατική → ονομαστική ("ηλεκτρολόγο" → "Ηλεκτρολόγος")
- Αφαίρεσε "στο/στη/στην/στον" από περιοχή
- Αν υπάρχει αποθηκευμένο επάγγελμα και δεν αναφέρεται νέο → χρησιμοποίησέ το

Παραδείγματα:
- "θελω ηλεκτρολόγο στο χαλάνδρι" → {{"profession": "Ηλεκτρολόγος", "location": "Χαλάνδρι"}}
- "χαλάνδρι" (μόνο περιοχή) → {{"profession": null, "location": "Χαλάνδρι"}}
- "ηλεκτρολόγο" (μόνο επάγγελμα) → {{"profession": "Ηλεκτρολόγος", "location": null}}

Απάντησε ΜΟΝΟ JSON:
{{"profession": "επάγγελμα ή null", "location": "περιοχή ή null"}}
"""
        extract_response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": extract_prompt}],
            temperature=0,
            max_tokens=60
        )

        try:
            result = extract_response.choices[0].message.content.strip()
            result = result.replace("```json", "").replace("```", "").strip()
            result = result.replace('"null"', 'null')
            extracted = json.loads(result)
            if extracted.get("profession") == "null":
                extracted["profession"] = None
            if extracted.get("location") == "null":
                extracted["location"] = None
        except:
            extracted = {"profession": None, "location": None}

        print("EXTRACTED:", extracted, flush=True)

        profession = extracted.get("profession") or profile.get("profession")
        location = extracted.get("location") or profile.get("location")

        # Αποθήκευση στο profile
        if profession:
            profile["profession"] = profession
        if location:
            profile["location"] = location

        print("SERVICES - Profession:", profession, "Location:", location, flush=True)

        # Αν λείπει επάγγελμα
        if not profession:
            return jsonify({
                "reply": "Τι επαγγελματία ψάχνεις; Π.χ. ηλεκτρολόγο, υδραυλικό, γιατρό...",
                "links": [],
                "showButton": False
            })

        # Αν λείπει περιοχή
        if not location:
            return jsonify({
                "reply": f"Σε ποια περιοχή ψάχνεις {profession};",
                "links": [],
                "showButton": False
            })

        # ============================
        # ΕΧΟΥΜΕ ΚΑΙ ΤΑ ΔΥΟ → ΨΑΧΝΟΥΜΕ
        # ============================

        # 1️⃣ Πρώτα ψάχνουμε στη δική μας DB (Firestore)
        try:
            pros_ref = db.collection("professionals")
            query = pros_ref.where("specialty", "==", profession)\
                           .where("area", "==", location)\
                           .where("is_active", "==", True)\
                           .limit(5)
            docs = query.stream()
            our_pros = [doc.to_dict() | {"id": doc.id} for doc in docs]
        except Exception as e:
            print("FIRESTORE ERROR:", e, flush=True)
            our_pros = []

        print(f"✅ DB RESULTS: {len(our_pros)}", flush=True)

        # 🔥 Συμπληρώνουμε με Google Places μέχρι 5
        web_pros = []
        if len(our_pros) < 5:
            web_needed = 5 - len(our_pros)
            print(f"⚠️ Getting {web_needed} from Google Places", flush=True)
            web_pros = search_google_places(profession, location)[:web_needed]

        # 🔥 Συνδυασμός — δικοί μας πρώτα
        professionals = our_pros + web_pros
        source = "mixed" if our_pros and web_pros else ("db" if our_pros else "google")

        if not professionals:
            return jsonify({
                "reply": f"Δυστυχώς δεν βρήκα {profession} στην περιοχή {location} 😕\n\nΔοκίμασε άλλη περιοχή ή άλλο επάγγελμα.",
                "links": [],
                "showButton": False
            })

        # ============================
        # ΕΜΦΑΝΙΣΗ ΑΠΟΤΕΛΕΣΜΑΤΩΝ
        # ============================

        # Καταγραφή αναζήτησης στο Firestore
        try:
            db.collection("service_searches").add({
                "profession": profession,
                "location": location,
                "user_id": user_id,
                "source": source,
                "timestamp": firestore.SERVER_TIMESTAMP
            })
        except Exception as e:
            print("SEARCH LOG ERROR:", e, flush=True)

        # Reset profile για επόμενη αναζήτηση
        profile.pop("profession", None)
        profile.pop("location", None)
        # 🔥 Αποθήκευσε στο Firestore
        try:
            db.collection("service_sessions").document(user_id).set({
                "found_professionals": professionals,
                "found_profession": profession,
                "found_location": location
            })
        except Exception as e:
            print("FIRESTORE SAVE ERROR:", e, flush=True)

        links = []
        for p in professionals:
            name = p.get("name", "Επαγγελματίας")
            address = p.get("address", "")
            maps_query = urllib.parse.quote(f"{name} {address}")
            url = f"https://www.google.com/maps/search/{maps_query}"
            links.append({"title": f"📞 {name}", "url": url})

        # 🔥 Αποθήκευσε στο ιστορικό
        save_search_history(db, user_id, f"{profession} στο {location}", "services")
        return jsonify({
            "reply": f"Βρήκα **{len(professionals)} {profession}** στην περιοχή **{location}** 👇",
            "links": links,
            "showButton": False
        })

    # Fallback
    return jsonify({
        "reply": "Πες μου τι επαγγελματία ψάχνεις!",
        "links": [],
        "showButton": False
    })       
# =====================================================
# GENERATE RECOMMENDATIONS – DATABASE VERSION
# =====================================================


@app.route("/ai-memory", methods=["POST"])
def ai_memory():
    try:
        data = request.json
        user_id = data.get("userId", "anonymous")
        text = data.get("text", "").strip()
        idempotency_key = data.get("idempotency_key", "")

        if not text:
            return jsonify({"error": "no_text"}), 400

        # 🔥 Idempotency check — αποτρέπει διπλές εγγραφές
        cache_key = f"{user_id}:{idempotency_key}"
        if idempotency_key and cache_key in _memory_idempotency_cache:
            print(f"⚠️ DUPLICATE REQUEST IGNORED: {cache_key}", flush=True)
            return jsonify({"status": "duplicate", "message": "Already processed"}), 200
        if idempotency_key:
            _memory_idempotency_cache.add(cache_key)
            # Καθάρισε παλιά keys αν γίνουν πολλά
            if len(_memory_idempotency_cache) > 1000:
                _memory_idempotency_cache.clear()

        # 🔥 GPT-4o-mini: κατηγοριοποίηση + σύνοψη
        prompt = f"""
Ο χρήστης είπε αυτό (transcribed από φωνή):
"{text}"

Κατηγοριοποίησε και συνόψισε σε JSON:

Κατηγορίες:
- "todo": κάτι που πρέπει να κάνει (πρέπει να, να θυμηθώ, μην ξεχάσω)
- "shopping": αγορά προϊόντος (αγοράσω, πάρω, θέλω να αγοράσω)
- "appointment": ραντεβού, συνάντηση, κλείσιμο
- "note": γενική σημείωση, ιδέα, παρατήρηση

Απάντησε ΜΟΝΟ JSON:
{{
  "category": "todo/shopping/appointment/note",
  "summary": "Σύντομη περιγραφή σε 1 πρόταση στα ελληνικά",
  "original_text": "{text[:100]}"
}}
"""
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=150
        )

        result_text = completion.choices[0].message.content.strip()
        result_text = result_text.replace("```json", "").replace("```", "").strip()

        try:
            result = json.loads(result_text)
        except:
            result = {
                "category": "note",
                "summary": text[:100],
                "original_text": text[:100]
            }

        # 🔥 Αποθήκευση στο Firestore
        db.collection("ai_memory").add({
            "userId": user_id,
            "category": result.get("category", "note"),
            "summary": result.get("summary", text[:100]),
            "original_text": text[:200],
            "done": False,
            "createdAt": firestore.SERVER_TIMESTAMP
        })

        print(f"✅ AI MEMORY SAVED: {result.get('category')} — {result.get('summary')}", flush=True)
        return jsonify({
            "status": "ok",
            "category": result.get("category"),
            "summary": result.get("summary")
        })

    except Exception as e:
        print(f"AI MEMORY ERROR: {e}", flush=True)
        return jsonify({"error": str(e)}), 500

@app.route("/voice-command", methods=["POST"])
def voice_command():
    try:
        data = request.json
        user_id = data.get("userId", "anonymous")
        text = data.get("text", "").strip()

        if not text:
            return jsonify({"error": "no_text"}), 400

        prompt = f"""
Ο χρήστης είπε: "{text}"

Αναλύσε τι θέλει και επέστρεψε JSON:

ΚΑΤΗΓΟΡΙΕΣ ACTION:
1. "navigate_shopping" - αγορά προϊόντος (βρες, θέλω να αγοράσω, ψάξε)
2. "navigate_travel" - ξενοδοχείο, ταξίδι, διακοπές
3. "navigate_services" - επαγγελματία, ηλεκτρολόγο, υδραυλικό κτλ
4. "save_memory" - σημείωση, υπενθύμιση, to-do, ραντεβού

SUB_MODE (μόνο για navigate actions):
- navigate_shopping → sub_mode: "buy" (θέλω να αγοράσω συγκεκριμένο) ή "help" (βοήθησέ με να επιλέξω)
- navigate_travel → sub_mode: "hotel" (ξενοδοχείο/κράτηση) ή "inspiration" (πρότεινέ μου προορισμό) ή "guide" (πληροφορίες για μέρος)
- navigate_services → sub_mode: "find" (βρες συγκεκριμένο επαγγελματία) ή "help_pro" (βοήθεια εύρεσης)

ΚΑΝΟΝΕΣ:
- Αν θέλει να ΑΓΟΡΑΣΕΙ συγκεκριμένο προϊόν → navigate_shopping + sub_mode: "buy"
- Αν θέλει βοήθεια επιλογής προϊόντος → navigate_shopping + sub_mode: "help"
- Αν θέλει ξενοδοχείο/κράτηση → navigate_travel + sub_mode: "hotel"
- Αν θέλει πρόταση ταξιδιού/προορισμού → navigate_travel + sub_mode: "inspiration"
- Αν θέλει πληροφορίες για μέρος → navigate_travel + sub_mode: "guide"
- Αν θέλει συγκεκριμένο επαγγελματία → navigate_services + sub_mode: "find"
- Αν δεν ξέρει ποιον επαγγελματία χρειάζεται → navigate_services + sub_mode: "help_pro"
- Αν θέλει να ΘΥΜΗΘΕΙ/σημειώσει → save_memory
- Για navigate: δημιούργησε το κατάλληλο μήνυμα που θα σταλεί στο chat
- Για travel: συμπεριέλαβε ΟΛΕΣ τις λεπτομέρειες (προορισμός, ημερομηνίες, άτομα, budget, παροχές)

Απάντησε ΜΟΝΟ JSON:
{{
  "action": "navigate_shopping/navigate_travel/navigate_services/save_memory",
  "sub_mode": "buy/help/hotel/inspiration/guide/find/help_pro",
  "message": "Το μήνυμα που θα σταλεί στο chat (για navigate actions)",
  "summary": "Σύντομη περιγραφή για confirmation popup",
  "category": "todo/shopping/appointment/note (μόνο για save_memory)"
}}
"""
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=200
        )

        result_text = completion.choices[0].message.content.strip()
        result_text = result_text.replace("```json", "").replace("```", "").strip()

        try:
            result = json.loads(result_text)
        except:
            result = {
                "action": "save_memory",
                "sub_mode": None,
                "message": text,
                "summary": text[:80],
                "category": "note"
            }

        print(f"✅ VOICE COMMAND: {result}", flush=True)
        return jsonify(result)

    except Exception as e:
        print(f"VOICE COMMAND ERROR: {e}", flush=True)
        return jsonify({"error": str(e)}), 500
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
    new_session = data.get("new_session", False)

    username = data.get("userName") or ""
    name = vocative_name(username)

    # ✅🔥 FIX: ΠΑΝΤΑ ΠΡΩΤΑ ΤΟ FLOATING BUTTON
    ask_for_options = data.get("askOptions", False)

    if ask_for_options:

        if mode == "travel":
            profile = USER_PROFILES_TRAVEL.setdefault(user_id, {})
            print("FINAL PROFILE:", profile, flush=True)
            response = generate_travel_recommendations(history, user_id, client, profile)
        elif mode == "shopping":
            profile = USER_PROFILES_SHOPPING.setdefault(user_id, {})
            # 🔥 Παίρνε από intent (έχει όλη τη συνομιλία) αντί από profile
            query = profile.get("search_query", "")
            max_price = profile.get("budget_max", None)
            shopping_mode = profile.get("shopping_mode", "buy")

            if shopping_mode == "help":
                # 🔥 Serper ΜΟΝΟ για "Χρειάζομαι βοήθεια"
                from shopping import search_products_serper
                products = search_products_serper(query, max_price)
                
                if products:
                    reply = "Βρήκα αυτά για εσένα 👇\n\n"
                    links = []
                    for p in products:
                        reply += f"**{p['title']}** — {p['price']}\n📍 {p['source']}\n\n"
                        links.append({
                            "title": f"{p['title']} — {p['price']}",
                            "url": p['link']
                        })
                    return jsonify({
                        "reply": reply,
                        "links": links,
                        "showButton": False
                    })
                else:
                    import urllib.parse
                    encoded = urllib.parse.quote(query)
                    return jsonify({
                        "reply": "Δες τις καλύτερες τιμές παρακάτω 👇",
                        "links": [
                            {"title": "Δες στο Skroutz", "url": f"https://www.skroutz.gr/search?keyphrase={encoded}"},
                            {"title": "Δες στο Google Shopping", "url": f"https://www.google.com/search?q={encoded}&tbm=shop"}
                        ],
                        "showButton": False
                    })
            else:
                # buy mode ή φωτογραφία → απλά links, χωρίς Serper
                import urllib.parse
                encoded = urllib.parse.quote(query)
                return jsonify({
                    "reply": "Δες τις καλύτερες τιμές παρακάτω 👇",
                    "links": [
                        {"title": "Δες στο Skroutz", "url": f"https://www.skroutz.gr/search?keyphrase={encoded}"},
                        {"title": "Δες στο Google Shopping", "url": f"https://www.google.com/search?q={encoded}&tbm=shop"}
                    ],
                    "showButton": False
                })
                
        elif mode == "services":
            try:
                doc = db.collection("service_sessions").document(user_id).get()
                session = doc.to_dict() if doc.exists else {}
            except:
                session = {}

            professionals = session.get("found_professionals", [])
            profession = session.get("found_profession", "")
            location = session.get("found_location", "")

            print("FOUND PROFESSIONALS:", len(professionals), flush=True)

            links = []
            for p in professionals:
                name = p.get("name", "Επαγγελματίας")
                phone = p.get("phone", "")
                address = p.get("address", "")
                
                # 🔥 Φτιάχνουμε Google Maps link αντί website
                import urllib.parse
                maps_query = urllib.parse.quote(f"{name} {address}")
                url = f"https://www.google.com/maps/search/{maps_query}"
                
                title = f"📞 {name}"
                
                links.append({
                    "title": title,
                    "url": url
                })

            print("LINKS:", links, flush=True)

            try:
                db.collection("service_sessions").document(user_id).delete()
            except:
                pass

            if not links:
                return jsonify({
                    "reply": "Δεν βρήκα αποτελέσματα. Δοκίμασε ξανά.",
                    "links": [],
                    "showButton": False
                })

            return jsonify({
                "reply": f"Οι {profession} που βρήκα στο {location} 👇",
                "links": links,
                "showButton": False
            })
        else:
            response = generate_recommendations(mode, history, user_id, client)

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

    # 🔥 ROUTING ΜΕΤΑ το askOptions
    if mode == "travel":
        return handle_travel(data, client)

    elif mode == "shopping":
        return handle_shopping(data, client)

    elif mode == "services":
        return handle_services(data, client)

    # ------------------------------------------------

    if new_session and len(history) <= 1:
        USER_PROFILES_SHOPPING[user_id] = {}
        USER_PROFILES_TRAVEL[user_id] = {}
        print("NEW SESSION:", new_session, flush=True)

    name = f" {name}" if name else ""

    total_user = len([
        m for m in history
        if isinstance(m, dict) and m.get("isUser") is True
    ])

    total_links = len([
        m for m in history
        if isinstance(m.get("links"), list) and m.get("links")
    ])

    # -----------------------------------------
    # BEFORE LINKS
    # -----------------------------------------

    if total_links == 0:

        intent_type = None

        if mode == "shopping":
            intent = ai_extract_search_intent(history, client)
            intent_type = intent.get("intent_type", "product_search")

        print("GLOBAL INTENT:", intent_type, flush=True)

        if mode == "shopping" and intent_type == "knowledge_question":

            web_info = web_search_context(full_conversation(history))

            prompt = f"""
            Ο χρήστης κάνει ερώτηση γνώσης.

            Συνομιλία:
            {full_conversation(history)}

            Web πληροφορίες:
            {web_info}

            Κανόνες:
            - Πες το πιο πρόσφατο μοντέλο
            - ΜΗΝ μαντεύεις
            - Απάντα σύντομα
            """

            completion = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "system", "content": prompt}],
                temperature=0.3
            )

            return jsonify({
                "reply": clean_reply(completion.choices[0].message.content.strip()),
                "links": [],
                "showButton": False
            })

        if mode == "shopping" and intent_type == "product_question":

            return jsonify({
                "reply": realtime_ai_advisor(history),
                "links": [],
                "showButton": False
            })

        if mode == "shopping":

            intent = ai_extract_search_intent(history, client)
            profile = build_profile_from_intent(intent)

            complete = is_profile_complete_ai(profile)

            if not complete:

                question = generate_next_question_ai(profile, history, client)

                return jsonify({
                    "reply": question,
                    "links": [],
                    "showButton": False
                })

            return jsonify(ai_advisor_response(history))

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

        if user_after_links >= 2:
            query = profile.get("search_query", "")
            encoded = urllib.parse.quote(query)
            links = [
                {"title": "🔍 Αποτελέσματα στο Skroutz", "url": f"https://www.skroutz.gr/search?keyphrase={encoded}"},
                {"title": "🔍 Αποτελέσματα στο BestPrice", "url": f"https://www.bestprice.gr/search?q={encoded}"},
            ]
            return jsonify({
                "reply": "Τέλεια! Δες τις καλύτερες επιλογές:",
                "links": links,
                "showButton": False
            })

        return jsonify(ai_advisor_response(history))

# ═══════════════════════════════════════
# RANDOM HOTELS ENDPOINT
# ═══════════════════════════════════════
import random as _random

_hotels_cache = None

def _load_hotels():
    global _hotels_cache
    if _hotels_cache is None:
        try:
            with open("hotels.json", "r", encoding="utf-8") as f:
                _hotels_cache = json.load(f)
            print(f"✅ Loaded {len(_hotels_cache)} hotels from hotels.json", flush=True)
        except Exception as e:
            print(f"❌ hotels.json not found: {e}", flush=True)
            _hotels_cache = []
    return _hotels_cache

@app.route("/random-hotels", methods=["GET"])
def random_hotels():
    try:
        count = int(request.args.get("count", 10))
        hotels = _load_hotels()
        if not hotels:
            return jsonify({"hotels": [], "error": "No hotels loaded"})
        
        sample = _random.sample(hotels, min(count, len(hotels)))
        
        # Επιστρέφουμε μόνο τα απαραίτητα fields
        result = []
        for h in sample:
            result.append({
                "hotel_id": h.get("hotel_id", ""),
                "hotel_name": h.get("hotel_name", ""),
                "city": h.get("city", ""),
                "country": h.get("country", ""),
                "star_rating": h.get("star_rating", 0),
                "rating": h.get("rating_average", 0),
                "reviews": h.get("number_of_reviews", 0),
                "overview": (h.get("overview", "") or "")[:150],
                "photo": h.get("photo1", ""),
                "url": h.get("url", ""),
            })
        
        return jsonify({"hotels": result})
    except Exception as e:
        print(f"RANDOM HOTELS ERROR: {e}", flush=True)
        return jsonify({"hotels": [], "error": str(e)}), 500


# ═══════════════════════════════════════════════════
# NEW: SUBMIT REQUEST — Χρήστης στέλνει αίτημα
# ═══════════════════════════════════════════════════
@app.route("/submit-request", methods=["POST"])
def submit_request():
    try:
        data = request.json
        user_id = data.get("userId", "anonymous")
        description = data.get("description", "").strip()
        criteria = data.get("criteria", "cheap")
        image_count = data.get("imageCount", 0)
        user_name = data.get("userName", "Χρήστης")

        if not description:
            return jsonify({"error": "no_description"}), 400

        # AI εξάγει κατηγορία επαγγελματία
        prompt = f"""
Ο χρήστης λέει: "{description}"

Εξάγαγε:
1. Κατηγορία επαγγελματία (πχ Ηλεκτρολόγος, Ελαιοχρωματιστής, Υδραυλικός)
2. Περιγραφή εργασίας σε 1 πρόταση

Απάντησε ΜΟΝΟ JSON:
{{"profession": "...", "work_summary": "..."}}
"""
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0, max_tokens=80
        )
        result_text = completion.choices[0].message.content.strip()
        result_text = result_text.replace("```json", "").replace("```", "").strip()
        try:
            ai_result = json.loads(result_text)
        except:
            ai_result = {"profession": "Επαγγελματίας", "work_summary": description[:80]}

        profession = ai_result.get("profession", "Επαγγελματίας")
        work_summary = ai_result.get("work_summary", description[:80])

        # Αποθήκευσε αίτημα
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)
        doc_ref = db.collection("requests").add({
            "userId": user_id,
            "userName": user_name,
            "description": description,
            "profession": profession,
            "workSummary": work_summary,
            "criteria": criteria,
            "imageCount": image_count,
            "status": "active",
            "offersCount": 0,
            "createdAt": firestore.SERVER_TIMESTAMP,
            "expiresAt": expires_at,
        })
        request_id = doc_ref[1].id

        # Βρες επαγγελματίες της ίδιας κατηγορίας και στείλε τους notification
        pros_snap = db.collection("professionals")\
            .where("specialty", "==", profession)\
            .where("is_active", "==", True)\
            .stream()

        notified = 0
        for pro_doc in pros_snap:
            pro_data = pro_doc.to_dict()
            pro_user_id = pro_data.get("userId", "")
            if not pro_user_id:
                continue
            # Γράψε notification στον επαγγελματία
            db.collection("users").document(pro_user_id)\
                .collection("notifications").add({
                "title": f"🔔 Νέο αίτημα για {profession}!",
                "body": f"{user_name}: {work_summary[:80]}",
                "isRead": False,
                "requestId": request_id,
                "type": "new_request",
                "createdAt": firestore.SERVER_TIMESTAMP,
            })
            # FCM push αν έχει token
            pro_user_doc = db.collection("users").document(pro_user_id).get()
            if pro_user_doc.exists:
                fcm_token = pro_user_doc.to_dict().get("fcmToken")
                if fcm_token:
                    try:
                        msg = messaging.Message(
                            notification=messaging.Notification(
                                title=f"🔔 Νέο αίτημα — {profession}",
                                body=f"{user_name}: {work_summary[:60]}"),
                            data={"requestId": request_id, "type": "new_request"},
                            android=messaging.AndroidConfig(priority="high"),
                            token=fcm_token,
                        )
                        messaging.send(msg)
                        notified += 1
                    except Exception as e:
                        print(f"FCM to pro error: {e}", flush=True)

        print(f"✅ Request {request_id} created, notified {notified} pros", flush=True)

        return jsonify({
            "status": "ok",
            "requestId": request_id,
            "profession": profession,
            "workSummary": work_summary,
            "notifiedPros": notified,
        })
    except Exception as e:
        print(f"SUBMIT REQUEST ERROR: {e}", flush=True)
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════
# NEW: SUBMIT OFFER — Επαγγελματίας στέλνει προσφορά
# ═══════════════════════════════════════════════════
@app.route("/submit-offer", methods=["POST"])
def submit_offer():
    try:
        data = request.json
        request_id = data.get("requestId", "")
        professional_id = data.get("professionalId", "")
        professional_name = data.get("professionalName", "")
        price = data.get("price", 0)
        message = data.get("message", "")
        available_from = data.get("availableFrom", "Άμεσα")
        rating = data.get("rating", 5.0)
        emoji = data.get("emoji", "🔧")

        if not request_id or not professional_name:
            return jsonify({"error": "missing_fields"}), 400

        # Ελέγξτε αν το αίτημα είναι ακόμα ενεργό
        req_doc = db.collection("requests").document(request_id).get()
        if not req_doc.exists:
            return jsonify({"error": "request_not_found"}), 404
        if req_doc.to_dict().get("status") != "active":
            return jsonify({"error": "request_expired"}), 400

        # Αποθήκευσε προσφορά
        db.collection("offers").add({
            "requestId": request_id,
            "professionalId": professional_id,
            "professionalName": professional_name,
            "price": float(price),
            "message": message,
            "availableFrom": available_from,
            "rating": float(rating),
            "emoji": emoji,
            "createdAt": firestore.SERVER_TIMESTAMP,
        })

        # Αύξησε counter
        db.collection("requests").document(request_id).update({
            "offersCount": firestore.Increment(1)
        })

        print(f"✅ Offer from {professional_name}: {price}€", flush=True)
        return jsonify({"status": "ok"})

    except Exception as e:
        print(f"SUBMIT OFFER ERROR: {e}", flush=True)
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════
# NEW: GET OFFERS — Φόρτωση προσφορών για αίτημα
# ═══════════════════════════════════════════════════
@app.route("/get-offers/<request_id>", methods=["GET"])
def get_offers(request_id):
    try:
        req_doc = db.collection("requests").document(request_id).get()
        if not req_doc.exists:
            return jsonify({"error": "not_found"}), 404

        req_data = req_doc.to_dict()
        criteria = req_data.get("criteria", "cheap")
        description = req_data.get("description", "")
        status = req_data.get("status", "active")

        # Αν έχουν ήδη φιλτραριστεί
        if status == "completed" and req_data.get("topOffers"):
            return jsonify({
                "status": "completed",
                "offers": req_data["topOffers"],
                "totalOffers": req_data.get("offersCount", 0),
            })

        # Φόρτωσε όλες τις προσφορές
        offers_snap = db.collection("offers")\
            .where("requestId", "==", request_id).stream()
        offers = [o.to_dict() | {"id": o.id} for o in offers_snap]

        if not offers:
            return jsonify({"status": status, "offers": [], "totalOffers": 0})

        # AI φιλτράρισμα on-demand
        top3 = ai_filter_offers(offers, criteria, description)

        return jsonify({
            "status": status,
            "offers": top3,
            "totalOffers": len(offers),
        })

    except Exception as e:
        print(f"GET OFFERS ERROR: {e}", flush=True)
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════
# NEW: BOOKING RESPONSE — Επαγγελματίας αποδέχεται/απορρίπτει
# ═══════════════════════════════════════════════════
@app.route("/booking-response", methods=["POST"])
def booking_response():
    try:
        data = request.json
        booking_id = data.get("bookingId", "")
        action = data.get("action", "")

        if not booking_id or action not in ["accept", "reject"]:
            return jsonify({"error": "invalid"}), 400

        booking_doc = db.collection("bookings").document(booking_id).get()
        if not booking_doc.exists:
            return jsonify({"error": "not_found"}), 404

        booking_data = booking_doc.to_dict()
        user_id = booking_data.get("userId", "")
        pro_name = booking_data.get("professionalName", "Ο επαγγελματίας")
        is_accepted = action == "accept"

        db.collection("bookings").document(booking_id).update({
            "status": "accepted" if is_accepted else "rejected",
            "respondedAt": firestore.SERVER_TIMESTAMP,
        })

        if user_id:
            db.collection("users").document(user_id)\
                .collection("notifications").add({
                "title": "✅ Αίτημα αποδεκτό!" if is_accepted else "❌ Αίτημα απορρίφθηκε",
                "body": f"{pro_name} {'αποδέχτηκε' if is_accepted else 'απέρριψε'} το αίτημά σου.",
                "isRead": False,
                "bookingId": booking_id,
                "createdAt": firestore.SERVER_TIMESTAMP,
            })
            # FCM
            user_doc = db.collection("users").document(user_id).get()
            if user_doc.exists:
                fcm_token = user_doc.to_dict().get("fcmToken")
                if fcm_token:
                    try:
                        msg = messaging.Message(
                            notification=messaging.Notification(
                                title="✅ Αίτημα αποδεκτό!" if is_accepted else "❌ Αίτημα απορρίφθηκε",
                                body=f"{pro_name} {'αποδέχτηκε' if is_accepted else 'απέρριψε'} το αίτημά σου."),
                            token=fcm_token,
                        )
                        messaging.send(msg)
                    except Exception as e:
                        print(f"FCM error: {e}", flush=True)

        return jsonify({"status": "ok"})

    except Exception as e:
        print(f"BOOKING RESPONSE ERROR: {e}", flush=True)
        return jsonify({"error": str(e)}), 500
