// Applies a saved light/dark choice before first paint; "system" leaves the attribute unset.
// Plain ES5: files in public/ are copied without transpiling.
(function () {
  var savedTheme = null;
  try {
    savedTheme = window.localStorage.getItem("crs-theme");
  } catch (error) {
    // Storage may be unavailable; the system appearance is used instead.
  }
  if (savedTheme !== "light" && savedTheme !== "dark") return;
  document.documentElement.setAttribute("data-theme", savedTheme);
  var metas = document.querySelectorAll('meta[name="theme-color"][data-scheme]');
  for (var i = 0; i < metas.length; i++) {
    metas[i].setAttribute("media", metas[i].getAttribute("data-scheme") === savedTheme ? "all" : "not all");
  }
})();
