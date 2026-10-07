; SPDX-License-Identifier: GPL-2.0-or-later
; A 144-pixel-high, right-anchored pitch history for CGB and DMG.
; Four black pixel pens on white, sampled once per display frame.
; The right 80 pixels scroll left one pixel per frame.
; A fixed sprite mask draws the piano pitch axis and divider at x=79.
;
; ABI $c920..$c923: four Y coordinates 0..143; $c924: active mask;
; $c925: integer music CPU percentage. All stages preserve registers/flags.
; Begin, clear/draw and map prepare hidden pitch history. Status/bar may run
; during preparation. Commit swaps window maps and moves noise sprites.
; The scheduler chooses fixed cadence.
; One spare pattern column and the inactive window map permit preparation
; in the preceding VBlanks without changing the visible pitch history.
; BG and window use disjoint map columns. Small windows use CPU map copies
; so the left status area can start immediately after the window columns.
; Sound registers, DIV, timers, IE and IF are never accessed.

ASSERT WATERFALL_WIDTH >= 8 && WATERFALL_WIDTH <= 80
ASSERT WATERFALL_WIDTH % 8 == 0
DEF WaterfallColumns EQU WATERFALL_WIDTH / 8
IF DEF(EXACT_PIXEL_WATERFALL)
    REDEF WaterfallColumns EQU 11
ENDC
DEF WaterfallRingColumns EQU WaterfallColumns + 1
IF DEF(EXACT_DMG) || WATERFALL_WIDTH < 32
    DEF WaterfallCPUMap EQU 1
ENDC
IF DEF(EXACT_DMG)
    DEF WaterfallPatternBase EQU $8280 ; reserve40 font/OBJ tiles,216canvas tiles
    DEF WaterfallClearCount EQU 12
ELSE
    DEF WaterfallPatternBase EQU $8000 ; CGB VRAM bank1
    DEF WaterfallClearCount EQU 3
ENDC

SECTION "Pitch waterfall input", WRAM0[$c920]
WaterfallPitchY:: ds 4
WaterfallActive:: ds 1
WaterfallCPU:: ds 1
WaterfallHead: ds 1
WaterfallLCD: ds 1
WaterfallNoiseHistory: ds WaterfallColumns
IF DEF(EXACT_WATERFALL_RAW)
SECTION "Pitch waterfall raw source", WRAM0[$c930]
WaterfallColumnBank:: ds 2
WaterfallColumnSource:: ds 2
ENDC
SECTION "Pitch waterfall stage state", WRAM0[$c940]
WaterfallColumnBase: ds 2
WaterfallMapSource: ds 2
WaterfallMapDestHigh: ds 1
WaterfallNextHead: ds 1
WaterfallNextLCD: ds 1
WaterfallPixelPhase: ds 1

MACRO WaterfallSave3
    push af
    push de
    push hl
ENDM
MACRO WaterfallRestore3
    pop hl
    pop de
    pop af
    ret
ENDM
MACRO WaterfallLoadPointer
    ld a,[\1]
    ld l,a
    ld a,[\1 + 1]
    ld h,a
ENDM

SECTION "Native pitch waterfall", ROM0[$1800]
WaterfallInit::
    push af
    push bc
    push de
    push hl
    xor a
    ld [WaterfallHead],a
    IF DEF(EXACT_PIXEL_WATERFALL)
        ld [WaterfallPixelPhase],a
    ENDC
    ld hl,WaterfallNoiseHistory
    ld b,WaterfallColumns
.history
    ld [hl+],a
    dec b
    jr nz,.history
    IF DEF(EXACT_PIXEL_WATERFALL)
        ld a,$f7
    ELSE
        ld a,$f3
    ENDC
    ld [WaterfallLCD],a
    IF DEF(EXACT_DMG)
        ld hl,WaterfallPatternBase
        ld b,WaterfallRingColumns * 18
        xor a
.patterns
        REPT 16
            ld [hl+],a
        ENDR
        dec b
        jr nz,.patterns
    ELSE
        ld a,1
        ldh [$ff4f],a
        FOR column,WaterfallRingColumns
            ld a,HIGH(WaterfallZeroColumn)
            ldh [$ff51],a
            ld a,LOW(WaterfallZeroColumn)
            ldh [$ff52],a
            ld a,HIGH(WaterfallPatternBase + column * 288)
            ldh [$ff53],a
            ld a,LOW(WaterfallPatternBase + column * 288)
            ldh [$ff54],a
            ld a,17
            ldh [$ff55],a
        ENDR
        ld a,$09
        FOR map,2
            FOR row,18
                ld hl,$9800 + map * $400 + row * 32
                REPT WaterfallColumns
                    ld [hl+],a
                ENDR
            ENDR
        ENDR
        xor a
        ldh [$ff4f],a
    ENDC
    ld de,WaterfallMaps
    ld h,$98
    call WaterfallInitialMap
    ld de,WaterfallMaps
    ld h,$9c
    call WaterfallInitialMap
    ld hl,$fe00
    ld b,160
    xor a
.oam
    ld [hl+],a
    dec b
    jr nz,.oam
    IF DEF(EXACT_PIXEL_WATERFALL)
        FOR stripe,9
            ld hl,$fe00 + stripe * 4
            ld a,16 + stripe * 16
            ld [hl+],a
            ld a,80 ; OAM X=screen X+8: keyboard72..78, divider79
            ld [hl+],a
            ld a,38 ; opaque margin and divider, without piano keys
            ld [hl+],a
            IF DEF(EXACT_DMG)
                ld a,$11
            ELSE
                ld a,1
            ENDC
            ld [hl],a
        ENDR
        ; Nine black sprites make a72-pixel bar. An overlapping final
        ; sprite, clipped at the left edge for short fills, gives1px steps.
        ; With the keyboard this uses at most10sprites on each scanline.
        FOR pen,8
            ld hl,$fe24 + pen * 4
            ld a,72
            ld [hl+],a
            inc hl
            ld a,26
            ld [hl+],a
            xor a
            ld [hl+],a
        ENDR
        ; Repeat the twelve-semitone pattern at the right edge. Overlapping
        ; rows agree, so the ten-sprite scanline limit may discard the later
        ; duplicate during the CPU bar without hiding any keyboard pixels.
        FOR stripe,12
            ld hl,$fe48 + stripe * 4
            ld a,16 + stripe * 12
            ld [hl+],a
            ld a,160
            ld [hl+],a
            ld a,26
            ld [hl+],a
            IF DEF(EXACT_DMG)
                ld a,$31
            ELSE
                ld a,$21
            ENDC
            ld [hl],a
        ENDR
    ELSE
    FOR column,WaterfallColumns
        ld hl,$fe01 + column * 4
        ld a,160 - WATERFALL_WIDTH + column * 8 + 8
        ld [hl+],a
        ld a,26
        ld [hl+],a
        xor a
        ld [hl],a
    ENDR
    ENDC
    IF DEF(EXACT_DMG)
        ld a,$03
        ldh [$ff47],a
        ld a,$0c
        ldh [$ff49],a
        ld a,$00
        ldh [$ff48],a
        ; CGB honors these palettes; DMG ignores the color registers. Keep
        ; both hardware paths at normal CPU speed with identical init cost.
        ld a,$80
        ldh [$ff6a],a
        ld hl,WaterfallBarPalette
        ld b,8
.dmg_bar_palette
        ld a,[hl+]
        ldh [$ff6b],a
        dec b
        jr nz,.dmg_bar_palette
        ld a,$88
        ldh [$ff6a],a
        ld hl,WaterfallNoisePalette
        ld b,8
.dmg_piano_palette
        ld a,[hl+]
        ldh [$ff6b],a
        dec b
        jr nz,.dmg_piano_palette
    ELSE
        ; OBJ palette0 uses the lightest SGB color for the CPU bar.
        ld a,$80
        ldh [$ff6a],a
        ld hl,WaterfallBarPalette
        ld b,8
.bar_palette
        ld a,[hl+]
        ldh [$ff6b],a
        dec b
        jr nz,.bar_palette
        REPT 1
            nop
        ENDR
        ld a,$88
        ldh [$ff68],a
        ld hl,WaterfallBackgroundPalette
        ld b,8
.background_palette
        ld a,[hl+]
        ldh [$ff69],a
        dec b
        jr nz,.background_palette
        ld a,$88
        ldh [$ff6a],a
        ld hl,WaterfallNoisePalette
        ld b,8
.noise_palette
        ld a,[hl+]
        ldh [$ff6b],a
        dec b
        jr nz,.noise_palette
    ENDC
    ld a,167 - WATERFALL_WIDTH
    ldh [$ff4b],a
    xor a
    ldh [$ff4a],a
    ld a,ExactUILeftMapColumn * 8
    ldh [$ff43],a
    pop hl
    pop de
    pop bc
    pop af
    ret

; Prepare an offscreen column and inactive map for the *future* commit.
WaterfallBegin::
    WaterfallSave3
    ld a,[WaterfallHead]
    add a,a
    ld e,a
    ld d,0
    ld hl,WaterfallColumnPointers
    add hl,de
    ld a,[hl+]
    ld [WaterfallColumnBase],a
    ld a,[hl]
    ld [WaterfallColumnBase + 1],a
    ld a,[WaterfallHead]
    inc a
    cp WaterfallRingColumns
    jr c,.next_ready
    xor a
.next_ready
    ld [WaterfallNextHead],a
    add a,a
    ld e,a
    ld d,0
    ld hl,WaterfallMapPointers
    add hl,de
    ld a,[hl+]
    ld [WaterfallMapSource],a
    ld a,[hl]
    ld [WaterfallMapSource + 1],a
    ld a,[WaterfallLCD]
    xor $40
    ld [WaterfallNextLCD],a
    and $40
    REPT 4
        rrca
    ENDR
    or $98
    ld [WaterfallMapDestHigh],a
    WaterfallRestore3

IF DEF(EXACT_WATERFALL_RAW)
    IF DEF(EXACT_DMG)
        DEF WaterfallUploadCount EQU 12
    ELSE
        DEF WaterfallUploadCount EQU 3
    ENDC
    DEF WaterfallUploadBytes EQU 288 / WaterfallUploadCount
FOR part,WaterfallUploadCount
WaterfallUpload{d:part}::
    WaterfallSave3
    ld a,[WaterfallColumnBank + 1]
    ld [$3000],a
    ld a,[WaterfallColumnBank]
    ld [$2000],a
    IF DEF(EXACT_DMG)
        WaterfallLoadPointer WaterfallColumnBase
        IF part > 0
            ld de,part * WaterfallUploadBytes
            add hl,de
        ENDC
        ld a,[WaterfallColumnSource]
        ld e,a
        ld a,[WaterfallColumnSource + 1]
        ld d,a
        IF part > 0
            ld a,e
            add LOW(part * WaterfallUploadBytes)
            ld e,a
            ld a,d
            adc HIGH(part * WaterfallUploadBytes)
            ld d,a
        ENDC
        REPT WaterfallUploadBytes
            ld a,[de]
            inc de
            ld [hl+],a
        ENDR
    ELSE
        WaterfallLoadPointer WaterfallColumnSource
        IF part > 0
            ld de,part * WaterfallUploadBytes
            add hl,de
        ENDC
        ld a,h
        ldh [$ff51],a
        ld a,l
        ldh [$ff52],a
        WaterfallLoadPointer WaterfallColumnBase
        IF part > 0
            ld de,part * WaterfallUploadBytes
            add hl,de
        ENDC
        ld a,h
        ldh [$ff53],a
        ld a,l
        ldh [$ff54],a
        ld a,1
        ldh [$ff4f],a
        ld a,WaterfallUploadBytes / 16 - 1
        ldh [$ff55],a
        xor a
        ldh [$ff4f],a
    ENDC
    ; Return to the bank containing the suspended audio function.
    ld a,[$c905] ; CompactFunctionBank high
    ld [$3000],a
    ld a,[$c904] ; CompactFunctionBank low
    ld [$2000],a
    WaterfallRestore3
ENDR
ELSE
FOR part,WaterfallClearCount
WaterfallClear{d:part}::
    WaterfallSave3
    WaterfallLoadPointer WaterfallColumnBase
    IF part > 0
        ld de,part * (288 / WaterfallClearCount)
        add hl,de
    ENDC
    IF DEF(EXACT_DMG)
        xor a
        REPT 288 / WaterfallClearCount
            ld [hl+],a
        ENDR
    ELSE
        ld a,1
        ldh [$ff4f],a
        ld a,HIGH(WaterfallZeroColumn)
        ldh [$ff51],a
        ld a,LOW(WaterfallZeroColumn)
        ldh [$ff52],a
        ld a,h
        ldh [$ff53],a
        ld a,l
        ldh [$ff54],a
        ld a,(18 / WaterfallClearCount) - 1
        ldh [$ff55],a
        xor a
        ldh [$ff4f],a
    ENDC
    WaterfallRestore3
ENDR

WaterfallDraw::
    WaterfallSave3
    ld a,[WaterfallColumnBase]
    ld e,a
    ld a,[WaterfallColumnBase + 1]
    ld d,a
    IF !DEF(EXACT_DMG)
        ld a,1
        ldh [$ff4f],a
    ENDC
    FOR channel,3
        ld a,[WaterfallPitchY + channel]
        ld l,a
        ld h,0
        add hl,hl
        add hl,de
        ld a,[WaterfallActive]
        bit channel,a
        jr z,.inactive\@
        nop
        jr .draw\@
.inactive\@
        ld hl,$8ff0
.draw\@
        IF (channel + 1) & 1
            ld a,$ff
        ELSE
            ld a,0
        ENDC
        ld [hl+],a
        IF (channel + 1) & 2
            ld a,$ff
        ELSE
            ld a,0
        ENDC
        ld [hl],a
    ENDR
    IF !DEF(EXACT_DMG)
        xor a
        ldh [$ff4f],a
    ENDC
    WaterfallRestore3
ENDC

; Nine two-row pieces: all CPU-copy DMG pieces stay below 1,000 cycles even
; at width80. CGB uses two one-block GDMA transfers per piece.
IF DEF(EXACT_DMG) && DEF(EXACT_PIXEL_WATERFALL)
    DEF WaterfallMapCount EQU 18
    DEF WaterfallMapRows EQU 1
ELSE
    DEF WaterfallMapCount EQU 9
    DEF WaterfallMapRows EQU 2
ENDC
FOR part,WaterfallMapCount
WaterfallMap{d:part}::
    WaterfallSave3
    WaterfallLoadPointer WaterfallMapSource
    IF part > 0
        ld de,part * WaterfallMapRows * 16
        add hl,de
    ENDC
    ld d,h
    ld e,l
    ld a,[WaterfallMapDestHigh]
    IF part * WaterfallMapRows >= 8
        add (part * WaterfallMapRows) / 8
    ENDC
    ld h,a
    IF !DEF(WaterfallCPUMap)
        ld a,d
        ldh [$ff51],a
        ld a,e
        ldh [$ff52],a
        ld a,h
        ldh [$ff53],a
    ENDC
    FOR row,part * WaterfallMapRows,part * WaterfallMapRows + WaterfallMapRows
        IF row > part * WaterfallMapRows && row % 8 == 0
            inc h
            IF !DEF(WaterfallCPUMap)
                ld a,h
                ldh [$ff53],a
            ENDC
        ENDC
        IF DEF(WaterfallCPUMap)
            ld l,LOW(row * 32)
            REPT WaterfallColumns
                ld a,[de]
                inc de
                ld [hl+],a
            ENDR
            ld a,e
            add 16 - WaterfallColumns
            ld e,a
            ld a,d
            adc 0
            ld d,a
        ELSE
            ld a,LOW(row * 32)
            ldh [$ff54],a
            xor a
            ldh [$ff55],a
        ENDC
    ENDR
    WaterfallRestore3
ENDR

WaterfallStatus::
    push af
    push bc
    push de
    push hl
    ld a,[WaterfallCPU]
    ld d,a
    ld c,a
    ld b,0
    ld hl,ExactUIValues
    sla c
    rl b
    sla c
    rl b
    add hl,bc
    ld a,[hl+]
    ld [ExactUICPUValue],a
    ld a,[hl+]
    ld [ExactUICPUValue + 1],a
    ld a,[hl+]
    ld [ExactUICPUValue + 2],a
    ld a,100
    sub d
    ld c,a
    ld b,0
    ld hl,ExactUIValues
    sla c
    rl b
    sla c
    rl b
    add hl,bc
    ld a,[hl+]
    ld [ExactUIFreeValue],a
    ld a,[hl+]
    ld [ExactUIFreeValue + 1],a
    ld a,[hl]
    ld [ExactUIFreeValue + 2],a
    pop hl
    pop de
    pop bc
    pop af
    ret

WaterfallBar::
    WaterfallSave3
    ld a,[WaterfallCPU]
    ld l,a
    ld h,0
    REPT 4
        add hl,hl
    ENDR
    ld de,ExactUIWaterfallBars
    add hl,de
    IF DEF(EXACT_PIXEL_WATERFALL)
        FOR pen,9
            ld a,[hl+]
            ld [$fe25 + pen * 4],a
        ENDR
    ELSE
    ld d,h
    ld e,l
    IF DEF(EXACT_DMG) || WATERFALL_WIDTH < 32
        ld hl,ExactUIBar
        REPT 16
            ld a,[de]
            inc de
            ld [hl+],a
        ENDR
        IF WaterfallColumns == 1
            ; The widest status area has a seventeenth cell. Its only full
            ; input is 100%; both branches have the same duration.
            ld a,[WaterfallCPU]
            cp 100
            jr z,.full
            ld a,ExactUITileEmpty
            jr .last
.full
            ld a,ExactUITileFull
            nop
            nop
.last
            ld [hl],a
        ENDC
    ELSE
        ld a,d
        ldh [$ff51],a
        ld a,e
        ldh [$ff52],a
        ld a,HIGH(ExactUIBar)
        ldh [$ff53],a
        ld a,LOW(ExactUIBar)
        ldh [$ff54],a
        xor a
        ldh [$ff55],a
    ENDC
    ENDC
    WaterfallRestore3

WaterfallCommit::
    WaterfallSave3
IF !DEF(EXACT_WATERFALL_RAW)
    ld hl,WaterfallNoiseHistory + 1
    ld de,WaterfallNoiseHistory
    REPT WaterfallColumns - 1
        ld a,[hl+]
        ld [de],a
        inc de
    ENDR
    ld a,[WaterfallPitchY + 3]
    add 16
    ld d,a
    ld a,[WaterfallActive]
    bit 3,a
    jr z,.inactive
    ld a,d
    jr .ready
.inactive
    xor a
    nop
    nop
.ready
    ld [WaterfallNoiseHistory + WaterfallColumns - 1],a
    FOR column,WaterfallColumns
        ld a,[WaterfallNoiseHistory + column]
        ld [$fe00 + column * 4],a
    ENDR
ENDC
    ld a,[WaterfallNextHead]
    ld [WaterfallHead],a
    ld a,[WaterfallNextLCD]
    ld [WaterfallLCD],a
    ldh [$ff40],a
    WaterfallRestore3

; Convenience entry for tests/callers with a sufficiently large blanking gap.
; Production players schedule the exported preparation/commit stages.
WaterfallUpdate::
    call WaterfallBegin
    IF DEF(EXACT_WATERFALL_RAW)
        FOR part,WaterfallUploadCount
            call WaterfallUpload{d:part}
        ENDR
    ELSE
    FOR part,WaterfallClearCount
        call WaterfallClear{d:part}
    ENDR
    call WaterfallDraw
    ENDC
    FOR part,WaterfallMapCount
        call WaterfallMap{d:part}
    ENDR
    call WaterfallStatus
    call WaterfallBar
    call WaterfallCommit
    ret

; Initial maps are built while LCD is disabled. H=destination map high byte.
WaterfallInitialMap:
    IF !DEF(WaterfallCPUMap)
        ld a,d
        ldh [$ff51],a
        ld a,e
        ldh [$ff52],a
        ld a,h
        ldh [$ff53],a
    ENDC
    FOR row,18
        IF row > 0 && row % 8 == 0
            inc h
            IF !DEF(WaterfallCPUMap)
                ld a,h
                ldh [$ff53],a
            ENDC
        ENDC
        IF DEF(WaterfallCPUMap)
            ld l,LOW(row * 32)
            REPT WaterfallColumns
                ld a,[de]
                inc de
                ld [hl+],a
            ENDR
            ld a,e
            add 16 - WaterfallColumns
            ld e,a
            ld a,d
            adc 0
            ld d,a
        ELSE
            ld a,LOW(row * 32)
            ldh [$ff54],a
            xor a
            ldh [$ff55],a
        ENDC
    ENDR
    ret

IF DEF(EXACT_PIXEL_WATERFALL)
WaterfallPixel::
    push af
    push bc
    push de
    push hl
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
    IF !DEF(EXACT_DMG)
        ld a,1
        ldh [$ff4f],a
    ENDC
    FOR channel,4
        ld a,[WaterfallPitchY + channel]
        ld l,a
        ld h,0
        add hl,hl
        add hl,de
        ld a,[WaterfallActive]
        bit channel,a
        jr z,.inactive\@
        nop
        nop
        jr .plot\@
.inactive\@
        ld hl,$8ff0
        nop
.plot\@
        ld a,[hl]
        or b
        ld [hl],a
    ENDR
    IF !DEF(EXACT_DMG)
        xor a
        ldh [$ff4f],a
    ENDC
    ld a,[WaterfallPixelPhase]
    inc a
    cp 8
    jr c,.fine
    xor a
    ld [WaterfallPixelPhase],a
    ld a,[WaterfallNextHead]
    ld [WaterfallHead],a
    ld a,[WaterfallNextLCD]
    ld [WaterfallLCD],a
    ldh [$ff40],a
    ld a,87
    ldh [$ff4b],a
    jr .done
.fine
    ld [WaterfallPixelPhase],a
    ld b,a
    ld a,87
    sub b
    ldh [$ff4b],a
    REPT 20
        nop
    ENDR
.done
    pop hl
    pop de
    pop bc
    pop af
    ret
IF DEF(EXACT_DMG)
FOR channel,4
WaterfallPixelPoint{d:channel}::
    push af
    push bc
    push de
    push hl
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
    ld a,[WaterfallPitchY + channel]
    ld l,a
    ld h,0
    add hl,hl
    add hl,de
    ld a,[WaterfallActive]
    bit channel,a
    jr z,.inactive
    nop
    nop
    jr .plot
.inactive
    ld hl,$8ff0
    nop
.plot
    ld a,[hl]
    or b
    ld [hl],a
    pop hl
    pop de
    pop bc
    pop af
    ret
    DEF WaterfallPixelPoint{d:channel}Cycles EQU 396
    EXPORT WaterfallPixelPoint{d:channel}Cycles
ENDR
WaterfallPixelScroll::
    push af
    push bc
    ld a,[WaterfallPixelPhase]
    inc a
    cp 8
    jr c,.fine
    xor a
    ld [WaterfallPixelPhase],a
    ld a,[WaterfallNextHead]
    ld [WaterfallHead],a
    ld a,[WaterfallNextLCD]
    ld [WaterfallLCD],a
    ldh [$ff40],a
    ld a,87
    ldh [$ff4b],a
    jr .done
.fine
    ld [WaterfallPixelPhase],a
    ld b,a
    ld a,87
    sub b
    ldh [$ff4b],a
    REPT 20
        nop
    ENDR
.done
    pop bc
    pop af
    ret
DEF WaterfallPixelScrollCycles EQU 260
EXPORT WaterfallPixelScrollCycles
ENDC
WaterfallPixelMasks:
    db $80,$40,$20,$10,$08,$04,$02,$01
WaterfallPixelPointers:
    FOR head,WaterfallRingColumns
        dw WaterfallPatternBase + ((head + 10) % WaterfallRingColumns) * 288
    ENDR
IF DEF(EXACT_DMG)
    DEF WaterfallPixelCycles EQU 908
ELSE
    DEF WaterfallPixelCycles EQU 944
ENDC
EXPORT WaterfallPixelCycles
ENDC

WaterfallColumnPointers:
    FOR head,WaterfallRingColumns
        dw WaterfallPatternBase + ((head + WaterfallColumns) % WaterfallRingColumns) * 288
    ENDR
WaterfallMapPointers:
    FOR head,WaterfallRingColumns
        dw WaterfallMaps + head * 288
    ENDR
WaterfallBackgroundPalette:
    dw $2866,$67bf,$10b5,$265b
WaterfallNoisePalette:
    dw $2866,$2866,$10b5,$67bf
WaterfallBarPalette:
    dw $2866,$67bf,$67bf,$67bf
    ds (-@) & 15,0
IF !DEF(EXACT_DMG)
WaterfallZeroColumn:
    ds 288,0
ENDC
WaterfallMaps:
    FOR head,WaterfallRingColumns
        FOR row,18
            FOR column,WaterfallColumns
                db ((head + column) % WaterfallRingColumns) * 18 + row + (WaterfallPatternBase - $8000) / 16
            ENDR
            ds 16 - WaterfallColumns,0
        ENDR
    ENDR

; Fixed CALL-inclusive costs. Actual-core regression tests verify each input
; path, ring head and both hardware modes against these exported values.
DEF WaterfallBeginCycles EQU 452
IF DEF(EXACT_WATERFALL_RAW)
    IF DEF(EXACT_DMG)
        DEF WaterfallUpload0Cycles EQU 908
        DEF WaterfallUploadExtraCycles EQU 52
    ELSE
        DEF WaterfallUpload0Cycles EQU 840
        DEF WaterfallUploadExtraCycles EQU 40
    ENDC
    FOR part,1,WaterfallUploadCount
        DEF WaterfallUpload{d:part}Cycles EQU WaterfallUpload0Cycles + WaterfallUploadExtraCycles
    ENDR
ENDC
IF DEF(EXACT_DMG)
    DEF WaterfallClear0Cycles EQU 360
    DEF WaterfallDrawCycles EQU 536
    DEF WaterfallMap0Cycles EQU 192 + 40 * WaterfallMapRows + 24 * WaterfallMapRows * WaterfallColumns
    DEF WaterfallBarCycles EQU 608
ELSE
    DEF WaterfallClear0Cycles EQU 680
    DEF WaterfallDrawCycles EQU 572
    DEF WaterfallMap0Cycles EQU 448
    DEF WaterfallBarCycles EQU 368
ENDC
IF !DEF(EXACT_DMG) && DEF(WaterfallCPUMap)
    REDEF WaterfallMap0Cycles EQU 192 + 40 * WaterfallMapRows + 24 * WaterfallMapRows * WaterfallColumns
    REDEF WaterfallBarCycles EQU 608
ENDC
IF WaterfallColumns == 1
    REDEF WaterfallBarCycles EQU WaterfallBarCycles + 60
ENDC
FOR part,1,WaterfallClearCount
    DEF WaterfallClear{d:part}Cycles EQU WaterfallClear0Cycles + 20
ENDR
FOR part,1,WaterfallMapCount
    DEF WaterfallMap{d:part}Cycles EQU WaterfallMap0Cycles + 20 + (part * WaterfallMapRows >= 8) * 8
ENDR
DEF WaterfallStatusCycles EQU 456
IF DEF(EXACT_PIXEL_WATERFALL)
    REDEF WaterfallBarCycles EQU 420
ENDC
DEF WaterfallCommitCycles EQU 292 + 56 * WaterfallColumns
DEF WaterfallPrepareStageCount EQU WaterfallClearCount + WaterfallMapCount + 4
DEF WaterfallStageCount EQU WaterfallPrepareStageCount + 1
DEF WaterfallUpdateCycles EQU 40 + WaterfallBeginCycles + WaterfallClear0Cycles + (WaterfallClearCount - 1) * (WaterfallClear0Cycles + 20) + WaterfallDrawCycles + WaterfallMap0Cycles * 9 + 8 * 20 + 5 * 8 + WaterfallStatusCycles + WaterfallBarCycles + WaterfallCommitCycles
IF DEF(EXACT_WATERFALL_RAW)
    REDEF WaterfallCommitCycles EQU 200
    REDEF WaterfallPrepareStageCount EQU WaterfallUploadCount + 12
    REDEF WaterfallStageCount EQU WaterfallPrepareStageCount + 1
    REDEF WaterfallUpdateCycles EQU 40 + WaterfallBeginCycles + WaterfallUpload0Cycles + (WaterfallUploadCount - 1) * (WaterfallUpload0Cycles + WaterfallUploadExtraCycles) + WaterfallMap0Cycles * 9 + 8 * 20 + 5 * 8 + WaterfallStatusCycles + WaterfallBarCycles + WaterfallCommitCycles
    DEF WaterfallRawPrepareStageCount EQU WaterfallPrepareStageCount
    DEF WaterfallRawStageCount EQU WaterfallStageCount
    DEF WaterfallRawUpdateCycles EQU WaterfallUpdateCycles
    EXPORT WaterfallRawPrepareStageCount,WaterfallRawStageCount,WaterfallRawUpdateCycles
ELSE
    EXPORT WaterfallDrawCycles
ENDC
EXPORT WaterfallBeginCycles,WaterfallStatusCycles,WaterfallBarCycles,WaterfallCommitCycles
EXPORT WaterfallStageCount,WaterfallPrepareStageCount,WaterfallUpdateCycles
WaterfallStageTable::
    dw WaterfallBegin,WaterfallBeginCycles
    IF DEF(EXACT_WATERFALL_RAW)
        FOR part,WaterfallUploadCount
            dw WaterfallUpload{d:part},WaterfallUpload{d:part}Cycles
        ENDR
    ELSE
    FOR part,WaterfallClearCount
        dw WaterfallClear{d:part},WaterfallClear{d:part}Cycles
    ENDR
    dw WaterfallDraw,WaterfallDrawCycles
    ENDC
    FOR part,WaterfallMapCount
        dw WaterfallMap{d:part},WaterfallMap{d:part}Cycles
    ENDR
    dw WaterfallStatus,WaterfallStatusCycles
    dw WaterfallBar,WaterfallBarCycles
    dw WaterfallCommit,WaterfallCommitCycles
WaterfallEnd::
IF DEF(EXACT_WATERFALL_RAW) && DEF(EXACT_DMG)
    ASSERT WaterfallEnd <= $4000, STRFMT("Waterfall module ends at $%x",WaterfallEnd)
ELIF DEF(EXACT_PIXEL_WATERFALL)
    ASSERT WaterfallEnd <= $4000
ELSE
    ASSERT WaterfallEnd <= $3000, STRFMT("Waterfall module ends at $%x",WaterfallEnd)
ENDC
