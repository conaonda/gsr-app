# Experiment 001 — 입력 준비 실행 기록

후속: 2026-09-11의 별도 GPU 환경에서는 PnLCalib 공개 예제 E0 실행과 좌표 검증을
완료했다. [E0 결과](e0-results.md)를 참고한다. 아래 CPU/DNS 제약은 최초 준비 실행 당시 기록이다.

## 상태

**입력 준비와 준비 도구 테스트는 실행했다. 자동 정합 모델 추론·정확도 비교는 실행하지 않았다.**
이 기록은 모델 성공률, 경기장 좌표 정확도 또는 RTX 3070 성능의 증거가 아니다.

## 실제 첨부 영상

| 항목 | 확인 결과 |
| --- | --- |
| 입력 | `20260717_153525.mp4` |
| 크기 | 319,755,337 bytes |
| decoded frame 수 | 5,268 |
| time base | 1/90000 |
| 첫/마지막 indexed PTS 간격 | 약 175.844889초; 컨테이너 duration과 구분 |
| 선택 방식 | 실제 PTS 시간축의 균등 최근접 선택 |
| 선택/추출 수 | 100 / 100 |
| 출력 | native displayed 2336×1080 PNG, resize/crop 없음 |
| 원본 변경 검사 | 준비 전후 전체 SHA256 및 파일 크기/수정 시각 일치 |
| 별도 추출 비교 | frame 0, 2611, 5267이 각각 단일 프레임 순차 추출과 PNG 바이트 해시 일치 |
| 시간축 창 | 584–733, 1904–2053, 3220–3369, 4534–4683; 각각 150프레임, 정의만 완료 |
| 모델 추론 | `not_run` |
| 실제 필드 모델/미터 자격 | `unverified` / false |
| 독립 정답 라벨 | `not_created` |

준비는 첨부 파일의 격리 실행 환경에서 수행했다. 원본과 정상 GSR 세션을 변경하지 않았으며 서버/SQLite를 열지 않았다.
전체 준비 430.55초에는 해시 검사, ffprobe, PNG 추출 및 별도 순차 추출 3회가 포함된다.
이 환경의 단일 측정이며 앱 프레임 접근 benchmark 또는 GPU 추론시간과 비교하지 않는다.
`manifest.json`, `frame_index.json`과 100개 PNG는 로컬 산출물로 유지하고 공개 Git에는 올리지 않는다.

## 준비 도구 테스트

`python -m unittest discover -s tests -p "test_auto_calibration_prepare.py" -v`: **6개 통과**.

검사 범위: 시간축 기반 샘플링, 중복/역순 PTS 거부, 2^53보다 큰 정수 PTS 유지,
best-effort provenance 비승격, 기존 출력 덮어쓰기 금지,
합성 VFR 영상의 실제 FFmpeg 추출/단일 프레임 참조 일치/원본 불변성.
이는 이번에 추가한 도구의 격리 실행 결과다. 기존 앱 전체 테스트나 새 커밋의 CI를 실행했다는 뜻은 아니다.

## 추론이 남아 있는 이유와 다음 실행

이 실행 환경에서 PyTorch는 CPU 빌드이고 `torch.cuda.is_available()`은 false였다.
GitHub clone은 DNS 접근에 실패했고 PnLCalib checkpoint의 binary 다운로드도 완료되지 않았다.
공식 README와 추론 코드는 GitHub 연결로 조회했지만 가중치를 로드한 적은 없다.
따라서 모든 후보의 `checkpoint_sha256`은 null, `inference_status`는 `not_run`으로 남겼다.
이것은 엔진의 영상 정확도 실패나 3070에서의 OOM 결과가 아니다.

다음 실행은 고정 upstream SHA와 실제 가중치 해시를 기록한 PnLCalib 공개 예제 smoke다.
이후 native 좌표/raw prediction sidecar 어댑터를 통해 준비된 100장에 적용하고 TVCalib와 비교한다.
수동 좌표를 넣어 설치·모델 실패를 우회하지 않는다. 평가 라벨은 추론 입력과 분리한다.
