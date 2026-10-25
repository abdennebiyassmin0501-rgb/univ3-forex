/* رسم الشموع اليابانية — نسخة مبسطة بدون مكتبات خارجية.
   يستقبل فقط الشموع الظاهرة للطالب (الخادم لا يرسل أي شمعة مستقبلية). */
(function () {
  var data = JSON.parse(document.getElementById('chart-data').textContent);
  var C = data.candles, T = data.trades || [];
  var canvas = document.getElementById('chart'), wrap = canvas.parentNode, tip = document.getElementById('tip');
  var ctx = canvas.getContext('2d');
  var UP = '#15803d', DOWN = '#c62828', GRID = '#eef1f5', AX = '#5f6b7a', BUY = '#2563eb', SELL = '#d97706';
  var RIGHT = 58, BOTTOM = 24, TOP = 10, PAD_BARS = 2;
  var W = 0, H = 0, dpr = 1;
  var view = { n: C.length, s: 0 };   // نعرض كل الشموع افتراضيًا
  var hover = -1;

  function clamp() {
    view.n = Math.max(8, Math.min(C.length, view.n));
    view.s = Math.max(0, Math.min(C.length - view.n, view.s));
  }
  function resize() {
    dpr = window.devicePixelRatio || 1;
    W = wrap.clientWidth; H = wrap.clientHeight;
    canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
    draw();
  }
  function plotW() { return W - RIGHT; }
  function bw() { return plotW() / (view.n + PAD_BARS); }
  function xOf(i) { return (i - view.s + 0.5) * bw(); }
  function iOf(x) { return Math.floor(x / bw() + view.s); }

  function draw() {
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, W, H);
    var a = Math.max(0, Math.floor(view.s)), b = Math.min(C.length - 1, Math.ceil(view.s + view.n));
    var lo = Infinity, hi = -Infinity;
    for (var i = a; i <= b; i++) { lo = Math.min(lo, C[i].l); hi = Math.max(hi, C[i].h); }
    var pad = (hi - lo) * 0.12 || 0.5; lo -= pad; hi += pad;
    var ph = H - BOTTOM - TOP;
    function y(p) { return TOP + (hi - p) / (hi - lo) * ph; }

    // محور الأسعار
    ctx.font = '11px system-ui, sans-serif'; ctx.textBaseline = 'middle'; ctx.textAlign = 'left';
    var st = niceStep((hi - lo) / 5);
    for (var p = Math.ceil(lo / st) * st; p <= hi; p += st) {
      var yy = Math.round(y(p)) + 0.5;
      ctx.strokeStyle = GRID; ctx.beginPath(); ctx.moveTo(0, yy); ctx.lineTo(plotW(), yy); ctx.stroke();
      ctx.fillStyle = AX; ctx.fillText(p.toFixed(2) + ' $', plotW() + 6, yy);
    }
    // محور التواريخ
    ctx.textAlign = 'center'; ctx.textBaseline = 'top';
    var every = Math.max(1, Math.ceil(64 / bw()));
    for (i = a; i <= b; i++) {
      if ((i - a) % every) continue;
      ctx.fillStyle = AX; ctx.fillText(C[i].d.slice(5), Math.max(18, Math.min(plotW() - 18, xOf(i))), H - BOTTOM + 5);
    }
    // الشموع
    var w = Math.max(2, bw() * 0.65);
    for (i = a; i <= b; i++) {
      var c = C[i], xc = xOf(i), col = c.c >= c.o ? UP : DOWN;
      ctx.strokeStyle = ctx.fillStyle = col; ctx.lineWidth = 1.2;
      ctx.beginPath(); ctx.moveTo(Math.round(xc) + 0.5, y(c.h)); ctx.lineTo(Math.round(xc) + 0.5, y(c.l)); ctx.stroke();
      ctx.fillRect(xc - w / 2, y(Math.max(c.o, c.c)), w, Math.max(1.5, Math.abs(y(c.o) - y(c.c))));
    }
    ctx.lineWidth = 1;
    // علامات صفقات الطالب
    T.forEach(function (t) {
      if (t.i < a || t.i > b) return;
      var c = C[t.i], xc = xOf(t.i), s = 6;
      ctx.fillStyle = t.side === 'buy' ? BUY : SELL; ctx.beginPath();
      if (t.side === 'buy') { var yb = y(c.l) + 6; ctx.moveTo(xc, yb); ctx.lineTo(xc - s, yb + s * 1.4); ctx.lineTo(xc + s, yb + s * 1.4); }
      else { var ya = y(c.h) - 6; ctx.moveTo(xc, ya); ctx.lineTo(xc - s, ya - s * 1.4); ctx.lineTo(xc + s, ya - s * 1.4); }
      ctx.fill();
    });
    // الشمعة المحددة
    if (hover >= a && hover <= b) {
      ctx.fillStyle = 'rgba(29,78,137,.08)';
      ctx.fillRect(xOf(hover) - bw() / 2, TOP, bw(), ph);
    }
    // المستقبل مجهول
    var lx = xOf(C.length - 1) + bw() / 2;
    if (lx < plotW() - 4) {
      ctx.fillStyle = 'rgba(95,107,122,.08)'; ctx.fillRect(lx, TOP, plotW() - lx, ph);
      ctx.fillStyle = AX; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText('?', lx + (plotW() - lx) / 2, TOP + ph / 2);
    }
    ctx.strokeStyle = '#d5dbe3'; ctx.strokeRect(0.5, 0.5, plotW(), H - BOTTOM);
  }
  function niceStep(r) {
    var e = Math.pow(10, Math.floor(Math.log10(r))), f = r / e;
    return (f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10) * e;
  }
  function showTip(i) {
    if (i < 0 || i >= C.length) { tip.hidden = true; hover = -1; draw(); return; }
    hover = i; var c = C[i];
    tip.innerHTML = c.d + '  ' + (c.c >= c.o ? '▲ صاعدة' : '▼ هابطة') +
      '<br>افتتاح ' + c.o.toFixed(2) + '  إغلاق ' + c.c.toFixed(2) +
      '<br>أعلى ' + c.h.toFixed(2) + '  أدنى ' + c.l.toFixed(2);
    tip.hidden = false; draw();
  }
  function zoom(f) {
    var right = view.s + view.n; view.n *= f; view.s = right - view.n; clamp(); draw();
  }
  function pick(clientX) {
    var x = clientX - canvas.getBoundingClientRect().left;
    showTip(x < plotW() ? iOf(x) : -1);
  }
  canvas.addEventListener('mousemove', function (e) { pick(e.clientX); });
  canvas.addEventListener('mouseleave', function () { showTip(-1); });
  canvas.addEventListener('touchstart', function (e) { pick(e.touches[0].clientX); }, { passive: true });

  document.querySelectorAll('[data-zoom]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var z = btn.getAttribute('data-zoom');
      if (z === 'in') zoom(1 / 1.4); else if (z === 'out') zoom(1.4);
      else { view.n = C.length; view.s = 0; clamp(); draw(); }
    });
  });

  window.addEventListener('resize', resize);
  clamp(); resize();
  window.__chart = { view: view, count: C.length };
})();
