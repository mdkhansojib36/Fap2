from flask import Flask, jsonify, request, render_template_string
import requests, os, re, logging, json

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_URL = "https://faphouse2.com"
EMAIL    = os.environ.get("EMAIL", "mdkhansojib36@gmail.com")
PASSWORD = os.environ.get("PASSWORD", "sojibkhusi12*#12")

# ══════════════════════════════════════════════
# CORE CLIENT
# ══════════════════════════════════════════════
class FaphouseClient:
    def __init__(self):
        self._cache: dict[str, str] = {}
        self._sess:  requests.Session | None = None
        self._token: str | None = None

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
        try:
            s.get(BASE_URL, timeout=10)
        except Exception:
            pass

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
                        tok = (data.get("data") or {}).get("token") or data.get("token")
                        if tok:
                            self._token = tok
                            logger.info("✅ Bearer token captured")
                    except Exception:
                        pass
                    logger.info("✅ Session established")
            except Exception as e:
                logger.warning(f"Login error: {e}")

        self._sess = s
        return s

    @staticmethod
    def _slug(url: str) -> str:
        return url.rstrip("/").split("/")[-1]

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

    # ── FULL VIDEO priority keys (trailer বাদ) ──
    @staticmethod
    def _find_m3u8_in_dict(data, _depth=0) -> str | None:
        if _depth > 10:
            return None
        if isinstance(data, str):
            if ".m3u8" in data and data.startswith("http"):
                # trailer/preview URL skip
                low = data.lower()
                if "trailer" in low or "preview" in low or "sample" in low:
                    return None
                return data
            return None
        if isinstance(data, list):
            # 1080p আগে খোঁজো
            best = None
            for item in data:
                r = FaphouseClient._find_m3u8_in_dict(item, _depth + 1)
                if r:
                    if "1080" in r:
                        return r
                    if best is None:
                        best = r
            return best
        if isinstance(data, dict):
            low_keys = {k.lower(): k for k in data}

            # trailer/preview key explicitly skip
            skip = {"trailer", "preview", "sample", "teaser"}
            for sk in skip:
                low_keys.pop(sk, None)

            # full video priority order
            priority = [
                "hls", "full", "fullvideo", "full_video",
                "m3u8", "stream", "sources", "source",
                "video", "url", "src", "file",
            ]
            for key in priority:
                if key in low_keys:
                    real_key = low_keys[key]
                    r = FaphouseClient._find_m3u8_in_dict(data[real_key], _depth + 1)
                    if r:
                        return r

            # qualities dict — pick highest
            for qkey in ["1080p", "1080", "720p", "720", "480p", "480"]:
                if qkey in low_keys:
                    r = FaphouseClient._find_m3u8_in_dict(data[low_keys[qkey]], _depth + 1)
                    if r:
                        return r

            for v in data.values():
                r = FaphouseClient._find_m3u8_in_dict(v, _depth + 1)
                if r:
                    return r
        return None

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
                logger.info(f"API {endpoint[-50:]} → {r.status_code}")
                if r.status_code != 200:
                    continue
                data = r.json()
                m3u8 = self._find_m3u8_in_dict(data)
                if m3u8:
                    logger.info(f"✅ M3U8 from API: {m3u8[:80]}")
                    return m3u8
            except Exception as e:
                logger.debug(f"API error: {e}")
        return None

    def _try_embed_api(self, video_url: str) -> str | None:
        slug = self._slug(video_url)
        s    = self._get_session()
        for url in [
            f"{BASE_URL}/embed/{slug}",
            f"{BASE_URL}/player/{slug}",
            f"https://player.faphouse2.com/{slug}",
        ]:
            try:
                r = s.get(url, headers=self._headers({
                    "Accept": "text/html,application/xhtml+xml,*/*",
                }), timeout=10)
                logger.info(f"Embed {url[-50:]} → {r.status_code}")
                if r.status_code == 200:
                    m3u8 = self._find_m3u8_in_html(r.text)
                    if m3u8:
                        logger.info(f"✅ M3U8 from embed: {m3u8[:80]}")
                        return m3u8
            except Exception as e:
                logger.debug(f"Embed error: {e}")
        return None

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

            for var in ["__NUXT__", "__INITIAL_STATE__", "__STATE__", "window.__data__"]:
                pat = rf"{re.escape(var)}\s*=\s*(\{{.*?\}})\s*;"
                m   = re.search(pat, html, re.DOTALL)
                if m:
                    try:
                        data = json.loads(m.group(1))
                        m3u8 = self._find_m3u8_in_dict(data)
                        if m3u8:
                            logger.info(f"✅ M3U8 from {var}: {m3u8[:80]}")
                            return m3u8
                    except Exception:
                        pass

            # trailer শব্দ বাদ দিয়ে full m3u8 খোঁজো
            hls_matches = re.findall(
                r'(?:hls|m3u8|stream|file|src)\s*[=:]\s*["\']?(https?://[^\s"\'<>\\]+\.m3u8[^\s"\'<>\\]*)',
                html, re.IGNORECASE
            )
            for candidate in hls_matches:
                low = candidate.lower()
                if "trailer" in low or "preview" in low or "sample" in low:
                    continue
                url = candidate.replace("&amp;", "&")
                logger.info(f"✅ M3U8 from page scrape: {url[:80]}")
                return url

            return self._find_m3u8_in_html(html)
        except Exception as e:
            logger.warning(f"Page scrape error: {e}")
        return None

    def _try_cdn_guess(self, video_url: str) -> str | None:
        slug = self._slug(video_url)
        s    = self._get_session()
        try:
            r = s.get(video_url, headers=self._headers({"Accept": "text/html,*/*"}), timeout=10)
            if r.status_code == 200:
                m = re.search(r'og:video[^>]*content=["\']([^"\']+)["\']', r.text)
                if m:
                    og = m.group(1)
                    if ".m3u8" in og and "trailer" not in og.lower():
                        return og
                    cdn_m = re.search(r'(https?://cdn\d*\.faphouse2\.com)', og)
                    if cdn_m:
                        cdn    = cdn_m.group(1)
                        vid_id = slug.split("-")[-1]
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
                low = url.lower()
                if "trailer" in low or "preview" in low or "sample" in low:
                    continue
                if url.startswith("//"):
                    url = "https:" + url
                if url.startswith("http"):
                    return url
        return None


client = FaphouseClient()


# ══════════════════════════════════════════════
# TEMPLATES — Ariyan Sefat Edition
# ══════════════════════════════════════════════
_HOME = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FapPlayer — Ariyan Sefat</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&display=swap');
  *, *::before, *::after { margin:0; padding:0; box-sizing:border-box; }

  :root {
    --bg:      #080b10;
    --glass:   rgba(255,255,255,0.04);
    --border:  rgba(255,255,255,0.08);
    --accent:  #7c5cfc;
    --accent2: #a78bfa;
    --text:    #e2e8f0;
    --muted:   #4a5568;
    --green:   #34d399;
    --red:     #f87171;
  }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: 'Inter', system-ui, sans-serif;
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 20px;
    background-image:
      radial-gradient(ellipse at 20% 50%, rgba(124,92,252,0.12) 0%, transparent 60%),
      radial-gradient(ellipse at 80% 20%, rgba(167,139,250,0.08) 0%, transparent 50%);
  }

  .card {
    background: var(--glass);
    border: 1px solid var(--border);
    border-radius: 20px;
    padding: 40px 36px;
    max-width: 560px;
    width: 100%;
    backdrop-filter: blur(20px);
    box-shadow: 0 25px 50px rgba(0,0,0,0.5);
  }

  .brand {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 28px;
  }

  .logo {
    width: 44px; height: 44px;
    background: linear-gradient(135deg, var(--accent), var(--accent2));
    border-radius: 12px;
    display: flex; align-items: center; justify-content: center;
    font-size: 20px;
    box-shadow: 0 4px 15px rgba(124,92,252,0.4);
  }

  .brand-text h1 { font-size: 20px; font-weight: 700; letter-spacing: -0.3px; }
  .brand-text span { font-size: 11px; color: var(--muted); }

  .dev-tag {
    display: inline-block;
    background: rgba(124,92,252,0.15);
    border: 1px solid rgba(124,92,252,0.3);
    color: var(--accent2);
    font-size: 10px;
    font-weight: 600;
    letter-spacing: .08em;
    padding: 3px 10px;
    border-radius: 20px;
    margin-bottom: 22px;
    text-transform: uppercase;
  }

  .input-wrap {
    position: relative;
    margin-bottom: 12px;
  }

  .input-wrap svg {
    position: absolute;
    left: 14px; top: 50%;
    transform: translateY(-50%);
    color: var(--muted);
    pointer-events: none;
  }

  input[type=text] {
    width: 100%;
    padding: 13px 14px 13px 42px;
    background: rgba(255,255,255,0.05);
    border: 1px solid var(--border);
    border-radius: 10px;
    color: var(--text);
    font-size: 13px;
    outline: none;
    transition: border-color .2s, box-shadow .2s;
    font-family: 'Inter', monospace;
  }

  input[type=text]::placeholder { color: var(--muted); }
  input[type=text]:focus {
    border-color: var(--accent);
    box-shadow: 0 0 0 3px rgba(124,92,252,0.15);
  }

  button {
    width: 100%;
    padding: 13px;
    background: linear-gradient(135deg, var(--accent), #6d4fe8);
    border: none;
    border-radius: 10px;
    color: #fff;
    font-size: 14px;
    font-weight: 700;
    cursor: pointer;
    transition: transform .15s, box-shadow .15s;
    letter-spacing: .02em;
    box-shadow: 0 4px 15px rgba(124,92,252,0.35);
  }

  button:hover {
    transform: translateY(-1px);
    box-shadow: 0 6px 20px rgba(124,92,252,0.5);
  }
  button:active { transform: translateY(0); }

  .hint {
    color: var(--muted);
    font-size: 11px;
    margin-top: 10px;
    text-align: center;
  }

  .divider {
    border: none;
    border-top: 1px solid var(--border);
    margin: 28px 0;
  }

  .api-title {
    color: var(--muted);
    font-size: 10px;
    font-weight: 600;
    letter-spacing: .1em;
    text-transform: uppercase;
    margin-bottom: 12px;
  }

  .endpoints { display: flex; flex-direction: column; gap: 6px; }

  .ep {
    display: flex;
    align-items: center;
    gap: 10px;
    background: rgba(255,255,255,0.02);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 9px 12px;
    font-size: 12px;
    color: var(--muted);
  }

  .method {
    font-size: 10px;
    font-weight: 700;
    padding: 2px 7px;
    border-radius: 5px;
    background: rgba(52,211,153,0.15);
    color: var(--green);
    letter-spacing: .05em;
    flex-shrink: 0;
  }

  .footer {
    margin-top: 24px;
    text-align: center;
    font-size: 10px;
    color: var(--muted);
  }
  .footer span { color: var(--accent2); }
</style>
</head>
<body>
<div class="card">
  <div class="brand">
    <div class="logo">🎬</div>
    <div class="brand-text">
      <h1>FapPlayer</h1>
      <span>Premium stream extractor</span>
    </div>
  </div>

  <div class="dev-tag">⚡ by Ariyan Sefat</div>

  <form method="GET" action="/play">
    <div class="input-wrap">
      <svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"
           viewBox="0 0 24 24">
        <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/>
        <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>
      </svg>
      <input name="url" type="text"
             placeholder="https://faphouse2.com/videos/..." required>
    </div>
    <button type="submit">▶ &nbsp; Watch in 1080p</button>
  </form>
  <p class="hint">Set EMAIL + PASSWORD env vars for full premium access</p>

  <hr class="divider">

  <p class="api-title">API Endpoints</p>
  <div class="endpoints">
    <div class="ep"><span class="method">GET</span>/play?url=VIDEO_URL</div>
    <div class="ep"><span class="method">GET</span>/api/m3u8?url=VIDEO_URL</div>
    <div class="ep"><span class="method">GET</span>/api/status</div>
  </div>

  <p class="footer">Built with 💜 by <span>Ariyan Sefat</span></p>
</div>
</body>
</html>"""

_PLAYER = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FapPlayer — Ariyan Sefat</title>
<link href="https://vjs.zencdn.net/8.6.1/video-js.css" rel="stylesheet">
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&display=swap');
  *, *::before, *::after { margin:0; padding:0; box-sizing:border-box; }

  :root {
    --bg:     #080b10;
    --glass:  rgba(255,255,255,0.04);
    --border: rgba(255,255,255,0.08);
    --accent: #7c5cfc;
    --text:   #e2e8f0;
    --muted:  #4a5568;
    --green:  #34d399;
  }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: 'Inter', system-ui, sans-serif;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 16px;
    background-image:
      radial-gradient(ellipse at 50% 0%, rgba(124,92,252,0.1) 0%, transparent 60%);
  }

  .topbar {
    width: 100%; max-width: 1100px;
    display: flex; align-items: center; justify-content: space-between;
    padding: 12px 0 16px;
  }

  .brand-sm {
    display: flex; align-items: center; gap: 8px;
    font-weight: 700; font-size: 15px;
  }

  .brand-sm .dot {
    width: 8px; height: 8px;
    border-radius: 50%;
    background: var(--green);
    box-shadow: 0 0 6px var(--green);
    animation: pulse 2s infinite;
  }

  @keyframes pulse {
    0%,100% { opacity:1; }
    50% { opacity:.4; }
  }

  a.back-btn {
    display: flex; align-items: center; gap: 6px;
    color: var(--muted);
    text-decoration: none;
    font-size: 12px;
    padding: 6px 12px;
    background: var(--glass);
    border: 1px solid var(--border);
    border-radius: 8px;
    transition: color .2s, border-color .2s;
  }
  a.back-btn:hover { color: var(--text); border-color: var(--accent); }

  .player-wrap {
    width: 100%; max-width: 1100px;
    aspect-ratio: 16/9;
    background: #000;
    border-radius: 16px;
    overflow: hidden;
    border: 1px solid var(--border);
    box-shadow: 0 20px 60px rgba(0,0,0,0.7);
  }

  #player { width:100%!important; height:100%!important; }

  /* quality badge */
  .quality-badge {
    display: inline-flex; align-items: center; gap: 5px;
    background: rgba(124,92,252,0.15);
    border: 1px solid rgba(124,92,252,0.3);
    color: #a78bfa;
    font-size: 10px; font-weight: 700;
    padding: 3px 10px; border-radius: 20px;
    letter-spacing: .05em; text-transform: uppercase;
    margin-top: 12px;
  }

  .meta-bar {
    width: 100%; max-width: 1100px;
    background: var(--glass);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 10px 14px;
    margin-top: 10px;
    display: flex; align-items: center; gap: 10px;
  }

  .meta-bar svg { flex-shrink:0; color: var(--muted); }
  .meta-url {
    font-size: 11px; color: var(--muted);
    word-break: break-all; flex:1;
  }
  .meta-url a { color: var(--green); text-decoration: none; }
  .meta-url a:hover { text-decoration: underline; }

  .footer {
    margin-top: 16px;
    font-size: 10px;
    color: var(--muted);
  }
  .footer span { color: #a78bfa; }

  /* video.js custom */
  .video-js .vjs-big-play-button {
    background: linear-gradient(135deg, rgba(124,92,252,0.8), rgba(109,79,232,0.8));
    border: 2px solid rgba(255,255,255,0.2);
    border-radius: 50%;
    width: 60px; height: 60px;
    line-height: 56px;
    font-size: 22px;
    left: 50%; top: 50%;
    transform: translate(-50%, -50%);
  }
  .video-js:hover .vjs-big-play-button {
    background: linear-gradient(135deg, rgba(124,92,252,1), rgba(109,79,232,1));
  }
  .vjs-default-skin .vjs-progress-holder { height: 4px; }
  .video-js .vjs-play-progress { background: var(--accent); }
  .video-js .vjs-volume-panel { display: flex; }
</style>
</head>
<body>

<div class="topbar">
  <div class="brand-sm">
    <div class="dot"></div>
    FapPlayer
  </div>
  <a class="back-btn" href="/">
    ← Home
  </a>
</div>

<div class="player-wrap">
  <video id="player" class="video-js vjs-default-skin vjs-big-play-centered"
         controls autoplay preload="auto">
    <source src="{{ m3u8_url }}" type="application/x-mpegURL">
  </video>
</div>

<div style="width:100%;max-width:1100px">
  <span class="quality-badge">⚡ 1080p • HLS Stream</span>
</div>

<div class="meta-bar">
  <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"
       viewBox="0 0 24 24">
    <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/>
    <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>
  </svg>
  <div class="meta-url">
    <a href="{{ m3u8_url }}" target="_blank">{{ m3u8_url[:120] }}</a>
  </div>
</div>

<p class="footer">Built with 💜 by <span>Ariyan Sefat</span></p>

<script src="https://vjs.zencdn.net/8.6.1/video.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/videojs-contrib-hls/5.15.0/videojs-contrib-hls.min.js"></script>
<script>
var player = videojs('player', {
  techOrder: ['html5'],
  html5: {
    hls: {
      overrideNative:           true,
      enableLowInitialPlaylist: false,  // full quality সরাসরি
      smoothQualityChange:      true,
      allowSeeksWithinUnsafeLiveWindow: true,
    }
  },
  playbackRates: [0.5, 0.75, 1, 1.25, 1.5, 2],
  responsive: true,
  fluid: true,
});

player.ready(function() {
  // 1080p / highest quality force
  this.on('loadedmetadata', function() {
    var q = this.qualityLevels ? this.qualityLevels() : null;
    if (q && q.length) {
      var best = -1, bestH = 0;
      for (var i = 0; i < q.length; i++) {
        if (q[i].height > bestH) { bestH = q[i].height; best = i; }
      }
      for (var j = 0; j < q.length; j++) {
        q[j].enabled = (j === best);
      }
    }
  });
  this.play().catch(function(){});
});
</script>
</body>
</html>"""

_ERR = """<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;700&display=swap');
  *{margin:0;padding:0;box-sizing:border-box}
  body{
    background:#080b10;color:#e2e8f0;
    display:flex;align-items:center;justify-content:center;
    min-height:100vh;font-family:'Inter',system-ui;
    background-image:radial-gradient(ellipse at 50% 30%, rgba(248,113,113,0.08) 0%, transparent 60%);
  }
  .box{text-align:center;max-width:480px;padding:20px}
  .icon{font-size:48px;margin-bottom:16px}
  h2{color:#f87171;margin-bottom:10px;font-size:20px}
  p{color:#4a5568;margin-bottom:24px;font-size:13px;line-height:1.6}
  a{
    color:#e2e8f0;padding:10px 24px;
    background:rgba(255,255,255,0.05);
    border:1px solid rgba(255,255,255,0.08);
    border-radius:8px;text-decoration:none;
    display:inline-block;font-size:13px;
    transition:border-color .2s;
  }
  a:hover{border-color:#7c5cfc}
  .sub{font-size:10px;color:#2d3748;margin-top:16px}
</style>
</head>
<body>
<div class="box">
  <div class="icon">🚫</div>
  <h2>{{ t }}</h2>
  <p>{{ m }}</p>
  <a href="/">← Back to Home</a>
  <p class="sub">FapPlayer by Ariyan Sefat</p>
</div>
</body>
</html>"""

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
            "Full video M3U8 not found. Check EMAIL/PASSWORD env vars — "
            "premium account required for full video access.", 404
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
    return jsonify({"success": False, "error": "Full video M3U8 not found"}), 404

@app.route("/api/status")
def api_status():
    return jsonify({
        "status":     "online",
        "logged_in":  client._sess is not None,
        "has_token":  client._token is not None,
        "cache_size": len(client._cache),
        "email":      EMAIL[:5] + "…",
        "developer":  "Ariyan Sefat",
    })

if __name__ == "__main__":
    print(f"FapPlayer by Ariyan Sefat — :5000  |  email: {EMAIL[:5]}…")
    app.run(host="0.0.0.0", port=5000, debug=False)
