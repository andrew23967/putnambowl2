/* PutnamBowl v3 - the only site-wide script.
   1. Times: the server writes UTC ISO strings into data-utc-* attributes and
      the browser renders them in its own zone, so a member in Denver and one
      in Boston each read the right clock. A day label and its time must come
      from the same attribute pair, never a server-rendered weekday.
   2. Countdowns: [data-countdown-to="<iso>"] ticks to one moment.
   3. Site state: while signed in, poll /site-state/ and reload when the week
      is published, locked or advanced, so an open tab never shows a stale slate. */
(function () {
  var fmt = {
    date: { month: 'short', day: 'numeric' },
    time: { hour: 'numeric', minute: '2-digit', timeZoneName: 'short' },
    full: { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZoneName: 'short' },
    day: { weekday: 'short', month: 'short', day: 'numeric' },
    daytime: { weekday: 'short', hour: 'numeric', minute: '2-digit' }
  };
  function conv(el, iso, opts) {
    try { el.textContent = new Date(iso).toLocaleString(undefined, opts); } catch (e) {}
  }
  function initUtcDates(root) {
    var scope = root || document;
    Object.keys(fmt).forEach(function (kind) {
      var attr = 'data-utc-' + kind;
      scope.querySelectorAll('[' + attr + ']').forEach(function (el) {
        conv(el, el.getAttribute(attr), fmt[kind]);
      });
    });
  }
  window.initUtcDates = initUtcDates;
  initUtcDates();

  function pad(n) { return String(n).padStart(2, '0'); }
  function countdownText(diff) {
    if (diff <= 0) return '00:00:00';
    if (diff >= 86400000) {
      return Math.floor(diff / 86400000) + 'd ' + pad(Math.floor(diff % 86400000 / 3600000)) + 'h ' +
        pad(Math.floor(diff % 3600000 / 60000)) + 'm';
    }
    return pad(Math.floor(diff / 3600000)) + ':' + pad(Math.floor(diff % 3600000 / 60000)) + ':' +
      pad(Math.floor(diff % 60000 / 1000));
  }
  window.countdownText = countdownText;
  function tickCountdowns() {
    var now = Date.now();
    document.querySelectorAll('[data-countdown-to]').forEach(function (el) {
      var ts = Date.parse(el.getAttribute('data-countdown-to'));
      if (isNaN(ts)) return;
      var diff = ts - now;
      if (diff <= 0 && el.getAttribute('data-countdown-done')) {
        el.textContent = el.getAttribute('data-countdown-done');
        return;
      }
      el.textContent = countdownText(diff);
    });
  }
  if (document.querySelector('[data-countdown-to]')) {
    tickCountdowns();
    setInterval(tickCountdowns, 1000);
  }

  var stateUrl = document.body.getAttribute('data-site-state');
  if (stateUrl) {
    var state = null;
    function poll() {
      fetch(stateUrl, { credentials: 'same-origin' }).then(function (r) { return r.json(); }).then(function (s) {
        if (!state) { state = s; return; }
        if (s.week !== state.week || s.publish !== state.publish || s.lock_picks !== state.lock_picks) {
          window.location.reload();
        }
      }).catch(function () {});
    }
    poll();
    setInterval(poll, 15000);
  }

  // Any <details> menu in the nav closes when you click elsewhere.
  document.addEventListener('click', function (e) {
    document.querySelectorAll('details.nav-menu[open], details.nav-burger[open]').forEach(function (d) {
      if (!d.contains(e.target)) d.removeAttribute('open');
    });
  });
})();
