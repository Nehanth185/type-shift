# Synthetic: slow ramp drift (dB/round), detection delay with floor 1.0, baseline 6
import random,statistics as S
def run(k,ramp,rounds=40,bn=6,floor=1.0,sd=1.0,trials=2000):
    delays=[];miss=0
    for _ in range(trials):
        x=[random.gauss(0,sd)+(ramp*max(0,i-10)) for i in range(rounds)]
        b=x[:bn];m=S.mean(b);s=max(S.stdev(b),floor);d=None
        for i in range(bn,rounds):
            if x[i]-m>k*s: d=i;break
        if d is None: miss+=1
        elif d>=10: delays.append(d-10)
    return (S.mean(delays) if delays else None),100*miss/trials
print("k ramp_dB/round mean_delay_rounds missed%")
for k in(3,4):
    for r in(0.2,0.5,1.0,2.0):
        d,m=run(k,r);print(k,r,None if d is None else round(d,1),round(m,1))
