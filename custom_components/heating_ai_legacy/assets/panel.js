/* Heating AI V1+ preview. Authenticated HA panel, strictly read-only. */
const NS = 'http://www.w3.org/2000/svg';
const HOUR = 3600;
const fmt = new Intl.DateTimeFormat('uk-UA', { timeZone: 'Europe/Kyiv', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
const num = (v, lo = -30, hi = 70) => {
  if (v === null || v === undefined || v === '' || typeof v === 'boolean') return null;
  const x = Number(v);
  return Number.isFinite(x) && x >= lo && x <= hi ? x : null;
};
const degrees = x => x === null ? '—' : x.toFixed(1).replace('.', ',') + ' °C';
const time = t => fmt.format(new Date(t * 1000));
const svg = (tag, attrs) => {
  const el = document.createElementNS(NS, tag);
  for (const [key, value] of Object.entries(attrs || {})) el.setAttribute(key, String(value));
  return el;
};
const add = (root, tag, attrs, text) => {
  const el = svg(tag, attrs);
  if (text !== undefined) el.textContent = text;
  root.appendChild(el);
  return el;
};
const readPts = (items, lo, hi, historical) => {
  if (!Array.isArray(items)) return [];
  let previous = -Infinity;
  const result = [];
  for (const item of items.slice(0, 2000)) {
    if (!item || typeof item !== 'object') continue;
    const at = num(item.ts ?? item.at, lo, hi);
    const temperature = num(item.temperature ?? item.value);
    if (at === null || temperature === null || at <= previous) continue;
    previous = at;
    const point = { at, temperature };
    if (!historical) for (const key of ['target', 'lower', 'upper']) point[key] = num(item[key], 10, 35);
    result.push(point);
    if (result.length >= 1200) break;
  }
  return result;
};
const readOutages = (entity, now, end) => {
  if (!entity || !entity.attributes) return [];
  const attrs = entity.attributes;
  const updated = Date.parse(entity.last_reported || entity.last_updated || '') / 1000;
  if (attrs.valid !== true || attrs.source_fresh !== true ||
      !Number.isFinite(updated) || now - updated > 1800 || now - updated < -60) return [];
  const events = attrs.outages;
  if (!Array.isArray(events) || events.length > 100) return [];
  const parseTime = value => {
    if (typeof value === 'string' && value.includes('T')) {
      const t = Date.parse(value) / 1000;
      return Number.isFinite(t) ? t : null;
    }
    return num(value, now - HOUR, end);
  };
  return events.slice(0, 48).flatMap(e => {
    const a = parseTime(e?.start), b = parseTime(e?.end);
    return a !== null && b !== null && b > Math.max(a, now) &&
      a < end ? [{ a:Math.max(a, now), b:Math.min(b, end) }] : [];
  });
};
const freshPower = (entity, now) => {
  if (!entity) return null;
  const stamp = Date.parse(entity.last_reported || entity.last_updated || '') / 1000;
  return Number.isFinite(stamp) && stamp <= now + 30 && now - stamp < 180
    ? num(entity.state, 0, 15000) : null;
};
const segments = (items, now, end) => {
  if (!Array.isArray(items)) return [];
  return items.slice(0, 50).flatMap(i => {
    const a = num(i && i.start, now - 3600, end);
    const b = num(i && i.end, now - 3600, end);
    const kw = num(i && i.power_kw, 0, 12);
    return a !== null && b !== null && kw !== null && b > Math.max(now, a)
      ? [{ a: Math.max(now, a), b, kw }] : [];
  });
};
const plot = (root, points, key, x, y, style, maxGap) => {
  let path = '', last = null;
  for (const p of points) {
    const value = p[key];
    if (value === null || value === undefined) { last = null; continue; }
    path += (last === null || p.at - last > maxGap ? ' M' : ' L') +
      x(p.at).toFixed(1) + ' ' + y(value).toFixed(1);
    last = p.at;
  }
  if (path) add(root, 'path', { d: path.trim(), class: style, fill: 'none' });
};
const stylesheet = [
  ':host{display:block;min-height:100vh;background:#101b2c;color:#eaf1fa;font:14px system-ui,-apple-system,sans-serif}',
  '.wrap{max-width:1350px;margin:auto;padding:22px 16px 48px}',
  '.top{display:flex;justify-content:space-between;gap:16px;align-items:center;margin-bottom:18px;flex-wrap:wrap}',
  '.brand{font-size:21px;font-weight:750;letter-spacing:-.5px}.minor{font-size:12px;color:#99acc4;margin-top:4px}',
  '.tag{border:1px solid #d0a15c;color:#ebc58b;border-radius:99px;padding:6px 11px;font-weight:700;font-size:11px}',
  '.stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin-bottom:14px}',
  '.stat,.chartbox,.notice{background:#19283b;border:1px solid #34465c;border-radius:14px}',
  '.stat{padding:15px}.stat label{display:block;color:#a8bad0;font-size:12px}.stat strong{display:block;font-size:23px;font-weight:700;margin-top:5px;letter-spacing:-.5px}',
  '.chartbox{padding:16px 18px 12px}.heading{font-size:16px;font-weight:700}',
  '.key{display:flex;gap:18px;flex-wrap:wrap;color:#b7c6d9;margin:13px 0 4px;font-size:12px}',
  '.key span:before{content:"";display:inline-block;width:19px;margin:0 6px 3px 0;vertical-align:middle;border-top:3px solid var(--line)}',
  '.scroll{overflow-x:auto;scrollbar-width:thin}.chart{display:block;width:100%;min-width:750px;aspect-ratio:1080/390}',
  '.chart text{fill:#9aadc6;font-size:13px}.gridline{stroke:#34465c;stroke-width:1;stroke-dasharray:3 5}',
  '.actual{stroke:#63d3b4;stroke-width:3}.forecast{stroke:#77a8ff;stroke-width:3;stroke-dasharray:8 5}',
  '.native{stroke:#f4a0bf;stroke-width:2.6;stroke-dasharray:10 4}',
  '.target{stroke:#ffbf6b;stroke-width:2;stroke-dasharray:3 6}.lower{stroke:#b18fff;stroke-width:1.5;stroke-dasharray:4 4}',
  '.heat{fill:#3fb48a;fill-opacity:.15}.outage{fill:#c27e82;fill-opacity:.18}.now{stroke:#dbe3f0;stroke-width:1.5;stroke-dasharray:4 5}',
  '.note,.notice{font-size:12px;color:#aabbd0;line-height:1.6}.note{margin:9px 0 2px}.notice{padding:12px 15px;margin-top:13px}',
  '.error{color:#f2b390}.right{display:flex;flex-direction:column;gap:5px;align-items:flex-end}',
  '@media(max-width:650px){.wrap{padding:12px 9px 36px}.stats{grid-template-columns:repeat(2,minmax(0,1fr));gap:7px}.stat{padding:12px}.stat strong{font-size:20px}.chartbox{padding:13px 9px}.key{gap:10px}.brand{font-size:18px}}'
].join('');
class HeatingV1NativePanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._history = [];
    this._historyEntity = null;
    this._historyAt = 0;
    this._historyPending = false;
    this._lastHass = null;
    this._native = null;
    this._nativeAt = 0;
    this._nativePending = false;
    this._raf = 0;
    this.shadowRoot.innerHTML =
      '<style>' + stylesheet + '</style>' +
      '<main class="wrap"><header class="top"><div><div class="brand">Heating AI V1+</div>' +
      '<div class="minor">Температура, 24-годинний план V1 і фактичні виміри</div></div>' +
      '<div class="right"><span class="tag">SHADOW · БЕЗ КОМАНД</span><small class="minor" id="mode"></small></div></header>' +
      '<section class="stats"><div class="stat"><label>Кімната</label><strong id="room">—</strong></div>' +
      '<div class="stat"><label>Ціль</label><strong id="target">—</strong></div>' +
      '<div class="stat"><label>Котел</label><strong id="boiler">—</strong></div>' +
      '<div class="stat"><label>План V1</label><strong id="plan">—</strong></div>' +
      '<div class="stat"><label>KWS · фактично</label><strong id="kws">—</strong></div>' +
      '<div class="stat"><label>Камін</label><strong id="fire">—</strong></div>' +
      '<div class="stat"><label>Циркуляція</label><strong id="fan">—</strong></div>' +
      '<div class="stat"><label>Мережа</label><strong id="grid">—</strong></div></section>' +
      '<section class="chartbox"><div class="heading">Температура · план і факт</div>' +
      '<div class="key"><span style="--line:#63d3b4">Факт HA</span>' +
      '<span style="--line:#77a8ff">Прогноз V1</span><span style="--line:#f4a0bf">V1+ умовний</span>' +
      '<span style="--line:#ffbf6b">Ціль</span>' +
      '<span style="--line:#b18fff">Нижня межа</span>' +
      '<span style="--line:#3fb48a">План нагріву</span>' +
      '<span style="--line:#c27e82">Відключення 3.1</span></div>' +
      '<div class="scroll"><svg class="chart" viewBox="0 0 1080 390" role="img" ' +
      'aria-label="Фактична температура, розрахована температура, ціль і вікна нагріву">' +
      '</svg></div><p class="note" id="source"></p>' +
      '<p class="note" id="native-status"></p></section>' +
      '<section class="notice">Це паралельний перегляд старого прогнозу V1. Він не є ' +
      'незалежним планом V1+ і не керує котлом, вентилятором чи рекуператором. ' +
      'Вручну обраний AUTO не перезапускається з цієї панелі.</section></main>';
  }
  set hass(value) {
    this._lastHass = value;
    if (!this.isConnected) return;
    if (!this._raf) this._raf = requestAnimationFrame(() => { this._raf = 0; this.render(); });
  }
  connectedCallback() {
    if (this._lastHass) this.render();
  }
  disconnectedCallback() { if (this._raf) cancelAnimationFrame(this._raf); this._raf = 0; }
  set panel(v) { this._panel = v; }
  async recorder(hass, roomId, now) {
    if (this._historyPending || !hass || typeof hass.callApi !== 'function') return;
    if (this._historyEntity === roomId && now - this._historyAt < 300) return;
    this._historyEntity = roomId; this._historyAt = now; this._historyPending = true;
    const start = new Date((now - 6 * HOUR) * 1000).toISOString();
    const end = new Date(now * 1000).toISOString();
    const url = 'history/period/' + encodeURIComponent(start) +
      '?filter_entity_id=' + encodeURIComponent(roomId) +
      '&end_time=' + encodeURIComponent(end) + '&minimal_response&no_attributes';
    try {
      const series = await hass.callApi('GET', url);
      const values = Array.isArray(series) && Array.isArray(series[0]) ? series[0] : [];
      const parsed = [];
      for (const item of values.slice(-2000)) {
        const rawTime = item.lu ?? item.last_updated ?? item.last_changed;
        const at = typeof rawTime === 'number' ? rawTime
          : Date.parse(rawTime || '') / 1000;
        const value = num(item.s ?? item.state, -20, 65);
        if (Number.isFinite(at) && value !== null && at >= now - 6 * HOUR && at <= now + 60)
          parsed.push({ at, temperature: value });
      }
      if (this._historyEntity === roomId) {
        this._history = parsed.sort((a, b) => a.at - b.at);
        if (this.isConnected && this._lastHass) this.render();
      }
    } catch (_) {
      this._history = [];
    } finally { this._historyPending = false; }
  }
  async nativeSnapshot(ha, now) {
    const entryId = this._panel && this._panel.config && this._panel.config.entry_id;
    if (this._nativePending || !entryId || typeof ha.callWS !== 'function' ||
        now - this._nativeAt < 120) return;
    this._nativeAt = now;
    this._nativePending = true;
    try {
      const result = await ha.callWS({
        type: 'heating_ai_legacy/native_snapshot',
        entry_id: entryId,
        version: [1, 0]
      });
      if (this.isConnected && result && result.physical_commands_enabled === false) {
        this._native = result;
        this.render();
      }
    } catch (_) {
      this._native = null;
    } finally { this._nativePending = false; }
  }
  render() {
    const ha = this._lastHass;
    if (!ha || !ha.states) return;
    const now = Date.now() / 1000;
    const since = now - 6 * HOUR, until = now + 24 * HOUR;
    const ui = ha.states['sensor.heating_ui'];
    const ids = (ui && ui.attributes && ui.attributes.entities)
      || (this._native && this._native.entity_map) || {};
    const roomId = ids.room;
    const state = id => id && ha.states[id];
    const roomState = state(roomId);
    const current = num(roomState && roomState.state, -20, 65);
    const rawPlan = state('sensor.heating_forecast');
    const attrs = rawPlan && rawPlan.attributes || {};
    const created = Date.parse(attrs.created_at || '') / 1000;
    const fresh = Number.isFinite(created) && created <= now + 60 && now - created < 2 * HOUR;
    const prediction = fresh ? readPts(attrs.curve, since, until, false) : [];
    const nativePlan = this._native;
    const nativeValid = nativePlan &&
      nativePlan.status === 'VIRTUAL_CONDITIONAL' &&
      num(nativePlan.computed_at, now - 3600, now + 60) !== null;
    const nativeCurve = nativeValid
      ? readPts(nativePlan.forecast, since, until, false) : [];
    const fallback = fresh ? readPts(attrs.actual_curve, since, now + 60, true) : [];
    const history = this._history.length ? this._history : fallback;
    const windows = fresh && prediction.length > 1 ? segments(attrs.heat_segments, now, until) : [];
    const outageSchedule = state('sensor.heating_outage_schedule');
    const outages = readOutages(outageSchedule, now, until);
    if (!outages.length && this._native?.outage_preview?.outages) {
      const nativeOutages = this._native.outage_preview;
      if (nativeOutages.valid === true &&
          nativeOutages.coverage_proven === false) {
        for (const interval of nativeOutages.outages.slice(0, 48)) {
          const a = num(interval?.start, now - HOUR, until);
          const b = num(interval?.end, now - HOUR, until);
          if (a !== null && b !== null && a < b) outages.push({
            a: Math.max(now, a), b:Math.min(until, b)
          });
        }
      }
    }
    const kwPhases = ['a','b','c'].map(phase =>
      freshPower(state('sensor.energy_meter_kotel_potuzhnist_' + phase), now)
    );
    const controller = state('sensor.heating_controller');
    const mode = state(ids.mode);
    const thermostat = state('climate.kotel');
    const kv = this.shadowRoot.getElementById.bind(this.shadowRoot);
    kv('room').textContent = degrees(current);
    kv('target').textContent = degrees(num(prediction[0] && prediction[0].target, 10, 35) ??
      num(state(ids.target) && state(ids.target).state, 10, 35));
    kv('boiler').textContent = thermostat ? String(thermostat.state).toUpperCase() : 'Невідомо';
    kv('plan').textContent = fresh && prediction.length > 1
      ? String(windows.length) + ' вікон' : 'Недоступний';
    kv('kws').textContent = kwPhases.every(v => v !== null)
      ? (kwPhases.reduce((a,b) => a+b, 0) / 1000).toFixed(1).replace('.', ',') + ' кВт'
      : 'Немає даних';
    const fire = state('sensor.heating_fireplace');
    kv('fire').textContent = fire && fire.state !== 'unavailable' &&
      fire.state !== 'unknown' ? fire.state : 'Невідомо';
    const fan = state(ids.fan);
    kv('fan').textContent = fan && ['on','off'].includes(fan.state)
      ? fan.state === 'on' ? 'Працює' : 'Вимкнена' : 'Невідомо';
    const grid = state('input_select.indikator_merezhi_stan');
    kv('grid').textContent = grid && grid.state === 'Наявна мережа'
      ? 'Є' : grid && grid.state === 'Відсутня мережа' ? 'Немає' : 'Невідомо';
    kv('mode').textContent = 'Режим V1: ' + (mode ? mode.state : 'невідомо') +
      ' · ' + (controller ? controller.state : 'статус невідомий');
    kv('source').textContent = prediction.length > 1
      ? 'Джерело: чинний V1 · ' + time(created) + ' · план ' +
        (attrs.execution_state || 'невідомий') + ' · ' +
        (num(attrs.planned_kwh, 0, 200) === null ? 'енергія невідома'
          : num(attrs.planned_kwh, 0, 200).toFixed(1).replace('.', ',') + ' кВт·год') +
        '. Планові вікна не підтверджують виконання.'
      : 'Немає актуального розрахунку V1; прогноз не домальовується.';
    const canvas = this.shadowRoot.querySelector('.chart');
    while (canvas.firstChild) canvas.removeChild(canvas.firstChild);
    const all = history.concat(prediction, nativeCurve);
    if (current !== null) all.push({ at: now, temperature: current });
    const temps = all.map(p => p.temperature).filter(p => p !== null);
    for (const p of prediction) for (const k of ['target', 'lower']) if (p[k] !== null) temps.push(p[k]);
    const ymin = temps.length ? Math.floor((Math.min(...temps) - .4) * 2) / 2 : 19;
    const ymax = temps.length ? Math.max(ymin + 1, Math.ceil((Math.max(...temps) + .4) * 2) / 2) : 23;
    const X0 = 63, X1 = 1057, Y0 = 31, Y1 = 314;
    const x = t => X0 + (t - since) / (until - since) * (X1 - X0);
    const y = v => Y1 - (v - ymin) / (ymax - ymin) * (Y1 - Y0);
    for (let i=0; i<=4; i++) {
      const t = ymin + (ymax - ymin) * i / 4;
      add(canvas, 'line', { x1:X0, x2:X1, y1:y(t), y2:y(t), class:'gridline' });
      add(canvas, 'text', { x:X0 - 7, y:y(t)+4, 'text-anchor':'end' }, t.toFixed(1));
    }
    for (const o of outages) add(canvas, 'rect', {
      x:x(o.a), y:Y0, width:Math.max(0, x(o.b)-x(o.a)),
      height:Y1-Y0, class:'outage'
    });
    for (const w of windows) add(canvas, 'rect', {
      x:x(w.a), y:Y0, width:Math.max(0, x(w.b)-x(w.a)),
      height:Y1-Y0, class:'heat'
    });
    add(canvas, 'line', { x1:x(now), x2:x(now), y1:Y0, y2:Y1+7, class:'now' });
    add(canvas, 'text', { x:x(now)+7, y:Y0 - 7 }, 'Зараз');
    plot(canvas, history, 'temperature', x, y, 'actual', 90*60);
    plot(canvas, prediction, 'temperature', x, y, 'forecast', 90*60);
    plot(canvas, nativeCurve, 'temperature', x, y, 'native', 90*60);
    plot(canvas, prediction, 'target', x, y, 'target', 90*60);
    plot(canvas, prediction, 'lower', x, y, 'lower', 90*60);
    if (current !== null && roomState && roomState.last_updated) {
      add(canvas, 'circle', { cx:x(now), cy:y(current), r:4.5, fill:'#63d3b4' });
    }
    for (let i=0;i<=5;i++) {
      const stamp = since + i * 6 * HOUR;
      add(canvas, 'text', {x:x(stamp), y:Y1+29, 'text-anchor':i===0?'start':i===5?'end':'middle'},time(stamp));
    }
    add(canvas, 'text',{x:X0,y:Y1+55},'−6 год · факт');
    add(canvas, 'text',{x:X1,y:Y1+55,'text-anchor':'end'},'+24 год · план');
    const nativeStatus = this.shadowRoot.getElementById('native-status');
    nativeStatus.textContent = nativeValid
      ? 'Окремий розрахунок V1+ · ' + nativePlan.quality +
        ' · ' + nativeCurve.length + ' точок. Умовний сценарій, команди заборонені.'
      : 'Окремий розрахунок V1+: очікує перевірених джерел; не підміняється планом V1.';
    if (typeof roomId === 'string') void this.recorder(ha,roomId,now);
    void this.nativeSnapshot(ha,now);
  }
}
const name = (() => {
  const v = new URL(import.meta.url).searchParams.get('v');
  return v && /^[a-f0-9]{12}$/.test(v) ? 'heating-v1-native-' + v : 'heating-v1-native-preview';
})();
if (!customElements.get(name)) customElements.define(name, HeatingV1NativePanel);
