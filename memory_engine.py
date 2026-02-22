# ==============================
# MEMORY ENGINE for GorealAI
# ==============================

from typing import Dict, Any
from firebase_admin import firestore
from openai import OpenAI
import os

# --- Init clients ---
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
def get_db():
    return firestore.client()


# ==============================
# 1. LOAD USER MEMORY
# ==============================
def load_user_memory(user_id: str) -> Dict[str, Any]:
    """Fetch stored smart memory for a user"""
    if not user_id:
        return {}

    doc = get_db().collection("user_memory").document(user_id).get()

    if doc.exists:
        return doc.to_dict()

    return {}


# ==============================
# 2. SAVE / UPDATE MEMORY
# ==============================
def save_user_memory(user_id: str, new_data: Dict[str, Any]):
    """Merge new learned data into Firestore"""
    if not user_id or not new_data:
        return

    get_db().collection("user_memory").document(user_id).set(new_data, merge=True)


# ==============================
# 3. AI EXTRACT PREFERENCES
# ==============================
def extract_preferences_from_text(text: str) -> Dict[str, Any]:
    """Use AI to detect permanent user preferences"""
    if not text:
        return {}

    prompt = f"""
Διάβασε το μήνυμα χρήστη και βρες ΜΟΝΟ μόνιμες πληροφορίες:
- budget
- brand
- πόλη
- κατηγορία ενδιαφέροντος

Αν δεν υπάρχει κάτι → γράψε NONE.

Μήνυμα:
{text}
"""

    completion = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "system", "content": prompt}],
        temperature=0
    )

    result = completion.choices[0].message.content.strip()

    if result == "NONE":
        return {}

    return {"preferences": result}


# ==============================
# 4. UPDATE MEMORY AFTER MESSAGE
# ==============================
def update_memory_from_message(user_id: str, text: str):
    """Load → extract → save → return memory"""
    memory = load_user_memory(user_id)
    new_data = extract_preferences_from_text(text)

    if new_data:
        memory.update(new_data)
        save_user_memory(user_id, memory)

    return memory


# ==============================
# 5. INJECT MEMORY INTO PROMPT
# ==============================
def inject_memory_into_prompt(conversation: str, memory: Dict[str, Any]) -> str:
    """Attach memory before sending to AI"""
    if not memory:
        return conversation

    memory_text = f"""
ΜΝΗΜΗ ΧΡΗΣΤΗ:
{memory}
"""

    return memory_text + "\n\n" + conversation