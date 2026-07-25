<p align="center">
  <img src="assets/social-preview.svg" alt="EPUB Bilingual Translator" width="100%">
</p>

<h1 align="center">EPUB Bilingual Translator</h1>

<p align="center">
  긴 문단도 빠뜨리지 않고, 링크·서식·원문을 보존하는 이중언어 EPUB 번역기
</p>

<p align="center">
  <a href="README.md">English</a> ·
  <a href="#빠른-시작">빠른 시작</a> ·
  <a href="#codex-스킬">Codex 스킬</a>
</p>

일반적인 EPUB 번역기는 문단이 길면 번역을 건너뛰거나 링크와 강조 서식을
망가뜨리기도 합니다. 이 도구는 긴 문단을 안전한 경계에서 나누고, 각 요청의
HTML 태그를 균형 있게 유지한 뒤, 번역문과 원문이 함께 있는 새 EPUB을 만듭니다.
원본 파일은 변경하지 않습니다.

## 빠른 시작

macOS 또는 Linux에서
[uv를 설치](https://docs.astral.sh/uv/getting-started/installation/)한 다음 실행하세요.

```bash
uvx --from git+https://github.com/jayjoolee/epub-bilingual-translator \
  epub-translate "책.epub" --to ko
```

원본 옆에 결과가 생성됩니다.

```text
책.ko.bilingual.epub
```

자주 사용한다면:

```bash
uv tool install git+https://github.com/jayjoolee/epub-bilingual-translator
epub-translate "책.epub" --to ko
```

## 보존하는 항목

| 기능 | 처리 방식 |
|---|---|
| 긴 문단 | 문장 또는 단어 경계에서 분할 |
| 링크와 강조 | 인라인 태그의 균형 유지 |
| 원문 | 각 번역문 바로 뒤에 보존 |
| 장과 패키지 순서 | 그대로 유지 |
| 이미지와 기타 파일 | 수정 없이 복사 |
| 재실행 | 성공한 번역 조각을 로컬에 캐시 |
| 잘못된 번역 마크업 | 단독 `&`와 누락된 닫는 태그 복구 |
| 원본 EPUB | 절대 덮어쓰지 않음 |

코드, 미리 서식이 지정된 텍스트, 스크립트, 스타일, MathML, SVG,
`translate="no"` 또는 `notranslate`로 표시된 내용은 번역하지 않습니다.

## 안전 경계

번역 전에 EPUB mimetype과 container를 검사하고, 암호화된 ZIP 멤버, 위험한 경로,
중복 파일, 비정상적인 압축률을 거부합니다. 한 번에 처리하는 범위는 멤버 10,000개,
멤버당 512 MiB, 전체 압축 해제 크기 2 GiB, XHTML 문서당 16 MiB로 제한합니다.
고정 파라미터와 퍼센트 인코딩을 포함한 공급자 요청 URL 전체는 32 KiB, 응답
스트림은 1 MiB로 제한합니다. 비공개 로컬 캐시는 경로에 있는 심볼릭 링크를
거부하고, 사용자 소유로 확인한 디렉터리에 파일 작업을 고정합니다. 결과는
원자적으로 생성하며, 실행 중 같은 경로에 생긴 파일도 덮어쓰지 않습니다.

## 실제 장편 검증

장편 EPUB 전체 번역에서 다음 결과를 확인했습니다.

| 확인 항목 | 결과 |
|---|---:|
| 번역한 대상 블록 | 1,041개 |
| 빈 번역문 | 0개 |
| 원문 불일치 | 0개 |
| 양쪽 사본에서 보존한 링크 | 68 / 68 |
| EPUB 압축 구조 검사 | 통과 |

## 명령어

```bash
epub-translate --help
epub-translate "책.epub" --to ko
epub-translate "책.epub" --source en --to ko
epub-translate "책.epub" --to ja --output "책.ja.bilingual.epub"
```

## Codex 스킬

선택적으로 Codex 스킬을 설치할 수 있습니다.

```bash
python ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo jayjoolee/epub-bilingual-translator \
  --path skill/translate-epub
```

다음 Codex 턴부터 이렇게 요청하세요.

```text
$translate-epub으로 이 DRM 없는 EPUB을 한국어 병기본으로 번역해줘.
```

## 개인정보, 서비스 제한 및 DRM

- 번역할 책의 텍스트는 Google 웹 번역 서비스로 전송됩니다.
- 키가 필요 없는 `client=gtx` 엔드포인트는 비공식입니다. 무제한이나 안정적인
  서비스가 아니며 Google이 제한하거나 변경할 수 있습니다.
- 로컬 캐시는 `~/.cache/epub-bilingual-translator/google-web-gtx-v1/` 아래에
  저장됩니다.
- 암호화된 콘텐츠와 DRM으로 보호된 EPUB은 거부합니다. 표준 EPUB 글꼴 난독화는
  해제하지 않고 그대로 보존합니다. DRM 제거·우회 기능은 없습니다.
- 처리할 권한이 있는 책만 번역하세요.

안정성이 보장되어야 하거나 상업적으로 사용한다면 공식 유료 번역 API를 사용하세요.

## 개발

```bash
git clone https://github.com/jayjoolee/epub-bilingual-translator
cd epub-bilingual-translator
uv sync --dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
```

## 라이선스

[MIT](LICENSE)
