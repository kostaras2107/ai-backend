import unicodedata
import requests
import os

def full_conversation(history):
    texts = []
    for msg in history:
        if msg.get("isUser") and msg.get("text"):
            texts.append(msg.get("text"))
    return " ".join(texts)


def get_last_user_text(history):
    for msg in reversed(history):
        if msg.get("isUser") and msg.get("text"):
            return msg.get("text")
    return ""

def tokenize(text):
    text = normalize_text(text)
    return re.findall(r'\w+', text)

GREEK_NUMBERS = {
    "ένα":1,"ενα":1,
    "δύο":2,"δυο":2,
    "τρία":3,"τρια":3,
    "τέσσερα":4,"τεσσερα":4,
    "πέντε":5,"πεντε":5,
    "έξι":6,"εξι":6,
    "επτά":7,"επτα":7,
    "οκτώ":8,"οκτω":8,
    "εννέα":9,"εννεα":9,
    "δέκα":10,"δεκα":10
}

def normalize_text(text):
    if not text:
        return ""
    text = str(text).lower()
    text = unicodedata.normalize("NFD", text)
    text = text.encode("ascii", "ignore").decode("utf-8")
    return text

def web_search_context(query):

    url = "https://google.serper.dev/search"

    payload = {
        "q": query,
        "num": 10
    }

    headers = {
        "X-API-KEY": os.getenv("SERPER_API_KEY"),
        "Content-Type": "application/json"
    }

    try:
        r = requests.post(url, json=payload, headers=headers)
        print("SERPER STATUS:", r.status_code, flush=True)
        print("SERPER RESPONSE:", r.text[:500], flush=True)
        data = r.json()

        snippets = []

        for result in data.get("organic", [])[:10]:
            snippets.append(result.get("snippet", ""))

        return "\n".join(snippets)

    except:
        return ""
