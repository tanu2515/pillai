// Shared light/dark theme toggle. Include this as the FIRST script in
// <head> (before the Tailwind CDN script) so data-theme is set before first
// paint — avoids a flash of the wrong theme. Every page defines its own
// :root { --bg:...; } and :root[data-theme="dark"] { --bg:...; } blocks
// using the same variable names, and points its own tailwind.config colors
// at var(--bg) etc. so both Tailwind utility classes and hand-written CSS
// rules repaint together when the attribute flips.
(function () {
  function getTheme() {
    try {
      return localStorage.getItem("vyavastha_theme") || "light";
    } catch (e) {
      return "light";
    }
  }

  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    const btn = document.getElementById("theme-toggle-btn");
    if (btn) btn.textContent = theme === "dark" ? "☀️" : "🌙";
  }

  applyTheme(getTheme());

  window.toggleTheme = function () {
    const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
    try {
      localStorage.setItem("vyavastha_theme", next);
    } catch (e) {}
    applyTheme(next);
  };

  document.addEventListener("DOMContentLoaded", function () {
    applyTheme(getTheme());
  });
})();
