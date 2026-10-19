// Choose the first visible surface before CSS paints. Safe mode set in storage is known only later, in console.js,
// which then shows the Console; the ?safe=1 link is known here.
(() => {
  const params = new URL(location.href).searchParams;
  const surface = params.get("surface") ?? "tab";
  const consoleVisible = params.get("safe") === "1" || params.get("mode") === "console" || surface === "tab";
  document.documentElement.dataset.surface = surface;
  document.documentElement.dataset.view = consoleVisible ? "console" : "app";
})();
