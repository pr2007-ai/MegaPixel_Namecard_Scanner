from flask import Flask, render_template
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


if __name__ == "__main__":
    app.run(debug=True)
