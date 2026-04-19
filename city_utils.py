import requests
from openai import OpenAI


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


def resolve_destination(destination, client):
    try:
        # 🔥 Normalize via AI
        destination = ai_normalize_destination(destination, client)
        destination = destination.strip().lower()

        # =========================
        # 🔥 SPECIAL CASES
        # Χρησιμοποιείται όταν το AI κάνει λάθος transliteration
        # ή για landmarks που δεν είναι πόλεις
        # =========================

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
            "ξανθη": "xanthi",
            "κομοτηνη": "komotini",
            "αλεξανδρουπολη": "alexandroupoli",
            "ιωαννινα": "ioannina",
            "ιωάννινα": "ioannina",
            "γιαννενα": "ioannina",
            "γιάννενα": "ioannina",
            "ναυπλιο": "nafplio",
            "ναύπλιο": "nafplio",
            "ναυπλια": "nafplio",
            "καλαματα": "kalamata",
            "καλαμάτα": "kalamata",
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
            "καρδιτσα": "karditsa",
            "λαμια": "lamia",
            "λαμία": "lamia",
            "χαλκιδα": "chalkida",
            "χαλκίδα": "chalkida",
            "λιβαδεια": "livadeia",
            "θηβα": "thebes",
            "θήβα": "thebes",
            "κορινθος": "corinth",
            "κόρινθος": "corinth",
            "δερβενι": "derveni",
            "δερβένι": "derveni",
            "ξυλοκαστρο": "xylokastro",
            "ξυλόκαστρο": "xylokastro",
            "αιγιο": "aigio",
            "αίγιο": "aigio",
            "πυργος": "pyrgos",
            "πύργος": "pyrgos",
            "αγρινιο": "agrinio",
            "αγρίνιο": "agrinio",
            "μεσολογγι": "messolonghi",
            "μεσολόγγι": "messolonghi",
            "αρτα": "arta",
            "άρτα": "arta",
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
            "εδεσσα": "edessa",
            "έδεσσα": "edessa",
            "βεροια": "veria",
            "βέροια": "veria",
            "ναουσα": "naoussa",
            "νάουσα": "naoussa",
            "σερρες": "serres",
            "σέρρες": "serres",
            "δραμα": "drama",
            "δράμα": "drama",
            "κιλκις": "kilkis",
            "πελλα": "pella",
            "γιαννιτσα": "giannitsa",
            "μυτιληνη": "mytilene",
            "μυτιλήνη": "mytilene",
            "λεσβος": "lesbos",
            "λέσβος": "lesbos",
            "χιος": "chios",
            "χίος": "chios",
            "σαμος": "samos",
            "σάμος": "samos",
            "λημνος": "lemnos",
            "λήμνος": "lemnos",
            "κως": "kos",
            "κος": "kos",
            "κρητη": "heraklion",
            "κρήτη": "heraklion",

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
            "αντάλια": "antalya",
            "ανταλια": "antalya",
            "αντάλεια": "antalya",
            "μποντρουμ": "bodrum",
            "bodrum": "bodrum",
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
            "τσιανγκ μάι": "chiang mai",
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
            "γρανάντα": "granada",
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
            "ποζιτάνο": "positano",
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
            "στρασβουργο": "strasbourg",
            "βορδο": "bordeaux",
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
            "βιρμιγχαμ": "birmingham",
            "λιβερπουλ": "liverpool",

            # ==============================
            # ΗΠΑ
            # ==============================
            "νεα υορκη": "new york",
            "νέα υόρκη": "new york",
            "νεοργη": "new york",
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
            "χονολουλου": "honolulu",
            "χονολούλου": "honolulu",
            "σιατλ": "seattle",

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
            "σανγκάη": "shanghai",
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
            "καζαμπλάνκα": "casablanca",
            "μπαλι": "bali",
            "ριο": "rio de janeiro",
            "ριο": "rio de janeiro",
            "ριο ντε τζανέιρο": "rio de janeiro",
            "μπουενος αιρες": "buenos aires",
            "μπουένος άιρες": "buenos aires",
            "μεξικο": "mexico city",
            "μεξικό": "mexico city",
            "κανκουν": "cancun",
            "κανκούν": "cancun",
            "μαλδιβες": "male",
            "μαλδίβες": "male",
            "σεϋχελλες": "mahe",
            "σεϋχέλλες": "mahe",
            "μοριτιος": "mauritius",
            "μαυρίκιος": "mauritius",
        }

        if destination in SPECIAL_CASES:
            destination = SPECIAL_CASES[destination]
            print("✅ SPECIAL CASE MATCH:", destination, flush=True)

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
# NORMALIZE TEXT AI
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
    "δυο": 2, "δύο": 2, "δύο": 2, "two": 2,
    "τρια": 3, "τρία": 3, "three": 3,
    "τεσσερα": 4, "τέσσερα": 4, "four": 4,
    "πεντε": 5, "πέντε": 5, "five": 5,
    "εξι": 6, "έξι": 6, "six": 6,
    "επτα": 7, "εφτα": 7, "επτά": 7, "εφτά": 7, "seven": 7,
    "οκτω": 8, "οχτω": 8, "οκτώ": 8, "οχτώ": 8, "eight": 8,
    "εννια": 9, "εννιά": 9, "nine": 9,
    "δεκα": 10, "δέκα": 10, "ten": 10,
}
