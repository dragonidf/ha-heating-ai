"""Render and verify the real UI with synthetic HA only; never connect to HA."""
import json
from functools import partial
from http.server import ThreadingHTTPServer
from threading import Thread
from pathlib import Path
from playwright.sync_api import sync_playwright
from check_ui_browser import QuietHandler, ROOT, ARTIFACTS


def main():
    ARTIFACTS.mkdir(exist_ok=True)
    server=ThreadingHTTPServer(('127.0.0.1',0),partial(QuietHandler,directory=str(ROOT)))
    Thread(target=server.serve_forever,daemon=True).start()
    measurements=[]
    try:
        with sync_playwright() as p:
            launch={'executable_path':'/usr/bin/chromium'} if Path('/usr/bin/chromium').exists() else {}
            browser=p.chromium.launch(**launch,args=['--no-sandbox'])
            page=browser.new_page(viewport={'width':390,'height':844},device_scale_factor=2)
            errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(f'http://127.0.0.1:{server.server_port}/tests/browser-fixture.html')
            page.wait_for_selector('.hero')
            page.evaluate('''() => {
                setHeatingScenario({
                    'sensor.heating_power_model':{state:'fixed_9',attributes:{recommendation:{mode:'fixed_6',confidence:'validated_scenario',reason:'Для поточного плану достатньо 6 кВт.'}}},
                    'sensor.heating_fireplace':{state:'Горить',attributes:{temperature:35.2,delta:13.4,trend:.7}},
                    'binary_sensor.heating_fireplace_active':{state:'on'},
                    'fan.circulation':{state:'on',attributes:{percentage:38}}
                });
                window.entityOpens=[];
                panel.addEventListener('hass-more-info',e=>entityOpens.push(e.detail.entityId));
            }''')
            page.screenshot(path=str(ARTIFACTS/'heating-mobile-initial-390.png'),full_page=True)
            for width in (320,360,390,430,768,1024,1440):
                page.set_viewport_size({'width':width,'height':844})
                page.evaluate('()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))')
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),(width,'page overflow')
                cards=page.locator('.heat-sources>.card').all()
                a,b=[x.bounding_box() for x in cards]
                assert abs(a['y']-b['y'])<1 and a['x']+a['width']<=b['x'],(width,'heat sources must share a row')
                assert page.locator('.overview-metrics,.forecast-stats,.overview>.plan-card').count()==0
                assert page.locator('.next-action .plan-card').is_visible() is False
                assert page.locator('.forecast-plot svg').bounding_box()['height']<=280
                assert page.locator('[data-forecast-lane="fireplace"]').count()>0
                assert page.locator('.hero-energy .compact-metric').count()==2
                assert '23,0' in page.locator('.hero-energy').inner_text()
                if width==390:
                    assert page.locator('.hero').bounding_box()['height']<300
                    assert page.locator('.heat-sources').bounding_box()['height']<330
                for button in page.locator('button:visible,summary:visible').all():
                    assert button.bounding_box()['height']>=44,(width,'touch target',button.inner_text(),button.bounding_box())
                measurements.append(dict(width=width,hero=page.locator('.hero').bounding_box()['height'],forecast=page.locator('.chart-card').bounding_box()['height'],sources=page.locator('.heat-sources').bounding_box()['height'],total=page.locator('main').bounding_box()['height']))
                if width in (390,1440):
                    page.screenshot(path=str(ARTIFACTS/f'heating-mobile-overview-{width}.png'),full_page=True)
            page.set_viewport_size({'width':390,'height':844})
            # Native summary interaction remains keyboard accessible and survives updates.
            summary=page.locator('.next-action>summary');summary.press('Enter')
            assert page.locator('.next-action .plan-card').is_visible()
            page.evaluate('panel.hass={...panel.hass}')
            assert page.locator('.next-action').get_attribute('open') is not None
            summary.press('Enter');assert not page.locator('.next-action .plan-card').is_visible()
            # Every visible linked number opens precisely its configured HA entity.
            for item in page.locator('.overview [data-info]:visible').all():
                expected=item.get_attribute('data-info');before=page.evaluate('entityOpens.length')
                item.click()
                assert page.evaluate('entityOpens.length')==before+1
                assert page.evaluate('entityOpens.at(-1)')==expected
            assert page.evaluate('calls.length')==0,'Read-only overview must not actuate equipment'
            page.locator('[data-open-manual]').click()
            assert page.locator('.next-action .manual-energy').is_visible()
            assert page.evaluate('calls.length')==0, 'Opening manual heat must not issue a command'
            page.locator('.next-action>summary').click()
            page.locator('.fire-details>summary').click()
            assert page.locator('.fireplace-card [data-fan-mode="Ручний запуск"]').is_visible()
            page.locator('.fire-details>summary').click()
            # Reproduce the actual reported production state, including KWS.
            page.evaluate("""() => {
              const values={kws_auto_restore:'disabled',power_accounting_method:'kws',boiler_power_mode:'fixed_6'};
              mockHass.states={...mockHass.states,
                'sensor.heating_system_settings':{state:'Налаштування',attributes:{revision:1,persisted_revision:1,values}},
                'sensor.heating_diagnostics':{state:'OK',attributes:{reliability:{equipment:{ready:true,reason:'ready',kws_relay_entity:'switch.energy_meter_kotel',kws_relay_state:'on',kws_link_fresh:true,kws_fault:'off',voltage_qualified:true,kws_restore_status:'disabled'}}}},
                'switch.energy_meter_kotel':{state:'on',attributes:{}},
                'sensor.heating_power_confirmed':{state:'0',attributes:{timestamp:new Date().toISOString()}},
                'sensor.probe':{state:'22.4',attributes:{}},
                'sensor.heating_power_model':{state:'fixed_6',attributes:{recommendation:{mode:'fixed_9'}}},
                'sensor.heating_fireplace':{state:'Неактивний',attributes:{temperature:20.9}},
                'fan.circulation':{state:'off',attributes:{}},
                'input_select.heating_fan_mode':{state:'Авто',attributes:{options:['Авто','Зупинити','Ручний запуск']}},
                'input_boolean.heating_fan_control':{state:'on',attributes:{}}};
              panel.hass={...mockHass};window.calls=[];window.systemRequests=[];
            }""")
            for width in (320,360,390,430):
                page.set_viewport_size({'width':width,'height':844})
                page.evaluate('()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))')
                boiler=page.locator('.boiler-card');fire=page.locator('.fireplace-card')
                assert boiler.locator('[data-kws-toggle]').is_visible()
                assert fire.locator('[data-fan-mode]').count()==3
                assert all(c.is_visible() for c in fire.locator('[data-fan-mode]').all())
                print('Production source cards:',width,boiler.bounding_box()['height'],fire.bounding_box()['height'],flush=True)
                page.locator('.heat-sources').screenshot(path=str(ARTIFACTS/f'heating-production-source-cards-{width}.png'))
                assert boiler.bounding_box()['height']<=290,(width,boiler.bounding_box())
                assert fire.bounding_box()['height']<=290,(width,fire.bounding_box())
                assert abs(boiler.bounding_box()['height']-fire.bounding_box()['height'])<=44,(width,boiler.bounding_box(),fire.bounding_box())
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                assert not boiler.locator('.power-advice').get_attribute('open')
                assert not fire.locator('.fire-details').get_attribute('open')
                page.locator('.heat-sources').screenshot(path=str(ARTIFACTS/f'heating-production-source-cards-{width}.png'))
                measurements.append(dict(production_width=width,boiler=boiler.bounding_box()['height'],fireplace=fire.bounding_box()['height']))
            page.set_viewport_size({'width':390,'height':844})
            toggle=page.locator('[data-kws-toggle]')
            assert toggle.get_attribute('aria-checked')=='false'
            toggle.click()
            assert toggle.get_attribute('aria-checked')=='true'
            assert page.evaluate('systemRequests.at(-1).data.values')=={'kws_auto_restore':'enabled'}
            toggle.click()
            assert toggle.get_attribute('aria-checked')=='false'
            assert page.evaluate('systemRequests.at(-1).data.values')=={'kws_auto_restore':'disabled'}
            page.locator('[data-fan-mode="Ручний запуск"]').click()
            assert page.evaluate('calls.at(-1)')=={'domain':'input_select','service':'select_option','data':{'entity_id':'input_select.heating_fan_mode','option':'Ручний запуск'}}
            page.locator('[data-fan-mode="Зупинити"]').click()
            assert page.evaluate('calls.at(-1).data.option')=='Зупинити'
            page.evaluate("setHeatingScenario({'input_select.heating_fan_mode':{state:'Зупинити'}})")
            assert page.locator('[data-fan-mode="Зупинити"]').get_attribute('aria-pressed')=='true'
            page.locator('[data-fan-mode="Авто"]').click()
            assert page.evaluate('calls.at(-1).data.option')=='Авто'
            page.evaluate('calls=[];systemRequests=[]')
            page.locator('nav [data-tab="model"]').click()
            page.wait_for_function('panel.comparisonPending===null')
            page.screenshot(path=str(ARTIFACTS/'heating-mobile-history-390.png'),full_page=True)
            page.locator('nav [data-tab="overview"]').click()
            page.evaluate("document.body.style.cssText+=';--ha-card-background:#fff;--primary-text-color:#202628;--secondary-text-color:#526069;--primary-color:#087e67;background:#f4f6f6';panel.hass={...panel.hass,themes:{darkMode:false}}")
            page.screenshot(path=str(ARTIFACTS/'heating-mobile-overview-light-390.png'),full_page=True)
            page.evaluate("""setHeatingScenario({'sensor.heating_forecast':{attributes:{weather_complete:true,morning_reserve:{comfort_deficit:{coverage_complete:true,first_comfort_violation_at:Date.now()/1000+3600,first_violation_temperature:20.8,first_violation_lower:21.0,first_violation_shortfall:.2}}}}})""")
            assert not page.locator('[data-controller-health]').is_visible(), 'Comfort detail belongs under boiler details'
            page.locator('.power-advice>summary').click()
            assert 'нижче мінімуму' in page.locator('.power-advice').inner_text()
            page.evaluate("setHeatingScenario({'sensor.heating_diagnostics':{attributes:{control_health:'error',control_error:'LedgerConflict: Outstanding reservation'}}})")
            assert page.locator('[data-controller-health][role="alert"]').is_visible(), 'Critical controller failures must remain visible'
            assert not errors,errors
            browser.close()
    finally:
        server.shutdown();server.server_close()
    (ARTIFACTS/'heating-qa-layout-mobile.json').write_text(json.dumps(measurements,indent=2)+'\n')
    print('Mobile layout, collapsed plan, fireplace lane, configured entity links and no service calls: PASS')


if __name__=='__main__':main()
