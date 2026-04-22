import json
import os
import re
import requests
from openai import OpenAI
from city_utils import full_conversation, get_last_user_text

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
GOOGLE_PLACES_KEY = os.getenv("GOOGLE_PLACES_KEY")


# =====================================================
# AI EXTRACT PROFESSION & LOCATION
# =====================================================
def ai_extract_service_intent(conversation, client):

    # 🔥 Παίρνουμε ΜΟΝΟ το τελευταίο μήνυμα του χρήστη
    last_user_text = ""
    for m in reversed(conversation):
        if isinstance(m, dict) and m.get("isUser"):
            last_user_text = m.get("text", "")
            break

    # Επίσης κρατάμε όλα τα user messages για context
    user_texts = [
        m.get("text", "")
        for m in conversation
        if isinstance(m, dict) and m.get("isUser")
    ]
    full_text = " ".join(user_texts)

    prompt = f"""
Εξάγαγε επάγγελμα και περιοχή από αυτά τα μηνύματα.

Τελευταίο μήνυμα: "{last_user_text}"
Όλη η συνομιλία: "{full_text}"

ΚΑΝΟΝΕΣ:
- Δώσε προτεραιότητα στο τελευταίο μήνυμα
- Αν το επάγγελμα είναι σε κλητική/αιτιατική → μετέτρεψέ το σε ονομαστική
- Αν η περιοχή έχει "στο/στη/στην/στον" → αφαίρεσέ το

Παραδείγματα:
- "θελω ηλεκτρολόγο στο χαλάνδρι" → profession: "Ηλεκτρολόγος", location: "Χαλάνδρι"
- "ψάχνω υδραυλικό θεσσαλονίκη" → profession: "Υδραυλικός", location: "Θεσσαλονίκη"
- "παιδίατρο στην αθήνα" → profession: "Παιδίατρος", location: "Αθήνα"
- "ηλεκτρολόγο χαλάνδρι" → profession: "Ηλεκτρολόγος", location: "Χαλάνδρι"
- "θελω ηλεκτρολογο" → profession: "Ηλεκτρολόγος", location: null
- "χαλάνδρι" (μόνο περιοχή) → profession: null, location: "Χαλάνδρι"

Αν κάτι δεν αναφέρεται → null

Απάντησε ΜΟΝΟ JSON χωρίς markdown, χωρίς εξήγηση:
{{"profession": "επάγγελμα ή null", "location": "περιοχή ή null"}}
"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=60
        )
        result = response.choices[0].message.content.strip()
        result = result.replace("```json", "").replace("```", "").strip()
        
        # Αν περιέχει null ως string → None
        result = result.replace('"null"', 'null')
        
        data = json.loads(result)
        
        # Καθαρισμός τιμών
        if data.get("profession") == "null":
            data["profession"] = None
        if data.get("location") == "null":
            data["location"] = None
            
        print("SERVICE INTENT EXTRACTED:", data, flush=True)
        return data
        
    except Exception as e:
        print("EXTRACT SERVICE INTENT ERROR:", e, flush=True)
        return {"profession": None, "location": None}


# =====================================================
# AI DETECT PROFESSION FROM PROBLEM
# =====================================================
def ai_detect_profession_from_problem(problem_text, client):

    prompt = f"""
Ο χρήστης περιγράφει ένα πρόβλημα:
"{problem_text}"

Ποιος επαγγελματίας χρειάζεται για να το λύσει;

Παραδείγματα:
- "χάλασε η αντλία νερού" → Υδραυλικός
- "δεν ανάβουν τα φώτα" → Ηλεκτρολόγος
- "χάλασε το ψυγείο" → Τεχνικός Ψυκτικών
- "πονάει το παιδί μου" → Παιδίατρος
- "χρειάζομαι βάψιμο σπιτιού" → Ελαιοχρωματιστής
- "έσπασε τζάμι" → Υαλουργός
- "πρόβλημα με θέρμανση" → Τεχνικός Θέρμανσης
- "χάλασε κλιματιστικό" → Τεχνικός Κλιματισμού
- "πρόβλημα με δόντια" → Οδοντίατρος
- "χρειάζομαι νομικό" → Δικηγόρος
- "πρόβλημα με αυτοκίνητο" → Μηχανικός Αυτοκινήτων

Απάντησε ΜΟΝΟ με το επάγγελμα στα ελληνικά (1-3 λέξεις).
"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=20
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print("DETECT PROFESSION ERROR:", e, flush=True)
        return None


# =====================================================
# GOOGLE PLACES SEARCH
# =====================================================
def search_google_places(profession, location):

    try:
        query = f"{profession} {location} Ελλάδα"

        url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
        params = {
            "query": query,
            "key": GOOGLE_PLACES_KEY,
            "language": "el",
            "region": "gr"
        }

        response = requests.get(url, params=params, timeout=10)
        data = response.json()

        results = data.get("results", [])[:3]

        professionals = []
        for r in results:
            place_id = r.get("place_id")
            details = get_place_details(place_id)

            professionals.append({
                "name": r.get("name", ""),
                "address": r.get("formatted_address", ""),
                "rating": r.get("rating", 0),
                "phone": details.get("phone", ""),
                "place_id": place_id,
                "source": "google"
            })

        print("GOOGLE PLACES RESULTS:", len(professionals), flush=True)
        return professionals

    except Exception as e:
        print("GOOGLE PLACES ERROR:", e, flush=True)
        return []


# =====================================================
# GOOGLE PLACE DETAILS (για τηλέφωνο)
# =====================================================
def get_place_details(place_id):

    try:
        url = "https://maps.googleapis.com/maps/api/place/details/json"
        params = {
            "place_id": place_id,
            "fields": "formatted_phone_number",
            "key": GOOGLE_PLACES_KEY,
            "language": "el"
        }
        response = requests.get(url, params=params, timeout=5)
        data = response.json()
        result = data.get("result", {})
        return {
            "phone": result.get("formatted_phone_number", "")
        }
    except:
        return {"phone": ""}


# =====================================================
# LOG CLICK (καταγραφή κλικ)
# =====================================================
def log_professional_click(db, professional_id, user_id, profession, location):
    try:
        db.collection("professional_clicks").add({
            "professional_id": professional_id,
            "user_id": user_id,
            "profession": profession,
            "location": location,
            "timestamp": __import__("firebase_admin").firestore.SERVER_TIMESTAMP
        })
        print("✅ CLICK LOGGED:", professional_id, flush=True)
    except Exception as e:
        print("CLICK LOG ERROR:", e, flush=True)