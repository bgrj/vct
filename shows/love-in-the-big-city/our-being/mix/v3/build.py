#!/usr/bin/env python3
"""Deterministic 30fps still montage. All timing comes from timeline.json."""
from __future__ import annotations
import argparse,json,subprocess,re,math,csv,shutil,os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
from PIL import Image
ROOT=Path(__file__).resolve().parent
PLAN=json.loads((ROOT/'timeline.json').read_text()); FPS=PLAN['fps']; W,H=1920,1080
BG='0x0a0b0d'; FONT=os.environ.get('NOTO_CJK_FAMILY','Noto Sans SC')
CACHE=ROOT/'assets/segments'; PREP=ROOT/'assets/prepared'; OUT=ROOT/'output'; QA=ROOT/'qa'
for d in [CACHE,PREP,OUT,QA,ROOT/'analysis']:d.mkdir(exist_ok=True,parents=True)
def run(cmd,log=None):
 p=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
 if log:Path(log).write_text(p.stderr)
 if p.returncode:raise RuntimeError('Command failed: '+' '.join(map(str,cmd[:10]))+'\n'+p.stderr[-4500:])
 return p.stdout,p.stderr

def ensure_assets():
 import requests
 from urllib.parse import quote
 def fetch(s):
  dest=ROOT/'assets/stills'/f"{s['episode']}_{s['filename']}";dest.parent.mkdir(parents=True,exist_ok=True)
  if not dest.exists():
   r=requests.get(s['source_url'],timeout=60);r.raise_for_status();dest.write_bytes(r.content)
 unique={s['episode']+s['filename']:s for s in PLAN['shots']}
 with ThreadPoolExecutor(max_workers=6) as ex:list(ex.map(fetch,unique.values()))
 dst=ROOT/'assets/music/source.mp3';dst.parent.mkdir(parents=True,exist_ok=True)
 if not dst.exists():
  url='https://raw.githubusercontent.com/bgrj/vct/main/shows/love-in-the-big-city/'+quote('T-ara - SEXY LOVE_L.mp3')
  r=requests.get(url,timeout=90);r.raise_for_status();dst.write_bytes(r.content)

def prepare():
 ensure_assets()
 for s in PLAN['shots']:
  src=ROOT/'assets/stills'/f"{s['episode']}_{s['filename']}"
  im=Image.open(src).convert('RGB');c=s['source_crop'];im=im.crop((c['left'],c['top'],c['left']+c['width'],c['top']+c['height'])).resize((1920,840),Image.Resampling.LANCZOS)
  im.save(PREP/f"{s['id']:03}.jpg",quality=97,subsampling=0)

def ts(t,ass=False):
 unit=100 if ass else 1000; z=round(t*unit);sec,frac=divmod(z,unit);h,sec=divmod(sec,3600);m,sec=divmod(sec,60)
 return f'{h}:{m:02}:{sec:02}.{frac:02}' if ass else f'{h:02}:{m:02}:{sec:02},{frac:03}'
ASS_HEADER=f'''[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Quote,{FONT},72,&H00EFEFEF,&H00EFEFEF,&H00303030,&H00000000,0,0,0,0,100,100,1,0,1,0.6,0,2,110,110,42,1
Style: Theme,{FONT},80,&H00E7E5E2,&H00E7E5E2,&H00303030,&H00000000,0,0,0,0,100,100,2,0,1,0.6,0,2,110,110,48,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
def event(s,t0,t1):
 cap=s['caption'].replace('\n',r'\N');style='Theme' if s['id']==46 else 'Quote'
 return f"Dialogue: 0,{ts(t0,True)},{ts(t1,True)},{style},,0,0,0,,{{\\fad(90,110)}}{cap}\n"
def captions():
 ev=[];srt=[];n=0
 for s in PLAN['shots']:
  if not s['caption']:continue
  n+=1;ev.append(event(s,s['caption_start'],s['caption_end']))
  srt.append(f"{n}\n{ts(s['caption_start'])} --> {ts(s['caption_end'])}\n{s['caption']}\n")
 (ROOT/'captions.ass').write_text(ASS_HEADER+''.join(ev));(ROOT/'captions.srt').write_text('\n'.join(srt))

def motion(s,local_start,n,progress=None):
 p=f'clip((on+{local_start})/{max(1,s["frames"]-1)},0,1)' if progress is None else str(progress)
 ease=f'(({p})*({p})*(3-2*({p})))'
 m=s['motion'];z=f'(1.025+0.045*{ease})' if m=='push' else f'(1.070-0.045*{ease})' if m=='pull' else '1.048' if m.startswith('pan') else '1.015'
 ax=f'(0.22+0.56*{ease})' if m=='pan_right' else f'(0.78-0.56*{ease})' if m=='pan_left' else '0.5'
 return f"scale=3840:1680:flags=lanczos,zoompan=z='{z}':x='(iw-iw/zoom)*{ax}':y='(ih-ih/zoom)*0.35':d={n}:s=1920x840:fps={FPS},pad=1920:1080:0:42:color={BG},setsar=1,format=yuv420p"

def preview(ids):
 for i in ids:
  s=PLAN['shots'][i];ass=QA/f'preview_{i:03}.ass';ass.write_text(ASS_HEADER+(event(s,0,10).replace(r'{\fad(90,110)}','') if s['caption'] else ''))
  # One real renderer frame, not a separate storyboard approximation.
  vf=motion(s,0,1,progress=.52)+f",ass='{ass.as_posix()}'"
  run(['ffmpeg','-y','-hide_banner','-loglevel','error','-filter_threads','1','-i',str(PREP/f'{i:03}.jpg'),'-vf',vf,'-frames:v','1',str(QA/f'preview_{i:03}.png')])
  print('preview',i,s['episode'],s['seq'],flush=True)

def segments():
 shots=PLAN['shots'];segs=[]
 for i,s in enumerate(shots):
  hin=s['dissolve_frames']//2 if i else 0
  hout=shots[i+1]['dissolve_frames']//2 if i+1<len(shots) else 0
  segs.append(dict(kind='body',shot=i,start=s['start_frame']+hin,frames=s['frames']-hin-hout,local=hin,name=f'body_{i:03}.mp4'))
  if hout:
   segs.append(dict(kind='bridge',shot=i,next=i+1,start=s['end_frame']-hout,frames=hout*2,local=s['frames']-hout,nextlocal=-hout,name=f'bridge_{i:03}_{i+1:03}.mp4'))
 assert sum(s['frames'] for s in segs)==round(PLAN['video_duration']*FPS)
 for a,b in zip(segs,segs[1:]):assert a['start']+a['frames']==b['start']
 (ROOT/'segments.json').write_text(json.dumps(segs,ensure_ascii=False,indent=2));return segs

def frame_count(p):
 out,_=run(['ffprobe','-v','error','-select_streams','v:0','-show_entries','stream=nb_frames','-of','csv=p=0',str(p)])
 return int(out.strip())
def render_segment(seg):
 dest=CACHE/seg['name']
 if dest.exists() and frame_count(dest)==seg['frames']:return seg['name']+' cached'
 s=PLAN['shots'][seg['shot']];cmd=['ffmpeg','-y','-hide_banner','-loglevel','error','-filter_threads','1','-filter_complex_threads','1','-i',str(PREP/f"{s['id']:03}.jpg")]
 if seg['kind']=='body':cmd+=['-vf',motion(s,seg['local'],seg['frames'])]
 else:
  s2=PLAN['shots'][seg['next']];cmd+=['-i',str(PREP/f"{s2['id']:03}.jpg")]
  d=seg['frames']/FPS
  graph=f"[0:v]{motion(s,seg['local'],seg['frames'])}[a];[1:v]{motion(s2,seg['nextlocal'],seg['frames'])}[b];[a][b]blend=all_expr='A*(0.5+0.5*cos(PI*T/{d}))+B*(0.5-0.5*cos(PI*T/{d}))'[v]"
  cmd+=['-filter_complex',graph,'-map','[v]']
 cmd+=['-frames:v',str(seg['frames']),'-r',str(FPS),'-an','-c:v','libx264','-threads','1','-preset','fast','-crf','17','-tune','stillimage','-pix_fmt','yuv420p','-color_primaries','bt709','-color_trc','bt709','-colorspace','bt709','-video_track_timescale','30000',str(dest)]
 run(cmd,QA/(seg['name']+'.log'));assert frame_count(dest)==seg['frames'];return seg['name']

def audio():
 src=ROOT/'assets/music/source.mp3';dst=ROOT/'assets/music/master.wav';m4a=ROOT/'assets/music/master.m4a'
 if dst.exists() and m4a.exists():return dst
 a=next(s['start'] for s in PLAN['shots'] if s['section']=='桥段：失去之后才看见');b=next(s['start'] for s in PLAN['shots'] if s['section']=='最后一段副歌：重新辨认爱')
 expr=f"if(lt(t,{a}),1,if(lt(t,{a+1.5}),1-0.35*(t-{a})/1.5,if(lt(t,{b-1}),0.65,if(lt(t,{b}),0.65+0.35*(t-{b-1}),1))))"
 prefix=f"highpass=f=28,volume='{expr}':eval=frame,aresample=48000"
 p=subprocess.run(['ffmpeg','-hide_banner','-i',str(src),'-map','0:a:0','-af',prefix+',loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json','-f','null','-'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
 if p.returncode:raise RuntimeError(p.stderr[-3000:])
 data=json.loads(re.findall(r'\{[^{}]+\}',p.stderr)[-1]);(ROOT/'analysis/loudnorm_input.json').write_text(json.dumps(data,indent=2))
 norm=f"loudnorm=I=-14:TP=-1.5:LRA=11:measured_I={data['input_i']}:measured_TP={data['input_tp']}:measured_LRA={data['input_lra']}:measured_thresh={data['input_thresh']}:offset={data['target_offset']}:linear=true:print_format=json"
 run(['ffmpeg','-y','-hide_banner','-i',str(src),'-map','0:a:0','-af',prefix+','+norm,'-ar','48000','-c:a','pcm_s24le',str(dst)],ROOT/'analysis/loudnorm_master.log')
 run(['ffmpeg','-y','-hide_banner','-loglevel','error','-i',str(dst),'-c:a','aac','-b:a','256k','-ar','48000','-movflags','+faststart',str(m4a)])
 return dst

def assemble(segs,audio_path):
 listing=ROOT/'segments.ffconcat';listing.write_text('ffconcat version 1.0\n'+''.join(f"file '{(CACHE/s['name']).as_posix()}'\n" for s in segs))
 silent=OUT/'picture_track.mp4'
 run(['ffmpeg','-y','-hide_banner','-loglevel','error','-f','concat','-safe','0','-i',str(listing),'-c','copy','-an',str(silent)])
 final=OUT/'不得人爱便无法爱人_v3.mp4'
 vf=f"ass='{(ROOT/'captions.ass').as_posix()}',fade=t=out:st={PLAN['video_duration']-.33}:d=0.33"
 run(['ffmpeg','-y','-hide_banner','-loglevel','error','-filter_threads','1','-i',str(silent),'-i',str(audio_path),'-map','0:v:0','-map','1:a:0','-vf',vf,'-c:v','libx264','-threads','2','-preset','medium','-crf','20','-maxrate','4M','-bufsize','8M','-pix_fmt','yuv420p','-color_primaries','bt709','-color_trc','bt709','-colorspace','bt709','-c:a','aac','-b:a','256k','-ar','48000','-af',f"apad=whole_dur={PLAN['video_duration']}",'-t',str(PLAN['video_duration']),'-movflags','+faststart',str(final)],QA/'export.log')
 assert frame_count(final)==round(PLAN['video_duration']*FPS)
 print('FINAL',final.name,final.stat().st_size,'bytes',flush=True);return final

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--preview',nargs='*',type=int);ap.add_argument('--audio-only',action='store_true');ap.add_argument('--workers',type=int,default=2);args=ap.parse_args()
 prepare();captions()
 if args.preview is not None:preview(args.preview or [0,11,23,29,33,41,46]);return
 if args.audio_only:audio();return
 segs=segments()
 with ThreadPoolExecutor(max_workers=args.workers) as ex:
  fs=[ex.submit(render_segment,s) for s in segs]
  for n,f in enumerate(as_completed(fs),1):print(f'{n}/{len(segs)} {f.result()}',flush=True)
 assemble(segs,audio())
if __name__=='__main__':main()
