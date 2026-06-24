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
이 repo에서 아래 순서대로 작업한다.

1. 의존성을 설치한다. (pip install -r requirements.txt)
2. python civitai_digest.py 를 실행한다.
3. 생성된 digest.json 을 읽는다.
4. digest.json 의 각 섹션을 매직 서비스 관점에서 검토한다.
   - 주력은 웹툰/만화/일러스트 그림체다.
   - 실사 모델도 일부 쓰지만 우선순위는 낮다.
   - 학교 대상 서비스 맥락상 NSFW 가능성이 높은 모델은 추천에서 보수적으로 본다.
5. 이번 주 추천 3개와 각 모델별 한 줄 코멘트를 recommendation.md 로 작성한다.
6. python send_slack.py digest.json --recommendation recommendation.md --send 를 실행해 Slack으로 보낸다.
7. Slack 발송이 성공하면, 변경된 state.json 과 history/ 를 커밋한다.
8. default branch 에 push 한다. 커밋 메시지는 영어로 작성한다.

제약(반드시 지킬 것):
- 숫자 계산을 다시 하지 마라. 좋아요/다운로드/랭킹 숫자는 digest.json 의 값을 그대로 사용한다.
- Claude 는 recommendation.md 작성(그림체 적합성 판단)에만 판단을 사용한다.
- Slack 메시지 렌더링은 send_slack.py 에 맡긴다. 직접 Block Kit 을 만들지 않는다.
- 어느 단계든 실패하면 Slack 발송 전에 멈추고 에러를 보고한다.
- state.json / history 커밋은 반드시 Slack 발송 성공 후에만 수행한다.
  (발송 실패 시 상태를 커밋하지 않는다 → 다음 실행에서 재시도 가능하게 둔다.)
```

추천 커밋 메시지:

```text
chore: update weekly civitai digest state
```

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
2. 첫 실행에서는 **🆕 / ▲ 증가분 없이 누적 TOP 만 발송**된다.
3. 첫 실행이 성공하면 생성된 **`state.json` 과 `history/YYYY-MM-DD.json` 을 반드시 커밋/푸시**한다.
4. **이 커밋이 되어야 다음 주부터 주간 증가분(🆕/▲)이 계산된다.**
   - 커밋이 누락되면 매주 baseline 으로만 동작해 비교가 영원히 안 된다.

> 정리: 첫 주 = baseline(절댓값만) → 상태 커밋 → 둘째 주부터 신규/증가분 비교 시작.
