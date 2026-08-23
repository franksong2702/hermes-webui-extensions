import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import * as vm from 'node:vm';

const source = await readFile(new URL('../extensions/theme-creator/assets/theme-creator.js', import.meta.url), 'utf8');

const EXTENSION_ID = 'theme-creator';
const PANEL_ID = 'hwxThemeCreatorPanel';
const RAIL_ID = 'hwxThemeCreatorRailBtn';
const STORE_KEY = 'hermes-ext-custom-themes';

const BASE_THEME = {
  key: 'custom-saved',
  name: 'Saved Theme',
  base: {
    bg: '#0d0d1a',
    surface: '#16161f',
    text: '#f5f5f5',
    muted: '#9aa0b5',
    accent: '#f5c542',
    border: '#2a2a3a',
    userBubble: '#26314a',
    bgImage: null,
    glassOpacity: 0.08,
    blur: 20,
  },
};

function findDescendant(node, predicate) {
  for (const child of node?.children || []) {
    if (predicate(child)) return child;
    const nested = findDescendant(child, predicate);
    if (nested) return nested;
  }
  return null;
}

function matches(node, selector) {
  if (!node) return false;
  if (selector === '.rail-spacer') return node.className.split(/\s+/).includes('rail-spacer');
  if (selector.startsWith('#')) return node.id === selector.slice(1);
  if (selector.startsWith('.')) return node.className.split(/\s+/).includes(selector.slice(1));
  if (/^[a-z]+$/i.test(selector)) return node.tagName === selector.toUpperCase();
  return false;
}

function makeHarness({
  configure = true,
  rail = true,
  register = true,
  configureResult = () => true,
  body = true,
  themes = [BASE_THEME],
  initialSkin = 'default',
} = {}) {
  const focusCalls = [];
  const timers = [];
  const registrations = [];
  const configureHandlers = [];
  const skinRegistrations = [];
  const events = [];
  const storage = new Map([
    [STORE_KEY, JSON.stringify(themes)],
    ['hermes-skin', initialSkin],
  ]);
  let document;

  function makeElement(tagName, className = '') {
    const listeners = new Map();
    const queryCache = new Map();
    const element = {
      tagName: tagName.toUpperCase(),
      id: '',
      className,
      children: [],
      parentNode: null,
      isConnected: false,
      hidden: false,
      disabled: false,
      value: '',
      textContent: '',
      innerHTML: '',
      dataset: {},
      files: [],
      style: { setProperty() {}, removeProperty() {} },
      classList: {
        toggle(name, enabled) {
          const names = new Set(element.className.split(/\s+/).filter(Boolean));
          if (enabled) names.add(name); else names.delete(name);
          element.className = [...names].join(' ');
        },
        contains(name) { return element.className.split(/\s+/).includes(name); },
      },
      get firstChild() { return this.children[0] || null; },
      appendChild(child) {
        if (child.parentNode) child.parentNode.children = child.parentNode.children.filter((item) => item !== child);
        this.children.push(child);
        child.parentNode = this;
        child.isConnected = this.isConnected;
        return child;
      },
      append(...children) { children.forEach((child) => this.appendChild(child)); },
      insertBefore(child, reference) {
        if (!this.children.includes(reference)) return this.appendChild(child);
        if (child.parentNode) child.parentNode.children = child.parentNode.children.filter((item) => item !== child);
        this.children.splice(this.children.indexOf(reference), 0, child);
        child.parentNode = this;
        child.isConnected = this.isConnected;
        return child;
      },
      removeChild(child) {
        this.children = this.children.filter((item) => item !== child);
        child.parentNode = null;
        child.isConnected = false;
        return child;
      },
      remove() {
        if (this.parentNode) this.parentNode.removeChild(this);
        this.isConnected = false;
      },
      addEventListener(type, listener) {
        const entries = listeners.get(type) || [];
        entries.push(listener);
        listeners.set(type, entries);
      },
      removeEventListener(type, listener) {
        listeners.set(type, (listeners.get(type) || []).filter((entry) => entry !== listener));
      },
      dispatchEvent(event) {
        const payload = event || {};
        if (!payload.target) payload.target = this;
        for (const listener of [...(listeners.get(payload.type) || [])]) listener.call(this, payload);
      },
      click() { this.dispatchEvent({ type: 'click', target: this }); },
      focus() {
        focusCalls.push(this);
        document.activeElement = this;
      },
      contains(candidate) {
        if (candidate === this) return true;
        if ([...queryCache.values()].includes(candidate)) return true;
        return Boolean(findDescendant(this, (node) => node === candidate));
      },
      getClientRects() { return this.isConnected ? [{}] : []; },
      getBoundingClientRect() { return { left: 0, right: 100, top: 0, bottom: 100 }; },
      setAttribute(name, value) { this[name] = String(value); },
      getAttribute(name) { return this[name] ?? null; },
      querySelector(selector) {
        const descendant = findDescendant(this, (child) => matches(child, selector));
        if (descendant) return descendant;
        if (!queryCache.has(selector)) {
          const tag = selector === 'select' ? 'select' : 'div';
          const fallback = makeElement(tag);
          if (selector.startsWith('#')) fallback.id = selector.slice(1).split(/\s/, 1)[0];
          if (selector.startsWith('.')) fallback.className = selector.slice(1);
          fallback.isConnected = this.isConnected;
          queryCache.set(selector, fallback);
        }
        return queryCache.get(selector);
      },
      querySelectorAll(selector) {
        if (selector.includes('button:not([disabled])')) {
          return ['.hwx-tc-x', '.hwx-tc-name', '.hwx-tc-save'].map((item) => this.querySelector(item));
        }
        const match = this.querySelector(selector);
        return match ? [match] : [];
      },
    };
    return element;
  }

  const bodyNode = body ? makeElement('body') : null;
  if (bodyNode) bodyNode.isConnected = true;
  const head = makeElement('head');
  head.isConnected = true;
  const documentElement = makeElement('html');
  documentElement.isConnected = true;
  const railNode = makeElement('nav', 'rail');
  railNode.isConnected = true;
  const spacer = makeElement('div', 'rail-spacer');
  railNode.appendChild(spacer);
  if (bodyNode && rail) bodyNode.appendChild(railNode);

  const documentListeners = new Map();
  document = {
    readyState: 'complete',
    body: bodyNode,
    head,
    documentElement,
    activeElement: makeElement('button'),
    fonts: { add() {}, delete() {} },
    getElementById(id) {
      return findDescendant(bodyNode, (node) => node.id === id)
        || findDescendant(head, (node) => node.id === id)
        || (documentElement.id === id ? documentElement : null);
    },
    querySelector(selector) {
      if (selector === '.rail') return bodyNode && rail ? railNode : null;
      return findDescendant(bodyNode, (node) => matches(node, selector))
        || findDescendant(head, (node) => matches(node, selector));
    },
    createElement(tagName) {
      const element = makeElement(tagName);
      element.isConnected = false;
      return element;
    },
    addEventListener(type, listener) {
      const entries = documentListeners.get(type) || [];
      entries.push(listener);
      documentListeners.set(type, entries);
    },
    removeEventListener(type, listener) {
      documentListeners.set(type, (documentListeners.get(type) || []).filter((entry) => entry !== listener));
    },
    dispatchEvent(event) {
      for (const listener of [...(documentListeners.get(event.type) || [])]) listener.call(document, event);
    },
  };

  const localStorage = {
    getItem(key) { return storage.has(key) ? storage.get(key) : null; },
    setItem(key, value) { storage.set(key, String(value)); },
    removeItem(key) { storage.delete(key); },
  };
  const settings = {};
  if (configure) {
    settings.registerConfigure = (handler) => {
      const result = typeof configureResult === 'function' ? configureResult() : configureResult;
      if (result !== null) configureHandlers.push(handler);
      return result;
    };
  }
  const handle = { id: EXTENSION_ID, settings };
  const window = {
    innerWidth: 1440,
    innerHeight: 1000,
    localStorage,
    registerHermesSkin(descriptor) {
      skinRegistrations.push(descriptor);
      return true;
    },
    _pickSkin(key) {
      events.push('apply:' + key);
      document.documentElement.dataset.skin = key === 'default' ? '' : key;
      localStorage.setItem('hermes-skin', key);
    },
    hermesExt: {
      register(id) {
        registrations.push(id);
        return id === EXTENSION_ID ? handle : null;
      },
    },
  };
  if (!register) delete window.hermesExt;

  const context = {
    window,
    document,
    localStorage,
    setTimeout(callback, delay) {
      timers.push({ callback, delay });
      return timers.length;
    },
    clearTimeout() {},
    console,
  };
  vm.runInNewContext(source, context, { filename: 'theme-creator.js' });

  return {
    context,
    window,
    document,
    body: bodyNode,
    railNode,
    focusCalls,
    timers,
    registrations,
    configureHandlers,
    skinRegistrations,
    events,
    storage,
    extension: window.HermesThemeCreatorExtension,
    evaluateAgain() { vm.runInNewContext(source, context, { filename: 'theme-creator.js' }); },
  };
}

function countById(root, id) {
  let count = 0;
  const visit = (node) => {
    if (!node) return;
    if (node.id === id) count += 1;
    for (const child of node.children || []) visit(child);
  };
  visit(root);
  return count;
}

function invokeConfigure(harness) {
  assert.equal(harness.configureHandlers.length, 1, 'one Configure handler is available');
  const opener = harness.document.createElement('button');
  opener.isConnected = true;
  if (harness.body) harness.body.appendChild(opener);
  let pending = false;
  let settled = 0;
  let restoreFocusCalls = 0;
  let result;
  const invoke = () => {
    if (pending) return false;
    pending = true;
    result = harness.configureHandlers[0]({
      opener,
      restoreFocus() { restoreFocusCalls += 1; },
    });
    assert.equal(typeof result?.then, 'function', 'Configure handler returns a Promise');
    Promise.resolve(result).then(() => {
      pending = false;
      settled += 1;
      opener.focus();
      harness.events.push('settled');
    });
    return true;
  };
  return {
    opener,
    invoke,
    get result() { return result; },
    get pending() { return pending; },
    get settled() { return settled; },
    get restoreFocusCalls() { return restoreFocusCalls; },
  };
}

async function closeWith(harness, route) {
  const panel = harness.document.getElementById(PANEL_ID);
  assert.ok(panel, 'Configure opened the Theme Creator editor');
  const preview = panel.querySelector('.hwx-tc-editor').querySelector('.hwx-tc-preview');
  preview.click();
  assert.ok(harness.events.some((event) => event === 'apply:custom-preview'), 'preview applies a temporary skin');
  if (route === 'x') panel.querySelector('.hwx-tc-x').click();
  else if (route === 'escape') harness.document.dispatchEvent({
    type: 'keydown',
    key: 'Escape',
    preventDefault() {},
    stopPropagation() {},
  });
  else panel.dispatchEvent({ type: 'click', target: panel });
}

// E0 Configure registration is the only supported Core-owned entry point.
const modern = makeHarness({ configure: true, rail: true });
assert.deepEqual(modern.registrations, [EXTENSION_ID], 'Theme Creator registers its exact manifest id once');
assert.equal(modern.configureHandlers.length, 1, 'Theme Creator registers one Configure handler');
assert.equal(countById(modern.body, RAIL_ID), 0, 'Configure-capable Core receives no permanent rail button');
assert.equal(modern.timers.length, 0, 'Configure migration does not install a retry timer');
assert.ok(modern.skinRegistrations.length >= 1, 'saved theme registration is independent of the rail');
assert.equal(typeof modern.extension?.open, 'function', 'programmatic open API remains exported');
assert.equal(typeof modern.extension?.registerAll, 'function', 'programmatic registerAll API remains exported');
assert.equal(typeof modern.extension?.themes, 'function', 'saved theme collection API remains exported');
assert.equal(JSON.stringify(modern.extension.themes()), JSON.stringify([BASE_THEME]), 'saved theme collection remains intact');

const noConfigureHook = makeHarness({ configure: false, rail: true });
assert.deepEqual(noConfigureHook.registrations, [EXTENSION_ID], 'old Core still receives the scoped identity lookup');
assert.equal(noConfigureHook.configureHandlers.length, 0, 'Core without registerConfigure receives no handler');
assert.equal(countById(noConfigureHook.body, RAIL_ID), 0, 'missing Configure capability receives no rail fallback');
assert.equal(noConfigureHook.timers.length, 0, 'missing Configure capability receives no retry timer');

const rejectedConfigureHook = makeHarness({ configure: true, rail: true, configureResult: null });
assert.deepEqual(rejectedConfigureHook.registrations, [EXTENSION_ID]);
assert.equal(rejectedConfigureHook.configureHandlers.length, 0, 'a failed Configure registration fails closed');
assert.equal(countById(rejectedConfigureHook.body, RAIL_ID), 0, 'a failed Configure registration receives no rail fallback');
assert.equal(rejectedConfigureHook.timers.length, 0, 'a failed Configure registration receives no retry timer');

const noRail = makeHarness({ configure: true, rail: false });
assert.deepEqual(noRail.registrations, [EXTENSION_ID], 'registration does not depend on a rail DOM node');
assert.equal(noRail.configureHandlers.length, 1, 'Configure registration does not depend on a rail DOM node');
assert.ok(noRail.skinRegistrations.length >= 1, 'theme registration does not depend on a rail DOM node');
assert.equal(typeof noRail.extension?.open, 'function');

const guard = makeHarness({ configure: true, rail: true });
guard.evaluateAgain();
assert.deepEqual(guard.registrations, [EXTENSION_ID], 'load guard prevents duplicate E0 registration');
assert.equal(guard.configureHandlers.length, 1, 'load guard prevents duplicate Configure registration');
assert.equal(guard.timers.length, 0, 'load guard does not create retry timers');

const keyboard = makeHarness({ configure: true, rail: true });
const keyboardCore = invokeConfigure(keyboard);
assert.equal(keyboardCore.invoke(), true, 'keyboard probe opens Configure');
const keyboardPanel = keyboard.document.getElementById(PANEL_ID);
assert.ok(keyboardPanel.innerHTML.includes('aria-modal="true"'), 'editor dialog declares modal semantics');
const nameInput = keyboardPanel.querySelector('.hwx-tc-name');
assert.equal(keyboard.document.activeElement, nameInput, 'editor takes initial focus');
const focusable = keyboardPanel.querySelectorAll('button:not([disabled]), select:not([disabled]), input:not([disabled]), [href], [tabindex]:not([tabindex="-1"])');
const firstFocusable = focusable[0];
const lastFocusable = focusable[focusable.length - 1];
lastFocusable.focus();
let tabPrevented = false;
keyboard.document.dispatchEvent({
  type: 'keydown', key: 'Tab', shiftKey: false,
  preventDefault() { tabPrevented = true; }, stopPropagation() {},
});
assert.equal(tabPrevented, true, 'forward Tab is consumed at the end of the dialog');
assert.equal(keyboard.document.activeElement, firstFocusable, 'forward Tab wraps to the first control');
firstFocusable.focus();
let shiftTabPrevented = false;
keyboard.document.dispatchEvent({
  type: 'keydown', key: 'Tab', shiftKey: true,
  preventDefault() { shiftTabPrevented = true; }, stopPropagation() {},
});
assert.equal(shiftTabPrevented, true, 'Shift+Tab is consumed at the start of the dialog');
assert.equal(keyboard.document.activeElement, lastFocusable, 'Shift+Tab wraps to the last control');
keyboardPanel.querySelector('.hwx-tc-x').click();
await keyboardCore.result;

const escapeIsolation = makeHarness({ configure: true, rail: true });
const settingsPanel = escapeIsolation.document.createElement('section');
settingsPanel.id = 'panelSettings';
settingsPanel.hidden = false;
escapeIsolation.body.appendChild(settingsPanel);
const escapeCore = invokeConfigure(escapeIsolation);
assert.equal(escapeCore.invoke(), true, 'Escape probe opens Configure');
let escapePrevented = false;
let escapeStopped = false;
escapeIsolation.document.dispatchEvent({
  type: 'keydown', key: 'Escape',
  preventDefault() { escapePrevented = true; },
  stopPropagation() { escapeStopped = true; },
});
if (!escapeStopped) settingsPanel.remove();
assert.equal(escapePrevented, true, 'Escape prevents the Core default key path');
assert.equal(escapeStopped, true, 'Escape does not propagate into Core Settings');
assert.equal(settingsPanel.isConnected, true, 'Escape leaves the Core Settings panel visible');
assert.equal(settingsPanel.hidden, false, 'Escape does not hide the Core Settings panel');
await escapeCore.result;

const configureThenProgrammatic = makeHarness({ configure: true, rail: true });
const configureThenProgrammaticCore = invokeConfigure(configureThenProgrammatic);
assert.equal(configureThenProgrammaticCore.invoke(), true, 'Configure opens before programmatic reuse');
const configureOwnedPanel = configureThenProgrammatic.document.getElementById(PANEL_ID);
configureThenProgrammatic.extension.open();
assert.equal(configureThenProgrammatic.document.getElementById(PANEL_ID), configureOwnedPanel,
  'programmatic open reuses the Configure-owned panel');
await Promise.resolve();
assert.equal(configureThenProgrammaticCore.pending, true,
  'programmatic reuse does not settle Configure while its editor remains visible');
configureOwnedPanel.querySelector('.hwx-tc-x').click();
await configureThenProgrammaticCore.result;

const programmaticThenConfigure = makeHarness({ configure: true, rail: true });
programmaticThenConfigure.extension.open();
const programmaticPanel = programmaticThenConfigure.document.getElementById(PANEL_ID);
const programmaticThenConfigureCore = invokeConfigure(programmaticThenConfigure);
assert.equal(programmaticThenConfigureCore.invoke(), true, 'Configure adopts an existing programmatic editor');
assert.equal(programmaticThenConfigure.document.getElementById(PANEL_ID), programmaticPanel,
  'Configure reuses rather than replaces the programmatic editor');
assert.equal(programmaticThenConfigureCore.pending, true, 'adopted editor owns the Configure pending lifecycle');
programmaticPanel.querySelector('.hwx-tc-x').click();
await programmaticThenConfigureCore.result;

for (const route of ['x', 'escape', 'backdrop']) {
  const harness = makeHarness({ configure: true, rail: true });
  const core = invokeConfigure(harness);
  assert.equal(core.invoke(), true, 'first Configure invocation is accepted');
  assert.equal(core.pending, true, 'Core pending state starts before the handler settles');
  assert.equal(core.invoke(), false, 'a second Configure invocation is suppressed by Core state');
  await closeWith(harness, route);
  assert.equal(core.pending, true, 'Configure remains pending until the editor closes');
  await core.result;
  await Promise.resolve();
  assert.equal(core.settled, 1, `${route} settles Configure exactly once`);
  assert.equal(core.restoreFocusCalls, 0, 'extension does not call Core-owned restoreFocus');
  assert.equal(countById(harness.body, PANEL_ID), 0, `${route} removes the Theme Creator editor`);
  const rollback = harness.events.indexOf('apply:default');
  const settled = harness.events.indexOf('settled');
  assert.ok(rollback >= 0 && rollback < settled, `${route} rolls back preview before Promise settlement`);
  assert.equal(core.invoke(), true, `${route} leaves Configure reusable`);
  assert.ok(harness.document.getElementById(PANEL_ID), `${route} can reopen the editor`);
  harness.document.getElementById(PANEL_ID).querySelector('.hwx-tc-x').click();
  await core.result;
  await Promise.resolve();
  assert.equal(core.settled, 2, `${route} second close settles exactly once`);
}

const missingBody = makeHarness({ configure: true, body: false, rail: false });
const missingBodyCore = invokeConfigure(missingBody);
assert.equal(missingBodyCore.invoke(), true, 'Configure invocation with no body is accepted for fail-closed handling');
await missingBodyCore.result;
await Promise.resolve();
assert.equal(missingBodyCore.settled, 1, 'missing body settles Configure immediately');
assert.equal(missingBodyCore.restoreFocusCalls, 0, 'missing body does not invoke extension focus restoration');

const legacy = makeHarness({ configure: false, register: false, rail: false });
assert.deepEqual(legacy.registrations, [], 'legacy Core receives no attempted capability registration');
assert.equal(countById(legacy.body, RAIL_ID), 0, 'legacy Core receives no rail fallback');
assert.equal(legacy.timers.length, 0, 'legacy Core receives no retry timer');
assert.equal(typeof legacy.extension?.open, 'function', 'legacy Core retains programmatic editor access');
legacy.extension.open();
assert.equal(countById(legacy.body, PANEL_ID), 1, 'programmatic open remains available on legacy Core');
legacy.document.getElementById(PANEL_ID).querySelector('.hwx-tc-x').click();
assert.equal(countById(legacy.body, PANEL_ID), 0, 'programmatic editor can close on legacy Core');
assert.equal(legacy.extension.version, '0.3.6');

console.log('theme-creator Configure-entry check passed');
