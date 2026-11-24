/* 화면 파일만 저장해 두고(오프라인에서도 앱이 열리게), 데이터 요청은 항상 서버로 보낸다. */
const CACHE = "moa-261124.1638";
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
    // 저장본도 없을 때: 화면 이동이면 첫 화면을, 스크립트·그림이면 원래 요청을 그대로(스크립트 자리에 HTML을 주면 안 된다)
    .then((r) => r || (e.request.mode === "navigate" ? caches.match("index.html") : null)).then((r) => r || fetch(e.request))));
});

// 앱 알림(웹 푸시): 서버(노트북)가 보낸 알림을 띄우고, 누르면 그 화면을 연다
self.addEventListener("push", (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (err) { d = { body: e.data ? e.data.text() : "" }; }
  e.waitUntil(self.registration.showNotification(d.title || "우석World", {
    body: d.body || "", tag: d.tag || undefined, renotify: !!d.tag, icon: "icon-192.png", badge: "icon-192.png", data: { url: d.url || "./" },
  }));
});
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const to = new URL(e.notification.data && e.notification.data.url && e.notification.data.url.startsWith("#") ? "./" + e.notification.data.url : (e.notification.data || {}).url || "./", self.registration.scope).href;
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((cs) => {
    const c = cs.find((x) => x.url.startsWith(self.registration.scope));
    if (c) return c.focus().then((w) => (w && w.navigate ? w.navigate(to) : null)).catch(() => self.clients.openWindow(to));
    return self.clients.openWindow(to);
  }));
});
