from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv
import os
import pyodbc
import requests
import re
from functools import lru_cache
from transformers import pipeline

# ✅ Load env first
load_dotenv()

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

# Zero-shot classifier (used only after entity-first heuristics)
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
    "help",
    "greeting",
]

CONFIDENCE_THRESHOLD = 0.55
MAX_SHOW = 20


# ---------------- UTILS ----------------
def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def clean_title(s: str) -> str:
    # Keep acronyms nicer than .title() sometimes (best-effort)
    s = (s or "").strip()
    return re.sub(r"\s+", " ", s)


def is_greeting(t: str) -> bool:
    return t in {"hi", "hello", "hey"} or t.startswith(("hi ", "hello ", "hey "))


def wants_help(t: str) -> bool:
    return any(x in t for x in ["help", "what can you do", "examples", "commands"])


def summarize_list(title: str, items: list[str], max_show: int = MAX_SHOW) -> str:
    if not items:
        return f"{title}: (none found)"
    shown = items[:max_show]
    extra = "" if len(items) <= max_show else f"\n… and {len(items) - max_show} more."
    return f"{title} ({len(items)}):\n" + "\n".join(f"- {x}" for x in shown) + extra


def split_name(text: str):
    """
    Stronger name extraction:
    - "email of rachel sim"
    - "rachel sim email"
    - "industry for john tan"
    Returns (first, last) or (None, None)
    """
    t = norm(text)

    # "email of rachel sim" / "email for rachel sim"
    m = re.search(r"(?:of|for)\s+([a-z]+)\s+([a-z]+)\s*$", t)
    if m:
        return m.group(1).title(), m.group(2).title()

    # "rachel sim email"
    m = re.search(r"^\s*([a-z]+)\s+([a-z]+)\s+(?:email|phone|number|job|title|industry)\s*$", t)
    if m:
        return m.group(1).title(), m.group(2).title()

    # last 2 tokens fallback ONLY if query clearly asks about a person field
    if any(k in t for k in ["email", "phone", "number", "job", "title", "industry"]):
        parts = re.findall(r"[a-z]+", t)
        if len(parts) >= 2:
            return parts[-2].title(), parts[-1].title()

    return None, None


JOB_STOPWORDS = {
    "who", "are", "the", "all", "list", "show", "me", "find", "give", "people", "contacts", "names",
    "in", "under", "from", "at", "of", "for", "with", "that", "having"
}

def singularize(w: str) -> str:
    w = (w or "").strip().lower()
    if w.endswith("ies"):
        return w[:-3] + "y"
    if w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w

def extract_job_keyword(message: str):
    t = norm(message)

    # Prefer patterns like: "who are engineers", "list sales managers"
    m = re.search(r"(?:who\s+are\s+|list\s+|show\s+)(.+)$", t)
    phrase = (m.group(1) if m else t).strip(" .")

    words = [w for w in re.findall(r"[a-z]+", phrase) if w not in JOB_STOPWORDS]
    if not words:
        return None

    # Keep up to 3 words ("biomedical engineer", "sales manager")
    return " ".join(words[:3]).strip()


def extract_company(message: str):
    t = norm(message)

    # "contacts from X" / "people at X"
    m = re.search(r"(?:from|at)\s+(.+)$", t)
    if not m:
        return None
    raw = m.group(1).strip(" .")
    if not raw or raw in {"me", "there"}:
        return None
    # Keep original spacing / capitalization best-effort
    return clean_title(raw)


def extract_industry(message: str):
    t = norm(message)

    patterns = [
        r"(?:people|contacts|names)\s+(?:in|under|from)\s+([a-zA-Z &/-]+)\s*$",
        r"(?:companies)\s+(?:in|under|from)\s+([a-zA-Z &/-]+)\s*$",
        r"(?:industry)\s*(?:is|=|:)?\s*([a-zA-Z &/-]+)\s*$",
        r"(?:in|under|for|category)\s+([a-zA-Z &/-]+)\s*$",
    ]

    for p in patterns:
        m = re.search(p, t, flags=re.IGNORECASE)
        if m:
            value = m.group(1).strip(" .")
            if value.lower() in {"hi", "hello", "hey"}:
                return None
            return clean_title(value)

    return None


def extract_missing_field(t: str):
    """
    Order matters: specific first, then generic.
    """
    t = norm(t)

    if "public" in t and "link" in t:
        return "publiclink"
    if ("qr" in t) or ("qrlink" in t) or ("qr link" in t):
        return "qrlink"

    # emails: specific first
    if "office" in t and "email" in t:
        return "office_email"
    if ("private" in t or "personal" in t) and "email" in t:
        return "private_email"
    if "email" in t:
        return "email"

    if "phone" in t or "number" in t:
        return "phone"
    if "job" in t or "title" in t:
        return "job"
    if "company" in t or "office name" in t:
        return "company"
    if "industry" in t:
        return "industry"

    return None


# ---------------- DB ----------------
def get_db_connection():
    return pyodbc.connect(conn_str)

def _fetchall(sql: str, params: tuple):
    conn = get_db_connection()
    cur = conn.cursor()
    rows = cur.execute(sql, *params).fetchall()
    conn.close()
    return rows

def _fetchone(sql: str, params: tuple):
    conn = get_db_connection()
    cur = conn.cursor()
    row = cur.execute(sql, *params).fetchone()
    conn.close()
    return row

def get_companies_by_industry(industry: str, limit: int = 200):
    if not industry:
        return []
    sql = """
        SELECT DISTINCT TOP (?)
            [Office Name]
        FROM dbo.BusinessCards
        WHERE [Industry] IS NOT NULL
          AND LOWER(LTRIM(RTRIM([Industry]))) = LOWER(?)
          AND [Office Name] IS NOT NULL
          AND LTRIM(RTRIM([Office Name])) <> ''
        ORDER BY [Office Name];
    """
    rows = _fetchall(sql, (limit, industry))
    return [r[0] for r in rows]

def db_contacts_by_company(company: str, limit: int = 50):
    if not company:
        return []
    sql = """
        SELECT TOP (?)
            [First Name], [Last Name], [Job Title], [Office Email], [Industry]
        FROM dbo.BusinessCards
        WHERE [Office Name] IS NOT NULL
          AND LOWER(LTRIM(RTRIM([Office Name]))) = LOWER(?)
        ORDER BY [Last Name], [First Name];
    """
    return _fetchall(sql, (limit, company))

def db_contacts_by_industry(industry: str, limit: int = 50):
    if not industry:
        return []
    sql = """
        SELECT TOP (?)
            [First Name],
            [Last Name],
            [Job Title],
            [Office Name],
            [Office Email],
            [Industry]
        FROM dbo.BusinessCards
        WHERE [Industry] IS NOT NULL
          AND LOWER(LTRIM(RTRIM([Industry]))) = LOWER(?)
        ORDER BY [Last Name], [First Name];
    """
    return _fetchall(sql, (limit, industry))

def db_people_with_job_keyword(keyword: str, limit: int = 50):
    if not keyword:
        return []
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
    return _fetchall(sql, (limit, like))

def db_person_field(first: str, last: str, column_sql: str):
    """
    column_sql is hardcoded by us (safe), not user input
    """
    if not first or not last:
        return None
    sql = f"""
        SELECT TOP 1 {column_sql}
        FROM dbo.BusinessCards
        WHERE LOWER([First Name]) = LOWER(?) AND LOWER([Last Name]) = LOWER(?);
    """
    row = _fetchone(sql, (first, last))
    return row[0] if row else None

def db_list_companies(limit: int = 200):
    sql = """
        SELECT DISTINCT TOP (?)
            [Office Name]
        FROM dbo.BusinessCards
        WHERE [Office Name] IS NOT NULL
          AND LTRIM(RTRIM([Office Name])) <> ''
        ORDER BY [Office Name];
    """
    rows = _fetchall(sql, (limit,))
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
    rows = _fetchall(sql, (limit,))
    return [r[0] for r in rows]

def db_search_name(term: str, limit: int = 50):
    term = (term or "").strip()
    if not term:
        return []
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
    return _fetchall(sql, (limit, like, like, like))

def db_missing(field: str, limit: int = 200):
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
    return _fetchall(sql, (limit,))


# ---------------- OLLAMA (optional) ----------------
def ask_ollama(user_msg: str, context: str | None = None) -> str:
    messages = [{"role": "system", "content": SYSTEM_PROMPT_TEXT}]
    if context:
        messages.append({"role": "system", "content": f"Database results:\n{context}"})
    messages.append({"role": "user", "content": user_msg})

    payload = {"model": MODEL_NAME, "messages": messages, "stream": False}
    r = requests.post(OLLAMA_URL, json=payload, timeout=60)
    r.raise_for_status()
    return r.json()["message"]["content"].strip()


# ---------------- INTENT (Entity-first + BERT) ----------------
def classify_intent(user_msg: str):
    t = norm(user_msg)

    # ultra-fast rules
    if not t:
        return "help", 1.0
    if is_greeting(t):
        return "greeting", 1.0
    if wants_help(t):
        return "help", 1.0
    if t.startswith(("find ", "search ")):
        return "search_name", 1.0
    if "missing" in t or t.startswith(("no ", "without ")):
        return "missing_fields", 1.0
    if t in {"list companies", "companies"}:
        return "list_companies", 1.0
    if t in {"list industries", "industries"}:
        return "list_industries", 1.0

    # entity-first routing (reduces misclassification a lot)
    # person field?
    if any(k in t for k in ["email", "phone", "number", "job title", "job", "industry confirms", "industry"]):
        first, last = split_name(user_msg)
        if first and last:
            if "email" in t:
                return "person_email", 0.99
            if "phone" in t or "number" in t:
                return "person_phone", 0.99
            if "job" in t or "title" in t:
                return "person_job_title", 0.99
            if "industry" in t:
                return "person_industry", 0.99

    # company / industry / job entity?
    if extract_company(user_msg):
        return "contacts_by_company", 0.90
    if extract_industry(user_msg):
        # decide company-vs-contact by keyword
        if "company" in t or t.startswith("companies"):
            return "companies_by_industry", 0.90
        return "contacts_by_industry", 0.90
    if extract_job_keyword(user_msg):
        return "people_by_job_keyword", 0.85

    # BERT fallback for the rest
    res = zsc(user_msg, INTENT_LABELS, multi_label=False)
    return res["labels"][0], float(res["scores"][0])


def help_text():
    return (
        "Try:\n"
        "- list companies\n"
        "- list industries\n"
        "- people in Healthcare\n"
        "- companies in Retail\n"
        "- contacts from Megapixel\n"
        "- email of Rachel Sim\n"
        "- missing office email / missing public link / missing phone"
    )


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
        row = _fetchone("SELECT @@VERSION;", ())
        return f"Connected!<br><br>{row[0]}"
    except Exception as e:
        return f"Error: {e}"

@app.route("/submit-contact", methods=["POST"])
def submit_contact():
    try:
        data = request.get_json() or {}

        sql = """
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
        """

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(sql, (
            data.get("firstName"),
            data.get("lastName"),
            data.get("jobTitle"),
            data.get("officeEmail"),
            data.get("privateEmail"),
            data.get("officeName"),
            data.get("phoneNumber"),
            data.get("industry"),
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
        intent, score = classify_intent(user_msg)

        # if unsure, don't run random SQL
        if score < CONFIDENCE_THRESHOLD and intent not in {"help", "greeting"}:
            return jsonify({"reply": "I’m not sure what you mean.\n" + help_text()})

        # greetings / help
        if intent == "greeting":
            return jsonify({"reply": "Hey 🙂 Ask me about your namecards — e.g. 'people in Healthcare' or 'email of Rachel Sim'."})
        if intent == "help":
            return jsonify({"reply": help_text()})

        # --- INDUSTRY -> COMPANIES ---
        if intent == "companies_by_industry":
            industry = extract_industry(user_msg)
            if not industry:
                return jsonify({"reply": "Which industry? Example: 'companies in Healthcare'."})

            companies = get_companies_by_industry(industry)
            if not companies:
                return jsonify({"reply": f"No companies found under '{industry}'."})

            return jsonify({"reply": summarize_list(f"Companies in {industry}", companies)})

        # --- COMPANY -> CONTACTS ---
        if intent == "contacts_by_company":
            company = extract_company(user_msg)
            if not company:
                return jsonify({"reply": "Which company? Example: 'show contacts from Megapixel'."})

            rows = db_contacts_by_company(company)
            if not rows:
                return jsonify({"reply": f"No contacts found for '{company}'."})

            lines = [
                f"- {fn} {ln} — {jt or 'No job'} ({email or 'No email'})"
                for fn, ln, jt, email, ind in rows[:MAX_SHOW]
            ]
            extra = "" if len(rows) <= MAX_SHOW else f"\n… and {len(rows) - MAX_SHOW} more."
            return jsonify({"reply": f"Contacts from {company} ({len(rows)}):\n" + "\n".join(lines) + extra})

        # --- INDUSTRY -> CONTACTS ---
        if intent == "contacts_by_industry":
            industry = extract_industry(user_msg)
            if not industry:
                return jsonify({"reply": "Which industry? Example: 'people in Healthcare'."})

            rows = db_contacts_by_industry(industry)
            if not rows:
                return jsonify({"reply": f"No contacts found in '{industry}'."})

            lines = [
                f"- {fn} {ln} — {jt or 'No job'} ({comp or 'No company'})"
                for fn, ln, jt, comp, email, ind in rows[:MAX_SHOW]
            ]
            extra = "" if len(rows) <= MAX_SHOW else f"\n… and {len(rows) - MAX_SHOW} more."
            return jsonify({"reply": f"Contacts in {industry} ({len(rows)}):\n" + "\n".join(lines) + extra})

        # --- JOB -> PEOPLE ---
        if intent == "people_by_job_keyword":
            keyword = extract_job_keyword(user_msg)
            if not keyword:
                return jsonify({"reply": "Which role? Example: 'who are engineers' or 'list sales managers'."})

            rows = db_people_with_job_keyword(keyword)
            if not rows:
                return jsonify({"reply": f"No contacts found with job title matching '{keyword}'."})

            lines = [f"- {fn} {ln} — {jt or 'No job'} ({comp or 'No company'})"
                     for fn, ln, jt, comp in rows[:MAX_SHOW]]
            extra = "" if len(rows) <= MAX_SHOW else f"\n… and {len(rows) - MAX_SHOW} more."
            return jsonify({"reply": f"People matching '{keyword}' ({len(rows)}):\n" + "\n".join(lines) + extra})

        # --- PERSON -> FIELD ---
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

                if office_email or private_email:
                    return jsonify({"reply": f"{first} {last}:\nOffice: {office_email or '—'}\nPrivate: {private_email or '—'}"})

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

        # --- LISTS ---
        if intent == "list_companies":
            comps = db_list_companies()
            return jsonify({"reply": summarize_list("Companies", comps, max_show=30)})

        if intent == "list_industries":
            inds = db_list_industries()
            return jsonify({"reply": summarize_list("Industries", inds, max_show=30)})

        # --- SEARCH ---
        if intent == "search_name":
            term = re.sub(r"^(find|search)\s+", "", t).strip()
            rows = db_search_name(term)
            if not rows:
                return jsonify({"reply": f"No contacts found for '{term}'."})

            lines = [f"- {fn} {ln} — {jt or 'No job'} ({comp or 'No company'})"
                     for fn, ln, jt, comp, email, ind in rows[:MAX_SHOW]]
            extra = "" if len(rows) <= MAX_SHOW else f"\n… and {len(rows) - MAX_SHOW} more."
            return jsonify({"reply": f"Matches for '{term}' ({len(rows)}):\n" + "\n".join(lines) + extra})

        # --- MISSING FIELDS ---
        if intent == "missing_fields":
            field = extract_missing_field(t)
            if not field:
                return jsonify({"reply": "Missing what? Try: missing office email / missing public link / missing phone"})

            rows = db_missing(field)
            # show count + preview (more useful than count only)
            if not rows:
                return jsonify({"reply": f"No contacts missing {field} 🎉"})

            preview = [f"- {fn} {ln} — {jt or 'No job'} ({comp or 'No company'})"
                       for fn, ln, jt, comp in rows[:MAX_SHOW]]
            extra = "" if len(rows) <= MAX_SHOW else f"\n… and {len(rows) - MAX_SHOW} more."
            return jsonify({"reply": f"Missing {field} ({len(rows)}):\n" + "\n".join(preview) + extra})

        # fallback
        return jsonify({"reply": "Try:\n" + help_text()})

    except Exception as e:
        return jsonify({"reply": f"Server error: {str(e)}"}), 500


if __name__ == "__main__":
    # ✅ avoids WinError 10038 (reloader + transformers threads)
    app.run(debug=False, use_reloader=False)
