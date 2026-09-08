// PagentOS App Factory (static-page) — stdlib-only Node test runner over logic.js's pure functions.
var assert = require("assert");
var PageLogic = require("../logic.js");

var passed = 0;

function check(name, fn) {
  fn();
  passed += 1;
  console.log("ok - " + name);
}

check("pageSummary counts words", function () {
  assert.strictEqual(PageLogic.pageSummary("Başlık", "bir iki üç"), "Başlık (3 kelime)");
});

check("pageSummary handles empty body", function () {
  assert.strictEqual(PageLogic.pageSummary("Başlık", ""), "Başlık (0 kelime)");
});

console.log(passed + "/" + passed + " passed");
process.exit(0);
