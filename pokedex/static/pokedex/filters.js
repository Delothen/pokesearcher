/*
 * Filter bars for the search boxes (Pokémon, Moves, Abilities, Items...).
 *
 *   var F = pksFilters.attach({
 *     kind: 'pokemon' | 'move' | 'ability' | 'item' | 'favourites',
 *     data: (favourites only) function () returning a Promise of the filter
 *           data of each favourite, see the favourites block below,
 *     before: element the filter bar is inserted in front of,
 *     onChange: function () { ...redraw the suggestions... }
 *   });
 *   var shown = F.apply(allNames);   // strings, or {name: ...} objects
 *
 * The Pokémon Type filter is a dropdown of checkboxes (up to 2 ticked); a
 * Pokémon must have every ticked type, so ticking two finds dual types.
 * The filter data (type, generation, gender...) comes from
 * /api/filter-data/?kind=..., cached on the server. Filtering only narrows
 * the suggestions: pressing Search with a typed name always searches it.
 */
(function () {
  var TYPES = ['normal', 'fire', 'water', 'electric', 'grass', 'ice',
    'fighting', 'poison', 'ground', 'flying', 'psychic', 'bug', 'rock',
    'ghost', 'dragon', 'dark', 'steel', 'fairy', 'stellar'];
  var REGIONS = ['Kanto', 'Johto', 'Hoenn', 'Sinnoh', 'Unova', 'Kalos',
    'Alola', 'Galar', 'Paldea'];

  function cap(s) {
    s = String(s || '').replace(/-/g, ' ');
    return s.charAt(0).toUpperCase() + s.slice(1);
  }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function genLabel(n) {
    return 'Gen ' + n + (REGIONS[n - 1] ? ' (' + REGIONS[n - 1] + ')' : '');
  }
  function has(arr, v) { return !!arr && arr.indexOf(v) !== -1; }

  // ---- what each kind of search can be filtered by ----------------------
  // values(data): the options present in the data, in display order.
  // test(entry, value): does one entry match the chosen option?
  function distinct(data, pick) {
    var seen = {};
    Object.keys(data).forEach(function (k) {
      [].concat(pick(data[k]) || []).forEach(function (v) {
        if (v != null && v !== '') seen[v] = true;
      });
    });
    return Object.keys(seen);
  }
  function ordered(values, order) {
    var known = order.filter(function (v) { return values.indexOf(v) !== -1; });
    var rest = values.filter(function (v) { return order.indexOf(v) === -1; })
      .sort();
    return known.concat(rest);
  }
  function gens(data) {
    return distinct(data, function (e) { return e.g; })
      .map(Number).sort(function (a, b) { return a - b; });
  }
  // multi: a dropdown of checkboxes (up to `max` ticked); an entry must have
  // every ticked type, so ticking two finds the dual-type Pokémon.
  function typeField(label, pick, multi) {
    return {
      id: 'type', label: label,
      multi: !!multi, max: 2,
      options: function (d) {
        return ordered(distinct(d, pick), TYPES)
          .map(function (t) { return [t, cap(t)]; });
      },
      test: function (e, v) { return has([].concat(pick(e) || []), v); }
    };
  }
  var genField = {
    id: 'gen', label: 'Generation',
    options: function (d) {
      return gens(d).map(function (n) { return [String(n), genLabel(n)]; });
    },
    test: function (e, v) { return String(e.g) === v; }
  };

  var FIELDS = {
    pokemon: [
      typeField('Type', function (e) { return e.t; }, true),
      genField,
      {
        id: 'gender', label: 'Gender',
        options: function (d) {
          var names = {
            both: 'Male or female', female: 'Female only',
            male: 'Male only', none: 'Genderless'
          };
          var found = distinct(d, function (e) { return e.x; });
          return ['both', 'female', 'male', 'none']
            .filter(function (k) { return found.indexOf(k) !== -1; })
            .map(function (k) { return [k, names[k]]; });
        },
        test: function (e, v) { return e.x === v; }
      },
      {
        id: 'form', label: 'Form',
        options: function (d) {
          var names = {
            base: 'Base form', mega: 'Mega', gmax: 'Gigantamax',
            tera: 'Terastal', regional: 'Regional', gender: 'Gender',
            other: 'Other forms'
          };
          var found = distinct(d, function (e) { return e.f; });
          return ['base', 'mega', 'gmax', 'tera', 'regional', 'gender',
                  'other']
            .filter(function (k) { return found.indexOf(k) !== -1; })
            .map(function (k) { return [k, names[k]]; });
        },
        test: function (e, v) { return e.f === v; }
      }
    ],
    move: [
      typeField('Type', function (e) { return e.t; }),
      {
        id: 'category', label: 'Category',
        options: function (d) {
          return ordered(distinct(d, function (e) { return e.c; }),
            ['physical', 'special', 'status'])
            .map(function (c) { return [c, cap(c)]; });
        },
        test: function (e, v) { return e.c === v; }
      },
      genField
    ],
    ability: [genField],
    item: [
      {
        id: 'category', label: 'Category',
        options: function (d) {
          return distinct(d, function (e) { return e.c; }).sort()
            .map(function (c) { return [c, cap(c)]; });
        },
        test: function (e, v) { return e.c === v; }
      },
      {
        id: 'use', label: 'Used',
        options: function () {
          return [['held', 'Held by a Pokémon'], ['battle', 'From the bag']];
        },
        test: function (e, v) { return has(e.u, v); }
      },
      {
        id: 'tag', label: 'Tag',
        options: function (d) {
          var names = { evolution: 'Evolution', form: 'Form changing' };
          var found = distinct(d, function (e) { return e.t; });
          return ['evolution', 'form']
            .filter(function (k) { return found.indexOf(k) !== -1; })
            .map(function (k) { return [k, names[k]]; });
        },
        test: function (e, v) { return has(e.t, v); }
      }
    ],
    // The Favourites page mixes every kind of search in one list. Each entry
    // carries what the other kinds filter on (t types, g generation, x
    // gender, f form, mc move category, ic item category, u use, it item
    // tags, k what it is). A filter only keeps the entries that have that
    // attribute, so ticking "Fire" keeps the Fire Pokémon and Fire moves.
    // A comparison has `sides` (the two things compared) and matches when
    // either side passes every filter.
    favourites: [
      {
        id: 'kind', label: 'Kind', top: true,
        options: function (d) {
          var names = {
            pokemon: 'Pokémon', move: 'Moves', ability: 'Abilities',
            item: 'Items', sprite: 'Sprites', card: 'Trading cards',
            compare: 'Comparisons'
          };
          var found = distinct(d, function (e) { return e.k; });
          return Object.keys(names)
            .filter(function (k) { return found.indexOf(k) !== -1; })
            .map(function (k) { return [k, names[k]]; });
        },
        test: function (e, v) { return e.k === v; }
      },
      typeField('Type', function (e) { return e.t; }, true),
      genField,
      {
        id: 'gender', label: 'Gender',
        options: function (d) {
          var names = {
            both: 'Male or female', female: 'Female only',
            male: 'Male only', none: 'Genderless'
          };
          var found = distinct(d, function (e) { return e.x; });
          return ['both', 'female', 'male', 'none']
            .filter(function (k) { return found.indexOf(k) !== -1; })
            .map(function (k) { return [k, names[k]]; });
        },
        test: function (e, v) { return e.x === v; }
      },
      {
        id: 'form', label: 'Form',
        options: function (d) {
          var names = {
            base: 'Base form', mega: 'Mega', gmax: 'Gigantamax',
            tera: 'Terastal', regional: 'Regional', gender: 'Gender',
            other: 'Other forms'
          };
          var found = distinct(d, function (e) { return e.f; });
          return ['base', 'mega', 'gmax', 'tera', 'regional', 'gender',
                  'other']
            .filter(function (k) { return found.indexOf(k) !== -1; })
            .map(function (k) { return [k, names[k]]; });
        },
        test: function (e, v) { return e.f === v; }
      },
      {
        id: 'mcat', label: 'Move category',
        options: function (d) {
          return ordered(distinct(d, function (e) { return e.mc; }),
            ['physical', 'special', 'status'])
            .map(function (c) { return [c, cap(c)]; });
        },
        test: function (e, v) { return e.mc === v; }
      },
      {
        id: 'icat', label: 'Item category',
        options: function (d) {
          return distinct(d, function (e) { return e.ic; }).sort()
            .map(function (c) { return [c, cap(c)]; });
        },
        test: function (e, v) { return e.ic === v; }
      },
      {
        id: 'use', label: 'Item used',
        options: function (d) {
          var found = distinct(d, function (e) { return e.u; });
          return [['held', 'Held by a Pokémon'], ['battle', 'From the bag']]
            .filter(function (o) { return found.indexOf(o[0]) !== -1; });
        },
        test: function (e, v) { return has(e.u, v); }
      },
      {
        id: 'tag', label: 'Item tag',
        options: function (d) {
          var names = { evolution: 'Evolution', form: 'Form changing' };
          var found = distinct(d, function (e) { return e.it; });
          return ['evolution', 'form']
            .filter(function (k) { return found.indexOf(k) !== -1; })
            .map(function (k) { return [k, names[k]]; });
        },
        test: function (e, v) { return has(e.it, v); }
      }
    ]
  };

  // ---- one download per kind, shared by every bar on the page -------------
  var loads = {};
  function load(kind) {
    if (!loads[kind]) {
      loads[kind] = fetch('/api/filter-data/?kind=' + kind)
        .then(function (r) { return r.json(); })
        .then(function (d) {
          return d && typeof d === 'object' && !d.error ? d : {};
        })
        .catch(function () { loads[kind] = null; return {}; });
    }
    return loads[kind];
  }

  function attach(opts) {
    var fields = FIELDS[opts.kind] || [];
    var data = null;            // null until loaded
    var chosen = {};            // field id -> chosen option value
    var selects = {};
    var resets = {};            // multi field id -> function that unticks it

    var root = el('div', 'filter-bar');
    var toggle = el('button', 'filter-toggle');
    toggle.type = 'button';
    toggle.setAttribute('aria-expanded', 'false');
    var panel = el('div', 'filter-panel');
    panel.hidden = true;
    var grid = el('div', 'filter-grid');
    // only says "Loading…" / "unavailable"; empty (and invisible) otherwise
    var summary = el('div', 'filter-summary');
    var clear = el('button', 'filter-clear', 'Clear filters');
    clear.type = 'button';
    // "Clear filters" is the last cell of the same row as the dropdowns
    var clearCell = el('div', 'filter-clear-cell');
    clearCell.appendChild(clear);
    panel.appendChild(summary);
    panel.appendChild(grid);
    root.appendChild(panel);
    if (opts.before && opts.before.parentNode) {
      opts.before.parentNode.insertBefore(root, opts.before);
    }
    // The Filters button sits on the search row, before the search box; the
    // panel it opens stays above the row.
    var form = opts.before && opts.before.querySelector
      ? opts.before.querySelector('form') : null;
    if (form) form.insertBefore(toggle, form.firstChild);
    else root.insertBefore(toggle, panel);

    function count() { return Object.keys(chosen).length; }
    function refresh() {
      var n = count();
      toggle.textContent = n ? 'Filters (' + n + ')' : 'Filters';
      toggle.classList.toggle('has-filters', n > 0);
      clear.disabled = n === 0;
      if (data && !Object.keys(data).length) {
        summary.textContent = 'Filters are unavailable right now.';
      } else if (!data) {
        summary.textContent = 'Loading filters…';
      } else {
        summary.textContent = '';
      }
    }

    function build() {
      grid.innerHTML = '';
      resets = {};
      if (!data || !Object.keys(data).length) { refresh(); return; }
      fields.forEach(function (f) {
        var opts2 = f.options(data);
        if (!opts2.length) return;
        if (f.multi) { buildMulti(f, opts2); return; }
        var wrap = el('label', 'filter-field');
        wrap.appendChild(el('span', 'filter-label', f.label));
        var sel = el('select');
        var any = el('option', null, 'Any');
        any.value = '';
        sel.appendChild(any);
        opts2.forEach(function (o) {
          var op = el('option', null, o[1]);
          op.value = o[0];
          sel.appendChild(op);
        });
        sel.addEventListener('change', function () {
          if (sel.value) chosen[f.id] = sel.value; else delete chosen[f.id];
          changed();
        });
        selects[f.id] = sel;
        wrap.appendChild(sel);
        grid.appendChild(wrap);
      });
      grid.appendChild(clearCell);
      refresh();
    }

    // A dropdown of checkboxes; at most f.max can be ticked at once.
    function buildMulti(f, options) {
      var wrap = el('div', 'filter-field filter-multi');
      wrap.appendChild(el('span', 'filter-label',
        f.label + ' (up to ' + f.max + ')'));
      var btn = el('button', 'multi-btn', 'Any');
      btn.type = 'button';
      btn.setAttribute('aria-haspopup', 'true');
      btn.setAttribute('aria-expanded', 'false');
      var list = el('div', 'multi-list');
      list.hidden = true;
      var boxes = [];
      var labels = {};

      function picked() {
        return boxes.filter(function (b) { return b.checked; })
          .map(function (b) { return b.value; });
      }
      function sync() {
        var p = picked();
        btn.textContent = p.length
          ? p.map(function (v) { return labels[v]; }).join(' + ') : 'Any';
        boxes.forEach(function (b) {
          b.disabled = !b.checked && p.length >= f.max;
          b.parentNode.classList.toggle('disabled', b.disabled);
        });
        if (p.length) chosen[f.id] = p; else delete chosen[f.id];
      }
      function setOpen(open) {
        list.hidden = !open;
        btn.setAttribute('aria-expanded', String(open));
        btn.classList.toggle('open', open);
      }

      options.forEach(function (o) {
        labels[o[0]] = o[1];
        var row = el('label', 'multi-opt');
        var cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.value = o[0];
        cb.addEventListener('change', function () {
          sync();
          changed();
        });
        boxes.push(cb);
        row.appendChild(cb);
        row.appendChild(el('span', null, o[1]));
        list.appendChild(row);
      });

      btn.addEventListener('click', function () { setOpen(list.hidden); });
      document.addEventListener('click', function (e) {
        if (!list.hidden && !wrap.contains(e.target)) setOpen(false);
      });
      wrap.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && !list.hidden) {
          setOpen(false);
          btn.focus();
        }
      });
      resets[f.id] = function () {
        boxes.forEach(function (b) { b.checked = false; });
        sync();
      };

      wrap.appendChild(btn);
      wrap.appendChild(list);
      grid.appendChild(wrap);
    }

    function changed() {
      refresh();
      if (opts.onChange) opts.onChange();
    }

    toggle.addEventListener('click', function () {
      panel.hidden = !panel.hidden;
      toggle.setAttribute('aria-expanded', String(!panel.hidden));
      toggle.classList.toggle('open', !panel.hidden);
    });
    clear.addEventListener('click', function () { reset(true); });

    function reset(notify) {
      chosen = {};
      Object.keys(selects).forEach(function (k) { selects[k].value = ''; });
      Object.keys(resets).forEach(function (k) { resets[k](); });
      chosen = {};
      refresh();
      if (notify && opts.onChange) opts.onChange();
    }

    function passes(entry, f) {
      var v = chosen[f.id];
      if (!v) return true;
      // multi fields: the entry must have every ticked option
      return f.multi ? v.every(function (x) { return f.test(entry, x); })
                     : f.test(entry, v);
    }

    function matches(name) {
      if (!count() || !data) return true;
      var entry = data[name];
      if (!entry) return false;
      // the things a comparison compares: one side has to pass every filter
      // that is about the thing itself (the Kind filter is about the
      // comparison)
      if (entry.sides) {
        return fields.every(function (f) { return !f.top || passes(entry, f); })
          && entry.sides.some(function (side) {
            return fields.every(function (f) { return f.top || passes(side, f); });
          });
      }
      return fields.every(function (f) { return passes(entry, f); });
    }

    function apply(list) {
      if (!count() || !data) return list;
      return list.filter(function (item) {
        return matches(typeof item === 'string' ? item : item.name);
      });
    }

    refresh();
    (opts.data ? opts.data() : load(opts.kind)).then(function (d) {
      data = d || {};
      build();
      if (opts.onLoad) opts.onLoad();
    });

    return {
      el: root,
      apply: apply,
      matches: matches,
      active: function () { return count() > 0; },
      reset: function () { reset(false); },
      show: function () { root.hidden = false; toggle.hidden = false; },
      hide: function () { root.hidden = true; toggle.hidden = true; },
      // shown in the suggestion box when the filters leave nothing
      emptyNote: function () {
        return el('div', 'suggestion-empty',
          'Nothing matches these filters. Change or clear them to see more.');
      }
    };
  }

  window.pksFilters = { attach: attach, load: load };
})();