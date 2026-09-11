# Experiment 001 — 수동 입력 없는 자동 경기장 정합 비교

상태: **입력 준비 및 PnLCalib 공개 예제 E0 완료. 실제 영상 E1/E2와 독립 정확도 비교는 미실행.**
2026-09-11 실행 근거는 [E0 결과](e0-results.md)에 기록한다.
기준 코드: `conaonda/gsr-app@fbecdf786e2690be94dba797f7d771d388b11841`.
관련 현황: [개발 현황](../../docs/development-status.md).

## 1. 결정과 검증할 질문

제품의 기본 흐름을 `수동 기준점 → 정합`에서 `자동 관측 → 자동 정합 후보 → 품질 검사 → 예외 검수`로 전환한다.
수동 점·선 편집기, 독자 mixed solver, 큰 observation-v2 DB 개편을 다음 선행 조건으로 두지 않는다.
기존 P0의 원본/PTS, 캐시, 오버레이, 이력은 자동 결과 검수·실패 분석·선택적 수정·평가 라벨에 재사용한다.
먼저 기존 자동 엔진을 시험하고, 실제 출력에 필요한 최소 sidecar 어댑터부터 만든다.

질문은 두 가지다. 공개 모델이 수동 점·선·박스·ROI 프롬프트 없이 휴대폰 영상에서 유용한 후보를 만드는가?
실패한다면 환경/좌표 어댑터, 인지 도메인 차이, 실제 필드 모델 불일치, 관측 부족, 영상 왜곡 중 무엇 때문인가?
행렬 반환, 많은 예측점, 성공한 디코딩 테스트를 정합 성공으로 세지 않는다.

## 2. 후보와 우선순위

코드 고정값은 [candidates.json](candidates.json)을 사용한다. 이는 **실행 준비 순서이지 정확도 순위가 아니다**.

| 순서 | 엔진 | 확인한 공개 경로 | 먼저 확인할 조건 |
| --- | --- | --- | --- |
| A1 | PnLCalib | SV/MV keypoint·line 가중치, `inference.py` 이미지/영상 예제 | 가중치 해시, batch=1 메모리, native 좌표 복원, 고정 105×68 모델 |
| A2 | TVCalib | `inference.ipynb`, pretrained `train_59.pt` 링크 | predicted segmentation 전체 경로. GT segment 입력과 분리 |
| B | Broadcast2Pitch SFR | `kpts.py`, `download_properties.py` | SFR 가중치, dataset 경로 의존성, 출력 좌표 방향. 추적/LLM 제외 |
| C | Sportlight 2023 | keypoint·line detector와 camera voting | 배포 checkpoint 확인, README의 Linux/Docker·24GB GPU 환경 대비 8GB 추론 실험 |

Sportlight의 24GB 환경 안내만으로 8GB 추론이 불가능하다고 단정하지 않는다. 반대로 3070에서 바로 된다고 약속하지도 않는다.
PnLCalib refinement on/off는 동일 detector의 ablation이며 두 독립 엔진으로 세지 않는다.
유사 계보/학습 데이터 엔진의 일치를 독립 정답 검증으로 취급하지 않는다.
코드/가중치 링크 존재와 실제 다운로드·모델 로드 성공을 구분한다. 코드·가중치·데이터의 사용 조건을 각각 확인하며,
이 패키지는 upstream 코드나 가중치를 복사·재배포하지 않는다.

## 3. 경기장 모델 불일치가 핵심 위험

PnLCalib `inference.py`는 105×68 경기장과 9.15 반지름의 중앙 원 등 표준 축구장 좌표를 정의한다.
유소년/풋살/복합 마킹 영상이 그 모델에 맞춰졌다고 실제 경기장 좌표가 확인된 것은 아니다.
길이·너비만 축소해도 내부 마킹 비율/형태가 같아지지 않을 수 있다.

첫 결과에는 `field_model_status=unverified`, `metric_eligible=false`를 명시한다.
불일치는 `model_mismatch`, 불분명한 방향은 `orientation_unknown`으로 따로 보고한다.
공통으로 의미가 확인된 마킹만 평가할 수 있지만 이를 전체 경기장 복원 성공으로 확대하지 않는다.
PnLCalib SV/MV는 학습 카메라 시점 분포의 구분이며 MV가 동시 멀티카메라 입력을 뜻하지 않는다.

## 4. 실험 단계

### E0 — 설치·좌표 대조군

공개 예제 또는 사용 조건을 확인한 방송 축구 샘플로 모델 로드·추론·native 좌표 오버레이를 먼저 확인한다.
이 단계가 실패하면 휴대폰 도메인 실패로 결론 내리지 않는다. 코드 SHA, 가중치 SHA256, 환경과 오류를 기록한다.
upstream 데모의 OpenCV 영상 루프/정수 FPS로 원본 시각을 복원하지 않고 exact manifest의 이미지를 공급한다.

### E1 — 고정된 원본 100장 zero-shot pilot

`20260717_153525.mp4`에서 실제 PTS 시간축으로 균등 최근접 최대 100장을 추출한다.
시간 공백 때문에 선택이 중복되면 중복을 제거하고 실제 수를 보고한다. 잘 된 프레임만 분모에 남기지 않는다.
처음/중간/마지막 3개는 환경 smoke에 사용한다. 모든 엔진은 동일한 샘플을 받는다.

수동 좌표/ROI 프롬프트, 모델별 프레임 선별, 평가 라벨을 이용한 프레임별 tuning은 금지한다.
임곗값 변경은 새 run ID로 개발 실험이라고 기록한다. 한 영상은 일반화 시험이 아니다.
학습/적응 뒤의 최종 검증에는 별도 경기 또는 겹치지 않는 촬영 구간을 고정한다. 인접 프레임 무작위 분할을 피한다.

### E2 — 시간축 평가

영상 1/8, 3/8, 5/8, 7/8 시점 중심 5초 창 4개를 정의하고, 실제 실행 시 그 안의 모든 원본 프레임을 처리한다.
100개 독립 샘플만으로 jitter/연속 추적을 평가하지 않는다. 수동 anchor 없이 자동 초기화/재초기화를 사용한다.
실제 팬/줌을 H 원소 변화량으로 벌점 주지 않는다. 지면 특징 재투영 잔차, 전후 전파 일관성, 방향 반전,
최장 실패 구간과 재초기화를 평가한다. 손에 든 휴대폰에 tripod 고정 위치를 강제하지 않는다.
원인 미확정 상태에서는 렌즈 전환 대신 기하 불연속으로 기록한다.

## 5. 입력 준비 실행

Python 3.11+ 표준 라이브러리, FFmpeg/FFprobe만 필요하다. 모델 환경과 앱 환경을 섞지 않는다.
새 출력 디렉터리만 허용하며 사용자 SQLite를 열지 않는다.

```powershell
python experiments/001-auto-calibration/prepare.py "D:\videos\20260717_153525.mp4" --output "data\auto-calibration\pilot-001" --count 100
python -m unittest discover -s tests -p "test_auto_calibration_prepare.py" -v
```

출력은 원본 SHA256·도구 버전·PTS/좌표 의미를 담은 `manifest.json`, 전체 `frame_index.json`,
resize/crop 없는 원본 표시 방향의 `native/000.png ...`다.
기본 3장은 별도의 단일 프레임 순차 추출과 PNG 바이트 해시까지 비교한다.
`prepared`는 추출 완료이며 `inference_status=not_run`을 유지한다. 이 sampler는 시각 누락/중복을
명시적으로 거부하고 FPS로 대체하지 않는다. 시간 창은 정의만 하며 이 단계에서 추출/추론하지 않는다.

영상·프레임·가중치·실험 출력은 Git 제외 `data/`에만 보관한다. 공개 CI는 합성 fixture만 사용한다.
자녀 영상은 GitHub/외부 추론 API/공개 CI로 전송하지 않는다.

## 6. 추론 어댑터 명세 — PnLCalib 단일 이미지 경로 구현

`run_pnlcalib.py`와 `coordinates.py`가 공개 이미지에서 검증됐다. 모델 출력·원본 좌표·
오버레이·환경/해시를 sidecar로 저장한다. E1 manifest 결합, batch 실행, 다른 엔진은 후속이다.

엔진별 별도 venv/conda와 worker를 사용한다. RTX 3070에서는 batch=1, 엔진 하나씩 시작한다.
장치명·dtype·로드 시간·프레임 처리시간·VRAM·RAM을 구분하고, CPU smoke를 3070 성능으로 환산하지 않는다.
OOM이면 단계별 실행/offload를 새 설정으로 측정한다. 측정하지 않은 FPS/VRAM은 기록하지 않는다.

PnLCalib 공식 단일 이미지 smoke 명령 예시:

```text
python inference.py --weights_kp <SV_kp> --weights_line <SV_lines> --pnl_refine --device cuda:0 --input_type image --input_path <native_frame.png> --save_path <overlay.png>
```

먼저 고정 SHA checkout 및 가중치 해시 기록이 필요하다. overlay만으로 판정하지 않는다.
어댑터는 raw heatmap/segment, camera/H, no-solution과 전처리 변환을 sidecar로 내보내고 원본 sample ID로 결합한다.

```text
sample_id, frame_index, pts, time_base, source_sha256
engine_id, upstream_commit, checkpoint_sha256, config_sha256
input_transform, model_field_id, model_field_dimensions, field_model_status
raw_prediction_path, transform_kind, transform_direction, native_transform
inference_outcome: candidate | no_solution | error
error_category: environment | oom | invalid_output | model_mismatch | ...
quality_state: unassessed | qc_pass | qc_fail
manual_prompt_count, processing_seconds, peak_vram_bytes
```

미실행은 별도의 `not_run`이며 성공/실패 비율에 포함하지 않는다. 이전 H/identity fallback을 새 관측 성공으로 세지 않는다.

## 7. 좌표·품질 안전 규칙

앱 규약은 native displayed pixel→normalized pitch다. 외부 출력의 방향/단위/원점과 전처리는 코드 및 합성 대응으로 검증한다.
T가 native→model, H가 model→pitch일 때만 `H_native=H*T`다. 반대 방향은 해당 역변환을 사용한다.
왜곡을 가진 camera 결과를 임의의 3×3 H로 축약하지 않고 왜곡/지면 투영의 합성 관계를 보존한다.
골대 상단 같은 비평면 관측은 floor H 대응점으로 섞지 않는다.

현재 `CONTRACT.md`의 `status != usable → image`를 완화하지 않는다.
모델 예측을 validation으로 복사해 usable을 얻는 편법을 금지한다. 실험 후보는 수동 저장 경로와 분리한다.
자동 채택 정책은 평가 후 별도 버전으로 설계하며 `qc_pass`, `human_verified`, `metric_eligible`을 구분한다.

## 8. 평가와 정답

제품 추론 중 수동 입력은 없애지만 객관적 정확도에는 독립 정답/검수가 필요하다.
평가 마킹은 엔진 결과를 보기 전에 고정하고 추론에는 절대 전달하지 않는다. 모든 엔진이 공통으로 사용한다.
자기 segmentation에 맞는 internal loss나 모델 간 일치만으로 정확도를 입증하지 않는다.
정답/실측이 없으면 `not_measured`로 기록하며 0 또는 성공으로 채우지 않는다.

| 지표 | 정의/주의 |
| --- | --- |
| output coverage | 유효 형식 후보 / 전체 실행 프레임. 정확도와 별개 |
| validated coverage | 독립 기준 충족 / 전체 평가 프레임. 미평가/판단 불가도 별도 보고 |
| 재투영 오차 | 실제로 확인된 line/arc와의 잔차. native px 및 영상 크기 정규화 기준 병기 |
| 큰 오류 | 좌우 뒤집힘·잘못된 마킹 의미·모델불일치·투영 폭주를 별도 분류 |
| false acceptance | QC 통과 중 독립 검수에서 틀린 비율. 분모/미검수 수 기록 |
| 수동 부담 | 추론 프롬프트·반복 보정·경기당 개입 횟수를 분리 |
| 시간 연속성 | E2에서 잔차·실패 길이·재초기화. H 차분만 사용하지 않음 |
| 자원 | load/inference/end-to-end 및 VRAM/RAM. 다른 PC 숫자를 한 순위로 섞지 않음 |

5px를 다른 해상도에 그대로 적용하지 않는다. P0/논문 임곗값은 해당 설정으로 표시하고 우리 허용 오차는
공통 좌표계·사용 목적에 맞춰 평가 전에 고정한다. 표본 한 영상의 오류 0건을 일반화 보장으로 쓰지 않는다.

## 9. 다음 의사결정

1. E0/A1: PnLCalib 공개 예제 smoke 및 native sidecar adapter.
2. E1/A1: 같은 100장에 수동 프롬프트 0회; 동일 detector로 refinement on/off 비교.
3. E0/E1/A2: TVCalib predicted-segmentation 경로를 동일 입력에 실행.
4. B/C: 가중치/메모리 조건 및 보완 가치를 확인해 추가.
5. 인지 실패면 domain adaptation, 모델불일치면 마킹/필드 모델 대응, 시간 단절이면 자동 temporal calibration을 투자한다.
6. 비교 후 필요한 최소 관측 계약·앱 통합을 구현한다. 수동 mixed solver를 다시 선행 조건으로 두지 않는다.
7. P1 image-space 선수 등장 구간 검색은 이 실험 완료와 독립적으로 진행한다.

## 10. 1차 자료

- [PnLCalib 코드](https://github.com/mguti97/PnLCalib/tree/8c87391d6f4ea40c5e4d65e61529916c7a49ce62), [inference.py](https://github.com/mguti97/PnLCalib/blob/8c87391d6f4ea40c5e4d65e61529916c7a49ce62/inference.py)
- [TVCalib](https://github.com/MM4SPA/tvcalib/tree/1222c5230af2742395d74918ed6f34eb2b9bf7f9)
- [Sportlight](https://github.com/NikolasEnt/soccernet-calibration-sportlight/tree/92ebbaed08ee80da2aa75ea820799dcf403b6f73)
- [Broadcast2Pitch 코드](https://github.com/yinmayoo185/SoccernetGSR/tree/dbfb65c05c847e8e50baa47aa91ace960cc5e600), [WACV 2026 정식 논문](https://openaccess.thecvf.com/content/WACV2026/html/Oo_Broadcast2Pitch_Game_State_Reconstruction_from_Unconstrained_Soccer_Videos_WACV_2026_paper.html)
- [SoccerNet 평가 프로토콜](https://github.com/SoccerNet/sn-calibration)

[입력 준비 결과](preparation-results.md)는 모델 정확도 결과가 아니다.
