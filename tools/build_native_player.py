#!/usr/bin/env python3
"""Build the experimental shared LSDj9.4.2 song interpreter, not a trace ROM.

Consumes only startup state and the32KiB song. Playback duration is not an
input and does not affect ROM size. Recovered code remains local to the build.
"""
import argparse,bisect,concurrent.futures,csv,json,math,re,subprocess
from pathlib import Path
from sm83_disassemble import instruction
from native_delay import delay_code
from song_title import write_tiles
from project_kits import used_kit_indices,kit_bank

ROOT=Path(__file__).resolve().parents[1]

def delay_source(cycles):
    data=delay_code(cycles)
    return '    db '+','.join(f'${byte:02x}' for byte in data) if data else '    ; zero-cycle delay'

def engine_source(data,executed,bank,ranges):
    lines=[];base=0x4000 if bank else 0
    for number,(begin,end) in enumerate(ranges):
        lines.append(f'SECTION "Native recovered bank{bank} range{number}", '+(f'ROMX[${base+begin:04x}], BANK[{bank}]' if bank else f'ROM0[${begin:04x}]'))
        offset=begin
        while offset<end:
            address=base+offset
            if address in executed:
                text,size=instruction(data,offset,address)
                if offset+size>end:raise ValueError('Recovery boundary splits an instruction')
                lines.append(f'    {text} ; ${address:04x}');offset+=size
            else:
                stop=offset+1
                while stop<end and stop-offset<16 and base+stop not in executed:stop+=1
                lines.append('    db '+','.join(f'${v:02x}' for v in data[offset:stop])+f' ; ${address:04x}');offset=stop
    return '\n'.join(lines)+'\n'


def _build_once(rom_path,profile,snapshot_prefix,output,name='SONG',song_delays=None,lcd_delays=None,timer_state=None,*,connect_pitch_bends=False):
    song_delays = song_delays or {'cgb':8220996, 'dmg':8547120}
    lcd_delays = lcd_delays or {'cgb':41700,'dmg':36960}
    timer_state = timer_state or {model:{'tima':tima,'tma':0x49,'tac':6,'if':0}
                                  for model,tima in [('cgb',0xaa),('dmg',0x90)]}
    output=Path(output).resolve();work=output.parent/(output.stem+'-native-data');work.mkdir(parents=True,exist_ok=True)
    rom=Path(rom_path).read_bytes();prefix=str(snapshot_prefix)
    core0=bytearray(rom[:0x4000])
    if core0[0x1889:0x188c] != bytes.fromhex('21 00 80'):raise ValueError('Unexpected tracker font update location')
    core0[0x188a:0x188c]=bytes.fromhex('80 cc') # retain cycles, redirect tracker font writes to unused WRAM
    states={m:{suffix:Path(prefix+'-'+m+'.'+suffix).read_bytes() for suffix in ['wram.bin','song.bin','hram.bin']} for m in ['dmg','cgb']}
    if states['dmg']['song.bin']!=states['cgb']['song.bin']:raise ValueError('DMG/CGB startup migration produced different song data')
    song=states['dmg']['song.bin'];(work/'song.bin').write_bytes(song)
    (work/'startup-ram.bin').write_bytes(states['dmg']['wram.bin'][:0x2000]+states['cgb']['wram.bin'][:0x2000])
    (work/'startup-hram.bin').write_bytes(states['dmg']['hram.bin']+states['cgb']['hram.bin'])
    executed={0:set(),2:set(),7:set()}
    for row in csv.DictReader(Path(profile).open(),delimiter='\t'):
        if row['bank']!='entry' and int(row['bank']) in executed:executed[int(row['bank'])].add(int(row['address'],16))
    # Tempo commands far-call bank 7's shared arithmetic/tables. Omitting
    # that bank turns a valid B command into execution of zero-filled ROM
    # and eventually a wrapping stack. It already fits the 128 KiB image.
    for bank in [0,2,7]:
        ranges=[(0,0x42),(0x45,0x100),(0x104,0xb54),(0x1306,0x4000)] if not bank else [(0,0x4000)]
        (work/f'engine{bank}.asm').write_text(engine_source(bytes(core0) if bank==0 else rom[bank*0x4000:(bank+1)*0x4000],executed[bank],bank,ranges))
    early=['MACRO NativeTimerInit','    ld a,[NativeHardware]','    cp $11','    jr nz,.dmg_timer\\@']
    for model in ('cgb','dmg'):
        if model=='dmg':early+=['.dmg_timer\\@']
        for key,address in [('tma',0xff06),('tima',0xff05),('tac',0xff07),('if',0xff0f)]:
            early += [f'    ld a,${timer_state[model][key]:02x}',f'    ldh [${address:04x}],a']
        if model=='cgb':early+=['    jr .timer_ready\\@']
    early+=['.timer_ready\\@','ENDM']
    for macro_name,cycles in [('NativeEarlyCGB',210908-80),('NativeEarlyDMG',211872-84)]:
        early += [f'MACRO {macro_name}','    db '+','.join(f'${b:02x}' for b in delay_code(cycles)), '    xor a','    ldh [$ff26],a']
        if macro_name=='NativeEarlyCGB':early+=['    db '+','.join(f'${b:02x}' for b in delay_code(164)), '    ei','    ld a,1','    ldh [$ff4d],a','    stop']
        early+=['ENDM']
    early+=['MACRO NativeSongDelay','    ld a,[NativeHardware]','    cp $11','    jr nz,.dmg_delay\\@',
            delay_source(song_delays['cgb']),
            '    jr .song_ready\\@','.dmg_delay\\@',
            delay_source(song_delays['dmg']),
            '.song_ready\\@','ENDM']
    (work/'boot.asm').write_text('\n'.join(early)+'\n'+(ROOT/'src/native/boot.asm').read_text())
    sources=[work/'engine0.asm',work/'engine2.asm',work/'engine7.asm',work/'boot.asm']
    data=f'SECTION "Native entry", ROM0[$100]\n    nop\n    jp NativeStart\n'
    # Leave the original music VBlank entry untouched. Foreground rendering
    # may skip a refresh; it must never run ahead of sequencer bookkeeping.
    data+='SECTION "Native VBlank vector", ROM0[$42]\n    jp $183a\n'
    for bank,offset in [(1,0),(3,0x4000)]:data+=f'SECTION "Native song{bank}", ROMX[$4000], BANK[{bank}]\n    INCBIN "{work / "song.bin"}",${offset:x},$4000\n'
    data+=f'SECTION "Native startup RAM", ROMX[$4000], BANK[4]\n    INCBIN "{work / "startup-ram.bin"}"\n'
    data+=f'SECTION "Native startup HRAM", ROMX[$4000], BANK[5]\n    INCBIN "{work / "startup-hram.bin"}"\n'
    for kit in used_kit_indices(song):
        bank=kit_bank(kit);path=work/f'kit-{bank}.bin';path.write_bytes(rom[bank*0x4000:(bank+1)*0x4000]);data+=f'SECTION "Native kit{bank}", ROMX[$4000], BANK[{bank}]\n    INCBIN "{path}"\n'
    (work/'data.asm').write_text(data);sources.append(work/'data.asm')
    title=work/'song-title.bin';write_tiles(title,name)
    waterfall=(ROOT/'src/waterfall.asm').read_text().replace('ROM0[$1800]','ROMX[$5800], BANK[5]').replace('WRAM0[$c920]','WRAM0[$cc20]').replace('WRAM0[$c930]','WRAM0[$cc30]').replace('WRAM0[$c940]','WRAM0[$cc40]')
    waterfall=re.sub(r'ASSERT WaterfallEnd <= \$[34]000', 'ASSERT WaterfallEnd <= $8000',waterfall)
    waterfall=waterfall.replace('ld hl,$8ff0','ld hl,$ccf0')
    waterfall=waterfall.replace('            ld a,72\n','            xor a\n') # remove retired CPU bar sprites
    waterfall=re.sub(r'WaterfallStatus::.*?(?=WaterfallCommit::)', 'WaterfallStatus::\nWaterfallBar::\n    ret\n\n',waterfall,flags=re.S)
    waterfall=waterfall.replace('        ld a,[hl]\n        or b','        call NativeRead\n        or b')
    ui=(ROOT/'src/exact_ui.asm').read_text().replace('ROM0[$0220]','ROMX[$4220], BANK[5]').replace('INCLUDE "src/waterfall.asm"',f'INCLUDE "{work / "waterfall.asm"}"')
    ui=re.sub(r'ASSERT ExactUIFontEnd <= \$[0-9a-fA-F]+','ASSERT ExactUIFontEnd <= $5800',ui)
    ui=ui.replace('ASSERT @ <= $1000','ASSERT @ <= $5800')
    ui=re.sub(r'ExactUIUpdate::.*?ExactUIUpdateEnd::', 'ExactUIUpdate::\n    ret\nExactUIUpdateEnd::',ui,flags=re.S)
    ui=re.sub(r'ExactUIValues:.*?(?=MACRO ExactUIGlyph)', 'ExactUIValues:\n    db 0\n\n',ui,flags=re.S)
    ui=re.sub(r'ExactUIWaterfallBars:.*?(?=    ASSERT @)', 'ExactUIWaterfallBars:\n    db 0\n',ui,flags=re.S)
    ui=ui.replace('        call WaterfallInit','        call WaterfallInit\n        call NativeSongInfoInit')
    # Preserve the reference LCD phase: sequencer IRQs are synchronized to it.
    final_lcd=ui.rfind('    ldh [$ff40],a')
    ui=ui[:final_lcd]+'    call NativeAlignLCD\n'+ui[final_lcd:]
    # LCD is disabled throughout ExactUIInit/WaterfallInit and their initial
    # map helper. Direct stores there avoid thousands of unnecessary waits.
    # Runtime stores retain A/flags and allow music IRQs to preempt the UI.
    initialization = {}
    for label,pattern in [('init',r'WaterfallInit::.*?(?=WaterfallBegin::)'),
                          ('map',r'WaterfallInitialMap:.*?(?=IF DEF\(EXACT_PIXEL_WATERFALL\))')]:
        match=re.search(pattern,waterfall,flags=re.S)
        if match is None:raise ValueError(f'Missing waterfall {label} initialization boundary')
        initialization[label]=match.group()
        waterfall=waterfall[:match.start()]+f'; NATIVE_INITIAL_{label}\n'+waterfall[match.end():]
    for original,replacement in [('ld [hl+],a','call NativeStoreIncrement'),('ld [hl],a','call NativeStore')]:
        waterfall=waterfall.replace(original,replacement)
    for label,text in initialization.items():
        waterfall=waterfall.replace(f'; NATIVE_INITIAL_{label}\n',text)
    (work/'waterfall.asm').write_text(waterfall);(work/'ui.asm').write_text(ui);sources.append(work/'ui.asm')
    tables=[]; lookup=['SECTION "Native constant-time pitch lookup", ROMX, BANK[5]']
    for label,clock in [('NativePulseBoundaries',131072),('NativeWaveBoundaries',65536)]:
        values=[min(2048,max(0,math.ceil(2048-clock/(440*2**((midi+.5-69)/12))))) for midi in range(24,168)]
        if clock==131072:
            lookup.append('NativeWaveBoundaries::\n'+label+'::\n    db '+','.join(str(143-min(143,bisect.bisect_right(values,f))) for f in range(2048)))
    (work/'pitch-lookup.asm').write_text('\n'.join(lookup)+'\n');sources.append(work/'pitch-lookup.asm')
    noise=[]
    for value in range(256):
        r=value&7;shift=value>>4;frequency=524288/(r if r else .5)/2**(shift+1);midi=round(69+12*math.log2(frequency/440));noise.append(167-min(167,max(24,midi)))
    tables.append('NativeNoiseY:\n    db '+','.join(str(x) for x in noise))
    (work/'pitch-tables.asm').write_text('\n'.join(tables)+'\n')
    display='DEF NativePrepareIndex EQU $cc00\n'+(ROOT/'src/native/display.asm').read_text()
    display+='\nNativeAlignLCD:\n    push af\n    ldh a,[$ff90]\n    or a\n    jr nz,.cgb\n'+delay_source(lcd_delays['dmg'])+'\n    jr .done\n.cgb\n'+delay_source(lcd_delays['cgb'])+'\n.done\n    pop af\n    ret\n'
    (work/'display.asm').write_text(display)
    with (work/'ui.asm').open('a') as combined:combined.write(f'\nINCLUDE "{work / "display.asm"}"\n')
    objects=[]
    flags=['-D','EXACT_WATERFALL=1','-D','EXACT_PIXEL_WATERFALL=1','-D','EXACT_DMG=1','-D','NATIVE_CHANNEL_COLORS=1','-D','WATERFALL_WIDTH=80','-D',f'EXACT_SONG_GLYPHS="{title}"','-D',f'NATIVE_PITCH_TABLES="{work / "pitch-tables.asm"}"']
    if connect_pitch_bends:
        flags += ['-D','NATIVE_CONNECT_PITCH_BENDS=1']
    for source in sources:
        obj=work/(source.stem+'.o');subprocess.run(['rgbasm',*flags,'-o',str(obj),str(source)],cwd=ROOT,check=True);objects.append(obj)
    subprocess.run(['rgblink','-n',str(output.with_suffix('.sym')),'-m',str(output.with_suffix('.map')),'-o',str(output),*map(str,objects)],cwd=ROOT,check=True)
    subprocess.run(['rgbfix','-v','-p','0','-m','0x1a','-r','0x03',str(output)],check=True)
    manifest={'architecture':'native_song_interpreter','status':'experimental_audio_equivalence_pending','song_bytes':len(song),'rom_bytes':output.stat().st_size,'engine_banks':[0,2,7],'startup_state_bytes':0x4000+254,'performance_trace_bytes':0,'duration_limit':None,'hardware':['DMG','CGB'],'kit_indices':used_kit_indices(song),'startup_delay_cpu_cycles':song_delays.copy()}
    manifest['lcd_delay_cpu_cycles']=lcd_delays.copy()
    manifest['initial_timer_state']={model:state.copy() for model,state in timer_state.items()}
    manifest['connect_pitch_bends']=bool(connect_pitch_bends)
    manifest['cgb_channel_colors']={'PU1':'cyan','PU2':'pink','WAV':'green','NOI':'yellow'}
    output.with_suffix('.json').write_text(json.dumps(manifest,indent=2)+'\n');return manifest


def _startup_targets(snapshot_prefix):
    targets={}
    for model in ('cgb','dmg'):
        path=Path(f'{snapshot_prefix}-{model}.json')
        metadata=json.loads(path.read_text())
        expected='CGB-E' if model=='cgb' else 'DMG-B'
        if (metadata.get('model')!=expected or metadata.get('pc')!=0x5fe7
                or metadata.get('rom_bank')!=2 or metadata.get('double_speed')!=(model=='cgb')):
            raise ValueError(f'{path}: expected {expected} snapshot at bank 2:$5fe7')
        tick=metadata.get('tick')
        if not isinstance(tick,int) or tick<=0:raise ValueError(f'{path}: missing positive startup tick')
        targets[model]=tick
    return targets


def _adjust_startup_delays(delays,targets,observed):
    adjusted={}
    for model in ('cgb','dmg'):
        scale=1 if model=='cgb' else 2
        delta=targets[model]-observed[model]
        if delta%(4*scale):raise ValueError(f'{model}: startup delta {delta} is not a whole 4-cycle delay')
        adjusted[model]=delays[model]+delta//scale
        if adjusted[model]<0:
            raise ValueError(f'{model}: bootstrap takes {-adjusted[model]} CPU cycles longer than the source startup; cannot align with a nonnegative delay')
    return adjusted


def _startup_io(snapshot_prefix):
    states={}
    for model in ('cgb','dmg'):
        path=Path(f'{snapshot_prefix}-{model}.io.bin')
        io=path.read_bytes()
        if len(io)!=128 or io[7]&7!=6:
            raise ValueError(f'{path}: expected 128 I/O bytes and the native 64-cycle timer')
        states[model]={'tima':io[5],'tma':io[6],'tac':io[7]&7,'if':io[15]&0x1e}
    return states


def _last_lcd_enable(path,cutoff):
    enabled=False
    last=None
    with Path(path).open() as stream:
        for row in csv.DictReader(stream,delimiter='\t'):
            tick=int(row['ticks_8mhz'])
            if tick>cutoff:break
            if int(row['address'],16)!=0xff40:continue
            current=bool(int(row['value'],16)&0x80)
            if current and not enabled:last=tick
            enabled=current
    if last is None or not enabled:raise ValueError(f'{path}: no active LCD enable edge before startup')
    return last


def _timer_increment_count(initial,observed,tma):
    """Count a short startup timer advance, including one TMA reload."""
    if observed>=initial:return observed-initial
    if observed<tma:raise ValueError('Startup TIMA changed without a valid TMA reload')
    return 256-initial+observed-tma


def build(rom_path,profile,snapshot_prefix,output,name='SONG',*,align_startup=False,runner=None,startup_traces=None,connect_pitch_bends=False):
    """Optionally align startup time, timer state, and initial LCD phase.

    Only pre-song hardware state and the LCD enable edge are used. No song
    writes or audio are consumed, and no playback events are scheduled.
    """
    if not align_startup:return _build_once(rom_path,profile,snapshot_prefix,output,name,connect_pitch_bends=connect_pitch_bends)
    output=Path(output).resolve()
    runner=Path(runner or ROOT/'build/sameboy-native-analysis').resolve()
    if not runner.is_file():raise ValueError(f'Startup alignment requires capture runner: {runner}')
    targets=_startup_targets(snapshot_prefix)
    target_io=_startup_io(snapshot_prefix)
    timer_state={model:state.copy() for model,state in target_io.items()}
    lcd_targets={model:_last_lcd_enable(startup_traces[model],targets[model]) for model in ('cgb','dmg')} if startup_traces else {}
    delays={'cgb':8220996,'dmg':8547120}
    lcd_delays={'cgb':41700,'dmg':36960}
    evidence={'scope':'Pre-song entry time, TIMA/IF, optional LCD enable phase only; no playback events or audio',
              'runner':str(runner),'target_ticks':targets,'target_io':target_io,
              'source_lcd_enable_ticks':lcd_targets,'passes':[],'aligned':False}
    for attempt in range(5):
        manifest=_build_once(rom_path,profile,snapshot_prefix,output,name,delays,lcd_delays,timer_state,connect_pitch_bends=connect_pitch_bends)
        work=output.parent/(output.stem+'-native-data')/'startup-alignment'
        work.mkdir(exist_ok=True)
        def measure(model):
            prefix=work/f'pass-{attempt}-{model}'
            command=[str(runner),'--rom',str(output),'--model',model,'--ticks',str(max(targets[model]+2*8388608,6*8388608)),
                     '--break-pc','2:0x5fe7','--native-dump',str(prefix),
                     '--trace',str(prefix)+'.tsv','--trace-address','ff40']
            result=subprocess.run(command,capture_output=True,text=True,check=True)
            Path(str(prefix)+'.command.json').write_text(json.dumps(command,indent=2)+'\n')
            Path(str(prefix)+'.log').write_text(result.stderr)
            stats=json.loads(prefix.with_suffix('.json').read_text())
            if stats['pc']!=0x5fe7 or stats['rom_bank']!=2:
                raise ValueError(f'{model}: calibration did not reach native song initialization')
            io=Path(str(prefix)+'.io.bin').read_bytes()
            return model,{'tick':stats['tick'],'tima':io[5],'if':io[15]&0x1f,
                          'lcd_enable_tick':_last_lcd_enable(Path(str(prefix)+'.tsv'),stats['tick'])}
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            observations=dict(pool.map(measure,('cgb','dmg')))
        observed={model:state['tick'] for model,state in observations.items()}
        evidence['passes'].append({'delay_cpu_cycles':delays.copy(),'lcd_delay_cpu_cycles':lcd_delays.copy(),
                                   'initial_timer_state':{model:state.copy() for model,state in timer_state.items()},
                                   'observed':observations})
        evidence['aligned']=(observed==targets and all(observations[m]['tima']==target_io[m]['tima']
            and observations[m]['if']&0x1e==target_io[m]['if'] for m in ('cgb','dmg'))
            and all((observations[m]['lcd_enable_tick']-lcd_targets[m])%140448==0 for m in lcd_targets))
        manifest['startup_alignment']=evidence
        output.with_suffix('.json').write_text(json.dumps(manifest,indent=2)+'\n')
        if evidence['aligned']:return manifest
        delays=_adjust_startup_delays(delays,targets,observed)
        for model in ('cgb','dmg'):
            if model in lcd_targets:
                scale=1 if model=='cgb' else 2
                delta=(lcd_targets[model]-observations[model]['lcd_enable_tick'])%140448
                if delta%(4*scale):raise ValueError(f'{model}: LCD phase cannot be represented as whole CPU cycles')
                lcd_delays[model]+=delta//scale
                delays[model]-=delta//scale
                if delays[model]<0:raise ValueError(f'{model}: no startup time remains for LCD phase alignment')
            increments=_timer_increment_count(timer_state[model]['tima'],observations[model]['tima'],timer_state[model]['tma'])
            corrected=target_io[model]['tima']-increments
            if not 0<=corrected<=255:raise ValueError(f'{model}: cannot restore source TIMA before native initialization')
            timer_state[model]['tima']=corrected
    raise ValueError(f'Native startup state did not align after five builds: {observations}')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--rom',type=Path,required=True);p.add_argument('--profile',type=Path,required=True);p.add_argument('--snapshot-prefix',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--name',default='SONG')
    p.add_argument('--align-startup',action='store_true',help='Calibrate bootstrap entry to both source snapshot timestamps (does not imply audio equality)')
    p.add_argument('--runner',type=Path,default=ROOT/'build/sameboy-native-analysis')
    p.add_argument('--connect-pitch-bends',action='store_true',help='Draw vertical waterfall connections for continuous legato/pitch bends (default: discrete points)')
    p.add_argument('--startup-trace-cgb',type=Path,help='Source trace containing FF40 writes before the CGB startup snapshot')
    p.add_argument('--startup-trace-dmg',type=Path,help='Source trace containing FF40 writes before the DMG startup snapshot')
    a=p.parse_args()
    if bool(a.startup_trace_cgb)!=bool(a.startup_trace_dmg):p.error('Supply both model startup traces together')
    traces={'cgb':a.startup_trace_cgb,'dmg':a.startup_trace_dmg} if a.startup_trace_cgb else None
    if traces and not a.align_startup:p.error('Startup traces require --align-startup')
    print(json.dumps(build(a.rom,a.profile,a.snapshot_prefix,a.output,a.name,align_startup=a.align_startup,runner=a.runner,startup_traces=traces,connect_pitch_bends=a.connect_pitch_bends),indent=2))
