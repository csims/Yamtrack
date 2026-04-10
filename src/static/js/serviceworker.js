const CACHE_NAME = 'yamtrack-v1';
const urlsToCache = [
  '/',
  '/static/css/main.css',
  '/static/favicon/android-chrome-192x192.png',
  '/static/favicon/android-chrome-512x512.png',
  '/static/fonts/roboto-flex.woff2',
  '/static/fonts/Figtree-Italic-VariableFont_wght.ttf',
  '/static/fonts/Figtree-VariableFont_wght.ttf',
  '/static/fonts/Fraunces-Italic-VariableFont_SOFT,WONK,opsz,wght.ttf',
  '/static/fonts/Fraunces-VariableFont_SOFT,WONK,opsz,wght.ttf'
];

// Install event
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => {
        return cache.addAll(urlsToCache);
      })
  );
});

// Fetch event
self.addEventListener('fetch', (event) => {
  event.respondWith(
    caches.match(event.request)
      .then((response) => {
        return response || fetch(event.request);
      }
    )
  );
});

// Activate event
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames.map((cacheName) => {
          if (cacheName !== CACHE_NAME) {
            return caches.delete(cacheName);
          }
        })
      );
    })
  );
});
