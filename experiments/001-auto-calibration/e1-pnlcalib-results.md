# PnLCalib E1 — 고정 100장 zero-shot 실행 결과

기록일: 2026-09-11 (KST)
상태: **E1-A 실행 완료, 독립 정확도 평가 대기**

이 문서는 고정 입력에 대한 PnLCalib zero-shot 실행 사실과 자원 측정만 기록한다.
`candidate`와 `no_solution`은 upstream solver의 실행 결과이며 정확도·정합 성공·제품 사용 가능
판정이 아니다. 독립 정답이 없으므로 `quality_state=unassessed`,
`independent_accuracy=not_measured`, `field_model_status=unverified`,
`metric_eligible=false`를 모든 run과 sidecar에 유지했다.

## 1. 실행 범위와 식별 정보

- 입력: `20260717_153525.mp4`, 319,755,337 bytes
- 원본 SHA256: `db1267e285d490937a5baa85f015af1592193ae5303921aeda7604e7e62e3c78`
- manifest: `data/auto-calibration/e1-input-20260911-181113/manifest.json`
- manifest SHA256: `bb3b62f489591a96c86310de710c5dd39240a78814d21a3db839989a9a05b3fd`
- 선택 입력: PTS 기준 균등 최근접 100장, `frame_index` 0–5267, time base `1/90000`
- native PNG: 2336×1080, resize/crop 없음; 준비 시 참조 프레임 0·2611·5267 PNG 바이트 검증 통과
- 수동 추론 프롬프트: 0회
- upstream: `8c87391d6f4ea40c5e4d65e61529916c7a49ce62`
- adapter commit recorded by the runs: `a882a74d0731386d2ef1199dece279d86f3e7a8b`
- SV keypoint SHA256: `7ea78fa76aaf94976a8eca428d6e3c59697a93430cba1a4603e20284b61f5113`
- SV line SHA256: `d72f4ed71734a2e3df9fa084f666e9b8adaef21bf69bac8952d6d3f970ff7455`
- upstream config SHA256: keypoint `ee9c1fa3f7147574370569c4161c8fa9d04fd8ba8f123a1cf7adb952efe90b73`, line `05ee5f459818ae6d92c40fbd566eb47f1cec9449127eeec32a0c0ae537c720a4`
- 실행 어댑터 파일 SHA256: `run_pnlcalib_e1.py` — `66352489feed3ec41555668d82d436e386382f91a3dc42d55892ec9f74ed99ca`
- run config SHA256: refinement off `2d63354885e1bceda57f8ab0ff4a1dc3d7116ef9fc207ccac11060470fa1e8d5`, refinement on `24d06d8a2336da4c5df150001e38ca1a38c2f3e0618c22c039392ebd8faac836`
- 설정: keypoint threshold `0.3434`, line threshold `0.7867`, CUDA batch=1, FP32
- 장치: NVIDIA GeForce RTX 3070 Ti, CUDA runtime 12.8; Python 3.13.7, torch 2.10.0+cu128, torchvision 0.25.0+cu128

## 2. 실행 결과

| 단계 | run ID | refinement | 선택 장수 | candidate | no_solution | error | not_run | 수동 프롬프트 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| smoke | `e1-pnlcalib-20260911-181856-smoke-off` | off | 3 (0·49·99) | 0 | 3 | 0 | 0 | 0 |
| smoke | `e1-pnlcalib-20260911-181955-smoke-on` | on | 3 (0·49·99) | 0 | 3 | 0 | 0 | 0 |
| full | `e1-pnlcalib-20260911-182028-full-off` | off | 100 | 0 | 100 | 0 | 0 | 0 |
| full | `e1-pnlcalib-20260911-182351-full-on` | on | 100 | 0 | 100 | 0 | 0 | 0 |

두 full run 모두 100개 frame sidecar, `results.jsonl`, raw heatmap, native overlay,
detections 이미지를 생성했다. 오류 traceback이나 OOM은 없었다. 고정 threshold에서 100장
모두 `no_solution`이었으므로 camera/H와 native transform은 생성되지 않았다.

refinement on/off는 서로 다른 엔진의 비교가 아니라 동일 detector 계열의 solver ablation이다.
두 full run의 raw detector 출력 파일 SHA256은 100/100개가 일치했다. keypoint 관측 수 분포도
양쪽 모두 `0:67, 1:29, 2:4`, line 관측 수는 `0:100`이었다. 이는 detector raw 출력과
ablation 입력이 같았다는 실행 확인이지 정합 정확도 검증은 아니다.

## 3. 자원 측정

`inference_seconds`는 CUDA synchronize를 포함한 engine detector·solver 구간이고,
`worker_seconds`는 한 frame의 read부터 output 저장까지의 worker wall time이다. 모델 로드는
별도 측정했다. RSS는 각 sidecar 시점의 관측값이며 호스트 전체 peak RAM을 뜻하지 않는다.

| 측정값 | refinement off | refinement on |
| --- | ---: | ---: |
| model load | 1.551 s | 1.569 s |
| inference 평균 (min–max) | 0.1635 s (0.1394–0.4037) | 0.1535 s (0.1389–0.4208) |
| worker 평균 (min–max) | 1.7607 s (1.7047–2.0131) | 1.7464 s (1.7011–2.0370) |
| solver 평균 (max) | 0.0000314 s (0.0000473) | 0.0000322 s (0.0000522) |
| peak allocated VRAM | 2,024,650,752 bytes (약 1.89 GiB) | 2,024,650,752 bytes (약 1.89 GiB) |
| peak reserved VRAM | 2,522,873,856 bytes (약 2.35 GiB) | 2,522,873,856 bytes (약 2.35 GiB) |
| 관측 최대 RSS | 1,756,856,320 bytes | 1,760,416,648 bytes |

이 수치는 이 Windows 환경의 batch=1 실행 측정이며 FPS 보장이나 다른 장비에 대한 성능
보장이 아니다.

## 4. provenance·출력 검증

- 네 run의 `run.json`과 모든 sidecar의 manifest SHA, sample ID, frame index, PTS, time base,
  source SHA256, native PNG SHA256을 입력 manifest와 대조했다.
- 100개 입력 PNG를 다시 해시해 manifest와 일치함을 확인했다.
- full OFF/ON 각각 100/100 sidecar와 raw/overlay/detections 출력이 존재했다.
- `manual_prompt_count=0`, `quality_state=unassessed`, `independent_accuracy=not_measured`,
  `field_model_status=unverified`, `metric_eligible=false`가 모든 sidecar와 summary에 남아 있다.
- 기존 `e0-official`, `e0-adapter`, `e0-blank`는 덮어쓰지 않았고 각 E1 실행은 새 output
  directory에서 끝났다.

## 5. 해석과 미측정 항목

이번 실행이 확인한 것은 고정 PnLCalib 코드·가중치·설정으로 해당 100장의 raw detector와
solver를 수동 입력 없이 끝까지 실행했다는 사실이다. 모든 샘플에서 solver가 camera를
반환하지 않았다는 사실만으로 인지 모델 실패, 표준 105×68 field-model 불일치, 임곗값 문제,
영상 도메인 문제 중 하나를 단정하지 않는다. `candidate coverage=0/100`은 기록할 수 있지만
정확도 0점이나 제품 실패율로 부르지 않는다.

다음 항목은 아직 측정하지 않았다.

- 독립 15–20장 평가 subset과 실제 line/arc/landmark 정답
- validated coverage, native/normalized reprojection error
- catastrophic failure, false acceptance, unjudgeable 분류
- 실제 field 치수 확인과 미터 단위 자격
- TVCalib predicted-segmentation 경로와의 동일 입력 비교
- E2 시간축 연속성, temporal propagation, 재초기화
- GSR 앱의 자동 `usable` 채택 또는 제품 통합

후속 평가 subset은 모델 결과를 보기 전 영상 조건으로 고정하고 추론 입력과 분리한다.
threshold나 adapter를 바꾸면 기존 run을 수정하지 않고 새 run ID로 기록한다.

## 6. 재현 명령과 보관 범위

저장소 루트에서 검증된 `.venv`를 사용하고, `<exact-source>`는 manifest의 SHA256과 일치하는
원본의 로컬 경로로 대체한다.

```powershell
.\.venv\Scripts\python.exe experiments/001-auto-calibration/run_pnlcalib_e1.py `
  --manifest data/auto-calibration/e1-input-20260911-181113/manifest.json `
  --source <exact-source> `
  --upstream data/upstream/PnLCalib `
  --weights-kp data/weights/PnLCalib/SV_kp `
  --weights-line data/weights/PnLCalib/SV_lines `
  --phase smoke `
  --output data/auto-calibration/<new-run-id>
```

`--pnl-refine`를 추가하면 refinement on이다. full run은 `--phase full`을 사용한다.
출력 경로는 이미 존재하면 중단하며, 영상·PNG·가중치·raw output은 Git 제외 `data/`에만
보관했다.

관련 문서: [실험 계획](README.md), [현재 상태와 인계](status-and-handoff.md),
[입력 준비 결과](preparation-results.md), [PnLCalib E0 결과](e0-results.md)
