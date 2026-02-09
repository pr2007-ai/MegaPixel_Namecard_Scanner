from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv
import os
import pyodbc
import requests
import re

# ✅ Load .env FIRST (before any os.getenv calls)
load_dotenv()
print("ENV FILE LOADED")
print("OLLAMA_URL env =", os.getenv("OLLAMA_URL"))
print("MODEL_NAME env =", os.getenv("MODEL_NAME"))

conn_str = os.getenv("DB_CONN")
if not conn_str:
    raise RuntimeError("DB_CONN missing in .env")


app = Flask(__name__)

# ---------------- CONFIG ----------------

OLLAMA_URL = os.getenv("OLLAMA_URL") or "http://localhost:11434/api/chat"
MODEL_NAME = os.getenv("MODEL_NAME", "tinyllama:latest")


SYSTEM_PROMPT_TEXT = (
    "You are a short, casual, friendly chatbot. "
    "If structured database results are provided, answer using ONLY those results. "
    "Never invent database values. "
    "Keep responses under 2–3 sentences unless asked for more detail."
)

# ---------------- DB ----------------
def get_db_connection():
    return pyodbc.connect(conn_str)

def get_companies_by_industry(industry: str, limit: int = 200):
    sql = """
        SELECT DISTINCT TOP (?)
            [Office Name]
        FROM dbo.BusinessCards
        WHERE LOWER(LTRIM(RTRIM([Industry]))) = LOWER(?)
          AND [Office Name] IS NOT NULL
          AND LTRIM(RTRIM([Office Name])) <> ''
        ORDER BY [Office Name];
    """
    conn = get_db_connection()
    cur = conn.cursor()
    rows = cur.execute(sql, limit, industry).fetchall()
    conn.close()
    return [r[0] for r in rows]

def extract_industry(message: str):
    # matches: "in technology", "under technology", "for technology", "category technology"
    m = re.search(r"(?:under|in|for|category)\s+([a-zA-Z &/-]+)", message.lower())
    if m:
        return m.group(1).strip().title()
    return None

# ---------------- OLLAMA ----------------
def ask_ollama(user_msg: str, context: str | None = None) -> str:
    messages = [{"role": "system", "content": SYSTEM_PROMPT_TEXT}]
    if context:
        messages.append({"role": "system", "content": f"Database results:\n{context}"})
    messages.append({"role": "user", "content": user_msg})

    payload = {"model": MODEL_NAME, "messages": messages, "stream": False}

    r = requests.post(OLLAMA_URL, json=payload, timeout=60)  # ✅ THIS LINE
    r.raise_for_status()
    return r.json()["message"]["content"].strip()


# ---------------- ROUTES ----------------
@app.route("/")
def home():
    return render_template("index.html")

@app.route("/index.html")
def index():
    return render_template("index.html")

@app.route("/info.html")
def info():
    return render_template("info.html")

@app.route("/chat.html")
def chat():
    return render_template("chat.html")

@app.route("/upload.html")
def upload():
    return render_template("upload.html")

@app.route("/test-db")
def test_db():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT @@VERSION;")
        row = cursor.fetchone()
        conn.close()
        return f"Connected!<br><br>{row[0]}"
    except Exception as e:
        return f"Error: {e}"

@app.route("/submit-contact", methods=["POST"])
def submit_contact():
    try:
        data = request.get_json() or {}

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO dbo.BusinessCards
            (
                [First Name],
                [Last Name],
                [Job Title],
                [Office Email],
                [Private Email],
                [Office Name],
                [Number],
                [Industry]
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data.get("firstName"),
            data.get("lastName"),
            data.get("jobTitle"),
            data.get("officeEmail"),
            data.get("privateEmail"),
            data.get("officeName"),
            data.get("phoneNumber"),
            data.get("industry")
        ))

        conn.commit()
        conn.close()

        return jsonify({"ok": True, "message": "Saved to database!"})

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/chat", methods=["POST"])
def api_chat():
    try:
        data = request.get_json() or {}
        user_msg = (data.get("message") or "").strip()
        if not user_msg:
            return jsonify({"reply": "Ask me something 🙂"})

        lower = user_msg.lower()

        # ✅ DB-only: companies by industry
        if ("company" in lower or "companies" in lower) and any(w in lower for w in ["industry", "under", " in "]):
            industry = extract_industry(user_msg) or user_msg.split()[-1].title()

            companies = get_companies_by_industry(industry)

            if not companies:
                return jsonify({"reply": f"No companies found under '{industry}'."})

            shown = companies[:20]
            reply = f"{len(companies)} companies in {industry}:\n" + "\n".join(f"- {c}" for c in shown)
            if len(companies) > 20:
                reply += "\n\n(Type 'more' to see the rest.)"

            return jsonify({"reply": reply})

        # fallback: normal chat (no DB)
        reply = ask_ollama(user_msg)
        return jsonify({"reply": "I can only answer using the database. Please ask about stored contacts or companies."})

    except Exception as e:
        # always return JSON so your frontend won't crash
        return jsonify({"reply": f"Server error: {str(e)}"}), 500

if __name__ == "__main__":
    app.run(debug=True)
