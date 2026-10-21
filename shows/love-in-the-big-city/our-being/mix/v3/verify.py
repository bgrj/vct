#!/usr/bin/env python3
"""Technical checks. Does not claim full-speed audiovisual or GUI review."""
from pathlib import Path
import json,re,subprocess,math,hashlib
from collections import Counter
import numpy as np
P=Path(__file__).resolve().parent;plan=json.loads((P/'timeline.json').read_text());v=P/'output/不得人爱便无法爱人_v3.mp4'
def cmd(a):
 p=subprocess.run(a,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 if p.returncode:raise RuntimeError(p.stderr.decode(errors='replace')[-3000:])
 return p.stdout,p.stderr.decode(errors='replace')
x,_=cmd(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(v)]);meta=json.loads(x)
vs=next(s for s in meta['streams'] if s['codec_type']=='video');aus=next(s for s in meta['streams'] if s['codec_type']=='audio')
assert (vs['width'],vs['height'])==(1920,1080);assert vs['r_frame_rate']==f"{plan['fps']}/1";assert int(vs['nb_frames'])==round(plan['video_duration']*plan['fps']);assert aus['codec_name']=='aac';assert abs(float(meta['format']['duration'])-plan['music_duration'])<.05
shots=plan['shots'];assert shots[0]['start_frame']==0
for a,b in zip(shots,shots[1:]):assert a['end_frame']==b['start_frame']
coverage=dict(sorted(Counter(s['episode'] for s in shots).items()));assert set(coverage)=={f'{n:02}' for n in range(1,9)}
caption_cps=max(s['readability_cps'] for s in shots);assert caption_cps<=4.5
cue=[]
for s in shots:
 h=s['nominal_start_hit']
 if h:
  error=(s['start']-h['t'])*1000;cue.append(dict(shot=s['id'],frame=s['start_frame'],time=s['start'],detected_attack=h['t'],attack_kind=h['k'],quantization_error_ms=error,alignment='dissolve center' if s['dissolve_frames'] else 'hard cut'))
assert max(abs(x['quantization_error_ms']) for x in cue)<=1000/(2*plan['fps'])+.01
(P/'analysis/cue_precision.json').write_text(json.dumps(cue,ensure_ascii=False,indent=2))
_,ln=cmd(['ffmpeg','-hide_banner','-i',str(v),'-map','0:a:0','-af','loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json','-f','null','-']);loud=json.loads(re.findall(r'\{[^{}]+\}',ln)[-1]);assert -15.0<=float(loud['input_i'])<=-13.0;assert float(loud['input_tp'])<=-.7
_,blacklog=cmd(['ffmpeg','-hide_banner','-i',str(v),'-an','-vf','blackdetect=d=0.1:pix_th=0.06:pic_th=0.99','-f','null','-']);(P/'analysis/blackdetect.log').write_text(blacklog)
blacks=[dict(start=float(a),end=float(b),duration=float(d)) for a,b,d in re.findall(r'black_start:([\d.]+) black_end:([\d.]+) black_duration:([\d.]+)',blacklog)]
assert not [r for r in blacks if r['start']<plan['video_duration']-.45]
# Decode the master and rendered AAC, then measure encoder/mux offset at four positions.
def pcm(path):
 raw,_=cmd(['ffmpeg','-v','error','-i',str(path),'-map','0:a:0','-ac','1','-ar','8000','-f','f32le','-'])
 return np.frombuffer(raw,dtype='<f4')
a=pcm(P/'assets/music/master.wav');b=pcm(v);offsets=[]
for t in [5,70,145,200]:
 i=int(t*8000);n=64000;x=b[i:i+n].astype(float);y=a[i:i+n].astype(float);x-=x.mean();y-=y.mean();nn=1<<(2*n-1).bit_length();cor=np.fft.irfft(np.fft.rfft(x,nn)*np.conj(np.fft.rfft(y,nn)),nn);lags=np.arange(-320,321);lag=int(lags[np.argmax(cor[lags%nn])]);offsets.append(dict(at_sec=t,offset_ms=lag/8))
assert max(abs(r['offset_ms']) for r in offsets)<20
sha=hashlib.sha256(v.read_bytes()).hexdigest()
report=dict(status='technical checks passed',video=dict(filename=v.name,duration=float(meta['format']['duration']),width=1920,height=1080,fps=plan['fps'],frames=int(vs['nb_frames']),bytes=v.stat().st_size,sha256=sha),source_media='still images, not motion episode footage',shots=len(shots),caption_cues=sum(bool(s['caption']) for s in shots),max_caption_characters_per_sec=caption_cps,episode_coverage=coverage,source_bpm=plan['source_bpm'],cue_alignment=dict(measured_cues=len(cue),mean_abs_frame_quantization_ms=float(np.mean([abs(z['quantization_error_ms']) for z in cue])),max_abs_frame_quantization_ms=max(abs(z['quantization_error_ms']) for z in cue),audio_reference='detected source transients; no subjective listening claim'),audio=dict(integrated_lufs=float(loud['input_i']),true_peak_dbtp=float(loud['input_tp']),lra=float(loud['input_lra']),source_codec='MP3 128 kb/s',export_codec='AAC 256 kb/s',master_sample_rate=48000,measured_mux_offsets=offsets,restoration_claim='loudness and peak management only; no recovery of lossy source information'),blank_sections=blacks,review_limits=['Technical measurements do not establish emotional quality.','Key frames inspected separately; no claim of full-speed listening.','Concat runtime cannot load here: missing GLIBC_2.35; schema-based project only.'])
(P/'analysis/qa_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps({k:report[k] for k in ['status','video','cue_alignment','audio']},ensure_ascii=False,indent=2))
