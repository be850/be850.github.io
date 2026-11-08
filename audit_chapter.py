#!/usr/bin/env python3
"""Book850 post-wave audit (2026-11-01, per kraser: 'после каждого поста главы — анализ').

Two modes, one checklist:
  --artifact site/public        CI mode: audit built files right after deploy (deterministic)
  --live https://be850.sok.lv   HQ mode: audit the real site (CDN/domain/deploy truth)

Audited for the LAST PUBLISHED chapter (state.json -> last_chapter, or --ch N):
  1. page 200 (live: 3 attempts x 20s — CDN propagation)
  2. canonical == {domain}/{word}/
  3. h1 title present
  4. "The word of today: {word}" block
  5. full-lesson iframe (embed/{video}) when video: set
  6. quick-lesson: <details> toggle when short+video, plain embed when short-only
  7. VideoObject count == expected (full needs video+title+duration+date; short ditto)
  8. FAQPage JSON-LD present
  9. prev/next navigation (prev chapter link / 'comes next' stub)
 10. no leftover VIDEO SLOT placeholder
 11. sitemap.xml contains the chapter URL
 12. rss.xml contains the chapter link
 13. /words/ hub links the chapter
 14. OG tags (og:title, og:image, og:url)
 15. IndexNow key file answers 200
Exit 1 on any FAIL; WARNs (network-bound checks) never fail the run.
"""
import json, os, re, subprocess, sys, time

BASE = os.path.dirname(os.path.abspath(__file__))
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'}


def parse_md(path):
    meta = {}
    for ln in open(path, encoding='utf-8').read().splitlines():
        if ln.strip() == '':
            break
        k, _, v = ln.partition(':')
        meta[k.strip()] = v.strip()
    return meta


def _cf_challenge(body):
    """Cloudflare challenge markers (page body, not headers)."""
    return ('Just a moment' in body or 'challenge-platform' in body
            or 'challenges.cloudflare.com' in body)


def fetch(url, tries=3, wait=20):
    """Live fetch via curl subprocess.

    2026-11-06 22:30Z: python-urllib TLS fingerprint (JA3) started getting
    Cloudflare challenges served as 404 pages, while curl with the same UA
    passes fine -> hard FAIL 'page -> 404' on a perfectly healthy site.
    curl transport is the fix; challenge detection kept (403/404/503 + markers).
    """
    last = None
    for i in range(tries):
        try:
            p = subprocess.run(
                ['curl', '-s', '-A', UA['User-Agent'], '--max-time', '25',
                 '-w', '\n__STATUS__%{http_code}', url],
                capture_output=True, text=True, timeout=30)
            out = p.stdout
            if '__STATUS__' in out:
                body, _, code = out.rpartition('\n__STATUS__')
                code = int(code.strip() or 0)
            else:
                body, code = out, 0
            if code and not _cf_challenge(body):
                return code, body
            if code in (403, 404, 503) and _cf_challenge(body):
                last = 'CF_CHALLENGE'
            elif code:
                return code, body          # real HTTP answer, no challenge
            else:
                last = f'curl exit {p.returncode}: {p.stderr[:80]}'
            if i + 1 < tries:
                time.sleep(wait)
        except Exception as e:
            last = e
            if i + 1 < tries:
                time.sleep(wait)
    return None, f'FETCH ERROR: {last}'


class Audit:
    def __init__(self, mode, root):
        self.mode = mode          # 'artifact' | 'live'
        self.root = root.rstrip('/')
        self.results = []         # (status, name, detail)

    def get(self, path):
        """path like '/go/' or 'sitemap.xml' — returns (status, text)"""
        if self.mode == 'artifact':
            fn = os.path.join(self.root, path.lstrip('/'))
            if path == '/':
                fn = os.path.join(self.root, 'index.html')
            if os.path.isdir(fn):
                fn = os.path.join(fn, 'index.html')
            if os.path.isfile(fn):
                return 200, open(fn, encoding='utf-8').read()
            return 404, ''
        return fetch(self.root + path)

    def check(self, name, ok, detail=''):
        self.results.append(('OK' if ok else 'FAIL', name, detail))
        return ok

    def warn(self, name, detail=''):
        self.results.append(('WARN', name, detail))


def video_objects(html_text):
    out = []
    for m in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html_text, re.DOTALL):
        try:
            d = json.loads(m)
        except Exception:
            continue
        items = d if isinstance(d, list) else [d]
        for it in items:
            if isinstance(it, dict) and it.get('@type') == 'VideoObject':
                out.append(it)
    return out


def audit(ch, cfg, A):
    chp = os.path.join(BASE, 'chapters', f'chapter-{ch:02d}.md')
    if not os.path.isfile(chp):
        chp = os.path.join(BASE, 'chapters', f'chapter-{ch}.md')
    meta = parse_md(chp)
    w = meta['word']
    dom = cfg['domain'].rstrip('/')
    page_path = (meta.get('url') or f'/{w}/')

    st, html_text = A.get(page_path)
    page_detail = '' if st == 200 else 'chapter page not reachable' + (
        f' ({str(html_text)[:60]})' if html_text else '')
    if not A.check(f'page {page_path} -> {st}', st == 200, page_detail):
        return A
    # 2 canonical
    A.check('canonical', f'href="{dom}{page_path}"' in html_text or f"href='{dom}{page_path}'" in html_text)
    # 3 h1
    A.check('h1 present', re.search(r'<h1[^>]*>.{0,200}</h1>', html_text, re.DOTALL) is not None)
    # 4 word block
    A.check('word-of-today block', f'The word of today: {w}' in html_text)
    # 5 full embed
    vid = (meta.get('video') or '').strip()
    if vid:
        A.check(f'full embed {vid}', f'/embed/{vid}' in html_text)
    # 6 short lesson (2026-11-08: mirror build_poc.py — the <details> toggle
    # renders ONLY for short+video pairs; short-only chapters get a plain
    # quick-lesson iframe (elif branch), so demanding <details> there was a
    # false FAIL on all 57 sweep chapters ch12-68, first hit ch62 'because')
    sid = (meta.get('short') or '').strip()
    if sid and vid:
        A.check(f'short toggle {sid}', '<details class="short-toggle">' in html_text)
        m = re.search(r'<details class="short-toggle">.*?</details>', html_text, re.DOTALL)
        A.check('short inside details', bool(m and f'/embed/{sid}' in m.group(0)))
    elif sid:
        A.check(f'short quick-lesson {sid}', f'/embed/{sid}' in html_text)
    # 7 VideoObjects
    vos = video_objects(html_text)
    expect = 0
    if vid and meta.get('video_title') and meta.get('video_duration') and meta.get('video_date'):
        expect += 1
    if sid and meta.get('short_title') and meta.get('short_duration') and meta.get('short_date'):
        expect += 1
    A.check(f'VideoObject count == {expect}', len(vos) == expect, f'found {len(vos)}')
    for vo in vos:
        if not vo.get('uploadDate') or not vo.get('duration'):
            A.check('VideoObject fields complete', False, str(vo.get('name'))[:60])
    # 8 FAQ
    A.check('FAQPage JSON-LD', '"FAQPage"' in html_text)
    # 9 nav
    if ch > 1:
        A.check('prev nav', 'Previous word' in html_text)
    else:
        A.check('next nav/stub', ('Next word' in html_text) or ('comes next' in html_text))
    # 10 placeholder
    A.check('no VIDEO SLOT placeholder', 'VIDEO SLOT' not in html_text)
    # 14 OG
    A.check('og:title', 'property="og:title"' in html_text)
    A.check('og:image', 'property="og:image"' in html_text)
    A.check('og:url', 'property="og:url"' in html_text)

    # 11 sitemap
    st, sm = A.get('/sitemap.xml')
    if st == 200:
        A.check('sitemap has chapter', f'{dom}{page_path}' in sm)
    else:
        A.warn('sitemap reachable', f'status {st}')
    # 12 rss
    st, rss = A.get('/rss.xml')
    if st == 200:
        A.check('rss has chapter', page_path in rss)
    else:
        A.warn('rss reachable', f'status {st}')
    # 13 hub
    st, hub = A.get('/words/')
    if st == 200:
        A.check('hub links chapter', f'href="{page_path}"' in hub)
    else:
        A.warn('hub reachable', f'status {st}')
    # 15 indexnow key file
    key = cfg.get('indexnow_key', '')
    if key:
        st, _ = A.get(f'/{key}.txt')
        if A.mode == 'live':
            if st == 200:
                A.check('indexnow key file', True)
            else:
                A.warn('indexnow key file', f'status {st}')
        else:
            A.check('indexnow key file', st == 200)
    return A


def main():
    args = sys.argv[1:]
    mode, root, only_ch = None, None, None
    if '--artifact' in args:
        mode = 'artifact'; root = args[args.index('--artifact') + 1]
    if '--live' in args:
        mode = 'live'; root = args[args.index('--live') + 1]
    if '--ch' in args:
        only_ch = int(args[args.index('--ch') + 1])
    if not mode:
        print(__doc__); sys.exit(2)
    cfg = json.load(open(os.path.join(BASE, 'config.json'), encoding='utf-8'))
    state = json.load(open(os.path.join(BASE, 'state.json'), encoding='utf-8'))
    ch = only_ch or int(state['last_chapter'])
    A = audit(ch, cfg, Audit(mode, root))
    fails = [r for r in A.results if r[0] == 'FAIL']
    warns = [r for r in A.results if r[0] == 'WARN']
    cf_block = any(r[0] == 'FAIL' and 'CF_CHALLENGE' in (r[2] or '') for r in A.results)
    if cf_block:
        print(f'== AUDIT ch{ch} ({mode}) == DEFERRED (Cloudflare challenge: auditor IP blocked, NOT a chapter failure)')
        for s, n, d in A.results:
            mark = {'OK': ' ok ', 'FAIL': 'FAIL', 'WARN': 'WARN'}[s]
            print(f' [{mark}] {n}' + (f' — {d}' if d else ''))
        sys.exit(2)
    print(f'== AUDIT ch{ch} ({mode}) == ' + ('PASS' if not fails else 'FAIL'))
    for s, n, d in A.results:
        mark = {'OK': ' ok ', 'FAIL': 'FAIL', 'WARN': 'WARN'}[s]
        print(f' [{mark}] {n}' + (f' — {d}' if d else ''))
    sys.exit(1 if fails else 0)


if __name__ == '__main__':
    main()
