; Four live oscillator pens. Read the interpreter's post-effect frequencies,
; not a precomputed pitch trace. Rendering and hidden-column preparation run
; once per LCD frame. CGB colors identify channels; DMG stays monochrome.
DEF NativeFrameReady EQU $cc01

SECTION "Native live display", ROMX, BANK[5]
NativeDisplayFrame::
    push af
    push bc
    push de
    push hl
    ; A missed VBlank can leave another main-loop iteration pending. Do not
    ; render the same phase twice while its completed frame awaits commit.
    ld a,[NativeFrameReady]
    or a
    jp nz,.done
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
    ldh a,[$ff90]
    or a
    call nz,NativeColorBegin
    xor a
    ld [NativePrepareIndex],a
.prepared_begin
    IF DEF(NATIVE_CONNECT_PITCH_BENDS)
        call NativeBendFrame
    ENDC
    ldh a,[$ff90]
    or a
    jr z,.monochrome_points
    call NativeColorPoints
    jr .points_ready
.monochrome_points
    call WaterfallPixelPoint0
    call WaterfallPixelPoint1
    call WaterfallPixelPoint2
    call WaterfallPixelPoint3
.points_ready
    ; The DMG-compatible renderer uses one map row per slice. Four slices
    ; per frame finish all 12 clears and 18 rows before the eight-pixel wrap.
    ld b,(WaterfallClearCount + WaterfallMapCount + 7) / 8
.prepare
    push bc
    ld a,[NativePrepareIndex]
    cp WaterfallClearCount + WaterfallMapCount
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
    ldh a,[$ff90]
    or a
    jr z,.slice_progress
    ld a,[NativePrepareIndex]
    sub WaterfallClearCount
    call nc,NativeColorMapRow
.slice_progress
    ld hl,NativePrepareIndex
    inc [hl]
.slice_done
    pop bc
    dec b
    jr nz,.prepare
    call NativeBPM
    ; DMG spends its smaller display budget on the piano roll.
    ldh a,[$ff90]
    or a
    call nz,NativeNotes
    ld a,1
    ld [NativeFrameReady],a
.done
    pop hl
    pop de
    pop bc
    pop af
    ret

; All four pens share a pixel column. Color pixels replace both bitplanes
; with the channel's palette index; OR would mix two channels into a third.
NativeColorPoints:
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
    FOR channel,4
        ld a,[WaterfallActive]
        bit channel,a
        jr z,.next{d:channel}
        ld a,channel
        ld [NativeColorChannel],a
        ld a,[WaterfallPitchY + channel]
        ld l,a
        ld h,0
        add hl,hl
        add hl,de
        call NativeColorPixel
.next{d:channel}
    ENDR
    ret

; Scroll is foreground work. The engine's original VBlank handler has
; already scheduled audio, and music interrupts may preempt preparation.
; If audio leaves no safe VBlank window, keep the completed frame pending:
; NativeDisplayFrame then skips drawing until this commit can succeed.
NativeDisplayScroll::
    push af
    push bc
    push de
    ld a,[NativeFrameReady]
    or a
    jr z,.done
    ldh a,[$ff44]
    sub 144
    cp 8
    jr nc,.done
    ld a,[WaterfallPixelPhase]
    inc a
    ld b,a
    cp 8
    jr c,.fine
    ld a,[NativePrepareIndex]
    cp WaterfallClearCount + WaterfallMapCount
    jr c,.done
    ld b,0
    ld c,87
    ld a,[WaterfallNextLCD]
    ld d,a
    ld a,[WaterfallNextHead]
    ld e,a
    jr .commit
.fine
    ld a,87
    sub b
    ld c,a
    ld a,[WaterfallLCD]
    ld d,a
    ld a,[WaterfallHead]
    ld e,a
.commit
    ; An audio interrupt may have used the rest of VBlank while the values
    ; were computed. Recheck with interrupts masked only for the two stores.
    ; LY 144..151 leaves two full blank lines of margin on either hardware.
    di
    ldh a,[$ff44]
    sub 144
    cp 8
    jr nc,.late
    ld a,b
    or a
    jr nz,.window
    ld a,d
    ldh [$ff40],a
.window
    ld a,c
    ldh [$ff4b],a
    ei
    ld a,b
    ld [WaterfallPixelPhase],a
    ld a,d
    ld [WaterfallLCD],a
    ld a,e
    ld [WaterfallHead],a
    xor a
    ld [NativeFrameReady],a
    jr .done
.late
    ei
.done
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
    push af
.wait
    di
    ldh a,[$ff41]
    and 2
    jr nz,.busy
    pop af
    ld [hl],a
    ei
    ret
.busy
    ei
    jr .wait
NativeStoreIncrement:
    call NativeStore
    inc hl
    ret

; A mode-3 read returns $ff and would paint a whole byte instead of a pixel.
NativeRead:
    push af
.wait
    di
    ldh a,[$ff41]
    and 2
    jr nz,.busy
    pop af
    ld a,[hl]
    ei
    ret
.busy
    ei
    jr .wait

NativePreparation:
    FOR part,WaterfallClearCount
        dw WaterfallClear{d:part}
    ENDR
    FOR part,WaterfallMapCount
        dw WaterfallMap{d:part}
    ENDR
INCLUDE NATIVE_PITCH_TABLES

NativeSongInfoInit:
    IF DEF(NATIVE_CONNECT_PITCH_BENDS)
        call NativeBendInit
    ENDC
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
    ldh a,[$ff90]
    or a
    jr nz,.cgb_labels
    ; The monochrome screen omits all four channel-status labels/values.
    xor a
    FOR channel,4
        ld hl,$9911 + channel * 64
        REPT 3
            ld [hl+],a
        ENDR
    ENDR
    jr .labels_ready
.cgb_labels
    ; The shared bank-0 tile space is full. CGB's second VRAM bank supplies
    ; the five extra label letters without taking piano-roll pattern tiles.
    ld a,1
    ldh [$ff4f],a
    ld hl,$8000
    ld de,NativeChannelGlyphs
    ld bc,80
    call .copy
    ld a,8 ; tile-data bank 1, existing palette 0
    ld [$9991],a
    ld [$9993],a
    ld [$99d1],a
    ld [$99d2],a
    ld [$99d3],a
    xor a
    ldh [$ff4f],a
    ld [$9991],a ; W in VRAM bank 1
    ld a,8
    ld [$9992],a ; A in VRAM bank 0
    ld a,1
    ld [$9993],a ; V
    inc a
    ld [$99d1],a ; N
    inc a
    ld [$99d2],a ; O
    inc a
    ld [$99d3],a ; I
.labels_ready
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
    ldh a,[$ff90]
    or a
    call nz,NativeColorInit
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

NativeChannelGlyphs:
    ExactUIGlyph $00,$44,$44,$44,$54,$54,$6c,$44 ; W
    ExactUIGlyph $00,$44,$44,$44,$44,$44,$28,$10 ; V
    ExactUIGlyph $00,$44,$64,$64,$54,$4c,$4c,$44 ; N
    ExactUIGlyph $00,$38,$44,$44,$44,$44,$44,$38 ; O
    ExactUIGlyph $00,$7c,$10,$10,$10,$10,$10,$7c ; I

IF DEF(NATIVE_CONNECT_PITCH_BENDS)
    INCLUDE "src/native/bend.asm"
ENDC

PUSHS
SECTION "Native channel colors", ROMX, BANK[5]
INCLUDE "src/native/color.asm"
POPS
