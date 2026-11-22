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
        <section class="wd-todo" id="wdTodo" hidden></section>
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
      this.todo().catch(() => {});
    },
    // 챙길 일: 앱들이 아는 날짜 있는 일을 모아 보여 주고, 한 줄씩 AI 비서(피클 등)에 맡길 문구로 넘긴다
    async todo() {
      const box = $("#wdTodo");
      if (!box) return;
      const d = await GET("/api/agenda");
      const hand = async (text) => {
        if (!text) return;
        try { if (navigator.share) { await navigator.share({ text }); return; } } catch (e) { if (e && e.name === "AbortError") return; }
        try { await navigator.clipboard.writeText(text); toast("복사했어요. 피클에 붙여 넣으세요"); } catch (e) { toast("복사하지 못했어요", true); }
      };
      box.hidden = false;
      box.innerHTML = `<div class="wd-th"><h2>챙길 일</h2>
          ${d.share ? `<button type="button" class="wd-tb" id="wdAll">오늘·내일 맡기기</button>` : ""}<button type="button" class="wd-tb ghost" id="wdCal">캘린더 연결</button></div>
        ${d.items.length ? `<ul class="wd-tl">${d.items.map((x, i) => `<li class="${x.dday === 0 ? "now" : ""}"><time>${esc(x.when)}</time><span><b>${esc(x.title)}</b>${x.note ? `<small>${esc(x.note)}</small>` : ""}</span>
            ${x.ask ? `<button type="button" class="wd-go" data-i="${i}" aria-label="${esc(x.title)} 맡기기">맡기기</button>` : ""}</li>`).join("")}</ul>${d.more ? `<p class="wd-tm">그 밖에 ${d.more}건은 캘린더에서 볼 수 있어요</p>` : ""}`
          : `<p class="wd-tm">앞으로 7일 동안 챙길 일이 없어요</p>`}`;
      box.onclick = (e) => {
        const b = e.target.closest("[data-i]");
        if (b) return hand(d.items[Number(b.dataset.i)].ask);
        if (e.target.closest("#wdAll")) return hand(d.share);
        if (e.target.closest("#wdCal")) this.calSheet(d);
      };
    },
    calSheet(d) {
      const base = (typeof apiBase === "function" ? apiBase() : "") || location.origin;
      const url = () => base.replace(/\/$/, "") + d.path, cal = () => url().replace(/^https?:/, "webcal:");
      const draw = () => `<div class="wd-cs">
          <p class="small">아이폰 캘린더에 구독해 두면 납부일·운동·투자 알림이 캘린더에 들어가요. 휴대폰 캘린더를 읽는 AI 비서(피클 등)가 이 일정도 함께 챙길 수 있어요.</p>
          <a class="btn dark wide" id="wdSub" href="${esc(cal())}">아이폰 캘린더에 구독하기</a>
          <button type="button" class="btn wide" id="wdCopy">주소 복사</button>
          <div class="sec-title">캘린더에 넣을 것</div>
          ${Object.entries(d.kinds).map(([k, v]) => `<label class="check"><input type="checkbox" data-k="${k}" ${d.conf[k] ? "checked" : ""}> ${esc(v)}</label>`).join("")}
          <label class="check"><input type="checkbox" data-k="amount" ${d.conf.amount ? "checked" : ""}> 금액도 함께 넣기</label>
          <p class="tiny">이 주소는 읽기 전용이고 앱 접속 토큰과 다른 값이에요. 주소를 아는 사람은 위 일정을 볼 수 있으니, 새어 나갔다 싶으면 새로 만드세요. 금액과 건강 일정은 기본으로 빼 뒀어요. 노트북과 같은 Tailscale에 연결된 기기에서만 열려요. 그래서 구독할 때 저장 위치를 iCloud가 아니라 '나의 iPhone'으로 골라 주세요(iCloud는 애플 서버가 대신 받아 와서 이 주소에 닿지 못해요).</p>
          <button type="button" class="btn wide" id="wdRenew">주소 새로 만들기(예전 주소 끊기)</button></div>`;
      const mount = (el) => {
        el.querySelectorAll("[data-k]").forEach((c) => { c.onchange = async () => { d = await api("PUT", "/api/agenda", { [c.dataset.k]: c.checked }); this.todo(); }; });
        $("#wdCopy").onclick = async () => { try { await navigator.clipboard.writeText(url()); toast("주소를 복사했어요"); } catch (e) { toast("복사하지 못했어요", true); } };
        $("#wdRenew").onclick = async () => { d = await api("PUT", "/api/agenda", { renew: true }); $("#wdSub").href = cal(); toast("새 주소를 만들었어요. 캘린더에서 다시 구독해 주세요"); };
      };
      openSheet("캘린더 연결", draw(), mount);
    },
  };
})();
