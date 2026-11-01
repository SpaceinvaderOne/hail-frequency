const CACHE_NAME = "hailfrequency-1.0.0";
const APP_SHELL = [
    "/",
    "/static/css/app.css",
    "/static/js/app.js",
    "/static/js/audio.js",
    "/static/js/voice.js",
    "/static/js/nowplaying.js",
    "/static/js/browse.js",
    "/static/js/setup.js",
    "/static/js/settings.js",
    "/static/fonts/antonio-latin.woff2",
    "/static/fonts/antonio-latin-ext.woff2",
    "/static/fonts/orbitron-latin.woff2",
    "/static/fonts/exo2-latin.woff2",
    "/static/fonts/sharetechmono-latin.woff2",
    "/static/fonts/michroma-latin.woff2",
    "/static/fonts/rajdhani-500-latin.woff2",
    "/static/fonts/rajdhani-700-latin.woff2",
    "/static/img/icon.png",
    "/static/manifest.json",
];

// All SW fetches use cache:"no-cache" — go to the server (a 304 still
// serves the local body) instead of trusting the browser's HTTP cache,
// whose heuristic freshness otherwise feeds us stale files forever.
self.addEventListener("install", (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) =>
            cache.addAll(APP_SHELL.map((url) => new Request(url, { cache: "no-cache" })))
        )
    );
    self.skipWaiting();
});

self.addEventListener("activate", (event) => {
    event.waitUntil(
        caches.keys().then((keys) =>
            Promise.all(
                keys
                    .filter((key) => key !== CACHE_NAME)
                    .map((key) => caches.delete(key))
            )
        )
    );
    self.clients.claim();
});

self.addEventListener("fetch", (event) => {
    const url = new URL(event.request.url);

    // API calls: network only (responses are never cached, so a cache
    // fallback could never hit anyway)
    if (url.pathname.startsWith("/api/")) {
        return;
    }

    // Navigations (app shell): network-first so UI updates appear without
    // a cache-version bump; fall back to the cached shell when offline
    if (event.request.mode === "navigate") {
        event.respondWith(
            fetch(event.request, { cache: "no-cache" })
                .then((response) => {
                    if (response.ok) {
                        const clone = response.clone();
                        caches.open(CACHE_NAME).then((cache) => cache.put("/", clone));
                    }
                    return response;
                })
                .catch(() => caches.match("/"))
        );
        return;
    }

    // Static assets: stale-while-revalidate — serve cached immediately,
    // refresh the cache in the background for next load
    event.respondWith(
        caches.match(event.request).then((cached) => {
            const network = fetch(event.request, { cache: "no-cache" })
                .then((response) => {
                    if (response.ok) {
                        const clone = response.clone();
                        caches.open(CACHE_NAME).then((cache) => {
                            cache.put(event.request, clone);
                        });
                    }
                    return response;
                })
                .catch(() => cached);
            return cached || network;
        })
    );
});
