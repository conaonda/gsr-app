# Experiment 001 — E1 zero-shot 실행 명세와 완료 기준

기준: `experiment/auto-calibration-bakeoff`의 PnLCalib E0 완료 상태에서 이어간다.  
목표: **수동 점·선·박스·ROI 프롬프트 없이, 고정된 휴대폰 영상 100장을 동일 조건으로 처리하고 후보 반환·오류·자원 사용과 독립 정확도를 분리해 평가한다.**

이 문서는 실행 순서와 완료 조건을 고정하기 위한 명세다. 실제 E1 결과가 생기기 전에는 정확도·성공률·3070 처리량을 미리 기입하지 않는다.

## 1. 범위와 비범위

### 이번 E1에 포함

- 기존 `prepare.py`가 만든 동일 100장 manifest를 그대로 사용
- PnLCalib SV detector의 zero-shot 실행
- `pnl_refine=false`와 `pnl_refine=true` 두 run을 **동일 detector 계열의 ablation**으로 기록
- 프레임별 `candidate | no_solution | error | not_run` 저장
- 원본 sample ID, frame index, PTS, time base, source SHA256과 sidecar 결합
- keypoint/line 관측 수, 실행시간, VRAM/RAM 및 원본 좌표 결과 저장
- 자동 QC의 입력값과 결과를 별도 기록
- 독립 평가 subset에서 재투영/큰 오류/오채택 평가
- 이후 TVCalib가 같은 schema와 같은 100장에 들어올 수 있는 결과 계약 고정

### 이번 E1에 포함하지 않음

- 모델별로 유리한 프레임을 사람이 골라 실행
- 프레임마다 수동 좌표·ROI·anchor 제공
- 결과를 본 뒤 해당 프레임의 threshold만 조정
- 자동 후보를 현재 앱의 `usable` 등록으로 자동 저장
- 표준 105×68 모델을 실제 경기장 실측으로 간주
- E1 100장의 결과만으로 시간축 jitter/연속 실패를 결론냄
- SAM2, 선수 추적, 등번호 인식, 이벤트 해설을 동시에 연결
- 수동 point+line solver나 observation-v2 대규모 DB 개편

## 2. 고정 입력과 run 식별

입력은 `20260717_153525.mp4`에서 실제 PTS 시간축으로 준비된 100장의 native PNG와 manifest를 사용한다.
기준 기록은 `preparation-results.md`와 `status-and-handoff.md`를 따른다.

각 실행은 다음 값으로 고유하게 식별한다.

```text
run_id
engine_id
upstream_commit
checkpoint_sha256[]
config_sha256
source_sha256
manifest_sha256
adapter_commit
pnl_refine
thresholds
runtime_environment
```

입력 파일·가중치·raw 출력·overlay는 Git 제외 `data/` 아래에 둔다. 저장소에는 schema, 실행 명령, 해시, 요약 결과만 남긴다.

## 3. E1-A — PnLCalib 100장 zero-shot

### A0. 실행 전 preflight

다음을 만족해야 100장 run을 시작한다.

1. E0에서 사용한 upstream commit과 SV 가중치 SHA256이 일치한다.
2. PnLCalib worker가 공개 E0 예제를 다시 읽을 수 있고 모델 strict load가 성공한다.
3. manifest의 원본 SHA256과 로컬 입력 100장의 sample ID·frame index·PTS가 준비 기록과 일치한다.
4. 출력 디렉터리는 새 경로이며 기존 run을 덮어쓰지 않는다.
5. CUDA 장치명·torch/CUDA 버전·dtype·batch size를 기록한다.
6. 수동 inference prompt count가 0으로 시작한다.

preflight 실패는 모델 정확도 실패가 아니라 `environment` 또는 `input_integrity` 실패다.

### A1. smoke 3장

100장 전체 실행 전에 기존 manifest의 처음·중간·마지막 샘플 3장만 같은 adapter로 처리한다.

확인 항목:

- 원본 방향과 해상도 보존
- sample ID/PTS sidecar 결합
- raw detector output 저장
- `candidate/no_solution/error`가 이전 H/identity fallback 없이 기록
- 원본 좌표 overlay 생성
- 비정상 수치·singular transform·nonfinite 출력 거부

3장 결과를 보고 모델 threshold를 프레임별로 조정하지 않는다. 공통 설정을 바꾸면 새 `run_id`를 만들고 개발 run으로 명시한다.

### A2. 100장 전체 실행

같은 고정 설정으로 100장을 모두 실행한다. 실행 순서는 입력 manifest 순서로 고정하며, 오류가 난 프레임 이후에도 가능한 경우 다음 프레임을 계속 처리한다.

프레임별 최소 결과:

```text
sample_id, frame_index, pts, time_base, source_sha256
engine_id, upstream_commit, checkpoint_sha256, config_sha256
pnl_refine, thresholds, dtype, device
input_transform, model_field_id, model_field_dimensions
field_model_status, metric_eligible
inference_outcome, error_category
keypoint_count, line_count
raw_prediction_path, overlay_path
transform_kind, transform_direction, native_transform
quality_state
manual_prompt_count
load_seconds, inference_seconds, solver_seconds, end_to_end_seconds
peak_allocated_vram_bytes, peak_reserved_vram_bytes, rss_bytes
```

`candidate`는 정확도 성공이 아니다. `no_solution`, `error`, `not_run`을 결과에서 삭제하지 않는다.

### A3. refinement ablation

PnL refinement 비교는 독립 엔진 비교가 아니라 같은 detector 출력에서 solver 효과를 보는 ablation이다.
가능하면 detector raw 결과를 재사용해 detector 차이를 제거한다.

두 run의 비교 항목:

- candidate coverage 변화
- invalid/no-solution 변화
- 독립 평가 subset의 reprojection 변화
- catastrophic error 변화
- solver 시간·자원 추가 비용

## 4. E1-B — 자동 QC v0

자동 QC는 **후보를 선별하는 실험 기능**이며 현재 앱의 `usable` 판정과 동일하지 않다.
초기 상태는 모든 후보에 `quality_state=unassessed`다.

QC v0에서 사용할 수 있는 신호:

- transform/camera 출력의 finite·invertible 여부
- 극단적 condition/singularity 또는 투영 폭주
- 모델 필드가 원본 화면에 투영될 때의 물리적으로 불가능한 배치
- 검출된 keypoint/line과 fitted model 사이의 내부 residual
- pitch orientation/좌우 뒤집힘 후보
- 모델 내부 confidence와 관측 개수

주의:

- 내부 residual이 낮다는 이유만으로 실제 정합이 맞다고 판정하지 않는다.
- 표준 필드 모델과 실제 경기장 불일치를 QC 실패와 인지 실패에서 구분한다.
- QC threshold는 독립 평가 subset을 보기 전에 고정하거나, threshold tuning용 subset과 평가 subset을 분리한다.
- `qc_pass != human_verified != metric_eligible`을 유지한다.

최소 집계:

```text
raw_output_coverage
qc_pass_coverage
qc_fail_count_by_reason
no_solution_count
error_count_by_category
```

## 5. E1-C — 독립 평가 subset

100장 전체를 수동 정답화하는 것부터 시작하지 않는다. 먼저 **15~20장**을 고정된 평가 subset으로 만든다.

선정은 모델 결과를 보기 전에 영상 조건을 기준으로 층화한다.
예:

- wide field / 많은 마킹
- medium zoom
- close zoom
- 중앙 원·원호가 보이는 장면
- 골문/페널티 영역
- 마킹이 적은 장면
- 선수/물체 가림
- 빠른 팬 또는 기하 변화 구간

평가용 관측은 추론 입력으로 절대 전달하지 않는다. 모든 엔진에 동일한 평가 자료를 사용한다.

평가 자료는 가능한 경우 다음을 포함한다.

- 의미가 확인된 실제 지면 line segment
- 실제 arc/circle 일부
- 명확한 지면 landmark
- `judgeable | unjudgeable` 상태와 이유
- 실제 경기장 모델/치수의 확인 상태

실제 치수가 확인되지 않았다면 미터 오차를 만들지 않는다.

### 독립 지표

| 지표 | 정의 |
| --- | --- |
| output coverage | 유효 candidate / 전체 100장 |
| validated coverage | 독립 평가를 통과한 프레임 / 전체 평가 대상 |
| native reprojection error | 확인된 line/arc/landmark와 투영 모델의 native pixel 잔차 |
| normalized reprojection error | 영상 크기로 정규화한 잔차 |
| catastrophic failure | 좌우 반전, 잘못된 필드 의미, 투영 폭주, 명백한 모델 불일치 등 |
| false acceptance | `qc_pass`인데 독립 평가에서 틀린 비율 |
| unjudgeable | 독립 판단에 필요한 마킹이 부족한 프레임 수 |
| manual burden | 추론 프롬프트 수와 평가 라벨 작업을 분리 기록 |

`unjudgeable`은 성공/실패로 몰래 재분류하지 않는다.

## 6. E1-D — TVCalib 동일 조건 비교

PnLCalib E1 결과 schema가 안정된 뒤 TVCalib를 같은 100장에 적용한다.

조건:

- GT segmentation 입력이 아니라 **predicted segmentation 전체 자동 경로** 사용
- 동일 native 입력과 sample ID 사용
- 같은 독립 평가 subset 사용
- 엔진별 사전처리/필드 모델/좌표 방향을 sidecar에 명시
- 서로 다른 엔진이 같은 결과를 냈다는 사실을 독립 정답으로 사용하지 않음

비교표는 정확도, coverage, 큰 오류, 자원 사용을 분리한다. 공개 논문의 서로 다른 데이터셋 숫자를 이 표에 섞지 않는다.

## 7. E1 완료 조건

PnLCalib E1은 다음이 모두 충족되어야 `completed`로 기록한다.

- [ ] 고정된 100장 모두 실행 시도됨
- [ ] 각 프레임이 `candidate | no_solution | error | not_run` 중 하나로 남음
- [ ] 수동 추론 프롬프트 0회가 확인됨
- [ ] upstream commit, checkpoint SHA256, config hash가 저장됨
- [ ] manifest의 sample ID/PTS/source hash가 sidecar와 연결됨
- [ ] raw 결과와 원본 좌표 overlay가 재현 가능하게 저장됨
- [ ] 처리시간·VRAM/RAM이 측정 범위와 함께 기록됨
- [ ] refinement on/off가 동일 detector 계열 ablation으로 기록됨
- [ ] 독립 평가 subset 15~20장이 모델 출력과 분리되어 고정됨
- [ ] output coverage와 validated coverage가 구분되어 보고됨
- [ ] catastrophic failure와 false acceptance가 별도 집계됨
- [ ] 실제 필드 모델 미확인 시 `metric_eligible=false` 유지
- [ ] 결과 요약 문서가 저장소에 추가되고 원본 영상/프레임/가중치는 Git에 포함되지 않음

TVCalib 비교는 별도 완료 조건으로 기록하며, PnLCalib E1 완료를 TVCalib 성공 여부에 종속시키지 않는다.

## 8. E1 결과에 따른 다음 분기

### 경우 A — single-frame zero-shot이 유용함

조건: 넓은/중간 시점에서 validated coverage가 실용적으로 나오고 catastrophic failure가 낮음.

다음: E2 시간축 평가로 이동한다. 4개×150프레임 전 구간에서 연속 실패 길이, 재초기화, 재투영 안정성을 본다.

### 경우 B — 검출은 괜찮지만 정합/필드 모델이 틀림

다음: field model adaptation과 solver/model-mismatch 처리를 우선한다. 더 많은 수동 anchor UI로 돌아가지 않는다.

### 경우 C — keypoint/line perception 자체가 자주 실패

다음: 유소년/풋살/휴대폰 도메인 라벨·합성 데이터·fine-tuning을 검토한다. 이때도 평가 subset과 학습 데이터를 분리한다.

### 경우 D — 프레임별 후보는 괜찮지만 시간적으로 끊김

다음: temporal calibration, propagation, automatic re-initialization에 투자한다. 단일-frame detector를 불필요하게 다시 학습하지 않는다.

### 경우 E — 자동 QC가 위험함

candidate는 계속 제공하되 자동 채택은 보류한다. 사람이 문제 구간만 검수하는 UX를 설계하고 `qc_pass`를 제품의 `usable`과 결합하지 않는다.

## 9. P1 병행 원칙

E1/E2가 끝날 때까지 선수 검색을 막지 않는다.
P1 첫 목표는 다음으로 제한한다.

```text
player detection
→ image-space tracklet
→ 사용자 identity review
→ 등장 구간 목록
→ 원본 구간 재생
```

경기장 정합은 검증된 구간에만 선택적으로 붙인다.
첫 P1에 SAM2, 등번호 인식, 자연어 이벤트 분석을 필수 의존성으로 만들지 않는다.

## 10. 실행 후 남길 문서

E1 실제 실행 후 다음 파일을 추가한다.

```text
experiments/001-auto-calibration/e1-pnlcalib-results.md
experiments/001-auto-calibration/e1-evaluation-schema.json   # 필요 시
experiments/001-auto-calibration/e1-tvcalib-results.md        # TVCalib 실행 뒤
```

각 결과 문서는 `실행함`, `측정함`, `판단하지 못함`을 구분한다. `not_run`과 `not_measured`를 0 또는 성공으로 바꾸지 않는다.

관련 문서:

- [실험 전체 계획](README.md)
- [PnLCalib E0 결과](e0-results.md)
- [현재 상태와 인계](status-and-handoff.md)
- [후보 고정 정보](candidates.json)
- [입력 준비 결과](preparation-results.md)
