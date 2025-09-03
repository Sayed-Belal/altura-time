# server.py
import os
import uuid
import json
from flask import Flask, request, jsonify, abort, send_file, render_template_string
from werkzeug.utils import secure_filename
from flask_cors import CORS

# Config
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "/tmp/uploads")   # set to /uploads if Render persistent disk mounted, otherwise /tmp/uploads
MAX_BYTES = 2 * 1024 * 1024  # 2 MB max file size
ALLOWED_EXT = {".ics"}

os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = MAX_BYTES
CORS(app, resources={r"/*": {"origins": "*"}})  # allow cross-origin requests (Firebase frontend will call /upload)

def allowed_filename(filename):
    return os.path.splitext(filename)[1].lower() in ALLOWED_EXT

@app.route("/")
def index():
    return jsonify({"status": "ok", "msg": "AlturaTime backend"})

@app.route("/upload", methods=["POST"])
def upload_schedule():
    # Expect form-data with fields: name, file
    if "file" not in request.files:
        return jsonify({"success": False, "error": "No file provided"}), 400
    uploaded = request.files["file"]
    name = (request.form.get("name") or "").strip()

    if uploaded.filename == "":
        return jsonify({"success": False, "error": "No filename"}), 400

    if not allowed_filename(uploaded.filename):
        return jsonify({"success": False, "error": "Only .ics files allowed"}), 400

    # Save file
    file_id = uuid.uuid4().hex
    safe_orig = secure_filename(uploaded.filename)
    file_name = f"{file_id}.ics"
    path = os.path.join(UPLOAD_DIR, file_name)
    try:
        uploaded.save(path)
    except Exception as e:
        return jsonify({"success": False, "error": f"Failed to save: {str(e)}"}), 500

    # Save metadata JSON
    meta = {"id": file_id, "name": name or "Unnamed Student", "orig_name": safe_orig}
    meta_path = os.path.join(UPLOAD_DIR, f"{file_id}.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f)

    # Provide share link
    base = request.url_root.rstrip("/")
    link = f"{base}/s/{file_id}"
    return jsonify({"success": True, "id": file_id, "name": meta["name"], "link": link})

@app.route("/i/<file_id>", methods=["GET"])
def get_ics(file_id):
    # Returns the raw .ics file
    p = os.path.join(UPLOAD_DIR, f"{file_id}.ics")
    if not os.path.exists(p):
        return abort(404)
    return send_file(p, mimetype="text/calendar")

@app.route("/meta/<file_id>", methods=["GET"])
def get_meta(file_id):
    p = os.path.join(UPLOAD_DIR, f"{file_id}.json")
    if not os.path.exists(p):
        return abort(404)
    with open(p, "r", encoding="utf-8") as f:
        return jsonify(json.load(f))

@app.route("/s/<file_id>", methods=["GET"])
def schedule_page(file_id):
    # Serve a small HTML page (AlturaTime UI) that fetches /i/<file_id> and /meta/<file_id>
    meta_path = os.path.join(UPLOAD_DIR, f"{file_id}.json")
    if not os.path.exists(meta_path):
        return abort(404)
    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)

    # Simple HTML (client parses the ICS and renders the same UI as the Firebase frontend)
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>AlturaTime — {{meta['name']}}</title>
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <link href="https://fonts.googleapis.com/css2?family=Nunito:wght@700;900&family=Lato:wght@400;700&display=swap" rel="stylesheet">
  <script src="https://unpkg.com/ical.js@1.4.0/build/ical.min.js"></script>
  <style>
    body {{{{ font-family: 'Lato', sans-serif; background: #f8fafb; margin:0; padding:18px; color:#203040; }}}}
    .container {{{{ max-width:760px; margin:20px auto; }}}}
    .title {{{{ font-family:'Nunito',sans-serif; color:#2a9078; font-size:28px; font-weight:900; }}}}
    .card {{{{ background:white; border-radius:10px; padding:18px; box-shadow:0 6px 18px rgba(0,0,0,0.06); margin-top:14px; }}}}
    .city{{{{ font-weight:700; color:#e27d60; }}}}
    .time{{{{ font-size:2rem; color:#2a9078; font-weight:700; }}}}
    .call-status.safe{{{{ background:#5fb878;color:#fff;padding:6px 10px;border-radius:6px;display:inline-block }}}}
    .call-status.avoid{{{{ background:#ef476f;color:#fff;padding:6px 10px;border-radius:6px;display:inline-block }}}}
    .note{{ color:#2a9078;margin-top:8px }}
    a.button{{ display:inline-block; padding:8px 12px; background:#2a9078; color:#fff; border-radius:8px; text-decoration:none }}
    pre.ics{{ white-space:pre-wrap; background:#f1f5f6; padding:8px; border-radius:6px; font-size:0.85rem }}
  </style>
</head>
<body>
  <div class="container">
    <div class="title">AlturaTime</div>
    <div style="margin-top:8px">Schedule for: <strong>{{meta['name']}}</strong></div>
    <div id="clockCard" class="card">
      <div id="city" class="city">Loading...</div>
      <div id="time" class="time">--:--</div>
      <div id="status" class="call-status maybe">Loading</div>
      <div id="note" class="note"></div>
      <div id="next" style="margin-top:10px;color:#333"></div>
    </div>

    <div style="margin-top:12px">
      <a class="button" href="/" target="_blank">Open AlturaTime Home</a>
    </div>

    <div style="margin-top:14px" class="card">
      <div><strong>Raw schedule (for debugging)</strong></div>
      <pre id="icsDump" class="ics">Loading ICS…</pre>
    </div>
  </div>

<script>
const fileId = "{{file_id}}";
const meta = {{json.dumps(meta)}};
const icsUrl = "/i/" + fileId;

let loadedSchedule = null;
let scheduleLocation = null;
let scheduleEventCount = 0;

function formatTimeDifference(targetDate, currentDate) {{{{
  const diffMs = targetDate - currentDate;
  const diffMins = Math.round(diffMs / 60000);
  const hours = Math.floor(diffMins / 60);
  const minutes = diffMins % 60;
  if (hours > 0) return `${{{{hours}}}} hr ${{{{minutes}}}} min`;
  return `${{{{minutes}}}} minutes`;
}}}}

function getStatusFromNow(now) {{{{
  // now is Date in schedule tz (JS Date)
  const hour = now.getHours();
  const minute = now.getMinutes();
  if (loadedSchedule) {{{{
    for (const eventObj of loadedSchedule) {{{{
      const comp = new ICAL.Component(eventObj);
      const e = new ICAL.Event(comp);
      const start = e.startDate.toJSDate();
      const end = e.endDate.toJSDate();
      if (now >= start && now <= end) {{{{
        return {{{{status: "CLASS IN SESSION", cls:"avoid"}}}};
      }}}}
    }}}}
  }}}}
  const mins = hour * 60 + minute;
  if (mins >= 420 && mins <= 1290) return {{{{status:"GOOD TO CALL", cls:"safe"}}}};
  if (mins > 1290 || mins < 420) return {{{{status:"AVOID CALLING", cls:"avoid"}}}};
  return {{{{status:"MAYBE", cls:"maybe"}}}};
}}}}

function getNextClassTime(now) {{{{
  let next = null; let mindiff = Infinity;
  if (!loadedSchedule) return null;
  for (const eventObj of loadedSchedule) {{{{
    const comp = new ICAL.Component(eventObj);
    const e = new ICAL.Event(comp);
    const start = e.startDate.toJSDate();
    if (start > now) {{{{
      const diff = start - now;
      if (diff < mindiff) {{{{ mindiff = diff; next = start; }}}}
    }}}}
  }}}}
  return next;
}}}}

async function fetchAndParse() {{{{
  try {{{{
    const r = await fetch(icsUrl);
    if (!r.ok) throw new Error("ICS fetch failed");
    const icsText = await r.text();
    document.getElementById("icsDump").textContent = icsText.slice(0, 4000) + (icsText.length>4000 ? "\\n\\n... (truncated)" : "");
    const jcal = ICAL.parse(icsText);
    const comp = new ICAL.Component(jcal);
    const vevents = comp.getAllSubcomponents("vevent");
    loadedSchedule = vevents.map(v => v.toJSON());
    scheduleEventCount = vevents.length;

    // timezone detection
    let tz = null;
    const tzComp = comp.getFirstSubcomponent("vtimezone");
    if (tzComp) tz = tzComp.getFirstPropertyValue("tzid");
    if (!tz && vevents.length>0) {{{{
      const first = vevents[0].getFirstProperty("dtstart");
      if (first?.parameters?.tzid) tz = first.parameters.tzid;
    }}}}
    scheduleLocation = {{{{tzValue: tz || 'UTC', displayName: meta.name}}}};
    renderClock();
    setInterval(renderClock, 1000);
  }}}} catch (err) {{{{
    document.getElementById("city").textContent = "Failed to load schedule";
    document.getElementById("time").textContent = "--:--";
    document.getElementById("status").textContent = "ERROR";
    console.error(err);
  }}}}
}}}}

function renderClock() {{{{
  if (!scheduleLocation) return;
  const tz = scheduleLocation.tzValue;
  const now = new Date(new Date().toLocaleString("en-US", {{{{timeZone: tz}}}}));
  const t12 = now.toLocaleTimeString('en-US', {{{{hour:'numeric', minute:'2-digit', hour12:true, timeZone: tz}}}});
  document.getElementById("city").textContent = scheduleLocation.displayName + " (" + tz + ")";
  document.getElementById("time").textContent = t12;

  const status = getStatusFromNow(now);
  const statusEl = document.getElementById("status");
  statusEl.className = "call-status " + status.cls;
  statusEl.textContent = status.status;

  const note = now.getHours() < 7 ? "Very Early Morning" : now.getHours() < 12 ? "Morning" : now.getHours() < 17 ? "Afternoon" : now.getHours() < 21 ? "Evening" : "Night";
  document.getElementById("note").textContent = note;

  const next = getNextClassTime(now);
  document.getElementById("next").textContent = next ? "Next class in " + formatTimeDifference(next, now) : "No more upcoming classes today.";
}}}}

fetchAndParse();
</script>
</body>
</html>"""
    return render_template_string(html)

if __name__ == "__main__":
    # When testing locally, use: python server.py
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
