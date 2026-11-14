export class HeatingAIPanel extends HTMLElement {
  constructor() {
    super(); this.attachShadow({mode:'open'}); this.tab='overview'; this.config={}; this.nativeCards=new Map(); this.nativeConfigs=new Map(); this.pending=new Set();
    this.historyRange=24;this.historyVisible=new Set(['room','return']);this.historyEpoch=0;this.timelineCursor=null;
    this.comparisonDays=7;this.comparisonCache=new Map();this.systemDraft={};this.systemHelp=null;this.systemPending=null;
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
    const history=this.attrs('plan_history');
    if(history.selected?.cycle_id)this.comparisonCache.set(history.selected.cycle_id,history.selected);
    const comparisonChanged=this.acceptComparisonPage(this.attrs('history_page'))||this.acceptComparison(history.comparison);
    this.toggleAttribute('data-light',value.themes?.darkMode===false);
    for(const card of this.nativeCards.values()) card.hass=value;
    const ids=new Set([...Object.values(defaults),...Object.values(this.config.entities||{}),...Object.values(value.states['sensor.heating_ui']?.attributes?.entities||{}),'sensor.heating_ui']);
    ids.delete(this.id('history_page'));
    if(!previous||comparisonChanged||[...ids].some(id=>previous.states[id]!==value.states[id])||previous.themes!==value.themes)this.render();
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
    this.comparisonResume??=()=>{
      if(document.visibilityState!=='hidden'&&this.tab==='model'&&(this.comparisonPending||!this.comparisonData||Date.now()-this.comparisonAttemptAt>=300000)){
        this.cancelComparison();this.loadComparison(true);
      }
    };
    document.addEventListener('visibilitychange',this.comparisonResume);
    window.addEventListener('online',this.comparisonResume);
    this.timer=setInterval(()=>this.render(),60000); this.render();
  }
  disconnectedCallback() {
    clearInterval(this.timer);this.resizeObserver?.disconnect();
    clearTimeout(this.systemTimeout);clearTimeout(this.systemRetryTimeout);this.cancelComparison();
    this.comparisonSignature='';this.comparisonAttemptAt=0;
    globalThis.document?.removeEventListener('visibilitychange',this.comparisonResume);
    window.removeEventListener?.('online',this.comparisonResume);
    cancelAnimationFrame(this.resizeFrame);this.resizeFrame=0;this.observedPlots=new Set();
    cancelAnimationFrame(this.historyFrame);this.historyFrame=0;this.historyEpoch++;
    if(this.historyPending)this.historyAttemptAt=0;
    this.historyPending=null;
  }
  observePlot() {
    const plots=new Set(this.shadowRoot.querySelectorAll('.forecast-plot,.history-plot,.comparison-plot'));
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
  systemFields() {
    const a=this.attrs('system_settings');
    return Array.isArray(a.fields)?a.fields:SYSTEM_SCHEMA.map(f=>({...f,value:a.values?.[f.key]??f.default}));
  }
  time(ts,date=false) {
    const t = typeof ts === 'number' ? ts*1000 : Date.parse(ts);
    return Number.isFinite(t) ? new Intl.DateTimeFormat('uk-UA',{timeZone:this.hass?.config?.time_zone || 'Europe/Kyiv',hour:'2-digit',minute:'2-digit',...(date?{day:'2-digit',month:'2-digit'}:{})}).format(t) : '—';
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
  comparisonWindow() {
    this.comparisonEnd??=Math.floor(Date.now()/300000)*300;
    this.comparisonStart??=this.comparisonEnd-(this.comparisonDays||7)*86400;
    return {start:this.comparisonStart,end:this.comparisonEnd};
  }
  comparisonView() {
    const span=this.comparisonWindow(),view=this.comparisonZoom;
    return view&&view.start>=span.start&&view.end<=span.end?view:span;
  }
  zoomComparison(action) {
    const span=this.comparisonWindow(),view=this.comparisonView();
    if(action==='reset')this.comparisonZoom=null;
    else {
      const duration=Math.min(span.end-span.start,Math.max(6*3600,(view.end-view.start)*(action==='in'?.5:2)));
      const center=numeric(this.comparisonCursor)??(view.start+view.end)/2;
      const start=Math.max(span.start,Math.min(span.end-duration,center-duration/2));
      this.comparisonZoom={start,end:start+duration};
    }
    this.render();
  }
  cancelComparison() {
    clearTimeout(this.comparisonTimeout);clearTimeout(this.comparisonPoll);this.comparisonPending=null;
    this.comparisonAssembly=null;this.comparisonPageRequest=null;
  }
  comparisonDeadline(request) {
    clearTimeout(this.comparisonTimeout);
    this.comparisonTimeout=setTimeout(()=>{
      if(this.comparisonPending===request){this.cancelComparison();this.comparisonError='Історія ще не надійшла. Натисніть оновлення.';this.render();}
    },60000);
  }
  acceptComparisonPage(page) {
    const items=unpackHistoryItems(page);if(!items)return false;
    page={...page,items};
    if(Number(page?.protocol)!==2||page.request_id!==this.comparisonPending||!this.comparisonPending||!Array.isArray(page.items))return false;
    const span=this.comparisonWindow(),offset=numeric(page.offset),total=numeric(page.total);
    if(Number(page.start)!==span.start||Math.abs(Number(page.end)-span.end)>=1||offset===null||total===null||!Number.isInteger(offset)||offset<0||offset+page.items.length>total)return false;
    if(!this.comparisonAssembly) {
      if(offset!==0||typeof page.result_id!=='string')return false;
      this.comparisonAssembly={resultId:page.result_id,offset:0,total,cycles:new Map(),data:{...span,actual:[],cycles:[],markers:[],energy_cycles:[],reviews:[]}};
    }
    const assembly=this.comparisonAssembly;
    if(page.result_id!==assembly.resultId||offset!==assembly.offset||total!==assembly.total)return false;
    for(const item of page.items) {
      const value=item?.value;if(!value)continue;
      if(item.kind==='actual')assembly.data.actual.push({...value,temperature:numeric(value.temperature)});
      else if(item.kind==='cycle') {
        const cycle={...value,forecast:value.forecast?{...value.forecast,curve:[]}:null};
        assembly.cycles.set(cycle.cycle_id,cycle);assembly.data.cycles.push(cycle);
      } else if(item.kind==='forecast')assembly.cycles.get(item.cycle_id)?.forecast?.curve.push({...value,temperature:numeric(value.temperature)});
      else if(item.kind==='marker')assembly.data.markers.push(value);
      else if(item.kind==='energy')assembly.data.energy_cycles.push(value);
      else if(item.kind==='review')assembly.data.reviews.push(value);
      else if(item.kind==='status')Object.assign(assembly.data,value);
      else if(item.kind==='error')assembly.data.error=String(value);
    }
    const next=numeric(page.next_offset);
    if(next!==null&&next!==offset+page.items.length||next===null&&offset+page.items.length!==total){this.cancelComparison();this.comparisonError='Неповна сторінка історії. Повторіть завантаження.';return true;}
    this.comparisonDeadline(this.comparisonPending);
    if(next===null)return this.acceptComparison(assembly.data);
    assembly.offset=next;
    this.comparisonPageRequest={request_id:this.comparisonPending,protocol:2,compact:true,...span,result_id:page.result_id,offset:next};
    queueMicrotask(()=>{if(this.comparisonPending===page.request_id)this.requestComparisonPage();});
    return false;
  }
  async requestComparisonPage() {
    if(!this.comparisonPending||!this.isConnected)return;
    const request=this.comparisonPending,data=this.comparisonPageRequest||{request_id:request,protocol:2,compact:true,...this.comparisonWindow()};
    clearTimeout(this.comparisonPoll);
    try{await this.hass.callApi('POST','events/heating_plan_history',data);}catch{/* Retry the same immutable page after connectivity returns. */}
    if(this.comparisonPending===request&&this.comparisonPageRequest===data) {
      await this.readComparison(request);return;
    }
    if(this.comparisonPending===request)this.comparisonPoll=setTimeout(()=>this.readComparison(request),1500);
  }
  acceptComparison(data) {
    if(!data||isTrue(data.requires_panel_reload)||!Array.isArray(data.actual)||!Array.isArray(data.cycles))return false;
    const span=this.comparisonWindow();
    if(Number(data.start)!==span.start||Math.abs(Number(data.end)-span.end)>=1)return false;
    // The HA entity is shared by clients: matching bounds, not one UUID, identify its data.
    const changed=this.comparisonData!==data||Boolean(this.comparisonPending);
    this.comparisonData=data;this.comparisonError='';this.cancelComparison();return changed;
  }
  async readComparison(request) {
    if(this.comparisonPending!==request||!this.isConnected)return;
    const previousOffset=this.comparisonAssembly?.offset;
    try {
      const record=await this.hass.callApi('GET',`states/${this.id('history_page')}`);
      if(this.comparisonPending!==request)return;
      if(this.acceptComparisonPage(record?.attributes)||this.acceptComparison(record?.attributes?.comparison)){this.render();return;}
    }catch{/* A missed state push or temporary connection loss can recover before the deadline. */}
    if(this.comparisonAssembly?.offset!==previousOffset)return;
    if(!this.comparisonAssembly)try {
      const record=await this.hass.callApi('GET',`states/${this.id('plan_history')}`);
      if(this.comparisonPending===request&&this.acceptComparison(record?.attributes?.comparison)){this.render();return;}
    }catch{/* Compatible with the preceding unpaged release during a browser refresh. */}
    if(this.comparisonPending===request)this.comparisonPoll=setTimeout(()=>this.requestComparisonPage(),5000+request.charCodeAt(0)%500);
  }
  async loadComparison(force=false) {
    if(!this.isConnected||this.tab!=='model'||!this.hass?.callApi||this.comparisonPending)return;
    if(this.comparisonDays&&(force||!this.comparisonAttemptAt||Date.now()-this.comparisonAttemptAt>=300000)){
      this.comparisonEnd=Math.floor(Date.now()/300000)*300;this.comparisonStart=this.comparisonEnd-this.comparisonDays*86400;
    }
    const span=this.comparisonWindow(),signature=`${span.start}:${span.end}`;
    if(!force&&this.comparisonSignature===signature&&Date.now()-this.comparisonAttemptAt<300000)return;
    this.comparisonSignature=signature;this.comparisonAttemptAt=Date.now();this.comparisonError='';
    const request=globalThis.crypto?.randomUUID?.()||`${Date.now()}-${Math.random().toString(36).slice(2)}`;this.comparisonPending=request;
    this.comparisonDeadline(request);
    this.render();
    try{
      await this.hass.callApi('POST','events/heating_plan_history',{request_id:request,protocol:2,compact:true,refresh:force,...span});
      if(this.comparisonPending===request)this.comparisonPoll=setTimeout(()=>this.readComparison(request),1500);
    }
    catch{if(this.comparisonPending===request){this.cancelComparison();this.comparisonError='Не вдалося завантажити історію. Повторіть.';this.render();}}
  }
  setComparisonDays(days) {
    if(![1,7,30].includes(days))return;
    this.comparisonDays=days;this.comparisonEnd=Math.floor(Date.now()/300000)*300;
    this.comparisonStart=this.comparisonEnd-days*86400;this.comparisonDateDraft={};
    this.cancelComparison();this.comparisonCursor=null;
    this.comparisonSignature='';this.render();
  }
  applyComparisonDates() {
    const zone=this.hass.config?.time_zone||'Europe/Kyiv';
    const day=stamp=>new Intl.DateTimeFormat('sv-SE',{timeZone:zone}).format(new Date(stamp*1000));
    const span=this.comparisonWindow(),draft=this.comparisonDateDraft||{};
    const from=draft.from||day(span.start),to=draft.to||day(span.end);
    const next=new Date(to+'T00:00:00Z');next.setUTCDate(next.getUTCDate()+1);
    if(!Number.isFinite(next.getTime()))return;
    const start=calendarMidnight(from,zone),end=Math.min(Date.now()/1000,calendarMidnight(next.toISOString().slice(0,10),zone));
    if(start===null||!Number.isFinite(end)||end<=start||end-start>31*86400){
      this.comparisonError='Оберіть минулий період тривалістю до 31 діб.';this.render();return;
    }
    this.comparisonDays=null;this.comparisonStart=start;this.comparisonEnd=end;
    this.cancelComparison();this.comparisonSignature='';this.comparisonCursor=null;this.render();
  }
  closeSystemHelp() {
    const key=this.systemHelp;this.systemHelp=null;this.render();
    [...this.shadowRoot.querySelectorAll('[data-system-help]')].find(el=>el.dataset.systemHelp===key)?.focus();
  }
  syncSystemSettings() {
    const a=this.attrs('system_settings');
    const sent=this.systemSent;
    const valuesMatch=sent&&numeric(a.persisted_revision??a.revision)>sent.revision&&Object.entries(sent.values).every(([key,value])=>{
      const field=this.systemFields().find(f=>f.key===key);
      return field&&(typeof value==='number'?numeric(field.value)===value:JSON.stringify(field.value)===JSON.stringify(value));
    });
    if((this.systemPending&&a.last_request_id===this.systemPending)||sent&&(a.last_request_id===sent.request||valuesMatch)) {
      if(isTrue(a.last_success)||valuesMatch){
        if(sent?.previous)this.systemUndo={revision:numeric(a.persisted_revision??a.revision),values:sent.previous};
        this.systemDraft={};this.systemMessage=isTrue(a.model_pending)?'Налаштування прийнято; кандидат моделі очікує перевірки':'Зміни збережено';
      }
      else this.systemMessage=a.last_error||'Зміни відхилено';
      this.systemPending=null;this.systemSent=null;clearTimeout(this.systemTimeout);clearTimeout(this.systemRetryTimeout);
    }
  }
  undoSystemSettings() {
    if(!this.systemUndo||this.systemPending)return;
    if(numeric(this.attrs('system_settings').revision)!==this.systemUndo.revision) {
      this.systemMessage='Налаштування вже змінено іншим клієнтом. Перевірте актуальні значення.';this.render();return;
    }
    this.systemDraft={...this.systemUndo.values};this.systemMessage='Попередні значення відновлено у формі. Натисніть «Зберегти».';this.render();
  }
  async saveSystemSettings() {
    if(this.systemPending||!Object.keys(this.systemDraft).length)return;
    if([...this.shadowRoot.querySelectorAll('[data-system-key]')].some(el=>Object.hasOwn(this.systemDraft,el.dataset.systemKey)&&!el.reportValidity())) {
      this.systemMessage='Перевірте значення змінених полів';this.render();return;
    }
    const request=globalThis.crypto?.randomUUID?.()||`${Date.now()}-${Math.random().toString(36).slice(2)}`;
    const previous=Object.fromEntries(this.systemFields().filter(f=>Object.hasOwn(this.systemDraft,f.key)).map(f=>[f.key,f.value]));
    this.systemSent={request,revision:numeric(this.attrs('system_settings').revision)??0,values:{...this.systemDraft},previous};
    this.systemPending=request;this.systemMessage='';this.render();
    try {
      if(this.config?.entry_id) {
        const result=await this.hass.callWS({type:'heating_ai_legacy/settings/save',entry_id:this.config.entry_id,version:[1,0],request_id:request,revision:this.systemSent.revision,values:this.systemSent.values});
        if(!isTrue(result?.last_success))throw new Error(result?.last_error||'Налаштування відхилено');
      } else {
        await this.hass.callApi('POST','events/heating_system_settings',{request_id:request,revision:this.systemSent.revision,values:this.systemSent.values});
      }
      if(this.systemPending!==request)return;
      this.systemTimeout=setTimeout(()=>{if(this.systemPending===request){this.systemMessage='Очікуємо підтвердження контролера: перерахунок може тривати довше. За потреби оновіть панель.';this.render();}},15000);
      this.systemRetryTimeout=setTimeout(()=>{if(this.systemPending===request){this.systemPending=null;this.systemMessage='Підтвердження ще немає. Перевірте з’єднання або повторіть збереження.';this.render();}},60000);
    } catch(error){this.systemPending=null;this.systemMessage=`Не вдалося зберегти: ${error.message||'помилка Home Assistant'}`;}
    this.render();
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
    const el=e.target.closest('[data-tab],[data-info],[data-press],[data-menu],[data-history-range],[data-history-series],[data-history-refresh],[data-forecast-horizon],[data-comparison-days],[data-comparison-zoom],[data-comparison-refresh],[data-comparison-apply],[data-system-help],[data-system-close],[data-system-save],[data-system-reset],[data-system-undo]'); if(!el)return;
    if(el.hasAttribute('data-system-help')){this.systemHelp=el.dataset.systemHelp;this.render();return;}
    if(el.hasAttribute('data-system-close')){this.closeSystemHelp();return;}
    if(el.hasAttribute('data-system-save')){await this.saveSystemSettings();return;}
    if(el.hasAttribute('data-system-undo')){this.undoSystemSettings();return;}
    if(el.hasAttribute('data-system-reset')){this.systemDraft={};this.systemMessage='';this.render();return;}
    if(el.dataset.comparisonZoom){this.zoomComparison(el.dataset.comparisonZoom);return;}
    if(el.dataset.comparisonDays){this.comparisonZoom=null;this.setComparisonDays(Number(el.dataset.comparisonDays));return;}
    if(el.hasAttribute('data-forecast-horizon')){this.forecastExtended=el.dataset.forecastHorizon==='extended';this.render();return;}
    if(el.hasAttribute('data-comparison-refresh')){await this.loadComparison(true);return;}
    if(el.hasAttribute('data-comparison-apply')){this.applyComparisonDates();return;}
    if(el.dataset.historyRange){this.historyRange=Number(el.dataset.historyRange);this.historyCursor=null;this.render();return;}
    if(el.dataset.historySeries){const key=el.dataset.historySeries;if(this.historyVisible.has(key)){if(this.historyVisible.size>1)this.historyVisible.delete(key);}else this.historyVisible.add(key);this.render();return;}
    if(el.hasAttribute('data-history-refresh')){this.loadHistory(true);return;}
    if(el.dataset.tab){this.tab=el.dataset.tab;this.error='';this.render();if(el.dataset.settingsTarget)this.shadowRoot.getElementById(el.dataset.settingsTarget)?.scrollIntoView({block:'start'});return;}
    if(el.hasAttribute('data-menu')){this.dispatchEvent(new CustomEvent('hass-toggle-menu',{bubbles:true,composed:true}));return;}
    if(el.dataset.info){this.dispatchEvent(new CustomEvent('hass-more-info',{detail:{entityId:el.dataset.info},bubbles:true,composed:true}));return;}
    if(el.dataset.press){if(el.dataset.press==='rollback'&&!confirm('Повернути попередню модель? Поточна модель збережеться для наступного відкату.'))return;await this.call(el.dataset.press,'press',{});}
  }
  async changed(e) {
    if(e.target.hasAttribute('data-comparison-date')){this.comparisonDateDraft={...this.comparisonDateDraft,[e.target.dataset.comparisonDate]:e.target.value};return;}
    if(e.target.hasAttribute('data-system-key')) {
      const el=e.target;
      if(el.hasAttribute('data-json'))el.setCustomValidity('');
      if(!el.reportValidity()||el.type==='number'&&numeric(el.value)===null)return;
      if(el.hasAttribute('data-json')){
        try{this.systemDraft[el.dataset.systemKey]=JSON.parse(el.value);el.setCustomValidity('');}
        catch{el.setCustomValidity('Потрібен коректний JSON');el.reportValidity();return;}
      }else this.systemDraft[el.dataset.systemKey]=el.type==='number'?Number(el.value):el.value;
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


