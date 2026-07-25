---
name: translate-epub
description: Translate DRM-free EPUB books into Korean or another target language and create a translation-first bilingual copy. Use when the user asks to translate an .epub file, retain the original beside the translation, or handle long paragraphs, links, emphasis, and other inline markup without missing content.
---

# Translate EPUB

Create a new bilingual EPUB without modifying the source file.

## Workflow

1. Resolve the exact input `.epub` path and choose a new output path.
2. Confirm the user has a DRM-free EPUB. Never bypass DRM.
3. Run the published CLI:

```bash
uvx --from git+https://github.com/jayjoolee/epub-bilingual-translator@v0.1.0 \
  epub-translate "/absolute/path/book.epub" \
  --to ko \
  --output "/absolute/path/book.ko.bilingual.epub"
```

4. Verify the created file:

```bash
unzip -t "/absolute/path/book.ko.bilingual.epub"
```

5. Report the output path, translated block count, fragment count, and EPUB integrity result.

## Output rules

- Put the translation first and the unchanged original block immediately after it.
- Preserve chapter order, inline emphasis, links, images, and EPUB package files.
- Skip code, preformatted content, scripts, styles, MathML, SVG, and blocks marked
  `translate="no"` or `notranslate`.
- Refuse to overwrite the source or an existing output file.
- Preserve the local cache so reruns do not resend successful fragments.

## Service boundary

The bundled provider uses Google's unofficial keyless `client=gtx` web endpoint. It is not
unlimited or guaranteed. Book text is sent to Google. If the endpoint fails, preserve the source
and cache, remove temporary output, and explain that a supported paid API is required for
guaranteed service.
