# PnLCalib E0 — 공개 예제 GPU 실행과 좌표 어댑터 검증

2026-09-11. **공개 예제 E0 완료. 실제 휴대폰 영상 100장(E1)과 시간축(E2)은 미실행이다.**
원본 경기 영상·앱·SQLite를 변경하지 않았다. 별도 작업 복사본과 가상 환경에서 실행했다.

## 실행 결과

공식 공개 예제 `examples/messi_sample.png`(1073×603)에서 기본 SV 가중치와
기본 임곗값(keypoint 0.3434, line 0.7867), `pnl_refine=true`, batch=1, FP32를 사용했다.
수동 점·선·박스·ROI 프롬프트는 0회다. 모델·임곗값을 이 이미지에 맞춰 조정하지 않았다.

- 실제 keypoint/line 모델 가중치 strict 로드 성공.
- detector keypoint 20개, line 3개; 완성된 keypoint 20개.
- 카메라와 정합 후보 반환: `candidate`.
- 원본 해상도 오버레이가 공식 CLI 결과와 모든 픽셀에서 일치.
- 20개 합성 지면 좌표 probe의 왕복 오차 최대 약 7.4e-12 native px.
- 카메라 투영과 비평면 골대 지점 대조, detector 좌표 denormalization 일치 확인.
- 좌표 단위 테스트 6개 통과: 비균일 resize, 960폭 bypass, 중심 원점, 골대 높이,
  왜곡 모델 축약 거부, singular/nonfinite 거부.
- 합성 검은 이미지 음성 대조군은 `no_solution`을 반환했고 H를 생성하지 않았다.
- 전체 검사: Python 41개, JavaScript 12개, JavaScript 문법 및 브라우저(DPR2·모바일 포함) 통과.
  Windows venv 테스트 서버의 자식 프로세스 종료와 일시적인 파일 잠금 해제 대기를 보완한 뒤,
  `tests/run_checks.py`가 임시 DB 정리까지 종료 코드 0으로 완료됐다.

원본 위 선의 배치를 시각적으로 확인했지만, 독립 정답을 만들지 않았다.
`quality_state=unassessed`, `independent_accuracy=not_measured`,
`field_model_status=unverified`, `metric_eligible=false`를 유지한다.
좌표 왕복 검사는 변환 구현의 일관성을 확인하며 경기장 정확도 수치가 아니다.

## 식별 정보

| 항목 | 값 |
| --- | --- |
| upstream commit | `8c87391d6f4ea40c5e4d65e61529916c7a49ce62` |
| SV_kp SHA256 | `7ea78fa76aaf94976a8eca428d6e3c59697a93430cba1a4603e20284b61f5113` |
| SV_lines SHA256 | `d72f4ed71734a2e3df9fa084f666e9b8adaef21bf69bac8952d6d3f970ff7455` |
| 공개 이미지 SHA256 | `f017af829ceabb80f81d6b856661938828ab2004760e9d96d16d71fb42b94f8c` |
| 장치 | NVIDIA GeForce RTX 3070 Ti, CUDA runtime 12.8 |
| 주요 환경 | Python 3.13, torch 2.10.0+cu128, torchvision 0.25.0+cu128, Shapely 2.0.7 |

환경의 정확한 패키지 버전과 설정·어댑터 해시는 sidecar에 기록했다. 기존 CUDA 설치를
참조하는 별도 `--system-site-packages` venv를 만들고 Shapely만 그 venv에 추가했다.
upstream의 과거 Linux 전체 requirements를 이 Windows 환경에 그대로 설치하지 않았다.
이는 현재 환경에서의 실행 확인이며 upstream의 원래 환경을 완전히 재현했다는 뜻은 아니다.

## 측정 범위

| 구간/자원 | 단일 실행 측정 |
| --- | ---: |
| 두 모델 생성·가중치 로드·GPU 배치 | 1.550초 |
| 입력 변환·두 detector·keypoint 완성·정합 | 0.490초 |
| keypoint detector | 0.269초 |
| line detector | 0.061초 |
| 정합 solver | 0.080초 |
| PyTorch peak allocated VRAM | 2,024,650,752 bytes (약 1.89 GiB) |
| PyTorch peak reserved VRAM | 2,522,873,856 bytes (약 2.35 GiB) |
| 추론 후 프로세스 RSS | 1,755,258,880 bytes |

GPU 시간은 synchronize를 포함한다. 0.490초에 모델 로딩·import·원본 해시·raw 압축 저장은
포함되지 않는다. PyTorch 메모리 카운터는 다른 앱과 CUDA context 전체를 포함하지 않으며,
RSS는 해당 시점 값으로 peak RAM이 아니다. 반복 benchmark나 E1 처리율을 측정한 수치는 아니다.

## 어댑터 구현

`run_pnlcalib.py`는 고정 upstream의 모델·추론·solver 함수를 직접 사용한다. 소스 수정 없이
forward hook으로 heatmap을 보관하고 camera update 전후 좌표를 기록한다. 가중치는
`weights_only=True`, CPU에서 strict 로드 후 GPU로 보낸다. 출력 디렉터리는 새 경로만 허용한다.

공식 CLI는 PNG를 VideoCapture로 열 때 FFmpeg 오류 로그를 남겼지만 CV_IMAGES로
전환하여 올바른 이미지 크기를 얻었다. 공식 출력은 실제로 생성됐으며 실패로 판정하지 않았다.
어댑터는 `imread().shape`에서 원본 크기를 직접 얻어 이 backend 의존성을 피한다.

`coordinates.py`는 upstream의 resize/denormalization과 중앙 원점 좌표를 보존한다.
왜곡 계수가 모두 0일 때만 pinhole 카메라의 z=0 평면에서 native→normalized pitch H를 만든다.
골대 상단의 비평면 점은 전체 카메라 투영에는 남기고 지면 H 대응으로 쓰지 않는다.
파생 교차점의 upstream `p=1`은 실측 confidence가 아닌 placeholder로 명시한다.

공식 모델은 105×68 축구장을 사용한다. 이 E0의 성공으로 풋살장 모델 적합성이나
실제 경기 영상 좌표의 자격을 부여하지 않는다. 왜곡을 포함하는 다른 upstream 경로는 미지원이다.

## 로컬 산출물과 재현

`data/auto-calibration/e0-official/`: 공식 명령 로그와 오버레이.
`data/auto-calibration/e0-adapter/`: `sidecar.json`, `raw-heatmaps.npz`,
`overlay.png`, `detections.png`. raw heatmap에는 배경 채널도 보존한다.
공개 정지 이미지이므로 PTS/frame_index는 null이다. E1용 manifest 결합·batch worker는 아직 없다.

아래 명령은 저장소 루트에서 검증된 모델 환경의 Python을 활성화한 뒤 실행한다.
출력 경로가 이미 있으면 덮어쓰지 않고 중단하므로 재실행 시 새 경로를 지정한다.
원시 출력과 이미지 파일은 Git 제외 대상이어서 다른 checkout에는 자동으로 전달되지 않는다.
저장소에는 이 실행 기록과 어댑터·검증 코드가 남고, 산출물은 같은 입력과 가중치로 재생성한다.

```powershell
python experiments/001-auto-calibration/run_pnlcalib.py --upstream data/upstream/PnLCalib --image data/upstream/PnLCalib/examples/messi_sample.png --weights-kp data/weights/PnLCalib/SV_kp --weights-line data/weights/PnLCalib/SV_lines --pnl-refine --output data/auto-calibration/e0-new-run
python -m unittest discover -s tests -p test_pnlcalib_coordinates.py -v
```

코드는 고정 checkout과 다운로드된 가중치를 필요로 한다. 모델 호출은 네트워크나 GSR DB를
사용하지 않는다. 원본 이미지의 SHA256을 실행 전후 비교한다. 장치·오류·해 없음·후보를
분리하여 sidecar에 저장하며, 해가 없을 때 이전 H나 identity를 후보로 채우지 않는다.

## 출처와 사용 조건 확인 범위

- [고정 upstream](https://github.com/mguti97/PnLCalib/tree/8c87391d6f4ea40c5e4d65e61529916c7a49ce62)의 LICENSE는 GPL v2 텍스트다.
- 가중치는 upstream `v1.0.0` release의 SV_kp/SV_lines를 다운로드했다.
- 이미지는 upstream README가 지정한 저장소 내 공개 예제다.
- 해당 release/예제에서 별도로 명시된 가중치·방송 이미지 재배포 허가는 확인하지 못했다.
  이번에는 로컬 예제 실행만 했으며 외부 코드·가중치·방송 이미지/결과는 Git에서 제외된 로컬 data에만 보관했다.
  제품 배포에 필요한 조건 검토는 완료 상태로 올리지 않는다.

다음 작업은 E1: 정확한 PTS manifest와 어댑터 출력을 결합해 고정 100장에 적용하는 것이다.
구체적인 순서와 완료 조건은 [다음 작업 인계](status-and-handoff.md)에 기록한다.
독립 라벨이 없으면 후보 반환 현황만 보고하고 정확도는 계속 `not_measured`로 둔다.
