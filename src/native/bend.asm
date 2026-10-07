; Optional native-only pitch-bend joins. The default build does not include
; this file or its calls. All state fits before the waterfall's $cc20 inputs.
DEF NativeBendPreviousY EQU $cc02
DEF NativeBendPreviousNote EQU $cc05
DEF NativeBendPreviousVelocity EQU $cc08
DEF NativeBendPreviousOffset EQU $cc0b
DEF NativeBendPreviousRow EQU $cc11
DEF NativeBendPreviousActive EQU $cc14
DEF NativeBendConnectMask EQU $cc15
DEF NativeBendStateEnd EQU $cc16
ASSERT NativeBendStateEnd <= $cc20

; Called once with the LCD off. PreviousActive=0 prevents a startup join.
NativeBendInit:
    xor a
    ld hl,NativeBendPreviousY
    ld b,NativeBendStateEnd - NativeBendPreviousY
.clear
    ld [hl+],a
    dec b
    jr nz,.clear
    ret

NativeBendFrame:
    push af
    push bc
    push de
    push hl
    xor a
    ld [NativeBendConnectMask],a
    ; Decide all three voices before drawing. Long spans may be preempted
    ; by music IRQs, so never read fresh sequencer metadata during a span.
    FOR channel,3
        ; P/L use a signed 16-bit velocity. Keep only whether it is nonzero;
        ; the previous value also catches the final step of a completed bend.
        ld a,[$c2d0 + channel * 2]
        ld c,a
        ld a,[$c2d1 + channel * 2]
        or c
        ld c,a
        ld a,[NativeBendPreviousVelocity + channel]
        or c
        ld b,a
        ld a,c
        ld [NativeBendPreviousVelocity + channel],a
        ; A music IRQ can apply an instant effect between the frequency
        ; sample and these metadata reads. Keep the row's L/P eligibility
        ; so its changed pitch can still join on the following frame.
        ld a,[$c365 + channel]
        cp $0a ; L
        jr z,.effect{d:channel}
        cp $0d ; P
        jr nz,.effect_ready{d:channel}
.effect{d:channel}
        ld b,1
.effect_ready{d:channel}
        ; L00 and short slides can change offset entirely between samples,
        ; leaving zero velocity. Changes to either offset byte count too.
        FOR byte,2
            ld a,[$c337 + channel * 2 + byte]
            ld c,a
            ld a,[NativeBendPreviousOffset + channel * 2 + byte]
            xor c
            or b
            ld b,a
            ld a,c
            ld [NativeBendPreviousOffset + channel * 2 + byte],a
        ENDR
        ; An ordinary note on a newly observed phrase row starts a stroke,
        ; even if it retriggers the same base pitch after a bend. L notes
        ; intentionally preserve the old base and continue its stroke.
        ld a,[$c16c + channel]
        ld c,a
        ld a,[NativeBendPreviousRow + channel]
        cp c
        ld a,c
        ld [NativeBendPreviousRow + channel],a
        jr z,.row_ready{d:channel}
        ld a,[$c0cc + channel]
        or a
        jr z,.row_ready{d:channel}
        ld a,[$c365 + channel]
        cp $0a ; L
        jr z,.row_ready{d:channel}
        ld b,0
.row_ready{d:channel}
        ; The row parser may run before the oscillator resets. Also break
        ; at the delayed neutral-offset reset of a non-legato note, even
        ; when the row itself has not changed since the previous sample.
        ; These saved values have already been refreshed for this sample.
        ld a,[NativeBendPreviousVelocity + channel]
        or a
        jr nz,.reset_ready{d:channel}
        ld a,[NativeBendPreviousOffset + channel * 2]
        or a
        jr nz,.reset_ready{d:channel}
        ld a,[NativeBendPreviousOffset + channel * 2 + 1]
        cp $80
        jr nz,.reset_ready{d:channel}
        ld a,[$c0cc + channel]
        or a
        jr z,.reset_ready{d:channel}
        ld a,[$c365 + channel]
        cp $0a
        jr z,.reset_ready{d:channel}
        ld b,0
.reset_ready{d:channel}
        ld a,[$c0e8 + channel]
        ld c,a
        ld a,[NativeBendPreviousNote + channel]
        cp c
        ld a,c
        ld [NativeBendPreviousNote + channel],a
        jr nz,.disconnected{d:channel}
        ld a,b
        or a
        jr z,.disconnected{d:channel}
        ld a,[WaterfallActive]
        ld b,a
        ld a,[NativeBendPreviousActive]
        and b
        bit channel,a
        jr z,.disconnected{d:channel}
        ld hl,NativeBendConnectMask
        set channel,[hl]
.disconnected{d:channel}
    ENDR
    FOR channel,3
        ld a,[NativeBendConnectMask]
        bit channel,a
        jr z,.point{d:channel}
        ld a,[NativeBendPreviousY + channel]
        ld b,a
        ld a,[WaterfallPitchY + channel]
        ld c,a
        IF DEF(NATIVE_CHANNEL_COLORS)
            ld a,channel
            ld [NativeColorChannel],a
        ENDC
        call NativeBendSpan
.point{d:channel}
        ld a,[WaterfallPitchY + channel]
        ld [NativeBendPreviousY + channel],a
    ENDR
    ld a,[WaterfallActive]
    ld [NativeBendPreviousActive],a
    pop hl
    pop de
    pop bc
    pop af
    ret

; B=previous Y, C=current Y (0..143). Fill the inclusive vertical span in
; the newest pixel column. Equal pitches already have their ordinary point.
; All spans use the same ring/head mapping as WaterfallPixelPoint0..3.
NativeBendSpan:
    ld a,b
    cp c
    ret z
    jr c,.ordered
    ld b,c
    ld c,a
.ordered
    ld a,c
    sub b
    inc a
    ld c,a ; count, at most 144 rows
    ld a,b
    push af
    ld a,[WaterfallPixelPhase]
    ld e,a
    ld d,0
    ld hl,WaterfallPixelMasks
    add hl,de
    ld b,[hl]
    ld a,[WaterfallHead]
    add a,a
    ld e,a
    ld d,0
    ld hl,WaterfallPixelPointers
    add hl,de
    ld a,[hl+]
    ld d,[hl]
    ld e,a
    pop af
    ld l,a
    ld h,0
    add hl,hl
    add hl,de
    IF DEF(NATIVE_CHANNEL_COLORS)
        ldh a,[$ff90]
        or a
        jr nz,.color_pixel
    ENDC
.pixel
    ; Each read/store waits with IRQs available and masks only its own VRAM
    ; access. Audio may stretch rendering across frames; the existing frame
    ; scheduler then defers scrolling and drops redundant screen updates.
    call NativeRead
    or b
    call NativeStore
    inc hl
    inc hl
    dec c
    jr nz,.pixel
    ret
    IF DEF(NATIVE_CHANNEL_COLORS)
.color_pixel
        call NativeColorPixel
        inc hl
        inc hl
        dec c
        jr nz,.color_pixel
        ret
    ENDC
