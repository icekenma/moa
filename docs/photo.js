"use strict";
/* 사진편집 앱(우석World). 화면만 여기 있고, 보정 계산과 사진 보관은 노트북 서버(/api/photo)가 한다.
   보관함(#photo) → 사진 하나를 누르면 편집기(#photo/edit?id=…). */
(function () {
  const svg = (p, s = 20, w = 2) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${w}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${p}</svg>`;
  const IC = {
    globe: svg('<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.8 3 2.8 15 0 18M12 3c-2.8 3-2.8 15 0 18"/>', 20, 1.8),
    back: svg('<path d="M15 5l-7 7 7 7"/>', 22, 2.2), undo: svg('<path d="M9 7L4 12l5 5"/><path d="M4 12h10a6 6 0 0 1 0 12h-2"/>', 20), check: svg('<path d="M5 12l5 5L19 7"/>', 16, 3),
    plus: svg('<path d="M12 5v14M5 12h14"/>', 20, 2.4), rotL: svg('<path d="M4 9a8 8 0 1 1 2 8"/><path d="M4 4v5h5"/>', 20), rotR: svg('<path d="M20 9a8 8 0 1 0-2 8"/><path d="M20 4v5h-5"/>', 20),
    flip: svg('<path d="M12 3v18M8 7l-5 5 5 5M16 7l5 5-5 5"/>', 20), spark: svg('<path d="M12 3l2 6 6 2-6 2-2 6-2-6-6-2 6-2z"/>', 18),
  };
  addApp({
    id: "photo", name: "사진편집", desc: "보정, 자르기, AI", home: "#photo", secs: ["photo"],
    skin: { dark: true, meta: "#101010", vars: { "--deep": "#101010", "--deep2": "#1C1C1C", "--bg": "#101010", "--hl": "#19B892", "--solid": "#101010",
      "--on-deep": "#EDEDED", "--on-deep2": "#A8A8A8", "--on-deep-strong": "#FFFFFF", "--sub-bg": "rgba(255,255,255,.08)" } },
    tile: { bg: "linear-gradient(150deg,#0F6F5A 0%,#101010 72%)", ink: "#FFFFFF", sub: "#BFD3CC", accent: "#3FE0B5" },
    live: (d) => (d ? [`사진 ${d.count}장`, d.edited ? `보정 ${d.edited}장` : ""] : []),
    art: () => `<svg viewBox="0 0 120 120" fill="none" aria-hidden="true"><circle cx="60" cy="60" r="44" stroke="currentColor" stroke-width="8"/>
      <path d="M60 16l22 38M98 38l-44 0M104 74l-22-38M60 104L38 66M22 82h44M16 46l22 38" stroke="#fff" stroke-width="3" stroke-linecap="round"/></svg>`,
  });

  // ---------- 서버와 주고받기(사진은 JSON이 아니라서 따로) ----------
  async function call(method, url, body, form) {
    if (lock.on) throw new Locked("잠겨 있어요");
    let res;
    try { res = await fetch(apiBase() + url, { method, headers: authHeaders(form || body == null ? {} : { "Content-Type": "application/json" }), body: form ? body : body == null ? undefined : JSON.stringify(body) }); }
    catch (e) { throw new Error("노트북 서버에 연결하지 못했어요"); }
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      if (res.status === 401 && (d.code === "locked" || d.code === "setup")) { lock.show("unlock"); throw new Locked(d.error || "잠겨 있어요"); }
      throw new Error(d.error || `요청이 실패했어요 (${res.status})`);
    }
    return res;
  }
  const thumbs = new Map();
  function thumbUrl(p) {
    const key = `${p.id}|${p.ver}`;
    if (!thumbs.has(key)) thumbs.set(key, call("GET", `/api/photo/${p.id}/thumb`).then((r) => r.blob()).then((b) => URL.createObjectURL(b)).catch((e) => { thumbs.delete(key); throw e; }));
    return thumbs.get(key);
  }
  async function shareFile(blob, name) {
    const file = new window.File([blob], name, { type: blob.type || "image/jpeg" });
    if (navigator.canShare && navigator.canShare({ files: [file] })) {
      try { await navigator.share({ files: [file] }); return; } catch (e) { if (e && e.name === "AbortError") return; }
    }
    const a = document.createElement("a"), u = URL.createObjectURL(blob);
    a.href = u; a.download = name; document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(u), 60000);
    toast("내려받았어요");
  }
  const fileName = (res, fallback) => { try { return decodeURIComponent(res.headers.get("X-File-Name") || "") || fallback; } catch (e) { return fallback; } };

  const P = { d: null, picking: false, sel: new Set(), ed: null, poll: 0 };

  // =====================================================================
  // 보관함
  // =====================================================================
  function library() {
    const d = P.d, st = d.status, n = d.photos.length, ed = d.photos.filter((p) => p.edited).length;
    const head = `<header class="pe-head"><div><p>${n ? `${n}장${ed ? ` · 보정 ${ed}장` : ""}` : "노트북에서 보정하고 휴대폰에 저장해요"}</p><h1>사진편집</h1></div>
      <div class="pe-head-r">${n ? `<button type="button" class="pe-btn ghost" data-act="pick">${P.picking ? "취소" : "선택"}</button>` : ""}
      <a class="pe-ico" href="#world" aria-label="우석World 첫 화면으로">${IC.globe}</a></div></header>`;
    if (!st.ready) {
      return `${head}<section class="pe-setup"><h2>보정 엔진을 먼저 설치해요</h2>
        <p>사진 보정 계산에 쓰는 프로그램(${esc(st.missing.join(", "))})을 노트북에 한 번 설치해야 해요. 노트북이 인터넷에 연결돼 있으면 1~3분 걸려요.</p>
        ${st.setup.error ? `<p class="pe-err">${esc(st.setup.error)}</p>` : ""}
        ${st.setup.running ? `<p class="pe-run"><i></i>${esc(st.setup.log.slice(-1)[0] || "설치 중")}</p>` : `<button type="button" class="pe-btn main wide" data-act="setup">보정 엔진 설치</button>`}</section>`;
    }
    const cells = d.photos.map((p) => `<button type="button" class="pe-cell ${P.sel.has(p.id) ? "on" : ""}" data-id="${p.id}" aria-label="${esc(p.name)}${p.edited ? ", 보정함" : ""}">
      <img data-th="${p.id}" alt="">${p.raw ? `<em>RAW</em>` : ""}${p.edited ? `<i class="pe-dot" title="보정함"></i>` : ""}${P.picking ? `<span class="pe-ck">${P.sel.has(p.id) ? IC.check : ""}</span>` : ""}</button>`).join("");
    const bar = P.picking
      ? `<div class="pe-bar"><button type="button" class="pe-btn main" data-act="exportSel" ${P.sel.size ? "" : "disabled"}>${P.sel.size}장 내보내기</button>
          <button type="button" class="pe-btn" data-act="slide" ${P.sel.size >= 2 ? "" : "disabled"}>슬라이드쇼</button>
          <button type="button" class="pe-btn danger" data-act="delSel" ${P.sel.size ? "" : "disabled"}>지우기</button></div>`
      : `<div class="pe-bar"><button type="button" class="pe-btn main wide" data-act="add">${IC.plus}사진 추가</button></div>`;
    return `${head}${n ? `<div class="pe-grid">${cells}</div>` : `<section class="pe-none"><b>아직 사진이 없어요</b><span>사진을 추가하면 노트북으로 올라가요. 원본은 휴대폰에 그대로 남아요.</span></section>`}
      ${st.raw && st.video ? "" : `<p class="pe-note">${[st.raw ? "" : "RAW 사진(CR2·NEF 등) 열기", st.video ? "" : "슬라이드쇼 영상 만들기"].filter(Boolean).join(", ")}에는 추가 도구가 필요해요.
        ${st.setup.running ? `<span class="pe-run"><i></i>${esc(st.setup.log.slice(-1)[0] || "설치 중")}</span>` : `<button type="button" class="pe-link" data-act="setup">추가 도구 설치</button>`}</p>`}
      <p class="pe-note">올린 사진은 긴 변 ${st.max_side}px까지로 줄여서 보관해요.</p>${bar}`;
  }
  function drawLib() {
    const host = $("#peApp");
    if (!host || P.ed) return;
    host.className = "pe-app";
    host.innerHTML = library();
    $$("img[data-th]", host).forEach((im) => { const p = P.d.photos.find((x) => x.id === im.dataset.th); if (p) thumbUrl(p).then((u) => { im.src = u; }).catch(() => {}); });
    clearTimeout(P.poll);
    if (P.d.status.setup.running) P.poll = setTimeout(async () => { try { P.d = await GET("/api/photo"); drawLib(); } catch (e) { /* 다음에 다시 */ } }, 2500);
  }
  async function reloadLib() { P.d = await GET("/api/photo"); drawLib(); }
  function addPhotos() {
    const inp = document.createElement("input");
    inp.type = "file"; inp.multiple = true;
    inp.accept = window.matchMedia("(pointer:coarse)").matches ? "image/*" : "image/*,.cr2,.cr3,.nef,.arw,.srw,.orf,.rw2,.dng,.raf,.pef";
    inp.onchange = async () => {
      const files = Array.from(inp.files || []);
      if (!files.length) return;
      let ok = 0; const bad = [];
      for (let i = 0; i < files.length; i++) {
        toast(`${i + 1} / ${files.length}장 올리는 중`);
        const fd = new FormData(); fd.append("file", files[i], files[i].name);
        try { const r = await (await call("POST", "/api/photo/upload", fd, true)).json(); ok += r.added.length; bad.push(...r.errors); }
        catch (e) { if (e instanceof Locked) return; bad.push(`${files[i].name}: ${e.message}`); }
      }
      toast(bad.length ? `${ok}장 올렸어요. 안 된 사진 ${bad.length}장: ${bad[0]}` : `${ok}장 올렸어요`, !!bad.length);
      await reloadLib();
    };
    inp.click();
  }
  function exportSheet(ids, single) {
    const ph = single && P.d.photos.find((p) => p.id === ids[0]);
    openSheet(single ? "사진 저장" : `${ids.length}장 내보내기`, `<form class="pe-form" novalidate>
      <div class="pe-fld"><span>크기</span><div class="pe-seg">${[[0, "원래 크기"], [2048, "긴 변 2048"], [1080, "긴 변 1080"]].map(([v, t], i) => `<label><input type="radio" name="size" value="${v}" ${i ? "" : "checked"}><span>${t}</span></label>`).join("")}</div></div>
      <div class="pe-fld"><span>화질</span><div class="pe-seg">${[[95, "가장 좋게"], [90, "좋게"], [80, "가볍게"]].map(([v, t], i) => `<label><input type="radio" name="quality" value="${v}" ${i === 1 ? "checked" : ""}><span>${t}</span></label>`).join("")}</div></div>
      <p class="pe-note">${single ? (ph && ph.params && ph.params.bg_remove ? "배경 제거를 켠 사진은 배경이 투명한 PNG로 저장돼요." : "저장을 누르면 공유 창이 떠요. '이미지 저장'을 고르면 사진 앱에 들어가요.") : "보정값을 입혀 압축 파일 하나로 받아요. 고품질 노이즈 제거를 켠 사진은 시간이 더 걸려요."}</p>
      <button type="submit" class="pe-btn main wide">${single ? "저장하기" : "내보내기"}</button></form>`, (root) => {
      $("form", root).onsubmit = async (e) => {
        e.preventDefault();
        const f = new FormData(e.target), btn = $("button[type=submit]", root), opt = { size: Number(f.get("size")), quality: Number(f.get("quality")) };
        btn.disabled = true; btn.textContent = "만드는 중…";
        try {
          if (single && P.ed) await saveNow();
          const res = single ? await call("POST", `/api/photo/${ids[0]}/export`, Object.assign({ params: P.ed ? P.ed.params : undefined }, opt)) : await call("POST", "/api/photo/export", Object.assign({ ids }, opt));
          const blob = await res.blob();
          closeSheet();
          await shareFile(blob, fileName(res, single ? "photo_edit.jpg" : "photos_edit.zip"));
        } catch (err) { if (!(err instanceof Locked)) toast(err.message, true); btn.disabled = false; btn.textContent = single ? "저장하기" : "내보내기"; }
      };
    });
  }
  // ---- 슬라이드쇼 영상: 고른 사진 + 분위기에 맞춰 고른 배경음악 ----
  const seg = (name, rows, pick) => `<div class="pe-seg">${rows.map(([v, t]) => `<label><input type="radio" name="${name}" value="${v}" ${String(v) === String(pick) ? "checked" : ""}><span>${t}</span></label>`).join("")}</div>`;
  function slideSheet(ids, last) {
    if (!P.d.status.video) { toast("영상 도구가 아직 없어요. 보관함 아래의 '추가 도구 설치'를 먼저 눌러 주세요", true); return; }
    const o = Object.assign({ sec: 3.5, ratio: "16:9", quality: "hd", music: "auto" }, last);
    openSheet(`슬라이드쇼 · ${ids.length}장`, `<form class="pe-form" novalidate>
      <div class="pe-fld"><span>사진 한 장에</span>${seg("sec", [[2.5, "2.5초"], [3.5, "3.5초"], [5, "5초"]], o.sec)}</div>
      <div class="pe-fld"><span>화면</span>${seg("ratio", [["16:9", "가로 16:9"], ["9:16", "세로 9:16"], ["1:1", "정사각"], ["4:5", "4:5"]], o.ratio)}</div>
      <div class="pe-fld"><span>화질</span>${seg("quality", [["hd", "보통(빠름)"], ["fhd", "선명하게"]], o.quality)}</div>
      <div class="pe-fld"><span>배경음악</span>${seg("music", P.d.moods, o.music)}</div>
      <p class="pe-note">자동 추천은 사진의 밝기와 색을 보고 분위기를 정한 뒤, 누구나 써도 되는 공개 음원(Openverse)에서 그때그때 한 곡을 골라요. 노트북이 인터넷에 연결돼 있어야 해요.</p>
      <button type="submit" class="pe-btn main wide">영상 만들기</button></form>`, (root) => {
      $("form", root).onsubmit = async (e) => {
        e.preventDefault();
        const f = new FormData(e.target), opt = { sec: Number(f.get("sec")), ratio: f.get("ratio"), quality: f.get("quality"), music: f.get("music") };
        try { const r = await (await call("POST", "/api/photo/slideshow", Object.assign({ ids }, opt))).json(); slideWatch(r.job, ids, opt); }
        catch (err) { if (!(err instanceof Locked)) toast(err.message, true); }
      };
    });
  }
  function slideWatch(job, ids, opt) {
    const body = $("#sheetBody");
    const paint = (j) => { body.innerHTML = `<div class="pe-form"><p class="pe-run"><i></i>${esc(j.step || "만드는 중")}</p>
      <div class="pe-prog" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(j.progress * 100)}"><i style="width:${(j.progress * 100).toFixed(1)}%"></i></div>
      <p class="pe-note">${j.count}장 · 약 ${Math.round(j.sec || 0)}초 영상. 이 창을 닫아도 노트북에서 계속 만들어요.</p></div>`; };
    paint(job);
    const tick = async () => {
      if (!$("#sheet").open || !body.isConnected) return;
      let j;
      try { j = (await (await call("GET", `/api/photo/slideshow/${job.id}`)).json()).job; } catch (err) { if (!(err instanceof Locked)) toast(err.message, true); return; }
      if (j.state === "running") { paint(j); return setTimeout(tick, 900); }
      if (j.state === "error") { body.innerHTML = `<div class="pe-form"><p class="pe-err">${esc(j.error)}</p><button type="button" class="pe-btn wide" id="peAgain">다시 설정하기</button></div>`; $("#peAgain").onclick = () => slideSheet(ids, opt); return; }
      let blob;
      try { blob = await (await call("GET", `/api/photo/slideshow/${j.id}/file`)).blob(); } catch (err) { toast(err.message, true); return; }
      const url = URL.createObjectURL(new Blob([blob], { type: "video/mp4" })), m = j.music;
      body.innerHTML = `<div class="pe-form"><video class="pe-video" src="${url}" controls playsinline></video>
        ${m ? `<div class="pe-credit">${m.credit ? `<b>${esc(m.mood_label || "")} 분위기</b><span>${esc(m.credit)}</span>${m.page ? `<small>${esc(m.page)}</small>` : ""}` : ""}${m.note ? `<small>${esc(m.note)}</small>` : ""}
          ${m.credit && m.license === "by" ? `<small>이 곡은 출처 표시가 조건이에요. 영상을 다른 곳에 올릴 때 위 출처를 같이 적어 주세요.</small>` : ""}</div>` : `<p class="pe-note">음악 없이 만들었어요.</p>`}
        <div class="pe-row">${m && m.credit ? `<button type="button" class="pe-btn" id="peCopy">출처 복사</button>` : ""}<button type="button" class="pe-btn grow" id="peAgain">${opt.music === "none" ? "다시 만들기" : "다른 음악으로"}</button></div>
        <button type="button" class="pe-btn main wide" id="peSaveV">영상 저장</button></div>`;
      $("#peSaveV").onclick = () => shareFile(new Blob([blob], { type: "video/mp4" }), "slideshow.mp4");
      $("#peAgain").onclick = () => { URL.revokeObjectURL(url); slideSheet(ids, opt); };
      if ($("#peCopy")) $("#peCopy").onclick = async () => { try { await navigator.clipboard.writeText(m.credit + (m.page ? "\n" + m.page : "")); toast("출처를 복사했어요"); } catch (err) { toast("복사하지 못했어요", true); } };
    };
    setTimeout(tick, 700);
  }
  async function onLib(e) {
    const el = (s) => e.target.closest(s);
    let b;
    if ((b = el("[data-act]"))) {
      const a = b.dataset.act;
      if (a === "add") return addPhotos();
      if (a === "pick") { P.picking = !P.picking; P.sel.clear(); return drawLib(); }
      if (a === "setup") { try { P.d.status = (await (await call("POST", "/api/photo/setup")).json()).status; } catch (err) { toast(err.message, true); } return drawLib(); }
      if (a === "exportSel") return exportSheet([...P.sel], false);
      if (a === "slide") return slideSheet(P.d.photos.filter((p) => P.sel.has(p.id)).map((p) => p.id).reverse());   // 먼저 올린 사진부터
      if (a === "delSel") {
        if (!confirmTap(b, "한 번 더 누르면 지워요")) return;
        for (const id of [...P.sel]) { try { await call("DELETE", `/api/photo/${id}`); } catch (err) { toast(err.message, true); } }
        P.sel.clear(); P.picking = false; toast("지웠어요");
        return reloadLib();
      }
    }
    if ((b = el(".pe-cell"))) {
      const id = b.dataset.id;
      if (!P.picking) { location.hash = `#photo/edit?id=${id}`; return; }
      if (P.sel.has(id)) P.sel.delete(id); else P.sel.add(id);
      drawLib();
    }
  }

  // =====================================================================
  // 편집기
  // =====================================================================
  const GEOM = ["rot", "flip", "angle", "crop"], AIK = ["skin", "bg_blur", "bg_remove"];
  const LABEL = { exposure: "노출", contrast: "대비", highlights: "밝은 곳", shadows: "어두운 곳", whites: "흰색", blacks: "검정",
    temp: "색온도", tint: "색조", vibrance: "생동감", saturation: "채도", clarity: "또렷함", dehaze: "안개 제거", sharpen: "선명도", sharpen_radius: "선명 반경",
    sharpen_thresh: "선명 문턱", nr_luma: "밝기 노이즈", nr_color: "색 노이즈", vignette: "가장자리", grain: "입자", fade: "바랜 느낌",
    split_sh_hue: "어두운 곳 색", split_sh_sat: "어두운 곳 색 세기", split_hl_hue: "밝은 곳 색", split_hl_sat: "밝은 곳 색 세기", angle: "수평", skin: "피부 보정", bg_blur: "배경 흐림" };
  const TABS = [["auto", "자동"], ["light", "빛"], ["color", "색"], ["hsl", "색별"], ["detail", "디테일"], ["fx", "효과"], ["crop", "자르기"], ["ai", "AI"]];
  const GROUP = { light: ["exposure", "contrast", "highlights", "shadows", "whites", "blacks"], color: ["temp", "tint", "vibrance", "saturation", "split_sh_hue", "split_sh_sat", "split_hl_hue", "split_hl_sat"],
    detail: ["clarity", "dehaze", "sharpen", "sharpen_radius", "sharpen_thresh", "nr_luma", "nr_color"], fx: ["vignette", "grain", "fade"], ai: ["skin", "bg_blur"] };
  const RATIOS = [["free", "자유"], ["orig", "원본 비율"], ["1:1", "1:1"], ["4:5", "4:5"], ["3:2", "3:2"], ["16:9", "16:9"], ["9:16", "9:16"]];
  const dflt = (k) => P.d.ranges[k][0];
  const val = (k) => (P.ed.params[k] == null ? dflt(k) : P.ed.params[k]);
  const fmt = (k, v) => (k === "exposure" ? (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(2) : k === "sharpen_radius" ? v.toFixed(1) : k === "angle" ? (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(1) + "°"
    : k.endsWith("_hue") ? Math.round(v) + "°" : (P.d.ranges[k][1] < 0 && v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(Math.round(v)));
  const step = (k) => (k === "exposure" ? 0.05 : k === "sharpen_radius" || k === "angle" ? 0.1 : 1);
  const changed = (keys) => keys.some((k) => (k === "hsl" ? Object.keys(P.ed.params.hsl || {}).length : k === "crop" ? !!P.ed.params.crop : typeof val(k) === "boolean" ? val(k) : P.d.ranges[k] ? val(k) !== dflt(k) : !!P.ed.params[k]));
  const tabChanged = (t) => (t === "hsl" ? changed(["hsl"]) : t === "crop" ? !!(P.ed.params.crop || P.ed.params.rot || P.ed.params.flip || P.ed.params.angle) : t === "color" ? changed(GROUP.color.filter((k) => !k.endsWith("_hue"))) || !!P.ed.params.bw
    : t === "ai" ? changed(GROUP.ai) || !!P.ed.params.bg_remove : t === "detail" ? changed(GROUP.detail.filter((k) => k !== "sharpen_radius")) || !!P.ed.params.nr_quality : GROUP[t] ? changed(GROUP[t]) : false);

  const slider = (k, cls = "") => { const [, lo, hi] = P.d.ranges[k], v = val(k); return `<label class="pe-sl ${v !== dflt(k) ? "on" : ""}" data-row="${k}"><span class="pe-sl-n" data-reset="${k}">${LABEL[k]}</span><output>${fmt(k, v)}</output>
    <input type="range" class="${cls}" min="${lo}" max="${hi}" step="${step(k)}" value="${v}" data-k="${k}" aria-label="${LABEL[k]}"></label>`; };
  const toggle = (k, label, note = "") => `<label class="pe-tg"><input type="checkbox" data-flag="${k}" ${P.ed.params[k] ? "checked" : ""}><span><b>${label}</b>${note ? `<small>${note}</small>` : ""}</span></label>`;

  function panel() {
    const ed = P.ed, t = ed.tab;
    if (t === "auto") return `<button type="button" class="pe-btn main wide" data-ed="auto">${IC.spark}AI 스마트 보정</button>
      <p class="pe-note">사진의 밝기와 색을 분석해 무난한 값을 넣어요. 그 뒤에 슬라이더로 다듬으세요.</p>
      <div class="pe-presets">${P.d.presets.map((p) => `<button type="button" class="pe-preset ${ed.preset === p.id ? "on" : ""}" data-preset="${p.id}"><img data-pv="${p.id}" alt=""><span>${esc(p.name)}</span></button>`).join("")}</div>`;
    if (t === "light" || t === "fx") return GROUP[t].map((k) => slider(k)).join("");
    if (t === "color") return slider("temp", "temp") + slider("tint", "tint") + slider("vibrance") + slider("saturation") + toggle("bw", "흑백")
      + `<p class="pe-sub">색조 (어두운 곳과 밝은 곳에 색 얹기)</p>` + slider("split_sh_hue", "hue") + slider("split_sh_sat") + slider("split_hl_hue", "hue") + slider("split_hl_sat");
    if (t === "detail") return ["clarity", "dehaze", "sharpen", "sharpen_radius", "sharpen_thresh"].map((k) => slider(k)).join("") + `<p class="pe-sub">노이즈 제거</p>` + slider("nr_luma") + slider("nr_color")
      + toggle("nr_quality", "고품질 노이즈 제거", "저장할 때만 적용돼요. 사진 한 장에 수십 초 걸릴 수 있어요");
    if (t === "hsl") {
      const cur = (ed.params.hsl || {})[ed.hsl] || [0, 0, 0];
      return `<div class="pe-chips" role="tablist" aria-label="색 고르기">${P.d.hsl.map(([k, n, h]) => `<button type="button" role="tab" data-hsl="${k}" aria-selected="${ed.hsl === k}" aria-label="${n}" class="${(ed.params.hsl || {})[k] ? "set" : ""}" style="--c:hsl(${h} 78% 52%)"></button>`).join("")}</div>
        ${[["색조", 0, "hue1"], ["채도", 1, ""], ["밝기", 2, ""]].map(([n, i, cls]) => `<label class="pe-sl ${cur[i] ? "on" : ""}"><span class="pe-sl-n" data-hslreset="${i}">${P.d.hsl.find((x) => x[0] === ed.hsl)[1]} ${n}</span><output>${cur[i] > 0 ? "+" : cur[i] < 0 ? "−" : ""}${Math.abs(Math.round(cur[i]))}</output>
          <input type="range" class="${cls}" min="-100" max="100" step="1" value="${cur[i]}" data-hi="${i}" aria-label="${n}"></label>`).join("")}`;
    }
    if (t === "crop") return `<div class="pe-chips txt">${RATIOS.map(([k, n]) => `<button type="button" data-ratio="${k}" aria-pressed="${ed.ratio === k}">${n}</button>`).join("")}</div>
      <div class="pe-row"><button type="button" class="pe-btn ghost" data-ed="rotL" aria-label="왼쪽으로 90도">${IC.rotL}</button><button type="button" class="pe-btn ghost" data-ed="rotR" aria-label="오른쪽으로 90도">${IC.rotR}</button>
        <button type="button" class="pe-btn ghost" data-ed="flip" aria-label="좌우 뒤집기">${IC.flip}</button><button type="button" class="pe-btn ghost grow" data-ed="cropReset">자르기 풀기</button></div>${slider("angle")}`;
    return `<button type="button" class="pe-btn main wide" data-ed="auto">${IC.spark}AI 스마트 보정</button>${slider("skin")}${P.d.status.ai
      ? slider("bg_blur") + toggle("bg_remove", "배경 제거", "사람·주제만 남기고 배경을 지워요. 투명한 PNG로 저장돼요") + `<p class="pe-note">배경 기능은 처음 한 번 사람을 찾는 데 몇 초 걸려요.</p>`
      : `<p class="pe-note">이 노트북에서는 배경 흐림·제거를 쓸 수 없어요.</p>`}`;
  }
  function drawPanel() {
    const ed = P.ed;
    $("#peTabs").innerHTML = TABS.map(([k, n]) => `<button type="button" role="tab" data-tab="${k}" aria-selected="${ed.tab === k}">${n}${tabChanged(k) ? `<i></i>` : ""}</button>`).join("");
    $("#pePanel").innerHTML = panel();
    $("#pePanel").scrollTop = 0;
    $("#peCrop").hidden = ed.tab !== "crop";
    if (ed.tab === "auto") previews();
    if (ed.tab === "crop") placeCrop();
  }
  function editorHtml() {
    const ed = P.ed;
    return `<div class="pe-top"><a class="pe-ico" href="#photo" aria-label="보관함으로">${IC.back}</a><span class="pe-name">${esc(ed.photo.name)}</span>
        <button type="button" class="pe-ico" data-ed="undo" aria-label="한 단계 되돌리기" disabled id="peUndo">${IC.undo}</button>
        <button type="button" class="pe-btn ghost" data-ed="reset">처음으로</button><button type="button" class="pe-btn main" data-ed="export">저장</button></div>
      <div class="pe-stage" id="peStage"><div class="pe-canvas" id="peCanvas"><img id="peOrig" class="pe-o" alt="" hidden><img id="peImg" alt="보정한 사진"><div class="pe-crop" id="peCrop" hidden>
          <div class="pe-crop-box" id="peBox"><i data-h="nw"></i><i data-h="ne"></i><i data-h="sw"></i><i data-h="se"></i></div></div>
          <div class="pe-split" id="peSplit" hidden><span></span></div></div>
        <span class="pe-busy" id="peBusy" hidden></span><span class="pe-tag" id="peTag" hidden>원본</span>
        <div class="pe-cmp"><button type="button" id="peHold" aria-label="누르고 있는 동안 원본 보기">원본</button><button type="button" id="peSplitBtn" aria-pressed="false">분할</button></div></div>
      <div class="pe-tabs" id="peTabs" role="tablist"></div><div class="pe-panel" id="pePanel"></div>`;
  }

  // ---- 그리기: 끄는 동안에는 작은 미리보기, 손을 떼면 제대로. 요청은 한 번에 하나만 ----
  function fitCanvas() {
    const ed = P.ed, st = $("#peStage"), im = $("#peImg");
    if (!ed || !st || !im || !im.naturalWidth) return;
    const pad = 12, W = st.clientWidth - pad * 2, H = st.clientHeight - pad * 2, k = Math.min(W / im.naturalWidth, H / im.naturalHeight);
    const c = $("#peCanvas");
    c.style.width = Math.max(40, Math.floor(im.naturalWidth * k)) + "px"; c.style.height = Math.max(40, Math.floor(im.naturalHeight * k)) + "px";
    if (ed.tab === "crop") placeCrop();
  }
  const geomOf = (p) => JSON.stringify(GEOM.map((k) => p[k] || 0));
  function request(fast) { const ed = P.ed; ed.want = fast ? (ed.want === "full" ? "full" : "fast") : "full"; pump(); }
  async function pump() {
    const ed = P.ed;
    if (!ed || ed.busy || !ed.want) return;
    const fast = ed.want === "fast";
    ed.want = null; ed.busy = true;
    $("#peBusy").hidden = fast;
    try {
      const blob = await (await call("POST", `/api/photo/${ed.id}/render`, { params: ed.params, size: fast ? "fast" : "base", crop: ed.tab !== "crop" })).blob();
      if (P.ed !== ed) return;
      const u = URL.createObjectURL(blob), im = $("#peImg"), old = ed.url;
      ed.url = u;
      im.onload = () => { fitCanvas(); if (old) URL.revokeObjectURL(old); };
      im.src = u;
    } catch (e) { if (!(e instanceof Locked)) toast(e.message, true); }
    finally { ed.busy = false; if (P.ed === ed) { $("#peBusy").hidden = true; if (ed.want) pump(); } }
  }
  async function loadOrig() {
    const ed = P.ed, key = geomOf(ed.params) + (ed.tab === "crop");
    if (ed.origKey === key) return;
    ed.origKey = key;
    try {
      const blob = await (await call("POST", `/api/photo/${ed.id}/render`, { params: ed.params, orig: true, crop: ed.tab !== "crop" })).blob();
      if (P.ed !== ed || ed.origKey !== key) return;
      if (ed.origUrl) URL.revokeObjectURL(ed.origUrl);
      ed.origUrl = URL.createObjectURL(blob); $("#peOrig").src = ed.origUrl;
    } catch (e) { ed.origKey = ""; }
  }
  function showOrig(on) { $("#peOrig").hidden = !on && !P.ed.split; $("#peImg").style.opacity = on ? "0" : ""; $("#peTag").hidden = !on; if (on) loadOrig(); }
  function setSplit(x) {
    const ed = P.ed; ed.splitX = Math.min(0.97, Math.max(0.03, x));
    $("#peImg").style.clipPath = ed.split ? `inset(0 0 0 ${(ed.splitX * 100).toFixed(1)}%)` : "";
    $("#peSplit").style.left = (ed.splitX * 100).toFixed(1) + "%";
  }
  async function previews() {
    const ed = P.ed, geom = {}; GEOM.forEach((k) => { if (ed.params[k]) geom[k] = ed.params[k]; });
    for (const p of P.d.presets) {
      const im = $(`img[data-pv="${p.id}"]`);
      if (!im || P.ed !== ed || ed.tab !== "auto") return;
      const key = p.id + geomOf(ed.params);
      try {
        if (!ed.pv[key]) ed.pv[key] = URL.createObjectURL(await (await call("POST", `/api/photo/${ed.id}/render`, { params: Object.assign({}, p.params, geom), size: "tiny" })).blob());
        if (im.isConnected) im.src = ed.pv[key];
      } catch (e) { return; }
    }
  }
  // ---- 저장(보정값은 손을 뗄 때마다 자동으로 저장) ----
  let saveT = 0;
  async function saveNow() {
    const ed = P.ed; clearTimeout(saveT);
    if (!ed || !ed.dirty) return;
    ed.dirty = false;
    try { const r = await (await call("PUT", `/api/photo/${ed.id}`, { params: ed.params })).json(); const i = P.d.photos.findIndex((p) => p.id === ed.id); if (i >= 0) P.d.photos[i] = r.photo; }
    catch (e) { ed.dirty = true; if (!(e instanceof Locked)) toast("보정값을 저장하지 못했어요: " + e.message, true); }
  }
  function commit(keepPreset) {      // 한 번의 조작이 끝났을 때: 되돌리기 기록 + 제대로 그리기 + 저장 예약
    const ed = P.ed, snap = JSON.stringify(ed.params);
    if (snap === ed.hist[ed.hist.length - 1]) return;
    ed.hist.push(snap); if (ed.hist.length > 40) ed.hist.shift();
    if (!keepPreset) ed.preset = "";
    ed.dirty = true; $("#peUndo").disabled = ed.hist.length < 2;
    clearTimeout(saveT); saveT = setTimeout(saveNow, 1200);
    request(false);
    if (ed.split || !$("#peOrig").hidden) loadOrig();
    $$("#peTabs button").forEach((b) => { const has = tabChanged(b.dataset.tab); if (has !== !!$("i", b)) b.innerHTML = b.textContent + (has ? "<i></i>" : ""); });
  }
  function setParams(p, keepPreset) { P.ed.params = p; commit(keepPreset); drawPanel(); }

  // ---- 자르기 틀 ----
  function cropRect() { return (P.ed.params.crop || [0, 0, 1, 1]).slice(); }
  function placeCrop() { const [x, y, w, h] = cropRect(), b = $("#peBox"); if (!b) return; b.style.left = x * 100 + "%"; b.style.top = y * 100 + "%"; b.style.width = w * 100 + "%"; b.style.height = h * 100 + "%"; }
  function ratioValue() {            // 틀의 가로/세로 비(정규화 좌표 기준). 없으면 자유
    const ed = P.ed, c = $("#peCanvas"), A = c.clientWidth / Math.max(1, c.clientHeight);
    if (ed.ratio === "free") return 0;
    if (ed.ratio === "orig") return 1;
    const [a, b] = ed.ratio.split(":").map(Number);
    return (a / b) / A;
  }
  function applyRatio() {
    const r = ratioValue(); if (!r) return;
    let [x, y, w, h] = cropRect(); const cx = x + w / 2, cy = y + h / 2;
    if (w / h > r) w = h * r; else h = w / r;
    if (w > 1) { w = 1; h = w / r; } if (h > 1) { h = 1; w = h * r; }
    x = Math.min(1 - w, Math.max(0, cx - w / 2)); y = Math.min(1 - h, Math.max(0, cy - h / 2));
    P.ed.params.crop = [x, y, w, h]; placeCrop();
  }
  function cropDrag(e) {
    const ed = P.ed, box = $("#peBox"), handle = e.target.dataset.h, c = $("#peCanvas").getBoundingClientRect();
    if (!handle && e.target !== box) return;
    e.preventDefault();
    const start = cropRect(), px = e.clientX, py = e.clientY, r = ratioValue(), MIN = 0.08;
    const move = (ev) => {
      const dx = (ev.clientX - px) / c.width, dy = (ev.clientY - py) / c.height;
      let [x, y, w, h] = start;
      if (!handle) { x = Math.min(1 - w, Math.max(0, x + dx)); y = Math.min(1 - h, Math.max(0, y + dy)); }
      else {
        let x1 = x + w, y1 = y + h;
        if (handle.includes("w")) x = Math.min(x1 - MIN, Math.max(0, x + dx)); else x1 = Math.max(x + MIN, Math.min(1, x1 + dx));
        if (handle.includes("n")) y = Math.min(y1 - MIN, Math.max(0, y + dy)); else y1 = Math.max(y + MIN, Math.min(1, y1 + dy));
        w = x1 - x; h = y1 - y;
        if (r) {                       // 비율 고정: 가로를 기준으로 세로를 맞추고, 넘치면 세로 기준으로
          let nh = w / r;
          if ((handle.includes("n") ? y1 - nh < 0 : y + nh > 1)) { nh = handle.includes("n") ? y1 : 1 - y; w = nh * r; if (handle.includes("w")) x = x1 - w; }
          if (handle.includes("n")) y = y1 - nh;
          h = nh;
        }
      }
      ed.params.crop = [x, y, w, h]; placeCrop();
    };
    const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); window.removeEventListener("pointercancel", up); const cr = ed.params.crop; if (cr && cr[2] > 0.995 && cr[3] > 0.995) ed.params.crop = null; commitCrop(); };
    window.addEventListener("pointermove", move); window.addEventListener("pointerup", up); window.addEventListener("pointercancel", up);
  }
  function commitCrop() {            // 자르기 틀은 화면에서만 움직이고, 사진은 다시 그리지 않는다(탭을 나갈 때 잘라서 보여 준다)
    const ed = P.ed, snap = JSON.stringify(ed.params);
    if (snap === ed.hist[ed.hist.length - 1]) return;
    ed.hist.push(snap); ed.dirty = true; $("#peUndo").disabled = ed.hist.length < 2;
    clearTimeout(saveT); saveT = setTimeout(saveNow, 1200);
  }

  // ---- 누름·끌기 ----
  let lastTap = ["", 0];
  function twice(key) { const now = Date.now(), ok = lastTap[0] === key && now - lastTap[1] < 400; lastTap = ok ? ["", 0] : [key, now]; return ok; }   // 이름을 두 번 누르면 처음 값으로
  async function onEd(e) {
    const ed = P.ed, el = (s) => e.target.closest(s);
    let b;
    if ((b = el("[data-tab]"))) {
      const was = ed.tab; ed.tab = b.dataset.tab; drawPanel();
      if ((was === "crop") !== (ed.tab === "crop")) { ed.origKey = ""; request(false); if (ed.split) loadOrig(); }
      return;
    }
    if ((b = el("[data-reset]"))) { const k = b.dataset.reset; if (val(k) !== dflt(k) && twice("r" + k)) { const p = Object.assign({}, ed.params); delete p[k]; setParams(p); } return; }
    if ((b = el("[data-hslreset]"))) { if (twice("h" + b.dataset.hslreset)) { const p = JSON.parse(JSON.stringify(ed.params)); const c = (p.hsl || {})[ed.hsl]; if (c) { c[Number(b.dataset.hslreset)] = 0; if (!c.some(Boolean)) delete p.hsl[ed.hsl]; setParams(p); } } return; }
    if ((b = el("[data-hsl]"))) { ed.hsl = b.dataset.hsl; return drawPanel(); }
    if ((b = el("[data-ratio]"))) { ed.ratio = b.dataset.ratio; applyRatio(); commitCrop(); return drawPanel(); }
    if ((b = el("[data-preset]"))) {
      const pr = P.d.presets.find((x) => x.id === b.dataset.preset), keep = {};
      GEOM.concat(AIK).forEach((k) => { if (ed.params[k]) keep[k] = ed.params[k]; });
      ed.preset = pr.id;
      return setParams(Object.assign({}, JSON.parse(JSON.stringify(pr.params)), keep), true);
    }
    if ((b = el("[data-ed]"))) {
      const a = b.dataset.ed;
      if (a === "export") return exportSheet([ed.id], true);
      if (a === "undo") { if (ed.hist.length > 1) { ed.hist.pop(); ed.params = JSON.parse(ed.hist[ed.hist.length - 1]); ed.dirty = true; $("#peUndo").disabled = ed.hist.length < 2; clearTimeout(saveT); saveT = setTimeout(saveNow, 1200); ed.origKey = ""; request(false); if (ed.split) loadOrig(); drawPanel(); } return; }
      if (a === "reset") { if (!confirmTap(b, "한 번 더")) return; ed.preset = ""; return setParams({}); }
      if (a === "auto") {
        b.disabled = true;
        try { const r = await (await call("GET", `/api/photo/${ed.id}/auto`)).json(), keep = {}; GEOM.concat(AIK).forEach((k) => { if (ed.params[k]) keep[k] = ed.params[k]; }); setParams(Object.assign({}, r.params, keep)); toast("자동 보정을 입혔어요"); }
        catch (err) { if (!(err instanceof Locked)) toast(err.message, true); }
        return;
      }
      const p = Object.assign({}, ed.params);
      if (a === "rotL" || a === "rotR") { p.rot = ((p.rot || 0) + (a === "rotR" ? 90 : 270)) % 360; delete p.crop; }
      if (a === "flip") { p.flip = !p.flip; if (p.crop) p.crop = [1 - p.crop[0] - p.crop[2], p.crop[1], p.crop[2], p.crop[3]]; }
      if (a === "cropReset") { delete p.crop; delete p.angle; ed.ratio = "free"; }
      ed.origKey = "";
      return setParams(p);
    }
  }
  function onInput(e) {
    const ed = P.ed, t = e.target;
    if (t.dataset.k) {
      const k = t.dataset.k, v = Number(t.value), row = t.closest(".pe-sl");
      if (v === dflt(k)) delete ed.params[k]; else ed.params[k] = v;
      $("output", row).textContent = fmt(k, v); row.classList.toggle("on", v !== dflt(k));
      if (k === "angle") { delete ed.params.crop; placeCrop(); }
      request(true);
    } else if (t.dataset.hi != null) {
      const i = Number(t.dataset.hi), v = Number(t.value), hsl = ed.params.hsl = Object.assign({}, ed.params.hsl), c = (hsl[ed.hsl] || [0, 0, 0]).slice();
      c[i] = v; if (c.some(Boolean)) hsl[ed.hsl] = c; else delete hsl[ed.hsl];
      if (!Object.keys(hsl).length) delete ed.params.hsl;
      const row = t.closest(".pe-sl"); $("output", row).textContent = (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v); row.classList.toggle("on", !!v);
      request(true);
    }
  }
  function onChange(e) {
    const ed = P.ed, t = e.target;
    if (t.dataset.flag) { if (t.checked) ed.params[t.dataset.flag] = true; else delete ed.params[t.dataset.flag]; commit(); return; }
    if (t.dataset.k || t.dataset.hi != null) { if (t.dataset.k === "angle") ed.origKey = ""; commit(); if (t.dataset.hi != null) $$("#pePanel [data-hsl]").forEach((b) => b.classList.toggle("set", !!(ed.params.hsl || {})[b.dataset.hsl])); }
  }
  function openEditor(id) {
    const photo = P.d.photos.find((p) => p.id === id);
    const host = $("#peApp");
    if (!photo) { location.hash = "#photo"; return; }
    Object.values((P.ed || {}).pv || {}).forEach((u) => URL.revokeObjectURL(u));
    const params = JSON.parse(JSON.stringify(photo.params || {}));
    P.ed = { id, photo, params, hist: [JSON.stringify(params)], tab: "light", hsl: "orange", ratio: "free", preset: "", pv: {}, split: false, splitX: 0.5, want: null, busy: false, url: null, origUrl: null, origKey: "", dirty: false };
    host.className = "pe-ed";
    host.innerHTML = editorHtml();
    drawPanel();
    request(false);
    const hold = $("#peHold");
    const down = (e) => { e.preventDefault(); showOrig(true); }, up = () => showOrig(false);
    hold.addEventListener("pointerdown", down); hold.addEventListener("pointerup", up); hold.addEventListener("pointerleave", up); hold.addEventListener("pointercancel", up);
    hold.addEventListener("keydown", (e) => { if (e.key === " " || e.key === "Enter") showOrig(true); }); hold.addEventListener("keyup", up);
    $("#peSplitBtn").onclick = (e) => { const ed = P.ed; ed.split = !ed.split; e.currentTarget.setAttribute("aria-pressed", ed.split); $("#peSplit").hidden = !ed.split; $("#peOrig").hidden = !ed.split; setSplit(ed.splitX); if (ed.split) loadOrig(); };
    $("#peSplit").addEventListener("pointerdown", (e) => {
      e.preventDefault(); const c = $("#peCanvas").getBoundingClientRect();
      const mv = (ev) => setSplit((ev.clientX - c.left) / c.width), end = () => { window.removeEventListener("pointermove", mv); window.removeEventListener("pointerup", end); window.removeEventListener("pointercancel", end); };
      window.addEventListener("pointermove", mv); window.addEventListener("pointerup", end); window.addEventListener("pointercancel", end);
    });
    $("#peCrop").addEventListener("pointerdown", cropDrag);
  }
  window.addEventListener("resize", () => { if (P.ed && $("#peStage")) fitCanvas(); });

  pages.photo = {
    async mount(h) {
      setTop("사진편집", {});
      const leaving = P.ed;
      if (leaving && leaving.dirty) await saveNow();
      if (leaving) { [leaving.url, leaving.origUrl].concat(Object.values(leaving.pv)).forEach((u) => u && URL.revokeObjectURL(u)); P.ed = null; }
      P.d = await GET("/api/photo");
      view.innerHTML = `<div class="pe-app" id="peApp"></div>`;
      const host = $("#peApp");
      host.addEventListener("click", (e) => { (P.ed ? onEd(e) : onLib(e)).catch((err) => { if (!(err instanceof Locked)) toast(err.message, true); }); });
      host.addEventListener("input", (e) => { if (P.ed) onInput(e); });
      host.addEventListener("change", (e) => { if (P.ed) onChange(e); });
      if (h.sub === "edit" && P.d.status.ready) openEditor(h.params.get("id")); else drawLib();
    },
  };
})();
