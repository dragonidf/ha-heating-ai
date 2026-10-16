"""Exercise the real component with fake HA I/O; never contacts a real home."""
from functools import partial
import argparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
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
            page.wait_for_function('nativeConfigs.length === 4')
            history=page.evaluate("nativeConfigs.find(c=>c.entities?.some(e=>e.entity==='binary_sensor.heating_fireplace_active'))")
            assert any(e.get('entity')=='binary_sensor.heating_boiler_active' for e in history['entities'] if isinstance(e,dict)), 'History needs actual boiler and fireplace lanes on the same time axis'
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
            # All 24 controls from the original climate dashboards remain reachable.
            expected_controls={
                'ai_auto_apply','armed','fan_control','restore_once','weather_refresh','rollback',
                'end','sleep_end','sleep_start','start','fan_night_limit','fan_speed','lower',
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
            page.wait_for_function('panel.nativeCards.size === 4')
            page.evaluate('''() => {
                window.chartDisconnects=0;
                window.firstHistory=panel.nativeCards.get('history');
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
                    if tab=='overview':
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
                        if width<=600:
                            assert page.locator('.fireplace-card').bounding_box()['height']<250, (width,'fireplace overview should be compact')
                            left=page.locator('.hero>div').first.bounding_box()
                            right=page.locator('.hero-side').bounding_box()
                            assert right['x']>=left['x']+left['width'], (width,'controller decision should share the comfort row')
                            assert page.locator('.temperature').evaluate('el=>el.scrollWidth <= el.clientWidth'), (width,'temperature overflow')
                        if width==390:
                            page.screenshot(path=str(ARTIFACTS/f'heating-mobile-overview-{engine}.png'),full_page=True)
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
            mobile.close()
            assert not errors, errors
            browser.close()
        print(f'{engine}: browser checks passed: stable charts, ten widths, readable labels, compact comfort row, fireplace states, all 24 controls, mobile rotation, helper writes and errors.')
    finally:
        server.shutdown()


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--browser',choices=('chromium','webkit'),default='chromium')
    main(parser.parse_args().browser)
