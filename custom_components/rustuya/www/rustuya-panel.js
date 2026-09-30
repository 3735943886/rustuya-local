// The Rustuya sidebar panel (panel.py): the bridge's devices against the cloud list (a simpler cut of rustuya-manager's
// device view: the same categories, order, filters and gateway/sub-device tree), then the files of
// <config>/rustuya_converters and the override pack (the rustuya-manager plugin tab's file API).
//
// Home Assistant sets `hass`, `narrow` and `panel` on the element; every call goes through hass.callApi, which carries
// the user's token. The views are for administrators only (converters run code in Home Assistant's process).

const ORIGIN = { pack: "origin_pack", pack_edited: "origin_pack_edited" };      // I18N keys
const PACK_CHANGES = ["added", "updated", "removed", "kept"];                    // a pack sync's lists, in this order
const CONVERTER_FILE = /\.(json|py)$/;

const STYLE = `
  :host { display: block; min-height: 100vh; background: var(--primary-background-color); color: var(--primary-text-color);
          font-family: var(--paper-font-body1_-_font-family, Roboto, sans-serif); }
  .toolbar .spacer { flex: 1; }
  .toolbar button { background: transparent; border-color: currentColor; color: inherit; }
  .toolbar { display: flex; align-items: center; gap: 4px; height: var(--header-height, 56px); padding: 0 12px;
             background: var(--app-header-background-color, var(--primary-color));
             color: var(--app-header-text-color, var(--text-primary-color)); font-size: 20px; }
  .content { max-width: 1100px; margin: 0 auto; padding: 16px; box-sizing: border-box; }
  .card { background: var(--card-background-color); border-radius: var(--ha-card-border-radius, 12px);
          border: 1px solid var(--divider-color); padding: 16px; margin-bottom: 16px; }
  .card.drop { outline: 2px dashed var(--primary-color); outline-offset: -6px; }
  h2 { font-size: 18px; font-weight: 500; margin: 0 0 8px; }
  .muted { color: var(--secondary-text-color); font-size: 14px; }
  .warn { color: var(--warning-color, #b58100); font-size: 14px; }
  .error { color: var(--error-color, #db4437); font-size: 14px; }
  .row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .split { display: grid; grid-template-columns: 260px 1fr; gap: 16px; margin-top: 12px; }
  :host([narrow]) .split { grid-template-columns: 1fr; }
  .files { list-style: none; margin: 0; padding: 0; border: 1px solid var(--divider-color); border-radius: 8px;
           overflow: auto; max-height: 520px; }
  .files li { padding: 8px 10px; cursor: pointer; border-bottom: 1px solid var(--divider-color);
              display: flex; justify-content: space-between; gap: 8px; font-size: 14px; word-break: break-all; }
  .files li:last-child { border-bottom: 0; }
  .files li:hover { background: var(--secondary-background-color); }
  .files li.sel { background: rgba(var(--rgb-primary-color, 3, 169, 244), 0.15); }
  .tag { font-size: 12px; color: var(--secondary-text-color); white-space: nowrap; }
  input, textarea { font: inherit; color: var(--primary-text-color); background: var(--card-background-color);
                    border: 1px solid var(--divider-color); border-radius: 6px; padding: 6px 8px; box-sizing: border-box; }
  input { flex: 1; min-width: 160px; }
  input[type=checkbox] { flex: none; min-width: 0; margin: 2px 0 0; }
  textarea { width: 100%; min-height: 420px; margin-top: 8px; resize: vertical; tab-size: 2;
             font-family: var(--code-font-family, ui-monospace, monospace); font-size: 13px; }
  button { font: inherit; font-size: 14px; padding: 6px 14px; border-radius: 6px; cursor: pointer;
           border: 1px solid var(--divider-color); background: var(--card-background-color); color: var(--primary-text-color); }
  button.primary { background: var(--primary-color); border-color: var(--primary-color); color: var(--text-primary-color, #fff); }
  button:disabled { opacity: .5; cursor: default; }
  .note { margin-top: 8px; }
  .cloud { border: 1px solid var(--divider-color); border-radius: 8px; padding: 12px; margin: 12px 0; display: grid; gap: 8px; }
  .cloud[hidden] { display: none; }
  .cloud .qr { width: 220px; height: 220px; image-rendering: pixelated; background: #fff; padding: 8px; border-radius: 8px; }
  .cloud .qr[hidden] { display: none; }
  .settings { display: grid; gap: 12px; margin-top: 12px; }
  .check { display: flex; gap: 10px; align-items: flex-start; cursor: pointer; }
  .check .muted { font-size: 12px; }
  .setting { display: grid; grid-template-columns: 180px minmax(0, 1fr); gap: 4px 12px; align-items: center; }
  .setting > .muted { grid-column: 2; font-size: 12px; }
  :host([narrow]) .setting { grid-template-columns: 1fr; }
  :host([narrow]) .setting > .muted { grid-column: 1; }
  /* bridge devices: rustuya-manager's category colors (sky / rose / amber / emerald) */
  .cat-missing { --cat: #0ea5e9; } .cat-orphan { --cat: #f43f5e; } .cat-mismatch { --cat: #f59e0b; } .cat-synced { --cat: #10b981; }
  .head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .head h2 { margin: 0; }
  .head .end { margin-left: auto; display: flex; gap: 8px; align-items: center; }
  .panel-menu { position: relative; margin-left: auto; }
  .panel-menu summary { cursor: pointer; list-style: none; padding: 8px 12px; border-radius: 6px;
                        border: 1px solid var(--divider-color); font-size: 20px; }
  .panel-menu summary::-webkit-details-marker { display: none; }
  .menu-items { position: absolute; right: 0; top: 100%; z-index: 2; display: grid; gap: 8px;
                width: min(260px, calc(100vw - 72px)); box-sizing: border-box; padding: 12px;
                background: var(--card-background-color); border: 1px solid var(--divider-color);
                border-radius: 8px; box-shadow: 0 4px 12px #0003; }
  .menu-items button, .menu-items select { width: 100%; white-space: normal; text-align: start; }
  .dev .top { flex-wrap: wrap; }
  .dev .acts { flex-wrap: wrap; max-width: 100%; }
  dialog { box-sizing: border-box; max-height: calc(100dvh - 32px); overflow: auto; }
  dialog .settings input { min-width: 0; width: 100%; }
  dialog input::placeholder { color: var(--secondary-text-color); opacity: 1; }
  .icon-button { display: inline-flex; align-items: center; justify-content: center; padding: 8px; }
  .icon-button ha-icon { --mdc-icon-size: 20px; }
  .head .icon-button { min-width: 42px; min-height: 42px; }
  .chips { display: flex; gap: 6px; flex-wrap: wrap; margin: 12px 0 8px; }
  .chip { padding: 3px 10px; font-size: 13px; border-radius: 999px; border: 1px solid var(--cat, var(--divider-color));
          background: transparent; color: var(--primary-text-color); }
  .chip.on { background: var(--cat, var(--primary-text-color)); color: #fff; }
  .chip.all.on { background: var(--primary-text-color); color: var(--card-background-color); }
  .chip.zero:not(.on) { opacity: .5; }
  .chip .n { margin-left: 4px; font-variant-numeric: tabular-nums; }
  select { font: inherit; font-size: 13px; padding: 4px 6px; border-radius: 6px; border: 1px solid var(--divider-color);
           background: var(--card-background-color); color: var(--primary-text-color); }
  .act { border-color: var(--cat, var(--divider-color));
                          background: color-mix(in srgb, var(--cat, transparent) 12%, var(--card-background-color)); }
  .devices { display: flex; flex-direction: column; gap: 6px; }
  .dev { border: 1px solid var(--divider-color); border-left: 4px solid var(--cat); border-radius: 8px; padding: 8px 10px;
         background: color-mix(in srgb, var(--cat) 8%, var(--card-background-color)); cursor: pointer; }
  .dev.cat-synced { background: var(--card-background-color); }
  .dev.cat-synced.offline { --cat: #94a3b8; }
  .dot { width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0; box-sizing: border-box; }
  .dot.online { background: #10b981; }
  .dot.offline { border: 2px solid #94a3b8; }
  .dev.child { margin-left: 24px; }
  .dev .top { display: flex; align-items: center; gap: 6px; min-width: 0; }
  .dev .name { font-weight: 500; font-size: 14px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
  .dev .tree { color: var(--secondary-text-color); }
  .dev .acts { margin-left: auto; display: flex; gap: 4px; align-items: center; flex-shrink: 0; }
  .dev .acts button { padding: 6px; min-width: 32px; min-height: 32px; }
  .dev .id { font-family: var(--code-font-family, ui-monospace, monospace); font-size: 11px; color: var(--secondary-text-color);
             word-break: break-all; }
  .pill { font-size: 11px; padding: 1px 8px; border-radius: 999px; color: #fff; background: var(--cat); white-space: nowrap; }
  .fields { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 2px 12px; margin-top: 6px; font-size: 12px; }
  :host([narrow]) .fields { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .fields .wide { grid-column: span 2; }
  .fields .full { grid-column: 1 / -1; }
  .dev .err { margin-top: 2px; font-size: 11px; color: var(--error-color, #db4437); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .fields b { font-weight: 400; color: var(--secondary-text-color); margin-right: 4px; }
  .fields span { font-family: var(--code-font-family, ui-monospace, monospace); word-break: break-all; }
  .reasons { margin-top: 6px; font-size: 12px; padding: 4px 8px; border-radius: 6px; word-break: break-all;
             border: 1px solid var(--cat); background: color-mix(in srgb, var(--cat) 12%, var(--card-background-color)); }
  .placeholder { border: 2px dashed #0ea5e9; border-radius: 8px; padding: 8px 10px; font-size: 13px; }
  dialog { border: 1px solid var(--divider-color); border-radius: 12px; padding: 16px; width: min(560px, calc(100vw - 32px));
           background: var(--card-background-color); color: var(--primary-text-color); }
  dialog::backdrop { background: rgba(0, 0, 0, .4); }
  dialog h3 { margin: 0 0 8px; font-size: 16px; font-weight: 500; }
  dialog .foot { display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px; }
`;

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v === true) node.setAttribute(k, "");
    else if (v !== false && v != null) node.setAttribute(k, v);
  }
  for (const c of children) node.append(c instanceof Node ? c : document.createTextNode(String(c ?? "")));
  return node;
}

// hass.callApi rejects with {error, body} (the view's json_message in body.message)
const message = (e) => (e && e.body && e.body.message) || (e && (e.message || e.error)) || String(e);

// ---- text in the user's Home Assistant language (hass.language): Korean, else English -----------------------------

const I18N = {
  en: {
    origin_pack: "pack", origin_pack_edited: "pack, edited",
    close_panel: "Close panel",
    menu: "Panel menu", edit_device: "Edit device", edit_hint: "Edit the bridge values. Device ID and type cannot be changed.",
    manual_add: "Register manually", manual_hint: "Register without Tuya Cloud. Leave IP/version blank for automatic detection. ID may be generated from IP or CID and name.",
    cat_all: "all", cat_missing: "missing", cat_orphan: "Bridge only", cat_mismatch: "mismatch", cat_synced: "synced",
    cloud_code: "User code (only for a new login)", cloud_fetch: "Fetch", cancel: "Cancel",
    cloud_qr_alt: "QR code to scan with the Smart Life or Tuya Smart app",
    cloud_intro: "A saved Tuya login is reused. Without one (or when it has expired) a QR code to scan follows; the user code is only needed for that first login (Smart Life → Me → Settings → Account and Security).",
    cloud_done: "Fetched the device list from Tuya Cloud", cloud_failed: "Not fetched: {error}",
    cloud_state_requesting_qr: "Connecting to Tuya…", cloud_state_awaiting_scan: "Scan the QR code with the Smart Life or Tuya Smart app",
    cloud_state_logged_in: "Logged in; fetching the devices…", cloud_state_fetching: "Fetching the devices…",
    cloud_state_finishing: "Fetching the devices…", cloud_state_cancelled: "Cancelled",
    refresh: "Refresh", fetch_title: "Fetch the device list from Tuya Cloud (a saved login is reused)",
    fetch_button: "Fetch from Tuya Cloud", sort_title: "Sort",
    sort_id: "ID", sort_name: "Name", sort_category: "Category",
    bridge_title: "Bridge devices", bridge_intro: "The cloud device list against what rustuya-bridge holds.",
    bridge_sending: "Sending to the bridge…", bridge_reading: "Reading the bridge…",
    bridge_no_cloud: "No cloud device list yet: fetch it with Fetch from Tuya Cloud.",
    bridge_sent_one: "Sent 1 command to the bridge", bridge_sent_many: "Sent {n} commands to the bridge",
    bridge_nothing: "Nothing to send",
    no_category: "No category is selected.", no_device_in_categories: "No device in the selected categories.",
    no_devices: "No devices in the cloud list or on the bridge.",
    missing_gateway: "missing gateway",
    missing_gateway_note: "Sub-devices below name this gateway, which is in neither the cloud list nor the bridge.",
    link_online: "Connected to the bridge", link_offline: "Not connected to the bridge",
    act_add: "Add", act_update: "Update", act_remove: "Remove",
    confirm_remove_device: "Remove {who} from the bridge?",
    set_bridge_root: "Bridge topic root", set_bridge_root_hint: "The MQTT root rustuya-bridge uses (its mqtt_root_topic).",
    set_il_prefix: "IL topic prefix", set_il_prefix_hint: "Where the IL devices are published; il-ha reads il by default.",
    set_il_source: "IL source name", set_il_source_hint: "This producer's name on IL (one topic level).",
    set_devices_path: "Device file", set_devices_path_hint: "The tuyadevices.json rustuya-manager keeps; relative to the config directory.",
    set_bridge_state_file: "Bridge state file", set_bridge_state_file_hint: "Where the embedded bridge keeps its device registry.",
    set_bridge_log_level: "Bridge log level", set_bridge_log_level_hint: "error, warn, info or debug.",
    opt_allow_hazardous: "Allow remote control of locks, alarms and garage doors",
    opt_allow_hazardous_hint: "Off: their state is shown, but IL and rustuya.send_command refuse writes to them.",
    opt_expose_unused: "Expose data points Home Assistant core would not classify",
    opt_expose_unused_hint: "Every data point the cloud schema lists, not only the ones a standard entity uses.",
    opt_pack: "Download fixes for non-standard devices published between releases",
    opt_pack_hint: "The override pack: copied into the converters directory at start and daily.",
    save_restart: "Save and restart", options_title: "Options", options_intro: "Saving restarts the integration.",
    options_unreadable: "Cannot read the options: {error}", nothing_changed: "Nothing changed",
    saving_restarting: "Saving; Rustuya is restarting…", not_saved: "Not saved: {error}",
    settings_title: "Settings",
    settings_intro: "Set up with their defaults. Saving restarts the integration; moving the IL prefix or source clears what the old one left on the broker, so IL consumers drop those devices and see them again under the new one.",
    settings_unreadable: "Cannot read the settings: {error}", confirm_settings: "Save {names} and restart Rustuya?",
    sync_now: "Sync now", hide_title: "Remove this panel from the sidebar; the integration's Configure adds it back",
    hide: "Hide panel", pack_title: "Override pack",
    pack_intro: "Fixes for non-standard devices, published between releases and copied into the converters directory.",
    converters_title: "Custom converters",
    converters_intro: "Override blocks (*.json) and code converters (*.py) in rustuya_converters/. Saved files apply within seconds. Code converters run inside Home Assistant. See tuya2ildevice's README, \"User overrides\". Drop files here to copy them in.",
    new_file: "New file", save: "Save", delete: "Delete",
    only_converters: "{name} (only *.json and *.py files are converters)",
    confirm_replace_pack: "{name} comes from the override pack. Replaced, it is yours: the pack no longer updates or removes it.",
    confirm_replace: "{name} already exists. Replace it?",
    copied: "Copied {names}", not_copied: "Not copied: {names}",
    confirm_hide_unsaved: "Unsaved changes in {names} will be lost. Remove the Rustuya panel from the sidebar anyway?",
    and: " and ", not_hidden: "Not hidden: {error}", files_unreadable: "Cannot list the files: {error}",
    no_files: "No files yet. Built-in fixes apply without any.",
    pack_syncing: "Syncing…", pack_off: "Off. Turn it on in Options below.", pack_never: "Not synced yet.",
    pack_added: "added", pack_updated: "updated", pack_removed: "removed", pack_kept: "kept",
    pack_failed: "; failed: {list}", pack_error: "Not synced ({error}), {when}", pack_synced: "Synced {when}",
    origin_note_pack: "From the override pack: the next sync updates it. Saving an edit makes it yours.",
    origin_note_pack_edited: "An edited pack file: the pack no longer updates it. Delete it to get the pack's copy back at the next sync.",
    name_first: "Name the file first (*.json or *.py)",
    confirm_edit_pack: "{name} comes from the override pack. Once edited it is yours: the pack no longer updates or removes it.",
    saved: "Saved {name}", confirm_delete: "Delete {name}?", deleted: "Deleted {name}", not_deleted: "Not deleted: {error}",
    pack_is_off: "The pack is off", cannot_sync: "Cannot sync: {error}", pack_changed: "The pack changed the converter files",
  },
  ko: {
    origin_pack: "팩", origin_pack_edited: "팩, 수정됨",
    close_panel: "패널 닫기",
    menu: "패널 메뉴", edit_device: "기기 수정", edit_hint: "브리지에 등록된 값을 수정합니다. 기기 ID와 유형은 변경할 수 없습니다.",
    manual_add: "수동 등록", manual_hint: "클라우드 없이 등록합니다. IP·버전을 비우면 자동 탐색합니다. ID는 IP 또는 CID와 이름으로 자동 생성할 수 있습니다.",
    cat_all: "전체", cat_missing: "없음", cat_orphan: "브릿지 전용", cat_mismatch: "불일치", cat_synced: "일치",
    cloud_code: "사용자 코드 (새 로그인일 때만)", cloud_fetch: "가져오기", cancel: "취소",
    cloud_qr_alt: "Smart Life 또는 Tuya Smart 앱으로 스캔할 QR 코드",
    cloud_intro: "저장된 Tuya 로그인이 있으면 그대로 씁니다. 없거나 만료됐으면 스캔할 QR 코드가 이어서 나옵니다. 사용자 코드는 그 첫 로그인에만 필요합니다(Smart Life → 나 → 설정 → 계정 및 보안).",
    cloud_done: "Tuya Cloud에서 기기 목록을 가져왔습니다", cloud_failed: "가져오지 못했습니다: {error}",
    cloud_state_requesting_qr: "Tuya에 연결하는 중…", cloud_state_awaiting_scan: "Smart Life 또는 Tuya Smart 앱으로 QR 코드를 스캔하세요",
    cloud_state_logged_in: "로그인했습니다. 기기를 가져오는 중…", cloud_state_fetching: "기기를 가져오는 중…",
    cloud_state_finishing: "기기를 가져오는 중…", cloud_state_cancelled: "취소됨",
    refresh: "새로 고침", fetch_title: "Tuya Cloud에서 기기 목록 가져오기 (저장된 로그인 재사용)",
    fetch_button: "Tuya Cloud에서 가져오기", sort_title: "정렬",
    sort_id: "ID", sort_name: "이름", sort_category: "분류",
    bridge_title: "브리지 기기", bridge_intro: "클라우드 기기 목록과 rustuya-bridge가 가진 기기를 비교합니다.",
    bridge_sending: "브리지에 보내는 중…", bridge_reading: "브리지를 읽는 중…",
    bridge_no_cloud: "아직 클라우드 기기 목록이 없습니다. 'Tuya Cloud에서 가져오기'로 가져오세요.",
    bridge_sent_one: "브리지에 명령 1개를 보냈습니다", bridge_sent_many: "브리지에 명령 {n}개를 보냈습니다",
    bridge_nothing: "보낼 것이 없습니다",
    no_category: "선택한 분류가 없습니다.", no_device_in_categories: "선택한 분류에 기기가 없습니다.",
    no_devices: "클라우드 목록에도 브리지에도 기기가 없습니다.",
    missing_gateway: "게이트웨이 없음",
    missing_gateway_note: "아래 서브 기기들이 가리키는 게이트웨이가 클라우드 목록에도 브리지에도 없습니다.",
    link_online: "브리지에 연결됨", link_offline: "브리지에 연결되지 않음",
    act_add: "추가", act_update: "갱신", act_remove: "삭제",
    confirm_remove_device: "{who}을(를) 브리지에서 삭제할까요?",
    set_bridge_root: "브리지 토픽 root", set_bridge_root_hint: "rustuya-bridge가 쓰는 MQTT root(mqtt_root_topic).",
    set_il_prefix: "IL 토픽 prefix", set_il_prefix_hint: "IL 기기를 게시하는 곳. il-ha는 기본으로 il을 읽습니다.",
    set_il_source: "IL source 이름", set_il_source_hint: "IL에서 이 생산자의 이름(토픽 한 단계).",
    set_devices_path: "기기 파일", set_devices_path_hint: "rustuya-manager가 관리하는 tuyadevices.json. config 디렉터리 기준 상대 경로.",
    set_bridge_state_file: "브리지 상태 파일", set_bridge_state_file_hint: "내장 브리지가 기기 목록을 저장하는 곳.",
    set_bridge_log_level: "브리지 로그 레벨", set_bridge_log_level_hint: "error, warn, info, debug 중 하나.",
    opt_allow_hazardous: "잠금장치·경보·차고문 원격 제어 허용",
    opt_allow_hazardous_hint: "끄면 상태는 보이지만 IL과 rustuya.send_command가 쓰기를 거부합니다.",
    opt_expose_unused: "Home Assistant core가 분류하지 않는 데이터 포인트도 노출",
    opt_expose_unused_hint: "표준 엔티티가 쓰는 것만이 아니라 클라우드 스키마의 모든 데이터 포인트.",
    opt_pack: "릴리즈 사이에 나온 비표준 기기 수정 다운로드",
    opt_pack_hint: "오버라이드 팩: 시작할 때와 매일 컨버터 디렉터리에 복사됩니다.",
    save_restart: "저장하고 재시작", options_title: "옵션", options_intro: "저장하면 통합구성요소가 재시작됩니다.",
    options_unreadable: "옵션을 읽을 수 없습니다: {error}", nothing_changed: "바뀐 것이 없습니다",
    saving_restarting: "저장하는 중. Rustuya를 재시작합니다…", not_saved: "저장하지 못했습니다: {error}",
    settings_title: "설정",
    settings_intro: "설정 과정에서 기본값으로 정해진 값들입니다. 저장하면 통합구성요소가 재시작됩니다. IL prefix나 source를 옮기면 예전 값으로 브로커에 남은 것을 지워서, IL 소비자가 그 기기를 지우고 새 값으로 다시 보게 됩니다.",
    settings_unreadable: "설정을 읽을 수 없습니다: {error}", confirm_settings: "{names}을(를) 저장하고 Rustuya를 재시작할까요?",
    sync_now: "지금 동기화", hide_title: "사이드바에서 이 패널을 없앱니다. 통합구성요소의 구성(Configure)에서 다시 추가할 수 있습니다",
    hide: "패널 숨기기", pack_title: "오버라이드 팩",
    pack_intro: "릴리즈 사이에 나온 비표준 기기 수정으로, 컨버터 디렉터리에 복사됩니다.",
    converters_title: "커스텀 컨버터",
    converters_intro: "rustuya_converters/ 안의 오버라이드 블록(*.json)과 코드 컨버터(*.py). 저장한 파일은 몇 초 안에 적용됩니다. 코드 컨버터는 Home Assistant 안에서 실행됩니다. tuya2ildevice README의 \"User overrides\"를 참고하세요. 여기에 파일을 끌어다 놓으면 복사됩니다.",
    new_file: "새 파일", save: "저장", delete: "삭제",
    only_converters: "{name} (*.json과 *.py 파일만 컨버터입니다)",
    confirm_replace_pack: "{name}은(는) 오버라이드 팩의 파일입니다. 바꾸면 내 파일이 되어 팩이 더 이상 갱신하거나 지우지 않습니다.",
    confirm_replace: "{name}이(가) 이미 있습니다. 바꿀까요?",
    copied: "{names} 복사함", not_copied: "복사하지 못함: {names}",
    confirm_hide_unsaved: "{names}의 저장하지 않은 변경이 사라집니다. 그래도 사이드바에서 Rustuya 패널을 없앨까요?",
    and: ", ", not_hidden: "숨기지 못했습니다: {error}", files_unreadable: "파일 목록을 읽을 수 없습니다: {error}",
    no_files: "아직 파일이 없습니다. 기본 수정은 파일 없이도 적용됩니다.",
    pack_syncing: "동기화하는 중…", pack_off: "꺼져 있습니다. 아래 옵션에서 켜세요.", pack_never: "아직 동기화하지 않았습니다.",
    pack_added: "추가", pack_updated: "갱신", pack_removed: "삭제", pack_kept: "유지",
    pack_failed: "; 실패: {list}", pack_error: "동기화하지 못함 ({error}), {when}", pack_synced: "{when}에 동기화함",
    origin_note_pack: "오버라이드 팩의 파일입니다. 다음 동기화 때 갱신됩니다. 수정해서 저장하면 내 파일이 됩니다.",
    origin_note_pack_edited: "수정한 팩 파일입니다. 팩이 더 이상 갱신하지 않습니다. 지우면 다음 동기화 때 팩의 파일이 다시 들어옵니다.",
    name_first: "먼저 파일 이름을 정하세요 (*.json 또는 *.py)",
    confirm_edit_pack: "{name}은(는) 오버라이드 팩의 파일입니다. 수정하면 내 파일이 되어 팩이 더 이상 갱신하거나 지우지 않습니다.",
    saved: "{name} 저장함", confirm_delete: "{name}을(를) 삭제할까요?", deleted: "{name} 삭제함", not_deleted: "삭제하지 못했습니다: {error}",
    pack_is_off: "팩이 꺼져 있습니다", cannot_sync: "동기화할 수 없습니다: {error}", pack_changed: "팩이 컨버터 파일을 바꿨습니다",
  },
};

let LANG = "en";

// the language to show: Korean when Home Assistant's is, else English (the fallback for any key too)
function setLanguage(hass) {
  const lang = String((hass && ((hass.locale && hass.locale.language) || hass.language)) || "en").toLowerCase();
  LANG = lang.startsWith("ko") ? "ko" : "en";
  return LANG;
}

function t(key, vars = {}) {
  const text = (I18N[LANG] && I18N[LANG][key]) ?? I18N.en[key] ?? key;
  return text.replace(/\{(\w+)\}/g, (_, k) => (k in vars ? String(vars[k]) : `{${k}}`));
}


// ---- bridge devices ------------------------------------------------------------------------------------------------

const CATEGORIES = ["missing", "orphan", "mismatch", "synced"];     // rustuya-manager's order and filter tabs
const RANK = Object.fromEntries(CATEGORIES.map((c, i) => [c, i]));
const SORTS = ["id", "name", "category"];                           // label: t(`sort_${key}`)

const side = (d) => d.cloud || d.bridge;                            // the cloud's record, else the bridge's
const nameOf = (d) => (side(d).name && side(d).name !== "N/A" ? side(d).name : null);

function stored(key, fallback) {
  try {
    const v = JSON.parse(localStorage.getItem(key));
    return v == null ? fallback : v;
  } catch (e) {
    return fallback;
  }
}

function store(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch (e) { /* private window or blocked storage: the default next time */ }
}

// ---- fetching the device list from Tuya Cloud (CloudView): a saved login is reused, a QR only without one ---------

class CloudFetchBox {
  constructor(panel, onDone) {
    this.panel = panel;
    this.onDone = onDone;
    this.timer = null;
    this.code = el("input", { placeholder: t("cloud_code"), spellcheck: "false" });
    this.startBtn = el("button", { class: "primary", onclick: () => this.start() }, t("cloud_fetch"));
    this.cancelBtn = el("button", { onclick: () => this.cancel() }, t("cancel"));
    this.msg = el("div", { class: "muted" });
    this.qr = el("img", { class: "qr", alt: t("cloud_qr_alt") });
    this.form = el("div", { class: "row" }, this.code, this.startBtn);
    this.root = el("div", { class: "cloud", hidden: true },
      el("div", { class: "muted" }, t("cloud_intro")),
      this.form, this.msg, this.qr, el("div", { class: "row note" }, this.cancelBtn));
    this.qr.hidden = true;
  }

  open() {
    this.root.hidden = false;
    this.form.hidden = false;
    this.msg.textContent = "";
    this.msg.className = "muted";
    this.poll(true);                     // a fetch may already run (another tab, or this page reloaded)
  }

  async start() {
    this.startBtn.disabled = true;
    try {
      this.show(await this.panel._api("POST", "cloud", { user_code: this.code.value.trim() }));
      this.form.hidden = true;
      this.schedule();
    } catch (e) {
      this.msg.className = "error";
      this.msg.textContent = message(e);
    } finally {
      this.startBtn.disabled = false;
    }
  }

  schedule() {
    clearTimeout(this.timer);
    this.timer = setTimeout(() => this.poll(false), 1000);
  }

  async poll(opening) {
    let r;
    try {
      r = await this.panel._api("GET", "cloud");
    } catch (e) {
      this.msg.className = "error";
      this.msg.textContent = message(e);
      return;
    }
    const running = !["idle", "done", "error", "cancelled"].includes(r.state);
    if (!running && r.user_code && !this.code.value) this.code.value = r.user_code;   // the saved login's code
    if (opening && !running) return;     // nothing running: the form, not the last outcome
    this.form.hidden = running;
    this.show(r);
    if (running) {
      this.schedule();
    } else if (r.state === "done") {
      this.panel._toast(t("cloud_done"));
      this.close();
      this.onDone();
    }
  }

  show(r) {
    this.msg.className = r.state === "error" ? "error" : "muted";
    // the state in the user's language; rustuya-manager's own words (English) only for an error's detail
    this.msg.textContent = r.state === "error" ? t("cloud_failed", { error: r.error || r.message })
      : I18N.en[`cloud_state_${r.state}`] ? t(`cloud_state_${r.state}`) : r.message || "…";
    this.qr.hidden = !r.qr;
    if (r.qr) this.qr.src = r.qr;
    if (r.state === "error" || r.state === "cancelled") this.form.hidden = false;
  }

  async cancel() {
    clearTimeout(this.timer);
    try {
      await this.panel._api("DELETE", "cloud");
    } catch (e) {
      // closing the box anyway: the session closes itself once nothing polls it
    }
    this.close();
  }

  close() {
    clearTimeout(this.timer);
    this.root.hidden = true;
    this.qr.hidden = true;
  }
}

class BridgeSection {
  constructor(panel) {
    this.panel = panel;
    this.devices = null;                                  // null until the first load
    this.online = {};                                     // the service's link state, with the list
    this.links = {};                                      // live from rustuya/subscribe_links while the panel is open
    this.unsub = null;
    const f = stored("rustuya.filters", CATEGORIES);
    this.filters = new Set(Array.isArray(f) ? f.filter((c) => CATEGORIES.includes(c)) : CATEGORIES);
    const sort = stored("rustuya.sort", "id");
    this.sort = SORTS.includes(sort) ? sort : "id";
    this.expanded = new Set();
    this.busy = false;

    this.status = el("div", { class: "muted" });
    this.chips = el("div", { class: "chips" });
    this.list = el("div", { class: "devices" });
    this.refreshBtn = el("button", { onclick: () => this.load() }, t("refresh"));
    this.cloud = new CloudFetchBox(panel, () => this.load());
    this.fetchBtn = el("button", { title: t("fetch_title"), onclick: () => this.cloud.open() }, t("fetch_button"));
    const sortSelect = el("select", { title: t("sort_title"), onchange: (e) => { this.sort = e.target.value; store("rustuya.sort", this.sort); this.paint(); } },
      el("option", { value: "", disabled: true }, t("sort_title")),
      ...SORTS.map((k) => el("option", { value: k, selected: k === this.sort }, t(`sort_${k}`))));
    const menu = el("details", { class: "panel-menu" },
      el("summary", { title: t("menu"), "aria-label": t("menu") }, "☰"),
      el("div", { class: "menu-items" }, sortSelect,
        el("button", { onclick: () => this.openManual() }, t("manual_add")), this.fetchBtn, this.refreshBtn));
    menu.addEventListener("click", (event) => {
      if (event.target.closest("button")) menu.open = false;
    });
    menu.addEventListener("keydown", (event) => {
      if (event.key === "Escape") { menu.open = false; menu.querySelector("summary").focus(); }
    });
    menu.addEventListener("focusout", (event) => {
      if (!menu.contains(event.relatedTarget)) menu.open = false;
    });
    this.dialog = el("dialog");
    this.root = el("div", { class: "card" },
      el("div", { class: "head" }, el("h2", {}, t("bridge_title")),
        el("div", { class: "end" }, menu,
          el("button", { class: "icon-button", title: t("close_panel"), "aria-label": t("close_panel"),
            onclick: () => panel._hide() }, el("ha-icon", { icon: "mdi:close" })))),
      el("div", { class: "muted" }, t("bridge_intro")),
      this.cloud.root,
      this.chips, this.status, this.list, this.dialog);
  }

  async load(selection) {
    if (this.busy) return;
    this.busy = true;
    this.refreshBtn.disabled = true;
    this.status.className = "muted";
    this.status.textContent = selection ? t("bridge_sending") : t("bridge_reading");
    try {
      const r = selection ? await this.panel._api("POST", "bridge", selection) : await this.panel._api("GET", "bridge");
      this.devices = r.devices;
      this.online = r.online || {};
      this.status.textContent = r.cloud_loaded ? "" : t("bridge_no_cloud");
      if (selection) this.panel._toast(!r.sent ? t("bridge_nothing") : r.sent === 1 ? t("bridge_sent_one") : t("bridge_sent_many", { n: r.sent }));
    } catch (e) {
      this.status.className = "error";
      this.status.textContent = message(e);
    } finally {
      this.busy = false;
      this.refreshBtn.disabled = false;
    }
    this.paint();
  }

  // Live link state for as long as the panel is on the page: Home Assistant opens its own MQTT listener for this
  // subscription and closes it when we unsubscribe (or the websocket goes). Without it the dots are the service's.
  async watch() {
    if (this.unsub || !this.panel._hass) return;
    this.unsub = "pending";
    let unsub;
    try {
      unsub = await this.panel._hass.connection.subscribeMessage((m) => {
        Object.assign(this.links, m.links || {});
        this.paintSoon();
      }, { type: "rustuya/subscribe_links" });
    } catch (e) {
      this.unsub = null;
      return;
    }
    if (this.unsub === "pending") this.unsub = unsub;
    else unsub();                                         // unwatched while subscribing
  }

  unwatch() {
    if (typeof this.unsub === "function") this.unsub();
    this.unsub = null;
    this.links = {};
  }

  paintSoon() {
    if (this.painting) return;
    this.painting = true;
    requestAnimationFrame(() => { this.painting = false; this.paint(); });
  }

  link(id) {
    if (id in this.links) return this.links[id];
    if (id in this.online) return { online: this.online[id], code: null, message: "" };
    return null;
  }

  count(cat) {
    return (this.devices || []).filter((d) => d.category === cat).length;
  }

  paint() {
    const all = CATEGORIES.every((c) => this.filters.has(c));
    const chip = (key, label, n, on) => el("button", {
      class: `chip ${key === "all" ? "all" : `cat-${key}`}${on ? " on" : ""}${n ? "" : " zero"}`,
      onclick: () => this.toggle(key),
    }, label, el("span", { class: "n" }, n || ""));
    this.chips.replaceChildren(
      chip("all", t("cat_all"), (this.devices || []).length, all),
      ...CATEGORIES.map((c) => chip(c, t(`cat_${c}`), this.count(c), this.filters.has(c))));

    if (!this.devices) {
      this.list.replaceChildren();
      return;
    }
    const entries = this.tree();
    if (!entries.length) {
      this.list.replaceChildren(el("div", { class: "muted" },
        !this.filters.size ? t("no_category") : this.devices.length ? t("no_device_in_categories") : t("no_devices")));
      return;
    }
    const nodes = [];
    for (const e of entries) {
      nodes.push(e.device ? this.card(e.device, false) : this.placeholder(e.id));
      for (const k of e.kids) nodes.push(this.card(k, true));
    }
    this.list.replaceChildren(...nodes);
  }

  toggle(key) {
    if (key === "all") {
      this.filters = CATEGORIES.every((c) => this.filters.has(c)) ? new Set() : new Set(CATEGORIES);
    } else if (this.filters.has(key)) {
      this.filters.delete(key);
    } else {
      this.filters.add(key);
    }
    store("rustuya.filters", [...this.filters]);
    this.paint();
  }

  // Gateways and WiFi devices at the top level with their sub-devices under them; a sub-device whose gateway is in
  // neither list hangs under a placeholder. An entry shows when it or one of its sub-devices passes the filters; a
  // shown gateway shows all its sub-devices, for context (as in rustuya-manager).
  tree() {
    const byId = new Map(this.devices.map((d) => [d.id, d]));
    const kids = new Map();
    const top = [];
    for (const d of this.devices) {
      const s = side(d);
      if (s.type === "SubDevice" && s.parent_id) {
        if (!kids.has(s.parent_id)) kids.set(s.parent_id, []);
        kids.get(s.parent_id).push(d);
      } else {
        top.push(d);
      }
    }
    const entries = top.map((d) => ({ id: d.id, device: d, kids: kids.get(d.id) || [] }));
    for (const d of top) kids.delete(d.id);
    for (const [id, list] of kids) if (!byId.has(id)) entries.push({ id, device: null, kids: list });
    const value = (d) => this.sort === "name" ? (side(d).name || "").toLowerCase()
      : this.sort === "category" ? RANK[d.category] : d.id;
    // a placeholder (a missing gateway) sorts as a missing device, by its id
    const key = (e) => (e.device ? value(e.device) : this.sort === "category" ? RANK.missing : e.id);
    const cmp = (a, b) => {
      const ka = key(a), kb = key(b);
      return ka < kb ? -1 : ka > kb ? 1 : 0;
    };
    const out = [];
    for (const e of entries) {
      const shown = e.device ? this.filters.has(e.device.category) : this.filters.has("missing");
      const passing = e.kids.filter((k) => this.filters.has(k.category));
      if (shown || passing.length) out.push({ ...e, kids: shown ? e.kids : passing });
    }
    out.sort(cmp);
    for (const e of out) e.kids.sort((a, b) => cmp({ device: a, id: a.id }, { device: b, id: b.id }));
    return out;
  }

  placeholder(id) {
    return el("div", { class: "placeholder" },
      el("div", { class: "row" }, el("span", { class: "id" }, id), el("span", { class: "pill cat-missing" }, t("missing_gateway"))),
      el("div", { class: "muted" }, t("missing_gateway_note")));
  }

  card(d, child) {
    const s = side(d);
    const name = nameOf(d) || d.id;
    // the bridge's word on the connection (live while the panel is open, else the service's); none for a missing one
    const ln = d.category === "missing" ? null : this.link(d.id);
    const live = ln ? (ln.online ? "online" : "offline") : null;
    const acts = el("span", { class: "acts" },
      live ? el("span", { class: `dot ${live}`, title: live === "online" ? t("link_online") : t("link_offline") }) : "",
      el("span", { class: `pill cat-${d.category}` }, t(`cat_${d.category}`)));
    const act = (label, icon, cat, fn) => el("button", {
      class: `act icon-button cat-${cat}`, title: label, "aria-label": label,
      onclick: (e) => { e.stopPropagation(); fn(); },
    }, el("ha-icon", { icon: `mdi:${icon}` }));
    if (d.category === "missing") acts.append(act(t("act_add"), "plus", "missing", () => this.one("add", d)));
    if (d.category === "mismatch") acts.append(act(t("act_update"), "sync", "mismatch", () => this.one("update", d)));
    if (d.category !== "missing") acts.append(act(t("edit_device"), "pencil", d.category, () => this.openManual(d)));
    if (d.category !== "missing") acts.append(act(t("act_remove"), "trash-can-outline", "orphan", () => this.one("remove", d)));
    const card = el("div", { class: `dev cat-${d.category}${child ? " child" : ""}${live === "offline" ? " offline" : ""}`,
      title: `${t(`cat_${d.category}`)} · ${s.type}${live ? ` · ${live === "online" ? t("link_online") : t("link_offline")}` : ""}`,
      onclick: () => { this.expanded.has(d.id) ? this.expanded.delete(d.id) : this.expanded.add(d.id); this.paint(); } },
      el("div", { class: "top" }, child ? el("span", { class: "tree" }, "└") : "", el("span", { class: "name" }, name), acts),
      name !== d.id ? el("div", { class: "id" }, d.id) : "");
    const open = this.expanded.has(d.id);
    // a synced card has no other sign of trouble, so its error shows collapsed too (as in rustuya-manager)
    if (!open && d.category === "synced" && live === "offline" && ln.message) card.append(el("div", { class: "err", title: ln.message }, `⚠ ${ln.message}`));
    if (!open) return card;
    // the bridge's value is what it runs with; the cloud's where the bridge does not have the device
    const b = d.bridge || {}, c = d.cloud || {};
    const pick = (k) => (b[k] && b[k] !== "Auto" ? b[k] : c[k] || b[k]) || "—";
    const field = (label, value, wide) => el("div", { class: wide ? "wide" : "" }, el("b", {}, label), el("span", {}, value));
    card.append(el("div", { class: "fields" }, ...(s.type === "SubDevice"
      ? [field("CID", s.cid || "—", true), field("PARENT", s.parent_id || "—", true)]
      : [field("IP", pick("ip")), field("VER", pick("version")), field("KEY", s.key || "—", true),
         ...(ln && ln.message ? [el("div", { class: "full" }, el("b", {}, "MSG"), el("span", {}, ln.code != null ? `${ln.message} (${ln.code})` : ln.message))] : [])])));
    if (d.reasons.length) card.append(el("div", { class: "reasons" }, ...d.reasons.flatMap((r, i) => i ? [el("br"), r] : [r])));
    return card;
  }

  openManual(device = null) {
    if (this.busy) return;
    const initial = device ? { ...device.bridge, id: device.id } : {};
    const inputs = {};
    const fields = el("div", { class: "settings" });
    const type = el("select", { onchange: () => render() },
      el("option", { value: "WiFi" }, "Wi-Fi"), el("option", { value: "SubDevice" }, "Sub-device"));
    type.value = initial.type || "WiFi";
    type.disabled = !!device;
    const render = () => {
      fields.replaceChildren(...["id", "name", ...(type.value === "WiFi" ? ["ip", "key", "version"] : ["cid", "parent_id"])].map((key) => {
        inputs[key] ||= el("input", { type: "text", autocomplete: "off", placeholder: key, "aria-label": key, title: key,
          value: initial[key] === "Auto" ? "" : initial[key] || "", readonly: !!device && key === "id" });
        return inputs[key];
      }));
    };
    render();
    const error = el("div", { class: "error" });
    const submit = el("button", { type: "submit", class: "primary" }, t(device ? "save" : "manual_add"));
    const form = el("form", { onsubmit: async (event) => {
      event.preventDefault();
      if (this.busy) return;
      const body = { type: type.value };
      for (const key of ["id", "name", ...(type.value === "WiFi" ? ["ip", "key", "version"] : ["cid", "parent_id"])]) {
        if (inputs[key].value.trim()) body[key] = inputs[key].value.trim();
      }
      submit.disabled = true;
      this.busy = true;
      try {
        await this.panel._api(device ? "PATCH" : "PUT", "bridge", body);
        this.dialog.close();
        this.filters.add("orphan");
        this.panel._toast(t("bridge_sent_one"));
      } catch (e) {
        error.textContent = message(e);
        return;
      } finally {
        this.busy = false;
        submit.disabled = false;
      }
      await this.load();
    } }, el("h3", {}, t(device ? "edit_device" : "manual_add")), el("p", { class: "muted" }, t(device ? "edit_hint" : "manual_hint")), type, fields, error,
    el("div", { class: "foot" }, el("button", { type: "button", onclick: () => this.dialog.close() }, t("cancel")), submit));
    this.dialog.replaceChildren(form);
    this.dialog.showModal();
  }

  async one(verb, d) {
    const who = nameOf(d) ? `${nameOf(d)} (${d.id})` : d.id;
    if (verb === "remove" && !confirm(t("confirm_remove_device", { who }))) return;
    await this.load({ [verb]: [d.id] });
  }


}

// ---- options and settings: cards of fields saved together, each save restarting the integration -----------------

class SavedSection {
  // `name`: the API path and the prefix of its text (t(`${name}_title`, `_intro`, `_unreadable`))
  constructor(panel, name) {
    this.panel = panel;
    this.name = name;
    this.inputs = {};
    this.current = {};
    this.body = el("div", { class: "settings" }, el("div", { class: "muted" }, "…"));
    this.save = el("button", { class: "primary", onclick: () => this.submit() }, t("save_restart"));
    this.root = el("div", { class: "card" },
      el("h2", {}, t(`${name}_title`)),
      el("div", { class: "muted" }, t(`${name}_intro`)),
      this.body,
      el("div", { class: "row note" }, this.save));
  }

  async load() {
    let r;
    try {
      r = await this.panel._api("GET", this.name);
    } catch (e) {
      this.body.replaceChildren(el("div", { class: "error" }, t(`${this.name}_unreadable`, { error: message(e) })));
      this.save.disabled = true;
      return;
    }
    this.inputs = {};
    this.body.replaceChildren(...this.render(r));
    this.updateSave();
  }

  changed() {
    return Object.fromEntries(Object.entries(this.inputs)
      .map(([k, input]) => [k, this.value(input)]).filter(([k, v]) => v !== this.current[k]));
  }

  dirty() {
    return Object.keys(this.changed()).length > 0;
  }

  updateSave() {
    this.save.disabled = !this.dirty();                   // only when there is something to save
  }

  confirmSave() {
    return true;
  }

  async submit() {
    const changed = this.changed();
    if (!Object.keys(changed).length) {
      this.panel._toast(t("nothing_changed"));
      return;
    }
    if (!this.confirmSave(changed)) return;
    this.save.disabled = true;
    this.panel._toast(t("saving_restarting"));
    try {
      const r = await this.panel._api("PUT", this.name, changed);     // answered once the restart is over
      if (r && r.restarting === false) {
        this.panel._toast(t("nothing_changed"));
        this.updateSave();
        return;
      }
    } catch (e) {
      this.panel._toast(t("not_saved", { error: message(e) }));
      this.updateSave();
      return;
    }
    this.current = { ...this.current, ...changed };      // saved: nothing left to warn about
    location.reload();                                    // onto the restarted integration
  }
}

// the entry's options other than the panel itself (OptionsView); label and hint: t(`opt_${key}`), t(`opt_${key}_hint`)
const OPTIONS = ["allow_hazardous", "expose_unused", "pack"];

class OptionsSection extends SavedSection {
  constructor(panel) {
    super(panel, "options");
  }

  render(r) {
    this.current = r;
    return OPTIONS.map((k) => {
      const box = el("input", { type: "checkbox", onchange: () => this.updateSave() });
      box.checked = !!r[k];
      this.inputs[k] = box;
      return el("label", { class: "check" }, box,
        el("span", {}, el("div", {}, t(`opt_${k}`)), el("div", { class: "muted" }, t(`opt_${k}_hint`))));
    });
  }

  value(box) {
    return box.checked;
  }
}

// what setup leaves at its defaults (SettingsView); label and hint: t(`set_${key}`), t(`set_${key}_hint`)
const SETTINGS = ["bridge_root", "il_prefix", "il_source", "devices_path", "bridge_state_file", "bridge_log_level"];

class SettingsSection extends SavedSection {
  constructor(panel) {
    super(panel, "settings");
  }

  render(r) {
    this.current = r.settings;
    return SETTINGS.filter((k) => k in r.settings).map((k) => {
      const input = k === "bridge_log_level"
        ? el("select", { onchange: () => this.updateSave() },
          ...(r.log_levels || []).map((v) => el("option", { value: v }, v)))
        : el("input", { spellcheck: "false", oninput: () => this.updateSave() });
      input.value = r.settings[k];
      this.inputs[k] = input;
      return el("label", { class: "setting" }, el("span", {}, t(`set_${k}`)), input,
        el("span", { class: "muted" }, t(`set_${k}_hint`)));
    });
  }

  value(input) {
    return input.value.trim();
  }

  confirmSave(changed) {
    return confirm(t("confirm_settings", { names: Object.keys(changed).map((k) => t(`set_${k}`)).join(", ") }));
  }
}

class RustuyaPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._files = [];
    this._selected = null;      // the name of the file in the editor, as loaded (null: a new file)
  }

  set hass(hass) {
    this._hass = hass;
    if (this._menu) this._menu.hass = hass;
    const before = LANG;
    if (setLanguage(hass) !== before && this._built) {
      // the user switched Home Assistant's language: build the page again in it
      if (this._bridge) this._bridge.unwatch();
      this._built = false;
    }
    if (!this._built) {
      this._built = true;
      this._build();
      this._refresh();
      this._bridge.load();
      if (this.isConnected) this._bridge.watch();
    }
  }

  connectedCallback() {
    if (this._bridge) this._bridge.watch();
  }

  disconnectedCallback() {
    if (this._bridge) this._bridge.unwatch();
  }

  set narrow(narrow) {
    this.toggleAttribute("narrow", !!narrow);
    if (this._menu) this._menu.narrow = narrow;
  }

  _toast(text) {
    this.dispatchEvent(new CustomEvent("hass-notification", { detail: { message: text }, bubbles: true, composed: true }));
  }

  _api(method, path, body) {
    return this._hass.callApi(method, `rustuya/${path}`, body);
  }

  _converter(method, name, body) {
    return this._api(method, `converters/${encodeURIComponent(name)}`, body);
  }

  _paintWarnings(warnings) {
    this._warnings.replaceChildren(...(warnings || []).map((w) => el("div", {}, w)));
  }

  _build() {
    this._menu = document.createElement("ha-menu-button");
    this._menu.hass = this._hass;
    this._menu.narrow = this.hasAttribute("narrow");

    this._bridge = new BridgeSection(this);
    this._options = new OptionsSection(this);
    this._settings = new SettingsSection(this);
    this._pack = el("div", { class: "muted" }, "…");
    this._syncBtn = el("button", { onclick: () => this._syncPack() }, t("sync_now"));
    this._list = el("ul", { class: "files" });
    this._warnings = el("div", { class: "warn note" });
    this._name = el("input", { placeholder: "10_mine.json", spellcheck: "false" });
    this._text = el("textarea", { spellcheck: "false" });
    this._origin = el("div", { class: "muted note" });
    this._text.addEventListener("keydown", (e) => {
      if (e.key === "Tab") {                                          // indent instead of leaving the editor
        e.preventDefault();
        this._text.setRangeText("  ", this._text.selectionStart, this._text.selectionEnd, "end");
      } else if ((e.ctrlKey || e.metaKey) && e.key === "s") {
        e.preventDefault();
        this._save();
      }
    });

    this.shadowRoot.replaceChildren(
      el("style", {}, STYLE),
      el("div", { class: "toolbar" }, this._menu, el("span", {}, "Rustuya"), el("span", { class: "spacer" })),
      el("div", { class: "content" },
        this._bridge.root,
        el("div", { class: "card" },
          el("h2", {}, t("pack_title")),
          el("div", { class: "muted" }, t("pack_intro")),
          el("div", { class: "row note" }, this._pack, this._syncBtn)),
        this._convertersCard = el("div", { class: "card" },
          el("h2", {}, t("converters_title")),
          el("div", { class: "muted" }, t("converters_intro")),
          this._warnings,
          el("div", { class: "split" },
            el("div", {},
              el("div", { class: "row", style: "margin-bottom:8px" },
                el("button", { onclick: () => this._new() }, t("new_file"))),
              this._list),
            el("div", {},
              el("div", { class: "row" },
                this._name,
                el("button", { class: "primary", onclick: () => this._save() }, t("save")),
                el("button", { onclick: () => this._delete() }, t("delete"))),
              this._origin,
              this._text))),
        this._options.root,
        this._settings.root));
    this._acceptDrops(this._convertersCard);
    this._options.load();
    this._settings.load();
  }

  // files dragged from the desktop: dropped on the converters card they are copied in (`_import`); dropped anywhere
  // else on the panel they are ignored, rather than the browser leaving Home Assistant to open the file
  _acceptDrops(target) {
    const hasFiles = (e) => e.dataTransfer && [...e.dataTransfer.types].includes("Files");
    let depth = 0;                                                    // enter/leave fire for every child crossed
    const done = () => {
      depth = 0;
      target.classList.remove("drop");
    };
    target.addEventListener("dragenter", (e) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      depth += 1;
      target.classList.add("drop");
    });
    target.addEventListener("dragleave", (e) => {
      if (hasFiles(e) && --depth <= 0) done();
    });
    target.addEventListener("dragover", (e) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "copy";
    });
    target.addEventListener("drop", (e) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      e.stopPropagation();
      done();
      this._import([...e.dataTransfer.files]);
    });
    for (const type of ["dragover", "drop"]) {
      this.shadowRoot.addEventListener(type, (e) => {
        if (!hasFiles(e) || e.composedPath().includes(target)) return;
        e.preventDefault();
        if (type === "dragover") e.dataTransfer.dropEffect = "none";
      });
    }
  }

  // each file saved under its own name through the same API and checks as Save (a *.json must parse)
  async _import(files) {
    const saved = [], failed = [];
    for (const file of files) {
      const name = file.name;
      if (!CONVERTER_FILE.test(name)) {
        failed.push(t("only_converters", { name }));
        continue;
      }
      const f = this._files.find((x) => x.name === name);
      if (f && !confirm(f.origin === "pack" ? t("confirm_replace_pack", { name }) : t("confirm_replace", { name }))) continue;
      try {
        await this._converter("PUT", name, { content: await file.text() });
        saved.push(name);
      } catch (e) {
        failed.push(`${name} (${message(e)})`);
      }
    }
    if (saved.length) {
      await this._refresh();
      if (saved.length === 1) await this._open(saved[0]);
    }
    // one notification: Home Assistant shows only the latest
    const parts = [saved.length ? t("copied", { names: saved.join(", ") }) : "",
      failed.length ? t("not_copied", { names: failed.join("; ") }) : ""];
    const text = parts.filter(Boolean).join(". ");
    if (text) this._toast(text);
  }

  async _hide() {
    const unsaved = [["options_title", this._options], ["settings_title", this._settings]].filter(([, x]) => x.dirty())
      .map(([key]) => t(key));
    // asked only when something would be lost; otherwise it just goes (Configure adds it back)
    if (unsaved.length && !confirm(t("confirm_hide_unsaved", { names: unsaved.join(t("and")) }))) return;
    try {
      await this._api("DELETE", "panel");
    } catch (e) {
      this._toast(t("not_hidden", { error: message(e) }));
      return;
    }
    // Home Assistant's own navigation (the frontend listens for location-changed), to the integration's page
    history.replaceState(null, "", "/config/integrations/integration/rustuya");
    window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: true } }));
  }

  async _refresh() {
    let r;
    try {
      r = await this._api("GET", "converters");
    } catch (e) {
      this._list.replaceChildren(el("li", { class: "error" }, t("files_unreadable", { error: message(e) })));
      return null;
    }
    this._files = r.files;
    this._paintList();
    this._paintWarnings(r.warnings);
    this._paintPack(r.pack);
    return r;
  }

  _paintList() {
    if (!this._files.length) {
      this._list.replaceChildren(el("li", { class: "muted" }, t("no_files")));
      return;
    }
    this._list.replaceChildren(...this._files.map((f) =>
      el("li", { class: f.name === this._selected ? "sel" : "", onclick: () => this._open(f.name) },
        el("span", {}, f.name), ORIGIN[f.origin] ? el("span", { class: "tag" }, t(ORIGIN[f.origin])) : "")));
  }

  _paintPack(pack) {
    this._syncBtn.disabled = this._syncing || !pack || !pack.enabled;
    const show = (text, cls = "muted") => {
      this._pack.textContent = text;
      this._pack.className = cls;
    };
    if (this._syncing) return show(t("pack_syncing"));
    if (!pack || !pack.enabled) return show(t("pack_off"));
    const p = pack.status;
    if (!p) return show(t("pack_never"));
    const when = new Date(p.at * 1000).toLocaleString(LANG);
    if (p.error) return show(t("pack_error", { error: p.error, when }), "warn");
    const changes = PACK_CHANGES.filter((k) => p[k] && p[k].length).map((k) => `${t(`pack_${k}`)} ${p[k].join(", ")}`);
    const failed = (p.failed || []).length ? t("pack_failed", { list: p.failed.join("; ") }) : "";
    show(`${t("pack_synced", { when })}${changes.length ? `: ${changes.join("; ")}` : ""}${failed}`, failed ? "warn" : "muted");
  }

  _originNote(name) {
    const f = this._files.find((x) => x.name === name);
    const o = f && f.origin;
    this._origin.textContent = o === "pack" ? t("origin_note_pack") : o === "pack_edited" ? t("origin_note_pack_edited") : "";
  }

  async _open(name) {
    try {
      const r = await this._converter("GET", name);
      this._selected = r.name;
      this._name.value = r.name;
      this._text.value = r.content;
      this._originNote(r.name);
      this._paintList();
    } catch (e) {
      this._toast(message(e));
    }
  }

  _new() {
    this._selected = null;
    this._name.value = "";
    this._text.value = "";
    this._origin.textContent = "";
    this._paintList();
    this._name.focus();
  }

  async _save() {
    const name = this._name.value.trim();
    if (!name) {
      this._toast(t("name_first"));
      return;
    }
    const f = this._files.find((x) => x.name === name);
    if (f && f.origin === "pack" && !confirm(t("confirm_edit_pack", { name }))) return;
    if (f && name !== this._selected && !confirm(t("confirm_replace", { name }))) return;
    try {
      const r = await this._converter("PUT", name, { content: this._text.value });
      this._selected = name;
      await this._refresh();
      this._paintWarnings(r.warnings);
      this._originNote(name);
      this._toast(t("saved", { name }));
    } catch (e) {
      this._toast(t("not_saved", { error: message(e) }));
    }
  }

  async _delete() {
    const name = this._name.value.trim();
    if (!name || !this._files.some((x) => x.name === name)) return;
    if (!confirm(t("confirm_delete", { name }))) return;
    try {
      await this._converter("DELETE", name);
      this._new();
      await this._refresh();
      this._toast(t("deleted", { name }));
    } catch (e) {
      this._toast(t("not_deleted", { error: message(e) }));
    }
  }

  async _syncPack() {
    const before = this._files.length, r0 = await this._refresh();
    const at = r0 && r0.pack && r0.pack.status ? r0.pack.status.at : 0;
    try {
      const r = await this._api("POST", "pack");
      if (!r.started) {
        this._toast(t("pack_is_off"));
        return;
      }
    } catch (e) {
      this._toast(t("cannot_sync", { error: message(e) }));
      return;
    }
    this._syncing = true;
    for (let i = 0; i < 30; i++) {                                   // up to ~30 s; the result lands in pack.status
      await new Promise((ok) => setTimeout(ok, 1000));
      const r = await this._refresh();
      if (!r || (r.pack && r.pack.status && r.pack.status.at > at)) break;
    }
    this._syncing = false;
    await this._refresh();
    if (this._files.length !== before) this._toast(t("pack_changed"));
  }
}

// a page that had the previous version loaded imports the new one (another `?v=`) too: the first definition stays
// until the page reloads, rather than an error
if (!customElements.get("rustuya-panel")) customElements.define("rustuya-panel", RustuyaPanel);
