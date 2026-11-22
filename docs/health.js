"use strict";
/* 건강관리 앱(우석World). 화면만 여기 있고, 기록은 노트북 서버(/api/health)에만 저장한다.
   개인 수치는 이 파일에 넣지 않는다: 앱에서 직접 넣거나 '자료 가져오기'로 넣는다. */
(function () {
  const IC = {
    home: '<path d="M3 11l9-7 9 7v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>',
    body: '<path d="M3 17l6-6 4 4 8-8"/><path d="M15 7h6v6"/>',
    gym: '<path d="M6 7v10M18 7v10M3 10v4M21 10v4M6 12h12"/>',
    chk: '<path d="M9 3h6v4H9z"/><path d="M5 5h4M15 5h4v16H5V5"/><path d="M9 13h6M12 10v6"/>',
  };
  const svg = (p, s = 20, w = 2) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${w}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${p}</svg>`;
  const GLOBE = svg('<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.8 3 2.8 15 0 18M12 3c-2.8 3-2.8 15 0 18"/>', 20, 1.8);
  const MORE = svg('<circle cx="5" cy="12" r="1.4"/><circle cx="12" cy="12" r="1.4"/><circle cx="19" cy="12" r="1.4"/>', 20, 2.4);
  const TICK = svg('<path d="M5 12l5 5L19 7"/>', 16, 3);
  const NEXT = svg('<path d="M9 6l6 6-6 6"/>', 18, 2);

  addApp({
    id: "health", name: "건강관리", desc: "체중, 운동, 검진", home: "#health", secs: ["health"],
    tabs: [["health/today", "홈", IC.home], ["health/body", "기록", IC.body], ["health/workout", "운동", IC.gym], ["health/checkup", "검진", IC.chk]],
    skin: { meta: "#F4F5F1", vars: { "--deep": "#17201B", "--deep2": "#24332B", "--bg": "#F4F5F1", "--hl": "#6FD39A", "--solid": "#17201B",
      "--on-deep": "#F4F5F1", "--on-deep2": "#B9C4BC", "--on-deep-strong": "#FFFFFF", "--sub-bg": "rgba(255,255,255,.08)" } },
    tile: { bg: "linear-gradient(150deg,#2A7A55 0%,#17201B 78%)", ink: "#FFFFFF", sub: "#C6D3CA", accent: "#6FD39A" },
    live: (d) => (d ? [`오늘 체크 ${d.checks}/${d.checks_total}`, d.next ? `${d.next.title} ${dday(d.next.dday)}` : `이번 주 운동 ${d.week_done}/${d.week_target}회`] : []),
    // 오른쪽 그림: 오늘 체크한 만큼 채워지는 고리
    art: (d) => {
      const k = d && d.checks_total ? Math.min(1, d.checks / d.checks_total) : 0, C = 2 * Math.PI * 44;
      return `<svg viewBox="0 0 120 120" fill="none" aria-hidden="true"><circle cx="60" cy="60" r="44" stroke="#fff" stroke-opacity=".16" stroke-width="12"/>
        <circle cx="60" cy="60" r="44" stroke="currentColor" stroke-width="12" stroke-linecap="round" stroke-dasharray="${(C * k).toFixed(1)} ${C.toFixed(1)}" transform="rotate(-90 60 60)"/>
        <path d="M36 62h12l6-14 10 26 6-12h14" stroke="#fff" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
    },
  });

  // ---------- 작은 도구 ----------
  const f1 = (v) => (v == null ? "–" : Number(v).toFixed(1));
  const md = (iso) => `${+iso.slice(5, 7)}/${+iso.slice(8, 10)}`;
  const dot = (iso) => iso.replace(/-/g, ".");
  const diff = (v) => (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(1);
  function dday(n) { return n > 0 ? `D-${n}` : n === 0 ? "D-DAY" : `D+${-n}`; }
  const send = (m, u, b) => api(m, u, b);
  const H = { d: null, sub: "today", tab: "weight", sel: "", week: "", busy: false, wtab: "week" };
  const SERIES = { weight: ["체중", "kg"], waist: ["허리", "cm"], fat: ["체지방", "%"] };

  const head = (sub, title, extra = "") => `<header class="hl-head"><div class="hl-head-t"><p>${esc(sub)}</p><h1>${esc(title)}</h1></div>
    <div class="hl-head-r">${extra}<a class="hl-ico" href="#world" aria-label="우석World 첫 화면으로">${GLOBE}</a></div></header>`;

  async function load() {
    H.d = await GET("/api/health" + (H.sub === "workout" && H.week ? `?week=${H.week}` : ""));
    return H.d;
  }
  function draw() {
    const d = H.d;
    if (!d || !$("#hlApp")) return;
    $("#hlApp").innerHTML = ({ today, body, workout, checkup }[H.sub] || today)(d);
    hydrate($("#hlApp"));
  }
  async function reload() { await load(); draw(); }
  // 누르자마자 화면부터 바꾸고 저장한다. 저장이 안 되면 서버 값으로 되돌린다
  async function save(fn, okMsg) {
    try { await fn(); if (okMsg) toast(okMsg); await reload(); return true; }
    catch (e) { if (!(e instanceof Locked)) toast(e.message, true); await reload().catch(() => {}); return false; }
  }

  // ---------- 홈 ----------
  function today(d) {
    const g = d.cfg.goal, L = d.body.latest, w = L.weight, wk = d.week, t = wk.days.find((x) => x.today) || {};
    const plan = d.cfg.plan.days[wk.days.indexOf(t)] || d.cfg.plan.days[0];
    const left = w && g.weight != null ? w.value - g.weight : null;
    const goalTxt = g.weight != null ? `목표 ${f1(g.weight)} kg${g.label ? ` · ${esc(g.label)}` : ""}` : "";
    const hero = w ? `<section class="hl-hero" aria-label="현재 체중과 목표">
        <div class="hl-hero-top"><span>현재 체중 <small>${md(w.date)}</small></span>${goalTxt ? `<span>${goalTxt}</span>` : `<button type="button" class="hl-link on-dark" data-act="goal">목표 정하기</button>`}</div>
        <div class="hl-hero-num"><b>${f1(w.value)}</b><i>kg</i>${left == null ? "" : `<em>${left > 0.05 ? `−${left.toFixed(1)} kg 남음` : "목표에 닿았어요"}</em>`}</div>
        ${d.body.progress == null ? "" : `<div class="hl-bar" role="progressbar" aria-label="목표까지 진행" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(d.body.progress * 100)}"><i style="width:${Math.max(2, d.body.progress * 100).toFixed(1)}%"></i></div>`}
        <div class="hl-hero-kv">
          <div><span>허리</span><b>${L.waist ? f1(L.waist.value) : "–"}<i> cm</i></b></div>
          <div><span>체지방률</span><b>${L.fat ? f1(L.fat.value) : "–"}<i> %</i></b></div>
          <div><span>이번 주 운동</span><b>${wk.done}<i> / ${wk.target}회</i></b></div>
        </div></section>`
      : `<section class="hl-hero hl-hero0"><b>아직 체중 기록이 없어요</b><span>아침 공복 체중부터 남겨 보세요. 목표까지 얼마나 왔는지 여기에 보여요.</span>
        <div class="hl-row2"><a class="hl-btn mint" href="#health/body">첫 기록 남기기</a><button type="button" class="hl-btn ghost" data-act="import">자료 가져오기</button></div></section>`;
    const rows = (d.med_slots || []).map((m) => ({ id: m.key, title: `${m.label} ${m.names.length}가지`, sub: m.names.join(", "), on: d.checks.includes(m.key) }))
      .concat([{ id: "gym", title: `운동 · ${plan.name}`, sub: plan.items.map((i) => i[0]).slice(0, 5).join(", "), on: !!t.done }])
      .concat(d.cfg.habits.map((h) => ({ id: h.id, title: h.title, sub: h.sub, on: d.checks.includes(h.id) })));
    const nx = d.follows.find((f) => f.next);
    return `${head(`${+d.today.slice(5, 7)}월 ${+d.today.slice(8, 10)}일 ${d.dow}요일`, "오늘의 건강",
      `${d.week_no ? `<span class="hl-chip">${d.week_no}주차</span>` : ""}<button type="button" class="hl-ico" data-act="manage" aria-label="목표·자료 관리">${MORE}</button>`)}
      ${hero}
      <section class="hl-sec"><div class="hl-sec-h"><h2>오늘 체크</h2><span>${rows.filter((r) => r.on).length} / ${rows.length} 완료</span></div>
        ${rows.map((r) => `<button type="button" class="hl-check" data-check="${esc(r.id)}" aria-pressed="${r.on}"><span class="hl-box">${r.on ? TICK : ""}</span>
          <span class="hl-check-t"><b>${esc(r.title)}</b>${r.sub ? `<small>${esc(r.sub)}</small>` : ""}</span></button>`).join("")}
      </section>
      ${nx ? `<a class="hl-next" href="#health/checkup"><b>${dday(nx.dday)}</b><span><strong>${esc(nx.title)} · ${dot(nx.date)}</strong>${nx.why ? `<small>${esc(nx.why)}</small>` : ""}</span>${NEXT}</a>` : ""}`;
  }

  // ---------- 기록 ----------
  function chart(rows, key, goal) {
    const pts = rows.filter((r) => r[key] != null).map((r) => ({ t: Date.parse(r.date), v: r[key], date: r.date }));
    const [name, unit] = SERIES[key];
    if (!pts.length) return `<div class="hl-none">아직 ${name} 기록이 없어요. 아래에서 오늘 수치를 넣어 보세요.</div>`;
    const W = 360, top = 30, bot = 128, x0 = 30, x1 = W - 30;
    let lo = Math.min(...pts.map((p) => p.v)), hi = Math.max(...pts.map((p) => p.v));
    if (goal != null) { lo = Math.min(lo, goal); hi = Math.max(hi, goal); }
    if (hi - lo < 1) { hi += 0.5; lo -= 0.5; }
    const t0 = pts[0].t, t1 = pts[pts.length - 1].t;
    const X = (p) => (t1 === t0 ? W / 2 : x0 + ((p.t - t0) / (t1 - t0)) * (x1 - x0));
    const Y = (v) => top + ((hi - v) / (hi - lo)) * (bot - top);
    const a = pts[0], b = pts[pts.length - 1], line = pts.map((p) => `${X(p).toFixed(1)},${Y(p.v).toFixed(1)}`).join(" ");
    const dots = (pts.length <= 16 ? pts.slice(0, -1) : [a]).map((p) => `<circle cx="${X(p).toFixed(1)}" cy="${Y(p.v).toFixed(1)}" r="4" class="hl-pt"/>`).join("");
    const lab = (p, anchor) => `<text x="${X(p).toFixed(1)}" y="${(Y(p.v) - 12).toFixed(1)}" text-anchor="${anchor}" class="hl-cv">${f1(p.v)}</text>
      <text x="${X(p).toFixed(1)}" y="150" text-anchor="${anchor}" class="hl-cd">${md(p.date)}</text>`;
    const gy = goal != null ? Y(goal) : null;
    return `<svg class="hl-chart" viewBox="0 0 ${W} 164" role="img" aria-label="${name} 추이: ${md(a.date)} ${f1(a.v)}${unit}에서 ${md(b.date)} ${f1(b.v)}${unit}${goal != null ? `, 목표 ${f1(goal)}${unit}` : ""}">
      ${[top, (top + bot) / 2, bot].map((y) => `<line x1="0" y1="${y}" x2="${W}" y2="${y}" class="hl-grid"/>`).join("")}
      ${gy == null ? "" : `<line x1="0" y1="${gy.toFixed(1)}" x2="${W}" y2="${gy.toFixed(1)}" class="hl-goal"/><text x="${W - 2}" y="${(gy - 6).toFixed(1)}" text-anchor="end" class="hl-cg">목표 ${f1(goal)}</text>`}
      ${pts.length > 1 ? `<polyline points="${line}" class="hl-line"/>` : ""}${dots}
      <circle cx="${X(b).toFixed(1)}" cy="${Y(b.v).toFixed(1)}" r="6" class="hl-pt last"/>
      ${pts.length > 1 ? lab(a, "start") : ""}${lab(b, pts.length > 1 ? "end" : "middle")}</svg>`;
  }
  function body(d) {
    const g = d.cfg.goal, L = d.body.latest, rows = d.body.rows;
    const gap = (k, unit) => (L[k] && g[k] != null ? (L[k].value - g[k] > 0.05 ? `−${(L[k].value - g[k]).toFixed(1)} ${unit}` : "달성") : "–");
    const prev = rows.filter((r) => r.weight != null).slice(-2);
    const change = prev.length === 2 ? `지난 기록보다 ${diff(prev[1].weight - prev[0].weight)} kg` : "";
    return `${head("아침 공복, 화장실 다녀온 뒤에 재요", "신체 기록", `<button type="button" class="hl-ico" data-act="goal" aria-label="목표 정하기">${MORE}</button>`)}
      <section class="hl-card"><div class="hl-tabs" role="tablist">${Object.keys(SERIES).map((k) =>
        `<button type="button" role="tab" data-tab="${k}" aria-selected="${H.tab === k}">${SERIES[k][0]}</button>`).join("")}${H.tab === "weight" && change ? `<span class="hl-tabs-n">${change}</span>` : ""}</div>
        ${chart(rows, H.tab, g[H.tab])}</section>
      <form class="hl-card" id="hlBody" novalidate><div class="hl-sec-h"><h2>수치 넣기</h2>
          <label class="hl-date"><span class="sr">날짜</span><input type="date" name="date" value="${d.today}" max="${d.today}"></label></div>
        <div class="hl-in3">
          <label>체중 kg<input name="weight" inputmode="decimal" autocomplete="off" placeholder="${L.weight ? f1(L.weight.value) : "0.0"}"></label>
          <label>허리 cm<input name="waist" inputmode="decimal" autocomplete="off" placeholder="${L.waist ? f1(L.waist.value) : "0.0"}"></label>
          <label>체지방 %<input name="fat" inputmode="decimal" autocomplete="off" placeholder="${L.fat ? f1(L.fat.value) : "0.0"}"></label>
        </div>
        <button type="submit" class="hl-btn green wide">기록 저장</button>
        <p class="hl-hint">잰 것만 넣으면 돼요. 같은 날 다시 넣으면 그 칸만 바뀌어요.</p></form>
      <section class="hl-sec"><div class="hl-sec-h"><h2 class="dim">목표까지</h2></div>
        <div class="hl-kpi">
          <div><span>체중${g.weight != null ? ` → ${f1(g.weight)}` : ""}</span><b>${gap("weight", "kg")}</b></div>
          <div><span>허리${g.waist != null ? ` → ${f1(g.waist)}` : ""}</span><b>${gap("waist", "cm")}</b></div>
          <div><span>BMI</span><b>${d.body.bmi != null ? d.body.bmi.toFixed(1) : "–"}</b></div>
        </div>
        ${g.weight == null && g.waist == null ? `<button type="button" class="hl-link" data-act="goal">목표를 정하면 남은 양이 보여요</button>` : ""}</section>
      ${rows.length ? `<section class="hl-card flush"><div class="hl-sec-h pad"><h2>지난 기록</h2><span>${rows.length}번</span></div>
        ${rows.slice(-12).reverse().map((r) => `<div class="hl-rec"><b>${md(r.date)}</b><span>${r.weight != null ? `${f1(r.weight)} kg` : ""}${r.waist != null ? `<i>허리 ${f1(r.waist)}</i>` : ""}${r.fat != null ? `<i>체지방 ${f1(r.fat)}</i>` : ""}</span>
          <button type="button" class="hl-del" data-delbody="${r.date}" aria-label="${md(r.date)} 기록 지우기">지우기</button></div>`).join("")}</section>` : ""}`;
  }

  // ---------- 운동 ----------
  function workout(d) {
    const wk = d.week, plan = d.cfg.plan;
    if (!wk.days.some((x) => x.date === H.sel)) H.sel = (wk.days.find((x) => x.today) || wk.days[0]).date;
    const i = wk.days.findIndex((x) => x.date === H.sel), day = wk.days[i], p = plan.days[i];
    const seg = `<div class="hl-seg2" role="tablist"><button type="button" role="tab" data-wtab="week" aria-selected="${H.wtab !== "gym"}">주간 루틴</button><button type="button" role="tab" data-wtab="gym" aria-selected="${H.wtab === "gym"}">기구 안내 ${GYM.length}</button></div>`;
    if (H.wtab === "gym") return `${head(plan.place || "헬스장", "기구 안내", "")}${seg}${gymList(d)}`;
    return `${head(`${md(wk.start)} – ${md(wk.end)}${plan.place ? ` · ${plan.place}` : ""}`, wk.this_week ? "이번 주 운동" : "지난 운동",
      `<button type="button" class="hl-ico" data-act="plan" aria-label="루틴 고치기">${MORE}</button>`)}
      ${seg}
      <div class="hl-wk"><button type="button" class="hl-ico line" data-wk="-7" aria-label="앞 주">${svg('<path d="M15 6l-6 6 6 6"/>', 18)}</button>
        <span>${wk.done} / ${wk.target}회 완료${wk.rpe_avg ? ` · 평균 힘듦 ${wk.rpe_avg}` : ""}</span>
        ${wk.this_week ? `<span class="hl-ico ghost"></span>` : `<button type="button" class="hl-ico line" data-wk="7" aria-label="다음 주">${NEXT}</button>`}</div>
      <div class="hl-days" role="tablist" aria-label="요일">${wk.days.map((x, k) => `<button type="button" role="tab" data-day="${x.date}" aria-selected="${x.date === H.sel}"
        aria-label="${x.dow}요일 ${esc(plan.days[k].name)}${x.done ? ", 완료" : ""}" class="${x.today ? "today" : ""}"><span>${x.dow}</span><b>${+x.date.slice(8, 10)}</b><i class="${x.done ? "on" : ""}"></i></button>`).join("")}</div>
      <section class="hl-card"><div class="hl-sec-h"><div><p class="hl-over">${day.dow}요일${day.today ? " · 오늘" : ""}</p><h2 class="big">${esc(p.name)}</h2></div>${p.time ? `<span class="hl-chip">${esc(p.time)}</span>` : ""}</div>
        <div class="hl-items">${p.items.map((it) => { const g = gymOf(it[0]); return g
          ? `<button type="button" data-gym="${g.id}" aria-label="${esc(it[0])} ${esc(it[1] || "")}, 기구 안내 보기"><span>${esc(it[0])}</span><b>${esc(it[1] || "")}</b>${NEXT}</button>`
          : `<div><span>${esc(it[0])}</span><b>${esc(it[1] || "")}</b></div>`; }).join("") || `<div><span class="dim">루틴이 비어 있어요</span></div>`}</div>
        <button type="button" class="hl-btn ${day.done ? "done" : "green"} wide" data-done="${day.done ? 0 : 1}" ${day.future ? "disabled" : ""}>${day.future ? "아직 오지 않은 날이에요" : day.done ? "완료됨 · 취소하려면 누르기" : p.rest ? "오늘 할 일 완료" : "운동 완료"}</button></section>
      <section class="hl-card"><div class="hl-sec-h"><h2>힘들었던 정도</h2><span>대화는 되고 노래는 힘들면 6~7</span></div>
        <div class="hl-rpe" role="group" aria-label="힘들었던 정도 1부터 10">${[1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map((v) =>
          `<button type="button" data-rpe="${v}" aria-pressed="${day.rpe === v}" ${day.future ? "disabled" : ""}>${v}</button>`).join("")}</div></section>`;
  }

  // ---------- 기구 안내 ----------
  // 루틴 줄 이름에서 기구를 찾는다(가장 긴 낱말이 맞는 기구)
  function gymOf(name) {
    let best = null, len = 0;
    GYM.forEach((g) => g.k.forEach((k) => { if (name.includes(k) && k.length > len) { best = g; len = k.length; } }));
    return best;
  }
  const gymDays = (d, id) => d.cfg.plan.days.map((p, i) => (p.items.some((it) => (gymOf(it[0]) || {}).id === id) ? "월화수목금토일"[i] : "")).filter(Boolean);
  const gymPic = (g, d, cls) => (d.photos && d.photos[g.id] ? `<img class="${cls}" data-ph="${g.id}" data-ver="${esc(d.photos[g.id])}" alt="${esc(g.name)} 사진">` : `<span class="${cls} none" aria-hidden="true">${svg(GYM_ICON[g.icon] || "", 26, 1.7)}</span>`);
  function gymList(d) {
    const n = Object.keys(d.photos || {}).filter((k) => GYM.some((g) => g.id === k)).length;
    return `<p class="hl-hint">기구를 누르면 맞추는 법과 하는 법이 나와요. 사진은 기구마다 직접 찍어 넣을 수 있어요${n ? ` (${n} / ${GYM.length}개 넣음)` : ""}.</p>
      ${GYM_CATS.map(([cat, label]) => `<section class="hl-sec"><div class="hl-sec-h"><h2>${label}</h2><span>${GYM.filter((g) => g.cat === cat).length}가지</span></div>
        <div class="hl-card flush">${GYM.filter((g) => g.cat === cat).map((g) => { const days = gymDays(d, g.id); return `<button type="button" class="hl-gym" data-gym="${g.id}">${gymPic(g, d, "hl-gym-ph")}
          <span class="hl-lab-t"><b>${esc(g.name)}</b><small>${esc(g.aka)} · ${esc(g.target)}</small></span>${days.length ? `<em>${days.join("·")}</em>` : ""}</button>`; }).join("")}</div></section>`).join("")}`;
  }
  // 사진은 로그인 정보가 있어야 받을 수 있어서 직접 받아 붙인다. 한 번 받은 것은 그대로 쓴다
  const picCache = new Map();
  async function picUrl(key, ver, thumb) {
    const id = `${key}|${ver}|${thumb ? 1 : 0}`;
    if (!picCache.has(id)) picCache.set(id, fetch(apiBase() + `/api/health/photo/${key}${thumb ? "?s=thumb" : ""}`, { headers: authHeaders({}) })
      .then((r) => { if (!r.ok) throw new Error("사진을 받지 못했어요"); return r.blob(); }).then((b) => URL.createObjectURL(b)).catch((e) => { picCache.delete(id); throw e; }));
    return picCache.get(id);
  }
  function hydrate(root) {
    $$("img[data-ph]", root).forEach((im) => { if (!im.src) picUrl(im.dataset.ph, im.dataset.ver, !im.dataset.full).then((u) => { im.src = u; }).catch(() => { im.classList.add("fail"); }); });
  }
  // 휴대폰 사진을 줄여서(긴 쪽 1280, 작은 것 320) 보낸다
  function shrink(img, max, q) {
    const k = Math.min(1, max / Math.max(img.naturalWidth, img.naturalHeight)), c = document.createElement("canvas");
    c.width = Math.round(img.naturalWidth * k); c.height = Math.round(img.naturalHeight * k);
    c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
    return c.toDataURL("image/jpeg", q);
  }
  function pickPhoto(g) {
    const inp = document.createElement("input");
    inp.type = "file"; inp.accept = "image/*";
    inp.onchange = () => {
      const file = inp.files && inp.files[0];
      if (!file) return;
      const img = new Image(), url = URL.createObjectURL(file);
      img.onerror = () => { URL.revokeObjectURL(url); toast("사진을 읽지 못했어요", true); };
      img.onload = async () => {
        const full = shrink(img, 1280, 0.82), thumb = shrink(img, 320, 0.78);
        URL.revokeObjectURL(url);
        if (await save(() => send("POST", `/api/health/photo/${g.id}`, { full, thumb }), "사진을 넣었어요")) gymSheet(g.id);
      };
      img.src = url;
    };
    inp.click();
  }
  function gymSheet(id) {
    const g = GYM.find((x) => x.id === id), d = H.d;
    if (!g) return;
    const has = !!(d.photos && d.photos[g.id]), days = gymDays(d, g.id), li = (a) => a.map((x) => `<li>${esc(x)}</li>`).join("");
    openSheet(g.name, `<div class="hl-guide">
      <div class="hl-guide-ph">${has ? `<img data-ph="${g.id}" data-full="1" data-ver="${esc(d.photos[g.id])}" alt="${esc(g.name)} 사진">` : `<span class="none">${svg(GYM_ICON[g.icon] || "", 54, 1.4)}<small>아직 사진이 없어요</small></span>`}</div>
      ${Object.keys(d.photos || {}).filter((k) => k.startsWith(g.id + "_")).sort().map((k) => `<div class="hl-guide-ph"><img data-ph="${k}" data-full="1" data-ver="${esc(d.photos[k])}" alt="${esc(g.name)} 사진 더"></div>`).join("")}
      <div class="hl-row2"><button type="button" class="btn ${has ? "soft" : "dark"} wide" id="hlPic">${has ? "사진 바꾸기" : "사진 넣기"}</button>${has ? `<button type="button" class="btn soft" id="hlPicDel">사진 지우기</button>` : ""}</div>
      <p class="hl-guide-tag"><b>${esc(g.aka)}</b><span>${esc(g.target)}</span>${days.length ? `<em>내 루틴 ${days.join("·")}요일</em>` : ""}</p>
      <p class="hl-guide-what">${esc(g.what)}</p>
      <h3>자리 맞추기</h3><ul>${li(g.setup)}</ul>
      <h3>하는 법</h3><ol>${li(g.how)}</ol>
      <h3>처음에는</h3><p>${esc(g.start)}</p>
      <h3>조심할 점</h3><ul class="care">${li(g.care)}</ul></div>`, (root) => {
      hydrate(root);
      $("#hlPic", root).onclick = () => pickPhoto(g);
      if (has) $("#hlPicDel", root).onclick = async (e) => { if (!confirmTap(e.currentTarget, "한 번 더 누르면 지워요")) return; if (await save(() => send("DELETE", `/api/health/photo/${g.id}`), "지웠어요")) gymSheet(g.id); };
    });
  }

  // ---------- 검진 ----------
  function checkup(d) {
    const flagged = d.labs.filter((l) => l.flag !== "ok"), ok = d.labs.filter((l) => l.flag === "ok");
    const row = (l) => `<button type="button" class="hl-lab" data-lab="${l.id}"><span class="hl-lab-t"><b>${esc(l.name)}</b><small>${esc(l.ref)}${l.prev ? `${l.ref ? " · " : ""}이전 ${esc(l.prev.value)} (${md(l.prev.date)})` : ""}</small></span>
      <strong>${esc(l.value)}</strong><em class="f-${l.flag}">${esc(l.flag_label)}</em></button>`;
    return `${head(d.lab_date ? `${dot(d.lab_date)} 검사 기준` : "검진 수치와 다음 검사 일정", "검진·추적", `<button type="button" class="hl-ico" data-act="manage" aria-label="목표·자료 관리">${MORE}</button>`)}
      <section class="hl-card flush">${flagged.map(row).join("")}
        ${ok.length ? `<details class="hl-ok"><summary><b>정상 ${ok.length}개</b><span>${ok.slice(0, 4).map((l) => `${esc(l.name)} ${esc(l.value)}`).join(" · ")}</span></summary>${ok.map(row).join("")}</details>` : ""}
        ${d.labs.length ? "" : `<div class="hl-none">검진 수치를 넣어 두면 다음 검사 때 얼마나 달라졌는지 볼 수 있어요.</div>`}
        <div class="hl-row2 pad"><button type="button" class="hl-btn line" data-act="lab">수치 추가</button>${d.labs.length ? "" : `<button type="button" class="hl-btn line" data-act="import">자료 가져오기</button>`}</div></section>
      <section class="hl-sec"><div class="hl-sec-h"><h2>먹는 약</h2><button type="button" class="hl-link" data-act="med">약 추가</button></div>
        ${(d.cfg.meds || []).length ? `<div class="hl-card flush">${d.cfg.meds.map((m, i) => `<button type="button" class="hl-lab" data-med="${i}"><span class="hl-lab-t"><b>${esc(m.name)}</b>${m.note ? `<small>${esc(m.note)}</small>` : ""}</span>
          <strong class="sm">${esc(m.dose)}</strong><em>${esc(whenLabel(d, m.when))}</em></button>`).join("")}
          <p class="hl-meds-n">${(d.med_slots || []).map((x) => `${x.label} 최근 7일 중 ${x.week}일 체크`).join(" · ")}</p></div>`
        : `<div class="hl-none">매일 먹는 약을 넣어 두면 홈의 오늘 체크에 한 줄로 들어가요.</div>`}
      </section>
      <section class="hl-sec"><div class="hl-sec-h"><h2>추적검사 일정</h2><button type="button" class="hl-link" data-act="follow">일정 추가</button></div>
        ${d.follows.map((f) => `<button type="button" class="hl-fu ${f.next ? "next" : ""} ${f.done ? "done" : ""}" data-follow="${f.id}"><b>${f.done ? "완료" : dday(f.dday)}</b>
          <span><strong>${esc(f.title)}</strong><small>${dot(f.date)}${f.why ? ` · ${esc(f.why)}` : ""}</small></span></button>`).join("") || `<div class="hl-none">다음 검사 날짜를 넣어 두면 며칠 남았는지 홈에 보여요.</div>`}
      </section>`;
  }

  // ---------- 아래에서 올라오는 창(입력) ----------
  const fld = (label, name, val, attr = "", hint = "") => `<label class="field">${label}<input name="${name}" value="${esc(val ?? "")}" ${attr}>${hint ? `<span class="hint">${hint}</span>` : ""}</label>`;
  const formData = (f) => Object.fromEntries(new FormData(f).entries());
  function sheetForm(title, html, onSave, onDel) {
    openSheet(title, `<form class="hl-form" novalidate>${html}<div class="hl-row2">${onDel ? `<button type="button" class="btn soft" id="hlDel">지우기</button>` : ""}<button type="submit" class="btn dark wide">저장</button></div></form>`, (root) => {
      const f = $("form", root);
      f.onsubmit = async (e) => {
        e.preventDefault();
        if (await save(() => onSave(formData(f), f), "저장했어요")) closeSheet();
      };
      if (onDel) $("#hlDel", root).onclick = async (e) => { if (!confirmTap(e.currentTarget, "한 번 더 누르면 지워요")) return; if (await save(onDel, "지웠어요")) closeSheet(); };
    });
  }
  function goalSheet() {
    const c = H.d.cfg, g = c.goal;
    sheetForm("목표 정하기", `<div class="hl-in2">${fld("키 cm", "height", c.height, 'inputmode="decimal"')}${fld("시작 체중 kg", "start_weight", g.start_weight, 'inputmode="decimal"')}</div>
      <div class="hl-in2">${fld("목표 체중 kg", "weight", g.weight, 'inputmode="decimal"')}${fld("목표 허리 cm", "waist", g.waist, 'inputmode="decimal"')}</div>
      <div class="hl-in2">${fld("시작한 날", "start_date", g.start_date, 'type="date"')}${fld("목표 날짜", "date", g.date, 'type="date"')}</div>
      ${fld("목표 한마디", "label", g.label, 'maxlength="30"', "홈의 목표 옆에 보여요. 예: 석 달 안에")}`,
    (v) => send("PUT", "/api/health/config", { height: v.height, goal: { weight: v.weight, waist: v.waist, start_weight: v.start_weight, start_date: v.start_date, date: v.date, label: v.label } }));
  }
  function labSheet(id) {
    const l = H.d.labs.find((x) => x.id === id) || { date: H.d.today, flag: "high" };
    sheetForm(id ? "수치 고치기" : "수치 추가", `${fld("항목", "name", l.name, 'list="hlNames" maxlength="30" required')}
      <datalist id="hlNames">${H.d.labs.map((x) => `<option value="${esc(x.name)}">`).join("")}</datalist>
      <div class="hl-in2">${fld("수치", "value", l.value, 'maxlength="16" required')}${fld("검사한 날", "date", l.date, `type="date" max="${H.d.today}"`)}</div>
      ${fld("기준·메모", "ref", l.ref, 'maxlength="50"', "예: 기준 46~116")}
      <div class="field">판정<div class="hl-seg">${H.d.flags.map(([k, v]) => `<label><input type="radio" name="flag" value="${k}" ${l.flag === k ? "checked" : ""}><span>${v}</span></label>`).join("")}</div></div>
      ${id ? "" : `<p class="hl-hint">같은 항목을 새 날짜로 넣으면 이전 수치와 나란히 보여요.</p>`}`,
    (v) => send("POST", "/api/health/lab", Object.assign(v, { id: id || undefined })), id ? () => send("DELETE", `/api/health/lab/${id}`) : null);
    const nm = $("#sheetBody [name=name]"), rf = $("#sheetBody [name=ref]");
    if (!id && nm && rf) nm.addEventListener("change", () => { const old = H.d.labs.find((x) => x.name === nm.value.trim()); if (old && !rf.value) rf.value = old.ref; });
  }
  function followSheet(id) {
    const f = H.d.follows.find((x) => x.id === id) || {};
    sheetForm(id ? "일정 고치기" : "일정 추가", `${fld("검사 이름", "title", f.title, 'maxlength="40" required')}
      <div class="hl-in2">${fld("날짜", "date", f.date, 'type="date" required')}${fld("이유", "why", f.why, 'maxlength="40"')}</div>
      ${id ? `<label class="hl-tog"><input type="checkbox" name="done" ${f.done ? "checked" : ""}><span>검사를 받았어요</span></label>` : ""}`,
    (v) => send("POST", "/api/health/follow", { id: id || undefined, title: v.title, date: v.date, why: v.why, done: v.done === "on" }), id ? () => send("DELETE", `/api/health/follow/${id}`) : null);
  }
  const whenLabel = (d, w) => ((d.when || []).find((x) => x[0] === w) || [0, "아침"])[1];
  function medSheet(i) {
    const meds = JSON.parse(JSON.stringify(H.d.cfg.meds || [])), m = i >= 0 ? meds[i] : { when: "am" };
    sheetForm(i >= 0 ? "약 고치기" : "약 추가", `${fld("약 이름", "name", m.name, 'maxlength="30" required')}
      <div class="hl-in2">${fld("용량", "dose", m.dose, 'maxlength="24"', "예: 10mg 1알")}${fld("메모", "note", m.note, 'maxlength="40"')}</div>
      <div class="field">먹는 때<div class="hl-seg">${(H.d.when || []).map(([k, v]) => `<label><input type="radio" name="when" value="${k}" ${m.when === k ? "checked" : ""}><span>${v}</span></label>`).join("")}</div></div>
      <p class="hl-hint">같은 때에 먹는 약은 홈에서 한 번에 체크해요. 용량을 바꾸거나 끊는 것은 처방한 병원과 정하세요.</p>`,
    (v) => {
      if (!v.name.trim()) throw new Error("약 이름을 넣어 주세요");
      const row = { name: v.name, dose: v.dose, note: v.note, when: v.when || "am" };
      if (i >= 0) meds[i] = row; else meds.push(row);
      return send("PUT", "/api/health/config", { meds });
    }, i >= 0 ? () => { meds.splice(i, 1); return send("PUT", "/api/health/config", { meds }); } : null);
  }
  function planSheet() {
    const plan = JSON.parse(JSON.stringify(H.d.cfg.plan)), i = H.d.week.days.findIndex((x) => x.date === H.sel), p = plan.days[Math.max(0, i)];
    sheetForm(`${H.d.week.days[Math.max(0, i)].dow}요일 루틴 고치기`, `<div class="hl-in2">${fld("이름", "name", p.name, 'maxlength="16" required')}${fld("걸리는 시간", "time", p.time, 'maxlength="12"')}</div>
      <label class="field">운동 (한 줄에 하나, 이름 | 분량)<textarea name="items" rows="7">${esc(p.items.map((it) => it[0] + (it[1] ? " | " + it[1] : "")).join("\n"))}</textarea></label>
      <label class="hl-tog"><input type="checkbox" name="rest" ${p.rest ? "checked" : ""}><span>쉬는 날 (주간 목표 횟수에서 빼요)</span></label>
      ${fld("운동하는 곳", "place", plan.place, 'maxlength="20"')}`,
    (v) => {
      Object.assign(p, { name: v.name, time: v.time, rest: v.rest === "on", items: v.items.split("\n").map((s) => s.split("|").map((x) => x.trim())).filter((x) => x[0]) });
      plan.place = v.place;
      return send("PUT", "/api/health/config", { plan });
    });
  }
  function habitSheet() {
    sheetForm("체크 항목 고치기", `<label class="field">하루 체크 (한 줄에 하나, 제목 | 설명)<textarea name="habits" rows="6">${esc(H.d.cfg.habits.map((h) => h.title + (h.sub ? " | " + h.sub : "")).join("\n"))}</textarea>
      <span class="hint">운동 줄은 주간 루틴에서 자동으로 들어가요. 제목을 그대로 두면 지난 체크가 이어져요.</span></label>`,
    (v) => send("PUT", "/api/health/config", { habits: v.habits.split("\n").map((s) => s.split("|").map((x) => x.trim())).filter((x) => x[0]).map((x) => ({ title: x[0], sub: x[1] || "" })) }));
  }
  function importPick() {
    const inp = document.createElement("input");
    inp.type = "file"; inp.accept = ".json,application/json";
    inp.onchange = async () => {
      const file = inp.files && inp.files[0];
      if (!file) return;
      let data;
      try { data = JSON.parse(await file.text()); } catch (e) { return toast("건강관리 자료 파일이 아니에요", true); }
      closeSheet();
      await save(async () => { const r = await send("POST", "/api/health/import", { data }); toast(r.added.photos && !r.added.body && !r.added.labs ? `기구 사진 ${r.added.photos}장을 넣었어요` : `가져왔어요: 기록 ${r.added.body}개, 수치 ${r.added.labs}개, 일정 ${r.added.follows}개${r.added.photos ? `, 사진 ${r.added.photos}장` : ""}`); });
    };
    inp.click();
  }
  function manageSheet() {
    openSheet("목표·자료 관리", `<div class="hl-menu">
      <button type="button" data-m="goal"><b>목표 정하기</b><small>키, 목표 체중·허리, 목표 날짜</small></button>
      <button type="button" data-m="habit"><b>체크 항목 고치기</b><small>홈의 하루 체크 목록</small></button>
      <button type="button" data-m="import"><b>자료 가져오기</b><small>건강관리 자료 파일(.json)을 넣어요. 같은 날짜·항목은 파일 값으로 바뀌어요</small></button>
      <button type="button" data-m="export"><b>자료 내보내기</b><small>지금까지의 기록을 파일 하나로 받아요</small></button></div>
      <p class="hl-hint">기록은 노트북 서버에만 저장돼요. 우석World 백업에도 함께 들어가요.</p>`, (root) => {
      root.onclick = async (e) => {
        const b = e.target.closest("[data-m]");
        if (!b) return;
        const m = b.dataset.m;
        if (m === "goal") goalSheet(); else if (m === "habit") habitSheet(); else if (m === "import") importPick();
        else if (m === "export") {
          try { const d = await GET("/api/health/export"); await bkSave(new Blob([JSON.stringify(d, null, 1)], { type: "application/json" }), `health-${H.d.today}.json`); }
          catch (err) { toast(err.message, true); }
        }
      };
    });
  }

  // ---------- 누름 처리 ----------
  async function onClick(e) {
    const el = (s) => e.target.closest(s), d = H.d;
    if (!d || H.busy) return;
    let b;
    if ((b = el("[data-check]"))) {
      const id = b.dataset.check, on = b.getAttribute("aria-pressed") !== "true";
      if (id === "gym") (d.week.days.find((x) => x.today) || {}).done = on;
      else d.checks = on ? d.checks.concat(id) : d.checks.filter((k) => k !== id);
      draw();
      return save(() => (id === "gym" ? send("POST", "/api/health/workout", { done: on }) : send("POST", "/api/health/check", { key: id, on })));
    }
    if ((b = el("[data-act]"))) return ({ goal: goalSheet, manage: manageSheet, import: importPick, plan: planSheet, lab: () => labSheet(0), follow: () => followSheet(0), med: () => medSheet(-1) }[b.dataset.act] || (() => {}))();
    if ((b = el("[data-tab]"))) { H.tab = b.dataset.tab; return draw(); }
    if ((b = el("[data-wtab]"))) { H.wtab = b.dataset.wtab; draw(); return window.scrollTo(0, 0); }
    if ((b = el("[data-gym]"))) return gymSheet(b.dataset.gym);
    if ((b = el("[data-delbody]"))) { if (!confirmTap(b, "한 번 더")) return; return save(() => send("DELETE", `/api/health/body/${b.dataset.delbody}`), "지웠어요"); }
    if ((b = el("[data-day]"))) { H.sel = b.dataset.day; return draw(); }
    if ((b = el("[data-wk]"))) {
      const t = new Date(d.week.start + "T12:00:00"); t.setDate(t.getDate() + Number(b.dataset.wk));
      H.week = t.toISOString().slice(0, 10); H.sel = "";
      H.busy = true;
      try { await reload(); } catch (err) { toast(err.message, true); } finally { H.busy = false; }
      return;
    }
    if ((b = el("[data-done]"))) { const on = b.dataset.done === "1", day = d.week.days.find((x) => x.date === H.sel); day.done = on; draw(); return save(() => send("POST", "/api/health/workout", { date: H.sel, done: on })); }
    if ((b = el("[data-rpe]"))) { const day = d.week.days.find((x) => x.date === H.sel), v = Number(b.dataset.rpe); day.rpe = day.rpe === v ? null : v; draw(); return save(() => send("POST", "/api/health/workout", { date: H.sel, rpe: day.rpe })); }
    if ((b = el("[data-med]"))) return medSheet(Number(b.dataset.med));
    if ((b = el("[data-lab]"))) return labSheet(Number(b.dataset.lab));
    if ((b = el("[data-follow]"))) return followSheet(Number(b.dataset.follow));
  }
  async function onSubmit(e) {
    if (e.target.id !== "hlBody") return;
    e.preventDefault();
    const v = formData(e.target);
    if (!v.weight && !v.waist && !v.fat) return toast("체중·허리·체지방 중 하나는 넣어 주세요", true);
    await save(() => send("POST", "/api/health/body", v), "기록했어요");
  }

  pages.health = {
    async mount(h) {
      H.sub = ["today", "body", "workout", "checkup"].includes(h.sub) ? h.sub : "today";
      if (H.sub !== "workout") { H.week = ""; H.sel = ""; }
      setTop("건강관리", {});
      await load();
      view.innerHTML = `<div class="hl-app" id="hlApp"></div>`;
      const host = $("#hlApp");
      host.addEventListener("click", (e) => { onClick(e).catch((err) => toast(err.message, true)); });
      host.addEventListener("submit", (e) => { onSubmit(e).catch((err) => toast(err.message, true)); });
      draw();
    },
  };
})();
