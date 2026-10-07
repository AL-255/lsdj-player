#!/usr/bin/env python3
"""Build the experimental shared LSDj9.4.2 song interpreter, not a trace ROM.

Consumes only startup state and the32KiB song. Playback duration is not an
input and does not affect ROM size. Recovered code remains local to the build.
"""
import argparse,bisect,csv,json,math,re,subprocess
from pathlib import Path
from sm83_disassemble import instruction
from native_delay import delay_code
from song_title import write_tiles
from project_kits import used_kit_indices,kit_bank

ROOT=Path(__file__).resolve().parents[1]

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


def build(rom_path,profile,snapshot_prefix,output,name='SONG'):
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
    executed={0:set(),2:set()}
    for row in csv.DictReader(Path(profile).open(),delimiter='\t'):
        if row['bank']!='entry' and int(row['bank']) in executed:executed[int(row['bank'])].add(int(row['address'],16))
    for bank in [0,2]:
        ranges=[(0,0x42),(0x45,0x100),(0x104,0xb54),(0x1306,0x4000)] if not bank else [(0,0x4000)]
        (work/f'engine{bank}.asm').write_text(engine_source(bytes(core0) if bank==0 else rom[bank*0x4000:(bank+1)*0x4000],executed[bank],bank,ranges))
    early=[]
    for macro_name,cycles in [('NativeEarlyCGB',210908-80),('NativeEarlyDMG',211872-84)]:
        early += [f'MACRO {macro_name}','    db '+','.join(f'${b:02x}' for b in delay_code(cycles)), '    xor a','    ldh [$ff26],a']
        if macro_name=='NativeEarlyCGB':early+=['    db '+','.join(f'${b:02x}' for b in delay_code(164)), '    ei','    ld a,1','    ldh [$ff4d],a','    stop']
        early+=['ENDM']
    early+=['MACRO NativeSongDelay','    ld a,[NativeHardware]','    cp $11','    jr nz,.dmg_delay\\@',
            '    db '+','.join(f'${b:02x}' for b in delay_code(8220996)),
            '    jr .song_ready\\@','.dmg_delay\\@',
            '    db '+','.join(f'${b:02x}' for b in delay_code(8547120)),
            '.song_ready\\@','ENDM']
    (work/'boot.asm').write_text('\n'.join(early)+'\n'+(ROOT/'src/native/boot.asm').read_text())
    sources=[work/'engine0.asm',work/'engine2.asm',work/'boot.asm']
    data=f'SECTION "Native entry", ROM0[$100]\n    nop\n    jp NativeStart\n'
    data+='SECTION "Native VBlank hook", ROM0[$42]\n    jp NativeVBlank\n'
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
    # Safe stores retain A/flags and allow the music interrupt to preempt UI.
    for original,replacement in [('ld [hl+],a','call NativeStoreIncrement'),('ld [hl],a','call NativeStore')]:
        waterfall=waterfall.replace(original,replacement);ui=ui.replace(original,replacement)
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
    display+='\nNativeAlignLCD:\n    push af\n    ldh a,[$ff90]\n    or a\n    jr nz,.cgb\n    db '+','.join(f'${b:02x}' for b in delay_code(36960))+'\n    jr .done\n.cgb\n    db '+','.join(f'${b:02x}' for b in delay_code(41700))+'\n.done\n    pop af\n    ret\n'
    (work/'display.asm').write_text(display)
    with (work/'ui.asm').open('a') as combined:combined.write(f'\nINCLUDE "{work / "display.asm"}"\n')
    objects=[]
    flags=['-D','EXACT_WATERFALL=1','-D','EXACT_PIXEL_WATERFALL=1','-D','EXACT_DMG=1','-D','WATERFALL_WIDTH=80','-D',f'EXACT_SONG_GLYPHS="{title}"','-D',f'NATIVE_PITCH_TABLES="{work / "pitch-tables.asm"}"']
    for source in sources:
        obj=work/(source.stem+'.o');subprocess.run(['rgbasm',*flags,'-o',str(obj),str(source)],cwd=ROOT,check=True);objects.append(obj)
    subprocess.run(['rgblink','-n',str(output.with_suffix('.sym')),'-m',str(output.with_suffix('.map')),'-o',str(output),*map(str,objects)],cwd=ROOT,check=True)
    subprocess.run(['rgbfix','-v','-p','0','-m','0x1a','-r','0x03',str(output)],check=True)
    manifest={'architecture':'native_song_interpreter','status':'experimental_audio_equivalence_pending','song_bytes':len(song),'rom_bytes':output.stat().st_size,'engine_banks':[0,2],'startup_state_bytes':0x4000+254,'performance_trace_bytes':0,'duration_limit':None,'hardware':['DMG','CGB'],'kit_indices':used_kit_indices(song)}
    output.with_suffix('.json').write_text(json.dumps(manifest,indent=2)+'\n');return manifest

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--rom',type=Path,required=True);p.add_argument('--profile',type=Path,required=True);p.add_argument('--snapshot-prefix',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--name',default='SONG')
    a=p.parse_args();print(json.dumps(build(a.rom,a.profile,a.snapshot_prefix,a.output,a.name),indent=2))
