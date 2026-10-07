; Four live oscillator pens. Read the interpreter's post-effect frequencies,
; not a precomputed pitch trace. Rendering and hidden-column preparation run
; once per LCD frame and retain the existing monochrome/SGB screen design.
SECTION "Native live display", ROMX, BANK[5]
NativeDisplayFrame::
    push af
    push bc
    push de
    push hl
    ldh a,[$ff26]
    and $0f
    ld [WaterfallActive],a
    ld hl,$c0f4
    ld de,WaterfallPitchY
    ld b,3
.voice
    ld a,[hl+]
    ld c,a
    ld a,[hl+]
    and 7
    push hl
    push bc
    ld h,a
    ld l,c
    ld a,b
    cp 1
    ld bc,NativePulseBoundaries
    jr nz,.pitch_table
    ld bc,NativeWaveBoundaries
.pitch_table
    call NativePitchY
    ld [de],a
    pop bc
    pop hl
    ld a,b
    cp 1
    jr nz,.pitch_ready
    ld a,[de]
    or a
    jr nz,.wave_octave
    ld a,11 ; highest representable oscillator value has a clipped pulse Y
    jr .wave_ready
.wave_octave
    add 12
    cp 144
    jr c,.wave_ready
    ld a,143
.wave_ready
    ld [de],a
.pitch_ready
    inc de
    dec b
    jr nz,.voice
    ldh a,[$ff22]
    ld l,a
    ld h,0
    ld bc,NativeNoiseY
    add hl,bc
    ld a,[hl]
    ld [de],a

    ld a,[WaterfallPixelPhase]
    or a
    jr nz,.prepared_begin
    call WaterfallBegin
    xor a
    ld [NativePrepareIndex],a
.prepared_begin
    call WaterfallPixelPoint0
    call WaterfallPixelPoint1
    call WaterfallPixelPoint2
    call WaterfallPixelPoint3
    ; Three small slices per frame prepare the next hidden column/map.
    ld b,3
.prepare
    push bc
    ld a,[NativePrepareIndex]
    cp 21
    jr nc,.slice_done
    ld hl,NativePreparation
    add a,a
    ld c,a
    ld b,0
    add hl,bc
    ld a,[hl+]
    ld h,[hl]
    ld l,a
    ld de,.slice_return
    push de
    jp hl
.slice_return
    ld hl,NativePrepareIndex
    inc [hl]
.slice_done
    pop bc
    dec b
    jr nz,.prepare
    call NativeBPM
    call NativeNotes
    pop hl
    pop de
    pop bc
    pop af
    ret

; HL=11-bit oscillator frequency, BC=constant-time pitch lookup.
; DE (the pen destination) is preserved. A=Y, one pixel per key.
NativePitchY:
    add hl,bc
    ld a,[hl]
    ret

NativeBPM:
    ld a,[$c529]
    ld e,a
    ld d,0
    cp 40
    jr nc,.decoded
    inc d
.decoded
    ld b,0
.hundreds
    ld a,d
    or a
    jr nz,.subtract
    ld a,e
    cp 100
    jr c,.tens
.subtract
    ld a,e
    sub 100
    ld e,a
    ld a,d
    sbc 0
    ld d,a
    inc b
    jr .hundreds
.tens
    ld a,b
    add 13
    ld hl,$9895
    call NativeStoreIncrement
    ld b,0
.tens_loop
    ld a,e
    cp 10
    jr c,.digits
    sub 10
    ld e,a
    inc b
    jr .tens_loop
.digits
    ld a,b
    add 13
    call NativeStoreIncrement
    ld a,e
    add 13
    call NativeStoreIncrement
    ret

; Interrupts remain available while waiting for accessible VRAM.
NativeStore:
    di
    push af
.wait
    ldh a,[$ff41]
    and 2
    jr nz,.wait
    pop af
    ld [hl],a
    ei
    ret
NativeStoreIncrement:
    call NativeStore
    inc hl
    ret

; A mode-3 read returns $ff and would paint a whole byte instead of a pixel.
NativeRead:
    di
    push af
.wait
    ldh a,[$ff41]
    and 2
    jr nz,.wait
    pop af
    ld a,[hl]
    ei
    ret

NativePreparation:
    FOR part,12
        dw WaterfallClear{d:part}
    ENDR
    FOR part,9
        dw WaterfallMap{d:part}
    ENDR
INCLUDE NATIVE_PITCH_TABLES

NativeSongInfoInit:
    ; Reuse the retired percentage and unused duplicate keyboard glyphs.
    ld hl,$8170
    ld de,NativeSharpGlyph
    ld bc,16
    call .copy
    ld hl,$8240
    ld de,NativeExtraGlyphs
    ld bc,32
    call .copy
    ld de,NativeInfoRows
    ld hl,$9891
    ld b,12
.row
    push bc
    push hl
    ld b,8
.cell
    ld a,[de]
    inc de
    ld [hl+],a
    dec b
    jr nz,.cell
    pop hl
    ld bc,32
    add hl,bc
    pop bc
    dec b
    jr nz,.row
    ld hl,$98d1
    ld a,5
    ld [hl+],a
    ldh a,[$ff90]
    or a
    jr nz,.cgb
    ld a,4
    ld [$98d1],a
    ld a,1
    jr .mode
.cgb
    ld a,36
.mode
    ld [hl+],a
    ld a,36
    ldh a,[$ff90]
    or a
    ld a,36
    jr z,.last
    ld a,24
.last
    ld [hl],a
    ret
.copy
    ld a,[de]
    inc de
    ld [hl+],a
    dec bc
    ld a,b
    or c
    jr nz,.copy
    ret

NativeNotes:
    ld de,WaterfallPitchY
    ld hl,$9915
    ld b,4
    ld c,1
.channel
    push bc
    push hl
    ld a,[WaterfallActive]
    and c
    jr z,.silent
    ld a,[de]
    ld c,a
    ld a,167
    sub c
    ld c,0
.octave
    cp 12
    jr c,.key
    sub 12
    inc c
    jr .octave
.key
    push de
    push hl
    ld hl,NativeNoteNames
    add a,a
    ld e,a
    ld d,0
    add hl,de
    ld a,[hl+]
    ld d,[hl]
    pop hl
    call NativeStoreIncrement
    ld a,d
    call NativeStoreIncrement
    ld a,c
    cp 11
    jr c,.one_digit
    ld a,14
    call NativeStoreIncrement
    ld a,c
    add 2
    call NativeStore
    jr .note_done
.one_digit
    ld a,c
    add 12 ; MIDI octave = MIDI/12 - 1; digit zero tile=13
    call NativeStore
    inc hl
    xor a
    call NativeStore
.note_done
    pop de
    jr .next
.silent
    xor a
    call NativeStoreIncrement
    call NativeStoreIncrement
    call NativeStore
.next
    pop hl
    ld bc,64
    add hl,bc
    inc de
    pop bc
    sla c
    dec b
    jr nz,.channel
    ret

NativeNoteNames:
    db 5,0,5,23,4,0,4,23,10,0,12,0,12,23,36,0,36,23,8,0,8,23,24,0
NativeInfoRows:
    db 24,6,1,0,13,13,13,0 ; BPM
    db 0,0,0,0,0,0,0,0
    db 0,0,0,0,0,0,0,0 ; hardware
    db 0,0,0,0,0,0,0,0
    db 6,2,14,0,0,0,0,0 ; PU1
    db 0,0,0,0,0,0,0,0
    db 6,2,15,0,0,0,0,0 ; PU2
    db 0,0,0,0,0,0,0,0
    db 5,37,16,0,0,0,0,0 ; CH3
    db 0,0,0,0,0,0,0,0
    db 5,37,17,0,0,0,0,0 ; CH4
    db 0,0,0,0,0,0,0,0
NativeSharpGlyph:
    db 0,0,$28,$28,$7c,$7c,$28,$28,$28,$28,$7c,$7c,$28,$28,0,0
NativeExtraGlyphs:
    db 0,0,$3c,$3c,$40,$40,$40,$40,$5c,$5c,$44,$44,$3c,$3c,0,0
    db 0,0,$44,$44,$44,$44,$7c,$7c,$44,$44,$44,$44,$44,$44,0,0
