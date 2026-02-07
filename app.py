from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv
import os
import pyodbc

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


if __name__ == "__main__":
    app.run(debug=True)
