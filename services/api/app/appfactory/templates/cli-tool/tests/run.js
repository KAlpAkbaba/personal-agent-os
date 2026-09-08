// {{APP_TITLE}} — stdlib-only Node test runner over logic.js's pure functions.
var assert = require("assert");
var CliLogic = require("../logic.js");

var COMMANDS = {{COMMANDS_JSON}};

var passed = 0;

function check(name, fn) {
  fn();
  passed += 1;
  console.log("ok - " + name);
}

check("helpText lists every command", function () {
  var text = CliLogic.helpText(COMMANDS);
  COMMANDS.forEach(function (c) {
    assert.ok(text.indexOf(c) !== -1, "help text should mention " + c);
  });
});

check("commandOutput echoes the command with no args", function () {
  assert.strictEqual(CliLogic.commandOutput(COMMANDS[0], []), COMMANDS[0] + " çalıştı.");
});

check("commandOutput echoes args", function () {
  assert.strictEqual(
    CliLogic.commandOutput(COMMANDS[0], ["a", "b"]),
    COMMANDS[0] + " çalıştı: a b"
  );
});

console.log(passed + "/" + passed + " passed");
process.exit(0);
