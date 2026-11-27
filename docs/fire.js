"use strict";
/* 파이어(FIRE, 일찍 은퇴) 앱(우석World). 화면만 여기 있고, 개인 자료(나이·연봉·집·대출·연금)는 노트북 서버(/api/fire)의 DB에만 둔다.
   이 파일에 개인 숫자를 넣지 않는다. 셈은 서버(moa/fire.py)가 한다. */
(function () {
  const IC = {
    home: '<path d="M12 3c2 3 5 5 5 9a5 5 0 0 1-10 0c0-2 1-3 2-4 0 2 1 3 2 3 0-3-1-5 1-8z"/>',
    plan: '<path d="M4 20h16M6 16V9M11 16V5M16 16v-4"/>',
    form: '<path d="M5 4h14v16H5z"/><path d="M8 9h8M8 13h8M8 17h5"/>',
    book: '<path d="M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2z"/><path d="M4 19V5"/>',
  };
  const svg = (p, s = 20, w = 2) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${w}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${p}</svg>`;
  const GLOBE = svg('<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.8 3 2.8 15 0 18M12 3c-2.8 3-2.8 15 0 18"/>', 20, 1.8);

  addApp({
    id: "fire", name: "파이어", desc: "일찍 은퇴 계획", home: "#fire", secs: ["fire"],
    tabs: [["fire/home", "요약", IC.home], ["fire/ages", "나이별", IC.plan], ["fire/form", "내 자료", IC.form], ["fire/facts", "제도", IC.book]],
    skin: { meta: "#F6F1EA", vars: { "--deep": "#1E1A2B", "--deep2": "#2D2740", "--bg": "#F6F1EA", "--hl": "#FF8A3D", "--solid": "#1E1A2B",
      "--on-deep": "#F6F1EA", "--on-deep2": "#BDB5CC", "--on-deep-strong": "#FFFFFF", "--sub-bg": "rgba(255,255,255,.08)" } },
    tile: { bg: "linear-gradient(150deg,#FF8A3D 0%,#1E1A2B 80%)", ink: "#FFFFFF", sub: "#F3D9C6", accent: "#FFC27A" },
    live: (d) => (!d ? [] : !d.ready ? ["생년월일·연봉을 넣으면 계획이 보여요"] : [d.best ? `보통이면 ${d.best}세에 은퇴 가능` : "정년까지 일해도 모자라요", d.bad ? `나쁘면 ${d.bad}세` : ""]),
    art: () => `<svg viewBox="0 0 120 120" fill="none" aria-hidden="true"><circle cx="60" cy="72" r="30" stroke="currentColor" stroke-width="8"/>
      <path d="M14 72h92" stroke="#fff" stroke-width="4" stroke-linecap="round"/><path d="M60 20v14M28 34l9 9M92 34l-9 9" stroke="#fff" stroke-width="4" stroke-linecap="round"/></svg>`,
  });

  const F = { d: null, sub: "home", age: null };
  const eok = (v) => {            // 1.23억 · 4,560만
    if (v == null || isNaN(v)) return "–";
    const a = Math.abs(v), s = v < 0 ? "−" : "";
    if (a >= 1e8) return `${s}${(a / 1e8).toFixed(a >= 1e9 ? 1 : 2).replace(/\.?0+$/, "")}억`;
    if (a >= 1e4) return `${s}${Math.round(a / 1e4).toLocaleString("ko-KR")}만`;
    return `${s}${Math.round(a).toLocaleString("ko-KR")}`;
  };
  const head = (sub, title) => `<header class="fi-head"><div><p>${esc(sub)}</p><h1>${esc(title)}</h1></div><a class="fi-ico" href="#world" aria-label="우석World 첫 화면으로">${GLOBE}</a></header>`;

  // 금융자산 곡선(나쁨·보통·좋음). 은퇴 나이·연금 시작 나이에 세로선
  function chart(d, R) {
    const ga = (d.table.find((x) => x.age === R) || {}).gov_age || d.gov_age;
    const ages = d.curve_ages, keys = ["bad", "mid", "good"], W = 400, H = 190, pl = 40, pr = 8, pt = 10, pb = 24;
    const C = Object.fromEntries(keys.map((k) => [k, d.curves[R][k].map((v, i) => ({ age: ages[i], assets: v }))]));
    const all = keys.flatMap((k) => C[k]);
    const a0 = ages[0], a1 = ages[ages.length - 1];
    let lo = Math.min(0, ...all.map((r) => r.assets)), hi = Math.max(...all.map((r) => r.assets));
    lo = Math.max(lo, -hi * 0.6);
    const x = (a) => pl + (a - a0) / (a1 - a0) * (W - pl - pr), y = (v) => pt + (hi - Math.max(lo, v)) / (hi - lo || 1) * (H - pt - pb);
    const path = (rows) => rows.map((r, i) => `${i ? "L" : "M"}${x(r.age).toFixed(1)},${y(r.assets).toFixed(1)}`).join("");
    const ticks = [0, hi / 2, hi].map((v) => `<text x="${pl - 6}" y="${y(v) + 4}" text-anchor="end">${eok(v)}</text><line x1="${pl}" x2="${W - pr}" y1="${y(v)}" y2="${y(v)}" class="fi-grid"/>`).join("");
    const xt = [a0, R, ga, a1].filter((v, i, s) => s.indexOf(v) === i).map((a) => `<text x="${x(a)}" y="${H - 6}" text-anchor="middle">${a}세</text>`).join("");
    return `<svg class="fi-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${R}세 은퇴 때 금융자산 흐름">
      ${ticks}${xt}
      <line x1="${x(R)}" x2="${x(R)}" y1="${pt}" y2="${H - pb}" class="fi-vl"/><line x1="${x(ga)}" x2="${x(ga)}" y1="${pt}" y2="${H - pb}" class="fi-vl p"/>
      <line x1="${pl}" x2="${W - pr}" y1="${y(0)}" y2="${y(0)}" class="fi-zero"/>
      ${keys.map((k) => `<path d="${path(C[k])}" class="fi-ln fi-${k}"/>`).join("")}</svg>
      <div class="fi-leg"><span class="fi-bad">나쁨 1.5%</span><span class="fi-mid">보통 3.5%</span><span class="fi-good">좋음 5%</span><span class="fi-v">│ 은퇴 ${R}세 · 공무원연금 ${ga}세</span></div>`;
  }

  function missingBox(d) {
    if (!d.missing.length) return "";
    return `<section class="fi-card fi-miss"><h2>더 넣으면 정확해져요</h2><ul>${d.missing.map((m) => `<li>${esc(m)}</li>`).join("")}</ul>
      <a class="fi-btn" href="#fire/form">내 자료 넣기</a></section>`;
  }

  function home(d) {
    if (!d.ready) return `${head("파이어", "일찍 은퇴 계획")}<section class="fi-card fi-empty"><h2>생년월일과 연봉부터 넣어 주세요</h2>
      <p>넣은 자료는 노트북 서버에만 저장돼요. 가계부 지출·자산 화면 금액은 비워 두면 자동으로 가져와요.</p><a class="fi-btn main" href="#fire/form">내 자료 넣기</a></section>${missingBox(d)}`;
    const R = F.age || d.pick, row = d.table.find((x) => x.age === R) || d.table[d.table.length - 1], N = d.net;
    const b = d.best, chip = (k, lb) => `<span class="fi-chip fi-${k}"><small>${lb}</small><b>${b[k] ? `${b[k]}세` : "정년도 모자람"}</b></span>`;
    const mid = row.mid, ok = mid.broke == null;
    return `${head("파이어", "일찍 은퇴 계획")}
      <section class="fi-hero">
        <p>지금 ${d.age}세 · 95세까지 돈이 버티는 가장 빠른 은퇴 나이</p>
        <div class="fi-big">${b.mid ? `${b.mid}<small>세</small>` : `<span>정년 60세에도 모자라요</span>`}</div>
        <div class="fi-chips">${chip("bad", "나쁨")}${chip("mid", "보통")}${chip("good", "좋음")}</div>
        <p class="fi-note">투자 수익률(물가 뺀)이 나쁨 1.5% · 보통 3.5% · 좋음 5%일 때. 금액은 모두 지금 돈 가치예요.</p>
      </section>
      <section class="fi-card"><div class="fi-ch"><h2>${R}세에 그만두면</h2><span class="fi-seg" role="group" aria-label="은퇴 나이">${[50, 52, 55, 57, 60].filter((a) => d.table.some((x) => x.age === a)).concat(d.pick).filter((v, i, s) => s.indexOf(v) === i).sort((a, c) => a - c)
        .map((a) => `<button type="button" data-age="${a}" aria-pressed="${a === R}">${a}</button>`).join("")}</span></div>
        ${chart(d, R)}
        <div class="fi-kv">
          <div><small>그때 금융자산(보통)</small><b>${eok(mid.at)}</b></div>
          <div><small>95세 때 남는 돈(보통)</small><b class="${ok ? "up" : "dn"}">${ok ? eok(mid.end) : `${mid.broke}세에 바닥`}</b></div>
          <div><small>퇴직수당(세전)</small><b>${eok(row.allowance)}</b></div>
          <div><small>명예퇴직수당(어림)</small><b>${row.honor ? eok(row.honor) : "대상 아님"}</b></div>
          <div><small>공무원연금(월, ${row.gov_age}세부터)</small><b>${eok(row.gov)}</b><em>지금 그만두면 ${eok(d.gov_now)} → ${R}세까지 일하면${row.years != null ? ` · 재직 ${row.years}년` : ""}${row.ok ? "" : " · 10년 미만: 연계퇴직연금"}</em></div>
          <div><small>은퇴 → 공무원연금까지</small><b>${Math.max(0, row.gov_age - R)}년</b><em>이 기간을 모은 돈으로 버텨야 해요</em></div>
        </div>
      </section>
      <section class="fi-card"><h2>이 나이에 은퇴하려면</h2>
        <table class="fi-tbl fi-goal"><thead><tr><th>은퇴</th><th>생활비 상한(월)</th><th>지금 더 있어야 할 돈</th></tr></thead>
        <tbody>${d.goals.map((g) => `<tr class="${g.age === R ? "on" : ""}"><th>${g.age}세</th><td><b>${g.spend_max ? eok(g.spend_max) : "생활비 0도 모자람"}</b></td>
          <td><b class="${g.add_need ? "dn" : "up"}">${g.add_need == null ? "30억 넘게" : g.add_need ? eok(g.add_need) : "충분"}</b></td></tr>`).join("")}</tbody></table>
        <p class="fi-note">보통(3.5%) 기준으로 95세까지 버티는 값이에요. 생활비 상한은 대출 상환·건강보험료를 뺀 순수 생활비예요.</p></section>
      <section class="fi-card"><h2>지금 순자산</h2>
        <div class="fi-bar" role="img" aria-label="집 순자산과 금융자산 비율"><i class="h" style="flex:${Math.max(0, N.home_eq)}"></i><i class="f" style="flex:${Math.max(0, N.fin)}"></i></div>
        <div class="fi-kv">
          <div><small>집 시세</small><b>${eok(N.home)}</b><em>${d.src.home_value ? "자산 화면 값" : ""}</em></div>
          <div><small>대출</small><b class="dn">${eok(-N.debt)}</b><em>월 ${eok(d.mortgage.pay_month)} · ${d.mortgage.end}년까지${d.mortgage.guess ? " (금리·기간 어림)" : ""}</em></div>
          <div><small>집 순자산</small><b>${eok(N.home_eq)}</b><em>은퇴 생활비로는 못 써요(주택연금·집 줄이기 전까지)</em></div>
          <div><small>금융자산</small><b>${eok(N.fin)}</b><em>${d.src.fin_assets ? "자산 화면 값" : ""}</em></div>
        </div></section>
      <section class="fi-card"><h2>한눈에 보는 기준</h2>
        <div class="fi-kv">
          <div><small>한 해 생활비</small><b>${eok(d.rule4.spend_year)}</b><em>${d.src.spend ? `가계부 ${d.app.spend_n}달 평균` : "내 자료 값"}</em></div>
          <div><small>4% 법칙 목표(×25)</small><b>${eok(d.rule4.x25)}</b><em>연금 없이 버티려면. 연금이 있어 실제 필요액은 더 적어요</em></div>
          <div><small>은퇴~연금 공백에 드는 돈(보통)</small><b>${eok(d.bridge.need)}</b><em>${d.bridge.years}년 동안 연금 없이</em></div>
          <div><small>연금 합계(월, ${d.pick}세 은퇴)</small><b>${eok(d.gov_month + (d.used.pension_nps || 0))}</b><em>공무원 ${eok(d.gov_month)} + 국민 ${eok(d.used.pension_nps)}</em></div>
          <div><small>1년 더 일하면 공무원연금</small><b class="up">월 +${eok(d.gov_gain)}</b><em>평생 해마다 +${eok(d.gov_gain * 12)} (지금 연봉÷12 × 지급률 약 1.7%)</em></div>
          <div><small>건보 피부양자(연금만 볼 때)</small><b>${d.dependent.ok_income ? "소득 기준 안" : "소득 기준 넘음"}</b><em>공적연금 연 ${eok(d.dependent.pension_year)} / 기준 2,000만. 재산 기준은 따로</em></div>
          <div><small>승진 뒤 세전 연봉(내년)</small><b>${eok(d.salary.y_next)}</b><em>봉급표 비율 ×${d.salary.bump.toFixed(3)}${d.salary.guess_th ? " · 실수령 76% 어림" : ""}</em></div>
        </div></section>
      ${missingBox(d)}
      <p class="fi-foot">어림값이에요. 세금(퇴직소득세·연금소득세), 큰 지출(자녀·병원·집 수리), 제도 변경은 빠져 있어요. 제도 숫자와 출처는 '제도' 탭에 있어요.</p>`;
  }

  function ages(d) {
    if (!d.ready) return home(d);
    const cell = (x) => (x.broke == null ? `<b class="up">버팀</b><small>${eok(x.end)}</small>` : `<b class="dn">${x.broke}세</b><small>바닥</small>`);
    return `${head("파이어", "은퇴 나이별 비교")}
      <section class="fi-card fi-tbl-wrap"><table class="fi-tbl"><thead><tr><th>은퇴</th><th>나쁨</th><th>보통</th><th>좋음</th><th>퇴직수당<br><small>+명퇴</small></th><th>연금(월)<br><small>시작</small></th></tr></thead>
        <tbody>${d.table.map((r) => `<tr class="${r.age === d.best.mid ? "on" : ""}"><th>${r.age}세<small>재직 ${r.years ?? "?"}년</small></th>
          <td>${cell(r.bad)}</td><td>${cell(r.mid)}</td><td>${cell(r.good)}</td>
          <td><b>${eok(r.allowance)}</b><small>${r.honor ? `+${eok(r.honor)}` : "-"}</small></td><td><b>${eok(r.gov)}</b><small>${r.gov_age}세</small></td></tr>`).join("")}</tbody></table>
        <p class="fi-note">'버팀'은 95세까지 금융자산이 0 아래로 안 내려간다는 뜻이고, 아래 금액은 95세 때 남는 돈이에요. 연금은 그만둘 때까지 일한 해만큼 늘려 어림했어요(그해 연봉 × 지급률).</p></section>`;
  }

  function form(d) {
    const P = d.profile, U = d.app;
    const val = (k, unit) => (unit === "date" ? esc(P[k] || "") : P[k] ? String(Math.round(P[k] * 100) / 100) : "");
    const ph = (k) => (U[k] ? `가져온 값 ${eok(U[k])}` : "");
    const groups = [["나", ["birth", "service_start", "life"]], ["월급", ["salary", "take_home", "grade_now", "step_now", "promo_date", "promo_grade", "promo_step"]],
      ["쓰는 돈·모은 돈", ["spend", "fin_assets", "save_extra"]], ["집", ["home_value", "mortgage", "mortgage_rate", "mortgage_years", "house_pension_age"]],
      ["연금", ["pension_gov", "pension_asof", "pension_nps", "pension_start", "pension_early", "honor_on"]], ["가정", ["health_ins", "inflation"]]];
    const meta = Object.fromEntries(d.fields.map((f) => [f.key, f]));
    return `${head("파이어", "내 자료")}
      <p class="fi-note fi-pad">노트북 서버에만 저장돼요(저장소·바깥으로 나가지 않아요). 비워 둔 생활비·금융자산·집·대출은 가계부·자산 화면 값을 써요.</p>
      <form class="fi-form" id="fiForm">${groups.map(([g, ks]) => `<fieldset class="fi-card"><legend>${g}</legend>${ks.map((k) => { const f = meta[k];
        return `<label class="fi-f"><span>${esc(f.label)}${f.unit && f.unit !== "date" ? ` <small>(${esc(f.unit)})</small>` : ""}</span>
          <input name="${k}" ${f.unit === "date" ? 'type="date"' : 'inputmode="decimal" type="text"'} value="${val(k, f.unit)}" placeholder="${esc(ph(k))}" autocomplete="off">
          ${f.help ? `<small>${esc(f.help)}</small>` : ""}</label>`; }).join("")}</fieldset>`).join("")}
        <div class="fi-act"><button type="submit" class="fi-btn main">저장하고 다시 셈하기</button><button type="button" class="fi-btn" id="fiPaste">한 번에 붙여넣기</button></div></form>`;
  }

  function facts(d) {
    return `${head("파이어", "셈에 쓴 제도")}${d.facts.map((f) => `<section class="fi-card fi-fact"><h2>${esc(f.t)}</h2><p>${esc(f.d)}</p>${f.src ? `<small>출처: ${esc(f.src)}</small>` : ""}</section>`).join("")}`;
  }

  function draw() {
    const host = $("#fiApp");
    if (!host || !F.d) return;
    host.innerHTML = ({ home, ages, form, facts }[F.sub] || home)(F.d);
  }
  async function reload() { F.d = await GET("/api/fire"); draw(); }

  function readForm(el) {
    const out = {};
    new FormData(el).forEach((v, k) => { out[k] = String(v).trim(); });
    return out;
  }
  async function onClick(e) {
    const a = e.target.closest("[data-age]");
    if (a) { F.age = Number(a.dataset.age); return draw(); }
    if (e.target.closest("#fiPaste")) {
      openSheet("한 번에 붙여넣기", `<label class="field">Claude가 준 자료(JSON)를 그대로 붙여넣어요<textarea id="fiJson" rows="8" placeholder='{"birth":"YYYY-MM-DD", ...}'></textarea></label>
        <button type="button" class="btn dark wide" id="fiJsonGo">넣기</button>`, () => {
        $("#fiJsonGo").onclick = async () => {
          let j;
          try { j = JSON.parse($("#fiJson").value); } catch (err) { return toast("JSON 모양이 아니에요", true); }
          F.d = await api("PUT", "/api/fire", j); closeSheet(); toast("넣었어요"); F.sub = "home"; location.hash = "#fire/home"; draw();
        };
      });
    }
  }
  async function onSubmit(e) {
    if (e.target.id !== "fiForm") return;
    e.preventDefault();
    F.d = await api("PUT", "/api/fire", readForm(e.target));
    toast("저장했어요"); F.age = null; location.hash = "#fire/home";
  }

  pages.fire = {
    async mount(h) {
      F.sub = ["home", "ages", "form", "facts"].includes(h.sub) ? h.sub : "home";
      setTop("파이어", {});
      view.innerHTML = `<div class="fi-app" id="fiApp"><section class="fi-card"><p class="fi-note">계획을 셈하는 중…</p></section></div>`;
      const host = $("#fiApp");
      host.addEventListener("click", (e) => { onClick(e).catch((err) => toast(err.message, true)); });
      host.addEventListener("submit", (e) => { onSubmit(e).catch((err) => toast(err.message, true)); });
      await reload();
    },
  };
})();
