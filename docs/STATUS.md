# rustuya-local — 진행 상황

> **2026-09-21: 이 문서의 접근(rustuya-local 이 v2 엔진과 HA 엔티티 렌더러를 직접 가짐)은 `docs/REDESIGN.md` 의
> 새 체인(bridge → tuya2ildevice → il-ha)으로 대체될 예정이다. 아래는 폐기 전 구현의 기록이다.**

마지막 갱신: 2026-09-20. **미커밋. 실제 HA + rustuya-bridge 에서 실행해본 적 없음(자동 테스트만).**

엔티티 생성 엔진의 상세 상태·설계·남은 일은 `rustuya-homeassistant` 저장소의
`docs/tuya2ha-v2/STATUS.md` 가 단일 기록이다. 여기에는 이 저장소에 해당하는 부분만 적는다.

## 이 저장소에서 한 것
- `entity_engine.py`: `tuya2ha.v2` 플랜을 HA 엔티티 17개 플랫폼으로 렌더(quirk 적용 → classify → 엔티티).
- `bridge_device.py`: raw LAN dp ↔ core 값 변환(`tuya2ha.v2.adapter`), 낙관적 로컬 갱신, 수신시각 timestamp.
- 플랫폼 파일 17개, `const.SUPPORTED_PLATFORMS`, `translations/en.json`(core entity 번역), `icons.json`.
- 제거: `vendor/`(HA core 복사본), `generic_entity.py`, `platform_dispatch.py`, `text` 플랫폼.
- 검증: `tests/test_render_golden.py` — 실제 HA 엔티티 객체로 core fixture 324개 → 골든 1205개 **불일치 0**.
  실행: `PYTHONPATH=<rustuya-homeassistant>/src:. <homeassistant 설치된 python> tests/test_render_golden.py`

## 남은 일 (이 저장소 관점)
1. **실기기 검증** (가장 중요): enum 값의 LAN 표기/쓰기, dj 조명 hex, 주기 재전송 시 delta 센서 중복 누적, `support_local=false` 기기, 엔티티 이름·아이콘 표시.
2. `manifest.json` 의 `rustuya-homeassistant` 요구 버전을 v2 포함 릴리스로 올리기(현재 `>=0.0.1rc24` 는 v2 없음).
3. 미포팅 SDK `value_convert` 전략 18개 — 해당 기기는 raw 값 그대로(로그에 경고). 우선순위는 상세 문서 P1-7.
4. `test_render_golden.py` 를 CI/pytest 에서 돌릴 수 있게 정리(현재는 스크립트, 외부 저장소 경로/`homeassistant` 필요).
5. config_flow 등 기존 코드 중 `text` 플랫폼/구 tier 언급이 남은 곳 점검, diagnostics 에 quirk/미지원 전략 정보 노출 검토.
6. 커밋 정리(전부 미커밋).

## 알려진 차이(요약)
`camera` 는 엔티티 모양만(스트림/이미지 없음), `scene` 미지원, core 가 예외를 던지는 3곳은 unknown 으로 처리,
unique_id 형식이 `rustuya_local.{device_id}{key}`. 전체 목록은 상세 문서 §5–§6.
