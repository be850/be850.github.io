#!/usr/bin/env python3
"""Book850 PoC builder: chapters/*.md -> static site with full SEO/AIO stack.
Includes the SPIRAL VALIDATOR: chapter N may only use words 1..N + additions.
Build FAILS on any constraint violation (грабли-prevention from day one)."""
import json, re, html, os, sys, datetime, shutil
from email.utils import format_datetime

BASE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.join(BASE, 'site', 'public')
# config.json: {domain, indexnow_key} — shared with publish_next.py
_cfg = os.path.join(BASE, 'config.json')
CFG = json.load(open(_cfg, encoding='utf-8')) if os.path.exists(_cfg) else {}
DOMAIN = CFG.get('domain', 'https://book850.example.org').rstrip('/')
IN_KEY = CFG.get('indexnow_key', '')
# AdSense (2026-11-01): one switch in config.json -> "adsense": {"client": "ca-pub-…",
# "slot_a": "…", "slot_b": "…"}. Empty client = the site ships with NO ad code at all
# (clean while the AdSense review has not passed). The moment the id arrives we fill
# config.json, rebuild, and three things appear: (1) adsbygoogle script +
# google-adsense-account meta in <head>, (2) responsive ad units at the reserved
# slots A (after the story) and B (before Continue), (3) ads.txt in the site root.
# No other file ever needs editing — pre-mortem: no hardcoded ids in templates.
ADS = CFG.get('adsense') or {}
ADS_CLIENT = (ADS.get('client') or '').strip()
ADS_SLOT_A = (ADS.get('slot_a') or '').strip()
ADS_SLOT_B = (ADS.get('slot_b') or '').strip()
# BL#52 (08.11): the adsbygoogle/gtag <script async src> tags are GONE from
# <head> — both srcs are injected on idle by _THIRD_PARTY_LOADER instead.
# The queue patterns (window.adsbygoogle.push, dataLayer+gtag config) already
# work pre-load, so units/analytics still fire — just after the page is
# interactive. PageSpeed: removes ~hundreds of KB of third-party JS from TBT.
ADS_HEAD = (f'<meta name="google-adsense-account" content="{ADS_CLIENT}">\n') if ADS_CLIENT else ''
# Google tag gtag.js / GA4 (2026-11-02, replaced GTM-58RV2C6 at owner request):
# config.json -> "ga4_id": "G-…". Empty = no tag code ships at all (same philosophy
# as adsense). Canonical gtag.js snippet in <head>; no <noscript> part (that is
# GTM-only, GA4 needs none).
GA_ID = (CFG.get('ga4_id') or '').strip()
_GA_SNIPPET = ('<!-- Google tag (gtag.js) — src loaded on idle, see _THIRD_PARTY_LOADER -->\n'
               '<script>\n'
               '  window.dataLayer = window.dataLayer || [];\n'
               '  function gtag(){dataLayer.push(arguments);}\n'
               "  gtag('js', new Date());\n"
               '\n'
               "  gtag('config', '__GA_ID__');\n"
               '</script>')
GA_HEAD = (_GA_SNIPPET.replace('__GA_ID__', GA_ID)) if GA_ID else ''
# Consent Mode v2 (2026-11-12, kraser 08:43Z «cookie-минимум как policies.google.com»;
# BACKLOG #72 = зеркало eli5 62dba67): всё denied по умолчанию — никаких analytics/
# ad-куков до явного согласия. Блок обязателен ПЕРЕД GA_HEAD/config в <head>.
CONSENT_HEAD = ('<!-- Consent Mode v2: denied by default (cookie minimum) -->\n'
                '<script>\n'
                '  window.dataLayer = window.dataLayer || [];\n'
                '  function gtag(){dataLayer.push(arguments);}\n'
                "  gtag('consent', 'default', {analytics_storage: 'denied', ad_storage: 'denied',"
                " ad_user_data: 'denied', ad_personalization: 'denied', wait_for_update: 500});\n"
                '</script>\n')
# Минимальная cookie-пилюля (fixed bottom-left, одна строка, 2 кнопки,
# localStorage 'ck' — тот же паттерн, что eli5_build_pages.py).
_CK_CSS = ('#ck{position:fixed;left:10px;bottom:10px;z-index:9;background:var(--card);'
           'border:1px solid var(--border);border-radius:999px;padding:.4rem .8rem;font-size:13px;'
           'line-height:1.45;max-width:min(92vw,470px);box-shadow:0 2px 10px rgba(0,0,0,.12)}'
           '#ck button{border:0;border-radius:999px;padding:.25rem .65rem;font:600 12px/1.3 inherit;'
           'background:var(--accent);color:#fff;cursor:pointer;margin-left:.3rem}'
           '#ck #ck-no{background:transparent;color:var(--accent);border:1px solid var(--border)}')
_CK_HTML = ('<div id="ck" hidden role="region" aria-label="Cookie notice">Anonymous traffic analytics. '
            '<button id="ck-ok" type="button">OK</button>'
            '<button id="ck-no" type="button">Essential only</button></div>\n'
            '<script>(function(){var b=document.getElementById("ck");'
            'function save(v){try{localStorage.setItem("ck",v)}catch(e){}b.hidden=true}'
            'var s=null;try{s=localStorage.getItem("ck")}catch(e){}'
            'if(s==="ok"&&window.gtag){gtag("consent","update",{analytics_storage:"granted"})}'
            'if(b){b.hidden=!!s;'
            'document.getElementById("ck-ok").onclick=function(){save("ok");'
            'if(window.gtag){gtag("consent","update",{analytics_storage:"granted"})}};'
            'document.getElementById("ck-no").onclick=function(){save("no")}}})()</script>\n')
# One loader for BOTH third-party srcs (BL#52): nothing network/main-thread
# heavy before idle. requestIdleCallback with a 4s cap, setTimeout fallback.
_TP_URLS = ([f'https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client={ADS_CLIENT}']
            if ADS_CLIENT else []) + \
           ([f'https://www.googletagmanager.com/gtag/js?id={GA_ID}'] if GA_ID else [])
_THIRD_PARTY_LOADER = (
    '<!-- adsense+gtag off the critical path (BL#52): srcs injected on idle -->\n'
    '<script>(function(){var u=' + json.dumps(_TP_URLS) + ';'
    'function go(){for(var k=0;k<u.length;k++){var s=document.createElement("script");'
    's.async=true;s.src=u[k];document.head.appendChild(s);}}'
    'if("requestIdleCallback" in window){requestIdleCallback(go,{timeout:4000})}'
    'else{setTimeout(go,2500)}})()</script>') if _TP_URLS else ''
AUTHOR = 'KraSer'
TODAY = datetime.date.today().isoformat()          # real build/deploy date
WARNINGS = []

# ---------- canon ----------
canon = json.load(open(os.path.join(BASE, 'words.json'), encoding='utf-8'))
WORDS = [w['word'] for w in canon['order']]          # 0-based index; WORDS[0] = word #1
CATS = [w['category'] for w in canon['order']]

ADDITIONS = {'i','you','he','she','it','we','they','me','him','her','us','them',
             'a','the'} | {str(n) for n in range(0,101)} | {
             'one','two','three','four','five','six','seven','eight','nine','ten'}

# Irregular forms per base verb (only applied when base is already allowed).
# Extended 2026-10-30 for chapters 4..16 (operations).
IRREG = {'come':['comes','coming','came'],
         'get':['gets','getting','got','gotten'],
         'give':['gives','giving','gave','given'],
         'go':['goes','going','went','gone'],
         'keep':['keeps','keeping','kept'],
         'let':['lets','letting'],
         'make':['makes','making','made'],
         'put':['puts','putting'],
         'seem':['seems','seeming'],
         'take':['takes','taking','took','taken'],
         'be':['is','am','are','was','were','being','been'],
         'do':['does','doing','did','done'],
         'have':['has','having','had'],
         'say':['says','saying','said'],
         'see':['sees','seeing','saw','seen'],
         'send':['sends','sending','sent']}

# Pre-mortem rule: a chapter page must carry >= MIN_WORDS words
# (story + word-card + FAQ), otherwise it is thin content.
MIN_WORDS = 300

def bases_up_to(n):  # words #1..#n (1-based)
    return set(WORDS[:n])

def token_base(tok, allowed_bases):
    """Return base word if tok is a form of an allowed base, else None."""
    if tok in ADDITIONS: return tok
    if tok in allowed_bases: return tok
    # irregular forms first (went, made, was, is, kept, took...) — suffix
    # rules cannot derive these; forms map only if the base is already allowed
    for b, forms in IRREG.items():
        if b in allowed_bases and tok in forms: return b
    cands = []
    if tok.endswith('ies'): cands.append(tok[:-3]+'y')
    if tok.endswith('es'): cands.append(tok[:-2])
    if tok.endswith('s'): cands.append(tok[:-1])
    if tok.endswith('ing'):
        r = tok[:-3]; cands += [r, r+'e', r[:-1] if r[-1:-1] or (len(r)>1 and r[-1]==r[-2]) else r]
        if len(r) > 1 and r[-1] == r[-2]: cands.append(r[:-1])
    if tok.endswith('ed'):
        r = tok[:-2]; cands += [r, r+'e']
        if len(r) > 1 and r[-1] == r[-2]: cands.append(r[:-1])
    for c in cands:
        if c in allowed_bases: return c
    return None

def strip_tags(s):
    s = re.sub(r'<!--.*?-->', '', s, flags=re.DOTALL)
    s = re.sub(r'<[^>]+>', ' ', s)
    s = html.unescape(s)
    return s

def validate_chapter(num, word, story_text):
    """Chapter N: only words #1..#N + additions; must introduce word #N."""
    tokens = re.findall(r"[a-zA-Z]+", strip_tags(story_text).lower())
    allowed = bases_up_to(num)
    bad = []
    for t in tokens:
        if token_base(t, allowed) is None:
            bad.append(t)
    intro = token_base(word, allowed) is not None and word in allowed
    used_intro = any(token_base(t, allowed) == word for t in tokens)
    ok = not bad and used_intro
    return ok, bad, len(tokens), used_intro

def page_words(body, faq):
    """Word count of a chapter page: story + FAQ (incl. word-card definition)."""
    parts = [body]
    for q, a in (faq or []):
        parts += [q, a]
    return len(re.findall(r"[a-zA-Z]+", strip_tags(' '.join(parts))))

# In-text internal links (2026-10-31): the FIRST occurrence of every
# already-published word in the story becomes a link to its word page.
# SEO: keyword-rich internal anchors + crawl paths; readers: jump to the
# word they want to re-read. Entities (&mdash;) are never touched, the
# chapter's own word is never self-linked, and the cap keeps density
# natural (~1 link per 25 words of story).
MAX_STORY_LINKS = 12

def linkify_story(text, num):
    """Wrap first occurrences of published words (#1..#num-1) in <a> links."""
    targets = {m['word']: n for n, m in CHAPTERS.items()
               if m.get('type') == 'chapter' and n < num}
    used, out, last = set(), [], 0
    for mo in re.finditer(r"&(?:[a-zA-Z]+|#\d+);|[A-Za-z]+(?:'[A-Za-z]+)?", text):
        if mo.group().startswith('&'):        # HTML entity — copy untouched
            continue
        tok = mo.group()
        base = token_base(tok.lower(), bases_up_to(num - 1))
        if base and base in targets and base not in used and len(used) < MAX_STORY_LINKS:
            used.add(base)
            out.append(text[last:mo.start()] +
                       f'<a href="{chap_path(targets[base])}">{tok}</a>')
            last = mo.end()
    out.append(text[last:])
    return ''.join(out)

# ---------- chapters ----------
def parse_md(path):
    """Frontmatter v2: key: value lines; faq-q/faq-a pairs; '---' ends the header.
    Fallback: header ends at the first blank line (legacy chapter-00)."""
    meta, faq = {}, []
    lines = open(path, encoding='utf-8').read().splitlines()
    i = 0
    header_end = None
    for i, ln in enumerate(lines):
        if ln.strip() == '---':
            header_end = i; break
        if ln.strip() == '':
            header_end = i; break
        k, _, v = ln.partition(':'); k = k.strip()
        if k == 'faq-q': faq.append([v.strip(), ''])
        elif k == 'faq-a':
            if faq: faq[-1][1] = v.strip()
        else: meta[k] = v.strip()
    meta['body'] = '\n'.join(lines[header_end+1:])
    meta['faq'] = faq
    return meta

CHAPTERS = {}
for f in sorted(os.listdir(os.path.join(BASE, 'chapters'))):
    if f.endswith('.md'):
        m = parse_md(os.path.join(BASE, 'chapters', f))
        CHAPTERS[int(m['number'])] = m

# Pretty URLs (2026-11-01): a chapter's canonical path IS its word — /go/,
# /come/, /get/ … The word is unique forever (spiral: one word per chapter),
# carries the exact keyword in the URL and reads like the project feels.
# The manifesto + word directory lives at /words/. Chapter numbers survive in
# titles, breadcrumbs and JSON-LD names. Old /chapter/N/ URLs are gone by
# design — the site is 1 day old, nothing indexed yet, zero redirect debt.
# NB: 'about' and 'page' are REAL Ogden words -> they will become chapter
# slugs some day, so they must stay OUTSIDE the reserved list.
RESERVED_SLUGS = {'words', 'chapter', 'assets', 'static', 'feed', 'search',
                  'contact', 'privacy', 'terms', 'tag', 'author', 'admin',
                  'api', 'blog', 'post', 'login', 'signup'}

def chap_path(n):
    """Canonical site path of chapter n: /<word>/ for chapters, /words/ for 0."""
    m = CHAPTERS.get(n) or {}
    if n == 0 or m.get('type') != 'chapter':
        return '/words/' if n == 0 else f'/chapter/{n}/'
    w = (m.get('word') or '').strip().lower()
    if not re.fullmatch(r'[a-z][a-z0-9-]*', w):
        print(f'BUILD ABORT: chapter {n}: word {w!r} is not a safe URL slug'); sys.exit(1)
    if w in RESERVED_SLUGS:
        print(f'BUILD ABORT: chapter {n}: slug {w!r} collides with a reserved path'); sys.exit(1)
    return f'/{w}/'

# Slug uniqueness is structural (spiral) — but data bugs must abort loudly.
_slug_seen = {}
for _n, _m in sorted(CHAPTERS.items()):
    if _m.get('type') == 'chapter':
        _w = (_m.get('word') or '').strip().lower()
        if _w in _slug_seen:
            print(f'BUILD ABORT: word {_w!r} used by chapters {_slug_seen[_w]} and {_n} — slugs must be unique'); sys.exit(1)
        _slug_seen[_w] = _n
del _slug_seen

# ---------- validate all ----------
print('== SPIRAL VALIDATOR ==')
all_ok = True
for num in sorted(CHAPTERS):
    m = CHAPTERS[num]
    if m.get('type') != 'chapter':
        # chapter 0: prologue must be additions-only
        prologue = m['body'].split('<!--in-constraint-->')[-1]
        toks = re.findall(r"[a-zA-Z]+", strip_tags(prologue).lower())
        bad = [t for t in toks if token_base(t, set()) is None]
        ok = not bad
        print(f'  ch0 prologue: {"PASS" if ok else "FAIL"} {bad if bad else ""}')
        all_ok &= ok
        continue
    ok, bad, ntok, intro = validate_chapter(num, m['word'], m['body'])
    nwords = page_words(m['body'], m['faq'])
    wok = nwords >= MIN_WORDS
    print(f'  ch{num} "{m["word"]}": {"PASS" if ok else "FAIL"} | tokens={ntok} | page_words={nwords}{"" if wok else f" <{MIN_WORDS}!"} | introduces "{m["word"]}": {intro} | violations: {bad if bad else "none"}')
    all_ok &= ok and intro and wok
if not all_ok:
    print('VALIDATION FAILED — build aborted'); sys.exit(1)
print('== ALL PASS ==\n')

# ---------- HTML template ----------
# Theme system (2026-11-01): CSS custom properties + prefers-color-scheme +
# a manual toggle in the crumbs row. Stored in localStorage('b850-theme').
# TWO themes since 2026-11-06 (kraser 14:16Z: «может только 2 оставить, эту
# белую не надо» — согласен, белая дублировала светлую «Бумагу»): «Бумага»
# (sepia) is the DEFAULT — it replaces the old white light theme as the day
# palette, white is deleted; dark is the night option. Unset follows the OS
# (light-OS -> Бумага, dark-OS -> dark), an explicit data-theme wins over
# the OS. The dark palette lives ONCE in _DARK_VARS and is injected into
# BOTH the media-query branch and the explicit data-theme branch: the two
# can never drift apart.
# Dark palette 2.0 (2026-11-06, kraser ok «твой выбор ок»): «Тёплый вечер» —
# warm dark plum canvas, ivory text, amber accent + rose secondary, soft
# top glow instead of a flat void. Fixes the gloomy cold-monochrome feel
# (old: blue-gray everything, one accent, flat canvas). Same dual-branch
# pattern for the glow/card extras (_DARK_EXTRAS) — no drift, ever.
_DARK_VARS = ('--bg:#1a1622;--text:#ded7cd;--head:#f3ede3;--accent:#e8a54b;'
              '--muted:#a0948a;--card:#241e2e;--border:#3a3244;--todo:#8d8172;'
              '--acc2:#d97fb0')
_DARK_GLOW = ('background-image:radial-gradient(760px 260px at 22% -70px,'
              'rgba(232,165,75,.11),transparent 72%),' 
              'radial-gradient(560px 220px at 96% -50px,'
              'rgba(217,127,176,.08),transparent 72%)')
_SEPIA_VARS = ('--bg:#f4ecd8;--text:#4b3d28;--head:#3b2f1e;--accent:#945322;'
               '--muted:#6f6244;--card:#ede1c6;--border:#dccfae;--todo:#786a42')
# A11y tuning 08.11 (BL#52, PageSpeed contrast): ONLY the failing pairs moved,
# hue kept — accent #a05a2c->#945322 (4.05->4.60 on card), muted #8a7a5f->#6f6244
# (3.22->4.62 on card), todo #a2916f->#786a42 (2.62->4.53 on bg); dark todo
# #6b6157->#8d8172 (2.94->4.67). bg/card/text/head/acc2 untouched — the
# approved look stands, WCAG 4.5:1 now passes everywhere text renders.
# «Бумага» (2026-11-06: kraser «сделай её тоже», 14:02Z; в 14:16Z — «может
# только 2 оставить, эту белую не надо»): кремовая бумага как в читалках,
# коричневые чернила, тёплый сиена-акцент. Сначала — третья тема по кнопке,
# теперь единственная светлая и ДЕФОЛТ сайта: белая light удалена совсем.
# Палитру не меняли — та, что kraser одобрил на превью 3 тем.
_DARK_EXTRAS = (
 f'@media(prefers-color-scheme:dark){{:root:not([data-theme=sepia]) body{{{_DARK_GLOW}}}'
 f':root:not([data-theme=sepia]) .card{{border-left-color:var(--acc2)}}}}'
 f':root[data-theme=dark] body{{{_DARK_GLOW}}}'
 ':root[data-theme=dark] .card{border-left-color:var(--acc2)}')
CSS = (f':root{{{_SEPIA_VARS};color-scheme:light}}'
 f'@media(prefers-color-scheme:dark){{:root:not([data-theme=sepia]){{{_DARK_VARS};color-scheme:dark}}}}'
 f':root[data-theme=dark]{{{_DARK_VARS};color-scheme:dark}}'
 'body{margin:0;font:17px/1.7 Georgia,serif;color:var(--text);background:var(--bg)}'
 + _DARK_EXTRAS +
 'main{max-width:680px;margin:0 auto;padding:24px 20px 64px}'
 'a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}'
 'article a,.faq a,footer a{text-decoration:underline dotted;text-underline-offset:4px}'
 '.crumbs{display:flex;align-items:center;justify-content:space-between;gap:12px;'
 'font:14px/1.4 Arial,sans-serif;color:var(--muted);margin-bottom:20px}'
 'h1{font:700 30px/1.25 Arial,sans-serif;margin:0 0 6px;color:var(--head)}'
 'h2{font:700 21px/1.3 Arial,sans-serif;margin:34px 0 10px;color:var(--head)}'
 '.story{font-size:19px;line-height:2.0;white-space:pre-wrap}'
 '.story p{margin:0.9em 0;white-space:normal}'
 '.card{background:var(--card);border-left:4px solid var(--accent);padding:14px 18px;border-radius:0 8px 8px 0;margin:20px 0}'
 '.card strong{font-family:Arial,sans-serif}'
 '.progress{font:700 13px/1.4 Arial,sans-serif;letter-spacing:1px;color:var(--accent);text-transform:uppercase}'
 'table{border-collapse:collapse;width:100%;margin:14px 0;font-size:16px}'
 'th,td{border:1px solid var(--border);padding:8px 10px;text-align:left}'
 'th{background:var(--card);font-family:Arial,sans-serif}'
 '.faq-item{border-top:1px solid var(--border);padding:12px 0}'
 '.faq-item h3{font:700 17px/1.4 Arial,sans-serif;margin:0 0 6px;color:var(--head)}.faq-item p{margin:0}'
 'footer{margin-top:48px;border-top:1px solid var(--border);padding-top:18px;font:14px/1.6 Arial,sans-serif;color:var(--muted)}'
 '.dir{font:15px/2 Arial,sans-serif;columns:3;column-gap:24px}'
 '.dir a{color:var(--text)}.dir .todo{color:var(--todo)}'
 '.dir a.done{font-weight:700;color:var(--accent)}'
 'article ul{margin:14px 0;padding-left:24px}article li{margin:5px 0}'
 '.short-toggle{margin:14px 0 0}'
 '.short-toggle summary{font:600 14px/1.4 Arial,sans-serif;color:var(--accent);cursor:pointer}'
 '.video-short{aspect-ratio:16/9;width:100%;margin:14px auto 0}'
 '.video-short.vertical{width:min(100%,270px);aspect-ratio:9/16}'
 '.video iframe,.video-short iframe{display:block;width:100%;height:100%;border:0;border-radius:10px;background:#000}'
 '.yt-facade{position:relative;width:100%;height:100%;background:#000 center/cover no-repeat;border-radius:10px;cursor:pointer}'
 '.yt-facade:focus-visible{outline:3px solid var(--accent);outline-offset:2px}'
 '.yt-play{position:absolute;inset:0;margin:auto;width:66px;height:46px;border-radius:12px;background:rgba(23,18,30,.82);display:flex;align-items:center;justify-content:center}'
 '.yt-play:before{content:"";border-style:solid;border-width:9px 0 9px 16px;border-color:transparent transparent transparent #fff;margin-left:3px}'
 '#video-course{margin:20px 0 28px}'
 '#video-course .pl{font:15px/1.9 Arial,sans-serif}'
 '#video-course .pl a{color:var(--accent);font-weight:700}'
 '#video-course .pl-meta{color:var(--muted)}'
 '#theme-btn{flex:none;display:inline-flex;align-items:center;justify-content:center;'
 'width:30px;height:30px;padding:0;border:1px solid var(--border);border-radius:50%;'
 'background:var(--card);color:var(--muted);cursor:pointer}'
 '#theme-btn:hover{color:var(--accent);border-color:var(--accent)}'
 '#theme-btn svg{width:15px;height:15px;stroke:currentColor;fill:none;'
 'stroke-width:2;stroke-linecap:round;stroke-linejoin:round}'
 '#theme-btn .book{display:none}'
 '[data-theme=dark] #theme-btn .moon{display:none}'
 '[data-theme=dark] #theme-btn .book{display:inline}'
 '@media(prefers-color-scheme:dark){:root:not([data-theme=sepia]) #theme-btn .moon{display:none}'
 ':root:not([data-theme=sepia]) #theme-btn .book{display:inline}}')
# Icon = the theme a click will SWITCH TO: «Бумага» (default) -> moon (dark),
# dark -> book (Бумага). OS-dark unset counts as dark (book shows).
# Moon/book button: feather icons, stroke=currentColor (adapts to both themes).
# Which icon shows = the theme a click will SWITCH TO. Covers all states
# (unset / data-theme=sepia / data-theme=dark / OS-following).
_THEME_BTN = ('<button id="theme-btn" type="button" aria-label="Switch color theme" title="Бумага / dark theme">'
 '<svg class="moon" viewBox="0 0 24 24" aria-hidden="true">'
 '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>'
 '<svg class="book" viewBox="0 0 24 24" aria-hidden="true">'
 '<path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/>'
 '<path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/></svg></button>')
# Runs before first paint: a saved preference must win over the OS setting,
# otherwise dark-mode readers get a white flash on every navigation.
_THEME_HEAD_JS = ('<script>try{var t=localStorage.getItem("b850-theme");'
 'if(t==="dark"||t==="sepia")document.documentElement.setAttribute("data-theme",t);'
 'var mc={dark:"#1a1622",sepia:"#f4ecd8"}[t];'
 'if(mc){var m=document.querySelector("meta[name=theme-color]");if(m)m.setAttribute("content",mc)}}catch(e){}</script>')
# YouTube facade loader (BL#52): one delegated click/keydown handler for ALL
# .yt-facade divs — swaps the poster for a real nocookie iframe on demand.
_YT_JS = ('<script>(function(){'
 'function arm(f){var v=f.getAttribute("data-vid"),t=f.getAttribute("data-title")||"";'
 'var i=document.createElement("iframe");'
 'i.src="https://www.youtube-nocookie.com/embed/"+v+"?autoplay=1";i.title=t;'
 'i.allow="autoplay; encrypted-media; picture-in-picture";i.allowFullscreen=true;'
 'i.style.cssText="width:100%;height:100%;border:0;border-radius:10px";'
 'f.replaceWith(i);}'
 'document.addEventListener("click",function(e){'
 'var f=e.target&&e.target.closest?e.target.closest(".yt-facade"):null;if(f)arm(f);});'
 'document.addEventListener("keydown",function(e){'
 'if(e.key!=="Enter"&&e.key!==" ")return;'
 'var f=e.target&&e.target.closest?e.target.closest(".yt-facade"):null;'
 'if(f){e.preventDefault();arm(f);}});})();</script>')
_THEME_JS = ('<script>(function(){var b=document.getElementById("theme-btn");if(!b)return;'
 'var ord=["sepia","dark"],mc={dark:"#1a1622",sepia:"#f4ecd8"};'
 'function eff(){var t=document.documentElement.getAttribute("data-theme");'
 'return t?t:(window.matchMedia&&matchMedia("(prefers-color-scheme: dark)").matches?"dark":"sepia")}'
 'function set(t){document.documentElement.setAttribute("data-theme",t);'
 'var m=document.querySelector("meta[name=theme-color]");if(m)m.setAttribute("content",mc[t]);'
 'try{localStorage.setItem("b850-theme",t)}catch(e){}}'
 'b.addEventListener("click",function(){set(ord[(ord.indexOf(eff())+1)%ord.length])})})();</script>')

def md_html(text):
    """Tiny Markdown subset for the manifesto (2026-11-01). Until today the
    front-door /words page printed raw '##' and '-' source — a real defect
    everyone learned to ignore. Covered: '## ' headings, '- ' lists (with
    two-space continuation lines), blank-line paragraphs. Entities and inline
    tags (<em>, <a>) pass through untouched. Render-time only: m0['body']
    stays raw for the spiral validator."""
    out, para, lst = [], [], []
    def flush_p():
        if para: out.append('<p>' + ' '.join(para) + '</p>'); para.clear()
    def flush_l():
        if lst: out.append('<ul>' + ''.join(f'<li>{x}</li>' for x in lst) + '</ul>'); lst.clear()
    for ln in text.splitlines():
        s = ln.strip()
        if not s:
            flush_p(); flush_l()
        elif s.startswith('## '):
            flush_p(); flush_l(); out.append('<h2>' + s[3:] + '</h2>')
        elif s.startswith('- '):
            flush_p(); lst.append(s[2:])
        elif ln[:2] == '  ' and lst:            # wrapped continuation of a list item
            lst[-1] += ' ' + s
        else:
            flush_l(); para.append(s)
    flush_p(); flush_l()
    return '\n'.join(out)

def dir_html():
    # Only PUBLISHED chapters get links; future words are plain text
    # (no href -> no 404 farm for the crawler).
    published = {n for n, m in CHAPTERS.items() if m.get('type') == 'chapter'}
    out, seen = [], set()
    for i, w in enumerate(WORDS, 1):
        if w in seen: continue
        seen.add(w)
        if i in published:
            out.append(f'<a class="done" href="{chap_path(i)}">{i}. {w}</a>')
        else:
            out.append(f'<span class="todo">{i}. {w}</span>')
    return '<div class="dir">' + '<br>'.join(out) + '</div>'

# The channel's playlists = the book's five volumes 1:1 (both follow Ogden's
# categories). Linking them from the hub (2026-11-01, per kraser's 'прикрутить
# плейлисты для рекламы') is the site-side half of the promo loop: a reader can
# switch to audio/video for ANY volume, and every playlist points back at the
# matching chapters via video descriptions (see HQ promo pack). No video
# counts here on purpose — they change as kraser uploads; spans are evergreen.
PLAYLISTS = [
    ('Operations', 'words 1–100', 'PLAF_m1QwGc__c5gEDn5nWpCdJN9BPuOig'),
    ('Things — General', 'words 101–500', 'PLAF_m1QwGc__dXClYMxv4j9jwrzpx4t1P'),
    ('Things — Picturable', 'words 501–700', 'PLAF_m1QwGc_-hMMzGwCywzy9Ch-rQc2Tx'),
    ('Qualities — General', 'words 701–800', 'PLAF_m1QwGc_-4wqj9AT0qm0UaLwwBDje5'),
    ('Qualities — Opposites', 'words 801–850', 'PLAF_m1QwGc_890m8GDcExtNJTd0onsxJt'),
]

def playlist_html():
    rows = [f'<a href="https://www.youtube.com/playlist?list={pid}" target="_blank" rel="noopener">{name}</a>'
            f'<span class="pl-meta"> — {span}</span>' for name, span, pid in PLAYLISTS]
    return ('<div class="card" id="video-course"><strong>The video course</strong> — every word of the book has '
            'a full lesson and a 60-second short on YouTube:<br><span class="pl">'
            + '<br>'.join(rows) + '</span></div>')

def page(title, desc, canonical, crumbs, jsonld, content, extra=''):
    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{CONSENT_HEAD}{GA_HEAD}
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(desc)}">
<link rel="canonical" href="{canonical}">
<meta property="og:type" content="article">
<meta property="og:title" content="{html.escape(title)}">
<meta property="og:description" content="{html.escape(desc)}">
<meta property="og:url" content="{canonical}">
<meta property="og:site_name" content="Book850">
<meta property="og:locale" content="en_US">
<meta property="og:image" content="{DOMAIN}/og.png">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:image" content="{DOMAIN}/og.png">
<link rel="alternate" type="application/rss+xml" title="Book850" href="{DOMAIN}/rss.xml">
<meta name="color-scheme" content="light dark">
<meta name="theme-color" content="#f4ecd8" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#1a1622" media="(prefers-color-scheme: dark)">
{_THEME_HEAD_JS}
<link rel="preconnect" href="https://i.ytimg.com">
{ADS_HEAD}
{_THIRD_PARTY_LOADER}
<style>{CSS}{_CK_CSS}</style>
{jsonld}
</head>
<body>
<main>
<nav class="crumbs"><span>{crumbs}</span>{_THEME_BTN}</nav>
{content}
<footer>
<p>Book850 &mdash; a novel that teaches itself English, one word per chapter, 850 words total.</p>
<p><a href="/words/">Start here</a> &middot; <a href="{chap_path(1) if 1 in CHAPTERS else '/words/'}">Chapter 1</a> &middot; The Basic English 850-word system</p>
</footer>
</main>
{_CK_HTML}
{extra}
{_THEME_JS}
{_YT_JS}
</body>
</html>'''

def ld(obj): return '<script type="application/ld+json">\n' + json.dumps(obj, ensure_ascii=False) + '\n</script>'

def ad_unit(slot):
    """Responsive AdSense unit for a reserved slot; '' while unconfigured."""
    if not (ADS_CLIENT and slot):
        return ''
    return (f'<ins class="adsbygoogle" style="display:block" data-ad-client="{ADS_CLIENT}" '
            f'data-ad-slot="{slot}" data-ad-format="auto" data-full-width-responsive="true"></ins>\n'
            f'<script>(adsbygoogle = window.adsbygoogle || []).push({{}});</script>')

def iframe_html(vid, title):
    """Click-to-load YouTube facade (BL#52, 08.11; also the eli5 fix pattern,
    BL#51): ships a poster div + CSS play button — ZERO YouTube JS/CSS/iframe
    bytes until the reader asks for playback (the old loading=lazy iframe
    still pulled ~594KB of embed JS and hit TBT on every chapter page).
    hqdefault is 4:3-letterboxed; center/cover in the 16:9 wrapper crops the
    bars. Keyboard: role=button + Enter/Space handler in _YT_JS. The
    youtube-nocookie embed domain also removes the third-party-cookie noise
    flagged by PageSpeed Best Practices."""
    t = html.escape(title, quote=True)
    return (f'<div class="yt-facade" data-vid="{vid}" data-title="{t}" role="button" tabindex="0" '
            f'aria-label="Play video: {t}" '
            f'style="background-image:url(https://i.ytimg.com/vi/{vid}/hqdefault.jpg)">'
            f'<span class="yt-play" aria-hidden="true"></span></div>')

def real_iframe(vid, title):
    """Crawlable primary player (GSC fix 11.11, dm-ход kraser 'нужен фикс
    везде'): the PRIMARY video of a page must be real HTML (<iframe>) in the
    served DOM — a click-facade keeps the player out of Googlebot's reach and
    the page fails the 'watch page' test (54 eli5 pages, Nov crawl). Real
    lazy nocookie iframe, no autoplay; the click-to-load facade stays ONLY
    for secondary embeds (quick lesson inside <details>)."""
    t = html.escape(title, quote=True)
    return (f'<iframe src="https://www.youtube-nocookie.com/embed/{vid}" title="{t}" loading="lazy" '
            f'allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; '
            f'picture-in-picture; web-share" allowfullscreen></iframe>')

def video_block_for(w, m):
    """Dual video (2026-11-01, per kraser's 'дуально и шортс и полное'):
    full lesson 16:9 as the primary embed + the quick lesson in a collapsed
    <details> — user picks 'quick or detailed' WITHOUT a second article (one
    chapter = one word = one URL stays intact; two articles would fork the
    spiral and halve every page's ranking signals). Quick video ALONE -> it
    becomes the primary embed. Reality check (2026-11-01, FULL channel scrape,
    1700+ videos paginated): each word has BOTH formats — a ~1-min quick
    lesson that IS a true vertical Short (/shorts/ redirect verified) and a
    4-6 min full lesson (fulls uploaded Jan 2026, shorts Aug 2026). The
    toggle embed therefore defaults to the 9:16 vertical player; a
    horizontal short may set 'short_shape: horizontal' for 16:9."""
    vid = (m.get('video') or '').strip()
    sid = (m.get('short') or '').strip()
    vcls = 'video-short' if (m.get('short_shape') or '').strip() == 'horizontal' else 'video-short vertical'
    out = ''
    if vid:
        out += ('<div class="video" style="margin:24px 0 8px;aspect-ratio:16/9;width:100%">'
                + real_iframe(vid, f'{w} — Basic English lesson') + '</div>'
                f'<p class="note" style="font:14px/1.5 Arial,sans-serif;color:var(--muted)">'
                f'Watch the video lesson for the word <strong>{w}</strong> from the Basic English channel.</p>')
    st = (m.get('short_title') or '').strip() or f'{w} in 60 seconds'
    if sid and vid:
        out += (f'<details class="short-toggle"><summary>In a hurry? Watch the quick lesson — {html.escape(st)}</summary>'
                f'<div class="{vcls}">' + iframe_html(sid, f'{w} — quick lesson') + '</div></details>')
    elif sid:
        out += (f'<div class="{vcls}">' + real_iframe(sid, f'{w} — quick lesson') + '</div>'
                f'<p class="note" style="font:14px/1.5 Arial,sans-serif;color:var(--muted)">'
                f'The quick version: <strong>{w}</strong> in about a minute.</p>')
    # No video data -> no slot at all (rule: "данных нет — слоты пустые,
    # невидимый слот, БЕЗ фейков"). A literal placeholder comment here leaks
    # "VIDEO SLOT" into the page source and fails watchdog audit check #10
    # (first hit: ch12 "do", 2026-11-03 02:20 tick).
    return out

# Clean build (2026-11-01): wipe the output dir first. Pre-mortem: a stale
# artifact from an older config (e.g. ads.txt after the AdSense id was removed,
# or a chapter page that no longer exists) must never leak into a deploy.
# site/ is pure build output and gitignored — safe to drop.
if os.path.isdir(SITE):
    shutil.rmtree(SITE)
os.makedirs(SITE, exist_ok=True)

# ---------- chapter 0 (manifesto + directory) ----------
m0 = CHAPTERS[0]
ch0_content = f'''
<article>
<div class="progress">Book850 &middot; Chapter 0 &middot; Word 0 of 850</div>
<h1>{m0['title']}</h1>
{md_html(linkify_story(m0['body'], 999))}
{playlist_html()}
<div id="directory">{dir_html()}</div>
</article>'''
ch0_ld = ld({"@context":"https://schema.org","@type":"Book","name":"Book850",
 "description":"A novel that teaches itself English: chapter N introduces word N of Ogden's Basic English 850 and uses only words 1..N.",
 "inLanguage":"en","numberOfPages":850,"author":{"@type":"Person","name":AUTHOR},
 "publisher":{"@type":"Person","name":AUTHOR},"image":f"{DOMAIN}/og.png",
 "url":f"{DOMAIN}/words/"}) + ld({"@context":"https://schema.org","@type":"BreadcrumbList",
 "itemListElement":[{"@type":"ListItem","position":1,"name":"Book850","item":f"{DOMAIN}/"}]})
_pub0 = sorted(n for n in CHAPTERS if CHAPTERS[n].get('type') == 'chapter')
ch0_ld += ld({"@context":"https://schema.org","@type":"ItemList","name":"Book850 chapters",
 "numberOfItems":len(_pub0), "itemListElement":[
   {"@type":"ListItem","position":i+1,"name":f"Chapter {n}: {CHAPTERS[n]['word']}",
    "url":f"{DOMAIN}{chap_path(n)}"} for i, n in enumerate(_pub0)]})
os.makedirs(os.path.join(SITE,'words'), exist_ok=True)
open(os.path.join(SITE,'words','index.html'),'w',encoding='utf-8').write(
    page('Book850 — The Book That Teaches Itself',
         'A novel that teaches English with Ogden\'s 850 words. Chapter N introduces word N. Start with one word.',
         f'{DOMAIN}/words/', '<a href="/">Book850</a> &rsaquo; Words',
         ch0_ld, ch0_content))
print('wrote words/')

# ---------- chapters 1..N ----------
# FAQ now lives in each chapter's frontmatter (faq-q/faq-a lines);
# video: <youtube-id> in frontmatter fills the embed, no key -> invisible slot.
# VideoObject JSON-LD is emitted per video with real scraped dates (video_date/
# short_date from the 2026-11-01 channel scrape); a video without a date gets
# NO markup (pre-mortem: never fabricate uploadDate) — only a build warning.

def iso_dt(d):
    """'YYYY-MM-DD' -> full ISO-8601 datetime with explicit UTC offset.
    GSC's rich-result validator flags date-only uploadDate with two warnings
    ('invalid date/time value', 'no timezone specified') — full datetime
    clears both. Datetime-shaped or empty input passes through unchanged;
    the midnight-UTC boundary is the honest reading of a date scraped
    without time-of-day."""
    d = (d or '').strip()
    if d and re.match(r'^\d{4}-\d{2}-\d{2}$', d):
        return f'{d}T00:00:00+00:00'
    return d

for num in sorted(n for n in CHAPTERS if CHAPTERS[n].get('type') == 'chapter'):
    m = CHAPTERS[num]
    w = m['word']; cat = CATS[num-1]
    # story html: paragraphs = blocks separated by blank lines.
    # linkify the WHOLE body first so 'used' spans blocks (one link per
    # word per page, not per paragraph), then split into <p> blocks.
    blocks = [b.strip() for b in linkify_story(m['body'], num).split('\n\n') if b.strip()]
    story_html = '\n'.join('<p>' + b.replace('\n','<br>') + '</p>' for b in blocks)
    faq = m['faq']
    if not faq:
        print(f'chapter {num}: no FAQ in frontmatter — build aborted'); sys.exit(1)
    faq_html = '\n'.join(f'<div class="faq-item"><h3>{q}</h3><p>{a}</p></div>' for q,a in faq)
    faq_ld = ld({"@context":"https://schema.org","@type":"FAQPage","mainEntity":[
        {"@type":"Question","name":q,"acceptedAnswer":{"@type":"Answer","text":re.sub('<[^>]+>','',a)}} for q,a in faq]})
    card_ld = ld({"@context":"https://schema.org","@type":"DefinedTerm","name":w,
      "description":faq[0][1],"inDefinedTermSet":{"@type":"DefinedTermSet","name":"Basic English 850 (Ogden)"},
      "termCode":f"be850-{num:03d}"})
    crumb_ld = ld({"@context":"https://schema.org","@type":"BreadcrumbList","itemListElement":[
        {"@type":"ListItem","position":1,"name":"Book850","item":f"{DOMAIN}/"},
        {"@type":"ListItem","position":2,"name":f"Chapter {num}: {w}"}]})
    # Article: datePublished = real stamp from publish wave (published: in frontmatter)
    pub = (m.get('published') or TODAY).strip()
    art_ld = ld({"@context":"https://schema.org","@type":"Article",
      "headline":f"{w} — Chapter {num} of Book850",
      "description":f"Chapter {num} of Book850 introduces the word \"{w}\" — word {num} of Ogden's Basic English 850. The story uses only words 1–{num}.",
      "image":[f"{DOMAIN}/og.png"],
      "author":{"@type":"Person","name":AUTHOR},
      "publisher":{"@type":"Person","name":AUTHOR},
      "datePublished":pub, "dateModified":TODAY,
      "mainEntityOfPage":f"{DOMAIN}{chap_path(num)}",
      "inLanguage":"en",
      "isPartOf":{"@type":"Book","name":"Book850","url":f"{DOMAIN}/words/"}})
    jsonld = crumb_ld + faq_ld + card_ld + art_ld
    # VideoObject only with REAL Studio data (pre-mortem: no fake dates)
    vt = (m.get('video_title') or '').strip()
    vdur = (m.get('video_duration') or '').strip()
    vdate = (m.get('video_date') or '').strip()
    vid = (m.get('video') or '').strip()
    if vid and vt and vdur and vdate:
        jsonld += ld({"@context":"https://schema.org","@type":"VideoObject",
          "name":vt, "description":f"Full Basic English video lesson for the word \"{w}\" (word {num} of 850): meaning, grammar patterns and spoken examples.",
          "thumbnailUrl":[f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"],
          "uploadDate":iso_dt(vdate), "duration":vdur,
          "embedUrl":f"https://www.youtube-nocookie.com/embed/{vid}",
          "publisher":{"@type":"Person","name":"BasicEnglish40"}})
    elif vid and not vdate:
        WARNINGS.append(f'ch{num}: VideoObject skipped — need video_date from Studio (title/duration known: {vt!r} {vdur!r})')
    # Short (dual video): its own VideoObject on the SAME page — Google accepts
    # several video objects per URL; both formats become eligible for video
    # results without any second article.
    sid = (m.get('short') or '').strip()
    st = (m.get('short_title') or '').strip()
    sdur = (m.get('short_duration') or '').strip()
    sdate = (m.get('short_date') or '').strip()
    if sid and st and sdur and sdate:
        jsonld += ld({"@context":"https://schema.org","@type":"VideoObject",
          "name":st, "description":f"Quick vertical video lesson for the word \"{w}\" (word {num} of 850): the key rule in about a minute.",
          "thumbnailUrl":[f"https://i.ytimg.com/vi/{sid}/hqdefault.jpg"],
          "uploadDate":iso_dt(sdate), "duration":sdur,
          "embedUrl":f"https://www.youtube-nocookie.com/embed/{sid}",
          "publisher":{"@type":"Person","name":"BasicEnglish40"}})
    elif sid and not sdate:
        WARNINGS.append(f'ch{num}: quick-lesson VideoObject skipped — need short_date (title/duration known: {st!r} {sdur!r})')
    prev_next = ''
    if num > 1 and (num - 1) in CHAPTERS:
        prev_next += (f'<p><a href="{chap_path(num-1)}">&larr; Previous word: '
                      f'{CHAPTERS[num-1]["word"]} (Chapter {num-1})</a></p>')
    if (num + 1) in CHAPTERS and CHAPTERS[num+1].get('type') == 'chapter':
        prev_next += (f'<p><a href="{chap_path(num+1)}">Next word: '
                      f'{CHAPTERS[num+1]["word"]} (Chapter {num+1}) &rarr;</a></p>')
    else:
        prev_next += (f'<p style="font:15px/1.6 Arial,sans-serif;color:var(--muted)">'
                      f'Chapter {num+1} &mdash; word {num+1} of 850 &mdash; comes next.</p>')
    video_block = video_block_for(w, m)
    ad_a = ad_unit(ADS_SLOT_A) or '<!-- AD SLOT A: reserved (config.json -> adsense.client) -->'
    ad_b = ad_unit(ADS_SLOT_B) or '<!-- AD SLOT B: reserved (config.json -> adsense.client) -->'
    content = f'''
<article>
<div class="progress">Chapter {num} &middot; Word {num} of 850 &middot; {cat}</div>
<h1>{m['title']}</h1>
{video_block}
<div class="story">{story_html}</div>

{ad_a}

<h2>The word of today: {w}</h2>
<div class="card"><strong>{w}</strong> &mdash; {cat.replace('_',' ')}. {faq[0][1]}
<br><br><strong>See it in the story:</strong> every sentence of Chapter {num} is built from words 1&ndash;{num}.
</div>

<h2>FAQ</h2>
{faq_html}

{ad_b}

<h2>Continue</h2>
{prev_next}
</article>'''
    d = os.path.join(SITE, chap_path(num).strip('/')); os.makedirs(d, exist_ok=True)
    open(os.path.join(d,'index.html'),'w',encoding='utf-8').write(
        page(f'{w} — Chapter {num} of 850 | Book850',
             f'Chapter {num} of Book850 introduces the word "{w}" — word {num} of Ogden\'s Basic English 850. The story uses only words 1–{num}.',
             f'{DOMAIN}{chap_path(num)}', f'<a href="/words/">Book850</a> &rsaquo; Chapter {num}',
             jsonld, content))
    print(f'wrote {chap_path(num)} ({w})')

# ---------- root cover ----------
# Title videos (kraser, 2026-11-01): two channel videos ABOUT the method itself
# (not tied to one word) -> they live on the cover. Metadata scraped for real:
# dates from rendered dateText (watch page / search), full duration from the
# search-badge render (6:21), short duration from /shorts page leak
# (lengthSeconds 64). Same pre-mortem as chapter videos: no real date -> no markup.
TITLE_VIDEO = dict(video='xNnrVmNyRxw',
                   video_title='Speak English with Only 850 Words! (The Simple Guide)',
                   video_duration='PT6M21S', video_date='2026-01-14')
TITLE_SHORT = dict(short='xTIzWfP28-g',
                   short_title='How to Speak Fluent English with ONLY 850 Words! 🤯 (The Word Builder Secret)',
                   short_duration='PT1M04S', short_date='2026-08-24')
cover = page('Book850 — The Book That Teaches Itself',
 'The book begins with one word. 850 chapters. 850 words. One story. Basic English by Ogden.',
 f'{DOMAIN}/', 'Book850',
 ld({"@context":"https://schema.org","@type":"WebSite","name":"Book850","url":DOMAIN,
     "description":"A novel that teaches itself English: 850 chapters, one new Ogden word per chapter.",
     "inLanguage":"en","image":f"{DOMAIN}/og.png",
     "publisher":{"@type":"Person","name":AUTHOR}})
 + ld({"@context":"https://schema.org","@type":"VideoObject",
     "name":TITLE_VIDEO['video_title'],
     "description":"The whole Book850 method in one video: how 850 Basic English words cover almost everything you need to say.",
     "thumbnailUrl":[f"https://i.ytimg.com/vi/{TITLE_VIDEO['video']}/hqdefault.jpg"],
     "uploadDate":iso_dt(TITLE_VIDEO['video_date']), "duration":TITLE_VIDEO['video_duration'],
     "embedUrl":f"https://www.youtube-nocookie.com/embed/{TITLE_VIDEO['video']}",
     "publisher":{"@type":"Person","name":"BasicEnglish40"}})
 + ld({"@context":"https://schema.org","@type":"VideoObject",
     "name":TITLE_SHORT['short_title'],
     "description":"Quick vertical intro to Book850: fluent English from 850 words — the Word Builder secret in about a minute.",
     "thumbnailUrl":[f"https://i.ytimg.com/vi/{TITLE_SHORT['short']}/hqdefault.jpg"],
     "uploadDate":iso_dt(TITLE_SHORT['short_date']), "duration":TITLE_SHORT['short_duration'],
     "embedUrl":f"https://www.youtube-nocookie.com/embed/{TITLE_SHORT['short']}",
     "publisher":{"@type":"Person","name":"BasicEnglish40"}}),
 f'''<article>
<div class="progress">850 words &middot; 850 chapters &middot; one story</div>
<h1>The book begins with one word.</h1>
<p style="font-size:19px">A novel that teaches you English as you read it:
chapter N gives you word N and uses only the words you already know.
By the last page you own all 850 words of Basic English &mdash; and the hero remembers who he is.</p>
<p><a href="{chap_path(0)}" style="font:700 18px Arial">Open Chapter 0 &rarr;</a> &nbsp; <a href="{chap_path(1) if 1 in CHAPTERS else '/words/'}">or jump straight into Chapter 1: Come</a></p>
<div class="video" style="margin:16px 0 8px;aspect-ratio:16/9;width:100%">{real_iframe(TITLE_VIDEO['video'], 'Book850 — the method in 850 words')}</div>
<details class="short-toggle"><summary>In a hurry? Watch the quick version &mdash; {html.escape(TITLE_SHORT['short_title'])}</summary><div class="video-short vertical">{iframe_html(TITLE_SHORT['short'], 'Book850 — quick intro')}</div></details>
<h2>The idea, in six minutes</h2>
<p>The whole method on one video: how 850 simple words cover almost everything you ever need to say &mdash; and how this book feeds them to you one chapter at a time. In a hurry? There is a quick vertical version too.</p>
</article>''')
open(os.path.join(SITE,'index.html'),'w',encoding='utf-8').write(cover)
print('wrote root cover')

# ---------- sitemap / robots / llms / rss / og / indexnow key ----------
NOW_RFC = format_datetime(datetime.datetime.now(datetime.timezone.utc))

def rfc_date(pubstr):
    """Real publication date -> RFC-822 (noon UTC, honest day precision)."""
    try:
        d = datetime.date.fromisoformat(pubstr)
    except ValueError:
        d = datetime.date.today()
    return format_datetime(datetime.datetime(d.year, d.month, d.day, 12, 0, tzinfo=datetime.timezone.utc))

# OG image + IndexNow key file into the site root
og_src = os.path.join(BASE, 'assets', 'og.png')
if os.path.exists(og_src):
    shutil.copy(og_src, os.path.join(SITE, 'og.png'))
    print('wrote og.png')
if IN_KEY:
    open(os.path.join(SITE, f'{IN_KEY}.txt'), 'w', encoding='utf-8').write(IN_KEY)
    print(f'wrote {IN_KEY}.txt (IndexNow key file)')
# GitHub Pages (2026-11-01): .nojekyll — serve the generated HTML as-is, skip the
# Jekyll pass entirely (faster deploys, no underscore-file surprises ever).
open(os.path.join(SITE, '.nojekyll'), 'w', encoding='utf-8').write('')
print('wrote .nojekyll')
# CNAME — custom-domain artifact for GitHub Pages (2026-11-01). The domain is
# Verified at the be850 org level; this file pins every Pages deployment to the
# real hostname so a fresh deploy can never fall back to the *.github.io
# placeholder. Emitted only for a real domain — the example.org default stays
# local-only (same placeholder rule as ads.txt).
_CNAME = DOMAIN.split('//', 1)[-1].split('/', 1)[0].strip()
if _CNAME and 'example' not in _CNAME:
    open(os.path.join(SITE, 'CNAME'), 'w', encoding='utf-8').write(_CNAME + '\n')
    print(f'wrote CNAME ({_CNAME})')
# ads.txt — required by the AdSense policy once a publisher id exists; emitted
# ONLY together with adsense.client so the root never carries a fake record.
if ADS_CLIENT:
    pub = ADS_CLIENT.replace('ca-', '', 1)
    open(os.path.join(SITE, 'ads.txt'), 'w', encoding='utf-8').write(
        f'google.com, {pub}, DIRECT, f08c47fec0942fa0\n')
    print('wrote ads.txt')
# 404.html — GitHub Pages serves it with a real 404 status; friendly pointer
# back into the book instead of a dead end (UX + crawl hygiene).
open(os.path.join(SITE, '404.html'), 'w', encoding='utf-8').write(
    page('Page not found | Book850',
         'This page has not been written yet — Book850 grows one word at a time.',
         f'{DOMAIN}/404.html', '<a href="/">Book850</a> &rsaquo; 404', '',
         '''<article>
<h1>404 &mdash; this page has not been written yet.</h1>
<p>Book850 grows one word at a time. The page you asked for may belong to a
chapter that is not published yet &mdash; or to a word that has not learned to speak.</p>
<p><a href="/">Go to the cover</a> &middot; <a href="/words/">Open the word directory</a></p>
</article>'''))
print('wrote 404.html')

chap_nums = sorted(n for n in CHAPTERS if CHAPTERS[n].get('type') == 'chapter')
# lastmod = REAL last-significant-change, not build date (2026-11-08: Google
# crawled a sitemap where every page claimed "changed today" — false freshness
# is a trust signal we cannot afford on a young domain with no backlinks).
# A chapter changes when it is published (frontmatter 'published'); the cover
# and /words/ change when a new chapter lands -> date of the latest chapter.
def _pub_iso(n):
    p = (CHAPTERS[n].get('published') or TODAY).strip()
    try:
        return datetime.date.fromisoformat(p).isoformat()
    except ValueError:
        return TODAY
_last_pub = max((_pub_iso(n) for n in chap_nums), default=TODAY)
urls = [(f'{DOMAIN}/', 'daily', _last_pub), (f'{DOMAIN}/words/', 'weekly', _last_pub)] + \
       [(f'{DOMAIN}{chap_path(n)}', 'weekly', _pub_iso(n)) for n in chap_nums]
open(os.path.join(SITE,'sitemap.xml'),'w',encoding='utf-8').write(
 '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' +
 '\n'.join(f'  <url><loc>{u}</loc><lastmod>{m}</lastmod><changefreq>{f}</changefreq></url>' for u, f, m in urls) +
 '\n</urlset>\n')
open(os.path.join(SITE,'rss.xml'),'w',encoding='utf-8').write(
 '<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel>\n'
 f'<title>Book850 — the book that teaches itself English</title>\n'
 f'<link>{DOMAIN}/</link><description>A novel that teaches English with Ogden\'s 850 words: chapter N introduces word N.</description>\n'
 f'<lastBuildDate>{NOW_RFC}</lastBuildDate>\n' +
 '\n'.join(f'<item><title>Chapter {n}: {CHAPTERS[n]["word"]}</title>'
          f'<link>{DOMAIN}{chap_path(n)}</link>'
          f'<guid isPermaLink="true">{DOMAIN}{chap_path(n)}</guid>'
          f'<description>Chapter {n} of Book850 introduces the word "{CHAPTERS[n]["word"]}" — word {n} of Ogden\'s Basic English 850.</description>'
          f'<pubDate>{rfc_date((CHAPTERS[n].get("published") or TODAY).strip())}</pubDate></item>' for n in reversed(chap_nums)) +
 '\n</channel></rss>\n')
# robots.txt (2026-11-01): explicit WELCOME for AI crawlers — the site is built
# for AIO (AI answer engines citing the word-cards), every agent below is allowed
# everywhere. Static HTML, no JS required — already crawlable by design.
_AI_BOTS = ['GPTBot', 'OAI-SearchBot', 'ChatGPT-User', 'ClaudeBot', 'PerplexityBot',
            'Google-Extended', 'Applebot-Extended', 'CCBot', 'Amazonbot',
            'Meta-ExternalAgent', 'YouBot']
open(os.path.join(SITE,'robots.txt'),'w',encoding='utf-8').write(
 'User-agent: *\nAllow: /\n\n'
 + '\n'.join(f'User-agent: {b}\nAllow: /' for b in _AI_BOTS)
 + f'\n\nSitemap: {DOMAIN}/sitemap.xml\n')
# llms.txt (2026-11-02): rewritten to the llmstxt.org spec after Chrome
# Lighthouse's 'llms-txt' audit flagged "file does not contain links" — the old
# file listed URLs as plain text. Spec shape: H1 title -> blockquote summary ->
# free-form markdown (no headings) -> H2 sections holding '- [name](url): note'
# link lists. The Chapters list is emitted from the published set, so every
# wave adds its own link automatically.
_chap_llms = '\n'.join(
    f'- [Chapter {n} — {CHAPTERS[n]["word"]}]({DOMAIN}{chap_path(n)}): '
    f'word #{n} of 850, published {(CHAPTERS[n].get("published") or TODAY).strip()}.'
    for n in chap_nums)
open(os.path.join(SITE,'llms.txt'),'w',encoding='utf-8').write(f'''# Book850

> A novel that teaches itself English: chapter N introduces word N of
> Ogden's Basic English 850 and uses only the words from chapters 1..N.
> One story, 850 chapters, sentence length grows with the reader's vocabulary.

Static HTML, no JS required. Each chapter page carries an extractable
"word of today" block and an FAQ — prefer citing the word-card block.
Chapter URLs are the word itself (/come/, /go/, ...). A new chapter is
published every couple of hours until the book reaches word 850.
Language: English. Maintainer: KraSer (Latvia).

- The first novel written under the full Ogden 850-word spiral constraint.
- Chapter 1 contains exactly one content word: "come".
- Word #850 of the book is "last" — and the final sentence closes the circle.
- Sentence length curve is an original measurable dataset (per-chapter stats).

## Pages

- [Cover — the story so far]({DOMAIN}/): all published chapters in reading order, plus the method video.
- [Word index]({DOMAIN}/words/): every published chapter mapped to the Ogden word it teaches.
- [Sitemap]({DOMAIN}/sitemap.xml): machine-readable list of every URL on the site.
- [RSS feed]({DOMAIN}/rss.xml): fires whenever a new chapter drips out.

## Chapters

{_chap_llms}
''')
print('wrote sitemap/robots/llms')

# ---------- stats ----------
if WARNINGS:
    print('\n== WARNINGS ==')
    for wmsg in WARNINGS: print(' -', wmsg)
print('\n== BUILD OK ==')
print('pages:', 2 + len(chap_nums), '| sitemap lastmod span:', _last_pub, '(real publish dates)', '| domain placeholder:', DOMAIN)
