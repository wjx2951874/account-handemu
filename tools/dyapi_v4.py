import json,os,time,zipfile,re
from pathlib import Path
from urllib.parse import urlparse,parse_qsl,urlencode,urlunparse
from datetime import datetime,timezone,timedelta
import requests
A='7666692683432429819'; UID='717344312142287'; SEC='MS4wLjABAAAASUWIr_SDLMfbpb6mbeGrx_5fKU6NG7z1msxOWgiwYwU'
O=Path('out'); (O/'作者图片评论').mkdir(parents=True,exist_ok=True); (O/'raw').mkdir(exist_ok=True)
L=json.load(open(os.environ['REQ_JSON'],encoding='utf8')); url=L['url']; h=L['headers']; h['cookie']=L['cookie']; S=requests.Session(); S.headers.update(h)
def setq(u,**kw):
 p=urlparse(u); q=dict(parse_qsl(p.query,keep_blank_values=True))
 for k,v in kw.items():
  if v is None:q.pop(k,None)
  else:q[k]=str(v)
 return urlunparse((p.scheme,p.netloc,p.path,p.params,urlencode(q),p.fragment))
def get(u):
 try:
  r=S.get(u,timeout=35); txt=r.text; d=r.json() if txt else {}
  return r.status_code,d,txt[:400]
 except Exception as e:return 0,{},str(e)
def walk(x):
 if isinstance(x,dict):
  if x.get('cid') and isinstance(x.get('user'),dict):yield x
  for v in x.values():yield from walk(v)
 elif isinstance(x,list):
  for v in x:yield from walk(v)
def auth(c):
 u=c.get('user') or {};return str(u.get('uid') or '')==UID or str(u.get('sec_uid') or '')==SEC
def imgs(c):
 for k in ('image_list','images','image_comment'):
  if isinstance(c.get(k),list):return [x for x in c[k] if isinstance(x,dict)]
 return []
def urls(x):
 z=[]
 def f(v):
  if isinstance(v,str) and v.startswith('http'):z.append(v)
  elif isinstance(v,dict):
   for a in v.values():f(a)
  elif isinstance(v,list):
   for a in v:f(a)
 f(x);return list(dict.fromkeys(z))
def ts(v):
 try:return datetime.fromtimestamp(int(v),timezone(timedelta(hours=8))).strftime('%Y-%m-%d_%H-%M-%S')
 except:return '时间未知'
def sf(s):return (re.sub(r'[\\/:*?"<>|\r\n]+','_',s or '').strip(' ._')[:28] or '无文字')
diag=[]; payloads=[]
code,d,head=get(url); diag.append({'case':'exact','code':code,'keys':list(d)[:20],'comments':len(d.get('comments') or []),'cursor':d.get('cursor'),'has_more':d.get('has_more'),'head':head})
if d:payloads.append({'url':url,'data':d})
for mode in ('same_signature','unsigned'):
 got=[]
 for cur in range(0,501,10):
  u=setq(url,cursor=cur,count=10)
  if mode=='unsigned':u=setq(u,a_bogus=None)
  c,x,hd=get(u); cs=(x.get('comments') or []) if isinstance(x,dict) else []
  diag.append({'case':mode,'cursor_req':cur,'code':c,'status_code':x.get('status_code') if isinstance(x,dict) else None,'comments':len(cs),'cursor':x.get('cursor') if isinstance(x,dict) else None,'has_more':x.get('has_more') if isinstance(x,dict) else None,'head':hd})
  if not cs:
   if cur==0:continue
   break
  got.append({'url':u,'data':x}); time.sleep(.25)
  if x.get('has_more') in (0,False):break
 if len(got)>len(payloads):payloads=got
 if len(got)>=20:break
D={}
for p in payloads:
 for c in walk(p['data']):
  if c.get('cid'):D[str(c['cid'])]=c
base=urlparse(setq(url,a_bogus=None)); bq=dict(parse_qsl(base.query,keep_blank_values=True))
for k in ('aweme_id','insert_ids','whale_cut_token','cut_version','rcFT'):bq.pop(k,None)
reply_payloads=[]
for c in list(D.values()):
 total=int(c.get('reply_comment_total') or 0); embedded=len(c.get('reply_comment') or [])
 if total<=embedded:continue
 cur=0
 for _ in range(30):
  q=dict(bq);q.update({'item_id':A,'comment_id':str(c.get('cid')),'cursor':cur,'count':20,'item_type':0})
  ru=urlunparse((base.scheme,base.netloc,'/aweme/v1/web/comment/list/reply/','',urlencode(q),''))
  rc,rd,rh=get(ru); arr=(rd.get('comments') or []) if isinstance(rd,dict) else []
  diag.append({'case':'reply_unsigned','comment_id':str(c.get('cid')),'cursor_req':cur,'code':rc,'status_code':rd.get('status_code') if isinstance(rd,dict) else None,'comments':len(arr),'cursor':rd.get('cursor') if isinstance(rd,dict) else None,'has_more':rd.get('has_more') if isinstance(rd,dict) else None,'head':rh})
  if not arr:break
  reply_payloads.append({'url':ru,'data':rd})
  for z in walk(rd):
   if z.get('cid'):D[str(z['cid'])]=z
  if rd.get('has_more') in (0,False):break
  nxt=int(rd.get('cursor') or 0)
  if nxt==cur:break
  cur=nxt;time.sleep(.2)
allc=list(D.values()); ac=[c for c in allc if auth(c)]; ai=[c for c in ac if imgs(c)]
man=[];no=2
for c in sorted(ai,key=lambda x:int(x.get('create_time') or 0)):
 m={'序号':no,'评论ID':str(c.get('cid') or ''),'时间':ts(c.get('create_time')),'文字':str(c.get('text') or ''),'文件':[]}
 for j,e in enumerate(imgs(c),1):
  err='无地址'
  for u in urls(e):
   try:
    r=S.get(u,timeout=35);r.raise_for_status();ct=r.headers.get('content-type','').lower();ext='.png' if 'png' in ct else '.webp' if 'webp' in ct else '.gif' if 'gif' in ct else '.jpg';name=f'{no:03d}_{m["时间"]}_{j:02d}_{sf(m["文字"])}{ext}';(O/'作者图片评论'/name).write_bytes(r.content);m['文件'].append({'name':name,'bytes':len(r.content),'url':u});break
   except Exception as ex:err=str(ex)
  else:m['文件'].append({'name':None,'error':err})
 man.append(m);no+=1
st={'exact_comments':diag[0]['comments'],'top_payloads':len(payloads),'reply_payloads':len(reply_payloads),'all_unique':len(allc),'author_comments':len(ac),'author_image_comments':len(ai),'author_image_files':sum(bool(f.get('name')) for m in man for f in m['文件']),'api_totals':list(dict.fromkeys([p['data'].get('total') for p in payloads if p['data'].get('total') is not None]))}
for n,o in [('诊断.json',diag),('全部评论及回复.json',allc),('作者全部评论.json',ac),('作者图片评论清单.json',man),('抓取统计.json',st)]:json.dump(o,open(O/n,'w',encoding='utf8'),ensure_ascii=False,indent=2)
with zipfile.ZipFile(f'douyin-api-v4-{A}.zip','w',zipfile.ZIP_DEFLATED) as z:
 for f in O.rglob('*'):
  if f.is_file():z.write(f,f)
print(json.dumps(st,ensure_ascii=False))
