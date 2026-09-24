# 남은 일 (2026-09-24 기준)

재설계(REDESIGN.md 9장) 1차는 끝났다: tuya2ildevice 0.3.0, rustuya-local 0.0.6 PyPI 배포, il-ha·rustuya-homeassistant(폐기 안내) 푸시.
아래는 그 뒤에 남은 것을 마일스톤 순서로 정리한 것이다. 저장소 표기: **T** tuya2ildevice, **L** rustuya-local, **H** il-ha,
**R** rustuya-homeassistant, **M** rustuya-manager.

## M1 — 배포 완결 · 목표 0.0.7

rustuya-local 은 IL 생산자만 한다(2026-09-24 MQTT discovery 제거, 아래 참고). HA 는 il-ha 로 본다.

- [x] **L** drop-in zip(0.0.7): 매니저(특히 Docker)는 카탈로그의 GitHub 릴리스 zip 을 풀어 최상위 패키지의 `register` 를 부르고
      pip 로 의존성을 설치하지 못한다. `rustuya_local.register` 노출, tuya2ildevice 를 `rustuya_local/_vendor/` 에 담은 재현 가능한 zip
      (`scripts/build_dropin.py`), 릴리스 에셋으로 첨부. paho-mqtt·pyrustuyabridge 는 매니저가 이미 가진다.
- [x] **M** 매니저 카탈로그에 rustuya-local 추가(rustuya-homeassistant 는 유지, `fef727d`). catalog-sync 봇이 이후 릴리스를 따라감.
- [ ] **L** 새 venv 에 PyPI 만으로 설치해 `Service`·매니저 플러그인 import 를 CI 에서 확인하는 스모크 잡.
- [ ] **L** HACS 검증 통과: 브랜드 에셋(아이콘) 추가. 0.0.5 부터 실패하던 것이다.
- [ ] **R** 폐기 안내를 "rustuya-local + il-ha" 로 고친다(지금은 rustuya-local 의 discovery 로 옮기라고 안내함). 마지막 PyPI 릴리스 후
      아카이브는 사용자가 결정한다.

## M2 — 실환경 검증

지금까지의 검증은 테스트 HA(`pytest-homeassistant-custom-component`)와 in-memory·로컬 브로커에서 한 것이다.

- [ ] 실제 HA + 실기기 + il-ha 로 상태·명령·가용성(`_producer` LWT) 확인. HA·브로커·서비스 재시작 뒤도 본다.
- [ ] 실제 rustuya-manager 에서 플러그인 확인: TLS 브로커, `watch_devices` 로 기기 추가·삭제, 탭 표시, 매니저 종료 시 offline.
- [ ] HA 통합(custom_components/rustuya)을 0.0.6 이상으로 업그레이드하는 경로와 `<config>/rustuya_converters` 핫리로드.
- [ ] rustuya-homeassistant v1 사용자 이전: 기존 discovery 설정 제거 방법(HA MQTT 에서 retained config 삭제) 안내, il-ha 로 바꾼 뒤
      엔티티 ID 가 달라지는 것에 대한 안내.
- [ ] 내장 오버라이드 4제품(커튼 3종, 창문 개폐기 `5rta89nj`)을 실기기로 확인.

## M3 — 정확성 버그

- [ ] **T** `bzyd_45idzfufidgee7ir`: 범위를 벗어난 colour_data 가 디스크립터 범위 밖의 IL 값이 된다(brightness 393/max 100,
      color `#-2ec-2e6ff`). 읽을 때 clamp 한다. (발견 경위: 제거된 discovery 패리티 테스트.)
- [ ] **T** 디스크립터 `source` 가 `"tuya"` 로 고정돼 있다(`assemble.py`). 프레젠스 토픽은 설정한 `source` 를 쓰므로, `source` 를
      바꾸면 il-ha 가용성이 어긋난다. 디스크립터도 Hub 의 `source` 를 따르게 한다.
- [ ] **T** fan(`fs`) 전원 prop 이름이 `prop` 으로 나온다(테스트 `fan_levels`, dp `switch`). 이름 규칙 누락인지 확인하고 고친다.
      IL 토픽 이름이 바뀌므로 릴리스 노트에 적는다.
- [ ] **L** 같은 프레젠스에 생산자가 둘이면 지금은 경고만 한다(HA 통합 + 플러그인 동시 실행). 시작을 거부하는 옵션을 둘지 정한다.

## M4 — 기능 보강

- [ ] **T** 이식하지 않은 SDK 값 변환 전략 18개를 우선순위대로 이식한다(`docs/analysis/SDK_CONVERT_INVESTIGATION.md`).
      실제 기기에서 보이는 것부터 한다.
- [ ] **L** 매니저 플러그인 설정 UI. 지금은 읽기 전용 탭이고 `settings.json` 은 손으로 편집한다. 탭에서 il prefix 와 옵션을 바꾸고
      서비스를 재시작하게 한다.
- [ ] **T** v1 `.py` 컨버터(`setup(api)`)는 지금 보고만 하고 로드하지 않는다. 이전 가이드를 쓰고, 가능하면 흔한 패턴을
      `CONVERTERS` 로 바꾸는 도구를 만든다.
- [ ] **L** `pack.py`(GitHub 에서 오버라이드 팩 동기화) 재도입 여부를 정한다. 1차에서 제외했다.

## 제거한 것

- **HA MQTT discovery (2026-09-24 제거)**: IL → HA discovery 변환은 Tuya 와 무관하고 HA 엔티티 규칙(il-ha core)이 필요해
  rustuya-local 이 il-ha 에 의존하게 만들었다. relay·mirror 때문에 서비스가 계속 떠 있어야 해서 il-ha 대비 이점도 없었다.
  코드(`discovery/` render·publisher·ops, CLI `discovery status|clear|restore`, 324 픽스처 패리티 테스트)는 커밋 `c646922` 에 있다.
  다시 필요하면 IL 소비자로서 il-ha 쪽 별도 도구로 되살린다.

## 참고

- 설계·결정·진행 기록: [REDESIGN.md](REDESIGN.md)
