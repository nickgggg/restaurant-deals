const CACHE_VERSION = "v5";
const SHELL_CACHE = `restaurant-deals-shell-${CACHE_VERSION}`;
const DATA_CACHE = `restaurant-deals-data-${CACHE_VERSION}`;
const SHELL_FILES = [
  "./",
  "./index.html",
  "./styles.css?v=17",
  "./app.js?v=19",
  "./analytics-config.js",
  "./favicon.svg",
  "./icon-192.png",
  "./icon-512.png",
  "./manifest.webmanifest",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL_CACHE).then((cache) => cache.addAll(SHELL_FILES)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key.startsWith("restaurant-deals-") && ![SHELL_CACHE, DATA_CACHE].includes(key)).map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

async function notifyCachedData(url) {
  const clients = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
  for (const client of clients) client.postMessage({ type: "OFFLINE_DATA_USED", url });
}

async function networkFirst(request, cacheName, notifyOnFallback = false) {
  const cache = await caches.open(cacheName);
  try {
    const response = await fetch(request);
    if (response.ok) await cache.put(request, response.clone());
    return response;
  } catch (error) {
    const cached = await cache.match(request, { ignoreSearch: true });
    if (!cached) throw error;
    if (notifyOnFallback) await notifyCachedData(request.url);
    return cached;
  }
}

async function staleWhileRevalidate(request) {
  const cache = await caches.open(SHELL_CACHE);
  const cached = await cache.match(request);
  const update = fetch(request)
    .then((response) => {
      if (response.ok) cache.put(request, response.clone());
      return response;
    })
    .catch(() => null);
  return cached || update;
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (request.mode === "navigate") {
    event.respondWith(networkFirst(request, SHELL_CACHE).catch(() => caches.match("./index.html")));
    return;
  }
  if (url.pathname.includes("/data/") && url.pathname.endsWith(".json")) {
    event.respondWith(networkFirst(request, DATA_CACHE, true));
    return;
  }
  event.respondWith(staleWhileRevalidate(request));
});
