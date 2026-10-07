#!/usr/bin/env python3
"""Recover a local ROM's shared interpreter as byte-identical RGBDS source.

Execution coverage distinguishes instructions from lookup tables. The source
ROM is supplied locally; neither it nor recovered proprietary code is bundled.
"""
import argparse,csv,hashlib,json,subprocess
from pathlib import Path
from sm83_disassemble import instruction


def recover(rom,profile,output,banks=(0,2)):
    data=Path(rom).read_bytes(); output=Path(output);output.mkdir(parents=True,exist_ok=True)
    executed={bank:set() for bank in banks}
    for row in csv.DictReader(Path(profile).open(),delimiter='\t'):
        if row['bank']=='entry':continue
        bank=int(row['bank'])
        if bank in executed:executed[bank].add(int(row['address'],16))
    manifest={'rom_sha256':hashlib.sha256(data).hexdigest(),'banks':{}}
    for bank in banks:
        binary=data[bank*0x4000:(bank+1)*0x4000]; base=0x4000 if bank else 0
        lines=[f'; Locally recovered LSDj 9.4.2 shared interpreter bank {bank}.',
               f'SECTION "Native engine bank {bank}", '+(f'ROMX[$4000], BANK[{bank}]' if bank else 'ROM0[$0000]')]
        offset=code_bytes=0
        while offset<len(binary):
            address=base+offset
            if address in executed[bank]:
                text,size=instruction(binary,offset,address)
                lines.append(f'    {text} ; ${address:04x}')
                code_bytes+=size;offset+=size
            else:
                end=offset+1
                while end<len(binary) and end-offset<16 and base+end not in executed[bank]:end+=1
                lines.append('    db '+','.join(f'${v:02x}' for v in binary[offset:end])+f' ; ${address:04x}')
                offset=end
        source=output/f'engine-bank-{bank}.asm';source.write_text('\n'.join(lines)+'\n')
        obj=source.with_suffix('.o'); rebuilt=source.with_suffix('.gb')
        subprocess.run(['rgbasm','-o',str(obj),str(source)],check=True)
        subprocess.run(['rgblink','-o',str(rebuilt),str(obj)],check=True)
        actual=rebuilt.read_bytes()[bank*0x4000:(bank+1)*0x4000]
        if actual!=binary:raise ValueError(f'Recovered bank {bank} differs from reference')
        manifest['banks'][str(bank)]={'source':str(source),'bytes':len(binary),'observed_instruction_bytes':code_bytes,'byte_identical':True}
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--rom',type=Path,required=True);p.add_argument('--profile',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();print(json.dumps(recover(a.rom,a.profile,a.output),indent=2))
