// PagentOS App Factory (static-page) — pure logic (no DOM). Same require()-from-Node / <script>-in-browser
// pattern as the task-tracker template's logic.js.
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.PageLogic = factory();
  }
})(typeof window !== "undefined" ? window : this, function () {
  "use strict";

  function pageSummary(heading, body) {
    var words = (body || "").trim().length === 0 ? 0 : body.trim().split(/\s+/).length;
    return heading + " (" + words + " kelime)";
  }

  return { pageSummary: pageSummary };
});
