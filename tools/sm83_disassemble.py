"""Lossless SM83 instruction decoding for local LSDj engine recovery."""
from __future__ import annotations

REGS = ('b','c','d','e','h','l','[hl]','a')
PAIRS = ('bc','de','hl','sp')
COND = ('nz','z','nc','c')


def instruction(data: bytes, offset: int, address: int) -> tuple[str, int]:
    op = data[offset]
    def byte(): return data[offset + 1]
    def word(): return data[offset + 1] | data[offset + 2] << 8
    def signed(): return byte() - 256 if byte() >= 128 else byte()
    if op == 0xcb:
        code = byte(); reg = REGS[code & 7]; bit = (code >> 3) & 7
        if code < 0x40: return f"{('rlc','rrc','rl','rr','sla','sra','swap','srl')[bit]} {reg}", 2
        return f"{('bit','res','set')[(code >> 6) - 1]} {bit},{reg}", 2
    if 0x40 <= op < 0x80:
        return ('halt' if op == 0x76 else f'ld {REGS[(op >> 3) & 7]},{REGS[op & 7]}'), 1
    if 0x80 <= op < 0xc0:
        return f"{('add a,','adc a,','sub ','sbc a,','and ','xor ','or ','cp ')[(op >> 3) & 7]}{REGS[op & 7]}", 1
    if op < 0x40:
        if op & 15 == 1: return f'ld {PAIRS[op >> 4]},${word():04x}',3
        if op & 15 == 3: return f'inc {PAIRS[op >> 4]}',1
        if op & 15 == 11: return f'dec {PAIRS[op >> 4]}',1
        if op & 15 == 9: return f'add hl,{PAIRS[op >> 4]}',1
        if op & 7 == 4: return f'inc {REGS[op >> 3]}',1
        if op & 7 == 5: return f'dec {REGS[op >> 3]}',1
        if op & 7 == 6: return f'ld {REGS[op >> 3]},${byte():02x}',2
        fixed={0:'nop',2:'ld [bc],a',7:'rlca',10:'ld a,[bc]',15:'rrca',
               18:'ld [de],a',23:'rla',26:'ld a,[de]',31:'rra',
               34:'ld [hl+],a',39:'daa',42:'ld a,[hl+]',47:'cpl',
               50:'ld [hl-],a',55:'scf',58:'ld a,[hl-]',63:'ccf'}
        if op in fixed:return fixed[op],1
        if op == 8:return f'ld [${word():04x}],sp',3
        if op == 0x10:return ('stop' if byte()==0 else f'db $10,${byte():02x}'),2
        if op == 0x18:return f'jr ${(address+2+signed()) & 65535:04x}',2
        if op in (0x20,0x28,0x30,0x38):return f'jr {COND[(op-0x20)//8]},${(address+2+signed()) & 65535:04x}',2
    if op in (0xc0,0xc8,0xd0,0xd8):return f'ret {COND[(op-0xc0)//8]}',1
    if op in (0xc2,0xca,0xd2,0xda):return f'jp {COND[(op-0xc2)//8]},${word():04x}',3
    if op in (0xc4,0xcc,0xd4,0xdc):return f'call {COND[(op-0xc4)//8]},${word():04x}',3
    if op in (0xc1,0xd1,0xe1,0xf1):return f"pop {('bc','de','hl','af')[(op-0xc1)//16]}",1
    if op in (0xc5,0xd5,0xe5,0xf5):return f"push {('bc','de','hl','af')[(op-0xc5)//16]}",1
    if op & 7 == 7:return f'rst ${op & 0x38:02x}',1
    immediate={0xc6:'add a,',0xce:'adc a,',0xd6:'sub ',0xde:'sbc a,',0xe6:'and ',0xee:'xor ',0xf6:'or ',0xfe:'cp '}
    if op in immediate:return f'{immediate[op]}${byte():02x}',2
    fixed={0xc9:'ret',0xd9:'reti',0xe2:'ldh [c],a',0xf2:'ldh a,[c]',0xe9:'jp hl',0xf9:'ld sp,hl',0xf3:'di',0xfb:'ei'}
    if op in fixed:return fixed[op],1
    if op == 0xc3:return f'jp ${word():04x}',3
    if op == 0xcd:return f'call ${word():04x}',3
    if op == 0xe0:return f'ldh [${0xff00+byte():04x}],a',2
    if op == 0xf0:return f'ldh a,[${0xff00+byte():04x}]',2
    if op == 0xea:return f'ld [${word():04x}],a',3
    if op == 0xfa:return f'ld a,[${word():04x}]',3
    if op == 0xe8:return f'add sp,{signed()}',2
    if op == 0xf8:return f'ld hl,sp{signed():+d}',2
    return f'db ${op:02x}',1
