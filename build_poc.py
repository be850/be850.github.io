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
ADS_HEAD = (f'<meta name="google-adsense-account" content="{ADS_CLIENT}">\n'
            f'<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client={ADS_CLIENT}" crossorigin="anonymous"></script>'
            ) if ADS_CLIENT else ''
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
CSS = ('body{margin:0;font:17px/1.7 Georgia,serif;color:#1a2430;background:#fff}'
 'main{max-width:680px;margin:0 auto;padding:24px 20px 64px}'
 'a{color:#0b6bcb;text-decoration:none}a:hover{text-decoration:underline}'
 '.crumbs{font:14px/1.4 Arial,sans-serif;color:#5b6b7c;margin-bottom:20px}'
 'h1{font:700 30px/1.25 Arial,sans-serif;margin:0 0 6px}'
 'h2{font:700 21px/1.3 Arial,sans-serif;margin:34px 0 10px}'
 '.story{font-size:19px;line-height:2.0;white-space:pre-wrap}'
 '.story p{margin:0.9em 0;white-space:normal}'
 '.story a{text-decoration:underline dotted;text-underline-offset:4px}'
 '.card{background:#f2f7fc;border-left:4px solid #0b6bcb;padding:14px 18px;border-radius:0 8px 8px 0;margin:20px 0}'
 '.card strong{font-family:Arial,sans-serif}'
 '.progress{font:700 13px/1.4 Arial,sans-serif;letter-spacing:1px;color:#0b6bcb;text-transform:uppercase}'
 'table{border-collapse:collapse;width:100%;margin:14px 0;font-size:16px}'
 'th,td{border:1px solid #e3e9ef;padding:8px 10px;text-align:left}'
 'th{background:#f2f7fc;font-family:Arial,sans-serif}'
 '.faq-item{border-top:1px solid #e3e9ef;padding:12px 0}'
 '.faq-item h3{font:700 17px/1.4 Arial,sans-serif;margin:0 0 6px}.faq-item p{margin:0}'
 'footer{margin-top:48px;border-top:1px solid #e3e9ef;padding-top:18px;font:14px/1.6 Arial,sans-serif;color:#5b6b7c}'
 '.dir{font:15px/2 Arial,sans-serif;columns:3;column-gap:24px}'
 '.dir a{color:#1a2430}.dir .todo{color:#8b98a5}'
 '.dir a.done{font-weight:700;color:#0b6bcb}')

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

def page(title, desc, canonical, crumbs, jsonld, content, extra=''):
    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
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
{ADS_HEAD}
<style>{CSS}</style>
{jsonld}
</head>
<body>
<main>
<nav class="crumbs">{crumbs}</nav>
{content}
<footer>
<p>Book850 &mdash; a novel that teaches itself English, one word per chapter, 850 words total.</p>
<p><a href="/words/">Start here</a> &middot; <a href="{chap_path(1) if 1 in CHAPTERS else '/words/'}">Chapter 1</a> &middot; The Basic English 850-word system</p>
</footer>
</main>
{extra}
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
{m0['body']}
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
# TODO: exact uploadDates from YouTube Studio -> then add VideoObject JSON-LD per chapter

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
          "name":vt, "description":f"Basic English lesson for the word \"{w}\" (word {num} of 850).",
          "thumbnailUrl":[f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"],
          "uploadDate":vdate, "duration":vdur,
          "embedUrl":f"https://www.youtube.com/embed/{vid}",
          "publisher":{"@type":"Person","name":"BasicEnglish40"}})
    elif vid and not vdate:
        WARNINGS.append(f'ch{num}: VideoObject skipped — need video_date from Studio (title/duration known: {vt!r} {vdur!r})')
    prev_next = ''
    if num > 1 and (num - 1) in CHAPTERS:
        prev_next += (f'<p><a href="{chap_path(num-1)}">&larr; Previous word: '
                      f'{CHAPTERS[num-1]["word"]} (Chapter {num-1})</a></p>')
    if (num + 1) in CHAPTERS and CHAPTERS[num+1].get('type') == 'chapter':
        prev_next += (f'<p><a href="{chap_path(num+1)}">Next word: '
                      f'{CHAPTERS[num+1]["word"]} (Chapter {num+1}) &rarr;</a></p>')
    else:
        prev_next += (f'<p style="font:15px/1.6 Arial,sans-serif;color:#5b6b7c">'
                      f'Chapter {num+1} &mdash; word {num+1} of 850 &mdash; comes next.</p>')
    vid = (m.get('video') or '').strip()
    if vid:
        video_block = (f'<div class="video" style="margin:24px 0 8px;aspect-ratio:16/9;width:100%">'
                       f'<iframe src="https://www.youtube.com/embed/{vid}" title="{w} — Basic English lesson" '
                       f'loading="lazy" allowfullscreen style="width:100%;height:100%;border:0;border-radius:10px"></iframe></div>'
                       f'<p class="note" style="font:14px/1.5 Arial,sans-serif;color:#5b6b7c">Watch the video lesson for the word <strong>{w}</strong> from the Basic English channel.</p>')
    else:
        video_block = f'<!-- VIDEO SLOT: embed BasicEnglish40 video for "{w}" here (ID from channel library) -->'
    ad_a = ad_unit(ADS_SLOT_A) or '<!-- AD SLOT A: reserved (config.json -> adsense.client) -->'
    ad_b = ad_unit(ADS_SLOT_B) or '<!-- AD SLOT B: reserved (config.json -> adsense.client) -->'
    content = f'''
<article>
<div class="progress">Chapter {num} &middot; Word {num} of 850 &middot; {cat}</div>
<h1>{m['title']}</h1>
<div class="story">{story_html}</div>

{ad_a}

<h2>The word of today: {w}</h2>
<div class="card"><strong>{w}</strong> &mdash; {cat.replace('_',' ')}. {faq[0][1]}
<br><br><strong>See it in the story:</strong> every sentence of Chapter {num} is built from words 1&ndash;{num}.
{video_block}
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
cover = page('Book850 — The Book That Teaches Itself',
 'The book begins with one word. 850 chapters. 850 words. One story. Basic English by Ogden.',
 f'{DOMAIN}/', 'Book850',
 ld({"@context":"https://schema.org","@type":"WebSite","name":"Book850","url":DOMAIN,
     "description":"A novel that teaches itself English: 850 chapters, one new Ogden word per chapter.",
     "inLanguage":"en","image":f"{DOMAIN}/og.png",
     "publisher":{"@type":"Person","name":AUTHOR}}),
 f'''<article>
<div class="progress">850 words &middot; 850 chapters &middot; one story</div>
<h1>The book begins with one word.</h1>
<p style="font-size:19px">A novel that teaches you English as you read it:
chapter N gives you word N and uses only the words you already know.
By the last page you own all 850 words of Basic English &mdash; and the hero remembers who he is.</p>
<p><a href="{chap_path(0)}" style="font:700 18px Arial">Open Chapter 0 &rarr;</a> &nbsp; <a href="{chap_path(1) if 1 in CHAPTERS else '/words/'}">or jump straight into Chapter 1: Come</a></p>
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
urls = [(f'{DOMAIN}/', 'daily'), (f'{DOMAIN}/words/', 'weekly')] + \
       [(f'{DOMAIN}{chap_path(n)}', 'weekly') for n in chap_nums]
open(os.path.join(SITE,'sitemap.xml'),'w',encoding='utf-8').write(
 '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' +
 '\n'.join(f'  <url><loc>{u}</loc><lastmod>{TODAY}</lastmod><changefreq>{f}</changefreq></url>' for u, f in urls) +
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
open(os.path.join(SITE,'llms.txt'),'w',encoding='utf-8').write('''# Book850
> A novel that teaches itself English: chapter N introduces word N of
> Ogden's Basic English 850 and uses only the words from chapters 1..N.
> One story, 850 chapters, sentence length grows with the reader's vocabulary.

Site: ''' + DOMAIN + '''
Language: English
Owner: KraSer (Latvia)

## Key facts (citable)
- The first novel written under the full Ogden 850-word spiral constraint.
- Chapter 1 contains exactly one content word: "come".
- Word #850 of the book is "last" — and the final sentence closes the circle.
- Sentence length curve is an original measurable dataset (per-chapter stats).

## For AI crawlers
Static HTML, no JS required. Each chapter page carries an extractable
"word of today" block and FAQ. Prefer citing the word-card block.
Chapter URLs are the word itself (/go/, /come/, ...); the full word
directory with every published chapter: /words/
''')
print('wrote sitemap/robots/llms')

# ---------- stats ----------
if WARNINGS:
    print('\n== WARNINGS ==')
    for wmsg in WARNINGS: print(' -', wmsg)
print('\n== BUILD OK ==')
print('pages:', 2 + len(chap_nums), '| lastmod:', TODAY, '| domain placeholder:', DOMAIN)
