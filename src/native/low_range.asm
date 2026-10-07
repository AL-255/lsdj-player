; One optional mode: three tonal pens, original Y=72..143, two pixels/key.
; The 40-pixel plot needs seven ring columns instead of twelve. Keep the
; sampled pitches unchanged for note readouts and bend event detection.
NativeLowRangePoints:
    ld a,[WaterfallPixelPhase]
    ld e,a
    ld d,0
    ld hl,WaterfallPixelMasks
    add hl,de
    ld b,[hl]
    ld a,[WaterfallHead]
    add a,a
    ld e,a
    ld hl,WaterfallPixelPointers
    add hl,de
    ld a,[hl+]
    ld d,[hl]
    ld e,a
    FOR channel,3
        ld a,[WaterfallActive]
        bit channel,a
        jr z,.next{d:channel}
        ld a,[WaterfallPitchY + channel]
        sub 72
        jr c,.next{d:channel}
        ld l,a
        ld h,0
        add hl,hl
        add hl,hl
        add hl,de
        ldh a,[$ff90]
        or a
        jr z,.monochrome{d:channel}
        ld a,channel
        ld [NativeColorChannel],a
        call NativeColorPixel
        jr .next{d:channel}
.monochrome{d:channel}
        call NativeLowRangeMonochromePixel
.next{d:channel}
    ENDR
    ret

; HL=first row's low plane, B=pixel mask. Both rows always match, so one
; read suffices. The final store is 60 cycles after STAT: within DMG's
; 80-cycle mode-2 window even when HBlank ends just after the check.
; Preserve BC/DE/HL; busy waits and the read/modify step permit audio IRQs.
NativeLowRangeMonochromePixel:
    call NativeRead
    or b
    push af
.wait
    di
    ldh a,[$ff41]
    and 2
    jr nz,.busy
    pop af
    ld [hl+],a
    inc hl
    ld [hl],a
    dec hl
    dec hl
    ei
    ret
.busy
    ei
    jr .wait

DEF NativeLowRangeKeyboardTile EQU (WaterfallPatternBase - $8000) / 16 + WaterfallRingColumns * 18
ASSERT NativeLowRangeKeyboardTile == 166
; LCD off, bank 0. Two overlapping 16-pixel sprites cover each 24-pixel
; octave. The repeated overlap matches exactly, keeping twelve sprites.
NativeLowRangeInit:
    ld hl,$81a0 ; original keyboard pair, tiles 26/27
    ld de,NativeLowRangeKeyboard
    ld b,32
    call .copy
    ld hl,$8000 + NativeLowRangeKeyboardTile * 16
    ld b,32
    call .copy
    FOR stripe,12
        ld hl,$fe48 + stripe * 4
        ld a,16 + (stripe / 2) * 24 + (stripe % 2) * 16
        ld [hl+],a
        inc hl ; retain the right-edge X coordinate and attributes
        IF stripe % 2 == 0
            ld a,26
        ELSE
            ld a,NativeLowRangeKeyboardTile
        ENDC
        ld [hl],a
    ENDR
    ret
.copy
    ld a,[de]
    inc de
    ld [hl+],a
    dec b
    jr nz,.copy
    ret

NativeLowRangeKeyboard:
    FOR row,32
        ; Original lower half is MIDI 95..24 (B6..C1), top to bottom.
        IF ((95 - row / 2) % 12) == 1 || ((95 - row / 2) % 12) == 3 || ((95 - row / 2) % 12) == 6 || ((95 - row / 2) % 12) == 8 || ((95 - row / 2) % 12) == 10
            db $ff,$0f
        ELSE
            db $ff,$01
        ENDC
    ENDR
