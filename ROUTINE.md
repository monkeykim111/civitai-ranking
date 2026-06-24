# Claude Code Routine 운영 가이드

이 문서는 **Civitai Weekly Digest**를 Claude Code Routine(클라우드)에서 매주 자동
실행하기 위한 설정과 실행 프롬프트를 정의한다.

운영 실행은 클라우드 Routine이 이 repo를 클론해서 수행한다. 박스(로컬)는 코드를
짜고 검증하는 작업대이고, 실제 주간 실행은 클라우드다. 따라서 박스가 꺼져 있어도
Routine이 실행되는 것을 목표로 한다.

---

## 1. 실행 스케줄

| 항목 | 값 |
|---|---|
| Trigger | weekly |
| Day | Sunday |
| Time | 05:00 |
| Timezone | Asia/Seoul (KST) |

---

## 2. 환경변수 (Routine Environment)

| 변수 | 필수 여부 | 용도 |
|---|---|---|
| `SLACK_WEBHOOK_URL` | **필수** (발송에 필요) | `send_slack.py --send`가 POST할 Slack Incoming Webhook |
| `CIVITAI_TOKEN` | 선택 | Civitai API 인증 호출(rate limit 여유). 없으면 public 호출 |

> ⚠️ **시크릿은 Routine UI의 Environment variables에만 입력한다.**
> 코드 / README / `.env` / 커밋 로그에 실제 값을 절대 남기지 않는다.
> Webhook이 노출되면 Slack에서 즉시 폐기하고 새로 발급한다.

---

## 3. 네트워크 접근 허용 (Network access)

| 호스트 | 용도 |
|---|---|
| `civitai.com` | Civitai REST API 수집 |
| `hooks.slack.com` | Slack Webhook 발송 |
| PyPI (`pypi.org`, `files.pythonhosted.org`) | 필요한 경우 `requirements.txt` 설치 |

---

## 4. Repo write 권한

Routine은 실행 후 변경된 **`state.json`과 `history/`** 를 default branch에
commit/push 할 수 있어야 한다. (다음 주 비교를 위해 상태가 유지돼야 하기 때문)

- 전용 private repo라면 **Allow unrestricted branch pushes**를 켜는 것을 권장한다.
- 이 권한을 켜기 싫으면 state 저장소를 GitHub가 아닌 외부 스토리지로 분리해야 하며,
  구현 복잡도가 올라간다. (현재 구성은 GitHub 커밋 방식 전제)

---

## 5. Routine 프롬프트

아래 프롬프트를 Claude Code Routine에 등록한다.

```text
이 Routine은 monkeykim111/civitai-ranking repo에서 매주 Civitai 모델 랭킹 digest를 생성하고 Slack으로 발송한다.
숫자/랭킹/델타는 직접 계산하지 말고 스크립트가 만든 digest.json 값을 그대로 사용한다.
civitai_digest.py 와 send_slack.py 의 로직은 수정하지 않는다.

1. 의존성 설치: pip install -r requirements.txt

2. 환경변수 SLACK_WEBHOOK_URL 확인. 없으면 발송하지 말고 "SLACK_WEBHOOK_URL 누락"으로 보고하고 멈춘다. (CIVITAI_TOKEN은 선택.)

3. python civitai_digest.py 실행. 실패하면 Slack 발송하지 말고 원인을 보고하고 멈춘다.

4. digest.json 이 생성됐는지 확인. 없으면 발송하지 말고 멈춘다.

5. digest.json 을 읽고 recommendation.md 를 작성한다.
   - 추천은 checkpoint_recent, lora_recent, 그리고 surge 섹션을 기준으로 작성한다.
     (all-time 누적 모델은 더 이상 없으며 추천 기준에서 제외한다.)
   - 매직 서비스 관점: 웹툰/만화/일러스트 그림체 중심 서비스. Checkpoint는 베이스 모델 후보,
     LoRA는 스타일/캐릭터/채색/선화/효과 확장 후보. 실사는 참고만.
   - SFW/NSFW를 구분하지 않는다. NSFW 여부는 서비스에서 이미 블러 처리한다고 가정한다.
   - 숫자는 digest.json 값을 그대로 인용하고 재계산하지 않는다.
   - digest.json 의 sections 에 실제로 있는 모델 중에서만 고른다. 없는 모델명을 지어내지 않는다.
   - 각 항목에 [Checkpoint] 또는 [LoRA] 와 base model 을 표기하고, 가능하면 Checkpoint와 LoRA를 균형 있게 포함한다.
   - 형식(너무 길지 않게):
     이번 주 추천 요약
     1. [모델명] — 추천 이유 한 줄
     2. [모델명] — 추천 이유 한 줄
     3. [모델명] — 추천 이유 한 줄
     운영 메모:
     - 이번 주 변화/주의점 1~2줄

6. python send_slack.py digest.json --recommendation recommendation.md --send 실행.
   이 명령이 exit code 0("Slack message sent")일 때만 다음 단계로 간다.
   실패하면 status/응답을 보고하고, state/history를 커밋하지 말고 멈춘다.

7. Slack 발송 성공 시에만, 변경된 state를 PR로 올린다:
   - git 사용자 정보가 없으면 설정한다: user.name=monkeykim111, user.email=monkeykim111@users.noreply.github.com
   - TODAY=$(date +%F)
   - git checkout -b "digest-update-$TODAY"
   - git add state.json history/   (digest.json, recommendation.md, *_debug.json, slack_payload_debug.json, .env 는 절대 add 하지 않는다)
   - 커밋할 변경이 없으면 push/PR을 생략하고 그 사실을 보고한다.
   - git commit -m "chore: update weekly civitai digest state ($TODAY)"
   - git push -u origin "digest-update-$TODAY"
   - gh pr create --base main --head "digest-update-$TODAY" --title "chore: weekly civitai digest state ($TODAY)" --body "주간 state 자동 업데이트. 다음 일요일 실행 전에 머지하세요." (PR을 draft로 만들지 않는다. --draft 옵션을 쓰지 않는다.)
   - 혹시 draft 상태로 생성되면 gh pr ready "digest-update-$TODAY" 로 리뷰 가능 상태로 전환한다.

제약(반드시 지킬 것):
- 숫자 계산을 다시 하지 마라. 좋아요/다운로드/랭킹 숫자는 digest.json 의 값을 그대로 사용한다.
- Claude 는 recommendation.md 작성(그림체 적합성 판단)에만 판단을 사용한다.
- Slack 메시지 렌더링은 send_slack.py 에 맡긴다. 직접 Block Kit 을 만들지 않는다.
- 어느 단계든 실패하면 Slack 발송 전에 멈추고 에러를 보고한다.
- 첫 실행은 state.json 이 없어 baseline 으로 동작한다(🆕/▲ 없이 2026 recent TOP 중심). 정상이다.
- 위 PR이 머지돼야 다음 주 비교가 된다. 머지는 사람이 한다.
```

추천 커밋 메시지:

```text
chore: update weekly civitai digest state
```

> 참고: 위 7번은 **PR 방식**이다. Routine의 `무제한 git push 허용`(default 브랜치 직접 push)이
> 켜져 있다면, 7번을 단순히 `main`에 직접 커밋·push 하도록 바꿔도 된다.
> 현재는 그 토글이 꺼져 있어 PR → 사람이 머지하는 흐름을 사용한다.

---

## 6. 실패 처리 원칙

- `civitai_digest.py` 또는 `send_slack.py --send` 가 실패하면 **state/history 를 커밋하지 않는다.**
- 이렇게 해야 다음 실행이 같은 baseline/비교 기준으로 재시도된다.
- 발송은 성공했는데 커밋이 실패한 경우, 다음 주에 신규/증가분이 일부 어긋날 수 있으므로
  로그를 확인하고 수동으로 state/history 를 정리한다.

---

## 7. 첫 주(baseline) 안내 — 운영 시작 전 필독

repo에는 운영 시작 시점에 `state.json` 이 **없는 상태**(로컬 테스트 state는 정리됨,
`history/` 는 `.gitkeep` 만 존재)로 커밋되어 있다. 따라서:

1. **운영 첫 Routine 실행은 baseline 이다.** (`state.json` 이 없으므로)
2. 첫 실행에서는 **🆕 / ▲ 증가분 없이 2026 recent TOP 중심으로 발송**된다.
3. 첫 실행이 성공하면 생성된 **`state.json` 과 `history/YYYY-MM-DD.json` 을 반드시 커밋/푸시**한다.
4. **이 커밋이 되어야 다음 주부터 주간 증가분(🆕/▲)이 계산된다.**
   - 커밋이 누락되면 매주 baseline 으로만 동작해 비교가 영원히 안 된다.

> 정리: 첫 주 = baseline(절댓값만) → 상태 커밋 → 둘째 주부터 신규/증가분 비교 시작.
