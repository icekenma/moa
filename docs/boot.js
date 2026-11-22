/* 화면을 그리기 전에 먼저 도는 작은 준비 코드. (보안 정책상 HTML 안에 스크립트를 직접 쓰지 않고 파일로 둔다) */
/* 다른 사이트가 이 앱을 틀(iframe) 안에 넣어 누르게 만드는 수법을 막는다 */
try { if (window.top !== window.self) document.documentElement.style.display = "none"; } catch (e) { document.documentElement.style.display = "none"; }
/* 우석World: 앱을 새로 켜면(홈 화면 아이콘) 첫 화면부터. 쓰던 중에 다시 불러올 때는 보던 화면 그대로 */
try { if (!sessionStorage.getItem("moa.launched")) { sessionStorage.setItem("moa.launched", "1"); if (!location.hash || location.hash === "#home") history.replaceState(null, "", location.pathname + location.search + "#world"); } } catch (e) {}
var bootSec = (location.hash || "#world").slice(1).split(/[/?]/)[0] || "world";
document.documentElement.dataset.sec = bootSec;
/* 자기 겉모습을 가진 구역(첫 화면·건강관리 등)은 저장한 테마를 입히지 않는다. 목록은 app.js가 적어 둔다 */
var bootSkin = false; try { bootSkin = JSON.parse(localStorage.getItem("moa.skinSecs") || '["world","health"]').indexOf(bootSec) >= 0; } catch (e) {}
/* 저장한 색상 테마를 화면 그리기 전에 먼저 적용(깜빡임 방지) */
try { var tv = bootSkin ? null : JSON.parse(localStorage.getItem("moa.themeVars") || "null"); if (tv) { for (var k in tv) document.documentElement.style.setProperty(k, tv[k]); var mc = document.querySelector('meta[name="theme-color"]'); if (mc && tv["--deep"]) mc.content = tv["--deep"]; } } catch (e) {}
try { if (!bootSkin && (localStorage.getItem("moa.themeV") !== "5" || localStorage.getItem("moa.dark") === "1")) document.documentElement.classList.add("dark-theme"); document.documentElement.dataset.neon = localStorage.getItem("moa.neon") || ""; } catch (e) {}
/* 투자 화면의 게임 테마(설정 → 화면에서 끌 수 있다). 투자 화면에서 열면 처음부터 입혀 깜빡임을 막는다 */
try { if (localStorage.getItem("moa.gameInvest") !== "0" && /^#invest/.test(location.hash)) document.documentElement.classList.add("gm-on"); } catch (e) {}
/* 글자 크기(작게·보통·크게) */
try { var fz = { m: 1.15, l: 1.3 }[localStorage.getItem("moa.fontSize")]; if (fz) { document.documentElement.style.zoom = fz; document.documentElement.style.setProperty("--zoom", fz); } } catch (e) {}
/* 앱이 안 뜨면(빈 화면) 한 번 자동으로 다시 받고, 그래도 안 되면 이유와 다시 불러오기 버튼을 보여 준다 */
(function () {
  var errs = [];
  window.addEventListener("error", function (e) { errs.push((e.message || "오류") + (e.filename ? " (" + String(e.filename).split("/").pop() + ":" + e.lineno + ")" : "")); });
  window.addEventListener("unhandledrejection", function (e) { errs.push(String((e.reason && e.reason.message) || e.reason)); });
  function fresh() { location.replace(location.pathname + "?u=" + Date.now() + location.hash); }
  setTimeout(function () {
    if (window.__moaReady) return;
    var tried = null; try { tried = sessionStorage.getItem("moa.retry"); sessionStorage.setItem("moa.retry", "1"); } catch (e) {}
    if (!tried) return fresh();
    var d = document.createElement("div");
    d.style.cssText = "position:fixed;inset:0;z-index:9999;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px;padding:24px;background:#0B0710;color:#fff;font:15px/1.6 -apple-system,sans-serif;text-align:center";
    d.innerHTML = "<b style='font-size:18px'>화면을 불러오지 못했어요</b><span style='opacity:.75'>업데이트 직후 잠깐 이럴 수 있어요. 아래 버튼을 눌러 주세요.</span>" +
      "<button id='bootRe' style='padding:12px 22px;border:0;border-radius:14px;background:#FF3DAE;color:#fff;font-weight:800;font-size:15px'>다시 불러오기</button>" +
      "<small style='opacity:.55;word-break:break-all'>" + (errs.slice(0, 3).join(" / ") || "") + "</small>";
    document.body.appendChild(d);
    document.getElementById("bootRe").onclick = function () {
      try { sessionStorage.removeItem("moa.retry"); } catch (e) {}
      var done = function () { fresh(); };
      try {
        Promise.all([window.caches ? caches.keys().then(function (ks) { return Promise.all(ks.map(function (k) { return caches.delete(k); })); }) : 0,
          navigator.serviceWorker ? navigator.serviceWorker.getRegistrations().then(function (rs) { return Promise.all(rs.map(function (r) { return r.unregister(); })); }) : 0]).then(done, done);
      } catch (e) { done(); }
    };
  }, 7000);
})();
