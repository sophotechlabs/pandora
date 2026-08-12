(function () {
  "use strict";

  var REFRESH_MS = 30000;
  var TYPING = ["INPUT", "TEXTAREA", "SELECT"];

  function boxes() {
    return Array.prototype.slice.call(
      document.querySelectorAll('#stream-form input[name="issue"]')
    );
  }

  function checked() {
    return boxes().filter(function (box) {
      return box.checked;
    });
  }

  function paintSelection() {
    var counter = document.querySelector("[data-selection-count]");
    if (!counter) {
      return;
    }
    counter.textContent = String(checked().length);
  }

  function bindSelection() {
    var form = document.getElementById("stream-form");
    if (!form) {
      return;
    }
    form.addEventListener("change", paintSelection);
    var all = document.querySelector("[data-select-all]");
    if (all) {
      all.addEventListener("change", function () {
        boxes().forEach(function (box) {
          box.checked = all.checked;
        });
        paintSelection();
      });
    }
    var clear = document.querySelector("[data-clear-selection]");
    if (clear) {
      clear.addEventListener("click", function () {
        boxes().forEach(function (box) {
          box.checked = false;
        });
        if (all) {
          all.checked = false;
        }
        paintSelection();
      });
    }
    paintSelection();
  }

  function bindTheme() {
    var button = document.querySelector("[data-theme-toggle]");
    if (!button) {
      return;
    }
    button.addEventListener("click", function () {
      var root = document.documentElement;
      var dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
      var current = root.dataset.theme;
      if (!current) {
        if (dark) {
          current = "dark";
        } else {
          current = "light";
        }
      }
      var next = "light";
      if (current === "light") {
        next = "dark";
      }
      root.dataset.theme = next;
      try {
        window.localStorage.setItem("pandora-theme", next);
      } catch (error) {
        return;
      }
    });
  }

  function rows() {
    return Array.prototype.slice.call(document.querySelectorAll(".issue-row"));
  }

  function cursorIndex(all) {
    for (var index = 0; index < all.length; index += 1) {
      if (all[index].classList.contains("cursor")) {
        return index;
      }
    }
    return -1;
  }

  function moveCursor(step) {
    var all = rows();
    if (!all.length) {
      return;
    }
    var index = cursorIndex(all) + step;
    if (index < 0) {
      index = 0;
    }
    if (index > all.length - 1) {
      index = all.length - 1;
    }
    all.forEach(function (row) {
      row.classList.remove("cursor");
    });
    all[index].classList.add("cursor");
    all[index].scrollIntoView({ block: "nearest" });
  }

  function currentRow() {
    var all = rows();
    var index = cursorIndex(all);
    if (index < 0) {
      return null;
    }
    return all[index];
  }

  function isTyping(target) {
    if (!target) {
      return false;
    }
    if (target.isContentEditable) {
      return true;
    }
    return TYPING.indexOf(target.tagName) !== -1;
  }

  function bindKeys() {
    document.addEventListener("keydown", function (event) {
      if (event.metaKey || event.ctrlKey || event.altKey) {
        return;
      }
      var search = document.getElementById("search");
      if (isTyping(event.target)) {
        if (event.key === "Escape" && event.target === search) {
          search.blur();
        }
        return;
      }
      if (event.key === "/" && search) {
        event.preventDefault();
        search.focus();
        search.select();
        return;
      }
      if (event.key === "j") {
        moveCursor(1);
        return;
      }
      if (event.key === "k") {
        moveCursor(-1);
        return;
      }
      var row = currentRow();
      if (!row) {
        return;
      }
      if (event.key === "x") {
        event.preventDefault();
        var box = row.querySelector('input[name="issue"]');
        if (box) {
          box.checked = !box.checked;
          paintSelection();
        }
        return;
      }
      if (event.key === "Enter") {
        window.location.href = row.dataset.href;
      }
    });
  }

  function bindMenus() {
    document.addEventListener("click", function (event) {
      document.querySelectorAll("details.menu[open]").forEach(function (menu) {
        if (!menu.contains(event.target)) {
          menu.open = false;
        }
      });
    });
  }

  function partialUrl(href) {
    var url = new URL(href, window.location.origin);
    url.searchParams.set("partial", "1");
    return url.toString();
  }

  function paintTabs(href) {
    document.querySelectorAll("[data-tab]").forEach(function (link) {
      link.classList.remove("active");
      if (link.getAttribute("href") === href) {
        link.classList.add("active");
      }
    });
  }

  function loadTab(href, push) {
    var panel = document.getElementById("tab-panel");
    if (!panel) {
      return;
    }
    fetch(partialUrl(href), { headers: { "X-Requested-With": "fetch" } })
      .then(function (response) {
        return response.text();
      })
      .then(function (html) {
        panel.innerHTML = html;
        paintTabs(href);
        if (push) {
          window.history.pushState({ tab: href }, "", href);
        }
      })
      .catch(function () {
        window.location.href = href;
      });
  }

  function bindTabs() {
    document.addEventListener("click", function (event) {
      if (event.metaKey || event.ctrlKey || event.shiftKey) {
        return;
      }
      var link = event.target.closest("[data-tab]");
      if (!link) {
        return;
      }
      event.preventDefault();
      loadTab(link.getAttribute("href"), true);
    });
    window.addEventListener("popstate", function () {
      if (document.getElementById("tab-panel")) {
        loadTab(window.location.pathname, false);
      }
    });
  }

  function swapRows(html) {
    var current = document.getElementById("stream-rows");
    if (!current) {
      return;
    }
    var holder = document.createElement("table");
    holder.innerHTML = html;
    var fresh = holder.querySelector("#stream-rows");
    if (!fresh) {
      return;
    }
    current.replaceWith(fresh);
    var total = document.getElementById("stream-total");
    if (total) {
      total.textContent = fresh.dataset.total;
    }
    paintSelection();
  }

  function refreshStream() {
    if (document.hidden) {
      return;
    }
    if (!document.getElementById("stream-rows")) {
      return;
    }
    if (checked().length) {
      return;
    }
    if (document.activeElement === document.getElementById("search")) {
      return;
    }
    fetch(partialUrl(window.location.href), {
      headers: { "X-Requested-With": "fetch" }
    })
      .then(function (response) {
        return response.text();
      })
      .then(swapRows)
      .catch(function () {
        return;
      });
  }

  bindSelection();
  bindTheme();
  bindKeys();
  bindTabs();
  bindMenus();
  window.setInterval(refreshStream, REFRESH_MS);
})();
