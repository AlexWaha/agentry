/* Applies the saved theme before first paint. Loaded as a blocking external script in <head>
   (the CSP allows no inline script). The storage key must equal STORAGE_KEYS.theme in src/config/storage-keys.ts;
   a unit test compares the two. "system" or no saved choice follows the OS colour scheme. */
(function () {
  var choice = "system";
  try {
    choice = localStorage.getItem("agentry-dashboard-theme") || choice;
  } catch {
    // Storage blocked: follow the system.
  }
  var dark = choice === "dark" || (choice !== "light" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
})();
