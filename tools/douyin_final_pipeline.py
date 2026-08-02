from pathlib import Path
from datetime import datetime,timezone,timedelta
from urllib.parse import urlparse
import html,json,shlex,shutil,subprocess,sys
import yaml
from weasyprint import HTML

TZ=timezone(timedelta(hours=8)); ROOT=Path('final_run'); OUT=ROOT/'lyx520'; RAW=Path('raw/Downloaded'); V=Path('vendor/douyin-downloader')
for p in (ROOT,OUT): p.mkdir(parents=True,exist_ok=True)
VE={'.mp4','.mov','.m4v','.webm','.mkv','.flv','.avi','.ts'}; IE={'.jpg','.jpeg','.png','.webp','.gif','.avif','.bmp','.heic'}

def parse_curl(s):
 t=shlex.split(s); h={}; c=''; i=2
 while i<len(t):
  if t[i] in ('-H','--header') and i+1<len(t):
   x=t[i+1]
   if ':' in x: k,v=x.split(':',1); h[k.strip().lower()]=v.strip()
   i+=2; continue
  if t[i] in ('-b','--cookie') and i+1<len(t): c=t[i+1]; i+=2; continue
  i+=1
 if 'sessionid=' not in c: raise RuntimeError('Cookie无sessionid')
 return h,c

def cmap(c): return dict(x.strip().split('=',1) for x in c.split(';') if '=' in x)

def ft(ts,sec=True):
 n=int(ts or 0); n=n//1000 if n>10_000_000_000 else n; d=datetime.fromtimestamp(n,TZ)
 return (f'{d.year}年{d.month}月{d.day}日 {d:%H:%M:%S}' if sec else f'{d.year}年{d.month}月{d.day}日 {d:%H:%M}')

def urls(v):
 o=[]
 if isinstance(v,str) and v.startswith('http'): o.append(v)
 elif isinstance(v,list):
  for x in v:o+=urls(x)
 elif isinstance(v,dict):
  for k in ('url_list','urls','url','download_url_list'):
   if k in v:o+=urls(v[k])
 return list(dict.fromkeys(o))

def imgpost(w):
 r=w.get('raw') or {}; a=r.get('images') or (r.get('image_post_info') or {}).get('images') or []
 return bool(a)

def roots(d):
 if isinstance(d,list): return [x for x in d if isinstance(x,dict)]
 if isinstance(d,dict):
  for k in ('comments','comment_list','data','items'):
   if k in d:
    r=roots(d[k])
    if r:return r
 return []

def replies(c):
 o=[]
 for k in ('reply_comment','reply_comments','replies','reply_list'):
  if isinstance(c.get(k),list):o += [x for x in c[k] if isinstance(x,dict)]
 seen=set(); r=[]
 for x in o:
  q=str(x.get('cid') or x.get('comment_id') or x.get('id') or json.dumps(x,ensure_ascii=False)[:150])
  if q not in seen:seen.add(q);r.append(x)
 return r

def cimgs(c):
 o=[]
 for k in ('image_list','images','image_comment'):
  for x in c.get(k) or []:
   if isinstance(x,dict):
    for q in ('origin_url','download_url','large_url','medium_url','url','thumb_url'):
     z=urls(x.get(q))
     if z:o.append(z[0]);break
 return list(dict.fromkeys(o))

def card(c,level):
 u=c.get('user') or {}; name=html.escape(str(u.get('nickname') or u.get('unique_id') or '未知用户')); text=html.escape(str(c.get('text') or c.get('content') or '（无文字）')).replace('\n','<br>')
 meta=f"{ft(c.get('create_time'))} · 点赞：{c.get('digg_count') or 0}"; ims=''.join(f'<img src="{html.escape(x)}">' for x in cimgs(c)); cl='reply' if level else 'comment'
 return f'<div class="{cl}"><b>{name}</b><div class="meta">{meta}</div><div>{text}</div>{ims}</div>'

def pdf(a,w,data,dst):
 rs=roots(data); blocks=[]; total=0; rep=0
 def add(c,l=0):
  nonlocal total,rep; total+=1; rep+=bool(l); blocks.append(card(c,l))
  for x in replies(c):add(x,l+1)
 for x in rs:add(x)
 body=''.join(blocks) or '<p class="empty">当前抓取时未获取到公开评论。</p>'; desc=html.escape(str(w.get('desc') or '（无简介）')).replace('\n','<br>')
 doc=f'''<html><meta charset="utf-8"><style>@page{{size:A4;margin:16mm}}body{{font-family:"Noto Sans CJK SC",sans-serif;font-size:10pt;line-height:1.55}}h1{{font-size:19pt}}.head{{background:#f3f5f7;padding:10px;border-radius:7px}}.comment,.reply{{border:1px solid #ddd;padding:9px;margin:8px 0;border-radius:7px;break-inside:avoid}}.reply{{margin-left:24px;background:#fafafa;border-left:4px solid #aabbd3}}.meta{{color:#666;font-size:8.5pt;margin:3px 0}}img{{max-width:90%;max-height:140mm;display:block;margin:7px 0}}.empty{{padding:30px;text-align:center;border:1px dashed #999}}</style><h1>作品评论存档</h1><div class="head"><b>作品ID：</b>{a}<br><b>发布时间：</b>{ft(w.get('create_time'))}<br><b>简介：</b>{desc}<br><b>一级评论：</b>{len(rs)} 条<br><b>已收录回复：</b>{rep} 条</div>{body}</html>'''
 HTML(string=doc).write_pdf(dst); return {'top':len(rs),'replies':rep,'all':total}

def main():
 h,c=parse_curl(Path('.private/douyin_login_curl.txt').read_text()); cm=cmap(c); works=json.loads(Path('douyin_profile_enum/全部作品.json').read_text())
 links=[f"https://www.douyin.com/{'note' if imgpost(w) else 'video'}/{w['aweme_id']}" for w in works]
 cfg={'link':links,'path':str(RAW.resolve()),'music':False,'cover':False,'avatar':False,'json':True,'folderstyle':True,'filename_template':'{id}','folder_template':'{id}','author_dir':'sec_uid','download_pinned':True,'mode':['post'],'number':{'post':0},'thread':4,'retry_times':4,'proxy':'','database':False,'video_quality':'highest','progress':{'quiet_logs':True},'browser_fallback':{'enabled':True,'headless':True,'max_scrolls':240,'idle_rounds':8,'wait_timeout_seconds':600},'comments':{'enabled':True,'include_replies':True,'max_comments':0,'page_size':20},'cookies':{k:cm.get(k,'') for k in ('msToken','ttwid','odin_tt','passport_csrf_token','sid_guard')}}
 (V/'config.yml').write_text(yaml.safe_dump(cfg,allow_unicode=True,sort_keys=False)); subprocess.run([sys.executable,'run.py','-c',str((V/'config.yml').resolve()),'--show-warnings'],cwd=V,check=True)
 rec=[]; fail=[]
 for i,w in enumerate(sorted(works,key=lambda x:int(x.get('create_time') or 0)),1):
  a=str(w['aweme_id']); d=OUT/ft(w.get('create_time'),False); d.mkdir(parents=True,exist_ok=True); fs=[p for p in RAW.rglob('*') if p.is_file() and a in p.name]
  media=[]
  for p in fs:
   n=p.name.lower(); e=p.suffix.lower()
   if any(x in n for x in ('cover','avatar','music','comments','data','transcript')):continue
   if e in VE or e in IE: media.append(p)
  if imgpost(w): chosen=sorted([p for p in media if p.suffix.lower() in IE])
  else: chosen=sorted([p for p in media if p.suffix.lower() in VE],key=lambda p:p.stat().st_size,reverse=True)[:1]
  outmedia=[]
  for j,p in enumerate(chosen,1):
   name=f"{a}_{j:03d}{p.suffix.lower()}" if imgpost(w) else f"{a}{p.suffix.lower()}"; shutil.copy2(p,d/name);outmedia.append(name)
  (d/'简介.txt').write_text(f"作品ID：{a}\n作品类型：{'图文作品' if imgpost(w) else '视频作品'}\n发布时间：{ft(w.get('create_time'))}\n原始链接：https://www.douyin.com/{'note' if imgpost(w) else 'video'}/{a}\n\n作品简介：\n{w.get('desc') or '（无简介）'}\n")
  cj=next((p for p in fs if p.suffix.lower()=='.json' and 'comment' in p.name.lower()),None); data=json.loads(cj.read_text()) if cj else []
  st=pdf(a,w,data,d/'评论.pdf'); r={'id':a,'folder':d.name,'media':outmedia,'comments':st};rec.append(r)
  if not outmedia:fail.append({'id':a,'reason':'无媒体'})
  print(json.dumps({'progress':f'{i}/{len(works)}',**r},ensure_ascii=False),flush=True)
 summary={'count':len(rec),'failures':fail,'records':rec}; (ROOT/'归档清单.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)); (ROOT/'全部作品ID.txt').write_text('\n'.join(x['id'] for x in rec))
 parts=ROOT/'parts';parts.mkdir();cur=None;sz=0;n=0
 for r in rec:
  src=OUT/r['folder']; s=sum(p.stat().st_size for p in src.rglob('*') if p.is_file())
  if cur is None or sz+s>210*1024*1024:n+=1;cur=parts/f'part_{n:02d}'/'lyx520';cur.mkdir(parents=True);sz=0
  shutil.copytree(src,cur/src.name);sz+=s
 summary['parts']=n;(ROOT/'归档清单.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
 if fail: raise RuntimeError(f'{len(fail)} items missing media')
if __name__=='__main__':main()
