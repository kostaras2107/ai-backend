import json
import unicodedata
import pandas as pd
from utils import full_conversation
from utils import get_last_user_text
import requests

travel_df = pd.read_csv("travel_feed.csv")

def normalize_destination(text):
    text = text.strip().lower()

    replacements = {
        "α": "a", "β": "v", "γ": "g", "δ": "d",
        "ε": "e", "ζ": "z", "η": "i", "θ": "th",
        "ι": "i", "κ": "k", "λ": "l", "μ": "m",
        "ν": "n", "ξ": "x", "ο": "o", "π": "p",
        "ρ": "r", "σ": "s", "ς": "s", "τ": "t",
        "υ": "y", "φ": "f", "χ": "ch", "ψ": "ps",
        "ω": "o",

        "ά": "a", "έ": "e", "ή": "i", "ί": "i",
        "ό": "o", "ύ": "y", "ώ": "o"
    }

    for gr, en in replacements.items():
        text = text.replace(gr, en)

    return text.title()


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
# AI EXTRACT TRAVEL
# =====================================================
def ai_extract_travel_intent(conversation, client):

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
• number of children
• atmosphere (quiet, vibrant, traditional, luxury)

User request:
{user_text}

Extract travel booking information from the conversation.

IMPORTANT RULES:

- If the user DOES NOT mention number of adults return:
  "adults": null

- NEVER assume default adults = 2.

- Only extract adults if the user clearly mentions:
  numbers, "2 persons", "3 adults", "for two", "couple", "family", etc.

- If the user says:
  "όχι παιδιά"
  "χωρίς παιδιά"
  "no children"

  return:
  "children": 0

- If children are not mentioned return:
  "children": null

Destination rules:

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

Dates rules:

Dates must be in YYYY-MM-DD format.

If the user does not specify the year assume the current year 2026.

Never return past dates.

If the user only gives one date assume it is the check-in date.

If checkout is missing return null.

Children rules:

If the user mentions children extract number and ages.

Example:

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
"adults": number or null,
"children": number or null,
"children_ages": [number],
"rooms": number,
"amenities": ["WIFI","POOL","FREE_BREAKFAST],
"budget_per_night": number or null
}}
Return amenities as list like:
["WIFI", "POOL", "FREE_BREAKFAST"]

Do NOT return meal_plan separately.

If information is missing return null.

Understand natural language such as:

People:

"2 persons"
"3 adults"
"for two"
"couple"
"family"

Budget:

"around 60 euros"
"max 100"
"cheap"
"budget hotel"
"up to 80"
"under 120"

Amenities:

"with breakfast"
"breakfast included"
"with wifi"
"with pool"
"with parking"
"spa hotel"
"sea view"
"pet friendly"

Map these to:



Amenities examples:
wifi -> WIFI
pool -> POOL
parking -> PARKING
spa -> SPA
gym -> FITNESS_CENTER
sea view -> OCEAN_VIEW
free_breakfast -> FREE_BREAKFAST

Understand both English and Greek language.

Examples:

User: ξενοδοχείο στο ναύπλιο με πισίνα 10 με 12 ιουνίου για 2 άτομα

{{
"destination": "nafplio",
"checkin": "2026-06-10",
"checkout": "2026-06-12",
"adults": 2,
"amenities": ["POOL"],
"budget_per_night": null
}}

User: hotel in patras 10 may to 13 may with breakfast

{{
"destination": "patras",
"checkin": "2026-05-10",
"checkout": "2026-05-13",
"adults": null,
"amenities": [],
"budget_per_night": null
}}

User: cheap hotel in xylokastro with wifi around 70

{{
"destination": "xylokastro",
"checkin": null,
"checkout": null,
"adults": null,
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
# AI DETECT ADVISOR
# =====================================================    
def ai_detect_travel_intent(text, client):

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
# TRAVEL AI ADVISOR
# =====================================================
def travel_ai_advisor(user_text, client):

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
# DETECT DESTINATION NAME
# =====================================================
def detect_destination_name(text):

    words = text.strip().split()

    if len(words) <= 2:
        return text.strip().lower()

    return None 

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


import urllib.parse
from datetime import datetime

def build_expedia_search_url(
    destination,
    region_id=None,
    latlong=None,
    checkin=None,
    checkout=None,
    adults=None,
    children_ages=None,
    rooms=1,
    amenities=None,
    budget_total=None
):
    destination_clean = normalize_destination(destination)
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

    params = {
        "destination": destination_clean,
        "startDate": checkin,
        "endDate": checkout,
        "adults": adults,
        "rooms": rooms,
        "sort": "RECOMMENDED",
        "categorySearch": "any_option",
        "useRewards": "false"
    }
    if children_ages:
        children_param = ",".join([f"1_{age}" for age in children_ages])
        params["children"] = children_param
        

    params = {k: v for k, v in params.items() if v}

    query = urllib.parse.urlencode(params)


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


def build_agoda_search_url(
    destination,
    checkin,
    checkout,
    adults=2,
    children=0,
    children_ages=None,
    rooms=1,
    amenities=None,
    budget=None
):
    import urllib.parse
    from datetime import datetime

    # 🔥 TEMPLATE (ΔΕΝ ΤΟ ΠΕΙΡΑΖΟΥΜΕ)
    base_params = {
        "locale": "en-gb",
        "currency": "EUR",
        "origin": "GR",
        "stateCode": "I",
        "cid": "1961158",
        "whitelabelid": "1",
        "loginLvl": "0",
        "storefrontId": "3",
        "currencyId": "1",
        "currencyCode": "EUR",
        "htmlLanguage": "en-gb",
        "cultureInfoName": "en-gb",
        "trafficGroupId": "2",
        "trafficSubGroupId": "2",
        "aid": "379556",
        "useFullPageLogin": "true",
        "cttp": "4",
        "isRealUser": "true",
        "mode": "production",
        "cdnDomain": "agoda.net",
        "travellerType": "2",
        "familyMode": "off",
        "benefits": "78322",
        "productType": "-1"
    }

    # dates
    base_params["checkIn"] = checkin
    base_params["checkOut"] = checkout

    # duration
    d1 = datetime.strptime(checkin, "%Y-%m-%d")
    d2 = datetime.strptime(checkout, "%Y-%m-%d")
    base_params["los"] = max((d2 - d1).days, 1)

    # people
    base_params["rooms"] = rooms
    base_params["adults"] = adults
    if children_ages:
        base_params["children"] = len(children_ages)
    else:
        base_params["children"] = children or 0

    if children_ages:
        base_params["childages"] = ",".join(map(str, children_ages))

    import unicodedata

    destination_clean = normalize_destination(destination)

    base_params["textToSearch"] = destination_clean
    base_params.pop("city", None)

    # amenities
    facility_map = {
        "WIFI": "90",
        "POOL": "93",
        "PARKING": "96",
        "SPA": "181"
    }

    facilities = []
    if amenities:
        for a in amenities:
            if a in facility_map:
                facilities.append(facility_map[a])

    if facilities:
        base_params["hotelFacility"] = ",".join(facilities)

    # budget
    if budget:
        base_params["PriceFrom"] = int(budget * 0.7)
        base_params["PriceTo"] = int(budget * 1.3)

    # build url
    query = urllib.parse.urlencode(base_params, doseq=True)

    final_url = f"https://www.agoda.com/en-gb/search?{query}"

    print("AGODA FINAL:", final_url, flush=True)

    return final_url
# -----------------------------------------
# TRAVEL RECOMMENDATION
# -----------------------------------------
def get_travel_recommendations(location, budget=None, limit=3):

    results = travel_df[
        travel_df["product_name"].str.contains(location, case=False, na=False)
    ]

    if len(results) == 0:
        results = travel_df.sample(limit)
    else:
        results = results.sample(min(limit, len(results)))

    suggestions = []

    for _, row in results.iterrows():
        suggestions.append({
            "title": row["product_name"],
            "url": row["tracking_url"]
        })

    return suggestions

def generate_travel_recommendations(conversation, user_id, client, profile):


    travel = ai_extract_travel_intent(conversation, client)

    user_text = get_last_user_text(conversation).lower()

    if travel.get("adults") == 2 and "ατομ" not in user_text and "people" not in user_text:
        travel["adults"] = None

    if travel.get("children") is None:
        if "παιδ" in user_text and any(x in user_text for x in ["όχι","οχι","no","χωρίς","δεν"]):
            travel["children"] = 0

    final_data = {
        "destination": profile.get("destination") or travel.get("destination"),
        "checkin": profile.get("checkin") or travel.get("checkin"),
        "checkout": profile.get("checkout") or travel.get("checkout"),
        "adults": profile.get("adults") or travel.get("adults"),
        "children": profile.get("children") or travel.get("children"),
        "children_ages": profile.get("children_ages") or travel.get("children_ages"),
        "amenities": profile.get("amenities") if profile.get("amenities") is not None else (travel.get("amenities") or []),
        "budget": profile.get("budget_per_night") or travel.get("budget_per_night")
    }

    destination = final_data["destination"]
    checkin = final_data["checkin"]
    checkout = final_data["checkout"]
    adults = final_data["adults"]
    children = final_data["children"]
    children_ages = final_data["children_ages"]
    amenities = final_data["amenities"]
    budget = final_data["budget"]

    rooms = 1

    profile.update(final_data)

    expedia_link = build_expedia_search_url(
        destination=destination,
        checkin=checkin,
        checkout=checkout,
        adults=adults,
        children_ages=children_ages,
        rooms=rooms,
        amenities=amenities,
        budget_total=budget
    )
    agoda_link = build_agoda_search_url(
        destination=destination,
        checkin=checkin,
        checkout=checkout,
        adults=adults,
        children_ages=children_ages,
        rooms=rooms,
        amenities=amenities,
        budget=budget
    )
    print("AGODA LINK FINAL:", agoda_link, flush=True)

    links = [
        {
            "title": "Δες στο Expedia",
            "url": expedia_link
        },
        {
            "title": "Δες στο Agoda",
            "url": agoda_link
        }
    
    ]

    return {
        "reply": f"Βρήκα επιλογές για {destination}. Δες τα ξενοδοχεία εδώ 👇",
        "links": links,
        "showButton": True
    }