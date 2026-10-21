#!/usr/bin/env python3
"""Build a Concat v1 document using its published 0.2.8 model.
Picture movement/dissolves are pre-rendered; captions remain native editable text.
The Linux Concat binary cannot run here (requires GLIBC_2.35).
"""
from pathlib import Path
import json
P=Path(__file__).resolve().parent
p=json.loads((P/'timeline.json').read_text());segs=json.loads((P/'segments.json').read_text());fps=p['fps']
media=[];clips=[]
base=dict(sourceStart=0,volume=1,fadeIn=0,fadeOut=0,scale=1,offsetX=0,offsetY=0,rotation=0,opacity=1,speed=1)
for i,s in enumerate(segs):
 mid=f'm{i+1}';cid=f'v{i+1}';dur=s['frames']/fps
 media.append(dict(id=mid,path='assets/segments/'+s['name'],name=s['name'],kind='video',width=1920,height=1080,hasAudio=False,duration=dur))
 clips.append(dict(base,id=cid,trackId='T2',mediaId=mid,name=s['name'],kind='video',start=s['start']/fps,duration=dur,volume=0))
for s in p['shots']:
 if not s['caption']:continue
 d=s['caption_end']-s['caption_start'];lines=s['caption'].count('\n')+1
 text=dict(content=s['caption'],fontFamily='Noto Sans SC',fontSize=.05 if s['id']!=46 else .056,fontWeight=400,italic=False,color='#efefef',align='center',opacity=1,strokeWidth=.0005,strokeColor='#303030',shadow=False,background='',lineHeight=1.30,tracking=.0005,maxWidth=.90,maxHeight=0)
 keys=[dict(property='opacity',at=0,value=0,ease='linear'),dict(property='opacity',at=min(.09/d,.2),value=1,ease='linear'),dict(property='opacity',at=max(1-.11/d,.8),value=1,ease='linear'),dict(property='opacity',at=1,value=0,ease='linear')]
 clips.append(dict(base,id=f"q{s['id']}",trackId='T1',mediaId='',name=s['caption'].split('\n')[0],kind='text',start=s['caption_start'],duration=d,offsetY=.436 if lines==1 else .401,text=text,keys=keys))
media.append(dict(id='music',path='assets/music/master.m4a',name='SEXY LOVE · mastered',kind='audio',hasAudio=True,duration=p['video_duration']))
clips.append(dict(base,id='music-bed',trackId='T3',mediaId='music',name='原曲完整时长 · 桥段轻收',kind='audio',start=0,duration=p['video_duration']))
video=dict(width=1920,height=1080,rateNum=fps,rateDen=1)
tracks=[dict(id='T1',visible=True,muted=False,name='可编辑原词字幕'),dict(id='T2',visible=True,muted=True,name='画面与叠化'),dict(id='T3',visible=True,muted=False,name='原曲与响度处理')]
tl=dict(id='TL1',name='V3 · 情绪与乐句',video=video,tracks=tracks,clips=clips)
doc=dict(concat='0.2.8',version=1,name=p['name']+' · 第三版',video=video,media=media,fonts=[],tracks=tracks,clips=clips,timelines=[tl],activeTimelineId='TL1')
(P/'concat.json').write_text(json.dumps(doc,ensure_ascii=False,indent=2))
print('Concat document created:',len(segs),'picture segments,',sum(s['kind']=='text' for s in clips),'editable captions. Runtime validation unavailable.')
