import requests

from openai import OpenAI

def ai_normalize_destination(user_text, client: OpenAI):
    try:
        prompt = f"""
        Convert the following user input into a clean travel destination city.

        Rules:
        - Return ONLY a city name (no explanation)
        - If it's a landmark → return nearest city
        - If it's vague → return a popular Greek destination
        - Always return in English

        Input: {user_text}
        """

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=20
        )

        result = response.choices[0].message.content.strip()

        print("AI NORMALIZED:", result, flush=True)

        return result

    except Exception as e:
        print("AI NORMALIZE ERROR:", e)
        return user_text

def resolve_destination(destination, client):
    try:
        # 🔥 Normalize via AI
        destination = ai_normalize_destination(destination, client)
        destination = destination.strip().lower()

        # 🔥 SPECIAL CASES (landmarks → cities)
        SPECIAL_CASES = {
            "μετεωρα": "kalabaka",
            "meteora": "kalabaka",
            "ναυαγιο": "zakynthos",
            "navagio": "zakynthos",
            "ελaφονησι": "chania",
            "elafonisi": "chania"
        }

        if destination in SPECIAL_CASES:
            destination = SPECIAL_CASES[destination]

        url = "https://www.agoda.com/api/en-gb/Main/GetDestinationSuggestions"

        params = {
            "searchText": destination,
            "isHotel": "true"
        }

        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.agoda.com/",
            "Origin": "https://www.agoda.com",
            "Accept-Language": "en-US,en;q=0.9"
        }

        res = requests.get(url, params=params, headers=headers, timeout=5)
        data = res.json()

        print("AGODA RAW:", data, flush=True)

        results = data.get("data", [])

        # =========================
        # ✅ FIXED MATCHING LOGIC
        # =========================

        best_match = None

        # 1️⃣ ΠΑΝΤΑ πρώτα City
        for item in results:
            if item.get("type") == "City":
                best_match = item
                break

        # 2️⃣ Αν δεν βρεθεί City → fallback (Island / Area)
        if not best_match:
            for item in results:
                if item.get("type") in ["Island", "Area"]:
                    best_match = item
                    break

        # 3️⃣ Αν πάει να πάρει Region → warning
        if best_match and best_match.get("type") == "Region":
            print("⚠️ WARNING: Region match detected (SKIPPED):", best_match, flush=True)

        # 4️⃣ Final επιλογή
        if best_match:
            print("✅ CHOSEN DESTINATION:", best_match, flush=True)

            return {
                "city_id": best_match.get("id"),
                "name": best_match.get("name")
            }

    except Exception as e:
        print("RESOLVE ERROR:", e, flush=True)

    # 🔥 fallback
    print("⚠️ FALLBACK DESTINATION:", destination, flush=True)

    return {
        "city_id": None,
        "name": destination
    }