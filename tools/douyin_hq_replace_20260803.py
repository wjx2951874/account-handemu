#!/usr/bin/env python3
import json, os, re, shutil, subprocess, sys, time, hashlib
from pathlib import Path
import requests
from playwright.sync_api import sync_playwright

TARGET_UID='141208157956367'
SEC_UID='MS4wLjABAAAA2XKcRLgWFfEHQ8HPVKuA5W6VKgyaImM9tHPX_wDSVpk'
PROFILE_URL=f'https://www.douyin.com/user/{SEC_UID}'
BASE=Path('baseline')
OUT=Path('hq_output')
REPORT=Path('hq_report')
COOKIE_FILE=Path('.private/cookie.txt')
UA='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36'

def parse_cookie(s):
    out=[]
    for part in s.strip().split(';'):
        if '=' not in part: continue
        k,v=part.strip().split('=',1)
        if k: out.append({'name':k,'value':v,'domain':'.douyin.com','path':'/','secure':True})
    return out

def fetch_fresh_works(cookie_text):
    captured=[]; seen=set()
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,args=['--disable-blink-features=AutomationControlled'])
        ctx=browser.new_context(user_agent=UA,locale='zh-CN',viewport={'width':1920,'height':1080})
        ctx.add_cookies(parse_cookie(cookie_text)); page=ctx.new_page()
        def on_response(resp):
            if '/aweme/v1/web/aweme/post/' not in resp.url: return
            try: obj=resp.json()
            except Exception: return
            key=(resp.url,json.dumps(obj,ensure_ascii=False,sort_keys=True)[:500])
            if key not in seen: seen.add(key); captured.append(obj)
        page.on('response',on_response)
        page.goto(PROFILE_URL,wait_until='domcontentloaded',timeout=120000); page.wait_for_timeout(8000)
        stable=0; last=0
        for _ in range(100):
            page.mouse.wheel(0,7000); page.wait_for_timeout(1200)
            count=sum(len(x.get('aweme_list') or []) for x in captured if isinstance(x,dict))
            if count==last: stable+=1
            else: stable=0; last=count
            if captured and any(x.get('has_more') in (0,False) for x in captured if isinstance(x,dict)) and stable>=4: break
            if stable>=12: break
        state=ctx.storage_state(); REPORT.mkdir(parents=True,exist_ok=True)
        (REPORT/'browser_state_summary.json').write_text(json.dumps({'captured_responses':len(captured),'captured_items':sum(len(x.get('aweme_list') or []) for x in captured if isinstance(x,dict)),'url':page.url,'cookie_names':[c['name'] for c in state.get('cookies',[])]},ensure_ascii=False,indent=2))
        browser.close()
    works={}
    for obj in captured:
        for a in obj.get('aweme_list') or []:
            aid=str(a.get('aweme_id') or '')
            if aid: works[aid]=a
    if not works: raise RuntimeError('未截获作品接口，可能出现登录或验证拦截')
    return list(works.values()),captured

def addr_urls(addr):
    if not isinstance(addr,dict): return []
    return [u for u in (addr.get('url_list') or addr.get('urlList') or []) if isinstance(u,str) and u.startswith('http')]

def dim(obj,key):
    try: return int(obj.get(key) or 0)
    except Exception: return 0

def candidates(aweme):
    video=aweme.get('video') or {}; out=[]
    def add(source,addr,br=0,gear='',quality=None):
        if not isinstance(addr,dict): return
        urls=addr_urls(addr)
        if not urls: return
        out.append({'source':source,'urls':urls,'width':dim(addr,'width') or dim(video,'width'),'height':dim(addr,'height') or dim(video,'height'),'bit_rate':int(br or 0),'data_size':dim(addr,'data_size') or dim(addr,'dataSize'),'gear_name':str(gear or ''),'quality_type':quality})
    for item in video.get('bit_rate') or video.get('bitRate') or []:
        if not isinstance(item,dict): continue
        br=item.get('bit_rate') or item.get('bitRate') or 0; gear=item.get('gear_name') or item.get('gearName') or ''; quality=item.get('quality_type') or item.get('qualityType')
        for k in ('play_addr','play_addr_265','play_addr_h264','play_addr_bytevc1','play_addr_lowbr'): add('bit_rate.'+k,item.get(k),br,gear,quality)
    for k in ('play_addr','play_addr_265','play_addr_h264','play_addr_bytevc1'): add('video.'+k,video.get(k))
    uniq=[]; keys=set()
    for c in out:
        k=(tuple(c['urls']),c['width'],c['height'])
        if k not in keys: keys.add(k); uniq.append(c)
    return sorted(uniq,key=lambda c:(c['width']*c['height'],c['bit_rate'],c['data_size']),reverse=True)

def probe(path):
    cmd=['ffprobe','-v','error','-select_streams','v:0','-show_entries','stream=codec_name,width,height,bit_rate','-show_entries','format=duration,size,bit_rate','-of','json',str(path)]
    p=subprocess.run(cmd,capture_output=True,text=True,timeout=60)
    if p.returncode: return {'ok':False,'error':p.stderr[-500:]}
    obj=json.loads(p.stdout); st=(obj.get('streams') or [{}])[0]; fm=obj.get('format') or {}
    return {'ok':True,'codec':st.get('codec_name'),'width':int(st.get('width') or 0),'height':int(st.get('height') or 0),'video_bit_rate':int(st.get('bit_rate') or 0),'format_bit_rate':int(fm.get('bit_rate') or 0),'duration':float(fm.get('duration') or 0),'size':int(fm.get('size') or path.stat().st_size)}

def download(session,url,dest,cookie_text):
    headers={'User-Agent':UA,'Referer':'https://www.douyin.com/','Origin':'https://www.douyin.com','Accept':'*/*','Cookie':cookie_text}
    with session.get(url,headers=headers,stream=True,timeout=(20,180),allow_redirects=True) as r:
        r.raise_for_status(); ct=(r.headers.get('content-type') or '').lower()
        with dest.open('wb') as f:
            for chunk in r.iter_content(1024*1024):
                if chunk: f.write(chunk)
        if dest.stat().st_size<5000: raise RuntimeError(f'文件过小 {dest.stat().st_size}, content-type={ct}')
        return {'status':r.status_code,'final_url':r.url,'content_type':ct,'content_length':r.headers.get('content-length')}

def main():
    REPORT.mkdir(parents=True,exist_ok=True); OUT.mkdir(parents=True,exist_ok=True)
    cookie_text=COOKIE_FILE.read_text().strip()
    manifest=json.loads((BASE/'归档清单.json').read_text()); old_list=json.loads((BASE/'全部作品.json').read_text())
    old={str(a['aweme_id']):a for a in old_list}; folders={str(r['id']):r['folder'] for r in manifest.get('records',[])}
    fresh,captured=fetch_fresh_works(cookie_text); (REPORT/'fresh_responses.json').write_text(json.dumps(captured,ensure_ascii=False))
    exact={str(a.get('aweme_id')):a for a in fresh if str((a.get('author') or {}).get('uid'))==TARGET_UID}
    targets=[]
    for aid,aold in old.items():
        if str((aold.get('author') or {}).get('uid'))!=TARGET_UID: continue
        oldbest=aold.get('best_video') or {}; old_area=int(oldbest.get('width') or 0)*int(oldbest.get('height') or 0); cold=aold.get('video_candidates') or []
        max_old=max((int(c.get('width') or 0)*int(c.get('height') or 0) for c in cold),default=0)
        if max_old>old_area: targets.append({'id':aid,'folder':folders.get(aid),'old_width':oldbest.get('width'),'old_height':oldbest.get('height'),'expected_area':max_old})
    session=requests.Session(); results=[]
    for idx,t in enumerate(targets,1):
        aid=t['id']; rec=dict(t); rec['index']=idx; aw=exact.get(aid)
        if not aw: rec.update(status='failed',error='fresh profile missing exact-author work'); results.append(rec); continue
        cs=candidates(aw); rec['candidate_count']=len(cs); rec['candidate_summary']=[{k:c[k] for k in ('source','width','height','bit_rate','data_size','gear_name')} for c in cs]
        if not cs: rec.update(status='failed',error='no fresh video candidates'); results.append(rec); continue
        tmpdir=Path('tmp_downloads')/aid; tmpdir.mkdir(parents=True,exist_ok=True); chosen=None; errors=[]; max_claim=max(c['width']*c['height'] for c in cs)
        for ci,c in enumerate(cs):
            claim_area=c['width']*c['height']
            if chosen and claim_area<chosen['probe']['width']*chosen['probe']['height']: break
            for ui,url in enumerate(c['urls']):
                dest=tmpdir/f'{ci:02d}_{ui:02d}.mp4'
                try:
                    meta=download(session,url,dest,cookie_text); pr=probe(dest)
                    if not pr.get('ok') or not pr.get('width') or not pr.get('height'): raise RuntimeError('ffprobe failed '+str(pr))
                    actual_area=pr['width']*pr['height']; cand={'candidate':c,'download':meta,'probe':pr,'path':str(dest)}
                    if chosen is None or (actual_area,pr.get('format_bit_rate',0),pr.get('size',0))>(chosen['probe']['width']*chosen['probe']['height'],chosen['probe'].get('format_bit_rate',0),chosen['probe'].get('size',0)): chosen=cand
                    if actual_area>=max_claim: break
                except Exception as e:
                    errors.append({'candidate':ci,'url':ui,'error':type(e).__name__+': '+str(e)[:400]})
                    if dest.exists(): dest.unlink()
            if chosen and chosen['probe']['width']*chosen['probe']['height']>=max_claim: break
        if not chosen: rec.update(status='failed',error='all candidates failed',errors=errors); results.append(rec); continue
        actual_area=chosen['probe']['width']*chosen['probe']['height']
        if actual_area<=int(t['old_width'] or 0)*int(t['old_height'] or 0): rec.update(status='failed',error='downloaded file not higher than old resolution',chosen=chosen,errors=errors); results.append(rec); continue
        folder=t['folder'] or aid; finaldir=OUT/folder; finaldir.mkdir(parents=True,exist_ok=True); final=finaldir/f'{aid}.mp4'; shutil.copy2(chosen['path'],final)
        sha=hashlib.sha256(final.read_bytes()).hexdigest(); rec.update(status='success',new_width=chosen['probe']['width'],new_height=chosen['probe']['height'],codec=chosen['probe']['codec'],size=final.stat().st_size,sha256=sha,source=chosen['candidate']['source'],gear_name=chosen['candidate']['gear_name'],errors=errors); results.append(rec)
        print(f'[{idx}/{len(targets)}] {aid}: {rec["old_width"]}x{rec["old_height"]} -> {rec["new_width"]}x{rec["new_height"]}',flush=True)
    summary={'target_uid':TARGET_UID,'fresh_exact_author_count':len(exact),'target_count':len(targets),'success_count':sum(r['status']=='success' for r in results),'failed_count':sum(r['status']!='success' for r in results),'results':results}
    (REPORT/'替换报告.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)); (REPORT/'上传映射.json').write_text(json.dumps([{'id':r['id'],'folder':r['folder'],'local':f"hq_output/{r['folder']}/{r['id']}.mp4",'width':r.get('new_width'),'height':r.get('new_height'),'sha256':r.get('sha256'),'size':r.get('size')} for r in results if r['status']=='success'],ensure_ascii=False,indent=2))
    print(json.dumps({k:summary[k] for k in ('fresh_exact_author_count','target_count','success_count','failed_count')},ensure_ascii=False,indent=2))
    if summary['failed_count']: sys.exit(2)

if __name__=='__main__': main()
