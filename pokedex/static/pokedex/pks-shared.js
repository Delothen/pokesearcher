// Helpers shared by the Pokémon page and the comparison page.
(function() {
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function(c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function cap(s) {
    s = String(s || '');
    return s.charAt(0).toUpperCase() + s.slice(1);
  }

  // ---- Type / category / power / accuracy columns of a moves table ----
  // The table rows are drawn first; the four extra cells fill in once the
  // server has looked the moves up (only for the rows being shown).
  var meta = {};        // move name -> {type, category, power, accuracy, priority}
  var asked = {};       // move name -> true once requested
  var CHUNK = 40;

  // Priority bracket as players write it: +1, 0, -6.
  function fmtPriority(p) {
    p = Number(p) || 0;
    return p > 0 ? '+' + p : String(p);
  }

  function paint(table) {
    table.querySelectorAll('tbody .move-row').forEach(function(row) {
      var m = meta[row.dataset.name];
      if (!m || row.dataset.painted) return;
      row.dataset.painted = '1';
      var set = function(cls, html) {
        var td = row.querySelector('.' + cls);
        if (td) td.innerHTML = html;
      };
      set('mm-type', m.type
        ? '<span class="type-badge type-' + esc(m.type) + '">' +
          esc(cap(m.type)) + '</span>' : '—');
      set('mm-cat', m.category
        ? '<span class="mm-cat-' + esc(m.category) + '">' +
          esc(cap(m.category)) + '</span>' : '—');
      set('mm-power', m.power == null ? '—' : esc(m.power));
      set('mm-acc', m.accuracy == null ? '—' : esc(m.accuracy));
      set('mm-pri', m.priority == null ? '—' : esc(fmtPriority(m.priority)));
    });
  }

  function fill(table) {
    if (!table) return;
    paint(table);
    var url = table.dataset.metaUrl;
    if (!url) return;
    var names = [];
    table.querySelectorAll('tbody .move-row').forEach(function(row) {
      var n = row.dataset.name;
      if (row.style.display === 'none' || meta[n] || asked[n]) return;
      if (names.indexOf(n) === -1) names.push(n);
    });
    for (var i = 0; i < names.length; i += CHUNK) {
      (function(chunk) {
        chunk.forEach(function(n) { asked[n] = true; });
        fetch(url + '?names=' + encodeURIComponent(chunk.join(',')))
          .then(function(r) { return r.json(); })
          .then(function(d) {
            var got = d.moves || {};
            chunk.forEach(function(n) {
              if (got[n]) meta[n] = got[n]; else delete asked[n];
            });
            // every table on the page may be waiting for these moves
            document.querySelectorAll('.moves-table[data-meta-url]')
              .forEach(paint);
          })
          .catch(function() {
            chunk.forEach(function(n) { delete asked[n]; });
          });
      })(names.slice(i, i + CHUNK));
    }
  }

  window.pksMoveMeta = {
    fill: fill,
    // the five empty cells a row needs (JS-built rows)
    cells: '<td class="mm-type"></td><td class="mm-cat"></td>' +
           '<td class="mm-power"></td><td class="mm-acc"></td>' +
           '<td class="mm-pri"></td>',
    fmtPriority: fmtPriority
  };

  // ---- Type chart: damage taken from each attacking type ----
  function multLabel(m) {
    if (m === 0) return '0';
    if (m === 0.25) return '¼';
    if (m === 0.5) return '½';
    return String(m);
  }
  window.pksTypeChart = {
    render: function(grid, types, chart) {
      if (!grid) return;
      // each type counts once: a repeated type must never double the damage
      var seen = {};
      var uniq = (types || []).map(function(t) {
        return String(t || '').toLowerCase();
      }).filter(function(t) {
        if (!t || seen[t]) return false;
        seen[t] = true;
        return true;
      });
      grid.innerHTML = Object.keys(chart || {}).map(function(atk) {
        var m = 1;
        uniq.forEach(function(t) {
          var v = chart[atk][t];
          if (v !== undefined) m *= v;
        });
        var cls = m === 1 ? 'tc-1' : (m === 0 ? 'tc-0' : (m > 1
          ? (m >= 4 ? 'tc-x4' : 'tc-x2') : (m <= 0.25 ? 'tc-q' : 'tc-h')));
        return '<div class="tc-cell"><span class="type-badge type-' + esc(atk) +
          '">' + esc(cap(atk)) + '</span><b class="tc-mult ' +
          cls + '">' + multLabel(m) + '×</b></div>';
      }).join('');
    }
  };
})();

// Remembers which form (Mimikyu's Busted form, Minior's colours...) and which
// game a page is on, in the URL's #hash, so a refresh opens on them instead
// of the first form / newest game. The hash never goes to the server and a
// new search drops it. Keys: form, game (form-a / game-a... on Compare).
// Every game, newest first (same order as api.VERSION_GROUPS).
window.pksGameOrder = [
  'champions', 'legends-za', 'scarlet-violet', 'legends-arceus',
  'brilliant-diamond-shining-pearl', 'sword-shield',
  'lets-go-pikachu-lets-go-eevee', 'ultra-sun-ultra-moon', 'sun-moon',
  'omega-ruby-alpha-sapphire', 'x-y', 'black-2-white-2', 'black-white',
  'heartgold-soulsilver', 'platinum', 'diamond-pearl', 'xd', 'emerald',
  'firered-leafgreen', 'colosseum', 'ruby-sapphire', 'crystal',
  'gold-silver', 'yellow', 'red-blue'
];

// Which game button each side of a comparison (move, ability, item) opens on.
// Each side keeps the game it was on before a refresh. Otherwise both start on
// the newest game they have in common; if they share none, each starts on the
// newest game it is in itself.
window.pksPickGames = function(btnsA, btnsB) {
  var M = window.pksPageMemory;
  var order = window.pksGameOrder;
  function newestFirst(btns) {
    var list = Array.prototype.slice.call(btns);
    function rank(b) {
      var i = order.indexOf(b.dataset.vg);
      return i === -1 ? order.length : i;
    }
    return list
      .map(function(b, i) { return { b: b, r: rank(b), i: i }; })
      .sort(function(x, y) { return x.r - y.r || x.i - y.i; })
      .map(function(x) { return x.b; });
  }
  function find(list, vg) {
    for (var i = 0; vg && i < list.length; i++) {
      if (list[i].dataset.vg === vg) return list[i];
    }
    return null;
  }
  var a = newestFirst(btnsA), b = newestFirst(btnsB);
  var pickA = find(a, M.get('game-a'));
  var pickB = find(b, M.get('game-b'));
  // Both sides kept the game they were on (a refresh): leave them alone.
  if (pickA && pickB) return { a: pickA, b: pickB };
  // A side with a new thing in it (its remembered game is not one of its
  // games): use the other side's game when the new one is in it too ...
  if (pickA && find(b, pickA.dataset.vg)) {
    return { a: pickA, b: find(b, pickA.dataset.vg) };
  }
  if (pickB && find(a, pickB.dataset.vg)) {
    return { a: find(a, pickB.dataset.vg), b: pickB };
  }
  // ... otherwise the newest game both are in, switching the other side's
  // game button too ...
  for (var i = 0; i < a.length; i++) {
    var match = find(b, a[i].dataset.vg);
    if (match) return { a: a[i], b: match };
  }
  // ... and with no game in common, each side on its own newest game.
  return { a: pickA || a[0] || null, b: pickB || b[0] || null };
};

window.pksPageMemory = {
  // The game button saved under `key`, else the first (newest) one.
  pick: function(btns, key) {
    var v = this.get(key);
    for (var i = 0; v && i < btns.length; i++) {
      if (btns[i].dataset.vg === v) return btns[i];
    }
    return btns[0] || null;
  },
  get: function(key) {
    try {
      return new URLSearchParams(location.hash.slice(1)).get(key) || '';
    } catch (e) { return ''; }
  },
  set: function(key, value) {
    try {
      var p = new URLSearchParams(location.hash.slice(1));
      if (value) p.set(key, value); else p.delete(key);
      var h = p.toString();
      history.replaceState(history.state, '',
        location.pathname + location.search + (h ? '#' + h : ''));
    } catch (e) {}
  }
};

// Games where Pokémon have no abilities: Legends Z-A for all of them, and
// Legends Arceus for all of them except Arceus, Cherrim and Regigigas (all
// of their forms). Used by the Pokémon page and the comparison.
window.pksNoAbilities = function(gen, formName) {
  if (gen === 'legends-za') return true;
  if (gen === 'legends-arceus') {
    return !/^(arceus|cherrim|regigigas)(?![a-z])/i.test(String(formName || ''));
  }
  return false;
};

// A suggestion's sprite that failed to load (the 3D render of a form PokeAPI
// has none for): fall back to its 2D sprite once.
// The fallbacks (p.sprite_alt) are space-separated and tried in order: the
// official artwork, then the 2D sprite (which is the only pixel art one).
window.pksSpriteChain = function(img, alts) {
  if (img._pksAlts !== alts) { img._pksAlts = alts; img._pksRest = null; }
  var rest = img._pksRest ||
    String(alts || '').split(' ').filter(Boolean);
  var next = rest.shift();
  img._pksRest = rest;
  if (!next) { img.onerror = null; return; }
  img.classList.toggle('sprite-3d', /\/other\//.test(next));
  img.src = next;
};
window.pksSuggestSprite = function(img, p) {
  window.pksSpriteChain(img, p && p.sprite_alt);
};

// ---- Pokémon results page (/search/): stay on the game and form ----
// Works through the page's own buttons (.game-btn[data-vg] and
// .form-btn[data-idx]), so whatever the buttons do on a normal click is what
// restores the view: remember a click in the #hash, and after a refresh click
// the remembered buttons again. Compare has its own per-side memory.
(function() {
  var M = window.pksPageMemory;
  if (!M) return;
  function onSearchPage() { return /\/search\/?$/.test(location.pathname); }
  document.addEventListener('click', function(e) {
    if (!e.isTrusted || !onSearchPage() || !e.target.closest) return;
    var g = e.target.closest('.game-btn[data-vg]');
    if (g) { M.set('game', g.dataset.vg); return; }
    var f = e.target.closest('.form-btn[data-idx]:not(.gender-btn)');
    if (f) M.set('formi', f.dataset.idx);
  }, true);
  function restore() {
    if (!onSearchPage()) return;
    var fi = M.get('formi'), g = M.get('game');
    try {
      // form first: it keeps the selected game when it is in it
      if (fi) {
        var fb = document.querySelector(
          '.form-btn[data-idx="' + fi + '"]:not(.gender-btn)');
        if (fb && !fb.classList.contains('active')) fb.click();
      }
      if (g) {
        var gb = document.querySelector('.game-btn[data-vg="' + g + '"]');
        if (gb && !gb.classList.contains('active')) gb.click();
      }
    } catch (err) {}
  }
  // After the page's own start-up has run; checked again once everything has
  // loaded in case that start-up finished late.
  document.addEventListener('DOMContentLoaded', function() {
    setTimeout(restore, 0);
  });
  window.addEventListener('load', restore);
})();

// Reveal the sprites (see .sprite-pending) once the remembered game / form
// has been restored; the timer is a safety net so they can never stay hidden.
(function() {
  function reveal() {
    document.querySelectorAll('.sprite-pending').forEach(function(el) {
      el.classList.remove('sprite-pending');
    });
  }
  window.pksRevealSprites = reveal;
  document.addEventListener('DOMContentLoaded', function() {
    setTimeout(reveal, 2500);
  });
  window.addEventListener('load', function() { setTimeout(reveal, 80); });
  // sprites inserted later (a search result swapped in by htmx) are shown
  // straight away: nothing is restored for them
  document.addEventListener('htmx:afterSettle', function() {
    setTimeout(reveal, 0);
  });
})();