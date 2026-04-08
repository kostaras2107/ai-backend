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
USER_PROFILES_SHOPPING = {}
USER_PROFILES_TRAVEL = {}

from shopping import (
    generate_recommendations,
    ai_extract_search_intent,
    build_profile_from_intent,
    is_profile_complete_ai,
    generate_next_question_ai
)


from travel import ai_extract_travel_intent
from travel import normalize_destination
from travel import detect_destination_name
from travel import ai_detect_travel_intent
from travel import travel_ai_advisor
from travel import generate_travel_recommendations
from travel import build_expedia_search_url
from utils import full_conversation, get_last_user_text, normalize_text_ai
from utils import web_search_context
from utils import GREEK_NUMBERS
from utils import get_last_user_text, full_conversation


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

# =========================
# DESTINATION NORMALIZER (AI)
# =========================

CITY_CACHE_AI = {}

def normalize_destination_ai(user_text, client):
    key = user_text.lower()

    if key in CITY_CACHE_AI:
        return CITY_CACHE_AI[key]

    prompt = f"""
Convert this location to a standard international city name (English).

Input: {user_text}

Return ONLY the city name.
"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=20
    )

    result = response.choices[0].message.content.strip()

    CITY_CACHE_AI[key] = result
    return result

def handle_travel(data, client):

    history = data.get("history", [])
    user_id = data.get("userId", "anonymous")
    username = data.get("userName", "")

    # ✅ FIX session reset
    if data.get("new_session") or len(history) <= 1:
        USER_PROFILES_TRAVEL[user_id] = {}
        print("RESET TRAVEL PROFILE", flush=True)

    profile = USER_PROFILES_TRAVEL.setdefault(user_id, {})

    print("TRAVEL PROFILE BEFORE:", profile, flush=True)

    # ✅ FIX welcome (μπαίνει ΜΟΝΟ εδώ)
    if len(history) <= 1:
        return jsonify({
            "reply": f"""
Καλώς ήρθες ξανά {username} ✈️

Πες μου σε ποια πόλη θέλεις να ταξιδέψεις και θα σου βρω ξενοδοχεία.

Μπορείς να γράψεις π.χ.

• ξενοδοχείο Πάτρα
• ξενοδοχείο Σαντορίνη

Αλλιώς πες μου να σου προτείνω εγώ ένα μέρος...
""",
            "links": [],
            "showButton": False
        })

    name = vocative_name(username)
    name = f" {name}" if name else ""

    user_text = get_last_user_text(history).lower()
    text_clean = clean_text(user_text)

    intent_type = ai_detect_travel_intent(user_text, client)
    possible_destination = detect_destination_name(user_text)

    if intent_type == "destination_inspiration" and not possible_destination:
        advice = travel_ai_advisor(user_text, client)
        return jsonify({
            "reply": advice,
            "links": [],
            "showButton": False
        })

    print("PROFILE BEFORE:", profile, flush=True)

    travel = {}
    print("HISTORY DEBUG:", history, flush=True)

    if profile.get("awaiting") is None:
        travel = ai_extract_travel_intent([history[-1]], client) or {}

    print("TRAVEL AI OUTPUT:", travel, flush=True)

    # ✅ ΚΡΙΣΙΜΟ FIX → αποθήκευση destination
    if travel.get("destination") and not profile.get("destination"):

        raw_dest = travel.get("destination")

        normalized_dest = normalize_destination_ai(raw_dest, client)

        profile["destination"] = normalized_dest

    # =========================
    # AUTO SAVE FROM AI
    # =========================

    if travel:

        # destination
        if travel.get("destination") and not profile.get("destination"):
            profile["destination"] = normalize_destination(travel.get("destination"))

        # dates
        if travel.get("checkin") and not profile.get("checkin"):
            profile["checkin"] = travel.get("checkin")

        if travel.get("checkout") and not profile.get("checkout"):
            profile["checkout"] = travel.get("checkout")

        # adults
        if travel.get("adults") and not profile.get("adults"):
            profile["adults"] = travel.get("adults")

        # children
        if travel.get("children") is not None and profile.get("children") is None:
            profile["children"] = travel.get("children")

        # children ages
        if travel.get("children_ages") and not profile.get("children_ages"):
            profile["children_ages"] = travel.get("children_ages")

        # budget
        if travel.get("budget_per_night") and not profile.get("budget_per_night"):
            profile["budget_per_night"] = travel.get("budget_per_night")

        # amenities
        if travel.get("amenities") and not profile.get("amenities"):
            profile["amenities"] = travel.get("amenities")    

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

    if profile.get("awaiting") == "dates":
        ai_dates = ai_extract_travel_intent(history, client)
        if ai_dates.get("checkin") and ai_dates.get("checkout"):
            profile["checkin"] = ai_dates.get("checkin")
            profile["checkout"] = ai_dates.get("checkout")
            profile.pop("awaiting", None)

    if adults is None and profile.get("awaiting") == "adults":

        if any(x in text_clean for x in ["εγω και η κοπελα μου","εγω και η γυναικα μου","couple","for two"]):
            profile["adults"] = 2
            profile.pop("awaiting", None)

        elif any(x in text_clean for x in ["μονος","solo","alone"]):
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

    # =========================
    # BUILD VALUES
    # =========================

    destination = profile.get("destination")
    checkin = profile.get("checkin")
    checkout = profile.get("checkout")
    adults = profile.get("adults")
    children = profile.get("children")
    budget = profile.get("budget_per_night")
    amenities = profile.get("amenities")
    children_ages = profile.get("children_ages", [])

    # =========================
    # MISSING
    # =========================

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
        else:
            if not value:
                missing.append(f)

    # 👉 children ages special case
    if profile.get("children", 0) > 0 and not profile.get("children_ages"):
        missing.append("children_ages")

    print("TRAVEL MISSING:", missing, flush=True)

    # ✅ ΑΝ ΕΙΝΑΙ ΠΛΗΡΕΣ → FLOATING
    if not missing:
        return jsonify({
            "reply": "Τέλεια 👌 Να σου δείξω τις καλύτερες επιλογές;",
            "links": [],
            "showButton": True
        })

    if missing:

        if "destination" in missing:
            profile["awaiting"] = "destination"
            return jsonify({"reply": f"Σε ποια πόλη θα ήθελες να ταξιδέψεις{name};","links": [],"showButton": False})

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

    return jsonify({
        "reply": "Τέλεια 👌 Να σου δείξω τις καλύτερες επιλογές;",
        "links": [],
        "showButton": True
    })

# =====================================================
# GENERATE RECOMMENDATIONS – DATABASE VERSION
# =====================================================


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
                "reply": completion.choices[0].message.content.strip(),
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

            return jsonify({
                "reply": "Τέλεια 👌 βρήκα ακριβώς τι χρειάζεσαι. Να σου δείξω τις καλύτερες επιλογές;",
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

        if user_after_links >= 2:
            return jsonify({
                "reply": "",
                "links": [],
                "showButton": True
            })

        return jsonify(ai_advisor_response(history))