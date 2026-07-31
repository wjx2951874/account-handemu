import asyncio,json,os,re,zipfile
from pathlib import Path
from datetime import datetime,timezone,timedelta
from urllib.parse import urlparse
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
  if x.get('cid') and isinstance(x.get('user'),dict):
   yield x
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
 L=json.load(open(os.environ['LOGIN_JSON'],encoding='utf8')); ck=L['cookie']; ua=L['user_agent']; idx=0; tasks=set()
 async def cap(r):
  nonlocal idx
  if '/aweme/v1/web/comment/list' not in r.url:return
  try:d=await r.json()
  except:return
  idx+=1; P.append({'url':r.url,'data':d}); json.dump(P[-1],open(O/'raw'/f'{idx:04d}.json','w',encoding='utf8'),ensure_ascii=False)
 def onresp(r):
  t=asyncio.create_task(cap(r)); tasks.add(t); t.add_done_callback(tasks.discard)
 async with async_playwright() as p:
  b=await p.chromium.launch(headless=True,args=['--no-sandbox','--disable-dev-shm-usage','--disable-blink-features=AutomationControlled'])
  c=await b.new_context(user_agent=ua,viewport={'width':1920,'height':1080},locale='zh-CN',timezone_id='Asia/Shanghai')
  await c.add_cookies(cookies(ck)); page=await c.new_page(); page.on('response',onresp)
  await page.goto(f'https://www.douyin.com/note/{A}',wait_until='domcontentloaded',timeout=120000); await page.wait_for_timeout(12000)
  await page.screenshot(path=str(O/'登录页面.png'))
  body=''
  try:body=await page.locator('body').inner_text()
  except:pass
  blocked=any(x in body for x in ('扫码登录','登录后即可查看更多评论','验证码登录'))
  pat=re.compile(r'(展开\s*\d*\s*条?回复|查看\s*\d*\s*条?回复|更多回复|查看全部回复|展开更多回复|查看更多回复)'); stale=0; last=0; clicks=[]
  for step in range(720):
   hit=0; bs=page.locator('button'); n=min(await bs.count(),900)
   for i in range(n):
    q=bs.nth(i)
    try:
     if await q.is_visible():
      t=(await q.inner_text(timeout=400)).strip()
      if pat.search(t): await q.click(force=True,timeout=1000); hit+=1; clicks.append([step,t]); await page.wait_for_timeout(180)
    except:pass
   try:
    await page.evaluate("""()=>{let a=[...document.querySelectorAll('*')].filter(e=>{let s=getComputedStyle(e),r=e.getBoundingClientRect();return r.height>160&&r.width>220&&r.bottom>0&&r.top<innerHeight&&/(auto|scroll)/.test(s.overflowY)&&e.scrollHeight>e.clientHeight+80}).sort((a,b)=>b.clientHeight*b.clientWidth-a.clientHeight*a.clientWidth);a.slice(0,12).forEach(e=>e.scrollBy(0,Math.max(650,e.clientHeight*.82)));window.scrollBy(0,900)}""")
   except: await page.mouse.wheel(0,1500)
   await page.wait_for_timeout(600)
   now=len({str(c.get('cid')) for x in P for c in walk(x['data']) if c.get('cid')})
   stale=stale+1 if now<=last and hit==0 else 0; last=max(last,now)
   if step>180 and stale>=65:break
  await page.wait_for_timeout(5000); await page.screenshot(path=str(O/'抓取结束.png')); json.dump(clicks,open(O/'点击记录.json','w',encoding='utf8'),ensure_ascii=False)
  if tasks: await asyncio.gather(*list(tasks),return_exceptions=True)
  bc=await c.cookies(); await b.close()
 D={}
 for x in P:
  for c in walk(x['data']):
   if c.get('cid'):D[str(c['cid'])]=c
 allc=list(D.values()); ac=[c for c in allc if author(c)]; ai=[c for c in ac if entries(c)]
 ses=requests.Session(); ses.headers.update({'user-agent':ua,'referer':f'https://www.douyin.com/note/{A}'})
 for c in bc:
  try:ses.cookies.set(c['name'],c['value'],domain=c.get('domain'))
  except:pass
 man=[]; no=2
 for c in sorted(ai,key=lambda x:int(x.get('create_time') or 0)):
  item={'序号':no,'评论ID':str(c.get('cid') or ''),'时间':stamp(c.get('create_time')),'文字':str(c.get('text') or ''),'文件':[]}
  for j,e in enumerate(entries(c),1):
   ok=False
   for u in urls(e):
    try:
     r=ses.get(u,timeout=60); r.raise_for_status()
     ct=r.headers.get('content-type','').lower(); ext='.png' if 'png' in ct else '.webp' if 'webp' in ct else '.gif' if 'gif' in ct else '.jpg'
     name=f'{no:03d}_{item["时间"]}_{j:02d}_{safe(item["文字"])}{ext}'; (O/'作者图片评论'/name).write_bytes(r.content); item['文件'].append({'name':name,'bytes':len(r.content),'url':u}); ok=True; break
    except Exception as ex:err=str(ex)
   if not ok:item['文件'].append({'name':None,'error':err if 'err' in locals() else 'no url'})
  man.append(item); no+=1
 tops={str(c.get('cid')) for x in P if '/reply/' not in x['url'] for c in (x['data'].get('comments') or []) if isinstance(c,dict) and c.get('cid')}
 reps={str(c.get('cid')) for x in P if '/reply/' in x['url'] for c in (x['data'].get('comments') or []) if isinstance(c,dict) and c.get('cid')}
 st={'aweme_id':A,'login_blocked':blocked,'payloads':len(P),'top_unique':len(tops),'reply_unique':len(reps),'all_unique':len(allc),'author_comments':len(ac),'author_image_comments':len(ai),'author_image_files':sum(bool(f.get('name')) for m in man for f in m['文件']),'api_totals':[x['data'].get('total') for x in P if '/reply/' not in x['url'] and x['data'].get('total') is not None],'captured_at':datetime.now(timezone(timedelta(hours=8))).isoformat()}
 for n,v in [('全部评论及回复.json',allc),('作者全部评论.json',ac),('作者图片评论清单.json',man),('抓取统计.json',st)]:json.dump(v,open(O/n,'w',encoding='utf8'),ensure_ascii=False,indent=2)
 open(O/'000_说明.txt','w',encoding='utf8').write('登录凭证未写入本包。作者仅按 UID/sec_uid 精确匹配。\n'+json.dumps(st,ensure_ascii=False))
 with zipfile.ZipFile(f'douyin-login-comments-{A}.zip','w',zipfile.ZIP_DEFLATED) as z:
  for f in O.rglob('*'):
   if f.is_file():z.write(f,f)
 print(json.dumps(st,ensure_ascii=False))
asyncio.run(main())
