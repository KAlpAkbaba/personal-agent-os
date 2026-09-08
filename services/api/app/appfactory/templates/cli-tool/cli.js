#!/usr/bin/env node
// {{APP_TITLE}} — a tiny command-line tool. `node cli.js --help` lists its commands
// (docs/M23_APP_FACTORY_SPEC.md §4: exercised through terminal.execute, read-only).
"use strict";

var path = require("path");
var CliLogic = require(path.join(__dirname, "logic.js"));

var COMMANDS = {{COMMANDS_JSON}};

var args = process.argv.slice(2);

if (args.length === 0 || args[0] === "--help" || args[0] === "-h") {
  console.log(CliLogic.helpText(COMMANDS));
  process.exit(0);
}

var name = args[0];
if (COMMANDS.indexOf(name) === -1) {
  console.error("Bilinmeyen komut: " + name);
  console.error(CliLogic.helpText(COMMANDS));
  process.exit(1);
}

console.log(CliLogic.commandOutput(name, args.slice(1)));
process.exit(0);
