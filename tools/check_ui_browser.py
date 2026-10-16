"""Exercise the real component with fake HA I/O; never contacts a real home."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
ARTIFACTS=ROOT.parent/'artifacts'


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def main():
    ARTIFACTS.mkdir(exist_ok=True)
    server=ThreadingHTTPServer(('127.0.0.1',0),partial(QuietHandler,directory=str(ROOT)))
    Thread(target=server.serve_forever,daemon=True).start()
    try:
        with sync_playwright() as p:
            executable={'executable_path':'/usr/bin/chromium'} if Path('/usr/bin/chromium').exists() else {}
            browser=p.chromium.launch(**executable,args=['--no-sandbox'])
            page=browser.new_page(viewport={'width':1440,'height':1100},device_scale_factor=1)
            errors=[]
            page.on('pageerror',lambda error: errors.append(str(error)))
            page.goto(f'http://127.0.0.1:{server.server_port}/tests/browser-fixture.html')
            page.wait_for_selector('heating-ai-panel >> text=Накопиченого тепла достатньо')
            page.screenshot(path=str(ARTIFACTS/'heating-desktop.png'),full_page=True)
            for width in (1440,1024,768,390):
                page.set_viewport_size({'width':width,'height':1000})
                for tab in ('overview','settings','model'):
                    page.locator(f'nav [data-tab="{tab}"]').click()
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (width,tab)
                if width==390:
                    page.screenshot(path=str(ARTIFACTS/'heating-mobile-model.png'),full_page=True)
            page.locator('nav [data-tab="settings"]').click()
            field=page.locator('[data-control="target"]')
            field.fill('22.5')
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
            assert not errors, errors
            browser.close()
        print('Browser checks passed: three tabs, four widths, helper writes, errors, unavailable states, escaped sensor text.')
    finally:
        server.shutdown()


if __name__=='__main__':
    main()
