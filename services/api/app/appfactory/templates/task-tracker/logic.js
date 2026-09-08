// PagentOS App Factory (task-tracker) — pure logic (no DOM, no localStorage). Loaded as a plain <script> in
// the browser (defines window.TaskLogic) and via require() from tests/run.js in Node —
// the SAME functions, exercised both ways, so a Node test proves what the browser runs.
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.TaskLogic = factory();
  }
})(typeof window !== "undefined" ? window : this, function () {
  "use strict";

  function nextId(tasks) {
    var max = 0;
    for (var i = 0; i < tasks.length; i++) {
      if (tasks[i].id > max) {
        max = tasks[i].id;
      }
    }
    return max + 1;
  }

  function addTask(tasks, text) {
    var trimmed = (text || "").trim();
    if (!trimmed) {
      return tasks;
    }
    var task = { id: nextId(tasks), text: trimmed, done: false };
    return tasks.concat([task]);
  }

  function toggleDone(tasks, id) {
    return tasks.map(function (t) {
      if (t.id !== id) {
        return t;
      }
      return { id: t.id, text: t.text, done: !t.done };
    });
  }

  function serialize(tasks) {
    return JSON.stringify(tasks);
  }

  function deserialize(json) {
    if (!json) {
      return [];
    }
    try {
      var parsed = JSON.parse(json);
      if (!Array.isArray(parsed)) {
        return [];
      }
      return parsed;
    } catch (e) {
      return [];
    }
  }

  return {
    addTask: addTask,
    toggleDone: toggleDone,
    serialize: serialize,
    deserialize: deserialize,
  };
});
