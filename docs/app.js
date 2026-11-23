"use strict";
/* moa — 휴대폰 화면. 외부 라이브러리 없이 동작한다.
   GitHub Pages에서 열면 설정한 서버(노트북)로, 서버가 직접 준 화면이면 같은 주소로 요청한다. */

const UI_VERSION = "261124.0828";
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
const view = $("#view");

// ---------- 연결 ----------
const store = {
  get(k) { try { return localStorage.getItem("moa." + k) || ""; } catch (e) { return ""; } },
  set(k, v) { try { v ? localStorage.setItem("moa." + k, v) : localStorage.removeItem("moa." + k); } catch (e) { /* 저장 불가 */ } },
};
// 최근에 찾아서 고른 종목(이 기기에만 저장, 시장마다 12개). 종목 검색·주문·적립·배당의 종목 고르기가 함께 쓴다
const recentStock = {
  all() { try { const d = JSON.parse(store.get("recentStocks") || "{}"); return d && typeof d === "object" ? d : {}; } catch (e) { return {}; } },
  list(m) { return (this.all()[m] || []).filter((x) => x && x.sym); },
  add(m, sym, name, exchange) {
    if (!sym) return;
    const d = this.all(), sy = String(sym);
    d[m] = [{ sym: sy, name: name || sy, exchange: exchange || "" }, ...(d[m] || []).filter((x) => x.sym !== sy)].slice(0, 12);
    store.set("recentStocks", JSON.stringify(d));
  },
  remove(m, sym) { const d = this.all(); d[m] = (d[m] || []).filter((x) => x.sym !== sym); store.set("recentStocks", JSON.stringify(d)); },
  clear(m) { const d = this.all(); delete d[m]; store.set("recentStocks", Object.keys(d).length ? JSON.stringify(d) : ""); },
};
const FONT_SIZES = [["s", "작게", 1], ["m", "보통", 1.15], ["l", "크게", 1.3]];
function applyFontSize(id) {
  const f = FONT_SIZES.find((x) => x[0] === id) || FONT_SIZES[0];
  store.set("fontSize", f[0] === "s" ? "" : f[0]);
  document.documentElement.style.zoom = f[2] === 1 ? "" : String(f[2]);
  document.documentElement.style.setProperty("--zoom", String(f[2]));
}
const memo = {  // 마지막으로 받은 화면 자료(앱을 다시 열어도 바로 보이게)
  get(k) { try { return JSON.parse(localStorage.getItem("moa.m." + k) || "null"); } catch (e) { return null; } },
  set(k, v) { try { localStorage.setItem("moa.m." + k, JSON.stringify(v)); } catch (e) { /* 저장 불가 */ } },
};
const ON_PAGES = location.hostname.endsWith("github.io") || location.protocol === "file:" || store.get("remote") === "1";
class NeedConnect extends Error {}
const apiBase = () => (ON_PAGES ? store.get("server").replace(/\/+$/, "") : "");

// ---------- 잠금(앱 비밀번호) ----------
// 접속 토큰은 '이 기기가 서버에 붙을 열쇠', 비밀번호는 '지금 쓰는 사람이 나라는 확인'.
// 비밀번호를 맞히면 서버가 열림 값(세션)을 주고, 그 값이 있어야 데이터를 볼 수 있다.
class Locked extends Error {}
const sess = {
  get() { try { return sessionStorage.getItem("moa.sess") || localStorage.getItem("moa.sess") || ""; } catch (e) { return ""; } },
  set(v, keep) {
    try {
      sessionStorage.removeItem("moa.sess"); localStorage.removeItem("moa.sess");
      if (v) (keep ? localStorage : sessionStorage).setItem("moa.sess", v);
    } catch (e) { /* 저장 불가 */ }
  },
};
function authHeaders(h = {}) {
  const tok = store.get("token"), sid = sess.get();
  if (tok) h.Authorization = "Bearer " + tok;
  if (sid) h["X-Moa-Session"] = sid;
  if (apiBase().includes("ngrok")) h["ngrok-skip-browser-warning"] = "1";
  return h;
}
// 잠기면 기기에 남겨 둔 화면 자료(잔액·내역 사본)를 지운다
function purgeCache() {
  try { Object.keys(localStorage).forEach((k) => { if (k.startsWith("moa.m.") || k === "moa.kwLast") localStorage.removeItem(k); }); } catch (e) { /* 무시 */ }
}
const OPEN_API = (u) => u.startsWith("/api/auth/") || u === "/api/ping";

async function api(method, url, body, isForm) {
  if (ON_PAGES && !store.get("server")) throw new NeedConnect("서버 주소를 먼저 넣어 주세요");
  if (lock.on && !OPEN_API(url)) throw new Locked("잠겨 있어요");
  const opt = { method, headers: authHeaders() };
  if (body !== undefined) {
    if (isForm) opt.body = body;
    else { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  }
  let res;
  try { res = await fetch(apiBase() + url, opt); }
  catch (e) { throw new Error(ON_PAGES ? "서버에 연결하지 못했어요. 노트북의 moa와 연결 앱(Tailscale 등)이 켜져 있는지 확인해 주세요" : "서버에 연결하지 못했어요. moa 실행 창이 켜져 있는지 확인해 주세요"); }
  const data = await res.json().catch(() => ({}));
  if (res.status === 401) {
    if (data.code === "locked" || data.code === "setup") { lock.show(data.code === "setup" ? "setup" : "unlock"); throw new Locked(data.error || "잠겨 있어요"); }
    throw new NeedConnect(data.error || "접속 토큰이 맞지 않아요");
  }
  if ((res.status === 404 && data.error === "없는 주소예요") || (res.status === 405 && method === "GET")) {   // 405: 예전 서버에는 이 주소의 조회가 없다
    // 화면은 새 버전인데 노트북 서버가 아직 예전 버전일 때: 서버가 이 기능의 주소를 모른다
    const err = new Error("노트북 서버가 아직 새 버전을 받지 못했어요"); err.code = "old_server"; throw err;
  }
  if (!res.ok) { const err = new Error(data.error || `요청이 실패했어요 (${res.status})`); err.wait = data.wait || 0; err.code = data.code || ""; throw err; }
  return data;
}
// 서버가 화면보다 예전 버전일 때 보여 주는 안내. 서버가 새 버전이 되면 알아서 다시 그린다(retry).
let oldSrvT = 0;
function oldServerBox(el, retry) {
  if (!el) return;
  clearInterval(oldSrvT);
  el.innerHTML = `<section class="card old-srv"><h2>노트북 서버를 업데이트하고 있어요</h2>
    <div class="tiny" id="osMsg">이 화면은 새 버전인데, 노트북의 우석World가 아직 예전 버전이에요. 노트북은 5분마다 새 버전을 확인해 스스로 받아요. 받으면 이 화면이 자동으로 열려요.</div>
    <div class="ai-status"><span class="ai-dot"></span><span id="osState">서버 버전을 확인하는 중</span></div>
    <button type="button" class="btn dark wide" id="osNow">지금 바로 업데이트</button></section>`;
  let first = "", busy = false;
  const done = () => { clearInterval(oldSrvT); retry(); };
  const tick = async () => {
    if (!el.isConnected || !el.querySelector("#osState")) return clearInterval(oldSrvT);
    try {
      const p = await GET("/api/ping");
      if (!first) first = p.build || p.version;
      else if ((p.build || p.version) !== first) return done();
      if (!busy) el.querySelector("#osState").textContent = `지금 서버 v${p.version} · 새 버전을 기다리는 중`;
    } catch (e) { if (el.querySelector("#osState")) el.querySelector("#osState").textContent = "서버가 다시 시작하는 중"; }
  };
  tick(); oldSrvT = setInterval(tick, 4000);
  el.querySelector("#osNow").onclick = async (e) => {
    const b = e.currentTarget; b.disabled = true; busy = true;
    el.querySelector("#osState").textContent = "새 버전을 받는 중";
    try {
      const r = await api("POST", "/api/update/check", { apply: true });
      if (r.applied) el.querySelector("#osState").textContent = "받았어요. 서버가 다시 시작하는 중(10초쯤)";
      else { busy = false; b.disabled = false; el.querySelector("#osMsg").textContent = r.msg || "지금은 업데이트할 수 없어요. 잠시 뒤 다시 눌러 주세요."; }
    } catch (err) { busy = false; b.disabled = false; el.querySelector("#osMsg").textContent = err.message; }
  };
}
const GET = (u) => api("GET", u);

const LOCK_LABEL = { 5: "5분", 30: "30분", 240: "4시간", 1440: "하루", 10080: "일주일" };
const lock = {
  on: false, mode: "", dlg: null, after: 30, required: false, hiddenAt: 0, waitT: null,
  el() {
    if (this.dlg) return this.dlg;
    const d = document.createElement("dialog");
    d.className = "lock"; d.id = "lockDlg"; d.setAttribute("aria-labelledby", "lockTitle");
    d.addEventListener("cancel", (e) => e.preventDefault());                      // Esc로 못 닫게
    d.addEventListener("close", () => { if (this.on) try { d.showModal(); } catch (e) { /* 이미 열림 */ } });
    document.body.appendChild(d);
    return (this.dlg = d);
  },
  show(mode, msg = "", wait = 0) {
    if (this.on && this.mode === mode && mode !== "offline") return;              // 여러 요청이 한꺼번에 잠김을 알려도 입력 중인 칸을 지우지 않는다
    const d = this.el();
    if (!this.on) { clearInterval(timer); closeSheet(); $(".fab")?.remove(); view.innerHTML = ""; }
    this.on = true; this.mode = mode;
    if (mode === "unlock" || mode === "setup") { sess.set(""); purgeCache(); }
    if (mode === "check") purgeCache();
    const eye = `<button type="button" class="lk-eye" id="lkEye" aria-label="비밀번호 보기" aria-pressed="false"><svg viewBox="0 0 24 24" width="19" height="19" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg></button>`;
    const user = `<input class="lk-user" type="text" name="username" autocomplete="username" value="가계부S" readonly tabindex="-1" aria-hidden="true">`;
    const body = {
      check: `<p class="lk-sub" id="lkMsg">잠금 상태를 확인하는 중</p><div class="lk-spin" aria-hidden="true"></div>`,
      offline: `<p class="lk-sub">${esc(msg || "서버에 연결하지 못했어요")}</p>
        <button type="button" class="lk-go" id="lkRetry">다시 연결</button>
        <a class="lk-link" href="#connect" id="lkConn">서버 연결 설정 열기</a>`,
      unlock: `<p class="lk-sub">비밀번호를 넣으면 열려요</p>${user}
        <label class="lk-field"><span class="lk-lab">비밀번호</span><span class="lk-box"><input id="lkPw" name="password" type="password" autocomplete="current-password" enterkeyhint="go" autocapitalize="off" autocorrect="off" spellcheck="false" maxlength="128" required>${eye}</span></label>
        <div class="lk-err" id="lkErr" role="alert"></div>
        <button type="submit" class="lk-go" id="lkGo" data-label="열기">열기</button>
        <label class="lk-keep"><input type="checkbox" id="lkKeep"${store.get("keep") ? " checked" : ""}><span>이 기기에서는 자동 잠금 시간(${LOCK_LABEL[this.after] || "30분"})까지 다시 묻지 않기</span></label>
        <details class="lk-help"><summary>비밀번호를 잊었어요</summary><div>노트북에서 우석World를 열고 설정 → 보안 → 비밀번호 초기화를 누르면 새로 만들 수 있어요. 데이터는 그대로 남아요.</div></details>`,
      setup: `<p class="lk-sub">앱을 열 때 물어볼 비밀번호를 만들어요.<br>노트북 서버에만, 원문이 아닌 암호화한 값으로 저장해요.</p>${user}
        <label class="lk-field"><span class="lk-lab">새 비밀번호</span><span class="lk-box"><input id="lkPw" name="new-password" type="password" autocomplete="new-password" autocapitalize="off" autocorrect="off" spellcheck="false" maxlength="128" required>${eye}</span></label>
        <label class="lk-field"><span class="lk-lab">한 번 더</span><span class="lk-box"><input id="lkPw2" type="password" autocomplete="new-password" enterkeyhint="go" autocapitalize="off" autocorrect="off" spellcheck="false" maxlength="128" required></span></label>
        <div class="lk-hint">4자 이상. 숫자와 글자를 섞어 6자 이상이면 더 안전해요</div>
        <div class="lk-err" id="lkErr" role="alert"></div>
        <button type="submit" class="lk-go" id="lkGo" data-label="비밀번호 만들고 시작하기">비밀번호 만들고 시작하기</button>`,
    }[mode];
    d.innerHTML = `<form class="lock-in" id="lkForm" novalidate>
      <span class="lk-mark"><img src="icon-192.png?v=s1" alt="" width="68" height="68"><i aria-hidden="true"><svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="11" width="14" height="9" rx="2.2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/></svg></i></span>
      <h2 id="lockTitle">${mode === "setup" ? "비밀번호 만들기" : "우석World"}</h2>${body}</form>`;
    if (!d.open) d.showModal();
    clearInterval(this.waitT);
    const form = $("#lkForm", d);
    form.onsubmit = (e) => { e.preventDefault(); if (mode === "unlock") this.unlock(); else if (mode === "setup") this.setup(); };
    if ($("#lkEye", d)) $("#lkEye", d).onclick = (e) => {
      const b = e.currentTarget, showing = b.getAttribute("aria-pressed") === "true";
      b.setAttribute("aria-pressed", String(!showing));
      $$("#lkPw,#lkPw2", d).forEach((x) => (x.type = showing ? "password" : "text"));
    };
    if ($("#lkRetry", d)) $("#lkRetry", d).onclick = () => { this.show("check"); this.verify(); };
    if ($("#lkConn", d)) $("#lkConn", d).onclick = () => { this.close(); };
    if (wait > 0) this.wait(wait);
    setTimeout(() => $("#lkPw", d)?.focus({ preventScroll: true }), 60);
  },
  close() { this.on = false; this.mode = ""; clearInterval(this.waitT); if (this.dlg) { if (this.dlg.open) this.dlg.close(); this.dlg.innerHTML = ""; } },
  hide() { this.close(); route(); },
  fail(e) {
    if (e instanceof NeedConnect) { this.close(); if (location.hash.startsWith("#connect")) route(); else location.hash = "#connect"; return; }
    const d = this.el(), err = $("#lkErr", d), pw = $("#lkPw", d);
    if (err) err.textContent = e.message;
    const box = pw && pw.closest(".lk-box");
    if (box) { box.classList.remove("shake"); void box.offsetWidth; box.classList.add("shake"); }
    if (this.mode === "unlock" && pw) { pw.value = ""; pw.focus({ preventScroll: true }); }
    if (e.wait > 0) this.wait(e.wait); else { const b = $("#lkGo", d); if (b) { b.disabled = false; b.textContent = b.dataset.label; } }
  },
  wait(sec) {
    const b = $("#lkGo", this.el()); if (!b) return;
    let left = Math.ceil(sec);
    const tick = () => {
      if (!b.isConnected) return clearInterval(this.waitT);
      if (left <= 0) { clearInterval(this.waitT); b.disabled = false; b.textContent = b.dataset.label; return; }
      b.disabled = true; b.textContent = left >= 60 ? `${Math.ceil(left / 60)}분 뒤에 다시 넣을 수 있어요` : `${left}초 뒤에 다시 넣을 수 있어요`; left--;
    };
    clearInterval(this.waitT); tick(); this.waitT = setInterval(tick, 1000);
  },
  busy() { const b = $("#lkGo", this.el()); if (b) { b.disabled = true; b.textContent = "확인하는 중"; } const e = $("#lkErr", this.el()); if (e) e.textContent = ""; },
  async unlock() {
    const d = this.el(), pw = $("#lkPw", d).value, keep = $("#lkKeep", d).checked;
    if (!pw) return;
    this.busy();
    try {
      const r = await api("POST", "/api/auth/unlock", { password: pw });
      sess.set(r.session, keep); store.set("keep", keep ? "1" : "");
      this.after = r.lock_after || this.after; this.required = true; this.hiddenAt = 0;
      this.hide();
    } catch (e) { this.fail(e); }
  },
  async setup() {
    const d = this.el(), pw = $("#lkPw", d).value, pw2 = $("#lkPw2", d).value;
    if (pw !== pw2) return this.fail(new Error("두 칸의 비밀번호가 서로 달라요"));
    this.busy();
    try {
      const r = await api("POST", "/api/auth/setup", { password: pw });
      sess.set(r.session, false); store.set("keep", "");
      this.after = r.lock_after || this.after; this.required = true;
      this.hide(); toast("비밀번호를 만들었어요. 다음부터 앱을 열 때 물어봐요");
    } catch (e) { this.fail(e); }
  },
  // 서버에 지금 상태를 묻고: 열려 있으면 화면을, 아니면 잠금 화면을 보여 준다
  async verify() {
    let st;
    try { st = await api("GET", "/api/auth/state"); }
    catch (e) {
      if (e instanceof NeedConnect) { this.close(); if (location.hash.startsWith("#connect")) route(); else location.hash = "#connect"; return; }
      return this.show("offline", e.message);
    }
    this.after = st.lock_after; this.required = !!st.required;
    if (!st.required || st.unlocked) { this.hide(); return; }
    this.show(st.set ? "unlock" : "setup", "", st.wait);
  },
  // 지금 잠그기(everywhere: 다른 기기까지)
  now(everywhere) {
    api("POST", "/api/auth/lock", { everywhere: !!everywhere }).catch(() => {});
    this.show("unlock");
  },
  // 앱으로 돌아왔을 때: 오래 자리를 비웠으면 바로 잠그고, 아니면 서버에 아직 열려 있는지 확인
  async back() {
    if (this.on) { if (this.mode === "offline") { this.show("check"); this.verify(); } return; }
    if (!this.required) return;
    const away = this.hiddenAt ? Date.now() - this.hiddenAt : 0;
    this.hiddenAt = 0;
    if (away > this.after * 60000) return this.now();
    if (away < 20000) return;
    try { const st = await api("GET", "/api/auth/state"); this.after = st.lock_after; if (st.required && !st.unlocked) this.show(st.set ? "unlock" : "setup", "", st.wait); }
    catch (e) { /* 연결이 안 되면 다음 요청에서 다시 확인 */ }
  },
};
document.addEventListener("visibilitychange", () => { if (document.hidden) lock.hiddenAt = Date.now(); else lock.back(); });

// ---------- 도구 ----------
const safeUrl = (u) => (/^https?:\/\//i.test(String(u || "")) ? esc(u) : "#");  // javascript: 같은 주소는 링크로 만들지 않는다
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const won = (n) => (n == null || isNaN(n) ? "-" : Math.round(n).toLocaleString("ko-KR").replace("-", "−"));   // 화면용 빼기 기호(−)
const shortWon = (n) => { const a = Math.abs(n); return a >= 10000 ? (a / 10000).toFixed(a >= 100000 ? 0 : 1).replace(/\.0$/, "") + "만" : won(a); };
const signed = (n) => (n == null || isNaN(n) ? "-" : (n > 0 ? "+" : n < 0 ? "−" : "") + won(Math.abs(n)));   // 화면용: 빼기는 하이픈이 아닌 수학 기호(−)
const pct = (n, d = 1) => (n == null ? "-" : (n > 0 ? "+" : n < 0 ? "−" : "") + Math.abs(Number(n)).toFixed(d) + "%");
const tone = (n) => (n > 0 ? "up" : n < 0 ? "down" : "");
const num = (v) => Number(String(v ?? "").replace(/,/g, "").replace(/−/g, "-")) || 0;
const localToday = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };
const thisMonth = () => localToday().slice(0, 7);
const WD = "일월화수목금토";
const korDate = (iso, wd = true) => { const [y, m, d] = iso.split("-").map(Number); return `${m}월 ${d}일` + (wd ? ` ${WD[new Date(y, m - 1, d).getDay()]}요일` : ""); };
// AI 추천 종류(보유 기간)
const AI_H = {
  day: { label: "당일", sub: "오늘 안에 정리", days: 0, go: "지금 추천 받기", title: "지금 사면 오늘 안에 수익 낼 확률이 높은 종목",
    desc: "누르는 순간의 실시간 순위·분봉 흐름·호가·뉴스를 Claude가 보고 최대 3종목을 골라요. 매수는 직접 고르고, 산 뒤에는 AI가 정한 값으로 자동 익절·손절해요." },
  swing: { label: "1주", sub: "5거래일 보유", days: 5, go: "1주 추천 받기", title: "지금 사서 1주일 들고 갈 종목",
    desc: "앞으로 1주 안에 주가를 움직일 재료(정책·실적·수주·수급)를 뉴스에서 먼저 찾고, 일봉 흐름으로 아직 덜 오른 수혜 종목을 최대 3개 골라요. 순위에 없는 종목도 찾아와요." },
  month: { label: "1달", sub: "20거래일 보유", days: 20, go: "1달 추천 받기", title: "지금 사서 한 달 들고 갈 종목",
    desc: "한 달 동안 이어질 재료(예산·법안·실적 시즌·업황)와 추세를 보고 최대 3종목을 골라요. 목표·손절을 넓게 잡고, 한 번의 뉴스로 끝날 테마는 걸러요." },
};

function toast(msg, err) {
  // 열린 시트(모달) 위에서도 보이게 알림 상자를 맨 위 창 안으로 옮긴다
  const box = $("#toast"), host = [$("#lockDlg"), $("#updDlg"), sheet].find((d) => d && d.open) || document.body;
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
    try { e.shown = true; } catch (x) { /* 읽기 전용 오류 객체 */ }  // 이미 안내했다는 표시(아래 전역 처리에서 또 띄우지 않게)
    if (e instanceof Locked) { closeSheet(); throw e; }
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
  neon: { name: "네온 핑크", deep: "#0B0710", deep2: "#1D1229", bg: "#0B0710", accent: "magenta", dark: true },
  neonblue: { name: "네온 블루", deep: "#060913", deep2: "#131A30", bg: "#060913", accent: "nblue", dark: true, neon: "blue" },
  neonmint: { name: "네온 민트", deep: "#050E0B", deep2: "#10221B", bg: "#050E0B", accent: "nmint", dark: true, neon: "mint" },
  neonsunset: { name: "네온 선셋", deep: "#100806", deep2: "#2A1810", bg: "#100806", accent: "nsun", dark: true, neon: "sunset" },
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
  magenta: ["마젠타", "#FF3DAE"],
  nblue: ["네온 블루", "#2F8CFF"], nmint: ["네온 민트", "#10B477"], nsun: ["네온 선셋", "#FF5A1F"],
  yellow: ["노랑", "#FFD84D"], lime: ["라임", "#C8F560"], mint: ["민트", "#7BE0B0"], aqua: ["하늘", "#7FD6F2"],
  peach: ["복숭아", "#FFB38A"], pink: ["핑크", "#FF9EC7"], lavender: ["라벤더", "#C3B1FF"], apricot: ["살구", "#FFC56B"],
};
function themeVars(id, accentId) {
  const t = THEMES[id] || THEMES.neon;
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
  const dark = !!(THEMES[id] || THEMES.neon).dark;
  document.documentElement.classList.toggle("dark-theme", dark);
  const neon = (THEMES[id] || {}).neon || "";
  document.documentElement.dataset.neon = neon;
  try { localStorage.setItem("moa.neon", neon); } catch (e) { /* 저장 불가 */ }
  try { localStorage.setItem("moa.dark", dark ? "1" : "0"); } catch (e) { /* 저장 불가 */ }
  Object.entries(v).forEach(([k, val]) => document.documentElement.style.setProperty(k, val));
  const mc = document.querySelector('meta[name="theme-color"]');
  if (mc) mc.content = v["--deep"];
  if (save) {
    store.set("theme", id); store.set("accent", accentId || "");
    try { localStorage.setItem("moa.themeVars", JSON.stringify(v)); } catch (e) { /* 저장 불가 */ }
  }
}

// v4.12: 새 기본 테마(네온)로 한 번 옮긴다. 설정 → 화면에서 예전 테마로 되돌릴 수 있다.
if (store.get("themeV") !== "5") { store.set("themeV", "5"); applyTheme("neon", "magenta"); }
else { const t0 = THEMES[store.get("theme") || "neon"] || {}; document.documentElement.classList.toggle("dark-theme", !!t0.dark); document.documentElement.dataset.neon = t0.neon || ""; }

// ---------- 투자 화면 게임 테마 ----------
// 기능은 그대로 두고 투자 화면만 게임처럼(입체 패널·누르는 버튼·금화 숫자) 보이게 한다. 설정 → 화면에서 끌 수 있다
const gameOn = () => store.get("gameInvest") !== "0";
function gameTheme(sec) {
  const on = sec === "invest" && gameOn(), el = document.documentElement;
  if (el.classList.contains("gm-on") === on && (!on || el.dataset.gmMeta)) return;
  el.classList.toggle("gm-on", on);
  el.dataset.gmMeta = on ? "1" : "";
  const mc = document.querySelector('meta[name="theme-color"]');
  if (mc) mc.content = on ? "#0B1830" : getComputedStyle(el).getPropertyValue("--deep").trim() || mc.content;
}

// ---------- 게임 화면의 그림 도구(투자 탭) ----------
// 목록 대신 그림으로: 금고 도넛, 장 시간 해 궤도, 보유 종목 지도(면적=금액, 색=수익률), 플랜 경주 트랙, 손익 달력
const G = {
  // 도넛: parts = [[값, 색, 이름]]
  donut(parts, size = 150, w = 20, center = "") {
    const r = (size - w) / 2, c = size / 2, C = 2 * Math.PI * r, tot = parts.reduce((a, p) => a + Math.max(0, p[0]), 0) || 1;
    let acc = 0;
    const seg = parts.filter((p) => p[0] > 0).map(([v, col]) => {
      const d = Math.max(0, C * v / tot - (parts.length > 1 ? 3 : 0)), o = acc; acc += C * v / tot;
      return `<circle cx="${c}" cy="${c}" r="${r}" fill="none" stroke="${col}" stroke-width="${w}" stroke-dasharray="${d.toFixed(1)} ${(C - d).toFixed(1)}" stroke-dashoffset="${(-o).toFixed(1)}" stroke-linecap="butt" transform="rotate(-90 ${c} ${c})"/>`;
    }).join("");
    return `<div class="g-donut" style="width:${size}px;height:${size}px"><svg viewBox="0 0 ${size} ${size}" aria-hidden="true">
      <circle cx="${c}" cy="${c}" r="${r}" fill="none" stroke="rgba(0,0,0,.35)" stroke-width="${w + 6}"/>${seg}
      <circle cx="${c}" cy="${c}" r="${r - w / 2 - 3}" fill="none" stroke="rgba(255,255,255,.08)" stroke-width="1.5"/></svg><div class="g-donut-c">${center}</div></div>`;
  },
  // 장 시간을 해가 지나가는 반원으로: 왼쪽 끝 = 첫 시간대 시작, 오른쪽 끝 = 마지막 시간대 끝
  arc(S) {
    const W = 156, H = 86, cx = W / 2, cy = 80, R = 66, a0 = S.segs[0][0], z = S.segs[S.segs.length - 1][1];
    const ang = (t) => Math.PI * (1 - (t - a0) / (z - a0)), pt = (t, rr = R) => [cx + rr * Math.cos(ang(t)), cy - rr * Math.sin(ang(t))];
    const col = { pre: "#7FA8FF", auction: "#FF9F5A", open: "#4BDB76", after: "#FFC531", day: "#9C8CFF" };
    const arcs = S.segs.map(([x, y, , k]) => { const [x1, y1] = pt(x), [x2, y2] = pt(y);
      return `<path d="M${x1.toFixed(1)} ${y1.toFixed(1)} A${R} ${R} 0 0 1 ${x2.toFixed(1)} ${y2.toFixed(1)}" stroke="${col[k] || "#7FA8FF"}" stroke-width="7" fill="none" stroke-linecap="butt" opacity="${S.now != null && S.now >= x && S.now < y ? 1 : .55}"/>`; }).join("");
    const inWin = S.now != null && S.now >= a0 && S.now <= z;
    let body;
    if (inWin) { const [sx, sy] = pt(S.now);
      body = `<circle cx="${sx.toFixed(1)}" cy="${sy.toFixed(1)}" r="11" fill="url(#gSun)" opacity=".55"/><circle cx="${sx.toFixed(1)}" cy="${sy.toFixed(1)}" r="6.5" fill="#FFE07A" stroke="#FF9F1C" stroke-width="2"/>`; }
    else body = `<g transform="translate(${cx} 50)"><circle r="12" fill="#DCE6FF"/><circle r="12" cx="6" cy="-4" fill="#14305C"/><text x="12" y="-10" font-size="9" fill="#9FB6DD" font-family="GameD">z</text><text x="18" y="-17" font-size="7" fill="#9FB6DD" font-family="GameD">z</text></g>`;
    return `<svg class="g-arc" viewBox="0 0 ${W} ${H}" aria-hidden="true"><defs><radialGradient id="gSun"><stop offset="0" stop-color="#FFE07A"/><stop offset="1" stop-color="#FFE07A" stop-opacity="0"/></radialGradient></defs>
      <path d="M${cx - R} ${cy} A${R} ${R} 0 0 1 ${cx + R} ${cy}" stroke="rgba(0,0,0,.35)" stroke-width="11" fill="none"/>${arcs}
      <line x1="${cx - R - 8}" y1="${cy}" x2="${cx + R + 8}" y2="${cy}" stroke="rgba(160,200,255,.25)" stroke-width="1.5"/>${body}</svg>`;
  },
  // 면적 비례 지도(squarify). items: [{v, ...}] (큰 것부터) → [{x,y,w,h,item}] (0~100)
  treemap(items, W = 100, H = 100) {
    const tot = items.reduce((a, x) => a + x.v, 0) || 1, out = [];
    let rest = items.map((it) => ({ it, a: it.v / tot * W * H })), x = 0, y = 0, w = W, h = H;
    const worst = (row, side) => { const s = row.reduce((a, r) => a + r.a, 0), mx = Math.max(...row.map((r) => r.a)), mn = Math.min(...row.map((r) => r.a));
      return Math.max(side * side * mx / (s * s), s * s / (side * side * mn)); };
    while (rest.length) {
      const side = Math.min(w, h), row = [rest.shift()];
      while (rest.length && worst([...row, rest[0]], side) <= worst(row, side)) row.push(rest.shift());
      const s = row.reduce((a, r) => a + r.a, 0);
      if (w >= h) { const cw = s / h; let yy = y; row.forEach((r) => { const rh = r.a / cw; out.push({ x, y: yy, w: cw, h: rh, item: r.it }); yy += rh; }); x += cw; w -= cw; }
      else { const rh = s / w; let xx = x; row.forEach((r) => { const rw = r.a / rh; out.push({ x: xx, y, w: rw, h: rh, item: r.it }); xx += rw; }); y += rh; h -= rh; }
    }
    return out;
  },
  // 수익률 → 칸 색(오르면 빨강, 내리면 파랑, 진할수록 크게)
  heat(p, cap = 6) {
    if (p == null || isNaN(p)) return "rgba(120,150,200,.25)";
    const k = Math.min(1, Math.abs(p) / cap) * .78 + .16;
    return p >= 0 ? `rgba(214,44,74,${k.toFixed(2)})` : `rgba(36,108,224,${k.toFixed(2)})`;   // 흰 글자가 4.5:1 넘게 읽히는 진하기
  },
  // 경주 트랙: 플랜들이 0% 선을 기준으로 왼쪽(손해)·오른쪽(이익)에 선다. 1등엔 왕관
  trackMini(race) {
    const xs = (race || []).filter((r) => r.n || r.open);
    if (!xs.length) return `<div class="g-track empty"><span>아직 출발 전이에요</span></div>`;
    const R = Math.max(1.5, ...xs.map((r) => Math.abs(r.net || 0))) * 1.15, X = (v) => 50 + (v / R) * 41;
    const lead = xs.slice().sort((a, b) => b.net - a.net)[0];
    return `<div class="g-track" role="img" aria-label="플랜 ${xs.length}개의 비용 뺀 수익률 분포, 1등 ${esc(lead.label)} ${pct(lead.net, 2)}">
      <i class="g-zero"></i>${xs.map((r, i) => `<b class="g-run ${r === lead ? "lead" : ""} ${r.net >= 0 ? "pos" : "neg"}" style="left:${X(r.net).toFixed(1)}%;top:${r === lead ? 50 : 26 + (i % 3) * 24}%"></b>`).join("")}
      <span class="g-tl">손해</span><span class="g-tr">이익</span></div>`;
  },
  hold(m) { return m == null ? "-" : m < 60 ? `${Math.round(m)}분` : m < 1440 ? `${Math.floor(m / 60)}시간` : `${(m / 1440).toFixed(1)}일`; },
  ico: {
    bag: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 8h12l-1 12H7L6 8z" fill="#FFC531" stroke="#7A4A00" stroke-width="1.6"/><path d="M9 8V6a3 3 0 0 1 6 0v2" fill="none" stroke="#7A4A00" stroke-width="1.6"/></svg>`,
    flag: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 21V4" stroke="#DCE6FF" stroke-width="2"/><path d="M6 4h11l-2.5 4L17 12H6z" fill="#4BDB76" stroke="#0F6B30" stroke-width="1.4"/></svg>`,
    radar: `<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9" fill="none" stroke="#7FD3FF" stroke-width="1.6"/><circle cx="12" cy="12" r="4.5" fill="none" stroke="#7FD3FF" stroke-width="1.2"/><path d="M12 12L19 6" stroke="#7FD3FF" stroke-width="2"/></svg>`,
    coin: `<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9" fill="#FFC531" stroke="#9A5F00" stroke-width="1.6"/><circle cx="12" cy="12" r="5.5" fill="none" stroke="#9A5F00" stroke-width="1.2"/></svg>`,
    trophy: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 4h10v4a5 5 0 0 1-10 0V4z" fill="#FFC531" stroke="#9A5F00" stroke-width="1.5"/><path d="M7 6H4a3 3 0 0 0 3 4M17 6h3a3 3 0 0 1-3 4" fill="none" stroke="#9A5F00" stroke-width="1.5"/><path d="M10 14h4v3h-4zM8 20h8" stroke="#9A5F00" stroke-width="1.8" fill="#FFC531"/></svg>`,
    search: `<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6" fill="rgba(127,211,255,.25)" stroke="#7FD3FF" stroke-width="2"/><path d="M15 15l5 5" stroke="#7FD3FF" stroke-width="2.6" stroke-linecap="round"/></svg>`,
    star: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3l2.7 5.6 6.1.8-4.5 4.2 1.1 6-5.4-2.9-5.4 2.9 1.1-6L3.2 9.4l6.1-.8z" fill="#FFC531" stroke="#9A5F00" stroke-width="1.3"/></svg>`,
    bolt: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M13 2L5 14h6l-1 8 8-12h-6z" fill="#FF9F5A" stroke="#7A3300" stroke-width="1.3"/></svg>`,
    bank: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 9l9-5 9 5z" fill="#9FB6DD"/><path d="M5 10h2v8H5zm4 0h2v8H9zm4 0h2v8h-2zm4 0h2v8h-2zM3 19h18v2H3z" fill="#DCE6FF"/></svg>`,
    bell: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 16V11a6 6 0 0 1 12 0v5l2 2H4z" fill="#FFC531" stroke="#9A5F00" stroke-width="1.4"/><circle cx="12" cy="20" r="2" fill="#9A5F00"/></svg>`,
    brain: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 4a3 3 0 0 0-3 3 3 3 0 0 0-2 5 3 3 0 0 0 2 5 3 3 0 0 0 3 3h1V4z" fill="#FF94C4" stroke="#8A1E5A" stroke-width="1.3"/><path d="M15 4a3 3 0 0 1 3 3 3 3 0 0 1 2 5 3 3 0 0 1-2 5 3 3 0 0 1-3 3h-1V4z" fill="#C3A6FF" stroke="#4B2A8A" stroke-width="1.3"/></svg>`,
  },
};

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
document.addEventListener("click", (e) => { if (e.target.closest && e.target.closest("a.stk")) e.stopPropagation(); }, true);
document.addEventListener("click", (e) => {
  if (!e.target.closest("[data-ailim]") || !pages.invest.at || !pages.invest.at.d) return;
  e.preventDefault(); e.stopPropagation();
  pages.invest.atAiLimit();
}, true);

// 1초마다 움직이는 숫자: data-el(지난 초), data-cd(남은 초), data-gap(간격 진행 막대). 서버에서 받은 값에 화면에서 흐른 시간을 더한다
function tickFmt(sec) {
  const v = Math.max(0, Math.round(sec)), h = Math.floor(v / 3600), m = Math.floor(v % 3600 / 60), ss = String(v % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}
function tickHtml(kind, sec) {
  return `<time class="tick" data-${kind}="${Math.round(sec)}" data-t0="${Date.now()}">${tickFmt(sec)}</time>`;
}
setInterval(() => {
  if (document.hidden) return;
  const now = Date.now();
  $$("[data-el]").forEach((e) => { e.textContent = tickFmt(Number(e.dataset.el) + (now - Number(e.dataset.t0)) / 1000); });
  $$("[data-cd]").forEach((e) => { const v = Number(e.dataset.cd) - (now - Number(e.dataset.t0)) / 1000; e.textContent = v > 0 ? tickFmt(v) : "0:00"; });
  $$("[data-gap]").forEach((e) => { const left = Number(e.dataset.gap) - (now - Number(e.dataset.t0)) / 1000; e.firstElementChild.style.width = `${Math.max(0, Math.min(100, (1 - left / Number(e.dataset.span)) * 100)).toFixed(1)}%`; });
}, 1000);

// 키움이 'IP가 등록되지 않았다'고 할 때: 노트북의 지금 공인 IP와 등록하는 곳을 바로 보여 준다(휴대폰에서도 등록할 수 있게)
const IPH = { ip: null, at: 0, busy: false, err: null };
function ipHelp(err) {
  if (!/8010|8050|IP/i.test(String(err || ""))) return "";
  if (!IPH.busy && Date.now() - IPH.at > 60000) {
    IPH.busy = true;
    api("GET", "/api/net/ip").then((r) => { IPH.ip = r.ip; IPH.err = null; }).catch((e) => { IPH.err = e.message; })
      .finally(() => { IPH.busy = false; IPH.at = Date.now(); $$("[data-iphelp]").forEach((x) => { x.outerHTML = ipHelpHtml(); }); });
  }
  return ipHelpHtml();
}
function ipHelpHtml() {
  return `<div class="ip-help" data-iphelp>
    <div class="ip-row"><span>노트북의 지금 인터넷 주소(IP)</span>${IPH.ip ? `<b>${esc(IPH.ip)}</b><button type="button" class="chip" data-ipcopy="${esc(IPH.ip)}">복사</button>` : `<b class="muted">${IPH.err ? "확인 실패" : "확인 중…"}</b>`}</div>
    ${IPH.err && !IPH.ip ? `<div class="ip-step">${esc(IPH.err)}</div>` : ""}
    <div class="ip-step">이 주소를 키움에 등록하면 다시 연결돼요. 휴대폰 브라우저로도 할 수 있어요.<br>키움 REST API 홈페이지 로그인 → API 사용신청 → 계좌 APP Key 관리 → IP 추가</div>
    <a class="btn sm" href="https://openapi.kiwoom.com" target="_blank" rel="noopener noreferrer">키움 REST API 홈페이지 열기</a>
  </div>`;
}
document.addEventListener("click", async (e) => {
  const b = e.target.closest("[data-ipcopy]");
  if (!b) return;
  e.preventDefault(); e.stopPropagation();
  try { await navigator.clipboard.writeText(b.dataset.ipcopy); toast("IP를 복사했어요"); } catch (err) { toast("복사하지 못했어요. 숫자를 길게 눌러 복사해 주세요", true); }
}, true);

function kwCardHtml(d) {
  if (!d || !d.configured) return `<h2>키움 계좌</h2><div class="small muted">아래 키움증권(조회 전용)에 앱키·시크릿키를 넣고 연결 확인을 누르면 여기서 언제든 계좌를 볼 수 있어요.</div>`;
  if (d.total == null) return `<div class="kw-top"><b>키움증권</b></div><div class="notice warn">${esc(d.error || "불러오는 중")}${ipHelp(d.error)}</div>`;
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
    ${!live && d.error ? `<div class="kw-err">${esc(d.error)}</div>${ipHelp(d.error)}` : ""}
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
      <b>2.</b> <b>메시지에 다음이 포함됨</b>에 <span class="code" style="padding:1px 4px">원</span> 입력(보낸 사람은 비워 두기)<br>
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
  document.documentElement.classList.add("modal-open");  // 뒤 화면이 같이 스크롤되지 않게
  $("#sheetBody").scrollTop = 0;
  if (mount) mount($("#sheetBody"));
}
function closeSheet() { if (sheet.open) sheet.close(); }
$("#sheetClose").onclick = closeSheet;
sheet.addEventListener("close", () => document.documentElement.classList.remove("modal-open"));
[sheet, $("#updDlg")].forEach((d) => d && d.addEventListener("close", () => document.body.appendChild($("#toast"))));
sheet.addEventListener("click", (e) => { if (e.target === sheet) closeSheet(); });

// 아래에서 올라온 창은 아래로 끌어내려 닫는다(머리 부분은 언제나, 내용은 맨 위까지 올라와 있을 때).
// panel: 움직일 요소, scroller(): 안에서 스크롤되는 요소, close(): 다 내려갔을 때 닫기
function dragDismiss(panel, scroller, close) {
  let g = null;
  const reset = () => { panel.style.transition = ""; panel.style.transform = ""; panel.classList.remove("dragging"); };
  panel.addEventListener("touchstart", (e) => {
    g = null;
    if (e.touches.length !== 1) return;
    const sc = scroller(), t = e.touches[0], inBody = sc && sc.contains(e.target);
    if (inBody) {
      if (e.target.closest("input,textarea,select,[contenteditable],.cz-hit,.mk-chart,[data-nodrag]")) return;
      for (let n = e.target; n && n !== sc; n = n.parentElement) {      // 안쪽에 따로 스크롤되는 칸이 내려가 있으면 그 칸이 먼저
        if (n.scrollTop > 0 || (n.scrollWidth > n.clientWidth + 4 && /auto|scroll/.test(getComputedStyle(n).overflowX))) return;
      }
    }
    g = { x: t.clientX, y: t.clientY, top: inBody ? sc.scrollTop : 0, on: false, t: Date.now(), dy: 0 };
  }, { passive: true });
  panel.addEventListener("touchmove", (e) => {
    if (!g) return;
    const t = e.touches[0], dx = t.clientX - g.x, dy = t.clientY - g.y;
    if (!g.on) {
      if (g.top > 0 || dy < -8 || Math.abs(dx) > Math.max(10, dy)) { g = null; return; }
      if (dy < 10) return;
      g.on = true; panel.classList.add("dragging"); panel.style.transition = "none";
    }
    e.preventDefault();
    g.dy = Math.max(0, dy - 10);
    panel.style.transform = `translateY(${g.dy}px)`;
  }, { passive: false });
  const end = () => {
    if (!g || !g.on) { g = null; return; }
    const v = g.dy / Math.max(1, Date.now() - g.t), go = g.dy > Math.min(160, panel.offsetHeight * 0.28) || (v > 0.55 && g.dy > 36);
    g = null;
    panel.style.transition = "transform .2s cubic-bezier(.3,.7,.3,1)";
    if (!go) { panel.style.transform = ""; setTimeout(reset, 210); return; }
    panel.style.transform = "translateY(105%)";
    setTimeout(() => { close(); reset(); }, 190);
  };
  panel.addEventListener("touchend", end); panel.addEventListener("touchcancel", end);
}
dragDismiss(sheet, () => $("#sheetBody"), closeSheet);

// 올라오는 전체 창: 목록에서 고른 것의 자세한 화면을, 화면을 바꾸지 않고 목록 위에 아래에서 올려 보여 준다.
// 닫기: 아래로 끌기 · × · 뒤로 가기 · Esc. 창 안에서 여는 작은 창(dialog)은 이 위에 뜬다
const pgSheet = {
  el: null, key: null, onClose: null,
  get open() { return !!this.el && !this.el.hidden && !this.el.classList.contains("out"); },
  body() { return $("#pgsBody"); },
  make() {
    const d = document.createElement("div");
    d.className = "pgs"; d.id = "pgs"; d.hidden = true;
    d.innerHTML = `<div class="pgs-bd"></div><section class="pgs-in" role="dialog" aria-modal="true" aria-label="자세히 보기" tabindex="-1">
      <div class="pgs-head"><i class="pgs-grab" aria-hidden="true"></i><b class="pgs-tt" id="pgsTitle"></b><span class="pgs-rt" id="pgsRight"></span><button type="button" class="pgs-x" id="pgsClose" aria-label="닫기">${CLOSE_ICO}</button></div>
      <div class="pgs-body" id="pgsBody"></div></section>`;
    document.body.appendChild(d);
    $(".pgs-bd", d).onclick = () => this.close();
    $("#pgsClose", d).onclick = () => this.close();
    dragDismiss($(".pgs-in", d), () => $("#pgsBody"), () => this.close(false, true));
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && this.open && !sheet.open && !$(".cz")) this.close(); });
    window.addEventListener("popstate", () => { if (this.skipPop) { this.skipPop = false; return; } if (this.open && !this.noHist && location.hash === this.hashAt) this.close(false, false, true); });   // 주소가 바뀐 이동은 화면 전환 쪽에서 처리한다
    return (this.el = d);
  },
  // key가 바뀔 때만 맨 위로 올린다(같은 화면을 다시 그릴 때는 보던 자리 그대로)
  // o.noHist: 주소(해시)로 여닫는 창이라 따로 뒤로 가기 기록을 만들지 않는다 · o.title/o.right: 머리의 제목과 버튼
  show(key, onClose, o = {}) {
    const d = this.el || this.make(), was = this.open, old = this.onClose, oldKey = this.key;
    this.onClose = onClose; this.noHist = !!o.noHist; this.hashAt = location.hash;
    if (was && oldKey !== key && old && old !== onClose) old(true, oldKey);      // 다른 화면이 이 창을 이어받음
    if (!was) {
      d.hidden = false; d.classList.remove("out"); void d.offsetWidth; d.classList.add("in");
      document.documentElement.classList.add("pgs-open");
      if (!o.noHist && !(history.state && history.state.pgs)) { try { history.pushState({ pgs: 1 }, ""); } catch (e) { /* 기록을 못 쓰는 환경 */ } }
      setTimeout(() => $(".pgs-in", d).focus({ preventScroll: true }), 30);
    }
    $("#pgsTitle").textContent = o.title || ""; $("#pgsRight").innerHTML = o.right || "";
    $(".pgs-head", d).classList.toggle("has-tt", !!(o.title || o.right));
    if (this.key !== key) { this.key = key; this.body().scrollTop = 0; }
    return this.body();
  },
  // silent: 다른 화면으로 넘어가며 닫음(목록을 다시 그리지 않음) · dragged: 끌어내려 닫음(내려가는 움직임은 이미 끝남) · popped: 뒤로 가기로 닫음
  close(silent, dragged, popped) {
    if (!this.open) return;
    const d = this.el, cb = this.onClose, key = this.key;
    this.onClose = null; this.key = null;
    document.documentElement.classList.remove("pgs-open");
    const done = () => { d.hidden = true; d.classList.remove("in", "out"); this.body().innerHTML = ""; };
    if (silent || dragged) done(); else { d.classList.add("out"); setTimeout(() => { if (!this.onClose) done(); }, 230); }
    if (!popped && !this.noHist && history.state && history.state.pgs && !silent) { try { this.skipPop = true; history.back(); } catch (e) { this.skipPop = false; } }
    this.noHist = false;
    if (cb) cb(!!silent, key);
  },
};

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
  ledger: { title: "가계부", subs: [["list", "내역"], ["calendar", "달력"], ["analysis", "분석"], ["budget", "예산"], ["fixed", "고정비"], ["cards", "카드"], ["auto", "자동"]] },
  assets: { title: "자산", subs: [["overview", "한눈에"], ["accounts", "계좌"], ["goal", "목표"]] },
  // 투자는 성격이 같은 화면을 묶는다(AI 추천·자동매매 / 관심·발견·주문 / 적립·배당). 묶음 안은 화면 위 탭으로 오간다
  invest: { title: "투자", subs: [["home", "홈"], ["ai", "AI"], ["market", "종목"], ["dca", "적립·배당"], ["evo", "엔진 노트"]],
    groups: { ai: [["auto", "자동매매"], ["ai", "AI 추천"], ["atlog", "매매 기록"]], market: [["market", "관심"], ["discover", "발견"], ["trade", "주문"]], dca: [["dca", "적립"], ["dividend", "배당"]] }, extra: { scalp: "ai", aistat: "ai", backtest: "ai", rules: "ai", sweep: "ai", stock: "home", account: "home" } },
  // 설정: 자주 바꾸는 것부터. 한 번만 맞추면 되는 서버 연결은 뒤로.
  settings: { title: "설정", subs: [["all", "전체"], ["theme", "화면"], ["ai", "AI"], ["accounts", "계좌"], ["trading", "주문·알림"], ["security", "보안"], ["backup", "백업"], ["connect", "서버"], ["app", "앱 정보"]] },
};
const lastSub = {}, lastIn = {};
// 묶음 안의 화면 → 묶음 이름(예: auto → ai)
function groupOf(name, sub) {
  const g = (MENU[name] || {}).groups || {};
  return Object.keys(g).find((k) => g[k].some(([id]) => id === sub)) || null;
}
function resolveSub(h) {
  const m = MENU[h.name];
  if (!m) return h.sub;
  const ok = m.subs.some(([id]) => id === h.sub) || (m.extra && m.extra[h.sub]) || groupOf(h.name, h.sub);
  return ok ? h.sub : lastSub[h.name] || m.subs[0][0];
}
function subnav(name, sub) {
  const m = MENU[name];
  if (!m) return "";
  const on = (m.extra && m.extra[sub]) || groupOf(name, sub) || sub;
  // 묶음 탭은 그 묶음에서 마지막으로 본 화면으로 간다
  return `<nav class="subnav ${m.subs.length > 5 ? "many" : ""}" aria-label="${esc(m.title)} 세부 메뉴">${m.subs.map(([id, label]) =>
    `<a href="#${name}/${lastIn[name + ":" + id] || id}" class="subtab" ${id === on ? 'aria-current="page"' : ""}>${esc(label)}</a>`).join("")}</nav>`;
}
// 묶음 안의 화면 탭(화면 맨 위). 묶음이 아니면 빈 글
function groupTabs(name, sub) {
  const g = groupOf(name, sub);
  if (!g) return "";
  lastIn[name + ":" + g] = sub;
  const items = MENU[name].groups[g];
  return `<nav class="ivg" aria-label="${esc(MENU[name].subs.find(([id]) => id === g)[1])} 화면">${items.map(([id, label]) =>
    `<a href="#${name}/${id}" class="ivg-t" ${id === sub ? 'aria-current="page"' : ""}>${esc(label)}</a>`).join("")}</nav>`;
}

// ---------- 우석World: 앱 모음 ----------
// 이 웹앱 하나에 내가 만든 앱들을 모은다. 앱마다 구역(주소 첫 마디)·아래 탭·겉모습(skin)을 가진다.
// 새 앱은 자기 파일에서 addApp({...})과 pages.<구역>만 더하면 첫 화면에 타일이 생긴다(docs/health.js 참고).
const APPS = [{ id: "ledger", name: "가계부", desc: "가계부, 자산, 투자", home: "#home", secs: ["home", "invest", "assets", "ledger", "settings", "connect"],
  tile: { bg: "linear-gradient(150deg,#1B1230 0%,#0B0710 62%)", ink: "#FFFFFF", sub: "#C9BEDD", accent: "#FF3DAE" } }];
function addApp(a) { if (!APPS.some((x) => x.id === a.id)) APPS.push(a); }
const appOf = (sec) => APPS.find((a) => a.secs.includes(sec)) || null;
const LEDGER_TABS = $(".tabbar .in").innerHTML;
let tabsOf = "ledger";
function setTabbar(app) {
  const id = app && app.tabs ? app.id : "ledger";
  if (id === tabsOf) return;
  tabsOf = id;
  $(".tabbar .in").innerHTML = id === "ledger" ? LEDGER_TABS : app.tabs.map(([to, label, icon]) =>
    `<a class="tab" href="#${to}" data-tab="${to}"><span class="dot"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icon}</svg></span>${esc(label)}</a>`).join("");
}
// 앱마다 다른 겉모습: 색 변수만 잠깐 바꿔 끼우고, 나가면 고른 테마로 되돌린다
let skinOf = "";
function sectionSkin(sec) {
  const app = appOf(sec), sk = sec === "world" ? WORLD_SKIN : app && app.skin, id = sk ? (sec === "world" ? "world" : app.id) : "";
  if (id === skinOf) return;
  skinOf = id;
  const el = document.documentElement, mc = document.querySelector('meta[name="theme-color"]');
  if (!sk) return applyTheme(THEMES[store.get("theme")] ? store.get("theme") : "neon", store.get("accent"), false);
  el.classList.toggle("dark-theme", !!sk.dark); el.dataset.neon = "";   // 어두운 앱(사진편집)은 공통 창·버튼도 어둡게
  Object.entries(sk.vars).forEach(([k, v]) => el.style.setProperty(k, v));
  if (mc) mc.content = sk.meta;
}
const WORLD_SKIN = { meta: "#E7EDF3", vars: { "--deep": "#10243A", "--deep2": "#1B3550", "--bg": "#E7EDF3", "--hl": "#FFD84D", "--solid": "#10243A",
  "--on-deep": "#E9EDF5", "--on-deep2": "#A3ADC2", "--on-deep-strong": "#FFFFFF", "--sub-bg": "rgba(255,255,255,.08)" } };

// ---------- 라우터 ----------
const pages = {};
let current = null, timer = null;
function setTop(title, opt = {}) {
  $("#title").textContent = title;
  if (opt.badge) $("#title").insertAdjacentHTML("beforeend", ` <span class="ver">${esc(opt.badge)}</span>`);
  document.title = title === "우석World" ? title : `${title} · 우석World`;
  const top = $("#top");
  $(".back", top)?.remove();
  if (opt.back) $("#title").insertAdjacentHTML("beforebegin", `<a class="back" href="${opt.back}" aria-label="뒤로"><svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 18l-6-6 6-6"/></svg></a>`);
  $("#topRight").innerHTML = opt.right || "";
  // 2뎁스 메뉴는 엄지가 닿는 아래쪽(하단 탭 바로 위)에 둔다
  $("#subdock").innerHTML = opt.menu ? subnav(opt.menu, opt.sub) : "";
  document.body.classList.toggle("has-sub", !!opt.menu);
  const cur = $("#subdock [aria-current]"), nav = $("#subdock .subnav");   // 메뉴가 길어 가려진 탭이면 보이는 곳으로
  if (cur && nav && nav.scrollWidth > nav.clientWidth) nav.scrollLeft = cur.offsetLeft - (nav.clientWidth - cur.offsetWidth) / 2;
}
// 큰 숫자는 처음 보일 때 0부터 올라가게(동작 줄이기 설정이면 생략)
const RM = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)");
function countUp(root) {
  if (RM && RM.matches) return;
  $$(".big, .ih-total, .ap-h-num", root).forEach((el) => {
    const tn = [...el.childNodes].find((n) => n.nodeType === 3 && /\d/.test(n.nodeValue));
    if (!tn) return;
    const raw = tn.nodeValue, m = raw.match(/-?[\d,]+(\.\d+)?/);
    if (!m) return;
    const target = Number(m[0].replace(/,/g, "")), dec = (m[1] || "").length - (m[1] ? 1 : 0);
    if (!isFinite(target) || Math.abs(target) < 10) return;
    const pre = raw.slice(0, m.index), post = raw.slice(m.index + m[0].length), t0 = performance.now(), dur = 700;
    const fmt = (v) => v.toLocaleString("ko-KR", { minimumFractionDigits: dec, maximumFractionDigits: dec });
    const step = (t) => {
      if (!tn.isConnected) return;
      const k = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - k, 3);
      tn.nodeValue = pre + fmt(k < 1 ? target * e : target) + post;
      if (k < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  });
}
function parseHash() {
  const raw = (location.hash || "#world").slice(1);
  const [path, query] = raw.split("?");
  const [name, sub] = path.split("/");
  return { name, sub, params: new URLSearchParams(query || "") };
}
async function route() {
  const h = parseHash();
  if (!pages[h.name]) h.name = "world";
  h.sub = resolveSub(h);
  if (MENU[h.name]) lastSub[h.name] = (MENU[h.name].extra && MENU[h.name].extra[h.sub]) ? lastSub[h.name] || h.sub : h.sub;
  if (h.name === "invest" && h.sub === "stock" && current && view.childElementCount && !$("#mkD", view) && (stockSheet.stack.length || nav.prev)) return stockSheet.open(h);
  stockSheet.stack = []; stockSheet.resume = null;
  const page = pages[h.name];
  const tabName = h.name, app = appOf(h.name);
  document.documentElement.dataset.sec = h.name;
  document.documentElement.dataset.app = h.name === "world" ? "world" : app ? app.id : "ledger";
  setTabbar(app);
  if (app && app.tabs && !h.sub) h.sub = app.tabs[0][0].split("/")[1];
  sectionSkin(h.name);
  gameTheme(h.name);
  const skin = h.name === "world" ? WORLD_SKIN : app && app.skin, mcol = document.querySelector('meta[name="theme-color"]');
  if (skin && mcol) mcol.content = skin.meta;   // 게임 테마를 끄면서 바뀐 상단 색을 앱 색으로 다시
  document.documentElement.dataset.sub = (h.sub === "scalp" ? "ai" : h.sub) || "";
  $$(".tab").forEach((a) => {
    const on = a.dataset.tab === tabName || a.dataset.tab === tabName + "/" + (h.sub || "");
    a.classList.toggle("on", on);
    if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  clearInterval(timer);
  closeSheet();
  pgSheet.close(true);
  $(".fab")?.remove();
  current = page;
  view.innerHTML = `<div class="empty loading">불러오는 중</div>`;
  try {
    await page.mount(h);
    view.classList.remove("enter"); void view.offsetWidth; view.classList.add("enter");
    clearTimeout(route._t); route._t = setTimeout(() => view.classList.remove("enter"), 1100);
    countUp(view);
    restoreScroll();
  }
  catch (e) {
    if (e instanceof Locked) { view.innerHTML = ""; return; }
    if (e instanceof NeedConnect && h.name !== "connect") { location.hash = "#connect"; return; }
    if (e.code === "old_server") { view.innerHTML = `<div id="osHost"></div>`; return oldServerBox($("#osHost"), route); }
    view.innerHTML = `<div class="notice warn">${esc(e.message)}</div><button class="btn wide" data-act="retry">다시 시도</button>`;
  }
  if (page.refresh) timer = setInterval(() => { if (current === page && !document.hidden && !sheet.open) page.refresh(h).catch(() => {}); }, page.every || 3000);
  window.scrollTo(0, 0);
}
// 뒤로 가기: 직전에 보던 화면으로(스크롤 위치도 되살린다)
const nav = { cur: location.hash, prev: "", scroll: {}, restore: "" };
function navBack(fallback) { return nav.prev && nav.prev !== location.hash && !nav.prev.startsWith("#invest/stock") ? nav.prev : nav.prevNonStock || fallback; }
// 종목 상세는 보던 화면 위로 올라오는 창으로 연다(주소는 #invest/stock 그대로라 링크·뒤로 가기가 그대로 동작한다).
// 닫으면 주소만 되돌리고 아래 화면은 다시 그리지 않는다. 주소로 바로 들어온 경우(새로 고침·바로 가기)에는 예전처럼 한 화면으로 보여 준다
const stockSheet = {
  stack: [], base: "", closing: "", timer: 0, resume: null,
  open(h) {
    const S = this, hash = location.hash, A = pages.invest.at;
    if (!S.stack.length) {
      S.base = nav.prev || "#home";
      S.resume = pgSheet.open && String(pgSheet.key).startsWith("auto:") && A ? { view: A.view, tab: A.tab, back: A.back, labId: A.labId, labTab: A.labTab } : null;   // 자동매매 창에서 종목을 눌렀으면 닫을 때 그 창으로
      S.stack = [hash];
    } else if (S.stack[S.stack.length - 2] === hash) S.stack.pop();
    else if (S.stack[S.stack.length - 1] !== hash) S.stack.push(hash);
    closeSheet();
    const host = pgSheet.show("stock:" + hash, (silent) => S.closed(silent), { noHist: true, title: "시세",
      right: `<button type="button" class="star" id="mkBell" aria-label="가격 알림">${BELL}</button><button type="button" class="star" id="mkStar" aria-pressed="false" aria-label="관심종목">${STAR}</button>` });
    clearInterval(S.timer);
    S.timer = setInterval(() => { if (pgSheet.open && !document.hidden && !sheet.open && !$(".pgs-in.dragging")) mkt.refreshDetail().catch(() => {}); }, 2500);
    return mkt.detail(h, host).catch((e) => { host.innerHTML = `<div class="notice warn">${esc(e.message)}</div>`; });
  },
  back() {            // 자동매매 창에서 왔으면 그 창을 다시 연다
    const r = this.resume, A = pages.invest.at;
    this.resume = null;
    if (r && A && investState.tab === "auto" && $("#iv")) { Object.assign(A, r); A.d = null; setTimeout(() => pages.invest.auto($("#iv")), 40); }
  },
  closed(silent) {
    const n = this.stack.length, base = this.base;
    clearInterval(this.timer); this.stack = [];
    if (silent || !n) return;                  // 다른 화면으로 넘어가거나 뒤로 가기로 닫힌 것: 주소는 이미 바뀌었다
    this.closing = base;
    pgSheet.skipPop = true;                    // 우리가 되돌린 것: 다시 연 창을 뒤로 가기로 착각해 닫지 않게
    history.go(-n);
    this.back();
  },
};
window.addEventListener("hashchange", () => {
  if (stockSheet.closing) {                    // 창을 닫으며 주소만 되돌리는 중: 아래 화면은 그대로 둔다
    const ok = location.hash === stockSheet.closing;
    stockSheet.closing = "";
    if (ok) { nav.cur = location.hash; return; }
  }
  if (stockSheet.stack.length && location.hash === stockSheet.base) {      // 뒤로 가기로 창을 닫음
    nav.cur = location.hash;
    const r = stockSheet.resume;
    pgSheet.close(true);
    stockSheet.resume = r; stockSheet.back();
    return;
  }
  nav.scroll[nav.cur] = window.scrollY;
  nav.prev = nav.cur; nav.cur = location.hash;
  if (!nav.prev.startsWith("#invest/stock")) nav.prevNonStock = nav.prev;
  route();
});
document.addEventListener("click", (e) => { const b = e.target.closest("a.back"); if (b) nav.restore = b.getAttribute("href"); }, true);
function restoreScroll() {
  if (nav.restore && nav.restore === location.hash && nav.scroll[location.hash] != null) {
    const y = nav.scroll[location.hash]; nav.restore = "";
    [0, 250, 700].forEach((t) => setTimeout(() => window.scrollTo(0, y), t));
  } else nav.restore = "";
}
// 버튼을 눌렀는데 아무 일도 안 일어나는 일이 없게: 어디서도 처리하지 않은 오류는 화면에 알린다
let lastOops = ["", 0];
window.addEventListener("unhandledrejection", (ev) => {
  const e = ev.reason || {};
  if (e.shown || e instanceof Locked || e.name === "AbortError") { ev.preventDefault(); return; }
  if (e instanceof NeedConnect) { ev.preventDefault(); if (!location.hash.startsWith("#connect")) location.hash = "#connect"; return; }
  const msg = String(e.message || e || "").slice(0, 140);
  if (!msg || (lastOops[0] === msg && Date.now() - lastOops[1] < 5000)) return;
  lastOops = [msg, Date.now()];
  try { toast(/^[가-힣]/.test(msg) ? msg : `문제가 생겼어요: ${msg}`, true); } catch (x) { /* 화면 준비 전 */ }
});
view.addEventListener("click", (e) => { if (e.target.closest('[data-act="retry"]')) route(); });
// 가계부 월 이동(어느 2뎁스 화면이든 공통)
view.addEventListener("click", (e) => {
  const b = e.target.closest("[data-mon]");
  if (b && current === pages.ledger) { shiftMonth(ledgerState, Number(b.dataset.mon)); route(); }
});
function fab(label, onClick) {
  $(".fab")?.remove();
  const b = document.createElement("button");
  b.type = "button"; b.className = "fab"; b.innerHTML = `<span aria-hidden="true" style="font-size:18px;line-height:1">+</span>${esc(label)}`;
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
      try { await GET("/api/ping"); toast("연결했어요"); location.hash = "#world"; return; }
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
          2. 노트북에서 moa를 켜고, 명령창에 <span class="code" style="padding:2px 4px;white-space:nowrap">tailscale serve --bg 8780</span> 를 한 번 실행해요.<br>
          3. 나오는 https 주소를 위 서버 주소에 넣어요.<br>
          노트북 화면의 설정 탭에서 휴대폰 연결 링크를 만들면 주소와 토큰이 한 번에 들어가요.</div>
      </section>`;
    $("#cGo").onclick = async () => {
      store.set("server", $("#cServer").value.trim().replace(/\/+$/, ""));
      store.set("token", $("#cToken").value.trim());
      await run(() => GET("/api/ping"), "연결했어요");
      location.hash = "#world";
    };
  },
};

// =====================================================================
// 홈
// =====================================================================
let catCache = null;
async function categories() { if (!catCache) catCache = (await GET(`/api/ledger?month=${thisMonth()}`)).categories; return catCache; }

// ---------- 갚기 목표: 정한 날까지 얼마를 갚을지, 지금 일정보다 앞섰는지 ----------
const payoff = {
  bar(S) {   // 채워진 만큼이 갚은 돈, 세로 눈금이 '일정대로라면 오늘 와 있어야 할 자리'
    return `<div class="pf-bar" role="img" aria-label="갚은 돈 ${S.pct}%, 지난 기간 ${S.time_pct}%"><i style="width:${Math.min(100, S.pct)}%"></i><em style="left:${Math.min(100, S.time_pct)}%"><span>오늘 일정</span></em></div>`;
  },
  homeCard(S) {
    if (!S || !S.goal) return "";
    return `<a class="card pf-home" href="#assets/goal">
      <div class="card-head"><h2>${esc(S.goal.name)}</h2><span class="tiny">${esc(S.goal.deadline.slice(0, 7))}까지</span></div>
      <div class="pf-eq"><b>${won(S.paid)}원</b><span>/ ${won(S.goal.amount)}원 · ${S.pct}%</span></div>
      ${this.bar(S)}<div class="small">${esc(S.text)}</div></a>`;
  },
  html(S) {
    if (!S.goal) return `<section class="card pf"><div class="card-head"><h2>갚기 목표</h2></div>
      <div class="small muted">정한 날까지 빚을 얼마 갚을지 정하면, 갚을 때마다 기록해 일정보다 앞섰는지 뒤처졌는지와 한 달에 필요한 돈을 알려 드려요.</div>
      <button type="button" class="btn dark wide" data-pf="goal">갚기 목표 정하기</button></section>`;
    const g = S.goal, mx = Math.max(1, ...S.months.map((m) => m.amount));
    return `<section class="card pf">
      <div class="card-head"><h2>${esc(g.name)}</h2><button type="button" class="link" data-pf="goal">목표 바꾸기</button></div>
      <div class="pf-eq"><b>${won(S.paid)}원</b><span>/ ${won(g.amount)}원 · ${S.pct}%</span></div>
      ${this.bar(S)}
      <div class="notice">${esc(S.text)}</div>
      <dl class="pf-kv"><div><dt>남은 돈</dt><dd>${won(S.left)}원</dd></div><div><dt>한 달에 필요</dt><dd>${won(S.need_month)}원</dd></div>
        <div><dt>지금 한 달 평균</dt><dd>${S.avg_month == null ? "한 달 뒤부터" : `${won(S.avg_month)}원`}</dd></div>
        <div><dt>이 속도면 끝나는 때</dt><dd>${S.eta === "done" ? "다 갚음" : S.eta ? esc(S.eta) : "-"}<small>기한 ${esc(g.deadline.slice(0, 7))}</small></dd></div></dl>
      ${S.months.length ? `<div class="pf-months" role="img" aria-label="달마다 갚은 돈">${S.months.map((m) => `<div><i style="height:${Math.max(4, (m.amount / mx) * 100)}%"></i><span>${Number(m.month.slice(5))}월</span></div>`).join("")}</div>` : ""}
      ${S.by.length > 1 ? `<div class="legend">${S.by.map((b) => `<span>${esc(b.label)} <b>${won(b.amount)}원</b></span>`).join("")}</div>` : ""}
      <button type="button" class="btn dark wide" data-pf="add">갚은 돈 기록</button>
      ${S.hints.length ? `<div class="pf-hints">${S.hints.map((h) => `<div><span><b>${esc(h.label)}</b><small>${esc(h.sub)}</small></span><b class="${tone(h.amount)}">${signed(h.amount)}원</b>
        ${h.amount > 0 ? `<button type="button" class="chip" data-pf="add" data-amt="${h.amount}" data-src="${h.key}">이만큼 갚았어요</button>` : ""}</div>`).join("")}</div>
        <div class="tiny">위 금액은 참고용이에요. 실제로 갚았을 때만 기록해 주세요.</div>` : ""}
      ${S.logs.length ? `<div class="list pf-logs">${S.logs.map((l) => `<div class="item"><span class="main"><b>${won(l.amount)}원</b><span class="sub">${esc(l.date)} · ${esc(l.src_label)}${l.note ? ` · ${esc(l.note)}` : ""}</span></span>
        <button type="button" class="chip" data-pf="del" data-id="${l.id}">지우기</button></div>`).join("")}</div>` : ""}
    </section>`;
  },
  async mount(box) {
    let S = await GET("/api/payoff");
    const draw = () => { box.innerHTML = this.html(S); };
    draw();
    box.onclick = async (e) => {
      const b = e.target.closest("[data-pf]");
      if (!b) return;
      const act = b.dataset.pf, today = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);
      if (act === "del") {
        if (!confirmTap(b, "한 번 더 누르면 지워요")) return;
        S = await run(() => api("DELETE", `/api/payoff/log/${b.dataset.id}`), "지웠어요"); return draw();
      }
      if (act === "add") return openSheet("갚은 돈 기록", `<input class="amount-in" id="pfA" inputmode="numeric" placeholder="0원" value="${b.dataset.amt ? won(b.dataset.amt) : ""}">
          <div class="fields"><label class="field">갚은 날<input id="pfD" type="date" value="${today}"></label><label class="field">메모<input id="pfN" maxlength="40" placeholder="선택"></label></div>
          <div class="field">어디서 난 돈인가요<div class="chips" id="pfS">${Object.entries(S.sources).map(([k, l]) => `<button type="button" class="chip" data-src="${k}" aria-pressed="${k === (b.dataset.src || "save")}">${esc(l)}</button>`).join("")}</div></div>
          <button type="button" class="btn dark wide" id="pfGo">기록</button>`, (sh) => {
        moneyInput($("#pfA", sh));
        let src = b.dataset.src || "save";
        $("#pfS", sh).onclick = (ev) => { const c = ev.target.closest("[data-src]"); if (!c) return; src = c.dataset.src; $$("[data-src]", sh).forEach((x) => x.setAttribute("aria-pressed", String(x === c))); };
        $("#pfGo", sh).onclick = async () => {
          try { S = await run(() => api("POST", "/api/payoff/log", { amount: num($("#pfA", sh).value), date: $("#pfD", sh).value, src, note: $("#pfN", sh).value }), "기록했어요"); } catch (err) { return; }
          closeSheet(); draw();
        };
      });
      const g = S.goal || {};
      openSheet("갚기 목표", `<label class="field">이름<input id="pgN" maxlength="20" placeholder="예: 대출 줄이기" value="${esc(g.name || "")}"></label>
          <label class="field">갚을 금액<input class="amount-in" id="pgA" inputmode="numeric" placeholder="0원" value="${g.amount ? won(g.amount) : ""}"></label>
          <div class="fields"><label class="field">시작일<input id="pgS" type="date" value="${esc(g.start || today)}"></label><label class="field">끝나는 날<input id="pgE" type="date" value="${esc(g.deadline || "")}"></label></div>
          <div class="chips">${[1, 3, 5, 10].map((y) => `<button type="button" class="chip" data-y="${y}">${y}년 뒤</button>`).join("")}</div>
          <div class="tiny">시작일부터 갚은 기록만 셈해요. 전부가 아니라 갚고 싶은 만큼만 넣어도 돼요.</div>
          <button type="button" class="btn dark wide" id="pgGo">저장</button>
          ${g.amount ? `<button type="button" class="btn wide" id="pgX">목표 지우기</button>` : ""}`, (sh) => {
        moneyInput($("#pgA", sh));
        sh.addEventListener("click", (ev) => { const c = ev.target.closest("[data-y]"); if (!c) return; const d0 = new Date($("#pgS", sh).value || today); d0.setFullYear(d0.getFullYear() + Number(c.dataset.y)); $("#pgE", sh).value = d0.toISOString().slice(0, 10); });
        $("#pgGo", sh).onclick = async () => {
          try { S = await run(() => api("PUT", "/api/payoff", { name: $("#pgN", sh).value, amount: num($("#pgA", sh).value), start: $("#pgS", sh).value, deadline: $("#pgE", sh).value }), "목표를 저장했어요"); } catch (err) { return; }
          closeSheet(); draw();
        };
        if ($("#pgX", sh)) $("#pgX", sh).onclick = async (ev) => {
          if (!confirmTap(ev.currentTarget, "한 번 더 누르면 지워요(기록은 남아요)")) return;
          S = await run(() => api("PUT", "/api/payoff", { clear: true }), "목표를 지웠어요"); closeSheet(); draw();
        };
      });
    };
  },
};

pages.home = {
  every: 3000,
  async refresh() {   // 돌고 있는 자동매매가 있으면 그 카드만 새로(받는 중에는 3초, 평소에는 15초마다)
    if ($("#hmAuto") && $("#hmAuto").children.length) await pages.invest.atBrief();
  },
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
      <section class="card tight"><div class="list">${xs.map((x) => `<button type="button" class="item" ${dt(actDetail(x))}>
        <span class="ico ${({ warn: "warn", order: "h-indigo", ledger: "h-coral", update: "h-violet" })[x.kind] || "h-slate"}">${icon[x.kind] || ""}</span>
        <span class="main"><b>${esc(x.title)}</b><span class="sub">${esc((x.ts || "").slice(11, 16))} · ${esc(x.sub || "")}</span></span>
        <span class="end">${x.amount != null ? `<b class="${x.amount > 0 ? "inc" : ""}">${x.amount > 0 ? "+" : "-"}${won(Math.abs(x.amount))}</b>` : x.price ? `<span class="small ${x.side === "buy" ? "up" : "down"}">${isUsCode(x.order?.code) ? usd(x.price) : won(x.price) + "원"}</span>` : ""}</span></button>`).join("")}</div></section>`).join("")
      : `<section class="card"><div class="empty">아직 기록이 없어요. 가계부를 쓰거나 주문하면 여기에 모여요</div></section>`;
  },
  async summary() {
    const d = await GET("/api/home");
    const L = d.ledger, A = d.assets, D = d.dca, AI = d.ai || {};
    const m = Number(L.month.slice(5));
    const hero = L.budget > 0
      ? (L.remaining > 0 ? `${m}월은 ${L.days_left}일 남았고,<br>하루 <span class="hl">${won(L.per_day)}원</span>씩 쓸 수 있어요`
        : `${m}월 예산을 <span class="hl">${won(-L.remaining)}원</span> 넘게 썼어요`)
      : `${m}월에 <span class="hl">${won(L.expense)}원</span> 썼어요`;
    const noBudget = L.budget > 0 ? "" : `<div class="notice" style="display:flex;align-items:center;gap:7px;justify-content:space-between"><span>한 달 예산을 정하면 하루에 쓸 수 있는 돈을 알려 드려요</span><a class="btn sm dark" href="#ledger/budget" style="flex:0 0 auto">예산 정하기</a></div>`;
    const used = L.budget ? Math.min(100, (L.expense / L.budget) * 100) : 0;
    const near = L.categories.filter((c) => c.budget > 0).map((c) => ({ ...c, p: c.used / c.budget })).sort((a, b) => b.p - a.p).slice(0, 3);
    view.innerHTML = `
      ${d.feed_error ? `<div class="notice warn">${esc(d.feed_error)}</div>` : ""}
      <section class="card dom dom-ledger">
        <h1 class="hero">${hero}</h1>
        ${L.budget ? `<div class="bar thick ${L.remaining < 0 ? "over" : ""}" role="img" aria-label="예산 ${Math.round(used)}% 사용"><div style="width:${used}%"></div></div>
          <div class="kv"><div>예산<b>${won(L.budget)}</b></div><div>쓴 돈<b>${won(L.expense)}</b></div><div>남은 예산<b>${won(Math.max(0, L.remaining))}</b></div></div>` : ""}
        ${noBudget}
        ${dayBars(L)}
      </section>
      <div class="btns"><button class="btn dark" data-add="out">지출 기록</button><button class="btn" data-add="in">수입 기록</button></div>
      ${d.account && d.account.configured ? `<a class="card dom dom-invest" href="#invest/account" style="text-decoration:none;color:inherit">
        <div class="card-head"><h2>내 키움 계좌</h2><span class="tiny live-dot ${d.account.ok ? "on" : ""}">${d.account.ok ? "실시간" : "연결 확인 필요"}</span></div>
        ${d.account.total != null ? `<div class="big">${won(d.account.total)}원</div>
          <div class="small muted"><b class="${tone(d.account.pnl)}">평가손익 ${signed(d.account.pnl)}원 (${pct(d.account.pnl_pct, 2)})</b> · 예수금 ${won(d.account.cash)}원</div>`
          : `<div class="small muted">${esc(d.account.error || "")}</div>`}</a>` : ""}
      <a class="card dom dom-assets" href="#assets/overview" style="text-decoration:none;color:inherit">
        <div class="card-head"><h2>순자산</h2><span class="tiny">자세히</span></div>
        ${A.groups.length ? `<div class="big">${won(A.net)}원</div>
          ${A.change != null ? `<div class="small ${tone(A.change)}" style="font-weight:700">지난달보다 ${won(Math.abs(A.change))}원 ${A.change >= 0 ? "늘었어요" : "줄었어요"}</div>` : ""}
          ${A.history.length >= 2 ? lineChart([{ values: A.history.map((h) => h.net) }], { h: 72, label: "월별 순자산 추이" }) : ""}`
        : `<div class="small muted">계좌를 등록하면 순자산을 매달 기록해요</div>`}
      </a>
      ${payoff.homeCard(d.payoff)}
      <div id="hmAuto" class="hm-auto">${pages.invest.atHomeGroup((d.auto || {}).runs, (d.auto || {}).labs, (d.auto || {}).today)}</div>
      <a class="card dark dom-night" href="#invest/ai" style="text-decoration:none">
        <div class="card-head"><h2>오늘의 투자</h2><span class="tiny" style="color:#C5CCD6">AI 추천 ›</span></div>
        <div class="grid2">
          <div><div class="small muted">AI 자동 익절·손절</div><div class="mid">${AI.watching ? `감시 ${AI.watching}` : "대기"}</div>
            <div class="tiny" style="color:#C5CCD6">${AI.done_today ? `오늘 정리 ${AI.done_today}건 · 평균 ${pct(AI.avg_pct, 2)}` : AI.picks_today ? `오늘 추천 ${AI.picks_today}회` : "AI 탭에서 추천 받기"}</div></div>
          <div><div class="small muted">적립 다음 매수</div>${D.schedule.length ? `<div class="mid">${korDate(D.schedule[0].date, false)}</div><div class="tiny" style="color:#C5CCD6">${won(D.schedule[0].amount)}원</div>` : `<div class="small muted">플랜 없음</div>`}</div>
        </div>
      </a>
      ${near.length ? `<section class="card"><div class="card-head"><h2>예산 많이 쓴 분류</h2><a href="#ledger/budget">예산</a></div>${near.map(budgetBar).join("")}</section>` : ""}
      <section class="card tight">
        <div class="card-head" style="padding-top:8.5px"><h2>최근 내역</h2><a href="#ledger/list">전체</a></div>
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
function donut(items, total) {
  const R = 42, C = 2 * Math.PI * R, top = items.slice(0, 6), rest = items.slice(6).reduce((a, x) => a + x[1], 0);
  const all = rest ? [...top, ["그 밖", rest]] : top;
  let off = 0;
  const segs = all.map(([n, v]) => { const len = v / Math.max(1, total) * C, el = `<circle r="${R}" cx="60" cy="60" class="dn-seg ${n === "그 밖" ? "h-slate" : hue(n)}" stroke-dasharray="${Math.max(0, len - 1.5).toFixed(1)} ${C.toFixed(1)}" stroke-dashoffset="${(-off).toFixed(1)}"/>`; off += len; return el; }).join("");
  return `<div class="donut"><svg viewBox="0 0 120 120" role="img" aria-label="분류별 지출 비율"><circle r="${R}" cx="60" cy="60" class="dn-bg"/>${segs}</svg>
    <div class="dn-c"><span>이 달 지출</span><b>${bigNum(total)}</b><small>${all.length}개 분류</small></div></div>`;
}
function budgetBar(c) {
  const p = c.budget ? Math.min(100, (c.used / c.budget) * 100) : 0;
  return `<div class="cat-row ${hue(c.name)} ${c.used > c.budget * 0.9 ? "warn" : ""}">
    <div class="cat-top"><span class="cat-dot"></span><b>${esc(c.name)}</b><span class="cat-amt">${won(c.used)}<small>/ ${won(c.budget)}</small></span></div>
    <div class="cat-bar"><i style="width:${p}%"></i></div></div>`;
}
// 분류·기관 이름마다 고정 색(같은 이름은 늘 같은 색)
const HUE_FIX = { 식비: "coral", 외식: "coral", 식사: "coral", 카페: "amber", 간식: "amber", 교통: "sky", 주유: "sky", 차량: "sky",
  쇼핑: "pink", 의류: "pink", 미용: "pink", 생활: "teal", 생필: "teal", 마트: "teal", 주거: "indigo", 통신: "indigo", 공과: "indigo",
  취미: "violet", 문화: "violet", 여가: "violet", 여행: "violet", 의료: "lime", 건강: "lime", 운동: "lime", 교육: "sky", 경조: "rose",
  선물: "rose", 보험: "slate", 세금: "slate", 기타: "slate", 월급: "lime", 급여: "lime", 이자: "lime", 저축: "teal", 구독: "violet", 반복: "slate" };
const HUES = ["coral", "amber", "teal", "indigo", "violet", "sky", "pink", "lime"];
function hue(label) {
  const t = String(label || "").trim();
  for (const k in HUE_FIX) if (t.startsWith(k)) return "h-" + HUE_FIX[k];
  let h = 0; for (const ch of t) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return "h-" + HUES[h % HUES.length];
}
function txItem(t, btn = true) {
  const tag = btn ? "button" : "div";
  return `<${tag} ${btn ? `type="button" data-edit="${t.id}"` : ""} class="item">
    <span class="ico ${t.transfer ? "xf" : t.kind === "in" ? "h-in" : hue(t.category || "기타")}">${t.transfer ? "이체" : esc((t.category || "기타").slice(0, 2))}</span>
    <span class="main"><b>${esc(t.memo || t.category || "-")}</b><span class="sub">${btn ? "" : t.date.slice(5).replace("-", ".") + " · "}${t.transfer ? `내 계좌 ${t.kind === "in" ? "입금" : "출금"} · 합계 제외` : esc(t.category || "분류 없음")}${t.method && !t.transfer ? " · " + esc(t.method) : ""}</span></span>
    <span class="end"><b class="${t.transfer ? "xf-amt" : t.kind === "in" ? "inc" : ""}">${t.transfer ? "" : t.kind === "in" ? "+" : "−"}${won(t.amount)}</b></span></${tag}>`;
}


// ---------- 고정비 시트 ----------
async function recSheet(e, cards, cats) {
  cards = cards || []; cats = cats || catCache || await categories();
  const S = { kind: e.kind || "out", cycle: e.cycle || "monthly", notify: e.notify_days ?? 1, cat: e.category_id || null,
    method: e.method || (e.card_id ? "신용카드" : "자동이체"), card: e.card_id || null, auto: e.auto_record ?? 1 };
  const methods = ["자동이체", "계좌이체", "신용카드", "체크카드", "현금", "간편결제"];
  const draw = () => {
    const list = cats.filter((c) => c.kind === S.kind);
    const dayOpts = (sel) => Array.from({ length: 30 }, (_, i) => i + 1).map((n) => `<option value="${n}" ${n == sel ? "selected" : ""}>${n}일</option>`).join("") + `<option value="31" ${sel >= 31 ? "selected" : ""}>말일</option>`;
    openSheet(e.id ? "고정비 고치기" : "고정비 등록", `
      ${seg("종류", [["out", "지출"], ["in", "수입"]], S.kind, "on-card")}
      <input class="amount-in" id="rAmt" inputmode="numeric" placeholder="0원" aria-label="금액" value="${e.amount ? won(e.amount) : ""}">
      <label class="field">이름<input id="rName" placeholder="예: 넷플릭스, 월세, 통신비" value="${esc(e.name || "")}"></label>
      ${seg("주기", [["monthly", "매월"], ["yearly", "매년"], ["weekly", "매주"]], S.cycle, "on-card")}
      <div class="fields">
        ${S.cycle === "yearly" ? `<label class="field">월<select id="rMonth">${Array.from({ length: 12 }, (_, i) => `<option value="${i + 1}" ${(e.month || new Date().getMonth() + 1) == i + 1 ? "selected" : ""}>${i + 1}월</option>`).join("")}</select></label>` : ""}
        ${S.cycle === "weekly" ? `<label class="field">요일<select id="rWd">${[..."월화수목금토일"].map((w, i) => `<option value="${i}" ${(e.weekday || 0) == i ? "selected" : ""}>${w}요일</option>`).join("")}</select></label>`
          : `<label class="field">날짜<select id="rDay">${dayOpts(e.day || new Date().getDate())}</select></label>`}
      </div>
      <div class="field">분류<div class="chips">${list.map((c) => `<button type="button" class="chip" data-cat="${c.id}" aria-pressed="${c.id == S.cat}">${esc(c.name)}</button>`).join("")}</div></div>
      <div class="field">결제 수단<div class="chips">${methods.map((x) => `<button type="button" class="chip" data-m="${x}" aria-pressed="${x === S.method}">${x}</button>`).join("")}</div></div>
      ${cards.length && /카드/.test(S.method) ? `<div class="field">카드<div class="chips">${cards.map((c) => `<button type="button" class="chip" data-card="${c.id}" aria-pressed="${c.id == S.card}">${esc(c.name)}</button>`).join("")}</div></div>` : ""}
      <div class="card-head" style="min-height:36.5px"><div><b style="font-size:12px">결제일에 자동 기록</b><div class="tiny">카드·은행 문자가 오면 그 기록과 맞춰 한 번만 남겨요</div></div>
        <button class="switch" role="switch" id="rAuto" aria-checked="${!!S.auto}" aria-label="자동 기록"><span></span></button></div>
      ${seg("알림", [["-1", "안 함"], ["0", "당일"], ["1", "1일 전"], ["3", "3일 전"]], String(S.notify), "on-card")}
      <details class="field"><summary class="tiny">더 보기(문자 이름·변동 금액·기간)</summary>
        <label class="field">문자에 찍히는 이름<input id="rMatch" placeholder="비우면 위 이름" value="${esc(e.match && e.match !== e.name ? e.match : "")}"></label>
        <label class="check"><input type="checkbox" id="rVar" ${e.variable ? "checked" : ""}> 금액이 달마다 조금씩 달라요(통신비·관리비)</label>
        <div class="fields"><label class="field">시작일<input type="date" id="rStart" value="${esc(e.start_date || localToday())}"></label>
          <label class="field">종료일<input type="date" id="rEnd" value="${esc(e.end_date || "")}"></label></div>
        ${e.id ? `<label class="check"><input type="checkbox" id="rActive" ${e.active === 0 ? "" : "checked"}> 사용 중</label>` : ""}
      </details>
      <button class="btn dark wide" id="rSave">${e.id ? "고친 내용 저장" : "등록"}</button>
      ${e.id ? `<button class="btn soft wide" id="rDel">지우기</button>` : ""}`, (box) => {
      moneyInput($("#rAmt", box));
      const keep = () => { e = { ...e, amount: num($("#rAmt").value), name: $("#rName").value, day: $("#rDay")?.value || e.day, month: $("#rMonth")?.value || e.month, weekday: $("#rWd")?.value ?? e.weekday }; };
      bindSeg(box, "종류", (v) => { keep(); S.kind = v; S.cat = null; draw(); });
      bindSeg(box, "주기", (v) => { keep(); S.cycle = v; draw(); });
      bindSeg(box, "알림", (v) => { S.notify = Number(v); });
      $("#rAuto").onclick = (ev) => { S.auto = ev.currentTarget.getAttribute("aria-checked") !== "true"; ev.currentTarget.setAttribute("aria-checked", String(S.auto)); };
      box.onclick = async (ev) => {
        const c = ev.target.closest("[data-cat]"), m = ev.target.closest("[data-m]"), k = ev.target.closest("[data-card]");
        if (c) { S.cat = Number(c.dataset.cat); $$("[data-cat]", box).forEach((x) => x.setAttribute("aria-pressed", String(x === c))); }
        if (m) { keep(); S.method = m.dataset.m; if (!/카드/.test(S.method)) S.card = null; draw(); }
        if (k) { S.card = Number(k.dataset.card); $$("[data-card]", box).forEach((x) => x.setAttribute("aria-pressed", String(x === k))); }
        if (ev.target.id === "rDel") {
          if (!confirmTap(ev.target, "한 번 더 누르면 지워요")) return;
          await run(() => api("DELETE", `/api/fixed/${e.id}`), "지웠어요"); closeSheet(); pages.ledger.fixed();
        }
        if (ev.target.id === "rSave") {
          const b = { id: e.id, kind: S.kind, amount: num($("#rAmt").value), name: $("#rName").value, cycle: S.cycle,
            day: num($("#rDay")?.value || 1), month: num($("#rMonth")?.value || 0) || null, weekday: num($("#rWd")?.value || 0),
            category_id: S.cat, method: S.method, card_id: S.card, auto_record: S.auto, notify_days: S.notify,
            match: $("#rMatch").value.trim() || $("#rName").value.trim(), variable: $("#rVar").checked,
            start_date: $("#rStart").value || localToday(), end_date: $("#rEnd").value || null, active: $("#rActive") ? $("#rActive").checked : true };
          await run(() => api("POST", "/api/fixed", b), e.id ? "고쳤어요" : "등록했어요. 결제일에 맞춰 챙길게요");
          closeSheet(); if (location.hash.startsWith("#ledger/fixed")) pages.ledger.fixed();
        }
      };
    });
  };
  draw();
}

// ---------- 카드 ----------
const ISSUER_COLORS = { "신한": ["#1A47A6", "#0E2E73"], "KB국민": ["#6B5B45", "#3F352A"], "삼성": ["#1C3FAA", "#0B1F66"], "현대": ["#2B2B2B", "#0F0F0F"],
  "롯데": ["#D0342C", "#8F1D18"], "하나": ["#00877A", "#00564E"], "우리": ["#0A6CB5", "#064775"], "BC": ["#E04848", "#9E2626"],
  "NH농협": ["#1D9A55", "#106338"], "씨티": ["#3D6DB5", "#22446F"], "IBK": ["#1F5FA8", "#123A68"], "카카오뱅크": ["#3A3A3A", "#1A1A1A"], "토스": ["#2F6FEB", "#1A44A0"] };
function nextPayInfo(c) {  // 화면 미리보기: 다음 결제일과 그때 나가는 이용기간
  const t = new Date(); t.setHours(0, 0, 0, 0);
  const dim = (y, m) => new Date(y, m + 1, 0).getDate();
  const at = (y, m, d) => new Date(y, m, Math.min(d, dim(y, m)));
  let p = at(t.getFullYear(), t.getMonth(), c.pay_day); if (p < t) p = at(t.getFullYear(), t.getMonth() + 1, c.pay_day);
  for (const back of [1, 2, 3]) {
    const s = at(p.getFullYear(), p.getMonth() - back, c.cycle_start), e = new Date(at(p.getFullYear(), p.getMonth() - back + 1, c.cycle_start) - 864e5);
    const d8 = new Date(+e + 8 * 864e5); let q = at(d8.getFullYear(), d8.getMonth(), c.pay_day); if (q < d8) q = at(d8.getFullYear(), d8.getMonth() + 1, c.pay_day);
    if (+q === +p) return { p, s, e };
  }
  return null;
}
const md = (d) => `${d.getMonth() + 1}/${d.getDate()}`;
function cardPlate(c) {
  const [a, b] = ISSUER_COLORS[c.issuer] || ["#2A3550", "#161D2E"];
  const b0 = c.bills[0], b1 = c.bills[1];
  const pct = c.perf_pct == null ? null : Math.min(100, c.perf_pct);
  return `<section class="card cd-card">
    <button type="button" class="cd-plate" data-card="${c.id}" style="--c1:${a};--c2:${b}">
      <span class="cd-top"><b>${esc(c.name)}</b><span>${esc(c.kind_label)}</span></span>
      <span class="cd-no">${c.last4 ? `**** ${esc(c.last4)}` : esc(c.issuer || "")}</span>
      ${b0 && c.kind === "credit" ? `<span class="cd-bill"><span>${esc(b0.pay_date.slice(5).replace("-", "/"))} 결제 · ${b0.d === 0 ? "오늘" : `D-${b0.d}`}</span><b>${won(b0.amount)}원</b></span>` : `<span class="cd-bill"><span>이번 달 사용</span><b>${won(c.perf)}원</b></span>`}
    </button>
    ${c.perf_target ? `<div class="cd-perf">
      <div class="cd-row"><span>전월 실적(이번 달)</span><b>${won(c.perf)} <small>/ ${won(c.perf_target)}</small></b></div>
      <div class="bar ${pct >= 100 ? "done" : ""}"><div style="width:${pct}%"></div></div>
      <div class="tiny">${c.perf_need ? `<b>${won(c.perf_need)}원</b> 더 쓰면 다음 달 혜택 · 남은 ${c.days_left}일, 하루 ${won(c.perf_daily)}원` : "이번 달 실적 채웠어요. 다음 달 혜택 받아요"}
        ${c.prev_perf ? ` · 지난달 ${c.prev_met ? `<span class="up">달성</span>` : `<span class="down">미달(${won(c.prev_perf)})</span>`}` : ""}</div></div>` : ""}
    ${c.kind === "credit" && b0 ? `<div class="cd-bills">
      <div><span>${esc(b0.pay_date.slice(5).replace("-", "/"))} 결제</span><span>${esc(b0.start.slice(5).replace("-", "/"))}~${esc(b0.end.slice(5).replace("-", "/"))} 이용분 ${b0.closed ? "· 마감" : "· 쓰는 중"} · ${b0.count}건</span><b>${won(b0.amount)}</b></div>
      ${b1 ? `<div><span>${esc(b1.pay_date.slice(5).replace("-", "/"))} 결제</span><span>${esc(b1.start.slice(5).replace("-", "/"))}~${esc(b1.end.slice(5).replace("-", "/"))} 이용분</span><b>${won(b1.amount)}</b></div>` : ""}</div>` : ""}
    <div class="cd-row tiny"><span>${c.annual_fee ? `연회비 ${won(c.annual_fee)}원` : ""}${c.note ? ` ${esc(c.note)}` : ""}</span><button class="link" data-cedit="${c.id}">카드 설정</button></div>
  </section>`;
}
async function cardDetail(c, issuers) {
  const b0 = c.bills[0];
  const start = c.kind === "credit" && b0 ? b0.start : localToday().slice(0, 8) + "01", end = c.kind === "credit" && b0 ? b0.end : localToday();
  const rows = await run(() => GET(`/api/cards/${c.id}/txs?start=${start}&end=${end}`));
  openSheet(c.name, `
    <div class="small muted">${esc(start.slice(5).replace("-", "/"))}~${esc(end.slice(5).replace("-", "/"))} ${c.kind === "credit" ? `이용분 · ${esc(b0.pay_date.slice(5).replace("-", "/"))} 결제` : "사용"} · ${rows.length}건 · <b>${won(rows.reduce((a, t) => a + (t.kind === "out" ? t.amount : -t.amount), 0))}원</b></div>
    <div class="list">${rows.length ? rows.map((t) => txItem(t, false)).join("") : `<div class="empty">이 기간 내역이 없어요</div>`}</div>
    <button class="btn wide" id="cdEdit">카드 설정</button>`, (box) => { $("#cdEdit", box).onclick = () => cardSheet(c, issuers); });
}
function cardSheet(c, issuers) {
  const S = { kind: c.kind || "credit", issuer: c.issuer || "" };
  const draw = () => {
    openSheet(c.id ? "카드 설정" : "카드 등록", `
      <label class="field">이름<input id="kName" placeholder="예: 신한 딥드림" value="${esc(c.name || "")}"></label>
      <div class="field">카드사<div class="chips">${(issuers || []).map((x) => `<button type="button" class="chip" data-iss="${esc(x)}" aria-pressed="${x === S.issuer}">${esc(x)}</button>`).join("")}</div></div>
      <div class="fields"><label class="field">끝 4자리<input id="kLast" inputmode="numeric" maxlength="4" placeholder="문자에 찍히는 번호" value="${esc(c.last4 || "")}"></label>
        <label class="field">종류<select id="kKind"><option value="credit" ${S.kind === "credit" ? "selected" : ""}>신용카드</option><option value="check" ${S.kind === "check" ? "selected" : ""}>체크카드</option></select></label></div>
      <label class="field">전월 실적 기준(원)<input id="kPerf" class="r" inputmode="numeric" placeholder="0이면 실적 없음" value="${c.perf_target ? won(c.perf_target) : ""}"></label>
      <div class="fields" id="kBillBox">
        <label class="field">이용기간 시작일<select id="kStart">${Array.from({ length: 28 }, (_, i) => `<option value="${i + 1}" ${(c.cycle_start || 1) == i + 1 ? "selected" : ""}>${i + 1}일</option>`).join("")}</select></label>
        <label class="field">결제일<select id="kPay">${Array.from({ length: 28 }, (_, i) => `<option value="${i + 1}" ${(c.pay_day || 14) == i + 1 ? "selected" : ""}>${i + 1}일</option>`).join("")}</select></label></div>
      <div class="notice" id="kPrev"></div>
      <label class="field">실적 제외 항목(쉼표로)<input id="kEx" placeholder="예: 국세, 상품권, 관리비" value="${esc(c.exclude_kw || "")}"></label>
      <div class="fields"><label class="field">연회비<input id="kFee" class="r" inputmode="numeric" value="${c.annual_fee ? won(c.annual_fee) : ""}"></label>
        <label class="field">메모<input id="kNote" placeholder="예: 통신비 할인" value="${esc(c.note || "")}"></label></div>
      <button class="btn dark wide" id="kSave">${c.id ? "저장" : "등록"}</button>
      ${c.id ? `<button class="btn soft wide" id="kDel">카드 지우기</button>` : ""}`, (box) => {
      ["#kPerf", "#kFee"].forEach((q) => moneyInput($(q, box)));
      const prev = () => {
        const credit = $("#kKind").value === "credit";
        $("#kBillBox").hidden = !credit;
        const info = credit ? nextPayInfo({ cycle_start: num($("#kStart").value), pay_day: num($("#kPay").value) }) : null;
        $("#kPrev").innerHTML = credit ? (info ? `다음 결제 <b>${md(info.p)}</b>에는 <b>${md(info.s)}~${md(info.e)}</b> 사용분이 나가요. 카드사 앱의 이용기간과 같은지 확인해 주세요.` : "")
          : "체크카드는 쓰는 즉시 통장에서 빠져서 결제일이 없어요. 실적만 계산해요.";
        $("#kPrev").hidden = !$("#kPrev").innerHTML;
      };
      ["#kStart", "#kPay", "#kKind"].forEach((q) => ($(q, box).onchange = prev)); prev();
      box.onclick = async (ev) => {
        const i = ev.target.closest("[data-iss]");
        if (i) { S.issuer = i.dataset.iss; $$("[data-iss]", box).forEach((x) => x.setAttribute("aria-pressed", String(x === i))); if (!$("#kName").value) $("#kName").value = S.issuer + "카드"; }
        if (ev.target.id === "kDel") {
          if (!confirmTap(ev.target, "한 번 더 누르면 지워요")) return;
          await run(() => api("DELETE", `/api/cards/${c.id}`), "지웠어요"); closeSheet(); pages.ledger.cards();
        }
        if (ev.target.id === "kSave") {
          const b = { id: c.id, name: $("#kName").value, issuer: S.issuer, last4: $("#kLast").value, kind: $("#kKind").value,
            perf_target: num($("#kPerf").value), cycle_start: num($("#kStart").value), pay_day: num($("#kPay").value),
            exclude_kw: $("#kEx").value, annual_fee: num($("#kFee").value), note: $("#kNote").value };
          if (!b.issuer) { toast("카드사를 골라 주세요", true); return; }
          await run(() => api("POST", "/api/cards", b), c.id ? "저장했어요" : "등록했어요. 지난 카드 문자도 이 카드에 연결했어요");
          cardCache = null; closeSheet(); if (location.hash.startsWith("#ledger/cards")) pages.ledger.cards();
        }
      };
    });
  };
  draw();
}
let cardCache = null;

// ---------- 내역 입력 시트 ----------
async function txSheet(e, done) {
  const cats = await run(() => categories());
  if (!cardCache) cardCache = await GET("/api/cards/simple").catch(() => []);
  let card = e.card_id || null;
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
      <div class="field">결제수단<div class="chips" role="group" aria-label="결제수단">${methods.map((x) => `<button type="button" class="chip" data-m="${x}" aria-pressed="${x === method}">${x}</button>`).join("")}</div></div>
      ${cardCache.length && /카드/.test(method) ? `<div class="field">카드<div class="chips" role="group" aria-label="카드">${cardCache.map((c) => `<button type="button" class="chip" data-tcard="${c.id}" aria-pressed="${c.id == card}">${esc(c.name)}</button>`).join("")}</div></div>` : ""}`}
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
        if (m) { method = m.dataset.m; if (!/카드/.test(method)) card = null; e = { ...e, amount: num($("#tAmt").value), memo: $("#tMemo").value, date: $("#tDate").value, category_id: cat }; draw(); return; }
        const tc = ev.target.closest("[data-tcard]");
        if (tc) { card = card == tc.dataset.tcard ? null : Number(tc.dataset.tcard); $$("[data-tcard]", box).forEach((x) => x.setAttribute("aria-pressed", String(x.dataset.tcard == card))); }
        if (ev.target.id === "tDel") {
          if (!confirmTap(ev.target, "한 번 더 누르면 지워요")) return;
          await run(() => api("DELETE", `/api/tx/${e.id}`), "지웠어요"); closeSheet(); done && done();
        }
        if (ev.target.id === "tSave") {
          const body = { kind, amount: num($("#tAmt").value), memo: $("#tMemo").value, category_id: xfer ? null : cat, method, date: $("#tDate").value, transfer: xfer, card_id: /카드/.test(method) ? card : null };
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
  const tot = Math.max(1, sum.income + sum.expense);
  return `<section class="card mnav">
    <div class="mnav-head">
      <button class="mnav-btn" data-mon="-1" aria-label="이전 달"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 18l-6-6 6-6"/></svg></button>
      <h2><small>${y}년</small>${m}월</h2>
      <button class="mnav-btn" data-mon="1" aria-label="다음 달"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg></button>
    </div>
    <div class="mnav-kv">
      <div class="k-in"><span>수입</span><b>+${won(sum.income)}</b></div>
      <div class="k-out"><span>지출</span><b>−${won(sum.expense)}</b></div>
      <div class="k-net ${sum.net < 0 ? "neg" : ""}"><span>남은 돈</span><b>${won(sum.net)}</b></div></div>
    <div class="mnav-ratio" aria-hidden="true"><i class="ri" style="flex:${sum.income / tot}"></i><i class="ro" style="flex:${sum.expense / tot}"></i></div>
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
    if (/^\d{4}-\d{2}$/.test(h.params.get("m") || "")) { S.month = h.params.get("m"); S.kind = "all"; }
    setTop("가계부", { menu: "ledger", sub: h.sub });
    if (h.params.get("budget")) { history.replaceState(null, "", "#ledger/budget"); h.sub = "budget"; lastSub.ledger = "budget"; setTop("가계부", { menu: "ledger", sub: "budget" }); }
    const kindQ = h.sub === "list" && S.kind !== "all" ? "&kind=" + S.kind : "";
    const d = await GET(`/api/ledger?month=${S.month}${kindQ}`);
    catCache = d.categories;
    this.d = d;
    const addFab = () => fab("기록", () => txSheet({ kind: S.kind === "in" ? "in" : "out", date: S.month === thisMonth() ? localToday() : S.month + "-01" }, (b) => { if (b && b.date.slice(0, 7) !== S.month) S.month = b.date.slice(0, 7); route(); }));
    if (h.sub === "auto") return this.auto();
    if (h.sub === "fixed") return this.fixed();
    if (h.sub === "cards") return this.cards();
    if (h.sub === "calendar") { this.calendar(d); addFab(); }
    else if (h.sub === "analysis") await this.analysis(d);
    else if (h.sub === "budget") this.budget(d);
    else { this.list(d); addFab(); }
  },
  list(d) {
    const S = ledgerState;
    const byDay = {};
    d.rows.forEach((t) => (byDay[t.date] = byDay[t.date] || []).push(t));
    const up = (d.fixed_soon || []);
    view.innerHTML = `${monthNav(S, d.summary)}
      ${up.length ? `<a class="fx-banner" href="#ledger/fixed"><span class="fx-d">${up[0].d === 0 ? "오늘" : up[0].d === 1 ? "내일" : `D-${up[0].d}`}</span>
        <span><b>${esc(up[0].name)}</b> ${won(up[0].amount)}원 결제 예정${up.length > 1 ? ` 외 ${up.length - 1}건` : ""}</span><span class="fx-go">›</span></a>` : ""}
      ${seg("내역 종류", [["all", "전체"], ["out", "지출"], ["in", "수입"], ["transfer", "이체"]], S.kind)}
      <section class="card tight">
        ${Object.keys(byDay).length ? Object.entries(byDay).map(([date, rows]) => {
          const out = rows.filter((r) => r.kind === "out" && !r.transfer).reduce((a, r) => a + r.amount, 0);
          return `<div class="day-head"><span>${korDate(date)}</span><span>${out ? "−" + won(out) : ""}</span></div><div class="list">${rows.map((t) => txItem(t)).join("")}</div>`;
        }).join("") : `<div class="empty">이 달에는 내역이 없어요</div>`}
      </section>`;
    bindSeg(view, "내역 종류", (v) => { S.kind = v; route(); });
    view.onclick = (e) => { const b = e.target.closest("[data-edit]"); if (b) txSheet({ ...d.rows.find((r) => r.id == b.dataset.edit) }, route); };
  },
  // ---------- 고정비·구독 ----------
  async fixed() {
    const d = await GET("/api/fixed");
    const sm = d.summary, cats = catCache || await categories();
    const pctPaid = sm.month_total ? Math.round(sm.month_paid / sm.month_total * 100) : 0;
    const stTag = { paid: ["냈어요", "g"], posted: ["자동 기록", "g"], skipped: ["건너뜀", ""], due: ["예정", ""], late: ["확인 필요", "warn"] };
    const dLabel = (x) => x.d === 0 ? "오늘" : x.d === 1 ? "내일" : x.d > 0 ? `D-${x.d}` : `${-x.d}일 지남`;
    view.innerHTML = `
      <section class="card fx-sum">
        <div class="card-head"><h2>이번 달 고정 지출</h2><span class="tiny">${d.items.filter((x) => x.active).length}개 등록</span></div>
        <div class="fx-big">${won(sm.month_total)}<small>원</small></div>
        <div class="bar"><div style="width:${pctPaid}%"></div></div>
        <div class="kv"><div>낸 것<b>${won(sm.month_paid)}</b></div><div>남은 것<b>${won(sm.month_left)}</b></div><div>1년 환산<b>${shortWon(sm.yearly)}</b></div></div>
      </section>
      ${d.suggest.length ? `<div class="sec-title">반복 결제를 찾았어요 · 눌러서 등록</div>
        <section class="card tight"><div class="list">${d.suggest.map((x, i) => `<div class="item fx-sug">
          <span class="ico ${x.subscription ? "h-violet" : "h-slate"}">${x.subscription ? "구독" : "반복"}</span>
          <span class="main"><b>${esc(x.name)}</b><span class="sub">매월 ${x.day}일쯤 · ${x.months >= 2 ? `${x.months}달 연속` : "구독으로 보여요"}${x.variable ? " · 금액 조금씩 달라요" : ""}</span></span>
          <span class="end"><b>${won(x.amount)}</b><span class="fx-btns"><button class="btn sm" data-sadd="${i}">등록</button><button class="btn sm soft" data-sno="${i}">아니에요</button></span></span></div>`).join("")}</div></section>` : ""}
      <div class="sec-title">다가오는 결제</div>
      <section class="card tight"><div class="list">${sm.upcoming.length ? sm.upcoming.map((x) => `<div class="item" data-rid="${x.id}">
        <span class="fx-dd ${x.d <= 1 && x.d >= 0 ? "soon" : x.d < 0 ? "past" : ""}">${dLabel(x)}</span>
        <span class="main"><b>${esc(x.name)}</b><span class="sub">${esc(x.due.slice(5).replace("-", "."))}${x.card ? " · " + esc(x.card) : ""}${x.auto ? "" : " · 직접 기록"}</span></span>
        <span class="end"><b class="${x.kind === "in" ? "inc" : ""}">${x.kind === "in" ? "+" : ""}${won(x.amount)}</b>
          ${x.status === "late" ? `<span class="fx-btns"><button class="btn sm" data-mark="paid" data-due="${x.due}" data-id="${x.id}">냈어요</button><button class="btn sm soft" data-mark="skipped" data-due="${x.due}" data-id="${x.id}">건너뛰기</button></span>`
            : `<span class="tag ${stTag[x.status][1]}">${stTag[x.status][0]}</span>`}</span></div>`).join("")
        : `<div class="empty">한 달 안에 예정된 결제가 없어요</div>`}</div></section>
      <div class="sec-title">등록한 고정비</div>
      <section class="card tight"><div class="list">${d.items.length ? d.items.map((r) => `<button type="button" class="item ${r.active ? "" : "off"}" data-rid="${r.id}">
        <span class="ico ${hue(r.category || r.name)}">${esc((r.category || r.name).slice(0, 2))}</span>
        <span class="main"><b>${esc(r.name)}</b><span class="sub">${esc(r.when)}${r.card ? " · " + esc(r.card) : r.method ? " · " + esc(r.method) : ""}${r.auto_record ? " · 자동 기록" : ""}${r.notify_days >= 0 ? " · 알림" : ""}${r.active ? "" : " · 멈춤"}</span></span>
        <span class="end"><b class="${r.kind === "in" ? "inc" : ""}">${r.variable ? "약 " : ""}${won(r.amount)}</b></span></button>`).join("")
        : `<div class="empty">월세·통신비·구독처럼 매달 나가는 돈을 등록하면, 그날 자동으로 기록하고 미리 알려 드려요</div>`}</div></section>
      <div class="tiny" style="padding:0 3px">카드로 내는 고정비는 카드 문자가 오면 그 기록과 맞춰 한 번만 남겨요. 문자가 없으면 결제일 3일 뒤 자동 기록해요. 알림은 설정 → 매매의 휴대폰 알림(ntfy)으로 가요.</div>`;
    fab("고정비", () => recSheet({}, d.cards, cats));
    view.onclick = async (e) => {
      const add = e.target.closest("[data-sadd]"), no = e.target.closest("[data-sno]"), mk = e.target.closest("[data-mark]");
      if (add) { const x = d.suggest[add.dataset.sadd]; return recSheet({ name: x.name, amount: x.amount, day: x.day, category_id: x.category_id, method: x.method, card_id: x.card_id, variable: x.variable, match: x.name, auto_record: true }, d.cards, cats); }
      if (no) { await run(() => api("POST", "/api/fixed/dismiss", { key: d.suggest[no.dataset.sno].key }), "다시 제안하지 않을게요"); return this.fixed(); }
      if (mk) { await run(() => api("POST", `/api/fixed/${mk.dataset.id}/mark`, { due: mk.dataset.due, status: mk.dataset.mark }), mk.dataset.mark === "paid" ? "낸 것으로 표시했어요" : "이번엔 건너뛸게요"); return this.fixed(); }
      const it = e.target.closest("[data-rid]");
      if (it) recSheet(d.items.find((r) => r.id == it.dataset.rid), d.cards, cats);
    };
  },

  // ---------- 카드 ----------
  async cards() {
    const d = await GET("/api/cards");
    view.innerHTML = `
      ${d.cards.some((c) => c.kind === "credit") ? `<section class="card"><div class="card-head"><h2>다음 카드 결제 예정</h2><b>${won(d.next_due_total)}원</b></div>
        <div class="tiny">신용카드별 가장 가까운 결제일에 나갈 금액 합계(지금까지 쓴 만큼)예요.</div></section>` : ""}
      ${d.suggest.length ? `<div class="sec-title">문자에서 찾은 카드 · 눌러서 등록</div>
        <section class="card tight"><div class="list">${d.suggest.map((x, i) => `<div class="item"><span class="ico y">${esc(x.issuer.slice(0, 2))}</span>
          <span class="main"><b>${esc(x.issuer)}카드${x.last4 ? ` ****${esc(x.last4)}` : ""}</b><span class="sub">문자 ${x.count}건 · 최근 ${esc(String(x.last).slice(5).replace("-", "."))}</span></span>
          <button class="btn sm" data-csug="${i}">등록</button></div>`).join("")}</div></section>` : ""}
      ${d.cards.length ? d.cards.map((c) => cardPlate(c)).join("") : `<div class="empty card">카드를 등록하면 전월 실적 진행률과 결제일·청구 예정액을 보여 드려요. 카드 문자가 오면 자동으로 이 카드에 연결돼요.</div>`}`;
    fab("카드", () => cardSheet({}, d.issuers));
    view.onclick = (e) => {
      const sg = e.target.closest("[data-csug]");
      if (sg) { const x = d.suggest[sg.dataset.csug]; return cardSheet({ name: `${x.issuer}카드`, issuer: x.issuer, last4: x.last4 }, d.issuers); }
      const ed = e.target.closest("[data-cedit]");
      if (ed) return cardSheet(d.cards.find((c) => c.id == ed.dataset.cedit), d.issuers);
      const pl = e.target.closest("[data-card]");
      if (pl) cardDetail(d.cards.find((c) => c.id == pl.dataset.card), d.issuers);
    };
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
    const omax = Math.max(1, ...Object.values(out));
    for (let i = 0; i < first; i++) cells += `<div class="cal-cell empty-cell"></div>`;
    for (let day = 1; day <= ndays; day++) {
      const iso = `${S.month}-${String(day).padStart(2, "0")}`;
      const wd = (first + day - 1) % 7;
      cells += `<button type="button" class="cal-cell ${iso === today ? "today" : ""} ${iso > today ? "future" : ""} ${out[day] ? "spent" : ""} ${wd === 0 ? "sun" : wd === 6 ? "sat" : ""}" style="--heat:${((out[day] || 0) / omax).toFixed(2)}" data-day="${iso}" aria-label="${korDate(iso)} 지출 ${won(out[day] || 0)}원">
        <span class="cal-d">${day}</span>${out[day] ? `<span class="cal-out">−${shortWon(out[day])}</span>` : ""}${inc[day] ? `<span class="cal-in">+${shortWon(inc[day])}</span>` : ""}</button>`;
    }
    const spentDays = Object.keys(out).length;
    view.innerHTML = `${monthNav(S, d.summary)}
      <section class="card cal">
        <div class="cal-grid cal-wd">${[..."일월화수목금토"].map((w, i) => `<span class="${i === 0 ? "sun" : i === 6 ? "sat" : ""}">${w}</span>`).join("")}</div>
        <div class="cal-grid">${cells}</div>
        <div class="cal-foot"><span class="cal-chip c-spent"><i></i>지출한 날 <b>${spentDays}일</b></span><span class="cal-chip c-zero"><i></i>무지출 <b>${Math.max(0, (S.month === thisMonth() ? Number(today.slice(8)) : ndays) - spentDays)}일</b></span>
          <span class="cal-scale" aria-hidden="true">적게<i></i>많이</span></div>
        <div class="tiny">날짜를 누르면 그날 내역이 보여요</div>
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
        <div class="trend" role="img" aria-label="최근 6개월 수입과 지출">${trend.map((t) => `<div class="tcol ${t.month === S.month ? "cur" : ""}">
          <em>${t.expense ? bigNum(t.expense) : ""}</em>
          <div class="tbars"><i class="ti" style="height:${(t.income / tmax) * 100}%"></i><i class="to" style="height:${(t.expense / tmax) * 100}%"></i></div>
          <span>${Number(t.month.slice(5))}월</span></div>`).join("")}</div>
        <div class="legend tiny"><span><i class="sw" style="background:var(--in)"></i>수입</span><span><i class="sw" style="background:var(--coral)"></i>지출</span><span style="margin-left:auto">막대 위 숫자는 지출</span></div>
      </section>
      <section class="card"><h2>분류별 지출</h2>
        ${cats.length ? `${donut(cats.map((c) => [c.name, c.used]), sum.expense)}
        <div class="cat-rows">${cats.map((c) => `<div class="cat-row ${hue(c.name)}">
          <div class="cat-top"><span class="cat-dot"></span><b>${esc(c.name)}</b><span class="cat-amt">${won(c.used)}<small>${Math.round((c.used / Math.max(1, sum.expense)) * 100)}%</small></span></div>
          <div class="cat-bar"><i style="width:${(c.used / maxc) * 100}%"></i></div></div>`).join("")}</div>` : `<div class="empty">이 달 지출이 없어요</div>`}
      </section>
      <section class="card"><h2>결제수단별</h2>
        ${methods.length ? `<div class="stack">${methods.map(([k, v], i) => `<i class="${hue(k)}" style="flex:${v}" title="${esc(k)}"></i>`).join("")}</div>
        ${methods.map(([k, v]) => `<div class="m-row ${hue(k)}"><span class="cat-dot"></span><span>${esc(k)}</span><b>${won(v)}</b><small>${Math.round((v / Math.max(1, sum.expense)) * 100)}%</small></div>`).join("")}` : `<div class="empty">이 달 지출이 없어요</div>`}
      </section>
      <section class="card tight"><div class="card-head" style="padding-top:8.5px"><h2>큰 지출 TOP 5</h2></div>
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
      <span class="ico ${x.status === "pending" ? "warn" : hue(x.issuer || x.kind)}">${esc((x.issuer || (x.kind === "card" ? "카드" : "은행")).slice(0, 2))}</span>
      <span class="main"><b>${esc(x.merchant || label[x.action])}</b><span class="sub">${x.transfer ? "내 계좌 이체" : esc(label[x.action])} · ${esc(x.date.slice(5).replace("-", "."))} ${esc(x.time || "")}${x.note ? " · " + esc(x.note) : ""}</span></span>
      <span class="end"><b class="${x.transfer ? "xf-amt" : x.action === "income" || x.action === "cancel" ? "inc" : ""}">${x.transfer ? "" : x.action === "income" || x.action === "cancel" ? "+" : "−"}${won(x.amount)}</b><span class="tag ${x.transfer ? "" : st[x.status][1]}">${x.transfer ? "이체" : st[x.status][0]}</span></span></button>`;
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
          <div class="card-head" style="min-height:36.5px"><div><b style="font-size:12px">${t}</b><div class="tiny">${desc}</div></div>
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
        <label class="field">카톡 내보내기 파일(.txt)<input type="file" id="ibFile" accept=".txt,text/plain" style="padding:8.5px"></label>
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
          <div class="big" style="font-size:21px">${x.action === "income" || x.action === "cancel" ? "+" : "−"}${won(x.amount)}원</div>
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
          return `<div style="display:flex;flex-direction:column;gap:4px;padding:4px 0;border-top:1px solid var(--bg)">
          <label class="field" style="flex-direction:row;align-items:center;justify-content:space-between;gap:8.5px">
            <span style="font-size:12px;color:var(--ink)">${esc(c.name)}<br><span class="tiny">이번 달 ${won(u)}원 썼어요</span></span>
            <input class="r" style="max-width:105px" inputmode="numeric" data-budget="${c.id}" data-name="${esc(c.name)}" value="${won(c.budget)}"></label>
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
        ${d.goal > 0 ? `<div class="big" style="font-size:21px">${Math.max(0, Math.round((d.net / d.goal) * 100))}%</div>
          <div class="small muted">순자산 <b style="color:var(--ink)">${won(d.goal)}원</b>까지 ${d.net >= d.goal ? "<b style='color:var(--ink)'>도달했어요</b>" : `<b style="color:var(--ink)">${won(d.goal - d.net)}원</b> 남았어요`}</div>
          <div class="bar thick"><div style="width:${Math.max(0, Math.min(100, (d.net / d.goal) * 100))}%;background:var(--hl);box-shadow:inset -2px 0 0 var(--ink)"></div></div>`
        : `<div class="small muted">목표 순자산을 정하면 얼마나 왔는지 보여드려요</div>`}
      </section>`;
    const groupsHtml = `
      ${d.groups.map((g) => `<div class="sec-title">${esc(g.label)} · ${g.type === "debt" ? "-" : ""}${won(g.total)}원</div>
        <section class="card tight"><div class="list">${g.items.map((a) => `<button type="button" class="item" data-edit="${a.id}">
          <span class="ico ${hue(g.label)}">${esc(g.label.slice(0, 2))}</span>
          <span class="main"><b>${esc(a.name)}</b><span class="sub">${a.linked ? ({ scalp: "기본 계좌와 연결", dca: "장기 계좌와 연결", kiwoom: "내 키움 계좌와 연결" }[a.linked]) : esc(a.note || "직접 입력")}${a.sync_error ? " · 연결 실패" : ""}</span></span>
          <span class="end"><b>${g.type === "debt" ? "-" : ""}${won(a.balance)}</b></span></button>`).join("")}</div></section>`).join("")
        || `<section class="card"><div class="empty">통장, 예적금, 연금, 대출을 등록하면<br>순자산을 계산해요</div></section>`}`;
    if (h.sub === "accounts") { view.innerHTML = groupsHtml; fab("계좌", () => this.sheet(d, null)); }
    else if (h.sub === "goal") view.innerHTML = `<div id="pfBox"></div>` + goalCard + `<section class="card"><h2>지금 순자산</h2><div class="big" style="font-size:21px">${won(d.net)}원</div>
        ${d.history.length >= 2 ? lineChart([{ values: d.history.map((x) => x.net) }], { h: 90, label: "월별 순자산" }) + `<div class="axis"><span>${esc(d.history[0].month)}</span><span>${esc(d.history[d.history.length - 1].month)}</span></div>` : `<div class="tiny">매달 순자산을 기록해 추이를 그려요</div>`}</section>`;
    else view.innerHTML = summaryCard + `<div class="sec-title">종류별</div><section class="card tight"><div class="list">${d.groups.map((g) => `<a class="item" href="#assets/accounts" style="text-decoration:none">
        <span class="ico ${hue(g.label)}">${esc(g.label.slice(0, 2))}</span><span class="main"><b>${esc(g.label)}</b><span class="sub">${g.items.length}개 계좌</span></span>
        <span class="end"><b>${g.type === "debt" ? "-" : ""}${won(g.total)}</b></span></a>`).join("") || `<div class="empty">계좌 탭에서 통장·예적금·대출을 등록해 보세요</div>`}</div></section>` + goalCard;
    if ($("#pfBox")) payoff.mount($("#pfBox")).catch(() => {});
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
        <option value="scalp" ${e?.linked === "scalp" ? "selected" : ""}>기본 계좌 평가금 자동 반영</option>
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
const investState = { tab: "home" };
pages.invest = {
  every: 2500,
  async mount(h) {
    const sub = h.sub || investState.tab;
    if (sub === "backtest") return this.backtest();
    if (sub === "rules") return this.rules();
    if (sub === "sweep") return this.sweep();
    if (sub === "stock") return mkt.detail(h);
    if (sub === "aistat") return this.aiStat();
    investState.tab = ["home", "ai", "market", "discover", "account", "trade", "dca", "dividend", "auto", "atlog", "evo"].includes(sub) ? sub : sub === "scalp" ? "ai" : "home";
    const tabs = groupTabs("invest", investState.tab);
    setTop("투자", { menu: "invest", sub: investState.tab });
    view.innerHTML = `${tabs}<div id="iv" style="display:flex;flex-direction:column;gap:8.5px"></div>`;
    await this.fill();
  },
  async refresh(h) {
    if (["backtest", "rules"].includes(h.sub)) return;
    if (h.sub === "stock") return mkt.refreshDetail();
    if (h.sub === "aistat") return;
    if (h.sub === "sweep") return this.sweepPoll();
    if (investState.tab === "ai") { this.apSess(); if (!this._apT || Date.now() - this._apT > 5000) { this._apT = Date.now(); this.apGuards(); } return; }
    if (["dividend", "atlog", "evo"].includes(investState.tab)) return;  // 입력·읽는 화면이라 주기적으로 다시 그리지 않는다
    if (investState.tab === "auto") {  // 자세히 보는 화면만 6초마다 새로(입력 화면·펼쳐 읽는 중에는 그대로)
      // 자동매매 창에서 종목을 눌러 시세 창이 떠 있으면 그 창이 같은 자리를 쓰고 있다: 다시 그리면 시세 창을 덮어 버리니 기다린다
      if (stockSheet.stack.length || (pgSheet.open && !String(pgSheet.key).startsWith("auto:"))) return;
      const A = this.at;
      const lab = A && A.view === "lab";
      if (A && (typeof A.view === "number" || lab || A.view === "list") && (lab || A.tab !== "plan") && !$(".pgs-in.dragging") && !$("#atBody details[open]:not([data-keep])") && !$(".at-more[open], .at-old[open]")
        && (!this._atT || Date.now() - this._atT > (A.view === "list" ? 12000 : 6000))) { this._atT = Date.now(); await this.auto($("#iv")); }
      return;
    }
    if (investState.tab !== "dca") await this.fill();
  },
  async fill() {
    const box = $("#iv");
    if (!box) return;
    if (investState.tab === "home") return this.home(box);
    if (investState.tab === "ai") return this.aiPick(box);
    if (investState.tab === "discover") return this.discover(box);
    if (investState.tab === "market") return mkt.board(box);
    if (investState.tab === "account") return this.account(box);
    if (investState.tab === "trade") return this.trade(box);
    if (investState.tab === "dividend") return this.dividend(box);
    if (investState.tab === "auto") return this.auto(box);
    if (investState.tab === "atlog") return this.atLog(box);
    if (investState.tab === "evo") return this.evo(box);
    if (investState.tab === "scalp") return this.scalp(box);
    return this.dca(box);
  },

  // ---------- 투자 홈(카카오페이증권 스타일) ----------
  atBriefHtml() {
    const B = this._atB;
    if (!B) return "";
    return B.runs.length || (B.labs || []).length ? this.atHomeGroup(B.runs, B.labs, B.today)
      : `<a class="card at-home-empty" href="#invest/auto"><span><b>AI 자동매매</b><small>${B.ended ? `끝난 자동매매 ${B.ended}개 · 돌고 있는 것은 없어요` : "쓸 돈만 정하면 AI가 플랜을 짜서 사고팔아요. 모의로 먼저 해 볼 수 있어요"}</small></span><em>›</em></a>`;
  },
  async atBrief() {   // 15초마다만 새로 받는다. AI 추천을 받는 중이거나 곧 받을 때는 3초마다
    const hot = ((this._atB || {}).labs || []).some((L) => L.now && L.now.busy) || ((this._atB || {}).runs || []).some((r) => r.now && (r.now.busy || r.now.next.kind === "soon" || (r.now.next.in_sec != null && r.now.next.in_sec < 20)));
    if (this._atBT && Date.now() - this._atBT < (hot ? 3000 : 15000)) return;
    this._atBT = Date.now();
    try { this._atB = await GET("/api/auto/brief"); } catch (e) { return; }
    if ($("#ihAuto")) $("#ihAuto").innerHTML = gameOn() ? this.gAutoMini() : this.atBriefHtml();
    if ($("#hmAuto")) $("#hmAuto").innerHTML = this.atHomeGroup(this._atB.runs, this._atB.labs, this._atB.today);
  },
  async home(box) {
    if (!box.children.length) { const c = memo.get("ihome"); if (c) this.homeRender(box, c); }
    this.atBrief();
    const d = await GET("/api/invest/home");
    memo.set("ihome", d);
    if (investState.tab !== "home") return;
    this.homeRender(box, d);
  },
  homeRender(box, d) {
    $(".fab")?.remove();
    if (gameOn()) return this.gHome(box, d);
    const a = d.account, H = (d.holdings || []).slice();
    const S = this.hs || (this.hs = { sort: "value" });
    const key = { value: (x) => -x.value, pnl: (x) => -(x.pnl_pct || 0), day: (x) => -(x.day_pct || 0) }[S.sort];
    H.sort((x, y) => key(x) - key(y));
    const idx = (d.indices || []).filter((x) => x.price != null);
    const us = a && a.us && !a.us.error && (a.us.eval || a.us.usd_krw);
    box.innerHTML = `
      ${a && a.error ? `<div class="notice warn">${a.stale ? "키움 연결이 끊겨 아래 금액은 마지막으로 받은 값이에요. 시세·AI 추천·자동매매도 연결돼야 움직여요.<br>" : ""}${esc(a.error)}${ipHelp(a.error)}</div>` : ""}
      <section class="ih-hero">
        ${a && a.total != null ? `<div class="ih-lbl">내 투자 · 키움 ${esc(a.server || "")}${a.stale ? " · 연결 끊김" : ""}</div>
          <div class="ih-total">${won(a.total)}<small>원</small></div>
          <div class="ih-day ${tone(d.day_pnl)}">오늘 ${signed(d.day_pnl)}원 (${pct(d.day_pct, 2)})</div>
          <div class="ih-sub"><span>평가손익 <b class="${tone(a.pnl)}">${signed(a.pnl)}</b> (${pct(a.pnl_pct, 2)})</span><span class="ih-cash" role="button" tabindex="0" data-cashwhy>주문 가능 <b>${won(a.orderable)}</b> ›</span>${us ? `<span>달러 <b>${usd(a.us.usd)}</b></span>` : ""}</div>`
        : `<div class="ih-lbl">내 투자</div><div class="ih-empty">키움 계좌를 연결하면 총자산·오늘 손익·보유 종목이 여기 보여요 <a href="#settings/accounts">연결하기</a></div>`}
      </section>
      <div class="ss-row">${sessionChip("kr")}${sessionChip("us")}<a href="#invest/market" class="tiny">장 시간 ›</a></div>
      <div id="ihAuto">${this.atBriefHtml()}</div>
      ${idx.length ? `<div class="ih-idx">${idx.map((x) => `<div><span>${esc(x.name)}</span><b>${x.name.includes("환율") ? won(x.price) : Number(x.price).toLocaleString("en-US", { maximumFractionDigits: 2 })}</b><em class="${tone(x.change_pct)}">${pct(x.change_pct, 2)}</em></div>`).join("")}</div>` : ""}
      ${a && a.total != null ? `<section class="card tight">
        <div class="card-head ih-head"><h2>보유 종목 ${H.length}</h2>${seg("정렬", [["value", "평가금액"], ["pnl", "수익률"], ["day", "오늘"]], S.sort, "on-card ih-sort")}</div>
        <div class="list">${H.length ? H.map((h) => `<a class="item ih-row" href="#invest/stock?m=${h.market === "us" ? "us" : "kr"}&s=${encodeURIComponent(h.code)}">
          <span class="main"><b>${esc(h.name)}${h.market === "us" ? ` <span class="tag">미국</span>` : ""}</b>
            <span class="sub">${won(h.qty)}주 · ${h.market === "us" ? usd(h.price) : won(h.price)} <span class="${tone(h.day_pct)}">${h.day_pct == null ? "" : pct(h.day_pct, 2)}</span></span></span>
          <span class="end"><b>${won(h.value)}</b><span class="small ${tone(h.pnl)}" style="font-weight:700">${signed(h.pnl)} (${pct(h.pnl_pct, 2)})</span></span></a>`).join("")
          : `<div class="empty">보유 종목이 없어요. 발견에서 종목을 둘러보세요</div>`}</div></section>` : ""}
      ${(d.alerts || []).length ? `<div class="sec-title">가격 알림 ${d.alerts.length}</div><section class="card tight"><div class="list">${d.alerts.slice(0, 5).map((x) => `<a class="item" href="#invest/stock?m=${x.mkt}&s=${encodeURIComponent(x.sym)}">
          <span class="ico y">${x.cond === "above" ? "이상" : "이하"}</span><span class="main"><b>${esc(x.name)}</b><span class="sub">${x.mkt === "us" ? usd(x.price) : won(x.price) + "원"} 닿으면 알림</span></span></a>`).join("")}</div></section>` : ""}
      <div class="ih-quick">
        <a class="btn" href="#invest/aistat">AI 성적</a><a class="btn" href="#invest/discover">실시간 순위</a><a class="btn" href="#invest/market">관심종목</a><button type="button" class="btn" id="ihFind">종목 검색</button></div>
      ${a && a.total != null ? AI_BTN("portfolio", "AI 포트폴리오 점검") : ""}
      <a class="tiny" href="#invest/account" style="padding:0 3px">계좌 자세히 보기 ›</a>`;
    bindSeg(box, "정렬", (v) => { S.sort = v; this.homeRender(box, d); });
    $("#ihFind").onclick = () => mkt.search();
    fab("주문", () => orderSheet({}));
  },

  // ===== 게임 화면: 투자 홈 = 금고(도넛) · 두 전장(해 궤도) · 자동매매 경주 · 내 부대 지도 · 기지 메뉴 =====
  gHome(box, d) {
    const a = d.account, H = (d.holdings || []).slice().sort((x, y) => y.value - x.value);
    const S = this.hs || (this.hs = { sort: "value", col: "pnl" });
    const has = a && a.total != null;
    const krV = H.filter((h) => h.market !== "us").reduce((s, h) => s + (h.value || 0), 0), usV = H.filter((h) => h.market === "us").reduce((s, h) => s + (h.value || 0), 0);
    const cash = has ? Math.max(0, a.total - krV - usV) : 0, tot = krV + usV + cash || 1;
    const parts = [[krV, "#FF7A59", "국내 주식"], [usV, "#52ABFF", "미국 주식"], [cash, "#FFC531", "현금"]];
    const idx = d.indices || [], ix = (n) => idx.find((x) => x.name === n);
    const arena = (m) => {
      const S2 = marketSession(m), names = m === "kr" ? ["코스피", "코스닥"] : ["S&P 500", "나스닥"];
      return `<a class="g-arena ${m}" href="#invest/market" aria-label="${m === "kr" ? "국내" : "미국"} 시장 · ${esc(S2.label)}">
        <span class="g-arena-h"><b>${m === "kr" ? "국내" : "미국"}</b><em class="ss-${S2.phase}">${esc(S2.label)}</em></span>
        ${G.arc(S2)}
        <span class="g-arena-ix">${names.map((n) => { const x = ix(n); return x && x.change_pct != null ? `<span><small>${esc(n)}</small><b class="${tone(x.change_pct)}">${x.change_pct >= 0 ? "▲" : "▼"} ${pct(x.change_pct, 2)}</b></span>` : ""; }).join("")}</span>
        <span class="g-arena-s">${esc(S2.sub || "")}</span></a>`;
    };
    const tiles = has && H.length ? G.treemap(H.map((h) => ({ v: Math.max(h.value, tot * 0.004), h }))) : [];
    const colv = (h) => (S.col === "day" ? h.day_pct : h.pnl_pct);
    const fx = idx.find((x) => x.name.includes("환율"));
    box.innerHTML = `
      ${a && a.error ? `<div class="notice warn">${a.stale ? "키움 연결이 끊겨 아래 금액은 마지막으로 받은 값이에요. 시세·AI 추천·자동매매도 연결돼야 움직여요.<br>" : ""}${esc(a.error)}${ipHelp(a.error)}</div>` : ""}
      ${has ? `<section class="g-vault">
        ${G.donut(parts, 132, 18, `<small>평가손익</small><b class="${tone(a.pnl_pct)}">${pct(a.pnl_pct, 1)}</b>`)}
        <div class="g-vault-r">
          <span class="g-lbl">${G.ico.coin}내 금고 · 키움 ${esc(a.server || "")}${a.stale ? " · 연결 끊김" : ""}</span>
          <b class="g-big">${won(a.total)}<small>원</small></b>
          <span class="g-today ${tone(d.day_pnl)}">${(d.day_pnl || 0) >= 0 ? "▲" : "▼"} 오늘 ${signed(d.day_pnl)}원 <i>${pct(d.day_pct, 2)}</i></span>
          <ul class="g-legend">${parts.map(([v, c, n]) => `<li><i style="background:${c}"></i>${n}<b>${Math.round(v / tot * 100)}%</b></li>`).join("")}</ul>
          <span class="g-cash" role="button" tabindex="0" data-cashwhy>주문 가능 <b>${won(a.orderable)}</b>원${a.us && a.us.usd ? ` · 달러 <b>${usd(a.us.usd)}</b>` : ""} ›</span>
        </div></section>`
      : `<section class="g-vault empty"><span class="g-lbl">${G.ico.coin}내 금고</span><b>키움 계좌를 연결하면 금고가 열려요</b><a class="btn dark" href="#settings/accounts">계좌 연결하기</a></section>`}
      <div class="g-arenas">${arena("kr")}${arena("us")}</div>
      ${fx && fx.price ? `<div class="g-fx">원달러 <b>${won(fx.price)}</b> <span class="${tone(fx.change_pct)}">${pct(fx.change_pct, 2)}</span></div>` : ""}
      <div id="ihAuto">${this.gAutoMini()}</div>
      ${has ? `<section class="g-army">
        <div class="g-army-h"><b>내 부대 · 보유 ${H.length}종목</b>${seg("색기준", [["pnl", "전체 수익"], ["day", "오늘"]], S.col, "g-mini-seg")}</div>
        ${H.length ? `<div class="g-map" role="list">${tiles.map((t) => { const h = t.item.h, p = colv(h), big = t.w * t.h > 900;
          return `<a role="listitem" class="g-tile ${big ? "big" : t.w * t.h < 260 ? "tiny" : ""}" href="#invest/stock?m=${h.market === "us" ? "us" : "kr"}&s=${encodeURIComponent(h.code)}"
            style="left:${t.x.toFixed(2)}%;top:${t.y.toFixed(2)}%;width:${t.w.toFixed(2)}%;height:${t.h.toFixed(2)}%;background:${G.heat(p)}"
            aria-label="${esc(h.name)} ${won(h.value)}원 ${pct(p, 2)}"><b>${esc(h.name)}</b><span>${pct(p, 1)}</span>${big ? `<small>${won(h.value)}원</small>` : ""}</a>`; }).join("")}</div>
          <div class="g-map-l"><span>칸 크기 = 평가금액</span><span><i style="background:${G.heat(5)}"></i>오름 <i style="background:${G.heat(-5)}"></i>내림</span></div>`
          : `<div class="empty">아직 부대가 없어요. 발견에서 종목을 둘러보세요</div>`}</section>` : ""}
      <nav class="g-base" aria-label="투자 바로 가기">
        <a href="#invest/aistat">${G.ico.trophy}<b>AI 성적</b></a>
        <a href="#invest/discover">${G.ico.bolt}<b>실시간 순위</b></a>
        <a href="#invest/market">${G.ico.star}<b>관심종목</b></a>
        <button type="button" id="ihFind">${G.ico.search}<b>종목 검색</b></button>
        <a href="#invest/account">${G.ico.bank}<b>계좌</b></a>
        ${(d.alerts || []).length ? `<a href="#invest/market">${G.ico.bell}<b>가격 알림 ${d.alerts.length}</b></a>` : `<a href="#invest/atlog">${G.ico.flag}<b>매매 기록</b></a>`}
      </nav>
      ${has ? AI_BTN("portfolio", "AI 포트폴리오 점검") : ""}`;
    bindSeg(box, "색기준", (v) => { S.col = v; this.gHome(box, d); });
    $("#ihFind").onclick = () => mkt.search();
    fab("주문", () => orderSheet({}));
  },
  // 투자 홈의 자동매매: 실험마다 경주 트랙 한 줄, 직접 만든 것은 체력 막대
  gAutoMini() {
    const B = this._atB;
    if (!B) return "";
    const labs = (B.labs || []).slice().sort((x, y) => (x.mkt === y.mkt ? 0 : x.mkt === "kr" ? -1 : 1));
    if (!labs.length && !B.runs.length) return `<a class="g-quest" href="#invest/auto">${G.ico.radar}<span><b>AI 자동매매</b><small>쓸 돈만 정하면 AI가 플랜을 짜서 사고팔아요. 모의로 먼저 해 볼 수 있어요</small></span><em>›</em></a>`;
    return `<section class="g-race">
      <div class="g-race-h"><b>AI 자동매매</b><a href="#invest/auto">전체 ›</a></div>
      ${this.atTodayMini(B.today, B.runs, labs)}
      ${B.runs.map((r) => { const m = r.money, hp = Math.max(0, Math.min(100, (m.equity / (r.budget || 1)) * 50)), n = r.now;
        return `<a class="g-unit ${r.mkt} ${r.mode}" href="#invest/auto" data-atgo="${r.id}">
          <span class="g-unit-h"><i class="mk-tag ${r.mkt}">${r.mkt === "kr" ? "국내" : "미국"}</i><b>${esc(r.name)}</b><em class="${r.mode === "real" ? "real" : ""}">${r.mode === "real" ? "실전" : "모의"}</em>
            <span class="live-dot ${r.status === "running" ? "on" : ""}"></span></span>
          <span class="g-hp" role="img" aria-label="쓸 돈 대비 ${pct(m.ret_pct, 2)}"><i style="width:${hp.toFixed(1)}%" class="${m.ret_pct >= 0 ? "pos" : "neg"}"></i><s></s></span>
          <span class="g-unit-f"><b>${this.atM(r.mkt, m.equity)}</b><b class="${tone(m.ret_pct)}">${pct(m.ret_pct, 2)}</b><small>${n && n.busy ? "AI 추천 받는 중" : n ? esc(n.next.kind === "at" ? `다음 추천 ${n.next.text}` : n.next.label).slice(0, 24) : ""}</small></span></a>`; }).join("")}
      ${labs.map((L) => { const lead = (L.race || []).filter((r) => r.n || r.open).sort((a, b) => b.net - a.net)[0];
        return `<a class="g-lane ${L.mkt}" href="#invest/auto" data-labgo="${L.id}">
          <span class="g-lane-h"><i class="mk-tag ${L.mkt}">${L.mkt === "kr" ? "국내" : "미국"}</i><b>${esc(L.style_label)} 실험</b>
            ${L.now && L.now.busy ? `<em class="g-scan">${G.ico.radar}추천 받는 중</em>` : `<span class="live-dot ${L.status === "running" ? "on" : ""}"></span>`}</span>
          ${G.trackMini(L.race)}
          <span class="g-lane-f">${lead ? `<span class="g-crown">👑</span><b>${esc(lead.label)}</b><b class="${tone(lead.net)}">${pct(lead.net, 2)}</b>` : "<span>출발 전</span>"}<small>보유 ${L.stocks ?? L.open} · 끝난 거래 ${L.closed}</small></span></a>`; }).join("")}
    </section>`;
  },
  // ===== 게임 화면: 자동매매 대기실 = 연료 계기판 · 네 경기장(경주 트랙) · 내 부대(체력 막대) =====
  gLobby(O, LB, live, done, oldLabs) {
    const S = (LB || {}).auto, U = (O.today || {}).units || {}, labs = ((LB || {}).labs || []).filter((L) => L.status !== "ended");
    const race = (L) => L.runs.map((r) => ({ label: r.label, net: r.net_ret_pct, n: r.closed, open: r.open }));
    const fuel = S ? Math.min(100, S.usd_today / (S.day_cap_usd || 1) * 100) : 0;
    const arena = (a) => {
      const L = labs.find((x) => x.mkt === a.mkt && x.style === a.style), kr = a.mkt === "kr", N = L && L.now;
      const head = `<span class="g-st-h"><i class="mk-tag ${a.mkt}">${kr ? "국내" : "미국"}</i><b>${esc(a.style_label)}</b>${N && N.busy ? `<em class="g-scan">${G.ico.radar}스캔 중</em>` : L ? `<span class="live-dot ${L.status === "running" ? "on" : ""}"></span>` : ""}</span>`;
      if (!L) return `<div class="g-st ${a.mkt} off">${head}<div class="g-track empty"><span>${S.on ? "곧 시작해요" : "꺼져 있어요"}</span></div><span class="g-st-f">끝난 거래 ${a.trades}건</span></div>`;
      const lead = race(L).filter((r) => r.n || r.open).sort((x, y) => y.net - x.net)[0];
      const when = !N ? "" : N.busy ? "AI 추천 받는 중" : N.next.kind === "at" ? `다음 추천 ${esc(N.next.text)}` : N.next.kind === "soon" ? "곧 추천을 받아요" : esc(N.next.label).slice(0, 22);
      return `<button type="button" class="g-st ${a.mkt}" data-lab="${L.id}">${head}
        ${G.trackMini(race(L))}
        <span class="g-st-lead">${a.best ? `<span class="g-crown">⭐</span><b>실전 후보 · ${esc(a.best.label)}</b><b class="${tone(a.best.net_pct)}">${pct(a.best.net_pct, 2)}</b>` : lead ? `<span class="g-crown">👑</span><b>${esc(lead.label)}</b><b class="${tone(lead.net)}">${pct(lead.net, 2)}</b>` : "<span>출발 전</span>"}</span>
        <span class="g-st-k"><span title="보유 종목">${G.ico.bag}<b>${L.stocks ?? L.open}</b></span><span title="끝난 거래">${G.ico.flag}<b>${L.closed}</b></span><span title="받은 추천">${G.ico.radar}<b>${L.cycles}</b></span></span>
        ${U["lab:" + L.id] ? `<span class="g-st-f"><b>${O.today.mkt[a.mkt].is_today ? "오늘" : "지난 장"} 산 ${U["lab:" + L.id].bought} · 판 ${U["lab:" + L.id].sold}</b></span>` : ""}
        <span class="g-st-f">${when}</span></button>`;
    };
    const cand = S ? S.advice.filter((a) => a.best) : [];
    const unit = (r) => { const m = r.money, n = r.now, hp = Math.max(0, Math.min(100, (m.equity / (r.budget || 1)) * 50)), [lbl] = this.AT_ST[r.status] || [r.status];
      return `<button type="button" class="g-unit ${r.mkt} ${r.mode}" data-run="${r.id}">
        <span class="g-unit-h"><i class="mk-tag ${r.mkt}">${r.mkt === "kr" ? "국내" : "미국"}</i><b>${esc(r.name)}</b><em class="${r.mode === "real" ? "real" : ""}">${r.mode === "real" ? "실전" : "모의"}</em>
          ${r.pending ? `<i class="tag warn">개선안 ${r.pending}</i>` : ""}<span class="live-dot ${r.status === "running" ? "on" : ""}"></span></span>
        <span class="g-hp" role="img" aria-label="쓸 돈 대비 ${pct(m.ret_pct, 2)}"><i style="width:${hp.toFixed(1)}%" class="${m.ret_pct >= 0 ? "pos" : "neg"}"></i><s></s></span>
        <span class="g-unit-f"><b>${this.atM(r.mkt, m.equity)}</b><b class="${tone(m.ret_pct)}">${pct(m.ret_pct, 2)}</b>
          <small>${lbl}${n ? ` · ${n.busy ? "AI 추천 받는 중" : esc(n.next.kind === "at" ? `다음 ${n.next.text}` : n.next.label).slice(0, 22)}` : ""} · 거래 ${r.closed} · 보유 ${r.open}</small></span></button>`; };
    return `<div class="g-lobby-top"><b>AI 자동매매</b><button type="button" class="btn dark sm" id="atNewBtn">+ 직접 만들기</button></div>
      ${this.atReadyHtml(O.ready)}
      ${this.atTodayHtml(O.today, O.runs, (LB || {}).labs, this.atState())}
      ${S ? `<section class="g-test ${S.on ? "on" : ""}">
        <div class="g-test-h"><span><b>모의 자동 테스트</b><small>가상 돈으로 플랜 ${((LB || {}).presets || []).length}가지를 겨뤄요</small></span>
          <button type="button" class="switch" role="switch" id="atAutoSw" aria-checked="${!!S.on}" aria-label="모의 자동 테스트"><span></span></button></div>
        <div class="g-fuel" role="img" aria-label="오늘 AI 비용 $${S.usd_today.toFixed(2)} / 하루 $${S.day_cap_usd}"><span>${G.ico.bolt}AI 연료</span>
          <span class="g-fuel-bar"><i style="width:${fuel.toFixed(1)}%"></i></span><b>$${S.usd_today.toFixed(2)}<small> / $${S.day_cap_usd}</small></b></div>
        <div class="g-stadia">${S.advice.slice().sort((a, b) => (a.mkt === b.mkt ? 0 : a.mkt === "kr" ? -1 : 1)).map(arena).join("")}</div>
        ${cand.map((a) => `<div class="at-cand"><span><b>${a.mkt === "kr" ? "국내" : "미국"} ${esc(a.style_label)} 실전 후보</b><small>${esc(a.text)}</small></span>
          <button type="button" class="btn sm" data-real="${a.mkt}:${a.style}:${esc(a.best.variant)}">실전 만들기</button></div>`).join("")}
        <div class="g-legend-row"><span><b class="g-run-s lead"></b>1등</span><span><b class="g-run-s pos"></b>이익 플랜</span><span><b class="g-run-s neg"></b>손해 플랜</span>
          <span>${G.ico.bag}보유</span><span>${G.ico.flag}끝난 거래</span><span>${G.ico.radar}추천</span><span>AI 비용 누적 $${S.usd_all.toFixed(2)}(${won(S.cur_all)}원)</span></div>
      </section>` : ""}
      ${live.length ? `<div class="g-sec">${G.ico.flag}<b>직접 만든 자동매매</b></div><div class="g-units">${live.map(unit).join("")}</div>` : ""}
      ${done.length || oldLabs.length ? `<details class="at-old"><summary>끝난 것 ${done.length + oldLabs.length}개</summary>
        <section class="card tight at-rows">${done.map((r) => this.atRow(r)).join("")}${oldLabs.map((L) => `<button type="button" class="at-row" data-lab="${L.id}">
          <span class="mk-tag ${L.mkt}">${L.mkt === "kr" ? "국내" : "미국"}</span><span class="at-row-n"><b>${esc(L.style_label)} 실험</b><small>${esc(L.start_date.slice(5))}~${esc(L.end_date.slice(5))} · 끝난 거래 ${L.closed}건</small></span>
          <span class="at-row-v"><small>끝남</small></span></button>`).join("")}</section></details>` : ""}
      <div class="tiny" style="padding:0 3px">수익을 보장하지 않아요. 손실 한도에 닿으면 스스로 멈추지만, 급변할 때는 한도보다 더 잃을 수 있어요.</div>`;
  },
  // ===== 게임 화면: 실험 경기장 = 플랜마다 한 줄씩 달리는 경주(0% 선 기준, 오른쪽이 이익) + 믿을 만함(30건) 게이지 =====
  gStadium(L, P, tab, adv) {
    const kr = L.mkt === "kr", N = L.now, rs = L.runs;
    const R = Math.max(1.5, ...rs.map((r) => Math.abs(r.net_ret_pct || 0))) * 1.12, X = (v) => 50 + (v / R) * 44;
    const medal = ["gold", "silver", "bronze"];
    const ranked = rs.filter((r) => r.closed >= 10);
    const lanes = rs.map((r, i) => {
      const rank = ranked.indexOf(r), started = r.closed || r.open, x = X(r.net_ret_pct || 0);
      return `<button type="button" class="g-lane2" data-run="${r.id}" aria-label="${esc(r.label)} 비용 뺀 ${pct(r.net_ret_pct, 2)}, 끝난 거래 ${r.closed}건">
        <span class="g-lane2-n"><i class="g-medal ${rank >= 0 && rank < 3 ? medal[rank] : ""}">${rank >= 0 ? rank + 1 : "·"}</i><b>${esc(r.label)}</b></span>
        <span class="g-lane2-t">${started ? `<span class="g-lane2-trail ${r.net_ret_pct >= 0 ? "pos" : "neg"}" style="left:${Math.min(50, x).toFixed(1)}%;width:${Math.abs(x - 50).toFixed(1)}%"></span>
          <span class="g-runner ${r.net_ret_pct >= 0 ? "pos" : "neg"} ${rank === 0 ? "lead" : ""}" style="left:${x.toFixed(1)}%">${r.open ? `<em>${r.open}</em>` : ""}</span>` : `<span class="g-lane2-wait">출발 전</span>`}</span>
        <span class="g-lane2-v"><b class="${tone(r.net_ret_pct)}">${started ? pct(r.net_ret_pct, 2) : "-"}</b>
          <span class="g-cred" title="믿을 만함 ${Math.min(r.closed, 30)}/30"><i style="width:${Math.min(100, r.closed / 30 * 100).toFixed(0)}%"></i></span></span></button>`;
    }).join("");
    return `<section class="g-stadium ${L.mkt}">
        <button type="button" class="at-back" id="atBack">‹ 대기실</button>
        <div class="g-stadium-h"><i class="mk-tag ${L.mkt}">${kr ? "국내장" : "미국장"}</i><b>${esc(L.style_label)} 경기장</b>
          <span class="tag ${{ running: "y", paused: "g", ended: "" }[L.status]}">${{ running: "가동 중", paused: "멈춤", ended: "끝남" }[L.status]}</span></div>
        ${N ? (N.busy ? this.atScanHtml(N) : `<div class="at-nowline ${N.next.kind}"><span class="at-dot ${L.status === "running" ? "on" : ""}"></span><div><b>${esc(N.next.label)}</b><span>${this.atNextLine(N)}</span></div></div>`) : ""}
        ${this.atFlowBtn(rs)}
        <div class="g-st-k big"><span>${G.ico.bag}<small>보유</small><b>${L.stocks ?? L.open}종목</b></span><span>${G.ico.flag}<small>끝난 거래</small><b>${L.closed}</b></span>
          <span>${G.ico.radar}<small>추천${L.plan && L.plan.quick_gap_min ? ` · ${L.plan.quick_gap_min}분마다` : ""}</small><b>${L.cycles}번</b></span><span>${G.ico.bolt}<small>AI 비용</small><b>$${Number(L.usd).toFixed(2)}</b></span></div>
        <div class="g-field" role="list" aria-label="플랜 경주">
          <div class="g-field-ax"><span>${pct(-R, 1)} 손해</span><span>0%</span><span>이익 ${pct(R, 1)}</span></div>
          ${lanes}
          <div class="g-field-l"><span>점 위치 = AI 비용 뺀 수익률 · 점 안 숫자 = 지금 보유 종목 수</span><span>막대 = 믿을 만함(끝난 거래 30건)</span></div>
        </div>
        ${seg("실험탭", [["hold", `보유 ${L.positions.length}`], ["last", "추천"], ["rank", "성적표"], ["sold", `판 기록 ${L.trades.length}`]], tab, "ap-mkt")}
      </section>
      <div id="atBody">${adv && adv.best ? `<div class="at-cand"><span><b>실전 후보</b><small>${esc(adv.text)}</small></span><button type="button" class="btn sm" data-real="${L.mkt}:${L.style}:${esc(adv.best.variant)}">실전 만들기</button></div>` : ""}
        <section class="card lab ${L.mkt}">${P[tab]}</section></div>`;
  },
  // ---------- AI 자동매매 ----------
  AT_ST: { running: ["가동 중", "y"], paused: ["새 매수 멈춤", "g"], closing: ["정리 중", "warn"], ended: ["종료", ""] },
  atM(mkt, v, sign) {  // 시장 통화로 금액 표시
    if (v == null || isNaN(v)) return "-";
    const s = sign && v > 0 ? "+" : "";
    return mkt === "us" ? s + (v < 0 ? "−" : "") + "$" + Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : s + won(v) + "원";
  },
  atState() {
    return this.at || (this.at = { view: "list", tab: "now", form: { mode: "paper", mkt: "kr", style: "day", budget: "", days: 30, model: "", daily: "", total: "", confirm: "" }, d: null, pv: null });
  },
  async auto(box) {
    const A = this.atState();
    $(".fab")?.remove();
    const iv = $("#iv") || box;
    try {
      if (A.view === "list") { pgSheet.close(); return await this.atList(iv); }
      if (iv && !$("#atNewBtn", iv)) { try { await this.atList(iv); } catch (e) { if (e instanceof Locked || e instanceof NeedConnect) throw e; } }   // 창 아래에 목록이 깔려 있게
      if (A.view === "list") return;                // 목록을 그리는 사이에 닫았으면 그만
      const key = A.view;
      box = pgSheet.show("auto:" + key, (silent, k) => {
        if (k !== "auto:" + A.view) return;           // 이미 다른 화면으로 넘어갔으면(목록으로 돌아가기 등) 그쪽이 그린다
        A.view = "list"; A.back = null; A.o = null;
        if (!silent && investState.tab === "auto" && $("#iv")) { this._atT = Date.now(); this.atList($("#iv")).catch(() => {}); }
      });
      if (key === "new") return await this.atNew(box);
      if (key === "lab") return await this.atLab(box);
      return await this.atRun(box);
    } catch (e) {
      if (e instanceof Locked || e instanceof NeedConnect) throw e;
      if (e.code === "old_server") return oldServerBox(box, () => this.auto(box));
      box.innerHTML = `<div class="notice warn">${esc(e.message)}</div><button type="button" class="btn wide" id="atRetry">다시 시도</button>`;
      $("#atRetry").onclick = () => { A.view = "list"; this.auto(box); };
    }
  },
  // 자동매매: 남은 시간을 읽기 쉽게
  atDur(min) {
    if (min == null) return "";
    if (min < 1) return "곧";
    if (min < 60) return `${min}분 뒤`;
    if (min < 1440) return `${Math.floor(min / 60)}시간${min % 60 ? ` ${min % 60}분` : ""} 뒤`;
    return `${Math.floor(min / 1440)}일${Math.floor(min % 1440 / 60) ? ` ${Math.floor(min % 1440 / 60)}시간` : ""} 뒤`;
  },
  // 다음에 언제 움직이는지 한 줄: "다음 추천 오늘 09:10 · 50분 뒤"
  atNextLine(n) {
    const x = n.next;
    if (x.kind === "at") return `다음 추천 <b>${esc(x.text)}</b>${x.local ? ` (${esc(x.local)})` : ""} · ${x.in_sec != null && x.in_sec > 0 && x.in_sec < 3 * 3600 ? `${tickHtml("cd", x.in_sec)} 뒤` : this.atDur(x.in_min)}`;
    const T = n.today;
    if (x.kind === "soon") return T && T.quick_gap ? `오늘 정밀 ${T.full}/${T.cycles_max}번 · 빠른 ${T.quick}번 · ${T.quick_gap}분마다` : "엔진이 다음에 돌 때(몇 초 안) 받아요";
    return { soon: "곧 AI 추천을 받아요", busy: "AI 추천을 받는 중", slot: "자리가 나면 다시 추천", hold: "새 매수 없음 · 보유만 정리", paused: "새 매수 멈춤 · 보유만 지켜봄", closing: "보유 종목 정리 중", blocked: "확인이 필요해요", wait: "AI를 부르지 않고 지켜보는 중 · 비용 없음" }[x.kind] || "";
  },
  // AI 추천을 받는 동안: 네 단계 중 어디인지, 얼마나 지났는지를 움직이는 표시로 보여 준다
  atScanHtml(n, mini) {
    const S = n.scan || { step: 1, steps: ["시장 자료 모으기", "AI 분석", "추천 정리", "매수 판단"], msg: "AI 추천을 받는 중이에요", elapsed: 0 };
    const facts = [];
    if (S.search) facts.push(`웹 검색 ${S.search}번`);
    if (S.sources) facts.push(`읽은 자료 ${S.sources}개`);
    if (S.step === 2 && S.chars) facts.push(`${won(S.chars)}자 작성`);
    if (S.step === 1 && S.src) facts.push(S.src);
    if (S.lite) facts.unshift("빠른 재추천");
    if (facts.length < (S.lite ? 2 : 1)) facts.push(S.lite ? "보통 10~20초 걸려요" : "보통 1~2분 걸려요");
    const segs = `<ol class="at-scan-steps">${S.steps.map((t, i) => `<li class="${i < S.step ? "done" : i === S.step ? "on" : ""}"${i === S.step ? ' aria-current="step"' : ""}><i></i>${mini ? "" : `<span>${esc(t)}</span>`}</li>`).join("")}</ol>`;
    const sync = `--sync:-${Date.now() % 2400}ms`;
    if (mini) return `<div class="at-scan mini" style="${sync}" role="status"><div class="at-scan-top"><span class="at-radar" aria-hidden="true"><i></i></span>
        <div class="at-scan-txt"><b>${S.lite ? "빠른 재추천 · " : ""}${esc(S.steps[S.step])} 중 <small>${S.step + 1}/${S.steps.length}</small></b></div>${tickHtml("el", S.elapsed)}</div>${segs}</div>`;
    return `<div class="at-scan" style="${sync}" role="status">
      <div class="at-scan-top"><span class="at-radar" aria-hidden="true"><i></i></span>
        <div class="at-scan-txt"><b>${esc(S.msg || S.steps[S.step])}</b><span>${facts.map(esc).join(" · ")}</span></div>${tickHtml("el", S.elapsed)}</div>
      ${segs}</div>`;
  },
  // 오늘 받은 추천 횟수: 빠른 재추천을 쓰면 하루 최대가 따로 없어 'n번', 아니면 'n/하루 최대'
  atCyc(t) {
    return t.quick_gap ? `${t.cycles}번` : `${t.cycles}/${t.cycles_max}`;
  },
  atTodayLine(r) {
    const n = r.now, t = n.today;
    return `오늘 추천 ${this.atCyc(t)} · 매수 ${t.buys} · 매도 ${t.sells} · 보유 ${n.size.slots_used}/${n.size.slots} · ${n.period.day}일째/${n.period.days}일`;
  },
  // 홈·투자 홈에 놓는 요약 카드(누르면 그 자동매매의 현황으로)
  atHomeCard(r) {
    const [lbl] = this.AT_ST[r.status] || [r.status], m = r.money, n = r.now, t = n.today, live = r.status === "running" && n.engine.ok, real = r.mode === "real";
    const kpi = [["추천", this.atCyc(t)], ["매수", t.buys], ["매도", t.sells], ["보유", `${n.size.slots_used}/${n.size.slots}`], ["기간", `${n.period.day}/${n.period.days}일`]];
    return `<a class="bot-run at-home ${real ? "real" : "paper"} ${r.mkt}" href="#invest/auto" data-atgo="${r.id}">
      <div class="bot-top"><span class="bot-mode">${real ? "실전" : "모의"}</span><b class="bot-name">${esc(r.name)}</b>
        <span class="bot-st live-dot ${live ? "on" : ""}">${lbl}${n.engine.ok ? "" : " · 엔진 확인 필요"}</span></div>
      <div class="bot-eq"><b>${this.atM(r.mkt, m.equity)}</b><em class="${tone(m.ret_pct)}">${pct(m.ret_pct, 2)}</em></div>
      ${n.busy ? this.atScanHtml(n, true) : `<div class="bot-now"><b>${esc(n.next.label)}</b><span>${this.atNextLine(n)}</span></div>`}
      <dl class="bot-kpi">${kpi.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("")}</dl></a>`;
  },
  // 홈·투자 홈: 자동매매는 잔고 카드와 성격이 달라(스스로 사고파는 '일') 한 묶음의 판으로 따로 보여 준다
  atHomeGroup(runs, labs, today) {
    runs = runs || []; labs = labs || [];
    if (!runs.length && !labs.length) return "";
    const live = runs.filter((r) => r.status === "running" && r.now.engine.ok).length + labs.filter((L) => L.status === "running").length;
    const busy = runs.some((r) => r.now.busy) || labs.some((L) => L.now && L.now.busy);
    return `<section class="bot ${live ? "live" : ""}" style="--sync:-${Date.now() % 2400}ms" aria-label="AI 자동매매">
      <header class="bot-head"><span class="bot-ico" aria-hidden="true"><i></i><i></i><i></i></span><h2>AI 자동매매</h2>
        <span class="bot-sub">${busy ? "AI 추천 받는 중" : live ? `${live}개 가동 중` : `${runs.length + labs.length}개 · 쉬는 중`}</span><a href="#invest/auto">전체 ›</a></header>
      ${this.atTodayMini(today, runs, labs)}
      ${["kr", "us"].map((mk) => {      // 국내장·미국장을 따로 묶어, 색으로도 가른다(국내 = 주황, 미국 = 파랑)
        const rs = runs.filter((r) => r.mkt === mk), ls = labs.filter((L) => L.mkt === mk);
        if (!rs.length && !ls.length) return "";
        const ss = marketSession(mk);
        return `<div class="bot-mk ${mk}"><b>${mk === "kr" ? "국내장" : "미국장"}</b><span class="${ss.phase === "open" ? "on" : ""}">${esc(ss.label)}${ss.sub ? ` · ${esc(ss.sub)}` : ""}</span></div>
      ${rs.map((r) => this.atHomeCard(r)).join("")}
      ${ls.map((L) => `<a class="bot-run lab slim ${mk}" href="#invest/auto" data-labgo="${L.id}">
        <div class="bot-top"><span class="bot-mode">실험</span><b class="bot-name">${esc(L.style_label)} · 플랜 ${L.n}개</b>
          <span class="bot-st live-dot ${L.status === "running" ? "on" : ""}">${L.status === "running" ? "가동 중" : "멈춤"}</span></div>
        ${L.now ? (L.now.busy ? this.atScanHtml(L.now, true) : `<div class="bot-now one"><b>${esc(L.now.next.kind === "at" ? `다음 추천 ${L.now.next.text}` : L.now.next.label)}</b></div>`) : ""}
        <div class="bot-line">보유 <b>${L.stocks ?? L.open}종목</b> · 끝난 거래 <b>${L.closed}</b> · 추천 <b>${L.cycles}번</b> · AI <b>$${Number(L.usd).toFixed(2)}</b></div>
        ${(L.hold || []).length ? `<div class="bot-hold">${L.hold.map((h) => `<span>${esc(h.name)} <b class="${tone(h.pct)}">${pct(h.pct, 2)}</b></span>`).join("")}</div>` : ""}
        <div class="bot-note">${esc(L.note)}</div></a>`).join("")}`;
      }).join("")}</section>`;
  },
  atReadyHtml(R) {
    const rows = [];
    if (!R.ai) rows.push(`Claude API 키가 없어요. <a href="#settings/ai">AI 설정에서 넣기</a>`);
    if (!R.data) rows.push(`실시간 순위를 받으려면 키움 계좌 연결이 필요해요. <a href="#settings/accounts">계좌 연결하기</a>`);
    return rows.map((r) => `<div class="notice warn">${r}</div>`).join("");
  },
  atCard(r) {
    const [lbl, cls] = this.AT_ST[r.status] || [r.status, ""], m = r.money;
    return `<button type="button" class="card at-run" data-run="${r.id}">
      <div class="at-run-top"><b>${esc(r.name)}</b><span class="tag ${cls}">${lbl}</span>${r.pending ? `<span class="tag warn">개선안 ${r.pending}</span>` : ""}</div>
      <div class="at-run-mid"><span class="at-eq">${this.atM(r.mkt, m.equity)}</span><span class="at-ret ${tone(m.ret_pct)}">${pct(m.ret_pct, 2)}</span></div>
      ${r.now ? (r.now.busy ? this.atScanHtml(r.now, true) : `<div class="at-run-next"><span class="at-dot ${r.status === "running" && r.now.engine.ok ? "on" : ""}"></span><span>${esc(r.now.next.label)}</span></div>
      <div class="small">${this.atNextLine(r.now)}</div>`) : ""}
      <div class="tiny">${r.mode === "real" ? "실전" : "모의"} · ${r.mkt === "us" ? "미국" : "국내"} ${esc(r.style_label)} · ${r.start_date.slice(5).replace("-", "/")}~${r.end_date.slice(5).replace("-", "/")} · 거래 ${r.closed}건${r.closed ? ` (승 ${r.wins})` : ""} · 보유 ${r.open}${r.now ? ` · 오늘 추천 ${r.now.today.cycles}/${r.now.today.cycles_max}` : ""}</div>
    </button>`;
  },
  // ----- 목록: 한 화면에 들어오게. 모의 자동 테스트는 시장×방식 네 칸, 직접 만든 것은 한 줄씩. 자세한 것은 눌러서 본다
  async atList(box) {
    const A = this.atState(), [O, LB] = await Promise.all([GET("/api/auto"), GET("/api/auto/lab")]);
    A.o = O; A.lab = LB;
    if (investState.tab !== "auto" || A.view !== "list") return;
    const live = O.runs.filter((r) => r.status !== "ended"), done = O.runs.filter((r) => r.status === "ended");
    const oldLabs = ((LB || {}).labs || []).filter((L) => L.status === "ended");
    box.innerHTML = gameOn() ? this.gLobby(O, LB, live, done, oldLabs) : `<div class="at-top"><div><b>AI 자동매매</b><span>AI가 고르고, 플랜대로 사고팔고, 기록으로 고쳐 가요</span></div>
        <button type="button" class="btn dark sm" id="atNewBtn">+ 직접 만들기</button></div>
      ${this.atReadyHtml(O.ready)}
      ${this.atTodayHtml(O.today, O.runs, (LB || {}).labs, A)}
      ${this.atAutoHtml(LB, O.today)}
      ${live.length ? `<div class="sec-title">직접 만든 자동매매</div><section class="card tight at-rows">${live.map((r) => this.atRow(r)).join("")}</section>` : ""}
      ${done.length || oldLabs.length ? `<details class="at-old"><summary>끝난 것 ${done.length + oldLabs.length}개</summary>
        <section class="card tight at-rows">${done.map((r) => this.atRow(r)).join("")}${oldLabs.map((L) => `<button type="button" class="at-row" data-lab="${L.id}">
          <span class="mk-tag ${L.mkt}">${L.mkt === "kr" ? "국내" : "미국"}</span><span class="at-row-n"><b>${esc(L.style_label)} 실험</b><small>${esc(L.start_date.slice(5))}~${esc(L.end_date.slice(5))} · 끝난 거래 ${L.closed}건</small></span>
          <span class="at-row-v"><small>끝남</small></span></button>`).join("")}</section></details>` : ""}
      <div class="tiny" style="padding:0 3px">수익을 보장하지 않아요. 손실 한도에 닿으면 스스로 멈추지만, 급변할 때는 한도보다 더 잃을 수 있어요.</div>`;
    $("#atNewBtn").onclick = () => { A.view = "new"; A.pv = null; this.auto(box); };
    box.onclick = async (e) => {
      const b = e.target.closest("[data-run]"), lb = e.target.closest("[data-lab]");
      const sw = e.target.closest("#atAutoSw"), rv = e.target.closest("[data-real]");
      const td = e.target.closest("[data-td]"), tf = e.target.closest("[data-tdf]");
      if (td) return this.atTodaySheet(O.today.rows[Number(td.dataset.td)], box);
      if (tf || e.target.closest("#tdMore")) {
        if (tf) { A.tdF = tf.dataset.tdf; A.tdAll = false; } else A.tdAll = !A.tdAll;
        const old = $(".td", box), tmp = document.createElement("div");
        tmp.innerHTML = this.atTodayHtml(O.today, O.runs, (LB || {}).labs, A);
        if (old) old.replaceWith(tmp.firstElementChild);
        return;
      }
      if (sw) {
        const on = sw.getAttribute("aria-checked") !== "true";
        try { await run(() => api("POST", "/api/auto/lab/auto", { on }), on ? "모의 자동 테스트를 켰어요" : "모의 자동 테스트를 껐어요"); } catch (err) { return; }
        return this.auto(box);
      }
      if (rv) return this.atGoReal(box, rv.dataset.real);
      if (lb) { A.view = "lab"; A.labId = Number(lb.dataset.lab); A.labTab = A.labTab || "hold"; box.onclick = null; return this.auto(box); }
      if (b) { A.view = Number(b.dataset.run); A.tab = "now"; A.d = null; box.onclick = null; this.auto(box); }
    };
  },
  atGoReal(box, key) {      // 모의에서 검증한 플랜으로 실전 만들기 화면을 연다(쓸 돈과 '실전' 확인은 직접 넣는다)
    const A = this.at, [mkt, style, variant] = key.split(":");
    Object.assign(A.form, { mode: "real", mkt, style, variant, budget: "", model: "auto", confirm: "" });
    A.view = "new"; A.pv = null; box.onclick = null;
    return this.auto(box);
  },
  // ----- 오늘 한 일: 지금까지 쌓인 숫자와 섞이지 않게, 오늘(장이 닫혀 있고 오늘 거래가 없으면 지난 장) 거래만 따로 보여 준다
  atDayLabel(M) {
    const d = new Date(M.day + "T00:00:00");
    return `${M.is_today ? "오늘" : "지난 장"} ${+M.day.slice(5, 7)}/${+M.day.slice(8, 10)}(${"일월화수목금토"[d.getDay()]})`;
  },
  atNextNote(runs, labs) {      // 가장 먼저 움직일 것 하나
    const xs = [];
    (runs || []).forEach((r) => r.now && r.status === "running" && xs.push([r.now, r.name]));
    (labs || []).forEach((L) => L.now && L.status === "running" && xs.push([L.now, `${L.mkt === "kr" ? "국내" : "미국"} ${L.style_label} 실험`]));
    const busy = xs.find(([n]) => n.busy);
    if (busy) return { k: "next", tone: "live", text: `지금 AI 추천 받는 중 · ${busy[1]}` };
    const at = xs.filter(([n]) => n.next && n.next.kind === "at").sort((a, b) => (a[0].next.in_sec ?? 9e9) - (b[0].next.in_sec ?? 9e9))[0];
    return at ? { k: "next", tone: "", text: `다음 추천 ${at[0].next.text} · ${at[1]}` } : null;
  },
  atTapeRow(e, i, link) {
    const sell = e.side === "sell", lab = e.kind === "lab";
    const sub = [e.unit, sell ? e.reason : "", lab ? `플랜 ${e.n}개${sell && e.n > 1 ? ` ${e.wins}승 ${e.n - e.wins}패` : ""}` : `${e.qty}주`].filter(Boolean).join(" · ");
    const val = sell ? `<b class="${tone(e.pct)}">${pct(e.pct, 2)}</b>${lab ? "" : `<small class="${tone(e.pnl)}">${this.atM(e.mkt, e.pnl, true)}</small>`}`
      : `<b>${this.atM(e.mkt, e.price)}</b><small>${lab ? (e.still ? `${e.still}개 플랜 보유` : "모두 팔았어요") : e.still ? "보유 중" : "팔았어요"}</small>`;
    const inner = `<time>${esc((e.ts || "").slice(11, 16))}</time><i class="td-side">${sell ? "팔았음" : "샀음"}</i>
      <span class="td-n"><b>${esc(e.name)}${e.mode === "real" ? `<em class="td-real">실전</em>` : ""}</b><small><i class="td-mkd ${e.mkt}"></i>${esc(sub)}</small></span><span class="td-v">${val}</span>`;
    return link ? `<a class="td-row ${e.mkt} ${e.side}" href="#invest/auto">${inner}</a>` : `<button type="button" class="td-row ${e.mkt} ${e.side}" data-td="${i}">${inner}</button>`;
  },
  atTodayPanel(T, mk) {
    const M = T.mkt[mk], ss = marketSession(mk), quiet = !M.bought && !M.sold;
    return `<div class="td-mk ${mk}${quiet ? " quiet" : ""}"><span class="td-mk-h"><i class="mk-tag ${mk}">${mk === "kr" ? "국내" : "미국"}</i><b>${this.atDayLabel(M)}</b>${ss.phase === "open" ? `<em class="on live-dot">장 중</em>` : ""}</span>
      ${quiet ? `<span class="td-mk-q">${M.is_today ? "아직 거래가 없어요" : "거래가 없었어요"}</span>`
        : `<span class="td-mk-k"><span><small>산 종목</small><b>${M.bought}</b></span><span><small>판 종목</small><b>${M.sold}</b></span><span><small>평균 수익률</small><b class="${tone(M.avg_pct)}">${M.avg_pct == null ? "-" : pct(M.avg_pct, 2)}</b></span></span>
        ${M.closed ? `<span class="td-wl" role="img" aria-label="끝난 거래 ${M.closed}건 중 ${M.wins}승 ${M.losses}패"><i style="flex:${M.wins}"></i><s style="flex:${M.losses}"></s></span>
          <span class="td-mk-f">끝난 거래 ${M.closed}건 · <b>${M.wins}승 ${M.losses}패</b></span>` : `<span class="td-mk-f">아직 판 것이 없어요</span>`}
        ${M.real_n ? `<span class="td-mk-r">실전 손익 <b class="${tone(M.real_pnl)}">${this.atM(mk, M.real_pnl, true)}</b></span>` : ""}`}</div>`;
  },
  atTodayHtml(T, runs, labs, A) {
    if (!T) return "";
    const f = A.tdF || "all", rows = T.rows.map((e, i) => [e, i]).filter(([e]) => f === "all" || e.mkt === f), cut = A.tdAll ? rows : rows.slice(0, 5);
    const both = T.rows.some((e) => e.mkt === "kr") && T.rows.some((e) => e.mkt === "us"), nx = this.atNextNote(runs, labs);
    const notes = (nx ? [nx] : []).concat(T.notes.filter((n) => n.k === "warn"), T.notes.filter((n) => n.k === "hold"));
    return `<section class="card td" aria-label="오늘 한 일">
      <div class="td-h"><b>오늘 한 일</b><span>지금까지 쌓인 숫자와 따로, 오늘 거래만</span></div>
      <div class="td-mks">${this.atTodayPanel(T, "kr")}${this.atTodayPanel(T, "us")}</div>
      ${notes.length ? `<ul class="td-notes">${notes.map((n) => `<li class="${n.tone || ""}">${esc(n.text)}</li>`).join("")}</ul>` : ""}
      ${T.rows.length ? `<div class="td-tape-h"><b>시간순 거래</b>${both ? `<span class="td-seg" role="group" aria-label="시장 고르기">${[["all", "전체"], ["kr", "국내"], ["us", "미국"]].map(([k, l]) => `<button type="button" data-tdf="${k}" aria-pressed="${f === k}">${l}</button>`).join("")}</span>` : ""}</div>
        <div class="td-tape">${cut.map(([e, i]) => this.atTapeRow(e, i)).join("")}</div>
        ${rows.length > 5 ? `<button type="button" class="td-more" id="tdMore">${A.tdAll ? "접기" : `나머지 ${rows.length - 5}건 더 보기`}</button>` : ""}
        ${T.rows.some((e) => e.kind === "lab") ? `<div class="tiny">모의 자동 테스트는 같은 종목을 여러 플랜이 따로 사고팔아요. 종목마다 한 줄로 묶었고, 끝난 거래는 플랜 수만큼 세요.</div>` : ""}` : ""}
    </section>`;
  },
  // 홈에 넣는 작은 판: 시장별 한 줄 + 알림 + 가장 최근 거래 3줄
  atTodayMini(T, runs, labs) {
    if (!T) return "";
    const line = (mk) => { const M = T.mkt[mk]; if (!M.bought && !M.sold) return "";
      return `<div class="td-ln ${mk}"><i class="mk-tag ${mk}">${mk === "kr" ? "국내" : "미국"}</i><b>${this.atDayLabel(M)}</b><span>산 ${M.bought} · 판 ${M.sold}${M.closed ? ` · ${M.wins}승 ${M.losses}패` : ""}</span>
        ${M.real_n ? `<em class="${tone(M.real_pnl)}">실전 ${this.atM(mk, M.real_pnl, true)}</em>` : M.avg_pct != null ? `<em class="${tone(M.avg_pct)}">${pct(M.avg_pct, 2)}</em>` : ""}</div>`; };
    const lines = line("kr") + line("us"), nx = this.atNextNote(runs, labs);
    const notes = (nx ? [nx] : []).concat(T.notes.filter((n) => n.k === "warn"), T.notes.filter((n) => n.k === "hold"));
    return `<div class="td-mini"><div class="td-mini-h"><b>오늘 한 일</b><a href="#invest/auto">${T.rows.length ? `거래 ${T.rows.length + (T.more || 0)}줄 보기 ›` : "자동매매 ›"}</a></div>
      ${lines || `<div class="td-ln none">오늘은 아직 거래가 없어요</div>`}
      ${notes.length ? `<ul class="td-notes">${notes.map((n) => `<li class="${n.tone || ""}">${esc(n.text)}</li>`).join("")}</ul>` : ""}
      ${T.rows.length ? `<div class="td-tape">${T.rows.slice(0, 3).map((e, i) => this.atTapeRow(e, i, true)).join("")}</div>` : ""}</div>`;
  },
  atTodaySheet(e, box) {
    const sell = e.side === "sell", lab = e.kind === "lab", M = (v) => this.atM(e.mkt, v), hm = (t) => esc((t || "").slice(11, 16)), A = this.atState();
    const kv = sell ? [["평균 수익률", `<i class="${tone(e.pct)}">${pct(e.pct, 2)}</i>`], [lab ? "플랜" : "손익", lab ? `${e.n}개 · ${e.wins}승 ${e.n - e.wins}패` : `<i class="${tone(e.pnl)}">${this.atM(e.mkt, e.pnl, true)}</i>`], ["판 가격(평균)", M(e.price)], ["주로 판 이유", esc(e.reason || "-")]]
      : [["산 가격(평균)", M(e.price)], [lab ? "산 플랜" : "수량", lab ? `${e.n}개` : `${e.qty}주`], ["처음 산 시각", hm(e.ts)], ["지금", lab ? (e.still ? `${e.still}개 플랜이 들고 있어요` : "모두 팔았어요") : e.still ? "들고 있어요" : "팔았어요"]];
    const part = (p) => sell
      ? `<li><span><b>${esc(lab ? p.label : e.unit)}</b><small>${hm(p.entry_ts)} ${M(p.entry)} → ${hm(p.ts)} ${M(p.price)} · ${p.qty}주${p.hold_min != null ? ` · ${p.hold_min >= 90 ? Math.round(p.hold_min / 60) + "시간" : p.hold_min + "분"} 보유` : ""}</small></span><em><b class="${tone(p.pct)}">${pct(p.pct, 2)}</b><small>${esc(p.reason)}</small></em></li>`
      : `<li><span><b>${esc(lab ? p.label : e.unit)}</b><small>${hm(p.ts)} · ${M(p.price)} × ${p.qty}주 · 목표 +${p.target_pct}% / 손절 −${p.stop_pct}%</small></span><em><small>${p.open ? "보유 중" : "팔았어요"}</small></em></li>`;
    const why = !sell && e.why ? (e.why.reasons || []).slice(0, 3) : [];
    openSheet(`${e.name} · ${sell ? "팔았음" : "샀음"}`, `<div class="hx-list">
      <div class="td-pop-h"><i class="mk-tag ${e.mkt}">${e.mkt === "kr" ? "국내" : "미국"}</i><span>${esc(e.unit)}${e.mode === "real" ? " · 실전" : " · 모의"}</span></div>
      <div class="hx-kv">${kv.map(([k, v]) => `<div><span>${k}</span><b>${v}</b></div>`).join("")}</div>
      ${why.length ? `<div class="hx-sub">AI가 고른 이유</div><ul class="td-why">${why.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
      <div class="hx-sub">${lab ? "플랜별 내역" : "거래 내역"}${e.n > e.parts.length ? ` (${e.parts.length}개만 보여요)` : ""}</div><ul class="td-parts">${e.parts.map(part).join("")}</ul>
      <button type="button" class="btn dark wide" id="tdGo">${lab ? "이 실험 자세히 보기" : "이 자동매매 자세히 보기"}</button></div>`, (root) => {
      $("#tdGo", root).onclick = () => { closeSheet();
        if (lab) { A.view = "lab"; A.labId = e.gid; A.labTab = A.labTab || "hold"; } else { A.view = e.gid; A.tab = "now"; A.d = null; }
        box.onclick = null; this.auto(box); };
    });
  },
  // 직접 만든 자동매매 한 줄
  atRow(r) {
    const [lbl] = this.AT_ST[r.status] || [r.status], m = r.money, n = r.now;
    return `<button type="button" class="at-row ${r.mkt}" data-run="${r.id}">
      <span class="mk-tag ${r.mkt}">${r.mkt === "kr" ? "국내" : "미국"}</span>
      <span class="at-row-n"><b>${esc(r.name)}${r.pending ? ` <i class="tag warn">개선안 ${r.pending}</i>` : ""}</b>
        <small>${r.mode === "real" ? "실전" : "모의"} · ${lbl}${n ? ` · ${n.busy ? "AI 추천 받는 중" : esc(n.next.kind === "at" ? `다음 ${n.next.text}` : n.next.label).slice(0, 30)}` : ""} · 거래 ${r.closed} · 보유 ${r.open}</small></span>
      <span class="at-row-v"><b>${this.atM(r.mkt, m.equity)}</b><small class="${tone(m.ret_pct)}">${pct(m.ret_pct, 2)}</small></span></button>`;
  },
  // 모의 자동 테스트: 스위치 한 줄 + 시장×방식 네 칸. 칸을 누르면 그 실험의 자세한 화면
  atAutoHtml(LB, T) {
    const S = (LB || {}).auto, U = (T || {}).units || {};
    if (!S) return "";
    const labs = ((LB || {}).labs || []).filter((L) => L.status !== "ended");
    const cell = (a) => {
      const L = labs.find((x) => x.mkt === a.mkt && x.style === a.style), kr = a.mkt === "kr";
      const head = `<span class="at-cell-h"><i class="mk-tag ${a.mkt}">${kr ? "국내" : "미국"}</i><b>${esc(a.style_label)}</b>${L ? `<em class="live-dot ${L.status === "running" ? "on" : ""}">${L.status === "running" ? "" : "멈춤"}</em>` : ""}</span>`;
      if (!L) return `<div class="at-cell off ${a.mkt}">${head}<span class="at-cell-s">${S.on ? "곧 시작해요" : "꺼져 있어요"}</span><span class="at-cell-f">끝난 거래 ${a.trades}건</span></div>`;
      const N = L.now, best = L.runs.filter((x) => x.closed || x.open).sort((x, y) => y.net_ret_pct - x.net_ret_pct)[0];
      const when = !N ? "" : N.busy ? "AI 추천 받는 중" : N.next.kind === "at" ? `다음 추천 ${esc(N.next.text)}` : N.next.kind === "soon" ? "곧 추천을 받아요" : esc(N.next.label).slice(0, 26);
      const u = U["lab:" + L.id], M = T && T.mkt[a.mkt], dl = M && !M.is_today ? "지난 장" : "오늘";
      return `<button type="button" class="at-cell ${a.mkt} ${N && N.busy ? "busy" : ""}" data-lab="${L.id}">${head}
        <span class="at-cell-s">${when}</span>
        <span class="at-cell-t ${u ? "" : "none"}"><i>${dl}</i>${u ? `산 <b>${u.bought}</b> · 판 <b>${u.sold}</b>${u.avg_pct != null ? ` · <b class="${tone(u.avg_pct)}">${pct(u.avg_pct, 2)}</b>` : ""}` : "거래 없음"}</span>
        <span class="at-cell-cap">지금까지</span>
        <span class="at-cell-k"><span><small>보유</small><b>${L.stocks ?? L.open}종목</b></span><span><small>끝난 거래</small><b>${L.closed}</b></span><span><small>추천</small><b>${L.cycles}</b></span></span>
        <span class="at-cell-f">${a.best ? `<b class="ok">실전 후보 · ${esc(a.best.label)} ${pct(a.best.net_pct, 2)}</b>` : best ? `1등 플랜 <b>${esc(best.label)}</b> <b class="${tone(best.net_ret_pct)}">${pct(best.net_ret_pct, 2)}</b>` : "아직 산 플랜이 없어요"}</span></button>`;
    };
    const cand = S.advice.filter((a) => a.best);
    return `<section class="card at-auto ${S.on ? "on" : ""}">
        <div class="at-auto-top"><div><b>모의 자동 테스트</b>
          <span>AI 비용 오늘 $${S.usd_today.toFixed(2)} / 하루 $${S.day_cap_usd} · 지금까지 $${S.usd_all.toFixed(2)}(${won(S.cur_all)}원)</span></div>
          <button type="button" class="switch" role="switch" id="atAutoSw" aria-checked="${!!S.on}" aria-label="모의 자동 테스트"><span></span></button></div>
        <div class="at-grid">${S.advice.slice().sort((a, b) => (a.mkt === b.mkt ? 0 : a.mkt === "kr" ? -1 : 1)).map(cell).join("")}</div>
        ${cand.map((a) => `<div class="at-cand"><span><b>${a.mkt === "kr" ? "국내" : "미국"} ${esc(a.style_label)} 실전 후보</b><small>${esc(a.text)}</small></span>
          <button type="button" class="btn sm" data-real="${a.mkt}:${a.style}:${esc(a.best.variant)}">실전 만들기</button></div>`).join("")}
        <details class="at-more"><summary>어떻게 시험하나요</summary><div class="tiny">켜 두면 국내·미국 × 단타·스윙 네 칸에서 플랜 ${((LB || {}).presets || []).length}가지를 가상 돈으로 계속 시험해요. 추천 한 번을 모든 플랜이 함께 써서 비용을 아끼고, AI 비용은 모두 합쳐 하루 $${S.day_cap_usd}(한 달 약 ${won(S.month_cap_cur)}원) 안에서 앱이 정해요. 실전 후보는 한 플랜에 30건(10거래일 이상)이 쌓이고 AI 비용을 빼고도 플러스일 때만 알려요. 모의 결과가 실전 수익을 보장하지는 않아요.</div></details>
      </section>`;
  },
  // ----- 실험 한 묶음 자세히: 보유 · 추천 · 성적 · 판 기록을 탭으로 나눠 한 번에 하나만 보여 준다
  async atLab(box) {
    const A = this.atState(), LB = (A.lab = await GET("/api/auto/lab"));
    if (investState.tab !== "auto" || A.view !== "lab") return;
    const L = (LB.labs || []).find((x) => x.id === A.labId);
    if (!L) { A.view = "list"; return this.atList(box); }
    const kr = L.mkt === "kr", N = L.now, tab = (A.labTab = A.labTab || "hold"), P = this.atLabParts(L);
    const adv = ((LB.auto || {}).advice || []).find((a) => a.mkt === L.mkt && a.style === L.style);
    box.innerHTML = gameOn() ? this.gStadium(L, P, tab, adv) : `<section class="ap-hero at-hero lab-hero ${L.mkt}">
        <button type="button" class="at-back" id="atBack">‹ 목록</button>
        <div class="at-hero-top"><b><span class="mk-tag ${L.mkt}">${kr ? "국내장" : "미국장"}</span>${esc(L.style_label)} 실험 · 플랜 ${L.runs.length}개</b>
          <span class="tag ${{ running: "y", paused: "g", ended: "" }[L.status]}">${{ running: "가동 중", paused: "멈춤", ended: "끝남" }[L.status]}</span></div>
        ${N ? (N.busy ? this.atScanHtml(N) : `<div class="at-nowline ${N.next.kind}"><span class="at-dot ${L.status === "running" ? "on" : ""}"></span><div><b>${esc(N.next.label)}</b><span>${this.atNextLine(N)}</span></div></div>`) : ""}
        ${this.atFlowBtn(L.runs)}
        <div class="at-hero-kv k4"><div><span>보유</span><b>${L.stocks ?? L.open}종목</b></div><div><span>끝난 거래</span><b>${L.closed}건</b></div><div><span>추천${L.plan && L.plan.quick_gap_min ? ` · ${L.plan.quick_gap_min}분마다` : ""}</span><b>${L.cycles}번</b></div><div><span>AI 비용</span><b>$${L.usd.toFixed(2)}</b></div></div>
        ${seg("실험탭", [["hold", `보유 ${L.positions.length}`], ["last", "추천"], ["rank", "성적"], ["sold", `판 기록 ${L.trades.length}`]], tab, "ap-mkt")}
      </section>
      <div id="atBody">${adv && adv.best ? `<div class="at-cand"><span><b>실전 후보</b><small>${esc(adv.text)}</small></span><button type="button" class="btn sm" data-real="${L.mkt}:${L.style}:${esc(adv.best.variant)}">실전 만들기</button></div>` : ""}
        <section class="card lab ${L.mkt}">${P[tab]}</section></div>`;
    $("#atBack").onclick = () => { A.view = "list"; this.auto(box); };
    bindSeg(box, "실험탭", (v) => { A.labTab = v; this.auto(box); });
    box.onclick = async (e) => {
      const b = e.target.closest("[data-run]"), lc = e.target.closest("[data-labctl]"), rv = e.target.closest("[data-real]");
      if (rv) return this.atGoReal(box, rv.dataset.real);
      if (lc) {
        const [id, act] = lc.dataset.labctl.split(":");
        if (act === "end" && !confirmTap(lc, "모두 끝내요. 한 번 더 누르기")) return;
        try { await run(() => api("POST", `/api/auto/lab/${id}/control`, { action: act }), act === "end" ? "실험을 끝냈어요" : act === "pause" ? "새 매수를 멈췄어요" : "다시 시작했어요"); } catch (err) { return; }
        return this.auto(box);
      }
      if (b) { A.view = Number(b.dataset.run); A.tab = b.dataset.attab || "now"; A.d = null; A.back = "lab"; box.onclick = null; this.auto(box); }
    };
  },
  // 실험 화면의 탭 내용: 보유(언제·몇 주·얼마에 샀고 지금 얼마이고 어떻게 파는지) · 추천(방금 판단) · 성적(플랜 순위) · 판 기록
  atLabParts(L) {
    const M = (v, sgn) => this.atM(L.mkt, v, sgn), hm = (ts) => (ts ? `${ts.slice(5, 10)} ${ts.slice(11, 16)}` : "");
    const pos = L.positions || [], sold = L.trades || [], last = L.last;
    const hold = pos.length ? `<div class="lab-sell"><b>파는 방법</b> 플랜마다 정한 익절가에 닿으면 이익을 챙기고, 손절가에 닿으면 바로 팔아요. 둘 다 안 닿으면 ${esc(pos[0].hold)}.</div>
      <div class="lab-pos">${pos.map((g, i) => `<details ${pos.length === 1 && i === 0 ? "open" : ""}>
        <summary><span class="lab-pn"><b>${stockA(L.mkt, g.sym, g.name)}</b><small>${g.plans.length}개 플랜 · 모두 ${g.qty}주 · ${hm(g.first_ts)}</small></span>
          <span class="lab-pp"><b class="${tone(g.pct)}">${pct(g.pct, 2)}</b><small>${M(g.avg)} → ${M(g.last)}</small></span></summary>
        <div class="lab-pd">
          ${g.plans.map((p) => `<button type="button" class="lab-pl" data-run="${p.run}">
            <span><b>${esc(p.label)}</b><small>${p.qty}주 × ${M(p.price)}</small></span>
            <span class="lab-ts"><em class="up">익절 ${M(p.target)}<small>+${p.target_pct}%</small></em><em class="down">손절 ${M(p.stop)}<small>-${p.stop_pct}%</small></em></span>
            <span class="${tone(p.pct)}">${pct(p.pct, 2)}</span></button>`).join("")}
          ${g.why && g.why.reasons.length ? `<div class="lab-why"><b>AI가 고른 이유</b><ul>${g.why.reasons.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>` : ""}
          ${g.why && g.why.risks.length ? `<div class="lab-why risk"><b>AI가 본 위험</b><ul>${g.why.risks.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>` : ""}
        </div></details>`).join("")}</div>` : `<div class="empty sm">지금 들고 있는 종목이 없어요.<br>${esc(L.last_line || "추천을 받으면 여기에 보여요")}</div>`;
    const lastH = last ? `<div class="card-head"><h2>${hm(last.ts)} ${last.lite ? "빠른 재추천" : "정밀 추천"}${last.stance ? ` · ${esc(last.stance)}` : ""}</h2></div>${L.plan && L.plan.quick_gap_min ? `<div class="tiny lab-freq">정밀 추천 하루 ${L.plan.cycles_per_day}번 + ${L.plan.quick_gap_min}분마다 빠른 재추천 · 지금까지 ${L.cycles}번(빠른 ${L.quick || 0})</div>` : ""}
        ${last.view ? `<div class="small muted">${esc(last.view)}</div>` : ""}
        <div class="lab-last">${last.picks.map((x) => `<div><span><b>${stockA(L.mkt, x.sym, x.name)}</b><small>점수 ${x.score ?? "-"} · 승률 ${x.win_prob ?? "-"}%</small></span>
          <span><b class="${x.bought ? "up" : ""}">${x.bought}개 플랜 매수</b><small>${x.skips.map((k) => `${esc(k.why)} ${k.n}`).join(" · ") || "모두 매수"}</small></span></div>`).join("") || `<div class="tiny">추천 종목이 없었어요</div>`}</div>`
      : `<div class="empty sm">아직 받은 추천이 없어요</div>`;
    const rank = `<div class="notice">${esc(L.note)}</div>
        <div class="lab-tbl" role="table" aria-label="플랜별 성적">
          <div class="lab-tr lab-th" role="row"><span>플랜</span><span>끝난 거래<small>지금 보유</small></span><span>승률</span><span>비용 뺀 수익률</span></div>
          ${L.runs.map((x, i) => `<button type="button" class="lab-tr" role="row" data-run="${x.id}">
            <span class="lab-nm"><i class="${x.closed >= 10 ? "" : "few"}">${x.closed >= 10 ? i + 1 : "·"}</i><span><b>${esc(x.label)}</b><small>${esc(x.desc)}</small></span></span>
            <span>${x.closed}${x.open ? `<small>${x.open > 1 ? `${x.open}종목 ` : ""}${x.open_qty}주 · ${x.open_pct}%</small>` : ""}</span>
            <span>${x.win_rate == null ? "-" : `${x.win_rate}%`}</span>
            ${x.closed || x.open ? `<span class="${tone(x.net_ret_pct)}">${pct(x.net_ret_pct, 2)}<small>비용 전 ${pct(x.ret_pct, 2)}${x.closed ? "" : " · 보유 중"}</small></span>` : `<span class="lab-none">-<small>거래 전</small></span>`}</button>`).join("")}
        </div>
        <details class="at-more"><summary>이 표 읽는 법</summary><div class="tiny">같은 추천을 받아 플랜만 다르게 사고판 결과예요. 수익률은 플랜에 배정된 가상 돈 전체 대비라, 같은 종목이어도 많이 산 플랜일수록 크게 움직여요(보유 칸의 %가 들어간 비중). 순위는 거래가 10건 넘은 플랜끼리만 매겨요. 추천 비용은 한 번만 들고 플랜 수로 나눠 적어요. 플랜을 누르면 그 매매 기록이 열려요.</div></details>
        ${L.status === "ended" ? "" : `<div class="grid2"><button type="button" class="btn sm" data-labctl="${L.id}:${L.status === "paused" ? "resume" : "pause"}">${L.status === "paused" ? "다시 시작" : "새 매수 멈춤"}</button>
          <button type="button" class="btn sm" data-labctl="${L.id}:end">실험 끝내기</button></div>`}`;
    const soldH = sold.length ? `<div class="lab-last">${sold.map((t) => `<div><span><b>${stockA(L.mkt, t.sym, t.name)}</b><small>${esc(t.label)} · ${t.qty}주 · ${hm(t.entry_ts)} ${M(t.entry)} → ${hm(t.exit_ts)} ${M(t.exit)}</small></span>
          <span><b class="${tone(t.pct)}">${pct(t.pct, 2)}</b><small>${esc(t.reason || "")} · ${M(t.pnl, true)}</small></span></div>`).join("")}</div>` : `<div class="empty sm">아직 판 기록이 없어요.<br>단타는 장 마감 전에, 스윙은 며칠 안에 채워져요.</div>`;
    return { hold, last: lastH, rank, sold: soldH };
  },
  atLabNew() {
    const A = this.at, P = (A.lab || {}).presets || [], F = { mkt: "kr", style: "day", budget: "1,000,000", days: 20 };
    openSheet("모의 실험 시작", `<div class="at-bud">
        <div class="field">시장${seg("실험시장", [["kr", "국내장"], ["us", "미국장"]], F.mkt, "on-card")}</div>
        <div class="field">방식${seg("실험방식", (A.o.styles || []).map((x) => [x.id, x.label]), F.style, "on-card")}</div>
        <div class="fields"><label class="field">플랜마다 쓸 돈(가상)<input id="lbB" class="r" inputmode="decimal" autocomplete="off" value="${F.budget}"></label>
          <label class="field">기간(일)<input id="lbD" class="r" inputmode="numeric" autocomplete="off" value="${F.days}"></label></div>
        <div class="field">시험할 플랜<div class="lab-pick">${P.map((x) => `<label><input type="checkbox" data-var="${x.key}" checked ${x.key === "base" ? "disabled" : ""}><span><b>${esc(x.label)}</b><small>${esc(x.desc)}</small></span></label>`).join("")}</div></div>
        <div id="lbPv" aria-live="polite"></div>
        <button type="button" class="btn dark wide" id="lbGo">실험 시작</button></div>`, (box) => {
      const body = () => ({ mkt: F.mkt, style: F.style, budget: num($("#lbB", box).value), days: Number($("#lbD", box).value) || 0, model: "auto",
        variants: $$("[data-var]", box).filter((i) => i.checked).map((i) => i.dataset.var) });
      let seq = 0;
      const pv = async () => {
        const my = ++seq, el = $("#lbPv", box);
        try {
          const v = await api("POST", "/api/auto/plan", Object.assign(body(), { mode: "paper" }));
          if (my === seq) el.innerHTML = this.atCostHtml(v) + `<div class="tiny">기간은 20거래일 이상을 권해요. 거래가 쌓여야 플랜 사이의 차이를 믿을 수 있어요.</div>`;
        } catch (e) { if (my === seq) el.innerHTML = `<div class="notice warn">${esc(e.message)}</div>`; }
      };
      bindSeg(box, "실험시장", (v) => { F.mkt = v; $("#lbB", box).value = v === "us" ? "1,000" : "1,000,000"; pv(); });
      bindSeg(box, "실험방식", (v) => { F.style = v; pv(); });
      $("#lbB", box).addEventListener("change", pv); $("#lbD", box).addEventListener("change", pv);
      $("#lbGo", box).onclick = async () => {
        try { await run(() => api("POST", "/api/auto/lab", body()), "실험을 시작했어요"); } catch (e) { return; }
        closeSheet(); this.auto($("#iv"));
      };
      pv();
    });
  },

  // ----- 새로 만들기
  async atNew(box) {
    const A = this.atState(), F = A.form;
    const O = A.o || (A.o = await GET("/api/auto"));
    if (!F.model) F.model = "auto";
    const cur = () => (F.mkt === "us" ? "달러" : "원");
    box.onclick = null;
    box.innerHTML = `<section class="ap-hero">
        <button type="button" class="at-back" id="atBack">‹ 목록</button>
        <div class="ap-h1">새 자동매매</div>
        <div class="ap-h2">다섯 가지만 정하면 플랜은 앱이 짜요. 아래에서 플랜을 확인한 뒤 시작해요.</div>
        ${seg("모드", [["paper", "모의(가상 돈)"], ["real", "실전(실제 주문)"]], F.mode, "ap-mkt")}
      </section>
      ${this.atReadyHtml(O.ready)}
      <div id="atRealNote"></div>
      <section class="card">
        <div class="card-head"><h2>어디서, 어떻게</h2></div>
        <div class="field">시장${seg("시장", [["kr", "국내장"], ["us", "미국장"]], F.mkt, "on-card")}</div>
        <div class="field">방식<div class="at-styles">${O.styles.map((s) => `<button type="button" class="ap-model" data-style="${s.id}" aria-pressed="${s.id === F.style}"><span class="ap-radio" aria-hidden="true"></span><span><b>${esc(s.label)}</b> <span class="tiny">${esc(s.sub)}</span></span></button>`).join("")}</div></div>
        <div class="fields">
          <label class="field"><span>쓸 돈(<span id="atCur">${cur()}</span>)</span><input id="atBudget" class="r" inputmode="decimal" autocomplete="off" placeholder="${F.mkt === "us" ? "3,000" : "3,000,000"}" value="${esc(F.budget)}"><span class="hint">이 금액 안에서만 사요</span></label>
          <label class="field">기간(일)<input id="atDays" class="r" inputmode="numeric" autocomplete="off" value="${esc(F.days)}"><span class="hint">끝나는 날 모두 정리해요</span></label>
        </div>
        <div class="chips at-days">${[[7, "1주"], [14, "2주"], [30, "1달"], [90, "3달"]].map(([d, l]) => `<button type="button" class="chip" data-days="${d}" aria-pressed="${Number(F.days) === d}">${l}</button>`).join("")}</div>
      </section>
      <section class="card">
        <div class="card-head"><h2>엔진</h2><span class="tiny">종목을 고르는 Claude 모델</span></div>
        <div class="ap-models">${[{ id: "auto", label: "자동 · 쓸 돈에 맞춰 모델·횟수를 골라요(권장)" }].concat(O.models).map((x) => `<button type="button" class="ap-model" data-model="${esc(x.id)}" aria-pressed="${x.id === F.model}"><span class="ap-radio" aria-hidden="true"></span><span><b>${esc(x.label.split(" · ")[0])}</b> <span class="tiny">${esc(x.label.split(" · ")[1] || "")}</span></span></button>`).join("")}</div>
      </section>
      <div id="atPlan"></div>`;
    const save = () => { F.budget = $("#atBudget").value; F.days = $("#atDays").value; };
    const pressed = (sel, attr, v) => $$(sel, box).forEach((b) => b.setAttribute("aria-pressed", String(b.dataset[attr] === String(v))));
    let tm = 0;
    const plan = (reset) => { clearTimeout(tm); if (reset) { F.daily = ""; F.total = ""; } tm = setTimeout(() => this.atPlan(), 350); };
    const realNote = () => {
      const R = O.ready.real;
      $("#atRealNote").innerHTML = F.mode !== "real" ? "" : R.ok
        ? `<div class="notice warn"><b>실전은 ${esc(R.label || "키움")} 계좌로 실제 주문이 나가요.</b> 모의로 충분히 돌려 본 뒤에 쓰는 걸 권해요.</div>`
        : `<div class="notice warn">${esc(R.why)} <a href="#settings/accounts">계좌 설정</a></div>`;
    };
    realNote();
    $("#atBack").onclick = () => { A.view = "list"; this.auto(box); };
    bindSeg(box, "모드", (v) => { F.mode = v; realNote(); plan(); });
    bindSeg(box, "시장", (v) => { F.mkt = v; F.budget = ""; $("#atBudget").value = ""; $("#atBudget").placeholder = v === "us" ? "3,000" : "3,000,000"; $("#atCur").textContent = cur(); plan(true); });
    $("#atBudget").addEventListener("input", (e) => { const raw = e.target.value.replace(/[^\d.]/g, ""); const n = Number(raw); e.target.value = raw && !raw.endsWith(".") && n ? n.toLocaleString("en-US", { maximumFractionDigits: 2 }) : raw; save(); plan(true); });
    $("#atDays").addEventListener("input", () => { save(); pressed("[data-days]", "days", F.days); plan(); });
    box.onclick = (e) => {
      const s = e.target.closest("[data-style]"), d = e.target.closest("[data-days]"), m = e.target.closest("[data-model]");
      if (s) { F.style = s.dataset.style; pressed("[data-style]", "style", F.style); if (F.style !== "day" && Number(F.days) < 7) { F.days = 30; $("#atDays").value = 30; pressed("[data-days]", "days", 30); } plan(true); }
      if (d) { F.days = Number(d.dataset.days); $("#atDays").value = F.days; pressed("[data-days]", "days", F.days); plan(); }
      if (m) { F.model = m.dataset.model; pressed("[data-model]", "model", F.model); plan(); }
    };
    this.atPlan();
  },
  // 플랜 미리보기의 AI 비용 칸: 어떤 엔진으로 하루 몇 번, 한 달에 얼마(쓸 돈의 몇 %), 실전이면 모의 검증 결과
  atCostHtml(pv) {
    const c = pv.cost, kr = pv.mkt === "kr", real = pv.mode === "real";
    if (!c) return "";
    return `<div class="at-cost ${c.ok ? "" : "over"}">
        <div class="at-cost-top"><span>AI 엔진${c.auto ? "(자동)" : ""}</span><b>${esc(c.label.split(" · ")[0])} · 하루 ${c.cycles}번${c.web ? "" : " · 웹 검색 없이"}</b></div>
        ${c.quick_gap ? `<div class="at-cost-top"><span>빠른 재추천</span><b>${c.quick_gap}분마다 · 하루 약 ${c.quick_n}번 · 한 번에 약 ${won(Math.max(1, Math.round(c.quick_usd * (c.fx || 1400))))}원</b></div>` : ""}
        <div class="at-cost-top"><span>한 달 AI 비용</span><b>약 ${kr ? `${won(c.month_cur)}원` : `$${c.month_usd}`}${real ? ` · 쓸 돈의 ${c.pct}%` : ""}</b></div>
        ${c.warn ? `<div class="notice warn">${esc(c.warn)}</div><div class="tiny">하루 AI 비용 한도 $${c.cap_day}(쓸 돈에 비례해 한 달 1%, 최소 하루 $3).</div>` : `<div class="tiny">하루 AI 비용 한도 $${c.cap_day}(쓸 돈에 비례해 한 달 1%, 최소 하루 $3) 안에서 맞췄어요.${real ? "" : " 모의도 AI 비용은 실제로 나가요."}${c.auto && !c.web ? " 비용에 맞추느라 웹 검색(뉴스)을 뺐어요." : ""}${c.quick_gap ? " 빠른 재추천은 가장 싼 엔진으로 달라진 흐름만 다시 물어요(이미 다룬 종목은 빼고)." : ""}</div>`}
        ${pv.ready ? `<div class="notice ${pv.ready.ok ? "" : "warn"}"><b>모의 검증</b> ${esc(pv.ready.text)}</div>` : ""}
      </div>`;
  },
  atBody() {
    const F = this.at.form;
    return { mode: F.mode, mkt: F.mkt, style: F.style, variant: F.mode === "real" ? F.variant || "" : "", budget: num(F.budget), days: Number(F.days) || 0, model: F.model, daily_loss_pct: F.daily, total_loss_pct: F.total, confirm: F.confirm };
  },
  async atPlan() {
    const A = this.at, F = A.form, el = $("#atPlan");
    if (!el || A.view !== "new") return;
    if (!num(F.budget)) { el.innerHTML = `<section class="card"><div class="empty">쓸 돈을 넣으면 플랜을 보여 드려요</div></section>`; return; }
    let pv;
    try { pv = A.pv = await api("POST", "/api/auto/plan", this.atBody()); }
    catch (e) { if (e instanceof Locked || e instanceof NeedConnect) throw e; el.innerHTML = `<div class="notice warn">${esc(e.message)}</div>`; return; }
    if (!$("#atPlan") || A.view !== "new") return;
    const p = pv.params, c = pv.ai_cost, real = F.mode === "real", ok = A.o.ready.ai && A.o.ready.data && (!real || A.o.ready.real.ok);
    F.daily = p.daily_loss_pct; F.total = p.total_loss_pct;
    el.innerHTML = `<section class="card">
        <div class="card-head"><h2>이 조건의 플랜</h2><span class="tiny">시작한 뒤에도 고칠 수 있어요</span></div>
        <ol class="at-plan">${pv.plan.map((s) => `<li><b>${esc(s.t)}</b><span>${esc(s.d)}</span></li>`).join("")}</ol>
        <div class="fields">
          <label class="field">하루 손실 한도(%)<input id="atDaily" class="r" inputmode="decimal" value="${p.daily_loss_pct}"></label>
          <label class="field">전체 손실 한도(%)<input id="atTotal" class="r" inputmode="decimal" value="${p.total_loss_pct}"></label>
        </div>
        ${pv.variant ? `<div class="notice">모의 실험에서 검증한 <b>${esc(pv.variant.label)}</b> 플랜의 기준을 그대로 가져왔어요(실전은 관망에 쉬는 것을 지켜요). <button type="button" class="chip" id="atVarOff">기본 플랜으로</button></div>` : ""}
        ${this.atCostHtml(pv)}
        <div class="tiny">AI 추천 비용: 정밀 추천 한 번에 약 ${won(c.per_usd * (c.fx || 1400))}원, 하루 최대 약 ${won(c.day_usd * (c.fx || 1400))}원. 이번 달 AI 예산 $${c.budget_usd ?? "-"} 가운데 $${Number(c.month_usd || 0).toFixed(2)} 사용.</div>
      </section>
      ${real ? `<section class="card"><label class="field"><span>실제 주문이 나가요. 시작하려면 <b>실전</b>이라고 적어 주세요</span><input id="atConfirm" autocomplete="off" placeholder="실전" value="${esc(F.confirm)}"></label></section>` : ""}
      <button type="button" class="btn ${real ? "red" : "dark"} wide" id="atStart" ${ok ? "" : "disabled"}>${real ? "실전으로 시작" : "모의로 시작"}</button>
      <div class="tiny" style="padding:0 3px">수익을 보장하지 않아요. 투자 판단과 책임은 본인에게 있어요.</div>`;
    const lim = () => { F.daily = $("#atDaily").value; F.total = $("#atTotal").value; };
    if ($("#atVarOff")) $("#atVarOff").onclick = () => { F.variant = ""; this.atPlan(); };
    $("#atDaily").onchange = () => { lim(); this.atPlan(); };
    $("#atTotal").onchange = () => { lim(); this.atPlan(); };
    if (real) $("#atConfirm").oninput = (e) => { F.confirm = e.target.value.trim(); };
    $("#atStart").onclick = async () => {
      lim();
      const d = await run(() => api("POST", "/api/auto/runs", this.atBody()), "자동매매를 시작했어요");
      F.confirm = ""; A.view = d.run.id; A.tab = "now"; A.d = d; A.o = null;
      this.auto($("#iv"));
    };
  },

  // ----- 한 건 자세히
  async atRun(box) {
    const A = this.atState();
    const d = (A.d = await GET(`/api/auto/runs/${A.view}`));
    if (investState.tab !== "auto" || A.view !== d.run.id) return;
    const r = d.run, m = r.money, M = (v, s) => this.atM(r.mkt, v, s), [lbl, cls] = this.AT_ST[r.status] || [r.status, ""];
    const N = d.now, next = N ? N.next.label : r.status_msg;
    const keep = $("#atBody") ? window.scrollY : null;
    box.onclick = null;
    box.innerHTML = `<section class="ap-hero at-hero">
        <button type="button" class="at-back" id="atBack">‹ 목록</button>
        <div class="at-run-top"><b>${esc(r.name)}</b><span class="tag ${cls}">${lbl}</span><span class="tag g">${r.mode === "real" ? esc(d.broker || "실전") : "모의"}</span></div>
        <div class="at-hero-eq">${M(m.equity)}</div>
        <div class="at-hero-ret ${tone(m.ret_pct)}">${M(m.equity - m.budget, true)} (${pct(m.ret_pct, 2)})</div>
        ${r.mode === "paper" && !r.lab_id && r.status !== "ended" ? `<button type="button" class="at-hero-tip" id="atToAuto">모의는 이제 <b>자동 테스트</b> 스위치 하나로 돌려요 ›</button>` : ""}
        ${m.ai_cost ? `<div class="at-hero-net">AI 비용 ${M(m.ai_cost)}을 빼면 <b class="${tone(m.net_pnl)}">${M(m.net_pnl, true)} (${pct(m.net_ret_pct, 2)})</b></div>` : ""}
        <div class="at-hero-kv">${["running", "paused"].includes(r.status) ? `<button type="button" id="atBud" aria-label="쓸 돈 바꾸기"><span>쓸 돈 <em>바꾸기 ›</em></span><b>${M(m.budget)}</b></button>` : `<div><span>쓸 돈</span><b>${M(m.budget)}</b></div>`}<div><span>남은 현금</span><b>${M(m.cash)}</b></div><div><span>확정 손익</span><b class="${tone(m.realized)}">${M(m.realized, true)}</b></div></div>
        ${N ? `${N.busy ? this.atScanHtml(N) : `<div class="at-nowline ${N.next.kind}"><span class="at-dot ${r.status === "running" && N.engine.ok ? "on" : ""}"></span>
          <div><b>${esc(next)}</b><span>${this.atNextLine(N)}</span>${N.next.code === "aicost" ? `<button type="button" class="btn yellow sm at-lim-go" data-ailim>AI 비용 한도 올리기</button>` : ""}${N.next.span_sec && N.next.in_sec != null ? `<i class="at-gap" data-gap="${N.next.in_sec}" data-span="${N.next.span_sec}" data-t0="${Date.now()}" aria-hidden="true"><i style="width:${Math.max(0, Math.min(100, (1 - N.next.in_sec / N.next.span_sec) * 100)).toFixed(1)}%"></i></i>` : ""}</div></div>`}
        <div class="ap-h2">${r.end_date.slice(5).replace("-", "/")}까지(${N.period.day}일째/${N.period.days}일) · 플랜 ${r.ver}판 · 엔진 ${N.engine.ok ? `정상(${N.engine.sec}초 전 확인)` : "확인 필요"}</div>`
          : `<div class="ap-h2">${esc(next || "")}</div>`}
        ${r.status === "ended" ? "" : `<div class="at-ctl">
          ${r.status === "running" ? `<button type="button" class="btn line-w" data-ctl="pause">새 매수 멈춤</button>` : r.status === "paused" ? `<button type="button" class="btn yellow" data-ctl="resume">다시 시작</button>` : ""}
          ${r.status === "closing" ? "" : `<button type="button" class="btn line-w" data-ctl="end">전부 팔고 종료</button>`}</div>`}
        ${seg("자동탭", [["now", "현황"], ["flow", "흐름도"], ["log", "기록"], ["stat", "분석"], ["plan", "플랜"]], A.tab, "ap-mkt")}
      </section><div id="atBody" class="at-body"></div>`;
    $("#atBack").onclick = () => { A.view = A.back === "lab" && d.run.lab_id ? "lab" : "list"; A.back = null; A.o = null; this.auto(box); };
    bindSeg(box, "자동탭", (v) => { A.tab = v; this.atTab(); });
    if ($("#atBud")) $("#atBud").onclick = () => this.atBudget();
    if ($("#atToAuto")) $("#atToAuto").onclick = () => { A.view = "list"; A.o = null; this.auto(box); };
    $$("[data-ctl]", box).forEach((b) => (b.onclick = async () => {
      const a = b.dataset.ctl;
      if (a === "end" && !confirmTap(b, d.open.length ? "보유 종목을 모두 팔고 끝내요. 한 번 더 누르기" : "한 번 더 누르면 종료")) return;
      await run(() => api("POST", `/api/auto/runs/${r.id}/control`, { action: a }), a === "pause" ? "새 매수를 멈췄어요" : a === "resume" ? "다시 시작했어요" : "종료를 시작했어요");
      this.auto(box);
    }));
    this.atTab();
    if (keep != null) window.scrollTo(0, keep);
  },
  // 현황 맨 위: 오늘 시간표, 오늘 한 일과 한도, 다음 매수 규모, 최근 추천
  atNowHtml(d) {
    const r = d.run, N = d.now, mkt = r.mkt, M = (v, s) => this.atM(mkt, v, s), T = N.today, Z = N.size, P = N.period;
    // 시간표: 지나간 마지막 단계가 '지금'(진행 중인 구간), 그다음이 '다음'. 아직 아무것도 안 지났으면 첫 단계가 '다음'
    const rows = N.steps.rows, nextIdx = N.steps.today ? rows.findIndex((x) => !x.done) : 0;
    const curIdx = N.steps.today ? (nextIdx < 0 ? rows.length - 1 : nextIdx - 1) : -1, hotIdx = curIdx >= 0 ? curIdx : nextIdx;
    const dayPnl = T.pnl + T.unreal, dayUsed = Math.min(100, Math.max(0, -dayPnl) / (T.limit || 1) * 100), totUsed = Math.min(100, N.risk.loss / (N.risk.limit || 1) * 100);
    const gauge = (label, used, left, cls) => `<div class="at-g ${cls || ""}"><div class="at-g-t"><span>${label}</span><b>${left}</b></div><div class="at-g-b" role="img" aria-label="${label} ${Math.round(used)}% 사용"><i style="width:${Math.max(used, used > 0 ? 3 : 0)}%"></i></div></div>`;
    const warn = [];
    if (!N.engine.ok) warn.push(N.engine.sec == null ? "엔진이 아직 한 번도 돌지 않았어요. 노트북에서 앱이 켜져 있는지 확인해 주세요." : `엔진이 ${N.engine.sec}초째 돌지 않았어요. 노트북의 앱을 확인해 주세요.`);
    const L = N.last_cycle;
    return `${this.atReadyHtml(d.ready)}${warn.map((w) => `<div class="notice warn">${esc(w)}</div>`).join("")}
      <section class="card at-sched">
        <div class="card-head"><h2>${N.steps.today ? "오늘" : esc(N.steps.label)} 시간표</h2><span class="tiny">지금 ${esc(N.clock.kst)}${N.clock.local ? ` · 뉴욕 ${esc(N.clock.local)}` : ""}${N.phase === "open" ? ` · 마감까지 ${N.clock.left_min}분` : ""}</span></div>
        ${N.steps.note ? `<div class="notice">${esc(N.steps.note)}. 시간표와 매수 시각을 그만큼 미뤘어요.</div>` : ""}
        <ol class="at-steps">${rows.map((x, i) => `<li class="${x.done && i !== curIdx ? "done" : ""} ${i === hotIdx ? "next" : ""}"${i === curIdx ? ' aria-current="step"' : ""}><time>${esc(x.hm)}${x.local ? `<small>뉴욕 ${esc(x.local)}</small>` : ""}</time>
          <div><b>${esc(x.name)}${i === curIdx ? ` <em>지금</em>` : i === nextIdx && N.steps.today ? ` <em class="soon">다음</em>` : ""}</b><span>${esc(x.sub)}</span></div></li>`).join("")}</ol>
        ${N.steps.today ? "" : `<div class="tiny">${N.phase === "off" ? "오늘은 장이 쉬는 날이라" : "오늘 장은 끝나서"} 다음 거래일 시간표를 보여 드려요.</div>`}
        ${mkt === "us" ? `<div class="tiny">시각은 한국 시간이에요.</div>` : ""}
      </section>
      <section class="card at-today">
        <div class="card-head"><h2>오늘 한 일</h2><span class="tiny">${N.busy ? "AI 추천 받는 중" : N.phase === "open" ? "장 열림" : N.phase === "pre" ? "장 시작 전" : N.phase === "post" ? "장 마감" : "쉬는 날"}</span></div>
        <div class="sc-kpi"><div><span>AI 추천${T.quick_gap ? ` · ${T.quick_gap}분마다` : ""}</span><b>${T.quick_gap ? `${T.cycles}<small>번 · 정밀 ${T.full}/${T.cycles_max} · 빠른 ${T.quick}</small>` : `${T.cycles}<small> / ${T.cycles_max}번</small>`}</b></div><div><span>매수 · 매도</span><b>${T.buys} · ${T.sells}${T.sells ? `<small> (승 ${T.wins})</small>` : ""}</b></div>
          <div><span>오늘 손익</span><b class="${tone(dayPnl)}">${M(dayPnl, true)}</b></div></div>
        ${gauge("하루 손실 한도", dayUsed, `${M(Math.min(0, dayPnl))} / -${M(T.limit)}`, dayUsed >= 100 ? "full" : "")}
        ${gauge("전체 손실 한도", totUsed, `${M(N.risk.loss ? -N.risk.loss : 0)} / -${M(N.risk.limit)}`, totUsed >= 100 ? "full" : "")}
        ${gauge("기간", P.day / P.days * 100, `${P.day}일째 / ${P.days}일 · ${P.left}일 남음`, "calm")}
        ${T.streak ? `<div class="tiny">오늘 연속 손절 ${T.streak}번 · ${T.streak_max}번이면 오늘은 쉬어요</div>` : ""}
      </section>
      <section class="card tight"><div class="card-head" style="padding-top:8px"><h2>다음에 살 때</h2></div>
        <dl class="dt-rows at-facts">
          <div><dt>한 번에 사는 금액</dt><dd>약 ${M(Z.alloc)}<small>굴리는 돈의 ${Z.pct}%${Z.mult < 1 ? ` × 검증 단계 ${Math.round(Z.mult * 100)}%` : ""}</small></dd></div>
          <div><dt>보유 자리</dt><dd>${Z.slots_used} / ${Z.slots}종목<small>${Z.slots_used >= Z.slots ? "꽉 차서 팔릴 때까지 새로 사지 않아요" : `${Z.slots - Z.slots_used}자리 비어 있어요`}</small></dd></div>
          ${Z.warm_need ? `<div><dt>검증 단계</dt><dd>${Z.warm_done} / ${Z.warm_need}건<small>${Z.warm_done >= Z.warm_need ? "끝났어요. 정해진 비중을 다 써요" : "끝나면 정해진 비중을 다 써요"}</small></dd></div>` : ""}
          <div><dt>고르는 기준</dt><dd>승률 ${d.params.min_win_prob}% · 점수 ${d.params.min_score} · 손익비 ${d.params.min_rr} 이상<small>${d.params.skip_watch ? "AI가 관망이면 쉬어요" : ""}</small></dd></div>
          <div><dt>AI 비용${r.status === "ended" ? "" : `<button type="button" class="chip at-lim" data-ailim>한도 바꾸기</button>`}</dt><dd class="${N.ai.today_usd >= N.ai.limit_usd ? "down" : ""}">오늘 $${N.ai.today_usd.toFixed(2)} / 한도 $${N.ai.limit_usd}<small>지금까지 ${N.ai.cycles_all}번 · $${N.ai.total_usd.toFixed(2)}</small></dd></div>
        </dl></section>
      <section class="card at-last">
        <div class="card-head"><h2>최근 추천</h2><span class="tiny">${L ? esc(L.ts.replace("T", " ").slice(5, 16)) : ""}</span></div>
        ${L ? `<div class="small">${L.lite ? `<span class="tag y">빠른 재추천</span> ` : ""}${L.stance ? `<span class="tag">${esc(L.stance)}</span> ` : ""}${esc(L.view || L.msg)}</div>
          ${L.picks ? `<div class="at-last-l">${L.bought.map((x) => `<div class="buy"><b>${esc(x)}</b><span>샀어요</span></div>`).join("")}${L.skipped.map((x) => `<div><b>${esc(x.name)}</b><span>${esc(x.why || "건너뜀")}</span></div>`).join("")}</div>` : `<div class="tiny">${esc(L.msg)}</div>`}`
          : `<div class="empty small">아직 받은 추천이 없어요.${N.next.kind === "at" ? `<br>첫 추천은 ${esc(N.next.text)}에 받아요.` : ""}</div>`}
      </section>`;
  },
  // 분석 탭: 추천받은 종목을 얼마나 샀는지, 왜 건너뛰었는지, 건너뛴 종목이 그 뒤 실제로 어땠는지
  atOppHtml(d) {
    const O = d.opp;
    if (!O || !O.cycles) return "";
    const res = (x) => (x.graded ? `<span class="${tone(x.avg)}">${pct(x.avg, 2)}</span><small>${x.graded}건 · ${x.win}% 이익</small>` : `<small>결과 대기</small>`);
    return `<section class="card at-opp">
        <div class="card-head"><h2>기회 분석</h2><span class="tiny">${esc(O.scope)} · ${O.days}거래일 · 추천 ${O.cycles}번</span></div>
        <div class="sc-kpi"><div><span>추천받은 종목</span><b>${O.picks}</b></div><div><span>산 종목</span><b>${O.bought}<small> · 하루 ${O.per_day}건</small></b></div><div><span>매수율</span><b>${O.buy_rate}%</b></div></div>
        ${(O.notes || []).length ? `<ul class="at-notes">${O.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : ""}
        ${O.reasons.length ? `<div class="at-tbl at-opp-t"><div class="at-tr at-th"><span>건너뛴 이유</span><span>건수</span><span>그 뒤 실제 결과</span></div>
          ${O.reasons.map((x) => `<div class="at-tr"><span>${esc(x.label)}</span><span>${x.n}</span><span class="at-opp-r">${res(x)}</span></div>`).join("")}</div>` : ""}
        <div class="tiny">'그 뒤 실제 결과'는 건너뛴 종목을 AI가 낸 목표·손절대로 샀다고 쳤을 때의 수익률(비용 뺀 값)이에요. 이 결과가 8건 이상 쌓이고 벌었을 때만 그 기준을 한 단계 낮추자고 해요. 돈이 모자라 못 산 경우는 결과를 기다리지 않고 종목 수를 줄이는 안을 내요.</div>
      </section>`;
  },
atAttrHtml(st) {
    // 성과 분해: 번 돈이 지수 덕인지, 종목을 잘 골라서인지, 비용이 얼마나 깎았는지. 그리고 지수를 그냥 들고 있었을 때와 비교
    const a = st.attr || {}, b = st.bench;
    if (!a.n && !b) return "";
    const part = a.n ? `<div class="sc-kpi"><div><span>지수가 움직여서</span><b class="${tone(a.market)}">${pct(a.market, 2)}</b></div><div><span>종목을 골라서</span><b class="${tone(a.pick)}">${pct(a.pick, 2)}</b></div>
        <div><span>비용</span><b class="${tone(a.cost)}">${pct(a.cost, 2)}</b></div></div>
      <div class="tiny">거래 한 번 평균 ${pct(a.net, 2)}을 셋으로 나눈 값이에요(${esc(a.index || "지수")} 기준, ${a.n}건). 지수보다 더 오른 거래는 ${a.beat}% · 보유 중 최고에서 팔 때까지 되돌려 준 폭은 평균 ${pct(-Math.abs(a.giveback || 0), 2)}.${a.total > a.n ? ` 지수 값을 적기 전의 ${a.total - a.n}건은 빠졌어요.` : ""}</div>`
      : `<div class="tiny">새로 사는 거래부터 살 때·팔 때의 지수 값을 적어요. 그 거래가 끝나면 번 돈을 '지수 덕'과 '종목 선택'으로 나눠 보여 드려요.</div>`;
    const bench = b && b.run_pct != null ? `<div class="tiny">같은 기간(${esc(b.since)}부터 ${b.days}거래일) ${esc(b.name)}를 그냥 들고 있었다면 <b class="${tone(b.idx_pct)}">${pct(b.idx_pct, 2)}</b>, 이 자동매매의 확정 손익은 굴리는 돈 대비 <b class="${tone(b.run_pct)}">${pct(b.run_pct, 2)}</b>예요(AI 비용 전).</div>` : "";
    return `<section class="card tight"><div class="card-head" style="padding-top:8px"><h2>수익이 어디서 왔나</h2></div>${part}${bench}</section>`;
  },
  atPrepHtml(d) {
    // 장 시작 전 준비: 넓게 훑기(국내) · 장 시작 전 리서치 메모 · 보유 종목 재료 확인
    const P = d.prep;
    if (!P) return "";
    const sc = P.scan || {}, b = P.brief, kr = d.run.mkt === "kr", cf = P.conf || {};
    const num = (v) => (v == null ? "–" : (v > 0 ? "+" : "") + Number(v).toLocaleString("ko-KR"));
    const scan = !kr ? "" : `<div class="at-prep-h"><b>넓게 훑기</b><span class="tiny">${sc.busy ? esc((sc.note || {}).msg || "훑는 중") : sc.day ? `${esc(sc.day.slice(5))} 장 마감 기준${sc.fresh ? "" : " · 오래돼서 쓰지 않아요"}` : "아직 없어요"}</span>
        <button type="button" class="btn sm" data-prep="scan" ${sc.busy ? "disabled" : ""}>${sc.busy ? "훑는 중" : "지금 훑기"}</button></div>
      ${(sc.top || []).length ? `<details class="at-more"><summary>상위 ${sc.top.length}종목 보기</summary><div class="at-prep-t">${sc.top.map((x) => `<div><span>${x.rank}</span><b>${esc(x.name || x.sym)}</b><span>점수 ${x.score}</span><span>20일 ${pct(x.r20, 1)}</span><span>외국인 ${num(x.f5)}억 · 기관 ${num(x.o5)}억</span></div>`).join("")}</div></details>` : ""}
      <div class="tiny">장이 끝나면 거래대금이 큰 종목 200개의 일봉과 수급을 훑어, 위쪽 종목을 스윙·장기 추천 후보에 6개까지 보태요. 점수는 어림값이라 추천 성적의 '종목 출처'에서 따로 재요.</div>`;
    const brief = `<div class="at-prep-h"><b>장 시작 전 리서치</b><span class="tiny">${P.brief_busy ? "받는 중" : !cf.brief ? "꺼 둠" : b && b.today ? `${esc(b.ts.slice(11, 16))} 받음 · $${(b.usd || 0).toFixed(3)}` : "오늘은 아직 없어요"}</span>
        <button type="button" class="btn sm" data-prep="brief" data-on="${cf.brief ? 1 : 0}">${cf.brief ? "끄기" : "켜기"}</button></div>
      ${b && b.text ? `<details class="at-more"${b.today ? " open" : ""}><summary>${b.today ? "오늘" : esc(b.day.slice(5))} 메모 보기</summary><div class="ai-ans">${mdLite(b.text)}</div></details>` : ""}
      <div class="tiny">자동매매가 도는 시장만, 장이 열리기 30~90분 전에 하루 한 번 AI가 밤사이 뉴스·일정을 찾아 메모를 남기고, 그날 정밀 추천이 함께 읽어요. 주문은 내지 않아요.</div>`;
    const hold = (P.hold || []).length ? `<div class="at-prep-h"><b>보유 종목 재료 확인</b><span class="tiny">오늘</span></div>
      <div class="at-prep-t">${P.hold.map((x) => `<div><b>${esc(x.sym)}</b><span class="tag ${x.verdict === "무효" ? "warn" : ""}">${esc(x.verdict)}</span><span>${esc(x.why || "")}</span></div>`).join("")}</div>` : "";
    const my = `<div class="at-prep-h"><b>내 의견</b><span class="tiny">메모는 AI가 함께 읽고, 제외 종목은 사지 않아요</span><button type="button" class="btn sm" data-prep="my">열기</button></div>`;
    return `<section class="card tight at-prep"><div class="card-head" style="padding-top:8px"><h2>장 시작 전 준비</h2></div>${scan}${brief}${my}${hold}</section>`;
  },
  atRiskHtml(d) {
    // 위험 점검(몬테카를로): 지금 보유의 '나쁜 날' 손실과 하루 한도, 지금까지의 거래를 다시 뽑아 본 앞날
    const R = d.risk;
    if (!R || R.error) return "";
    const h = R.hold, f = R.fwd, M = (v) => this.atM(d.run.mkt, v);
    const use = h ? Math.min(100, h.var / R.day_limit * 100) : 0;
    return `<section class="card tight rkc"><div class="card-head" style="padding-top:8px"><h2>위험 점검</h2><span class="tag ${R.guard ? "y" : ""}">${R.guard ? "미리 막기 켜짐" : "보기만"}</span></div>
      ${h ? `<div class="rkc-m"><div class="rkc-t"><span>지금 보유 ${h.n}종목 · 나쁜 날 하루 손실</span><b class="${h.over ? "down" : ""}">${h.n ? `−${h.var}%` : "없음"}${h.n ? ` <small>약 ${M(h.krw)}</small>` : ""}</b></div>
          <div class="rkc-bar ${h.over ? "over" : ""}" role="img" aria-label="하루 손실 한도의 ${Math.round(h.var / R.day_limit * 100)}%"><i style="width:${use.toFixed(1)}%"></i></div>
          <div class="rkc-s"><span>0</span><span>하루 손실 한도 −${R.day_limit}%</span></div>
          ${h.gap ? `<div class="tiny">손절 주문이 듣지 않는 날(갭·${d.run.mkt === "kr" ? "하한가" : "급락"})에 가장 크게 든 종목이 −${h.gap.drop}%까지 밀리면 굴리는 돈의 <b class="down">−${h.gap.pct}%</b>예요.</div>` : ""}</div>
        ${h.over ? `<div class="notice warn">스무 날에 한 번쯤 오는 나쁜 날이면 하루 손실 한도를 넘을 수 있는 크기예요.${R.guard ? "" : " 실험 플랜 '위험 미리 막기'는 이럴 때 살 때부터 수량을 줄여요."}</div>` : ""}` : `<div class="tiny">보유 종목의 일봉을 받지 못해 나쁜 날 손실은 계산하지 못했어요.</div>`}
      ${f ? `<div class="sc-kpi"><div><span>손실 한도에 닿을 확률</span><b class="${f.p_ruin >= 20 ? "down" : ""}">${f.p_ruin}%</b></div>
          <div><span>이익으로 끝날 확률</span><b>${f.p_profit}%</b></div>
          <div><span>우연이 아닐 가능성</span><b>${R.edge == null ? "–" : Math.round(R.edge * 100) + "%"}</b></div></div>
        <div class="tiny">지금까지의 거래 ${R.n}건을 무작위로 다시 뽑아 앞으로 약 ${f.next}번의 거래를 ${f.n.toLocaleString("ko-KR")}번 그려 본 값이에요. 굴리는 돈 대비 <b class="${tone(f.p5)}">${pct(f.p5, 1)}</b> ~ <b class="${tone(f.p95)}">${pct(f.p95, 1)}</b>(가운데 ${pct(f.p50, 1)}) 사이에 열에 아홉이 들어오고, 그사이 가장 깊은 낙폭은 나쁜 경우 ${f.mdd95}%예요. 전체 손실 한도는 −${R.total_limit}%${R.loss_now ? `, 지금 −${R.loss_now}%` : ""}. 앞으로도 지금까지처럼 거래한다고 가정한 어림값이에요.</div>`
        : `<div class="tiny">끝난 거래가 ${R.need}건이 되면, 그 거래들을 다시 뽑아 '전체 손실 한도에 닿을 확률'과 '성적이 우연이 아닐 가능성'도 보여 드려요(지금 ${R.n}건).</div>`}</section>`;
  },
  atSlipHtml(st) {
    // 체결 품질: 사기로·팔기로 정했을 때 본 값과 실제로 체결된 값의 차이
    const s = st.slip || {};
    if (!s.n) return "";
    const v = (x) => (x == null ? "–" : pct(x, 2));
    return `<section class="card tight"><div class="card-head" style="padding-top:8px"><h2>체결 품질</h2><span class="tag">${s.real ? "실제 체결가" : "모의 가정"}</span></div>
      <div class="sc-kpi"><div><span>살 때</span><b class="${tone(-(s.buy || 0))}">${v(s.buy)}</b></div><div><span>팔 때</span><b class="${tone(s.sell)}">${v(s.sell)}</b></div>
        <div><span>한 번 사고팔 때</span><b class="${tone(-s.round)}">${pct(-s.round, 2)}</b></div></div>
      <div class="tiny">정했을 때 본 값보다 얼마나 비싸게 사고 싸게 팔았는지예요(${s.n}건 평균). ${s.real ? "증권사 체결가로 잰 값이에요." : "모의는 앱이 정해 둔 가정값이라 늘 비슷해요. 실전에서 진짜 값이 쌓여요."}
        ${(s.by_time || []).length > 1 ? ` 살 때 시간대별: ${s.by_time.map((x) => `${esc(x.key)} ${pct(x.buy, 2)}(${x.n}건)`).join(" · ")}` : ""}</div></section>`;
  },
    atPropHtml(p, mkt) {
    const pend = p.status === "pending";
    const stLbl = { applied: "반영함", rejected: "반영 안 함", none: "바꿀 것 없음", pending: "승인 대기" }[p.status] || p.status;
    return `<section class="card at-prop ${pend ? "pend" : ""}" data-prop="${p.id}">
      <div class="card-head"><h2>${p.source === "ai" ? "AI 복기 개선안" : "자동 분석 개선안"}</h2><span class="tag ${pend ? "warn" : ""}">${stLbl}</span></div>
      <div class="small">${esc(p.summary)}</div>
      ${p.changes.length ? `<div class="at-chg">${p.changes.map((c) => `<div><b>${esc(c.label || c.key)}${c.loosen ? ` <span class="tag">매수 늘리기</span>` : ""}</b><span class="at-arrow">${esc(String(c.from))} → <em>${esc(String(c.to))}</em></span><span class="tiny">${esc(c.why || "")}</span></div>`).join("")}</div>` : ""}
      ${((p.stats || {}).notes || []).length ? `<ul class="at-notes">${p.stats.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : ""}
      ${p.text ? `<details class="at-more"><summary>AI 복기 전문 보기</summary><div class="ai-ans">${mdLite(p.text)}</div></details>` : ""}
      ${pend ? `<div class="grid2"><button type="button" class="btn" data-dec="reject">반영 안 함</button><button type="button" class="btn dark" data-dec="apply">승인하고 반영</button></div>` : `<div class="tiny">${esc((p.decided_at || p.ts).replace("T", " ").slice(5, 16))}</div>`}
    </section>`;
  },
  atTradeDt(t, mkt) {
    const M = (v, s) => this.atM(mkt, v, s), w = t.why || {};
    return dt({ title: t.name, rows: [
      ["상태", t.status === "open" ? (t.fill_ok ? "보유 중" : "체결 확인 중") : t.reason, t.status === "closed" ? tone(t.pnl) : ""],
      ["손익", t.pnl == null ? null : `${M(t.pnl, true)} (${pct(t.pnl_pct, 2)})`, tone(t.pnl)],
      ["수량", `${won(t.qty)}주`], ["산 값", M(t.entry_price)], ["판 값", t.exit_price ? M(t.exit_price) : null],
      ["산 때", (t.entry_ts || "").replace("T", " ").slice(5, 16)], ["판 때", t.exit_ts ? t.exit_ts.replace("T", " ").slice(5, 16) : null],
      ["목표 / 손절", `+${t.target_pct}% / -${t.stop_pct}%`], ["보유 중 최고 / 최저", t.mfe_pct == null ? null : `${pct(t.mfe_pct, 2)} / ${pct(t.mae_pct, 2)}`],
      ["정리 예정", t.exit_date || t.exit_by || null], ["AI 승률 · 점수", `${t.win_prob ?? "-"}% · ${t.score ?? "-"}`], ["비용", t.fees == null ? null : M(t.fees)], ["플랜", `${t.ver}판`]],
      note: [w.view ? `시장: ${w.view}` : "", (w.reasons || []).length ? "산 이유\n- " + w.reasons.join("\n- ") : "", (w.risks || []).length ? "위험\n- " + w.risks.join("\n- ") : ""].filter(Boolean).join("\n\n"),
      links: [["종목 보기", stockHref(t.sym, mkt)]] });
  },
  atFlowBtn(rs) {
    const b = (rs || []).find((r) => r.variant === "base") || (rs || [])[0];
    return b ? `<button type="button" class="fl-go" data-run="${b.id}" data-attab="flow"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="5" cy="5" r="2.5"/><circle cx="5" cy="19" r="2.5"/><circle cx="19" cy="12" r="2.5"/><path d="M5 7.5v9M7.3 6l9.4 4.8M7.3 18l9.4-4.8"/></svg><span><b>전 과정 흐름도</b><small>기본 플랜이 지금 어느 단계인지</small></span><i aria-hidden="true">›</i></button>` : "";
  },
  // ===== 엔진 노트: 걸어온 길 · 지금 로직 · 앞으로 =====
  async evo(box) {
    const E = (this.ev = this.ev || { tab: store.get("evoTab") || "now", f: "all", d: null });
    if (!E.d) box.innerHTML = `<div class="empty loading">엔진 노트를 모으는 중</div>`;
    else this.evoDraw(box);
    let d;
    try { d = await GET("/api/evo"); } catch (e) {
      if (e.code === "old_server") return oldServerBox(box, () => this.evo(box));
      if (!E.d) box.innerHTML = `<div class="notice warn">${esc(e.message)}</div>`; return;
    }
    if (investState.tab !== "evo") return;
    E.d = d;
    this.evoDraw(box);
  },
  evoDraw(box) {
    const E = this.ev, D = E.d, O = D.overview;
    const P = { now: () => this.evoNow(D), rules: () => this.evoRules(D), road: () => this.evoRoad(D), logic: () => this.evoLogic(D), next: () => this.evoNext(D) };
    if (!P[E.tab] || (E.tab === "rules" && !D.rules)) E.tab = "now";
    box.innerHTML = `<section class="ev-hero card">
        <div class="ev-hh"><b>엔진 노트</b><span>${O.since ? `${esc(O.since.slice(2).replace(/-/g, "."))}부터 ${O.days}일째 · ` : ""}수익률을 높이려고 해 온 일과 지금 규칙, 다음 수</span></div>
        ${seg("엔진노트", [["now", "한눈에"], ["rules", "매매 규칙"], ["road", "걸어온 길"], ["logic", "지금 로직"], ["next", "앞으로"]], E.tab, "ap-mkt ev-seg")}
      </section><div id="evBody" class="ev-body">${P[E.tab]()}</div>`;
    bindSeg(box, "엔진노트", (v) => { E.tab = v; store.set("evoTab", v); this.evoDraw(box); window.scrollTo(0, 0); });
    box.onclick = (e) => {
      const f = e.target.closest("[data-evf]");
      if (f) { E.f = f.dataset.evf; $("#evBody").innerHTML = this.evoRoad(D); return; }
      const g = e.target.closest("[data-evtab]");
      if (g) { E.tab = g.dataset.evtab; store.set("evoTab", E.tab); this.evoDraw(box); window.scrollTo(0, 0); }
    };
  },
  evoCurve(C) {
    const W = 320, H = 92, xs = C.days || [], lines = [["paper", "모의"], ["real", "실전"]].filter(([k]) => (C[k] || []).some((v) => v));
    if (xs.length < 2 || !lines.length) return `<div class="ev-nocurve">끝난 거래가 쌓이면 날마다 AI 비용까지 뺀 누적 손익을 선으로 그려요.</div>`;
    const all = lines.flatMap(([k]) => C[k]).concat(0), mn = Math.min(...all), mx = Math.max(...all), sp = mx - mn || 1;
    const X = (i) => (i / (xs.length - 1)) * W, Y = (v) => 6 + (1 - (v - mn) / sp) * (H - 12);
    const path = (k) => C[k].map((v, i) => `${i ? "L" : "M"}${X(i).toFixed(1)} ${Y(v).toFixed(1)}`).join(" ");
    return `<svg class="ev-curve" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="날짜별 누적 손익(AI 비용 뺌)">
        <line class="z" x1="0" x2="${W}" y1="${Y(0).toFixed(1)}" y2="${Y(0).toFixed(1)}"/>${lines.map(([k]) => `<path class="${k}" d="${path(k)}"/>`).join("")}</svg>
      <div class="ev-cx"><span>${esc(xs[0].slice(5).replace("-", "/"))}</span><span class="ev-lg">${lines.map(([k, t]) => `<i class="${k}"></i>${t}`).join(" ")}</span><span>${esc(xs[xs.length - 1].slice(5).replace("-", "/"))}</span></div>`;
  },
  evoNow(D) {
    const O = D.overview, A = O.auto, L = D.logic;
    const money = (v) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${won(Math.abs(v))}원`;
    const k = (t, v, sub, cls = "") => `<div class="ev-k ${cls}"><span>${t}</span><b>${v}</b>${sub ? `<small>${sub}</small>` : ""}</div>`;
    const mode = (m, t) => { const x = A[m]; return x.n ? k(t, `<em class="${tone(x.net)}">${money(x.net)}</em>`, `${x.n}건 · 승률 ${x.win}% · 평균 ${pct(x.avg, 2)} · AI 비용 ${won(x.cost)}원 뺌`) : k(t, "-", t === "실전 자동매매" ? "아직 실전 거래가 없어요" : "끝난 거래가 아직 없어요"); };
    return `<section class="card ev-main">
        <div class="ev-mh"><span>AI 비용까지 뺀 누적 손익</span></div>
        ${this.evoCurve(O.curve)}
        <div class="ev-ks two">${mode("paper", "모의 자동매매")}${mode("real", "실전 자동매매")}</div>
      </section>
      <section class="card"><div class="ev-ks">
        ${k("AI 추천", `${won(O.picks)}번`, O.graded ? `채점 ${won(O.graded)}건 · 평균 ${pct(O.pick_avg, 2)} · 이익 ${O.pick_win}%` : "채점 전")}
        ${k("AI 비용", `${won(O.cost_all)}원`, `전체 $${O.usd_all} · 이번 달 $${O.usd_month}`)}
        ${k("돌고 있는 자동매매", `${O.runs}개`, O.auto_on ? "모의 자동 테스트 켜짐" : "모의 자동 테스트 꺼짐")}
        ${k("스스로 고친 횟수", `${O.changes.plan + O.changes.engine + O.changes.lessons}번`, `플랜 ${O.changes.plan} · 엔진 ${O.changes.engine} · 추천 규칙 ${O.changes.lessons}판`)}
      </div><button type="button" class="ev-more" data-evtab="road">앱 개선 ${O.dev}번과 스스로 고친 기록 보기 ›</button></section>
      <section class="card"><h2 class="ev-h2">수익률을 위한 원칙</h2>
        <ol class="ev-pr">${L.principles.map((x) => `<li><b>${esc(x.t)}</b><span>${esc(x.d)}</span></li>`).join("")}</ol></section>
      <div class="tiny" style="padding:0 3px">${esc(D.at.replace("T", " "))} 기준 · 미국 손익은 지금 환율(${won(O.fx)}원)로 바꿔 더했어요.</div>`;
  },
  // 매매 규칙: 기본 원칙 · 지금 들어 있는 매수·매도 규칙 전부(최근 쓰임과 판정) · 변경 이력
  evoRules(D) {
    const R = D.rules, K = { add: ["더함", "g"], change: ["고침", "y"], remove: ["지움", "warn"], check: ["전체 점검", "v"] }, SD = { buy: "매수", sell: "매도", both: "매수·매도" };
    const row = (r) => `<li class="ev-rl ${r.tone ? "rk-" + r.tone : ""}"><div class="ev-rlh"><b>${esc(r.t)}</b>${r.since ? `<span class="ev-rv">${esc(r.since)}</span>` : ""}${r.tone === "check" ? `<span class="tag warn">검토</span>` : r.tone === "keep" ? `<span class="tag g">유지</span>` : ""}</div>
        <p>${esc(r.why)}</p>${r.verdict ? `<small>${esc(r.verdict)}</small>` : ""}</li>`;
    const block = (list, stages) => stages.map((st) => { const xs = list.filter((r) => r.stage === st), J = xs.filter((r) => r.kind === "judge"), O = xs.filter((r) => r.kind !== "judge");
      return xs.length ? `<h3 class="ev-h3">${st} <span>${xs.length}</span></h3>${J.length ? `<ul class="ev-rls">${J.map(row).join("")}</ul>` : ""}
        ${O.length ? `<details class="ev-ops"><summary>운영·안전장치 ${O.length}개</summary><ul class="ev-rls">${O.map(row).join("")}</ul></details>` : ""}` : ""; }).join("");
    const B = R.bought;
    return `<section class="card ev-core"><span>가장 기본이 되는 원칙</span><b>${esc(R.core.t)}</b><p>${esc(R.core.d)}</p></section>
      <section class="card"><div class="ev-ks three">
          <div class="ev-k"><span>매수 규칙</span><b>${R.n.buy}</b></div><div class="ev-k"><span>매도 규칙</span><b>${R.n.sell}</b></div>
          <div class="ev-k ${R.n.check ? "warnk" : ""}"><span>검토할 규칙</span><b>${R.n.check}</b></div></div>
        <div class="tiny" style="margin-top:8px">판정은 최근 ${R.days}일 기록(지금 ${R.seen_days}거래일치)으로 해요. 걸러낸 종목을 AI 목표·손절대로 샀다면 어땠을지(추천 성적표)를 봐요.${B.graded ? ` 같은 기간 실제로 산 종목은 평균 ${pct(B.avg, 2)} · 이익 ${B.win}%.` : ""}</div></section>
      <section class="card ev-rb"><h2 class="ev-h2">매수 규칙</h2>${block(R.buy, ["점검", "거르기", "크기"])}</section>
      <section class="card ev-rb"><h2 class="ev-h2">매도 규칙</h2>${block(R.sell, ["팔기"])}</section>
      <section class="card"><h2 class="ev-h2">규칙을 관리하는 방법</h2><ol class="ev-pr plain">${R.upkeep.map((t) => `<li><span>${esc(t)}</span></li>`).join("")}</ol></section>
      <section class="card"><h2 class="ev-h2">매수·매도 로직 변경 이력</h2>
        <ol class="ev-lg2">${R.log.map((x) => `<li class="lk-${x.kind}"><div class="ev-lgh"><time>${esc(x.day.slice(5).replace("-", "/"))}</time><span class="tag ${K[x.kind][1]}">${K[x.kind][0]}</span><span class="ev-sd">${SD[x.side]}</span><span class="ev-rv">${esc(x.ver)}</span></div>
          <b>${esc(x.t)}</b><p>${esc(x.what)}</p><small>왜: ${esc(x.why)}</small></li>`).join("")}</ol></section>`;
  },
  evoRoad(D) {
    const E = this.ev;
    const F = [["all", "전체"], ["dev", "앱 개선"], ["plan", "플랜 조정"], ["engine", "엔진·비용"], ["learn", "학습"], ["lab", "실험·시작"]];
    const pass = (x) => E.f === "all" || (E.f === "dev" ? x.kind === "dev" : E.f === "plan" ? ["plan", "proposal"].includes(x.kind) : E.f === "engine" ? ["engine", "cost"].includes(x.kind)
      : E.f === "learn" ? ["review", "lesson"].includes(x.kind) : ["lab", "run"].includes(x.kind));
    const rows = D.timeline.filter(pass), by = [];
    rows.forEach((x) => { const g = by[by.length - 1]; if (g && g.day === x.day) g.items.push(x); else by.push({ day: x.day, items: [x] }); });
    const W = "일월화수목금토";
    return `<div class="ev-fs">${F.map(([k2, t]) => `<button type="button" class="chip" data-evf="${k2}" aria-pressed="${E.f === k2}">${t}</button>`).join("")}</div>
      ${by.length ? `<ol class="ev-tl">${by.map((g) => `<li class="ev-day"><div class="ev-dd"><b>${esc(g.day.slice(5).replace("-", "/"))}</b><small>${W[new Date(g.day + "T00:00").getDay()]}</small></div>
        <ul>${g.items.map((x) => `<li class="ev-it ${x.area} ${x.kind}${x.pending ? " pend" : ""}"><i aria-hidden="true"></i>
          <div><span class="ev-tag">${esc(x.area_label)}</span><b>${esc(x.title)}</b>${x.what ? `<p>${esc(x.what)}</p>` : ""}${x.why ? `<small>${x.kind === "dev" ? "왜: " : ""}${esc(x.why)}</small>` : ""}</div></li>`).join("")}</ul></li>`).join("")}</ol>`
        : `<div class="empty">이 분류의 기록이 아직 없어요</div>`}`;
  },
  evoLogic(D) {
    const L = D.logic;
    const hz = L.horizons.map((h) => `<div class="ev-hz"><div class="ev-hzh"><b>${esc(h.label)} 추천</b><span>${h.overall ? `채점 ${h.n}건 · 평균 ${pct(h.overall.avg, 2)} · 이익 ${h.overall.win_rate}%` : `채점 ${h.n}건`}</span></div>
        ${h.tune ? `<div class="ev-tn">실제 결과로 맞춘 폭: 익절 ${h.tune.tp}% · 손절 ${h.tune.sl}%${h.tune.avoid && h.tune.avoid.length ? ` · 피할 조건 ${esc([].concat(h.tune.avoid).slice(0, 2).join(", "))}` : ""}</div>` : ""}
        ${(h.insights || []).map((t) => `<div class="ev-in">${esc(t)}</div>`).join("")}
        ${h.lesson ? `<details class="ev-ls"><summary>추천 규칙 ${h.lesson.ver}판 (${esc(h.lesson.at)})</summary><div>${esc(h.lesson.text)}</div></details>` : ""}
        <div class="ev-nx">${h.n < h.min ? `첫 규칙까지 ${h.next_lesson}건 더` : `다음 규칙 고치기까지 ${h.next_lesson}건`}</div></div>`).join("");
    const runs = L.runs.map((r) => `<div class="ev-run"><div class="ev-rh"><b>${esc(r.name)}</b><span class="tag ${r.mode === "real" ? "warn" : ""}">${r.mode === "real" ? "실전" : "모의"}</span><span class="tiny">플랜 ${r.ver}판 · ${esc(r.model)}</span></div>
        <dl class="ev-kv">${r.keys.map(([a, b]) => `<div><dt>${esc(a)}</dt><dd>${esc(b)}</dd></div>`).join("")}</dl>
        <a class="ev-more" href="#invest/auto" data-atgo="${r.id}" data-attab="flow">이 플랜의 흐름도 보기 ›</a></div>`).join("");
    const labs = L.labs.map((l) => `<div class="ev-lab"><b>${esc(l.label)} 실험</b><ol>${l.plans.map((p) => `<li><span>${esc(p.label)}<small>${esc(p.desc)}</small></span><em class="${tone(p.net)}">${p.n ? pct(p.net, 2) : "-"}</em></li>`).join("")}</ol></div>`).join("");
    return `${L.sections.map((S) => `<section class="card ev-sec ${S.key}"><h2 class="ev-h2">${esc(S.t)}</h2>
        <dl class="ev-rows">${S.items.map(([a, b]) => `<div><dt>${esc(a)}</dt><dd>${esc(b)}</dd></div>`).join("")}</dl>
        ${S.key === "ai" ? `<h3 class="ev-h3">지금까지 배운 것</h3>${hz}` : ""}
        ${S.key === "auto" && runs ? `<h3 class="ev-h3">직접 만든 자동매매</h3>${runs}` : ""}
        ${S.key === "learn" && labs ? `<h3 class="ev-h3">실험 플랜 순위(비용 뺀 수익률)</h3>${labs}` : ""}</section>`).join("")}`;
  },
  evoNext(D) {
    const P = D.plan, ST = { done: ["완료", "g"], doing: ["진행 중", "y"], wait: ["승인 대기", "warn"], idea: ["제안", "v"] };
    const bar = (p) => (p ? `<span class="ev-pg" role="img" aria-label="${p[0]}/${p[1]}"><i style="width:${Math.min(100, (p[0] / (p[1] || 1)) * 100).toFixed(0)}%"></i></span><em>${p[0]}/${p[1]}</em>` : "");
    const area = { lab: "실험", learn: "학습", ai: "엔진" };
    return `${P.goal ? `<section class="card ev-goal"><span>최종 목표</span><b>${esc(P.goal.name)}</b><small>${won(P.goal.amount)}원 · ${esc(P.goal.deadline)}까지</small></section>` : ""}
      <section class="card"><h2 class="ev-h2">지금 진행 중인 단계</h2>
        <ul class="ev-steps">${P.steps.map((x) => `<li class="${x.st}"><div class="ev-sh"><span class="tag ${ST[x.st][1]}">${ST[x.st][0]}</span><span class="ev-ar">${area[x.area] || ""}</span><b>${esc(x.t)}</b></div>
          <p>${esc(x.d)}</p>${x.prog ? `<div class="ev-pr2">${x.prog2 ? `<span class="ev-pgl">한 플랜의 끝난 거래</span>` : ""}${bar(x.prog)}${x.prog2 ? `<span class="ev-pgl">거래일</span>${bar(x.prog2)}` : ""}</div>` : ""}
          ${x.top && x.top.length ? `<div class="ev-top">${x.top.map((t, i) => `<span>${i + 1}. ${esc(t.label)} <em class="${tone(t.net)}">${pct(t.net, 2)}</em> <small>${t.n}건</small></span>`).join("")}</div>` : ""}
          ${x.run ? `<a class="ev-more" href="#invest/auto" data-atgo="${x.run}">개선안 보러 가기 ›</a>` : ""}</li>`).join("")}</ul></section>
      <section class="card"><h2 class="ev-h2">다음에 해 볼 만한 것</h2>
        <ul class="ev-steps">${P.ideas.map((x) => `<li class="idea"><div class="ev-sh"><span class="tag v">제안</span><b>${esc(x.t)}</b></div><p>${esc(x.d)}</p></li>`).join("")}</ul>
        <div class="tiny">제안은 아직 반영하지 않았어요. 해 보고 싶은 것을 말해 주면 넣어 드릴게요.</div></section>`;
  },
  // ===== 흐름도: 이 플랜이 한 바퀴 도는 전 과정과 지금 위치 =====
  async atFlow(el, rid) {
    const A = this.at;
    if (A.flow && A.flow.run.id === rid) el.innerHTML = this.atFlowHtml(A.flow);
    else el.innerHTML = `<div class="empty loading">흐름도를 그리는 중</div>`;
    let F;
    try { F = await GET(`/api/auto/runs/${rid}/flow`); } catch (e) { if (!A.flow) el.innerHTML = `<div class="notice warn">${esc(e.message)}</div>`; return; }
    if (A.tab !== "flow" || !document.body.contains(el)) return;
    A.flow = F;
    const open = [...el.querySelectorAll("details[open]")].map((x) => x.dataset.k);
    el.innerHTML = this.atFlowHtml(F);
    open.forEach((k) => { const x = el.querySelector(`details[data-k="${k}"]`); if (x) x.open = true; });
  },
  atFlowHtml(F) {
    const r = F.run, mkt = r.mkt, M = (v) => this.atM(mkt, v), ai = F.ai, fu = F.funnel, sz = F.size, L = F.learn;
    const stop = F.gates.find((g) => g.st === "stop");
    const stage = F.where === "ai" ? 2 : stop ? 1 : 2;
    const here = (n) => (stage === n ? `<em class="fl-here">지금 여기</em>` : "");
    const ring = (v, max, lbl) => { const k = Math.max(0, Math.min(1, max ? v / max : 0)), C = 2 * Math.PI * 17;
      return `<span class="fl-ring" role="img" aria-label="${lbl} ${v}/${max}"><svg viewBox="0 0 42 42"><circle cx="21" cy="21" r="17" class="t"/><circle cx="21" cy="21" r="17" class="v" stroke-dasharray="${(C * k).toFixed(1)} ${C.toFixed(1)}" transform="rotate(-90 21 21)"/></svg><b>${v}</b><small>/${max}</small></span>`; };
    const ICO = { pass: "✓", stop: "■", wait: "·" };
    // ① 점검
    const s1 = `<div class="fl-gates">${F.gates.map((g) => `<div class="fl-g ${g.st}"><i aria-hidden="true">${ICO[g.st]}</i><span><b>${esc(g.t)}</b><small>${esc(g.d)}</small>${g.why ? `<em>${esc(g.why)}</em>` : ""}</span></div>`).join("")}</div>`;
    // ② AI 추천
    const N = F.now;
    const s2 = `${N ? (N.busy ? this.atScanHtml(N) : `<div class="fl-next"><b>${esc(N.next.label || "")}</b><span>${this.atNextLine(N)}</span></div>`) : ""}
      <div class="fl-two">
        <div class="fl-box ${ai.next_kind === "full" ? "on" : ""}"><span class="fl-k">정밀 추천</span><b>${esc(ai.model_label)}${ai.web ? " + 뉴스 검색" : ""}</b><small>오늘 ${ai.full}/${ai.full_max}번</small></div>
        <div class="fl-box ${ai.next_kind === "quick" ? "on" : ""} ${ai.quick_gap ? "" : "off"}"><span class="fl-k">빠른 재추천</span><b>${ai.quick_gap ? `${ai.quick_gap}분마다 · ${ai.quick_model}` : "꺼짐"}</b><small>${ai.quick_gap ? `오늘 ${ai.quick}번 · 뉴스 없이 숫자만` : "플랜에서 켤 수 있어요"}</small></div></div>
      <div class="fl-ladder" aria-label="추천 엔진 단계">${ai.engine.ladder.map((x, i) => `${i ? `<span class="fl-arr" aria-hidden="true">›</span>` : ""}<span class="${x.on ? "on" : ""}">${esc(x.label.replace(/ [\d.]+$/, ""))}</span>`).join("")}</div>
      <div class="fl-meter"><span>오늘 AI 비용${ai.shared ? "<br><small>실험 묶음 합계</small>" : ""}</span><span class="fl-bar"><i style="width:${Math.min(100, ai.usd / (ai.cap || 1) * 100).toFixed(1)}%"></i></span><b>$${ai.usd.toFixed(2)} / $${ai.cap}</b></div>
      <div class="fl-facts">${ai.shared ? `<span>같은 추천을 플랜들이 함께 써서 이 플랜 몫은 $${ai.mine.toFixed(2)}예요</span>` : ""}<span>정밀 추천이 남은 한도를 넘길 것 같으면 그 차례는 빠른 재추천으로 받아요</span><span>이미 다룬 ${ai.exclude}종목은 빼고 물어요</span>${ai.price_cap ? `<span>${M(ai.price_cap)} 넘는 종목은 후보에서 빼요(1주를 못 사서)</span>` : ""}</div>`;
    // ③ 거르기: 깔때기
    const s3 = fu.picks ? `<div class="fl-fun">
        <div class="fl-f fl-first"><span><b>받은 종목</b><small>오늘 추천 ${ai.full + ai.quick}번</small></span><span class="fl-fb"><i style="width:100%"></i></span><b>${fu.picks}</b></div>
        ${fu.steps.filter((x) => x.in > 0 && (x.out || !["watch", "etc", "market"].includes(x.code))).map((x) => `<div class="fl-f ${x.out ? "cut" : ""}"><span><b>${esc(x.t)}</b><small>${esc(x.rule)}</small></span>
          <span class="fl-fb"><i style="width:${((x.in - x.out) / fu.picks * 100).toFixed(1)}%"></i>${x.out ? `<u style="left:${((x.in - x.out) / fu.picks * 100).toFixed(1)}%;width:${(x.out / fu.picks * 100).toFixed(1)}%"></u>` : ""}</span>
          <b>${x.out ? `−${x.out}` : "✓"}</b></div>`).join("")}
        <div class="fl-f end"><span><b>산 종목</b><small>${fu.buy ? "아래 크기대로 주문" : "오늘은 아직 없어요"}</small></span><span class="fl-fb"><i style="width:${(fu.buy / fu.picks * 100).toFixed(1)}%"></i></span><b>${fu.buy}</b></div></div>
      <details class="fl-det" data-k="recent"><summary>최근 판단 ${fu.recent.length}건 보기</summary><div class="fl-rec">${fu.recent.map((x) => `<div class="${x.act === "buy" ? "buy" : ""}"><time>${esc(x.ts)}</time>
        <b>${esc(x.name || x.sym)}</b>${x.lite ? `<span class="tag y">빠른</span>` : ""}<span>${x.act === "buy" ? "샀어요" : esc(x.why || "")}</span></div>`).join("")}</div></details>`
      : `<div class="fl-empty">오늘은 아직 받은 종목이 없어요. 추천을 받으면 여기서 기준을 하나씩 통과해요.</div>
        <div class="fl-rules">${fu.steps.filter((x) => ["winprob", "score", "rr", "fee", "chase"].includes(x.code)).map((x) => `<span>${esc(x.rule)}</span>`).join("")}</div>`;
    // ④ 크기
    const s4 = `<div class="fl-size"><div class="fl-sb"><i style="width:${Math.min(100, sz.pct).toFixed(1)}%"></i><span>굴리는 돈 ${M(sz.base)}</span></div>
        <div class="fl-sz"><b>한 번에 ${sz.pct}% ≈ ${M(sz.alloc)}</b>
          ${sz.warm ? `<span class="tag">검증 단계 ×${sz.warm_mult} · ${sz.warm_left}건 남음</span>` : ""}
          ${sz.skip_watch ? `<span class="tag">관망이면 안 삼</span>` : `<span class="tag">관망이면 ×${sz.watch_mult}</span>`}
          ${sz.min_one ? `<span class="tag">모의: 모자라도 1주</span>` : ""}
          ${sz.scale_in ? `<span class="tag y">분할 매수: 확신만큼 먼저, 오르면 나머지</span>` : ""}${sz.vol_stop ? `<span class="tag y">흔들림 맞춤 손절</span>` : ""}${sz.hold_watch ? `<span class="tag y">재료 감시: 무효면 정리</span>` : ""}${sz.var_guard ? `<span class="tag y">위험 미리 막기: 나쁜 날 손실 ≤ 하루 한도</span>` : ""}${sz.regime_size ? `<span class="tag y">국면별 비중 0.5~1.2배</span>` : ""}${sz.counter_check ? `<span class="tag y">반대 의견 확인</span>` : ""}
          ${sz.risk_pct ? `<span class="tag y">손절하면 굴리는 돈의 ${sz.risk_pct}%만 잃게</span>` : ""}${sz.edge_size ? `<span class="tag y">확신만큼 0.5~1.5배</span>` : ""}
          ${sz.group_max ? `<span class="tag y">같은 업종·테마 ${sz.group_max}종목까지</span>` : ""}${sz.corr_max ? `<span class="tag y">같이 움직이는 종목 제외</span>` : ""}</div>
        <div class="fl-facts"><span>익절·손절 폭 = AI가 정한 폭 × ${sz.exit_mult}(최대 +${sz.tp_cap}% / −${sz.sl_cap}%)</span><span>남은 현금 ${M(sz.cash)}</span></div></div>`;
    // ⑤ 주문
    const s5 = `<div class="fl-two"><div class="fl-box on"><span class="fl-k">${F.order.mode === "real" ? "실전" : "모의"}</span><b>${esc(F.order.slip)}</b><small>오늘 산 것 ${F.order.today}건</small></div></div>`;
    // ⑥ 들고 있기
    const s6 = F.hold.length ? `<div class="fl-hold">${F.hold.map((h) => { const lo = Math.min(-h.sl, 0), hi = h.tp, X = (v) => Math.max(0, Math.min(100, (v - lo) / (hi - lo) * 100));
      return `<div class="fl-h"><div class="fl-hh"><b>${stockA(mkt, h.sym, h.name)}</b><span>${h.qty}주${h.fill_ok ? "" : " · 체결 확인 중"}${h.add ? ` · 오르면 ${h.add}주 더` : ""}${h.part === 2 || h.part === 4 ? " · 나눠 팔고 남긴 몫" : h.part === 3 ? " · 더 산 몫" : ""}</span><b class="${tone(h.pct)}">${pct(h.pct, 2)}</b></div>
        <div class="fl-gauge" role="img" aria-label="손절선 ${-h.sl}%, 익절 +${h.tp}%, 지금 ${pct(h.pct, 2)}"><span class="z" style="left:${X(0).toFixed(1)}%"></span>
          ${h.high > 0 ? `<span class="hw" style="left:${X(h.high).toFixed(1)}%" title="보유 중 최고"></span>` : ""}<span class="me ${h.pct >= 0 ? "pos" : "neg"}" style="left:${X(h.pct).toFixed(1)}%"></span></div>
        <div class="fl-gl"><span class="down">${h.sl > 0 ? `손절 −${h.sl}%` : h.sl < 0 ? `+${-h.sl}% 아래면 정리` : "본전 아래면 정리"}</span>${h.trail ? `<span>최고점에서 −${h.trail}% 빠지면 추적 익절</span>` : ""}${h.exit ? `<span>${esc(h.exit)} 정리</span>` : ""}<span class="up">익절 +${h.tp}%</span></div></div>`; }).join("")}</div>`
      : `<div class="fl-empty">들고 있는 종목이 없어요. 사면 여기서 익절·손절선 사이 어디쯤인지 보여요.</div>`;
    // ⑦ 팔기
    const ways = [["익절", "목표에 닿음", "up"], ["손절", "손절선에 닿음", "down"], ["추적 익절", "최고점에서 밀림", "up"], [r.style === "day" ? "시간 정리" : "기간 정리", r.style === "day" ? "장 마감 전·정한 시각" : "보유 기간 끝", ""], ["종료 정리", "자동매매를 끝낼 때", ""]];
    if (sz.scale_out) ways.splice(0, 0, ["분할 익절", "힘에 맞춘 비율로 먼저", "up"], ["이익 지키기", "남긴 몫이 지킴선으로", "up"], ["본전 정리", "남긴 몫이 본전으로", ""]);
    const exn = Object.fromEntries(F.exits.map((e) => [e.reason, e]));
    const s7 = `<div class="fl-ways">${ways.map(([k, d, c]) => { const e = exn[k]; return `<div class="fl-w ${e ? "hit" : ""} ${c}"><b>${k}</b><small>${d}</small>${e ? `<em class="${tone(e.pnl)}">${e.n}건 · ${this.atM(mkt, e.pnl, true)}</em>` : `<em>오늘 0건</em>`}</div>`; }).join("")}</div>`;
    // ⑧ 배우기
    const s8 = `<div class="fl-learn">
        <div>${ring(Math.min(L.review.n, L.review.every), L.review.every, "분석까지 거래")}<span><b>성적 분석${L.review.ai ? ` <span class="tag">AI 복기 ${esc(L.review.model)}</span>` : ""}</b><small>${L.review.every}건 또는 ${L.review.every_days}거래일마다(지금 ${L.review.days}일째) · ${L.review.auto ? "모의라 스스로 반영" : "개선안은 승인 후 반영"}</small></span></div>
        <div>${ring(Math.min(L.engine.n, L.engine.need), L.engine.need, "엔진 판단까지 거래")}<span><b>엔진 점검</b><small>${L.engine.n ? `지금 엔진 평균 ${pct(L.engine.avg, 2)} · 이익 ${L.engine.win}% · ` : ""}${esc(L.engine.rule)}${L.engine.lock ? " · 더 올리지 않기로 함" : ""}</small></span></div>
        ${L.pending ? `<div class="fl-pend">개선안 ${L.pending}건이 승인을 기다려요 → 현황 탭</div>` : ""}</div>
      <div class="fl-loop" aria-hidden="true">↺ 다시 ① 점검으로</div>`;
    const st = (n, t, sub, body, cls = "") => `<li class="fl-st ${cls} ${stage === n ? "now" : ""}"><span class="fl-n" aria-hidden="true">${n}</span>
        <div class="fl-c"><div class="fl-t"><b>${t}</b>${here(n)}<small>${sub}</small></div>${body}</div></li>`;
    return `<div class="fl-top ${stop ? "stop" : F.where === "ai" ? "ai" : "go"}"><span class="fl-dot"></span><div><b>${stop ? `① 점검에서 멈춤 · ${esc(stop.t)}` : F.where === "ai" ? "② AI 추천을 받는 중" : "② 다음 AI 추천을 기다리는 중"}</b>
        <small>${esc(stop ? stop.why : (N && N.next.label) || "")}</small></div></div>
      <ol class="fl">
        ${st(1, "점검", "무료 · 엔진이 몇 초마다 위에서부터 차례로 봐요", s1)}
        ${st(2, "AI 추천", "돈이 드는 단계 · 정밀과 빠른 재추천", s2)}
        ${st(3, "거르기", "받은 종목이 기준을 하나씩 통과", s3)}
        ${st(4, "크기 정하기", "한 번에 얼마나 살지", s4)}
        ${st(5, "주문", r.mode === "real" ? "키움으로 실제 주문" : "앱 안의 가상 체결", s5)}
        ${st(6, "들고 있기", F.hold.length ? `${F.hold.length}종목을 지키는 중` : "시세를 보며 팔 때를 기다림", s6, F.hold.length ? "busy" : "")}
        ${st(7, "팔기", "다섯 가지 길 중 먼저 닿는 쪽", s7)}
        ${st(8, "배우기", "쌓인 기록으로 스스로 고침", s8)}
      </ol>`;
  },
  atTab() {
    const A = this.at, d = A.d, el = $("#atBody");
    if (!el || !d) return;
    const r = d.run, mkt = r.mkt, M = (v, s) => this.atM(mkt, v, s), S = d.stats.all || { n: 0 };
    el.onclick = null;
    if (A.tab === "flow") return this.atFlow(el, r.id);
    if (A.tab === "now") {
      const pend = d.proposals.filter((p) => p.status === "pending");
      const KIND = { buy: ["매수", "y"], sell: ["매도", ""], cycle: ["추천", ""], skip: ["쉼", ""], risk: ["한도", "warn"], review: ["분석", ""], control: ["조작", ""], start: ["시작", "y"], end: ["종료", ""] };
      el.innerHTML = `${d.now ? this.atNowHtml(d) : ""}${pend.map((p) => this.atPropHtml(p, mkt)).join("")}
        <section class="card tight"><div class="card-head" style="padding-top:8px"><h2>보유 ${d.open.length}</h2><span class="tiny">평가 ${M(r.money.open_value)} · ${M(r.money.unrealized, true)}</span></div>
          <div class="list">${d.open.length ? d.open.map((t) => `<div class="item" ${this.atTradeDt(t, mkt)} role="button" tabindex="0">
            <span class="main"><b>${stockA(mkt, t.sym, t.name)}</b><span class="sub">${won(t.qty)}주 · ${M(t.entry_price)} → ${M(t.last)}${t.fill_ok ? "" : " · 체결 확인 중"}</span>
              <span class="at-bar" aria-hidden="true"><i style="left:${Math.max(0, Math.min(100, (t.pnl_pct + t.stop_pct) / (t.target_pct + t.stop_pct) * 100)).toFixed(1)}%"></i><u style="left:${(t.stop_pct / (t.target_pct + t.stop_pct) * 100).toFixed(1)}%"></u></span></span>
            <span class="end"><b class="${tone(t.pnl)}">${pct(t.pnl_pct, 2)}</b><span class="small ${tone(t.pnl)}">${M(t.pnl, true)}</span></span></div>`).join("")
            : `<div class="empty">들고 있는 종목이 없어요</div>`}</div></section>
        <div class="sec-title">판단 기록</div>
        <section class="card tight"><div class="list">${d.log.length ? d.log.slice(0, 25).map((x) => { const k = KIND[x.kind] || [x.kind, ""], dec = (x.data || {}).decisions; return `<div class="item at-log" ${dec ? dt({ title: "이번 추천의 판단", rows: dec.map((q) => [q.name || q.sym, q.act === "buy" ? `매수 ${q.qty}주` : `건너뜀: ${q.why}`, q.act === "buy" ? "up" : ""]), note: (x.data.view ? `시장: ${x.data.view}\n` : "") + (x.data.stance ? `AI 분위기: ${x.data.stance}` : "") }) + ' role="button" tabindex="0"' : ""}>
            <span class="ico ${k[1]}">${k[0]}</span><span class="main"><span class="at-msg">${esc(x.msg)}</span><span class="sub">${esc(x.ts.replace("T", " ").slice(5, 16))}${dec ? " · 눌러서 종목별 판단 보기" : ""}</span></span></div>`; }).join("") : `<div class="empty">아직 기록이 없어요</div>`}</div></section>`;
    } else if (A.tab === "log") {
      el.innerHTML = `<section class="card tight"><div class="card-head" style="padding-top:8px"><h2>끝난 거래 ${S.n}</h2><button type="button" class="link" id="atCsv">CSV 내려받기</button></div>
          <div class="list">${d.closed.length ? d.closed.map((t) => `<div class="item" ${this.atTradeDt(t, mkt)} role="button" tabindex="0">
            <span class="main"><b>${stockA(mkt, t.sym, t.name)}</b><span class="sub">${esc((t.exit_ts || "").replace("T", " ").slice(5, 16))} · ${esc(t.reason || "")} · ${won(t.qty)}주</span></span>
            <span class="end"><b class="${tone(t.pnl)}">${pct(t.pnl_pct, 2)}</b><span class="small ${tone(t.pnl)}">${M(t.pnl, true)}</span></span></div>`).join("")
            : `<div class="empty">아직 끝난 거래가 없어요</div>`}</div></section>
        <div class="tiny" style="padding:0 3px">살 때의 근거·지표, 보유 중 최고·최저, 판 사유까지 모두 저장해요. CSV에는 전체 기록이 들어 있어요.</div>`;
      $("#atCsv").onclick = () => run(async () => bkSave(await apiBlob(`/api/auto/runs/${r.id}/export`), `자동매매_${r.id}.csv`));
    } else if (A.tab === "stat") {
      const hist = d.proposals.filter((p) => p.status !== "pending");
      const canRev = S.n >= 10 || (d.opp && d.opp.cycles >= 3);
      el.innerHTML = !S.n ? `${d.opp && d.opp.cycles ? `<div class="tiny" style="padding:0 3px">끝난 거래가 아직 없어 성적표는 비어 있어요. 대신 추천을 얼마나 샀는지부터 봐요.</div>`
          : `<section class="card"><div class="empty">거래가 끝나면 성적이 쌓여요.<br>${d.params.review_every}건 또는 ${d.params.review_days || 5}거래일마다 스스로 분석해요.</div></section>`}
        ${this.atOppHtml(d)}
        ${d.opp && d.opp.cycles ? `<button type="button" class="btn wide" id="atRev" ${canRev ? "" : "disabled"}>지금 분석하기</button>
        <div class="tiny" style="padding:0 3px">거래가 없어도 추천이 3번 쌓이면 '왜 못 샀는지'부터 분석해요(무료).</div>` : ""}
        ${hist.length ? `<div class="sec-title">지난 분석</div>${hist.map((p) => this.atPropHtml(p, mkt)).join("")}` : ""}` : `
        <section class="card">
          <div class="sc-kpi"><div><span>승률</span><b>${S.win_rate}%</b></div><div><span>평균 수익률</span><b class="${tone(S.avg_pct)}">${pct(S.avg_pct, 2)}</b></div><div><span>번 돈 ÷ 잃은 돈</span><b>${S.pf == null ? "-" : S.pf >= 99 ? "손실 없음" : S.pf}</b></div>
            <div><span>거래</span><b>${S.n}건</b></div><div><span>최대 낙폭</span><b>${S.mdd_pct ?? "-"}%</b></div><div><span>확정 손익</span><b class="${tone(S.pnl)}">${M(S.pnl, true)}</b></div></div>
          ${lineChart([{ values: [r.budget].concat(d.stats.curve || []), color: "#8A7CF0" }], { label: "거래별 굴리는 돈 추이", h: 110 })}
          <div class="tiny">이길 때 평균 ${pct(S.avg_win, 2)}, 질 때 평균 ${pct(S.avg_loss, 2)} · 보유 중 최고 오름폭 중간값 ${pct(S.mfe_med, 2)}, 최저 ${pct(S.mae_med, 2)} · 비용 합계 ${M(S.fees)}</div>
        </section>
        ${this.atRiskHtml(d)}
        ${this.atAttrHtml(d.stats)}
        ${this.atSlipHtml(d.stats)}
        ${this.atPrepHtml(d)}
        ${(d.stats.groups || []).filter((g) => g.rows.length > 1 || g.label === "파는 사유").map((g) => `<section class="card tight"><div class="card-head" style="padding-top:8px"><h2>${esc(g.label)}별</h2></div>
          <div class="at-tbl"><div class="at-tr at-th"><span></span><span>거래</span><span>승률</span><span>평균</span></div>${g.rows.sort((a, b) => b.n - a.n).map((x) => `<div class="at-tr"><span>${esc(x.key)}</span><span>${x.n}</span><span>${x.win_rate}%</span><span class="${tone(x.avg_pct)}">${pct(x.avg_pct, 2)}</span></div>`).join("")}</div></section>`).join("")}
        ${this.atOppHtml(d)}
        <div class="grid2"><button type="button" class="btn" id="atRev" ${canRev ? "" : "disabled"}>지금 분석하기</button><button type="button" class="btn ai-btn" id="atAi" ${S.n < 5 ? "disabled" : ""}><span class="ai-spark" aria-hidden="true">✦</span>AI 복기</button></div>
        <div class="tiny" style="padding:0 3px">'지금 분석하기'는 조건별 성적과 건너뛴 종목의 결과로 고칠 값을 찾아요(무료). 'AI 복기'는 Claude가 기록 전체를 읽고 개선안을 내요(유료). 어느 쪽이든 승인해야 반영돼요.</div>
        ${hist.length ? `<div class="sec-title">지난 분석</div>${hist.map((p) => this.atPropHtml(p, mkt)).join("")}` : ""}`;
      if ($("#atRev")) $("#atRev").onclick = async () => { A.d = await run(() => api("POST", `/api/auto/runs/${r.id}/review`), "분석했어요. 현황에서 개선안을 확인해요"); A.tab = "now"; this.auto($("#iv")); };
      if ($("#atAi")) $("#atAi").onclick = () => { aiSheet("auto", { run_id: r.id }); sheet.addEventListener("close", () => { if (investState.tab === "auto") this.auto($("#iv")); }, { once: true }); };
    } else {
      const p = d.params, SRC = { start: "시작", rule: "자동 분석", ai: "AI 복기", user: "직접 고침" };
      const val = (k) => (typeof p[k] === "boolean" ? (p[k] ? "예" : "아니오") : p[k]);
      el.innerHTML = `<section class="card"><div class="card-head"><h2>지금 플랜 · ${r.ver}판</h2><span class="tiny">${esc((A.o?.models || []).find((x) => x.id === r.model)?.label.split(" · ")[0] || r.model)}</span></div>
          <ol class="at-plan">${d.plan.map((s) => `<li><b>${esc(s.t)}</b><span>${esc(s.d)}</span></li>`).join("")}</ol></section>
        <section class="card tight"><div class="card-head" style="padding-top:8px"><h2>세부 값</h2>${r.status === "ended" ? "" : `<button type="button" class="link" id="atEdit">직접 고치기</button>`}</div>
          <dl class="dt-rows">${(Array.isArray(d.keys) ? d.keys : Object.entries(d.keys)).map(([k, l]) => `<div><dt>${esc(l)}</dt><dd>${esc(String(val(k)))}</dd></div>`).join("")}</dl></section>
        ${r.status === "ended" ? "" : `<section class="card">
          <div class="card-head"><div><h2>개선안 스스로 반영</h2><div class="tiny">${r.mode === "real" ? "실전은 개선안을 직접 승인해야 반영돼요." : "모의에서만 켤 수 있어요. 켜면 스스로 낸 개선안을 바로 반영하고 알림을 보내요. 바뀐 내용은 아래 이력에 남고, 언제든 직접 고칠 수 있어요."}</div></div>
            <button type="button" class="switch" role="switch" id="atAuto" aria-checked="${!!p.auto_apply && r.mode !== "real"}" aria-label="개선안 스스로 반영" ${r.mode === "real" ? "disabled" : ""}><span></span></button></div>
          <div class="tiny">스스로 분석하는 때: 끝난 거래 ${p.review_every}건마다, ${p.review_days || 5}거래일마다, 그리고 이틀째 한 건도 못 샀을 때.</div></section>`}
        <div class="sec-title">바뀐 이력</div>
        <section class="card tight"><div class="list">${d.changes.slice().reverse().map((c) => `<div class="item"><span class="ico">${c.ver}판</span><span class="main"><span class="at-msg">${c.changes.length ? c.changes.map((x) => `${esc(x.label || x.key)} ${esc(String(x.from))} → ${esc(String(x.to))}`).join(", ") : "처음 플랜"}</span><span class="sub">${esc(c.ts.replace("T", " ").slice(5, 16))} · ${SRC[c.source] || c.source}</span></span></div>`).join("")}</div></section>`;
      if ($("#atEdit")) $("#atEdit").onclick = () => this.atEdit();
      if ($("#atAuto")) $("#atAuto").onclick = async () => {
        try { A.d = await run(() => api("PUT", `/api/auto/runs/${r.id}/params`, { auto_apply: !p.auto_apply }), p.auto_apply ? "이제 승인해야 반영해요" : "개선안을 스스로 반영해요(모의)"); } catch (e) { return; }
        this.atTab();
      };
    }
    el.onclick = async (e) => {
      const pb = e.target.closest("[data-prep]");
      if (pb) {
        if (pb.dataset.prep === "my") return this.mySheet();
        if (pb.dataset.prep === "scan") await run(() => api("POST", "/api/prep/scan"), "훑기 시작했어요. 1~3분 걸려요");
        else await run(() => api("PUT", "/api/prep", { brief: pb.dataset.on !== "1" }), pb.dataset.on === "1" ? "장 시작 전 리서치를 껐어요" : "장 시작 전 리서치를 켰어요");
        A.d = await api("GET", `/api/auto/runs/${A.d.run.id}`);
        return this.atTab();
      }
      const b = e.target.closest("[data-dec]");
      if (!b) return;
      const pid = b.closest("[data-prop]").dataset.prop, a = b.dataset.dec;
      A.d = await run(() => api("POST", `/api/auto/proposals/${pid}`, { action: a }), a === "apply" ? "개선안을 반영했어요" : "반영하지 않았어요");
      this.auto($("#iv"));
    };
  },
  // 돌고 있는 중에 쓸 돈을 늘리거나 줄인다. 넣는 금액에 따라 바뀌는 값을 미리 보여 준다
  atBudget() {
    const A = this.at, d = A.d, r = d.run, kr = r.mkt === "kr", M = (v, s) => this.atM(r.mkt, v, s), real = r.mode === "real";
    const steps = kr ? [[-500000, "-50만"], [-100000, "-10만"], [100000, "+10만"], [500000, "+50만"], [1000000, "+100만"]] : [[-500, "-$500"], [-100, "-$100"], [100, "+$100"], [500, "+$500"], [1000, "+$1,000"]];
    openSheet("쓸 돈 바꾸기", `<div class="at-bud">
        <div class="at-bud-now"><span>지금 쓸 돈</span><b>${M(r.money.budget)}</b></div>
        <label class="field">바꿀 금액(${kr ? "원" : "달러"})<input id="abV" class="r big" inputmode="decimal" autocomplete="off" value="${esc(kr ? won(r.money.budget) : String(r.money.budget))}"></label>
        <div class="chips">${steps.map(([v, l]) => `<button type="button" class="chip" data-add="${v}">${l}</button>`).join("")}</div>
        <div id="abPv" class="at-bud-pv" aria-live="polite"></div>
        <div class="tiny">지금까지의 손익은 그대로 두고 쓸 돈만 바꿔요. 수익률과 손실 한도는 새 금액을 기준으로 다시 계산돼요. 바꾼 기록은 판단 기록에 남아요.</div>
        <button type="button" class="btn dark wide" id="abGo" disabled>쓸 돈 바꾸기</button></div>`, (box) => {
      const inp = $("#abV", box), go = $("#abGo", box), pvBox = $("#abPv", box);
      const val = () => Number(String(inp.value).replace(/[^0-9.]/g, "")) || 0;
      let seq = 0, last = null;
      const row = (label, a, b, sign) => `<div><dt>${label}</dt><dd>${a === b ? M(a, sign) : `<s>${M(a, sign)}</s> <b>${M(b, sign)}</b>`}</dd></div>`;
      const draw = async () => {
        const my = ++seq;
        let P;
        try { P = await api("POST", `/api/auto/runs/${r.id}/budget`, { budget: val(), dry: true }); } catch (e) { P = { error: e.message }; }
        if (my !== seq) return;
        last = P;
        const same = P.error && /같은 금액/.test(P.error);
        pvBox.innerHTML = `${P.error && !same ? `<div class="notice warn">${esc(P.error)}</div>` : ""}
          ${P.cash ? `<dl class="dt-rows">${row("쓸 돈", P.old, P.new)}${row("남은 현금", P.cash[0], P.cash[1])}${row(`한 번에 사는 금액${P.warmup ? "(검증 단계)" : ""}`, P.per_trade[0], P.per_trade[1])}
            ${row("하루 손실 한도", -P.day_limit[0], -P.day_limit[1])}${row("전체 손실 한도", -P.total_limit[0], -P.total_limit[1])}
            <div><dt>수익률 표시</dt><dd>${P.ret_pct[0] === P.ret_pct[1] ? pct(P.ret_pct[0], 2) : `<s>${pct(P.ret_pct[0], 2)}</s> <b>${pct(P.ret_pct[1], 2)}</b>`}</dd></div></dl>` : ""}
          ${P.error ? "" : (P.notes || []).map((n) => `<div class="notice">${esc(n)}</div>`).join("")}
          ${P.fit && !P.error ? `<label class="at-bud-fit"><input type="checkbox" id="abFit" checked><span>종목 수도 새 금액대에 맞추기 <b>${P.fit.max_positions[0]} → ${P.fit.max_positions[1]}종목</b>${P.fit.per_trade_pct[0] !== P.fit.per_trade_pct[1] ? `, 1회 비중 <b>${P.fit.per_trade_pct[0]} → ${P.fit.per_trade_pct[1]}%</b>` : ""}</span></label>` : ""}
          ${real && P.diff > 0 && !P.error ? `<label class="field">실제 주문이 더 나갈 수 있어요. 확인하려면 '실전'이라고 적어 주세요<input id="abOk" autocomplete="off" placeholder="실전"></label>` : ""}`;
        go.disabled = !!P.error;
        go.textContent = P.error ? "쓸 돈 바꾸기" : `${M(Math.abs(P.diff))} ${P.diff > 0 ? "늘리기" : "줄이기"}`;
      };
      let t = null;
      const later = () => { go.disabled = true; clearTimeout(t); t = setTimeout(draw, 250); };
      inp.addEventListener("input", () => { if (kr) { const v = val(); inp.value = v ? won(v) : ""; } later(); });
      box.addEventListener("click", (e) => {
        const c = e.target.closest("[data-add]");
        if (!c) return;
        const v = Math.max(0, val() + Number(c.dataset.add));
        inp.value = kr ? won(v) : String(v);
        later();
      });
      go.onclick = async () => {
        if (!last || last.error) return;
        const body = { budget: val(), fit: !!($("#abFit", box) && $("#abFit", box).checked), confirm: $("#abOk", box) ? $("#abOk", box).value : "" };
        try { A.d = await run(() => api("POST", `/api/auto/runs/${r.id}/budget`, body), `쓸 돈을 ${M(body.budget)}으로 바꿨어요`); } catch (e) { return; }
        closeSheet(); this.auto($("#iv"));
      };
      draw();
    });
  },
  // ---------- 매매 기록: 지난 자동매매를 날짜별·전략별·여러 기준별로 ----------
  async atLog(box) {
    const H = this.hx || (this.hx = { days: 30, mkt: "", mode: "", strat: "", view: "day", openDay: "", openSt: "", dd: {} });
    if (!H.d) box.innerHTML = `<div class="empty loading">기록을 모으는 중</div>`;
    let d;
    try { d = await GET(`/api/auto/history?${new URLSearchParams({ days: H.days, mkt: H.mkt, mode: H.mode, strat: H.strat })}`); }
    catch (e) { if (e instanceof Locked || e instanceof NeedConnect) throw e; box.innerHTML = `<div class="notice warn">${esc(e.message)}</div>`; return; }
    if (investState.tab !== "atlog" || !$("#iv")) return;
    H.d = d; H.dd = {}; H.tn = 40; H.dayAll = "";
    const T = d.total, has = T.n || T.buys || d.days.length;
    const groups = {};
    (d.strat_list || []).forEach((x) => (groups[x.group] = groups[x.group] || []).push(x));
    const sel = (id, label, opts, v) => `<label class="hx-sel"><span>${label}</span><select id="${id}">${opts}</select></label>`;
    const o = (v, l, cur) => `<option value="${esc(v)}" ${v === cur ? "selected" : ""}>${esc(l)}</option>`;
    const dayVals = d.days.slice(0, 30).reverse();
    box.innerHTML = `<section class="card hx-head">
        <div class="hx-title"><b>매매 기록</b><span>끝난 거래를 날짜·전략·여러 기준으로 나눠 봐요. 돈은 원화예요${d.fx ? `(미국은 지금 환율 ${won(d.fx)}원으로 바꿈)` : ""}.</span></div>
        ${seg("기록기간", [["7", "7일"], ["30", "30일"], ["90", "90일"], ["0", "전체"]], String(H.days), "on-card")}
        <div class="hx-f">${sel("hxMkt", "시장", o("", "전체", H.mkt) + o("kr", "국내", H.mkt) + o("us", "미국", H.mkt), H.mkt)}
          ${sel("hxMode", "구분", o("", "실전·모의", H.mode) + o("real", "실전", H.mode) + o("paper", "모의", H.mode), H.mode)}
          ${sel("hxStrat", "전략", o("", "모든 전략", H.strat) + Object.entries(groups).map(([g, xs]) => `<optgroup label="${esc(g)}">${xs.map((x) => o(x.key, x.label, H.strat)).join("")}</optgroup>`).join(""), H.strat)}</div>
        ${has ? `<div class="hx-kpi">
          <div><span>끝난 거래</span><b>${won(T.n)}<small>건</small></b></div>
          <div><span>승률</span><b>${T.n ? `${T.win_rate}<small>%</small>` : "-"}</b></div>
          <div><span>한 번 평균</span><b class="${tone(T.avg_pct)}">${T.n ? pct(T.avg_pct, 2) : "-"}</b></div>
          <div><span>손익</span><b class="${tone(T.pnl_krw)}">${T.n ? signed(T.pnl_krw) : "-"}</b></div>
          <div><span>AI 비용</span><b>${won(T.ai_cost)}</b></div>
          <div><span>비용 뺀 손익</span><b class="${tone(T.net)}">${signed(T.net)}</b></div></div>
          ${dayVals.length > 1 ? `<div class="hx-chart">${pnlBars(dayVals.map((x) => x.net), dayVals.map((x) => x.day), 64)}<div class="axis"><span>${esc(dayVals[0].day.slice(5))}</span><span>날마다 비용 뺀 손익</span><span>${esc(dayVals[dayVals.length - 1].day.slice(5))}</span></div></div>` : ""}
          <div class="tiny">기간 안에 산 것 ${won(T.buys)}건 · 지금 들고 있는 것 ${won(T.open)}건 · 거래한 날 ${T.days}일${T.n ? ` · 이익/손실 비 ${T.pf ?? "-"}` : ""}</div>` : ""}
      </section>
      ${has ? `<div class="hx-tabs">${seg("기록보기", [["day", "날짜별"], ["strat", "전략별"], ["angle", "기준별"], ["trade", `거래 ${d.trades_n > 400 ? "400+" : d.trades_n}`]], H.view)}</div>
      <div id="hxBody">${this.hxBody()}</div>` : `<section class="card"><div class="empty">이 조건의 자동매매 기록이 아직 없어요. 모의 자동 테스트가 돌면 여기 쌓여요.</div></section>`}`;
    bindSeg(box, "기록기간", (v) => { H.days = Number(v); this.atLog(box); });
    bindSeg(box, "기록보기", (v) => { H.view = v; $("#hxBody").innerHTML = this.hxBody(); });
    $("#hxMkt").onchange = (e) => { H.mkt = e.target.value; H.strat = ""; this.atLog(box); };
    $("#hxMode").onchange = (e) => { H.mode = e.target.value; H.strat = ""; this.atLog(box); };
    $("#hxStrat").onchange = (e) => { H.strat = e.target.value; this.atLog(box); };
    box.onkeydown = (e) => { if ((e.key === "Enter" || e.key === " ") && e.target.matches("[data-hxcell],[data-hxsd]")) { e.preventDefault(); e.target.click(); } };
    box.onclick = async (e) => {
      const cell = e.target.closest("[data-hxcell]");
      if (cell && !e.target.closest("a")) return this.hxCell(cell.dataset.hxcell);
      const sd = e.target.closest("[data-hxsd]");
      if (sd && !e.target.closest("a")) {
        // 전략의 날짜별 한 줄: 그날 그 전략의 거래를 팝업으로
        const [sk, day] = sd.dataset.hxsd.split("|"), lab = ((H.d.strategies || []).find((x) => x.key === sk) || {}).label || "";
        openSheet(`${lab} · ${day.slice(5).replace("-", "/")}`, `<div class="empty small loading">그날 거래를 여는 중</div>`);
        try {
          const D = await GET(`/api/auto/history?${new URLSearchParams({ days: H.days, mkt: H.mkt, mode: H.mode, strat: sk, day })}`);
          const ts = D.trades.filter((t) => t.status === "closed" ? (t.exit_day || t.entry_day) === day : t.entry_day === day);
          $("#sheetBody").innerHTML = `<div class="hx-pop">${ts.map((t) => this.hxTrade(t, true)).join("") || `<div class="empty small">거래가 없어요</div>`}</div>`;
        } catch (err) { $("#sheetBody").innerHTML = `<div class="notice warn">${esc(err.message)}</div>`; }
        return;
      }
      const dy = e.target.closest("[data-hxday]"), st = e.target.closest("[data-hxst]"), only = e.target.closest("[data-hxonly]"), go = e.target.closest("[data-hxgo]"), more = e.target.closest("[data-hxmore]");
      if (more) {
        if (more.dataset.hxmore === "trade") H.tn = (H.tn || 40) + 40; else H.dayAll = more.dataset.hxmore;
        const y = scrollY; $("#hxBody").innerHTML = this.hxBody(); window.scrollTo(0, y); return;
      }
      if (only) { H.strat = only.dataset.hxonly; H.view = "day"; window.scrollTo({ top: 0, behavior: "smooth" }); return this.atLog(box); }
      if (go) { H.view = go.dataset.hxgo; $$('[data-seg="기록보기"]', box).forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.v === H.view))); $("#hxBody").innerHTML = this.hxBody(); return; }
      if (dy) {
        const day = dy.dataset.hxday;
        H.openDay = H.openDay === day ? "" : day;
        $("#hxBody").innerHTML = this.hxBody();
        if (H.openDay && !H.dd[day]) {
          try { H.dd[day] = await GET(`/api/auto/history?${new URLSearchParams({ days: H.days, mkt: H.mkt, mode: H.mode, strat: H.strat, day })}`); }
          catch (err) { H.dd[day] = { error: err.message }; }
          if (H.openDay === day && $("#hxBody")) $("#hxBody").innerHTML = this.hxBody();
        }
        if (gameOn() && H.openDay) $(".g-day")?.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
        return;
      }
      if (st) { H.openSt = H.openSt === st.dataset.hxst ? "" : st.dataset.hxst; $("#hxBody").innerHTML = this.hxBody(); }
    };
  },
  hxBody() {
    const H = this.hx, d = H.d;
    if (H.view === "strat") return this.hxStrats(d);
    if (H.view === "angle") return this.hxAngles(d);
    if (H.view === "trade") {
      const n = H.tn || 40, xs = d.trades.slice(0, n);
      return `<section class="card tight hx-list">${xs.map((t) => this.hxTrade(t)).join("") || `<div class="empty small">거래가 없어요</div>`}</section>
        ${d.trades.length > n ? `<button type="button" class="btn wide" data-hxmore="trade">거래 더 보기(${won(Math.min(40, d.trades.length - n))}건)</button>` : ""}
        ${d.trades_n > d.trades.length && d.trades.length <= n ? `<div class="tiny" style="padding:0 3px">최근 ${d.trades.length}건까지만 보여요. 기간이나 전략을 좁혀 보세요.</div>` : ""}`;
    }
    if (gameOn()) return this.gCal(d);
    return `<section class="card tight hx-list">${d.days.map((x) => {
      const S = x.stats, on = H.openDay === x.day;
      return `<div class="hx-item ${on ? "on" : ""}"><button type="button" class="hx-row" data-hxday="${x.day}" aria-expanded="${on}">
          <span class="hx-date"><b>${esc(x.day.slice(5).replace("-", "/"))}</b><small>${x.dow}</small></span>
          <span class="hx-mid"><b>${S.n ? `매도 ${S.n} · 승률 ${S.win_rate}% · 평균 ${pct(S.avg_pct, 2)}` : `매도 없음`}</b>
            <small>매수 ${x.buys} · 추천 ${x.cycles}번${x.quick ? `(빠른 ${x.quick})` : ""}${x.best && x.best.pnl > 0 ? ` · 1등 ${esc(x.best.label)}` : ""}</small>
            ${(x.by_mkt || []).length > 1 ? `<span class="hx-bm">${x.by_mkt.map((m) => `<em class="${m.mkt}">${m.mkt === "kr" ? "국내" : "미국"} ${m.n}건 <i class="${tone(m.pnl)}">${signed(m.pnl)}</i></em>`).join("")}</span>` : ""}
            ${(x.idx || []).length ? `<span class="hx-idx">${x.idx.map((i) => `<em class="${i.mkt}">${esc(i.name)} <i class="${tone(i.pct)}">${pct(i.pct, 2)}</i></em>`).join("")}</span>` : ""}</span>
          <span class="hx-val"><b class="${tone(x.net)}">${signed(x.net)}</b><small>AI ${won(x.ai_cost)}원 뺌</small></span></button>
        ${on ? `<div class="hx-more">${this.hxDay(x)}</div>` : ""}</div>`;
    }).join("") || `<div class="empty small">기록이 없어요</div>`}</section>`;
  },
  // ===== 게임 화면: 매매 기록 날짜별 = 손익 달력(칸 색 = 비용 뺀 손익), 날짜를 누르면 그날 전투 기록 =====
  gCal(d) {
    const H = this.hx, days = d.days || [];
    if (!days.length) return `<section class="card"><div class="empty small">기록이 없어요</div></section>`;
    const by = Object.fromEntries(days.map((x) => [x.day, x])), mx = Math.max(1, ...days.map((x) => Math.abs(x.net)));
    const short = (v) => { const a = Math.abs(v), sg = v > 0 ? "+" : v < 0 ? "-" : ""; return a >= 1e8 ? `${sg}${(a / 1e8).toFixed(1)}억` : a >= 1e4 ? `${sg}${Math.round(a / 1e4)}만` : `${sg}${won(a)}`; };
    const months = [...new Set(days.map((x) => x.day.slice(0, 7)))].sort().reverse();
    const today = new Date(Date.now() + 9 * 3600e3).toISOString().slice(0, 10);
    const cal = months.map((ym) => {
      const [y, m] = ym.split("-").map(Number), first = new Date(Date.UTC(y, m - 1, 1)), n = new Date(Date.UTC(y, m, 0)).getUTCDate();
      const cells = [];
      let lead = (first.getUTCDay() + 6) % 7;                 // 월요일부터
      if (lead > 4) lead = 0;                                  // 주말에 시작하면 다음 월요일부터
      for (let i = 0; i < lead; i++) cells.push(`<span class="g-cd pad"></span>`);
      for (let dd = 1; dd <= n; dd++) {
        const dt = new Date(Date.UTC(y, m - 1, dd)), wd = dt.getUTCDay();
        if (wd === 0 || wd === 6) continue;
        const key = `${ym}-${String(dd).padStart(2, "0")}`, x = by[key];
        if (!x) { cells.push(`<span class="g-cd none ${key === today ? "today" : ""}"><i>${dd}</i></span>`); continue; }
        const k = Math.min(1, Math.abs(x.net) / mx) * .8 + .2, bg = x.net >= 0 ? `rgba(214,44,74,${k.toFixed(2)})` : `rgba(36,108,224,${k.toFixed(2)})`;
        cells.push(`<button type="button" class="g-cd ${H.openDay === key ? "on" : ""} ${key === today ? "today" : ""}" data-hxday="${key}" style="background:${bg}"
          aria-label="${m}월 ${dd}일 비용 뺀 손익 ${signed(x.net)}원, 매도 ${x.sells}건" aria-pressed="${H.openDay === key}">
          <i>${dd}</i><b>${short(x.net)}</b>${x.stats.n ? `<small>${x.stats.win_rate}%</small>` : ""}</button>`);
      }
      const sum = days.filter((x) => x.day.startsWith(ym)).reduce((a, x) => a + x.net, 0);
      return `<div class="g-cal-m"><div class="g-cal-h"><b>${y}년 ${m}월</b><span class="${tone(sum)}">${signed(sum)}원</span></div>
        <div class="g-cal-w">${"월화수목금".split("").map((w) => `<span>${w}</span>`).join("")}</div><div class="g-cal">${cells.join("")}</div></div>`;
    }).join("");
    const x = by[H.openDay];
    return `<section class="g-calbox">${cal}
        <div class="g-map-l"><span>칸 = 그날 AI 비용 뺀 손익 · 작은 숫자 = 승률</span><span><i style="background:${G.heat(5)}"></i>이익 <i style="background:${G.heat(-5)}"></i>손해</span></div></section>
      ${x ? `<section class="g-day">
          <div class="g-day-h"><b>${esc(x.day.slice(5).replace("-", "/"))}(${x.dow}) 전투 기록</b><b class="${tone(x.net)}">${signed(x.net)}원</b></div>
          <div class="g-st-k big"><span>${G.ico.flag}<small>매도 · 승률</small><b>${x.stats.n ? `${x.sells} · ${x.stats.win_rate}%` : x.sells}</b></span><span>${G.ico.bag}<small>매수</small><b>${x.buys}</b></span>
            <span>${G.ico.radar}<small>추천${x.quick ? `(빠른 ${x.quick})` : ""}</small><b>${x.cycles}</b></span><span>${G.ico.bolt}<small>AI 비용</small><b>${won(x.ai_cost)}</b></span></div>
          ${x.best && x.best.pnl > 0 ? `<div class="g-mvp">${G.ico.trophy}<span><small>오늘의 MVP</small><b>${esc(x.best.label)}</b><em>${esc(x.best.group)}</em></span><b class="up">${signed(x.best.pnl)}원</b></div>` : ""}
          <div class="hx-more">${this.hxDay(x)}</div></section>`
        : `<div class="g-hint">${G.ico.search}<span>달력에서 날짜를 누르면 그날 지수·전략별 성적·받은 추천·거래가 열려요</span></div>`}`;
  },
  hxDay(x) {
    const D = this.hx.dd[x.day];
    if (!D) return `<div class="empty small loading">그날 기록을 여는 중</div>`;
    if (D.error) return `<div class="notice warn">${esc(D.error)}</div>`;
    const cl = D.trades.filter((t) => t.status === "closed" && (t.exit_day || t.entry_day) === x.day), by = {};
    cl.forEach((t) => { const g = by[t.strat] || (by[t.strat] = { label: t.strat_label, group: t.strat_group, n: 0, w: 0, sp: 0, pnl: 0 }); g.n++; g.w += t.pnl > 0 ? 1 : 0; g.sp += t.pnl_pct || 0; g.pnl += t.pnl_krw || 0; });
    const rows = Object.values(by).sort((a, b) => b.pnl - a.pnl);
    const recs = (D.day && D.day.recs) || [];
    const ix = (D.days.find((y) => y.day === x.day) || x).idx || x.idx || [];
    const iv = (v) => (v == null ? "-" : v >= 1000 ? Number(v).toLocaleString("ko-KR", { maximumFractionDigits: 2 }) : Number(v).toFixed(2));
    return `${ix.length ? `<div class="hx-sub">그날 지수</div><div class="hx-ixg">${ix.map((i) => `<div class="${i.mkt}"><span>${esc(i.name)}</span>
        <b class="${tone(i.pct)}">${pct(i.pct, 2)}</b><small>종가 ${iv(i.close)}${i.hi != null && i.lo != null ? ` · 장중 ${pct(i.lo, 1)}~${pct(i.hi, 1)}` : ""}</small></div>`).join("")}</div>` : ""}
      ${rows.length ? `<div class="hx-sub">전략별 그날 성적</div><div class="hx-tbl">${rows.map((g) => `<div><span><b>${esc(g.label)}</b><small>${esc(g.group)}</small></span>
        <span>${g.n}건 · 승 ${g.w}</span><span class="${tone(g.sp)}">${pct(g.sp / g.n, 2)}</span><b class="${tone(g.pnl)}">${signed(g.pnl)}</b></div>`).join("")}</div>` : ""}
      ${recs.length ? `<details class="hx-det"><summary class="hx-sub">받은 추천 ${recs.length}번 <small>정밀 ${recs.filter((r) => !r.lite).length} · 빠른 ${recs.filter((r) => r.lite).length} · 펼치기</small></summary><ol class="hx-recs">${recs.map((r) => `<li><time>${esc(r.ts.slice(11, 16))}</time>
        <div><b>${r.lite ? `<span class="tag y">빠른</span>` : `<span class="tag">정밀</span>`} ${esc(r.group)}${r.stance ? ` · ${esc(r.stance)}` : ""}</b>
          <span>${r.picks.length ? `${r.picks.map(esc).join(", ")} → ${r.bought ? `플랜 ${r.bought}번 매수` : "안 삼"}` : esc(r.msg || r.view)}${r.cost ? ` · ${won(r.cost)}원` : ""}</span>
          ${r.view && r.picks.length ? `<small>${esc(r.view)}</small>` : ""}</div></li>`).join("")}</ol></details>` : ""}
      <div class="hx-sub">그날 거래 ${D.trades.length}건</div>${D.trades.slice(0, this.hx.dayAll === x.day ? 400 : 12).map((t) => this.hxTrade(t, true)).join("") || `<div class="empty small">거래가 없어요</div>`}
      ${D.trades.length > 12 && this.hx.dayAll !== x.day ? `<button type="button" class="btn wide sm" data-hxmore="${x.day}">그날 거래 ${D.trades.length}건 모두 보기</button>` : ""}`;
  },
  hxStrats(d) {
    const H = this.hx, by = {};
    d.strategies.forEach((s) => (by[s.group] = by[s.group] || []).push(s));
    const hold = (m) => m == null ? "-" : m < 60 ? `${Math.round(m)}분` : m < 1440 ? `${Math.floor(m / 60)}시간${Math.round(m % 60) ? ` ${Math.round(m % 60)}분` : ""}` : `${(m / 1440).toFixed(1)}일`;
    return Object.entries(by).map(([g, xs]) => `<div class="sec-title">${esc(g)}</div><section class="card tight hx-list">${xs.map((s) => {
      const S = s.stats, on = H.openSt === s.key;
      return `<div class="hx-item ${on ? "on" : ""}"><button type="button" class="hx-row" data-hxst="${esc(s.key)}" aria-expanded="${on}">
          <span class="hx-mid"><b>${esc(s.label)} ${s.mode === "real" ? `<span class="tag warn">실전</span>` : ""}${s.status === "running" ? `<span class="tag y">가동 중</span>` : ""}</b>
            <small>${S.n ? `${S.n}건 · 승률 ${S.win_rate}% · 평균 ${pct(S.avg_pct, 2)} · 이익/손실 비 ${S.pf ?? "-"}` : "끝난 거래 없음"}${s.open ? ` · 보유 ${s.open}` : ""}</small></span>
          <span class="hx-val"><b class="${tone(s.net)}">${signed(s.net)}</b><small>${s.net_pct != null ? pct(s.net_pct, 2) : ""} · AI ${won(s.ai_cost)}원</small></span></button>
        ${on ? `<div class="hx-more">
          <div class="hx-kv"><div><span>평균 이익 · 손실</span><b><i class="up">${pct(S.avg_win, 2)}</i> · <i class="down">${pct(S.avg_loss, 2)}</i></b></div>
            <div><span>가장 좋았던 · 나빴던</span><b><i class="up">${pct(S.best, 2)}</i> · <i class="down">${pct(S.worst, 2)}</i></b></div>
            <div><span>들고 있던 시간(중간값)</span><b>${hold(S.hold_med)}</b></div>
            <div><span>최대 상승 · 하락(중간값)</span><b>${S.mfe_med != null ? `${pct(S.mfe_med, 2)} · ${pct(-Math.abs(S.mae_med || 0), 2)}` : "-"}</b></div>
            <div><span>손익(AI 비용 전)</span><b class="${tone(S.pnl_krw)}">${S.n ? signed(S.pnl_krw) : "-"}</b></div>
            <div><span>거래한 날 · 쓴 돈</span><b>${s.days}일 · ${won(s.budget)}</b></div></div>
          ${s.reasons.length ? `<div class="chips hx-chips">${s.reasons.map(([k, n]) => `<span class="chip">${esc(k)} ${n}</span>`).join("")}</div>` : ""}
          ${s.by_day.length ? `<div class="hx-sub">날짜별</div><div class="hx-tbl">${s.by_day.map((x) => `<div class="hx-link" role="button" tabindex="0" data-hxsd="${esc(s.key)}|${x.day}" aria-label="${esc(x.day)} 거래 보기"><span><b>${esc(x.day.slice(5).replace("-", "/"))}<i class="hx-chev" aria-hidden="true">›</i></b></span><span>${x.n}건 · 승 ${x.win}</span>
            <span class="${tone(x.avg)}">${pct(x.avg, 2)}</span><b class="${tone(x.pnl)}">${signed(x.pnl)}</b></div>`).join("")}</div>` : ""}
          <button type="button" class="btn wide sm" data-hxonly="${esc(s.key)}">이 전략만 날짜별·기준별로 보기</button></div>` : ""}</div>`;
    }).join("")}</section>`).join("") || `<section class="card"><div class="empty small">기록이 없어요</div></section>`;
  },
  // 기준별 성적 한 칸(예: '12시', '목', '관망')을 누르면 그 칸에 든 거래를 팝업으로
  hxCell(key) {
    const d = this.hx.d;
    if (!d) return;
    const [g, i] = key.split("|"), r = g === "s" ? (d.symbols || [])[+i] : ((d.groups[+g] || {}).rows || [])[+i];
    if (!r) return;
    const title = g === "s" ? r.name : `${d.groups[+g].label} · ${r.key}`;
    const ids = new Set(r.ids || []), ts = d.trades.filter((t) => ids.has(t.id));
    const miss = Math.max(0, (r.ids || []).length - ts.length);
    const html = `<div class="sc-kpi"><div><span>거래 · 승률</span><b>${r.n}건 · ${r.win_rate}%</b></div><div><span>한 번 평균</span><b class="${tone(r.avg_pct)}">${pct(r.avg_pct, 2)}</b></div>
        <div><span>손익</span><b class="${tone(r.pnl_krw)}">${signed(r.pnl_krw)}</b></div></div>
      ${r.avg_win != null ? `<div class="tiny" style="margin:6px 2px">이길 때 평균 <b class="up">${pct(r.avg_win, 2)}</b> · 질 때 평균 <b class="down">${pct(r.avg_loss, 2)}</b>${r.best != null ? ` · 가장 좋았던 ${pct(r.best, 2)} · 나빴던 ${pct(r.worst, 2)}` : ""}</div>` : ""}
      <div class="hx-sub">거래 ${ts.length}건${miss ? ` (오래된 ${miss}건은 기간을 줄이거나 전략을 골라 보세요)` : ""}</div>
      <div class="hx-pop">${ts.map((t) => this.hxTrade(t)).join("") || `<div class="empty small">보여 줄 거래가 없어요</div>`}</div>`;
    openSheet(title, html);
  },
  hxAngles(d) {
    const sym = d.symbols || [];
    return `${d.groups.map((g, gi) => `<section class="card hx-ang"><div class="card-head"><h2>${esc(g.label)}</h2><span class="tiny">건수 · 승률 · 한 번 평균 · 손익</span></div>
      ${g.rows.map((r, ri) => `<div class="hx-ar hx-link" role="button" tabindex="0" data-hxcell="${gi}|${ri}" aria-label="${esc(g.label)} ${esc(r.key)} 거래 ${r.n}건 보기"><span class="hx-ak">${esc(r.key)}<i class="hx-chev" aria-hidden="true">›</i></span><span class="hx-an">${r.n}건</span>
        <span class="hx-bar" role="img" aria-label="승률 ${r.win_rate}%"><i style="width:${r.win_rate}%"></i><em>${r.win_rate}%</em></span>
        <b class="${tone(r.avg_pct)}">${pct(r.avg_pct, 2)}</b><small class="${tone(r.pnl_krw)}">${signed(r.pnl_krw)}</small></div>`).join("")}</section>`).join("")}
      ${sym.length ? `<section class="card hx-ang"><div class="card-head"><h2>자주 거래한 종목</h2><span class="tiny">이름은 차트, 줄은 거래 목록</span></div>
        ${sym.map((r, ri) => `<div class="hx-ar hx-link" role="button" tabindex="0" data-hxcell="s|${ri}" aria-label="${esc(r.name)} 거래 ${r.n}건 보기"><span class="hx-ak">${stockA(r.mkt, r.sym, r.name)}<i class="hx-chev" aria-hidden="true">›</i></span><span class="hx-an">${r.n}건</span>
          <span class="hx-bar"><i style="width:${r.win_rate}%"></i><em>${r.win_rate}%</em></span><b class="${tone(r.avg_pct)}">${pct(r.avg_pct, 2)}</b><small class="${tone(r.pnl_krw)}">${signed(r.pnl_krw)}</small></div>`).join("")}</section>` : ""}
      <div class="tiny" style="padding:0 3px">건수가 적은 칸은 운일 수 있어요. 10건 넘는 칸끼리 비교해 보세요.</div>`;
  },
  hxTrade(t, inDay) {
    const us = t.mkt === "us", P = (v) => v == null ? "-" : us ? `$${Number(v).toFixed(2)}` : `${won(v)}원`;
    const hold = t.hold_min == null ? "" : t.hold_min < 60 ? `${t.hold_min}분` : t.hold_min < 1440 ? `${Math.floor(t.hold_min / 60)}시간 ${t.hold_min % 60}분` : `${(t.hold_min / 1440).toFixed(1)}일`;
    const open = t.status === "open", p = open ? (t.last && t.entry_price ? (t.last / t.entry_price - 1) * 100 : null) : t.pnl_pct;
    const ts = (x) => x ? x.slice(5, 16).replace("T", " ").replace("-", "/") : "";
    return `<div class="hx-tr">
      <div class="hx-tr-a"><span>${stockA(t.mkt, t.sym, t.name)} <small>${esc(t.strat_label)}${inDay ? "" : ` · ${esc(t.strat_group)}`}</small></span>
        <b class="${tone(p)}">${p == null ? "-" : pct(p, 2)}</b></div>
      <div class="hx-tr-b"><span>산 때 ${esc(ts(t.entry_ts))} · ${P(t.entry_price)} × ${won(t.qty)}주</span>
        <span>${open ? `보유 중 · 지금 ${P(t.last)}` : `판 때 ${esc(ts(t.exit_ts))} · ${P(t.exit_price)} · ${esc(t.reason || "")}`}</span></div>
      <div class="hx-tr-c">${t.mode === "real" ? `<span class="tag warn">실전</span>` : `<span class="tag">모의</span>`}${t.lite ? `<span class="tag y">빠른 재추천</span>` : t.engine ? `<span class="tag">${esc(t.engine)}</span>` : ""}
        <span>목표 +${t.target_pct}% · 손절 −${t.stop_pct}%</span>${t.win_prob != null ? `<span>AI 승률 ${Math.round(t.win_prob)}%</span>` : ""}${t.score != null ? `<span>점수 ${Math.round(t.score)}</span>` : ""}
        ${t.stance ? `<span>${esc(t.stance)}</span>` : ""}${hold ? `<span>${hold} 보유</span>` : ""}${!open && t.pnl_krw ? `<span class="${tone(t.pnl_krw)}">${signed(t.pnl_krw)}원</span>` : ""}
        ${t.mfe_pct != null ? `<span>최대 +${Number(t.mfe_pct).toFixed(2)}% / −${Math.abs(Number(t.mae_pct || 0)).toFixed(2)}%</span>` : ""}</div>
      ${t.catalyst || (t.reasons && t.reasons.length) ? `<div class="hx-tr-d">${esc(t.catalyst || t.reasons.join(" · "))}</div>` : ""}</div>`;
  },
  // 하루 AI 비용 한도만 빠르게 바꾼다(현황의 'AI 비용'과, 한도에 걸렸을 때 뜨는 버튼에서 연다)
  atAiLimit() {
    const A = this.at, d = A.d, N = d.now, cur = Number(d.params.ai_daily_usd != null ? d.params.ai_daily_usd : 1);
    const qg = Number(d.params.quick_gap_min) || 0;
    const per = qg ? 0.15 : N && N.ai.cycles_all ? N.ai.total_usd / N.ai.cycles_all : 0.8, need = Math.ceil((per * d.params.cycles_per_day + (qg ? 0.2 : 0)) * 2) / 2;
    openSheet("하루 AI 비용 한도", `<div class="at-bud">
        <div class="at-bud-now"><span>지금 한도 · 오늘 쓴 돈</span><b>$${cur} · $${N ? N.ai.today_usd.toFixed(2) : "0.00"}</b></div>
        <label class="field">새 한도($, 0.2~200)<input id="alV" class="r big" inputmode="decimal" autocomplete="off" value="${Math.max(cur, need)}"></label>
        <div class="chips">${[3, 5, 10, 20].map((v) => `<button type="button" class="chip" data-v="${v}">$${v}</button>`).join("")}</div>
        <div class="tiny">${qg ? `정밀 추천은 한 번에 약 $0.1~0.2, ${qg}분마다 받는 빠른 재추천은 한 번에 약 $0.01이 들어요. 하루 일정을 다 받으려면 $${need} 정도면 돼요.` : `추천 한 번에 약 $${per.toFixed(2)}(약 ${won(Math.round(per * 1400 / 10) * 10)}원)가 들어요. 하루 ${d.params.cycles_per_day}번을 다 받으려면 $${need} 정도가 필요해요.`} 올리면 바로 다음 추천을 받아요. 조건이 같은 자동매매끼리는 추천을 한 번만 받아 함께 써요.</div>
        <button type="button" class="btn dark wide" id="alGo">한도 바꾸기</button></div>`, (box) => {
      box.addEventListener("click", (e) => { const c = e.target.closest("[data-v]"); if (c) $("#alV", box).value = c.dataset.v; });
      $("#alGo", box).onclick = async () => {
        try { A.d = await run(() => api("PUT", `/api/auto/runs/${d.run.id}/params`, { ai_daily_usd: $("#alV", box).value }), "한도를 바꿨어요"); } catch (e) { return; }
        closeSheet(); this.auto($("#iv"));
      };
    });
  },
  atEdit() {
    const A = this.at, d = A.d, p = d.params;
    openSheet("플랜 직접 고치기", `<div class="fields">${(Array.isArray(d.keys) ? d.keys : Object.entries(d.keys)).map(([k, l]) => typeof p[k] === "boolean"
      ? `<label class="field">${esc(l)}<select data-k="${k}"><option value="true" ${p[k] ? "selected" : ""}>예</option><option value="false" ${p[k] ? "" : "selected"}>아니오</option></select></label>`
      : `<label class="field">${esc(l)}<input data-k="${k}" class="r" ${/^entry_/.test(k) ? 'type="time"' : 'inputmode="decimal"'} value="${esc(p[k] != null ? p[k] : "")}"></label>`).join("")}</div>
      <div class="fields" style="margin-top:8px"><label class="field">하루 AI 비용 한도($) · 정밀 추천과 빠른 재추천을 합쳐서 봐요<input data-k="ai_daily_usd" class="r" inputmode="decimal" value="${esc(p.ai_daily_usd != null ? p.ai_daily_usd : 1)}"></label></div>
      <div class="tiny" style="margin:8px 0">고치면 플랜 판이 하나 올라가고 이력에 남아요. 범위를 벗어난 값은 가장 가까운 값으로 맞춰요.</div>
      <button type="button" class="btn dark wide" id="atSave">저장</button>`, (box) => {
      $("#atSave", box).onclick = async () => {
        const patch = {};
        $$("[data-k]", box).forEach((i) => { patch[i.dataset.k] = i.value; });
        A.d = await run(() => api("PUT", `/api/auto/runs/${d.run.id}/params`, patch), "저장했어요");
        closeSheet(); this.auto($("#iv"));
      };
    });
  },

  // ---------- AI 추천 ----------
  async aiPick(box) {
    const A = this.ap || (this.ap = { m: store.get("apMkt") === "us" ? "us" : "kr", h: AI_H[store.get("apH")] ? store.get("apH") : "day", theme: "", model: store.get("apModel") || "", web: store.get("apWeb") !== "0", est: {}, busy: false, res: null, text: "", src: [] });
    if (!$("#apRoot", box)) {
      A.m = curMkt(A.m);
      $(".fab")?.remove();
      box.innerHTML = `<div id="apRoot" class="ap">
        <section class="ap-hero">
          <div class="ap-hz" role="group" aria-label="추천 종류">${Object.entries(AI_H).map(([k, v]) => `<button type="button" data-hz="${k}" aria-pressed="${k === A.h}"><b>${v.label}</b><small>${v.sub}</small></button>`).join("")}</div>
          <div class="ap-h1" id="apH1"></div>
          <div class="ap-h2" id="apH2"></div>
          ${seg("AI시장", [["kr", "국내장"], ["us", "미국장"]], A.m, "ap-mkt")}
          <div class="ap-sess" id="apSess"></div>
          <a class="btn ap-statbtn" href="#invest/aistat"><svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M5 20V11M12 20V5M19 20v-7"/></svg>AI 추천 전체 성적 · 자체 학습 보기 ›</a>
        </section>
        <a class="card ap-score" id="apScore" href="#invest/aistat"><div class="empty small loading">추천 성적 불러오는 중</div></a>
        <section class="card"><div class="card-head"><h2>추천 엔진</h2><span class="tiny" id="apFx"></span></div>
          <div class="ap-models" id="apModels"><div class="empty small loading">불러오는 중</div></div>
          <label class="field ap-theme" id="apThemeBox" hidden>궁금한 테마(선택)<input id="apTheme" maxlength="60" autocomplete="off" placeholder="예: 시험장 전파 탐지, 원전, 방산" value="${esc(A.theme)}">
            <span class="hint">적으면 그 테마의 재료가 아직 유효한지부터 따지고 관련 종목을 찾아 평가해요. 비우면 AI가 재료를 직접 찾아요.</span>
            <button type="button" class="btn sm" id="apBasket">이 테마를 여러 종목으로 나눠 담기(바구니 조사)</button></label>
          <label class="ap-web"><input type="checkbox" id="apWeb" ${A.web ? "checked" : ""}><span><b id="apWebT"></b><small id="apWebS"></small></span></label>
          <button type="button" class="btn sm ap-my" id="apMy">내 의견 넣기: 메모 · 제외 종목</button>
          <button type="button" class="btn ap-go wide" id="apGo">지금 추천 받기</button>
          <div id="apMeter" class="cm-slot"></div>
          <div class="tiny" id="apWarn"></div>
        </section>
        <div id="apOut"></div>
        <div id="apGuards"></div>
        <div id="apHist"></div>
        <div class="tiny" style="padding:0 3px 6px">AI 추천은 실시간 자료로 낸 추정이라 틀릴 수 있어요. 승률은 AI가 매긴 추정치예요. 매수 여부는 직접 결정하고, 자동 매도는 켜 둔 종목만 정해진 값으로 실행돼요.</div>
      </div>`;
      bindSeg(box, "AI시장", (v) => { setMkt(v); A.m = v; store.set("apMkt", v === "us" ? "us" : ""); this.apEstimate(); });
      $(".ap-hz", box).onclick = (e) => {
        const b = e.target.closest("[data-hz]");
        if (!b || A.busy || b.dataset.hz === A.h) return;
        A.h = b.dataset.hz; store.set("apH", A.h === "day" ? "" : A.h);
        $$(".ap-hz [data-hz]", box).forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
        this.apKind(); this.apEstimate(); this.apScore();
      };
      $("#apTheme").oninput = (e) => { A.theme = e.target.value; };
      $("#apBasket").onclick = (e) => { e.preventDefault(); this.apBasket(); };
      $("#apMy").onclick = () => this.mySheet();
      this.apKind();
      $("#apWeb").onchange = (e) => { A.web = e.target.checked; store.set("apWeb", A.web ? "" : "0"); this.apModels(); };
      $("#apModels").onclick = (e) => { const b = e.target.closest("[data-model]"); if (b) { A.model = b.dataset.model; store.set("apModel", A.model); this.apModels(); } };
      $("#apGo").onclick = () => this.apRun();
      if (A.res || A.text) this.apDraw();
      this.apEstimate();
      this.apHistory();
      this.apScore();
    }
    this.apSess();
    this.apGuards();
  },
  // 추천 종류(당일·1주·1달)에 맞춰 설명 문구와 입력 칸을 바꾼다
  apKind() {
    const A = this.ap, H = AI_H[A.h], day = A.h === "day";
    if (!$("#apH1")) return;
    $("#apH1").textContent = H.title;
    $("#apH2").textContent = H.desc;
    $("#apThemeBox").hidden = day;
    $("#apWebT").textContent = day ? "오늘 뉴스·공시 확인" : "뉴스·정책·공시에서 재료 찾기";
    $("#apWebS").textContent = day ? "웹 검색 최대 3회 · 재료 확인으로 정확도↑ · 비용↑" : "웹 검색 최대 6회 · 중기 추천의 핵심이에요. 끄면 일봉 흐름만 보고 골라요";
  },
  apSess() {
    const A = this.ap, S = marketSession(A.m), el = $("#apSess");
    if (!el) return;
    el.innerHTML = `<span class="ss-chip ss-${S.phase}"><i></i>${esc(S.label)}</span><span>${esc(S.sub)}</span>`;
    if (A.h !== "day") { $("#apWarn").textContent = S.phase === "open" ? "" : "지금은 정규장이 아니에요. 마지막 가격 기준으로 고르고, 다음 정규장에 사는 것으로 봐요. 그사이 시가가 크게 달라질 수 있어요."; return; }
    $("#apWarn").textContent = S.phase === "open" ? "" : S.phase === "closed"
      ? "지금은 정규장이 아니라, 다음 정규장 시작 직후 기준으로 추천해요. 자동 익절·손절은 정규장에만 움직여요."
      : "지금은 정규장 밖이에요. 시간외·프리마켓은 호가가 얇아 추천 정확도가 떨어져요. 자동 익절·손절은 정규장에만 움직여요.";
  },
  async apEstimate() {
    const A = this.ap;
    try { A.est = await GET(`/api/ai/pick/estimate?mkt=${A.m}&h=${A.h}`); } catch (e) { A.est = { error: e.message, models: [] }; }
    if (!A.model || !A.est.models.some((x) => x.id === A.model)) A.model = A.est.default || (A.est.models[0] || {}).id;
    this.apModels(); this.apSess();
  },
  apCost(x) {
    const A = this.ap;
    if (!x) return "";
    if (x.avg_krw != null && A.web) return `약 ${won(x.avg_krw)}원`;
    return A.web ? `약 ${won(x.krw_lo)}~${won(x.krw_hi)}원` : `약 ${won(Math.max(1, x.krw_lo))}원`;
  },
  apModels() {
    const A = this.ap, E = A.est || {}, el = $("#apModels");
    if (!el) return;
    if (E.error) { el.innerHTML = `<div class="notice warn">${esc(E.error)}</div>`; return; }
    const note = { "claude-fable-5-2": "가장 똑똑함 · 미묘한 신호까지 · 가장 비쌈", "claude-opus-5-5": "후보를 가장 깊게 비교 · 느림", "claude-sonnet-5-5": "정확도·속도·비용 균형", "claude-haiku-4-5": "빠르고 싸지만 판단이 얕음" };
    el.innerHTML = (E.models || []).map((x) => {
      const [nm] = x.label.split(" · ");
      return `<button type="button" class="ap-model" data-model="${esc(x.id)}" aria-pressed="${x.id === A.model}">
        <span class="ap-radio" aria-hidden="true"></span>
        <span class="ap-mn"><b>${esc(nm)}${x.id === "claude-sonnet-5-5" ? ` <span class="tag y">추천</span>` : ""}</b><small>${esc(note[x.id] || "")}</small></span>
        <span class="ap-cost"><b>${this.apCost(x)}</b><small>${x.n ? `지난 ${x.n}회 평균` : "1회 예상"}</small></span></button>`;
    }).join("");
    $("#apFx").textContent = E.fx ? `환율 ${won(E.fx)}원 기준` : "";
    const go = $("#apGo");
    go.disabled = A.busy || !E.configured || !E.kiwoom;
    go.innerHTML = A.busy ? "AI가 고르는 중…" : `<span class="ai-spark" aria-hidden="true">✦</span> ${AI_H[A.h].go}`;
    aiMeter.get(A.h === "day" ? "pick" : "pickw", A.model, A.web).then((m) => { A.meter = m; if ($("#apMeter")) $("#apMeter").innerHTML = meterHTML(m); }).catch(() => {});
    if (!E.configured) $("#apWarn").innerHTML = `Claude API 키가 필요해요. <a href="#settings/ai">설정 → AI</a>`;
    else if (!E.kiwoom) $("#apWarn").innerHTML = `실시간 순위·일봉을 쓰려면 키움 계좌 연결이 필요해요. <a href="#settings/accounts">연결하기</a>`;
  },
  async apRun() {
    const A = this.ap;
    if (A.busy) return;
    A.busy = true; A.res = null; A.votes = {}; A.text = ""; A.shown = false; A.used = null; A.src = []; A.status = A.h === "day" ? "실시간 순위와 분봉을 모으는 중" : "순위·관심종목의 일봉 흐름을 모으는 중";
    this.apModels(); this.apDraw();
    let raf = 0;
    try {
      await aiStream({ kind: "pick", mkt: A.m, model: A.model, web_search: A.web, horizon: A.h, theme: A.h === "day" ? "" : A.theme.trim() }, (ev) => {
        if (ev.t === "status") { A.status = ev.msg; this.apDraw(); }
        else if (ev.t === "text") { A.text += ev.text; A.status = "분석 중"; if (!raf) raf = requestAnimationFrame(() => { raf = 0; this.apDraw(); }); }
        else if (ev.t === "reset") A.text = "";
        else if (ev.t === "source") { A.src.push(ev); }
        else if (ev.t === "picks") { A.res = ev; }
        else if (ev.t === "error") { A.err = ev.msg; }
        else if (ev.t === "done") { A.used = ev; aiMeter.clear(); }
      });
    } catch (e) { if (e instanceof NeedConnect) { location.hash = "#connect"; return; } A.err = e.message; }
    finally { if (raf) cancelAnimationFrame(raf); A.busy = false; A.status = ""; this.apDraw(); this.apEstimate(); this.apHistory(); this.apScore(); }
  },
  // 테마 바구니: 테마 하나를 여러 종목으로 나눠 담는 구성을 조사해 보여 준다(주문은 내지 않는다)
  async apBasket() {
    const A = this.ap, th = (A.theme || "").trim();
    if (!th) return toast("테마를 먼저 적어 주세요", true);
    if (A.busy) return;
    A.busy = true; A.bk = null; A.res = null; A.err = ""; A.text = ""; A.used = null; A.src = []; A.status = "테마의 재료를 찾아보는 중";
    this.apDraw();
    try {
      await aiStream({ kind: "basket", mkt: A.m, model: A.model, web_search: true, theme: th }, (ev) => {
        if (ev.t === "status") { A.status = ev.msg; this.apDraw(); }
        else if (ev.t === "text") { A.text += ev.text; A.status = "구성하는 중"; }
        else if (ev.t === "reset") A.text = "";
        else if (ev.t === "source") A.src.push(ev);
        else if (ev.t === "basket") A.bk = ev;
        else if (ev.t === "error") A.err = ev.msg;
        else if (ev.t === "done") { A.used = ev; aiMeter.clear(); }
      });
    } catch (e) { if (e instanceof NeedConnect) { location.hash = "#connect"; return; } A.err = e.message; }
    finally { A.busy = false; A.status = ""; this.apDraw(); }
  },
  basketHtml(b) {
    const us = b.mkt === "us", P = (n) => (us ? usd(n) : won(Math.round(n)) + "원");
    return `<section class="card bk"><div class="card-head"><h2>${esc(b.theme)} 바구니</h2><span class="tag ${b.alive ? "" : "warn"}">${b.alive ? "재료 유효" : "재료 약함"}</span></div>
      ${b.view ? `<div class="small"><b>${esc(b.view)}</b></div>` : ""}
      ${b.stocks.length ? `<div class="bk-list">${b.stocks.map((x) => `<a class="bk-row" href="#invest/stock?m=${b.mkt}&s=${encodeURIComponent(x.sym)}">
          <span class="bk-w"><b>${x.w}%</b><i style="height:${Math.min(100, x.w * 2.5).toFixed(0)}%"></i></span>
          <span class="bk-m"><b>${esc(x.name)}</b><small>${esc(x.role)} · 지금 ${P(x.price)}${x.change_pct == null ? "" : ` <em class="${tone(x.change_pct)}">${pct(x.change_pct, 2)}</em>`}</small>
            <span>${esc(x.why)}</span>${x.risk ? `<span class="bk-r">위험: ${esc(x.risk)}</span>` : ""}${x.warn ? `<span class="tag warn">${esc(x.warn)}</span>` : ""}</span></a>`).join("")}</div>`
        : `<div class="empty">담을 만한 종목을 찾지 못했어요</div>`}
      ${b.risks.length ? `<ul class="at-notes">${b.risks.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>` : ""}
      <div class="tiny">${b.check ? `다시 확인할 때: ${esc(b.check)} · ` : ""}AI가 조사한 구성안이에요. 비중은 참고용이고 주문은 내지 않아요.</div></section>`;
  },
  // 내 의견: 메모 · 제외 종목(팝업). AI 추천이 메모를 함께 읽고, 제외 종목은 AI가 골라도 사지 않는다
  async mySheet() {
    let V;
    try { V = await GET("/api/myview"); } catch (e) { return toast(e.message, true); }
    const mk = this.ap ? this.ap.m : "kr";
    const draw = () => `<div class="my">
        <div class="hx-sub">메모 <small>${V.notes.filter((n) => n.live).length}/${V.max_notes}</small></div>
        <p class="tiny">AI 추천(자동매매 포함)이 매번 함께 읽어요. 참고 자료로만 쓰고, 사실과 다르면 따르지 않도록 일러 뒀어요. 예: "이번 주 조선·방산 관심", "실적 발표 앞둔 종목은 조심", "바이오는 당분간 빼 줘"</p>
        ${V.notes.map((n) => `<div class="my-row ${n.live ? "" : "off"}"><span><b>${esc(n.text)}</b><small>${n.mkt === "all" ? "국내·미국" : n.mkt === "kr" ? "국내" : "미국"} · ${n.until ? (n.live ? `${esc(n.until.slice(5).replace("-", "/"))}까지` : "기한 지남") : "계속"}</small></span>
          <button type="button" class="my-x" data-dn="${n.id}" aria-label="메모 지우기">지우기</button></div>`).join("") || `<div class="empty small">아직 메모가 없어요</div>`}
        <label class="field">새 메모<input id="myText" maxlength="${V.note_len}" placeholder="짧게 한 줄" autocomplete="off"></label>
        <div class="fields"><label class="field">시장<select id="myMkt"><option value="all">국내·미국</option><option value="kr" ${mk === "kr" ? "selected" : ""}>국내만</option><option value="us" ${mk === "us" ? "selected" : ""}>미국만</option></select></label>
          <label class="field">기한<select id="myDays"><option value="7">1주</option><option value="30">1달</option><option value="0">계속</option></select></label></div>
        <button type="button" class="btn dark wide" id="myAdd">메모 넣기</button>
        <div class="hx-sub" style="margin-top:8px">제외 종목 <small>${V.block.length}</small></div>
        <p class="tiny">여기 넣은 종목은 추천 후보에서 빼고, AI가 골라도 자동매매가 사지 않아요(모의·실전 모두).</p>
        ${V.block.map((b) => `<div class="my-row"><span><b>${esc(b.name)}</b><small>${b.mkt === "kr" ? "국내" : "미국"} · ${esc(b.sym)}${b.why ? " · " + esc(b.why) : ""}</small></span>
          <button type="button" class="my-x" data-db="${b.mkt}|${esc(b.sym)}" aria-label="${esc(b.name)} 제외 풀기">풀기</button></div>`).join("") || `<div class="empty small">제외한 종목이 없어요</div>`}
        <div class="fields"><label class="field">종목 이름·코드<input id="myBq" placeholder="예: 삼성전자, 005930, TSLA" autocomplete="off" autocapitalize="off"></label>
          <label class="field">시장<select id="myBm"><option value="kr" ${mk === "kr" ? "selected" : ""}>국내</option><option value="us" ${mk === "us" ? "selected" : ""}>미국</option></select></label></div>
        <button type="button" class="btn wide" id="myBadd">제외 종목에 넣기</button></div>`;
    const mount = () => {
      const again = (v) => { V = v; $("#sheetBody").innerHTML = draw(); mount(); };
      $("#myAdd").onclick = async () => { try { again(await api("POST", "/api/myview/note", { text: $("#myText").value, mkt: $("#myMkt").value, days: Number($("#myDays").value) })); toast("메모를 넣었어요. 다음 추천부터 읽어요"); } catch (e) { toast(e.message, true); } };
      $("#myBadd").onclick = async () => { try { again(await api("POST", "/api/myview/block", { q: $("#myBq").value, mkt: $("#myBm").value })); toast("제외 종목에 넣었어요"); } catch (e) { toast(e.message, true); } };
      $("#sheetBody").onclick = async (e) => {
        const dn = e.target.closest("[data-dn]"), db = e.target.closest("[data-db]");
        try {
          if (dn) again(await api("DELETE", `/api/myview/note/${dn.dataset.dn}`));
          else if (db) { const [m, s] = db.dataset.db.split("|"); again(await api("DELETE", `/api/myview/block/${m}/${encodeURIComponent(s)}`)); }
        } catch (err) { toast(err.message, true); }
      };
    };
    openSheet("내 의견", draw(), mount);
  },
  apDraw() {
    const A = this.ap, el = $("#apOut");
    if (!el) return;
    if (!el.dataset.myBound) {
      el.dataset.myBound = "1";
      el.addEventListener("click", async (e) => {
        const v = e.target.closest("[data-vote]"), bk = e.target.closest("[data-blk]"), R = this.ap.res;
        if (v && R && R.id) {
          const cur = (A.votes || (A.votes = {}))[v.dataset.sym], want = cur === Number(v.dataset.vote) ? 0 : Number(v.dataset.vote);
          try { await api("POST", "/api/ai/vote", { pick_id: R.id, sym: v.dataset.sym, vote: want }); A.votes[v.dataset.sym] = want; A.shown = true; this.apDraw(); toast(want ? "내 판단을 적어 뒀어요. 결과가 나오면 성적표에서 비교해요" : "내 판단을 지웠어요"); }
          catch (err) { toast(err.message, true); }
        } else if (bk) {
          if (!confirmTap(bk, "한 번 더 누르면 제외 종목에 넣어요")) return;
          try { await api("POST", "/api/myview/block", { q: bk.dataset.blk, mkt: (R && R.mkt) || A.m, why: "추천에서 뺌" }); toast(`${bk.dataset.name}을(를) 제외 종목에 넣었어요`); bk.disabled = true; }
          catch (err) { toast(err.message, true); }
        }
      });
    }
    const body = A.text.split("```")[0];
    const R = A.res, us = (R ? R.mkt : A.m) === "us";
    const P = (n) => n == null ? "-" : us ? usd(n) : won(Math.round(n)) + "원";
    const cash = (() => { const h = memo.get("ihome"); return h && h.account ? (us ? (h.account.us || {}).usd : h.account.orderable) : null; })();
    el.innerHTML = (A.busy || A.text || R || A.err || A.bk) ? `<section class="card ap-res">
      ${A.busy ? `<div class="ai-status"><i class="ai-dot"></i><span>${esc(A.status || "")}</span></div>` : ""}
      ${R && R.stance ? `<div class="ap-stance s-${esc(R.stance)}"><b>${esc(R.stance)}</b><span>${esc(R.view || "")}</span></div>` : ""}
      ${R && R.note ? `<div class="notice">${esc(R.note)}</div>` : ""}
      ${R && R.horizon && R.horizon !== "day" ? `<div class="ap-kind"><span class="tag v">${esc(R.label)} 추천</span>${R.theme ? `<span class="tag">테마: ${esc(R.theme)}</span>` : ""}<span class="tiny">몇 분 안에 끝나는 단타가 아니라, 약 ${AI_H[R.horizon].days}거래일 들고 가는 기준이에요</span></div>` : ""}
      ${R && R.stance === "관망" && R.picks.length && !R.note ? `<div class="notice">AI는 지금 관망을 권해요. 그래도 가장 나은 종목을 비중을 줄여 골라 뒀어요. 들어간다면 작게${R.horizon && R.horizon !== "day" ? "" : ", 짧게"}.</div>` : ""}
      ${body ? `<div class="ai-ans ap-text">${mdLite(body)}</div>` : ""}
      ${A.err ? `<div class="notice warn">${esc(A.err)}</div>` : ""}
      ${A.src.length ? `<details class="ap-src"><summary class="tiny">찾아본 뉴스·자료 ${A.src.length}건</summary>${A.src.map((x) => `<a href="${safeUrl(x.url)}" target="_blank" rel="noopener">${esc(x.title)}</a>`).join("")}</details>` : ""}
      ${A.used ? meterHTML(A.meter || { fx: A.used.fx, credit: A.used.credit }, A.used) : ""}
    </section>
    ${A.bk ? this.basketHtml(A.bk) : ""}
    ${R ? (R.picks.length ? R.picks.map((p, i) => {
      const fresh = A.shown ? "" : "fresh";
      const ev = (p.win_prob / 100 * p.target_pct - (1 - p.win_prob / 100) * p.stop_pct).toFixed(2);
      const px = p.entry_type === "limit" && p.entry_price ? p.entry_price : p.price;
      const qty = cash && px ? Math.max(1, Math.floor(cash * (p.weight_pct / 100) / px)) : 1;
      const sw = !!(p.horizon && p.horizon !== "day"), dl = p.daily || {};
      const dchip = (l, v, suf = "%", sign = true) => v == null ? "" : `<span>${l} <b class="${sign ? tone(v) : ""}">${sign && v > 0 ? "+" : ""}${v}${suf}</b></span>`;
      return `<section class="card ap-pick ${fresh}" style="--d:${i * 90}ms">
        <div class="ap-ph"><span class="ap-rank">${i + 1}</span>
          <a class="ap-pn" href="#invest/stock?m=${R.mkt}&s=${encodeURIComponent(p.sym)}"><b>${esc(p.name)}</b><small>${esc(p.sym)} · 지금 ${P(p.price)} <span class="${tone(p.change_pct)}">${p.change_pct == null ? "" : pct(p.change_pct, 2)}</span></small></a>
          ${winRing(p.win_prob)}</div>
        ${p.cal_prob != null ? `<div class="ap-cal"><b>보정 승률 ${p.cal_prob}%</b><span>AI는 ${p.win_prob}%라고 했지만, 비슷하게 말한 지난 추천 ${p.cal_n}건의 실제 결과로 맞춘 값이에요</span></div>` : ""}
        ${p.auto ? `<div class="tiny ap-auto">지표 자동 선정 · AI가 종목을 주지 않아 앱이 ${sw ? "일봉" : "실시간"} 지표로 고른 후보예요</div>` : ""}
        ${sw ? `<div class="ap-cat"><div class="ap-ct"><span class="tag c-${esc(p.catalyst_type)}">${esc(p.catalyst_type)}</span>${p.theme ? `<span class="tag">${esc(p.theme)}</span>` : ""}${p.in_cand ? "" : `<span class="tag y">뉴스에서 찾음</span>`}</div>
          ${p.catalyst ? `<b>${esc(p.catalyst)}</b>` : ""}${p.catalyst_date ? `<small>확인 시기: ${esc(p.catalyst_date)}</small>` : ""}</div>
          ${p.daily ? `<div class="ap-daily">${dchip("5일", dl["5일%"])}${dchip("20일", dl["20일%"])}${dchip("20일선", dl["20일선대비%"])}${dchip("고점 대비", dl["60일고점대비%"])}${dchip("거래량", dl["5일거래량배수"], "배", false)}${dchip("하루 변동", dl["하루변동폭%"], "%", false)}</div>` : ""}` : ""}
        ${R.id && !p.auto ? `<div class="ap-vote" role="group" aria-label="${esc(p.name)} 추천에 대한 내 판단"><span>내 판단</span>
          <button type="button" data-vote="1" data-sym="${esc(p.sym)}" aria-pressed="${(A.votes || {})[p.sym] === 1}">동의</button>
          <button type="button" data-vote="-1" data-sym="${esc(p.sym)}" aria-pressed="${(A.votes || {})[p.sym] === -1}">반대</button>
          <button type="button" class="blk" data-blk="${esc(p.sym)}" data-name="${esc(p.name)}">이 종목 빼기</button></div>` : ""}
        <div class="ap-bar"><i class="sl" style="width:${(p.stop_pct / (p.stop_pct + p.target_pct) * 100).toFixed(1)}%"></i><i class="tp"></i></div>
        <div class="ap-lv">
          <div><span>손절</span><b class="down">−${p.stop_pct}%</b><small>${P(px * (1 - p.stop_pct / 100))}</small></div>
          <div><span>${p.entry_type === "limit" ? "지정가 매수" : "시장가 매수"}</span><b>${P(px)}</b><small>기대값 ${ev > 0 ? "+" : ""}${ev}%</small></div>
          <div><span>익절</span><b class="up">+${p.target_pct}%</b><small>${P(px * (1 + p.target_pct / 100))}</small></div></div>
        <div class="ap-meta">${p.trail_pct ? `<span>추적 익절 ${p.trail_pct}%</span>` : ""}<span>${sw ? `약 ${p.hold_days}거래일 보유 · ${esc((p.end_date || "").slice(5).replace("-", "/"))} 장 마감 전까지 안 닿으면 정리` : us ? "정규장 마감 8분 전 정리" : `${esc(p.exit_by || "15:12")}까지 안 닿으면 정리`}</span><span>권장 비중 ${p.weight_pct}%${cash ? ` · 약 ${won(qty)}주` : ""}</span><span>점수 ${p.score}</span></div>
        <ul class="ap-why">${p.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}${p.risks.map((r) => `<li class="risk">${esc(r)}</li>`).join("")}</ul>
        <button type="button" class="btn red wide" data-apbuy="${i}">매수하고 자동 익절·손절 걸기</button>
      </section>`;
    }).join("") : `<section class="card"><div class="empty">추천할 종목을 만들지 못했어요. ${esc(R.note || "")} 잠시 뒤 다시 받아 보세요.</div></section>`) : ""}` : "";
    if (R && R.picks.length && !A.shown) setTimeout(() => { A.shown = true; }, 50);
    $$("[data-apbuy]", el).forEach((b) => b.onclick = () => {
      const p = R.picks[Number(b.dataset.apbuy)], px = p.entry_type === "limit" && p.entry_price ? p.entry_price : p.price;
      const qty = cash && px ? Math.max(1, Math.floor(cash * (p.weight_pct / 100) / px)) : 1;
      orderSheet({ code: p.sym, market: R.mkt, side: "buy", name: p.name, exch: p.exch, qty,
        price: p.entry_type === "limit" ? (us ? px : Math.round(px / krTick(px)) * krTick(px)) : null,
        guard: p.horizon && p.horizon !== "day"
          ? { target_pct: p.target_pct, stop_pct: p.stop_pct, trail_pct: p.trail_pct, exit_by: "", pick_id: R.id, horizon: p.horizon, hold_days: p.hold_days, end_date: p.end_date }
          : { target_pct: p.target_pct, stop_pct: p.stop_pct, trail_pct: p.trail_pct, exit_by: R.mkt === "kr" ? (p.exit_by || "15:12") : "", pick_id: R.id },
        after: () => this.apGuards() });
    });
  },
  async apGuards() {
    const el = $("#apGuards");
    if (!el) return;
    let G;
    try { G = await GET("/api/ai/guards"); } catch (e) { return; }
    if (!$("#apGuards")) return;
    const on = G.filter((g) => g.status === "watching"), past = G.filter((g) => g.status !== "watching").slice(0, 6);
    const P = (g, n) => n == null ? "-" : g.mkt === "us" ? usd(n) : won(Math.round(n));
    const row = (g) => {
      const lo = g.sl_price, hi = g.tp_price, cur = g.status === "done" ? g.exit_price : g.last;
      const x = cur && hi > lo ? Math.min(100, Math.max(0, (cur - lo) / (hi - lo) * 100)) : null, base = (g.base - lo) / (hi - lo) * 100;
      const st = { watching: g.msg || "감시 중", done: `${g.exit_reason} · ${P(g, g.exit_price)}`, cancelled: "감시 끔", expired: g.msg }[g.status] || g.status;
      return `<div class="ap-g ${g.status}">
        <div class="ap-gh"><a href="#invest/stock?m=${g.mkt}&s=${encodeURIComponent(g.sym)}"><b>${esc(g.name)}</b>${g.horizon && g.horizon !== "day" ? ` <span class="tag v">${AI_H[g.horizon].label}</span>` : ""} <small>${won(g.qty)}주 · 매수 ${P(g, g.base)}</small></a>
          <span class="${tone(g.pnl_pct)}" style="font-weight:800">${g.pnl_pct == null ? "" : pct(g.pnl_pct, 2)}</span></div>
        <div class="ap-gbar"><i class="b" style="left:${base.toFixed(1)}%"></i>${x == null ? "" : `<i class="c ${tone(g.pnl_pct)}" style="left:${x.toFixed(1)}%"></i>`}</div>
        <div class="ap-gl"><span class="down">손절 ${P(g, lo)}</span><span class="tiny">${esc(st)}${g.trail_pct ? ` · 추적 ${g.trail_pct}%` : ""}${g.exit_by ? ` · ${esc(g.exit_by)} 정리` : ""}${g.exit_date ? ` · ${esc(g.exit_date.slice(5).replace("-", "/"))}까지 보유` : ""}</span><span class="up">익절 ${P(g, hi)}</span></div>
        ${g.status === "watching" ? `<button type="button" class="btn sm soft" data-gcancel="${g.id}">감시 끄기</button>` : ""}</div>`;
    };
    el.innerHTML = G.length ? `<div class="sec-title">자동 익절·손절 ${on.length ? `· 감시 중 ${on.length}` : ""}</div>
      <section class="card">${on.length ? on.map(row).join("") : `<div class="empty small">감시 중인 종목이 없어요</div>`}
      ${past.length ? `<details class="ap-past"><summary class="tiny">지난 기록 ${past.length}</summary>${past.map(row).join("")}</details>` : ""}</section>` : "";
    el.onclick = async (e) => {
      const b = e.target.closest("[data-gcancel]");
      if (!b) return;
      if (!confirmTap(b, "한 번 더 누르면 감시를 꺼요(보유는 그대로)")) return;
      await run(() => api("DELETE", `/api/ai/guards/${b.dataset.gcancel}`), "자동 익절·손절을 껐어요");
      this.apGuards();
    };
  },
  async apHistory() {
    const el = $("#apHist");
    if (!el) return;
    const H = await GET("/api/ai/picks").catch(() => []);
    if (!$("#apHist") || !H.length) return;
    el.innerHTML = `<details class="card ap-hist"><summary><b>지난 추천</b> <span class="tiny">${H.length}회 · 비용 합계 약 ${won(Math.round(H.reduce((a, x) => a + (x.usd || 0), 0) * ((this.ap.est || {}).fx || 1400)))}원</span></summary>
      <div class="list">${H.map((h) => `<button type="button" class="item" data-pick="${h.id}"><span class="main"><b>${esc(h.ts.slice(5, 16).replace("T", " "))} · ${AI_H[h.horizon || "day"].label} · ${h.mkt === "us" ? "미국" : "국내"} · ${esc(h.stance || "-")}</b>
        <span class="sub">${h.picks.length ? h.picks.map((p) => `${esc(p.name)}(${p.win_prob}%·+${p.target_pct}/−${p.stop_pct})`).join(", ") : "관망"}</span></span>
        <span class="end tiny">${esc(h.model.replace("claude-", ""))} ›</span></button>`).join("")}</div></details>`;
    el.onclick = (e) => { const b = e.target.closest("[data-pick]"); if (b) pickSheet(b.dataset.pick); };
  },

  async apScore() {
    const el = $("#apScore");
    if (!el) return;
    let st;
    const hz = this.ap.h, dd = hz === "day" ? 30 : 90, sw = hz !== "day";
    (this.sf || (this.sf = { days: dd, mkt: "", model: "", h: hz })).h = hz; this.sf.days = dd;  // 성적 화면도 같은 종류로 열리게
    try { st = await GET(`/api/ai/stats?days=${dd}&h=${hz}`); } catch (e) { el.innerHTML = `<div class="tiny">${esc(e.message)}</div>`; return; }
    if (!$("#apScore") || this.ap.h !== hz) return;
    el.onclick = (e) => { const g = e.target.closest("[data-go]"); if (g) { e.preventDefault(); location.hash = g.dataset.go; } };
    const a = st.all;
    el.innerHTML = a.n ? `<div class="card-head"><h2>AI ${AI_H[hz].label} 추천 성적 · 최근 ${dd}일</h2><span class="link">자세히 ›</span></div>
      <div class="sc-kpi">
        <div><span>추천 1건당 평균</span><b class="${tone(a.avg)}">${a.avg == null ? "-" : pct(a.avg, 2)}</b></div>
        <div><span>익절 도달률</span><b>${a.tp_rate == null ? "-" : a.tp_rate + "%"}</b></div>
        <div><span>AI 예측 승률</span><b>${a.pred == null ? "-" : a.pred + "%"}</b></div></div>
      ${resultBar(a, sw ? "기간 만료" : "시간 정리")}
      <div class="sc-recent">${st.rows.slice(0, 4).map((r) => `<span class="sc-r" data-go="#invest/stock?m=${r.mkt}&s=${encodeURIComponent(r.sym)}&pk=${r.pick_id}${sw ? "&ph=" + hz : ""}"><i class="as-badge r-${esc(r.result || "진행 중")}">${esc(sw && r.result === "시간 정리" ? "기간 만료" : r.result || "진행 중")}</i><b>${esc(r.name || r.sym)}</b><em class="${tone(r.net_pct)}">${r.net_pct == null ? "" : pct(r.net_pct, 1)}</em></span>`).join("")}</div>
      <div class="tiny">추천 ${a.n}건 · 채점 ${a.done} · 진행 중 ${a.live} · 종목을 누르면 차트에서 추적, 카드를 누르면 전체 성적</div>`
      : `<div class="card-head"><h2>AI ${AI_H[hz].label} 추천 성적</h2></div><div class="tiny">${sw ? `추천을 받으면, 사지 않아도 그 뒤 ${AI_H[hz].days}거래일 동안 일봉으로 익절·손절·기간 만료 결과를 매기고, 지수와 견준 성적까지 여기 모아요.` : "추천을 받으면, 사지 않아도 그 뒤 실제 가격으로 익절·손절·시간 정리 결과를 매겨 여기 모아 보여 드려요."}</div>`;
  },
  async asLearn() {
    const el = $("#asLearn");
    if (!el) return;
    const hz = (this.sf || {}).h || "day", HL = AI_H[hz].label;
    let L;
    try { L = await GET(`/api/ai/learn?h=${hz}`); } catch (e) { el.innerHTML = `<div class="tiny">${esc(e.message)}</div>`; return; }
    if (!$("#asLearn") || ((this.sf || {}).h || "day") !== hz) return;
    const step = L.n < L.min ? 0 : L.lessons.text ? 2 : 1, ls = L.lessons || {}, T = L.tune || {};
    const arrow = (a, b) => a === b ? `<b>${b}%</b>` : `<span>${a}%</span><i aria-hidden="true">→</i><b>${b}%</b>`;
    el.innerHTML = `<div class="card-head"><h2>자체 학습 · ${HL} 추천</h2><span class="tag ${step ? "g" : "y"}">${esc(L.stage)}</span></div>
      <div class="lr-steps">${[["자료 쌓기", `채점 ${L.n}/${L.min}건${L.live ? ` · 진행 ${L.live}` : ""}`], ["통계·자동 조정", "추천할 때마다 자동"], ["AI 복기 규칙", ls.at && ls.text ? esc(ls.at.slice(5).replace("T", " ")) + ` · ${ls.ver}판` : "아직 없음"]].map(([a, b], i) => `<div class="${i <= step ? "on" : ""}"><i>${i + 1}</i><b>${a}</b><span>${b}</span></div>`).join("")}</div>
      <div class="tiny">추천 뒤 실제 가격을 추적한 결과를 다음 ${HL} 추천 때 AI에게 함께 보여 줘요. 잘 통한 조건은 더 고르고, 안 통한 조건은 피하고, 승률·목표·손절${hz === "day" ? "" : "·보유일"}을 실제 결과에 맞추게 돼요. 당일·1주·1달은 서로 섞지 않고 따로 배워요. 자동매매의 빠른 재추천(싼 엔진, 뉴스 없이)은 배우는 자료에 넣지 않아요.</div>
      ${L.n < L.min ? `<div class="bar"><div style="width:${L.n / L.min * 100}%"></div></div><div class="tiny">채점이 ${L.min}건 쌓이면 학습 내용이 추천에 들어가기 시작해요. 추천을 받아 두기만 해도(사지 않아도) 쌓여요.${hz === "day" ? "" : ` ${HL} 추천은 결과가 나오기까지 최대 ${AI_H[hz].days}거래일이 걸려요.`}</div>` : `
      ${T.ready ? `<h3 class="lr-h">자동 조정값</h3>
        <div class="lr-tune"><div><span>목표 폭</span><p>${arrow(T.now_tp, T.tp)}</p><small>절반은 실제로 닿았던 상승폭</small></div>
          <div><span>손절 폭</span><p>${arrow(T.now_sl, T.sl)}</p><small>이긴 추천이 견딘 하락폭</small></div>
          ${T.hold ? `<div><span>보유일</span><p><b>${T.hold}거래일</b></p><small>이때 평균 ${pct(T.hold_avg, 2)}</small></div>` : ""}</div>
        ${T.prefer.length ? `<ul class="lr-ins good">${T.prefer.map((x) => `<li><b>우선</b> ${esc(x)}</li>`).join("")}</ul>` : ""}
        ${T.avoid.length ? `<ul class="lr-ins bad">${T.avoid.map((x) => `<li><b>피함</b> ${esc(x)}</li>`).join("")}</ul>` : ""}
        <div class="tiny">왼쪽은 지금까지 AI가 잡은 평균, 오른쪽은 실제 결과에서 계산한 값이에요. 다음 추천부터 이 값을 기준으로 잡게 하고, 같은 조건의 후보에는 가산점을 줘요.</div>` : ""}
      <h3 class="lr-h">지금 추천에 반영 중인 통계</h3>
      ${L.insights.length ? `<ul class="lr-ins">${L.insights.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : `<div class="tiny">아직 뚜렷한 차이가 나는 조건이 없어요. 조건별 성적표는 그대로 AI에게 전달돼요.</div>`}
      ${L.bands.length ? `<h3 class="lr-h">승률 보정</h3><div class="lr-cal">${L.bands.map((b) => `<div><span>AI ${esc(b.band)}</span><div class="lr-cb"><i class="p" style="width:${b.pred}%"></i><i class="a" style="width:${b.actual}%"></i></div><b>실제 ${b.actual}%</b><small>${b.n}건</small></div>`).join("")}</div>
        <div class="tiny">옅은 막대는 AI가 말한 승률, 진한 막대는 실제 익절률이에요. 새 추천 카드에는 이걸로 맞춘 '보정 승률'이 함께 나와요.</div>` : ""}
      <h3 class="lr-h">AI 복기 규칙${ls.text ? ` · ${ls.ver}판` : ""}</h3>
      ${ls.text ? `<div class="ai-ans lr-rules">${mdLite(ls.text)}</div><div class="tiny">${esc((ls.model || "").replace("claude-", ""))} · 채점 ${ls.n}건 기준 · 새 채점 ${L.next_in}건 더 쌓이면 다시 복기</div>`
        : `<div class="tiny">Claude가 성적표 전체를 읽고 '추천 규칙'을 써 두면, 그 뒤 추천마다 그 규칙을 따라요.</div>`}
      ${L.by_ver.length > 1 ? `<div class="lr-ver">${L.by_ver.map((v) => `<div><span>${v.ver ? `규칙 ${v.ver}판` : "규칙 없음"}</span><b class="${tone(v.avg)}">${pct(v.avg, 2)}</b><small>${v.n}건 · 이익 ${v.win}%</small></div>`).join("")}</div>
        <div class="tiny">규칙을 고칠 때마다 판이 올라가고, 그 판을 읽고 낸 추천의 성적을 따로 재요. 규칙이 실제로 도움이 됐는지 여기서 보여요.</div>` : ""}
      <div class="lr-act"><button type="button" class="btn ai-btn" id="lrRun" data-ai-x="learn"><span class="ai-spark" aria-hidden="true">✦</span>지금 복기해서 규칙 ${ls.text ? "갱신" : "만들기"}</button>
        ${ls.text ? `<button type="button" class="btn sm soft" id="lrClear">규칙 지우기</button>` : ""}</div>
      <div class="lr-auto"><span><b>자동 복기</b><small>새 채점이 ${L.step}건 쌓일 때마다(하루 한 번까지) 알아서 복기해요. 한 번에 수백 원쯤 들어요.</small></span>
        <button class="switch" role="switch" id="lrAuto" aria-checked="${L.auto}" aria-label="자동 복기"><span></span></button></div>`}`;
  },
  async aiStat() {
    setTop("AI 추천 성적", { back: "#invest/ai", menu: "invest", sub: "aistat" });
    const F = this.sf || (this.sf = { days: 30, mkt: "", model: "", kind: "", h: (this.ap || {}).h || "day" });
    if (!AI_H[F.h]) F.h = "day";
    view.innerHTML = `<div id="asRoot" class="as"><div class="empty loading">채점하는 중</div></div>`;
    const load = async (refresh) => {
      const st = await GET(`/api/ai/stats?h=${F.h}&days=${F.days}&mkt=${F.mkt}&model=${encodeURIComponent(F.model)}&kind=${F.kind || ""}${refresh ? "&refresh=1" : ""}`);
      if (!$("#asRoot")) return;
      this.asDraw(st, F, load);
    };
    try { await load(true); } catch (e) { $("#asRoot").innerHTML = `<div class="notice warn">${esc(e.message)}</div>`; }
  },
  asDraw(st, F, load) {
    const a = st.all, M = st.models, mn = (k) => M[k] || String(k).replace("claude-", "");
    const won$ = (u) => `${won(Math.round((u || 0) * st.fx))}원`;
    const chips = (name, opts, cur) => `<div class="chips as-f" data-f="${name}">${opts.map(([v, l]) => `<button type="button" class="chip" data-v="${esc(v)}" aria-pressed="${String(v) === String(cur)}">${esc(l)}</button>`).join("")}</div>`;
    const row = (x, label) => `<tr><th>${esc(label)}</th><td>${x.n}</td><td>${x.tp_rate == null ? "-" : x.tp_rate + "%"}</td><td>${x.win_rate == null ? "-" : x.win_rate + "%"}</td>
      <td class="${tone(x.avg)}"><b>${x.avg == null ? "-" : pct(x.avg, 2)}</b></td><td>${x.pf == null ? "-" : x.pf}</td></tr>`;
    const table = (title, list, lab, extra = "") => list.length ? `<section class="card"><h2>${title}</h2>${extra}
      <div class="as-tw"><table class="as-t"><thead><tr><th></th><th>건수</th><th>익절률</th><th>이익률</th><th>평균</th><th>손익비</th></tr></thead>
      <tbody>${list.map((x) => row(x, lab(x.key))).join("")}</tbody></table></div></section>` : "";
    const maxH = Math.max(1, ...st.hist);
    const hl = st.hist_labels || ["−3%↓", "−3~−2", "−2~−1", "−1~0", "0~1", "1~2", "2~3", "3%↑"];
    const sw = st.horizon !== "day", HL = AI_H[st.horizon].label, endName = sw ? "기간 만료" : "시간 정리";
    const rname = (r) => (r === "시간 정리" && sw ? "기간 만료" : r || "진행 중");
    const bestPath = sw && st.path && st.path.length ? st.path.slice().sort((x, y) => y.avg - x.avg)[0] : null;
    const insight = [];
    if (a.done >= 3) {
      if (a.pred != null && a.tp_rate != null) insight.push(a.tp_rate >= a.pred ? `AI가 말한 승률(${a.pred}%)보다 실제 익절률(${a.tp_rate}%)이 높아요. AI가 보수적으로 말하는 편이에요.` : `AI 예측 승률(${a.pred}%)보다 실제 익절률(${a.tp_rate}%)이 낮아요. 화면의 승률을 그만큼 낮춰 보세요.`);
      if (a.mfe != null) insight.push(`추천 뒤 평균 최대 ${pct(a.mfe, 2)}까지 올랐고 ${pct(a.mae, 2)}까지 내렸어요.`);
      const best = st.by_model.filter((x) => x.done >= 3 && x.avg != null).sort((x, y) => y.avg - x.avg)[0];
      if (best && st.by_model.length > 1) insight.push(`엔진 중에선 ${mn(best.key)}가 1건당 평균 ${pct(best.avg, 2)}로 가장 좋아요.`);
      const sb = st.by_bought.find((x) => x.key === "샀음"), nb = st.by_bought.find((x) => x.key === "안 삼");
      if (sb && nb && sb.avg != null && nb.avg != null) insight.push(`직접 산 종목 평균 ${pct(sb.avg, 2)}, 안 산 종목 평균 ${pct(nb.avg, 2)} — ${sb.avg >= nb.avg ? "고르는 눈이 AI보다 나아요" : "안 산 쪽이 더 좋았어요"}.`);
      if (sw && a.alpha != null) insight.push(`같은 기간 지수는 평균 ${pct(a.bench, 2)}, 추천 종목은 그보다 ${pct(a.alpha, 2)}p ${a.alpha >= 0 ? "더 벌었어요. 시장 덕이 아니라 종목 선택이 통한 거예요" : "덜 벌었어요. 지수를 사는 편이 나았어요"}.`);
      if (bestPath && st.path.length > 1) insight.push(`목표·손절 없이 그냥 들고 있었다면 ${bestPath.k}거래일째가 평균 ${pct(bestPath.avg, 2)}로 가장 좋았어요.`);
      const bc = sw ? (st.by_cat || []).filter((x) => x.done >= 2 && x.avg != null).sort((x, y) => y.avg - x.avg) : [];
      if (bc.length > 1) insight.push(`재료 유형은 '${bc[0].key}'가 평균 ${pct(bc[0].avg, 2)}로 가장 좋고, '${bc[bc.length - 1].key}'가 ${pct(bc[bc.length - 1].avg, 2)}로 가장 나빠요.`);
    } else insight.push("채점이 3건 이상 쌓이면 엔진·시장별 비교와 해석을 보여 드려요.");
    const W = 320, H = 110, cv = st.curve, cmin = Math.min(0, ...cv.map((x) => x.v)), cmax = Math.max(0, ...cv.map((x) => x.v)), cr = cmax - cmin || 1;
    const X = (i) => (cv.length < 2 ? W / 2 : i / (cv.length - 1) * W), Y = (v) => 8 + (1 - (v - cmin) / cr) * (H - 16);
    const line = cv.map((p, i) => `${i ? "L" : "M"}${X(i).toFixed(1)},${Y(p.v).toFixed(1)}`).join("");
    $("#asRoot").innerHTML = `
      <div class="as-hz" role="group" aria-label="추천 종류" data-f="h">${Object.entries(AI_H).map(([k, v]) => `<button type="button" data-v="${k}" aria-pressed="${k === F.h}"><b>${v.label} 추천</b><small>${v.sub}</small></button>`).join("")}</div>
      <div class="as-filters">
        ${chips("days", [[7, "7일"], [30, "30일"], [90, "90일"], [0, "전체"]], F.days)}
        ${chips("mkt", [["", "전체 시장"], ["kr", "국내"], ["us", "미국"]], F.mkt)}
        ${chips("model", [["", "모든 엔진"], ...Object.entries(M)], F.model)}
        ${chips("kind", [["", "모든 추천"], ["full", "정밀 추천"], ["lite", "빠른 재추천"]], F.kind || "")}
      </div>
      <section class="card as-hero">
        <div class="as-big"><span>추천 1건당 평균 수익(비용 뺀 뒤)</span><b class="${tone(a.avg)}">${a.avg == null ? "-" : pct(a.avg, 2)}</b>
          <small>전부 같은 금액으로 샀다면 누적 ${pct(a.sum, 2)} · 손익비 ${a.pf == null ? "-" : a.pf}</small></div>
        <div class="sc-kpi">
          <div><span>익절 도달률</span><b>${a.tp_rate == null ? "-" : a.tp_rate + "%"}</b></div>
          <div><span>이익으로 끝남</span><b>${a.win_rate == null ? "-" : a.win_rate + "%"}</b></div>
          <div><span>AI 예측 승률</span><b>${a.pred == null ? "-" : a.pred + "%"}</b></div>
          <div><span>최고 / 최저</span><b><em class="up">${a.best == null ? "-" : pct(a.best, 1)}</em> / <em class="down">${a.worst == null ? "-" : pct(a.worst, 1)}</em></b></div>
          ${sw ? `<div><span>지수보다(같은 기간)</span><b class="${tone(a.alpha)}">${a.alpha == null ? "-" : pct(a.alpha, 2) + "p"}</b></div>
          <div><span>결과까지 평균</span><b>${a.hold == null ? "-" : a.hold + "거래일"}</b></div>` : ""}</div>
        ${resultBar(a, endName)}
        <div class="tiny">${HL} 추천 ${a.n}건 · 채점 끝 ${a.done} · 진행 중 ${a.live}${sw ? "" : ` · 미체결 ${a.unfilled}`} · AI 비용 ${won$(a.cost_usd)}</div>
        ${a.alt_n ? `<div class="notice">성적은 <b>추천을 보고 바로 샀을 때</b> 기준이에요. AI가 말한 지정가를 기다렸다면: ${a.alt_n}건 중 미체결 ${a.alt_unfilled}건${a.alt_avg == null ? "" : ` · 체결된 것 평균 <b class="${tone(a.alt_avg)}">${pct(a.alt_avg, 2)}</b>`}</div>` : ""}
      </section>
      <div class="sec-title" id="asList">추천 하나하나 · ${st.rows.length}건 <span class="tiny" style="margin-left:auto;font-weight:600">누르면 차트에 추천 시점·익절·손절 선</span></div>
      <section class="card tight"><div class="list">${st.rows.length ? st.rows.map((r, ri) => `<a class="item as-r ${ri >= 8 ? "more" : ""}" href="#invest/stock?m=${r.mkt}&s=${encodeURIComponent(r.sym)}&pk=${r.pick_id}${sw ? "&ph=" + st.horizon : ""}">
        <span class="as-badge r-${esc(r.result || "진행 중")}">${esc(rname(r.result))}</span>
        <span class="main"><b>${esc(r.name || r.sym)} ${r.bought ? `<span class="tag g">샀음</span>` : ""}${sw && r.cat ? ` <span class="tag c-${esc(r.cat)}">${esc(r.cat)}</span>` : ""}</b>
          <span class="sub">${esc(r.ts.slice(5, 16).replace("T", " "))} · ${r.mkt === "us" ? "미국" : "국내"} · ${esc(mn(r.model))}${sw && r.theme ? ` · ${esc(r.theme)}` : ""} · 예측 ${r.win_prob ?? "-"}% · +${r.target_pct}/−${r.stop_pct}${r.mfe != null ? ` · 최대 ${pct(r.mfe, 1)}/${pct(r.mae, 1)}` : ""}</span></span>
        <span class="end"><b class="${tone(r.net_pct)}">${r.net_pct == null ? "-" : pct(r.net_pct, 2)}</b><span class="tiny">${sw ? (r.days_held ? `${r.days_held}거래일째${r.bench != null ? ` · 지수 ${pct(r.bench, 1)}` : ""}` : esc(r.src || "")) : r.alt_result ? `지정가였다면 <span class="${tone(r.alt_net)}">${esc(r.alt_result)}${r.alt_net == null ? "" : " " + pct(r.alt_net, 1)}</span>` : r.src ? esc(r.src) : ""}</span></span></a>`).join("") : `<div class="empty">아직 ${HL} 추천 기록이 없어요. AI 탭에서 '${HL}'을 고르고 추천을 받아 두면, 사지 않아도 여기에 쌓여요.</div>`}</div>${st.rows.length > 8 ? `<button type="button" class="btn sm soft as-more" id="asMore">나머지 ${st.rows.length - 8}건 더 보기</button>` : ""}</section>
      <section class="card as-learn" id="asLearn"><div class="empty small loading">자체 학습 상태 불러오는 중</div></section>
      <section class="card as-ins"><h2>한눈에 해석</h2><ul>${insight.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></section>
      ${st.by_mkt.length ? `<div class="as-duo">${["kr", "us"].map((k) => { const x = st.by_mkt.find((y) => y.key === k); return `<section class="card as-m ${k}">
        <h2>${k === "kr" ? "국내" : "미국"}</h2>${x ? `<b class="${tone(x.avg)}">${x.avg == null ? "-" : pct(x.avg, 2)}</b><span>${x.n}건 · 익절 ${x.tp_rate ?? "-"}%</span>
        <div class="as-mini">${resultBar(x, endName)}</div>` : `<span>아직 없어요</span>`}</section>`; }).join("")}</div>` : ""}
      ${st.by_model.length ? `<section class="card"><h2>엔진별</h2><div class="as-eng">${st.by_model.map((x) => `<div class="as-e">
        <div class="as-eh"><b>${esc(mn(x.key))}</b><span>${x.n}건 · 1건 비용 ${won$(x.cost_usd / Math.max(1, x.n))}</span></div>
        <div class="as-eb"><i class="${(x.avg || 0) >= 0 ? "pos" : "neg"}" style="width:${Math.min(100, Math.abs(x.avg || 0) * 25)}%"></i><b class="${tone(x.avg)}">${x.avg == null ? "-" : pct(x.avg, 2)}</b></div>
        <div class="tiny">익절 ${x.tp_rate ?? "-"}% · 이익 ${x.win_rate ?? "-"}% · 예측 ${x.pred ?? "-"}% · 손익비 ${x.pf ?? "-"}</div></div>`).join("")}</div></section>` : ""}
      <section class="card"><h2>누적 수익 흐름</h2>${cv.length >= 2 ? `<svg viewBox="0 0 ${W} ${H}" class="as-curve anim mk-svg"><line x1="0" x2="${W}" y1="${Y(0)}" y2="${Y(0)}" stroke="var(--line2)" stroke-dasharray="3 4"/>
        <path class="ln" pathLength="1" d="${line}" fill="none" stroke="${cv[cv.length - 1].v >= 0 ? "var(--up)" : "var(--down)"}" stroke-width="2.2" stroke-linejoin="round"/></svg>
        <div class="as-axis"><span>${esc(cv[0].t.slice(5))}</span><span>${pct(cv[cv.length - 1].v, 2)}</span><span>${esc(cv[cv.length - 1].t.slice(5))}</span></div>` : `<div class="empty small">채점이 2건 이상 쌓이면 그려요</div>`}</section>
      <section class="card"><h2>수익률 분포</h2><div class="as-hist">${st.hist.map((n, i) => `<div><em>${n || ""}</em><i class="${i < 4 ? "neg" : "pos"}" style="height:${n / maxH * 100}%"></i><span>${hl[i]}</span></div>`).join("")}</div></section>
      ${sw && st.path && st.path.length ? `<section class="card"><h2>그냥 들고 있었다면</h2>
        <div class="tiny">목표·손절을 걸지 않고 추천가에 사서 들고만 있었을 때, 며칠째에 얼마였는지예요(비용 뺀 뒤).</div>
        <div class="as-path">${st.path.map((x) => { const mx = Math.max(0.01, ...st.path.map((y) => Math.abs(y.avg))); return `<div class="${bestPath && x.k === bestPath.k ? "best" : ""}"><em class="${tone(x.avg)}">${pct(x.avg, 1)}</em><span class="bx"><i class="${x.avg >= 0 ? "pos" : "neg"}" style="height:${Math.abs(x.avg) / mx * 100}%"></i></span><b>${x.k}일째</b><small>${x.n}건 · 이익 ${x.win}%</small></div>`; }).join("")}</div></section>` : ""}
      ${sw ? table("재료 유형별", st.by_cat || [], (k) => k, `<div class="tiny">어떤 종류의 재료가 ${HL} 안에 실제로 통했는지예요.</div>`) : ""}
      ${sw ? table("테마별", st.by_theme || [], (k) => k) : ""}
      ${sw ? table("종목을 어디서 찾았나", st.by_cand || [], (k) => k, `<div class="tiny">'순위 후보 안'은 지금 돈이 몰리는 종목, 'AI가 뉴스에서 찾음'은 순위에 없던 종목이에요.</div>`) : ""}
      ${sw ? table("결과까지 걸린 기간별", st.by_hold || [], (k) => k) : ""}
      ${table("AI가 말한 승률 vs 실제", st.by_band, (k) => `예측 ${k}`, `<div class="tiny">예측이 높을수록 실제 익절률도 높아야 믿을 만한 승률이에요.</div>`)}
      ${table("추천 순위별", st.by_rank, (k) => k)}
      ${table("AI 태도별", st.by_stance, (k) => k)}
      ${table("추천 받은 시간대별", st.by_tod, (k) => k)}
      ${table("추천 받은 요일별", st.by_dow || [], (k) => k + "요일")}
      ${table("직접 산 것 vs 안 산 것", st.by_bought, (k) => k)}
      ${(st.by_kind || []).length > 1 ? table("정밀 추천 vs 빠른 재추천", st.by_kind, (k) => k, `<div class="tiny">빠른 재추천은 자동매매가 정밀 추천 사이에 가장 싼 엔진으로 뉴스 없이 다시 물은 것이에요. 자체 학습(통계·승률 보정·복기)에는 정밀 추천만 써요.</div>`) : ""}
      ${(st.by_lv || []).length > 1 ? table("자체 학습 규칙 판별", st.by_lv, (k) => k, `<div class="tiny">AI 복기 규칙을 고칠 때마다 판이 올라가요. 판이 올라갈수록 평균이 좋아지면 학습이 통하고 있는 거예요.</div>`) : ""}
      ${(st.by_feat || []).length ? `<section class="card"><h2>추천 순간의 조건별</h2><div class="tiny">추천할 때 그 종목이 어떤 상태였는지로 나눈 성적이에요(3건 이상 모인 조건만). 차이가 큰 조건은 자체 학습이 다음 추천에 반영해요.</div>
        ${st.by_feat.map((g) => `<details class="as-fg" ${g.items.length > 1 && Math.max(...g.items.map((x) => x.avg)) - Math.min(...g.items.map((x) => x.avg)) >= (sw ? 1.5 : 0.5) ? "open" : ""}><summary><b>${esc(g.group)}</b><span class="tiny">${g.items.map((x) => `${esc(x.key)} ${pct(x.avg, 1)}`).join(" · ")}</span></summary>
          <div class="as-fr">${g.items.slice().sort((x, y) => y.avg - x.avg).map((x) => `<div><span>${esc(x.key)}</span><small>${x.n}건 · 익절 ${x.tp_rate}%</small><b class="${tone(x.avg)}">${pct(x.avg, 2)}</b></div>`).join("")}</div></details>`).join("")}</section>` : ""}
      <div class="tiny" style="padding:0 3px 8px">${sw ? `채점 방법(${HL} 추천): 추천을 보고 그 순간 가격에 바로 샀다고 봐요. 추천 당일은 추천 뒤의 5분봉만, 그다음 날부터는 일봉으로 손절가·익절가 중 먼저 닿은 쪽을 가려요. 시가가 이미 손절가 아래(또는 익절가 위)에서 시작하면 그 시가에 팔린 것으로 쳐요(갭). 한 봉에 둘 다 닿으면 봉 모양으로 순서를 짐작해요. ${AI_H[st.horizon].days}거래일 동안 둘 다 안 닿으면 마지막 날 종가로 끝내요(기간 만료). 주말만 빼고 세므로 공휴일이 끼면 하루쯤 짧을 수 있어요. '지수보다'는 같은 기간 코스피·코스닥·S&P 500 수익률을 뺀 값이에요. 수익률은 왕복 비용(국내 0.2%, 미국 0.25%)을 뺀 값이에요.` : `채점 방법: 추천을 보고 그 순간 가격에 바로 샀다고 봐요(AI가 지정가를 말했어도 기다리지 않아요. 지정가를 기다렸을 때의 결과는 참고로 따로 보여 줘요). 추천 시각이 낀 5분봉은 추천 전 움직임이 섞여 있어 종가만 써요. 그 뒤 5분봉에서 손절가·익절가 중 먼저 닿은 쪽으로 판정해요. 한 봉 안에 둘 다 닿으면 봉 모양으로 순서를 짐작해요(음봉이면 익절, 양봉이면 손절). 둘 다 안 닿으면 정리 시각(국내 추천 정리 시각, 미국 정규장 마감) 가격으로 끝내요. 수익률은 왕복 비용(국내 0.2%, 미국 0.25%)을 뺀 값이에요.`}</div>`;
    this.asLearn();
    if (this.asOpen) $("#asRoot").classList.add("show-all");
    $("#asRoot").onclick = async (e) => {
      if (e.target.closest("#asMore")) { this.asOpen = true; $("#asRoot").classList.add("show-all"); return; }
      const hb = e.target.closest(".as-hz [data-v]");
      if (hb) {
        if (hb.dataset.v === F.h) return;
        F.h = hb.dataset.v; F.days = F.h === "day" ? 30 : F.h === "swing" ? 90 : 0; this.asOpen = false;
        if (this.ap) { this.ap.h = F.h; store.set("apH", F.h === "day" ? "" : F.h); }
        $("#asRoot").innerHTML = `<div class="empty loading">채점하는 중</div>`;
        try { await load(true); } catch (err) { $("#asRoot").innerHTML = `<div class="notice warn">${esc(err.message)}</div>`; }
        return;
      }
      if (e.target.closest("#lrRun")) { aiSheet("learn", { horizon: F.h }); sheet.addEventListener("close", () => this.asLearn(), { once: true }); return; }
      if (e.target.closest("#lrAuto")) { const b = e.target.closest("#lrAuto"), on = b.getAttribute("aria-checked") !== "true"; await run(() => api("PUT", "/api/ai/learn", { auto: on }), on ? "자동 복기를 켰어요" : "자동 복기를 껐어요"); return this.asLearn(); }
      if (e.target.closest("#lrClear")) { if (!confirmTap(e.target.closest("#lrClear"), "한 번 더 누르면 규칙을 지워요")) return; await run(() => api("PUT", "/api/ai/learn", { clear: true, h: F.h }), "규칙을 지웠어요"); return this.asLearn(); }
      const pk = e.target.closest("[data-pick]");
      if (pk) { e.preventDefault(); return pickSheet(pk.dataset.pick); }
      const c = e.target.closest(".as-f [data-v]");
      if (!c) return;
      const f = c.closest("[data-f]").dataset.f;
      F[f] = f === "days" ? Number(c.dataset.v) : c.dataset.v;
      $$(`[data-f="${f}"] .chip`).forEach((x) => x.setAttribute("aria-pressed", String(x === c)));
      try { await load(false); } catch (err) { toast(err.message); }
    };
  },

  // ---------- 발견: 실시간 순위 ----------
  async discover(box) {
    const S = this.disc || (this.disc = { m: "kr", kind: "value" });   // 배당 화면(this.dv)과 상태를 나눠 쓴다
    if (S.m === "kr" && ["views", "watch", "buy"].includes(S.kind)) S.kind = "value";
    if (!$("#dvList", box)) {
      const am = curMkt(S.m);
      if (am !== S.m) { S.m = am; S.kind = am === "us" ? "views" : (["views", "watch", "buy"].includes(S.kind) ? "value" : S.kind); }
      $(".fab")?.remove();
      box.innerHTML = `<div class="dv">
        ${seg("시장", [["kr", "국내"], ["us", "미국"]], S.m, "dv-mkt")}
        <div class="chips dv-kind" id="dvChips" role="group" aria-label="순위 종류">${(S.m === "us" ? [["views", "조회 급상승"], ["value", "거래대금"], ["up", "급상승"], ["down", "급하락"], ["volume", "거래량"], ["watch", "관심 등록"], ["buy", "키움 매수"]] : [["value", "거래대금"], ["up", "급상승"], ["down", "급하락"], ["volume", "거래량"]]).map(([k, v]) => `<button type="button" class="chip" data-kind="${k}" aria-pressed="${k === S.kind}">${v}</button>`).join("")}</div>
        <div class="ss-row" id="dvSess"></div>
        <section class="card tight"><div class="dv-kwrap" id="dvKind"></div><div class="list" id="dvList"><div class="empty loading">불러오는 중</div></div></section>
        <div class="tiny" id="dvFoot" style="padding:0 3px"></div></div>`;
      $("#dvKind").append($("#dvChips"));
      bindSeg(box, "시장", (v) => { setMkt(v); S.m = v; S.kind = v === "us" ? "views" : (["views", "watch", "buy"].includes(S.kind) ? "value" : S.kind); box.innerHTML = ""; this.discover(box); });
      box.onclick = async (e) => {
        const st = e.target.closest("[data-star]");
        if (st) {
          e.preventDefault();
          const W = this.dvW[S.m] || (this.dvW[S.m] = new Set()), sym = st.dataset.star, on = !W.has(sym);
          await run(() => api("POST", "/api/market/watch", { mkt: S.m, sym, on }), on ? "관심종목에 넣었어요" : "관심종목에서 뺐어요");
          on ? W.add(sym) : W.delete(sym); st.setAttribute("aria-pressed", String(on)); delete mkt.snap[S.m];
          return;
        }
        const k = e.target.closest("[data-kind]"); if (k) { S.kind = k.dataset.kind; $$("[data-kind]", box).forEach((x) => x.setAttribute("aria-pressed", String(x === k))); $("#dvList").innerHTML = `<div class="empty loading">불러오는 중</div>`; this.discover(box); } };
      const c = memo.get(`rk.${S.m}.${S.kind}`); if (c) this.dvRender(c);
      if (!this.dvW[S.m]) GET(`/api/market/overview?mkt=${S.m}`).then((o) => { this.dvW[S.m] = new Set(o.items.map((x) => x.sym)); const c2 = memo.get(`rk.${S.m}.${S.kind}`); if (c2 && $("#dvList")) this.dvRender(c2); }).catch(() => {});
    }
    try {
      const r = await GET(`/api/invest/rank?mkt=${S.m}&kind=${S.kind}`);
      memo.set(`rk.${S.m}.${S.kind}`, r);
      if (investState.tab === "discover" && S.m === r.mkt && S.kind === r.kind) this.dvRender(r);
    } catch (e) { if ($("#dvList")) $("#dvList").innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
  },
  dvW: {},
  dvRender(r) {
    const us = r.mkt === "us", W = this.dvW[r.mkt] || new Set();
    const mv = (x) => x.new ? `<em class="dv-mv new">NEW</em>` : x.move ? `<em class="dv-mv ${x.move > 0 ? "up" : "down"}">${x.move > 0 ? "▲" : "▼"}${Math.abs(x.move)}</em>` : "";
    const sub = (x) => x.value ? ` · 거래대금 ${us ? "$" + (x.value >= 1e9 ? (x.value / 1e9).toFixed(1) + "B" : Math.round(x.value / 1e6) + "M") : bigNum(x.value * 1e6)}` : x.volume ? ` · 거래량 ${bigNum(x.volume)}` : "";
    if ($("#dvSess")) { const S = marketSession(r.mkt); $("#dvSess").innerHTML = `<span class="ss-chip ss-${S.phase}"><i></i><b>${us ? "미국" : "국내"}</b> ${esc(S.label)}</span><span class="tiny">${esc(S.sub)}</span>`; }
    $("#dvList").innerHTML = r.items.length ? r.items.map((x) => `<a class="item dv-row" href="#invest/stock?m=${r.mkt}&s=${encodeURIComponent(x.sym)}">
      <span class="dv-rank ${x.rank <= 3 ? "dv-top" : ""}">${x.rank}${mv(x)}</span>
      <span class="main"><b>${esc(x.name)}</b><span class="sub">${esc(x.sym)}${sub(x)}</span></span>
      <span class="end"><b>${us ? usd(x.price) : won(x.price)}</b><span class="mk-c ${tone(x.change_pct)}">${pct(x.change_pct, 2)}</span></span>
      <button type="button" class="star dv-star" data-star="${esc(x.sym)}" aria-pressed="${W.has(x.sym)}" aria-label="관심종목">${STAR}</button></a>`).join("")
      : `<div class="empty">순위가 비어 있어요(장 시작 전일 수 있어요)</div>`;
    $("#dvFoot").textContent = `${r.at} 기준 · ${{ views: "키움 고객이 최근 10분간 많이 찾아본 종목", watch: "오늘 관심종목에 많이 담긴 종목", buy: "오늘 키움 고객이 많이 산 종목" }[r.kind] || "키움 실시간 순위"} · ☆을 누르면 관심종목에 담겨요`;
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
    const err = d.error ? `<div class="notice warn">${esc(d.error)}${ipHelp(d.error)}</div>` : "";
    if (d.total == null) { box.innerHTML = err || `<div class="empty loading">불러오는 중</div>`; return; }
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
      ${AI_BTN("portfolio", "AI 포트폴리오 점검(쏠림·위험·적립 계획)")}
      <div class="sec-title">보유 종목 ${H.length}개</div>
      <section class="card tight"><div class="list">${H.length ? H.map((h) => `<a class="item" style="text-decoration:none;color:inherit" href="${stockHref(h.code, h.market)}">
        <span class="ico ${hue(h.name)}">${esc(h.name.slice(0, 2))}</span>
        <span class="main"><b>${esc(h.name)} <span class="tiny">${esc(h.code)}</span></b>
          <span class="sub">${h.market === "us" ? "미국 · " : ""}${won(h.qty)}주 · 평균 ${h.market === "us" ? usd(h.avg_price) : won(h.avg_price)} · 현재 ${h.market === "us" ? usd(h.price) : won(h.price)}</span></span>
        <span class="end"><b>${won(h.value)}</b><span class="small ${tone(h.pnl)}" style="font-weight:700">${signed(h.pnl)} (${pct(h.pnl_pct, 2)})</span></span></a>`).join("")
        : `<div class="empty">보유 종목이 없어요</div>`}</div></section>
      <div class="tiny" style="padding:0 3px">몇 초마다 키움에서 새로 받아요. 자산 탭에서 이 계좌를 연결하면 순자산에도 자동으로 들어가요.</div>`;
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
        if (a.same_as) return `<div class="tile"><div class="lbl">${esc(a.label)}</div><div class="val" style="font-size:12px">기본 계좌와 같은 계좌</div>
          <div class="tiny">같은 키움 앱키라 같은 계좌예요. 잔고는 왼쪽에 함께 보여요.</div></div>`;
        const H = a.holdings.concat(a.us_holdings || []);
        const val = H.reduce((s, x) => s + x.value, 0), pnl = H.reduce((s, x) => s + x.pnl, 0);
        return `<div class="tile"><div class="lbl">${esc(a.label)}</div><div class="val">${won(val + (a.cash || 0))}</div>
          <div class="tiny">현금 ${won(a.cash)}${a.us_cash && a.us_cash.usd_orderable ? ` · ${usd(a.us_cash.usd_orderable)}` : ""}</div><div class="tiny ${tone(pnl)}" style="font-weight:700">평가손익 ${signed(pnl)}</div></div>`;
      }).join("")}</div>
      <div class="tiny" style="padding:0 3px">${esc(d.now.replace("T", " "))} · ${d.market_open ? "장 운영 중" : "장 마감"} · ${esc(d.accounts.map((a) => a.mode_label || "준비 안 됨").join(" / "))}</div>
      <div class="sec-title">보유 종목</div>
      <section class="card tight"><div class="list">${rows.length ? rows.map((x) => `<button type="button" class="item" data-sell="${x.eng}|${esc(x.code)}|${x.sellable ?? x.qty}">
        <span class="ico ${x.market === "us" ? "" : x.eng === "scalp" ? "y" : ""}">${x.market === "us" ? "미국" : x.eng === "scalp" ? "기본" : "장기"}</span>
        <span class="main"><b>${esc(x.name)}</b><span class="sub">${won(x.qty)}주 · 평균 ${x.market === "us" ? usd(x.avg_price) : won(x.avg_price)} · 현재 ${x.market === "us" ? usd(x.price) : won(x.price)}</span></span>
        <span class="end"><b>${won(x.value)}</b><span class="small ${tone(x.pnl)}" style="font-weight:700">${signed(x.pnl)} (${pct(x.pnl_pct, 1)})</span></span></button>`).join("")
        : `<div class="empty">보유 종목이 없어요</div>`}</div></section>
      <div id="riskBox">${this.riskHtml(this.riskMemo)}</div>
      <div id="openBox">${this.openHtml(this.openMemo)}</div>
      <div class="sec-title">최근 주문</div>
      <section class="card tight"><div class="list">${d.orders.length ? d.orders.slice(0, 10).map((o) => {
        const st = { cancel: ["취소", ""], cancelled: ["취소됨", ""], accepted: ["접수·예상가", "y"], partial: ["일부 체결", "y"], filled: ["체결", "g"] }[o.status] || [o.status, ""];
        return `<button type="button" class="item" ${dt(orderDetail(o))}>
        <span class="ico" style="color:${o.status === "cancel" ? "var(--ink3)" : o.side === "buy" ? "var(--up)" : "var(--down)"}">${o.status === "cancel" ? "취소" : o.side === "buy" ? "매수" : "매도"}</span>
        <span class="main"><b>${esc(o.name || o.code)}</b><span class="sub">${o.ts.slice(5, 16).replace("T", " ")} · ${esc(o.msg)}</span></span>
        <span class="end"><b>${won(o.qty)}주</b><span class="tiny">${o.fill_price ? (isUsCode(o.code) ? usd(o.fill_price) + " " : won(o.fill_price) + "원 ") : ""}<span class="tag ${st[1]}">${esc(st[0])}</span></span></span></button>`; }).join("")
        : `<div class="empty">아직 주문이 없어요</div>`}</div></section>`;
    this.loadOpen();
    this.riskBind();
    this.loadRisk();
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

  // ---------- 계좌 전체 위험(읽기 전용) ----------
  riskMemo: null, riskAt: 0,
  async loadRisk() {
    if (Date.now() - this.riskAt < 120000) return;          // 2분에 한 번만(일봉을 여러 종목 받는다)
    this.riskAt = Date.now();
    try {
      this.riskMemo = await GET("/api/risk");
      if (investState.tab === "trade" && $("#riskBox")) $("#riskBox").innerHTML = this.riskHtml(this.riskMemo);
    } catch (e) { /* 다음 새로고침에 다시 */ }
  },
  riskBind() {
    const box = $("#riskBox");
    if (!box) return;
    box.onclick = (e) => { const b = e.target.closest("[data-rks]"); if (b) this.riskPop(b.dataset.rks); };
    box.onkeydown = (e) => { if ((e.key === "Enter" || e.key === " ") && e.target.matches("[data-rks]")) { e.preventDefault(); this.riskPop(e.target.dataset.rks); } };
  },
  riskFan(r) {
    // 앞으로 20거래일을 3,000번 그려 본 분포: 바깥 띠 = 열에 아홉, 안쪽 띠 = 절반, 선 = 가운데
    const m = r.mc;
    if (!m) return "";
    const W = 320, H = 118, L = 6, R = 58, T = 10, B = 18, pts = [{ d: 0, p5: 0, p25: 0, p50: 0, p75: 0, p95: 0 }].concat(m.band);
    const lo = Math.min(m.p5, -1), hi = Math.max(m.p95, 1), X = (d) => L + (W - L - R) * d / m.days, Y = (v) => T + (H - T - B) * (hi - v) / (hi - lo);
    const area = (a, b) => pts.map((p, i) => `${i ? "L" : "M"}${X(p.d).toFixed(1)},${Y(p[a]).toFixed(1)}`).join("") + pts.slice().reverse().map((p) => `L${X(p.d).toFixed(1)},${Y(p[b]).toFixed(1)}`).join("") + "Z";
    const lab = (v, k) => `<text x="${W - R + 6}" y="${(Y(v) + 3.5).toFixed(1)}" class="rk-fl ${k}">${v > 0 ? "+" : ""}${v}%</text>`;
    return `<div class="rk-h">앞으로 ${m.days}거래일 · ${m.n.toLocaleString("ko-KR")}번 그려 본 결과</div>
      <svg class="rk-fan" viewBox="0 0 ${W} ${H}" role="img" aria-label="${m.days}거래일 뒤 계좌 변화: 나쁜 경우 ${m.p5}%, 가운데 ${m.p50}%, 좋은 경우 ${m.p95}%">
        <path class="b1" d="${area("p95", "p5")}"/><path class="b2" d="${area("p75", "p25")}"/>
        <line class="z" x1="${L}" x2="${W - R}" y1="${Y(0).toFixed(1)}" y2="${Y(0).toFixed(1)}"/>
        <path class="md" d="${pts.map((p, i) => `${i ? "L" : "M"}${X(p.d).toFixed(1)},${Y(p.p50).toFixed(1)}`).join("")}"/>
        ${lab(m.p95, "up")}${lab(m.p5, "down")}
        <text x="${L}" y="${H - 4}" class="rk-fx">오늘</text><text x="${W - R}" y="${H - 4}" class="rk-fx" text-anchor="end">${m.days}거래일 뒤</text></svg>
      <div class="sc-kpi"><div><span>나쁜 경우(스무 번에 한 번)</span><b class="down">−${won(m.var_krw)}</b></div>
        <div><span>그보다 나쁠 때 평균</span><b class="down">−${won(m.cvar_krw)}</b></div>
        <div><span>10% 넘게 잃을 확률</span><b>${m.p_loss10}%</b></div></div>
      <div class="tiny">지난 ${m.window || r.days}거래일에 실제 있었던 하루하루를 무작위로 이어 붙여 본 값이에요(몬테카를로).${m.alt ? ` 긴 기간과 최근 ${r.days}일을 둘 다 그려 보고 더 나쁜 쪽을 썼어요(다른 쪽은 나쁜 경우 −${m.alt.var95}%).` : ""} 바깥 띠에 열에 아홉, 안쪽 띠에 절반이 들어와요. 최근 오름세가 이어진다고 가정하지 않고, 그사이 가장 깊은 낙폭은 보통 ${m.mdd50}%·나쁜 경우 ${m.mdd95}%예요.</div>`;
  },
  riskScen(r) {
    // 요인 시나리오: 과거 위기를 지금 보유에 입히면 · 만약에 · 한 종목 급락. 줄을 누르면 종목별 내역이 팝업으로
    const S = r.scen;
    if (!S || !S.rows.length) return "";
    const sh = (s) => [["kr", "코스피"], ["us", "S&P"], ["fx", "원/달러"]].filter(([k]) => s[k] != null).map(([k, n]) => `${n} ${s[k] > 0 ? "+" : ""}${s[k]}%`).join(" · ");
    const row = (x, i) => `<div class="rk-sr hx-link" role="button" tabindex="0" data-rks="${i}" aria-label="${esc(x.name)} 종목별 내역 보기"><span><b>${esc(x.name)}<i class="hx-chev" aria-hidden="true">›</i></b><small>${x.sub ? esc(x.sub) + " · " : ""}${esc(x.shock_txt || sh(x.shock))}</small></span>
        <span class="rk-sv"><b class="${tone(x.pnl)}">${signed(x.pnl)}</b><small class="${tone(x.pct)}">${pct(x.pct, 1)}</small></span></div>`;
    const cr = S.rows.map((x, i) => [x, i]).filter(([x]) => x.kind === "crisis"), wi = S.rows.map((x, i) => [x, i]).filter(([x]) => x.kind === "whatif");
    return `${cr.length ? `<div class="rk-h">과거 위기가 지금 다시 온다면</div><div class="rk-sl">${cr.map(([x, i]) => row(x, i)).join("")}</div>` : ""}
      ${wi.length ? `<div class="rk-h">만약에</div><div class="rk-sl">${wi.map(([x, i]) => row(x, i)).join("")}</div>` : ""}
      <div class="rk-h">한 종목 급락</div><div class="rk-sl"><div class="rk-sr"><span><b>${esc(S.gap.name)} 하루 −${S.gap.drop}%</b><small>가장 크게 든 종목 · 손절 주문이 듣지 않는 경우(하한가·거래정지·갭)</small></span>
        <span class="rk-sv"><b class="down">−${won(S.gap.loss)}</b><small class="down">−${S.gap.pct}%</small></span></div></div>
      <div class="tiny">종목마다 요인(${esc((S.factors || ["코스피", "S&P 500", "원/달러"]).join("·"))})에 따라 움직인 정도(민감도)를 구해, 그 기간 요인이 실제 움직인 폭을 입힌 어림값이에요. '만약에'는 나머지 요인도 과거에 함께 움직인 만큼 따라간다고 봐요. 충격이 클수록 오차가 커요.${S.est < S.n ? ` 기록이 짧은 ${S.n - S.est}종목은 지수와 똑같이 움직인다고 봤어요.` : ""}</div>`;
  },
  riskPop(i) {
    const S = (this.riskMemo || {}).scen, x = S && S.rows[+i];
    if (!x) return;
    const bt = Object.fromEntries(S.betas.map((b) => [b.name, b]));
    openSheet(x.name, `<div class="sc-kpi"><div><span>계좌 손익(어림)</span><b class="${tone(x.pnl)}">${signed(x.pnl)}</b></div><div><span>계좌 대비</span><b class="${tone(x.pct)}">${pct(x.pct, 2)}</b></div>
        <div><span>${x.kind === "crisis" ? "기간" : "가정"}</span><b style="font-size:13px">${esc(x.sub || "요인 하나를 흔듦")}</b></div></div>
      <div class="hx-sub">넣은 충격</div><div class="tiny">${esc(x.shock_txt || "")}${x.kind === "whatif" ? " (첫째만 정한 값, 나머지는 과거에 함께 움직인 만큼)" : " (그 기간 실제 움직임)"}</div>
      <div class="hx-sub">종목별(많이 잃는 순)</div>
      <div class="rk-sl">${x.items.map((t) => { const b = bt[t.name] || {}; return `<div class="rk-sr"><span><b>${esc(t.name)}</b><small>${b.txt ? "민감도: " + esc(b.txt) : `지수 민감도 ${b.bm ?? "–"}`}</small></span>
        <span class="rk-sv"><b class="${tone(t.pnl)}">${signed(t.pnl)}</b><small class="${tone(t.pct)}">${pct(t.pct, 1)}</small></span></div>`; }).join("")}</div>
      <div class="tiny">민감도는 최근 약 2년의 일간 움직임으로 구했어요. 위기 때는 종목들이 평소보다 더 같이 떨어지는 경향이 있어 실제 손실은 더 클 수 있어요.</div>`);
  },
  riskHtml(r) {
    if (!r || !r.configured || !r.n) return "";
    const bar = (w) => `<span class="rk-bar"><i style="width:${Math.min(100, w).toFixed(1)}%"></i></span>`;
    return `<div class="sec-title">계좌 전체 위험</div>
      <section class="card tight rk"><div class="sc-kpi"><div><span>보통 하루 흔들림</span><b>${r.day_vol == null ? "–" : "±" + r.day_vol + "%"}</b></div>
          <div><span>나쁜 날 하루 손실</span><b class="down">${r.day_risk == null ? "–" : "−" + won(r.day_risk)}</b></div>
          <div><span>지수 −5%면</span><b class="down">${r.stress ? "−" + won(Math.abs(r.stress.loss)) : "–"}</b></div></div>
        ${r.flags.length ? `<ul class="at-notes">${r.flags.map((f) => `<li>${esc(f)}</li>`).join("")}</ul>` : ""}
        ${this.riskFan(r)}
        ${this.riskScen(r)}
        <div class="rk-h">종목 비중</div>
        <div class="rk-list">${r.top.map((x) => `<div><b>${esc(x.name)}</b>${bar(x.w)}<span>${x.w}%</span><small>${x.sector ? esc(x.sector) + " · " : ""}${x.vol == null ? "일봉 자료 없음" : "하루 ±" + x.vol + "%"}${x.beta == null ? "" : " · 지수 민감도 " + x.beta}</small></div>`).join("")}</div>
        <div class="rk-h">업종 비중</div>
        <div class="rk-list">${r.sectors.map((x) => `<div><b>${esc(x.name)}</b>${bar(x.w)}<span>${x.w}%</span></div>`).join("")}</div>
        ${r.pairs.length ? `<div class="rk-h">같이 움직이는 종목</div><div class="tiny">${r.pairs.map((p) => `${esc(p.a)}–${esc(p.b)} ${p.corr}`).join(" · ")}</div>` : ""}
        <div class="tiny">최근 ${r.days}거래일의 일간 등락으로 셈한 어림값이에요. '나쁜 날'은 스무 날에 한 번쯤 넘는 수준이고, 실제 손실은 더 클 수 있어요.${r.cover < 100 ? ` 자료가 있는 종목(비중 ${r.cover}%)만 셈했어요.` : ""}${r.beta == null ? "" : ` 계좌 전체의 지수 민감도는 ${r.beta}(지수가 1% 움직일 때 약 ${r.beta}%).`}</div></section>`;
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
    const err = Object.values(d.errors || {}).map((m) => `<div class="notice warn">${esc(m)}${ipHelp(m)}</div>`).join("");
    if (!d.items.length) return err;
    return `${err}<div class="sec-title">미체결 주문 ${d.items.length}건</div>
      <section class="card tight"><div class="list">${d.items.map((o, i) => `<div class="item">
        <span class="ico" style="color:${o.side === "buy" ? "var(--up)" : "var(--down)"}">${o.side === "buy" ? "매수" : "매도"}</span>
        <a class="main" style="text-decoration:none;color:inherit" href="${stockHref(o.code, o.market)}"><b>${esc(o.name || o.code)}</b><span class="sub">${o.market === "us" ? "미국 · " : ""}${o.price ? (o.market === "us" ? usd(o.price) : won(o.price) + "원") : "시장가"} · ${won(o.left)}/${won(o.qty)}주 남음${o.time ? " · " + esc(String(o.time).replace(/^(\d\d)(\d\d).*/, "$1:$2")) : ""}</span></a>
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
        <div><div class="t"><span class="state-dot"></span>${esc(title)}</div><div class="small muted" style="margin-top:3px">${esc(sub)}</div></div>
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
      ${AI_BTN("review", "AI 오늘 단타 복기")}
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
  // ---------- 배당 ----------
  dvState() {
    if (this.dv) return this.dv;
    let s = {};
    try { s = JSON.parse(store.get("dv") || "{}") || {}; } catch (e) { s = {}; }
    const live0 = { on: false, spend: 2000000, other: 0, from: 1, health: "local", years: 30 };
    const D = Object.assign({ tab: "mine", amount: 300000000, add: 0, years: 10, reinvest: true, pg: 3, gs: 1, fxc: 0, preset: "growth", assets: null,
      style: "balance", region: "mix", memo: "", web: true, model: "" }, s,
      { uni: null, sim: null, view: "first", f: "all", sort: "yield", plan: null, busy: false, text: "", src: [], used: null, err: "", status: "", mine: null });
    // 저장된 값이 비었거나(예전 판) 일부만 있어도 생활비 설정은 늘 온전한 모양으로
    D.live = Object.assign({}, live0, D.live && typeof D.live === "object" ? D.live : {});
    return (this.dv = D);
  },
  dvSave() {
    const D = this.dv, keep = {};
    ["tab", "amount", "add", "years", "reinvest", "pg", "gs", "fxc", "preset", "assets", "style", "region", "memo", "web", "model", "live"].forEach((k) => (keep[k] = D[k]));
    store.set("dv", JSON.stringify(keep));
  },
  async dividend(box) {
    const D = this.dvState();
    box.innerHTML = `<section class="ap-hero dv-hero">
        <div class="ap-h1">배당으로 매달 받는 돈</div>
        <div class="ap-h2">보유 종목의 배당 달력, 투자금별 시뮬레이션, 배당주 찾기, AI 구성까지 한곳에서 봐요. 금액은 모두 세금을 뺀 원화예요.</div>
        ${seg("배당탭", [["mine", "내 배당"], ["sim", "시뮬레이션"], ["list", "종목"], ["ai", "AI 구성"]], D.tab, "ap-mkt")}
      </section><div id="dvBody" class="dv-body"></div>`;
    bindSeg(box, "배당탭", (v) => { D.tab = v; this.dvSave(); this.dvTab(); });
    await this.dvTab();
  },
  async dvTab() {
    const D = this.dv, el = $("#dvBody");
    if (!el) return;
    el.onclick = null; el.oninput = null; el.onchange = null;
    el.innerHTML = `<div class="empty loading">${D.tab === "mine" ? "보유 종목의 배당을 계산하는 중" : D.uni ? "불러오는 중" : "배당 자료를 불러오는 중 (처음 열 때는 20초쯤 걸려요)"}</div>`;
    try {
      if (D.tab === "mine") return await this.dvMine();
      if (D.tab === "sim") return await this.dvSimView();
      if (D.tab === "list") return await this.dvList();
      return await this.dvAi();
    } catch (e) {
      if (e instanceof Locked || e instanceof NeedConnect) throw e;
      if (e.code === "old_server") return oldServerBox($("#dvBody"), () => this.dvTab());
      if ($("#dvBody")) $("#dvBody").innerHTML = `<div class="notice warn">${esc(e.message)}</div><button type="button" class="btn wide" id="dvRetry">다시 시도</button>`;
      if ($("#dvRetry")) $("#dvRetry").onclick = () => this.dvTab();
    }
  },
  // 후보 종목과 예시 구성(처음엔 종목마다 자료를 받느라 몇 초 걸린다)
  async dvUni(force) {
    const D = this.dv;
    if (!D.uni || force) D.uni = await GET("/api/div/universe" + (force ? "?refresh=1" : ""));
    return D.uni;
  },
  // 지금 배당률이 지난 5년 월별 배당률 가운데 어디쯤인지(높을수록 주가가 싼 편)
  dvBandTag(b) {
    if (!b) return `<span class="tiny">-</span>`;
    const c = b.pct >= 80 ? "hi" : b.pct <= 20 ? "lo" : "";
    return `<span class="dv-bt ${c}">${b.pct >= 80 ? "5년 중 높음" : b.pct <= 20 ? "5년 중 낮음" : `5년 중 ${b.pct}%`}</span>`;
  },
  dvBandHtml(b) {
    if (!b) return "";
    const X = (v) => (b.hi > b.lo ? ((v - b.lo) / (b.hi - b.lo)) * 100 : 50), S = b.series || [], W = 300, H = 46;
    const mn = Math.min(...S, b.cur), mx = Math.max(...S, b.cur), Y = (v) => (mx > mn ? H - 4 - ((v - mn) / (mx - mn)) * (H - 8) : H / 2);
    const path = S.map((v, k) => `${k ? "L" : "M"}${(k / Math.max(1, S.length - 1) * W).toFixed(1)} ${Y(v).toFixed(1)}`).join(" ");
    const say = b.pct >= 80 ? "지난 5년 중 배당률이 높은 편이에요. 주가가 싸졌다는 뜻일 수 있지만, 실적이 나빠 주가가 내린 것인지도 함께 보세요."
      : b.pct <= 20 ? "지난 5년 중 배당률이 낮은 편이에요. 주가가 많이 올라 지금 사면 받는 배당이 적어요." : "지난 5년 범위의 가운데쯤이에요.";
    return `<h3 class="lr-h">배당률 위치(${esc(b.from.replace("-", "/"))}부터 월말 기준)</h3>
      <div class="dv-band"><div class="dv-bb" role="img" aria-label="5년 최저 ${b.lo}%, 가운데 ${b.med}%, 최고 ${b.hi}%, 지금 ${b.cur}%">
          <span class="md" style="left:${X(b.med).toFixed(1)}%"></span><span class="me" style="left:${Math.max(0, Math.min(100, X(b.cur))).toFixed(1)}%"><b>지금 ${b.cur}%</b></span></div>
        <div class="dv-bl"><span>최저 ${b.lo}%</span><span>가운데 ${b.med}%</span><span>최고 ${b.hi}%</span></div>
        <svg class="dv-bs" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true"><path d="${path}"/><line x1="0" x2="${W}" y1="${Y(b.cur).toFixed(1)}" y2="${Y(b.cur).toFixed(1)}"/></svg>
        <div class="tiny">${say}</div></div>`;
  },
  // 최근 배당 변화(하루 한 번 모을 때 새 배당이 나오면 직전과 비교해 적는다)
  dvEvents(EV, n) {
    if (!EV) return "";
    const K = { raise: ["인상", "up"], cut: ["삭감", "down"], same: ["그대로", ""], first: ["첫 기록", ""] };
    const rows = (EV.events || []).slice(0, n);
    return `<section class="card dv-ev"><div class="card-head"><h2>최근 배당 변화</h2><span class="tiny">${EV.days ? `매일 아침 모아요 · ${EV.days}일째` : "매일 아침 8시 뒤에 모아요"}</span></div>
      ${rows.length ? `<div class="list">${rows.map((e) => `<button type="button" class="item dv-evr" data-dv="${e.mkt}:${esc(e.sym)}">
        <span class="main"><b>${esc(e.mkt === "us" ? e.sym : e.name || e.sym)}${e.held ? ` <span class="tag v">보유</span>` : ""}</b><span class="sub">배당락 ${esc(e.ex.slice(5).replace("-", "/"))} · 주당 ${e.mkt === "us" ? "$" + Number(e.amt).toFixed(3) : won(e.amt) + "원"}</span></span>
        <span class="end"><b class="${(K[e.kind] || ["", ""])[1]}">${(K[e.kind] || [e.kind])[0]}</b><span class="tiny">${e.chg == null ? "" : pct(e.chg, 1)}</span></span></button>`).join("")}</div>`
        : `<div class="tiny">아직 새로 나온 배당이 없어요. 배당이 나오면 직전보다 늘었는지 줄었는지 여기 적고, 보유 종목이 바뀌면 알려 드려요.</div>`}</section>`;
  },
  dvFind(mkt, sym) { return ((this.dv.uni || {}).items || []).find((i) => i.mkt === mkt && i.sym === sym); },
  // 12개월 막대(지급 달력). rows: [{label, total, parts:[{c, v}], on}]
  dvStrip(rows, attr = "") {
    const max = Math.max(1, ...rows.map((r) => r.total));
    // 막대 위 숫자는 좁아서 '만' 글자를 빼고 만원 단위로만 적는다(1억 이상은 '1.2억')
    const lab = (v) => (v >= 1e8 ? bigNum(v) : v >= 1e4 ? Math.round(v / 1e4).toLocaleString("ko-KR") : won(v));
    return `<div class="dv-strip-u">${max >= 1e4 ? "단위: 만원" : "단위: 원"}</div><div class="dv-strip" ${attr}>${rows.map((r, i) => `<button type="button" class="dv-col${r.on ? " on" : ""}${r.total ? "" : " zero"}" data-mi="${i}" aria-label="${esc(r.label)} ${won(r.total)}원">
        <em>${r.total ? lab(r.total) : "·"}</em>
        <span class="dv-bar"><span style="height:${Math.max(r.total ? 4 : 0, r.total / max * 100)}%">${(r.parts || [{ c: "", v: 1 }]).filter((p) => p.v > 0).map((p) => `<i class="${p.c}" style="flex:${p.v}"></i>`).join("")}</span></span>
        <b>${esc(r.label)}</b></button>`).join("")}</div>`;
  },
  dvDots(months) {
    const mx = Math.max(0.0001, ...(months || []));
    return `<span class="dv-dots" aria-hidden="true">${Array.from({ length: 12 }, (_, i) => { const v = (months || [])[i] || 0; return `<i class="${v > 0.004 ? "on" : ""}" style="${v > 0.004 ? `opacity:${(0.45 + 0.55 * v / mx).toFixed(2)}` : ""}"></i>`; }).join("")}</span>`;
  },
  // 미국 종목은 티커가 더 익숙하다: 제목은 티커, 설명 앞에 이름
  dvT(i) { return i.mkt === "us" ? i.sym : i.name || i.sym; },
  dvS(i) { return i.mkt === "us" && i.name && i.name !== i.sym ? i.name + " · " : ""; },
  dvYm(ym) { return `${ym.slice(0, 4)}년 ${+ym.slice(5)}월`; },
  dvPays(months) { const m = (months || []).map((v, i) => (v > 0.004 ? i + 1 : 0)).filter(Boolean); return m.length >= 11 ? "매달" : m.length ? m.join("·") + "월" : "-"; },

  // ----- 내 배당 -----
  async dvMine() {
    const D = this.dv, M = (D.mine = await GET("/api/div/mine")), el = $("#dvBody");
    if (!el || D.tab !== "mine") return;
    if (!M.configured || !M.rows.length) {
      el.innerHTML = `<section class="card dv-empty"><div class="dv-coin" aria-hidden="true"><i></i><i></i><i></i></div>
        <h2>${M.configured ? "아직 보유 종목이 없어요" : "키움 계좌를 연결하면 내 배당을 보여 드려요"}</h2>
        <div class="tiny">보유 종목마다 최근 1년 실제 배당을 찾아, 몇 월에 얼마가 들어오는지 달력으로 정리해요.</div>
        <button type="button" class="btn dark wide" data-dvgo="sim">투자금으로 먼저 시뮬레이션해 보기</button>
        ${M.configured ? "" : `<a class="btn wide" href="#settings/accounts">계좌 연결하기</a>`}</section>`;
      el.onclick = (e) => { const g = e.target.closest("[data-dvgo]"); if (g) { D.tab = g.dataset.dvgo; this.dvSave(); route(); } };
      return;
    }
    const now = new Date().getMonth(), HU = this.DV_HUES;
    const col = {}; M.rows.filter((r) => r.net > 0).forEach((r, i) => (col[r.sym] = "h-" + HU[i % HU.length]));
    const strip = M.months.map((v, m) => ({ label: `${m + 1}월`, total: v, on: m === now,
      parts: M.rows.filter((r) => r.net > 0 && (r.months || [])[m] > 0).map((r) => ({ c: col[r.sym], v: r.net * r.months[m] })) }));
    const low = Math.min(...M.months), high = Math.max(...M.months), empty = M.months.filter((v) => !v).length;
    el.innerHTML = `
      <section class="card dv-main">
        <div class="dv-big"><span>한 달 평균(세후)</span><b data-count="${M.monthly_net}">${won(M.monthly_net)}원</b>
          <small>1년에 ${won(M.annual_net)}원 · 세전 ${won(M.annual_gross)}원</small></div>
        <div class="sc-kpi dv-kpi"><div><span>지금 배당률(세전)</span><b>${M.yield == null ? "-" : M.yield + "%"}</b></div>
          <div><span>내 매수가 대비</span><b>${M.yoc == null ? "-" : M.yoc + "%"}</b></div>
          <div><span>배당 주는 종목</span><b>${M.payers}<small> / ${M.rows.length}</small></b></div></div>
        <div class="dv-goal">${M.goal ? `<div class="dv-goal-h"><span>목표 월 ${won(M.goal)}원</span><b>${M.goal_pct}%</b></div>
            <div class="bar"><div style="width:${Math.min(100, M.goal_pct)}%"></div></div>
            <div class="tiny">${M.goal_pct >= 100 ? "목표를 넘었어요." : M.need ? `지금 배당률 그대로라면 약 ${bigNum(M.need)}원을 더 투자하면 닿아요.` : ""} <button type="button" class="lnk" id="dvGoal">목표 바꾸기</button></div>`
          : `<button type="button" class="btn sm soft" id="dvGoal">목표 월 배당 정하기</button>`}</div>
      </section>
      <section class="card">
        <div class="card-head"><h2>몇 월에 얼마가 들어오나</h2><span class="tiny">세후 · 막대를 누르면 종목별</span></div>
        ${this.dvStrip(strip, 'id="dvCal"')}
        <div class="tiny">${empty ? `배당이 없는 달이 ${empty}번 있어요. ` : ""}가장 적은 달 ${won(low)}원, 가장 많은 달 ${won(high)}원이에요. 지급월은 지난 배당락일로 추정한 값이라 한 달쯤 다를 수 있어요.</div>
      </section>
      ${M.annual_gross > M.line ? `<div class="notice">세전 배당이 연 ${bigNum(M.line)}원을 넘어요. 금융소득종합과세 대상이 되고 건강보험료가 달라질 수 있으니 세무 상담을 권해요.</div>` : ""}
      <div class="sec-title">보유 종목별 배당</div>
      <section class="card tight"><div class="list">${M.rows.map((r) => `<button type="button" class="item dv-row" data-dv="${r.mkt}:${esc(r.sym)}">
        <span class="dv-sw ${col[r.sym] || ""}"></span>
        <span class="main"><b>${esc(r.name)}</b><span class="sub">${r.net ? `${esc(r.freq_name)} · 배당률 ${r.yield ?? "-"}%${r.yoc != null ? ` · 매수가 대비 ${r.yoc}%` : ""}` : r.error ? "배당 자료를 받지 못했어요" : "최근 1년 배당 없음"}</span>${r.net ? this.dvDots(r.months) : ""}</span>
        <span class="end"><b>${r.net ? won(r.net) + "원" : "-"}</b><span class="tiny">${r.net ? `월 ${won(r.net / 12)}원` : ""}</span></span></button>`).join("")}</div></section>
      <div id="dvMineEv"></div>
      <div class="tiny" style="padding:0 3px">최근 1년 동안 실제로 지급된 주당 배당금 × 보유 수량으로 계산했어요(국내 15.4%, 미국 15% 세금 차감, 환율 ${won(M.fx)}원). 앞으로의 배당은 달라질 수 있어요.</div>`;
    countUp(el);
    GET("/api/div/events").then((EV) => {
      const h = { ...EV, events: (EV.events || []).filter((x) => x.held) };
      if ($("#dvMineEv") && h.events.length) $("#dvMineEv").innerHTML = this.dvEvents(h, 5);
    }).catch(() => {});
    el.onclick = (e) => {
      if (e.target.closest("#dvGoal")) return this.dvGoalSheet(M.goal);
      const c = e.target.closest("#dvCal [data-mi]");
      if (c) {
        const m = +c.dataset.mi, list = M.rows.filter((r) => r.net > 0 && (r.months || [])[m] > 0).map((r) => [r.name, won(r.net * r.months[m]) + "원"]);
        return detailSheet({ title: `${m + 1}월에 들어오는 배당`, head: `<b style="font-size:22px">${won(M.months[m])}원</b><div class="tiny">세후 · 지급월은 추정</div>`,
          rows: list.length ? list : [["이 달에는 배당이 없어요", ""]] });
      }
      const r = e.target.closest("[data-dv]");
      if (r) { const [mk, sy] = r.dataset.dv.split(":"); this.dvDetail(mk, sy); }
    };
  },
  dvGoalSheet(cur) {
    openSheet("목표 월 배당", `<form id="dvGoalF" class="sc-form" novalidate>
      <label class="field">한 달에 받고 싶은 배당(세후)<span class="dv-unit"><input id="dvGoalV" inputmode="numeric" autocomplete="off" value="${cur ? Math.round(cur / 1e4) : ""}" placeholder="예: 300"><i>만원</i></span>
        <span class="hint">내 배당과 시뮬레이션에서 이 금액에 언제 닿는지 보여 줘요. 비우면 목표를 지워요.</span></label>
      <button type="submit" class="btn dark wide">저장</button></form>`, (box) => {
      $("#dvGoalF", box).onsubmit = async (e) => {
        e.preventDefault();
        const v = Number(String($("#dvGoalV", box).value).replace(/[^\d.]/g, "")) || 0;
        try { await run(() => api("PUT", "/api/div/goal", { goal: v * 1e4 }), v ? `목표를 월 ${won(v)}만원으로 정했어요` : "목표를 지웠어요"); } catch (err) { return; }
        if (this.dv.uni) this.dv.uni.goal = v * 1e4;
        closeSheet(); this.dvTab(); if ($("#dcaDiv")) this.dcaOutlook();
      };
      setTimeout(() => $("#dvGoalV", box)?.focus(), 80);
    });
  },
  // 종목 하나의 배당 내역
  async dvDetail(mkt, sym) {
    const D = this.dv;
    let i = this.dvFind(mkt, sym);
    if (!i) { try { i = await run(() => GET(`/api/div/info/${mkt}/${encodeURIComponent(sym)}`)); } catch (e) { return; } }
    const cur = i.currency === "USD" ? (v) => "$" + Number(v).toFixed(v < 10 ? 3 : 2) : (v) => won(v) + "원";
    const ys = (i.years || []).slice(-8), mx = Math.max(0.0001, ...ys.map((y) => y.amt)), thisY = new Date().getFullYear();
    const inSim = (D.assets || []).some((a) => a.mkt === mkt && a.sym === sym);
    openSheet(i.name, `<div class="dv-dt">
      <div class="chips"><span class="tag">${esc(i.cat_name || "")}</span><span class="tag v">${esc(i.freq_name)}</span>${i.custom ? `<span class="tag y">직접 넣음</span>` : ""}${i.stale ? `<span class="tag y">예전 자료</span>` : ""}</div>
      ${i.note ? `<div class="tiny">${esc(i.note)}</div>` : ""}
      ${i.error ? `<div class="notice warn">${esc(i.error)}. 시뮬레이션에서는 배당률을 직접 넣어 쓸 수 있어요.</div>` : `
      <div class="sc-kpi dv-kpi4"><div><span>배당률(세전)</span><b>${i.yield == null ? "-" : i.yield + "%"}</b></div>
        <div><span>1년 주당 배당</span><b>${cur(i.ttm)}</b></div>
        <div><span>5년 배당 성장</span><b class="${tone(i.growth5)}">${i.growth5 == null ? (i.growth3 == null ? "-" : pct(i.growth3, 1)) : pct(i.growth5, 1)}</b></div>
        <div><span>안 줄인 햇수</span><b>${i.streak ? i.streak + "년" : "-"}</b></div></div>
      ${this.dvBandHtml(i.band)}
      <h3 class="lr-h">해마다 받은 주당 배당</h3>
      <div class="dv-years">${ys.map((y) => `<div class="${y.y === thisY ? "now" : ""}"><em>${cur(y.amt)}</em><span><i style="height:${Math.max(4, y.amt / mx * 100)}%"></i></span><b>${String(y.y).slice(2)}년${y.y === thisY ? "<small>진행 중</small>" : ""}</b></div>`).join("") || `<div class="tiny">기록이 없어요</div>`}</div>
      <h3 class="lr-h">지급월(추정)</h3>
      <div class="dv-mon">${Array.from({ length: 12 }, (_, m) => `<span class="${(i.months || [])[m] > 0.004 ? "on" : ""}">${m + 1}</span>`).join("")}</div>
      <dl class="dv-dl"><div><dt>현재가</dt><dd>${cur(i.price)}</dd></div>
        ${i.last ? `<div><dt>마지막 배당락</dt><dd>${esc(i.last.date)} · 주당 ${cur(i.last.amt)}</dd></div>` : ""}
        ${i.next_ex ? `<div><dt>다음 배당락(예상)</dt><dd>${esc(i.next_ex)} 무렵</dd></div>` : ""}
        <div><dt>시뮬레이션 기본 성장률</dt><dd>연 ${i.growth}% <span class="tiny">(과거 성장률을 0~10%로 제한)</span></dd></div></dl>`}
      <div class="tiny">최근 1년 실제 지급 기준이에요. 배당락일 전날까지 사야 그 배당을 받아요.</div>
      <div class="dv-foot"><a class="btn" href="${stockHref(sym, mkt)}" data-close>종목 화면</a>
        <button type="button" class="btn dark" id="dvAddSim" ${inSim ? "disabled" : ""}>${inSim ? "구성에 들어 있어요" : "시뮬레이션에 담기"}</button></div>
      ${mkt === "kr" ? `<button type="button" class="btn wide" id="dvDca">매달 적립하기</button>` : `<div class="tiny">미국 종목은 자동 적립이 안 돼요. 같은 지수의 국내 상장 ETF는 적립할 수 있어요.</div>`}
      ${i.custom ? `<button type="button" class="btn sm soft wide" id="dvDrop">후보 목록에서 빼기</button>` : ""}</div>`, (box) => {
      $("#dvAddSim", box).onclick = () => { this.dvAdd(i); closeSheet(); toast(`${this.dvT(i)}: 시뮬레이션 구성에 담았어요`); if (D.tab === "sim") this.dvTab(); };
      if ($("#dvDca", box)) $("#dvDca", box).onclick = () => dcaOne(sym, i.name);
      if ($("#dvDrop", box)) $("#dvDrop", box).onclick = async () => {
        try { await run(() => api("DELETE", `/api/div/custom/${mkt}/${encodeURIComponent(sym)}`), "후보 목록에서 뺐어요"); } catch (e) { return; }
        D.uni = null; closeSheet(); this.dvTab();
      };
    });
  },
  dvAdd(i) {
    const D = this.dv;
    if (!D.assets) D.assets = [];
    if (D.assets.some((a) => a.mkt === i.mkt && a.sym === i.sym)) return;
    D.assets.push({ mkt: i.mkt, sym: i.sym, weight: D.assets.length ? 10 : 100 });
    D.preset = "custom"; this.dvSave();
  },
  dvPreset(id) {
    const D = this.dv, U = D.uni || {};
    let src = null;
    if (id === "mine") src = ((D.mine || {}).rows || []).filter((r) => r.value > 0).slice(0, 12).map((r) => ({ mkt: r.mkt, sym: r.sym, weight: r.value, name: r.name }));
    else if (id === "dca") { const by = {}, nm = {}; ((D.dca || {}).plans || []).filter((x) => x.enabled).forEach((x) => { by[x.code] = (by[x.code] || 0) + x.amount; nm[x.code] = x.name; }); src = Object.entries(by).map(([sym, weight]) => ({ mkt: "kr", sym, weight, name: nm[sym] })); }
    else if (String(id).startsWith("ai:")) src = ((D.plans || []).find((p) => "ai:" + p.id === id) || {}).assets;
    else src = ((U.presets || []).find((p) => p.id === id) || {}).assets;
    if (!src || !src.length) return false;
    const tw = src.reduce((s, a) => s + (a.weight || 0), 0) || 1;
    D.assets = src.map((a) => Object.assign({ mkt: a.mkt, sym: a.sym, weight: Math.round(a.weight / tw * 1000) / 10 }, a.name && !this.dvFind(a.mkt, a.sym) ? { name: a.name } : {}));
    D.preset = id; this.dvSave();
    return true;
  },

  // ----- 시뮬레이션 -----
  DV_HUES: ["indigo", "teal", "amber", "coral", "violet", "sky", "pink", "lime"],
  async dvSimView() {
    const D = this.dv, U = await this.dvUni(), el = $("#dvBody");
    if (!el || D.tab !== "sim") return;
    if (!D.plans) { try { D.plans = await GET("/api/div/plans"); } catch (e) { D.plans = []; } }
    if (!D.mine) { try { D.mine = await GET("/api/div/mine"); } catch (e) { D.mine = { rows: [] }; } }
    if (!D.dca) { try { D.dca = await GET("/api/dca/plans"); } catch (e) { D.dca = { plans: [], monthly: 0 }; } }
    if (!D.assets || !D.assets.length) this.dvPreset(D.preset && D.preset !== "custom" ? D.preset : "growth") || this.dvPreset("growth");
    if (!$("#dvBody") || D.tab !== "sim") return;
    const A = (v) => Math.round(v / 1e4), amtChips = [[5e7, "5천만"], [1e8, "1억"], [3e8, "3억"], [5e8, "5억"], [1e9, "10억"]];
    const L = D.live, lives = (U.presets || []).filter((p) => p.group === "live").map((p) => [p.id, p.name]);
    const sets = [...(U.presets || []).filter((p) => p.group !== "live").map((p) => [p.id, p.name]), ...(D.mine.rows || []).some((r) => r.value > 0) ? [["mine", "내 보유 종목"]] : [],
      ...(D.dca.plans || []).some((x) => x.enabled) || D.preset === "dca" ? [["dca", "내 적립 플랜"]] : [],
      ...(D.plans || []).slice(0, 3).map((p) => ["ai:" + p.id, "AI · " + p.title])];
    el.innerHTML = `
      <section class="card dv-in">
        <h2>얼마를, 얼마나 오래</h2>
        <label class="field">처음 넣는 돈<span class="dv-unit"><input id="dvAmt" inputmode="numeric" autocomplete="off" value="${A(D.amount)}"><i>만원</i><em id="dvAmtT">${bigNum(D.amount)}원</em></span></label>
        <div class="chips dv-amt">${amtChips.map(([v, t]) => `<button type="button" class="chip" data-amt="${v}" aria-pressed="${D.amount === v}">${t}</button>`).join("")}</div>
        <label class="field">매달 더 넣는 돈(선택)<span class="dv-unit"><input id="dvAdd" inputmode="numeric" autocomplete="off" value="${D.add ? A(D.add) : ""}" placeholder="0"><i>만원</i></span></label>
        <div class="dv-lab">기간</div>${seg("배당기간", [["5", "5년"], ["10", "10년"], ["20", "20년"], ["30", "30년"]], String(D.years), "on-card")}
        <div class="lr-auto dv-re"><span><b>받은 배당을 다시 투자</b><small>끄면 배당을 생활비로 쓰는 경우예요. 결과에는 두 경우를 함께 보여 줘요.</small></span>
          <button type="button" class="switch" role="switch" id="dvRe" aria-checked="${D.reinvest}" aria-label="배당 재투자"><span></span></button></div>
      </section>
      <section class="card dv-pf">
        <div class="card-head"><h2>어디에 넣을까</h2><button type="button" class="btn sm soft" data-dvgo="ai"><span class="ai-spark" aria-hidden="true">✦</span> AI에게 맡기기</button></div>
        <div class="dv-sets" role="group" aria-label="구성 고르기">${sets.map(([id, t]) => `<button type="button" class="chip" data-set="${esc(id)}" aria-pressed="${D.preset === id}">${esc(t)}</button>`).join("")}</div>
        ${lives.length ? `<div class="dv-lab dv-sets-l">모은 돈으로 생활할 때</div>
        <div class="dv-sets" role="group" aria-label="생활비로 쓰는 구성 고르기">${lives.map(([id, t]) => `<button type="button" class="chip" data-set="${esc(id)}" data-live="1" aria-pressed="${D.preset === id}">${esc(t)}</button>`).join("")}</div>` : ""}
        <div class="tiny" id="dvSetDesc"></div>
        <div id="dvAssets"></div>
        <div class="dv-pf-foot"><button type="button" class="btn sm" id="dvPick">+ 종목 추가</button><span id="dvSum"></span></div>
      </section>
      <section class="card dv-live-in">
        <div class="card-head"><div><h2>생활비로 살아 보기</h2><div class="tiny">이 돈에서 생활비를 꺼내 쓰면 몇 년을 버티는지, 평소와 하락장 두 경우로 계산해요.</div></div>
          <button type="button" class="switch" role="switch" id="dvLive" aria-checked="${!!L.on}" aria-label="생활비로 살아 보기"><span></span></button></div>
        ${L.on ? `<label class="field">한 달 생활비(지금 물가)<span class="dv-unit"><input id="dvSpend" inputmode="numeric" autocomplete="off" value="${L.spend ? A(L.spend) : ""}" placeholder="200"><i>만원</i></span></label>
        <div class="chips dv-spend">${[[1.5e6, "150만"], [2e6, "200만"], [3e6, "300만"], [4e6, "400만"]].map(([v, t]) => `<button type="button" class="chip" data-spend="${v}" aria-pressed="${L.spend === v}">${t}</button>`).join("")}</div>
        <div class="dv-lab">버텨야 하는 기간</div>${seg("배당생활기간", [["20", "20년"], ["30", "30년"], ["40", "40년"]], String(L.years || 30), "on-card")}
        <div class="dv-lab">건강보험</div>${seg("배당건보", [["local", "직장 없음(지역가입)"], ["work", "직장 다님"]], L.health === "work" ? "work" : "local", "on-card")}
        <div class="fields"><label class="field">다른 수입(알바·연금)<span class="dv-unit"><input id="dvOther" inputmode="numeric" autocomplete="off" value="${L.other ? A(L.other) : ""}" placeholder="0"><i>만원</i></span></label>
          <label class="field">몇 년차부터<span class="dv-unit"><input id="dvOtherFrom" inputmode="numeric" autocomplete="off" value="${L.from || 1}"><i>년차</i></span></label></div>
        <div class="tiny">생활비와 다른 수입은 해마다 물가만큼(2.5%) 오른다고 봐요. 배당으로 먼저 내고, 모자라면 현금성 자산부터 팔아요.</div>` : ""}
      </section>
      <details class="card dv-assume"><summary><h2>가정</h2><span class="tiny" id="dvAsT"></span></summary>
        <div class="dv-lab">주가 상승(연)</div>${seg("배당주가", [["0", "0%"], ["3", "3%"], ["5", "5%"], ["7", "7%"]], String(D.pg), "on-card")}
        <div class="dv-lab">배당 성장</div>${seg("배당성장", [["0", "안 늘어남"], ["0.5", "과거의 절반"], ["1", "과거만큼"]], String(D.gs), "on-card")}
        <div class="dv-lab">환율 변화(연)</div>${seg("배당환율", [["-2", "−2%"], ["0", "그대로"], ["2", "+2%"]], String(D.fxc), "on-card")}
        <div class="tiny">세금은 국내 ${U.tax.kr}%, 미국 상장 종목 ${U.tax.us}% 원천징수로 계산해요. 배당 성장은 종목마다 과거 5년 성장률(0~10%로 제한)을 써요. 채권은 주가가 오르지 않고, 성장 ETF는 위 가정보다 연 3%p 더 오른다고 봐요. 커버드콜은 분배금과 주가를 합친 수익이 성장 ETF의 주가 상승과 같다고 봐서, 분배율이 그보다 높으면 그만큼 원금과 분배금이 해마다 줄어요. 지금 환율 ${won(U.fx)}원.</div>
      </details>
      <div id="dvOut"></div>`;
    this.dvAssetsDraw(); this.dvAsText();
    const num = (v) => Number(String(v).replace(/[^\d.]/g, "")) || 0;
    el.oninput = (e) => {
      const t = e.target;
      if (t.id === "dvAmt") { D.amount = num(t.value) * 1e4; $("#dvAmtT").textContent = bigNum(D.amount) + "원"; $$(".dv-amt .chip", el).forEach((c) => c.setAttribute("aria-pressed", String(+c.dataset.amt === D.amount))); }
      else if (t.id === "dvAdd") D.add = num(t.value) * 1e4;
      else if (t.id === "dvSpend") { L.spend = num(t.value) * 1e4; $$(".dv-spend .chip", el).forEach((c) => c.setAttribute("aria-pressed", String(+c.dataset.spend === L.spend))); }
      else if (t.id === "dvOther") L.other = num(t.value) * 1e4;
      else if (t.id === "dvOtherFrom") L.from = Math.min(40, Math.max(1, Math.round(num(t.value)) || 1));
      else if (t.dataset.w != null) { D.assets[+t.dataset.w].weight = num(t.value); D.preset = "custom"; this.dvSumDraw(); $$("[data-set]", el).forEach((c) => c.setAttribute("aria-pressed", "false")); $("#dvSetDesc").textContent = "직접 고친 구성이에요."; }
      else if (t.dataset.y != null) { const a = D.assets[+t.dataset.y]; if (t.value === "") delete a.yield; else a.yield = num(t.value); }
      else return;
      this.dvSave(); this.dvSimSoon();
    };
    el.onclick = (e) => {
      const q = (s) => e.target.closest(s);
      let b;
      if ((b = q("[data-dvgo]"))) { D.tab = b.dataset.dvgo; this.dvSave(); return route(); }
      if ((b = q("[data-amt]"))) { D.amount = +b.dataset.amt; $("#dvAmt").value = A(D.amount); $("#dvAmtT").textContent = bigNum(D.amount) + "원"; $$(".dv-amt .chip", el).forEach((c) => c.setAttribute("aria-pressed", String(c === b))); this.dvSave(); return this.dvSim(); }
      if (q("#dvLive")) { L.on = !L.on; if (L.on && !L.spend) L.spend = 2000000; this.dvSave(); return this.dvSimView(); }
      if ((b = q("[data-spend]"))) { L.spend = +b.dataset.spend; $("#dvSpend").value = A(L.spend); $$(".dv-spend .chip", el).forEach((c) => c.setAttribute("aria-pressed", String(c === b))); this.dvSave(); return this.dvSim(); }
      if (q("#dvRe")) { D.reinvest = !D.reinvest; $("#dvRe").setAttribute("aria-checked", String(D.reinvest)); this.dvSave(); return this.dvSim(); }
      if ((b = q("[data-set]"))) {
        if (!this.dvPreset(b.dataset.set)) return toast("이 구성을 불러오지 못했어요", true);
        if (b.dataset.set === "dca" && D.dca.monthly && D.add !== D.dca.monthly) { D.add = D.dca.monthly; $("#dvAdd").value = A(D.add); this.dvSave(); toast(`매달 더 넣는 돈을 적립 금액 ${won(D.add)}원으로 맞췄어요`); }
        if (b.dataset.live && !L.on) { L.on = true; this.dvSave(); return this.dvSimView(); }      // 생활비용 구성을 고르면 생활비 계산을 함께 켠다
        $$("[data-set]", el).forEach((c) => c.setAttribute("aria-pressed", String(c === b))); this.dvAssetsDraw(); return this.dvSim();
      }
      if (q("#dvToDca")) return dcaDraftSheet({ total: D.add || 0, assets: ((D.sim || {}).assets || D.assets).map((a) => ({ mkt: a.mkt, sym: a.sym, name: a.name || (this.dvFind(a.mkt, a.sym) || {}).name || "", weight: a.weight })) });
      if ((b = q("[data-rm]"))) { D.assets.splice(+b.dataset.rm, 1); D.preset = "custom"; this.dvSave(); this.dvAssetsDraw(); return this.dvSim(); }
      if (q("#dvNorm")) { const tw = D.assets.reduce((s, a) => s + (a.weight || 0), 0) || 1; D.assets.forEach((a) => (a.weight = Math.round(a.weight / tw * 1000) / 10)); this.dvSave(); this.dvAssetsDraw(); return this.dvSim(); }
      if (q("#dvPick")) return this.dvPicker();
      if ((b = q("[data-asd]"))) { const a = D.assets[+b.dataset.asd]; return this.dvDetail(a.mkt, a.sym); }
      if ((b = q("[data-view]"))) { D.view = b.dataset.view; return this.dvOutDraw(); }
      if ((b = q("#dvMon [data-mi]"))) return this.dvMonthSheet(+b.dataset.mi);
      if (q("#dvMoreY")) { D.allY = !D.allY; return this.dvOutDraw(); }
    };
    bindSeg(el, "배당기간", (v) => { D.years = +v; this.dvSave(); this.dvSim(); });
    bindSeg(el, "배당주가", (v) => { D.pg = +v; this.dvSave(); this.dvAsText(); this.dvSim(); });
    bindSeg(el, "배당성장", (v) => { D.gs = +v; this.dvSave(); this.dvAsText(); this.dvSim(); });
    bindSeg(el, "배당환율", (v) => { D.fxc = +v; this.dvSave(); this.dvAsText(); this.dvSim(); });
    bindSeg(el, "배당건보", (v) => { L.health = v; this.dvSave(); this.dvSim(); });
    bindSeg(el, "배당생활기간", (v) => { L.years = +v; this.dvSave(); this.dvSim(); });
    this.dvSim();
  },
  dvAsText() { const D = this.dv, t = $("#dvAsT"); if (t) t.textContent = `주가 연 ${D.pg}% · 배당 성장 ${D.gs === 1 ? "과거만큼" : D.gs ? "과거의 절반" : "없음"} · 환율 ${D.fxc ? (D.fxc > 0 ? "+" : "−") + Math.abs(D.fxc) + "%" : "그대로"}`; },
  dvAssetsDraw() {
    const D = this.dv, box = $("#dvAssets"), HU = this.DV_HUES;
    if (!box) return;
    const set = ((D.uni || {}).presets || []).find((p) => p.id === D.preset), plan = (D.plans || []).find((p) => "ai:" + p.id === D.preset);
    $("#dvSetDesc").textContent = set ? `${set.desc} · 위험 ${set.risk}` : plan ? plan.summary : D.preset === "mine" ? "지금 보유한 종목을 평가액 비중 그대로 넣었어요."
      : D.preset === "dca" ? "적립 플랜의 매달 금액 비율 그대로예요. 처음 넣는 돈은 지금 보유분, 매달 더 넣는 돈은 적립 금액으로 보면 돼요." : "직접 고친 구성이에요.";
    const simA = (D.sim || {}).assets || [], dn = {}; ((D.dca || {}).plans || []).forEach((x) => (dn[x.code] = x.name));
    this._dvWait = false;
    $$("[data-set]").forEach((c) => c.setAttribute("aria-pressed", String(c.dataset.set === D.preset)));
    box.innerHTML = D.assets.map((a, i) => { const u = this.dvFind(a.mkt, a.sym), f = u || simA.find((x) => x.mkt === a.mkt && x.sym === a.sym) || {}, wait = !f.sym;
      if (wait) this._dvWait = true;
      return `<div class="dv-as"><span class="dv-sw h-${HU[i % HU.length]}"></span>
        <button type="button" class="dv-as-n" data-asd="${i}"><b>${esc(u ? this.dvT(u) : a.name || (a.mkt === "kr" && dn[a.sym]) || (f.sym ? this.dvT(f) : a.sym))}</b><small>${wait ? "배당 자료를 확인하는 중" : f.error || f.yield == null ? "배당 자료 없음 · 배당률을 직접 넣어 주세요" : `${esc(this.dvS(f))}${esc(f.freq_name)} · ${f.yield}% · 성장 ${f.growth}%`}</small></button>
        ${!wait && (f.error || f.yield == null) ? `<label class="dv-num y"><input data-y="${i}" inputmode="decimal" aria-label="${esc(a.sym)} 가정 배당률" value="${a.yield ?? ""}" placeholder="배당률"><i>%</i></label>` : ""}
        <label class="dv-num"><input data-w="${i}" inputmode="decimal" aria-label="${esc(f.name || a.sym)} 비중" value="${a.weight}"><i>%</i></label>
        <button type="button" class="dv-x" data-rm="${i}" aria-label="${esc(f.name || a.sym)} 빼기">×</button></div>`; }).join("") || `<div class="empty">종목을 추가해 주세요</div>`;
    this.dvSumDraw();
  },
  dvSumDraw() {
    const D = this.dv, el = $("#dvSum");
    if (!el) return;
    const tw = Math.round(D.assets.reduce((s, a) => s + (a.weight || 0), 0) * 10) / 10;
    el.innerHTML = Math.abs(tw - 100) < 0.11 ? `<b class="ok">합계 100%</b>` : `<b class="warn">합계 ${tw}%</b><button type="button" class="lnk" id="dvNorm">100%로 맞추기</button>`;
  },
  dvPicker() {
    const D = this.dv, U = D.uni, has = (i) => D.assets.some((a) => a.mkt === i.mkt && a.sym === i.sym);
    const groups = Object.entries(U.cats).map(([k, name]) => [name, U.items.filter((i) => i.cat === k)]).filter(([, xs]) => xs.length);
    openSheet("종목 추가", `<div class="dv-pick">${groups.map(([name, xs]) => `<h3 class="lr-h">${esc(name)}</h3><div class="list">${xs.map((i) => `<button type="button" class="item" data-pk="${i.mkt}:${esc(i.sym)}" ${has(i) ? "disabled" : ""}>
        <span class="main"><b>${esc(this.dvT(i))}</b><span class="sub">${esc(this.dvS(i))}${i.error ? "자료 없음" : `${esc(i.freq_name)} · ${this.dvPays(i.months)}`}</span></span>
        <span class="end"><b>${i.yield == null ? "-" : i.yield + "%"}</b><span class="tiny">${has(i) ? "담김" : i.growth ? `성장 ${i.growth}%` : ""}</span></span></button>`).join("")}</div>`).join("")}
      <div class="tiny">여기 없는 종목은 '종목' 탭에서 직접 넣을 수 있어요.</div></div>`, (box) => {
      box.onclick = (e) => { const b = e.target.closest("[data-pk]"); if (!b || b.disabled) return; const [mk, sy] = b.dataset.pk.split(":"); this.dvAdd(this.dvFind(mk, sy)); closeSheet(); this.dvAssetsDraw(); this.dvSim(); };
    });
  },
  dvSimSoon() { clearTimeout(this._dvT); this._dvT = setTimeout(() => this.dvSim(), 450); },
  async dvSim() {
    const D = this.dv, out = $("#dvOut");
    if (!out) return;
    clearTimeout(this._dvT);
    const seq = (this._dvSeq = (this._dvSeq || 0) + 1);
    if (!D.sim) out.innerHTML = `<div class="empty loading">계산하는 중</div>`; else out.classList.add("busy");
    try {
      const R = await api("POST", "/api/div/simulate", { amount: D.amount, monthly_add: D.add, years: D.years, reinvest: D.reinvest, price_growth: D.pg, growth_scale: D.gs, fx_change: D.fxc,
        live: D.live && D.live.on ? { spend: D.live.spend, other: D.live.other, other_from: D.live.from, health: D.live.health, years: D.live.years || 30 } : null,
        assets: D.assets.map((a) => ({ mkt: a.mkt, sym: a.sym, weight: a.weight, yield: a.yield, name: a.name })) });
      if (seq !== this._dvSeq) return;
      D.sim = R; D.simErr = "";
      // 후보 목록에 없는 종목(보유·적립 종목)은 계산 결과에 실려 온 자료로 구성 줄을 채운다(입력 중이면 건드리지 않는다)
      if (this._dvWait && $("#dvAssets") && !$("#dvAssets").contains(document.activeElement)) this.dvAssetsDraw();
    } catch (e) {
      if (e instanceof Locked || e instanceof NeedConnect) throw e;
      if (seq !== this._dvSeq) return;
      D.sim = null; D.simErr = e.message;
    }
    this.dvOutDraw();
  },
  dvOutDraw() {
    const D = this.dv, R = D.sim, out = $("#dvOut"), HU = this.DV_HUES;
    if (!out) return;
    out.classList.remove("busy");
    if (!R) { out.innerHTML = `<div class="notice warn">${esc(D.simErr || "계산하지 못했어요")}</div>`; return; }
    const S = R.summary, N = R.years, col = {}, nm = {};
    R.assets.forEach((a, i) => { col[a.sym] = "h-" + HU[i % HU.length]; nm[a.sym] = this.dvT(a); });
    const last = D.view === "last" && N > 1, ms = last ? R.last_months : R.months;
    const strip = ms.map((m) => ({ label: `${+m.ym.slice(5)}월`, total: m.net, parts: m.by.map((b) => ({ c: col[b.sym], v: b.net })) }));
    const lo = Math.min(...ms.map((m) => m.net)), hi = Math.max(...ms.map((m) => m.net));
    const A = R.reinvest ? R.yearly : R.other, B = R.reinvest ? R.other : R.yearly;       // A: 재투자함, B: 안 함
    const ratio = B[N - 1].monthly ? A[N - 1].monthly / B[N - 1].monthly : 0;
    const pickY = D.allY ? R.yearly.map((y) => y.year) : [1, 2, 3, 5, 7, 10, 15, 20, 25, 30, 35, 40].filter((y) => y <= N);
    if (!pickY.includes(N)) pickY.push(N);
    const fxNow = R.fx_now || 1400;
    out.innerHTML = `
      ${R.live ? this.dvLiveDraw(R) : (R.overlap || []).map((o) => `<div class="notice">${esc(o.base)} 한 지수에 ${o.weight}%가 몰려 있어요(${o.syms.map((x) => esc(nm[x] || x)).join(", ")}). ${o.both ? "커버드콜과 성장 ETF가 같은 지수라 " : ""}오를 때도 같이 오르지만 떨어질 때도 같이 떨어져요.</div>`).join("")}
      ${S.missing.length ? `<div class="notice warn">배당 자료가 없는 종목이 있어요(${S.missing.map((s) => esc(nm[s] || s)).join(", ")}). 위 구성에서 배당률을 직접 넣으면 계산에 들어가요.</div>` : ""}
      <section class="card dv-res">
        <div class="dv-big"><span>첫해에 한 달 평균(세후)</span><b data-count="${S.first_monthly}">${won(S.first_monthly)}원</b>
          <small>세전 배당률 ${S.yield}%${S.yield_net == null ? "" : ` · 세후 ${S.yield_net}%`} · ${S.amount ? `${bigNum(S.amount)}원 기준` : `매달 ${bigNum(S.monthly_add)}원 적립 기준`}</small></div>
        <div class="sc-kpi dv-kpi"><div><span>${N}년 뒤 한 달 평균</span><b>${won(S.last_monthly)}<small>원</small></b></div>
          <div><span>${N}년 뒤 평가액</span><b>${bigNum(S.last_value)}<small>원</small></b></div>
          <div><span>${N}년간 받은 배당</span><b>${bigNum(S.total_net)}<small>원</small></b></div></div>
        <div class="tiny">넣은 돈 합계 ${bigNum(S.invested)}원 · 낸 세금 ${bigNum(S.total_tax)}원 · ${R.reinvest ? "배당을 다시 투자한 경우" : "배당을 쓰고 다시 투자하지 않은 경우"}</div>
      </section>
      <section class="card">
        <div class="card-head"><h2>매달 얼마나 들어오나</h2>${N > 1 ? `<div class="dv-view" role="group"><button type="button" data-view="first" aria-pressed="${!last}">1년차</button><button type="button" data-view="last" aria-pressed="${last}">${N}년차</button></div>` : ""}</div>
        ${this.dvStrip(strip, 'id="dvMon"')}
        <div class="dv-leg">${R.assets.map((a) => `<span><i class="dv-sw ${col[a.sym]}"></i>${esc(this.dvT(a))} <b>${Math.round(a.weight)}%</b></span>`).join("")}</div>
        <div class="tiny">${this.dvYm(ms[0].ym)}부터 12개월 · 가장 적은 달 ${won(lo)}원, 가장 많은 달 ${won(hi)}원${hi > lo * 1.8 && lo >= 0 ? " · 달마다 차이가 커요. 월배당 종목을 섞으면 고르게 돼요" : ""}. 막대를 누르면 종목별 금액이 나와요.</div>
      </section>
      <section class="card">
        <h2>다시 투자하면 어떻게 달라지나</h2>
        ${this.dvChart(A.map((y) => y.monthly), B.map((y) => y.monthly), N)}
        <div class="dv-leg"><span><i class="dv-ln a"></i>다시 투자 <b>${N}년 뒤 월 ${won(A[N - 1].monthly)}원</b></span><span><i class="dv-ln b"></i>그냥 받아 씀 <b>월 ${won(B[N - 1].monthly)}원</b></span></div>
        <div class="tiny">${ratio > 1.02 ? `다시 투자하면 ${N}년 뒤 월 배당이 ${ratio.toFixed(1)}배예요. ` : ""}평가액은 다시 투자하면 ${bigNum(A[N - 1].value)}원, 안 하면 ${bigNum(B[N - 1].value)}원이에요.</div>
        <div class="dv-ms">${(() => { const ms = R.milestones.filter((m) => m.goal || m.target > S.first_monthly * 0.7), g = ms.find((m) => m.goal);
          const pick = ms.filter((m) => !m.goal).slice(0, g ? 2 : 3).concat(g ? [g] : []).sort((a, b) => a.target - b.target);
          const yr = (v) => (v ? v + "년차" : "못 닿음");
          return pick.map((m) => `<div class="${m.goal ? "goal" : ""}"><span>${m.goal ? "내 목표 " : ""}월 ${bigNum(m.target)}원</span><b>${yr(m.year)}</b><small>${R.reinvest ? "안 하면" : "다시 투자하면"} ${yr(m.other)}</small></div>`).join(""); })()}</div>
      </section>
      <section class="card">
        <div class="card-head"><h2>해마다</h2><button type="button" class="lnk" id="dvMoreY">${D.allY ? "주요 해만" : "모든 해 보기"}</button></div>
        <div class="dv-tbl" role="table"><div class="h" role="row"><span>연차</span><span>넣은 돈</span><span>평가액</span><span>연 배당(세후)</span><span>월평균</span></div>
          ${R.yearly.filter((y) => pickY.includes(y.year)).map((y) => `<div role="row"><span>${y.year}년</span><span>${bigNum(y.invested)}</span><span>${bigNum(y.value)}</span><span>${bigNum(y.net)}</span><b>${won(y.monthly)}</b></div>`).join("")}</div>
      </section>
      <section class="card">
        <h2>가정이 달라지면</h2>
        <div class="dv-sc">${R.scenarios.map((s) => `<div class="${s.name === "기본" ? "base" : ""}"><span><b>${esc(s.name)}</b><small>${esc(s.note)}</small></span><em>첫해 ${won(s.first)}원</em><strong>${N}년 뒤 월 ${won(s.last)}원</strong></div>`).join("")}</div>
        ${S.us_weight > 0 ? `<h3 class="lr-h">환율에 따라(첫해 월평균)</h3>
          <div class="dv-fx">${R.fx.map((f) => `<div class="${f.chg === 0 ? "now" : ""}"><span>${won(fxNow * (1 + f.chg / 100))}원</span><b>${won(f.monthly)}</b><small>${f.chg ? pct(f.chg, 0) : "지금"}</small></div>`).join("")}</div>
          <div class="tiny">미국 종목이 ${S.us_weight}%라, 환율이 10% 움직이면 원화 배당도 그 비중만큼 움직여요.</div>` : ""}
        <div class="dv-cut"><span>배당이 ${R.cut.pct}% 깎이면</span><b>첫해 월 ${won(R.cut.first)}원</b><small>${N}년 뒤 월 ${won(R.cut.last)}원</small></div>
      </section>
      ${(() => { const kr = R.assets.filter((x) => x.mkt === "kr").length, us = R.assets.length - kr;
        return `<section class="card dv-todca"><div><h2>이 구성으로 매달 적립하기</h2>
          <div class="tiny">${kr ? `비중대로 금액을 나눠 종목마다 적립 플랜을 만들어요. 국내 종목 ${kr}개가 대상이에요${us ? `(미국 ${us}개는 자동 적립이 안 돼요)` : ""}.` : "자동 적립은 국내 상장 종목만 돼요. 이 구성에는 국내 종목이 없어요."}</div></div>
          <button type="button" class="btn dark" id="dvToDca" ${kr ? "" : "disabled"}>적립 플랜 만들기</button></section>`; })()}
      ${S.over_line_year ? `<div class="notice">${S.over_line_year === 1 ? "첫해부터" : S.over_line_year + "년차부터"} 세전 배당이 연 2,000만 원을 넘어요. 금융소득종합과세 대상이 되고 건강보험료가 달라질 수 있어, 여기 계산보다 세금이 늘 수 있어요. 국내 고배당 기업 배당은 분리과세를 고를 수 있는 제도가 있으니 세무 상담을 권해요.</div>` : ""}
      <div class="tiny" style="padding:0 3px">최근 1년 실제 배당과 위 가정으로 계산한 추정이에요. 배당과 주가, 환율은 달라질 수 있고 수익을 보장하지 않아요. 세금은 원천징수만 반영했고, 투자 판단은 직접 해 주세요.</div>`;
    countUp(out);
  },
  // 월평균 배당 추이(두 경우 비교)
  // 생활비로 살아 보기 결과: 버티는지(평소·하락장), 첫해 생활비를 무엇으로 대는지, 짚어 볼 점
  dvLiveDraw(R) {
    const L = R.live, N = L.years, f = L.first, man = (v) => `${won(Math.round(v / 1e4))}만`;
    const need = Math.max(0, f.need - f.other) + f.fee, fromDiv = Math.min(f.div, need), parts = [["div", "배당(세후)", fromDiv], ["oth", "다른 수입", f.other], ["sell", "팔아서", Math.max(0, need - fromDiv)]];
    const tot = f.need + f.fee || 1;
    const head = L.code === "div" ? ["배당만으로 생활비가 나와요", `첫해 배당이 필요한 돈의 ${f.cover}%예요. 남는 배당은 다시 투자해요.`]
      : L.code === "mix" ? [`팔아서 보태며 ${N}년을 버텨요`, `배당이 필요한 돈의 ${f.cover}%라, 첫해에 자산의 ${L.rate}%를 팔아 메워요.`]
      : [`${L.dead}년차에 바닥나요`, `배당이 필요한 돈의 ${f.cover}%뿐이라 해마다 팔아 쓰다가 ${L.dead}년차에 다 써요.`];
    const pad = (rows) => Array.from({ length: N }, (_, i) => (rows[i] ? rows[i].value : 0));
    const a = pad(L.yearly), b = pad(L.bad);
    const alone = L.code === "div" ? "" : `<div class="dv-alone">팔지 않고 지내려면 <b>생활비를 월 ${man(L.alone.spend)} 원</b>으로 맞추거나${L.alone.amount ? `, 같은 구성으로 <b>${bigNum(L.alone.amount)}원</b>이 있어야 해요` : " 배당이 나오는 종목을 늘려야 해요"}.</div>`;
    return `<section class="card dv-live is-${L.code}">
        <div class="dv-live-h"><span class="dv-live-tag">${L.code === "div" ? "여유" : L.code === "mix" ? "가능" : "부족"}</span><div><b>${head[0]}</b><span>${head[1]}</span></div></div>
        <div class="dv-cov" role="img" aria-label="첫해 한 달 ${man(f.need + f.fee)} 원을 ${parts.filter((x) => x[2] > 0).map((x) => `${x[1]} ${man(x[2])}`).join(", ")}으로 대요">${parts.filter((x) => x[2] > 0).map(([k, , v]) => `<i class="${k}" style="flex:${Math.max(0.04, v / tot)}"></i>`).join("")}</div>
        <div class="dv-cov-l">${parts.filter((x) => x[2] > 0).map(([k, t, v]) => `<span><i class="${k}"></i>${t} <b>${man(v)}</b></span>`).join("")}</div>
        <div class="tiny">첫해 한 달에 필요한 돈 ${man(f.need + f.fee)} 원 = 생활비 ${man(f.need)}${f.fee ? ` + 건강보험료 약 ${man(f.fee)}` : ""}${f.div > need ? ` · 배당이 ${man(f.div - need)} 원 남아요` : ""}</div>
        <div class="sc-kpi dv-kpi"><div><span>하락장이 오면</span><b>${L.bad_dead ? `${L.bad_dead}년차 바닥` : `${N}년 버팀`}</b></div>
          <div><span>첫해 팔아 쓰는 비율</span><b>${L.rate ? L.rate + "%" : "0%"}</b></div>
          <div><span>현금성 자산</span><b>${L.cash.value ? (L.cash.months ? `${L.cash.months >= 99 ? "99+" : Math.round(L.cash.months)}개월치` : bigNum(L.cash.value) + "원") : "없음"}</b></div></div>
        <div class="dv-leg"><span><i class="dv-lg a"></i>평소 <b>${L.dead ? "바닥" : bigNum(L.last.value) + "원"}</b></span><span><i class="dv-lg b"></i>하락장 <b>${L.bad_dead ? "바닥" : bigNum(L.last.bad_value) + "원"}</b></span><span class="tiny">${N}년 뒤 남는 자산</span></div>
        ${this.dvChart(a, b, N, (hi) => `${bigNum(hi)}원`, "연차별 남는 자산: 평소와 하락장")}
        ${L.tips.length ? `<ul class="dv-tips">${L.tips.map((t) => `<li class="${esc(t.kind)}">${esc(t.text)}</li>`).join("")}</ul>` : ""}
        ${alone}
        <div class="tiny">하락장은 주가가 ${L.shock.down}년에 걸쳐 ${L.shock.drop}% 내리고, ${L.shock.flat}년 머문 뒤 ${L.shock.up}년에 걸쳐 돌아오는 경우예요(커버드콜은 ${L.shock.cc_back}%까지만 회복, 분배금도 함께 줄어요). 건강보험료는 배당에 붙는 몫만 ${L.rates.health}%로 어림했고(${L.health === "local" ? "지역가입자: 연 1천만 원을 넘으면 전액, 재산에 붙는 보험료는 빠져 있어요" : "직장가입자: 월급 외 소득 2천만 원 초과분"}), 미국 종목을 팔아 남긴 이익은 연 250만 원을 넘는 부분에 22%를 이듬해에 내는 것으로 넣었어요. 위 '가정'의 주가 상승을 바꾸면 결과가 크게 달라져요.</div>
      </section>`;
  },
  dvChart(a, b, n, top, aria) {
    const W = 600, H = 150, P = 10, hi = Math.max(1, ...a, ...b), x = (i) => (n > 1 ? i / (n - 1) : 0.5) * (W - 2 * P) + P, y = (v) => H - P - v / hi * (H - 2 * P);
    const line = (v) => v.map((p, i) => `${x(i).toFixed(1)},${y(p).toFixed(1)}`).join(" ");
    const ticks = [0, 0.5, 1].map((k) => `<line class="g" x1="0" x2="${W}" y1="${y(hi * k)}" y2="${y(hi * k)}" vector-effect="non-scaling-stroke"/>`).join("");
    return `<div class="dv-chart"><span class="dv-top">${top ? top(hi) : `월 ${bigNum(hi)}원`}</span><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="${aria || "연차별 월평균 배당: 재투자한 경우와 안 한 경우"}">
      ${ticks}<polygon class="fa" points="${P},${H - P} ${line(a)} ${x(n - 1)},${H - P}"/>
      <polyline class="lb" points="${line(b)}" vector-effect="non-scaling-stroke"/><polyline class="la" points="${line(a)}" vector-effect="non-scaling-stroke"/></svg>
      <div class="ax"><span>1년차</span>${n > 2 ? `<span>${Math.ceil(n / 2)}년차</span>` : ""}<span>${n}년차</span></div></div>`;
  },
  dvMonthSheet(i) {
    const D = this.dv, R = D.sim, ms = D.view === "last" && R.years > 1 ? R.last_months : R.months, m = ms[i], nm = {};
    R.assets.forEach((a) => (nm[a.sym] = this.dvT(a)));
    detailSheet({ title: `${this.dvYm(m.ym)} 배당`, head: `<b style="font-size:22px">${won(m.net)}원</b><div class="tiny">세후 · 세전 ${won(m.gross)}원</div>`,
      rows: m.by.length ? m.by.slice().sort((a, b) => b.net - a.net).map((b) => [nm[b.sym] || b.sym, won(b.net) + "원"]) : [["이 달에는 배당이 없어요", ""]] });
  },

  // ----- 종목 -----
  async dvList() {
    const D = this.dv, U = await this.dvUni(), el = $("#dvBody");
    if (!el || D.tab !== "list") return;
    const F = [["all", "전체"], ["month", "월배당"], ["us_etf", "미국 ETF"], ["cc", "커버드콜"], ["us_stock", "미국 주식"], ["kr_etf", "국내 ETF"], ["kr_stock", "국내 주식·리츠"]];
    const pass = (i) => D.f === "all" || (D.f === "month" ? i.freq === 12 : D.f === "cc" ? i.cat.endsWith("_cc") : D.f === "us_etf" ? ["us_etf", "us_bond"].includes(i.cat)
      : D.f === "us_stock" ? ["us_stock", "us_month"].includes(i.cat) : D.f === "kr_stock" ? ["kr_stock", "kr_reit"].includes(i.cat) : i.cat === D.f);
    const key = { yield: (i) => i.yield ?? -1, growth: (i) => (i.growth5 ?? i.growth3 ?? -99), streak: (i) => i.streak || 0, band: (i) => (i.band ? i.band.pct : -1) }[D.sort];
    let EV = null;
    try { EV = await GET("/api/div/events"); } catch (e) { EV = null; }
    if (!$("#dvBody") || D.tab !== "list") return;
    const rows = U.items.filter(pass).sort((a, b) => key(b) - key(a));
    el.innerHTML = `
      <section class="card">
        <div class="dv-sets">${F.map(([k, t]) => `<button type="button" class="chip" data-f="${k}" aria-pressed="${D.f === k}">${t}</button>`).join("")}</div>
        ${seg("배당정렬", [["yield", "배당률"], ["band", "5년 대비 높은"], ["growth", "배당 성장"], ["streak", "안 줄인 햇수"]], D.sort, "on-card")}
        ${U.failed ? `<div class="tiny">${U.failed}개 종목은 배당 자료를 받지 못했어요. <button type="button" class="lnk" id="dvReload">다시 받기</button></div>` : ""}
      </section>
      ${this.dvEvents(EV, 6)}
      <section class="card tight"><div class="list">${rows.map((i) => `<button type="button" class="item dv-row" data-dv="${i.mkt}:${esc(i.sym)}">
        <span class="main"><b>${esc(this.dvT(i))} ${i.custom ? `<span class="tag y">직접</span>` : ""}</b>
          <span class="sub">${esc(this.dvS(i))}${i.error ? (i.pending ? "자료를 받는 중" : "배당 자료 없음") : `${esc(i.freq_name)} · ${this.dvPays(i.months)}${i.streak >= 3 ? ` · ${i.streak}년째 유지·증가` : ""}`}</span>${i.error ? "" : this.dvDots(i.months)}</span>
        <span class="end"><b>${i.yield == null ? "-" : i.yield + "%"}</b>${D.sort === "band" || (i.band && (i.band.pct >= 80 || i.band.pct <= 20)) ? this.dvBandTag(i.band) : `<span class="tiny ${tone(i.growth5 ?? i.growth3)}">${(i.growth5 ?? i.growth3) == null ? "" : "성장 " + pct(i.growth5 ?? i.growth3, 1)}</span>`}</span></button>`).join("") || `<div class="empty">조건에 맞는 종목이 없어요</div>`}</div></section>
      <section class="card">
        <h2>다른 종목 넣기</h2>
        ${seg("배당시장", [["us", "미국"], ["kr", "국내"]], D.addM || "us", "on-card")}
        <div id="dvAddPick"></div>
        <div class="tiny">이름으로 찾아 누르면 바로 넣어요. 최근 10년 배당 내역을 받아 배당률·성장률·지급월을 계산해요.</div>
      </section>
      <div class="tiny" style="padding:0 3px">배당률은 최근 1년 실제 지급액 ÷ 현재가(세전)예요. 하루에 한 번 갱신하고, 종목 설명은 참고용이에요. 배당률이 유난히 높은 종목은 주가가 떨어졌거나 배당이 깎일 위험이 있는 경우가 많아요.</div>`;
    bindSeg(el, "배당정렬", (v) => { D.sort = v; this.dvList(); });
    const addPk = stockPick($("#dvAddPick"), { mkt: D.addM || "us", onChange: async (it) => {
      if (!it.length) return;
      let i;
      try { i = await run(() => api("POST", "/api/div/custom", { mkt: D.addM || "us", sym: it[0].sym, name: it[0].name === it[0].sym ? "" : it[0].name })); } catch (err) { return addPk.setMkt(D.addM || "us"); }
      D.uni = null; await this.dvList();
      if (i.error) toast(`${it[0].name}: ${i.error}`, true); else this.dvDetail(i.mkt, i.sym);
    } });
    bindSeg(el, "배당시장", (v) => { D.addM = v; addPk.setMkt(v); });
    el.onclick = async (e) => {
      const f = e.target.closest("[data-f]");
      if (f) { D.f = f.dataset.f; return this.dvList(); }
      if (e.target.closest("#dvReload")) { el.innerHTML = `<div class="empty loading">배당 자료를 다시 받는 중</div>`; D.uni = null; await this.dvUni(true); return this.dvList(); }
      const r = e.target.closest("[data-dv]");
      if (r) { const [mk, sy] = r.dataset.dv.split(":"); this.dvDetail(mk, sy); }
    };
  },

  // ----- AI 구성 -----
  async dvAi() {
    const D = this.dv, el = $("#dvBody");
    const [U, st] = await Promise.all([this.dvUni(), GET("/api/ai/status").catch(() => null)]);
    try { D.plans = await GET("/api/div/plans"); } catch (e) { D.plans = D.plans || []; }
    if (!el || D.tab !== "ai") return;
    const models = Object.entries((st || {}).models || {});
    if (!D.model || !models.some(([k]) => k === D.model)) D.model = (st || {}).model || (models[0] || [""])[0];
    el.innerHTML = `
      <section class="card dv-in">
        <h2>AI에게 배당 구성 맡기기</h2>
        <div class="tiny">후보 종목의 실제 배당 자료(배당률·성장률·지급월)를 Claude에게 보여 주고, 내 조건에 맞는 종목과 비중을 받아요. 받은 구성은 바로 시뮬레이션에 넣어 볼 수 있어요.</div>
        <div class="dv-cond"><span>투자금 <b>${bigNum(D.amount)}원</b></span>${D.add ? `<span>매달 <b>${bigNum(D.add)}원</b></span>` : ""}<span>${D.years}년</span><span>${D.reinvest ? "배당 재투자" : "배당은 생활비"}</span>${U.goal ? `<span>목표 월 <b>${bigNum(U.goal)}원</b></span>` : ""}
          <button type="button" class="lnk" data-dvgo="sim">바꾸기</button></div>
        <div class="dv-lab">원하는 방향</div>${seg("배당방향", [["growth", "배당 성장"], ["balance", "균형"], ["income", "매월 현금"], ["high", "고분배"]], D.style, "on-card")}
        <div class="dv-lab">어디에</div>${seg("배당지역", [["mix", "국내 + 미국"], ["us", "미국 상장"], ["kr", "국내 상장만"]], D.region, "on-card")}
        ${D.live && D.live.on && D.live.spend ? `<div class="notice">시뮬레이션의 생활비 계획(한 달 ${won(Math.round(D.live.spend / 1e4))}만 원, ${D.live.health === "work" ? "직장가입자" : "지역가입자"})도 함께 보내요. 생활비에 닿는지, 현금을 몇 개월치 둘지까지 따져 달라고 해요.</div>` : ""}
        <label class="field">더 알려 줄 것(선택)<input id="dvMemo" maxlength="300" autocomplete="off" value="${esc(D.memo)}" placeholder="예: 5년 뒤부터 배당을 생활비로 쓸 계획, 커버드콜은 빼 줘"></label>
        ${models.length > 1 ? `<label class="field">엔진<select id="dvModel">${models.map(([k, v]) => `<option value="${esc(k)}" ${k === D.model ? "selected" : ""}>${esc(String(v).split(" · ")[0])}</option>`).join("")}</select></label>` : ""}
        <label class="ap-web"><input type="checkbox" id="dvWeb" ${D.web ? "checked" : ""}><span><b>최근 배당 뉴스도 확인</b><small>웹 검색 최대 4회 · 배당 삭감·정책 변화 확인 · 비용↑</small></span></label>
        <button type="button" class="btn ap-go wide" id="dvGo"></button>
        <div id="dvCost" class="cm-slot"></div>
        <div class="tiny" id="dvWarn"></div>
      </section>
      <div id="dvAiOut"></div>
      <div id="dvPlans"></div>`;
    this.dvAiBtn(st); this.dvAiDraw(); this.dvPlansDraw();
    bindSeg(el, "배당방향", (v) => { D.style = v; this.dvSave(); });
    bindSeg(el, "배당지역", (v) => { D.region = v; this.dvSave(); });
    el.oninput = (e) => { if (e.target.id === "dvMemo") { D.memo = e.target.value; this.dvSave(); } };
    el.onchange = (e) => {
      if (e.target.id === "dvModel") { D.model = e.target.value; this.dvSave(); this.dvAiBtn(st); }
      if (e.target.id === "dvWeb") { D.web = e.target.checked; this.dvSave(); this.dvAiBtn(st); }
    };
    el.onclick = async (e) => {
      const q = (s) => e.target.closest(s);
      let b;
      if ((b = q("[data-dvgo]"))) { D.tab = b.dataset.dvgo; this.dvSave(); return route(); }
      if (q("#dvGo")) return this.dvAiRun(st);
      if ((b = q("[data-use]"))) {
        const p = b.dataset.use === "now" ? D.plan : (D.plans || []).find((x) => String(x.id) === b.dataset.use);
        if (!p) return;
        if (!(D.plans || []).some((x) => x.id === p.id)) D.plans = [p, ...(D.plans || [])];
        this.dvPreset("ai:" + p.id); D.sim = null; D.tab = "sim"; this.dvSave(); return route();
      }
      if ((b = q("[data-open]"))) { const p = (D.plans || []).find((x) => String(x.id) === b.dataset.open); if (p) { D.plan = p; D.text = p.text || ""; D.src = []; D.used = null; this.dvAiDraw(); $("#dvAiOut").scrollIntoView({ behavior: "smooth", block: "start" }); } return; }
      if ((b = q("[data-del]"))) {
        if (!confirm("이 AI 구성을 지울까요?")) return;
        try { await run(() => api("DELETE", `/api/div/plans/${b.dataset.del}`), "지웠어요"); } catch (err) { return; }
        D.plans = (D.plans || []).filter((x) => String(x.id) !== b.dataset.del); if (D.plan && String(D.plan.id) === b.dataset.del) D.plan = null;
        this.dvAiDraw(); return this.dvPlansDraw();
      }
      if ((b = q("[data-asd2]"))) { const [mk, sy] = b.dataset.asd2.split(":"); return this.dvDetail(mk, sy); }
      if (q("[data-todca]") && D.plan) return dcaDraftSheet({ title: D.plan.title, total: D.add || 0, assets: D.plan.assets.map((a) => ({ mkt: a.mkt, sym: a.sym, name: a.name, weight: a.weight })) });
    };
  },
  dvAiBtn(st) {
    const D = this.dv, go = $("#dvGo");
    if (!go) return;
    go.disabled = D.busy || !(st && st.configured);
    go.innerHTML = D.busy ? "AI가 구성을 짜는 중…" : `<span class="ai-spark" aria-hidden="true">✦</span> 배당 구성 받기`;
    $("#dvWarn").innerHTML = st && st.configured ? "" : `Claude API 키가 필요해요. <a href="#settings/ai">AI 설정 열기</a>`;
    aiMeter.get("divplan", D.model, D.web).then((m) => { if ($("#dvCost")) $("#dvCost").innerHTML = meterHTML(m); }).catch(() => {});
  },
  async dvAiRun(st) {
    const D = this.dv;
    if (D.busy) return;
    D.busy = true; D.text = ""; D.src = []; D.plan = null; D.used = null; D.err = ""; D.status = "후보 종목의 배당 자료를 모으는 중";
    this.dvAiBtn(st); this.dvAiDraw();
    let raf = 0;
    const paint = () => { raf = 0; this.dvAiDraw(true); };
    try {
      await aiStream({ kind: "divplan", amount: D.amount, monthly_add: D.add, years: D.years, reinvest: D.reinvest, style: D.style, region: D.region, memo: D.memo.trim(),
        live: D.live && D.live.on ? { spend: D.live.spend, other: D.live.other, other_from: D.live.from, health: D.live.health, years: D.live.years || 30 } : null,
        goal: (D.uni || {}).goal || 0, model: D.model, web_search: D.web }, (ev) => {
        if (ev.t === "status") D.status = ev.msg;
        else if (ev.t === "text") { D.text += ev.text; D.status = "구성을 쓰는 중"; }
        else if (ev.t === "reset") D.text = "";
        else if (ev.t === "source") D.src.push(ev);
        else if (ev.t === "plan") { if (ev.error) D.err = ev.error; else { D.plan = ev; D.plans = [ev, ...(D.plans || []).filter((x) => x.id !== ev.id)]; } }
        else if (ev.t === "done") { D.used = ev; aiMeter.clear(); }
        else if (ev.t === "error") D.err = ev.msg;
        if (!raf) raf = requestAnimationFrame(paint);
      });
    } catch (e) {
      if (e instanceof Locked) { D.busy = false; throw e; }
      if (e instanceof NeedConnect) { D.busy = false; location.hash = "#connect"; return; }
      D.err = e.message;
    } finally { if (raf) cancelAnimationFrame(raf); D.busy = false; D.status = ""; this.dvAiBtn(st); this.dvAiDraw(); this.dvPlansDraw(); }
  },
  dvAiDraw(streaming) {
    const D = this.dv, out = $("#dvAiOut"), HU = this.DV_HUES;
    if (!out) return;
    const P = D.plan, body = D.text.split("```")[0];
    if (!D.busy && !P && !D.err && !body) { out.innerHTML = ""; return; }
    out.innerHTML = `<section class="card dv-ai">
      ${D.busy ? `<div class="ai-status"><span class="ai-dot"></span><span>${esc(D.status)}</span></div>` : ""}
      ${D.err ? `<div class="notice warn">${esc(D.err)}</div>` : ""}
      ${body ? `<div class="ai-ans ap-text">${mdLite(body)}</div>` : ""}
      ${D.src.length ? `<details class="ai-src"><summary>찾아본 뉴스·자료 ${D.src.length}건</summary>${D.src.map((x) => `<a href="${safeUrl(x.url)}" target="_blank" rel="noopener noreferrer">${esc(x.title || x.url)}</a>`).join("")}</details>` : ""}
      ${P ? `<div class="dv-plan"><div class="dv-plan-h"><div><b>${esc(P.title)}</b><small>${esc(P.summary || "")}</small></div><span class="dv-y"><em>${P.yield}%</em><small>세전 배당률</small></span></div>
        <div class="dv-wbar" aria-hidden="true">${P.assets.map((a, i) => `<i class="dv-sw h-${HU[i % HU.length]}" style="flex:${a.weight}"></i>`).join("")}</div>
        <div class="dv-pa">${P.assets.map((a, i) => `<button type="button" data-asd2="${a.mkt}:${esc(a.sym)}"><span class="dv-sw h-${HU[i % HU.length]}"></span>
          <span class="main"><b>${esc(this.dvT(a))}${a.role ? ` <span class="tag">${esc(a.role)}</span>` : ""}</b><small>${esc(this.dvS(a))}${a.yield == null ? "배당 자료 없음" : `${esc(a.freq_name || "")} · 배당률 ${a.yield}%`}${a.reason ? "<br>" + esc(a.reason) : ""}</small></span>
          <strong>${a.weight}%</strong></button>`).join("")}</div>
        ${P.rebalance ? `<div class="tiny"><b>다시 맞추기</b> ${esc(P.rebalance)}</div>` : ""}
        ${(P.risks || []).length ? `<ul class="lr-ins bad">${P.risks.map((x) => `<li><b>위험</b> ${esc(x)}</li>`).join("")}</ul>` : ""}
        ${(P.notes || []).length ? `<ul class="lr-ins">${P.notes.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
        <button type="button" class="btn dark wide" data-use="${D.plans && D.plans.some((x) => x.id === P.id) ? P.id : "now"}">이 구성으로 시뮬레이션</button>
        ${P.assets.some((a) => a.mkt === "kr") ? `<button type="button" class="btn wide" data-todca="1">이 구성으로 적립 플랜 만들기</button>` : ""}
        <div class="tiny">배당률은 AI가 말한 값이 아니라 실제 자료로 다시 계산한 값이에요. AI는 주문하지 않아요.</div></div>` : ""}
      ${D.used && !streaming ? meterHTML(D.used, D.used) : ""}</section>`;
  },
  dvPlansDraw() {
    const D = this.dv, el = $("#dvPlans");
    if (!el) return;
    const ps = (D.plans || []).filter((p) => !D.plan || p.id !== D.plan.id);
    el.innerHTML = ps.length ? `<div class="sec-title">지난 AI 구성</div><section class="card tight"><div class="list">${ps.map((p) => `<div class="item dv-pl">
      <button type="button" class="main" data-open="${p.id}"><b>${esc(p.title)}</b><span class="sub">${esc(p.ts.slice(5, 16).replace("T", " "))} · ${p.assets.map((a) => esc(this.dvT(a).split(" ")[0])).slice(0, 3).join(", ")}${p.assets.length > 3 ? " 외" : ""}</span></button>
      <span class="end"><b>${p.yield}%</b><span class="dv-pl-a"><button type="button" class="lnk" data-use="${p.id}">시뮬레이션</button><button type="button" class="lnk" data-del="${p.id}" aria-label="${esc(p.title)} 지우기">지우기</button></span></span></div>`).join("")}</div></section>` : "";
  },

  async dca(box) {
    const d = await GET("/api/dca");
    if (investState.tab !== "dca") return;
    const h = d.history;
    box.innerHTML = `
      ${d.error ? `<div class="notice warn">${esc(d.error)}</div>` : ""}
      <section class="card">
        <h1 class="hero" style="font-size:18px">${d.plans.length ? `매달 <span class="hl">${won(d.monthly)}원</span>씩 모아<br>지금 ${won(d.value)}원이에요` : `매달 정한 날, 정한 금액만큼<br><span class="hl">자동으로</span> 사 모아요`}</h1>
        <div class="kv"><div>매수 원금<b>${won(d.cost)}</b></div><div>평가손익<b class="${tone(d.gain)}">${pct(d.gain_pct, 1)}</b></div><div>다음 매수<b>${d.schedule[0] ? korDate(d.schedule[0].date, false) : "-"}</b></div></div>
        ${h.length >= 2 ? `${lineChart([{ values: h.map((x) => x.value) }, { values: h.map((x) => x.cost), color: "#5B6676", dash: true, w: 2 }], { h: 90, label: "평가금과 원금 추이" })}
          <div class="legend tiny"><span><i class="sw" style="height:2px;width:11px;background:#1B2430"></i>평가금</span><span><i class="sw" style="height:0;width:11px;border-top:2px dashed #5B6676;border-radius:0"></i>원금</span></div>` : ""}
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
          <b>${esc(p.name)}</b><span class="sub">${p.enabled ? `매월 ${p.day}일 · ${won(p.amount)}원` : "쉬는 중"}${p.weight_goal ? ` · 목표 ${p.weight_goal}%` : ""}<span data-pdv="${esc(p.code)}"></span></span>
          <span class="sub ${tone(p.gain_pct)}" style="font-weight:700">${p.gain_pct == null ? "아직 없음" : `평가 ${won(p.value)} · ${pct(p.gain_pct)}`}</span></button>
        <button class="switch" role="switch" aria-checked="${!!p.enabled}" aria-label="${esc(p.name)} 자동 매수" data-toggle="${p.id}"><span></span></button></div>`).join("")
        : `<div class="empty">첫 플랜을 만들어 보세요<br><span class="tiny">예: TIGER 미국S&P500(360750) 매달 25일 50만 원</span></div>`}</div></section>
      ${d.plans.length ? AI_BTN("dca", "AI로 적립 플랜 점검") : `<section class="card dca-from"><h2>구성부터 정하고 싶다면</h2>
        <div class="tiny">배당 탭에서 고른 구성을 그대로 적립 플랜으로 옮길 수 있어요. 종목마다 금액을 비중대로 나눠 줘요.</div>
        <div class="btns"><button type="button" class="btn" data-dcago="sim">배당 구성에서 고르기</button><button type="button" class="btn" data-dcago="ai"><span class="ai-spark" aria-hidden="true">✦</span> AI 구성 받기</button></div></section>`}
      <div id="dcaDiv"></div>
      ${d.plans.some((p) => p.target_weight > 0) ? `<section class="card"><h2>목표 비중과 지금</h2>${d.plans.filter((p) => p.enabled).map((p) => `<div style="display:flex;flex-direction:column;gap:4px">
        <div style="display:flex;justify-content:space-between;font-size:11px"><b>${esc(p.name)}</b><span class="muted">${p.weight_now}% / 목표 ${p.weight_goal}%</span></div>
        <div style="position:relative;height:7px;background:var(--bg);border-radius:3.5px"><div style="width:${Math.min(100, p.weight_now)}%;height:7px;background:var(--ink);border-radius:3.5px"></div>
          <div style="position:absolute;top:-3px;left:${Math.min(98, p.weight_goal)}%;width:3px;height:12.5px;border-radius:2px;background:var(--hl);box-shadow:0 0 0 1px var(--ink)"></div></div></div>`).join("")}</section>` : ""}
      <section class="card"><h2>매수 일정</h2>${d.schedule.length ? d.schedule.map((s) => `<div style="display:flex;justify-content:space-between;font-size:11px"><span>${korDate(s.date)}</span><b>${won(s.amount)}원</b></div>`).join("") : `<div class="tiny">예정된 매수가 없어요</div>`}</section>
      <div class="sec-title">최근 매수 기록</div>
      <section class="card tight"><div class="list">${d.runs.length ? d.runs.slice(0, 10).map((r) => `<div class="item">
        <span class="ico">${r.run_ts.slice(5, 10).replace("-", ".")}</span>
        <span class="main"><b>${esc(r.name || "지운 플랜")}</b><span class="sub">${r.status === "done" ? `${r.qty}주 · ${won(r.price)}원` : esc(r.msg)}</span></span>
        <span class="end"><span class="tag ${r.status === "done" ? "g" : "warn"}">${{ done: "매수", skipped: "건너뜀", failed: "실패" }[r.status]}</span></span></button>`).join("") : `<div class="empty">아직 기록이 없어요</div>`}</div></section>`;
    fab("플랜", () => planSheet(null));
    bindSeg(box, "매수 방식", async (v) => { await run(() => api("PUT", "/api/dca/config", { mode: v }), "매수 방식을 바꿨어요"); this.fill(); });
    box.onclick = async (e) => {
      const b = e.target.closest("[data-act],[data-toggle],[data-pedit],[data-dcago],#dcaGoal,#dcaAgain");
      if (!b) return;
      if (b.dataset.dcago) return this.dcaGo(b.dataset.dcago);
      if (b.id === "dcaGoal") { this.dvState(); return this.dvGoalSheet((this.dcaO || {}).goal || 0); }
      if (b.id === "dcaAgain") return this.dcaOutlook();
      if (b.dataset.act === "auto") { await run(() => api("PUT", "/api/dca/config", { auto: !d.auto }), d.auto ? "자동 매수를 껐어요" : "자동 매수를 켰어요"); this.fill(); }
      else if (b.dataset.toggle) { await run(() => api("PUT", `/api/dca/plans/${b.dataset.toggle}/enabled`, { enabled: b.getAttribute("aria-checked") !== "true" })); this.fill(); }
      else if (b.dataset.pedit) planSheet(d.plans.find((p) => p.id == b.dataset.pedit));
    };
    if (d.plans.length) this.dcaOutlook();
  },
  // 적립 → 배당: 플랜을 지금처럼 이어 갈 때 받게 될 배당(플랜 종목의 실제 배당 자료로 추정)
  async dcaOutlook() {
    const el = $("#dcaDiv");
    if (!el) return;
    el.innerHTML = `<section class="card"><div class="empty small loading">플랜 종목의 배당을 계산하는 중</div></section>`;
    let O;
    try { O = await GET("/api/dca/outlook"); } catch (e) {
      if ($("#dcaDiv")) $("#dcaDiv").innerHTML = e instanceof Locked || e instanceof NeedConnect || e.code === "old_server" ? "" : `<section class="card"><div class="tiny">배당 전망을 불러오지 못했어요. ${esc(e.message)} <button type="button" class="lnk" id="dcaAgain">다시 계산</button></div></section>`;
      return;
    }
    if (investState.tab !== "dca" || !$("#dcaDiv")) return;
    this.dcaO = O;
    O.rows.forEach((r) => $$(`[data-pdv="${r.code}"]`).forEach((t) => (t.textContent = r.yield ? ` · 배당 ${r.yield}% ${r.freq_name}` : "")));
    const S = O.sim;
    if (!S) { $("#dcaDiv").innerHTML = ""; return; }
    const nm = {}; O.rows.forEach((r) => (nm[r.sym] = r.name));
    const tail = `${S.missing.length && !O.pending ? `<div class="tiny">배당 자료가 없는 종목(${S.missing.map((x) => esc(nm[x] || x)).join(", ")})은 배당 0으로 계산했어요.</div>` : ""}
        ${O.pending ? `<div class="tiny">배당 자료를 아직 받는 중인 종목이 있어요. <button type="button" class="lnk" id="dcaAgain">다시 계산</button></div>` : ""}`;
    if (!O.payers) {
      $("#dcaDiv").innerHTML = `<section class="card dca-dv"><h2>${O.pending ? "배당 자료를 받는 중이에요" : "지금 플랜에는 배당을 주는 종목이 없어요"}</h2>
        <div class="tiny">${O.pending ? "잠시 뒤 다시 계산해 보세요." : "배당 탭에서 배당 종목을 골라 적립 플랜에 담으면, 모을수록 매달 들어오는 돈이 여기 보여요."}</div>${tail}
        <div class="btns"><button type="button" class="btn dark" data-dcago="list">배당 종목 보기</button><button type="button" class="btn" data-dcago="ai"><span class="ai-spark" aria-hidden="true">✦</span> AI 구성 받기</button></div></section>`;
      return;
    }
    const at = (n) => S.yearly[n - 1], steps = [["지금", O.now_monthly, O.start, "보유 "], ...[5, 10, 20].filter((n) => at(n)).map((n) => [`${n}년 뒤`, at(n).monthly, at(n).value, "평가 "])];
    const hi = Math.max(1, ...steps.map((x) => x[1])), last = steps[steps.length - 1], lastY = at(Math.min(20, S.yearly.length));
    const goal = O.goal ? (S.goal_year ? `목표 월 ${bigNum(O.goal)}원은 <b>${S.goal_year}년차</b>에 닿아요${S.goal_year_spend && S.goal_year_spend !== S.goal_year ? ` <span>(배당을 받아 쓰면 ${S.goal_year_spend}년차)</span>` : S.goal_year_spend ? "" : " <span>(배당을 받아 쓰면 20년 안에 못 닿아요)</span>"}`
      : `목표 월 ${bigNum(O.goal)}원에는 <b>20년 안에 닿지 않아요</b> <span>매달 금액을 늘리거나 배당률이 높은 종목을 섞어야 해요</span>`) : "";
    $("#dcaDiv").innerHTML = `<section class="card dca-dv">
        <div class="dv-plan-h"><div><b>이대로 모으면 받는 배당</b><small>매달 ${won(O.monthly)}원 적립 · 배당은 다시 투자 · 세금 뺀 한 달 평균</small></div>
          <span class="dv-y"><em>${S.yield}%</em><small>플랜 배당률</small></span></div>
        <div class="dca-stair" role="img" aria-label="${steps.map((x) => `${x[0]} 월 ${won(x[1])}원`).join(", ")}">${steps.map(([t, v, val, lab], i) => `<div class="${i ? "" : "now"}">
          <em><b>${v ? bigNum(v) : "0"}<small>원</small></b><i style="height:${Math.max(3, Math.round(v / hi * 78))}%"></i></em><span>${t}</span><small>${lab}${bigNum(val)}원</small></div>`).join("")}</div>
        <div class="tiny">${last[0]}에는 넣은 돈 ${bigNum(lastY.invested)}원이 ${bigNum(last[2])}원이 되고, 배당만 한 달 ${won(last[1])}원쯤이에요.</div>
        ${goal ? `<div class="dca-goal">${goal} <button type="button" class="lnk" id="dcaGoal">목표 바꾸기</button></div>` : `<div class="dca-goal none">한 달에 받고 싶은 배당을 정하면 언제 닿는지 보여 드려요. <button type="button" class="lnk" id="dcaGoal">목표 정하기</button></div>`}
        ${tail}
        <div class="btns"><button type="button" class="btn dark" data-dcago="sim">시뮬레이션으로 자세히</button><button type="button" class="btn" data-dcago="list">배당 종목 더 보기</button></div>
        <div class="tiny">플랜 금액 비율대로 계속 사고 주가가 연 3% 오른다고 본 추정이에요. 최근 1년 실제 배당 기준이고, 배당·주가는 달라질 수 있어요.</div>
      </section>`;
  },
  // 적립 → 배당 탭으로 이어 가기(sim: 내 적립 플랜을 그대로 시뮬레이션에 넣는다)
  dcaGo(to) {
    const D = this.dvState(), O = this.dcaO;
    if (to === "sim" && O && O.assets && O.assets.length) {
      D.assets = O.assets.map((a) => ({ mkt: a.mkt, sym: a.sym, weight: a.weight, name: a.name })); D.preset = "dca";
      D.amount = O.start; D.add = O.monthly; D.reinvest = true; D.years = Math.max(10, D.years || 10); D.sim = null;
    }
    D.tab = to; this.dvSave(); location.hash = "#invest/dividend";
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
      <div id="btOut" style="display:flex;flex-direction:column;gap:8.5px"></div>`;
    const src = () => {
      $("#btSrc").innerHTML = S.source === "demo"
        ? `<label class="field">기간(영업일)<input id="btDays" class="r" inputmode="numeric" value="60"></label>
           <div class="notice">가상 데이터는 무작위 가격이라 <b>실제 수익성과 무관</b>해요. 기능 확인용이에요.</div>`
        : `<label class="field">CSV 파일<input id="btFile" type="file" accept=".csv,text/csv" style="padding:8.5px"></label>
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
        <div class="field">종목(최대 12개)<div id="swPick"></div></div>
        <button class="btn dark wide" id="swGo">훑기 시작</button>
        <div class="tiny">처음엔 vectorbt 설치와 분봉 받기로 몇 분 걸려요. 받은 분봉은 저장해 두고 다음부터 다시 써요.</div>
      </section>
      <div id="swOut" style="display:flex;flex-direction:column;gap:8.5px"></div>`;
    const keep = () => { F.strategy = $("#swStrat").value; F.days = num($("#swDays").value); };
    F.names = F.names || {};
    stockPick($("#swPick"), { mkt: "kr", multi: true, max: 12, value: F.codes.split(/[,\s]+/).filter(Boolean).slice(0, 12).map((x) => ({ sym: x, name: F.names[x] || (d.names || {})[x] || x })),
      onChange: (it) => { F.codes = it.map((x) => x.sym).join(", "); it.forEach((x) => (F.names[x.sym] = x.name)); } });
    $("#swStrat").onchange = () => { keep(); $("#swN").value = (GRID_N[F.strategy] || "") + "개"; };
    bindSeg(view, "분봉", (v) => { keep(); F.source = v; });
    $("#swGo").onclick = async () => {
      keep();
      const codes = F.codes.split(/[,\s]+/).map((x) => x.replace(/\D/g, "")).filter((x) => x.length === 6);
      if (!codes.length) return toast("훑어볼 종목을 이름으로 찾아 넣어 주세요", true);
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
      ${AI_BTN("sweep", "AI로 결과 해석(과최적화 점검)")}
      <div class="tiny" style="padding:0 3px">앞 기간에서 고른 조건은 뒤 기간에서 대개 성적이 떨어져요. '검증 구간도 플러스'인 조건만 고려하고, 적용 뒤에도 앱 내 모의로 먼저 돌려 보세요.</div>`;
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
          <div id="wlPick"></div>
          <div class="tiny">이름으로 찾아 넣고, ×로 빼요. 10개 안팎을 권해요.</div></section>
        <button class="btn dark wide" id="saveRules">규칙 저장</button>`;
      stockPick($("#wlPick"), { mkt: "kr", multi: true, max: 30, value: c.watchlist.map((x) => ({ sym: x, name: d.names[x] || x })),
        onChange: (it) => { c.watchlist = it.map((x) => x.sym); it.forEach((x) => (d.names[x.sym] = x.name)); } });
      bindSeg(view, "전략", (v) => { collect(); strat = v; draw(); });
      $("#saveRules").onclick = async () => {
        collect();
        await run(() => api("PUT", "/api/scalp/config", { strategy: { ...c.strategy, name: strat }, exit: c.exit, risk: c.risk, watchlist: c.watchlist }), "규칙을 저장했어요");
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
// ---------- 장 운영 시간(국내 KRX·NXT, 미국 NYSE·나스닥) ----------
const MK_HOLI = {
  kr: { "2026-01-01": "신정", "2026-02-16": "설 연휴", "2026-02-17": "설날", "2026-02-18": "설 연휴", "2026-03-02": "삼일절 대체휴일",
    "2026-05-01": "근로자의 날", "2026-05-05": "어린이날", "2026-05-25": "부처님오신날 대체휴일", "2026-06-03": "지방선거", "2026-08-17": "광복절 대체휴일",
    "2026-09-24": "추석 연휴", "2026-09-25": "추석", "2026-10-05": "개천절 대체휴일", "2026-10-09": "한글날", "2026-12-25": "성탄절", "2026-12-31": "연말 휴장",
    "2027-01-01": "신정", "2027-02-08": "설 연휴", "2027-02-09": "설 대체휴일", "2027-03-01": "삼일절", "2027-05-05": "어린이날", "2027-05-13": "부처님오신날",
    "2027-08-16": "광복절 대체휴일", "2027-09-14": "추석 연휴", "2027-09-15": "추석", "2027-09-16": "추석 연휴", "2027-10-04": "개천절 대체휴일",
    "2027-10-11": "한글날 대체휴일", "2027-12-27": "성탄절 대체휴일", "2027-12-31": "연말 휴장" },
  us: { "2026-01-01": "새해", "2026-01-19": "마틴 루터 킹 데이", "2026-02-16": "대통령의 날", "2026-04-03": "성금요일", "2026-05-25": "메모리얼 데이",
    "2026-06-19": "준틴스", "2026-07-03": "독립기념일 대체", "2026-09-07": "노동절", "2026-11-26": "추수감사절", "2026-12-25": "크리스마스",
    "2027-01-01": "새해", "2027-01-18": "마틴 루터 킹 데이", "2027-02-15": "대통령의 날", "2027-03-26": "성금요일", "2027-05-31": "메모리얼 데이",
    "2027-06-18": "준틴스 대체", "2027-07-05": "독립기념일 대체", "2027-09-06": "노동절", "2027-11-25": "추수감사절", "2027-12-24": "크리스마스 대체" },
};
const MK_HALF = { us: ["2026-11-27", "2026-12-24", "2027-11-26"] };  // 13시 조기 마감
function tzParts(tz, d = new Date()) {
  const p = Object.fromEntries(new Intl.DateTimeFormat("en-US", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", weekday: "short", hourCycle: "h23" })
    .formatToParts(d).map((x) => [x.type, x.value]));
  return { ymd: `${p.year}-${p.month}-${p.day}`, min: Number(p.hour) * 60 + Number(p.minute) + Number(p.second) / 60, wd: p.weekday };
}
// 국내 정규장이 늦게 여는 날: 수능일(11월 셋째 주 목요일)은 개장·마감 모두 1시간, 새해 첫 거래일은 개장만 1시간. [개장 늦춤 분, 마감 늦춤 분, 이름]
function krLate(ymd) {
  const [y, mo, d] = ymd.split("-").map(Number);
  if (mo === 11) {
    const nov1 = new Date(Date.UTC(y, 10, 1)).getUTCDay();          // 0=일
    if (d === 1 - nov1 + 18) return [60, 60, "수능일"];
  }
  if (mo === 1 && d <= 4) {
    let f = 2;
    while ([0, 6].includes(new Date(Date.UTC(y, 0, f)).getUTCDay())) f++;
    if (d === f) return [60, 0, "새해 첫 거래일"];
  }
  return [0, 0, ""];
}
function mkSegs(m, ymd) {
  if (m === "kr") {
    const [so, sc, why] = krLate(ymd), T = (v) => `${String(Math.floor(v / 60)).padStart(2, "0")}:${String(v % 60).padStart(2, "0")}`;
    if (so || sc) return [[510 + so, 540 + so, "장 시작 동시호가", "auction", `${why}이라 ${T(510 + so)}~${T(540 + so)} · 프리마켓은 쉬어요`],
      [540 + so, 920 + sc, `정규장(${why})`, "open", `${T(540 + so)}~${T(920 + sc)}`], [920 + sc, 930 + sc, "장 마감 동시호가", "auction", `${T(920 + sc)}~${T(930 + sc)}`],
      [sc ? 1020 : 930, 1200, "애프터마켓", "after", sc ? "NXT 17:00~20:00" : "NXT 15:30~20:00 · KRX 시간외 15:40~18:00"]];
    return [[480, 510, "프리마켓", "pre", "NXT 08:00~08:50"], [510, 540, "장 시작 동시호가", "auction", "08:30~09:00 주문만 받고 9시에 한 번에 체결"],
      [540, 920, "정규장", "open", "09:00~15:20"], [920, 930, "장 마감 동시호가", "auction", "15:20~15:30"], [930, 1200, "애프터마켓", "after", "NXT 15:30~20:00 · KRX 시간외 15:40~18:00"]];
  }
  const close = (MK_HALF.us || []).includes(ymd) ? 780 : 960;
  return [[240, 570, "프리마켓", "pre", ""], [570, close, close < 960 ? "정규장(조기 마감)" : "정규장", "open", ""], [close, 1200, "애프터마켓", "after", ""]];
}
function mkDayKind(m, ymd, wd) {
  if (wd === "Sat" || wd === "Sun") return "주말";
  return MK_HOLI[m][ymd] || "";
}
function marketSession(m) {
  const tz = m === "kr" ? "Asia/Seoul" : "America/New_York", now = new Date(), P = tzParts(tz, now);
  const off = m === "us" ? Math.round((tzParts("Asia/Seoul", now).min - P.min + 1440 * 2) % 1440) : 0;  // 뉴욕→한국 시간 차(분)
  const kst = (mm) => { const v = ((mm + off) % 1440 + 1440) % 1440; return `${String(Math.floor(v / 60)).padStart(2, "0")}:${String(Math.round(v % 60)).padStart(2, "0")}`; };
  const holi = mkDayKind(m, P.ymd, P.wd), segs = mkSegs(m, P.ymd);
  let cur = null;
  if (!holi) cur = segs.find(([a, b]) => P.min >= a && P.min < b) || null;
  // 미국 주간거래(키움, 뉴욕 20:00~04:00 · 일~목 밤)
  let day = false;
  if (m === "us" && !cur) {
    const wdN = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].indexOf(P.wd);
    day = (P.min >= 1200 && wdN >= 0 && wdN <= 4) || (P.min < 240 && wdN >= 1 && wdN <= 5);
  }
  const left = (t) => { const x = Math.max(0, Math.round(t)); return x >= 60 ? `${Math.floor(x / 60)}시간${x % 60 ? ` ${x % 60}분` : ""}` : `${x}분`; };
  // 다음 정규장 시작까지
  const openOf = (ymd) => (m === "kr" ? 540 + krLate(ymd)[0] : 570);
  let openAt = openOf(P.ymd);
  let next = "";
  if (!cur || cur[3] !== "open") {
    let mins = 0, d = new Date(now), dP = P, guard = 0;
    if (!holi && P.min < openAt) mins = openAt - P.min;
    else {
      mins = 1440 - P.min;
      while (guard++ < 15) { d = new Date(d.getTime() + 864e5); dP = tzParts(tz, d); if (!mkDayKind(m, dP.ymd, dP.wd)) break; mins += 1440; }
      openAt = openOf(dP.ymd);
      mins += openAt;
    }
    next = mins < 1440 ? `정규장 시작까지 ${left(mins)}` : `다음 정규장 ${dP.ymd.slice(5).replace("-", "/")}(${{ Mon: "월", Tue: "화", Wed: "수", Thu: "목", Fri: "금", Sat: "토", Sun: "일" }[dP.wd]}) ${kst(openAt)}`;
  }
  let phase, label, sub;
  if (cur) { phase = cur[3]; label = cur[2]; const nx = segs[segs.indexOf(cur) + 1]; sub = cur[3] === "open" ? `마감까지 ${left(cur[1] - P.min)}` : nx ? `${nx[2]}까지 ${left(cur[1] - P.min)}` : `종료까지 ${left(cur[1] - P.min)}`; }
  else if (day) { phase = "day"; label = "주간거래"; sub = `키움 주간거래 중 · ${next}`; }
  else { phase = "closed"; label = holi ? `휴장 · ${holi}` : "장 마감"; sub = next; }
  return { m, phase, label, sub, holi, segs, now: holi ? null : P.min, kst, off };
}
// 지금 시간대에 맞는 시장: 열려 있는 장 > 프리·애프터 > 미국 주간거래. 직접 고르면 30분 동안은 그 선택을 따른다.
const mktPick = { m: null, at: 0 };
function autoMkt() {
  const rank = { open: 4, auction: 4, pre: 3, after: 3, day: 1, closed: 0 };
  const k = rank[marketSession("kr").phase] || 0, u = rank[marketSession("us").phase] || 0;
  return k === 0 && u === 0 ? null : k >= u ? "kr" : "us";
}
function curMkt(fallback) { return mktPick.m && Date.now() - mktPick.at < 30 * 60 * 1000 ? mktPick.m : autoMkt() || fallback || "kr"; }
function setMkt(m) { mktPick.m = m; mktPick.at = Date.now(); }
function sessionChip(m) {
  const S = marketSession(m);
  return `<span class="ss-chip ss-${S.phase}"><i></i><b>${m === "kr" ? "국내" : "미국"}</b> ${esc(S.label)}</span>`;
}
function sessionCard(m) {
  const S = marketSession(m), a = S.segs[0][0], z = S.segs[S.segs.length - 1][1], W = z - a, X = (v) => ((v - a) / W * 100).toFixed(2);
  const op = S.segs.find((x) => x[3] === "open"), marks = [op[0], op[1], z];
  if ((op[0] - a) / W > 0.14) marks.unshift(a);
  return `<section class="card ss-card">
    <div class="ss-top"><span class="ss-chip ss-${S.phase}"><i></i>${esc(S.label)}</span><span class="ss-sub">${esc(S.sub)}</span></div>
    <div class="ss-line">${S.segs.map(([x, y, n, k]) => `<span class="ss-seg k-${k}" style="left:${X(x)}%;width:${((y - x) / W * 100).toFixed(2)}%" title="${esc(n)}"></span>`).join("")}
      ${S.now != null && S.now >= a && S.now <= z ? `<span class="ss-now" style="left:${X(S.now)}%"></span>` : ""}</div>
    <div class="ss-marks">${marks.map((v) => `<span style="left:${X(v)}%">${S.kst(v)}</span>`).join("")}</div>
    <div class="ss-legend">${S.segs.map(([x, y, n, k, note]) => `<span><i class="k-${k}"></i>${esc(n)} ${S.kst(x)}~${S.kst(y)}</span>`).join("")}${S.m === "us" ? `<span><i class="k-day"></i>주간거래(키움) ${S.kst(1200)}~${S.kst(240)}</span>` : ""}</div>
    ${S.m === "us" ? `<div class="tiny">모두 한국 시간이에요. 미국 서머타임이 끝나면(11월~3월) 1시간씩 늦어져요. 시장가 주문은 정규장에만 돼요.</div>`
      : `<div class="tiny">NXT(넥스트레이드) 시간은 키움에서 통합 주문(SOR)으로 거래할 때 적용돼요. 동시호가 때는 주문만 받고 끝날 때 한 번에 체결돼요.</div>`}
  </section>`;
}

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
      const am = curMkt(this.m);
      if (am !== this.m) { this.m = am; this.last = {}; }
      $(".fab")?.remove();
      box.innerHTML = `<div id="mkBoard" class="mk">
        <div class="mk-idx" id="mkIdx" aria-label="지수"></div>
        <div class="mk-bar">
          ${seg("시장", [["kr", "국내"], ["us", "미국"]], this.m, "mk-seg")}
          <button type="button" class="mk-find" id="mkFind" aria-label="종목 검색"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/></svg>종목 검색</button>
        </div>
        <div id="mkSess"></div>
        <div id="mkNote"></div>
        <section class="card tight"><div class="mk-head"><span>관심종목</span><button type="button" class="link" id="mkToggle"></button></div><div class="list" id="mkList"><div class="empty loading">불러오는 중</div></div></section>
        <div class="tiny" id="mkFoot" style="padding:0 3px"></div>
        ${AI_BTN("chat", "AI에게 묻기 · 내 계좌·관심종목을 알고 답해요")}
      </div>`;
      bindSeg(box, "시장", (v) => {
        setMkt(v);
        this.m = v; store.set("mkt", v === "us" ? "us" : ""); this.last = {};
        const c = this.snap[v] || memo.get("mk." + v);
        if (c) this.renderBoard(c, v); else { $("#mkList").innerHTML = `<div class="empty loading">불러오는 중</div>`; $("#mkIdx").innerHTML = ""; $("#mkSess").innerHTML = sessionCard(v); }
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
    if ($("#mkSess")) $("#mkSess").innerHTML = sessionCard(m);
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
      const ext = q.session === "pre" ? ` · <span class="tag y">프리마켓</span>` : q.session === "post" ? ` · <span class="tag y">애프터</span>` : m === "us" && q.live ? ` · <span class="tag g">실시간</span>` : "";
      const sub = m === "us" && d.fx && q.price != null ? `${esc(q.sym)} · ${won(q.price * d.fx)}원${ext}` : `${esc(q.sym)}${q.exchange ? " · " + esc(q.exchange) : ""}`;
      return `<button type="button" class="item mk-row" data-sym="${esc(q.sym)}">
        <span class="main"><b>${esc(q.name || q.sym)}</b><span class="sub">${q.error ? "시세를 받지 못했어요" : sub}</span></span>
        ${this.spark(q.spark, (q.change || 0) >= 0)}
        <span class="end"><b class="mk-p ${flash}">${m === "us" && q.price != null ? "$" : ""}${this.money(q.price, q.currency)}</b>
          <span class="mk-c ${tone(q.change)}">${this.chg(q)}</span></span></button>`;
    }).join("") : `<div class="empty">관심종목이 없어요. 종목 검색에서 별을 눌러 추가하세요.</div>`;
    $("#mkFoot").textContent = `${(d.updated_at || "").slice(11, 19)} 기준 · ${m === "kr" ? (d.kiwoom ? "키움 실시간" : "지연 시세") : d.items.some((x) => x.live) ? "미국 시세는 키움 실시간(프리·애프터마켓 포함)" : "미국 시세는 공개 시세(프리·애프터마켓 포함, 최대 15분 지연)"} · 종목을 누르면 차트와 상세가 보여요`;
  },

  // ---------- 검색 ----------
  search() {
    const m = this.m;
    openSheet(m === "kr" ? "국내 종목 검색" : "미국 종목 검색", `
      <label class="field">${m === "kr" ? "종목명 또는 6자리 코드" : "회사 이름 또는 기호(AAPL)"}<input id="mkQ" type="search" autocomplete="off" enterkeyhint="search" placeholder="${m === "kr" ? "삼성전자" : "apple"}"></label>
      <div id="mkRes"></div>`, (box) => {
      const q = $("#mkQ", box);
      let t = null, seq = 0;
      const hint = `<div class="empty small">${m === "kr" ? "이름 일부만 넣어도 찾아요" : "영문으로 넣어 주세요"}</div>`;
      const recent = () => {
        const R = recentStock.list(m);
        $("#mkRes").innerHTML = R.length ? `<div class="op-h rc-h">최근 찾은 종목<button type="button" class="rc-clr" data-rcclr>모두 지우기</button></div><div class="list">${R.map((x) => `<div class="item">
            <a class="main" href="#invest/stock?m=${m}&s=${encodeURIComponent(x.sym)}" data-rs="${esc(x.sym)}" data-name="${esc(x.name)}" data-exch="${esc(x.exchange || "")}" style="text-decoration:none"><b>${esc(x.name)}</b><span class="sub">${esc(x.sym)}${x.exchange ? " · " + esc(x.exchange) : ""}</span></a>
            <button type="button" class="rc-x" data-rcx="${esc(x.sym)}" aria-label="${esc(x.name)} 최근 목록에서 지우기">${CLOSE_ICO}</button></div>`).join("")}</div>` : hint;
      };
      const go = async () => {
        const v = q.value.trim(), my = ++seq;
        if (!v) return recent();
        $("#mkRes").innerHTML = `<div class="empty small">찾는 중</div>`;
        try {
          const [res, w] = await Promise.all([GET(`/api/market/search?mkt=${m}&q=${encodeURIComponent(v)}`), GET(`/api/market/overview?mkt=${m}`).catch(() => ({ items: [] }))]);
          if (my !== seq) return;
          const on = new Set((w.items || []).map((x) => x.sym));
          $("#mkRes").innerHTML = res.length ? `<div class="list">${res.map((x) => `<div class="item">
            <a class="main" href="#invest/stock?m=${m}&s=${encodeURIComponent(x.sym)}" data-rs="${esc(x.sym)}" data-name="${esc(x.name)}" data-exch="${esc(x.exchange || "")}" style="text-decoration:none"><b>${esc(x.name)}</b><span class="sub">${esc(x.sym)}${x.exchange ? " · " + esc(x.exchange) : ""}</span></a>
            <button type="button" class="star" data-star="${esc(x.sym)}" data-name="${esc(x.name)}" data-exch="${esc(x.exchange || "")}" aria-pressed="${on.has(x.sym)}" aria-label="${esc(x.name)} 관심종목">${STAR}</button></div>`).join("")}</div>`
            : `<div class="empty small">찾는 종목이 없어요</div>`;
        } catch (e) { if (my === seq) $("#mkRes").innerHTML = `<div class="notice warn">${esc(e.message)}</div>`; }
      };
      q.oninput = () => { clearTimeout(t); t = setTimeout(go, 300); };
      $("#mkRes").onclick = async (e) => {
        if (e.target.closest("[data-rcclr]")) { recentStock.clear(m); return recent(); }
        const x = e.target.closest("[data-rcx]");
        if (x) { recentStock.remove(m, x.dataset.rcx); return recent(); }
        const b = e.target.closest("[data-star]");
        if (!b) { const a = e.target.closest("a[data-rs]"); if (a) { recentStock.add(m, a.dataset.rs, a.dataset.name, a.dataset.exch); closeSheet(); } return; }
        recentStock.add(m, b.dataset.star, b.dataset.name, b.dataset.exch);
        const on = b.getAttribute("aria-pressed") !== "true";
        await run(() => api("POST", "/api/market/watch", { mkt: m, sym: b.dataset.star, on }), on ? "관심종목에 넣었어요" : "관심종목에서 뺐어요");
        b.setAttribute("aria-pressed", String(on));
        if ($("#mkList")) this.board($("#iv"));
      };
      recent();
      setTimeout(() => q.focus(), 50);
    });
  },

  // ---------- 종목 상세 ----------
  async detail(h, host) {
    const m = h.params.get("m") === "us" ? "us" : "kr", sym = (h.params.get("s") || "").toUpperCase();
    const pk = h.params.get("pk"), ph = h.params.get("ph"), fromAt = h.params.get("at") === "1";
    this.d = { m, sym, at: null, range: pk ? (ph && ph !== "day" ? "d" : "m5") : fromAt ? "m5" : this.lastRange || "1d", tab: "chart", q: null, chart: null, cd: null, pk, markFell: !!(ph && ph !== "day") };
    if (pk) this.loadMark(this.d);
    this.loadAt(this.d);
    if (host) host.innerHTML = `<div class="mk-d" id="mkD"><div class="empty loading">불러오는 중</div></div>`;
    else {
      setTop("시세", { back: navBack("#invest/home"), menu: "invest", sub: pk ? "aistat" : "stock", right: `<button type="button" class="star on-deep" id="mkBell" aria-label="가격 알림">${BELL}</button><button type="button" class="star on-deep" id="mkStar" aria-pressed="false" aria-label="관심종목">${STAR}</button>` });
      view.innerHTML = `<div class="mk-d" id="mkD"><div class="empty loading">불러오는 중</div></div>`;
    }
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
    const line = D.range === "1d";
    const needChart = D.tab === "chart" && (full || (line ? (!D.chart || true) : (!D.cd || D.range === "m5")));
    const [q, ch] = await Promise.all([
      GET(`/api/market/quote/${D.m}/${encodeURIComponent(D.sym)}`),
      needChart ? (line ? GET(`/api/market/chart/${D.m}/${encodeURIComponent(D.sym)}?range=1d`)
        : GET(`/api/invest/candles/${D.m}/${encodeURIComponent(D.sym)}?tf=${D.range}`)).catch((e) => ({ points: [], bars: [], error: e.message })) : null]);
    if (this.d !== D) return;
    D.q = q; this.qmemo[D.m + D.sym] = q;
    if (D.cd && D.cd.bars && D.cd.bars.length && q.price) { const b = D.cd.bars[D.cd.bars.length - 1]; b.c = q.price; b.h = Math.max(b.h, q.price); b.l = Math.min(b.l, q.price); }
    if (ch) { if (line) { D.chart = ch; this.cmemo[D.m + D.sym + "1d"] = ch; } else D.cd = ch; }
    this.drawDetail(full || !$("#mkPrice"));
    if (D.tab !== "chart") this.loadTab();
  },
  // 이 종목에서 자동매매가 한 일(추천·매수·매도). 받으면 차트를 다시 그리고 아래 목록을 채운다
  async loadAt(D) {
    try {
      const r = await GET(`/api/auto/marks/${D.m}/${encodeURIComponent(D.sym)}`);
      if (this.d !== D) return;
      D.at = r.events || [];
      if (D.at.length) { this.drawChart(); this.drawAt(); }
    } catch (e) { /* 예전 서버면 표시 없이 둔다 */ }
  },
  drawAt() {
    const D = this.d, el = $("#mkAt");
    if (!el || !D || !D.at || !D.at.length) return;
    const M = (n) => this.money(n, D.q ? D.q.currency : D.m === "us" ? "USD" : "KRW");
    // 같은 분·같은 종류·같은 구분(실전/모의)은 한 줄로 묶는다
    const rows = [];
    for (const e of D.at.slice().reverse()) {
      const k = `${e.ts.slice(0, 16)}|${e.kind}|${e.mode || ""}`, last = rows[rows.length - 1];
      if (last && last.k === k) { last.n += 1; last.plans.push(e.plan); last.sum += e.price || 0; if (e.pct != null) last.pcts.push(e.pct); }
      else rows.push({ k, e, n: 1, plans: [e.plan], sum: e.price || 0, pcts: e.pct != null ? [e.pct] : [] });
    }
    const KIND = { rec: "AI 추천", buy: "매수", sell: "매도" };
    el.innerHTML = `<section class="card at-mk">
      <div class="card-head"><h2>자동매매 기록</h2><span class="tiny">최근 30일 · 5분·일 차트에 표시돼요</span></div>
      <div class="at-mk-lg">${atMarkLegend()}</div>
      <div class="lab-last">${rows.slice(0, 30).map((r) => {
        const e = r.e, avg = r.n ? r.sum / r.n : 0, p = r.pcts.length ? r.pcts.reduce((a, b) => a + b, 0) / r.pcts.length : null;
        return `<div><span><b><i class="at-mk-i ${e.kind} ${e.mode || ""}"></i>${e.kind === "rec" ? "AI 추천" : `${e.mode === "real" ? "실전" : "모의"} ${KIND[e.kind]}`}${r.n > 1 ? ` · 플랜 ${r.n}개` : e.plan ? ` · ${esc(e.plan)}` : ""}</b>
          <small>${esc(e.ts.slice(5, 10))} ${esc(e.ts.slice(11, 16))}${e.kind === "rec" ? ` · ${esc(e.stance || "")} · 점수 ${e.score ?? "-"} · 승률 ${e.win_prob ?? "-"}%` : r.n > 1 ? ` · ${esc(r.plans.slice(0, 3).join(", "))}${r.n > 3 ? " 외" : ""}` : ` · ${e.qty}주`}</small></span>
          <span><b class="${p == null ? "" : tone(p)}">${e.kind === "sell" && p != null ? pct(p, 2) : avg ? M(avg) : "-"}</b>
          <small>${e.kind === "buy" ? `익절 ${M(e.target)} · 손절 ${M(e.stop)}` : e.kind === "sell" ? `${esc(e.reason || "")} · ${M(avg)}` : e.target_pct ? `목표 +${e.target_pct}% · 손절 -${e.stop_pct}%` : ""}</small></span></div>`;
      }).join("")}</div></section>`;
  },
  async loadMark(D) {
    try {
      const p = await GET(`/api/ai/picks/${D.pk}`);
      const x = p.picks.find((y) => String(y.sym).toUpperCase() === D.sym);
      if (!x || this.d !== D) return;
      const o = x.outcome || {}, entry = o.entry || x.price, sw = p.horizon && p.horizon !== "day";
      D.mark = { ts: p.ts, end: sw && x.end_date ? x.end_date + "T00:00" : o.end_ts && o.end_ts.length >= 16 ? o.end_ts : null, entry, tp: entry * (1 + x.target_pct / 100), sl: entry * (1 - x.stop_pct / 100),
        tp_pct: x.target_pct, sl_pct: x.stop_pct };
      D.pick = { p, x, o };
      if (sw && D.range !== "d") {  // 중기 추천은 일봉으로 본다
        D.markFell = true; D.range = "d";
        $$('[data-seg="기간"]').forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.v === "d")));
        try { const cd = await GET(`/api/invest/candles/${D.m}/${encodeURIComponent(D.sym)}?tf=d`); if (this.d === D) D.cd = cd; } catch (e) { /* 다음 갱신 때 */ }
      }
      if ($("#mkPick")) this.drawPick();
      if ($("#mkChart") && D.cd) this.drawChart();
    } catch (e) { /* 표시만 생략 */ }
  },
  drawPick() {
    const D = this.d, el = $("#mkPick");
    if (!el || !D.pick) return;
    const { p, x, o } = D.pick, cur = D.q.currency, M = (v) => this.money(v, cur), res = o.result || "진행 중";
    const sw = p.horizon && p.horizon !== "day", path = (o.path && Object.keys(o.path).length) ? o.path : null;
    el.innerHTML = `<section class="card pk-info">
      <div class="pk-h"><span class="as-badge r-${esc(res)}">${esc(sw && res === "시간 정리" ? "기간 만료" : res)}</span><b>AI ${sw ? esc(p.label) + " " : ""}추천 추적</b><span class="${tone(o.net_pct)}" style="margin-left:auto;font-weight:900">${o.net_pct == null ? "" : pct(o.net_pct, 2)}</span></div>
      <div class="tiny">${esc(p.ts.slice(5, 16).replace("T", " "))} 추천 · ${esc(p.model.replace("claude-", ""))} · 예측 승률 ${x.win_prob}%${o.end_ts ? ` · 평가 마감 ${esc(String(o.end_ts).slice(5, 16).replace("T", " "))}` : ""}</div>
      <div class="pk-lv"><div class="en"><span>추천가</span><b>${M(D.mark.entry)}</b></div><div class="tp"><span>익절 +${x.target_pct}%</span><b>${M(D.mark.tp)}</b></div><div class="sl"><span>손절 −${x.stop_pct}%</span><b>${M(D.mark.sl)}</b></div>
        ${o.exit ? `<div><span>가상 청산</span><b>${M(o.exit)}</b></div>` : ""}</div>
      ${x.news_score != null && (x.news_score || x.news) ? `<div class="tiny">뉴스 <b class="${tone(x.news_score)}">${x.news_score > 0 ? "+" : ""}${x.news_score}</b>${x.news ? " · " + esc(x.news) : ""}</div>` : ""}
      ${sw ? `<div class="pk-sw">${x.catalyst ? `<div><span class="tag c-${esc(x.catalyst_type)}">${esc(x.catalyst_type)}</span><b>${esc(x.catalyst)}</b></div>` : ""}
        ${path ? `<div class="pk-path">${Object.entries(path).map(([k, v]) => `<span><small>${k.slice(1)}일째</small><b class="${tone(v)}">${pct(v, 1)}</b></span>`).join("")}</div><div class="tiny">추천가에 사서 그냥 들고 있었다면(비용 빼기 전)${o.bench != null ? ` · 같은 기간 지수 ${pct(o.bench, 2)}` : ""}</div>` : ""}</div>` : ""}
      ${o.alt_result ? `<div class="tiny">추천 순간 가격에 바로 샀을 때 기준이에요. AI 지정가 ${M(x.entry_price)}를 기다렸다면 <b class="${tone(o.alt_net)}">${esc(o.alt_result)}${o.alt_net == null ? "" : " " + pct(o.alt_net, 2)}</b></div>` : ""}
      <div class="tiny">차트에 추천 시점(세로선)과 익절·손절·추천가 선을 함께 그렸어요. 색칠된 구간이 평가 기간이에요.</div>
      <button type="button" class="btn sm" id="pkOpen">추천 근거·전체 내용 보기</button></section>`;
    $("#pkOpen").onclick = () => pickSheet(D.pk);
  },
  async loadTab() {
    const D = this.d, box = $("#dtPane");
    if (!box || D.tab === "chart") return;
    const u = { book: "book", ticks: "ticks", info: "info" }[D.tab];
    try {
      const [r, tk] = await Promise.all([GET(`/api/invest/${u}/${D.m}/${encodeURIComponent(D.sym)}`),
        D.tab === "book" ? GET(`/api/invest/ticks/${D.m}/${encodeURIComponent(D.sym)}`).catch(() => []) : null]);
      if (this.d !== D || !$("#dtPane")) return;
      if (D.tab === "book") this.drawBook(r, tk || []); else if (D.tab === "ticks") this.drawTicks(r); else this.drawInfo(r);
    } catch (e) { if ($("#dtPane")) $("#dtPane").innerHTML = `<div class="empty small">${esc(e.message)}</div>`; }
  },
  drawBook(b, tk = []) {
    const D = this.d, q = D.q, cur = q.currency, box = $("#dtPane"), us = D.m === "us", fx = q.fx;
    const mx = Math.max(1, ...b.asks.map((x) => x.qty), ...b.bids.map((x) => x.qty));
    const asks = b.asks.slice().sort((x, y) => y.price - x.price), bids = b.bids.slice().sort((x, y) => y.price - x.price);
    const pc = (p) => { const r = q.prev ? (p / q.prev - 1) * 100 : 0; return `<span class="ob-p ${tone(r)} ${Math.abs(p - q.price) < 1e-9 ? "now" : ""}">${this.money(p, cur)}<small>${pct(r, 2)}${us && fx ? ` · ${won(p * fx)}원` : ""}</small></span>`; };
    const M = (v) => v == null ? "-" : this.money(v, cur);
    const st = tk.length ? tk[0].strength : 0, tb = b.tot_ask + b.tot_bid || 1;
    box.innerHTML = `<div class="ob ${us ? "us" : ""}">
      <div class="ob-half">
        ${asks.map((x) => `<button type="button" class="ob-row ask" data-px="${x.price}"><span class="ob-q"><i style="width:${x.qty / mx * 100}%"></i><em>${won(x.qty)}</em></span>${pc(x.price)}<span></span></button>`).join("")}
        <dl class="ob-side ob-info">
          <div><dt>거래량</dt><dd>${q.volume != null ? bigNum(q.volume) : "-"}</dd></div>
          <div><dt>52주 최고</dt><dd class="up">${M(q.high52)}</dd></div><div><dt>52주 최저</dt><dd class="down">${M(q.low52)}</dd></div>
          <div><dt>시가</dt><dd class="${tone((q.open || 0) - (q.prev || 0))}">${M(q.open)}</dd></div>
          <div><dt>고가</dt><dd class="up">${M(q.high)}</dd></div><div><dt>저가</dt><dd class="down">${M(q.low)}</dd></div>
          <div><dt>전일 종가</dt><dd>${M(q.prev)}</dd></div>
          ${us && fx ? `<div><dt>환율</dt><dd>${won(fx)}원</dd></div>` : ""}
        </dl></div>
      <div class="ob-half">
        ${bids.map((x) => `<button type="button" class="ob-row bid" data-px="${x.price}"><span></span>${pc(x.price)}<span class="ob-q"><i style="width:${x.qty / mx * 100}%"></i><em>${won(x.qty)}</em></span></button>`).join("")}
        <div class="ob-side ob-tk">
          <div class="ob-st ${st >= 100 ? "up" : "down"}"><span>체결강도</span><b>${st ? st.toFixed(1) + "%" : "-"}</b></div>
          ${tk.slice(0, 12).map((x) => `<div class="${x.qty > 0 ? "up" : "down"}"><span>${this.money(x.price, cur)}</span><b>${won(Math.abs(x.qty))}</b></div>`).join("") || `<div class="tiny">체결 없음</div>`}
        </div></div>
      <div class="ob-bal" aria-label="잔량 비율"><i class="a" style="flex:${b.tot_ask / tb}"></i><i class="b" style="flex:${b.tot_bid / tb}"></i></div>
      <div class="ob-tot"><span>매도 잔량 ${won(b.tot_ask)}</span><span>매수 잔량 ${won(b.tot_bid)}</span></div></div>
      <div class="tiny">가격을 누르면 그 가격으로 주문 창이 열려요. 체결은 빨강이 매수, 파랑이 매도 체결이에요.</div>`;
    box.onclick = (e) => { const r = e.target.closest("[data-px]"); if (r) orderSheet({ code: D.sym, market: D.m, side: r.classList.contains("ask") ? "buy" : "sell", price: Number(r.dataset.px), exch: q.exch, name: q.name, holding: q.holding }); };
  },
  drawTicks(t) {
    const cur = this.d.q.currency;
    $("#dtPane").innerHTML = t.length ? `<table class="tk"><thead><tr><th>시간</th><th>체결가</th><th>수량</th><th>체결강도</th></tr></thead><tbody>
      ${t.map((x) => `<tr><td>${esc(x.t)}</td><td class="${tone(x.change_pct)}">${this.money(x.price, cur)}</td><td class="${x.qty > 0 ? "up" : "down"}">${won(Math.abs(x.qty))}</td><td>${x.strength ? x.strength.toFixed(1) + "%" : "-"}</td></tr>`).join("")}</tbody></table>`
      : `<div class="empty small">체결 내역이 없어요</div>`;
  },
  drawInfo(r) {
    const D = this.d, q = D.q, cur = q.currency;
    const r52 = q.high52 && q.low52 && q.high52 > q.low52 ? Math.min(100, Math.max(0, (q.price - q.low52) / (q.high52 - q.low52) * 100)) : null;
    const vol = q.volume != null ? Number(q.volume).toLocaleString("ko-KR") : "-";
    $("#dtPane").innerHTML = `<div class="mk-grid">${[["시가", q.open], ["고가", q.high], ["저가", q.low], ["전일 종가", q.prev]]
      .map(([k, v]) => `<div><span>${k}</span><b class="${k === "고가" ? "up" : k === "저가" ? "down" : ""}">${v == null ? "-" : this.money(v, cur)}</b></div>`).join("")}<div><span>거래량</span><b>${vol}</b></div></div>
      ${r52 == null ? "" : `<div class="mk-52"><div class="tiny">52주 범위</div><div class="mk-52bar"><i style="left:${r52}%"></i></div>
        <div class="mk-52lbl"><span>최저 ${this.money(q.low52, cur)}</span><span>최고 ${this.money(q.high52, cur)}</span></div></div>`}
      <div class="mk-grid info">${(r.items || []).filter(([k]) => !/52주/.test(k)).map(([k, v]) => `<div><span>${esc(k)}</span><b>${esc(v == null ? "-" : String(v))}</b></div>`).join("")}</div>`;
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
    const hd = q.holding;
    const box = $("#mkD");
    if (full || !$("#mkPrice")) {
      box.innerHTML = `
        <section class="mk-hero">
          <div class="mk-name"><b>${esc(q.name)}</b><span>${esc(D.sym)}${q.exchange ? " · " + esc(q.exchange) : ""}${q.delayed ? " · 지연" : ""}</span></div>
          <div class="mk-price" id="mkPrice"></div>
          <div class="mk-delta" id="mkDelta"></div>
          <div class="mk-mini" id="mkMini"></div>
          <div class="ss-hero" id="mkSessH"></div>
        </section>
        <div id="mkPick"></div>
        <div class="dt-tabs">${seg("보기", [["chart", "차트"], ["book", "호가"], ["ticks", "체결"], ["info", "정보"]], D.tab)}</div>
        <section class="card mk-chart-card" id="dtChart" ${D.tab === "chart" ? "" : "hidden"}>
          <div class="mk-chart" id="mkChart"></div>
          <div class="mk-range-row">${seg("기간", [["1d", "1일"], ["m5", "5분"], ["d", "일"], ["w", "주"], ["mo", "월"]], D.range, "on-card mk-range")}
            <button type="button" class="cz-btn" id="mkZoom" aria-label="차트 크게 보기(가로 전체 화면)">${ZOOM_ICO}</button></div>
        </section>
        <section class="card" id="dtPane" ${D.tab === "chart" ? "hidden" : ""}><div class="empty small loading">불러오는 중</div></section>
        <div id="mkAt"></div>
        <div id="mkHold"></div>
        ${AI_BTN("stock", "AI로 이 종목 정리(뉴스 포함)", `data-sym="${esc(D.sym)}" data-mkt="${m}"`)}
        ${usd ? "" : `<button type="button" class="btn wide" id="mkDca">매달 적립하기</button>`}
        ${usd && !q.live ? `<div class="tiny" style="padding:0 3px">지금은 공개 시세(최대 15분 지연)예요. 키움을 연결하면 실시간으로 보여요.</div>` : ""}
        <div class="mk-act"><button type="button" class="btn blue" data-o="sell">매도</button><button type="button" class="btn red" data-o="buy">매수</button></div>`;
      bindSeg(box, "보기", (v) => {
        D.tab = v;
        $("#dtChart").hidden = v !== "chart"; $("#dtPane").hidden = v === "chart";
        if (v === "chart") this.drawChart(); else { $("#dtPane").innerHTML = `<div class="empty small loading">불러오는 중</div>`; this.loadTab(); }
      });
      bindSeg(box, "기간", async (v) => {
        D.range = v; this.lastRange = v;
        $("#mkChart").innerHTML = `<div class="empty small loading">불러오는 중</div>`;
        try {
          if (v === "1d") { D.chart = this.cmemo[m + D.sym + "1d"] || await GET(`/api/market/chart/${m}/${encodeURIComponent(D.sym)}?range=1d`); }
          else D.cd = await GET(`/api/invest/candles/${m}/${encodeURIComponent(D.sym)}?tf=${v}`);
        } catch (e) { if (v === "1d") D.chart = { points: [], error: e.message }; else D.cd = { bars: [], error: e.message }; }
        if (D.range === v && this.d === D) this.drawChart();
      });
      $("#mkZoom", box).onclick = () => this.zoom();
      $$("[data-o]", box).forEach((b) => b.onclick = () => orderSheet({ code: D.sym, market: m, side: b.dataset.o, exch: D.q?.exch, name: D.q?.name, holding: D.q?.holding,
        qty: b.dataset.o === "sell" && D.q?.holding ? (D.q.holding.sellable || D.q.holding.qty) : 1 }));
      if ($("#mkDca", box)) {
        // 이미 적립 중인 종목이면 단추에 표시(가벼운 조회라 화면을 막지 않는다)
        const mark = async () => { try { const B = await GET("/api/dca/plans"), p = B.plans.find((x) => x.code === D.sym); if (this.d === D && $("#mkDca")) $("#mkDca").textContent = p ? `적립 중 · 매달 ${won(p.amount)}원${p.enabled ? "" : " (쉬는 중)"}` : "매달 적립하기"; } catch (e) { /* 표시만 생략 */ } };
        $("#mkDca", box).onclick = () => dcaOne(D.sym, D.q?.name || "", mark);
        mark();
      }
      this.drawPick();
      $("#mkBell").onclick = () => alertSheet({ mkt: m, sym: D.sym, name: q.name, price: D.q.price, currency: cur });
    }
    const pEl = $("#mkPrice"), old = pEl.dataset.v;
    pEl.textContent = P(q.price); pEl.dataset.v = q.price;
    if (old && Number(old) !== q.price) { pEl.classList.remove("fl-up", "fl-dn"); void pEl.offsetWidth; pEl.classList.add(q.price > Number(old) ? "fl-up" : "fl-dn"); }
    $("#mkDelta").className = "mk-delta " + tone(q.change);
    $("#mkDelta").innerHTML = `${q.change > 0 ? `<i class="arr up"></i>` : q.change < 0 ? `<i class="arr dn"></i>` : ""} ${this.money(Math.abs(q.change), cur)} (${pct(q.change_pct, 2)})${usd && q.fx ? `<span class="mk-krw">약 ${won(q.price * q.fx)}원</span>` : ""}`;
    { const S = marketSession(m); $("#mkSessH").innerHTML = `<span class="ss-chip ss-${S.phase}"><i></i>${esc(S.label)}</span><span>${esc(S.sub)}</span>`; }
    $("#mkMini").innerHTML = `<span>시가 ${this.money(q.open, cur)}</span><span>고가 <b class="up">${this.money(q.high, cur)}</b></span><span>저가 <b class="down">${this.money(q.low, cur)}</b></span><span>거래량 ${q.volume != null ? bigNum(q.volume) : "-"}</span>`;
    $("#mkHold").innerHTML = hd ? `<section class="card"><div class="card-head"><h2>내 보유</h2><span class="tiny">키움 계좌</span></div>
      <div class="kv"><div>보유 수량<b>${won(hd.qty)}주</b></div><div>평균 단가<b>${m === "us" ? this.money(hd.avg_price, "USD") : won(hd.avg_price)}</b></div><div>평가 금액<b>${won(hd.value)}원</b></div></div>
      <div class="small ${tone(hd.pnl)}" style="font-weight:700">평가손익 ${signed(hd.pnl)}원 (${pct(hd.pnl_pct, 2)})</div></section>` : "";
    this.drawChart();
  },
  dayStats(pts, c, pmax, pmin) {
    const D = this.d, q = D.q, cur = q.currency, M = (v) => v == null ? "-" : this.money(v, cur);
    const hi = Math.max(q.high || pmax, pmax), lo = Math.min(q.low || pmin, pmin), now = q.price, prev = c.prev || q.prev;
    const pos = hi > lo ? Math.min(100, Math.max(0, (now - lo) / (hi - lo) * 100)) : 50;
    const r = (a, b) => b ? (a / b - 1) * 100 : null, P = (v) => v == null ? "-" : pct(v, 2);
    const avg = pts.reduce((a, x) => a + x.c, 0) / pts.length;
    const half = pts.slice(Math.floor(pts.length / 2)), mom = r(now, half[0].c);
    const cells = [["전일 대비", r(now, prev)], ["시가 대비", r(now, q.open)], ["고점 대비", r(now, hi)], ["저점 대비", r(now, lo)], ["오늘 평균가 대비", r(now, avg)], ["최근 흐름", mom]];
    return `<div class="ds">
      <div class="ds-range"><span class="down">저 ${M(lo)}</span><div class="ds-bar"><i style="left:${pos}%"></i></div><span class="up">고 ${M(hi)}</span></div>
      <div class="ds-grid">${cells.map(([k, v]) => `<div><span>${k}</span><b class="${tone(v)}">${P(v)}</b></div>`).join("")}</div>
      <div class="ds-row"><span>시가 <b>${M(q.open)}</b></span><span>전일 <b>${M(prev)}</b></span><span>변동폭 <b>${prev ? ((hi - lo) / prev * 100).toFixed(2) + "%" : "-"}</b></span><span>거래량 <b>${q.volume != null ? bigNum(q.volume) : "-"}</b></span></div>
    </div>`;
  },
  // 차트 크게 보기: 화면 전체를 쓰고, 휴대폰을 세로로 들고 있으면 화면을 90도 돌려 가로로 넓게 보여 준다
  zoom() {
    const D = this.d;
    if (!D || this.zv) return;
    const Z = this.zv = { D, range: D.range === "1d" ? "m5" : D.range, cd: D.range === "1d" ? null : D.cd, win: null };
    const ov = document.createElement("div");
    ov.className = "cz";
    ov.setAttribute("role", "dialog"); ov.setAttribute("aria-modal", "true"); ov.setAttribute("aria-label", `${D.q.name} 차트 크게 보기`);
    ov.innerHTML = `<div class="cz-in"><div class="cz-top">
        <div class="cz-name"><b>${esc(D.q.name)}</b><span id="czPx"></span></div>
        ${seg("크게기간", [["m5", "5분"], ["d", "일"], ["w", "주"], ["mo", "월"]], Z.range, "cz-seg")}
        <div class="cz-zm" role="group" aria-label="차트 확대·축소"><button type="button" data-cz="out" aria-label="축소(봉 더 많이)">−</button><button type="button" data-cz="reset" aria-label="처음 크기로">맞춤</button><button type="button" data-cz="in" aria-label="확대(봉 더 크게)">+</button></div>
        <button type="button" class="cz-x" id="czX" aria-label="크게 보기 닫기">${CLOSE_ICO}</button></div>
      <div class="cz-chart mk-chart" id="czChart"><div id="czPlot"><div class="empty small loading">불러오는 중</div></div><div class="cz-hit" aria-hidden="true"></div></div></div>`;
    document.body.appendChild(ov);
    Z.ov = ov; Z.prevOverflow = document.body.style.overflow; document.body.style.overflow = "hidden";
    const load = async () => {
      const r = Z.range;
      try { const cd = await GET(`/api/invest/candles/${D.m}/${encodeURIComponent(D.sym)}?tf=${r}&n=600`); if (this.zv === Z && Z.range === r) { Z.cd = cd; this.drawZoom(); } }   // 앞쪽 봉이 늘어도 오른쪽 기준(end)이라 보던 구간은 그대로
      catch (e) { if (this.zv === Z && Z.range === r) { Z.cd = { bars: [], error: e.message }; this.drawZoom(); } }
    };
    bindSeg(ov, "크게기간", (v) => { Z.range = v; Z.cd = null; Z.win = null; $("#czPlot").innerHTML = `<div class="empty small loading">불러오는 중</div>`; load(); });
    this.zoomGestures(Z);
    Z.close = () => {
      clearInterval(Z.timer); window.removeEventListener("resize", Z.fit); window.removeEventListener("keydown", Z.key); window.removeEventListener("hashchange", Z.close);
      document.body.style.overflow = Z.prevOverflow; ov.remove(); if (this.zv === Z) this.zv = null; $("#mkZoom")?.focus();
    };
    Z.key = (e) => { if (e.key === "Escape") Z.close(); };
    Z.fit = () => { if (this.zv === Z) this.drawZoom(); };
    $("#czX", ov).onclick = Z.close;
    addEventListener("resize", Z.fit); addEventListener("keydown", Z.key); addEventListener("hashchange", Z.close);
    Z.timer = setInterval(() => { if (this.zv === Z && !document.hidden) load(); }, 20000);
    this.drawZoom();
    load();
    $("#czX", ov).focus();
  },
  // 크게 보기의 손가락 동작
  // - 한 손가락으로 끌기: 앞뒤로 옮김(놓으면 조금 더 미끄러짐) · 톡 누르기: 그 봉의 가격 · 꾹 누른 채 움직이기: 봉마다 가격
  // - 두 손가락: 벌리면 확대, 오므리면 축소, 함께 끌면 옮김 · 두 번 톡: 맞춤
  // - 컴퓨터: 끌기 = 옮김, 올려 두기 = 가격, 휠 = 확대·축소, 가로 휠(Shift+휠) = 옮김
  zoomGestures(Z) {
    const hit = $(".cz-hit", Z.ov);
    let g = null, raf = 0, lastTap = { t: 0, x: 0, y: 0 }, glide = 0;
    Z.pinching = () => !!g;
    const total = () => ((Z.cd && Z.cd.bars) || []).length;
    const redraw = () => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; this.drawZoom(); }); };
    const rot = () => Z.ov.classList.contains("rot");
    const box = () => { const sv = $("#czPlot svg", Z.ov); return (sv || hit).getBoundingClientRect(); };
    const axis = (x, y) => (rot() ? y : x);                                         // 차트 가로축 방향의 화면 좌표
    const len = () => { const r = box(); return rot() ? r.height : r.width; };
    const frac = (x, y) => { const r = box(); return Math.max(0, Math.min(1, rot() ? (y - r.top) / r.height : (x - r.left) / r.width)); };
    const fromOf = () => total() - Z.win.end - Z.win.n;
    const setWin = (n, from) => {
      const T = total(); if (!T || !Z.win) return;
      n = Math.max(Math.min(20, T), Math.min(T, n));
      from = Math.max(0, Math.min(T - n, from));
      Z.win = { n, end: T - from - n };
      redraw();
    };
    const zoomAt = (n, f) => { const n0 = Z.win.n, from0 = fromOf(); n = Math.max(Math.min(20, total()), Math.min(total(), n)); setWin(n, from0 + f * n0 - f * n); };
    const cross = (x, y) => Z.cross && Z.cross.show({ clientX: x, clientY: y });
    const uncross = () => Z.cross && Z.cross.hide();
    const stopGlide = () => { cancelAnimationFrame(glide); glide = 0; };
    const startGlide = (v) => {                         // v: 1ms에 옮긴 봉 수
      let last = performance.now();
      const step = (t) => {
        const dt = t - last; last = t; v *= Math.pow(0.94, dt / 16);
        if (!Z.win || Math.abs(v) < 0.002) { glide = 0; return; }
        const T = total(), end = Math.max(0, Math.min(T - Z.win.n, Z.win.end + v * dt));
        if (end === Z.win.end) { glide = 0; return; }
        Z.win = { n: Z.win.n, end }; this.drawZoom(); glide = requestAnimationFrame(step);
      };
      glide = requestAnimationFrame(step);
    };
    const mid = (t) => [(t[0].clientX + t[1].clientX) / 2, (t[0].clientY + t[1].clientY) / 2];
    const dist = (t) => Math.hypot(t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY);
    hit.addEventListener("touchstart", (e) => {
      if (!Z.win) return;
      e.preventDefault(); stopGlide();
      if (e.touches.length >= 2) {
        clearTimeout(g && g.hold);
        const [mx, my] = mid(e.touches);
        g = { kind: "pinch", d: dist(e.touches), n: Z.win.n, from: fromOf(), f: frac(mx, my), a: axis(mx, my) };
        uncross(); return;
      }
      const t = e.touches[0];
      g = { kind: "wait", x: t.clientX, y: t.clientY, a0: axis(t.clientX, t.clientY), end0: Z.win.end, t0: Date.now(), vs: [] };
      g.hold = setTimeout(() => { if (g && g.kind === "wait") { g.kind = "cross"; cross(g.x, g.y); } }, 320);
    }, { passive: false });
    hit.addEventListener("touchmove", (e) => {
      if (!g) return;
      e.preventDefault();
      if (g.kind === "pinch" && e.touches.length >= 2) {
        const [mx, my] = mid(e.touches);
        const n = Math.max(Math.min(20, total()), Math.min(total(), g.n * g.d / Math.max(10, dist(e.touches))));
        const drag = (axis(mx, my) - g.a) / len() * n;                         // 함께 끈 만큼 지난 봉 쪽으로
        return setWin(n, g.from + g.f * g.n - g.f * n - drag);
      }
      const t = e.touches[0], a = axis(t.clientX, t.clientY);
      if (g.kind === "wait" && Math.abs(a - g.a0) > 7) { clearTimeout(g.hold); g.kind = "pan"; uncross(); }
      if (g.kind === "cross") return cross(t.clientX, t.clientY);
      if (g.kind === "pan") {
        const T = total(), end = Math.max(0, Math.min(T - Z.win.n, g.end0 + (a - g.a0) / len() * Z.win.n));   // 손가락을 오른쪽으로 → 지난 봉
        g.vs.push([performance.now(), end]); if (g.vs.length > 5) g.vs.shift();
        if (end !== Z.win.end) { Z.win = { n: Z.win.n, end }; redraw(); }
      }
    }, { passive: false });
    const touchEnd = (e) => {
      if (!g) return;
      if (g.kind === "pinch") { if (e.touches.length < 2) g = null; return; }
      clearTimeout(g.hold);
      const was = g; g = null;
      if (was.kind === "pan" && was.vs.length >= 2) {
        const [t1, e1] = was.vs[0], [t2, e2] = was.vs[was.vs.length - 1];
        if (t2 - t1 > 0 && performance.now() - t2 < 80) startGlide((e2 - e1) / (t2 - t1));
      } else if (was.kind === "wait") {                                   // 톡: 가격 보기 · 두 번 톡: 맞춤
        const now = Date.now();
        if (now - lastTap.t < 300 && Math.hypot(was.x - lastTap.x, was.y - lastTap.y) < 30) { Z.win = null; uncross(); redraw(); lastTap = { t: 0, x: 0, y: 0 }; return; }
        lastTap = { t: now, x: was.x, y: was.y };
        cross(was.x, was.y);
      }
    };
    hit.addEventListener("touchend", touchEnd); hit.addEventListener("touchcancel", touchEnd);
    // 컴퓨터(마우스)
    let drag = null;
    hit.addEventListener("pointerdown", (e) => { if (e.pointerType !== "mouse" || !Z.win) return; stopGlide(); drag = { a0: axis(e.clientX, e.clientY), end0: Z.win.end }; hit.setPointerCapture(e.pointerId); });
    hit.addEventListener("pointermove", (e) => {
      if (e.pointerType !== "mouse") return;
      if (!drag) return cross(e.clientX, e.clientY);
      const T = total(); Z.win = { n: Z.win.n, end: Math.max(0, Math.min(T - Z.win.n, drag.end0 + (axis(e.clientX, e.clientY) - drag.a0) / len() * Z.win.n)) }; redraw();
    });
    hit.addEventListener("pointerup", () => { drag = null; });
    hit.addEventListener("pointerleave", (e) => { if (e.pointerType === "mouse" && !drag) uncross(); });
    hit.addEventListener("wheel", (e) => {
      if (!Z.win) return;
      e.preventDefault(); stopGlide();
      const T = total();
      if (e.shiftKey || Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
        const d = (e.shiftKey ? e.deltaY : e.deltaX) / 40 * Math.max(1, Z.win.n / 40);
        Z.win = { n: Z.win.n, end: Math.max(0, Math.min(T - Z.win.n, Z.win.end - d)) };
        return redraw();
      }
      zoomAt(Z.win.n * Math.exp(e.deltaY / 400), frac(e.clientX, e.clientY));
    }, { passive: false });
    Z.ov.addEventListener("click", (e) => {
      const b = e.target.closest("[data-cz]");
      if (!b || !Z.win) return;
      if (b.dataset.cz === "reset") { Z.win = null; uncross(); return redraw(); }
      zoomAt(Z.win.n * (b.dataset.cz === "in" ? 0.67 : 1.5), 1);         // 버튼은 가장 최근 봉을 기준으로
    });
  },
  drawZoom() {
    const Z = this.zv;
    if (!Z) return;
    const D = Z.D, q = D.q, cur = q.currency, vw = innerWidth, vh = innerHeight, rot = vh > vw;
    Z.ov.classList.toggle("rot", rot);
    const inn = $(".cz-in", Z.ov);
    // 돌릴 때는 안쪽 상자를 가로(긴 변)×세로(짧은 변)로 잡고 90도 돌려 화면에 맞춘다
    inn.style.width = `${rot ? vh : vw}px`; inn.style.height = `${rot ? vw : vh}px`;
    $("#czPx", Z.ov).innerHTML = `${this.money(q.price, cur)} <i class="${tone(q.change)}">${pct(q.change_pct, 2)}</i>`;
    const el = $("#czPlot", Z.ov), area = $("#czChart", Z.ov);
    if (!Z.cd) return;
    const r = area.getBoundingClientRect(), k = 1.15;                    // SVG 1단위 ≈ 1.15px(글자 크기가 평소 차트와 비슷하게)
    const w = rot ? r.height : r.width, h = rot ? r.width : r.height;
    const W = Math.max(340, Math.round(w / k)), VH = 40, PH = Math.max(120, Math.round(h / k) - VH - 6 - 64 - 16);
    const o = { W, PH, VH, N: Math.max(60, Math.min(240, Math.round(W / 6.5))), rot, pinching: Z.pinching, axes: true, tf: Z.range };
    const T = (Z.cd.bars || []).length;
    if (!Z.win) Z.win = { n: Math.min(T, o.N), end: 0 };
    o.win = Z.win;
    o.hint = `${Math.round(Z.win.n)}봉/${T}봉 · 끌면 앞뒤로 · 두 손가락 벌리기·오므리기로 확대·축소 · 꾹 누르면 가격 · 두 번 누르면 맞춤`;
    const zm = $(".cz-zm", Z.ov);
    if (zm) { zm.querySelector('[data-cz="in"]').disabled = Z.win.n <= Math.min(20, T); zm.querySelector('[data-cz="out"]').disabled = Z.win.n >= T; }
    const fmt = (n) => this.money(n, cur), mark = Z.range === D.range ? D.mark : null;
    const keep = Z.cross && Z.cross.last;
    el.innerHTML = candleChart(Z.cd, cur, fmt, mark, D.at, o);
    Z.cross = bindCandle(el, Z.cd, fmt, mark, o);
    if (keep && Z.cross) Z.cross.show(keep);
  },
  drawChart() {
    const D = this.d, el = $("#mkChart");
    if (this.zv && this.zv.D === D && $("#czPx")) $("#czPx").innerHTML = `${this.money(D.q.price, D.q.currency)} <i class="${tone(D.q.change)}">${pct(D.q.change_pct, 2)}</i>`;
    if (!el) return;
    if (D.range !== "1d") { const a0 = !$("svg", el); el.innerHTML = candleChart(D.cd, D.q.currency, (n) => this.money(n, D.q.currency), D.mark, D.at); bindCandle(el, D.cd, (n) => this.money(n, D.q.currency), D.mark); this.drawAt();
      if (D.mark && D.range === "m5" && cdWin(D.cd, D.mark).gone && !D.markFell) { D.markFell = true; D.range = "d"; $$('[data-seg="기간"]').forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.v === "d"))); GET(`/api/invest/candles/${D.m}/${encodeURIComponent(D.sym)}?tf=d`).then((cd) => { if (this.d === D) { D.cd = cd; this.drawChart(); } }).catch(() => {}); } if (a0) $("svg", el)?.classList.add("anim"); return; }
    const c = D.chart || {}, pts = (c.points || []).filter((p) => p.c != null).slice();
    if (pts.length && D.q.price && pts[pts.length - 1].c !== D.q.price) pts.push({ t: "지금", c: D.q.price });
    if (pts.length < 2) { el.innerHTML = `<div class="empty small">${esc(c.error || "차트 자료가 아직 없어요")}</div>`; return; }
    const base = D.range === "1d" && c.prev ? c.prev : pts[0].c;
    const last = pts[pts.length - 1].c, up = last >= base, col = up ? "var(--up)" : "var(--down)";
    const vals = pts.map((p) => p.c).concat(D.range === "1d" && c.prev ? [c.prev] : []);
    const lo = Math.min(...vals), hi = Math.max(...vals), rr = hi - lo || 1;
    const W = 340, PW = 296, H = 170, VH = 26, pad = 8;
    const X = (i) => (i / (pts.length - 1)) * PW, Y = (v) => pad + (1 - (v - lo) / rr) * (H - pad * 2);
    const line = pts.map((p, i) => `${i ? "L" : "M"}${X(i).toFixed(1)},${Y(p.c).toFixed(1)}`).join("");
    const pmax = Math.max(...pts.map((x) => x.c)), pmin = Math.min(...pts.map((x) => x.c));
    const hiI = pts.findIndex((p) => p.c === pmax), loI = pts.findIndex((p) => p.c === pmin);
    const cur = D.q.currency, us = D.m === "us";
    const short = (v) => us ? Number(v).toFixed(2) : won(Math.round(v));
    const lab = (i, v, below) => { const x = X(i), anchor = x < 50 ? "start" : x > PW - 50 ? "end" : "middle"; return `<text x="${x.toFixed(1)}" y="${(Y(v) + (below ? 13 : -5)).toFixed(1)}" text-anchor="${anchor}" class="mk-lab">${below ? "최저" : "최고"} ${short(v)}</text>`; };
    // 가로 눈금 4줄: 가격 + 전일 대비 %
    const grid = [0, 1, 2, 3].map((k) => { const v = hi - (rr * k) / 3, y = Y(v), r = c.prev ? (v / c.prev - 1) * 100 : null;
      return `<line class="ax-g" x1="0" x2="${PW}" y1="${y.toFixed(1)}" y2="${y.toFixed(1)}"/><text class="ax-t" x="${PW + 5}" y="${(y + 1).toFixed(1)}">${short(v)}</text>${r == null ? "" : `<text class="ax-p ${r > 0 ? "up" : r < 0 ? "down" : ""}" x="${PW + 5}" y="${(y + 9).toFixed(1)}">${pct(r, 1)}</text>`}`; }).join("");
    // 시간 눈금
    const tl = (p) => String(p.t || "").slice(-5);
    const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => { const i = Math.round(f * (pts.length - 1)); return `<text class="ax-x" x="${X(i).toFixed(1)}" y="${H + VH + 12}" text-anchor="${f === 0 ? "start" : f === 1 ? "end" : "middle"}">${esc(pts[i].t === "지금" ? "지금" : tl(pts[i]))}</text>`; }).join("");
    // 거래량(오늘 5분봉)
    const vb = (D.vol || []), vmax = Math.max(1, ...vb.map((x) => x.v));
    const vols = vb.length ? vb.map((x, i) => { const w = PW / vb.length, hh = x.v / vmax * VH; return `<rect x="${(i * w + w * 0.15).toFixed(1)}" y="${(H + VH - hh).toFixed(1)}" width="${(w * 0.7).toFixed(1)}" height="${Math.max(0.5, hh).toFixed(1)}" fill="${x.c >= x.o ? "var(--up)" : "var(--down)"}" opacity=".45"/>`; }).join("") : "";
    const yl = Y(last), tagY = Math.max(8, Math.min(H - 8, yl));
    const anim = !$("svg", el);
    el.innerHTML = `<svg viewBox="0 -12 ${W} ${H + VH + 28}" class="mk-svg ${anim ? "anim" : ""}" role="img" aria-label="가격 차트">
      <defs><linearGradient id="mkG" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${col}" stop-opacity=".28"/><stop offset="1" stop-color="${col}" stop-opacity="0"/></linearGradient></defs>
      ${grid}
      ${D.range === "1d" && c.prev ? `<line x1="0" x2="${PW}" y1="${Y(c.prev).toFixed(1)}" y2="${Y(c.prev).toFixed(1)}" stroke="var(--ink3)" stroke-dasharray="3 4" stroke-width=".8"/><text class="ax-prev" x="2" y="${(Y(c.prev) - 3).toFixed(1)}">전일 종가 ${short(c.prev)}</text>` : ""}
      <path d="${line}L${PW},${H}L0,${H}Z" fill="url(#mkG)"/>
      <path class="ln" pathLength="1" d="${line}" fill="none" stroke="${col}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
      ${lab(hiI, pts[hiI].c, false)}${lab(loI, pts[loI].c, true)}
      <line class="lv-line" x1="0" x2="${PW}" y1="${yl.toFixed(1)}" y2="${yl.toFixed(1)}" stroke="${col}"/>
      <circle class="lv-ring" cx="${PW}" cy="${yl.toFixed(1)}" r="5" fill="${col}"/><circle cx="${PW - 0.5}" cy="${yl.toFixed(1)}" r="3.6" fill="${col}" stroke="var(--card)" stroke-width="1.5"/>
      <rect class="lv-tag" x="${PW + 3}" y="${(tagY - 7).toFixed(1)}" width="${W - PW - 3}" height="14" rx="4" fill="${col}"/><text class="lv-tagt" x="${PW + 6}" y="${(tagY + 3).toFixed(1)}">${short(last)}</text>
      <line class="ax-g" x1="0" x2="${PW}" y1="${H + 2}" y2="${H + 2}"/>${vols}${vb.length ? `<text class="ax-t" x="${PW + 5}" y="${H + VH - 2}">거래량</text>` : ""}
      ${ticks}
      <g id="mkCur" visibility="hidden"><line y1="0" y2="${H + VH}" stroke="var(--ink3)" stroke-width="1"/><circle r="4.5" fill="${col}" stroke="#fff" stroke-width="2"/></g>
    </svg><div class="mk-tip" id="mkTip" hidden></div><div class="lv-badge"><i></i>실시간</div>
    ${this.dayStats(pts, c, pmax, pmin)}`;
    if (D.range === "1d" && (!D.volAt || Date.now() - D.volAt > 60000)) {
      D.volAt = Date.now();
      GET(`/api/invest/candles/${D.m}/${encodeURIComponent(D.sym)}?tf=m5`).then((cd) => {
        const bs = cd.bars || [], day = bs.length ? bs[bs.length - 1].t.slice(0, 5) : "";
        D.vol = bs.filter((x) => x.t.slice(0, 5) === day);
        if (this.d === D && D.range === "1d" && $("#mkChart")) this.drawChart();
      }).catch(() => {});
    }
    const svg = $("svg", el), g = $("#mkCur", el), tip = $("#mkTip", el);
    const at = (ev) => {
      const r = svg.getBoundingClientRect(), x = ((ev.touches ? ev.touches[0].clientX : ev.clientX) - r.left) / (r.width * PW / W);
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

const ZOOM_ICO = `<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5"/><path d="M15.5 15.5 21 21M10.5 7.5v6M7.5 10.5h6"/></svg>`;
const CLOSE_ICO = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg>`;
const BELL = `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/></svg>`;
// pre: 보이는 구간 바로 앞의 봉들(있으면 이동평균선이 화면 왼쪽 끝부터 이어진다)
function maLine(bars, n, X, Y, pre = []) {
  const all = pre.concat(bars), p = pre.length, pts = [];
  for (let i = Math.max(n - 1, p); i < all.length; i++) { let s2 = 0; for (let k = i - n + 1; k <= i; k++) s2 += all[k].c; pts.push(`${pts.length ? "L" : "M"}${X(i - p).toFixed(1)},${Y(s2 / n).toFixed(1)}`); }
  return pts.join("");
}
// 추천 표시가 있으면 추천 시점이 보이게 구간을 잡는다
function cdWin(cd, mark, N = 80, win = null) {
  const all = (cd && cd.bars) || [];
  if (win) {   // 크게 보기에서 손가락으로 넓히고 옮긴 구간
    const n = Math.max(2, Math.min(all.length, Math.round(win.n))), from = Math.max(0, Math.min(all.length - n, all.length - Math.round(win.end) - n));
    const bars = all.slice(from, from + n);
    if (!mark) return { bars, ri: -1, ei: -1, from };
    const daily = /^\d{4}-/.test((all[0] || {}).t || ""), key = (iso) => (daily ? iso.slice(0, 10) : `${iso.slice(5, 7)}/${iso.slice(8, 10)} ${iso.slice(11, 16)}`);
    let ri = bars.findIndex((b) => b.t >= key(mark.ts));
    if (!daily && ri > 0 && bars[ri].t > key(mark.ts)) ri -= 1;
    return { bars, ri, ei: mark.end ? bars.findIndex((b) => b.t >= key(mark.end)) : -1, daily, from };
  }
  if (!mark) return { bars: all.slice(-N), ri: -1, ei: -1, from: Math.max(0, all.length - N) };
  const key = (iso, daily) => daily ? iso.slice(0, 10) : `${iso.slice(5, 7)}/${iso.slice(8, 10)} ${iso.slice(11, 16)}`;
  const daily = all.length && /^\d{4}-/.test(all[0].t);
  const rk = key(mark.ts, daily), ek = mark.end ? key(mark.end, daily) : null;
  let ri = all.findIndex((b) => b.t >= rk);
  if (!daily && ri > 0 && all[ri].t > rk) ri -= 1;  // 추천 시각이 들어 있는 봉
  if (ri < 0 || (!daily && all[0].t > rk)) return { bars: all.slice(-N), ri: -1, ei: -1, gone: !daily, from: Math.max(0, all.length - N) };
  const from = Math.max(0, Math.min(ri - Math.round(N / 8), all.length - N));
  const bars = all.slice(from, from + N);
  let ei = ek ? bars.findIndex((b) => b.t >= ek) : -1;
  return { bars, ri: ri - from, ei, daily, from };
}
// 차트 표시의 범례: ◆ 추천, ▲ 매수, ▼ 매도. 채운 것이 실전, 테두리만 있는 것이 모의
function atMarkLegend() {
  return `<span><i class="at-mk-i rec"></i>AI 추천</span><span><i class="at-mk-i buy real"></i>실전 매수</span><span><i class="at-mk-i sell real"></i>실전 매도</span><span><i class="at-mk-i buy paper"></i>모의 매수</span><span><i class="at-mk-i sell paper"></i>모의 매도</span>`;
}
// 자동매매 기록을 차트의 봉 위치로 옮긴다(같은 봉·같은 종류·같은 구분은 하나로 묶는다)
function atMarkPoints(bars, evs) {
  if (!evs || !evs.length || bars.length < 2) return [];
  const daily = /^\d{4}-/.test(bars[0].t), weekly = daily && bars.length > 1 && (new Date(bars[1].t) - new Date(bars[0].t)) / 864e5 > 2;
  const key = (iso) => (daily ? iso.slice(0, 10) : `${iso.slice(5, 7)}/${iso.slice(8, 10)} ${iso.slice(11, 16)}`);
  const out = new Map();
  for (const e of evs) {
    const k = key(e.ts);
    if (k < bars[0].t) continue;
    let i = bars.findIndex((b) => b.t > k);
    i = i < 0 ? bars.length - 1 : i - 1;
    if (i < 0) continue;
    if (!daily && bars[i].t.slice(0, 5) !== k.slice(0, 5)) continue;        // 다른 날의 봉에 붙이지 않는다
    if (daily && !weekly && bars[i].t !== k && i === bars.length - 1 && k > bars[i].t) { /* 오늘 봉이 아직 없으면 마지막 봉에 */ }
    const id = `${i}|${e.kind}|${e.mode || ""}`, g = out.get(id) || { i, kind: e.kind, mode: e.mode, n: 0, sum: 0 };
    g.n += 1; g.sum += e.price || 0;
    out.set(id, g);
  }
  return [...out.values()].map((g) => ({ ...g, price: g.n ? g.sum / g.n : 0 }));
}
// o: 크게 보기에서 쓰는 크기(W·PH·VH는 SVG 단위, N은 보여 줄 봉 수)
function candleChart(cd, cur, fmt, mark, evs, o = {}) {
  const win = cdWin(cd, mark, o.N, o.win), bars = win.bars;
  if (bars.length < 2) return `<div class="empty small">${esc((cd && cd.error) || "차트 자료가 아직 없어요")}</div>`;
  const W = o.W || 340, PH = o.PH || 150, VH = o.VH || 34, G = 6, n = bars.length, bw = W / n, TA = o.axes ? 16 : 0;
  const pts = atMarkPoints(bars, evs);
  const lv = (mark ? [mark.tp, mark.sl, mark.entry].filter(Boolean) : []).concat(pts.filter((p) => p.kind !== "rec" && p.price).map((p) => p.price));
  const hi = Math.max(...bars.map((b) => b.h), ...lv), lo = Math.min(...bars.map((b) => b.l), ...lv), rr = hi - lo || 1;
  const vmax = Math.max(1, ...bars.map((b) => b.v));
  const X = (i) => i * bw + bw / 2, Y = (v) => 6 + (1 - (v - lo) / rr) * (PH - 12);
  let mk = "";
  if (mark) {
    const x0 = win.ri >= 0 ? X(win.ri) : 0, x1 = win.ei >= 0 ? X(win.ei) : W;
    const hl = (v, cls, label) => `<line class="pk-l ${cls}" x1="${x0.toFixed(1)}" x2="${W}" y1="${Y(v).toFixed(1)}" y2="${Y(v).toFixed(1)}"/><text class="pk-t ${cls}" x="${W - 2}" y="${(Y(v) - 3).toFixed(1)}" text-anchor="end">${label} ${fmt(v)}</text>`;
    mk = `${win.ri >= 0 ? `<rect class="pk-zone" x="${x0.toFixed(1)}" y="0" width="${Math.max(0, x1 - x0).toFixed(1)}" height="${PH}"/>
        <rect class="pk-win" x="${x0.toFixed(1)}" y="${Y(mark.tp).toFixed(1)}" width="${Math.max(0, x1 - x0).toFixed(1)}" height="${Math.max(0, Y(mark.entry) - Y(mark.tp)).toFixed(1)}"/>
        <rect class="pk-lose" x="${x0.toFixed(1)}" y="${Y(mark.entry).toFixed(1)}" width="${Math.max(0, x1 - x0).toFixed(1)}" height="${Math.max(0, Y(mark.sl) - Y(mark.entry)).toFixed(1)}"/>
        <line class="pk-v" x1="${x0.toFixed(1)}" x2="${x0.toFixed(1)}" y1="0" y2="${PH + G + VH}"/>
        <text class="pk-t rec" x="${(x0 + 3).toFixed(1)}" y="${PH + G + 9}" text-anchor="${x0 > W - 70 ? "end" : "start"}">추천 ${esc(win.daily ? mark.ts.slice(5, 10) : mark.ts.slice(11, 16))}</text>
        <circle class="pk-dot" cx="${x0.toFixed(1)}" cy="${Y(mark.entry).toFixed(1)}" r="3.4"/>
        ${win.ei >= 0 ? `<line class="pk-v end" x1="${x1.toFixed(1)}" x2="${x1.toFixed(1)}" y1="0" y2="${PH}"/>` : ""}` : ""}
      ${hl(mark.tp, "tp", `익절 +${mark.tp_pct}%`)}${hl(mark.sl, "sl", `손절 −${mark.sl_pct}%`)}${hl(mark.entry, "en", "추천가")}`;
  }
  const body = bars.map((b, i) => {
    const up = b.c >= b.o, col = up ? "var(--up)" : "var(--down)", x = X(i);
    const y1 = Y(Math.max(b.o, b.c)), y2 = Y(Math.min(b.o, b.c)), w = Math.max(1, bw * 0.66);
    const vh = b.v / vmax * VH;
    return `<line x1="${x.toFixed(1)}" x2="${x.toFixed(1)}" y1="${Y(b.h).toFixed(1)}" y2="${Y(b.l).toFixed(1)}" stroke="${col}" stroke-width="1"/>
      <rect x="${(x - w / 2).toFixed(1)}" y="${y1.toFixed(1)}" width="${w.toFixed(1)}" height="${Math.max(1, y2 - y1).toFixed(1)}" fill="${col}"/>
      <rect x="${(x - w / 2).toFixed(1)}" y="${(PH + G + VH - vh).toFixed(1)}" width="${w.toFixed(1)}" height="${Math.max(0.5, vh).toFixed(1)}" fill="${col}" opacity=".35"/>`;
  }).join("");
  // 자동매매 표시: 추천 = 세로 점선 + ◆(위), 매수 = ▲(봉 아래), 매도 = ▼(봉 위). 실전은 채우고 모의는 테두리만
  const tri = (x, y, up, s) => (up ? `M${x.toFixed(1)} ${(y - s).toFixed(1)}L${(x + s).toFixed(1)} ${(y + s * 0.8).toFixed(1)}L${(x - s).toFixed(1)} ${(y + s * 0.8).toFixed(1)}Z` : `M${x.toFixed(1)} ${(y + s).toFixed(1)}L${(x + s).toFixed(1)} ${(y - s * 0.8).toFixed(1)}L${(x - s).toFixed(1)} ${(y - s * 0.8).toFixed(1)}Z`);
  const am = pts.map((p) => {
    let x = X(p.i);
    const b = bars[p.i];
    if (p.kind === "rec") return `<line class="am-v" x1="${x.toFixed(1)}" x2="${x.toFixed(1)}" y1="9" y2="${PH}"/><path class="am rec" d="M${x.toFixed(1)} 1.5L${(x + 4).toFixed(1)} 6L${x.toFixed(1)} 10.5L${(x - 4).toFixed(1)} 6Z"/>`;
    const up = p.kind === "buy", both = pts.some((o) => o !== p && o.i === p.i && o.kind === p.kind);
    const y = Math.max(8, Math.min(PH - 8, up ? Y(b.l) + 10 : Y(b.h) - 10));
    if (both) x += p.mode === "real" ? -5.5 : 5.5;                          // 같은 봉에 실전과 모의가 함께면 나란히(왼쪽 실전, 오른쪽 모의)
    const tx = x > W - 26 ? x - 7 : x + 7;
    return `${p.price ? `<line class="am-p ${p.kind}" x1="${(X(p.i) - bw * 0.9).toFixed(1)}" x2="${(X(p.i) + bw * 0.9).toFixed(1)}" y1="${Y(p.price).toFixed(1)}" y2="${Y(p.price).toFixed(1)}"/>` : ""}
      <path class="am ${p.kind} ${p.mode}" d="${tri(x, y, up, 4.6)}"/>${p.n > 1 ? `<text class="am-n ${p.kind}" x="${tx.toFixed(1)}" y="${(y + 3).toFixed(1)}" text-anchor="${x > W - 26 ? "end" : "start"}">${p.n}</text>` : ""}`;
  }).join("");
  // 크게 보기의 축: 오른쪽 가격 눈금(가로 점선) · 아래 시간 눈금 · 봉이 충분히 크면 봉마다 등락률
  let ax = "";
  if (o.axes) {
    const raw = (hi - lo) / 4, mag = Math.pow(10, Math.floor(Math.log10(raw || 1))), st = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((m) => m >= raw) || raw;
    for (let v = Math.ceil(lo / st) * st; v <= hi; v += st) {
      const y = Y(v);
      if (y < 10 || y > PH - 4) continue;
      ax += `<line class="ax-g" x1="0" x2="${W}" y1="${y.toFixed(1)}" y2="${y.toFixed(1)}"/><text class="ax-pv" x="${W - 2}" y="${(y - 2).toFixed(1)}" text-anchor="end">${fmt(v)}</text>`;
    }
    const from = win.from || 0, step = Math.max(1, Math.ceil(64 / bw)), minute = !/^\d{4}-/.test(bars[0].t);
    const lab = (b, prev) => {
      if (minute) { const d = b.t.slice(0, 5), hm = b.t.slice(6, 11); return !prev || prev.t.slice(0, 5) !== d ? [d, true] : [hm, false]; }
      const y = b.t.slice(0, 4), m = b.t.slice(5, 7), d = b.t.slice(8, 10);
      if (o.tf === "mo") return !prev || prev.t.slice(0, 4) !== y ? [y, true] : [`${+m}월`, false];
      if (o.tf === "w") return !prev || prev.t.slice(0, 4) !== y ? [`${y}.${m}`, true] : [`${+m}/${+d}`, false];
      return !prev || prev.t.slice(0, 4) !== y ? [y, true] : !prev || prev.t.slice(5, 7) !== m ? [`${+m}월`, true] : [`${+m}/${+d}`, false];
    };
    let prev = null;
    bars.forEach((b, i) => {
      if ((from + i) % step) return;
      const [t, strong] = lab(b, prev); prev = b;
      const x = X(i);
      ax += `<line class="ax-tk" x1="${x.toFixed(1)}" x2="${x.toFixed(1)}" y1="${PH + G + VH}" y2="${PH + G + VH + 3}"/><text class="ax-tm${strong ? " on" : ""}" x="${x.toFixed(1)}" y="${PH + G + VH + 13}" text-anchor="${x < 18 ? "start" : x > W - 18 ? "end" : "middle"}">${esc(t)}</text>`;
    });
    if (bw >= 22) {
      const all = cd.bars;
      bars.forEach((b, i) => {
        const pb = all[from + i - 1]; if (!pb) return;
        const ch = (b.c / pb.c - 1) * 100;
        const d = bw >= 30 ? 2 : 1, flat = Math.abs(ch) < 0.5 * Math.pow(10, -d);
        ax += `<text class="ax-ch ${flat ? "" : ch > 0 ? "up" : "down"}" x="${X(i).toFixed(1)}" y="${Math.max(9, Y(b.h) - 3).toFixed(1)}" text-anchor="middle">${flat ? "" : ch > 0 ? "+" : "−"}${Math.abs(ch).toFixed(d)}</text>`;
      });
    }
  }
  const bhi = Math.max(...bars.map((b) => b.h)), blo = Math.min(...bars.map((b) => b.l));
  const hiI = bars.findIndex((b) => b.h === bhi), loI = bars.findIndex((b) => b.l === blo);
  const lab = (i, v, below) => { const x = X(i), a = x < 60 ? "start" : x > W - 60 ? "end" : "middle"; return `<text x="${x.toFixed(1)}" y="${(Y(v) + (below ? 11 : -4)).toFixed(1)}" text-anchor="${a}" class="mk-lab">${fmt(v)}</text>`; };
  return `<div class="cd-legend tiny"><span><i style="background:#F2A900"></i>5일선</span><span><i style="background:#8E44AD"></i>20일선</span></div>${pts.length ? `<div class="at-mk-lg tiny">${atMarkLegend()}</div>` : evs && evs.length ? `<div class="tiny at-mk-none">이 기간에는 자동매매 기록이 없어요. 5분·일 차트에서 볼 수 있어요.</div>` : ""}<div class="tiny cd-tip" id="cdTip">${o.hint || "차트를 누르면 그날 시가·고가·저가·종가가 보여요"}</div>
    <svg viewBox="0 0 ${W} ${PH + G + VH + TA}" class="mk-svg cd-svg" role="img" aria-label="캔들 차트">
    ${ax}${body}${mk}${am}
    <path d="${maLine(bars, 5, X, Y, cd.bars.slice(Math.max(0, (win.from || 0) - 4), win.from || 0))}" fill="none" stroke="#F2A900" stroke-width="1.2"/>
    <path d="${maLine(bars, 20, X, Y, cd.bars.slice(Math.max(0, (win.from || 0) - 19), win.from || 0))}" fill="none" stroke="#8E44AD" stroke-width="1.2"/>
    ${lab(hiI, bhi, false)}${lab(loI, blo, true)}
    <line x1="0" x2="${W}" y1="${PH + G / 2}" y2="${PH + G / 2}" stroke="var(--line2)" stroke-width=".5"/>
    <line class="lv-line" x1="0" x2="${W}" y1="${Y(bars[n - 1].c).toFixed(1)}" y2="${Y(bars[n - 1].c).toFixed(1)}" stroke="${bars[n - 1].c >= bars[n - 1].o ? "var(--up)" : "var(--down)"}"/>
    <circle class="lv-ring" cx="${X(n - 1).toFixed(1)}" cy="${Y(bars[n - 1].c).toFixed(1)}" r="4" fill="${bars[n - 1].c >= bars[n - 1].o ? "var(--up)" : "var(--down)"}"/>
    <g id="cdCur" visibility="hidden"><line y1="0" y2="${PH + G + VH}" stroke="var(--ink3)" stroke-width=".7" stroke-dasharray="2 2"/>${o.axes ? `<line class="cd-hl" x1="0" x2="${W}" y1="0" y2="0"/><g class="cd-pt"><rect x="${W - 58}" y="-7" width="58" height="13" rx="3"/><text x="${W - 3}" y="3" text-anchor="end"></text></g><g class="cd-tt"><rect y="${PH + G + VH + 2}" height="13" rx="3"/><text y="${PH + G + VH + 12}" text-anchor="middle"></text></g>` : ""}</g>
  </svg><div class="tiny cd-src">${esc(bars[0].t)} ~ ${esc(bars[bars.length - 1].t)} · ${esc(cd.source || "")}</div>`;
}
function bindCandle(el, cd, fmt, mark, o = {}) {
  const svg = $("svg", el), win = cdWin(cd, mark, o.N, o.win), bars = win.bars;
  if (!svg || bars.length < 2) return null;
  const g = $("#cdCur", el), tip = $("#cdTip", el), W = o.W || 340, bw = W / bars.length;
  const lo = Math.min(...bars.map((b) => b.l)), vb = svg.viewBox.baseVal;
  const H = { last: null };
  const at = (ev) => {
    if (o.pinching && o.pinching() && ev.type) return;
    const r = svg.getBoundingClientRect(), p = ev.touches ? ev.touches[0] : ev;
    // 크게 보기에서 화면을 90도 돌렸으면 손가락의 세로 위치가 차트의 가로 위치다
    const x = (o.rot ? (p.clientY - r.top) / r.height : (p.clientX - r.left) / r.width) * W;
    const i = Math.max(0, Math.min(bars.length - 1, Math.floor(x / bw))), b = bars[i], cx = i * bw + bw / 2;
    g.setAttribute("visibility", "visible"); $("line", g).setAttribute("x1", cx); $("line", g).setAttribute("x2", cx);
    const pb = i ? bars[i - 1] : (cd.bars || [])[(win.from || 0) - 1];
    const ch = pb ? (b.c / pb.c - 1) * 100 : 0;
    tip.innerHTML = `${esc(b.t)} 시 ${fmt(b.o)} 고 ${fmt(b.h)} 저 ${fmt(b.l)} 종 <b class="${tone(ch)}">${fmt(b.c)}</b> ${pct(ch, 2)}${o.axes ? ` · 거래량 ${bigNum(b.v)}` : ""}`;
    if (o.axes) {   // 종가 가로선과 가격표, 아래 시간표
      const PH = o.PH, hi = Math.max(...bars.map((x2) => x2.h)), Y = (v) => 6 + (1 - (v - lo) / ((hi - lo) || 1)) * (PH - 12);
      const ys = (mark ? [mark.tp, mark.sl, mark.entry].filter(Boolean) : []);
      const hi2 = Math.max(hi, ...ys), lo2 = Math.min(lo, ...ys), Y2 = (v) => 6 + (1 - (v - lo2) / ((hi2 - lo2) || 1)) * (PH - 12);
      const yy = (ys.length ? Y2 : Y)(b.c);
      $(".cd-hl", g).setAttribute("y1", yy); $(".cd-hl", g).setAttribute("y2", yy);
      $(".cd-pt", g).setAttribute("transform", `translate(0 ${yy.toFixed(1)})`); $(".cd-pt text", g).textContent = fmt(b.c);
      const tt = $(".cd-tt", g), tw = Math.max(40, b.t.length * 5.6 + 8), tx = Math.max(tw / 2, Math.min(W - tw / 2, cx));
      $("rect", tt).setAttribute("x", tx - tw / 2); $("rect", tt).setAttribute("width", tw); $("text", tt).setAttribute("x", tx); $("text", tt).textContent = b.t;
    }
    H.last = { clientX: p.clientX, clientY: p.clientY };
  };
  const hide = () => { g.setAttribute("visibility", "hidden"); H.last = null; tip.textContent = o.hint || "차트를 누르면 그날 시가·고가·저가·종가가 보여요"; };
  svg.addEventListener("pointerdown", at); svg.addEventListener("pointermove", at);
  svg.addEventListener("pointerleave", hide);
  H.show = at; H.hide = hide;
  void vb;
  return H;
}
async function alertSheet(a) {
  const list = (await GET("/api/invest/alerts").catch(() => [])).filter((x) => x.sym === a.sym);
  const us = a.mkt === "us", f = (n) => us ? Number(n).toFixed(2) : won(n);
  openSheet(`${a.name} 가격 알림`, `
    <div class="small muted">현재가 <b>${us ? "$" : ""}${f(a.price)}${us ? "" : "원"}</b> · 목표가에 닿으면 휴대폰으로 알려 드려요(설정의 ntfy 알림).</div>
    <div class="chips">${[-10, -5, -3, 3, 5, 10].map((p) => `<button type="button" class="chip" data-ap="${p}">${p > 0 ? "+" : ""}${p}%</button>`).join("")}</div>
    <div class="ai-cr-set"><input id="alPx" class="r" inputmode="decimal" placeholder="목표 가격" value="${f(us ? a.price * 1.05 : Math.round(a.price * 1.05 / krTick(a.price * 1.05)) * krTick(a.price * 1.05))}"><button class="btn dark" id="alAdd">알림 추가</button></div>
    <div class="tiny" id="alHint"></div>
    <div class="list">${list.length ? list.map((x) => `<div class="item"><span class="ico ${x.active ? "y" : ""}">${x.cond === "above" ? "이상" : "이하"}</span>
      <span class="main"><b>${us ? "$" : ""}${f(x.price)}${us ? "" : "원"}</b><span class="sub">${x.active ? "기다리는 중" : `알림 보냄 · ${esc((x.fired_at || "").slice(5, 16).replace("T", " "))}`}</span></span>
      <button class="btn sm soft" data-adel="${x.id}">지우기</button></div>`).join("") : `<div class="empty small">아직 알림이 없어요</div>`}</div>`, (box) => {
    const hint = () => { const v = Number(String($("#alPx").value).replace(/,/g, "")); $("#alHint").textContent = v ? `${v >= a.price ? "이 가격 이상으로 오르면" : "이 가격 이하로 내리면"} 알려요 (${pct((v / a.price - 1) * 100, 1)})` : ""; };
    $("#alPx").oninput = hint; hint();
    box.onclick = async (e) => {
      const p = e.target.closest("[data-ap]"), d = e.target.closest("[data-adel]");
      if (p) { const v = a.price * (1 + Number(p.dataset.ap) / 100); $("#alPx").value = us ? v.toFixed(2) : won(Math.round(v / krTick(v)) * krTick(v)); hint(); }
      if (d) { await run(() => api("DELETE", `/api/invest/alerts/${d.dataset.adel}`), "지웠어요"); alertSheet(a); }
      if (e.target.id === "alAdd") { await run(() => api("POST", "/api/invest/alerts", { mkt: a.mkt, sym: a.sym, name: a.name, price: $("#alPx").value }), "알림을 걸었어요"); alertSheet(a); }
    };
  });
}
const STAR = `<svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3.2l2.7 5.6 6.1.8-4.5 4.2 1.1 6.1L12 17l-5.4 2.9 1.1-6.1L3.2 9.6l6.1-.8z" fill="currentColor" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/></svg>`;


// =====================================================================
// Claude AI 도우미(투자): 답변을 글자가 나오는 대로 보여 준다
// =====================================================================
// AI 요청은 서버에서 '작업'으로 끝까지 돈다. 연결이 끊기면(화면 꺼짐, 앱 전환, 통신 불안) 같은 작업에 다시 붙어 이어 받는다.
async function aiStream(payload, onEvent, signal) {
  if (ON_PAGES && !store.get("server")) throw new NeedConnect("서버 주소를 먼저 넣어 주세요");
  if (lock.on) throw new Locked("잠겨 있어요");
  let job = "", got = 0, finished = false;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  if (signal) signal.addEventListener("abort", () => { if (job) fetch(apiBase() + `/api/ai/job/${job}/cancel`, { method: "POST", headers: authHeaders() }).catch(() => {}); });
  const open = async (resume) => {
    const res = resume
      ? await fetch(apiBase() + `/api/ai/job/${job}?from=${got}`, { headers: authHeaders(), signal })
      : await fetch(apiBase() + "/api/ai/ask", { method: "POST", headers: authHeaders({ "Content-Type": "application/json" }), body: JSON.stringify(payload), signal });
    if (res.status === 401) {
      const d = await res.json().catch(() => ({}));
      if (d.code === "locked" || d.code === "setup") { lock.show(d.code === "setup" ? "setup" : "unlock"); throw new Locked(d.error || "잠겨 있어요"); }
      throw new NeedConnect("접속 토큰이 맞지 않아요");
    }
    if (res.status === 404 && resume) { const e = new Error("연결이 끊긴 사이 서버가 다시 켜져서 이어 받지 못했어요. 다시 받아 주세요"); e.fatal = true; throw e; }
    if (!res.ok || !res.body) { const e = new Error(`AI 요청이 실패했어요 (${res.status})`); e.fatal = !resume; throw e; }
    return res.body.getReader();
  };
  const pump = async (reader) => {
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const r = await reader.read();
      if (r.done) return;
      buf += dec.decode(r.value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, i).trim(); buf = buf.slice(i + 1);
        if (!line) continue;
        let ev;
        try { ev = JSON.parse(line); } catch (e) { continue; }  // 깨진 줄은 건너뜀
        if (ev.t === "ping") continue;
        if (ev.t === "job") { job = ev.id; continue; }
        got += 1;
        if (ev.t === "done" || ev.t === "error") finished = true;
        try { onEvent(ev); } catch (e) { /* 화면 그리기 오류는 흐름을 막지 않게 */ }
      }
    }
  };
  let reader;
  try { reader = await open(false); }
  catch (e) { if (e.name === "AbortError") return; if (e instanceof NeedConnect || e instanceof Locked || e.fatal) throw e; throw new Error("서버에 연결하지 못했어요"); }
  let tries = 0;
  for (;;) {
    try { await pump(reader); if (finished || !job) return; }           // 서버가 끝까지 보냈으면 끝
    catch (e) { if (e.name === "AbortError" || (signal && signal.aborted)) return; if (!job) throw new Error("연결이 끊겼어요. 다시 받아 주세요"); }
    // 여기 왔으면 도중에 끊긴 것: 같은 작업에 다시 붙는다(화면이 꺼져 있는 동안은 기다린다)
    for (;;) {
      if (signal && signal.aborted) return;
      while (document.hidden) await sleep(700);
      if (++tries > 40) throw new Error("연결이 계속 끊겨요. 추천은 노트북에서 끝까지 만들고 있으니, 잠시 뒤 '지난 추천'에서 확인해 주세요");
      try { onEvent({ t: "status", msg: "연결이 끊겨 다시 붙는 중" }); } catch (e) { /* 무시 */ }
      await sleep(Math.min(4000, 600 * tries));
      try { reader = await open(true); break; }
      catch (e) { if (e.name === "AbortError") return; if (e instanceof NeedConnect || e instanceof Locked || e.fatal) throw e; }
    }
  }
}
function mdLite(src) {  // 안전한 간단 마크다운: 소제목·목록·굵게
  const inl = (t) => esc(t).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
  const out = []; let list = false;
  for (const raw of String(src).split("\n")) {
    const l = raw.trimEnd();
    const li = l.match(/^\s*[-*•]\s+(.*)/), h = l.match(/^#{1,4}\s+(.*)/);
    if (li) { if (!list) { out.push("<ul>"); list = true; } out.push(`<li>${inl(li[1])}</li>`); continue; }
    if (list) { out.push("</ul>"); list = false; }
    if (h) out.push(`<h3>${inl(h[1])}</h3>`);
    else if (l.trim()) out.push(`<p>${inl(l)}</p>`);
  }
  if (list) out.push("</ul>");
  return out.join("");
}
function creditHtml(cr) {
  if (!cr || !cr.set) return `<h2>남은 API 크레딧</h2>
    <div class="small muted">콘솔 결제 화면의 잔액을 한 번 넣어 두면, 그 뒤 moa가 쓴 금액을 빼서 실시간으로 보여 줘요.</div>
    <div class="ai-cr-set"><input id="crUsd" class="r" inputmode="decimal" placeholder="예: 100.00"><button class="btn dark" id="crSave">잔액 맞추기</button></div>
    <a class="tiny" href="https://platform.claude.com/settings/billing" target="_blank" rel="noopener">콘솔 결제 화면 열기 ↗</a>`;
  const lowish = cr.exhausted || cr.left_pct < 10;
  return `<div class="card-head"><h2>남은 API 크레딧</h2><span class="tiny live-dot on">실시간</span></div>
    <div class="ai-cr-big ${lowish ? "low" : ""}">$${Number(cr.left).toFixed(2)}<small> / $${Number(cr.start_usd).toFixed(2)}</small></div>
    <div class="bar ${lowish ? "over" : ""}"><div style="width:${Math.min(100, cr.left_pct)}%"></div></div>
    <div class="tiny">${esc(cr.since.slice(5, 16).replace("T", " "))}에 맞춘 뒤 $${Number(cr.spent).toFixed(4)} 사용 · 기준 ${esc(cr.source)}${cr.spent_admin != null ? ` (콘솔 집계는 약 5분 늦게 반영)` : ""}</div>
    ${cr.exhausted ? `<div class="notice warn">Claude가 크레딧 부족이라고 알려 왔어요. 충전 뒤 잔액을 다시 맞춰 주세요.</div>` : ""}
    ${cr.admin_error ? `<div class="tiny" style="color:var(--up)">${esc(cr.admin_error)}</div>` : ""}
    <details class="ai-cr-more"><summary class="tiny">콘솔 잔액으로 다시 맞추기</summary>
      <div class="ai-cr-set"><input id="crUsd" class="r" inputmode="decimal" placeholder="콘솔에 보이는 잔액($)"><button class="btn" id="crSave">맞추기</button></div>
      <a class="tiny" href="https://platform.claude.com/settings/billing" target="_blank" rel="noopener">콘솔 결제 화면 열기 ↗</a></details>`;
}
function bindCredit(box, after) {
  const b = $("#crSave", box);
  if (b) b.onclick = async () => { const v = $("#crUsd", box).value; await run(() => api("POST", "/api/ai/credit", { usd: v }), "잔액을 맞췄어요"); after && after(); };
}
const AI_TITLES = { auto: "AI 자동매매 복기 · 개선안", learn: "AI 추천 복기 · 규칙 갱신", stock: "AI 종목 정리", portfolio: "AI 포트폴리오 점검", dca: "AI 적립 점검", review: "AI 단타 복기", sweep: "AI 훑기 해석", chat: "AI에게 묻기" };
function aiSheet(kind, payload = {}) {
  const chat = kind === "chat";
  const S = { history: [], ctl: null, busy: false };
  openSheet(AI_TITLES[kind] || "AI", `
    <div class="ai-box">
      <div id="aiMeterTop" class="cm-slot"></div>
      <div id="aiThread" class="ai-thread"></div>
      ${chat ? `<div class="ai-chips">${["내 포트폴리오에서 가장 위험한 부분은?", "관심종목 중 이번 주 뉴스가 큰 종목은?", "오늘 미국장 주요 이슈 정리해 줘"].map((q) => `<button type="button" class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>
        <div class="ai-ask"><textarea id="aiQ" rows="2" placeholder="예: 엔비디아 실적 발표 전에 볼 점은?"></textarea><button class="btn dark" id="aiSend">묻기</button></div>` : ""}
      <div class="tiny">AI 답변은 참고용이에요. 틀릴 수 있고 매수·매도 지시가 아니에요. AI는 주문하지 않아요.</div>
    </div>`, (box) => {
    const thread = $("#aiThread", box);
    let M = null;
    const top = async () => { try { M = await aiMeter.get(kind); if ($("#aiMeterTop")) $("#aiMeterTop").innerHTML = meterHTML(M); } catch (e) { /* 생략 */ } };
    top();
    const ask = async (extra) => {
      if (S.busy) return;
      S.busy = true;
      const turn = document.createElement("div");
      turn.className = "ai-turn";
      turn.innerHTML = `${extra.question ? `<div class="ai-q">${esc(extra.question)}</div>` : ""}
        <div class="ai-status"><i class="ai-dot"></i><span>요청하는 중</span></div><div class="ai-ans"></div><div class="ai-src"></div><div class="ai-cost tiny"></div>`;
      thread.appendChild(turn);
      const st = $(".ai-status span", turn), ans = $(".ai-ans", turn), src = $(".ai-src", turn), cost = $(".ai-cost", turn);
      let text = "", sources = [], raf = 0;
      const paint = () => { raf = 0; ans.innerHTML = mdLite(text); };
      S.ctl = new AbortController();
      try {
        await aiStream({ kind, ...payload, ...extra, history: S.history }, (ev) => {
          if (ev.t === "status") st.textContent = ev.msg;
          else if (ev.t === "text") { text += ev.text; $(".ai-status", turn).hidden = true; if (!raf) raf = requestAnimationFrame(paint); }
          else if (ev.t === "reset") { text = ""; if (!raf) raf = requestAnimationFrame(paint); }
          else if (ev.t === "source") {
            sources.push(ev);
            src.innerHTML = `<div class="tiny">찾아본 자료 ${sources.length}건</div>` + sources.map((x) => `<a href="${safeUrl(x.url)}" target="_blank" rel="noopener">${esc(x.title)}${x.age ? ` <span>${esc(x.age)}</span>` : ""}</a>`).join("");
          } else if (ev.t === "error") {
            $(".ai-status", turn).hidden = true;
            ans.innerHTML += `<div class="notice warn">${esc(ev.msg)}${/키를 넣어/.test(ev.msg) ? ` <a href="#settings/ai">AI 설정 열기</a>` : ""}</div>`;
          } else if (ev.t === "done") {
            paint();
            cost.innerHTML = meterHTML(M || { fx: ev.fx, credit: ev.credit }, ev);
            aiMeter.clear(); top();
          }
        }, S.ctl.signal);
        if (text) S.history.push({ role: "user", content: extra.question || AI_TITLES[kind] }, { role: "assistant", content: text });
      } catch (e) {
        $(".ai-status", turn).hidden = true;
        if (e instanceof NeedConnect) { closeSheet(); location.hash = "#connect"; return; }
        ans.innerHTML += `<div class="notice warn">${esc(e.message)}</div>`;
      } finally {
        if (raf) { cancelAnimationFrame(raf); paint(); }
        S.busy = false;
      }
    };
    if (chat) {
      const go = () => { const q = $("#aiQ").value.trim(); if (!q) return; $("#aiQ").value = ""; ask({ question: q }); };
      $("#aiSend", box).onclick = go;
      $("#aiQ", box).onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); go(); } };
      box.addEventListener("click", (e) => { const c = e.target.closest("[data-q]"); if (c) ask({ question: c.dataset.q }); });
      setTimeout(() => $("#aiQ")?.focus(), 250);
    } else ask({});
  });
  sheet.addEventListener("close", () => S.ctl && S.ctl.abort(), { once: true });
}
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-ai]");
  if (!b || b.closest("#sheetBody")) return;
  e.preventDefault(); e.stopPropagation();
  aiSheet(b.dataset.ai, b.dataset.sym ? { sym: b.dataset.sym, mkt: b.dataset.mkt } : {});
}, true);


// ---------- 목록 → 상세: 어떤 줄이든 누르면 이어서 볼 수 있게 ----------
const DT = [];
// '주문 가능'이 왜 그 금액인지: 총자산이 어디에 들어가 있는지 풀어서 보여 준다
function cashDetail(a) {
  const c = a.cash_detail || {}, u = a.us || {}, W = (n) => (n == null ? null : won(n) + "원");
  const krEval = Math.max(0, (a.eval || 0) - (u.eval || 0)), tied = Math.max(0, (a.cash || 0) - (a.orderable || 0));
  const why = [];
  if (krEval + (u.eval || 0) > 0) why.push(`주식에 ${won(krEval + (u.eval || 0))}원이 들어가 있어요.`);
  if (u.usd_krw) why.push(`달러로 ${usd(u.usd)}(약 ${won(u.usd_krw)}원)가 있어요. 달러는 국내 주식 주문 가능 금액에 들어가지 않아요.`);
  if (c.margin) why.push(`매수 주문의 증거금으로 ${won(c.margin)}원이 묶여 있어요(아직 체결 안 된 주문이나 결제 전인 매수). 미체결 주문이면 주문 탭에서 취소하면 풀려요.`);
  else if (tied > 0) why.push(`예수금 중 ${won(tied)}원은 지금 주문에 쓸 수 없어요(결제 전 금액·미수 등).`);
  if (c.unpaid) why.push(`미수금 ${won(c.unpaid)}원이 있어요.`);
  if (!why.length) why.push("예수금이 거의 그대로 주문 가능 금액이에요. 다른 계좌에 돈이 있다면, 설정 → 계좌 연결에 넣은 키의 계좌가 맞는지 확인해 보세요.");
  return { title: "주문 가능 금액", head: `<b style="font-size:22px">${won(a.orderable)}원</b><div class="tiny">키움이 알려 준 '국내 주식을 지금 바로 살 수 있는 원화'예요${a.acct_no ? ` · 계좌 ${esc(String(a.acct_no).slice(0, 4))}••••` : ""}</div>`,
    rows: [["총자산", W(a.total)], ["국내 주식 평가", W(krEval)], u.eval ? ["미국 주식 평가", W(u.eval)] : null, ["원화 예수금", W(a.cash)],
      u.usd ? ["달러 예수금", `${usd(u.usd)}${u.usd_krw ? ` (약 ${won(u.usd_krw)}원)` : ""}`] : null, u.usd_orderable != null && u.usd ? ["달러 주문 가능", usd(u.usd_orderable)] : null,
      c.margin ? ["매수 주문에 묶인 돈(증거금)", W(c.margin)] : null, c.d2 != null ? ["이틀 뒤 예수금(추정)", W(c.d2)] : null,
      c.withdrawable != null ? ["출금 가능", W(c.withdrawable)] : null, ["주문 가능(원화)", W(a.orderable), "up"]],
    note: why.join("\n"), links: [["주문·미체결 보기", "#invest/trade"], ["계좌 연결 확인", "#settings/accounts"]] };
}
function dt(o) { DT.push(o); if (DT.length > 600) DT.splice(0, 200); return `data-dt="${DT.indexOf(o)}"`; }
function detailSheet(o) {
  if (o.run) return o.run();
  openSheet(o.title || "자세히", `
    ${o.head ? `<div class="dt-head">${o.head}</div>` : ""}
    <dl class="dt-rows">${(o.rows || []).filter((r) => r && r[1] != null && r[1] !== "").map(([k, v, cls]) => `<div><dt>${esc(k)}</dt><dd class="${cls || ""}">${esc(String(v))}</dd></div>`).join("")}</dl>
    ${o.note ? `<div class="notice" style="white-space:pre-line">${esc(o.note)}</div>` : ""}
    <div class="dt-links">${(o.links || []).filter(Boolean).map(([l, h, cls]) => `<a class="btn ${cls || ""}" href="${h}" data-close>${esc(l)}</a>`).join("")}</div>`,
  (box) => box.addEventListener("click", (e) => { if (e.target.closest("[data-close]")) closeSheet(); }));
}
document.addEventListener("keydown", (e) => { if (e.key === "Enter" && e.target.matches && e.target.matches('[data-cashwhy]')) e.target.click(); });
// 홈·투자 홈의 자동매매 카드: 링크가 자동매매 화면으로 데려가기 전에, 그 자동매매의 현황이 바로 열리게 해 둔다
document.addEventListener("click", (e) => {
  const g = e.target.closest && e.target.closest("[data-atgo]");
  if (g) { const A = pages.invest.atState(); A.view = Number(g.dataset.atgo); A.tab = g.dataset.attab || "now"; A.d = null; }
  const lg = e.target.closest && e.target.closest("[data-labgo]");
  if (lg) { const A = pages.invest.atState(); A.view = "lab"; A.labId = Number(lg.dataset.labgo); A.labTab = "hold"; }
}, true);
document.addEventListener("click", (e) => {
  if (!e.target.closest("[data-cashwhy]")) return;
  const h = memo.get("ihome");
  if (h && h.account) detailSheet(cashDetail(h.account));
});
document.addEventListener("click", (e) => {
  const r = e.target.closest("[data-dt]");
  if (!r || e.target.closest("button:not([data-dt]), a:not([data-dt]), .switch, input")) return;
  const o = DT[Number(r.dataset.dt)];
  if (o) { e.preventDefault(); detailSheet(o); }
});
// 종목 이름을 누르면 그 종목 차트로(자동매매가 추천·매수·매도한 지점이 5분 차트에 찍힌다)
const stockA = (mkt, sym, name, cls) => `<a class="stk ${cls || ""}" href="#invest/stock?m=${mkt === "us" ? "us" : "kr"}&s=${encodeURIComponent(sym)}&at=1">${esc(name)}</a>`;
const stockHref = (code, market) => `#invest/stock?m=${market === "us" || isUsCode(code) ? "us" : "kr"}&s=${encodeURIComponent(code)}`;
const ORDER_ST = { cancel: "취소", cancelled: "취소됨", accepted: "접수(체결 확인 중)", partial: "일부 체결", filled: "체결" };
function actDetail(x) {
  if (x.order) return orderDetail(x.order);
  if (x.tx) { const t = x.tx; return { title: t.memo || t.category || "가계부 내역", rows: [["날짜", t.date], ["구분", t.transfer ? "내 계좌 이체(합계 제외)" : t.kind === "in" ? "수입" : "지출"],
    ["금액", `${won(t.amount)}원`, t.kind === "in" ? "inc" : ""], ["분류", t.category || "분류 없음"], ["결제수단", t.method], ["메모", t.memo], ["기록 시각", (x.ts || "").replace("T", " ")]],
    links: [["가계부에서 보기·고치기", `#ledger/list?m=${(t.date || "").slice(0, 7)}`, "dark"], ["달력", "#ledger/calendar"]] }; }
  return { title: x.title, rows: [["시각", (x.ts || "").replace("T", " ")]], note: x.sub, links: [x.href ? ["관련 화면 열기", x.href, "dark"] : x.kind === "update" ? ["앱 정보", "#settings/app"] : null] };
}
function orderDetail(o) {
  const us = isUsCode(o.code), P = (v) => v ? (us ? usd(v) : won(v) + "원") : "-";
  return { title: `${o.name || o.code} ${o.side === "buy" ? "매수" : "매도"}`, rows: [
    ["종목", `${o.name || ""} (${o.code})`], ["주문 시각", (o.ts || "").replace("T", " ")], ["구분", o.side === "buy" ? "매수" : "매도", o.side === "buy" ? "up" : "down"],
    ["수량", `${won(o.qty)}주`], ["주문 가격", o.price ? P(o.price) : "시장가"], ["체결가", P(o.fill_price)],
    ["금액", o.fill_price ? P(o.fill_price * o.qty) : ""], ["상태", ORDER_ST[o.status] || o.status],
    ["계좌", { scalp: "기본 계좌", dca: "장기투자 계좌" }[o.engine] || o.engine], ["주문 방식", { paper: "앱 내 모의", "kiwoom-mock": "키움 모의투자", "kiwoom-real": "키움 실전" }[o.mode] || o.mode],
    ["주문번호", String(o.broker_no || "").split("|")[0]], ["메모", o.msg]],
    links: [["종목 화면 보기", stockHref(o.code), "dark"], ["주문 내역", "#invest/trade"]] };
}
async function pickSheet(id) {
  openSheet("AI 추천 추적", `<div class="empty loading">불러오는 중</div>`);
  let p;
  try { p = await GET(`/api/ai/picks/${id}`); } catch (e) { $("#sheetBody").innerHTML = `<div class="notice warn">${esc(e.message)}</div>`; return; }
  const us = p.mkt === "us", P = (v) => v == null ? "-" : us ? usd(v) : won(Math.round(v)) + "원";
  $("#sheetBody").innerHTML = `
    <div class="pk-meta"><span class="tag">${esc(p.ts.slice(5, 16).replace("T", " "))}</span><span class="tag v">${esc(p.label || "당일")} 추천</span><span class="tag">${us ? "미국" : "국내"}</span><span class="tag y">${esc(p.stance || "-")}</span>${p.theme ? `<span class="tag">테마: ${esc(p.theme)}</span>` : ""}<span class="tag">${esc(p.model.replace("claude-", ""))}</span><span class="tag">$${Number(p.usd || 0).toFixed(3)}</span></div>
    ${p.view ? `<div class="small" style="font-weight:700">${esc(p.view)}</div>` : ""}
    ${p.picks.length ? p.picks.map((x, i) => { const o = x.outcome || {}, res = o.result || "진행 중", since = x.now && x.price ? (x.now / x.price - 1) * 100 : null;
      const sw = p.horizon && p.horizon !== "day", path = o.path && Object.keys(o.path).length ? o.path : null;
      return `<section class="pk-card">
        <div class="pk-h"><span class="as-badge r-${esc(res)}">${esc(sw && res === "시간 정리" ? "기간 만료" : res)}</span><b>${i + 1}. ${esc(x.name)}</b><span class="${tone(o.net_pct)}" style="margin-left:auto;font-weight:900">${o.net_pct == null ? "" : pct(o.net_pct, 2)}</span></div>
        <dl class="dt-rows">
          <div><dt>추천 때 가격</dt><dd>${P(x.price)}</dd></div><div><dt>지금 가격</dt><dd class="${tone(since)}">${P(x.now)}${since == null ? "" : ` (${pct(since, 2)})`}</dd></div>
          <div><dt>익절 / 손절</dt><dd><span class="up">+${x.target_pct}%</span> / <span class="down">−${x.stop_pct}%</span></dd></div>
          <div><dt>AI 예측 승률</dt><dd>${x.win_prob}%</dd></div>
          ${sw ? `<div><dt>재료</dt><dd><span class="tag c-${esc(x.catalyst_type)}">${esc(x.catalyst_type)}</span> ${esc(x.catalyst || "-")}${x.catalyst_date ? ` · ${esc(x.catalyst_date)}` : ""}</dd></div>
          <div><dt>보유 계획</dt><dd>약 ${x.hold_days}거래일 · ${esc(x.end_date || "")}까지${x.in_cand ? "" : " · 뉴스에서 찾은 종목"}</dd></div>` : ""}
          ${o.entry ? `<div><dt>바로 매수 → 청산</dt><dd>${P(o.entry)} → ${P(o.exit)}${sw && o.days_held ? ` · ${o.days_held}거래일째` : ""}</dd></div>` : ""}
          ${sw && o.bench != null ? `<div><dt>같은 기간 지수</dt><dd>${pct(o.bench, 2)} <span class="${tone(o.net_pct - o.bench)}">(종목이 ${pct(o.net_pct - o.bench, 2)}p)</span></dd></div>` : ""}
          ${path ? `<div><dt>그냥 들고 있었다면</dt><dd>${Object.entries(path).map(([k, v]) => `${k.slice(1)}일 <span class="${tone(v)}">${pct(v, 1)}</span>`).join(" · ")}</dd></div>` : ""}
          ${o.mfe != null ? `<div><dt>추천 뒤 최대 상승 / 하락</dt><dd><span class="up">${pct(o.mfe, 2)}</span> / <span class="down">${pct(o.mae, 2)}</span></dd></div>` : ""}
${o.alt_result ? `<div><dt>AI 지정가(${P(x.entry_price)})를 기다렸다면</dt><dd class="${tone(o.alt_net)}">${esc(o.alt_result)}${o.alt_net == null ? "" : " " + pct(o.alt_net, 2)}</dd></div>` : ""}
          <div><dt>평가 마감</dt><dd>${esc((o.end_ts || "").slice(5, 16).replace("T", " ") || "-")}${o.src ? ` · ${esc(o.src)}` : ""}</dd></div>
          <div><dt>직접 매수</dt><dd>${x.guard ? `했어요 · ${esc(x.guard.status === "watching" ? "감시 중" : x.guard.exit_reason || x.guard.status)}` : "안 했어요"}</dd></div>
        </dl>
        ${(x.reasons || []).length ? `<ul class="ap-why">${x.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}${(x.risks || []).map((r) => `<li class="risk">${esc(r)}</li>`).join("")}</ul>` : ""}
        <a class="btn sm" href="${stockHref(x.sym, p.mkt)}&pk=${p.id}${sw ? "&ph=" + p.horizon : ""}" data-close>차트에서 추천 시점·익절·손절 선 보기</a></section>`; }).join("") : `<div class="empty small">이 추천에는 종목이 없어요(관망)</div>`}
    ${p.text ? `<details class="pk-text"><summary class="tiny">AI가 쓴 판단 전문</summary><div class="ai-ans">${mdLite(p.text)}</div></details>` : ""}
    <div class="dt-links"><a class="btn dark" href="#invest/aistat" data-close>전체 성적 보기</a></div>`;
  $("#sheetBody").onclick = (e) => { if (e.target.closest("[data-close]")) closeSheet(); };
}

// ---------- AI 비용 표시(앱 전체 공통): 예상/실제 비용 + Claude 잔액 ----------
const aiMeter = {
  c: {},
  async get(kind, model, web) {
    const k = `${kind}|${model || ""}|${web == null ? "" : web ? 1 : 0}`, hit = this.c[k];
    if (hit && Date.now() - hit.t < 30000) return hit.v;
    const v = await GET(`/api/ai/meter?kind=${kind}${model ? `&model=${encodeURIComponent(model)}` : ""}${web == null ? "" : `&web=${web ? 1 : 0}`}`);
    this.c[k] = { t: Date.now(), v }; return v;
  },
  clear() { this.c = {}; },
};
const krw = (usd, fx) => `${won(Math.max(1, Math.round((usd || 0) * (fx || 1400))))}원`;
function meterEst(m) {
  if (m.avg_usd != null) return { main: `약 ${krw(m.avg_usd, m.fx)}`, sub: `지난 ${m.n}회 평균 · $${m.avg_usd.toFixed(3)}` };
  return { main: `${krw(m.usd_lo, m.fx)}~${krw(m.usd_hi, m.fx).replace("원", "")}원`, sub: `1회 예상 · $${m.usd_lo.toFixed(3)}~${m.usd_hi.toFixed(3)}` };
}
function meterHTML(m, used) {
  if (!m) return "";
  const cr = (used && used.credit) || m.credit || {}, fx = (used && used.fx) || m.fx;
  const L = used ? { main: krw(used.usd, fx), sub: `$${Number(used.usd).toFixed(4)} · ${esc((used.model || m.model || "").replace("claude-", ""))}${used.searches ? ` · 웹 검색 ${used.searches}회` : ""}` } : meterEst(m);
  const low = cr.set && (cr.left_pct < 15 || cr.exhausted);
  return `<div class="cm ${used ? "used" : ""} ${low ? "low" : ""}">
    <div class="cm-l"><span>${used ? "이번 사용" : "예상 비용"}${!used ? ` · ${esc(m.model_name || "")}${m.web ? " · 뉴스 검색" : ""}` : ""}</span><b>${L.main}</b><small>${L.sub}</small></div>
    <a class="cm-r" href="#settings/ai">${cr.set ? `<span>Claude 잔액</span><b>$${Number(cr.left).toFixed(2)}</b><small>약 ${won(Math.round(cr.left * fx))}원 · ${cr.left_pct}% 남음</small>
      <i class="cm-g"><i style="width:${Math.max(2, Math.min(100, cr.left_pct))}%"></i></i>`
      : `<span>Claude 잔액</span><b class="cm-na">미설정</b><small>설정 → AI에서 맞추기 ›</small>`}</a></div>`;
}
// AI 버튼마다 예상 비용·잔액을 작게 붙인다(화면이 다시 그려져도 자동으로)
function fillAiBtns(root = document) {
  $$(".ai-btn[data-ai]:not([data-cm])", root).forEach(async (b) => {
    b.dataset.cm = "1";
    try {
      const m = await aiMeter.get(b.dataset.ai);
      if (!m.configured) return;
      const e = meterEst(m), cr = m.credit || {};
      b.insertAdjacentHTML("beforeend", `<small class="ai-est">${e.main}${cr.set ? ` · 잔액 $${Number(cr.left).toFixed(2)}` : ""}</small>`);
    } catch (err) { /* 표시만 생략 */ }
  });
}
new MutationObserver(() => { clearTimeout(fillAiBtns._t); fillAiBtns._t = setTimeout(() => fillAiBtns(), 120); }).observe(document.getElementById("view") || document.body, { childList: true, subtree: true });
const AI_BTN = (kind, label, attrs = "") => `<button type="button" class="btn ai-btn wide" data-ai="${kind}" ${attrs}><span class="ai-spark" aria-hidden="true">✦</span>${esc(label)}</button>`;

function resultBar(a, mid = "시간 정리") {
  return a.done ? `<div class="rbar" role="img" aria-label="익절 ${a.tp}, ${mid} ${a.time}, 손절 ${a.sl}"><i class="tp" style="flex:${a.tp}"></i><i class="tm" style="flex:${a.time}"></i><i class="sl" style="flex:${a.sl}"></i></div>
    <div class="rleg"><span><i class="tp"></i>익절 ${a.tp}</span><span><i class="tm"></i>${mid} ${a.time}</span><span><i class="sl"></i>손절 ${a.sl}</span></div>` : "";
}
function winRing(v) {
  const r = 19, c = 2 * Math.PI * r, f = Math.max(0, Math.min(100, v)) / 100;
  return `<span class="ap-ring" role="img" aria-label="AI 추정 승률 ${v}%"><svg viewBox="0 0 48 48"><defs><linearGradient id="rg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#8B5CF6"/><stop offset="1" stop-color="#E14F8C"/></linearGradient></defs>
    <circle cx="24" cy="24" r="${r}" class="bg"/><circle cx="24" cy="24" r="${r}" class="fg" style="--c:${c.toFixed(1)};--o:${(c * (1 - f)).toFixed(1)}" stroke-dasharray="${c.toFixed(1)}" stroke-dashoffset="${(c * (1 - f)).toFixed(1)}"/></svg>
    <b>${v}<small>%</small></b><em>승률</em></span>`;
}

// ---------- 주문 시트 ----------
const usd = (n) => (n == null || isNaN(n) ? "-" : (n < 0 ? "−$" : "$") + Math.abs(Number(n)).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const isUsCode = (c) => /[A-Za-z]/.test(String(c || ""));
function krTick(p) {  // KRX 호가단위(2023~)
  return p < 2000 ? 1 : p < 5000 ? 5 : p < 20000 ? 10 : p < 50000 ? 50 : p < 200000 ? 100 : p < 500000 ? 500 : 1000;
}
function bigNum(n) {
  n = Number(n) || 0; const a = Math.abs(n);
  return a >= 1e12 ? (n / 1e12).toFixed(1).replace(/\.0$/, "") + "조" : a >= 1e8 ? (n / 1e8).toFixed(a >= 1e10 ? 0 : 1).replace(/\.0$/, "") + "억" : a >= 1e4 ? Math.round(n / 1e4).toLocaleString("ko-KR") + "만" : won(n);
}
function orderSheet(o) {
  if (!o.code && !o.market) o = { ...o, market: curMkt("kr") };
  const us0 = o.market === "us" || isUsCode(o.code);
  const S = { market: us0 ? "us" : "kr", engine: o.engine || "scalp", side: o.side || "buy", type: us0 || o.price ? "limit" : "market",
    code: o.code || "", qty: o.qty || 1, q: null, real: null, ctx: null, err: "", exch: o.exch || "", name: o.name || "",
    price: o.price ? (us0 ? Number(o.price).toFixed(2) : won(o.price)) : "", guardOn: true, pk: {}, pick: false };
  const acc = () => (S.ctx ? S.ctx.accounts.find((a) => a.engine === S.engine) : null);
  const avail = () => {
    const a = acc(); if (!a) return null;
    if (S.side === "buy") return S.market === "us" ? (a.us_cash ? a.us_cash.usd_orderable : null) : (a.cash ?? null);
    const code = String(S.code || "").toUpperCase();
    const h = (S.market === "us" ? a.us_holdings || [] : a.holdings || []).find((x) => String(x.code || x.sym || "").toUpperCase().replace(/^A/, "") === code);
    return h ? (h.sellable ?? h.qty) : (o.holding && String(o.code).toUpperCase() === code ? (o.holding.sellable || o.holding.qty) : 0);
  };
  const pickRows = (list, empty) => list.length ? list.map((x) => `<button type="button" class="item op-row" data-sym="${esc(x.sym)}" data-name="${esc(x.name || "")}" data-exch="${esc(x.exch || "")}">
      ${x.badge ? `<span class="ico ${x.cls || "h-slate"}">${esc(x.badge)}</span>` : ""}
      <span class="main"><b>${esc(x.name || x.sym)}</b><span class="sub">${esc(x.sub || x.sym)}</span></span>
      <span class="end">${x.price ? `<b>${S.market === "us" ? usd(x.price) : won(x.price)}</b>` : ""}${x.pct == null ? "" : `<span class="mk-c ${tone(x.pct)}">${pct(x.pct, 2)}</span>`}</span></button>`).join("")
    : `<div class="empty small">${empty}</div>`;
  const pickLists = () => {
    const el = $("#opRes"); if (!el) return;
    const m = S.market, US = m === "us", buy = S.side === "buy", a = acc(), K = S.pk[m] || {};
    const hold = ((a && (US ? a.us_holdings : a.holdings)) || []).filter((h) => (h.qty || 0) > 0).map((h) => ({ sym: String(h.code || h.sym).replace(/^A/, ""), name: h.name,
      sub: `${won(h.qty)}주 보유 · 평단 ${US ? usd(h.avg_price) : won(h.avg_price)}`, price: h.price, pct: h.pnl_pct, exch: h.exch }));
    const sec = (title, body, note = "") => `<div class="op-sec"><div class="op-h">${title}${note ? `<span>${note}</span>` : ""}</div><div class="list">${body}</div></div>`;
    if (!buy) { el.innerHTML = sec("팔 수 있는 종목", pickRows(hold, a ? "이 계좌에 보유 종목이 없어요" : "계좌를 확인하는 중이에요"), "수익률은 평가 기준"); return; }
    const watch = (K.watch || []).filter((x) => !x.error).map((x) => ({ sym: x.sym, name: x.name, price: x.price, pct: x.change_pct, exch: x.exch }));
    const rank = (K.rank || []).slice(0, 8).map((x) => ({ sym: x.sym, name: x.name, price: x.price, pct: x.change_pct, badge: String(x.rank), cls: x.rank <= 3 ? "h-pink" : "h-slate" }));
    const seen = new Set(), recent = ((S.ctx && S.ctx.orders) || []).filter((r) => isUsCode(r.code) === US && !seen.has(r.code) && seen.add(r.code)).slice(0, 5)
      .map((r) => ({ sym: r.code, name: r.name, sub: `${r.code} · ${(r.ts || "").slice(5, 10)} ${r.side === "buy" ? "매수" : "매도"}` }));
    const found = recentStock.list(m).slice(0, 6).map((x) => ({ sym: x.sym, name: x.name, sub: `${x.sym}${x.exchange ? " · " + x.exchange : ""}` }));
    el.innerHTML = (found.length ? sec("최근 찾은 종목", pickRows(found, "")) : "")
      + (hold.length ? sec("내 보유 종목", pickRows(hold, ""), "더 사기") : "")
      + sec("관심종목", K.watch ? pickRows(watch, "관심종목이 없어요. 검색해서 골라 보세요") : `<div class="empty small loading">불러오는 중</div>`)
      + (K.rank && K.rank.length ? sec(US ? "지금 많이 찾는 종목" : "지금 거래대금 많은 종목", pickRows(rank, ""), "실시간 순위") : "")
      + (recent.length ? sec("최근 주문한 종목", pickRows(recent, "")) : "");
  };
  const loadPick = async (m) => {
    if (S.pk[m]) return;
    S.pk[m] = {};
    const [w, r] = await Promise.all([GET(`/api/market/overview?mkt=${m}`).catch(() => ({ items: [] })), GET(`/api/invest/rank?mkt=${m}&kind=${m === "us" ? "views" : "value"}`).catch(() => ({ items: [] }))]);
    S.pk[m] = { watch: w.items || [], rank: r.items || [] };
    if (sheet.open && $("#opRes") && S.market === m && !$("#opQ").value.trim()) pickLists();
  };
  const choose = (sym, name, exch) => {
    const US = S.market === "us";
    S.code = US ? String(sym).toUpperCase() : String(sym).replace(/\D/g, "").slice(-6); S.name = name || ""; S.exch = exch || ""; S.pick = false; S.q = null; S.real = null; S.price = ""; S.err = "";
    S.type = US ? "limit" : "market";
    if (S.side === "sell") { const v = avail(); S.qty = v || 1; } else S.qty = 1;
    draw(); loadQuote();
  };
  const drawPick = () => {
    const US = S.market === "us", buy = S.side === "buy";
    openSheet(buy ? "살 종목 고르기" : "팔 종목 고르기", `
      <div class="o-steps"><b class="on">1 종목</b><i></i><b>2 수량·가격</b><i></i><b>3 주문</b></div>
      <div class="op-top">${seg("시장", [["kr", "국내"], ["us", "미국"]], S.market, "on-card")}${seg("매수 또는 매도", [["buy", "매수"], ["sell", "매도"]], S.side, "on-card " + (buy ? "buy" : "sell"))}</div>
      <div class="ss-row">${sessionChip(S.market)}<span class="tiny">${esc(marketSession(S.market).sub)}</span></div>
      <label class="op-search"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>
        <input id="opQ" type="search" autocomplete="off" enterkeyhint="search" placeholder="${US ? "회사 이름이나 기호로 찾기 (apple, NVDA)" : "종목 이름이나 코드로 찾기 (삼성전자, 005930)"}" aria-label="종목 검색"></label>
      <div id="opRes"><div class="empty small loading">불러오는 중</div></div>
      ${S.code ? `<button type="button" class="btn sm soft" id="opBack">${esc(S.name || S.code)} 주문으로 돌아가기</button>` : ""}`, (box) => {
      bindSeg(box, "시장", (v) => { if (v !== S.market) { setMkt(v); S.market = v; } drawPick(); });
      bindSeg(box, "매수 또는 매도", (v) => { S.side = v; drawPick(); });
      const q = $("#opQ", box); let t = null, seq = 0;
      const go = async () => {
        const v = q.value.trim(), my = ++seq;
        if (!v) return pickLists();
        $("#opRes").innerHTML = `<div class="empty small loading">찾는 중</div>`;
        try {
          const res = await GET(`/api/market/search?mkt=${S.market}&q=${encodeURIComponent(v)}`);
          if (my !== seq || !$("#opRes")) return;
          $("#opRes").innerHTML = `<div class="op-sec"><div class="op-h">검색 결과 ${res.length}</div><div class="list">${pickRows(res.map((x) => ({ sym: x.sym, name: x.name, sub: `${x.sym}${x.exchange ? " · " + x.exchange : ""}` })), "찾는 종목이 없어요. 다른 이름으로 찾아 보세요")}</div></div>`;
        } catch (e) { if (my === seq) $("#opRes").innerHTML = `<div class="notice warn">${esc(e.message)}</div>`; }
      };
      q.oninput = () => { clearTimeout(t); t = setTimeout(go, 300); };
      box.onclick = (e) => {
        const r = e.target.closest(".op-row");
        if (r) { if (S.side === "buy") recentStock.add(S.market, r.dataset.sym, r.dataset.name); return choose(r.dataset.sym, r.dataset.name, r.dataset.exch); }
        if (e.target.closest("#opBack")) { S.pick = false; draw(); }
      };
      pickLists(); loadPick(S.market);
    });
  };
  const draw = () => {
    if (!S.code || S.pick) return drawPick();
    const US = S.market === "us", buy = S.side === "buy", a = acc(), demo = !US && S.ctx && S.ctx.data_source === "demo";
    const mode = a ? a.mode_label : null, realOrder = mode === "키움 실전", isKiwoom = mode && mode.startsWith("키움");
    const gap = demo && S.q && S.real && Math.abs(S.real.price - S.q.price) / S.real.price > 0.005;
    const usBlocked = US && a && !a.error && !isKiwoom;
    const Q = US ? S.q : (S.real && !demo ? { ...S.q, ...S.real, price: (S.q || S.real).price } : S.q), nm = (Q && Q.name) || S.name || S.code;
    const P = (v) => v == null ? "-" : US ? usd(v) : won(v) + "원";
    openSheet(`${nm} ${buy ? "매수" : "매도"}`, `
      <div class="o-steps"><b class="done">1 종목</b><i></i><b class="on">2 수량·가격</b><i></i><b>3 주문</b></div>
      <div class="o-stock">
        <div class="o-sn"><b>${esc(nm)}</b><span>${esc(S.code)}${Q && Q.exchange ? " · " + esc(Q.exchange) : ""}${US ? (Q && Q.live ? " · 실시간" : Q ? " · 지연 시세" : "") : gap ? " · 가상 시세" : ""}</span></div>
        <div class="o-sp">${Q ? `<b class="${tone(Q.change_pct)}">${P(Q.price)}</b><em class="${tone(Q.change_pct)}">${Q.change_pct == null ? "" : pct(Q.change_pct, 2)}${US && Q.fx ? ` · 약 ${won(Q.price * Q.fx)}원` : ""}</em>` : `<span class="empty small loading">시세 받는 중</span>`}</div>
        <button type="button" class="btn sm soft" id="oChange">종목 바꾸기</button>
      </div>
      ${Q && (Q.high || Q.low) ? `<div class="o-mini"><span>고가 <b class="up">${P(Q.high)}</b></span><span>저가 <b class="down">${P(Q.low)}</b></span>${Q.prev ? `<span>전일 <b>${P(Q.prev)}</b></span>` : ""}<a href="${stockHref(S.code, S.market)}" data-close>차트·호가 보기 ›</a></div>` : ""}
      ${demo ? `<div class="notice warn">지금 시세 출처가 <b>가상 시세</b>예요. 주문 가격이 실제 시세와 달라요${S.real ? `(실제 ${esc(S.real.name)} ${won(S.real.price)}원)` : ""}.
        <button type="button" class="btn sm dark" id="oLive" style="margin-top:5.5px">키움 시세로 바꾸기</button></div>` : ""}
      ${seg("매수 또는 매도", [["buy", "매수"], ["sell", "매도"]], S.side, "on-card " + (buy ? "buy" : "sell"))}
      <div class="o-sec"><div class="o-lab">어떻게 ${buy ? "살까요" : "팔까요"}?</div>
        ${seg("주문 방식", [["market", "시장가 · 지금 바로"], ["limit", "지정가 · 가격 정하기"]], S.type, "on-card")}
        <div class="tiny">${S.type === "market" ? (US ? "미국 시장가는 정규장(한국 시간 23:30~06:00)에만 돼요. 그 밖의 시간에는 지정가로 주문하세요." : "지금 팔려는(사려는) 사람의 가격에 바로 체결돼요. 가격이 조금 달라질 수 있어요.")
          : `정한 가격에 닿아야 체결돼요. ${buy ? "현재가보다 낮게 걸면 내려올 때까지 기다려요." : "현재가보다 높게 걸면 오를 때까지 기다려요."}`}</div>
        <label class="field" ${S.type === "market" ? "hidden" : ""}>가격${US ? "(달러)" : ""}
          <span class="stepper"><button type="button" data-tk="-1" aria-label="한 호가 내리기">−</button><input id="oPrice" class="r" inputmode="decimal" value="${esc(S.price || (S.q ? (US ? S.q.price.toFixed(2) : won(S.q.price)) : ""))}"><button type="button" data-tk="1" aria-label="한 호가 올리기">+</button></span></label>
        ${S.type === "limit" ? `<div class="o-pct">${(buy ? [[0, "현재가"], [-0.5, "−0.5%"], [-1, "−1%"], [-2, "−2%"]] : [[0, "현재가"], [0.5, "+0.5%"], [1, "+1%"], [2, "+2%"]]).map(([v, l]) => `<button type="button" class="chip" data-px="${v}">${l}</button>`).join("")}</div>` : ""}
      </div>
      <div class="o-sec"><div class="o-lab">몇 주? <span class="o-avail">${(() => { const v = avail(); return v == null ? "" : buy ? `주문 가능 <b>${US ? usd(v) : won(v) + "원"}</b>` : `매도 가능 <b>${won(v)}주</b>`; })()}</span></div>
        <label class="field" style="gap:0"><span class="stepper"><button type="button" data-qn="-1" aria-label="1주 빼기">−</button><input id="oQty" class="r" inputmode="numeric" value="${esc(S.qty)}" aria-label="수량"><button type="button" data-qn="1" aria-label="1주 더하기">+</button></span></label>
        <div class="o-pct">${[[10, "10%"], [25, "25%"], [50, "50%"], [100, "최대"]].map(([v, l]) => `<button type="button" class="chip" data-pq="${v}">${buy ? "가능 금액의 " : "보유의 "}${l}</button>`).join("")}</div>
        ${buy ? `<div class="o-pct">${(US ? [[100, "$100어치"], [300, "$300어치"], [500, "$500어치"], [1000, "$1,000어치"]] : [[100000, "10만 원어치"], [300000, "30만 원어치"], [500000, "50만 원어치"], [1000000, "100만 원어치"]]).map(([v, l]) => `<button type="button" class="chip" data-amt="${v}">${l}</button>`).join("")}</div>` : ""}
      </div>
      <div class="o-sum" id="oSum"><div><span>예상 금액</span><b id="oAmt">-</b></div><div class="tiny" id="oSumT"></div></div>
      ${o.guard && buy ? `<label class="o-guard"><input type="checkbox" id="oGuard" ${S.guardOn ? "checked" : ""}>
        <span><b>체결되면 자동 익절·손절</b><small>평균 단가 기준 +${o.guard.target_pct}% 익절 · −${o.guard.stop_pct}% 손절${o.guard.trail_pct ? ` · 추적 익절 ${o.guard.trail_pct}%` : ""} · ${o.guard.horizon ? `${esc((o.guard.end_date || "").slice(5).replace("-", "/"))}(약 ${o.guard.hold_days}거래일)까지 들고 가다 그날 장 마감 직전에 정리` : o.guard.exit_by ? `${esc(o.guard.exit_by)}까지 안 닿으면 정리` : "정규장 마감 8분 전 정리"}. 정규장에만 시장가로 팔아요.${o.guard.horizon ? " 노트북의 moa가 켜져 있어야 하고, 밤사이 갭은 막지 못해요." : ""}</small></span></label>` : ""}
      <div class="o-mode ${realOrder ? "real" : ""}">${a ? (a.error ? `<b>주문할 수 없어요</b> ${esc(a.error)}`
        : usBlocked ? `<b>미국 주식은 키움 계좌로만</b> 이 계좌는 '앱 내 모의'라 미국 주문을 할 수 없어요. 설정 → 계좌 연결에서 주문 방식을 '키움으로 주문'으로 바꿔 주세요`
        : realOrder ? `<b>키움 실전 계좌</b> 실제 돈으로 주문이 나가요${US ? ". 달러(또는 원화주문 설정)로 결제돼요" : ""}`
        : mode === "키움 모의투자" ? `<b>키움 모의투자</b> 키움 모의 서버로 주문해요. 실제 돈이 아니에요`
        : `<b>앱 내 모의</b> 앱 안에서만 체결을 흉내 내요. 실제 주문이 아니에요`) : "계좌 확인 중"}</div>
      <div id="oErr">${S.err ? `<div class="notice warn">${esc(S.err)}</div>` : ""}</div>
      <button class="btn ${buy ? "red" : "blue"} wide o-send" id="oSend" ${a && (a.error || usBlocked) ? "disabled" : ""}>${realOrder ? "실제 " : ""}${buy ? "매수 주문" : "매도 주문"}</button>
      <details class="o-acct"><summary class="tiny">계좌 바꾸기 · 지금 ${S.engine === "dca" ? "장기투자 계좌" : "기본 계좌"}</summary>${seg("계좌", [["scalp", "기본 계좌"], ["dca", "장기투자 계좌"]], S.engine, "on-card")}</details>`, (box) => {
      const keep = () => { S.qty = $("#oQty").value; S.price = $("#oPrice").value; if ($("#oGuard")) S.guardOn = $("#oGuard").checked; };
      bindSeg(box, "시장", (v) => { keep(); if (v !== S.market) { S.market = v; S.code = ""; S.q = null; S.price = ""; S.type = v === "us" ? "limit" : "market"; S.err = ""; } draw(); });
      bindSeg(box, "계좌", (v) => { keep(); S.engine = v; S.err = ""; draw(); if (US && S.code) loadQuote(); });
      bindSeg(box, "매수 또는 매도", (v) => { keep(); S.side = v; draw(); });
      bindSeg(box, "주문 방식", (v) => { keep(); S.type = v; draw(); });
      const calc = () => {
        const p = S.type === "limit" ? Number(String($("#oPrice").value).replace(/,/g, "")) : S.q?.price;
        const n = num($("#oQty").value);
        $("#oAmt").textContent = !p ? "-" : US ? `${usd(p * n)}${S.q?.fx ? ` (약 ${won(p * n * S.q.fx)}원)` : ""}` : won(p * n) + "원";
        const v = avail(), t = $("#oSumT");
        const what = `${n}주를 ${S.type === "market" ? "시장가로" : !$("#oPrice").value ? "지정가로" : `${US ? "$" + $("#oPrice").value : $("#oPrice").value + "원"}에`} ${buy ? "사요" : "팔아요"}`;
        let extra = "", bad = false;
        if (p && n && v != null) {
          if (buy) { const left = v - p * n; bad = left < 0; extra = bad ? ` · 주문 가능 금액보다 ${US ? usd(-left) : won(-left) + "원"} 많아요` : ` · 주문 뒤 남는 돈 ${US ? usd(left) : won(left) + "원"}`; }
          else { bad = n > v; extra = bad ? ` · 팔 수 있는 수량(${won(v)}주)보다 많아요` : ` · 팔고 남는 수량 ${won(v - n)}주`; }
        }
        t.textContent = n ? what + extra : "수량을 넣어 주세요"; $("#oSum").classList.toggle("bad", bad);
      };
      $("#oQty").oninput = calc; $("#oPrice").oninput = calc; calc();
      const curPx = () => (S.type === "limit" ? Number(String($("#oPrice").value).replace(/,/g, "")) : 0) || S.q?.price || 0;
      box.onclick = (e) => {
        const tk = e.target.closest("[data-tk]"), qn = e.target.closest("[data-qn]"), pq = e.target.closest("[data-pq]"), px = e.target.closest("[data-px]"), am = e.target.closest("[data-amt]");
        if (e.target.closest("[data-close]")) return closeSheet();
        if (e.target.closest("#oChange")) { keep(); S.pick = true; S.err = ""; return draw(); }
        if (px) { const b0 = S.q?.price; if (!b0) return toast("현재가를 아직 못 받았어요"); const v = b0 * (1 + Number(px.dataset.px) / 100); $("#oPrice").value = US ? v.toFixed(2) : won(Math.round(v / krTick(v)) * krTick(v)); calc(); }
        if (am) { const p = curPx(); if (!p) return toast("현재가를 아직 못 받았어요"); $("#oQty").value = Math.max(1, Math.floor(Number(am.dataset.amt) / p)); calc(); }
        if (tk) { const p = curPx(), d = Number(tk.dataset.tk), t = US ? 0.01 : krTick(d > 0 ? p : p - 1); const v = Math.max(t, p + d * t); $("#oPrice").value = US ? v.toFixed(2) : won(v); calc(); }
        if (qn) { $("#oQty").value = Math.max(1, num($("#oQty").value) + Number(qn.dataset.qn)); calc(); }
        if (pq) {
          const v = avail(), r = Number(pq.dataset.pq) / 100, p = curPx();
          if (v == null) return toast("주문 가능 금액을 아직 못 받았어요");
          const n = buy ? Math.floor(v * r / (p || Infinity)) : Math.floor(v * r);
          $("#oQty").value = Math.max(buy ? 0 : 1, n); calc();
        }
      };
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
          exch: S.q?.exch || S.exch || undefined, exchange: S.q?.exchange || undefined, name: S.q?.name || S.name || undefined };
        btn.disabled = true; btn.textContent = "주문 넣는 중";
        try {
          const r = await api("POST", "/api/order", body);
          toast(r.status === "filled" ? `${r.name} ${body.qty}주 ${buy ? "매수" : "매도"} 체결 ${won(r.fill_price)}원` : `${r.name} ${body.qty}주 ${buy ? "매수" : "매도"} 주문을 넣었어요`);
          if (o.guard && buy && S.guardOn) {
            try {
              await api("POST", "/api/ai/guards", { ...o.guard, mkt: S.market, engine: S.engine, sym: body.code, name: r.name || body.name, exch: r.exch || body.exch,
                qty: body.qty, entry: price || r.fill_price || S.q?.price });
              toast(`${r.name} 자동 익절·손절을 걸었어요`);
            } catch (e) { toast(`자동 익절·손절 등록 실패: ${e.message}`); }
          }
          o.after && o.after(r, body);
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
  const loadCtx = async () => {
    S.ctx = await GET("/api/stocks").catch(() => null);
    if (!sheet.open) return;
    if ($("#opRes")) { if (!$("#opQ").value.trim()) pickLists(); } else draw();
  };
  draw();
  loadCtx();
  if (S.code) loadQuote();
}

// ---------- 적립 플랜 시트 ----------
// 종목 고르기: 코드를 외워 넣는 대신 이름으로 찾아 고른다. 빈 칸에서는 관심종목·적립 중인 종목을 바로 보여 준다.
// host: 빈 요소. opts: { mkt, multi, value: [{sym, name}], max, onChange(items) }
function stockPick(host, opts = {}) {
  const S = { mkt: opts.mkt === "us" ? "us" : "kr", multi: !!opts.multi, max: opts.max || 30, seq: 0, t: 0, sug: null,
    items: (opts.value || []).filter((x) => x && x.sym).map((x) => ({ sym: String(x.sym), name: x.name || String(x.sym) })) };
  const US = () => S.mkt === "us";
  host.classList.add("sp");
  host.innerHTML = `<div class="sp-chips"></div>
    <label class="op-search sp-q"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>
      <input type="search" autocomplete="off" autocapitalize="off" spellcheck="false" enterkeyhint="search" aria-label="종목 이름으로 찾기"></label>
    <div class="sp-res" aria-live="polite"></div>`;
  const chips = $(".sp-chips", host), bar = $(".sp-q", host), q = $("input", bar), res = $(".sp-res", host);
  const fire = () => opts.onChange && opts.onChange(S.items.slice());
  const chipDraw = () => {
    chips.innerHTML = S.multi ? S.items.map((x, i) => `<span class="sp-chip"><b>${esc(x.name)}</b>${x.name !== x.sym ? `<small>${esc(x.sym)}</small>` : ""}<button type="button" data-sprm="${i}" aria-label="${esc(x.name)} 빼기">×</button></span>`).join("")
      : S.items.length ? `<div class="sp-one"><span><b>${esc(S.items[0].name)}</b>${S.items[0].name !== S.items[0].sym ? `<small>${esc(S.items[0].sym)}</small>` : ""}</span><button type="button" class="btn sm soft" data-spchg="1">바꾸기</button></div>` : "";
    bar.hidden = !S.multi && S.items.length > 0;
    q.placeholder = S.multi && S.items.length ? "이름으로 찾아 더 넣기" : US() ? "회사 이름이나 티커로 찾기 (apple, MSFT)" : "종목 이름으로 찾기 (삼성전자, TIGER)";
  };
  const rows = (list, head) => (list.length ? `${head ? `<div class="op-h">${esc(head)}</div>` : ""}<div class="list">${list.map((x) => { const has = S.items.some((y) => y.sym === x.sym);
    return `<button type="button" class="item op-row" data-spk="${esc(x.sym)}" data-name="${esc(x.name)}" ${has ? "disabled" : ""}><span class="main"><b>${esc(x.name)}</b><span class="sub">${[x.name !== x.sym ? x.sym : "", x.exchange || x.tag || ""].filter(Boolean).map(esc).join(" · ")}</span></span><span class="end tiny">${has ? "담김" : ""}</span></button>`; }).join("")}</div>` : "");
  // 코드·티커를 아는 사람은 그대로 넣을 수도 있다(검색에 안 나올 때)
  const direct = (v, found) => (US() ? /^[A-Za-z][A-Za-z.-]{0,6}$/.test(v) : /^\d{6}$/.test(v)) && !found.some((x) => x.sym === v.toUpperCase()) ? [{ sym: v.toUpperCase(), name: v.toUpperCase(), exchange: US() ? "이 티커로 넣기" : "이 코드로 넣기" }] : [];
  const suggest = async () => {
    const my = S.seq;
    if (S.sug == null) { S.sug = []; try { S.sug = (await GET(`/api/market/picks?mkt=${S.mkt}`)).picks || []; } catch (e) { /* 바로 고르기만 생략 */ } }
    if (my === S.seq && !q.value.trim() && host.isConnected && !bar.hidden) {
      const R = recentStock.list(S.mkt).slice(0, 6), rs = new Set(R.map((x) => x.sym));
      res.innerHTML = rows(R, "최근 찾은 종목") + rows(S.sug.filter((x) => !rs.has(x.sym) && !S.items.some((y) => y.sym === x.sym)).slice(0, 8), "바로 고르기");
    }
  };
  const go = async () => {
    const v = q.value.trim(), my = ++S.seq;
    if (!v) return suggest();
    res.innerHTML = `<div class="empty small loading">찾는 중</div>`;
    try {
      const r = await GET(`/api/market/search?mkt=${S.mkt}&q=${encodeURIComponent(v)}`);
      if (my !== S.seq) return;
      res.innerHTML = rows(r.length ? r : direct(v, r)) || `<div class="empty small">찾는 종목이 없어요. ${US() ? "영문 이름이나 티커로 넣어 보세요" : "이름 일부만 넣어도 찾아요"}</div>`;
    } catch (e) {
      if (e instanceof Locked) throw e;
      if (my === S.seq) res.innerHTML = `<div class="notice warn">${esc(e.message)}</div>` + rows(direct(v, []));
    }
  };
  q.addEventListener("input", () => { clearTimeout(S.t); S.t = setTimeout(go, 280); });
  q.addEventListener("focus", () => { if (!q.value.trim()) suggest(); });
  q.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); clearTimeout(S.t); go(); } });
  host.addEventListener("click", (e) => {
    let b;
    if ((b = e.target.closest("[data-spk]"))) {
      if (b.disabled) return;
      if (S.multi && S.items.length >= S.max) return toast(`${S.max}개까지 넣을 수 있어요`, true);
      const it = { sym: b.dataset.spk, name: b.dataset.name || b.dataset.spk };
      recentStock.add(S.mkt, it.sym, it.name);
      S.items = S.multi ? [...S.items, it] : [it];
      q.value = ""; res.innerHTML = ""; S.seq++; chipDraw(); fire();
      if (S.multi) q.focus();
    } else if ((b = e.target.closest("[data-sprm]"))) { S.items.splice(+b.dataset.sprm, 1); chipDraw(); fire(); }
    else if (e.target.closest("[data-spchg]")) { S.items = []; chipDraw(); fire(); q.focus(); }
  });
  chipDraw();
  // 코드만 아는 종목(예전에 코드로 저장한 것)은 이름을 받아 와 바꿔 보여 준다
  const bare = S.items.filter((x) => x.name === x.sym && !US()).map((x) => x.sym);
  if (bare.length) GET(`/api/market/picks?mkt=${S.mkt}&syms=${encodeURIComponent(bare.join(","))}`).then((r) => {
    S.sug = S.sug || r.picks || [];
    let hit = false;
    S.items.forEach((x) => { const n = (r.names || {})[x.sym]; if (n && n !== x.sym && x.name === x.sym) { x.name = n; hit = true; } });
    if (hit && host.isConnected) chipDraw();
  }).catch(() => { /* 이름 표시만 생략 */ });
  return { items: () => S.items.slice(), codes: () => S.items.map((x) => x.sym), focus: () => q.focus(),
    setMkt(m) { S.mkt = m === "us" ? "us" : "kr"; S.sug = null; S.items = []; q.value = ""; res.innerHTML = ""; S.seq++; chipDraw(); } };
}
function planSheet(e, pre, after) {
  const done = after || (() => pages.invest.fill());
  openSheet(e ? "플랜 고치기" : "플랜 만들기", `
    <div class="field">종목<div id="pPick"></div></div>
    <label class="field">매달 금액<input id="pAmt" class="r" inputmode="numeric" value="${e ? won(e.amount) : ""}" placeholder="500,000"></label>
    <div class="tiny sp-hint" id="pHint" hidden></div>
    <div class="fields"><label class="field">매수일<select id="pDay">${Array.from({ length: 28 }, (_, i) => i + 1).map((n) => `<option value="${n}" ${(e?.day || 25) === n ? "selected" : ""}>매월 ${n}일</option>`).join("")}</select></label>
      <label class="field">목표 비중(%)<input id="pW" class="r" inputmode="decimal" value="${e?.target_weight || ""}" placeholder="선택"></label></div>
    <button class="btn dark wide" id="pSave">${e ? "고친 내용 저장" : "플랜 저장"}</button>
    ${e ? `<div class="btns"><button class="btn" id="pRun">지금 매수</button><button class="btn soft" id="pDel">지우기</button></div>` : `<div class="tiny">저장만 해요. 매수일에 자동 매수가 사요.</div>`}`, (box) => {
    moneyInput($("#pAmt", box));
    // 고른 종목의 1주 값을 보여 준다(한 달 금액이 1주 값보다 적으면 그 달은 사지 못한다)
    let price = 0, pseq = 0;
    const hint = () => {
      const h = $("#pHint", box), amt = num($("#pAmt", box).value);
      if (!h) return;
      h.hidden = !price;
      if (!price) return;
      h.classList.toggle("warn", amt > 0 && amt < price);
      h.textContent = `1주 약 ${won(price)}원` + (!amt ? "" : amt < price ? " · 이 금액으로는 1주를 못 사 그 달은 건너뛰어요" : ` · 한 번에 약 ${Math.floor(amt / price)}주`);
    };
    const quote = async (it) => {
      const my = ++pseq; price = 0; hint();
      if (!it.length) return;
      try { const r = await GET(`/api/market/quote/kr/${encodeURIComponent(it[0].sym)}`); if (my === pseq) { price = Number(r.price) || 0; hint(); } } catch (err) { /* 안내만 생략 */ }
    };
    const start = e ? [{ sym: e.code, name: e.name }] : pre && pre.code ? [{ sym: pre.code, name: pre.name || pre.code }] : [];
    const pk = stockPick($("#pPick", box), { mkt: "kr", value: start, onChange: (it) => { quote(it); if (it.length) $("#pAmt", box).focus(); } });
    $("#pAmt", box).addEventListener("input", hint);
    quote(start);
    if (pre && !e) setTimeout(() => $("#pAmt", box)?.focus(), 80);
    box.onclick = async (ev) => {
      const id = ev.target.id;
      if (id === "pSave") {
        const it = pk.items()[0];
        if (!it) { toast("종목을 이름으로 찾아 골라 주세요", true); return pk.focus(); }
        await run(() => api("POST", "/api/dca/plans", { id: e?.id, code: it.sym, name: it.name === it.sym ? "" : it.name, amount: num($("#pAmt").value), day: num($("#pDay").value), target_weight: num($("#pW").value), enabled: e ? !!e.enabled : true }), "플랜을 저장했어요");
        closeSheet(); done();
      } else if (id === "pDel") {
        if (!confirmTap(ev.target, "한 번 더 누르면 지워요")) return;
        await run(() => api("DELETE", `/api/dca/plans/${e.id}`), "플랜을 지웠어요"); closeSheet(); done();
      } else if (id === "pRun") {
        if (!confirmTap(ev.target, "한 번 더 누르면 사요")) return;
        const r = await run(() => api("POST", `/api/dca/plans/${e.id}/run`));
        const x = r.results[0];
        toast(x.status === "done" ? `${x.qty}주, ${won(x.amount)}원 매수했어요` : x.msg, x.status !== "done");
        closeSheet(); done();
      }
    };
  });
}
// 종목 하나를 적립으로: 이미 플랜이 있으면 고치기, 없으면 종목을 채운 새 플랜
async function dcaOne(code, name, after) {
  let B;
  try { B = await run(() => GET("/api/dca/plans")); } catch (e) { return; }
  if (pages.invest.dv) pages.invest.dv.dca = null;
  const p = B.plans.find((x) => x.code === code);
  planSheet(p || null, p ? null : { code, name }, after || (() => { if (pages.invest.dv) pages.invest.dv.dca = null; if ($("#iv")) pages.invest.fill(); }));
}
// 구성(종목·비중) → 적립 플랜 여러 개. 저장만 하고 주문은 하지 않는다(매수일에 자동 매수가 산다).
function dcaDraftSheet(src) {
  const S = { total: Math.round((src.total || 0) / 1e4) * 1e4, day: 0, spread: false, R: null, amt: {}, off: {}, err: "", seq: 0, t: 0 };
  const assets = src.assets.filter((a) => a.sym && a.weight > 0);
  if (!assets.some((a) => a.mkt === "kr")) return toast("자동 적립은 국내 상장 종목만 돼요. 이 구성에는 국내 종목이 없어요", true);
  openSheet("적립 플랜 만들기", `<div class="dd">
      <div class="tiny">${src.title ? `'${esc(src.title)}' 구성` : "이 구성"}의 비중대로 매달 넣을 돈을 나눠, 종목마다 적립 플랜을 만들어요.</div>
      <label class="field">매달 넣을 돈<span class="dv-unit"><input id="ddTot" inputmode="numeric" autocomplete="off" value="${S.total ? Math.round(S.total / 1e4) : ""}" placeholder="예: 50"><i>만원</i></span></label>
      <div class="chips dd-amt">${[[1e5, "10만"], [3e5, "30만"], [5e5, "50만"], [1e6, "100만"], [2e6, "200만"]].map(([v, t]) => `<button type="button" class="chip" data-tot="${v}" aria-pressed="${S.total === v}">${t}</button>`).join("")}</div>
      <label class="field">매수일<select id="ddDay">${Array.from({ length: 28 }, (_, i) => i + 1).map((n) => `<option value="${n}" ${n === 25 ? "selected" : ""}>매월 ${n}일</option>`).join("")}</select></label>
      <div id="ddOut"></div></div>`, (box) => {
    const out = $("#ddOut", box);
    const amt = (r) => (S.amt[r.code] != null ? S.amt[r.code] : r.amount);
    const picked = () => (S.R ? S.R.rows.filter((r) => !S.off[r.code]) : []);
    const foot = () => {
      const ps = picked(), sum = ps.reduce((t, r) => t + amt(r), 0), ok = ps.length && ps.every((r) => amt(r) >= 1000);
      if ($("#ddSumV", box)) $("#ddSumV", box).textContent = won(sum) + "원";
      if ($("#ddSumN", box)) $("#ddSumN", box).textContent = `플랜 ${ps.length}개 · 매달`;
      const b = $("#ddSave", box); if (b) { b.disabled = !ok; b.textContent = ps.length ? `플랜 ${ps.length}개 저장` : "담을 종목을 골라 주세요"; }
    };
    const draw = () => {
      const R = S.R;
      if (!S.total) { out.innerHTML = `<div class="empty small">매달 넣을 돈을 넣으면 종목별 금액을 나눠 보여 드려요</div>`; return; }
      if (S.err) { out.innerHTML = `<div class="notice warn">${esc(S.err)}</div>`; return; }
      if (!R) { out.innerHTML = `<div class="empty small loading">금액을 나누는 중</div>`; return; }
      const usW = R.us.reduce((t, u) => t + u.weight, 0), usAmt = R.us.reduce((t, u) => t + u.amount, 0);
      out.innerHTML = `<div class="dd-rows">${R.rows.map((r) => { const on = !S.off[r.code], v = amt(r);
          return `<div class="dd-row ${on ? "" : "off"}">
            <button type="button" class="dd-ck" role="checkbox" aria-checked="${on}" data-ck="${r.code}" aria-label="${esc(r.name)} 담기"></button>
            <span class="main"><b>${esc(r.name)}</b><small>비중 ${r.weight}%${r.price ? ` · 1주 ${won(r.price)}원` : ""}</small>
              ${r.plan ? `<small class="has">이미 적립 중이에요. 매달 ${won(r.plan.amount)}원에서 이 금액으로 바꿔요${r.plan.enabled ? "" : " (쉬는 중 → 다시 켜요)"}</small>` : ""}
              ${on && v < 1000 ? `<small class="warn">1,000원 이상이어야 해요</small>` : on && r.unit != null && v < r.unit ? `<small class="warn">1주 값보다 적어, 이 금액으로는 사지 못하고 건너뛰어요</small>` : ""}</span>
            <label class="dv-num dd-won"><input data-da="${r.code}" inputmode="numeric" autocomplete="off" value="${won(v)}" ${on ? "" : "disabled"} aria-label="${esc(r.name)} 매달 금액"><i>원</i></label></div>`; }).join("")}</div>
        ${R.us.length ? `<div class="dd-us"><b>미국 종목 ${R.us.length}개(${Math.round(usW)}%)는 자동 적립이 안 돼요</b>
          <span>${R.us.map((u) => esc(u.sym)).join(", ")} · ${S.spread ? "그 몫까지 국내 종목에 나눠 담았어요." : `매달 약 ${won(usAmt)}원은 주문 탭에서 직접 사야 해요.`}</span>
          <div class="lr-auto"><span><b>미국 몫도 국내 종목에 나눠 담기</b></span><button type="button" class="switch" role="switch" id="ddSpread" aria-checked="${S.spread}" aria-label="미국 몫도 국내 종목에 나눠 담기"><span></span></button></div></div>` : ""}
        <div class="dd-sum"><span id="ddSumN"></span><b id="ddSumV"></b></div>
        ${R.error ? `<div class="notice warn">${esc(R.error)}</div>` : ""}
        ${R.mode === "kiwoom-real" ? `<div class="notice warn">실전 계좌예요. 자동 매수가 켜져 있으면 매수일에 실제 주문이 나가요.</div>` : ""}
        <div class="tiny">${esc(R.mode_label || "계좌 없음")} · 자동 매수 ${R.auto ? "켜짐" : "꺼짐(적립 화면에서 켜야 사요)"}${R.others ? ` · 지금 있는 다른 플랜 ${R.others}개(매달 ${won(R.others_monthly)}원)는 그대로 둬요` : ""}</div>
        <button type="button" class="btn dark wide" id="ddSave"></button>
        <div class="tiny">저장만 하고 지금 주문하지 않아요. 매수일(휴장일이면 다음 영업일)에 자동 매수가 사고, 적립 화면에서 언제든 고치거나 끌 수 있어요.</div>`;
      foot();
    };
    const load = async () => {
      clearTimeout(S.t);
      const seq = ++S.seq;
      S.err = ""; S.amt = {};
      if (!S.total) { S.R = null; return draw(); }
      if (!S.R) draw();
      try {
        const R = await api("POST", "/api/dca/draft", { total: S.total, spread: S.spread, assets });
        if (seq !== S.seq) return;
        S.R = R;
        if (!S.day) { const p = R.rows.map((r) => r.plan).find(Boolean); S.day = p ? p.day : 25; $("#ddDay", box).value = String(S.day); }
      } catch (e) {
        if (e instanceof Locked) { closeSheet(); throw e; }
        if (seq !== S.seq) return;
        S.R = null; S.err = e.code === "old_server" ? "노트북의 앱이 아직 예전 버전이에요. 업데이트한 뒤 다시 해 주세요." : e.message;
      }
      draw();
    };
    box.oninput = (e) => {
      const t = e.target;
      if (t.id === "ddTot") { S.total = (Number(String(t.value).replace(/[^\d.]/g, "")) || 0) * 1e4; $$("[data-tot]", box).forEach((c) => c.setAttribute("aria-pressed", String(+c.dataset.tot === S.total))); clearTimeout(S.t); S.t = setTimeout(load, 450); }
      else if (t.dataset.da) { const v = num(t.value); S.amt[t.dataset.da] = v; t.value = v ? won(v) : ""; foot(); }
    };
    box.onchange = (e) => { if (e.target.id === "ddDay") S.day = +e.target.value; else if (e.target.dataset.da) draw(); };
    box.onclick = async (e) => {
      const q = (x) => e.target.closest(x);
      let b;
      if ((b = q("[data-tot]"))) { S.total = +b.dataset.tot; $("#ddTot", box).value = Math.round(S.total / 1e4); $$("[data-tot]", box).forEach((c) => c.setAttribute("aria-pressed", String(c === b))); return load(); }
      if ((b = q("[data-ck]"))) { S.off[b.dataset.ck] = !S.off[b.dataset.ck]; return draw(); }
      if (q("#ddSpread")) { S.spread = !S.spread; return load(); }
      if ((b = q("#ddSave")) && !b.disabled) {
        const ps = picked(), tw = ps.reduce((t, r) => t + r.weight, 0) || 1;
        b.disabled = true;
        let res;
        try {
          res = await run(() => api("POST", "/api/dca/plans/bulk", { day: S.day || 25, set_weight: !S.R.others && !S.R.rows.some((r) => S.off[r.code] && r.plan),
            items: ps.map((r) => ({ code: r.code, name: r.name, amount: amt(r), target_weight: Math.round(r.weight / tw * 1000) / 10 })) }));
        } catch (err) { b.disabled = false; return; }
        closeSheet();
        toast([res.created ? `플랜 ${res.created}개를 만들었어요` : "", res.updated ? `${res.updated}개는 금액을 바꿨어요` : ""].filter(Boolean).join(" · ") + ` · 매달 ${won(res.monthly)}원`);
        if (pages.invest.dv) pages.invest.dv.dca = null;
        if (location.hash.startsWith("#invest/dca")) pages.invest.fill(); else location.hash = "#invest/dca";
      }
    };
    load();
  });
}

// =====================================================================
// 설정
// =====================================================================
// ---------- 백업 화면 도우미 ----------
const bkSize = (n) => (n >= 1048576 ? (n / 1048576).toFixed(1) + "MB" : Math.max(1, Math.round((n || 0) / 1024)) + "KB");
const bkWhen = (ts) => `${+ts.slice(5, 7)}월 ${+ts.slice(8, 10)}일 ${ts.slice(11, 16)}`;
// now: 서버 시각(휴대폰과 노트북의 시간대가 달라도 맞게)
function bkAgo(ts, now) {
  const m = Math.max(0, Math.round(((now ? new Date(now).getTime() : Date.now()) - new Date(ts).getTime()) / 60000));
  return m < 2 ? "방금" : m < 60 ? `${m}분 전` : m < 1440 ? `${Math.floor(m / 60)}시간 전` : `${Math.floor(m / 1440)}일 전`;
}
// 파일로 내려받는 요청(응답이 JSON이 아니라 파일 내용)
async function apiBlob(url, body) {
  if (lock.on) throw new Locked("잠겨 있어요");
  let res;
  try { res = await fetch(apiBase() + url, { method: "POST", headers: authHeaders({ "Content-Type": "application/json" }), body: JSON.stringify(body || {}) }); }
  catch (e) { throw new Error("서버에 연결하지 못했어요"); }
  if (!res.ok) {
    const d = await res.json().catch(() => ({}));
    if (res.status === 401 && (d.code === "locked" || d.code === "setup")) { lock.show("unlock"); throw new Locked(d.error || "잠겨 있어요"); }
    throw new Error(d.error || `요청이 실패했어요 (${res.status})`);
  }
  return res.blob();
}
// 휴대폰은 공유 창(파일에 저장), PC는 바로 내려받기
async function bkSave(blob, name) {
  const file = new window.File([blob], name, { type: "application/octet-stream" });
  if (navigator.canShare && navigator.canShare({ files: [file] })) {
    try { await navigator.share({ files: [file], title: name }); return; }
    catch (e) { if (e && e.name === "AbortError") return; }
  }
  const a = document.createElement("a"), u = URL.createObjectURL(blob);
  a.href = u; a.download = name; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(u), 60000);
  toast("내려받았어요");
}
// 되돌린 뒤: 서버가 다시 뜰 때까지 기다렸다가 화면을 새로 연다
async function bkAfterRestore(r) {
  view.innerHTML = `<section class="card old-srv"><h2>백업으로 되돌렸어요</h2>
    <div class="tiny">${esc(bkWhen(r.restored.ts))} 시점으로 바꿨어요. 되돌리기 직전 상태도 백업 목록에 남겨 뒀어요.</div>
    <div class="empty small loading" id="bkWait">${r.restart ? "노트북의 앱을 다시 띄우는 중" : "화면을 새로 여는 중"}</div></section>`;
  if (!r.restart) { toast("되돌렸어요. 노트북에서 앱을 한 번 껐다 켜 주세요"); setTimeout(() => location.reload(), 1800); return; }
  await new Promise((ok) => setTimeout(ok, 3000));
  for (let n = 0; n < 60; n++) {
    try { const p = await fetch(apiBase() + "/api/ping", { headers: authHeaders(), cache: "no-store" }); if (p.ok) return location.reload(); } catch (e) { /* 아직 다시 뜨는 중 */ }
    await new Promise((ok) => setTimeout(ok, 1500));
  }
  if ($("#bkWait")) { $("#bkWait").classList.remove("loading"); $("#bkWait").textContent = "앱이 다시 뜨지 않았어요. 노트북에서 run.bat을 실행해 주세요."; }
}

pages.settings = {
  // 휴대폰 알림: 갈래별 켬/끔 · 시험 알림 · 최근 보낸 알림
  async notifyBox(el) {
    let N;
    try { N = await GET("/api/notify"); } catch (e) { el.innerHTML = ""; return; }
    if (!el.isConnected) return;
    const hm = (ts) => `${ts.slice(5, 10).replace("-", "/")} ${ts.slice(11, 16)}`;
    el.innerHTML = `<div class="nf-cats">${N.cats.map((c) => `<div class="nf-cat"><span><b>${esc(c.label)}</b>${c.fixed ? `<small>손실 한도·주문 실패·보안·엔진 오류 · 끌 수 없어요</small>` : ""}</span>
        <button type="button" class="switch" role="switch" data-nf="${c.key}" aria-checked="${!!c.on}" ${c.fixed ? "disabled" : ""} aria-label="${esc(c.label)} 알림"><span></span></button></div>`).join("")}</div>
      <div class="tiny">모의 매매는 하나하나 보내지 않고 장 마감 요약에 모아 보내요. 켜고 끈 것은 "설정 저장"을 눌러야 반영돼요.</div>
      <button type="button" class="btn sm" id="nfTest">시험 알림 보내기</button>
      <details class="nf-log"><summary>최근 알림 ${N.log.length}건</summary><ul>${N.log.map((x) => `<li class="${x.sent ? "" : "off"}"><time>${esc(hm(x.ts))}</time><div><b>${esc(x.title || "알림")}</b><p>${esc(x.msg)}</p>${x.sent ? "" : `<small>보내지 않음(꺼 둔 갈래이거나 토픽 없음)</small>`}</div></li>`).join("") || `<li class="off"><div><p>아직 없어요</p></div></li>`}</ul></details>`;
    el.onclick = async (e) => {
      const sw = e.target.closest("[data-nf]");
      if (sw && !sw.disabled) return sw.setAttribute("aria-checked", sw.getAttribute("aria-checked") !== "true");
      if (e.target.closest("#nfTest")) { try { await run(() => api("POST", "/api/notify/test"), "시험 알림을 보냈어요. 휴대폰을 확인해 주세요"); } catch (err) { /* 안내는 run이 띄운다 */ } }
    };
  },
  every: 5000,
  async refresh(h) {  // 계좌 연결 화면에서는 키움 카드만 몇 초마다 새로(입력란은 그대로)
    if (h.sub === "ai") {
      const cb = $("#crCard");
      if (!cb || cb.contains(document.activeElement) || $("details[open]", cb)) return;
      const cr = await GET("/api/ai/credit");
      if ($("#crCard") === cb) { cb.innerHTML = creditHtml(cr); bindCredit(cb, () => this.ai()); }
      return;
    }
    const box = $("#kwCard");
    if (h.sub !== "accounts" || !box || box.contains(document.activeElement)) return;
    const d = await kwFetch();
    if (d.configured && $("#kwCard") === box) { box.innerHTML = kwCardHtml(d); bindKwCard(box); }
  },
  // 설정 첫 화면: 항목마다 지금 상태를 보여 주고, 누르면 그 설정으로
  async hub() {
    const I = (d) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${d}</svg>`;
    const row = (id, ico, cls, title, state, tag = "") => `<a class="item st-row" href="#settings/${id}"><span class="ico ${cls}">${I(ico)}</span>
      <span class="main"><b>${title}</b><span class="sub">${state}</span></span>${tag}</a>`;
    const th = THEMES[store.get("theme") || "neon"] || THEMES.neon, fs = (FONT_SIZES.find((x) => x[0] === (store.get("fontSize") || "s")) || FONT_SIZES[0])[1];
    const draw = (s, st, ping, up, au, bk) => {
      const tm = s ? s.trade_mode : null, kw = s ? s.kiwoom : null;
      const acct = (k) => !tm ? "" : tm[k] === "kiwoom" ? (kw[k].mock ? "키움 모의투자" : "키움 실전") : "앱 내 모의";
      const cr = st && st.credit;
      view.innerHTML = `
        <div class="sec-title">자주 쓰는 설정</div>
        <section class="card tight"><div class="list">
          ${row("theme", '<circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 0 0 18z" fill="currentColor"/>', "h-pink", "화면", `${esc(th.name)} 테마 · 글자 ${esc(fs)}`)}
          ${row("ai", '<path d="M12 3l1.9 5.3L19 10l-5.1 1.7L12 17l-1.9-5.3L5 10l5.1-1.7z"/><path d="M19 15l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7z"/>', "h-violet", "AI",
            !st ? "상태를 못 받았어요" : !st.configured ? "Claude API 키를 넣으면 쓸 수 있어요" : `${cr && cr.set ? `잔액 $${Number(cr.left).toFixed(2)} · ` : ""}이번 달 $${Number(st.month_usd || 0).toFixed(2)} 사용`,
            st && !st.configured ? `<span class="tag warn">키 필요</span>` : cr && cr.set && cr.left_pct < 15 ? `<span class="tag warn">잔액 적음</span>` : "")}
          ${row("security", '<path d="M12 3l7.5 3v5.6c0 4.6-3.1 7.9-7.5 9.4-4.4-1.5-7.5-4.8-7.5-9.4V6z"/><path d="M8.8 12.2l2.3 2.3 4.2-4.4"/>', "h-sky", "보안",
            !au ? "잠금 상태를 확인하는 중" : !au.set ? "비밀번호를 만들면 앱을 열 때 물어요" : `비밀번호 잠금 켜짐 · ${LOCK_LABEL[au.lock_after]} 뒤 자동 잠금`,
            au && !au.set ? `<span class="tag warn">설정 필요</span>` : au && au.fails ? `<span class="tag warn">틀린 시도 ${au.fails}번</span>` : "")}
          ${row("backup", '<path d="M4 7c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3z"/><path d="M4 7v5c0 1.7 3.6 3 8 3s8-1.3 8-3V7"/><path d="M4 12v5c0 1.7 3.6 3 8 3s8-1.3 8-3v-5"/>', "h-teal", "백업",
            !bk ? "백업 상태를 확인하는 중" : !bk.last ? "아직 백업이 없어요" : `마지막 백업 ${bkAgo(bk.last.ts, bk.now)} · 자동 백업 ${bk.auto ? "켜짐" : "꺼짐"}`,
            bk && (!bk.last || new Date(bk.now).getTime() - new Date(bk.last.ts).getTime() > 3 * 864e5) ? `<span class="tag warn">백업 필요</span>` : "")}
        </div></section>
        <div class="sec-title">계좌와 주문</div>
        <section class="card tight"><div class="list">
          ${row("accounts", '<rect x="3" y="6" width="18" height="13" rx="2.5"/><path d="M3 10h18"/><path d="M7 15h4"/>', "h-indigo", "계좌 연결", s ? `기본 ${acct("scalp")} · 장기투자 ${acct("dca")}` : "서버에 연결하면 보여요")}
          ${row("trading", '<path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>', "h-amber", "주문·알림", s ? `시세 ${s.data_source === "kiwoom" ? "키움 실시간" : s.data_source === "demo" ? "가상 시세" : esc(s.data_source || "-")} · 휴대폰 알림 ${s.ntfy_topic ? "켜짐" : "꺼짐"}` : "시세 출처, 거래 비용, 적립 매수 시간, 알림")}
        </div></section>
        <div class="sec-title">처음 한 번만</div>
        <section class="card tight"><div class="list">
          ${row("connect", '<rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/><path d="M7 7.5h.01M7 16.5h.01"/>', "h-teal", "서버 연결", ping && ping.ok ? `노트북과 연결됨 · v${esc(ping.version || "")}` : "노트북 서버에 연결되지 않았어요", ping && ping.ok ? `<span class="tag g">연결됨</span>` : `<span class="tag warn">끊김</span>`)}
          ${row("app", '<circle cx="12" cy="12" r="9"/><path d="M12 8h.01M11 12h1v5h1"/>', "h-slate", "앱 정보", `${ping && ping.version ? "v" + esc(ping.version) : "버전 확인"}${up && up.available ? " · 새 버전이 있어요" : ""}`, up && up.available ? `<span class="tag y">업데이트</span>` : "")}
        </div></section>
        <div class="tiny" style="padding:0 3px">아래 메뉴는 자주 쓰는 순서예요. 서버 연결은 처음 한 번만 맞추면 돼서 뒤에 뒀어요.</div>`;
    };
    draw(null, null, null, null, null, null);
    const [s, st, ping, up, au, bk] = await Promise.all([GET("/api/settings").catch(() => null), GET("/api/ai/status").catch(() => null), GET("/api/ping").catch(() => null), GET("/api/update").catch(() => null), GET("/api/auth/state").catch(() => null), GET("/api/backup").catch(() => null)]);
    if (location.hash.replace(/^#/, "").startsWith("settings/all") || location.hash === "#settings") draw(s, st, ping, up, au, bk);
  },
  // 백업: 지금 백업, 자동 백업, 내려받기, 되돌리기
  async backup() {
    let B;
    try { B = await GET("/api/backup"); } catch (e) {
      if (e.code !== "old_server") throw e;
      return oldServerBox(view, () => this.backup());
    }
    if (!location.hash.startsWith("#settings/backup")) return;
    const I = (d) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${d}</svg>`;
    const last = B.last, stale = !last || new Date(B.now).getTime() - new Date(last.ts).getTime() > 3 * 864e5;
    // 최근 14일: 백업이 남아 있는 날
    const days = Array.from({ length: 14 }, (_, i) => { const d = new Date(B.today + "T00:00:00"); d.setDate(d.getDate() - (13 - i)); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; });
    const has = new Set(B.days), kept = days.filter((d) => has.has(d)).length;
    const cnt = (c) => [c.transactions != null ? `내역 ${won(c.transactions)}건` : "", c.accounts ? `계좌 ${c.accounts}` : "", c.dca_plans ? `적립 ${c.dca_plans}` : ""].filter(Boolean).join(" · ");
    view.innerHTML = `
      <section class="card bk-hero ${stale ? "off" : "on"}">
        <div class="bk-top"><span class="sc-shield">${I('<path d="M4 7c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3z"/><path d="M4 7v5c0 1.7 3.6 3 8 3s8-1.3 8-3V7"/><path d="M4 12v5c0 1.7 3.6 3 8 3s8-1.3 8-3v-5"/>')}</span>
          <div><b>${last ? `마지막 백업 ${bkAgo(last.ts, B.now)}` : "아직 백업이 없어요"}</b>
            <span>${last ? `${bkWhen(last.ts)} · ${esc(last.kind_name)} · ${bkSize(last.size)}` : "지금 한 번 만들어 두면, 잘못 지우거나 고장 나도 되돌릴 수 있어요"}</span></div></div>
        <div class="bk-days" role="img" aria-label="최근 14일 가운데 ${kept}일의 백업이 남아 있어요">${days.map((d) => `<i class="${has.has(d) ? "on" : ""} ${d === B.today ? "today" : ""}"></i>`).join("")}</div>
        <div class="bk-days-l"><span>14일 전</span><span>최근 14일 중 ${kept}일</span><span>오늘</span></div>
        <button type="button" class="btn dark wide" id="bkNow">지금 백업</button>
      </section>
      <section class="card">
        <div class="card-head"><div><h2>자동 백업</h2><div class="tiny">하루 한 번, 노트북이 켜져 있을 때 만들어요. 업데이트 전과 되돌리기 전에도 따로 남겨요.</div></div>
          <button type="button" class="switch" role="switch" id="bkAuto" aria-checked="${B.auto}" aria-label="자동 백업"><span></span></button></div>
        <div class="dv-lab">자동 백업 보관</div>${seg("bkKeep", B.keep_choices.map((n) => [String(n), `${n}일치`]), String(B.keep), "on-card")}
      </section>
      <section class="card">
        <h2>저장 위치</h2>
        <dl class="bk-dl"><div><dt>노트북</dt><dd>${esc(B.dir)}</dd></div>
          <div><dt>다른 폴더</dt><dd>${B.extra_dir ? `${esc(B.extra_dir)}${B.extra_ok ? "" : ` <span class="tag warn">폴더 없음</span>`}` : "정하지 않았어요"}</dd></div></dl>
        ${B.local ? `<form id="bkDirF" class="bk-dirf" novalidate><input id="bkDir" autocomplete="off" spellcheck="false" value="${esc(B.extra_dir)}" placeholder="예: D:\\백업 또는 OneDrive 폴더 경로" aria-label="다른 폴더 경로"><button type="submit" class="btn">저장</button></form>
          <div class="tiny">클라우드와 맞춰지는 폴더(OneDrive 등)나 USB를 정해 두면 백업할 때마다 그곳에도 복사해요. 비우고 저장하면 꺼요.</div>`
          : `<div class="tiny">다른 폴더(클라우드·USB)는 노트북에서 이 화면을 열어 정할 수 있어요.</div>`}
        ${!B.extra_dir ? `<div class="notice">백업은 노트북 안에만 있어요. 노트북이 고장 나면 함께 사라지니, 가끔 휴대폰에 내려받거나 다른 폴더를 정해 두세요.</div>` : ""}
      </section>
      <div class="sec-title">백업 목록 · ${B.items.length}개 · ${bkSize(B.total_size)}</div>
      <section class="card tight"><div class="list">${B.items.length ? B.items.map((i) => `<button type="button" class="item bk-row" data-bk="${esc(i.name)}">
        <span class="ico">${i.ts.slice(5, 10).replace("-", ".")}</span>
        <span class="main"><b>${bkWhen(i.ts)}</b><span class="sub">${esc(cnt(i.counts) || "내용 요약 없음")}${i.note && i.kind !== "upload" ? ` · ${esc(i.note)}` : ""}</span></span>
        <span class="end"><span class="tag ${i.kind === "auto" ? "" : i.kind === "manual" ? "v" : "y"}">${esc(i.kind_name)}</span><span class="tiny">${bkSize(i.size)}</span></span></button>`).join("")
        : `<div class="empty">아직 백업이 없어요</div>`}</div></section>
      <section class="card">
        <h2>파일에서 되돌리기</h2>
        <div class="tiny">휴대폰이나 다른 PC에 내려받아 둔 백업 파일(.db)을 올려요. 올린 뒤 내용을 확인하고 되돌릴 수 있어요.</div>
        <label class="btn wide bk-up"><input type="file" id="bkFile" accept=".db,application/octet-stream,application/x-sqlite3" hidden>백업 파일 고르기</label>
      </section>
      <div class="tiny" style="padding:0 3px">백업에는 가계부·자산·투자 기록과 설정이 모두 들어 있어요${B.db_size ? `(지금 데이터 ${bkSize(B.db_size)})` : ""}. 종목 목록 같은 다시 받을 수 있는 자료는 빼요.</div>`;
    const again = () => this.backup();
    $("#bkNow").onclick = async (e) => {
      const b = e.currentTarget; b.disabled = true; b.textContent = "백업하는 중";
      try { const m = await run(() => api("POST", "/api/backup", {})); toast(m.copy_error ? `백업했어요. ${m.copy_error}` : `백업했어요 · ${bkSize(m.size)}${m.copied ? " · 다른 폴더에도 복사" : ""}`, !!m.copy_error); } catch (err) { /* 안내함 */ }
      again();
    };
    $("#bkAuto").onclick = async () => { await run(() => api("PUT", "/api/backup/config", { auto: !B.auto }), B.auto ? "자동 백업을 껐어요" : "자동 백업을 켰어요").catch(() => null); again(); };
    bindSeg(view, "bkKeep", async (v) => { await run(() => api("PUT", "/api/backup/config", { keep: Number(v) }), `자동 백업을 ${v}일치 남겨요`).catch(() => null); again(); });
    if ($("#bkDirF")) $("#bkDirF").onsubmit = async (e) => {
      e.preventDefault();
      const v = $("#bkDir").value.trim();
      const r = await run(() => api("PUT", "/api/backup/config", { extra_dir: v }), v ? "백업할 때마다 이 폴더에도 복사해요" : "다른 폴더 복사를 껐어요").catch(() => null);
      if (r) again();
    };
    $("#bkFile").onchange = async (e) => {
      const f = e.target.files[0];
      if (!f) return;
      const fd = new FormData(); fd.append("file", f);
      let m;
      try { m = await run(() => api("POST", "/api/backup/upload", fd, true), "파일을 올렸어요. 내용을 확인해 주세요"); } catch (err) { e.target.value = ""; return; }
      await this.backup();
      this.backupSheet(m, { ...B, items: [m, ...B.items] });
    };
    view.onclick = (e) => { const r = e.target.closest("[data-bk]"); if (r) this.backupSheet(B.items.find((i) => i.name === r.dataset.bk), B); };
  },
  // 백업 하나: 내용 요약, 내려받기, 되돌리기, 지우기
  backupSheet(i, B) {
    if (!i) return;
    const c = i.counts || {}, pw = B.need_password;
    const S = { blob: null, full: false };
    openSheet(bkWhen(i.ts), `<div class="bk-sh">
      <div class="chips"><span class="tag ${i.kind === "auto" ? "" : i.kind === "manual" ? "v" : "y"}">${esc(i.kind_name)}</span>${i.version ? `<span class="tag">v${esc(i.version)}</span>` : ""}${i.keyless ? `<span class="tag y">키 뺀 사본</span>` : ""}<span class="tag">${bkSize(i.size)}</span></div>
      <dl class="dv-dl">${[["가계부 내역", c.transactions, "건"], ["계좌·자산", c.accounts, "개"], ["적립 플랜", c.dca_plans, "개"], ["AI 추천", c.ai_picks, "건"], ["주문 기록", c.orders, "건"]].filter((x, n) => x[1] != null && (x[1] > 0 || n === 0)).map(([k, v, u]) => `<div><dt>${k}</dt><dd>${won(v)}${u}</dd></div>`).join("")}
        ${c.last ? `<div><dt>내역 기간</dt><dd>${esc(c.first)} ~ ${esc(c.last)}</dd></div>` : ""}</dl>
      <h3 class="lr-h">내려받기</h3>
      <div class="tiny">이 기기에 파일로 남겨요. 기본 사본에는 API 키·접속 토큰·비밀번호를 넣지 않아서, 파일이 새어도 계좌에 접근할 수 없어요.</div>
      <div class="lr-auto"><span><b>키까지 포함한 전체 사본</b><small>새 노트북으로 옮길 때만. 파일을 안전한 곳에 두세요${pw ? " · 비밀번호 확인" : ""}</small></span>
        <button type="button" class="switch" role="switch" id="bkFull" aria-checked="false" aria-label="키까지 포함한 전체 사본"><span></span></button></div>
      <button type="button" class="btn wide" id="bkDown">내려받을 파일 만들기</button>
      <h3 class="lr-h">되돌리기</h3>
      <div class="tiny">지금 데이터를 이 백업 시점으로 바꿔요. 이 뒤에 넣은 내역은 사라지지만, 되돌리기 직전 상태를 자동으로 백업해 두니 다시 돌아올 수 있어요. 접속 토큰과 비밀번호는 지금 것을 그대로 써요.</div>
      ${pw ? `<label class="field" id="bkPwF">앱 비밀번호<input id="bkPw" type="password" autocomplete="current-password" placeholder="되돌리기·전체 사본에 필요해요"></label>` : ""}
      <button type="button" class="btn wide bk-danger" id="bkRestore">이 백업으로 되돌리기</button>
      <button type="button" class="btn sm soft wide" id="bkDel">이 백업 지우기</button></div>`, (box) => {
      const pwv = () => ($("#bkPw", box) ? $("#bkPw", box).value : "");
      const needPw = () => { if (pw && !pwv()) { toast("앱 비밀번호를 넣어 주세요", true); $("#bkPw", box).focus(); return true; } return false; };
      $("#bkFull", box).onclick = (e) => { S.full = !S.full; e.currentTarget.setAttribute("aria-checked", String(S.full)); S.blob = null; $("#bkDown", box).textContent = "내려받을 파일 만들기"; };
      // 두 단계: 파일을 받아 온 뒤, 한 번 더 눌러 저장할 곳을 고른다(휴대폰의 공유 창은 누른 직후에만 열린다)
      $("#bkDown", box).onclick = async (e) => {
        const b = e.currentTarget;
        if (S.blob) return bkSave(S.blob, i.name.replace(/\.db$/, S.full ? "-full.db" : ".db"));
        if (S.full && needPw()) return;
        b.disabled = true; b.textContent = "파일을 만드는 중";
        try {
          S.blob = await apiBlob(`/api/backup/${encodeURIComponent(i.name)}/download`, { full: S.full, password: pwv() });
          b.textContent = `저장할 곳 고르기 · ${bkSize(S.blob.size)}`; b.classList.add("dark");
        } catch (err) { if (err instanceof Locked) { closeSheet(); throw err; } toast(err.message, true); b.textContent = "내려받을 파일 만들기"; }
        b.disabled = false;
      };
      $("#bkRestore", box).onclick = async (e) => {
        const b = e.currentTarget;
        if (needPw()) return;
        if (!confirmTap(b, "한 번 더 누르면 되돌려요")) return;
        b.disabled = true; b.textContent = "되돌리는 중";
        let r;
        try { r = await run(() => api("POST", `/api/backup/${encodeURIComponent(i.name)}/restore`, { password: pwv() })); }
        catch (err) { b.disabled = false; b.textContent = "이 백업으로 되돌리기"; return; }
        closeSheet(); purgeCache();
        bkAfterRestore(r);
      };
      $("#bkDel", box).onclick = async (e) => {
        if (!confirmTap(e.currentTarget, "한 번 더 누르면 지워요")) return;
        try { await run(() => api("DELETE", `/api/backup/${encodeURIComponent(i.name)}`), "백업을 지웠어요"); } catch (err) { return; }
        closeSheet(); this.backup();
      };
    });
  },
  // 보안: 잠금 상태, 자동 잠금 시간, 비밀번호 바꾸기, 점검표
  async security() {
    const [st, s, ping] = await Promise.all([GET("/api/auth/state"), GET("/api/settings").catch(() => null), GET("/api/ping").catch(() => null)]);
    lock.after = st.lock_after;
    const I = (d) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${d}</svg>`;
    const act = (id, ico, cls, title, sub) => `<button type="button" class="item st-row sc-act" data-sc="${id}"><span class="ico ${cls}">${I(ico)}</span><span class="main"><b>${title}</b><span class="sub">${sub}</span></span></button>`;
    const base = apiBase() || location.origin, https = /^https:/i.test(base) || /^(http:\/\/)?(127\.0\.0\.1|localhost)/i.test(base.replace(/^https?:\/\//, "http://"));
    const chk = (ok, title, sub) => `<li class="${ok === true ? "ok" : ok === false ? "bad" : "todo"}"><i aria-hidden="true">${ok === true ? I('<path d="M5 12.5l4.5 4.5L19 7.5"/>') : ok === false ? I('<path d="M12 8v5M12 16.5h.01"/>') : I('<circle cx="12" cy="12" r="3.2"/>')}</i><span><b>${title}</b><em>${sub}</em></span></li>`;
    const failTxt = st.fails ? `비밀번호를 ${st.fails}번 틀린 기록이 있어요${st.last_fail ? ` (${esc(st.last_fail.slice(5, 16).replace("T", " "))})` : ""}. 본인이 아니면 접속 토큰을 새로 만드세요` : "틀린 비밀번호 시도가 없어요";
    view.innerHTML = `
      <section class="card sc-hero ${st.set ? "on" : "off"}">
        <span class="sc-shield">${I(st.set ? '<path d="M12 3l7.5 3v5.6c0 4.6-3.1 7.9-7.5 9.4-4.4-1.5-7.5-4.8-7.5-9.4V6z"/><path d="M8.8 12.2l2.3 2.3 4.2-4.4"/>' : '<path d="M12 3l7.5 3v5.6c0 4.6-3.1 7.9-7.5 9.4-4.4-1.5-7.5-4.8-7.5-9.4V6z"/><path d="M12 8.5v4M12 15.5h.01"/>')}</span>
        <div><b>${st.set ? "비밀번호 잠금을 쓰고 있어요" : "아직 비밀번호가 없어요"}</b>
          <span>${st.local ? (st.set ? "이 노트북에서는 묻지 않고, 휴대폰처럼 바깥에서 열 때 물어요" : "비밀번호를 만들면 휴대폰에서 열 때마다 물어요") : `열려 있는 기기 ${st.sessions}대 · ${LOCK_LABEL[st.lock_after]} 동안 안 쓰면 잠겨요`}</span></div>
      </section>
      ${st.set ? `<section class="card">
        <h2>자동 잠금</h2>
        <div class="small muted" style="margin:-2px 0 9px">이 시간 동안 앱을 쓰지 않으면 다시 비밀번호를 물어요. 앱을 완전히 닫았다 열 때는 항상 물어요.</div>
        ${seg("lockAfter", st.choices.map((m) => [String(m), LOCK_LABEL[m]]), String(st.lock_after), "on-card")}
      </section>` : ""}
      <section class="card tight"><div class="list">
        ${st.set ? act("change", '<path d="M15 7a4 4 0 1 1-3.9 5H8v2.5H5.5V17H3v-2.6l5.2-5.2A4 4 0 0 1 15 7z"/><path d="M16 9.5h.01"/>', "h-indigo", "비밀번호 바꾸기", "바꾸면 다른 기기는 모두 잠겨요")
          : act("make", '<path d="M15 7a4 4 0 1 1-3.9 5H8v2.5H5.5V17H3v-2.6l5.2-5.2A4 4 0 0 1 15 7z"/><path d="M16 9.5h.01"/>', "h-indigo", "비밀번호 만들기", "휴대폰에서 열 때 물어볼 비밀번호")}
        ${st.set && !st.local ? act("lock", '<rect x="5" y="11" width="14" height="9" rx="2.2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>', "h-pink", "지금 잠그기", "이 기기를 바로 잠가요") : ""}
        ${st.set ? act("all", '<rect x="3" y="5" width="12" height="9" rx="1.6"/><path d="M7 18h4M9 14v4"/><rect x="17" y="9" width="4.5" height="9" rx="1.2"/>', "h-amber", "모든 기기에서 잠그기", `열려 있는 ${st.sessions}대를 모두 닫아요. 휴대폰을 잃어버렸을 때`) : ""}
        ${st.set && st.local ? act("reset", '<path d="M4 12a8 8 0 1 0 2.6-5.9"/><path d="M4 4v4.5h4.5"/>', "h-rose", "비밀번호 초기화", "잊었을 때. 이 노트북에서만 할 수 있어요") : ""}
      </div></section>
      <section class="card">
        <h2>보안 점검</h2>
        <ul class="sc-list">
          ${chk(!!st.set, "앱 비밀번호", st.set ? "토큰이 새어도 비밀번호 없이는 데이터를 볼 수 없어요" : "비밀번호를 만들어 주세요")}
          ${chk(https, "암호화된 주소(https)", https ? "휴대폰과 노트북 사이 내용이 암호화돼요" : "지금 주소는 http라 내용이 그대로 보여요. Tailscale의 https 주소로 바꾸세요")}
          ${chk(!st.fails, "틀린 접속 시도", failTxt)}
          ${s ? chk(!!s.ntfy_topic, "보안 알림", s.ntfy_topic ? "비밀번호·토큰을 여러 번 틀리면 휴대폰 알림을 보내요" : "주문·알림 설정에서 휴대폰 알림을 켜면 틀린 접속 시도를 바로 알려 줘요") : ""}
          ${s ? chk(null, "실제 주문", s.real_orders_allowed ? "실제 주문이 켜져 있어요. 잠금을 풀어야만 주문할 수 있어요" : "실제 주문이 꺼져 있어요") : ""}
          ${chk(null, "GitHub 2단계 인증", "앱은 GitHub에 올린 코드를 받아 노트북에서 실행해요. GitHub 계정에 2단계 인증을 꼭 켜 두세요")}
          ${chk(null, "접속 토큰", "토큰이 새었다고 느끼면 노트북 화면의 설정 → 서버에서 새로 만드세요. 열려 있던 기기도 함께 잠겨요")}
        </ul>
      </section>
      <div class="tiny" style="padding:0 3px">비밀번호는 노트북 서버에만 암호화한 값으로 저장하고, 휴대폰에는 남기지 않아요. 잠기면 휴대폰에 남겨 둔 화면 자료도 지워요.${ping && ping.version ? ` · v${esc(ping.version)}` : ""}</div>`;
    bindSeg(view, "lockAfter", async (v) => { const r = await run(() => api("PUT", "/api/auth/options", { lock_after: Number(v) }), `${LOCK_LABEL[v]} 동안 안 쓰면 잠겨요`).catch(() => null); if (r) lock.after = r.lock_after; });
    view.onclick = null;
    $$("[data-sc]", view).forEach((b) => (b.onclick = async () => {
      const k = b.dataset.sc;
      if (k === "lock") return lock.now(false);
      if (k === "all") {
        if (!confirm(`열려 있는 기기 ${st.sessions}대를 모두 잠글까요? 이 기기도 비밀번호를 다시 넣어야 해요.`)) return;
        if (st.local) { await run(() => api("POST", "/api/auth/lock", { everywhere: true }), "모든 기기를 잠갔어요"); return route(); }
        return lock.now(true);
      }
      if (k === "reset") {
        if (!confirm("비밀번호를 지울까요? 데이터는 그대로 두고, 휴대폰에서 다음에 열 때 새 비밀번호를 만들게 돼요.")) return;
        await run(() => api("POST", "/api/auth/reset"), "비밀번호를 지웠어요"); return route();
      }
      const make = k === "make";
      openSheet(make ? "비밀번호 만들기" : "비밀번호 바꾸기", `<form id="pwForm" class="sc-form" novalidate>
        <input class="lk-user" type="text" name="username" autocomplete="username" value="가계부S" readonly tabindex="-1" aria-hidden="true">
        ${make ? "" : `<label class="field">지금 비밀번호<input id="pwCur" type="password" autocomplete="current-password" autocapitalize="off" spellcheck="false" maxlength="128"></label>`}
        <label class="field">새 비밀번호<input id="pwNew" type="password" autocomplete="new-password" autocapitalize="off" spellcheck="false" maxlength="128">
          <span class="hint">4자 이상. 6자 이상에 숫자와 글자를 섞으면 더 안전해요</span></label>
        <label class="field">새 비밀번호 한 번 더<input id="pwNew2" type="password" autocomplete="new-password" autocapitalize="off" spellcheck="false" maxlength="128"></label>
        <button type="submit" class="btn dark wide" id="pwGo">${make ? "만들기" : "바꾸기"}</button></form>`, (box) => {
        $("#pwForm", box).onsubmit = async (e) => {
          e.preventDefault();
          const a = $("#pwNew", box).value, b2 = $("#pwNew2", box).value;
          if (a !== b2) return toast("새 비밀번호 두 칸이 서로 달라요", true);
          try {
            if (make) { const r = await run(() => api("POST", "/api/auth/setup", { password: a }), "비밀번호를 만들었어요"); if (!st.local) sess.set(r.session, false); }
            else await run(() => api("POST", "/api/auth/change", { current: $("#pwCur", box).value, new: a }), "비밀번호를 바꿨어요");
          } catch (err) { return; }  // 안내는 run이 이미 띄웠다
          closeSheet(); route();
        };
        setTimeout(() => $(make ? "#pwNew" : "#pwCur", box)?.focus(), 80);
      });
    }));
  },
  async ai() {
    const st = await GET("/api/ai/status");
    const s = await GET("/api/settings");
    const c = s.ai || {};
    view.innerHTML = `
      <section class="card ai-cr" id="crCard">${creditHtml(st.credit)}</section>
      <section class="card">
        <div class="card-head"><h2>Claude AI 도우미</h2><span class="tag ${st.configured ? "g" : "warn"}">${st.configured ? "연결됨" : "키 필요"}</span></div>
        <div class="small muted">투자 화면의 ✦ 버튼에서 종목 정리, 포트폴리오 점검, AI 종목 추천, 자유 질문을 할 수 있어요. AI는 주문하지 않아요.</div>
        <label class="field">Claude API 키<input id="aiKey" type="password" value="${esc(c.api_key || "")}" placeholder="sk-ant-..." autocomplete="off" autocapitalize="off"></label>
        <div class="tiny">platform.claude.com/settings/keys에서 만들어요. 키는 노트북에만 저장하고 화면엔 가려서 보여요.</div>
        <label class="field">모델<select id="aiModel">${Object.entries(st.models).map(([k, v]) => `<option value="${k}" ${k === c.model ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></label>
        <label class="field">복기 엔진<select id="aiReview">${Object.entries(st.models).map(([k, v]) => `<option value="${k}" ${k === (c.review_model || "claude-opus-5-5") ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></label>
        <div class="tiny">쌓인 매매·추천 기록을 돌아보고 규칙을 고칠 때 쓰는 엔진이에요. 플랜마다 주 1번 안팎이라 좋은 엔진을 써도 한 번 100원 남짓이에요.</div>
        <div class="fields"><label class="field">월 예산(달러)<input id="aiBudget" class="r" inputmode="decimal" value="${esc(c.budget_usd ?? 10)}"></label>
          <label class="field">이번 달 사용<input value="$${st.month_usd} · ${st.month_calls}회" disabled></label></div>
        <label class="check"><input type="checkbox" id="aiWeb" ${c.web_search !== false ? "checked" : ""}> 웹 검색으로 최신 뉴스 찾기(검색 1회 약 14원 추가)</label>
        <details><summary class="tiny">콘솔 전체 사용액까지 반영(선택: Admin API 키)</summary>
          <label class="field">Admin API 키<input id="aiAdmin" type="password" value="${esc(c.admin_key || "")}" placeholder="sk-ant-admin..." autocomplete="off" autocapitalize="off"></label>
          <div class="tiny">moa 밖(콘솔 플레이그라운드 등)에서 쓴 금액까지 빼려면 넣어요. 개인 계정은 조직을 만들어야 발급돼요. 없으면 moa 사용분만 빼요.</div></details>
        <div class="btns"><button class="btn" id="aiTest">연결 확인</button><button class="btn dark" id="aiSave">저장</button></div>
      </section>
      <section class="card" id="dartCard"><div class="card-head"><h2>전자공시(DART)</h2><span class="tag" id="dartTag">확인 중</span></div>
        <div class="tiny">금융감독원 전자공시 인증키를 넣으면 AI 추천 후보와 보유 종목에 최근 공시 제목(공급계약·증자·실적 등)을 붙여 줘요. 인증키는 opendart.fss.or.kr에서 무료로 받아요. 없어도 다른 기능은 그대로 돌아요.</div>
        <label class="field">인증키<input id="dartKey" type="password" placeholder="40자리 인증키" autocomplete="off" autocapitalize="off"></label>
        <div class="tiny" id="dartNote"></div>
        <div class="btns"><button class="btn dark" id="dartSave">저장하고 확인</button></div>
        <details><summary class="tiny">검색량 추이도 붙이기(선택: 네이버 데이터랩 키) <span id="nvTag"></span></summary>
          <div class="tiny">네이버 개발자센터에서 '데이터랩(검색어 트렌드)' 앱을 만들어 받은 값을 넣으면, 스윙·장기 후보에 종목 이름 검색량이 최근 7일에 얼마나 늘었는지 붙여요. 검색량이 실제로 도움이 되는지는 추천 성적에서 따로 재요.</div>
          <div class="fields"><label class="field">Client ID<input id="nvId" autocomplete="off" autocapitalize="off"></label>
            <label class="field">Client Secret<input id="nvSec" type="password" autocomplete="off" autocapitalize="off"></label></div>
          <div class="tiny" id="nvNote"></div>
          <div class="btns"><button class="btn" id="nvSave">저장하고 확인</button></div></details>
      </section>
      <section class="card tight"><div class="card-head" style="padding-top:8.5px"><h2>최근 사용</h2></div><div class="list">
        ${st.recent.length ? st.recent.map((r) => `<div class="item"><span class="ico">${esc({ stock: "종목", portfolio: "점검", review: "복기", sweep: "훑기", chat: "질문", test: "확인", pick: "추천", pickw: "중기", learn: "복기", divplan: "배당" }[r.kind] || r.kind)}</span>
          <span class="main"><b>${esc(r.ts.slice(5, 16).replace("T", " "))}</b><span class="sub">${esc(r.model.replace("claude-", ""))} · 입력 ${won(r.in_tok)} · 출력 ${won(r.out_tok)}${r.searches ? ` · 검색 ${r.searches}` : ""}</span></span>
          <span class="end"><b>$${Number(r.usd).toFixed(4)}</b></span></button>`).join("") : `<div class="empty">아직 사용 기록이 없어요</div>`}</div></section>`;
    const body = () => ({ ai: { api_key: $("#aiKey").value.trim(), admin_key: $("#aiAdmin").value.trim(), model: $("#aiModel").value, review_model: $("#aiReview").value, budget_usd: Number($("#aiBudget").value) || 0, web_search: $("#aiWeb").checked } });
    bindCredit($("#crCard"), () => this.ai());
    const dartShow = (s) => {
      const n = s.note || {};
      $("#dartTag").textContent = !s.configured ? "키 없음" : n.ok ? "연결됨" : n.ok === false ? "오류" : "확인 중";
      $("#dartTag").className = "tag" + (s.configured && n.ok === false ? " warn" : "");
      $("#dartNote").textContent = !s.configured ? "" : n.ok ? `최근 4일 공시 ${n.n}건 · 상장 종목 ${n.codes}개 (${String(n.at || "").slice(11, 16)} 기준)` : n.error || "";
      if (s.configured) $("#dartKey").placeholder = "저장돼 있어요(바꿀 때만 입력)";
    };
    const nvShow = (t) => {
      const n = (t || {}).note || {};
      $("#nvTag").textContent = !t || !t.configured ? "" : n.ok === false ? "· 오류" : "· 켜짐";
      $("#nvNote").textContent = t && t.configured && n.ok === false ? n.error || "" : t && t.sample && t.sample["삼성전자"] != null ? `확인: 삼성전자 검색량 ${t.sample["삼성전자"]}배` : "";
    };
    api("GET", "/api/dart").then((s) => { dartShow(s); nvShow(s.trend); }).catch(() => {});
    $("#nvSave").onclick = async () => {
      const id = $("#nvId").value.trim(), sec = $("#nvSec").value.trim();
      if (id && sec) await run(() => api("PUT", "/api/settings", { naver: { client_id: id, client_secret: sec } }), "저장했어요");
      nvShow(await api("POST", "/api/trend/test"));
      $("#nvSec").value = "";
    };
    $("#dartSave").onclick = async () => {
      const k = $("#dartKey").value.trim();
      if (k) await run(() => api("PUT", "/api/settings", { dart: { api_key: k } }), "저장했어요");
      dartShow(await api("GET", "/api/dart?fresh=1"));
      $("#dartKey").value = "";
    };
    $("#aiSave").onclick = async () => { await run(() => api("PUT", "/api/settings", body()), "저장했어요"); this.ai(); };
    $("#aiTest").onclick = async () => {
      await run(() => api("PUT", "/api/settings", body()));
      const r = await run(() => api("POST", "/api/ai/test"));
      toast(`연결됐어요: "${r.reply}" ($${r.usd})`);
      this.ai();
    };
  },
  theme() {
    const cur = store.get("theme") || "neon";
    const curA = store.get("accent") || THEMES[cur].accent;
    const draw = (tid, aid) => {
      view.innerHTML = `
        <section class="card"><h2>글자 크기</h2>
          <div class="small muted">글자와 버튼, 여백이 함께 커지고 작아져요. 누르면 바로 바뀌어요.</div>
          <div class="fs-opts">${FONT_SIZES.map(([id, name, z]) => `<button type="button" class="fs-opt" data-fs="${id}" aria-pressed="${id === (store.get("fontSize") || "s")}">
            <span class="fs-a" style="font-size:${Math.round(13 * z)}px">가</span><span>${name}</span></button>`).join("")}</div>
          <div class="tiny">크게는 처음 버전과 같은 크기, 작게는 지금처럼 한 화면에 많이 보이는 크기예요.</div>
        </section>
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
        <section class="card"><h2>투자 화면</h2>
          <div class="small muted">투자 탭만 게임처럼 바꿔요. 입체 패널, 누르는 버튼, 금화색 숫자로 상태를 한눈에 보여 줘요. 기능은 같아요.</div>
          <div class="fs-opts">${[["1", "게임", "🎮"], ["0", "기본", "▤"]].map(([v, name, ic]) => `<button type="button" class="fs-opt" data-gm="${v}" aria-pressed="${(gameOn() ? "1" : "0") === v}">
            <span class="fs-a" aria-hidden="true">${ic}</span><span>${name}</span></button>`).join("")}</div>
        </section>
        <section class="card"><h2>미리 보기</h2>
          <div class="hero" style="font-size:16px">하루 <span class="hl">63,700원</span>씩 쓸 수 있어요</div>
          <div class="btns"><button type="button" class="btn dark">저장</button><button type="button" class="btn">취소</button></div>
          <div class="card dark" style="padding:11px"><div class="small muted">오늘 투자 손익</div><div class="mid">+38,400원</div></div>
        </section>
        <button class="btn soft wide" data-reset-theme>기본 색으로 되돌리기</button>
        <div class="tiny" style="text-align:center">글자 크기와 색상은 이 기기에만 저장돼요. 다른 기기는 따로 고를 수 있어요.</div>`;
    };
    let tid = cur, aid = curA;
    draw(tid, aid);
    view.onclick = (e) => {
      const fz = e.target.closest("[data-fs]"), gm = e.target.closest("[data-gm]");
      if (fz) { applyFontSize(fz.dataset.fs); draw(tid, aid); return; }
      if (gm) { store.set("gameInvest", gm.dataset.gm === "1" ? "" : "0"); draw(tid, aid); toast(gm.dataset.gm === "1" ? "투자 화면을 게임 테마로 바꿨어요" : "투자 화면을 기본으로 되돌렸어요"); return; }
      const t = e.target.closest("[data-theme]"), a = e.target.closest("[data-accent]"), r = e.target.closest("[data-reset-theme]");
      if (t) { tid = t.dataset.theme; aid = THEMES[tid].accent; }
      else if (a) aid = a.dataset.accent;
      else if (r) { tid = "neon"; aid = "magenta"; }
      else return;
      applyTheme(tid, aid);
      draw(tid, aid);
    };
  },
  async mount(h) {
    setTop("설정", { menu: "settings", sub: h.sub });
    if (h.sub === "all") return this.hub();
    if (h.sub === "theme") return this.theme();  // 색상은 서버 연결 없이도 바꿀 수 있게
    if (h.sub === "ai") return this.ai();
    if (h.sub === "security") return this.security();
    if (h.sub === "backup") return this.backup();
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
      return `<details class="card"><summary>${label} 계좌 <span class="tiny" style="margin-left:auto;margin-right:8.5px">${s.trade_mode[k] === "kiwoom" ? (kw.mock ? "키움 모의투자" : "키움 실전") : "앱 내 모의"}</span></summary>
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
            <span class="ico ${hue(b.bank)}">${esc(bankLabel(b.bank).slice(0, 2))}</span>
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
        ${kw.error ? `<div class="notice warn">${esc(kw.error)}${ipHelp(kw.error)}</div>` : ""}
        <label class="field">앱키<input data-kv="appkey" value="${esc(s.kiwoom_view.appkey)}" autocomplete="off" autocapitalize="off"></label>
        <label class="field">시크릿키<input data-kv="secretkey" type="password" value="${esc(s.kiwoom_view.secretkey)}" autocomplete="off"></label>
        <label class="check"><input type="checkbox" data-kv="mock" ${s.kiwoom_view.mock ? "checked" : ""}> 모의투자 계좌 키예요</label>
        <button class="btn sm" id="kvTest">저장하고 연결 확인</button>
      </section>
      <div class="sec-title">자동매매 주문 계좌</div>
      ${acct("scalp", "기본")}
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
        <div class="tiny">본인 증권사 수수료와 올해 거래세율로 맞춰 주세요. 실제 손익 계산에 쓰여요.</div></section>
      <section class="card"><h2>적립 매수 시간·휴장일</h2>
        <div class="fields"><label class="field">매수 시작<input id="dwS" value="${esc(s.dca_window.start)}"></label><label class="field">매수 마감<input id="dwE" value="${esc(s.dca_window.end)}"></label></div>
        <label class="field">휴장일(YYYY-MM-DD, 줄마다)<textarea id="hol" rows="4">${esc(s.holidays.join("\n"))}</textarea></label>
        <div class="tiny">주말은 자동으로 빼요.</div></section>
      <section class="card"><h2>휴대폰 알림</h2>
        <label class="field">ntfy 토픽<input id="ntfy" value="${esc(s.ntfy_topic)}" autocapitalize="off" placeholder="비우면 알림 안 보냄"></label>
        <div class="tiny">ntfy 앱에서 같은 토픽을 구독하면 꼭 알아야 할 것만 골라 보내요. 토픽을 바꿨으면 먼저 아래 "설정 저장"을 눌러 주세요.</div>
        <div id="nfBox"></div></section>
      <div id="setErr"></div>
      <button class="btn dark wide" id="saveSet">설정 저장</button>`;
    const appHtml = `
      <section class="card" id="upCard">${updateCard(up)}</section>
      <section class="card"><h2>버전</h2>
        <div style="display:flex;justify-content:space-between;font-size:11px"><span>앱</span><b>v${esc(ping.version || "-")}</b></div>
        <div style="display:flex;justify-content:space-between;font-size:11px"><span class="muted">화면 빌드</span><span class="muted">${esc(UI_VERSION)}</span></div>
        <div style="display:flex;justify-content:space-between;font-size:11px"><span class="muted">서버 빌드</span><span class="muted">${esc(ping.build || "-")}</span></div>
      </section>`;
    view.innerHTML = sub === "accounts" ? accountsHtml : sub === "trading" ? tradingHtml : sub === "app" ? appHtml : connectCard;
    if ($("#kwCard")) bindKwCard($("#kwCard"));
    $("#bkAdd")?.addEventListener("click", () => bankSheet(null, route));
    $$("[data-bk]").forEach((el) => el.addEventListener("click", () => bankSheet(con.banks.find((b) => b.id == el.dataset.bk), route)));
    const errs = (e) => { $("#setErr").innerHTML = Object.entries(e || {}).map(([k, v]) => `<div class="notice warn">${esc({ scalp: "기본 계좌", dca: "장기투자 계좌", feed: "시세" }[k] || k)}: ${esc(v)}</div>`).join(""); };
    // 지금 화면에 있는 입력란만 모아서 저장한다(다른 2뎁스 화면의 값은 건드리지 않음)
    const collect = () => {
      const out = {};
      if ($("[data-kw]")) {
        const kiwoom = { scalp: {}, dca: {} };
        $$("[data-kw]").forEach((i) => { kiwoom[i.dataset.kw][i.dataset.k] = i.type === "checkbox" ? i.checked : i.value.trim(); });
        const real = {}; $$("[data-real]").forEach((i) => (real[i.dataset.real] = i.checked));
        const cash = {}; $$("[data-cash]").forEach((i) => (cash[i.dataset.cash] = num(i.value)));
        out.kiwoom = kiwoom; out.real_orders_allowed = real; out.paper_start_cash = cash;
        out.trade_mode = { scalp: segVal(view, "기본 주문 방식"), dca: segVal(view, "장기투자 주문 방식") };
      }
      if ($("[data-kv]")) { const kv = {}; $$("[data-kv]").forEach((i) => (kv[i.dataset.kv] = i.type === "checkbox" ? i.checked : i.value.trim())); out.kiwoom_view = kv; }
      if (segVal(view, "시세 출처")) { out.data_source = segVal(view, "시세 출처"); out.demo_speed = num($("#demoSpeed").value) || 2; }
      if ($("#cCom")) out.costs = { commission: num($("#cCom").value) / 100, tax: num($("#cTax").value) / 100, slippage_ticks: num($("#cSlip").value) };
      if ($("#dwS")) {
        out.dca_window = { start: $("#dwS").value.trim(), end: $("#dwE").value.trim() };
        out.holidays = $("#hol").value.split(/\s+/).filter((x) => /^\d{4}-\d\d-\d\d$/.test(x));
      }
      if ($("#ntfy")) out.ntfy_topic = $("#ntfy").value.trim();
      if ($("[data-nf]")) { const nf = {}; $$("[data-nf]").forEach((b) => (nf[b.dataset.nf] = b.getAttribute("aria-checked") === "true")); out.notify = nf; }
      if ($("#pubUrl")) out.public_url = $("#pubUrl").value.trim().replace(/\/+$/, "");
      return out;
    };
    ["시세 출처", "기본 주문 방식", "장기투자 주문 방식"].forEach((n) => bindSeg(view, n, () => {}));
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
    if ($("#nfBox")) this.notifyBox($("#nfBox"));
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
      // 새 서비스워커가 자리 잡은 뒤에 넘어간다(도중에 새로 고치면 아이폰에서 빈 화면이 될 수 있음)
      try {
        const reg = await navigator.serviceWorker?.getRegistration();
        if (reg) {
          await Promise.race([reg.update(), new Promise((r) => setTimeout(r, 4000))]);
          const w = reg.installing || reg.waiting;
          if (w) await Promise.race([new Promise((r) => { w.addEventListener("statechange", () => { if (w.state === "activated" || w.state === "redundant") r(); }); }), new Promise((r) => setTimeout(r, 5000))]);
        }
      } catch (e) { /* 무시 */ }
      await new Promise((r) => setTimeout(r, 300));
      location.replace(`${location.pathname}?u=${Date.now()}${location.hash}`);
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
function startApp() {
  if (startApp.done) return;
  startApp.done = true;
  store.set("skinSecs", JSON.stringify(["world"].concat(...APPS.filter((a) => a.skin).map((a) => a.secs))));
  if (ON_PAGES && !store.get("server") && !location.hash.startsWith("#connect")) location.hash = "#connect";
  // 서버 연결 화면이 아니면, 먼저 잠금 상태부터 확인하고 화면을 그린다(잠겨 있으면 저장해 둔 자료도 보여 주지 않는다)
  if ((ON_PAGES && !store.get("server")) || location.hash.startsWith("#connect")) route();
  else {
    if (sess.get()) view.innerHTML = `<div class="empty loading">불러오는 중</div>`; else lock.show("check");
    lock.verify();
  }
}
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", startApp); else setTimeout(startApp, 0);
window.__moaReady = true;
try { sessionStorage.removeItem("moa.retry"); } catch (e) { /* 무시 */ }
if (/[?&]u=\d+/.test(location.search)) history.replaceState(null, "", location.pathname + location.hash);  // 주소의 임시 꼬리표 지우기
