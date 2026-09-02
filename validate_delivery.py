from __future__ import annotations
import hashlib, http.server, json, socketserver, threading
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parent
BUILD=ROOT/'delivery-build'
EXTRACT=BUILD/'extract-check'/'client-presentation'
OUT=BUILD/'browser-validation.json'
THEMES=[
 ('theme-01','pursuit','Backcountry'),
 ('theme-02','logbook',"Captain's Log"),
 ('theme-03','strike','Strike Zone'),
 ('theme-04','openwater','Driftline'),
]

class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

def main():
    if not EXTRACT.exists():
        raise RuntimeError('Clean extracted package is missing.')
    handler=lambda *a,**kw: Quiet(*a,directory=str(EXTRACT),**kw)
    srv=socketserver.TCPServer(('127.0.0.1',0),handler)
    port=srv.server_address[1]
    th=threading.Thread(target=srv.serve_forever,daemon=True); th.start()
    results=[]; screenshot_hashes=[]; errors=[]
    try:
      with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        for folder,cid,title in THEMES:
          item={'folder':folder,'concept':cid,'title':title,'desktop':{},'mobile':{}}
          for mode,viewport in [('desktop',{'width':1440,'height':1000}),('mobile',{'width':390,'height':844})]:
            page=browser.new_page(viewport=viewport, device_scale_factor=1)
            page_errors=[]; failed=[]; external_assets=[]
            page.on('pageerror',lambda exc,arr=page_errors: arr.append(str(exc)))
            page.on('requestfailed',lambda req,arr=failed: arr.append(req.url))
            def on_req(req, arr=external_assets):
              u=urlparse(req.url)
              if u.scheme in ('http','https') and u.hostname not in ('127.0.0.1','localhost') and req.resource_type in ('image','font','stylesheet','script','media'):
                arr.append(req.url)
            page.on('request',on_req)
            url=f'http://127.0.0.1:{port}/{folder}/index.html'
            resp=page.goto(url,wait_until='networkidle',timeout=60000)
            if not resp or resp.status>=400:
              errors.append(f'{folder} {mode}: failed to load ({resp.status if resp else "no response"})')
              page.close(); continue
            page.wait_for_timeout(800)
            active=page.locator(f'section.concept#{cid}.active').count()
            concepts=page.locator('section.concept').count()
            imgs=page.locator('img').count()
            broken=page.evaluate("() => [...document.images].filter(i => !i.complete || i.naturalWidth===0).map(i=>i.getAttribute('src'))")
            overflow=page.evaluate('() => Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) - window.innerWidth')
            height=page.evaluate('() => document.documentElement.scrollHeight')
            for frac in (0.25,0.5,0.75,1):
              page.evaluate(f'window.scrollTo(0,{int(height*frac)})')
              page.wait_for_timeout(200)
            motion_count=page.evaluate("""() => {
              let n=document.getAnimations().length;
              if(n) return n;
              return [...document.querySelectorAll('*')].filter(el=>{
                const s=getComputedStyle(el);
                return (s.animationName && s.animationName!=='none') ||
                       (s.transitionDuration && s.transitionDuration.split(',').some(v=>parseFloat(v)>0));
              }).length;
            }""")
            nav_ok=True
            links=page.locator('a[href^="#"]:not([href="#"])')
            if links.count():
              href=links.first.get_attribute('href')
              links.first.click(timeout=5000)
              page.wait_for_timeout(100)
              nav_ok=(page.url.endswith(href) or href in page.url)
            interaction_ok=True
            try:
              if cid=='pursuit':
                root=page.locator('#bcReviewCarousel')
                if root.count():
                  before=root.locator('.active').count()
                  btn=root.locator('button').last
                  if btn.count(): btn.click(); page.wait_for_timeout(350)
                  interaction_ok=root.locator('.active').count()>=before
              elif cid=='logbook':
                root=page.locator('#islandReviewCarousel')
                if root.count():
                  btn=root.locator('button').last
                  if btn.count(): btn.click(); page.wait_for_timeout(350)
                  interaction_ok=True
              elif cid=='strike':
                btn=page.locator('[data-gallery-index="1"]')
                if btn.count():
                  btn.first.click(); page.wait_for_timeout(350)
                  interaction_ok=page.locator('.strike-gallery-slide.active').count()==1
              elif cid=='openwater':
                btn=page.locator('#dlTideNext')
                if btn.count(): btn.click(); page.wait_for_timeout(500)
                interaction_ok=True
            except Exception as e:
              interaction_ok=False
              page_errors.append(f'interaction smoke test: {e}')
            shot=BUILD/f'{folder}-{mode}.png'
            page.screenshot(path=str(shot),full_page=True)
            sh=hashlib.sha256(shot.read_bytes()).hexdigest(); screenshot_hashes.append((folder,mode,sh))
            shot.unlink(missing_ok=True)
            item[mode]={
              'active_concept':active==1,
              'concept_count':concepts,
              'images':imgs,
              'broken_images':broken,
              'horizontal_overflow_px':overflow,
              'navigation_ok':nav_ok,
              'interaction_smoke_test':interaction_ok,
              'motion_or_transition_elements':motion_count,
              'page_errors':page_errors,
              'failed_requests':failed,
              'external_asset_requests':external_assets,
              'screenshot_sha256':sh,
            }
            if active!=1 or concepts!=1: errors.append(f'{folder} {mode}: standalone concept activation failed')
            if broken: errors.append(f'{folder} {mode}: broken images {broken[:3]}')
            if overflow>4: errors.append(f'{folder} {mode}: page horizontal overflow {overflow}px')
            if not nav_ok: errors.append(f'{folder} {mode}: internal navigation check failed')
            if not interaction_ok: errors.append(f'{folder} {mode}: interaction smoke test failed')
            if motion_count<1: errors.append(f'{folder} {mode}: no motion/transition behavior detected')
            if page_errors: errors.append(f'{folder} {mode}: JS errors: {page_errors[:2]}')
            if failed: errors.append(f'{folder} {mode}: failed requests: {failed[:2]}')
            if external_assets: errors.append(f'{folder} {mode}: external asset dependency: {external_assets[:2]}')
            page.close()
          results.append(item)
        browser.close()
    finally:
      srv.shutdown(); srv.server_close()
    desktop_hashes=[h for f,m,h in screenshot_hashes if m=='desktop']
    if len(set(desktop_hashes))!=len(THEMES):
      errors.append('Desktop theme screenshots are not all distinct.')
    report={'status':'PASSED' if not errors else 'FAILED','themes':results,'errors':errors}
    OUT.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))
    if errors:
      raise SystemExit(1)

if __name__=='__main__': main()
