"""Civitai Weekly Digest — 수집 + 2026 recent 필터 + 상태/델타/랭킹.

이 스크립트는 Civitai 공식 REST API에서 지정된 base model 계열의 모델을 수집하고,
최신 버전 공개일(version_published_at)이 2026년 이후인 "최근 모델"만 추려, 지난 실행
state와 비교해 신규 여부·증가분을 계산하고 Checkpoint / LoRA를 분리한 랭킹 섹션
(digest)을 만든다.

수집 정책:
- Checkpoint 후보 + 동일 base model 계열의 호환 LoRA 후보 (same_base_model_lora 기준)
- SFW/NSFW를 모두 수집해 하나의 랭킹으로 합친다. (nsfw=false / nsfw=true 둘 다 호출)
- 단, Slack/digest 섹션에서는 SFW/NSFW를 구분 표시하지 않는다. (NSFW는 서비스에서 블러 처리 가정)

선정 기준(메인 섹션):
- version_published_at >= RECENT_VERSION_FROM ("2026-01-01")
- type별 ratio/reviews/downloads 하한 (is_recent_candidate)

> 참고: Civitai API만으로는 특정 LoRA가 특정 Checkpoint에서 정확히 "파생"되었는지
> 항상 보장하기 어렵다. 초기 운영에서는 동일 base model을 공유하는 LoRA를 호환 후보로
> 수집한다.

산출물: state.json / history/YYYY-MM-DD.json / digest.json /
        checkpoint_candidates_debug.json / lora_candidates_debug.json

Slack 발송(send_slack.py)과 Routine 연결은 이 파일에서 다루지 않는다.
"""

from __future__ import annotations

import datetime
import json
import os
import time

import requests

# ---------------------------------------------------------------------------
# 상수 블록
# ---------------------------------------------------------------------------

# 수집 대상 base model 목록 (SDXL 계열 + Z-Image Turbo)
TARGET_BASE_MODELS = [
    "SDXL 1.0",
    "Pony",
    "Illustrious",
    "NoobAI",
    "ZImageTurbo",
]

# 수집 대상 타입
CHECKPOINT_TYPES = ["Checkpoint"]
LORA_TYPES = ["LORA"]

# 2026 recent 기준
RECENT_VERSION_FROM = "2026-01-01"

# SFW/NSFW 모두 수집 (nsfw 쿼리 값)
SAFETY_MODES = [False, True]

# recent 선정 임계값 — Checkpoint
RECENT_CHECKPOINT_MIN_RATIO = 0.95
RECENT_CHECKPOINT_MIN_REVIEWS = 100
RECENT_CHECKPOINT_MIN_DOWNLOADS = 1000

# recent 선정 임계값 — LoRA
RECENT_LORA_MIN_RATIO = 0.95
RECENT_LORA_MIN_REVIEWS = 100
RECENT_LORA_MIN_DOWNLOADS = 500

# API 호출 제어
API_BASE_URL = "https://civitai.com/api/v1/models"
FETCH_LIMIT = 100                            # 한 페이지당 요청 개수
CHECKPOINT_MAX_PAGES_PER_BASE_MODEL = 30     # Checkpoint base model당 최대 페이지
LORA_MAX_PAGES_PER_BASE_MODEL = 15           # LoRA base model당 최대 페이지
# sort=Highest Rated 정렬상 engagement(ratio/reviews/downloads) 기준 통과 모델은 앞쪽에
# 몰린다. 통과 모델 0건 페이지가 연속 N번이면 조기 종료한다.
STOP_AFTER_EMPTY_QUALIFYING_PAGES = 2

# 재시도/백오프 제어
MAX_RETRIES = 3
BACKOFF_BASE_SEC = 2.0
REQUEST_TIMEOUT_SEC = 60

# 랭킹 섹션 개수
N_CHECKPOINT_RECENT = 10
N_LORA_RECENT = 10
N_CHECKPOINT_DL_SURGE = 5
N_LORA_DL_SURGE = 5
N_CHECKPOINT_UP_SURGE = 5
N_LORA_UP_SURGE = 5

# 급상승 가드 (작은 수의 비율 뻥튀기 방지: 최소 절대 증가량)
GUARD_CHECKPOINT_DL = 500
GUARD_LORA_DL = 300
GUARD_CHECKPOINT_UP = 50
GUARD_LORA_UP = 30

# 파일/디렉토리 경로
STATE_PATH = "state.json"
DIGEST_PATH = "digest.json"
HISTORY_DIR = "history"
CHECKPOINT_DEBUG_PATH = "checkpoint_candidates_debug.json"
LORA_DEBUG_PATH = "lora_candidates_debug.json"


# ---------------------------------------------------------------------------
# HTTP 호출 유틸
# ---------------------------------------------------------------------------

def _request_json(url: str, token: str | None, params: dict | None = None) -> dict:
    """주어진 URL로 GET 요청을 보내고 JSON을 파싱해 반환한다.

    CIVITAI_TOKEN이 있으면 Authorization 헤더를 붙인다.
    간단한 재시도/지수 백오프를 적용한다.
    """
    headers = {
        "User-Agent": "civitai-weekly-digest/recent",
        "Accept": "application/json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(
                url, params=params, headers=headers, timeout=REQUEST_TIMEOUT_SEC
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            last_error = exc
            if attempt < MAX_RETRIES:
                wait = BACKOFF_BASE_SEC * (2 ** (attempt - 1))
                print(f"    [retry {attempt}/{MAX_RETRIES}] 요청 실패({exc}). {wait:.1f}s 대기 후 재시도")
                time.sleep(wait)
            else:
                print(f"    [error] 최대 재시도 초과: {exc}")

    assert last_error is not None
    raise last_error


def _first_page_params(base_model: str, model_type: str, nsfw: bool) -> dict:
    """base model + type + nsfw 모드에 대한 첫 페이지 요청 쿼리 파라미터를 만든다."""
    return {
        "types": model_type,
        "baseModels": base_model,
        "sort": "Highest Rated",
        "period": "AllTime",
        "limit": str(FETCH_LIMIT),
        "nsfw": "true" if nsfw else "false",
    }


# ---------------------------------------------------------------------------
# 통계 / 선정 기준
# ---------------------------------------------------------------------------

def latest_version(model: dict) -> dict | None:
    """모델의 최신 버전(modelVersions[0])을 반환한다. 없으면 None."""
    versions = model.get("modelVersions") or []
    if not versions:
        return None
    return versions[0]


def latest_stats(model: dict) -> tuple[int, int, int]:
    """최신 버전의 stats에서 (thumbs_up, thumbs_down, downloads)를 반환한다."""
    version = latest_version(model)
    if version is None:
        return 0, 0, 0
    stats = version.get("stats", {}) or {}
    thumbs_up = int(stats.get("thumbsUpCount", 0) or 0)
    thumbs_down = int(stats.get("thumbsDownCount", 0) or 0)
    downloads = int(stats.get("downloadCount", 0) or 0)
    return thumbs_up, thumbs_down, downloads


def _ratio(up: int, down: int) -> float:
    """thumbs up 비율. total==0이면 0."""
    total = up + down
    if total == 0:
        return 0.0
    return up / total


def _engagement_thresholds(model_type: str) -> tuple[float, int, int]:
    """type별 (min_ratio, min_reviews, min_downloads)를 반환한다."""
    if model_type == CHECKPOINT_TYPES[0]:
        return (
            RECENT_CHECKPOINT_MIN_RATIO,
            RECENT_CHECKPOINT_MIN_REVIEWS,
            RECENT_CHECKPOINT_MIN_DOWNLOADS,
        )
    return (
        RECENT_LORA_MIN_RATIO,
        RECENT_LORA_MIN_REVIEWS,
        RECENT_LORA_MIN_DOWNLOADS,
    )


def _passes_engagement(up: int, down: int, downloads: int,
                       min_ratio: float, min_reviews: int, min_downloads: int) -> bool:
    """ratio/reviews/downloads 하한을 모두 통과하는지 (날짜 무관)."""
    total = up + down
    if total == 0:
        return False
    return (_ratio(up, down) >= min_ratio
            and total >= min_reviews
            and downloads >= min_downloads)


def is_recent_candidate(candidate: dict) -> bool:
    """후보가 2026 recent 메인 섹션 기준을 만족하는지 판정한다.

    - version_published_at >= RECENT_VERSION_FROM
    - type별 ratio/reviews/downloads 하한 통과
    """
    vp = candidate.get("version_published_at")
    if not vp or vp < RECENT_VERSION_FROM:
        return False
    min_ratio, min_reviews, min_downloads = _engagement_thresholds(candidate.get("type"))
    return _passes_engagement(
        candidate["thumbs_up"], candidate["thumbs_down"], candidate["downloads"],
        min_ratio, min_reviews, min_downloads,
    )


def _count_qualifying_in_page(page_items: list[dict],
                              min_ratio: float, min_reviews: int, min_downloads: int) -> int:
    """페이지 내 engagement 기준 통과 모델 수 (조기 종료 판단용, 날짜 무관)."""
    count = 0
    for model in page_items:
        up, down, downloads = latest_stats(model)
        if _passes_engagement(up, down, downloads, min_ratio, min_reviews, min_downloads):
            count += 1
    return count


def build_model_url(model_id: int, version_id: int) -> str:
    """Civitai 모델 페이지 URL을 만든다."""
    return f"https://civitai.com/models/{model_id}?modelVersionId={version_id}"


# ---------------------------------------------------------------------------
# 수집
# ---------------------------------------------------------------------------

def fetch_models(base_model: str, model_type: str, token: str | None, nsfw: bool,
                 max_pages: int, min_ratio: float, min_reviews: int, min_downloads: int) -> list[dict]:
    """단일 base model + type + nsfw 모드에 대해 Civitai API를 호출한다.

    - cursor/nextPage pagination 처리, base model당 최대 max_pages 페이지.
    - engagement 통과 0건 페이지가 연속되면 조기 종료한다.
    """
    items: list[dict] = []
    page_count = 0
    empty_streak = 0

    url: str | None = API_BASE_URL
    params: dict | None = _first_page_params(base_model, model_type, nsfw)

    while True:
        if page_count >= max_pages:
            break
        data = _request_json(url, token, params=params)
        page_count += 1

        page_items = data.get("items", []) or []
        items.extend(page_items)
        if not page_items:
            break

        if _count_qualifying_in_page(page_items, min_ratio, min_reviews, min_downloads) == 0:
            empty_streak += 1
        else:
            empty_streak = 0
        if empty_streak >= STOP_AFTER_EMPTY_QUALIFYING_PAGES:
            break

        metadata = data.get("metadata", {}) or {}
        next_cursor = metadata.get("nextCursor")
        next_page = metadata.get("nextPage")
        if next_cursor:
            url = API_BASE_URL
            params = _first_page_params(base_model, model_type, nsfw)
            params["cursor"] = str(next_cursor)
        elif next_page:
            url = next_page
            params = None
        else:
            break

    return items


def _build_candidate(model: dict, version: dict, base_model: str, model_type: str) -> dict:
    """단일 모델/버전을 후보 dict로 변환한다.

    날짜는 리스트 API에서 안정적으로 제공되는 version_published_at만 사용한다.
    nsfw 필드는 디버그용으로 남기되 Slack에는 표시하지 않는다.
    """
    up, down, downloads = latest_stats(model)
    creator = model.get("creator") or {}
    description = model.get("description") or ""
    description_short = description[:200] if description else None

    return {
        "type": model_type,
        "model_id": model.get("id"),
        "version_id": version.get("id"),
        "name": model.get("name"),
        "base_model": base_model,
        "thumbs_up": up,
        "thumbs_down": down,
        "downloads": downloads,
        "version_published_at": version.get("publishedAt"),
        "url": build_model_url(model.get("id"), version.get("id")),
        # 디버그용 메타데이터 (Slack 미표시)
        "nsfw": bool(model.get("nsfw", False)),
        "creator": creator.get("username"),
        "tags": model.get("tags") or [],
        "description_short": description_short,
    }


def collect_raw_candidates(token: str | None, model_type: str, max_pages: int) -> list[dict]:
    """특정 type의 전체 후보(recent 필터 적용 전)를 수집한다.

    - SFW/NSFW(SAFETY_MODES)를 모두 호출해 합친다.
    - type+version_id 기준으로 중복을 제거한다.
    """
    min_ratio, min_reviews, min_downloads = _engagement_thresholds(model_type)
    raw: list[dict] = []
    seen_version_ids: set[int] = set()

    for nsfw in SAFETY_MODES:
        for base_model in TARGET_BASE_MODELS:
            print(f"[fetch] {model_type} · {base_model} · nsfw={str(nsfw).lower()} 수집 중...")
            models = fetch_models(
                base_model, model_type, token, nsfw,
                max_pages, min_ratio, min_reviews, min_downloads,
            )
            for model in models:
                version = latest_version(model)
                if version is None:
                    continue
                model_id = model.get("id")
                version_id = version.get("id")
                if model_id is None or version_id is None:
                    continue
                if version_id in seen_version_ids:
                    continue
                seen_version_ids.add(version_id)
                raw.append(_build_candidate(model, version, base_model, model_type))
    return raw


def collect_checkpoint_candidates(token: str | None) -> list[dict]:
    """Checkpoint 2026 recent 후보를 수집한다."""
    raw = collect_raw_candidates(token, CHECKPOINT_TYPES[0], CHECKPOINT_MAX_PAGES_PER_BASE_MODEL)
    return [c for c in raw if is_recent_candidate(c)]


def collect_lora_candidates(token: str | None) -> list[dict]:
    """동일 base model 계열의 호환 LoRA 2026 recent 후보를 수집한다."""
    raw = collect_raw_candidates(token, LORA_TYPES[0], LORA_MAX_PAGES_PER_BASE_MODEL)
    return [c for c in raw if is_recent_candidate(c)]


# ---------------------------------------------------------------------------
# 상태 / 델타 / 신규
# ---------------------------------------------------------------------------

def _seen_key(candidate: dict) -> str:
    """state.seen 키: '{type}:{version_id}'."""
    return f"{candidate['type']}:{candidate['version_id']}"


def load_state(path: str) -> tuple[dict, bool]:
    """state.json을 읽어 (state, is_baseline)을 반환한다. 없으면 baseline."""
    if not os.path.exists(path):
        return {"last_run": None, "seen": {}}, True
    with open(path, "r", encoding="utf-8") as f:
        state = json.load(f)
    state.setdefault("seen", {})
    return state, False


def save_state(path: str, state: dict) -> None:
    """state.json을 저장한다."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _round_pct(delta: int, previous: int) -> float | None:
    """증가율(%)을 계산한다. previous가 0이면 None."""
    if previous == 0:
        return None
    return round(delta / previous * 100, 1)


def compute_deltas(candidate: dict, state: dict, is_baseline: bool) -> dict:
    """후보의 신규 여부 + 증가분/증가율을 계산해 dict로 반환한다."""
    if is_baseline:
        return {
            "is_new": False, "thumbs_up_delta": None, "thumbs_up_pct": None,
            "downloads_delta": None, "downloads_pct": None,
        }
    prev = state.get("seen", {}).get(_seen_key(candidate))
    if prev is None:
        return {
            "is_new": True, "thumbs_up_delta": None, "thumbs_up_pct": None,
            "downloads_delta": None, "downloads_pct": None,
        }
    prev_up = int(prev.get("last_thumbs_up", 0) or 0)
    prev_dl = int(prev.get("last_downloads", 0) or 0)
    up_delta = max(0, candidate["thumbs_up"] - prev_up)
    dl_delta = max(0, candidate["downloads"] - prev_dl)
    return {
        "is_new": False,
        "thumbs_up_delta": up_delta,
        "thumbs_up_pct": _round_pct(up_delta, prev_up),
        "downloads_delta": dl_delta,
        "downloads_pct": _round_pct(dl_delta, prev_dl),
    }


def enrich_candidates(candidates: list[dict], state: dict, is_baseline: bool) -> list[dict]:
    """후보 목록에 신규/델타 정보를 합쳐 새 dict 목록을 반환한다."""
    enriched = []
    for c in candidates:
        merged = dict(c)
        merged.update(compute_deltas(c, state, is_baseline))
        enriched.append(merged)
    return enriched


def update_state(state: dict, candidates: list[dict], today: str) -> dict:
    """현재 후보로 state.seen을 갱신한다."""
    seen = state.get("seen", {})
    for c in candidates:
        key = _seen_key(c)
        first_seen = seen.get(key, {}).get("first_seen", today)
        seen[key] = {
            "type": c["type"],
            "model_id": c["model_id"],
            "version_id": c["version_id"],
            "name": c["name"],
            "base_model": c["base_model"],
            "first_seen": first_seen,
            "last_thumbs_up": c["thumbs_up"],
            "last_downloads": c["downloads"],
            "version_published_at": c["version_published_at"],
            "url": c["url"],
        }
    return {"last_run": today, "seen": seen}


# ---------------------------------------------------------------------------
# 랭킹 섹션
# ---------------------------------------------------------------------------

def _top_recent(candidates: list[dict], n: int) -> list[dict]:
    """thumbs_up 절댓값 내림차순 상위 n개."""
    return sorted(candidates, key=lambda c: c["thumbs_up"], reverse=True)[:n]


def _top_surge(candidates: list[dict], n: int, delta_key: str, pct_key: str, guard: int) -> list[dict]:
    """증가율(pct_key) 내림차순 상위 n개. 단 delta_key >= guard 이고 pct가 있는 경우만."""
    eligible = [
        c for c in candidates
        if c.get(delta_key) is not None and c[delta_key] >= guard and c.get(pct_key) is not None
    ]
    return sorted(eligible, key=lambda c: c[pct_key], reverse=True)[:n]


def build_sections(checkpoint: list[dict], lora: list[dict], is_baseline: bool) -> dict:
    """Checkpoint / LoRA를 분리한 6개 랭킹 섹션을 만든다.

    - recent: thumbs_up 내림차순
    - surge: 증가율 내림차순 + 최소 증가량 가드 (baseline이면 빈 배열)
    """
    sections = {
        "checkpoint_recent": _top_recent(checkpoint, N_CHECKPOINT_RECENT),
        "lora_recent": _top_recent(lora, N_LORA_RECENT),
        "checkpoint_download_surge": [],
        "lora_download_surge": [],
        "checkpoint_thumbs_surge": [],
        "lora_thumbs_surge": [],
    }
    if not is_baseline:
        sections["checkpoint_download_surge"] = _top_surge(
            checkpoint, N_CHECKPOINT_DL_SURGE, "downloads_delta", "downloads_pct", GUARD_CHECKPOINT_DL)
        sections["lora_download_surge"] = _top_surge(
            lora, N_LORA_DL_SURGE, "downloads_delta", "downloads_pct", GUARD_LORA_DL)
        sections["checkpoint_thumbs_surge"] = _top_surge(
            checkpoint, N_CHECKPOINT_UP_SURGE, "thumbs_up_delta", "thumbs_up_pct", GUARD_CHECKPOINT_UP)
        sections["lora_thumbs_surge"] = _top_surge(
            lora, N_LORA_UP_SURGE, "thumbs_up_delta", "thumbs_up_pct", GUARD_LORA_UP)
    return sections


# ---------------------------------------------------------------------------
# 산출물 기록
# ---------------------------------------------------------------------------

def _dump(path: str, data) -> None:
    """JSON을 사람이 읽기 좋게 저장한다."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def write_history(today: str, candidates: list[dict], is_baseline: bool, counts: dict) -> None:
    """history/YYYY-MM-DD.json에 전체 후보 스냅샷을 저장한다."""
    os.makedirs(HISTORY_DIR, exist_ok=True)
    path = os.path.join(HISTORY_DIR, f"{today}.json")
    _dump(path, {"date": today, "is_baseline": is_baseline, "counts": counts, "candidates": candidates})


def write_digest(today: str, sections: dict, is_baseline: bool, counts: dict) -> None:
    """digest.json에 랭킹 섹션을 저장한다."""
    _dump(DIGEST_PATH, {"date": today, "is_baseline": is_baseline, "counts": counts, "sections": sections})


# ---------------------------------------------------------------------------
# 실행 엔트리
# ---------------------------------------------------------------------------

def _print_run_summary(today: str, is_baseline: bool, counts: dict, sections: dict) -> None:
    print()
    print("[Run summary]")
    print(f"date: {today}")
    print(f"is_baseline: {str(is_baseline).lower()}")
    print(f"checkpoint_recent_count: {counts['checkpoint_recent_count']}")
    print(f"lora_recent_count: {counts['lora_recent_count']}")
    print()
    print("[Sections]")
    for key in (
        "checkpoint_recent", "lora_recent",
        "checkpoint_download_surge", "lora_download_surge",
        "checkpoint_thumbs_surge", "lora_thumbs_surge",
    ):
        print(f"{key}: {len(sections[key])}")
    print()
    print("[Files]")
    print(STATE_PATH)
    print(os.path.join(HISTORY_DIR, f"{today}.json"))
    print(DIGEST_PATH)
    print(CHECKPOINT_DEBUG_PATH)
    print(LORA_DEBUG_PATH)


def main() -> None:
    token = os.environ.get("CIVITAI_TOKEN") or None
    print("[auth] CIVITAI_TOKEN 사용 (인증 호출)" if token else "[auth] CIVITAI_TOKEN 없음 (public 호출)")

    today = datetime.date.today().isoformat()
    state, is_baseline = load_state(STATE_PATH)

    # raw 수집(SFW+NSFW) → 2026 recent 필터
    raw_checkpoint = collect_raw_candidates(token, CHECKPOINT_TYPES[0], CHECKPOINT_MAX_PAGES_PER_BASE_MODEL)
    raw_lora = collect_raw_candidates(token, LORA_TYPES[0], LORA_MAX_PAGES_PER_BASE_MODEL)
    checkpoint = [c for c in raw_checkpoint if is_recent_candidate(c)]
    lora = [c for c in raw_lora if is_recent_candidate(c)]

    # 신규/델타 계산
    checkpoint = enrich_candidates(checkpoint, state, is_baseline)
    lora = enrich_candidates(lora, state, is_baseline)

    counts = {"checkpoint_recent_count": len(checkpoint), "lora_recent_count": len(lora)}
    sections = build_sections(checkpoint, lora, is_baseline)

    # debug 덤프 (type별 enriched 후보)
    _dump(CHECKPOINT_DEBUG_PATH, checkpoint)
    _dump(LORA_DEBUG_PATH, lora)

    # history / digest 기록
    all_candidates = checkpoint + lora
    write_history(today, all_candidates, is_baseline, counts)
    write_digest(today, sections, is_baseline, counts)

    # state 갱신
    new_state = update_state(state, all_candidates, today)
    save_state(STATE_PATH, new_state)

    _print_run_summary(today, is_baseline, counts, sections)


if __name__ == "__main__":
    main()
