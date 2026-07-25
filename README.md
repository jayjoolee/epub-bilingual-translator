<p align="center">
  <img src="assets/social-preview.svg" alt="EPUB Bilingual Translator" width="100%">
</p>

<h1 align="center">EPUB Bilingual Translator</h1>

<p align="center">
  Translate long EPUB chapters without losing links, formatting, or the original text.
</p>

<p align="center">
  <a href="README.ko.md">한국어</a> ·
  <a href="#quick-start">Quick start</a> ·
  <a href="#codex-skill">Codex skill</a>
</p>

<p align="center">
  <a href="https://github.com/jayjoolee/epub-bilingual-translator/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/jayjoolee/epub-bilingual-translator/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://github.com/jayjoolee/epub-bilingual-translator/releases/latest"><img alt="Release" src="https://img.shields.io/github/v/release/jayjoolee/epub-bilingual-translator"></a>
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-22c55e"></a>
  <img alt="Python 3.12+" src="https://img.shields.io/badge/python-3.12%2B-3776AB">
</p>

Most EPUB translators fail on long paragraphs or damage inline markup. This tool splits long
content at safe boundaries, balances HTML tags in every request, and builds a new
translation-first bilingual EPUB while leaving the source untouched.

## Quick start

[Install uv](https://docs.astral.sh/uv/getting-started/installation/), then run:

```bash
uvx --from git+https://github.com/jayjoolee/epub-bilingual-translator \
  epub-translate "book.epub" --to ko
```

The result is written next to the source:

```text
book.ko.bilingual.epub
```

For repeated use:

```bash
uv tool install git+https://github.com/jayjoolee/epub-bilingual-translator
epub-translate "book.epub" --to ko
```

## What it preserves

| Capability | Behavior |
|---|---|
| Long paragraphs | Split at sentence or word boundaries |
| Links and emphasis | Inline markup stays balanced |
| Original text | Kept immediately after each translation |
| Chapter and package order | Preserved |
| Images and other assets | Copied without modification |
| Repeat runs | Successful fragments are cached locally |
| Broken translation markup | Bare ampersands and missing closing tags are repaired |
| Source EPUB | Never overwritten |

The translator skips code, preformatted content, scripts, styles, MathML, SVG, and content
marked `translate="no"` or `notranslate`.

## Real-world verification

The translation pipeline completed a full-length EPUB with:

| Check | Result |
|---|---:|
| Eligible blocks translated | 1,041 |
| Empty translations | 0 |
| Original-text mismatches | 0 |
| Links preserved in both copies | 68 / 68 |
| EPUB archive integrity | Passed |

## Commands

```bash
epub-translate --help
epub-translate "book.epub" --to ko
epub-translate "book.epub" --source en --to ko
epub-translate "book.epub" --to ja --output "book.ja.bilingual.epub"
```

## Codex skill

The repository includes an optional Codex skill. Install it with Codex's bundled skill installer:

```bash
python ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo jayjoolee/epub-bilingual-translator \
  --path skill/translate-epub
```

Then ask Codex:

```text
Use $translate-epub to translate this DRM-free EPUB into Korean.
```

The skill becomes available on the next Codex turn.

## Privacy, service limits, and DRM

- Book text is sent to Google's web translation service for translation.
- The keyless `client=gtx` endpoint is unofficial. It is not unlimited or guaranteed and may be
  throttled, blocked, or changed by Google.
- The local cache is stored under
  `~/.cache/epub-bilingual-translator/google-web-gtx-v1/`.
- Encrypted content and DRM-protected EPUBs are refused. Standard EPUB font obfuscation is
  preserved without deobfuscating it. This project does not remove or bypass DRM.
- Only translate books you have the right to process.

For guaranteed service or commercial workloads, use a supported paid translation API instead
of the bundled experimental provider.

## Development

```bash
git clone https://github.com/jayjoolee/epub-bilingual-translator
cd epub-bilingual-translator
uv sync --dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
```

## License

[MIT](LICENSE)
