"use strict";
/* 사진편집(우석World) = 노트북에서 도는 PhotoGrader 웹 프로그램(AWS Album)을 그대로 띄운다.
   화면·기능·업데이트는 전부 그 프로그램의 것이고(판매용 코드라 이 저장소에는 없다), 여기서는 주소를 찾아 틀 안에 여는 일만 한다. */
(function () {
  addApp({
    id: "photo", name: "사진편집", desc: "PhotoGrader 앨범·보정·영상", home: "#photo", secs: ["photo"],
    skin: { meta: "#0B0C0E", vars: { "--deep": "#1C2128", "--deep2": "#2A3038", "--bg": "#0B0C0E", "--hl": "#0F8A6E", "--solid": "#1C2128",
      "--on-deep": "#EDEFF2", "--on-deep2": "#A9B1BC", "--on-deep-strong": "#FFFFFF", "--sub-bg": "rgba(255,255,255,.08)" } },
    tile: { bg: "linear-gradient(150deg,#0F8A6E 0%,#1C2128 78%)", ink: "#FFFFFF", sub: "#CFE5DE", accent: "#7FE3C8" },
    live: (d) => (d ? [`사진 ${d.count.toLocaleString()}장`, d.best ? `베스트컷 ${d.best}장` : ""] : ["노트북에서 프로그램이 꺼져 있어요"]),
    art: () => `<svg viewBox="0 0 120 120" fill="none" aria-hidden="true"><circle cx="60" cy="60" r="44" stroke="currentColor" stroke-width="8"/>
      <path d="M60 16l22 38M98 38l-44 0M104 74l-22-38M60 104L38 66M22 82h44M16 46l22 38" stroke="#fff" stroke-width="3" stroke-linecap="round"/></svg>`,
  });
  const secure = location.protocol === "https:";
  const usable = (u) => !!u && (!secure || u.startsWith("https://") || /^http:\/\/(127\.0\.0\.1|localhost)[:/]/.test(u + "/"));
  // 앱 껍데기(index.html)가 예전 것이면 다른 주소의 화면을 끼워 넣는 것이 막혀 있다(업데이트 직후 몇 분 동안 그럴 수 있다) → 빈 화면 대신 안내
  const frameBlocked = () => { const m = document.querySelector('meta[http-equiv="Content-Security-Policy"]'); return !!m && /frame-src\s+'none'/.test(m.content); };
  const pickUrl = (d) => [d.url].concat(d.urls).find(usable) || "";
  // 휴대폰(이 기기)에서 그 주소에 닿는지: 내용은 못 읽어도 연결이 되는지는 알 수 있다
  async function reach(url) {
    const c = new AbortController(), t = setTimeout(() => c.abort(), 7000);
    try { await fetch(url + "/api/version", { mode: "no-cors", cache: "no-store", signal: c.signal }); return true; } catch (e) { return false; } finally { clearTimeout(t); }
  }
  const WHAT = { album: "프로그램 맞음", serve: "Tailscale에 연결돼 있음", other: "다른 서버(우석World 등)", fail: "응답 없음" };
  const tried = (d) => (d.checked.length ? `<p class="px-dim">확인한 주소: ${d.checked.map((x) => `${esc(x.url.replace("https://", ""))} → ${WHAT[x.what] || x.what}`).join(" · ")}</p>` : "");

  function notice(title, lines, d, url) {
    return `<section class="px-note"><a class="px-home" href="#world">🌐 우석World</a><h1>${title}</h1>${lines.map((t) => `<p>${t}</p>`).join("")}
      <div class="px-row">${url ? `<a class="px-btn main" href="${esc(url)}" target="_blank" rel="noopener">프로그램 열기</a>` : ""}<button type="button" class="px-btn" data-a="retry">다시 확인</button><button type="button" class="px-btn" data-a="addr">주소 직접 넣기</button></div>
      ${d.running ? `<p class="px-dim">${esc(d.name)} v${esc(d.version)} · 노트북 ${d.port}번 포트에서 실행 중</p>` : ""}</section>`;
  }
  function draw(d) {
    const host = $("#pxApp");
    if (!host) return;
    const url = pickUrl(d);
    if (!d.running) host.innerHTML = notice("프로그램이 꺼져 있어요", ["노트북에서 PhotoGrader 웹 프로그램(AWS Album)을 켜 주세요. 프로그램 폴더의 <b>run_album.bat</b>을 실행하면 됩니다.", "노트북을 켤 때 같이 켜지게 하려면 <b>autostart_album_on.bat</b>을 한 번 실행해 두세요."], d, "");
    else if (!url) host.innerHTML = notice("프로그램으로 가는 https 주소가 없어요", d.can_serve
      ? ["우석World 안에 띄우려면 프로그램 전용 https 주소가 필요한데, 지금 노트북의 https 주소는 다른 서버(우석World)에 연결돼 있어요.", "'https 주소 만들기'를 누르면 노트북의 Tailscale에 프로그램용 주소를 하나 더 만듭니다. 이미 있는 연결은 건드리지 않아요."]
      : [d.tailscale ? "프로그램으로 가는 https 주소를 찾지 못했어요." : "노트북의 Tailscale 정보를 읽지 못했어요. Tailscale이 켜져 있는지 확인해 주세요.", "프로그램 전용 https 주소를 알고 있으면 '주소 직접 넣기'에 넣어도 됩니다."], d, "")
      .replace('<div class="px-row">', `<div class="px-row">${d.can_serve ? '<button type="button" class="px-btn main" data-a="https">https 주소 만들기</button>' : ""}`) + `<div class="px-pad">${tried(d)}</div>`;
    else if (P.unreach === url) host.innerHTML = notice("이 기기에서 프로그램에 닿지 않아요", [`주소 <b>${esc(url.replace("https://", ""))}</b> 에 연결되지 않았어요.`, "휴대폰의 Tailscale 앱이 Connected 인지 확인하고 '다시 확인'을 눌러 주세요."], d, url) + `<div class="px-pad">${tried(d)}</div>`;
    else if (frameBlocked() || P.blocked) host.innerHTML = notice("앱을 새 버전으로 바꾸는 중이에요", ["우석World가 방금 업데이트돼서, 프로그램 화면을 띄우는 새 설정이 아직 이 휴대폰에 내려오지 않았어요.", "'새 버전 받기'를 누르거나 앱을 완전히 닫았다가 다시 열어 주세요. 몇 분 뒤에도 같으면 '프로그램 열기'로 따로 열 수 있어요."], d, url)
      .replace('<button type="button" class="px-btn" data-a="retry">다시 확인</button>', '<button type="button" class="px-btn main" data-a="fresh">새 버전 받기</button>');
    else if (d.pin && !store.get("photoFrame", false)) host.innerHTML = notice("잠금 번호를 쓰고 있어요", ["프로그램에 잠금 번호(PIN)가 걸려 있으면 우석World 안에 끼워 넣은 화면에서는 로그인이 유지되지 않아요.", "'프로그램 열기'로 따로 열어 쓰거나, 프로그램 설정에서 잠금 번호를 끄면 이 자리에 바로 뜹니다."], d, url)
      + `<p class="px-dim px-pad"><button type="button" class="px-link" data-a="force">그래도 여기에 띄워 보기</button></p>`;
    else host.innerHTML = `<div class="px-bar"><a class="px-ico" href="#world" aria-label="우석World 첫 화면으로">🌐</a><b>사진편집</b><span>${esc(d.name)} v${esc(d.version)}</span>
        <button type="button" class="px-ico" data-a="reload" aria-label="새로 고침">↻</button><a class="px-ico" href="${esc(url)}" target="_blank" rel="noopener" aria-label="따로 열기">↗</a><button type="button" class="px-ico" data-a="addr" aria-label="주소 바꾸기">⋯</button></div>
      <iframe class="px-frame" id="pxFrame" src="${esc(url)}" title="PhotoGrader 웹 프로그램" allow="fullscreen; web-share; clipboard-write; autoplay; geolocation; camera; microphone; screen-wake-lock" allowfullscreen></iframe>`;
  }
  const P = { blocked: false, d: null, unreach: "" };
  document.addEventListener("securitypolicyviolation", (e) => {      // 그래도 막혔으면(예전 껍데기) 빈 화면으로 두지 않는다
    if (!/^frame-src/.test(e.violatedDirective || e.effectiveDirective || "") || !$("#pxFrame")) return;
    P.blocked = true; if (P.d) draw(P.d);
  });
  async function fresh() {         // 저장해 둔 화면 파일을 버리고 새로 받는다
    try { if (window.caches) { const ks = await window.caches.keys(); await Promise.all(ks.map((k) => window.caches.delete(k))); } } catch (e) { /* 그냥 새로 고침 */ }
    try { const r = navigator.serviceWorker && await navigator.serviceWorker.getRegistration(); if (r) await r.update(); } catch (e) { /* 그냥 새로 고침 */ }
    location.replace(location.pathname + "?u=" + Date.now() + "#photo");
  }
  async function load(fresh) {
    const host = $("#pxApp");
    if (host && !host.firstChild) host.innerHTML = `<section class="px-note"><p class="px-dim">프로그램 주소를 확인하는 중…</p></section>`;
    const d = await GET("/api/photo/app" + (fresh ? "?fresh=1" : "")), url = pickUrl(d);
    P.d = d; P.unreach = d.running && url && !(await reach(url)) ? url : "";
    draw(d); return d;
  }
  function addrSheet(d) {
    openSheet("프로그램 주소", `<label class="field">PhotoGrader 웹 프로그램 주소<input id="pxUrl" type="url" inputmode="url" autocomplete="off" placeholder="https://내컴퓨터.ts.net" value="${esc(d.url || "")}"></label>
      <div class="tiny">비워 두면 자동으로 찾아요.${d.urls.length ? ` 지금 쓰는 주소: ${esc(pickUrl(d))}` : ""}</div><button type="button" class="btn dark wide" id="pxSave">저장</button>`, (root) => {
      $("#pxSave", root).onclick = async () => {
        try { await api("PUT", "/api/photo/app", { url: $("#pxUrl", root).value }); closeSheet(); Object.assign(d, await load(true)); } catch (err) { if (!(err instanceof Locked)) toast(err.message, true); }
      };
    });
  }
  pages.photo = {
    async mount() {
      setTop("사진편집", {});
      view.innerHTML = `<div class="px-app" id="pxApp"></div>`;
      let d = await load();
      $("#pxApp").addEventListener("click", async (e) => {
        const b = e.target.closest("[data-a]");
        if (!b) return;
        try {
          if (b.dataset.a === "retry") { b.disabled = true; d = await load(true); }
          if (b.dataset.a === "https") { b.disabled = true; b.textContent = "만드는 중…"; try { await api("POST", "/api/photo/app/https", {}); } finally { d = await load(true); } }
          if (b.dataset.a === "fresh") return fresh();
          if (b.dataset.a === "reload") { const f = $("#pxFrame"); if (f) f.src = f.getAttribute("src"); }
          if (b.dataset.a === "force") { store.set("photoFrame", true); draw(d); }
          if (b.dataset.a === "addr") addrSheet(d);
        } catch (err) { if (!(err instanceof Locked)) toast(err.message, true); }
      });
    },
  };
})();
