from flask import Flask, jsonify, request, render_template_string
import requests, os, re, json, logging, zlib, gzip
from functools import lru_cache

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_URL = "https://faphouse2.com"
EMAIL    = os.environ.get("EMAIL", "")
PASSWORD = os.environ.get("PASSWORD", "")

class FaphouseClient:
    def __init__(self):
        self._sess  = None
        self._token = None

    def _make_session(self):
        s = requests.Session()
        s.headers.update({
            "User-Agent":                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                         "AppleWebKit/537.36 (KHTML, like Gecko) "
                                         "Chrome/124.0.0.0 Safari/537.36",
            "Accept":                    "text/html,application/xhtml+xml,*/*;q=0.9",
            "Accept-Language":           "en-US,en;q=0.9",
            "Accept-Encoding":           "gzip, deflate",  # br বাদ — brotli নেই Railway তে
            "DNT":                       "1",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest":            "document",
            "Sec-Fetch-Mode":            "navigate",
            "Sec-Fetch-Site":            "none",
        })
        return s

    def _get_session(self):
        if self._sess:
            return self._sess
        s = self._make_session()

        # warm-up cookies
        try:
            s.get(BASE_URL, timeout=10)
        except Exception:
            pass

        # login
        if EMAIL:
            try:
                s.headers.update({
                    "Accept":       "application/json, text/plain, */*",
                    "Content-Type": "application/json",
                    "Origin":       BASE_URL,
                    "Referer":      BASE_URL + "/",
                })
                r = s.post(
                    f"{BASE_URL}/api/auth/signin",
                    json={
                        "login":             EMAIL,
                        "password":          PASSWORD,
                        "rememberMe":        "1",
                        "recaptcha":         "",
                        "trackingParamsBag": "",
                    },
                    timeout=15,
                )
                logger.info(f"Login → {r.status_code}")
                if r.status_code == 200:
                    try:
                        d   = r.json()
                        tok = (d.get("data") or {}).get("token") or d.get("token")
                        if tok:
                            self._token = tok
                            s.headers["Authorization"] = f"Bearer {tok}"
                            logger.info("✅ Bearer token")
                    except Exception:
                        pass
                    logger.info("✅ Session ok")
                # reset accept header for page fetches
                s.headers["Accept"] = "text/html,application/xhtml+xml,*/*;q=0.9"
                s.headers.pop("Content-Type", None)
            except Exception as e:
                logger.warning(f"Login err: {e}")

        self._sess = s
        return s

    @staticmethod
    def _slug(url):
        return url.rstrip("/").split("/")[-1]

    @staticmethod
    def _decode(resp):
        enc = resp.headers.get("Content-Encoding", "")
        if "gzip" in enc:
            try:
                return gzip.decompress(resp.content).decode("utf-8", errors="ignore")
            except Exception:
                pass
        if "deflate" in enc:
            try:
                return zlib.decompress(resp.content).decode("utf-8", errors="ignore")
            except Exception:
                try:
                    return zlib.decompress(resp.content, -zlib.MAX_WBITS).decode("utf-8", errors="ignore")
                except Exception:
                    pass
        return resp.text

    # ── Main entry ─────────────────────────────
    @lru_cache(maxsize=200)
    def get_m3u8_url(self, video_url: str):
        video_url = video_url.split("#")[0].strip()
        logger.info(f"🔍 {video_url[:80]}")
        return (
            self._from_nuxt_api(video_url)
            or self._from_page_nuxt(video_url)
            or self._from_embed(video_url)
            or self._from_regex(video_url)
        )

    # ── Method 1: Nuxt JSON API ────────────────
    def _from_nuxt_api(self, video_url):
        slug = self._slug(video_url)
        s    = self._get_session()
        endpoints = [
            f"{BASE_URL}/api/video/{slug}",
            f"{BASE_URL}/api/videos/{slug}",
            f"{BASE_URL}/api/v1/video/{slug}",
            f"{BASE_URL}/api/v2/video/{slug}",
            f"{BASE_URL}/api/content/video/{slug}",
            f"{BASE_URL}/api/content/{slug}",
        ]
        for ep in endpoints:
            try:
                r = s.get(ep, headers={
                    "Accept": "application/json, */*",
                    "Referer": video_url,
                }, timeout=10)
                logger.info(f"API {ep[-50:]} → {r.status_code}")
                if r.status_code == 200:
                    m3u8 = self._hunt_dict(r.json())
                    if m3u8:
                        logger.info(f"✅ Nuxt API: {m3u8[:70]}")
                        return m3u8
            except Exception as e:
                logger.debug(e)
        return None

    # ── Method 2: __NUXT_DATA__ in page ────────
    def _from_page_nuxt(self, video_url):
        s = self._get_session()
        try:
            r = s.get(video_url, timeout=15)
            logger.info(f"Page → {r.status_code}")
            if r.status_code != 200:
                return None
            html = self._decode(r)

            # Nuxt3 uses <script type="application/json" id="__NUXT_DATA__">
            m = re.search(
                r'<script[^>]+id=["\']__NUXT_DATA__["\'][^>]*>(.*?)</script>',
                html, re.DOTALL | re.IGNORECASE
            )
            if m:
                try:
                    raw  = json.loads(m.group(1))
                    m3u8 = self._hunt_list_or_dict(raw)
                    if m3u8:
                        logger.info(f"✅ NUXT_DATA: {m3u8[:70]}")
                        return m3u8
                except Exception:
                    pass

            # Nuxt2 __NUXT__ = {...}
            for var in ("__NUXT__", "__INITIAL_STATE__", "window.__data__", "__STATE__"):
                pat = rf"{re.escape(var)}\s*=\s*(\{{.*?\}})\s*[;<]"
                mm  = re.search(pat, html, re.DOTALL)
                if mm:
                    try:
                        data = json.loads(mm.group(1))
                        m3u8 = self._hunt_dict(data)
                        if m3u8:
                            logger.info(f"✅ {var}: {m3u8[:70]}")
                            return m3u8
                    except Exception:
                        pass

            # inline JSON blocks containing m3u8
            for block in re.findall(r'\{[^{}]{200,}\}', html):
                if '.m3u8' not in block:
                    continue
                try:
                    data = json.loads(block)
                    m3u8 = self._hunt_dict(data)
                    if m3u8:
                        logger.info(f"✅ inline JSON: {m3u8[:70]}")
                        return m3u8
                except Exception:
                    pass

        except Exception as e:
            logger.warning(f"Page err: {e}")
        return None

    # ── Method 3: embed / player page ──────────
    def _from_embed(self, video_url):
        slug = self._slug(video_url)
        s    = self._get_session()
        for url in [
            f"{BASE_URL}/embed/{slug}",
            f"{BASE_URL}/player/{slug}",
            f"https://player.faphouse2.com/{slug}",
            f"https://player.faphouse2.com/embed/{slug}",
        ]:
            try:
                r = s.get(url, timeout=10)
                logger.info(f"Embed {url[-50:]} → {r.status_code}")
                if r.status_code == 200:
                    m3u8 = self._regex_hunt(self._decode(r))
                    if m3u8:
                        logger.info(f"✅ Embed: {m3u8[:70]}")
                        return m3u8
            except Exception as e:
                logger.debug(e)
        return None

    # ── Method 4: raw regex on page ────────────
    def _from_regex(self, video_url):
        s = self._get_session()
        try:
            r    = s.get(video_url, timeout=15)
            m3u8 = self._regex_hunt(self._decode(r))
            if m3u8:
                logger.info(f"✅ Regex: {m3u8[:70]}")
            return m3u8
        except Exception as e:
            logger.debug(e)
        return None

    # ── Hunters ────────────────────────────────
    @staticmethod
    def _is_full(url: str) -> bool:
        low = url.lower()
        return ".m3u8" in low and not any(
            x in low for x in ("trailer", "preview", "sample", "teaser")
        )

    @classmethod
    def _regex_hunt(cls, html: str) -> str | None:
        html = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', html)
        pats = [
            r'(https?://[^\s"\'<>\\]+\.m3u8(?:\?[^\s"\'<>\\]*)?)',
            r'(//.+?\.m3u8(?:\?[^\s"\'<>\\]*)?)',
        ]
        candidates = []
        for pat in pats:
            for m in re.finditer(pat, html, re.IGNORECASE):
                url = m.group(1).replace("&amp;", "&")
                if url.startswith("//"):
                    url = "https:" + url
                if url.startswith("http") and cls._is_full(url):
                    candidates.append(url)
        # prefer 1080
        for c in candidates:
            if "1080" in c:
                return c
        return candidates[0] if candidates else None

    @classmethod
    def _hunt_list_or_dict(cls, data, depth=0) -> str | None:
        if depth > 12:
            return None
        if isinstance(data, list):
            best = None
            for item in data:
                r = cls._hunt_list_or_dict(item, depth + 1)
                if r:
                    if "1080" in r:
                        return r
                    best = best or r
            return best
        return cls._hunt_dict(data, depth)

    @classmethod
    def _hunt_dict(cls, data, depth=0) -> str | None:
        if depth > 12:
            return None
        if isinstance(data, str):
            return data if cls._is_full(data) else None
        if isinstance(data, list):
            return cls._hunt_list_or_dict(data, depth)
        if isinstance(data, dict):
            low = {k.lower(): k for k in data}
            # skip trailer keys
            for bad in ("trailer", "preview", "sample", "teaser"):
                low.pop(bad, None)
            # priority order — full video keys first
            for key in ("hls", "full", "fullvideo", "full_video",
                        "stream", "sources", "source",
                        "video", "url", "src", "file", "m3u8"):
                if key in low:
                    r = cls._hunt_dict(data[low[key]], depth + 1)
                    if r:
                        return r
            # quality levels
            for q in ("1080p", "1080", "720p", "720", "480p", "480"):
                if q in low:
                    r = cls._hunt_dict(data[low[q]], depth + 1)
                    if r:
                        return r
            # everything else
            for v in data.values():
                r = cls._hunt_dict(v, depth + 1)
                if r:
                    return r
        return None


client = FaphouseClient()

# ══════════════════════════════════════════════
# TEMPLATES
# ══════════════════════════════════════════════
_HOME = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
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
  .brand-text h1{font-size:20px;font-weight:700;letter-spacing:-.3px}
  .brand-text span{font-size:11px;color:var(--muted)}
  .dev-tag{display:inline-block;background:rgba(124,92,252,.15);border:1px solid rgba(124,92,252,.3);
           color:var(--accent2);font-size:10px;font-weight:600;letter-spacing:.08em;
           padding:3px 10px;border-radius:20px;margin-bottom:22px;text-transform:uppercase}
  .input-wrap{position:relative;margin-bottom:12px}
  .input-wrap svg{position:absolute;left:14px;top:50%;transform:translateY(-50%);color:var(--muted);pointer-events:none}
  input[type=text]{width:100%;padding:13px 14px 13px 42px;background:rgba(255,255,255,.05);
                   border:1px solid var(--border);border-radius:10px;color:var(--text);
                   font-size:13px;outline:none;transition:border-color .2s,box-shadow .2s;font-family:'Inter',monospace}
  input[type=text]::placeholder{color:var(--muted)}
  input[type=text]:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(124,92,252,.15)}
  button{width:100%;padding:13px;background:linear-gradient(135deg,var(--accent),#6d4fe8);
         border:none;border-radius:10px;color:#fff;font-size:14px;font-weight:700;cursor:pointer;
         transition:transform .15s,box-shadow .15s;letter-spacing:.02em;
         box-shadow:0 4px 15px rgba(124,92,252,.35)}
  button:hover{transform:translateY(-1px);box-shadow:0 6px 20px rgba(124,92,252,.5)}
  button:active{transform:translateY(0)}
  .hint{color:var(--muted);font-size:11px;margin-top:10px;text-align:center}
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
  .status-chip{display:inline-flex;align-items:center;gap:6px;font-size:11px;
               color:var(--muted);margin-bottom:18px}
  .dot{width:7px;height:7px;border-radius:50%;background:var(--green);
       box-shadow:0 0 5px var(--green);animation:pulse 2s infinite}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
</style></head><body>
<div class="card">
  <div class="brand">
    <div class="logo">🎬</div>
    <div class="brand-text"><h1>FapPlayer</h1><span>Premium stream extractor</span></div>
  </div>
  <div class="dev-tag">⚡ by Ariyan Sefat</div><br>
  <div class="status-chip"><div class="dot"></div> Live</div>
  <form method="GET" action="/play">
    <div class="input-wrap">
      <svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
        <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/>
        <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>
      </svg>
      <input name="url" type="text" placeholder="https://faphouse2.com/videos/..." required>
    </div>
    <button type="submit">▶ &nbsp;Watch in 1080p</button>
  </form>
  <p class="hint">Set EMAIL + PASSWORD env vars for premium access</p>
  <hr>
  <p class="api-title">API Endpoints</p>
  <div class="endpoints">
    <div class="ep"><span class="method">GET</span>/play?url=VIDEO_URL</div>
    <div class="ep"><span class="method">GET</span>/api/m3u8?url=VIDEO_URL</div>
    <div class="ep"><span class="method">GET</span>/api/status</div>
  </div>
  <p class="footer">Built with 💜 by <span>Ariyan Sefat</span></p>
</div></body></html>"""

_PLAYER = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
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
  a.back-btn{display:flex;align-items:center;gap:6px;color:var(--muted);text-decoration:none;
             font-size:12px;padding:6px 12px;background:var(--glass);border:1px solid var(--border);
             border-radius:8px;transition:color .2s,border-color .2s}
  a.back-btn:hover{color:var(--text);border-color:var(--accent)}
  .player-wrap{width:100%;max-width:1100px;aspect-ratio:16/9;background:#000;
               border-radius:16px;overflow:hidden;border:1px solid var(--border);
               box-shadow:0 20px 60px rgba(0,0,0,.7)}
  #player{width:100%!important;height:100%!important}
  .badge{display:inline-flex;align-items:center;gap:5px;background:rgba(124,92,252,.15);
         border:1px solid rgba(124,92,252,.3);color:#a78bfa;font-size:10px;font-weight:700;
         padding:3px 10px;border-radius:20px;letter-spacing:.05em;text-transform:uppercase;margin-top:12px}
  .meta-bar{width:100%;max-width:1100px;background:var(--glass);border:1px solid var(--border);
            border-radius:10px;padding:10px 14px;margin-top:10px;display:flex;align-items:center;gap:10px}
  .meta-url{font-size:11px;color:var(--muted);word-break:break-all;flex:1}
  .meta-url a{color:var(--green);text-decoration:none}
  .footer{margin-top:16px;font-size:10px;color:var(--muted)}
  .footer span{color:#a78bfa}
  .video-js .vjs-big-play-button{background:linear-gradient(135deg,rgba(124,92,252,.8),rgba(109,79,232,.8));
    border:2px solid rgba(255,255,255,.2);border-radius:50%;width:60px;height:60px;line-height:56px;
    font-size:22px;left:50%;top:50%;transform:translate(-50%,-50%)}
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
  <span class="badge">⚡ 1080p • HLS Stream</span>
</div>
<div class="meta-bar">
  <div class="meta-url">
    <a href="{{ m3u8_url }}" target="_blank">{{ m3u8_url[:130] }}</a>
  </div>
</div>
<p class="footer">Built with 💜 by <span>Ariyan Sefat</span></p>
<script src="https://vjs.zencdn.net/8.6.1/video.min.js"></script>
<script>
var player = videojs('player', {
  techOrder: ['html5'],
  html5: { hls: { overrideNative: true, enableLowInitialPlaylist: false, smoothQualityChange: true } },
  playbackRates: [0.5, 0.75, 1, 1.25, 1.5, 2],
  responsive: true, fluid: true
});
player.ready(function() {
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
  this.play().catch(function(){});
});
</script></body></html>"""

_ERR = """<!DOCTYPE html><html><head><meta charset="UTF-8"><style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;700&display=swap');
  *{margin:0;padding:0;box-sizing:border-box}
  body{background:#080b10;color:#e2e8f0;display:flex;align-items:center;justify-content:center;
       min-height:100vh;font-family:'Inter',system-ui;
       background-image:radial-gradient(ellipse at 50% 30%,rgba(248,113,113,.08) 0%,transparent 60%)}
  .box{text-align:center;max-width:480px;padding:20px}
  .icon{font-size:48px;margin-bottom:16px}
  h2{color:#f87171;margin-bottom:10px;font-size:20px}
  p{color:#4a5568;margin-bottom:24px;font-size:13px;line-height:1.6}
  a{color:#e2e8f0;padding:10px 24px;background:rgba(255,255,255,.05);
    border:1px solid rgba(255,255,255,.08);border-radius:8px;text-decoration:none;
    display:inline-block;font-size:13px;transition:border-color .2s}
  a:hover{border-color:#7c5cfc}
  .sub{font-size:10px;color:#2d3748;margin-top:16px}
</style></head><body><div class="box">
  <div class="icon">🚫</div>
  <h2>{{ t }}</h2><p>{{ m }}</p>
  <a href="/">← Back to Home</a>
  <p class="sub">FapPlayer by Ariyan Sefat</p>
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
        return err("No URL", "Pass ?url=VIDEO_URL", 400)
    m3u8 = client.get_m3u8_url(url)
    if not m3u8:
        return err(
            "Stream Not Found",
            "M3U8 not found. Login may have failed — check Railway logs.", 404
        )
    return render_template_string(_PLAYER, m3u8_url=m3u8, video_url=url)

@app.route("/api/m3u8")
def api_m3u8():
    url = request.args.get("url", "").strip().split("#")[0]
    if not url:
        return jsonify({"error": "Missing url"}), 400
    m3u8 = client.get_m3u8_url(url)
    if m3u8:
        return jsonify({"success": True, "m3u8_url": m3u8})
    return jsonify({"success": False, "error": "No M3U8 found"}), 404

@app.route("/api/status")
def api_status():
    return jsonify({
        "status":     "online",
        "logged_in":  client._sess is not None,
        "has_token":  client._token is not None,
        "cache_size": client.get_m3u8_url.cache_info()._asdict(),
        "developer":  "Ariyan Sefat",
    })

if __name__ == "__main__":
    print(f"FapPlayer by Ariyan Sefat — :5000  |  {EMAIL[:5]}…")
    app.run(host="0.0.0.0", port=5000, debug=False)
