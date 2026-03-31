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
from shopping import (
    generate_recommendations,
    ai_extract_search_intent,
    build_profile_from_intent,
    is_profile_complete,
    generate_next_question
)


from travel import ai_extract_travel_intent
from travel import normalize_destination
from travel import detect_destination_name
from travel import ai_detect_travel_intent
from travel import travel_ai_advisor
from travel import generate_travel_recommendations
from travel import build_expedia_search_url
from utils import full_conversation, get_last_user_text, normalize_text
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

    if new_session:
        USER_PROFILES[user_id] = {}
    ask_for_options = data.get("askOptions", False)

    username = data.get("userName") or ""
    name = vocative_name(username)

    name = f" {name}" if name else ""

    if len(history) <= 1:

        if mode == "travel":

            welcome_text = f"""
            
            Καλώς ήρθες ξανά {username} ✈️

            Πες μου σε ποια πόλη θέλεις να ταξιδέψεις και θα σου βρω ξενοδοχεία.

            Μπορείς να γράψεις π.χ.

            • ξενοδοχείο Πάτρα
            • ξενοδοχείο Σαντορίνη
            
            Αλλιώς πες μου να σου προτείνω εγω ενα μέρος...
            """

        elif mode == "services":
            welcome_text = f"""Καλώς ήρθες ξανά {username} 🔧

        Πες μου τι επαγγελματία χρειάζεσαι.

        Μπορείς να γράψεις π.χ.

        • υδραυλικός Χαλάνδρι
        • ηλεκτρολόγος Αθήνα
        • μάστορας για πλακάκια
        """

        elif mode == "shopping":
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

        if mode == "travel":
            profile = USER_PROFILES.setdefault(user_id, {})
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

    # -----------------------------------------
    # BEFORE LINKS
    # -----------------------------------------

    if total_links == 0:  
        if mode == "shopping":

            # 🔥 1. intent
            intent = ai_extract_search_intent(history, client)

            # 🔥 2. profile
            profile = build_profile_from_intent(intent)

            print("PROFILE:", profile, flush=True)

            # 🔥 3. completeness
            complete = is_profile_complete(profile)

            print("IS COMPLETE:", complete, flush=True)

            # 🔥 4. αν ΔΕΝ είναι complete → ρώτα
            if not complete:

                question = generate_next_question(profile, history, client)

                return jsonify({
                    "reply": question,
                    "links": [],
                    "showButton": False
                })

            # 🔥 5. αν είναι complete → δείξε κουμπί
            return jsonify({
                "reply": "Τέλεια 👌 βρήκα ακριβώς τι χρειάζεσαι. Να σου δείξω τις καλύτερες επιλογές;",
                "links": [],
                "showButton": True
            })

        elif mode == "services":
            response = generate_services_recommendations(history)
            return jsonify(response)

        profile = USER_PROFILES.setdefault(user_id, {})
  
        if mode == "travel":  

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

            profile = USER_PROFILES.setdefault(user_id, {})

            travel = {}
            if profile.get("awaiting") is None:
                travel = ai_extract_travel_intent(history, client) or {}

            print("TRAVEL AI OUTPUT:", travel, flush=True)

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
                    adults = number
                    profile["adults"] = number
                    profile.pop("awaiting", None)

                elif awaiting == "children":
                    children = number
                    profile["children"] = number
                    profile.pop("awaiting", None)

                elif awaiting == "budget":
                    budget = number
                    profile["budget_per_night"] = number
                    profile.pop("awaiting", None)

            # =========================
            # SMART PARSERS (ΜΕΤΑΦΕΡΜΕΝΑ ΠΑΝΩ)
            # =========================

            if profile.get("awaiting") == "dates":
                ai_dates = ai_extract_travel_intent(history, client)
                if ai_dates.get("checkin") and ai_dates.get("checkout"):
                    checkin = ai_dates.get("checkin")
                    checkout = ai_dates.get("checkout")
                    profile["checkin"] = checkin
                    profile["checkout"] = checkout
                    profile.pop("awaiting", None)

            if adults is None and profile.get("awaiting") == "adults":

                if any(x in text_clean for x in ["εγω και η κοπελα μου","εγω και η γυναικα μου","couple","for two"]):
                    adults = 2
                    profile["adults"] = adults
                    profile.pop("awaiting", None)

                elif any(x in text_clean for x in ["μονος","solo","alone"]):
                    adults = 1
                    profile["adults"] = adults
                    profile.pop("awaiting", None)

                elif "παρεα" in text_clean:
                    adults = 2
                    profile["adults"] = adults
                    profile.pop("awaiting", None)

                nums = re.findall(r"\d+", text_clean)
                if nums:
                    adults = int(nums[0])
                    profile["adults"] = adults
                    profile.pop("awaiting", None)
                else:
                    for w, val in GREEK_NUMBERS.items():
                        if w in text_clean:
                            adults = val
                            profile["adults"] = adults
                            profile.pop("awaiting", None)

            if profile.get("awaiting") == "children":
                nums = re.findall(r"\d+", text_clean)
                if nums:
                    children = int(nums[0])
                    profile["children"] = children
                    profile.pop("awaiting", None)
                else:
                    for w, val in GREEK_NUMBERS.items():
                        if w in text_clean:
                            children = val
                            profile["children"] = children
                            profile.pop("awaiting", None)

            if profile.get("awaiting") == "children_ages":
                nums = re.findall(r"\d+", text_clean)
                if nums:
                    children_ages = [int(n) for n in nums]
                    profile["children_ages"] = children_ages
                    profile.pop("awaiting", None)
                else:
                    words = text_clean.split()
                    for w, val in GREEK_NUMBERS.items():
                        if w in words:
                            children_ages = [val]
                            profile["children_ages"] = children_ages
                            profile.pop("awaiting", None)
                            break

            if profile.get("awaiting") == "children":

                if any(x in text_clean for x in ["χωρις παιδια","οχι","δεν εχω παιδια","no children"]):
                    children = 0
                    children_ages = []
                    profile["children"] = 0
                    profile["children_ages"] = []
                    profile.pop("awaiting", None)

            if profile.get("awaiting") == "budget":
                nums = re.findall(r"\d+", text_clean)
                if nums:
                    budget = int(nums[0])
                    profile["budget_per_night"] = budget
                    profile.pop("awaiting", None)

            if profile.get("awaiting") == "amenities":

                if any(x in text_clean for x in ["οχι", "όχι", "no", "χωρις", "χωρίς", "δεν"]):
                    amenities = []
                    profile["amenities"] = []
                    profile.pop("awaiting", None)

                elif any(x in text_clean for x in [
                    "ολα", "όλα", "και τα 3", "και τα τρια",
                    "τα παντα", "όλα τα amenities", "βαλε ολα",
                    "yes all", "all", "ολες", "όλες"
                ]):
                    amenities = ["FREE_BREAKFAST", "WIFI", "POOL"]
                    profile["amenities"] = amenities
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
                        amenities = list(set(selected))
                        profile["amenities"] = amenities
                        profile.pop("awaiting", None) 

        # =========================
        # BUILD VALUES (ΜΕΤΑ PARSING)
        # =========================

        safe_travel = travel if (mode == "travel" and isinstance(travel, dict)) else {}

        destination = normalize_destination(
            safe_travel.get("destination") or profile.get("destination")
        )

        checkin = safe_travel.get("checkin") or profile.get("checkin")
        checkout = safe_travel.get("checkout") or profile.get("checkout")

        if checkin is not None:
            profile["checkin"] = checkin

        if checkout is not None:
            profile["checkout"] = checkout

        adults = safe_travel.get("adults") if safe_travel.get("adults") is not None else profile.get("adults")
        children = safe_travel.get("children") if safe_travel.get("children") is not None else profile.get("children")
        budget = safe_travel.get("budget_per_night") or profile.get("budget_per_night")
        rooms = safe_travel.get("rooms") or profile.get("rooms")

        if safe_travel.get("amenities") not in [None, []]:
            amenities = safe_travel.get("amenities")
        else:
            amenities = profile.get("amenities")

        if profile.get("children_ages"):
            children_ages = profile.get("children_ages")
        elif safe_travel.get("children_ages"):
            children_ages = safe_travel.get("children_ages")
        else:
            children_ages = []

        # =========================
        # AI FALLBACK (ΙΔΙΟ)
        # =========================

        ai_data = {}
        need_ai = False

        if profile.get("awaiting"):
            need_ai = False
        elif profile.get("awaiting") is None:
            if any(x is None for x in [destination, checkin, checkout]):
                need_ai = True

        if need_ai:
            ai_data = ai_extract_travel_intent(history, client)
            profile["ai_used"] = True

            if profile.get("children_ages"):
                children_ages = profile.get("children_ages")

            if adults is None:
                adults = ai_data.get("adults")

            if children is None:
                children = ai_data.get("children")

            if checkin is None:
                checkin = ai_data.get("checkin")

            if checkout is None:
                checkout = ai_data.get("checkout")

            if destination is None:
                destination = ai_data.get("destination")

            if budget is None:
                budget = ai_data.get("budget_per_night")

            ai_amenities = ai_data.get("amenities")

            if amenities is None and not profile.get("amenities"):
                if ai_amenities:
                    amenities = ai_amenities

            if profile.get("destination") is None:
                profile["destination"] = destination

            if profile.get("checkin") is None:
                profile["checkin"] = checkin

            if profile.get("checkout") is None:
                profile["checkout"] = checkout

            if profile.get("adults") is None:
                profile["adults"] = adults

            if profile.get("children") is None:
                profile["children"] = children

            if profile.get("amenities") is None:
                profile["amenities"] = amenities

            if profile.get("budget_per_night") is None:
                profile["budget_per_night"] = budget

        # =========================
        # SYNC + MISSING (ΙΔΙΟ)
        # =========================

        if profile.get("adults") is not None:
            adults = profile.get("adults")

        if profile.get("children") is not None:
            children = profile.get("children")

        if profile.get("children_ages"):
            children_ages = profile.get("children_ages")

        if profile.get("budget_per_night") is not None:
            budget = profile.get("budget_per_night")

        if profile.get("amenities") is not None:
            amenities = profile.get("amenities")

        if profile.get("amenities") == []:
            amenities = []    

        missing = []

        if destination is None:
            missing.append("destination")

        if checkin is None or checkout is None:
            missing.append("dates")

        if adults is None:
            missing.append("adults")

        if children is None:
            missing.append("children")

        if children is not None and children > 0 and not children_ages:
            missing.append("children_ages")

        if budget is None:
            missing.append("budget")

        if amenities is None:
            missing.append("amenities")

        if missing:

            if "destination" in missing:
                return jsonify({"reply": f"Σε ποια πόλη θα ήθελες να ταξιδέψεις{name};","links": [],"showButton": False})

            if "dates" in missing:
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

            if "budget" in missing:
                profile["awaiting"] = "budget"
                return jsonify({"reply": f"{name} Τι budget ανα βράδυ έχεις περίπου στο μυαλό σου;","links": [],"showButton": False})

            if "amenities" in missing:
                profile["awaiting"] = "amenities"
                return jsonify({"reply": "Θέλεις κάποιες συγκεκριμένες παροχές όπως πρωινό, wifi ή πισίνα;","links": [],"showButton": False})

        profile.pop("awaiting", None)

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