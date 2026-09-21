# rustuya-local 전면 재설계 (v2 계획) — HA 독립 코어 + 모듈형

작성: 2026-09-21. 상태: **계획 (구현 시작 전, 승인 대기)**. 이전 v1 계획(HA custom component 전제)을 대체한다.
`docs/STATUS.md`, `rustuya-homeassistant/docs/tuya2ha-v2/STATUS.md` 의 "rustuya-local 이 엔진을 품고 엔티티까지 만든다"는 접근도 대체된다. 그 문서의 검증 자산은 계속 쓴다.

## 1. 결정 (사용자 확정)

| 항목 | 결정 |
|---|---|
| 체인 | `rustuya-bridge → tuya2ildevice → (ildevice IL) → il-ha` |
| 형태 | **HA 독립**이 기본. custom component 는 나중에 얹는 얇은 래퍼(확장). 모듈형으로 설계 |
| IL | 기존 `../ildevice` 규격. 필요하면 규격도 수정 |
| 변환기 | `../tuya2ildevice` (지금은 로컬 경로 의존, 나중에 pip) |
| il-ha | **별도 설치 / 통합(내장) 둘 다 가능**하게. il-ha 자체를 모듈화해서 맞춘다 |
| 다른 저장소 | tuya2ildevice / il-ha / ildevice 수정 허용 |
| 기본값(추천안 수락) | `expose_unused=False`(core와 동일), camera 는 IL 범위 밖(문서화), alarm/vacuum 은 tuya2ildevice 에 구현, 이름·아이콘은 il-ha 쪽 소관 |
| 자산 | 골든 1205 / 쓰기 58 / 오라클 / 생성기 / 어댑터는 안전망. 구현은 새로 |
| 커밋 | 요청 전에는 커밋하지 않는다 |

## 2. 목표 구조 (계층)

```
                 ┌─────────────── rustuya-local (앱, HA 독립 코어) ───────────────┐
 rustuya-bridge  │  config · 기기/브리지 관리 · 클라우드 스키마 · Runner(asyncio) │
 (LAN dps, MQTT) │        │                                                        │
   ◄── Transport ┼── tuya2ildevice.Hub (sans-IO 변환) ── Transport ──► IL 메시지 ──┼─► (A) MQTT 브로커 → il-ha(별도 통합)
                 │                                                                 └─► (B) 프로세스 내 직결 → il-ha 엔진(내장)
                 └────────────────────────────────────────────────────────────────┘
                       ▲ 위를 HA 에 올리는 얇은 래퍼: custom_components/rustuya_local (Phase 4)
```

핵심은 **전송(Transport)을 추상화**하는 것이다. 양쪽(Hub 쪽, il-ha 쪽) 모두 "토픽 구독/발행"만 알고, 그것이 MQTT 인지 프로세스 내 메모리인지 모른다.
- 별도 설치(A): il-ha 가 HA `mqtt` 로 `il/…` 를 구독. rustuya-local 은 그냥 IL 프로듀서(HA 없이 Docker 로도 동작).
- 통합(B): 같은 프로세스에서 브로커 없이 직결(in-process transport). rustuya-local 래퍼가 il-ha 엔진을 임베드.
IL 메시지 자체는 두 경우 동일하므로 il-ha 입장에서 두 모드는 같은 코드다.

## 3. 저장소별 작업

### 3.1 ildevice (규격) — 최소 수정
- 코드 없음, 규격만. 변경은 발견되는 만큼만: alarm/vacuum role 재확인, camera 는 범위 밖으로 명시(il-rationale.md 에 기록), 전송 추상화(il-mqtt.md 의 메시지는 어떤 전송에도 실린다)를 il-mqtt.md 에 한 절로 명시.
- 적합성 벡터에 새로 생기는 케이스(alarm/vacuum, 쓰기 리플레이)를 추가.

### 3.2 tuya2ildevice (변환기) — parity 와 런타임 API
1. 골든 대조를 **개수 → 속성 전체**(class/unit/options/features/기본 활성 여부/상태값)로 확장, 쓰기는 58 리플레이를 IL 명령→dps 로 대조.
2. 누락 해소: climate −3, cover −1, humidifier −3 (원인 조사), alarm 2, vacuum 2 구현. camera 는 명시적 미지원.
3. `expose_unused` 기본값 False (+ 옵션). 초과 +112 를 core와 대조해 정리.
4. Hub 에 **기기 런타임 추가/삭제/스키마 갱신** API (지금은 생성 시 고정 + reload 만 있음).
5. 남은 엔진 과제: 미이식 SDK 전략 18개, 델타 누적기 중복(주기 재전송 시 이중 누적 위험), 열거형 wire 형식 검증.
6. `examples/host.py` 는 rustuya-local Runner 가 대체하므로 예제로만 남긴다(중복 구현 금지).

### 3.3 il-ha — 모듈화 (별도/내장 겸용)
현재: `core/`(HA 무관: descriptor, plan, topics, values) + `hub.py`(301줄, HA `mqtt` 에 직결) + `entity.py`(754줄) + 플랫폼 shim(각 13줄) + `__init__`/config_flow.
목표:
```
il_ha/
  core/        HA 무관 (기존) + Transport 프로토콜(subscribe/publish/call_later) + 기기 상태 모델(디스크립터 추적, 가용성/유예, reject, 발견 결정)
  ha/          HA 의존: 엔티티 클래스(entity.py), 레지스트리/디바이스 연동, 플랫폼 셋업 함수
  transports/  HaMqttTransport(HA mqtt), (테스트용) InProcessTransport
custom_components/il_ha/   얇은 통합: config flow + attach(hass, entry, HaMqttTransport)
```
- 공개 진입점 `attach(hass, config_entry, transport, options) -> IlHub` 로 **다른 통합(rustuya-local)이 자기 config entry 로 il-ha 엔진을 임베드**할 수 있게 한다. 플랫폼 shim 도 `il_ha.ha.platforms.setup(platform, hub, add_entities)` 형태로 재사용.
- 이름/아이콘/번역: il-ha 가 소유. core 번역 재현은 필요 시 별도 과제(현재는 `humanize(prop)`).
- 리스크: 임베드 모드에서는 엔티티 플랫폼(domain)이 `rustuya_local` 이 되어 unique_id 는 같아도 레지스트리 항목이 il_ha 모드와 **다르다** → 모드 전환 시 엔티티 중복/이력 단절. 모드는 설치 시 고르고 전환은 지원 밖으로 문서화.

### 3.4 rustuya-local (앱) — 새로 작성
패키지 `rustuya_local/` (HA 무관) + 나중에 `custom_components/rustuya_local/` (래퍼).
- `config`: 브리지 연결(호스트/포트/루트), IL 접속/프리픽스, overrides/converters, 기기 목록(캐시). HA 없이 파일/환경변수/CLI.
- `devices`: 기기 등록·삭제·스캔·클라우드 스키마 캐시는 **rustuya-manager 의 Manager 파사드** 사용(직접 프로토콜 구현 금지).
- `bridge`: 외부 rustuya-bridge 가 기본, pyrustuyabridge 내장은 opt-in (기존 확정사항). 브리지 설정에서 `BridgeTopics` 템플릿 읽기.
- `runner`: asyncio. `Hub` + Transport 2개(브리지 쪽/IL 쪽)를 구동: `Subscribe/Publish/Schedule/Unschedule` 실행, LWT(M-12), 재연결 시 재구독·`start()`.
- `transports`: `MqttTransport`(paho), `InProcessTransport`(브로커 흉내: 와일드카드+retained, 테스트와 임베드용).
- `cli`: `rustuya-local run --config …` 데몬. Docker 로도 가능.
- HA 래퍼(Phase 4): config flow/옵션/서비스/진단은 위 코어를 호출만 한다. 모드 A(il_ha 별도, manifest `dependencies`) / 모드 B(il-ha 임베드).
- 폐기: `entity_engine.py`, `bridge_device.py`, 17 플랫폼 파일, 엔티티 번역, `icons.json`. 재작성: config_flow(1012줄), bridge_supervisor, bridge_client, services, diagnostics.
- 기존 코드는 커밋된 적이 없으므로 삭제 전에 `legacy/`(패키징 제외)로 옮겨 두고, 새 구조가 검증된 뒤 삭제한다.

## 4. 측정된 현재 체인 (2026-09-21, 개수 기준)

골든 1205 중 재현 1185, 오류 0, 생산 1317. light 65/65, fan 13/13, valve 14/14, siren 5/5, event 14/14, button 12/12.
부족: climate 17/20, cover 15/16, humidifier 4/7, alarm 0/2, vacuum 0/2, camera 0/9.
초과(expose_unused): switch +9, select +22, number +26, sensor +73, binary_sensor +2 = +112.
속성/상태/쓰기 비교는 아직 안 했다 → Phase 1 첫 작업.

## 5. 단계 (각 단계는 HA 없이 검증 가능한 것부터)

| 단계 | 내용 | 완료 기준 |
|---|---|---|
| **0. 안전망** | rustuya-local 을 패키지 구조로 전환, `legacy/` 이동, 경로 의존 설정, 체인 골든 하네스 이식(`tests/chain/`) | 체인 대조가 pytest 로 돌고 현재 수치가 기준선으로 고정 |
| **1. tuya2ildevice** | §3.2 1–4 | 골든 속성·상태 전량 일치(문서화된 예외 제외), 쓰기 58 일치, Hub 런타임 추가/삭제 테스트 |
| **2. il-ha 모듈화** | §3.3 (`core` Transport/상태 모델, `ha`, `attach`) | 기존 il-ha 테스트 통과 + HaMqtt/InProcess 두 전송으로 동일 결과 |
| **3. rustuya-local 코어** | §3.4 config/devices/runner/transports/cli | 모의 브리지(InProcess) → Hub → il-ha core plan 까지 HA 없이 E2E, 재연결/타이머/LWT 테스트 |
| **4. HA 래퍼** | custom component (모드 A/B), config flow, 서비스, 진단 | pytest-homeassistant-custom-component 로 두 모드 E2E |
| **5. 실기기** | 실 브리지·기기·HA | enum wire/dj hex/델타 중복/`support_local=false` 등 미검증 항목 확인 |

Phase 1 과 2 는 서로 독립이라 병행 가능. 3 은 1·2 의 인터페이스(Hub add/remove, Transport 프로토콜)가 굳은 뒤 시작한다.

## 6. 비용/이득과 리스크

- 이득: HA 없이 돌고 테스트된다(반복 속도↑), il-ha 를 쓰는 다른 프로듀서(rusthinq 등)와 구조가 같다, 엔티티 로직이 한 곳(il-ha)에만 있다.
- 비용: 세 저장소 동시 변경. 인터페이스(Hub API, Transport, attach)를 먼저 고정하지 않으면 서로 깨진다 → 각 단계 시작에 인터페이스 초안을 문서로 확정.
- core 재현도 하락: il-ha 는 core 이름/번역/아이콘을 모른다. "core 와 동일한 이름"은 이 구조에서 보장되지 않는다(속성 골든만 보장).
- 임베드/별도 모드 간 엔티티 레지스트리 비호환(§3.3).
- 검증 한계: 실기기 없이는 wire 형식과 델타 중복을 확정할 수 없다.

## 7. 열려 있는 결정 (기본안으로 진행, 나중에 변경 가능)

1. Runner 의 MQTT 클라이언트: paho(tuya2ildevice 예제와 동일, 기본안) vs aiomqtt.
2. 의존 관리: 각 pyproject 의 path 의존(기본안), 나중에 pip 로 교체.
3. il-ha 이름·번역 이식 여부: Phase 2 이후 실사용 보고 판단.

## 8. 진행 기록

- **2026-09-21 Phase 0 완료**: `custom_components/`, `hacs.json`, `test_render_golden.py` → `legacy/`(gitignore). `pyproject.toml`(패키지 `rustuya_local`, src 레이아웃, tuya2ildevice 를 `../tuya2ildevice` 경로 의존), `.venv`(uv). `tests/chain/`(measure.py, test_counts.py, baseline.json: 1185/1205, 오류 0, 생산 1317) — pytest 34 통과. 골든/픽스처는 `../tuya2ildevice/tests/golden` 를 사용하고 il-ha `core` 는 경로로 import(Phase 2 에서 패키지화하며 제거).
- 주의: `../tuya2ildevice` 는 **git 저장소가 아니다**(변경 이력 없음). 수정 전 백업을 세션 스크래치패드에 만들어 뒀다(`tuya2ildevice.backup.tgz`, `il-ha.backup.tgz`). Phase 1 전에 git init 여부를 정하자.
- **2026-09-21 tuya2ildevice git init(master) + 초기 커밋 `6ea06c9`(변경 전 기준선)**.
- **2026-09-21 Phase 1 1차 완료(커밋 안 함)**
  - 체인 비교를 개수에서 **속성 단위**로 확장(`tests/chain/props.py`, `test_props.py`): 골든 엔티티를 dp 코드(`src`/비트 라벨)로 IL 디스크립터에서 찾아 platform/class/category/unit 비교.
  - 처음 측정: 누락 151(대부분 비교 방법 문제 — 비트맵 라벨 대소문자, 합성 엔티티 key `""`), 실제 차이 class 18 / category 12 / platform 3 / unit 1 / 누락 20.
  - **수정**: (tuya2ildevice) alarm·vacuum 조립 추가(kind `alarm`, `disarm` 은 S-1 로 `allow_hazardous` 일 때만), 그룹 `class` 와 합성 엔티티 `category` 를 디스크립터에 싣기, `expose_unused` 기본 False; (il-ha) valve `device_class`, 합성 엔티티 `entity_category`(자기 속성의 category 에서).
  - **결과**: 1190/1205 재현(플랫폼 일치). 남은 15 = camera 9(IL 범위 밖), climate 3(`target_temperature` 없는 core climate), humidifier 3(`target_humidity` 없는 core humidifier). 나머지 차이는 unit 1(windspeed, HA 호스트 변환)뿐. class/category 차이 0. 이 예외는 `test_props.py` 에 명시돼 새 차이는 실패한다.
  - Hub: `set_device(device)`(추가/교체, 사라진 속성은 먼저 비움 M-11), `remove_device(id)`(값 비움 → 디스크립터 비움) 추가 + 테스트.
  - 테스트: tuya2ildevice 47 통과(alarm/vacuum 명령·상태 신규), il-ha 105 통과, rustuya-local 41 통과.
- **Phase 1 남은 것 / 발견**
  - **IL 규격 공백(fan)**: core 의 fan 은 percentage/oscillate/direction 을 한 엔티티에 갖지만 IL 의 fan 역할에는 select `fan_speed`/`mode` 뿐이라 `speed`(number)·`direction`·`oscillate` 가 별도 number/select/switch 로 나온다(6개 초과). 규격에 역할 추가(`speed`, `oscillate`, `direction`) + il-ha fan 반영 필요 — 결정 대기.
  - climate/humidifier 강등: 대응 dp 는 switch/select/sensor 로 남는다(기능 손실 아님, 표현 차이). 그대로 둘지 IL 요구 조건을 완화할지 결정 대기.
  - 쓰기(58 리플레이)와 상태값의 IL 단계 대조는 il-ha 엔티티(서비스→IL 명령)를 거쳐야 정확하므로 Phase 4(HA 통합 테스트)에서 한다. 지금은 엔진 단계 골든이 tuya2ildevice 에서 이미 통과.
  - 미이식 SDK 전략 18개, 델타 누적기 중복, enum wire 형식은 그대로(실기기 Phase 5).
- **2026-09-21 fan 역할 추가(결정 1 승인)**: ildevice il.md/schema/il-consumers/il-rationale 에 `speed`(number %), `oscillate`(binary), `direction`(select) 추가, tuya2ildevice 가 역할을 붙이고 il-ha fan 이 percentage/oscillate/direction 을 지원. 체인 초과 39 → 28. climate/humidifier 강등은 그대로 유지(결정 2).
- **2026-09-21 Phase 2 완료(il-ha 모듈화, 커밋 안 함)**
  - `il_ha/core/transport.py`(Transport 프로토콜, Message), `core/memory.py`(InProcessTransport: MQTT 필터·retained·빈 retained 삭제 의미 유지), `core/model.py`(**IlModel + Sink**: 디스크립터/값/가용성 유예/presence/reject/발견 결정, HA 무관).
  - HA 층은 `hub.py`(IlHub = Sink: 엔티티·레지스트리·디스커버리·디스패처), `mqtt_transport.py`(HaMqttTransport), `attach.py`(`build_hub(hass, options, transport, platform)` — 다른 통합이 임베드할 때의 진입점). `__init__` 은 HA 를 import 하지 않는다(`import il_ha.core` 가 HA 없이 됨, 테스트로 고정).
  - 패키징: il-ha pyproject 가 `custom_components/il_ha` 를 wheel 의 `il_ha` 로 설치(hatch). rustuya-local 이 `il_ha.core` 를 일반 의존성으로 쓰므로 경로 해킹 제거.
  - il-ha 테스트 116(기존 106 + 모델 10) 통과, 기존 HA 통합 테스트 무수정 통과.
- **2026-09-21 Phase 3 1차(rustuya-local 코어)**
  - `runner.py`(Hub 를 두 Transport 에 연결: 순서 보장 발행 큐, Schedule/Unschedule 타이머, 재연결 시 `hub.start()`, 런타임 set/remove/reload), `transports.py`(paho MqttTransport: 재연결 시 재구독·on_connect 훅, LWT), `config.py`, `cli.py`(`rustuya-local run --config`).
  - 테스트 51 통과: 체인(41) + 인프로세스 E2E(5: 값 흐름/명령→dps/reject/런타임 추가·삭제/정지 시 offline) + **실제 mosquitto** E2E(2: 값·명령, 소켓을 끊어 Last Will 발동→재연결 후 presence 복구) + 데몬 서브프로세스(SIGTERM 시 offline) + 설정(2).
  - 발견/수정: InProcessTransport.settle 이 done-callback 을 못 돌려 무한 루프 → `sleep(0)` 로 수정; Runner.stop 이중 호출 시 drain 교착 → 멱등화.
- **2026-09-21 il-ha 아이콘**: `core/icons.py`(이름 규칙 → 단위 → 종류 순), `EntitySpec.icon`, 엔티티 `_attr_icon`. device_class 가 있는 엔티티는 HA 기본 아이콘을 그대로 둔다. 실제 324기기의 445개 classless 엔티티 전부에 부여. il-ha 테스트 133.
- **2026-09-21 Phase 3 2차(기기 목록·브리지 설정)**
  - **설계 판단**: rustuya-manager 는 이미 독립 앱(CLI/웹/QR 마법사/브리지 동기화)이고 `tuyadevices.json`(스키마 포함 클라우드 레코드)을 원자적으로 쓴다. rustuya-local 이 기기 관리를 재구현하지 않고 **그 파일을 읽어 따라간다**(쓰지 않음). manager 라이브러리(pyrustuyabridge/aiomqtt/tuyawizard 의존)는 코어에 넣지 않는다 — Phase 4 HA 래퍼가 마법사에 필요할 때만 사용.
  - `devices.py`(`parse_devices`: category 없는 레코드는 건너뛰고 보고, `DeviceWatcher`: mtime/size 폴링 → `Runner.sync_devices` 로 추가/교체/삭제, 읽기 실패 시 현 상태 유지), `Runner.sync_devices`(실패한 레코드는 격리), `Hub.records`(tuya2ildevice), `bridge.py`(`{root}/bridge/config` retained 를 읽어 `mqtt_event_topic`/`mqtt_message_topic`/`mqtt_command_topic`/`mqtt_root_topic` 반영, 없으면 기본 레이아웃+경고).
  - 테스트 55 통과(신규: 파싱/템플릿/동기화/워처 4 + 데몬 E2E 1: 브리지 자체 템플릿으로 값 추적, 파일에 기기 추가·삭제를 실제 mosquitto 위에서 반영).
  - **미검증/미지원**: 브리지의 `mqtt_payload_template`(기본 `{value}` 아님) 미반영, single-dp 모드는 Hub 가 처리하나 실브리지로 확인 안 함, 내장 브리지(pyrustuyabridge) 수명주기 미구현(외부 브리지가 기본이라는 기존 확정에 따라 opt-in 과제로 남김).
- **2026-09-21 Phase 3 3차: 독립 구동 검증(실제 브리지 + 기기 에뮬레이터)** — custom component 는 미루고 데몬만으로 검증
  - 구성: `tuyamock`(Tuya LAN 프로토콜 에뮬레이터, 3.4) ↔ **실제 rustuya-bridge**(`pyrustuyabridge.PyBridgeServer`, 프로세스 내) ↔ mosquitto ↔ `rustuya-local run`(서브프로세스) ↔ mosquitto(il/…) ↔ 일반 MQTT 관찰자. 실제 브리지 없이 가짜로 흉내 낸 것은 Tuya 기기뿐.
  - `tests/e2e/test_full_stack.py`(7): IL 디스크립터+실제 상태 도착, 기기 쪽 변경(push)→IL, IL `set`→기기 dps(`brightness=50` → 507 + 스위치 on), 스위치 off, 범위 밖 명령은 reject 되고 기기에 안 감, 기기가 네트워크에서 사라지면 available=false → 복귀, **데몬 재시작 시 IL retained 를 다 지운 상태에서 브리지의 retained 스냅샷만으로 복원**, 데몬 stderr 에 예외/ERROR 없음.
  - `tests/e2e/test_full_stack_devices.py`(2): HA core 픽스처 38종(플랫폼당 최대 4개)을 각자 tuyamock 기기(픽스처의 dps 를 번호 매겨 local_strategy 생성)로 띄워 실제 브리지 → 데몬을 통과시키고, 나온 IL 값 273개가 **같은 dps 를 TuyaDriver 에 직접 넣은 결과와 전부 일치**(변이 테스트로 이 비교가 실제로 실패할 수 있음을 확인). 브리지가 Boolean/Integer/Enum/String/Raw/Json/Bitmap 을 wire 로 내보내는 방식이 엔진 가정과 맞음을 확인.
  - 발견: 버그 없음. 테스트 쪽 가정 오류만 있었다(밝기 500/1000 → 49, 정지한 MockDevice 는 재시작 불가 → 새로 생성).
  - **여전히 미검증**: 실제 Tuya 기기(3.3/3.5 프로토콜, 실제 펌웨어의 dp 형태), 브리지 `mqtt_payload_template` 비기본값, 쓰기 58 리플레이의 IL 단계 대조(Phase 4), 장시간(soak)/대규모(수백 기기) 동작.
- **2026-09-21 리팩터링(코드 위치 정리)**: `Runner`/`MqttTransport`/`InProcessTransport`/Transport Protocol/`DeviceWatcher`/`parse_devices`/`read_bridge_config` 를 **tuya2ildevice.host** 로 이동(extras `host`=paho), `BridgeTopics.from_config` 추가. rustuya-local 은 config+cli 144줄만 남고 **il-ha 의존 제거**(테스트 의존성만). il-ha 는 변경 없음 — 두 저장소가 공유하는 것은 규격 문서뿐이고 Protocol 은 구조적 타입이라 서로 import 하지 않는다. `tests/chain` 은 tuya2ildevice 로 이동(il-ha 없으면 수집 안 함). tuya2ildevice 137 / il-ha 174 / rustuya-local 20 통과.
- **2026-09-21 규격 벡터(ildevice/vectors)**: `wire-values.json`(값↔페이로드 4방향), `topics.json`(레이아웃·x-mqtt·presence·id 유효성), `composites.json`(kind/role→합성 성립 18케이스, 스키마 검증 통과). 손 복사본(`tuya2ildevice/tests/spec`) 삭제 — 두 구현체가 `../ildevice`(또는 `$ILDEVICE`)를 직접 읽는다(없으면 skip).
  - **벡터가 잡은 것**: (1) tuya2ildevice `encode_value(50.0)` 가 `"50.0"` 을 냄 → 규격(정수값은 소수부 없음) 위반, 수정. (2) il-ha 의 cover 는 `open` 하나만 있어도 성립하는데 규격은 `open` 과 `close` 둘 다를 요구 → 규격 K-2 를 "position, 또는 open, 또는 close 중 하나"로 완화(차고 열림 전용 리모컨 같은 실제 사례). 
  - 규격 모호점 → **해결(K-6)**: `lock`/`valve` 합성은 **쓰기 가능한** `locked`/`opened` 를 요구, 읽기 전용이면 일반 binary 속성(네이티브 읽기 전용 표현으로 보여주는 것은 소비자 자유). il-ha 는 이미 그렇게 동작했고 벡터 4케이스로 고정(il-ha 45 통과). il-rationale.md 에 이유 기록.
  - **S-1 과 valve → 해결**: 규격 S-1 의 목록에서 `opened` 를 뺐다(물 밸브는 일반 제어, 가스처럼 위험한 공급의 밸브는 S-1 의 일반 문장이 그대로 적용). tuya2ildevice 는 이미 valve 를 rw 로 내므로 코드 변경 없음. 이유는 il-rationale.md 에 기록. (S-3 이 이미 `locked` 에 대해 같은 말을 했음을 뒤늦게 확인 — K-6 이 이를 인용.)
- **남은 것**: (Phase 3) 내장 브리지(opt-in) 수명주기, 진단/상태 노출(devices 목록·연결 상태); (Phase 4) HA 래퍼(모드 A/B, config flow, il-ha `attach` 임베드) + 쓰기 58/상태 IL 단계 대조; (Phase 5) 실기기.
