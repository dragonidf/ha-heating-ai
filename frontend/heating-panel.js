/* Heating AI — native Home Assistant panel/card. No tokens or external services. */
const VERSION = '0.2.2';
export const escapeHTML = value => String(value ?? '—').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export const numeric = value => value !== null && value !== undefined && String(value).trim() !== '' && Number.isFinite(Number(value)) ? Number(value) : null;
export const isTrue = value => value === true || value === 'true' || value === 'on';
export const mergeSegments = rows => (Array.isArray(rows) ? rows : []).filter(r => numeric(r.start) !== null && numeric(r.end) !== null && r.end > r.start).sort((a,b)=>a.start-b.start).reduce((out,row) => {
  const last = out.at(-1);
  if (last && row.start <= last.end + 1 && isTrue(row.conditional) === isTrue(last.conditional)) last.end = Math.max(last.end,row.end);
  else out.push({...row});
  return out;
}, []);
const h = escapeHTML;
const fmt = (v,d=1) => numeric(v) === null ? '—' : Number(v).toLocaleString('uk-UA',{maximumFractionDigits:d,minimumFractionDigits:d});
export const chartLayout = width => {
  const w=Math.max(1,Math.round(numeric(width)??880)), height=w<520?320:340;
  return {width:w,height,left:46,right:12,top:24,bottom:height-96,tickIntervals:w<520?2:4};
};
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
    const keep=name=>editing&&['value','checked','selected'].includes(name);
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
  heartbeat:'sensor.heating_heartbeat', temperature:'sensor.heating_temperature', power_confirmed:'sensor.heating_power_confirmed',
  grid_status:'sensor.heating_grid', fireplace_status:'sensor.heating_fireplace', fireplace_active:'binary_sensor.heating_fireplace_active',
  energy_24h:'sensor.heating_energy_24h', energy_monthly:'sensor.heating_energy_monthly', forecast:'sensor.heating_forecast',
  outages:'sensor.heating_outage_schedule', model:'sensor.heating_model', environment:'sensor.heating_environment', summary:'sensor.heating_ai_summary',
  restore_status:'sensor.heating_restore_status', fan_status:'sensor.heating_fan',
  target:'input_number.heating_target',lower:'input_number.heating_lower',upper:'input_number.heating_upper',
  night_target:'input_number.heating_night_target',night_lower:'input_number.heating_night_lower',night_upper:'input_number.heating_night_upper',
  sleep_start:'input_datetime.heating_sleep_start',sleep_end:'input_datetime.heating_sleep_end',start:'input_datetime.heating_start',end:'input_datetime.heating_end',
  monthly_budget:'input_number.heating_monthly_budget',session_limit:'input_number.heating_session_limit',native_setpoint:'input_number.heating_native_setpoint',
  mode:'input_select.heating_mode',strategy:'input_select.heating_strategy',armed:'input_boolean.heating_armed',restore_once:'input_boolean.heating_restore_once',
  fan_control:'input_boolean.heating_fan_control',fan_mode:'input_select.heating_fan_mode',fan_speed:'input_number.heating_fan_speed',fan_night_limit:'input_number.heating_fan_night_limit',
  ai_auto_apply:'input_boolean.heating_ai_auto_apply',rollback:'input_button.heating_model_rollback',weather_refresh:'input_button.heating_forecast_refresh'
};
export class HeatingAIPanel extends HTMLElement {
  constructor() {
    super(); this.attachShadow({mode:'open'}); this.tab='overview'; this.config={}; this.nativeCards=new Map(); this.pending=new Set();
    this.shadowRoot.addEventListener('click',e=>this.clicked(e));
    this.shadowRoot.addEventListener('change',e=>this.changed(e));
    this.shadowRoot.addEventListener('focusout',()=>setTimeout(()=>this.render(),0));
  }
  setConfig(config) { this.config=config || {}; this.nativeCards.clear(); this.render(); }
  set panel(panel) { this.setConfig(panel?.config || {}); }
  set hass(value) {
    const previous=this._hass;this._hass=value;
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
    cancelAnimationFrame(this.resizeFrame);this.resizeFrame=0;this.observedPlot=null;
  }
  observePlot() {
    const plot=this.shadowRoot.querySelector('.forecast-plot');
    if(plot===this.observedPlot)return;
    if(this.observedPlot)this.resizeObserver?.unobserve(this.observedPlot);
    this.observedPlot=plot;
    if(plot)this.resizeObserver?.observe(plot);
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
    let svg=`<div class="forecast-plot"><svg width="${g.width}" height="${g.height}" viewBox="0 0 ${g.width} ${g.height}" role="img" aria-label="Прогноз температури, комфортні межі, нагрів і відключення на 24 години"><defs><linearGradient id="curve-fill" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#69d5b2" stop-opacity=".2"/><stop offset="1" stop-color="#69d5b2" stop-opacity="0"/></linearGradient></defs>`;
    let prev=start;
    for(const r of rows) {
      if(numeric(r.lower)!==null&&numeric(r.upper)!==null) svg+=`<rect x="${x(prev)}" y="${y(r.upper)}" width="${Math.max(0,x(r.ts)-x(prev))}" height="${Math.max(0,y(r.lower)-y(r.upper))}" fill="#69d5b2" opacity=".09"/>`;
      prev=r.ts;
    }
    const known=numeric(a.schedule_known_until);
    if(known!==null&&known<end) svg+=`<rect x="${x(known)}" y="${g.top}" width="${Math.max(0,right-x(known))}" height="${g.bottom+54-g.top}" fill="#ffc26d" opacity=".07"/>`;
    for(let i=0;i<4;i++) { const t=low+(high-low)*i/3; svg+=`<path d="M${g.left} ${y(t)}H${right}" stroke="currentColor" opacity=".08"/><text x="0" y="${y(t)+4}" fill="currentColor" font-size="12">${fmt(t)}°</text>`; }
    const path=rows.map((r,i)=>`${i?'L':'M'}${x(r.ts).toFixed(2)},${y(r.temperature).toFixed(2)}`).join(' ');
    svg+=`<path d="${path} L${x(rows.at(-1).ts)},${g.bottom+8} L${x(rows[0].ts)},${g.bottom+8} Z" fill="url(#curve-fill)"/><path d="${path}" fill="none" stroke="#69d5b2" stroke-width="3" stroke-dasharray="5 4" vector-effect="non-scaling-stroke"/>`;
    for(const r of rows) svg+=`<circle cx="${x(r.ts)}" cy="${y(r.temperature)}" r="6" fill="transparent"><title>${h(this.time(r.ts))} · ${fmt(r.temperature)} °C</title></circle>`;
    svg+=`<text x="0" y="${g.bottom+35}" fill="currentColor" font-size="11">Нагрів</text><text x="0" y="${g.bottom+60}" fill="currentColor" font-size="11">Мережа</text>`;
    for(const s of mergeSegments(a.heat_segments)) if(s.end>start&&s.start<end) svg+=`<rect x="${x(s.start)}" y="${g.bottom+22}" width="${Math.max(1,x(s.end)-x(s.start))}" height="15" rx="4" fill="#ffc26d" opacity="${isTrue(s.conditional)?.45:1}"><title>${h(this.time(s.start))}–${h(this.time(s.end))}${isTrue(s.conditional)?' · умовно':''}</title></rect>`;
    for(const s of mergeSegments(this.attrs('outages').outages)) if(s.end>start&&s.start<end) svg+=`<rect x="${x(s.start)}" y="${g.bottom+47}" width="${Math.max(1,x(s.end)-x(s.start))}" height="15" rx="4" fill="#f28d9a"><title>Відключення ${h(this.time(s.start))}–${h(this.time(s.end))}</title></rect>`;
    for(let i=0;i<=g.tickIntervals;i++) svg+=`<text x="${x(start+i*86400/g.tickIntervals)}" y="${g.height-8}" text-anchor="${i===0?'start':i===g.tickIntervals?'end':'middle'}" fill="currentColor" font-size="12">${h(this.time(start+i*86400/g.tickIntervals))}</text>`;
    return svg+'</svg></div><div class="legend"><span>● Прогноз</span><span>● Нагрів</span><span>● Відключення</span><span>Тло — комфортний діапазон</span></div>';
  }
  fireplaceOverview() {
    const phase=this.state('fireplace_status'), fire=this.attrs('fireplace_status'), fan=this.attrs('fan_status');
    const valid=!['unknown','unavailable','Датчик недоступний'].includes(phase);
    const active=valid&&this.state('fireplace_active')==='on', running=this.state('fan')==='on';
    const requested=isTrue(fan.requested), waiting=requested&&this.state('fan')==='off';
    const signed=value=>numeric(value)===null?'—':(Number(value)>0?'+':'')+fmt(value);
    const reason=fan.reason || (!['unknown','unavailable'].includes(this.state('fan_status'))?this.state('fan_status'):'Очікуємо даних про циркуляцію');
    const measure=(title,value,unit)=>`<div><span class="muted">${h(title)}</span><strong>${h(value)} <small>${h(unit)}</small></strong></div>`;
    return this.section('Камін і циркуляція тепла',`<div class="fireplace-grid"><div class="fire-block" data-active="${active}"><button class="fire-phase" data-info="${h(this.id('fireplace_status'))}" aria-label="Подробиці стану каміна"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M13 2c1 5-5 6-3 11 1-1 2-2 2-4 4 3 6 6 4 9-2 4-9 3-10-1-2-5 2-8 2-11 1 1 2 2 2 3 1-3 3-4 3-7Z"/></svg><strong>${h(valid?phase:phase==='Датчик недоступний'?phase:'Немає даних')}</strong></button><span class="fire-heat muted">Корисне тепло: ${active?'виявлено':valid&&this.state('fireplace_active')==='off'?'не виявлено':'немає даних'}</span><div class="fire-measures">${measure('Датчик каміна',fmt(valid?fire.temperature:null),'°C')}${measure('Понад кімнатну',signed(valid?fire.delta:null),'°C')}${measure('Зміна різниці',signed(valid?fire.trend:null),'°C/год')}</div></div><div class="circulation-block"><span class="eyebrow">ЦИРКУЛЯЦІЯ ПОВІТРЯ</span><strong class="fan-actual">${running?`Увімкнено · ${fmt(this.attrs('fan').percentage,0)} %`:this.state('fan')==='off'?'Вимкнено':'Немає даних'}</strong><p class="fan-reason ${waiting?'fan-waiting':''}">${h(reason)}</p><div class="fan-details">${this.row('Режим',this.entity('fan_mode')?this.state('fan_mode'):'—')}${this.row('Керування',fan.control_enabled===undefined?'—':isTrue(fan.control_enabled)?'Увімкнене':'Вимкнене')}${this.row('Швидкість за планом',fmt(fan.proposed_percentage,0)+' %')}${this.row('Нічне обмеження',fmt(this.state('fan_night_limit'),0)+' %')}</div></div></div><div class="fire-footer"><span class="muted">${requested?'Є запит на циркуляцію.':'requested' in fan?'Запиту на циркуляцію немає.':'Очікуємо даних про запит.'} ${valid?'Температурний сигнал каміна враховується в моделі.':'Очікуємо актуальних вимірювань каміна.'}</span><button class="text-button" data-tab="settings" data-settings-target="fireplace-settings">Налаштувати циркуляцію ↗</button></div>`, 'fireplace-card');
  }
  overview() {
    const c=this.attrs('controller'), f=this.attrs('forecast'), s=this.attrs('summary').last_24h||{}, o=this.attrs('outages');
    const heating=numeric(this.state('power_confirmed'))!==null&&Number(this.state('power_confirmed'))>1500;
    const segments=mergeSegments(f.heat_segments), budget=numeric(this.state('monthly_budget')), used=numeric(this.state('energy_monthly'));
    const morningMinutes=Array.isArray(f.morning_runs_minutes)?f.morning_runs_minutes.reduce((sum,v)=>sum+(numeric(v)??0),0):null;
    const stale=!Number.isFinite(Date.parse(f.created_at)) || Date.now()-Date.parse(f.created_at)>660000;
    return `<div class="hero card"><div><span class="eyebrow">КОМФОРТ У ВАШОМУ ДОМІ</span><div class="temperature">${fmt(this.state('temperature'))}<span>°C</span></div><p>${h(c.comfort_zone || 'Поточний комфорт')} · ціль ${fmt(c.target_temperature)} °C</p><div class="pill">${heating?'◉ Нагрів':!isTrue(c.quality)?'◌ Очікуємо даних':'◌ Очікування'}</div></div><div class="hero-side"><span class="eyebrow">РІШЕННЯ КОНТРОЛЕРА</span><h2>${h(c.reason || 'Очікуємо даних')}</h2><p class="muted">${h(c.proposed_action || '')}</p><div class="comfort">Межі комфорту <strong>${fmt(c.lower_temperature)}–${fmt(c.upper_temperature)} °C</strong></div></div></div>${this.fireplaceOverview()}
    <div class="metrics">${this.metric('Нагрів зараз',numeric(this.state('power_confirmed'))===null?'—':fmt(Number(this.state('power_confirmed'))/1000),'кВт','Підтверджена потужність','power_confirmed')}${this.metric('Обратка',fmt(this.state('return')),'°C','Показник запасу тепла','return')}${this.metric('Надворі',fmt(this.state('outside')),'°C','Місцевий датчик','outside')}${this.metric('За останні 24 години',fmt(this.state('energy_24h'),2),'кВт·год',`${fmt(s.temperature_min)}–${fmt(s.temperature_max)} °C у кімнаті`,'energy_24h')}</div>
    <div class="columns"><div>${this.section('Наступні 24 години',`<div class="section-meta"><span class="pill ${isTrue(f.comfort_shortfall)?'warn':''}">${stale?'Прогноз застарів':isTrue(f.comfort_shortfall)?'Ризик зниження температури':'План утримує комфорт'}</span><span class="muted">Оновлено ${h(this.time(f.created_at))}</span></div>${this.forecastChart()}<div class="forecast-stats">${this.row('Мінімальна температура',fmt(this.state('forecast'))+' °C')}${this.row('Очікувана енергія',fmt(f.planned_kwh,2)+' кВт·год')}${this.row('Горизонт прогнозу',fmt(f.horizon_hours,0)+' год')}${this.row('Графік мережі відомий до',this.time(o.known_until,true))}</div><p class="muted note">Прогноз — оцінка. Після кінця графіка мережі нагрів умовний; перед виконанням контролер перевіряє фактичну мережу.${!isTrue(f.weather_complete)?' Погодинна погода неповна: далі використано останню відому температуру.':''}</p>`, 'chart-card')}
    ${this.section('Температура · остання доба','<div class="native" data-native="history"></div>')}
    ${this.section('Енергія котла по годинах','<div class="native" data-native="energy"></div>')}
    ${this.section('Мережа й камін · остання доба','<div class="native" data-native="grid"></div>')}</div>
    <div>${this.section('План нагріву',`<p class="muted">${h(f.split_reason || 'Контролер розраховує оптимальні інтервали.')}</p>${segments.map(s=>`<div class="session"><div><strong>${h(this.time(s.start,true))} → ${h(this.time(s.end))}</strong><span class="muted">${fmt((s.end-s.start)/60,0)} хв${isTrue(s.conditional)?' · умовний інтервал':''}</span></div><span class="heat-dot"></span></div>`).join('')||'<p class="empty">Нагрів не запланований.</p>'}${this.row('Вечір',fmt(f.session_kwh?.evening,2)+' кВт·год')}${this.row('Ранок',fmt(f.session_kwh?.morning,2)+' кВт·год')}${this.row('Інші інтервали',fmt(f.session_kwh?.other,2)+' кВт·год')}${this.row('Умовний нагрів',fmt(f.conditional_heat_minutes,0)+' хв')}${this.row('За межами дозволених годин',fmt(f.outside_minutes,0)+' хв')}${this.row('Перенесено через відключення',isTrue(f.outage_shifted)?'Так':'Ні')}<div class="inset"><strong>Ранковий прогрів</strong>${this.row('Заплановано',fmt(morningMinutes,0)+' хв')}${this.row('Мінімум',fmt(f.morning_min_minutes,0)+' хв')}<p class="muted">${h(stale?'План застарів: потрібен новий розрахунок.':morningMinutes?'Ранковий прогрів зарезервовано.':f.split_reason || 'Ранкового нагріву немає.')}</p></div>`)}
    ${this.section('Енергія за місяць',`<div class="large">${fmt(used,1)} <small>кВт·год</small></div><progress max="${budget!==null&&budget>0?budget:1}" value="${used??0}" aria-label="Місячна енергія"></progress><p class="muted">Орієнтир ${fmt(budget,0)} кВт·год${budget&&used!==null?' · '+fmt(used/budget*100,0)+' %':''}</p>`)}
    ${this.section('Погода на наступну добу','<div class="native" data-native="weather"></div>'+this.field('weather_refresh','Оновити погодинну погоду'))}</div></div>`;
  }
  settings() {
    const group=(title,fields,description='',id='')=>this.section(title,`${description?'<p class="muted">'+h(description)+'</p>':''}${fields.map(([k,l])=>this.field(k,l)).join('')}`,'',id);
    return `<div class="page-title"><h1>Ваш комфорт, ваші правила</h1><p class="muted">Зміни зберігаються у чинних налаштуваннях Home Assistant.</p></div><div class="settings-grid">${group('Денний комфорт',[['target','Бажана температура'],['lower','Нижня межа'],['upper','Верхня межа']])}${group('Комфорт під час сну',[['sleep_start','Початок сну'],['sleep_end','Кінець сну'],['night_target','Бажана температура'],['night_lower','Нижня межа'],['night_upper','Верхня межа']],'Час сну задає нічний комфорт окремо від тарифу й годин нагріву.')}${group('Розклад і витрати',[['strategy','Планування нагріву'],['start','Початок дозволених годин'],['end','Кінець дозволених годин'],['monthly_budget','Місячний орієнтир'],['session_limit','Ліміт одноразового нагріву']])}${group('Керування опаленням',[['mode','Режим системи'],['armed','Дозволити автоматичні команди'],['native_setpoint','Уставка штатного резерву'],['restore_once','Один нагрів після появи мережі']],'Для повернення до розкладу термостата оберіть «Штатний auto». Ручна зміна термостата може призупинити модель.')}${group('Розподіл тепла каміна',[['fan_control','Дозволити керування вентилятором'],['fan_mode','Режим циркуляції'],['fan_speed','Звичайна швидкість'],['fan_night_limit','Нічне обмеження']],'','fireplace-settings')}${this.section('Поточний стан',`${this.row('Одноразовий запит',this.state('restore_status'))}${this.row('Команди контролера',this.state('permitted')==='on'?'Дозволені':'Не дозволені')}${this.row('Контролер',this.state('controller'))}<p class="muted">Перед ранком система планує основне накопичення тепла для дня. До того підтримує нічний комфорт. Межі температури й фактична мережа мають пріоритет.</p>`)}</div>`;
  }
  model() {
    const a=this.attrs('model'), env=this.attrs('environment'), p=a.power_learning||{}, s=this.attrs('summary').last_24h||{}, f=this.attrs('forecast');
    const updated=this.entity('summary')?.last_updated;
    return `<div class="page-title"><h1>Будинок, який вчиться</h1><p class="muted">Точність прогнозу, досвід котла та пояснення кожного рішення.</p></div><div class="metrics">${this.metric('Модель',this.state('model'),'','Версія '+(a.parameters?.version??'—'),'model')}${this.metric('Годинна похибка',fmt(a.hour_mae,3),'°C','MAE прогнозу','model')}${this.metric('Навчання',fmt(a.usable_pairs,0),'інтервалів','Придатні вимірювання','model')}${this.metric('Запас тепла',fmt(this.state('environment')),'°C','Розрахунковий еквівалент','environment')}</div><div class="columns"><div>
    ${this.section('Навчання модуляції котла',`<p class="muted">${h(a.power_learning_status || '')} · ${fmt(p.intervals,0)} інтервалів · ${fmt(p.minutes,0)} хв</p><div class="table-scroll"><table><thead><tr><th>Обратка</th><th>Потужність</th><th>Досвід</th></tr></thead><tbody>${(p.bands||[]).map(b=>`<tr><td>${fmt(b.return_min,0)}–${fmt(b.return_max,0)} °C</td><td>${fmt(b.measured_kw)} кВт</td><td>${fmt(b.minutes,0)} хв</td></tr>`).join('')||'<tr><td colspan="3">Очікуємо підтверджених сеансів нагріву.</td></tr>'}</tbody></table></div><p class="muted note">Прогноз враховує фактичну енергію та модуляцію. Для невивчених температур початкова оцінка ${fmt(f.power_prior_kw)} кВт. Короткі спостереження мають меншу вагу; це ще не точні пороги перемикання ТЕНів.</p>`)}
    ${this.section('Чому система приймає ці рішення',`<div class="journal">${[...(a.journal||[])].reverse().map(r=>`<div class="entry"><time>${h(this.time(r.time,true))}</time><p>${h(r.reason)}</p></div>`).join('')||'<p class="empty">Журнал ще порожній.</p>'}</div>`)}
    ${this.section('Параметри моделі',Object.entries(a.parameters||{}).map(([k,v])=>this.row(k,typeof v==='number'?fmt(v,4):v)).join(''))}</div><div>
    ${this.section('Останній результат навчання',`<p>${h(a.last_result || 'Навчання ще не виконувалось')}</p>${this.row('Останній аналіз',this.time(a.last_analysis,true))}${this.row('Кількість проб',fmt(a.samples,0))}`)}
    ${this.section('Підлога, погода й точність',`${this.row('Оцінка запасу тепла',fmt(this.state('environment'))+' °C')}${this.row('Виміряна обратка',fmt(env.raw_return)+' °C')}${this.row('Зовнішній прогрів',fmt(env.external_gain_c_per_hour,3)+' °C/год')}${this.row('Сонячна складова',isTrue(env.solar_validated)?'Перевірена':'Навчається')}${this.row('Днів спостережень',fmt(env.solar_learning_days,0))}${this.row('Поточна похибка',fmt(a.reliability?.recent_mae,2)+' °C')}${this.row('Корекція місцевої погоди',fmt(f.local_weather_bias)+' °C')}${isTrue(a.reliability?.degraded)?`<p class="warn pill">Точність знижена · запас до верхньої межі ${fmt(env.upper_margin)} °C</p>`:''}<p class="muted note">Це еквівалент запасу тепла, а не температура поверхні підлоги. Холодна труба без циркуляції не обнуляє запас. Неврахований прогрів сам по собі не доводить сонячний внесок.</p>`)}
    ${this.section('Висновок за останню добу',`<p>${h(this.state('summary'))}</p>${this.row('Оновлено',this.time(updated,true))}${!Number.isFinite(Date.parse(updated))||Date.now()-Date.parse(updated)>660000?'<p class="warn pill">Дані звіту застаріли</p>':''}${this.row('Охоплення',fmt(s.hours_collected)+' год')}${this.row('Придатні проби',`${fmt(s.valid_samples,0)} / ${fmt(s.samples,0)}`)}${this.row('Якість проб',numeric(s.coverage)===null?'—':fmt(s.coverage*100,0)+' %')}<p class="muted note">Автоматичний підсумок вимірювань. Частка придатних проб не гарантує відсутності часових прогалин.</p>`)}
    ${this.section('Застосування і відкат',this.field('ai_auto_apply','Застосовувати перевірені пропозиції')+this.field('rollback','Попередня модель'))}
    ${this.section('Погода та прогноз',this.field('weather_refresh','Оновити погодинну погоду')+this.row('Погодинні дані відомі до',this.time(f.weather_known_until,true))+'<p class="muted note">Погода оновлюється автоматично щогодини. Результат ручного запиту — у сповіщеннях Home Assistant.</p>')}
    ${this.section('Стан контролера',`${this.row('Зв’язок',this.state('alive')==='on'?'Є':'Не підтверджений')}${this.row('Команди дозволені',this.state('permitted')==='on'?'Так':'Ні')}${this.row('Останній цикл',this.time(this.state('heartbeat'),true))}<a href="https://github.com/dragonidf/ha-heating-ai" target="_blank" rel="noopener noreferrer">Heating AI ${VERSION} ↗</a>`)}</div></div>`;
  }
  render() {
    if(!this.hass) return;
    const beat=Date.parse(this.state('heartbeat')), alive=this.state('alive')==='on'&&Number.isFinite(beat)&&Date.now()-beat<90000;
    const quality=isTrue(this.attrs('controller').quality), grid=this.attrs('grid_status').raw;
    const html=`<style>${styles}</style><main><header><div class="brand"><span class="brand-icon">⌂</span><div><strong>Heating <span>AI</span></strong><small>Клімат вашого дому</small></div></div><div class="header-status"><span class="dot ${alive?'':'red'}"></span>${alive?'Контролер онлайн':'Перевірте контролер'}<span class="mode">${h(this.state('mode'))}</span><button class="menu-button" data-menu aria-label="Відкрити меню Home Assistant">☰</button></div></header><nav aria-label="Розділи клімату">${[['overview','Огляд'],['settings','Налаштування'],['model','Модель і ШІ']].map(([key,label])=>`<button data-tab="${key}" aria-current="${this.tab===key?'page':'false'}" class="${this.tab===key?'selected':''}">${label}</button>`).join('')}<a href="/climate-control">Поточні панелі ↗</a></nav><div class="notice ${this.error?'':'hidden'}" role="alert">${h(this.error || '')}</div><div class="notice ${alive&&quality&&grid==='on'?'hidden':''}" role="status">${!alive?'Немає свіжого підтвердження роботи контролера.':!quality?'Контролеру бракує актуальних вимірювань.':'Нагрів очікує підтвердження електромережі.'}</div><div class="content">${this.tab==='settings'?this.settings():this.tab==='model'?this.model():this.overview()}</div><footer>Тепло під контролем <span>Home Assistant · Heating AI ${VERSION}</span></footer></main>`;
    const template=document.createElement('template');template.innerHTML=html;
    patchDOM(this.shadowRoot,template.content);
    this.observePlot();
    this.mountNative();
  }
  async mountNative() {
    const slots=[...this.shadowRoot.querySelectorAll('[data-native]')];
    if(!slots.length)return;
    try {
      this.helpers ||= await (this.helpersPromise ||= window.loadCardHelpers());
      const ids=keys=>keys.map(key=>this.id(key)).filter(id=>id&&this.hass.states[id]);
      const configs={
        history:{type:'history-graph',hours_to_show:24,entities:ids(['room','return','outside'])},
        energy:{type:'statistics-graph',entities:ids(['energy']),stat_types:['change'],period:'hour',chart_type:'bar',days_to_show:1},
        grid:{type:'history-graph',hours_to_show:24,entities:ids(['grid_status','fireplace_active'])},
        weather:{type:'weather-forecast',entity:this.id('weather'),forecast_type:'hourly',forecast_slots:24,show_current:false,show_forecast:true}
      };
      for(const slot of slots) {
        if(!slot.isConnected)continue;
        const key=slot.dataset.native, config=configs[key];
        if(config.entity&&!this.hass.states[config.entity] || config.entities?.length===0){slot.textContent='Джерело ще не під’єднане до панелі.';continue;}
        if(!this.nativeCards.has(key))this.nativeCards.set(key,this.helpers.createCardElement(config));
        const card=this.nativeCards.get(key);card.hass=this.hass;if(slot.firstElementChild!==card)slot.replaceChildren(card);
      }
    } catch { this.helpersPromise=null;for(const slot of slots)if(slot.isConnected)slot.textContent='Не вдалося завантажити графік Home Assistant. Оновіть сторінку.'; }
  }
  async clicked(e) {
    const el=e.target.closest('[data-tab],[data-info],[data-press],[data-menu]'); if(!el)return;
    if(el.dataset.tab){this.tab=el.dataset.tab;this.error='';this.render();if(el.dataset.settingsTarget)this.shadowRoot.getElementById(el.dataset.settingsTarget)?.scrollIntoView({block:'start'});return;}
    if(el.hasAttribute('data-menu')){this.dispatchEvent(new CustomEvent('hass-toggle-menu',{bubbles:true,composed:true}));return;}
    if(el.dataset.info){this.dispatchEvent(new CustomEvent('hass-more-info',{detail:{entityId:el.dataset.info},bubbles:true,composed:true}));return;}
    if(el.dataset.press){if(el.dataset.press==='rollback'&&!confirm('Повернути попередню модель? Поточна модель збережеться для наступного відкату.'))return;await this.call(el.dataset.press,'press',{});}
  }
  async changed(e) {
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
:host([data-light]){--green:#167c5b}
:host{display:block;width:100%;min-width:0;container-type:inline-size;color:var(--primary-text-color,#e8eeeb);--green:#69d5b2;--muted:var(--secondary-text-color,#a4b1ab);--surface:var(--ha-card-background,var(--card-background-color,#18221e));--stroke:rgba(140,164,150,.17);font-family:var(--primary-font-family,system-ui),sans-serif;font-size:14px;line-height:1.6}*{box-sizing:border-box}main{width:100%;max-width:1520px;margin:auto;padding:20px 32px 12px}header{display:flex;justify-content:space-between;align-items:center;gap:20px;padding:12px 0 26px}.brand{display:flex;gap:12px;align-items:center}.brand-icon{display:grid;place-items:center;background:#69d5b21c;color:var(--green);width:46px;height:46px;border-radius:15px;font-size:33px}.brand strong{font-size:23px;letter-spacing:-.7px}.brand strong span{color:var(--green)}.brand small{display:block;color:var(--muted);font-size:11px;letter-spacing:.5px}.header-status{display:flex;align-items:center;gap:9px;font-size:12px}.dot{width:7px;height:7px;border-radius:50%;background:var(--green)}.dot.red{background:#f28d9a}.mode{margin-left:12px;color:var(--muted);padding-left:20px;border-left:1px solid var(--stroke)}nav{display:flex;align-items:center;gap:8px;border-bottom:1px solid var(--stroke);margin-bottom:26px;padding-bottom:12px}button,input,select{font:inherit}button,a,input,select{touch-action:manipulation}button{min-height:44px;cursor:pointer;color:inherit;background:transparent;border:1px solid var(--stroke);border-radius:10px;padding:9px 14px}button:hover{border-color:var(--green)}button:focus-visible,input:focus-visible,select:focus-visible,a:focus-visible{outline:2px solid var(--green);outline-offset:4px}button:disabled{opacity:.45;cursor:wait}nav button{border:0;color:var(--muted);padding:9px 20px}nav button.selected{background:#69d5b21c;color:var(--green)}a{color:var(--green);text-decoration:none}nav a{margin-left:auto;font-size:12px;color:var(--muted)}.card{background:var(--surface);border:1px solid var(--stroke);border-radius:20px;padding:24px;margin-bottom:20px;min-width:0;box-shadow:0 4px 24px #00000006}h1,h2,p{margin:0}h1{font-size:27px;letter-spacing:-.8px}h2{font-size:16px;font-weight:600;letter-spacing:-.2px;margin-bottom:16px}p{margin:10px 0}.muted,.eyebrow{color:var(--muted)}.eyebrow{font-size:11px;font-weight:500;letter-spacing:1.3px;display:block}.hero{display:grid;grid-template-columns:1fr 1fr;align-items:center;gap:40px;padding:32px 36px;background:radial-gradient(ellipse at 12% 10%,#69d5b21a,transparent 75%),var(--surface)}.temperature{font-size:82px;line-height:1.2;letter-spacing:-5px;font-weight:500;margin-top:10px}.temperature span{font-size:34px;color:var(--green);letter-spacing:-1px;vertical-align:top;margin-left:5px}.hero p{margin:6px 0 16px}.hero-side{border-left:1px solid var(--stroke);padding-left:36px}.hero-side h2{font-size:23px;margin-top:12px;line-height:1.4}.comfort{margin-top:25px;font-size:12px;color:var(--muted)}.comfort strong{display:block;font-size:18px;color:var(--primary-text-color)}.pill{display:inline-block;background:#69d5b215;border:1px solid #69d5b22c;color:var(--green);border-radius:30px;padding:5px 12px;font-size:12px}.warn{color:#c88826;background:#ffc26d15;border-color:#ffc26d40}.metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:18px}.metric{display:flex;flex-direction:column;align-items:flex-start;text-align:left;gap:12px;padding:22px}.metric strong{font-size:28px;font-weight:500;line-height:1.2;letter-spacing:-.8px}.metric strong small{font-size:13px;color:var(--muted);letter-spacing:0}.metric .muted{font-size:11px}.columns{display:grid;grid-template-columns:minmax(0,1.85fr) minmax(300px,1fr);gap:20px}.section-meta{display:flex;justify-content:space-between;align-items:center;gap:10px;font-size:11px;margin-bottom:22px}.forecast-plot{width:100%;min-width:0}.chart-card svg{width:100%;height:auto;max-width:100%;display:block;color:var(--muted)}.legend{display:flex;flex-wrap:wrap;gap:9px 17px;font-size:11px;color:var(--muted);margin:18px 0}.legend span:first-child{color:var(--green)}.legend span:nth-child(2){color:#c88826}.legend span:nth-child(3){color:#dd7c8d}.forecast-stats{display:grid;grid-template-columns:1fr 1fr;gap:0 24px}.row{display:flex;align-items:baseline;justify-content:space-between;gap:12px;padding:10px 0;border-bottom:1px solid var(--stroke);font-size:12px}.row strong{font-weight:500;text-align:right;overflow-wrap:anywhere}.row:last-child{border-bottom:0}.note{font-size:11px;line-height:1.7}.session{display:flex;justify-content:space-between;gap:10px;padding:16px 0;border-bottom:1px solid var(--stroke)}.session strong{font-size:13px}.session .muted{display:block;font-size:11px}.heat-dot{width:9px;height:9px;background:#ffc26d;border-radius:50%;margin-top:8px}.inset{background:#69d5b208;border-radius:12px;padding:16px;margin-top:20px}.large{font-size:32px;letter-spacing:-1px}.large small{font-size:14px;color:var(--muted)}progress{width:100%;height:6px;accent-color:var(--green);margin-top:18px;border:0;border-radius:8px;overflow:hidden}progress::-webkit-progress-bar{background:var(--stroke)}progress::-webkit-progress-value{background:var(--green)}.text-button{border:0;color:var(--green);padding-left:0;font-size:12px;margin-top:12px}.page-title{margin:30px 0}.settings-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,420px),1fr));gap:0 20px}.field{display:flex;align-items:center;justify-content:space-between;gap:20px;padding:15px 0;border-top:1px solid var(--stroke);font-size:13px}.field small{display:block;font-size:10px}.field input,.field select{background:var(--surface);color:inherit;border:1px solid var(--stroke);border-radius:9px;padding:8px;max-width:220px;min-height:40px}.number{display:flex;align-items:center;gap:8px}.number input{width:85px}.number span{font-size:11px;color:var(--muted)}.toggle{position:relative;display:inline-flex;min-width:44px;min-height:44px;align-items:center}.toggle input{opacity:0;position:absolute;inset:0;width:100%;margin:0}.toggle span{width:44px;height:25px;border-radius:25px;background:var(--stroke);pointer-events:none}.toggle span:after{content:'';display:block;width:19px;height:19px;border-radius:50%;background:#bac8bf;transform:translate(3px,3px)}.toggle input:checked+span{background:#69d5b24c}.toggle input:checked+span:after{background:var(--green);transform:translate(22px,3px)}.toggle:focus-within{outline:2px solid var(--green);border-radius:24px}.table-scroll{overflow:auto}table{width:100%;border-collapse:collapse;font-size:13px}th{text-align:left;font-size:11px;color:var(--muted);font-weight:400}td,th{padding:12px 8px;border-bottom:1px solid var(--stroke)}td:not(:first-child),th:not(:first-child){text-align:right}.journal{max-height:600px;overflow:auto}.entry{display:grid;grid-template-columns:90px 1fr;gap:15px;border-bottom:1px solid var(--stroke);padding:14px 0}.entry time{font-size:11px;color:var(--muted)}.entry p{font-size:12px;margin:0}.columns>div{min-width:0}.native{width:100%;max-width:100%;min-width:0;min-height:110px;--ha-card-border-width:0;--ha-card-box-shadow:none}.native>*{display:block;width:100%;max-width:100%;min-width:0}.empty{color:var(--muted);padding:20px 0;font-size:13px}.notice{border-radius:12px;background:#ffc26d15;border:1px solid #ffc26d40;padding:12px 18px;margin-bottom:20px;font-size:13px}.hidden{display:none}footer{display:flex;justify-content:space-between;color:var(--muted);font-size:10px;padding:12px 2px 22px}.menu-button{min-width:44px;border:0;font-size:20px;padding:2px 6px}
section[id]{scroll-margin-top:16px}.fireplace-grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:28px}.fireplace-grid>div{min-width:0}.fire-phase{display:flex;align-items:center;gap:12px;text-align:left;border:0;padding:0;max-width:100%}.fire-phase svg{width:36px;height:36px;flex:0 0 auto;fill:none;stroke:var(--muted);stroke-width:1.5;stroke-linejoin:round}.fire-phase strong{font-size:23px;line-height:1.35;overflow-wrap:anywhere}.fire-block[data-active="true"] .fire-phase{color:#d88b35}.fire-block[data-active="true"] svg{stroke:#e9a851;fill:#e9a8511a}.fire-heat{display:block;font-size:12px;margin:5px 0 16px}.fire-measures{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.fire-measures span{display:block;font-size:11px}.fire-measures strong{display:block;font-size:21px;font-weight:500;white-space:nowrap}.fire-measures small{font-size:11px;color:var(--muted)}.circulation-block{border-left:1px solid var(--stroke);padding-left:28px}.fan-actual{display:block;font-size:20px;font-weight:500;margin-top:7px}.fan-reason{font-size:12px;margin:8px 0;overflow-wrap:anywhere}.fan-waiting{color:#c88826}.fan-details{display:grid;grid-template-columns:1fr 1fr;gap:0 18px}.fire-footer{display:flex;align-items:center;justify-content:space-between;gap:20px;border-top:1px solid var(--stroke);margin-top:16px;padding-top:8px}.fire-footer>span{font-size:11px}.fire-footer button{flex-shrink:0;margin:0}.fireplace-card h2{margin-bottom:10px}
@container(max-width:600px){.fireplace-grid{gap:14px}.fire-phase{gap:8px}.fire-phase svg{width:26px;height:26px}.fire-phase strong{font-size:15px}.fire-heat{font-size:10px;margin:3px 0 12px}.fire-measures{grid-template-columns:1fr;gap:9px}.fire-measures span{font-size:10px}.fire-measures strong{font-size:18px}.circulation-block{padding-left:14px}.circulation-block .eyebrow{font-size:9px;letter-spacing:.4px}.fan-actual{font-size:15px}.fan-details{grid-template-columns:1fr}.fan-details .row{font-size:10px;padding:6px 0;gap:6px}.fan-reason{font-size:11px}.fire-footer{display:block;margin-top:12px}.fire-footer>span{font-size:10px}.fire-footer button{font-size:12px;width:100%;text-align:left;padding-right:0}}
@media(prefers-reduced-motion:no-preference){button{transition:background .15s,border-color .15s}.toggle span:after{transition:transform .2s}}
@container(min-width:1600px){main{padding:24px 42px}}@container(max-width:1000px){main{padding:15px 20px}.columns{grid-template-columns:1fr}.hero{gap:25px}.metrics{gap:12px}.metric{padding:17px}.metric strong{font-size:23px}.mode{display:none}}@container(max-width:600px){main{padding:8px 12px}header{padding:8px 2px 18px;gap:10px}.brand strong{font-size:20px}.brand-icon{width:38px;height:38px}.header-status{font-size:10px}.header-status .dot{display:none}nav{gap:3px;margin-bottom:16px}nav button{padding:10px 12px;font-size:12px}nav a{display:none}.hero{grid-template-columns:minmax(0,.85fr) minmax(0,1.15fr);padding:18px;gap:14px;align-items:center}.hero>div{min-width:0}.temperature{font-size:44px;font-size:clamp(38px,12cqi,54px);letter-spacing:-3px;white-space:nowrap}.temperature span{font-size:20px;letter-spacing:-1px;margin-left:3px}.hero p{font-size:11px;line-height:1.5;margin:6px 0 10px}.hero .pill{font-size:10px;letter-spacing:0;padding:4px 8px}.hero-side{padding:0 0 0 14px;border-left:1px solid var(--stroke);border-top:0}.hero-side h2{font-size:15px;line-height:1.4;margin:8px 0}.hero .eyebrow{font-size:10px;letter-spacing:.5px}.comfort{margin-top:10px;font-size:10px}.comfort strong{font-size:14px}.metrics{grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.metric{padding:16px;gap:10px;border-radius:16px}.metric strong{font-size:23px}.eyebrow{font-size:9px;letter-spacing:.8px}.card{padding:18px;border-radius:16px;margin-bottom:14px}.columns{gap:0}.settings-grid{grid-template-columns:1fr}.field{gap:12px;font-size:12px}.field select{max-width:170px;font-size:11px}.section-meta{flex-wrap:wrap}.forecast-stats{grid-template-columns:1fr}.entry{grid-template-columns:75px 1fr;gap:10px}h1{font-size:23px}footer span{font-size:9px}}
`;
if(!customElements.get('heating-ai-panel'))customElements.define('heating-ai-panel',HeatingAIPanel);
window.customCards=window.customCards||[];
if(!window.customCards.some(c=>c.type==='heating-ai-panel'))window.customCards.push({type:'heating-ai-panel',name:'Heating AI',description:'Клімат: огляд, комфорт, прогноз і навчання',preview:false});
