from pathlib import Path
import numpy as np, librosa,json
from scipy.signal import butter,sosfilt,find_peaks
ROOT=Path(__file__).resolve().parent; b=ROOT/'analysis'; b.mkdir(exist_ok=True); p=str(ROOT/'assets/music/source.mp3')
y,sr=librosa.load(p,sr=22050,mono=True); hop=256
S=np.abs(librosa.stft(y,n_fft=2048,hop_length=hop)); H,P=librosa.decompose.hpss(S,margin=(2,3))
y_p=librosa.istft(P,hop_length=hop,length=len(y))
env=librosa.onset.onset_strength(y=y_p,sr=sr,hop_length=hop)
tempo,bf=librosa.beat.beat_track(onset_envelope=env,sr=sr,hop_length=hop,start_bpm=132,tightness=400,trim=True)
bt=librosa.frames_to_time(bf,sr=sr,hop_length=hop); keep=(bt>12)&(bt<218); x=np.arange(len(bt));T,t0=np.polyfit(x[keep],bt[keep],1)
for k in range(3):
 residual=bt-(t0+x*T); good=keep&(abs(residual)<.065);T,t0=np.polyfit(x[good],bt[good],1)
# Pull phase/period towards the actual onset pool, rather than librosa's envelope lag.
times=librosa.times_like(env,sr=sr,hop_length=hop)
raw_on=librosa.onset.onset_detect(onset_envelope=env,sr=sr,hop_length=hop,units='time',backtrack=False)
# waveform band-flux, explicitly center=True STFT compensation through librosa.
freq=librosa.fft_frequencies(sr=sr,n_fft=2048)
flux={}
for name,lo,hi in [('kick',40,170),('snare',170,3000),('hat',6000,10000)]:
 z=np.log1p(P[(freq>=lo)&(freq<hi)]*20)
 e=np.maximum(0,np.diff(z,axis=1,prepend=z[:,:1])).mean(axis=0)
 e=e/(np.percentile(e,95)+1e-9); flux[name]=e
# Get local low-frequency attacks near fitted beat predictions; align by robust regression.
kt=times[np.array([int(np.argmax(flux['kick'][max(0,int((t-.055)*sr/hop)):min(len(times),int((t+.055)*sr/hop)+1)]))+max(0,int((t-.055)*sr/hop)) for t in bt])]
res=kt-(t0+x*T); good=keep&(np.abs(res)<.055)
Tf,t0f=np.polyfit(x[good],kt[good],1)
# Keep least-squares full-sequence timing; actual events stored separately for cuts.
T=Tf;t0=t0f
b0=t0-np.floor(t0/T)*T
nbeat=np.arange(int(np.ceil((len(y)/sr-b0)/T)))
grid=b0+nbeat*T
hits=[]
for name,e in flux.items():
 ids,_=find_peaks(e,distance=int(.17*sr/hop),prominence=.13)
 for i in ids:
  if e[i]>.25:hits.append(dict(t=float(times[i]),s=float(e[i]),k=name))
rms=librosa.feature.rms(y=y,frame_length=2048,hop_length=hop)[0]
# Aggregated bar statistics and chroma signatures for phrase-level changes.
chroma=librosa.feature.chroma_stft(S=S**2,sr=sr,hop_length=hop)
bar=[]
for i in range(0,len(grid)-4,4):
 a=grid[i]; z=grid[i+4]; mask=(times>=a)&(times<z)
 if not mask.any():continue
 bar.append(dict(bar=i//4,beat=i,t=float(a),end=float(z),rms_db=float(20*np.log10(np.sqrt(np.mean(rms[mask]**2))+1e-9)),kick=float(np.mean(flux['kick'][mask])),snare=float(np.mean(flux['snare'][mask])),hat=float(np.mean(flux['hat'][mask])),chroma=chroma[:,mask].mean(axis=1).tolist()))
# Half-second feature export for visualization and manual section interpretation.
feat=[]
for a in np.arange(0,len(y)/sr,.5):
 mask=(times>=a)&(times<a+.5)
 if mask.any():feat.append(dict(t=float(a),rms_db=float(20*np.log10(np.sqrt(np.mean(rms[mask]**2))+1e-9)),kick=float(np.mean(flux['kick'][mask])),snare=float(np.mean(flux['snare'][mask])),hat=float(np.mean(flux['hat'][mask]))))
res=kt-(t0+x*T); report=dict(duration=len(y)/sr,sr=sr,tracker_bpm=float(np.asarray(tempo).ravel()[0]),fitted_bpm=60/T,period=T,phase=b0,tracker_residual_median_ms=float(np.median(abs(res[keep]))*1000),tracker_residual_p95_ms=float(np.percentile(abs(res[keep]),95)*1000),beats=grid.tolist(),tracked_beats=bt.tolist(),hits=sorted(hits,key=lambda x:x['t']),bars=bar,features=feat)
(b/'beat_data.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));np.savez_compressed(b/'features.npz',times=times,rms=rms,chroma=chroma,env=env,**flux)
print('duration',round(report['duration'],4),'tracker BPM',round(report['tracker_bpm'],4),'fitted BPM',round(report['fitted_bpm'],6),'phase',round(b0,6),'P95 local kick residual ms',round(report['tracker_residual_p95_ms'],2))
print('bars: id seconds dB kick snare hat')
for a in bar:
 if a['bar']%4==0:print(a['bar'],round(a['t'],3),round(a['rms_db'],1),round(a['kick'],2),round(a['snare'],2),round(a['hat'],2))
print('strong onsets',len(hits),'saved beat_data.json')
