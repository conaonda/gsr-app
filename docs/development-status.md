# 개발 현황과 다음 단계

기준: `master` @ `cc6caf6` (`Document real video evaluation findings`)

이 문서는 P0 구현 이후 논의한 개발 계획이 현재 저장소에 어느 정도 반영되었는지 정리하고, 다음 개발 순서를 고정하기 위한 상태 문서다. 세부 API 계약은 `CONTRACT.md`, v0.2 구현 범위는 `V02_IMPLEMENTATION.md`, 실제 영상 평가 결과는 `docs/v02-evaluation.md`를 우선한다.

## 현재 상태 요약

현재 저장소는 초기 P0의 수동 점 대응 정합에서 발전하여, v0.2 첫 개발 묶음인 **정확성 보완 + 비저장 미리보기 + 원본 편집기 + 원본 오버레이**까지 구현되어 있다.

| 계획 항목 | 상태 | 현재 구현 |
| --- | --- | --- |
| PR-01 정확성 기준 고정 | 완료 | `review/unavailable -> image` 좌표 자격 강제, 과거 결과 read-time 보정, RANSAC lazy/bounded hypothesis |
| PR-02 비저장 정합 API | 완료 | `POST /api/videos/{id}/registrations/preview`, `draft_version`, 저장/미리보기 공통 계산 경로 |
| PR-03 원본 정합 편집기 | 완료 | zoom/pan/fit/1:1, 점 선택·드래그·키보드 미세조정, undo/redo, 프레임별 초안 |
| PR-04 원본 오버레이·비동기 통합 | 완료 | inverse homography 기반 원본 오버레이, support/line/residual 토글, stale preview 방어, 독립 검증 모드 |
| PR-05 프레임 접근·연속 디코딩·캐시 | 미구현 | 현재 media layer는 기존 정확한 프레임 추출 경로 유지 |
| PR-06 실제 영상 평가·CI | 대부분 완료 | `tests/run_checks.py`, GitHub Actions, 실제 경기 영상 1차 평가 문서화 |
| v0.3 의미 기반 관측 | 미구현 | semantic landmark, point+line, arc, semi-auto snap은 후속 |
| P1 선수 추적·검색 | 미구현 | 영상 좌표 기반 player detection/tracking/search는 후속 |

## 반영된 핵심 설계 결정

### 1. 좌표 사용 자격과 정합 행렬을 분리

정합 행렬이 존재하더라도 `status != usable`이면 공간 분석용 좌표 자격을 부여하지 않는다. `review`와 `unavailable`은 `coordinate_level: "image"`로 제한한다.

과거 SQLite 결과 중 이전 정책으로 더 높은 좌표 자격이 기록된 경우, 원본 분석 JSON은 덮어쓰지 않고 조회 시 `eligibility_correction`을 붙여 정책을 적용한다.

### 2. 미리보기와 저장을 분리

정합 수정 과정에서 실제 geometry를 반복 계산할 수 있지만, 미리보기는 등록 이력을 생성하지 않는다.

- preview와 save는 동일한 입력 검증과 geometry 계산 경로를 사용한다.
- preview는 `persisted:false`이며 registration ID를 만들지 않는다.
- save 시 클라이언트가 보낸 행렬이나 상태를 신뢰하지 않고 서버에서 다시 계산한다.

### 3. 화면 좌표와 원본 좌표를 분리

zoom/pan은 저장되는 관측 좌표를 변경하지 않는다.

- homography `H`: 원본 표시 픽셀 -> 정규화 경기장 좌표
- viewport: 원본 표시 픽셀 <-> 편집 화면 좌표
- 원본 오버레이: 경기장 특징 -> `inverse(H)` -> 원본 표시 픽셀 -> viewport

### 4. 편집 초안과 저장 결과를 분리

현재 편집 초안, 해당 초안의 preview, 선택된 저장 결과는 서로 다른 상태로 관리한다. `video_id + source_revision + frame_index`를 기준으로 프레임별 초안을 구분한다.

오래된 preview 응답이 이후 편집이나 다른 프레임의 상태를 덮어쓰지 않도록 `draft_version`과 현재 video/frame 문맥을 확인한다.

### 5. 독립 검증은 유지

적합에 사용한 관측과 검증 관측을 분리한다. 독립 검증 입력 중에는 예측 오버레이를 숨길 수 있으며, 전파 결과는 자동으로 `usable`이 되지 않는다.

## 자동 검증 상태

현재 저장소에는 다음 검증 경로가 있다.

- Python geometry/API 테스트
- Node 기반 `editor-core` 테스트
- JavaScript syntax check
- Chromium 브라우저 흐름 테스트
- GitHub Actions에서 `python tests/run_checks.py` 실행

`master @ cc6caf6`의 GitHub Actions는 성공 상태다.

문서화된 2026-09-09 로컬 격리 실행에서는 다음이 통과했다.

- Python 테스트 20개
- Node editor-core 테스트 11개
- JavaScript syntax validation
- Chromium DPR 2 편집 흐름

이는 합성 fixture를 대상으로 한 기능 검증이며 실제 경기 정합 정확도 보장은 아니다.

## 실제 영상 1차 평가에서 확인한 문제

`20260717_153525.mp4`를 로컬 격리 DB로 평가한 결과, 편집기 기능은 실제 영상에서도 동작했지만 프레임 접근 비용이 크게 나타났다.

측정 예:

- import + 첫 프레임: 약 73.315초
- frame 150: 약 1.913초
- frame 2700: 약 8.007초
- frame 5100: 약 15.010초
- 인접 프레임: 약 0.980초

이 수치는 해당 장비·영상에 대한 측정값이며 성능 보장이 아니다. 다만 현재 media I/O 구조가 실제 사용자 흐름의 다음 병목이라는 근거로 사용한다.

같은 평가에서 영상에 경기장 일부만 보이고 카메라가 이동하기 때문에, 한 프레임에서 알려진 잘 분포된 대응점 네 개를 항상 확보하기 어렵다는 점도 확인했다. 따라서 v0.3에서는 단순히 점 입력을 더 편하게 만드는 것뿐 아니라 **semantic landmark / line / arc 같은 관측 계약 확장**을 검토해야 한다.

## 다음 개발 우선순위

### 1. PR-05: media access 개선

가장 먼저 프레임 탐색과 전파의 대기시간을 줄인다. 단, 기존의 정확한 프레임/PTS 대응을 기준 구현으로 유지한다.

우선순위:

1. 연속 구간을 한 번의 디코딩 세션으로 읽는 frame provider 추가
2. 주변 프레임 제한 캐시 도입
3. 탐색용 thumbnail과 정밀 입력용 원본 프레임 분리
4. 기존 추출 경로와 새 경로의 frame/PTS 동등성 테스트
5. 필요 시 그 이후에만 seek 최적화 검토

성능 개선 때문에 VFR, B-frame, 회전 메타데이터에서 원본 프레임 대응이 깨지지 않아야 한다.

### 2. v0.3: 관측 계약 v2

자동 line detector부터 만들기보다 먼저 데이터 계약을 확장한다.

관측이 표현해야 할 최소 정보:

- observation ID
- 종류: point / line / arc
- 연결된 경기장 feature ID
- 원본 영상 관측 좌표
- 역할: fit / validation
- 생성 방법: manual / snap / propagated / derived
- 부모 관측 ID
- field model ID와 version

기존 point observation은 계속 읽을 수 있어야 하며, 구버전 데이터에 없던 의미 정보를 임의로 추정해서 채우지 않는다.

이 계약 위에서 다음 순서로 확장한다.

1. semantic landmark 선택
2. 점 + 선 혼합 정합
3. 사용자가 지정한 좁은 ROI 내 line snap
4. 제약 부족/중복/비독립 검증 판정
5. arc fitting과 자동 후보는 별도 실험으로 평가

### 3. P1은 v0.3 완료를 기다리지 않는다

프로젝트의 최종 목적은 정합 도구 자체가 아니라 **경기 내용을 이해하고 나중에 선수·상황·사건으로 역조회하는 것**이다.

따라서 P1은 영상 좌표만으로도 시작할 수 있어야 한다.

```text
player detection / tracking
        ↓
image-space tracklet
        ↓
identity review
        ↓
appearance interval search
```

검증된 registration이 존재할 때만 선택적으로 pitch coordinate를 부여한다.

```text
tracklet + usable registration
              ↓
       optional pitch position
```

완전한 경기장 자동 정합을 P1의 선행 조건으로 두지 않는다.

## 개발 프로세스 개선

초기 v0.2 첫 묶음은 `master`에 직접 커밋되었고 현재 PR 이력은 없다. 저장소가 커지면서 geometry, editor, media, tracking 변경이 동시에 진행될 가능성이 높으므로 다음 단계부터는 feature branch + PR 단위를 권장한다.

예시:

- `perf/frame-provider-cache`
- `feat/observation-contract-v2`
- `feat/semantic-landmarks`
- `feat/player-tracklets`

각 PR은 기능 구현과 회귀 테스트를 함께 포함하고, 기존 source-of-truth 문서(`CONTRACT.md`, 평가 문서)를 변경 사항에 맞게 갱신한다.

## 현재 로드맵

```text
P0 v0.1
  └─ 기본 수동 점 정합                         완료

v0.2 first bundle
  ├─ 좌표 자격/계산 안전성                    완료
  ├─ 실제 비저장 preview                      완료
  ├─ zoom/pan/editor + undo/redo              완료
  ├─ 프레임별 draft                           완료
  ├─ 원본 overlay + residual                  완료
  ├─ async stale-result 방어                  완료
  ├─ CI                                       완료
  └─ 실제 영상 1차 평가                      완료

v0.2 remaining
  └─ media cache / continuous decode           다음 우선순위

v0.3
  ├─ observation contract v2                  예정
  ├─ semantic landmark                        예정
  ├─ point + line                             예정
  ├─ line snap                                예정
  └─ arc / auto candidate                     실험

P1
  ├─ player detection                         예정
  ├─ image-space tracklet                     예정
  ├─ identity review                          예정
  └─ appearance interval search               예정
```

## 관련 문서

- [`../CONTRACT.md`](../CONTRACT.md): API와 데이터 계약
- [`../V02_IMPLEMENTATION.md`](../V02_IMPLEMENTATION.md): v0.2 첫 개발 묶음의 구현 범위
- [`v02-evaluation.md`](v02-evaluation.md): 자동 검증과 실제 영상 평가 결과
