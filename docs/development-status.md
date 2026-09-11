# 개발 현황과 다음 단계

코드 기준: `master@fbecdf786e2690be94dba797f7d771d388b11841`.
로드맵 결정: **자동 정합 비교를 먼저 수행하며, 수동 점·선 입력 확장을 제품의 선행 조건으로 두지 않는다.**

다음 작업의 기준 문서는 [Experiment 001: 수동 입력 없는 자동 정합 비교](../experiments/001-auto-calibration/README.md)다.
완료 범위·미실행 항목·E1 실제 영상 평가의 완료 조건은 [실행 현황과 다음 작업 인계](../experiments/001-auto-calibration/status-and-handoff.md)에 정리한다.
이 결정은 이전 문서의 `observation-v2 → 수동 점+선 → snap → 자동화` 우선순위를 대체한다.
기존 구현과 저장 데이터를 삭제하거나 현재 `CONTRACT.md`의 좌표 사용 자격을 완화하는 결정은 아니다.

## 현재 구현과 미구현을 구분

| 항목 | 상태 | 근거/범위 |
| --- | --- | --- |
| P0 점 정합·독립 검증 | 구현 | `gsr/geometry.py`, `CONTRACT.md` |
| PR-01 좌표 자격·계산 안전성 | 구현 | review/unavailable은 image-only; 과거 결과 read-time 정정; bounded RANSAC |
| PR-02 비저장 preview | 구현 | 저장과 공통 계산, draft_version, DB 비저장 |
| PR-03/04 원본 편집·오버레이 | 구현 | zoom/pan/undo/redo/draft, inverse-H, stale response 방어 |
| PR-05 영상 접근 | 구현 | exact-PTS seek와 순차 fallback, 제한 캐시, 짧은 구간 디코딩, 썸네일 |
| 이름 있는 기준점 7개 | 선택 UI 구현 | feature ID를 보존하는 정식 관측 계약은 아직 없음 |
| 기능·영상 접근 평가 | 기록 있음 | 합성 CI 및 실제 영상 대기시간/픽셀 비교; 경기장 정확도 평가와 다름 |
| 자동 정합 엔진 비교 | 입력 준비·PnLCalib 공개 예제 E0 완료 | [실행 결과](../experiments/001-auto-calibration/e0-results.md); 실제 영상 E1/E2·독립 정확도 비교는 미실행 |
| 자동 결과의 앱 채택 | 미구현 | 현재 수동 validation 규칙을 우회하지 않는 별도 품질 정책 필요 |
| P1 선수 추적·장면 검색 | 미구현 | 경기장 정합과 독립적으로 진행 가능 |

기존 v0.2 구현 내용은 [V02_IMPLEMENTATION](../V02_IMPLEMENTATION.md),
[기능 평가](v02-evaluation.md), [영상 접근 개선/측정](improvement-plan.md)을 참고한다.
`fbecdf7`의 GitHub Actions [run 34336336016](https://github.com/conaonda/gsr-app/actions/runs/34336336016)은
이 코드 기준의 기능 검사 근거다. 이후 experiment 변경이나 자동 모델 정확도까지 그 성공으로 인증하지 않는다.

Experiment 001 준비 커밋 `48c78a6`의 PR 검사 [run 34368428939](https://github.com/conaonda/gsr-app/actions/runs/34368428939)도 `completed / success`를 확인했다. 이는 준비 도구와 기존 기능의 검사이며 자동 정합 추론·정확도 검증 결과가 아니다. 입력 준비 실행 및 테스트 기록은 [preparation-results.md](../experiments/001-auto-calibration/preparation-results.md), 실행 여부와 다음 작업은 [인계 문서](../experiments/001-auto-calibration/status-and-handoff.md)를 참고한다.

## 왜 순서를 바꾸는가

목표는 수동 캘리브레이션 편집기가 아니라 **경기 내용을 이해하고 선수·상황·사건에서 원본 장면을 역조회하는 것**이다.
PnLCalib, TVCalib, Broadcast2Pitch, Sportlight에는 자동 경기장 인지/정합 구현이 있다.
이들을 실제 입력에 시험하기 전에 사람에게 점/선 지정 작업을 반복시키는 기능에 더 투자하지 않는다.
실행 경로와 가중치·환경 제약은 [고정 후보 목록](../experiments/001-auto-calibration/candidates.json)에 명시한다.
공개 pretrained 모델이 있다고 우리 촬영·마킹에 zero-shot으로 충분하다고 가정하지도 않는다.

## 유지하는 자산과 안전 규칙

원본 PTS 인덱스·frame provider·캐시, 오버레이, 프레임별 초안, 이력과 JSON export는 유지한다.
기존 수동 모드는 평가 라벨·오류 조사·선택적 보정 수단으로 둔다. 모든 사용 프레임에 수동 anchor를 요구하지 않는다.

화면 확대와 native 이미지 좌표는 분리한다. H는 image→normalized pitch 방향이며 metric 자격과 행렬 단위는 별개다.
과거 SQLite는 덮어쓰지 않는다. 모델 예측을 validation으로 복사해서 usable을 얻지 않는다.
자동 후보는 우선 별도 sidecar로 기록한다. 후보 생성·자동 QC·사람 검증·미터 자격을 각각 구분한다.
실제 경기장 모델/치수가 확인되지 않으면 모델의 표준 105×68 값을 실측으로 간주하지 않는다.
원본과 식별 가능한 경기 프레임은 공개 저장소/CI/외부 추론 서비스에 전송하지 않는다.

## 새 우선순위

### 1. E0/E1 — 기존 자동 엔진을 실행해서 비교

첫 실행 순서는 PnLCalib와 TVCalib다. 공개 예제로 환경/좌표를 확인한 후 같은 native 샘플을 처리한다.
Broadcast2Pitch SFR와 Sportlight는 가중치/의존성/VRAM 준비 상태와 보완 가치를 확인해 추가한다.
각 모델의 코드 SHA·가중치 SHA256·전처리·필드 모델·오류·시간·메모리를 기록한다.
표본에 관측을 수동으로 넣거나 잘 나온 프레임만 성공률 분모에 남기는 것을 금지한다.

### 2. 실측 실패 원인에 따른 분기

인지 실패면 목표 도메인 적응을 검토한다. 다른 종목/규격이면 필드 모델·라벨 체계부터 대응시킨다.
프레임별 결과가 좋고 시간 연속성이 나쁘면 자동 시간축 정합과 재초기화를 강화한다.
결과를 보기 전 큰 DB migration, 독자 mixed solver, 선 스냅 UI나 상주 디코더를 먼저 만들지 않는다.
필요한 관측 계약은 자동 엔진의 실제 출력으로부터 최소한으로 도출한다.

### 3. P1 병행

`player detection → image-space tracklet → identity review → 등장 구간 검색 → 원본 재생`을 별도 작업으로 둔다.
정합 실패 구간에도 등장 구간 검색은 계속 가능해야 한다. 검증된 정합만 선택적으로 pitch 위치를 제공한다.
현재 30단계 전파 provider를 경기 전체 분석기처럼 무조건 확장하지 않고, 긴 순차 읽기/청크/중단 재개를 별도 검토한다.
SAM2, 등번호 인식, 이벤트 해설을 동시에 첫 실행의 필수 의존성으로 붙이지 않는다.

## 진행 상태를 기록하는 법

`문헌 확인`, `가중치 다운로드`, `환경 smoke`, `실제 추론`, `독립 정확도 평가`를 각각 구분한다.
이번 입력 준비의 실행 결과는 [preparation-results.md](../experiments/001-auto-calibration/preparation-results.md)에 기록한다.
`not_run`/`not_measured`를 0점 또는 성공으로 바꾸지 않는다.
추론 때의 수동 개입은 0회를 목표로 하되, 평가 전용 독립 라벨 작업은 추론 입력과 분리해 한 번 작성·재사용한다.

기능 branch와 PR 단위로 코드를 검토한다. 이 문서의 PR-01~05 명칭은 과거 계획 작업 단위이며 실제 GitHub PR 번호가 아니다.
후속 예: `experiment/auto-calibration-bakeoff`, `feat/pnlcalib-adapter`, `feat/player-tracklet-search`.

## 관련 문서

- [현재 API·모듈 계약](../CONTRACT.md)
- [v0.2 구현 기록](../V02_IMPLEMENTATION.md)
- [기능/초기 실제 영상 평가](v02-evaluation.md)
- [영상 접근 개선 결과](improvement-plan.md)
- [자동 정합 비교 실험](../experiments/001-auto-calibration/README.md)
- [실행 현황과 다음 작업 인계](../experiments/001-auto-calibration/status-and-handoff.md)
