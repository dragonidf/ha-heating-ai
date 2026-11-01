"""Real browser text-scale, two-client transport and collapsed-view measurements."""
import argparse
import json
from functools import partial
from http.server import ThreadingHTTPServer
from threading import Thread
from pathlib import Path
from playwright.sync_api import sync_playwright
from check_ui_browser import QuietHandler,ROOT,ARTIFACTS


def main(engine='chromium'):
    ARTIFACTS.mkdir(exist_ok=True)
    server=ThreadingHTTPServer(('127.0.0.1',0),partial(QuietHandler,directory=str(ROOT)))
    Thread(target=server.serve_forever,daemon=True).start();measurements=[]
    try:
        with sync_playwright() as p:
            kwargs={'executable_path':'/usr/bin/chromium'} if engine=='chromium' and Path('/usr/bin/chromium').exists() else {}
            browser=getattr(p,engine).launch(**kwargs,**({'args':['--no-sandbox']} if engine=='chromium' else {}))
            page=browser.new_page(viewport={'width':390,'height':844});errors=[]
            page.on('pageerror',lambda exc:errors.append(str(exc)))
            page.goto(f'http://127.0.0.1:{server.server_port}/tests/browser-fixture.html')
            page.wait_for_selector('.hero')
            for width in (320,390,430,768,1440):
                page.set_viewport_size({'width':width,'height':1000})
                for scale in (1,1.25,2):
                    page.evaluate('size=>document.documentElement.style.fontSize=size+"px"',16*scale)
                    for tab in ('overview','settings','model'):
                        page.locator(f'nav [data-tab="{tab}"]').click()
                        page.evaluate('()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))')
                        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),(width,scale,tab,'overflow',page.evaluate('document.documentElement.scrollWidth'),page.locator('heating-ai-panel').evaluate("e=>[...e.shadowRoot.querySelectorAll('*')].filter(x=>x.getBoundingClientRect().width>0&&(x.getBoundingClientRect().right>innerWidth+1||x.scrollWidth>x.clientWidth+1)).slice(0,12).map(x=>[x.tagName,x.className,x.parentElement?.className||'',(x.textContent||'').slice(0,65),x.getBoundingClientRect().width,x.getBoundingClientRect().right])"))
                        if tab=='overview':
                            font=page.locator('.hero-side h2').evaluate('e=>parseFloat(getComputedStyle(e).fontSize)')
                            assert font>=14*scale-.1,(width,scale,'fixed-size text',font)
                        for item in page.locator('button:visible,select:visible,input:visible,summary:visible').all():
                            assert item.bounding_box()['height']>=44,(width,scale,tab,'touch target')
                        measurements.append(dict(width=width,scale=scale,tab=tab,height=round(page.locator('main').bounding_box()['height'],1)))
                        if scale==1 and width in (390,1440):
                            page.screenshot(path=str(ARTIFACTS/f'heating-qa-{tab}-{width}-{engine}.png'),full_page=True)
            page.evaluate("document.documentElement.style.fontSize='16px'")
            # Two real component instances share the same HA page entity.
            page.evaluate('''()=>{
                window.second=document.createElement('heating-ai-panel');second.setConfig({});second.tab='model';
                const original=mockHass.callApi;mockHass.callApi=async(...args)=>{const result=await original(...args);second.hass={...mockHass};return result;};
                document.body.append(second);second.hass={...mockHass};panel.hass={...mockHass};
                second.setComparisonDays(1);panel.setComparisonDays(30);
            }''')
            page.wait_for_function('panel.comparisonPending===null&&second.comparisonPending===null')
            assert page.evaluate('panel.comparisonData.actual.length>second.comparisonData.actual.length')
            assert page.evaluate('panel.comparisonData.end-panel.comparisonData.start===30*86400')
            assert page.evaluate('second.comparisonData.end-second.comparisonData.start===86400')
            page.evaluate('second.remove()');assert not errors,errors
            browser.close()
    finally:server.shutdown();server.server_close()
    target=ARTIFACTS/f'heating-qa-layout-{engine}.json';target.write_text(json.dumps(measurements,indent=2)+'\n')
    print(f'{engine}: text scale 100/125/200%, 320–1440px, 44px controls, two-client history: PASS')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--browser',choices=['chromium','webkit'],default='chromium')
    main(parser.parse_args().browser)
