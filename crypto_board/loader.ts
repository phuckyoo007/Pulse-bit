// Crypto Edge Board loader. The board's full source lives in the Pulse-bit repo (crypto_board/index.ts) so it can be
// updated with a git push + restart. Read-only dashboard: never places orders.
const SRC = "https://raw.githubusercontent.com/phuckyoo007/Pulse-bit/main/crypto_board/index.ts";
const res = await fetch(SRC, { headers: { "Cache-Control": "no-cache" } });
if (!res.ok) throw new Error(`couldn't load the board source (${res.status})`);
let src = await res.text();
// Patches applied at load time; remove once the same changes are committed in the repo.
const patch = (from: string, to: string) => {
  if (!src.includes(from)) console.log("[loader] patch target not found: " + from.slice(0, 50));
  src = src.replace(from, () => to);
};
// 30 Oct: big UP / DOWN badge on every coin card + a consensus banner (how many coins agree).
patch(".pk .sd.yes{color:var(--green)}.pk .sd.no{color:#ff7b72}",
  `.pk .sd.yes{color:var(--green)}.pk .sd.no{color:#ff7b72}
.pk .sd{border-radius:10px;padding:4px 0;margin:4px 0;font-size:24px;letter-spacing:.03em}
.pk .sd.yes{background:rgba(63,185,80,.18);border:2px solid var(--green)}
.pk .sd.no{background:rgba(255,123,114,.18);border:2px solid #ff7b72}
.pk.gr .sd{background:#0d1117}
.cons{background:var(--bg);border:1px solid var(--line);border-radius:12px;padding:10px 12px;margin:0 0 10px;text-align:center}
.cons .ch{font-size:19px}.cons .ct{display:block;font-size:13px;font-weight:900;letter-spacing:.08em;margin-top:2px}
.cons.up .ct{color:var(--green)}.cons.dn .ct{color:#ff7b72}.cons.mx .ct{color:var(--amber)}
.cbar{display:flex;height:28px;border-radius:8px;overflow:hidden;margin:8px 0;font-weight:900;font-size:15px;line-height:28px;color:#0d1117}
.cbar .cu{background:var(--green)}.cbar .cd{background:#ff7b72}
.cons .cl{display:flex;justify-content:space-between;gap:8px;font-size:12px;font-weight:700;flex-wrap:wrap}
.cons .cl .u{color:var(--green)}.cons .cl .d{color:#ff7b72}
.pk{position:relative;padding-right:22px}.pk.gr{padding-right:24px}
.mbar{position:absolute;right:5px;top:34px;bottom:10px;width:12px;border-radius:6px;background:rgba(139,148,158,.2);overflow:hidden}
.pk.gr .mbar{background:rgba(13,17,23,.6)}
.mmid{position:absolute;left:0;right:0;top:50%;height:2px;margin-top:-1px;background:#c9d1d9;opacity:.8;z-index:2}
.mfill{position:absolute;left:0;right:0}
.mfill.up{bottom:50%;background:var(--green);border-radius:6px 6px 0 0}
.mfill.dn{top:50%;background:#ff7b72;border-radius:0 0 6px 6px}
.pk.gr .mfill.up{background:#fff}
.mfill.flat{top:calc(50% - 1px);height:3px;background:#8b949e}
.mfill.up,.mfill.dn{animation:mpulse 1s ease-in-out infinite}
@keyframes mpulse{0%,100%{opacity:.45;filter:brightness(.9)}50%{opacity:1;filter:brightness(1.5)}}
.tw{position:absolute;top:0;left:0;right:0;bottom:0;border-radius:10px;pointer-events:none;z-index:3;border:3px solid #ff2d2d;animation:tflash .8s step-end infinite}
.tw.lo{border-color:#ffb020}
.tw span{position:absolute;bottom:-1px;left:50%;transform:translateX(-50%);background:#ff2d2d;color:#fff;font-size:11px;font-weight:900;padding:1px 8px;border-radius:8px 8px 0 0;white-space:nowrap;letter-spacing:.04em}
.tw.lo span{background:#ffb020;color:#0d1117}
@keyframes tflash{50%{opacity:.25}}
.mbar.tu{box-shadow:0 0 7px 1px var(--green)}.mbar.td{box-shadow:0 0 7px 1px #ff7b72}
.mtr{position:absolute;right:4px;top:16px;width:14px;text-align:center;font-size:13px;font-weight:900;line-height:14px;z-index:3;animation:tblink .7s step-end infinite}
.mtr.up{color:var(--green)}.mtr.dn{color:#ff7b72}.pk.gr .mtr.up{color:#fff}
@keyframes tblink{50%{opacity:.15}}
.mfill.fa{background:var(--green)}.mfill.ag{background:#ff3b3b}
.pk.gr .mfill.fa{background:#fff}.pk.gr .mfill.ag{background:#ff3b3b}
#strip{display:flex;align-items:stretch;gap:6px;margin:0 0 8px}
#strip .sh{flex:1;min-width:0}
#strip .sdv{flex:0 0 3px;border-radius:2px;background:linear-gradient(var(--green),#ff7b72)}
#strip .shd{font-size:12px;font-weight:900;letter-spacing:.06em;text-align:center;margin-bottom:5px;padding:3px 0;border-radius:6px}
#strip .shd span{opacity:.75;margin-left:4px}
#strip .up .shd{color:var(--green);background:rgba(63,185,80,.14)}#strip .dn .shd{color:#ff7b72;background:rgba(255,123,114,.14)}
#strip .sg{display:grid;grid-template-columns:repeat(auto-fill,minmax(78px,1fr));gap:5px}
#strip .se{grid-column:1/-1;text-align:center;color:var(--dim);font-size:12px;padding:20px 0}
.st{position:relative;min-width:0;min-height:112px;border:2px solid #484f58;border-radius:8px;background:var(--panel);text-align:center;padding:3px 1px 3px;box-sizing:border-box;overflow:hidden;display:flex;flex-direction:column}
.st .sy{font-size:11px;font-weight:900;color:var(--text);line-height:13px;white-space:nowrap}.st .sy b{font-size:12px}.st .sy .yes{color:var(--green)}.st .sy .no{color:#ff7b72}.st .sy .none{color:var(--dim)}
.st .sp{font-size:15px;font-weight:900;color:#39d353;line-height:18px;font-variant-numeric:tabular-nums;letter-spacing:-.6px;white-space:nowrap}
.st .sw{font-size:8px;font-weight:900;color:#fff;line-height:10px}
.st .bz{display:flex;flex:1;min-height:0;margin-top:2px}
.st .bc{flex:1;min-width:0;display:flex;flex-direction:column;align-items:center}
.st .bw{font-size:6.5px;font-weight:900;line-height:8px;letter-spacing:-.2px;color:var(--dim);white-space:nowrap;height:8px}
.st .bw.u{color:var(--green)}.st .bw.d{color:#ff7b72}
.st .sb2,.st .sb{position:relative;flex:1;min-height:30px;width:11px;margin:1px 0;border-radius:5px;background:rgba(139,148,158,.25);overflow:hidden}
.st .bl{font-size:5.5px;font-weight:900;line-height:8px;color:var(--text);letter-spacing:-.1px;white-space:nowrap}
.st.fa{border-color:var(--green)}.st.ag{border-color:#ffb020;animation:tflash .8s step-end infinite}.st.ag.hi{border-color:#ff2d2d}
/* 1 Nov 19:18: PRICE meter runs the full height on the box's left edge, MOMENTUM the full height on the right edge */
.st{position:relative;padding-left:13px !important;padding-right:13px !important;overflow:hidden}
.st .bz{position:absolute;top:4px;bottom:4px;left:2px;right:2px;margin:0;display:flex;justify-content:space-between;pointer-events:none;z-index:2}
.st .bc{flex:0 0 auto;width:8px}
.st .sb2,.st .sb{width:8px !important;border-radius:4px !important}
.st .bc .bw,.st .bc .bl{display:none}
.st .sb2,.st .sb{flex:1;min-height:0;margin:0;height:100%}
.st .dl{right:13px !important}
@media (min-width:700px){.st{padding-left:24px !important;padding-right:24px !important}.st .bz{top:6px;bottom:6px;left:4px;right:4px}.st .bc{width:16px}.st .sb2,.st .sb{width:16px !important;border-radius:8px !important}.st .dl{right:24px !important}.st .go{font-size:11px !important;letter-spacing:-.3px}}
@media (min-width:700px){#strip{gap:10px}#strip .sdv{flex-basis:4px}#strip .shd{font-size:16px}#strip .sg{grid-template-columns:repeat(auto-fill,minmax(105px,1fr));gap:8px}.st{min-height:170px;padding:8px 4px;border-radius:12px}.st .sy{font-size:16px;line-height:19px}.st .sy b{font-size:22px}.st .sp{font-size:28px;line-height:32px;letter-spacing:-.5px}.st .sw{font-size:13px;line-height:16px}.st .bw,.st .bl{font-size:10px;line-height:12px;height:12px;letter-spacing:0}.st .sb2,.st .sb{width:16px;border-radius:8px}}`);
patch('<div class="pickbox" id="picks"></div>', '<div id="strip"></div><div id="consensus"></div><div class="pickbox" id="picks"></div>');
patch('<div class="sd \'+s+\'">\'+(s==="yes"?"YES ▲":"NO ▼")+\'</div>',
  '<div class="sd \'+s+\'">\'+(s==="yes"?"▲ UP":"▼ DOWN")+\'</div>');
patch('function renderPicks(list,crit){$("picks")',
  `function __consensus(list){
  var up=[],dn=[];
  list.slice().sort(function(a,b){return fixedOrder(a.coin,b.coin)}).forEach(function(m){if(m.best_side==="yes")up.push(m.coin);else if(m.best_side==="no")dn.push(m.coin);});
  var n=up.length+dn.length,top=Math.max(up.length,dn.length),lead=up.length>=dn.length?"UP":"DOWN",pct=n?Math.round(top/n*100):0;
  var tag=!n?"WAITING FOR DATA":(top===n?"ALL AGREE":(pct>=75?"STRONG AGREEMENT":(pct>=60?"LEANING "+lead:"SPLIT / MIXED")));
  var cls=!n||pct<60?"mx":(lead==="UP"?"up":"dn");
  var el=document.getElementById("consensus");if(!el)return;
  el.innerHTML='<div class="cons '+cls+'"><div class="ch"><b>'+top+' of '+n+'</b> coins say <b>'+lead+(lead==="UP"?" ▲":" ▼")+'</b><span class="ct">'+tag+'</span></div>'
    +'<div class="cbar"><div class="cu" style="flex:'+up.length+'">'+(up.length?"▲ "+up.length:"")+'</div><div class="cd" style="flex:'+dn.length+'">'+(dn.length?"▼ "+dn.length:"")+'</div></div>'
    +'<div class="cl" style="justify-content:center;color:var(--dim);margin-bottom:3px">Top boxes: left bar $ = how fast your buy price is moving, right bar = momentum. Bar on each card = live momentum (fills up = rising, down = falling; green = moving with the card pick, red = against it), bigger + faster pulse = stronger. Blinking arrow on the bar = momentum growing (green) or losing steam (red). Flashing border = slowing or turning against its side</div>'
    +'<div class="cl"><span class="u">UP: '+(up.join(" ")||"–")+'</span><span class="d">DOWN: '+(dn.join(" ")||"–")+'</span></div></div>';
}
var __mom={},__hist={},__pxh={};
function __mval(k){
  var s=DATA&&DATA.coins&&DATA.coins[k];if(!s)return null;
  var v=s.ann_vol_30m||s.ann_vol_24h;if(!v||s.chg_1m==null||s.chg_5m==null)return null;
  var sm=v/Math.sqrt(525600),z1=s.chg_1m/sm,z5=s.chg_5m/(sm*Math.sqrt(5));
  var m=Math.max(-1,Math.min(1,(0.4*z1+0.6*z5)/2)),p=__mom[k];
  m=p==null?m:p*0.85+m*0.15;__mom[k]=m;
  var now=performance.now()/1000,h=__hist[k]||(__hist[k]=[]);
  if(!h.length||now-h[h.length-1][0]>=0.8)h.push([now,m]);
  while(h.length&&now-h[0][0]>30)h.shift();
  return m;
}
function __trend(k,m){
  var h=__hist[k],now=performance.now()/1000,ref=null;
  if(!h||m==null)return 0;
  for(var i=0;i<h.length;i++){if(now-h[i][0]>=6)ref=h[i][1];}
  return ref==null?0:m-ref;
}
function __bars(){
  var t=performance.now()/1000;
  document.querySelectorAll("#picks .pk[data-coin]").forEach(function(c){
    var k=c.getAttribute("data-coin"),m=__mval(k),a=m==null?0:Math.abs(m),up=m!=null&&m>=0,flat=m==null||a<0.08;
    var side=c.getAttribute("data-side"),good=side==="yes"?up:(side==="no"?!up:null),fcls=good==null?"":(good?" fa":" ag");
    var tr=__trend(k,m),tdir=tr>0.04?"up":(tr<-0.04?"dn":"");
    var per=(1.7-1.1*a).toFixed(2),dl=(-(t%per)).toFixed(2),h=Math.round(a*50);
    var fill=flat?'<i class="mfill flat"></i>':'<i class="mfill '+(up?"up":"dn")+fcls+'" style="height:'+h+'%;animation-duration:'+per+'s;animation-delay:'+dl+'s"></i>';
    var d=document.createElement("div");d.className="mbar"+(tdir?(tdir==="up"?" tu":" td"):"");
    d.title=k+" momentum: "+(m==null?"no data":(flat?"flat":(up?"rising ":"falling ")+Math.round(a*100)+"%")+(tdir?(tdir==="up"?", getting stronger":", losing steam"):""));
    d.innerHTML=fill+'<b class="mmid"></b>';c.appendChild(d);
    if(tdir){var ar=document.createElement("div");ar.className="mtr "+tdir;ar.textContent=tdir==="up"?"▲":"▼";
      ar.style.animationDelay=(-(t%0.7)).toFixed(2)+"s";c.appendChild(ar);}
    var ag=m==null?0:(side==="yes"?-m:(side==="no"?m:0));
    var fav=m==null?0:(side==="yes"?m:(side==="no"?-m:0)),ftr=side==="yes"?tr:(side==="no"?-tr:0);
    var msg=null,cls2="lo";
    if(ag>=0.5){msg="⚠ SELL WATCH";cls2="hi";}
    else if(ag>=0.2){msg="⚠ TURNING";}
    else if(fav>=0.1&&ftr<=-0.08){msg="⚠ SLOWING";}
    if(msg){var w=document.createElement("div");w.className="tw "+cls2;
      w.innerHTML='<span>'+msg+'</span>';
      w.style.animationDelay=(-(t%0.8)).toFixed(2)+"s";c.appendChild(w);}
  });
}
function __strip(list){
  var el=document.getElementById("strip");if(!el||!DATA||!DATA.coins)return;
  var by={},t=performance.now()/1000;list.forEach(function(m){by[m.coin]=m;});
  var keys=Object.keys(DATA.coins).sort(function(a,b){return fixedOrder(a,b)}),html="";
  var hu=[],hd=[];
  keys.forEach(function(k){html="";
    var mk=by[k],m=__mom[k]!=null?__mom[k]:__mval(k),a=m==null?0:Math.abs(m),up=m!=null&&m>=0,flat=m==null||a<0.08;
    var side=mk&&mk.best_side?mk.best_side:"none";
    var good=side==="yes"?up:(side==="no"?!up:null),fcls=good==null?"":(good?" fa":" ag");
    var ag=m==null?0:(side==="yes"?-m:(side==="no"?m:0));
    var cls="st"+(flat?"":(good?" fa":(good===false?" ag"+(ag>=0.5?" hi":""):"")));
    if(!flat&&good===false&&ag<0.2)cls="st";
    var per=(1.7-1.1*a).toFixed(2),dl=(-(t%per)).toFixed(2),h=Math.round(a*50);
    var fill=flat?'<i class="mfill flat"></i>':'<i class="mfill '+(up?"up":"dn")+fcls+'" style="height:'+h+'%;animation-duration:'+per+'s;animation-delay:'+dl+'s"></i>';
    var p=mk&&mk.cr?mk.cr.sideP:null,sd=mk&&side!=="none"?mk[side]:null;
    var win=p==null?"–":Math.round(p*100)+"%",mod=p==null?"–":Math.min(99,Math.floor(p*100))+"¢",pr=sd&&sd.ask!=null?(sd.ask*100).toFixed(1)+"¢":"–",prt=sd&&sd.ask!=null?("$"+Number(sd.ask).toFixed(3)).replace("$0.","$."):"–";
    var cp=sd&&sd.ask!=null?Number(sd.ask):null,sp=0,sdir=0,dv="",hk=k+":"+side;
    if(cp!=null){var ph=__pxh[hk]||(__pxh[hk]=[]);if(!ph.length||t-ph[ph.length-1][0]>=0.5)ph.push([t,cp]);while(ph.length&&t-ph[0][0]>30)ph.shift();
      var ref=null;for(var qi=0;qi<ph.length;qi++){if(t-ph[qi][0]>=5)ref=ph[qi][1];}
      var dd=ref==null?0:cp-ref;sdir=Math.abs(dd)>=0.0005?(dd>0?1:-1):0;
      sp=Math.min(1,Math.abs(dd)/0.05);dv=(dd>=0?"+":"-")+Math.abs(dd).toFixed(3);}
    var sfill=sdir===0||sp<0.05?'<i class="mfill flat"></i>':'<i class="mfill '+(sdir>0?"up":"dn")+'" style="height:'+Math.round(sp*50)+'%;animation-duration:'+(1.7-1.1*sp).toFixed(2)+'s;animation-delay:'+(-(t%(1.7-1.1*sp))).toFixed(2)+'s"></i>';
    var sxc=sdir>0?"u":(sdir<0?"d":"");
    var mw=flat?"FLAT":(up?"RISING":"FALLING"),mc=flat?"":(up?"u":"d"),pw=sdir===0||sp<0.05?"STEADY":(sdir>0?"RISING":"FALLING"),pc=sdir===0||sp<0.05?"":(sdir>0?"u":"d");
    html+='<div class="'+cls+'" style="animation-delay:'+(-(t%0.8)).toFixed(2)+'s"><div class="sy">'+k+' <b class="'+side+'">'+(side==="yes"?"▲":(side==="no"?"▼":"–"))+'</b></div><div class="sw">'+win+'</div><div class="sp">'+prt+'</div><div class="bz"><div class="bc"><div class="bw '+pc+'">'+pw+'</div><div class="sb2">'+sfill+'<b class="mmid"></b></div><div class="bl">PRICE</div></div><div class="bc"><div class="bw '+mc+'">'+mw+'</div><div class="sb">'+fill+'<b class="mmid"></b></div><div class="bl">MOMENTUM</div></div></div></div>';
    var goUp=side==="yes"||(side==="none"&&m!=null&&m>=0);(goUp?hu:hd).push(html);
  });
  el.innerHTML='<div class="sh up"><div class="shd">▲ GOING UP <span>'+hu.length+'</span></div><div class="sg">'+(hu.join("")||'<div class="se">none</div>')+'</div></div>'
    +'<div class="sdv"></div>'
    +'<div class="sh dn"><div class="shd">▼ GOING DOWN <span>'+hd.length+'</span></div><div class="sg">'+(hd.join("")||'<div class="se">none</div>')+'</div></div>';
}
function __err(w,e){try{var el=document.getElementById("strip");if(el)el.innerHTML='<div style="grid-column:1/-1;color:#ff7b72;font-size:12px;padding:6px">Boxes error ('+w+'): '+String(e&&e.message||e)+'</div>';}catch(x){}}
function renderPicks(list,crit){try{__consensus(list);}catch(e){__err("banner",e);}
  try{__renderPicks0(list,crit);}finally{
    try{__bars();}catch(e){__err("bars",e);}
    try{__strip(list);}catch(e){__err("strip",e);}}}
function __renderPicks0(list,crit){$("picks")`);
patch(`'<div class="pk'+(m.sig==="bid"?' go':'')`, `'<div data-coin="'+m.coin+'" data-side="'+(m.best_side||"")+'" class="pk'+(m.sig==="bid"?' go':'')`);
// 1 Nov: the price you BUY at (Kalshi ask) shown in cents to 1 decimal (Kalshi moves in tenths up in the 90s), e.g. 93.1¢.
patch("const c=v=>", 'const cd=v=>v==null?"–":(Number(v)*100).toFixed(1)+"¢";\nconst c=v=>');
patch('" @ "+c(sd.ask)+"¢</span>":"–"}</td>', '" @ "+cd(sd.ask)+"</span>":"–"}</td>');
patch("<b>@ '+c(sd.ask)+'¢</b></td>'", "<b>@ '+cd(sd.ask)+'</b></td>'");
patch("<strong>'+c(sd.ask)+'¢</strong>", "<strong>'+cd(sd.ask)+'</strong>");
patch("Price <b>'+c(nsd.ask)+'¢</b>", "Price <b>'+cd(nsd.ask)+'</b>");
patch("' @ '+c(sd.ask)+'¢</span> · edge", "' @ '+cd(sd.ask)+'</span> · edge");
patch('" @ "+c(sd.ask)+"¢ · pays', '" @ "+cd(sd.ask)+" · pays');
patch('sd?c(sd.ask)+"¢":"–"', 'sd?cd(sd.ask):"–"');
patch("'\">'+c(m[s].ask)+'¢</b></div>", "'\">'+cd(m[s].ask)+'</b></div>");
patch("c(m.yes_ask)}¢</b><small>bid", "cd(m.yes_ask)}</b><small>bid");
patch("c(m.no_ask)}¢</b><small>bid", "cd(m.no_ask)}</b><small>bid");
// 1 Nov: faster refresh. Page polls 4x/sec (no overlapping requests); Kalshi quotes 2x/sec.
patch("async function poll(){", "var __pb=0;async function poll(){if(__pb)return;__pb=1;try{await __poll0();}finally{__pb=0;}}\nasync function __poll0(){");
patch("setInterval(poll,1000);poll();", "setInterval(poll,250);poll();");
patch("await sleep(Math.max(200, 1000 - (Date.now() - t0)));", "await sleep(Math.max(200, 500 - (Date.now() - t0)));");
// 1 Nov 16:30 CT (Matt): RPM-style gauge in every top box -- needle = momentum for the box's pick (left/red = against,
// right/green = with), number = momentum score -100..+100, price change in cents over ~5s, and a one-word verdict:
// SELL (momentum meter red against the pick -- what the bot sells manual bets on), EASING (price falling), HOLD.
patch(".st.fa{border-color:var(--green)}",
  `.gv{margin:1px 0 0}.gs{width:100%;max-width:64px;height:auto;display:block;margin:0 auto}
.gn{font-size:14px;font-weight:900;line-height:15px;font-variant-numeric:tabular-nums;color:var(--text)}
.gp{font-size:9px;font-weight:900;line-height:11px;font-variant-numeric:tabular-nums;color:var(--dim)}
.gn.u,.gp.u{color:var(--green)}.gn.d,.gp.d{color:#ff7b72}
.gx{font-size:10px;font-weight:900;letter-spacing:.05em;border-radius:5px;margin:2px 3px 0;line-height:15px;color:#0d1117}
.gx.s{background:#ff3b3b;color:#fff;animation:tflash .6s step-end infinite}.gx.e{background:#ffb020}.gx.h{background:#3fb950}.gx.n{background:#484f58;color:#c9d1d9}
@media (min-width:700px){.gs{max-width:110px}.gn{font-size:24px;line-height:26px}.gp{font-size:14px;line-height:17px}.gx{font-size:16px;line-height:22px;margin:4px 6px 0}}
.st.fa{border-color:var(--green)}`);
patch("function __err(w,e){",
  `function __sigcls(m,side,sdir,p){
  if(m==null||side==="none")return "";
  var g=side==="no"?-m:m;
  if(g<=-0.08||sdir<0)return " ssell";
  if(p!=null&&p>0.5&&g>=0.08&&sdir>0)return " sbuy";
  return "";
}
function __gauge(m,side,sdir,dd,p,k){
  var g=m==null?null:(side==="no"?-m:m);
  if(g==null)return '<div class="gv"><div class="gn">–</div><div class="gx n">NO DATA</div></div>';
  function pt(a,r){var q=a*Math.PI/180;return [(30+r*Math.sin(q)).toFixed(1),(32-r*Math.cos(q)).toFixed(1)];}
  function arc(a0,a1,col){var p0=pt(a0,26),p1=pt(a1,26);return '<path d="M'+p0[0]+','+p0[1]+' A26,26 0 0 1 '+p1[0]+','+p1[1]+'" stroke="'+col+'" stroke-width="6" fill="none"/>';}
  var ang=Math.max(-1,Math.min(1,g))*90,np=pt(ang,22),red=g<=-0.08;
  var tr=__trend(k,__mom[k]);tr=side==="no"?-tr:tr;
  var buy=p!=null&&p>0.5&&g>=0.08&&sdir>0,sell=red||sdir<0;
  var v=buy?"$ BUY":(sell?"SELL":"HOLD"),vc=buy?"b":(sell?"s":"h");
  var ol=red?"▼ REVERSING":(g<0.08?"■ STALLED":(tr<-0.04?"◆ SLOWING":"▲ CONTINUING")),oc=red?"d":(g<0.08||tr<-0.04?"a":"u");
  dd=dd||0;var pv=(dd>=0?"+":"−")+Math.abs(dd*100).toFixed(1)+"¢";
  return (buy?'<div class="dl">$</div>':'')+'<div class="gv"><svg viewBox="0 0 60 36" class="gs">'+arc(-90,-7.2,"#ff3b3b")+arc(-7.2,7.2,"#ffb020")+arc(7.2,90,"#3fb950")
    +'<line x1="30" y1="32" x2="'+np[0]+'" y2="'+np[1]+'" stroke="#fff" stroke-width="2.5" stroke-linecap="round"/><circle cx="30" cy="32" r="3" fill="#fff"/></svg>'
    +'<div class="gn '+(g>=0.08?"u":(red?"d":""))+'">'+(g>=0?"+":"−")+Math.round(Math.abs(g)*100)+'</div>'
    +'<div class="gp '+(dd>0.00049?"u":(dd<-0.00049?"d":""))+'">'+pv+'</div>'
    +'<div class="go '+oc+'">'+ol+'</div>'
    +'<div class="gx '+vc+'">'+v+'</div></div>';
}
function __err(w,e){`);
patch(`<div class="sp">'+prt+'</div>`, `<div class="sp">'+prt+'</div>'+__gauge(m,side,sdir,(typeof dd==="number"?dd:0),p,k)+'`);
// 1 Nov 16:56 CT (Matt): BUY / SELL signals on the top boxes.
//   BUY  ($ badge + pulsing green glow): pick more likely than not (>50%), momentum green for the pick, price rising.
//   SELL (flashing red border): momentum turned against the pick, or the price stopped growing and is falling.
//   Outlook line: CONTINUING (momentum with the pick and holding), SLOWING (with it but losing steam),
//   STALLED (flat), REVERSING (rolling the other way).
patch(`html+='<div class="'+cls+'" style=`, `html+='<div class="'+cls+__sigcls(m,side,sdir,p)+'" style=`);
patch(".st.fa{border-color:var(--green)}",
  `.st .dl{position:absolute;top:0;right:3px;font-size:17px;font-weight:900;line-height:18px;color:#39d353;text-shadow:0 0 6px #39d353;z-index:4;animation:tblink .7s step-end infinite}
.st.sbuy{border-color:#39d353 !important;animation:bpulse 1s ease-in-out infinite !important}
@keyframes bpulse{0%,100%{box-shadow:0 0 0 0 rgba(57,211,83,.85)}50%{box-shadow:0 0 12px 4px rgba(57,211,83,.85)}}
.st.ssell{border:3px solid #ff2d2d !important;animation:sflash .5s step-end infinite !important}
@keyframes sflash{50%{border-color:rgba(255,45,45,.12);box-shadow:none}0%{box-shadow:0 0 10px 2px rgba(255,45,45,.8)}}
.go{font-size:7.5px;font-weight:900;line-height:10px;letter-spacing:-.1px;white-space:nowrap}
.go.u{color:#39d353}.go.a{color:#ffb020}.go.d{color:#ff5b52}
.gx.b{background:#39d353;color:#0d1117;animation:tblink 1s step-end infinite}
@media (min-width:700px){.st .dl{font-size:30px;line-height:30px;right:6px}.go{font-size:13px;line-height:17px}}
.st.fa{border-color:var(--green)}`);
// 1 Nov 16:35 CT (Matt): price to 2 decimals ($.93 -- the third was always 0), and digital readouts on both meters:
// PRICE = cents moved in the last ~5s, MOMENTUM = -100..+100 (coin falling .. rising), in place of the RISING/FALLING words.
patch('("$"+Number(sd.ask).toFixed(3)).replace("$0.","$.")', '("$"+Number(sd.ask).toFixed(2)).replace("$0.","$.")');
patch(`<div class="bw '+pc+'">'+pw+'</div>`,
  `<div class="bw dg '+pc+'">'+(cp==null?"–":((typeof dd==="number"?dd:0)>=0?"+":"−")+Math.abs((typeof dd==="number"?dd:0)*100).toFixed(1)+"¢")+'</div>`);
patch(`<div class="bw '+mc+'">'+mw+'</div>`,
  `<div class="bw dg '+mc+'">'+(m==null?"–":(m>=0?"+":"−")+Math.round(Math.abs(m)*100))+'</div>`);
patch(".st.fa{border-color:var(--green)}",
  `.st .bw.dg{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:9px;line-height:11px;height:11px;letter-spacing:-.3px;color:#c9d1d9;background:#000;border-radius:3px;padding:0 2px}
.st .bw.dg.u{color:#39d353}.st .bw.dg.d{color:#ff5b52}
@media (min-width:700px){.st .bw.dg{font-size:16px;line-height:19px;height:19px;padding:0 4px}}
.st.fa{border-color:var(--green)}`);
// 2 Nov 09:53 CT (Matt): the $ BUY badge sat on top of the up/down arrow -- move it to the LEFT of the coin name
// (inline in the title, like an element card's corner number) and drop the old top-right badge.
patch(`<div class="sy">'+k+' <b class=`, `<div class="sy">'+(__sigcls(m,side,sdir,p)===" sbuy"?'<i class="dl2">$</i>':'')+k+' <b class=`);
patch(".st.fa{border-color:var(--green)}",
  `.st .dl{display:none !important}
.st .dl2{font-style:normal;font-size:15px;font-weight:900;color:#39d353;text-shadow:0 0 6px #39d353;margin-right:3px;animation:tblink .7s step-end infinite}
@media (min-width:700px){.st .dl2{font-size:24px;margin-right:5px}}
.st.fa{border-color:var(--green)}`);
// 2 Nov 10:17 CT (Matt): BTC / XRP / SOL / BNB as four big playing-card style cards across the top, above the other
// small boxes (which no longer repeat those four). Same contents and BUY / SELL signals, just bigger.
patch("var hu=[],hd=[];", 'var hu=[],hd=[],bg={};');
patch('var goUp=side==="yes"||(side==="none"&&m!=null&&m>=0);(goUp?hu:hd).push(html);',
  'if(__BIG.indexOf(k)>=0){bg[k]=html.replace(\'<div class="st\',\'<div class="st bigc\').slice(0,-6)+\'<div class="cnr">\'+k+\'</div></div>\';var __gu=(side==="yes"||(side==="none"&&m!=null&&m>=0));bg[k]=[bg[k].replace(\'<div class="sy">\',\'<div class="bdir \'+(__gu?\'u\':\'d\')+\'">\'+(__gu?\'▲ UP\':\'▼ DOWN\')+\'</div><div class="sy">\'),__gu];}\n    var goUp=side==="yes"||(side==="none"&&m!=null&&m>=0);(goUp?hu:hd).push(html);');
patch(`el.innerHTML='<div class="sh up">`, `el.innerHTML='<div class="bigrow">'+__BIG.filter(function(c){return bg[c]&&bg[c][1];}).map(function(c){return bg[c][0];}).join('')+'<div class="bdv"></div>'+__BIG.filter(function(c){return bg[c]&&!bg[c][1];}).map(function(c){return bg[c][0];}).join('')+'</div><div class="hdiv"></div>'+'<div class="sh up">`);
patch("function __err(w,e){", 'var __BIG=["BTC","XRP","SOL","BNB"];\nfunction __err(w,e){');
patch(".st.fa{border-color:var(--green)}",
  `#strip{flex-wrap:wrap}
.bigrow{flex:0 0 100%;display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:7px;margin:0 0 4px}
.st.bigc{min-height:230px;border-width:3px;border-radius:14px;padding-top:7px;padding-bottom:16px;background:linear-gradient(160deg,#1c2330,#0d1117)}
.st.bigc .sy{font-size:16px;line-height:20px}.st.bigc .sy b{font-size:17px}.st.bigc .dl2{font-size:19px}
.st.bigc .sw{font-size:12px;line-height:15px}.st.bigc .sp{font-size:22px;line-height:26px}
.st.bigc .gs{max-width:100%}.st.bigc .gn{font-size:21px;line-height:23px}.st.bigc .gp{font-size:12px;line-height:15px}
.st.bigc .go{font-size:8px;line-height:13px;letter-spacing:-.4px}.st.bigc .gx{font-size:13px;line-height:20px}
.st.bigc .cnr{position:absolute;right:15px;bottom:2px;font-size:10px;font-weight:900;color:var(--dim);transform:rotate(180deg);letter-spacing:.04em}
@media (min-width:700px){.bigrow{gap:14px;margin-bottom:8px}.st.bigc{aspect-ratio:5/7;min-height:0;max-height:440px;padding-top:14px;padding-bottom:26px;border-radius:20px}
.st.bigc .sy{font-size:28px;line-height:34px}.st.bigc .sy b{font-size:30px}.st.bigc .dl2{font-size:34px}.st.bigc .sw{font-size:18px;line-height:22px}
.st.bigc .sp{font-size:44px;line-height:50px}.st.bigc .gs{max-width:200px}.st.bigc .gn{font-size:38px;line-height:42px}.st.bigc .gp{font-size:20px;line-height:24px}
.st.bigc .go{font-size:16px !important;line-height:22px}.st.bigc .gx{font-size:22px;line-height:32px;margin:6px 12px 0}.st.bigc .cnr{font-size:16px;right:30px;bottom:6px}}
.st.fa{border-color:var(--green)}`);
// 2 Nov 12:02 CT (Matt): the four big cards stay in the small groups too; on top they sit LEFT if going up, RIGHT if
// going down (vertical divider between), each with a big GOING UP / GOING DOWN banner; thick divider under the row.
patch(".st.fa{border-color:var(--green)}",
  `.bigrow{display:flex !important;align-items:stretch;min-width:0;max-width:100%}
.bigrow .st.bigc{flex:1 1 0;min-width:0}
.bigrow .bdv{flex:0 0 5px;border-radius:3px;background:linear-gradient(var(--green),#ff7b72);margin:0 2px}
.hdiv{flex:0 0 100%;height:8px;border-radius:4px;margin:8px 0 12px;background:linear-gradient(90deg,var(--green) 0%,var(--green) 49%,#ff7b72 51%,#ff7b72 100%);box-shadow:0 0 10px rgba(201,209,217,.25)}
.st.bigc .bdir{font-size:10px;font-weight:900;letter-spacing:-.3px;overflow:hidden;line-height:16px;border-radius:6px;margin:0 0 3px;white-space:nowrap;color:#0d1117}
.st.bigc .bdir.u{background:#39d353}.st.bigc .bdir.d{background:#ff6b62}
@media (min-width:700px){.bigrow .bdv{flex-basis:8px;margin:0 6px}.hdiv{height:12px;margin:12px 0 16px}.st.bigc .bdir{font-size:20px;line-height:34px;margin:0 6px 8px;border-radius:10px}}
.st.fa{border-color:var(--green)}`);
// 2 Nov 15:10 CT (Matt): count how many times each 15-minute round's pick changed sides (server-side, so it is the
// same for everyone and does not depend on when the page was opened), shown on every card as "⇄ N flips".
patch("windowExtras(r); rows.push(r);", "windowExtras(r); __flipTrack(r); rows.push(r);");
patch("function sigmaFor(st: any, tSec: number) {",
  `const __flips = new Map<string, { side: string; n: number; seen: number }>();
function __flipTrack(r: any) {
  const t = r && r.ticker, s = r && r.best_side;
  if (!t) return;
  let f = __flips.get(t);
  if (!f) { f = { side: s || "", n: 0, seen: Date.now() }; __flips.set(t, f); }
  if (s && f.side && s !== f.side) f.n++;
  if (s) f.side = s;
  f.seen = Date.now();
  r.flips = f.n;
  if (__flips.size > 400) for (const [k, v] of __flips) if (Date.now() - v.seen > 3_600_000) __flips.delete(k);
}
function sigmaFor(st: any, tSec: number) {`);
patch(`<div class="sw">'+win+'</div>`,
  `<div class="sw">'+win+'</div><div class="fl '+(mk&&mk.flips!=null?(mk.flips===0?"z":(mk.flips<=2?"a":"r")):"")+'">⇄ '+(mk&&mk.flips!=null?mk.flips+(mk.flips===1?" flip":" flips"):"–")+'</div>`);
patch(".st.fa{border-color:var(--green)}",
  `.st .fl{font-size:8px;font-weight:900;line-height:11px;color:var(--dim);white-space:nowrap}
.st .fl.z{color:#39d353}.st .fl.a{color:#ffb020}.st .fl.r{color:#ff5b52}
.st.bigc .fl{font-size:11px;line-height:15px}
@media (min-width:700px){.st .fl{font-size:12px;line-height:15px}.st.bigc .fl{font-size:17px;line-height:22px}}
.st.fa{border-color:var(--green)}`);
// 31 Oct: older iPads (iOS 12 Safari) cannot parse "??"; rewrite "x??N" as a plain conditional so the page script loads.
src = src.replace(/([A-Za-z_][A-Za-z0-9_.]*)\?\?(-?\d+)/g, (_m, a, b) => "(" + a + "!=null?" + a + ":" + b + ")");
await Bun.write("/tmp/crypto_board.ts", src);
await import("/tmp/crypto_board.ts");
