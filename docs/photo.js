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
    drop: svg('<path d="M14 4l6 6M4 20l1-5 9-9 4 4-9 9z"/>', 18), more: svg('<circle cx="5" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="19" cy="12" r="1.6"/>', 18),
  };
  addApp({
    id: "photo", name: "사진편집", desc: "PhotoGrader 보정, 프리셋", home: "#photo", secs: ["photo"],
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
    const running = st.setup.running ? `<p class="pe-run"><i></i>${esc(st.setup.log.slice(-1)[0] || "설치 중")}</p>` : "";
    if (st.missing.length) {
      return `${head}<section class="pe-setup"><h2>필요한 프로그램을 먼저 설치해요</h2>
        <p>사진 계산에 쓰는 프로그램(${esc(st.missing.join(", "))})을 노트북에 한 번 설치해야 해요. 노트북이 인터넷에 연결돼 있으면 1~3분 걸려요.</p>
        ${st.setup.error ? `<p class="pe-err">${esc(st.setup.error)}</p>` : ""}
        ${running || `<button type="button" class="pe-btn main wide" data-act="setup">필요한 프로그램 설치</button>`}</section>`;
    }
    if (!st.ready) {
      return `${head}<section class="pe-setup"><h2>PhotoGrader 엔진 파일을 넣어요</h2>
        <p>보정 엔진은 판매용 코드라 공개 저장소에 두지 않고 노트북에만 둬요. 받은 엔진 zip(photograder-engine.zip)을 그대로 올리면 노트북에 들어가요.</p>
        ${st.setup.error ? `<p class="pe-err">${esc(st.setup.error)}</p>` : ""}
        <button type="button" class="pe-btn main wide" data-act="engine">엔진 zip 올리기</button>
        <p class="pe-note">노트북에서 직접 넣으려면 zip을 우석World 폴더의 data/photo_engine 안에 풀어 두세요.</p></section>`;
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
      <p class="pe-note">PhotoGrader Pro ${esc(d.version)} 엔진 · 올린 원본은 노트북에 그대로 두고, 저장할 때 원본 크기로 보정해요.</p>${bar}`;
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
  function engineUpload() {
    const inp = document.createElement("input");
    inp.type = "file"; inp.accept = ".zip,application/zip";
    inp.onchange = async () => {
      const f = (inp.files || [])[0];
      if (!f) return;
      const fd = new FormData(); fd.append("file", f, f.name);
      toast("엔진 파일을 올리는 중");
      try { await call("POST", "/api/photo/engine", fd, true); toast("엔진을 넣었어요"); await reloadLib(); }
      catch (e) { if (!(e instanceof Locked)) toast(e.message, true); }
    };
    inp.click();
  }
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
  const seg = (name, rows, pick) => `<div class="pe-seg">${rows.map(([v, t]) => `<label><input type="radio" name="${name}" value="${v}" ${String(v) === String(pick) ? "checked" : ""}><span>${t}</span></label>`).join("")}</div>`;
  function exportSheet(ids, single) {
    const ph = single && P.d.photos.find((p) => p.id === ids[0]);
    const o = Object.assign({ fmt: "JPG", quality: 95, resize: 0, size_cap: false, wm_text: "", wm_pos: "우측 하단", wm_op: 60, shot_info: false }, store.get("photoExport", {}));
    const cut = single && P.ed ? P.ed.params.bg_remove : ph && ph.params && ph.params.bg_remove;
    openSheet(single ? "사진 저장" : `${ids.length}장 내보내기`, `<form class="pe-form" novalidate>
      <div class="pe-fld"><span>형식</span>${seg("fmt", [["JPG", "JPG"], ["PNG", "PNG"]], o.fmt)}</div>
      <div class="pe-fld"><span>품질 (JPG)</span>${seg("quality", [[100, "100"], [95, "95"], [90, "90"], [80, "80"]], o.quality)}</div>
      <div class="pe-fld"><span>크기 (긴 변)</span>${seg("resize", [[0, "원본"], [4000, "4000"], [2048, "2048"], [1080, "1080"]], o.resize)}</div>
      <label class="pe-tg"><input type="checkbox" name="size_cap" ${o.size_cap ? "checked" : ""}><span><b>20MB 이하로 내보내기</b><small>넘으면 품질과 크기를 조금씩 낮춰요</small></span></label>
      <label class="pe-tg"><input type="checkbox" name="shot_info" ${o.shot_info ? "checked" : ""}><span><b>촬영 정보 표기</b><small>오른쪽 아래에 바디·렌즈·노출값을 작게 넣어요</small></span></label>
      <label class="pe-fld"><span>워터마크</span><input type="text" name="wm_text" maxlength="60" value="${esc(o.wm_text)}" placeholder="비우면 없음" autocomplete="off"></label>
      <div class="pe-fld"><span>워터마크 위치</span><select name="wm_pos">${["우측 하단", "좌측 하단", "중앙", "우측 상단", "좌측 상단"].map((v) => `<option ${v === o.wm_pos ? "selected" : ""}>${v}</option>`).join("")}</select></div>
      <div class="pe-fld"><span>워터마크 진하기</span>${seg("wm_op", [[30, "30%"], [60, "60%"], [90, "90%"]], o.wm_op)}</div>
      <p class="pe-note">${cut ? "배경 제거를 켠 사진은 배경이 투명한 PNG로 저장돼요(워터마크·촬영 정보 없이). " : ""}${single ? "저장을 누르면 공유 창이 떠요. '이미지 저장'을 고르면 사진 앱에 들어가요." : "보정값을 입혀 압축 파일 하나로 받아요."} 원본 크기로 계산해서 한 장에 몇 초씩 걸려요.</p>
      <button type="submit" class="pe-btn main wide">${single ? "저장하기" : "내보내기"}</button></form>`, (root) => {
      $("form", root).onsubmit = async (e) => {
        e.preventDefault();
        const f = new FormData(e.target), btn = $("button[type=submit]", root);
        const opt = { fmt: f.get("fmt"), quality: Number(f.get("quality")), resize: Number(f.get("resize")), size_cap: !!f.get("size_cap"), shot_info: !!f.get("shot_info"),
          wm_text: String(f.get("wm_text") || "").trim(), wm_pos: f.get("wm_pos"), wm_op: Number(f.get("wm_op")) };
        store.set("photoExport", opt);
        btn.disabled = true; btn.textContent = "만드는 중…";
        try {
          if (single && P.ed) await saveNow();
          const res = single ? await call("POST", `/api/photo/${ids[0]}/export`, { params: P.ed ? P.ed.params : undefined, opt }) : await call("POST", "/api/photo/export", { ids, opt });
          const blob = await res.blob();
          closeSheet();
          await shareFile(blob, fileName(res, single ? "photo_edited.jpg" : "photos_edited.zip"));
        } catch (err) { if (!(err instanceof Locked)) toast(err.message, true); btn.disabled = false; btn.textContent = single ? "저장하기" : "내보내기"; }
      };
    });
  }
  // ---- 슬라이드쇼 영상: 고른 사진 + 분위기에 맞춰 고른 배경음악 ----
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
      if (a === "engine") return engineUpload();
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
  // 이름은 PhotoGrader Pro 화면 그대로
  const LABEL = { exposure: "노출", contrast: "대비", highlights: "하이라이트", shadows: "섀도", whites: "화이트", blacks: "블랙",
    temp: "색온도", tint: "색조", vibrance: "생동감", saturation: "채도", texture: "텍스처", clarity: "클래리티", sharpen_amt: "샤프닝 양", sharpen_rad: "샤프닝 반경",
    noise_red: "휘도 강도", noise_detail: "디테일 보존", noise_chroma: "색상 강도", noise_chroma_rad: "색상 반경", fade: "페이드(매트)", grain: "그레인", vignette: "비네팅",
    split_sh_hue: "섀도 색상", split_sh_amt: "섀도 강도", split_hi_hue: "하이라이트 색", split_hi_amt: "하이라이트 강도", angle: "수평", skin: "피부 보정", bg_blur: "배경 흐림" };
  const TABS = [["auto", "프리셋"], ["light", "톤"], ["color", "색"], ["hsl", "HSL"], ["detail", "디테일"], ["fx", "효과"], ["crop", "자르기"], ["ai", "AI"]];
  const GROUP = { light: ["exposure", "contrast", "highlights", "shadows", "whites", "blacks"], color: ["temp", "tint", "vibrance", "saturation", "split_sh_hue", "split_sh_amt", "split_hi_hue", "split_hi_amt"],
    detail: ["texture", "clarity", "sharpen_amt", "sharpen_rad", "noise_red", "noise_detail", "noise_chroma", "noise_chroma_rad"], fx: ["fade", "grain", "vignette"], ai: ["skin", "bg_blur"] };
  const QUIET = ["split_sh_hue", "split_hi_hue", "sharpen_rad", "noise_detail", "noise_chroma_rad"];      // 혼자서는 사진을 바꾸지 않는 값(세기를 따라가지 않는다)
  const RATIOS = [["free", "자유"], ["orig", "원본 비율"], ["1:1", "1:1"], ["4:5", "4:5"], ["3:2", "3:2"], ["16:9", "16:9"], ["9:16", "9:16"]];
  const dflt = (k) => P.d.ranges[k][0];
  const val = (k) => (P.ed.params[k] == null ? dflt(k) : P.ed.params[k]);
  const fmt = (k, v) => (k === "exposure" ? (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(2) : k === "sharpen_rad" ? v.toFixed(1) : k === "angle" ? (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(1) + "°"
    : k.endsWith("_hue") ? Math.round(v) + "°" : (P.d.ranges[k][1] < 0 && v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(Math.round(v)));
  const step = (k) => (k === "exposure" ? 0.05 : k === "sharpen_rad" || k === "angle" ? 0.1 : 1);
  const changed = (keys) => keys.some((k) => (k === "hsl" ? Object.keys(P.ed.params.hsl || {}).length : k === "crop" ? !!P.ed.params.crop : typeof val(k) === "boolean" ? val(k) : P.d.ranges[k] ? val(k) !== dflt(k) : !!P.ed.params[k]));
  const tabChanged = (t) => (t === "hsl" ? changed(["hsl"]) : t === "crop" ? !!(P.ed.params.crop || P.ed.params.rot || P.ed.params.flip || P.ed.params.angle) : t === "color" ? changed(GROUP.color.filter((k) => !QUIET.includes(k))) || !!P.ed.params.bw || !!P.ed.params.wb_gains
    : t === "ai" ? changed(GROUP.ai) || !!P.ed.params.bg_remove : t === "detail" ? changed(GROUP.detail.filter((k) => !QUIET.includes(k))) || !!P.ed.params.noise_hq : GROUP[t] ? changed(GROUP[t]) : false);
  // 프리셋 세기: 원본 프로그램의 blendParams와 같은 계산(색상·반경 같은 값은 그대로, 켜고 끄는 값은 50%부터)
  function blend(src, s) {
    const o = {};
    Object.keys(src || {}).forEach((k) => {
      const v = src[k];
      if (k === "hsl") { const h = {}; Object.keys(v).forEach((c) => { const t = v[c].map((x) => Math.max(-100, Math.min(100, +(x * s).toFixed(1)))); if (t.some(Boolean)) h[c] = t; }); if (Object.keys(h).length) o.hsl = h; }
      else if (typeof v === "number" && P.d.ranges[k] && !QUIET.includes(k)) { const [d, lo, hi] = P.d.ranges[k], n = Math.max(lo, Math.min(hi, +(d + (v - d) * s).toFixed(k === "exposure" ? 2 : 1))); if (n !== d) o[k] = n; }
      else if (typeof v === "boolean") { if (s >= 0.5 && v) o[k] = v; }
      else o[k] = v;
    });
    return o;
  }
  const keepOwn = () => { const keep = {}; GEOM.concat(AIK).forEach((k) => { if (P.ed.params[k]) keep[k] = P.ed.params[k]; }); return keep; };

  const slider = (k, cls = "") => { const [, lo, hi] = P.d.ranges[k], v = val(k); return `<label class="pe-sl ${v !== dflt(k) ? "on" : ""}" data-row="${k}"><span class="pe-sl-n" data-reset="${k}">${LABEL[k]}</span><output>${fmt(k, v)}</output>
    <input type="range" class="${cls}" min="${lo}" max="${hi}" step="${step(k)}" value="${v}" data-k="${k}" aria-label="${LABEL[k]}"></label>`; };
  const toggle = (k, label, note = "") => `<label class="pe-tg"><input type="checkbox" data-flag="${k}" ${P.ed.params[k] ? "checked" : ""}><span><b>${label}</b>${note ? `<small>${note}</small>` : ""}</span></label>`;

  function panel() {
    const ed = P.ed, t = ed.tab;
    if (t === "auto") {
      const card = (p) => { const why = (ed.rec || []).find((r) => r.id === p.id); return `<button type="button" class="pe-preset ${ed.preset === p.id ? "on" : ""}" data-preset="${esc(p.id)}"><img data-pv="${esc(p.id)}" alt=""><span>${esc(p.name)}</span>${why ? `<em title="${esc(why.why)}">추천</em>` : ""}</button>`; };
      const mine = P.d.presets.filter((p) => p.mine), cur = P.d.presets.find((p) => p.id === ed.preset);
      return `<div class="pe-row"><button type="button" class="pe-btn main grow" data-ed="auto">${IC.spark}자동 보정</button><button type="button" class="pe-btn grow" data-ed="match">참고 사진으로 만들기</button></div>
        ${(ed.rec || []).length ? `<p class="pe-note">이 사진 추천: ${ed.rec.map((r) => `${esc(r.id.slice(2))} (${esc(r.why)})`).join(" · ")}</p>` : ""}
        <label class="pe-sl ${ed.str !== 100 ? "on" : ""}"><span class="pe-sl-n">프리셋 강도</span><output>${ed.str}%</output><input type="range" min="0" max="150" step="5" value="${ed.str}" data-str aria-label="프리셋 강도" ${cur ? "" : "disabled"}></label>
        <p class="pe-sub">기본 프리셋</p><div class="pe-presets">${P.d.presets.filter((p) => !p.mine).map(card).join("")}</div>
        <p class="pe-sub">내 프리셋${cur && cur.mine ? `<button type="button" class="pe-link" data-ed="presetMore">'${esc(cur.name)}' 이름 바꾸기·지우기</button>` : ""}</p>
        ${mine.length ? `<div class="pe-presets">${mine.map(card).join("")}</div>` : `<p class="pe-note">아직 없어요. 지금 보정값을 저장하거나 참고 사진으로 만들어 보세요.</p>`}
        <button type="button" class="pe-btn wide" data-ed="presetSave">${IC.plus}현재 보정을 프리셋으로 저장</button>`;
    }
    if (t === "light" || t === "fx") return GROUP[t].map((k) => slider(k)).join("");
    if (t === "color") return `<div class="pe-row"><button type="button" class="pe-btn grow ${ed.pick ? "main" : ""}" data-ed="wbPick" aria-pressed="${!!ed.pick}">${IC.drop}${ed.pick ? "사진에서 회색이어야 할 곳을 누르세요" : "스포이드 (화이트밸런스)"}</button>
        ${ed.params.wb_gains ? `<button type="button" class="pe-btn ghost" data-ed="wbClear">해제</button>` : ""}</div>`
      + slider("temp", "temp") + slider("tint", "tint") + slider("vibrance") + slider("saturation") + toggle("bw", "흑백")
      + `<p class="pe-sub">스플릿 토닝</p>` + slider("split_sh_hue", "hue") + slider("split_sh_amt") + slider("split_hi_hue", "hue") + slider("split_hi_amt");
    if (t === "detail") return `<p class="pe-sub">질감</p>` + slider("texture") + slider("clarity") + `<p class="pe-sub">샤프닝</p>` + slider("sharpen_amt") + slider("sharpen_rad")
      + `<p class="pe-sub">노이즈 감소</p>` + ["noise_red", "noise_detail", "noise_chroma", "noise_chroma_rad"].map((k) => slider(k)).join("")
      + toggle("noise_hq", "고품질 노이즈 감소 (NLM)", "저장할 때만 적용돼요. 사진 한 장에 수십 초 걸릴 수 있어요");
    if (t === "hsl") {
      const cur = (ed.params.hsl || {})[ed.hsl] || [0, 0, 0];
      return `<div class="pe-chips" role="tablist" aria-label="색 고르기">${P.d.hsl.map(([k, n, h]) => `<button type="button" role="tab" data-hsl="${k}" aria-selected="${ed.hsl === k}" aria-label="${n}" class="${(ed.params.hsl || {})[k] ? "set" : ""}" style="--c:hsl(${h} 78% 52%)"></button>`).join("")}</div>
        ${[["색조 이동", 0, "hue1"], ["채도", 1, ""], ["휘도", 2, ""]].map(([n, i, cls]) => `<label class="pe-sl ${cur[i] ? "on" : ""}"><span class="pe-sl-n" data-hslreset="${i}">${P.d.hsl.find((x) => x[0] === ed.hsl)[1]} ${n}</span><output>${cur[i] > 0 ? "+" : cur[i] < 0 ? "−" : ""}${Math.abs(Math.round(cur[i]))}</output>
          <input type="range" class="${cls}" min="-100" max="100" step="1" value="${cur[i]}" data-hi="${i}" aria-label="${n}"></label>`).join("")}`;
    }
    if (t === "crop") return `<div class="pe-chips txt">${RATIOS.map(([k, n]) => `<button type="button" data-ratio="${k}" aria-pressed="${ed.ratio === k}">${n}</button>`).join("")}</div>
      <div class="pe-row"><button type="button" class="pe-btn ghost" data-ed="rotL" aria-label="왼쪽으로 90도">${IC.rotL}</button><button type="button" class="pe-btn ghost" data-ed="rotR" aria-label="오른쪽으로 90도">${IC.rotR}</button>
        <button type="button" class="pe-btn ghost" data-ed="flip" aria-label="좌우 뒤집기">${IC.flip}</button><button type="button" class="pe-btn ghost grow" data-ed="cropReset">자르기 풀기</button></div>${slider("angle")}`;
    return `<p class="pe-note">PhotoGrader 엔진으로 보정한 뒤에 덧입히는 기능이에요.</p>${slider("skin")}${P.d.status.ai
      ? slider("bg_blur") + toggle("bg_remove", "배경 제거", "사람·주제만 남기고 배경을 지워요. 투명한 PNG로 저장돼요") + `<p class="pe-note">배경 기능은 처음 한 번 사람을 찾는 데 몇 초 걸려요.</p>`
      : `<p class="pe-note">이 노트북에서는 배경 흐림·제거를 쓸 수 없어요.</p>`}`;
  }
  function drawPanel() {
    const ed = P.ed;
    $("#peTabs").innerHTML = TABS.map(([k, n]) => `<button type="button" role="tab" data-tab="${k}" aria-selected="${ed.tab === k}">${n}${tabChanged(k) ? `<i></i>` : ""}</button>`).join("");
    $("#pePanel").innerHTML = panel();
    $("#pePanel").scrollTop = 0;
    $("#peCrop").hidden = ed.tab !== "crop";
    if (ed.tab === "auto") { previews(); suggest(); }
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
        <canvas class="pe-hist" id="peHist" width="128" height="52" aria-hidden="true" hidden></canvas><span class="pe-busy" id="peBusy" hidden></span><span class="pe-tag" id="peTag" hidden>원본</span>
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
      const res = await call("POST", `/api/photo/${ed.id}/render`, { params: ed.params, size: fast ? "fast" : "base", crop: ed.tab !== "crop" }), blob = await res.blob();
      if (P.ed !== ed) return;
      drawHist(res.headers.get("X-Hist"));
      const u = URL.createObjectURL(blob), im = $("#peImg"), old = ed.url;
      ed.url = u;
      im.onload = () => { fitCanvas(); if (old) URL.revokeObjectURL(old); };
      im.src = u;
    } catch (e) { if (!(e instanceof Locked)) toast(e.message, true); }
    finally { ed.busy = false; if (P.ed === ed) { $("#peBusy").hidden = true; if (ed.want) pump(); } }
  }
  function drawHist(b64) {            // 빨강·초록·파랑 밝기 분포(각 64칸)
    const cv = $("#peHist");
    if (!cv || !b64) return;
    let raw; try { raw = window.atob(b64); } catch (e) { return; }
    if (raw.length !== 192) return;
    const g = cv.getContext("2d"), W = cv.width, H = cv.height;
    g.clearRect(0, 0, W, H); g.globalCompositeOperation = "lighter";
    ["#E5484D", "#3DBE6E", "#4C8DF6"].forEach((col, c) => {
      g.fillStyle = col; g.globalAlpha = 0.85; g.beginPath(); g.moveTo(0, H);
      for (let i = 0; i < 64; i++) g.lineTo((i + 0.5) * W / 64, H - Math.sqrt(raw.charCodeAt(c * 64 + i) / 255) * (H - 2));
      g.lineTo(W, H); g.closePath(); g.fill();
    });
    g.globalCompositeOperation = "source-over"; g.globalAlpha = 1; cv.hidden = false;
  }
  async function suggest() {          // 이 사진에 어울리는 기본 프리셋(한 번만 묻는다)
    const ed = P.ed;
    if (ed.rec) return;
    ed.rec = [];
    try { ed.rec = (await GET(`/api/photo/${ed.id}/suggest`)).items || []; if (P.ed === ed && ed.tab === "auto" && ed.rec.length) { const y = $("#pePanel").scrollTop; $("#pePanel").innerHTML = panel(); $("#pePanel").scrollTop = y; previews(); } }
    catch (e) { /* 추천이 없어도 쓰는 데 지장 없다 */ }
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
      const im = $$("img[data-pv]").find((x) => x.dataset.pv === p.id);
      if (!im || P.ed !== ed || ed.tab !== "auto") return;
      const key = p.id + geomOf(ed.params);
      try {
        if (!ed.pv[key] || ed.pvSrc[key] !== JSON.stringify(p.params)) { ed.pv[key] = URL.createObjectURL(await (await call("POST", `/api/photo/${ed.id}/render`, { params: Object.assign({}, p.params, geom), size: "tiny" })).blob()); ed.pvSrc[key] = JSON.stringify(p.params); }
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
      const pr = P.d.presets.find((x) => x.id === b.dataset.preset);
      if (!pr) return;
      ed.preset = pr.id; ed.str = 100;
      return setParams(Object.assign(blend(pr.params, 1), keepOwn()), true);
    }
    if ((b = el("[data-ed]"))) {
      const a = b.dataset.ed;
      if (a === "export") return exportSheet([ed.id], true);
      if (a === "undo") { if (ed.hist.length > 1) { ed.hist.pop(); ed.params = JSON.parse(ed.hist[ed.hist.length - 1]); ed.dirty = true; $("#peUndo").disabled = ed.hist.length < 2; clearTimeout(saveT); saveT = setTimeout(saveNow, 1200); ed.origKey = ""; request(false); if (ed.split) loadOrig(); drawPanel(); } return; }
      if (a === "reset") { if (!confirmTap(b, "한 번 더")) return; ed.preset = ""; return setParams({}); }
      if (a === "auto") {             // 원본처럼 지금 값 위에 분석 결과(노출·블랙·화이트·대비·생동감·WB)만 덮어쓴다
        b.disabled = true;
        try { const r = await (await call("POST", `/api/photo/${ed.id}/auto`, { params: ed.params })).json(), p = Object.assign({}, ed.params); delete p.temp; delete p.tint; setParams(Object.assign(p, r.params)); toast("자동 보정을 입혔어요 (노출·블랙·화이트·WB 분석)"); }
        catch (err) { if (!(err instanceof Locked)) toast(err.message, true); b.disabled = false; }
        return;
      }
      if (a === "wbPick") { ed.pick = !ed.pick; $("#peStage").classList.toggle("pick", ed.pick); return drawPanel(); }
      if (a === "wbClear") { const p = Object.assign({}, ed.params); delete p.wb_gains; return setParams(p); }
      if (a === "match") return matchPick();
      if (a === "presetSave") return presetSheet();
      if (a === "presetMore") { const cur = P.d.presets.find((x) => x.id === ed.preset); return cur && cur.mine ? presetSheet(cur) : undefined; }
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
    if (t.dataset.str != null) {
      const pr = P.d.presets.find((x) => x.id === ed.preset);
      ed.str = Number(t.value); $("output", t.closest(".pe-sl")).textContent = ed.str + "%"; t.closest(".pe-sl").classList.toggle("on", ed.str !== 100);
      if (pr) { ed.params = Object.assign(blend(pr.params, ed.str / 100), keepOwn()); request(true); }
    } else if (t.dataset.k) {
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
    if (t.dataset.str != null) { commit(true); return; }
    if (t.dataset.flag) { if (t.checked) ed.params[t.dataset.flag] = true; else delete ed.params[t.dataset.flag]; commit(); return; }
    if (t.dataset.k || t.dataset.hi != null) { if (t.dataset.k === "angle") ed.origKey = ""; commit(); if (t.dataset.hi != null) $$("#pePanel [data-hsl]").forEach((b) => b.classList.toggle("set", !!(ed.params.hsl || {})[b.dataset.hsl])); }
  }
  function openEditor(id) {
    const photo = P.d.photos.find((p) => p.id === id);
    const host = $("#peApp");
    if (!photo) { location.hash = "#photo"; return; }
    Object.values((P.ed || {}).pv || {}).forEach((u) => URL.revokeObjectURL(u));
    const params = JSON.parse(JSON.stringify(photo.params || {}));
    P.ed = { id, photo, params, hist: [JSON.stringify(params)], tab: "auto", hsl: "orange", ratio: "free", preset: "", str: 100, rec: null, pick: false, pv: {}, pvSrc: {}, split: false, splitX: 0.5, want: null, busy: false, url: null, origUrl: null, origKey: "", dirty: false };
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
    $("#peCanvas").addEventListener("click", (e) => { if (P.ed && P.ed.pick) wbPick(e).catch((err) => { if (!(err instanceof Locked)) toast(err.message, true); }); });
  }
  async function wbPick(e) {          // 스포이드: 누른 곳이 회색이 되도록 색 균형을 맞춘다(원본처럼 색온도·색조는 0으로)
    const ed = P.ed, c = $("#peCanvas").getBoundingClientRect();
    const u = (e.clientX - c.left) / c.width, v = (e.clientY - c.top) / c.height;
    const send = Object.assign({}, ed.params); if (ed.tab === "crop") delete send.crop;
    const r = await (await call("POST", `/api/photo/${ed.id}/wb`, { params: send, u, v })).json();
    const p = Object.assign({}, ed.params); delete p.temp; delete p.tint; p.wb_gains = r.wb_gains;
    ed.pick = false; $("#peStage").classList.remove("pick");
    setParams(p); toast("스포이드 화이트밸런스를 입혔어요");
  }
  // ---- 내 프리셋 저장·이름 바꾸기·지우기 ----
  function presetSheet(cur) {
    openSheet(cur ? "내 프리셋" : "프리셋 저장", `<form class="pe-form" novalidate>
      <label class="pe-fld"><span>이름</span><input type="text" name="name" maxlength="30" value="${esc(cur ? cur.name : "")}" placeholder="예: 실내 고감도" autocomplete="off" required></label>
      <p class="pe-note">${cur ? "이름만 바꿔요. 값을 바꾸려면 같은 이름으로 다시 저장하세요." : "지금 보정값을 저장해요. 자르기·돌리기·AI 값은 들어가지 않아요."}</p>
      <button type="submit" class="pe-btn main wide">${cur ? "이름 바꾸기" : "저장"}</button>
      ${cur ? `<button type="button" class="pe-btn danger wide" data-del>이 프리셋 지우기</button>` : ""}</form>`, (root) => {
      const done = (r, id, msg) => { P.d.presets = r.presets; P.ed.preset = id; P.ed.pv = {}; P.ed.pvSrc = {}; closeSheet(); drawPanel(); toast(msg); };
      $("form", root).onsubmit = async (e) => {
        e.preventDefault();
        const name = $("input[name=name]", root).value.replace(/\s+/g, " ").trim();
        if (!name) { toast("프리셋 이름을 적어 주세요", true); return; }
        try { done(await (await call("POST", "/api/photo/preset", cur ? { name, old: cur.name } : { name, params: P.ed.params })).json(), "u:" + name, cur ? "이름을 바꿨어요" : "프리셋으로 저장했어요"); }
        catch (err) { if (!(err instanceof Locked)) toast(err.message, true); }
      };
      const del = $("[data-del]", root);
      if (del) del.onclick = async () => {
        if (!confirmTap(del, "한 번 더 누르면 지워요")) return;
        try { done(await (await call("POST", "/api/photo/preset/delete", { name: cur.name })).json(), "", "프리셋을 지웠어요"); }
        catch (err) { if (!(err instanceof Locked)) toast(err.message, true); }
      };
    });
  }
  // ---- 참고 사진으로 프리셋 만들기 ----
  function matchPick() {
    const ed = P.ed, inp = document.createElement("input");
    inp.type = "file"; inp.accept = "image/*";
    inp.onchange = async () => {
      const f = (inp.files || [])[0];
      if (!f) return;
      toast("참고 사진의 색감과 톤을 분석하는 중");
      const fd = new FormData(); fd.append("file", f, f.name);
      try { const r = await (await call("POST", `/api/photo/${ed.id}/match`, fd, true)).json(); if (P.ed === ed) matchSheet(r); }
      catch (err) { if (!(err instanceof Locked)) toast(err.message, true); }
    };
    inp.click();
  }
  function matchSheet(r) {
    const ed = P.ed, before = JSON.stringify(ed.params);
    let str = 100, used = false;
    const show = () => { ed.params = Object.assign(blend(r.params, str / 100), keepOwn()); request(false); };
    openSheet("참고 사진으로 프리셋 만들기", `<form class="pe-form" novalidate>
      <div class="pe-meter"><span>닮은 정도</span><b>${r.before}%</b><i><u style="width:${Math.max(2, Math.min(100, r.after))}%"></u></i><b class="hl">${r.after}%</b></div>
      <div class="pe-chips txt">${r.traits.map((t) => `<span>${esc(t)}</span>`).join("") || `<span>큰 차이 없음</span>`}</div>
      <label class="pe-sl"><span class="pe-sl-n">강도</span><output>100%</output><input type="range" min="0" max="150" step="5" value="100" aria-label="강도"></label>
      <label class="pe-fld"><span>프리셋 이름</span><input type="text" name="name" maxlength="30" value="${esc(r.name)}" autocomplete="off"></label>
      <p class="pe-note">사진 내용이 많이 다르면 색이 과하게 따라올 수 있어요. 강도로 조절하고, 입힌 뒤 세부 값을 다듬으세요.</p>
      <div class="pe-row"><button type="button" class="pe-btn grow" data-apply>이 사진에만 적용</button><button type="submit" class="pe-btn main grow">프리셋으로 저장하고 적용</button></div></form>`, (root) => {
      const sl = $("input[type=range]", root);
      sl.oninput = () => { str = Number(sl.value); $("output", root).textContent = str + "%"; };
      sl.onchange = show;
      const finish = (msg) => { used = true; closeSheet(); commit(); drawPanel(); toast(msg); };
      $("[data-apply]", root).onclick = () => finish("참고 사진의 느낌을 입혔어요");
      $("form", root).onsubmit = async (e) => {
        e.preventDefault();
        const name = $("input[name=name]", root).value.replace(/\s+/g, " ").trim();
        if (!name) { toast("프리셋 이름을 적어 주세요", true); return; }
        try { const res = await (await call("POST", "/api/photo/preset", { name, params: blend(r.params, str / 100) })).json(); P.d.presets = res.presets; ed.pv = {}; ed.pvSrc = {}; finish("프리셋으로 저장하고 입혔어요"); ed.preset = "u:" + name; drawPanel(); }
        catch (err) { if (!(err instanceof Locked)) toast(err.message, true); }
      };
      show();
      sheet.addEventListener("close", () => { if (!used && P.ed === ed) { ed.params = JSON.parse(before); request(false); } }, { once: true });   // 그냥 닫으면 원래대로
    });
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
