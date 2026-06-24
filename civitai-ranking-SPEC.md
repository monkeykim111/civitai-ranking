# Civitai Weekly Digest — 작업 명세서 (Claude Code용)

> 이 문서를 Claude Code 세션에 붙여넣고 작업을 지시한다.  
> **중요: 한 번에 다 만들지 말 것. Phase 1만 구현하고 멈춰서 결과를 보고한 뒤, 사람이 확인하면 다음 Phase로 넘어간다.**

---

## 0. 작업 환경

- **실제 주간 실행**: Anthropic 클라우드 루틴이 이 GitHub repo를 클론해서 실행.
- 즉, 박스는 코드를 짜고 돌려보는 작업대이고, 운영 실행은 클라우드다.
- 운영 루틴은 **일요일 05:00 (KST)** 기준으로 설정한다.
- Routine은 클라우드에서 실행되므로 박스가 꺼져 있어도 실행되는 것을 목표로 한다.

---

## 1. 무엇을 만드나

매주 **일요일 05:00 (KST)**, Civitai에서 **SDXL 계열 + Z-Image Turbo 체크포인트** 중
**Overwhelmingly Positive(OP)에 가까운 고평가 모델**을 가져와서:

1. 랭킹을 매기고
2. 지난주 대비 **신규(🆕)** 를 표시하고
3. 좋아요·다운로드의 **주간 상승분(▲)** 을 계산하고
4. 모델 생성일 / 최신 버전 공개일을 같이 보여주고
5. 각 모델명을 클릭하면 Civitai 페이지로 바로 들어갈 수 있게 링크를 걸고
6. Claude가 **매직 서비스 관점의 추천**을 붙이고
7. **Slack**으로 보낸다.

> 매직 서비스 맥락: 웹툰/만화/일러스트 그림체 위주의 이미지 생성 서비스  
> 3D는 ControlNet i2i, editor는 t2i/i2i. 실사 모델도 일부 쓰지만 주력은 일러/만화체.

---

## 2. 아키텍처 — 역할 분담

```text
            ┌─────────────────────── Cloud Routine (오케스트레이터) ───────────────────────┐
            │                                                                              │
  매주 일요일 05:00 KST                                                                     │
            │                                                                              │
            ▼                                                                              │
   1. civitai_digest.py 실행  ──────────────► 결정적 엔진 (LLM 안 씀)                         │
            │                                   - Civitai API 수집                         │
            │                                   - OP 필터                                  │
            │                                   - state.json 읽고 델타·랭킹·신규 계산         │
            │                                   - 모델 생성일/최신버전일 추출                │
            │                                   - state.json / history 갱신                │
            │                                   - digest.json 출력                         │
            ▼                                                                              │
   2. digest.json 을 Claude가 읽음 ──────────► LLM은 "판단"만                                │
            │                                   - 그림체 적합성                             │
            │                                   - 매직 서비스 관점 추천 3개 작성             │
            │                                   - recommendation.md 생성                   │
            ▼                                                                              │
   3. send_slack.py 실행 ───────────────────► 결정적 발송                                   │
            │                                   - digest.json + recommendation.md           │
            │                                   - Slack Block Kit 구성                     │
            │                                   - webhook POST                             │
            └──────────────────────────────────────────────────────────────────────────────┘
```

- **`civitai_digest.py`**: 수집·필터·계산·랭킹·신규판별·상태갱신. **숫자는 전부 여기서 계산한다.**
- **`send_slack.py`**: Slack 발송. Block Kit 구성도 여기서 한다.
- **Claude (LLM)**: `digest.json`을 보고 **추천 코멘트 텍스트만** 생성한다. 숫자 계산은 하지 않는다.
- **GitHub repo**: 상태(`state.json`) + 누적 기록(`history/`)을 저장한다.

이유: 수집·계산·발송은 매주 똑같이 재현돼야 하므로 스크립트가 한다. LLM은 사람이 잘 못하는 “그림체 적합성 판단”과 “추천 코멘트”에만 쓴다.

---

## 3. 확정된 설계 결정

### 3.1 수집 대상

- **base models**:

```python
TARGET_BASE_MODELS = [
    "SDXL 1.0",
    "SDXL Turbo",
    "SDXL Lightning",
    "Pony",
    "Illustrious",
    "NoobAI",
    "ZImageTurbo",
]
```

- 이유: Illustrious·Pony·NoobAI는 SDXL 아키텍처 파생이고 웹툰/일러 주력이라 반드시 포함한다.
- **types**: `Checkpoint`만 대상으로 한다. LoRA는 추후 옵션.
- **NSFW 제외**: 학교 대상 판매 맥락이 있으므로 `nsfw=false`로 수집한다.

---

### 3.2 Civitai API 호출

- 엔드포인트:

```text
GET https://civitai.com/api/v1/models
```

- 기본 쿼리 파라미터:

```text
types=Checkpoint
baseModels=<해당 base model>
sort=Highest Rated
period=AllTime
limit=100
nsfw=false
```

- 인증:
  - public 호출 가능.
  - `CIVITAI_TOKEN`이 있으면 `Authorization: Bearer <token>` 헤더를 추가한다.
  - 토큰은 rate limit 여유를 위해 선택적으로 사용한다.

- **사이트 크롤링이 아니라 공식 REST API 호출로 처리한다.**

---

### 3.3 Pagination / Cursor 처리

`limit=100`으로 한 번만 호출하면 인기 모델이 많은 계열에서 후보를 놓칠 수 있다.  
따라서 `metadata.nextCursor` 또는 `metadata.nextPage`가 있으면 다음 페이지를 이어서 호출한다.

상수:

```python
FETCH_LIMIT = 100
MAX_PAGES_PER_BASE_MODEL = 10
```

규칙:

- base model 하나당 최대 `MAX_PAGES_PER_BASE_MODEL` 페이지만 수집한다.
- 응답에 `metadata.nextCursor`가 있으면 다음 요청에 `cursor=<nextCursor>`를 붙인다.
- `metadata.nextCursor`가 없고 `metadata.nextPage`가 있으면 `nextPage` URL을 따라간다.
- 둘 다 없으면 해당 base model 수집을 종료한다.
- Phase 1 출력에는 base model별로 다음 값을 함께 보여준다.

```text
base_model | fetched_count | op_count | page_count | truncated
```

`truncated=true`는 최대 페이지 제한 때문에 더 가져올 수 있었지만 멈췄다는 뜻이다.

---

### 3.4 OP(Overwhelmingly Positive) 판정 — 직접 계산

API는 “Overwhelmingly Positive” 라벨을 직접 주지 않을 수 있으므로, 최신 버전(`modelVersions[0]`)의 통계로 직접 계산한다.

```text
total = thumbs_up + thumbs_down
ratio = thumbs_up / total

OP 조건:
ratio >= 0.95 AND total >= 500
```

- `total == 0`이면 제외한다.
- `sort=Highest Rated` 정렬을 이미 걸기 때문에 Civitai 평점 정렬 + 우리 임계값이 이중으로 작동한다.
- 이 기준은 Civitai의 공식 라벨과 100% 동일하다고 가정하지 않는다.
- 목적은 **매직 서비스에서 볼 만한 고평가 모델 후보를 안정적으로 추리는 것**이다.
- `OP_MIN_RATIO=0.95`, `OP_MIN_REVIEWS=500`은 1주차 캘리브레이션 대상이다.

---

### 3.5 모델 날짜 정보 저장

각 후보 모델에는 생성일과 최신 버전 공개일을 함께 저장한다.

필드:

```text
model_created_at      = model.createdAt
model_published_at    = model.publishedAt 또는 null
version_created_at    = modelVersions[0].createdAt
version_published_at  = modelVersions[0].publishedAt 또는 null
```

규칙:

- API 응답에 없는 날짜 필드는 `null`로 둔다.
- Slack에는 너무 길게 쓰지 않고 아래처럼 표시한다.

```text
생성: 2024-03-12 · 최신버전: 2025-11-04
```

- “최신버전” 날짜는 우선순위를 둔다.

```text
version_published_at > version_created_at > model_published_at > model_created_at
```

이유:

- 모델 생성일은 “오래 검증된 모델인지”를 보는 데 좋다.
- 최신 버전 공개일은 “최근에 관리/업데이트되는 모델인지”를 보는 데 좋다.

---

### 3.6 모델 링크

각 후보 모델에는 Civitai 페이지 링크를 포함한다.

URL 형식:

```text
https://civitai.com/models/{model_id}?modelVersionId={version_id}
```

Slack에서는 모델명을 클릭 가능한 링크로 표시한다.

Slack 링크 형식:

```text
<https://civitai.com/models/827184?modelVersionId=2514310|WAI-illustrious-SDXL>
```

Slack 표시 예:

```text
1. <https://civitai.com/models/827184?modelVersionId=2514310|WAI-illustrious-SDXL>
   Illustrious · 👍 13,850 · DL 215,627
   생성: 2024-03-12 · 최신버전: 2025-11-04
```

---

### 3.7 신규 판별

- 신규 판별 키는 **`version_id`** 로 한다.
- `version_id = modelVersions[0].id`
- 기존 모델이 새 버전으로 OP 기준에 진입해도 🆕로 잡는다.

이유:

- 모델 자체는 오래되었어도 최신 버전이 새로 올라오면 실무적으로 다시 볼 가치가 있다.

---

### 3.8 랭킹 — 3 섹션

| 섹션 | 정렬 | 가드 | 기본 개수 |
|---|---|---|---|
| 🏆 누적 TOP | `thumbsUpCount` 절댓값 내림차순 | 없음 | 12 (`N_CUMULATIVE`) |
| 📈 다운로드 급상승 | 다운로드 증가율(%) 내림차순 | 주간 증가량 ≥ `GUARD_DL` | 8 (`N_DL_SURGE`) |
| ❤️ 좋아요 급상승 | thumbsUp 증가율(%) 내림차순 | 주간 증가량 ≥ `GUARD_UP` | 8 (`N_UP_SURGE`) |

상수:

```python
N_CUMULATIVE = 12
N_DL_SURGE = 8
N_UP_SURGE = 8
GUARD_DL = 500
GUARD_UP = 50
```

규칙:

- 급상승은 “증가율 순위 + 최소 증가량 가드” 방식으로 정렬한다.
- 작은 수의 비율 뻥튀기를 막기 위해 `GUARD_DL`, `GUARD_UP`을 둔다.
- 같은 모델이 여러 섹션에 중복 등장하는 것은 일단 허용한다.
- 각 섹션 숫자는 1~2주차 결과를 보고 조정한다.

---

### 3.9 첫 실행(baseline) 처리

`state.json`이 없으면 그 주는 **baseline**으로 처리한다.

baseline 규칙:

- 🆕 표시 없음.
- ▲ 상승분 표시 없음.
- 절댓값만 보여준다.
- 전체 후보를 `seen`에 기록한다.
- 둘째 주부터 실제 신규/상승분 비교를 시작한다.

---

### 3.10 새 base model 카테고리 감지

매주 base model enum 목록을 확인해, `TARGET_BASE_MODELS`에 없는 새 항목이 있으면 `digest.json`의 `new_base_models`에 담는다.

규칙:

- Slack에 ⚠️ 한 줄로 표시한다.
- 자동으로 수집 대상에 추가하지 않는다.
- SDXL 계열인지 여부는 사람이 보고 결정한다.

Slack 예:

```text
⚠️ 새 base model 후보 발견: Qwen-Image, Flux.2
```

---

## 4. 데이터 구조

### 4.1 `state.json` — 비교용 작업 기억

```json
{
  "last_run": "2026-06-28",
  "seen": {
    "2514310": {
      "model_id": 827184,
      "version_id": 2514310,
      "name": "WAI-illustrious-SDXL",
      "base_model": "Illustrious",
      "first_seen": "2026-06-21",
      "last_thumbs_up": 12610,
      "last_downloads": 197325,
      "model_created_at": "2024-03-12T00:00:00.000Z",
      "version_published_at": "2025-11-04T00:00:00.000Z",
      "url": "https://civitai.com/models/827184?modelVersionId=2514310"
    }
  }
}
```

---

### 4.2 `history/2026-06-28.json` — 주간 스냅샷

```json
{
  "date": "2026-06-28",
  "is_baseline": false,
  "op_count_total": 137,
  "ranked": [
    {
      "rank": 1,
      "model_id": 827184,
      "version_id": 2514310,
      "name": "WAI-illustrious-SDXL",
      "base_model": "Illustrious",
      "thumbs_up": 13850,
      "thumbs_down": 120,
      "downloads": 215627,
      "thumbs_up_delta": 1240,
      "thumbs_up_pct": 9.8,
      "downloads_delta": 18302,
      "downloads_pct": 9.3,
      "is_new": false,
      "model_created_at": "2024-03-12T00:00:00.000Z",
      "model_published_at": null,
      "version_created_at": "2025-11-04T00:00:00.000Z",
      "version_published_at": "2025-11-04T00:00:00.000Z",
      "url": "https://civitai.com/models/827184?modelVersionId=2514310",
      "creator": "example_creator",
      "tags": ["anime", "illustration"],
      "description_short": "optional short description"
    }
  ]
}
```

---

### 4.3 `digest.json` — Slack/Claude 입력

```json
{
  "date": "2026-06-28",
  "is_baseline": false,
  "op_count_total": 137,
  "new_base_models": [],
  "sections": {
    "cumulative": [],
    "download_surge": [],
    "thumbs_surge": []
  }
}
```

`sections` 안의 항목은 `history.ranked` 항목과 같은 형태를 사용한다.

Claude가 판단하기 쉽도록 가능하면 아래 메타데이터도 포함한다.

```text
creator
tags
description_short
model_created_at
version_published_at
url
```

---

## 5. Slack 메시지 형식

### 5.1 전체 구성

Slack 메시지는 너무 길지 않게 아래 구조로 보낸다.

```text
Civitai Weekly Digest — 2026-06-28
OP 후보: 137개

🏆 누적 TOP
1. <url|모델명>
   base · 👍 total · DL total
   생성: YYYY-MM-DD · 최신버전: YYYY-MM-DD

📈 다운로드 급상승
...

❤️ 좋아요 급상승
...

⭐ Claude 추천
1. 모델명 — 추천 이유
2. 모델명 — 추천 이유
3. 모델명 — 추천 이유
```

---

### 5.2 모델 한 줄 표시 규칙

기본 표시:

```text
1. <{url}|{name}> {new_badge}
   {base_model} · 👍 {thumbs_up} ({thumbs_up_delta_text}) · DL {downloads} ({downloads_delta_text})
   생성: {model_created_date} · 최신버전: {version_date}
```

예:

```text
1. <https://civitai.com/models/827184?modelVersionId=2514310|WAI-illustrious-SDXL>
   Illustrious · 👍 13,850 (+1,240, +9.8%) · DL 215,627 (+18,302, +9.3%)
   생성: 2024-03-12 · 최신버전: 2025-11-04
```

baseline이면 상승분을 생략한다.

```text
1. <https://civitai.com/models/827184?modelVersionId=2514310|WAI-illustrious-SDXL>
   Illustrious · 👍 13,850 · DL 215,627
   생성: 2024-03-12 · 최신버전: 2025-11-04
```

신규 모델이면 모델명 옆에 `🆕`를 붙인다.

---

## 6. 코딩 컨벤션

- 설명·주석은 **한국어**.
- 식별자·파일명·커밋 메시지는 **영어**.
- 의존성 최소화: 표준 라이브러리 우선, 필요하면 `requests` 정도만 사용한다.
- `requirements.txt`에 의존성을 명시한다.
- 모든 임계값·개수·가드는 파일 상단 상수 블록으로 분리한다.
- 시크릿은 환경변수로만 받는다.

환경변수:

```text
SLACK_WEBHOOK_URL  # 필수
CIVITAI_TOKEN      # 선택
```

금지:

- Slack webhook URL 하드코딩 금지.
- Civitai token 하드코딩 금지.
- 숫자 랭킹/델타 계산을 Claude에게 맡기지 말 것.
- Phase를 건너뛰지 말 것.

커밋 메시지 예:

```text
feat: phase1 collect and filter op models
feat: phase2 build digest sections
feat: phase3 send slack digest
```

---

## 7. Phase별 상세

## Phase 1 — 수집 + OP 필터

### 목표

“OP 기준을 통과하는 모델이 총 몇 개 나오는지”를 확인한다.  
이 결과를 보고 `OP_MIN_REVIEWS`, 섹션 개수, 가드 값을 캘리브레이션한다.

### 구현

repo 스켈레톤:

```text
civitai_digest.py
requirements.txt
README.md
```

상수:

```python
TARGET_BASE_MODELS = [
    "SDXL 1.0",
    "SDXL Turbo",
    "SDXL Lightning",
    "Pony",
    "Illustrious",
    "NoobAI",
    "ZImageTurbo",
]
TYPES = ["Checkpoint"]
FETCH_LIMIT = 100
MAX_PAGES_PER_BASE_MODEL = 10
OP_MIN_RATIO = 0.95
OP_MIN_REVIEWS = 500
```

함수:

```python
fetch_models(base_model: str, token: str | None) -> tuple[list[dict], dict]
latest_version(model: dict) -> dict | None
latest_stats(model: dict) -> tuple[int, int, int]
extract_dates(model: dict) -> dict
build_model_url(model_id: int, version_id: int) -> str
is_op(up: int, down: int) -> bool
collect_candidates(token: str | None) -> list[dict]
```

`fetch_models` 규칙:

- base model별로 Civitai API를 호출한다.
- `metadata.nextCursor` 또는 `metadata.nextPage`가 있으면 최대 `MAX_PAGES_PER_BASE_MODEL`까지 이어서 호출한다.
- 간단한 재시도/백오프를 넣는다.
- 페이지 수, 수집 개수, truncated 여부를 함께 기록한다.

`collect_candidates` 규칙:

- 전체 base model을 순회한다.
- `version_id` 기준으로 중복 제거한다.
- OP 기준 통과 모델만 남긴다.
- 각 후보에 아래 필드를 포함한다.

```text
model_id
version_id
name
base_model
thumbs_up
thumbs_down
downloads
model_created_at
model_published_at
version_created_at
version_published_at
url
creator
tags
description_short
```

### `__main__` 출력

`python civitai_digest.py` 실행 시 다음을 출력한다.

```text
base_model        fetched_count   op_count   page_count   truncated
SDXL 1.0          300             21         3            false
Pony              1000            44         10           true
Illustrious       700             38         7            false
...
TOTAL OP: 137
```

그리고 후보 전체를 `candidates_debug.json`으로 덤프한다.

### 검증 게이트

- `python civitai_digest.py` 실행.
- base model별 OP 개수와 총합이 출력되는지 확인.
- `candidates_debug.json`에서 2~3개를 골라 실제 Civitai 페이지와 대조한다.
- 모델명 링크가 정상적으로 열리는지 확인한다.
- 날짜 필드가 들어오는지 확인한다.
- 여기서 `OP_MIN_REVIEWS`, `OP_MIN_RATIO`, `MAX_PAGES_PER_BASE_MODEL` 조정 여부를 결정한다.

### 하지 말 것

- state/델타 구현 금지.
- digest.json 구현 금지.
- Slack 발송 금지.
- Claude 추천 코멘트 생성 금지.

---

## Phase 2 — 상태·델타·랭킹·3섹션

### 목표

지난주 대비 신규·상승분을 계산하고 `digest.json`을 만든다.

### 구현

상수 추가:

```python
N_CUMULATIVE = 12
N_DL_SURGE = 8
N_UP_SURGE = 8
GUARD_DL = 500
GUARD_UP = 50
STATE_PATH = "state.json"
DIGEST_PATH = "digest.json"
HISTORY_DIR = "history"
```

함수:

```python
load_state(path: str) -> tuple[dict, bool]
save_state(path: str, state: dict) -> None
compute_deltas(candidate: dict, state: dict, is_baseline: bool) -> dict
build_sections(candidates: list[dict], is_baseline: bool) -> dict
update_state(state: dict, candidates: list[dict], today: str) -> dict
write_history(today: str, ranked: list[dict], is_baseline: bool) -> None
write_digest(today: str, sections: dict, is_baseline: bool, new_base_models: list[str]) -> None
```

baseline 규칙:

- `state.json`이 없으면 `is_baseline=true`.
- baseline에서는 `is_new=false`, delta는 `null`.
- 전체 후보를 `seen`에 저장한다.

신규 규칙:

- `version_id`가 `state.seen`에 없으면 `is_new=true`.
- 신규 모델은 delta를 계산하지 않는다.

델타 규칙:

```text
thumbs_up_delta = current_up - previous_up
downloads_delta = current_downloads - previous_downloads
thumbs_up_pct = thumbs_up_delta / previous_up * 100
downloads_pct = downloads_delta / previous_downloads * 100
```

분모가 0이면 pct는 `null`로 둔다.

### 검증 게이트

- 가짜 `state.json`을 만들어 실행한다.
- 델타·증가율·신규 판별이 손계산과 맞는지 확인한다.
- 두 번 연속 실행해서 둘째 실행에서 신규가 0이 되는지 확인한다.
- `history/<date>.json`과 `digest.json`이 올바르게 생성되는지 확인한다.

---

## Phase 3 — Slack 발송

### 목표

`digest.json`과 `recommendation.md`를 보기 좋은 Slack 메시지로 발송한다.

### 구현

파일:

```text
send_slack.py
```

입력:

```bash
python send_slack.py digest.json recommendation.md
```

중요:

- Claude 추천 코멘트는 CLI 문자열 인자로 넘기지 않는다.
- 따옴표, 줄바꿈, 이모지 문제를 피하기 위해 `recommendation.md` 파일로 전달한다.

`send_slack.py` 기능:

- `digest.json`을 읽는다.
- `recommendation.md`가 있으면 읽어서 `⭐ Claude 추천` 섹션으로 붙인다.
- Slack Block Kit으로 3섹션을 구성한다.
- 각 모델명은 `<url|모델명>` 형식으로 클릭 가능하게 만든다.
- 숫자, ▲ 상승분, 🆕 신규 표시를 포함한다.
- baseline이면 ▲/🆕를 생략한다.
- `new_base_models`가 있으면 ⚠️ 라인을 추가한다.
- `SLACK_WEBHOOK_URL`로 POST한다.

### 검증 게이트

- 테스트 채널로 발송한다.
- 모델명이 클릭 가능한 링크로 보이는지 확인한다.
- 날짜가 잘 보이는지 확인한다.
- baseline 메시지에서 ▲/🆕가 빠지는지 확인한다.
- 일반 메시지에서 신규/상승분이 잘 보이는지 확인한다.

---

## Phase 4 — repo 푸시 + Claude Routine 연결

### 목표

end-to-end를 클라우드 Routine에서 한 번 돌린다.

### 작업

1. repo를 GitHub private repo로 push한다.
2. Claude Code Routine을 만든다.
3. 이 repo를 연결한다.
4. 트리거를 설정한다.

```text
weekly / Sunday / 05:00 / Asia/Seoul 또는 KST
```

5. Routine environment를 설정한다.

환경변수:

```text
SLACK_WEBHOOK_URL
CIVITAI_TOKEN(optional)
```

Network access:

```text
civitai.com
developer.civitai.com
hooks.slack.com
api.slack.com(optional)
github.com
```

6. `state.json`과 `history/`를 다음 주에도 유지해야 하므로 push 전략을 정한다.

추천:

- 전용 private repo라면 **Allow unrestricted branch pushes**를 켠다.
- Routine이 default branch에 `state.json`과 `history/` 변경사항을 직접 push하게 한다.
- 이 권한을 켜기 싫으면 state 저장소를 GitHub가 아니라 Google Drive/S3 등으로 분리한다. 단, 구현 복잡도가 올라간다.

---

### Routine 프롬프트 초안

```text
이 repo에서 아래 순서대로 작업한다.

1. python civitai_digest.py 를 실행한다.
2. 생성된 digest.json 을 읽는다.
3. 각 섹션의 모델을 매직 서비스 관점에서 검토한다.
   - 주력은 웹툰/만화/일러스트 그림체다.
   - 실사 모델은 일부 쓰지만 우선순위는 낮다.
   - 학교 대상 서비스 맥락상 NSFW 가능성이 높은 모델은 추천에서 보수적으로 본다.
4. 이번 주 추천 3개와 각 모델별 한 줄 코멘트를 recommendation.md 로 작성한다.
5. python send_slack.py digest.json recommendation.md 를 실행해 Slack으로 보낸다.
6. 변경된 state.json, digest.json, history/ 를 커밋한다.
7. default branch에 push한다.

주의:
- 숫자 계산은 직접 하지 않는다. civitai_digest.py 결과를 그대로 사용한다.
- Phase별 구현 중에는 요청된 Phase 밖의 작업을 하지 않는다.
- Slack 전송 실패 시 에러를 보고하고 state 커밋은 하지 않는다.
```

### 검증 게이트

- 수동 트리거로 한 번 실행한다.
- Slack 메시지가 정상적으로 오는지 확인한다.
- 모델명 링크가 정상적으로 열리는지 확인한다.
- `state.json`과 `history/`가 repo에 커밋/푸시되는지 확인한다.
- 다음 실행에서 baseline이 아니라 비교 모드로 동작할 수 있는지 확인한다.

---

## Phase 5 — 운영·캘리브레이션

첫 일요일:

- baseline.
- 🆕/▲ 없음.
- OP 후보 수와 섹션 길이를 확인한다.

둘째 주:

- 실제 신규/상승분 비교 시작.
- `GUARD_DL`, `GUARD_UP`, `OP_MIN_REVIEWS`, `N_*` 값 조정.

운영 중 확인할 것:

- OP 후보가 너무 많으면 `OP_MIN_REVIEWS`를 올린다.
- OP 후보가 너무 적으면 `OP_MIN_REVIEWS`를 낮춘다.
- 급상승 섹션이 너무 빈약하면 `GUARD_DL`, `GUARD_UP`을 낮춘다.
- 급상승 섹션에 품질 낮은 모델이 많으면 가드를 올린다.
- 특정 base model이 과도하게 많으면 base model별 quota를 추가할지 검토한다.

`history/`에 매주 쌓이는 데이터는 추후 추세 분석 자산으로 사용한다.

---

## 8. 지금 당장 Claude Code에 시킬 것

아래 문구를 Claude Code에 그대로 붙여넣는다.

```text
위 명세의 Phase 1만 구현해라.

구현 범위:
- repo 스켈레톤 생성
- civitai_digest.py 작성
- Civitai API 수집
- cursor/pagination 처리
- OP 필터
- 모델 생성일/최신버전일 추출
- Civitai 모델 링크 생성
- base model별 fetched_count / op_count / page_count / truncated 출력
- candidates_debug.json 생성

완료 후:
- base model별 OP 개수 표를 보여줘라.
- candidates_debug.json에 어떤 필드가 들어갔는지 요약해라.
- Phase 2로 넘어가지 마라.
- state.json, digest.json, Slack 발송, Claude 추천 코멘트는 아직 구현하지 마라.
```

---

## 9. 체크리스트

### Phase 1 완료 체크

- [ ] `python civitai_digest.py` 실행 가능
- [ ] base model별 `fetched_count` 출력
- [ ] base model별 `op_count` 출력
- [ ] `page_count` 출력
- [ ] `truncated` 출력
- [ ] `candidates_debug.json` 생성
- [ ] 각 후보에 `url` 포함
- [ ] 각 후보에 `model_created_at` 포함
- [ ] 각 후보에 `version_created_at` 또는 `version_published_at` 포함
- [ ] 실제 Civitai 페이지에서 2~3개 대조 확인

### Phase 2 완료 체크

- [ ] baseline 동작 확인
- [ ] `state.json` 생성 확인
- [ ] 두 번째 실행에서 신규 0 확인
- [ ] 가짜 state로 델타 손계산 검증
- [ ] `digest.json` 생성 확인
- [ ] `history/<date>.json` 생성 확인

### Phase 3 완료 체크

- [ ] Slack 테스트 채널 발송 성공
- [ ] 모델명 클릭 링크 정상
- [ ] 날짜 표시 정상
- [ ] baseline에서 ▲/🆕 생략
- [ ] 일반 실행에서 ▲/🆕 표시

### Phase 4 완료 체크

- [ ] Routine 생성
- [ ] GitHub repo 연결
- [ ] Schedule: Sunday 05:00 KST
- [ ] `SLACK_WEBHOOK_URL` 설정
- [ ] `CIVITAI_TOKEN` 설정 여부 결정
- [ ] network access 허용
- [ ] unrestricted branch push 여부 결정
- [ ] 수동 실행 성공
- [ ] state/history 커밋 확인

