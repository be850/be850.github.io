# Book850 — a novel that teaches itself English

Chapter N introduces word N of Ogden's Basic English 850 and uses **only** the
words from chapters 1..N. One story, 850 chapters. English explained in simple
English — no translations, no dictionaries, the text grows exactly as fast as
the reader's vocabulary.

The live copy is served on GitHub Pages (https://be850.sok.lv).

## How the machine works

- `chapters/` — published chapters (frontmatter v2: `number`, `word`, `title`,
  `faq-q`/`faq-a` pairs, `video`, `video_title`, `video_duration`…).
- `chapters/queue/` — approved fuel. Every hour a GitHub Actions job
  (`.github/workflows/publish.yml`) moves the next chapter out of the queue,
  runs the **SPIRAL VALIDATOR**, rebuilds the whole static site, commits the
  new state and deploys to Pages.
- The validator aborts the build if chapter N uses any word outside 1..N
  (plus a fixed set of function words) or fails to introduce its own word —
  a failed build publishes nothing.
- The pace gate `min_hours_between_chapters` in `config.json` keeps the drip
  human-sane; `workflow_dispatch` with `force: true` overrides it for
  calibration waves.
- `build_poc.py` — validator + static generator: chapter pages, word
  directory, cover, `sitemap.xml`, `rss.xml`, `robots.txt`, `llms.txt`,
  `404.html`, and JSON-LD (Book, WebSite, ItemList, Article, FAQPage,
  DefinedTerm, BreadcrumbList, VideoObject once real upload dates arrive).
- `publish_next.py` — wave publisher: `--n N`, `--force`, `--no-git`.

## Run locally

```bash
python3 build_poc.py                 # validate + build into site/public
python3 publish_next.py --n 1 --no-git
```

Static site, no external dependencies, Python 3 standard library only.

© KraSer. All rights reserved.
