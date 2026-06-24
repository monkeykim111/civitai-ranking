"""Civitai Weekly Digest — Phase 3 (Slack 렌더링 / 발송).

digest.json을 읽어 Slack Block Kit payload를 만들고, 선택적으로 Slack Webhook으로
발송한다. 기본 동작은 dry-run(발송하지 않고 payload만 저장)이다.

사용법:
    python send_slack.py digest.json --dry-run
    python send_slack.py digest.json --send
    python send_slack.py digest.json --recommendation recommendation.md --dry-run
    python send_slack.py digest.json --recommendation recommendation.md --send

이 스크립트는 숫자 계산을 하지 않는다. digest.json의 값을 그대로 렌더링만 한다.
Phase 4(Routine 연결)와 Claude 추천 코멘트 자동 생성은 구현하지 않는다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import requests

# ---------------------------------------------------------------------------
# 상수 블록
# ---------------------------------------------------------------------------

SLACK_PAYLOAD_DEBUG_PATH = "slack_payload_debug.json"

# 환경변수 이름
ENV_SLACK_WEBHOOK = "SLACK_WEBHOOK_URL"

# Slack section block text 안전 길이 (실제 한계 3000자보다 보수적으로 자른다)
MAX_SECTION_TEXT_LEN = 2800

# 발송 타임아웃
SEND_TIMEOUT_SEC = 30

# 섹션 정의: (digest key, 표시 제목, is_surge)
SECTION_DEFS = [
    ("checkpoint_cumulative", "🏆 Checkpoint 누적 TOP", False),
    ("lora_cumulative", "🎨 LoRA 누적 TOP", False),
    ("checkpoint_download_surge", "📈 Checkpoint 다운로드 급상승", True),
    ("lora_download_surge", "🚀 LoRA 다운로드 급상승", True),
    ("checkpoint_thumbs_surge", "❤️ Checkpoint 좋아요 급상승", True),
    ("lora_thumbs_surge", "💜 LoRA 좋아요 급상승", True),
]

EMPTY_SECTION_TEXT = "이번 주 조건을 만족한 항목 없음"


# ---------------------------------------------------------------------------
# 렌더링 유틸
# ---------------------------------------------------------------------------

def _sanitize_link_text(name: str | None) -> str:
    """Slack 링크 텍스트에서 깨질 수 있는 문자를 정리한다."""
    if not name:
        return "(이름 없음)"
    return (
        name.replace("\n", " ")
        .replace("<", "")
        .replace(">", "")
        .replace("|", "/")
        .strip()
    )


def _short_date(value: str | None) -> str | None:
    """ISO 날짜 문자열을 YYYY-MM-DD까지만 자른다. 없으면 None."""
    if not value:
        return None
    return value[:10]


def _stat_segment(label: str, value: int, delta, pct) -> str:
    """'👍 13,850 ▲1,240 (+9.8%)' 형태의 통계 조각을 만든다.

    - delta가 null 또는 0이면 증가분 생략
    - pct가 null이면 퍼센트 생략
    """
    segment = f"{label} {value:,}"
    if delta is not None and delta > 0:
        segment += f" ▲{delta:,}"
        if pct is not None:
            segment += f" (+{pct}%)"
    return segment


def render_item(index: int, item: dict) -> str:
    """단일 모델 항목을 Slack mrkdwn 두 줄로 렌더링한다.

    description_short는 Slack 표시에 포함하지 않는다.
    """
    name = _sanitize_link_text(item.get("name"))
    url = item.get("url") or ""
    badge = " 🆕" if item.get("is_new") else ""
    line1 = f"{index}. <{url}|{name}>{badge}"

    base_model = item.get("base_model") or "-"
    thumbs = _stat_segment(
        "👍",
        int(item.get("thumbs_up", 0) or 0),
        item.get("thumbs_up_delta"),
        item.get("thumbs_up_pct"),
    )
    downloads = _stat_segment(
        "DL",
        int(item.get("downloads", 0) or 0),
        item.get("downloads_delta"),
        item.get("downloads_pct"),
    )
    line2 = f"   {base_model} · {thumbs} · {downloads}"

    date = _short_date(item.get("version_published_at"))
    if date:
        line2 += f" · 최신버전 {date}"

    return f"{line1}\n{line2}"


def _truncate(text: str) -> str:
    """Slack section text 안전 길이로 자른다."""
    if len(text) <= MAX_SECTION_TEXT_LEN:
        return text
    return text[:MAX_SECTION_TEXT_LEN].rstrip() + "\n… (이하 생략)"


def _section_block(text: str) -> dict:
    """mrkdwn section block을 만든다."""
    return {"type": "section", "text": {"type": "mrkdwn", "text": _truncate(text)}}


def render_section(title: str, items: list[dict], is_surge: bool, is_baseline: bool) -> dict | None:
    """digest 섹션 하나를 Slack section block으로 렌더링한다.

    - 항목이 있으면 제목 + 번호 목록
    - baseline에서 비어 있는 surge 섹션은 None(생략)
    - baseline이 아닌데 비어 있으면 '없음' 안내
    """
    if not items:
        if is_baseline and is_surge:
            return None
        return _section_block(f"*{title}*\n_{EMPTY_SECTION_TEXT}_")

    lines = [f"*{title}*"]
    for i, item in enumerate(items, start=1):
        lines.append(render_item(i, item))
    return _section_block("\n".join(lines))


# ---------------------------------------------------------------------------
# payload 빌드
# ---------------------------------------------------------------------------

def build_payload(digest: dict, recommendation_text: str | None) -> dict:
    """digest.json + 선택적 recommendation으로 Slack Block Kit payload를 만든다."""
    date = digest.get("date", "")
    is_baseline = bool(digest.get("is_baseline", False))
    counts = digest.get("counts", {}) or {}
    sections = digest.get("sections", {}) or {}

    ckpt_count = counts.get("checkpoint_op_count", 0)
    lora_count = counts.get("lora_op_count", 0)

    blocks: list[dict] = []

    # 헤더
    blocks.append({
        "type": "header",
        "text": {"type": "plain_text", "text": f"Civitai Weekly Digest — {date}", "emoji": True},
    })

    # 요약 (baseline이면 표기)
    summary = f"요약: Checkpoint OP {ckpt_count:,}개 / LoRA OP {lora_count:,}개"
    if is_baseline:
        summary += "\n_baseline 첫 실행: 🆕/증가분 표시는 생략됩니다._"
    blocks.append(_section_block(summary))
    blocks.append({"type": "divider"})

    # 6개 섹션
    for key, title, is_surge in SECTION_DEFS:
        block = render_section(title, sections.get(key, []) or [], is_surge, is_baseline)
        if block is not None:
            blocks.append(block)

    # Claude 추천 (있을 때만)
    if recommendation_text and recommendation_text.strip():
        blocks.append({"type": "divider"})
        blocks.append(_section_block(f"*⭐ Claude 추천*\n{recommendation_text.strip()}"))

    return {"blocks": blocks}


# ---------------------------------------------------------------------------
# 입출력 / 발송
# ---------------------------------------------------------------------------

def load_digest(path: str) -> dict:
    """digest.json을 읽는다."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_recommendation(path: str | None) -> str | None:
    """recommendation.md를 읽는다. 경로가 없거나 파일이 없으면 None."""
    if not path:
        return None
    if not os.path.exists(path):
        print(f"[warn] recommendation 파일을 찾을 수 없음: {path} (추천 섹션 생략)")
        return None
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _count_section_blocks(payload: dict) -> int:
    """divider/header를 제외한 section block 개수를 센다."""
    return sum(1 for b in payload.get("blocks", []) if b.get("type") == "section")


def send_to_slack(payload: dict, webhook_url: str) -> bool:
    """Slack Webhook으로 payload를 POST한다. 성공하면 True."""
    try:
        response = requests.post(webhook_url, json=payload, timeout=SEND_TIMEOUT_SEC)
    except requests.RequestException as exc:
        print(f"[error] Slack 발송 중 네트워크 오류: {exc}")
        return False

    if response.status_code == 200:
        print("Slack message sent")
        return True

    print(f"[error] Slack 발송 실패: status={response.status_code}")
    print(f"[error] response body: {response.text}")
    return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="digest.json을 Slack 메시지로 렌더링/발송한다 (기본: dry-run).",
    )
    parser.add_argument("digest", help="digest.json 경로")
    parser.add_argument(
        "--recommendation",
        default=None,
        help="선택: ⭐ Claude 추천 섹션으로 포함할 recommendation.md 경로",
    )
    parser.add_argument(
        "--send",
        action="store_true",
        help="실제 Slack Webhook으로 발송한다. 없으면 dry-run.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="발송하지 않고 payload만 저장한다 (기본 동작).",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    digest = load_digest(args.digest)
    recommendation_text = load_recommendation(args.recommendation)
    payload = build_payload(digest, recommendation_text)

    section_count = _count_section_blocks(payload)
    has_reco = bool(recommendation_text and recommendation_text.strip())

    # --send가 있을 때만 발송, 그 외엔 dry-run
    if args.send:
        webhook_url = os.environ.get(ENV_SLACK_WEBHOOK)
        if not webhook_url:
            print(f"[error] --send 인데 {ENV_SLACK_WEBHOOK} 환경변수가 없습니다.")
            print(f"[error] 발송하려면 export {ENV_SLACK_WEBHOOK}=\"https://hooks.slack.com/services/...\" 설정 후 다시 실행하세요.")
            return 1
        print(f"[send] {ENV_SLACK_WEBHOOK}로 발송합니다. (section blocks: {section_count}, 추천 포함: {has_reco})")
        ok = send_to_slack(payload, webhook_url)
        return 0 if ok else 1

    # dry-run
    with open(SLACK_PAYLOAD_DEBUG_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print("[dry-run] payload 생성 성공")
    print(f"[dry-run] date: {digest.get('date')} · is_baseline: {str(bool(digest.get('is_baseline'))).lower()}")
    print(f"[dry-run] section blocks: {section_count} · 추천 포함: {has_reco}")
    print(f"[dry-run] saved: {SLACK_PAYLOAD_DEBUG_PATH}")
    print(f"[dry-run] 실제 발송하려면 --send 옵션과 {ENV_SLACK_WEBHOOK} 환경변수가 필요합니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
