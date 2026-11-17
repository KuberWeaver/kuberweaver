/* Applies the stored theme before the first paint, so the portal never shows
   the wrong theme for a frame. This is a classic script rather than an inline
   one because the app is served under script-src 'self'. The storage key and
   both colours are asserted against src/theme.ts in theme.test.ts. */
(function () {
  var theme = "dark";
  try {
    if (window.localStorage.getItem("kuberweaver.theme") === "light") {
      theme = "light";
    }
  } catch (error) {
    theme = "dark";
  }
  document.documentElement.setAttribute("data-theme", theme);
  var meta = document.querySelector('meta[name="theme-color"]');
  if (meta) {
    meta.setAttribute("content", theme === "light" ? "#f5f7f9" : "#0b1116");
  }
})();
