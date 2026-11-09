// pagewright site search: loads /search-index.json once and searches it in the browser.
(function () {
  const input = document.getElementById("search-input");
  const list = document.getElementById("search-results");
  if (!input || !list) return;
  let pages = null;
  let loading = null;

  function load() {
    if (pages) return Promise.resolve(pages);
    if (!loading) {
      loading = fetch("/search-index.json")
        .then((r) => (r.ok ? r.json() : { pages: [] }))
        .then((data) => (pages = data.pages || []))
        .catch(() => (pages = []));
    }
    return loading;
  }

  function score(page, terms) {
    const title = page.title.toLowerCase();
    const tags = (page.tags || []).join(" ").toLowerCase();
    const text = (page.summary + " " + page.text).toLowerCase();
    let total = 0;
    for (const term of terms) {
      const inTitle = title.includes(term), inTags = tags.includes(term), inText = text.includes(term);
      if (!inTitle && !inTags && !inText) return 0; // every word must appear somewhere
      total += (inTitle ? 10 : 0) + (inTags ? 5 : 0) + (inText ? 1 : 0);
    }
    return total;
  }

  function show(results, query) {
    list.replaceChildren();
    if (!query) { list.hidden = true; return; }
    if (!results.length) {
      const li = document.createElement("li");
      li.textContent = "No results";
      li.style.padding = "0.5rem 0.6rem";
      list.append(li);
    }
    for (const page of results.slice(0, 8)) {
      const li = document.createElement("li");
      const a = document.createElement("a");
      a.href = page.url;
      a.textContent = page.title;
      if (page.summary) {
        const small = document.createElement("small");
        small.textContent = page.summary.slice(0, 110) + (page.summary.length > 110 ? "…" : "");
        a.append(small);
      }
      li.append(a);
      list.append(li);
    }
    list.hidden = false;
  }

  let timer;
  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const query = input.value.trim().toLowerCase();
      const terms = query.split(/\s+/).filter(Boolean);
      const all = await load();
      const results = all
        .map((p) => ({ p, s: score(p, terms) }))
        .filter((x) => x.s > 0)
        .sort((a, b) => b.s - a.s)
        .map((x) => x.p);
      show(results, query);
    }, 120);
  });
  input.addEventListener("focus", load, { once: true });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { input.value = ""; show([], ""); }
    if (e.key === "ArrowDown") { const first = list.querySelector("a"); if (first) { e.preventDefault(); first.focus(); } }
  });
  document.addEventListener("click", (e) => { if (!e.target.closest(".search")) list.hidden = true; });
})();
