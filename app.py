import os
import re
import json
import random
import secrets
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from flask import Flask, after_this_request, g, redirect, url_for, session, request, jsonify, send_file
from flask_session import Session
from google_auth_oauthlib.flow import Flow
from itsdangerous import BadSignature, URLSafeTimedSerializer   # ships with Flask
from markupsafe import escape, Markup   # ships with Flask
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename

from config import (
    PICTURES_DIR,
    DATA_DIR,
    REFERENCES_FACES_DIR,
    PUBLIC_HOSTNAME,
    SECRET_KEY,
    ALLOWED_EMAILS,
)
from query_parser import parse_query

# A single string here would turn `email in ALLOWED_EMAILS` into a substring
# test ("ad@gmail.com" in "dad@gmail.com" is True) and let other accounts in.
if isinstance(ALLOWED_EMAILS, str):
    raise SystemExit('config.ALLOWED_EMAILS must be a set, e.g. {"you@example.com"}, not a string')

KNOWN_PEOPLE = {p.name.lower() for p in Path(REFERENCES_FACES_DIR).iterdir() if p.is_dir()}

# In-memory session log. Resets when Flask restarts.
ACTIVE_SESSIONS = {}

# Only these emails can view the sessions page
ADMIN_EMAILS = {"your-email@gmail.com"}   # put your own address here

# Optional code to track usage of pic-search webGUI

def client_ip():
    """Real client IP — Caddy forwards the original in X-Forwarded-For."""
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.remote_addr or "unknown"


def human_duration(seconds):
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


app = Flask(__name__)

# Caddy terminates HTTPS and forwards plain HTTP to Flask. Trust its
# X-Forwarded-For / X-Forwarded-Proto (exactly one proxy hop) so request.url is
# https:// (which oauthlib requires) and request.remote_addr is the real client.
# Keep Flask bound to localhost: anything reaching it directly could spoof these.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

# Local development only: run with PICSEARCH_DEV=1 to allow OAuth over plain http.
# Such as: PICSEARCH_DEV=1 python3 app.py
# Leave it unset in production (behind Caddy / HTTPS).
if os.environ.get("PICSEARCH_DEV") == "1":
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
    REDIRECT_URI = "http://localhost:5000/callback"
else:
    REDIRECT_URI = f"https://{PUBLIC_HOSTNAME}/callback"

# --- Server-side sessions -----------------------------------------------------
# The browser only receives an opaque session ID; user data stays on the server.
SESSION_DIR = os.environ.get(
    "SESSION_FILE_DIR", str(Path.home() / ".pic-search" / "sessions")
)
os.makedirs(SESSION_DIR, exist_ok=True)
try:
    os.chmod(SESSION_DIR, 0o700)
except OSError:
    pass  # e.g. Windows

app.config.update(
    SECRET_KEY=SECRET_KEY,
    SESSION_TYPE="filesystem",
    SESSION_FILE_DIR=SESSION_DIR,
    SESSION_PERMANENT=False,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=True,          # requires HTTPS (Caddy provides it)
    SESSION_COOKIE_SAMESITE="Lax",
    MAX_CONTENT_LENGTH=200 * 1024 * 1024,   # cap uploads at 200 MB per request
)
Session(app)
@app.after_request
def add_site_title(response):
    """Put the site title at the top of every HTML page."""
    if response.mimetype == "text/html" and response.status_code == 200:
        banner = """
<title>Pic-Search</title>
<h1 style="text-align:center; margin:20px 0; font-size:38px;
           font-family:'Segoe UI', Arial, sans-serif;
           background:linear-gradient(90deg,#4f46e5,#06b6d4);
           -webkit-background-clip:text; background-clip:text;
           -webkit-text-fill-color:transparent; color:transparent;">
    Welcome to the Pic-Search Application
</h1>
"""

        response.set_data(banner + response.get_data(as_text=True))
    return response


@app.before_request
def make_csp_nonce():
    g.csp_nonce = secrets.token_urlsafe(16)


@app.after_request
def add_security_headers(response):
    """Browser-side defences: only nonce-tagged scripts run, no framing, no MIME sniffing."""
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        f"script-src 'nonce-{g.get('csp_nonce', '')}'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' https://*.googleusercontent.com; "
        "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    )
    response.headers["Strict-Transport-Security"] = "max-age=31536000"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


# Path to your downloaded client secret JSON file
CLIENT_SECRETS_FILE = "client_secret.json"

SCOPES = [
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid"
]

# Paths stored in index.json / faces.json are container-internal (e.g. /pictures/foo.jpg),
# since that's the mount point main.py / face_search.py see inside the podman container.
CONTAINER_PICTURES_PREFIX = "/pictures"

FACE_SEARCH_LIMIT = 10000
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}

# At most this many search containers run at once. Each loads the CLIP model or
# the face index, so a burst of searches (or someone hammering /search) queues
# up instead of piling containers onto the PC.
SEARCH_SLOTS = threading.BoundedSemaphore(2)


# =============================================================================
# PATH SAFETY HELPERS
# =============================================================================
def is_within_pictures(path):
    """True only if `path` (after resolving symlinks) is inside PICTURES_DIR."""
    real_path = os.path.normcase(os.path.realpath(path))
    real_root = os.path.normcase(os.path.realpath(PICTURES_DIR))
    try:
        return os.path.commonpath([real_path, real_root]) == real_root
    except ValueError:          # e.g. different drives on Windows
        return False


def container_path_to_host(container_path):
    """Translate a container-internal /pictures/... path to the real host path."""
    prefix = CONTAINER_PICTURES_PREFIX.rstrip("/") + "/"
    if "\x00" in container_path or not container_path.startswith(prefix):
        return None

    relative = os.path.normpath(container_path[len(prefix):])
    if (
        relative in (".", "..")
        or relative.startswith(".." + os.sep)
        or os.path.isabs(relative)
        or os.path.splitdrive(relative)[0]
    ):
        return None

    return os.path.join(PICTURES_DIR, relative)


def safe_relative_path(rel_path):
    """Normalize a user-supplied relative path and reject any attempt to escape PICTURES_DIR."""
    rel_path = (rel_path or "").replace("\\", "/")
    if "\x00" in rel_path:
        return None
    norm = os.path.normpath(rel_path).replace("\\", "/")
    if norm == ".":
        return ""
    if norm == ".." or norm.startswith("../") or norm.startswith("/"):
        return None
    return norm


def browse_abs_path(rel_path):
    safe_rel = safe_relative_path(rel_path)
    if safe_rel is None:
        return None
    abs_path = os.path.normpath(os.path.join(PICTURES_DIR, safe_rel))
    if not is_within_pictures(abs_path):
        return None
    return abs_path


def unique_path(directory, filename):
    """Return a path in `directory` that doesn't overwrite an existing file."""
    base, ext = os.path.splitext(filename)
    candidate = os.path.join(directory, filename)
    n = 1
    while os.path.exists(candidate):
        candidate = os.path.join(directory, f"{base}_{n}{ext}")
        n += 1
    return candidate


# =============================================================================
# SHARED HTML SNIPPETS
# =============================================================================
def top_nav_html(count):
    return """
        <div style="text-align:center; margin: 15px 0;">
        <a href="/browse"><button style="padding:8px 15px; font-size:20px; cursor:pointer;">📁 Top of Pictures Directory</button></a>
        </div>
    """


def bottom_nav_html():
    return """
        <hr>
        <div style="text-align:center; margin: 15px 0; font-size:20px; color:#666;">
            <a href="/">Home</a> &nbsp;|&nbsp;
            <a href="/browse">Pictures Directory</a> &nbsp;|&nbsp;
            <a href="/logout">Logout</a>
        </div>
    """


# =============================================================================
# SEARCH HELPERS (podman)
# =============================================================================
def parse_score_lines(output):
    """Parse lines like '0.8421  /pictures/foo.jpg' from search script stdout."""
    results = []
    for line in output.splitlines():
        m = re.match(r"^([\d.]+)\s+(.+)$", line.strip())
        if m:
            results.append((float(m.group(1)), m.group(2)))
    return results


def run_clip_search(prompt, top_k=20):
    try:
        with SEARCH_SLOTS:
            result = subprocess.run(
                [
                    "podman", "run", "--rm", "--network=none",
                    "-v", f"{DATA_DIR}:/data:Z,ro",
                    "-v", f"{PICTURES_DIR}:/pictures:Z,ro",
                    "-v", "clip_models:/root/.cache:Z",
                    "pic-search", "search", "--prompt", prompt, "--top", str(top_k)
                ],
                capture_output=True, text=True, timeout=120
            )
    except subprocess.TimeoutExpired:
        print("CLIP search timed out")
        return []
    if result.returncode != 0:
        print(f"CLIP search failed: {result.stderr}")
        return []
    return parse_score_lines(result.stdout)


def run_face_search(name, top_k=50):
    try:
        with SEARCH_SLOTS:
            result = subprocess.run(
                [
                    "podman", "run", "--rm", "--network=none",
                    "--entrypoint", "python",
                    "-v", f"{DATA_DIR}:/data:Z,ro",
                    "-v", f"{PICTURES_DIR}:/pictures:Z,ro",
                    "-v", "insightface_models:/root/.insightface:Z",
                    "pic-search", "face_search.py", "search", "--name", name, "--top", str(top_k)
                ],
                capture_output=True, text=True, timeout=120
            )
    except subprocess.TimeoutExpired:
        print(f"Face search timed out for '{name}'")
        return []
    if result.returncode != 0:
        print(f"Face search failed for '{name}': {result.stderr}")
        return []
    return parse_score_lines(result.stdout)


# =============================================================================
# PAGE 1: HOME / SEARCH PAGE  —  URL: /
# The landing page. Shows the login button if logged out; if logged in, shows
# the search text box, the count dropdown, and the bottom nav links.
# =============================================================================
@app.route("/")
def index():
    if "google_id" in session:
        try:
            sel = int(request.args.get("count", 5))
        except ValueError:
            sel = 5

        options = "".join(
            f'<option value="{n}"{" selected" if n == sel else ""}>{n} images</option>'
            for n in (5, 10, 20, 50)
        )

        name = escape(session.get("name", ""))
        email = escape(session.get("email", ""))
        picture = escape(session.get("picture", ""))

        return f"""
            <h3>Welcome, {name}!</h3>
            <p>Email: {email}</p>
            <img src='{picture}' style='border-radius:50%; width:100px;'><br><br>
            <hr>
            <!-- IMAGE SEARCH INPUT BOX -->
            <div style="margin: 20px 0;">
                <form action="/search" method="GET">
                    <label for="query" style="font-size: 30px; font-weight: bold;">
                        Please input phrase to search for images:
                    </label><br><br>
                    <input type="text" id="query" name="query" placeholder="Jack and Jill or Jack in meadow, or Jill near well..." required style="padding: 8px; width: 500px; font-size: 18px;">
                    <button type="submit" style="padding: 8px 15px; font-size: 14px; cursor: pointer;">Search</button>
                    <select name="count" style="padding: 8px; font-size: 14px;">
                        {options}
                    </select>
                </form>
            </div>
            <hr>
            {bottom_nav_html()}
        """
    return """
    <h3>Please first login to the application >>>
    <a href='/login'><button>Login with Google</button></a>
    </h3>
    """

# =============================================================================
# LOGIN / OAUTH CALLBACK
# =============================================================================
@app.before_request
def enforce_allowlist():
    """Re-check the allowlist on every request, so removing an email from
    ALLOWED_EMAILS (and restarting) locks that user out of existing sessions."""
    if "google_id" in session and session.get("email") not in ALLOWED_EMAILS:
        session.clear()


# The OAuth state and PKCE code_verifier live in a short-lived signed cookie, not
# the server-side session. Otherwise every anonymous /login hit (bots, scanners)
# creates a session file, and once the store passes its 500-file limit it evicts
# the oldest sessions first, logging the family out.
OAUTH_COOKIE = "oauth_pending"
OAUTH_MAX_AGE = 600   # seconds allowed to finish signing in with Google


def oauth_signer():
    return URLSafeTimedSerializer(app.secret_key, salt="oauth-pending")


@app.route("/login")
def login():
    flow = Flow.from_client_secrets_file(
        client_secrets_file=CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
    )

    # 1. Generate the URL along with the PKCE code_verifier
    authorization_url, state = flow.authorization_url(
        include_granted_scopes="true"
    )

    # 2. Save BOTH the state and code_verifier in the signed login cookie
    response = redirect(authorization_url)
    response.set_cookie(
        OAUTH_COOKIE,
        oauth_signer().dumps({"state": state, "code_verifier": flow.code_verifier}),
        max_age=OAUTH_MAX_AGE, secure=True, httponly=True, samesite="Lax",
    )
    return response


@app.route("/callback")
def callback():
    @after_this_request
    def forget_pending_login(response):
        response.delete_cookie(OAUTH_COOKIE, secure=True, httponly=True, samesite="Lax")
        return response

    try:
        pending = oauth_signer().loads(request.cookies.get(OAUTH_COOKIE, ""), max_age=OAUTH_MAX_AGE)
    except BadSignature:   # missing, tampered with, or older than OAUTH_MAX_AGE
        return redirect(url_for("login"))

    # 1. Re-initialize the OAuth flow configuration
    flow = Flow.from_client_secrets_file(
        client_secrets_file=CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
        state=pending["state"]
    )

    # 2. Inject the code verifier back from the login cookie before grabbing tokens
    flow.code_verifier = pending["code_verifier"]

    # 3. Exchange the authorization code for actual access tokens
    try:
        flow.fetch_token(authorization_response=request.url)
    except Exception as exc:
        print(f"OAuth token exchange failed: {exc}")
        session.clear()
        return "Login failed. Please try again.", 400

    # 4. Use the newly authenticated session to grab the user's profile info
    authorized_session = flow.authorized_session()
    user_info = authorized_session.get(
        "https://www.googleapis.com/oauth2/v2/userinfo"
    ).json()

    email = user_info.get("email")

    # 5. Enforce the allowlist before granting any session access
    if email not in ALLOWED_EMAILS or not user_info.get("verified_email", False):
        session.clear()
        return "Access denied — this account is not authorized.", 403

    # 6. Start a fresh session and populate only the profile fields the home
    #    route needs.
    #    OAuth tokens are deliberately NOT stored: the app never calls Google
    #    APIs after login, so there is nothing to leak.
    #    A new session ID is issued too, so an ID planted before login is useless.
    app.session_interface.regenerate(session)
    session.clear()
    session["google_id"] = user_info.get("id")
    session["name"] = user_info.get("name")
    session["email"] = email
    session["picture"] = user_info.get("picture")

    # 7. Redirect back to the home landing page URL
    return redirect("/")


# =============================================================================
# PAGE 2: SEARCH RESULTS PAGE  —  URL: /search?query=...&count=...
# Parses the query (single-word names skip the LLM), runs face and/or CLIP
# search via podman, intersects the results, and renders the matching photos
# as clickable thumbnails with download links.
# =============================================================================
@app.route("/search")
def search():
    if "google_id" not in session:
        return redirect(url_for("index"))

    query = request.args.get("query", "").strip().lower()
    if not query:
        return redirect(url_for("index"))

    try:
        count = int(request.args.get("count", 5))
    except ValueError:
        count = 5
    count = max(1, min(count, 50))

    query_words = query.split()
    if len(query_words) == 1:
        word = query_words[0]
        if word in KNOWN_PEOPLE:
            people, scene = [word], ""
        else:
            people, scene = [], query
    else:
        try:
            parsed = parse_query(query)
        except Exception as exc:   # e.g. Ollama not running
            print(f"Query parser failed: {exc}")
            parsed = {}
        # The LLM's JSON is untrusted: keep only a list of name strings and a
        # scene string, deduplicated, so malformed output can't crash the page
        # or fan out into one podman container per character of a string.
        if not isinstance(parsed, dict):
            parsed = {}
        raw_people = parsed.get("people")
        if not isinstance(raw_people, list):
            raw_people = []
        people = list(dict.fromkeys(
            p.strip().lower() for p in raw_people if isinstance(p, str) and p.strip()
        ))
        raw_scene = parsed.get("scene")
        scene = raw_scene.strip() if isinstance(raw_scene, str) else ""
        if not people and not scene:
            scene = query   # fall back to plain scene search

    clip_top_k = 500 if people else 20
    clip_results = run_clip_search(scene, top_k=clip_top_k) if scene else []

    if people:
        # A name without a reference-face folder can never match, so don't
        # spawn podman containers for names the LLM made up.
        if all(person in KNOWN_PEOPLE for person in people):
            face_path_sets = []
            for person in people:
                face_results = run_face_search(person, top_k=FACE_SEARCH_LIMIT)
                face_path_sets.append({path for _, path in face_results})
            common_face_paths = set.intersection(*face_path_sets)
        else:
            common_face_paths = set()

        if clip_results:
            matched_paths = [p for _, p in clip_results if p in common_face_paths]
        else:
            matched_paths = list(common_face_paths)

        final_paths = random.sample(matched_paths, min(count, len(matched_paths)))
    else:
        pool = [p for _, p in clip_results[:max(20, count)]]
        final_paths = random.sample(pool, min(count, len(pool)))

    if final_paths:
        images_html = "".join(
            f'''<div style="display:inline-block; margin:10px; text-align:center; vertical-align:top;">
                    <a href="/image?path={quote(p)}" target="_blank">
                        <img src='/image?path={quote(p)}' style='width:200px;'>
                    </a><br>
                    <span style="font-size:11px;">{escape(os.path.basename(p))}</span><br>
                    <a href="/image?path={quote(p)}" download style="font-size:11px;">Download</a>
                </div>'''
            for p in final_paths
        )
    else:
        images_html = "<p>No matches found.</p>"

    return f"""
        {top_nav_html(count)}
        <h3>Results for: {escape(query)}</h3>

        <div style="margin: 15px 0;">
            <a href="/search?query={quote(query)}&count={count}">
                <button style="padding:8px 15px; font-size:14px; cursor:pointer;">🔄 Show Different Images</button>
            </a>
            &nbsp;&nbsp;&nbsp;
            <span style="font-size: 16px; font-weight: bold; margin-left: 20px;">Or Ctrl-r to refresh with new images</span>
            <br>
            <a href='/?count={count}'>Back to search</a>
        </div>

        <div>{images_html}</div>

        <br>
        <a href='/?count={count}'>Back to search</a>
        {bottom_nav_html()}
    """


# =============================================================================
# IMAGE ENDPOINT  —  URL: /image?path=/pictures/...
# Serves a single image, only if it is inside PICTURES_DIR.
# =============================================================================
@app.route("/image")
def image():
    if "google_id" not in session:
        return "Unauthorized", 403

    container_path = request.args.get("path", "")
    host_path = container_path_to_host(container_path)
    if not host_path:
        return "Invalid path", 400

    real_host = os.path.realpath(host_path)
    if not is_within_pictures(real_host):
        return "Invalid path", 403

    if os.path.splitext(real_host)[1].lower() not in IMAGE_EXTS:
        return "Unsupported type", 415

    if not os.path.isfile(real_host):
        return "Not found", 404

    return send_file(real_host, download_name=os.path.basename(real_host))


# =============================================================================
# PAGE 3: BROWSE PICTURES PAGE  —  URL: /browse?path=...
# File-explorer view of the Pictures directory. Lists subfolders, shows photo
# thumbnails, and provides the drag-and-drop / upload form for adding photos.
# =============================================================================
@app.route("/browse")
def browse():
    if "google_id" not in session:
        return redirect(url_for("index"))

    rel_path = safe_relative_path(request.args.get("path", ""))
    abs_path = browse_abs_path(rel_path) if rel_path is not None else None
    if abs_path is None or not os.path.isdir(abs_path):
        return "Invalid directory", 400

    entries = sorted(os.listdir(abs_path), key=str.lower)
    dirs, images = [], []
    for name in entries:
        full = os.path.join(abs_path, name)
        if os.path.isdir(full):
            dirs.append(name)
        elif os.path.splitext(name)[1].lower() in IMAGE_EXTS:
            images.append(name)

    dir_links = "".join(
        f'<div style="margin:4px;"><a href="/browse?path={quote((rel_path + "/" + d) if rel_path else d)}">📁 {escape(d)}</a></div>'
        for d in dirs
    )

    thumbs = "".join(
        f'''<div style="display:inline-block; margin:6px; text-align:center; vertical-align:top;">
                <a href="/image?path={quote("/pictures/" + ((rel_path + "/" + img) if rel_path else img))}" target="_blank">
                    <img src="/image?path={quote("/pictures/" + ((rel_path + "/" + img) if rel_path else img))}"
                         style="width:150px; height:150px; object-fit:cover;">
                </a><br>
                <span style="font-size:11px;">{escape(img)}</span><br>
                <a href="/image?path={quote("/pictures/" + ((rel_path + "/" + img) if rel_path else img))}" download style="font-size:11px;">Download</a>
            </div>'''
        for img in images
    )

    parent_link = ""
    if rel_path:
        parent_rel = "/".join(rel_path.split("/")[:-1])
        parent_link = f'<a href="/browse?path={quote(parent_rel)}">⬆ Up one level</a><br><br>'

    uploaded_param = request.args.get("uploaded", "")
    skipped_param = request.args.get("skipped", "")
    upload_dir_param = request.args.get("upload_dir", "")

    if uploaded_param or skipped_param:
        lines = []
        if uploaded_param:
            lines.append(f"Uploaded: {escape(uploaded_param)}")
        if skipped_param:
            lines.append(f"Skipped (unsupported type): {escape(skipped_param)}")
        if upload_dir_param:
            lines.append(f"Destination: {escape(upload_dir_param)}")
        upload_message = Markup("<br>".join(lines))
    else:
        upload_message = "(no upload yet)"

    # json.dumps does not escape "<", so do it here to keep "</script>" from
    # ending the script block early.
    rel_path_json = json.dumps(rel_path).replace("<", "\\u003c")

    return f"""
        <h3>Pictures: /{escape(rel_path)}</h3>
        {parent_link}

        <div style="margin: 15px 0; padding: 10px; border: 1px solid #ccc; background:#f9f9f9;">
            <strong>Upload Result &amp; Details:</strong>
            <div id="uploadMessage">{upload_message}</div>
        </div>

        <div id="dropzone" style="border: 3px dashed #999; padding: 30px; text-align:center; margin: 15px 0;">
            Drag and drop photos here to add them to this folder
            <br><br>
            <form id="uploadForm" method="POST" action="/upload?path={quote(rel_path)}" enctype="multipart/form-data">
                <input type="file" name="files" multiple accept="image/*">
                <button type="submit">Upload</button>
            </form>
        </div>

        <div>{dir_links}</div>
        <div>{thumbs}</div>

        <script nonce="{g.csp_nonce}">
        const currentRelPath = {rel_path_json};
        const dropzone = document.getElementById('dropzone');
        dropzone.addEventListener('dragover', e => {{ e.preventDefault(); dropzone.style.background = '#eee'; }});
        dropzone.addEventListener('dragleave', e => {{ dropzone.style.background = ''; }});
        dropzone.addEventListener('drop', e => {{
            e.preventDefault();
            dropzone.style.background = '';
            const formData = new FormData();
            for (const file of e.dataTransfer.files) {{
                formData.append('files', file);
            }}
            fetch('/upload?path=' + encodeURIComponent(currentRelPath), {{
                method: 'POST',
                body: formData,
                headers: {{ 'X-Requested-With': 'fetch' }}
            }})
                .then(res => res.json())
                .then(data => {{
                    const params = new URLSearchParams();
                    params.set('path', currentRelPath);
                    if (data.saved.length) params.set('uploaded', data.saved.join(', '));
                    if (data.skipped.length) params.set('skipped', data.skipped.join(', '));
                    params.set('upload_dir', data.path);
                    window.location.href = '/browse?' + params.toString();
                }});
        }});
        </script>
        <hr>
        {bottom_nav_html()}
    """


# =============================================================================
# UPLOAD ENDPOINT  —  POST /upload?path=...
# =============================================================================
@app.route("/upload", methods=["POST"])
def upload():
    if "google_id" not in session:
        return "Unauthorized", 403

    rel_path = safe_relative_path(request.args.get("path", ""))
    abs_path = browse_abs_path(rel_path) if rel_path is not None else None
    if abs_path is None or not os.path.isdir(abs_path):
        return "Invalid directory", 400

    saved, skipped = [], []
    for f in request.files.getlist("files"):
        if not f or not f.filename:
            continue
        filename = secure_filename(f.filename)
        ext = os.path.splitext(filename)[1].lower()
        if ext not in IMAGE_EXTS:
            skipped.append(f.filename)
            continue
        target = unique_path(abs_path, filename)   # never overwrite existing photos
        f.save(target)
        saved.append(os.path.basename(target))

    display_path = "/" + rel_path if rel_path else "/ (Pictures root)"

    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify({"saved": saved, "skipped": skipped, "path": display_path})

    return redirect(url_for(
        "browse",
        path=rel_path,
        uploaded=", ".join(saved) if saved else "",
        skipped=", ".join(skipped) if skipped else "",
        upload_dir=display_path,
    ))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


if __name__ == "__main__":
    # Host on local interface port 5000
    app.run(host="localhost", port=5000, debug=False)
