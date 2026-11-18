/* Audiora player — one HTML5 audio element, HTMX-swappable shell.
   Handles play/pause, ±15s skips, seeking, volume + rate memory,
   now-playing markers, and search UI glue. */
(function () {
  'use strict';

  var root = null;
  var audio = null;
  var currentSrc = null;
  var seeking = false;
  var muted = false;
  var rates = [1, 1.25, 1.5, 2];
  var rateIdx = 0;
  var playerCollapsed = false;

  function fmt(s) {
    if (!isFinite(s) || s < 0) s = 0;
    s = Math.round(s);
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    var mm = (h ? String(m).padStart(2, '0') : String(m));
    var ss = String(sec).padStart(2, '0');
    return h ? h + ':' + mm + ':' + ss : mm + ':' + ss;
  }

  function $all(sel, scope) {
    return Array.prototype.slice.call((scope || document).querySelectorAll(sel));
  }

  // Paint the gradient "filled" portion of a range input via --pct.
  function paint(el) {
    var max = parseFloat(el.max) || 0;
    var pct = max > 0 ? Math.min(100, Math.max(0, (parseFloat(el.value) || 0) / max * 100)) : 0;
    el.style.setProperty('--pct', pct + '%');
  }

  // Highlight the sidebar / bottom-nav link matching the current URL.
  function markActiveNav() {
    var path = window.location.pathname;
    $all('[data-nav]').forEach(function (a) {
      var href = a.getAttribute('href');
      a.classList.toggle('is-active', href === path);
    });
  }

  // Storage may be unavailable (private browsing / blocked site data).
  function stored(key, fallback) {
    try {
      var value = localStorage.getItem(key);
      return value === null ? fallback : value;
    } catch (e) { return fallback; }
  }

  /* ---------- Account progress sync (signed-in listeners) ---------- */

  // Server-side resume point rendered onto #global-player by Django.
  function serverPosition() {
    var value = parseFloat(root && root.dataset.position);
    return isFinite(value) && value > 0 ? value : 0;
  }

  function csrfToken() {
    var match = (document.cookie || '').match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : '';
  }

  // Fire-and-forget POST so a listener can resume on another device.
  function pushProgress(completed) {
    if (!root || !root.dataset.progressUrl) return;
    if (typeof fetch !== 'function') return;
    if (!completed && audio.currentTime < 0.5) return;
    var duration = isFinite(audio.duration) ? Math.round(audio.duration) : 0;
    var position = completed && duration
      ? duration
      : Math.floor(audio.currentTime || 0);
    try {
      var data = new FormData();
      data.append('position', String(position));
      data.append('duration', String(duration));
      if (completed) data.append('completed', '1');
      fetch(root.dataset.progressUrl, {
        method: 'POST',
        body: data,
        keepalive: true,
        credentials: 'same-origin',
        headers: { 'X-CSRFToken': csrfToken() }
      }).catch(function () {});
    } catch (e) { /* the position is still in localStorage */ }
  }

  var lastPush = 0;

  function maybePush() {
    // Only push once playback has actually moved (or is running), so a page
    // load never overwrites a stored resume point with 0.
    if (!currentSrc || pendingSeek !== null) return;
    if (audio.currentTime < 0.5 && audio.paused) return;
    if (Date.now() - lastPush < 10000) return;
    lastPush = Date.now();
    pushProgress(false);
  }

  function playAudio() {
    var p = audio.play();
    if (p && p.catch) p.catch(function () {
      // Autoplay can be blocked after an asynchronous HTMX request.
      setPlayingUI(!audio.paused);
    });
  }

  var pendingResume = 0;
  var pendingSeek = null;

  function seekTo(seconds) {
    if (!audio || !currentSrc || !isFinite(seconds)) return;
    var duration = isFinite(audio.duration) ? audio.duration : (parseFloat(root.dataset.duration) || 0);
    var target = Math.max(0, duration > 0 ? Math.min(duration, seconds) : seconds);
    pendingResume = 0;
    if (audio.readyState === 0) {
      pendingSeek = target;
      return;
    }
    pendingSeek = null;
    audio.currentTime = target;
    seeking = false;
    timeTick();
  }

  function skip(seconds) {
    var position = pendingSeek !== null ? pendingSeek : (pendingResume || audio.currentTime);
    seekTo(position + seconds);
  }

  function restorePosition() {
    if (pendingSeek !== null) {
      seekTo(pendingSeek);
    }
    if (pendingResume > 0) {
      var duration = audio.duration;
      // A completed book should restart, not immediately end again.
      audio.currentTime = isFinite(duration) && pendingResume >= duration - 1 ? 0 : pendingResume;
      pendingResume = 0;
    }
    syncTotal();
    timeTick();
  }

  /* ---------- Bottom-chrome clearance (fix #1) ---------- */

  // Rendered height of a fixed element (0 when not displayed).
  function chromeHeight(el) {
    if (!el) return 0;
    return getComputedStyle(el).display === 'none' ? 0 : el.offsetHeight;
  }

  // Keep the scrollable content clear of whatever is pinned to the bottom:
  // desktop player bar, mobile mini player, mobile bottom navigation.
  function updateContentPadding() {
    var bar = chromeHeight(document.getElementById('player-bar'));
    var mini = chromeHeight(document.getElementById('player-mini'));
    var nav = chromeHeight(document.querySelector('nav[aria-label="Bottom navigation"]'));
    // The mini player floats 18px above the bottom nav; add breathing room.
    var pad = bar + (mini ? mini + 18 : 0) + nav + 32;
    document.documentElement.style.setProperty('--content-pad-bottom', pad + 'px');
  }

  // Desktop player collapse state. The class lives on <html> so the head
  // script can apply a remembered preference before first paint.
  function applyPlayerCollapsed(collapsed) {
    playerCollapsed = collapsed;
    document.documentElement.classList.toggle('player-collapsed', collapsed);
    var btn = document.querySelector('[data-action="toggle-collapse"]');
    if (btn) {
      btn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
      btn.setAttribute('aria-label', collapsed ? 'Expand player' : 'Collapse player');
      btn.title = collapsed ? 'Expand player' : 'Collapse player';
    }
    updateContentPadding();
  }

  function togglePlayerCollapsed() {
    applyPlayerCollapsed(!playerCollapsed);
    try {
      localStorage.setItem('ab.playerCollapsed', playerCollapsed ? '1' : '0');
    } catch (e) {}
  }

  /* ---------- Mobile navigation drawer (fix #2) ---------- */

  // Freeze the page behind any open drawer: no background scrolling, no
  // fixed-bottom chrome peeking through while momentum-scrolling.
  function syncScrollLock() {
    var menu = document.getElementById('nav-drawer');
    var search = document.getElementById('search-drawer');
    var anyOpen = (menu && menu.classList.contains('is-open')) ||
                  (search && !search.classList.contains('hidden'));
    document.body.style.overflow = anyOpen ? 'hidden' : '';
  }

  function openMenu() {
    var drawer = document.getElementById('nav-drawer');
    if (!drawer) return;
    drawer.classList.remove('is-closed');
    drawer.classList.add('is-open');
    var btn = document.querySelector('[data-open-menu]');
    if (btn) btn.setAttribute('aria-expanded', 'true');
    syncScrollLock();
  }

  function closeMenu() {
    var drawer = document.getElementById('nav-drawer');
    if (!drawer || drawer.classList.contains('is-closed')) return;
    drawer.classList.remove('is-open');
    drawer.classList.add('is-closed');
    var btn = document.querySelector('[data-open-menu]');
    if (btn) btn.setAttribute('aria-expanded', 'false');
    syncScrollLock();
  }

  /* ---------- Track loading ---------- */

  function syncTrack(autoplay) {
    root = document.getElementById('global-player');
    audio = document.getElementById('audio');
    if (!root || !audio) return;

    var src = root.dataset.src || '';
    if (src && src !== currentSrc) {
      currentSrc = src;
      // The server copy wins on a fresh device; localStorage wins mid-session.
      pendingResume = Math.max(
        0,
        parseFloat(stored('ab.position.' + root.dataset.trackId, '0')) || 0,
        serverPosition()
      );
      seeking = false;
      pendingSeek = null;
      audio.src = src;
      audio.load();
      audio.playbackRate = rates[rateIdx];
      audio.muted = muted;
    }
    if (src) {
      root.hidden = false;
      updateMediaSession();
      markNowPlaying();
      updateContentPadding();
      if (autoplay) playAudio();
    }
    setPlayingUI(!audio.paused);
    applyVolumeUI();
    applyRateUI();
    syncTotal();
    if (!pendingResume) timeTick();
  }

  function markNowPlaying() {
    var id = root ? root.dataset.trackId : '';
    $all('[data-book-card]').forEach(function (card) {
      card.classList.toggle('now-playing', card.dataset.bookId === id);
    });
  }

  function updateMediaSession() {
    if (!('mediaSession' in navigator) || typeof MediaMetadata === 'undefined') return;
    navigator.mediaSession.metadata = new MediaMetadata({
      title: root.dataset.title || '',
      artist: root.dataset.author || '',
      album: 'Audiora',
      artwork: root.dataset.cover ? [{ src: root.dataset.cover, sizes: '600x600' }] : []
    });
  }

  /* ---------- UI sync ---------- */

  function setPlayingUI(playing) {
    root.classList.toggle('player-paused', !playing);
    $all('[data-role="icon-play"]', root).forEach(function (el) {
      el.classList.toggle('hidden', playing);
    });
    $all('[data-role="icon-pause"]', root).forEach(function (el) {
      el.classList.toggle('hidden', !playing);
    });
  }

  function timeTick() {
    if (!root || seeking) return;
    var dur = isFinite(audio.duration) ? audio.duration : (parseFloat(root.dataset.duration) || 0);
    $all('[data-role="time-current"]', root).forEach(function (el) { el.textContent = fmt(audio.currentTime); });
    $all('[data-role="seek"]', root).forEach(function (el) {
      el.max = dur || 0;
      el.value = Math.floor(audio.currentTime);
      paint(el);
    });
    if (root.dataset.trackId && currentSrc && !pendingResume && pendingSeek === null) {
      try { localStorage.setItem('ab.position.' + root.dataset.trackId, String(audio.currentTime)); } catch (e) {}
    }
    maybePush();
  }

  function syncTotal() {
    var dur = isFinite(audio.duration) ? Math.round(audio.duration) : (parseInt(root.dataset.duration, 10) || 0);
    $all('[data-role="time-total"]', root).forEach(function (el) { el.textContent = fmt(dur); });
    $all('[data-role="seek"]', root).forEach(function (el) { el.max = dur; paint(el); });
  }

  function applyVolumeUI() {
    var v = audio.muted ? 0 : audio.volume;
    $all('[data-role="volume"]', root).forEach(function (el) { el.value = v; paint(el); });
    $all('[data-role="icon-volume"]', root).forEach(function (el) { el.classList.toggle('hidden', audio.muted); });
    $all('[data-role="icon-muted"]', root).forEach(function (el) { el.classList.toggle('hidden', !audio.muted); });
  }

  function applyRateUI() {
    var r = rates[rateIdx];
    audio.playbackRate = r;
    try { localStorage.setItem('ab.rate', String(r)); } catch (e) {}
    $all('[data-action="rate"]', root).forEach(function (el) {
      el.textContent = (r === 1 ? '1×' : r + '×');
    });
  }

  /* ---------- Events ---------- */

  function bindAudio() {
    if (!audio) return;
    audio.addEventListener('play', function () { setPlayingUI(true); });
    audio.addEventListener('pause', function () { setPlayingUI(false); });
    audio.addEventListener('timeupdate', timeTick);
    audio.addEventListener('loadedmetadata', restorePosition);
    audio.addEventListener('durationchange', syncTotal);
    audio.addEventListener('volumechange', applyVolumeUI);
    audio.addEventListener('ended', function () {
      setPlayingUI(false);
      try { localStorage.removeItem('ab.position.' + root.dataset.trackId); } catch (e) {}
      pushProgress(true);
    });
    audio.addEventListener('error', function () {
      $all('[data-role="meta"]', root).forEach(function (el) {
        el.textContent = 'Playback unavailable';
      });
    });

    // Last-chance save when the tab is closed or backgrounded.
    window.addEventListener('pagehide', function () { pushProgress(false); });
    document.addEventListener('visibilitychange', function () {
      if (document.visibilityState === 'hidden') pushProgress(false);
    });

    if ('mediaSession' in navigator) {
      navigator.mediaSession.setActionHandler('play', playAudio);
      navigator.mediaSession.setActionHandler('pause', function () { audio.pause(); });
      navigator.mediaSession.setActionHandler('seekbackward', function () { skip(-15); });
      navigator.mediaSession.setActionHandler('seekforward', function () { skip(15); });
    }
  }

  document.addEventListener('click', function (e) {
    var playHit = e.target.closest('[data-play]');
    if (playHit) {
      // htmx fires the request from its own element-level listener;
      // here we only stop the surrounding card link from navigating.
      e.preventDefault();
      e.stopPropagation();
      return;
    }
    if (e.target.closest('[data-open-search]') || e.target.closest('#search-open')) {
      openSearch();
      return;
    }
    if (e.target.closest('[data-close-search]')) {
      closeSearch();
      return;
    }
    if (e.target.closest('[data-open-menu]')) {
      openMenu();
      return;
    }
    if (e.target.closest('[data-close-menu]')) {
      closeMenu();
      return;
    }
    // Close the desktop search panel when clicking outside it
    var panel = document.getElementById('search-panel');
    if (panel && !panel.classList.contains('hidden') &&
        !e.target.closest('#search-panel') && !e.target.closest('input[name="q"]')) {
      panel.classList.add('hidden');
    }

    var action = e.target.closest('[data-action]');
    if (!action || !root || !root.contains(action)) return;
    switch (action.dataset.action) {
      case 'toggle':
        if (audio.paused) playAudio(); else audio.pause();
        break;
      case 'back15':
        skip(-15);
        break;
      case 'fwd15':
        skip(15);
        break;
      case 'mute':
        audio.muted = !audio.muted;
        muted = audio.muted;
        try { localStorage.setItem('ab.muted', muted ? '1' : '0'); } catch (err) {}
        applyVolumeUI();
        break;
      case 'rate':
        rateIdx = (rateIdx + 1) % rates.length;
        applyRateUI();
        break;
      case 'toggle-collapse':
        togglePlayerCollapsed();
        break;
      case 'open-modal':
        var modal = document.getElementById('player-modal');
        if (modal) { modal.classList.remove('hidden'); modal.classList.add('flex'); }
        break;
      case 'close-modal':
        var m = document.getElementById('player-modal');
        if (m) { m.classList.add('hidden'); m.classList.remove('flex'); }
        break;
    }
  });

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' || e.key === ' ') {
      var t = e.target.closest && e.target.closest('span[data-play]');
      if (t) {
        e.preventDefault();
        t.click(); // dispatch a real click so htmx handles it
      }
    }
    if (e.key === 'Escape') { closeSearch(); closeMenu(); }
  });

  document.addEventListener('input', function (e) {
    var t = e.target;
    if (!root || !root.contains(t)) return;
    if (t.matches('[data-role="seek"]')) {
      seeking = true;
      audio.currentTime = parseFloat(t.value) || 0;
      $all('[data-role="seek"]', root).forEach(function (el) { el.value = t.value; paint(el); });
      $all('[data-role="time-current"]', root).forEach(function (el) {
        el.textContent = fmt(audio.currentTime);
      });
    } else if (t.matches('[data-role="volume"]')) {
      audio.volume = parseFloat(t.value);
      audio.muted = audio.volume === 0;
      muted = audio.muted;
      try {
        localStorage.setItem('ab.volume', String(audio.volume));
        localStorage.setItem('ab.muted', muted ? '1' : '0');
      } catch (err) {}
      applyVolumeUI();
    }
  });

  document.addEventListener('change', function (e) {
    if (e.target.matches && e.target.matches('[data-role="seek"]')) seeking = false;
  });

  /* ---------- HTMX glue ---------- */

  // Top progress bar while any navigation request is in flight.
  document.body.addEventListener('htmx:beforeRequest', function () { document.body.classList.add('is-loading'); });
  document.body.addEventListener('htmx:afterRequest', function () { document.body.classList.remove('is-loading'); });
  document.body.addEventListener('htmx:pushedIntoHistory', markActiveNav);
  document.body.addEventListener('htmx:historyRestore', markActiveNav);
  window.addEventListener('popstate', function () { setTimeout(markActiveNav, 0); });

  document.body.addEventListener('htmx:afterSwap', function (e) {
    var target = e.detail && e.detail.target;
    if (!target) return;
    if (target.id === 'global-player') {
      syncTrack(true);
      applyPlayerCollapsed(playerCollapsed);
    }
    if (target.id === 'main-content') {
      closeSearch();
      closeMenu();
      markNowPlaying();
      if (root && root.dataset.trackId) root.classList.remove('hidden');
      updateContentPadding();
    }
    if (target.id === 'search-panel' || target.id === 'search-drawer-results') {
      var panel = document.getElementById('search-panel');
      if (panel && target.id === 'search-panel') panel.classList.remove('hidden');
    }
  });

  // Re-measure the fixed bottom chrome when the viewport changes.
  window.addEventListener('resize', updateContentPadding);

  // Height is animated for collapse/expand; measure again when it settles so
  // the content clearance matches the final 6rem / 3.75rem exactly.
  document.addEventListener('transitionend', function (e) {
    if (e.target && e.target.id === 'player-bar' && e.propertyName === 'height') {
      updateContentPadding();
    }
  });

  /* ---------- Search UI ---------- */

  function openSearch() {
    var drawer = document.getElementById('search-drawer');
    var input = document.getElementById('search-drawer-input');
    if (!drawer) return;
    drawer.classList.remove('hidden');
    if (input) { input.focus(); input.select(); }
    syncScrollLock();
  }

  function closeSearch() {
    var drawer = document.getElementById('search-drawer');
    if (drawer) drawer.classList.add('hidden');
    var panel = document.getElementById('search-panel');
    if (panel) panel.classList.add('hidden');
    syncScrollLock();
  }

  /* ---------- Boot ---------- */

  function boot() {
    markActiveNav();
    playerCollapsed = stored('ab.playerCollapsed', '0') === '1';
    applyPlayerCollapsed(playerCollapsed);
    root = document.getElementById('global-player');
    audio = document.getElementById('audio');
    if (!root || !audio) return;

    rateIdx = Math.max(0, rates.indexOf(parseFloat(stored('ab.rate', '1'))));
    var volume = parseFloat(stored('ab.volume', '1'));
    audio.volume = isFinite(volume) ? Math.min(1, Math.max(0, volume)) : 1;
    muted = stored('ab.muted', '0') === '1';
    audio.muted = muted;
    audio.playbackRate = rates[rateIdx];

    bindAudio();
    // Load server-rendered book pages too, without forcing autoplay.
    syncTrack(false);
    markNowPlaying();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
