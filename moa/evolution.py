"""엔진 노트: 투자 엔진이 수익률을 높이려고 걸어온 길(앱 개선 + 실제로 스스로 고친 기록), 지금 로직 전반, 앞으로의 전략을 한곳에.

- 앱 개선 이력(DEV)은 사람이 적어 두는 목록이다. 투자 엔진에 영향을 주는 판을 올릴 때마다 한 줄씩 더한다.
- 나머지(플랜 조정·엔진 교체·복기·실험·성적)는 모두 DB에서 그때그때 계산한다.
"""
import json
from datetime import date, datetime

# (판, 날짜, 분야, 제목, 무엇을 했나, 왜: 수익률·안정성과의 관계)
AREAS = {"ai": "AI 추천", "auto": "자동매매", "learn": "학습", "cost": "비용", "lab": "실험", "safe": "안전장치", "data": "시세·자료", "div": "배당·적립"}
DEV = [
    ("v5.37", "2026-11-24", "auto", "추천 사이 실시간 가격 쓰기(실험)", "눌림 기다리기 · 조건 걸어 두고 사기 · 급변 때 일찍 묻기를 모의 실험 플랜으로 추가. 기다리는 매수는 실험 화면 보유 탭에 보임", "추천을 받기 전까지 매수 쪽이 아무것도 하지 않던 빈틈을 채우려고"),
    ("v5.36", "2026-11-24", "data", "미국 후보 넓히기", "미국 단타·스윙 후보에 거래대금 상위(주도 대형주)를 넣고, 스팩·유닛·채권형 상품을 뺌. 실험 기준선 플랜은 AI가 낸 종목을 다 사 봄", "시장 전체가 오른 날 과열 급등주만 보게 되던 구멍을 막으려고"),
    ("v5.24", "2026-11-23", "ai", "내 의견: 메모 · 제외 목록 · 추천 평가", "사용자 메모를 AI 추천 자료에 함께 싣고(참고용, 사실 확인을 일러 둠), 제외 종목을 거르고, 추천에 남긴 동의·반대를 성적표 기준('내 판단', '내 메모')으로 따로 잼", "사용자가 아는 정보를 자동매수에 넣는 길을 만들고, 그 판단이 실제로 도움이 됐는지 숫자로 보려고"),
    ("v5.21", "2026-11-23", "safe", "몬테카를로 위험 체계", "계좌 전체(앞으로 20거래일 분포)·자동매매마다(지금 보유의 나쁜 날 손실, 손실 한도에 닿을 확률, 성적이 우연이 아닐 가능성) 위험을 수천 번 다시 뽑아 잼. 실험 플랜 '위험 미리 막기' 추가", "한 가지 가정(지수 −5%)만 보던 위험을 분포로 보고, 손실 한도를 사후 대응에서 사전 점검으로 넓히려고"),
    ("v5.13", "2026-11-22", "data", "재무 지표 · 전자공시", "스윙·장기 후보에 PER·PBR·ROE·시가총액을 붙이고, 인증키를 넣으면 후보·보유 종목에 최근 공시 제목을 붙임", "가격 흐름만 보던 후보에 회사 체력과 공식 공시를 더하려고"),
    ("v5.13", "2026-11-22", "lab", "국면별 비중 · 반대 의견 확인 실험", "두 플랜을 실험 묶음에 더함(반대 의견은 스윙·장기만)", "약한 장에서 줄이는 것과 추천을 한 번 더 의심하는 것이 실제로 도움이 되는지 보려고"),
    ("v5.12", "2026-11-22", "data", "수급·뉴스 점수·넓게 훑기", "외국인·기관 순매수, 뉴스 영향 점수(−2~+2), 장 마감 뒤 넓게 훑은 후보를 추천 자료에 더하고 조건별 성적에 새 기준 3개를 추가", "순위에 뜬 종목만 보던 눈을 넓히고, 새 자료가 실제 성적과 이어지는지 재려고"),
    ("v5.12", "2026-11-22", "lab", "재료 감시 실험 · 장 시작 전 리서치", "스윙·장기 실험에 '재료 감시' 플랜을 더하고, 자동매매가 도는 시장은 장 시작 전에 AI 메모를 하루 한 번 받음", "재료가 사라진 종목을 일찍 정리하는 것이 도움이 되는지, 준비 메모가 추천을 낫게 하는지 보려고"),
    ("v5.11", "2026-11-22", "lab", "쏠림·위험 기준 수량·확신만큼 크기 실험", "같은 추천으로 '쏠림 줄이기', '위험 기준 수량', '확신만큼 크기' 플랜을 함께 굴림", "크기와 분산을 바꾸는 것이 실제로 낙폭과 수익에 도움이 되는지 숫자로 보려고"),
    ("v5.11", "2026-11-22", "learn", "체결 품질", "사기로·팔기로 정했을 때 본 값과 실제 체결가의 차이를 거래마다 적고 시간대별로 모음", "미끄러짐이 수익을 얼마나 깎는지, 어느 시간대가 나쁜지 보려고"),
    ("v5.10", "2026-11-22", "learn", "성과 분해 · 지수 보유 비교", "거래마다 살 때·팔 때 지수 값을 적어, 번 돈을 '지수가 올라서 / 종목을 골라서 / 비용'으로 나눔. 같은 기간 지수를 그냥 들고 있었을 때와 나란히",
     "AI가 종목을 잘 골라서 번 건지, 오른 장에 얹힌 건지 구분하려고"),
    ("v5.10", "2026-11-22", "safe", "경고·특수 종목 제외", "관리·투자경고·단기과열 종목, 스팩, 우선주, 레버리지·인버스 상품을 후보에서 뺌", "규칙대로 사고팔기에 맞지 않는 종목에 돈이 나가지 않게"),
    ("v5.10", "2026-11-22", "learn", "적은 표본을 덜 믿기", "조건별 성적은 표본 수만큼만 믿고(적으면 전체 평균 쪽으로), 개선안은 우연이 아닐 가능성 90% 이상일 때만", "잡음으로 기준을 이리저리 바꾸지 않게"),
    ("v1.2", "2026-11-17", "data", "투자 시세 시작", "국내(키움)·미국 시세, 지수, 종목 차트", "판단의 재료가 되는 시세부터 갖춤"),
    ("v1.6", "2026-11-17", "data", "키움 호출 한도 대응", "앱키별 토큰·간격 공유, 재시도", "시세가 끊기면 매매 판단도 멈추므로"),
    ("v2.1", "2026-11-17", "data", "미국 주식 주문·잔고", "키움 미국 주문·체결·예수금 연결", "미국장까지 기회를 넓힘"),
    ("v2.4", "2026-11-17", "lab", "단타 조건 대량 훑기", "1분봉을 모아 조건 수천 개를 학습/검증으로 나눠 백테스트", "감이 아니라 숫자로 조건을 고르려고"),
    ("v2.6", "2026-11-17", "ai", "Claude AI 투자 도우미", "종목·포트폴리오 정리, 웹 검색 출처", "뉴스·공시를 사람 대신 읽게"),
    ("v3.0", "2026-11-17", "data", "실제 체결가 반영", "체결 내역의 가중 평균 체결가로 손익 계산", "가짜 손익으로 판단하지 않게"),
    ("v3.5", "2026-11-17", "ai", "AI 종목 추천 + 자동 익절·손절", "추천마다 목표·손절선, 켜 둔 종목만 자동 매도", "들어갈 때와 나올 때를 함께 정함"),
    ("v4.2", "2026-11-17", "learn", "AI 추천 성적표", "모든 추천을 추천 시점 가격으로 가상 매매해 채점", "AI가 실제로 맞히는지 재기 시작"),
    ("v4.8", "2026-11-17", "learn", "AI 자기 학습 고리", "조건별 성적·승률 보정·복기 규칙을 다음 추천에 넣음", "틀린 패턴을 반복하지 않게"),
    ("v4.20", "2026-11-17", "learn", "채점 방식 바로잡기", "추천 전 가격 범위 제외, 체결 가능 시간 제한", "성적이 부풀려지지 않게"),
    ("v4.28", "2026-11-17", "ai", "스윙(1주·1달) 추천", "보유 기간별로 따로 추천·학습", "단타 말고 다른 시간대의 기회"),
    ("v4.32", "2026-11-18", "div", "배당 탭", "배당 달력·시뮬레이션·종목·AI 구성", "매달 들어오는 현금흐름 쪽 전략"),
    ("v4.38", "2026-11-18", "auto", "AI 자동매매", "플랜·모의/실전·매매 기록·개선안 승인", "추천을 규칙대로 실행해 사람 실수를 줄임"),
    ("v4.41", "2026-11-18", "learn", "기회 분석", "건너뛴 종목이 그 뒤 어땠는지 보고 기준을 조정(바닥 있음)", "너무 깐깐해 놓친 수익을 되찾으려고"),
    ("v4.49", "2026-11-19", "cost", "추천 함께 쓰기", "같은 조건의 자동매매는 한 번 받은 추천을 나눠 씀", "AI 비용을 줄여 순수익을 늘림"),
    ("v4.51", "2026-11-19", "cost", "비용을 먼저 따지는 플랜", "약한 날은 AI를 안 부름, 관망 뒤 쉬기, 비용 한도에 맞춰 엔진·횟수 조절", "수수료처럼 AI 비용도 수익을 깎으므로"),
    ("v4.51", "2026-11-19", "lab", "모의 실험 묶음", "같은 추천을 기준이 다른 플랜들이 동시에 굴림", "어떤 기준이 더 버는지 같은 날 비교"),
    ("v4.52", "2026-11-19", "lab", "손 안 대는 자동 테스트", "국내·미국 단타·스윙 실험을 앱이 알아서 돌리고 실전 후보를 추천", "실전 전에 모의로 충분히 검증"),
    ("v4.57", "2026-11-19", "lab", "짧게/길게 끊기 실험", "AI가 낸 익절·손절 폭을 0.5배·1.5배로", "파는 타이밍이 수익에 주는 영향 측정"),
    ("v4.59", "2026-11-19", "lab", "가리지 않고 사기 기준선", "AI가 낸 종목을 거의 다 사 보는 플랜", "앱의 거르기가 실제로 도움이 되는지 비교 기준"),
    ("v4.62", "2026-11-19", "lab", "모의는 1주라도 사 봄", "배정 금액이 모자라도 현금이 되면 1주", "돈 부족으로 시험이 안 되는 일을 없앰"),
    ("v4.64", "2026-11-19", "cost", "빠른 재추천", "정밀 추천 사이에 가장 싼 엔진으로 자주 다시 물음", "적은 비용으로 기회를 더 자주 봄"),
    ("v4.70", "2026-11-19", "learn", "매매 기록(날짜·전략·기준별)", "지난 거래를 여러 각도로 모아 봄", "무엇이 벌고 잃는지 보이게"),
    ("v4.73", "2026-11-19", "learn", "정밀 추천만으로 학습", "빠른 재추천 결과는 학습에서 뺌", "싼 엔진의 잡음이 학습을 흐리지 않게"),
    ("v4.74", "2026-11-19", "cost", "시드에 비례한 AI 한도 + 엔진 올리기", "한도 = 한 달 시드의 1%(하루 최소 $3), 성과가 나쁘면 Sonnet→Opus→Fable", "돈이 클수록 더 좋은 판단에 투자"),
    ("v4.77", "2026-11-19", "auto", "자동매매 흐름도", "점검→추천→거르기→크기→주문→보유→팔기→배우기와 지금 위치", "어디서 막히는지 바로 보이게"),
    ("v4.78", "2026-11-19", "learn", "복기 엔진 Opus", "쌓인 기록을 돌아보는 AI는 따로 좋은 엔진으로", "배우는 질이 가장 중요해서"),
    ("v4.79", "2026-11-20", "cost", "실험 AI 한도 수정", "정밀 추천 한 번에 한도가 차면 그 차례는 빠른 재추천으로", "하루 중간에 추천이 멈추던 문제"),
    ("v4.80", "2026-11-20", "div", "배당 자료 매일 모으기", "배당률 기록, 인상·삭감 감지·알림, 5년 대비 배당률 위치", "싸게 살 때와 삭감 위험을 보려고"),
    ("v4.92", "2026-11-21", "safe", "핵심만 휴대폰 알림", "실전 매매·쉬는 날·손실 한도·주문 실패·엔진 오류·AI 예산·실전 후보·승인 대기는 바로, 모의 매매는 장 마감 요약으로 한 번에",
     "꼭 봐야 할 일을 놓치지 않고, 알림이 많아 무뎌지지 않게"),
    ("v4.90", "2026-11-20", "learn", "매매 규칙 대장과 점검표", "매수·매도 규칙 전부와 변경 이력을 엔진 노트 '매매 규칙'에. 규칙마다 최근 30일 쓰임과 판정(유지·검토)",
     "규칙이 늘기만 하지 않게, 고칠 때마다 전체를 점검하고 필요 없는 것은 지우려고"),
    ("v4.89", "2026-11-20", "auto", "분할 비율을 상황에 맞게", "승률·지수·오른 속도·남은 장 시간으로 '힘'을 재서 파는 시점·비율(25~65%, 두 번까지)·남긴 몫의 지킴선·추적 폭을 정함. 살 때는 확신만큼 먼저(40~80%, 아주 높으면 한 번에)",
     "강한 종목은 길게 가져가 이익을 키우고, 약한 종목은 일찍 챙기려고"),
    ("v4.88", "2026-11-20", "auto", "분할 매도 · 분할 매수", "목표의 절반에서 절반을 먼저 팔고 남긴 몫은 본전 손절 / 절반만 먼저 사고 오르면 나머지. 실험 플랜으로 먼저 비교, 플랜에서 켤 수 있음",
     "이익은 일부 먼저 챙기고 나머지는 더 길게, 바로 밀리는 매수는 절반만 잃게"),
    ("v4.86", "2026-11-20", "lab", "약한 날 쉬기 · 흔들림 맞춤 손절 실험", "지수 -1% 아래면 사지 않는 플랜, 손절을 최근 흔들림(ATR)×배수까지 넓히고 수량을 줄이는 플랜을 실험에 추가",
     "11/19 오른 장 전부 익절, 11/20 내린 장 전부 손절 — 지수 기준과 손절폭이 실제로 손실을 줄이는지 같은 추천으로 비교"),
    ("v4.82", "2026-11-20", "learn", "매매 기록 숫자 바로잡기", "국내(원)·미국(달러)을 섞어 센 이익/손실 비를 원화로, 날짜마다 시장별 손익", "성적을 잘못 읽고 기준을 고치지 않게"),
    ("v4.81", "2026-11-20", "learn", "엔진 노트", "개선 과정·지금 로직·앞으로 전략을 한곳에", "전체를 보고 다음 수를 정하려고"),
]
# ---------------------------------------------------------------------------------------------------------------------
# 가장 기본이 되는 원칙. 모든 매수·매도 규칙은 이 한 줄에 도움이 될 때만 둔다. 이 원칙은 빼거나 약하게 바꾸지 않는다(테스트가 확인).
CORE = ("자산을 불릴 확률이 가장 높은 쪽으로 사고판다",
        "매수·매도 규칙의 목적은 하나예요. 지금 가진 자산을 불리는 데 확률이 가장 높은 쪽을 고르는 것. 규칙은 많다고 좋은 게 아니라, 이 목적에 실제로 도움이 될 때만 둬요.")
# 매매 규칙을 고칠 때 지키는 절차(엔진 노트에 그대로 보여 준다)
UPKEEP = ["규칙을 하나 고치거나 더할 때마다 매수·매도 규칙 전체를 다시 훑어봐요.",
          "새 규칙 때문에 필요 없어진 규칙은 함께 지워요. 겹치는 규칙은 하나로 합쳐요.",
          "최근 30일 동안 한 번도 안 걸렸거나, 걸러낸 종목이 오히려 좋았던 규칙은 '검토'로 표시하고 없앨지 따져요.",
          "더하고·고치고·지운 것은 아래 변경 이력에 날짜와 이유를 남겨요."]

# 매매 규칙 대장: 지금 엔진에 들어 있는 매수·매도 규칙 전부.
# (구분 side, 단계 stage, 코드 code, 이름, 원칙에 어떻게 도움이 되나, 들어온 판, 종류 kind: judge=판단 규칙 / ops=운영·안전장치)
# code는 엔진의 값과 같아야 한다 — 점검 gate:<AutoTrade.GATES>, 거르기 filter:<AutoTrade.FILTERS>, 팔기 exit:<autotrade.EXITS>. 크기 size:* 는 이름만.
# 규칙을 엔진에 더하거나 지우면 여기와 LOGIC_LOG도 함께 고친다(tests/test_auto.py::test_rule_book_matches_engine 이 어긋나면 실패).
RULES = [
    ("buy", "점검", "gate:closed", "장이 열렸을 때만", "닫힌 장에서는 체결이 안 되거나 값이 튀어요", "v4.38", "ops"),
    ("buy", "점검", "gate:over", "정한 기간 안에서만", "기간이 끝나면 새로 사지 않고 정리해요", "v4.38", "ops"),
    ("buy", "점검", "gate:time", "매수 시간대에만", "장 시작 직후·마감 직전의 큰 흔들림을 피해요", "v4.38", "judge"),
    ("buy", "점검", "gate:full", "동시 보유 종목 수 제한", "한꺼번에 너무 많이 들어 한 번에 크게 잃지 않게", "v4.38", "judge"),
    ("buy", "점검", "gate:dayloss", "하루 손실 한도", "안 맞는 날에 손실이 불어나는 걸 끊어요", "v4.38", "judge"),
    ("buy", "점검", "gate:streak", "연속 손절이면 그날 쉼", "연달아 틀리는 날은 판단이 장과 안 맞는 날이에요", "", "judge"),
    ("buy", "점검", "gate:cash", "현금이 있을 때만", "빚 없이 가진 돈 안에서만 사요", "v4.38", "ops"),
    ("buy", "점검", "gate:turn", "추천 받을 차례", "정밀 추천 횟수·간격과 빠른 재추천 간격을 지켜 비용을 아껴요", "v4.64", "ops"),
    ("buy", "점검", "gate:aicost", "하루 AI 비용 한도", "AI 비용이 수익을 갉아먹지 않게", "v4.51", "ops"),
    ("buy", "점검", "gate:market", "지수가 약한 날 쉼", "지수가 크게 빠지는 날은 단기 매수 승률이 낮아요(실전 기본 -1%)", "v4.51", "judge"),
    ("buy", "점검", "gate:watchwait", "관망 뒤 쉼", "AI가 관망이라 한 직후엔 같은 답에 돈을 또 쓰지 않아요", "v4.51", "ops"),
    ("buy", "점검", "gate:nokey", "AI 키", "키가 없으면 추천을 못 받아요", "v4.38", "ops"),
    ("buy", "점검", "gate:budget", "월 AI 예산", "한 달 예산을 넘기지 않아요", "v4.38", "ops"),
    ("buy", "거르기", "filter:market", "실험: 지수 기준", "'약한 날 쉬기' 실험 플랜이 지수 기준의 효과를 재요", "v4.86", "judge"),
    ("buy", "거르기", "filter:wait", "조건 기다리기(켠 플랜만)", "바로 사지 않고 가격 조건(눌림·지정가·돌파)에 닿을 때까지 기다렸다가, 닿으면 같은 기준으로 다시 걸러 사요", "v5.37", "ops"),
    ("buy", "거르기", "filter:full", "자리", "보유 종목 수가 차면 더 사지 않아요", "v4.38", "ops"),
    ("buy", "거르기", "filter:watch", "AI 관망", "AI가 관망을 권하면 안 사거나 작게 사요", "v4.38", "judge"),
    ("buy", "거르기", "filter:auto", "AI가 고른 종목만", "AI 답이 없을 때 앱이 지표로 채운 예비 종목은 사지 않아요", "v4.38", "judge"),
    ("buy", "거르기", "filter:junk", "경고·특수 종목 제외", "관리·투자경고·단기과열·정리매매 종목과 스팩(미국 유닛·워런트 포함)·우선주·레버리지 상품·채권형 상품은 규칙대로 사고팔기에 맞지 않아요", "v5.10", "judge"),
    ("buy", "거르기", "filter:riskcap", "위험 미리 막기(켠 플랜만)", "사기 전에 보유 전체의 '나쁜 날' 손실을 몬테카를로로 재서, 하루 손실 한도를 넘으면 줄이거나 사지 않아요", "v5.21", "judge"),
    ("buy", "거르기", "filter:counter", "반대 의견 확인(켠 플랜만)", "추천한 AI와 다른 AI가 반대편에서 약점·악재를 찾아, 확인된 문제가 있는 종목을 걸러요", "v5.13", "judge"),
    ("buy", "거르기", "filter:crowd", "쏠림 제한(켠 플랜만)", "같은 업종·테마나 같이 움직이는 종목을 겹쳐 들면 한 번에 같이 잃어요", "v5.11", "judge"),
    ("buy", "거르기", "filter:winprob", "최소 승률", "실제 적중률로 보정한 승률이 기준보다 높은 종목만", "v4.38", "judge"),
    ("buy", "거르기", "filter:score", "최소 점수", "AI 종합 점수가 기준보다 높은 종목만", "v4.38", "judge"),
    ("buy", "거르기", "filter:rr", "손익비", "익절 폭이 손절 폭보다 충분히 커야 같은 승률에서 남아요", "v4.38", "judge"),
    ("buy", "거르기", "filter:fee", "비용보다 큰 목표", "목표가 왕복 비용의 3배는 돼야 이겨도 남아요", "v4.38", "judge"),
    ("buy", "거르기", "filter:dup", "하루 한 번", "같은 종목을 하루에 되풀이해 사지 않아요", "v4.38", "judge"),
    ("buy", "거르기", "filter:quote", "시세", "지금 가격을 모르면 사지 않아요", "v4.38", "ops"),
    ("buy", "거르기", "filter:chase", "쫓지 않기", "추천가보다 많이 오른 뒤에 사면 남는 폭이 줄고 손절에 가까워져요", "", "judge"),
    ("buy", "거르기", "filter:qty", "1주 이상", "배정 금액으로 1주를 못 사면 넘겨요", "v4.38", "ops"),
    ("buy", "거르기", "filter:fail", "주문 실패", "주문이 안 들어가면 기록만 남겨요", "v4.38", "ops"),
    ("buy", "거르기", "filter:etc", "그 밖", "예전 기록의 분류 안 된 이유", "v4.38", "ops"),
    ("buy", "때", "when:early", "급변 때 일찍 묻기(켠 플랜만)", "지수가 직전 추천 때보다 크게 움직이면 간격을 기다리지 않고 바로 다시 물어요(하루 4번까지)", "v5.37", "judge"),
    ("buy", "크기", "size:pct", "1회 비중", "한 종목에 굴리는 돈의 정한 비율만", "v4.38", "judge"),
    ("buy", "크기", "size:warmup", "검증 단계는 작게", "새 플랜의 처음 몇 건은 줄여 사서 틀려도 덜 잃어요", "v4.38", "judge"),
    ("buy", "크기", "size:watch", "관망이면 작게", "AI가 관망인데도 사는 플랜은 크기를 줄여요", "v4.51", "judge"),
    ("buy", "크기", "size:vol", "흔들림 맞춤 손절(켠 플랜만)", "평소 흔들림보다 좁은 손절은 잡음에 걸려요. 손절을 넓히고 수량을 줄여 잃는 돈은 같게", "v4.86", "judge"),
    ("buy", "크기", "size:regime", "국면별 비중(켠 플랜만)", "지수가 20일선 아래인 약한 장에서는 작게, 오르는 장에서는 조금 크게 사요", "v5.13", "judge"),
    ("buy", "크기", "size:risk", "위험 기준 수량(켠 플랜만)", "종목마다 같은 돈이 아니라, 손절했을 때 잃는 돈이 같도록 수량을 정해요", "v5.11", "judge"),
    ("buy", "크기", "size:edge", "확신만큼 크기(켠 플랜만)", "보정 승률과 손익비로 본 기대값이 큰 종목에 더, 작은 종목에 덜 실어요(0.5~1.5배)", "v5.11", "judge"),
    ("buy", "크기", "size:in", "분할 매수(켠 플랜만)", "확신만큼 먼저 사고 오르는 걸 확인한 뒤 나머지. 바로 밀리면 덜 잃어요", "v4.89", "judge"),
    ("sell", "팔기", "exit:익절", "익절", "목표에 닿으면 이익을 확정해요", "v3.5", "judge"),
    ("sell", "팔기", "exit:손절", "손절", "사기 전에 정한 선에서 손실을 끊어요", "v3.5", "judge"),
    ("sell", "팔기", "exit:재료 무효", "재료 무효(켠 플랜만)", "살 때의 재료가 번복·실현·악재로 사라졌다고 AI가 뉴스로 확인하면 손절·기한을 기다리지 않고 정리해요", "v5.12", "judge"),
    ("sell", "팔기", "exit:추적 익절", "추적 익절", "오른 뒤 최고점에서 밀리면 번 것을 지켜요", "v4.38", "judge"),
    ("sell", "팔기", "exit:분할 익절", "분할 매도(켠 플랜만)", "힘이 약하면 일찍·많이, 강하면 늦게·조금만 팔아 나머지를 길게", "v4.89", "judge"),
    ("sell", "팔기", "exit:이익 지키기", "남긴 몫의 지킴선", "나눠 판 뒤 남긴 몫이 번 것의 일부 아래로 내려오면 정리", "v4.89", "judge"),
    ("sell", "팔기", "exit:본전 정리", "남긴 몫의 본전 손절", "나눠 판 뒤 남긴 몫은 본전 아래로는 들고 가지 않아요", "v4.88", "judge"),
    ("sell", "팔기", "exit:시간 정리", "시간 정리(단타)", "단타는 장 마감 전·정한 시각에 정리해 밤사이 위험을 안 져요", "v4.38", "judge"),
    ("sell", "팔기", "exit:기간 정리", "기간 정리(스윙)", "정한 보유 기간이 지나면 정리해요", "v4.38", "judge"),
    ("sell", "팔기", "exit:종료 정리", "종료 정리", "자동매매를 끝내거나 전체 손실 한도에 닿으면 전부 정리", "v4.38", "ops"),
]
# 매수·매도 로직 변경 이력(새것이 위). kind: add 더함 / change 고침 / remove 지움 / check 전체 점검
LOGIC_LOG = [
    ("2026-11-24", "v5.37", "add", "buy", "추천 사이 실시간 가격 쓰기(실험 플랜 3개, 실전 꺼짐)",
     "실험 플랜 '눌림 기다리기'(추천가보다 올라 있으면 버리지 않고 추천가 근처로 내려올 때 삼), '조건 걸어 두고 사기'(AI가 낸 지정가·돌파가에 닿을 때 삼), '급변 때 일찍 묻기'(지수가 직전 추천 때보다 단타 0.8%p·스윙 1.2%p 넘게 움직이면 바로 빠른 재추천). 기다리는 매수는 5초마다 보고, 닿으면 모든 점검·거르기를 다시 거친다. AI 답에 선택 항목 '돌파 매수가'를 더함",
     "추천과 추천 사이(실험 20분, 실전은 더 길게)에 가격이 움직여도 새 매수는 쉬고 있었음. AI 비용 없이 실시간 가격으로 놓친 기회를 잡는지 모의에서 먼저 비교"),
    ("2026-11-24", "v5.36", "change", "buy", "미국 후보에 거래대금 상위 · 스팩·유닛·채권형 제외 · 기준선 플랜은 다 사기",
     "미국 단타·스윙 후보에 거래대금 상위를 넣고 상승률·거래량 순위와 번갈아 섞음. 미국 거래량 순위를 채우던 스팩·유닛·워런트·권리와 채권형 상품, 한글 이름의 레버리지 ETF('3배')를 경고·특수 종목 거르기에 더함. 실험 '가리지 않고 사기'는 승률·점수 하한을 0으로(범위 밖 고정값, 실험 전용)",
     "11/23(나스닥 +6.6%)에 미국 후보가 상승률 상위(+20~30%)뿐이라 17번 추천에 매수 0건. 거래대금 상위의 주도 대형주(AMD·메타·알파벳 +6%대)는 후보에 한 번도 오르지 못했고, 거래량 순위는 10달러짜리 스팩 유닛으로 차 있었음. 기준선 플랜도 승률 40% 하한 때문에 AI 추천 자체의 성적을 재지 못했음"),
    ("2026-11-23", "v5.24", "change", "buy", "경고·특수 종목 제외에 '내가 뺀 종목' 포함",
     "사용자가 제외 목록에 넣은 종목은 후보에서 빼고, AI가 골라도 '경고·특수 종목' 거르기에서 거른다(모의·실전 모두). 새 규칙 코드를 만들지 않고 기존 거르기에 합침",
     "사용자가 아는 사정(이미 다른 계좌에 있음, 믿지 않는 회사 등)을 자동매매에 바로 반영하려고"),
    ("2026-11-23", "v5.21", "add", "buy", "위험 미리 막기(몬테카를로, 켠 플랜만, 실험 먼저)",
     "살 종목을 보유 종목과 합쳐, 지난 120일의 같은 날 수익률을 다시 뽑아 '나쁜 날(95%)' 하루 손실을 잰다(종목별 손실은 손절선×여유에서 멈춤). 하루 손실 한도를 넘으면 수량을 ¾·½·¼로 줄이고, 그래도 넘으면 사지 않음. 기본 꺼짐, 실험 플랜 '위험 미리 막기'. "
     "전체 점검: '하루 손실 한도'는 잃은 뒤에 멈추는 규칙이고 이것은 잃기 전에 크기를 맞추는 규칙이라 서로 보완. '위험 기준 수량'(종목 하나의 손절 손실)과 '쏠림 제한'(상관)을 한 숫자로 묶는 성격이라, 실험에서 이쪽이 낫다면 그 둘을 합칠 후보",
     "한도는 있는데 그 한도를 넘길 수 있는 크기로 사는 어긋남을 없애려고(알라딘식 사전 위험 점검)"),
    ("2026-11-22", "v5.13", "add", "buy", "국면별 비중 · 반대 의견 확인(켠 플랜만, 실험 먼저)",
     "지수 20일선 대비 −3% 아래 0.5배 / 아래 0.75배 / 위 1배 / +2% 위에서 5일 오름 1.2배. 반대 의견: 스윙·장기 정밀 추천마다 한 번, 가장 싼 엔진이 뉴스로 약점을 찾아 '반대'만 거름(판정을 못 받으면 거르지 않음). 둘 다 기본 꺼짐. "
     "전체 점검: '약한 날 쉬기'(당일 지수 등락)와 국면별 비중(여러 날 추세)은 보는 기간이 달라 함께 둠. 실험 결과에서 한쪽이 다른 쪽을 대신하면 합칠 예정",
     "약한 장에서 같은 크기로 사는 문제와, 추천한 AI가 자기 근거를 스스로 의심하지 않는 문제를 줄이려고"),
    ("2026-11-22", "v5.12", "add", "sell", "재료 무효 정리(켠 플랜만, 스윙·장기 실험 먼저)",
     "하루 한 번 가장 싼 엔진이 보유 종목의 재료를 뉴스로 확인해 유효·약화·무효로 판정하고, '무효'만 판다(산 다음 날부터, 주가 하락만으로는 무효가 아님, 판정을 못 받으면 팔지 않음). 기본 꺼짐, 실험 플랜 '재료 감시'로 먼저 비교. "
     "전체 점검: 기존 파는 이유 9개는 모두 가격·시간 기준이라 겹치지 않음",
     "스윙·장기는 재료를 보고 사는데, 재료가 사라져도 손절선에 닿을 때까지 들고 있는 빈틈을 메우려고"),
    ("2026-11-22", "v5.12", "check", "buy", "추천 자료 넓히기: 수급 · 뉴스 점수 · 넓게 훑기 · 장 시작 전 메모",
     "후보마다 외국인·기관 5·20일 순매수를 붙이고, AI가 뉴스 영향을 −2~+2로 적게 하고, 장이 끝난 뒤 거래대금 상위 200종목을 일봉·수급으로 훑어 스윙·장기 후보에 6종목까지 보태고, 장 시작 전 AI 메모를 그날 정밀 추천에 함께 줌. 사고파는 규칙은 그대로",
     "AI가 보는 자료를 넓히고, 수급·뉴스 점수·종목 출처별 성적을 따로 재서 실제로 도움이 되는지 확인하려고"),
    ("2026-11-22", "v5.11", "add", "buy", "쏠림 제한 · 위험 기준 수량 · 확신만큼 크기(켠 플랜만, 실험 먼저)",
     "같은 업종·테마 1종목 + 20일 상관 0.8 이상 제외 / 손절 시 굴리는 돈의 0.5%만 잃는 수량 / 기대값(켈리 비율)에 따라 0.5~1.5배. 셋 다 기본 꺼짐이고 실험 플랜(쏠림 줄이기·위험 기준 수량·확신만큼 크기)으로 먼저 비교. "
     "전체 점검: '위험 기준 수량'은 '흔들림 맞춤 손절'과 목적이 겹치지만(잃는 돈을 고르게) 손절 폭을 건드리지 않는 점이 달라 실험 결과를 보고 하나로 합칠 예정",
     "한 번에 같이 잃는 일을 줄이고, 같은 돈을 걸어도 위험이 종목마다 달라지는 문제를 줄이려고(블랙록식 위험 예산)"),
    ("2026-11-22", "v5.10", "add", "buy", "경고·특수 종목 제외",
     "거래소 경고(관리·투자경고·투자위험·단기과열·정리매매)가 붙은 종목, 스팩, 우선주, 레버리지·인버스·ETN을 추천 후보에서 빼고, AI가 후보 밖에서 찾아 와도 사지 않음. "
     "전체 점검: 겹치는 규칙 없음(가격 1,000원 미만·상한가 근처 제외는 후보 단계에 그대로 둠). 개선안은 두 묶음의 차이가 우연이 아닐 가능성 90% 이상일 때만 올리도록 고침",
     "AI가 추천 자리를 규칙대로 사고팔 수 없는 종목에 쓰지 않게, 그리고 적은 표본의 우연으로 기준을 바꾸지 않게"),
    ("2026-11-20", "v4.90", "check", "both", "규칙 대장과 점검표를 만들고 전체 점검",
     "매수 32개·매도 9개 규칙을 대장에 올리고, 규칙마다 최근 30일에 얼마나 걸렸고 도움이 됐는지 보이게 함. 겹치거나 죽은 규칙은 이번 점검에서 못 찾음(자료가 이틀치라 판정 보류)",
     "규칙이 늘기만 하지 않게, 고칠 때마다 전체를 보고 필요 없는 것은 지우려고"),
    ("2026-11-20", "v4.89", "remove", "both", "'절반 고정' 분할 규칙 삭제", "v4.88의 '목표 절반에서 절반 매도 / 절반만 먼저 매수'를 지우고 힘(승률·지수·오른 속도·남은 시간) 기반 비율로 바꿈",
     "강한 종목과 약한 종목을 똑같이 나누면 이익을 덜 키우거나 늦게 챙겨서"),
    ("2026-11-20", "v4.89", "add", "sell", "남긴 몫의 지킴선·2차 분할 매도", "약하면 번 것의 25%(2차 뒤 50%)를 지킴선으로, 목표의 80%에서 한 번 더 나눠 팖", "오른 뒤 다 돌려주는 일을 줄이려고"),
    ("2026-11-20", "v4.88", "add", "both", "분할 매도·분할 매수(켠 플랜만)", "기록을 몫마다 한 줄로 나눠 실전 주문·체결 확인도 그대로 쓰게 함. 자리 수는 종목 수로 셈", "이익 일부는 먼저 챙기고 나머지는 길게, 바로 밀리는 매수는 덜 잃게"),
    ("2026-11-20", "v4.86", "add", "buy", "흔들림 맞춤 손절 · 실험 '약한 날 쉬기'", "손절이 최근 흔들림(ATR)보다 좁으면 넓히고 수량을 줄임. 지수 -1% 아래면 안 사는 실험 플랜",
     "11/19 오른 장 전부 익절, 11/20 내린 장 전부 손절 — 손절폭과 지수 기준의 효과를 숫자로 보려고"),
    ("2026-11-20", "v4.79", "change", "buy", "한도가 차면 빠른 재추천으로", "정밀 추천이 남은 AI 한도를 넘기면 그 차례는 빠른 재추천으로 받음", "하루 중간에 매수 판단이 통째로 멈추지 않게"),
    ("2026-11-19", "v4.74", "change", "buy", "성과가 나쁘면 추천 엔진을 올림", "정밀 추천 거래 20건마다 평균 0% 이하·이익 45% 미만이면 한 칸 위 엔진, 더 나빠지면 되돌림", "판단의 질이 승률을 좌우해서"),
    ("2026-11-19", "v4.73", "change", "buy", "학습은 정밀 추천 결과만", "빠른 재추천 결과는 승률 보정·규칙 복기에서 뺌", "싼 엔진의 잡음이 기준을 흐리지 않게"),
    ("2026-11-19", "v4.64", "add", "buy", "빠른 재추천", "정밀 추천 사이에 싼 엔진으로 자주 다시 물어 매수 판단", "적은 비용으로 기회를 더 자주 보려고"),
    ("2026-11-19", "v4.62", "change", "buy", "모의는 1주라도 삼", "배정 금액이 모자라도 현금이 되면 1주", "돈이 모자라 검증이 안 되는 일을 없애려고"),
    ("2026-11-19", "v4.57", "add", "sell", "익절·손절 폭 배수(실험)", "AI가 낸 폭을 0.5배·1.5배로 굴려 비교", "파는 타이밍이 수익에 주는 영향을 재려고"),
    ("2026-11-19", "v4.51", "add", "buy", "지수가 약한 날 쉼 · 관망 뒤 쉼 · AI 비용 한도", "지수가 기준 아래면 AI를 부르지 않음(실전 기본 -1%)", "약한 장의 낮은 승률과 헛돈을 피하려고"),
    ("2026-11-18", "v4.41", "change", "buy", "기회 분석으로 기준 조정", "건너뛴 종목의 실제 결과가 8건 이상 좋을 때만 승률·점수 기준을 한 단계 낮춤(처음 플랜의 바닥 아래로는 안 내림)", "너무 깐깐해 놓친 수익을 되찾으려고"),
    ("2026-11-18", "v4.38", "add", "both", "AI 자동매매 기본 규칙", "승률·점수·손익비·비용 기준, 동시 보유·1회 비중, 하루·전체 손실 한도, 익절·손절·추적 익절·시간·기간 정리", "추천을 정한 규칙대로 실행해 사람 실수를 줄이려고"),
    ("2026-11-17", "v3.5", "add", "sell", "추천마다 목표·손절선", "AI가 종목마다 익절·손절 폭을 내고, 켜 둔 종목은 자동 매도", "들어갈 때 나올 곳을 함께 정하려고"),
]

PRINCIPLES = [
    (CORE[0], CORE[1]),
    ("검증 먼저", "새 기준은 모의 실험에서 먼저 굴리고, 한 플랜이 30건·10거래일 이상에서 AI 비용까지 빼고 플러스일 때만 실전 후보로 올려요."),
    ("비용까지 뺀 수익", "AI 비용도 수수료처럼 수익을 깎아요. 모든 성적은 비용을 뺀 값으로 보고, 비용 한도 안에서 엔진·횟수를 맞춰요."),
    ("손실 먼저 막기", "하루 손실 한도, 연속 손절이면 그날 쉬기, 손절선은 사기 전에 정해요. 큰 손실 한 번이 작은 이익 여러 번을 지워요."),
    ("숫자로 고치기", "기준은 실제 결과(건너뛴 종목 포함)가 쌓였을 때만 바꾸고, 실전 변경은 승인 후 반영해요."),
    ("판단의 질에 투자", "성과가 나쁘면 더 좋은 엔진으로 올리고, 복기는 처음부터 좋은 엔진을 써요."),
]


class Evolution:
    def __init__(self, rt):
        self.rt = rt

    # ------------------------------------------------------------------ 한눈에
    def overview(self):
        db, at, fx = self.rt.db, self.rt.autotrade, self.rt.ai.fx()
        firsts = [x for x in (db.one("SELECT MIN(ts) t FROM ai_picks")["t"], db.one("SELECT MIN(created_at) t FROM at_runs")["t"]) if x]
        since = min(firsts + [DEV[0][1]])[:10]                 # 투자 엔진을 만들기 시작한 날부터
        pk = db.one("SELECT COUNT(*) n FROM ai_picks WHERE COALESCE(lite,0)=0")["n"] if self._has("ai_picks", "lite") else db.one("SELECT COUNT(*) n FROM ai_picks")["n"]
        lite = "AND COALESCE(lite,0)=0" if self._has("ai_outcomes", "lite") else ""
        g = db.one(f"SELECT COUNT(*) n, AVG(net_pct) a, COALESCE(SUM(net_pct>0),0) w FROM ai_outcomes WHERE status!='live' AND net_pct IS NOT NULL {lite}")
        # 자동매매: 모의·실전 각각 (원화, AI 비용 뺀 값)
        modes = {}
        cost_by = {}
        for r in db.q("SELECT r.mode, r.mkt, l.day, l.data FROM at_log l JOIN at_runs r ON r.id=l.run_id WHERE l.kind='cycle' AND l.data IS NOT NULL"):
            u = float((json.loads(r["data"]) or {}).get("usd") or 0)
            if u:
                cost_by[(r["mode"], r["day"])] = cost_by.get((r["mode"], r["day"]), 0) + u * fx
        pnl_by = {}
        for r in db.q("SELECT r.mode, t.mkt, t.exit_day, t.pnl, t.pnl_pct FROM at_trades t JOIN at_runs r ON r.id=t.run_id WHERE t.status='closed'"):
            k = fx if r["mkt"] == "us" else 1.0
            m = modes.setdefault(r["mode"], {"n": 0, "w": 0, "pnl": 0.0, "sp": 0.0})
            m["n"] += 1
            m["w"] += 1 if (r["pnl"] or 0) > 0 else 0
            m["pnl"] += (r["pnl"] or 0) * k
            m["sp"] += r["pnl_pct"] or 0
            pnl_by[(r["mode"], r["exit_day"])] = pnl_by.get((r["mode"], r["exit_day"]), 0) + (r["pnl"] or 0) * k
        auto = {}
        for mode in ("paper", "real"):
            m = modes.get(mode, {"n": 0, "w": 0, "pnl": 0.0, "sp": 0.0})
            cost = sum(v for (md, _), v in cost_by.items() if md == mode)
            auto[mode] = {"n": m["n"], "win": round(m["w"] / m["n"] * 100) if m["n"] else None, "avg": round(m["sp"] / m["n"], 2) if m["n"] else None,
                          "pnl": round(m["pnl"]), "cost": round(cost), "net": round(m["pnl"] - cost)}
        # 날짜별 누적(비용 뺀 손익)
        days = sorted({d for _, d in list(pnl_by) + list(cost_by) if d})
        curve = {}
        for mode in ("paper", "real"):
            s, pts = 0.0, []
            for d in days:
                s += pnl_by.get((mode, d), 0) - cost_by.get((mode, d), 0)
                pts.append(round(s))
            curve[mode] = pts
        usd_all = db.one("SELECT COALESCE(SUM(usd),0) s FROM ai_usage")["s"]
        usd_m = self.rt.ai.month_usd()[0]
        ch = {r["source"]: r["n"] for r in db.q("SELECT source, COUNT(*) n FROM at_changes WHERE source NOT IN ('start') GROUP BY source")}
        eng = db.one("SELECT COUNT(*) n FROM at_log WHERE kind='control' AND data LIKE '%\"engine\": {%'")["n"]
        lessons = sum(int((self.rt.aipick.lessons(h) or {}).get("ver") or 0) for h in ("day", "swing", "month"))
        return {"since": since, "days": (date.today() - date.fromisoformat(since)).days + 1,
                "picks": pk, "graded": g["n"], "pick_avg": round(g["a"], 2) if g["a"] is not None else None, "pick_win": round(g["w"] / g["n"] * 100) if g["n"] else None,
                "auto": auto, "curve": {"days": days, **curve}, "usd_all": round(usd_all, 2), "usd_month": round(usd_m, 2), "cost_all": round(usd_all * fx),
                "changes": {"plan": sum(v for k, v in ch.items() if k != "user"), "user": ch.get("user", 0), "engine": eng, "lessons": lessons},
                "dev": len(DEV), "runs": db.one("SELECT COUNT(*) n FROM at_runs WHERE status IN ('running','paused','closing')")["n"], "fx": fx,
                "auto_on": at.auto_on()}

    def _has(self, table, col):
        try:
            return any(r["name"] == col for r in self.rt.db.q(f"PRAGMA table_info({table})"))
        except Exception:
            return False

    # ------------------------------------------------------------------ 걸어온 길
    def timeline(self, limit=160):
        db, out = self.rt.db, []
        for i, (v, d, a, t, what, why) in enumerate(DEV):
            out.append({"day": d, "ts": f"{d}T00:00:{i:03d}", "kind": "dev", "area": a, "area_label": AREAS[a], "title": f"{v} {t}", "what": what, "why": why})
        names = {r["id"]: r for r in db.q("SELECT id, name, mode, mkt, style, lab_id, variant, created_at, ended_at, status FROM at_runs")}
        src = {"ai": "AI 분석", "auto": "자동 분석", "self": "스스로 반영(모의)", "user": "직접 고침", "opp": "기회 분석"}
        for r in db.q("SELECT run_id, ts, ver, source, changes FROM at_changes WHERE source!='start' ORDER BY id DESC LIMIT 120"):
            run = names.get(r["run_id"]) or {}
            cs = json.loads(r["changes"] or "[]")
            out.append({"day": r["ts"][:10], "ts": r["ts"], "kind": "plan", "area": "auto", "area_label": "플랜 조정", "title": f"{run.get('name', '')} 플랜 {r['ver']}판",
                        "what": " · ".join(f"{c.get('label') or c.get('key')} {c.get('from')}→{c.get('to')}" for c in cs)[:240],
                        "why": src.get(r["source"], r["source"]) + (f" · {cs[0].get('why')}" if cs and cs[0].get("why") else ""), "run": r["run_id"]})
        for r in db.q("SELECT run_id, ts, status, source, summary FROM at_proposals WHERE status IN ('pending','rejected') ORDER BY id DESC LIMIT 40"):
            run = names.get(r["run_id"]) or {}
            out.append({"day": r["ts"][:10], "ts": r["ts"], "kind": "proposal", "area": "learn", "area_label": "개선안", "title": f"{run.get('name', '')} 개선안 {'승인 대기' if r['status'] == 'pending' else '반영 안 함'}",
                        "what": r["summary"][:240], "why": src.get(r["source"], r["source"]), "pending": r["status"] == "pending", "run": r["run_id"]})
        for r in db.q("SELECT run_id, ts, msg FROM at_log WHERE kind='control' AND (data LIKE '%\"engine\": {%' OR msg LIKE '%AI 비용 한도%' OR msg LIKE '%비용 목표%') ORDER BY id DESC LIMIT 60"):
            run = names.get(r["run_id"]) or {}
            eng = "엔진" in r["msg"]
            out.append({"day": r["ts"][:10], "ts": r["ts"], "kind": "engine" if eng else "cost", "area": "ai" if eng else "cost", "area_label": "엔진 교체" if eng else "비용 조정",
                        "title": run.get("name", ""), "what": r["msg"][:240], "why": "", "run": r["run_id"]})
        for r in db.q("SELECT run_id, ts, msg FROM at_log WHERE kind='review' AND msg LIKE '%분석 ·%' ORDER BY id DESC LIMIT 40"):
            run = names.get(r["run_id"]) or {}
            out.append({"day": r["ts"][:10], "ts": r["ts"], "kind": "review", "area": "learn", "area_label": "복기", "title": run.get("name", ""), "what": r["msg"][:240], "why": "", "run": r["run_id"]})
        for h, lbl in (("day", "당일"), ("swing", "1주"), ("month", "1달")):
            ls = self.rt.aipick.lessons(h) or {}
            if ls.get("text") and ls.get("at"):
                out.append({"day": str(ls["at"])[:10], "ts": str(ls["at"]), "kind": "lesson", "area": "learn", "area_label": "추천 규칙",
                            "title": f"{lbl} 추천 규칙 {ls.get('ver', 1)}판", "what": str(ls["text"])[:300], "why": f"추천 {ls.get('n') or 0}건의 결과를 복기"})
        for r in names.values():
            if r.get("lab_id") and r.get("variant") == "base":
                out.append({"day": r["created_at"][:10], "ts": r["created_at"], "kind": "lab", "area": "lab", "area_label": "실험 시작",
                            "title": f"{'국내' if r['mkt'] == 'kr' else '미국'} {'단타' if r['style'] == 'day' else '스윙'} 실험 묶음", "what": "같은 추천을 기준이 다른 플랜들로 동시에 굴려 비교", "why": ""})
            elif not r.get("lab_id"):
                out.append({"day": r["created_at"][:10], "ts": r["created_at"], "kind": "run", "area": "auto", "area_label": "실전 시작" if r["mode"] == "real" else "모의 시작",
                            "title": r["name"], "what": "", "why": "", "run": r["id"]})
        out.sort(key=lambda x: x["ts"], reverse=True)
        return out[:limit]

    # ------------------------------------------------------------------ 지금 로직
    def logic(self):
        from . import aipick as AP, autotrade as AT
        from .ai import LITE_MODEL, MODELS
        rt, at = self.rt, self.rt.autotrade
        c = rt.ai.conf()
        name = lambda m: MODELS.get(m, (m,))[0].split(" · ")[0]
        hz = []
        for h, H in AP.HORIZONS.items():
            st = rt.aipick.learn_stats(None, h)
            tn = rt.aipick.tune(h) if st["n"] >= H["min"] else {}
            ls = rt.aipick.lessons(h) or {}
            hz.append({"h": h, "label": H["label"], "n": st["n"], "min": H["min"], "step": H["step"], "overall": st.get("overall"),
                       "insights": (st.get("insights") or [])[:3], "tune": {k: tn.get(k) for k in ("tp", "sl", "avoid", "prefer", "hold")} if tn.get("ready") else None,
                       "lesson": {"ver": ls.get("ver"), "at": str(ls.get("at") or "")[:10], "text": str(ls.get("text") or "")[:400]} if ls.get("text") else None,
                       "next_lesson": max(0, H["step"] - (st["n"] - int(ls.get("n") or 0))) if st["n"] >= H["min"] else H["min"] - st["n"]})
        runs = []
        for r in at._runs("WHERE status IN ('running','paused','closing') AND lab_id IS NULL"):
            p = r["params"]
            runs.append({"id": r["id"], "name": r["name"], "mode": r["mode"], "mkt": r["mkt"], "style": r["style"], "model": name(r["model"]), "ver": r["ver"],
                         "keys": [["최소 승률", f"{p['min_win_prob']:g}%"], ["최소 점수", f"{p['min_score']:g}"], ["손익비", f"{p['min_rr']:g} 이상"],
                                  ["동시 보유", f"{p['max_positions']}종목"], ["1회 비중", f"{p['per_trade_pct']:g}%"], ["하루 손실 한도", f"-{p['daily_loss_pct']:g}%"],
                                  ["정밀 추천", f"하루 {p['cycles_per_day']}번"], ["빠른 재추천", f"{p.get('quick_gap_min') or 0}분마다" if p.get("quick_gap_min") else "꺼짐"],
                                  ["하루 AI 한도", f"${float(p.get('ai_daily_usd') or 1):g}"]]})
        labs = []
        for L in (at.labs().get("labs") or []):
            rs = sorted(L["runs"], key=lambda x: -(x.get("net_ret_pct") or 0))
            labs.append({"mkt": L["mkt"], "style": L["style"], "label": f"{'국내' if L['mkt'] == 'kr' else '미국'} {L.get('style_label', '')}",
                         "plans": [{"label": x["label"], "variant": x["variant"], "desc": AT.VARIANTS.get(x["variant"], ("", ""))[1], "net": x.get("net_ret_pct"), "n": x.get("closed")} for x in rs]})
        S = [
            {"key": "ai", "t": "AI 추천", "items": [
                ["무엇을 묻나", "그 시장 지수·거래대금 상위·급등락·뉴스(웹 검색)를 모아 Claude에게 살 종목과 진입·목표·손절가, 승률·점수를 받아요."],
                ["엔진", f"정밀 추천 기본 {name(c.get('model') or 'claude-sonnet-5-5')}(웹 검색 {'켬' if c.get('web_search', True) else '끔'}) · 빠른 재추천 {name(LITE_MODEL)}(검색 없이 숫자만)"],
                ["보유 기간", " · ".join(f"{H['label']}(익절 최대 {H['tp_max']}%·손절 최대 {H['sl_max']}%)" for H in AP.HORIZONS.values())],
                ["배운 것 넣기", "조건별 성적, 실제 결과로 맞춘 익절·손절 폭, 피할·우선할 조건, 지난 복기 규칙을 매 추천에 함께 보내요."],
                ["승률 보정", "AI가 말한 승률을 같은 구간의 실제 적중률로 바꿔 써요(10건 이상 쌓인 구간)."]]},
            {"key": "auto", "t": "자동매매 한 바퀴", "items": [
                ["① 점검(무료)", "장 시간·기간·매수 시간·자리·하루 손실·연속 손절·현금·차례·AI 비용·지수·관망·키·월 예산 13가지"],
                ["② AI 추천", "정밀 추천(하루 몇 번) 사이사이 빠른 재추천. 이미 들고 있거나 오늘 거래한 종목은 빼고 물어요."],
                ["③ 거르기", "승률·점수·손익비·비용(목표가 비용의 3배)·같은 날 중복·추격(추천가보다 많이 오르면 안 삼)"],
                ["④ 크기", "굴리는 돈 × 1회 비중. 처음 몇 건은 검증 단계로 줄여 사고, 관망이면 절반"],
                ["⑤ 주문", "모의는 앱 안 가상 체결(미끄러짐 반영), 실전은 키움 실주문('실전' 입력으로 만든 것만)"],
                ["⑥⑦ 보유·팔기", "익절·손절·추적 익절(최고점에서 밀리면)·시간 정리(단타는 장 마감 전)·기간 정리(스윙)"],
                ["⑧ 배우기", "거래 30건 또는 5거래일마다 성적 분석 → 개선안(실전은 승인 후), 엔진 점검"]]},
            {"key": "learn", "t": "학습·개선", "items": [
                ["추천 성적표", "모든 정밀 추천을 추천 시점 가격으로 가상 매매해 채점(빠른 재추천은 학습에서 뺌)"],
                ["추천 규칙 복기", f"결과가 쌓이면(당일 {AP.HORIZONS['day']['step']}건·1주 {AP.HORIZONS['swing']['step']}건·1달 {AP.HORIZONS['month']['step']}건마다) {name(rt.ai.review_model())}로 복기해 규칙을 고쳐요."],
                ["자동매매 개선안", "조건별 성적 + 건너뛴 종목의 실제 결과. 기준은 8건 이상 결과가 좋을 때만 한 단계 낮추고, 처음 플랜의 바닥 아래로는 안 내려요."],
                ["엔진 올리기", f"정밀 추천 거래 {AT.ESC_N}건마다 평균 0% 이하 또는 이익 비율 {AT.ESC_WIN}% 미만이면 Haiku→Sonnet→Opus→Fable 한 칸 위로. 더 나빠지면 되돌리고 멈춰요."],
                ["실전 후보", f"모의 실험의 한 플랜이 {AT.READY_N}건·{AT.READY_DAYS}거래일 이상에서 AI 비용까지 빼고 플러스일 때"]]},
            {"key": "cost", "t": "비용 관리", "items": [
                ["직접 만든 자동매매", f"하루 AI 한도 = 쓸 돈의 한 달 {AT.COST_PCT:g}%를 하루로 나눈 값(최소 ${AT.RUN_DAY_MIN_USD:g})"],
                ["모의 자동 테스트", f"하루 ${AT.AUTO_DAY_USD:g}을 단타 35%씩·스윙 15%씩 나눠요. 정밀 추천이 남은 한도를 넘기면 그 차례는 빠른 재추천으로."],
                ["복기", f"{name(rt.ai.review_model())} · 가끔만 돌아 한 번 100원 남짓"],
                ["월 예산", f"${c.get('budget_usd') or 0:g} · 이번 달 ${rt.ai.month_usd()[0]:.2f} 사용 · 넘으면 AI를 부르지 않아요"]]},
            {"key": "safe", "t": "안전장치", "items": [
                ["AI는 주문하지 않아요", "AI 추천의 매수는 사람이 해요. 자동 매도는 켜 둔 익절·손절만."],
                ["실전 자동매매", "'실전'을 직접 입력해 만든 것만 실제 주문. 하루 손실 한도·연속 손절이면 그날 쉼"],
                ["조회 전용 키", "내 계좌 보기 키로는 주문이 나가지 않아요."],
                ["실전 변경은 승인", "개선안·엔진 변경의 실전 반영은 승인이 기본이에요(모의만 스스로 반영 가능)."]]},
            {"key": "div", "t": "배당·적립", "items": [
                ["매일 모으기", "아침 8시 뒤 배당 자료를 새로 받아 배당률을 기록하고, 새 배당은 직전과 비교해 인상·삭감을 알려요."],
                ["배당률 위치", "지금 배당률이 지난 5년 범위에서 어디쯤인지(높으면 주가가 싼 편)"],
                ["적립", "정해 둔 날 정해 둔 금액을 사고, 배당 전망과 이어 봐요."]]},
        ]
        return {"sections": S, "horizons": hz, "runs": runs, "labs": labs, "principles": [{"t": t, "d": d} for t, d in PRINCIPLES]}

    # ------------------------------------------------------------------ 앞으로
    def plan(self):
        from . import autotrade as AT
        rt, at, db = self.rt, self.rt.autotrade, self.rt.db
        steps = []
        for a in at.advice():
            lab = f"{'국내' if a['mkt'] == 'kr' else '미국'} {a['style_label']}"
            top = a.get("top") or []
            n, d = a.get("max_n", 0), a.get("max_days", 0)
            st = "done" if a.get("best") else "doing"
            steps.append({"area": "lab", "t": f"{lab}: 실전 후보 찾기", "d": a["text"], "st": st,
                          "prog": [min(n, AT.READY_N), AT.READY_N], "prog2": [min(d, AT.READY_DAYS), AT.READY_DAYS],
                          "top": [{"label": x["label"], "net": x["net_pct"], "n": x["n"], "win": x["win_rate"]} for x in top]})
        pend = db.q("SELECT p.id, p.run_id, p.summary, r.name FROM at_proposals p JOIN at_runs r ON r.id=p.run_id WHERE p.status='pending' ORDER BY p.id DESC LIMIT 5")
        for p in pend:
            steps.append({"area": "learn", "t": f"{p['name']}: 개선안 승인 대기", "d": p["summary"][:200], "st": "wait", "run": p["run_id"]})
        for r in at._runs("WHERE status='running'"):
            if r.get("lab_id") and r.get("variant") != "base":
                continue
            p = r["params"]
            es = at.engine_stats([r], p.get("engine_since") or r["created_at"])
            steps.append({"area": "ai", "t": f"{r['name'] if not r.get('lab_id') else '실험 묶음'}: 다음 엔진 점검", "st": "doing",
                          "d": f"지금 {AT._short_model(r['model'])} · 정밀 추천 거래 {es['n']}/{AT.ESC_N}건" + (f" · 평균 {es['avg']:+.2f}% · 이익 {es['win']}%" if es["n"] else "")
                          + (" · 더 올리지 않기로 함" if p.get("engine_lock") else ""), "prog": [min(es["n"], AT.ESC_N), AT.ESC_N]})
        from . import aipick as AP
        for h, H in AP.HORIZONS.items():
            st = rt.aipick.learn_stats(None, h)
            ls = rt.aipick.lessons(h) or {}
            if st["n"] < H["min"]:
                steps.append({"area": "learn", "t": f"{H['label']} 추천: 첫 규칙 만들기", "d": f"채점된 추천 {st['n']}/{H['min']}건이 모이면 처음 복기해요.", "st": "doing", "prog": [st["n"], H["min"]]})
            else:
                k = st["n"] - int(ls.get("n") or 0)
                steps.append({"area": "learn", "t": f"{H['label']} 추천: 규칙 다시 고치기", "d": f"지난 복기 뒤 새 결과 {min(k, H['step'])}/{H['step']}건", "st": "doing", "prog": [min(k, H["step"]), H["step"]]})
        ideas = [
            {"t": "흔들림 맞춤 손절을 실전에", "d": "실험의 '흔들림 맞춤 손절'이 기본보다 나으면, 실전 플랜에서도 켤 수 있게(플랜의 '흔들림에 맞춰 손절')", "st": "idea"},
            {"t": "실전은 엔진 올리기도 승인 후", "d": "지금은 실전도 성과가 나쁘면 자동으로 올려요. 비용이 커지는 변경이라 승인 방식으로 바꿀 수 있어요.", "st": "idea"},
            {"t": "실험 1등 기준을 실전 플랜에", "d": "실전 후보가 나오면 그 플랜의 기준(승률·점수·손익비·익절 폭)으로 작게 실전을 시작", "st": "idea"},
            {"t": "배당률 위치로 적립 강약", "d": "배당 자료가 쌓이면 5년 대비 배당률이 높은 달엔 적립을 늘리고 낮은 달엔 줄이는 규칙을 시험", "st": "idea"},
        ]
        goal = None
        try:
            pf = rt.settings.get("payoff") or {}
            if pf.get("amount") and pf.get("deadline"):
                goal = {"name": pf.get("name") or "갚기 목표", "amount": pf["amount"], "deadline": pf["deadline"]}
        except Exception:
            goal = None
        return {"steps": steps, "ideas": ideas, "goal": goal}

    # ------------------------------------------------------------------ 매매 규칙(대장 + 최근 쓰임 + 변경 이력)
    def rules(self, days=30):
        U = self.rt.autotrade.rule_use(days)
        base = U["bought"]
        rows = []
        for side, stage, code, t, why, since, kind in RULES:
            typ, key = code.split(":", 1)
            r = {"side": side, "stage": stage, "code": code, "t": t, "why": why, "since": since, "kind": kind, "use": None, "verdict": "", "tone": ""}
            if typ == "filter":
                u = U["filters"].get(key) or {"n": 0, "graded": 0, "avg": None, "win": None}
                r["use"] = u
                if kind == "judge":
                    if not u["n"] and U["seen_days"] < 10:
                        r["verdict"] = f"아직 안 걸림(기록 {U['seen_days']}거래일치 · 10거래일부터 판정)"
                    elif not u["n"]:
                        r["verdict"], r["tone"] = f"최근 {days}일 동안 한 번도 안 걸림 · 없애도 되는지 검토", "check"
                    elif u["graded"] < 8:
                        r["verdict"] = f"{u['n']}번 걸러냄 · 결과를 모으는 중({u['graded']}/8건)"
                    elif u["avg"] > 0 and u["win"] >= 50:
                        r["verdict"], r["tone"] = f"걸러낸 종목이 오히려 좋았음(평균 {u['avg']:+.2f}%, 이익 {u['win']}%) · 기준을 풀지 검토", "check"
                    else:
                        r["verdict"], r["tone"] = f"걸러낸 종목이 실제로 나빴음(평균 {u['avg']:+.2f}%, 이익 {u['win']}%) · 유지", "keep"
                elif u["n"]:
                    r["verdict"] = f"{u['n']}번"
            elif typ == "exit":
                u = U["exits"].get(key)
                r["use"] = u
                r["verdict"] = f"{u['n']}건 · 평균 {u['avg']:+.2f}% · 이익 {u['win']}%" if u else f"최근 {days}일 0건"
            rows.append(r)
        return {"core": {"t": CORE[0], "d": CORE[1]}, "upkeep": UPKEEP, "days": days, "seen_days": U["seen_days"], "bought": base,
                "buy": [r for r in rows if r["side"] == "buy"], "sell": [r for r in rows if r["side"] == "sell"],
                "n": {"buy": sum(1 for r in rows if r["side"] == "buy"), "sell": sum(1 for r in rows if r["side"] == "sell"),
                      "check": sum(1 for r in rows if r["tone"] == "check")},
                "log": [{"day": d, "ver": v, "kind": k, "side": sd, "t": t, "what": what, "why": why} for d, v, k, sd, t, what, why in LOGIC_LOG]}

    def all(self):
        return {"overview": self.overview(), "timeline": self.timeline(), "logic": self.logic(), "plan": self.plan(), "areas": AREAS, "rules": self.rules(),
                "at": datetime.now().isoformat(timespec="minutes")}
