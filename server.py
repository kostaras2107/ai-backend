from flask import Flask, request, jsonify
from flask_cors import CORS
from openai import OpenAI
import os
import json
import requests
import firebase_admin
from firebase_admin import credentials, firestore, messaging
import random as _random
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timezone, timedelta

# =====================================================
# CONFIG
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
_memory_idempotency_cache = set()

# =====================================================
# HELPER
# =====================================================
def clean_reply(text):
    import re
    text = re.sub(r'^(Assistant|AI|Bot)\s*:\s*', '', text.strip(), flags=re.IGNORECASE)
    return text.strip()

def vocative_name(name):
    if not name:
        return ""
    name = name.strip()
    if name.endswith("ς"):
        return name[:-1]
    return name

# =====================================================
# APSCHEDULER — Reminders + Request expiry
# =====================================================
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
            temperature=0, max_tokens=50
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
        sorted_offers = sorted(offers, key=lambda x: float(x.get('price', 9999)))
        for i, o in enumerate(sorted_offers[:3]):
            o['rank'] = i + 1
        return sorted_offers[:3]


def process_expired_requests():
    """Κάθε λεπτό — ελέγχει αιτήματα που έληξαν."""
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
                continue

            request_id = doc.id
            user_id = data.get('userId', '')
            criteria = data.get('criteria', 'cheap')
            print(f"⏰ Request expired: {request_id}", flush=True)

            offers_snap = db.collection('offers').where('requestId', '==', request_id).stream()
            offers = [o.to_dict() | {'id': o.id} for o in offers_snap]

            if offers:
                top3 = ai_filter_offers(offers, criteria, data.get('description', ''))
                doc.reference.update({
                    'status': 'completed',
                    'topOffers': top3,
                    'completedAt': firestore.SERVER_TIMESTAMP,
                })
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
                            notification=messaging.Notification(title='⏰ Ο χρόνος έληξε!', body=body),
                            data={'requestId': request_id, 'type': 'request_expired'},
                            android=messaging.AndroidConfig(priority='high'),
                            token=fcm_token,
                        )
                        messaging.send(msg)
                    except Exception as e:
                        print(f"FCM error: {e}", flush=True)
    except Exception as e:
        print(f"REQUEST EXPIRY ERROR: {e}", flush=True)


# Ξεκίνα APScheduler
_scheduler = BackgroundScheduler(timezone="Europe/Athens")
_scheduler.add_job(send_reminders, 'interval', minutes=1, id='reminders')
_scheduler.add_job(process_expired_requests, 'interval', minutes=1, id='requests')
_scheduler.start()
print("✅ Scheduler started", flush=True)

# =====================================================
# HOTELS (παραμένει για το random-hotels endpoint)
# =====================================================
_hotels_cache = None

def _load_hotels():
    global _hotels_cache
    if _hotels_cache is None:
        try:
            with open("hotels.json", "r", encoding="utf-8") as f:
                _hotels_cache = json.load(f)
            print(f"✅ Loaded {len(_hotels_cache)} hotels", flush=True)
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
                "photo": h.get("photo1", ""),
                "url": h.get("url", ""),
            })
        return jsonify({"hotels": result})
    except Exception as e:
        print(f"RANDOM HOTELS ERROR: {e}", flush=True)
        return jsonify({"hotels": [], "error": str(e)}), 500

# =====================================================
# SUBMIT REQUEST — Χρήστης στέλνει αίτημα
# =====================================================
@app.route("/submit-request", methods=["POST"])
def submit_request():
    try:
        data = request.json
        user_id = data.get("userId", "anonymous")
        description = data.get("description", "").strip()
        criteria = data.get("criteria", "cheap")
        user_name = data.get("userName", "Χρήστης")
        images_data = data.get("images", [])
        request_id = data.get("requestId")  # Flutter περνάει το ID για να μην φτιαχτεί διπλό

        if not description:
            return jsonify({"error": "no_description"}), 400

        # AI εξάγει κατηγορία επαγγελματία
        prompt = (
            f'Ο χρήστης λέει: "{description}"\n'
            f'Εξάγαγε 1) Κατηγορία επαγγελματία (πχ Ηλεκτρολόγος, Ελαιοχρωματιστής)\n'
            f'2) Περιγραφή εργασίας σε 1 πρόταση.\n'
            f'Απάντησε ΜΟΝΟ JSON: {{"profession": "...", "work_summary": "..."}}'
        )
        try:
            completion = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0, max_tokens=80
            )
            result_text = completion.choices[0].message.content.strip()
            result_text = result_text.replace("```json", "").replace("```", "").strip()
            ai_result = json.loads(result_text)
        except Exception as ai_err:
            print(f"AI error (non-fatal): {ai_err}", flush=True)
            ai_result = {"profession": "Επαγγελματίας", "work_summary": description[:80]}

        profession = ai_result.get("profession", "Επαγγελματίας")
        work_summary = ai_result.get("work_summary", description[:80])

        # Αν το Flutter έστειλε requestId → το request υπάρχει ήδη, μόνο update
        if request_id:
            db.collection("requests").document(request_id).update({
                "profession": profession,
                "workSummary": work_summary,
            })
            print(f"✅ Updated existing request {request_id}", flush=True)
        else:
            # Δημιουργία νέου request (fallback — κανονικά δεν χρειάζεται)
            expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)
            doc_ref = db.collection("requests").add({
                "userId": user_id,
                "userName": user_name,
                "description": description,
                "profession": profession,
                "workSummary": work_summary,
                "criteria": criteria,
                "imageCount": len(images_data),
                "hasImages": len(images_data) > 0,
                "status": "active",
                "offersCount": 0,
                "createdAt": firestore.SERVER_TIMESTAMP,
                "expiresAt": expires_at,
            })
            request_id = doc_ref[1].id
            print(f"✅ Created new request {request_id}", flush=True)

        # ═══════════════════════════════════════════════
        # MATCHING: χρησιμοποιώ το profession που επέλεξε ο χρήστης
        # από το scroll picker (exact match πρώτα, μετά fuzzy fallback)
        # ═══════════════════════════════════════════════
        user_selected_profession = data.get("profession", "").strip()
        # Αν ο χρήστης επέλεξε επάγγελμα από το picker → χρησιμοποίησε αυτό
        # Αλλιώς χρησιμοποίησε αυτό που βρήκε το AI
        match_profession = user_selected_profession if user_selected_profession else profession

        all_pros = list(db.collection("professionals")
            .where("is_active", "==", True)
            .stream())

        print(f"Matching profession: '{match_profession}' (user selected: '{user_selected_profession}', AI: '{profession}')", flush=True)
        print(f"Total active pros: {len(all_pros)}", flush=True)

        match_lower = match_profession.lower()

        def pro_matches(specialty):
            if not specialty:
                # Επαγγελματίας χωρίς specialty → ειδοποίησε τον
                return True
            s = specialty.lower()
            # 1) Exact match (Ηλεκτρολόγος == Ηλεκτρολόγος)
            if match_lower == s:
                return True
            # 2) Contains match (Συντήρηση Κλιματιστικών ⊃ Κλιματισμός)
            if match_lower in s or s in match_lower:
                return True
            # 3) Keyword match — τουλάχιστον 1 κοινή λέξη >= 4 γράμματα
            match_words = set(w for w in match_lower.split() if len(w) >= 4)
            spec_words = set(w for w in s.split() if len(w) >= 4)
            if match_words & spec_words:
                return True
            return False

        pros_snap = [p for p in all_pros if pro_matches(p.to_dict().get("specialty", ""))]
        print(f"Matched {len(pros_snap)} pros for '{match_profession}'", flush=True)

        # ═══════════════════════════════════════════════
        # LOCATION FILTER: φιλτράρω βάσει περιοχής χρήστη
        # ═══════════════════════════════════════════════
        user_location = data.get("location", "").strip()
        if user_location and user_location != "Κοντά μου":
            loc_lower = user_location.lower()
            location_filtered = []
            for p in pros_snap:
                pro_area = p.to_dict().get("area", "").lower()
                # Exact ή contains match περιοχής
                if not pro_area or loc_lower in pro_area or pro_area in loc_lower:
                    location_filtered.append(p)
            print(f"Location filter '{user_location}': {len(pros_snap)} → {len(location_filtered)} pros", flush=True)
            # Αν δεν βρεθεί κανείς στην περιοχή → ειδοποίησε όλους (fallback)
            pros_snap = location_filtered if location_filtered else pros_snap
        else:
            print(f"No location filter applied (location='{user_location}')", flush=True)

        for p in pros_snap:
            print(f"  → {p.to_dict().get('name','?')} ({p.to_dict().get('specialty','?')}) [{p.to_dict().get('area','?')}]", flush=True)

        notified = 0
        for pro_doc in pros_snap:
            pro_data = pro_doc.to_dict()
            pro_user_id = pro_data.get("userId", "")
            if not pro_user_id:
                continue
            # In-app notification
            db.collection("users").document(pro_user_id).collection("notifications").add({
                "title": f"🔔 Νέο αίτημα για {profession}!",
                "body": f"{user_name}: {work_summary[:80]}",
                "isRead": False,
                "requestId": request_id,
                "type": "new_request",
                "hasImages": len(images_data) > 0,
                "imageCount": len(images_data),
                "createdAt": firestore.SERVER_TIMESTAMP,
            })
            # FCM push
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
                    except Exception as fcm_err:
                        print(f"FCM to pro error: {fcm_err}", flush=True)

        print(f"✅ Request {request_id} → notified {notified} pros", flush=True)
        return jsonify({
            "status": "ok",
            "requestId": request_id,
            "profession": profession,
            "workSummary": work_summary,
            "notifiedPros": notified,
        })
    except Exception as e:
        print(f"SUBMIT REQUEST ERROR: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

# =====================================================
# SUBMIT OFFER — Επαγγελματίας στέλνει προσφορά
# =====================================================
# backend/app.py — αντικατάστησε το /submit-offer route

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

        req_doc = db.collection("requests").document(request_id).get()
        if not req_doc.exists:
            return jsonify({"error": "request_not_found"}), 404
        if req_doc.to_dict().get("status") != "active":
            return jsonify({"error": "request_expired"}), 400

        # ── DUPLICATE CHECK ──────────────────────────────────────────
        # Αποφυγή διπλής προσφοράς από τον ίδιο επαγγελματία
        if professional_id:
            existing = (db.collection("offers")
                .where("requestId", "==", request_id)
                .where("professionalId", "==", professional_id)
                .limit(1)
                .stream())
            if any(True for _ in existing):
                print(f"⚠️ Duplicate offer blocked: {professional_name} → {request_id}", flush=True)
                return jsonify({"status": "duplicate", "message": "Offer already submitted"}), 200
        # ─────────────────────────────────────────────────────────────

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
        db.collection("requests").document(request_id).update({
            "offersCount": firestore.Increment(1)
        })
        print(f"✅ Offer from {professional_name}: {price}€", flush=True)
        return jsonify({"status": "ok"})
    except Exception as e:
        print(f"SUBMIT OFFER ERROR: {e}", flush=True)
        return jsonify({"error": str(e)}), 500
# =====================================================
# GET OFFERS — Φόρτωση + AI φιλτράρισμα προσφορών
# =====================================================
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

        offers_snap = db.collection("offers")\
            .where("requestId", "==", request_id).stream()
        offers = [o.to_dict() | {"id": o.id} for o in offers_snap]

        if not offers:
            return jsonify({"status": status, "offers": [], "totalOffers": 0})

        top3 = ai_filter_offers(offers, criteria, description)
        return jsonify({
            "status": status, "offers": top3, "totalOffers": len(offers),
        })
    except Exception as e:
        print(f"GET OFFERS ERROR: {e}", flush=True)
        return jsonify({"error": str(e)}), 500

# =====================================================
# BOOKING RESPONSE — Επαγγελματίας αποδέχεται/απορρίπτει
# =====================================================
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
                "isRead": False, "bookingId": booking_id,
                "createdAt": firestore.SERVER_TIMESTAMP,
            })
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

# =====================================================
# AI MEMORY — Αποθήκευση σημειώσεων
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

        cache_key = f"{user_id}:{idempotency_key}"
        if idempotency_key and cache_key in _memory_idempotency_cache:
            print(f"⚠️ DUPLICATE REQUEST IGNORED: {cache_key}", flush=True)
            return jsonify({"status": "duplicate"}), 200
        if idempotency_key:
            _memory_idempotency_cache.add(cache_key)
            if len(_memory_idempotency_cache) > 1000:
                _memory_idempotency_cache.clear()

        prompt = f"""
Ο χρήστης είπε: "{text}"
Κατηγοριοποίησε και συνόψισε σε JSON:
Κατηγορίες: "todo", "shopping", "appointment", "note"
Απάντησε ΜΟΝΟ JSON:
{{"category": "todo/shopping/appointment/note", "summary": "σύντομη περιγραφή στα ελληνικά"}}
"""
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0, max_tokens=100
        )
        result_text = completion.choices[0].message.content.strip()
        result_text = result_text.replace("```json", "").replace("```", "").strip()
        try:
            result = json.loads(result_text)
        except:
            result = {"category": "note", "summary": text[:100]}

        db.collection("ai_memory").add({
            "userId": user_id,
            "category": result.get("category", "note"),
            "summary": result.get("summary", text[:100]),
            "original_text": text[:200],
            "done": False,
            "createdAt": firestore.SERVER_TIMESTAMP
        })
        print(f"✅ AI MEMORY: {result.get('category')} — {result.get('summary')}", flush=True)
        return jsonify({"status": "ok", "category": result.get("category"), "summary": result.get("summary")})
    except Exception as e:
        print(f"AI MEMORY ERROR: {e}", flush=True)
        return jsonify({"error": str(e)}), 500

# =====================================================
# VOICE COMMAND — Κατηγοριοποίηση φωνητικής εντολής
# =====================================================
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
Αναλύσε τι θέλει:
- "navigate_services" αν ψάχνει επαγγελματία
- "save_memory" αν θέλει σημείωση/υπενθύμιση

Απάντησε ΜΟΝΟ JSON:
{{"action": "navigate_services/save_memory", "message": "μήνυμα για chat", "summary": "σύντομη περιγραφή", "category": "todo/appointment/note (μόνο για save_memory)"}}
"""
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0, max_tokens=150
        )
        result_text = completion.choices[0].message.content.strip()
        result_text = result_text.replace("```json", "").replace("```", "").strip()
        try:
            result = json.loads(result_text)
        except:
            result = {"action": "save_memory", "message": text, "summary": text[:80], "category": "note"}

        print(f"✅ VOICE COMMAND: {result}", flush=True)
        return jsonify(result)
    except Exception as e:
        print(f"VOICE COMMAND ERROR: {e}", flush=True)
        return jsonify({"error": str(e)}), 500

# =====================================================
# SEND PUSH — Generic FCM push από Flutter
# =====================================================
@app.route("/send-push", methods=["POST"])
def send_push():
    try:
        data = request.json
        token = data.get("token", "")
        title = data.get("title", "GorealAI")
        body = data.get("body", "")
        extra_data = data.get("data", {})

        if not token:
            return jsonify({"error": "no_token"}), 400

        msg = messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            data={k: str(v) for k, v in extra_data.items()},
            android=messaging.AndroidConfig(
                priority="high",
                notification=messaging.AndroidNotification(
                    channel_id="gorealai_main", sound="default")),
            apns=messaging.APNSConfig(
                payload=messaging.APNSPayload(
                    aps=messaging.Aps(sound="default", badge=1))),
            token=token,
        )
        result = messaging.send(msg)
        print(f"✅ Push sent: {title} → {token[:20]}...", flush=True)
        return jsonify({"status": "ok", "messageId": result})
    except Exception as e:
        print(f"SEND PUSH ERROR: {e}", flush=True)
        return jsonify({"error": str(e)}), 500

# =====================================================
# HEALTH CHECK
# =====================================================
@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "GorealAI Bidding Server v2.0"})

