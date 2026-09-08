// {{APP_TITLE}} — a stdlib-only Node test runner over logic.js's pure functions
// (docs/M23_APP_FACTORY_SPEC.md §2: "a tests/ folder with a Node test runner over the
// app's pure functions"). No dependencies, no network: `node tests/run.js`, the exact
// command the manifest's "test" entry names.
var assert = require("assert");
var TaskLogic = require("../logic.js");

var passed = 0;

function check(name, fn) {
  fn();
  passed += 1;
  console.log("ok - " + name);
}

check("addTask appends a task with done=false", function () {
  var tasks = TaskLogic.addTask([], "Sütü al");
  assert.strictEqual(tasks.length, 1);
  assert.strictEqual(tasks[0].text, "Sütü al");
  assert.strictEqual(tasks[0].done, false);
});

check("addTask ignores blank text", function () {
  var tasks = TaskLogic.addTask([], "   ");
  assert.strictEqual(tasks.length, 0);
});

check("toggleDone flips only the matching task", function () {
  var tasks = TaskLogic.addTask(TaskLogic.addTask([], "A"), "B");
  var toggled = TaskLogic.toggleDone(tasks, tasks[0].id);
  assert.strictEqual(toggled[0].done, true);
  assert.strictEqual(toggled[1].done, false);
});

check("serialize/deserialize round-trips (persistence across reload)", function () {
  var tasks = TaskLogic.toggleDone(TaskLogic.addTask([], "Kalıcı görev"), 1);
  var json = TaskLogic.serialize(tasks);
  var restored = TaskLogic.deserialize(json);
  assert.strictEqual(restored.length, 1);
  assert.strictEqual(restored[0].text, "Kalıcı görev");
  assert.strictEqual(restored[0].done, true);
});

check("deserialize tolerates missing/invalid storage", function () {
  assert.deepStrictEqual(TaskLogic.deserialize(null), []);
  assert.deepStrictEqual(TaskLogic.deserialize("not json"), []);
});

console.log(passed + "/" + passed + " passed");
process.exit(0);
