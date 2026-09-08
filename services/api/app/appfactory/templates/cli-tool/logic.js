// PagentOS App Factory (cli-tool) — pure logic (no process, no argv parsing). Same require()-from-Node
// pattern as the other templates' logic.js.
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.CliLogic = factory();
  }
})(typeof window !== "undefined" ? window : this, function () {
  "use strict";

  function helpText(commands) {
    var lines = ["Kullanım: cli.js <komut> [argümanlar]", "Komutlar:"];
    commands.forEach(function (c) {
      lines.push("  " + c);
    });
    return lines.join("\n");
  }

  function commandOutput(name, args) {
    if (!Array.isArray(args) || args.length === 0) {
      return name + " çalıştı.";
    }
    return name + " çalıştı: " + args.join(" ");
  }

  return { helpText: helpText, commandOutput: commandOutput };
});
