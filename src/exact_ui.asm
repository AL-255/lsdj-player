; SPDX-License-Identifier: GPL-2.0-or-later
; Native display for the exact bus-timed player.
;
; ExactUIInit: CALL at normal speed with LCD timing unconstrained and IE=0.
; It preserves AF/BC/DE/HL, disables LCD while preparing VRAM, then enables
; LCDC=$91 (legacy status) or $f3 (pitch history). It never writes APU
; registers, DIV, or timers.
;
; ExactUIUpdate: A=integer music CPU percentage (0..100). CALL only during
; VBlank with enough remaining cycles. Preserves AF/BC/DE/HL. Constant duration
; for every input, including the 20-tile bar. No interrupts or waiting.
;
; Legacy status uses ROM0 $0220..$07ff. Pitch history additionally uses
; ROM0 below $1000 and $1800..$2fff, with the replay entry at $3000.
; Timing constants are in T-cycles and INCLUDE the caller's CALL and RET.
; At double speed, a T-cycle is one 8 MHz tick; at normal speed it is two.
; The LCD-on bus-write offset is from the beginning of CALL, not function entry.

IF DEF(WATERFALL_WIDTH) && !DEF(EXACT_WATERFALL)
    DEF EXACT_WATERFALL EQU 1
ENDC
IF DEF(EXACT_WATERFALL)
    IF !DEF(WATERFALL_WIDTH)
        DEF WATERFALL_WIDTH EQU 48
    ENDC
    DEF ExactUIBarTiles EQU 18 - WATERFALL_WIDTH / 8
    IF WATERFALL_WIDTH < 32
        DEF ExactUILeftMapColumn EQU WATERFALL_WIDTH / 8
    ELSE
        DEF ExactUILeftMapColumn EQU 16
    ENDC
    IF DEF(EXACT_DMG)
        DEF ExactUIInitCycles EQU 82420 + 3512 * (WATERFALL_WIDTH / 8)
    ELIF WATERFALL_WIDTH < 32
        DEF ExactUIInitCycles EQU 81580 + 1888 * (WATERFALL_WIDTH / 8)
    ELSE
        DEF ExactUIInitCycles EQU 82892 + 1024 * (WATERFALL_WIDTH / 8)
    ENDC
    IF DEF(EXACT_PIXEL_WATERFALL)
        IF DEF(EXACT_DMG)
            REDEF ExactUIInitCycles EQU 128748
        ELSE
            REDEF ExactUIInitCycles EQU 105160
        ENDC
    ENDC
    IF DEF(EXACT_DMG)
        DEF ExactUIInitLCDOnWriteCycles EQU ExactUIInitCycles - 69
    ELSE
        DEF ExactUIInitLCDOnWriteCycles EQU ExactUIInitCycles - 68
    ENDC
    DEF ExactUIUpdateCycles EQU 1692 - 68 * (WATERFALL_WIDTH / 8)
ELSE
    DEF ExactUIBarTiles EQU 20
    DEF ExactUIInitCycles EQU 73820
    DEF ExactUIInitLCDOnWriteCycles EQU 73752
    DEF ExactUIUpdateCycles EQU 1828
ENDC
DEF ExactUILCDOnCycles EQU ExactUIInitLCDOnWriteCycles
EXPORT ExactUIInitCycles,ExactUILCDOnCycles,ExactUIUpdateCycles

DEF ExactUITile0 EQU 13
DEF ExactUITilePercent EQU 23
IF DEF(EXACT_PIXEL_WATERFALL)
    DEF ExactUITileFull EQU 26 ; keyboard tile is solid black with BG palette0
    DEF ExactUITileEmpty EQU 0
ELSE
    DEF ExactUITileFull EQU 24
    DEF ExactUITileEmpty EQU 25
ENDC
IF DEF(EXACT_WATERFALL)
    DEF ExactUICPUValue EQU $98a5 + ExactUILeftMapColumn
    DEF ExactUIFreeValue EQU $9945 + ExactUILeftMapColumn
    DEF ExactUIBar EQU $98e0 + ExactUILeftMapColumn
ELSE
    DEF ExactUICPUValue EQU $98ad
    DEF ExactUIFreeValue EQU $994d
    DEF ExactUIBar EQU $98e0
ENDC

SECTION "Exact native CGB UI", ROM0[$0220]
ExactUIInit::
    push af
    push bc
    push de
    push hl
    xor a
    ldh [$ff40],a
    ldh [$ff42],a
    ldh [$ff43],a
    ldh [$ff4f],a

    ; Twenty-six font tiles, unsigned tile addresses starting at $8000.
    ld de,ExactUIFont
    ld hl,$8000
    IF DEF(EXACT_DMG) && DEF(EXACT_PIXEL_WATERFALL)
        ld bc,ExactUIFontEnd-ExactUIFont-64
    ELSE
        ld bc,ExactUIFontEnd-ExactUIFont
    ENDC
.font
    ld a,[de]
    inc de
    ld [hl+],a
    dec bc
    ld a,b
    or c
    jr nz,.font
    IF DEF(EXACT_DMG) && DEF(EXACT_PIXEL_WATERFALL)
        ; Extra keyboard patterns cost1536 cycles. Skip64 redundant map
        ; writes below to keep the original LCD/audio phase on DMG.
        REPT 64
            ld a,[de]
            inc de
            ld [hl+],a
        ENDR
    ENDC

    ; Each map clear takes 24652 cycles, including setup.
    ld hl,$9800
    xor a
    ld b,4
    ld c,0
.map
    ld [hl+],a
    dec c
    jr nz,.map
    dec b
    jr nz,.map
    ld a,1
    ldh [$ff4f],a
    ld hl,$9800
    xor a
    IF DEF(EXACT_DMG) && DEF(EXACT_PIXEL_WATERFALL)
        ; Clear both visible attribute maps on CGB. Unrolling eight stores
        ; preserves the DMG initialization budget (eight NOP cycles removed
        ; from WaterfallInit). The final16 bytes are outside the display.
        ld b,254
.attrs
        REPT 8
            ld [hl+],a
        ENDR
        dec b
        jr nz,.attrs
    ELSE
        ld b,4
        IF DEF(EXACT_PIXEL_WATERFALL)
            ld c,154
        ELSE
            ld c,0
        ENDC
.attrs
        ld [hl+],a
        dec c
        jr nz,.attrs
        dec b
        jr nz,.attrs
    ENDC
    xor a
    ldh [$ff4f],a

    ; White background and black text on both models.
    ld a,$80
    ldh [$ff68],a
    ld hl,ExactUIPalette
    ld b,8
.palette
    ld a,[hl+]
    ldh [$ff69],a
    dec b
    jr nz,.palette

    ld de,ExactUITitle
    IF DEF(EXACT_WATERFALL)
        ld hl,$9821 + ExactUILeftMapColumn
        ld b,8
    ELSE
        ld hl,$9844
        ld b,12
    ENDC
.title
    ld a,[de]
    inc de
    ld [hl+],a
    dec b
    jr nz,.title
    IF DEF(EXACT_WATERFALL)
        ld de,ExactUITitleBottom
        ld hl,$9841 + ExactUILeftMapColumn
        ld b,6
.title_bottom
        ld a,[de]
        inc de
        ld [hl+],a
        dec b
        jr nz,.title_bottom
    ENDC
    ld de,ExactUICPUText
    IF DEF(EXACT_WATERFALL)
        ld hl,$98a1 + ExactUILeftMapColumn
        ld b,8
    ELSE
        ld hl,$98a2
        ld b,15
    ENDC
.cpu
    ld a,[de]
    inc de
    ld [hl+],a
    dec b
    jr nz,.cpu
    ld de,ExactUIFreeText
    IF DEF(EXACT_WATERFALL)
        ld hl,$9941 + ExactUILeftMapColumn
        ld b,8
    ELSE
        ld hl,$9942
        ld b,15
    ENDC
.free
    ld a,[de]
    inc de
    ld [hl+],a
    dec b
    jr nz,.free

    IF DEF(EXACT_PIXEL_WATERFALL)
        ld de,ExactUIBPMText
        ld hl,$9981 + ExactUILeftMapColumn
        ld b,8
.bpm
        ld a,[de]
        inc de
        ld [hl+],a
        dec b
        jr nz,.bpm
        ld de,ExactUISongText
        ld hl,$9a01 + ExactUILeftMapColumn
        ld b,8
.song
        ld a,[de]
        inc de
        ld [hl+],a
        dec b
        jr nz,.song
    ENDC

    ld hl,ExactUIBar
    ld a,ExactUITileEmpty
    ld b,ExactUIBarTiles
.bar
    ld [hl+],a
    dec b
    jr nz,.bar
    IF DEF(EXACT_WATERFALL)
        call WaterfallInit
        IF DEF(EXACT_PIXEL_WATERFALL)
            ld a,$f7
        ELSE
            ld a,$f3
        ENDC
    ELSE
        ld a,$91
    ENDC
    ldh [$ff40],a
ExactUIInitLCDOn::
    pop hl
    pop de
    pop bc
    pop af
    ret
ExactUIInitEnd::

ExactUIUpdate::
    push af
    push bc
    push de
    push hl
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
    ld [ExactUICPUValue+1],a
    ld a,[hl+]
    ld [ExactUICPUValue+2],a
    ld a,[hl]
    ld c,a
    ld hl,ExactUIBar
    ld b,ExactUIBarTiles
.bar
    ld a,c
    sub 1
    jr c,.empty
    ld c,a
    ld a,ExactUITileFull
    ld [hl+],a
    jr .next
.empty
    ld a,ExactUITileEmpty
    ld [hl+],a
    nop
    nop
    nop
.next
    dec b
    jr nz,.bar

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
    ld [ExactUIFreeValue+1],a
    ld a,[hl]
    ld [ExactUIFreeValue+2],a
    pop hl
    pop de
    pop bc
    pop af
    ret
ExactUIUpdateEnd::

ExactUIPalette:
    IF DEF(EXACT_DMG) && DEF(EXACT_PIXEL_WATERFALL)
        dw $2866,$67bf,$10b5,$67bf ; Normal-speed CGB: background, pixels, text
    ELIF !DEF(EXACT_PIXEL_WATERFALL)
        dw $7fff,$0000,$0000,$0000
    ELSE
        dw $2866,$10b5,$265b,$67bf ; SGB darkest background, lightest text
    ENDC
ExactUITitle:
    IF DEF(EXACT_PIXEL_WATERFALL)
        db 7,3,4,25,0,0,0,0 ; LSDJ
    ELIF DEF(EXACT_WATERFALL)
        db 1,2,3,4,5
    ELSE
        db 1,2,3,4,5,0,6,7,8,9,10,11
    ENDC
ExactUITitleBottom:
    db 6,7,8,9,10,11
ExactUISongText:
    db 28,29,30,31,32,33,34,35
ExactUIBPMText:
    db 24,6,1,0,13,13,13,0 ; BPM 000
ExactUICPUText:
    IF DEF(EXACT_WATERFALL)
        db 5,6,2,0,13,13,13,23 ; CPU 000%
    ELSE
        db 1,2,3,4,5,0,5,6,2,0,0,13,13,13,23 ; MUSIC CPU  000%
    ENDC
ExactUIFreeText:
    IF DEF(EXACT_WATERFALL)
        db 12,11,10,10,14,13,13,23 ; FREE100%
    ELSE
        db 12,11,10,10,0,5,6,2,0,0,0,14,13,13,23 ; FREE CPU   100%
    ENDC

; Lookup avoids data-dependent decimal conversion/division.
ExactUIValues:
    FOR value,101
        db ExactUITile0+value/100
        db ExactUITile0+(value / 10) % 10
        db ExactUITile0+value % 10
        IF DEF(EXACT_WATERFALL)
            db value * ExactUIBarTiles / 100
        ELSE
            db value/5
        ENDC
    ENDR

MACRO ExactUIGlyph
    db \1,\1,\2,\2,\3,\3,\4,\4,\5,\5,\6,\6,\7,\7,\8,\8
ENDM
; Each keyboard row is one semitone. Three 16-row sprite patterns span
; four octaves and repeat vertically. Index1 is white; index3 is black.
; The rightmost pixel retains the divider on every row.
MACRO ExactUIPianoStripe
    FOR row,16
        IF (167 - \1 * 16 - row) % 12 == 1 || (167 - \1 * 16 - row) % 12 == 3 || (167 - \1 * 16 - row) % 12 == 6 || (167 - \1 * 16 - row) % 12 == 8 || (167 - \1 * 16 - row) % 12 == 10
            db $ff,$0f
        ELSE
            db $ff,$01
        ENDC
    ENDR
ENDM
ExactUIFont:
    ExactUIGlyph $00,$00,$00,$00,$00,$00,$00,$00 ; 0 blank
    ExactUIGlyph $00,$44,$6c,$54,$44,$44,$44,$00 ; 1 M
    ExactUIGlyph $00,$44,$44,$44,$44,$44,$38,$00 ; 2 U
    ExactUIGlyph $00,$3c,$40,$38,$04,$04,$78,$00 ; 3 S
    IF DEF(EXACT_PIXEL_WATERFALL)
        ExactUIGlyph $00,$78,$44,$44,$44,$44,$78,$00 ; 4 D
    ELSE
        ExactUIGlyph $00,$38,$10,$10,$10,$10,$38,$00 ; 4 I
    ENDC
    ExactUIGlyph $00,$3c,$40,$40,$40,$40,$3c,$00 ; 5 C
    ExactUIGlyph $00,$78,$44,$44,$78,$40,$40,$00 ; 6 P
    ExactUIGlyph $00,$40,$40,$40,$40,$40,$7c,$00 ; 7 L
    ExactUIGlyph $00,$38,$44,$44,$7c,$44,$44,$00 ; 8 A
    ExactUIGlyph $00,$44,$44,$28,$10,$10,$10,$00 ; 9 Y
    ExactUIGlyph $00,$7c,$40,$78,$40,$40,$7c,$00 ; 10 E
    ExactUIGlyph $00,$78,$44,$44,$78,$48,$44,$00 ; 11 R
    ExactUIGlyph $00,$7c,$40,$78,$40,$40,$40,$00 ; 12 F
    ExactUIGlyph $00,$38,$44,$4c,$54,$64,$38,$00 ; 13 0
    ExactUIGlyph $00,$10,$30,$10,$10,$10,$38,$00 ; 14 1
    ExactUIGlyph $00,$38,$44,$04,$18,$20,$7c,$00 ; 15 2
    ExactUIGlyph $00,$78,$04,$18,$04,$04,$78,$00 ; 16 3
    ExactUIGlyph $00,$08,$18,$28,$48,$7c,$08,$00 ; 17 4
    ExactUIGlyph $00,$7c,$40,$78,$04,$04,$78,$00 ; 18 5
    ExactUIGlyph $00,$38,$40,$78,$44,$44,$38,$00 ; 19 6
    ExactUIGlyph $00,$7c,$04,$08,$10,$20,$20,$00 ; 20 7
    ExactUIGlyph $00,$38,$44,$38,$44,$44,$38,$00 ; 21 8
    ExactUIGlyph $00,$38,$44,$44,$3c,$04,$38,$00 ; 22 9
    ExactUIGlyph $00,$64,$68,$10,$20,$2c,$4c,$00 ; 23 percent
    IF DEF(EXACT_PIXEL_WATERFALL)
        ExactUIGlyph $00,$78,$44,$78,$44,$44,$78,$00 ; 24 B
        ExactUIGlyph $00,$1c,$08,$08,$08,$48,$30,$00 ; 25 J
    ELSE
        ExactUIGlyph $ff,$ff,$ff,$ff,$ff,$ff,$ff,$ff ; 24 filled bar
        ExactUIGlyph $ff,$00,$00,$00,$00,$00,$00,$ff ; 25 empty bar
    ENDC
    IF DEF(EXACT_PIXEL_WATERFALL)
        ExactUIPianoStripe 0 ; tiles26/27
    ELIF DEF(EXACT_WATERFALL)
        db $ff,$00,$00,$00,$00,$00,$00,$00,$00,$00,$00,$00,$00,$00,$00,$00
    ENDC
IF DEF(EXACT_PIXEL_WATERFALL)
    IF DEF(EXACT_SONG_GLYPHS)
        INCBIN EXACT_SONG_GLYPHS
    ELSE
        ds 8 * 16,0
    ENDC
    ExactUIPianoStripe 1 ; tiles36/37
    REPT 16
        db $ff,$01 ; tiles38/39: white margin with vertical divider
    ENDR
ENDC
ExactUIFontEnd:
IF DEF(EXACT_PIXEL_WATERFALL)
    ASSERT ExactUIFontEnd-ExactUIFont == 40*16
ELIF DEF(EXACT_WATERFALL)
    ASSERT ExactUIFontEnd-ExactUIFont == 27*16
ELSE
    ASSERT ExactUIFontEnd-ExactUIFont == 26*16
ENDC
IF DEF(EXACT_PIXEL_WATERFALL)
    ASSERT ExactUIFontEnd <= $0990
ELSE
    ASSERT ExactUIFontEnd <= $0800
ENDC

IF DEF(EXACT_WATERFALL)
ExactUITimingData::
    dw ExactUIInitCycles & $ffff,ExactUIInitCycles >> 16
    dw ExactUILCDOnCycles & $ffff,ExactUILCDOnCycles >> 16
    dw ExactUIUpdateCycles
    ds (-@) & 15,0
ExactUIWaterfallBars:
    FOR value,101
        IF DEF(EXACT_PIXEL_WATERFALL)
            FOR column,9
                IF column >= (((64 * value + 99) / 100) + 7) / 8
                    db 0
                ELIF column == (((64 * value + 99) / 100) + 7) / 8 - 1
                    db (64 * value + 99) / 100
                ELSE
                    db 8 + column * 8
                ENDC
            ENDR
            ds 7,0
        ELSE
            FOR column,16
                IF column >= ExactUIBarTiles
                    db 0
                ELIF column < value * ExactUIBarTiles / 100
                    db ExactUITileFull
                ELSE
                    db ExactUITileEmpty
                ENDC
            ENDR
        ENDC
    ENDR
    ASSERT @ <= $1000
    INCLUDE "src/waterfall.asm"
ENDC
