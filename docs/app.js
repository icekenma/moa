"use strict";
/* moa — 휴대폰 화면. 외부 라이브러리 없이 동작한다.
   GitHub Pages에서 열면 설정한 서버(노트북)로, 서버가 직접 준 화면이면 같은 주소로 요청한다. */

const UI_VERSION = "261117.1800";
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
const view = $("#view");

// ---------- 연결 ----------
const store = {
  get(k) { try { return localStorage.getItem("moa." + k) || ""; } catch (e) { return ""; } },
  set(k, v) { try { v ? localStorage.setItem("moa." + k, v) : localStorage.removeItem("moa." + k); } catch (e) { /* 저장 불가 */ } },
};
const memo = {  // 마지막으로 받은 화면 자료(앱을 다시 열어도 바로 보이게)
  get(k) { try { return JSON.parse(localStorage.getItem("moa.m." + k) || "null"); } catch (e) { return null; } },
  set(k, v) { try { localStorage.setItem("moa.m." + k, JSON.stringify(v)); } catch (e) { /* 저장 불가 */ } },
};
const ON_PAGES = location.hostname.endsWith("github.io") || location.protocol === "file:" || store.get("remote") === "1";
class NeedConnect extends Error {}
const apiBase = () => (ON_PAGES ? store.get("server").replace(/\/+$/, "") : "");

async function api(method, url, body, isForm) {
  if (ON_PAGES && !store.get("server")) throw new NeedConnect("서버 주소를 먼저 넣어 주세요");
  const opt = { method, headers: {} };
  const tok = store.get("token");
  if (tok) opt.headers.Authorization = "Bearer " + tok;
  if (apiBase().includes("ngrok")) opt.headers["ngrok-skip-browser-warning"] = "1";
  if (body !== undefined) {
    if (isForm) opt.body = body;
    else { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  }
  let res;
  try { res = await fetch(apiBase() + url, opt); }
  catch (e) { throw new Error(ON_PAGES ? "서버에 연결하지 못했어요. 노트북의 moa와 연결 앱(Tailscale 등)이 켜져 있는지 확인해 주세요" : "서버에 연결하지 못했어요. moa 실행 창이 켜져 있는지 확인해 주세요"); }
  const data = await res.json().catch(() => ({}));
  if (res.status === 401) throw new NeedConnect(data.error || "접속 토큰이 맞지 않아요");
  if (!res.ok) throw new Error(data.error || `요청이 실패했어요 (${res.status})`);
  return data;
}
const GET = (u) => api("GET", u);

// ---------- 도구 ----------
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const won = (n) => (n == null || isNaN(n) ? "-" : Math.round(n).toLocaleString("ko-KR"));
const shortWon = (n) => { const a = Math.abs(n); return a >= 10000 ? (a / 10000).toFixed(a >= 100000 ? 0 : 1).replace(/\.0$/, "") + "만" : won(a); };
const signed = (n) => (n > 0 ? "+" : "") + won(n);
const pct = (n, d = 1) => (n == null ? "-" : (n > 0 ? "+" : "") + Number(n).toFixed(d) + "%");
const tone = (n) => (n > 0 ? "up" : n < 0 ? "down" : "");
const num = (v) => Number(String(v ?? "").replace(/,/g, "")) || 0;
const localToday = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };
const thisMonth = () => localToday().slice(0, 7);
const WD = "일월화수목금토";
const korDate = (iso, wd = true) => { const [y, m, d] = iso.split("-").map(Number); return `${m}월 ${d}일` + (wd ? ` ${WD[new Date(y, m - 1, d).getDay()]}요일` : ""); };
const STATE = { running: "가동 중", paused: "새 매수 멈춤", stopped: "멈춤" };

function toast(msg, err) {
  // 열린 시트(모달) 위에서도 보이게 알림 상자를 맨 위 창 안으로 옮긴다
  const box = $("#toast"), host = [$("#updDlg"), sheet].find((d) => d && d.open) || document.body;
  if (box.parentNode !== host) host.appendChild(box);
  const el = document.createElement("div");
  if (err) el.className = "err";
  el.textContent = msg;
  box.appendChild(el);
  setTimeout(() => el.remove(), err ? 5000 : 2600);
}
async function run(fn, okMsg) {
  try { const r = await fn(); if (okMsg) toast(okMsg); return r; }
  catch (e) {
    if (e instanceof NeedConnect) { closeSheet(); location.hash = "#connect"; }
    toast(e.message, true); throw e;
  }
}
function seg(name, options, value, cls = "") {
  return `<div class="seg ${cls}" role="group" aria-label="${esc(name)}">` +
    options.map(([v, l]) => `<button type="button" data-seg="${esc(name)}" data-v="${esc(v)}" aria-pressed="${v === value}">${esc(l)}</button>`).join("") + `</div>`;
}
function bindSeg(root, name, onPick) {
  $$(`[data-seg="${name}"]`, root).forEach((b) => b.addEventListener("click", () => {
    $$(`[data-seg="${name}"]`, root).forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    onPick(b.dataset.v);
  }));
}
const segVal = (root, name) => ($(`[data-seg="${name}"][aria-pressed="true"]`, root) || {}).dataset?.v;
function moneyInput(el) { el.addEventListener("input", () => { const v = num(el.value); el.value = v ? won(v) : ""; }); }
function confirmTap(btn, label) {
  if (btn.dataset.sure === "1") return true;
  btn.dataset.sure = "1"; btn.dataset.orig = btn.textContent; btn.textContent = label; btn.classList.add("red");
  setTimeout(() => { if (btn.isConnected) { btn.dataset.sure = ""; btn.textContent = btn.dataset.orig; btn.classList.remove("red"); } }, 4000);
  return false;
}

// ---------- 색상 테마(이 기기에만 저장) ----------
const THEMES = {
  navy: { name: "남색", deep: "#111A2B", deep2: "#1C2840", bg: "#EEF1F7", accent: "yellow" },
  black: { name: "블랙", deep: "#0E0E10", deep2: "#1F1F23", bg: "#F0F0F2", accent: "lime" },
  forest: { name: "숲", deep: "#0E2A22", deep2: "#173B31", bg: "#EDF3F0", accent: "mint" },
  ocean: { name: "바다", deep: "#082A3A", deep2: "#0F3D52", bg: "#EBF3F6", accent: "aqua" },
  wine: { name: "와인", deep: "#2A0F19", deep2: "#3D1726", bg: "#F5EEF0", accent: "peach" },
  purple: { name: "보라", deep: "#1C1436", deep2: "#2B2050", bg: "#F0EEF7", accent: "lavender" },
  brown: { name: "커피", deep: "#2A1E16", deep2: "#3B2B20", bg: "#F4F0EC", accent: "apricot" },
  light: { name: "화이트", deep: "#FFFFFF", deep2: "#232C3D", bg: "#F2F4F8", accent: "yellow", light: true },
};
const ACCENTS = {
  yellow: ["노랑", "#FFD84D"], lime: ["라임", "#C8F560"], mint: ["민트", "#7BE0B0"], aqua: ["하늘", "#7FD6F2"],
  peach: ["복숭아", "#FFB38A"], pink: ["핑크", "#FF9EC7"], lavender: ["라벤더", "#C3B1FF"], apricot: ["살구", "#FFC56B"],
};
function themeVars(id, accentId) {
  const t = THEMES[id] || THEMES.navy;
  const a = (ACCENTS[accentId] || ACCENTS[t.accent])[1];
  return t.light ? {
    "--deep": t.deep, "--deep2": t.deep2, "--bg": t.bg, "--hl": a, "--solid": "#172033",
    "--on-deep": "#172033", "--on-deep2": "#5A6578", "--on-deep-strong": "#172033", "--sub-bg": "#EEF1F6",
  } : {
    "--deep": t.deep, "--deep2": t.deep2, "--bg": t.bg, "--hl": a, "--solid": t.deep,
    "--on-deep": "#E9EDF5", "--on-deep2": "#A3ADC2", "--on-deep-strong": "#FFFFFF", "--sub-bg": "rgba(255,255,255,.08)",
  };
}
function applyTheme(id, accentId, save = true) {
  const v = themeVars(id, accentId);
  Object.entries(v).forEach(([k, val]) => document.documentElement.style.setProperty(k, val));
  const mc = document.querySelector('meta[name="theme-color"]');
  if (mc) mc.content = v["--deep"];
  if (save) {
    store.set("theme", id); store.set("accent", accentId || "");
    try { localStorage.setItem("moa.themeVars", JSON.stringify(v)); } catch (e) { /* 저장 불가 */ }
  }
}

// ---------- 은행·단축어 ----------
const BANKS = [["하나", "하나은행"], ["NH농협", "NH농협은행"], ["신한", "신한은행"], ["KB국민", "KB국민은행"], ["우리", "우리은행"],
  ["IBK", "IBK기업은행"], ["카카오뱅크", "카카오뱅크"], ["토스뱅크", "토스뱅크"], ["케이뱅크", "케이뱅크"], ["기타", "기타 은행"]];
const bankLabel = (code) => (BANKS.find((b) => b[0] === code) || [code, code])[1];
const maskAcct = (no) => { const d = String(no || "").replace(/\D/g, ""); return d ? "····" + d.slice(-4) : "계좌번호 없음"; };

// ---------- 키움 계좌 카드(설정·투자 공통). 마지막 조회를 휴대폰에도 남겨 서버가 꺼져 있어도 보여 준다 ----------
const kwCache = {
  save(d) { if (d && d.total != null && d.ok !== false) store.set("kwLast", JSON.stringify(d)); },
  load() { try { return JSON.parse(store.get("kwLast") || "null"); } catch (e) { return null; } },
};
async function kwFetch(force) {
  try { const d = await GET("/api/myaccount" + (force ? "?force=1" : "")); kwCache.save(d); return d; }
  catch (e) {
    if (e instanceof NeedConnect) throw e;
    const c = kwCache.load();
    if (c) return { ...c, ok: false, offline: true, error: e.message };
    throw e;
  }
}
const fullAcct = (no) => { const d = String(no || "").replace(/\D/g, ""); return d.length >= 8 ? `${d.slice(0, 4)}-${d.slice(4, 8)}${d.length > 8 ? "-" + d.slice(8) : ""}` : d; };
function usLine(d) {  // 미국 주식·달러가 총자산에 합쳐졌을 때 한 줄 설명
  const u = d.us;
  if (!u || u.error || !(u.eval || u.usd_krw)) return "";
  return `<div class="tiny">국내 ${won(d.kr_total)}원 + 미국 주식 ${won(u.eval)}원${u.usd ? ` + 달러 ${usd(u.usd)}(약 ${won(u.usd_krw)}원)` : ""}${u.fx ? ` · 환율 ${won(u.fx)}원` : ""}</div>`;
}
function kwCardHtml(d) {
  if (!d || !d.configured) return `<h2>키움 계좌</h2><div class="small muted">아래 키움증권(조회 전용)에 앱키·시크릿키를 넣고 연결 확인을 누르면 여기서 언제든 계좌를 볼 수 있어요.</div>`;
  if (d.total == null) return `<div class="kw-top"><b>키움증권</b></div><div class="notice warn">${esc(d.error || "불러오는 중")}</div>`;
  const live = d.ok !== false, H = d.holdings || [];
  const when = (d.updated_at || "").replace("T", " ");
  const stateTxt = live ? "실시간" : d.offline ? "서버 꺼짐 · 마지막 조회값" : "연결 끊김 · 마지막 조회값";
  return `
    <div class="kw-top"><b>키움증권</b><span>${esc(d.server || "")} · 조회 전용</span>
      <span class="tiny live-dot ${live ? "on" : ""} kw-live">${stateTxt}</span></div>
    <div class="kw-no">${d.acct_no ? esc(fullAcct(d.acct_no)) : "계좌번호는 다음 조회 때 받아와요"}</div>
    <div><div class="kw-lbl">총자산(추정예탁자산)</div><div class="kw-total">${won(d.total)}<small>원</small></div>
      <div class="kw-pnl ${tone(d.pnl)}">평가손익 ${signed(d.pnl)}원 (${pct(d.pnl_pct, 2)})</div></div>
    <div class="kw-kv"><div><span>주식 평가</span><b>${won(d.eval)}</b></div><div><span>예수금</span><b>${won(d.cash)}</b></div><div><span>주문 가능</span><b>${won(d.orderable)}</b></div></div>
    ${usLine(d)}
    ${H.length ? `<div class="kw-list">${H.slice(0, 6).map((h) => `<div><span class="kw-nm">${esc(h.name)}<i>${won(h.qty)}주</i></span><b>${won(h.value)}</b><em class="${tone(h.pnl)}">${pct(h.pnl_pct, 2)}</em></div>`).join("")}
      ${H.length > 6 ? `<div class="kw-more">외 ${H.length - 6}종목</div>` : ""}</div>` : `<div class="kw-more">보유 종목 없음</div>`}
    ${!live && d.error ? `<div class="kw-err">${esc(d.error)}</div>` : ""}
    <div class="kw-foot"><span>${esc(when.slice(5, 19))} 기준</span>
      <span><button type="button" class="kw-btn" id="kwReload">새로고침</button><a class="kw-btn" href="#invest/account">전체 보기</a></span></div>`;
}
function bindKwCard(box) {
  const b = $("#kwReload", box);
  if (b) b.onclick = async () => { b.disabled = true; try { box.innerHTML = kwCardHtml(await run(() => kwFetch(true))); } finally { bindKwCard(box); } };
}
async function shortcutGuide() {
  const serverUrl = (ON_PAGES ? store.get("server") : location.origin).replace(/\/+$/, "");
  let token = store.get("token");
  if (!token && !ON_PAGES) token = (await GET("/api/connect-info").catch(() => ({}))).token || "";
  return `<details class="card"><summary>아이폰 단축어로 문자 자동 보내기</summary>
    <div class="small" style="line-height:1.75">
      <b>1.</b> 단축어 앱 → 아래 <b>자동화</b> → 오른쪽 위 <b>＋</b> → <b>메시지</b><br>
      <b>2.</b> <b>메시지에 다음이 포함됨</b>에 <span class="code" style="padding:1px 6px">원</span> 입력(보낸 사람은 비워 두기)<br>
      <b>3.</b> <b>즉시 실행</b> 선택(실행 전 확인 끄기) → 다음 → <b>새로운 빈 단축어</b><br>
      <b>4.</b> 동작 추가: <b>URL 콘텐츠 가져오기</b><br>
      &nbsp;&nbsp;· URL: 아래 주소 · 방법: <b>POST</b><br>
      &nbsp;&nbsp;· 헤더: <b>Authorization</b> = 아래 값<br>
      &nbsp;&nbsp;· 본문 요청: <b>JSON</b>, 키 <b>text</b>, 값은 변수 <b>단축어 입력</b>(메시지 내용)<br>
      <b>5.</b> 완료. 카드·은행 문자가 오면 몇 초 안에 가계부와 계좌 잔액에 들어가요.</div>
    <div class="field">URL<div class="code">${esc(serverUrl)}/api/inbox</div></div>
    <div class="field">Authorization 값<div class="code">Bearer ${esc(token || "(서버 탭의 접속 토큰)")}</div></div>
    <div class="tiny">휴대폰 Tailscale이 켜져 있어야 보내져요. 메뉴 이름은 iOS 버전에 따라 조금 다를 수 있어요.</div>
  </details>`;
}
function bankSheet(a, done) {
  let bank = a?.bank || "하나";
  openSheet(a ? "은행 계좌 고치기" : "은행 계좌 추가", `
    <div class="field">은행<div class="chips">${BANKS.map(([c, l]) => `<button type="button" class="chip" data-bank="${c}" aria-pressed="${c === bank}">${esc(l)}</button>`).join("")}</div></div>
    <label class="field">계좌 이름<input id="bkName" value="${esc(a?.name || "")}" placeholder="예: 하나 급여통장"></label>
    <label class="field">계좌번호<input id="bkNo" inputmode="numeric" value="${esc(a?.acct_no || "")}" placeholder="123-456789-12345">
      <span class="hint">알림 문자의 계좌번호 끝자리로 이 계좌를 찾아요. 앱에는 끝 4자리만 보여요.</span></label>
    <div class="fields">
      <label class="field">종류<select id="bkType"><option value="cash" ${a?.type !== "deposit" ? "selected" : ""}>입출금</option><option value="deposit" ${a?.type === "deposit" ? "selected" : ""}>예금·적금</option></select></label>
      <label class="field">지금 잔액<input id="bkBal" class="r" inputmode="numeric" value="${a ? won(a.balance) : ""}" placeholder="0원"></label></div>
    <button class="btn dark wide" id="bkSave">${a ? "고친 내용 저장" : "계좌 추가"}</button>
    ${a ? `<button class="btn soft wide" id="bkDel">이 계좌 지우기</button>` : ""}`, (box) => {
    moneyInput($("#bkBal", box));
    box.onclick = async (ev) => {
      const c = ev.target.closest("[data-bank]");
      if (c) { bank = c.dataset.bank; $$("[data-bank]", box).forEach((z) => z.setAttribute("aria-pressed", String(z === c))); if (!$("#bkName").value) $("#bkName").placeholder = `예: ${bankLabel(bank).replace("은행", "")} 생활비 통장`; }
      if (ev.target.id === "bkSave") {
        const name = $("#bkName").value.trim() || `${bankLabel(bank)} ${maskAcct($("#bkNo").value)}`;
        await run(() => api("POST", "/api/accounts", { id: a?.id, name, type: $("#bkType").value, balance: num($("#bkBal").value), note: a?.note || "", bank, acct_no: $("#bkNo").value, linked: "" }), "계좌를 저장했어요");
        closeSheet(); done && done();
      }
      if (ev.target.id === "bkDel") {
        if (!confirmTap(ev.target, "한 번 더 누르면 지워요")) return;
        await run(() => api("DELETE", `/api/accounts/${a.id}`), "지웠어요"); closeSheet(); done && done();
      }
    };
  });
}

// ---------- 바텀 시트 ----------
const sheet = $("#sheet");
function openSheet(title, html, mount) {
  $("#sheetTitle").textContent = title;
  $("#sheetBody").innerHTML = html;
  if (!sheet.open) sheet.showModal();
  $("#sheetBody").scrollTop = 0;
  if (mount) mount($("#sheetBody"));
}
function closeSheet() { if (sheet.open) sheet.close(); }
$("#sheetClose").onclick = closeSheet;
[sheet, $("#updDlg")].forEach((d) => d && d.addEventListener("close", () => document.body.appendChild($("#toast"))));
sheet.addEventListener("click", (e) => { if (e.target === sheet) closeSheet(); });

// ---------- 차트 ----------
function lineChart(series, opt = {}) {
  const W = 600, H = opt.h || 120, P = 6;
  const all = series.flatMap((s) => s.values).filter((v) => v != null);
  if (all.length < 2) return `<div class="tiny">기록이 쌓이면 추이를 그려요</div>`;
  let lo = Math.min(...all), hi = Math.max(...all);
  if (hi === lo) { hi += 1; lo -= 1; }
  const n = Math.max(...series.map((s) => s.values.length));
  const x = (i) => (i / Math.max(1, n - 1)) * W;
  const y = (v) => P + (1 - (v - lo) / (hi - lo)) * (H - 2 * P);
  let g = "";
  for (let k = 1; k <= 2; k++) g += `<line x1="0" x2="${W}" y1="${(H / 3) * k}" y2="${(H / 3) * k}" stroke="#DDE2E8" stroke-width="1" vector-effect="non-scaling-stroke"/>`;
  for (const s of series) {
    const pts = s.values.map((v, i) => (v == null ? null : `${x(i).toFixed(1)},${y(v).toFixed(1)}`)).filter(Boolean).join(" ");
    g += `<polyline fill="none" stroke="${s.color || "#1B2430"}" stroke-width="${s.w || 2.5}" ${s.dash ? 'stroke-dasharray="6 5"' : ""} stroke-linejoin="round" vector-effect="non-scaling-stroke" points="${pts}"/>`;
  }
  const v0 = series[0].values, li = v0.length - 1;
  if (li >= 0 && v0[li] != null) g += `<circle cx="${x(li)}" cy="${y(v0[li])}" r="5" fill="#FFD84D" stroke="#1B2430" stroke-width="2" vector-effect="non-scaling-stroke"/>`;
  return `<svg class="chart" role="img" aria-label="${esc(opt.label || "추이 그래프")}" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="height:${H}px">${g}</svg>`;
}
function pnlBars(values, labels, h = 96) {
  if (!values.length) return `<div class="tiny">아직 기록이 없어요</div>`;
  const W = 600, max = Math.max(1, ...values.map(Math.abs)), bw = W / values.length, mid = h / 2;
  let g = `<line x1="0" x2="${W}" y1="${mid}" y2="${mid}" stroke="#AEB7C4" stroke-width="1" vector-effect="non-scaling-stroke"/>`;
  values.forEach((v, i) => {
    const hh = (Math.abs(v) / max) * (mid - 4);
    g += `<rect x="${i * bw + bw * 0.15}" width="${bw * 0.7}" y="${v >= 0 ? mid - hh : mid}" height="${Math.max(1, hh)}" fill="${v >= 0 ? "#C9281C" : "#1F55D6"}"><title>${esc(labels[i])} ${signed(v)}원</title></rect>`;
  });
  return `<svg class="chart" role="img" aria-label="날짜별 손익 막대" viewBox="0 0 ${W} ${h}" preserveAspectRatio="none" style="height:${h}px">${g}</svg>`;
}

// ---------- 메뉴(1뎁스 탭 → 2뎁스) ----------
const MENU = {
  home: { title: "홈", subs: [["summary", "요약"], ["activity", "최근 활동"]] },
  ledger: { title: "가계부", subs: [["list", "내역"], ["calendar", "달력"], ["analysis", "분석"], ["budget", "예산"], ["auto", "자동"]] },
  assets: { title: "자산", subs: [["overview", "한눈에"], ["accounts", "계좌"], ["goal", "목표"]] },
  invest: { title: "투자", subs: [["market", "시세"], ["account", "내 계좌"], ["trade", "매매"], ["scalp", "단타"], ["dca", "적립"]], extra: { backtest: "scalp", rules: "scalp", sweep: "scalp", stock: "market" } },
  settings: { title: "설정", subs: [["connect", "서버"], ["accounts", "계좌 연결"], ["trading", "매매"], ["theme", "색상"], ["app", "앱 정보"]] },
};
const lastSub = {};
function resolveSub(h) {
  const m = MENU[h.name];
  if (!m) return h.sub;
  const ok = m.subs.some(([id]) => id === h.sub) || (m.extra && m.extra[h.sub]);
  return ok ? h.sub : lastSub[h.name] || m.subs[0][0];
}
function subnav(name, sub) {
  const m = MENU[name];
  if (!m) return "";
  const on = (m.extra && m.extra[sub]) || sub;
  return `<nav class="subnav" aria-label="${esc(m.title)} 세부 메뉴">${m.subs.map(([id, label]) =>
    `<a href="#${name}/${id}" class="subtab" ${id === on ? 'aria-current="page"' : ""}>${esc(label)}</a>`).join("")}</nav>`;
}

// ---------- 라우터 ----------
const pages = {};
let current = null, timer = null;
function setTop(title, opt = {}) {
  $("#title").textContent = title;
  if (opt.badge) $("#title").insertAdjacentHTML("beforeend", ` <span class="ver">${esc(opt.badge)}</span>`);
  document.title = title === "moa" ? "moa" : `${title} · moa`;
  const top = $("#top");
  $(".back", top)?.remove();
  if (opt.back) top.insertAdjacentHTML("afterbegin", `<a class="back" href="${opt.back}" aria-label="뒤로"><svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 18l-6-6 6-6"/></svg></a>`);
  $("#topRight").innerHTML = opt.right || "";
  // 2뎁스 메뉴는 엄지가 닿는 아래쪽(하단 탭 바로 위)에 둔다
  $("#subdock").innerHTML = opt.menu ? subnav(opt.menu, opt.sub) : "";
  document.body.classList.toggle("has-sub", !!opt.menu);
}
function parseHash() {
  const raw = (location.hash || "#home").slice(1);
  const [path, query] = raw.split("?");
  const [name, sub] = path.split("/");
  return { name, sub, params: new URLSearchParams(query || "") };
}
async function route() {
  const h = parseHash();
  if (!pages[h.name]) h.name = "home";
  h.sub = resolveSub(h);
  if (MENU[h.name]) lastSub[h.name] = (MENU[h.name].extra && MENU[h.name].extra[h.sub]) ? lastSub[h.name] || h.sub : h.sub;
  const page = pages[h.name];
  const tabName = h.name;
  $$(".tab").forEach((a) => {
    const on = a.dataset.tab === tabName;
    a.classList.toggle("on", on);
    if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  clearInterval(timer);
  closeSheet();
  $(".fab")?.remove();
  current = page;
  view.innerHTML = `<div class="empty">불러오는 중</div>`;
  try { await page.mount(h); }
  catch (e) {
    if (e instanceof NeedConnect && h.name !== "connect") { location.hash = "#connect"; return; }
    view.innerHTML = `<div class="notice warn">${esc(e.message)}</div><button class="btn wide" onclick="route()">다시 시도</button>`;
  }
  if (page.refresh) timer = setInterval(() => { if (current === page && !document.hidden && !sheet.open) page.refresh(h).catch(() => {}); }, page.every || 3000);
  window.scrollTo(0, 0);
}
window.addEventListener("hashchange", route);
// 가계부 월 이동(어느 2뎁스 화면이든 공통)
view.addEventListener("click", (e) => {
  const b = e.target.closest("[data-mon]");
  if (b && current === pages.ledger) { shiftMonth(ledgerState, Number(b.dataset.mon)); route(); }
});
function fab(label, onClick) {
  $(".fab")?.remove();
  const b = document.createElement("button");
  b.type = "button"; b.className = "fab"; b.innerHTML = `<span aria-hidden="true" style="font-size:22px;line-height:1">+</span>${esc(label)}`;
  b.onclick = onClick;
  document.body.appendChild(b);
}
const demoNotice = (src) => (src === "demo" ? `<div class="notice"><b>가상 시세</b>로 돌고 있어요. 실제 시장과 무관한 무작위 가격이고 시간이 빨리 흘러요.</div>` : "");

// =====================================================================
// 연결
// =====================================================================
pages.connect = {
  async mount(h) {
    setTop("서버 연결");
    if (h.params.get("server")) {
      store.set("server", h.params.get("server").trim());
      store.set("token", (h.params.get("token") || "").trim());
      history.replaceState(null, "", location.pathname + "#connect");
      try { await GET("/api/ping"); toast("연결했어요"); location.hash = "#home"; return; }
      catch (e) { toast(e.message, true); }
    }
    view.innerHTML = `
      <section class="card">
        <h2>노트북의 moa 서버에 연결해요</h2>
        <div class="small muted">가계부와 투자 데이터는 노트북에만 있어요. 이 화면은 그 서버에 접속하는 창이에요.</div>
        <label class="field">서버 주소<input id="cServer" inputmode="url" autocapitalize="off" autocomplete="off" spellcheck="false" placeholder="https://노트북이름.tailnet.ts.net" value="${esc(store.get("server"))}"></label>
        <label class="field">접속 토큰<input id="cToken" autocapitalize="off" autocomplete="off" spellcheck="false" value="${esc(store.get("token"))}">
          <span class="hint">노트북의 moa 실행 창 또는 노트북 화면의 설정 탭에 보여요</span></label>
        <button class="btn dark wide" id="cGo">연결하기</button>
      </section>
      <section class="card">
        <h2>처음 한 번 준비</h2>
        <div class="small muted" style="line-height:1.7">
          1. 노트북과 휴대폰에 Tailscale을 설치하고 같은 계정으로 로그인해요.<br>
          2. 노트북에서 moa를 켜고, 명령창에 <span class="code" style="padding:2px 6px;white-space:nowrap">tailscale serve --bg 8780</span> 를 한 번 실행해요.<br>
          3. 나오는 https 주소를 위 서버 주소에 넣어요.<br>
          노트북 화면의 설정 탭에서 휴대폰 연결 링크를 만들면 주소와 토큰이 한 번에 들어가요.</div>
      </section>`;
    $("#cGo").onclick = async () => {
      store.set("server", $("#cServer").value.trim().replace(/\/+$/, ""));
      store.set("token", $("#cToken").value.trim());
      await run(() => GET("/api/ping"), "연결했어요");
      location.hash = "#home";
    };
  },
};

// =====================================================================
// 홈
// =====================================================================
let catCache = null;
async function categories() { if (!catCache) catCache = (await GET(`/api/ledger?month=${thisMonth()}`)).categories; return catCache; }

pages.home = {
  async mount(h) {
    const ping = await GET("/api/ping").catch(() => ({}));
    setTop("홈", { menu: "home", sub: h.sub, badge: ping.version ? "v" + ping.version : "", right: `<span class="tiny">${korDate(localToday())}</span>` });
    if (h.sub === "activity") return this.activity();
    return this.summary();
  },
  async activity() {
    const items = await GET("/api/timeline");
    const icon = { ledger: "가계", order: "주문", warn: "확인", update: "업데" };
    const groups = {};
    items.forEach((x) => (groups[(x.ts || "").slice(0, 10)] = groups[(x.ts || "").slice(0, 10)] || []).push(x));
    view.innerHTML = Object.keys(groups).length ? Object.entries(groups).map(([day, xs]) => `
      <div class="sec-title">${day ? korDate(day) : "날짜 없음"}</div>
      <section class="card tight"><div class="list">${xs.map((x) => `<div class="item">
        <span class="ico ${x.kind === "warn" ? "warn" : x.kind === "order" ? "y" : ""}">${icon[x.kind] || ""}</span>
        <span class="main"><b>${esc(x.title)}</b><span class="sub">${esc((x.ts || "").slice(11, 16))} · ${esc(x.sub || "")}</span></span>
        <span class="end">${x.amount != null ? `<b class="${x.amount > 0 ? "inc" : ""}">${x.amount > 0 ? "+" : "-"}${won(Math.abs(x.amount))}</b>` : x.price ? `<span class="small ${x.side === "buy" ? "up" : "down"}">${won(x.price)}원</span>` : ""}</span></div>`).join("")}</div></section>`).join("")
      : `<section class="card"><div class="empty">아직 기록이 없어요. 가계부를 쓰거나 주문하면 여기에 모여요</div></section>`;
  },
  async summary() {
    const d = await GET("/api/home");
    const L = d.ledger, A = d.assets, S = d.scalp, D = d.dca;
    const m = Number(L.month.slice(5));
    const hero = L.budget > 0
      ? (L.remaining > 0 ? `${m}월은 ${L.days_left}일 남았고,<br>하루 <span class="hl">${won(L.per_day)}원</span>씩 쓸 수 있어요`
        : `${m}월 예산을 <span class="hl">${won(-L.remaining)}원</span> 넘게 썼어요`)
      : `${m}월에 <span class="hl">${won(L.expense)}원</span> 썼어요`;
    const noBudget = L.budget > 0 ? "" : `<div class="notice" style="display:flex;align-items:center;gap:10px;justify-content:space-between"><span>한 달 예산을 정하면 하루에 쓸 수 있는 돈을 알려 드려요</span><a class="btn sm dark" href="#ledger/budget" style="flex:0 0 auto">예산 정하기</a></div>`;
    const used = L.budget ? Math.min(100, (L.expense / L.budget) * 100) : 0;
    const near = L.categories.filter((c) => c.budget > 0).map((c) => ({ ...c, p: c.used / c.budget })).sort((a, b) => b.p - a.p).slice(0, 3);
    view.innerHTML = `
      ${d.feed_error ? `<div class="notice warn">${esc(d.feed_error)}</div>` : ""}
      <section class="card">
        <h1 class="hero">${hero}</h1>
        ${L.budget ? `<div class="bar thick ${L.remaining < 0 ? "over" : ""}" role="img" aria-label="예산 ${Math.round(used)}% 사용"><div style="width:${used}%"></div></div>
          <div class="kv"><div>예산<b>${won(L.budget)}</b></div><div>쓴 돈<b>${won(L.expense)}</b></div><div>남은 예산<b>${won(Math.max(0, L.remaining))}</b></div></div>` : ""}
        ${noBudget}
        ${dayBars(L)}
      </section>
      <div class="btns"><button class="btn dark" data-add="out">지출 기록</button><button class="btn" data-add="in">수입 기록</button></div>
      ${d.account && d.account.configured ? `<a class="card" href="#invest/account" style="text-decoration:none;color:inherit">
        <div class="card-head"><h2>내 키움 계좌</h2><span class="tiny live-dot ${d.account.ok ? "on" : ""}">${d.account.ok ? "실시간" : "연결 확인 필요"}</span></div>
        ${d.account.total != null ? `<div class="big">${won(d.account.total)}원</div>
          <div class="small muted"><b class="${tone(d.account.pnl)}">평가손익 ${signed(d.account.pnl)}원 (${pct(d.account.pnl_pct, 2)})</b> · 예수금 ${won(d.account.cash)}원</div>`
          : `<div class="small muted">${esc(d.account.error || "")}</div>`}</a>` : ""}
      <a class="card" href="#assets/overview" style="text-decoration:none;color:inherit">
        <div class="card-head"><h2>순자산</h2><span class="tiny">자세히</span></div>
        ${A.groups.length ? `<div class="big">${won(A.net)}원</div>
          ${A.change != null ? `<div class="small ${tone(A.change)}" style="font-weight:700">지난달보다 ${won(Math.abs(A.change))}원 ${A.change >= 0 ? "늘었어요" : "줄었어요"}</div>` : ""}
          ${A.history.length >= 2 ? lineChart([{ values: A.history.map((h) => h.net) }], { h: 72, label: "월별 순자산 추이" }) : ""}`
        : `<div class="small muted">계좌를 등록하면 순자산을 매달 기록해요</div>`}
      </a>
      <a class="card dark" href="#invest/scalp" style="text-decoration:none">
        <div class="card-head"><h2>오늘의 투자</h2><span class="tiny" style="color:#C5CCD6">${esc(S.mode_label || "")}</span></div>
        <div class="grid2">
          <div><div class="small muted">단타 실현손익</div><div class="mid">${signed(S.today.realized)}원</div><div class="tiny" style="color:#C5CCD6">${STATE[S.state]} · ${S.today.closed}건</div></div>
          <div><div class="small muted">적립 다음 매수</div>${D.schedule.length ? `<div class="mid">${korDate(D.schedule[0].date, false)}</div><div class="tiny" style="color:#C5CCD6">${won(D.schedule[0].amount)}원</div>` : `<div class="small muted">플랜 없음</div>`}</div>
        </div>
      </a>
      ${near.length ? `<section class="card"><div class="card-head"><h2>예산 많이 쓴 분류</h2><a href="#ledger/budget">예산</a></div>${near.map(budgetBar).join("")}</section>` : ""}
      <section class="card tight">
        <div class="card-head" style="padding-top:12px"><h2>최근 내역</h2><a href="#ledger/list">전체</a></div>
        <div class="list">${d.recent.length ? d.recent.map((t) => txItem(t, false)).join("") : `<div class="empty">아직 내역이 없어요</div>`}</div>
      </section>`;
    view.onclick = (e) => { const b = e.target.closest("[data-add]"); if (b) txSheet({ kind: b.dataset.add }, route); };
  },
};
function dayBars(L) {
  const max = Math.max(1, ...L.daily);
  return `<div class="daybars" role="img" aria-label="이번 달 일별 지출">` + L.daily.map((v, i) => {
    const day = i + 1, cls = day === L.today_idx ? "today" : day > L.today_idx ? "future" : "";
    const h = cls === "future" ? 3 : Math.max(3, Math.round((v / max) * 56));
    return `<div class="d ${cls}" title="${day}일 ${won(v)}원"><i style="height:${h}px"></i><em>${day === L.today_idx || ((day === 1 || day % 5 === 0) && Math.abs(day - L.today_idx) > 1) ? day : ""}</em></div>`;
  }).join("") + `</div>`;
}
function budgetBar(c) {
  const p = c.budget ? Math.min(100, (c.used / c.budget) * 100) : 0;
  return `<div style="display:flex;flex-direction:column;gap:6px">
    <div style="display:flex;justify-content:space-between;font-size:14px"><b>${esc(c.name)}</b><span class="muted">${won(c.used)} / ${won(c.budget)}</span></div>
    <div class="bar ${c.used > c.budget * 0.9 ? "over" : ""}"><div style="width:${p}%"></div></div></div>`;
}
function txItem(t, btn = true) {
  const tag = btn ? "button" : "div";
  return `<${tag} ${btn ? `type="button" data-edit="${t.id}"` : ""} class="item">
    <span class="ico ${t.transfer ? "xf" : t.kind === "in" ? "y" : ""}">${t.transfer ? "이체" : esc((t.category || "기타").slice(0, 2))}</span>
    <span class="main"><b>${esc(t.memo || t.category || "-")}</b><span class="sub">${btn ? "" : t.date.slice(5).replace("-", ".") + " · "}${t.transfer ? `내 계좌 ${t.kind === "in" ? "입금" : "출금"} · 합계 제외` : esc(t.category || "분류 없음")}${t.method && !t.transfer ? " · " + esc(t.method) : ""}</span></span>
    <span class="end"><b class="${t.transfer ? "xf-amt" : t.kind === "in" ? "inc" : ""}">${t.transfer ? "" : t.kind === "in" ? "+" : "-"}${won(t.amount)}</b></span></${tag}>`;
}

// ---------- 내역 입력 시트 ----------
async function txSheet(e, done) {
  const cats = await run(() => categories());
  let kind = e.kind || "out";
  let xfer = !!e.transfer;
  let cat = e.category_id || null;
  const methods = ["신용카드", "체크카드", "현금", "계좌이체", "자동이체", "간편결제"];
  let method = e.method || (kind === "in" ? "계좌이체" : "신용카드");
  const draw = () => {
    const list = cats.filter((c) => c.kind === kind);
    if (!list.some((c) => c.id === cat)) cat = list[0]?.id;
    openSheet(e.id ? "내역 고치기" : xfer ? "내 계좌 이체 기록" : kind === "out" ? "지출 기록" : "수입 기록", `
      ${seg("수입 또는 지출", [["out", "지출"], ["in", "수입"], ["xfer", "내 계좌 이체"]], xfer ? "xfer" : kind, "on-card")}
      <input class="amount-in" id="tAmt" inputmode="numeric" placeholder="0원" aria-label="금액" value="${e.amount ? won(e.amount) : ""}">
      ${xfer ? `<div class="small muted">내 계좌끼리 옮긴 돈은 기록만 남기고 수입·지출 합계, 예산, 분석에서 빼요.</div>` : ""}
      <label class="field">내용<input id="tMemo" placeholder="${xfer ? "예: 하나 → 키움" : kind === "out" ? "어디에 썼나요" : "어디서 들어왔나요"}" value="${esc(e.memo || "")}"></label>
      ${xfer ? "" : `<div class="field">분류<div class="chips" role="group" aria-label="분류">${list.map((c) => `<button type="button" class="chip" data-cat="${c.id}" aria-pressed="${c.id === cat}">${esc(c.name)}</button>`).join("")}</div></div>
      <div class="field">결제수단<div class="chips" role="group" aria-label="결제수단">${methods.map((x) => `<button type="button" class="chip" data-m="${x}" aria-pressed="${x === method}">${x}</button>`).join("")}</div></div>`}
      <label class="field">날짜<input type="date" id="tDate" value="${e.date || localToday()}"></label>
      <button class="btn dark wide" id="tSave">${e.id ? "고친 내용 저장" : "저장"}</button>
      ${e.id ? `<button class="btn soft wide" id="tDel">이 내역 지우기</button>` : ""}`, (box) => {
      moneyInput($("#tAmt", box));
      if (!e.id) setTimeout(() => $("#tAmt", box).focus(), 250);
      bindSeg(box, "수입 또는 지출", (v) => {
        e = { ...e, amount: num($("#tAmt").value), memo: $("#tMemo").value, date: $("#tDate").value };
        if (v === "xfer") { xfer = true; method = "계좌이체"; } else { xfer = false; kind = v; method = v === "in" ? "계좌이체" : "신용카드"; }
        draw();
      });
      box.onclick = async (ev) => {
        const c = ev.target.closest("[data-cat]"), m = ev.target.closest("[data-m]");
        if (c) { cat = Number(c.dataset.cat); $$("[data-cat]", box).forEach((x) => x.setAttribute("aria-pressed", String(x === c))); }
        if (m) { method = m.dataset.m; $$("[data-m]", box).forEach((x) => x.setAttribute("aria-pressed", String(x === m))); }
        if (ev.target.id === "tDel") {
          if (!confirmTap(ev.target, "한 번 더 누르면 지워요")) return;
          await run(() => api("DELETE", `/api/tx/${e.id}`), "지웠어요"); closeSheet(); done && done();
        }
        if (ev.target.id === "tSave") {
          const body = { kind, amount: num($("#tAmt").value), memo: $("#tMemo").value, category_id: xfer ? null : cat, method, date: $("#tDate").value, transfer: xfer };
          await run(() => (e.id ? api("PUT", `/api/tx/${e.id}`, body) : api("POST", "/api/tx", body)), e.id ? "고쳤어요" : "저장했어요");
          closeSheet(); done && done(body);
        }
      };
    });
  };
  draw();
}

// =====================================================================
// 가계부
// =====================================================================
const ledgerState = { month: thisMonth(), kind: "all" };
function monthNav(S, sum) {
  const [y, m] = S.month.split("-").map(Number);
  return `<section class="card">
    <div class="card-head">
      <button class="icon-btn" style="background:var(--bg)" data-mon="-1" aria-label="이전 달">‹</button>
      <h2 style="font-size:18px">${y}년 ${m}월</h2>
      <button class="icon-btn" style="background:var(--bg)" data-mon="1" aria-label="다음 달">›</button>
    </div>
    <div class="kv"><div>수입<b class="inc">+${won(sum.income)}</b></div><div>지출<b>-${won(sum.expense)}</b></div><div>남은 돈<b>${won(sum.net)}</b></div></div>
    ${sum.transfer_count ? `<div class="tiny">내 계좌끼리 옮긴 ${sum.transfer_count}건은 수입·지출에서 뺐어요</div>` : ""}
  </section>`;
}
function shiftMonth(S, by) {
  let [yy, mm] = S.month.split("-").map(Number);
  mm += by;
  if (mm < 1) { mm = 12; yy--; } if (mm > 12) { mm = 1; yy++; }
  S.month = `${yy}-${String(mm).padStart(2, "0")}`;
}
pages.ledger = {
  async mount(h) {
    const S = ledgerState;
    setTop("가계부", { menu: "ledger", sub: h.sub });
    if (h.params.get("budget")) { history.replaceState(null, "", "#ledger/budget"); h.sub = "budget"; lastSub.ledger = "budget"; setTop("가계부", { menu: "ledger", sub: "budget" }); }
    const kindQ = h.sub === "list" && S.kind !== "all" ? "&kind=" + S.kind : "";
    const d = await GET(`/api/ledger?month=${S.month}${kindQ}`);
    catCache = d.categories;
    this.d = d;
    const addFab = () => fab("기록", () => txSheet({ kind: S.kind === "in" ? "in" : "out", date: S.month === thisMonth() ? localToday() : S.month + "-01" }, (b) => { if (b && b.date.slice(0, 7) !== S.month) S.month = b.date.slice(0, 7); route(); }));
    if (h.sub === "auto") return this.auto();
    if (h.sub === "calendar") { this.calendar(d); addFab(); }
    else if (h.sub === "analysis") await this.analysis(d);
    else if (h.sub === "budget") this.budget(d);
    else { this.list(d); addFab(); }
  },
  list(d) {
    const S = ledgerState;
    const byDay = {};
    d.rows.forEach((t) => (byDay[t.date] = byDay[t.date] || []).push(t));
    view.innerHTML = `${monthNav(S, d.summary)}
      ${seg("내역 종류", [["all", "전체"], ["out", "지출"], ["in", "수입"], ["transfer", "이체"]], S.kind)}
      <section class="card tight">
        ${Object.keys(byDay).length ? Object.entries(byDay).map(([date, rows]) => {
          const out = rows.filter((r) => r.kind === "out" && !r.transfer).reduce((a, r) => a + r.amount, 0);
          return `<div class="day-head"><span>${korDate(date)}</span><span>${out ? "-" + won(out) : ""}</span></div><div class="list">${rows.map((t) => txItem(t)).join("")}</div>`;
        }).join("") : `<div class="empty">이 달에는 내역이 없어요</div>`}
      </section>`;
    bindSeg(view, "내역 종류", (v) => { S.kind = v; route(); });
    view.onclick = (e) => { const b = e.target.closest("[data-edit]"); if (b) txSheet({ ...d.rows.find((r) => r.id == b.dataset.edit) }, route); };
  },
  calendar(d) {
    const S = ledgerState;
    const [y, m] = S.month.split("-").map(Number);
    const first = new Date(y, m - 1, 1).getDay();
    const ndays = new Date(y, m, 0).getDate();
    const inc = {}, out = {};
    d.rows.filter((t) => !t.transfer).forEach((t) => { const k = Number(t.date.slice(8)); (t.kind === "in" ? inc : out)[k] = ((t.kind === "in" ? inc : out)[k] || 0) + t.amount; });
    const today = localToday();
    let cells = "";
    for (let i = 0; i < first; i++) cells += `<div class="cal-cell empty-cell"></div>`;
    for (let day = 1; day <= ndays; day++) {
      const iso = `${S.month}-${String(day).padStart(2, "0")}`;
      const wd = (first + day - 1) % 7;
      cells += `<button type="button" class="cal-cell ${iso === today ? "today" : ""} ${wd === 0 ? "sun" : wd === 6 ? "sat" : ""}" data-day="${iso}" aria-label="${korDate(iso)} 지출 ${won(out[day] || 0)}원">
        <span class="cal-d">${day}</span>${out[day] ? `<span class="cal-out">-${shortWon(out[day])}</span>` : ""}${inc[day] ? `<span class="cal-in">+${shortWon(inc[day])}</span>` : ""}</button>`;
    }
    const spentDays = Object.keys(out).length;
    view.innerHTML = `${monthNav(S, d.summary)}
      <section class="card cal">
        <div class="cal-grid cal-wd">${[..."일월화수목금토"].map((w, i) => `<span class="${i === 0 ? "sun" : i === 6 ? "sat" : ""}">${w}</span>`).join("")}</div>
        <div class="cal-grid">${cells}</div>
        <div class="tiny">지출한 날 ${spentDays}일 · 지출 없는 날 ${Math.max(0, (S.month === thisMonth() ? Number(today.slice(8)) : ndays) - spentDays)}일 · 날짜를 누르면 그날 내역이 보여요</div>
      </section>`;
    view.onclick = (e) => {
      const c = e.target.closest("[data-day]");
      if (!c) return;
      const iso = c.dataset.day;
      const rows = d.rows.filter((t) => t.date === iso);
      openSheet(korDate(iso), `<div class="list">${rows.length ? rows.map((t) => txItem(t)).join("") : `<div class="empty">이날은 내역이 없어요</div>`}</div>
        <button class="btn dark wide" id="dayAdd">이날 기록하기</button>`, (box) => {
        $("#dayAdd", box).onclick = () => txSheet({ kind: "out", date: iso }, route);
        box.addEventListener("click", (ev) => { const b = ev.target.closest("[data-edit]"); if (b) txSheet({ ...rows.find((r) => r.id == b.dataset.edit) }, route); });
      });
    };
  },
  async analysis(d) {
    const S = ledgerState;
    const sum = d.summary;
    const trend = await GET("/api/ledger/trend?months=6");
    const cats = sum.categories.filter((c) => c.used > 0).sort((a, b) => b.used - a.used);
    const maxc = Math.max(1, ...cats.map((c) => c.used));
    const outs = d.rows.filter((t) => t.kind === "out" && !t.transfer);
    const byMethod = {};
    outs.forEach((t) => (byMethod[t.method || "기타"] = (byMethod[t.method || "기타"] || 0) + t.amount));
    const methods = Object.entries(byMethod).sort((a, b) => b[1] - a[1]);
    const top5 = outs.slice().sort((a, b) => b.amount - a.amount).slice(0, 5);
    const tmax = Math.max(1, ...trend.flatMap((t) => [t.income, t.expense]));
    view.innerHTML = `${monthNav(S, sum)}
      <section class="card"><h2>최근 6개월</h2>
        <div class="trend" role="img" aria-label="최근 6개월 수입과 지출">${trend.map((t) => `<div class="tcol">
          <div class="tbars"><i class="ti" style="height:${(t.income / tmax) * 100}%"></i><i class="to" style="height:${(t.expense / tmax) * 100}%"></i></div>
          <span>${Number(t.month.slice(5))}월</span></div>`).join("")}</div>
        <div class="legend tiny"><span><i class="sw" style="background:#0B7A55"></i>수입</span><span><i class="sw" style="background:#172033"></i>지출</span></div>
      </section>
      <section class="card"><h2>분류별 지출</h2>
        ${cats.length ? cats.map((c) => `<div style="display:flex;flex-direction:column;gap:6px">
          <div style="display:flex;justify-content:space-between;font-size:14px"><b>${esc(c.name)}</b><span class="muted">${won(c.used)} · ${Math.round((c.used / Math.max(1, sum.expense)) * 100)}%</span></div>
          <div class="bar"><div style="width:${(c.used / maxc) * 100}%"></div></div></div>`).join("") : `<div class="tiny">이 달 지출이 없어요</div>`}
      </section>
      <section class="card"><h2>결제수단별</h2>
        ${methods.length ? methods.map(([k, v]) => `<div style="display:flex;justify-content:space-between;font-size:14px"><span>${esc(k)}</span><b>${won(v)}</b></div>`).join("") : `<div class="tiny">이 달 지출이 없어요</div>`}
      </section>
      <section class="card tight"><div class="card-head" style="padding-top:12px"><h2>큰 지출 TOP 5</h2></div>
        <div class="list">${top5.length ? top5.map((t) => txItem(t, false)).join("") : `<div class="empty">이 달 지출이 없어요</div>`}</div>
      </section>`;
    view.onclick = null;
  },
  async auto() {
    const d = await GET("/api/inbox");
    const cats = (catCache || []).filter((c) => c.kind === "out");
    const label = { spend: "카드 결제", cancel: "결제 취소", income: "입금", outflow: "출금" };
    const st = { added: ["기록됨", "g"], pending: ["확인 필요", "warn"], ignored: ["무시함", ""] };
    const item = (x) => `<button type="button" class="item" data-inbox="${x.id}">
      <span class="ico ${x.status === "pending" ? "warn" : x.kind === "card" ? "" : "y"}">${esc((x.issuer || (x.kind === "card" ? "카드" : "은행")).slice(0, 2))}</span>
      <span class="main"><b>${esc(x.merchant || label[x.action])}</b><span class="sub">${x.transfer ? "내 계좌 이체" : esc(label[x.action])} · ${esc(x.date.slice(5).replace("-", "."))} ${esc(x.time || "")}${x.note ? " · " + esc(x.note) : ""}</span></span>
      <span class="end"><b class="${x.transfer ? "xf-amt" : x.action === "income" || x.action === "cancel" ? "inc" : ""}">${x.transfer ? "" : x.action === "income" || x.action === "cancel" ? "+" : "-"}${won(x.amount)}</b><span class="tag ${x.transfer ? "" : st[x.status][1]}">${x.transfer ? "이체" : st[x.status][0]}</span></span></button>`;
    const pend = d.items.filter((x) => x.status === "pending"), rest = d.items.filter((x) => x.status !== "pending");
    view.innerHTML = `
      <section class="card">
        <h2>문자·카톡 자동 기록</h2>
        <div class="small muted">카드 승인·취소, 은행 입출금 알림을 읽어 가계부에 넣고, 잔액이 오면 자산 계좌도 맞춰요. 금융 알림이 아닌 메시지는 저장하지 않아요.</div>
        <div class="kv"><div>확인 필요<b class="${d.pending ? "up" : ""}">${d.pending}건</b></div><div>자동 기록<b>${d.added}건</b></div><div>무시<b>${d.ignored}건</b></div></div>
      </section>
      ${pend.length ? `<div class="sec-title">확인 필요 · 눌러서 넣거나 무시해요</div><section class="card tight"><div class="list">${pend.map(item).join("")}</div></section>` : ""}
      <div class="sec-title">최근 처리</div>
      <section class="card tight"><div class="list">${rest.length ? rest.slice(0, 30).map(item).join("") : `<div class="empty">아직 받은 알림이 없어요. 아래에서 단축어를 설정하거나 지난 알림을 가져와 보세요</div>`}</div></section>
      <section class="card"><h2>바로 넣을 알림</h2>
        ${[["card", "카드 승인·취소", "결제하자마자 지출로 기록"], ["income", "입금", "월급·이체 받은 돈. 내 계좌끼리 옮긴 돈도 있어 기본은 확인 후 기록"], ["outflow", "출금", "카드값 자동이체와 겹칠 수 있어 기본은 확인 후 기록"]].map(([k, t, desc]) => `
          <div class="card-head" style="min-height:52px"><div><b style="font-size:15px">${t}</b><div class="tiny">${desc}</div></div>
          <button class="switch" role="switch" data-auto="${k}" aria-checked="${!!d.auto[k]}" aria-label="${t} 바로 기록"><span></span></button></div>`).join("")}
      </section>
      <section class="card"><h2>내 계좌끼리 옮긴 돈</h2>
        <div class="small muted">내 계좌 사이에서 오간 돈은 기록만 남기고 수입·지출에서 빼요. 이렇게 알아봐요.</div>
        <ul class="xf-rules">
          <li>받는 분·보낸 분이 <b>내 이름</b>이거나 '본인'일 때</li>
          <li>내 증권 계좌(키움 등)로 보낸 돈일 때</li>
          <li>같은 금액이 내 다른 계좌에서 30분 안에 나가고 들어왔을 때</li></ul>
        <label class="field">입출금 알림에 찍히는 내 이름<input id="myNames" placeholder="예: 홍길동, 홍길동(급여)" value="${esc((d.my_names || []).join(", "))}"></label>
        <button class="btn wide" id="myNamesSave">저장하고 지난 내역 다시 살피기</button>
      </section>
      <section class="card"><h2>지난 알림 가져오기</h2>
        <div class="small muted">카카오톡 카드사·은행 알림 대화방의 <b>대화 내용 내보내기</b> 파일이나, 복사한 문자들을 넣으면 한꺼번에 기록해요. 이미 들어온 알림은 다시 넣지 않아요.</div>
        <label class="field">카톡 내보내기 파일(.txt)<input type="file" id="ibFile" accept=".txt,text/plain" style="padding:12px"></label>
        <label class="field">또는 문자 붙여넣기(메시지 사이는 빈 줄)<textarea id="ibText" rows="5" placeholder="[Web발신]&#10;신한카드(1234)승인&#10;12,000원 일시불&#10;11/17 12:30 스타벅스"></textarea></label>
        <button class="btn dark wide" id="ibImport">분석해서 가져오기</button>
      </section>
      <section class="card"><div class="card-head"><h2>문자 자동 보내기 설정</h2><a href="#settings/accounts">계좌 연결에서 설정</a></div>
        <div class="tiny">아이폰 단축어 설정과 은행 계좌 연결은 설정 → 계좌 연결에 모아 두었어요.</div></section>`;
    view.onclick = async (e) => {
      const a = e.target.closest("[data-auto]"), it = e.target.closest("[data-inbox]");
      if (a) {
        const on = a.getAttribute("aria-checked") !== "true";
        await run(() => api("PUT", "/api/inbox/auto", { [a.dataset.auto]: on }), on ? "바로 기록하게 했어요" : "확인 후 기록하게 했어요");
        return this.auto();
      }
      if (e.target.id === "myNamesSave") {
        const names = $("#myNames").value.split(/[,，]/).map((x) => x.trim()).filter(Boolean);
        const r = await run(() => api("PUT", "/api/inbox/names", { names }));
        toast(r.retagged ? `지난 내역 ${r.retagged}건을 내 계좌 이체로 바꿨어요` : "저장했어요. 새로 바꿀 지난 내역은 없어요");
        return this.auto();
      }
      if (e.target.id === "ibImport") {
        const f = $("#ibFile").files[0];
        let r;
        if (f) { const fd = new FormData(); fd.append("file", f); r = await run(() => api("POST", "/api/inbox/import", fd, true)); }
        else r = await run(() => api("POST", "/api/inbox/import", { text: $("#ibText").value }));
        toast(`메시지 ${r.total}개 중 금융 알림 ${r.financial}개: 기록 ${r.added} · 확인 필요 ${r.pending} · 이미 있음 ${r.duplicate}`);
        return this.auto();
      }
      if (it) {
        const x = d.items.find((y) => y.id == it.dataset.inbox);
        let cat = x.category || (x.action === "income" ? "" : "기타지출");
        openSheet(label[x.action], `
          <div class="big" style="font-size:26px">${x.action === "income" || x.action === "cancel" ? "+" : "-"}${won(x.amount)}원</div>
          <div class="small muted">${esc(x.merchant || "")} · ${esc(x.date)} ${esc(x.time || "")} · ${esc(x.issuer || "")}</div>
          <div class="code" style="white-space:pre-wrap;user-select:text">${esc(x.raw)}</div>
          ${x.action === "spend" || x.action === "outflow" ? `<div class="field">분류<div class="chips">${cats.map((c) => `<button type="button" class="chip" data-c="${esc(c.name)}" aria-pressed="${c.name === cat}">${esc(c.name)}</button>`).join("")}</div></div>` : ""}
          ${x.status === "added" ? `<div class="notice">이미 가계부에 들어갔어요. 고치려면 가계부 내역에서 눌러 주세요.</div>` : `<div class="btns"><button class="btn soft" id="ibIgnore">무시</button><button class="btn dark" id="ibApply">가계부에 넣기</button></div>
            ${x.kind === "bank" ? `<button class="btn wide" id="ibXfer">내 계좌끼리 옮긴 돈 (수입·지출에서 빼기)</button>` : ""}`}`, (box) => {
          box.onclick = async (ev) => {
            const c = ev.target.closest("[data-c]");
            if (c) { cat = c.dataset.c; $$("[data-c]", box).forEach((z) => z.setAttribute("aria-pressed", String(z === c))); }
            if (ev.target.id === "ibApply") { await run(() => api("POST", `/api/inbox/${x.id}/apply`, { category: cat }), "가계부에 넣었어요"); closeSheet(); this.auto(); }
            if (ev.target.id === "ibXfer") { await run(() => api("POST", `/api/inbox/${x.id}/apply`, { transfer: true }), "내 계좌 이체로 기록했어요"); closeSheet(); this.auto(); }
            if (ev.target.id === "ibIgnore") { await run(() => api("POST", `/api/inbox/${x.id}/ignore`)); closeSheet(); this.auto(); }
          };
        });
      }
    };
  },
  budget(d) {
    const sum = d.summary;
    const used = Object.fromEntries(sum.categories.map((c) => [c.id, c.used]));
    view.innerHTML = `${monthNav(ledgerState, sum)}
      <section class="card">
        <div class="card-head"><h2>한 달 예산</h2><b>${won(sum.budget)}원</b></div>
        <div class="small muted">분류마다 한 달에 쓸 금액을 넣어요. 매달 같은 예산을 쓰고, 0이면 예산 없이 기록만 해요.</div>
        ${d.categories.filter((c) => c.kind === "out").map((c) => {
          const u = used[c.id] || 0, p = c.budget ? Math.min(100, (u / c.budget) * 100) : 0;
          return `<div style="display:flex;flex-direction:column;gap:6px;padding:6px 0;border-top:1px solid var(--bg)">
          <label class="field" style="flex-direction:row;align-items:center;justify-content:space-between;gap:12px">
            <span style="font-size:15px;color:var(--ink)">${esc(c.name)}<br><span class="tiny">이번 달 ${won(u)}원 썼어요</span></span>
            <input class="r" style="max-width:150px" inputmode="numeric" data-budget="${c.id}" data-name="${esc(c.name)}" value="${won(c.budget)}"></label>
          ${c.budget ? `<div class="bar ${u > c.budget * 0.9 ? "over" : ""}"><div style="width:${p}%"></div></div>` : ""}</div>`;
        }).join("")}
        <button class="btn dark wide" id="bSave">예산 저장</button>
      </section>
      <section class="card"><h2>분류 추가</h2>
        <div class="fields"><label class="field">새 분류<input id="newCat" placeholder="예: 반려동물"></label>
          <label class="field">종류<select id="newKind"><option value="out">지출</option><option value="in">수입</option></select></label></div>
        <button class="btn wide" id="cAdd">분류 추가</button>
      </section>`;
    $$("[data-budget]").forEach(moneyInput);
    $("#bSave").onclick = async () => {
      await run(async () => { for (const i of $$("[data-budget]")) await api("POST", "/api/categories", { id: i.dataset.budget, name: i.dataset.name, budget: num(i.value) }); }, "예산을 저장했어요");
      catCache = null; route();
    };
    $("#cAdd").onclick = async () => {
      await run(() => api("POST", "/api/categories", { name: $("#newCat").value, kind: $("#newKind").value, budget: 0 }), "분류를 추가했어요");
      catCache = null; route();
    };
    view.onclick = null;
  },
};

// =====================================================================
// 자산
// =====================================================================
const MIX = ["#1B2430", "#5B6676", "#AEB7C4", "#FFD84D", "#7A8699", "#DDE2E8"];
pages.assets = {
  async mount(h) {
    setTop("자산", { menu: "assets", sub: h.sub });
    const d = await GET("/api/assets");
    const total = d.mix.reduce((s, x) => s + x.total, 0);
    const summaryCard = `
      <section class="card">
        <div class="small muted" style="font-weight:700">순자산</div>
        <div class="big">${won(d.net)}원</div>
        ${d.change != null ? `<div class="small ${tone(d.change)}" style="font-weight:700">지난달보다 ${signed(d.change)}원</div>` : ""}
        <div class="kv" style="grid-template-columns:1fr 1fr"><div>가진 것<b>${won(d.assets)}</b></div><div>갚을 것<b>${won(d.debts)}</b></div></div>
        ${total ? `<div class="mix" role="img" aria-label="자산 구성 ${d.mix.map((x) => `${x.label} ${((x.total / total) * 100).toFixed(1)}%`).join(", ")}">${d.mix.map((x, i) => `<div style="flex:${x.total} 1 0;background:${MIX[i % 6]}"></div>`).join("")}</div>
          <div class="legend">${d.mix.map((x, i) => `<span><i class="sw" style="background:${MIX[i % 6]}"></i>${esc(x.label)} <b>${((x.total / total) * 100).toFixed(0)}%</b></span>`).join("")}</div>` : ""}
        ${d.history.length >= 2 ? lineChart([{ values: d.history.map((x) => x.net) }], { h: 80, label: "월별 순자산" }) : ""}
      </section>`;
    const goalCard = `
      <section class="card">
        <div class="card-head"><h2>목표까지</h2><button class="link" data-act="goal">${d.goal ? "목표 바꾸기" : "목표 정하기"}</button></div>
        ${d.goal > 0 ? `<div class="big" style="font-size:26px">${Math.max(0, Math.round((d.net / d.goal) * 100))}%</div>
          <div class="small muted">순자산 <b style="color:var(--ink)">${won(d.goal)}원</b>까지 ${d.net >= d.goal ? "<b style='color:var(--ink)'>도달했어요</b>" : `<b style="color:var(--ink)">${won(d.goal - d.net)}원</b> 남았어요`}</div>
          <div class="bar thick"><div style="width:${Math.max(0, Math.min(100, (d.net / d.goal) * 100))}%;background:var(--hl);box-shadow:inset -2px 0 0 var(--ink)"></div></div>`
        : `<div class="small muted">목표 순자산을 정하면 얼마나 왔는지 보여드려요</div>`}
      </section>`;
    const groupsHtml = `
      ${d.groups.map((g) => `<div class="sec-title">${esc(g.label)} · ${g.type === "debt" ? "-" : ""}${won(g.total)}원</div>
        <section class="card tight"><div class="list">${g.items.map((a) => `<button type="button" class="item" data-edit="${a.id}">
          <span class="ico ${a.linked ? "y" : ""}">${esc(g.label.slice(0, 2))}</span>
          <span class="main"><b>${esc(a.name)}</b><span class="sub">${a.linked ? ({ scalp: "단타 계좌와 연결", dca: "장기 계좌와 연결", kiwoom: "내 키움 계좌와 연결" }[a.linked]) : esc(a.note || "직접 입력")}${a.sync_error ? " · 연결 실패" : ""}</span></span>
          <span class="end"><b>${g.type === "debt" ? "-" : ""}${won(a.balance)}</b></span></button>`).join("")}</div></section>`).join("")
        || `<section class="card"><div class="empty">통장, 예적금, 연금, 대출을 등록하면<br>순자산을 계산해요</div></section>`}`;
    if (h.sub === "accounts") { view.innerHTML = groupsHtml; fab("계좌", () => this.sheet(d, null)); }
    else if (h.sub === "goal") view.innerHTML = goalCard + `<section class="card"><h2>지금 순자산</h2><div class="big" style="font-size:26px">${won(d.net)}원</div>
        ${d.history.length >= 2 ? lineChart([{ values: d.history.map((x) => x.net) }], { h: 90, label: "월별 순자산" }) + `<div class="axis"><span>${esc(d.history[0].month)}</span><span>${esc(d.history[d.history.length - 1].month)}</span></div>` : `<div class="tiny">매달 순자산을 기록해 추이를 그려요</div>`}</section>`;
    else view.innerHTML = summaryCard + `<div class="sec-title">종류별</div><section class="card tight"><div class="list">${d.groups.map((g) => `<a class="item" href="#assets/accounts" style="text-decoration:none">
        <span class="ico">${esc(g.label.slice(0, 2))}</span><span class="main"><b>${esc(g.label)}</b><span class="sub">${g.items.length}개 계좌</span></span>
        <span class="end"><b>${g.type === "debt" ? "-" : ""}${won(g.total)}</b></span></a>`).join("") || `<div class="empty">계좌 탭에서 통장·예적금·대출을 등록해 보세요</div>`}</div></section>` + goalCard;
    view.onclick = (e) => {
      const b = e.target.closest("[data-edit],[data-act]");
      if (!b) return;
      if (b.dataset.edit) this.sheet(d, d.groups.flatMap((g) => g.items).find((a) => a.id == b.dataset.edit));
      if (b.dataset.act === "goal") openSheet("목표 순자산", `<input class="amount-in" id="gAmt" inputmode="numeric" placeholder="0원" value="${d.goal ? won(d.goal) : ""}"><button class="btn dark wide" id="gSave">저장</button>`, (box) => {
        moneyInput($("#gAmt", box));
        $("#gSave", box).onclick = async () => { await run(() => api("PUT", "/api/goal", { goal: num($("#gAmt").value) }), "목표를 저장했어요"); closeSheet(); route(); };
      });
    };
  },
  sheet(d, e) {
    openSheet(e ? "계좌 고치기" : "계좌 추가", `
      <label class="field">이름<input id="aName" value="${esc(e?.name || "")}" placeholder="예: 생활비 통장"></label>
      <label class="field">종류<select id="aType">${Object.entries(d.types).map(([k, v]) => `<option value="${k}" ${e?.type === k ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></label>
      <label class="field">매매 계좌와 연결<select id="aLink"><option value="">연결 안 함(잔액 직접 입력)</option>
        <option value="scalp" ${e?.linked === "scalp" ? "selected" : ""}>단타 계좌 평가금 자동 반영</option>
        <option value="dca" ${e?.linked === "dca" ? "selected" : ""}>장기투자 계좌 평가금 자동 반영</option>
        <option value="kiwoom" ${e?.linked === "kiwoom" ? "selected" : ""}>내 키움 계좌 총자산 자동 반영</option></select></label>
      <label class="field" id="aBalWrap">잔액<input id="aBal" class="r" inputmode="numeric" value="${e ? won(e.balance) : ""}" placeholder="0원"></label>
      <label class="field">메모<input id="aNote" value="${esc(e?.note || "")}" placeholder="예: 2027년 3월 만기"></label>
      <button class="btn dark wide" id="aSave">${e ? "고친 내용 저장" : "저장"}</button>
      ${e ? `<button class="btn soft wide" id="aDel">이 계좌 지우기</button>` : ""}`, (box) => {
      moneyInput($("#aBal", box));
      const sync = () => ($("#aBalWrap").hidden = !!$("#aLink").value);
      $("#aLink").onchange = sync; sync();
      box.onclick = async (ev) => {
        if (ev.target.id === "aDel") {
          if (!confirmTap(ev.target, "한 번 더 누르면 지워요")) return;
          await run(() => api("DELETE", `/api/accounts/${e.id}`), "지웠어요"); closeSheet(); route();
        }
        if (ev.target.id === "aSave") {
          await run(() => api("POST", "/api/accounts", { id: e?.id, name: $("#aName").value, type: $("#aType").value, balance: num($("#aBal").value), note: $("#aNote").value, linked: $("#aLink").value }), "저장했어요");
          closeSheet(); route();
        }
      };
    });
  },
};

// =====================================================================
// 투자: 매매 / 단타 / 적립 (+ 백테스트, 매매 규칙)
// =====================================================================
const investState = { tab: "market" };
pages.invest = {
  every: 2500,
  async mount(h) {
    const sub = h.sub || investState.tab;
    if (sub === "backtest") return this.backtest();
    if (sub === "rules") return this.rules();
    if (sub === "sweep") return this.sweep();
    if (sub === "stock") return mkt.detail(h);
    investState.tab = ["market", "account", "trade", "scalp", "dca"].includes(sub) ? sub : "market";
    setTop("투자", { menu: "invest", sub: investState.tab });
    view.innerHTML = `<div id="iv" style="display:flex;flex-direction:column;gap:12px"></div>`;
    await this.fill();
  },
  async refresh(h) {
    if (["backtest", "rules"].includes(h.sub)) return;
    if (h.sub === "stock") return mkt.refreshDetail();
    if (h.sub === "sweep") return this.sweepPoll();
    if (investState.tab !== "dca") await this.fill();
  },
  async fill() {
    const box = $("#iv");
    if (!box) return;
    if (investState.tab === "market") return mkt.board(box);
    if (investState.tab === "account") return this.account(box);
    if (investState.tab === "trade") return this.trade(box);
    if (investState.tab === "scalp") return this.scalp(box);
    return this.dca(box);
  },

  // ---------- 내 계좌(조회 전용) ----------
  async account(box) {
    if (!box.children.length) { const c = kwCache.load(); if (c) await this.accountRender(box, { ...c, ok: true }); }
    const d = await kwFetch();
    if (investState.tab !== "account") return;
    return this.accountRender(box, d);
  },
  async accountRender(box, d) {
    $(".fab")?.remove();
    if (!d.configured) {
      box.innerHTML = `<section class="card">
        <h2>키움 계좌를 연결하면 보유액이 여기 보여요</h2>
        <div class="small muted" style="line-height:1.7">조회 전용 연결이라 이 키로는 주문이 나가지 않아요.<br>
          1. 키움 REST API 홈페이지에서 앱키·시크릿키를 받아요(노트북 IP 등록 필요)<br>
          2. 설정 → 내 계좌 연결에 넣고 연결 확인을 눌러요</div>
        <a class="btn dark" href="#settings/accounts">설정으로 가기</a></section>`;
      return;
    }
    const err = d.error ? `<div class="notice warn">${esc(d.error)}${/8010/.test(d.error) ? "<br>노트북 인터넷 주소(IP)가 바뀐 것 같아요. 키움 REST API 홈페이지 → 계좌 App Key 관리에서 현재 IP를 추가해 주세요." : ""}</div>` : "";
    if (d.total == null) { box.innerHTML = err || `<div class="empty">불러오는 중</div>`; return; }
    const H = d.holdings || [];
    const mixTotal = H.reduce((s2, h) => s2 + h.value, 0) + (d.cash || 0);
    const colors = ["#172033", "#465269", "#7E8AA3", "#AFB8C9", "#D3D9E4"];
    const live = d.ok !== false;
    box.innerHTML = `
      ${err}
      <section class="card">
        <div class="card-head"><span class="tiny">키움 ${esc(d.server)} 계좌 · 조회 전용</span>
          <span class="tiny live-dot ${live ? "on" : ""}">${live ? "실시간" : "연결 끊김"} · ${esc((d.updated_at || "").slice(11, 19))}</span></div>
        <div><div class="small muted" style="font-weight:700">총자산(추정예탁자산)</div><div class="big">${won(d.total)}원</div></div>
        <div class="small ${tone(d.pnl)}" style="font-weight:700">평가손익 ${signed(d.pnl)}원 (${pct(d.pnl_pct, 2)})</div>
        <div class="kv"><div>주식 평가<b>${won(d.eval)}</b></div><div>예수금<b>${won(d.cash)}</b></div><div>주문 가능<b>${won(d.orderable)}</b></div></div>
        ${usLine(d)}
        ${mixTotal ? `<div class="mix" role="img" aria-label="보유 비중">${H.slice(0, 5).map((h, i) => `<div style="flex:${h.value} 1 0;background:${colors[i]}"></div>`).join("")}${H.length > 5 ? `<div style="flex:${H.slice(5).reduce((a, h) => a + h.value, 0)} 1 0;background:#E3E7F0"></div>` : ""}<div style="flex:${d.cash || 0} 1 0;background:var(--hl)"></div></div>
          <div class="legend tiny">${H.slice(0, 5).map((h, i) => `<span><i class="sw" style="background:${colors[i]}"></i>${esc(h.name)} ${Math.round((h.value / mixTotal) * 100)}%</span>`).join("")}<span><i class="sw" style="background:var(--hl)"></i>예수금 ${Math.round(((d.cash || 0) / mixTotal) * 100)}%</span></div>` : ""}
      </section>
      <div class="sec-title">보유 종목 ${H.length}개</div>
      <section class="card tight"><div class="list">${H.length ? H.map((h) => `<div class="item">
        <span class="ico">${esc(h.name.slice(0, 2))}</span>
        <span class="main"><b>${esc(h.name)} <span class="tiny">${esc(h.code)}</span></b>
          <span class="sub">${h.market === "us" ? "미국 · " : ""}${won(h.qty)}주 · 평균 ${h.market === "us" ? usd(h.avg_price) : won(h.avg_price)} · 현재 ${h.market === "us" ? usd(h.price) : won(h.price)}</span></span>
        <span class="end"><b>${won(h.value)}</b><span class="small ${tone(h.pnl)}" style="font-weight:700">${signed(h.pnl)} (${pct(h.pnl_pct, 2)})</span></span></div>`).join("")
        : `<div class="empty">보유 종목이 없어요</div>`}</div></section>
      <div class="tiny" style="padding:0 4px">몇 초마다 키움에서 새로 받아요. 자산 탭에서 이 계좌를 연결하면 순자산에도 자동으로 들어가요.</div>`;
  },

  // ---------- 매매 ----------
  async trade(box) {
    if (!box.children.length) { const c = memo.get("stocks"); if (c) this.tradeRender(box, c); }
    const d = await GET("/api/stocks");
    memo.set("stocks", d);
    if (investState.tab !== "trade") return;
    this.tradeRender(box, d);
  },
  tradeRender(box, d) {
    const rows = d.accounts.flatMap((a) => a.holdings.map((x) => ({ ...x, eng: a.engine })).concat((a.us_holdings || []).map((x) => ({ ...x, eng: a.engine }))));
    box.innerHTML = `
      ${demoNotice(d.data_source)}
      ${d.accounts.filter((a) => (a.error || a.us_error) && !a.same_as).map((a) => `<div class="notice warn">${esc(a.label)}: ${esc(a.error || a.us_error)}</div>`).join("")}
      <div class="grid2">${d.accounts.map((a) => {
        if (a.same_as) return `<div class="tile"><div class="lbl">${esc(a.label)}</div><div class="val" style="font-size:15px">단타 계좌와 같은 계좌</div>
          <div class="tiny">같은 키움 앱키라 같은 계좌예요. 잔고는 왼쪽에 함께 보여요.</div></div>`;
        const H = a.holdings.concat(a.us_holdings || []);
        const val = H.reduce((s, x) => s + x.value, 0), pnl = H.reduce((s, x) => s + x.pnl, 0);
        return `<div class="tile"><div class="lbl">${esc(a.label)}</div><div class="val">${won(val + (a.cash || 0))}</div>
          <div class="tiny">현금 ${won(a.cash)}${a.us_cash && a.us_cash.usd_orderable ? ` · ${usd(a.us_cash.usd_orderable)}` : ""}</div><div class="tiny ${tone(pnl)}" style="font-weight:700">평가손익 ${signed(pnl)}</div></div>`;
      }).join("")}</div>
      <div class="tiny" style="padding:0 4px">${esc(d.now.replace("T", " "))} · ${d.market_open ? "장 운영 중" : "장 마감"} · ${esc(d.accounts.map((a) => a.mode_label || "준비 안 됨").join(" / "))}</div>
      <div class="sec-title">보유 종목</div>
      <section class="card tight"><div class="list">${rows.length ? rows.map((x) => `<button type="button" class="item" data-sell="${x.eng}|${esc(x.code)}|${x.sellable ?? x.qty}">
        <span class="ico ${x.market === "us" ? "" : x.eng === "scalp" ? "y" : ""}">${x.market === "us" ? "미국" : x.eng === "scalp" ? "단타" : "장기"}</span>
        <span class="main"><b>${esc(x.name)}</b><span class="sub">${won(x.qty)}주 · 평균 ${x.market === "us" ? usd(x.avg_price) : won(x.avg_price)} · 현재 ${x.market === "us" ? usd(x.price) : won(x.price)}</span></span>
        <span class="end"><b>${won(x.value)}</b><span class="small ${tone(x.pnl)}" style="font-weight:700">${signed(x.pnl)} (${pct(x.pnl_pct, 1)})</span></span></button>`).join("")
        : `<div class="empty">보유 종목이 없어요</div>`}</div></section>
      <div id="openBox">${this.openHtml(this.openMemo)}</div>
      <div class="sec-title">최근 주문</div>
      <section class="card tight"><div class="list">${d.orders.length ? d.orders.slice(0, 10).map((o) => {
        const st = { cancel: ["취소", ""], cancelled: ["취소됨", ""], accepted: ["접수", "y"], filled: ["체결", "g"] }[o.status] || [o.status, ""];
        return `<div class="item">
        <span class="ico" style="color:${o.status === "cancel" ? "var(--ink3)" : o.side === "buy" ? "var(--up)" : "var(--down)"}">${o.status === "cancel" ? "취소" : o.side === "buy" ? "매수" : "매도"}</span>
        <span class="main"><b>${esc(o.name || o.code)}</b><span class="sub">${o.ts.slice(5, 16).replace("T", " ")} · ${esc(o.msg)}</span></span>
        <span class="end"><b>${won(o.qty)}주</b><span class="tiny">${o.fill_price ? (isUsCode(o.code) ? usd(o.fill_price) + " " : won(o.fill_price) + "원 ") : ""}<span class="tag ${st[1]}">${esc(st[0])}</span></span></span></div>`; }).join("")
        : `<div class="empty">아직 주문이 없어요</div>`}</div></section>`;
    this.loadOpen();
    fab("주문", () => orderSheet({}));
    box.onclick = async (e) => {
      const cx = e.target.closest("[data-cancel]");
      if (cx) {
        const o = (this.openMemo?.items || [])[Number(cx.dataset.cancel)];
        if (!o) return;
        const sideTxt = o.side === "buy" ? "매수" : "매도";
        openSheet("주문 취소", `
          <div class="o-mode"><b>${esc(o.name || o.code)} ${sideTxt} ${won(o.left)}주</b>
            ${o.price ? (o.market === "us" ? usd(o.price) : won(o.price) + "원") : "시장가"} · 주문번호 ${esc(o.ord_no)}</div>
          <div class="small muted">아직 체결되지 않은 ${won(o.left)}주를 모두 취소해요. 그사이 체결된 수량은 취소되지 않아요.</div>
          <div id="cxErr"></div>
          <div class="btns"><button type="button" class="btn soft" id="cxNo">닫기</button><button type="button" class="btn dark" id="cxYes">주문 취소</button></div>`, (sb) => {
          $("#cxNo", sb).onclick = closeSheet;
          $("#cxYes", sb).onclick = async (ev) => {
            const btn = ev.currentTarget; btn.disabled = true; btn.textContent = "취소하는 중";
            try {
              await api("POST", "/api/orders/cancel", o);
              closeSheet(); toast(`${o.name || o.code} ${sideTxt} 주문을 취소했어요`);
              this.openMemo = null; await this.fill();
            } catch (err) {
              btn.disabled = false; btn.textContent = "주문 취소";
              $("#cxErr", sb).innerHTML = `<div class="notice warn">${esc(err.message)}</div>`;
            }
          };
        });
        return;
      }
      const b = e.target.closest("[data-sell]");
      if (b) { const [eng, code, qty] = b.dataset.sell.split("|"); orderSheet({ engine: eng, code, side: "sell", qty }); }
    };
  },

  // ---------- 단타 ----------
  openMemo: null,
  async loadOpen() {
    try {
      const d = await GET("/api/orders/open");
      this.openMemo = d;
      if (investState.tab === "trade" && $("#openBox")) $("#openBox").innerHTML = this.openHtml(d);
    } catch (e) { /* 다음 새로고침에 다시 */ }
  },
  openHtml(d) {
    if (!d) return "";
    const err = Object.values(d.errors || {}).map((m) => `<div class="notice warn">${esc(m)}</div>`).join("");
    if (!d.items.length) return err;
    return `${err}<div class="sec-title">미체결 주문 ${d.items.length}건</div>
      <section class="card tight"><div class="list">${d.items.map((o, i) => `<div class="item">
        <span class="ico" style="color:${o.side === "buy" ? "var(--up)" : "var(--down)"}">${o.side === "buy" ? "매수" : "매도"}</span>
        <span class="main"><b>${esc(o.name || o.code)}</b><span class="sub">${o.market === "us" ? "미국 · " : ""}${o.price ? (o.market === "us" ? usd(o.price) : won(o.price) + "원") : "시장가"} · ${won(o.left)}/${won(o.qty)}주 남음${o.time ? " · " + esc(String(o.time).replace(/^(\d\d)(\d\d).*/, "$1:$2")) : ""}</span></span>
        <button type="button" class="btn sm soft o-cancel" data-cancel="${i}">취소</button></div>`).join("")}</div></section>`;
  },

  async scalp(box) {
    const d = await GET("/api/scalp");
    if (investState.tab !== "scalp") return;
    $(".fab")?.remove();
    const T = d.today, on = d.state === "running", paused = d.state === "paused";
    const title = on ? "엔진이 돌고 있어요" : paused ? "새 매수를 멈췄어요" : "엔진이 멈춰 있어요";
    const sub = on ? `${d.mode_label} · ${d.strategy_label} · ${d.market_open ? "장 운영 중" : "다음 장을 기다려요"}${d.gate ? " · " + d.gate : ""}`
      : paused ? "보유 종목의 손절·익절·장마감 청산만 계속해요" : `${d.mode_label || "계좌 없음"} · 시작하면 감시 종목부터 살펴봐요`;
    const histSum = d.history.reduce((s, x) => s + x.pnl, 0);
    box.innerHTML = `
      ${demoNotice(d.data_source)}
      ${d.error ? `<div class="notice warn">${esc(d.error)}</div>` : ""}
      <section class="engine ${on ? "on" : ""}" aria-label="엔진 상태">
        <div><div class="t"><span class="state-dot"></span>${esc(title)}</div><div class="small muted" style="margin-top:4px">${esc(sub)}</div></div>
        <div class="btns">
          ${on ? `<button class="btn yellow" data-ctl="pause">새 매수 멈추기</button>` : `<button class="btn ${paused ? "yellow" : "dark"}" data-ctl="start">${paused ? "다시 시작" : "엔진 시작"}</button>`}
          ${d.state !== "stopped" ? `<button class="btn ${on ? "line-w" : ""}" data-ctl="stop">전부 팔고 멈추기</button>` : `<a class="btn" href="#invest/rules">매매 규칙</a>`}
        </div>
      </section>
      <div class="grid2">
        <div class="tile"><div class="lbl">오늘 실현손익</div><div class="val ${tone(T.realized)}">${signed(T.realized)}원</div></div>
        <div class="tile"><div class="lbl">오늘 승률</div><div class="val">${T.win_rate == null ? "-" : T.win_rate + "%"}</div><div class="tiny">${T.wins}승 ${T.losses}패</div></div>
        <div class="tile"><div class="lbl">손실 한도 남음</div><div class="val">${won(Math.max(0, T.loss_left))}</div><div class="tiny">하루 ${won(T.daily_loss_krw)}원</div></div>
        <div class="tile"><div class="lbl">누적 손익</div><div class="val ${tone(histSum)}">${signed(histSum)}</div><div class="tiny">${d.history.length ? `최근 ${d.history.length}일` : "기록 없음"}</div></div>
      </div>
      <div class="sec-title">보유 중</div>
      <section class="card tight"><div class="list">${d.open.length ? d.open.map((t) => `<div class="item">
        <span class="ico y">${t.entry_ts.slice(11, 16)}</span>
        <span class="main"><b>${esc(t.name)} <span class="tiny">${t.qty}주</span></b><span class="sub">매수 ${won(t.entry_price)} · 손절 <span class="down">${won(t.stop)}</span> · 목표 <span class="up">${won(t.target)}</span></span></span>
        <span class="end"><b class="${tone(t.unrealized)}">${signed(t.unrealized)}</b><span class="small ${tone(t.unrealized)}">${pct(t.unrealized_pct, 2)}</span></span></div>`).join("")
        : `<div class="empty">들고 있는 종목이 없어요</div>`}</div></section>
      <div class="sec-title">오늘 청산</div>
      <section class="card tight"><div class="list">${d.trades.length ? d.trades.map((t) => `<div class="item">
        <span class="ico">${t.exit_ts.slice(11, 16)}</span>
        <span class="main"><b>${esc(t.name)}</b><span class="sub">${esc(t.reason)} · ${won(t.entry_price)} → ${won(t.exit_price)}</span></span>
        <span class="end"><b class="${tone(t.pnl)}">${signed(t.pnl)}</b></span></div>`).join("") : `<div class="empty">오늘 청산한 거래가 없어요</div>`}</div></section>
      <section class="card"><h2>날짜별 손익</h2>${pnlBars(d.history.map((x) => x.pnl), d.history.map((x) => x.session))}</section>
      <div class="btns"><a class="btn" href="#invest/backtest">백테스트</a><a class="btn" href="#invest/rules">매매 규칙</a></div>
      <a class="btn wide" href="#invest/sweep">조건 대량 훑기</a>
      <details class="card"><summary>엔진 기록</summary>
        <div class="log">${d.log.length ? d.log.map((l) => `<div class="${l.level}"><time>${l.ts.slice(11, 16)}</time><span>${esc(l.msg)}</span></div>`).join("") : `<div class="tiny">아직 기록이 없어요</div>`}</div></details>`;
    if (this._logOpen) $("details", box).open = true;
    $("details", box).ontoggle = (e) => (this._logOpen = e.target.open);
    box.onclick = async (e) => {
      const b = e.target.closest("[data-ctl]");
      if (!b) return;
      if (b.dataset.ctl === "stop" && !confirmTap(b, "한 번 더 누르면 모두 팔아요")) return;
      await run(() => api("POST", "/api/scalp/control", { action: b.dataset.ctl }), { start: "엔진을 시작했어요", pause: "새 매수를 멈췄어요", stop: "엔진을 멈췄어요" }[b.dataset.ctl]);
      this.fill();
    };
  },

  // ---------- 적립식 ----------
  async dca(box) {
    const d = await GET("/api/dca");
    if (investState.tab !== "dca") return;
    const h = d.history;
    box.innerHTML = `
      ${d.error ? `<div class="notice warn">${esc(d.error)}</div>` : ""}
      <section class="card">
        <h1 class="hero" style="font-size:22px">${d.plans.length ? `매달 <span class="hl">${won(d.monthly)}원</span>씩 모아<br>지금 ${won(d.value)}원이에요` : `매달 정한 날, 정한 금액만큼<br><span class="hl">자동으로</span> 사 모아요`}</h1>
        <div class="kv"><div>매수 원금<b>${won(d.cost)}</b></div><div>평가손익<b class="${tone(d.gain)}">${pct(d.gain_pct, 1)}</b></div><div>다음 매수<b>${d.schedule[0] ? korDate(d.schedule[0].date, false) : "-"}</b></div></div>
        ${h.length >= 2 ? `${lineChart([{ values: h.map((x) => x.value) }, { values: h.map((x) => x.cost), color: "#5B6676", dash: true, w: 2 }], { h: 90, label: "평가금과 원금 추이" })}
          <div class="legend tiny"><span><i class="sw" style="height:3px;width:16px;background:#1B2430"></i>평가금</span><span><i class="sw" style="height:0;width:16px;border-top:2px dashed #5B6676;border-radius:0"></i>원금</span></div>` : ""}
      </section>
      <section class="card">
        <div class="card-head"><div><h2>자동 매수</h2><div class="tiny">${esc(d.mode_label || "계좌 없음")} · 남은 현금 ${won(d.cash)}원</div></div>
          <button class="switch" role="switch" aria-checked="${d.auto}" aria-label="적립식 자동 매수" data-act="auto"><span></span></button></div>
        ${seg("매수 방식", [["fixed", "플랜별 고정 금액"], ["rebalance", "목표 비중 맞추기"]], d.rebalance, "on-card")}
        <div class="tiny">${d.rebalance === "rebalance" ? "같은 날 사는 금액을 합쳐 목표 비중에서 모자란 종목부터 더 사요." : "플랜마다 정한 금액만큼 사요."} 휴장일이면 다음 영업일, PC가 꺼져 있었다면 7일 안에 켜질 때 사요.</div>
      </section>
      <div class="sec-title">적립 플랜 · 매달 ${won(d.monthly)}원</div>
      <section class="card tight"><div class="list">${d.plans.length ? d.plans.map((p) => `<div class="item">
        <button type="button" class="main" data-pedit="${p.id}" style="border:0;background:none;padding:0;text-align:left;cursor:pointer">
          <b>${esc(p.name)}</b><span class="sub">${p.enabled ? `매월 ${p.day}일 · ${won(p.amount)}원` : "쉬는 중"}${p.weight_goal ? ` · 목표 ${p.weight_goal}%` : ""}</span>
          <span class="sub ${tone(p.gain_pct)}" style="font-weight:700">${p.gain_pct == null ? "아직 없음" : `평가 ${won(p.value)} · ${pct(p.gain_pct)}`}</span></button>
        <button class="switch" role="switch" aria-checked="${!!p.enabled}" aria-label="${esc(p.name)} 자동 매수" data-toggle="${p.id}"><span></span></button></div>`).join("")
        : `<div class="empty">첫 플랜을 만들어 보세요<br><span class="tiny">예: TIGER 미국S&P500(360750) 매달 25일 50만 원</span></div>`}</div></section>
      ${d.plans.some((p) => p.target_weight > 0) ? `<section class="card"><h2>목표 비중과 지금</h2>${d.plans.filter((p) => p.enabled).map((p) => `<div style="display:flex;flex-direction:column;gap:6px">
        <div style="display:flex;justify-content:space-between;font-size:13px"><b>${esc(p.name)}</b><span class="muted">${p.weight_now}% / 목표 ${p.weight_goal}%</span></div>
        <div style="position:relative;height:10px;background:var(--bg);border-radius:5px"><div style="width:${Math.min(100, p.weight_now)}%;height:10px;background:var(--ink);border-radius:5px"></div>
          <div style="position:absolute;top:-4px;left:${Math.min(98, p.weight_goal)}%;width:4px;height:18px;border-radius:2px;background:var(--hl);box-shadow:0 0 0 1px var(--ink)"></div></div></div>`).join("")}</section>` : ""}
      <section class="card"><h2>매수 일정</h2>${d.schedule.length ? d.schedule.map((s) => `<div style="display:flex;justify-content:space-between;font-size:14px"><span>${korDate(s.date)}</span><b>${won(s.amount)}원</b></div>`).join("") : `<div class="tiny">예정된 매수가 없어요</div>`}</section>
      <div class="sec-title">최근 매수 기록</div>
      <section class="card tight"><div class="list">${d.runs.length ? d.runs.slice(0, 10).map((r) => `<div class="item">
        <span class="ico">${r.run_ts.slice(5, 10).replace("-", ".")}</span>
        <span class="main"><b>${esc(r.name || "지운 플랜")}</b><span class="sub">${r.status === "done" ? `${r.qty}주 · ${won(r.price)}원` : esc(r.msg)}</span></span>
        <span class="end"><span class="tag ${r.status === "done" ? "g" : "warn"}">${{ done: "매수", skipped: "건너뜀", failed: "실패" }[r.status]}</span></span></div>`).join("") : `<div class="empty">아직 기록이 없어요</div>`}</div></section>`;
    fab("플랜", () => planSheet(null));
    bindSeg(box, "매수 방식", async (v) => { await run(() => api("PUT", "/api/dca/config", { mode: v }), "매수 방식을 바꿨어요"); this.fill(); });
    box.onclick = async (e) => {
      const b = e.target.closest("[data-act],[data-toggle],[data-pedit]");
      if (!b) return;
      if (b.dataset.act === "auto") { await run(() => api("PUT", "/api/dca/config", { auto: !d.auto }), d.auto ? "자동 매수를 껐어요" : "자동 매수를 켰어요"); this.fill(); }
      else if (b.dataset.toggle) { await run(() => api("PUT", `/api/dca/plans/${b.dataset.toggle}/enabled`, { enabled: b.getAttribute("aria-checked") !== "true" })); this.fill(); }
      else if (b.dataset.pedit) planSheet(d.plans.find((p) => p.id == b.dataset.pedit));
    };
  },

  // ---------- 백테스트 ----------
  async backtest() {
    setTop("백테스트", { back: "#invest/scalp", menu: "invest", sub: "backtest" });
    const d = await GET("/api/scalp");
    const cfg = d.config;
    const S = this.bt || (this.bt = { source: "demo", result: null });
    view.innerHTML = `
      <section class="card">
        <div class="small muted">매매 규칙에 저장한 값으로 검증해요.</div>
        <label class="field">전략<select id="btStrat">${Object.entries(d.strategies).map(([k, v]) => `<option value="${k}" ${k === cfg.strategy.name ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></label>
        ${seg("데이터", [["demo", "가상 데이터"], ["csv", "분봉 CSV"]], S.source, "on-card")}
        <div id="btSrc"></div>
        <button class="btn dark wide" id="btRun">백테스트 실행</button>
        <div class="tiny">신호 다음 봉 시가에 1호가 불리하게 사고, 같은 봉에서 손절·익절이 함께 닿으면 손절로 쳐요. 수수료 ${(cfg.costs.commission * 100).toFixed(3)}%, 거래세 ${(cfg.costs.tax * 100).toFixed(2)}%를 빼요.</div>
      </section>
      <div id="btOut" style="display:flex;flex-direction:column;gap:12px"></div>`;
    const src = () => {
      $("#btSrc").innerHTML = S.source === "demo"
        ? `<label class="field">기간(영업일)<input id="btDays" class="r" inputmode="numeric" value="60"></label>
           <div class="notice">가상 데이터는 무작위 가격이라 <b>실제 수익성과 무관</b>해요. 기능 확인용이에요.</div>`
        : `<label class="field">CSV 파일<input id="btFile" type="file" accept=".csv,text/csv" style="padding:12px"></label>
           <label class="field">종목 이름<input id="btCode" placeholder="예: 삼성전자"></label>
           <div class="tiny">열: date,time,open,high,low,close,volume (또는 datetime 한 열)</div>`;
    };
    src();
    bindSeg(view, "데이터", (v) => { S.source = v; src(); });
    if (S.result) this.btRender(S.result);
    $("#btRun").onclick = async () => {
      const btn = $("#btRun");
      btn.disabled = true; btn.textContent = "계산 중";
      try {
        const strategy = { ...cfg.strategy, name: $("#btStrat").value };
        let r;
        if (S.source === "demo") r = await run(() => api("POST", "/api/backtest", { days: num($("#btDays").value) || 60, strategy }));
        else {
          const f = $("#btFile").files[0];
          if (!f) { toast("CSV 파일을 골라 주세요", true); return; }
          const fd = new FormData(); fd.append("file", f); fd.append("code", $("#btCode").value || f.name.replace(/\.csv$/i, "")); fd.append("strategy", JSON.stringify(strategy));
          r = await run(() => api("POST", "/api/backtest", fd, true));
        }
        r.label = d.strategies[strategy.name];
        S.result = r; this.btRender(r);
        $("#btOut").scrollIntoView({ behavior: "smooth", block: "start" });
      } finally { btn.disabled = false; btn.textContent = "백테스트 실행"; }
    };
  },
  btRender(r) {
    const s = r.stats;
    $("#btOut").innerHTML = `
      <div class="sec-title">${esc(r.label || "")} · ${s.days}일 · 거래 ${s.trades}건</div>
      <div class="grid2">
        <div class="tile"><div class="lbl">순손익(비용 차감)</div><div class="val ${tone(s.net)}">${signed(s.net)}</div><div class="tiny">${pct(s.net_pct, 2)}</div></div>
        <div class="tile"><div class="lbl">승률</div><div class="val">${s.win_rate}%</div><div class="tiny">${s.wins}승 ${s.losses}패</div></div>
        <div class="tile"><div class="lbl">손익비(PF)</div><div class="val">${s.profit_factor ?? "-"}</div><div class="tiny">1보다 커야 남아요</div></div>
        <div class="tile"><div class="lbl">최대 낙폭</div><div class="val">${s.mdd_pct}%</div><div class="tiny">비용 ${won(s.fees)}원</div></div>
      </div>
      <section class="card">
        <div class="kv"><div>거래당 기대<b class="${tone(s.expectancy)}">${signed(s.expectancy)}</b></div><div>평균 수익<b class="up">${signed(s.avg_win)}</b></div><div>평균 손실<b class="down">${signed(s.avg_loss)}</b></div></div>
        <div class="tiny">${Object.entries(s.by_reason).map(([k, v]) => `${esc(k)} ${v}`).join(" · ")} · 이긴 날 ${s.positive_days}/${s.days}</div>
        <h2>자산 추이</h2>${lineChart([{ values: r.daily.map((x) => x.equity) }], { h: 100, label: "백테스트 자산 추이" })}
        <h2>날짜별 손익</h2>${pnlBars(r.daily.map((x) => x.pnl), r.daily.map((x) => x.date))}
      </section>
      <div class="sec-title">거래 내역 · 최근 ${Math.min(80, r.trades.length)}건</div>
      <section class="card tight"><div class="list">${r.trades.length ? r.trades.slice().reverse().slice(0, 80).map((t) => `<div class="item">
        <span class="ico">${t.date.slice(5).replace("-", ".")}</span>
        <span class="main"><b>${esc(t.code)} <span class="tiny">${t.entry_t}~${t.exit_t}</span></b><span class="sub">${esc(t.reason)} · ${won(t.entry)} → ${won(t.exit)}</span></span>
        <span class="end"><b class="${tone(t.pnl)}">${signed(t.pnl)}</b></span></div>`).join("") : `<div class="empty">조건에 맞는 거래가 없었어요</div>`}</div></section>`;
  },

  // ---------- 조건 대량 훑기(vectorbt) ----------
  async sweep() {
    setTop("조건 훑기", { back: "#invest/scalp", menu: "invest", sub: "sweep" });
    const [d, st] = await Promise.all([GET("/api/scalp"), GET("/api/sweep")]);
    const cfg = d.config;
    const F = this.sw || (this.sw = { strategy: cfg.strategy.name, source: "kiwoom", days: 40, codes: (cfg.watchlist || []).join(", ") });
    const GRID_N = { orb_vwap: 1152, vwap_pullback: 288 };
    view.innerHTML = `
      <section class="card">
        <div class="small muted">여러 조건을 한꺼번에 돌려 보고, 앞쪽 70% 날짜로 순위를 매긴 뒤 뒤쪽 30%로 맞는지 확인해요. 상위 5개는 실제 엔진 규칙으로 한 번 더 돌려요.</div>
        <label class="field">전략<select id="swStrat">${Object.entries(d.strategies).map(([k, v]) => `<option value="${k}" ${k === F.strategy ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></label>
        ${seg("분봉", [["kiwoom", "키움 실제 분봉"], ["demo", "가상 데이터"]], F.source, "on-card")}
        <div class="fields">
          <label class="field">기간(영업일)<select id="swDays">${[20, 40, 60, 90].map((n) => `<option value="${n}" ${n == F.days ? "selected" : ""}>${n}일</option>`).join("")}</select></label>
          <label class="field">조건 수<input id="swN" value="${GRID_N[F.strategy] || ""}개" disabled></label></div>
        <label class="field">종목(6자리, 쉼표로 · 최대 12개)<input id="swCodes" value="${esc(F.codes)}" inputmode="numeric"></label>
        <button class="btn dark wide" id="swGo">훑기 시작</button>
        <div class="tiny">처음엔 vectorbt 설치와 분봉 받기로 몇 분 걸려요. 받은 분봉은 저장해 두고 다음부터 다시 써요.</div>
      </section>
      <div id="swOut" style="display:flex;flex-direction:column;gap:12px"></div>`;
    const keep = () => { F.strategy = $("#swStrat").value; F.days = num($("#swDays").value); F.codes = $("#swCodes").value; };
    $("#swStrat").onchange = () => { keep(); $("#swN").value = (GRID_N[F.strategy] || "") + "개"; };
    bindSeg(view, "분봉", (v) => { keep(); F.source = v; });
    $("#swGo").onclick = async () => {
      keep();
      const codes = F.codes.split(/[,\s]+/).map((x) => x.replace(/\D/g, "")).filter((x) => x.length === 6);
      const r = await run(() => api("POST", "/api/sweep", { strategy: F.strategy, source: F.source, days: F.days, codes }));
      this.swRender(r);
    };
    this.swRender(st);
  },
  async sweepPoll() {
    if (!$("#swOut") || this.swLast?.status !== "running") return;
    this.swRender(await GET("/api/sweep"));
  },
  swRender(st) {
    this.swLast = st;
    const box = $("#swOut");
    if (!box) return;
    if ($("#swGo")) $("#swGo").disabled = st.status === "running";
    if (st.status === "running") {
      box.innerHTML = `<section class="card"><div class="card-head"><h2>훑는 중</h2><b>${st.progress || 0}%</b></div>
        <div class="bar"><div style="width:${st.progress || 0}%"></div></div>
        <div class="small muted">${esc(st.msg || "")}</div>
        <button class="btn soft wide" id="swStop">멈추기</button></section>`;
      $("#swStop").onclick = async () => { await run(() => api("POST", "/api/sweep/stop"), "멈추라고 했어요"); };
      return;
    }
    if (st.status === "error") { box.innerHTML = `<div class="notice warn">${esc(st.error || "실패했어요")}</div>`; return; }
    if (st.status !== "done") { box.innerHTML = ""; return; }
    const L = st.labels || {}, m = st.meta, cur = st.current;
    const keys = Object.keys(st.grid || {});
    const chk = Object.fromEntries((st.checked || []).map((c) => [c.id, c]));
    const statLine = (x) => x && x.trades ? `${x.trades}건 · 승률 ${x.win_rate}% · 평균 ${x.avg > 0 ? "+" : ""}${x.avg}% · PF ${x.pf ?? "-"}` : "거래 없음";
    const params = (p) => keys.map((k) => {
      const diff = cur && cur.params[k] !== p[k];
      return `<span class="sw-p ${diff ? "diff" : ""}">${esc(L[k] || k)} <b>${p[k]}</b></span>`;
    }).join("");
    const verdict = (r) => {
      const c = chk[r.id];
      const oosOk = r.oos.trades >= 5 && r.oos.avg > 0;
      const exact = c ? (c.oos && c.oos.trades ? c.oos.net > 0 : null) : null;
      if (oosOk && exact !== false) return `<span class="tag g">검증 구간도 플러스</span>`;
      if (r.oos.trades < 5) return `<span class="tag">검증 거래 부족</span>`;
      return `<span class="tag warn">검증에서 무너짐</span>`;
    };
    box.innerHTML = `
      ${st.source === "demo" ? `<div class="notice warn">가상 데이터 결과예요. 무작위 가격이라 실제 수익성과 무관해요.</div>` : ""}
      <section class="card">
        <h2>${esc(st.strategy === "orb_vwap" ? "시가범위 돌파" : "VWAP 눌림 반등")} · 조건 ${m.combos}개 · ${(st.elapsed || 0)}초</h2>
        <div class="kv"><div>학습 기간<b>${esc(m.first.slice(5))}~${esc(m.split_day.slice(5))}</b></div><div>검증 기간<b>~${esc(m.last.slice(5))}</b></div><div>종목<b>${m.codes.length}개</b></div></div>
        <div class="small muted">거래 30건 이상인 조건 ${st.tested_ok}개 중 학습 기간에 평균 플러스는 ${st.positive_is}개예요. 수익은 거래당 % (수수료·세금·슬리피지 차감, 1주 기준)예요.</div>
      </section>
      ${cur ? `<section class="card"><h2>지금 내 설정</h2><div class="sw-ps">${params(cur.params)}</div>
        <div class="sw-row"><span>학습</span><b class="${tone(cur.is.avg)}">${statLine(cur.is)}</b></div>
        <div class="sw-row"><span>검증</span><b class="${tone(cur.oos.avg)}">${statLine(cur.oos)}</b></div></section>` : ""}
      <div class="sec-title">학습 기간 상위 ${st.top.length}개 · 바뀐 값은 굵게</div>
      ${st.top.length ? st.top.map((r, i) => {
        const c = chk[r.id];
        return `<section class="card sw-card">
          <div class="card-head"><h2>${i + 1}위</h2>${verdict(r)}</div>
          <div class="sw-ps">${params(r.params)}</div>
          <div class="sw-row"><span>학습</span><b class="${tone(r.is.avg)}">${statLine(r.is)}</b></div>
          <div class="sw-row"><span>검증</span><b class="${tone(r.oos.avg)}">${statLine(r.oos)}</b></div>
          ${c ? `<div class="sw-row"><span>엔진 규칙</span><b class="${tone(c.full.net)}">전체 ${signed(c.full.net)}원 · PF ${c.full.profit_factor ?? "-"}${c.oos ? ` · 검증 ${signed(c.oos.net)}원` : ""}</b></div>` : ""}
          <button class="btn wide" data-apply="${r.id}">이 조건을 매매 규칙에 적용</button></section>`;
      }).join("") : `<div class="notice">거래 30건 이상이면서 학습 기간에 플러스인 조건이 없어요. 기간을 늘리거나 종목을 더 넣어 보세요. 플러스 조건이 없다는 것도 중요한 결과예요.</div>`}
      <div class="tiny" style="padding:0 4px">앞 기간에서 고른 조건은 뒤 기간에서 대개 성적이 떨어져요. '검증 구간도 플러스'인 조건만 고려하고, 적용 뒤에도 앱 내 모의로 먼저 돌려 보세요.</div>`;
    box.onclick = async (e) => {
      const b = e.target.closest("[data-apply]");
      if (!b) return;
      if (!confirmTap(b, "한 번 더 누르면 매매 규칙을 바꿔요")) return;
      await run(() => api("POST", "/api/sweep/apply", { id: Number(b.dataset.apply) }), "매매 규칙에 적용했어요");
    };
  },

  // ---------- 매매 규칙 ----------
  async rules() {
    setTop("매매 규칙", { back: "#invest/scalp", menu: "invest", sub: "rules" });
    const d = await GET("/api/scalp");
    const c = d.config;
    let strat = c.strategy.name;
    const inputs = (group, obj) => Object.entries(PARAM_LABELS[group]).map(([k, label]) =>
      `<label class="field">${esc(label)}<input class="r" data-g="${group}" data-k="${k}" value="${esc(obj[k])}" ${typeof obj[k] === "number" ? 'inputmode="decimal"' : ""}></label>`).join("");
    const collect = () => $$("[data-g]").forEach((i) => {
      const g = i.dataset.g, k = i.dataset.k;
      const tgt = g === "exit" ? c.exit : g === "risk" ? c.risk : c.strategy[g];
      tgt[k] = typeof tgt[k] === "number" ? num(i.value) : i.value.trim();
    });
    const draw = () => {
      view.innerHTML = `
        ${d.state !== "stopped" ? `<div class="notice">엔진이 돌고 있어요. 저장하면 다음 판단부터 바로 적용돼요.</div>` : ""}
        <section class="card"><h2>진입 전략</h2>
          ${seg("전략", Object.entries(d.strategies), strat, "on-card")}
          <div class="small muted">${esc(STRAT_DESC[strat])}</div>
          <div class="fields">${inputs(strat, c.strategy[strat])}</div></section>
        <section class="card"><h2>청산</h2>
          <div class="small muted">목표의 절반쯤 가면 손절가를 매수가로 올리고, 너무 오래 들고 있으면 정리해요.</div>
          <div class="fields">${inputs("exit", c.exit)}</div></section>
        <section class="card"><h2>돈 관리와 안전장치</h2>
          <div class="small muted">한 번 틀렸을 때 잃을 돈을 먼저 정하고 손절폭에 맞춰 수량을 계산해요. 목표수익이 비용을 충분히 넘지 않으면 신호가 나도 사지 않아요.</div>
          <div class="fields">${inputs("risk", c.risk)}</div></section>
        <section class="card"><h2>감시 종목</h2>
          <label class="field">종목코드(쉼표나 줄바꿈)<textarea id="wl" rows="5" inputmode="numeric">${esc(c.watchlist.join("\n"))}</textarea></label>
          <div class="tiny">${c.watchlist.map((x) => esc(d.names[x] || x)).join(", ")} · 10개 안팎을 권해요</div></section>
        <button class="btn dark wide" id="saveRules">규칙 저장</button>`;
      bindSeg(view, "전략", (v) => { collect(); strat = v; draw(); });
      $("#saveRules").onclick = async () => {
        collect();
        await run(() => api("PUT", "/api/scalp/config", { strategy: { ...c.strategy, name: strat }, exit: c.exit, risk: c.risk, watchlist: $("#wl").value.split(/[\s,]+/).filter(Boolean) }), "규칙을 저장했어요");
        location.hash = "#invest/scalp";
      };
    };
    draw();
  },
};
const PARAM_LABELS = {
  orb_vwap: { range_min: "시가범위(분)", vol_mult: "돌파 거래량(배)", atr_n: "ATR 기간(봉)", stop_atr: "손절폭(ATR 배)", target_r: "목표(손절폭 배)", max_day_pct: "당일 상승 상한(%)", min_range_pct: "시가범위 최소폭(%)" },
  vwap_pullback: { min_day_pct: "당일 최소 상승(%)", max_day_pct: "당일 상승 상한(%)", band_pct: "VWAP 근접(%)", lookback: "눌림 확인 봉 수", min_bars: "장 시작 후(분)", target_r: "목표(손절폭 배)" },
  exit: { breakeven_r: "본전 이동(손절폭 배)", max_hold_min: "최대 보유(분)" },
  risk: { capital: "운용 자본(원)", risk_pct: "1회 감수 손실(%)", max_position_krw: "종목당 최대(원)", max_positions: "동시 보유 수", daily_loss_krw: "하루 손실 한도(원)", max_trades: "하루 최대 거래", loss_streak: "연속 손절 횟수", cooldown_min: "쉬는 시간(분)", min_edge_mult: "최소 목표(비용 배)", entry_start: "진입 시작", entry_end: "진입 마감", force_exit: "전부 청산" },
};
const STRAT_DESC = {
  orb_vwap: "장 시작 후 정한 시간 동안의 고가를 거래량이 붙으며 처음 넘을 때 사요. 가격이 VWAP(당일 평균 체결가) 위일 때만 들어가요.",
  vwap_pullback: "오늘 충분히 오른 종목이 VWAP 근처까지 눌렸다가 직전 봉 고가를 넘으면 사요. 손절은 눌림 저점 바로 아래예요.",
};


// =====================================================================
// 투자 → 시세: 국내·미국 관심종목, 지수, 검색, 종목 상세(차트)
// =====================================================================
const mkt = {
  m: store.get("mkt") === "us" ? "us" : "kr",
  showAmt: store.get("mktAmt") === "1",
  last: {},          // 직전 가격(바뀐 칸 깜빡임용)
  d: null,           // 상세 화면 상태

  money(n, cur) {
    if (n == null || isNaN(n)) return "-";
    if (cur === "KRW" || this.m === "kr" && cur !== "USD") return won(n);
    return Number(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  },
  chg(q) {
    if (q.price == null) return "";
    return this.showAmt ? (q.change > 0 ? "+" : q.change < 0 ? "−" : "") + this.money(Math.abs(q.change), q.currency) : pct(q.change_pct, 2);
  },
  spark(pts, up) {
    const v = (pts || []).filter((x) => x != null);
    if (v.length < 2) return `<svg class="spark" aria-hidden="true"></svg>`;
    const lo = Math.min(...v), hi = Math.max(...v), r = hi - lo || 1, W = 64, H = 26;
    const d = v.map((y, i) => `${i ? "L" : "M"}${(i / (v.length - 1) * W).toFixed(1)},${(H - 2 - (y - lo) / r * (H - 4)).toFixed(1)}`).join("");
    return `<svg class="spark" viewBox="0 0 ${W} ${H}" aria-hidden="true"><path d="${d}" fill="none" stroke="${up ? "var(--up)" : "var(--down)"}" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"/></svg>`;
  },

  // ---------- 관심종목 보드 ----------
  async board(box) {
    const first = !$("#mkBoard", box);
    if (first) {
      $(".fab")?.remove();
      box.innerHTML = `<div id="mkBoard" class="mk">
        <div class="mk-idx" id="mkIdx" aria-label="지수"></div>
        <div class="mk-bar">
          ${seg("시장", [["kr", "국내"], ["us", "미국"]], this.m, "mk-seg")}
          <button type="button" class="mk-find" id="mkFind" aria-label="종목 검색"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/></svg>종목 검색</button>
        </div>
        <div id="mkNote"></div>
        <section class="card tight"><div class="mk-head"><span>관심종목</span><button type="button" class="link" id="mkToggle"></button></div><div class="list" id="mkList"><div class="empty">불러오는 중</div></div></section>
        <div class="tiny" id="mkFoot" style="padding:0 4px"></div>
      </div>`;
      bindSeg(box, "시장", (v) => {
        this.m = v; store.set("mkt", v === "us" ? "us" : ""); this.last = {};
        const c = this.snap[v] || memo.get("mk." + v);
        if (c) this.renderBoard(c, v); else { $("#mkList").innerHTML = `<div class="empty">불러오는 중</div>`; $("#mkIdx").innerHTML = ""; }
        this.board(box);
      });
      $("#mkFind").onclick = () => this.search();
      $("#mkToggle").onclick = () => { this.showAmt = !this.showAmt; store.set("mktAmt", this.showAmt ? "1" : ""); this.board(box); };
      $("#mkList").onclick = (e) => {
        const r = e.target.closest("[data-sym]");
        if (r) location.hash = `#invest/stock?m=${this.m}&s=${encodeURIComponent(r.dataset.sym)}`;
      };
    }
    const m = this.m;
    if (first || !this.snap[m]) { const c = this.snap[m] || memo.get("mk." + m); if (c) this.renderBoard(c, m); }
    const d = await GET(`/api/market/overview?mkt=${m}`);
    this.snap[m] = d; memo.set("mk." + m, d);
    if (investState.tab !== "market" || m !== this.m || !$("#mkList")) return;
    this.renderBoard(d, m);
  },
  snap: {},
  renderBoard(d, m) {
    if (!$("#mkList")) return;
    $("#mkToggle").textContent = this.showAmt ? "등락폭" : "등락률";
    $("#mkIdx").innerHTML = d.indices.map((x) => `<div class="mk-i"><span>${esc(x.name)}</span>
      <b>${x.price == null ? "-" : x.name.includes("환율") ? won(x.price) : Number(x.price).toLocaleString("en-US", { maximumFractionDigits: 2 })}</b>
      <em class="${tone(x.change_pct)}">${x.price == null ? "" : pct(x.change_pct, 2)}</em></div>`).join("");
    const delayed = m === "kr" && !d.kiwoom;
    $("#mkNote").innerHTML = delayed ? `<div class="notice">키움을 연결하면 국내 시세가 실시간으로 바뀌어요. 지금은 지연 시세예요. <a href="#settings/accounts">연결하기</a></div>` : "";
    const L = d.items;
    $("#mkList").innerHTML = L.length ? L.map((q) => {
      const key = m + q.sym, prev = this.last[key];
      const flash = prev != null && q.price != null && prev !== q.price ? (q.price > prev ? "fl-up" : "fl-dn") : "";
      if (q.price != null) this.last[key] = q.price;
      const sub = m === "us" && d.fx && q.price != null ? `${esc(q.sym)} · ${won(q.price * d.fx)}원` : `${esc(q.sym)}${q.exchange ? " · " + esc(q.exchange) : ""}`;
      return `<button type="button" class="item mk-row" data-sym="${esc(q.sym)}">
        <span class="main"><b>${esc(q.name || q.sym)}</b><span class="sub">${q.error ? "시세를 받지 못했어요" : sub}</span></span>
        ${this.spark(q.spark, (q.change || 0) >= 0)}
        <span class="end"><b class="mk-p ${flash}">${m === "us" && q.price != null ? "$" : ""}${this.money(q.price, q.currency)}</b>
          <span class="mk-c ${tone(q.change)}">${this.chg(q)}</span></span></button>`;
    }).join("") : `<div class="empty">관심종목이 없어요. 종목 검색에서 별을 눌러 추가하세요.</div>`;
    $("#mkFoot").textContent = `${(d.updated_at || "").slice(11, 19)} 기준 · ${m === "kr" ? (d.kiwoom ? "키움 실시간" : "지연 시세") : "미국 시세는 공개 시세(최대 15분 지연)"} · 종목을 누르면 차트와 상세가 보여요`;
  },

  // ---------- 검색 ----------
  search() {
    const m = this.m;
    openSheet(m === "kr" ? "국내 종목 검색" : "미국 종목 검색", `
      <label class="field">${m === "kr" ? "종목명 또는 6자리 코드" : "회사 이름 또는 기호(AAPL)"}<input id="mkQ" type="search" autocomplete="off" enterkeyhint="search" placeholder="${m === "kr" ? "삼성전자" : "apple"}"></label>
      <div class="list" id="mkRes"><div class="empty small">${m === "kr" ? "이름 일부만 넣어도 찾아요" : "영문으로 넣어 주세요"}</div></div>`, (box) => {
      const q = $("#mkQ", box);
      let t = null, seq = 0;
      const go = async () => {
        const v = q.value.trim(), my = ++seq;
        if (!v) { $("#mkRes").innerHTML = ""; return; }
        $("#mkRes").innerHTML = `<div class="empty small">찾는 중</div>`;
        try {
          const [res, w] = await Promise.all([GET(`/api/market/search?mkt=${m}&q=${encodeURIComponent(v)}`), GET(`/api/market/overview?mkt=${m}`).catch(() => ({ items: [] }))]);
          if (my !== seq) return;
          const on = new Set((w.items || []).map((x) => x.sym));
          $("#mkRes").innerHTML = res.length ? res.map((x) => `<div class="item">
            <a class="main" href="#invest/stock?m=${m}&s=${encodeURIComponent(x.sym)}" style="text-decoration:none"><b>${esc(x.name)}</b><span class="sub">${esc(x.sym)}${x.exchange ? " · " + esc(x.exchange) : ""}</span></a>
            <button type="button" class="star" data-star="${esc(x.sym)}" aria-pressed="${on.has(x.sym)}" aria-label="${esc(x.name)} 관심종목">${STAR}</button></div>`).join("")
            : `<div class="empty small">찾는 종목이 없어요</div>`;
        } catch (e) { if (my === seq) $("#mkRes").innerHTML = `<div class="notice warn">${esc(e.message)}</div>`; }
      };
      q.oninput = () => { clearTimeout(t); t = setTimeout(go, 300); };
      $("#mkRes").onclick = async (e) => {
        const b = e.target.closest("[data-star]");
        if (!b) { if (e.target.closest("a")) closeSheet(); return; }
        const on = b.getAttribute("aria-pressed") !== "true";
        await run(() => api("POST", "/api/market/watch", { mkt: m, sym: b.dataset.star, on }), on ? "관심종목에 넣었어요" : "관심종목에서 뺐어요");
        b.setAttribute("aria-pressed", String(on));
        if ($("#mkList")) this.board($("#iv"));
      };
      setTimeout(() => q.focus(), 50);
    });
  },

  // ---------- 종목 상세 ----------
  async detail(h) {
    const m = h.params.get("m") === "us" ? "us" : "kr", sym = (h.params.get("s") || "").toUpperCase();
    this.d = { m, sym, range: "1d", q: null, chart: null };
    setTop("시세", { back: "#invest/market", menu: "invest", sub: "stock", right: `<button type="button" class="star on-deep" id="mkStar" aria-pressed="false" aria-label="관심종목">${STAR}</button>` });
    view.innerHTML = `<div class="mk-d" id="mkD"><div class="empty">불러오는 중</div></div>`;
    // 목록에서 이미 받은 값이 있으면 먼저 그려 둔다
    const pre = this.qmemo[m + sym] || ((this.snap[m] || {}).items || []).find((x) => x.sym === sym);
    const D = this.d;
    if (pre && pre.price != null) { D.q = { currency: m === "us" ? "USD" : "KRW", ...pre }; D.chart = this.cmemo[m + sym + "1d"] || null; this.drawDetail(true); }
    await this.refreshDetail(!pre);
  },
  qmemo: {}, cmemo: {},
  async refreshDetail(full) {
    const D = this.d;
    if (!D || !$("#mkD")) return;
    const needChart = full || !D.chart || D.range === "1d";
    const [q, ch] = await Promise.all([
      GET(`/api/market/quote/${D.m}/${encodeURIComponent(D.sym)}`),
      needChart ? GET(`/api/market/chart/${D.m}/${encodeURIComponent(D.sym)}?range=${D.range}`).catch((e) => ({ points: [], error: e.message })) : null]);
    if (this.d !== D) return;
    D.q = q; this.qmemo[D.m + D.sym] = q;
    if (ch) { D.chart = ch; this.cmemo[D.m + D.sym + D.range] = ch; }
    this.drawDetail(full || !$("#mkPrice"));
  },
  drawDetail(full) {
    const D = this.d, q = D.q, m = D.m, cur = q.currency, usd = m === "us";
    const P = (n) => (usd ? "$" : "") + this.money(n, cur) + (usd ? "" : "원");
    $("#mkStar").setAttribute("aria-pressed", String(!!q.watched));
    $("#mkStar").onclick = async () => {
      const on = !q.watched;
      await run(() => api("POST", "/api/market/watch", { mkt: m, sym: D.sym, on }), on ? "관심종목에 넣었어요" : "관심종목에서 뺐어요");
      q.watched = on; $("#mkStar").setAttribute("aria-pressed", String(on));
    };
    const r52 = q.high52 && q.low52 && q.high52 > q.low52 ? Math.min(100, Math.max(0, (q.price - q.low52) / (q.high52 - q.low52) * 100)) : null;
    const hd = q.holding;
    const box = $("#mkD");
    if (full || !$("#mkPrice")) {
      box.innerHTML = `
        <section class="mk-hero">
          <div class="mk-name"><b>${esc(q.name)}</b><span>${esc(D.sym)}${q.exchange ? " · " + esc(q.exchange) : ""}${q.delayed ? " · 지연" : ""}</span></div>
          <div class="mk-price" id="mkPrice"></div>
          <div class="mk-delta" id="mkDelta"></div>
        </section>
        <section class="card mk-chart-card">
          <div class="mk-chart" id="mkChart"></div>
          ${seg("기간", [["1d", "1일"], ["1m", "1개월"], ["3m", "3개월"], ["1y", "1년"]], D.range, "on-card mk-range")}
        </section>
        <section class="card"><div class="mk-grid" id="mkGrid"></div>
          <div id="mk52"></div></section>
        <div id="mkHold"></div>
        ${usd && !q.live ? `<div class="tiny" style="padding:0 4px">지금은 공개 시세(최대 15분 지연)예요. 키움을 연결하면 실시간으로 보여요.</div>` : ""}
        <div class="mk-act"><button type="button" class="btn blue" data-o="sell">매도</button><button type="button" class="btn red" data-o="buy">매수</button></div>`;
      bindSeg(box, "기간", async (v) => {
        D.range = v;
        const cm = this.cmemo[m + D.sym + v];
        if (cm) { D.chart = cm; this.drawChart(); } else $("#mkChart").innerHTML = `<div class="empty small">불러오는 중</div>`;
        try { D.chart = await GET(`/api/market/chart/${m}/${encodeURIComponent(D.sym)}?range=${v}`); this.cmemo[m + D.sym + v] = D.chart; } catch (e) { if (!cm) D.chart = { points: [], error: e.message }; }
        if (D.range !== v) return;
        if (this.d === D) this.drawChart();
      });
      $$("[data-o]", box).forEach((b) => b.onclick = () => orderSheet({ code: D.sym, market: m, side: b.dataset.o, exch: D.q?.exch, name: D.q?.name,
        qty: b.dataset.o === "sell" && D.q?.holding ? (D.q.holding.sellable || D.q.holding.qty) : 1 }));
    }
    const pEl = $("#mkPrice"), old = pEl.dataset.v;
    pEl.textContent = P(q.price); pEl.dataset.v = q.price;
    if (old && Number(old) !== q.price) { pEl.classList.remove("fl-up", "fl-dn"); void pEl.offsetWidth; pEl.classList.add(q.price > Number(old) ? "fl-up" : "fl-dn"); }
    $("#mkDelta").className = "mk-delta " + tone(q.change);
    $("#mkDelta").innerHTML = `${q.change > 0 ? "▲" : q.change < 0 ? "▼" : ""} ${this.money(Math.abs(q.change), cur)} (${pct(q.change_pct, 2)})${usd && q.fx ? `<span class="mk-krw">약 ${won(q.price * q.fx)}원</span>` : ""}`;
    const vol = q.volume != null ? Number(q.volume).toLocaleString("ko-KR") : "-";
    $("#mkGrid").innerHTML = [["시가", q.open], ["고가", q.high], ["저가", q.low], ["전일 종가", q.prev]]
      .map(([k, v]) => `<div><span>${k}</span><b class="${k === "고가" ? "up" : k === "저가" ? "down" : ""}">${v == null ? "-" : this.money(v, cur)}</b></div>`).join("") + `<div><span>거래량</span><b>${vol}</b></div>`;
    $("#mk52").innerHTML = r52 == null ? "" : `<div class="mk-52"><div class="tiny">52주 범위</div>
      <div class="mk-52bar"><i style="left:${r52}%"></i></div>
      <div class="mk-52lbl"><span>최저 ${this.money(q.low52, cur)}</span><span>최고 ${this.money(q.high52, cur)}</span></div></div>`;
    $("#mkHold").innerHTML = hd ? `<section class="card"><div class="card-head"><h2>내 보유</h2><span class="tiny">키움 계좌</span></div>
      <div class="kv"><div>보유 수량<b>${won(hd.qty)}주</b></div><div>평균 단가<b>${m === "us" ? this.money(hd.avg_price, "USD") : won(hd.avg_price)}</b></div><div>평가 금액<b>${won(hd.value)}원</b></div></div>
      <div class="small ${tone(hd.pnl)}" style="font-weight:700">평가손익 ${signed(hd.pnl)}원 (${pct(hd.pnl_pct, 2)})</div></section>` : "";
    this.drawChart();
  },
  drawChart() {
    const D = this.d, el = $("#mkChart");
    if (!el) return;
    const c = D.chart || {}, pts = (c.points || []).filter((p) => p.c != null);
    if (pts.length < 2) { el.innerHTML = `<div class="empty small">${esc(c.error || "차트 자료가 아직 없어요")}</div>`; return; }
    const base = D.range === "1d" && c.prev ? c.prev : pts[0].c;
    const last = pts[pts.length - 1].c, up = last >= base, col = up ? "var(--up)" : "var(--down)";
    const vals = pts.map((p) => p.c).concat(D.range === "1d" && c.prev ? [c.prev] : []);
    const lo = Math.min(...vals), hi = Math.max(...vals), rr = hi - lo || 1;
    const W = 340, H = 180, pad = 8;
    const X = (i) => (i / (pts.length - 1)) * W, Y = (v) => pad + (1 - (v - lo) / rr) * (H - pad * 2);
    const line = pts.map((p, i) => `${i ? "L" : "M"}${X(i).toFixed(1)},${Y(p.c).toFixed(1)}`).join("");
    const hiI = pts.findIndex((p) => p.c === Math.max(...pts.map((x) => x.c))), loI = pts.findIndex((p) => p.c === Math.min(...pts.map((x) => x.c)));
    const cur = D.q.currency;
    const lab = (i, v, below) => { const x = X(i), anchor = x < 50 ? "start" : x > W - 50 ? "end" : "middle"; return `<text x="${x.toFixed(1)}" y="${(Y(v) + (below ? 14 : -6)).toFixed(1)}" text-anchor="${anchor}" class="mk-lab">${below ? "최저" : "최고"} ${this.money(v, cur)}</text>`; };
    el.innerHTML = `<svg viewBox="0 -14 ${W} ${H + 28}" class="mk-svg" role="img" aria-label="가격 차트">
      <defs><linearGradient id="mkG" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${up ? "#D0342C" : "#2557D6"}" stop-opacity=".16"/><stop offset="1" stop-color="${up ? "#D0342C" : "#2557D6"}" stop-opacity="0"/></linearGradient></defs>
      ${D.range === "1d" && c.prev ? `<line x1="0" x2="${W}" y1="${Y(c.prev).toFixed(1)}" y2="${Y(c.prev).toFixed(1)}" stroke="var(--line2)" stroke-dasharray="3 4"/>` : ""}
      <path d="${line}L${W},${H}L0,${H}Z" fill="url(#mkG)"/>
      <path d="${line}" fill="none" stroke="${col}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
      ${lab(hiI, pts[hiI].c, false)}${lab(loI, pts[loI].c, true)}
      <g id="mkCur" visibility="hidden"><line y1="0" y2="${H}" stroke="var(--ink3)" stroke-width="1"/><circle r="4.5" fill="${col}" stroke="#fff" stroke-width="2"/></g>
    </svg><div class="mk-tip" id="mkTip" hidden></div>`;
    const svg = $("svg", el), g = $("#mkCur", el), tip = $("#mkTip", el);
    const at = (ev) => {
      const r = svg.getBoundingClientRect(), x = ((ev.touches ? ev.touches[0].clientX : ev.clientX) - r.left) / r.width;
      const i = Math.max(0, Math.min(pts.length - 1, Math.round(x * (pts.length - 1))));
      g.setAttribute("visibility", "visible");
      $("line", g).setAttribute("x1", X(i)); $("line", g).setAttribute("x2", X(i));
      $("circle", g).setAttribute("cx", X(i)); $("circle", g).setAttribute("cy", Y(pts[i].c));
      const ch = (pts[i].c - base) / base * 100;
      tip.hidden = false; tip.innerHTML = `<b>${this.money(pts[i].c, cur)}</b> <span class="${tone(ch)}">${pct(ch, 2)}</span> <span>${esc(pts[i].t)}</span>`;
    };
    const off = () => { g.setAttribute("visibility", "hidden"); tip.hidden = true; };
    svg.addEventListener("pointerdown", at); svg.addEventListener("pointermove", at);
    svg.addEventListener("pointerleave", off); svg.addEventListener("pointerup", (e) => { if (e.pointerType !== "mouse") setTimeout(off, 1200); });
  },
};
const STAR = `<svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3.2l2.7 5.6 6.1.8-4.5 4.2 1.1 6.1L12 17l-5.4 2.9 1.1-6.1L3.2 9.6l6.1-.8z" fill="currentColor" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/></svg>`;

// ---------- 주문 시트 ----------
const usd = (n) => (n == null || isNaN(n) ? "-" : "$" + Number(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const isUsCode = (c) => /[A-Za-z]/.test(String(c || ""));
function orderSheet(o) {
  const us0 = o.market === "us" || isUsCode(o.code);
  const S = { market: us0 ? "us" : "kr", engine: o.engine || "scalp", side: o.side || "buy", type: us0 ? "limit" : "market",
    code: o.code || "", qty: o.qty || 1, q: null, real: null, ctx: null, err: "", exch: o.exch || "", name: o.name || "", price: "" };
  const acc = () => (S.ctx ? S.ctx.accounts.find((a) => a.engine === S.engine) : null);
  const draw = () => {
    const US = S.market === "us", buy = S.side === "buy", a = acc(), demo = !US && S.ctx && S.ctx.data_source === "demo";
    const mode = a ? a.mode_label : null, realOrder = mode === "키움 실전", isKiwoom = mode && mode.startsWith("키움");
    const gap = demo && S.q && S.real && Math.abs(S.real.price - S.q.price) / S.real.price > 0.005;
    const usBlocked = US && a && !a.error && !isKiwoom;
    const qtxt = !S.q ? (US ? "기호(예: AAPL)를 넣고 현재가를 확인하세요" : "종목코드를 넣고 현재가를 확인하세요")
      : US ? `${esc(S.q.name)}${S.q.exchange ? " · " + esc(S.q.exchange) : ""} 현재가 <b>${usd(S.q.price)}</b>${S.q.fx ? ` · 약 ${won(S.q.price * S.q.fx)}원` : ""}${S.q.live ? "" : ` <span class="tag">지연 시세</span>`}`
      : `${esc(S.q.name)} 주문 기준가 <b style="color:${gap ? "var(--up)" : "var(--ink)"}">${won(S.q.price)}원</b>${gap ? " (가상 시세)" : ""}`;
    openSheet(US ? "미국 주식 주문" : "직접 주문", `
      ${seg("시장", [["kr", "국내"], ["us", "미국"]], S.market, "on-card")}
      ${seg("계좌", [["scalp", "단타 계좌"], ["dca", "장기투자 계좌"]], S.engine, "on-card")}
      <div class="o-mode ${realOrder ? "real" : ""}">${a ? (a.error ? `<b>주문할 수 없어요</b> ${esc(a.error)}`
        : usBlocked ? `<b>미국 주식은 키움 계좌로만</b> 이 계좌는 '앱 내 모의'라 미국 주문을 할 수 없어요. 설정 → 계좌 연결에서 주문 방식을 '키움으로 주문'으로 바꿔 주세요`
        : realOrder ? `<b>키움 실전 계좌</b> 실제 돈으로 주문이 나가요${US ? ". 달러(또는 원화주문 설정)로 결제돼요" : ""}`
        : mode === "키움 모의투자" ? `<b>키움 모의투자</b> 키움 모의 서버로 주문해요. 실제 돈이 아니에요`
        : `<b>앱 내 모의</b> 앱 안에서만 체결을 흉내 내요. 실제 주문이 아니에요`) : "계좌 확인 중"}</div>
      ${US && a && a.us_cash ? `<div class="small muted">주문 가능 ${usd(a.us_cash.usd_orderable)} · 원화 예수금 ${won(a.us_cash.krw)}원</div>` : ""}
      ${demo ? `<div class="notice warn">지금 시세 출처가 <b>가상 시세</b>예요. 주문 가격이 실제 시세와 달라요${S.real ? `(실제 ${esc(S.real.name)} ${won(S.real.price)}원)` : ""}.
        <button type="button" class="btn sm dark" id="oLive" style="margin-top:8px">키움 시세로 바꾸기</button></div>` : ""}
      ${seg("매수 또는 매도", [["buy", "매수"], ["sell", "매도"]], S.side, "on-card " + (buy ? "buy" : "sell"))}
      <div class="fields" style="grid-template-columns:1fr auto;align-items:end">
        <label class="field">${US ? "종목 기호" : "종목코드"}<input id="oCode" ${US ? `autocapitalize="characters" autocomplete="off" maxlength="10" placeholder="AAPL"` : `inputmode="numeric" maxlength="6" placeholder="005930"`} value="${esc(S.code)}"></label>
        <button class="btn" id="oQ" style="height:50px">현재가</button></div>
      <div class="small muted">${qtxt}</div>
      ${seg("주문 방식", [["limit", "지정가"], ["market", "시장가"]], S.type, "on-card")}
      ${US && S.type === "market" ? `<div class="tiny">미국 시장가는 정규장(한국 시간 23:30~06:00)에만 돼요. 주간거래·프리마켓은 지정가로 주문하세요.</div>` : ""}
      <div class="fields">
        <label class="field">수량<input id="oQty" class="r" inputmode="numeric" value="${esc(S.qty)}"></label>
        <label class="field" ${S.type === "market" ? "hidden" : ""}>가격${US ? "(달러)" : ""}<input id="oPrice" class="r" inputmode="decimal" value="${esc(S.price || (S.q ? (US ? S.q.price.toFixed(2) : won(S.q.price)) : ""))}"></label></div>
      <div style="display:flex;justify-content:space-between;font-size:15px"><span class="muted">예상 금액</span><b id="oAmt">-</b></div>
      <div id="oErr">${S.err ? `<div class="notice warn">${esc(S.err)}</div>` : ""}</div>
      <button class="btn ${buy ? "red" : "blue"} wide" id="oSend" ${a && (a.error || usBlocked) ? "disabled" : ""}>${realOrder ? "실제 " : ""}${buy ? "매수 주문" : "매도 주문"}</button>`, (box) => {
      const keep = () => { S.code = $("#oCode").value.trim(); S.qty = $("#oQty").value; S.price = $("#oPrice").value; };
      bindSeg(box, "시장", (v) => { keep(); if (v !== S.market) { S.market = v; S.code = ""; S.q = null; S.price = ""; S.type = v === "us" ? "limit" : "market"; S.err = ""; } draw(); });
      bindSeg(box, "계좌", (v) => { keep(); S.engine = v; S.err = ""; draw(); if (US && S.code) loadQuote(); });
      bindSeg(box, "매수 또는 매도", (v) => { keep(); S.side = v; draw(); });
      bindSeg(box, "주문 방식", (v) => { keep(); S.type = v; draw(); });
      const calc = () => {
        const p = S.type === "limit" ? Number(String($("#oPrice").value).replace(/,/g, "")) : S.q?.price;
        const n = num($("#oQty").value);
        $("#oAmt").textContent = !p ? "-" : US ? `${usd(p * n)}${S.q?.fx ? ` (약 ${won(p * n * S.q.fx)}원)` : ""}` : won(p * n) + "원";
      };
      $("#oQty").oninput = calc; $("#oPrice").oninput = calc; calc();
      if (US) $("#oCode").oninput = (e) => { e.target.value = e.target.value.toUpperCase(); };
      $("#oQ").onclick = async () => { keep(); S.err = ""; S.price = ""; await loadQuote(); };
      if ($("#oLive")) $("#oLive").onclick = async () => {
        keep();
        await run(() => api("PUT", "/api/settings", { data_source: "kiwoom" }), "시세 출처를 키움 시세로 바꿨어요");
        await loadCtx(); await loadQuote();
      };
      $("#oSend").onclick = async (ev) => {
        keep();
        const btn = ev.currentTarget;
        if (realOrder && !confirmTap(btn, `한 번 더 누르면 실제로 ${buy ? "매수" : "매도"}해요`)) return;
        const price = S.type === "limit" ? (US ? Number(String(S.price).replace(/,/g, "")) : num(S.price)) : null;
        const body = { engine: S.engine, side: S.side, code: US ? S.code.toUpperCase() : S.code, qty: num(S.qty), price, market: S.market,
          exch: S.q?.exch || S.exch || undefined, name: S.q?.name || S.name || undefined };
        btn.disabled = true; btn.textContent = "주문 넣는 중";
        try {
          const r = await api("POST", "/api/order", body);
          toast(r.status === "filled" ? `${r.name} ${body.qty}주 ${buy ? "매수" : "매도"} 체결 ${won(r.fill_price)}원` : `${r.name} ${body.qty}주 ${buy ? "매수" : "매도"} 주문을 넣었어요`);
          closeSheet(); pages.invest.fill?.();
        } catch (e) {
          if (e instanceof NeedConnect) { closeSheet(); location.hash = "#connect"; return; }
          S.err = e.message; draw();
          $("#oErr")?.scrollIntoView({ block: "center", behavior: "smooth" });
        }
      };
    });
  };
  const loadQuote = async () => {
    if (S.market === "us") {
      const sym = String(S.code || "").trim().toUpperCase();
      if (!/^[A-Z][A-Z0-9.\-]{0,9}$/.test(sym)) { S.err = "미국 종목 기호(예: AAPL)를 넣어 주세요"; return draw(); }
      S.code = sym;
      S.q = await GET(`/api/us/quote/${encodeURIComponent(sym)}?engine=${S.engine}`).catch((e) => { S.err = e.message; return null; });
      if (sheet.open) draw();
      return;
    }
    const code = String(S.code || "").replace(/\D/g, "");
    if (code.length !== 6) { S.err = "종목코드 6자리를 넣어 주세요"; return draw(); }
    const [q, real] = await Promise.all([GET(`/api/quote/${code}`).catch((e) => { S.err = e.message; return null; }),
      GET(`/api/market/quote/kr/${code}`).catch(() => null)]);
    S.q = q; S.real = real; if (sheet.open) draw();
  };
  const loadCtx = async () => { S.ctx = await GET("/api/stocks").catch(() => null); if (sheet.open) draw(); };
  draw();
  loadCtx();
  if (S.code) loadQuote();
}

// ---------- 적립 플랜 시트 ----------
function planSheet(e) {
  openSheet(e ? "플랜 고치기" : "플랜 만들기", `
    <div class="fields"><label class="field">종목코드<input id="pCode" maxlength="6" inputmode="numeric" value="${esc(e?.code || "")}" placeholder="360750"></label>
      <label class="field">이름<input id="pName" value="${esc(e?.name || "")}" placeholder="비우면 자동"></label></div>
    <label class="field">매달 금액<input id="pAmt" class="r" inputmode="numeric" value="${e ? won(e.amount) : ""}" placeholder="500,000"></label>
    <div class="fields"><label class="field">매수일<select id="pDay">${Array.from({ length: 28 }, (_, i) => i + 1).map((n) => `<option value="${n}" ${(e?.day || 25) === n ? "selected" : ""}>매월 ${n}일</option>`).join("")}</select></label>
      <label class="field">목표 비중(%)<input id="pW" class="r" inputmode="decimal" value="${e?.target_weight || ""}" placeholder="선택"></label></div>
    <button class="btn dark wide" id="pSave">${e ? "고친 내용 저장" : "플랜 저장"}</button>
    ${e ? `<div class="btns"><button class="btn" id="pRun">지금 매수</button><button class="btn soft" id="pDel">지우기</button></div>` : ""}`, (box) => {
    moneyInput($("#pAmt", box));
    box.onclick = async (ev) => {
      const id = ev.target.id;
      if (id === "pSave") {
        await run(() => api("POST", "/api/dca/plans", { id: e?.id, code: $("#pCode").value, name: $("#pName").value, amount: num($("#pAmt").value), day: num($("#pDay").value), target_weight: num($("#pW").value), enabled: e ? !!e.enabled : true }), "플랜을 저장했어요");
        closeSheet(); pages.invest.fill();
      } else if (id === "pDel") {
        if (!confirmTap(ev.target, "한 번 더 누르면 지워요")) return;
        await run(() => api("DELETE", `/api/dca/plans/${e.id}`), "플랜을 지웠어요"); closeSheet(); pages.invest.fill();
      } else if (id === "pRun") {
        if (!confirmTap(ev.target, "한 번 더 누르면 사요")) return;
        const r = await run(() => api("POST", `/api/dca/plans/${e.id}/run`));
        const x = r.results[0];
        toast(x.status === "done" ? `${x.qty}주, ${won(x.amount)}원 매수했어요` : x.msg, x.status !== "done");
        closeSheet(); pages.invest.fill();
      }
    };
  });
}

// =====================================================================
// 설정
// =====================================================================
pages.settings = {
  every: 5000,
  async refresh(h) {  // 계좌 연결 화면에서는 키움 카드만 몇 초마다 새로(입력란은 그대로)
    const box = $("#kwCard");
    if (h.sub !== "accounts" || !box || box.contains(document.activeElement)) return;
    const d = await kwFetch();
    if (d.configured && $("#kwCard") === box) { box.innerHTML = kwCardHtml(d); bindKwCard(box); }
  },
  theme() {
    const cur = store.get("theme") || "navy";
    const curA = store.get("accent") || THEMES[cur].accent;
    const draw = (tid, aid) => {
      view.innerHTML = `
        <section class="card"><h2>바탕 색</h2>
          <div class="small muted">상단·하단 메뉴와 강조 카드에 쓰이는 색이에요. 누르면 바로 바뀌어요.</div>
          <div class="theme-grid">${Object.entries(THEMES).map(([id, t]) => {
            const a = ACCENTS[id === tid ? aid : t.accent][1];
            return `<button type="button" class="theme-opt" data-theme="${id}" aria-pressed="${id === tid}">
              <span class="theme-prev" style="background:${t.bg}" aria-hidden="true">
                <span class="tp-band" style="background:${t.deep};${t.light ? "border-bottom:1px solid #E1E5EE" : ""}"></span>
                <span class="tp-card"></span><span class="tp-hl" style="background:${a}"></span>
                <span class="tp-bar" style="background:${t.light ? "#FFFFFF;border-top:1px solid #E1E5EE" : t.deep}"></span></span>
              <span class="theme-name">${esc(t.name)}${id === tid ? `<span class="tag y">사용 중</span>` : ""}</span></button>`;
          }).join("")}</div>
        </section>
        <section class="card"><h2>포인트 색</h2>
          <div class="small muted">금액 강조, 선택된 탭, ＋ 버튼에 쓰이는 색이에요.</div>
          <div class="swatches">${Object.entries(ACCENTS).map(([id, [name, c]]) => `<button type="button" class="swatch" style="background:${c}" data-accent="${id}" aria-pressed="${id === aid}" aria-label="${name}"></button>`).join("")}</div>
          <div class="tiny">지금: ${esc(THEMES[tid].name)} 바탕 · ${esc(ACCENTS[aid][0])} 포인트</div>
        </section>
        <section class="card"><h2>미리 보기</h2>
          <div class="hero" style="font-size:20px">하루 <span class="hl">63,700원</span>씩 쓸 수 있어요</div>
          <div class="btns"><button type="button" class="btn dark">저장</button><button type="button" class="btn">취소</button></div>
          <div class="card dark" style="padding:16px"><div class="small muted">단타 실현손익</div><div class="mid">+38,400원</div></div>
        </section>
        <button class="btn soft wide" data-reset-theme>기본 색으로 되돌리기</button>
        <div class="tiny" style="text-align:center">색상은 이 기기에만 저장돼요. 다른 기기는 따로 고를 수 있어요.</div>`;
    };
    let tid = cur, aid = curA;
    draw(tid, aid);
    view.onclick = (e) => {
      const t = e.target.closest("[data-theme]"), a = e.target.closest("[data-accent]"), r = e.target.closest("[data-reset-theme]");
      if (t) { tid = t.dataset.theme; aid = THEMES[tid].accent; }
      else if (a) aid = a.dataset.accent;
      else if (r) { tid = "navy"; aid = "yellow"; }
      else return;
      applyTheme(tid, aid);
      draw(tid, aid);
    };
  },
  async mount(h) {
    setTop("설정", { menu: "settings", sub: h.sub });
    if (h.sub === "theme") return this.theme();  // 색상은 서버 연결 없이도 바꿀 수 있게
    if (h.sub === "kiwoom") h.sub = "accounts";
    const sub = h.sub;
    let s;
    try { s = await GET("/api/settings"); }
    catch (e) {
      if (sub !== "accounts" || e instanceof NeedConnect || !kwCache.load()) throw e;
      view.innerHTML = `<div class="notice warn">${esc(e.message)}</div><section class="card kw-card" id="kwCard">${kwCardHtml({ ...kwCache.load(), ok: false, offline: true })}</section>`;
      bindKwCard($("#kwCard"));
      return;
    }
    const ping = await GET("/api/ping").catch(() => ({}));
    const info = ping.local ? await GET("/api/connect-info").catch(() => null) : null;
    const up = await GET("/api/update").catch(() => ({ available: false, message: "" }));
    const acct = (k, label) => {
      const kw = s.kiwoom[k];
      return `<details class="card"><summary>${label} 계좌 <span class="tiny" style="margin-left:auto;margin-right:12px">${s.trade_mode[k] === "kiwoom" ? (kw.mock ? "키움 모의투자" : "키움 실전") : "앱 내 모의"}</span></summary>
        ${seg(label + " 주문 방식", [["paper", "앱 내 모의"], ["kiwoom", "키움으로 주문"]], s.trade_mode[k], "on-card")}
        <div class="tiny">앱 내 모의는 시세만 받아 앱 안에서 체결을 흉내 내요. 키움으로 주문하면 아래 키의 계좌로 주문이 나가요.</div>
        <label class="field">앱키<input data-kw="${k}" data-k="appkey" value="${esc(kw.appkey)}" autocomplete="off" autocapitalize="off"></label>
        <label class="field">시크릿키<input data-kw="${k}" data-k="secretkey" type="password" value="${esc(kw.secretkey)}" autocomplete="off"></label>
        <label class="check"><input type="checkbox" data-kw="${k}" data-k="mock" ${kw.mock ? "checked" : ""}> 키움 모의투자 서버 사용</label>
        <label class="check"><input type="checkbox" data-real="${k}" ${s.real_orders_allowed[k] ? "checked" : ""}> 실전 서버 주문 허용</label>
        <div class="tiny">모의투자 서버를 끄고 실전 주문도 허용해야 실제 돈으로 주문해요.</div>
        <button class="btn sm" data-test="${k}">연결 확인</button>
        <label class="field">앱 내 모의 시작 금액<input class="r" data-cash="${k}" inputmode="numeric" value="${won(s.paper_start_cash[k])}"></label>
        <button class="btn sm soft" data-reset="${k}">앱 내 모의 계좌 초기화</button>
      </details>`;
    };
    const connectCard = `
      <section class="card">
        <div class="card-head"><h2>연결</h2><span class="tag ${ping.ok ? "g" : "warn"}">${ping.ok ? "연결됨" : "연결 안 됨"}</span></div>
        ${ON_PAGES ? `<div class="small muted">서버 ${esc(store.get("server") || "-")}</div>
          <div class="btns"><a class="btn sm" href="#connect">연결 바꾸기</a><button class="btn sm soft" id="disc">연결 끊기</button></div>`
        : info ? `<div class="small muted">휴대폰에서 이 서버에 접속할 때 쓰는 정보예요. 이 화면은 노트북에서만 보여요.</div>
          <label class="field">바깥 주소(Tailscale 등)<input id="pubUrl" inputmode="url" autocapitalize="off" placeholder="https://노트북이름.tailnet.ts.net" value="${esc(info.public_url)}"></label>
          <div class="field">접속 토큰<div class="code" id="tokTxt">${esc(info.token)}</div></div>
          <div class="field">휴대폰 연결 링크<div class="code" id="linkTxt"></div><span class="hint">이 링크를 휴대폰으로 보내 한 번 열면 주소와 토큰이 저장돼요</span></div>
          <div class="btns"><button class="btn sm" id="copyLink">링크 복사</button><button class="btn sm soft" id="rotate">토큰 새로 만들기</button></div>`
        : `<div class="small muted">이 서버에 직접 연결돼 있어요.</div>`}
      </section>`;
    const con = sub === "accounts" ? await GET("/api/connections").catch(() => null) : null;
    const kw = con ? con.kiwoom : {};
    const kwState = !kw.configured ? ["연결 안 됨", "warn"] : kw.ok ? ["연결됨", "g"] : ["확인 필요", "warn"];
    const kwSnap = con && con.kiwoom.configured ? con.kiwoom : null;
    if (kwSnap) kwCache.save(kwSnap);
    const accountsHtml = con ? `
      <section class="card kw-card" id="kwCard">${kwCardHtml(kwSnap || (kwCache.load() && { ...kwCache.load(), ok: false }) || { configured: false })}</section>
      <section class="card">
        <div class="card-head"><h2>연결된 계좌</h2><span class="tiny">${(con.banks.length + (kw.configured ? 1 : 0))}개</span></div>
        <div class="list">
          <a class="item" href="#invest/account" style="text-decoration:none">
            <span class="ico y">키움</span>
            <span class="main"><b>키움증권</b><span class="sub">조회 전용 · ${kw.configured ? `${esc(kw.server || "")} · ${kw.updated_at ? esc(kw.updated_at.slice(11, 16)) + " 갱신" : ""}` : "아래에서 앱키를 넣어 연결"}</span></span>
            <span class="end">${kw.total != null ? `<b>${won(kw.total)}</b>` : ""}<span class="tag ${kwState[1]}">${kwState[0]}</span></span></a>
          ${con.banks.map((b) => `<button type="button" class="item" data-bk="${b.id}">
            <span class="ico">${esc(bankLabel(b.bank).slice(0, 2))}</span>
            <span class="main"><b>${esc(b.name)}</b><span class="sub">${esc(bankLabel(b.bank))} ${esc(maskAcct(b.acct_no))} · ${b.last_alert ? `${esc(b.last_alert.received_at.slice(5, 16).replace("T", " "))} ${b.last_alert.action === "income" ? "입금" : "출금"} 알림` : "알림 기다리는 중"}</span></span>
            <span class="end"><b>${won(b.balance)}</b><span class="tag ${b.last_alert ? "g" : ""}">${b.last_alert ? "알림 연결" : "대기"}</span></span></button>`).join("")}
        </div>
        <button class="btn wide" id="bkAdd">＋ 은행 계좌 추가</button>
      </section>
      <section class="card"><h2>은행 계좌는 이렇게 연결돼요</h2>
        <div class="small muted" style="line-height:1.7">은행은 개인이 직접 쓸 수 있는 공개 연동(API)이 없어서, <b>입출금 알림 문자</b>로 받은 금액과 잔액을 읽어 이 계좌들에 반영해요.<br>
          <b>1.</b> 은행 앱에서 입출금 알림을 <b>문자(SMS)</b>로 받도록 신청해요. 하나은행은 하나원큐, NH농협은 NH올원뱅크·콕뱅크에서 신청할 수 있고, 은행에 따라 유료일 수 있어요.<br>
          <b>2.</b> 위에서 계좌를 추가하고 계좌번호를 넣어요(문자 속 계좌번호 끝자리로 찾아요).<br>
          <b>3.</b> 아래 단축어 자동화를 한 번 설정해요.<br>
          지난 알림은 가계부 → 자동에서 카톡 내보내기 파일이나 복사한 문자로 넣을 수 있어요.</div>
        <div class="tiny">마지막으로 받은 알림: ${con.inbox.last_at ? esc(con.inbox.last_at.slice(0, 16).replace("T", " ")) : "아직 없음"} · 확인 필요 ${con.inbox.pending}건</div>
      </section>
      ${await shortcutGuide()}
      <section class="card" id="kvSec"><h2>키움증권(조회 전용) <span class="tag ${kwState[1]}" style="vertical-align:2px">${kwState[0]}</span></h2>
        <div class="small muted">보유액·예수금·종목을 보기만 해요. 이 키로는 주문 기능이 아예 꺼져 있어요.<br>
          <b>다른 계좌로 바꾸려면</b> 키움 REST API 홈페이지에서 그 계좌의 앱키·시크릿키를 받아 아래에 덮어쓰고 연결 확인을 누르세요. 앱키는 계좌마다 따로 발급돼요.</div>
        ${kw.error ? `<div class="notice warn">${esc(kw.error)}</div>` : ""}
        <label class="field">앱키<input data-kv="appkey" value="${esc(s.kiwoom_view.appkey)}" autocomplete="off" autocapitalize="off"></label>
        <label class="field">시크릿키<input data-kv="secretkey" type="password" value="${esc(s.kiwoom_view.secretkey)}" autocomplete="off"></label>
        <label class="check"><input type="checkbox" data-kv="mock" ${s.kiwoom_view.mock ? "checked" : ""}> 모의투자 계좌 키예요</label>
        <button class="btn sm" id="kvTest">저장하고 연결 확인</button>
      </section>
      <div class="sec-title">자동매매 주문 계좌</div>
      ${acct("scalp", "단타")}
      ${acct("dca", "장기투자")}
      <div id="setErr"></div>
      <button class="btn dark wide" id="saveSet">주문 계좌 설정 저장</button>` : `<div class="notice warn">서버에 연결되지 않아 계좌 정보를 불러오지 못했어요.</div>`;
    const tradingHtml = `
      <section class="card"><h2>시세</h2>
        ${seg("시세 출처", [["demo", "가상 시세"], ["kiwoom", "키움 시세"]], s.data_source, "on-card")}
        <div class="tiny">가상 시세는 장 시간이 아니어도 시험할 수 있게 빠르게 흐르는 무작위 가격이에요. 실제 검증은 <b>키움 시세 + 앱 내 모의</b>로 하세요. 키움 시세는 계좌 연결의 주문 계좌 키를 써요.</div>
        <label class="field">가상 시세 속도(1분 = 몇 초)<input class="r" id="demoSpeed" inputmode="decimal" value="${s.demo_speed}"></label>
      </section>
      <section class="card"><h2>거래 비용</h2>
        <div class="fields"><label class="field">수수료(%, 한쪽)<input class="r" id="cCom" inputmode="decimal" value="${+(s.costs.commission * 100).toFixed(4)}"></label>
          <label class="field">매도 거래세(%)<input class="r" id="cTax" inputmode="decimal" value="${+(s.costs.tax * 100).toFixed(4)}"></label>
          <label class="field">슬리피지(호가 수)<input class="r" id="cSlip" inputmode="numeric" value="${s.costs.slippage_ticks}"></label></div>
        <div class="tiny">본인 증권사 수수료와 올해 거래세율로 맞춰 주세요. 단타 손익에 가장 크게 영향을 줘요.</div></section>
      <section class="card"><h2>적립 매수 시간·휴장일</h2>
        <div class="fields"><label class="field">매수 시작<input id="dwS" value="${esc(s.dca_window.start)}"></label><label class="field">매수 마감<input id="dwE" value="${esc(s.dca_window.end)}"></label></div>
        <label class="field">휴장일(YYYY-MM-DD, 줄마다)<textarea id="hol" rows="4">${esc(s.holidays.join("\n"))}</textarea></label>
        <div class="tiny">주말은 자동으로 빼요.</div></section>
      <section class="card"><h2>휴대폰 알림</h2>
        <label class="field">ntfy 토픽<input id="ntfy" value="${esc(s.ntfy_topic)}" autocapitalize="off" placeholder="비우면 알림 안 보냄"></label>
        <div class="tiny">ntfy 앱에서 같은 토픽을 구독하면 매수·매도·적립 결과를 받아요.</div></section>
      <div id="setErr"></div>
      <button class="btn dark wide" id="saveSet">설정 저장</button>`;
    const appHtml = `
      <section class="card" id="upCard">${updateCard(up)}</section>
      <section class="card"><h2>버전</h2>
        <div style="display:flex;justify-content:space-between;font-size:14px"><span>앱</span><b>v${esc(ping.version || "-")}</b></div>
        <div style="display:flex;justify-content:space-between;font-size:14px"><span class="muted">화면 빌드</span><span class="muted">${esc(UI_VERSION)}</span></div>
        <div style="display:flex;justify-content:space-between;font-size:14px"><span class="muted">서버 빌드</span><span class="muted">${esc(ping.build || "-")}</span></div>
      </section>`;
    view.innerHTML = sub === "accounts" ? accountsHtml : sub === "trading" ? tradingHtml : sub === "app" ? appHtml : connectCard;
    if ($("#kwCard")) bindKwCard($("#kwCard"));
    $("#bkAdd")?.addEventListener("click", () => bankSheet(null, route));
    $$("[data-bk]").forEach((el) => el.addEventListener("click", () => bankSheet(con.banks.find((b) => b.id == el.dataset.bk), route)));
    const errs = (e) => { $("#setErr").innerHTML = Object.entries(e || {}).map(([k, v]) => `<div class="notice warn">${esc({ scalp: "단타 계좌", dca: "장기투자 계좌", feed: "시세" }[k] || k)}: ${esc(v)}</div>`).join(""); };
    // 지금 화면에 있는 입력란만 모아서 저장한다(다른 2뎁스 화면의 값은 건드리지 않음)
    const collect = () => {
      const out = {};
      if ($("[data-kw]")) {
        const kiwoom = { scalp: {}, dca: {} };
        $$("[data-kw]").forEach((i) => { kiwoom[i.dataset.kw][i.dataset.k] = i.type === "checkbox" ? i.checked : i.value.trim(); });
        const real = {}; $$("[data-real]").forEach((i) => (real[i.dataset.real] = i.checked));
        const cash = {}; $$("[data-cash]").forEach((i) => (cash[i.dataset.cash] = num(i.value)));
        out.kiwoom = kiwoom; out.real_orders_allowed = real; out.paper_start_cash = cash;
        out.trade_mode = { scalp: segVal(view, "단타 주문 방식"), dca: segVal(view, "장기투자 주문 방식") };
      }
      if ($("[data-kv]")) { const kv = {}; $$("[data-kv]").forEach((i) => (kv[i.dataset.kv] = i.type === "checkbox" ? i.checked : i.value.trim())); out.kiwoom_view = kv; }
      if (segVal(view, "시세 출처")) { out.data_source = segVal(view, "시세 출처"); out.demo_speed = num($("#demoSpeed").value) || 2; }
      if ($("#cCom")) out.costs = { commission: num($("#cCom").value) / 100, tax: num($("#cTax").value) / 100, slippage_ticks: num($("#cSlip").value) };
      if ($("#dwS")) {
        out.dca_window = { start: $("#dwS").value.trim(), end: $("#dwE").value.trim() };
        out.holidays = $("#hol").value.split(/\s+/).filter((x) => /^\d{4}-\d\d-\d\d$/.test(x));
      }
      if ($("#ntfy")) out.ntfy_topic = $("#ntfy").value.trim();
      if ($("#pubUrl")) out.public_url = $("#pubUrl").value.trim().replace(/\/+$/, "");
      return out;
    };
    ["시세 출처", "단타 주문 방식", "장기투자 주문 방식"].forEach((n) => bindSeg(view, n, () => {}));
    $$("[data-cash]").forEach(moneyInput);
    const link = () => {
      if (!info) return "";
      const pub = ($("#pubUrl")?.value || "").trim().replace(/\/+$/, "");
      return pub ? `${info.pages_url}#connect?server=${encodeURIComponent(pub)}&token=${encodeURIComponent(info.token)}` : "";
    };
    const showLink = () => { if ($("#linkTxt")) $("#linkTxt").textContent = link() || "바깥 주소를 넣으면 만들어져요"; };
    showLink();
    $("#pubUrl")?.addEventListener("input", showLink);
    $("#copyLink")?.addEventListener("click", async () => {
      const l = link();
      if (!l) return toast("바깥 주소를 먼저 넣어 주세요", true);
      await run(() => api("PUT", "/api/settings", collect()));
      try { await navigator.clipboard.writeText(l); toast("링크를 복사했어요. 휴대폰으로 보내 열어 주세요"); } catch (e) { toast("복사하지 못했어요. 링크를 길게 눌러 복사해 주세요", true); }
    });
    $("#rotate")?.addEventListener("click", async (e) => {
      if (!confirmTap(e.target, "한 번 더 누르면 바꿔요(휴대폰 다시 연결 필요)")) return;
      const r = await run(() => api("POST", "/api/connect-info/rotate"), "토큰을 바꿨어요");
      info.token = r.token; $("#tokTxt").textContent = r.token; showLink();
    });
    $("#disc")?.addEventListener("click", (e) => {
      if (!confirmTap(e.target, "한 번 더 누르면 끊어요")) return;
      store.set("server", ""); store.set("token", ""); location.hash = "#connect";
    });
    const bindUp = () => {
      $("#upAuto")?.addEventListener("click", async (e) => {
        const on = e.currentTarget.getAttribute("aria-checked") !== "true";
        await run(() => api("PUT", "/api/update/auto", { auto: on }), on ? "자동 업데이트를 켰어요" : "자동 업데이트를 껐어요");
        $("#upCard").innerHTML = updateCard(await GET("/api/update")); bindUp();
      });
      $("#upCheck")?.addEventListener("click", async () => {
        const r = await run(() => api("POST", "/api/update/check", {}));
        $("#upCard").innerHTML = updateCard(r); bindUp();
      });
      $("#upApply")?.addEventListener("click", async () => {
        const r = await run(() => api("POST", "/api/update/check", { apply: true }));
        toast(r.msg, !r.applied);
        if (r.applied) setTimeout(route, 8000);
        $("#upCard").innerHTML = updateCard(r); bindUp();
      });
    };
    bindUp();
    if ($("#kvTest")) $("#kvTest").onclick = async () => {
      await run(() => api("PUT", "/api/settings", collect()));
      store.set("kwLast", "");  // 다른 계좌로 바꿨을 수 있으니 휴대폰에 남긴 예전 값은 지운다
      $("#kwCard").innerHTML = kwCardHtml({ configured: true, error: "새 키로 연결하는 중" }); 
      const r = await run(() => api("POST", "/api/myaccount/test"));
      toast(`${r.server} 계좌 연결 성공. 총자산 ${won(r.total)}원, 보유 ${r.holdings.length}종목`);
      kwCache.save(r); $("#kwCard").innerHTML = kwCardHtml(r); bindKwCard($("#kwCard"));
    };
    if ($("#saveSet")) $("#saveSet").onclick = async () => { const r = await run(() => api("PUT", "/api/settings", collect()), "설정을 저장했어요"); errs(r.errors); };
    view.onclick = async (e) => {
      const t = e.target.closest("[data-test]"), rs = e.target.closest("[data-reset]");
      if (t) {
        await run(() => api("PUT", "/api/settings", collect()));
        const r = await run(() => api("POST", "/api/kiwoom/test", { engine: t.dataset.test }));
        toast(`${r.server} 서버 연결 성공. 주문 가능 ${won(r.cash)}원`);
      }
      if (rs) {
        if (!confirmTap(rs, "한 번 더 누르면 초기화해요")) return;
        await run(() => api("PUT", "/api/settings", collect()));
        await run(() => api("POST", "/api/paper/reset", { engine: rs.dataset.reset }), "초기화했어요");
        route();
      }
    };
  },
};

function updateCard(u) {
  if (!u.available) return `<h2>업데이트</h2><div class="small muted">${esc(u.message || "자동 업데이트를 쓸 수 없어요")}</div>`;
  const when = u.checked_at ? u.checked_at.slice(5, 16).replace("T", " ") : "-";
  return `<div class="card-head"><div><h2>업데이트</h2><div class="tiny">버전 v${esc(u.version)} · 확인 ${esc(when)}</div></div>
      <button class="switch" role="switch" id="upAuto" aria-checked="${!!u.auto}" aria-label="GitHub 자동 업데이트"><span></span></button></div>
    <div class="small muted">${esc(u.message || "")}${u.behind && u.latest_subject ? `<br>새 버전: ${esc(u.latest_subject)}` : ""}</div>
    <div class="tiny">켜 두면 ${u.interval_min}분마다 GitHub를 확인해, 종목을 들고 있지 않을 때 노트북 서버가 스스로 새 버전으로 바뀌어요. 가계부 데이터는 그대로예요.</div>
    <div class="btns"><button class="btn sm" id="upCheck">지금 확인</button>${u.behind ? `<button class="btn sm dark" id="upApply">지금 업데이트</button>` : ""}</div>`;
}

// ---------- 새 버전 알림 ----------
// 화면(GitHub Pages)과 노트북 서버가 둘 다 새 버전으로 바뀐 뒤에만 알린다.
// 그래야 [지금 반영]을 누르는 즉시 새 버전이 완전히 보인다.
const upd = { server: null, shown: false, pendingTimer: null };
async function checkNewVersion() {
  if (upd.shown || document.hidden) return;
  let v = null, p = null;
  try {
    const r = await fetch(`version.json?t=${Date.now()}`, { cache: "no-store" });
    if (r.ok) v = await r.json();
  } catch (e) { /* 오프라인 */ }
  try { p = await GET("/api/ping"); } catch (e) { /* 서버 연결 안 됨 */ }
  if (p && !upd.server) upd.server = p.build;
  const uiNew = v && v.build && v.build !== UI_VERSION && UI_VERSION !== "0";
  const serverReady = !p || !v || p.version === v.version;  // 서버에 못 붙으면 화면만 기준
  clearTimeout(upd.pendingTimer);
  if (uiNew && !serverReady) {
    // 화면은 올라왔지만 노트북이 아직 받는 중 → 30초 뒤 다시 확인
    upd.pendingTimer = setTimeout(checkNewVersion, 30 * 1000);
    return;
  }
  if (uiNew) return showUpdateDialog({ kind: "ui", from: p ? p.version : "", to: v.version, note: v.note });
  if (p && upd.server && p.build && p.build !== upd.server) {
    return showUpdateDialog({ kind: "server", from: "", to: p.version, note: "노트북 서버가 새 버전으로 바뀌었어요", build: p.build });
  }
}
function showUpdateDialog(u) {
  if (sheet.open) { setTimeout(checkNewVersion, 5000); return; }
  upd.shown = true;
  const dlg = $("#updDlg");
  const cur = (document.querySelector(".top .ver") || {}).textContent || "";
  $("#updBody").innerHTML = `<div class="upd-ver">${cur && cur !== "v" + u.to ? `<span>${esc(cur)}</span><b aria-hidden="true">→</b>` : ""}<strong>v${esc(u.to)}</strong></div>
    ${u.note ? `<div class="upd-row"><span>${esc(u.note)}</span></div>` : ""}`;
  dlg.showModal();
  $("#updOk").disabled = false; $("#updOk").textContent = "확인";
  $("#updOk").onclick = async () => {
    $("#updOk").disabled = true; $("#updOk").textContent = "반영하는 중";
    if (u.kind === "ui") {
      try { const reg = await navigator.serviceWorker?.getRegistration(); await reg?.update(); } catch (e) { /* 무시 */ }
      location.reload();
    } else {
      upd.server = u.build;
      dlg.close(); upd.shown = false; route();
    }
  };
  $("#updLater").onclick = () => { dlg.close(); setTimeout(() => (upd.shown = false), 10 * 60 * 1000); };
}
setInterval(checkNewVersion, 60 * 1000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) checkNewVersion(); });
setTimeout(checkNewVersion, 4000);

// ---------- 시작 ----------
if ("serviceWorker" in navigator && location.protocol === "https:") {
  navigator.serviceWorker.register("sw.js", { updateViaCache: "none" }).then((reg) => reg.update()).catch(() => {});
}
if (ON_PAGES && !store.get("server") && !location.hash.startsWith("#connect")) location.hash = "#connect";
route();
