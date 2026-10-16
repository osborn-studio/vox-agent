// Minimal DOM stand-in for module tests: only what src/context/*.js uses.
export class FakeElement {
  constructor(document, tagName) {
    this.ownerDocument = document;
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.parent = null;
    this.attributes = new Map();
    this.dataset = {};
    this.listeners = new Map();
    this.className = "";
    this.textContent = "";
    this.inert = false;
    this.type = "";
  }
  append(...nodes) {
    for (const node of nodes) {
      node.parent = this;
      this.children.push(node);
    }
  }
  insertBefore(node, reference) {
    node.parent?.removeChild(node);
    node.parent = this;
    const index = reference ? this.children.indexOf(reference) : -1;
    if (index < 0) this.children.push(node);
    else this.children.splice(index, 0, node);
  }
  removeChild(node) {
    this.children = this.children.filter((child) => child !== node);
    node.parent = null;
  }
  remove() {
    this.parent?.removeChild(this);
  }
  replaceChildren(...nodes) {
    this.children = [];
    this.append(...nodes);
  }
  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }
  getAttribute(name) {
    return this.attributes.has(name) ? this.attributes.get(name) : null;
  }
  hasAttribute(name) {
    return this.attributes.has(name);
  }
  removeAttribute(name) {
    this.attributes.delete(name);
  }
  toggleAttribute(name, force) {
    if (force) this.attributes.set(name, "");
    else this.attributes.delete(name);
  }
  addEventListener(type, handler) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), handler]);
  }
  dispatch(type, event = {}) {
    for (const handler of this.listeners.get(type) ?? []) handler(event);
  }
  click() {
    this.dispatch("click", {});
  }
  focus() {
    this.ownerDocument.activeElement = this;
  }
  contains(node) {
    for (let current = node; current; current = current.parent) {
      if (current === this) return true;
    }
    return false;
  }
  /** Only the universal selector, as `scrub()` uses it. */
  querySelectorAll(selector) {
    if (selector !== "*") throw new Error(`unsupported selector ${selector}`);
    return this.all().slice(1);
  }
  /** Depth-first list of this element and its descendants. */
  all() {
    return [this, ...this.children.flatMap((child) => child.all())];
  }
  get text() {
    return this.all().map((node) => node.textContent).join(" ");
  }
}

export class FakeDocument {
  constructor() {
    this.activeElement = null;
  }
  createElement(tagName) {
    return new FakeElement(this, tagName);
  }
}

export function memoryStorage(initial = {}) {
  const values = new Map(Object.entries(initial));
  return {
    values,
    get: (key, fallback = "") => (values.has(key) ? values.get(key) : fallback),
    set: (key, value) => values.set(key, value),
  };
}
