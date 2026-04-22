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
from city_lookup import fix_city_name
from city_utils import resolve_destination
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
from travel import travel_guide_ai
from travel import travel_followup_questions
from travel import ai_detect_travel_intent
from travel import travel_ai_advisor
from travel import generate_travel_recommendations
from travel import build_expedia_search_url
from city_utils import full_conversation, get_last_user_text, normalize_text_ai
from city_utils import web_search_context
from city_utils import GREEK_NUMBERS

from services import (
    ai_extract_service_intent,
    ai_detect_profession_from_problem,
    search_google_places,
    log_professional_click
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
    if data.get("new_session") or len(history) <= 1:
        USER_PROFILES_TRAVEL[user_id] = {}
        print("RESET TRAVEL PROFILE", flush=True)

    profile = USER_PROFILES_TRAVEL.setdefault(user_id, {})

    print("TRAVEL PROFILE BEFORE:", profile, flush=True)

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

    if len(history) <= 1:
        return jsonify({
            "reply": f"""Καλώς ήρθες ξανά {username} ✈️

    Πες μου πως μπορώ να σε βοηθήσω:

    • 🏨 Θέλεις να σου βρω ξενοδοχείο σε κάποια πόλη;
    • ✨ Θέλεις να σου προτείνω εγώ έναν προορισμό;
    • 🗺️ Θέλεις πληροφορίες για κάποιο μέρος;

    Πάτα ένα από τα παρακάτω κουμπιά 👇""",
            "links": [],
            "showButton": False
        })

    name = vocative_name(username)
    name = f" {name}" if name else ""

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
        profile["destination"] = None
        profile["awaiting"] = None  # σημαντικό

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
            reply = travel_ai_advisor(inspiration_query, client, None, already_suggested)

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

        # 🔥 Αποθήκευσε το αρχικό αίτημα αν δεν υπάρχει ήδη
        if not profile.get("inspiration_query"):
            profile["inspiration_query"] = user_text

        # Κρατάμε τη λίστα ήδη προτεινόμενων
        already_suggested = profile.get("already_suggested", [])

        context = profile.get("destination") or profile.get("suggested_destination")

        # 🔥 Περνάμε και τη λίστα already_suggested στο AI
        reply = travel_ai_advisor(user_text, client, context, already_suggested)

        match = re.search(r"👉\s*(.+)", reply)

        if match:
            suggested = match.group(1).strip()
            
            # 🔥 ΕΛΕΓΧΟΣ ΑΝ ΥΠΑΡΧΕΙ ΣΤΟ CITY INDEX
            test_resolve = resolve_destination(suggested, client)
            
            if not test_resolve.get("city_id"):
                # Δεν βρέθηκε → ενημερώνουμε τον χρήστη
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
    
        # 🔥 Εξάγουμε το μέρος από το μήνυμα αν δεν το έχουμε ήδη
        if not profile.get("guide_location"):
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

        # 🔥 Χρησιμοποιούμε το αποθηκευμένο μέρος
        guide_location = profile.get("guide_location", "")
        
        # Περνάμε το context στο AI
        reply = travel_guide_ai(user_text, client, guide_location)
        
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
            "reply": f"""Καλώς ήρθες ξανά {username} 🛒

    Πες μου πως μπορώ να σε βοηθήσω:

    • 🏨 Θέλεις να αγοράσεις κάποιο προιόν;
    • ✨ Θέλεις να σε βοηθήσω εγω στο να επιλέξεις;

    Πάτα ένα από τα παρακάτω κουμπιά 👇""",
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
            "reply": completion.choices[0].message.content.strip(),
            "links": [],
            "showButton": False
        })

    # ============================
    # HELP MODE → AI βοηθάει να αποφασίσει
    # ============================
    if shopping_mode == "help":

        # 🔥 RESET ΠΡΩΤΑ - αν ο χρήστης αλλάζει κάτι
        if profile.get("help_ready") and not any(x in user_text for x in ["ναι", "yes", "nai", "ok", "οκ", "ναί"]):
            profile.pop("help_ready", None)
            profile.pop("search_query", None)
            print("🔥 HELP RESET - user changed preference", flush=True)

        # ΕΛΕΓΧΟΣ ΑΝ ΕΧΕΙ ΑΡΚΕΤΕΣ ΠΛΗΡΟΦΟΡΙΕΣ
        check_prompt = f"""
Διάβασε αυτή τη συνομιλία:
{full_conversation(history)}

Έχεις αρκετές πληροφορίες για να προτείνεις προϊόν;

Χρειάζεσαι:Τι είδος προϊόντος ψάχνειBudget - αν πει "δεν ξέρω" ή "δεν έχω συγκεκριμένο" → ΟΚΤουλάχιστον 1 χαρακτηριστικό (χρώμα, μέγεθος, χρήση κτλ)
Αν ΟΛΑ είναι ΟΚ (έστω και με αόριστη απάντηση) → YES
Αλλιώς → NO

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
                "reply": question.choices[0].message.content.strip(),
                "links": [],
                "showButton": False
            })

        # ============================
        # ΕΧΕΙ ΑΡΚΕΤΑ → AI ΠΡΟΤΕΙΝΕΙ ΣΥΓΚΕΚΡΙΜΕΝΟ ΠΡΟΪΟΝ
        # ============================

        if not profile.get("help_ready"):

            product_prompt = f"""
Είσαι expert σύμβουλος αγορών.

Ο χρήστης θέλει:
{full_conversation(history)}

Βήμα 1: Πρότεινε 1 συγκεκριμένο προϊόν που ταιριάζει.

Κανόνες ανά κατηγορία:
- Ηλεκτρονικά/gadgets → δώσε ΣΥΓΚΕΚΡΙΜΕΝΟ μοντέλο (π.χ. Samsung Galaxy A55)
- Έπιπλα/είδη σπιτιού → δώσε περιγραφικό query (π.χ. καναπές εξωτερικού χώρου μαύρος)
- Ρούχα/αξεσουάρ → δώσε περιγραφικό query με χαρακτηριστικά
- Άλλο → δώσε το πιο συγκεκριμένο query που μπορείς

Βήμα 2: Στο τέλος γράψε ΠΑΝΤΑ:
SEARCH: [ακριβές search query για Skroutz/BestPrice]

Παραδείγματα SEARCH:
- "οικονομικό κινητό με καλή κάμερα" → SEARCH: Samsung Galaxy A55
- "καναπές εξωτερικού χώρου μαύρο χρώμα" → SEARCH: καναπές εξωτερικού χώρου μαύρος
- "laptop για φοιτητή 600 ευρώ" → SEARCH: Lenovo IdeaPad 3 15
- "ακουστικά για τρέξιμο αδιάβροχα" → SEARCH: ακουστικά running αδιάβροχα

Απάντα στα ελληνικά.
"""
            completion = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "system", "content": product_prompt}],
                temperature=0.3
            )

            reply = completion.choices[0].message.content.strip()

            # 🔥 Εξάγουμε το SEARCH keyword
            import re
            search_match = re.search(r"SEARCH:\s*(.+)", reply)
            if search_match:
                query = search_match.group(1).strip()
                reply = reply.replace(f"SEARCH: {query}", "").strip()
            else:
                # Fallback αν δεν βρει SEARCH tag
                intent = ai_extract_search_intent(history, client)
                query = intent.get("search_keywords_gr") or intent.get("search_keywords_en") or user_text

            profile["search_query"] = query
            profile["help_ready"] = True

            print("HELP SEARCH QUERY:", query, flush=True)

            return jsonify({
                "reply": reply + "\n\nΘες να σου δείξω τις καλύτερες τιμές; Γράψε 'ναι' 😊",
                "links": [],
                "showButton": False
            })

        # ============================
        # ΧΡΗΣΤΗΣ ΕΓΡΑΨΕ "ΝΑΙ" → FLOATING
        # ============================
        if any(x in user_text for x in ["ναι", "yes", "nai", "ok", "οκ", "ναί"]):
            profile.pop("help_ready", None)
            return jsonify({
                "reply": "Τέλεια 👌 Να σου δείξω τις καλύτερες τιμές;",
                "links": [],
                "showButton": True
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
            "reply": question.choices[0].message.content.strip(),
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

        # 🔥 Αποθήκευσε το query για το askOptions
        profile["search_query"] = query

        return jsonify({
            "reply": "Τέλεια 👌 Βρήκα αυτό που ψάχνεις! Να σου δείξω τις καλύτερες τιμές;",
            "links": [],
            "showButton": True  # 🔥 εμφανίζεται το floating
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
            "reply": f"""Καλώς ήρθες{name} 🔧

Μπορώ να σε βοηθήσω να βρεις τον κατάλληλο επαγγελματία!

🔧 Θέλεις ηλεκτρολόγο, υδραυλικό, γιατρό...
🤔 Ή δεν ξέρεις ποιον χρειάζεσαι;

Πάτα ένα από τα παρακάτω κουμπιά 👇""",
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

        # Αν δεν έχουμε εντοπίσει επάγγελμα ακόμα
        if not profile.get("profession"):

            profession_prompt = f"""
Ο χρήστης περιγράφει ένα πρόβλημα:
"{user_text}"

Ποιος επαγγελματίας χρειάζεται για να το λύσει;

Παραδείγματα:
- "χάλασε η αντλία νερού" → Υδραυλικός
- "δεν ανάβουν τα φώτα" → Ηλεκτρολόγος
- "χάλασε το ψυγείο" → Τεχνικός Ψυκτικών
- "πονάει το παιδί μου" → Παιδίατρος
- "χρειάζομαι βάψιμο σπιτιού" → Ελαιοχρωματιστής

Απάντησε ΜΟΝΟ με το επάγγελμα στα ελληνικά (1-3 λέξεις).
Παράδειγμα: "Υδραυλικός" ή "Ηλεκτρολόγος"
"""
            prof_response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": profession_prompt}],
                max_tokens=20,
                temperature=0
            )
            profession = prof_response.choices[0].message.content.strip()
            profile["profession"] = profession
            profile["services_mode"] = "find"  # Πάμε στο find mode

            print("DETECTED PROFESSION:", profession, flush=True)

            return jsonify({
                "reply": f"Κατάλαβα! Χρειάζεσαι **{profession}** 😊\n\nΣε ποια περιοχή είσαι;",
                "links": [],
                "showButton": False
            })

    # ============================
    # FIND MODE → Ψάχνει επαγγελματία
    # ============================
    if services_mode == "find":

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
            query = pros_ref.where("profession", "==", profession).where("location", "==", location).limit(3)
            docs = query.stream()
            our_pros = [doc.to_dict() | {"id": doc.id} for doc in docs]
        except Exception as e:
            print("FIRESTORE ERROR:", e, flush=True)
            our_pros = []

        if our_pros:
            print("✅ FOUND IN DB:", len(our_pros), "professionals", flush=True)
            professionals = our_pros
            source = "db"
        else:
            # 2️⃣ Fallback: Google Places API
            print("⚠️ NOT IN DB, using Google Places", flush=True)
            professionals = search_google_places(profession, location)
            source = "google"

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

        return jsonify({
            "reply": f"Βρήκα **{len(professionals)} {profession}** στην περιοχή **{location}** 👇\n\nΠάτα σε κάποιον για να δεις το τηλέφωνο!",
            "links": [],
            "showButton": True
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
            query = profile.get("search_query", "")
            import urllib.parse
            encoded = urllib.parse.quote(query)
            return jsonify({
                "reply": "Δες τις καλύτερες τιμές παρακάτω 👇",
                "links": [
                    {
                        "title": "Δες στο Skroutz",
                        "url": f"https://www.skroutz.gr/search?keyphrase={encoded}"
                    },
                    {
                        "title": "Δες στο BestPrice",
                        "url": f"https://www.bestprice.gr/search?q={encoded}"
                    }
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
                url = p.get("website", p.get("place_id", "#"))
                if url and url != "#":
                    links.append({
                        "title": name,
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