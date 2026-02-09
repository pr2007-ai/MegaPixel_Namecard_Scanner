from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv
import os
import pyodbc
import requests
import json
import re 
import requests 

SYSTEM_PROMPT = (
    "You are a short, casual, friendly chatbot. "
    "Keep responses under 2–3 sentences. "
    "Be friendly and conversational."
)

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


@app.route("/api/chat", methods=["POST"])
def api_chat():
    try:
        data = request.get_json() or {}
        user_msg = (data.get("message") or "").strip()

        if not user_msg:
            return jsonify({"reply": "Ask me something 🙂"})

        # Step 1: Ask LLM what to do
        planner_reply = ask_ollama(user_msg)

        # Step 2: Try to parse JSON
        try:
            tool_request = json.loads(planner_reply)
        except:
            # Not JSON → normal reply
            return jsonify({"reply": planner_reply})

        action = tool_request.get("action")

        # Step 3: Execute DB tool
        if action == "query_companies_by_industry":
            industry = tool_request["industry"]
            result = get_companies_by_industry(industry)

            context = "\n".join(result)

        elif action == "query_contacts_by_company":
            company = tool_request["company"]
            rows = get_contacts_by_company(company)

            context = "\n".join(
                f"{r[0]} {r[1]} ({r[2]}) - {r[3]}"
                for r in rows
            )

        elif action == "count_by_industry":
            industry = tool_request["industry"]
            count = count_by_industry(industry)

            context = f"Total: {count}"

        else:
            return jsonify({"reply": "I don't know how to do that yet."})

        # Step 4: Ask LLM to answer with DB result
        final_answer = ask_ollama(
            "Explain the database results clearly.",
            extra_context=context
        )

        return jsonify({"reply": final_answer})

    except Exception as e:
        return jsonify({"reply": f"Error: {str(e)}"}), 500


if __name__ == "__main__":
    app.run(debug=True)


