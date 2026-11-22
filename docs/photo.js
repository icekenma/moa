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
  function guess() {              // 프로그램이 주소를 안 알려 줄 때(잠금 번호 사용 등): 우석World 서버와 같은 컴퓨터의 기본 https 주소
    try { const u = new URL(apiBase() || location.href); return u.protocol === "https:" && u.hostname.endsWith(".ts.net") ? "https://" + u.hostname : ""; } catch (e) { return ""; }
  }
  const pickUrl = (d) => [d.url].concat(d.urls, [guess()]).find(usable) || "";

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
    else if (!url) host.innerHTML = notice("접속 주소를 찾지 못했어요", [secure ? "휴대폰에서 쓰려면 https 주소가 필요해요. 노트북의 Tailscale이 켜져 있으면 프로그램이 https 주소를 자동으로 만듭니다." : "프로그램이 알려 준 주소가 없어요.", "평소 앨범 앱을 여는 주소를 '주소 직접 넣기'에 넣어도 됩니다."], d, "");
    else if (d.pin && !store.get("photoFrame", false)) host.innerHTML = notice("잠금 번호를 쓰고 있어요", ["프로그램에 잠금 번호(PIN)가 걸려 있으면 우석World 안에 끼워 넣은 화면에서는 로그인이 유지되지 않아요.", "'프로그램 열기'로 따로 열어 쓰거나, 프로그램 설정에서 잠금 번호를 끄면 이 자리에 바로 뜹니다."], d, url)
      + `<p class="px-dim px-pad"><button type="button" class="px-link" data-a="force">그래도 여기에 띄워 보기</button></p>`;
    else host.innerHTML = `<div class="px-bar"><a class="px-ico" href="#world" aria-label="우석World 첫 화면으로">🌐</a><b>사진편집</b><span>${esc(d.name)} v${esc(d.version)}</span>
        <button type="button" class="px-ico" data-a="reload" aria-label="새로 고침">↻</button><a class="px-ico" href="${esc(url)}" target="_blank" rel="noopener" aria-label="따로 열기">↗</a><button type="button" class="px-ico" data-a="addr" aria-label="주소 바꾸기">⋯</button></div>
      <iframe class="px-frame" id="pxFrame" src="${esc(url)}" title="PhotoGrader 웹 프로그램" allow="fullscreen; web-share; clipboard-write; autoplay; geolocation; camera; microphone; screen-wake-lock" allowfullscreen></iframe>`;
  }
  async function load() { const d = await GET("/api/photo/app"); draw(d); return d; }
  function addrSheet(d) {
    openSheet("프로그램 주소", `<label class="field">PhotoGrader 웹 프로그램 주소<input id="pxUrl" type="url" inputmode="url" autocomplete="off" placeholder="https://내컴퓨터.ts.net" value="${esc(d.url || "")}"></label>
      <div class="tiny">비워 두면 자동으로 찾아요.${d.urls.length ? ` 프로그램이 알려 준 주소: ${d.urls.map(esc).join(", ")}` : ""}</div><button type="button" class="btn dark wide" id="pxSave">저장</button>`, (root) => {
      $("#pxSave", root).onclick = async () => {
        try { const n = await api("PUT", "/api/photo/app", { url: $("#pxUrl", root).value }); Object.assign(d, n); draw(d); closeSheet(); } catch (err) { if (!(err instanceof Locked)) toast(err.message, true); }
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
          if (b.dataset.a === "retry") d = await load();
          if (b.dataset.a === "reload") { const f = $("#pxFrame"); if (f) f.src = f.getAttribute("src"); }
          if (b.dataset.a === "force") { store.set("photoFrame", true); draw(d); }
          if (b.dataset.a === "addr") addrSheet(d);
        } catch (err) { if (!(err instanceof Locked)) toast(err.message, true); }
      });
    },
  };
})();
