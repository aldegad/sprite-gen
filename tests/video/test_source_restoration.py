# SPDX-License-Identifier: Apache-2.0
"""Source-backed acceptance: actual bytes, fixed source time, and final exports.

These are synthetic contract fixtures, not a production repair success claim.
"""
import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from sprite_gen.video import evidence, source, restoration, playback, loop
from sprite_gen.cli import main
from sprite_gen.util.gif_utils import save_clean_gif
from sprite_gen.video.compare import Loop


def frames():
    result = []
    for k in range(12):
        im = Image.new('RGBA', (64, 80))
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


def damage(im):
    made = im.copy()
    for y in range(8, 58):
        for x in range(made.width):
            if made.getpixel((x,y))[:3] in ((20,20,20),(30,30,30)):
                made.putpixel((x,y),(210,160,110,255))
    return made


def exports(paths, fs, delay=42):
    save_clean_gif(fs, paths['gif'], duration_ms=delay, alpha_threshold=128)
    loop.write_webp(fs, paths['webp'], delay_ms=delay, workdir=paths['webp'].parent/'webp-temp')


@pytest.fixture
def case(tmp_path):
    if not shutil.which('ffmpeg') or not loop.img2webp_supports_exact():
        pytest.skip('ffmpeg and img2webp >=1.5 required')
    fs = frames()
    src_dir=tmp_path/'keyed'
    src_dir.mkdir()
    for k,f in enumerate(fs): f.save(src_dir/f'frame-{k:04}.png')
    canvas=src_dir/'frame-0000.png'
    freport=tmp_path/'frames.json'
    freport.write_text(json.dumps({'kind':'sprite-gen-video-frames-report','fps':24.,'frames':12,'width':64,'height':80,'stream_index':0,'key':'green'}))
    manifest=tmp_path/'source.json'
    inputs=dict(source_manifest=manifest,source_frames_dir=src_dir,source_canvas=canvas,source_frames_report=freport)
    mp4=tmp_path/'clip.mp4'
    subprocess.run(['ffmpeg','-v','error','-framerate','24','-i',str(src_dir/'frame-%04d.png'),'-c:v','libx264','-pix_fmt','yuv420p',str(mp4)],check=True)
    inputs['source_clip']=mp4
    manifest.write_text(json.dumps(source.manifest(clip=mp4,canvas=canvas,frames_report=freport,files=sorted(src_dir.glob('*.png')),timestamps=source.timestamps(mp4,0))))
    # The loop cleanup is part of the recorded recipe.
    from sprite_gen.video.frames import drop_specks
    clean=[]
    for f in fs:
        c=f.copy(); loop._scrub(c)
        clean.append(drop_specks(c,alpha_over=16,diagonal=False,apart=0)[0])
    bad=list(clean); bad[2]=damage(clean[2]); bad[8]=damage(clean[8])
    paths={k:tmp_path/('b'+ext) for k,ext in [('strip','.png'),('meta','.json'),('report','.loop.json'),('gif','.gif'),('webp','.webp')]}
    m={'frames':12,'w':64,'h':80,'delay_ms':41.67,'cycle_seconds':.5,'cycle_frames':12,'subsampled':False,'loop':True,'scale':1.,'source_rect':[0,0,64,80],'sample_indices':list(range(12)),'foot_anchor':'none'}
    src=source.Source.read(**inputs)
    m['source_cut']={'source':src.record['source'],'start':0,'length':12,'samples':list(range(12))}
    report={'kind':'sprite-gen-video-loop-report','status':'passed','fps':24.,'frames_total':12,'source':src.record['source'],'cycle':{'start':0,'length':12},'anchor':'none','jump_repair':{'replaced':[2,8]},'strip':m,'n_out':12,'delay_ms':42}
    paths['meta'].write_text(json.dumps(m)); paths['report'].write_text(json.dumps(report))
    sheet=Image.new('RGBA',(12*64,80))
    for k,f in enumerate(bad): sheet.paste(f,(64*k,0))
    sheet.save(paths['strip']); exports(paths,bad)
    return paths, inputs, clean


def repair(case,tmp_path,index=1):
    paths,inputs,_=case
    return restoration.restore(paths,source.Source.read(**inputs),out_dir=tmp_path/f'candidate-{index}',name='loop',proposal_index=index)


def compare_result(case,result):
    paths,inputs,_=case
    a,ap=restoration.read_loop(paths)
    b,bp=restoration.read_loop({k:Path(p) for k,p in result['outputs'].items()})
    return restoration.compare(a,b,source.Source.read(**inputs),baseline_playback=ap,candidate_playback=bp,repair_evidence=result)


def test_exact_source_restoration_preserves_normal_motion_and_timing(case,tmp_path):
    result=repair(case,tmp_path)
    assert result['status']=='candidate'
    comparison=compare_result(case,result)
    assert comparison['verdict']=='improved', comparison['reasons']
    assert comparison['metric_version']=='source-restoration-v1'
    target=result['target']
    a,_=restoration.read_loop(case[0]); b,_=restoration.read_loop({k:Path(v) for k,v in result['outputs'].items()})
    assert all(x.tobytes()==y.tobytes() for k,(x,y) in enumerate(zip(a.frames,b.frames)) if k!=target)
    assert b.frames[target].tobytes()==case[2][target].tobytes()
    axis=comparison['axes']['introduced_partial']
    assert axis['raw_candidate'][target]>axis['raw_baseline'][target]
    assert axis['candidate'][target]==0
    assert comparison['cleared_faults']==[{'cell':target,'faults':['outline']}]
    assert comparison['gait']=={'absolute':'unverified','source_order':'preserved'}
    assert comparison['playback']['baseline']['gif']['durations_ms']==[40]*12
    assert comparison['playback']['candidate']['webp']['durations_ms']==[42]*12


def test_next_independent_proposal_and_budget(case,tmp_path):
    first,second=repair(case,tmp_path,1),repair(case,tmp_path,2)
    assert first['target']!=second['target'] and first['proposal_id']!=second['proposal_id']
    assert second['comparison']['verdict']=='improved'
    assert repair(case,tmp_path,3)['status']=='exhausted'
    with pytest.raises(ValueError,match='between 1 and 3'): repair(case,tmp_path,4)
    with pytest.raises(ValueError,match='directory must be new'): repair(case,tmp_path,1)


def test_normal_source_is_noop(case,tmp_path):
    paths,_,clean=case
    sheet=Image.new('RGBA',(12*64,80))
    for k,f in enumerate(clean): sheet.paste(f,(k*64,0))
    sheet.save(paths['strip']); exports(paths,clean)
    result=repair(case,tmp_path)
    assert result['status']=='no_change' and result['comparison']['verdict']=='non_regressing'
    assert result['baseline_artifacts']==result['candidate_artifacts']


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


@pytest.mark.parametrize('mutation',['freeze','wrong_time','colour','alpha','duration','crop','report'])
def test_tampered_final_artifacts_are_not_accepted(case,tmp_path,mutation):
    r=repair(case,tmp_path)
    from pathlib import Path
    paths={k:Path(p) for k,p in r['outputs'].items()}
    if mutation in ('duration','crop'):
        m=json.loads(paths['meta'].read_bytes()); m['delay_ms' if mutation=='duration' else 'source_rect']=80 if mutation=='duration' else [1,0,65,80]
        paths['meta'].write_text(json.dumps(m))
    elif mutation=='report':
        paths['report'].write_text(paths['report'].read_text()+'\n')
    else:
        c,_=restoration.read_loop(paths)
        fs=c.frames
        if mutation=='freeze': fs=[fs[0]]*len(fs)
        elif mutation=='wrong_time': fs[r['target']]=fs[r['target']-1]
        elif mutation=='colour': ImageDraw.Draw(fs[r['target']]).rectangle((22,25,36,35),fill=(0,255,0))
        else: fs[r['target']].putpixel((32,32),(255,0,0,0))
        sheet=Image.new('RGBA',(12*64,80))
        for k,f in enumerate(fs): sheet.paste(f,(64*k,0))
        sheet.save(paths['strip']); exports(paths,fs)
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
    from pathlib import Path
    paths={k:Path(p) for k,p in r['outputs'].items()}
    # Same strip, differently timed WebP: even a rewritten receipt is rejected.
    c,_=restoration.read_loop(paths)
    loop.write_webp(c.frames,paths['webp'],delay_ms=60,workdir=tmp_path/'bad-webp')
    c,_=restoration.read_loop(paths); r['candidate_artifacts']=c.artifacts
    assert compare_result(case,r)['verdict']=='unknown'
    for key,p in paths.items(): assert c.artifacts[key]['sha256']==evidence.digest(p.read_bytes())


def test_cli_real_json_and_v1_remains_separate(case,tmp_path,capsys):
    paths,inputs,_=case
    args=[a for k,p in paths.items() for a in ('--baseline-'+k,str(p))]
    args += [a for k,p in inputs.items() for a in ('--'+k.replace('_','-'),str(p))]
    report=tmp_path/'repair.json'
    assert main(['video-loop-repair',*args,'--out-dir',str(tmp_path/'cli'),'--report',str(report)])==0
    r=json.loads(report.read_bytes())
    candidate=[a for k,p in r['outputs'].items() for a in ('--candidate-'+k,p)]
    comparison=tmp_path/'comparison.json'
    assert main(['video-loop-compare',*args,*candidate,'--repair-evidence',str(report),'--report',str(comparison)])==0
    c=json.loads(comparison.read_bytes())
    assert c['verdict']=='improved' and c['schema_version']==2
    assert set(c['candidate']['artifacts'])=={'strip','meta','report','gif','webp'}


def test_colour_tradeoff_does_not_stop_next_independent_target(case,tmp_path):
    paths,inputs,clean=case
    # A real source pixel can still be bad: restoring it may reintroduce key tint.
    # Colour at one target differs from its delivered frame but the other target is independent.
    p=inputs['source_frames_dir']/'frame-0002.png'
    with Image.open(p) as image: tinted=image.convert('RGBA')
    ImageDraw.Draw(tinted).rectangle((25,25,28,28),fill=(180,205,120))
    tinted.save(p)
    record=source.manifest(clip=inputs['source_clip'],canvas=inputs['source_canvas'],frames_report=inputs['source_frames_report'],
                           files=sorted(inputs['source_frames_dir'].glob('*.png')),timestamps=source.timestamps(inputs['source_clip'],0))
    inputs['source_manifest'].write_text(json.dumps(record))
    m=json.loads(paths['meta'].read_bytes()); m['source_cut']['source']=record['source']; paths['meta'].write_text(json.dumps(m))
    r=json.loads(paths['report'].read_bytes()); r.update(source=record['source'],strip=m); paths['report'].write_text(json.dumps(r))
    outcomes=[repair(case,tmp_path,k)['comparison'] for k in (1,2)]
    assert {o['verdict'] for o in outcomes}=={'regressed','improved'}
    bad=next(o for o in outcomes if o['verdict']=='regressed')
    assert 'key_colour:regressed' in bad['reasons']


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
    result=restoration.restore(paths,source.Source.read(**case[1]),out_dir=tmp_path/'rejected',name='loop')
    assert result['reasons']==['source-processing-receipt-mismatch'] and result['common_failure']


def test_overwriting_any_input_is_refused(case,tmp_path):
    paths,inputs,_=case
    args=[a for k,p in paths.items() for a in ('--baseline-'+k,str(p))]
    args += [a for k,p in inputs.items() for a in ('--'+k.replace('_','-'),str(p))]
    old=paths['meta'].read_bytes()
    with pytest.raises(SystemExit,match='cannot overwrite'):
        main(['video-loop-repair',*args,'--out-dir',str(tmp_path/'unsafe'),'--report',str(paths['meta'])])
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


def test_later_request_uses_new_baseline_and_preserves_previous_restoration(case,tmp_path):
    first=repair(case,tmp_path,1)
    first_paths={k:Path(p) for k,p in first['outputs'].items()}
    second=restoration.restore(first_paths,source.Source.read(**case[1]),out_dir=tmp_path/'next-request',name='loop')
    assert second['comparison']['verdict']=='improved'
    assert second['target']!=first['target']
    assert second['baseline_artifacts']==first['candidate_artifacts']
    assert second['proposal_id']!=first['proposal_id']
    assert first['target'] in second['projection']['unmodified_cells']
    assert second['comparison']['preservation']['changed_cells']==[second['target']]
    assert first['target'] in second['comparison']['preservation']['unchanged_cells']
    final_paths={k:Path(p) for k,p in second['outputs'].items()}
    third=restoration.restore(final_paths,source.Source.read(**case[1]),out_dir=tmp_path/'all-restored',name='loop')
    assert third['status']=='no_change' and third['comparison']['verdict']=='non_regressing'


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


def test_less_total_key_spill_cannot_hide_new_local_key_spill(case,tmp_path):
    paths,inputs,_=case
    # More green is removed at one location than introduced at a different one.
    # A summed colour score would incorrectly allow the tradeoff.
    with Image.open(paths['strip']) as im: strip=im.convert('RGBA')
    d=ImageDraw.Draw(strip)
    for k in (2,8): d.rectangle((k*64+20,22,k*64+28,28),fill=(80,240,60))
    strip.save(paths['strip'])
    fs=[strip.crop((k*64,0,(k+1)*64,80)) for k in range(12)];exports(paths,fs)
    for k in (2,8):
        p=inputs['source_frames_dir']/f'frame-{k:04}.png'
        with Image.open(p) as im: f=im.convert('RGBA')
        ImageDraw.Draw(f).rectangle((35,35,36,36),fill=(180,210,110));f.save(p)
    record=source.manifest(clip=inputs['source_clip'],canvas=inputs['source_canvas'],frames_report=inputs['source_frames_report'],files=sorted(inputs['source_frames_dir'].glob('*.png')),timestamps=source.timestamps(inputs['source_clip'],0))
    inputs['source_manifest'].write_text(json.dumps(record))
    m=json.loads(paths['meta'].read_bytes());m['source_cut']['source']=record['source'];paths['meta'].write_text(json.dumps(m))
    r=json.loads(paths['report'].read_bytes());r.update(source=record['source'],strip=m);paths['report'].write_text(json.dumps(r))
    outcome=repair(case,tmp_path)['comparison']
    key=outcome['axes']['key_colour'];target=outcome['preservation']['changed_cells'][0]
    assert key['candidate'][target]<key['baseline'][target]
    assert key['introduced_excess'][target]>0
    assert outcome['verdict']=='regressed' and 'key_colour:regressed' in outcome['reasons']
