import requests
import pandas as pd
from openai import OpenAI
from fuzzywuzzy import fuzz

# =====================================================
# ΦΟΡΤΩΝΟΥΜΕ ΤΟ CITY INDEX ΜΙΑ ΦΟΡΑ
# =====================================================
try:
    _city_df = pd.read_csv("city_index.csv")
    _city_df["city"] = _city_df["city"].str.strip().str.lower()
    print("✅ CITY INDEX LOADED:", len(_city_df), "cities", flush=True)
except Exception as e:
    print("❌ CITY INDEX LOAD ERROR:", e, flush=True)
    _city_df = pd.DataFrame(columns=["city", "city_id"])


# =====================================================
# SPECIAL CASES
# Χρησιμοποιείται όταν το AI κάνει λάθος transliteration
# ή για landmarks που δεν είναι πόλεις
# =====================================================
SPECIAL_CASES = {

    # ==============================
    # ΕΛΛΑΔΑ - Landmarks
    # ==============================
    "μετεωρα": "kalabaka",
    "meteora": "kalabaka",
    "ναυαγιο": "zakynthos",
    "navagio": "zakynthos",
    "ελαφονησι": "chania",
    "elafonisi": "chania",
    "ακροπολη": "athens",
    "acropolis": "athens",
    "ολυμπια": "olympia",
    "δελφοι": "delphi",
    "επιδαυρος": "nafplio",
    "μυκηνες": "nafplio",

    # ==============================
    # ΕΛΛΑΔΑ - Πόλεις & Νησιά
    # ==============================
    "αθηνα": "athens",
    "αθήνα": "athens",
    "θεσσαλονικη": "thessaloniki",
    "θεσσαλονίκη": "thessaloniki",
    "πατρα": "patras",
    "πάτρα": "patras",
    "ηρακλειο": "heraklion",
    "ηράκλειο": "heraklion",
    "ηρακλειο κρητης": "heraklion",
    "χανια": "chania",
    "χανιά": "chania",
    "ρεθυμνο": "rethymno",
    "ρέθυμνο": "rethymno",
    "ροδος": "rhodes",
    "ρόδος": "rhodes",
    "κερκυρα": "corfu",
    "κέρκυρα": "corfu",
    "κορφος": "corfu",
    "κορφού": "corfu",
    "ζακυνθος": "zakynthos",
    "ζάκυνθος": "zakynthos",
    "ζακυνθο": "zakynthos",
    "κεφαλονια": "kefalonia",
    "κεφαλονιά": "kefalonia",
    "κεφαλλονια": "kefalonia",
    "ιθακη": "ithaca",
    "λευκαδα": "lefkada",
    "λευκάδα": "lefkada",
    "σαντορινη": "santorini",
    "σαντορίνη": "santorini",
    "θηρα": "santorini",
    "θήρα": "santorini",
    "μυκονος": "mykonos",
    "μύκονος": "mykonos",
    "παρος": "paros",
    "πάρος": "paros",
    "ναξος": "naxos",
    "νάξος": "naxos",
    "ιος": "ios",
    "ίος": "ios",
    "σιφνος": "sifnos",
    "σίφνος": "sifnos",
    "μηλος": "milos",
    "μήλος": "milos",
    "σκιαθος": "skiathos",
    "σκιάθος": "skiathos",
    "σκοπελος": "skopelos",
    "σκόπελος": "skopelos",
    "αλοννησος": "alonissos",
    "χαλκιδικη": "chalkidiki",
    "χαλκιδική": "chalkidiki",
    "καβαλα": "kavala",
    "καβάλα": "kavala",
    "ναυπλιο": "nafplio",
    "ναύπλιο": "nafplio",
    "ναυπλια": "nafplio",
    "καλαματα": "kalamata",
    "καλαμάτα": "kalamata",
    "κορωνη": "koroni",
    "κορώνη": "koroni",
    "σπαρτη": "sparta",
    "σπάρτη": "sparta",
    "τριπολη": "tripoli",
    "τρίπολη": "tripoli",
    "πυλος": "pylos",
    "πύλος": "pylos",
    "βολος": "volos",
    "βόλος": "volos",
    "λαρισα": "larissa",
    "λάρισα": "larissa",
    "τρικαλα": "trikala",
    "τρίκαλα": "trikala",
    "λαμια": "lamia",
    "λαμία": "lamia",
    "χαλκιδα": "chalkida",
    "χαλκίδα": "chalkida",
    "κορινθος": "corinth",
    "κόρινθος": "corinth",
    "δερβενι": "derveni",
    "δερβένι": "derveni",
    "ξυλοκαστρο": "xylokastro",
    "ξυλόκαστρο": "xylokastro",
    "αιγιο": "aigio",
    "αίγιο": "aigio",
    "ιωαννινα": "ioannina",
    "ιωάννινα": "ioannina",
    "γιαννενα": "ioannina",
    "γιάννενα": "ioannina",
    "πρεβεζα": "preveza",
    "πρέβεζα": "preveza",
    "ηγουμενιτσα": "igoumenitsa",
    "ηγουμενίτσα": "igoumenitsa",
    "κοζανη": "kozani",
    "κοζάνη": "kozani",
    "καστορια": "kastoria",
    "καστοριά": "kastoria",
    "φλωρινα": "florina",
    "φλώρινα": "florina",
    "βεροια": "veria",
    "βέροια": "veria",
    "σερρες": "serres",
    "σέρρες": "serres",
    "δραμα": "drama",
    "δράμα": "drama",
    "μυτιληνη": "mytilene",
    "μυτιλήνη": "mytilene",
    "λεσβος": "lesbos",
    "λέσβος": "lesbos",
    "χιος": "chios",
    "χίος": "chios",
    "σαμος": "samos",
    "σάμος": "samos",
    "κως": "kos",
    "κος": "kos",
    "κρητη": "heraklion",
    "κρήτη": "heraklion",
    "θασος": "thassos",
    "θάσος": "thassos",
    "σαμοθρακη": "samothrace",
    "σαμοθράκη": "samothrace",
    "λημνος": "lemnos",
    "λήμνος": "lemnos",
    "σικινος": "sikinos",
    "σίκινος": "sikinos",
    "φολεγανδρος": "folegandros",
    "φολέγανδρος": "folegandros",
    "αμοργος": "amorgos",
    "αμοργός": "amorgos",
    "τηνος": "tinos",
    "τήνος": "tinos",
    "συρος": "syros",
    "σύρος": "syros",
    "μυκονος": "mykonos",
    "ανδρος": "andros",
    "άνδρος": "andros",
    "κεα": "kea",
    "κύθνος": "kythnos",
    "σεριφος": "serifos",
    "σέριφος": "serifos",
    "αστυπαλαια": "astypalaia",
    "αστυπάλαια": "astypalaia",
    "καλυμνος": "kalymnos",
    "κάλυμνος": "kalymnos",
    "λερος": "leros",
    "λέρος": "leros",
    "πατμος": "patmos",
    "πάτμος": "patmos",

    # ==============================
    # ΑΙΓΥΠΤΟΣ
    # ==============================
    "χαγια": "hurghada",
    "haya": "hurghada",
    "χαγκια": "hurghada",
    "χουργκαντα": "hurghada",
    "σαρμ": "sharm el-sheikh",
    "σαρμ ελ σεϊχ": "sharm el-sheikh",
    "sharm": "sharm el-sheikh",
    "καιρο": "cairo",
    "κάιρο": "cairo",
    "αλεξανδρεια": "alexandria",
    "αλεξάνδρεια": "alexandria",
    "λουξορ": "luxor",
    "ασουαν": "aswan",

    # ==============================
    # ΤΟΥΡΚΙΑ
    # ==============================
    "κωνσταντινουπολη": "istanbul",
    "κωνσταντινούπολη": "istanbul",
    "σταμπουλ": "istanbul",
    "ανταλια": "antalya",
    "αντάλια": "antalya",
    "αντάλεια": "antalya",
    "μποντρουμ": "bodrum",
    "σμυρνη": "izmir",
    "σμύρνη": "izmir",
    "καππαδοκια": "goreme",
    "καππαδοκία": "goreme",
    "cappadocia": "goreme",
    "παμουκαλε": "pamukkale",
    "μαρμαρις": "marmaris",
    "φετιγε": "fethiye",
    "αλανυα": "alanya",

    # ==============================
    # ΤΑΪΛΑΝΔΗ
    # ==============================
    "μπανγκοκ": "bangkok",
    "μπάνγκοκ": "bangkok",
    "μπαλι": "bali",
    "μπάλι": "bali",
    "πουκετ": "phuket",
    "πούκετ": "phuket",
    "κο σαμουι": "koh samui",
    "κοσαμουι": "koh samui",
    "παταγια": "pattaya",
    "πατάγια": "pattaya",
    "τσιανγκ μαι": "chiang mai",

    # ==============================
    # ΙΣΠΑΝΙΑ
    # ==============================
    "βαρκελωνη": "barcelona",
    "βαρκελώνη": "barcelona",
    "μαδριτη": "madrid",
    "μαδρίτη": "madrid",
    "σεβιλλη": "seville",
    "σεβίλλη": "seville",
    "μαλαγα": "malaga",
    "μάλαγα": "malaga",
    "γρανάδα": "granada",
    "βαλενθια": "valencia",
    "βαλένθια": "valencia",
    "ιβιζα": "ibiza",
    "ίβιζα": "ibiza",
    "μαγιορκα": "mallorca",
    "μαγιόρκα": "mallorca",
    "τενεριφη": "tenerife",
    "τενερίφη": "tenerife",

    # ==============================
    # ΙΤΑΛΙΑ
    # ==============================
    "ρωμη": "rome",
    "ρώμη": "rome",
    "μιλανο": "milan",
    "μιλάνο": "milan",
    "βενετια": "venice",
    "βενετία": "venice",
    "φλωρεντια": "florence",
    "φλωρεντία": "florence",
    "νεαπολη": "naples",
    "νεάπολη": "naples",
    "μπολονια": "bologna",
    "τορινο": "turin",
    "τορίνο": "turin",
    "παλερμο": "palermo",
    "σαρδηνια": "cagliari",
    "σαρδηνία": "cagliari",
    "σικελια": "palermo",
    "σικελία": "palermo",
    "αμαλφι": "amalfi",
    "αμάλφι": "amalfi",
    "ποζιτανο": "positano",
    "κομο": "como",
    "κόμο": "como",

    # ==============================
    # ΓΑΛΛΙΑ
    # ==============================
    "παρισι": "paris",
    "παρίσι": "paris",
    "νιτσα": "nice",
    "νίτσα": "nice",
    "λυων": "lyon",
    "λυών": "lyon",
    "μασσαλια": "marseille",
    "μασσαλία": "marseille",
    "μονακο": "monaco",
    "μονακό": "monaco",

    # ==============================
    # ΗΝΩΜΕΝΟ ΒΑΣΙΛΕΙΟ
    # ==============================
    "λονδινο": "london",
    "λονδίνο": "london",
    "εδιμβουργο": "edinburgh",
    "εδιμβούργο": "edinburgh",
    "μαντσεστερ": "manchester",
    "μάντσεστερ": "manchester",
    "λιβερπουλ": "liverpool",

    # ==============================
    # ΗΠΑ
    # ==============================
    "νεα υορκη": "new york",
    "νέα υόρκη": "new york",
    "νεα ορλεανη": "new orleans",
    "λος αντζελες": "los angeles",
    "λος άντζελες": "los angeles",
    "λας βεγκας": "las vegas",
    "λας βέγκας": "las vegas",
    "μαϊαμι": "miami",
    "μαϊάμι": "miami",
    "σικαγο": "chicago",
    "σικάγο": "chicago",
    "σαν φρανσισκο": "san francisco",
    "ορλαντο": "orlando",
    "ορλάντο": "orlando",

    # ==============================
    # ΑΛΛΕΣ ΔΗΜΟΦΙΛΕΙΣ
    # ==============================
    "δουβλινο": "dublin",
    "δουβλίνο": "dublin",
    "αμστερνταμ": "amsterdam",
    "βρυξελλες": "brussels",
    "βρυξέλλες": "brussels",
    "βιεννη": "vienna",
    "βιέννη": "vienna",
    "πραγα": "prague",
    "πράγα": "prague",
    "βουδαπεστη": "budapest",
    "βουδαπέστη": "budapest",
    "βαρσοβια": "warsaw",
    "βαρσοβία": "warsaw",
    "στοκχολμη": "stockholm",
    "στοκχόλμη": "stockholm",
    "κοπενχαγη": "copenhagen",
    "κοπεγχάγη": "copenhagen",
    "ελσινκι": "helsinki",
    "ελσίνκι": "helsinki",
    "λισαβονα": "lisbon",
    "λισαβόνα": "lisbon",
    "ζυριχη": "zurich",
    "ζυρίχη": "zurich",
    "γενευη": "geneva",
    "γενεύη": "geneva",
    "μοναχο": "munich",
    "μόναχο": "munich",
    "βερολινο": "berlin",
    "βερολίνο": "berlin",
    "αμβουργο": "hamburg",
    "αμβούργο": "hamburg",
    "τοκιο": "tokyo",
    "τόκιο": "tokyo",
    "σεουλ": "seoul",
    "σεούλ": "seoul",
    "σαγκαη": "shanghai",
    "πεκινο": "beijing",
    "πεκίνο": "beijing",
    "σιγκαπουρη": "singapore",
    "σιγκαπούρη": "singapore",
    "σιδνεϊ": "sydney",
    "σίντνεϊ": "sydney",
    "σιντνεϊ": "sydney",
    "μελβουρνη": "melbourne",
    "μελβούρνη": "melbourne",
    "δουβαι": "dubai",
    "ντουμπάι": "dubai",
    "ντουμπαι": "dubai",
    "αμπου ντάμπι": "abu dhabi",
    "μαροκο": "marrakech",
    "μαρόκο": "marrakech",
    "μαρακες": "marrakech",
    "μαρακές": "marrakech",
    "καζαμπλανκα": "casablanca",
    "ριο": "rio de janeiro",
    "μπουενος αιρες": "buenos aires",
    "μεξικο": "mexico city",
    "μεξικό": "mexico city",
    "κανκουν": "cancun",
    "κανκούν": "cancun",
    "μαλδιβες": "male",
    "μαλδίβες": "male",
    "σεϋχελλες": "mahe",
    "μοριτιος": "mauritius",
    "μαυρίκιος": "mauritius",
}


# =====================================================
# AI NORMALIZE
# =====================================================
def ai_normalize_destination(user_text, client: OpenAI):
    try:
        prompt = f"""
        Convert the following user input into a standard English city name for hotel search.

        Rules:
        - Return ONLY the city name in lowercase English (no explanation, no country)
        - Translate Greek city names to their correct English version
        - Do NOT guess similar-sounding cities — be precise
        - If it's a landmark → return nearest city
        - If you are not sure → transliterate as-is

        Examples:
        Χανιά → chania
        Σαντορίνη → santorini
        Ναύπλιο → nafplio
        Πάτρα → patras
        Χάγια → hurghada
        Κορώνη → koroni
        Σίντνεϊ → sydney
        Μπανγκόκ → bangkok
        Κωνσταντινούπολη → istanbul

        Input: {user_text}

        Return ONLY the city name in lowercase English.
        """

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=20
        )

        result = response.choices[0].message.content.strip().lower()
        print("AI NORMALIZED:", result, flush=True)
        return result

    except Exception as e:
        print("AI NORMALIZE ERROR:", e)
        return user_text


# =====================================================
# RESOLVE DESTINATION
# Ψάχνει στο city_index.txt αντί να καλεί το Agoda API
# =====================================================
def resolve_destination(destination, client):
    try:
        # 1️⃣ Normalize via AI
        destination = ai_normalize_destination(destination, client)
        destination = destination.strip().lower()
        print("🔍 LOOKING FOR:", destination, "in", len(_city_df), "cities", flush=True)


        # 2️⃣ Special cases
        if destination in SPECIAL_CASES:
            destination = SPECIAL_CASES[destination]
            print("✅ SPECIAL CASE:", destination, flush=True)

        # 3️⃣ Exact match στο city_index
        match = _city_df[_city_df["city"] == destination]
        if not match.empty:
            city_id = int(match.iloc[0]["city_id"])
            name = match.iloc[0]["city"]
            print("✅ EXACT MATCH:", name, city_id, flush=True)
            return {"city_id": city_id, "name": name}

        # 4️⃣ Fuzzy match για παρόμοια ονόματα (π.χ. "santorini" → "santorini island")
        best_score = 0
        best_row = None

        for _, row in _city_df.iterrows():
            score = fuzz.ratio(destination, str(row["city"]))
            if score > best_score:
                best_score = score
                best_row = row

        if best_score >= 80 and best_row is not None:
            city_id = int(best_row["city_id"])
            name = best_row["city"]
            print(f"✅ FUZZY MATCH ({best_score}%):", name, city_id, flush=True)
            return {"city_id": city_id, "name": name}

        # 5️⃣ Fallback - χωρίς city_id, το Agoda θα κάνει text search
        print("⚠️ FALLBACK - NO MATCH:", destination, flush=True)
        return {"city_id": None, "name": destination}

    except Exception as e:
        print("RESOLVE ERROR:", e, flush=True)
        return {"city_id": None, "name": destination}


# =====================================================
# FULL CONVERSATION
# =====================================================
def full_conversation(history):
    lines = []
    for msg in history:
        if isinstance(msg, dict):
            role = "User" if msg.get("isUser") else "Assistant"
            text = msg.get("content") or msg.get("text") or msg.get("message") or ""
            if text:
                lines.append(f"{role}: {text}")
    return "\n".join(lines)


# =====================================================
# GET LAST USER TEXT
# =====================================================
def get_last_user_text(history):
    for msg in reversed(history):
        if isinstance(msg, dict) and msg.get("isUser"):
            return (
                msg.get("content")
                or msg.get("text")
                or msg.get("message")
                or ""
            )
    return ""


# =====================================================
# NORMALIZE TEXT
# =====================================================
def normalize_text_ai(text):
    import unicodedata
    text = unicodedata.normalize('NFD', text)
    text = ''.join(c for c in text if unicodedata.category(c) != 'Mn')
    return text.lower().strip()


# =====================================================
# WEB SEARCH CONTEXT
# =====================================================
def web_search_context(query):
    try:
        url = "https://api.duckduckgo.com/"
        params = {
            "q": query,
            "format": "json",
            "no_html": "1",
            "skip_disambig": "1"
        }
        res = requests.get(url, params=params, timeout=5)
        data = res.json()
        abstract = data.get("AbstractText", "")
        return abstract if abstract else ""
    except Exception as e:
        print("WEB SEARCH ERROR:", e)
        return ""


# =====================================================
# GREEK NUMBERS
# =====================================================
GREEK_NUMBERS = {
    "ενα": 1, "ένα": 1, "one": 1,
    "δυο": 2, "δύο": 2, "two": 2,
    "τρια": 3, "τρία": 3, "three": 3,
    "τεσσερα": 4, "τέσσερα": 4, "four": 4,
    "πεντε": 5, "πέντε": 5, "five": 5,
    "εξι": 6, "έξι": 6, "six": 6,
    "επτα": 7, "εφτα": 7, "επτά": 7, "εφτά": 7, "seven": 7,
    "οκτω": 8, "οχτω": 8, "οκτώ": 8, "οχτώ": 8, "eight": 8,
    "εννια": 9, "εννιά": 9, "nine": 9,
    "δεκα": 10, "δέκα": 10, "ten": 10,
}
