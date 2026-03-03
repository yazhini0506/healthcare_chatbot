"""
app.py – Flask Web Application
Serves the chat UI and exposes REST API endpoints.
"""

import os
import logging
from datetime import datetime
from flask import Flask, request, jsonify, render_template_string, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv

from database import init_db, get_all_leads, get_all_conversations, get_conversation_messages, get_kb_stats, get_all_products, get_product_stats
from scraper import run_scraper
from agent import chat, new_conversation

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [APP] %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder="static", template_folder="templates")
CORS(app)
app.secret_key = os.getenv("SECRET_KEY", "healthcare-lead-agent-secret")

# ── Startup ───────────────────────────────────────────────────────────────────

@app.before_request
def _once():
    """Initialise DB and seed data on first request."""
    if not getattr(app, "_initialised", False):
        init_db()
        from database import get_kb_stats, get_product_stats
        kb_empty  = get_kb_stats()["total"] == 0
        prod_empty = get_product_stats()["total"] == 0
        if kb_empty or prod_empty:
            logger.info("DB empty – running scraper to populate products & knowledge base …")
            run_scraper()
        app._initialised = True


# ── Chat API ──────────────────────────────────────────────────────────────────

@app.route("/api/chat/new", methods=["POST"])
def api_new_chat():
    conv_id = new_conversation()
    return jsonify({"conv_id": conv_id})


@app.route("/api/chat/message", methods=["POST"])
def api_chat_message():
    data = request.get_json(silent=True) or {}
    conv_id = data.get("conv_id") or new_conversation()
    user_message = (data.get("message") or "").strip()

    if not user_message:
        return jsonify({"error": "message is required"}), 400

    result = chat(conv_id, user_message)
    return jsonify(result)


@app.route("/api/chat/history/<conv_id>", methods=["GET"])
def api_chat_history(conv_id):
    messages = get_conversation_messages(conv_id)
    return jsonify(messages)


# ── Admin / Analytics API ─────────────────────────────────────────────────────

@app.route("/api/leads", methods=["GET"])
def api_leads():
    return jsonify(get_all_leads())


@app.route("/api/conversations", methods=["GET"])
def api_conversations():
    return jsonify(get_all_conversations())


@app.route("/api/kb/stats", methods=["GET"])
def api_kb_stats():
    return jsonify(get_kb_stats())


@app.route("/api/products", methods=["GET"])
def api_products():
    """Return all scraped products."""
    category = request.args.get("category", "").strip()
    if category:
        from database import get_products_by_category
        return jsonify(get_products_by_category(category))
    return jsonify(get_all_products())


@app.route("/api/products/stats", methods=["GET"])
def api_products_stats():
    return jsonify(get_product_stats())


@app.route("/api/products/search", methods=["GET"])
def api_products_search():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])
    from database import search_products
    return jsonify(search_products(q))


@app.route("/api/test-email", methods=["POST"])
def api_test_email():
    """Fire a test email to verify SMTP credentials."""
    from email_service import test_email
    result = test_email()
    return jsonify(result), (200 if result["success"] else 500)


@app.route("/api/scrape", methods=["POST"])
def api_scrape():
    """Manually trigger a re-scrape from all sources."""
    count = run_scraper()
    stats = get_product_stats()
    return jsonify({"message": f"Scraping complete.", "products": stats["total"], "total": count})


# ── Serve Frontend ────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/admin")
def admin():
    return send_from_directory("static", "admin.html")


# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(debug=os.getenv("DEBUG", "True") == "True", port=5000, host="0.0.0.0")
