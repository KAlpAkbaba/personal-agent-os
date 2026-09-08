// PagentOS App Factory (task-tracker) — DOM glue over logic.js (TaskLogic) + localStorage. Vanilla JS, no
// build step, no framework: the app.js/index.html/app.css/logic.js quartet is the
// whole runtime (docs/M23_APP_FACTORY_SPEC.md §2).
(function () {
  "use strict";

  var STORAGE_KEY = "{{STORAGE_KEY}}";

  function loadTasks() {
    return TaskLogic.deserialize(window.localStorage.getItem(STORAGE_KEY));
  }

  function saveTasks(tasks) {
    window.localStorage.setItem(STORAGE_KEY, TaskLogic.serialize(tasks));
  }

  function render(tasks) {
    var list = document.getElementById("task-list");
    var empty = document.getElementById("empty-message");
    list.innerHTML = "";
    if (tasks.length === 0) {
      empty.hidden = false;
    } else {
      empty.hidden = true;
    }
    tasks.forEach(function (task) {
      var li = document.createElement("li");
      li.className = "task-item" + (task.done ? " done" : "");
      li.dataset.taskId = String(task.id);

      var toggle = document.createElement("input");
      toggle.type = "checkbox";
      toggle.className = "toggle-done";
      toggle.checked = task.done;
      toggle.addEventListener("change", function () {
        var tasks2 = TaskLogic.toggleDone(loadTasks(), task.id);
        saveTasks(tasks2);
        render(tasks2);
      });

      var span = document.createElement("span");
      span.className = "task-text";
      span.textContent = task.text;

      li.appendChild(toggle);
      li.appendChild(span);
      list.appendChild(li);
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    var tasks = loadTasks();
    render(tasks);

    var form = document.getElementById("task-form");
    var input = document.getElementById("new-task-input");
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var updated = TaskLogic.addTask(loadTasks(), input.value);
      saveTasks(updated);
      input.value = "";
      render(updated);
    });
  });
})();
