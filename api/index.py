"""Contract Lifecycle Management API — entry point; registers all blueprints."""

import os, sys
from flask import Flask, jsonify, send_from_directory

# Ensure api/ directory is in path for module imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import log
from ai import oai_h

# ─── Flask App ───────────────────────────────────────────────────────────
app = Flask(__name__, static_folder="../public", static_url_path="")
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max request size

# ─── Security Headers ───────────────────────────────────────────────────
@app.after_request
def _security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["X-XSS-Protection"] = "1; mode=block"
    resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if resp.content_type and "text/html" in resp.content_type:
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' "
            "https://cdn.quilljs.com https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' "
            "https://fonts.googleapis.com https://cdn.quilljs.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data: blob:; "
            "connect-src 'self' https://*.supabase.co https://api.openai.com"
        )
    return resp


# ─── Error Handlers ──────────────────────────────────────────────────────
@app.errorhandler(413)
def too_large(e):
    return jsonify({"error": {"message": "Request too large. Max 16MB.", "code": 413}}), 413


@app.errorhandler(415)
def unsupported_media(e):
    return jsonify({"error": {"message": "Content-Type must be application/json", "code": 415}}), 415


@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": {"message": "Not found", "code": 404}}), 404


@app.errorhandler(Exception)
def handle_exception(e):
    log.error(f"Unhandled exception: {e}", exc_info=True)
    return jsonify({"error": {"message": "Internal server error", "code": 500}}), 500


# ─── Core Routes ─────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/health")
def health():
    from config import sb
    return jsonify({"status": "ok", "db": bool(sb), "ai": bool(oai_h())})


# ─── Register Blueprints ─────────────────────────────────────────────────
from routes.auth_routes import bp as auth_bp
from routes.contracts import bp as contracts_bp
from routes.contract_features import bp as contract_features_bp
from routes.ai_routes import bp as ai_bp
from routes.dashboard import bp as dashboard_bp
from routes.admin import bp as admin_bp
from routes.catalog import bp as catalog_bp
from routes.receivables import bp as receivables_bp

app.register_blueprint(auth_bp)
app.register_blueprint(contracts_bp)
app.register_blueprint(contract_features_bp)
app.register_blueprint(ai_bp)
app.register_blueprint(dashboard_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(catalog_bp)
app.register_blueprint(receivables_bp)
