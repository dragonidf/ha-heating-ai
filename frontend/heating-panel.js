/* Heating AI — native Home Assistant panel/card. No tokens or external services. */
const VERSION = '0.2.9';
export const escapeHTML = value => String(value ?? '—').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export const numeric = value => value !== null && value !== undefined && String(value).trim() !== '' && Number.isFinite(Number(value)) ? Number(value) : null;
export const isTrue = value => value === true || value === 'true' || value === 'on';
export const mergeSegments = rows => (Array.isArray(rows) ? rows : []).filter(r => numeric(r.start) !== null && numeric(r.end) !== null && r.end > r.start).sort((a,b)=>a.start-b.start).reduce((out,row) => {
  const last = out.at(-1);
  if (last && row.start <= last.end + 1 && isTrue(row.conditional) === isTrue(last.conditional)) last.end = Math.max(last.end,row.end);
  else out.push({...row});
  return out;
}, []);
const icon = name => `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">${{
  home:'<path d="m3 10 9-7 9 7v10H3Z"/><path d="M9 20v-7h6v7"/>',
  menu:'<path d="M4 6h16M4 12h16M4 18h16"/>',
  refresh:'<path d="M20 7v5h-5"/><path d="M19 12a7 7 0 1 1-2-5l3 3"/>'
}[name]||''}</svg>`;
const h = escapeHTML;
const fmt = (v,d=1) => numeric(v) === null ? '—' : Number(v).toLocaleString('uk-UA',{maximumFractionDigits:d,minimumFractionDigits:d});
export const chartLayout = width => {
  const w=Math.max(1,Math.round(numeric(width)??880)), height=w<520?350:370;
  return {width:w,height,left:46,right:12,top:24,bottom:height-124,tickIntervals:w<520?2:4};
};
// Recorder uses compact {s, lu} records over WebSocket; REST uses ISO timestamps.
export const normalizeHistory = rows => [...new Map((Array.isArray(rows)?rows:[]).map(row=>{
  const stamp=row.ts??row.lu??row.lc??row.last_updated??row.last_changed;
  const ts=typeof stamp==='number'?stamp:Date.parse(stamp)/1000;
  return [ts,{ts,state:String(row.s??row.state??'unavailable')}];
}).filter(([ts])=>Number.isFinite(ts))).values()].sort((a,b)=>a.ts-b.ts);
const activityState = value => ['on','3 кВт','6 кВт','9 кВт'].includes(value)?'on':value==='off'?'off':'unknown';
export const activityIntervals = (primary,fallback,start,end) => {
  const a=normalizeHistory(primary),b=normalizeHistory(fallback),out=[];
  const times=[...new Set([start,end,...a.map(r=>r.ts),...b.map(r=>r.ts)].filter(t=>t>=start&&t<=end))].sort((x,y)=>x-y);
  let ai=-1,bi=-1;
  for(let i=0;i<times.length-1;i++) {
    const t=times[i];while(ai+1<a.length&&a[ai+1].ts<=t)ai++;while(bi+1<b.length&&b[bi+1].ts<=t)bi++;
    const known=activityState(a[ai]?.state),state=known!=='unknown'?known:activityState(b[bi]?.state);
    const last=out.at(-1);if(last?.state===state)last.end=times[i+1];else out.push({start:t,end:times[i+1],state});
  }
  return out;
};
const windowHistory = (rows,start,end) => {
  const before=rows.filter(r=>r.ts<=start).at(-1),out=rows.filter(r=>r.ts>start&&r.ts<=end);
  if(before)out.unshift({...before,ts:start});
  if(out.length&&out.at(-1).ts<end)out.push({...out.at(-1),ts:end});
  return out;
};
export const historyPaths = (rows,start,end,x,y) => {
  const paths=[];let points=[];
  for(const row of windowHistory(rows,start,end)) {
    const value=numeric(row.state);
    if(value===null){if(points.length){const previous=points.at(-1).split(',').at(-1);points.push(`L${x(row.ts)},${previous}`);paths.push(points.join(' '));}points=[];continue;}
    points.push(`${points.length?'L':'M'}${x(row.ts)},${y(value)}`);
  }
  if(points.length)paths.push(points.join(' '));return paths;
};
const historySeries=[['room','Кімната','var(--history-room)'],['return','Обратка','var(--history-return)'],['outside','Надворі','var(--history-outside)']];
// Keep native chart elements and their ancestors attached while values change.
// HA chart subscriptions and ResizeObservers depend on a stable DOM lifecycle.
const patchDOM = (current,next) => {
  if(current.nodeType!==next.nodeType || current.nodeName!==next.nodeName) {
    current.replaceWith(next.cloneNode(true)); return;
  }
  if(current.nodeType===Node.TEXT_NODE || current.nodeType===Node.COMMENT_NODE) {
    if(current.data!==next.data)current.data=next.data;
    return;
  }
  const editing=current.nodeType===Node.ELEMENT_NODE && current.matches('input,select') && current.getRootNode().activeElement===current;
  if(current.nodeType===Node.ELEMENT_NODE) {
    const keep=name=>editing&&['value','checked','selected'].includes(name) || (current instanceof HTMLDetailsElement||current instanceof HTMLDialogElement)&&name==='open';
    for(const attr of [...current.attributes])if(!keep(attr.name)&&!next.hasAttribute(attr.name))current.removeAttribute(attr.name);
    for(const attr of next.attributes)if(!keep(attr.name)&&current.getAttribute(attr.name)!==attr.value)current.setAttribute(attr.name,attr.value);
    if(current.hasAttribute('data-native'))return;
    if(editing&&current instanceof HTMLSelectElement)return;
  }
  for(let i=0;i<next.childNodes.length;i++) {
    if(current.childNodes[i])patchDOM(current.childNodes[i],next.childNodes[i]);
    else current.append(next.childNodes[i].cloneNode(true));
  }
  while(current.childNodes.length>next.childNodes.length)current.lastChild.remove();
  if(current instanceof HTMLInputElement&&!editing) {
    if(current.value!==next.value)current.value=next.value;
    current.checked=next.checked;
  } else if(current instanceof HTMLSelectElement&&!editing)current.value=next.value;
};
const defaults = {
  controller:'sensor.heating_controller', alive:'binary_sensor.heating_controller_alive', permitted:'binary_sensor.heating_actuation_permitted',
  heartbeat:'sensor.heating_heartbeat', temperature:'sensor.heating_temperature', power_confirmed:'sensor.heating_power_confirmed', boiler_active:'binary_sensor.heating_boiler_active',boiler_stage:'sensor.heating_boiler_stage',
  grid_status:'sensor.heating_grid', fireplace_status:'sensor.heating_fireplace', fireplace_active:'binary_sensor.heating_fireplace_active',
  energy_24h:'sensor.heating_energy_24h', energy_monthly:'sensor.heating_energy_monthly', forecast:'sensor.heating_forecast',
  outages:'sensor.heating_outage_schedule', model:'sensor.heating_model', environment:'sensor.heating_environment', summary:'sensor.heating_ai_summary',
  restore_status:'sensor.heating_restore_status', fan_status:'sensor.heating_fan',
  system_settings:'sensor.heating_system_settings',
  target:'input_number.heating_target',lower:'input_number.heating_lower',upper:'input_number.heating_upper',
  night_target:'input_number.heating_night_target',night_lower:'input_number.heating_night_lower',night_upper:'input_number.heating_night_upper',
  sleep_start:'input_datetime.heating_sleep_start',sleep_end:'input_datetime.heating_sleep_end',start:'input_datetime.heating_start',end:'input_datetime.heating_end',
  monthly_budget:'input_number.heating_monthly_budget',session_limit:'input_number.heating_session_limit',native_setpoint:'input_number.heating_native_setpoint',
  mode:'input_select.heating_mode',strategy:'input_select.heating_strategy',armed:'input_boolean.heating_armed',restore_once:'input_boolean.heating_restore_once',
  fan_control:'input_boolean.heating_fan_control',fan_mode:'input_select.heating_fan_mode',fan_speed:'input_number.heating_fan_speed',fan_intense_speed:'input_number.heating_fan_intense_speed',fan_night_limit:'input_number.heating_fan_night_limit',
  ai_auto_apply:'input_boolean.heating_ai_auto_apply',rollback:'input_button.heating_model_rollback',weather_refresh:'input_button.heating_forecast_refresh'
};
export class HeatingAIPanel extends HTMLElement {
  constructor() {
    super(); this.attachShadow({mode:'open'}); this.tab='overview'; this.config={}; this.nativeCards=new Map(); this.nativeConfigs=new Map(); this.pending=new Set();
    this.historyRange=24;this.historyVisible=new Set(['room','return']);this.historyEpoch=0;
    this.systemDraft={};this.systemHelp=null;this.systemPending=null;
    this.shadowRoot.addEventListener('click',e=>this.clicked(e));
    this.shadowRoot.addEventListener('change',e=>this.changed(e));
    this.shadowRoot.addEventListener('focusout',()=>setTimeout(()=>this.render(),0));
    this.shadowRoot.addEventListener('cancel',e=>{if(e.target.matches('.system-dialog')){e.preventDefault();this.closeSystemHelp();}},true);
    for(const event of ['pointerdown','pointermove','pointerleave','keydown'])this.shadowRoot.addEventListener(event,e=>this.inspectHistory(e),event==='pointerleave');
  }
  setConfig(config) { this.config=config || {}; this.nativeCards.clear(); this.nativeConfigs.clear(); this.historyEpoch++;this.historyData=null;this.historyPending=null;this.historyKey='';this.render(); }
  set panel(panel) { this.setConfig(panel?.config || {}); }
  set hass(value) {
    const previous=this._hass;this._hass=value;
    this.syncSystemSettings();
    this.toggleAttribute('data-light',value.themes?.darkMode===false);
    for(const card of this.nativeCards.values()) card.hass=value;
    const ids=new Set([...Object.values(defaults),...Object.values(this.config.entities||{}),...Object.values(value.states['sensor.heating_ui']?.attributes?.entities||{}),'sensor.heating_ui']);
    if(!previous||[...ids].some(id=>previous.states[id]!==value.states[id])||previous.themes!==value.themes)this.render();
  }
  get hass() { return this._hass; }
  connectedCallback() {
    clearInterval(this.timer);
    this.resizeObserver?.disconnect();
    this.observedWidths=new WeakMap();
    this.resizeObserver=new ResizeObserver(entries=>{
      let changed=false;
      for(const entry of entries) {
        const width=Math.round(entry.contentRect.width);
        if(this.observedWidths.get(entry.target)!==width){this.observedWidths.set(entry.target,width);changed=true;}
      }
      if(changed&&!this.resizeFrame)this.resizeFrame=requestAnimationFrame(()=>{this.resizeFrame=0;if(this.isConnected)this.render();});
    });
    this.resizeObserver.observe(this);
    this.timer=setInterval(()=>this.render(),30000); this.render();
  }
  disconnectedCallback() {
    clearInterval(this.timer);this.resizeObserver?.disconnect();
    clearTimeout(this.systemTimeout);
    cancelAnimationFrame(this.resizeFrame);this.resizeFrame=0;this.observedPlots=new Set();
    cancelAnimationFrame(this.historyFrame);this.historyFrame=0;this.historyEpoch++;
    if(this.historyPending)this.historyAttemptAt=0;
    this.historyPending=null;
  }
  observePlot() {
    const plots=new Set(this.shadowRoot.querySelectorAll('.forecast-plot,.history-plot'));
    for(const plot of this.observedPlots||[])if(!plots.has(plot))this.resizeObserver?.unobserve(plot);
    for(const plot of plots)if(!this.observedPlots?.has(plot))this.resizeObserver?.observe(plot);
    this.observedPlots=plots;
  }
  getCardSize() { return 12; }
  static getStubConfig() { return {}; }
  id(key) { return this.config.entities?.[key] || this.hass?.states['sensor.heating_ui']?.attributes?.entities?.[key] || defaults[key]; }
  entity(key) { return this.hass?.states[this.id(key)]; }
  state(key) { return this.entity(key)?.state ?? 'unavailable'; }
  attrs(key) { return this.entity(key)?.attributes || {}; }
  time(ts,date=false) {
    const t = typeof ts === 'number' ? ts*1000 : Date.parse(ts);
    return Number.isFinite(t) ? new Intl.DateTimeFormat('uk-UA',{timeZone:this.hass?.config?.time_zone || 'Europe/Kyiv',hour:'2-digit',minute:'2-digit',...(date?{day:'2-digit',month:'2-digit'}:{})}).format(t) : '—';
  }
  section(title,body,cls='',id='') { return `<section class="card ${cls}" ${id?`id="${h(id)}"`:''}><h2>${h(title)}</h2>${body}</section>`; }
  metric(title,value,unit,detail='',key='') {
    return `<button class="metric card" ${key?`data-info="${h(this.id(key))}"`:''}><span class="eyebrow">${h(title)}</span><strong>${h(value)} <small>${h(unit)}</small></strong><span class="muted">${h(detail)}</span></button>`;
  }
  row(label,value) { return `<div class="row"><span class="muted">${h(label)}</span><strong>${h(value)}</strong></div>`; }
  field(key,label) {
    const entity=this.entity(key), id=this.id(key), state=entity?.state, a=entity?.attributes||{}, domain=id?.split('.')[0];
    const disabled=!entity || ['unknown','unavailable'].includes(state) || this.pending.has(id);
    let control=`<span class="muted">Недоступно</span>`;
    const common=`data-control="${h(key)}" aria-label="${h(label)}" ${disabled?'disabled':''}`;
    if(domain==='input_number') control=`<div class="number"><input type="number" ${common} value="${numeric(state)===null?'':h(state)}" min="${h(a.min ?? '')}" max="${h(a.max ?? '')}" step="${h(a.step ?? 'any')}"><span>${h(a.unit_of_measurement || '')}</span></div>`;
    if(domain==='input_select') control=`<select ${common}>${(a.options||[]).map(v=>`<option ${v===state?'selected':''} value="${h(v)}">${h(v)}</option>`).join('')}</select>`;
    if(domain==='input_boolean') control=`<label class="toggle"><input type="checkbox" role="switch" ${common} ${state==='on'?'checked':''}><span></span></label>`;
    if(domain==='input_datetime') control=a.has_date ? `<button data-info="${h(id)}">${h(state)}</button>` : `<input type="time" ${common} step="1" value="${h(state || '')}">`;
    if(domain==='input_button') control=`<button class="action" data-press="${h(key)}" ${disabled?'disabled':''}>${key==='rollback'?'Відновити модель ↶':'Оновити погоду ↻'}</button>`;
    return `<div class="field"><span>${h(label)}${disabled?'<small class="muted">Недоступно або очікуємо підтвердження</small>':''}</span>${control}</div>`;
  }
  async loadHistory(force=false) {
    if(!this.isConnected||this.tab!=='overview'||!this.hass?.callWS)return;
    const keys=['room','return','outside','boiler_active','boiler_stage','fireplace_active'];
    const entities=Object.fromEntries(keys.map(key=>[key,this.id(key)]));
    const ids=[...new Set(Object.values(entities).filter(id=>typeof id==='string'))],signature=JSON.stringify(ids);
    if(this.historyPending?.signature===signature)return;
    if(!force&&this.historyKey===signature&&Date.now()-this.historyAttemptAt<60000)return;
    if(this.historyKey!==signature){this.historyData=null;this.historyCursor=null;}
    const epoch=++this.historyEpoch,end=Date.now()/1000,start=end-86400;
    this.historyKey=signature;this.historyAttemptAt=Date.now();this.historyPending={signature,epoch};this.historyError='';
    this.render();let timeout;
    try {
      const response=await Promise.race([this.hass.callWS({type:'history/history_during_period',start_time:new Date(start*1000).toISOString(),end_time:new Date(end*1000).toISOString(),entity_ids:ids,minimal_response:true,no_attributes:true,significant_changes_only:true}),new Promise((_,reject)=>{timeout=setTimeout(()=>reject(new Error('timeout')),20000);})]);
      if(epoch!==this.historyEpoch)return;
      this.historyData={start,end,rows:Object.fromEntries(keys.map(key=>[key,normalizeHistory(response?.[entities[key]])]))};
    } catch {
      if(epoch===this.historyEpoch)this.historyError='Не вдалося завантажити історію. Перевірте з’єднання та повторіть.';
    } finally {
      clearTimeout(timeout);
      if(epoch===this.historyEpoch){this.historyPending=null;if(this.isConnected)this.render();}
    }
  }
  historyGeometry() {
    const width=Math.max(1,Math.round(this.shadowRoot.querySelector('.history-plot')?.clientWidth||Math.min(this.clientWidth-80,880)));
    return {width,height:330,left:46,right:12,top:16,bottom:202,ticks:width<520?2:4};
  }
  historyChart() {
    const header=`<div class="history-tools"><div class="history-ranges" role="group" aria-label="Період історії">${[6,12,24].map(hours=>`<button data-history-range="${hours}" aria-pressed="${this.historyRange===hours}">${hours} год</button>`).join('')}</div><button class="history-refresh" data-history-refresh aria-label="Оновити історію" ${this.historyPending?'disabled':''}>${icon('refresh')}</button></div>`;
    if(!this.historyData)return header+`<div class="history-placeholder" role="status">${h(this.historyError||'Завантажуємо історію…')}</div>`;
    const data=this.historyData,start=data.end-this.historyRange*3600,end=data.end,g=this.historyGeometry(),right=g.width-g.right;
    const shown=historySeries.filter(([key])=>this.historyVisible.has(key));
    const values=shown.flatMap(([key])=>windowHistory(data.rows[key],start,end).map(r=>numeric(r.state)).filter(v=>v!==null));
    const low=values.length?Math.floor(Math.min(...values)-.5):20,high=values.length?Math.ceil(Math.max(...values)+.5):24;
    const x=ts=>g.left+(Math.max(start,Math.min(end,ts))-start)/(end-start)*Math.max(1,right-g.left);
    const y=value=>g.bottom-(value-low)/(high-low)*(g.bottom-g.top);
    const boiler=activityIntervals(data.rows.boiler_active,data.rows.boiler_stage,start,end);
    const fire=activityIntervals(data.rows.fireplace_active,[],start,end);
    const cursor=this.historyCursor===null||this.historyCursor===undefined?null:Math.max(start,Math.min(end,this.historyCursor));
    const at=(key)=>cursor===null?numeric(this.state(key)):numeric(data.rows[key].filter(r=>r.ts<=cursor).at(-1)?.state);
    const legend=`<div class="history-legend" role="group" aria-label="Температурні показники">${historySeries.map(([key,label,color])=>`<button data-history-series="${key}" aria-pressed="${this.historyVisible.has(key)}" style="--series-color:${color}"><span><i></i>${label}</span><strong>${fmt(at(key))}<small> °C</small></strong></button>`).join('')}</div>`;
    let svg=`<div class="history-plot" tabindex="0" role="slider" aria-label="Час на графіку температури, котла й каміна" aria-valuemin="${start}" aria-valuemax="${end}" aria-valuenow="${cursor??end}" aria-valuetext="${h(this.time(cursor??end,true))}"><svg width="${g.width}" height="${g.height}" viewBox="0 0 ${g.width} ${g.height}" role="img" aria-label="Історія температур і фактична робота котла та каміна на спільній часовій шкалі"><defs><pattern id="history-missing" width="6" height="6" patternUnits="userSpaceOnUse"><path d="M-1 1L1 -1M0 6L6 0M5 7L7 5" stroke="currentColor" opacity=".22"/></pattern></defs>`;
    for(let i=0;i<4;i++) {
      const value=low+(high-low)*i/3;
      svg+=`<path d="M${g.left} ${y(value)}H${right}" stroke="currentColor" opacity=".1"/><text x="0" y="${y(value)+4}" fill="currentColor" font-size="12">${fmt(value)}°</text>`;
    }
    for(const [key,label,color] of shown)for(const path of historyPaths(data.rows[key],start,end,x,y))svg+=`<path data-history-line="${key}" d="${path}" fill="none" stroke="${color}" stroke-width="2.4" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"><title>${h(label)}</title></path>`;
    for(let i=0;i<=g.ticks;i++)svg+=`<text x="${x(start+i*(end-start)/g.ticks)}" y="225" fill="currentColor" text-anchor="${i===0?'start':i===g.ticks?'end':'middle'}" font-size="12">${h(this.time(start+i*(end-start)/g.ticks))}</text>`;
    for(const [key,label,segments,top,color] of [['boiler','Котел',boiler,248,'var(--source-red)'],['fireplace','Камін',fire,286,'var(--history-fire)']]) {
      svg+=`<text x="0" y="${top+13}" fill="currentColor" font-size="12">${label}</text><rect x="${g.left}" y="${top}" width="${Math.max(1,right-g.left)}" height="18" rx="5" fill="currentColor" opacity=".06"/>`;
      for(const segment of segments)if(segment.state!=='off')svg+=`<rect data-history-lane="${key}" data-state="${segment.state}" x="${x(segment.start)}" y="${top}" width="${Math.max(1,x(segment.end)-x(segment.start))}" height="18" rx="4" fill="${segment.state==='on'?color:'url(#history-missing)'}"><title>${label} · ${h(this.time(segment.start))}–${h(this.time(segment.end))} · ${segment.state==='on'?'працював':'немає даних'}</title></rect>`;
    }
    if(cursor!==null)svg+=`<path class="history-crosshair" d="M${x(cursor)} ${g.top}V306" stroke="currentColor" stroke-dasharray="3 4" opacity=".55"/>`;
    svg+='</svg></div>';
    const duration=segments=>{const minutes=Math.round(segments.filter(s=>s.state==='on').reduce((sum,s)=>sum+s.end-s.start,0)/60);return minutes>=60?`${Math.floor(minutes/60)} год ${minutes%60} хв`:`${minutes} хв`;};
    const total=segments=>segments.every(s=>s.state==='unknown')?'немає даних':segments.some(s=>s.state==='on')?(segments.some(s=>s.state==='unknown')?'≥ ':'')+duration(segments):segments.some(s=>s.state==='unknown')?'неповні дані':'0 хв';
    const missing=[...boiler,...fire].some(s=>s.state==='unknown');
    const status=(segments,on,off)=>{const state=segments.find(s=>s.start<=cursor&&cursor<s.end)?.state??segments.at(-1)?.state;return state==='on'?on:state==='off'?off:'немає даних';};
    const readout=cursor===null?'Торкніться графіка, щоб переглянути значення.':`${this.time(cursor,true)} · Котел: ${status(boiler,'працює','вимкнено')} · Камін: ${status(fire,'є тепло','немає тепла')}`;
    return header+legend+svg+`<div class="history-totals"><span><i style="background:var(--source-red)"></i>Котел <strong>${total(boiler)}</strong></span><span><i style="background:var(--history-fire)"></i>Камін <strong>${total(fire)}</strong></span></div><p class="history-readout muted" aria-live="polite">${h(readout)}</p>${missing?'<p class="muted note">Штрихування — немає історичних даних.</p>':''}${!values.length?'<p class="muted note">Температурні вимірювання за цей період відсутні.</p>':''}${this.historyError?`<p class="warn note" role="status">${h(this.historyError)} Показано попередні дані.</p>`:''}`;
  }
  inspectHistory(event) {
    const plot=event.target.closest?.('.history-plot');if(!plot||!this.historyData)return;
    if(event.type==='pointerleave'&&event.target!==plot)return;
    const end=this.historyData.end,start=end-this.historyRange*3600;
    if(event.type==='keydown') {
      const key=event.key;if(!['ArrowLeft','ArrowRight','Home','End'].includes(key))return;
      event.preventDefault();this.historyCursor=key==='Home'?start:key==='End'?end:Math.max(start,Math.min(end,(this.historyCursor??end)+(key==='ArrowLeft'?-300:300)));
    } else if(event.type==='pointerleave') {
      if(event.pointerType!=='mouse')return;this.historyCursor=null;
    } else {
      const svg=plot.querySelector('svg'),bounds=svg.getBoundingClientRect(),g=this.historyGeometry();
      const fraction=Math.max(0,Math.min(1,((event.clientX-bounds.left)*g.width/bounds.width-g.left)/Math.max(1,g.width-g.right-g.left)));
      this.historyCursor=start+fraction*(end-start);
    }
    if(!this.historyFrame)this.historyFrame=requestAnimationFrame(()=>{this.historyFrame=0;if(this.isConnected)this.render();});
  }
  forecastChart() {
    const a=this.attrs('forecast'), rows=(Array.isArray(a.curve)?a.curve:[]).filter(r=>numeric(r.ts)!==null&&numeric(r.temperature)!==null);
    const start=Date.parse(a.created_at)/1000,end=start+86400;
    if(!rows.length||!Number.isFinite(start)) return '<p class="empty">Прогноз з’явиться після наступного розрахунку контролера.</p>';
    const low=Math.min(...rows.map(r=>Math.min(Number(r.temperature),numeric(r.lower)??Number(r.temperature))))-.4;
    const high=Math.max(...rows.map(r=>Math.max(Number(r.temperature),numeric(r.upper)??Number(r.temperature))))+.4;
    const measured=this.shadowRoot.querySelector('.forecast-plot')?.clientWidth;
    const g=chartLayout(measured||Math.min(Math.max(1,this.clientWidth-80),880));
    const right=g.width-g.right;
    const x=t=>g.left+Math.max(0,Math.min(1,(t-start)/86400))*Math.max(1,right-g.left);
    const y=t=>g.bottom-(t-low)/(high-low)*(g.bottom-g.top);
    let svg=`<div class="forecast-plot"><svg width="${g.width}" height="${g.height}" viewBox="0 0 ${g.width} ${g.height}" role="img" aria-label="Прогноз температури, комфортні межі, нагрів, залишкове тепло каміна і відключення на 24 години"><defs><linearGradient id="curve-fill" x1="0" y1="0" x2="0" y2="1"><stop stop-color="var(--history-room)" stop-opacity=".2"/><stop offset="1" stop-color="var(--history-room)" stop-opacity="0"/></linearGradient></defs>`;
    let prev=start;
    for(const r of rows) {
      if(numeric(r.lower)!==null&&numeric(r.upper)!==null) svg+=`<rect x="${x(prev)}" y="${y(r.upper)}" width="${Math.max(0,x(r.ts)-x(prev))}" height="${Math.max(0,y(r.lower)-y(r.upper))}" fill="var(--history-room)" opacity=".09"/>`;
      prev=r.ts;
    }
    const known=numeric(a.schedule_known_until);
    if(known!==null&&known<end) svg+=`<rect x="${x(known)}" y="${g.top}" width="${Math.max(0,right-x(known))}" height="${g.bottom+79-g.top}" fill="var(--source-red)" opacity=".07"/>`;
    for(let i=0;i<4;i++) { const t=low+(high-low)*i/3; svg+=`<path d="M${g.left} ${y(t)}H${right}" stroke="currentColor" opacity=".08"/><text x="0" y="${y(t)+4}" fill="currentColor" font-size="12">${fmt(t)}°</text>`; }
    const path=rows.map((r,i)=>`${i?'L':'M'}${x(r.ts).toFixed(2)},${y(r.temperature).toFixed(2)}`).join(' ');
    svg+=`<path d="${path} L${x(rows.at(-1).ts)},${g.bottom+8} L${x(rows[0].ts)},${g.bottom+8} Z" fill="url(#curve-fill)"/><path d="${path}" fill="none" stroke="var(--history-room)" stroke-width="3" stroke-dasharray="5 4" vector-effect="non-scaling-stroke"/>`;
    for(const r of rows) svg+=`<circle cx="${x(r.ts)}" cy="${y(r.temperature)}" r="6" fill="transparent"><title>${h(this.time(r.ts))} · ${fmt(r.temperature)} °C</title></circle>`;
    svg+=`<text x="0" y="${g.bottom+35}" fill="currentColor" font-size="11">Котел</text><text x="0" y="${g.bottom+60}" fill="currentColor" font-size="11">Камін*</text><text x="0" y="${g.bottom+85}" fill="currentColor" font-size="11">Мережа</text>`;
    for(const s of mergeSegments(a.heat_segments)) if(s.end>start&&s.start<end) svg+=`<rect x="${x(s.start)}" y="${g.bottom+22}" width="${Math.max(1,x(s.end)-x(s.start))}" height="15" rx="4" fill="var(--source-red)" opacity="${isTrue(s.conditional)?.45:1}"><title>${h(this.time(s.start))}–${h(this.time(s.end))}${isTrue(s.conditional)?' · умовно':''}</title></rect>`;
    for(const s of mergeSegments(a.fireplace_segments)) if(s.end>start&&s.start<end) svg+=`<rect data-series="fireplace" x="${x(s.start)}" y="${g.bottom+47}" width="${Math.max(1,x(s.end)-x(s.start))}" height="15" rx="4" fill="var(--history-fire)" opacity=".6"><title>${h(this.time(s.start))}–${h(this.time(s.end))} · оцінка залишкового тепла, а не майбутнього горіння</title></rect>`;
    for(const s of mergeSegments(this.attrs('outages').outages)) if(s.end>start&&s.start<end) svg+=`<rect x="${x(s.start)}" y="${g.bottom+72}" width="${Math.max(1,x(s.end)-x(s.start))}" height="15" rx="4" fill="var(--source-red)"><title>Відключення ${h(this.time(s.start))}–${h(this.time(s.end))}</title></rect>`;
    for(let i=0;i<=g.tickIntervals;i++) svg+=`<text x="${x(start+i*86400/g.tickIntervals)}" y="${g.height-8}" text-anchor="${i===0?'start':i===g.tickIntervals?'end':'middle'}" fill="currentColor" font-size="12">${h(this.time(start+i*86400/g.tickIntervals))}</text>`;
    return svg+'</svg></div><div class="legend"><span>● Прогноз</span><span>● Котел</span><span style="color:var(--history-fire)">● Камін*</span><span style="color:var(--source-red)">● Відключення</span></div>';
  }
  compactMetric(label,value,unit='',entity='',key='') {
    const tag=entity?'button':'div';
    return `<${tag} class="compact-metric" ${entity?`data-info="${h(this.id(entity))}"`:''} ${key?`data-forecast-metric="${h(key)}"`:''}><span class="muted">${h(label)}</span><strong>${h(value)}${value!=='—'&&unit?` <small>${h(unit)}</small>`:''}</strong></${tag}>`;
  }
  boilerOverview() {
    const binary=this.state('boiler_active'),power=numeric(this.state('power_confirmed'));
    const heating=binary==='on'?true:binary==='off'?false:power===null?null:power>=1500;
    const summary=this.attrs('summary').last_24h||{};
    return `<section class="card boiler-card" data-heating="${heating??'unknown'}"><div class="card-heading"><h2>Котел</h2><span class="pill boiler-status">${heating===true?'Гріє':heating===false?'Очікування':'Немає даних'}</span></div><div class="boiler-metrics">${this.compactMetric('Потужність',fmt(power===null?null:power/1000),'кВт','power_confirmed')}${this.compactMetric('Обратка',fmt(this.state('return')),'°C','return')}${this.compactMetric('За 24 год',fmt(this.state('energy_24h'),2),'кВт·год','energy_24h')}</div><p class="boiler-note muted">У кімнаті за добу ${fmt(summary.temperature_min)}–${fmt(summary.temperature_max)} °C</p></section>`;
  }
  fireplaceOverview() {
    const phase=this.state('fireplace_status'),fire=this.attrs('fireplace_status'),fan=this.attrs('fan_status');
    const valid=['Неактивний','Розпалювання','Горить','Інтенсивно горить','Згасає'].includes(phase);
    const active=valid&&this.state('fireplace_active')==='on',running=this.state('fan')==='on';
    const waiting=isTrue(fan.requested)&&this.state('fan')==='off';
    const signed=value=>numeric(value)===null?'—':(Number(value)>0?'+':'')+fmt(value);
    const reason=fan.reason||(!['unknown','unavailable'].includes(this.state('fan_status'))?this.state('fan_status'):'Очікуємо даних про циркуляцію');
    return `<section class="card fireplace-card" data-phase="${h(phase)}"><div class="card-heading"><h2>Камін і циркуляція</h2><button class="fire-phase" data-info="${h(this.id('fireplace_status'))}" aria-label="Подробиці стану каміна"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M13 2c1 5-5 6-3 11 1-1 2-2 2-4 4 3 6 6 4 9-2 4-9 3-10-1-2-5 2-8 2-11 1 1 2 2 2 3 1-3 3-4 3-7Z"/></svg><strong>${h(valid?phase:phase==='Датчик недоступний'?phase:'Немає даних')}</strong></button></div><div class="fireplace-grid"><div class="fire-block" data-active="${active}"><span class="eyebrow">Температура каміна</span><strong class="fire-temperature">${fmt(valid?fire.temperature:null)} °C <small class="muted">${valid&&numeric(fire.delta)!==null?signed(fire.delta)+' °C до кімнати':'—'}</small></strong></div><div class="circulation-block"><span class="eyebrow">Циркуляція</span><strong class="fan-actual">${running?`Увімкнено · ${fmt(this.attrs('fan').percentage,0)} %`:this.state('fan')==='off'?'Вимкнено':'Немає даних'}</strong>${waiting?'<p class="fan-waiting">Очікує запуску</p>':''}${running&&isTrue(fan.manual_running)?'<p class="fan-level muted">Ручне увімкнення</p>':running&&isTrue(fan.requested)&&numeric(fan.selected_level)!==null?`<p class="fan-level muted">Рівень ${fmt(fan.selected_level,0)} · ${Number(fan.selected_level)===1?'Ніч':Number(fan.selected_level)===3?'Інтенсив':'Стандарт'}</p>`:''}</div></div><details class="fire-details"><summary>Подробиці та налаштування</summary><p class="fan-reason">${h(reason)}</p>${this.row('Корисне тепло',active?'Є':valid?'Немає':'Немає даних')}${this.row('Зміна різниці',signed(valid?fire.trend:null)+' °C/год')}${this.row('Режим циркуляції',this.entity('fan_mode')?this.state('fan_mode'):'—')}${this.row('Рівень циркуляції',fmt(fan.selected_level,0))}${this.row('Швидкість за планом',fmt(fan.proposed_percentage,0)+' %')}${numeric(fire.cooldown_remaining_seconds)>0?this.row('До вимкнення',fmt(fire.cooldown_remaining_seconds/60,1)+' хв'):''}<button class="text-button" data-tab="settings" data-settings-target="fireplace-settings">Налаштувати циркуляцію ↗</button></details></section>`;
  }
  forecastOverview() {
    const f=this.attrs('forecast'),o=this.attrs('outages'),known=f.schedule_known_until??o.known_until;
    const mode=o.mode??f.outage_mode,normal=mode==='normal'&&!isTrue(o.emergency);
    const stale=!Number.isFinite(Date.parse(f.created_at))||Date.now()-Date.parse(f.created_at)>660000;
    const warnings=[];
    if(Array.isArray(f.curve)&&f.curve.length) {
      if(!normal&&(mode==='unknown'||!isTrue(f.schedule_complete)))warnings.push('Графік мережі неповний'+(numeric(known)!==null?' · після '+this.time(known,true):'')+' нагрів умовний.');
      if(!isTrue(f.weather_complete))warnings.push('Погодинна погода неповна · далі використано останню відому температуру.');
      if(isTrue(f.reliability?.degraded))warnings.push('Точність знижена · обережне планування.');
    }
    if(o.warning)warnings.push(o.warning);
    const help='<details class="forecast-help"><summary aria-label="Як читати прогноз" title="Як читати прогноз">ⓘ</summary><div><strong>Як читати прогноз</strong><p>Тло — комфортний діапазон. Прогноз — оцінка, пунктир показує майбутню температуру.</p><p>Камін — оцінка залишкового тепла. Час майбутнього горіння невідомий.</p><p>Після кінця відомого графіка мережі нагрів умовний; перед виконанням контролер перевіряє фактичну мережу.</p></div></details>';
    return `<section class="card chart-card"><div class="card-heading"><h2>Наступні 24 години</h2>${help}</div><div class="section-meta"><span class="pill ${stale||isTrue(f.comfort_shortfall)?'warn':''}">${stale?'Прогноз застарів':isTrue(f.comfort_shortfall)?'Ризик зниження температури':'План утримує комфорт'}</span><span class="muted">Оновлено ${h(this.time(f.created_at))}</span></div>${this.forecastChart()}<div class="forecast-stats">${this.compactMetric('Мінімальна температура',fmt(this.state('forecast')),'°C','','minimum')}${this.compactMetric('Очікувана енергія',fmt(f.planned_kwh,2),'кВт·год','','energy')}${this.compactMetric('Горизонт прогнозу',fmt(f.horizon_hours,0),'год','','horizon')}${this.compactMetric(normal?'Планові відключення':'Графік мережі відомий до',normal?'Не очікуються':this.time(known,true),'','','schedule')}</div>${warnings.map(text=>`<p class="forecast-warning warn note" role="status">${h(text)}</p>`).join('')}</section>`;
  }
  heatingPlan() {
    const f=this.attrs('forecast'),segments=mergeSegments(f.heat_segments);
    const morning=Array.isArray(f.morning_runs_minutes)?f.morning_runs_minutes.reduce((sum,v)=>sum+(numeric(v)??0),0):null;
    const stale=!Number.isFinite(Date.parse(f.created_at))||Date.now()-Date.parse(f.created_at)>660000;
    return this.section('План нагріву',`<p class="plan-reason muted">${h(f.split_reason||'Контролер розраховує оптимальні інтервали.')}</p><div class="sessions-grid">${segments.map(s=>`<div class="session"><div><strong>${h(this.time(s.start,true))} → ${h(this.time(s.end))}</strong><span class="muted">${fmt((s.end-s.start)/60,0)} хв${isTrue(s.conditional)?' · умовний інтервал':''}</span></div><span class="heat-dot"></span></div>`).join('')||'<p class="empty">Нагрів не запланований.</p>'}</div><div class="plan-morning"><strong>Ранковий прогрів</strong><span>${fmt(morning,0)} хв <small class="muted">· мін. ${fmt(f.morning_min_minutes,0)} хв</small></span></div>${stale?'<p class="forecast-warning warn note">План застарів: потрібен новий розрахунок.</p>':''}<details class="plan-details"><summary>Подробиці плану</summary><div class="plan-breakdown">${this.compactMetric('Вечір',fmt(f.session_kwh?.evening,2),'кВт·год')}${this.compactMetric('Ранок',fmt(f.session_kwh?.morning,2),'кВт·год')}${this.compactMetric('Інші інтервали',fmt(f.session_kwh?.other,2),'кВт·год')}${this.compactMetric('Умовний нагрів',fmt(f.conditional_heat_minutes,0),'хв')}${this.compactMetric('Поза дозволеними годинами',fmt(f.outside_minutes,0),'хв')}${this.compactMetric('Перенесено через відключення',isTrue(f.outage_shifted)?'Так':'Ні')}</div><p class="muted note">${h(morning?'Ранковий прогрів зарезервовано.':f.split_reason||'Ранкового нагріву немає.')}</p></details>`, 'plan-card');
  }
  overview() {
    const c=this.attrs('controller'),budget=numeric(this.state('monthly_budget')),used=numeric(this.state('energy_monthly'));
    return `<div class="overview"><div class="hero card"><div><h1 class="eyebrow">Комфорт у вашому домі</h1><div class="temperature">${fmt(this.state('temperature'))}<span>°C</span></div><p class="hero-target"><span>${h(c.comfort_zone||'Поточний комфорт')}</span><span>Ціль <strong>${fmt(c.target_temperature)} °C</strong></span></p><button class="hero-outside" data-info="${h(this.id('outside'))}"><span>Надворі</span> <strong>${fmt(this.state('outside'))} °C</strong></button></div><div class="hero-side"><span class="eyebrow">Рішення контролера</span><h2>${h(c.reason||'Очікуємо даних')}</h2><p class="muted">${h(c.proposed_action||'')}</p><div class="comfort">Межі комфорту <strong>${fmt(c.lower_temperature)}–${fmt(c.upper_temperature)} °C</strong></div></div></div><div class="heat-sources">${this.boilerOverview()}${this.fireplaceOverview()}</div>${this.forecastOverview()}${this.heatingPlan()}<div class="columns"><div>${this.section('Температура й нагрів',this.historyChart(),'history-card')}${this.section('Енергія котла по годинах','<div class="native" data-native="energy"></div>')}</div><div>${this.section('Енергія за місяць',`<div class="large">${fmt(used,1)} <small>кВт·год</small></div><progress max="${budget!==null&&budget>0?budget:1}" value="${used??0}" aria-label="Місячна енергія"></progress><p class="muted">Орієнтир ${fmt(budget,0)} кВт·год${budget&&used!==null?' · '+fmt(used/budget*100,0)+' %':''}</p>`)}${this.section('Погода на наступну добу','<div class="native" data-native="weather"></div>'+this.field('weather_refresh','Оновити погодинну погоду'))}</div></div></div>`;
  }
  settings() {
    const fields=items=>items.map(([key,label])=>this.field(key,label)).join('');
    const tuning=(key,fallback)=>(this.attrs('system_settings').fields||[]).find(f=>f.key===key)?.value??fallback;
    return `<div class="settings-page compact-page"><div class="page-title"><h1>Налаштування</h1></div><div class="settings-grid">
    ${this.section('Комфорт',`<div class="comfort-groups"><fieldset><legend>День</legend><div class="control-grid thirds">${fields([['target','Ціль'],['lower','Мінімум'],['upper','Максимум']])}</div></fieldset><fieldset><legend>Під час сну</legend><div class="control-grid thirds">${fields([['night_target','Ціль'],['night_lower','Мінімум'],['night_upper','Максимум']])}</div><div class="control-grid">${fields([['sleep_start','Початок сну'],['sleep_end','Кінець сну']])}</div></fieldset></div>`, 'comfort-settings')}
    ${this.section('Розклад і витрати',`${this.field('strategy','Планування нагріву')}<div class="control-grid">${fields([['start','Дозволено з'],['end','Дозволено до'],['monthly_budget','Місячний орієнтир'],['session_limit','Ліміт сеансу, хв']])}</div>`)}
    ${this.section('Керування опаленням',`${this.field('mode','Режим системи')}<div class="control-grid">${fields([['armed','Дозволити команди'],['restore_once','Нагрів після появи мережі']])}</div>${this.field('native_setpoint','Уставка штатного резерву')}<div class="settings-status">${this.row('Одноразовий запит',this.state('restore_status'))}${this.row('Контролер',this.state('controller'))}${this.row('Команди',this.state('permitted')==='on'?'Дозволені':'Не дозволені')}</div>`)}
    ${this.section('Циркуляція',`<div class="circulation-config"><div class="control-grid circulation-mode">${fields([['fan_control','Автоматичні команди'],['fan_mode','Режим циркуляції']])}</div><div class="control-grid thirds circulation-levels">${fields([['fan_night_limit','1 · Ніч'],['fan_speed','2 · День стандарт'],['fan_intense_speed','3 · День інтенсив']])}</div></div><details class="inline-help"><summary>Коли змінюється рівень</summary><p>${h(tuning('fan_night_start','23:00'))}–${h(tuning('fan_night_end','07:00'))} — рівень 1. Удень при інтенсивному горінні — 3, за інших умов — 2. Нічна швидкість не перевищує стандартну, інтенсивна — не нижча за неї.</p><p>Запуск при різниці від ${fmt(tuning('fire_start_delta',5))} °C; нижче ${fmt(tuning('fire_stop_delta',3))} °C протягом ${fmt(tuning('fire_cooldown_seconds',300)/60)} хв — зупинка. При різниці нижче ${fmt(tuning('fire_fast_stop_delta',4))} °C і тренді ${fmt(tuning('fire_fast_stop_slope',-2))} °C/год або нижче — зупинка без затримки.</p></details>`, 'circulation-settings','fireplace-settings')}
    </div>${this.systemSettings()}</div>`;
  }
  systemSettings() {
    const a=this.attrs('system_settings'),fields=Array.isArray(a.fields)?a.fields:[];
    this.syncSystemSettings();
    const groups=[...new Set(fields.map(f=>f.group))];
    const helpButton=(key,label)=>`<button type="button" class="system-help" data-system-help="${h(key)}" aria-label="Підказка: ${h(label)}">?</button>`;
    const rows=fields.map(f=>{
      const value=Object.hasOwn(this.systemDraft,f.key)?this.systemDraft[f.key]:f.value;
      const common=`data-system-key="${h(f.key)}" aria-label="${h(f.label)}" ${this.systemPending?'disabled':''}`;
      const control=f.kind==='readonly'?`<span class="system-fixed">${h(value)} ${h(f.unit||'')}</span>`:
        `<div class="number"><input type="${f.kind==='time'?'time':'number'}" ${common} value="${h(value)}" ${f.kind==='time'?'':`min="${h(f.min)}" max="${h(f.max)}" step="${h(f.step)}"`}><span>${h(f.unit||'')}</span></div>`;
      return {group:f.group,html:`<div class="system-field"><div class="system-label"><label>${h(f.label)}</label>${helpButton(f.key,f.label)}</div>${control}</div>`};
    });
    const existing=[['target','Ціль удень'],['lower','Мінімум удень'],['upper','Максимум удень'],['night_target','Ціль уві сні'],['night_lower','Мінімум уві сні'],['night_upper','Максимум уві сні'],['sleep_start','Початок сну'],['sleep_end','Кінець сну'],['start','Дозволений початок нагріву'],['end','Дозволений кінець нагріву'],['monthly_budget','Місячний орієнтир енергії'],['session_limit','Ліміт одноразового нагріву'],['native_setpoint','Уставка штатного auto'],['fan_night_limit','Швидкість 1 · ніч'],['fan_speed','Швидкість 2 · стандарт'],['fan_intense_speed','Швидкість 3 · інтенсив'],['fan_mode','Режим циркуляції'],['mode','Режим опалення'],['strategy','Стратегія нагріву'],['armed','Дозвіл команд котлу'],['restore_once','Одноразовий нагрів після мережі'],['fan_control','Дозвіл команд циркуляції'],['fan_backup','Підтвердження резерву циркуляції'],['ai_auto_apply','Застосування перевірених пропозицій ШІ']];
    this.systemExisting=existing;
    const helpers=existing.map(([key,label])=>`<div class="system-field"><div class="system-label"><span>${h(label)}</span>${helpButton('entity:'+key,label)}</div><button class="system-entity" data-info="${h(this.id(key))}" ${this.entity(key)?'':'disabled'}>${h(this.state(key))} <small>${h(this.attrs(key).unit_of_measurement||'')} ↗</small></button></div>`).join('');
    return `<details class="card system-settings"><summary>Системні налаштування <span aria-hidden="true">＋</span></summary><div class="system-body"><details class="system-group"><summary>Температури, режими й ліміти</summary><div class="system-grid">${helpers}</div></details>${groups.map(group=>`<details class="system-group"><summary>${h(group)}</summary><div class="system-grid">${rows.filter(r=>r.group===group).map(r=>r.html).join('')}</div></details>`).join('')}${fields.length?'':'<p class="muted">Розширені параметри будуть доступні після оновлення контролера. Чинні helpers можна редагувати вище.</p>'}<div class="system-actions"><span class="system-message" role="status">${h(this.systemMessage||'')}</span><button data-system-reset ${!Object.keys(this.systemDraft).length||this.systemPending?'disabled':''}>Скасувати зміни</button><button data-system-save ${!Object.keys(this.systemDraft).length||this.systemPending?'disabled':''}>${this.systemPending?'Очікуємо контролер…':'Зберегти зміни'}</button></div></div></details>${this.systemHelp?this.systemHelpDialog(fields):''}`;
  }
  systemHelpDialog(fields) {
    const f=fields.find(f=>f.key===this.systemHelp),entityKey=this.systemHelp.startsWith('entity:')?this.systemHelp.slice(7):null;
    const label=f?.label||this.systemExisting?.find(([key])=>key===entityKey)?.[1]||'Параметр';
    const existingHelp={
      target:'Денна цільова температура кімнати.',lower:'Нижня межа денного комфорту.',upper:'Верхня межа денного комфорту; має бути не нижчою за ціль.',
      night_target:'Ціль кімнати під час сну.',night_lower:'Нижня межа нічного комфорту.',night_upper:'Верхня межа нічного комфорту.',
      sleep_start:'Початок нічного температурного профілю.',sleep_end:'Кінець нічного температурного профілю.',
      start:'Початок дозволеного вікна нагріву за обраною стратегією.',end:'Кінець дозволеного вікна нагріву. Однакові початок і кінець не означають цілодобову роботу.',
      monthly_budget:'Орієнтир споживання за місяць у кВт·год. Нуль вимикає бюджетну поправку.',session_limit:'Максимальна тривалість одноразового запиту після повернення мережі, у хвилинах.',
      native_setpoint:'Температурна уставка штатного auto для резервного керування.',fan_night_limit:'Швидкість у нічному вікні, не вище стандартної.',fan_speed:'Звичайна денна швидкість циркуляції.',fan_intense_speed:'Денна швидкість за інтенсивного горіння; нічний рівень має пріоритет.',
      fan_backup:'Увімкніть лише коли вентилятор фізично працює від резервного живлення. Це підтвердження не скасовує перевірки доступності та heartbeat інвертора.',fan_mode:'Авто керує ON/OFF за запитами; Ручний запуск тримає вентилятор увімкненим; Зупинити підтримує OFF. Перевірки живлення й дозвіл команд залишаються чинними.',
      mode:'Обирає автоматичне керування, спостереження, штатний auto або вимкнення.',strategy:'Визначає, як дозволене вікно і тариф впливають на планування нагріву.',armed:'Дозволяє команди обладнанню за умови чинних перевірок контролера.',restore_once:'Запитує один нагрів після підтвердженого відновлення мережі.',fan_control:'Дозволяє контролеру надсилати команди вентилятору.',ai_auto_apply:'Дозволяє застосовувати пропозиції ШІ лише після чинної перевірки моделі.'
    };
    const instruction=entityKey?'Натисніть значення зі стрілкою: відкриється стандартний редактор Home Assistant. Зміна helper застосовується його чинним механізмом.':f?.kind==='readonly'?'Це системна константа, наведена для діагностики. Ця панель не змінює захисну логіку.':'Змініть поле й натисніть «Зберегти зміни». Поки не збережено, контролер використовує попереднє значення. Перевірені зміни зберігаються поза каталогом HACS і відновлюються після reload/update.';
    return `<dialog class="system-dialog" aria-labelledby="system-help-title"><div class="system-dialog-heading"><h2 id="system-help-title">${h(label)}</h2><button data-system-close aria-label="Закрити підказку">×</button></div><p>${h(f?.help||existingHelp[entityKey]||'Чинний параметр Home Assistant.')}</p>${f&&f.kind!=='readonly'?`<p class="muted">За замовчуванням: ${h(f.default)} ${h(f.unit||'')}${f.kind==='number'?` · Межі: ${h(f.min)}–${h(f.max)}`:''}</p>`:''}<p>${h(instruction)}</p></dialog>`;
  }
  closeSystemHelp() {
    const key=this.systemHelp;this.systemHelp=null;this.render();
    [...this.shadowRoot.querySelectorAll('[data-system-help]')].find(el=>el.dataset.systemHelp===key)?.focus();
  }
  syncSystemSettings() {
    const a=this.attrs('system_settings');
    if(this.systemPending&&a.last_request_id===this.systemPending) {
      if(isTrue(a.last_success)){this.systemDraft={};this.systemMessage='Зміни збережено';}
      else this.systemMessage=a.last_error||'Зміни відхилено';
      this.systemPending=null;clearTimeout(this.systemTimeout);
    }
  }
  async saveSystemSettings() {
    if(this.systemPending||!Object.keys(this.systemDraft).length)return;
    if([...this.shadowRoot.querySelectorAll('[data-system-key]')].some(el=>!el.reportValidity()))return;
    const request=globalThis.crypto?.randomUUID?.()||String(Date.now());
    this.systemPending=request;this.systemMessage='';this.render();
    try {
      await this.hass.callApi('POST','events/heating_system_settings',{request_id:request,revision:numeric(this.attrs('system_settings').revision)??0,values:{...this.systemDraft}});
      this.systemTimeout=setTimeout(()=>{if(this.systemPending===request){this.systemPending=null;this.systemMessage='Немає підтвердження контролера. Оновіть дані перед повтором.';this.render();}},15000);
    } catch(error){this.systemPending=null;this.systemMessage=`Не вдалося зберегти: ${error.message||'помилка Home Assistant'}`;}
    this.render();
  }
  model() {
    const a=this.attrs('model'),env=this.attrs('environment'),p=a.power_learning||{},s=this.attrs('summary').last_24h||{},f=this.attrs('forecast');
    const updated=this.entity('summary')?.last_updated;
    const facts=items=>items.map(([label,value,unit])=>this.compactMetric(label,value,unit||'')).join('');
    return `<div class="model-page compact-page"><div class="page-title"><h1>Модель і ШІ</h1></div><section class="card model-stats">${this.compactMetric('Модель v'+(a.parameters?.version??'—'),this.state('model'),'','model')}${this.compactMetric('Годинна похибка',fmt(a.hour_mae,3),'°C','model')}${this.compactMetric('Придатні інтервали',fmt(a.usable_pairs,0),'','model')}${this.compactMetric('Запас тепла',fmt(this.state('environment')),'°C','environment')}</section><div class="model-grid">
    ${this.section('Висновок за добу',`<p class="report-text">${h(this.state('summary'))}</p><div class="report-meta muted">Оновлено ${h(this.time(updated,true))}</div>${!Number.isFinite(Date.parse(updated))||Date.now()-Date.parse(updated)>660000?'<p class="warn pill">Дані звіту застаріли</p>':''}<div class="report-stats">${facts([['Охоплення',fmt(s.hours_collected),'год'],['Придатні проби',`${fmt(s.valid_samples,0)} / ${fmt(s.samples,0)}`],['Якість проб',numeric(s.coverage)===null?'—':fmt(s.coverage*100,0),'%']])}</div>`, 'ai-report')}
    ${this.section('Результат навчання',`<p class="learning-result">${h(a.last_result||'Навчання ще не виконувалось')}</p><div class="fact-grid">${facts([['Останній аналіз',this.time(a.last_analysis,true)],['Кількість проб',fmt(a.samples,0)]])}</div>`)}
    ${this.section('Дії',`${this.field('ai_auto_apply','Застосовувати перевірені пропозиції')}<div class="action-buttons">${this.field('rollback','Попередня модель')}${this.field('weather_refresh','Оновити погоду')}</div><div class="report-meta muted">Погодинні дані до ${h(this.time(f.weather_known_until,true))}</div>`, 'model-actions')}
    ${this.section('Модуляція котла',`<p class="model-meta muted">${h(a.power_learning_status||'')} · ${fmt(p.intervals,0)} інтервалів · ${fmt(p.minutes,0)} хв</p><div class="table-scroll"><table><thead><tr><th>Обратка</th><th>Потужність</th><th>Досвід</th></tr></thead><tbody>${(p.bands||[]).map(b=>`<tr><td>${fmt(b.return_min,0)}–${fmt(b.return_max,0)} °C</td><td>${fmt(b.measured_kw)} кВт</td><td>${fmt(b.minutes,0)} хв</td></tr>`).join('')||'<tr><td colspan="3">Очікуємо підтверджених сеансів.</td></tr>'}</tbody></table></div>`)}
    ${this.section('Підлога й погода',`<div class="fact-grid">${facts([['Виміряна обратка',fmt(env.raw_return),'°C'],['Зовнішній прогрів',fmt(env.external_gain_c_per_hour,3),'°C/год'],['Сонячна складова',isTrue(env.solar_validated)?'Перевірена':'Навчається'],['Днів спостережень',fmt(env.solar_learning_days,0)],['Поточна похибка',fmt(a.reliability?.recent_mae,2),'°C'],['Корекція погоди',fmt(f.local_weather_bias),'°C']])}</div>${isTrue(a.reliability?.degraded)?`<p class="warn pill">Точність знижена · запас ${fmt(env.upper_margin)} °C</p>`:''}`)}
    ${this.section('Журнал рішень',`<div class="journal">${[...(a.journal||[])].reverse().map(r=>`<div class="entry"><time>${h(this.time(r.time,true))}</time><p>${h(r.reason)}</p></div>`).join('')||'<p class="empty">Журнал ще порожній.</p>'}</div>`, 'model-journal')}
    <section class="card model-advanced"><details><summary>Параметри й діагностика</summary><div class="parameter-grid">${Object.entries(a.parameters||{}).map(([key,value])=>this.row(key,typeof value==='number'?fmt(value,4):value)).join('')}</div><div class="fact-grid controller-facts">${facts([['Зв’язок',this.state('alive')==='on'?'Є':'Не підтверджений'],['Команди',this.state('permitted')==='on'?'Дозволені':'Не дозволені'],['Останній цикл',this.time(this.state('heartbeat'),true)]])}</div><p class="muted note">Запас тепла — еквівалент, а не температура поверхні підлоги. Зовнішній прогрів сам по собі не доводить сонячний внесок.</p><p class="muted note">Прогноз враховує енергію та модуляцію; початкова оцінка для невивчених температур ${fmt(f.power_prior_kw)} кВт. Частка придатних проб не гарантує відсутності часових прогалин.</p></details></section>
    </div></div>`;
  }
  render() {
    if(!this.hass) return;
    const beat=Date.parse(this.state('heartbeat')), alive=this.state('alive')==='on'&&Number.isFinite(beat)&&Date.now()-beat<90000;
    const quality=isTrue(this.attrs('controller').quality), grid=this.attrs('grid_status').raw;
    const html=`<style>${styles}</style><main><header><div class="brand"><span class="brand-icon">${icon('home')}</span><div><strong>Heating <span>AI</span></strong><small>Клімат вашого дому</small></div></div><div class="header-status"><span class="dot ${alive?'':'red'}"></span>${alive?'Контролер онлайн':'Перевірте контролер'}<span class="mode">${h(this.state('mode'))}</span><button class="menu-button" data-menu aria-label="Відкрити меню Home Assistant">${icon('menu')}</button></div></header><nav aria-label="Розділи клімату">${[['overview','Огляд'],['settings','Налаштування'],['model','Модель і ШІ']].map(([key,label])=>`<button data-tab="${key}" aria-current="${this.tab===key?'page':'false'}" class="${this.tab===key?'selected':''}">${label}</button>`).join('')}<a href="/climate-control">Поточні панелі ↗</a></nav><div class="notice ${this.error?'':'hidden'}" role="alert">${h(this.error || '')}</div><div class="notice ${alive&&quality&&grid==='on'?'hidden':''}" role="status">${!alive?'Немає свіжого підтвердження роботи контролера.':!quality?'Контролеру бракує актуальних вимірювань.':'Нагрів очікує підтвердження електромережі.'}</div><div class="content">${this.tab==='settings'?this.settings():this.tab==='model'?this.model():this.overview()}</div><footer>Тепло під контролем <span>Home Assistant · Heating AI ${VERSION}</span></footer></main>`;
    const template=document.createElement('template');template.innerHTML=html;
    patchDOM(this.shadowRoot,template.content);
    const dialog=this.shadowRoot.querySelector('.system-dialog');
    if(dialog&&!dialog.open)dialog.showModal();
    this.observePlot();
    this.mountNative();
    this.loadHistory();
  }
  async mountNative() {
    const slots=[...this.shadowRoot.querySelectorAll('[data-native]')];
    if(!slots.length)return;
    try {
      this.helpers ||= await (this.helpersPromise ||= window.loadCardHelpers());
      const ids=keys=>keys.map(key=>this.id(key)).filter(id=>id&&this.hass.states[id]);
      const configs={
        energy:{type:'statistics-graph',entities:ids(['energy']),stat_types:['change'],period:'hour',chart_type:'bar',days_to_show:1},
        weather:{type:'weather-forecast',entity:this.id('weather'),forecast_type:'hourly',forecast_slots:24,show_current:false,show_forecast:true}
      };
      for(const slot of slots) {
        if(!slot.isConnected)continue;
        const key=slot.dataset.native, config=configs[key];
        if(config.entity&&!this.hass.states[config.entity] || config.entities?.length===0){slot.textContent='Джерело ще не під’єднане до панелі.';continue;}
        const signature=JSON.stringify(config);
        if(!this.nativeCards.has(key)||this.nativeConfigs.get(key)!==signature){const current=this.nativeCards.get(key);if(current?.setConfig)current.setConfig(config);else this.nativeCards.set(key,this.helpers.createCardElement(config));this.nativeConfigs.set(key,signature);}
        const card=this.nativeCards.get(key);card.hass=this.hass;if(slot.firstElementChild!==card)slot.replaceChildren(card);
      }
    } catch { this.helpersPromise=null;for(const slot of slots)if(slot.isConnected)slot.textContent='Не вдалося завантажити графік Home Assistant. Оновіть сторінку.'; }
  }
  async clicked(e) {
    const el=e.target.closest('[data-tab],[data-info],[data-press],[data-menu],[data-history-range],[data-history-series],[data-history-refresh],[data-system-help],[data-system-close],[data-system-save],[data-system-reset]'); if(!el)return;
    if(el.hasAttribute('data-system-help')){this.systemHelp=el.dataset.systemHelp;this.render();return;}
    if(el.hasAttribute('data-system-close')){this.closeSystemHelp();return;}
    if(el.hasAttribute('data-system-save')){await this.saveSystemSettings();return;}
    if(el.hasAttribute('data-system-reset')){this.systemDraft={};this.systemMessage='';this.render();return;}
    if(el.dataset.historyRange){this.historyRange=Number(el.dataset.historyRange);this.historyCursor=null;this.render();return;}
    if(el.dataset.historySeries){const key=el.dataset.historySeries;if(this.historyVisible.has(key)){if(this.historyVisible.size>1)this.historyVisible.delete(key);}else this.historyVisible.add(key);this.render();return;}
    if(el.hasAttribute('data-history-refresh')){this.loadHistory(true);return;}
    if(el.dataset.tab){this.tab=el.dataset.tab;this.error='';this.render();if(el.dataset.settingsTarget)this.shadowRoot.getElementById(el.dataset.settingsTarget)?.scrollIntoView({block:'start'});return;}
    if(el.hasAttribute('data-menu')){this.dispatchEvent(new CustomEvent('hass-toggle-menu',{bubbles:true,composed:true}));return;}
    if(el.dataset.info){this.dispatchEvent(new CustomEvent('hass-more-info',{detail:{entityId:el.dataset.info},bubbles:true,composed:true}));return;}
    if(el.dataset.press){if(el.dataset.press==='rollback'&&!confirm('Повернути попередню модель? Поточна модель збережеться для наступного відкату.'))return;await this.call(el.dataset.press,'press',{});}
  }
  async changed(e) {
    if(e.target.hasAttribute('data-system-key')) {
      const el=e.target;
      if(!el.reportValidity()||el.type==='number'&&numeric(el.value)===null)return;
      this.systemDraft[el.dataset.systemKey]=el.type==='number'?Number(el.value):el.value;
      this.systemMessage='Є незбережені зміни';this.render();return;
    }
    const el=e.target, key=el.dataset.control;if(!key)return;
    const id=this.id(key), domain=id.split('.')[0];
    if(domain==='input_number') {
      if(!el.checkValidity()||numeric(el.value)===null){el.reportValidity();return;}
      await this.call(key,'set_value',{value:Number(el.value)});
    } else if(domain==='input_select') await this.call(key,'select_option',{option:el.value});
    else if(domain==='input_datetime') {if(el.value)await this.call(key,'set_datetime',{time:el.value.length===5?el.value+':00':el.value});}
    else if(domain==='input_boolean') {
      if(el.checked&&['armed','restore_once'].includes(key)&&!confirm(key==='armed'?'Дозволити автоматичні команди опалення?':'Запросити один нагрів після появи підтвердженої мережі?')){el.checked=false;return;}
      await this.call(key,el.checked?'turn_on':'turn_off',{});
    }
    el.blur();this.render();
  }
  async call(key,service,data) {
    const id=this.id(key), entity=this.hass.states[id];
    if(!entity||['unknown','unavailable'].includes(entity.state)||this.pending.has(id))return;
    const domain=id.split('.')[0];
    const allowed={input_number:['set_value'],input_select:['select_option'],input_datetime:['set_datetime'],input_boolean:['turn_on','turn_off'],input_button:['press']};
    if(!allowed[domain]?.includes(service))return;
    this.pending.add(id);this.error='';
    try { await this.hass.callService(domain,service,{...data,entity_id:id}); }
    catch(error) {this.error=`Не вдалося зберегти: ${error.message || 'помилка Home Assistant'}`;}
    finally{this.pending.delete(id);this.render();}
  }
}
const styles=`
/* The surrounding HA theme owns the palette, typography and surface shape. */
:host {
  display:block;width:100%;min-width:0;container-type:inline-size;
  color:var(--primary-text-color,#e8e8e8);
  --green:var(--primary-color,#69d5b2);
  --muted:var(--secondary-text-color,#b4b4b4);
  --surface:var(--ha-card-background,var(--card-background-color,#1c1c1c));
  --raised:color-mix(in srgb,var(--primary-text-color,#e8e8e8) 5%,var(--surface));
  --stroke:var(--ha-card-border-color,var(--divider-color,rgba(155,155,155,.2)));
  --control-stroke:color-mix(in srgb,var(--primary-text-color,#e8e8e8) 25%,var(--surface));
  --card-radius:var(--ha-card-border-radius,20px);
  --source-red:#ffb4ab;--source-yellow:#f1cf86;
  --history-room:var(--green);--history-return:#e9bd79;
  --history-outside:var(--muted);--history-fire:var(--source-yellow);
  --heat-fill:color-mix(in srgb,#c9505c 12%,var(--surface));
  --warm-fill:color-mix(in srgb,#c9a04b 10%,var(--surface));
  --ease-out:cubic-bezier(.23,1,.32,1);
  font-family:var(--ha-font-family-body,var(--primary-font-family,Roboto,system-ui,sans-serif));
  font-size:14px;line-height:1.5;font-variant-numeric:tabular-nums;
  color-scheme:dark;caret-color:var(--green);
}

:host([data-light]) {
  --source-red:#9c3039;--source-yellow:#815c17;
  --green:color-mix(in srgb,var(--primary-color,#167c5b) 70%,var(--primary-text-color,#212121));
  --history-return:#96621c;color-scheme:light;
}

*{box-sizing:border-box}
main{width:100%;max-width:1360px;margin:auto;padding:16px 28px 12px}
header{display:flex;justify-content:space-between;align-items:center;gap:16px;padding:4px 0 16px}
.brand{display:flex;gap:10px;align-items:center;min-width:0}
.brand-icon{display:grid;place-items:center;color:var(--green);width:36px;height:40px;flex:none}
.brand strong{font-size:22px;font-weight:600;letter-spacing:-.5px;line-height:1.3}
.brand strong span{color:var(--green)}
.brand small{display:block;color:var(--muted);font-size:12px;letter-spacing:0}
.header-status{display:flex;align-items:center;gap:8px;font-size:12px;white-space:nowrap}
.dot{width:7px;height:7px;border-radius:50%;background:var(--green)}
.dot.red{background:var(--source-red)}
.mode{margin-left:12px;color:var(--muted);padding-left:20px;border-left:1px solid var(--stroke)}
nav{display:flex;align-items:center;gap:4px;border-bottom:1px solid var(--stroke);margin-bottom:16px;padding-bottom:8px}
button,input,select{font:inherit}
button,a,input,select{touch-action:manipulation}
button{min-height:44px;cursor:pointer;color:inherit;background:transparent;border:1px solid var(--control-stroke);border-radius:14px;padding:9px 14px;transition:transform 120ms var(--ease-out)}
button:hover{border-color:var(--control-stroke)}
button:focus-visible,input:focus-visible,select:focus-visible,a:focus-visible{outline:2px solid var(--green);outline-offset:4px}
button:disabled{color:var(--muted);background:var(--raised);cursor:default}
nav button{border:0;color:var(--muted);padding:9px 18px;font-size:14px;font-weight:500}
nav button.selected{background:var(--raised);color:var(--primary-text-color,#e8e8e8)}
a{color:var(--green);text-decoration:none}
nav a{margin-left:auto;font-size:12px;color:var(--muted)}
.card{background:var(--surface);border:1px solid var(--stroke);border-radius:var(--card-radius);padding:20px;margin-bottom:12px;min-width:0;box-shadow:var(--ha-card-box-shadow,none)}
h1,h2,p{margin:0}
h1{font-size:24px;line-height:1.3;font-weight:600;letter-spacing:-.5px}
h2{font-size:16px;font-weight:600;letter-spacing:0;line-height:1.4;margin-bottom:12px;text-wrap:balance}
p{margin:10px 0}
.muted,.eyebrow{font-size:12px;font-weight:400;letter-spacing:0;line-height:1.4;display:block}
.eyebrow{font-size:11px;font-weight:500;letter-spacing:1.3px;display:block}
.hero{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);align-items:center;gap:24px;background:var(--surface)}
.temperature{font-size:64px;line-height:1.1;letter-spacing:-2px;font-weight:500;margin-top:8px}
.temperature span{font-size:24px;color:var(--muted);letter-spacing:0;vertical-align:top;margin-left:4px;line-height:1.5}
.hero p{margin:6px 0 16px}
.hero-side{border-left:1px solid var(--stroke);padding-left:24px}
.hero-side h2{font-size:22px;margin:8px 0;line-height:1.35}
.comfort{margin-top:25px;font-size:12px;color:var(--muted)}
.comfort strong{display:block;font-size:18px;color:var(--primary-text-color)}
.pill{display:inline-block;background:var(--raised);border:1px solid transparent;color:var(--primary-text-color,#e8e8e8);border-radius:999px;padding:4px 10px;font-size:12px;line-height:1.4}
.warn{color:var(--source-yellow);background:var(--warm-fill);border-color:var(--stroke)}
.metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:18px}
.metric{display:flex;flex-direction:column;align-items:flex-start;text-align:left;gap:12px;padding:22px}
.metric strong{font-size:28px;font-weight:500;line-height:1.2;letter-spacing:-.8px}
.metric strong small{font-size:13px;color:var(--muted);letter-spacing:0}
.metric .muted{font-size:11px}
.columns{display:grid;grid-template-columns:minmax(0,1.85fr) minmax(300px,1fr);gap:20px}
.section-meta{display:flex;justify-content:space-between;align-items:center;gap:10px;font-size:12px;margin-bottom:12px}
.forecast-plot{width:100%;min-width:0}
.chart-card svg{width:100%;height:auto;max-width:100%;display:block;color:var(--muted)}
.legend{display:flex;flex-wrap:wrap;gap:8px 16px;font-size:12px;color:var(--muted);margin:12px 0}
.legend span:first-child{color:var(--green)}
.legend span:nth-child(2){color:var(--source-red)}
.legend span:nth-child(3){color:var(--source-yellow)}
.forecast-stats{display:grid;grid-template-columns:1fr 1fr;gap:0 24px}
.row{display:flex;align-items:baseline;justify-content:space-between;gap:12px;padding:10px 0;border-bottom:1px solid var(--stroke);font-size:12px}
.row strong{font-weight:500;text-align:right;overflow-wrap:anywhere}
.row:last-child{border-bottom:0}
.note{font-size:12px;line-height:1.6}
.session{display:flex;justify-content:space-between;gap:10px;padding:16px 0;border-bottom:1px solid var(--stroke)}
.session strong{font-size:13px}
.session .muted{display:block;font-size:11px}
.heat-dot{width:7px;height:7px;flex:none;background:var(--source-red);border-radius:50%;margin-top:8px}
.inset{background:var(--raised);border-radius:14px;padding:16px;margin-top:16px}
.large{font-size:32px;letter-spacing:-1px}
.large small{font-size:14px;color:var(--muted)}
progress{width:100%;height:6px;accent-color:var(--green);margin-top:18px;border:0;border-radius:8px;overflow:hidden}
progress::-webkit-progress-bar{background:var(--stroke)}
progress::-webkit-progress-value{background:var(--green)}
.text-button{border:0;color:var(--green);padding-left:0;font-size:12px;margin-top:12px}
.page-title{margin:30px 0}
.settings-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,420px),1fr));gap:0 20px}
.field{display:flex;align-items:center;justify-content:space-between;gap:20px;padding:15px 0;border-top:1px solid var(--stroke);font-size:13px}
.field small{display:block;font-size:11px}
.field input,.field select{background:var(--raised);color:inherit;border:1px solid var(--control-stroke);border-radius:12px;padding:8px 10px;max-width:220px;min-height:44px}
.number{display:flex;align-items:center;gap:8px}
.number input{width:85px}
.number span{font-size:11px;color:var(--muted)}
.toggle{position:relative;display:inline-flex;min-width:44px;min-height:44px;align-items:center}
.toggle input{opacity:0;position:absolute;inset:0;width:100%;margin:0}
.toggle span{width:44px;height:26px;border-radius:26px;background:var(--control-stroke);pointer-events:none}
.toggle span:after{content:'';display:block;width:20px;height:20px;border-radius:50%;background:var(--primary-text-color,#e8e8e8);transform:translate(3px,3px);transition:transform 160ms var(--ease-out)}
.toggle input:checked+span{background:var(--green)}
.toggle input:checked+span:after{background:var(--surface);transform:translate(21px,3px)}
.toggle:focus-within{outline:2px solid var(--green);border-radius:24px}
.table-scroll{overflow:auto}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;font-size:11px;color:var(--muted);font-weight:400}
td,th{padding:12px 8px;border-bottom:1px solid var(--stroke)}
td:not(:first-child),th:not(:first-child){text-align:right}
.journal{max-height:600px;overflow:auto}
.entry{display:grid;grid-template-columns:90px 1fr;gap:15px;border-bottom:1px solid var(--stroke);padding:14px 0}
.entry time{font-size:11px;color:var(--muted)}
.entry p{font-size:12px;margin:0}
.columns>div{min-width:0}
.native{width:100%;max-width:100%;min-width:0;min-height:110px;--ha-card-border-width:0;--ha-card-box-shadow:none}
.native>*{display:block;width:100%;max-width:100%;min-width:0}
.empty{color:var(--muted);padding:20px 0;font-size:13px}
.notice{border-radius:14px;background:var(--warm-fill);border:1px solid var(--stroke);color:var(--source-yellow);padding:12px 16px;margin-bottom:12px;font-size:13px}
.hidden{display:none}
footer{display:flex;justify-content:space-between;gap:12px;color:var(--muted);font-size:11px;padding:12px 2px 16px}
.menu-button{min-width:44px;border:0;font-size:20px;padding:2px 6px}

.history-tools{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-top:-4px;margin-bottom:12px}
.history-ranges{display:flex;gap:2px;padding:2px;border-radius:14px;background:var(--raised)}
.history-ranges button{border:0;padding:6px 14px;font-size:12px;min-height:44px;color:var(--muted)}
.history-ranges button[aria-pressed="true"]{background:var(--surface);color:var(--primary-text-color,#e8e8e8)}
.history-refresh{font-size:24px;width:44px;height:44px;padding:0;border:0;color:var(--muted)}
.history-legend{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin:8px 0 18px}
.history-legend button{min-width:0;text-align:left;border-color:transparent;padding:8px 10px;background:transparent;border-radius:12px}
.history-legend button>span{display:flex;align-items:center;gap:6px;font-size:11px;color:var(--muted)}
.history-legend i,.history-totals i{display:inline-block;width:7px;height:7px;flex-shrink:0;border-radius:50%;background:var(--series-color)}
.history-legend strong{display:block;font-weight:500;font-size:20px;white-space:nowrap}
.history-legend strong small{font-size:11px;color:var(--muted)}
.history-legend button[aria-pressed="true"]{border-color:var(--control-stroke);background:var(--raised)}
.history-legend button[aria-pressed="false"] i{background:transparent;border:1px solid var(--series-color)}
.history-legend button[aria-pressed="false"] strong{color:var(--muted)}
.history-plot{width:100%;min-width:0;touch-action:pan-y}
.history-plot:focus-visible{outline:2px solid var(--green);outline-offset:3px;border-radius:6px}
.history-plot svg{display:block;width:100%;height:auto;max-width:100%;color:var(--muted);overflow:visible}
.history-totals{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin:8px 0 0;padding-top:10px;border-top:1px solid var(--stroke);font-size:11px;color:var(--muted)}
.history-totals>span{min-width:0}
.history-totals i{margin-right:7px}
.history-totals strong{display:block;color:var(--primary-text-color);font-size:15px;font-weight:500;padding-left:14px}
.history-readout{font-size:11px;margin:10px 0 0;min-height:18px}
.history-placeholder{min-height:330px;display:grid;place-items:center;font-size:13px;color:var(--muted)}

@container(max-width:600px){.history-ranges button{min-height:44px;padding:5px 10px}
.history-legend{gap:4px}
.history-legend button{padding:7px 6px}
.history-legend button>span{font-size:11px;gap:4px}
.history-legend strong{font-size:18px}
.history-tools{margin-bottom:6px}
.history-readout{font-size:11px}
}

section[id]{scroll-margin-top:16px}

@container(min-width:1600px){main{padding:24px 42px}
}
@container(max-width:1000px){main{padding:15px 20px}
.columns{grid-template-columns:1fr}
.hero{gap:25px}
.metrics{gap:12px}
.metric{padding:17px}
.metric strong{font-size:23px}
.mode{display:none}
}
@container(max-width:600px){main{padding:8px 12px}
header{padding:8px 2px 18px;gap:10px}
.brand strong{font-size:20px}
.brand-icon{width:26px;height:38px}
.header-status{font-size:11px}
.header-status .dot{display:block}
nav{gap:3px;margin-bottom:16px}
nav button{padding:10px 12px;font-size:12px}
nav a{display:none}
.hero{grid-template-columns:minmax(0,.85fr) minmax(0,1.15fr);padding:18px;gap:14px;align-items:center}
.hero>div{min-width:0}
.temperature{font-size:44px;font-size:42px;letter-spacing:-1.5px;white-space:nowrap}
.temperature span{font-size:20px;letter-spacing:-1px;margin-left:3px}
.hero p{font-size:11px;line-height:1.5;margin:6px 0 10px}
.hero .pill{font-size:11px;letter-spacing:0;padding:4px 8px}
.hero-side{padding:0 0 0 14px;border-left:1px solid var(--stroke);border-top:0}
.hero-side h2{font-size:15px;line-height:1.4;margin:8px 0}
.hero .eyebrow{font-size:12px;letter-spacing:0}
.comfort{margin-top:10px;font-size:11px}
.comfort strong{font-size:14px}
.metrics{grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
.metric{padding:16px;gap:10px;border-radius:16px}
.metric strong{font-size:23px}
.eyebrow{font-size:11px;letter-spacing:.8px}
.card{padding:18px;border-radius:var(--card-radius);margin-bottom:14px}
.columns{gap:0}
.settings-grid{grid-template-columns:1fr}
.field{gap:12px;font-size:12px}
.field select{max-width:170px;font-size:11px}
.section-meta{flex-wrap:wrap}
.forecast-stats{grid-template-columns:1fr}
.entry{grid-template-columns:75px 1fr;gap:10px}
h1{font-size:23px}
footer span{font-size:11px}
}

.overview .card{padding:18px;margin-bottom:12px}
.overview h2{margin-bottom:12px}
.overview .columns{gap:14px}
.overview .hero{padding:24px;gap:24px}
.overview .hero-side{padding-left:24px}
.overview .hero-target{margin:4px 0 0;display:flex;flex-wrap:wrap;gap:2px 8px}
.hero-target>span{white-space:normal}
.hero-target strong{font-weight:500}
.hero-outside{display:flex;align-items:center;gap:8px;min-height:44px;padding:0;border:0;font-size:12px;color:var(--muted)}
.hero-outside strong{font-size:15px;font-weight:500;color:var(--primary-text-color)}
.overview .comfort{margin-top:12px}
.heat-sources{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin-bottom:12px}
.heat-sources>.card{margin:0;min-width:0}
.card-heading{display:flex;align-items:center;justify-content:space-between;gap:10px;min-width:0}
.overview .card-heading h2{margin:0}
.boiler-status{font-size:12px;white-space:nowrap;padding:3px 0 3px 9px;color:var(--muted);background:transparent;border:0}
.boiler-card[data-heating="true"]{border-color:color-mix(in srgb,var(--source-red) 32%,var(--stroke));background:var(--heat-fill)}
.boiler-card[data-heating="true"] .boiler-status{color:var(--source-red);background:transparent;border-color:transparent}
.boiler-metrics{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin-top:8px}
.compact-metric{min-width:0;text-align:left;padding:8px 0;border:0;border-radius:8px}
.compact-metric>span{display:block;font-size:11px;line-height:1.4}
.compact-metric>strong{display:block;font-size:19px;font-weight:500;line-height:1.35;margin-top:4px;overflow-wrap:anywhere}
.compact-metric small{font-size:11px;font-weight:400;color:var(--muted)}
.boiler-metrics .compact-metric strong{font-size:24px}
.boiler-metrics .compact-metric small{display:block;font-size:11px}
.boiler-note{font-size:11px;line-height:1.5;margin:4px 0 0}

.fireplace-card{--fire-accent:var(--muted)}
.fireplace-card[data-phase="Розпалювання"]{--fire-accent:var(--source-yellow);border-color:color-mix(in srgb,var(--source-yellow) 25%,var(--stroke));background:var(--warm-fill)}
.fireplace-card[data-phase="Горить"]{--fire-accent:var(--source-red);border-color:color-mix(in srgb,var(--source-red) 32%,var(--stroke));background:var(--heat-fill)}
.fireplace-card[data-phase="Інтенсивно горить"]{--fire-accent:var(--source-red);border-color:color-mix(in srgb,var(--source-red) 42%,var(--stroke));background:color-mix(in srgb,#c9505c 18%,var(--surface))}
.fireplace-card[data-phase="Згасає"]{--fire-accent:var(--source-yellow);border-color:color-mix(in srgb,var(--source-yellow) 18%,var(--stroke));background:color-mix(in srgb,#c9a04b 7%,var(--surface))}
.fire-phase{display:flex;align-items:center;justify-content:flex-end;gap:5px;text-align:right;border:0;padding:0;max-width:58%;min-height:44px;color:var(--fire-accent)}
.fire-phase svg{width:18px;height:22px;flex-shrink:0;fill:none;stroke:currentColor;stroke-width:1.5}
.fire-phase strong{font-size:13px;font-weight:500;line-height:1.3;overflow-wrap:anywhere}
.fireplace-grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.1fr);gap:14px;align-items:start;margin:6px 0 0}
.fireplace-grid>div{min-width:0}
.fireplace-grid .eyebrow{font-size:11px;letter-spacing:.5px}
.fire-temperature{display:block;font-size:22px;font-weight:500;line-height:1.3;margin-top:4px}
.fire-temperature small{display:block;font-size:11px;font-weight:400;margin-top:2px}
.circulation-block{border-left:1px solid var(--stroke);padding-left:14px}
.fan-actual{display:block;font-size:17px;font-weight:500;line-height:1.4;margin-top:4px}
.fan-reason{font-size:12px;margin:8px 0;overflow-wrap:anywhere}
.fan-level{font-size:11px;margin:2px 0 0}
.fan-waiting{color:var(--source-yellow);font-size:12px;margin:2px 0 0}
.fire-details{border-top:1px solid var(--stroke);margin-top:10px}
.fire-details summary,.plan-details summary{min-height:44px;display:flex;align-items:center;cursor:pointer;font-size:12px;color:var(--muted)}
.fire-details summary::after,.plan-details summary::after{content:'＋';margin-left:auto}
.fire-details[open] summary::after,.plan-details[open] summary::after{content:'−'}

.overview .section-meta{margin:6px 0 10px;gap:8px}
.overview .section-meta .pill{font-size:11px}
.overview .legend{margin:10px 0;gap:6px 14px}
.overview .forecast-stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin-top:10px}
.forecast-stats .compact-metric{background:transparent;padding:8px 10px 8px 0;border-radius:0}
.forecast-stats .compact-metric>span{font-size:12px}
.forecast-stats .compact-metric>strong{font-size:18px}
.forecast-warning{margin:8px 0 0;padding:8px 10px;border-radius:10px}
.forecast-help{position:relative;flex-shrink:0}
.forecast-help summary{width:44px;min-height:44px;display:grid;place-items:center;cursor:pointer;color:var(--muted);list-style:none;font-size:19px}
.forecast-help summary::-webkit-details-marker{display:none}
.forecast-help>div{position:absolute;top:44px;right:0;z-index:3;width:min(340px,calc(100cqi - 72px));padding:14px;background:var(--surface);border:1px solid var(--stroke);border-radius:12px;box-shadow:0 8px 30px #0003;font-size:12px}
.forecast-help p{margin:8px 0 0}

.plan-reason{font-size:12px;line-height:1.5;margin:0 0 8px}
.sessions-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,250px),1fr));gap:0 16px}
.overview .session{padding:8px 0}
.session strong{font-size:12px}
.session .muted{font-size:11px}
.plan-morning{display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:4px 12px;font-size:12px;padding:10px 0}
.plan-morning strong{font-weight:500}
.plan-morning small{font-size:11px}
.plan-details{border-top:1px solid var(--stroke)}
.plan-breakdown{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:4px 16px}
.plan-breakdown .compact-metric>strong{font-size:16px}
.overview .empty{padding:8px 0;margin:0;font-size:12px}

@container(max-width:900px){.heat-sources{grid-template-columns:1fr}
}

@container(max-width:700px){.overview .forecast-stats{grid-template-columns:repeat(2,minmax(0,1fr))}
.plan-breakdown{grid-template-columns:repeat(2,minmax(0,1fr))}
}

@container(max-width:600px){.overview .card{padding:14px 16px;margin-bottom:10px}
.heat-sources{gap:10px;margin-bottom:10px}
.heat-sources>.card{margin:0}
.overview h2{font-size:15px;margin-bottom:10px}
.overview .hero{padding:16px;gap:12px}
.overview .hero-side{padding-left:12px}
.overview .hero-side h2{margin:6px 0;font-size:14px}
.overview .hero p{font-size:12px;line-height:1.4}
.overview .hero-outside{font-size:11px;gap:6px}
.hero-outside strong{font-size:13px}
.overview .comfort{font-size:11px;margin-top:8px}
.overview .comfort strong{font-size:13px}
.overview .hero .eyebrow{font-size:12px;letter-spacing:0}
.boiler-metrics{gap:8px;margin-top:4px}
.boiler-metrics .compact-metric strong{font-size:22px}
.boiler-metrics .compact-metric>span{font-size:11px}
.boiler-note{font-size:11px;margin-top:2px}
.fire-phase strong{font-size:12px}
.fireplace-grid .eyebrow{font-size:12px;letter-spacing:0}
.fire-temperature{font-size:20px}
.fire-temperature small{font-size:11px}
.fan-actual{font-size:15px}
.fire-details{margin-top:8px}
.fire-details summary,.plan-details summary{font-size:11px}
.forecast-stats .compact-metric{padding:8px 0}
.forecast-stats .compact-metric strong{font-size:15px}
.overview .section-meta{font-size:11px}
.overview .section-meta .pill{font-size:11px;padding:3px 8px}
}


.compact-page .page-title{margin:10px 0 14px}
.compact-page h1{font-size:24px}
.compact-page h2{margin-bottom:10px;font-size:15px}
.compact-page .card{padding:16px;margin:0}
.settings-page .settings-grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;align-items:start}
.comfort-settings,.circulation-settings{grid-column:1/-1}
.comfort-groups{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px}
.comfort-groups fieldset{min-width:0;border:0;padding:0;margin:0}
.comfort-groups legend{font-size:11px;color:var(--muted);padding:0}
.control-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:4px 12px;align-items:end}
.control-grid.thirds{grid-template-columns:repeat(3,minmax(0,1fr))}
.compact-page .field{min-width:0;display:flex;flex-direction:column;align-items:stretch;justify-content:flex-start;gap:5px;padding:7px 0;border:0;font-size:12px}
.compact-page .field>span{font-size:12px;line-height:1.4}
.compact-page .field .number{width:100%;min-width:0;gap:5px}
.compact-page .number input{flex:1;min-width:0}
.compact-page .field input:not([type="checkbox"]),.compact-page .field select{width:100%;min-width:0;max-width:none;min-height:44px;font-size:14px;padding:8px 10px}
.compact-page .field:has(.toggle){flex-direction:row;align-items:center;justify-content:space-between;min-height:58px;gap:8px}
.compact-page .field:has(.toggle)>span{font-size:12px}
.compact-page .toggle{flex-shrink:0}
.settings-status{border-top:1px solid var(--stroke);padding-top:6px;margin-top:6px}
.settings-status .row{padding:6px 0;font-size:11px}
.circulation-config{display:grid;grid-template-columns:minmax(240px,1fr) minmax(0,1.4fr);gap:20px;align-items:end}
.inline-help{margin-top:6px;border-top:1px solid var(--stroke)}
.inline-help summary,.model-advanced summary{min-height:44px;display:flex;align-items:center;justify-content:space-between;cursor:pointer;color:var(--muted);font-size:12px;gap:8px}
.inline-help summary::after,.model-advanced summary::after{content:'＋';margin-left:auto}
.inline-help[open] summary::after,.model-advanced details[open] summary::after{content:'−'}
.inline-help p{font-size:11px;margin:6px 0;line-height:1.6}

.model-stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-bottom:12px!important}
.model-stats .compact-metric{padding:4px 0}
.model-stats .compact-metric strong{font-size:21px}
.model-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;align-items:start}
.model-grid>.card{min-width:0}
.model-grid .ai-report{grid-column:1/-1}
.report-text,.learning-result{font-size:13px;line-height:1.6;margin:0 0 6px;overflow-wrap:anywhere}
.report-meta,.model-meta{font-size:11px;margin:4px 0 8px}
.report-stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}
.fact-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:4px 16px}
.model-page .compact-metric>span{font-size:11px}
.model-page .compact-metric>strong{font-size:16px;margin-top:3px}
.model-page .compact-metric small{font-size:11px}
.model-page .model-stats strong{font-size:20px}
.model-page .compact-metric{padding:6px 0}
.model-page td,.model-page th{padding:7px 6px;font-size:12px}
.model-page th{font-size:11px}
.model-page .journal{max-height:320px}
.model-page .entry{grid-template-columns:80px minmax(0,1fr);gap:10px;padding:8px 0}
.model-page .entry time{font-size:11px}
.model-page .entry p{font-size:12px}
.action-buttons{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
.action-buttons .field>span{display:none}
.action-buttons .field{padding:4px 0}
.action-buttons .field .action{font-size:12px;width:100%;padding:8px;min-height:44px}
.model-advanced summary{color:var(--primary-text-color);font-size:13px;font-weight:500}
.parameter-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 16px}
.parameter-grid .row{min-width:0;padding:7px 0;font-size:11px}
.parameter-grid .row>span{overflow-wrap:anywhere}
.controller-facts{grid-template-columns:repeat(3,minmax(0,1fr));border-top:1px solid var(--stroke);margin-top:10px;padding-top:6px}
.model-advanced .note{margin:8px 0 0}
.model-page .empty{padding:10px 0;margin:0}

@container(max-width:900px){.settings-page .settings-grid{grid-template-columns:1fr}
.circulation-config{grid-template-columns:1fr;gap:4px}
.model-grid{grid-template-columns:repeat(2,minmax(0,1fr))}
}

@container(max-width:700px){.comfort-groups{grid-template-columns:1fr;gap:14px}
.model-grid{grid-template-columns:1fr}
.model-stats{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px 16px}
.model-grid .ai-report{grid-column:auto}
}

@container(max-width:600px){.compact-page .page-title{margin:4px 0 12px}
.compact-page h1{font-size:22px}
.compact-page .card{padding:14px 16px}
.settings-page .settings-grid,.model-grid{gap:10px}
.control-grid{gap:3px 10px}
.control-grid.thirds{gap:3px 8px}
.compact-page .field{padding:6px 0}
.compact-page .field>span{font-size:12px}
.compact-page .field input:not([type="checkbox"]),.compact-page .field select{font-size:12px}
.compact-page .field:has(.toggle)>span{font-size:11px}
.circulation-levels .field>span{min-height:28px}
.fact-grid{gap:2px 12px}
.report-stats{gap:8px}
.report-stats .compact-metric strong{font-size:14px}
.model-page .model-stats strong{font-size:18px}
.model-page .compact-metric>strong{font-size:15px}
.action-buttons{gap:8px}
.model-page .entry{grid-template-columns:72px minmax(0,1fr)}
.inline-help summary{font-size:11px}
}


.system-settings{margin-top:14px!important}
.system-settings>summary,.system-group>summary{min-height:48px;display:flex;align-items:center;justify-content:space-between;cursor:pointer;font-weight:600;gap:10px}
.system-settings[open]>summary>span{transform:rotate(45deg)}
.system-body{border-top:1px solid var(--stroke);padding-top:6px}
.system-group{border-bottom:1px solid var(--stroke)}
.system-group>summary{font-size:13px;font-weight:500}
.system-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px 18px;padding:6px 0 16px}
.system-field{min-width:0;display:flex;flex-direction:column;gap:5px}
.system-label{display:flex;align-items:center;justify-content:space-between;gap:6px;font-size:12px;line-height:1.4}
.system-help{flex-shrink:0;border:0;color:var(--green);min-width:44px;min-height:44px;padding:8px;font-size:15px}
.system-field input{width:100%;min-width:0;min-height:44px;border:1px solid var(--control-stroke);border-radius:12px;background:var(--raised);color:inherit;padding:8px 10px;font:inherit}
.system-field .number{width:100%;min-width:0}
.system-field .number input{flex:1;width:0}
.system-field .number span{white-space:nowrap}
.system-entity{text-align:left;padding:8px 10px;font-size:13px;overflow-wrap:anywhere}
.system-entity small{color:var(--muted)}
.system-fixed{display:flex;align-items:center;min-height:44px;color:var(--muted);font-size:13px}
.system-actions{display:flex;flex-wrap:wrap;align-items:center;justify-content:flex-end;gap:8px;padding-top:12px}
.system-message{flex:1 1 180px;font-size:12px;color:var(--muted)}
.system-dialog{position:fixed;width:min(92vw,560px);max-height:85vh;overflow:auto;border:1px solid var(--stroke);border-radius:var(--card-radius);padding:20px;background:var(--surface);color:var(--primary-text-color,#e8e8e8);box-shadow:0 12px 48px #0005;font:inherit}
.system-dialog::backdrop{background:#0008}
.system-dialog-heading{display:flex;gap:12px;align-items:center;justify-content:space-between}
.system-dialog h2{margin:0;font-size:17px}
.system-dialog p{font-size:13px;line-height:1.65;overflow-wrap:anywhere}
.system-dialog button{min-width:44px;min-height:44px;font-size:22px}
.system-dialog-heading button{flex-shrink:0}
@container(max-width:900px){.system-grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:10px 12px}
}
@container(max-width:430px){.system-grid{grid-template-columns:1fr}
.system-settings>summary{font-size:14px}
.system-label{font-size:12px}
.system-actions button{flex:1 1 120px}
.system-dialog{padding:16px}
}



/* One spacing rhythm and a common touch/focus vocabulary on all three tabs. */
.icon{width:20px;height:20px;display:block;fill:none;stroke:currentColor;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round;flex:none}

.brand-icon .icon{width:30px;height:30px}

.menu-button,.history-refresh{display:grid;place-items:center}

.hero .eyebrow{letter-spacing:0;text-transform:none;font-weight:400}
.fireplace-grid .eyebrow{text-transform:none;letter-spacing:0}

.hero h1.eyebrow{font-size:14px}

.hero-side .comfort{font-size:12px}

.boiler-status::before{content:'';display:inline-block;width:6px;height:6px;border:1px solid currentColor;border-radius:50%;margin-right:6px;vertical-align:middle}

.boiler-card[data-heating="true"] .boiler-status::before{background:currentColor}

.boiler-metrics .compact-metric+.compact-metric{border-left:1px solid var(--stroke);padding-left:12px;border-radius:0}

.boiler-note{font-size:12px}

.overview .forecast-stats{border-top:1px solid var(--stroke);padding-top:4px}

.forecast-help>div{transform-origin:top right;line-height:1.6}

.forecast-help summary:focus-visible,summary:focus-visible{outline:2px solid var(--green);outline-offset:3px;border-radius:6px}

.forecast-help summary{list-style:none}

.system-help{color:var(--muted);border:1px solid var(--control-stroke);border-radius:50%;padding:0;min-width:32px;min-height:32px;box-shadow:0 0 0 6px transparent;margin:6px}

.system-actions button{min-height:44px}

[data-system-save]:not(:disabled){background:var(--primary-text-color,#e8e8e8);color:var(--surface);border-color:transparent;font-weight:500}

.field input:disabled,.field select:disabled,.system-field input:disabled{color:var(--muted);opacity:1;-webkit-text-fill-color:var(--muted)}

.toggle:has(input:disabled){opacity:.65}
.toggle:has(input:focus-visible) span:after{transition:none}

.table-scroll,.journal{scrollbar-width:thin;scrollbar-color:var(--control-stroke) transparent}

::selection{background:color-mix(in srgb,var(--green) 28%,transparent);color:var(--primary-text-color,#e8e8e8)}

progress::-moz-progress-bar{background:var(--green)}

/* No entrances or chart animation on recurring telemetry updates. */
@media(hover:hover) and (pointer:fine){
  button:not(:disabled):hover{background:var(--raised)}

  .hero-outside:hover,.fire-phase:hover{background:transparent;color:var(--primary-text-color,#e8e8e8)}

  button:not(:disabled):not(:focus-visible):active{transform:scale(.98)}

}

@media(pointer:coarse){button:not(:disabled):not(:focus-visible):active{transform:scale(.98)}
}

@media(prefers-reduced-motion:reduce){*,*::before,*::after{transition:none!important;animation:none!important}
}

@container(max-width:600px){
  main{padding:8px 12px}

  header{padding:4px 0 10px}

  .brand{gap:8px}
.brand small{font-size:11px}
.header-status{font-size:11px;gap:6px}

  nav{margin-bottom:12px;padding-bottom:6px;gap:2px}

  nav button{flex:1;min-height:44px;padding:9px 6px;font-size:13px}

  .overview .hero{gap:12px}
.overview .hero .eyebrow{font-size:11px}

  .overview .hero h1.eyebrow{font-size:12px}

  .hero-side h2{overflow-wrap:anywhere}
.overview .comfort{font-size:11px}

  .hero-side .comfort strong{font-size:14px}

  .boiler-metrics .compact-metric+.compact-metric{padding-left:10px}

  .boiler-note{font-size:11px}

  .compact-metric>span,.history-legend button>span{font-size:12px}

  .overview .section-meta{font-size:11px;align-items:flex-start}

  .section-meta>.muted{text-align:right}

  .forecast-stats .compact-metric strong{font-size:17px}

  .forecast-stats .compact-metric>span{font-size:12px}

  .forecast-warning{font-size:12px}

  .history-legend{gap:6px}
.history-legend button{padding:7px 6px}

  .history-legend strong{font-size:18px}

  .system-help{min-width:44px;min-height:44px;margin:0}

  .system-field{gap:4px}
  .compact-page .field input:not([type="checkbox"]),.compact-page .field select,.system-field input{font-size:16px}

}

`;
if(!customElements.get('heating-ai-panel'))customElements.define('heating-ai-panel',HeatingAIPanel);
window.customCards=window.customCards||[];
if(!window.customCards.some(c=>c.type==='heating-ai-panel'))window.customCards.push({type:'heating-ai-panel',name:'Heating AI',description:'Клімат: огляд, комфорт, прогноз і навчання',preview:false});
