// Markets Edge Board — Railway Function (Bun). Read-only: never places orders.
// 26 Oct 2026: copy of the Crypto Edge Board for Kalshi's COMMODITY and CURRENCY (and index) 15-minute and hourly
// markets. Spot prices and 1m/5m candles come from Yahoo Finance; one strike per series per window is shown (the
// strike closest to the 94-98c buy band). Read-only: never places orders.

const KALSHI = "https://api.elections.kalshi.com/trade-api/v2";
const CB_EX = "https://api.exchange.coinbase.com";
const CB_RATES = "https://api.coinbase.com/v2/exchange-rates?currency=USD";
const BINANCE_US = "https://api.binance.us/api/v3";
const PORT = Number(Bun.env.PORT || 8787);
const HORIZON_H = Number(Bun.env.EDGE_HORIZON_HOURS || 24);
const PASSWORD = Bun.env.EDGE_PASSWORD || "";
const HDRS = { "User-Agent": "Mozilla/5.0 (markets-edge-board)", Accept: "application/json" };
const YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/";

const PAGE: string = `<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Markets Edge Board</title>
<style>
:root{--bg:#0d1117;--panel:#161b22;--line:#2a313c;--text:#e6edf3;--dim:#8b949e;
--green:#3fb950;--red:#f85149;--amber:#e3b341;--blue:#58a6ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);
font:13px/1.35 -apple-system,BlinkMacSystemFont,"SF Pro Text",Segoe UI,Roboto,sans-serif}
header{display:flex;align-items:center;gap:10px;padding:10px 14px;border-bottom:1px solid var(--line);
position:sticky;top:0;background:var(--bg);z-index:5;flex-wrap:wrap}
h1{font-size:16px;margin:0;font-weight:650}
.pill{padding:2px 8px;border-radius:99px;background:var(--panel);border:1px solid var(--line);color:var(--dim);font-size:12px}
.live{color:var(--green)}.stale{color:var(--red)}
.wrap{padding:12px 14px}
.coins{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:8px;margin-bottom:12px}
.coin{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:8px 10px;cursor:pointer}
.coin.sel{outline:2px solid var(--blue)}
.coin .top{display:flex;justify-content:space-between;align-items:baseline}
.coin .sym{font-weight:700;font-size:14px}.coin .px{font-size:16px;font-weight:650;font-variant-numeric:tabular-nums}
.coin .sig{font-size:11px;margin-top:2px}
.grid4{display:grid;grid-template-columns:1fr 1fr;gap:1px 8px;margin-top:5px;font-size:12px}
.grid4 span{color:var(--dim)}.num{font-variant-numeric:tabular-nums}
.pos{color:var(--green)}.neg{color:var(--red)}
.box{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:8px 10px;margin-bottom:10px}
.box h2{font-size:12px;margin:0 0 6px;color:var(--dim);font-weight:600;text-transform:uppercase;letter-spacing:.04em}
.controls{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.controls label{color:var(--dim);font-size:12px;display:flex;gap:5px;align-items:center}
select,input[type=number]{background:var(--bg);color:var(--text);border:1px solid var(--line);border-radius:6px;padding:4px 6px;font-size:13px}
input[type=number]{width:58px}
.dot{display:inline-block;width:10px;height:10px;border-radius:50%;vertical-align:middle;margin-right:4px}
.d-g{background:var(--green)}.d-y{background:var(--amber)}.d-r{background:var(--red)}
.tbl{overflow-x:auto;border:1px solid var(--line);border-radius:10px}
table{border-collapse:collapse;width:100%;min-width:1120px}
th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
th{position:sticky;top:0;background:var(--panel);color:var(--dim);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.03em}
td.l,th.l{text-align:left}
tr.bid td{background:rgba(63,185,80,.20)} tr.bid td:first-child{box-shadow:inset 4px 0 0 var(--green)}
tr.close td{background:rgba(227,179,65,.15)} tr.close td:first-child{box-shadow:inset 4px 0 0 var(--amber)}
tr.avoid td{background:rgba(248,81,73,.13);color:#c9a3a0} tr.avoid td:first-child{box-shadow:inset 4px 0 0 var(--red)}
tr.bid.fresh td{animation:flash 1.2s ease-out 2}
@keyframes flash{0%{background:rgba(63,185,80,.55)}100%{background:rgba(63,185,80,.20)}}
.sigl{font-weight:700;font-size:13px;padding:3px 9px;border-radius:4px;color:#0d1117}
.s-bid{background:var(--green)}.s-close{background:var(--amber)}.s-avoid{background:var(--red)}.s-none{background:#30363d;color:var(--dim)}
.tag{font-size:10px;padding:1px 6px;border-radius:4px;background:#21262d;color:var(--dim);margin-left:4px}
.side{font-weight:700}.side.yes{color:var(--green)}.side.no{color:#ff7b72}
.bar{height:6px;background:#21262d;border-radius:3px;position:relative;width:80px;display:inline-block;vertical-align:middle}
.bar i{position:absolute;top:-2px;width:2px;height:10px}
.bar .m{background:var(--blue)}.bar .k{background:var(--amber)}
.foot{color:var(--dim);font-size:12px;margin-top:12px;line-height:1.5}
.err{color:var(--amber);font-size:12px;margin-top:6px}
.urgent{color:var(--amber)}
.t-red{color:var(--red)!important}.t-yel{color:var(--amber)!important}.t-grn{color:var(--green)!important}
.t-red .tbar i{background:var(--red)!important}.t-yel .tbar i{background:var(--amber)!important}.t-grn .tbar i{background:var(--green)!important}
:root{--gold:#f2c94c}
tr.gold td{border-top:2px solid var(--gold);border-bottom:2px solid var(--gold)}
tr.gold td:first-child{border-left:2px solid var(--gold)} tr.gold td:last-child{border-right:2px solid var(--gold)}
.crit{display:inline-flex;gap:3px;margin-left:5px;vertical-align:middle}
.crit i{font-style:normal;font-size:10px;padding:1px 4px;border-radius:3px;background:#21262d;color:#56606b}
.crit i.on{background:var(--gold);color:#0d1117;font-weight:700}
.star{color:var(--gold);font-weight:700;margin-left:4px}
.wins{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:10px;margin-bottom:12px}
.win{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:10px 12px;position:relative}
.win.bid{border-color:var(--green);box-shadow:0 0 0 1px var(--green) inset}
.win.close{border-color:var(--amber)}
.win.avoid{border-color:#5a2a28}
.win.gold{box-shadow:0 0 0 3px var(--gold),0 0 18px rgba(242,201,76,.35)}
.win .hd{display:flex;justify-content:space-between;align-items:center;gap:8px}
.win .hd b{font-size:15px}
.win .clock{font-size:20px;font-weight:700;font-variant-numeric:tabular-nums}
.prog{height:5px;background:#21262d;border-radius:3px;margin:6px 0 8px;overflow:hidden}
.prog i{display:block;height:100%;background:var(--blue)}
.kv{display:grid;grid-template-columns:auto 1fr auto 1fr;gap:2px 8px;font-size:12px;font-variant-numeric:tabular-nums}
.kv span{color:var(--dim)}.kv b{text-align:right}
.big{display:flex;gap:10px;margin:6px 0;align-items:stretch}
.big div{flex:1;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:5px 7px;text-align:center}
.big small{display:block;color:var(--dim);font-size:10px;text-transform:uppercase;letter-spacing:.04em}
.big strong{font-size:17px;font-variant-numeric:tabular-nums}
.book{display:flex;height:8px;border-radius:4px;overflow:hidden;margin:3px 0 2px;background:#21262d}
.book .y{background:var(--green)}.book .n{background:var(--red)}
.spark{width:100%;height:46px;display:block;margin-top:6px}
.call{margin-top:6px;font-size:12px;font-weight:600}
.winband{display:flex;align-items:center;gap:10px;background:var(--bg);border:1px solid var(--line);border-radius:10px;padding:6px 10px;margin-bottom:6px}
.winband .pctbig{font-size:30px;font-weight:800;font-variant-numeric:tabular-nums;line-height:1}
.winband .lbl{font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:.05em}
.winband .meter{flex:1;height:10px;background:#21262d;border-radius:5px;overflow:hidden}
.winband .meter i{display:block;height:100%}
.wp-hi{color:var(--green)}.wp-mid{color:var(--amber)}.wp-lo{color:var(--red)}.wp-na{color:var(--dim)}
td.sf-hi,td.wp-hi{color:var(--green)!important}td.sf-mid,td.wp-mid{color:var(--amber)!important}td.sf-lo,td.wp-lo{color:var(--red)!important}td.wp-na{color:var(--dim)!important}
td.wp{font-size:22px;font-weight:800}
.lb{border:1px solid var(--line);border-radius:12px;overflow:hidden;margin-bottom:10px;background:var(--panel)}
.lb table{min-width:0}
.lb th{position:static;font-size:12px;padding:10px 12px}
.lb td{padding:13px 12px;font-size:17px;transition:background .4s}
.lb tr.r1 td{font-size:19px;padding:17px 12px;background:rgba(63,185,80,.12)}
.lb tr.r1 td:first-child{box-shadow:inset 5px 0 0 var(--green)}
.lb tr.r1.gold td{background:rgba(242,201,76,.16)}
.lb tr.r1.nobid td{background:transparent}
.lb .rk{font-weight:800;color:var(--dim);width:34px}
.lb tr.r1 .rk{color:var(--green)}
.lb .mv{font-size:10px;margin-left:2px}.mv.up{color:var(--green)}.mv.dn{color:var(--red)}
.lb .buy{font-weight:800;white-space:nowrap}
.lb .new1{animation:pulse1 1.4s ease-out}
@keyframes pulse1{0%{background:rgba(242,201,76,.6)}100%{}}
.lbhead{display:flex;justify-content:space-between;align-items:center;padding:7px 10px;border-bottom:1px solid var(--line)}
.lbhead b{font-size:15px;text-transform:uppercase;letter-spacing:.05em}
.lbhead span{color:var(--dim);font-size:12px}
.best{font-size:14px;font-weight:700}
details.sec{margin-bottom:10px}
details.sec>summary{cursor:pointer;color:var(--dim);font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.04em;padding:6px 2px;list-style:none}
details.sec>summary::before{content:"▸ ";}
details.sec[open]>summary::before{content:"▾ ";}
.lb .bk{display:inline-block;width:56px;height:7px;background:var(--red);border-radius:3px;vertical-align:middle;overflow:hidden}
.lb .bk i{display:block;height:100%;background:var(--green)}
.lb td.tleft b{font-size:20px;display:block}
.lb tr.r1 td.tleft b{font-size:23px}
.lb .tbar{display:block;height:5px;background:#21262d;border-radius:2px;margin-top:2px;overflow:hidden;min-width:56px}
.lb .tbar i{display:block;height:100%;background:var(--blue);margin-left:auto}
.lb td.urgent .tbar i{background:var(--amber)}
.lb td.px b{display:block;font-size:21px}
.lb td.px small{color:var(--dim);font-size:12px}
.lb td.px.yes b{color:var(--green)}.lb td.px.no b{color:#ff7b72}
.fav{display:inline-block;font-weight:800;font-size:16px;padding:5px 12px;border-radius:6px;color:#0d1117;letter-spacing:.03em}
.fav.yes{background:var(--green)}.fav.no{background:#ff7b72}.fav.none{background:#30363d;color:var(--dim)}
.lb tr.r1 .fav{font-size:18px;padding:6px 14px}
.lb td.px.pick{outline:2px solid currentColor;outline-offset:-3px;border-radius:6px}
.lb td.px.yes.pick{color:var(--green)}.lb td.px.no.pick{color:#ff7b72}
.lb td.px.dim b{opacity:.45}
.fx td.up{animation:fup 1s ease-out}.fx td.dn{animation:fdn 1s ease-out}
@keyframes fup{0%{background:rgba(63,185,80,.55)}100%{}}
@keyframes fdn{0%{background:rgba(248,81,73,.55)}100%{}}
.fx .rkn{color:var(--dim);font-weight:700}
.lb tr.r1 td,.lb tr.r1.bid td,.lb tr.r1.close td,.lb tr.r1.avoid td,.lb tr.r1.none td,.lb tr.r1.gold td,.lb tr.r1.nobid td{background:rgba(242,201,76,.13);color:var(--text);border-top:2px solid var(--gold);border-bottom:2px solid var(--gold)}
.lb tr.r1 td:first-child{border-left:2px solid var(--gold);box-shadow:inset 5px 0 0 var(--gold)}
.lb tr.r1 td:last-child{border-right:2px solid var(--gold)}
.lb tr.r1 .rk{color:var(--gold)}
.lb tr.r1 td.px.pick{background:var(--gold);outline:none;border-radius:0}
.lb tr.r1 td.px.pick b,.lb tr.r1 td.px.pick small{color:#0d1117;opacity:1}
.rec{background:var(--panel);border:2px solid var(--gold);border-radius:14px;padding:12px 14px;margin-bottom:12px;box-shadow:0 0 18px rgba(242,201,76,.18)}
.rec .rh{font-size:12px;color:var(--gold);font-weight:800;text-transform:uppercase;letter-spacing:.06em}
.rec .main{display:flex;flex-wrap:wrap;align-items:center;gap:14px;margin-top:6px}
.rec .main .coin1{font-size:30px;font-weight:800}
.rec .main .fav{font-size:20px;padding:5px 14px}
.rec .main .clock1{margin-left:auto;font-size:30px;font-weight:800;font-variant-numeric:tabular-nums}
.rec .stats{display:flex;flex-wrap:wrap;gap:10px;margin-top:10px}
.rec .stats div{flex:1;min-width:110px;background:var(--bg);border:1px solid var(--line);border-radius:10px;padding:7px 10px;text-align:center}
.rec .stats small{display:block;color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.04em}
.rec .stats strong{font-size:22px;font-variant-numeric:tabular-nums}
.rec .advice{margin-top:10px;font-size:14px;line-height:1.7}
.rec .advice b{color:var(--text)}
.tg{display:inline-block;font-size:11px;font-weight:800;padding:1px 7px;border-radius:4px;margin-right:6px;color:#0d1117}
.tg.s{background:var(--green)}.tg.x{background:var(--red)}.tg.h{background:var(--gold)}
.rec .nexts{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px;margin-top:12px}
.rec .nx{border:1px solid var(--line);border-radius:10px;padding:8px 10px;font-size:13px;line-height:1.6}
.rec .nx .ph{display:flex;align-items:center;gap:8px;font-size:15px}
.rec .nx .pt{margin-left:auto;font-weight:800;font-variant-numeric:tabular-nums}
.rec .none{color:var(--dim);font-size:15px;margin-top:6px}
.big div.hl{border-color:var(--gold)}
.mline{margin-top:10px;padding-top:8px;border-top:1px solid var(--line);font-size:12px;color:var(--dim);line-height:1.6}
.mline b{color:var(--text)}
.dots i{display:inline-block;width:12px;height:12px;border-radius:50%;margin-right:3px;vertical-align:middle}
.dots i.u{background:var(--green)}.dots i.d{background:var(--red)}
td.sf{font-size:22px;font-weight:800}
.sf-hi{color:var(--green)}.sf-mid{color:var(--amber)}.sf-lo{color:var(--red)}
#proof .mline{border-top:none;margin-top:0;padding-top:0;font-size:13px}
.split{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px}
.split .lb{margin-bottom:0;height:300px;display:flex;flex-direction:column}
.split .lb>div:last-child{flex:1;overflow:auto}.split .lb th{position:sticky;top:0;z-index:1;background:var(--panel)}
.rec{height:450px;overflow-y:auto}
@media (max-width:820px){.split{grid-template-columns:1fr}.rec{height:640px}}
.lb tr.bid td{animation:bidpulse 1.6s ease-in-out infinite;border-top:2px solid var(--gold);border-bottom:2px solid var(--gold)}
.lb tr.bid td:first-child{border-left:2px solid var(--gold)}.lb tr.bid td:last-child{border-right:2px solid var(--gold)}
@keyframes bidpulse{0%,100%{background:rgba(63,185,80,.14)}50%{background:rgba(242,201,76,.32)}}
.lb.buyb{border-color:var(--green)}.lb.buyb .lbhead b{color:var(--green)}
.lb.sellb{border-color:#ff7b72}.lb.sellb .lbhead b{color:#ff7b72}
.lb .why{font-size:11px;color:var(--dim)}
.lb td.empty{color:var(--dim);padding:14px;text-align:left}
.pickbox{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:10px;margin-bottom:12px}
.pk{background:var(--bg);border:1px solid var(--line);border-radius:10px;padding:8px 10px;text-align:center}.pk.go{border:2px solid var(--gold);box-shadow:0 0 10px rgba(242,201,76,.3)}
.pk .c{font-size:18px;font-weight:800}.pk .sd{font-size:22px;font-weight:900;margin:2px 0}.pk .sd.yes{color:var(--green)}.pk .sd.no{color:#ff7b72}
.pk.gr{position:relative;overflow:hidden;isolation:isolate;border:0;padding:10px 12px;background:var(--green);animation:cglow 2s ease-in-out infinite}.pk.gr::before{content:"";position:absolute;left:50%;top:50%;width:300%;aspect-ratio:1;z-index:-2;background:conic-gradient(from 0deg,transparent 0 70%,rgba(63,185,80,.4) 80%,#aaffb8 88%,#fff 90%,transparent 92%);transform:translate(-50%,-50%);animation:cspin 2.4s linear infinite}.pk.gr.both::before{background:conic-gradient(from 0deg,transparent 0 30%,#aaffb8 38%,#fff 40%,transparent 42% 80%,#aaffb8 88%,#fff 90%,transparent 92%);animation-duration:1.6s}.pk.gr.go::before{background:conic-gradient(from 0deg,transparent 0 30%,#f2c94c 38%,#fff 40%,transparent 42% 80%,#aaffb8 88%,#fff 90%,transparent 92%)}.pk.gr::after{content:"";position:absolute;inset:3px;border-radius:8px;z-index:-1;background:linear-gradient(rgba(63,185,80,.13),rgba(63,185,80,.13)),repeating-linear-gradient(90deg,transparent 0 22px,rgba(63,185,80,.10) 22px 23px),repeating-linear-gradient(0deg,transparent 0 22px,rgba(63,185,80,.10) 22px 23px),var(--bg)}.pk.gr{background:rgba(63,185,80,.25)}@keyframes cspin{to{transform:translate(-50%,-50%) rotate(360deg)}}@keyframes cglow{0%,100%{box-shadow:0 0 6px rgba(63,185,80,.35)}50%{box-shadow:0 0 18px rgba(63,185,80,.75)}}
.bdg{display:inline-block;font-size:11px;font-weight:800;padding:1px 6px;border-radius:4px;background:var(--green);color:#0d1117;margin:2px 2px 0}.pk .sfl{font-size:13px;font-weight:700;color:var(--dim)}
.fy1{color:#d4dc4a}.fy2{color:#e3b341}.fy3{color:#ff9a3c}.pk .fzt{white-space:nowrap;font-size:10px;font-weight:900;letter-spacing:.04em;margin-top:1px}.pk .fzt.fy3,.pk .fzt.wp-lo{animation:fzblink 1s step-end infinite}@keyframes fzblink{50%{opacity:.35}}.pk .flip{font-size:12px;white-space:nowrap;font-weight:800;margin-top:2px}.rstar{color:#ff2d2d;font-size:26px;line-height:1;vertical-align:-3px;text-shadow:0 0 8px rgba(255,45,45,.8)}.pk .vs{font-size:13px;font-weight:900;letter-spacing:.03em;margin-top:4px}.pk .vsd{font-size:15px;font-weight:800;white-space:nowrap}.pk .vsd span{font-size:12px}.pk .vs.up,.pk .vsd.up{color:var(--green)}.pk .vs.dn,.pk .vsd.dn{color:#ff7b72}.pk .tgt{font-size:11px;color:var(--dim);margin-bottom:2px}.pk .tgt b{color:var(--text)}.pk .pr{font-size:14px;font-weight:700;color:var(--dim);margin-top:2px}.pk .pr b{font-size:20px;font-weight:800}.pk .wl{font-size:11px;font-weight:800;color:var(--dim);text-transform:uppercase;letter-spacing:.05em;margin-top:2px}.pk .p{font-size:28px;font-weight:800;line-height:1.1}.pk small{display:block;color:var(--dim);font-size:11px;margin-top:2px}
</style></head><body>
<header>
  <h1>Markets Edge Board</h1>
  <span id="status" class="pill">connecting…</span>
  <span id="count" class="pill"></span>
  <span id="tally" class="pill"></span>
  <span id="cover" class="pill"></span>
</header>
<div class="wrap">
  <div class="pickbox" id="picks"></div>
  <div class="lb fx">
    <div class="lbhead"><b>Watch list — coins stay in place</b><span style="color:var(--dim);font-size:12px">cells flash green/red when they change · Rank = position on the live board</span></div>
    <div style="overflow-x:auto"><table>
      <thead><tr><th class="l">Coin</th><th class="l">Rank</th><th>YES price</th><th>NO price</th><th>Model value</th><th>Safety</th><th>Win %</th><th>Side Favored</th><th>Time left</th><th>Favored</th><th>Edge ¢</th><th class="l">Signal</th><th class="l">Buy</th>
      <th>Book</th><th>Sprd</th></tr></thead>
      <tbody id="fxrows"></tbody></table></div>
  </div>
  <div class="rec" id="rec"></div>
  <div class="split">
    <div class="lb buyb">
      <div class="lbhead"><b>▲ Bid on these</b><span>meets your bid rules · best first</span></div>
      <div style="overflow-x:auto"><table>
        <thead><tr><th class="l">Coin</th><th class="l">Buy</th><th>Model value</th><th>Edge ¢</th><th>Safety</th><th>Win %</th><th>Time left</th></tr></thead>
        <tbody id="buyrows"></tbody></table></div>
    </div>
    <div class="lb sellb">
      <div class="lbhead"><b>▼ Close / sell these</b><span>if you hold it · bid is worth more than the model</span></div>
      <div style="overflow-x:auto"><table>
        <thead><tr><th class="l">Coin</th><th class="l">Sell</th><th>Model value</th><th>Gain ¢</th><th>Win %</th><th>Time left</th></tr></thead>
        <tbody id="sellrows"></tbody></table></div>
    </div>
  </div>
  <div class="lb">
    <div class="lbhead"><b>Live board — best bid on top</b></div>
    <div style="overflow-x:auto"><table>
      <thead><tr><th class="l">#</th><th class="l">Coin</th><th>YES price</th><th>NO price</th><th>Model value</th><th>Safety</th><th>Win %</th><th>Side Favored</th><th>Time left</th><th>Favored</th><th>Edge ¢</th><th class="l">Signal</th><th class="l">Buy</th>
      <th>Book</th><th>Sprd</th></tr></thead>
      <tbody id="lbrows"></tbody></table></div>
  </div>
  <div class="lb">
    <div class="lbhead"><b>Up / down tally — like red &amp; black</b><span id="tsum" style="color:var(--dim);font-size:12px"></span></div>
    <div style="overflow-x:auto"><table>
      <thead><tr><th class="l">Coin</th><th>Ups</th><th>Downs</th><th>Up %</th><th class="l">Last 10 (oldest → newest)</th><th>Streak</th><th>Matches BTC</th></tr></thead>
      <tbody id="tallyrows"></tbody></table></div>
  </div>
  <details class="sec" id="secCards"><summary>15-minute window detail cards</summary><div class="wins" id="wins"></div></details>
  <details class="sec" id="secCoins"><summary>Coin overview</summary><div class="coins" id="coins"></div></details>
  <details class="sec" id="secCrit"><summary>Signal criteria (your bid rules)</summary>
  <div class="box"><h2>Signal criteria</h2><div class="controls">
    <label><span class="dot d-g"></span>Bid when edge ≥ <input id="cBid" type="number" step="0.5" value="1.5">¢</label>
    <label><span style="color:var(--gold)">★</span> Bid when win chance ≥ <input id="cModel" type="number" step="1" value="85">% on the favored side</label>
    <label><span style="color:var(--gold)">▢</span> Gold outline at <select id="cGold"><option value="2" selected>2+</option><option value="3">3+</option><option value="4">all 4</option></select> criteria</label>
    <label><span class="dot d-y"></span>Close when edge ≥ <input id="cClose" type="number" step="0.5" value="0.5">¢</label>
    <label><span class="dot d-r"></span>Avoid when edge ≤ <input id="cAvoid" type="number" step="0.5" value="-1">¢</label>
    <label>…or spread &gt; <input id="cSpread" type="number" step="1" value="5">¢</label>
    <label>Min time left <input id="cMinT" type="number" step="10" value="60">s</label>
    <label><input id="cBeep" type="checkbox"> Beep on new green</label>
  </div></div></details>

  <details class="sec" id="secAll"><summary>Full detail table &amp; filters</summary>
  <div class="box"><h2>Filters</h2><div class="controls">
    <label>Coin <select id="fCoin"><option value="">All</option></select></label>
    <label>Type <select id="fKind"><option value="">All</option><option>15m</option><option>above/below</option><option>range</option><option>other</option></select></label>
    <label>Closes within <select id="fTime">
      <option value="900">15 min</option><option value="3600" selected>1 hour</option>
      <option value="14400">4 hours</option><option value="86400">24 hours</option></select></label>
    <label>Show <select id="fSig"><option value="all">All signals</option><option value="gy">Green + yellow</option><option value="g">Green only</option><option value="nored">Hide red</option></select></label>
    <label>Ask between <input id="fLo" type="number" value="2"> – <input id="fHi" type="number" value="98">¢</label>
    <label>Sort <select id="fSort">
      <option value="sig">Signal, then edge</option><option value="win">Win chance</option><option value="edge">Best edge</option><option value="time">Closing soonest</option>
      <option value="vol">Volume</option><option value="z">Closest to strike</option></select></label>
  </div></div>

  <div class="tbl"><table>
    <thead><tr>
      <th class="l">Signal</th><th class="l">Market</th><th>Win %</th><th>Closes in</th><th>Strike</th><th>Spot</th><th>Dist</th><th>σ-dist</th>
      <th>Yes bid/ask</th><th>No ask</th><th>Spread</th><th>Mkt %</th><th>Model %</th><th>Mkt vs model</th>
      <th>Best</th><th>Edge ¢</th><th>EV %</th><th>Kelly</th><th>Vol 24h</th><th>OI</th>
    </tr></thead><tbody id="rows"></tbody></table></div>
  </details>
  <div class="box" id="proof"></div>
  <div class="err" id="errs"></div>
  <div class="foot">
    <span class="dot d-g"></span><b>Green / BID</b>: the favored side (the one most likely to win) has a win chance at or above your model minimum, the spread is tight enough and there's enough time left.
    <span class="dot d-y"></span><b>Yellow / CLOSE</b>: the favored side is 70%+ to win but under your minimum; watch it.
    <span class="dot d-r"></span><b>Red / AVOID</b>: no side is a clear favorite, spread too wide, or too close to the bell. Uncolored rows are neutral.
    <b>Best bid box</b>: the windows whose favored side is most likely to win right now, favoring 2–12 minutes left (under 1 minute is skipped). <b>Model value</b> = what the contract is worth to the model (its win % in cents). <b>Confidence range</b> = the win % if volatility turns out 25% lower or higher than measured; a tight range means a steadier call. <b>SELL</b> when your side's bid reaches model value; <b>OR HOLD</b> to the end when win % is 93%+; <b>BAIL</b> once win % falls below what you paid.<br><b>Bid on these</b> (left): every coin that's green under your bid rules, best first. <b>Close / sell these</b> (right): any side whose current bid, after Kalshi's fee, is worth at least 1¢ more than the model's value. If you hold that side, selling now beats holding to the end on average. TAKE PROFIT means the market is overpaying; BAIL means the model now favors the other side. The board can't see your positions, so it lists every side that qualifies.<br>
    <b>The model</b> combines volatility (with fat tails for sudden jumps), recent momentum, Kalshi order-book lean, and the market's own price. It scores itself against every finished 15-minute window (plus ~4½ hours of history pulled at startup) and re-tunes how much weight each factor gets every 5 minutes. The accuracy score is a Brier score: lower is better; if it is lower than the market's, the model is adding real information.<br>
    <b>Up / down tally</b>: how each coin's recent 15-minute windows actually settled (green = closed UP, red = DOWN), from Kalshi's results over roughly the last 4½ hours plus every window since. Like roulette, one coin's past streak doesn't predict its next window, but coins moving together is real: "Matches BTC" shows how often a coin settles the same way as Bitcoin.<br>
    <b>Safety</b> (0–100): the worst-case win chance from the confidence range, minus points for a wide spread, under 2 minutes left, negative edge, and momentum or order book against your side. Green 85+, yellow 70–84, red under 70.<br>
    <span style="color:#ff2d2d">★ Red star</span> (top boxes): almost certain not to flip: win % 95%+, worst case 92%+, price 2+ standard moves (σ) on the right side of the target, 30s+ left and a tight spread. "To flip" shows how far the coin must move against you: green 2σ+; yellow is split in three, Yellow 1 (1.5–2σ, bid zone), Yellow 2 (1–1.5σ, hold), Yellow 3 (0.5–1σ, get ready to sell); red under 0.5σ (sell). Pulse also sells at once if win % falls under 60%. Not a guarantee: news can still move it.<br>
    <b>Win %</b>: the model's chance the recommended side (YES or NO) settles in your favor — green at or above your model minimum, yellow 70%+, red below 70%.
    <span style="color:var(--gold)">★ Gold outline</span>: meets 2+ of the four criteria — EDGE (edge ≥ bid cutoff), MODEL (model probability on that side ≥ your minimum), MOM (1m &amp; 5m spot momentum agree with that side), BOOK (Kalshi order-book depth leans that way, 15-min markets).<br>
    <b>Model %</b> is the chance the contract settles YES from spot, strike, time left and recent realized volatility (no drift).
    <b>Edge ¢</b> = model probability − ask − Kalshi taker fee, per $1 contract, on the favored side. <b>EV %</b> is edge as a return on cost.
    <b>Kelly</b> is the full-Kelly fraction; most people bet ¼ of it or less. <b>σ-dist</b> = standard deviations from strike (|σ| &lt; 0.5 is coin-flip territory).
    Kalshi settles on CF Benchmarks indexes (15-min markets use a 60-second average); spot here is Coinbase/Binance.US, so small gaps are normal.
    This is a model, not a guarantee — news, thin books and fat tails can make it wrong.
  </div>
</div>
<script>
const $=id=>document.getElementById(id);
let recvAt=0,DATA=null,lastOk=0,selCoin="",seenBid=new Set(),firstRender=true,audio=null;
const fmtPx=v=>v==null?"–":(v>=1000?v.toLocaleString(undefined,{maximumFractionDigits:2}):v>=1?v.toFixed(v>=100?2:4):v>=0.01?v.toFixed(5):v.toPrecision(4));
const pct=(v,d=2)=>v==null?"–":(v>0?"+":"")+v.toFixed(d)+"%";
const cls=v=>v==null?"":(v>0?"pos":v<0?"neg":"");
const c=v=>v==null?"–":(v<0.1&&v>0?(v*100).toFixed(1):Math.round(v*100));
function fmtT(s){if(s==null)return"–";if(s<=0)return"closed";const h=Math.floor(s/3600),m=Math.floor(s%3600/60),x=Math.floor(s%60);
 return h?h+"h "+m+"m":m+":"+String(x).padStart(2,"0");}
function fmtN(v){if(v==null)return"–";if(v>=1e6)return(v/1e6).toFixed(1)+"M";if(v>=1e3)return(v/1e3).toFixed(1)+"k";return Math.round(v);}
const IDS=["cModel","cGold","cBid","cClose","cAvoid","cSpread","cMinT","cBeep","fCoin","fKind","fTime","fSig","fLo","fHi","fSort"];
IDS.forEach(id=>{const el=$(id);
  try{const v=localStorage.getItem("ceb2_"+id);if(v!=null&&id!=="fCoin"){if(el.type==="checkbox")el.checked=v==="1";else el.value=v;}}catch(e){}
  el.addEventListener("input",()=>{try{localStorage.setItem("ceb2_"+id,el.type==="checkbox"?(el.checked?"1":"0"):el.value)}catch(e){};if(id==="fCoin")selCoin=el.value;render();});
});
["secCards","secCoins","secCrit","secAll"].forEach(id=>{const d=$(id);
  try{const v=localStorage.getItem("ceb2_"+id);if(v!=null)d.open=v==="1";}catch(e){}
  d.addEventListener("toggle",()=>{try{localStorage.setItem("ceb2_"+id,d.open?"1":"0")}catch(e){}});});
let prevRank={},prevTop=null;
let FL={},FLX=false;
function fl(coin,k,v){if(!FLX||v==null)return"";const key=coin+k,p=FL[key];FL[key]=v;return p==null||p===v?"":(v>p?"up":"dn");}
function favCell(m){if(!m.best_side)return '<td><span class="fav none">–</span></td>';
  return '<td><span class="fav '+m.best_side+'">'+(m.best_side==="yes"?"YES ▲":"NO ▼")+'</span></td>';}
function cells(m,crit,s,sd,b){const sf=safety(m,crit);return \`<td class="px yes \${m.best_side==="yes"?"pick":m.best_side?"dim":""} \${fl(m.coin,"ya",m.yes_ask)}"><b>\${c(m.yes_ask)}¢</b><small>bid \${c(m.yes_bid)}</small></td>
      <td class="px no \${m.best_side==="no"?"pick":m.best_side?"dim":""} \${fl(m.coin,"na",m.no_ask)}"><b>\${c(m.no_ask)}¢</b><small>bid \${c(m.no_bid)}</small></td>
      <td class="mval"><b>\${m.cr.sideP==null?"–":(m.cr.sideP*100).toFixed(0)+"¢"}</b></td>
      <td class="sf \${sfClass(sf)}" title="Safety: worst-case win chance, minus penalties for wide spread, little time left, negative edge, and momentum/book against you">\${sf==null?"–":sf}</td>
      <td class="wp \${wpClass(m.cr.sideP,crit)}">\${m.cr.sideP==null?"–":(m.cr.sideP*100).toFixed(0)+"%"}</td>
      <td class="sidefav"><span class="side \${m.best_side||""}">\${m.best_side?(m.best_side==="yes"?"YES":"NO"):"–"}</span></td>
      <td class="tleft \${tcls(m.tl)}"><b>\${fmtT(m.tl)}</b><span class="tbar"><i style="width:\${Math.max(0,Math.min(100,(m.tl/((m.elapsed!=null?m.elapsed:0)+m.tl||900))*100)).toFixed(0)}%"></i></span></td>
      \${favCell(m)}
      <td class="\${cls(m.best_edge)} \${fl(m.coin,"ed",m.best_edge)}"><b>\${m.best_edge==null?"–":(m.best_edge*100).toFixed(1)}</b></td>
      <td class="l"><span class="sigl s-\${m.sig}">\${SIGLBL[m.sig]}</span>\${chips(m.cr)}</td>
      <td class="l buy">\${sd?'<span class="side '+m.best_side+'">'+(m.best_side==="yes"?"UP":"DOWN")+" @ "+c(sd.ask)+"¢</span>":"–"}</td>
      <td title="Order book: share of depth on YES">\${b&&b.imb!=null?'<span class="bk"><i style="width:'+(b.imb*100).toFixed(0)+'%"></i></span> '+(b.imb*100).toFixed(0)+"%":"–"}</td>
      <td>\${m.spread==null?"–":Math.round(m.spread*100)}</td>\`;}
function safety(m,crit){
  if(!m.best_side||m.cr.sideP==null)return null;
  const r=sideRange(m); let v=(r?r[0]:m.cr.sideP)*100;          // worst-case win chance
  if(m.spread!=null&&m.spread>0.03)v-=(m.spread*100-3)*2;        // wide spread = hard to get out
  if(m.tl!=null&&m.tl<60)v-=15; else if(m.tl!=null&&m.tl<120)v-=5; // too close to the bell
  if(m.best_edge!=null&&m.best_edge<0)v-=10;                     // overpriced
  const d=(m.dir||0)*(m.best_side==="yes"?1:-1),st=DATA.coins[m.coin]||{};
  if(d&&st.chg_5m!=null&&st.chg_5m*d<0)v-=5;                     // momentum against you
  const im=m.book&&m.book.imb; if(im!=null&&(m.best_side==="yes"?im<0.45:im>0.55))v-=5; // order book against you
  return Math.max(0,Math.min(100,Math.round(v)));
}
function fmtD(v){return v>=10000?Math.round(v).toLocaleString():v>=100?v.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2}):v.toPrecision(6);}
function flipZone(z){return z>=2?['wp-hi','']:z>=1.5?['fy1','Y1 · BID ZONE']:z>=1?['fy2','Y2 · HOLD']:z>=0.5?['fy3','Y3 · GET READY TO SELL']:['wp-lo','RED · SELL'];}
function tcls(t){return t==null?"":t>600?"t-red":t>300?"t-yel":"t-grn";}
function sfClass(v){return v==null?"":v>=85?"sf-hi":v>=70?"sf-mid":"sf-lo";}
function fixedOrder(a,b){const P=["GOLD","SILVER","OIL","BRENT","NATGAS","COPPER","PLATINUM","PALLADIUM","EURUSD","GBPUSD","USDJPY","SP500","NASDAQ","DOW"];
  const ia=P.indexOf(a),ib=P.indexOf(b);return (ia<0?99:ia)-(ib<0?99:ib)||a.localeCompare(b);}

function pickScore(m){
  if(!m.best_side||m.cr.sideP==null||m.sig!=="bid"||m.tl==null)return -1;
  const t=m.tl, tf=t<60?0:t<120?0.4:t>720?0.8:1; // under 1 min: too late; very early in window: less certain
  return tf*(m.cr.sideP*100+(m.gold?2:0));
}
function sideRange(m){if(m.p_lo==null)return null;return m.best_side==="yes"?[m.p_lo,m.p_hi]:[1-m.p_hi,1-m.p_lo];}
function sellLines(m){
  const sd=m[m.best_side],ask=Math.round(sd.ask*100),wp=m.cr.sideP||0,fair=Math.min(99,Math.floor(wp*100)),nm=m.best_side==="yes"?"YES":"NO";
  return '<div><span class="tg s">SELL</span>when the '+nm+' bid reaches <b>'+fair+'¢</b> (model value; locks in the edge)</div>'+
    (wp>=0.93?'<div><span class="tg h">OR HOLD</span>to the end; pays <b>100¢</b> if it wins</div>':'')+
    '<div><span class="tg x">BAIL</span>if win % drops below what you paid (<b>'+ask+'%</b> at this price)</div>';
}
function modelLine(){
  const M=DATA.model||{};
  if(!M.n)return '<div class="mline">Model: volatility + fat tails + momentum + order book + market blend · learning from past windows (loading history…)</div>';
  let t='Model learned from <b>'+M.windows+'</b> past 15-min windows ('+M.n+' snapshots)';
  if(M.brierMkt!=null&&M.brierModelVsMkt!=null){const better=M.brierModelVsMkt<M.brierMkt;
    t+=' · accuracy score <b>'+M.brierModelVsMkt.toFixed(3)+'</b> vs market '+M.brierMkt.toFixed(3)+' <b class="'+(better?'pos':'neg')+'">('+(better?'beating the market':'not beating the market yet')+')</b>';}
  if(M.hit85!=null)t+=' · strong calls (85%+) right <b>'+(M.hit85*100).toFixed(0)+'%</b> of the time ('+M.n85+')';
  if(M.n<150)t+=' · still collecting (tuning starts at 150 snapshots)';
  return '<div class="mline">'+t+'</div>';
}
function renderPicks(list,crit){$("picks").innerHTML=[...list].sort((a,b)=>fixedOrder(a.coin,b.coin)).map(m=>{const s=m.best_side,p=m.cr.sideP;
  if(!s||p==null)return '<div class="pk"><div class="c">'+m.coin+'</div><small>waiting for data…</small></div>';
  const sf=safety(m,crit),wg=p>=crit.model,sg=sf!=null&&sf>=85,rg=sideRange(m),zs=m.z==null?null:m.z*(s==="yes"?1:-1),lk=zs!=null&&zs>=2&&p>=0.95&&(!rg||rg[0]>=0.92)&&m.tl>=30&&(m.spread==null||m.spread<=0.03);
  return '<div class="pk'+(m.sig==="bid"?' go':'')+(wg||sg?' gr':'')+(wg&&sg?' both':'')+(lk?' lock':'')+'"><div class="c">'+m.coin+(lk?' <span class="rstar" title="Almost certain: would need a big move to flip">★</span>':'')+'</div><div class="sd '+s+'">'+(s==="yes"?"YES ▲":"NO ▼")+'</div><div class="wl">Win %</div><div class="p '+wpClass(p,crit)+'">'+Math.round(p*100)+'%</div>'+(m.spot!=null&&m.strike?(()=>{const d=m.spot-m.strike,up=d>=0;return '<div class="vs '+(up?'up':'dn')+'">'+(up?'▲ ABOVE':'▼ BELOW')+' target</div><div class="vsd '+(up?'up':'dn')+'">'+(up?'+':'−')+'$'+fmtD(Math.abs(d))+' <span>('+(up?'+':'')+(m.dist_pct||0).toFixed(2)+'%)</span></div><div class="tgt">Now <b>$'+fmtD(m.spot)+'</b></div><div class="tgt">Target <b>$'+fmtD(m.strike)+'</b></div>';})():'')+(zs!=null?(()=>{const fz=flipZone(zs);return '<div class="flip '+fz[0]+'">To flip: '+(m.dist_pct>=0?'▼':'▲')+Math.abs(m.dist_pct||0).toFixed(2)+'% ('+zs.toFixed(1)+'σ)</div>'+(fz[1]?'<div class="fzt '+fz[0]+'">'+fz[1]+'</div>':'');})():'')+'<div class="sfl">Safety <b class="'+sfClass(sf)+'">'+(sf==null?'–':sf)+'</b></div>'+(wg||sg?'<div>'+(wg?'<span class="bdg">WIN ✓</span>':'')+(sg?'<span class="bdg">SAFE ✓</span>':'')+'</div>':'')+'<small>chance it closes '+(s==="yes"?"YES":"NO")+'</small><div class="pr">Price <b class="'+(m.best_edge==null?'':m.best_edge>=crit.bid?'wp-hi':m.best_edge>=0?'wp-mid':'wp-lo')+'">'+c(m[s].ask)+'¢</b></div><small class="'+tcls(m.tl)+'">'+fmtT(m.tl)+' left'+(m.sig==="bid"?' · <b>BID</b>':'')+'</small></div>';}).join('');}
function renderTally(){
  const T=DATA.tally; if(!T){return;}
  const rows=[...T.coins].sort((a,b)=>fixedOrder(a.coin,b.coin));
  $("tallyrows").innerHTML=rows.map(r=>'<tr><td class="l"><b>'+r.coin+'</b></td><td class="pos">'+r.ups+'</td><td class="neg">'+r.downs+'</td>'+
    '<td>'+(r.n?Math.round(r.ups/r.n*100)+'%':'–')+'</td>'+
    '<td class="l dots">'+r.last10.map(y=>'<i class="'+(y?'u':'d')+'" title="'+(y?'UP':'DOWN')+'"></i>').join('')+'</td>'+
    '<td><b class="'+(r.streak.side?'pos':'neg')+'">'+r.streak.len+' '+(r.streak.side?'UP':'DOWN')+'</b></td>'+
    '<td>'+(r.btcMatch==null?(r.coin==="BTC"?'—':'–'):Math.round(r.btcMatch*100)+'%')+'</td></tr>').join('')||'<tr><td class="l" colspan="7" style="color:var(--dim);padding:14px">Loading past results…</td></tr>';
  $("tsum").innerHTML=T.windows?('All coins closed on the same side in <b>'+T.allSame+'</b> of '+T.windows+' windows ('+Math.round(T.allSame/T.windows*100)+'%) · on average '+Math.round((T.avgAgree||0)*100)+'% of coins agree'):'';
}
function kfee(p){return Math.ceil(0.07*p*(1-p)*100-1e-9)/100;}
function renderSplit(list,crit){
  const buys=list.filter(m=>m.sig==="bid"&&m.best_side).sort((a,b)=>(pickScore(b)-pickScore(a))||(b.best_edge-a.best_edge));
  $("buyrows").innerHTML=buys.map(m=>{const sd=m[m.best_side],sf=safety(m,crit),rg=sideRange(m);
    return '<tr class="bid'+(m.gold?' gold':'')+'"><td class="l"><b>'+m.coin+'</b>'+(m.gold?'<span class="star">★</span>':'')+'</td>'+
      '<td class="l"><span class="fav '+m.best_side+'">'+(m.best_side==="yes"?"YES ▲":"NO ▼")+'</span> <b>@ '+c(sd.ask)+'¢</b></td>'+
      '<td><b>'+((m.cr.sideP||0)*100).toFixed(0)+'¢</b>'+(rg?'<div class="why">'+(rg[0]*100).toFixed(0)+'–'+(rg[1]*100).toFixed(0)+'%</div>':'')+'</td>'+
      '<td class="pos"><b>+'+(m.best_edge*100).toFixed(1)+'</b></td>'+
      '<td class="sf '+sfClass(sf)+'">'+(sf==null?'–':sf)+'</td>'+
      '<td class="wp '+wpClass(m.cr.sideP,crit)+'">'+(m.cr.sideP==null?'–':(m.cr.sideP*100).toFixed(0)+'%')+'</td>'+
      '<td class="tleft '+tcls(m.tl)+'"><b>'+fmtT(m.tl)+'</b></td></tr>';}).join('')
    ||'<tr><td class="empty" colspan="7">Nothing meets your bid rules right now. Wait for a green row.</td></tr>';
  const sells=[];
  list.forEach(m=>{if(m.model_p==null)return;
    [["yes",m.yes_bid,m.model_p],["no",m.no_bid,1-m.model_p]].forEach(([side,bid,p])=>{
      if(bid==null||bid<0.03)return;
      const gain=bid-kfee(bid)-p;            // what selling now gets you vs. what holding is worth
      if(gain>=0.01)sells.push({m,side,bid,p,gain});});});
  sells.sort((a,b)=>b.gain-a.gain);
  $("sellrows").innerHTML=sells.map(x=>{const m=x.m;
    return '<tr class="avoid"><td class="l"><b>'+m.coin+'</b></td>'+
      '<td class="l"><span class="fav '+x.side+'">'+(x.side==="yes"?"YES":"NO")+'</span> <b>@ '+c(x.bid)+'¢ bid</b><div class="why">'+
        (x.p<0.5?'BAIL: model says it is more likely to lose':'TAKE PROFIT: the market pays more than it is worth')+'</div></td>'+
      '<td><b>'+(x.p*100).toFixed(0)+'¢</b></td>'+
      '<td class="pos"><b>+'+(x.gain*100).toFixed(1)+'</b></td>'+
      '<td class="wp '+wpClass(x.p,crit)+'">'+(x.p*100).toFixed(0)+'%</td>'+
      '<td class="tleft '+tcls(m.tl)+'"><b>'+fmtT(m.tl)+'</b></td></tr>';}).join('')
    ||'<tr><td class="empty" colspan="6">Nothing to sell right now. If you hold a contract, holding is worth more than selling.</td></tr>';
}
function renderRec(list){
  const ps=list.map(m=>({m:m,s:pickScore(m)})).filter(x=>x.s>0).sort((a,b)=>b.s-a.s).slice(0,3);
  if(!ps.length){$("rec").innerHTML='<div class="rh">Best bid right now</div><div class="none">No good bids right now. No side is a strong enough favorite to win with enough time left; check back next window.</div>';return;}
  const m=ps[0].m,sd=m[m.best_side],wp=m.cr.sideP||0,rg=sideRange(m);
  let h='<div class="rh">★ Best bid right now</div>'+
    '<div class="main"><span class="coin1">'+m.coin+'</span><span class="fav '+m.best_side+'">BUY '+(m.best_side==="yes"?"YES ▲":"NO ▼")+'</span>'+
    '<span class="clock1 '+tcls(m.tl)+'">'+fmtT(m.tl)+'</span></div>'+
    '<div class="stats">'+
      '<div><small>Price</small><strong>'+c(sd.ask)+'¢</strong></div>'+
      '<div><small>Model value</small><strong class="wp-hi">'+(wp*100).toFixed(0)+'¢</strong></div>'+
      '<div><small>Confidence range</small><strong>'+(rg?(rg[0]*100).toFixed(0)+'–'+(rg[1]*100).toFixed(0)+'%':'–')+'</strong></div>'+
      '<div><small>Edge</small><strong class="pos">+'+(m.best_edge*100).toFixed(1)+'¢</strong></div>'+
      '<div><small>Safety</small><strong class="'+sfClass(safety(m))+'">'+(safety(m)==null?'–':safety(m))+'/100</strong></div>'+
    '</div>'+
    '<div class="advice">'+sellLines(m)+'</div>';
  if(ps.length>1){
    h+='<div class="nexts">';
    ps.slice(1).forEach((x,i)=>{const n=x.m,nsd=n[n.best_side],nr=sideRange(n);
      h+='<div class="nx"><div class="ph"><b style="color:var(--gold)">#'+(i+2)+'</b><b>'+n.coin+'</b><span class="fav '+n.best_side+'">'+(n.best_side==="yes"?"YES ▲":"NO ▼")+'</span><span class="pt '+tcls(n.tl)+'">'+fmtT(n.tl)+'</span></div>'+
        '<div>Price <b>'+c(nsd.ask)+'¢</b> · model <b>'+((n.cr.sideP||0)*100).toFixed(0)+'¢</b>'+(nr?' ('+(nr[0]*100).toFixed(0)+'–'+(nr[1]*100).toFixed(0)+'%)':'')+' · edge <b class="pos">+'+(n.best_edge*100).toFixed(1)+'¢</b></div>'+
        sellLines(n)+'</div>';});
    h+='</div>';
  }
  $("rec").innerHTML=h;
}
function lbScore(m){
  if(!m.best_side)return -1e9;
  const sigw={bid:3000,close:2000,none:1000,avoid:0}[m.sig]||0;
  return (m.gold?10000:0)+sigw+m.cr.n*100+(m.cr.sideP||0)*1000;
}
function renderBoard(all,crit){
  const cur={};
  all.filter(m=>m.kind==="15m").forEach(m=>{if(!cur[m.coin]||m.tl<cur[m.coin].tl)cur[m.coin]=m;});
  const list=Object.values(cur).sort((a,b)=>lbScore(b)-lbScore(a));
  renderPicks(list,crit);
  renderRec(list);
  renderSplit(list,crit);
  renderTally();
  let h="";const rank={};
  list.forEach((m,i)=>{
    rank[m.coin]=i+1;
    const s=DATA.coins[m.coin]||{},sd=m.best_side?m[m.best_side]:null,b=m.book;
    const pr=prevRank[m.coin],mv=pr==null||pr===i+1?"":(pr>i+1?'<span class="mv up">▲'+(pr-i-1)+'</span>':'<span class="mv dn">▼'+(i+1-pr)+'</span>');
    const top=i===0, isNew=top&&prevTop&&prevTop!==m.coin;
    const cl=(top?"r1 ":"")+m.sig+(m.gold?" gold":"")+(top&&m.sig!=="bid"&&!m.gold?" nobid":"")+(isNew?" new1":"");
    h+=\`<tr class="\${cl}">
      <td class="l rk">\${i+1}\${mv}</td>
      <td class="l"><b>\${m.coin}</b>\${m.gold?'<span class="star">★</span>':""}</td>
      \${cells(m,crit,s,sd,b)}</tr>\`;
  });
  $("lbrows").innerHTML=h||'<tr><td class="l" colspan="15" style="color:var(--dim);padding:14px">Waiting for 15-minute windows…</td></tr>';
  const t=list[0];
  let fx="";FLX=true;
  for(const m of [...list].sort((a,b)=>fixedOrder(a.coin,b.coin))){
    const s=DATA.coins[m.coin]||{},sd=m.best_side?m[m.best_side]:null,b=m.book,r=rank[m.coin];
    fx+=\`<tr class="\${r===1?"r1 ":""}\${m.sig}\${m.gold?" gold":""}"><td class="l"><b>\${m.coin}</b>\${m.gold?'<span class="star">★</span>':""}</td>
      <td class="l rkn">#\${r}\${r===1?' <span style="color:var(--green)">TOP</span>':""}</td>
      \${cells(m,crit,s,sd,b)}</tr>\`;
  }
  FLX=false;
  $("fxrows").innerHTML=fx||'<tr><td class="l" colspan="15" style="color:var(--dim);padding:14px">Waiting for 15-minute windows…</td></tr>';
  prevRank=rank;prevTop=t?t.coin:null;
}
function beep(){try{audio=audio||new (window.AudioContext||window.webkitAudioContext)();const o=audio.createOscillator(),g=audio.createGain();
 o.frequency.value=880;g.gain.value=0.08;o.connect(g);g.connect(audio.destination);o.start();o.stop(audio.currentTime+0.18);}catch(e){}}

function classify(m,crit){
  const p=m.cr?m.cr.sideP:null;
  if(p==null) return (m.spread!=null&&m.spread>crit.spread)?"avoid":"none";
  if(m.spread!=null&&m.spread>crit.spread+1e-9) return "avoid";
  const lvl=p>=crit.model?"bid":p>=0.70?"close":"avoid";   // same cut-offs as the Win % colors
  if(m.tl!=null&&m.tl<crit.minT) return lvl==="bid"?"close":"avoid";
  return lvl;
}
function criteria(m,crit,coins){
  const out={edge:false,model:false,mom:false,book:false,n:0};
  if(!m.best_side)return out;
  const isYes=m.best_side==="yes";
  const sp=m.model_p==null?null:(isYes?m.model_p:1-m.model_p);
  out.sideP=sp;
  out.edge=m.best_edge!=null&&m.best_edge>=crit.bid;
  out.model=sp!=null&&sp>=crit.model;
  const d=(m.dir||0)*(isYes?1:-1), st=coins[m.coin]||{};
  out.mom=d!==0&&st.chg_5m!=null&&st.chg_5m*d>0&&(st.chg_1m==null||st.chg_1m*d>=0);
  const im=m.book&&m.book.imb;
  out.book=im!=null&&(isYes?im>=0.55:im<=0.45);
  out.n=[out.edge,out.model,out.mom,out.book].filter(Boolean).length;
  return out;
}
function wpClass(p,crit){return p==null?"wp-na":p>=crit.model?"wp-hi":p>=0.70?"wp-mid":"wp-lo";}
function wpColor(p,crit){return p==null?"#30363d":p>=crit.model?"var(--green)":p>=0.70?"var(--amber)":"#6e7681";}
function chips(cr){return '<span class="crit"><i class="'+(cr.edge?"on":"")+'" title="Edge meets bid cutoff">EDGE</i><i class="'+(cr.model?"on":"")+'" title="Model probability on this side meets your minimum">MODEL</i><i class="'+(cr.mom?"on":"")+'" title="1m and 5m spot momentum point the same way as this side">MOM</i><i class="'+(cr.book?"on":"")+'" title="Kalshi order book depth leans this way (15m only)">BOOK</i></span>';}
function spark(h,strike){
  if(!h||h.length<3)return "";
  const W=300,H=46,t0=h[0][0],t1=h[h.length-1][0]||t0+1;
  const X=t=>((t-t0)/Math.max(1,t1-t0)*W).toFixed(1),Y=v=>(H-2-v*(H-4)).toFixed(1);
  const line=(i,col)=>{const pts=h.filter(x=>x[i]!=null).map(x=>X(x[0])+","+Y(x[i]));return pts.length>1?'<polyline fill="none" stroke="'+col+'" stroke-width="1.6" points="'+pts.join(" ")+'"/>':"";};
  return '<svg class="spark" viewBox="0 0 '+W+' '+H+'" preserveAspectRatio="none"><line x1="0" x2="'+W+'" y1="'+Y(0.5)+'" y2="'+Y(0.5)+'" stroke="#30363d" stroke-dasharray="3 3"/>'+line(1,"#e3b341")+line(2,"#58a6ff")+'</svg>';
}
function renderWindows(all,crit){
  const cur={};
  all.filter(m=>m.kind==="15m").forEach(m=>{if(!cur[m.coin]||m.tl<cur[m.coin].tl)cur[m.coin]=m;});
  const list=Object.values(cur).sort((a,b)=>(b.cr.n-a.cr.n)||((b.best_edge??-9)-(a.best_edge??-9)));
  let h="";
  for(const m of list){
    const s=DATA.coins[m.coin]||{},cr=m.cr,b=m.book;
    const dur=m.open_ts?Math.max(1,m.t_left+m.elapsed):900, prog=m.elapsed!=null?Math.min(100,Math.max(0,m.elapsed/dur*100)):0;
    const sd=m.best_side?m[m.best_side]:null;
    const up=m.spot!=null&&m.strike!=null?(m.spot>=m.strike):null;
    const gold=cr.n>=crit.gold&&m.sig!=="avoid";
    const call=m.best_side?('<span class="side '+m.best_side+'">'+(m.best_side==="yes"?"UP (YES)":"DOWN (NO)")+' @ '+c(sd.ask)+'¢</span> · edge '+(m.best_edge*100).toFixed(1)+'¢ · model '+(cr.sideP*100).toFixed(1)+'% · '+cr.n+'/4 criteria'):'<span style="color:var(--dim)">waiting for data…</span>';
    h+=\`<div class="win \${m.sig} \${gold?"gold":""}">
      <div class="hd"><span><b>\${m.coin}</b> <span class="sigl s-\${m.sig}">\${SIGLBL[m.sig]}</span>\${gold?'<span class="star">★ GOLD</span>':""}</span><span class="clock \${tcls(m.tl)}">\${fmtT(m.tl)}</span></div>
      <div class="prog"><i style="width:\${prog.toFixed(1)}%"></i></div>
      <div class="winband"><div><div class="lbl">Win chance</div><div class="pctbig \${wpClass(cr.sideP,crit)}">\${cr.sideP==null?"–":(cr.sideP*100).toFixed(0)+"%"}</div></div>
        <div style="flex:1"><div class="lbl">\${m.best_side?(m.best_side==="yes"?"if you buy UP (YES)":"if you buy DOWN (NO)")+" @ "+c(sd.ask)+"¢ · pays "+(sd?(100-Math.round(sd.ask*100)):"")+"¢ profit":"waiting for data"}</div>
        <div class="meter"><i style="width:\${cr.sideP==null?0:(cr.sideP*100).toFixed(0)}%;background:\${wpColor(cr.sideP,crit)}"></i></div></div></div>
      <div class="big">
        <div class="hl"><small>Price \${m.best_side?(m.best_side==="yes"?"YES":"NO"):""}</small><strong>\${sd?c(sd.ask)+"¢":"–"}</strong></div>
        <div><small>Model value</small><strong class="wp-hi">\${cr.sideP==null?"–":(cr.sideP*100).toFixed(0)+"¢"}</strong></div>
        <div><small>Confidence</small><strong>\${(()=>{const r=m.best_side?sideRange(m):null;return r?(r[0]*100).toFixed(0)+"–"+(r[1]*100).toFixed(0)+"%":"–";})()}</strong></div>
        <div><small>Edge</small><strong class="\${cls(m.best_edge)}">\${m.best_edge==null?"–":(m.best_edge>0?"+":"")+(m.best_edge*100).toFixed(1)+"¢"}</strong></div>
      </div>
      <div class="kv">
        <span>Target</span><b>\${fmtPx(m.strike)}</b><span>Spot</span><b class="\${up==null?"":up?"pos":"neg"}">\${fmtPx(m.spot)}</b>
        <span>Spot vs target</span><b class="\${cls(m.dist_pct)}">\${pct(m.dist_pct,3)}</b><span>σ-distance</span><b>\${m.z==null?"–":m.z.toFixed(2)}</b>
        <span>Move to flip</span><b>\${m.need_pct==null?"–":pct(m.need_pct,3)}</b><span>Elapsed</span><b>\${m.elapsed!=null?fmtT(m.elapsed):"–"}</b>
        <span>Window hi / lo</span><b>\${m.win_hi?fmtPx(m.win_hi):"–"} / \${m.win_lo?fmtPx(m.win_lo):"–"}</b><span>Vol 30m/24h</span><b>\${s.ann_vol_30m?s.ann_vol_30m.toFixed(0):"–"}/\${s.ann_vol_24h?s.ann_vol_24h.toFixed(0):"–"}%</b>
        <span>Spot 1m / 5m</span><b><span class="\${cls(s.chg_1m)}">\${pct(s.chg_1m,3)}</span> / <span class="\${cls(s.chg_5m)}">\${pct(s.chg_5m)}</span></b><span>Regime</span><b>\${s.vol_regime==null?"–":s.vol_regime>1.25?'<span class="urgent">expanding</span>':s.vol_regime<0.8?"quiet":"normal"}</b>
        <span>YES bid / ask</span><b>\${c(m.yes_bid)} / \${c(m.yes_ask)}¢</b><span>NO ask</span><b>\${c(m.no_ask)}¢</b>
        <span>Edge YES / NO</span><b><span class="\${cls(m.yes&&m.yes.edge)}">\${m.yes?(m.yes.edge*100).toFixed(1):"–"}</span> / <span class="\${cls(m.no&&m.no.edge)}">\${m.no?(m.no.edge*100).toFixed(1):"–"}</span>¢</b><span>Spread</span><b>\${m.spread==null?"–":Math.round(m.spread*100)+"¢"}</b>
        <span>Price Δ 1m / 3m</span><b><span class="\${cls(m.mid_chg_1m)}">\${m.mid_chg_1m==null?"–":(m.mid_chg_1m*100).toFixed(1)}</span> / <span class="\${cls(m.mid_chg_3m)}">\${m.mid_chg_3m==null?"–":(m.mid_chg_3m*100).toFixed(1)}</span>¢</b><span>Window vol / OI</span><b>\${fmtN(m.volume)} / \${fmtN(m.oi)}</b>
      </div>
      \${b?\`<div style="font-size:11px;color:var(--dim);margin-top:6px;display:flex;justify-content:space-between"><span>YES bids \${fmtN(b.yes_depth)}</span><span>book \${b.imb==null?"–":(b.imb*100).toFixed(0)+"% YES"}</span><span>NO bids \${fmtN(b.no_depth)}</span></div>
      <div class="book"><i class="y" style="width:\${b.imb==null?50:(b.imb*100).toFixed(0)}%"></i><i class="n" style="flex:1"></i></div>\`:""}
      \${spark(m.hist,m.strike)}
      <div style="font-size:10px;color:var(--dim);display:flex;gap:10px"><span style="color:#58a6ff">━ model up %</span><span style="color:#e3b341">━ market up %</span><span>(this window)</span></div>
      <div>\${chips(cr)}</div>
      <div class="call">\${call}</div>
    </div>\`;
  }
  $("wins").innerHTML=h||'<div style="color:var(--dim);padding:6px">No 15-minute windows open right now.</div>';
}
const SIGRANK={bid:0,close:1,none:2,avoid:3};
const SIGLBL={bid:"BID",close:"CLOSE",avoid:"AVOID",none:"–"};

function renderCoins(sigByCoin){
  const el=$("coins"),coins=DATA.coins;let h="";
  const opts=['<option value="">All</option>'];
  const keys=Object.keys(coins).sort((a,b)=>((sigByCoin[b]||{}).bid||0)-((sigByCoin[a]||{}).bid||0)||(DATA.coin_order.indexOf(a)-DATA.coin_order.indexOf(b)));
  for(const k of keys){
    const s=coins[k];const sg=sigByCoin[k]||{};
    opts.push('<option '+(selCoin===k?"selected":"")+'>'+k+'</option>');
    const regime=s.vol_regime==null?"–":(s.vol_regime>1.25?'<span class="urgent">expanding</span>':s.vol_regime<0.8?"quiet":"normal");
    h+=\`<div class="coin \${selCoin===k?"sel":""}" data-c="\${k}">
      <div class="top"><span class="sym">\${k}</span><span class="px">\${fmtPx(s.price)}</span></div>
      <div class="sig"><span class="dot d-g"></span>\${sg.bid||0} <span class="dot d-y" style="margin-left:6px"></span>\${sg.close||0} <span class="dot d-r" style="margin-left:6px"></span>\${sg.avoid||0} <span style="color:var(--dim);margin-left:4px">of \${sg.total||0}</span></div>
      <div class="grid4 num">
        <span>5m</span><b class="\${cls(s.chg_5m)}">\${pct(s.chg_5m)}</b>
        <span>15m</span><b class="\${cls(s.chg_15m)}">\${pct(s.chg_15m)}</b>
        <span>1h</span><b class="\${cls(s.chg_60m)}">\${pct(s.chg_60m)}</b>
        <span>Vol 30m/24h</span><b>\${s.ann_vol_30m?s.ann_vol_30m.toFixed(0):"–"}/\${s.ann_vol_24h?s.ann_vol_24h.toFixed(0):"–"}%</b>
        <span>Regime</span><b>\${regime}</b>
        <span>Feed</span><b>\${s.src||"none"}</b>
      </div></div>\`;
  }
  el.innerHTML=h;
  el.querySelectorAll(".coin").forEach(d=>d.onclick=()=>{selCoin=selCoin===d.dataset.c?"":d.dataset.c;$("fCoin").value=selCoin;render();});
  $("fCoin").innerHTML=opts.join("");$("fCoin").value=selCoin;
}

function render(){
  if(!DATA)return;
  const skew=(performance.now()-recvAt)/1000; // seconds since this data arrived (ignores the device clock)
  const crit={model:+$("cModel").value/100,gold:+$("cGold").value,bid:+$("cBid").value/100,close:+$("cClose").value/100,avoid:+$("cAvoid").value/100,spread:+$("cSpread").value/100,minT:+$("cMinT").value};
  const coin=$("fCoin").value,kind=$("fKind").value,maxT=+$("fTime").value,show=$("fSig").value,
        lo=+$("fLo").value/100,hi=+$("fHi").value/100,sort=$("fSort").value;
  let all=DATA.markets.map(m=>({...m,tl:m.t_left==null?null:m.t_left-skew})).filter(m=>m.tl!=null&&m.tl>0);
  all.forEach(m=>{m.cr=criteria(m,crit,DATA.coins);m.sig=classify(m,crit);m.gold=m.cr.n>=crit.gold&&m.sig!=="avoid";});
  renderBoard(all,crit);
  $("proof").innerHTML='<h2>Proof — how the model\\'s calls have actually done</h2>'+modelLine();
  renderWindows(all,crit);
  const sigByCoin={};
  all.filter(m=>m.tl<=maxT).forEach(m=>{const o=sigByCoin[m.coin]=sigByCoin[m.coin]||{bid:0,close:0,avoid:0,total:0};o.total++;if(m.sig in o)o[m.sig]++;});
  renderCoins(sigByCoin);
  let rows=all.filter(m=>(!coin||m.coin===coin)&&(!kind||m.kind===kind)&&m.tl<=maxT
     &&((m.yes_ask!=null&&m.yes_ask>=lo&&m.yes_ask<=hi)||(m.no_ask!=null&&m.no_ask>=lo&&m.no_ask<=hi))
     &&(show==="all"||(show==="gy"&&(m.sig==="bid"||m.sig==="close"))||(show==="g"&&m.sig==="bid")||(show==="nored"&&m.sig!=="avoid")));
  const E=m=>-(m.best_edge??-9);
  const key={win:m=>-(m.cr.sideP??0),sig:m=>SIGRANK[m.sig]*100-(m.gold?50:0)-m.cr.n*5+E(m),edge:E,time:m=>m.tl,vol:m=>-(m.volume_24h??0),z:m=>Math.abs(m.z??99)}[sort];
  rows.sort((a,b)=>key(a)-key(b));
  const n={bid:0,close:0,avoid:0,gold:0};rows.forEach(m=>{if(m.sig in n)n[m.sig]++;if(m.gold)n.gold++;});
  $("count").textContent=rows.length+" shown / "+DATA.markets.length+" open";
  $("tally").innerHTML='<span class="dot d-g"></span>'+n.bid+' bid · <span class="dot d-y"></span>'+n.close+' close · <span class="dot d-r"></span>'+n.avoid+' avoid · <span style="color:var(--gold)">★ '+n.gold+' gold</span>';
  $("cover").textContent=Object.keys(DATA.coins).length+" coins with 15-min windows";
  let newBid=false;const nowBid=new Set();
  let h="";
  for(const m of rows.slice(0,500)){
    const e=m.best_edge,sd=m.best_side?m[m.best_side]:null,mp=m.model_p,mk=m.mid;
    let fresh="";
    if(m.sig==="bid"){nowBid.add(m.ticker);if(!seenBid.has(m.ticker)){fresh=" fresh";if(!firstRender)newBid=true;}}
    const bar=(mp!=null&&mk!=null)?\`<span class="bar"><i class="k" style="left:\${(mk*78).toFixed(0)}px"></i><i class="m" style="left:\${(mp*78).toFixed(0)}px"></i></span>\`:"–";
    const label=/15M$/i.test(m.series||"")?"Up in 15m":(m.yes_sub||m.title);
    h+=\`<tr class="\${m.sig}\${fresh}\${m.gold?" gold":""}">
      <td class="l"><span class="sigl s-\${m.sig}">\${SIGLBL[m.sig]}</span>\${m.gold?'<span class="star">★</span>':""}\${chips(m.cr)}</td>
      <td class="l"><b>\${m.coin}</b> \${label}<span class="tag">\${m.kind}</span><div style="color:var(--dim);font-size:11px">\${m.ticker}</div></td>
      <td class="wp \${wpClass(m.cr.sideP,crit)}" title="Model chance the recommended side wins">\${m.cr.sideP==null?"–":(m.cr.sideP*100).toFixed(0)+"%"}</td>
      <td class="\${tcls(m.tl)}">\${fmtT(m.tl)}</td>
      <td>\${m.strike?fmtPx(m.strike):(m.floor?fmtPx(m.floor):"TBD")}</td>
      <td>\${fmtPx(m.spot)}</td>
      <td class="\${cls(m.dist_pct)}">\${m.dist_pct==null?"–":pct(m.dist_pct,3)}</td>
      <td>\${m.z==null?"–":m.z.toFixed(2)}</td>
      <td>\${c(m.yes_bid)} / \${c(m.yes_ask)}</td>
      <td>\${c(m.no_ask)}</td>
      <td>\${m.spread==null?"–":Math.round(m.spread*100)}</td>
      <td>\${mk==null?"–":(mk*100).toFixed(0)}</td>
      <td><b>\${mp==null?"–":(mp*100).toFixed(1)}</b></td>
      <td>\${bar}</td>
      <td>\${m.best_side?\`<span class="side \${m.best_side}">\${m.best_side.toUpperCase()} @ \${c(sd.ask)}</span>\`:"–"}</td>
      <td class="\${cls(e)}"><b>\${e==null?"–":(e*100).toFixed(1)}</b></td>
      <td class="\${cls(sd&&sd.ev_pct)}">\${sd&&sd.ev_pct!=null?sd.ev_pct.toFixed(0)+"%":"–"}</td>
      <td>\${sd&&sd.kelly!=null&&sd.kelly>0?(sd.kelly*100).toFixed(1)+"%":"–"}</td>
      <td>\${fmtN(m.volume_24h)}</td><td>\${fmtN(m.oi)}</td></tr>\`;
  }
  $("rows").innerHTML=h||'<tr><td class="l" colspan="20" style="color:var(--dim);padding:18px">No contracts match these filters yet. Try widening "Closes within" or "Show".</td></tr>';
  if(newBid&&$("cBeep").checked)beep();
  seenBid=nowBid;firstRender=false;
  const bad=(DATA.series_errors||[]).slice(0,3);
  $("errs").textContent=[...bad,...DATA.errors.map(e=>e.msg)].join("  ·  ");
}
async function poll(){
  try{const r=await fetch("api/state",{cache:"no-store"});if(r.ok){DATA=await r.json();recvAt=performance.now();lastOk=Date.now();render();}}catch(e){}
  const age=(Date.now()-lastOk)/1000;
  $("status").textContent=lastOk?(age<5?"● live":"● stale "+Math.round(age)+"s"):"connecting…";
  $("status").className="pill "+(lastOk&&age<5?"live":"stale");
}
setInterval(poll,1000);poll();
</script></body></html>
`;

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
async function getJSON(url: string, timeoutMs = 8000): Promise<any> {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const r = await fetch(url, { headers: HDRS, signal: ctl.signal });
    if (!r.ok) throw new Error(`HTTP ${r.status} ${url.split("?")[0].replace(/^https:\/\//, "")}`);
    return await r.json();
  } finally {
    clearTimeout(t);
  }
}

// ---------------------------------------------------------------- Kalshi rate limiter
// Keeps us well under Kalshi's public read limit and backs off on HTTP 429.
const KALSHI_GAP_MS = 150; // ~6-7 requests per second max
let kNext = 0, kPauseUntil = 0;
async function kget(url: string, timeoutMs = 8000): Promise<any> {
  while (true) {
    const now = Date.now();
    const wait = Math.max(kNext - now, kPauseUntil - now);
    if (wait <= 0) break;
    await sleep(wait);
  }
  kNext = Date.now() + KALSHI_GAP_MS;
  try { return await getJSON(url, timeoutMs); }
  catch (e) {
    if (String(e).includes("HTTP 429")) kPauseUntil = Date.now() + 3000;
    throw e;
  }
}

// ---------------------------------------------------------------- assets
// asset -> [Yahoo symbol, keywords that identify it in a Kalshi series ticker/title]
const ASSETS: Record<string, [string, RegExp]> = {
  GOLD: ["GC=F", /\bGOLD\b|KXGOLD/i], SILVER: ["SI=F", /\bSILVER\b|KXSILVER/i], COPPER: ["HG=F", /\bCOPPER\b|KXCOPPER/i],
  PLATINUM: ["PL=F", /PLATINUM|KXPLAT/i], PALLADIUM: ["PA=F", /PALLADIUM|KXPALL/i], NATGAS: ["NG=F", /NATURAL GAS|NATGAS/i],
  BRENT: ["BZ=F", /BRENT/i], OIL: ["CL=F", /\bWTI\b|CRUDE|\bOIL\b|KXWTI|KXOIL/i],
  EURUSD: ["EURUSD=X", /EUR\s*\/?\s*USD|EURUSD|\bEURO\b/i], GBPUSD: ["GBPUSD=X", /GBP\s*\/?\s*USD|GBPUSD|POUND/i],
  USDJPY: ["JPY=X", /USD\s*\/?\s*JPY|USDJPY|\bYEN\b/i],
  SP500: ["^GSPC", /S&P|SP500|KXINX/i], NASDAQ: ["^NDX", /NASDAQ|KXNDQ/i], DOW: ["^DJI", /\bDOW\b|KXDJI/i],
};
function coinFor(ticker: string, title: string): string | null {
  const text = `${ticker} ${title || ""}`;
  if (/BTC|ETH|SOL|XRP|DOGE|BITCOIN|ETHEREUM|CRYPTO/i.test(ticker)) return null;
  for (const [a, [, re]] of Object.entries(ASSETS)) if (re.test(text)) return a;
  return null;
}
const SEED = ["KXGOLD15M", "KXSILVER15M", "KXNATGAS15M", "KXINX15M", "KXNDQ15M", "KXEURUSD15M", "KXGBPUSD15M", "KXUSDJPY15M"];

// ---------------------------------------------------------------- state
type Series = { ticker: string; title: string; coin: string | null; fast: boolean;
  lastRun: number; count: number; error: string | null };
const series = new Map<string, Series>();
const markets = new Map<string, any>();
const spotEx = new Map<string, any>();    // Coinbase Exchange ticker (fast coins)
const spotRates = new Map<string, number>(); // Coinbase rates (all coins)
let spotRatesTs = 0;
const spotBn = new Map<string, number>(); // Binance.US
let spotBnTs = 0;
const candles = new Map<string, any>();
const errors: { ts: number; msg: string }[] = [];
let discoveryNote = "";
function noteError(msg: string) {
  errors.push({ ts: Date.now() / 1000, msg });
  if (errors.length > 20) errors.splice(0, errors.length - 20);
}

function addSeries(ticker: string, title: string, freq = "") {
  if (series.has(ticker)) return;
  const fast = /15M$/i.test(ticker) || /15 ?min/i.test(title || "") || /hour/i.test(freq) || /hourly/i.test(title || "");
  if (!fast || !coinFor(ticker, title)) return;
  series.set(ticker, { ticker, title, coin: coinFor(ticker, title), fast, lastRun: 0, count: -1, error: null });
}

async function discover() {
  const found: any[] = [];
  try {
    let cursor = "";
    for (let i = 0; i < 10; i++) {
      const url = `${KALSHI}/series?limit=1000` + (cursor ? `&cursor=${encodeURIComponent(cursor)}` : "");
      const d = await getJSON(url, 15000);
      found.push(...(d.series || []));
      cursor = d.cursor || "";
      if (!cursor) break;
    }
    discoveryNote = "";
  } catch (e) {
    discoveryNote = "Series discovery failed (" + String(e).slice(0, 80) + "); using built-in list";
  }
  for (const s of found) if (s && s.ticker) addSeries(String(s.ticker), String(s.title || ""), String(s.frequency || ""));
  for (const t of SEED) addSeries(t, "");
  console.log(`discovery: ${found.length} series from Kalshi, ${series.size} commodity/currency/index series tracked: ${[...series.keys()].join(', ')}`);
}

// ---------------------------------------------------------------- kalshi
const num = (v: any) => (v === null || v === undefined || v === "" ? null : Number.isFinite(Number(v)) ? Number(v) : null);
function dollars(m: any, k: string) {
  const v = num(m[k + "_dollars"]);
  if (v !== null) return v;
  const c = m[k];
  return typeof c === "number" ? c / 100 : null;
}
const fp = (m: any, k: string) => num(m[k + "_fp"]) ?? num(m[k]);

function kindFor(s: Series, m: any) {
  if (s.fast) return "15m";
  const st = m.strike_type;
  if (st === "between") return "range";
  if (st === "greater" || st === "greater_or_equal" || st === "less" || st === "less_or_equal") return "above/below";
  return "other";
}

async function fetchSeries(s: Series) {
  const params = new URLSearchParams({
    // 18 Oct 2026: Kalshi now reports running markets as status "active", and status=open returns nothing.
    // So no status filter here; keep running ones (active/open) below.
    series_ticker: s.ticker, limit: "1000",
    min_close_ts: String(Math.floor(Date.now() / 1000)),
    // Kalshi lists far-future rounds first in small pages, so only ask for the current 15-minute window.
    max_close_ts: String(Math.floor(Date.now() / 1000 + 20 * 60)),
  });
  const out: any[] = [];
  let cursor = "";
  for (let i = 0; i < 5; i++) {
    if (cursor) params.set("cursor", cursor);
    const d = await kget(`${KALSHI}/markets?${params}`);
    out.push(...(d.markets || []));
    cursor = d.cursor || "";
    if (!cursor) break;
  }
  const slim = new Map<string, any>();
  let pick: any = null, pickD = 9;
  for (const m of out) {
    if (!m.ticker) continue;
    if (m.status && !["active", "open"].includes(m.status)) continue;
    const sm = slimMarket(m, s);
    const ya = sm.yes_ask, yb = sm.yes_bid;
    const fav = Math.max(ya ?? 0, yb != null ? 1 - yb : 0);
    const d = Math.abs(fav - 0.96);
    // nearest closing window first, then the strike nearest the 94-98c buy band
    const key = Date.parse(sm.close_time) / 1e6 + d;
    if (pick == null || key < pickD) { pick = sm; pickD = key; }
  }
  if (pick) slim.set(pick.ticker, pick);
  for (const [t, v] of markets) if (v.series === s.ticker && !slim.has(t)) markets.delete(t);
  for (const [t, v] of slim) markets.set(t, v);
  s.count = slim.size;
  s.error = null;
}
function slimMarket(m: any, s: Series) {
  return {
      ticker: m.ticker, series: s.ticker, coin: s.coin || "OTHER", kind: kindFor(s, m),
      title: m.title, yes_sub: m.yes_sub_title || m.subtitle || "",
      strike_type: m.strike_type, floor: num(m.floor_strike), cap: num(m.cap_strike),
      yes_bid: dollars(m, "yes_bid"), yes_ask: dollars(m, "yes_ask"),
      no_bid: dollars(m, "no_bid"), no_ask: dollars(m, "no_ask"), last: dollars(m, "last_price"),
      volume_24h: fp(m, "volume_24h"), volume: fp(m, "volume"), oi: fp(m, "open_interest"),
      open_time: m.open_time,
      close_time: m.close_time, status: m.status,
    };
}

function needsNewWindow(s: Series) {
  const now = Date.now();
  let live = false;
  for (const m of markets.values()) if (m.series === s.ticker && Date.parse(m.close_time) > now) live = true;
  return !live;
}
async function seriesLoop(_fast: boolean) {
  // Finds the current 15-minute window for each coin. Live prices come from quoteLoop.
  while (true) {
    const now = Date.now();
    for (const s of series.values()) {
      if (!s.fast || !s.coin) continue;
      const gap = s.count === 0 ? 60_000 : needsNewWindow(s) ? 3_000 : 30_000;
      if (now - s.lastRun < gap) continue;
      s.lastRun = Date.now();
      try { await fetchSeries(s); }
      catch (e) { s.error = String(e).slice(0, 140); s.lastRun = Date.now() - gap + 5_000; }
    }
    await sleep(300);
  }
}
let batchOk = true;
async function quoteLoop() {
  // One batched request per second refreshes prices for every open 15-minute window.
  while (true) {
    const t0 = Date.now();
    const live = [...markets.values()].filter((m) => m.kind === "15m" && Date.parse(m.close_time) > t0);
    if (live.length && batchOk) {
      try {
        const d = await kget(`${KALSHI}/markets?tickers=${encodeURIComponent(live.map((m) => m.ticker).join(","))}&limit=${live.length + 5}`);
        let hit = 0;
        for (const m of d.markets || []) {
          const old = markets.get(m.ticker);
          if (!old) continue;
          hit++;
          const s = series.get(old.series);
          if (s) markets.set(m.ticker, slimMarket(m, s));
        }
        if (hit === 0 && (d.markets || []).length) { batchOk = false; noteError("Batch quotes unsupported; using per-coin refresh"); }
      } catch (e) { /* limiter handles 429 backoff */ }
    } else if (live.length && !batchOk) {
      // fallback: refresh each series' list in turn
      for (const s of series.values()) {
        if (!s.fast || !s.coin || s.count === 0) continue;
        try { await fetchSeries(s); s.lastRun = Date.now(); } catch (e) { s.error = String(e).slice(0, 140); }
      }
    }
    await sleep(Math.max(200, 1000 - (Date.now() - t0)));
  }
}

// ---------------------------------------------------------------- spot & candles
function neededCoins() {
  const set = new Set<string>();
  const ok = fifteenCoins();
  for (const m of markets.values()) if (m.coin && ok.has(m.coin)) set.add(m.coin);
  return set;
}
function fifteenCoins() {
  const set = new Set<string>();
  for (const s of series.values()) if (s.fast && s.coin && s.count !== 0) set.add(s.coin);
  return set;
}
function fastCoins() {
  const set = new Set<string>();
  for (const s of series.values()) if (s.fast && s.coin && s.count > 0) set.add(s.coin);
  return [...set].slice(0, 10);
}
async function yahoo(sym: string, interval: string, range: string): Promise<{ price: number | null; bars: Bar[] }> {
  const d = await getJSON(`${YAHOO}${encodeURIComponent(sym)}?interval=${interval}&range=${range}`, 8000);
  const r = d && d.chart && d.chart.result && d.chart.result[0];
  if (!r) throw new Error("Yahoo: no data for " + sym);
  const ts: number[] = r.timestamp || [], q = (r.indicators && r.indicators.quote && r.indicators.quote[0]) || {};
  const bars: Bar[] = [];
  for (let i = 0; i < ts.length; i++) {
    const c = q.close && q.close[i], h = q.high && q.high[i], l = q.low && q.low[i];
    if (c != null && h != null && l != null) bars.push([ts[i], +c, +h, +l]);
  }
  const price = r.meta && Number(r.meta.regularMarketPrice) > 0 ? Number(r.meta.regularMarketPrice) : bars.length ? bars[bars.length - 1][1] : null;
  return { price, bars };
}
const yLogged = new Set<string>();
async function spotFastLoop() {
  // live price + 1-minute candles every ~5s, 5-minute candles every minute
  const c5At = new Map<string, number>();
  while (true) {
    for (const coin of neededCoins()) {
      const sym = ASSETS[coin] && ASSETS[coin][0];
      if (!sym) continue;
      try {
        const a = await yahoo(sym, "1m", "1d");
        if (a.price) spotEx.set(coin, { price: a.price, ts: Date.now() });
        if (!yLogged.has(coin)) { yLogged.add(coin); console.log(`Yahoo OK ${coin} (${sym}): price ${a.price}, ${a.bars.length} 1m bars`); }
        const prev = candles.get(coin) || { c1: [], c5: [], ts: 0 };
        let c5 = prev.c5;
        if ((c5At.get(coin) || 0) < Date.now() - 60_000) {
          try { c5 = (await yahoo(sym, "5m", "5d")).bars; c5At.set(coin, Date.now()); } catch { /* keep old */ }
        }
        candles.set(coin, { c1: a.bars, c5, ts: Date.now() });
      } catch (e) { noteError("Yahoo " + sym + ": " + String(e).slice(0, 80)); if (!yLogged.has("x" + coin)) { yLogged.add("x" + coin); console.log(`Yahoo FAILED ${coin} (${sym}): ${String(e).slice(0, 120)}`); } }
      await sleep(250);
    }
    await sleep(3000);
  }
}
async function ratesLoop() { /* not used for markets */ }
function spotFor(coin: string) {
  const ex = spotEx.get(coin);
  if (ex && Date.now() - ex.ts < 60_000) return { price: ex.price, src: "Yahoo" };
  return null;
}
type Bar = [number, number, number, number]; // t, close, high, low
async function candleLoop() { /* Yahoo candles come from spotFastLoop */ }

// ---------------------------------------------------------------- 15m order books & history
const books = new Map<string, any>();
const hist = new Map<string, [number, number | null, number | null][]>(); // ticker -> [t, mid, model]
function levels(arr: any): [number, number][] {
  if (!Array.isArray(arr)) return [];
  return arr.map((x: any) => {
    let pr = Number(x[0]), q = Number(x[1]);
    if (pr > 1) pr = pr / 100;
    return [pr, q] as [number, number];
  }).filter((x) => Number.isFinite(x[0]) && Number.isFinite(x[1]));
}
function parseBook(d: any) {
  const ob = d.orderbook_fp || d.orderbook || {};
  const yes = levels(ob.yes_dollars || ob.yes), no = levels(ob.no_dollars || ob.no);
  const top = (a: [number, number][]) => a.sort((x, y) => y[0] - x[0]).slice(0, 5);
  const ty = top(yes), tn = top(no);
  const dy = ty.reduce((a, b) => a + b[1], 0), dn = tn.reduce((a, b) => a + b[1], 0);
  return { yes: ty, no: tn, yes_depth: dy, no_depth: dn, imb: dy + dn > 0 ? dy / (dy + dn) : null, ts: Date.now() };
}
async function bookLoop() {
  while (true) {
    const live = [...markets.values()].filter((m) => m.kind === "15m" && Date.parse(m.close_time) > Date.now());
    for (const m of live) {
      try { books.set(m.ticker, parseBook(await kget(`${KALSHI}/markets/${m.ticker}/orderbook?depth=10`, 5000))); }
      catch { /* keep last */ }
      await sleep(250);
    }
    for (const t of books.keys()) if (!markets.has(t)) books.delete(t);
    for (const t of hist.keys()) if (!markets.has(t)) hist.delete(t);
    await sleep(live.length ? 600 : 2000);
  }
}
function midChange(h: [number, number | null, number | null][] | undefined, secs: number) {
  if (!h || h.length < 2) return null;
  const last = h[h.length - 1];
  const target = last[0] - secs;
  let ref: number | null = null;
  for (const x of h) if (x[0] <= target && x[1] != null) ref = x[1];
  return ref == null || last[1] == null ? null : last[1] - ref;
}
function windowExtras(r: any) {
  const now = Date.now() / 1000;
  const openTs = r.open_time ? Date.parse(r.open_time) / 1000 : NaN;
  if (Number.isFinite(openTs)) { r.open_ts = openTs; r.elapsed = now - openTs; }
  const cd = candles.get(r.coin);
  if (cd && Number.isFinite(openTs)) {
    const bars: Bar[] = (cd.c1 || []).filter((b: Bar) => b[0] >= openTs - 60);
    const hs = bars.map((b) => b[2]), ls = bars.map((b) => b[3]);
    if (r.spot != null) { hs.push(r.spot); ls.push(r.spot); }
    if (hs.length) { r.win_hi = Math.max(...hs); r.win_lo = Math.min(...ls); }
  }
  if (r.spot != null && r.strike) r.need_pct = (r.strike / r.spot - 1) * 100;
  const b = books.get(r.ticker);
  if (b) r.book = { yes: b.yes, no: b.no, yes_depth: b.yes_depth, no_depth: b.no_depth, imb: b.imb };
  let h = hist.get(r.ticker);
  if (!h) { h = []; hist.set(r.ticker, h); }
  if (!h.length || now - h[h.length - 1][0] >= 1.5) { h.push([now, r.mid ?? null, r.model_p ?? null]); if (h.length > 700) h.shift(); }
  r.mid_chg_1m = midChange(h, 60);
  r.mid_chg_3m = midChange(h, 180);
  r.hist = h.filter((_, i) => i % 3 === 0 || i === h.length - 1).map((x) => [Math.round(x[0]), x[1] == null ? null : +x[1].toFixed(3), x[2] == null ? null : +x[2].toFixed(3)]);
}

// ---------------------------------------------------------------- model
function erf(x: number) {
  const s = Math.sign(x); x = Math.abs(x);
  const t = 1 / (1 + 0.3275911 * x);
  const y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-x * x);
  return s * y;
}
const ncdf = (x: number) => 0.5 * (1 + erf(x / Math.SQRT2));
function stdev(xs: number[]) {
  const n = xs.length; if (n < 3) return null;
  const mu = xs.reduce((a, b) => a + b, 0) / n;
  return Math.sqrt(xs.reduce((a, b) => a + (b - mu) ** 2, 0) / (n - 1));
}
function perMinVol(closes: number[], minsPerBar: number) {
  const r: number[] = [];
  for (let i = 1; i < closes.length; i++) if (closes[i - 1] > 0 && closes[i] > 0) r.push(Math.log(closes[i] / closes[i - 1]));
  const s = stdev(r); return s === null ? null : s / Math.sqrt(minsPerBar);
}

function coinStats(coin: string) {
  const st: any = { coin };
  const sp = spotFor(coin);
  if (sp) { st.price = sp.price; st.src = sp.src; }
  const cd = candles.get(coin);
  if (!cd) return st;
  const c1: Bar[] = cd.c1 || [], c5: Bar[] = cd.c5 || [];
  const px = st.price ?? (c1.length ? c1[c1.length - 1][1] : null);
  if (st.price == null && px != null) { st.price = px; st.src = "candles"; }
  const chg = (mins: number) => {
    if (!c1.length || px == null) return null;
    const target = c1[c1.length - 1][0] - mins * 60;
    let ref: number | null = null;
    for (const b of c1) if (b[0] <= target) ref = b[1];
    return ref ? (px / ref - 1) * 100 : null;
  };
  st.chg_1m = chg(1); st.chg_5m = chg(5); st.chg_15m = chg(15); st.chg_60m = chg(60);
  const v1 = c1.length > 20 ? perMinVol(c1.slice(-121).map((b) => b[1]), 1) : null;
  const v30 = c1.length > 20 ? perMinVol(c1.slice(-31).map((b) => b[1]), 1) : null;
  const v5 = c5.length > 20 ? perMinVol(c5.slice(-289).map((b) => b[1]), 5) : null;
  st.vol_1m = v1; st.vol_1m_30 = v30; st.vol_5m = v5;
  const ann = (v: number | null) => (v == null ? null : v * Math.sqrt(525600) * 100);
  st.ann_vol_30m = ann(v30); st.ann_vol_24h = ann(v5);
  if (v1 && v30) st.vol_regime = v30 / v1;
  return st;
}
function sigmaFor(st: any, tSec: number) {
  const { vol_1m: v1, vol_1m_30: v30, vol_5m: v5 } = st;
  if (tSec <= 3600) return v30 && v1 ? 0.5 * v30 + 0.5 * v1 : v30 || v1 || v5;
  return v5 || v1;
}
function probAbove(S: number, K: number, sig: number | null, t: number) {
  if (!(S > 0) || !(K > 0)) return null;
  if (t <= 0) return S >= K ? 1 : 0;
  if (!sig) return null;
  const s = sig * Math.sqrt(t / 60);
  if (s < 1e-12) return S >= K ? 1 : 0;
  return ncdf((Math.log(S / K) - 0.5 * s * s) / s);
}
function kalshiFee(p: number | null) {
  if (p == null || p <= 0 || p >= 1) return 0;
  return Math.ceil(0.07 * p * (1 - p) * 100 - 1e-9) / 100;
}


// ---------------------------------------------------------------- learning model (15-minute windows)
// Features for one moment in a window -> probability YES ("price up") wins.
type Feat = { x: number; sig: number; tEff: number; r5: number | null; imb: number | null; mid: number | null };
type Params = { volK: number; momK: number; bookK: number; w: number };
let PARAMS: Params = { volK: 1, momK: 0.25, bookK: 0.4, w: 0.5 };
const logit = (p: number) => Math.log(p / (1 - p));
const inv = (x: number) => 1 / (1 + Math.exp(-x));
const clampP = (p: number) => Math.min(0.995, Math.max(0.005, p));
function pFromFeat(f: Feat, P: Params, volMult = 1): number | null {
  if (!(f.sig > 0) || !(f.tEff > 0)) return null;
  const s = f.sig * P.volK * volMult * Math.sqrt(f.tEff / 60);
  if (!(s > 0)) return null;
  let drift = P.momK * ((f.r5 ?? 0) / 5) * (f.tEff / 60);        // recent momentum carried forward
  drift = Math.max(-0.5 * s, Math.min(0.5 * s, drift));
  const x = f.x + drift;
  // fat tails: mixture of a calm regime and a jumpy regime
  const comp = (k: number) => ncdf((x - 0.5 * (s * k) ** 2) / (s * k));
  let L = logit(clampP(0.85 * comp(0.9) + 0.15 * comp(1.8)));
  if (f.imb != null) L += P.bookK * (f.imb - 0.5);                // order-book lean
  if (f.mid != null && f.mid > 0.01 && f.mid < 0.99) L = P.w * L + (1 - P.w) * logit(f.mid); // blend with market
  return inv(L);
}

type Sample = { f: Feat; y: number; t: number; ticker: string };
const DATASET: Sample[] = [];
const PENDING = new Map<string, { close: number; samples: { f: Feat; t: number }[]; tries: number; coin: string }>();
const SEEN = new Set<string>();
const OUTCOMES = new Map<string, { coin: string; close: number; y: number }>(); // ticker -> result
function recordOutcome(ticker: string, coin: string, close: number, y: number) {
  OUTCOMES.set(ticker, { coin, close, y });
  if (OUTCOMES.size > 3000) { const old = [...OUTCOMES.entries()].sort((a, b) => a[1].close - b[1].close).slice(0, OUTCOMES.size - 3000); for (const [k] of old) OUTCOMES.delete(k); }
}
function tally() {
  const byCoin: Record<string, { close: number; y: number }[]> = {};
  const byClose: Record<string, Record<string, number>> = {};
  for (const o of OUTCOMES.values()) {
    (byCoin[o.coin] = byCoin[o.coin] || []).push({ close: o.close, y: o.y });
    const k = String(Math.round(o.close / 60) * 60);
    (byClose[k] = byClose[k] || {})[o.coin] = o.y;
  }
  const coins: any[] = [];
  for (const [coin, arr] of Object.entries(byCoin)) {
    arr.sort((a, b) => a.close - b.close);
    const ups = arr.filter((a) => a.y === 1).length, downs = arr.length - ups;
    let len = 0; const last = arr[arr.length - 1].y;
    for (let i = arr.length - 1; i >= 0 && arr[i].y === last; i--) len++;
    let match = 0, both = 0;
    for (const a of arr) { const b = byClose[String(Math.round(a.close / 60) * 60)]; if (coin !== "BTC" && b && b.BTC != null) { both++; if (b.BTC === a.y) match++; } }
    coins.push({ coin, ups, downs, n: arr.length, last10: arr.slice(-10).map((a) => a.y), streak: { side: last, len }, btcMatch: both ? match / both : null, btcN: both });
  }
  let windows = 0, allSame = 0, agreeSum = 0;
  for (const res of Object.values(byClose)) {
    const v = Object.values(res);
    if (v.length < 3) continue;
    windows++;
    const ups = v.filter((x) => x === 1).length, maj = Math.max(ups, v.length - ups);
    if (maj === v.length) allSame++;
    agreeSum += maj / v.length;
  }
  return { coins, windows, allSame, avgAgree: windows ? agreeSum / windows : null };
}
const MODEL_INFO: any = { n: 0, windows: 0, fittedAt: null, brier: null, brierMkt: null, logloss: null, loglossMkt: null, hit85: null, n85: 0, bootstrapped: false, params: PARAMS };

function featuresLive(m: any, st: any): Feat | null {
  const S = st.price, K = m.floor, close = Date.parse(m.close_time) / 1000;
  if (!(S > 0) || !(K > 0) || !Number.isFinite(close)) return null;
  const tLeft = close - Date.now() / 1000;
  const tEff = tLeft > 60 ? tLeft - 40 : tLeft;
  const sig = sigmaFor(st, tEff);
  if (!sig || tEff <= 0) return null;
  const r5 = st.chg_5m != null ? Math.log(1 + st.chg_5m / 100) : null;
  const b = books.get(m.ticker);
  const mid = m.yes_ask != null && m.yes_bid != null && m.yes_bid > 0 ? (m.yes_ask + m.yes_bid) / 2 : null;
  return { x: Math.log(S / K), sig, tEff, r5, imb: b && b.imb != null ? b.imb : null, mid };
}

async function sampleLoop() {
  // every 60s: snapshot the features of each live 15-minute window, to be scored when it settles
  while (true) {
    await sleep(60_000);
    const stats: Record<string, any> = {};
    for (const m of markets.values()) {
      if (m.kind !== "15m" || !m.floor) continue;
      const close = Date.parse(m.close_time) / 1000, tl = close - Date.now() / 1000;
      if (!(tl > 60 && tl < 900)) continue;
      const st = stats[m.coin] || (stats[m.coin] = coinStats(m.coin));
      const f = featuresLive(m, st);
      if (!f) continue;
      const p: any = PENDING.get(m.ticker) || { close, samples: [], tries: 0, coin: m.coin };
      p.samples.push({ f, t: Date.now() / 1000 });
      PENDING.set(m.ticker, p);
    }
  }
}
async function resultLoop() {
  // label pending samples with the real Kalshi result once a window settles
  while (true) {
    await sleep(20_000);
    const now = Date.now() / 1000;
    for (const [ticker, p] of PENDING) {
      if (now < p.close + 45) continue;
      if (p.tries > 40) { PENDING.delete(ticker); continue; }
      p.tries++;
      try {
        const d = await kget(`${KALSHI}/markets/${ticker}`, 6000);
        const r = d && d.market && d.market.result;
        if (r === "yes" || r === "no") {
          const y = r === "yes" ? 1 : 0;
          for (const smp of p.samples) DATASET.push({ f: smp.f, y, t: smp.t, ticker });
          recordOutcome(ticker, p.coin, p.close, y);
          SEEN.add(ticker); PENDING.delete(ticker);
          if (DATASET.length > 6000) DATASET.splice(0, DATASET.length - 6000);
        }
      } catch { /* retry next pass */ }
    }
  }
}

function candleClose(c1: Bar[], tau: number) {
  let v: number | null = null, idx = -1;
  for (let i = 0; i < c1.length; i++) if (c1[i][0] + 60 <= tau) { v = c1[i][1]; idx = i; }
  return { v, idx };
}
async function bootstrap() {
  // learn from the last few hours of settled 15-minute windows (Kalshi results + price history)
  await sleep(45_000);
  const since = Math.floor(Date.now() / 1000 - 4.5 * 3600);
  for (const s of series.values()) {
    if (!s.fast || !s.coin) continue;
    let settled: any[] = [];
    try {
      const d = await kget(`${KALSHI}/markets?series_ticker=${s.ticker}&status=settled&min_close_ts=${since}&limit=200`);
      settled = d.markets || [];
    } catch { continue; }
    for (const m of settled) if (m.result === "yes" || m.result === "no") recordOutcome(m.ticker, s.coin, Date.parse(m.close_time) / 1000, m.result === "yes" ? 1 : 0);
    const cd = candles.get(s.coin);
    if (!cd || !cd.c1 || cd.c1.length < 60) continue;
    const c1: Bar[] = cd.c1;
    for (const m of settled) {
      if (SEEN.has(m.ticker) || !(m.result === "yes" || m.result === "no")) continue;
      const K = num(m.floor_strike), open = Date.parse(m.open_time) / 1000, close = Date.parse(m.close_time) / 1000;
      if (!(K! > 0) || !Number.isFinite(open) || !Number.isFinite(close)) continue;
      let sticks: any[] = [];
      try {
        const d = await kget(`${KALSHI}/series/${s.ticker}/markets/${m.ticker}/candlesticks?start_ts=${Math.floor(open)}&end_ts=${Math.ceil(close)}&period_interval=1`);
        sticks = d.candlesticks || [];
      } catch { /* no price history: learn without the market blend for this window */ }
      const px = (o: any) => { if (!o) return null; const v = num(o.close_dollars) ?? num(o.close); return v == null ? null : v > 1 ? v / 100 : v; };
      const y = m.result === "yes" ? 1 : 0;
      for (let tau = open + 60; tau <= close - 60; tau += 60) {
        const { v: S, idx } = candleClose(c1, tau);
        if (S == null || idx < 25) continue;
        const closes = c1.slice(Math.max(0, idx - 60), idx + 1).map((b) => b[1]);
        const v1 = perMinVol(closes, 1), v30 = perMinVol(closes.slice(-31), 1);
        const sig = v1 && v30 ? 0.5 * v1 + 0.5 * v30 : v1 || v30;
        const s5 = idx >= 5 ? c1[idx - 5][1] : null;
        const tLeft = close - tau, tEff = tLeft > 60 ? tLeft - 40 : tLeft;
        let mid: number | null = null;
        for (const k of sticks) { const e = num(k.end_period_ts); if (e != null && e <= tau) { const b = px(k.yes_bid), a = px(k.yes_ask); mid = b != null && a != null && b > 0 ? (a + b) / 2 : mid; } }
        if (!sig) continue;
        DATASET.push({ f: { x: Math.log(S / K!), sig, tEff, r5: s5 ? Math.log(S / s5) : null, imb: null, mid }, y, t: tau, ticker: m.ticker });
      }
      SEEN.add(m.ticker);
    }
  }
  MODEL_INFO.bootstrapped = true;
  fitModel();
}

function scoreOf(P: Params | null, data: Sample[]) {
  let ll = 0, br = 0, n = 0;
  for (const d of data) {
    const p = P ? pFromFeat(d.f, P) : d.f.mid;
    if (p == null) continue;
    const q = clampP(p);
    ll -= d.y ? Math.log(q) : Math.log(1 - q); br += (q - d.y) ** 2; n++;
  }
  return n ? { ll: ll / n, br: br / n, n } : null;
}
function fitModel() {
  const data = DATASET;
  MODEL_INFO.n = data.length;
  MODEL_INFO.windows = new Set(data.map((d) => d.ticker)).size;
  if (data.length < 150) return;
  let best: { P: Params; ll: number } | null = null;
  const hasMid = data.some((d) => d.f.mid != null);
  for (const volK of [0.8, 1, 1.25]) for (const momK of [0, 0.25, 0.5, 1]) for (const bookK of [0, 0.4, 0.8])
    for (const w of hasMid ? [0.2, 0.35, 0.5, 0.65, 0.8, 1] : [1]) {
      const P = { volK, momK, bookK, w }, sc = scoreOf(P, data);
      if (sc && (!best || sc.ll < best.ll)) best = { P, ll: sc.ll };
    }
  if (best) PARAMS = best.P;
  const withMid = data.filter((d) => d.f.mid != null);
  const mm = scoreOf(PARAMS, withMid), mk = scoreOf(null, withMid), all = scoreOf(PARAMS, data);
  let n85 = 0, hit = 0;
  for (const d of data) { const p = pFromFeat(d.f, PARAMS); if (p == null) continue; if (p >= 0.85 || p <= 0.15) { n85++; if ((p >= 0.5 ? 1 : 0) === d.y) hit++; } }
  Object.assign(MODEL_INFO, {
    fittedAt: Date.now() / 1000, params: PARAMS,
    brier: all ? all.br : null, logloss: all ? all.ll : null,
    brierModelVsMkt: mm ? mm.br : null, brierMkt: mk ? mk.br : null, nMkt: withMid.length,
    hit85: n85 ? hit / n85 : null, n85,
  });
}
async function fitLoop() { while (true) { await sleep(5 * 60_000); try { fitModel(); } catch (e) { noteError("fit: " + e); } } }

function evaluate(m: any, st: any) {
  const out: any = { ...m };
  const closeTs = m.close_time ? Date.parse(m.close_time) / 1000 : NaN;
  const tLeft = Number.isFinite(closeTs) ? closeTs - Date.now() / 1000 : null;
  out.t_left = tLeft;
  const S = st.price ?? null;
  out.spot = S;
  let p: number | null = null;
  if (S != null && tLeft != null) {
    const tEff = m.kind === "15m" && tLeft > 60 ? tLeft - 40 : tLeft;
    const sig = sigmaFor(st, tEff);
    const { floor, cap, strike_type: stp } = m;
    const pAt = (sg: number | null) => {
      if ((stp === "greater" || stp === "greater_or_equal") && floor) return probAbove(S, floor, sg, tEff);
      if ((stp === "less" || stp === "less_or_equal") && cap) { const a = probAbove(S, cap, sg, tEff); return a == null ? null : 1 - a; }
      if (stp === "between" && floor && cap) { const a = probAbove(S, floor, sg, tEff), b = probAbove(S, cap, sg, tEff); return a == null || b == null ? null : Math.max(0, a - b); }
      return null;
    };
    p = pAt(sig);
    out.strike = (stp === "between" && floor && cap) ? (floor + cap) / 2 : (stp === "less" || stp === "less_or_equal") ? cap : floor;
    if (m.kind === "15m" && p != null && String(stp || "").startsWith("greater")) {
      const f = featuresLive({ ...m, yes_ask: m.yes_ask, yes_bid: m.yes_bid }, st);
      if (f) {
        out.model_raw = p;
        const pf = pFromFeat(f, PARAMS);
        if (pf != null) {
          p = pf;
          const a = pFromFeat(f, PARAMS, 0.75), b = pFromFeat(f, PARAMS, 1.25);
          if (a != null && b != null) { out.p_lo = Math.min(p, a, b); out.p_hi = Math.max(p, a, b); }
        }
      }
    } else if (p != null && sig) {
      // confidence range: same model with volatility 25% lower / higher than measured
      const a = pAt(sig * 0.75), b = pAt(sig * 1.25);
      if (a != null && b != null) { out.p_lo = Math.min(p, a, b); out.p_hi = Math.max(p, a, b); }
    }
    if (out.strike && sig) {
      const s = sig * Math.sqrt(Math.max(tEff, 1) / 60);
      out.dist_pct = (S / out.strike - 1) * 100;
      out.z = s > 0 ? Math.log(S / out.strike) / s : null;
    }
  }
  out.model_p = p;
  const stp2 = m.strike_type;
  out.dir = stp2 === "less" || stp2 === "less_or_equal" ? -1 : stp2 === "between" ? 0 : 1;
  let { yes_ask: ya, yes_bid: yb, no_ask: na, no_bid: nb } = m;
  if (na == null && yb != null) na = 1 - yb;
  if (ya == null && nb != null) ya = 1 - nb;
  out.yes_ask = ya; out.no_ask = na;
  out.mid = ya != null && yb != null && yb > 0 ? (ya + yb) / 2 : ya ?? m.last;
  out.spread = ya != null && yb != null ? ya - yb : null;
  if (p != null) {
    const side = (prob: number, ask: number | null) => {
      if (ask == null || ask <= 0 || ask >= 1) return null;
      const fee = kalshiFee(ask), cost = ask + fee, edge = prob - cost;
      return { ask, fee, edge, ev_pct: (edge / cost) * 100, kelly: cost < 1 ? (prob - cost) / (1 - cost) : null };
    };
    out.yes = side(p, ya); out.no = side(1 - p, na);
    // favored side = the side the model says is MORE likely to win (never a longshot bought for its "edge")
    const fav = p >= 0.5 ? "yes" : "no";
    out.best_side = out[fav] ? fav : null;
    out.best_edge = out[fav] ? out[fav].edge : null;
  }
  return out;
}

function snapshot() {
  const coinsNeeded = neededCoins();
  const stats: Record<string, any> = {};
  for (const c of coinsNeeded) stats[c] = coinStats(c);
  const rows: any[] = [];
  const allowed = fifteenCoins();
  for (const m of markets.values()) {
    if (m.kind !== "15m" || !allowed.has(m.coin)) continue;
    if (m.status && !["active", "open"].includes(m.status)) continue;
    try { const r = evaluate(m, stats[m.coin] || {}); if (r.kind === "15m") windowExtras(r); rows.push(r); } catch (e) { noteError("eval " + m.ticker + ": " + e); }
  }
  // order coins by number of open contracts (most active first)
  const cnt: Record<string, number> = {};
  for (const m of rows) cnt[m.coin] = (cnt[m.coin] || 0) + 1;
  const order = Object.keys(stats).sort((a, b) => (cnt[b] || 0) - (cnt[a] || 0));
  const sErr = [...series.values()].filter((s) => s.error).map((s) => s.ticker + ": " + s.error);
  if (discoveryNote) sErr.unshift(discoveryNote);
  return {
    now: Date.now() / 1000, coins: stats, coin_order: order, markets: rows,
    series_active: [...series.values()].filter((s) => s.count > 0).length,
    series_total: series.size, series_errors: sErr, errors: errors.slice(-5), horizon_h: HORIZON_H,
    model: MODEL_INFO,
    tally: tally(),
  };
}

// ---------------------------------------------------------------- server
function authorized(req: Request) {
  if (!PASSWORD) return true;
  const h = req.headers.get("authorization") || "";
  if (!h.startsWith("Basic ")) return false;
  const [, pw] = atob(h.slice(6)).split(":");
  return pw === PASSWORD;
}

Bun.serve({
  port: PORT,
  hostname: "0.0.0.0",
  fetch(req) {
    if (!authorized(req))
      return new Response("Password required", { status: 401, headers: { "WWW-Authenticate": 'Basic realm="Markets Edge Board"' } });
    const url = new URL(req.url);
    if (url.pathname.endsWith("/api/state")) {
      return new Response(JSON.stringify(snapshot()), { headers: { "Content-Type": "application/json", "Cache-Control": "no-store" } });
    }
    if (url.pathname === "/health") return new Response("ok");
    if (url.pathname === "/" || url.pathname === "/index.html")
      return new Response(PAGE, { headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" } });
    return new Response("not found", { status: 404 });
  },
});
console.log(`Markets Edge Board listening on :${PORT}`);

await discover();
seriesLoop(true); // 15-minute markets only
quoteLoop();
spotFastLoop();
ratesLoop();
candleLoop();
bookLoop();
sampleLoop();
resultLoop();
bootstrap();
fitLoop();
(async () => { while (true) { try { snapshot(); } catch {} await sleep(2000); } })(); // keep 15m history even with no viewers
(async () => { while (true) { await sleep(30 * 60_000); await discover(); } })();

