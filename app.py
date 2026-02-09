from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv
import os
import pyodbc
import requests
import json
import re 

# Load .env FIRST
load_dotenv()

# System prompt must be a STRING (not dict)
SYSTEM_PROMPT = (
    "You are a short, casual, friendly chatbot. "
    "Read the user's input, infer intent, and respond naturally. "
    "Keep responses under 2–3 sentences unless asked for more detail. "
    "If the user expresses distress, respond with empathy first. "
    "Do NOT write biographies, long posts, tutorials, code examples, or instructions. "
    "Always be friendly, concise, and conversational. "
    "Never start responses with 'Dear', 'Certainly', 'I hope this', 'As a chatbot', or 'Here is an example'."
)

# Environment variables
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/chat")
MODEL_NAME = os.getenv("MODEL_NAME", "tinyllama")

# Database connection string
conn_str = os.getenv("DB_CONN")


# Load .env
load_dotenv()

app = Flask(__name__)

# Get DB connection
conn_str = os.getenv("DB_CONN")


def get_db_connection():
    if not conn_str:
        raise RuntimeError("DB_CONN is not set in .env file")
    return pyodbc.connect(conn_str)



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
        data = request.get_json()

        first_name = data.get("firstName")
        last_name = data.get("lastName")
        job_title = data.get("jobTitle")
        office_email = data.get("officeEmail")
        private_email = data.get("privateEmail")
        office_name = data.get("officeName")
        phone_number = data.get("phoneNumber")
        industry = data.get("industry")
        company_logo = data.get("companyLogo")

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
            first_name,
            last_name,
            job_title,
            office_email,
            private_email,
            office_name,
            phone_number,
            industry
        ))

        conn.commit()
        conn.close()

        return jsonify({"ok": True, "message": "Saved to database!"})

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

def ask_ollama(user_msg, history=None):

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_msg}
    ]

    payload = {
        "model": MODEL_NAME,
        "messages": messages,
        "stream": False
    }

    r = requests.post(
        OLLAMA_URL,
        json=payload,
        timeout=60
    )

    r.raise_for_status()
    return r.json()["message"]["content"]


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
    m = re.search(
        r"(?:under|in|for|category)\s+([a-zA-Z &/-]+)",
        message.lower()
    )

    if m:
        return m.group(1).strip().title()

    return None

def safe_sql(sql):
    s = sql.lower()
    return s.startswith("select") and not any(
        w in s for w in ["insert","delete","update","drop","alter","exec"]
    )


@app.route("/api/chat", methods=["POST"])
def api_chat():

    try:
        data = request.get_json() or {}
        user_msg = (data.get("message") or "").strip()

        if not user_msg:
            return jsonify({"reply": "Ask me something 🙂"})

        lower = user_msg.lower()

        # ✅ DB-only: companies by industry
        if ("company" in lower or "companies" in lower) and (
            "industry" in lower or "under" in lower or " in " in lower
        ):

            industry = extract_industry(user_msg)

            # Fallback: last word
            if not industry:
                industry = user_msg.split()[-1].title()

            companies = get_companies_by_industry(industry)

            if not companies:
                return jsonify({
                    "reply": f"No companies found under '{industry}'."
                })

            # Show first 20 only
            shown = companies[:20]

            reply = (
                f"{len(companies)} companies in {industry}:\n" +
                "\n".join(f"- {c}" for c in shown)
            )

            if len(companies) > 20:
                reply += "\n\nType 'more' to see the rest."

            return jsonify({"reply": reply})

        # ✅ Normal chat (no DB)
        reply = ask_ollama(user_msg)
        return jsonify({"reply": reply})

    except Exception as e:
        print("ERROR:", e)
        return jsonify({"reply": "Something went wrong. Check server logs."}), 500



if __name__ == "__main__":
    app.run(debug=True)