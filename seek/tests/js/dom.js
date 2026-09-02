'use strict';
//
// A DOM small enough to read in one sitting and just large enough to run the
// Download Templates picker's inline script unmodified.
//
// Deliberately not jsdom: this repo ships no node dependencies at all, and the
// point of the harness is that it needs none. Everything here exists because
// templatesList.html's script actually uses it -- if the script grows a new
// call, this file is where it goes, and an unsupported CSS selector or a
// non-empty innerHTML assignment throws rather than quietly doing nothing.
//

function Text(data) {
  this.nodeType = 3;
  this.data = String(data);
}

Object.defineProperty(Text.prototype, 'textContent', {
  get: function () { return this.data; },
  set: function (value) { this.data = String(value); },
});

function Element(tagName) {
  this.nodeType = 1;
  this.tagName = String(tagName).toUpperCase();
  this.childNodes = [];
  this.attributes = {};
  this.listeners = {};
  this.dataset = {};
  this.parentNode = null;
  this.hidden = false;
  this.disabled = false;
  this.checked = false;
  this.title = '';
  this.value = '';
  this._classes = [];

  var self = this;
  this.classList = {
    add: function () {
      Array.prototype.forEach.call(arguments, function (name) {
        if (self._classes.indexOf(name) === -1) { self._classes.push(name); }
      });
    },
    remove: function () {
      Array.prototype.forEach.call(arguments, function (name) {
        var at = self._classes.indexOf(name);
        if (at !== -1) { self._classes.splice(at, 1); }
      });
    },
    contains: function (name) { return self._classes.indexOf(name) !== -1; },
  };
}

Object.defineProperty(Element.prototype, 'className', {
  get: function () { return this._classes.join(' '); },
  set: function (value) {
    this._classes = String(value).split(/\s+/).filter(Boolean);
  },
});

Object.defineProperty(Element.prototype, 'textContent', {
  get: function () {
    return this.childNodes.map(function (node) { return node.textContent; }).join('');
  },
  set: function (value) {
    this.childNodes = [];
    if (value !== '' && value !== null && value !== undefined) {
      this.appendChild(new Text(value));
    }
  },
});

// The template has had three XSS vectors fixed and the standing rule is that
// the only acceptable innerHTML is assigning ''. Enforce that here: a future
// edit that builds markup from a string fails loudly in the harness instead of
// shipping.
Object.defineProperty(Element.prototype, 'innerHTML', {
  get: function () { return this.textContent; },
  set: function (value) {
    if (value !== '') {
      throw new Error("innerHTML may only be assigned '' (build nodes instead)");
    }
    this.childNodes = [];
  },
});

Object.defineProperty(Element.prototype, 'children', {
  get: function () {
    return this.childNodes.filter(function (node) { return node.nodeType === 1; });
  },
});

Element.prototype.appendChild = function (node) {
  node.parentNode = this;
  this.childNodes.push(node);
  return node;
};

Element.prototype.setAttribute = function (name, value) {
  this.attributes[name] = String(value);
  if (name === 'class') { this.className = value; }
};

Element.prototype.getAttribute = function (name) {
  if (name === 'class') { return this.className; }
  return Object.prototype.hasOwnProperty.call(this.attributes, name)
    ? this.attributes[name]
    : null;
};

Element.prototype.addEventListener = function (type, handler) {
  (this.listeners[type] = this.listeners[type] || []).push(handler);
};

Element.prototype.dispatchEvent = function (event) {
  var detail = event || {};
  if (typeof detail === 'string') { detail = { type: detail }; }
  if (!detail.target) { detail.target = this; }
  if (!detail.preventDefault) { detail.preventDefault = function () {}; }
  (this.listeners[detail.type] || []).slice().forEach(function (handler) {
    handler.call(this, detail);
  }, this);
  return true;
};

Element.prototype.focus = function () { /* nothing observable to model */ };

function matches(el, selector) {
  if (selector.charAt(0) === '.') {
    return el.classList.contains(selector.slice(1));
  }
  var attr = /^([a-zA-Z]+)\[([a-zA-Z-]+)="([^"]*)"\]$/.exec(selector);
  if (attr) {
    return el.tagName === attr[1].toUpperCase()
      && el.getAttribute(attr[2]) === attr[3];
  }
  throw new Error('selector not supported by the DOM stub: ' + selector);
}

function queryAll(root, selector) {
  var found = [];
  (function walk(node) {
    node.children.forEach(function (child) {
      if (matches(child, selector)) { found.push(child); }
      walk(child);
    });
  })(root);
  // A real NodeList is not an Array, but it has forEach and survives
  // Array.prototype.slice.call -- the only two things the script asks of it.
  return found;
}

Element.prototype.querySelectorAll = function (selector) {
  return queryAll(this, selector);
};

function createDocument() {
  var root = new Element('body');
  var byId = {};

  var doc = {
    body: root,
    createElement: function (tag) { return new Element(tag); },
    createTextNode: function (text) { return new Text(text); },
    getElementById: function (id) {
      return Object.prototype.hasOwnProperty.call(byId, id) ? byId[id] : null;
    },
    querySelectorAll: function (selector) { return queryAll(root, selector); },
    // Harness-only: the real page gets its ids from the rendered HTML.
    register: function (id, el) {
      el.setAttribute('id', id);
      byId[id] = el;
      return el;
    },
  };
  return doc;
}

module.exports = { Element: Element, Text: Text, createDocument: createDocument };
