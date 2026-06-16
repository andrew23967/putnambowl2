
// Custom leaderboard scrollbar
(function(){
  const scroll = document.getElementById('lb-scroll');
  const thumb  = document.getElementById('lb-thumb');
  const track  = document.getElementById('lb-track');
  if (!scroll || !thumb || !track) return;
  const THUMB_H = 36;
  function updateThumb() {
    const trackH   = track.clientHeight;
    const scrollable = scroll.scrollHeight - scroll.clientHeight;
    const pct      = scrollable > 0 ? scroll.scrollTop / scrollable : 0;
    thumb.style.top = Math.min(pct * (trackH - THUMB_H), trackH - THUMB_H) + 'px';
  }
  scroll.addEventListener('scroll', updateThumb);
  window.addEventListener('resize', updateThumb);
  updateThumb();
  // Drag thumb to scroll
  thumb.addEventListener('mousedown', e => {
    e.preventDefault();
    const startY   = e.clientY;
    const startTop = scroll.scrollTop;
    const trackH   = track.clientHeight;
    function onMove(e) {
      const dy = e.clientY - startY;
      const scrollable = scroll.scrollHeight - scroll.clientHeight;
      scroll.scrollTop = startTop + (dy / (trackH - THUMB_H)) * scrollable;
    }
    function onUp() {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
    }
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  });
  // Click track to jump
  track.addEventListener('click', e => {
    if (e.target === thumb) return;
    const rect = track.getBoundingClientRect();
    const pct  = (e.clientY - rect.top) / rect.height;
    scroll.scrollTop = pct * scroll.scrollHeight;
  });
})();

// Score bars
(function(){
  const data = 0;
  if (!data.length) return;
  const max = Math.max(...data.map(d=>d.score));
  if (max<=0) return;
  document.querySelectorAll('.score-bar-fill').forEach((el,i)=>{
    const rawPct = parseFloat(el.dataset.pct)/max*100;
    const hue = (rawPct/100*120).toFixed(0);
    el.style.background = `hsl(${hue},58%,48%)`;
    setTimeout(()=>{ el.style.width=rawPct.toFixed(1)+'%'; }, 250+i*55);
  });
})();

// Dist bars
document.querySelectorAll('.dist-t1').forEach(el=>{
  setTimeout(()=>{ el.style.width=el.dataset.w+'%'; }, 400);
});

const PALETTE = ['#6366f1','#10b981','#f59e0b','#ef4444','#8b5cf6','#06b6d4','#f97316','#ec4899'];
const pointsData   = 0;
const positionData = 0;
const wrData       = 0;
const effData      = 0;

// â”€â”€ shared helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
const TIP = {backgroundColor:'#0e1422',titleColor:'#f1f5f9',bodyColor:'#64748b',padding:10,cornerRadius:8,borderColor:'rgba(255,255,255,.1)',borderWidth:1};
const SX  = {grid:{display:false},border:{display:false},ticks:{font:{size:10},color:'#475569'}};
const SY  = (e={}) => ({grid:{color:'rgba(255,255,255,.04)',drawTicks:false},border:{display:false},ticks:{font:{size:10},color:'#475569',padding:6},...e});
const LEG = {position:'bottom',labels:{boxWidth:8,padding:14,font:{size:10},color:'#64748b'}};

function lineDS(data, fill, ctx, h) {
  return data[0].slice(1).map((p,i)=>{
    const c=PALETTE[i%PALETTE.length];
    let bg='transparent';
    if(fill&&ctx){const g=ctx.createLinearGradient(0,0,0,h);g.addColorStop(0,c+'28');g.addColorStop(1,c+'00');bg=g;}
    return{label:p,data:data.slice(1).map(r=>r[i+1]),borderColor:c,backgroundColor:bg,
      pointBackgroundColor:c,pointBorderColor:'rgba(7,11,20,.9)',pointBorderWidth:1.5,
      pointRadius:3,pointHoverRadius:6,tension:0.4,fill,borderWidth:2};
  });
}

// â”€â”€ main score/rank â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
let mainChart=null, currentMode='score';

function buildMainOn(cid,mode,showLegend=false){
  const el=document.getElementById(cid);if(!el)return null;
  const ctx=el.getContext('2d'),data=mode==='score'?pointsData:positionData,rev=mode==='rank';
  if(!data||data.length<2)return null;
  const h=el.parentElement.offsetHeight||240;
  return new Chart(ctx,{type:'line',
    data:{labels:data.slice(1).map(r=>'Wk '+r[0]),datasets:lineDS(data,false,ctx,h)},
    options:{responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},
      plugins:{legend:{...LEG,display:showLegend},tooltip:{...TIP,callbacks:{label:c=>` ${c.dataset.label}: ${c.parsed.y}${rev?'':' pts'}`}}},
      scales:{y:SY({reverse:rev}),x:SX},animation:{duration:600,easing:'easeInOutQuart'}}});
}
// Generic draggable pill toggle
// modes[0]=left, modes[1]=right; returns movePill(mode,animate)
function setupPillDrag(trackId,pillId,labelIds,modes,switchFn,getMode){
  function movePill(mode,animate){
    const tr=document.getElementById(trackId),pl=document.getElementById(pillId);
    if(!tr||!pl)return;
    const half=(tr.offsetWidth-6)/2;
    pl.style.transition=animate?'left .22s cubic-bezier(.4,0,.2,1)':'none';
    pl.style.left=mode===modes[0]?'3px':(3+half)+'px';
    document.getElementById(labelIds[0]).style.color=mode===modes[0]?'var(--text)':'var(--muted)';
    document.getElementById(labelIds[1]).style.color=mode===modes[1]?'var(--text)':'var(--muted)';
  }
  const track=document.getElementById(trackId);
  if(!track)return movePill;
  let startX=null,moved=false;
  function pt(e){return e.touches?e.touches[0]:e;}
  function onStart(e){
    startX=pt(e).clientX;moved=false;track.style.cursor='grabbing';
    document.addEventListener('mousemove',onMove);document.addEventListener('mouseup',onEnd);
    document.addEventListener('touchmove',onMove,{passive:false});document.addEventListener('touchend',onEnd);
  }
  function onMove(e){
    if(e.cancelable)e.preventDefault();
    if(Math.abs(pt(e).clientX-startX)>3)moved=true;
    const rect=track.getBoundingClientRect(),half=(rect.width-6)/2;
    const pl=document.getElementById(pillId);
    pl.style.transition='none';
    pl.style.left=Math.max(3,Math.min(pt(e).clientX-rect.left-half/2-3,3+half))+'px';
    const right=pt(e).clientX>=rect.left+rect.width/2;
    document.getElementById(labelIds[0]).style.color=right?'var(--muted)':'var(--text)';
    document.getElementById(labelIds[1]).style.color=right?'var(--text)':'var(--muted)';
  }
  function onEnd(e){
    track.style.cursor='grab';
    document.removeEventListener('mousemove',onMove);document.removeEventListener('mouseup',onEnd);
    document.removeEventListener('touchmove',onMove);document.removeEventListener('touchend',onEnd);
    const rect=track.getBoundingClientRect();
    const ex=(e.changedTouches?e.changedTouches[0]:e).clientX;
    const picked=ex>=rect.left+rect.width/2?modes[1]:modes[0];
    switchFn(moved?picked:(getMode()===modes[0]?modes[1]:modes[0]));
  }
  track.addEventListener('mousedown',onStart);
  track.addEventListener('touchstart',onStart,{passive:true});
  return movePill;
}

function initChart(mode){if(mainChart){mainChart.destroy();mainChart=null;}mainChart=buildMainOn('mainChart',mode);}
let movePillMain;
function switchChart(mode){
  if(mode===currentMode)return;currentMode=mode;initChart(mode);if(movePillMain)movePillMain(mode,true);
  document.getElementById('chart-label').textContent=mode==='score'?'Cumulative Score':'Rank Over Time';
}
movePillMain=setupPillDrag('chart-toggle','toggle-pill',['tog-score','tog-rank'],['score','rank'],switchChart,()=>currentMode);
initChart('score');

// â”€â”€ weekly bars â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
function buildWeeklyOn(cid,showLegend=false){
  const el=document.getElementById(cid);if(!el||!pointsData||pointsData.length<2)return null;
  const players=pointsData[0].slice(1),rows=pointsData.slice(1);
  const weekly=players.map((_,pi)=>rows.map((r,wi)=>parseFloat((r[pi+1]-(wi>0?rows[wi-1][pi+1]:0)).toFixed(2))));
  return new Chart(el.getContext('2d'),{type:'bar',
    data:{labels:rows.map(r=>'Wk '+r[0]),
      datasets:players.map((p,i)=>({label:p,data:weekly[i],backgroundColor:PALETTE[i%PALETTE.length]+'bb',borderColor:PALETTE[i%PALETTE.length],borderWidth:1,borderRadius:3}))},
    options:{responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},
      plugins:{legend:{...LEG,display:showLegend},tooltip:{...TIP,callbacks:{label:c=>` ${c.dataset.label}: ${c.parsed.y} pts`}}},
      scales:{x:SX,y:SY()},animation:{duration:700,easing:'easeInOutQuart'}}});
}
buildWeeklyOn('weeklyChart');

// â”€â”€ win rate / efficiency â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
let analyticsChart=null,analyticsMode='wr';

function buildAnalyticsOn(cid,mode,showLegend=false){
  const el=document.getElementById(cid);if(!el)return null;
  const data=mode==='wr'?wrData:effData;if(!data||data.length<2)return null;
  return new Chart(el.getContext('2d'),{type:'line',
    data:{labels:data.slice(1).map(r=>'Wk '+r[0]),datasets:lineDS(data,false,null,0)},
    options:{responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},
      plugins:{legend:{...LEG,display:showLegend},tooltip:{...TIP,callbacks:{label:c=>` ${c.dataset.label}: ${c.parsed.y}%`}}},
      scales:{y:SY({min:0,max:100,ticks:{font:{size:10},color:'#475569',padding:6,callback:v=>v+'%'}}),x:SX},
      animation:{duration:600,easing:'easeInOutQuart'}}});
}
function initAnalytics(mode){if(analyticsChart){analyticsChart.destroy();analyticsChart=null;}analyticsChart=buildAnalyticsOn('analyticsChart',mode);}
let movePillAnalytics;
function switchAnalytics(mode){
  if(mode===analyticsMode)return;analyticsMode=mode;initAnalytics(mode);if(movePillAnalytics)movePillAnalytics(mode,true);
  document.getElementById('analytics-label').textContent=mode==='wr'?'% Correct Picks Per Week':'% of Max Available Points Captured';
}
movePillAnalytics=setupPillDrag('analytics-toggle','analytics-pill',['tog-wr','tog-eff'],['wr','eff'],switchAnalytics,()=>analyticsMode);
initAnalytics('wr');

// â”€â”€ variance â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
function buildVarianceOn(cid,showLegend=false,showNames=false){
  const el=document.getElementById(cid);if(!el||!effData||effData.length<2)return null;
  const players=effData[0].slice(1),rows=effData.slice(1);
  const v=players.map((_,pi)=>{const sc=rows.map(r=>r[pi+1]),m=sc.reduce((a,b)=>a+b,0)/sc.length;return parseFloat(Math.sqrt(sc.reduce((a,b)=>a+(b-m)**2,0)/sc.length).toFixed(2));});
  const xAxis={...SX,ticks:{...SX.ticks,display:showNames}};
  return new Chart(el.getContext('2d'),{type:'bar',
    data:{labels:players,datasets:[{label:'Std. Deviation',data:v,
      backgroundColor:players.map((_,i)=>PALETTE[i%PALETTE.length]+'bb'),
      borderColor:players.map((_,i)=>PALETTE[i%PALETTE.length]),borderWidth:1,borderRadius:4}]},
    options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:showLegend},
      tooltip:{...TIP,callbacks:{label:c=>` ${c.label}: ${c.parsed.y}`}}},
      scales:{x:xAxis,y:SY()},animation:{duration:700,easing:'easeInOutQuart'}}});
}
buildVarianceOn('varianceChart');

// â”€â”€ modal expand â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
let modalChart=null;
const MODAL_LABELS={
  mainChart:()=>currentMode==='score'?'Cumulative Score':'Rank Over Time',
  weeklyChart:()=>'Points Per Week',
  analyticsChart:()=>analyticsMode==='wr'?'Win Rate Per Week':'Efficiency Per Week',
  varianceChart:()=>'Efficiency Std. Deviation',
};
function openModal(chartId){
  document.getElementById('chart-modal').style.display='flex';
  document.getElementById('modal-title').textContent=(MODAL_LABELS[chartId]||(() =>''))();
  if(modalChart){modalChart.destroy();modalChart=null;}
  if(chartId==='mainChart')      modalChart=buildMainOn('modal-canvas',currentMode,true);
  else if(chartId==='weeklyChart')    modalChart=buildWeeklyOn('modal-canvas',true);
  else if(chartId==='analyticsChart') modalChart=buildAnalyticsOn('modal-canvas',analyticsMode,true);
  else if(chartId==='varianceChart')  modalChart=buildVarianceOn('modal-canvas',true,true);
}
function closeModal(){
  document.getElementById('chart-modal').style.display='none';
  if(modalChart){modalChart.destroy();modalChart=null;}
}
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeModal();});

// Countdown
(function(){
  const h=document.getElementById('cd-h'),m=document.getElementById('cd-m'),s=document.getElementById('cd-s');
  if(!h) return;
  const daysWrap=document.getElementById('cd-days-wrap');
  const hmsWrap=document.getElementById('cd-hms-wrap');
  const daysText=document.getElementById('cd-days-text');
  const nextGameTs = 0;
  function getTarget(){
    if(nextGameTs) return new Date(nextGameTs);
    const now=new Date();
    const etOff=-5*60;
    const etNow=new Date(now.getTime()+now.getTimezoneOffset()*60000+etOff*60000);
    const d=etNow.getDay(),days=d===0?0:7-d;
    const t=new Date(etNow); t.setDate(etNow.getDate()+days); t.setHours(13,0,0,0);
    if(etNow>=t) t.setDate(t.getDate()+7);
    return new Date(t.getTime()-etOff*60000);
  }
  function pad(n){return String(n).padStart(2,'0');}
  function tick(){
    const diff=getTarget()-new Date();
    if(diff<=0){h.textContent=m.textContent=s.textContent='00';return;}
    h.textContent=pad(Math.floor(diff/3600000));
    m.textContent=pad(Math.floor(diff%3600000/60000));
    s.textContent=pad(Math.floor(diff%60000/1000));
  }
  tick(); setInterval(tick,1000);
})();

// Inline pick autosave
const SAVE_URL = '';
const CSRF = '0';
let picksMade = 0;
const totalGames = 0;

function updatePickStatus() {
  const el = document.getElementById('pick-status');
  if (!el) return;
  if (picksMade >= totalGames && totalGames > 0) {
    el.innerHTML = '<span style="width:7px;height:7px;background:#34d399;border-radius:50%;flex-shrink:0;"></span><span style="font-size:.75rem;font-weight:600;color:#34d399;">All '+totalGames+' picks submitted</span>';
  } else if (picksMade > 0) {
    el.innerHTML = '<span style="width:7px;height:7px;background:#fbbf24;border-radius:50%;flex-shrink:0;" class="pulse"></span><span style="font-size:.75rem;font-weight:600;color:#fbbf24;">'+picksMade+' / '+totalGames+' picks made</span>';
  } else {
    el.innerHTML = '<span style="width:7px;height:7px;background:#f87171;border-radius:50%;flex-shrink:0;" class="pulse"></span><span style="font-size:.75rem;font-weight:600;color:#f87171;">No picks yet</span>';
  }
}

function teamColor(abbr) {
  let h = 0;
  for (let i = 0; i < abbr.length; i++) h = (h * 31 + abbr.charCodeAt(i)) & 0xFFFF;
  return `hsla(${h % 360},65%,62%,0.28)`;
}

function selectPick(gameId, choice, optEl) {
  const knob = document.getElementById('pknob-'+gameId);
  const hadEl = document.getElementById('pick-had-'+gameId);
  const toggle = document.getElementById('ptoggle-'+gameId);
  const noPickYet = hadEl && hadEl.textContent === '0';
  const isLeft = choice === 'team1';

  if (knob) {
    // Skip slide animation when clicking the already-default-left favorite with no pick yet
    if (noPickYet && isLeft) {
      knob.style.transition = 'none';
    } else {
      knob.style.transition = 'left .32s cubic-bezier(.25,0,.2,1),background-color .22s ease';
    }
    knob.style.display = '';
    knob.style.left = isLeft ? '2px' : '50%';
    const abbr = isLeft ? toggle.dataset.team1 : toggle.dataset.team2;
    if (abbr) knob.style.backgroundColor = teamColor(abbr);
  }
  // Update label colors
  document.querySelectorAll('#ptoggle-'+gameId+' .pick-opt').forEach(el => el.removeAttribute('data-selected'));
  if (optEl) optEl.setAttribute('data-selected','true');
  // Autosave
  const fd = new FormData();
  fd.append('game_id', gameId); fd.append('choice', choice); fd.append('csrfmiddlewaretoken', CSRF);
  fetch(SAVE_URL, {method:'POST', body:fd})
    .then(r=>r.json())
    .then(data=>{
      if (data.ok && hadEl && hadEl.textContent==='0') { hadEl.textContent='1'; picksMade++; updatePickStatus(); movePickedToBottom(gameId); }
    })
    .catch(()=>{ });
}

// Drag-to-slide on pick toggles (real-time knob tracking)
document.querySelectorAll('.pick-toggle').forEach(toggle => {
  let active = false;
  function pt(e) { return e.touches ? e.touches[0] : e; }

  function onStart(e) {
    active = true;
    const knob = toggle.querySelector('.pick-knob');
    if (knob) { knob.style.transition = 'none'; knob.style.display = ''; }
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onEnd);
    document.addEventListener('touchmove', onMove, { passive: false });
    document.addEventListener('touchend', onEnd);
  }

  function onMove(e) {
    if (!active) return;
    if (e.cancelable) e.preventDefault();
    const knob = toggle.querySelector('.pick-knob');
    if (!knob) return;
    const rect = toggle.getBoundingClientRect();
    const halfW = (rect.width - 6) / 2;
    const raw = pt(e).clientX - rect.left - halfW / 2;
    knob.style.left = Math.max(2, Math.min(raw, rect.width / 2)) + 'px';
  }

  function onEnd(e) {
    if (!active) return;
    active = false;
    document.removeEventListener('mousemove', onMove);
    document.removeEventListener('mouseup', onEnd);
    document.removeEventListener('touchmove', onMove);
    document.removeEventListener('touchend', onEnd);
    const rect = toggle.getBoundingClientRect();
    const ex = (e.changedTouches ? e.changedTouches[0] : e).clientX;
    const choice = ex >= rect.left + rect.width / 2 ? 'team2' : 'team1';
    const gid = parseInt(toggle.dataset.game);
    const opt = toggle.querySelector('[data-choice="' + choice + '"]');
    selectPick(gid, choice, opt);
  }

  toggle.addEventListener('mousedown', onStart);
  toggle.addEventListener('touchstart', onStart, { passive: true });
});

function movePickedToBottom(gameId) {
  const toggle = document.getElementById('ptoggle-' + gameId);
  if (!toggle) return;
  const row = toggle.closest('.game-row');
  if (!row) return;
  const container = row.parentElement;
  if (!container) return;

  // FLIP: snapshot every row's position before the DOM change
  const children = Array.from(container.children);
  const before = children.map(c => c.getBoundingClientRect().top);

  // Move picked row to end of container
  container.appendChild(row);

  // Animate each row from where it was to where it is now
  Array.from(container.children).forEach((child, newIdx) => {
    const oldIdx = children.indexOf(child);
    const dy = (oldIdx === -1 ? before[before.length - 1] : before[oldIdx]) - child.getBoundingClientRect().top;
    if (Math.abs(dy) < 1) return;
    child.style.transition = 'none';
    child.style.transform = `translateY(${dy}px)`;
    requestAnimationFrame(() => requestAnimationFrame(() => {
      child.style.transition = 'transform .4s cubic-bezier(.25,0,.2,1)';
      child.style.transform = '';
    }));
  });
}

// Restore pick knob colors on page load
document.querySelectorAll('.pick-toggle').forEach(toggle => {
  const knob = toggle.querySelector('.pick-knob');
  if (!knob || knob.style.display === 'none') return;
  const isTeam2 = knob.style.left === '50%';
  const abbr = isTeam2 ? toggle.dataset.team2 : toggle.dataset.team1;
  if (abbr) { knob.style.transition = 'none'; knob.style.backgroundColor = teamColor(abbr); }
});

// Mobile tab toggle
function mobTab(tab){
  const isChart=tab==='chart';
  document.querySelector('.col-center').classList.toggle('mob-on',isChart);
  document.querySelector('.col-right').classList.toggle('mob-on',!isChart);
  const on='background:var(--accent);color:#fff;',off='background:transparent;color:var(--muted);';
  document.getElementById('mob-tab-chart').style.cssText+=isChart?on:off;
  document.getElementById('mob-tab-picks').style.cssText+=isChart?off:on;
}

// Email nav + collapse
(function(){
  const msgs=Array.from(document.querySelectorAll('.email-msg'));
  if(!msgs.length)return;
  const total=msgs.length;
  const dateEl=document.getElementById('email-date');
  const counter=document.getElementById('email-counter');
  const prevBtn=document.getElementById('email-prev');
  const nextBtn=document.getElementById('email-next');
  const colBtn=document.getElementById('email-collapse');
  const body=document.getElementById('email-body');
  const fade=document.getElementById('email-fade');
  const dates=[];
  dates.push("0");
  let idx=0,expanded=false;
  function showMsg(i){
    msgs.forEach((m,j)=>m.style.display=j===i?'':'none');
    if(dateEl)dateEl.textContent=dates[i]||'';
    if(counter)counter.textContent=(i+1)+' / '+total;
    if(prevBtn){prevBtn.disabled=i===total-1;prevBtn.style.opacity=i===total-1?'.3':'1';}
    if(nextBtn){nextBtn.disabled=i===0;nextBtn.style.opacity=i===0?'.3':'1';}
    if(body&&expanded)body.style.maxHeight=body.scrollHeight+'px';
  }
  if(prevBtn)prevBtn.addEventListener('click',()=>{if(idx<total-1){idx++;showMsg(idx);}});
  if(nextBtn)nextBtn.addEventListener('click',()=>{if(idx>0){idx--;showMsg(idx);}});
  function toggle(){
    expanded=!expanded;
    body.style.maxHeight=expanded?body.scrollHeight+'px':'5.6em';
    if(fade)fade.style.opacity=expanded?'0':'1';
    if(colBtn)colBtn.textContent=expanded?'collapse':'expand';
    body.style.cursor='pointer';
  }
  if(body){
    body.style.cursor='pointer';
    body.addEventListener('click',()=>{ toggle(); });
  }
  if(colBtn)colBtn.addEventListener('click',(e)=>{ e.stopPropagation(); toggle(); });
})();

// Week slider
(function(){
  const slider=document.getElementById('week-slider');
  if(!slider)return;
  const urls={};
  urls[0]="";
  const label=document.getElementById('wk-label');
  const accentColor=getComputedStyle(document.documentElement).getPropertyValue('--accent').trim()||'#00897b';
  function updateFill(){
    const min=+slider.min,max=+slider.max,val=+slider.value;
    const pct=max>min?((val-min)/(max-min))*100:0;
    const fill=`color-mix(in srgb,${accentColor} 55%,transparent)`;
    slider.style.background=`linear-gradient(to right,${fill} 0%,${fill} ${pct}%,rgba(255,255,255,.07) ${pct}%,rgba(255,255,255,.07) 100%)`;
  }
  updateFill();
  slider.addEventListener('input',()=>{ label.textContent='W'+slider.value; updateFill(); });
  slider.addEventListener('change',()=>{ const u=urls[+slider.value];if(u)window.location.href=u; });
})();

