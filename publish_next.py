#!/usr/bin/env python3
"""Book850 wave publisher.
Moves the next chapters from chapters/queue/ -> chapters/, rebuilds the site
(build_poc.py = spiral validator + generator; a failed build rolls everything
back), then commits & pushes. Designed for GitHub Actions scheduled workflow
(hourly drip, push state back with GITHUB_TOKEN) and for local runs.

Pace gate (2026-11-01): config.json -> min_hours_between_chapters (default 2).
state.json stores the UTC stamp of the last successful publish; a scheduled run
that arrives too early just rebuilds + redeploys (no new chapter). --force
overrides the gate for manual calibration waves.

Usage:
  python3 publish_next.py            # publish next 1 chapter (gate respected)
  python3 publish_next.py --n 5      # publish next 5
  python3 publish_next.py --force    # ignore the pace gate
  python3 publish_next.py --no-git   # build only, no commit/push
"""
import argparse, datetime, json, os, re, shutil, subprocess, sys, urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
QUEUE = os.path.join(BASE, 'chapters', 'queue')
CHAPS = os.path.join(BASE, 'chapters')
STATE = os.path.join(BASE, 'state.json')   # committed: {last_publish, last_chapter}


def min_hours():
    """Pace knob from config.json; default 2 -> up to 12 chapters/day."""
    try:
        return float((json.load(open(os.path.join(BASE, 'config.json'),
                                     encoding='utf-8')).get(
                     'min_hours_between_chapters')) or 2)
    except Exception:
        return 2.0


def gate_open(force):
    """True if enough time passed since the last publish (or gate bypassed)."""
    if force or not os.path.exists(STATE):
        return True, ''
    try:
        last = (json.load(open(STATE, encoding='utf-8')).get('last_publish') or '')
        dt = datetime.datetime.fromisoformat(last)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
    except Exception:
        return True, ''
    elapsed = (datetime.datetime.now(datetime.timezone.utc) - dt).total_seconds() / 3600
    if elapsed >= min_hours():
        return True, ''
    return False, (f'pace gate: last publish {last} is {elapsed:.1f}h ago, '
                   f'min_hours_between_chapters={min_hours():g} — rebuilding only '
                   '(use --force to override)')


def write_state(nums):
    """Stamp the publish wave AFTER a green build (validator passed)."""
    json.dump({'last_publish': datetime.datetime.now(datetime.timezone.utc)
               .replace(microsecond=0).isoformat(),
               'last_chapter': max(nums)},
              open(STATE, 'w', encoding='utf-8'), indent=1)


def stamp_published(path):
    """Stamp 'published: YYYY-MM-DD' = real publish date (Riga) into frontmatter.
    BACKLOG #15 (урок W28.6): дату ставит ИЗДАТЕЛЬ, не писатель — перезаписываем
    и пустое поле, и «пророческую» дату, угаданную писателем заранее."""
    txt = open(path, encoding='utf-8').read()
    try:
        from zoneinfo import ZoneInfo
        today = datetime.datetime.now(ZoneInfo('Europe/Riga')).date().isoformat()
    except Exception:  # tzdata недоступен — UTC как fallback
        today = datetime.datetime.utcnow().date().isoformat()
    if re.search(r'^published:', txt, re.M):
        txt = re.sub(r'^published:.*$', 'published: ' + today, txt, count=1, flags=re.M)
    else:
        txt, n = re.subn(r'^(type:\s*chapter\s*)$', r'type: chapter\npublished: ' + today,
                         txt, count=1, flags=re.M)
        if n == 0:  # no type line -> put right after 'number:' line
            txt = re.sub(r'^(number:\s*\d+\s*)$', r'\1\npublished: ' + today,
                         txt, count=1, flags=re.M)
    open(path, 'w', encoding='utf-8').write(txt)


def chapter_url(n):
    """Canonical URL of chapter N from its frontmatter (word slugs, 96510be).
    Falls back to None when the file is missing/malformed."""
    p = os.path.join(CHAPS, f'chapter-{n:02d}.md')
    try:
        with open(p, encoding='utf-8') as f:
            for line in f:
                if line.strip() == '---':
                    break
                m = re.match(r'^url:\s*(\S+)', line)
                if m:
                    return m.group(1)
    except OSError:
        pass
    return None


def indexnow_ping(new_nums):
    """Ping IndexNow (Bing/Yandex/Seznam/Naver) about changed URLs.
    Non-fatal on failure. NOTE: Google does NOT consume IndexNow —
    Google discovery = sitemap lastmod + GSC.
    URLs come from the chapters' frontmatter (word slugs) — a stale
    /chapter/N/ path here would make IndexNow crawl 404s (fixed 2026-11-01)."""
    cfgp = os.path.join(BASE, 'config.json')
    if not os.path.exists(cfgp):
        print('indexnow: no config.json — skipped'); return
    cfg = json.load(open(cfgp, encoding='utf-8'))
    dom = (cfg.get('domain') or '').rstrip('/')
    key = cfg.get('indexnow_key') or ''
    if not dom or not key or 'example.org' in dom:
        print('indexnow: skipped (placeholder domain or no key)'); return
    host = dom.split('//', 1)[1]
    urls = [dom + u for u in (chapter_url(n) for n in new_nums) if u]
    urls += [f'{dom}/words/', f'{dom}/']
    body = json.dumps({'host': host, 'key': key,
                       'keyLocation': f'{dom}/{key}.txt', 'urlList': urls}).encode('utf-8')
    req = urllib.request.Request('https://api.indexnow.org/indexnow', data=body,
                                 headers={'Content-Type': 'application/json; charset=utf-8'},
                                 method='POST')
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            print(f'indexnow: HTTP {r.status} — pinged {len(urls)} URLs')
    except Exception as e:
        print('indexnow: ping failed (non-fatal):', e)


def chapter_num(fname):
    m = re.search(r'chapter-(\d+)\.md$', fname)
    return int(m.group(1)) if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, default=1, help='how many chapters to publish now')
    ap.add_argument('--no-git', action='store_true', help='build but skip commit/push')
    ap.add_argument('--no-ping', action='store_true', help='skip IndexNow ping')
    ap.add_argument('--force', action='store_true',
                    help='ignore min_hours_between_chapters pace gate')
    args = ap.parse_args()

    if not os.path.isdir(QUEUE):
        print('queue: no chapters/queue directory — nothing to publish')
        subprocess.run([sys.executable, os.path.join(BASE, 'build_poc.py')])
        return 0
    files = sorted(f for f in os.listdir(QUEUE) if f.endswith('.md'))
    if not files:
        print('queue empty — nothing to publish (writer pipeline refills it)')
        # still build so the Pages artifact exists on scheduled runs
        subprocess.run([sys.executable, os.path.join(BASE, 'build_poc.py')])
        return 0

    ok, why = gate_open(args.force)
    if not ok:
        print(why)
        subprocess.run([sys.executable, os.path.join(BASE, 'build_poc.py')])
        return 0

    batch = files[:max(1, args.n)]
    nums = [chapter_num(f) for f in batch]
    print('publishing:', ', '.join(f'{f} (#{n})' for f, n in zip(batch, nums)))

    # 1) move queue -> chapters (+ real publish-date stamp)
    moved = []
    for f in batch:
        src, dst = os.path.join(QUEUE, f), os.path.join(CHAPS, f)
        shutil.move(src, dst)
        stamp_published(dst)
        moved.append((src, dst))

    # 2) rebuild; validator failure -> rollback
    r = subprocess.run([sys.executable, os.path.join(BASE, 'build_poc.py')],
                       capture_output=True, text=True)
    tail = (r.stdout or '')[-2500:] + (r.stderr or '')[-1500:]
    print(tail)
    if r.returncode != 0:
        print('PUBLISH ABORTED: build/validator failed — rolling back')
        for src, dst in moved:
            shutil.move(dst, src)
        return 1

    # 2.5) green build -> stamp the wave (drives the pace gate on next runs)
    write_state(nums)

    # 3) commit & push
    if args.no_git or not os.path.isdir(os.path.join(BASE, '.git')):
        print('git: skipped (no repo or --no-git)')
    else:
        words = []
        for _, dst in moved:
            mm = re.search(r'^word:\s*(\S+)', open(dst, encoding='utf-8').read(), re.M)
            words.append(mm.group(1) if mm else '?')
        msg = 'publish: ' + ', '.join(f'ch{n} ({w})' for n, w in zip(nums, words)) + ' [skip ci]'
        for cmd in (['git', 'add', '-A', 'chapters', 'state.json'],
                    ['git', 'commit', '-m', msg],
                    ['git', 'push']):
            g = subprocess.run(cmd, cwd=BASE, capture_output=True, text=True)
            print('$', ' '.join(cmd))
            if (g.stdout or '').strip(): print(g.stdout.strip())
            if (g.stderr or '').strip(): print(g.stderr.strip())
            if g.returncode != 0:
                print('git step failed — chapter files stay published locally, push again later')
                return 1
    if not args.no_ping:
        indexnow_ping(nums)
    print('PUBLISH OK:', ', '.join(f'#{n}' for n in nums))
    return 0


if __name__ == '__main__':
    sys.exit(main())
