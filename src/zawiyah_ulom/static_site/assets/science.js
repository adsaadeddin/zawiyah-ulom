// Science tabs for the static site. The chosen science comes from ?sci=<slug>,
// else localStorage, else الفقه (like the local app's cookie). Elements with
// data-sci="<slugs>" outside it are hidden; search.js reads window.ZU_SCIENCE.
(function () {
  "use strict";
  const KEY = "zawiyah-science";
  let sci = new URLSearchParams(location.search).get("sci");
  try {
    if (sci) localStorage.setItem(KEY, sci); else sci = localStorage.getItem(KEY);
  } catch (e) { /* storage blocked: fall back to the URL / default */ }
  window.ZU_SCIENCE = sci || "fiqh";

  document.addEventListener("DOMContentLoaded", () => {
    const tabs = [...document.querySelectorAll(".science-tabs [data-tab]")];
    if (!tabs.some((t) => t.dataset.tab === window.ZU_SCIENCE)) window.ZU_SCIENCE = "fiqh";
    const cur = window.ZU_SCIENCE, all = cur === "all";
    for (const t of tabs) t.classList.toggle("active", t.dataset.tab === cur);

    let shown = 0, total = 0;
    for (const el of document.querySelectorAll("[data-sci]")) {
      const ok = all || el.dataset.sci.split(" ").includes(cur);
      if (el.tagName === "OPTION") { el.hidden = el.disabled = !ok; continue; }
      el.hidden = !ok;
      if (el.classList.contains("card")) { total++; if (ok) shown++; }
    }
    for (const el of document.querySelectorAll(".empty-science")) el.hidden = !(total && !shown);
    for (const el of document.querySelectorAll(".sci-only")) el.hidden = all;

    const tab = tabs.find((t) => t.dataset.tab === cur);
    const q = document.getElementById("q");
    if (q && tab && !all) q.placeholder = "ابحث في كتب " + tab.dataset.name + "…";
  });
})();
