/* @odoo-module ignore */
// The guide's only script: filter the navigation as you type, ask the
// server for full-text matches, open screenshots in a lightbox, copy the
// replay command. Nothing here runs without the reader doing something.
(() => {
    "use strict";

    // --- Search ------------------------------------------------------
    const search = document.getElementById("doc-search");
    const results = document.getElementById("doc-results");
    let pending = null;
    let timer = null;

    function filterNavigation(value) {
        for (const link of document.querySelectorAll(".doc-link")) {
            link.hidden = Boolean(value) && !link.dataset.search.includes(value);
        }
        for (const group of document.querySelectorAll(".doc-group")) {
            const links = Array.from(group.querySelectorAll(".doc-link"));
            group.hidden = links.length > 0 && links.every((link) => link.hidden);
            for (const app of group.querySelectorAll(".doc-app")) {
                let sibling = app.nextElementSibling;
                let visible = false;
                while (sibling && !sibling.classList.contains("doc-app")) {
                    visible = visible || !sibling.hidden;
                    sibling = sibling.nextElementSibling;
                }
                app.hidden = !visible;
            }
        }
    }

    function escapeHtml(text) {
        return String(text).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    }

    async function fullText(value) {
        if (!results || !search.dataset.endpoint) {
            return;
        }
        if (pending) {
            pending.abort();
        }
        pending = new AbortController();
        try {
            const response = await fetch(`${search.dataset.endpoint}?q=${encodeURIComponent(value)}`, {
                headers: { Accept: "application/json" },
                signal: pending.signal,
            });
            if (!response.ok) {
                return;
            }
            const payload = await response.json();
            const items = payload.results || [];
            results.innerHTML = items.length
                ? items
                      .slice(0, 6)
                      .map(
                          (item) =>
                              `<a href="${escapeHtml(item.url)}"><span class="result-title">${escapeHtml(item.title)}</span>` +
                              `<span class="result-snippet">${escapeHtml(item.snippet || item.description || "")}</span></a>`
                      )
                      .join("")
                : `<div class="result-empty">No page mentions “${escapeHtml(value)}”.</div>`;
            results.hidden = false;
        } catch (error) {
            if (error.name !== "AbortError") {
                results.hidden = true;
            }
        }
    }

    if (search) {
        search.addEventListener("input", (event) => {
            const value = event.target.value.toLowerCase().trim();
            filterNavigation(value);
            clearTimeout(timer);
            if (value.length < 3) {
                if (results) {
                    results.hidden = true;
                    results.innerHTML = "";
                }
                return;
            }
            timer = setTimeout(() => fullText(value), 180);
        });
        search.addEventListener("keydown", (event) => {
            if (event.key === "Escape") {
                search.value = "";
                search.dispatchEvent(new Event("input"));
            }
        });
    }

    // --- Lightbox ----------------------------------------------------
    const box = document.getElementById("shot-box");
    if (box) {
        const shots = Array.from(document.querySelectorAll("a[data-shot]"));
        const image = box.querySelector("img");
        const caption = box.querySelector("figcaption");
        const counter = document.getElementById("shot-counter");
        let current = 0;
        const show = (index) => {
            if (!shots.length) {
                return;
            }
            current = (index + shots.length) % shots.length;
            const shot = shots[current];
            image.src = shot.getAttribute("href");
            image.alt = shot.dataset.caption || "";
            caption.textContent = shot.dataset.caption || "";
            counter.textContent = `${current + 1} / ${shots.length}`;
        };
        shots.forEach((shot, index) => {
            shot.addEventListener("click", (event) => {
                event.preventDefault();
                show(index);
                box.showModal();
            });
        });
        for (const button of box.querySelectorAll("button[data-nav]")) {
            button.addEventListener("click", () => show(current + Number(button.dataset.nav)));
        }
        box.querySelector("button[data-close]").addEventListener("click", () => box.close());
        box.addEventListener("keydown", (event) => {
            if (event.key === "ArrowLeft") {
                show(current - 1);
            }
            if (event.key === "ArrowRight") {
                show(current + 1);
            }
        });
        box.addEventListener("click", (event) => {
            if (event.target === box) {
                box.close();
            }
        });
    }

    // --- Copy buttons ------------------------------------------------
    for (const button of document.querySelectorAll("button.copy")) {
        button.addEventListener("click", async () => {
            try {
                await navigator.clipboard.writeText(button.dataset.copy);
                button.textContent = "copied";
            } catch (error) {
                button.textContent = "select and copy";
            }
            setTimeout(() => {
                button.textContent = "copy";
            }, 1500);
        });
    }
})();
