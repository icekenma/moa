"use strict";
/* 우석World 첫 화면: 내가 만든 앱들이 모이는 곳. 앱은 app.js의 addApp()으로 등록한 순서대로 타일이 된다. */
(function () {
  const GLOBE = `<svg viewBox="0 0 200 200" fill="none" stroke="currentColor" stroke-width="1.2" aria-hidden="true">
    <circle cx="100" cy="100" r="92"/><ellipse cx="100" cy="100" rx="92" ry="34"/><ellipse cx="100" cy="100" rx="92" ry="66"/>
    <ellipse cx="100" cy="100" rx="34" ry="92"/><ellipse cx="100" cy="100" rx="66" ry="92"/><path d="M8 100h184M100 8v184"/></svg>`;
  const GEAR = `<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg>`;
  const DOW = ["일", "월", "화", "수", "목", "금", "토"];

  // 가계부 타일: 이번 달 지출과 자동매매 상태, 오른쪽 그림은 막대 + 오르는 선
  const ledger = APPS.find((a) => a.id === "ledger");
  ledger.live = (d) => (d ? [`${d.month}월 지출 ${won(d.expense)}원`, d.auto ? `자동매매 ${d.auto}개 가동 중` : ""] : []);
  ledger.art = () => `<svg viewBox="0 0 120 120" fill="none" aria-hidden="true">
    <rect x="14" y="70" width="16" height="36" rx="4" fill="currentColor" opacity=".28"/><rect x="38" y="54" width="16" height="52" rx="4" fill="currentColor" opacity=".42"/>
    <rect x="62" y="62" width="16" height="44" rx="4" fill="currentColor" opacity=".58"/><rect x="86" y="34" width="16" height="72" rx="4" fill="currentColor"/>
    <path d="M14 46l26-14 22 10 34-26" stroke="#fff" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/><circle cx="96" cy="16" r="5" fill="#fff"/></svg>`;

  const tile = (a, d) => {
    const t = a.tile || {}, lines = (a.live ? a.live(d) : []).filter(Boolean);
    return `<a class="wd-app" href="${a.home}" data-app="${a.id}" style="--tb:${t.bg || "#10243A"};--ti:${t.ink || "#fff"};--ts:${t.sub || "#C5CEDB"};--ta:${t.accent || "#FFD84D"}">
      <span class="wd-art">${a.art ? a.art(d) : ""}</span>
      <span class="wd-name">${esc(a.name)}</span>
      <span class="wd-desc">${esc(a.desc || "")}</span>
      <span class="wd-live">${lines.length ? lines.map((x, i) => `<span class="${i ? "dim" : ""}">${esc(x)}</span>`).join("") : `<span class="dim">열기</span>`}</span>
    </a>`;
  };

  pages.world = {
    every: 60000,
    async mount() {
      setTop("우석World", {});
      const now = new Date(), live = memo.get("world") || {};
      view.innerHTML = `<div class="wd">
        <header class="wd-head">
          <div class="wd-globe">${GLOBE}</div>
          <a class="wd-set" href="#settings" aria-label="설정">${GEAR}</a>
          <p class="wd-date">${now.getMonth() + 1}월 ${now.getDate()}일 ${DOW[now.getDay()]}요일</p>
          <h1 class="wd-mark">우석World</h1>
        </header>
        <nav class="wd-apps" id="wdApps" aria-label="앱" ${APPS.length > 4 ? "data-many" : ""}>${APPS.map((a) => tile(a, live[a.id])).join("")}</nav>
        <p class="wd-foot" id="wdFoot">앱 ${APPS.length}개${live.version ? ` · v${esc(live.version)}` : ""}</p>
      </div>`;
      this.refresh().catch(() => {});
    },
    async refresh() {
      let d;
      try { d = await GET("/api/world"); }
      catch (e) { if (e instanceof Locked || e instanceof NeedConnect) throw e; return; }   // 예전 서버: 타일만 보여 준다
      const host = $("#wdApps");
      if (!host) return;
      memo.set("world", Object.assign({ version: d.version }, d.apps));
      host.innerHTML = APPS.map((a) => tile(a, (d.apps || {})[a.id])).join("");
      if (d.version && $("#wdFoot")) $("#wdFoot").textContent = `앱 ${APPS.length}개 · v${d.version}`;
    },
  };
})();
