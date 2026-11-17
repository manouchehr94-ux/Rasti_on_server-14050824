/* Opt-in large management dialogs. Native dialog owns top-layer/inert semantics.
 * Future forms: data-management-modal + data-management-form;
 * controls: data-management-open="dialog-id" / data-management-close.
 * Integration drawer IDs/data attributes remain supported for deep links.
 */
(function () {
  'use strict';
  if (window.RastiManagementModal) return;
  var states = new WeakMap();
  var automaticSeen = new WeakSet();
  var selector = 'dialog[data-management-modal]';
  function forms(d) { return Array.from(d.querySelectorAll('form[data-management-form], form[data-rs-form]')); }
  function snapshot(d) {
    return JSON.stringify(forms(d).map(function (f) {
      return Array.from(new FormData(f)).filter(function (x) { return x[0] !== 'csrfmiddlewaretoken'; }).map(function (x) {
        return [x[0], x[1] instanceof File ? [x[1].name, x[1].size, x[1].name ? x[1].lastModified : 0] : x[1]];
      });
    }));
  }
  function capture(d) {
    return forms(d).flatMap(function (f) { return Array.from(f.elements).filter(function (e) {
      return e.matches('input,select,textarea');
    }).map(function (e) { return {element: e, value: e.value, checked: e.checked, disabled: e.disabled,
      files: e.type === 'file' ? e.files : null,
      selected: e.tagName === 'SELECT' ? Array.from(e.options).map(function (o) { return o.selected; }) : null}; }); });
  }
  function restore(values) {
    values.forEach(function (s) {
      s.element.disabled = s.disabled;
      if (s.files) s.element.files = s.files;
      else if (s.selected) Array.from(s.element.options).forEach(function (o, i) { o.selected = s.selected[i]; });
      else { s.element.value = s.value; s.element.checked = s.checked; }
    });
  }
  function lock() { document.documentElement.classList.toggle('au-management-open', !!document.querySelector(selector + '[open]')); }
  function visibleControls(d) {
    return Array.from(d.querySelectorAll('button,input:not([type=hidden]),select,textarea,a[href],[tabindex]')).filter(function (e) {
      return !e.disabled && e.tabIndex >= 0 && e.getClientRects().length > 0;
    });
  }
  function finish(d) {
    if (d.open) return; // A queued close event may belong to an earlier opening.
    var s = states.get(d); lock();
    if (!s) return;
    if (s.opener) s.opener.setAttribute('aria-expanded', 'false');
    if (!document.querySelector(selector + '[open]') && s.opener && s.opener.isConnected) s.opener.focus({preventScroll: true});
  }
  function requestClose(d) {
    if (!d || !d.open) return true;
    var s = states.get(d);
    if (s && snapshot(d) !== s.snapshot) {
      if (!window.confirm('تغییرهای ذخیره‌نشده از بین می‌روند. بسته شود؟')) return false;
      restore(s.values);
    }
    d.close();
    return true;
  }
  function wire(d) {
    if (states.has(d)) return;
    states.set(d, {});
    d.addEventListener('cancel', function (e) { e.preventDefault(); requestClose(d); });
    d.addEventListener('close', function () { finish(d); });
    d.addEventListener('keydown', function (e) {
      if (e.key !== 'Tab') return;
      var controls = visibleControls(d), first = controls[0], last = controls[controls.length - 1];
      if (!first) { e.preventDefault(); d.focus(); return; }
      if (e.shiftKey && (document.activeElement === first || document.activeElement === d)) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    });
  }
  function open(d, from) {
    if (!d || !d.matches(selector) || typeof d.showModal !== 'function') return false;
    wire(d);
    if (d.open) return true;
    var other = document.querySelector(selector + '[open]');
    if (other && !requestClose(other)) return false;
    var opener = from || Array.from(document.querySelectorAll('[data-drawer-open],[data-management-open]')).find(function (e) {
      return (e.getAttribute('data-management-open') || e.getAttribute('data-drawer-open')) === d.id;
    }) || document.activeElement;
    states.set(d, {opener: opener, snapshot: snapshot(d), values: capture(d)});
    d.showModal(); lock();
    opener && opener.setAttribute('aria-expanded', 'true');
    var bad = d.querySelector('[aria-invalid="true"]');
    if (bad) {
      var details = bad.closest('details');
      while (details) { details.open = true; details = details.parentElement.closest('details'); }
    }
    var fields = visibleControls(d).filter(function (e) { return e.matches('input:not([type=checkbox]):not([type=radio]),textarea,select'); });
    var field = fields.find(function (e) { return e.tagName === 'TEXTAREA'; }) || fields[0];
    (bad || field || visibleControls(d)[0] || d).focus();
    return true;
  }
  function init() {
    document.querySelectorAll(selector).forEach(wire);
    var automatic = document.querySelector(selector + '[data-autoopen]');
    var provider = new URLSearchParams(location.search).get('manage');
    var target = automatic || (provider && document.getElementById('drawer-' + provider));
    if (target && !automaticSeen.has(target) && open(target)) automaticSeen.add(target);
    else if (!target && provider) {
      // A provider-only deep link starts on overview: follow its existing,
      // server-authorized tab link rather than inventing a route/provider map.
      var card = document.getElementById('int-' + provider);
      var link = card && card.querySelector('.rs-svc__actions a[href]');
      if (link && link.href !== location.href) location.replace(link.href);
    }
  }
  document.addEventListener('click', function (e) {
    var t = e.target.closest && e.target.closest('[data-management-open],[data-drawer-open]');
    if (t) {
      var d = document.getElementById(t.getAttribute('data-management-open') || t.getAttribute('data-drawer-open'));
      if (d && d.matches(selector)) { e.preventDefault(); open(d, t); }
      return;
    }
    var close = e.target.closest && e.target.closest('[data-management-close],[data-drawer-close]');
    if (close) {
      var parent = close.closest(selector);
      if (parent) { e.preventDefault(); requestClose(parent); }
      return;
    }
    if (e.target.matches && e.target.matches(selector + '[open]')) {
      var r = e.target.getBoundingClientRect();
      if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) requestClose(e.target);
    }
  });
  window.RastiManagementModal = Object.freeze({open: open, close: requestClose, init: init});
  document.addEventListener('htmx:afterSwap', init);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, {once: true}); else init();
})();
