; CGB prepares each inactive map row with two short general-DMA blocks.
; The 11 visible cells fit a 16-byte block; columns 11..15 are offscreen.
; The row buffer lies below the retired font IRQ scratch at $cc80.
DEF NativeColorRowBuffer EQU $cc70
ASSERT LOW(NativeColorRowBuffer) % 16 == 0
ASSERT NativeColorRowBuffer + 16 <= $cc80

; A=clear stage 0..11. Alternate 32-byte and 16-byte chunks so all 288
; hidden-column bytes are cleared in the original twelve scheduler stages.
; Each 16-byte block releases interrupts before another transfer begins.
NativeColorClearSlice:
    push af
    push bc
    push de
    push hl
    ld b,a
    and 1
    push af
    ld a,b
    srl a
    ld l,a
    ld h,0
    add hl,hl
    ld d,h
    ld e,l
    add hl,hl
    add hl,de ; pair * 6
    REPT 3
        add hl,hl ; pair * 48
    ENDR
    bit 0,b
    jr z,.offset_ready
    ld de,32
    add hl,de
.offset_ready
    ld a,[WaterfallColumnBase]
    ld e,a
    ld a,[WaterfallColumnBase + 1]
    ld d,a
    add hl,de
    ld d,h
    ld e,l
    ld hl,NativeColorRowBuffer
    xor a
    REPT 16
        ld [hl+],a
    ENDR
    ld hl,NativeColorRowBuffer
    ld c,0
    call NativeColorTransferRow
    pop af
    or a
    jr nz,.done
    ld a,e
    add 16
    ld e,a
    jr nc,.second
    inc d
.second
    call NativeColorTransferRow
.done
    pop hl
    pop de
    pop bc
    pop af
    ret

; A=row 0..17. Copies tile numbers and matching palette attributes together.
NativeColorPrepareRow:
    push af
    push bc
    push de
    push hl
    ld [NativeColorRows],a
    ld l,a
    ld h,0
    REPT 4
        add hl,hl
    ENDR
    ld a,[WaterfallMapSource]
    ld e,a
    ld a,[WaterfallMapSource + 1]
    ld d,a
    add hl,de
    push hl
    ld b,11
    ld c,LOW(NativeColorRowBuffer)
.attributes
    ld a,[hl+]
    sub 40
    push hl
    ld l,a
    ld h,HIGH(NativeColorMetadata)
    call NativeColorRead
    call NativeColorPaletteForMask
    ld h,HIGH(NativeColorRowBuffer)
    ld l,c
    ld [hl],a
    inc c
    pop hl
    dec b
    jr nz,.attributes
    ; Unused map columns must not borrow stale buffer bytes.
    ld hl,NativeColorRowBuffer + 11
    xor a
    REPT 5
        ld [hl+],a
    ENDR
    ld a,[NativeColorRows]
    ld l,a
    ld h,0
    REPT 5
        add hl,hl
    ENDR
    ld a,[WaterfallMapDestHigh]
    add h
    ld d,a
    ld e,l
    pop hl
    ld c,0
    call NativeColorTransferRow
    ld hl,NativeColorRowBuffer
    inc c
    call NativeColorTransferRow
    pop hl
    pop de
    pop bc
    pop af
    ret

; HL=16-byte-aligned source, DE=destination, C=VRAM bank. Audio does not
; touch FF51..FF55, so DMA addresses can be staged with IRQs enabled.
; Only the mode check, one 16-byte transfer and VBK restoration are atomic.
; CGB runs at double speed: the transfer finishes before the next mode 3
; even if the check catches the final cycle of HBlank. No HBlank DMA runs
; in the background, and music always resumes with VRAM bank 0 selected.
NativeColorTransferRow:
    ld a,h
    ldh [$ff51],a
    ld a,l
    ldh [$ff52],a
    ld a,d
    ldh [$ff53],a
    ld a,e
    ldh [$ff54],a
.wait
    di
    ldh a,[$ff41]
    and 2
    jr nz,.busy
    ld a,c
    ldh [$ff4f],a
    xor a
    ldh [$ff55],a
    ldh [$ff4f],a
    ei
    ret
.busy
    ei
    jr .wait
