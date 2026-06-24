# Civitai Weekly Digest

Civitai에서 **SDXL 계열 + Z-Image Turbo 체크포인트** 중
**Overwhelmingly Positive(OP)에 가까운 고평가 모델**을 주기적으로 수집·랭킹해
Slack으로 보내는 것을 목표로 하는 프로젝트.

전체 설계는 [`civitai-ranking-SPEC.md`](civitai-ranking-SPEC.md) 참고.

> **현재 상태: Phase 4 운영 준비 완료** (수집 + OP 필터 + 상태/델타/랭킹 + Slack 렌더링/발송 + Routine 문서).
> 실제 Claude Code Routine 생성은 사람이 UI에서 수행한다 → [`ROUTINE.md`](ROUTINE.md) 참고.

## 수집 + 2026 recent 필터

Civitai 공식 REST API에서 지정된 base model 계열의 모델을 수집하고,
**최신 버전 공개일이 2026년 이후(`version_published_at >= 2026-01-01`)인 최근 모델**만
추려 랭킹한다. 오래된(2023~2025) 모델이 반복 노출되지 않게 하기 위함이다.

수집 대상은 두 종류로 분리한다.

- **Checkpoint 후보** → `checkpoint_candidates_debug.json`
- **동일 base model 계열의 호환 LoRA 후보** → `lora_candidates_debug.json`

> 참고: Civitai API만으로는 특정 LoRA가 특정 Checkpoint에서 정확히 *파생*되었는지
> 항상 보장하기 어렵다. 따라서 초기 운영에서는 동일 base model을 공유하는 LoRA를
> `same_base_model_lora` 기준의 **호환 LoRA 후보**로 수집하고, 이름/태그/설명 기반의
> `related_lora` 매칭은 추후 추가한다.

수집 대상 base model (Turbo / Lightning 계열은 제외):

```python
TARGET_BASE_MODELS = ["SDXL 1.0", "Pony", "Illustrious", "NoobAI", "ZImageTurbo"]
```

**SFW/NSFW를 모두 수집해 하나의 랭킹으로 합친다** (`nsfw=false` / `nsfw=true` 둘 다 호출).
단, Slack/digest에는 SFW/NSFW를 구분 표시하지 않는다 (NSFW는 서비스에서 블러 처리 가정).

recent 선정 기준 (Checkpoint / LoRA 분리):

```
version_published_at >= "2026-01-01"
ratio = thumbs_up / (thumbs_up + thumbs_down)   (total == 0 이면 제외)

Checkpoint: ratio >= 0.95, reviews >= 100, downloads >= 1000
LoRA:       ratio >= 0.95, reviews >= 100, downloads >= 500
```

페이지 상한은 type별로 분리한다. `sort=Highest Rated` 정렬상 engagement(ratio/reviews/
downloads) 기준 통과 모델은 앞쪽에 몰리므로, 통과 0건 페이지가 연속되면 조기 종료한다.

```python
CHECKPOINT_MAX_PAGES_PER_BASE_MODEL = 30
LORA_MAX_PAGES_PER_BASE_MODEL = 15
STOP_AFTER_EMPTY_QUALIFYING_PAGES = 2
```

### 날짜 표기

Civitai 리스트 API에서 안정적으로 확인 가능한 `modelVersions[0].publishedAt`을
`version_published_at`으로 저장하고, 표시에는 `최신버전: YYYY-MM-DD` 형태로 사용한다.
모델 생성일은 리스트 API에서 안정적으로 제공되지 않으므로 사용하지 않는다.

## 상태 / 델타 / 랭킹

지난 실행 `state.json`과 비교해 신규 여부(`is_new`)와 좋아요/다운로드 증가분을
계산하고, Checkpoint / LoRA를 분리한 6개 랭킹 섹션(`digest.json`)을 만든다.

```
checkpoint_recent · lora_recent                 (thumbs_up 내림차순)
checkpoint_download_surge · lora_download_surge  (다운로드 증가율 내림차순 + 가드)
checkpoint_thumbs_surge · lora_thumbs_surge      (좋아요 증가율 내림차순 + 가드)
```

- all-time 누적(cumulative) 섹션은 제거됨 (오래된 모델 반복 노출 방지).
- 신규 판별 키: `"{type}:{version_id}"`
- delta는 음수면 0으로 clamp, pct는 이전 값이 0이면 `null`
- `state.json`이 없으면 **baseline 모드**: `is_new=false`, delta/pct 생략, recent 랭킹만 (surge 생략)
- 산출 파일: `state.json`, `history/YYYY-MM-DD.json`, `digest.json`

## 실행 방법

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python civitai_digest.py
```

실행하면 `[Run summary]` / `[Sections]` / `[Files]`가 출력되고
아래 파일이 생성된다.

- `state.json` — 비교용 작업 기억 (seen)
- `history/YYYY-MM-DD.json` — 그 날짜의 전체 후보 스냅샷
- `digest.json` — 6개 랭킹 섹션
- `checkpoint_candidates_debug.json` / `lora_candidates_debug.json` — 사람 확인용 덤프

## Slack 렌더링 / 발송

`digest.json`을 읽어 Slack Block Kit payload로 렌더링하고, 선택적으로 Slack
Webhook으로 발송한다. **기본 동작은 dry-run**(발송하지 않고 payload만 저장)이다.

```bash
# payload만 만들어 slack_payload_debug.json으로 저장 (발송 안 함)
python send_slack.py digest.json --dry-run

# 실제 Slack 발송 (SLACK_WEBHOOK_URL 필요)
python send_slack.py digest.json --send

# ⭐ Claude 추천 섹션(recommendation.md)을 포함해 dry-run
python send_slack.py digest.json --recommendation recommendation.md --dry-run
```

- `--send`가 있을 때만 실제 발송한다. 옵션이 없으면 dry-run.
- `--send`인데 `SLACK_WEBHOOK_URL`이 없으면 에러를 출력하고 종료한다.
- `--recommendation`은 선택값이며, 파일이 있으면 메시지 하단에 `⭐ Claude 추천`
  섹션으로 붙는다. (추천 코멘트 자동 생성은 하지 않는다.)

렌더링 섹션(순서): 🆕 2026 Checkpoint TOP · 🎨 2026 LoRA TOP · 📈 Checkpoint 다운로드 급상승
· 🚀 LoRA 다운로드 급상승 · ❤️ Checkpoint 좋아요 급상승 · 💜 LoRA 좋아요 급상승 · ⭐ Claude 추천.

모델 항목은 여러 줄 mrkdwn으로 표시한다. 모델명은 `<url|name>` 클릭 링크,
`is_new=true`면 제목 옆 🆕, delta/percent가 있으면 ▲/(+%)을 함께 보여준다 (null/0이면 생략).
SFW/NSFW 여부와 `description_short`는 Slack에 표시하지 않는다.

```
*1. <url|모델명>* 🆕

• 유형: `LoRA`
• 베이스: `Illustrious`
• 최신버전: `2026-06-10`
• 좋아요: `1,920` ▲120 (+6.7%)
• 다운로드: `19,300` ▲3,200 (+19.8%)
```

## 환경변수

| 변수 | 필수 여부 | 설명 |
|---|---|---|
| `CIVITAI_TOKEN` | 선택 | 있으면 `Authorization: Bearer <token>` 헤더로 인증 호출. 없으면 public API 호출. rate limit 여유를 위해 사용 권장. |
| `SLACK_WEBHOOK_URL` | 선택(발송 시 필수) | `send_slack.py --send` 시 사용할 Slack Incoming Webhook URL. |

```bash
cp .env.example .env
export SLACK_WEBHOOK_URL="https://hooks.slack.com/services/..."
```

> **주의:** `CIVITAI_TOKEN`과 `SLACK_WEBHOOK_URL`은 **비밀번호처럼 취급한다.**
> public repo / README / 코드 / 커밋 로그에 실제 값을 절대 남기지 말 것.
> Slack Webhook이 노출되면 즉시 폐기하고 새로 발급한다.
> `.env`는 `.gitignore`로 커밋에서 제외된다.

**Slack Webhook URL은 어디에 넣나?**

- **로컬 실행**: repo 루트에 `.env`를 만들고 `SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...`
  를 적는다. `.env`는 `.gitignore`로 커밋에서 제외된다. (또는 셸에서 `export`)
- **운영(Routine)**: Claude Code Routine UI의 **Environment variables**에 `SLACK_WEBHOOK_URL`을 등록한다.
- 두 경우 모두 **코드/README/`.env.example`/커밋 로그에는 절대 실제 값을 넣지 않는다.**

## 파일 역할

| 파일/디렉토리 | 역할 | 커밋 |
|---|---|---|
| `civitai_digest.py` | 수집·OP 필터·델타·랭킹·`state/history/digest` 생성 (Phase 1~2) | ✅ |
| `send_slack.py` | `digest.json` → Slack 렌더링/발송 (Phase 3) | ✅ |
| `requirements.txt` / `README.md` / `ROUTINE.md` / `.env.example` / `.gitignore` | 코드/문서/설정 | ✅ |
| `state.json` | 주간 비교용 작업 기억 (seen) | ✅ **반드시 커밋** |
| `history/YYYY-MM-DD.json` | 주간 전체 후보 스냅샷 | ✅ **반드시 커밋** |
| `digest.json` | 발송 입력 (매주 재생성) | ❌ gitignore |
| `recommendation.md` | Claude 추천 코멘트 (매주 재생성) | ❌ gitignore |
| `checkpoint_candidates_debug.json` / `lora_candidates_debug.json` / `slack_payload_debug.json` | 검증 산출물 (매번 재생성) | ❌ gitignore |
| `.env` | 실제 시크릿 | ❌ gitignore |

> **왜 `state.json`과 `history/`는 커밋해야 하나?**
> Routine은 매주 클라우드에서 repo를 클론해 실행한다. 지난주 `state.json`이 repo에
> 남아 있어야 이번 주 실행이 **신규(🆕)·증가분(▲)** 을 계산할 수 있다. 커밋되지 않으면
> 매주 baseline 으로만 동작해 비교가 불가능하다. `history/`는 추세 분석 자산으로 누적한다.

## 운영 (Claude Code Routine)

매주 **일요일 05:00 (KST)** Claude Code Routine이 이 repo를 클론해 실행한다.
스케줄 / 환경변수 / 네트워크 허용 / repo write 권한 / 실행 프롬프트는
[`ROUTINE.md`](ROUTINE.md)에 정리되어 있다.

핵심 흐름: `civitai_digest.py` 실행 → `digest.json` 검토 → `recommendation.md` 작성
→ `send_slack.py ... --send` 발송 → **발송 성공 후** `state.json` / `history/` 커밋·푸시.

> **운영 첫 실행은 baseline 이다.** repo는 `state.json` 없이(=`history/`에 `.gitkeep`만) 시작한다.
> 첫 주는 🆕/▲ 없이 **누적 TOP만** 발송되며, 첫 실행 성공 후 생성된 `state.json` 과
> `history/YYYY-MM-DD.json` 을 커밋/푸시해야 **다음 주부터 주간 증가분이 계산**된다.
> (커밋이 안 되면 매주 baseline 만 반복된다.)
