# 남은 일 (2026-09-28 기준)

재설계(REDESIGN.md 9장)는 끝났다. 현재 배포: tuya2ildevice 0.3.11, rustuya-local 0.0.32(PyPI, HACS, 매니저 카탈로그 drop-in), rustuya-manager 0.2.1, pyrustuyabridge 0.4.0.dev2(PyPI).
rustuya-homeassistant 는 폐기 안내만 푸시했다(아카이브 안 함). 2026-09-27 에 저장소 이름을 rustuya-homeassistant 로 바꿨다가 같은 날
되돌렸다(HA 전용이 아니고 이름이 여러 개면 헷갈림): 저장소·PyPI·import·CLI·플러그인 id 모두 `rustuya-local`.
아래는 그 뒤에 남은 것을 마일스톤 순서로 정리한 것이다. 저장소 표기: **T** tuya2ildevice, **L** rustuya-local, **H** il-ha,
**R** rustuya-homeassistant, **M** rustuya-manager.

## M1 — 배포

rustuya-local 은 IL 생산자만 한다(2026-09-24 MQTT discovery 제거, 아래 참고). HA 는 il-ha 로 본다.

- [x] **L** drop-in zip(0.0.7): 매니저(특히 Docker)는 카탈로그의 GitHub 릴리스 zip 을 풀어 최상위 패키지의 `register` 를 부르고
      pip 로 의존성을 설치하지 못한다. `rustuya_local.register` 노출, tuya2ildevice 를 `rustuya_local/_vendor/` 에 담은 재현 가능한 zip
      (`scripts/build_dropin.py`), 릴리스 에셋으로 첨부. paho-mqtt·pyrustuyabridge 는 매니저가 이미 가진다.
- [x] **M** 매니저 카탈로그에 rustuya-local 추가(rustuya-homeassistant 는 유지, `fef727d`). catalog-sync 봇이 이후 릴리스를 따라감.
- [x] **L** 0.0.8: pip·drop-in 이중 설치 시 한 번만 등록, drop-in zip 을 매니저 플러그인 호스트로 로드하는 CI 테스트(tuya2ildevice 를
      zip 에서만 찾게 강제), 린트 정리. 매니저 카탈로그의 rustuya-homeassistant 항목에 "retired" 표시.
- [x] **L** 절차로 정함: drop-in 은 tuya2ildevice 를 고정해 담으므로, tuya2ildevice 릴리스 뒤에는 rustuya-local 패치 릴리스를 낸다
      (0.3.1→0.0.9, 0.3.2→0.0.10, 0.3.3→0.0.11). 카탈로그는 catalog-sync 봇이 따라간다.
- [x] **L** CI `package` job(0.0.13): wheel 을 새 venv 에 설치해(의존성은 PyPI 만) import·CLI 확인. 릴리스 전에도 돈다.
- [x] **L** HACS 검증 통과(0.0.11): `custom_components/rustuya/brand/`(아이콘: Rust 톱니바퀴 + Tuya t, 로고: Alfa Slab One 워드마크).
- [x] **L** PyPI 에 README 가 없던 문제(0.0.12): pyproject `readme`, 릴리스는 `twine check --strict`.
- [x] **R** 폐기 안내를 "rustuya-local + il-ha" 로 고침(`89731f7`).
- [ ] **R** 폐기 안내를 담은 마지막 PyPI 릴리스(PyPI 최신은 아직 0.0.1rc24)와 GitHub 아카이브. 사용자 결정.
- [x] **M** catalog-sync 자동 병합이 PR 의 체크가 나타날 때까지(최대 10분) 기다린다(`0c85d68`).

## M2 — 실환경 검증

지금까지의 검증은 테스트 HA(`pytest-homeassistant-custom-component`)와 in-memory·로컬 브로커에서 한 것이다.

- [~] 실제 HA + 실기기 + il-ha: hk1(임베디드 브리지, 기기 1개)에서 상태·가용성 확인. 여기서 생산자 인계 시 presence 가 offline 으로
      남는 버그를 찾아 고침(tuya2ildevice 0.3.2), 0.0.11 에서 blocking call·`MISSING_VALUE` 없음. 남은 것: 명령, 여러 기기·카테고리,
      HA·브로커 재시작 뒤.
- [ ] 실제 rustuya-manager 에서 플러그인 확인: 카탈로그 UI 로 drop-in 설치, TLS 브로커, `watch_devices` 로 기기 추가·삭제, 탭 표시,
      매니저 종료 시 offline. (지금까지는 매니저의 플러그인 호스트 코드로만 검증.)
- [ ] HA 통합(custom_components/rustuya)을 0.0.6 이상으로 업그레이드하는 경로와 `<config>/rustuya_converters` 핫리로드.
- [x] rustuya-homeassistant v1 사용자 이전 안내: [MIGRATING.md](MIGRATING.md)(옛 discovery 지우기, 엔티티 ID 변경, 컨버터 디렉터리).
      rustuya-homeassistant 폐기 안내에서 링크.
- [ ] 내장 오버라이드 4제품(커튼 3종, 창문 개폐기 `5rta89nj`)을 실기기로 확인.

## M3 — 정확성 버그

- [x] **T** tuya2ildevice 0.3.1 (rustuya-local 0.0.9 에 반영):
      - 범위 밖 값: `bzyd_45idzfufidgee7ir` 의 brightness 393, color `#-2ec-2e6ff` 같은 값을 light 읽기에서 clamp(brightness,
        색온도, 색). `tests/test_il_values.py` 가 324 픽스처 전부의 발행 값을 디스크립터와 대조(수정 전 33개 실패).
      - 디스크립터 `source` 가 Hub 의 `IlTopics.source` 를 따른다(프레젠스 토픽과 일치).
      - core 에 key 가 없는 fan·climate 전원 prop 이 `prop` 이 아니라 dp 이름(`switch`)이 된다. 26 픽스처의 IL 토픽이 바뀜
        (`il/<id>/prop` -> `il/<id>/switch`).
- [x] **T** 0.3.2: 생산자 인계(새 인스턴스 시작 후 옛 인스턴스 종료) 뒤 presence 가 offline 으로 남던 문제. Runner 가 자기 presence 를 구독해
      다른 쪽의 `offline` 에 `online` 으로 답한다. 이벤트 루프를 막지 않는 `preload()`.
- [x] **T** 0.3.3: 스위치의 device class 를 core 의 outlet 이 아니라 Tuya 카테고리 목록으로 정한다.
- [x] **T/L** 같은 IL prefix·source 에 생산자 둘 금지(tuya2ildevice 0.3.5, rustuya-local 0.0.15): 시작 전에 `producer_running` 이
      `<presence>/probe` 로 물어 살아 있는 Runner 가 `<presence>/alive` 로 답하면 `AnotherProducer` 로 거부. 데몬은 종료 코드 1, HA 는
      `ConfigEntryNotReady`(HA 가 재시도), 매니저 탭은 오류 표시 후 30초마다 재시도. 답이 없는 `online`(브로커와 함께 사라진 Last Will,
      0.3.5 이전 생산자)은 경고만 하고 시작. 인계 순서는 이제 "옛것을 멈추고 새것을 시작".

## M4 — 기능 보강

- [x] **T** 0.3.4: SDK 값 변환 16개 추가(24개 중 22개), SDK 를 오라클로 무작위·경계 입력 대조(CI 에서도 실행). 정확한 역변환이 있는
      것만 쓰기 허용, 나머지는 `unsupported` 로 거절.
- [ ] **T** `db_v1_data`, `db_v1_tariff`: SDK 파서 자체가 실제 페이로드에서 틀림. 실기기 데이터가 생기면 이식.
- [x] **L** 0.0.13 매니저 플러그인 탭: 설정(il prefix·source, 옵션) 편집 후 서비스 제자리 재시작, `custom_converters/` 파일 편집(로더 경고 표시),
      쓸 수 없는 설정은 재시도 대신 탭에 오류로 표시하고 기다림. `/api/rustuya-local/...`(`manager_plugin/api.py`).
- [x] **T** v1 `.py` 컨버터 이식 가이드(tuya2ildevice `docs/porting-v1-converters.md`, 예제는 테스트가 문서에서 읽어 검증). 로더 메시지가 링크.
      자동 변환 도구는 만들지 않음: v1 은 비동기 핸들러·dp 번호·쓰기가 섞인 코드라 기계 변환이 성립하지 않고, 대표 사례(커튼)는 내장됨.
- [x] **T/L** 오버라이드 팩 재도입(tuya2ildevice 0.3.5 `host.pack`, rustuya-local 0.0.15): tuya2ildevice `master` 의 `pack/` 을
      시작 시와 하루마다 `custom_converters/` 로 복사(SHA-256 확인, `.tuya2ildevice_pack.json` 원장에 적힌 파일만 쓰고 지움, 사용자 파일·
      사용자가 고친 팩 파일은 건드리지 않음, `requires`/`until` 로 tuya2ildevice 버전 범위 지정). 기본 켜짐: 데몬 `"pack"`, HA 튜닝 옵션,
      매니저 탭 옵션(상태에 마지막 동기화 표시). 팩은 비어 있는 채로 시작.

## 제거한 것

- **HA MQTT discovery (2026-09-24 제거)**: IL → HA discovery 변환은 Tuya 와 무관하고 HA 엔티티 규칙(il-ha core)이 필요해
  rustuya-local 이 il-ha 에 의존하게 만들었다. relay·mirror 때문에 서비스가 계속 떠 있어야 해서 il-ha 대비 이점도 없었다.
  코드(`discovery/` render·publisher·ops, CLI `discovery status|clear|restore`, 324 픽스처 패리티 테스트)는 커밋 `c646922` 에 있다.
  다시 필요하면 IL 소비자로서 il-ha 쪽 별도 도구로 되살린다.

## REDESIGN.md 7절의 열린 결정

- MQTT 클라이언트: paho 로 정해짐(tuya2ildevice `MqttTransport`).
- 의존 관리: tuya2ildevice·pyrustuyabridge 는 PyPI. il-ha 는 테스트 의존으로만 남아 형제 체크아웃을 쓴다.
- [ ] **H** il-ha 의 이름·번역 이식 여부: 아직 판단 안 함(il-ha 쪽 일).

## 참고

- 설계·결정·진행 기록: [REDESIGN.md](REDESIGN.md)
