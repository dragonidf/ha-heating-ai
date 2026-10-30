import test from 'node:test';
import assert from 'node:assert/strict';
globalThis.HTMLElement=class {};
globalThis.customElements={get:()=>false,define:()=>{}};
globalThis.window={};
const {HeatingAIPanel,numeric,escapeHTML,isTrue,mergeSegments,chartLayout,normalizeHistory,activityIntervals,historyPaths}=await import('../frontend/heating-panel.js');
test('saving a boiler mode ignores untouched advanced coefficient step validity',async()=>{
  const panel=Object.create(HeatingAIPanel.prototype);panel.systemDraft={boiler_power_mode:'fixed_6'};panel.render=()=>{};
  panel.shadowRoot={querySelectorAll:()=>[
    {dataset:{systemKey:'boiler_power_mode'},reportValidity:()=>true},
    {dataset:{systemKey:'floor_air'},reportValidity:()=>false}]};
  let requests=0,ack={revision:2};panel.attrs=()=>ack;
  panel._hass={callApi:async(method,path,data)=>{requests++;assert.equal(data.values.boiler_power_mode,'fixed_6');
    ack={revision:3,last_request_id:data.request_id,last_success:'true'};panel.syncSystemSettings();}};
  await panel.saveSystemSettings();clearTimeout(panel.systemTimeout);
  assert.equal(requests,1);assert.deepEqual(panel.systemDraft,{});assert.equal(panel.systemMessage,'Зміни збережено');
});
test('a delayed controller acknowledgement still clears the saved draft',async()=>{
  const panel=Object.create(HeatingAIPanel.prototype);panel.systemDraft={boiler_power_mode:'fixed_6'};panel.render=()=>{};
  panel.shadowRoot={querySelectorAll:()=>[]};let ack={revision:2},timeout;
  panel.attrs=()=>ack;panel._hass={callApi:async()=>{}};
  const previous=globalThis.setTimeout;globalThis.setTimeout=callback=>{timeout=callback;return 0;};
  try{await panel.saveSystemSettings();}finally{globalThis.setTimeout=previous;}
  const request=panel.systemPending;timeout();
  ack={revision:3,last_request_id:request,last_success:'true'};panel.syncSystemSettings();
  assert.deepEqual(panel.systemDraft,{});assert.equal(panel.systemMessage,'Зміни збережено');
});
test('device bindings use existing HA relay choices with friendly names',()=>{
  const panel=Object.create(HeatingAIPanel.prototype);panel.systemDraft={};panel._hass={states:{
    'switch.recuperator':{state:'on',attributes:{friendly_name:'Рекуператор вітальні'}},
    'fan.bath':{state:'unavailable',attributes:{friendly_name:'Витяжка ванної'}},
    'sensor.unrelated':{state:'12',attributes:{friendly_name:'Не пристрій'}}}};
  const html=panel.equipmentControl({kind:'entity',key:'recuperator_entity',label:'Рекуператор',value:'switch.recuperator'});
  assert.match(html,/<select/);assert.doesNotMatch(html,/<input/);
  assert.match(html,/Рекуператор вітальні/);assert.match(html,/Витяжка ванної/);assert.doesNotMatch(html,/sensor.unrelated/);
});
test('boiler advice remains visible when no mode can fully meet comfort',()=>{
  const p=Object.create(HeatingAIPanel.prototype);p.config={};p.state=k=>({power_model:'auto',power_confirmed:'0',boiler_active:'off'})[k];
  p.attrs=k=>k==='power_model'?{recommendation:{status:'model_bound_insufficient',alternatives:[
    {nominal_kw:3,mode:'fixed_3',found:true,deficit_degree_hours:4},
    {nominal_kw:6,mode:'fixed_6',found:true,deficit_degree_hours:3},
    {nominal_kw:9,mode:'fixed_9',found:true,deficit_degree_hours:2}]}}:{};
  const html=p.boilerOverview();assert.match(html,/Для зменшення дефіциту/);assert.match(html,/9 кВт/);
});
test('a corrected equipment JSON draft clears its earlier validation error',async()=>{
  const panel=Object.create(HeatingAIPanel.prototype);panel.systemDraft={};panel.render=()=>{};
  let error='';const field={value:'{',type:'textarea',dataset:{systemKey:'power_phase_topology'},
    hasAttribute:key=>['data-system-key','data-json'].includes(key),reportValidity:()=>!error,setCustomValidity:value=>{error=value;}};
  await panel.changed({target:field});assert.ok(error);assert.deepEqual(panel.systemDraft,{});
  field.value='{"verified":false,"phases":[]}';await panel.changed({target:field});
  assert.equal(error,'');assert.deepEqual(panel.systemDraft,{power_phase_topology:{verified:false,phases:[]}});
});
test('model diagnostics render with legacy attributes and new rollout results',()=>{
  const panel=Object.create(HeatingAIPanel.prototype);
  panel.config={};
  panel.attrs=key=>key==='model'?{forecast_validation:{groups:[{kind:'measured_inputs',horizon:'required',minimum_hours:39,maximum_hours:39,count:10,mae:.2}],rejected:{measurement_gap:2}}}:{};
  panel.entity=()=>undefined;panel.state=()=>'';panel.field=()=>'';
  assert.match(panel.model(),/Потрібний/);
  assert.match(panel.model(),/Прогалина/);
  panel.attrs=()=>({});assert.match(panel.model(),/Ще немає завершених перевірок/);
});
test('history decoder accepts compact HA states and keeps unknown gaps',()=>{
  const rows=normalizeHistory([{s:'22',lu:10},{s:'unavailable',lu:20},{state:'23',last_updated:'1970-01-01T00:00:30Z'}]);
  assert.deepEqual(rows,[{ts:10,state:'22'},{ts:20,state:'unavailable'},{ts:30,state:'23'}]);
});
test('boiler history uses old measured stages before the new binary sensor existed',()=>{
  const old=[{ts:0,state:'off'},{ts:10,state:'9 кВт'},{ts:30,state:'off'}];
  const recent=[{ts:80,state:'off'}];
  assert.deepEqual(activityIntervals(recent,old,0,100),[
    {start:0,end:10,state:'off'},{start:10,end:30,state:'on'},{start:30,end:100,state:'off'}]);
});
test('no recorder history is unknown rather than a full day of off',()=>{
  assert.deepEqual(activityIntervals([],[],0,100),[{start:0,end:100,state:'unknown'}]);
  assert.deepEqual(activityIntervals([{ts:80,state:'off'}],[],0,100),[{start:0,end:80,state:'unknown'},{start:80,end:100,state:'off'}]);
});
test('active primary readings override fallback and unknown readings break lines',()=>{
  assert.deepEqual(activityIntervals([{ts:0,state:'on'},{ts:30,state:'unavailable'}],[],0,50),[{start:0,end:30,state:'on'},{start:30,end:50,state:'unknown'}]);
  assert.deepEqual(activityIntervals([{ts:0,state:'off'}],[{ts:0,state:'9 кВт'}],0,50),[{start:0,end:50,state:'off'}]);
  const paths=historyPaths([{ts:0,state:'22'},{ts:10,state:'unavailable'},{ts:20,state:'23'},{ts:30,state:'24'}],0,40,t=>t,v=>v);
  assert.equal(paths.length,2);
  assert.ok(!paths.some(p=>p.includes('0,22 L20,23')));
});
test('chart keeps a readable height and reduces ticks for narrow containers',()=>{
  for(const width of [258,320,390,480,720,1440]) {
    const g=chartLayout(width);
    assert.equal(g.width,width);assert.ok(g.height>=300);
    assert.ok(g.bottom>g.top);assert.ok(g.width-g.right>g.left);
    assert.equal(g.tickIntervals,width<520?2:4);
  }
});
test('unknown measurements never become zero',()=>{
  for(const value of [null,undefined,'',' ','unknown','unavailable',Infinity])assert.equal(numeric(value),null);
  assert.equal(numeric('0'),0);assert.equal(numeric('-2.1'),-2.1);
});
test('AppDaemon serialised booleans retain their meaning',()=>{
  for(const value of [true,'true','on'])assert.equal(isTrue(value),true);
  for(const value of [false,'false','off',undefined])assert.equal(isTrue(value),false);
});
test('sensor text cannot inject HTML or attributes',()=>{
  assert.equal(escapeHTML('<img onerror="x"> &'), '&lt;img onerror=&quot;x&quot;&gt; &amp;');
});
test('sessions merge without hiding conditional heat and never mutate the forecast',()=>{
  const rows=[{start:20,end:30,conditional:'true'},{start:0,end:10},{start:10,end:20},{start:30,end:40,conditional:true}];
  const original=JSON.stringify(rows);
  assert.deepEqual(mergeSegments(rows),[{start:0,end:20},{start:20,end:40,conditional:'true'}]);
  assert.equal(JSON.stringify(rows),original);
});
test('controller health prioritizes fallback error over generic measurement warning',()=>{
  const panel=Object.create(HeatingAIPanel.prototype);
  const attrs={controller:{control_method:'temporary_native_auto',reason:"Помилка циклу: null record"},diagnostics:{control_health:'fallback'},forecast:{created_at:new Date().toISOString()}};
  panel.attrs=k=>attrs[k]||{};panel.state=k=>k==='heartbeat'?new Date().toISOString():k==='process'?'on':'';panel.time=()=> '12:00';
  let status=panel.controllerHealth();assert.match(status.message,/null record/);assert.match(status.details,/немає підтвердження/);
  attrs.controller={quality:true};attrs.diagnostics={control_health:'healthy',applied_plan:{applied_at:Date.now()/1000}};attrs.grid_status={raw:'on'};
  attrs.forecast.created_at=new Date(Date.now()-600000).toISOString();status=panel.controllerHealth();
  assert.match(status.message,/застарів/);assert.match(status.details,/0 хв/);
  attrs.forecast.created_at=new Date().toISOString();assert.equal(panel.controllerHealth().message,'');
});
