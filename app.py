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

def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())

def split_name(text: str):
    """
    Extract name from:
    - 'industry of rachel sim'
    - 'email of rachel sim'
    - 'job title of rachel sim'
    Returns (first, last) or (None, None)
    """
    t = norm(text)

    m = re.search(r"(?:of|for)\s+([a-z]+)\s+([a-z]+)$", t)
    if m:
        return m.group(1).title(), m.group(2).title()

    parts = re.findall(r"[a-z]+", t)
    if len(parts) >= 2:
        return parts[-2].title(), parts[-1].title()

    return None, None


def db_contacts_by_company(company: str, limit: int = 50):
    sql = """
        SELECT TOP (?)
            [First Name], [Last Name], [Job Title], [Office Email], [Industry]
        FROM dbo.BusinessCards
        WHERE LOWER(LTRIM(RTRIM([Office Name]))) = LOWER(?)
        ORDER BY [Last Name], [First Name];
    """
    conn = get_db_connection()
    cur = conn.cursor()
    rows = cur.execute(sql, limit, company).fetchall()
    conn.close()
    return rows


def db_people_with_job_keyword(keyword: str, limit: int = 50):
    sql = """
        SELECT TOP (?)
            [First Name], [Last Name], [Job Title], [Office Name]
        FROM dbo.BusinessCards
        WHERE [Job Title] IS NOT NULL
          AND LOWER([Job Title]) LIKE ?
        ORDER BY [Last Name], [First Name];
    """
    like = f"%{keyword.lower()}%"
    conn = get_db_connection()
    cur = conn.cursor()
    rows = cur.execute(sql, limit, like).fetchall()
    conn.close()
    return rows


def db_person_field(first: str, last: str, column_sql: str):
    # column_sql is hardcoded by us (safe), not user input
    sql = f"""
        SELECT TOP 1 {column_sql}
        FROM dbo.BusinessCards
        WHERE LOWER([First Name]) = LOWER(?) AND LOWER([Last Name]) = LOWER(?);
    """
    conn = get_db_connection()
    cur = conn.cursor()
    row = cur.execute(sql, first, last).fetchone()
    conn.close()
    return row[0] if row else None

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

        t = norm(user_msg)

        # 1) Companies by industry
        if any(w in t for w in ["company", "companies"]) and any(w in t for w in ["industry", "under", " in ", "category"]):
            industry = extract_industry(user_msg) or user_msg.split()[-1].title()
            companies = get_companies_by_industry(industry)

            if not companies:
                return jsonify({"reply": f"No companies found under '{industry}'."})

            shown = companies[:20]
            reply = f"{len(companies)} companies in {industry}:\n" + "\n".join(f"- {c}" for c in shown)
            if len(companies) > 20:
                reply += "\n\n(Showing first 20. Ask: 'show more companies in {industry}')"
            return jsonify({"reply": reply})

        # 2) Contacts from a company: "show contacts from Megapixel"
        if "contact" in t and ("from" in t or "at" in t):
            m = re.search(r"(?:from|at)\s+(.+)$", t)
            company = (m.group(1).strip() if m else "").title()

            if not company:
                return jsonify({"reply": "Which company? Example: 'show contacts from Megapixel'."})

            rows = db_contacts_by_company(company)
            if not rows:
                return jsonify({"reply": f"No contacts found for '{company}'."})

            lines = []
            for fn, ln, jt, email, ind in rows[:20]:
                lines.append(f"- {fn} {ln} — {jt or 'No job title'} ({email or 'No email'})")

            reply = f"Contacts from {company}:\n" + "\n".join(lines)
            if len(rows) > 20:
                reply += "\n\n(Showing first 20.)"
            return jsonify({"reply": reply})

        # 3) Managers: "who are the Sales Managers" OR "job titles that have manager"
        if "manager" in t:
            keyword = "sales manager" if "sales" in t else "manager"
            rows = db_people_with_job_keyword(keyword)

            if not rows:
                return jsonify({"reply": f"No contacts found with job title containing '{keyword}'."})

            lines = [f"- {fn} {ln} — {jt} ({comp})" for fn, ln, jt, comp in rows[:20]]
            return jsonify({"reply": "Found:\n" + "\n".join(lines)})

        # 4) Person lookups: industry/email/job title/number of a person
        if "industry" in t and ("industry of" in t or "industry for" in t or "what is" in t):
            first, last = split_name(user_msg)
            if not first or not last:
                return jsonify({"reply": "Who’s the person? Example: 'industry of Rachel Sim'."})

            val = db_person_field(first, last, "[Industry]")
            if not val:
                return jsonify({"reply": f"No industry found for {first} {last}."})
            return jsonify({"reply": f"{first} {last} is in {val}."})

        if "email" in t and ("email of" in t or "email for" in t):
            first, last = split_name(user_msg)
            if not first or not last:
                return jsonify({"reply": "Who’s the person? Example: 'email of Rachel Sim'."})

            val = db_person_field(first, last, "[Office Email]") or db_person_field(first, last, "[Private Email]")
            if not val:
                return jsonify({"reply": f"No email found for {first} {last}."})
            return jsonify({"reply": f"{first} {last}'s email is {val}."})

        if any(k in t for k in ["job title", "role", "position"]) and ("of" in t or "for" in t):
            first, last = split_name(user_msg)
            if not first or not last:
                return jsonify({"reply": "Who’s the person? Example: 'job title of Rachel Sim'."})

            val = db_person_field(first, last, "[Job Title]")
            if not val:
                return jsonify({"reply": f"No job title found for {first} {last}."})
            return jsonify({"reply": f"{first} {last}'s job title is {val}."})

        if any(k in t for k in ["phone", "number", "contact"]) and ("of" in t or "for" in t):
            first, last = split_name(user_msg)
            if not first or not last:
                return jsonify({"reply": "Who’s the person? Example: 'number of Rachel Sim'."})

            val = db_person_field(first, last, "[Number]")
            if not val:
                return jsonify({"reply": f"No phone number found for {first} {last}."})
            return jsonify({"reply": f"{first} {last}'s number is {val}."})

        # ✅ DB-only fallback (NO OLLAMA to prevent hallucinations)
        return jsonify({
            "reply": (
                "I can only answer using your database. Try:\n"
                "- companies in Technology\n"
                "- show contacts from Megapixel\n"
                "- who are the Sales Managers\n"
                "- industry of Rachel Sim\n"
                "- email of Rachel Sim"
            )
        })

    except Exception as e:
        return jsonify({"reply": f"Server error: {str(e)}"}), 500
if __name__ == "__main__":
    app.run(debug=True)
