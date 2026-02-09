from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv
import os
import pyodbc
import requests
import json
import re 

SYSTEM_PROMPT = (
    "You are a short, casual, friendly chatbot. "
    "Keep responses under 2–3 sentences. "
    "Be friendly and conversational."
)

SQL_PROMPT = """
You are an assistant that answers ONLY using database results.

Rules:
- Use ONLY the data provided.
- Do NOT invent companies or people.
- If no data is found, say: "No records found."
- Keep answers short and factual.
"""


OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/chat")
MODEL_NAME = os.getenv("MODEL_NAME", "tinyllama")

# Load .env
load_dotenv()

app = Flask(__name__)

# Get DB connection
conn_str = os.getenv("DB_CONN")


def get_db_connection():
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
        "model": "tinyllama",
        "messages": messages,
        "stream": False
    }

    r = requests.post(
        "http://localhost:11434/api/chat",
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

def run_sql(sql):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(sql)
    cols = [c[0] for c in cur.description]
    rows = cur.fetchall()
    conn.close()
    return [dict(zip(cols,r)) for r in rows]




@app.route("/api/chat", methods=["POST"])
def api_chat():

    data = request.get_json() or {}
    question = (data.get("message") or "").strip()

    if not question:
        return jsonify({"reply":"Ask me something 🙂"})

    # ---- Step 1: Ask LLM for SQL ----
    planner = [
        {"role":"system","content":SQL_PROMPT},
        {"role":"user","content":question}
    ]

    r = requests.post(OLLAMA_URL, json={
        "model": MODEL_NAME,
        "messages": planner,
        "stream": False
    })

    plan = r.json()["message"]["content"]

    try:
        sql = json.loads(plan)["sql"]
    except:
        return jsonify({"reply":"I couldn't understand that query."})

    if not safe_sql(sql):
        return jsonify({"reply":"Unsafe query blocked."})

    # ---- Step 2: Run DB ----
    results = run_sql(sql)

    if not results:
        return jsonify({"reply":"No results found."})

    # ---- Step 3: Summarize with LLM ----
    context = "\n".join(str(r) for r in results[:20])

    final_prompt = [
        {"role":"system","content":SYSTEM_PROMPT},
        {"role":"system","content":"Use ONLY the data below."},
        {"role":"system","content":context},
        {"role":"user","content":question}
    ]

    r2 = requests.post(OLLAMA_URL, json={
        "model": MODEL_NAME,
        "messages": final_prompt,
        "stream": False
    })

    answer = r2.json()["message"]["content"]

    return jsonify({"reply":answer})
    
    

if __name__ == "__main__":
    app.run(debug=True)


