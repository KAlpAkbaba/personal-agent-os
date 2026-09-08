// {{PAGE_TITLE}} — nothing to wire up beyond a page-load log line; this template is a
// static page (docs/M23_APP_FACTORY_SPEC.md §2), not an interactive app.
(function () {
  "use strict";
  document.addEventListener("DOMContentLoaded", function () {
    var heading = document.getElementById("page-heading").textContent;
    var body = document.getElementById("page-body").textContent;
    // Exercises the same pure function the Node test runner checks, so a console
    // reader (or a browser evidence fetch) sees the same summary the tests assert.
    console.log(PageLogic.pageSummary(heading, body));
  });
})();
