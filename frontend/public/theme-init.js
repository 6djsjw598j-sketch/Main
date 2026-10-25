// Applies the saved theme before first paint to avoid a light/dark flash.
try {
  var saved = JSON.parse(localStorage.getItem("marketlens.preferences") || "{}");
  if (saved.theme === "light") document.documentElement.classList.remove("dark");
} catch (e) {
  /* storage unavailable */
}
