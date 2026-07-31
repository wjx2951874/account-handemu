import asyncio,json,os,re,zipfile
from pathlib import Path
from datetime import datetime,timezone,timedelta
import requests
from playwright.async_api import async_playwright
A='7666692683432429819'; UID='717344312142287'; SEC='MS4wLjABAAAASUWIr_SDLMfbpb6mbeGrx_5fKU6NG7z1msxOWgiwYwU'
O=Path('out'); O.mkdir(exist_ok=True); (O/'作者图片评论').mkdir(exist_ok=True); (O/'raw').mkdir(exist_ok=True)
P=[]
def cookies(s):
 r=[]
 for x in s.split(';'):
  if '=' in x:
   n,v=x.strip().split('=',1); r.append({'name':n,'value':v,'domain':'.douyin.com','path':'/','secure':True,'sameSite':'Lax'})
 return r
def walk(x):
 if isinstance(x,dict):
  if x.get('cid') and isinstance(x.get('user'),dict): yield x
  for v in x.values(): yield from walk(v)
 elif isinstance(x,list):
  for v in x: yield from walk(v)
def author(c):
 u=c.get('user') or {}; return str(u.get('uid') or '')==UID or str(u.get('sec_uid') or '')==SEC
def entries(c):
 for k in ('image_list','images','image_comment'):
  v=c.get(k)
  if isinstance(v,list): return [x for x in v if isinstance(x,dict)]
 return []
def urls(x):
 z=[]
 def f(v):
  if isinstance(v,str) and v.startswith('http'): z.append(v)
  elif isinstance(v,dict):
   for q in v.values(): f(q)
  elif isinstance(v,list):
   for q in v: f(q)
 f(x); return list(dict.fromkeys(z))
def stamp(t):
 try:return datetime.fromtimestamp(int(t),timezone(timedelta(hours=8))).strftime('%Y-%m-%d_%H-%M-%S')
 except:return '时间未知'
def safe(s): return (re.sub(r'[\\/:*?"<>|\r\n]+','_',s or '').strip(' ._')[:30] or '无文字')
async def main():
 L=json.load(open(os.environ['LOGIN_JSON'],encoding='utf8')); ck=L['cookie']; ua=L['user_agent']; idx=0
 async def cap(r):
  nonlocal idx
  if '/aweme/v1/web/comment/list' not in r.url:return
  try:d=await r.json()
  except:return
  idx+=1; P.append({'url':r.url,'data':d}); json.dump(P[-1],open(O/'raw'/f'{idx:04d}.json','w',encoding='utf8'),ensure_ascii=False)
 async with async_playwright() as p:
  b=await p.chromium.launch(headless=True,args=['--no-sandbox','--disable-dev-shm-usage','--disable-blink-features=AutomationControlled'])
  c=await b.new_context(user_agent=ua,viewport={'width':1920,'height':1080},locale='zh-CN',timezone_id='Asia/Shanghai')
  await c.add_cookies(cookies(ck)); page=await c.new_page(); page.on('response',lambda r:asyncio.create_task(cap(r)))
  await page.goto(f'https://www.douyin.com/note/{A}',wait_until='domcontentloaded',timeout=120000); await page.wait_for_timeout(8000)
  for label in ('取消','保存'):
   try:
    q=page.get_by_text(label,exact=True)
    for i in range(min(await q.count(),5)):
     if await q.nth(i).is_visible(): await q.nth(i).click(force=True); await page.wait_for_timeout(800); raise StopIteration
   except StopIteration: break
   except: pass
  clicked_tab=False
  for sel in (r'^评论\(\d+\)$',r'^评论\s*\d+$',r'^评论$'):
   try:
    q=page.get_by_text(re.compile(sel))
    for i in range(min(await q.count(),20)):
     if await q.nth(i).is_visible(): await q.nth(i).click(force=True); clicked_tab=True; break
    if clicked_tab: break
   except: pass
  await page.wait_for_timeout(5000); await page.screenshot(path=str(O/'评论面板起始.png'))
  body=''
  try:body=await page.locator('body').inner_text()
  except:pass
  blocked=any(x in body for x in ('扫码登录','登录后即可查看更多评论','验证码登录'))
  stale=0; last=0; click_log=[]; scroll_log=[]
  for step in range(700):
   try:
    hit=await page.evaluate("""()=>{const re=/^(展开\s*\d*\s*条?回复|查看\s*\d*\s*条?回复|展开更多回复|查看更多回复|更多回复|查看全部回复)$/;let n=0;for(const e of document.querySelectorAll('button,[role=button],span,div')){const t=(e.innerText||'').trim(),r=e.getBoundingClientRect(),s=getComputedStyle(e);if(t.length<40&&re.test(t)&&r.width>0&&r.height>0&&r.bottom>0&&r.top<innerHeight&&s.visibility!=='hidden'&&s.display!=='none'){try{e.click();n++}catch{}}}return n}""")
   except: hit=0
   if hit: click_log.append([step,hit])
   try:
    sc=await page.evaluate("""()=>{let a=[...document.querySelectorAll('*')].filter(e=>{let s=getComputedStyle(e),r=e.getBoundingClientRect();return r.left>1050&&r.height>220&&r.width>250&&r.bottom>0&&r.top<innerHeight&&/(auto|scroll)/.test(s.overflowY)&&e.scrollHeight>e.clientHeight+100}).sort((a,b)=>b.clientHeight*b.clientWidth-a.clientHeight*a.clientWidth);if(a.length){let e=a[0],before=e.scrollTop;e.scrollBy(0,Math.max(700,e.clientHeight*.8));return {found:true,before,after:e.scrollTop,sh:e.scrollHeight,ch:e.clientHeight,cls:String(e.className).slice(0,120)}}return {found:false}}""")
   except: sc={'found':False}
   if not sc.get('found'):
    try: await page.mouse.move(1700,850); await page.mouse.wheel(0,1200)
    except: pass
   if step%20==0: scroll_log.append([step,sc])
   await page.wait_for_timeout(650)
   now=len({str(c.get('cid')) for x in P for c in walk(x['data']) if c.get('cid')})
   top_more=[x['data'].get('has_more') for x in P if '/reply/' not in x['url']]
   stale=stale+1 if now<=last and not hit else 0; last=max(last,now)
   if step>100 and stale>=35 and top_more and top_more[-1] in (0,False,None): break
   if step>180 and stale>=70: break
  await page.wait_for_timeout(5000); await page.screenshot(path=str(O/'抓取结束.png')); json.dump({'tab_clicked':clicked_tab,'clicks':click_log,'scroll':scroll_log},open(O/'交互记录.json','w',encoding='utf8'),ensure_ascii=False,indent=2); await b.close()
 D={}
 for x in P:
  for c in walk(x['data']):
   if c.get('cid'):D[str(c['cid'])]=c
 allc=list(D.values()); ac=[c for c in allc if author(c)]; ai=[c for c in ac if entries(c)]
 ses=requests.Session(); ses.headers.update({'user-agent':ua,'referer':f'https://www.douyin.com/note/{A}','cookie':ck})
 man=[]; no=2
 for c in sorted(ai,key=lambda x:int(x.get('create_time') or 0)):
  item={'序号':no,'评论ID':str(c.get('cid') or ''),'时间':stamp(c.get('create_time')),'文字':str(c.get('text') or ''),'文件':[]}
  for j,e in enumerate(entries(c),1):
   ok=False; err='no url'
   for u in urls(e):
    try:
     r=ses.get(u,timeout=35); r.raise_for_status(); ct=r.headers.get('content-type','').lower(); ext='.png' if 'png' in ct else '.webp' if 'webp' in ct else '.gif' if 'gif' in ct else '.jpg'; name=f'{no:03d}_{item["时间"]}_{j:02d}_{safe(item["文字"])}{ext}'; (O/'作者图片评论'/name).write_bytes(r.content); item['文件'].append({'name':name,'bytes':len(r.content),'url':u}); ok=True; break
    except Exception as ex: err=str(ex)
   if not ok:item['文件'].append({'name':None,'error':err})
  man.append(item); no+=1
 tops={str(c.get('cid')) for x in P if '/reply/' not in x['url'] for c in (x['data'].get('comments') or []) if isinstance(c,dict) and c.get('cid')}; reps={str(c.get('cid')) for x in P if '/reply/' in x['url'] for c in (x['data'].get('comments') or []) if isinstance(c,dict) and c.get('cid')}
 st={'aweme_id':A,'login_blocked':blocked,'comments_tab_clicked':clicked_tab,'payloads':len(P),'top_payloads':sum('/reply/' not in x['url'] for x in P),'reply_payloads':sum('/reply/' in x['url'] for x in P),'top_unique':len(tops),'reply_unique':len(reps),'all_unique':len(allc),'author_comments':len(ac),'author_image_comments':len(ai),'author_image_files':sum(bool(f.get('name')) for m in man for f in m['文件']),'api_totals':[x['data'].get('total') for x in P if '/reply/' not in x['url'] and x['data'].get('total') is not None],'top_has_more':[x['data'].get('has_more') for x in P if '/reply/' not in x['url']],'captured_at':datetime.now(timezone(timedelta(hours=8))).isoformat()}
 for n,v in [('全部评论及回复.json',allc),('作者全部评论.json',ac),('作者图片评论清单.json',man),('抓取统计.json',st)]:json.dump(v,open(O/n,'w',encoding='utf8'),ensure_ascii=False,indent=2)
 open(O/'000_说明.txt','w',encoding='utf8').write('登录凭证未写入本包。作者仅按 UID/sec_uid 精确匹配。\n'+json.dumps(st,ensure_ascii=False))
 with zipfile.ZipFile(f'douyin-login-comments-{A}.zip','w',zipfile.ZIP_DEFLATED) as z:
  for f in O.rglob('*'):
   if f.is_file():z.write(f,f)
 print(json.dumps(st,ensure_ascii=False))
asyncio.run(main())
