import asyncio,json,os,re,zipfile
from pathlib import Path
from datetime import datetime,timezone,timedelta
import requests
from playwright.async_api import async_playwright
A='7666692683432429819'; UID='717344312142287'; SEC='MS4wLjABAAAASUWIr_SDLMfbpb6mbeGrx_5fKU6NG7z1msxOWgiwYwU'
O=Path('out'); (O/'作者图片评论').mkdir(parents=True,exist_ok=True); (O/'raw').mkdir(exist_ok=True)
P=[]
def ck(s):
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
def is_author(c):
 u=c.get('user') or {}; return str(u.get('uid') or '')==UID or str(u.get('sec_uid') or '')==SEC
def imgs(c):
 for k in ('image_list','images','image_comment'):
  v=c.get(k)
  if isinstance(v,list): return [x for x in v if isinstance(x,dict)]
 return []
def all_urls(x):
 z=[]
 def f(v):
  if isinstance(v,str) and v.startswith('http'): z.append(v)
  elif isinstance(v,dict):
   for q in v.values(): f(q)
  elif isinstance(v,list):
   for q in v: f(q)
 f(x); return list(dict.fromkeys(z))
def ts(v):
 try:return datetime.fromtimestamp(int(v),timezone(timedelta(hours=8))).strftime('%Y-%m-%d_%H-%M-%S')
 except:return '时间未知'
def sf(s):return (re.sub(r'[\\/:*?"<>|\r\n]+','_',s or '').strip(' ._')[:28] or '无文字')
async def main():
 L=json.load(open(os.environ['LOGIN_JSON'],encoding='utf8')); cookie=L['cookie']; ua=L['user_agent']; n=0
 async def cap(r):
  nonlocal n
  if '/aweme/v1/web/comment/list' not in r.url:return
  try:d=await r.json()
  except:return
  n+=1; P.append({'url':r.url,'data':d}); json.dump(P[-1],open(O/'raw'/f'{n:04d}.json','w',encoding='utf8'),ensure_ascii=False)
 async with async_playwright() as p:
  b=await p.chromium.launch(headless=True,args=['--no-sandbox','--disable-dev-shm-usage','--disable-blink-features=AutomationControlled'])
  c=await b.new_context(user_agent=ua,viewport={'width':1920,'height':1080},locale='zh-CN',timezone_id='Asia/Shanghai'); await c.add_cookies(ck(cookie))
  page=await c.new_page(); page.on('response',lambda r:asyncio.create_task(cap(r)))
  await page.goto(f'https://www.douyin.com/note/{A}',wait_until='domcontentloaded',timeout=120000); await page.wait_for_timeout(9000)
  for label in ('取消','以后再说','关闭'):
   try:
    q=page.get_by_text(label,exact=True)
    for i in range(min(await q.count(),5)):
     if await q.nth(i).is_visible(): await q.nth(i).click(force=True); await page.wait_for_timeout(500); break
   except:pass
  clicked=False
  cand=page.get_by_text(re.compile(r'^评论\(\d+\)$'))
  for i in range(min(await cand.count(),30)):
   try:
    q=cand.nth(i); box=await q.bounding_box()
    if box and box['x']>1300 and 170<box['y']<340:
     await page.mouse.click(box['x']+box['width']/2,box['y']+box['height']/2); clicked=True; break
   except:pass
  if not clicked: await page.mouse.click(1485,244)
  await page.wait_for_timeout(5000); await page.screenshot(path=str(O/'评论页起始.png'))
  body=await page.locator('body').inner_text(); blocked=('登录后即可查看更多评论' in body or '扫码登录' in body)
  stale=0; last=0; clicks=0
  for step in range(420):
   try:
    hit=await page.evaluate("""()=>{const re=/(展开|查看).{0,8}回复|更多回复|查看全部回复/;let n=0;for(const e of document.querySelectorAll('button,[role=button]')){const t=(e.innerText||'').trim(),r=e.getBoundingClientRect();if(t.length<40&&re.test(t)&&r.width>0&&r.height>0&&r.bottom>180&&r.top<1000){try{e.click();n++}catch{}}}return n}""")
   except:hit=0
   clicks+=hit
   await page.mouse.move(1700,820); await page.mouse.wheel(0,1500); await page.wait_for_timeout(650)
   now=len({str(c.get('cid')) for x in P for c in walk(x['data']) if c.get('cid')})
   stale=stale+1 if now<=last and hit==0 else 0; last=max(last,now)
   top=[x['data'] for x in P if '/reply/' not in x['url']]
   if step>80 and stale>35 and top and top[-1].get('has_more') in (0,False):break
   if step>160 and stale>70:break
  await page.wait_for_timeout(5000); await page.screenshot(path=str(O/'评论页结束.png')); await b.close()
 D={}
 for x in P:
  for c in walk(x['data']):
   if c.get('cid'):D[str(c['cid'])]=c
 allc=list(D.values()); ac=[c for c in allc if is_author(c)]; ai=[c for c in ac if imgs(c)]
 s=requests.Session(); s.headers.update({'user-agent':ua,'referer':f'https://www.douyin.com/note/{A}','cookie':cookie}); man=[]; no=2
 for c in sorted(ai,key=lambda x:int(x.get('create_time') or 0)):
  m={'序号':no,'评论ID':str(c.get('cid') or ''),'时间':ts(c.get('create_time')),'文字':str(c.get('text') or ''),'文件':[]}
  for j,e in enumerate(imgs(c),1):
   err='无地址'
   for u in all_urls(e):
    try:
     r=s.get(u,timeout=35); r.raise_for_status(); ct=r.headers.get('content-type','').lower(); ext='.png' if 'png' in ct else '.webp' if 'webp' in ct else '.gif' if 'gif' in ct else '.jpg'; name=f'{no:03d}_{m["时间"]}_{j:02d}_{sf(m["文字"])}{ext}'; (O/'作者图片评论'/name).write_bytes(r.content); m['文件'].append({'name':name,'bytes':len(r.content),'url':u}); break
    except Exception as ex:err=str(ex)
   else:m['文件'].append({'name':None,'error':err})
  man.append(m); no+=1
 topu={str(c.get('cid')) for x in P if '/reply/' not in x['url'] for c in (x['data'].get('comments') or []) if isinstance(c,dict) and c.get('cid')}; repu={str(c.get('cid')) for x in P if '/reply/' in x['url'] for c in (x['data'].get('comments') or []) if isinstance(c,dict) and c.get('cid')}
 st={'aweme_id':A,'login_blocked':blocked,'comments_tab_clicked':clicked,'payloads':len(P),'top_payloads':sum('/reply/' not in x['url'] for x in P),'reply_payloads':sum('/reply/' in x['url'] for x in P),'top_unique':len(topu),'reply_unique':len(repu),'all_unique':len(allc),'author_comments':len(ac),'author_image_comments':len(ai),'author_image_files':sum(bool(f.get('name')) for m in man for f in m['文件']),'reply_clicks':clicks,'api_totals':[x['data'].get('total') for x in P if '/reply/' not in x['url'] and x['data'].get('total') is not None],'top_has_more':[x['data'].get('has_more') for x in P if '/reply/' not in x['url']],'captured_at':datetime.now(timezone(timedelta(hours=8))).isoformat()}
 for name,obj in [('全部评论及回复.json',allc),('作者全部评论.json',ac),('作者图片评论清单.json',man),('抓取统计.json',st)]:json.dump(obj,open(O/name,'w',encoding='utf8'),ensure_ascii=False,indent=2)
 open(O/'000_说明.txt','w',encoding='utf8').write('登录凭证未写入本包；作者按 UID/sec_uid 精确匹配。\n'+json.dumps(st,ensure_ascii=False))
 with zipfile.ZipFile(f'douyin-login-comments-{A}.zip','w',zipfile.ZIP_DEFLATED) as z:
  for f in O.rglob('*'):
   if f.is_file():z.write(f,f)
 print(json.dumps(st,ensure_ascii=False))
asyncio.run(main())
