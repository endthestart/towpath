// Keeps a page that waits on background work current without reloading it. Reloading every few seconds for
// hours made Safari's memory grow until it reset the tab. Every few seconds the page is fetched again and its
// <main> swapped in, until the new page no longer asks to stay live. Pages without scripts use the <noscript>
// refresh instead.
(function () {
  function delay(doc) {
    var meta = doc.querySelector('meta[name="towpath-live"]');
    return meta ? Math.max(2, parseInt(meta.content, 10) || 5) * 1000 : null;
  }

  function busy() {  // never pull a form out from under someone typing in it
    var el = document.activeElement;
    return el && el.closest("main") && /^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName);
  }

  function tick() {
    if (busy() || document.hidden) {
      setTimeout(tick, delay(document) || 5000);
      return;
    }
    fetch(location.href, {credentials: "same-origin", cache: "no-store"}).then(function (response) {
      if (response.redirected) {  // signed out meanwhile: show the page the server chose
        location.reload();
        return null;
      }
      if (!response.ok) throw new Error(String(response.status));
      return response.text();
    }).then(function (html) {
      if (html === null) return;
      var next = new DOMParser().parseFromString(html, "text/html");
      var fresh = next.querySelector("main"), now = document.querySelector("main");
      if (!fresh || !now) throw new Error("no main");
      now.replaceWith(document.adoptNode(fresh));
      document.title = next.title;
      var wait = delay(next);
      if (wait) setTimeout(tick, wait);
    }).catch(function () {
      setTimeout(tick, 30000);  // the server restarting, say; try again a little later
    });
  }

  var wait = delay(document);
  if (wait) setTimeout(tick, wait);
})();
