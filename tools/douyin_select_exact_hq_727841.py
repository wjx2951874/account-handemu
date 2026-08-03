#!/usr/bin/env python3
from __future__ import annotations
import hashlib,json,shlex,subprocess,urllib.parse
from pathlib import Path
import requests
AID='7278410935755148604'
items=json.loads(Path('douyin_profile_enum/全部作品.json').read_text(encoding='utf-8'))
target=next((x for x in items if str(x.get('aweme_id'))==AID),None)
if not target: raise SystemExit('target missing from fresh profile enumeration')
raw=target.get('raw') or {}; video=raw.get('video') or {}
curl=Path('.private/douyin_login_curl.txt').read_text(encoding='utf-8')
tokens=shlex.split(curl,posix=True); cookie=''; ua='Mozilla/5.0'; referer=f'https://www.douyin.com/video/{AID}'
for i,t in enumerate(tokens):
    if t in ('-b','--cookie') and i+1<len(tokens): cookie=tokens[i+1]
    elif t in ('-A','--user-agent') and i+1<len(tokens): ua=tokens[i+1]
    elif t in ('-e','--referer') and i+1<len(tokens): referer=tokens[i+1]
    elif t in ('-H','--header') and i+1<len(tokens):
        h=tokens[i+1]
        if h.lower().startswith('cookie:'): cookie=h.split(':',1)[1].strip()
        elif h.lower().startswith('user-agent:'): ua=h.split(':',1)[1].strip()
candidates=[]
def add(source,addr,meta):
    if not isinstance(addr,dict): return
    for url in addr.get('url_list') or []:
        if isinstance(url,str) and url.startswith('http'):
            candidates.append({'source':source,'url':url,'declared_width':int(addr.get('width') or meta.get('width') or video.get('width') or 0),'declared_height':int(addr.get('height') or meta.get('height') or video.get('height') or 0),'declared_size':int(addr.get('data_size') or meta.get('data_size') or 0),'bit_rate':int(meta.get('bit_rate') or 0),'gear_name':meta.get('gear_name')})
for i,br in enumerate(video.get('bit_rate') or []):
    if isinstance(br,dict):
        for key in ('play_addr_265','play_addr_h264','play_addr'): add(f'bit_rate[{i}].{key}',br.get(key),br)
for key in ('play_addr_265','play_addr_h264','play_addr'): add(f'video.{key}',video.get(key),video)
unique=[]; seen=set()
for c in candidates:
    if c['url'] not in seen: seen.add(c['url']); unique.append(c)
unique.sort(key=lambda c:(c['declared_width']*c['declared_height'],c['bit_rate'],c['declared_size']),reverse=True)
Path('output').mkdir(exist_ok=True); Path('report').mkdir(exist_ok=True); Path('candidate_files').mkdir(exist_ok=True)
def probe(path):
    p=subprocess.run(['ffprobe','-v','error','-probesize','20M','-analyzeduration','20M','-show_entries','stream=codec_type,codec_name,width,height,duration,bit_rate','-show_entries','format=duration,size,bit_rate,format_name','-of','json',str(path)],capture_output=True,text=True,timeout=90)
    return json.loads(p.stdout) if p.returncode==0 else None
s=requests.Session(); headers={'User-Agent':ua,'Referer':referer,'Cookie':cookie,'Accept':'*/*'}
attempts=[]; valid=[]
for idx,c in enumerate(unique):
    path=Path('candidate_files')/f'{idx:03d}.bin'; rec={k:v for k,v in c.items() if k!='url'}; rec['host']=urllib.parse.urlsplit(c['url']).netloc
    try:
        with s.get(c['url'],headers=headers,stream=True,timeout=(20,150),allow_redirects=True) as r:
            rec['status']=r.status_code; rec['content_type']=r.headers.get('content-type',''); r.raise_for_status()
            with path.open('wb') as f:
                for chunk in r.iter_content(1024*1024):
                    if chunk: f.write(chunk)
        rec['downloaded_size']=path.stat().st_size; pr=probe(path); rec['probe']=pr
        if not pr: raise RuntimeError('ffprobe failed')
        streams=pr.get('streams') or []; vids=[x for x in streams if x.get('codec_type')=='video']; auds=[x for x in streams if x.get('codec_type')=='audio']
        if not vids: raise RuntimeError('no video stream')
        v=vids[0]; w=int(v.get('width') or 0); h=int(v.get('height') or 0); d=float((pr.get('format') or {}).get('duration') or v.get('duration') or 0)
        rec.update({'width':w,'height':h,'duration':d,'has_audio':bool(auds)})
        if 5<=d<=10 and w*h>720*1280: valid.append((w*h,path,rec))
    except Exception as e: rec['error']=type(e).__name__+': '+str(e)[:300]
    attempts.append(rec)
report={'fresh_work_count':len(items),'work_id':AID,'target_create_time':target.get('create_time_text'),'candidate_count':len(unique),'attempts':attempts,'valid_hq_count':len(valid)}
if not valid:
    report['target_raw']=raw; Path('report/report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8'); raise SystemExit('no verified HQ candidate')
_,src,chosen=max(valid,key=lambda x:(x[0],x[1].stat().st_size)); final=Path('output')/f'{AID}.mp4'
p=subprocess.run(['ffmpeg','-y','-i',str(src),'-map','0:v:0','-map','0:a:0?','-c','copy','-movflags','+faststart',str(final)],capture_output=True,text=True,timeout=180)
if p.returncode: raise SystemExit(p.stderr[-1500:])
subprocess.run(['ffmpeg','-v','error','-i',str(final),'-f','null','-'],check=True,timeout=180)
report.update({'chosen':chosen,'final_probe':probe(final),'final_size':final.stat().st_size,'sha256':hashlib.sha256(final.read_bytes()).hexdigest()})
Path('report/report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'success':True,'fresh_work_count':len(items),'width':chosen['width'],'height':chosen['height'],'duration':chosen['duration'],'has_audio':chosen['has_audio'],'source':chosen['source'],'size':final.stat().st_size},ensure_ascii=False))
