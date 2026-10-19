"use strict";
(() => {
  // app/web/static/js/study_spa/_dom.ts
  function el(tag, attrs, ...kids) {
    const n = document.createElement(tag);
    if (attrs) {
      for (const k in attrs) {
        if (!Object.prototype.hasOwnProperty.call(attrs, k)) continue;
        const v = attrs[k];
        if (k === "class") n.className = v;
        else if (k === "text") n.textContent = v;
        else n.setAttribute(k, v);
      }
    }
    for (const c of kids) {
      if (c == null || c === false) continue;
      if (typeof c === "string") n.appendChild(document.createTextNode(c));
      else n.appendChild(c);
    }
    return n;
  }
  function closestEl(target, selector) {
    if (target instanceof Element) return target.closest(selector);
    return null;
  }
  function clear(root) {
    while (root.firstChild) {
      root.removeChild(root.firstChild);
    }
  }
  function scrollAndFocus(root) {
    const bar = document.getElementById("remaining-bar");
    if (bar) {
      const rect = bar.getBoundingClientRect();
      window.scrollTo(0, rect.top + window.pageYOffset);
    }
    const p = root.querySelector(".btn-primary");
    if (p && p.focus) {
      try {
        p.focus({ preventScroll: true });
      } catch (e) {
        p.focus();
      }
    }
  }
  function showError(root, msg) {
    const old = root.querySelector(".error");
    if (old && old.parentNode) old.parentNode.removeChild(old);
    if (!msg) return;
    const p = el("p", { class: "error", text: msg });
    root.insertBefore(p, root.firstChild);
  }
  function showFatal(root, msg) {
    clear(root);
    root.appendChild(el("p", { class: "error", text: msg }));
  }

  // app/web/static/js/study_spa/_api.ts
  function createApi(ctx) {
    return function api(method, path, formData = null) {
      const ctrl = new AbortController();
      if (ctx.inflight) {
        try {
          ctx.inflight.abort();
        } catch (e) {
        }
      }
      ctx.inflight = ctrl;
      const opts = {
        method,
        signal: ctrl.signal,
        headers: {
          "X-CSRF-Token": ctx.cfg.csrfToken || "",
          "X-Requested-With": "study-spa"
        },
        credentials: "same-origin"
      };
      if (formData) opts.body = formData;
      return fetch(path, opts).then((r) => {
        if (r.status === 401) {
          location.assign("/login");
          throw { message: "unauth" };
        }
        if (r.status === 403) {
          showFatal(ctx.root, "Session expired \u2014 reloading\u2026");
          setTimeout(() => {
            location.reload();
          }, 1500);
          throw { message: "csrf" };
        }
        if (r.status === 404) {
          location.assign("/deck/" + ctx.cfg.deckId + "/study");
          throw { message: "spa-disabled" };
        }
        if (!r.ok) {
          return r.json().catch(() => ({})).then((body) => {
            throw {
              message: body && body.error_msg ? body.error_msg : "HTTP " + r.status,
              code: r.status,
              body
            };
          });
        }
        return r.json();
      });
    };
  }
  function errorMessage(e) {
    return "body" in e && e.body && e.body.error_msg ? e.body.error_msg : null;
  }

  // app/web/static/js/study_spa/_state.ts
  function createCtx(root, cfg) {
    return {
      root,
      cfg,
      base: "/deck/" + cfg.deckId + "/spa",
      state: null,
      busy: false,
      inflight: null,
      confirmCard: null,
      uiMode: "study"
    };
  }

  // app/web/static/js/study_spa/_render.ts
  function createRender(ctx) {
    function updateSyncIndicator(syncRequired) {
      const ind = document.getElementById("sync-indicator");
      if (!ind) return;
      ind.textContent = syncRequired ? " \u25CF" : "";
    }
    function renderBar(p) {
      if (!p.card) return el("div");
      const card = p.card;
      const cur = p.card_type;
      function chipClass(kind) {
        if (kind === cur) return "chip chip-current";
        const n = kind === "learning" ? p.remaining_learn : kind === "review" ? p.remaining_review : p.remaining_new;
        return n === 0 ? "chip chip-empty" : "chip";
      }
      function flagBtn(action, value, label, aria) {
        const active = action === "mark" ? !!card.is_marked : card.flag === value;
        return el(
          "button",
          {
            type: "button",
            class: "flag-btn" + (active ? " flag-btn-on" : ""),
            "data-action": action,
            "data-flag": String(value),
            "aria-label": aria
          },
          label
        );
      }
      const summary = el(
        "div",
        { class: "summary" },
        el(
          "div",
          { class: "summary-total" },
          String(p.remaining_total),
          el("span", { class: "summary-total-label", text: "remaining" })
        ),
        el(
          "div",
          { class: "breakdown" },
          el("span", { class: chipClass("new"), text: "new " + p.remaining_new }),
          el("span", { class: chipClass("learning"), text: "learn " + p.remaining_learn }),
          el("span", { class: chipClass("review"), text: "review " + p.remaining_review })
        )
      );
      const toolbar = el(
        "div",
        { class: "flag-toolbar" },
        el(
          "div",
          { class: "flag-buttons" },
          flagBtn("mark", 0, card.is_marked ? "\u2605" : "\u2606", card.is_marked ? "Unmark" : "Mark"),
          flagBtn("flag", 1, "R", "Red"),
          flagBtn("flag", 2, "O", "Orange"),
          flagBtn("flag", 3, "G", "Green")
        )
      );
      return el("div", { class: "remaining-bar", id: "remaining-bar" }, summary, toolbar);
    }
    function renderCard(p) {
      if (!p.card) return el("div");
      const article = el("article", { class: "card", id: "card-host" });
      article.innerHTML = p.phase === "back" ? p.card.answer_html : p.card.question_html;
      return article;
    }
    function renderPrimary(p) {
      if (p.phase === "front") {
        return el(
          "div",
          { class: "actions-split" },
          el("a", { class: "btn", href: "/" }, "Back to decks"),
          el(
            "button",
            {
              type: "button",
              class: "btn btn-primary",
              "data-action": "show-answer"
            },
            "Show answer"
          )
        );
      }
      if (p.phase === "back") {
        if (!p.card) return el("div");
        const card = p.card;
        const ratings = el("div", { class: "ratings" });
        const labels = ["Again", "Hard", "Good", "Easy"];
        const keys = ["again", "hard", "good", "easy"];
        for (let i = 0; i < 4; i++) {
          const rate = i + 1;
          const interval = card.intervals && card.intervals[keys[i]] || "\u2014";
          ratings.appendChild(
            el(
              "button",
              {
                type: "submit",
                name: "rate",
                class: "btn" + (rate === 3 ? " btn-primary" : ""),
                "data-action": "answer",
                "data-rate": String(rate)
              },
              labels[i],
              el("span", { class: "rating-interval", text: " (" + interval + ")" })
            )
          );
        }
        return el("form", { method: "post", class: "form", "data-spa-form": "answer" }, ratings);
      }
      return el("div");
    }
    function renderSecondary(p) {
      const row = el("div", { class: "actions-row" });
      if (p.undo && p.undo.can_undo) {
        const label = p.undo.label ? "Undo " + p.undo.label.toLowerCase() : "Undo";
        row.appendChild(
          el(
            "button",
            {
              type: "button",
              class: "btn",
              "data-action": "undo",
              "aria-label": "Undo"
            },
            label
          )
        );
      }
      if (p.phase === "back") {
        row.appendChild(
          el(
            "button",
            {
              type: "button",
              class: "btn",
              "data-action": "delete"
            },
            "Delete note"
          )
        );
      }
      if (p.is_filtered) {
        row.appendChild(
          el(
            "button",
            {
              type: "button",
              class: "btn",
              "data-action": "rebuild"
            },
            "Rebuild deck"
          )
        );
        if (p.has_cards) {
          row.appendChild(
            el(
              "button",
              {
                type: "button",
                class: "btn",
                "data-action": "empty"
              },
              "Empty deck"
            )
          );
        }
      }
      return row;
    }
    function renderDone(p) {
      const wrap = el("div");
      wrap.appendChild(el("h1", { text: "Session complete" }));
      wrap.appendChild(el("p", { text: "No more cards due in this deck right now." }));
      wrap.appendChild(el("p", null, el("a", { class: "btn", href: "/" }, "Back to decks")));
      if (p.is_filtered) {
        const row = el("div", { class: "actions-row" });
        if (p.undo && p.undo.can_undo) {
          const label = p.undo.label ? "Undo " + p.undo.label.toLowerCase() : "Undo";
          row.appendChild(
            el(
              "button",
              {
                type: "button",
                class: "btn",
                "data-action": "undo",
                "aria-label": "Undo"
              },
              label
            )
          );
        }
        row.appendChild(
          el(
            "button",
            {
              type: "button",
              class: "btn",
              "data-action": "rebuild"
            },
            "Rebuild deck"
          )
        );
        if (p.has_cards) {
          row.appendChild(
            el(
              "button",
              {
                type: "button",
                class: "btn",
                "data-action": "empty"
              },
              "Empty deck"
            )
          );
        }
        wrap.appendChild(row);
      }
      return wrap;
    }
    function setMarkOptimistic(isMarked) {
      const btn = ctx.root.querySelector('[data-action="mark"]');
      if (!btn) return;
      if (isMarked) {
        btn.className = btn.className.replace(/\s*flag-btn-on/, "") + " flag-btn-on";
      } else {
        btn.className = btn.className.replace(/\s*flag-btn-on/, "");
      }
      btn.textContent = isMarked ? "\u2605" : "\u2606";
      btn.setAttribute("aria-label", isMarked ? "Unmark" : "Mark");
    }
    function setFlagOptimistic(flag) {
      const btns = ctx.root.querySelectorAll('[data-action="flag"]');
      for (let i = 0; i < btns.length; i++) {
        const b = btns[i];
        const v = Number(b.getAttribute("data-flag"));
        if (v === flag) {
          if (b.className.indexOf("flag-btn-on") === -1) b.className += " flag-btn-on";
        } else {
          b.className = b.className.replace(/\s*flag-btn-on/, "");
        }
      }
    }
    function renderDeleteConfirm(_p) {
      const card = ctx.confirmCard;
      if (!card) {
        return el("div");
      }
      const preview = el("article", { class: "card" });
      preview.innerHTML = card.question_html;
      return el(
        "div",
        null,
        el("h1", { text: "Delete this note?" }),
        el("p", {
          class: "muted",
          text: "This will permanently remove the note and all its cards. You can undo this with the Undo button afterwards."
        }),
        preview,
        el(
          "div",
          { class: "actions-row" },
          el(
            "button",
            {
              type: "button",
              class: "btn",
              "data-action": "cancel-delete"
            },
            "Cancel"
          ),
          el(
            "button",
            {
              type: "button",
              class: "btn btn-primary",
              "data-action": "confirm-delete"
            },
            "Confirm"
          )
        )
      );
    }
    function render(p) {
      ctx.state = p;
      clear(ctx.root);
      if (p.error_msg) {
        ctx.root.appendChild(el("p", { class: "error", text: p.error_msg }));
      }
      if (ctx.uiMode === "delete-confirm") {
        ctx.root.appendChild(renderDeleteConfirm(p));
      } else if (p.phase === "done") {
        ctx.root.appendChild(renderDone(p));
      } else {
        ctx.root.appendChild(renderBar(p));
        ctx.root.appendChild(renderCard(p));
        ctx.root.appendChild(renderPrimary(p));
        ctx.root.appendChild(renderSecondary(p));
      }
      if (history.replaceState) {
        try {
          history.replaceState(null, "", ctx.base);
        } catch (e) {
        }
      }
      updateSyncIndicator(p.sync_required);
      scrollAndFocus(ctx.root);
    }
    return {
      render,
      updateSyncIndicator,
      setMarkOptimistic,
      setFlagOptimistic
    };
  }

  // app/web/static/js/study_spa/_actions.ts
  function createActions(ctx, api, render, setMarkOptimistic, setFlagOptimistic, showErrorFn = (m) => showError(ctx.root, m), errorMessageFn = errorMessage) {
    function withBusy(fn) {
      if (ctx.busy) return Promise.resolve();
      ctx.busy = true;
      return Promise.resolve().then(fn).then(
        () => {
          ctx.busy = false;
        },
        (e) => {
          ctx.busy = false;
          throw e;
        }
      );
    }
    function onReveal() {
      if (!ctx.state || !ctx.state.card) return Promise.resolve();
      const card = ctx.state.card;
      const body = new FormData();
      body.append("card_id", String(card.card_id));
      return api("POST", ctx.base + "/reveal", body).then(render);
    }
    function onAnswer(rate) {
      if (!ctx.state || !ctx.state.card) return Promise.resolve();
      const card = ctx.state.card;
      const body = new FormData();
      body.append("card_id", String(card.card_id));
      body.append("rate", String(rate));
      return api("POST", ctx.base + "/answer", body).then(render);
    }
    function onUndo() {
      return api("POST", ctx.base + "/undo", null).then(render);
    }
    function onMark() {
      if (!ctx.state || !ctx.state.card) return Promise.resolve();
      const card = ctx.state.card;
      const target = !card.is_marked;
      setMarkOptimistic(target);
      const body = new FormData();
      body.append("card_id", String(card.card_id));
      body.append("marked", target ? "true" : "false");
      return api("POST", ctx.base + "/mark", body).then(
        (r) => {
          card.is_marked = r.is_marked;
          setMarkOptimistic(r.is_marked);
        },
        () => {
          setMarkOptimistic(!target);
        }
      );
    }
    function onFlag(clickedFlag) {
      if (!ctx.state || !ctx.state.card) return Promise.resolve();
      const card = ctx.state.card;
      const prev = card.flag;
      const target = prev === clickedFlag ? 0 : clickedFlag;
      setFlagOptimistic(target);
      const body = new FormData();
      body.append("card_id", String(card.card_id));
      body.append("flag", String(target));
      return api("POST", ctx.base + "/flag", body).then(
        (r) => {
          card.flag = r.flag;
          setFlagOptimistic(r.flag);
        },
        () => {
          setFlagOptimistic(prev);
        }
      );
    }
    function showDeleteConfirm(card) {
      ctx.confirmCard = card;
      ctx.uiMode = "delete-confirm";
      if (ctx.state) render(ctx.state);
    }
    function onCancelDelete() {
      ctx.uiMode = "study";
      ctx.confirmCard = null;
      if (ctx.state) render(ctx.state);
    }
    function onDeleteConfirm() {
      if (!ctx.confirmCard) return Promise.resolve();
      const cardId = ctx.confirmCard.card_id;
      ctx.uiMode = "study";
      ctx.confirmCard = null;
      const body = new FormData();
      body.append("card_id", String(cardId));
      return api("POST", ctx.base + "/delete", body).then(render, (e) => {
        const msg = errorMessageFn(e);
        if (msg) showErrorFn(msg);
      });
    }
    function onRebuild() {
      return api("POST", ctx.base + "/rebuild", null).then(
        (r) => {
          location.assign("/?rebuild_ok=" + r.count);
        },
        (e) => {
          const msg = errorMessageFn(e);
          showErrorFn(msg || "rebuild failed");
        }
      );
    }
    function onEmpty() {
      return api("POST", ctx.base + "/empty", null).then(
        (r) => {
          location.assign("/?empty_ok=" + r.count);
        },
        (e) => {
          const msg = errorMessageFn(e);
          showErrorFn(msg || "empty failed");
        }
      );
    }
    return {
      withBusy,
      onReveal,
      onAnswer,
      onUndo,
      onMark,
      onFlag,
      showDeleteConfirm,
      onCancelDelete,
      onDeleteConfirm,
      onRebuild,
      onEmpty
    };
  }

  // app/web/static/js/study_spa/_events.ts
  function attachEvents(ctx, actions) {
    ctx.root.addEventListener("click", function(ev) {
      const target = ev.target;
      const form = closestEl(target, 'form[data-spa-form="answer"]');
      if (form) {
        const btn = closestEl(target, 'button[data-action="answer"]');
        if (btn) {
          ev.preventDefault();
          const rate = Number(btn.getAttribute("data-rate"));
          actions.withBusy(() => actions.onAnswer(rate));
          return;
        }
      }
      const btn2 = closestEl(target, "[data-action]");
      if (!btn2) return;
      const a = btn2.getAttribute("data-action");
      if (a === "cancel-delete") {
        if (ctx.busy) return;
        actions.withBusy(actions.onCancelDelete);
        return;
      }
      if (a === "confirm-delete") {
        if (ctx.busy) return;
        actions.withBusy(actions.onDeleteConfirm);
        return;
      }
      if (ctx.busy) return;
      actions.withBusy(() => {
        switch (a) {
          case "show-answer":
            return actions.onReveal();
          case "undo":
            return actions.onUndo();
          case "mark":
            return actions.onMark();
          case "flag":
            return actions.onFlag(Number(btn2.getAttribute("data-flag")));
          case "delete":
            if (ctx.state && ctx.state.card) actions.showDeleteConfirm(ctx.state.card);
            return;
          case "rebuild":
            return actions.onRebuild();
          case "empty":
            return actions.onEmpty();
        }
        return;
      });
    });
  }

  // app/web/static/js/study_spa/index.ts
  (function() {
    const configEl = document.getElementById("spa-config");
    if (!configEl) return;
    let cfg;
    try {
      cfg = JSON.parse(configEl.textContent || "null");
    } catch (e) {
      return;
    }
    if (!cfg) return;
    const root = document.getElementById("study-root");
    if (!root) return;
    const ctx = createCtx(root, cfg);
    const api = createApi(ctx);
    const renderApi = createRender(ctx);
    const actions = createActions(
      ctx,
      api,
      renderApi.render,
      renderApi.setMarkOptimistic,
      renderApi.setFlagOptimistic
    );
    attachEvents(ctx, actions);
    api("GET", ctx.base + "/state", null).then(renderApi.render, (e) => {
      if (e && (e.message === "unauth" || e.message === "csrf" || e.message === "spa-disabled")) return;
      showFatal(root, "Failed to load. Please refresh.");
    });
  })();
})();
