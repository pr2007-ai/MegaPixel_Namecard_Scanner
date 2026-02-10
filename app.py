from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv
import os
import pyodbc
import requests
import re
from transformers import pipeline


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

# BERT-ish zero-shot classifier
ZSC_MODEL = os.getenv("ZSC_MODEL", "typeform/distilbert-base-uncased-mnli")
zsc = pipeline("zero-shot-classification", model=ZSC_MODEL)

INTENT_LABELS = [
    "companies_by_industry",
    "contacts_by_company",
    "contacts_by_industry",
    "person_email",
    "person_job_title",
    "person_phone",
    "person_industry",
    "people_by_job_keyword",
    "list_companies",
    "list_industries",
    "search_name",
    "missing_fields",
]

def classify_intent(user_msg: str):
    t = norm(user_msg)

    # fast rules
    if t.startswith(("find ", "search ")):
        return "search_name", 1.0
    if t.startswith(("hi", "hello", "hey")):
        return "greeting", 1.0
    if "missing" in t or t.startswith("no "):
        return "missing_fields", 1.0

    res = zsc(user_msg, INTENT_LABELS, multi_label=False)
    return res["labels"][0], float(res["scores"][0])



def extract_industry(message: str):
    t = norm(message)

    # common patterns
    patterns = [
        r"(?:people|contacts|names|companies)\s+(?:in|under|from)\s+([a-zA-Z &/-]+)$",
        r"(?:industry)\s*(?:is|=|:)?\s*([a-zA-Z &/-]+)$",
        r"(?:in|under|for|category)\s+([a-zA-Z &/-]+)$",
    ]

    for p in patterns:
        m = re.search(p, t, flags=re.IGNORECASE)
        if m:
            value = m.group(1).strip(" .").title()

            # block greetings being treated as an industry
            if value.lower() in {"hi", "hello", "hey"}:
                return None

            return value

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


def db_contacts_by_industry(industry: str, limit: int = 50):
    sql = """
        SELECT TOP (?)
            [First Name],
            [Last Name],
            [Job Title],
            [Office Name],
            [Office Email],
            [Industry]
        FROM dbo.BusinessCards
        WHERE LOWER(LTRIM(RTRIM([Industry]))) = LOWER(?)
        ORDER BY [Last Name], [First Name];
    """

    conn = get_db_connection()
    cur = conn.cursor()

    rows = cur.execute(sql, limit, industry).fetchall()

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

def db_list_companies(limit: int = 200):
    sql = """
        SELECT DISTINCT TOP (?)
            [Office Name]
        FROM dbo.BusinessCards
        WHERE [Office Name] IS NOT NULL
          AND LTRIM(RTRIM([Office Name])) <> ''
        ORDER BY [Office Name];
    """
    conn = get_db_connection()
    cur = conn.cursor()
    rows = cur.execute(sql, limit).fetchall()
    conn.close()
    return [r[0] for r in rows]


def db_list_industries(limit: int = 200):
    sql = """
        SELECT DISTINCT TOP (?)
            [Industry]
        FROM dbo.BusinessCards
        WHERE [Industry] IS NOT NULL
          AND LTRIM(RTRIM([Industry])) <> ''
        ORDER BY [Industry];
    """
    conn = get_db_connection()
    cur = conn.cursor()
    rows = cur.execute(sql, limit).fetchall()
    conn.close()
    return [r[0] for r in rows]


def db_list_job_titles(limit: int = 200):
    sql = """
        SELECT DISTINCT TOP (?)
            [Job Title]
        FROM dbo.BusinessCards
        WHERE [Job Title] IS NOT NULL
          AND LTRIM(RTRIM([Job Title])) <> ''
        ORDER BY [Job Title];
    """
    conn = get_db_connection()
    cur = conn.cursor()
    rows = cur.execute(sql, limit).fetchall()
    conn.close()
    return [r[0] for r in rows]


def db_search_name(term: str, limit: int = 50):
    # matches first name OR last name OR full name contains term
    like = f"%{term.lower()}%"
    sql = """
        SELECT TOP (?)
            [First Name], [Last Name], [Job Title],
            [Office Name], [Office Email], [Industry]
        FROM dbo.BusinessCards
        WHERE LOWER([First Name]) LIKE ?
           OR LOWER([Last Name]) LIKE ?
           OR LOWER(CONCAT([First Name], ' ', [Last Name])) LIKE ?
        ORDER BY [Last Name], [First Name];
    """
    conn = get_db_connection()
    cur = conn.cursor()
    rows = cur.execute(sql, limit, like, like, like).fetchall()
    conn.close()
    return rows


def db_missing(field: str, limit: int = 200):
    """
    field can be:
    - 'email' (both office + private missing)
    - 'office_email'
    - 'private_email'
    - 'phone'
    - 'job'
    - 'company'
    - 'industry'
    - 'publiclink'
    - 'qrlink'
    """
    field = (field or "").lower().strip()

    conditions = {
        "email": "([Office Email] IS NULL OR LTRIM(RTRIM([Office Email]))='') AND ([Private Email] IS NULL OR LTRIM(RTRIM([Private Email]))='')",
        "office_email": "([Office Email] IS NULL OR LTRIM(RTRIM([Office Email]))='')",
        "private_email": "([Private Email] IS NULL OR LTRIM(RTRIM([Private Email]))='')",
        "phone": "([Number] IS NULL OR LTRIM(RTRIM([Number]))='')",
        "job": "([Job Title] IS NULL OR LTRIM(RTRIM([Job Title]))='')",
        "company": "([Office Name] IS NULL OR LTRIM(RTRIM([Office Name]))='')",
        "industry": "([Industry] IS NULL OR LTRIM(RTRIM([Industry]))='')",
        "publiclink": "([PublicLink] IS NULL OR LTRIM(RTRIM([PublicLink]))='')",
        "qrlink": "([QR link] IS NULL OR DATALENGTH([QR link])=0)",
    }

    cond = conditions.get(field)
    if not cond:
        return []

    sql = f"""
        SELECT TOP (?)
            [First Name], [Last Name], [Job Title], [Office Name]
        FROM dbo.BusinessCards
        WHERE {cond}
        ORDER BY [Last Name], [First Name];
    """

    conn = get_db_connection()
    cur = conn.cursor()
    rows = cur.execute(sql, limit).fetchall()
    conn.close()
    return rows

JOB_STOPWORDS = {
    "who","are","the","all","list","show","me","find","give","people","contacts","names",
    "in","under","from","at","of","for","with"
}

def extract_job_keyword(message: str):
    t = norm(message)

    # try patterns first
    m = re.search(r"(?:who\s+are\s+the\s+|list\s+all\s+|show\s+)(.+)$", t)
    if m:
        phrase = m.group(1).strip(" .")
    else:
        phrase = t

    words = [w for w in re.findall(r"[a-z]+", phrase) if w not in JOB_STOPWORDS]

    if not words:
        return None

    # if user wrote "biomedical engineer", keep phrase instead of single token
    # pick up to 3 words to match titles like "sales manager", "biomedical engineer"
    return " ".join(words[:3]).strip()

def singularize(w: str) -> str:
    w = (w or "").strip().lower()
    if w.endswith("ies"):  # companies -> company
        return w[:-3] + "y"
    if w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w

def db_people_with_job_keyword(keyword: str, limit: int = 50):
    keyword = singularize(keyword)
    like = f"%{keyword}%"
    sql = """
        SELECT TOP (?)
            [First Name], [Last Name], [Job Title], [Office Name]
        FROM dbo.BusinessCards
        WHERE [Job Title] IS NOT NULL
          AND LOWER([Job Title]) LIKE ?
        ORDER BY [Last Name], [First Name];
    """
    conn = get_db_connection()
    cur = conn.cursor()
    rows = cur.execute(sql, limit, like).fetchall()
    conn.close()
    return rows

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

        t = norm(user_msg)  # ✅ you MUST define this
        intent, score = classify_intent(user_msg)

        # if model is unsure, don't run random SQL
        if score < 0.55:
            return jsonify({"reply": "I’m not sure what you mean. Try: 'list companies', 'people in Healthcare', 'contacts from Megapixel', or 'email of Rachel Sim'."})


        # --- BERT ROUTING ---
        if intent == "companies_by_industry":
            industry = extract_industry(user_msg) 
            companies = get_companies_by_industry(industry)
            if not companies:
                return jsonify({"reply": f"No companies found under '{industry}'."})
            shown = companies[:20]
            reply = f"{len(companies)} companies in {industry}:\n" + "\n".join(f"- {c}" for c in shown)
            return jsonify({"reply": reply})

        if intent == "contacts_by_company":
            m = re.search(r"(?:from|at)\s+(.+)$", t)
            company = (m.group(1).strip() if m else "").title()
            if not company:
                return jsonify({"reply": "Which company? Example: 'show contacts from Megapixel'."})

            rows = db_contacts_by_company(company)
            if not rows:
                return jsonify({"reply": f"No contacts found for '{company}'."})

            lines = [f"- {fn} {ln} — {jt or 'No job'} ({email or 'No email'})"
                     for fn, ln, jt, email, ind in rows[:20]]
            return jsonify({"reply": f"Contacts from {company}:\n" + "\n".join(lines)})

        if intent == "contacts_by_industry":
            industry = extract_industry(user_msg) 
            rows = db_contacts_by_industry(industry)
            if not rows:
                return jsonify({"reply": f"No contacts found in '{industry}'."})

            lines = [f"- {fn} {ln} — {jt or 'No job'} ({comp or 'No company'})"
                     for fn, ln, jt, comp, email, ind in rows[:20]]
            return jsonify({"reply": f"Contacts in {industry}:\n" + "\n".join(lines)})
        
        if intent == "people_by_job_keyword":
            keyword = extract_job_keyword(user_msg)
            if not keyword:
                return jsonify({"reply": "Which role? Example: 'who are engineers' or 'list sales managers'."})

            rows = db_people_with_job_keyword(keyword)
            if not rows:
                return jsonify({"reply": f"No contacts found with job title matching '{keyword}'."})

            lines = [f"- {fn} {ln} — {jt} ({comp})" for fn, ln, jt, comp in rows[:20]]
            return jsonify({"reply": f"People with job title matching '{keyword}':\n" + "\n".join(lines)})


        if intent in ("person_email", "person_job_title", "person_phone", "person_industry"):
            first, last = split_name(user_msg)
            if not first or not last:
                return jsonify({"reply": "Who’s the person? Example: 'email of Rachel Sim'."})

            if intent == "person_email":
                wants_office = ("office" in t) or ("work" in t)
                wants_private = ("private" in t) or ("personal" in t)

                office_email = db_person_field(first, last, "[Office Email]")
                private_email = db_person_field(first, last, "[Private Email]")

                if wants_office:
                    return jsonify({"reply": f"{first} {last}'s office email is {office_email or 'not found'}."})

                if wants_private:
                    return jsonify({"reply": f"{first} {last}'s private email is {private_email or 'not found'}."})

                # ✅ default return both (fixes your “can’t differentiate” issue)
                if office_email or private_email:
                    return jsonify({"reply": f"Office: {office_email or '—'}\nPrivate: {private_email or '—'}"})

                return jsonify({"reply": f"No email found for {first} {last}."})

            if intent == "person_job_title":
                jt = db_person_field(first, last, "[Job Title]")
                return jsonify({"reply": f"{first} {last}'s job title is {jt or 'not found'}."})

            if intent == "person_phone":
                num = db_person_field(first, last, "[Number]")
                return jsonify({"reply": f"{first} {last}'s number is {num or 'not found'}."})

            if intent == "person_industry":
                ind = db_person_field(first, last, "[Industry]")
                return jsonify({"reply": f"{first} {last} is in {ind or 'not found'}."})

        # Lists
        if intent == "list_companies":
            comps = db_list_companies()
            return jsonify({"reply": "Companies:\n" + "\n".join(f"- {c}" for c in comps[:30])})

        if intent == "list_industries":
            inds = db_list_industries()
            return jsonify({"reply": "Industries:\n" + "\n".join(f"- {i}" for i in inds[:30])})

        # Search
        if intent == "search_name":
            term = t.replace("find", "").replace("search", "").strip()
            rows = db_search_name(term)
            if not rows:
                return jsonify({"reply": f"No contacts found for '{term}'."})
            lines = [f"- {fn} {ln} — {jt or 'No job'} ({comp or 'No company'})"
                     for fn, ln, jt, comp, email, ind in rows[:20]]
            return jsonify({"reply": f"{len(rows)} matches:\n" + "\n".join(lines)})

        # Missing fields
        if intent == "missing_fields":
            if "public" in t and "link" in t:
                rows = db_missing("publiclink")
                return jsonify({"reply": f"{len(rows)} contacts are missing PublicLink."})

            if "email" in t:
                rows = db_missing("email")
                return jsonify({"reply": f"{len(rows)} contacts are missing BOTH office + private email."})

            if "office" in t and "email" in t:
                rows = db_missing("office_email")
                return jsonify({"reply": f"{len(rows)} contacts are missing office email."})

            if "private" in t and "email" in t:
                rows = db_missing("private_email")
                return jsonify({"reply": f"{len(rows)} contacts are missing private email."})

            if "phone" in t or "number" in t:
                rows = db_missing("phone")
                return jsonify({"reply": f"{len(rows)} contacts are missing phone number."})

            if "job" in t or "title" in t:
                rows = db_missing("job")
                return jsonify({"reply": f"{len(rows)} contacts are missing job title."})

            if "company" in t:
                rows = db_missing("company")
                return jsonify({"reply": f"{len(rows)} contacts are missing company."})

            if "industry" in t:
                rows = db_missing("industry")
                return jsonify({"reply": f"{len(rows)} contacts are missing industry."})

            return jsonify({"reply": "Missing what? Try: missing email / missing public link / missing phone"})

        # fallback
        return jsonify({"reply": "Try: list companies, list industries, companies in Technology, show contacts from Megapixel, email of Rachel Sim."})

    except Exception as e:
        return jsonify({"reply": f"Server error: {str(e)}"}), 500

if __name__ == "__main__":
    app.run(debug=True)