# moa (우석World) — 작업 규칙

## 매매 로직(가장 기본 원칙)

**자산을 불릴 확률이 가장 높은 쪽으로 사고판다.** 모든 매수·매도 규칙은 이 한 줄에 실제로 도움이 될 때만 둔다.
이 원칙은 `moa/evolution.py`의 `CORE`에 있고, 빼거나 약하게 바꾸지 않는다(테스트가 확인한다).

## 매수·매도 로직을 고칠 때(복기 뒤든, 요청 반영이든 항상)

조건이 많다고 좋은 게 아니다. 규칙 하나를 고치거나 더할 때마다:

1. **전체 점검** — `moa/evolution.py`의 `RULES`(매매 규칙 대장)와 엔진(`moa/autotrade.py`의 `gate_code`·`_enter`·`_manage`)을 처음부터 끝까지 다시 읽는다.
2. **지우기** — 새 규칙 때문에 필요 없어진 규칙, 겹치는 규칙은 같은 판에서 지우거나 합친다. 엔진 노트 '매매 규칙'의 판정이 '검토'인 규칙(30일간 안 걸림, 걸러낸 종목이 오히려 좋았음)은 없앨지 따진다.
3. **대장 맞추기** — 엔진에 규칙을 더하거나 지우면 `RULES`도 같이 고친다. 점검 `AutoTrade.GATES`, 거르기 `AutoTrade.FILTERS`, 파는 이유 `autotrade.EXITS`와 어긋나면 `test_rule_book_matches_engine`이 실패한다.
4. **이력 남기기** — `LOGIC_LOG` 맨 위에 한 줄(날짜, 판, add/change/remove/check, buy/sell/both, 제목, 무엇을, 왜). 투자 엔진에 영향을 주는 판은 `DEV`에도 한 줄.
5. **검증 먼저** — 새 판단 규칙은 기본 꺼짐 + 모의 실험 플랜(`VARIANTS`)으로 먼저 비교하고, 실전 변경은 승인 후 반영이 기본이다.

## 우석World(앱 모음) 구조

이 웹앱 하나에 직접 만든 앱들을 모은다. 첫 화면(`#world`)에 앱 타일이 있고, 앱마다 구역(주소 첫 마디)·아래 탭·겉모습을 가진다.

- 앱 등록: `docs/app.js`의 `APPS`·`addApp()`. 가계부(가계부·자산·투자·설정)는 `app.js`, 건강관리는 `docs/health.js`·`docs/health.css`·`moa/health.py`(기구 안내 글은 `docs/gym.js`, 기구 사진은 서버 DB). 사진편집은 `docs/photo.js`·`docs/photo.css`·`moa/photo.py`(보관·설치)·`moa/photo_engine.py`(보정 계산, numpy·OpenCV)·`moa/slideshow.py`(슬라이드쇼 영상·배경음악). 무거운 꾸러미는 `requirements.txt`에 넣지 않고 앱의 설치 버튼으로 넣는다.
- 사진편집은 우석World 코드가 아니다: 노트북에서 따로 도는 PhotoGrader 웹 프로그램(AWS Album, 기본 8790 포트)을 `docs/photo.js`가 틀(iframe) 안에 그대로 띄운다. 그 프로그램은 **판매용이라 이 저장소에 넣지 않는다**(코드·화면 일부라도). 고칠 일이 있으면 프로그램 파일을 고쳐 업데이트 zip으로 사용자에게 주고, 프로그램 자체의 '업데이트 → zip 올리기'로 적용한다. `moa/album.py`는 켜짐 여부·주소·사진 수만 읽는다.
- **사진편집 프로그램 수정 요청이 오면(사용자가 따로 할 일 없이 적용)**: ① 프로그램 원본(작업본은 이 대화의 `/home/claude/album-src`, 없으면 연결된 PC의 프로그램 폴더에서 가져오거나 사용자에게 최신 zip을 받는다)을 고치고 로컬에서 켜서 확인 ② `python tools/ship_album.py /home/claude/album-src "바뀐 내용"`으로 `ship/album.bin`·`ship/album.json` 생성 ③ 커밋·푸시. 노트북이 5분 안에 받아 프로그램의 자체 업데이트(문법 검사·백업·재시작·실패 시 되돌림)로 적용한다(`moa/ship.py`, `moa/shipbox.py`). 프로그램 파일을 잠그지 않은 채로 저장소에 넣으면 안 된다. 적용 여부는 `/api/photo/app`의 `ship`에서 본다.
- 새 앱 더하기: ① `docs/<앱>.js`에서 `addApp({id, name, desc, home, secs, tabs, skin, tile, live, art})`와 `pages.<구역>` 정의 ② `docs/<앱>.css`(구역은 `html[data-sec="<구역>"]`로 한정, 클래스는 앱 머리글자 접두사) ③ `index.html`에 두 파일 연결 ④ 서버가 필요하면 `moa/<앱>.py` + `app.py` 라우트 + `/api/world`의 한 줄 요약.
- 전역 클래스(`.top`, `.hl`, `.empty`, `.check` …)와 이름이 겹치지 않게 한다(겹쳐서 난 문제가 여러 번 있었다).
- 건강 수치 같은 개인 자료는 저장소에 넣지 않는다. 서버 DB에만 두고, 처음 자료는 앱의 '자료 가져오기'로 넣는다.

## 자동매매가 보는 자료(v5.10~)

- 후보를 고르고 자료를 붙이는 곳은 `moa/aipick.py`의 `context`(단타)·`context_swing`(스윙·장기). 붙인 자료를 성적과 이어 보려면 `_feat_of`/`_feat_swing`에 특징값으로 남기고 `_defs`에 기준을 더한다(수급 `ff5`·`fo5`, 뉴스 점수 `news`, 넓게 훑기 `scan`).
- `moa/prep.py`: 넓게 훑기(장 마감 뒤, 국내만, 키움 조회만 씀)와 장 시작 전 리서치(AI 비용이 듦 → 자동매매가 도는 시장만, 하루 한 번, 설정 `prep.brief`로 끔). 둘 다 주문을 내지 않는다.
- AI 비용이 드는 새 기능은 '하루·종목마다 한 번'처럼 상한을 코드로 묶고, 실패해도 같은 날 다시 부르지 않게 한다(`ai_briefs`, `at_holdwatch`).
- 실험 플랜이 특정 방식에서만 뜻이 있으면 `VARIANT_STYLES`에 적는다(예: 재료 감시는 스윙·장기만).

## 그 밖

- 응답·화면 문구는 한국어. 화면을 고치면 기본·게임 테마 둘 다 확인한다.
- BAT 파일은 ASCII만. 외부 CDN 없이 동작해야 한다.
- 개인 금융 정보는 저장소에 넣지 않는다. 조회 전용 키로는 주문이 나가지 않아야 한다.
- 판 올리기: 테스트 통과 → `python tools/bump_version.py X.Y "메모"` → README에 한 줄.
