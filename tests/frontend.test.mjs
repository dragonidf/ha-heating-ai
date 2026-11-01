import test from 'node:test';
import assert from 'node:assert/strict';
globalThis.HTMLElement=class {};
globalThis.customElements={get:()=>false,define:()=>{}};
globalThis.window={};
const {HeatingAIPanel,numeric,escapeHTML,isTrue,mergeSegments,chartLayout,normalizeHistory,activityIntervals,historyPaths,calendarMidnight}=await import('../frontend/heating-panel.js');
test('partial-meter reserve is visible without calling assumed watts actual boiler heating',()=>{
  const p=Object.create(HeatingAIPanel.prototype);p.config={};p.entity=()=>null;p.time=()=> '00:00';
  const data={controller:{quality:true,control_power_basis:'partial_meter_reserve'},
    forecast:{created_at:new Date().toISOString(),weather_complete:true},grid_status:{raw:'on'},
    power_confirmed:{assumed_control_w:6000,control_reserve:{valid:true,upper_w:6200}}};
  p.attrs=k=>data[k]||{};p.state=k=>k==='heartbeat'?new Date().toISOString():'unavailable';
  assert.match(p.controllerHealth().message,/Резервний облік/);
  p.powerAdvice=()=>({recommend:{},mode:'6 кВт',recommended:false,partial:false,adviceValue:'—'});
  const markup=p.boilerOverview();assert.match(markup,/Для розрахунку.*6,0/);
  assert.doesNotMatch(markup,/data-heating="true"/);assert.match(markup,/Фактичне споживання не підтверджене/);
  data.power_confirmed.timestamp=new Date(Date.now()-180000).toISOString();
  assert.doesNotMatch(p.boilerOverview(),/Для розрахунку:/);
});
test('quality shows real archive pressure without deleting history or claiming the disk is full',()=>{
  const p=Object.create(HeatingAIPanel.prototype);p.section=(title,body)=>body;
  p.attrs=()=>({archive_health:{used_bytes:257864750,max_bytes:268435456,free_bytes:39350693888,pressure:'true'}});
  const markup=p.controllerQuality();assert.match(markup,/96/);assert.match(markup,/Архів/);
  assert.match(markup,/ліміту/);assert.match(markup,/зберігається/);assert.doesNotMatch(markup,/диск заповнений/);
  p.attrs=()=>({});assert.doesNotMatch(p.controllerQuality(),/NaN|undefined/);
});
test('home energy compares the immutable initial plan with known fact in the same cycle',()=>{
  const p=Object.create(HeatingAIPanel.prototype);p.time=()=> '23:00';p.compactMetric=(label,value)=>`<metric>${label}: ${value}</metric>`;
  p.attrs=()=>({energy_cycles:[{start:Date.now()/1000-3600,end:Date.now()/1000+3600,
    initial_plan:{kwh:10,before_start:true},observed_kwh:12,meter_kwh:15,complete:'false',latest_plan:{kwh:18}}]});
  const markup=p.energyComparison();assert.equal((markup.match(/<metric>/g)||[]).length,2);
  assert.match(markup,/12,00/);assert.doesNotMatch(markup,/15,00|18,00/);assert.match(markup,/частково/);
});
test('home minimum uses the real forecast sensor state rather than an absent attribute',()=>{
  const p=Object.create(HeatingAIPanel.prototype);p.config={};p.pending=new Set();p.entity=()=>null;
  p.attrs=k=>k==='controller'?{quality:true}:{};p.state=k=>k==='forecast'?'21.4':'unavailable';
  p.controllerHealth=()=>({stale:false});
  for(const method of ['energyComparison','forecastOverview','heatingPlan','boilerOverview','fireplaceOverview','historyChart'])p[method]=()=>'';
  assert.match(p.overview(),/Очікуваний мінімум <strong>21,4 °C/);
});
test('history graph is the first model content and labels the actual advance lead',()=>{
  const p=Object.create(HeatingAIPanel.prototype);p.config={};p.attrs=()=>({});p.state=()=> 'unavailable';p.entity=()=>null;
  p.temperatureComparison=()=>'<central-comparison />';p.energyComparison=()=>'';p.heatingPlan=()=>'';
  p.powerDetails=()=>'';p.field=()=>'';p.controllerHealth=()=>({details:''});
  const markup=p.model();assert.ok(markup.indexOf('<central-comparison />')<markup.indexOf('model-stats'));
});
test('completed cycle details keep partial fact and unexplained excess explicit',()=>{
  const p=Object.create(HeatingAIPanel.prototype);p.attrs=()=>({});p.time=()=> '01.11 07:00';
  p.comparisonData={energy_cycles:[{cycle_id:'night',start:1,end:2,initial_plan:{kwh:10},latest_plan:{kwh:14},
    observed_kwh:12,complete:'false',unknown_seconds:600}],reviews:[{cycle_id:'night',at:1,energy_increase_kwh:4,projected_deficit_reduction:.2}]};
  const markup=p.cycleHistory();assert.match(markup,/12,00/);assert.match(markup,/частково/);
  assert.match(markup,/Причина не встановлена/);assert.match(markup,/План/);assert.match(markup,/10,00/);
});
test('comparison zoom preserves the requested archive range and shows evidence markers',()=>{
  const p=Object.create(HeatingAIPanel.prototype);p.comparisonStart=100;p.comparisonEnd=100+7*86400;
  p.comparisonCursor=100+3*86400;p.render=()=>{};p.time=()=> '01.11';p.attrs=()=>({});
  p.shadowRoot={querySelector:()=>null};p.clientWidth=390;
  p.comparisonData={start:p.comparisonStart,end:p.comparisonEnd,actual:[{ts:200,temperature:21,lower:20.5,upper:22}],
    cycles:[],markers:[{kind:'actual_heat',start:300,end:900},{kind:'model_change',start:1000,end:1000,model_version:7}]};
  p.zoomComparison('in');assert.equal(p.comparisonWindow().end-p.comparisonWindow().start,7*86400);
  assert.equal(p.comparisonView().end-p.comparisonView().start,3.5*86400);
  p.zoomComparison('reset');const markup=p.temperatureComparison();
  assert.match(markup,/data-comparison-marker="actual_heat"/);assert.match(markup,/data-comparison-marker="model_change"/);
  assert.match(markup,/data-comparison-comfort/);
});
test('undo stages prior settings and requires saving, and rejects a newer client revision',async()=>{
  const p=Object.create(HeatingAIPanel.prototype);p.systemDraft={boiler_power_mode:'fixed_6'};p.render=()=>{};
  p.shadowRoot={querySelectorAll:()=>[]};let a={revision:2,values:{boiler_power_mode:'auto'}};
  p.attrs=()=>a;p._hass={callApi:async(_,__,payload)=>{a={revision:3,persisted_revision:3,values:{boiler_power_mode:'fixed_6'},last_request_id:payload.request_id,last_success:true};p.syncSystemSettings();}};
  await p.saveSystemSettings();assert.equal(p.systemDraft.boiler_power_mode,undefined);
  p.undoSystemSettings();assert.equal(p.systemDraft.boiler_power_mode,'auto');
  assert.equal(a.values.boiler_power_mode,'fixed_6'); // no service call from Undo
  p.systemDraft={};a.revision=4;p.undoSystemSettings();assert.deepEqual(p.systemDraft,{});
});
test('all missed page pushes recover through REST without a polling delay per page',async()=>{
  const p=Object.create(HeatingAIPanel.prototype);p.comparisonStart=100;p.comparisonEnd=1000;p.comparisonPending='rest';
  p.isConnected=true;p.render=()=>{};p.id=k=>k;p.attrs=()=>({});let current;
  p._hass={callApi:async(method,path,data)=>{
    if(method==='GET')return {attributes:current};
    const offset=data.offset||0;current={protocol:2,request_id:'rest',result_id:'immutable',start:100,end:1000,
      offset,total:3,next_offset:offset<2?offset+1:null,items:[{kind:'actual',value:{ts:100+offset*300,temperature:21}}]};
  }};
  try {
    await p._hass.callApi('POST','',{offset:0});await p.readComparison('rest');
    for(let i=0;i<20;i++)await Promise.resolve();
    assert.equal(p.comparisonPending,null);assert.equal(p.comparisonData.actual.length,3);
  } finally {p.cancelComparison();}
});
test('healthy missing schedule stays neutral but unknown source remains an explicit warning',()=>{
  const p=Object.create(HeatingAIPanel.prototype);let mode='unknown';
  p.state=k=>k==='heartbeat'?new Date().toISOString():'on';p.time=()=> '00:00';
  p.attrs=k=>({controller:{quality:true},forecast:{created_at:new Date().toISOString(),weather_complete:true},
    grid_status:{raw:'on'},outages:{mode,warning:'Джерело Svitlo недоступне'}})[k]||{};
  assert.match(p.controllerHealth().message,/Svitlo/);mode='normal';assert.equal(p.controllerHealth().message,'');
});
test('expert help opens with the compact values schema and no runtime error',()=>{
  const p=Object.create(HeatingAIPanel.prototype);p.systemHelp='room_max_age';p.systemDraft={};
  p.attrs=()=>({schema_version:1,values:{room_max_age:7200}});p.entity=()=>null;p.state=()=> 'unavailable';p.id=x=>x;
  const markup=p.systemSettings();assert.match(markup,/<dialog/);assert.match(markup,/Свіжість кімнатного датчика/);
  assert.equal(p.systemFields().find(f=>f.key==='room_max_age').value,7200);
});
test('paged history ignores duplicate and foreign pages and completes only after every point',()=>{
  const p=Object.create(HeatingAIPanel.prototype);p.comparisonStart=100;p.comparisonEnd=1000;p.comparisonPending='mine';
  p.requestComparisonPage=()=>{};p.render=()=>{};
  const first={protocol:2,request_id:'mine',result_id:'frozen',start:100,end:1000,offset:0,next_offset:2,total:4,
    items:[{kind:'actual',value:{ts:100,temperature:0}},{kind:'actual',value:{ts:200,temperature:null}}]};
  assert.equal(p.acceptComparisonPage(first),false);assert.equal(p.comparisonData,undefined);
  assert.equal(p.acceptComparisonPage(first),false);
  assert.equal(p.acceptComparisonPage({...first,request_id:'another',start:200,offset:2}),false);
  const last={...first,offset:2,next_offset:null,items:[{kind:'cycle',value:{cycle_id:'night',started_at:100,forecast:{at:-43100,plan_id:'original'}}},
    {kind:'forecast',cycle_id:'night',value:{ts:100,temperature:21}}]};
  assert.equal(p.acceptComparisonPage(last),true);assert.equal(p.comparisonPending,null);
  assert.deepEqual(p.comparisonData.actual,[{ts:100,temperature:0},{ts:200,temperature:null}]);
  assert.equal(p.comparisonData.cycles[0].forecast.curve[0].temperature,21);
});
test('comparison range controls are real delegated click targets',async()=>{
  const p=Object.create(HeatingAIPanel.prototype);p.render=()=>{};
  p.loadComparison=async()=>{};
  const el={dataset:{comparisonDays:'7'},hasAttribute:()=>false};
  await p.clicked({target:{closest:selector=>selector.includes('[data-comparison-days]')?el:null}});
  assert.equal(p.comparisonDays,7);
});
test('an in-flight default comparison keeps its bounds across a five-minute clock boundary',()=>{
  const p=Object.create(HeatingAIPanel.prototype);p.comparisonDays=7;
  const original=Date.now;
  try {
    Date.now=()=>1793478899000;const first=p.comparisonWindow();
    Date.now=()=>1793478901000;assert.deepEqual(p.comparisonWindow(),first);
  }finally{Date.now=original;}
});
test('detaching a pending comparison allows the panel to load again after reconnect',()=>{
  const p=Object.create(HeatingAIPanel.prototype);p.comparisonPending='lost-request';p.historyEpoch=0;
  const old=globalThis.cancelAnimationFrame;globalThis.cancelAnimationFrame=()=>{};
  try{p.disconnectedCallback();assert.equal(p.comparisonPending,null);}
  finally{globalThis.cancelAnimationFrame=old;}
});
test('the comparison can load by REST when no websocket acknowledgement reaches the panel',async()=>{
  const p=Object.create(HeatingAIPanel.prototype);p.comparisonStart=100;p.comparisonEnd=1000;p.tab='model';p.isConnected=true;
  p.render=()=>{};p.id=()=> 'sensor.heating_plan_history';p.attrs=()=>({});
  p._hass={callApi:async(method,path,data)=>method==='POST'?{}:{attributes:{comparison:{...p.comparisonWindow(),request_id:p.comparisonPending,actual:[{ts:100,temperature:21}],cycles:[]}}}};
  try{await p.loadComparison();await p.readComparison(p.comparisonPending);assert.equal(p.comparisonData.actual.length,1);assert.equal(p.comparisonPending,null);}
  finally{clearTimeout(p.comparisonTimeout);clearTimeout(p.comparisonPoll);}
});
test('matching shared comparison data redraws even if HA reuses its hass object',()=>{
  const p=Object.create(HeatingAIPanel.prototype);p.config={};p.comparisonStart=100;p.comparisonEnd=1000;
  p.comparisonPending='this-client';p.comparisonCache=new Map();p.nativeCards=new Map();p.toggleAttribute=()=>{};
  p.syncSystemSettings=()=>{};let renders=0;p.render=()=>renders++;
  const hass={states:{'sensor.heating_plan_history':{attributes:{comparison:{start:100,end:1000,request_id:'another-client',actual:[{ts:100,temperature:21}],cycles:[]}}}}};
  p._hass=hass;p.hass=hass;
  assert.equal(renders,1);assert.equal(p.comparisonData.actual.length,1);assert.equal(p.comparisonPending,null);
});
test('a response for another date range cannot replace the requested comparison',()=>{
  const p=Object.create(HeatingAIPanel.prototype);p.comparisonStart=100;p.comparisonEnd=1000;p.comparisonPending='active';
  assert.equal(p.acceptComparison({start:200,end:1100,actual:[],cycles:[]}),false);
  assert.equal(p.comparisonPending,'active');assert.equal(p.comparisonData,undefined);
});
test('a legacy client refresh notice cannot replace another clients paged history',()=>{
  const p=Object.create(HeatingAIPanel.prototype);p.comparisonStart=100;p.comparisonEnd=1000;p.comparisonPending='active';
  assert.equal(p.acceptComparison({start:100,end:1000,actual:[],cycles:[],requires_panel_reload:'true',error:'Оновіть панель'}),false);
  assert.equal(p.comparisonPending,'active');
});
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
test('merged heat sessions preserve their total energy and show absolute dates',()=>{
  const start=Date.parse('2026-11-01T23:00:00+02:00')/1000;
  const rows=[{start,end:start+300,kwh:.5,power_kw:6},{start:start+300,end:start+600,kwh:.25,power_kw:3}];
  const merged=mergeSegments(rows);assert.equal(merged[0].kwh,.75);assert.equal(merged[0].power_kw,4.5);
  const p=Object.create(HeatingAIPanel.prototype);p.config={};p.attrs=()=>({created_at:new Date().toISOString(),heat_segments:rows});
  const markup=p.heatingPlan();assert.match(markup,/0,75 кВт·год/);assert.match(markup,/4,5 кВт/);
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
test('a superseded acknowledgement reconciles only actually persisted matching values',async()=>{
  const p=Object.create(HeatingAIPanel.prototype);p.systemDraft={boiler_power_mode:'fixed_6'};p.render=()=>{};
  p.shadowRoot={querySelectorAll:()=>[]};let a={revision:2,persisted_revision:2,fields:[]};p.attrs=()=>a;
  p._hass={callApi:async()=>{}};await p.saveSystemSettings();
  a={revision:3,persisted_revision:2,last_success:false,fields:[{key:'boiler_power_mode',value:'fixed_6'}]};p.syncSystemSettings();
  assert.equal(p.systemDraft.boiler_power_mode,'fixed_6');assert.ok(p.systemPending);
  a={...a,persisted_revision:3,last_request_id:'another-client',last_success:true};p.syncSystemSettings();
  assert.deepEqual(p.systemDraft,{});assert.equal(p.systemPending,null);assert.match(p.equipmentSave(),/Збережено ✓/);
});
test('deficit warning uses concrete limits and labels the horizon without stale conclusions',()=>{
  const p=Object.create(HeatingAIPanel.prototype);p.time=()=> '01.11 20:00';
  const data={controller:{quality:true},grid_status:{raw:'on'},forecast:{created_at:new Date().toISOString(),weather_complete:true,
    morning_reserve:{comfort_deficit:{coverage_complete:true,first_comfort_violation_at:Date.now()/1000+25*3600,
      first_violation_temperature:20.8,first_violation_lower:21.,first_violation_shortfall:.2,outside_visible_horizon:true}}}};
  p.attrs=k=>data[k]||{};p.state=k=>k==='heartbeat'?new Date().toISOString():'';
  assert.match(p.controllerHealth().message,/20,80 °C/);assert.match(p.controllerHealth().message,/поза видимими/);
  data.forecast.created_at=new Date(Date.now()-600000).toISOString();
  assert.match(p.controllerHealth().message,/застарів/);assert.doesNotMatch(p.controllerHealth().message,/20,80/);
});
test('temperature comparison explicitly shows absent advance forecasts and nullable observations',()=>{
  const p=Object.create(HeatingAIPanel.prototype);p.config={};p.comparisonCache=new Map();p.time=()=> '23:00';
  const row={cycle_id:'night',started_at:100,start_known:true,forecast:null,actual:[{ts:100,temperature:21},{ts:400,temperature:null},{ts:700,temperature:21.2}]};
  p.comparisonStart=100;p.comparisonEnd=1000;p.comparisonData={start:100,end:1000,cycles:[row],actual:row.actual};
  p.attrs=()=>({});p.shadowRoot={querySelector:()=>null};p.clientWidth=390;
  const text=p.temperatureComparison();assert.match(text,/не відновлено заднім числом/);
  assert.doesNotMatch(text,/data-comparison-line="forecast"/);assert.match(text,/data-comparison-line="actual"/);
});
test('comparison dates use HA timezone across the autumn DST transition',()=>{
  const before=calendarMidnight('2026-10-25','Europe/Kyiv'),after=calendarMidnight('2026-10-26','Europe/Kyiv');
  assert.equal(after-before,25*3600);
  assert.equal(new Date(after*1000).toISOString(),'2026-10-25T22:00:00.000Z');
  assert.equal(calendarMidnight('bad','Europe/Kyiv'),null);
  assert.equal(calendarMidnight('2026-02-31','Europe/Kyiv'),null);
});
test('weekly comparison draws actuals without requiring a heating cycle',()=>{
  const p=Object.create(HeatingAIPanel.prototype);p.time=()=> '24.10';p.comparisonDays=7;
  p.comparisonStart=100;p.comparisonEnd=100+7*86400;
  p.comparisonData={start:100,end:p.comparisonEnd,cycles:[],actual:[{ts:100,temperature:22},{ts:400,temperature:22.2}]};
  p.attrs=()=>({});p.shadowRoot={querySelector:()=>null};p.clientWidth=390;
  const text=p.temperatureComparison();assert.match(text,/data-comparison-days="7" aria-pressed="true"/);
  assert.match(text,/data-comparison-line="actual"/);assert.doesNotMatch(text,/data-comparison-line="forecast"/);
  assert.match(text,/Показано фактичну температуру/);
});
test('an unconfirmed later start cannot truncate the frozen post-heat temperature curve',()=>{
  const p=Object.create(HeatingAIPanel.prototype);p.time=()=> '07:00';p.comparisonStart=100;p.comparisonEnd=2000;
  p.comparisonData={start:100,end:2000,actual:[],cycles:[
    {cycle_id:'night',started_at:100,start_known:true,forecast:{at:-43100,curve:[100,400,1000,1600].map(ts=>({ts,temperature:21.}))}},
    {cycle_id:'next-window',started_at:800,start_known:false,forecast:null}]};
  p.attrs=()=>({});p.shadowRoot={querySelector:()=>null};p.clientWidth=390;
  const text=p.temperatureComparison();const path=text.match(/data-comparison-line="forecast" d="([^"]+)"/)[1];
  assert.equal((path.match(/L/g)||[]).length,3);
});
