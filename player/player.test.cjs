const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../static/js/player.js'), 'utf8');

function element(extra = {}) {
  const listeners = {};
  const classes = new Set();
  return Object.assign({
    dataset: {},
    hidden: false,
    classList: {
      toggle(name, enabled) { if (enabled) classes.add(name); else classes.delete(name); },
      add(name) { classes.add(name); },
      remove(name) { classes.delete(name); },
      contains(name) { return classes.has(name); },
    },
    addEventListener(name, callback) { (listeners[name] ||= []).push(callback); },
    dispatch(name, event = {}) { for (const callback of listeners[name] || []) callback(event); },
    listenerCount(name) { return (listeners[name] || []).length; },
    querySelectorAll() { return []; },
    contains() { return false; },
  }, extra);
}

function setup({ track = '', saved = {}, blockedStorage = false, position = 0, progress = '' } = {}) {
  const storage = new Map(Object.entries(saved));
  const fetches = [];
  const audio = element({
    src: '', currentTime: 0, duration: NaN, paused: true, readyState: 0,
    volume: 1, muted: false, playbackRate: 1, loads: 0, plays: 0,
    load() { this.loads++; this.currentTime = 0; this.duration = NaN; this.paused = true; this.readyState = 0; },
    play() { this.plays++; this.paused = false; this.dispatch('play'); return Promise.resolve(); },
    pause() { this.paused = true; this.dispatch('pause'); },
  });
  function shell(id) {
    return element({
      id: 'global-player',
      dataset: {
        trackId: id,
        src: id ? `/media/audio/${id}.mp3` : '',
        duration: '120',
        position: String(position),
        progressUrl: id && progress ? progress.replace('<id>', id) : '',
      },
    });
  }
  let root = shell(track);
  const body = element();
  const document = element({
    readyState: 'complete', body,
    getElementById(id) { return id === 'audio' ? audio : id === 'global-player' ? root : null; },
  });
  vm.runInNewContext(source, {
    document,
    window: { location: { pathname: '/' }, addEventListener() {} },
    navigator: {},
    localStorage: {
      getItem(key) { if (blockedStorage) throw new Error('Storage blocked'); return storage.get(key) ?? null; },
      setItem(key, value) { if (blockedStorage) throw new Error('Storage blocked'); storage.set(key, value); },
      removeItem(key) { if (blockedStorage) throw new Error('Storage blocked'); storage.delete(key); },
    },
    setTimeout,
    FormData,
    fetch(url, options) {
      fetches.push({ url, options, data: options && options.body });
      return Promise.resolve({ ok: true });
    },
  });
  return {
    audio, storage, fetches,
    get root() { return root; },
    select(id) {
      root = shell(id);
      body.dispatch('htmx:afterSwap', { detail: { target: root } });
    },
    click(action) {
      const button = { dataset: { action } };
      root.contains = (target) => target === button;
      document.dispatch('click', {
        target: { closest(selector) { return selector === '[data-action]' ? button : null; } },
      });
    },
    metadata(duration = 120) { audio.readyState = 1; audio.duration = duration; audio.dispatch('loadedmetadata'); },
  };
}

test('direct book page loads a source without forcing autoplay', () => {
  const app = setup({ track: '1' });
  assert.equal(app.audio.src, '/media/audio/1.mp3');
  assert.equal(app.audio.loads, 1);
  assert.equal(app.audio.plays, 0);
  assert.equal(app.root.classList.contains('player-paused'), true);
});

test('selecting the same book preserves audio, time, and event bindings', () => {
  const app = setup();
  app.select('1');
  app.metadata();
  app.audio.currentTime = 42;
  app.select('1');
  assert.equal(app.audio.src, '/media/audio/1.mp3');
  assert.equal(app.audio.currentTime, 42);
  assert.equal(app.audio.loads, 1);
  assert.equal(app.audio.plays, 2);
  assert.equal(app.audio.listenerCount('timeupdate'), 1);
  assert.equal(app.root.classList.contains('player-paused'), false);
});

test('track switches retain volume, mute, and speed', () => {
  const app = setup({ saved: { 'ab.volume': '0.35', 'ab.muted': '1', 'ab.rate': '1.5' } });
  app.select('1');
  app.select('2');
  assert.equal(app.audio.src, '/media/audio/2.mp3');
  assert.equal(app.audio.volume, 0.35);
  assert.equal(app.audio.muted, true);
  assert.equal(app.audio.playbackRate, 1.5);
  assert.equal(app.audio.listenerCount('loadedmetadata'), 1);
});

test('saved progress restores after metadata and is not overwritten while loading', () => {
  const app = setup({ saved: { 'ab.position.1': '45' } });
  app.select('1');
  app.audio.dispatch('timeupdate');
  assert.equal(app.storage.get('ab.position.1'), '45');
  app.metadata();
  assert.equal(app.audio.currentTime, 45);
});

test('switching before metadata does not apply the previous track position', () => {
  const app = setup({ saved: { 'ab.position.1': '45', 'ab.position.2': '20' } });
  app.select('1');
  app.select('2');
  app.metadata();
  assert.equal(app.audio.currentTime, 20);
});

test('completed books restart and clear saved progress on end', () => {
  const app = setup({ track: '1', saved: { 'ab.position.1': '120' } });
  app.metadata();
  assert.equal(app.audio.currentTime, 0);
  app.audio.currentTime = 120;
  app.audio.dispatch('timeupdate');
  app.audio.dispatch('ended');
  assert.equal(app.storage.has('ab.position.1'), false);
});

test('blocked storage does not prevent boot or playback', () => {
  const app = setup({ blockedStorage: true });
  app.select('1');
  app.metadata();
  assert.equal(app.audio.plays, 1);
  assert.equal(app.audio.volume, 1);
});

test('skip buttons move fifteen seconds and save the updated position immediately', () => {
  const app = setup({ track: '1' });
  app.metadata();
  app.audio.currentTime = 40;
  app.click('fwd15');
  assert.equal(app.audio.currentTime, 55);
  assert.equal(app.storage.get('ab.position.1'), '55');
  app.click('back15');
  assert.equal(app.audio.currentTime, 40);
  assert.equal(app.audio.paused, true);
});

test('skip buttons clamp to the beginning and end of the track', () => {
  const app = setup({ track: '1' });
  app.metadata();
  app.audio.currentTime = 5;
  app.click('back15');
  assert.equal(app.audio.currentTime, 0);
  app.audio.currentTime = 115;
  app.click('fwd15');
  assert.equal(app.audio.currentTime, 120);
});

test('skips before metadata are queued relative to saved progress', () => {
  const app = setup({ track: '1', saved: { 'ab.position.1': '45' } });
  app.click('fwd15');
  app.click('fwd15');
  assert.equal(app.audio.currentTime, 0);
  app.metadata();
  assert.equal(app.audio.currentTime, 75);
});

test('switching tracks clears queued skips', () => {
  const app = setup({ track: '1' });
  app.click('fwd15');
  app.select('2');
  app.metadata();
  assert.equal(app.audio.currentTime, 0);
});

test('invalid saved volume is sanitized', () => {
  assert.equal(setup({ saved: { 'ab.volume': 'invalid' } }).audio.volume, 1);
  assert.equal(setup({ saved: { 'ab.volume': '10' } }).audio.volume, 1);
  assert.equal(setup({ saved: { 'ab.volume': '-1' } }).audio.volume, 0);
});

test('server resume point restores on a fresh device', () => {
  const app = setup({ track: '1', position: 75 });
  app.metadata();
  assert.equal(app.audio.currentTime, 75);
});

test('the furthest known position wins between server and local storage', () => {
  const ahead = setup({ track: '1', position: 30, saved: { 'ab.position.1': '10' } });
  ahead.metadata();
  assert.equal(ahead.audio.currentTime, 30);

  const local = setup({ track: '1', position: 10, saved: { 'ab.position.1': '30' } });
  local.metadata();
  assert.equal(local.audio.currentTime, 30);
});

test('a zero server position never overrides local progress', () => {
  const app = setup({ track: '1', position: 0, saved: { 'ab.position.1': '45' } });
  app.metadata();
  assert.equal(app.audio.currentTime, 45);
});

test('playback pushes the position to the account endpoint', () => {
  const app = setup({ track: '1', progress: '/player/progress/<id>/' });
  app.metadata();
  app.audio.currentTime = 12;
  app.audio.dispatch('timeupdate');
  assert.equal(app.fetches.length, 1);
  assert.equal(app.fetches[0].url, '/player/progress/1/');
  assert.equal(app.fetches[0].data.get('position'), '12');
  assert.equal(app.fetches[0].data.get('duration'), '120');
  assert.equal(app.fetches[0].data.get('completed'), null);
});

test('pushes are throttled to roughly every ten seconds', () => {
  const app = setup({ track: '1', progress: '/player/progress/<id>/' });
  app.metadata();
  app.audio.currentTime = 12;
  app.audio.dispatch('timeupdate');
  app.audio.currentTime = 14;
  app.audio.dispatch('timeupdate');
  assert.equal(app.fetches.length, 1);
});

test('guests without an endpoint never push', () => {
  const app = setup({ track: '1' });
  app.metadata();
  app.audio.currentTime = 30;
  app.audio.dispatch('timeupdate');
  assert.equal(app.fetches.length, 0);
});

test('finishing a book reports completion instead of a resume point', () => {
  const app = setup({ track: '1', progress: '/player/progress/<id>/' });
  app.metadata();
  app.audio.currentTime = 120;
  app.audio.dispatch('ended');
  assert.equal(app.fetches.length, 1);
  assert.equal(app.fetches[0].data.get('completed'), '1');
  assert.equal(app.fetches[0].data.get('position'), '120');
  assert.equal(app.storage.has('ab.position.1'), false);
});

test('loading a page never overwrites the stored position with zero', () => {
  const app = setup({ track: '1', progress: '/player/progress/<id>/' });
  app.audio.dispatch('timeupdate');
  assert.equal(app.fetches.length, 0);
});
