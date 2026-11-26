import test from 'node:test';
import assert from 'node:assert/strict';
globalThis.HTMLElement=class {};
globalThis.customElements={get:()=>false,define:()=>{}};
globalThis.window={};
const {HeatingAIPanel,numeric,escapeHTML,isTrue,mergeSegments,chartLayout,normalizeHistory,activityIntervals,historyPaths,calendarMidnight}=await import('../frontend/heating-panel.js');
test('persisted profile selection is shown immediately and superseded plan is pending',()=>{
  const p=Object.create(HeatingAIPanel.prototype),data={system_settings:{values:{boiler_power_mode:'fixed_9'}},power_model:{equipment_version:10},forecast:{equipment_version:9,created_at:new Date().toISOString()}};
  p.attrs=k=>data[k]||{};p.state=k=>k==='power_model'?'fixed_6':'';p.section=(title,body)=>body;
  assert.equal(p.powerAdvice().mode,'9 кВт');assert.equal(p.equipmentPlanPending(),true);
  assert.match(p.nextHeatText(),/перераховується/);assert.match(p.heatingPlan(),/оновленим профілем/);
  data.forecast.equipment_version=10;p.state=k=>k==='power_model'?'fixed_9':'';
  assert.equal(p.equipmentPlanPending(),false);
});
test('actual heating overrides a waiting plan and observation never promises automatic execution',()=>{
  const p=Object.create(HeatingAIPanel.prototype),values={power_confirmed:'6393',boiler_active:'off',mode:'Спостереження'};
  const attrs={power_confirmed:{timestamp:new Date().toISOString()},controller:{quality:false},grid_status:{raw:'on'},forecast:{created_at:new Date().toISOString(),heat_segments:[{start:Date.now()/1000+600,end:Date.now()/1000+1200}]}};
  p.state=k=>values[k];p.attrs=k=>attrs[k]||{};
  assert.match(p.overviewDecision(),/Гріє/);assert.match(p.nextHeatText(),/призупин/);
  values.mode='Автоматичне керування';assert.match(p.nextHeatText(),/поза активним сеансом/);
  values.power_confirmed='unavailable';attrs.power_confirmed.confirmation_pending=true;values.boiler_active='unavailable';
  assert.equal(p.boilerActual().power,null);assert.equal(p.boilerActual().heating,null);
  values.power_confirmed='6393';values.boiler_active='on';attrs.power_confirmed={timestamp:new Date(Date.now()-100000).toISOString()};
  assert.doesNotMatch(p.overviewDecision(),/Гріє/);
});
test('grid loss explains unavailable electric measurements without calling the controller broken',()=>{
  const p=Object.create(HeatingAIPanel.prototype),attrs={controller:{quality:false},diagnostics:{control_health:'healthy'},forecast:{created_at:new Date().toISOString()},grid_status:{raw:'off'}};
  p.attrs=k=>attrs[k]||{};p.state=k=>k==='heartbeat'?new Date().toISOString():'on';p.time=()=>'';
  assert.match(p.controllerHealth().message,/Немає електромережі/);
  assert.doesNotMatch(p.controllerHealth().message,/бракує актуальних/);
  attrs.grid_status.raw='unknown';assert.match(p.controllerHealth().message,/підтвердження електромережі/);
  attrs.grid_status.raw='on';assert.match(p.controllerHealth().message,/бракує актуальних/);
});
test('overview status separates controller heartbeat, grid blocking and actual heating',()=>{
  const p=Object.create(HeatingAIPanel.prototype),values={heartbeat:new Date().toISOString(),alive:'on',mode:'Автоматичне керування',permitted:'on'};
  const attrs={controller:{quality:true},diagnostics:{control_health:'healthy'},forecast:{created_at:new Date().toISOString(),heat_segments:[]},grid_status:{raw:'off'}};
  p.state=key=>values[key]??'off';p.attrs=key=>attrs[key]||{};p.boilerActual=()=>({heating:false,power:0});
  p.controllerHealth=()=>({message:'',severity:'info',stale:false});
  assert.deepEqual(p.overviewStatus().label,'Немає електромережі');
  attrs.grid_status.raw='unknown';assert.equal(p.overviewStatus().label,'Мережу не підтверджено');
  attrs.grid_status.raw='on';values.permitted='off';assert.equal(p.overviewStatus().label,'Нагрів призупинено');
  values.permitted='on';p.boilerActual=()=>({heating:true,power:6000});
  assert.equal(p.overviewStatus().label,'Опалення працює');
});
test('overview translates KWS readiness without confusing recovered ledger history with an active failure',()=>{
  const p=Object.create(HeatingAIPanel.prototype),values={heartbeat:new Date().toISOString(),alive:'on',mode:'Автоматичне керування',permitted:'on'};
  const attrs={controller:{quality:true},diagnostics:{control_health:'healthy',control_error:null,reliability:{equipment:{ready:false,reason:'kws_reset_pending',reason_text:'KWS: очікування підтвердженого відновлення'},ledger_recovery:{state:'reconciliation_verified',at:Date.now()/1000}}},forecast:{created_at:new Date().toISOString(),heat_segments:[]},grid_status:{raw:'on'}};
  p.state=key=>values[key]??'off';p.attrs=key=>attrs[key]||{};p.boilerActual=()=>({heating:false,power:0});p.controllerHealth=()=>({message:'',severity:'info',stale:false});
  assert.deepEqual(p.equipmentReadiness(),{ready:false,configured:true,reason:'kws_reset_pending',label:'Очікує відновлення',holding:false,relayControl:null,relayId:null,relayState:'unknown',restoreLabel:'Очікує'});
  assert.equal(p.overviewStatus().label,'Нагрів заблоковано захистом');
  assert.match(p.overviewStatus().reason,/KWS/);
  attrs.diagnostics.reliability.equipment={ready:true,reason:'ready'};
  assert.notEqual(p.overviewStatus().tone,'error');
  assert.match(p.diagnosticSummary(),/Відновлено/);
  attrs.diagnostics.reliability.equipment={};
  assert.equal(p.equipmentReadiness().configured,false);
});
test('compact history decodes AppDaemon wire zeros, gaps and cycle identity',async()=>{
  const {unpackHistoryItems}=await import('../frontend/heating-panel.js');
  const page={encoding:'columns-v1',cycle_ids:['night'],items:[['0',null,15,100,null,21,22],[1,'0',3,101,'0'],[2,{kind:'marker',value:{kind:'grid_off'}}]]};
  const rows=unpackHistoryItems(page);assert.equal(rows.length,3);
  assert.equal(rows[0].value.temperature,null);assert.equal(rows[1].cycle_id,'night');
  assert.equal(numeric(rows[1].value.temperature),0);
  assert.equal(unpackHistoryItems({...page,items:[[1,10,3,1,2]]}),null);
  assert.equal(unpackHistoryItems({...page,items:[['0',null,15,1]]}),null);
});
test('compact history decodes real HA rows and explicit null sentinels without shifting columns',async()=>{
  const {unpackHistoryItems}=await import('../frontend/heating-panel.js');
  const rows=unpackHistoryItems({encoding:'columns-v1',cycle_ids:['night'],items:[['0','0',3,100,21.5],['0','0',15,101,'~','~',22],[1,'0',3,102,'0']]});
  assert.equal(rows.length,3);assert.deepEqual(rows[0].value,{ts:100,temperature:21.5});
  assert.deepEqual(rows[1].value,{ts:101,temperature:null,lower:null,upper:22});
  assert.equal(numeric(rows[2].value.temperature),0);
});
test('extended forecast uses the matching plan only and preserves unknown plan identity',()=>{
  const p=Object.create(HeatingAIPanel.prototype);p.forecastExtended=true;
  p.attrs=()=>({plan_id:'current',curve:[{ts:1,temperature:21}],horizon_hours:24});
  p._hass={states:{'sensor.heating_forecast_outlook':{attributes:{plan_id:'current',horizon_hours:36,curve:[[1,21,20,22,21],[2,20.8,20,22,21]],heat_segments:[[1,2,'0'],[3,4,1]]}}}};
  assert.equal(p.forecastData().horizon_hours,36);assert.equal(p.forecastData().curve.length,2);
  assert.deepEqual(p.forecastData().heat_segments.map(s=>s.conditional),[false,true]);
  p.hass.states['sensor.heating_forecast_outlook'].attributes.plan_id='old';
  assert.equal(p.forecastData().horizon_hours,24);
});
test('a wholly unmeasured cycle is not displayed as zero actual consumption',()=>{
  const p=Object.create(HeatingAIPanel.prototype),now=Date.now()/1000;p.config={};
  const row={start:now-3600,end:now+3600,initial_plan:{kwh:4},observed_kwh:0,complete:false,unknown_seconds:3600};
  p.time=()=> '07:00';p.attrs=()=>({energy_cycles:[row]});
  const text=p.energyComparison();assert.match(text,/немає даних/);assert.doesNotMatch(text,/0,00/);
  row.complete=true;row.unknown_seconds=0;
  assert.match(p.energyComparison(),/0,00/);
});
test('inlet nominal accounting is labelled as calculated in the boiler and cycle cards',()=>{
  const p=Object.create(HeatingAIPanel.prototype);p.systemDraft={};p.pending=new Set();p.config={};p.entity=()=>null;p.time=()=> '00:00';p.config={};
  const now=Date.now()/1000;
  const data={power_confirmed:{measurement_basis:'nominal_inlet_stage',timestamp:new Date().toISOString()},
    plan_history:{energy_cycles:[{start:now-3600,end:now+3600,observed_kwh:1,calculated_kwh:2,calculated_other_kwh:2,complete:false,initial_plan:{kwh:2}}]}};
  p.attrs=k=>data[k]||{};p.state=k=>({power_confirmed:'6000',boiler_active:'on'})[k]||'unavailable';
  p.powerAdvice=()=>({recommend:{},mode:'6 кВт',recommended:false,partial:false,adviceValue:'—'});
  assert.match(p.boilerOverview(),/Гріє · розрахунок/);
  for (const basis of ['group_balance','appliance_stage']) {
    data.power_confirmed.measurement_basis=basis;
    assert.match(p.boilerOverview(),/Гріє · розрахунок/);
    assert.match(p.boilerOverview(),basis==='group_balance'?/баланс груп/:/окремі прилади/);
  }
  assert.match(p.energyComparison(),/Облік · розрахунок/);
  assert.match(p.energyComparison(),/3,00/);
  assert.match(p.energyComparison(true),/≈/);
  assert.doesNotMatch(p.energyComparison(true),/≥/);
});
test('plan/fact names KWS measurements and explicitly unclassified meter increments',()=>{
  const now=Date.now()/1000,p=Object.create(HeatingAIPanel.prototype);
  p.time=()=> '06:00';p.config={};p.attrs=()=>({energy_cycles:[{
    cycle_id:'meter',start:now-7200,end:now-3600,
    initial_plan:{at:now-10800,kwh:9,before_start:true},
    observed_kwh:7.5,meter_kwh:7.5,transition_kwh:7.5,
    automatic_kwh:0,other_kwh:0,energy_basis:'kws_counter_delta',complete:true
  }]});
  const markup=p.energyComparison(true);
  assert.match(markup,/KWS/);
  assert.match(markup,/7,50/);
  assert.match(markup,/не визначен|без визначення режиму/i);
  assert.doesNotMatch(markup,/лічильником розрахункової потужності/);
  p.comparisonData={energy_cycles:[{
    cycle_id:'meter',start:now-7200,end:now-3600,
    initial_plan:{at:now-10800,kwh:9},latest_plan:{kwh:10},
    observed_kwh:7.5,transition_kwh:7.5,energy_basis:'kws_counter_delta',complete:true
  }],reviews:[]};
  const history=p.cycleHistory();
  assert.match(history,/KWS/);
  assert.doesNotMatch(history,/незалежного лічильника котла немає|розрахунковий баланс/);
});

test('partial-meter reserve is visible without calling assumed watts actual boiler heating',()=>{
  const p=Object.create(HeatingAIPanel.prototype);p.systemDraft={};p.pending=new Set();p.config={};p.entity=()=>null;p.time=()=> '00:00';p.config={};
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
  assert.match(p.overview(),/data-info="sensor.heating_forecast">Мін. прогноз <strong>21,4°/);
});
test('home opens with a forecast-only timeline and three simple tabs',()=>{
  const now=Date.now()/1000,p=Object.create(HeatingAIPanel.prototype);p.timelineCursor=now;p.forecastExtended=false;p.clientWidth=780;
  p.shadowRoot={querySelector:()=>null};p.time=()=> '12:00';p.historyData={rows:{room:[{ts:now-1800,state:'21.2'}],boiler_stage:[{ts:now-1200,state:'6 кВт'}]}};
  const forecast={created_at:new Date(now*1000).toISOString(),horizon_hours:24,curve:[{ts:now,temperature:21.3,lower:20,upper:22},{ts:now+3600,temperature:21.5,lower:20,upper:22}],heat_segments:[{start:now+600,end:now+1200}]};
  p.attrs=key=>key==='forecast'?forecast:key==='outages'?{outages:[]}:{};p._hass={states:{}};
  const chart=p.forecastChart();assert.match(chart,/home-timeline/);assert.match(chart,/role="slider"/);assert.doesNotMatch(chart,/Факт кімнати|KWS 3·6·9|Факт: 21,2 °C/);assert.match(chart,/прогнозу температури, нагріву й мережі/);assert.match(chart,/>Камін<\/text>/);assert.doesNotMatch(chart,/class="legend"/);assert.match(chart,/Прогноз: 21,3 °C/);
  p.timelineCursor=now+700;const planned=p.forecastChart();assert.match(planned,/План: підтверджений/);assert.match(planned,/Мережа: невідомо/);
  const source=HeatingAIPanel.prototype.render.toString();assert.match(source,/\['overview','Огляд'\].*\['model','Історія'\].*\['settings','Налаштування'\]/s);
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
  p.state=k=>k==='heartbeat'?new Date().toISOString():'on';p.time=()=> '00:00';p.config={};
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
test('expert settings search filters locally without dropping schema fields',()=>{
  const p=Object.create(HeatingAIPanel.prototype);p.systemDraft={};p.settingsQuery='камін';
  p.attrs=()=>({schema_version:1,values:{}});p.entity=()=>null;p.state=()=> 'unavailable';p.id=x=>x;
  const filtered=p.systemSettings();
  assert.match(filtered,/data-settings-search/);assert.match(filtered,/data-system-key="fire_start_delta"/);
  assert.doesNotMatch(filtered,/data-system-key="room_loss"/);
  p.settingsQuery='';const all=p.systemSettings();
  assert.equal(p.systemFields().length,52);assert.match(all,/data-system-key="kws_auto_restore"/);assert.ok((all.match(/data-system-key=/g)||[]).length>=35);
  assert.equal(p.systemFields().find(field=>field.key==='day_insurance').value,'disabled');
  assert.match(all,/data-system-key="day_insurance_kwh"/);
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
test('native production settings use the versioned authenticated websocket API',async()=>{
  const panel=Object.create(HeatingAIPanel.prototype);panel.config={entry_id:'native-entry'};
  panel.systemDraft={boiler_power_mode:'fixed_6'};panel.render=()=>{};panel.shadowRoot={querySelectorAll:()=>[]};
  panel.attrs=()=>({revision:4});let request;
  panel._hass={callWS:async payload=>{request=payload;return {last_success:true,persisted_revision:5};}};
  await panel.saveSystemSettings();clearTimeout(panel.systemTimeout);clearTimeout(panel.systemRetryTimeout);
  assert.equal(request.type,'heating_ai_legacy/settings/save');assert.deepEqual(request.version,[1,0]);
  assert.equal(request.entry_id,'native-entry');assert.equal(request.revision,4);
  assert.deepEqual(request.values,{boiler_power_mode:'fixed_6'});
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
  const p=Object.create(HeatingAIPanel.prototype);p.systemDraft={};p.pending=new Set();p.config={};p.state=k=>({power_model:'auto',power_confirmed:'0',boiler_active:'off'})[k];
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
    assert.equal(g.width,width);assert.ok(g.height>=240&&g.height<=280);
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
test('plan details distinguish 24-hour comfort from the extended reserve improvement',()=>{
  const p=Object.create(HeatingAIPanel.prototype);
  p.config={};
  p.attrs=()=>({created_at:new Date().toISOString(),heat_segments:[],morning_reserve:{
    local_deficit_reduction_degree_hours:1.25,
    comfort_24h:{predicted_maximum:21.65,minimum_before_next_heat:19.85,
      predicted_maximum_at:1795644000,comfort_minutes:600,first_comfort_violation_at:null},
  }});
  const markup=p.heatingPlan(true);
  assert.match(markup,/Пік за 24 год/);
  assert.match(markup,/21,65/);
  assert.match(markup,/Мінімум за 24 год/);
  assert.match(markup,/скорочено на 1,25 °C·год/);
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
test('manual thermostat auto is explicit and does not look like a stale controller error',()=>{
  const panel=Object.create(HeatingAIPanel.prototype),attrs={controller:{quality:true},diagnostics:{control_health:'healthy'},forecast:{created_at:new Date().toISOString()},grid_status:{raw:'on'}};
  panel.attrs=key=>attrs[key]||{};panel.state=key=>key==='mode'?'Штатний auto':key==='heartbeat'?new Date().toISOString():key==='process'?'on':'off';panel.time=()=> '12:00';panel.boilerActual=()=>({heating:false});
  assert.equal(panel.overviewDecision(),'Ручний режим AUTO');assert.match(panel.controllerHealth().message,/не надсилає автоматичні команди/);assert.doesNotMatch(panel.controllerHealth().message,/Помилка|застарів/);
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
  assert.equal(p.controllerHealth().comfortWarning,p.controllerHealth().message);
  data.diagnostics={control_health:'error',control_error:'LedgerConflict: Outstanding reservation'};
  assert.equal(p.controllerHealth().comfortWarning,'');
  assert.match(p.controllerHealth().message,/Безпечне керування/);
  delete data.diagnostics;
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
test('mobile overview keeps the full plan on demand and daily energy in the current-state card',()=>{
  const p=Object.create(HeatingAIPanel.prototype);p.config={};p.entity=()=>null;
  p.attrs=key=>key==='forecast'?{planned_kwh:23,horizon_hours:24}:key==='controller'?{target_temperature:22}:{};
  p.state=key=>({energy_24h:'0',forecast:'21.4',mode:'Автоматичне керування'})[key]??'unknown';
  p.controllerHealth=()=>({stale:false});p.overviewStatus=()=>({tone:'normal',label:'Очікує',reason:'Очікує сеансу'});
  p.overviewAction=()=>({title:'Наступний нагрів',value:'23:00',detail:'За планом',tone:'scheduled'});
  for(const method of ['energyComparison','forecastOverview','boilerOverview','fireplaceOverview','historyChart'])p[method]=()=>'';
  p.heatingPlan=()=>'<section class="plan-card">Усі сеанси</section>';
  const view=p.overview();
  assert.match(view,/<details class="next-action card"[^>]*><summary>/);
  assert.match(view,/Більше/);assert.match(view,/next-action-details.*Усі сеанси/s);
  assert.doesNotMatch(view,/overview-metrics|Енергія за місяць/);
  assert.match(view,/data-info="sensor.heating_energy_24h"[^>]*>.*?0,0/s);
  assert.match(view,/data-info="sensor.heating_forecast"[^>]*>.*?23,0/s);
  // Switching to a longer chart horizon must not relabel its energy as 24 hours.
  p.forecastExtended=true;p.forecastData=()=>({planned_kwh:99,horizon_hours:72});
  assert.doesNotMatch(p.overview(),/99,0/);
  p.controllerHealth=()=>({stale:true});assert.doesNotMatch(p.overview(),/23,0/);
  p.controllerHealth=()=>({stale:false});
  p.state=()=> 'unavailable';assert.doesNotMatch(p.overview(),/>0,0/);
});
test('forecast preserves a distinct residual-fireplace lane without duplicating the legend',()=>{
  const now=Date.now()/1000,p=Object.create(HeatingAIPanel.prototype);p.clientWidth=390;p.timelineCursor=null;
  p.shadowRoot={querySelector:()=>null};p.time=()=> '12:00';p.attrs=()=>({outages:[]});
  p.forecastData=()=>({created_at:new Date(now*1000).toISOString(),curve:[{ts:now,temperature:21},{ts:now+86400,temperature:22}],fireplace_segments:[{start:now-3600,end:now+3600,estimated:true}],heat_segments:[]});
  const chart=p.forecastChart();assert.match(chart,/data-forecast-lane="fireplace"/);
  assert.match(chart,/оцінка залишкового тепла/);assert.doesNotMatch(chart,/class="legend"/);
});

test('manual KWS UI is inside existing disclosure and never changes the planned text',()=>{
  const p=Object.create(HeatingAIPanel.prototype);
  p.manualEnergyKwh=6;p.manualEnergyPending=false;p.manualEnergyMessage='';
  p.state=k=>k==='manual_energy'?'idle':'on';
  p.attrs=k=>k==='manual_energy'?{}:k==='controller'?{target_temperature:22}:k==='forecast'?{heat_segments:[],created_at:new Date().toISOString()}:{};
  p.boilerActual=()=>({power:6000,heating:false});
  const manual=p.manualEnergyControls();
  assert.match(manual,/Додатковий нагрів/);
  assert.match(manual,/6 кВт·год/);
  assert.match(manual,/data-manual-energy="start"/);
  assert.doesNotMatch(manual,/data-manual-energy="stop"/);
});
test('manual KWS UI presents measured progress and only a stop action while active',()=>{
  const p=Object.create(HeatingAIPanel.prototype);
  p.manualEnergyKwh=6;p.manualEnergyPending=false;p.manualEnergyMessage='';
  p.state=k=>k==='manual_energy'?'active':'on';
  p.attrs=k=>k==='manual_energy'?{target_kwh:6,consumed_kwh:2.4}:({});
  p.boilerActual=()=>({power:6000,heating:true});
  const html=p.manualEnergyControls();
  assert.match(html,/2,40 \/ 6 кВт·год/);
  assert.match(html,/data-manual-energy="stop"/);
  assert.doesNotMatch(html,/data-manual-energy="start"/);
});

test('manual progress labels its live KWS power integral separately from counter',()=>{
  const p=Object.create(HeatingAIPanel.prototype);
  p.manualEnergyKwh=1;p.manualEnergyPending=false;p.manualEnergyMessage='';
  p.state=k=>k==='manual_energy'?'active':'on';
  p.attrs=k=>k==='manual_energy'?{target_kwh:1,consumed_kwh:0,progress_kwh:.56,progress_basis:'kws_power'}:{};
  p.boilerActual=()=>({power:6200,heating:true});
  const html=p.manualEnergyControls();
  assert.match(html,/≈ 0,56 \/ 1 кВт·год/);
  assert.match(html,/Лічильник KWS: 0,00 кВт·год/);
  assert.match(html,/aria-valuenow="56"/);
});
test('manual KWS selector exposes every integer from 1 to 40 and clamps buttons',async()=>{
  const p=Object.create(HeatingAIPanel.prototype);
  p.manualEnergyKwh=39;p.manualEnergyPending=false;p.manualEnergyMessage='';
  p.state=k=>k==='manual_energy'?'idle':'on';p.attrs=()=>({});p.render=()=>{};
  await p.manualEnergyClick('plus');assert.equal(p.manualEnergyKwh,40);
  await p.manualEnergyClick('plus');assert.equal(p.manualEnergyKwh,40);
  const html=p.manualEnergyControls();
  assert.equal((html.match(/<option value="/g)||[]).length,40);
  assert.match(html,/value="40" selected/);assert.doesNotMatch(html,/value="41"/);
  assert.match(html,/data-manual-energy="plus"[^>]*disabled/);
  await p.changed({target:{value:'1',hasAttribute:k=>k==='data-manual-kwh'}});
  await p.manualEnergyClick('minus');assert.equal(p.manualEnergyKwh,1);
  await p.changed({target:{value:'41',hasAttribute:k=>k==='data-manual-kwh'}});
  assert.equal(p.manualEnergyKwh,1);
});

test('manual energy sends the explicit entry ID for start and stop without lookup',async()=>{
  const p=Object.create(HeatingAIPanel.prototype),calls=[];
  p.config={entry_id:'explicit-native'};p.manualEnergyKwh=6;p.render=()=>{};
  p._hass={callWS:async payload=>{calls.push(payload);return {status:'active'};}};
  await p.manualEnergyClick('start');await p.manualEnergyClick('stop');
  assert.deepEqual(calls,[
    {type:'heating_ai_legacy/manual_energy',entry_id:'explicit-native',version:[1,0],action:'start',kwh:6},
    {type:'heating_ai_legacy/manual_energy',entry_id:'explicit-native',version:[1,0],action:'stop'}
  ]);
  assert.equal(p.manualEnergyPending,false);
});
test('manual energy resolves missing entry ID from registered HA production panel for start and stop',async()=>{
  const p=Object.create(HeatingAIPanel.prototype),calls=[];
  p.config={};p.manualEnergyKwh=6;p.render=()=>{};
  p._hass={callWS:async payload=>{calls.push(payload);return payload.type==='get_panels'?{
    'heating-ai-v1':{config:{entry_id:'live-native'}},
    'other-panel':{config:{entry_id:'wrong-native'}}
  }:{status:'active'};}};
  await p.manualEnergyClick('start');await p.manualEnergyClick('stop');
  assert.deepEqual(calls,[
    {type:'get_panels'},
    {type:'heating_ai_legacy/manual_energy',entry_id:'live-native',version:[1,0],action:'start',kwh:6},
    {type:'get_panels'},
    {type:'heating_ai_legacy/manual_energy',entry_id:'live-native',version:[1,0],action:'stop'}
  ]);
  assert.equal(p.manualEnergyPending,false);
});
test('manual energy fails closed if the production panel has no entry ID or lookup fails',async()=>{
  for(const fail of [false,true]){
    const p=Object.create(HeatingAIPanel.prototype),calls=[];
    p.config={};p.manualEnergyKwh=6;p.render=()=>{};
    p._hass={callWS:async payload=>{calls.push(payload);if(fail)throw new Error('network');return {'other-panel':{config:{entry_id:'wrong-native'}}};}};
    await p.manualEnergyClick('start');
    assert.deepEqual(calls,[{type:'get_panels'}]);assert.equal(p.manualEnergyPending,false);
    assert.match(p.manualEnergyMessage,/Не вдалося виконати/);
  }
});

test('manual heating without cumulative KWS baseline labels verified phase-power progress',()=>{
  const p=Object.create(HeatingAIPanel.prototype);
  p.manualEnergyKwh=1;p.manualEnergyPending=false;p.manualEnergyMessage='';
  p.state=k=>k==='manual_energy'?'active':'on';
  p.attrs=k=>k==='manual_energy'?{target_kwh:1,consumed_kwh:0,progress_kwh:.25,progress_basis:'kws_power',power_only:true}:{};
  p.boilerActual=()=>({power:6000,heating:true});
  const html=p.manualEnergyControls();
  assert.match(html,/трьох фаз/);
  assert.doesNotMatch(html,/Лічильник KWS: 0,00 кВт·год/);
});

test('unified learning card displays current collection status rather than the old candidate rejection',()=>{
  const p=Object.create(HeatingAIPanel.prototype);
  const a={learning_status:'collecting',learning_gate:'qualified_inputs_required',
    independent_windows:0,qualified_minutes:0,active_version:6,
    candidate_holdout_mae_1h:null,candidate_holdout_mae_3h:null,
    last_successful_learning:'2026-10-24T06:54:54+03:00',next_learning:'2026-11-24T10:00:00+02:00',
    learning_attempt:{time:'2026-11-23T20:45:12+02:00',status:'rejected',reason:'Old legacy gate'}};
  p.attrs=()=>a;p.time=value=>value.slice(0,16);
  p.compactMetric=(label,value,unit)=>`<span>${label}: ${value} ${unit}</span>`;
  p.section=(title,body)=>`<section><h2>${title}</h2>${body}</section>`;
  const text=p.learningProgress();
  assert.match(text,/Самонавчання моделі/);
  assert.match(text,/Збирає дані/);assert.match(text,/Активна модель v6/);
  assert.match(text,/Незалежні вікна: 0/);assert.match(text,/Придатні вимірювання: 0 хв/);
  assert.match(text,/Кандидат · MAE 1 год: —/);assert.match(text,/Кандидат · MAE 3 год: —/);
  assert.match(text,/Наступний аналіз/);assert.match(text,/2026-11-24T10:00/);
  assert.match(text,/Останнє успішне застосування/);assert.match(text,/2026-10-24T06:54/);
  assert.match(text,/жодних нових коефіцієнтів ще не застосовано/);
  assert.match(text,/Попередній алгоритм · історія спроб/);
  assert.match(text,/Old legacy gate/);
  assert.doesNotMatch(text,/Остання спроба навчання/);
});
test('unified learning renders confirmed improvements, rollback, and safe missing-data fallbacks',()=>{
  const p=Object.create(HeatingAIPanel.prototype);
  const a={learning_status:'accepted',learning_gate:'independent_holdout_passed',
    independent_windows:12,qualified_minutes:480,active_version:7,
    candidate_holdout_mae_1h:.1234,candidate_holdout_mae_3h:.3456,
    last_successful_learning:'2026-11-24T10:00:00+02:00',next_learning:'2026-11-24T20:45:00+02:00'};
  p.attrs=()=>a;p.time=value=>value.slice(0,16);
  p.compactMetric=(label,value,unit)=>`<span>${label}: ${value} ${unit}</span>`;
  p.section=(title,body)=>body;
  const accepted=p.learningProgress();
  assert.match(accepted,/Коефіцієнти оновлено/);assert.match(accepted,/Активна модель v7/);
  assert.match(accepted,/0,123/);assert.match(accepted,/0,346/);
  a.learning_status='rolled_back';a.learning_gate='rollback_cooldown';a.rollback_reason='quality_3h';
  const rolled=p.learningProgress();assert.match(rolled,/Відновлено попередню модель/);
  assert.match(rolled,/Відкат моделі/);assert.match(rolled,/прогноз на 3 години/);
  p.attrs=()=>({});assert.match(p.learningProgress(),/Стан навчання недоступний/);
  assert.doesNotMatch(p.learningProgress(),/undefined|NaN|Invalid Date/);
});
test('unified learner does not mistake legacy AI auto-apply control for its own promotion gate',()=>{
  const source=HeatingAIPanel.prototype.model.toString();
  assert.match(source,/Штатне самонавчання/);
  assert.match(source,/Спадковий параметр ШІ · не для самонавчання/);
  assert.match(source,/this\.field\('ai_auto_apply','Автозастосування старих пропозицій'\)/);
});

test('online learner explains rejected evidence and retains last accepted results across restart',()=>{
  const p=Object.create(HeatingAIPanel.prototype);
  const a={learning_status:'frozen',learning_gate:'current_conditions_unqualified',
    independent_windows:0,qualified_minutes:0,active_version:6,
    verified_reports:23,rejection_counts:{unknown_ventilation:12,unknown_or_unqualified_voltage:4},
    current_reasons:['unqualified_outage','unknown_ventilation'],
    analysis_status:'insufficient_data',analysis_blockers:['independent_days','heating_coverage'],
    last_accepted_changes:[{parameter:'room_loss',before:0.008,after:0.0079}],
    last_accepted_mae_1h:0.15,last_accepted_mae_3h:0.31};
  p.attrs=()=>a;p.time=()=> '—';
  p.compactMetric=(name,value,unit)=>'<span>'+name+': '+value+' '+unit+'</span>';
  p.section=(title,body)=>body;
  const html=p.learningProgress();
  assert.match(html,/Архівні вимірювання все одно аналізуються/);
  assert.match(html,/Незалежних температурних звітів.*23/);
  assert.match(html,/Невідомий стан вентиляції — 12/);
  assert.match(html,/Замало незалежних днів/);
  assert.match(html,/Тепловтрати 0,00800 → 0,00790/);
  assert.match(html,/не поточний кандидат/);
  assert.match(html,/<details class="learning-evidence" open>/);
});

test('journal breakdown uses cached metrics and never equates archive budget with disk',()=>{
  const p=Object.create(HeatingAIPanel.prototype);
  p.attrs=()=>({archive_health:{used_bytes:700*1048576,max_bytes:1024*1048576,
    snapshot_bytes:600*1048576,daily_log_bytes:100*1048576,free_bytes:33*1073741824}});
  p.compactMetric=(label,value,unit)=>'<span>'+label+': '+value+' '+unit+'</span>';
  p.section=(title,body)=>body;
  p.time=()=> '—';
  const html=p.controllerQuality();
  assert.match(html,/Знімки: 600,0 МіБ/);
  assert.match(html,/журнали: 100,0 МіБ/);
  assert.match(html,/Ліміт архіву не є розміром диска/);
});

 test('source controls reuse guarded KWS and circulation settings rather than inventing a pump',()=>{
  const p=Object.create(HeatingAIPanel.prototype);p.pending=new Set();p.systemDraft={};
  p.attrs=k=>k==='system_settings'?{values:{kws_auto_restore:'disabled',boiler_power_mode:'fixed_6'}}:k==='fireplace_status'?{temperature:21.1}:{};
  p.state=k=>({fireplace_status:'Неактивний',fan:'off',fan_mode:'Авто',manual_energy:'idle',return:'22.8'})[k]||'off';
  p.id=k=>({fan_mode:'input_select.heating_fan_mode',fan_control:'input_boolean.heating_fan_control'})[k]||'sensor.'+k;
  p.entity=k=>k==='fan_mode'?{state:'Авто',attributes:{options:['Авто','Зупинити','Ручний запуск']}}:k==='fan_control'?{state:'on',attributes:{}}:undefined;
  p.boilerActual=()=>({meta:{},stale:false,power:0,heating:false});p.controllerHealth=()=>({});
  p.equipmentReadiness=()=>({configured:true,ready:true,relayState:'on',relayControl:true,relayId:'switch.energy_meter_kotel',label:'Готовий',restoreLabel:'Вимкнено'});
  const boiler=p.boilerOverview(),fire=p.fireplaceOverview();
  assert.match(boiler,/role="switch" aria-label="Автовідновлення KWS"/);assert.match(boiler,/data-kws-toggle="enabled"/);assert.match(boiler,/data-open-manual/);assert.match(boiler,/data-info="switch.energy_meter_kotel"/);
  assert.match(fire,/data-fan-mode="Авто"/);assert.match(fire,/data-fan-mode="Зупинити"/);assert.match(fire,/Ручний запуск/);assert.match(fire,/data-control="fan_control"/);assert.doesNotMatch(fire,/Насос/);
});

test('frontend loader keeps HA registration stable while fetching a new local asset digest',async()=>{
  const {mkdtemp,readFile,writeFile,rm}=await import('node:fs/promises');
  const {tmpdir}=await import('node:os');const {join}=await import('node:path');const {pathToFileURL}=await import('node:url');
  const dir=await mkdtemp(join(tmpdir(),'heating-loader-'));
  const oldFetch=globalThis.fetch,oldRegistry=globalThis.customElements;
  const registered='0123456789abcdef',next='fedcba9876543210',definitions=new Map(),requests=[];
  try{
    const loader=await readFile(new URL('../custom_components/heating_ai_legacy/assets/full-panel-loader.js',import.meta.url),'utf8');
    await writeFile(join(dir,'loader.mjs'),loader);
    await writeFile(join(dir,'package.json'),'{"type":"module"}');
    const stub='export class HeatingAIPanel { static digest = new URL(import.meta.url).searchParams.get("v") || new URL(import.meta.url).pathname.match(/full-panel-([a-f0-9]{16})\\.js$/)?.[1] }';
    await writeFile(join(dir,'full-panel.js'),stub);
    await writeFile(join(dir,'full-panel-'+next+'.js'),stub);
    globalThis.customElements={get:name=>definitions.get(name),define:(name,component)=>definitions.set(name,component)};
    globalThis.fetch=async(url,options)=>{requests.push([url.pathname,options]);return {ok:true,json:async()=>({digest:next})}};
    const url=pathToFileURL(join(dir,'loader.mjs'));url.searchParams.set('v',registered);
    await import(url);
    assert.equal(definitions.get('heating-ai-v1-'+registered).digest,next);
    assert.equal(definitions.size,1);
    assert.equal(requests.length,1);assert.ok(requests[0][0].endsWith('/full-panel-version.json'));assert.equal(requests[0][1].cache,'no-store');
    globalThis.fetch=async()=>({ok:true,json:async()=>({digest:'../../untrusted.js'})});
    url.searchParams.set('case','invalid');
    await assert.rejects(import(url),/Invalid Heating AI asset manifest/);
    globalThis.fetch=async()=>({ok:false,status:404});
    definitions.clear();url.searchParams.set('case','legacy');
    await import(url);assert.equal(definitions.get('heating-ai-v1-'+registered).digest,registered);
  }finally{globalThis.fetch=oldFetch;globalThis.customElements=oldRegistry;await rm(dir,{recursive:true,force:true});}
});

test('inline KWS toggle cannot save another draft or bypass existing revision save',async()=>{const p=Object.create(HeatingAIPanel.prototype);p.attrs=()=>({values:{kws_auto_restore:'disabled'}});p.systemDraft={boiler_power_mode:'fixed_9'};let calls=0;p.saveSystemSettings=async()=>{calls++;assert.deepEqual(p.systemDraft,{kws_auto_restore:'enabled'});};await p.changeKwsRestore('enabled');assert.equal(calls,0);p.systemDraft={};await p.changeKwsRestore('bogus');assert.equal(calls,0);await p.changeKwsRestore('enabled');assert.equal(calls,1);});

test('learning reports real support for all four thermal coefficients without claiming promotion',()=>{
  const p=Object.create(HeatingAIPanel.prototype);
  p.attrs=()=>({learning_status:'collecting',parameters:{floor_air:.044,room_loss:.0128,floor_decay:.12,heat_gain:.5},
    parameter_support:{floor_air:{available:{windows:12,minutes:180,days:3},training:{windows:8,minutes:120,days:2},holdout:{windows:4,minutes:60,days:1},selected_for_candidate:true,status:'candidate_evaluated',basis:'thermal_windows'},
      room_loss:{available:{windows:12,minutes:180,days:3},status:'not_identifiable_with_ventilation',basis:'thermal_windows'},
      floor_decay:{available:{windows:0,minutes:0,days:0},status:'insufficient_support',basis:'warm_idle'},
      heat_gain:{available:{windows:2,minutes:60,days:1},status:'not_selected',basis:'measured_heat'}}});
  p.section=(title,body)=>body;p.compactMetric=()=>'';p.time=()=> '12:00';
  const markup=p.learningProgress();
  assert.match(markup,/Докази за коефіцієнтами/);
  for(const label of ['Передача тепла в кімнату','Тепловтрати','Охолодження підлоги','Потужність нагріву підлоги'])assert.ok(markup.includes(label));
  assert.match(markup,/12 \/ 180 \/ 3/);
  assert.match(markup,/8 \/ 120 \/ 2/);
  assert.match(markup,/4 \/ 60 \/ 1/);
  assert.match(markup,/Не відокремлено від вентиляції/);
  assert.match(markup,/0 \/ 0 \/ 0/);
  assert.doesNotMatch(markup,/NaN|undefined|Коефіцієнти оновлено/);
});
test('quality distinguishes advisory 48h conditional cohorts and independent counts',()=>{
  const p=Object.create(HeatingAIPanel.prototype);p.section=(title,body)=>body;
  p.attrs=()=>({advisory_validation:{groups:[
    {horizon:48,basis:'conditional_repeat',weather_known:false,schedule_known:false,days:3,independent:{n:2,mae:.65,bias:-.25},all_reports:{n:16,mae:.35},minimum_lead_hours:47.5,maximum_lead_hours:48.3},
    {horizon:24,basis:'calculated_plan',weather_known:true,schedule_known:true,days:1,independent:{n:1,mae:.4,bias:.4},all_reports:{n:4}}],pending:{24:0,36:2,48:3},used_for_control:false}});
  const markup=p.controllerQuality();
  assert.match(markup,/Дорадчий прогноз · 24–48 год/);
  assert.match(markup,/Умовний повтор нагріву/);assert.match(markup,/Розрахований план/);
  assert.match(markup,/0,650/);assert.match(markup,/-0,250/);
  assert.match(markup,/2 \/ 16 \/ 3/);
  assert.match(markup,/36 год: 2/);assert.match(markup,/48 год: 3/);
  assert.match(markup,/не керує котлом/);
  assert.doesNotMatch(markup,/NaN|undefined/);
});
