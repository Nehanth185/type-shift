"""G158: tune baseline length + std floor (SYNTHETIC). Fixes G157's 19% false alerts."""
import random, statistics as st
def chk(h,k,bn,floor,cons=2):
    if len(h)<bn+cons: return False
    b=h[:bn]; mu=st.mean(b); sd=max(st.pstdev(b),floor)
    return all(abs(x-mu)>k*sd for x in h[-cons:])
def run(k,bn,fl,ns,ds,seed,drift=6.0,n=20):
    r=random.Random(seed);h=[]
    for i in range(n):
        h.append(60+r.gauss(0,ns)+(drift if ds is not None and i>=ds else 0))
        if chk(h,k,bn,fl): return i
def main():
    print("k bn floor noise false% missed% (drift 6dB at round 10, noise sd 1.0 dB)")
    for k in (3,4):
      for bn in (4,6,8):
        for fl in (0.0,1.0):
            fa=sum(run(k,bn,fl,1.0,None,s) is not None for s in range(500))/5
            ms=sum(run(k,bn,fl,1.0,10,s) is None for s in range(500))/5
            print(k,bn,fl,f"{fa:.1f}",f"{ms:.1f}")
main()
