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

# ---------------- Pagination memory (simple) ----------------
LAST = {
    "items": [],
    "offset": 0,
    "page_size": 20,
    "label": ""
}

def set_last(items, label=""):
    LAST["items"] = items
    LAST["offset"] = 0
    LAST["label"] = label

def get_more():
    items = LAST["items"]
    if not items:
        return "Nothing to show more of yet. Ask something like 'companies in technology'."

    start = LAST["offset"]
    end = start + LAST["page_size"]
    chunk = items[start:end]
    LAST["offset"] = end

    msg = "\n".join(chunk)
    if end < len(items):
        msg += "\n\n(Type 'more' to see more.)"
    else:
        msg += "\n\n(That’s all.)"
    return msg

def is_more(text: str) -> bool:
    return norm(text) in ("more", "next", "show more", "more please")

# ---------------- More DB functions ----------------
def db_contacts_by_industry(industry: str, limit: int = 200):
    sql = """
        SELECT TOP (?)
            [First Name], [Last Name], [Job Title], [Office Name], [Office Email], [Industry]
        FROM dbo.BusinessCards
        WHERE [Industry] IS NOT NULL
          AND LTRIM(RTRIM([Industry])) <> ''
          AND LOWER(LTRIM(RTRIM([Industry]))) = LOWER(?)
        ORDER BY [Office Name], [Last Name], [First Name];
    """
    conn = get_db_connection()
    cur = conn.cursor()
    rows = cur.execute(sql, limit, industry).fetchall()
    conn.close()
    return rows

def db_list_industries(limit: int = 500):
    sql = """
        SELECT DISTINCT TOP (?)
            LTRIM(RTRIM([Industry])) AS Industry
        FROM dbo.BusinessCards
        WHERE [Industry] IS NOT NULL AND LTRIM(RTRIM([Industry])) <> ''
        ORDER BY Industry;
    """
    conn = get_db_connection()
    cur = conn.cursor()
    rows = cur.execute(sql, limit).fetchall()
    conn.close()
    return [r[0] for r in rows]

def db_list_companies(limit: int = 500):
    sql = """
        SELECT DISTINCT TOP (?)
            LTRIM(RTRIM([Office Name])) AS Company
        FROM dbo.BusinessCards
        WHERE [Office Name] IS NOT NULL AND LTRIM(RTRIM([Office Name])) <> ''
        ORDER BY Company;
    """
    conn = get_db_connection()
    cur = conn.cursor()
    rows = cur.execute(sql, limit).fetchall()
    conn.close()
    return [r[0] for r in rows]

def db_list_job_titles(limit: int = 500):
    sql = """
        SELECT DISTINCT TOP (?)
            LTRIM(RTRIM([Job Title])) AS JobTitle
        FROM dbo.BusinessCards
        WHERE [Job Title] IS NOT NULL AND LTRIM(RTRIM([Job Title])) <> ''
        ORDER BY JobTitle;
    """
    conn = get_db_connection()
    cur = conn.cursor()
    rows = cur.execute(sql, limit).fetchall()
    conn.close()
    return [r[0] for r in rows]

def db_search_name(term: str, limit: int = 50):
    sql = """
        SELECT TOP (?)
            [First Name], [Last Name], [Job Title], [Office Name], [Office Email], [Industry]
        FROM dbo.BusinessCards
        WHERE LOWER([First Name]) LIKE LOWER(?)
           OR LOWER([Last Name]) LIKE LOWER(?)
        ORDER BY [Last Name], [First Name];
    """
    like = f"%{term}%"
    conn = get_db_connection()
    cur = conn.cursor()
    rows = cur.execute(sql, limit, like, like).fetchall()
    conn.close()
    return rows

def db_missing(field: str, limit: int = 50):
    mapping = {
        "email": "[Office Email]",
        "phone": "[Number]",
        "job": "[Job Title]",
        "company": "[Office Name]",
        "industry": "[Industry]"
    }
    col = mapping[field]

    sql = f"""
        SELECT TOP (?)
            [First Name], [Last Name], [Job Title], [Office Name], [Office Email], [Industry]
        FROM dbo.BusinessCards
        WHERE {col} IS NULL OR LTRIM(RTRIM({col})) = ''
        ORDER BY [Last Name], [First Name];
    """
    conn = get_db_connection()
    cur = conn.cursor()
    rows = cur.execute(sql, limit).fetchall()
    conn.close()
    return rows

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

        # 0) "more" pagination
        if is_more(user_msg):
            return jsonify({"reply": get_more()})

        # A) list industries
        if t in ("industries", "list industries", "show industries"):
            industries = db_list_industries()
            lines = [f"- {x}" for x in industries]
            set_last(lines, "industries")
            reply = f"{len(lines)} industries:\n" + "\n".join(lines[:20])
            if len(lines) > 20:
                reply += "\n\n(Type 'more' to see more.)"
            return jsonify({"reply": reply})

        # B) list companies
        if t in ("companies", "list companies", "show companies"):
            comps = db_list_companies()
            lines = [f"- {x}" for x in comps]
            set_last(lines, "companies")
            reply = f"{len(lines)} companies:\n" + "\n".join(lines[:20])
            if len(lines) > 20:
                reply += "\n\n(Type 'more' to see more.)"
            return jsonify({"reply": reply})

        # C) list job titles
        if t in ("job titles", "list job titles", "show job titles"):
            titles = db_list_job_titles()
            lines = [f"- {x}" for x in titles]
            set_last(lines, "job titles")
            reply = f"{len(lines)} job titles:\n" + "\n".join(lines[:20])
            if len(lines) > 20:
                reply += "\n\n(Type 'more' to see more.)"
            return jsonify({"reply": reply})

        # D) contacts/names/people in industry: "names under retail", "people in technology"
        if any(k in t for k in ["contacts", "names", "people"]) and any(k in t for k in ["under", " in ", "industry", "category"]):
            industry = extract_industry(user_msg) or user_msg.split()[-1].title()
            rows = db_contacts_by_industry(industry)
            if not rows:
                return jsonify({"reply": f"No contacts found under '{industry}'."})

            lines = []
            for fn, ln, jt, comp, email, ind in rows:
                lines.append(f"- {fn} {ln} — {jt or 'No job title'} ({comp or 'No company'})")

            set_last(lines, f"contacts in {industry}")
            reply = f"{len(lines)} contacts in {industry}:\n" + "\n".join(lines[:20])
            if len(lines) > 20:
                reply += "\n\n(Type 'more' to see more.)"
            return jsonify({"reply": reply})

        # E) search by name: "find rachel", "search rachel"
        if t.startswith("find ") or t.startswith("search "):
            term = t.replace("find", "").replace("search", "").strip()
            if not term:
                return jsonify({"reply": "Search who? Example: 'find rachel'."})

            rows = db_search_name(term)
            if not rows:
                return jsonify({"reply": f"No contacts found matching '{term}'."})

            lines = []
            for fn, ln, jt, comp, email, ind in rows:
                lines.append(f"- {fn} {ln} — {jt or 'No job title'} ({comp or 'No company'})")

            set_last(lines, f"search {term}")
            reply = f"{len(lines)} matches for '{term}':\n" + "\n".join(lines[:20])
            if len(lines) > 20:
                reply += "\n\n(Type 'more' to see more.)"
            return jsonify({"reply": reply})

        # F) missing fields: "missing email", "no phone", "missing job title"
        if "missing" in t or "no " in t:
            if "email" in t:
                rows = db_missing("email")
                lines = [f"- {fn} {ln} ({comp or 'No company'})" for fn, ln, jt, comp, email, ind in rows]
                set_last(lines, "missing email")
                return jsonify({"reply": f"{len(lines)} contacts missing email:\n" + "\n".join(lines[:20]) + ("\n\n(Type 'more' to see more.)" if len(lines) > 20 else "")})

            if "phone" in t or "number" in t:
                rows = db_missing("phone")
                lines = [f"- {fn} {ln} ({comp or 'No company'})" for fn, ln, jt, comp, email, ind in rows]
                set_last(lines, "missing phone")
                return jsonify({"reply": f"{len(lines)} contacts missing phone:\n" + "\n".join(lines[:20]) + ("\n\n(Type 'more' to see more.)" if len(lines) > 20 else "")})

            if "job" in t or "title" in t:
                rows = db_missing("job")
                lines = [f"- {fn} {ln} ({comp or 'No company'})" for fn, ln, jt, comp, email, ind in rows]
                set_last(lines, "missing job title")
                return jsonify({"reply": f"{len(lines)} contacts missing job title:\n" + "\n".join(lines[:20]) + ("\n\n(Type 'more' to see more.)" if len(lines) > 20 else "")})

            if "company" in t:
                rows = db_missing("company")
                lines = [f"- {fn} {ln} ({jt or 'No job title'})" for fn, ln, jt, comp, email, ind in rows]
                set_last(lines, "missing company")
                return jsonify({"reply": f"{len(lines)} contacts missing company:\n" + "\n".join(lines[:20]) + ("\n\n(Type 'more' to see more.)" if len(lines) > 20 else "")})

            if "industry" in t:
                rows = db_missing("industry")
                lines = [f"- {fn} {ln} ({comp or 'No company'})" for fn, ln, jt, comp, email, ind in rows]
                set_last(lines, "missing industry")
                return jsonify({"reply": f"{len(lines)} contacts missing industry:\n" + "\n".join(lines[:20]) + ("\n\n(Type 'more' to see more.)" if len(lines) > 20 else "")})


        # ✅ DB-only fallback (NO OLLAMA to prevent hallucinations)
        return jsonify({
            "reply": (
                "I can only answer using your database. Try:\n"
                "- companies in Technology\n"
                "- contacts in Technology\n"
                "- show contacts from Megapixel\n"
                "- who are the Sales Managers\n"
                "- industry of Rachel Sim\n"
                "- email of Rachel Sim\n"
                "- list industries\n"
                "- list companies\n"
                "- list job titles\n"
                "- find rachel\n"
                "- missing email\n"
                "- more"
            )
        })

    except Exception as e:
        return jsonify({"reply": f"Server error: {str(e)}"}), 500
if __name__ == "__main__":
    app.run(debug=True)
