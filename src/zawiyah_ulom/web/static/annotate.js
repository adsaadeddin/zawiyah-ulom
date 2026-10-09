// Notes on a passage, shared by the reading page and the study view.
// Annotatable text is <p data-section="<section id>" data-p="<paragraph index>">.
// Selecting words inside one such paragraph shows a ✎ button; it opens the
// new-note form under the paragraph. Clicking a highlight (mark.ann) shows its
// note card(s). See templates/_ann_macros.html.
(function () {
  "use strict";
  const addBtn = document.getElementById("ann-add");
  const form = document.getElementById("ann-new");
  if (!addBtn || !form) return;
  const CONTEXT = 32;  // characters of context stored before/after the quote (notes.ANCHOR_CONTEXT)
  let pending = null;

  // Ctrl+Enter submits any note form on the page.
  document.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && e.target.form) e.target.form.requestSubmit();
  });

  // Clicking a highlight toggles its note card(s); × closes a card.
  function toggleCards(ids) {
    ids.forEach((id) => {
      const card = document.getElementById("ann-" + id);
      if (card) card.hidden = !card.hidden;
    });
  }
  document.addEventListener("click", (e) => {
    const mark = e.target.closest && e.target.closest("mark.ann");
    if (mark && window.getSelection().isCollapsed) toggleCards(mark.dataset.ids.split(" "));
    const close = e.target.closest && e.target.closest(".ann-close");
    if (close) close.closest(".ann-card").hidden = true;
    // A note listed elsewhere (the study view's notes column) opens its card in the text.
    const jump = e.target.closest && e.target.closest("[data-open-ann]");
    if (jump) {
      e.preventDefault();
      const card = document.getElementById("ann-" + jump.dataset.openAnn);
      if (card) { card.hidden = false; card.scrollIntoView({ block: "center", behavior: "smooth" }); }
    }
  });
  document.addEventListener("keydown", (e) => {
    const mark = e.target.closest && e.target.closest("mark.ann");
    if (mark && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); toggleCards(mark.dataset.ids.split(" ")); }
  });

  // Selecting text offers the ✎ button.
  function paragraphOf(node) {
    const el = node.nodeType === 1 ? node : node.parentElement;
    return el && el.closest("p[data-section][data-p]");
  }
  function currentSelection() {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || sel.rangeCount === 0) return null;
    const range = sel.getRangeAt(0);
    const p = paragraphOf(range.startContainer);
    if (!p) return null;
    if (p !== paragraphOf(range.endContainer)) return { error: "حدد نصًا داخل فقرة واحدة" };
    const before = document.createRange();
    before.selectNodeContents(p);
    before.setEnd(range.startContainer, range.startOffset);
    let start = before.toString().length;
    let quote = range.toString();
    start += quote.length - quote.trimStart().length;
    quote = quote.trim();
    if (!quote) return null;
    const text = p.textContent;
    return { p, rect: range.getBoundingClientRect(), quote,
             prefix: text.slice(Math.max(0, start - CONTEXT), start),
             suffix: text.slice(start + quote.length, start + quote.length + CONTEXT) };
  }
  function showButton() {
    const sel = currentSelection();
    if (!sel) { addBtn.hidden = true; return; }
    addBtn.hidden = false;
    if (sel.error) { addBtn.textContent = sel.error; addBtn.disabled = true; }
    else { addBtn.textContent = "✎ ملاحظة"; addBtn.disabled = false; pending = sel; }
    addBtn.style.top = (window.scrollY + sel.rect.top - 40) + "px";
    addBtn.style.left = (window.scrollX + sel.rect.left + sel.rect.width / 2 - 45) + "px";
  }
  let timer;
  document.addEventListener("selectionchange", () => { clearTimeout(timer); timer = setTimeout(showButton, 250); });
  addBtn.addEventListener("mousedown", (e) => e.preventDefault());  // keep the selection
  addBtn.addEventListener("click", () => {
    if (!pending) return;
    form.action = form.dataset.action.replace("/0/", "/" + pending.p.dataset.section + "/");
    form.elements.quote.value = pending.quote;
    form.elements.prefix.value = pending.prefix;
    form.elements.suffix.value = pending.suffix;
    form.elements.paragraph.value = pending.p.dataset.p;
    document.getElementById("ann-new-quote").textContent = pending.quote;
    pending.p.after(form);
    form.hidden = false;
    addBtn.hidden = true;
    window.getSelection().removeAllRanges();
    form.elements.body.focus();
  });
  document.getElementById("ann-cancel").addEventListener("click", () => { form.hidden = true; form.reset(); });

  // Opened from a link (#ann-12): show and scroll to that note.
  const target = location.hash.startsWith("#ann-") && document.querySelector(location.hash);
  if (target) { target.hidden = false; target.scrollIntoView({ block: "center" }); }
})();
