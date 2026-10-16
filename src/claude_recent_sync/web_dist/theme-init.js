// Applies a saved light/dark choice before first paint; "system" leaves the attribute unset.
try {
  var savedTheme = window.localStorage.getItem("crs-theme");
  if (savedTheme === "light" || savedTheme === "dark") document.documentElement.dataset.theme = savedTheme;
} catch (error) {
  // Storage may be unavailable; the system appearance is used instead.
}
