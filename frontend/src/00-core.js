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



