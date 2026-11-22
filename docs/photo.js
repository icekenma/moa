"use strict";
/* 사진편집 앱(우석World) = PhotoGrader Pro (Web) 화면을 옮긴 것.
   원본(ui.html)의 구성 그대로다: 위 툴바 · 사진 목록 · 가운데 미리보기 · 히스토그램과 접히는 보정 카드 · 아래 상태줄.
   PC의 가로 3분할을 휴대폰 세로 화면에 맞게 위아래로 쌓았을 뿐, 이름·순서·색은 원본을 따른다.
   보정 계산과 사진 보관은 노트북 서버(/api/photo)가 원본 엔진으로 한다. */
(function () {
  addApp({
    id: "photo", name: "사진편집", desc: "PhotoGrader Pro", home: "#photo", secs: ["photo"],
    skin: { meta: "#F7F8FA", vars: { "--deep": "#1C2128", "--deep2": "#2A3038", "--bg": "#EEF0F3", "--hl": "#0F8A6E", "--solid": "#1C2128",
      "--on-deep": "#EDEFF2", "--on-deep2": "#A9B1BC", "--on-deep-strong": "#FFFFFF", "--sub-bg": "rgba(255,255,255,.08)" } },
    tile: { bg: "linear-gradient(150deg,#0F8A6E 0%,#1C2128 78%)", ink: "#FFFFFF", sub: "#CFE5DE", accent: "#7FE3C8" },
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

  // ---------- 원본 화면의 정의(이름·범위·설명·묶음) ----------
  const hueGrad = () => { const s = []; for (let h = 0; h <= 360; h += 30) s.push(`hsl(${h},80%,62%) ${h / 3.6}%`); return "linear-gradient(90deg," + s.join(",") + ")"; };
  const DEFS = {
    exposure: { label: "노출", step: 0.05, fmt: 2 }, contrast: { label: "대비" }, highlights: { label: "하이라이트" }, shadows: { label: "섀도" }, whites: { label: "화이트" }, blacks: { label: "블랙" },
    temp: { label: "색온도", grad: "linear-gradient(90deg,#6E9BE0,#EDEFF2 50%,#E8B96B)" }, tint: { label: "색조", grad: "linear-gradient(90deg,#6FBF73,#EDEFF2 50%,#D96FC0)" },
    vibrance: { label: "생동감" }, saturation: { label: "채도" }, _hsl_hue: { label: "색조 이동" }, _hsl_sat: { label: "채도" }, _hsl_lum: { label: "휘도" },
    texture: { label: "텍스처" }, clarity: { label: "클래리티" }, sharpen_amt: { label: "샤프닝 양" }, sharpen_rad: { label: "샤프닝 반경", step: 0.1, fmt: 1 },
    noise_red: { label: "휘도 강도" }, noise_detail: { label: "디테일 보존" }, noise_chroma: { label: "색상 강도" }, noise_chroma_rad: { label: "색상 반경" },
    fade: { label: "페이드(매트)" }, grain: { label: "그레인" }, vignette: { label: "비네팅" },
    split_sh_hue: { label: "섀도 색상", grad: hueGrad() }, split_sh_amt: { label: "섀도 강도" }, split_hi_hue: { label: "하이라이트 색", grad: hueGrad() }, split_hi_amt: { label: "하이라이트 강도" },
    angle: { label: "수평 맞추기", step: 0.1, fmt: 1 }, skin: { label: "피부 보정" }, bg_blur: { label: "배경 흐림" },
  };
  const TIPS = { exposure: "사진 전체를 밝게(+) 또는 어둡게(-). 가장 먼저 맞추세요", contrast: "밝은 곳은 더 밝게, 어두운 곳은 더 어둡게. 올리면 또렷, 내리면 부드러움",
    highlights: "하늘·창문처럼 밝은 부분만 조절. 내리면 날아간 하늘이 살아나요", shadows: "그늘·역광 얼굴처럼 어두운 부분만 밝게(+)", whites: "가장 밝은 점의 기준. 올리면 화사, 내리면 차분",
    blacks: "가장 어두운 점의 기준. 내리면 깊고 진득, 올리면 흐릿(매트)", temp: "왼쪽=파랗고 시원하게, 오른쪽=노랗고 따뜻하게", tint: "왼쪽=초록 기운, 오른쪽=분홍(자홍) 기운. 형광등 사진 보정",
    vibrance: "흐린 색만 골라 진하게. 피부는 덜 바뀌어 인물에 안전", saturation: "모든 색을 한꺼번에 진하게(+)/흐리게(-)", _hsl_hue: "선택한 색만 다른 색으로 살짝 이동 (예: 초록 잎을 연두로)",
    _hsl_sat: "선택한 색만 진하게/흐리게", _hsl_lum: "선택한 색만 밝게/어둡게 (예: 파란 하늘만 어둡게)", texture: "피부·나뭇결 같은 잔디테일. 내리면 피부가 매끈",
    clarity: "중간 크기 윤곽의 또렷함. 풍경은 올리고 인물은 살짝 내리기", sharpen_amt: "저장 결과의 선명도. 너무 높으면 테두리가 거칠어져요", sharpen_rad: "선명하게 할 윤곽의 굵기. 보통 1.0",
    noise_red: "어두운 곳의 자글자글한 입자 줄이기 (야간·실내)", noise_detail: "노이즈를 줄일 때 디테일을 얼마나 남길지", noise_chroma: "어두운 곳의 알록달록한 색 얼룩 제거", noise_chroma_rad: "색 얼룩 제거 범위",
    fade: "검정을 살짝 회색으로 띄워 필름·매트 느낌", grain: "필름 같은 입자감 추가", vignette: "가장자리를 어둡게(+) 해서 시선을 가운데로",
    split_sh_hue: "어두운 부분에 입힐 색 (영화 같은 색감)", split_sh_amt: "어두운 부분 색 입히기 강도", split_hi_hue: "밝은 부분에 입힐 색", split_hi_amt: "밝은 부분 색 입히기 강도",
    angle: "기울어진 수평선을 바로잡아요", skin: "피부만 곱게 (우석World에서 덧붙인 기능)", bg_blur: "사람·주제 뒤 배경을 흐리게 (우석World에서 덧붙인 기능)" };
  const SECTIONS = [
    { t: "기본 보정", open: true, rows: ["exposure", "contrast", "highlights", "shadows", "whites", "blacks"] },
    { t: "화이트밸런스", open: true, rows: ["temp", "tint"], extra: "wb" },
    { t: "색상", open: true, rows: ["vibrance", "saturation"], extra: "bw" },
    { t: "색상별 조정 (HSL)", rows: ["_hsl_hue", "_hsl_sat", "_hsl_lum"], pre: "hsl" },
    { t: "디테일", open: true, rows: ["texture", "clarity", "sharpen_amt", "sharpen_rad"] },
    { t: "노이즈 제거", open: true, rows: ["noise_red", "noise_detail", "noise_chroma", "noise_chroma_rad"], extra: "noise" },
    { t: "효과", rows: ["fade", "grain", "vignette"] },
    { t: "스플릿 토닝", rows: ["split_sh_hue", "split_sh_amt", "split_hi_hue", "split_hi_amt"] },
    { t: "변형 / 자르기", open: true, rows: ["angle"], extra: "transform" },
    { t: "AI 덧보정", rows: ["skin", "bg_blur"], extra: "ai" },
  ];
  const GEOM = ["rot", "flip", "angle", "crop"], OWN = GEOM.concat(["skin", "bg_blur", "bg_remove"]);
  const QUIET = ["split_sh_hue", "split_hi_hue", "sharpen_rad", "noise_detail", "noise_chroma_rad"];      // 강도를 따라가지 않는 값(원본의 KEEP_KEYS)
  const RATIOS = [["free", "자유"], ["orig", "원본"], ["1:1", "1:1"], ["4:5", "4:5"], ["3:2", "3:2"], ["16:9", "16:9"], ["9:16", "9:16"]];

  const P = { d: null, cur: "", params: {}, hist: [], redo: [], view: "edit", crop: false, ratio: "free", pick: false, band: 0, preset: "무보정", sl: {}, want: null, busy: false, url: null, origUrl: null, origKey: "", dirty: false, poll: 0, rec: {} };
  const q = (s) => $(s, $("#pgApp")), status = (t) => { const el = q("#pgStatus"); if (el) el.textContent = t; };
  const photo = () => P.d.photos.find((p) => p.id === P.cur);
  const range = (k) => (k[0] === "_" ? [0, -100, 100] : P.d.ranges[k]);
  const bandKey = () => P.d.hsl[P.band][0];
  function val(k) {
    const p = P.params;
    if (k[0] === "_") return ((p.hsl || {})[bandKey()] || [0, 0, 0])[["_hsl_hue", "_hsl_sat", "_hsl_lum"].indexOf(k)];
    return p[k] == null ? range(k)[0] : p[k];
  }
  function setVal(k, v) {
    const p = P.params;
    if (k[0] === "_") {
      const hsl = p.hsl = Object.assign({}, p.hsl), c = (hsl[bandKey()] || [0, 0, 0]).slice();
      c[["_hsl_hue", "_hsl_sat", "_hsl_lum"].indexOf(k)] = v;
      if (c.some(Boolean)) hsl[bandKey()] = c; else delete hsl[bandKey()];
      if (!Object.keys(hsl).length) delete p.hsl;
    } else if (v === range(k)[0]) delete p[k]; else p[k] = v;
    if (k === "angle") delete p.crop;
  }
  // 프리셋 강도: 원본의 blendParams와 같은 계산
  function blend(src, s) {
    const o = {};
    Object.keys(src || {}).forEach((k) => {
      const v = src[k];
      if (OWN.includes(k)) return;
      if (k === "hsl") { const h = {}; Object.keys(v).forEach((c) => { const t = v[c].map((x) => Math.max(-100, Math.min(100, +(x * s).toFixed(1)))); if (t.some(Boolean)) h[c] = t; }); if (Object.keys(h).length) o.hsl = h; }
      else if (typeof v === "number" && P.d.ranges[k] && !QUIET.includes(k)) { const [d, lo, hi] = P.d.ranges[k], n = Math.max(lo, Math.min(hi, +(d + (v - d) * s).toFixed(k === "exposure" ? 2 : 1))); if (n !== d) o[k] = n; }
      else if (typeof v === "boolean") { if (s >= 0.5 && v) o[k] = v; }
      else o[k] = v;
    });
    return o;
  }
  const keepOwn = (p) => { const keep = {}; OWN.forEach((k) => { if ((p || P.params)[k]) keep[k] = (p || P.params)[k]; }); return keep; };
  const geomOnly = (p) => { const g = {}; GEOM.forEach((k) => { if (p[k]) g[k] = p[k]; }); return g; };

  // =====================================================================
  // 화면 뼈대 (원본: #toolbar · #film · #center · #panel · #statusbar)
  // =====================================================================
  function shell() {
    const st = P.d.status, n = P.d.photos.length;
    const tools = `<div class="pg-tb" id="pgToolbar"><a class="pg-btn pg-home" href="#world" aria-label="우석World 첫 화면으로">🌐</a><div class="pg-brand">PhotoGrader <em>Pro</em></div><span class="pg-spacer"></span>
        <button type="button" class="pg-btn" data-a="help" aria-label="도움말">?</button><button type="button" class="pg-btn secondary" data-a="sync" title="현재 설정을 모든 사진에 적용">전체 적용</button>
        <button type="button" class="pg-btn primary" data-a="export">📤 내보내기</button></div>
      <div class="pg-tb two"><button type="button" class="pg-btn" data-a="add" title="파일 추가">＋ 파일 추가</button><i class="pg-sep"></i>
        <button type="button" class="pg-btn secondary" data-a="gallery" title="프리셋을 내 사진 미리보기로 비교하고 고르기">🎨 <span id="pgPresetName">${esc(P.preset)}</span> ▾</button>
        <button type="button" class="pg-btn" data-a="ref" title="마음에 드는 사진을 넣으면 비슷한 색감의 프리셋을 만들어 드려요">🖼 참고 사진</button><i class="pg-sep"></i>
        <button type="button" class="pg-btn" data-a="auto">✨ 자동 보정</button><button type="button" class="pg-btn" data-a="reset">↩ 초기화</button>
        <button type="button" class="pg-btn sq" data-a="undo" id="pgUndo" aria-label="실행 취소" disabled>↶</button><button type="button" class="pg-btn sq" data-a="redo" id="pgRedo" aria-label="다시 실행" disabled>↷</button><i class="pg-sep"></i>
        <div class="pg-seg" id="pgSeg"><span class="pg-seg-cap">보기</span>${[["edit", "보정"], ["split", "분할"], ["orig", "원본"]].map(([v, t]) => `<button type="button" data-v="${v}" class="${P.view === v ? "on" : ""}">${t}</button>`).join("")}</div><i class="pg-sep"></i>
        <button type="button" class="pg-btn" data-a="slide">🎬 슬라이드쇼</button><button type="button" class="pg-btn" data-a="del">🗑 사진 삭제</button></div>`;
    let empty = "";
    if (st.missing.length) empty = `<div class="pg-big">필요한 프로그램을 먼저 설치해요</div><div>사진 계산에 쓰는 프로그램(${esc(st.missing.join(", "))})을 노트북에 한 번 설치합니다 · 1~3분</div>
        ${st.setup.error ? `<div class="pg-err">${esc(st.setup.error)}</div>` : ""}${st.setup.running ? `<div class="pg-busy"><i class="pg-spin"></i>${esc(st.setup.log.slice(-1)[0] || "설치 중")}</div>` : `<button type="button" class="pg-btn primary" data-a="setup">필요한 프로그램 설치</button>`}`;
    else if (!st.ready) empty = `<div class="pg-big">PhotoGrader 엔진 파일을 넣어요</div><div>보정 엔진은 판매용 코드라 노트북에만 둡니다. 받은 photograder-engine.zip을 그대로 올리세요</div>
        ${st.setup.error ? `<div class="pg-err">${esc(st.setup.error)}</div>` : ""}<button type="button" class="pg-btn primary" data-a="engine">엔진 zip 올리기</button>`;
    else if (!n) empty = `<div class="pg-big">사진을 불러와 시작하세요</div><div>＋ 파일 추가 · RAW(CR2/NEF/SRW/ORF)와 JPG 지원</div>
        <div class="pg-dim">처음이라면 🎨 프리셋에서 색감을 고르거나, 마음에 드는 사진을 🖼 참고 사진으로 넣어보세요</div><button type="button" class="pg-btn primary" data-a="add">＋ 파일 추가</button>`;
    return `${tools}<div class="pg-main">
        <div class="pg-center" id="pgCenter"><div class="pg-canvas" id="pgCanvas" ${n && st.ready ? "" : "hidden"}><img id="pgOrig" class="pg-o" alt="" hidden><img id="pgImg" alt="보정한 사진">
            <div class="pg-crop" id="pgCrop" hidden><div class="pg-crop-box" id="pgBox"><i data-h="nw"></i><i data-h="ne"></i><i data-h="sw"></i><i data-h="se"></i></div></div>
            <div class="pg-split" id="pgSplit" hidden><span></span></div><em class="pg-chip l" id="pgChipL" hidden>원본</em><em class="pg-chip r" id="pgChipR" hidden>보정</em></div>
          ${empty ? `<div class="pg-empty">${empty}</div>` : ""}<i class="pg-spin pg-wait" id="pgBusy" hidden></i></div>
        <div class="pg-film"><h3>사진 목록 <span class="pg-cnt">${n ? n + "장" : ""}</span></h3><div class="pg-filmlist" id="pgFilm"></div></div>
        <div class="pg-panel" id="pgPanel"><div class="pg-card"><div class="pg-histwrap"><h4>히스토그램</h4><canvas id="pgHist" width="292" height="84"></canvas></div></div><div id="pgSections"></div></div>
      </div><div class="pg-statusbar"><div id="pgStatus">${n ? "" : "＋ 파일 추가로 사진을 올려주세요"}</div>
        ${st.ready && !(st.raw && st.video) ? (st.setup.running ? `<span class="pg-busy"><i class="pg-spin"></i>설치 중</span>` : `<button type="button" class="pg-link" data-a="setup">추가 도구 설치 (${[st.raw ? "" : "RAW", st.video ? "" : "슬라이드쇼"].filter(Boolean).join("·")})</button>`) : ""}
        <span class="pg-hints">라벨을 누르면 설명 · 슬라이더를 두 번 누르면 초기화</span></div><div class="pg-ov" id="pgOv" hidden></div>`;
  }
  function drawFilm() {
    const host = q("#pgFilm");
    if (!P.d.photos.length) { host.innerHTML = `<div class="pg-filmempty">파일을 추가하면 사진이 여기에 표시됩니다</div>`; return; }
    host.innerHTML = P.d.photos.map((p) => `<button type="button" class="pg-fcard ${p.id === P.cur ? "sel" : ""} ${p.edited ? "edited" : ""}" data-id="${p.id}" aria-label="${esc(p.name)}${p.edited ? ", 보정함" : ""}">
      <span class="pg-thumbwrap"><img data-th="${p.id}" alt=""><span class="pg-fbadge">${esc((p.name.split(".").pop() || "JPG").toUpperCase().slice(0, 4))}</span></span>
      <span class="pg-fnamerow"><i class="pg-fdot"></i><span class="pg-fname">${esc(p.name)}</span></span></button>`).join("");
    $$("img[data-th]", host).forEach((im) => { const p = P.d.photos.find((x) => x.id === im.dataset.th); if (p) thumbUrl(p).then((u) => { im.src = u; }).catch(() => {}); });
    const sel = $(".sel", host); if (sel && sel.scrollIntoView) sel.scrollIntoView({ block: "nearest", inline: "nearest" });
  }
  // ---- 슬라이더(원본 makeSlider): 트랙을 누르거나 끌어서 조절, 두 번 누르면 처음 값 ----
  function makeSlider(key) {
    const def = DEFS[key], [, lo, hi] = range(key), step = def.step || 1, row = document.createElement("div");
    row.className = "pg-srow"; row.title = def.label + " — " + (TIPS[key] || "");
    row.innerHTML = `<span class="pg-slabel">${def.label}</span><div class="pg-strack" role="slider" tabindex="0" aria-label="${def.label}" aria-valuemin="${lo}" aria-valuemax="${hi}"><div class="rail"></div><div class="fill"></div><div class="notch"></div><div class="thumb"></div></div><span class="pg-sval">0</span>`;
    const track = $(".pg-strack", row), fill = $(".fill", row), notch = $(".notch", row), thumb = $(".thumb", row), sval = $(".pg-sval", row);
    if (def.grad) { $(".rail", row).style.background = def.grad; fill.style.display = "none"; }
    const bidir = lo < 0, zeroF = bidir ? (0 - lo) / (hi - lo) : 0;
    if (bidir && !def.grad) notch.style.left = `calc(${zeroF * 100}% - 1px)`; else notch.style.display = "none";
    let value = 0;
    const draw = () => {
      const f = (value - lo) / (hi - lo), d0 = range(key)[0];
      thumb.style.left = f * 100 + "%";
      if (!def.grad) { const a = Math.min(f, zeroF), b = Math.max(f, zeroF); fill.style.left = a * 100 + "%"; fill.style.width = (b - a) * 100 + "%"; }
      sval.textContent = (value > 0 && bidir ? "+" : "") + (def.fmt ? value.toFixed(def.fmt) : Math.round(value));
      sval.classList.toggle("chg", Math.abs(value - d0) > 1e-9); track.setAttribute("aria-valuenow", value);
    };
    const put = (v, done) => { v = Math.max(lo, Math.min(hi, +(Math.round(v / step) * step).toFixed(4))); if (v !== value) { value = v; draw(); setVal(key, v); request(true); } if (done) commit(); };
    const fromX = (x) => { const r = track.getBoundingClientRect(); return lo + Math.max(0, Math.min(1, (x - r.left) / r.width)) * (hi - lo); };
    let tap = 0;
    track.addEventListener("pointerdown", (e) => {
      if (!P.cur) return;
      const now = Date.now();
      if (now - tap < 320) { tap = 0; put(range(key)[0], true); return; }     // 두 번 누르면 처음 값(원본의 더블클릭)
      tap = now;
      track.setPointerCapture(e.pointerId); put(fromX(e.clientX));
      const mv = (ev) => put(fromX(ev.clientX)), up = () => { track.removeEventListener("pointermove", mv); track.removeEventListener("pointerup", up); track.removeEventListener("pointercancel", up); commit(); };
      track.addEventListener("pointermove", mv); track.addEventListener("pointerup", up); track.addEventListener("pointercancel", up);
    });
    track.addEventListener("keydown", (e) => { const d = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0; if (d && P.cur) { e.preventDefault(); put(value + d * step, true); } });
    $(".pg-slabel", row).addEventListener("click", () => status(row.title));
    P.sl[key] = { set: (v) => { value = v; draw(); } };
    return row;
  }
  function buildSections() {
    const host = q("#pgSections"), open = store.get("photoOpen", null);
    host.innerHTML = ""; P.sl = {};
    SECTIONS.forEach((sec) => {
      const card = document.createElement("div"), isOpen = open ? open.includes(sec.t) : !!sec.open;
      card.className = "pg-card" + (isOpen ? " open" : ""); card.dataset.sec = sec.t;
      card.innerHTML = `<button type="button" class="pg-sec-h" aria-expanded="${isOpen}"><span class="caret">${isOpen ? "▾" : "▸"}</span>${sec.t}</button><div class="pg-sec-b"></div>`;
      const b = $(".pg-sec-b", card);
      $(".pg-sec-h", card).onclick = (e) => { const on = card.classList.toggle("open"); e.currentTarget.setAttribute("aria-expanded", on); $(".caret", card).textContent = on ? "▾" : "▸"; store.set("photoOpen", $$(".pg-card.open[data-sec]", host).map((c) => c.dataset.sec)); };
      if (sec.pre === "hsl") b.insertAdjacentHTML("beforeend", `<div class="pg-hslchips">${P.d.hsl.map(([, n, h], i) => `<button type="button" class="pg-hslchip ${i === P.band ? "on" : ""}" data-band="${i}" title="${n}" aria-label="${n}" style="background:hsl(${h},70%,55%)"></button>`).join("")}</div>`);
      sec.rows.forEach((k) => b.append(makeSlider(k)));
      const x = sec.extra;
      if (x === "wb") b.insertAdjacentHTML("beforeend", `<div class="pg-row"><button type="button" class="pg-btn secondary small" data-a="wbpick" id="pgWbPick">💧 스포이드 (회색 지점 누르기)</button><button type="button" class="pg-btn small" data-a="wbreset">WB 초기화</button></div>`);
      if (x === "bw") b.insertAdjacentHTML("beforeend", `<label class="pg-chk"><input type="checkbox" data-flag="bw">흑백으로 변환</label>`);
      if (x === "noise") b.insertAdjacentHTML("beforeend", `<label class="pg-chk"><input type="checkbox" data-flag="noise_hq">고품질 노이즈 제거 (내보내기 때만 적용 · 느림)</label>`);
      if (x === "transform") b.insertAdjacentHTML("beforeend", `<div class="pg-row"><button type="button" class="pg-btn secondary small" data-a="rotL">⟲ 왼쪽 90°</button><button type="button" class="pg-btn secondary small" data-a="rotR">⟳ 오른쪽 90°</button><button type="button" class="pg-btn secondary small" data-a="flip">⇋ 좌우 반전</button></div>
        <div class="pg-row"><button type="button" class="pg-btn secondary small" data-a="crop" id="pgCropBtn">✂ 자르기</button><button type="button" class="pg-btn small" data-a="cropreset">자르기 초기화</button></div>
        <div class="pg-row" id="pgRatios" hidden>${RATIOS.map(([k, n]) => `<button type="button" class="pg-btn small ${P.ratio === k ? "active" : ""}" data-ratio="${k}">${n}</button>`).join("")}</div>`);
      if (x === "ai") b.insertAdjacentHTML("beforeend", P.d.status.ai ? `<label class="pg-chk"><input type="checkbox" data-flag="bg_remove">배경 제거 (투명 PNG로 저장)</label><div class="pg-dim">원본 프로그램에 없는, 우석World에서 덧붙인 기능입니다. 배경 기능은 처음 한 번 몇 초 걸려요</div>` : `<div class="pg-dim">이 노트북에서는 배경 흐림·제거를 쓸 수 없어요</div>`);
      host.append(card);
    });
  }
  function loadUI() {            // 원본의 loadParamsToUI
    Object.keys(P.sl).forEach((k) => P.sl[k].set(val(k)));
    $$("#pgSections [data-flag]").forEach((c) => { c.checked = !!P.params[c.dataset.flag]; });
    const u = q("#pgUndo"), r = q("#pgRedo"); if (u) { u.disabled = P.hist.length < 2; r.disabled = !P.redo.length; }
    const cb = q("#pgCropBtn"); if (cb) { cb.textContent = P.crop ? "✔ 자르기 적용" : "✂ 자르기"; cb.classList.toggle("active", P.crop); q("#pgRatios").hidden = !P.crop; }
    const wb = q("#pgWbPick"); if (wb) wb.classList.toggle("active", P.pick);
    q("#pgCenter").classList.toggle("pick", P.pick);
    q("#pgPresetName").textContent = P.preset;
  }

  // ---- 그리기: 끄는 동안에는 작은 미리보기, 손을 떼면 제대로. 요청은 한 번에 하나만 ----
  function fitCanvas() {
    const st = q("#pgCenter"), im = q("#pgImg"), c = q("#pgCanvas");
    if (!st || !im || !im.naturalWidth) return;
    const pad = 10, k = Math.min((st.clientWidth - pad * 2) / im.naturalWidth, (st.clientHeight - pad * 2) / im.naturalHeight);
    c.style.width = Math.max(40, Math.floor(im.naturalWidth * k)) + "px"; c.style.height = Math.max(40, Math.floor(im.naturalHeight * k)) + "px";
    if (P.crop) placeCrop();
  }
  function request(fast) { if (!P.cur) return; P.want = fast ? (P.want === "full" ? "full" : "fast") : "full"; pump(); }
  async function pump() {
    if (P.busy || !P.want || !P.cur) return;
    const fast = P.want === "fast", id = P.cur;
    P.want = null; P.busy = true; q("#pgBusy").hidden = fast;
    try {
      const res = await call("POST", `/api/photo/${id}/render`, { params: P.params, size: fast ? "fast" : "base", crop: !P.crop }), blob = await res.blob();
      if (P.cur !== id || !q("#pgImg")) return;
      drawHist(res.headers.get("X-Hist"));
      const u = URL.createObjectURL(blob), im = q("#pgImg"), old = P.url;
      P.url = u; im.onload = () => { fitCanvas(); if (old) URL.revokeObjectURL(old); }; im.src = u;
    } catch (e) { if (!(e instanceof Locked)) toast(e.message, true); }
    finally { P.busy = false; if (q("#pgBusy")) { q("#pgBusy").hidden = true; if (P.want) pump(); } }
  }
  function drawHist(b64) {
    const cv = q("#pgHist");
    if (!cv) return;
    const g = cv.getContext("2d"), W = cv.width, H = cv.height;
    g.clearRect(0, 0, W, H);
    let raw = ""; try { raw = b64 ? window.atob(b64) : ""; } catch (e) { raw = ""; }
    if (raw.length !== 192) return;
    ["rgba(229,72,77,.55)", "rgba(46,160,90,.55)", "rgba(59,125,221,.55)"].forEach((col, c) => {
      g.fillStyle = col; g.beginPath(); g.moveTo(0, H);
      for (let i = 0; i < 64; i++) g.lineTo((i + 0.5) * W / 64, H - Math.sqrt(raw.charCodeAt(c * 64 + i) / 255) * (H - 4));
      g.lineTo(W, H); g.closePath(); g.fill();
    });
  }
  async function loadOrig() {
    const id = P.cur, key = id + JSON.stringify(geomOnly(P.params)) + P.crop;
    if (P.origKey === key) return;
    P.origKey = key;
    try {
      const blob = await (await call("POST", `/api/photo/${id}/render`, { params: P.params, orig: true, crop: !P.crop })).blob();
      if (P.cur !== id || P.origKey !== key) return;
      if (P.origUrl) URL.revokeObjectURL(P.origUrl);
      P.origUrl = URL.createObjectURL(blob); q("#pgOrig").src = P.origUrl;
    } catch (e) { P.origKey = ""; }
  }
  function setView(v) {          // 원본의 보기: 보정 · 분할 · 원본
    P.view = v;
    $$("#pgSeg button").forEach((b) => b.classList.toggle("on", b.dataset.v === v));
    const split = v === "split", orig = v === "orig";
    q("#pgOrig").hidden = v === "edit"; q("#pgSplit").hidden = !split; q("#pgChipL").hidden = !split && !orig; q("#pgChipR").hidden = !split;
    q("#pgImg").style.opacity = orig ? "0" : "";
    setSplit(P.splitX || 0.5);
    if (v !== "edit") loadOrig();
  }
  function setSplit(x) {
    P.splitX = Math.min(0.97, Math.max(0.03, x));
    q("#pgImg").style.clipPath = P.view === "split" ? `inset(0 0 0 ${(P.splitX * 100).toFixed(1)}%)` : "";
    q("#pgSplit").style.left = (P.splitX * 100).toFixed(1) + "%";
  }

  // ---- 저장·되돌리기 ----
  let saveT = 0;
  async function saveNow() {
    clearTimeout(saveT);
    if (!P.cur || !P.dirty) return;
    P.dirty = false;
    const id = P.cur;
    try { const r = await (await call("PUT", `/api/photo/${id}`, { params: P.params })).json(); const i = P.d.photos.findIndex((p) => p.id === id); if (i >= 0) { P.d.photos[i] = r.photo; const c = $(`.pg-fcard[data-id="${id}"]`); if (c) { c.classList.toggle("edited", r.photo.edited); thumbUrl(r.photo).then((u) => { const im = $("img", c); if (im) im.src = u; }).catch(() => {}); } } }
    catch (e) { P.dirty = true; if (!(e instanceof Locked)) toast("보정값을 저장하지 못했어요: " + e.message, true); }
  }
  function commit(msg) {         // 한 번의 조작이 끝났을 때: 기록 + 제대로 그리기 + 저장 예약
    const snap = JSON.stringify(P.params);
    if (snap === P.hist[P.hist.length - 1]) { request(false); return; }
    P.hist.push(snap); if (P.hist.length > 50) P.hist.shift();
    P.redo = []; P.dirty = true;
    clearTimeout(saveT); saveT = setTimeout(saveNow, 1200);
    request(false); if (P.view !== "edit") loadOrig();
    loadUI(); if (msg) status(msg);
  }
  function setParams(p, msg) { P.params = p; commit(msg); loadUI(); }
  function step(back) {
    const from = back ? P.hist : P.redo;
    if (back ? from.length < 2 : !from.length) return;
    if (back) { P.redo.push(P.hist.pop()); P.params = JSON.parse(P.hist[P.hist.length - 1]); } else { const s = P.redo.pop(); P.hist.push(s); P.params = JSON.parse(s); }
    P.dirty = true; clearTimeout(saveT); saveT = setTimeout(saveNow, 1200); P.origKey = "";
    request(false); if (P.view !== "edit") loadOrig(); loadUI(); status(back ? "실행 취소" : "다시 실행");
  }
  async function select(id) {
    if (P.cur === id) return;
    await saveNow();
    const p = P.d.photos.find((x) => x.id === id);
    if (!p) return;
    P.cur = id; P.params = JSON.parse(JSON.stringify(p.params || {})); P.hist = [JSON.stringify(P.params)]; P.redo = []; P.crop = false; P.pick = false; P.origKey = ""; P.preset = p.edited ? "사용자 보정" : "무보정";
    store.set("photoCur", id);
    $$(".pg-fcard").forEach((c) => c.classList.toggle("sel", c.dataset.id === id));
    q("#pgCrop").hidden = true; loadUI(); request(false); if (P.view !== "edit") loadOrig();
    status(`${p.name} · ${p.w}×${p.h}${p.raw ? " · RAW" : ""}`);
  }

  // ---- 자르기 틀 ----
  const cropRect = () => (P.params.crop || [0, 0, 1, 1]).slice();
  function placeCrop() { const [x, y, w, h] = cropRect(), b = q("#pgBox"); if (!b) return; b.style.left = x * 100 + "%"; b.style.top = y * 100 + "%"; b.style.width = w * 100 + "%"; b.style.height = h * 100 + "%"; }
  function ratioValue() {
    const c = q("#pgCanvas"), A = c.clientWidth / Math.max(1, c.clientHeight);
    if (P.ratio === "free") return 0;
    if (P.ratio === "orig") return 1;
    const [a, b] = P.ratio.split(":").map(Number);
    return (a / b) / A;
  }
  function applyRatio() {
    const r = ratioValue(); if (!r) return;
    let [x, y, w, h] = cropRect(); const cx = x + w / 2, cy = y + h / 2;
    if (w / h > r) w = h * r; else h = w / r;
    if (w > 1) { w = 1; h = w / r; } if (h > 1) { h = 1; w = h * r; }
    x = Math.min(1 - w, Math.max(0, cx - w / 2)); y = Math.min(1 - h, Math.max(0, cy - h / 2));
    P.params.crop = [x, y, w, h]; placeCrop();
  }
  function cropDrag(e) {
    const box = q("#pgBox"), handle = e.target.dataset.h, c = q("#pgCanvas").getBoundingClientRect();
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
        if (r) { let nh = w / r; if ((handle.includes("n") ? y1 - nh < 0 : y + nh > 1)) { nh = handle.includes("n") ? y1 : 1 - y; w = nh * r; if (handle.includes("w")) x = x1 - w; } if (handle.includes("n")) y = y1 - nh; h = nh; }
      }
      P.params.crop = [x, y, w, h]; placeCrop();
    };
    const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); window.removeEventListener("pointercancel", up); const cr = P.params.crop; if (cr && cr[2] > 0.995 && cr[3] > 0.995) delete P.params.crop; };
    window.addEventListener("pointermove", move); window.addEventListener("pointerup", up); window.addEventListener("pointercancel", up);
  }
  function toggleCrop() {
    P.crop = !P.crop; P.origKey = "";
    q("#pgCrop").hidden = !P.crop;
    if (P.crop) { if (!P.params.crop) P.params.crop = [0.1, 0.1, 0.8, 0.8]; placeCrop(); request(false); status("틀을 끌어 자를 범위를 정한 뒤 '자르기 적용'을 누르세요"); loadUI(); }
    else commit("자르기 적용");
    loadUI();
  }

  // =====================================================================
  // 창(원본의 모달)
  // =====================================================================
  function modal(html, wide, mount, onClose) {
    const ov = q("#pgOv");
    ov.innerHTML = `<div class="pg-modal ${wide ? "wide" : ""}" role="dialog" aria-modal="true">${html}</div>`; ov.hidden = false;
    ov._close = onClose || null;
    if (mount) mount($(".pg-modal", ov));
  }
  function closeModal(silent) { const ov = q("#pgOv"); if (!ov || ov.hidden) return; const f = ov._close; ov._close = null; ov.hidden = true; ov.innerHTML = ""; if (f && !silent) f(); }
  const pvUrls = [];
  async function pvRender(params, size) { const u = URL.createObjectURL(await (await call("POST", `/api/photo/${P.cur}/render`, { params: Object.assign({}, params, geomOnly(P.params)), size })).blob()); pvUrls.push(u); return u; }
  function freePv() { pvUrls.splice(0).forEach((u) => URL.revokeObjectURL(u)); }

  // ---- 프리셋 갤러리 ----
  function gallery() {
    if (!P.cur) { status("사진을 먼저 열어주세요"); return; }
    let sel = P.d.presets.find((p) => p.name === P.preset) || P.d.presets[0], str = 100, timer = 0;
    const id = P.cur;
    modal(`<div class="mh"><h3>프리셋 고르기</h3><div class="sub">내 사진에 적용된 모습을 보고 고르세요 · 강도로 효과의 세기를 조절할 수 있어요</div></div>
      <div class="mb"><div><div class="pg-pv" id="gPv"><i class="pg-spin"></i></div>
          <div class="lbl">강도</div><div class="pg-strength"><input type="range" id="gStr" min="0" max="150" step="5" value="100" aria-label="강도"><b id="gStrV">100%</b></div>
          <div class="lbl" id="gRecL" hidden>이 사진 추천</div><div class="pg-recs" id="gRecs"></div></div>
        <div><div class="lbl">기본 프리셋</div><div class="pg-pgrid" id="gBuiltin"></div>
          <div class="lbl between">내 프리셋<span><button type="button" class="pg-btn small" id="gImport" title="공유받은 프리셋 파일(.json) 불러오기">가져오기</button><button type="button" class="pg-btn small" id="gExport" title="내 프리셋을 파일로 저장해 공유">공유 파일 저장</button></span></div>
          <div class="pg-pgrid" id="gUser"></div><div class="pg-hint" id="gUserEmpty">아직 없어요. 현재 보정을 저장하거나 🖼 참고 사진으로 만들어 보세요.</div>
          <div class="pg-row" id="gMine" hidden><button type="button" class="pg-btn small" id="gRename">이름 바꾸기</button><button type="button" class="pg-btn small" id="gDelete">삭제</button></div></div></div>
      <div class="mf"><button type="button" class="pg-btn" id="gSave">＋ 현재 보정을 프리셋으로 저장</button><span class="pg-spacer"></span>
        <button type="button" class="pg-btn secondary" id="gCancel">취소</button><button type="button" class="pg-btn secondary" id="gAll">모든 사진에 적용</button><button type="button" class="pg-btn primary" id="gApply">이 사진에 적용</button></div>`, true, (m) => {
      const preview = () => { clearTimeout(timer); timer = setTimeout(async () => { try { const u = await pvRender(blend(sel.params, str / 100), "fast"); if ($("#gPv", m)) $("#gPv", m).innerHTML = `<img src="${u}" alt=""><span class="cap">${esc(sel.name)}</span>`; } catch (e) { /* 미리보기만 못 보여 줌 */ } }, 120); };
      const cell = (p) => `<button type="button" class="pg-pcell ${p.id === sel.id ? "on" : ""}" data-p="${esc(p.id)}"><img alt=""><span>${esc(p.name)}</span></button>`;
      const grids = async () => {
        const mine = P.d.presets.filter((p) => p.mine);
        $("#gBuiltin", m).innerHTML = P.d.presets.filter((p) => !p.mine).map(cell).join(""); $("#gUser", m).innerHTML = mine.map(cell).join("");
        $("#gUserEmpty", m).hidden = !!mine.length; $("#gMine", m).hidden = !sel.mine;
        for (const p of P.d.presets) { const im = $$(".pg-pcell", m).find((c) => c.dataset.p === p.id); if (!im || P.cur !== id) return; try { $("img", im).src = await pvRender(p.params, "tiny"); } catch (e) { return; } }
      };
      const pick = (p) => { sel = p; $$(".pg-pcell", m).forEach((c) => c.classList.toggle("on", c.dataset.p === p.id)); $("#gMine", m).hidden = !p.mine; preview(); };
      m.addEventListener("click", (e) => { const c = e.target.closest(".pg-pcell,[data-rec]"); if (c) { const p = P.d.presets.find((x) => x.id === (c.dataset.p || c.dataset.rec)); if (p) pick(p); } });
      $("#gStr", m).oninput = (e) => { str = Number(e.target.value); $("#gStrV", m).textContent = str + "%"; preview(); };
      const take = () => Object.assign(blend(sel.params, str / 100));
      $("#gApply", m).onclick = () => { P.preset = sel.name; closeModal(); setParams(Object.assign(take(), keepOwn()), "프리셋 적용: " + sel.name); };
      $("#gAll", m).onclick = async (e) => { if (!confirmTap(e.currentTarget, "한 번 더 누르면 모두 바꿔요")) return; const np = take(), name = sel.name; closeModal(); P.preset = name; setParams(Object.assign({}, np, keepOwn()), "프리셋 적용: " + name); await applyAll(np); };
      $("#gCancel", m).onclick = () => closeModal();
      $("#gSave", m).onclick = () => nameModal("프리셋 저장", "현재 보정값을 프리셋으로 저장합니다 (자르기/회전 제외)", "", async (name) => { P.d.presets = (await (await call("POST", "/api/photo/preset", { name, params: P.params })).json()).presets; P.preset = name; status("프리셋 저장: " + name); gallery(); });
      $("#gRename", m).onclick = () => { const old = sel.name; nameModal("프리셋 이름 바꾸기", "이름만 바뀝니다", old, async (name) => { P.d.presets = (await (await call("POST", "/api/photo/preset", { name, old })).json()).presets; if (P.preset === old) P.preset = name; gallery(); }); };
      $("#gDelete", m).onclick = async (e) => { if (!confirmTap(e.currentTarget, "한 번 더")) return; try { P.d.presets = (await (await call("POST", "/api/photo/preset/delete", { name: sel.name })).json()).presets; sel = P.d.presets[0]; grids(); preview(); } catch (err) { toast(err.message, true); } };
      $("#gExport", m).onclick = () => { const o = {}; P.d.presets.filter((p) => p.mine).forEach((p) => { o[p.name] = p.params; }); if (!Object.keys(o).length) { toast("저장할 내 프리셋이 없어요", true); return; } shareFile(new Blob([JSON.stringify({ kind: "photograder-presets", presets: o }, null, 1)], { type: "application/json" }), "photograder_presets.json"); };
      $("#gImport", m).onclick = () => { const inp = document.createElement("input"); inp.type = "file"; inp.accept = ".json,application/json"; inp.onchange = async () => {
        try { const d = JSON.parse(await inp.files[0].text()), src = d.presets || d; let n = 0; for (const name of Object.keys(src)) { if (src[name] && typeof src[name] === "object") { try { P.d.presets = (await (await call("POST", "/api/photo/preset", { name, params: src[name] })).json()).presets; n++; } catch (e) { /* 같은 이름의 기본 프리셋 등은 건너뛴다 */ } } } toast(`프리셋 ${n}개를 가져왔어요`); grids(); }
        catch (e) { toast("프리셋 파일을 읽지 못했어요", true); } }; inp.click(); };
      grids(); preview();
      const rec = (items) => { $("#gRecL", m).hidden = !items.length; $("#gRecs", m).innerHTML = items.map((r) => `<button type="button" class="pg-rec" data-rec="${esc(r.id)}">${esc(r.id.slice(2))} <small>${esc(r.why)}</small></button>`).join(""); };
      if (P.rec[id]) rec(P.rec[id]); else GET(`/api/photo/${id}/suggest`).then((r) => { P.rec[id] = r.items || []; if ($("#gRecs", m)) rec(P.rec[id]); }).catch(() => {});
    }, freePv);
  }
  function nameModal(title, sub, value, done) {
    modal(`<h3>${title}</h3><div class="sub">${sub}</div><div class="pg-field"><label for="pgName">이름</label><input type="text" id="pgName" maxlength="30" value="${esc(value)}" placeholder="예: 실내 고감도" autocomplete="off"></div>
      <div class="btns"><button type="button" class="pg-btn secondary" id="pgNo">취소</button><button type="button" class="pg-btn primary" id="pgYes">저장</button></div>`, false, (m) => {
      $("#pgNo", m).onclick = () => closeModal();
      $("#pgYes", m).onclick = async () => { const name = $("#pgName", m).value.replace(/\s+/g, " ").trim(); if (!name) { toast("이름이 비었습니다", true); return; } try { await done(name); } catch (e) { if (!(e instanceof Locked)) toast(e.message, true); } };
      $("#pgName", m).focus();
    });
  }
  async function applyAll(np) {  // 원본의 '전체 적용': 색·톤 값만 모든 사진에(자르기·회전은 사진마다 그대로)
    await saveNow();
    let n = 0;
    for (const p of P.d.photos) {
      if (p.id === P.cur) continue;
      status(`전체 적용 중 ${++n} / ${P.d.photos.length - 1}`);
      try { const r = await (await call("PUT", `/api/photo/${p.id}`, { params: Object.assign({}, np, keepOwn(p.params)) })).json(); P.d.photos[P.d.photos.findIndex((x) => x.id === p.id)] = r.photo; } catch (e) { if (e instanceof Locked) return; }
    }
    drawFilm(); status(`현재 설정을 ${n + 1}장에 적용했습니다`);
  }

  // ---- 참고 사진으로 프리셋 만들기 ----
  function refModal() {
    if (!P.cur) { status("사진을 먼저 열어주세요"); return; }
    const before = JSON.stringify(P.params);
    let res = null, str = 100, used = false;
    modal(`<div class="mh"><h3>참고 사진으로 프리셋 만들기</h3><div class="sub">마음에 드는 사진을 넣으면, 그 색감·톤을 분석해 내 사진에 맞는 프리셋을 제안합니다</div></div>
      <div class="mb"><div><div class="pg-pair"><div><div class="lbl">참고 사진</div><button type="button" class="pg-drop" id="rDrop"><b>사진 선택</b>눌러서 참고할 사진을 고르세요</button></div>
            <div><div class="lbl">내 사진 (제안 적용)</div><div class="pg-pv" id="rPv"><div class="none">참고 사진을 넣으면<br>결과가 여기에 나옵니다</div></div></div></div>
          <div class="lbl">강도</div><div class="pg-strength"><input type="range" id="rStr" min="0" max="150" step="5" value="100" disabled aria-label="강도"><b id="rStrV">100%</b></div></div>
        <div><div id="rState"><div class="lbl">분석 결과</div><div class="pg-hint">사진의 밝기·대비·색온도·채도·그림자/하이라이트 색감·필름 느낌을 분석합니다.</div></div>
          <div id="rResult" hidden><div class="lbl">닮은 정도</div><div class="pg-meter"><span id="rBefore">0%</span><div class="bar"><i id="rBar"></i></div><b id="rAfter">0%</b></div>
            <div class="lbl">이런 느낌이에요</div><div class="pg-chips" id="rTraits"></div><div class="lbl">프리셋 이름</div><div class="pg-field"><input type="text" id="rName" maxlength="30" aria-label="프리셋 이름"></div>
            <div class="pg-hint">사진 내용이 많이 다르면 색이 과하게 따라올 수 있어요. 강도로 조절하고, 적용 후 세부 값을 다듬으세요.</div></div></div></div>
      <div class="mf"><span class="pg-spacer"></span><button type="button" class="pg-btn secondary" id="rCancel">닫기</button><button type="button" class="pg-btn secondary" id="rApply" disabled>이 사진에만 적용</button><button type="button" class="pg-btn primary" id="rSave" disabled>프리셋으로 저장</button></div>`, true, (m) => {
      const show = async () => { try { $("#rPv", m).innerHTML = `<img src="${await pvRender(blend(res.params, str / 100), "fast")}" alt="">`; } catch (e) { /* 미리보기만 못 보여 줌 */ } };
      $("#rDrop", m).onclick = () => { const inp = document.createElement("input"); inp.type = "file"; inp.accept = "image/*"; inp.onchange = async () => {
        const f = (inp.files || [])[0]; if (!f) return;
        $("#rState", m).innerHTML = `<div class="pg-busy"><i class="pg-spin"></i>참고 사진을 분석하는 중</div>`; $("#rDrop", m).innerHTML = `<img src="${URL.createObjectURL(f)}" alt="참고 사진">`;
        const fd = new FormData(); fd.append("file", f, f.name);
        try { res = await (await call("POST", `/api/photo/${P.cur}/match`, fd, true)).json(); } catch (e) { $("#rState", m).innerHTML = `<div class="pg-err">${esc(e.message)}</div>`; return; }
        if (!$("#rResult", m)) return;
        $("#rState", m).hidden = true; $("#rResult", m).hidden = false; $("#rBefore", m).textContent = res.before + "%"; $("#rAfter", m).textContent = res.after + "%"; $("#rBar", m).style.width = Math.max(2, Math.min(100, res.after)) + "%";
        $("#rTraits", m).innerHTML = res.traits.map((t) => `<span>${esc(t)}</span>`).join("") || "<span>큰 차이 없음</span>"; $("#rName", m).value = res.name;
        ["#rStr", "#rApply", "#rSave"].forEach((s) => { $(s, m).disabled = false; }); show();
      }; inp.click(); };
      $("#rStr", m).oninput = (e) => { str = Number(e.target.value); $("#rStrV", m).textContent = str + "%"; }; $("#rStr", m).onchange = show;
      const apply = (name) => { used = true; P.preset = name; closeModal(); setParams(Object.assign(blend(res.params, str / 100), keepOwn()), "참고 사진 색감 적용: " + name); };
      $("#rApply", m).onclick = () => apply($("#rName", m).value.trim() || "참고 스타일");
      $("#rSave", m).onclick = async () => { const name = $("#rName", m).value.replace(/\s+/g, " ").trim(); if (!name) { toast("이름이 비었습니다", true); return; } try { P.d.presets = (await (await call("POST", "/api/photo/preset", { name, params: blend(res.params, str / 100) })).json()).presets; apply(name); } catch (e) { if (!(e instanceof Locked)) toast(e.message, true); } };
      $("#rCancel", m).onclick = () => closeModal();
    }, () => { freePv(); if (!used && JSON.stringify(P.params) !== before) { P.params = JSON.parse(before); request(false); } });
  }

  // ---- 내보내기 ----
  function exportModal() {
    if (!P.d.photos.length) { status("내보낼 사진이 없습니다"); return; }
    const o = Object.assign({ target: "cur", fmt: "JPG", quality: 95, resize: 0, size_cap: false, suffix: "_edited", wm_text: "", wm_pos: "우측 하단", wm_op: 60, shot_info: false }, store.get("photoExport", {}));
    modal(`<h3>내보내기</h3><div class="sub">보정 결과를 JPG/PNG 파일로 저장합니다</div>
      <div class="pg-field"><label>대상</label><div class="radio"><label><input type="radio" name="target" value="all" ${o.target === "all" ? "checked" : ""}>전체 (${P.d.photos.length}장)</label><label><input type="radio" name="target" value="cur" ${o.target === "all" ? "" : "checked"}>현재 사진만</label></div></div>
      <div class="pg-field"><label for="eFmt">형식 / 품질</label><select id="eFmt" style="flex:0 0 84px"><option ${o.fmt === "JPG" ? "selected" : ""}>JPG</option><option ${o.fmt === "PNG" ? "selected" : ""}>PNG</option></select><input type="number" id="eQuality" min="70" max="100" value="${o.quality}" inputmode="numeric" aria-label="품질"></div>
      <div class="pg-field"><label for="eResize">리사이즈</label><input type="number" id="eResize" min="0" max="10000" step="200" value="${o.resize}" inputmode="numeric"><span class="unit">px (긴 변, 0=원본)</span></div>
      <div class="pg-field"><label>파일 크기</label><label class="pg-chk"><input type="checkbox" id="eCap" ${o.size_cap ? "checked" : ""}>20MB 이하로 내보내기</label></div>
      <div class="pg-field"><label for="eSuffix">파일명 접미사</label><input type="text" id="eSuffix" value="${esc(o.suffix)}" maxlength="20" autocomplete="off"></div>
      <div class="pg-field"><label for="eWm">워터마크</label><input type="text" id="eWm" value="${esc(o.wm_text)}" maxlength="60" placeholder="비우면 없음" autocomplete="off"></div>
      <div class="pg-field"><label for="eWmPos">위치 / 투명도</label><select id="eWmPos" style="flex:0 0 110px">${["우측 하단", "좌측 하단", "중앙", "우측 상단", "좌측 상단"].map((v) => `<option ${v === o.wm_pos ? "selected" : ""}>${v}</option>`).join("")}</select><input type="number" id="eWmOp" min="10" max="100" value="${o.wm_op}" inputmode="numeric" aria-label="투명도"><span class="unit">%</span></div>
      <div class="pg-field"><label>촬영 정보</label><label class="pg-chk"><input type="checkbox" id="eInfo" ${o.shot_info ? "checked" : ""}>우측 하단에 바디·렌즈·노출 표기</label></div>
      <div class="pg-strip">저장 위치 : 휴대폰으로 내려받기 &nbsp;(원본은 수정되지 않습니다 · 전체는 zip 한 개)</div>
      <div class="btns"><button type="button" class="pg-btn secondary" id="eCancel">취소</button><button type="button" class="pg-btn primary" id="eStart">내보내기 시작</button></div>`, false, (m) => {
      $("#eCancel", m).onclick = () => closeModal();
      $("#eStart", m).onclick = async (e) => {
        const all = $("input[name=target]:checked", m).value === "all", btn = e.currentTarget;
        const opt = { fmt: $("#eFmt", m).value, quality: Number($("#eQuality", m).value) || 95, resize: Number($("#eResize", m).value) || 0, size_cap: $("#eCap", m).checked, suffix: $("#eSuffix", m).value.trim(), wm_text: $("#eWm", m).value.trim(), wm_pos: $("#eWmPos", m).value, wm_op: Number($("#eWmOp", m).value) || 60, shot_info: $("#eInfo", m).checked };
        store.set("photoExport", Object.assign({ target: all ? "all" : "cur" }, opt));
        if (!all && !P.cur) { toast("사진을 먼저 열어주세요", true); return; }
        btn.disabled = true; btn.textContent = "변환 중…"; status(all ? `${P.d.photos.length}장 변환 중 (원본 크기라 시간이 걸립니다)` : "변환 중");
        try {
          await saveNow();
          const res = all ? await call("POST", "/api/photo/export", { ids: P.d.photos.map((p) => p.id).reverse(), opt }) : await call("POST", `/api/photo/${P.cur}/export`, { params: P.params, opt }), blob = await res.blob();
          closeModal(); status("내보내기 완료"); await shareFile(blob, fileName(res, all ? "photos_edited.zip" : "photo_edited.jpg"));
        } catch (err) { if (!(err instanceof Locked)) toast(err.message, true); btn.disabled = false; btn.textContent = "내보내기 시작"; status("내보내기 실패"); }
      };
    });
  }
  function helpModal() {
    modal(`<h3>사용법</h3><div class="sub">PhotoGrader Pro ${esc(P.d.version || "")} · 우석World판</div>
      <div class="pg-help"><p><b>1. 사진 추가</b> ＋ 파일 추가로 올립니다. 원본은 노트북에 그대로 보관돼요.</p><p><b>2. 색감 고르기</b> 🎨 프리셋에서 내 사진에 입힌 모습을 보고 고르거나, 🖼 참고 사진으로 비슷한 색감을 만듭니다.</p>
      <p><b>3. 다듬기</b> ✨ 자동 보정 후 아래 카드의 슬라이더로 조절합니다. 슬라이더를 두 번 누르면 처음 값, 라벨을 누르면 설명이 상태줄에 나와요.</p>
      <p><b>4. 비교</b> 보기에서 보정 · 분할 · 원본을 바꿉니다.</p><p><b>5. 내보내기</b> 📤 내보내기로 휴대폰에 저장합니다. '전체 적용'은 현재 색·톤 설정을 모든 사진에 입힙니다.</p></div>
      <div class="btns"><button type="button" class="pg-btn primary" id="hOk">확인</button></div>`, false, (m) => { $("#hOk", m).onclick = () => closeModal(); });
  }
  function slidePick() {
    if (P.d.photos.length < 2) { status("슬라이드쇼는 사진이 2장 이상 있어야 합니다"); return; }
    const on = new Set(P.d.photos.map((p) => p.id));
    modal(`<h3>슬라이드쇼에 넣을 사진</h3><div class="sub">뺄 사진을 눌러 해제하세요 · 먼저 올린 사진부터 나옵니다</div><div class="pg-pgrid four" id="sGrid">${P.d.photos.map((p) => `<button type="button" class="pg-pcell on" data-id="${p.id}"><img data-th="${p.id}" alt="${esc(p.name)}"></button>`).join("")}</div>
      <div class="btns"><button type="button" class="pg-btn secondary" id="sNo">취소</button><button type="button" class="pg-btn primary" id="sGo">다음</button></div>`, false, (m) => {
      $$("img[data-th]", m).forEach((im) => { const p = P.d.photos.find((x) => x.id === im.dataset.th); thumbUrl(p).then((u) => { im.src = u; }).catch(() => {}); });
      $("#sGrid", m).onclick = (e) => { const c = e.target.closest(".pg-pcell"); if (!c) return; if (on.has(c.dataset.id)) on.delete(c.dataset.id); else on.add(c.dataset.id); c.classList.toggle("on", on.has(c.dataset.id)); $("#sGo", m).disabled = on.size < 2; };
      $("#sNo", m).onclick = () => closeModal();
      $("#sGo", m).onclick = async () => { await saveNow(); closeModal(); slideSheet(P.d.photos.filter((p) => on.has(p.id)).map((p) => p.id).reverse()); };
    });
  }
  const seg = (name, rows, pick) => `<div class="pe-seg">${rows.map(([v, t]) => `<label><input type="radio" name="${name}" value="${v}" ${String(v) === String(pick) ? "checked" : ""}><span>${t}</span></label>`).join("")}</div>`;
  // ---- 슬라이드쇼 영상: 고른 사진 + 분위기에 맞춰 고른 배경음악 ----
  function slideSheet(ids, last) {
    if (!P.d.status.video) { toast("영상 도구가 아직 없어요. 아래 상태줄의 '추가 도구 설치'를 먼저 눌러 주세요", true); return; }
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

  // ---- 사진 올리기·엔진 넣기 ----
  function addPhotos() {
    const inp = document.createElement("input");
    inp.type = "file"; inp.multiple = true;
    inp.accept = window.matchMedia("(pointer:coarse)").matches ? "image/*" : "image/*,.cr2,.cr3,.nef,.arw,.srw,.orf,.rw2,.dng,.raf,.pef";
    inp.onchange = async () => {
      const files = Array.from(inp.files || []);
      if (!files.length) return;
      let last = ""; const bad = [];
      for (let i = 0; i < files.length; i++) {
        status(`${i + 1} / ${files.length}장 올리는 중`);
        const fd = new FormData(); fd.append("file", files[i], files[i].name);
        try { const r = await (await call("POST", "/api/photo/upload", fd, true)).json(); if (r.added.length) last = r.added[0].id; bad.push(...r.errors); }
        catch (e) { if (e instanceof Locked) return; bad.push(`${files[i].name}: ${e.message}`); }
      }
      if (bad.length) toast(`안 된 사진 ${bad.length}장: ${bad[0]}`, true);
      await reload(last);
      status(`${files.length - bad.length}장 불러옴`);
    };
    inp.click();
  }
  function engineUpload() {
    const inp = document.createElement("input");
    inp.type = "file"; inp.accept = ".zip,application/zip";
    inp.onchange = async () => {
      const f = (inp.files || [])[0];
      if (!f) return;
      const fd = new FormData(); fd.append("file", f, f.name);
      toast("엔진 파일을 올리는 중");
      try { await call("POST", "/api/photo/engine", fd, true); toast("엔진을 넣었어요"); await reload(); }
      catch (e) { if (!(e instanceof Locked)) toast(e.message, true); }
    };
    inp.click();
  }

  // ---- 누름 ----
  async function onClick(e) {
    const el = (s) => e.target.closest(s);
    let b;
    if (e.target.id === "pgOv") return closeModal();
    if ((b = el(".pg-fcard"))) return select(b.dataset.id);
    if ((b = el("#pgSeg [data-v]"))) return setView(b.dataset.v);
    if ((b = el("[data-band]"))) { P.band = Number(b.dataset.band); $$(".pg-hslchip").forEach((c) => c.classList.toggle("on", c === b)); return loadUI(); }
    if ((b = el("[data-ratio]"))) { P.ratio = b.dataset.ratio; $$("[data-ratio]").forEach((c) => c.classList.toggle("active", c === b)); return applyRatio(); }
    if (!(b = el("[data-a]"))) return;
    const a = b.dataset.a;
    if (a === "add") return addPhotos();
    if (a === "engine") return engineUpload();
    if (a === "setup") { try { P.d.status = (await (await call("POST", "/api/photo/setup")).json()).status; } catch (err) { toast(err.message, true); } return reload(); }
    if (a === "help") return helpModal();
    if (a === "export") return exportModal();
    if (a === "slide") return slidePick();
    if (!P.cur) { status("사진을 먼저 열어주세요"); return; }
    if (a === "gallery") return gallery();
    if (a === "ref") return refModal();
    if (a === "undo" || a === "redo") return step(a === "undo");
    if (a === "sync") { if (!confirmTap(b, "한 번 더 누르면 적용")) return; const np = Object.assign({}, P.params); OWN.forEach((k) => delete np[k]); return applyAll(np); }
    if (a === "reset") { P.preset = "무보정"; return setParams(geomOnly(P.params), "초기화"); }      // 원본처럼 자르기·회전은 남긴다
    if (a === "auto") {            // 원본처럼 지금 값 위에 분석 결과만 덮어쓴다
      const r = await (await call("POST", `/api/photo/${P.cur}/auto`, { params: P.params })).json(), p = Object.assign({}, P.params);
      delete p.temp; delete p.tint;
      return setParams(Object.assign(p, r.params), "자동 보정 적용 (노출/블랙/화이트/WB 분석)");
    }
    if (a === "wbpick") { P.pick = !P.pick; loadUI(); status(P.pick ? "미리보기에서 '회색이어야 하는 지점'을 누르세요" : "스포이드 해제"); if (P.pick) q("#pgCenter").scrollIntoView({ block: "nearest" }); return; }
    if (a === "wbreset") { const p = Object.assign({}, P.params); delete p.wb_gains; delete p.temp; delete p.tint; return setParams(p, "WB 초기화"); }
    if (a === "crop") return toggleCrop();
    if (a === "del") {
      if (!confirmTap(b, "한 번 더 누르면 삭제")) return;
      const id = P.cur; P.dirty = false; P.cur = "";
      await call("DELETE", `/api/photo/${id}`); toast("삭제했어요");
      return reload();
    }
    const p = Object.assign({}, P.params);
    if (a === "rotL" || a === "rotR") { p.rot = ((p.rot || 0) + (a === "rotR" ? 90 : 270)) % 360; if (!p.rot) delete p.rot; delete p.crop; }
    if (a === "flip") { if (p.flip) delete p.flip; else p.flip = true; if (p.crop) p.crop = [1 - p.crop[0] - p.crop[2], p.crop[1], p.crop[2], p.crop[3]]; }
    if (a === "cropreset") { delete p.crop; delete p.angle; P.ratio = "free"; if (P.crop) { P.crop = false; q("#pgCrop").hidden = true; } }
    P.origKey = "";
    return setParams(p, a === "cropreset" ? "자르기 초기화" : "변형 적용");
  }
  async function wbPick(e) {      // 스포이드(원본 doWbPick): 누른 곳이 회색이 되게, 색온도·색조는 0으로
    const c = q("#pgCanvas").getBoundingClientRect(), u = (e.clientX - c.left) / c.width, v = (e.clientY - c.top) / c.height;
    const send = Object.assign({}, P.params); if (P.crop) delete send.crop;
    const r = await (await call("POST", `/api/photo/${P.cur}/wb`, { params: send, u, v })).json(), p = Object.assign({}, P.params);
    delete p.temp; delete p.tint; p.wb_gains = r.wb_gains; P.pick = false;
    setParams(p, "스포이드 화이트밸런스 적용");
  }
  async function reload(pick) {
    P.d = await GET("/api/photo");
    draw(pick);
  }
  function draw(pick) {
    const host = $("#pgApp");
    if (!host) return;
    const keep = pick || P.cur || store.get("photoCur", "");
    host.innerHTML = shell();
    drawFilm();
    clearTimeout(P.poll);
    if (P.d.status.setup.running) P.poll = setTimeout(() => { if ($("#pgApp")) reload().catch(() => {}); }, 2500);
    if (!P.d.status.ready) { P.cur = ""; return; }
    buildSections();
    q("#pgCrop").addEventListener("pointerdown", cropDrag);
    q("#pgCanvas").addEventListener("click", (e) => { if (P.pick && P.cur) wbPick(e).catch((err) => { if (!(err instanceof Locked)) toast(err.message, true); }); });
    q("#pgSplit").addEventListener("pointerdown", (e) => {
      e.preventDefault(); const c = q("#pgCanvas").getBoundingClientRect();
      const mv = (ev) => setSplit((ev.clientX - c.left) / c.width), end = () => { window.removeEventListener("pointermove", mv); window.removeEventListener("pointerup", end); window.removeEventListener("pointercancel", end); };
      window.addEventListener("pointermove", mv); window.addEventListener("pointerup", end); window.addEventListener("pointercancel", end);
    });
    const first = P.d.photos.find((p) => p.id === keep) || P.d.photos[0];
    P.cur = "";
    if (first) select(first.id).then(() => setView(P.view)); else loadUI();
  }
  window.addEventListener("resize", () => { if ($("#pgApp") && P.cur) fitCanvas(); });

  pages.photo = {
    async mount() {
      setTop("사진편집", {});
      if (P.cur && P.dirty) await saveNow();
      [P.url, P.origUrl].forEach((u) => u && URL.revokeObjectURL(u)); P.url = P.origUrl = null; freePv(); P.cur = ""; P.busy = false; P.want = null;
      P.d = await GET("/api/photo");
      view.innerHTML = `<div class="pg-app" id="pgApp"></div>`;
      const host = $("#pgApp");
      host.addEventListener("click", (e) => { onClick(e).catch((err) => { if (!(err instanceof Locked)) toast(err.message, true); }); });
      host.addEventListener("change", (e) => { const t = e.target; if (t.dataset.flag && P.cur) { if (t.checked) P.params[t.dataset.flag] = true; else delete P.params[t.dataset.flag]; commit(); } });
      draw();
    },
  };
})();
