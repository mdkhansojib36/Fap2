from flask import Flask, jsonify, request, render_template_string
import os, logging, subprocess, json

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

EMAIL    = os.environ.get("EMAIL", "")
PASSWORD = os.environ.get("PASSWORD", "")

_cache: dict[str, str] = {}

def get_m3u8(video_url: str) -> str | None:
    if video_url in _cache:
        return _cache[video_url]

    cmd = [
        "yt-dlp",
        "--no-warnings",
        "--quiet",
        "-f", "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
        "--get-url",
        video_url
    ]

    if EMAIL and PASSWORD:
        cmd += ["--username", EMAIL, "--password", PASSWORD]

    try:
        logger.info(f"yt-dlp → {video_url[:60]}")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60
        )
        logger.info(f"yt-dlp exit: {result.returncode}")
        if result.returncode == 0:
            lines = [l.strip() for l in result.stdout.strip().splitlines() if l.strip()]
            if lines:
                # m3u8 prefer
                for line in lines:
                    if ".m3u8" in line:
                        _cache[video_url] = line
                        logger.info(f"✅ M3U8: {line[:70]}")
                        return line
                # fallback to first URL
                url = lines[0]
                _cache[video_url] = url
                logger.info(f"✅ URL: {url[:70]}")
                return url
        else:
            logger.error(f"yt-dlp stderr: {result.stderr[:300]}")
    except subprocess.TimeoutExpired:
        logger.error("yt-dlp timeout")
    except Exception as e:
        logger.error(f"yt-dlp error: {e}")

    return None


# ══════════════════════════════════════════════
# TEMPLATES
# ══════════════════════════════════════════════
_HOME = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>FapPlayer — Ariyan Sefat</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&display=swap');
  *,*::before,*::after{margin:0;padding:0;box-sizing:border-box}
  :root{--bg:#080b10;--glass:rgba(255,255,255,0.04);--border:rgba(255,255,255,0.08);
        --accent:#7c5cfc;--accent2:#a78bfa;--text:#e2e8f0;--muted:#4a5568;--green:#34d399}
  body{background:var(--bg);color:var(--text);font-family:'Inter',system-ui,sans-serif;
       min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px;
       background-image:radial-gradient(ellipse at 20% 50%,rgba(124,92,252,.12) 0%,transparent 60%),
                        radial-gradient(ellipse at 80% 20%,rgba(167,139,250,.08) 0%,transparent 50%)}
  .card{background:var(--glass);border:1px solid var(--border);border-radius:20px;
        padding:40px 36px;max-width:560px;width:100%;backdrop-filter:blur(20px);
        box-shadow:0 25px 50px rgba(0,0,0,.5)}
  .brand{display:flex;align-items:center;gap:12px;margin-bottom:28px}
  .logo{width:44px;height:44px;background:linear-gradient(135deg,var(--accent),var(--accent2));
        border-radius:12px;display:flex;align-items:center;justify-content:center;font-size:20px;
        box-shadow:0 4px 15px rgba(124,92,252,.4)}
  .brand-text h1{font-size:20px;font-weight:700}
  .brand-text span{font-size:11px;color:var(--muted)}
  .dev-tag{display:inline-block;background:rgba(124,92,252,.15);border:1px solid rgba(124,92,252,.3);
           color:var(--accent2);font-size:10px;font-weight:600;letter-spacing:.08em;
           padding:3px 10px;border-radius:20px;margin-bottom:22px;text-transform:uppercase}
  .input-wrap{position:relative;margin-bottom:12px}
  .input-wrap svg{position:absolute;left:14px;top:50%;transform:translateY(-50%);
                  color:var(--muted);pointer-events:none}
  input[type=text]{width:100%;padding:13px 14px 13px 42px;background:rgba(255,255,255,.05);
                   border:1px solid var(--border);border-radius:10px;color:var(--text);
                   font-size:13px;outline:none;transition:border-color .2s,box-shadow .2s;
                   font-family:'Inter',monospace}
  input[type=text]::placeholder{color:var(--muted)}
  input[type=text]:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(124,92,252,.15)}
  button{width:100%;padding:13px;background:linear-gradient(135deg,var(--accent),#6d4fe8);
         border:none;border-radius:10px;color:#fff;font-size:14px;font-weight:700;cursor:pointer;
         transition:transform .15s,box-shadow .15s;box-shadow:0 4px 15px rgba(124,92,252,.35)}
  button:hover{transform:translateY(-1px);box-shadow:0 6px 20px rgba(124,92,252,.5)}
  button:active{transform:translateY(0)}
  .hint{color:var(--muted);font-size:11px;margin-top:10px;text-align:center}
  .warn{background:rgba(251,191,36,.08);border:1px solid rgba(251,191,36,.2);
        border-radius:8px;padding:10px 14px;font-size:11px;color:#fbbf24;margin-bottom:16px;line-height:1.5}
  hr{border:none;border-top:1px solid var(--border);margin:28px 0}
  .api-title{color:var(--muted);font-size:10px;font-weight:600;letter-spacing:.1em;
             text-transform:uppercase;margin-bottom:12px}
  .endpoints{display:flex;flex-direction:column;gap:6px}
  .ep{display:flex;align-items:center;gap:10px;background:rgba(255,255,255,.02);
      border:1px solid var(--border);border-radius:8px;padding:9px 12px;font-size:12px;color:var(--muted)}
  .method{font-size:10px;font-weight:700;padding:2px 7px;border-radius:5px;
          background:rgba(52,211,153,.15);color:var(--green);letter-spacing:.05em;flex-shrink:0}
  .footer{margin-top:24px;text-align:center;font-size:10px;color:var(--muted)}
  .footer span{color:var(--accent2)}
</style></head><body>
<div class="card">
  <div class="brand">
    <div class="logo">🎬</div>
    <div class="brand-text"><h1>FapPlayer</h1><span>yt-dlp powered extractor</span></div>
  </div>
  <div class="dev-tag">⚡ by Ariyan Sefat</div>
  <div class="warn">⏳ প্রথম request এ ~20 সেকেন্ড লাগতে পারে — yt-dlp processing করছে</div>
  <form method="GET" action="/play">
    <div class="input-wrap">
      <svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
        <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/>
        <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>
      </svg>
      <input name="url" type="text" placeholder="https://faphouse2.com/videos/..." required>
    </div>
    <button type="submit">▶  Watch Now</button>
  </form>
  <p class="hint">EMAIL + PASSWORD env vars set থাকলে premium video চলবে</p>
  <hr>
  <p class="api-title">API</p>
  <div class="endpoints">
    <div class="ep"><span class="method">GET</span>/play?url=VIDEO_URL</div>
    <div class="ep"><span class="method">GET</span>/api/m3u8?url=VIDEO_URL</div>
    <div class="ep"><span class="method">GET</span>/api/status</div>
  </div>
  <p class="footer">Built with 💜 by <span>Ariyan Sefat</span></p>
</div></body></html>"""

_PLAYER = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>FapPlayer</title>
<link href="https://vjs.zencdn.net/8.6.1/video-js.css" rel="stylesheet">
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;700&display=swap');
  *,*::before,*::after{margin:0;padding:0;box-sizing:border-box}
  :root{--bg:#080b10;--glass:rgba(255,255,255,0.04);--border:rgba(255,255,255,0.08);
        --accent:#7c5cfc;--text:#e2e8f0;--muted:#4a5568;--green:#34d399}
  body{background:var(--bg);color:var(--text);font-family:'Inter',system-ui,sans-serif;
       min-height:100vh;display:flex;flex-direction:column;align-items:center;padding:16px;
       background-image:radial-gradient(ellipse at 50% 0%,rgba(124,92,252,.1) 0%,transparent 60%)}
  .topbar{width:100%;max-width:1100px;display:flex;align-items:center;
          justify-content:space-between;padding:12px 0 16px}
  .brand-sm{display:flex;align-items:center;gap:8px;font-weight:700;font-size:15px}
  .dot{width:8px;height:8px;border-radius:50%;background:var(--green);
       box-shadow:0 0 6px var(--green);animation:pulse 2s infinite}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
  a.back-btn{color:var(--muted);text-decoration:none;font-size:12px;padding:6px 12px;
             background:var(--glass);border:1px solid var(--border);border-radius:8px;
             transition:color .2s,border-color .2s}
  a.back-btn:hover{color:var(--text);border-color:var(--accent)}
  .player-wrap{width:100%;max-width:1100px;aspect-ratio:16/9;background:#000;
               border-radius:16px;overflow:hidden;border:1px solid var(--border);
               box-shadow:0 20px 60px rgba(0,0,0,.7)}
  #player{width:100%!important;height:100%!important}
  .badge{display:inline-flex;align-items:center;gap:5px;background:rgba(124,92,252,.15);
         border:1px solid rgba(124,92,252,.3);color:#a78bfa;font-size:10px;font-weight:700;
         padding:3px 10px;border-radius:20px;letter-spacing:.05em;text-transform:uppercase;margin-top:12px}
  .meta-bar{width:100%;max-width:1100px;background:var(--glass);border:1px solid var(--border);
            border-radius:10px;padding:10px 14px;margin-top:10px}
  .meta-url{font-size:11px;color:var(--muted);word-break:break-all}
  .meta-url a{color:var(--green);text-decoration:none}
  .footer{margin-top:16px;font-size:10px;color:var(--muted)}
  .footer span{color:#a78bfa}
  .video-js .vjs-big-play-button{background:linear-gradient(135deg,rgba(124,92,252,.8),rgba(109,79,232,.8));
    border:2px solid rgba(255,255,255,.2);border-radius:50%;width:60px;height:60px;
    line-height:56px;left:50%;top:50%;transform:translate(-50%,-50%)}
  .video-js .vjs-play-progress{background:var(--accent)}
</style></head><body>
<div class="topbar">
  <div class="brand-sm"><div class="dot"></div> FapPlayer</div>
  <a class="back-btn" href="/">← Home</a>
</div>
<div class="player-wrap">
  <video id="player" class="video-js vjs-default-skin vjs-big-play-centered"
         controls autoplay preload="auto">
    <source src="{{ m3u8_url }}" type="application/x-mpegURL">
  </video>
</div>
<div style="width:100%;max-width:1100px">
  <span class="badge">⚡ Best Quality • yt-dlp</span>
</div>
<div class="meta-bar">
  <div class="meta-url">
    <a href="{{ m3u8_url }}" target="_blank">{{ m3u8_url[:130] }}</a>
  </div>
</div>
<p class="footer">Built with 💜 by <span>Ariyan Sefat</span></p>
<script src="https://vjs.zencdn.net/8.6.1/video.min.js"></script>
<script>
var p = videojs('player', {
  techOrder: ['html5'],
  html5: { hls: { overrideNative: true, enableLowInitialPlaylist: false } },
  playbackRates: [0.5, 0.75, 1, 1.25, 1.5, 2],
  responsive: true, fluid: true
});
p.ready(function() {
  this.on('loadedmetadata', function() {
    var q = this.qualityLevels ? this.qualityLevels() : null;
    if (q && q.length) {
      var best = -1, bestH = 0;
      for (var i = 0; i < q.length; i++) {
        if (q[i].height > bestH) { bestH = q[i].height; best = i; }
      }
      for (var j = 0; j < q.length; j++) { q[j].enabled = (j === best); }
    }
  });
  this.play().catch(function() {});
});
</script></body></html>"""

_ERR = """<!DOCTYPE html><html><head><meta charset="UTF-8"><style>
  *{margin:0;padding:0;box-sizing:border-box}
  body{background:#080b10;color:#e2e8f0;display:flex;align-items:center;
       justify-content:center;min-height:100vh;font-family:system-ui;
       background-image:radial-gradient(ellipse at 50% 30%,rgba(248,113,113,.08) 0%,transparent 60%)}
  .b{text-align:center;max-width:480px;padding:20px}
  .icon{font-size:48px;margin-bottom:16px}
  h2{color:#f87171;margin-bottom:10px}
  p{color:#4a5568;margin-bottom:24px;font-size:13px;line-height:1.6}
  a{color:#e2e8f0;padding:10px 24px;background:rgba(255,255,255,.05);
    border:1px solid rgba(255,255,255,.08);border-radius:8px;text-decoration:none;font-size:13px}
  a:hover{border-color:#7c5cfc}
</style></head><body><div class="b">
  <div class="icon">🚫</div>
  <h2>{{ t }}</h2><p>{{ m }}</p>
  <a href="/">← Back to Home</a>
</div></body></html>"""

def err(t, m, code=500):
    return render_template_string(_ERR, t=t, m=m), code


# ══════════════════════════════════════════════
# ROUTES
# ══════════════════════════════════════════════
@app.route("/")
def index():
    return _HOME

@app.route("/play")
def play():
    url = request.args.get("url", "").strip().split("#")[0]
    if not url:
        return err("No URL", "?url=VIDEO_URL দাও", 400)
    m3u8 = get_m3u8(url)
    if not m3u8:
        return err("Stream Not Found",
                   "yt-dlp m3u8 পায়নি। Premium account দরকার হতে পারে।", 404)
    return render_template_string(_PLAYER, m3u8_url=m3u8, video_url=url)

@app.route("/api/m3u8")
def api_m3u8():
    url = request.args.get("url", "").strip().split("#")[0]
    if not url:
        return jsonify({"error": "Missing url"}), 400
    m3u8 = get_m3u8(url)
    if m3u8:
        return jsonify({"success": True, "m3u8_url": m3u8})
    return jsonify({"success": False, "error": "Not found"}), 404

@app.route("/api/status")
def api_status():
    return jsonify({
        "status":    "online",
        "engine":    "yt-dlp",
        "cache":     len(_cache),
        "developer": "Ariyan Sefat",
    })

if __name__ == "__main__":
    print("FapPlayer by Ariyan Sefat — yt-dlp engine — :5000")
    app.run(host="0.0.0.0", port=5000, debug=False)
