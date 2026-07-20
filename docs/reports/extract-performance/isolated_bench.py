import sys, os
ROOT = os.path.abspath(sys.argv[1])   # repo root to load sprite_gen from
MODE = sys.argv[2]                    # 'bench' | 'golden' | 'wall'
# purge setuptools editable finder so PathFinder + sys.path[0] wins
sys.meta_path = [f for f in sys.meta_path
                 if 'editable' not in getattr(type(f),'__module__','').lower()]
for m in list(sys.modules):
    if m == 'sprite_gen' or m.startswith('sprite_gen.'):
        del sys.modules[m]
sys.path.insert(0, ROOT)

import time, shutil, io, json, hashlib, functools
from pathlib import Path
import sprite_gen
from sprite_gen import extract, cli
assert sprite_gen.__file__.startswith(ROOT), f"WRONG PKG: {sprite_gen.__file__}"
HAS = hasattr(extract, '_grid_score_edges')
print(f"# loaded {sprite_gen.__file__}  optimized={HAS}", file=sys.stderr)

SRC = Path(ROOT)/"docs/reports/perfectpixel-b-loop-e2e-founder-v7/candidate-2"
WORKBASE = Path(sys.argv[3])

def do_extract(work):
    if work.exists(): shutil.rmtree(work)
    shutil.copytree(SRC, work)
    buf=io.StringIO(); old=sys.stdout; sys.stdout=buf
    t=time.perf_counter()
    try: cli.main(["extract","--run-dir",str(work),"--states","up_idle"])
    except SystemExit: pass
    dt=time.perf_counter()-t
    sys.stdout=old
    return dt, buf.getvalue(), work

if MODE=='bench':
    N=int(sys.argv[4]); times=[]
    for k in range(N):
        dt,_,_=do_extract(WORKBASE/f"ib{k}"); times.append(dt)
    times.sort()
    print(json.dumps({"min":min(times),"median":times[len(times)//2],"runs":[round(x,3) for x in times]}))

elif MODE=='golden':
    dt,out,work=do_extract(WORKBASE/"ibg")
    start=out.rfind("\n{")
    warnings=None
    try: warnings=json.loads(out[start:]).get("warnings")
    except Exception as e: warnings=f"<parse {e}>"
    hashes={str(p.relative_to(work)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((work/"frames").rglob("*.png"))}
    print(json.dumps({"warnings":warnings,"hashes":hashes},ensure_ascii=False))

elif MODE=='wall':
    acc={}
    def wrap(n):
        fn=getattr(extract,n)
        @functools.wraps(fn)
        def w(*a,**k):
            t=time.perf_counter()
            try: return fn(*a,**k)
            finally: acc[n]=acc.get(n,0.0)+time.perf_counter()-t
        setattr(extract,n,w)
    for n in ["_best_phase","_grid_uniformity","_grid_score_edges","remove_chroma_background_ycbcr",
              "_matte_ycc","_cleanup_alpha_ycc","_flood_clear_background_ycc","detect_background_key_ycc",
              "key_residue_fraction_ycc","connected_components","_edge_histograms","_dominant_block_color",
              "snap_by_edges","_boundary_mass","detect_pixel_grid"]:
        if hasattr(extract,n): wrap(n)
    dt,_,_=do_extract(WORKBASE/"ibw")
    print(f"TOTAL {dt:.3f}s")
    for n,v in sorted(acc.items(),key=lambda x:-x[1]):
        print(f"  {v:6.3f}s {100*v/dt:5.1f}% {n}")
