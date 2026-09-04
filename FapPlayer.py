from flask import Flask, jsonify, request, render_template_string
import requests, os, re, logging, json, time

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_URL = "https://faphouse2.com"
EMAIL    = os.environ.get("EMAIL", "mdkhansojib36@gmail.com")
PASSWORD = os.environ.get("PASSWORD", "sojibkhusi12*#12")

# ══════════════════════════════════════════════
# CORE CLIENT — pure requests, no browser
# ══════════════════════════════════════════════
class FaphouseClient:
    def __init__(self):
        self._cache: dict[str, str] = {}
        self._sess:  requests.Session | None = None
        self._token: str | None = None

    # ── session ──────────────────────────────
    def _headers(self, extra: dict = {}) -> dict:
        h = {
            "User-Agent":      "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
                               "AppleWebKit/537.36 (KHTML, like Gecko) "
                               "Chrome/124.0.6367.82 Mobile Safari/537.36",
            "Accept":          "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "Origin":          BASE_URL,
            "Referer":         BASE_URL + "/",
        }
        h.update(extra)
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    def _get_session(self) -> requests.Session:
        if self._sess:
            return self._sess
        s = requests.Session()
        s.headers.update(self._headers())

        # warm-up — grab cookies
        try:
            s.get(BASE_URL, timeout=10)
        except Exception:
            pass

        # login
        if EMAIL != "ENTER_YOUR_EMAIL":
            try:
                r = s.post(
                    f"{BASE_URL}/api/auth/signin",
                    json={
                        "login":             EMAIL,
                        "password":          PASSWORD,
                        "rememberMe":        "1",
                        "recaptcha":         "",
                        "trackingParamsBag": "",
                    },
                    headers=self._headers({"Content-Type": "application/json"}),
                    timeout=15,
                )
                logger.info(f"Login → {r.status_code}")
                if r.status_code == 200:
                    try:
                        data = r.json()
                        # grab token if returned
                        tok = (data.get("data") or {}).get("token") or data.get("token")
                        if tok:
                            self._token = tok
                            logger.info("✅ Got Bearer token")
                    except Exception:
                        pass
                    logger.info("✅ Session established")
            except Exception as e:
                logger.warning(f"Login error: {e}")

        self._sess = s
        return s

    # ── slug extractor ────────────────────────
    @staticmethod
    def _slug(url: str) -> str:
        return url.rstrip("/").split("/")[-1]

    # ── M3U8 hunting ─────────────────────────
    def get_m3u8_url(self, video_url: str) -> str | None:
        video_url = video_url.split("#")[0].strip()
        if video_url in self._cache:
            return self._cache[video_url]

        m3u8 = (
            self._try_video_api(video_url)
            or self._try_embed_api(video_url)
            or self._try_page_scrape(video_url)
            or self._try_cdn_guess(video_url)
        )

        if m3u8:
            self._cache[video_url] = m3u8
        return m3u8

    # ── method 1: /api/video/{slug} ──────────
    def _try_video_api(self, video_url: str) -> str | None:
        slug = self._slug(video_url)
        s    = self._get_session()

        for endpoint in [
            f"{BASE_URL}/api/video/{slug}",
            f"{BASE_URL}/api/videos/{slug}",
            f"{BASE_URL}/api/v1/video/{slug}",
            f"{BASE_URL}/api/v2/video/{slug}",
        ]:
            try:
                r = s.get(endpoint, headers=self._headers(), timeout=10)
                logger.info(f"API {endpoint[-40:]} → {r.status_code}")
                if r.status_code != 200:
                    continue
                data = r.json()
                m3u8 = self._find_m3u8_in_dict(data)
                if m3u8:
                    logger.info(f"✅ M3U8 from API: {m3u8[:60]}")
                    return m3u8
            except Exception as e:
                logger.debug(f"API error: {e}")
        return None

    # ── method 2: embed / player API ─────────
    def _try_embed_api(self, video_url: str) -> str | None:
        slug = self._slug(video_url)
        s    = self._get_session()

        embed_urls = [
            f"{BASE_URL}/embed/{slug}",
            f"{BASE_URL}/player/{slug}",
            f"https://player.faphouse2.com/{slug}",
        ]

        for url in embed_urls:
            try:
                r = s.get(url, headers=self._headers({
                    "Accept": "text/html,application/xhtml+xml,*/*",
                }), timeout=10)
                logger.info(f"Embed {url[-40:]} → {r.status_code}")
                if r.status_code == 200:
                    m3u8 = self._find_m3u8_in_html(r.text)
                    if m3u8:
                        logger.info(f"✅ M3U8 from embed: {m3u8[:60]}")
                        return m3u8
            except Exception as e:
                logger.debug(f"Embed error: {e}")
        return None

    # ── method 3: scrape the video page ──────
    def _try_page_scrape(self, video_url: str) -> str | None:
        s = self._get_session()
        try:
            r = s.get(video_url, headers=self._headers({
                "Accept": "text/html,application/xhtml+xml,*/*",
            }), timeout=15)
            logger.info(f"Page scrape → {r.status_code}")
            if r.status_code != 200:
                return None

            html = r.text

            # look for __NUXT__ or __INITIAL_STATE__ JSON blobs
            for var in ["__NUXT__", "__INITIAL_STATE__", "__STATE__", "window.__data__"]:
                pat = rf"{re.escape(var)}\s*=\s*(\{{.*?\}})\s*;"
                m   = re.search(pat, html, re.DOTALL)
                if m:
                    try:
                        data = json.loads(m.group(1))
                        m3u8 = self._find_m3u8_in_dict(data)
                        if m3u8:
                            logger.info(f"✅ M3U8 from {var}: {m3u8[:60]}")
                            return m3u8
                    except Exception:
                        pass

            # inline JSON arrays / objects near "hls" keyword
            hls_matches = re.findall(
                r'(?:hls|m3u8|stream|file|src)\s*[=:]\s*["\']?(https?://[^\s"\'<>\\]+\.m3u8[^\s"\'<>\\]*)',
                html, re.IGNORECASE
            )
            if hls_matches:
                url = hls_matches[0].replace("&amp;", "&")
                logger.info(f"✅ M3U8 from page scrape: {url[:60]}")
                return url

            # generic M3U8 URL pattern
            m3u8 = self._find_m3u8_in_html(html)
            if m3u8:
                return m3u8

        except Exception as e:
            logger.warning(f"Page scrape error: {e}")
        return None

    # ── method 4: CDN pattern guess ──────────
    def _try_cdn_guess(self, video_url: str) -> str | None:
        """
        Faphouse CDN usually follows:
        https://cdn[N].faphouse2.com/videos/{id}/master.m3u8
        Try to extract video ID from page meta or URL slug hash.
        """
        slug  = self._slug(video_url)
        s     = self._get_session()

        # extract ID from page (og:video or similar)
        try:
            r = s.get(video_url, headers=self._headers({
                "Accept": "text/html,*/*",
            }), timeout=10)
            if r.status_code == 200:
                # og:video content
                m = re.search(r'og:video[^>]*content=["\']([^"\']+)["\']', r.text)
                if m:
                    og = m.group(1)
                    if ".m3u8" in og:
                        return og
                    # extract CDN base
                    cdn_m = re.search(r'(https?://cdn\d*\.faphouse2\.com)', og)
                    if cdn_m:
                        cdn = cdn_m.group(1)
                        # try common ID patterns in slug
                        vid_id = slug.split("-")[-1]  # last segment is usually ID
                        for path in [
                            f"/videos/{vid_id}/master.m3u8",
                            f"/hls/{vid_id}/master.m3u8",
                            f"/stream/{vid_id}/index.m3u8",
                        ]:
                            url = cdn + path
                            try:
                                rr = requests.head(url, timeout=5)
                                if rr.status_code in (200, 206):
                                    logger.info(f"✅ CDN guess hit: {url}")
                                    return url
                            except Exception:
                                pass
        except Exception as e:
            logger.debug(f"CDN guess error: {e}")
        return None

    # ── helpers ───────────────────────────────
    @staticmethod
    def _find_m3u8_in_html(html: str) -> str | None:
        html = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", html)
        pats = [
            r'(https?://[^\s"\'<>\\]+\.m3u8(?:\?[^\s"\'<>\\]*)?)',
            r'(//.+?\.m3u8(?:\?[^\s"\'<>\\]*)?)',
        ]
        for pat in pats:
            for m in re.finditer(pat, html, re.IGNORECASE):
                url = m.group(1).replace("&amp;", "&")
                if url.startswith("//"):
                    url = "https:" + url
                if url.startswith("http"):
                    return url
        return None

    @staticmethod
    def _find_m3u8_in_dict(data, _depth=0) -> str | None:
        if _depth > 8:
            return None
        if isinstance(data, str):
            if ".m3u8" in data and data.startswith("http"):
                return data
            return None
        if isinstance(data, list):
            for item in data:
                r = FaphouseClient._find_m3u8_in_dict(item, _depth + 1)
                if r:
                    return r
        if isinstance(data, dict):
            # check high-priority keys first
            for key in ("hls", "m3u8", "url", "src", "file", "stream", "source", "video"):
                if key in data:
                    r = FaphouseClient._find_m3u8_in_dict(data[key], _depth + 1)
                    if r:
                        return r
            for v in data.values():
                r = FaphouseClient._find_m3u8_in_dict(v, _depth + 1)
                if r:
                    return r
        return None


client = FaphouseClient()


# ══════════════════════════════════════════════
# TEMPLATES
# ══════════════════════════════════════════════
_HOME = """<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>FapPlayer</title><style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0a0a0a;color:#fff;font-family:system-ui,sans-serif;
     min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px}
.card{background:#1a1a1a;border-radius:14px;padding:36px;max-width:580px;width:100%}
h1{font-size:24px;margin-bottom:4px}
.sub{color:#555;font-size:12px;margin-bottom:22px}
input{width:100%;padding:12px 14px;background:#252525;border:1px solid #333;
      border-radius:8px;color:#fff;font-size:14px;outline:none}
input:focus{border-color:#4CAF50}
button{width:100%;margin-top:10px;padding:13px;background:#4CAF50;border:none;
       border-radius:8px;color:#fff;font-size:15px;font-weight:700;cursor:pointer}
button:hover{background:#43a047}
.hint{color:#3a3a3a;font-size:11px;margin-top:8px}
.api{margin-top:24px;background:#111;border-radius:8px;padding:16px}
.api p{color:#444;font-size:11px;margin-bottom:8px;text-transform:uppercase;letter-spacing:.05em}
.ep{color:#666;font-size:12px;padding:5px 0;border-bottom:1px solid #1a1a1a}
.ep:last-child{border:none}.ep b{color:#4CAF50}
</style></head><body><div class="card">
  <h1>🎬 FapPlayer</h1>
  <p class="sub">No browser needed • pure API extraction</p>
  <form method="GET" action="/play">
    <input name="url" type="text" placeholder="https://faphouse2.com/videos/..." required>
    <button type="submit">▶ Watch Now</button>
  </form>
  <p class="hint">Make sure EMAIL + PASSWORD env vars are set for premium content.</p>
  <div class="api">
    <p>API</p>
    <div class="ep"><b>GET</b> /play?url=VIDEO_URL</div>
    <div class="ep"><b>GET</b> /api/m3u8?url=VIDEO_URL</div>
    <div class="ep"><b>GET</b> /api/status</div>
  </div>
</div></body></html>"""

_PLAYER = """<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>FapPlayer</title>
<link href="https://vjs.zencdn.net/8.6.1/video-js.css" rel="stylesheet"><style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#000;color:#fff;font-family:system-ui,sans-serif;
     display:flex;flex-direction:column;align-items:center;padding:16px;min-height:100vh}
.wrap{width:100%;max-width:1100px}
.vwrap{width:100%;aspect-ratio:16/9;background:#000;border-radius:10px;overflow:hidden}
#player{width:100%!important;height:100%!important}
.meta{margin-top:10px;background:#111;border-radius:8px;padding:10px;
      font-size:11px;color:#555;word-break:break-all}
.meta a{color:#4CAF50;text-decoration:none}
a.back{display:inline-block;margin-top:8px;color:#333;text-decoration:none;font-size:11px}
</style></head><body><div class="wrap">
  <div class="vwrap">
    <video id="player" class="video-js vjs-default-skin vjs-big-play-centered"
           controls autoplay preload="auto">
      <source src="{{ m3u8_url }}" type="application/x-mpegURL">
    </video>
  </div>
  <div class="meta">
    🎯 <a href="{{ m3u8_url }}" target="_blank">{{ m3u8_url[:100] }}</a>
  </div>
  <a class="back" href="/">← Back</a>
</div>
<script src="https://vjs.zencdn.net/8.6.1/video.min.js"></script>
<script>
  var p=videojs('player',{html5:{hls:{overrideNative:true,enableLowInitialPlaylist:true}}});
  p.ready(function(){this.play().catch(function(){})});
</script></body></html>"""

_ERR = """<!DOCTYPE html><html><head><meta charset="UTF-8"><style>
body{background:#0a0a0a;color:#fff;display:flex;align-items:center;
justify-content:center;min-height:100vh;font-family:system-ui}
.b{text-align:center;max-width:480px}h2{color:#f44336;margin-bottom:10px}
p{color:#555;margin-bottom:18px;font-size:14px}
a{color:#4CAF50;padding:9px 24px;background:#1a1a1a;border-radius:6px;
text-decoration:none;display:inline-block}
</style></head><body><div class="b">
<h2>{{ t }}</h2><p>{{ m }}</p><a href="/">← Home</a>
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
            "Could not find M3U8. Check EMAIL/PASSWORD env vars, "
            "or this video needs a premium account.", 404
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
        "cache_size": len(client._cache),
        "email":      EMAIL[:5] + "…",
    })

if __name__ == "__main__":
    print(f"FapPlayer — :5000  |  email: {EMAIL[:5]}…")
    app.run(host="0.0.0.0", port=5000, debug=False)