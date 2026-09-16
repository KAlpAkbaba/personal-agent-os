/**
 * B11 req 372: the Push API's service worker. This file's only two jobs are receiving a
 * `push` event and handling the click on the notification it showed — nothing else.
 *
 * Deliberately no `fetch` handler. `app/manifest.ts` documents this app's decision that
 * the Core (`/core`) ships "no service worker, no offline cache" because a cached shell
 * that renders yesterday's state would be the most expensive lie a living-presence page
 * could tell. Registering this worker (`app/lib/cockpit/webpush.ts:registerAndSubscribe`,
 * required by the Push API — a subscription cannot exist without one) does not go back on
 * that: with no `fetch` listener, this worker never intercepts a network request and never
 * serves anything from a cache. It exists purely so the browser has somewhere to deliver a
 * push event while no tab is open, which a page script cannot do on its own.
 *
 * The push payload is the plaintext `app.webpush.service.build_payload` encrypts (RFC
 * 8291) — `{ notification_id, title, body, tag, url? }`, already JSON, already bounded in
 * size and field length on the server. This worker trusts that shape but never trusts the
 * CONTENT as anything other than text to display: `showNotification`'s `body`/`title` are
 * rendered as plain text by the browser (no HTML/script execution), so nothing here can
 * turn a crafted title into a page action.
 */

self.addEventListener("push", (event) => {
  let payload = { title: "Bildirim", body: "", tag: "", url: "" };
  try {
    if (event.data) payload = { ...payload, ...event.data.json() };
  } catch {
    // Not JSON, or empty. Fall back to the honest, empty defaults above rather than
    // failing the event — a push the payload can't be read from still deserves SOME
    // notification, since the ladder already counted this as reaching the owner.
  }

  const title = typeof payload.title === "string" && payload.title ? payload.title : "Bildirim";
  const options = {
    body: typeof payload.body === "string" ? payload.body : "",
    tag: typeof payload.tag === "string" ? payload.tag : undefined,
    // A later push sharing the same tag REPLACES the shown notification rather than
    // stacking a second one — the same "a newer one supersedes an older sibling" rule
    // app.notifications.service._supersede_older_in_group applies server-side, kept
    // consistent here for the one channel the server cannot enforce it on directly.
    renotify: Boolean(payload.tag),
    data: { url: typeof payload.url === "string" ? payload.url : "" },
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const targetPath = event.notification.data && event.notification.data.url;
  // Same-origin paths only (module docstring's trust boundary): `targetPath` is data
  // this system's own payload put there, but resolving it against `self.location.origin`
  // rather than treating it as an absolute URL means even a malformed value can only ever
  // land somewhere on this origin, never navigate the owner off it.
  const url = targetPath && typeof targetPath === "string" ? new URL(targetPath, self.location.origin).href : self.location.origin;

  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clients) => {
      for (const client of clients) {
        if (client.url === url && "focus" in client) return client.focus();
      }
      return self.clients.openWindow(url);
    }),
  );
});
