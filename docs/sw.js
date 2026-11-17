/* 화면 파일만 저장해 두고(오프라인에서도 앱이 열리게), 데이터 요청은 항상 서버로 보낸다. */
const CACHE = "moa-261118.0829";
const SHELL = ["./", "index.html", "manifest.webmanifest", "icon-192.png", "icon-512.png"];
self.addEventListener("install", (e) => { e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL))); self.skipWaiting(); });
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k)))));
  self.clients.claim();
});
self.addEventListener("fetch", (e) => {
  const u = new URL(e.request.url);
  if (e.request.method !== "GET" || u.origin !== location.origin || u.pathname.includes("/api/")) return;
  // 브라우저 캐시를 건너뛰고 새 버전을 먼저 받아 보고(GitHub Pages는 10분 캐시), 안 되면 저장본
  // 주소(문자열)로 다시 요청한다: 화면 이동 요청을 그대로 넘기면 아이폰에서 실패해 빈 화면이 될 수 있다
  e.respondWith(fetch(u.href, { cache: "no-cache", credentials: "same-origin" }).then((r) => {
    if (!r || !r.ok) throw new Error("bad response");
    const copy = r.clone();
    caches.open(CACHE).then((c) => c.put(u.href, copy)).catch(() => {});
    return r;
  }).catch(() => caches.match(u.href, { ignoreSearch: e.request.mode === "navigate" })
    .then((r) => r || caches.match("index.html")).then((r) => r || fetch(e.request))));
});
