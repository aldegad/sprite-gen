# SPDX-License-Identifier: Apache-2.0
"""Source-backed acceptance: actual bytes, an immutable origin, fixed source time, final exports.

These are synthetic contract fixtures, not a production repair success claim.
"""
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from sprite_gen.video import evidence, source, restoration, playback, loop, repair as repair_mod
from sprite_gen.cli import main
from sprite_gen.util.gif_utils import save_clean_gif
from sprite_gen.util.resample import resize_cell
from sprite_gen.video.compare import Loop
from sprite_gen.video.interpolation_quality import GHOST_WARN

W, H, N = 64, 80, 12
FILL = (210, 160, 110, 255)
METRIC, POLICY = 'source-restoration-v5', 'key-protected-source-copy-one-step-cap-jump-ghost-guard-v1'
REPAIR_SCHEMA, COMPARISON_SCHEMA = 4, 6
# Two colours under the green key's bar (hue excess -6 each), one led by red and one by blue:
# their even mix (105, 114, 105) is one step over it. ROSE and IRIS are the same for magenta.
WARM, COOL = (120, 114, 90, 255), (90, 114, 120, 255)
ROSE, IRIS = (124, 100, 94, 255), (94, 100, 124, 255)


def frames():
    result = []
    for k in range(N):
        im = Image.new('RGBA', (W, H))
        d = ImageDraw.Draw(im)
        y = [0, 1, 2, 1, 0, -1][k % 6]
        d.rectangle((16, 10+y, 46, 57+y), fill=(210, 160, 110), outline=(20, 20, 20), width=3)
        # Different feet alternate independently; body bob is intentional.
        d.rectangle((15+k%6, 58, 26+k%6, 74), fill=(45, 35, 25))
        d.rectangle((39-k%6, 58, 48-k%6, 74), fill=(65, 45, 25))
        # Natural partial coverage, absent from the damaged frame below.
        d.line((15, 14+y, 15, 52+y), fill=(30, 30, 30, 170))
        result.append(im)
    return result


def damage(im, zoom=1):
    made = im.copy()
    for y in range(8*zoom, 58*zoom):
        for x in range(made.width):
            if made.getpixel((x,y))[:3] in ((20,20,20),(30,30,30)):
                made.putpixel((x,y),FILL)
    return made


def exports(paths, fs, delay=42):
    save_clean_gif(fs, paths['gif'], duration_ms=delay, alpha_threshold=128)
    loop.write_webp(fs, paths['webp'], delay_ms=delay, workdir=paths['webp'].parent/'webp-temp')


def cleaned(frame):
    # The loop cleanup is part of the recorded recipe.
    from sprite_gen.video.frames import drop_specks
    c=frame.copy(); loop._scrub(c)
    return drop_specks(c,alpha_over=16,diagonal=False,apart=0)[0]


def write_strip(paths, fs):
    sheet=Image.new('RGBA',(N*W,H))
    for k,f in enumerate(fs): sheet.paste(f,(W*k,0))
    sheet.save(paths['strip']); exports(paths,fs)


def cells(paths):
    return restoration.read_loop(paths)[0].frames


def build(tmp_path, zoom=1, key='green', paint=None, facing='right', unpainted=()):
    """A loop with two damaged cells and its keyed source, `zoom` times the cell's size.

    At zoom 1 a cell is a copy of its frame; at 2 every cell is a resample, as a delivered loop's are.
    `paint` draws on chosen source frames before the loop is cut from them. `facing` is the one the
    jump repair records, as `video-loop --facing` writes it. A damaged cell in `unpainted` is made from
    its frame as it was before `paint`: what the cut gave way for what was painted.
    """
    if not shutil.which('ffmpeg') or not loop.img2webp_supports_exact():
        pytest.skip('ffmpeg and img2webp >=1.5 required')
    fs = [f.resize((W*zoom,H*zoom),Image.Resampling.NEAREST) for f in frames()]
    drawn = [f.copy() for f in fs]
    for k,draw in (paint or {}).items(): draw(fs[k])
    src_dir=tmp_path/'keyed'
    src_dir.mkdir()
    for k,f in enumerate(fs): f.save(src_dir/f'frame-{k:04}.png')
    canvas=src_dir/'frame-0000.png'
    freport=tmp_path/'frames.json'
    freport.write_text(json.dumps({'kind':'sprite-gen-video-frames-report','fps':24.,'frames':12,'width':W*zoom,'height':H*zoom,'stream_index':0,'key':key}))
    manifest=tmp_path/'source.json'
    inputs=dict(source_manifest=manifest,source_frames_dir=src_dir,source_canvas=canvas,source_frames_report=freport)
    mp4=tmp_path/'clip.mp4'
    subprocess.run(['ffmpeg','-v','error','-framerate','24','-i',str(src_dir/'frame-%04d.png'),'-c:v','libx264','-pix_fmt','yuv420p',str(mp4)],check=True)
    inputs['source_clip']=mp4
    manifest.write_text(json.dumps(source.manifest(clip=mp4,canvas=canvas,frames_report=freport,files=sorted(src_dir.glob('*.png')),timestamps=source.timestamps(mp4,0))))
    clean=[resize_cell(cleaned(f),(W,H)) for f in fs]
    bad=list(clean)
    for k in (2,8): bad[k]=resize_cell(damage(cleaned((drawn if k in unpainted else fs)[k]),zoom),(W,H))
    paths={k:tmp_path/('b'+ext) for k,ext in [('strip','.png'),('meta','.json'),('report','.loop.json'),('gif','.gif'),('webp','.webp')]}
    m={'frames':12,'w':64,'h':80,'delay_ms':41.67,'cycle_seconds':.5,'cycle_frames':12,'subsampled':False,'loop':True,'scale':1/zoom,'source_rect':[0,0,W*zoom,H*zoom],'sample_indices':list(range(12)),'foot_anchor':'none'}
    src=source.Source.read(**inputs)
    m['source_cut']={'source':src.record['source'],'start':0,'length':12,'samples':list(range(12))}
    report={'kind':'sprite-gen-video-loop-report','status':'passed','fps':24.,'frames_total':12,'source':src.record['source'],'cycle':{'start':0,'length':12},'anchor':'none','jump_repair':{'replaced':[2,8],'facing':facing},'strip':m,'n_out':12,'delay_ms':42}
    paths['meta'].write_text(json.dumps(m)); paths['report'].write_text(json.dumps(report))
    write_strip(paths,bad)
    return paths, inputs, clean


@pytest.fixture
def case(tmp_path):
    return build(tmp_path)


@pytest.fixture
def scaled(tmp_path,request):
    return build(tmp_path,zoom=2,key=getattr(request,'param','green'))


def seam_patch(left,right):
    """On a twice-size keyed frame: two colours inside the body that meet within one cell column (27, rows 25-30)."""
    def paint(f):
        f.paste(left,(48,48,55,64)); f.paste(right,(55,48,64,64))
    return paint


def repair(case,tmp_path,index=1,*,baseline=None,origin=None,out=None):
    paths,inputs,_=case
    return restoration.restore(baseline or paths,origin or paths,source.Source.read(**inputs),
                               out_dir=tmp_path/(out or f'candidate-{index}'),name='loop',proposal_index=index)


def outputs(result):
    return {k:Path(p) for k,p in result['outputs'].items()}


def compare_result(case,result,*,baseline=None,origin=None,candidate=None):
    paths,inputs,_=case
    o,_=restoration.read_loop(origin or paths)
    a,ap=restoration.read_loop(baseline or paths)
    b,bp=restoration.read_loop(candidate or outputs(result))
    return restoration.compare(o,a,b,source.Source.read(**inputs),baseline_playback=ap,candidate_playback=bp,repair_evidence=result)


def repaint_source(case, index, paint):
    """Change one keyed source frame and receipt the new bytes; the baseline's normal cells are untouched."""
    paths,inputs,_=case
    p=inputs['source_frames_dir']/f'frame-{index:04}.png'
    with Image.open(p) as image: frame=image.convert('RGBA')
    paint(frame); frame.save(p)
    record=source.manifest(clip=inputs['source_clip'],canvas=inputs['source_canvas'],frames_report=inputs['source_frames_report'],
                           files=sorted(inputs['source_frames_dir'].glob('*.png')),timestamps=source.timestamps(inputs['source_clip'],0))
    inputs['source_manifest'].write_text(json.dumps(record))
    m=json.loads(paths['meta'].read_bytes()); m['source_cut']['source']=record['source']; paths['meta'].write_text(json.dumps(m))
    r=json.loads(paths['report'].read_bytes()); r.update(source=record['source'],strip=m); paths['report'].write_text(json.dumps(r))
    return frame


def test_source_restoration_preserves_normal_motion_and_timing(case,tmp_path):
    result=repair(case,tmp_path)
    assert result['status']=='candidate' and result['schema_version']==REPAIR_SCHEMA
    assert result['origin_artifacts']==result['baseline_artifacts']
    comparison=compare_result(case,result)
    assert comparison['verdict']=='improved', comparison['reasons']
    assert (comparison['metric_version'],comparison['policy_version'],comparison['schema_version'])==(METRIC,POLICY,COMPARISON_SCHEMA)
    target=result['target']
    a,b=cells(case[0]),cells(outputs(result))
    assert all(x.tobytes()==y.tobytes() for k,(x,y) in enumerate(zip(a,b)) if k!=target)
    # Nothing is protected or capped here, so the copy is the whole source cell.
    assert result['partial']['protected']['count']==result['partial']['capped']['count']==0
    assert b[target].tobytes()==case[2][target].tobytes()
    axis=comparison['axes']['introduced_partial']
    assert axis['raw_candidate'][target]>axis['raw_baseline'][target]
    assert axis['candidate'][target]==0
    assert comparison['cleared_faults']==[{'cell':target,'faults':['outline']}]
    assert comparison['gait']=={'absolute':'unverified','source_order':'preserved'}
    assert comparison['playback']['baseline']['gif']['durations_ms']==[40]*12
    assert comparison['playback']['candidate']['webp']['durations_ms']==[42]*12
    # The loop report stays the origin's record; only the receipt is added.
    report=json.loads(outputs(result)['report'].read_bytes())
    receipt=report.pop('restoration')
    assert report==json.loads(case[0]['report'].read_bytes())
    assert outputs(result)['meta'].read_bytes()==case[0]['meta'].read_bytes()
    assert receipt['origin_artifacts']==result['origin_artifacts'] and receipt['source_artifacts']==result['source_artifacts']
    assert [e['target'] for e in receipt['applied']]==[target]
    assert receipt['applied'][0]['proposal_id']==result['proposal_id']==comparison['restoration']['proposal_id']


def test_next_independent_proposal_and_budget(case,tmp_path):
    first,second=repair(case,tmp_path,1),repair(case,tmp_path,2)
    assert first['target']!=second['target'] and first['proposal_id']!=second['proposal_id']
    assert second['comparison']['verdict']=='improved'
    # Proposal 2 starts from the same baseline: it does not hold proposal 1.
    assert cells(outputs(second))[first['target']].tobytes()==cells(case[0])[first['target']].tobytes()
    assert repair(case,tmp_path,3)['status']=='exhausted'
    with pytest.raises(ValueError,match='between 1 and 3'): repair(case,tmp_path,4)
    with pytest.raises(ValueError,match='directory must be new'): repair(case,tmp_path,1)


def test_normal_source_is_noop(case,tmp_path):
    paths,_,clean=case
    write_strip(paths,clean)
    result=repair(case,tmp_path)
    assert result['status']=='no_change' and result['comparison']['verdict']=='non_regressing'
    assert result['baseline_artifacts']==result['candidate_artifacts']
    assert all(outputs(result)[k].read_bytes()==paths[k].read_bytes() for k in paths)


@pytest.mark.parametrize('field',['clip','canvas','frames_report','frame','manifest','pts'])
def test_changed_or_forged_source_receipt_is_error(case,field):
    _,inputs,_=case
    if field=='manifest':
        m=json.loads(inputs['source_manifest'].read_bytes()); m['source']['sha256']='f'*64
        inputs['source_manifest'].write_text(json.dumps(m))
    elif field=='pts':
        m=json.loads(inputs['source_manifest'].read_bytes()); m['frames'][3]['pts_seconds']+=.0001
        inputs['source_manifest'].write_text(json.dumps(m))
    else:
        p=next(inputs['source_frames_dir'].glob('*.png')) if field=='frame' else inputs['source_'+field]
        p.write_bytes(p.read_bytes()+b'changed')
    with pytest.raises(ValueError): source.Source.read(**inputs)


def test_missing_source_is_error_and_follow_is_shared_unknown(case,tmp_path):
    paths,inputs,_=case
    meta=json.loads(paths['meta'].read_bytes()); meta['follow']={'gain':1}; paths['meta'].write_text(json.dumps(meta))
    r=repair(case,tmp_path)
    assert r['status']=='unknown' and r['common_failure'] and not r['outputs']
    assert r['reasons']==['follow-source-warp-unverified']
    inputs['source_clip'].unlink()
    with pytest.raises(FileNotFoundError): source.Source.read(**inputs)


@pytest.mark.parametrize('mutation',['freeze','wrong_time','colour','alpha','duration','crop','report','receipt'])
def test_tampered_final_artifacts_are_not_accepted(case,tmp_path,mutation):
    r=repair(case,tmp_path)
    paths=outputs(r)
    if mutation in ('duration','crop'):
        m=json.loads(paths['meta'].read_bytes()); m['delay_ms' if mutation=='duration' else 'source_rect']=80 if mutation=='duration' else [1,0,65,80]
        paths['meta'].write_text(json.dumps(m))
    elif mutation=='report':
        paths['report'].write_text(paths['report'].read_text()+'\n')
    elif mutation=='receipt':
        report=json.loads(paths['report'].read_bytes()); report['restoration']['applied'][0]['copied']['rle'][0]+=1
        paths['report'].write_text(json.dumps(report))
    else:
        fs=cells(paths)
        if mutation=='freeze': fs=[fs[0]]*len(fs)
        elif mutation=='wrong_time': fs[r['target']]=fs[r['target']-1]
        elif mutation=='colour': ImageDraw.Draw(fs[r['target']]).rectangle((22,25,36,35),fill=(0,255,0))
        else: fs[r['target']].putpixel((32,32),(255,0,0,0))
        write_strip(paths,fs)
    # An unchanged evidence JSON cannot authorize different final bytes.
    with pytest.raises(ValueError): compare_result(case,r)
    if mutation=='freeze':
        # The WebP encoder collapses a frozen sequence to a still with no duration.
        with pytest.raises(ValueError,match='duration'): restoration.read_loop(paths)
        return
    # Even rewritten receipts cannot conceal actual final pixels/schedule.
    c,_=restoration.read_loop(paths); r['candidate_artifacts']=c.artifacts
    verdict=compare_result(case,r)['verdict']
    assert verdict == 'improved' if mutation=='report' else verdict in ('unknown','regressed')


def test_actual_playback_mismatch_and_byte_hashes(case,tmp_path):
    r=repair(case,tmp_path)
    paths=outputs(r)
    # Same strip, differently timed WebP: even a rewritten receipt is rejected.
    c,_=restoration.read_loop(paths)
    loop.write_webp(c.frames,paths['webp'],delay_ms=60,workdir=tmp_path/'bad-webp')
    c,_=restoration.read_loop(paths); r['candidate_artifacts']=c.artifacts
    assert compare_result(case,r)['verdict']=='unknown'
    for key,p in paths.items(): assert c.artifacts[key]['sha256']==evidence.digest(p.read_bytes())


def cli_args(case):
    paths,inputs,_=case
    baseline=[a for k,p in paths.items() for a in ('--baseline-'+k,str(p))]
    origin=[a for k,p in paths.items() for a in ('--origin-'+k,str(p))]
    return baseline,origin,[a for k,p in inputs.items() for a in ('--'+k.replace('_','-'),str(p))]


def test_cli_real_json_needs_the_origin(case,tmp_path,capsys):
    baseline,origin,src=cli_args(case)
    report=tmp_path/'repair.json'
    # A request without the origin is refused: the current baseline is never taken for it.
    with pytest.raises(SystemExit):
        main(['video-loop-repair',*baseline,*src,'--out-dir',str(tmp_path/'guessed'),'--report',str(report)])
    assert not (tmp_path/'guessed').exists() and not report.exists()
    assert main(['video-loop-repair',*baseline,*origin,*src,'--out-dir',str(tmp_path/'cli'),'--report',str(report)])==0
    r=json.loads(report.read_bytes())
    assert (r['schema_version'],r['metric_version'],r['policy_version'])==(REPAIR_SCHEMA,METRIC,POLICY)
    candidate=[a for k,p in r['outputs'].items() for a in ('--candidate-'+k,p)]
    comparison=tmp_path/'comparison.json'
    with pytest.raises(SystemExit,match='origin'):
        main(['video-loop-compare',*baseline,*candidate,*src,'--repair-evidence',str(report),'--report',str(comparison)])
    assert not comparison.exists()
    assert main(['video-loop-compare',*baseline,*origin,*candidate,*src,'--repair-evidence',str(report),'--report',str(comparison)])==0
    c=json.loads(comparison.read_bytes())
    assert c['verdict']=='improved' and c['schema_version']==COMPARISON_SCHEMA and c['metric_version']==METRIC
    assert set(c['candidate']['artifacts'])==set(c['origin']['artifacts'])=={'strip','meta','report','gif','webp'}
    assert c['origin']['artifacts']==r['origin_artifacts']


def test_key_spill_stays_out_and_the_rest_of_the_cell_is_restored(case,tmp_path):
    # A real source pixel can still be bad: copying it would bring key tint the delivered cell lacks.
    # Those places keep the delivered pixel; every other differing place is the source's.
    tinted=repaint_source(case,2,lambda f: ImageDraw.Draw(f).rectangle((25,25,28,28),fill=(180,205,120)))
    outcomes={r['target']:r for r in (repair(case,tmp_path,k) for k in (1,2))}
    assert {r['comparison']['verdict'] for r in outcomes.values()}=={'improved'}
    r=outcomes[2]
    assert r['partial']['protected']['count']==16 and r['partial']['alpha_equals_source']
    assert r['partial']['protected_pixels']['alpha_conflicts']==0 and r['partial']['capped']['count']==0
    assert r['partial']['protected_pixels']['pixels'][0]=={
        'x':25,'y':25,'origin_rgba':list(FILL),'source_rgba':[180,205,120,255],
        'origin_key_weight':0,'source_key_weight':255*(205-180-8),
        'source_key_excess':205-180,'source_window_key_weight':255*(205-180-8),'alpha_equal':True}
    made,before,tinted=cells(outputs(r))[2],cells(case[0])[2],cleaned(tinted)
    for y in range(H):
        for x in range(W):
            kept=25<=x<=28 and 25<=y<=28
            assert made.getpixel((x,y))==(before if kept else tinted).getpixel((x,y))
    key=r['comparison']['axes']['key_colour']
    assert key['introduced_excess'][2]==0 and key['introduced_pixels'][2]==0 and key['tint_threshold']==8
    assert r['comparison']['cleared_faults']==[{'cell':2,'faults':['outline']}]


def test_protected_pixel_with_other_coverage_ends_that_proposal_only(case,tmp_path):
    # The delivered cell lost its part-covered line (solid fill there); the source's line is key tinted.
    # Keeping the delivered pixel would keep coverage the source does not have, so nothing is written.
    y=14+[0,1,2,1,0,-1][2]
    repaint_source(case,2,lambda f: f.putpixel((15,y),(30,60,30,170)))
    outcomes={r['target']:r for r in (repair(case,tmp_path,k) for k in (1,2))}
    r=outcomes[2]
    assert (r['status'],r['common_failure'],r['reasons'],r['outputs'])==('unknown',False,['protected-pixel-alpha-conflict'],{})
    assert r['proposal_id'] and 'comparison' not in r and 'candidate_artifacts' not in r
    assert r['partial']['protected_pixels']=={'count':1,'alpha_conflicts':1,'listed':1,'pixels':[{
        'x':15,'y':y,'origin_rgba':list(FILL),'source_rgba':[30,60,30,170],
        'origin_key_weight':0,'source_key_weight':170*(60-30-8),
        'source_key_excess':60-30,'source_window_key_weight':170*(60-30-8),'alpha_equal':False}]}
    assert not (tmp_path/f"candidate-{r['proposal_index']}").exists()
    # The other proposal is independent of it.
    assert outcomes[8]['comparison']['verdict']=='improved'
    assert repair(case,tmp_path,3)['status']=='exhausted'


def test_colours_beside_the_key_hue_are_copied_and_a_key_accent_is_left(case,tmp_path):
    # Yellow, cyan, red and blue are not the green key's hue; a green accent is.
    colours={(20,20):(255,255,0),(22,20):(0,255,255),(24,20):(255,0,0),(26,20):(80,80,255),(28,20):(60,200,60)}
    def paint(f):
        for place,colour in colours.items(): f.putpixel(place,colour+(255,))
    repaint_source(case,2,paint)
    r=next(r for r in (repair(case,tmp_path,k) for k in (1,2)) if r['target']==2)
    assert r['comparison']['verdict']=='improved'
    made=cells(outputs(r))[2]
    for place,colour in colours.items():
        assert made.getpixel(place)==(FILL if colour==(60,200,60) else colour+(255,))
    assert r['partial']['protected']['count']==1


def test_less_total_key_spill_cannot_hide_new_local_key_spill(case,tmp_path):
    paths,inputs,_=case
    # More green is removed at one location than the source would bring at a different one.
    # A summed colour score would allow the whole source cell.
    with Image.open(paths['strip']) as im: strip=im.convert('RGBA')
    d=ImageDraw.Draw(strip)
    for k in (2,8): d.rectangle((k*64+20,22,k*64+28,28),fill=(80,240,60))
    fs=[strip.crop((k*64,0,(k+1)*64,80)) for k in range(12)];write_strip(paths,fs)
    for k in (2,8): repaint_source(case,k,lambda f: ImageDraw.Draw(f).rectangle((35,35,36,36),fill=(180,210,110)))
    r=repair(case,tmp_path)
    outcome=r['comparison']
    key=outcome['axes']['key_colour'];target=outcome['preservation']['changed_cells'][0]
    assert key['candidate'][target]<key['baseline'][target]
    assert key['introduced_excess'][target]==0 and outcome['verdict']=='improved'
    assert r['partial']['protected']['count']==4
    # The whole source cell at that time is still refused by the guard on final pixels.
    o,_=restoration.read_loop(paths)
    src=source.Source.read(**inputs)
    state,_=restoration.chain(o,o,src)
    whole=[state.projection.frames[k] if k==target else f for k,f in enumerate(o.frames)]
    judged=restoration.judge(o.frames,whole,state.projection.frames,state.key,state.facing,state.screen)
    assert sum(f for f in judged['axes']['key_colour']['candidate'])<sum(judged['axes']['key_colour']['baseline'])
    assert judged['axes']['key_colour']['introduced_pixels'][target]==4
    assert judged['verdict']=='regressed' and 'key_colour:regressed' in judged['reasons']
    # Written out as a candidate, it is not the verified copy either.
    forged=outputs(r)
    write_strip(forged,whole)
    c,_=restoration.read_loop(forged); r['candidate_artifacts']=c.artifacts
    assert compare_result(case,r)['reasons']==['changed-cell-is-not-the-verified-source-copy']


def ghost_band(f):
    """A filmed ghost under the body of frame 2, between its feet: a band of part coverage whose sides the dark
    feet outline, so it carries no smear and no outline fault against the frames beside it."""
    for y in range(60,72):
        for x in range(29,37):
            if f.getpixel((x,y))[3]==0: f.putpixel((x,y),(240,225,220,77))


def test_a_cell_whose_source_frame_is_a_filmed_ghost_is_never_restored(tmp_path):
    # Cell 2 is the frame the jump repair gave a filmed ghost way for, and it lost its outline at the cell's size.
    # The ghost passes the smear and outline faults, so source-restoration-v4 proposed the cell and took the ghost
    # back. The ghost screen of the cut's source frames names it: no target, and a ghost there regresses.
    case=build(tmp_path,paint={2:ghost_band},unpainted=(2,))
    paths,inputs,_=case
    first=repair(case,tmp_path)
    assert (first['target'],first['proposals_available'])==(8,1)  # before: cell 2 proposed too, the ghost restored
    assert first['comparison']['verdict']=='improved', first['comparison']['reasons']
    axis=first['comparison']['axes']['ghost']
    assert [g['frame'] for g in axis['filmed']['ghosts']]==[2] and axis['status']=='non_regressing'
    assert axis['baseline'][2]<=GHOST_WARN<axis['reference'][2]
    assert repair(case,tmp_path,2)['status']=='exhausted'
    # The source frame written into the cell is the ghost again: the ghost axis regresses.
    o,_=restoration.read_loop(paths)
    state,_=restoration.chain(o,o,source.Source.read(**inputs))
    whole=[state.projection.frames[k] if k==2 else f for k,f in enumerate(o.frames)]
    judged=restoration.judge(o.frames,whole,state.projection.frames,state.key,state.facing,state.screen)
    assert judged['axes']['ghost']['status']=='regressed' and judged['verdict']=='regressed'
    assert 'ghost:regressed' in judged['reasons']


def test_partial_copy_formula():
    def image(pixels):
        im=Image.new('RGBA',(len(pixels),1))
        for x,pixel in enumerate(pixels): im.putpixel((x,0),pixel)
        return im
    def made(origin,reference,key='green'):
        # A cell at its frame's size is a copy: every excess in it is the source's own, so none is capped.
        part=restoration.partial(image(origin),image(reference),image(reference),key)
        assert not part.capped.any()
        return part,[part.frame.getpixel((x,0)) for x in range(len(origin))]
    # A larger drop at one place does not pay for a rise at another.
    part,pixels=made([(100,110,100,255),(100,108,100,255)],[(100,108,100,255),(100,109,100,255)])
    assert pixels==[(100,108,100,255),(100,108,100,255)]
    assert part.copied.tolist()==[[True,False]] and part.protected.tolist()==[[False,True]] and not part.alpha_conflict
    # The same rise where the coverage also differs cannot keep the delivered pixel.
    assert made([(100,108,100,128)],[(100,109,100,255)])[0].alpha_conflict
    # The excess is weighted by coverage: more hue under less coverage can be less of it.
    origin,thin,thick=(100,120,100,255),(100,140,100,90),(100,140,100,100)
    assert made([origin],[thin])[1]==[thin]
    assert made([origin],[thick])[0].alpha_conflict
    # A colour under zero coverage has no excess on either side; the pixel is copied whole.
    hidden=(0,255,0,0)
    assert made([hidden],[(90,80,70,255)])[1]==[(90,80,70,255)]
    assert made([(90,80,70,255)],[hidden])[1]==[hidden]
    # Magenta's hue is min(R, B) - G; green's is G - max(R, B).
    accent=(200,60,200,255)
    assert made([FILL],[accent],'magenta')[1]==[FILL]
    assert made([FILL],[accent],'green')[1]==[accent]


def seam(left,right):
    """A 16 px picture whose columns 0-6 are one colour and the rest another: halved, column 3 mixes them evenly."""
    im=Image.new('RGBA',(16,16),right)
    im.paste(left,(0,0,7,16))
    return im


def halved(im):
    return resize_cell(im,(im.width//2,im.height//2))


COLUMN=[[x==3 for x in range(8)]]*8


@pytest.mark.parametrize('key,left,right,mixed,capped',[
    ('green',WARM,COOL,(105,114,105,255),(105,113,105,255)),
    # Magenta's hue is min(R, B) - G: both keyed channels come down one step.
    ('magenta',ROSE,IRIS,(109,100,109,255),(108,100,108,255)),
    # A part-covered side: the mix is weighted by coverage, and the capped pixel keeps the source's.
    ('green',(126,120,103,255),(81,120,126,127),(111,120,111,191),(111,119,111,191))])
def test_one_step_the_resample_made_is_capped(key,left,right,mixed,capped):
    drawn=seam(left,right); ref=halved(drawn)
    origin=Image.new('RGBA',ref.size,FILL)
    # No source pixel is over the bar; the cell's mixed column is exactly one step over it.
    assert {ref.getpixel((3,y)) for y in range(8)}=={mixed}
    assert int(restoration.source_key_weight(drawn,ref.size,key).max())==0
    assert int(restoration.key_weight(np.asarray(ref),key)[:,3].min())==mixed[3]
    part=restoration.partial(origin,ref,drawn,key)
    assert part.capped.tolist()==COLUMN and not part.protected.any() and not part.alpha_conflict
    assert part.copied.tolist()==[[x!=3 for x in range(8)]]*8
    for y in range(8):
        for x in range(8):
            assert part.frame.getpixel((x,y))==(capped if x==3 else ref.getpixel((x,y)))
    assert not restoration.key_weight(np.asarray(part.frame),key).any()
    # The other key does not read that colour as its hue: the whole source cell is copied.
    other=restoration.partial(origin,ref,drawn,'magenta' if key=='green' else 'green')
    assert not other.capped.any() and not other.protected.any() and other.frame.tobytes()==ref.tobytes()
    # The cap answers a rise only. Where the delivered pixel already carries more, the source's is copied as it is.
    stained=Image.new('RGBA',ref.size,(100,140,100,255) if key=='green' else (140,100,140,255))
    kept=restoration.partial(stained,ref,drawn,key)
    assert kept.copied.all() and not kept.capped.any() and kept.frame.tobytes()==ref.tobytes()


@pytest.mark.parametrize('key,left,right,dark',[
    ('green',(120,115,90,255),(90,115,120,255),None),    # two steps
    ('green',(240,230,40,255),(40,230,240,255),None),    # yellow meets cyan: the mix is a bright green
    ('magenta',(230,60,60,255),(60,60,230,255),None),    # red meets blue
    ('green',(240,230,40,255),(40,230,240,255),(5,6))])  # a dark pixel under the mix does not make a deep cap a small one
def test_a_deeper_made_excess_keeps_the_delivered_pixel(key,left,right,dark):
    drawn=seam(left,right)
    if dark: drawn.putpixel(dark,(20,20,20,255))
    ref=halved(drawn)
    part=restoration.partial(Image.new('RGBA',ref.size,FILL),ref,drawn,key)
    # Nothing under those places is over the bar either: it is the depth alone that refuses the cap.
    assert int(restoration.source_key_weight(drawn,ref.size,key).max())==0
    assert not part.capped.any() and part.protected.tolist()==COLUMN and not part.alpha_conflict
    assert all(part.frame.getpixel((3,y))==FILL for y in range(8))


def test_an_excess_a_source_pixel_carries_is_not_capped():
    origin=Image.new('RGBA',(8,8),FILL)
    # A key-coloured source pixel, barely covered, under two of the mixed places. Those two still read
    # one step over the bar, but the excess is the source's own there: the delivered pixel stays, and
    # its coverage is not the source's.
    drawn=seam(WARM,COOL); drawn.putpixel((5,6),(60,200,60,1))
    ref=halved(drawn)
    part=restoration.partial(origin,ref,drawn,'green')
    assert [ref.getpixel((3,y))[:3] for y in (2,3)]==[(105,114,105)]*2
    assert part.protected.tolist()==[[x==3 and y in (2,3) for x in range(8)] for y in range(8)]
    assert part.capped.tolist()==[[x==3 and y not in (2,3) for x in range(8)] for y in range(8)]
    assert part.alpha_conflict
    # Under zero coverage the same colour carries nothing: every mixed place is capped, with the source's coverage.
    drawn.putpixel((5,6),(60,200,60,0))
    ref=halved(drawn)
    part=restoration.partial(origin,ref,drawn,'green')
    assert part.capped.tolist()==COLUMN and not part.protected.any() and not part.alpha_conflict
    assert ref.getpixel((3,3))[3]<255 and part.frame.getchannel('A').tobytes()==ref.getchannel('A').tobytes()


def recolour_outline(colour):
    def paint(f):
        for y in range(8,58):
            for x in range(W):
                if f.getpixel((x,y))[:3]==(20,20,20): f.putpixel((x,y),colour)
    return paint


def test_protected_outline_leaves_the_fault_and_is_not_adopted(case,tmp_path):
    # The source's outline is dark and key tinted: every outline pixel is protected, with the same coverage.
    # The copy then restores too little to clear the fault, and nothing else makes it an improvement.
    repaint_source(case,2,recolour_outline((20,40,20,255)))
    outcomes={r['target']:r for r in (repair(case,tmp_path,k) for k in (1,2))}
    r=outcomes[2]
    assert r['status']=='candidate' and r['partial']['alpha_equals_source'] and r['partial']['protected']['count']>100
    assert (r['comparison']['verdict'],r['comparison']['reasons'])==('unknown',['interpolation-fault-not-cleared'])
    assert r['comparison']['axes']['key_colour']['introduced_pixels'][2]==0
    assert outcomes[8]['comparison']['verdict']=='improved'


def test_source_cell_with_its_own_fault_is_not_proposed(case,tmp_path):
    repaint_source(case,2,recolour_outline(FILL))
    first=repair(case,tmp_path,1)
    assert (first['target'],first['proposals_available'])==(8,1)
    assert repair(case,tmp_path,2)['status']=='exhausted'


def test_source_that_does_not_reproduce_the_normal_cells_is_shared_unknown(case,tmp_path):
    # Another extraction of the same clip is another source unless every normal cell is reproduced exactly.
    repaint_source(case,5,lambda f: f.putpixel((30,40),(200,150,100,255)))
    r=repair(case,tmp_path)
    assert (r['status'],r['common_failure'],r['reasons'],r['outputs'])==(
        'unknown',True,['legacy-source-correspondence-unverified'],{})
    assert not (tmp_path/'candidate-1').exists()


def test_missing_legacy_recipe_is_shared_unknown(case,tmp_path):
    paths,_,_=case
    m=json.loads(paths['meta'].read_bytes()); m.pop('source_rect'); paths['meta'].write_text(json.dumps(m))
    r=json.loads(paths['report'].read_bytes()); r['strip']=m; paths['report'].write_text(json.dumps(r))
    result=repair(case,tmp_path)
    assert result['status']=='unknown' and result['common_failure']
    assert result['reasons']==['legacy-projection-recipe-missing']
    assert not (tmp_path/'candidate-1').exists()


def test_native_before_repair_receipt_is_checked(case,tmp_path):
    paths,_,clean=case
    r=json.loads(paths['report'].read_bytes())
    r['source_projection']={'recipe':source.RECIPE,'wrap_dx_px':0,'before_repair_pixels_sha256':[evidence.digest(f.tobytes()) for f in clean]}
    paths['report'].write_text(json.dumps(r))
    assert repair(case,tmp_path)['comparison']['verdict']=='improved'
    r['source_projection']['before_repair_pixels_sha256'][2]='a'*64
    paths['report'].write_text(json.dumps(r))
    result=repair(case,tmp_path,out='rejected')
    assert result['reasons']==['source-processing-receipt-mismatch'] and result['common_failure']


def test_overwriting_any_input_is_refused(case,tmp_path):
    paths,_,_=case
    baseline,origin,src=cli_args(case)
    old=paths['meta'].read_bytes()
    with pytest.raises(SystemExit,match='cannot overwrite'):
        main(['video-loop-repair',*baseline,*origin,*src,'--out-dir',str(tmp_path/'unsafe'),'--report',str(paths['meta'])])
    assert paths['meta'].read_bytes()==old and not (tmp_path/'unsafe').exists()


def test_legacy_ambiguous_projection_cannot_choose_one_crop():
    # Severe downscaling erases differences between several integer crops.
    # A single best-looking crop is insufficient evidence.
    from sprite_gen.util.resample import resize_cell
    im=Image.new('RGBA',(64,64))
    ImageDraw.Draw(im).rectangle((16,16,47,63),fill=(180,120,60,8))
    src=source.Source([im.copy() for _ in range(6)],
                      {'fps':24.,'source':{},'frames':[{'pts_seconds':k/24} for k in range(6)]},{})
    m={'frames':6,'w':2,'h':2,'delay_ms':41.67,'cycle_seconds':.25,'cycle_frames':6,'subsampled':False,'loop':True,
       'scale':round(2/64,4),'foot_anchor':'none','body_height_target':2,'body_src_h':64,'cell_height_cap':10,'top_margin_px':8}
    r={'kind':'sprite-gen-video-loop-report','status':'passed','anchor':'none','fps':24.,'frames_total':6,'cycle':{'start':0,'length':6},'strip':m}
    cell=resize_cell(im.crop((8,8,56,64)),(2,2))
    b=Loop([cell.copy() for _ in range(6)],m,r,{},41.67)
    p,reason=source.project(b,src)
    assert p is None and reason=='legacy-projection-ambiguous'


@pytest.fixture
def adopted(case,tmp_path):
    """A first request whose restored cell keeps protected pixels, adopted as the next baseline."""
    repaint_source(case,2,lambda f: ImageDraw.Draw(f).rectangle((25,25,28,28),fill=(180,205,120)))
    origin=case[0]
    first=next(r for r in (repair(case,tmp_path,k) for k in (1,2)) if r['target']==2)
    assert first['comparison']['verdict']=='improved' and first['partial']['protected']['count']==16
    return origin,first,outputs(first)


def test_later_request_rebuilds_the_new_baseline_from_the_origin(case,tmp_path,adopted):
    origin,first,current=adopted
    # The restored cell is not the source's whole cell, and is still verified: it is rebuilt from the origin.
    second=repair(case,tmp_path,baseline=current,origin=origin,out='next-request')
    assert second['comparison']['verdict']=='improved', second['comparison']['reasons']
    assert (second['target'],second['applied_cells'])==(8,[2])
    assert second['baseline_artifacts']==first['candidate_artifacts']
    assert second['origin_artifacts']==first['origin_artifacts']
    assert second['proposal_id']!=first['proposal_id']
    assert second['comparison']['preservation']['changed_cells']==[8]
    assert [e['target'] for e in second['comparison']['provenance']['applied']]==[2]
    receipt=json.loads(outputs(second)['report'].read_bytes())['restoration']
    assert [e['target'] for e in receipt['applied']]==[2,8]
    assert receipt['applied'][0]==json.loads(current['report'].read_bytes())['restoration']['applied'][0]
    assert receipt['applied'][1]['baseline_artifacts']==first['candidate_artifacts']
    # A separate comparison of the final files agrees with the preview.
    assert compare_result(case,second,baseline=current,origin=origin)['verdict']=='improved'
    # Once all supported damage has been restored, the next request copies its baseline.
    final=outputs(second)
    third=repair(case,tmp_path,baseline=final,origin=origin,out='all-restored')
    assert third['status']=='no_change' and third['comparison']['verdict']=='non_regressing'
    assert third['applied_cells']==[2,8] and third['target'] is None
    assert all(outputs(third)[k].read_bytes()==final[k].read_bytes() for k in final)


def rewrite_receipt(current, change):
    report=json.loads(current['report'].read_bytes()); change(report); current['report'].write_text(json.dumps(report))


def repaint_cell(current, index, place, colour):
    fs=cells(current); fs[index].putpixel(place,colour); write_strip(current,fs)


REBUILT='not reproduced by the origin'; PIXELS='pixels differ from the origin rebuilt'; PREFIX='not a proposal of its prefix'
OUTSIDE='differs from the origin outside its restoration receipt'


@pytest.mark.parametrize('tamper,refusal',[
    ('mask_run',REBUILT),('mask_digest',REBUILT),('output_digest',REBUILT),
    ('restored_pixel',PIXELS),('protected_pixel',PIXELS),('normal_pixel',PIXELS),
    ('list_emptied','lists no applied cell'),('receipt_removed','carries no restoration receipt'),
    ('duplicate',PREFIX),('out_of_range',PREFIX),('first_baseline','another baseline than its prefix'),
    ('extra_field','differs from the one the origin and the source reproduce'),
    ('meta_bytes',OUTSIDE),('report_field',OUTSIDE)])
def test_changed_current_baseline_or_receipt_is_error(case,tmp_path,adopted,tamper,refusal):
    origin,first,current=adopted
    entry=lambda report: report['restoration']['applied'][0]
    if tamper=='mask_run': rewrite_receipt(current,lambda r: entry(r)['copied']['rle'].__setitem__(0,entry(r)['copied']['rle'][0]+1))
    elif tamper=='mask_digest': rewrite_receipt(current,lambda r: entry(r)['protected'].update(sha256='0'*64))
    elif tamper=='output_digest': rewrite_receipt(current,lambda r: entry(r).update(output_rgba_sha256='0'*64))
    elif tamper=='restored_pixel': repaint_cell(current,2,(30,40),(200,150,100,255))
    # One protected place quietly given the source's pixel: fewer protected pixels than the origin allows.
    elif tamper=='protected_pixel': repaint_cell(current,2,(25,25),(180,205,120,255))
    elif tamper=='normal_pixel': repaint_cell(current,5,(30,40),(200,150,100,255))
    elif tamper=='list_emptied': rewrite_receipt(current,lambda r: r['restoration'].update(applied=[]))
    elif tamper=='receipt_removed': rewrite_receipt(current,lambda r: r.pop('restoration'))
    elif tamper=='duplicate': rewrite_receipt(current,lambda r: r['restoration']['applied'].append(entry(r)))
    elif tamper=='out_of_range': rewrite_receipt(current,lambda r: entry(r).update(target=99))
    elif tamper=='first_baseline': rewrite_receipt(current,lambda r: entry(r)['baseline_artifacts']['strip'].update(sha256='0'*64))
    elif tamper=='extra_field': rewrite_receipt(current,lambda r: r['restoration'].update(verified=True))
    elif tamper=='meta_bytes': current['meta'].write_text(current['meta'].read_text()+'\n')
    else: rewrite_receipt(current,lambda r: r.update(producer={'implementation_sha256':'0'*64}))
    with pytest.raises(ValueError,match=refusal):
        repair(case,tmp_path,baseline=current,origin=origin,out='next-request')
    assert not (tmp_path/'next-request').exists()


def test_replaced_origin_or_source_is_error(case,tmp_path,adopted):
    origin,first,current=adopted
    paths,inputs,_=case
    # The current baseline offered as its own origin.
    with pytest.raises(ValueError,match='origin carries a restoration receipt'):
        repair(case,tmp_path,baseline=current,origin=current,out='own-origin')
    # One origin file changed: not the origin the receipt names.
    other={k:tmp_path/('other'+p.suffix if k!='report' else 'other.loop.json') for k,p in origin.items()}
    for k,p in origin.items(): shutil.copyfile(p,other[k])
    other['meta'].write_text(other['meta'].read_text()+'\n')
    with pytest.raises(ValueError,match='another origin or source'):
        repair(case,tmp_path,baseline=current,origin=other,out='other-origin')
    # A first request must start at its origin.
    with pytest.raises(ValueError,match='carries no restoration receipt'):
        repair(case,tmp_path,baseline=other,origin=origin,out='unrelated-first')
    # Another source: one keyed frame changed and receipted again.
    p=inputs['source_frames_dir']/'frame-0005.png'
    with Image.open(p) as image: frame=image.convert('RGBA')
    frame.putpixel((30,40),(200,150,100,255)); frame.save(p)
    record=source.manifest(clip=inputs['source_clip'],canvas=inputs['source_canvas'],frames_report=inputs['source_frames_report'],
                           files=sorted(inputs['source_frames_dir'].glob('*.png')),timestamps=source.timestamps(inputs['source_clip'],0))
    inputs['source_manifest'].write_text(json.dumps(record))
    with pytest.raises(ValueError,match='another origin or source'):
        repair(case,tmp_path,baseline=current,origin=origin,out='other-source')
    assert not any((tmp_path/d).exists() for d in ('own-origin','other-origin','unrelated-first','other-source'))


def test_other_policy_receipt_is_not_read_as_this_one(case,tmp_path,adopted):
    origin,first,current=adopted
    for change in (lambda r: r['restoration'].update(policy_version='another-policy'),
                   # The released contract before the ghost guard: it could restore a filmed ghost the cut gave way.
                   lambda r: r['restoration'].update(metric_version='source-restoration-v4',
                                                     policy_version='key-protected-source-copy-one-step-cap-jump-guard-v1'),
                   # The released contract before the jump guard: its cells were accepted without it.
                   lambda r: r['restoration'].update(metric_version='source-restoration-v3',
                                                     policy_version='key-protected-source-copy-one-step-cap-v1'),
                   # The same cell under the contract before this one, which never capped: its receipt is not this one's.
                   lambda r: r['restoration'].update(metric_version='source-restoration-v2',policy_version='key-protected-source-copy-v1'),
                   lambda r: r.update(restoration={'operation':'restore_active_cut','metric_version':'source-restoration-v1',
                                                   'target':2,'restored_cells':[2]})):
        rewrite_receipt(current,change)
        result=repair(case,tmp_path,baseline=current,origin=origin,out='other-policy')
        assert (result['status'],result['common_failure'],result['reasons'],result['outputs'])==(
            'unknown',True,['restoration-receipt-policy-unsupported'],{})
        assert not (tmp_path/'other-policy').exists()


def test_old_proposal_cannot_be_compared_on_the_new_baseline(case,tmp_path,adopted):
    origin,first,current=adopted
    second=repair(case,tmp_path,baseline=current,origin=origin,out='next-request')
    # The first request's evidence, or its candidate, against the adopted baseline.
    with pytest.raises(ValueError,match='does not bind'):
        compare_result(case,first,baseline=current,origin=origin,candidate=outputs(second))
    with pytest.raises(ValueError,match='does not bind'):
        compare_result(case,second,baseline=case[0],origin=origin)
    # Evidence of another schema or metric is not this contract's.
    for field,value in (('schema_version',2),('schema_version',3),('metric_version','source-restoration-v2'),
                        ('policy_version','key-protected-source-copy-v1'),('metric_version','source-restoration-v3'),
                        ('metric_version','source-restoration-v4'),('policy_version','key-protected-source-copy-one-step-cap-jump-guard-v1'),
                        ('policy_version','key-protected-source-copy-one-step-cap-v1'),('policy_version','another-policy')):
        with pytest.raises(ValueError,match='does not bind'):
            compare_result(case,{**second,field:value},baseline=current,origin=origin)
    # A candidate whose receipt drops the earlier cell is not the verified one.
    forged=outputs(second)
    rewrite_receipt(forged,lambda r: r['restoration']['applied'].pop(0))
    c,_=restoration.read_loop(forged)
    outcome=compare_result(case,{**second,'candidate_artifacts':c.artifacts},baseline=current,origin=origin)
    assert (outcome['verdict'],outcome['reasons'])==('unknown',['candidate-report-differs-from-verified-receipt'])


def shifted(dx):
    """The whole drawing moved `dx` pixels sideways: the same lines, fills and coverage, all of it somewhere else."""
    def paint(f):
        moved=Image.new('RGBA',f.size); moved.paste(f,(dx,0)); f.paste(moved)
    return paint


@pytest.mark.parametrize('facing',['right','left'])
def test_the_jump_around_a_restored_cell_is_the_jump_repairs_own_score(tmp_path,facing):
    # The melted in-between filled the part-covered line, so the steps into and out of it changed more
    # coverage than the source's do. The restored cell gives those steps back the source's.
    case=build(tmp_path,facing=facing)
    paths,_,clean=case
    r=repair(case,tmp_path)
    c=r['comparison']
    assert c['verdict']=='improved', c['reasons']
    axis,target=c['axes']['jump'],r['target']
    # Both loops scored on the source cells' box, with the facing the jump repair recorded.
    box=repair_mod.union_box(clean)
    a,b=(repair_mod.jump_scores(cells(p),facing=facing,box=box) for p in (paths,outputs(r)))
    assert (axis['facing'],axis['hair_box'],axis['box'])==(facing,list(repair_mod.hair_box(facing)),list(box))
    assert (axis['baseline'],axis['candidate'])==(a['score'].tolist(),b['score'].tolist())
    assert axis['whole']=={'baseline':a['whole'].tolist(),'candidate':b['whole'].tolist()}
    assert axis['hair']=={'baseline':a['hair'].tolist(),'candidate':b['hair'].tolist()}
    # A cell reads the larger of its two steps, the step into it and the step out of it.
    steps=[target-1,target]
    assert axis['cells']==[{'cell':target,'steps':steps,'baseline':max(a['score'][steps]),'candidate':max(b['score'][steps])}]
    assert axis['cells'][0]['candidate']<=axis['cells'][0]['baseline'] and axis['status']!='regressed'


@pytest.mark.parametrize('facing',['right','left'])
def test_a_restored_cell_that_brings_its_jump_back_is_not_improved(tmp_path,facing):
    # The source frame under the melted cell is the jump the repair took out: its whole drawing stands
    # six pixels to one side of its neighbours'. Its pixels clear the melt and bring the jump back.
    case=build(tmp_path,facing=facing)
    repaint_source(case,2,shifted(6))
    outcomes={r['target']:r for r in (repair(case,tmp_path,k) for k in (1,2))}
    r=outcomes[2]
    assert r['status']=='candidate' and r['partial']['alpha_equals_source'] and r['partial']['protected']['count']==0
    c=r['comparison']
    assert (c['verdict'],c['reasons'])==('regressed',['jump:regressed'])
    # The melt is cleared and every other protection holds: the jump alone refuses the cell.
    assert c['cleared_faults']==[{'cell':2,'faults':['outline']}]
    assert all(v['status']!='regressed' for k,v in c['axes'].items() if k!='jump')
    cell=c['axes']['jump']['cells'][0]
    assert (cell['cell'],cell['steps'])==(2,[1,2]) and cell['candidate']>cell['baseline']
    assert compare_result(case,r)['verdict']=='regressed'
    # The other cell is independent of it.
    assert outcomes[8]['comparison']['verdict']=='improved'


@pytest.mark.parametrize('facing',[None,'sideways'])
def test_a_jump_repair_record_without_its_facing_is_shared_unknown(case,tmp_path,facing):
    # The jump repair records the facing its hair box was read with. Without it, which steps of a
    # replaced cell jumped is not known, so none of its cells is proposed.
    paths,_,clean=case
    rewrite_receipt(paths,lambda r: r['jump_repair'].pop('facing') if facing is None else r['jump_repair'].update(facing=facing))
    r=repair(case,tmp_path)
    assert (r['status'],r['common_failure'],r['reasons'],r['outputs'])==('unknown',True,['jump-repair-facing-unverified'],{})
    # A loop the repair left alone needs none: a request on it is the byte-exact no-op.
    write_strip(paths,clean)
    rewrite_receipt(paths,lambda r: r.update(jump_repair={'applied':False,'why':'--repair off'}))
    noop=repair(case,tmp_path,out='noop')
    assert noop['status']=='no_change' and noop['comparison']['verdict']=='non_regressing'
    assert noop['comparison']['axes']['jump']=={'facing':None,'cells':[],'status':'non_regressing','reason':'no-jump-repaired-cell'}


@pytest.mark.parametrize('scaled,left,right,mixed,capped',[
    ('green',WARM,COOL,(105,114,105,255),(105,113,105,255)),
    ('magenta',ROSE,IRIS,(109,100,109,255),(108,100,108,255))],indirect=['scaled'])
def test_cli_restores_a_resampled_cell_with_its_made_excess_capped(scaled,tmp_path,left,right,mixed,capped):
    paths,_,_=scaled
    painted={k:repaint_source(scaled,k,seam_patch(left,right)) for k in (2,8)}
    baseline,origin,src=cli_args(scaled)
    report=tmp_path/'repair.json'
    assert main(['video-loop-repair',*baseline,*origin,*src,'--out-dir',str(tmp_path/'cli'),'--report',str(report)])==0
    r=json.loads(report.read_bytes())
    assert (r['status'],r['schema_version'],r['metric_version'],r['policy_version'])==('candidate',REPAIR_SCHEMA,METRIC,POLICY)
    target,part=r['target'],r['partial']
    assert (part['capped']['count'],part['protected']['count'],part['alpha_equals_source'])==(6,0,True)
    assert part['capped_pixels']=={'count':6,'listed':6,'pixels':[
        {'x':27,'y':y,'origin_rgba':list(FILL),'source_rgba':list(mixed),'output_rgba':list(capped),
         'source_window':[53,2*y-1,56,2*y+2]} for y in range(25,31)]}
    candidate=[a for k,p in r['outputs'].items() for a in ('--candidate-'+k,p)]
    comparison=tmp_path/'comparison.json'
    assert main(['video-loop-compare',*baseline,*origin,*candidate,*src,'--repair-evidence',str(report),'--report',str(comparison)])==0
    c=json.loads(comparison.read_bytes())
    assert (c['verdict'],c['schema_version'],c['metric_version'],c['policy_version'])==('improved',COMPARISON_SCHEMA,METRIC,POLICY), c['reasons']
    key=c['axes']['key_colour']
    assert (key['introduced_pixels'][target],key['introduced_excess'][target],key['candidate'][target])==(0,0,0)
    assert c['cleared_faults']==[{'cell':target,'faults':['outline']}]
    assert c['restoration']['capped']==part['capped'] and c['restoration']['capped_pixels']==part['capped_pixels']
    # The final strip: the source's cell but for the six capped pixels, their coverage the source's; no other cell touched.
    made,before=cells(outputs(r)),cells(paths)
    reference=resize_cell(cleaned(painted[target]),(W,H))
    assert reference.getpixel((27,25))==mixed
    for y in range(H):
        for x in range(W):
            assert made[target].getpixel((x,y))==(capped if x==27 and 25<=y<=30 else reference.getpixel((x,y)))
    assert all(a.tobytes()==b.tobytes() for k,(a,b) in enumerate(zip(before,made)) if k!=target)
    receipt=json.loads(outputs(r)['report'].read_bytes())['restoration']
    assert (receipt['metric_version'],receipt['policy_version'])==(METRIC,POLICY)
    assert receipt['applied'][0]['capped']==part['capped']


def test_whole_source_cell_with_a_made_excess_is_still_refused(scaled,tmp_path):
    paths,inputs,_=scaled
    for k in (2,8): repaint_source(scaled,k,seam_patch(WARM,COOL))
    r=repair(scaled,tmp_path)
    target=r['target']
    # The guard itself is unchanged: the source's own cell there raises it at each of the six places.
    o,_=restoration.read_loop(paths)
    state,_=restoration.chain(o,o,source.Source.read(**inputs))
    whole=[state.projection.frames[k] if k==target else f for k,f in enumerate(o.frames)]
    judged=restoration.judge(o.frames,whole,state.projection.frames,state.key,state.facing,state.screen)
    assert judged['axes']['key_colour']['introduced_pixels'][target]==6
    assert judged['verdict']=='regressed' and 'key_colour:regressed' in judged['reasons']
    # Written out as the candidate, one capped place given the source's pixel back is not the verified cell.
    forged=outputs(r)
    fs=cells(forged); fs[target].putpixel((27,25),(105,114,105,255)); write_strip(forged,fs)
    c,_=restoration.read_loop(forged); r['candidate_artifacts']=c.artifacts
    outcome=compare_result(scaled,r)
    assert (outcome['verdict'],outcome['reasons'])==('regressed',['changed-cell-is-not-the-verified-source-copy'])


def test_deeper_made_excess_in_a_resampled_cell_keeps_the_delivered_pixels(scaled,tmp_path):
    for k in (2,8): repaint_source(scaled,k,seam_patch((120,115,90,255),(90,115,120,255)))
    r=repair(scaled,tmp_path)
    part=r['partial']
    assert (part['capped']['count'],part['protected']['count'],part['alpha_equals_source'])==(0,6,True)
    assert part['protected_pixels']['pixels'][0]=={
        'x':27,'y':25,'origin_rgba':list(FILL),'source_rgba':[105,115,105,255],'origin_key_weight':0,
        'source_key_weight':255*2,'source_key_excess':10,'source_window_key_weight':0,'alpha_equal':True}
    assert r['comparison']['verdict']=='improved', r['comparison']['reasons']
    made=cells(outputs(r))[r['target']]
    assert all(made.getpixel((27,y))==FILL for y in range(25,31))


def test_normal_cell_with_a_made_excess_is_left_as_delivered(tmp_path):
    # A delivered normal cell can carry such an excess itself. It is not a target and is never rewritten.
    case=build(tmp_path,zoom=2,paint={5:seam_patch(WARM,COOL)})
    paths,_,clean=case
    assert [clean[5].getpixel((27,y)) for y in range(25,31)]==[(105,114,105,255)]*6
    r=repair(case,tmp_path)
    assert r['comparison']['verdict']=='improved', r['comparison']['reasons']
    assert r['comparison']['axes']['key_colour']['baseline'][5]==r['comparison']['axes']['key_colour']['candidate'][5]==6
    assert cells(outputs(r))[5].tobytes()==clean[5].tobytes()
    # With nothing damaged the request copies the five files, that cell included.
    write_strip(paths,clean)
    noop=repair(case,tmp_path,out='noop')
    assert noop['status']=='no_change' and noop['comparison']['verdict']=='non_regressing'
    assert all(outputs(noop)[k].read_bytes()==paths[k].read_bytes() for k in paths)


@pytest.fixture
def capped_baseline(scaled,tmp_path):
    """A resampled loop whose first restored cell holds capped pixels, adopted as the next baseline."""
    for k in (2,8): repaint_source(scaled,k,seam_patch(WARM,COOL))
    first=repair(scaled,tmp_path)
    assert first['comparison']['verdict']=='improved' and first['partial']['capped']['count']==6
    return scaled[0],first,outputs(first)


def test_capped_cell_is_rebuilt_from_the_origin_by_later_requests(scaled,tmp_path,capped_baseline):
    origin,first,current=capped_baseline
    second=repair(scaled,tmp_path,baseline=current,origin=origin,out='next-request')
    assert second['comparison']['verdict']=='improved', second['comparison']['reasons']
    assert second['applied_cells']==[first['target']] and {first['target'],second['target']}=={2,8}
    assert second['baseline_artifacts']==first['candidate_artifacts'] and second['origin_artifacts']==first['origin_artifacts']
    receipt=json.loads(outputs(second)['report'].read_bytes())['restoration']
    assert [e['target'] for e in receipt['applied']]==[first['target'],second['target']]
    assert [e['capped']['count'] for e in receipt['applied']]==[6,6]
    # A separate comparison of the final files agrees with the preview.
    assert compare_result(scaled,second,baseline=current,origin=origin)['verdict']=='improved'
    final=outputs(second)
    third=repair(scaled,tmp_path,baseline=final,origin=origin,out='all-restored')
    assert third['status']=='no_change' and third['comparison']['verdict']=='non_regressing'
    assert third['applied_cells']==[first['target'],second['target']] and third['target'] is None
    assert all(outputs(third)[k].read_bytes()==final[k].read_bytes() for k in final)


@pytest.mark.parametrize('tamper,refusal',[
    ('capped_mask',REBUILT),('capped_digest',REBUILT),('capped_field_removed',REBUILT),
    ('uncapped_pixel',PIXELS),('deeper_cap',PIXELS),('cap_elsewhere',PIXELS)])
def test_changed_capped_cell_or_its_receipt_is_error(scaled,tmp_path,capped_baseline,tamper,refusal):
    origin,first,current=capped_baseline
    target=first['target']
    entry=lambda report: report['restoration']['applied'][0]
    if tamper=='capped_mask': rewrite_receipt(current,lambda r: entry(r)['capped']['rle'].__setitem__(0,entry(r)['capped']['rle'][0]+1))
    elif tamper=='capped_digest': rewrite_receipt(current,lambda r: entry(r)['capped'].update(sha256='0'*64))
    elif tamper=='capped_field_removed': rewrite_receipt(current,lambda r: entry(r).pop('capped'))
    # A capped place given the source's own pixel back, capped a step further, or a cap where none was due.
    elif tamper=='uncapped_pixel': repaint_cell(current,target,(27,25),(105,114,105,255))
    elif tamper=='deeper_cap': repaint_cell(current,target,(27,25),(105,112,105,255))
    else: repaint_cell(current,target,(26,25),(120,113,90,255))
    with pytest.raises(ValueError,match=refusal):
        repair(scaled,tmp_path,baseline=current,origin=origin,out='next-request')
    assert not (tmp_path/'next-request').exists()


def test_low_scale_legacy_search_is_bounded():
    from sprite_gen.util.resample import resize_cell
    im=Image.new('RGBA',(64,80))
    ImageDraw.Draw(im).rectangle((16,16,47,63),fill=(180,120,60))
    src=source.Source([im.copy() for _ in range(6)],{'fps':24.,'source':{},'frames':[{'pts_seconds':k/24} for k in range(6)]},{})
    m={'frames':6,'w':2,'h':2,'delay_ms':41.67,'cycle_seconds':.25,'cycle_frames':6,'subsampled':False,'loop':True,
       'scale':round(2/64,4),'foot_anchor':'none','body_height_target':2,'body_src_h':64,'cell_height_cap':10,'top_margin_px':8}
    r={'kind':'sprite-gen-video-loop-report','status':'passed','anchor':'none','fps':24.,'frames_total':6,'cycle':{'start':0,'length':6},'strip':m}
    b=Loop([resize_cell(im.crop((8,8,56,64)),(2,2)) for _ in range(6)],m,r,{},41.67)
    assert source.project(b,src)[1]=='legacy-projection-search-budget'


def test_video_frames_writes_a_receipt_of_the_actual_extraction(tmp_path):
    from sprite_gen.video.frames import run_frames
    if not shutil.which('ffmpeg'):
        pytest.skip('ffmpeg required')
    inputs=tmp_path/'input'; inputs.mkdir()
    for k,f in enumerate(frames()):
        canvas=Image.new('RGB',f.size,(0,255,0));canvas.paste(f,mask=f.getchannel('A'))
        canvas.save(inputs/f'frame-{k:04}.png')
    clip=tmp_path/'clip.mp4'
    subprocess.run(['ffmpeg','-v','error','-framerate','24','-i',str(inputs/'frame-%04d.png'),'-c:v','libx264','-pix_fmt','yuv420p',str(clip)],check=True)
    report=tmp_path/'frames.json'; manifest=tmp_path/'receipts/source.json'
    run_frames(clip,tmp_path/'output',key='green',allow_edge_contact=False,report_path=report,
               reference=inputs/'frame-0000.png',source_manifest=manifest)
    src=source.Source.read(source_clip=clip,source_canvas=inputs/'frame-0000.png',source_frames_report=report,
                           source_manifest=manifest,source_frames_dir=tmp_path/'output/keyed')
    assert len(src.frames)==12
    assert src.record['extraction_engine']==evidence.engine_identity()
    assert src.record['inputs']['frames_report']['sha256']==evidence.digest(report.read_bytes())
