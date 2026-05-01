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
IMAGE_USAGE = {}

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

        if mode == "shopping":   # ← υπάρχει ήδη γραμμή 241
            from shopping import ai_analyze_image_shopping
            result = ai_analyze_image_shopping(image_base64, user_text, client)

            # 🔥 ΜΟΝΟ αν είναι help mode → αποθήκευσε ανάλυση για συνομιλία
            current_shopping_mode = USER_PROFILES_SHOPPING.get(user_id, {}).get("shopping_mode", "buy")
            if current_shopping_mode == "help":
                USER_PROFILES_SHOPPING.setdefault(user_id, {})["image_analysis"] = {
                    "product_name": result.get("product_name", ""),
                    "search_query": result.get("search_query", ""),
                    "user_text": user_text
                }
                # 🔥 Το reply το φτιάχνει το AI βάσει φωτογραφίας + κειμένου
                if user_text:
                    # Έχει κείμενο → αποθήκευσε και συνέχισε στο chat flow
                    result["reply"] = None  # θα το χειριστεί το handle_shopping
                else:
                    # Δεν έχει κείμενο → ρώτα
                    result["reply"] = f"Είδα **{result.get('product_name', 'το προϊόν')}** 👀 Τι ακριβώς ψάχνεις;"

        else:
            from services import ai_analyze_image_services
            result = ai_analyze_image_services(image_base64, user_text, client)
            
            # 🔥 Αν είναι help mode → αποθήκευσε για διάλογο
            current_services_mode = USER_PROFILES_SERVICES.get(user_id, {}).get("services_mode", "find")
            if current_services_mode == "help":
                USER_PROFILES_SERVICES.setdefault(user_id, {})["image_analysis"] = {
                    "problem": result.get("problem", ""),
                    "profession": result.get("profession", ""),
                    "user_text": user_text
                }
                if user_text:
                    result["reply"] = None  # θα το χειριστεί το handle_services
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
        expedia_url = build_expedia_search_url(profile)
        links = [
            {"title": "🔍 Αποτελέσματα στο Expedia", "url": expedia_url},
            {"title": "🔍 Αποτελέσματα στο Agoda", "url": f"https://www.agoda.com/search?city={profile.get('destination','')}"},
        ]
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
    expedia_url = build_expedia_search_url(profile)
    links = [
        {"title": "🔍 Αποτελέσματα στο Expedia", "url": expedia_url},
        {"title": "🔍 Αποτελέσματα στο Agoda", "url": f"https://www.agoda.com/search?city={profile.get('destination','')}"},
    ]
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
            "reply": completion.choices[0].message.content.strip(),
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
                "reply": question.choices[0].message.content.strip(),
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

        query = profile.get("search_query", "")
        encoded = urllib.parse.quote(query)
        links = [
            {"title": "🔍 Αποτελέσματα στο Skroutz", "url": f"https://www.skroutz.gr/search?keyphrase={encoded}"},
            {"title": "🔍 Αποτελέσματα στο BestPrice", "url": f"https://www.bestprice.gr/search?q={encoded}"},
        ]
        return jsonify({
            "reply": "Τέλεια 👌 Βρήκα αυτό που ψάχνεις! Δες τις καλύτερες τιμές:",
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

