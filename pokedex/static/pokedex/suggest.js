/*
 * Shared search-assist scorer.
 *
 * Ranking tiers (higher = shown first):
 *   exact match  >  starts with  >  a word starts with  >  contains
 *   >  close spelling (typo tolerant)  >  letters in order
 * Inside a tier, shorter names come first, so the exact spelling is always
 * on top and near-misses follow underneath. Returns 0 for "no match".
 */
(function () {
  function norm(s) {
    return String(s).toLowerCase()
      .replace(/[\s_]+/g, '-')
      .replace(/[^a-z0-9\-]/g, '');
  }

  // Damerau-Levenshtein (adjacent swaps count as one typo), with early exit.
  function distance(a, b, max) {
    var la = a.length, lb = b.length;
    if (Math.abs(la - lb) > max) return max + 1;
    var prev2 = null;
    var prev = [], cur = [];
    for (var j = 0; j <= lb; j++) prev[j] = j;
    for (var i = 1; i <= la; i++) {
      cur = [i];
      var rowMin = i;
      for (var k = 1; k <= lb; k++) {
        var cost = a[i - 1] === b[k - 1] ? 0 : 1;
        var v = Math.min(prev[k] + 1, cur[k - 1] + 1, prev[k - 1] + cost);
        if (prev2 && i > 1 && k > 1 &&
            a[i - 1] === b[k - 2] && a[i - 2] === b[k - 1]) {
          v = Math.min(v, prev2[k - 2] + 1);
        }
        cur[k] = v;
        if (v < rowMin) rowMin = v;
      }
      if (rowMin > max) return max + 1;
      prev2 = prev;
      prev = cur;
    }
    return prev[lb];
  }

  function pack(tier, name) {
    // shorter names win ties inside a tier
    return tier * 1000 + (999 - Math.min(name.length, 999));
  }

  // Alphabetical order inside a tier: the part of the name after the typed
  // text becomes a number between 0 and 1 (a "-" sorts first, then digits,
  // then letters), and the earlier in the alphabet the higher the score.
  function packAlpha(tier, rest) {
    var key = 0, scale = 1;
    for (var i = 0; i < Math.min(rest.length, 8); i++) {
      var c = rest.charCodeAt(i), v;
      if (c >= 97 && c <= 122) v = c - 97 + 12;        // a-z
      else if (c >= 48 && c <= 57) v = c - 48 + 2;     // 0-9
      else v = 1;                                      // "-"
      scale /= 38;
      key += v * scale;
    }
    return tier * 1000 + 999 * (1 - key);
  }

  window.pksScore = function (rawName, rawQuery) {
    var name = norm(rawName);
    var q = norm(rawQuery);
    if (!q) return 50;
    if (name === q) return pack(10, name);

    var words = name.split('-');

    // The query is the whole name a family of forms starts with ("zygarde"
    // -> zygarde-10, zygarde-50, zygarde-complete, zygarde-mega): those are
    // listed alphabetically, right under the exact match.
    if (name.indexOf(q + '-') === 0) return packAlpha(9.5, name.slice(q.length));

    // The query is one whole word of the name ("mega" -> charizard-mega-x),
    // so every Mega is listed before names that only start with those
    // letters (meganium).
    if (words.length > 1 && words.indexOf(q) !== -1) return pack(9.5, name);
    if (name.indexOf(q) === 0) return pack(9, name);

    // Several words, in any order: "mega charizard", "charizard mega x",
    // "mega x" all find charizard-mega-x. Each typed word has to start a
    // different word of the name.
    var qWords = q.split('-').filter(Boolean);
    if (qWords.length > 1) {
      var free = words.slice(), all = true;
      for (var t = 0; t < qWords.length && all; t++) {
        var at = -1;
        for (var f = 0; f < free.length; f++) {
          if (free[f].indexOf(qWords[t]) === 0) { at = f; break; }
        }
        if (at === -1) all = false; else free.splice(at, 1);
      }
      if (all) return pack(free.length === 0 ? 9.6 : 8.5, name);
    }

    for (var w = 0; w < words.length; w++) {
      if (words[w].indexOf(q) === 0) return pack(8, name);
    }
    if (name.indexOf(q) !== -1) return pack(7, name);

    // close spellings: allow more typos for longer queries
    var allowed = q.length <= 3 ? 0 : (q.length <= 6 ? 1 : 2);
    if (allowed > 0) {
      var d = distance(name, q, allowed);
      if (d <= allowed) return pack(6, name) - d * 100;
      // typo while still typing: compare against the same-length prefix
      var pd = distance(name.slice(0, q.length), q, allowed);
      if (pd <= allowed) return pack(5, name) - pd * 100;
      for (var x = 0; x < words.length; x++) {
        if (words[x].length >= 3 && distance(words[x], q, allowed) <= allowed) {
          return pack(4, name) - 0;
        }
      }
    }

    // letters appear in order ("tbolt" -> thunderbolt)
    if (q.length >= 5) {
      var qi = 0;
      for (var i = 0; i < name.length && qi < q.length; i++) {
        if (name[i] === q[qi]) qi++;
      }
      if (qi === q.length) return pack(3, name);
    }
    return 0;
  };
})();