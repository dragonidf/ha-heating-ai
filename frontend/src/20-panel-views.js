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
    const comparison=event.target.closest?.('.comparison-plot');
    if(comparison){this.inspectComparison(event,comparison);return;}
    const timeline=event.target.closest?.('.home-timeline');
    if(timeline){this.inspectTimeline(event,timeline);return;}
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
  inspectTimeline(event,plot) {
    if(event.type==='pointerleave'&&event.target!==plot)return;
    const forecast=this.forecastData(),forecastStart=Date.parse(forecast.created_at)/1000;
    if(!Number.isFinite(forecastStart))return;
    const start=forecastStart,end=forecastStart+Math.max(86400,(numeric(forecast.horizon_hours)||24)*3600);
    if(event.type==='keydown') {
      if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
      event.preventDefault();
      this.timelineCursor=event.key==='Home'?start:event.key==='End'?end:Math.max(start,Math.min(end,(this.timelineCursor??forecastStart)+(event.key==='ArrowLeft'?-300:300)));
    } else if(event.type==='pointerleave') {
      if(event.pointerType!=='mouse')return;this.timelineCursor=null;
    } else {
      const svg=plot.querySelector('svg'),bounds=svg.getBoundingClientRect(),g=chartLayout(bounds.width);
      const fraction=Math.max(0,Math.min(1,((event.clientX-bounds.left)*g.width/bounds.width-g.left)/Math.max(1,g.width-g.right-g.left)));
      this.timelineCursor=start+fraction*(end-start);
    }
    if(!this.timelineFrame)this.timelineFrame=requestAnimationFrame(()=>{this.timelineFrame=0;if(this.isConnected)this.render();});
  }
  forecastData() {
    const main=this.attrs('forecast'),outlook=this.hass?.states?.['sensor.heating_forecast_outlook']?.attributes;
    if(this.forecastExtended&&outlook&&typeof outlook.plan_id==='string'&&outlook.plan_id===main.plan_id&&Array.isArray(outlook.curve)) {
      return {...main,...outlook,curve:outlook.curve.map(p=>({ts:p[0],temperature:p[1],lower:p[2],upper:p[3],target:p[4]})),
        heat_segments:(outlook.heat_segments||[]).map(s=>Array.isArray(s)?{start:s[0],end:s[1],conditional:Number(s[2])===1}:s)};
    }
    return main;
  }
  forecastChart() {
    const a=this.forecastData(), rows=(Array.isArray(a.curve)?a.curve:[]).filter(r=>numeric(r.ts)!==null&&numeric(r.temperature)!==null);
    const forecastStart=Date.parse(a.created_at)/1000,futureDuration=Math.max(86400,(numeric(a.horizon_hours)||24)*3600);
    const start=forecastStart,end=forecastStart+futureDuration,duration=end-start;
    if(!rows.length||!Number.isFinite(forecastStart)) return '<p class="empty">Прогноз з’явиться після наступного розрахунку контролера.</p>';
    const cursor=this.timelineCursor===null||this.timelineCursor===undefined?null:Math.max(start,Math.min(end,this.timelineCursor));
    const temperatures=rows.map(r=>Number(r.temperature));
    const low=Math.min(...temperatures,...rows.map(r=>numeric(r.lower)??Number(r.temperature)))-.4;
    const high=Math.max(...temperatures,...rows.map(r=>numeric(r.upper)??Number(r.temperature)))+.4;
    const measured=this.shadowRoot.querySelector('.forecast-plot')?.clientWidth;
    const g=chartLayout(measured||Math.min(Math.max(1,this.clientWidth-80),880));
    const right=g.width-g.right;
    const x=t=>g.left+Math.max(0,Math.min(1,(t-start)/duration))*Math.max(1,right-g.left);
    const y=t=>g.bottom-(t-low)/(high-low)*(g.bottom-g.top);
    let svg=`<div class="forecast-plot home-timeline" tabindex="0" role="slider" aria-label="Час на графіку прогнозу" aria-valuemin="${start}" aria-valuemax="${end}" aria-valuenow="${cursor??forecastStart}" aria-valuetext="${h(this.time(cursor??forecastStart,true))}"><svg width="${g.width}" height="${g.height}" viewBox="0 0 ${g.width} ${g.height}" role="img" aria-label="${fmt(a.horizon_hours||24,1)} годин прогнозу температури, нагріву й мережі"><defs><linearGradient id="curve-fill" x1="0" y1="0" x2="0" y2="1"><stop stop-color="var(--history-room)" stop-opacity=".2"/><stop offset="1" stop-color="var(--history-room)" stop-opacity="0"/></linearGradient></defs>`;
    let prev=forecastStart;
    for(const r of rows) {
      if(numeric(r.lower)!==null&&numeric(r.upper)!==null) svg+=`<rect x="${x(prev)}" y="${y(r.upper)}" width="${Math.max(0,x(r.ts)-x(prev))}" height="${Math.max(0,y(r.lower)-y(r.upper))}" fill="var(--history-room)" opacity=".09"/>`;
      prev=r.ts;
    }
    const known=numeric(a.schedule_known_until);
    if(known!==null&&known<end&&!(a.outage_mode==='normal'&&!isTrue(this.attrs('outages').emergency))) svg+=`<rect x="${x(known)}" y="${g.top}" width="${Math.max(0,right-x(known))}" height="${g.bottom+79-g.top}" fill="var(--source-red)" opacity=".07"/>`;
    for(let i=0;i<4;i++) { const t=low+(high-low)*i/3; svg+=`<path d="M${g.left} ${y(t)}H${right}" stroke="currentColor" opacity=".08"/><text x="0" y="${y(t)+4}" fill="currentColor" font-size="12">${fmt(t)}°</text>`; }
    const path=rows.map((r,i)=>`${i?'L':'M'}${x(r.ts).toFixed(2)},${y(r.temperature).toFixed(2)}`).join(' ');
    svg+=`<path d="${path} L${x(rows.at(-1).ts)},${g.bottom+8} L${x(rows[0].ts)},${g.bottom+8} Z" fill="url(#curve-fill)"/><path d="${path}" fill="none" stroke="var(--history-room)" stroke-width="3" stroke-dasharray="5 4" vector-effect="non-scaling-stroke"/>`;
    for(const r of rows) svg+=`<circle cx="${x(r.ts)}" cy="${y(r.temperature)}" r="6" fill="transparent"><title>${h(this.time(r.ts))} · ${fmt(r.temperature)} °C</title></circle>`;
    svg+=`<text x="0" y="${g.bottom+35}" fill="currentColor" font-size="11">Котел</text>`;
    for(const s of mergeSegments(a.heat_segments)) if(s.end>start&&s.start<end) svg+=`<rect x="${x(s.start)}" y="${g.bottom+22}" width="${Math.max(1,x(s.end)-x(s.start))}" height="15" rx="4" fill="var(--source-red)" opacity="${isTrue(s.conditional)?.45:1}"><title>${h(this.time(s.start))}–${h(this.time(s.end))}${isTrue(s.conditional)?' · умовно':''}</title></rect>`;
    const outages=mergeSegments(this.attrs('outages').outages).filter(s=>s.end>start&&s.start<end);
    if(outages.length)svg+=`<text x="0" y="${g.bottom+60}" fill="currentColor" font-size="11">Мережа</text>`;
    for(const s of outages) svg+=`<rect x="${x(s.start)}" y="${g.bottom+47}" width="${Math.max(1,x(s.end)-x(s.start))}" height="15" rx="4" fill="var(--source-red)"><title>Відключення ${h(this.time(s.start))}–${h(this.time(s.end))}</title></rect>`;
    if(cursor!==null)svg+=`<path class="history-crosshair" d="M${x(cursor)} ${g.top}V${g.bottom+87}" stroke="currentColor" stroke-dasharray="3 4" opacity=".62"/>`;
    for(let i=0;i<=g.tickIntervals;i++) svg+=`<text x="${x(start+i*duration/g.tickIntervals)}" y="${g.height-8}" text-anchor="${i===0?'start':i===g.tickIntervals?'end':'middle'}" fill="currentColor" font-size="12">${h(this.time(start+i*duration/g.tickIntervals,duration>86400))}</text>`;
    const nearest=(values,time,key)=>values.reduce((best,row)=>best===null||Math.abs(row.ts-time)<Math.abs(best.ts-time)?row:best,null)?.[key];
    const cursorTemperature=cursor===null?null:numeric(nearest(rows,cursor,'temperature'));
    const planned=cursor===null?null:mergeSegments(a.heat_segments).find(segment=>segment.start<=cursor&&cursor<segment.end);
    const readout=cursor===null?'Торкніться або наведіть на графік, щоб побачити значення.':`${this.time(cursor,true)} · Прогноз: ${cursorTemperature===null?'—':fmt(cursorTemperature)+' °C'} · Котел: ${planned?'заплановано':'вимкнено'}`;
    return svg+`</svg></div><div class="legend"><span>┄ Прогноз температури</span><span>● План нагріву</span>${outages.length?'<span>● Відключення</span>':''}</div><p class="history-readout muted" aria-live="polite">${h(readout)}</p>`;
  }
  compactMetric(label,value,unit='',entity='',key='') {
    const tag=entity?'button':'div';
    return `<${tag} class="compact-metric" ${entity?`data-info="${h(this.id(entity))}"`:''} ${key?`data-forecast-metric="${h(key)}"`:''}><span class="muted">${h(label)}</span><strong>${h(value)}${value!=='—'&&unit?` <small>${h(unit)}</small>`:''}</strong></${tag}>`;
  }
  equipmentControl(field) {
    let value=this.systemDraft[field.key]??field.value??field.default;
    if(field.kind==='json'&&value&&typeof value==='object')value={...value,verified:isTrue(value.verified)};
    const common=`data-system-key="${h(field.key)}" aria-label="${h(field.label)}" ${this.systemPending?'disabled':''}`;
    if(field.kind==='json')return `<textarea ${common} data-json rows="5" spellcheck="false">${h(JSON.stringify(value,null,2))}</textarea>`;
    let options=field.options||{};
    if(field.kind==='entity') {
      options={'':'Не підключено'};
      const relays=Object.entries(this.hass?.states||{}).filter(([id])=>/^(switch|fan)\./.test(id));
      const name=([id,row])=>row.attributes?.friendly_name||id;
      relays.sort((a,b)=>name(a).localeCompare(name(b),'uk'));
      const names=new Map();for(const relay of relays)names.set(name(relay),(names.get(name(relay))||0)+1);
      for(const [id,row] of relays){const label=name([id,row]);options[id]=`${label}${names.get(label)>1?' · '+id:''}${['unavailable','unknown'].includes(row.state)?' · недоступний':''}`;}
      if(value&&!Object.hasOwn(options,value))options[value]=value+' · недоступний';
    }
    if(['select','entity'].includes(field.kind))return `<span class="equipment-select"><select ${common}>${Object.entries(options).map(([key,label])=>`<option value="${h(key)}" ${key===value?'selected':''}>${h(label)}</option>`).join('')}</select><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg></span>`;
    return `<input type="text" ${common} value="${h(value)}" placeholder="switch.…" autocapitalize="none" spellcheck="false">`;
  }
  equipmentFields(keys) {
    return this.systemFields().filter(f=>keys.includes(f.key)).map(f=>`<label class="field"><span>${h(f.label)} <button type="button" class="system-help" data-system-help="${h(f.key)}" aria-label="Підказка: ${h(f.label)}">?</button></span>${this.equipmentControl(f)}</label>`).join('');
  }
  equipmentSave() {
    if(!Object.keys(this.systemDraft).length&&!this.systemPending&&!this.systemMessage)return '';
    return `<div class="system-actions equipment-save">${this.systemUndo?`<button data-system-undo ${this.systemPending?'disabled':''}>Попередні значення</button>`:''}<span class="equipment-status" role="status">${h(this.systemMessage||'')}</span><button data-system-save ${this.systemPending||!Object.keys(this.systemDraft).length?'disabled':''}>${this.systemPending?'Зберігаємо…':Object.keys(this.systemDraft).length?'Зберегти':'Збережено ✓'}</button></div>`;
  }
  powerDetails() {
    const p=this.attrs('power_model'),v=this.attrs('ventilation'),o=p.power_observation||{},energy=p.energy||{};
    return this.section('Потужність і вентиляція',`${this.row('Фізичний режим',({fixed_3:'3 кВт',fixed_6:'6 кВт',fixed_9:'9 кВт',auto:'Авто',unconfirmed:'Не підтверджено'})[this.state('power_model')]||'—')}${this.row('Точність потужності',p.accuracy||'—')}${this.row(o.policy==='inlet'?'Залишок на вводі':'Метод A',fmt(o.a?.watts,0)+' Вт')}${o.policy==='inlet'?this.row('Перевірка фаз',({derived_group_balance:'Основний баланс груп',nominal_inlet_stage:'Резерв за окремими приладами',consumer_unavailable:'Немає свіжого виміру споживача',phase_stage_conflict:'Розподіл не відповідає ТЕНам',physical_mode_conflict:'Не відповідає режиму котла',power_without_demand:'Немає запиту нагріву',return_model_conflict:'Неузгоджено з моделлю обратки',stage_mismatch:'Залишок не відповідає 3/6/9 кВт'})[o.reason]||'Очікуємо узгоджених даних'):this.row('Метод B',fmt(o.b?.watts,0)+' Вт · '+(o.b?.reason==='topology_unconfirmed'?'схема фаз не підтверджена':o.b?.reason||'—'))}${this.row('Якість',({measured:'Виміряна',estimated:'Оцінена',unknown:'Невідома'})[o.quality]||'—')}<div class="plan-breakdown">${this.compactMetric('Виміряно',fmt(energy.measured_kwh,3),'кВт·год')}${this.compactMetric('Оцінено',fmt(energy.estimated_kwh,3),'кВт·год')}${this.compactMetric('Невідомий час',fmt(numeric(energy.unknown_seconds)===null?null:energy.unknown_seconds/60,1),'хв')}</div><p class="muted note">Новий облік якості від ${this.time(energy.started_at,true)}. ${o.policy==='inlet'?'Основний баланс груп і резерв за приладами — розрахунок; незалежного лічильника котла немає.':'A/B можуть мати спільні джерела; це перевірка узгодженості.'}</p>${this.row('Вентиляція',this.state('ventilation'))}${this.row('Незалежних днів',fmt(v.model?.days,0))}${this.row('Причина статусу',({insufficient_independent_days:'Бракує незалежних днів',correlated_or_constant_relays:'Стани пристроїв не дозволяють розділити вплив',heldout_improvement:'Покращення на відкладеному дні',no_heldout_improvement:'Кандидат не покращив прогноз'})[v.model?.reason]||v.model?.reason||'—')}<p class="muted note">Реле показує живлення, а не виміряну витрату повітря. Старий прогноз діє, доки кандидат не пройде перевірку.</p>`);
  }
  selectedBoilerMode() {
    return this.attrs('system_settings').values?.boiler_power_mode||this.state('power_model');
  }
  equipmentPlanPending() {
    const f=numeric(this.attrs('forecast').equipment_version),p=numeric(this.attrs('power_model').equipment_version);
    const selected=this.attrs('system_settings').values?.boiler_power_mode,current=this.state('power_model');
    return f!==null&&p!==null&&f!==p||!!selected&&['fixed_3','fixed_6','fixed_9','auto'].includes(current)&&selected!==current;
  }
  powerAdvice() {
    const recommend=this.attrs('power_model').recommendation||{};
    const mode=({fixed_3:'3 кВт',fixed_6:'6 кВт',fixed_9:'9 кВт',auto:'Авто',unconfirmed:'Не підтверджено'})[this.selectedBoilerMode()]||'Не підтверджено';
    const recommended=({fixed_3:'3 кВт',fixed_6:'6 кВт',fixed_9:'9 кВт'})[recommend.mode];
    const best=(recommend.alternatives||[]).filter(a=>isTrue(a.found)&&numeric(a.deficit_degree_hours)!==null).sort((a,b)=>Number(a.deficit_degree_hours)-Number(b.deficit_degree_hours)||Number(a.nominal_kw)-Number(b.nominal_kw))[0];
    const partial=!recommended&&best&&recommend.status!=='no_available_interval';
    const adviceValue=recommended||(partial?`${fmt(best.nominal_kw,0)} кВт`:recommend.status==='no_available_interval'?'Немає вікна':recommend.status==='insufficient_data'?'Потрібні дані':'Уточнюється');
    return {recommend,mode,recommended,partial,adviceValue,changeBefore:recommend.change_before||(partial?best.change_before:null)};
  }
  boilerActual() {
    const meta=this.attrs('power_confirmed'),reported=Date.parse(meta.timestamp);
    const stale=meta.timestamp!==undefined&&(!Number.isFinite(reported)||Date.now()-reported>90000);
    const pending=stale||isTrue(meta.confirmation_pending),binary=pending?'unavailable':this.state('boiler_active'),power=pending?null:numeric(this.state('power_confirmed'));
    const heating=power!==null?power>=1500:binary==='on'?true:binary==='off'?false:null;
    return {meta,stale,power,heating};
  }
  boilerOverview() {
    const {meta,stale,power,heating}=this.boilerActual();
    const calculated=['nominal_inlet_stage','group_balance','appliance_stage'].includes(meta.measurement_basis);
    const {recommend,mode,recommended,partial,adviceValue,changeBefore}=this.powerAdvice();
    const advice=`<details class="power-advice"><summary><span>Обраний профіль<strong>${h(mode)}</strong></span><span class="${partial?'warn':''}">${partial?'Для зменшення дефіциту':recommend.mode===this.selectedBoilerMode()?'Режим відповідає плану':'Рекомендовано'}<strong>${h(recommend.mode===this.selectedBoilerMode()?'✓':adviceValue)}${(recommended||partial)&&recommend.confidence!=='validated_scenario'?'<small> · умовно</small>':''}</strong></span></summary><p class="muted note">${partial?'Жоден режим не забезпечує весь комфорт за чинними умовами. Наведений варіант найкраще зменшує прогнозований дефіцит. ':''}${h(recommend.reason||'Рекомендація з’явиться після перерахунку температури, погоди й плану.')}</p>${changeBefore?this.row('Потрібна ручна зміна до',this.time(changeBefore,true)):''}${(recommend.alternatives||[]).map(a=>this.row(`${a.nominal_kw} кВт`,isTrue(a.feasible)?'План знайдено':`Дефіцит ${fmt(a.deficit_degree_hours,2)} °C·год`)).join('')}<p class="muted note">${h(recommend.model_bound?.assumptions||'Номінальна потужність не гарантує фактичних кВт·год. Рекомендація не перемикає котел.')}</p></details>`;
    return `<section class="card boiler-card" data-heating="${heating??'unknown'}"><div class="card-heading"><h2>Котел</h2><span class="pill boiler-status">${heating===true?(calculated?'Гріє · розрахунок':'Гріє'):heating===false?'Очікування':'Немає даних'}</span></div><div class="boiler-metrics">${this.compactMetric('Потужність',fmt(power===null?null:power/1000),'кВт','power_confirmed')}${this.compactMetric('Обратка',fmt(this.state('return')),'°C','return')}${this.compactMetric('За 24 год',fmt(this.state('energy_24h'),2),'кВт·год','energy_24h')}</div>${!stale&&numeric(meta.assumed_control_w)!==null&&isTrue(meta.control_reserve?.valid)?`<p class="muted note">Для розрахунку: ${fmt(meta.assumed_control_w/1000)} кВт · резерв. Фактичне споживання не підтверджене.</p>`:''}${calculated?`<p class="muted note">Облік за вводом · ${meta.measurement_basis==='group_balance'?'баланс груп':meta.measurement_basis==='appliance_stage'?'окремі прилади · резерв':'номінальні ступені ТЕНів'}${power===null?' · немає достовірної потужності':''}.</p>`:''}${advice}</section>`;
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
    const f=this.forecastData(),o=this.attrs('outages'),known=f.schedule_known_until??o.known_until;
    const outlook=this.hass?.states?.['sensor.heating_forecast_outlook']?.attributes;
    const extended=outlook?.plan_id===f.plan_id&&numeric(outlook?.horizon_hours)>24;
    const mode=o.mode??f.outage_mode,normal=mode==='normal'&&!isTrue(o.emergency);
    const assumed=isTrue(o.assumed_available);
    const stale=this.equipmentPlanPending()||!Number.isFinite(Date.parse(f.created_at))||Date.now()-Date.parse(f.created_at)>420000;
    const help=`<details class="forecast-help"><summary aria-label="Як читати прогноз" title="Як читати прогноз">${icon('info')}</summary><div><strong>Як читати прогноз</strong><p>Тло — комфортний діапазон. Прогноз — оцінка, пунктир показує майбутню температуру.</p><p>Камін — оцінка залишкового тепла. Час майбутнього горіння невідомий.</p><p>Після кінця відомого графіка мережі нагрів умовний; перед виконанням контролер перевіряє фактичну мережу.</p><p>Повний горизонт показує вже обчислене продовження цього плану. Невідомі погода та живлення залишаються умовними.</p></div></details>`;
    return `<section class="card chart-card"><div class="card-heading"><h2>Прогноз на ${numeric(f.horizon_hours)>24?fmt(f.horizon_hours,1):'24'} год</h2>${help}</div><div class="section-meta">${stale?'<span class="pill warn">Попередній сценарій</span>':isTrue(f.comfort_shortfall)?'<span class="pill warn">Прогнозується дефіцит тепла</span>':''}<span class="muted">Оновлено ${h(this.time(f.created_at))}</span></div>${extended?`<div class="history-ranges" role="group" aria-label="Горизонт прогнозу"><button data-forecast-horizon="24" aria-pressed="${!this.forecastExtended}">24 год</button><button data-forecast-horizon="extended" aria-pressed="${!!this.forecastExtended}">${fmt(outlook.horizon_hours,1)} год · увесь розрахунок</button></div>`:''}${this.forecastChart()}<div class="forecast-stats">${this.compactMetric('Мінімальна температура',fmt(f.minimum??this.state('forecast')),'°C','','minimum')}${this.compactMetric('Очікувана енергія',fmt(f.planned_kwh,2),'кВт·год','','energy')}${!normal?`<p class="muted">Графік мережі відомий до ${h(this.time(known,true))}</p>`:''}</div></section>`;
  }
  heatingPlan(diagnostics=false) {
    if(!diagnostics&&this.equipmentPlanPending())return this.section('План нагріву','<p class="muted">Перераховуємо план за оновленим профілем котла.</p>','plan-card');
    const f=this.attrs('forecast'),segments=mergeSegments(f.heat_segments);
    const morning=Array.isArray(f.morning_runs_minutes)?f.morning_runs_minutes.reduce((sum,v)=>sum+(numeric(v)??0),0):null;
    const stale=this.equipmentPlanPending()||!Number.isFinite(Date.parse(f.created_at))||Date.now()-Date.parse(f.created_at)>420000;
    const reserve=f.morning_reserve&&typeof f.morning_reserve==='object'?f.morning_reserve:null;
    const budget=f.cycle_energy&&typeof f.cycle_energy==='object'?f.cycle_energy:{};
    const budgetRow=numeric(budget.remaining_kwh)!==null?`<div class="plan-morning" data-cycle-budget><strong>Цикл${isTrue(budget.observation_complete)?'':' · часткові дані'}</strong><span>${fmt(budget.observed_kwh,2)} + ${fmt(budget.remaining_kwh,2)} = ${fmt(budget.projected_total_kwh,2)} кВт·год</span></div>`:'';
    const sourceNames={weather:'погода',grid:'мережа',thermal_state:'тепловий стан',power:'потужність',manual:'режим і дозволи',fallback:'штатне керування',model:'модель',execution:'виконання',initial_projection:'перший прогноз'};
    const budgetDetails=numeric(budget.remaining_kwh)!==null?`<div class="plan-breakdown">${this.compactMetric('Фактична перерва живлення',fmt(budget.deviation?.outage_minutes,1),'хв')}${this.compactMetric('Середня потужність за запитом',fmt(budget.deviation?.request_average_kw,2),'кВт')}${this.compactMetric('Поточний мінімум',fmt(budget.deviation?.minimum_temperature,2),'°C')}</div>${budget.deviation?.violation_at?this.row('Ризик нижче мінімуму',this.time(Number(budget.deviation.violation_at),true)+' · '+fmt(budget.deviation.violation_temperature,3)+' °C < '+fmt(budget.deviation.lower,3)+' °C'):''}<div class="plan-breakdown">${this.compactMetric('Передцикловий план',fmt(budget.baseline?.kwh,2),'кВт·год')}${this.compactMetric('Зміна оцінки циклу',fmt(budget.delta_from_baseline_kwh,2),'кВт·год')}</div><p class="muted note">Виміряно + залишок = оцінка всього циклу. База ${h(this.time(budget.baseline?.at,true))}. Зміни: ${h((Array.isArray(budget.change_sources)?budget.change_sources:[]).map(k=>sourceNames[k]||k).join(', '))}.${isTrue(budget.baseline_consumed)?' Початковий бюджет витрачено; залишкова потреба перевіряється, це не жорсткий stop.':''}</p>`:'';
    const stresses=Array.isArray(f.stress_scenarios)?f.stress_scenarios:[];
    const sessionDetails=s=>`${fmt((s.end-s.start)/60,0)} хв${numeric(s.power_kw)!==null?' · ≈'+fmt(s.power_kw,1)+' кВт':''}${numeric(s.kwh)!==null?' · '+fmt(s.kwh,2)+' кВт·год':''}${isTrue(s.conditional)?' · умовно':''}`;
    const stressDetails=stresses.length?`<div class="plan-breakdown">${stresses.map(r=>this.compactMetric(r.name==='cold_low_solar'?'Холодно / мало сонця':`Без мережі ${String(r.name).replace('blackout_','')} хв`,fmt(r.deficit_degree_hours,2),'°C·год дефіциту')).join('')}</div><p class="muted note">Гіпотетичні сценарії замороженого плану без перепланування. Це не прогноз аварії та не додатковий запит нагріву.</p>`:'';
    const execution=reserve?`<div class="plan-breakdown" data-reserve-execution>${this.compactMetric('Заплановано вранці',fmt(reserve.planned_minutes,1),'хв')}${this.compactMetric('Автоматично виконано',fmt(reserve.confirmed_completed_minutes,1),'хв')}${this.compactMetric('Фактична енергія ранку',fmt(reserve.confirmed_kwh,2),'кВт·год')}${this.compactMetric('Ручний нагрів вранці',fmt(reserve.manual_completed_minutes,1),'хв')}</div><p class="note ${isTrue(reserve.predicted_sufficient)?'muted':'warn'}">${h(reserve.impossibility_reason|| (reserve.reserve_sufficient===null?'Запас до наступного вікна умовний: бракує перевірки потрібного горизонту.':'Достатній за поточним сценарієм.'))}${numeric(reserve.day_deficit_minutes)>0?` Денна нижня межа: ${fmt(reserve.day_deficit_minutes,0)} хв, найбільше відхилення ${fmt(reserve.day_max_shortfall,2)} °C.`:''}</p>`:'';
    if(!diagnostics)return this.section('План нагріву',`<div class="sessions-grid">${segments.map(s=>`<div class="session"><div><strong>${h(this.time(s.start,true))} → ${h(this.time(s.end))}</strong><span class="muted">${h(sessionDetails(s))}</span></div><span class="heat-dot"></span></div>`).join('')||'<p class="empty">Нагрів не запланований.</p>'}</div><button class="text-button" data-tab="model">План і виконання докладніше ↗</button>`,'plan-card');
    return this.section('План нагріву',`<p class="plan-reason muted">${h(f.split_reason||'Контролер розраховує оптимальні інтервали.')}</p><div class="sessions-grid">${segments.map(s=>`<div class="session"><div><strong>${h(this.time(s.start,true))} → ${h(this.time(s.end))}</strong><span class="muted">${h(sessionDetails(s))}</span></div><span class="heat-dot"></span></div>`).join('')||'<p class="empty">Нагрів не запланований.</p>'}</div><div class="plan-morning"><strong>Ранковий прогрів</strong><span>${fmt(morning,0)} хв <small class="muted">· мін. ${fmt(f.morning_min_minutes,0)} хв</small></span></div>${execution}${budgetRow}${stale?'<p class="forecast-warning warn note">План застарів: потрібен новий розрахунок.</p>':''}<details class="plan-details"><summary>Подробиці плану</summary>${budgetDetails}${stressDetails}<div class="plan-breakdown">${this.compactMetric('Вечір',fmt(f.session_kwh?.evening,2),'кВт·год')}${this.compactMetric('Ранок',fmt(f.session_kwh?.morning,2),'кВт·год')}${this.compactMetric('Інші інтервали',fmt(f.session_kwh?.other,2),'кВт·год')}${this.compactMetric('Умовний нагрів',fmt(f.conditional_heat_minutes,0),'хв')}${this.compactMetric('Поза дозволеними годинами',fmt(f.outside_minutes,0),'хв')}${this.compactMetric('Перенесено через відключення',isTrue(f.outage_shifted)?'Так':'Ні')}</div><p class="muted note">${h(morning?'Ранковий прогрів зарезервовано.':f.split_reason||'Ранкового нагріву немає.')}</p></details>`, 'plan-card');
  }
  nextHeatText() {
    if((this.state('mode')||this.attrs('controller').mode)==='Спостереження')return 'призупинене';
    if(this.equipmentPlanPending())return 'перераховується';
    const f=this.attrs('forecast'),now=Date.now()/1000;
    if(!Number.isFinite(Date.parse(f.created_at))||Date.now()-Date.parse(f.created_at)>420000)return 'уточнюється';
    if(this.boilerActual().heating===true){
      const current=mergeSegments(f.heat_segments).find(s=>s.start<=now&&s.end>now);
      return current?`за планом до ${this.time(current.end)}`:'поза активним сеансом плану';
    }
    const segment=mergeSegments(f.heat_segments).find(s=>s.end>now);
    return segment?segment.start<=now?'зараз':this.time(segment.start,true):'не потрібен';
  }
  overviewDecision() {
    const c=this.attrs('controller'),d=this.attrs('diagnostics');
    const mode=this.state('mode')||c.mode;
    if(this.boilerActual().heating===true)return mode==='Спостереження'?'Гріє · спостереження':mode==='Штатний auto'?'Гріє · ручний AUTO':'Гріє';
    if(mode==='Штатний auto')return 'Ручний режим AUTO';
    if(c.control_method==='temporary_native_auto'||d.control_health==='error'||!isTrue(c.quality))return 'Потребує уваги';
    if(this.attrs('grid_status').raw!=='on')return 'Очікує світло';
    return 'Зберігає тепло';
  }
  energyComparison(detailed=false) {
    const now=Date.now()/1000,cycles=this.attrs('plan_history').energy_cycles||[];
    const row=cycles.find(c=>Number(c.start)<=now&&Number(c.end)>now)||cycles.find(c=>Number(c.end)<=now);
    if(!row)return detailed?this.section('Енергія: план і факт','<p class="empty">Ще немає збереженого циклу. Початковий план фіксується до нагріву.</p>'):'';
    const initial=row.initial_plan||row.baseline,complete=isTrue(row.complete),finished=now>=Number(row.end);
    const calculated=numeric(row.calculated_kwh)||0;
    let fact=complete?(numeric(row.meter_kwh)??numeric(row.observed_kwh)):(numeric(row.observed_kwh)??0)+calculated;
    if(!complete&&fact===0&&numeric(row.unknown_seconds)>0)fact=null;
    const current=numeric(row.latest_plan?.kwh)??numeric(row.last_prestart?.kwh),diff=fact!==null&&numeric(initial?.kwh)!==null?fact-Number(initial.kwh):null;
    const metrics=`<div class="plan-energy-grid ${detailed?'':'energy-pair'}">${this.compactMetric(row.initial_plan&&!isTrue(row.initial_plan.legacy)?(isTrue(row.initial_plan.before_start)?'Початковий план':'Перша збережена оцінка'):'Збережена база',fmt(initial?.kwh,2),'кВт·год')}${detailed?this.compactMetric(row.latest_plan?'Актуальний план':'План перед стартом',fmt(current,2),'кВт·год'):''}${this.compactMetric(calculated>0?'Облік · розрахунок':fact===null?'Факт · немає даних':complete?'Факт':'Факт · частково',fmt(fact,2),'кВт·год')}</div>`;
    return this.section(finished?'Останній цикл · план і факт':'Поточний цикл · план і факт',`<div class="muted note">${h(this.time(Number(row.start),true))} → ${h(this.time(Number(row.end),true))}</div>${metrics}${detailed?`${this.row('Відхилення від початкового',diff===null||!complete&&diff<=0?'—':(calculated>0?'≈ ':!complete?'≥ ':'')+(diff>0?'+':'')+fmt(diff,2)+' кВт·год')}<details class="energy-notes"><summary>Якість обліку</summary>${this.row('Невизначені інтервали',fmt(Number(row.unknown_seconds||0)/60,1)+' хв')}${this.row('Без автоматичного запиту',fmt((numeric(row.other_kwh)||0)+(numeric(row.calculated_other_kwh)||0),2)+' кВт·год')}<p class="muted note">${complete?'Облік за лічильником розрахункової потужності.':calculated>0?'Включено розрахунок за номінальними ступенями ТЕНів. Це не незалежний вимір.':'Показано лише підтверджену частину; прогалини не зараховані як нуль.'} Початкова база від ${h(this.time(initial?.at,true))} не змінюється під час перерахунків.</p></details>`:''}`,'plan-energy');
  }
  inspectComparison(event,plot) {
    const {start,end}=this.comparisonView();
    if(event.type==='pointerleave')return;
    if(event.type==='keydown') {
      if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
      event.preventDefault();const step=(end-start)>86400?3600:900;
      this.comparisonCursor=event.key==='Home'?start:event.key==='End'?end:Math.max(start,Math.min(end,(this.comparisonCursor??start)+(event.key==='ArrowLeft'?-step:step)));
    }else{
      const rect=plot.getBoundingClientRect(),fraction=(event.clientX-rect.left-42)/(rect.width-54);
      this.comparisonCursor=start+Math.max(0,Math.min(1,fraction))*(end-start);
    }
    this.render();
  }
  temperatureComparison() {
    const span=this.comparisonWindow(),{start,end}=this.comparisonView(),incoming=this.attrs('plan_history').comparison;
    const matched=data=>data&&!isTrue(data.requires_panel_reload)&&Number(data.start)===span.start&&Math.abs(Number(data.end)-span.end)<1;
    const data=matched(this.comparisonData)?this.comparisonData:matched(incoming)?incoming:null;
    const zone=this.hass?.config?.time_zone||'Europe/Kyiv';
    const day=stamp=>new Intl.DateTimeFormat('sv-SE',{timeZone:zone}).format(new Date(stamp*1000));
    const draft=this.comparisonDateDraft||{};
    const controls=`<div class="comparison-tools"><div class="history-ranges" role="group" aria-label="Період порівняння">${[[1,'24 год'],[7,'7 днів'],[30,'30 днів']].map(([days,label])=>`<button data-comparison-days="${days}" aria-pressed="${this.comparisonDays===days}">${label}</button>`).join('')}</div><button class="history-refresh" data-comparison-refresh aria-label="Оновити порівняння" ${this.comparisonPending?'disabled':''}>↻</button></div><details class="comparison-dates"><summary>Обрати дати</summary><div><label>Від<input type="date" data-comparison-date="from" aria-label="Початкова дата" value="${h(draft.from||day(start))}" max="${day(Date.now()/1000)}"></label><label>До<input type="date" data-comparison-date="to" aria-label="Кінцева дата" value="${h(draft.to||day(end))}" max="${day(Date.now()/1000)}"></label><button data-comparison-apply>Показати</button></div></details><p class="comparison-period muted">${h(this.time(start,true))} — ${h(this.time(end,true))}</p>`;
    const cycles=Array.isArray(data?.cycles)?data.cycles:[],actual=Array.isArray(data?.actual)?data.actual:[];
    const forecasts=cycles.map((c,i)=>({cycle:c,finish:Math.min(end,cycles.slice(i+1).find(next=>isTrue(next.start_known))?.started_at??end),points:c.forecast?.curve||[]}));
    const visible=p=>numeric(p.ts)!==null&&Number(p.ts)>=start&&Number(p.ts)<=end;
    const values=[...actual,...forecasts.flatMap(f=>f.points.filter(p=>Number(p.ts)>=Number(f.cycle.started_at)&&Number(p.ts)<=f.finish))].filter(visible).map(p=>numeric(p.temperature)).filter(v=>v!==null);
    const width=Math.max(220,Math.round(this.shadowRoot?.querySelector('.comparison-plot')?.clientWidth||Math.min(this.clientWidth-64||600,900)));
    const height=260,left=42,right=12,top=15,bottom=220;
    const low=values.length?Math.floor(Math.min(...values)-.5):20,high=values.length?Math.ceil(Math.max(...values)+.5):24;
    const x=ts=>left+(Number(ts)-start)/(end-start)*(width-left-right),y=v=>bottom-(Number(v)-low)/(high-low)*(bottom-top);
    // Stop at real endpoints; keep gaps and separate independently frozen issues.
    const paths=(points,gap)=>{
      const out=[];let path='',last=null;
      for(const p of points){const ts=numeric(p.ts),v=numeric(p.temperature);
        if(ts===null||v===null||last!==null&&ts-last>gap){if(path)out.push(path);path='';}
        if(ts!==null&&v!==null)path+=`${path?'L':'M'}${x(ts).toFixed(2)},${y(v).toFixed(2)} `;
        last=ts;
      }
      if(path)out.push(path);return out;
    };
    const grid=Array.from({length:5},(_,i)=>{const value=low+(high-low)*i/4;return `<line x1="${left}" x2="${width-right}" y1="${y(value)}" y2="${y(value)}" class="comparison-grid"/><text x="${left-8}" y="${y(value)+4}" text-anchor="end">${fmt(value,1)}</text>`;}).join('');
    const count=width<520?3:6;
    const ticks=Array.from({length:count+1},(_,i)=>{const ts=start+(end-start)*i/count;
      const label=new Intl.DateTimeFormat('uk-UA',{timeZone:zone,...(end-start>86400?{day:'2-digit',month:'2-digit'}:{hour:'2-digit',minute:'2-digit'})}).format(new Date(ts*1000));
      return `<text x="${x(ts)}" y="248" text-anchor="${i===0?'start':i===count?'end':'middle'}">${h(label)}</text>`;}).join('');
    const forecastPaths=forecasts.flatMap(f=>paths(f.points.filter(p=>Number(p.ts)>=Number(f.cycle.started_at)&&Number(p.ts)<=f.finish),3600)).map(d=>`<path data-comparison-line="forecast" d="${d}" fill="none" stroke="var(--history-return)" stroke-width="2" stroke-dasharray="5 4"/>`).join('');
    const actualPaths=paths(actual,1200).map(d=>`<path data-comparison-line="actual" d="${d}" fill="none" stroke="var(--history-room)" stroke-width="2.5"/>`).join('');
    const comfort=paths(actual.map(p=>({...p,temperature:p.lower})),1200).concat(paths(actual.map(p=>({...p,temperature:p.upper})),1200))
      .map(d=>`<path data-comparison-comfort d="${d}" fill="none" stroke="var(--muted)" opacity=".6" stroke-dasharray="2 5"/>`).join('');
    const events=[...(data?.markers||[]),...forecasts.flatMap(f=>f.points.flatMap(p=>(p.heat_segments||[]).map(s=>({...s,kind:'planned_heat'}))))];
    const labels={actual_heat:'Фактичний нагрів',planned_heat:'Запланований нагрів',fireplace_heat:'Працював камін',grid_off:'Мережі немає',unknown:'Немає достовірних даних',model_change:'Зміна моделі'};
    const markers=events.filter(m=>Number(m.end)>=start&&Number(m.start)<=end).map(m=>{
      const a=x(Math.max(start,Number(m.start))),b=x(Math.min(end,Number(m.end))),kind=m.kind;
      const title=`${labels[kind]||kind} · ${this.time(Number(m.start),true)}${kind==='model_change'?' · v'+m.model_version:''}`;
      if(kind==='model_change')return `<path data-comparison-marker="${h(kind)}" d="M${a} ${top}V${bottom}" stroke="var(--muted)" stroke-dasharray="3 5"><title>${h(title)}</title></path>`;
      const lane=kind==='planned_heat'?bottom-12:kind==='actual_heat'?bottom-6:kind==='fireplace_heat'?bottom-18:top;
      const color=kind==='actual_heat'?'var(--source-red)':kind==='planned_heat'?'var(--history-return)':kind==='fireplace_heat'?'var(--fireplace, #d97706)':'var(--muted)';
      return `<rect data-comparison-marker="${h(kind)}" x="${a}" y="${lane}" width="${Math.max(2,b-a)}" height="${kind==='grid_off'||kind==='unknown'?bottom-top:4}" fill="${color}" opacity="${kind==='grid_off'||kind==='unknown'?'.10':'.85'}"><title>${h(title)}</title></rect>`;
    }).join('');
    const cursor=numeric(this.comparisonCursor),cross=cursor!==null?`<line x1="${x(cursor)}" x2="${x(cursor)}" y1="${top}" y2="${bottom}" stroke="currentColor" stroke-dasharray="3 4"/>`:'';
    const svg=`<div class="comparison-plot" tabindex="0" role="img" aria-label="Очікувана і фактична температура за вибрані дати. Стрілки змінюють час."><svg viewBox="0 0 ${width} ${height}" width="100%" height="${height}"><defs><clipPath id="comparison-clip"><rect x="${left}" y="${top}" width="${width-left-right}" height="${bottom-top}"/></clipPath></defs>${grid}${ticks}<g clip-path="url(#comparison-clip)">${markers}${comfort}${forecastPaths}${actualPaths}${cross}</g></svg></div><div class="comparison-zoom" role="group" aria-label="Масштаб графіка"><button data-comparison-zoom="in" aria-label="Збільшити масштаб">+</button><button data-comparison-zoom="out" aria-label="Зменшити масштаб">−</button><button data-comparison-zoom="reset">Увесь період</button></div>`;
    const nearest=points=>points.filter(p=>numeric(p.temperature)!==null).reduce((a,p)=>!a||Math.abs(Number(p.ts)-cursor)<Math.abs(Number(a.ts)-cursor)?p:a,null);
    const measured=cursor===null?null:nearest(actual),expected=cursor===null?null:nearest(forecasts.flatMap(f=>cursor>=Number(f.cycle.started_at)&&cursor<=f.finish?f.points:[]));
    const readout=cursor===null?'Торкніться графіка, щоб порівняти значення.':`${this.time(cursor,true)} · Очікувана ${fmt(expected&&Math.abs(Number(expected.ts)-cursor)<=1800?expected.temperature:null)} °C · Фактична ${fmt(measured&&Math.abs(Number(measured.ts)-cursor)<=600?measured.temperature:null)} °C`;
    const missing=cycles.filter(c=>!c.forecast).length;
    const coverage=forecasts.reduce((sum,f)=>sum+f.points.slice(1).reduce((n,p,i)=>{
      const previous=f.points[i];return n+(Number(p.ts)-Number(previous.ts)<=3600?Math.max(0,Math.min(Number(p.ts),f.finish)-Math.max(Number(previous.ts),start,Number(f.cycle.started_at))):0);
    },0),0);
    const status=this.comparisonError||data?.error||(this.comparisonPending?'Читаємо збережені дані… Якщо відповідь не надійде за 25 секунд, з’явиться кнопка повтору.':!actual.length?'Фактичних вимірів за цей період немає.':!forecastPaths?'Завчасних прогнозів за цей період не збережено. Показано фактичну температуру.':'');
    const sources=`<details class="comparison-sources"><summary>Джерела прогнозу · ${cycles.filter(c=>c.forecast).length} з ${cycles.length} циклів</summary><p class="muted note">Пунктир — незмінний прогноз, виданий щонайменше за 12 годин до контрольного початку вікна або підтвердженого нагріву. Фактичну давність наведено нижче. Нові перерахунки його не замінюють. Пропуски не відновлено заднім числом.</p>${cycles.map(c=>`<p class="comparison-source"><strong>${h(this.time(Number(c.started_at),true))}</strong><span>${c.forecast?`Прогноз від ${h(this.time(Number(c.forecast.at),true))} · за ${fmt((Number(c.started_at)-Number(c.forecast.at))/3600,2)} год${c.forecast.source==='archive'?' · оригінал з архіву':''}`:'Завчасний прогноз не збережено'}</span></p>`).join('')}${data?.warnings?.length?'<p class="warn note">Частина архіву недоступна. Відсутні ділянки залишено порожніми.</p>':''}</details>`;
    return this.section('Прогноз і фактична температура',controls+svg+`<div class="comparison-legend"><span>┄ Завчасний прогноз · від 12 год</span><span>━ Фактична температура</span><span>··· Межі комфорту</span><span>Смужки: план / котел / камін · тло: мережа / прогалини</span></div><p class="comparison-readout muted" aria-live="polite">${h(readout)}</p>${status?`<p class="comparison-status ${this.comparisonError||data?.error?'warn':''}" role="status">${h(status)}</p>`:''}${data&&coverage<(end-start)-300?`<p class="comparison-coverage muted">Збережений прогноз: ${fmt(coverage/3600,1)} з ${fmt((end-start)/3600,0)} год. Решта — без завчасних даних.</p>`:''}${missing?`<p class="muted note">Без завчасного прогнозу: ${missing} циклів.</p>`:''}`+sources,'comparison-card');
  }
  overview() {
    const c=this.attrs('controller'),budget=numeric(this.state('monthly_budget')),used=numeric(this.state(this.attrs('system_settings').values?.power_accounting_method==='inlet'?'energy_accounted_monthly':'energy_monthly'));
    return `<div class="overview">${this.forecastOverview()}<div class="hero card"><div><h1 class="eyebrow">Комфорт у вашому домі</h1><div class="temperature">${fmt(this.state('temperature'))}<span>°C</span></div><p class="hero-target"><span>${h(c.comfort_zone||'Поточний комфорт')}</span><span>Ціль <strong>${fmt(c.target_temperature)} °C</strong></span></p><button class="hero-outside" data-info="${h(this.id('outside'))}"><span>Надворі</span> <strong>${fmt(this.state('outside'))} °C</strong></button></div><div class="hero-side"><span class="eyebrow">Зараз у будинку</span><h2>${h(this.overviewDecision())}</h2><p class="muted">${(this.state('mode')||c.mode)==='Спостереження'?'Автокерування':this.boilerActual().heating===true?'Поточний нагрів':'Наступний нагрів'} <strong>${h(this.nextHeatText())}</strong></p><div class="comfort">Очікуваний мінімум <strong>${this.controllerHealth().stale?'—':fmt(this.state('forecast'))} °C</strong></div><div class="comfort">Межі <strong>${fmt(c.lower_temperature)}–${fmt(c.upper_temperature)} °C</strong></div></div></div>${this.energyComparison()}${this.heatingPlan()}<details class="card overview-details"><summary>Детальніше · котел, камін та історія</summary><div class="heat-sources">${this.boilerOverview()}${this.fireplaceOverview()}</div><div class="columns"><div>${this.section('Температура й нагрів',this.historyChart(),'history-card')}${this.section('Енергія котла по годинах','<div class="native" data-native="energy"></div>')}</div><div>${this.section('Енергія за місяць',`<div class="large">${fmt(used,1)} <small>кВт·год</small></div><progress max="${budget!==null&&budget>0?budget:1}" value="${used??0}" aria-label="Місячна енергія"></progress><p class="muted">Орієнтир ${fmt(budget,0)} кВт·год${budget&&used!==null?' · '+fmt(used/budget*100,0)+' %':''}</p>`)}${this.section('Погода на наступну добу','<div class="native" data-native="weather"></div>'+this.field('weather_refresh','Оновити погодинну погоду'))}</div></div></details></div>`;
  }
  settings() {
    this.syncSystemSettings();
    const fields=items=>items.map(([key,label])=>this.field(key,label)).join('');
    const tuning=(key,fallback)=>this.systemFields().find(f=>f.key===key)?.value??fallback;
    return `<div class="settings-page compact-page"><div class="page-title"><h1>Налаштування</h1></div><div class="settings-grid">
    ${this.section('Керування опаленням',`${this.field('mode','Режим системи')}<div class="control-grid">${fields([['armed','Дозволити команди'],['restore_once','Нагрів після появи мережі']])}</div><div class="settings-status">${this.row('Контролер',this.state('controller'))}${this.row('Команди',this.state('permitted')==='on'?'Дозволені':'Не дозволені')}</div>`)}
    ${this.section('Комфорт',`<div class="comfort-groups"><fieldset><legend>День</legend><div class="control-grid thirds">${fields([['target','Ціль'],['lower','Мінімум'],['upper','Максимум']])}</div></fieldset><fieldset><legend>Під час сну</legend><div class="control-grid thirds">${fields([['night_target','Ціль'],['night_lower','Мінімум'],['night_upper','Максимум']])}</div><div class="control-grid">${fields([['sleep_start','Початок сну'],['sleep_end','Кінець сну']])}</div></fieldset></div>`, 'comfort-settings')}
    ${this.section('Години нагріву',`${this.field('strategy','Планування нагріву')}<div class="control-grid">${fields([['start','Дозволено з'],['end','Дозволено до']])}</div>${this.row('Одноразовий запит',this.state('restore_status'))}`)}
    ${this.section('Циркуляція',`<div class="circulation-config"><div class="control-grid circulation-mode">${fields([['fan_control','Автоматичні команди'],['fan_mode','Режим циркуляції']])}</div><div class="control-grid thirds circulation-levels">${fields([['fan_night_limit','1 · Ніч'],['fan_speed','2 · День стандарт'],['fan_intense_speed','3 · День інтенсив']])}</div></div><details class="inline-help"><summary>Коли змінюється рівень</summary><p>${h(tuning('fan_night_start','23:00'))}–${h(tuning('fan_night_end','07:00'))} — рівень 1. Удень при інтенсивному горінні — 3, за інших умов — 2. Нічна швидкість не перевищує стандартну, інтенсивна — не нижча за неї.</p><p>Запуск при різниці від ${fmt(tuning('fire_start_delta',5))} °C; нижче ${fmt(tuning('fire_stop_delta',3))} °C протягом ${fmt(tuning('fire_cooldown_seconds',300)/60)} хв — зупинка. При різниці нижче ${fmt(tuning('fire_fast_stop_delta',4))} °C і тренді ${fmt(tuning('fire_fast_stop_slope',-2))} °C/год або нижче — зупинка без затримки.</p></details>`, 'circulation-settings','fireplace-settings')}
    </div><details class="card additional-settings"><summary>Додаткові налаштування · котел і вентиляція</summary><div class="settings-grid">    ${this.section('Котел',`<div class="control-grid">${this.equipmentFields(['boiler_power_mode'])}${this.field('native_setpoint','Уставка штатного резерву')}${fields([['monthly_budget','Місячний орієнтир'],['session_limit','Ліміт сеансу, хв']])}</div><p class="muted note">Вкажіть режим на самому котлі. Цей вибір не перемикає обладнання.</p>${this.equipmentSave()}`)}
    ${this.section('Вентиляція',`<div class="control-grid">${this.equipmentFields(['recuperator_entity','bath_entity'])}</div><p class="muted note">Враховувати роботу цих пристроїв у прогнозі опалення.</p>${this.equipmentSave()}`)}
    </div></details>${this.systemSettings()}</div>`;
  }
  systemSettings() {
    const a=this.attrs('system_settings'),fields=this.systemFields().filter(f=>!['boiler_power_mode','recuperator_entity','bath_entity'].includes(f.key));
    this.syncSystemSettings();
    const groups=[...new Set(fields.map(f=>f.group))];
    const helpButton=(key,label)=>`<button type="button" class="system-help" data-system-help="${h(key)}" aria-label="Підказка: ${h(label)}">?</button>`;
    const rows=fields.map(f=>{
      const value=Object.hasOwn(this.systemDraft,f.key)?this.systemDraft[f.key]:f.value;
      const common=`data-system-key="${h(f.key)}" aria-label="${h(f.label)}" ${this.systemPending?'disabled':''}`;
      const control=['select','entity','json'].includes(f.kind)?this.equipmentControl(f):f.kind==='readonly'?`<span class="system-fixed">${h(value)} ${h(f.unit||'')}</span>`:
        `<div class="number"><input type="${f.kind==='time'?'time':'number'}" ${common} value="${h(value)}" ${f.kind==='time'?'':`min="${h(f.min)}" max="${h(f.max)}" step="${h(f.step)}"`}><span>${h(f.unit||'')}</span></div>`;
      return {group:f.group,html:`<div class="system-field"><div class="system-label"><label>${h(f.label)}</label>${helpButton(f.key,f.label)}</div>${control}</div>`};
    });
    const existing=[['target','Ціль удень'],['lower','Мінімум удень'],['upper','Максимум удень'],['night_target','Ціль уві сні'],['night_lower','Мінімум уві сні'],['night_upper','Максимум уві сні'],['sleep_start','Початок сну'],['sleep_end','Кінець сну'],['start','Дозволений початок нагріву'],['end','Дозволений кінець нагріву'],['monthly_budget','Місячний орієнтир енергії'],['session_limit','Ліміт одноразового нагріву'],['native_setpoint','Уставка штатного auto'],['fan_night_limit','Швидкість 1 · ніч'],['fan_speed','Швидкість 2 · стандарт'],['fan_intense_speed','Швидкість 3 · інтенсив'],['fan_mode','Режим циркуляції'],['mode','Режим опалення'],['strategy','Стратегія нагріву'],['armed','Дозвіл команд котлу'],['restore_once','Одноразовий нагрів після мережі'],['fan_control','Дозвіл команд циркуляції'],['fan_backup','Підтвердження резерву циркуляції'],['ai_auto_apply','Застосування перевірених пропозицій ШІ']];
    this.systemExisting=existing;
    const helpers=existing.map(([key,label])=>`<div class="system-field"><div class="system-label"><span>${h(label)}</span>${helpButton('entity:'+key,label)}</div><button class="system-entity" data-info="${h(this.id(key))}" ${this.entity(key)?'':'disabled'}>${h(this.state(key))} <small>${h(this.attrs(key).unit_of_measurement||'')} ↗</small></button></div>`).join('');
    return `<details class="card system-settings"><summary>Системні налаштування <span aria-hidden="true">+</span></summary><div class="system-body"><details class="system-group"><summary>Температури, режими й ліміти</summary><div class="system-grid">${helpers}</div></details>${groups.map(group=>`<details class="system-group"><summary>${h(group)}</summary><div class="system-grid">${rows.filter(r=>r.group===group).map(r=>r.html).join('')}</div></details>`).join('')}${fields.length?'':'<p class="muted">Розширені параметри будуть доступні після оновлення контролера. Чинні helpers можна редагувати вище.</p>'}<div class="system-actions">${this.systemUndo?`<button data-system-undo ${this.systemPending?'disabled':''}>Попередні значення</button>`:''}<span class="system-message" role="status">${h(this.systemMessage||'')}</span><button data-system-reset ${!Object.keys(this.systemDraft).length||this.systemPending?'disabled':''}>Скасувати зміни</button><button data-system-save ${!Object.keys(this.systemDraft).length||this.systemPending?'disabled':''}>${this.systemPending?'Очікуємо контролер…':'Зберегти зміни'}</button></div></div></details>${this.systemHelp?this.systemHelpDialog(this.systemFields()):''}`;
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
    return `<dialog class="system-dialog" aria-labelledby="system-help-title"><div class="system-dialog-heading"><h2 id="system-help-title">${h(label)}</h2><button data-system-close aria-label="Закрити підказку">×</button></div><p>${h(f?.help||existingHelp[entityKey]||'Чинний параметр Home Assistant.')}</p>${f&&f.kind!=='readonly'?`<p class="muted">За замовчуванням: ${h(typeof f.default==='object'?JSON.stringify(f.default):f.default)} ${h(f.unit||'')}${f.kind==='number'?` · Межі: ${h(f.min)}–${h(f.max)}`:''}</p>`:''}<p>${h(instruction)}</p></dialog>`;
  }
  cycleHistory() {
    const data=this.comparisonData,cycles=(data?.energy_cycles||[]).filter(c=>Number(c.end)<=Date.now()/1000).sort((a,b)=>Number(b.end)-Number(a.end));
    const body=cycles.map(c=>{
      const base=c.initial_plan||c.baseline,report=c.quality_report||{},amount=(numeric(c.observed_kwh)??0)+(numeric(c.calculated_kwh)||0),complete=isTrue(c.complete),known=!complete&&amount===0&&numeric(c.unknown_seconds)>0?null:amount;
      const difference=known!==null&&numeric(base?.kwh)!==null?known-Number(base.kwh):null;
      const reviews=(data.reviews||[]).filter(r=>r.cycle_id===c.cycle_id);
      return `<div class="cycle-history-entry"><h3>${h(this.time(Number(c.start),true))} → ${h(this.time(Number(c.end),true))}</h3><div class="fact-grid">${this.compactMetric('План · початкова база',fmt(base?.kwh,2),'кВт·год')}${this.compactMetric('Актуальний план',fmt(c.latest_plan?.kwh,2),'кВт·год')}${this.compactMetric(numeric(c.calculated_kwh)>0?'Облік · розрахунок':known===null?'Факт · немає даних':complete?'Факт':'Факт · частково',fmt(known,2),'кВт·год')}${this.compactMetric('Відхилення',difference===null||!complete&&difference<=0?'—':(numeric(c.calculated_kwh)>0?'≈ ':!complete?'≥ ':'')+fmt(difference,2),'кВт·год')}</div><p class="muted note">${h(report.attribution||(difference>0?'Причина не встановлена':'Перевищення не підтверджено'))}. Невизначені виміри: ${fmt(Number(c.unknown_seconds||0)/60,1)} хв. Джерело енергії — розрахунковий баланс; незалежного лічильника котла немає.</p>${this.row('За автоматичним запитом',fmt((numeric(c.automatic_kwh)||0)+(numeric(c.calculated_automatic_kwh)||0),2)+' кВт·год')}${this.row('Без автоматичного запиту',fmt((numeric(c.other_kwh)||0)+(numeric(c.calculated_other_kwh)||0),2)+' кВт·год')}${reviews.length?`<details class="cycle-reviews"><summary>Зміни плану · ${reviews.length}</summary>${reviews.map(r=>`<p class="muted note">${h(this.time(Number(r.at),true))}: зміна оцінки ${fmt(r.energy_increase_kwh,2)} кВт·год; прогнозоване зменшення дефіциту ${fmt(r.projected_deficit_reduction,3)} °C·год. ${h(r.reason||'Порівняння послідовних прогнозів; причинний внесок не визначено.')}</p>`).join('')}</details>`:'<p class="muted note">Докладний журнал змін для цього старого циклу не зберігався.</p>'}</div>`;
    }).join('');
    return `<details class="card cycle-history"><summary>Завершені цикли за обраний період · ${cycles.length}</summary>${body||'<p class="muted note">Завершених циклів із збереженим енергетичним обліком за цей період немає.</p>'}</details>`;
  }
  controllerQuality() {
    const q=this.attrs('controller_quality'),last=q.last_completed_cycle,horizons=Array.isArray(q.horizons)?q.horizons:[];
    const archive=q.archive_health,used=numeric(archive?.used_bytes),limit=numeric(archive?.max_bytes);
    const storage=used!==null&&limit>0?`<p class="${isTrue(archive.pressure)?'warn':'muted'} note">Архів: ${fmt(used/limit*100,1)}% ліміту · ${fmt(used/1048576,1)} / ${fmt(limit/1048576,1)} МіБ. ${isTrue(archive.pressure)?'Наближається ліміт журналу; захищена історія зберігається. ':''}Вільно на диску: ${fmt(numeric(archive.free_bytes)===null?null:archive.free_bytes/1073741824,1)} ГіБ.</p>`:'';
    const table=`<div class="table-scroll"><table><thead><tr><th>Горизонт</th><th>Похибка °C</th><th>Перевірок / днів</th><th>Похибка кВт·год</th></tr></thead><tbody>${(horizons.length?horizons:[1,3,6,12,24].map(hours=>({hours}))).map(r=>`<tr><td>${fmt(r.hours,0)} год</td><td>${fmt(r.temperature_mae,3)}</td><td>${fmt(r.count??0,0)} / ${fmt(r.days??0,0)}${r.pending?` · очікує ${fmt(r.pending,0)}`:''}</td><td>${fmt(r.energy_mae,2)}</td></tr>`).join('')}</tbody></table></div>`;
    const report=last?`<p class="muted note">Цикл ${h(this.time(Number(last.start),true))} — ${h(this.time(Number(last.end),true))}</p><div class="fact-grid">${this.compactMetric('Відхилення енергії',(isTrue(last.energy_error_is_lower_bound)&&numeric(last.energy_error_percent)!==null?'≥ ':'')+fmt(last.energy_error_percent,1),'%')}${this.compactMetric('Поза комфортом',fmt(last.comfort_violation_minutes,1),'хв')}${this.compactMetric('Перегрів',fmt(last.overheat_minutes,1),'хв')}${this.compactMetric('Покриття датчика',numeric(last.sensor_coverage)===null?'—':fmt(last.sensor_coverage*100,0),'%')}</div><p class="muted note">${h(last.attribution||'Причина відхилення не встановлена')}${isTrue(last.energy_complete)?'':' · енергія врахована частково'}. ${last.significant_replans===null||last.significant_replans===undefined?'Історію перепланувань цього циклу не збережено.':`Суттєвих змін плану: ${fmt(last.significant_replans,0)}.`}</p>`:'<p class="muted note">Щоденний підсумок з’явиться після завершення циклу збереження даних.</p>';
    return this.section('Точність і якість роботи',table+`<p class="muted note">Незалежні перевірки виданих прогнозів. «—» означає, що спостережень ще недостатньо. Підтвердження короткого горизонту не доводить точності на добу.</p>`+report+storage,'controller-quality');
  }
  model() {
    const a=this.attrs('model'),env=this.attrs('environment'),p=a.power_learning||{},s=this.attrs('summary').last_24h||{},f=this.attrs('forecast');
    const horizons=this.attrs('controller_quality').horizons||[],hour=horizons.find(r=>r.hours===1),energy=horizons.find(r=>r.hours===6);
    const facts=items=>items.map(([label,value,unit])=>this.compactMetric(label,value,unit||'')).join('');
    const v=a.forecast_validation||{},groups=Array.isArray(v.groups)?v.groups:[];
    const kinds={frozen_plan:'План і виконання',measured_inputs:'Модель / фактичні входи',post_stop_peak:'Пік після stop'};
    const rejects={unknown_measurement:'Невідомий вимір',measurement_gap:'Прогалина',control_mode_changed:'Інший режим',model_changed:'Інша модель',execution_changed:'Змінене виконання',energy_mismatch:'Інша енергія',counter_reset:'Скидання лічильника',counter_jump:'Стрибок лічильника',restart_gap:'Перезапуск',reheated_before_peak_window:'Новий нагрів до завершення піку'};
    const validation=`<div class="fact-grid">${facts([['Часове покриття',numeric(s.coverage)===null?'—':fmt(s.coverage*100,1),'%'],['Вік кімнатного виміру',fmt(numeric(s.room_measurement_age_seconds)===null?null:s.room_measurement_age_seconds/60,1),'хв'],['Найбільший розрив',fmt(numeric(s.max_gap_seconds)===null?null:s.max_gap_seconds/60,1),'хв'],['Оцінена потужність',numeric(s.estimated_power_fraction)===null?'—':fmt(s.estimated_power_fraction*100,1),'%'],['Нижче / вище меж',`${fmt(s.below_degree_hours,2)} / ${fmt(s.above_degree_hours,2)}`,'°C·год']])}</div><div class="table-scroll validation-table"><table><thead><tr><th>Перевірка</th><th>Горизонт</th><th>MAE °C</th><th>MAE кВт·год</th><th>MAE кВт</th><th>N / днів</th><th>Вік, год</th></tr></thead><tbody>${groups.map(g=>`<tr><td>${h(kinds[g.kind]||g.kind)}</td><td>${g.horizon==='required'?'Потрібний · ':''}${fmt(g.minimum_hours,1)}${numeric(g.maximum_hours)!==numeric(g.minimum_hours)?`–${fmt(g.maximum_hours,1)}`:''} год</td><td>${fmt(g.mae,3)}</td><td>${fmt(g.energy_mae,2)}</td><td>${fmt(g.power_mae,2)}</td><td>${fmt(g.count,0)} / ${fmt(g.days,0)}</td><td>${fmt(g.age_hours,1)}</td></tr>`).join('')||'<tr><td colspan="7">Ще немає завершених перевірок.</td></tr>'}</tbody></table></div>${Object.keys(v.rejected||{}).length?`<p class="muted note">Відсіяні перевірки: ${Object.entries(v.rejected).map(([k,n])=>`${h(rejects[k]||k)} — ${fmt(n,0)}`).join(' · ')}</p>`:''}`;
    const updated=this.entity('ai_report')?.last_changed;
    const attempt=a.learning_attempt;
    const learning=attempt?`Остання спроба навчання ${this.time(attempt.time,true)}: ${{rejected:'кандидат відхилено',pending:'кандидат перевіряється',applied:'кандидат застосовано',proposed:'пропозицію перевірено',initial_validation:'початкова перевірка',storage_error:'помилка збереження'}[attempt.status]||'стан невідомий'}; ${isTrue(a.parameters?.validated)?'чинна модель працює':'модель збирає дані'}. ${attempt.reason||''}`:a.last_result||'Навчання ще не виконувалось';
    return `<div class="model-page compact-page"><div class="page-title"><h1>Історія</h1></div>${this.temperatureComparison()}<section class="card model-stats">${this.compactMetric('Модель v'+(a.parameters?.version??'—'),isTrue(a.parameters?.validated)?'Працює':'Навчається','','model')}${this.compactMetric('Похибка прогнозу · 1 год',fmt(hour?.temperature_mae,3),'°C','model')}${this.compactMetric('Похибка енергії · 6 год',fmt(energy?.energy_mae,2),'кВт·год')}${this.compactMetric('Покриття вимірів',numeric(s.coverage)===null?'—':fmt(s.coverage*100,0),'%')}</section><div class="model-grid">${this.controllerQuality()}${this.cycleHistory()}
    ${this.section('Аналітичний звіт ШІ',`<p class="report-text">${h(this.state('summary'))}</p><div class="report-meta muted">Оновлено ${h(this.time(updated,true))}</div>${!Number.isFinite(Date.parse(updated))||Date.now()-Date.parse(updated)>420000?`<p class="warn pill">${Number.isFinite(Date.parse(updated))?'Дані звіту застаріли':'Час створення звіту не збережено'}</p>`:''}<div class="report-stats">${facts([['Охоплення',fmt(s.hours_collected),'год'],['Придатні проби',`${fmt(s.valid_samples,0)} / ${fmt(s.samples,0)}`],['Часове покриття',numeric(s.coverage)===null?'—':fmt(s.coverage*100,0),'%']])}</div>`, 'ai-report')}
    ${this.section('Результат навчання',`<p class="learning-result">${h(learning)}</p>${a.learning_storage_error?`<p class="warn note">Помилка збереження навчання: ${h(a.learning_storage_error)}</p>`:''}<div class="fact-grid">${facts([['Останній аналіз',this.time(a.last_analysis,true)],['Останній успіх',this.time(a.learning_success?.time,true)],['Кількість проб',fmt(a.samples,0)]])}</div>`)}
    <details class="card model-tools"><summary>Керування моделлю й діагностика</summary><div class="model-grid">${this.section('Дії',`${this.field('ai_auto_apply','Застосовувати перевірені пропозиції')}<div class="action-buttons">${this.field('rollback','Попередня модель')}${this.field('weather_refresh','Оновити погоду')}</div><div class="report-meta muted">Погодинні дані до ${h(this.time(f.weather_known_until,true))}</div>`, 'model-actions')}
    <details class="card modulation-details"><summary>Модуляція котла</summary>${this.section('Модуляція котла',`<p class="model-meta muted">${h(a.power_learning_status||'')} · ${fmt(p.intervals,0)} інтервалів · ${fmt(p.minutes,0)} хв</p><div class="table-scroll"><table><thead><tr><th>Обратка</th><th>Потужність</th><th>Досвід</th></tr></thead><tbody>${(p.bands||[]).map(b=>`<tr><td>${fmt(b.return_min,0)}–${fmt(b.return_max,0)} °C</td><td>${fmt(b.measured_kw)} кВт</td><td>${fmt(b.minutes,0)} хв</td></tr>`).join('')||'<tr><td colspan="3">Очікуємо підтверджених сеансів.</td></tr>'}</tbody></table></div>`)}</details>
    ${this.section('Підлога й погода',`<div class="fact-grid">${facts([['Виміряна обратка',fmt(env.raw_return),'°C'],['Зовнішній прогрів',fmt(env.external_gain_c_per_hour,3),'°C/год'],['Сонячна складова',isTrue(env.solar_validated)?'Перевірена':'Навчається'],['Днів спостережень',fmt(env.solar_learning_days,0)],['Поточна похибка',fmt(a.reliability?.recent_mae,2),'°C'],['Корекція погоди',fmt(f.local_weather_bias),'°C']])}</div>${isTrue(a.reliability?.degraded)?`<p class="warn pill">Точність знижена · запас ${fmt(env.upper_margin)} °C</p>`:''}`)}
    ${this.energyComparison(true)}<details class="card execution-details"><summary>План і виконання · докладно</summary>${this.heatingPlan(true)}</details><details class="card power-diagnostics"><summary>Вимірювання потужності й вентиляція</summary>${this.powerDetails()}</details>${this.section('Журнал рішень',`<div class="journal">${[...(a.journal||[])].reverse().map(r=>`<div class="entry"><time>${h(this.time(r.time,true))}</time><p>${h(r.reason)}</p></div>`).join('')||'<p class="empty">Журнал ще порожній.</p>'}</div>`, 'model-journal')}
    <section class="card model-advanced"><details><summary>Параметри й діагностика</summary>${validation}<div class="parameter-grid">${Object.entries(a.parameters||{}).map(([key,value])=>this.row(key,typeof value==='number'?fmt(value,4):value)).join('')}</div><div class="fact-grid controller-facts">${facts([['Процес AppDaemon',this.state('process')==='on'?'Працює':'Не підтверджено'],['Контур керування',this.attrs('diagnostics').control_health||'Немає даних'],['Команди',this.state('permitted')==='on'?'Дозволені':'Не дозволені'],['Останній цикл',this.time(this.state('heartbeat'),true)]])}</div><p class="muted note">${h(this.controllerHealth().details)}</p><p class="muted note">Внутрішній режим Tenko не читається через зовнішній термостат. Запас тепла — еквівалент, а не температура поверхні підлоги. Зовнішній прогрів сам по собі не доводить сонячний внесок.</p><p class="muted note">Прогноз враховує енергію та модуляцію; початкова оцінка для невивчених температур ${fmt(f.power_prior_kw)} кВт. Годинна похибка не підтверджує довгий горизонт; метод, кількість і вік оцінок наведено окремо.</p></details></section></div></details>
    </div></div>`;
  }
  controllerHealthReason(c,d) {
    const missing=Array.isArray(c.missing_inputs)?c.missing_inputs:[];
    const labels={return:'датчик температури обратки',room:'кімнатний датчик температури',outdoor:'зовнішній датчик температури',power:'вимірювання потужності',grid:'підтвердження електромережі'};
    if(missing.length){
      const names=missing.map(key=>labels[key]||key).join(', ');
      return {severity:'warning',message:`Увага: ${names} не передає свіжі дані. Автоматичні рішення Heating AI тимчасово призупинені; котел залишено у штатному AUTO. Перевірте датчик і його доступність у Home Assistant.`};
    }
    const raw=String(c.error||d.control_error||c.reason||'');
    if(/LedgerConflict|fenced/i.test(raw))return {severity:'error',message:'Безпечне керування тимчасово призупинено після помилки виконання. Котел залишено у безпечному режимі; контролер повторить перевірку автоматично.'};
    if(raw)return {severity:'error',message:`Помилка контролера: ${raw.replace(/^Помилка циклу:\\s*/i,'')}. Автоматичні команди тимчасово не надсилаються.`};
    return {severity:'warning',message:''};
  }
  controllerHealth() {
    const c=this.attrs('controller'),d=this.attrs('diagnostics'),f=this.attrs('forecast');
    const beat=Date.parse(this.state('heartbeat')),fresh=Number.isFinite(beat)&&Date.now()-beat<90000;
    const fallback=c.control_method==='temporary_native_auto'||d.control_health==='fallback';
    const failure=d.control_health==='error'||c.control_state==='error';
    const age=Date.parse(f.created_at),stale=this.equipmentPlanPending()||!Number.isFinite(age)||Date.now()-age>420000;
    const reason=this.controllerHealthReason(c,d);
    const manualAuto=this.state('mode')==='Штатний auto';
    const deficit=f.morning_reserve?.comfort_deficit;
    const deficitText=!stale&&isTrue(f.weather_complete)&&isTrue(deficit?.coverage_complete)&&numeric(deficit.first_comfort_violation_at)!==null?
      `${this.time(Number(deficit.first_comfort_violation_at),true)} очікується ${fmt(deficit.first_violation_temperature,Number(deficit.first_violation_shortfall)<.05?3:2)} °C — нижче мінімуму ${fmt(deficit.first_violation_lower,Number(deficit.first_violation_shortfall)<.05?3:2)} °C на ${fmt(deficit.first_violation_shortfall,Number(deficit.first_violation_shortfall)<.05?3:2)} °C${isTrue(deficit.outside_visible_horizon)?' · поза видимими 24 годинами':''}.`:'';
    const emergency=this.attrs('outages');
    const sensor=this.attrs('temperature').source_quality||{};
    const power=this.attrs('power_confirmed'),powerMode=this.attrs('power_model');
    const message=fallback?(reason.message||'Увага: автоматичне керування тимчасово призупинено. Котел залишено у штатному AUTO.'):
      failure?(reason.message||'Помилка контролера: потрібна перевірка. Автоматичні команди тимчасово не надсилаються.'):
      manualAuto?'Ручний «Штатний auto»: Heating AI не надсилає автоматичні команди нагріву.':
      !fresh?'Немає свіжого успішного циклу контролера.':
      this.attrs('grid_status').raw==='off'?'Немає електромережі · нагрів очікує її повернення.':
      this.attrs('grid_status').raw!=='on'?'Нагрів очікує підтвердження електромережі.':
      !isTrue(c.quality)?'Контролеру бракує актуальних вимірювань.':
      isTrue(emergency.emergency)?emergency.warning||'Аварійні відключення: графік не гарантує світло.':
      emergency.mode==='unknown'?emergency.warning||'Джерело графіка мережі недоступне. Майбутні сеанси умовні.':
      d.storage_health==='degraded'?'Не вдалося зберегти частину діагностики — перевірте журнал.':
      d.validation_health==='degraded'?'Перевірка прогнозу обмежена; контур керування продовжує працювати.':
      c.control_power_basis==='inlet_stage_reserve'?'Ввід не підтверджує ступінь котла. План орієнтовний; непевні кВт·год не зараховано.':
      c.control_power_basis==='partial_meter_reserve'?'Резервний облік: один лічильник без свіжих даних. План орієнтовний; фактична енергія неповна.':
      this.equipmentPlanPending()?'Оновлюємо план для обраного профілю котла.':stale?'Прогноз застарів: це попередній сценарій.':
      isTrue(power.power_without_request)?'Є потужність без автоматичного запиту — перевірте котел і вимірювання.':
      isTrue(powerMode.mode_contradiction)?'Виміряна потужність суперечить вибраному режиму котла.':
      sensor.primary_status==='stale'?`Кімнатна температура без нового звіту ${fmt(Number(sensor.primary_age_seconds)/60,0)} хв. Перевірте датчик.`:
      deficitText||(!isTrue(f.weather_complete)&&f.curve?.length?'Погодинна погода неповна · далі використано останню відому температуру.':
      isTrue(f.reliability?.degraded)?`Похибка останніх годинних прогнозів ${fmt(f.reliability.recent_mae,2)} °C.`:'');
    const applied=numeric(d.applied_plan?.applied_at);
    const appliedText=applied===null?'немає підтвердження':`${Math.max(0,Math.floor((Date.now()/1000-applied)/60))} хв тому`;
    const process=this.state('process');
    return {message,fresh,fallback,stale,severity:(fallback||failure)?reason.severity:'info',details:`Процес: ${process==='on'?'працює':process==='off'?'зупинений':'не підтверджено'} · цикл: ${Number.isFinite(beat)?this.time(beat/1000,true):'—'} · застосований план: ${appliedText}`};
  }
  render() {
    if(!this.hass) return;
    const beat=Date.parse(this.state('heartbeat')), alive=this.state('alive')==='on'&&Number.isFinite(beat)&&Date.now()-beat<90000;
    const health=this.controllerHealth();
    const html=`<style>${styles}</style><main><header><div class="brand"><span class="brand-icon">${icon('home')}</span><div><strong>Heating <span>AI</span></strong><small>Клімат вашого дому</small></div></div><div class="header-status"><span class="dot ${alive?'':'red'}"></span>${alive?'Контролер онлайн':'Перевірте контролер'}<span class="mode">${h(this.state('mode'))}</span><button class="menu-button" data-menu aria-label="Відкрити меню Home Assistant">${icon('menu')}</button></div></header><nav aria-label="Розділи клімату">${[['overview','Дім'],['model','Історія'],['settings','Налаштування']].map(([key,label])=>`<button data-tab="${key}" aria-current="${this.tab===key?'page':'false'}" class="${this.tab===key?'selected':''}">${label}</button>`).join('')}<a href="/climate-control">Поточні панелі ↗</a></nav><div class="notice ${this.error?'':'hidden'}" role="alert">${h(this.error || '')}</div><div class="notice ${health.message&&!this.error?'':'hidden'} ${health.severity||''}" role="${health.severity==='error'?'alert':'status'}" data-controller-health>${h(health.message)}<small class="health-details">${this.tab==='overview'?'':h(health.details)}</small></div><div class="content">${this.tab==='settings'?this.settings():this.tab==='model'?this.model():this.overview()}</div><footer>Тепло під контролем <span>Home Assistant · Heating AI ${VERSION}</span></footer></main>`;
    const template=document.createElement('template');template.innerHTML=html;
    patchDOM(this.shadowRoot,template.content);
    const dialog=this.shadowRoot.querySelector('.system-dialog');
    if(dialog&&!dialog.open)dialog.showModal();
    this.observePlot();
    this.mountNative();
    this.loadHistory();
    this.loadComparison();
  }
}
