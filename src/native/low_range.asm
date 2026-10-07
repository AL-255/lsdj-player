; One optional mode: three tonal pens, original Y=72..143, two pixels/key.
; Keep the full 80-pixel history width and the sampled pitches unchanged
; for note readouts and bend event detection.
; The caller selects the hardware entrypoint once, outside all pixel loops.
MACRO NativeLowRangePointsBody
    call NativeLowRangePointsSetup
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
        IF \1
            ld a,channel
            ld [NativeColorChannel],a
            call NativeColorPixel
        ELSE
            call NativeLowRangeMonochromePixel
        ENDC
.next{d:channel}
    ENDR
    ret
ENDM

IF !DEF(NATIVE_CHANNEL_COLORS)
NativeLowRangePoints::
NativeLowRangePointsCGB::
ENDC
NativeLowRangePointsDMG::
    NativeLowRangePointsBody 0

IF DEF(NATIVE_CHANNEL_COLORS)
NativeLowRangePoints::
NativeLowRangePointsCGB::
    NativeLowRangePointsBody 1
ENDC

NativeLowRangePointsSetup:
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
