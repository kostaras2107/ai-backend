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

    # =====================================
    # ΕΛΛΑΔΑ
    # =====================================

    "αθηνα": "athens", "αθήνα": "athens", "αττικη": "athens", "αττική": "athens",
    "πειραιας": "athens", "πειραιάς": "athens", "γλυφαδα": "athens", "γλυφάδα": "athens",
    "βουλιαγμενη": "athens", "βουλιαγμένη": "athens", "κηφισια": "athens", "κηφισιά": "athens",
    "μαρουσι": "athens", "μαρούσι": "athens", "χαλανδρι": "athens", "χαλάνδρι": "athens",
    "λαυριο": "athens", "λαύριο": "athens", "μαραθωνας": "athens", "μαραθώνας": "athens",
    "ραφηνα": "athens", "ραφήνα": "athens", "ελευσινα": "athens", "ελευσίνα": "athens",
    "μεγαρα": "athens", "μέγαρα": "athens", "θεσσαλονικη": "thessaloniki",
    "θεσσαλονίκη": "thessaloniki", "σαλονικη": "thessaloniki", "σαλονίκη": "thessaloniki",
    "κρητη": "crete island", "κρήτη": "crete island", "ηρακλειο": "heraklion",
    "ηράκλειο": "heraklion", "χανια": "chania", "χανιά": "chania",
    "ρεθυμνο": "rethymno", "ρέθυμνο": "rethymno", "σητεια": "sitia", "σητεία": "sitia",
    "ιεραπετρα": "ierapetra", "ιεράπετρα": "ierapetra", "ελουντα": "elounda", "ελούντα": "elounda",
    "ροδος": "rhodes", "ρόδος": "rhodes", "κως": "kos island",
    "πατμος": "patmos", "πάτμος": "patmos", "καλυμνος": "kalymnos", "κάλυμνος": "kalymnos",
    "συμη": "symi", "σύμη": "symi", "καρπαθος": "karpathos", "κάρπαθος": "karpathos",
    "λινδος": "lindos", "λίνδος": "lindos",
    "σαντορινη": "santorini", "σαντορίνη": "santorini", "θηρα": "santorini", "θήρα": "santorini",
    "φηρα": "santorini", "φήρα": "santorini",
    "μυκονος": "mykonos", "μύκονος": "mykonos",
    "παρος": "paros island", "πάρος": "paros island",
    "ναξος": "naxos island", "νάξος": "naxos island",
    "ιος": "ios chora", "ίος": "ios chora",
    "μηλος": "milos island", "μήλος": "milos island",
    "σιφνος": "sifnos", "σίφνος": "sifnos",
    "ανδρος": "andros", "άνδρος": "andros",
    "τηνος": "tinos", "τήνος": "tinos",
    "συρος": "syros", "σύρος": "syros",
    "φολεγανδρος": "folegandros", "φολέγανδρος": "folegandros",
    "αντιπαρος": "antiparos", "αντίπαρος": "antiparos",
    "κερκυρα": "corfu island", "κέρκυρα": "corfu island",
    "κορφος": "corfu island", "κόρφος": "corfu island",
    "κεφαλονια": "kefalonia", "κεφαλονιά": "kefalonia",
    "λευκαδα": "lefkada", "λευκάδα": "lefkada",
    "ζακυνθος": "zakynthos island", "ζάκυνθος": "zakynthos island",
    "ζακυνθη": "zakynthos island", "ζάκυνθη": "zakynthos island",
    "παξοι": "paxos", "πάξοι": "paxos",
    "σκιαθος": "skiathos island", "σκίαθος": "skiathos island",
    "σκοπελος": "skopelos", "σκόπελος": "skopelos",
    "λεσβος": "lesbos", "λέσβος": "lesbos",
    "μυτιληνη": "mytilene", "μυτιλήνη": "mytilene",
    "χιος": "chios", "χίος": "chios",
    "σαμος": "samos island", "σάμος": "samos island",
    "θασος": "thassos", "θάσος": "thassos",
    "αιγινα": "aegina", "αίγινα": "aegina",
    "υδρα": "hydra", "ύδρα": "hydra",
    "σπετσες": "spetses", "σπέτσες": "spetses",
    "ναυπλιο": "nafplion", "ναύπλιο": "nafplion", "ναυπλιον": "nafplion",
    "επιδαυρος": "panagia (epidaurus)", "επίδαυρος": "panagia (epidaurus)",
    "καλαματα": "kalamata", "καλαμάτα": "kalamata",
    "μονεμβασια": "monemvasia", "μονεμβασιά": "monemvasia",
    "γυθειο": "gythio", "γύθειο": "gythio",
    "αρεοπολη": "areopoli", "αρεόπολη": "areopoli",
    "πυλος": "pylos", "πύλος": "pylos",
    "ολυμπια": "olympia", "ολυμπία": "olympia", "αρχαια ολυμπια": "olympia",
    "τριπολη": "tripolis", "τρίπολη": "tripolis",
    "αργος": "argos", "άργος": "argos",
    "λεωνιδιο": "leonidion", "λεωνίδιο": "leonidion",
    "δημητσανα": "dimitsana", "δημητσάνα": "dimitsana",
    "στεμνιτσα": "stemnitsa", "στεμνίτσα": "stemnitsa",
    "λουτρακι": "loutraki", "λουτράκι": "loutraki",
    "κορινθος": "corinth", "κόρινθος": "corinth",
    "δελφοι": "delphi", "δελφοί": "delphi",
    "αραχωβα": "arachova", "αράχωβα": "arachova",
    "ναυπακτος": "nafpaktos", "ναύπακτος": "nafpaktos",
    "μεσολογγι": "messolonghi", "μεσολόγγι": "messolonghi",
    "λαμια": "lamia", "λαμία": "lamia",
    "καρπενησι": "karpenision", "καρπενήσι": "karpenision",
    "ιωαννινα": "ioannina", "ιωάννινα": "ioannina",
    "ζαγοροχωρια": "zagori", "ζαγοροχώρια": "zagori",
    "ζαγορι": "zagori", "ζαγόρι": "zagori",
    "ζαγορια": "zagori", "ζαγόρια": "zagori",
    "μετσοβο": "metsovo", "μέτσοβο": "metsovo",
    "παργα": "parga", "πάργα": "parga",
    "πρεβεζα": "preveza", "πρέβεζα": "preveza",
    "αρτα": "arta", "άρτα": "arta",
    "ηγουμενιτσα": "igoumenitsa", "ηγουμενίτσα": "igoumenitsa",
    "βολος": "volos", "βόλος": "volos",
    "πηλιο": "pelion", "πήλιο": "pelion",
    "μηλιες": "pelion", "μηλιές": "pelion",
    "λαρισα": "larissa", "λάρισα": "larissa",
    "τρικαλα": "trikala", "τρίκαλα": "trikala",
    "μετεωρα": "meteora", "μετέωρα": "meteora",
    "καλαμπακα": "kalampaka", "καλαμπάκα": "kalampaka",
    "καρδιτσα": "karditsa", "καρδίτσα": "karditsa",
    "βεροια": "veria", "βέροια": "veria",
    "ναουσα": "naoussa", "νάουσα": "naoussa",
    "βεργινα": "vergina", "βεργίνα": "vergina",
    "κατερινη": "katerini", "κατερίνη": "katerini",
    "λιτοχωρο": "litochoron", "λιτόχωρο": "litochoron",
    "ολυμπος": "litochoron", "όλυμπος": "litochoron",
    "καβαλα": "kavala", "καβάλα": "kavala",
    "δραμα": "drama", "δράμα": "drama",
    "σερρες": "serres", "σέρρες": "serres",
    "κοζανη": "kozani", "κοζάνη": "kozani",
    "φλωρινα": "florina", "φλώρινα": "florina",
    "καστορια": "kastoria", "καστοριά": "kastoria",
    "αλεξανδρουπολη": "alexandroupolis", "αλεξανδρούπολη": "alexandroupolis",
    "ξανθη": "xanthi", "ξάνθη": "xanthi",
    "κομοτηνη": "komotini", "κομοτηνή": "komotini",
    "εδεσσα": "edessa", "έδεσσα": "edessa",
    "πατρα": "patras", "πάτρα": "patras",
    "αγρινιο": "agrinio", "αίγιο": "aigio",
    "λεμεσος": "limassol", "λεμεσός": "limassol",
    "λευκωσια": "nicosia", "λευκωσία": "nicosia",
    "λαρνακα": "larnaca", "λάρνακα": "larnaca",
    "παφος": "paphos", "πάφος": "paphos",
    "αγια ναπα": "ayia napa", "αγία νάπα": "ayia napa",
    "πρωταρας": "protaras", "πρωτάρας": "protaras",

    # =====================================
    # ΕΥΡΩΠΗ
    # =====================================

    # Ιταλία
    "ρωμη": "rome", "ρώμη": "rome",
    "βενετια": "venice", "βενετία": "venice",
    "φλωρεντια": "florence", "φλωρεντία": "florence",
    "μιλανο": "milan", "μιλάνο": "milan",
    "ναπολη": "naples", "νάπολη": "naples",
    "σορεντο": "sorrento", "σορέντο": "sorrento",
    "αμαλφι": "amalfi", "αμάλφι": "amalfi",
    "πορτοφινο": "portofino", "πορτοφίνο": "portofino",
    "ριμινι": "rimini", "ρίμινι": "rimini",
    "μπολονια": "bologna", "μπολόνια": "bologna",

    # Ισπανία
    "βαρκελωνη": "barcelona", "βαρκελώνη": "barcelona",
    "μαδριτη": "madrid", "μαδρίτη": "madrid",
    "σεβιλλη": "seville", "σεβίλλη": "seville",
    "γρανάδα": "granada",
    "κορδοβα": "cordoba", "κόρδοβα": "cordoba",
    "τολεδο": "toledo", "τολέδο": "toledo",
    "βαλενθια": "valencia", "βαλένθια": "valencia",
    "μαλαγα": "malaga", "μάλαγα": "malaga",
    "μαρμπεγια": "marbella", "μαρμπέγια": "marbella",
    "ιμπιθα": "ibiza", "ίμπιθα": "ibiza",
    "μαγιορκα": "mallorca", "μαγιόρκα": "mallorca",
    "τενεριφη": "tenerife", "τενερίφη": "tenerife",
    "λανθαροτε": "lanzarote", "λανζαρότε": "lanzarote",
    "μπιλμπαο": "bilbao", "μπιλμπάο": "bilbao",
    "σαν σεμπαστιαν": "san sebastian",
    "φουενχιρολα": "fuengirola", "φουενχιρόλα": "fuengirola",
    "νερχα": "nerja", "τορεμολινος": "torremolinos",

    # Γαλλία
    "παρισι": "paris", "παρίσι": "paris",
    "νιτσα": "nice", "νίτσα": "nice",
    "μονακο": "monaco", "μονάκο": "monaco",

    # Αγγλία
    "λονδινο": "london", "λονδίνο": "london",
    "μαντσεστερ": "manchester", "μάντσεστερ": "manchester",
    "εδιμβουργο": "edinburgh", "εδιμβούργο": "edinburgh",
    "λιβερπουλ": "liverpool", "λίβερπουλ": "liverpool",

    # Αυστρία
    "βιεννη": "vienna", "βιέννη": "vienna",
    "σαλτσβουργο": "salzburg", "σάλτσβουργκ": "salzburg",
    "ινσμπρουκ": "innsbruck", "γκρατς": "graz",

    # Γερμανία
    "βερολινο": "berlin", "βερολίνο": "berlin",
    "μοναχο": "munich", "μόναχο": "munich",
    "φρανκφουρτη": "frankfurt", "φρανκφούρτη": "frankfurt",
    "αμβουργο": "hamburg", "αμβούργο": "hamburg",

    # Ολλανδία - Βέλγιο
    "αμστερνταμ": "amsterdam", "άμστερνταμ": "amsterdam",
    "βρυξελλες": "brussels", "βρυξέλλες": "brussels",
    "μπρυζ": "bruges", "γεντ": "ghent", "αντβερπεν": "antwerp",

    # Ελβετία
    "ζυριχη": "zurich", "ζυρίχη": "zurich",
    "γενευη": "geneva", "γενεύη": "geneva",
    "βερνη": "bern", "βέρνη": "bern",
    "λουγκανο": "lugano",

    # Πορτογαλία
    "λισσαβονα": "lisbon", "λισσαβώνα": "lisbon", "λισαβονα": "lisbon",
    "πορτο": "porto", "πόρτο": "porto",
    "αλμπουφειρα": "albufeira", "αλμπουφέιρα": "albufeira",
    "σιντρα": "sintra", "σίντρα": "sintra",
    "κασκαης": "cascais", "κάσκαης": "cascais",
    "λαγκος": "lagos", "φαρο": "faro", "φάρο": "faro",
    "βιλαμουρα": "vilamoura", "βιλαμούρα": "vilamoura",
    "ταβιρα": "tavira", "ταβίρα": "tavira",
    "εβορα": "evora", "έβορα": "evora",
    "κοιμπρα": "coimbra", "μπραγκα": "braga",
    "μαδειρα": "madeira island", "μαδέιρα": "madeira island",

    # Σκανδιναβία
    "στοκχολμη": "stockholm", "στοκχόλμη": "stockholm",
    "κοπεγχαγη": "copenhagen", "κοπεγχάγη": "copenhagen",
    "οσλο": "oslo", "όσλο": "oslo",
    "ελσινκι": "helsinki", "ελσίνκι": "helsinki",
    "ρεικιαβικ": "reykjavik", "ρέικιαβικ": "reykjavik",

    # Ανατολική Ευρώπη
    "πραγα": "prague", "πράγα": "prague",
    "βουδαπεστη": "budapest", "βουδαπέστη": "budapest",
    "βαρσοβια": "warsaw", "βαρσοβία": "warsaw",
    "κρακοβια": "krakow", "κρακοβία": "krakow",
    "βουκουρεστι": "bucharest", "βουκουρέστι": "bucharest",
    "σοφια": "sofia", "σόφια": "sofia",
    "βελιγραδι": "belgrade", "βελιγράδι": "belgrade",
    "σαραγεβο": "sarajevo", "σαράγεβο": "sarajevo",
    "ζαγκρεμπ": "zagreb", "ζάγκρεμπ": "zagreb",
    "σπλιτ": "split",
    "ντουμπροβνικ": "dubrovnik", "ντουμπρόβνικ": "dubrovnik",
    "κοτορ": "kotor", "κότορ": "kotor",
    "σκοπια": "skopje", "σκόπια": "skopje",
    "τιρανα": "tirana", "τίρανα": "tirana",
    "ταλιν": "tallinn", "τάλιν": "tallinn",
    "ριγα": "riga", "ρίγα": "riga",
    "βιλνιους": "vilnius", "βίλνιους": "vilnius",
    "βαλεττα": "valletta", "βαλέττα": "valletta",

    # =====================================
    # ΜΕΣΗ ΑΝΑΤΟΛΗ & ΑΦΡΙΚΗ
    # =====================================

    # Τουρκία
    "κωνσταντινουπολη": "istanbul", "κωνσταντινούπολη": "istanbul",
    "ιστανμπουλ": "istanbul", "ιστανμπούλ": "istanbul",
    "ανταλια": "antalya", "ανταλία": "antalya",
    "μποντρουμ": "bodrum", "μπόντρουμ": "bodrum",
    "αλανια": "alanya", "αλάνια": "alanya",
    "φεθιγε": "fethiye", "φεθιγέ": "fethiye",
    "κουσαντασι": "kusadasi", "κουσαντασί": "kusadasi",
    "μαρμαρης": "marmaris", "μαρμαρής": "marmaris",
    "κας": "kas",
    "καππαδοκια": "cappadocia", "καππαδοκία": "cappadocia",
    "παμουκαλε": "pamukkale", "παμούκαλε": "pamukkale",
    "γκορεμε": "goreme", "γκορέμε": "goreme",
    "ουργκουπ": "urgup", "ουργκούπ": "urgup",
    "νταλαμαν": "dalaman",

    # Εμιράτα & Μέση Ανατολή
    "ντουμπαι": "dubai", "ντουμπάι": "dubai",
    "ιερουσαλημ": "jerusalem", "ιερουσαλήμ": "jerusalem",
    "τελ αβιβ": "tel aviv",
    "πετρα": "petra", "πέτρα": "petra",
    "ακαμπα": "aqaba", "ακάμπα": "aqaba",
    "σαρμ ελ σεϊχ": "sharm el sheikh",
    "χουργκαντα": "hurghada", "χουργκάντα": "hurghada",
    "λουξορ": "luxor", "λούξορ": "luxor",
    "καιρο": "cairo", "καΐρο": "cairo",

    # Αφρική
    "μαρακες": "marrakech", "μαρακές": "marrakech",
    "καζαμπλανκα": "casablanca", "καζαμπλάνκα": "casablanca",
    "τυνιδα": "tunis", "τύνιδα": "tunis",
    "ζανζιβαρη": "zanzibar", "ζανζιβάρη": "zanzibar",
    "ναϊρομπι": "nairobi", "ναϊρόμπι": "nairobi",
    "καπσταντ": "cape town", "κέιπ τάουν": "cape town",

    # =====================================
    # ΑΣΙΑ
    # =====================================

    # Ιαπωνία
    "τοκιο": "tokyo", "τόκιο": "tokyo",
    "κιοτο": "kyoto", "κιότο": "kyoto",
    "οσακα": "osaka", "όσακα": "osaka",
    "κομπε": "kobe", "κόμπε": "kobe",
    "ναρα": "nara",

    # Ταϊλάνδη
    "μπανγκοκ": "bangkok", "μπανγκόκ": "bangkok",
    "φουκετ": "phuket", "φουκέτ": "phuket",
    "κραμπι": "krabi", "κράμπι": "krabi",
    "κο σαμουι": "koh samui",
    "κο φανγκαν": "ko pha-ngan",
    "παταγια": "pattaya", "πατάγια": "pattaya",

    # Βιετνάμ & ΝΑ Ασία
    "χο τσι μιν": "ho chi minh city",
    "ανοι": "hanoi", "ανόι": "hanoi",
    "χοι αν": "hoi an", "χόι αν": "hoi an",
    "ντα νανγκ": "danang",
    "σιεμ ριπ": "siem reap",
    "φνομ πεν": "phnom penh",
    "γιανγκον": "yangon",

    # Ινδία
    "νεο δελχι": "new delhi", "νέο δελχί": "new delhi",
    "μουμπαϊ": "mumbai", "βομβαη": "mumbai", "βομβάη": "mumbai",
    "γκοα": "goa", "γκόα": "goa",
    "τζαϊπουρ": "jaipur", "τζαϊπούρ": "jaipur",
    "αγρα": "agra", "άγρα": "agra",
    "κατμαντου": "kathmandu", "κατμαντού": "kathmandu",

    # Μαλαισία & Σιγκαπούρη
    "κουαλα λουμπουρ": "kuala lumpur", "κουάλα λουμπούρ": "kuala lumpur",
    "σιγκαπουρη": "singapore", "σιγκαπούρη": "singapore",
    "πεναγκ": "penang",
    "λανγκαουι": "langkawi", "λάνγκαουι": "langkawi",

    # Ινδονησία
    "μπαλι": "bali", "μπάλι": "bali",
    "λομποκ": "lombok", "κομοντο": "komodo",

    # Κίνα & Κορέα
    "πεκινο": "beijing", "πεκίνο": "beijing",
    "σαγκαη": "shanghai", "σαγκάη": "shanghai",
    "σεουλ": "seoul", "σεούλ": "seoul",
    "χονγκ κονγκ": "hong kong", "χόνγκ κόνγκ": "hong kong",
    "μακαο": "macau", "μακάο": "macau",

    # Σρι Λάνκα & Μαλδίβες
    "κολομβο": "colombo", "κολόμπο": "colombo",
    "μαλδιβες": "maldive islands", "μαλδίβες": "maldive islands",
    "μαλε": "male city and airport", "μάλε": "male city and airport",

    # =====================================
    # ΑΜΕΡΙΚΗ
    # =====================================

    "νεα υορκη": "new york (ny)", "νέα υόρκη": "new york (ny)",
    "νεω υορκη": "new york (ny)", "νέο υόρκη": "new york (ny)",
    "λος αντζελες": "los angeles (ca)", "λος άντζελες": "los angeles (ca)",
    "μαϊαμι": "miami (fl)", "μαϊάμι": "miami (fl)",
    "σικαγο": "chicago (il)", "σικάγο": "chicago (il)",
    "λας βεγκας": "las vegas (nv)", "λας βέγκας": "las vegas (nv)",
    "σαν φρανσισκο": "san francisco (ca)", "σαν φρανσίσκο": "san francisco (ca)",
    "ορλαντο": "orlando (fl)", "ορλάντο": "orlando (fl)",
    "χαβαη": "hawaii", "χαβάη": "hawaii",
    "καγκουν": "cancun", "κανκούν": "cancun",
    "τοροντο": "toronto (on)", "τορόντο": "toronto (on)",
    "μοντρεαλ": "montreal (qc)", "μόντρεαλ": "montreal (qc)",
    "ριο": "rio de janeiro", "ρίο": "rio de janeiro",
    "ριο ντε ζανεϊρο": "rio de janeiro", "ρίο ντε ζανέιρο": "rio de janeiro",
    "μπουενος αϊρες": "buenos aires", "μπουένος άιρες": "buenos aires",
    "μεξικο σιτι": "mexico city", "μεξικό σίτι": "mexico city",

    # =====================================
    # ΩΚΕΑΝΙΑ & ΝΗΣΙΑ
    # =====================================

    "συδνεϊ": "sydney", "σύδνεϊ": "sydney",
    "μελβουρνη": "melbourne", "μελβούρνη": "melbourne",
    "ταϊτη": "tahiti", "τάιτη": "tahiti",
    "μπορα μπορα": "bora bora island", "μπόρα μπόρα": "bora bora island",
    "σεϋχελλες": "seychelles islands", "σεϋχέλλες": "seychelles islands",
    "μαυρικιος": "mauritius island", "μαυρίκιος": "mauritius island",
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
        raw = destination.strip().lower()

        # 1️⃣ ΠΡΩΤΑ special cases με το raw input (ελληνικά)
        if raw in SPECIAL_CASES:
            destination = SPECIAL_CASES[raw]
            print("✅ SPECIAL CASE (raw):", destination, flush=True)
        else:
            # 2️⃣ AI normalize μόνο αν δεν βρέθηκε στα special cases
            destination = ai_normalize_destination(raw, client)
            destination = destination.strip().lower()
            print("🔍 AI NORMALIZED:", destination, flush=True)

            # 3️⃣ Ξαναέλεγξε special cases με το AI result
            # (π.χ. αν AI επέστρεψε "corfu island" ή "kerkyra")
            if destination in SPECIAL_CASES:
                destination = SPECIAL_CASES[destination]
                print("✅ SPECIAL CASE (ai):", destination, flush=True)

        print("🔍 LOOKING FOR:", destination, "in", len(_city_df), "cities", flush=True)

        # 4️⃣ Exact match στο city_index
        match = _city_df[_city_df["city"] == destination]
        if not match.empty:
            city_id = int(match.iloc[0]["city_id"])
            name = match.iloc[0]["city"]
            print("✅ EXACT MATCH:", name, city_id, flush=True)
            return {"city_id": city_id, "name": name}

        # 5️⃣ Fuzzy match
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

        # 6️⃣ Fallback
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
