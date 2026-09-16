# ruff: noqa: E501 - the JS/HTML sources below are text the composer ships, not code to wrap
"""``ComposedAppGenerator``: a multi-file, stdlib-only Node application from a
:class:`ProjectPlan` (B40 req 425-434). Every file is composed from fixed source with the
plan's own values spliced as JSON or escaped text - the same splice discipline
``DeterministicAppGenerator`` keeps for the three templates (module docstring of
``app.appfactory.generator``): a record kind or a field name reaches code only as a slug
that matched an identifier shape, and reaches HTML only escaped.

What is generated, and what proves it:

* ``schema.js`` (req 427) - the record kinds, fields and validation, UMD so the browser and
  the tests run the SAME functions;
* ``store.js`` (req 427) - a schema-checked JSON-file store with atomic writes (an
  in-memory mode for tests);
* ``auth.js`` (req 430, when asked) - scrypt password hashing, a session cookie, four
  routes, a guard every record route sits behind;
* ``server.js`` (req 428) - the HTTP server: static files, ``/api/schema``,
  ``/api/<kind>`` list/create/update/delete, the optional ``/api/custom`` surface for a
  model-authored ``custom.js``;
* ``public/`` (req 429) - the page, its stylesheet and the DOM glue driven by the schema;
* ``tests/`` (req 431-434) - unit tests over the schema, integration tests over a server
  on an ephemeral port, the runner the manifest names, and the browser oracle the device
  lab replays;
* ``README.md`` and ``manifest.json``.

The runner prints the SAME ``ok - `` / ``not ok - `` lines and the ``passed/total passed``
summary the built-in templates print, because that is what the device parses.
"""

from __future__ import annotations

import html
import json
import re
from typing import Any, Final

from app.appfactory.generator import AppGeneratorError, ProjectFile, ProjectFiles
from app.appfactory.planner import ArchitecturePlan, ProjectPlan
from app.appfactory.requirements import EntitySpec

TEMPLATE_COMPOSED: Final = "composed"
_SLUG_RE: Final = re.compile(r"[^a-z0-9]+")
_IDENT_RE: Final = re.compile(r"^[a-z][a-z0-9-]{0,39}$")
_TR_FOLD: Final = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosucgiosu")


def slugify(text: str) -> str:
    folded = (text or "").translate(_TR_FOLD).lower()
    slug = _SLUG_RE.sub("-", folded).strip("-")
    return slug[:40]


def _entity_json(entities: list[EntitySpec]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entity in entities:
        slug = slugify(entity.name)
        if not _IDENT_RE.match(slug) or slug in seen:
            raise AppGeneratorError(f"record kind {entity.name!r} has no usable identifier")
        seen.add(slug)
        fields: list[dict[str, Any]] = []
        field_seen: set[str] = set()
        for f in entity.fields:
            fslug = slugify(f.name)
            if not _IDENT_RE.match(fslug) or fslug in field_seen:
                raise AppGeneratorError(
                    f"field {f.name!r} of {entity.name!r} has no usable identifier"
                )
            field_seen.add(fslug)
            fields.append({"name": fslug, "label": f.name[:60], "type": f.type})
        if not fields:
            raise AppGeneratorError(f"record kind {entity.name!r} has no fields")
        out.append({"name": slug, "label": entity.name[:60], "fields": fields})
    if not out:
        raise AppGeneratorError("a composed application needs at least one record kind")
    return out


# ------------------------------------------------------------------ the sources

SCHEMA_JS: Final = r"""// PagentOS App Factory (composed) - the record kinds, their fields and the validation.
// UMD: loaded as a plain <script> in the browser (window.AppSchema) and via require()
// from the store, the server and the tests - the SAME functions everywhere.
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.AppSchema = factory();
  }
})(typeof window !== "undefined" ? window : this, function () {
  "use strict";

  var ENTITIES = __ENTITIES__;
  var DATE_RE = /^[0-9]{4}-[0-9]{2}-[0-9]{2}$/;

  function byName(name) {
    for (var i = 0; i < ENTITIES.length; i++) {
      if (ENTITIES[i].name === name) {
        return ENTITIES[i];
      }
    }
    return null;
  }

  function coerce(field, value) {
    if (field.type === "number") {
      if (value === "" || value === null || value === undefined) {
        return { ok: false, error: field.label + " sayı olmalı" };
      }
      var n = Number(value);
      if (!isFinite(n)) {
        return { ok: false, error: field.label + " sayı olmalı" };
      }
      return { ok: true, value: n };
    }
    if (field.type === "boolean") {
      if (value === true || value === "true" || value === "on" || value === 1) {
        return { ok: true, value: true };
      }
      return { ok: true, value: false };
    }
    if (field.type === "date") {
      var text = String(value === null || value === undefined ? "" : value).trim();
      if (!DATE_RE.test(text)) {
        return { ok: false, error: field.label + " YYYY-AA-GG biçiminde bir tarih olmalı" };
      }
      return { ok: true, value: text };
    }
    var s = String(value === null || value === undefined ? "" : value).trim();
    if (s.length > 500) {
      return { ok: false, error: field.label + " en çok 500 karakter olabilir" };
    }
    return { ok: true, value: s };
  }

  function validate(kind, record) {
    var entity = byName(kind);
    if (!entity) {
      return { ok: false, errors: ["bilinmeyen kayıt türü: " + kind], value: null };
    }
    var errors = [];
    var value = {};
    var source = record && typeof record === "object" ? record : {};
    for (var i = 0; i < entity.fields.length; i++) {
      var field = entity.fields[i];
      var raw = source[field.name];
      var result = coerce(field, raw);
      if (!result.ok) {
        errors.push(result.error);
        continue;
      }
      if (i === 0 && field.type === "text" && result.value === "") {
        errors.push(field.label + " boş olamaz");
        continue;
      }
      value[field.name] = result.value;
    }
    return { ok: errors.length === 0, errors: errors, value: errors.length === 0 ? value : null };
  }

  return { ENTITIES: ENTITIES, byName: byName, validate: validate, coerce: coerce };
});
"""

STORE_JS: Final = r"""// PagentOS App Factory (composed) - the JSON-file store. One file under the project,
// written atomically (temp file + rename); an in-memory mode when no file is named (the
// tests). Every write goes through schema.js first: nothing the schema refuses is stored.
"use strict";

var fs = require("fs");
var path = require("path");
var Schema = require("./schema.js");

var DATA_FILE = path.join(__dirname, "data", "records.json");
var VERSION = 1;

function emptyData() {
  var records = {};
  for (var i = 0; i < Schema.ENTITIES.length; i++) {
    records[Schema.ENTITIES[i].name] = [];
  }
  return { version: VERSION, records: records };
}

function load(file) {
  if (!file || !fs.existsSync(file)) {
    return emptyData();
  }
  try {
    var parsed = JSON.parse(fs.readFileSync(file, "utf8"));
    var data = emptyData();
    if (parsed && parsed.records && typeof parsed.records === "object") {
      for (var kind in data.records) {
        if (Array.isArray(parsed.records[kind])) {
          data.records[kind] = parsed.records[kind];
        }
      }
    }
    return data;
  } catch (err) {
    return emptyData();
  }
}

function save(file, data) {
  if (!file) {
    return;
  }
  var dir = path.dirname(file);
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }
  var tmp = file + ".tmp";
  fs.writeFileSync(tmp, JSON.stringify(data, null, 2), "utf8");
  fs.renameSync(tmp, file);
}

function nextId(rows) {
  var max = 0;
  for (var i = 0; i < rows.length; i++) {
    if (rows[i].id > max) {
      max = rows[i].id;
    }
  }
  return max + 1;
}

function createStore(options) {
  var file = options && Object.prototype.hasOwnProperty.call(options, "file") ? options.file : DATA_FILE;
  var data = load(file);

  function rows(kind) {
    if (!Schema.byName(kind)) {
      throw new Error("unknown kind: " + kind);
    }
    return data.records[kind];
  }

  function list(kind) {
    return rows(kind).slice();
  }

  function create(kind, record) {
    var checked = Schema.validate(kind, record);
    if (!checked.ok) {
      return { ok: false, errors: checked.errors };
    }
    var all = rows(kind);
    var row = checked.value;
    row.id = nextId(all);
    row.createdAt = new Date().toISOString();
    all.push(row);
    save(file, data);
    return { ok: true, row: row };
  }

  function update(kind, id, patch) {
    var all = rows(kind);
    for (var i = 0; i < all.length; i++) {
      if (all[i].id === id) {
        var merged = {};
        var key;
        for (key in all[i]) {
          merged[key] = all[i][key];
        }
        for (key in patch || {}) {
          merged[key] = patch[key];
        }
        var checked = Schema.validate(kind, merged);
        if (!checked.ok) {
          return { ok: false, errors: checked.errors };
        }
        var row = checked.value;
        row.id = id;
        row.createdAt = all[i].createdAt;
        row.updatedAt = new Date().toISOString();
        all[i] = row;
        save(file, data);
        return { ok: true, row: row };
      }
    }
    return { ok: false, errors: ["kayıt bulunamadı"], notFound: true };
  }

  function remove(kind, id) {
    var all = rows(kind);
    for (var i = 0; i < all.length; i++) {
      if (all[i].id === id) {
        all.splice(i, 1);
        save(file, data);
        return { ok: true };
      }
    }
    return { ok: false, errors: ["kayıt bulunamadı"], notFound: true };
  }

  return { list: list, create: create, update: update, remove: remove, file: file };
}

module.exports = { createStore: createStore, DATA_FILE: DATA_FILE, VERSION: VERSION };
"""

AUTH_JS: Final = r"""// PagentOS App Factory (composed) - the single owner's login. One password, hashed with
// scrypt (node:crypto, a random salt), kept in its own file; sessions live in memory and
// travel as an HttpOnly, SameSite=Strict cookie. Nothing here is ever logged.
"use strict";

var crypto = require("crypto");
var fs = require("fs");
var path = require("path");

var AUTH_FILE = path.join(__dirname, "data", "auth.json");
var COOKIE = "sid";
var MIN_PASSWORD_CHARS = 8;

function hashPassword(plain, salt) {
  return crypto.scryptSync(String(plain), salt, 32).toString("hex");
}

function createAuth(options) {
  var file = options && Object.prototype.hasOwnProperty.call(options, "file") ? options.file : AUTH_FILE;
  var record = null;
  var sessions = {};
  if (file && fs.existsSync(file)) {
    try {
      record = JSON.parse(fs.readFileSync(file, "utf8"));
    } catch (err) {
      record = null;
    }
  }

  function persist() {
    if (!file) {
      return;
    }
    var dir = path.dirname(file);
    if (!fs.existsSync(dir)) {
      fs.mkdirSync(dir, { recursive: true });
    }
    var tmp = file + ".tmp";
    fs.writeFileSync(tmp, JSON.stringify(record), "utf8");
    fs.renameSync(tmp, file);
  }

  function isConfigured() {
    return !!(record && record.salt && record.hash);
  }

  function setup(plain) {
    if (isConfigured()) {
      return { ok: false, error: "parola zaten belirlenmiş" };
    }
    if (typeof plain !== "string" || plain.length < MIN_PASSWORD_CHARS) {
      return { ok: false, error: "parola en az " + MIN_PASSWORD_CHARS + " karakter olmalı" };
    }
    var salt = crypto.randomBytes(16).toString("hex");
    record = { salt: salt, hash: hashPassword(plain, salt), createdAt: new Date().toISOString() };
    persist();
    return { ok: true };
  }

  function login(plain) {
    if (!isConfigured()) {
      return { ok: false, error: "önce parola belirlenmeli" };
    }
    var candidate = Buffer.from(hashPassword(plain || "", record.salt), "hex");
    var expected = Buffer.from(record.hash, "hex");
    if (candidate.length !== expected.length || !crypto.timingSafeEqual(candidate, expected)) {
      return { ok: false, error: "parola yanlış" };
    }
    var sid = crypto.randomBytes(24).toString("hex");
    sessions[sid] = { createdAt: Date.now() };
    return { ok: true, sid: sid };
  }

  function logout(sid) {
    delete sessions[sid];
    return { ok: true };
  }

  function sessionOf(req) {
    var header = req.headers.cookie || "";
    var parts = header.split(";");
    for (var i = 0; i < parts.length; i++) {
      var pair = parts[i].trim().split("=");
      if (pair[0] === COOKIE && sessions[pair[1]]) {
        return pair[1];
      }
    }
    return null;
  }

  function cookieFor(sid) {
    return COOKIE + "=" + sid + "; HttpOnly; SameSite=Strict; Path=/";
  }

  function clearedCookie() {
    return COOKIE + "=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0";
  }

  return {
    isConfigured: isConfigured,
    setup: setup,
    login: login,
    logout: logout,
    sessionOf: sessionOf,
    cookieFor: cookieFor,
    clearedCookie: clearedCookie
  };
}

module.exports = { createAuth: createAuth, AUTH_FILE: AUTH_FILE, MIN_PASSWORD_CHARS: MIN_PASSWORD_CHARS };
"""

SERVER_JS: Final = r"""// PagentOS App Factory (composed) - the HTTP server. Static files from public/, the schema
// at /api/schema, one REST surface per record kind at /api/<kind>, __AUTH_COMMENT__
// Listens on the manifest's port on the loopback only; exports createServer() so the
// integration tests run it on an ephemeral port with an in-memory store.
"use strict";

var http = require("http");
var fs = require("fs");
var path = require("path");
var Schema = require("./schema.js");
var Store = require("./store.js");
__AUTH_REQUIRE__
var PORT = __PORT__;
var PUBLIC_DIR = path.join(__dirname, "public");
var MAX_BODY_BYTES = 65536;
var CONTENT_TYPES = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "application/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8"
};

function json(res, status, body, headers) {
  var text = JSON.stringify(body);
  var all = { "Content-Type": "application/json; charset=utf-8", "Content-Length": Buffer.byteLength(text) };
  for (var key in headers || {}) {
    all[key] = headers[key];
  }
  res.writeHead(status, all);
  res.end(text);
}

function readBody(req, callback) {
  var chunks = [];
  var size = 0;
  var failed = false;
  req.on("data", function (chunk) {
    size += chunk.length;
    if (size > MAX_BODY_BYTES) {
      failed = true;
      req.destroy();
      return;
    }
    chunks.push(chunk);
  });
  req.on("end", function () {
    if (failed) {
      callback(new Error("body too large"), null);
      return;
    }
    var text = Buffer.concat(chunks).toString("utf8");
    if (!text) {
      callback(null, {});
      return;
    }
    try {
      callback(null, JSON.parse(text));
    } catch (err) {
      callback(err, null);
    }
  });
}

function serveStatic(pathname, res) {
  var rel = pathname === "/" ? "index.html" : pathname.slice(1);
  var target = path.normalize(path.join(PUBLIC_DIR, rel));
  if (target.indexOf(PUBLIC_DIR) !== 0 || rel.indexOf("..") !== -1) {
    json(res, 404, { error: "not_found" });
    return;
  }
  fs.readFile(target, function (err, data) {
    if (err) {
      json(res, 404, { error: "not_found" });
      return;
    }
    var type = CONTENT_TYPES[path.extname(target)] || "application/octet-stream";
    res.writeHead(200, { "Content-Type": type, "Content-Length": data.length });
    res.end(data);
  });
}

function loadCustom() {
  var file = path.join(__dirname, "custom.js");
  if (!fs.existsSync(file)) {
    return null;
  }
  return require(file);
}

function createServer(options) {
  var store = Store.createStore({ file: options && Object.prototype.hasOwnProperty.call(options, "dataFile") ? options.dataFile : Store.DATA_FILE });
__AUTH_CREATE__
  var custom = loadCustom();

  var server = http.createServer(function (req, res) {
    var pathname = req.url.split("?")[0];
    if (pathname === "/api/schema" && req.method === "GET") {
      json(res, 200, { entities: Schema.ENTITIES, auth: __AUTH_FLAG__ });
      return;
    }
__AUTH_ROUTES__
    if (pathname === "/api/custom" && req.method === "GET") {
      var names = [];
      if (custom) {
        for (var name in custom) {
          if (typeof custom[name] === "function") {
            names.push(name);
          }
        }
      }
      json(res, 200, { functions: names });
      return;
    }
    var customMatch = /^\/api\/custom\/([a-zA-Z_][a-zA-Z0-9_]*)$/.exec(pathname);
    if (customMatch && req.method === "POST") {
      if (!custom || typeof custom[customMatch[1]] !== "function") {
        json(res, 404, { error: "not_found" });
        return;
      }
      readBody(req, function (err, body) {
        if (err) {
          json(res, 400, { error: "invalid_json" });
          return;
        }
        try {
          var args = Array.isArray(body && body.args) ? body.args : [];
          json(res, 200, { result: custom[customMatch[1]].apply(null, args) });
        } catch (callErr) {
          json(res, 400, { error: "custom_failed" });
        }
      });
      return;
    }
    var match = /^\/api\/([a-z][a-z0-9-]*)(?:\/([0-9]+))?$/.exec(pathname);
    if (match) {
      var kind = match[1];
      var id = match[2] ? parseInt(match[2], 10) : null;
      if (!Schema.byName(kind)) {
        json(res, 404, { error: "unknown_kind" });
        return;
      }
__AUTH_GUARD__
      if (req.method === "GET" && id === null) {
        json(res, 200, { rows: store.list(kind) });
        return;
      }
      if (req.method === "POST" && id === null) {
        readBody(req, function (err, body) {
          if (err) {
            json(res, 400, { error: "invalid_json" });
            return;
          }
          var created = store.create(kind, body);
          if (!created.ok) {
            json(res, 400, { error: "invalid_record", errors: created.errors });
            return;
          }
          json(res, 201, { row: created.row });
        });
        return;
      }
      if (req.method === "PUT" && id !== null) {
        readBody(req, function (err, body) {
          if (err) {
            json(res, 400, { error: "invalid_json" });
            return;
          }
          var updated = store.update(kind, id, body);
          if (!updated.ok) {
            json(res, updated.notFound ? 404 : 400, { error: updated.notFound ? "not_found" : "invalid_record", errors: updated.errors });
            return;
          }
          json(res, 200, { row: updated.row });
        });
        return;
      }
      if (req.method === "DELETE" && id !== null) {
        var removed = store.remove(kind, id);
        json(res, removed.ok ? 200 : 404, removed.ok ? { removed: true } : { error: "not_found" });
        return;
      }
      json(res, 405, { error: "method_not_allowed" });
      return;
    }
    if (pathname.indexOf("/api/") === 0) {
      json(res, 404, { error: "not_found" });
      return;
    }
    if (req.method !== "GET") {
      json(res, 405, { error: "method_not_allowed" });
      return;
    }
    serveStatic(pathname, res);
  });
  server.store = store;
  return server;
}

if (require.main === module) {
  createServer({}).listen(PORT, "127.0.0.1", function () {
    console.log("__APP_NAME_JS__ listening on http://127.0.0.1:" + PORT + "/");
  });
}

module.exports = { createServer: createServer, PORT: PORT };
"""

AUTH_REQUIRE: Final = 'var Auth = require("./auth.js");\n'
AUTH_CREATE: Final = '  var auth = Auth.createAuth({ file: options && Object.prototype.hasOwnProperty.call(options, "authFile") ? options.authFile : Auth.AUTH_FILE });\n'
AUTH_ROUTES: Final = r"""    if (pathname === "/api/me" && req.method === "GET") {
      json(res, 200, { configured: auth.isConfigured(), loggedIn: auth.sessionOf(req) !== null });
      return;
    }
    if (pathname === "/api/setup" && req.method === "POST") {
      readBody(req, function (err, body) {
        if (err) {
          json(res, 400, { error: "invalid_json" });
          return;
        }
        var done = auth.setup(body && body.parola);
        if (!done.ok) {
          json(res, 400, { error: "setup_refused", message: done.error });
          return;
        }
        var opened = auth.login(body.parola);
        json(res, 201, { configured: true, loggedIn: true }, { "Set-Cookie": auth.cookieFor(opened.sid) });
      });
      return;
    }
    if (pathname === "/api/login" && req.method === "POST") {
      readBody(req, function (err, body) {
        if (err) {
          json(res, 400, { error: "invalid_json" });
          return;
        }
        var opened = auth.login(body && body.parola);
        if (!opened.ok) {
          json(res, 401, { error: "login_failed", message: opened.error });
          return;
        }
        json(res, 200, { loggedIn: true }, { "Set-Cookie": auth.cookieFor(opened.sid) });
      });
      return;
    }
    if (pathname === "/api/logout" && req.method === "POST") {
      var sid = auth.sessionOf(req);
      if (sid) {
        auth.logout(sid);
      }
      json(res, 200, { loggedIn: false }, { "Set-Cookie": auth.clearedCookie() });
      return;
    }
"""
AUTH_GUARD: Final = r"""      if (auth.sessionOf(req) === null) {
        json(res, 401, { error: "login_required" });
        return;
      }
"""

INDEX_HTML: Final = """<!doctype html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>__TITLE__</title>
  <link rel="stylesheet" href="app.css">
</head>
<body>
  <main>
    <h1 id="app-title">__TITLE__</h1>
__LOGIN_SECTION__
    <div id="kinds">
__SECTIONS__
    </div>
    <p id="status" class="status" hidden></p>
  </main>
  <script src="schema.js"></script>
  <script src="app.js"></script>
</body>
</html>
"""

LOGIN_SECTION: Final = """    <section id="login" hidden>
      <h2>Giriş</h2>
      <form id="login-form">
        <input id="login-password" type="password" placeholder="Parola" minlength="8" required autocomplete="current-password">
        <button id="login-button" type="submit">Giriş yap</button>
      </form>
      <p id="login-message" class="status"></p>
    </section>
"""

SECTION_HTML: Final = """      <section id="kind-__KIND__" class="kind" data-kind="__KIND__">
        <h2>__LABEL__</h2>
        <form id="form-__KIND__" class="record-form" data-kind="__KIND__">
__INPUTS__
          <button id="add-__KIND__" type="submit">Ekle</button>
        </form>
        <ul id="list-__KIND__" class="record-list"></ul>
        <p id="empty-__KIND__" class="empty">Henüz kayıt yok.</p>
      </section>
"""

APP_CSS: Final = """:root { color-scheme: light dark; font-family: system-ui, "Segoe UI", sans-serif; }
body { margin: 0; padding: 1.5rem; max-width: 56rem; margin-inline: auto; }
h1 { font-size: 1.6rem; margin: 0 0 1rem; }
h2 { font-size: 1.1rem; margin: 1.5rem 0 .5rem; }
section.kind { border-top: 1px solid #8884; padding-top: .5rem; }
form { display: flex; flex-wrap: wrap; gap: .5rem; align-items: center; }
input, button { font: inherit; padding: .4rem .6rem; }
input { flex: 1 1 10rem; }
ul.record-list { list-style: none; padding: 0; margin: .75rem 0; }
ul.record-list li { display: flex; justify-content: space-between; gap: .5rem; padding: .35rem 0; border-bottom: 1px solid #8883; }
ul.record-list li button { font-size: .85rem; }
.empty, .status { color: #777; }
"""

APP_JS: Final = r"""// PagentOS App Factory (composed) - the DOM glue. The forms are drawn from the schema the
// server reports, the lists from /api/<kind>; nothing is stored in the page itself.
(function () {
  "use strict";

  var AUTH = __AUTH_FLAG__;

  function api(method, url, body) {
    var options = { method: method, headers: {}, credentials: "same-origin" };
    if (body !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    return fetch(url, options).then(function (res) {
      return res.json().then(function (data) {
        return { status: res.status, data: data };
      });
    });
  }

  function say(text) {
    var status = document.getElementById("status");
    status.textContent = text;
    status.hidden = !text;
  }

  function displayValue(field, value) {
    if (field.type === "boolean") {
      return value ? "evet" : "hayır";
    }
    return String(value);
  }

  function render(kind, rows) {
    var list = document.getElementById("list-" + kind);
    var empty = document.getElementById("empty-" + kind);
    var entity = AppSchema.byName(kind);
    list.innerHTML = "";
    empty.hidden = rows.length !== 0;
    rows.forEach(function (row) {
      var li = document.createElement("li");
      li.className = "record-item";
      li.dataset.recordId = String(row.id);
      var text = document.createElement("span");
      var parts = [];
      for (var i = 0; i < entity.fields.length; i++) {
        var field = entity.fields[i];
        parts.push(field.label + ": " + displayValue(field, row[field.name]));
      }
      text.textContent = parts.join(" · ");
      var remove = document.createElement("button");
      remove.type = "button";
      remove.className = "remove";
      remove.textContent = "Sil";
      remove.addEventListener("click", function () {
        api("DELETE", "/api/" + kind + "/" + row.id).then(function () {
          load(kind);
        });
      });
      li.appendChild(text);
      li.appendChild(remove);
      list.appendChild(li);
    });
  }

  function load(kind) {
    return api("GET", "/api/" + kind).then(function (answer) {
      if (answer.status === 401) {
        showLogin(true);
        return;
      }
      render(kind, answer.data.rows || []);
    });
  }

  function readForm(form, entity) {
    var record = {};
    for (var i = 0; i < entity.fields.length; i++) {
      var field = entity.fields[i];
      var input = form.querySelector("[name=" + field.name + "]");
      record[field.name] = field.type === "boolean" ? input.checked : input.value;
    }
    return record;
  }

  function wireForm(entity) {
    var form = document.getElementById("form-" + entity.name);
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      api("POST", "/api/" + entity.name, readForm(form, entity)).then(function (answer) {
        if (answer.status === 201) {
          form.reset();
          say("");
          load(entity.name);
        } else {
          say((answer.data.errors || [answer.data.error || "kaydedilemedi"]).join(", "));
        }
      });
    });
  }

  function showLogin(visible) {
    var section = document.getElementById("login");
    if (section) {
      section.hidden = !visible;
    }
    var kinds = document.getElementById("kinds");
    kinds.hidden = visible;
  }

  function wireLogin() {
    var form = document.getElementById("login-form");
    if (!form) {
      return;
    }
    var message = document.getElementById("login-message");
    api("GET", "/api/me").then(function (answer) {
      var button = document.getElementById("login-button");
      if (!answer.data.configured) {
        button.textContent = "Parola belirle";
        message.textContent = "İlk kullanım: bir parola belirleyin (en az 8 karakter).";
      }
      showLogin(!answer.data.loggedIn);
      if (answer.data.loggedIn) {
        loadAll();
      }
    });
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var entered = document.getElementById("login-password").value;
      api("GET", "/api/me").then(function (me) {
        var route = me.data.configured ? "/api/login" : "/api/setup";
        return api("POST", route, { parola: entered });
      }).then(function (answer) {
        if (answer.status === 200 || answer.status === 201) {
          message.textContent = "";
          showLogin(false);
          loadAll();
        } else {
          message.textContent = answer.data.message || "giriş başarısız";
        }
      });
    });
  }

  function loadAll() {
    AppSchema.ENTITIES.forEach(function (entity) {
      load(entity.name);
    });
  }

  AppSchema.ENTITIES.forEach(wireForm);
  if (AUTH) {
    wireLogin();
  } else {
    loadAll();
  }
})();
"""

UNIT_TESTS_JS: Final = r"""// PagentOS App Factory (composed) - unit tests over schema.js (the SAME functions the
// browser and the server run). Loaded by tests/run.js; each test reports its own line.
"use strict";

var assert = require("assert");
var Schema = require("../schema.js");

module.exports = function (check) {
  Schema.ENTITIES.forEach(function (entity) {
    var first = entity.fields[0];

    check(entity.name + ": a complete record validates", function () {
      var record = {};
      entity.fields.forEach(function (field) {
        record[field.name] = sampleFor(field);
      });
      var result = Schema.validate(entity.name, record);
      assert.strictEqual(result.ok, true, result.errors.join(", "));
      assert.strictEqual(Object.keys(result.value).length, entity.fields.length);
    });

    if (first.type === "text") {
      check(entity.name + ": a blank " + first.name + " is refused", function () {
        var record = {};
        entity.fields.forEach(function (field) {
          record[field.name] = sampleFor(field);
        });
        record[first.name] = "   ";
        var result = Schema.validate(entity.name, record);
        assert.strictEqual(result.ok, false);
        assert.ok(result.errors.length >= 1);
      });
    }

    entity.fields.forEach(function (field) {
      if (field.type === "number") {
        check(entity.name + "." + field.name + ": text is not a number", function () {
          var record = {};
          entity.fields.forEach(function (f) {
            record[f.name] = sampleFor(f);
          });
          record[field.name] = "abc";
          assert.strictEqual(Schema.validate(entity.name, record).ok, false);
        });
      }
      if (field.type === "date") {
        check(entity.name + "." + field.name + ": a date must be YYYY-MM-DD", function () {
          var record = {};
          entity.fields.forEach(function (f) {
            record[f.name] = sampleFor(f);
          });
          record[field.name] = "12/03/2026";
          assert.strictEqual(Schema.validate(entity.name, record).ok, false);
        });
      }
      if (field.type === "boolean") {
        check(entity.name + "." + field.name + ": 'true' and 'on' are true, anything else false", function () {
          assert.strictEqual(Schema.coerce(field, "true").value, true);
          assert.strictEqual(Schema.coerce(field, "on").value, true);
          assert.strictEqual(Schema.coerce(field, "").value, false);
        });
      }
    });
  });

  check("an unknown record kind is refused by name", function () {
    var result = Schema.validate("yok-boyle", {});
    assert.strictEqual(result.ok, false);
    assert.ok(result.errors[0].indexOf("yok-boyle") !== -1);
  });
};

function sampleFor(field) {
  if (field.type === "number") {
    return 42;
  }
  if (field.type === "boolean") {
    return true;
  }
  if (field.type === "date") {
    return "2026-09-15";
  }
  return "Örnek " + field.name;
}

module.exports.sampleFor = sampleFor;
"""

INTEGRATION_TESTS_JS: Final = r"""// PagentOS App Factory (composed) - integration tests: the real server on an ephemeral
// loopback port with an in-memory store, driven over HTTP. Returns a promise.
"use strict";

var assert = require("assert");
var http = require("http");
var Schema = require("../schema.js");
var Server = require("../server.js");
var unit = require("./unit.js");

var AUTH = __AUTH_FLAG__;
var SETUP_PASS = "gizli-parola-1234";

function request(port, method, urlPath, body, cookie) {
  return new Promise(function (resolve, reject) {
    var payload = body === undefined ? null : JSON.stringify(body);
    var headers = {};
    if (payload !== null) {
      headers["Content-Type"] = "application/json";
      headers["Content-Length"] = Buffer.byteLength(payload);
    }
    if (cookie) {
      headers.Cookie = cookie;
    }
    var req = http.request({ host: "127.0.0.1", port: port, method: method, path: urlPath, headers: headers }, function (res) {
      var chunks = [];
      res.on("data", function (c) {
        chunks.push(c);
      });
      res.on("end", function () {
        var text = Buffer.concat(chunks).toString("utf8");
        var data = null;
        try {
          data = text ? JSON.parse(text) : null;
        } catch (err) {
          data = text;
        }
        resolve({ status: res.statusCode, data: data, headers: res.headers });
      });
    });
    req.on("error", reject);
    if (payload !== null) {
      req.write(payload);
    }
    req.end();
  });
}

function sample(entity) {
  var record = {};
  entity.fields.forEach(function (field) {
    record[field.name] = unit.sampleFor(field);
  });
  return record;
}

module.exports = function (check) {
  var server = Server.createServer({ dataFile: null, authFile: null });
  return new Promise(function (resolve) {
    server.listen(0, "127.0.0.1", function () {
      resolve(server.address().port);
    });
  }).then(function (port) {
    var cookie = null;
    var first = Schema.ENTITIES[0];
    var chain = Promise.resolve();

    chain = chain.then(function () {
      return request(port, "GET", "/api/schema").then(function (answer) {
        check("GET /api/schema names every record kind", function () {
          assert.strictEqual(answer.status, 200);
          assert.strictEqual(answer.data.entities.length, Schema.ENTITIES.length);
          assert.strictEqual(answer.data.auth, AUTH);
        });
      });
    });

    if (AUTH) {
      chain = chain.then(function () {
        return request(port, "GET", "/api/" + first.name).then(function (answer) {
          check("a record route without a session is 401", function () {
            assert.strictEqual(answer.status, 401);
            assert.strictEqual(answer.data.error, "login_required");
          });
        });
      }).then(function () {
        return request(port, "POST", "/api/setup", { parola: "kisa" }).then(function (answer) {
          check("a short password is refused at setup", function () {
            assert.strictEqual(answer.status, 400);
          });
        });
      }).then(function () {
        return request(port, "POST", "/api/setup", { parola: SETUP_PASS }).then(function (answer) {
          check("the first setup sets the password and opens a session", function () {
            assert.strictEqual(answer.status, 201);
            assert.ok(answer.headers["set-cookie"]);
          });
        });
      }).then(function () {
        return request(port, "POST", "/api/login", { parola: "yanlis-parola" }).then(function (answer) {
          check("a wrong password is 401", function () {
            assert.strictEqual(answer.status, 401);
          });
        });
      }).then(function () {
        return request(port, "POST", "/api/login", { parola: SETUP_PASS }).then(function (answer) {
          check("the right password opens a session cookie", function () {
            assert.strictEqual(answer.status, 200);
            assert.ok(answer.headers["set-cookie"][0].indexOf("HttpOnly") !== -1);
          });
          cookie = answer.headers["set-cookie"][0].split(";")[0];
        });
      });
    }

    Schema.ENTITIES.forEach(function (entity) {
      var created = null;
      chain = chain.then(function () {
        return request(port, "POST", "/api/" + entity.name, sample(entity), cookie).then(function (answer) {
          check("POST /api/" + entity.name + " creates a record", function () {
            assert.strictEqual(answer.status, 201, JSON.stringify(answer.data));
            assert.strictEqual(typeof answer.data.row.id, "number");
          });
          created = answer.data.row;
        });
      }).then(function () {
        var bad = sample(entity);
        bad[entity.fields[0].name] = entity.fields[0].type === "text" ? "" : "abc";
        return request(port, "POST", "/api/" + entity.name, bad, cookie).then(function (answer) {
          check("POST /api/" + entity.name + " refuses an invalid record with its reasons", function () {
            assert.strictEqual(answer.status, 400);
            assert.ok(answer.data.errors.length >= 1);
          });
        });
      }).then(function () {
        return request(port, "GET", "/api/" + entity.name, undefined, cookie).then(function (answer) {
          check("GET /api/" + entity.name + " lists the record", function () {
            assert.strictEqual(answer.status, 200);
            assert.strictEqual(answer.data.rows.length, 1);
          });
        });
      }).then(function () {
        var patch = {};
        patch[entity.fields[0].name] = unit.sampleFor(entity.fields[0]);
        return request(port, "PUT", "/api/" + entity.name + "/" + created.id, patch, cookie).then(function (answer) {
          check("PUT /api/" + entity.name + "/:id updates and stamps updatedAt", function () {
            assert.strictEqual(answer.status, 200);
            assert.ok(answer.data.row.updatedAt);
          });
        });
      }).then(function () {
        return request(port, "DELETE", "/api/" + entity.name + "/" + created.id, undefined, cookie).then(function (answer) {
          check("DELETE /api/" + entity.name + "/:id removes it", function () {
            assert.strictEqual(answer.status, 200);
          });
        });
      }).then(function () {
        return request(port, "DELETE", "/api/" + entity.name + "/" + created.id, undefined, cookie).then(function (answer) {
          check("DELETE /api/" + entity.name + "/:id twice is 404", function () {
            assert.strictEqual(answer.status, 404);
          });
        });
      });
    });

    chain = chain.then(function () {
      return request(port, "GET", "/api/yok-boyle", undefined, cookie).then(function (answer) {
        check("an unknown record kind is 404", function () {
          assert.strictEqual(answer.status, 404);
        });
      });
    }).then(function () {
      return request(port, "GET", "/").then(function (answer) {
        check("GET / serves the page", function () {
          assert.strictEqual(answer.status, __ROOT_STATUS__);
        });
      });
    }).then(function () {
      return request(port, "GET", "/../server.js").then(function (answer) {
        check("a path outside public/ is refused", function () {
          assert.notStrictEqual(answer.status, 200);
        });
      });
    });

    return chain.then(function () {
      server.close();
    }, function (err) {
      server.close();
      throw err;
    });
  });
};
"""

RUN_JS: Final = r"""// PagentOS App Factory (composed) - the test runner the manifest names: unit tests over
// the schema, integration tests over the server, the model-authored custom tests when
// present. Prints "ok - " / "not ok - " per test and "passed/total passed" at the end,
// exactly what the device parses. `node tests/run.js`.
"use strict";

var fs = require("fs");
var path = require("path");

var passed = 0;
var failed = 0;

function check(name, fn) {
  try {
    fn();
    passed += 1;
    console.log("ok - " + name);
  } catch (err) {
    failed += 1;
    console.log("not ok - " + name + " :: " + (err && err.message ? err.message : String(err)));
  }
}

function finish() {
  var total = passed + failed;
  console.log(passed + "/" + total + " passed");
  process.exit(failed === 0 ? 0 : 1);
}

require("./unit.js")(check);
var customFile = path.join(__dirname, "custom.js");
if (fs.existsSync(customFile)) {
  require(customFile)(check);
}
Promise.resolve(require("./integration.js")(check)).then(finish, function (err) {
  failed += 1;
  console.log("not ok - integration suite crashed :: " + (err && err.message ? err.message : String(err)));
  finish();
});
"""

README_MD: Final = """# __TITLE__

PagentOS App Factory tarafından üretildi (bileşik uygulama). Bağımlılık yok: yalnız Node.

## Çalıştırma

    node server.js

http://127.0.0.1:__PORT__/ adresinde çalışır. Kayıtlar `data/records.json` dosyasında tutulur.

## Testler

    node tests/run.js

Birim testleri `schema.js` üzerinde, bütünleşme testleri geçici bir portta çalışan sunucu
üzerinden koşar; `tests/browser-oracle.json` cihaz laboratuvarının tarayıcıda oynattığı adımlardır.

## Kayıt türleri

__ENTITY_LIST__
__AUTH_NOTE__
"""


def _oracle(entities: list[dict[str, Any]], *, auth: bool) -> dict[str, Any]:
    first = entities[0]
    first_field = first["fields"][0]
    sample = "Örnek kayıt" if first_field["type"] == "text" else "42"
    steps: list[dict[str, Any]] = []
    if auth:
        steps.extend(
            [
                {"action": "fill", "selector": "#login-password", "value": "gizli-parola-1234"},
                {"action": "click", "selector": "#login-button"},
                {"action": "assert_visible", "selector": "#kinds"},
            ]
        )
    for field in first["fields"]:
        selector = f"#form-{first['name']} [name={field['name']}]"
        if field["type"] == "boolean":
            steps.append({"action": "click", "selector": selector})
        elif field["type"] == "date":
            steps.append({"action": "fill", "selector": selector, "value": "2026-09-15"})
        elif field["type"] == "number":
            steps.append({"action": "fill", "selector": selector, "value": "42"})
        else:
            steps.append({"action": "fill", "selector": selector, "value": sample})
    steps.extend(
        [
            {"action": "click", "selector": f"#add-{first['name']}"},
            {
                "action": "assert_text",
                "selector": f"#list-{first['name']}",
                "contains": sample if first_field["type"] == "text" else "42",
            },
            {"action": "reload"},
        ]
    )
    if auth:
        steps.append({"action": "assert_visible", "selector": "#kinds"})
    steps.append(
        {
            "action": "assert_text",
            "selector": f"#list-{first['name']}",
            "contains": sample if first_field["type"] == "text" else "42",
        }
    )
    initial = [
        {"selector": "#app-title", "exists": True},
        {"selector": f"#form-{first['name']}", "exists": True},
        {"selector": f"#list-{first['name']}", "exists": True},
    ]
    if auth:
        initial.insert(1, {"selector": "#login-form", "exists": True})
    return {
        "template": TEMPLATE_COMPOSED,
        "url_path": "/",
        "initial_assertions": initial,
        "interaction_steps": steps,
    }


def _inputs_html(entity: dict[str, Any]) -> str:
    lines: list[str] = []
    for field in entity["fields"]:
        fid = f"field-{entity['name']}-{field['name']}"
        label = html.escape(field["label"], quote=True)
        if field["type"] == "boolean":
            lines.append(
                f'          <label><input id="{fid}" name="{field["name"]}" type="checkbox"> {label}</label>'
            )
        elif field["type"] == "number":
            lines.append(
                f'          <input id="{fid}" name="{field["name"]}" type="number" step="any" placeholder="{label}" required>'
            )
        elif field["type"] == "date":
            lines.append(
                f'          <input id="{fid}" name="{field["name"]}" type="date" placeholder="{label}" required>'
            )
        else:
            required = " required" if field is entity["fields"][0] else ""
            lines.append(
                f'          <input id="{fid}" name="{field["name"]}" type="text" maxlength="500" placeholder="{label}"{required}>'
            )
    return "\n".join(lines)


class ComposedAppGenerator:
    """The files of :class:`ProjectPlan`, composed from the sources above."""

    name = "composed"

    def generate_from_plan(
        self,
        *,
        app_name: str,
        arch: ArchitecturePlan,
        plan: ProjectPlan,
        custom_files: dict[str, str] | None = None,
    ) -> ProjectFiles:
        entities = _entity_json(arch.entities)
        entities_json = json.dumps(entities, ensure_ascii=False, indent=2)
        title = html.escape(app_name, quote=True)
        auth_flag = "true" if arch.auth else "false"
        files: list[ProjectFile] = [
            ProjectFile("schema.js", SCHEMA_JS.replace("__ENTITIES__", entities_json)),
            ProjectFile("store.js", STORE_JS),
        ]
        if arch.auth:
            files.append(ProjectFile("auth.js", AUTH_JS))
        server = (
            SERVER_JS.replace(
                "__AUTH_COMMENT__",
                "a login in front of every record route."
                if arch.auth
                else "no login (none was asked for).",
            )
            .replace("__AUTH_REQUIRE__", AUTH_REQUIRE if arch.auth else "")
            .replace("__AUTH_CREATE__", AUTH_CREATE if arch.auth else "")
            .replace("__AUTH_ROUTES__", AUTH_ROUTES if arch.auth else "")
            .replace("__AUTH_GUARD__", AUTH_GUARD if arch.auth else "")
            .replace("__AUTH_FLAG__", auth_flag)
            .replace("__PORT__", str(int(arch.port)))
            .replace(
                "__APP_NAME_JS__", json.dumps(app_name, ensure_ascii=False)[1:-1].replace('"', "")
            )
        )
        files.append(ProjectFile("server.js", server))
        if arch.frontend:
            sections = "\n".join(
                SECTION_HTML.replace("__KIND__", e["name"])
                .replace("__LABEL__", html.escape(e["label"], quote=True))
                .replace("__INPUTS__", _inputs_html(e))
                for e in entities
            )
            page = (
                INDEX_HTML.replace("__TITLE__", title)
                .replace("__LOGIN_SECTION__", LOGIN_SECTION if arch.auth else "")
                .replace("__SECTIONS__", sections)
            )
            files.append(ProjectFile("public/index.html", page))
            files.append(ProjectFile("public/app.css", APP_CSS))
            files.append(ProjectFile("public/app.js", APP_JS.replace("__AUTH_FLAG__", auth_flag)))
            files.append(
                ProjectFile("public/schema.js", SCHEMA_JS.replace("__ENTITIES__", entities_json))
            )
        for path_, text in (custom_files or {}).items():
            files.append(ProjectFile(path_, text))
        files.append(ProjectFile("tests/unit.js", UNIT_TESTS_JS))
        files.append(
            ProjectFile(
                "tests/integration.js",
                INTEGRATION_TESTS_JS.replace("__AUTH_FLAG__", auth_flag).replace(
                    "__ROOT_STATUS__", "200" if arch.frontend else "404"
                ),
            )
        )
        files.append(ProjectFile("tests/run.js", RUN_JS))
        if arch.frontend:
            files.append(
                ProjectFile(
                    "tests/browser-oracle.json",
                    json.dumps(_oracle(entities, auth=arch.auth), ensure_ascii=False, indent=2)
                    + "\n",
                )
            )
        entity_list = "\n".join(
            f"- **{html.escape(e['label'])}** (`/api/{e['name']}`): "
            + ", ".join(f"{f['label']} ({f['type']})" for f in e["fields"])
            for e in entities
        )
        auth_note = (
            "\nGiriş: ilk açılışta bir parola belirlenir; her kayıt yolu oturum ister.\n"
            if arch.auth
            else ""
        )
        files.append(
            ProjectFile(
                "README.md",
                README_MD.replace("__TITLE__", html.escape(app_name))
                .replace("__PORT__", str(int(arch.port)))
                .replace("__ENTITY_LIST__", entity_list)
                .replace("__AUTH_NOTE__", auth_note),
            )
        )
        manifest = {
            "entry": plan.entry,
            "run": {"serve": plan.run_command},
            "test": {"unit": plan.test_command},
            "port": int(arch.port),
        }
        files.append(ProjectFile("manifest.json", json.dumps(manifest, indent=2) + "\n"))
        return ProjectFiles(files=tuple(files))


__all__ = ["TEMPLATE_COMPOSED", "ComposedAppGenerator", "slugify"]
