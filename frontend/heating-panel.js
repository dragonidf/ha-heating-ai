/* Heating AI — native Home Assistant panel/card. No tokens or external services. */
const VERSION = '0.5.4';
/* settings-schema:start */
const SYSTEM_SCHEMA = [{"key":"risk_cold_delta","group":"Графік і прогноз","label":"Холодний стрес-сценарій","default":2,"min":0,"max":10,"step":0.5,"unit":"°C","help":"На скільки холодніша погода в окремій перевірці плану. Сценарій не підміняє погоду й не додає команди нагріву.","target":"config","kind":"number"},{"key":"risk_blackout_short_minutes","group":"Графік і прогноз","label":"Коротке аварійне відключення","default":60,"min":5,"max":240,"step":5,"unit":"хв","help":"Гіпотетична втрата останніх хвилин нічного живлення за невідомого/аварійного графіка. Це перевірка без перепланування, не прогноз аварії.","target":"config","kind":"number"},{"key":"risk_blackout_long_minutes","group":"Графік і прогноз","label":"Довге аварійне відключення","default":120,"min":5,"max":480,"step":5,"unit":"хв","help":"Другий стрес-сценарій. Не гарантує комфорт за довшого blackout і не дозволяє денний нагрів поза обраною політикою.","target":"config","kind":"number"},{"key":"outage_max_age","group":"Графік і прогноз","label":"Свіжість даних Svitlo","default":10800,"min":300,"max":21600,"step":60,"unit":"с","help":"Максимальний вік updated. 10800 с = 3 години. Старіші дані дають unknown; фізичний стан мережі перевіряється окремо.","target":"config","kind":"number"},{"key":"outage_advance_limit","group":"Графік і прогноз","label":"Ліміт завчасного нагріву","default":180,"min":0,"max":360,"step":5,"unit":"хв","help":"Максимальне перенесення нагріву перед відключенням поза дозволеним вікном. Застосовується лише за чинними перевірками комфорту й відомої мережі.","target":"config","kind":"number"},{"key":"morning_min_minutes","group":"Графік і прогноз","label":"Мінімальний ранковий сеанс","default":30,"min":5,"max":30,"step":5,"unit":"хв","help":"Мінімальна тривалість ранкового сеансу за наявності потреби в теплі; не змушує котел гріти без потреби.","target":"config","kind":"number"},{"key":"weather_max_age","group":"Графік і прогноз","label":"Свіжість погодинної погоди","default":10800,"min":300,"max":10800,"step":60,"unit":"с","help":"Вік останнього отриманого прогнозу. Після цієї межі використовується чинний резервний розрахунок без погодинних даних.","target":"config","kind":"number"},{"key":"room_max_age","group":"Датчики","label":"Свіжість кімнатного датчика","default":14400,"min":300,"max":14400,"step":60,"unit":"с","help":"Верхня межа віку саме температури, не вологості. За достатньої історії попередження діє після 1,5 типового інтервалу звітів, непридатність — після 3 інтервалів, але не пізніше цієї межі. Резерв потребує свіжого термостата й підтвердженого зміщення.","target":"config","kind":"number"},{"key":"thermostat_temperature_max_age","group":"Датчики","label":"Свіжість резервної температури","default":5400,"min":300,"max":14400,"step":60,"unit":"с","help":"Граничний вік звіту термостата для резервної температури. Потрібне також підтверджене зміщення, не старше доби. Не змінює перевірку прийняття команд термостатом.","target":"config","kind":"number"},{"key":"return_max_age","group":"Датчики","label":"Свіжість датчика обратки","default":1200,"min":60,"max":1200,"step":60,"unit":"с","help":"Після цього віку датчик не вважається придатним для автоматичного керування. Верхня межа залишається 20 хвилин.","target":"config","kind":"number"},{"key":"fireplace_max_age","group":"Датчики","label":"Свіжість датчика каміна","default":14400,"min":300,"max":14400,"step":60,"unit":"с","help":"Застарілий датчик не може створювати автоматичний запит циркуляції.","target":"config","kind":"number"},{"key":"fire_ignite_delta","group":"Камін","label":"Різниця для розпалювання","default":2,"min":0,"max":5,"step":0.1,"unit":"°C","help":"Температура каміна мінус температура кімнати. Разом із додатним трендом визначає розпалювання; сама фаза не запускає вентилятор.","target":"config","kind":"number"},{"key":"fire_start_delta","group":"Камін","label":"Запуск корисного тепла","default":5,"min":2,"max":15,"step":0.1,"unit":"°C","help":"Від цієї різниці формується запит циркуляції. Має бути вище порогів вимкнення та швидкого згасання.","target":"config","kind":"number"},{"key":"fire_intense_delta","group":"Камін","label":"Інтенсивне горіння","default":10,"min":5,"max":30,"step":0.1,"unit":"°C","help":"Від цієї різниці вдень обирається рівень 3, якщо тренд не вказує на згасання.","target":"config","kind":"number"},{"key":"fire_stop_delta","group":"Камін","label":"Різниця для вимкнення","default":3,"min":1,"max":10,"step":0.1,"unit":"°C","help":"Нижче цього порога починається безперервна витримка вимкнення. Повернення до порога скидає таймер.","target":"config","kind":"number"},{"key":"fire_fast_stop_delta","group":"Камін","label":"Різниця швидкого вимкнення","default":4,"min":1,"max":15,"step":0.1,"unit":"°C","help":"Нижче цього перепаду та за сильного негативного тренду запит знімається без витримки.","target":"config","kind":"number"},{"key":"fire_ignite_slope","group":"Камін","label":"Тренд розпалювання","default":0.8,"min":0,"max":5,"step":0.1,"unit":"°C/год","help":"Тренд має бути більшим за це значення, щоб позначити розпалювання за достатньої різниці температур.","target":"config","kind":"number"},{"key":"fire_fade_slope","group":"Камін","label":"Тренд згасання","default":-0.8,"min":-5,"max":0,"step":0.1,"unit":"°C/год","help":"Тренд нижче цього значення позначає згасання й повертає денну інтенсивну швидкість до стандартної.","target":"config","kind":"number"},{"key":"fire_fast_stop_slope","group":"Камін","label":"Сильне охолодження","default":-2,"min":-10,"max":-0.1,"step":0.1,"unit":"°C/год","help":"Тренд не вище цієї межі разом із низькою різницею температур знімає запит одразу.","target":"config","kind":"number"},{"key":"fire_cooldown_seconds","group":"Камін","label":"Витримка вимкнення","default":300,"min":60,"max":600,"step":15,"unit":"с","help":"Безперервний час нижче порога вимкнення; 300 с = 5 хвилин. Прогалини вимірювань не зараховуються.","target":"config","kind":"number"},{"key":"fire_trend_seconds","group":"Камін","label":"Вікно оцінки тренду","default":1800,"min":300,"max":3600,"step":60,"unit":"с","help":"Глибина історії різниці температур для оцінки тренду. При короткій історії зберігається чинне згладжування.","target":"config","kind":"number"},{"key":"fire_continuity_seconds","group":"Камін","label":"Допустима перерва вимірів","default":90,"min":30,"max":90,"step":15,"unit":"с","help":"Більша перерва скидає відлік безперервного низького перепаду. Не змінює період controller tick.","target":"config","kind":"number"},{"key":"fan_night_start","group":"Циркуляція","label":"Початок нічної циркуляції","default":"23:00","target":"config","kind":"time","help":"Час місцевої зони Home Assistant. Межі мають відрізнятися; в нічному вікні рівень 1 має пріоритет над інтенсивним горінням."},{"key":"fan_night_end","group":"Циркуляція","label":"Кінець нічної циркуляції","default":"07:00","target":"config","kind":"time","help":"Час місцевої зони Home Assistant. Межі мають відрізнятися; в нічному вікні рівень 1 має пріоритет над інтенсивним горінням."},{"key":"floor_air","group":"Теплова модель","label":"Передавання тепла в кімнату","help":"Вплив різниці між еквівалентною температурою накопиченого тепла та кімнатою. Більше значення — швидша передача тепла. Зміна проходить чинну перевірку моделі на історії; без достатніх даних може бути відхилена. Попередню модель можна відновити.","default":0.035,"min":0.001,"max":0.2,"step":0.0001,"unit":"","target":"model","kind":"number"},{"key":"room_loss","group":"Теплова модель","label":"Тепловтрати кімнати","help":"Вплив різниці між кімнатою й вулицею. Більше значення — швидше прогнозоване охолодження. Зміна проходить чинну перевірку моделі на історії; без достатніх даних може бути відхилена. Попередню модель можна відновити.","default":0.025,"min":0.001,"max":0.15,"step":0.0001,"unit":"","target":"model","kind":"number"},{"key":"floor_decay","group":"Теплова модель","label":"Охолодження накопиченого тепла","help":"Темп зменшення запасу тепла. Обратка є індикатором запасу, а не температурою поверхні підлоги. Зміна проходить чинну перевірку моделі на історії; без достатніх даних може бути відхилена. Попередню модель можна відновити.","default":0.12,"min":0.01,"max":1.5,"step":0.0001,"unit":"","target":"model","kind":"number"},{"key":"heat_gain","group":"Теплова модель","label":"Накопичення тепла від котла","help":"Вплив виміряної потужності котла на еквівалентний тепловий запас. Зміна проходить чинну перевірку моделі на історії; без достатніх даних може бути відхилена. Попередню модель можна відновити.","default":0.5,"min":0.02,"max":3.0,"step":0.0001,"unit":"","target":"model","kind":"number"},{"key":"fire_gain","group":"Теплова модель","label":"Вплив тепла каміна","help":"Внесок різниці температур каміна та кімнати в прогноз температури. Не змінює пороги циркуляції. Зміна проходить чинну перевірку моделі на історії; без достатніх даних може бути відхилена. Попередню модель можна відновити.","default":0.025,"min":0.0,"max":0.15,"step":0.0001,"unit":"","target":"model","kind":"number"},{"key":"boiler_power_mode","group":"Котел і вимірювання","label":"Режим на котлі","default":"unconfirmed","target":"config","kind":"select","options":{"unconfirmed":"Не підтверджено","fixed_3":"3 кВт","fixed_6":"6 кВт","fixed_9":"9 кВт","auto":"Авто — котел обирає потужність"},"help":"Вкажіть режим, який ви виставили на самому котлі. Цей вибір не перемикає котел. Фіксована номінальна потужність допускає внутрішні відсічення; авто навчається окремо."},{"key":"recuperator_entity","group":"Вентиляція","label":"Рекуператор","default":"","target":"config","kind":"entity","help":"Виберіть перемикач або вентилятор пристрою зі списку Home Assistant. Це врахування стану для прогнозу; автоматики керування не змінюються. «Не підключено» вимикає врахування цього пристрою; недоступність не означає вимкнений стан."},{"key":"bath_entity","group":"Вентиляція","label":"Витяжка ванної","default":"","target":"config","kind":"entity","help":"Виберіть перемикач або вентилятор пристрою зі списку Home Assistant. Це врахування стану для прогнозу; автоматики керування не змінюються. «Не підключено» вимикає врахування цього пристрою; недоступність не означає вимкнений стан."},{"key":"power_measurement_policy","group":"Котел і вимірювання","label":"Перевірка вимірювань","default":"legacy","target":"config","kind":"select","options":{"legacy":"Попередній алгоритм + shadow A/B","audited":"Узгоджені A/B; непевні дані — unknown"},"help":"Узгоджені A/B перевіряють кожний timestamp і не оцінюють котел за порогом фази чи запитом термостата. За відсутності даних контролер використовує чинний безпечний резерв. Новий окремий облік якості не переписує старі кВт·год."},{"key":"power_partial_reserve","group":"Котел і вимірювання","label":"Резерв при втраті одного лічильника","default":"disabled","target":"config","kind":"select","options":{"disabled":"Штатний auto при втраті обліку","enabled":"Продовжити орієнтовний план"},"help":"Для чинного балансу: загальний ввід, три споживчі групи й GRID інвертора. Справні складові віднімаються; за однієї невідомої групи залишок є лише верхньою межею. План використовує припущення фізичного fixed_3/6/9 і запит термостата. Невідомі Вт та кВт·год не стають виміряними, не навчають модель і не додають тепла до запасу підлоги. Дві несправні групи, втрата вводу/GRID, невідомий режим або несвіжі дані залишають штатний auto. Точність енергії у резерві не підтверджена."},{"key":"power_phase_topology","group":"Котел і вимірювання","label":"Підтверджена схема фаз · JSON","default":{"verified":false,"phases":[]},"target":"config","kind":"json","help":"Для кожної з 3 фаз: input, loads [{entity, physical_id}], background_w і uncertainty_w. GRID інвертора включіть як одне підписане навантаження на підтвердженій фазі. physical_id має бути єдиним для двох інтеграцій того самого приладу. verified=true лише після перевірки реального розміщення лічильників. Непідтверджена схема не застосовується."},{"key":"outage_missing_policy","group":"Електромережа","label":"Сумісність політики графіка","default":"unknown","target":"config","kind":"select","options":{"assume_available":"Перевіряти відсутність обмежень","unknown":"Залишати невизначеність"},"help":"Старий параметр збережено для сумісності. Жоден його варіант не перетворює недоступне чи застаріле джерело на підтверджене світло. Нормальний режим потребує свіжого позитивного підтвердження Svitlo; частковий графік залишається умовним."},{"key":"power_accounting_method","group":"Котел і вимірювання","label":"Джерело обліку котла","default":"legacy","target":"config","kind":"select","options":{"legacy":"Попередній баланс","inlet":"Ввід: основний і резервний баланс","kws":"KWS · прямий фазовий вимір і лічильник"},"help":"Основний метод віднімає три групи лічильників і Deye GRID; локальні та хмарні дублікати є альтернативами. Резерв віднімає окремі неперекривні прилади, перевіряє фази вводу, запит і режим котла. Номінальні 3/6/9 кВт — розрахунок, не незалежний вимір. Старий лічильник зберігається."},{"key":"power_inlet_config","group":"Котел і вимірювання","label":"Ввід і неперекривні споживачі · JSON","default":{},"target":"config","kind":"json","help":"total — загальна активна потужність; phases — три фазові потужності цього вводу. loads: physical_id, entities (локальний сенсор і його резервні дублікати), signed для GRID Deye, phase 0/1/2 або null. Прилад віднімається один раз. Optional groups: три групові лічильники з physical_id/entities. Основний метод не віднімає loads повторно, крім signed GRID. heartbeats: сенсор потужності → список напруг того самого приладу; лише тихі 0–50 Вт можуть лишатися явно утриманою оцінкою за свіжої напруги, не свіжим виміром. Не додавайте LOAD/АКБ та прилади всередині вже врахованої гілки GRID. Невідомі фази перевіряються перебором; відсутні чи застарілі значення не замінюються нулем."},{"key":"forecast_hours","group":"Системні захисти","label":"Горизонт прогнозу","unit":"год","help":"Фіксований горизонт чинного планувальника. Ця панель не змінює алгоритм прогнозування.","kind":"readonly","target":"fixed"},{"key":"weather_tolerance","group":"Системні захисти","label":"Допуск покриття погоди","unit":"с","help":"Один погодинний інтервал. Більший дефіцит показує попередження; ця діагностична константа не редагується.","kind":"readonly","target":"fixed"},{"key":"boiler_min_on","group":"Системні захисти","label":"Мінімальна робота котла","unit":"с","help":"Чинний захист від частих перемикань; аварійна зупинка має пріоритет. Не редагується з панелі.","kind":"readonly","target":"fixed"},{"key":"boiler_min_off","group":"Системні захисти","label":"Пауза між запусками котла","unit":"с","help":"Чинний захист повторного запуску. Не редагується з панелі.","kind":"readonly","target":"fixed"},{"key":"fan_retry","group":"Системні захисти","label":"Повтор команди вентилятора","unit":"с","help":"Обмеження повторів команд, включно з повтором після помилки. Збережено як системний захист.","kind":"readonly","target":"fixed"},{"key":"inverter_fresh","group":"Системні захисти","label":"Свіжість інвертора","unit":"с","help":"Незмінний захист фактичної доступності живлення. Графік Svitlo не замінює цю перевірку.","kind":"readonly","target":"fixed"},{"key":"grid_input_fresh","group":"Системні захисти","label":"Свіжість вхідної мережі","unit":"с","help":"Незмінний захист фактичного grid monitoring. Не налаштовується через панель.","kind":"readonly","target":"fixed"},{"key":"return_limit","group":"Системні захисти","label":"Межа теплового запасу в прогнозі","unit":"°C","help":"Чинна верхня межа еквівалентної температури, не температура поверхні підлоги. Не редагується з панелі.","kind":"readonly","target":"fixed"}];
/* settings-schema:end */
export const escapeHTML = value => String(value ?? '—').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export const numeric = value => value !== null && value !== undefined && String(value).trim() !== '' && Number.isFinite(Number(value)) ? Number(value) : null;
export const isTrue = value => value === true || value === 'true' || value === 'on';
export const mergeSegments = rows => (Array.isArray(rows) ? rows : []).filter(r => numeric(r.start) !== null && numeric(r.end) !== null && r.end > r.start).sort((a,b)=>a.start-b.start).reduce((out,row) => {
  const last = out.at(-1);
  if (last && row.start <= last.end + 1 && isTrue(row.conditional) === isTrue(last.conditional)) {
    if(Object.hasOwn(last,'kwh')||Object.hasOwn(row,'kwh')) {
      last.kwh=numeric(last.kwh)!==null&&numeric(row.kwh)!==null&&row.start>=last.end-.001?Number(last.kwh)+Number(row.kwh):null;
      last.power_kw=last.kwh===null?null:last.kwh*3600/(Math.max(last.end,row.end)-last.start);
    }
    last.end = Math.max(last.end,row.end);
  }
  else out.push({...row});
  return out;
}, []);
const icon = name => `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">${{
  home:'<path d="m3 10 9-7 9 7v10H3Z"/><path d="M9 20v-7h6v7"/>',
  menu:'<path d="M4 6h16M4 12h16M4 18h16"/>',
  info:'<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7v1"/>',
  refresh:'<path d="M20 7v5h-5"/><path d="M19 12a7 7 0 1 1-2-5l3 3"/>'
}[name]||''}</svg>`;
const h = escapeHTML;
const fmt = (v,d=1) => numeric(v) === null ? '—' : Number(v).toLocaleString('uk-UA',{maximumFractionDigits:d,minimumFractionDigits:d});
export const calendarMidnight = (day,timeZone) => {
  if(!/^\d{4}-\d{2}-\d{2}$/.test(day))return null;
  const target=Date.parse(day+'T00:00:00Z');if(!Number.isFinite(target)||new Date(target).toISOString().slice(0,10)!==day)return null;
  let stamp=target;
  const format=new Intl.DateTimeFormat('sv-SE',{timeZone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'});
  for(let i=0;i<3;i++)stamp-=Date.parse(format.format(new Date(stamp)).replace(' ','T')+'Z')-target;
  return stamp/1000;
};
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
  power_model:'sensor.heating_power_model',ventilation:'sensor.heating_ventilation',diagnostics:'sensor.heating_diagnostics',process:'binary_sensor.appdaemon_running',controller:'sensor.heating_controller', alive:'binary_sensor.heating_controller_alive', permitted:'binary_sensor.heating_actuation_permitted',
  heartbeat:'sensor.heating_heartbeat', temperature:'sensor.heating_temperature', power_confirmed:'sensor.heating_power_confirmed', boiler_active:'binary_sensor.heating_boiler_active',boiler_stage:'sensor.heating_boiler_stage',
  grid_status:'sensor.heating_grid', fireplace_status:'sensor.heating_fireplace', fireplace_active:'binary_sensor.heating_fireplace_active',
  energy_24h:'sensor.heating_energy_24h', energy_monthly:'sensor.heating_energy_monthly', energy_accounted_monthly:'sensor.heating_accounted_monthly', forecast:'sensor.heating_forecast', forecast_outlook:'sensor.heating_forecast_outlook',
  outages:'sensor.heating_outage_schedule', model:'sensor.heating_model', environment:'sensor.heating_environment', summary:'sensor.heating_ai_summary',ai_report:'input_text.heating_ai_report',controller_quality:'sensor.heating_controller_quality',
  restore_status:'sensor.heating_restore_status', fan_status:'sensor.heating_fan',
  plan_history:'sensor.heating_plan_history',history_page:'sensor.heating_history_page',system_settings:'sensor.heating_system_settings',
  target:'input_number.heating_target',lower:'input_number.heating_lower',upper:'input_number.heating_upper',
  night_target:'input_number.heating_night_target',night_lower:'input_number.heating_night_lower',night_upper:'input_number.heating_night_upper',
  sleep_start:'input_datetime.heating_sleep_start',sleep_end:'input_datetime.heating_sleep_end',start:'input_datetime.heating_start',end:'input_datetime.heating_end',
  monthly_budget:'input_number.heating_monthly_budget',session_limit:'input_number.heating_session_limit',native_setpoint:'input_number.heating_native_setpoint',
  mode:'input_select.heating_mode',strategy:'input_select.heating_strategy',armed:'input_boolean.heating_armed',restore_once:'input_boolean.heating_restore_once',
  fan_control:'input_boolean.heating_fan_control',fan_mode:'input_select.heating_fan_mode',fan_speed:'input_number.heating_fan_speed',fan_intense_speed:'input_number.heating_fan_intense_speed',fan_night_limit:'input_number.heating_fan_night_limit',
  ai_auto_apply:'input_boolean.heating_ai_auto_apply',rollback:'input_button.heating_model_rollback',weather_refresh:'input_button.heating_forecast_refresh'
};
export function unpackHistoryItems(page) {
  if(!page||!Array.isArray(page.items))return null;
  if(!page.encoding)return page.items;
  if(page.encoding!=='columns-v1'||!Array.isArray(page.cycle_ids))return null;
  const columns=[['ts','temperature','lower','upper'],['ts','temperature','lower','upper','target','heat_segments']],result=[];
  for(const row of page.items) {
    if(!Array.isArray(row))return null;
    const [kindRaw,cycleRaw,maskRaw,...values]=row,kind=numeric(kindRaw),cycle=numeric(cycleRaw),mask=numeric(maskRaw);
    if(kind===2){if(!row[1]||typeof row[1]!=='object')return null;result.push(row[1]);continue;}
    if(![0,1].includes(kind)||!Number.isInteger(mask)||mask<0||mask>=(1<<columns[kind].length))return null;
    if(kind===1&&(!Number.isInteger(cycle)||typeof page.cycle_ids[cycle]!=='string'))return null;
    const value={};let cursor=0;
    columns[kind].forEach((key,i)=>{if(mask&(1<<i)){const raw=values[cursor++];value[key]=raw==='~'?null:raw;}});
    if(cursor!==values.length)return null;
    result.push({kind:kind===0?'actual':'forecast',value,...(kind===1?{cycle_id:page.cycle_ids[cycle]}:{})});
  }
  return result;
}

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
  font-size:0.875rem;line-height:1.5;font-variant-numeric:tabular-nums;
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
.brand strong{font-size:1.375rem;font-weight:600;letter-spacing:-.5px;line-height:1.3}
.brand strong span{color:var(--green)}
.brand small{display:block;color:var(--muted);font-size:0.75rem;letter-spacing:0}
.header-status{display:flex;align-items:center;gap:8px;font-size:0.75rem;white-space:nowrap}
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
nav button{border:0;color:var(--muted);padding:9px 18px;font-size:0.875rem;font-weight:500}
nav button{min-width:0;overflow-wrap:anywhere;white-space:normal}
nav button.selected{background:var(--raised);color:var(--primary-text-color,#e8e8e8)}
a{color:var(--green);text-decoration:none}
nav a{margin-left:auto;font-size:0.75rem;color:var(--muted)}
.card{background:var(--surface);border:1px solid var(--stroke);border-radius:var(--card-radius);padding:20px;margin-bottom:12px;min-width:0;box-shadow:var(--ha-card-box-shadow,none)}
h1,h2,p{margin:0}
h1{font-size:1.5rem;line-height:1.3;font-weight:600;letter-spacing:-.5px}
h2{font-size:1rem;font-weight:600;letter-spacing:0;line-height:1.4;margin-bottom:12px;text-wrap:balance}
p{margin:10px 0}
.muted,.eyebrow{font-size:0.75rem;font-weight:400;letter-spacing:0;line-height:1.4;display:block}
.eyebrow{font-size:0.6875rem;font-weight:500;letter-spacing:1.3px;display:block}
.hero{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);align-items:center;gap:24px;background:var(--surface)}
.temperature{font-size:4rem;line-height:1.1;letter-spacing:-2px;font-weight:500;margin-top:8px}
.temperature span{font-size:1.5rem;color:var(--muted);letter-spacing:0;vertical-align:top;margin-left:4px;line-height:1.5}
.hero p{margin:6px 0 16px}
.hero-side{border-left:1px solid var(--stroke);padding-left:24px}
.hero-side h2{font-size:1.375rem;margin:8px 0;line-height:1.35}
.comfort{margin-top:25px;font-size:0.75rem;color:var(--muted)}
.comfort strong{display:block;font-size:1.125rem;color:var(--primary-text-color)}
.pill{display:inline-block;background:var(--raised);border:1px solid transparent;color:var(--primary-text-color,#e8e8e8);border-radius:999px;padding:4px 10px;font-size:0.75rem;line-height:1.4}
.warn{color:var(--source-yellow);background:var(--warm-fill);border-color:var(--stroke)}
.metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:18px}
.metric{display:flex;flex-direction:column;align-items:flex-start;text-align:left;gap:12px;padding:22px}
.metric strong{font-size:1.75rem;font-weight:500;line-height:1.2;letter-spacing:-.8px}
.metric strong small{font-size:0.8125rem;color:var(--muted);letter-spacing:0}
.metric .muted{font-size:0.6875rem}
.columns{display:grid;grid-template-columns:minmax(0,1.85fr) minmax(300px,1fr);gap:20px}
.section-meta{display:flex;justify-content:space-between;align-items:center;gap:10px;font-size:0.75rem;margin-bottom:12px}
.forecast-plot{width:100%;min-width:0;touch-action:pan-y}
.forecast-plot:focus-visible{outline:2px solid var(--primary-color);outline-offset:3px;border-radius:8px}
.chart-card svg{width:100%;height:auto;max-width:100%;display:block;color:var(--muted)}
.legend{display:flex;flex-wrap:wrap;gap:8px 16px;font-size:0.75rem;color:var(--muted);margin:12px 0}
.legend span:first-child{color:var(--green)}
.legend span:nth-child(2){color:var(--source-red)}
.legend span:nth-child(3){color:var(--source-yellow)}
.forecast-stats{display:grid;grid-template-columns:1fr 1fr;gap:0 24px}
.row{display:flex;align-items:baseline;justify-content:space-between;gap:12px;padding:10px 0;border-bottom:1px solid var(--stroke);font-size:0.75rem}
.row strong{font-weight:500;text-align:right;overflow-wrap:anywhere}
.row:last-child{border-bottom:0}
.note{font-size:0.75rem;line-height:1.6}
.session{display:flex;justify-content:space-between;gap:10px;padding:16px 0;border-bottom:1px solid var(--stroke)}
.session strong{font-size:0.8125rem}
.session .muted{display:block;font-size:0.6875rem}
.heat-dot{width:7px;height:7px;flex:none;background:var(--source-red);border-radius:50%;margin-top:8px}
.inset{background:var(--raised);border-radius:14px;padding:16px;margin-top:16px}
.large{font-size:2rem;letter-spacing:-1px}
.large small{font-size:0.875rem;color:var(--muted)}
progress{width:100%;height:6px;accent-color:var(--green);margin-top:18px;border:0;border-radius:8px;overflow:hidden}
progress::-webkit-progress-bar{background:var(--stroke)}
progress::-webkit-progress-value{background:var(--green)}
.text-button{border:0;color:var(--green);padding-left:0;font-size:0.75rem;margin-top:12px}
.page-title{margin:30px 0}
.settings-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,420px),1fr));gap:0 20px}
.field{display:flex;align-items:center;justify-content:space-between;gap:20px;padding:15px 0;border-top:1px solid var(--stroke);font-size:0.8125rem}
.field small{display:block;font-size:0.6875rem}
.field input,.field select{background:var(--raised);color:inherit;border:1px solid var(--control-stroke);border-radius:12px;padding:8px 10px;max-width:220px;min-height:44px}
.number{display:flex;align-items:center;gap:8px}
.number input{width:85px}
.number span{font-size:0.6875rem;color:var(--muted)}
.toggle{position:relative;display:inline-flex;min-width:44px;min-height:44px;align-items:center}
.toggle input{opacity:0;position:absolute;inset:0;width:100%;margin:0}
.toggle span{width:44px;height:26px;border-radius:26px;background:var(--control-stroke);pointer-events:none}
.toggle span:after{content:'';display:block;width:20px;height:20px;border-radius:50%;background:var(--primary-text-color,#e8e8e8);transform:translate(3px,3px);transition:transform 160ms var(--ease-out)}
.toggle input:checked+span{background:var(--green)}
.toggle input:checked+span:after{background:var(--surface);transform:translate(21px,3px)}
.toggle:focus-within{outline:2px solid var(--green);border-radius:24px}
.table-scroll{overflow:auto}
table{width:100%;border-collapse:collapse;font-size:0.8125rem}
th{text-align:left;font-size:0.6875rem;color:var(--muted);font-weight:400}
td,th{padding:12px 8px;border-bottom:1px solid var(--stroke)}
td:not(:first-child),th:not(:first-child){text-align:right}
.journal{max-height:600px;overflow:auto}
.entry{display:grid;grid-template-columns:90px 1fr;gap:15px;border-bottom:1px solid var(--stroke);padding:14px 0}
.entry time{font-size:0.6875rem;color:var(--muted)}
.entry p{font-size:0.75rem;margin:0}
.columns>div{min-width:0}
.native{width:100%;max-width:100%;min-width:0;min-height:110px;--ha-card-border-width:0;--ha-card-box-shadow:none}
.native>*{display:block;width:100%;max-width:100%;min-width:0}
.empty{color:var(--muted);padding:20px 0;font-size:0.8125rem}
.field .system-help{min-height:44px;min-width:44px;margin:0}
.system-field textarea{box-sizing:border-box;width:100%;max-width:100%;min-width:0;font:16px/1.5 monospace;background:var(--surface);color:var(--ink);border:1px solid var(--stroke);border-radius:10px;padding:8px}.system-field:has(textarea){grid-column:1/-1}
.health-details{display:block;margin-top:5px;font-size:0.6875rem;line-height:1.5;overflow-wrap:anywhere;color:var(--muted)}
.notice{border-radius:14px;background:var(--warm-fill);border:1px solid var(--stroke);color:var(--source-yellow);padding:12px 16px;margin-bottom:12px;font-size:0.8125rem}
.notice.warning{background:var(--warm-fill);color:var(--source-yellow)}
.notice.error{background:color-mix(in srgb,var(--red,#d9534f) 16%,var(--surface));border-color:color-mix(in srgb,var(--red,#d9534f) 55%,var(--stroke));color:var(--red,#d9534f)}
.hidden{display:none}
footer{display:flex;flex-wrap:wrap;justify-content:space-between;gap:12px;color:var(--muted);font-size:0.6875rem;padding:12px 2px 16px}
.menu-button{min-width:44px;border:0;font-size:1.25rem;padding:2px 6px}

.history-tools{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-top:-4px;margin-bottom:12px}
.history-ranges{display:flex;gap:2px;padding:2px;border-radius:14px;background:var(--raised)}
.history-ranges button{border:0;padding:6px 14px;font-size:0.75rem;min-height:44px;color:var(--muted)}
.history-ranges button[aria-pressed="true"]{background:var(--surface);color:var(--primary-text-color,#e8e8e8)}
.history-refresh{font-size:1.5rem;width:44px;height:44px;padding:0;border:0;color:var(--muted)}
.history-legend{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin:8px 0 18px}
.history-legend button{min-width:0;text-align:left;border-color:transparent;padding:8px 10px;background:transparent;border-radius:12px}
.history-legend button>span{display:flex;align-items:center;gap:6px;font-size:0.6875rem;color:var(--muted)}
.history-legend i,.history-totals i{display:inline-block;width:7px;height:7px;flex-shrink:0;border-radius:50%;background:var(--series-color)}
.history-legend strong{display:block;font-weight:500;font-size:1.25rem;white-space:nowrap}
.history-legend strong small{font-size:0.6875rem;color:var(--muted)}
.history-legend button[aria-pressed="true"]{border-color:var(--control-stroke);background:var(--raised)}
.history-legend button[aria-pressed="false"] i{background:transparent;border:1px solid var(--series-color)}
.history-legend button[aria-pressed="false"] strong{color:var(--muted)}
.history-plot{width:100%;min-width:0;touch-action:pan-y}
.history-plot:focus-visible{outline:2px solid var(--green);outline-offset:3px;border-radius:6px}
.history-plot svg{display:block;width:100%;height:auto;max-width:100%;color:var(--muted);overflow:visible}
.history-totals{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin:8px 0 0;padding-top:10px;border-top:1px solid var(--stroke);font-size:0.6875rem;color:var(--muted)}
.history-totals>span{min-width:0}
.history-totals i{margin-right:7px}
.history-totals strong{display:block;color:var(--primary-text-color);font-size:0.9375rem;font-weight:500;padding-left:14px}
.history-readout{font-size:0.6875rem;margin:10px 0 0;min-height:18px}
.history-placeholder{min-height:330px;display:grid;place-items:center;font-size:0.8125rem;color:var(--muted)}

@container(max-width:600px){.history-ranges button{min-height:44px;padding:5px 10px}
.history-legend{gap:4px}
.history-legend button{padding:7px 6px}
.history-legend button>span{font-size:0.6875rem;gap:4px}
.history-legend strong{font-size:1.125rem}
.history-tools{margin-bottom:6px}
.history-readout{font-size:0.6875rem}
}

section[id]{scroll-margin-top:16px}

@container(min-width:1600px){main{padding:24px 42px}
}
@container(max-width:1000px){main{padding:15px 20px}
.columns{grid-template-columns:1fr}
.hero{gap:25px}
.metrics{gap:12px}
.metric{padding:17px}
.metric strong{font-size:1.4375rem}
.mode{display:none}
}
.fireplace-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-top:10px}
.fire-block,.circulation-block{min-width:0;min-height:0;aspect-ratio:1/1;display:flex;flex-direction:column;justify-content:center;align-items:flex-start;overflow:hidden}
.fire-block .fire-temperature,.circulation-block .fan-actual{overflow-wrap:anywhere}
@container(max-width:600px){.fireplace-card{padding:10px 12px}.fireplace-grid{gap:8px}.fire-block,.circulation-block{padding:4px 8px;aspect-ratio:auto;height:auto}.fire-block .eyebrow,.circulation-block .eyebrow{font-size:.6875rem}.fire-block .fire-temperature,.circulation-block .fan-actual{font-size:1rem}.fire-details{margin-top:8px}.fire-details summary{min-height:36px}}
@container(max-width:600px){main{padding:8px 12px}
header{padding:8px 2px 18px;gap:10px}
.brand strong{font-size:1.25rem}
.brand-icon{width:26px;height:38px}
.header-status{font-size:0.6875rem}
.header-status .dot{display:block}
nav{gap:3px;margin-bottom:16px}
nav button{padding:10px 12px;font-size:0.75rem}
nav a{display:none}
.hero{grid-template-columns:minmax(0,.85fr) minmax(0,1.15fr);padding:18px;gap:14px;align-items:center}
.hero>div{min-width:0}
.temperature{font-size:2.75rem;font-size:2.625rem;letter-spacing:-1.5px;white-space:nowrap}
.temperature span{font-size:1.25rem;letter-spacing:-1px;margin-left:3px}
.hero p{font-size:0.6875rem;line-height:1.5;margin:6px 0 10px}
.hero .pill{font-size:0.6875rem;letter-spacing:0;padding:4px 8px}
.hero-side{padding:0 0 0 14px;border-left:1px solid var(--stroke);border-top:0}
.hero-side h2{font-size:0.9375rem;line-height:1.4;margin:8px 0}
.hero .eyebrow{font-size:0.75rem;letter-spacing:0}
.comfort{margin-top:10px;font-size:0.6875rem}
.comfort strong{font-size:0.875rem}
.metrics{grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
.metric{padding:16px;gap:10px;border-radius:16px}
.metric strong{font-size:1.4375rem}
.eyebrow{font-size:0.6875rem;letter-spacing:.8px}
.card{padding:18px;border-radius:var(--card-radius);margin-bottom:14px}
.columns{gap:0}
.settings-grid{grid-template-columns:1fr}
.field{gap:12px;font-size:0.75rem}
.field select{max-width:170px;font-size:0.6875rem}
.section-meta{flex-wrap:wrap}
.forecast-stats{grid-template-columns:1fr}
.entry{grid-template-columns:75px 1fr;gap:10px}
h1{font-size:1.4375rem}
footer span{font-size:0.6875rem}
}

.overview .card{padding:18px;margin-bottom:12px}
.overview h2{margin-bottom:12px}
.overview .columns{gap:14px}
.overview .hero{padding:24px;gap:24px}
.overview .hero-side{padding-left:24px}
.overview .hero-target{margin:4px 0 0;display:flex;flex-wrap:wrap;gap:2px 8px}
.hero-target>span{white-space:normal}
.hero-target strong{font-weight:500}
.hero-outside{display:flex;align-items:center;gap:8px;min-height:44px;padding:0;border:0;font-size:0.75rem;color:var(--muted)}
.hero-outside strong{font-size:0.9375rem;font-weight:500;color:var(--primary-text-color)}
.overview .comfort{margin-top:12px}
.heat-sources{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin-bottom:12px}
.heat-sources>.card{margin:0;min-width:0}
.card-heading{display:flex;align-items:center;justify-content:space-between;gap:10px;min-width:0}
.overview .card-heading h2{margin:0}
.boiler-status{font-size:0.75rem;white-space:nowrap;padding:3px 0 3px 9px;color:var(--muted);background:transparent;border:0}
.boiler-card[data-heating="true"]{border-color:color-mix(in srgb,var(--source-red) 32%,var(--stroke));background:var(--heat-fill)}
.boiler-card[data-heating="true"] .boiler-status{color:var(--source-red);background:transparent;border-color:transparent}
.boiler-metrics{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin-top:8px}
.compact-metric{min-width:0;text-align:left;padding:8px 0;border:0;border-radius:8px}
.compact-metric>span{display:block;font-size:0.6875rem;line-height:1.4}
.compact-metric>strong{display:block;font-size:1.1875rem;font-weight:500;line-height:1.35;margin-top:4px;overflow-wrap:anywhere}
.compact-metric small{font-size:0.6875rem;font-weight:400;color:var(--muted)}
.boiler-metrics .compact-metric strong{font-size:1.5rem}
.boiler-metrics .compact-metric small{display:block;font-size:0.6875rem}
.boiler-metrics .compact-metric{padding-top:2px;padding-bottom:2px}
.boiler-note{font-size:0.6875rem;line-height:1.5;margin:4px 0 0}

.fireplace-card{--fire-accent:var(--muted)}
.fireplace-card[data-phase="Розпалювання"]{--fire-accent:var(--source-yellow);border-color:color-mix(in srgb,var(--source-yellow) 25%,var(--stroke));background:var(--warm-fill)}
.fireplace-card[data-phase="Горить"]{--fire-accent:var(--source-red);border-color:color-mix(in srgb,var(--source-red) 32%,var(--stroke));background:var(--heat-fill)}
.fireplace-card[data-phase="Інтенсивно горить"]{--fire-accent:var(--source-red);border-color:color-mix(in srgb,var(--source-red) 42%,var(--stroke));background:color-mix(in srgb,#c9505c 18%,var(--surface))}
.fireplace-card[data-phase="Згасає"]{--fire-accent:var(--source-yellow);border-color:color-mix(in srgb,var(--source-yellow) 18%,var(--stroke));background:color-mix(in srgb,#c9a04b 7%,var(--surface))}
.fire-phase{display:flex;align-items:center;justify-content:flex-end;gap:5px;text-align:right;border:0;padding:0;max-width:58%;min-height:44px;color:var(--fire-accent)}
.fire-phase svg{width:18px;height:22px;flex-shrink:0;fill:none;stroke:currentColor;stroke-width:1.5}
.fire-phase strong{font-size:0.8125rem;font-weight:500;line-height:1.3;overflow-wrap:anywhere}
.fireplace-grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.1fr);gap:14px;align-items:start;margin:6px 0 0}
.fireplace-grid>div{min-width:0}
.fireplace-grid .eyebrow{font-size:0.6875rem;letter-spacing:.5px}
.fire-temperature{display:block;font-size:1.375rem;font-weight:500;line-height:1.3;margin-top:4px}
.fire-temperature small{display:block;font-size:0.6875rem;font-weight:400;margin-top:2px}
.circulation-block{border-left:1px solid var(--stroke);padding-left:14px}
.fan-actual{display:block;font-size:1.0625rem;font-weight:500;line-height:1.4;margin-top:4px}
.fan-reason{font-size:0.75rem;margin:8px 0;overflow-wrap:anywhere}
.fan-level{font-size:0.6875rem;margin:2px 0 0}
.fan-waiting{color:var(--source-yellow);font-size:0.75rem;margin:2px 0 0}
.fire-details{border-top:1px solid var(--stroke);margin-top:10px}
.fire-details summary,.plan-details summary{min-height:44px;display:flex;align-items:center;cursor:pointer;font-size:0.75rem;color:var(--muted)}
.fire-details summary::after,.plan-details summary::after{content:'+';margin-left:auto}
.fire-details[open] summary::after,.plan-details[open] summary::after{content:'−'}

.overview .section-meta{margin:6px 0 10px;gap:8px}
.overview .section-meta .pill{font-size:0.6875rem}
.overview .legend{margin:10px 0;gap:6px 14px}
.overview .forecast-stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin-top:10px}
.forecast-stats .compact-metric{background:transparent;padding:8px 10px 8px 0;border-radius:0}
.forecast-stats .compact-metric>span{font-size:0.75rem}
.forecast-stats .compact-metric>strong{font-size:1.125rem}
.forecast-warning{margin:8px 0 0;padding:8px 10px;border-radius:10px}
.forecast-help{position:relative;flex-shrink:0}
.forecast-help summary{width:44px;min-height:44px;display:grid;place-items:center;cursor:pointer;color:var(--muted);list-style:none;font-size:1.1875rem}
.forecast-help summary::-webkit-details-marker{display:none}
.forecast-help>div{position:absolute;top:44px;right:0;z-index:3;width:min(340px,calc(100cqi - 72px));padding:14px;background:var(--surface);border:1px solid var(--stroke);border-radius:12px;box-shadow:0 8px 30px #0003;font-size:0.75rem}
.forecast-help p{margin:8px 0 0}

.plan-reason{font-size:0.75rem;line-height:1.5;margin:0 0 8px}
.sessions-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,250px),1fr));gap:0 16px}
.overview .session{padding:8px 0}
.session strong{font-size:0.75rem}
.session .muted{font-size:0.6875rem}
.plan-morning{display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:4px 12px;font-size:0.75rem;padding:10px 0}
.plan-morning strong{font-weight:500}
.plan-morning small{font-size:0.6875rem}
.plan-details{border-top:1px solid var(--stroke)}
.plan-breakdown{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:4px 16px}
.plan-breakdown .compact-metric>strong{font-size:1rem}
.overview .empty{padding:8px 0;margin:0;font-size:0.75rem}

@container(max-width:900px){.heat-sources{grid-template-columns:1fr}
}

@container(max-width:700px){.overview .forecast-stats{grid-template-columns:repeat(2,minmax(0,1fr))}
.plan-breakdown{grid-template-columns:repeat(2,minmax(0,1fr))}
}

@container(max-width:600px){.overview .card{padding:14px 16px;margin-bottom:10px}
.heat-sources{gap:10px;margin-bottom:10px}
.heat-sources>.card{margin:0}
.overview h2{font-size:0.9375rem;margin-bottom:10px}
.overview .hero{padding:16px;gap:12px}
.overview .hero-side{padding-left:12px}
.overview .hero-side h2{margin:6px 0;font-size:0.875rem}
.overview .hero p{font-size:0.75rem;line-height:1.4}
.overview .hero-outside{font-size:0.6875rem;gap:6px}
.hero-outside strong{font-size:0.8125rem}
.overview .comfort{font-size:0.6875rem;margin-top:8px}
.overview .comfort strong{font-size:0.8125rem}
.overview .hero .eyebrow{font-size:0.75rem;letter-spacing:0}
.boiler-metrics{gap:8px;margin-top:4px}
.boiler-metrics .compact-metric strong{font-size:1.375rem}
.boiler-metrics .compact-metric>span{font-size:0.6875rem}
.boiler-note{font-size:0.6875rem;margin-top:2px}
.fire-phase strong{font-size:0.75rem}
.fireplace-grid .eyebrow{font-size:0.75rem;letter-spacing:0}
.fire-temperature{font-size:1.25rem}
.fire-temperature small{font-size:0.6875rem}
.fan-actual{font-size:0.9375rem}
.fire-details{margin-top:8px}
.fire-details summary,.plan-details summary{font-size:0.6875rem}
.forecast-stats .compact-metric{padding:8px 0}
.forecast-stats .compact-metric strong{font-size:0.9375rem}
.overview .section-meta{font-size:0.6875rem}
.overview .section-meta .pill{font-size:0.6875rem;padding:3px 8px}
}


.compact-page .page-title{margin:10px 0 14px}
.compact-page h1{font-size:1.5rem}
.compact-page h2{margin-bottom:10px;font-size:0.9375rem}
.compact-page .card{padding:16px;margin:0}
.settings-page .settings-grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;align-items:start}
.comfort-settings,.circulation-settings{grid-column:1/-1}
.comfort-groups{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px}
.comfort-groups fieldset{min-width:0;border:0;padding:0;margin:0}
.comfort-groups legend{font-size:0.6875rem;color:var(--muted);padding:0}
.control-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:4px 12px;align-items:end}
.control-grid.thirds{grid-template-columns:repeat(3,minmax(0,1fr))}
.compact-page .field{min-width:0;display:flex;flex-direction:column;align-items:stretch;justify-content:flex-start;gap:5px;padding:7px 0;border:0;font-size:0.75rem}
.compact-page .field>span{font-size:0.75rem;line-height:1.4}
.compact-page .field .number{width:100%;min-width:0;gap:5px}
.compact-page .number input{flex:1;min-width:0}
.compact-page .field input:not([type="checkbox"]),.compact-page .field select{width:100%;min-width:0;max-width:none;min-height:44px;font-size:0.875rem;padding:8px 10px}
.compact-page .field:has(.toggle){flex-direction:row;align-items:center;justify-content:space-between;min-height:58px;gap:8px}
.compact-page .field:has(.toggle)>span{font-size:0.75rem}
.compact-page .toggle{flex-shrink:0}
.settings-status{border-top:1px solid var(--stroke);padding-top:6px;margin-top:6px}
.settings-status .row{padding:6px 0;font-size:0.6875rem}
.circulation-config{display:grid;grid-template-columns:minmax(240px,1fr) minmax(0,1.4fr);gap:20px;align-items:end}
.inline-help{margin-top:6px;border-top:1px solid var(--stroke)}
.inline-help summary,.model-advanced summary{min-height:44px;display:flex;align-items:center;justify-content:space-between;cursor:pointer;color:var(--muted);font-size:0.75rem;gap:8px}
.inline-help summary::after,.model-advanced summary::after{content:'+';margin-left:auto}
.inline-help[open] summary::after,.model-advanced details[open] summary::after{content:'−'}
.inline-help p{font-size:0.6875rem;margin:6px 0;line-height:1.6}

.model-stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-bottom:12px!important}
.model-stats .compact-metric{padding:4px 0}
.model-stats .compact-metric strong{font-size:1.3125rem}
.model-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;align-items:start}
.model-grid>.card{min-width:0}
.model-grid .cycle-history{grid-column:1/-1}.cycle-history>summary,.cycle-reviews>summary{display:flex;align-items:center;min-height:44px;cursor:pointer;font-weight:500}.cycle-history-entry{padding:12px 0;border-top:1px solid var(--stroke)}.cycle-history-entry h3{font-size:1rem;margin:4px 0 8px}
.model-grid .controller-quality{grid-column:1/-1}
.model-grid .ai-report{grid-column:1/-1}
.report-text,.learning-result{font-size:0.8125rem;line-height:1.6;margin:0 0 6px;overflow-wrap:anywhere}
.report-meta,.model-meta{font-size:0.6875rem;margin:4px 0 8px}
.report-stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}
.fact-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:4px 16px}
.model-page .compact-metric>span{font-size:0.6875rem}
.model-page .compact-metric>strong{font-size:1rem;margin-top:3px}
.model-page .compact-metric small{font-size:0.6875rem}
.model-page .model-stats strong{font-size:1.25rem}
.model-page .compact-metric{padding:6px 0}
.model-page td,.model-page th{padding:7px 6px;font-size:0.75rem}
.model-page th{font-size:0.6875rem}
.model-page .journal{max-height:320px}
.model-page .entry{grid-template-columns:80px minmax(0,1fr);gap:10px;padding:8px 0}
.model-page .entry time{font-size:0.6875rem}
.model-page .entry p{font-size:0.75rem}
.action-buttons{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
.action-buttons .field>span{display:none}
.action-buttons .field{padding:4px 0}
.action-buttons .field .action{font-size:0.75rem;width:100%;padding:8px;min-height:44px}
.model-advanced summary{color:var(--primary-text-color);font-size:0.8125rem;font-weight:500}
.parameter-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 16px}
.parameter-grid .row{min-width:0;padding:7px 0;font-size:0.6875rem}
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
.model-grid .cycle-history{grid-column:1/-1}.cycle-history>summary,.cycle-reviews>summary{display:flex;align-items:center;min-height:44px;cursor:pointer;font-weight:500}.cycle-history-entry{padding:12px 0;border-top:1px solid var(--stroke)}.cycle-history-entry h3{font-size:1rem;margin:4px 0 8px}
.model-grid .controller-quality{grid-column:1/-1}
.model-grid .ai-report{grid-column:auto}
}

@container(max-width:600px){.compact-page .page-title{margin:4px 0 12px}
.compact-page h1{font-size:1.375rem}
.compact-page .card{padding:14px 16px}
.settings-page .settings-grid,.model-grid{gap:10px}
.control-grid{gap:3px 10px}
.control-grid.thirds{gap:3px 8px}
.compact-page .field{padding:6px 0}
.compact-page .field>span{font-size:0.75rem}
.compact-page .field input:not([type="checkbox"]),.compact-page .field select{font-size:0.75rem}
.compact-page .field:has(.toggle)>span{font-size:0.6875rem}
.circulation-levels .field>span{min-height:28px}
.fact-grid{gap:2px 12px}
.report-stats{gap:8px}
.report-stats .compact-metric strong{font-size:0.875rem}
.model-page .model-stats strong{font-size:1.125rem}
.model-page .compact-metric>strong{font-size:0.9375rem}
.action-buttons{gap:8px}
.model-page .entry{grid-template-columns:72px minmax(0,1fr)}
.inline-help summary{font-size:0.6875rem}
}


.system-settings{margin-top:14px!important}
.system-settings>summary,.system-group>summary{min-height:48px;display:flex;align-items:center;justify-content:space-between;cursor:pointer;font-weight:600;gap:10px}
.system-settings[open]>summary>span{transform:rotate(45deg)}
.system-body{border-top:1px solid var(--stroke);padding-top:6px}
.system-group{border-bottom:1px solid var(--stroke)}
.system-group>summary{font-size:0.8125rem;font-weight:500}
.system-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px 18px;padding:6px 0 16px}
.system-field{min-width:0;max-width:100%;overflow-wrap:anywhere;display:flex;flex-direction:column;gap:5px}
.system-label{min-width:0;max-width:100%;overflow-wrap:anywhere;display:flex;align-items:center;justify-content:space-between;gap:6px;font-size:0.75rem;line-height:1.4}
.system-help{flex-shrink:0;border:0;color:var(--green);min-width:44px;min-height:44px;padding:8px;font-size:0.9375rem}
.system-field input,.system-field select{width:100%;max-width:100%;min-width:0;min-height:44px;border:1px solid var(--control-stroke);border-radius:12px;background:var(--raised);color:inherit;padding:8px 10px;font:inherit}
.system-label>span,.system-label>label{min-width:0;overflow-wrap:anywhere}
.equipment-select{position:relative;display:block;width:100%;max-width:100%;min-width:0;overflow:hidden}
.field .equipment-select select,.system-field .equipment-select select{display:block;appearance:none;-webkit-appearance:none;padding-right:34px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.equipment-select svg{position:absolute;right:10px;top:50%;transform:translateY(-50%);width:16px;height:16px;fill:none;stroke:currentColor;stroke-width:2;pointer-events:none}
.equipment-select:focus-within{outline:2px solid var(--green);outline-offset:2px;border-radius:12px}
.system-settings summary{min-width:0;overflow-wrap:anywhere}
.system-field .number{width:100%;min-width:0}
.system-field .number input{flex:1;width:0}
.system-field .number span{white-space:nowrap}
.system-entity{text-align:left;padding:8px 10px;font-size:0.8125rem;overflow-wrap:anywhere}
.system-entity small{color:var(--muted)}
.system-fixed{display:flex;align-items:center;min-height:44px;color:var(--muted);font-size:0.8125rem}
.system-actions{display:flex;flex-wrap:wrap;align-items:center;justify-content:flex-end;gap:8px;padding-top:12px}
.system-message{flex:1 1 180px;font-size:0.75rem;color:var(--muted)}
.system-dialog{position:fixed;width:min(92vw,560px);max-height:85vh;overflow:auto;border:1px solid var(--stroke);border-radius:var(--card-radius);padding:20px;background:var(--surface);color:var(--primary-text-color,#e8e8e8);box-shadow:0 12px 48px #0005;font:inherit}
.system-dialog::backdrop{background:#0008}
.system-dialog-heading{display:flex;gap:12px;align-items:center;justify-content:space-between}
.system-dialog h2{margin:0;font-size:1.0625rem}
.system-dialog p{font-size:0.8125rem;line-height:1.65;overflow-wrap:anywhere}
.system-dialog button{min-width:44px;min-height:44px;font-size:1.375rem}
.system-dialog-heading button{flex-shrink:0}
@container(max-width:900px){.system-grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:10px 12px}
}
@container(max-width:430px){.system-grid{grid-template-columns:minmax(0,1fr)}
.system-settings>summary{font-size:0.875rem}
.system-label{font-size:0.75rem}
.system-actions button{flex:1 1 120px}
.system-dialog{padding:16px}
}



/* One spacing rhythm and a common touch/focus vocabulary on all three tabs. */
.icon{width:20px;height:20px;display:block;fill:none;stroke:currentColor;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round;flex:none}

.brand-icon .icon{width:30px;height:30px}

.menu-button,.history-refresh{display:grid;place-items:center}

.hero .eyebrow{letter-spacing:0;text-transform:none;font-weight:400}
.fireplace-grid .eyebrow{text-transform:none;letter-spacing:0}

.hero h1.eyebrow{font-size:0.875rem}

.hero-side .comfort{font-size:0.75rem}

.boiler-status::before{content:'';display:inline-block;width:6px;height:6px;border:1px solid currentColor;border-radius:50%;margin-right:6px;vertical-align:middle}

.boiler-card[data-heating="true"] .boiler-status::before{background:currentColor}

.boiler-metrics .compact-metric+.compact-metric{border-left:1px solid var(--stroke);padding-left:12px;border-radius:0}

.boiler-note{font-size:0.75rem}.power-advice{margin:2px 0}.power-advice>summary{position:relative;min-height:44px;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;align-items:center;padding-right:18px;font-size:0.625rem;color:var(--muted);cursor:pointer}.power-advice>summary strong{display:block;font-size:0.8125rem;font-weight:500;color:var(--ink)}.power-advice>summary small{font-size:0.625rem;color:var(--muted)}.power-advice>summary .warn strong{color:inherit}.power-advice>summary::after{content:"＋";position:absolute;right:0;top:50%;transform:translateY(-50%)}
.equipment-save .equipment-status{flex:1 1 110px;min-width:0;font-size:0.75rem;color:var(--muted)}.equipment-save button{font-size:0.75rem}
.overview .hero-side p{margin:4px 0}.overview .hero-side .comfort{margin-top:6px}
.hero-power{font-size:0.6875rem;line-height:1.35;margin:4px 0;color:var(--muted)}.hero-power strong{display:block;font-size:0.75rem;font-weight:500;color:var(--green)}

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
.brand small{font-size:0.6875rem}
.header-status{font-size:0.6875rem;gap:6px}

  nav{margin-bottom:12px;padding-bottom:6px;gap:2px}

  nav button{flex:1;min-height:44px;padding:9px 6px;font-size:0.8125rem}

  .overview .hero{gap:12px}
.overview .hero .eyebrow{font-size:0.6875rem}

  .overview .hero h1.eyebrow{font-size:0.75rem}

  .hero-side h2{overflow-wrap:anywhere}
.overview .comfort{font-size:0.6875rem}

  .hero-side .comfort strong{font-size:0.875rem}

  .boiler-metrics .compact-metric+.compact-metric{padding-left:10px}

  .boiler-note{font-size:0.6875rem}

  .compact-metric>span,.history-legend button>span{font-size:0.75rem}

  .overview .section-meta{font-size:0.6875rem;align-items:flex-start}

  .section-meta>.muted{text-align:right}

  .forecast-stats .compact-metric strong{font-size:1.0625rem}

  .forecast-stats .compact-metric>span{font-size:0.75rem}

  .forecast-warning{font-size:0.75rem}

  .history-legend{gap:6px}
.history-legend button{padding:7px 6px}

  .history-legend strong{font-size:1.125rem}

  .system-help{min-width:44px;min-height:44px;margin:0}

  .system-field{gap:4px}
  .compact-page .field input:not([type="checkbox"]),.compact-page .field select,.system-field input,.system-field select{font-size:1rem}

}

.plan-energy-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-top:8px}
.plan-energy-grid .compact-metric{min-width:0;padding:4px 0}.plan-energy-grid strong{font-variant-numeric:tabular-nums;font-size:1.375rem}
.modulation-details>summary,.energy-notes>summary,.power-diagnostics>summary,.execution-details>summary{min-height:44px;display:flex;align-items:center;cursor:pointer;font-weight:600}.modulation-details>.card,.power-diagnostics>.card,.execution-details>.card{padding:8px 0 0;border:0;box-shadow:none;margin:0}
.comparison-tools{display:flex;gap:12px;align-items:end;justify-content:space-between;flex-wrap:wrap}
.comparison-tools label{display:grid;gap:4px;font-size:0.75rem;color:var(--muted)}
.comparison-tools select{min-height:44px;max-width:100%;background:var(--surface);color:inherit;border:1px solid var(--stroke);border-radius:8px;padding:8px;font:inherit}
.comparison-plot{width:100%;min-width:0;margin-top:12px}.comparison-plot svg{display:block;overflow:hidden}
.comparison-plot text{fill:var(--muted);font-size:0.6875rem}.comparison-grid{stroke:var(--stroke);stroke-width:1}
.comparison-legend{display:flex;flex-wrap:wrap;gap:8px 20px;font-size:0.75rem;color:var(--muted)}
.comparison-legend span:first-child{color:var(--history-return)}.comparison-legend span:nth-child(2){color:var(--history-room)}
.comparison-dates{margin-top:8px}.comparison-dates summary,.comparison-sources summary{min-height:44px;display:flex;align-items:center;cursor:pointer}
.comparison-dates>div{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:10px;padding:4px 0 10px}
.comparison-dates label{min-width:0;display:grid;gap:5px;font-size:0.75rem;color:var(--muted)}
.comparison-dates input{box-sizing:border-box;min-width:0;width:100%;min-height:44px;background:var(--surface);color:var(--primary-text-color);border:1px solid var(--stroke);border-radius:8px;padding:8px;font:inherit}
.comparison-dates button{grid-column:1/-1;min-height:44px}.comparison-period{font-size:0.75rem;margin:4px 0}
.comparison-plot{touch-action:pan-y}.comparison-plot:focus-visible{outline:2px solid var(--primary-color);border-radius:8px}
.comparison-readout{font-size:0.75rem;min-height:36px;margin:8px 0}.comparison-status{font-size:0.8125rem}
.comparison-coverage{font-size:0.75rem;margin:6px 0;color:var(--muted)}
.comparison-sources{border-top:1px solid var(--stroke);margin-top:10px;font-size:0.75rem}
.comparison-source{display:grid;gap:3px;margin:0;padding:9px 0;color:var(--muted)}.comparison-source strong{color:var(--primary-text-color);font-weight:500}
@container(max-width:600px){.plan-energy-grid{gap:8px}.plan-energy-grid .compact-metric>strong{font-size:1.1875rem}.plan-energy-grid .compact-metric>span{font-size:0.6875rem}}

.overview-details,.additional-settings,.model-tools{margin-top:12px;padding:0!important;border:0;background:transparent;box-shadow:none}
.overview-details>summary,.additional-settings>summary,.model-tools>summary{padding:8px 12px;border-bottom:1px solid var(--stroke)}
.overview-details>summary,.additional-settings>summary,.model-tools>summary{display:flex;align-items:center;min-height:44px;cursor:pointer;font-weight:500;gap:10px;list-style:none}
.overview-details>summary::after,.additional-settings>summary::after,.model-tools>summary::after{content:'+';margin-left:auto}
.overview-details[open]>summary::after,.additional-settings[open]>summary::after,.model-tools[open]>summary::after{content:'−'}
.overview-details>.heat-sources,.additional-settings>.settings-grid,.model-tools>.model-grid{margin-top:12px}
.model-tools{grid-column:1/-1}.model-grid>.plan-energy{grid-column:1/-1}
.energy-pair{grid-template-columns:repeat(2,minmax(0,1fr))}
.comparison-zoom{display:flex;flex-wrap:wrap;gap:8px;margin:4px 0 8px}.comparison-zoom button{min-width:44px;min-height:44px}
.overview .hero-side .comfort{margin-top:6px}.overview .hero-side p{margin-bottom:6px}
.field select,.system-field select{appearance:none;-webkit-appearance:none;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:100%;contain:inline-size}
h1,h2{min-width:0;overflow-wrap:anywhere}button,input,select,textarea{font-family:inherit}button,summary,input,select{min-height:44px}
.model-grid>.card,.model-grid>details,.settings-grid>.card{overflow-wrap:anywhere}
::selection{background:var(--green);color:var(--surface)}input,textarea{caret-color:var(--green)}
:host{scrollbar-color:var(--muted) var(--surface)}
@container(max-width:24rem){.model-stats{grid-template-columns:repeat(2,minmax(0,1fr))}.model-grid,.comfort-groups{grid-template-columns:1fr}.hero-side h2{overflow-wrap:anywhere}.comparison-dates>div{grid-template-columns:1fr}}

@container(max-width:18rem){header{flex-wrap:wrap}.brand{flex-shrink:0}.header-status{white-space:normal}.overview .forecast-stats,.plan-energy-grid{grid-template-columns:1fr}.hero{grid-template-columns:1fr}.hero-side{border-left:0;padding-left:0;border-top:1px solid var(--stroke);padding-top:12px}.control-grid,.control-grid.thirds{grid-template-columns:1fr}}
@container(max-width:350px){
  .temperature{font-size:2.375rem}
  .temperature span{font-size:1.0625rem}
}
@container(max-width:600px){
  .overview .fireplace-card{padding:10px 12px}
  .fireplace-card .fire-details{margin-top:0}
  .fireplace-card .fire-details summary{min-height:44px}
}
`;
if(!customElements.get('heating-ai-panel'))customElements.define('heating-ai-panel',HeatingAIPanel);
window.customCards=window.customCards||[];
if(!window.customCards.some(c=>c.type==='heating-ai-panel'))window.customCards.push({type:'heating-ai-panel',name:'Heating AI',description:'Клімат: огляд, комфорт, прогноз і навчання',preview:false});


