// Study SPA client. Vanilla ES6 (Kindle WebKit 1.x compatible).
// Single root container is re-rendered on every state change so old
// DOM nodes can be garbage-collected.

(function () {
  var cfg = window.__STUDY_SPA__;
  if (!cfg) return;
  var root = document.getElementById('study-root');
  if (!root) return;
  var base = '/deck/' + cfg.deckId + '/spa';

  var state = null;
  var busy = false;
  var inflight = null;
  var confirmCard = null;

  // ---------- helpers ----------

  function api(method, path, formData) {
    var ctrl = new AbortController();
    if (inflight) {
      try { inflight.abort(); } catch (e) { /* ignore */ }
    }
    inflight = ctrl;
    var opts = {
      method: method,
      signal: ctrl.signal,
      headers: {
        'X-CSRF-Token': cfg.csrfToken || '',
        'X-Requested-With': 'study-spa'
      },
      credentials: 'same-origin'
    };
    if (formData) opts.body = formData;
    return fetch(path, opts).then(function (r) {
      if (r.status === 401) {
        location.assign('/login');
        throw { message: 'unauth' };
      }
      if (r.status === 403) {
        showFatal('Session expired — reloading…');
        setTimeout(function () { location.reload(); }, 1500);
        throw { message: 'csrf' };
      }
      if (r.status === 404) {
        location.assign('/deck/' + cfg.deckId + '/study');
        throw { message: 'spa-disabled' };
      }
      if (!r.ok) {
        return r.json().catch(function () { return {}; }).then(function (body) {
          throw { message: body && body.error_msg ? body.error_msg : ('HTTP ' + r.status), code: r.status, body: body };
        });
      }
      return r.json();
    });
  }

  function el(tag, attrs) {
    var n = document.createElement(tag);
    if (attrs) {
      for (var k in attrs) {
        if (!Object.prototype.hasOwnProperty.call(attrs, k)) continue;
        var v = attrs[k];
        if (k === 'class') n.className = v;
        else if (k === 'text') n.textContent = v;
        else n.setAttribute(k, v);
      }
    }
    for (var i = 2; i < arguments.length; i++) {
      var c = arguments[i];
      if (c == null || c === false) continue;
      if (c.nodeType) n.appendChild(c);
      else n.appendChild(document.createTextNode(String(c)));
    }
    return n;
  }

  function clear() {
    while (root.firstChild) {
      root.removeChild(root.firstChild);
    }
  }

  function scrollAndFocus() {
    var bar = document.getElementById('remaining-bar');
    if (bar) {
      var rect = bar.getBoundingClientRect();
      window.scrollTo(0, rect.top + window.pageYOffset);
    }
    var p = root.querySelector('.btn-primary');
    if (p && p.focus) {
      try { p.focus({ preventScroll: true }); } catch (e) { p.focus(); }
    }
  }

  function showError(msg) {
    var old = root.querySelector('.error');
    if (old && old.parentNode) old.parentNode.removeChild(old);
    if (!msg) return;
    var p = el('p', { class: 'error', text: msg });
    root.insertBefore(p, root.firstChild);
  }

  function showFatal(msg) {
    clear();
    root.appendChild(el('p', { class: 'error', text: msg }));
  }

  // ---------- render ----------

  function render(p) {
    state = p;
    clear();
    if (p.error_msg) {
      root.appendChild(el('p', { class: 'error', text: p.error_msg }));
    }
    if (p.phase === 'done') {
      root.appendChild(renderDone(p));
    } else {
      root.appendChild(renderBar(p));
      root.appendChild(renderCard(p));
      root.appendChild(renderPrimary(p));
      root.appendChild(renderSecondary(p));
    }
    if (history.replaceState) {
      try { history.replaceState(null, '', base); } catch (e) { /* ignore */ }
    }
    scrollAndFocus();
  }

  function renderBar(p) {
    var card = p.card;
    var cur = p.card_type;

    function chipClass(kind) {
      if (kind === cur) return 'chip chip-current';
      var n = kind === 'learning' ? p.remaining_learn
            : kind === 'review'   ? p.remaining_review
            : p.remaining_new;
      return n === 0 ? 'chip chip-empty' : 'chip';
    }

    var summary = el('div', { class: 'summary' },
      el('div', { class: 'summary-total' },
        String(p.remaining_total),
        el('span', { class: 'summary-total-label', text: 'remaining' })
      ),
      el('div', { class: 'breakdown' },
        el('span', { class: chipClass('new'),      text: 'new ' + p.remaining_new }),
        el('span', { class: chipClass('learning'), text: 'learn ' + p.remaining_learn }),
        el('span', { class: chipClass('review'),   text: 'review ' + p.remaining_review })
      )
    );

    function flagBtn(action, value, label, aria) {
      var active = action === 'mark'
        ? !!card.is_marked
        : (card.flag === value);
      return el('button', {
        type: 'button',
        class: 'flag-btn' + (active ? ' flag-btn-on' : ''),
        'data-action': action,
        'data-flag': String(value),
        'aria-label': aria
      }, label);
    }

    var toolbar = el('div', { class: 'flag-toolbar' },
      el('div', { class: 'flag-buttons' },
        flagBtn('mark', 0, card.is_marked ? '\u2605' : '\u2606', card.is_marked ? 'Unmark' : 'Mark'),
        flagBtn('flag', 1, 'R', 'Red'),
        flagBtn('flag', 2, 'O', 'Orange'),
        flagBtn('flag', 3, 'G', 'Green')
      )
    );

    return el('div', { class: 'remaining-bar', id: 'remaining-bar' }, summary, toolbar);
  }

  function renderCard(p) {
    var article = el('article', { class: 'card', id: 'card-host' });
    // card.*_html comes sanitized server-side (scripts stripped).
    article.innerHTML = p.phase === 'back' ? p.card.answer_html : p.card.question_html;
    return article;
  }

  function renderPrimary(p) {
    if (p.phase === 'front') {
      return el('div', { class: 'actions-split' },
        el('a', { class: 'btn', href: '/' }, 'Back to decks'),
        el('button', {
          type: 'button',
          class: 'btn btn-primary',
          'data-action': 'show-answer'
        }, 'Show answer')
      );
    }
    if (p.phase === 'back') {
      var ratings = el('div', { class: 'ratings' });
      var labels = ['Again', 'Hard', 'Good', 'Easy'];
      var keys   = ['again', 'hard', 'good', 'easy'];
      var card   = p.card;
      for (var i = 0; i < 4; i++) {
        var rate = i + 1;
        var interval = (card.intervals && card.intervals[keys[i]]) || '\u2014';
        ratings.appendChild(el('button', {
          type: 'submit',
          name: 'rate',
          class: 'btn' + (rate === 3 ? ' btn-primary' : ''),
          'data-action': 'answer',
          'data-rate': String(rate)
        },
          labels[i],
          el('span', { class: 'rating-interval', text: ' (' + interval + ')' })
        ));
      }
      return el('form', { method: 'post', class: 'form', 'data-spa-form': 'answer' }, ratings);
    }
    return el('div');
  }

  function renderSecondary(p) {
    var row = el('div', { class: 'actions-row' });
    if (p.undo && p.undo.can_undo) {
      var label = p.undo.label ? 'Undo ' + p.undo.label.toLowerCase() : 'Undo';
      row.appendChild(el('button', {
        type: 'button',
        class: 'btn',
        'data-action': 'undo',
        'aria-label': 'Undo'
      }, label));
    }
    if (p.phase === 'back') {
      row.appendChild(el('button', {
        type: 'button',
        class: 'btn',
        'data-action': 'delete'
      }, 'Delete note'));
    }
    if (p.is_filtered) {
      row.appendChild(el('button', {
        type: 'button',
        class: 'btn',
        'data-action': 'rebuild'
      }, 'Rebuild deck'));
      if (p.has_cards) {
        row.appendChild(el('button', {
          type: 'button',
          class: 'btn',
          'data-action': 'empty'
        }, 'Empty deck'));
      }
    }
    return row;
  }

  function renderDone(p) {
    var wrap = el('div');
    wrap.appendChild(el('h1', { text: 'Session complete' }));
    wrap.appendChild(el('p', { text: 'No more cards due in this deck right now.' }));
    wrap.appendChild(el('p', null,
      el('a', { class: 'btn', href: '/' }, 'Back to decks')
    ));
    if (p.is_filtered) {
      var row = el('div', { class: 'actions-row' });
      if (p.undo && p.undo.can_undo) {
        var label = p.undo.label ? 'Undo ' + p.undo.label.toLowerCase() : 'Undo';
        row.appendChild(el('button', {
          type: 'button',
          class: 'btn',
          'data-action': 'undo',
          'aria-label': 'Undo'
        }, label));
      }
      row.appendChild(el('button', {
        type: 'button',
        class: 'btn',
        'data-action': 'rebuild'
      }, 'Rebuild deck'));
      if (p.has_cards) {
        row.appendChild(el('button', {
          type: 'button',
          class: 'btn',
          'data-action': 'empty'
        }, 'Empty deck'));
      }
      wrap.appendChild(row);
    }
    return wrap;
  }

  // ---------- optimistic mark/flag ----------

  function setMarkOptimistic(isMarked) {
    var btn = root.querySelector('[data-action="mark"]');
    if (!btn) return;
    if (isMarked) {
      btn.className = btn.className.replace(/\s*flag-btn-on/, '') + ' flag-btn-on';
    } else {
      btn.className = btn.className.replace(/\s*flag-btn-on/, '');
    }
    btn.textContent = isMarked ? '\u2605' : '\u2606';
    btn.setAttribute('aria-label', isMarked ? 'Unmark' : 'Mark');
  }

  function setFlagOptimistic(flag) {
    var btns = root.querySelectorAll('[data-action="flag"]');
    for (var i = 0; i < btns.length; i++) {
      var b = btns[i];
      var v = Number(b.getAttribute('data-flag'));
      if (v === flag) {
        if (b.className.indexOf('flag-btn-on') === -1) b.className += ' flag-btn-on';
      } else {
        b.className = b.className.replace(/\s*flag-btn-on/, '');
      }
    }
  }

  // ---------- delete confirm modal ----------

  function showDeleteConfirm(card) {
    hideDeleteConfirm();
    confirmCard = card;
    var preview = el('div', { class: 'card-preview' });
    preview.innerHTML = card.question_html;
    var dlg = el('div', { class: 'modal' },
      el('h1', { text: 'Delete this note?' }),
      el('p', { class: 'muted', text: 'This will permanently remove the note and all its cards. You can undo this with the Undo button afterwards.' }),
      preview,
      el('div', { class: 'actions-row' },
        el('button', {
          type: 'button',
          class: 'btn',
          'data-action': 'cancel-delete'
        }, 'Cancel'),
        el('button', {
          type: 'button',
          class: 'btn btn-primary',
          'data-action': 'confirm-delete'
        }, 'Confirm')
      )
    );
    var backdrop = el('div', { class: 'modal-backdrop', id: 'delete-modal' }, dlg);
    document.body.appendChild(backdrop);
  }

  function hideDeleteConfirm() {
    var m = document.getElementById('delete-modal');
    if (m && m.parentNode) m.parentNode.removeChild(m);
    confirmCard = null;
  }

  // ---------- actions ----------

  function withBusy(fn) {
    if (busy) return Promise.resolve();
    busy = true;
    return Promise.resolve().then(fn).then(function (v) { busy = false; return v; }, function (e) { busy = false; throw e; });
  }

  function onReveal() {
    if (!state || !state.card) return Promise.resolve();
    var body = new FormData();
    body.append('card_id', String(state.card.card_id));
    return api('POST', base + '/reveal', body).then(render);
  }

  function onAnswer(rate) {
    if (!state || !state.card) return Promise.resolve();
    var body = new FormData();
    body.append('card_id', String(state.card.card_id));
    body.append('rate', String(rate));
    return api('POST', base + '/answer', body).then(render);
  }

  function onUndo() {
    return api('POST', base + '/undo').then(render);
  }

  function onMark() {
    if (!state || !state.card) return Promise.resolve();
    var target = !state.card.is_marked;
    setMarkOptimistic(target);
    var body = new FormData();
    body.append('card_id', String(state.card.card_id));
    body.append('marked', target ? 'true' : 'false');
    return api('POST', base + '/mark', body).then(function (r) {
      state.card.is_marked = r.is_marked;
      setMarkOptimistic(r.is_marked);
    }, function () {
      setMarkOptimistic(!target);
    });
  }

  function onFlag(targetFlag) {
    if (!state || !state.card) return Promise.resolve();
    var prev = state.card.flag;
    setFlagOptimistic(targetFlag);
    var body = new FormData();
    body.append('card_id', String(state.card.card_id));
    body.append('flag', String(targetFlag));
    return api('POST', base + '/flag', body).then(function (r) {
      state.card.flag = r.flag;
      setFlagOptimistic(r.flag);
    }, function () {
      setFlagOptimistic(prev);
    });
  }

  function onDeleteConfirm() {
    if (!confirmCard) return Promise.resolve();
    var cardId = confirmCard.card_id;
    hideDeleteConfirm();
    var body = new FormData();
    body.append('card_id', String(cardId));
    return api('POST', base + '/delete', body).then(render, function (e) {
      if (e && e.body && e.body.error_msg) showError(e.body.error_msg);
    });
  }

  function onRebuild() {
    return api('POST', base + '/rebuild').then(function (r) {
      location.assign('/?rebuild_ok=' + r.count);
    }, function (e) {
      showError((e && e.body && e.body.error_msg) || 'rebuild failed');
    });
  }

  function onEmpty() {
    return api('POST', base + '/empty').then(function (r) {
      location.assign('/?empty_ok=' + r.count);
    }, function (e) {
      showError((e && e.body && e.body.error_msg) || 'empty failed');
    });
  }

  // ---------- delegated events ----------

  root.addEventListener('click', function (ev) {
    var target = ev.target;
    // For <form data-spa-form="answer">: pick the rate from the clicked button.
    var form = target.closest && target.closest('form[data-spa-form="answer"]');
    if (form) {
      var btn = target.closest && target.closest('button[data-action="answer"]');
      if (btn) {
        ev.preventDefault();
        withBusy(function () { return onAnswer(Number(btn.getAttribute('data-rate'))); });
        return;
      }
    }
    var btn2 = target.closest && target.closest('[data-action]');
    if (!btn2) return;
    var a = btn2.getAttribute('data-action');
    if (a === 'cancel-delete' || a === 'confirm-delete') {
      if (busy) return;
      withBusy(function () {
        if (a === 'cancel-delete') { hideDeleteConfirm(); return null; }
        return onDeleteConfirm();
      });
      return;
    }
    if (busy) return;
    withBusy(function () {
      switch (a) {
        case 'show-answer': return onReveal();
        case 'undo':        return onUndo();
        case 'mark':        return onMark();
        case 'flag':        return onFlag(Number(btn2.getAttribute('data-flag')));
        case 'delete':      if (state && state.card) showDeleteConfirm(state.card); return null;
        case 'rebuild':     return onRebuild();
        case 'empty':       return onEmpty();
      }
      return null;
    });
  });

  // ---------- boot ----------

  api('GET', base + '/state').then(render, function (e) {
    if (e && (e.message === 'unauth' || e.message === 'csrf' || e.message === 'spa-disabled')) return;
    showFatal('Failed to load. Please refresh.');
  });
})();
