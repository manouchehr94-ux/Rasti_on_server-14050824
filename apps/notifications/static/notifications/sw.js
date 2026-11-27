/* Order push carries only an encrypted, opaque inbox ID. */
var SUMMARY_URL = '__SUMMARY_URL__';
var INBOX_URL = '__INBOX_URL__';

self.addEventListener('install', function () { self.skipWaiting(); });
self.addEventListener('activate', function (e) { e.waitUntil(self.clients.claim()); });

self.addEventListener('push', function (event) {
  event.waitUntil((async function () {
    try {
      var wanted = event.data ? event.data.json().item : null;
      if (event.data && !/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(wanted)) return;
      var summary = wanted ? SUMMARY_URL + '?push_item=' + encodeURIComponent(wanted) : SUMMARY_URL;
      var item = null, response;
      try {
        response = await fetch(summary, { credentials: 'same-origin', headers: { Accept: 'application/json' }, cache: 'no-store', redirect: 'manual' });
      } catch (_) { /* Targeted alerts can still show a generic notice offline. */ }
      if (response && response.ok && (response.headers.get('content-type') || '').includes('json')) {
        var data = await response.json();
        item = wanted ? data.item : (data.items || []).find(function (candidate) { return !candidate.read; });
      } else if (wanted && (!response || response.status === 401)) {
        item = {id: wanted};
      }
      // A scoped 403/404 means revoked, disabled, read, or another user's item.
      if (!item || (wanted && item.id !== wanted)) return;
      var url = INBOX_URL + item.id + '/' + (wanted ? 'go/?push=1' : (item.has_link ? 'go/' : ''));
      await self.registration.showNotification(String(item.title || 'در پنل راستی‌سی اعلان تازه‌ای دارید').slice(0, 120), {
        tag: 'rs-' + item.id, dir: 'rtl', lang: 'fa', data: { url: url }, requireInteraction: item.severity >= 4,
      });
    } catch (e) { /* بی‌صدا: اعلانِ درون‌برنامه‌ای همچنان در دسترس است */ }
  })());
});

self.addEventListener('notificationclick', function (event) {
  event.notification.close();
  var target = (event.notification.data && event.notification.data.url) || INBOX_URL;
  if (typeof target !== 'string' || target.charAt(0) !== '/' || target.charAt(1) === '/') target = INBOX_URL;   // فقط مسیرِ همین سایت
  event.waitUntil((async function () {
    var all = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (var i = 0; i < all.length; i++) { if ('focus' in all[i]) { await all[i].navigate(target); return all[i].focus(); } }
    return self.clients.openWindow(target);
  })());
});
