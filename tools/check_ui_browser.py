"""Exercise the real component with fake HA I/O; never contacts a real home."""
from functools import partial
import argparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'apps/heating_ai'))
from heating_ui import system_fields
from heating_model import DEFAULT_MODEL
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
ARTIFACTS=ROOT.parent/'artifacts'


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def wait_for_chart_layout(page):
    # Mobile WebKit can apply a viewport rotation after set_viewport_size returns.
    # Wait for ResizeObserver to finish the actual layout, rather than a fixed delay.
    page.wait_for_function('''() => {
        const svg=document.querySelector('heating-ai-panel')?.shadowRoot.querySelector('.forecast-plot svg');
        if(!svg)return false;
        const box=svg.getBoundingClientRect(), label=svg.querySelector('text');
        return box.height>=250 && Math.abs(box.width-svg.viewBox.baseVal.width)<=1 &&
            parseFloat(label.getAttribute('font-size'))*label.getScreenCTM().a>=10.5;
    }''',timeout=5000)


def background_rgb(locator):
    # Canvas resolves both rgb() and modern color(srgb ...) from CSS color-mix.
    return locator.evaluate('''el=>{
        const c=document.createElement('canvas').getContext('2d');
        c.fillStyle=getComputedStyle(el).backgroundColor;c.fillRect(0,0,1,1);
        return [...c.getImageData(0,0,1,1).data].slice(0,3);
    }''')


def check_theme_and_motion(page):
    original=page.evaluate("document.body.style.cssText")
    for name,dark,ground,surface,ink,muted,accent,radius in [
        ('native-dark',True,'#111111','#1c1c1c','#e1e1e1','#aaaaaa','#80c7ef','20px'),
        ('material-dark',True,'#141218','#211f26','#e6e0e9','#cac4d0','#d0bcff','24px'),
        ('native-light',False,'#fafafa','#ffffff','#212121','#606060','#03a9f4','16px'),
        ('material-light',False,'#fef7ff','#f3edf7','#1d1b20','#49454f','#6750a4','24px'),
    ]:
        page.evaluate('''([dark,ground,surface,ink,muted,accent,radius])=>{
            Object.entries({'background':ground,'--ha-card-background':surface,
              '--primary-text-color':ink,'--secondary-text-color':muted,
              '--primary-color':accent,'--ha-card-border-radius':radius}).forEach(([k,v])=>document.body.style.setProperty(k,v));
            panel.hass={...mockHass,themes:{darkMode:dark}};
        }''',[dark,ground,surface,ink,muted,accent,radius])
        assert page.locator('.hero').evaluate('el=>getComputedStyle(el).borderRadius')==radius, name
        assert page.locator('.hero').evaluate("el=>getComputedStyle(el).backgroundImage==='none'"), name
        colors=page.locator('.hero').evaluate('''el=>{
            const c=document.createElement('canvas').getContext('2d');
            const rgb=color=>{c.fillStyle=color;c.fillRect(0,0,1,1);return [...c.getImageData(0,0,1,1).data].slice(0,3)};
            return [rgb(getComputedStyle(el).backgroundColor),rgb(getComputedStyle(el).color),rgb(getComputedStyle(el.querySelector('.hero-outside')).color)];
        }''')
        def lum(rgb):
            values=[v/255 for v in rgb]
            linear=[v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4 for v in values]
            return sum(v*k for v,k in zip(linear,[.2126,.7152,.0722]))
        for text in colors[1:]:
            a,b=sorted([lum(colors[0]),lum(text)])
            assert (b+.05)/(a+.05)>=4.5, (name,'text contrast')
        assert page.locator('.forecast-plot path[stroke-dasharray]').evaluate('el=>getComputedStyle(el).stroke')==page.locator('[data-history-line="room"]').first.evaluate('el=>getComputedStyle(el).stroke'), (name,'room series color differs between charts')
        for width in (390,1440):
            page.set_viewport_size({'width':width,'height':1000})
            for tab in ('overview','settings','model'):
                page.locator(f'nav [data-tab="{tab}"]').click()
                assert page.locator('h1').count()==1, (name,tab,'missing/duplicate page heading')
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), (name,tab)
            page.locator('nav [data-tab="overview"]').click()
            assert page.locator('.hero').evaluate('el=>getComputedStyle(el).borderRadius')==radius, (name,width)
            if name in ('material-dark','material-light'):
                page.screenshot(path=str(ARTIFACTS/f'heating-theme-{name}-{width}.png'),full_page=True)
    page.evaluate("css=>{document.body.style.cssText=css;panel.hass={...mockHass,themes:{darkMode:true}}}",original)
    page.emulate_media(reduced_motion='reduce')
    assert page.locator('nav button').first.evaluate("el=>getComputedStyle(el).transitionDuration")=='0s'
    assert page.locator('.hero').evaluate("el=>getComputedStyle(el).animationName")=='none'
    page.emulate_media(reduced_motion='no-preference')
    assert page.evaluate('calls.length===0'), 'Theme and motion checks must not actuate anything'


def check_compact_overview(page):
    assert page.locator('.boiler-card').count()==1, 'Boiler metrics must share one card'
    assert page.locator('.heat-sources>.card').count()==2
    assert page.locator('.hero .hero-outside').count()==1
    assert page.locator('.overview>.metrics').count()==0, 'Old standalone metrics must be removed'
    assert page.locator('[data-native="grid"]').count()==0, 'Grid history is no longer displayed'
    headings=page.locator('.heat-sources>.card,.overview>.chart-card,.overview>.plan-card').evaluate_all('els=>els.map(x=>x.querySelector("h2")?.textContent)')
    assert headings==['Котел','Камін і циркуляція','Наступні 24 години','План нагріву'], headings
    assert page.locator('.chart-card + .plan-card').count()==1, 'Heating plan must immediately follow the forecast'
    assert page.locator('.plan-details').get_attribute('open') is None
    assert '17,50' in page.locator('.boiler-card').inner_text()
    assert page.locator('.boiler-card').get_attribute('data-heating')=='false'
    for binary,power,expected in [('on','6000','true'),('off','9000','false'),('unavailable','3000','true'),('unavailable','unavailable','unknown')]:
        page.evaluate('''([binary,power])=>setHeatingScenario({
            'binary_sensor.heating_boiler_active':{state:binary},
            'sensor.heating_power_confirmed':{state:power},
            'sensor.heating_controller':{attributes:{proposed_action:'Нагрів · термостат +2 °C'}}
        })''',[binary,power])
        assert page.locator('.boiler-card').get_attribute('data-heating')==expected
        if expected=='true':
            red,green,blue=background_rgb(page.locator('.boiler-card'))
            assert red>green and blue>green, 'Heating boiler must have a red tint'
            assert page.locator('.boiler-card').evaluate("el=>getComputedStyle(el).backgroundImage==='none'"), 'Source highlight should be a calm flat tint'
    accents=[]
    for phase,active in [('Неактивний','off'),('Розпалювання','off'),('Горить','on'),('Інтенсивно горить','on'),('Згасає','on')]:
        page.evaluate('''([phase,active])=>setHeatingScenario({
            'sensor.heating_fireplace':{state:phase,attributes:{temperature:21.8+({'Неактивний':.2,'Розпалювання':3,'Горить':6,'Інтенсивно горить':11,'Згасає':4}[phase]),delta:{'Неактивний':.2,'Розпалювання':3,'Горить':6,'Інтенсивно горить':11,'Згасає':4}[phase]}},
            'binary_sensor.heating_fireplace_active':{state:active},
            'sensor.heating_fan':{attributes:{selected_level:phase==='Інтенсивно горить'?3:2,requested:active==='on',manual_running:active==='off'}},
            'fan.circulation':{state:'on',attributes:{percentage:phase==='Інтенсивно горить'?60:42}}
        })''',[phase,active])
        assert page.locator('.fireplace-card').get_attribute('data-phase')==phase
        assert phase in page.locator('.fireplace-card .fire-phase').inner_text()
        assert ('60 %' if phase=='Інтенсивно горить' else '42 %') in page.locator('.fan-actual').inner_text()
        accents.append(page.locator('.fire-phase').evaluate('el=>getComputedStyle(el).color'))
        if phase!='Неактивний':
            red,green,blue=background_rgb(page.locator('.fireplace-card'))
            assert (red>green and blue>green) if phase in ('Горить','Інтенсивно горить') else (red>green>blue), (phase,'wrong tint')
        if page.viewport_size['width']<=430:
            assert page.locator('.fireplace-card').bounding_box()['height']<225, (phase,'fireplace too tall')
            assert page.locator('.fan-actual').evaluate('el=>el.scrollWidth<=el.clientWidth')
        if page.viewport_size['width']==360:
            page.locator('.fireplace-card').screenshot(path=str(ARTIFACTS/f'heating-fireplace-{phase}-360.png'))
    assert len(set(accents))>=3, 'Off, burning and transitional phases need distinct accents'
    page.evaluate("setHeatingScenario({'sensor.heating_forecast':{attributes:{schedule_complete:true,weather_complete:true}},'sensor.heating_outage_schedule':{attributes:{known_until:Date.now()/1000+86400}}})")
    assert page.locator('.forecast-warning').count()==0, 'Complete forecast must not show generic warnings'
    assert 'Тло — комфортний діапазон' not in page.locator('.chart-card').inner_text()
    assert 'Час майбутнього горіння невідомий' not in page.locator('.chart-card').inner_text()
    page.locator('.forecast-help summary').click()
    assert 'Час майбутнього горіння невідомий' in page.locator('.chart-card').inner_text()
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), 'Forecast help overflow'
    page.locator('.forecast-help summary').click()
    for missing in (0,42,59,60):
        page.evaluate('''missing=>setHeatingScenario({'sensor.heating_forecast':{attributes:{
            schedule_complete:true,weather_complete:true,weather_missing_minutes:missing}}})''',missing)
        assert page.locator('.forecast-warning').count()==0, (missing,'Tolerated weather tail must not warn')
    page.evaluate("setHeatingScenario({'sensor.heating_forecast':{attributes:{schedule_complete:false,weather_complete:true}}})")
    assert 'Графік мережі неповний' in page.locator('.forecast-warning').inner_text()
    for missing in (61,95,None):
        page.evaluate('''missing=>setHeatingScenario({'sensor.heating_forecast':{attributes:{
            schedule_complete:true,weather_complete:false,weather_missing_minutes:missing}}})''',missing)
        assert 'Погодинна погода неповна' in page.locator('.forecast-warning').inner_text()
    page.evaluate("""setHeatingScenario({
        'sensor.heating_forecast':{attributes:{schedule_complete:false,weather_complete:true,weather_missing_minutes:42}},
        'sensor.heating_outage_schedule':{attributes:{warning:'Аварійне відключення'}}})""")
    warnings=page.locator('.forecast-warning').all_text_contents()
    assert any('Графік мережі неповний' in warning for warning in warnings)
    assert any('Аварійне відключення' in warning for warning in warnings)
    assert all('Погодинна погода неповна' not in warning for warning in warnings)
    page.evaluate("""setHeatingScenario({
        'sensor.heating_forecast':{attributes:{schedule_complete:false,weather_complete:true,outage_mode:'normal'}},
        'sensor.heating_outage_schedule':{attributes:{mode:'normal',warning:null,emergency:false,outages:[]}}})""")
    assert page.locator('.forecast-warning').count()==0, 'Normal mode must not warn about absent schedule'
    assert page.locator('[data-forecast-metric="schedule"] strong').inner_text()=='Не очікуються'
    page.evaluate("""setHeatingScenario({
        'sensor.heating_forecast':{attributes:{schedule_complete:true,weather_complete:true,outage_mode:'normal'}},
        'sensor.heating_outage_schedule':{attributes:{mode:'unknown',warning:'Графік недоступний'}}})""")
    assert 'Графік недоступний' in page.locator('.forecast-warning').all_text_contents()
    assert any('Графік мережі неповний' in text for text in page.locator('.forecast-warning').all_text_contents())
    page.evaluate("""setHeatingScenario({
        'sensor.heating_forecast':{attributes:{schedule_complete:false,weather_complete:true}},
        'sensor.heating_outage_schedule':{attributes:{mode:'scheduled',emergency:true,warning:'Аварійні відключення: графік не гарантує світло'}}})""")
    assert any('Аварійні відключення' in text for text in page.locator('.forecast-warning').all_text_contents())
    page.evaluate("setHeatingScenario({'sensor.heating_forecast':{attributes:{planned_kwh:null}}})")
    assert page.locator('[data-forecast-metric="energy"] strong').inner_text()=='—'
    # Automatic completion is separate from the future plan and manual power.
    page.evaluate("""setHeatingScenario({'sensor.heating_forecast':{attributes:{morning_reserve:{
        planned_minutes:30,confirmed_completed_minutes:0,confirmed_kwh:0,manual_completed_minutes:12,
        reserve_sufficient:null,predicted_sufficient:true}}}})""")
    metrics=page.locator('[data-reserve-execution]')
    assert 'Заплановано вранці' in metrics.inner_text()
    assert 'Автоматично виконано' in metrics.inner_text()
    assert 'Ручний нагрів вранці' in metrics.inner_text()
    values=metrics.locator('strong').all_text_contents()
    assert values==['30,0 хв','0,0 хв','0,00 кВт·год','12,0 хв'],values
    assert 'бракує перевірки потрібного горизонту' in page.locator('.plan-card').inner_text()
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    page.evaluate("setHeatingScenario({'sensor.heating_forecast':{attributes:{morning_reserve:null}}})")
    assert page.locator('[data-reserve-execution]').count()==0
    page.evaluate('panel.hass=mockHass')


def check_compact_tabs(page):
    page.locator('nav [data-tab="settings"]').click()
    assert page.locator('.settings-page').count()==1
    levels=page.locator('.circulation-levels [data-control]').evaluate_all('els=>els.map(el=>el.dataset.control)')
    assert levels==['fan_night_limit','fan_speed','fan_intense_speed'], levels
    field=page.locator('[data-control="fan_intense_speed"]')
    field.fill('65');field.press('Tab')
    page.wait_for_function('calls.some(c=>c.data.entity_id==="input_number.heating_fan_intense_speed")')
    assert page.evaluate('calls.at(-1).data.value')==65
    page.evaluate('calls=[];panel.hass=mockHass')
    page.locator('nav [data-tab="model"]').click()
    assert page.locator('.model-page .model-stats .compact-metric').count()==4
    assert page.locator('.model-advanced details').get_attribute('open') is None
    page.locator('.model-advanced summary').click()
    assert page.locator('.parameter-grid').is_visible()
    assert page.locator('.controller-facts').is_visible()
    page.locator('.model-advanced summary').click()
    page.locator('nav [data-tab="overview"]').click()


def check_system_settings(page):
    page.locator('nav [data-tab="settings"]').click()
    panel=page.locator('.system-settings')
    assert panel.get_attribute('open') is None
    panel.locator(':scope>summary').click()
    assert page.evaluate('systemRequests.length')==0
    fields=page.locator('[data-system-key]')
    assert fields.count()==25, fields.count()  # 20 config values and five thermal coefficients.
    for summary in panel.locator('.system-group>summary').all():summary.click()
    assert panel.locator('.system-help').count()==57
    page.locator('[data-system-help="fire_start_delta"]').click()
    assert page.locator('.system-dialog').is_visible()
    assert '5' in page.locator('.system-dialog').inner_text()
    page.keyboard.press('Escape')
    assert page.locator('.system-dialog').count()==0
    assert page.evaluate('systemRequests.length')==0, 'Reading settings/help must not write'
    help_keys=panel.locator('[data-system-help]').evaluate_all('els=>els.map(el=>el.dataset.systemHelp)')
    for key in help_keys:
        page.evaluate("key=>[...panel.shadowRoot.querySelectorAll('[data-system-help]')].find(el=>el.dataset.systemHelp===key).click()",key)
        assert page.locator('.system-dialog h2').inner_text().strip()
        assert 'undefined' not in page.locator('.system-dialog').inner_text()
        page.locator('[data-system-close]').click()
    assert page.evaluate('systemRequests.length')==0
    before=page.evaluate("panel.hass.states['sensor.heating_system_settings'].attributes.fields.find(f=>f.key==='outage_max_age').value")
    field=page.locator('[data-system-key="outage_max_age"]')
    field.fill('7200');field.press('Tab')
    assert page.evaluate('systemRequests.length')==0, 'Editing a draft must not apply it'
    assert page.evaluate("panel.hass.states['sensor.heating_system_settings'].attributes.fields.find(f=>f.key==='outage_max_age').value")==before
    page.locator('[data-system-save]').click()
    page.wait_for_function('systemRequests.length===1')
    assert page.evaluate('systemRequests[0].path')=='events/heating_system_settings'
    assert page.evaluate('systemRequests[0].data.values')=={'outage_max_age':7200}
    page.wait_for_function("document.querySelector('heating-ai-panel').shadowRoot.querySelector('.system-message').textContent.includes('Зміни збережено')")
    page.evaluate('failSystem=true')
    field.fill('3600');field.press('Tab');page.locator('[data-system-save]').click()
    assert 'Не вдалося' in page.locator('.system-message').inner_text()
    page.evaluate('failSystem=false')
    page.locator('[data-system-reset]').click()
    assert field.input_value()=='7200'
    for width in (430,390,360,320):
        page.set_viewport_size({'width':width,'height':850})
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        for control in page.locator('[data-system-key],.system-help').all():
            assert control.bounding_box()['height']>=40
        page.locator('[data-system-help="floor_air"]').click()
        box=page.locator('.system-dialog').bounding_box()
        assert box['x']>=0 and box['x']+box['width']<=width
        if width==390:page.locator('.system-dialog').screenshot(path=str(ARTIFACTS/'heating-system-help-390.png'))
        page.locator('[data-system-close]').click()
        if width==390:panel.screenshot(path=str(ARTIFACTS/'heating-system-settings-390.png'))
    # Close nested groups so the main density checks still inspect the default layout.
    for summary in panel.locator('.system-group>summary').all():summary.click()
    panel.locator(':scope>summary').click()
    page.set_viewport_size({'width':1440,'height':1100})
    page.evaluate('systemRequests=[];calls=[]')
    page.locator('nav [data-tab="overview"]').click()


def main(engine='chromium'):
    ARTIFACTS.mkdir(exist_ok=True)
    server=ThreadingHTTPServer(('127.0.0.1',0),partial(QuietHandler,directory=str(ROOT)))
    Thread(target=server.serve_forever,daemon=True).start()
    try:
        with sync_playwright() as p:
            executable={'executable_path':'/usr/bin/chromium'} if engine=='chromium' and Path('/usr/bin/chromium').exists() else {}
            browser=getattr(p,engine).launch(**executable,**({'args':['--no-sandbox']} if engine=='chromium' else {}))
            page=browser.new_page(viewport={'width':1440,'height':1100},device_scale_factor=1)
            errors=[]
            page.on('pageerror',lambda error: errors.append(str(error)))
            page.goto(f'http://127.0.0.1:{server.server_port}/tests/browser-fixture.html')
            page.wait_for_selector('heating-ai-panel >> text=Накопиченого тепла достатньо')
            page.evaluate('''fields=>{
                mockHass.states={...mockHass.states,'sensor.heating_system_settings':{state:'Налаштування системи',attributes:{revision:0,fields}}};
                panel.hass={...mockHass};
            }''',system_fields({},DEFAULT_MODEL))
            check_compact_overview(page)
            page.wait_for_function('nativeConfigs.length === 2')
            check_compact_tabs(page)
            check_system_settings(page)
            page.wait_for_selector('.history-plot svg')
            check_theme_and_motion(page)
            assert page.locator('[data-history-lane="boiler"][data-state="on"]').count()==2, 'Past boiler runs must remain visible when the binary sensor is new'
            assert '4 год 0 хв' in page.locator('.history-totals').inner_text()
            assert page.evaluate("historyRequests[0].entity_ids.includes('sensor.heating_boiler_stage')")
            assert page.evaluate("!historyRequests[0].entity_ids.includes('sensor.heating_power_confirmed')"), 'Do not load the high-frequency raw power stream'
            page.locator('[data-history-range="6"]').click()
            page.locator('[data-history-series="outside"]').click()
            assert page.locator('[data-history-line="outside"]').count()>0
            page.locator('.history-plot').focus();page.locator('.history-plot').press('ArrowLeft')
            page.wait_for_selector('.history-crosshair',state='attached')
            assert 'Котел:' in page.locator('.history-readout').inner_text() and 'Камін:' in page.locator('.history-readout').inner_text()
            page.locator('[data-history-range="24"]').click()
            assert page.locator('[data-series="fireplace"]').count()==1
            assert 'оцінка залишкового тепла' in page.locator('[data-series="fireplace"] title').text_content()
            assert page.locator('.fireplace-card').count()==1, 'Fireplace must have a visible overview card'
            assert page.locator('.fireplace-card').bounding_box()['y']<page.locator('.chart-card').bounding_box()['y'], 'Fireplace should precede charts'
            page.evaluate('''() => {
                const updated={...mockHass.states,
                  'sensor.heating_fireplace':{state:'Горить',attributes:{temperature:25.6,delta:4.1,trend:1.2}},
                  'binary_sensor.heating_fireplace_active':{state:'on',attributes:{}},
                  'sensor.heating_fan':{state:'Очікування живлення',attributes:{requested:'true',control_enabled:'true',proposed_percentage:42,reason:'Запит є; резервне живлення вентилятора не підтверджено'}}};
                panel.hass={...mockHass,states:updated};
            }''')
            fire=page.locator('.fireplace-card')
            assert '25,6' in fire.inner_text() and '+4,1' in fire.inner_text()
            assert not fire.locator('details').get_attribute('open'), 'Technical details should stay collapsed'
            fire.locator('summary').click()
            assert 'резервне живлення' in fire.inner_text()
            assert 'Вимкнено' in fire.inner_text(), 'Pending fan demand must not be presented as actual airflow'
            page.evaluate("panel.hass={...panel.hass,states:{...panel.hass.states,'sensor.heating_fireplace':{state:'Датчик недоступний',attributes:{temperature:25.6,delta:4.1,trend:1.2}}}}")
            assert fire.locator('details').get_attribute('open') is not None, 'Live readings must preserve expanded details'
            assert '25,6' not in fire.inner_text() and 'Датчик недоступний' in fire.inner_text(), 'Unavailable fireplace must not show stale heat readings'
            page.locator('[data-settings-target="fireplace-settings"]').click()
            assert page.locator('#fireplace-settings [data-control="fan_mode"]').is_visible()
            page.locator('nav [data-tab="overview"]').click()
            page.evaluate('panel.hass=mockHass')
            page.locator('[data-press="weather_refresh"]').click()
            page.wait_for_function('calls.length === 1')
            assert page.evaluate('calls[0]')=={'domain':'input_button','service':'press','data':{'entity_id':'input_button.heating_forecast_refresh'}}
            page.evaluate('calls=[]')
            # All 25 controls from the original climate dashboards remain reachable.
            expected_controls={
                'ai_auto_apply','armed','fan_control','restore_once','weather_refresh','rollback',
                'end','sleep_end','sleep_start','start','fan_night_limit','fan_speed','fan_intense_speed','lower',
                'monthly_budget','native_setpoint','night_lower','night_target','night_upper',
                'session_limit','target','upper','fan_mode','mode','strategy',
            }
            reachable=set()
            for tab in ('overview','settings','model'):
                page.locator(f'nav [data-tab="{tab}"]').click()
                reachable.update(page.locator('[data-control],[data-press]').evaluate_all('els=>els.map(el=>el.dataset.control || el.dataset.press)'))
            assert expected_controls<=reachable, ('Missing original controls',expected_controls-reachable)
            page.locator('nav [data-tab="overview"]').click()
            # A live measurement must not tear down native HA chart subscriptions.
            page.wait_for_function('panel.nativeCards.size === 2')
            page.evaluate('''() => {
                window.chartDisconnects=0;
                window.firstHistory=panel.nativeCards.get('energy');
                window.historyFetchesBeforeUpdate=historyRequests.length;
                const observer=new MutationObserver(records=>{
                    for(const record of records) for(const node of record.removedNodes)
                        if(node===firstHistory || node.contains?.(firstHistory)) chartDisconnects++;
                });
                observer.observe(panel.shadowRoot,{childList:true,subtree:true});
                panel.hass={...mockHass,states:{...mockHass.states,
                    'sensor.heating_heartbeat':{...mockHass.states['sensor.heating_heartbeat'],state:new Date().toISOString()}}};
            }''')
            page.wait_for_timeout(100)
            assert page.evaluate('chartDisconnects === 0'), 'Live update disconnected native charts'
            assert page.evaluate('historyFetchesBeforeUpdate === historyRequests.length'), 'Ordinary live updates must reuse Recorder history'
            # HA sidebar/card width can change without a viewport resize.
            page.evaluate("panel.style.width='720px'")
            page.wait_for_timeout(150)
            assert len(page.locator('.columns').evaluate('el=>getComputedStyle(el).gridTemplateColumns.split(" ")'))==1, 'Layout uses viewport instead of panel width'
            page.evaluate("panel.style.removeProperty('width')")
            page.screenshot(path=str(ARTIFACTS/'heating-desktop.png'),full_page=True)
            for width in (1920,1440,1024,768,620,600,430,390,360,320):
                page.set_viewport_size({'width':width,'height':1000})
                for tab in ('overview','settings','model'):
                    page.locator(f'nav [data-tab="{tab}"]').click()
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (width,tab)
                    assert page.evaluate('''()=>{const ids=[...panel.shadowRoot.querySelectorAll('[id]')].map(el=>el.id);return ids.length===new Set(ids).size}'''), (width,tab,'duplicate IDs')
                    if tab in ('settings','model'):
                        for control in page.locator('.field input,.field select,.field button').all():
                            assert control.bounding_box()['height']>=40, (width,tab,'small touch target')
                        if width in (360,390,430,768):
                            assert page.locator('main').bounding_box()['height']<(2200 if tab=='settings' else 2700), (width,tab,'page not compact')
                    if tab=='overview':
                        if width in (360,430):check_compact_overview(page)
                        wait_for_chart_layout(page)
                        svg=page.locator('.chart-card svg')
                        assert svg.bounding_box()['height']>=250, (width,'chart too short')
                        font=svg.locator('text').first.evaluate('el=>parseFloat(el.getAttribute("font-size"))*el.getScreenCTM().a')
                        assert font>=10.5, (width,'unreadable chart labels',font)
                        for canvas in page.locator('fixture-native-chart canvas').all():
                            assert canvas.bounding_box()['width']<=page.locator('main').bounding_box()['width'], (width,'native canvas overflow')
                        labels=svg.locator('text').evaluate_all('els=>els.map(el=>{const r=el.getBoundingClientRect();return {x:r.x,right:r.right}})')
                        bounds=svg.bounding_box()
                        assert all(r['x']>=bounds['x']-1 and r['right']<=bounds['x']+bounds['width']+1 for r in labels), (width,'clipped chart labels')
                        history=page.locator('.history-plot svg')
                        page.wait_for_function("Math.abs(panel.shadowRoot.querySelector('.history-plot svg').getBoundingClientRect().width-panel.shadowRoot.querySelector('.history-plot svg').viewBox.baseVal.width)<=1")
                        assert history.bounding_box()['height']>=300, (width,'history too short')
                        assert history.locator('text').first.evaluate('el=>parseFloat(el.getAttribute("font-size"))*el.getScreenCTM().a')>=10.5, (width,'unreadable history labels')
                        assert page.locator('[data-history-lane="boiler"][data-state="on"]').count()==2
                        cells=page.locator('.forecast-stats').evaluate('el=>getComputedStyle(el).gridTemplateColumns.split(" ").length')
                        assert cells==(2 if width<=700 else 4), (width,'forecast metric columns',cells)
                        for metric in page.locator('.boiler-metrics .compact-metric,.forecast-stats .compact-metric').all():
                            assert metric.evaluate('el=>el.scrollWidth<=el.clientWidth'), (width,'metric overflow')
                        forecast_box=page.locator('.chart-card').bounding_box()
                        plan_box=page.locator('.plan-card').bounding_box()
                        assert 0<=plan_box['y']-forecast_box['y']-forecast_box['height']<=16, (width,'plan separated from forecast')
                        if width in (360,390,430):
                            # v0.2.4 was 4493–4550 px on these same synthetic fixtures.
                            assert page.locator('main').bounding_box()['height']<3650, (width,'overview did not become compact')
                            assert page.locator('.hero').bounding_box()['height']<225
                            assert page.locator('.boiler-card').bounding_box()['height']<175
                            assert page.locator('.fireplace-card').bounding_box()['height']<225
                            assert page.locator('.plan-card').bounding_box()['height']<420
                        if width<=600:
                            assert page.locator('.fireplace-card').bounding_box()['height']<250, (width,'fireplace overview should be compact')
                            left=page.locator('.hero>div').first.bounding_box()
                            right=page.locator('.hero-side').bounding_box()
                            assert right['x']>=left['x']+left['width'], (width,'controller decision should share the comfort row')
                            assert page.locator('.temperature').evaluate('el=>el.scrollWidth <= el.clientWidth'), (width,'temperature overflow')
                        if width==390:
                            page.screenshot(path=str(ARTIFACTS/f'heating-mobile-overview-{engine}.png'),full_page=True)
                            page.locator('.history-card').screenshot(path=str(ARTIFACTS/f'heating-history-mobile-{engine}.png'))
                if width==390:
                    page.screenshot(path=str(ARTIFACTS/'heating-mobile-model.png'),full_page=True)
            page.locator('nav [data-tab="settings"]').click()
            field=page.locator('[data-control="target"]')
            field.fill('22.5')
            page.evaluate("panel.hass={...panel.hass,states:{...panel.hass.states,'sensor.heating_heartbeat':{state:new Date().toISOString(),attributes:{}}}}")
            assert field.input_value()=='22.5', 'Live update overwrote an edited value'
            field.press('Tab')
            page.wait_for_function('window.calls.length === 1')
            assert page.evaluate('calls[0]')=={'domain':'input_number','service':'set_value','data':{'value':22.5,'entity_id':'input_number.heating_target'}}
            page.evaluate('window.failService=true')
            page.locator('[data-control="target"]').fill('23')
            page.locator('[data-control="target"]').press('Tab')
            page.wait_for_selector('[role="alert"]:visible')
            assert 'Немає доступу' in page.locator('[role="alert"]').inner_text()
            page.evaluate("panel.hass={...mockHass,states:{...mockHass.states,'input_number.heating_target':{state:'unavailable',attributes:{}}}}")
            assert page.locator('[data-control="target"]').is_disabled()
            page.evaluate("mockHass.states['sensor.heating_controller'].attributes.reason='<img src=x onerror=alert(1)>'; panel.hass=mockHass")
            page.locator('nav [data-tab="overview"]').click()
            assert page.locator('heating-ai-panel img').count()==0
            page.evaluate('emptyHistory=true');page.locator('[data-history-refresh]').click()
            page.wait_for_selector('[data-history-lane="boiler"][data-state="unknown"]')
            assert 'немає даних' in page.locator('.history-totals').inner_text()
            page.evaluate('emptyHistory=false;failHistory=true;panel.historyData=null;panel.loadHistory(true)')
            page.wait_for_selector('.history-placeholder >> text=Не вдалося завантажити історію')
            assert page.locator('[data-history-lane="boiler"][data-state="on"]').count()==0
            page.evaluate('failHistory=false');page.locator('[data-history-refresh]').click()
            page.wait_for_selector('[data-history-lane="boiler"][data-state="on"]')
            # Closing the HA view during a request must not suppress the next load.
            page.evaluate('''() => {
                panel.hass={...mockHass,callWS:()=>new Promise(resolve=>window.resolveOldHistory=resolve)};
                panel.historyData=null;panel.loadHistory(true);
                panel.remove();panel.hass=mockHass;
                document.body.append(panel);
            }''')
            page.wait_for_selector('[data-history-lane="boiler"][data-state="on"]',timeout=5000)
            page.evaluate('resolveOldHistory({})')
            page.wait_for_timeout(50)
            assert page.locator('[data-history-lane="boiler"][data-state="on"]').count()==2, 'Late history response replaced a fresh load'
            # Phone WebView: touch, high pixel density and rotation in-place.
            mobile=browser.new_context(viewport={'width':390,'height':844},is_mobile=True,has_touch=True,device_scale_factor=3)
            phone=mobile.new_page()
            phone.goto(f'http://127.0.0.1:{server.server_port}/tests/browser-fixture.html')
            phone.wait_for_selector('.forecast-plot svg')
            for size in ({'width':390,'height':844},{'width':844,'height':390},{'width':360,'height':800}):
                phone.set_viewport_size(size)
                wait_for_chart_layout(phone)
                assert phone.evaluate('document.documentElement.scrollWidth <= innerWidth'), ('mobile',engine,size)
                chart=phone.locator('.forecast-plot svg')
                assert chart.bounding_box()['height']>=250
                assert chart.locator('text').first.evaluate('el=>parseFloat(el.getAttribute("font-size"))*el.getScreenCTM().a')>=10.5
                phone.wait_for_selector('.history-plot svg')
                phone.locator('.history-plot').tap()
                phone.wait_for_selector('.history-crosshair',state='attached')
                assert 'Котел:' in phone.locator('.history-readout').inner_text()
            mobile.close()
            page.evaluate("panel.hass={...mockHass,themes:{darkMode:false}};document.body.style.background='#fafafa';document.body.style.setProperty('--primary-text-color','#17271f');document.body.style.setProperty('--secondary-text-color','#56675d');document.body.style.setProperty('--ha-card-background','#fff')")
            page.locator('.history-card').screenshot(path=str(ARTIFACTS/f'heating-history-light-{engine}.png'))
            page.locator('.heat-sources').screenshot(path=str(ARTIFACTS/f'heating-sources-light-{engine}.png'))
            page.locator('nav [data-tab="model"]').click()
            page.evaluate("setHeatingScenario({'sensor.heating_ai_summary':{state:'Новий AI-звіт без очікування циклу'}})")
            assert 'Новий AI-звіт без очікування циклу' in page.locator('.content').inner_text()
            page.locator('nav [data-tab="overview"]').click()
            page.evaluate('panel.hass={...mockHass,states:{}}')
            assert page.locator('.boiler-card').get_attribute('data-heating')=='unknown'
            assert page.locator('[data-forecast-metric="energy"] strong').inner_text()=='—'
            assert page.locator('.boiler-status').inner_text()=='Немає даних'
            assert not errors, errors
            browser.close()
        print(f'{engine}: browser checks passed: compact grouping/order/heights, boiler and five fireplace states, conditional forecast warnings, unavailable energy, unique IDs, ten widths, touch/rotation, stable charts, instant summary display and all 25 controls.')
    finally:
        server.shutdown()


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--browser',choices=('chromium','webkit'),default='chromium')
    main(parser.parse_args().browser)
