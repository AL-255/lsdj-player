; Hardware-scrolled BG pitch history at x=0..79; fixed window UI at x=80.
; Twelve physical pattern columns are recycled independently of the 32 BG
; map columns. Only the next offscreen map column changes at each tile step.
DEF WaterfallColumns EQU 11
DEF WaterfallRingColumns EQU 12
DEF WaterfallPatternBase EQU $8280
DEF WaterfallClearCount EQU 12
DEF WaterfallMapCount EQU 18
DEF NativeMapHead EQU $cc48
DEF NativeMapColumn EQU $cc49
DEF NativeDrawMapColumn EQU $cc4a
DEF NativeScrollX EQU $cc4b

SECTION "Pitch waterfall input", WRAM0[$cc20]
WaterfallPitchY:: ds 4
WaterfallActive:: ds 1
WaterfallCPU:: ds 1
WaterfallHead:: ds 1
WaterfallLCD:: ds 1
SECTION "Pitch waterfall stage state", WRAM0[$cc40]
WaterfallColumnBase:: ds 2
WaterfallMapSource:: ds 2 ; reserved former map-table pointer
WaterfallMapDestHigh:: ds 1 ; reserved former double-map state
WaterfallNextHead:: ds 1
WaterfallNextLCD:: ds 1
WaterfallPixelPhase:: ds 1
    ds 4 ; NativeMapHead through NativeScrollX

SECTION "Native hardware scroll", ROMX, BANK[5]
WaterfallInit::
    ; The shared font initializer wrote its UI into BG columns16..31.
    ; Move that stationary panel once into the window before reusing BG.
    ld hl,$9810
    ld de,$9c00
    ld b,18
.panel_row
    ld c,16
.panel_cell
    ld a,[hl+]
    ld [de],a
    inc de
    dec c
    jr nz,.panel_cell
    ld a,l
    add 16
    ld l,a
    jr nc,.source_ready
    inc h
.source_ready
    ld a,e
    add 16
    ld e,a
    jr nc,.dest_ready
    inc d
.dest_ready
    dec b
    jr nz,.panel_row
    ld hl,$9800
    ld bc,$400
    call .clear
    ld hl,WaterfallPatternBase
    ld bc,WaterfallRingColumns * 288
    call .clear
    ld hl,$fe00
    ld bc,160
    call .clear
    xor a
    ld [WaterfallHead],a
    ld [WaterfallPixelPhase],a
    ld [NativeMapHead],a
    ld [NativeScrollX],a
    ldh [$ff42],a
    ldh [$ff43],a
    ldh [$ff4a],a
    ld a,87
    ldh [$ff4b],a
    ld a,$f3 ; BG map0, window map1, unsigned tiles, 8x8 objects
    ld [WaterfallLCD],a
    ld [WaterfallNextLCD],a
    ; Initialize enough columns for the viewport, pen, and hidden recycle.
    FOR row,18
        ld hl,$9800 + row * 32
        FOR column,WaterfallRingColumns
            ld a,40 + column * 18 + row
            ld [hl+],a
        ENDR
    ENDR
    ld a,$03
    ldh [$ff47],a
    ld a,$0c
    ldh [$ff49],a
    xor a
    ldh [$ff48],a
    ld a,$88
    ldh [$ff6a],a
    ld hl,NativeKeyboardPalette
    ld b,8
.palette
    ld a,[hl+]
    ldh [$ff6b],a
    dec b
    jr nz,.palette
    ret
.clear
    ; All startup blocks are multiples of16. Keep initialization short
    ; enough to align even songs whose tracker START snapshot occurs early.
    xor a
    REPT 16
        ld [hl+],a
    ENDR
    ld a,c
    sub 16
    ld c,a
    jr nc,.count_ready
    dec b
.count_ready
    ld a,b
    or c
    jr nz,.clear
    ret

; Called after status glyph initialization, LCD off and bank0 selected.
; Obsolete H glyph37 provides the third8x8 keyboard tile. No extra canvas
; tiles are needed, even for the doubled lower-range keyboard.
NativeKeyboardInit:
    ld hl,$81a0
    ld de,NativeKeyboardPixels
    ld b,32
    call .copy
    ld hl,$8250
    ld b,16
    call .copy
    FOR stripe,18
        ld hl,$fe00 + stripe * 4
        ld a,16 + stripe * 8
        ld [hl+],a
        ld a,80 ; screen x72, last eight pixels of the plot
        ld [hl+],a
        IF stripe % 3 == 2
            ld a,37
        ELSE
            ld a,26 + stripe % 3
        ENDC
        ld [hl+],a
        ld a,$31 ; horizontal flip, OBJ palette1 on either hardware
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
NativeKeyboardPixels:
    FOR row,24
        IF DEF(NATIVE_LOW_RANGE)
            DEF note = 95 - row / 2
        ELSE
            DEF note = 167 - row
        ENDC
        IF note % 12 == 1 || note % 12 == 3 || note % 12 == 6 || note % 12 == 8 || note % 12 == 10
            db $ff,$0f
        ELSE
            db $ff,$01
        ENDC
        PURGE note
    ENDR
NativeKeyboardPalette:
    dw $2866,$2866,$10b5,$67bf

WaterfallBegin::
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
    jr c,.head_ready
    xor a
.head_ready
    ld [WaterfallNextHead],a
    ld a,[NativeMapHead]
    add WaterfallColumns - 1
    and 31
    ld [NativeDrawMapColumn],a
    inc a
    and 31
    ld [NativeMapColumn],a
    ret

; One hidden-column clear slice. IRQs remain enabled between VRAM bytes.
; A=0..11, each stage clears24bytes; independent of viewport width.
NativeClearSliceDMG:
    ld l,a
    ld h,0
    add hl,hl
    add hl,hl
    add hl,hl
    ld d,h
    ld e,l
    add hl,hl
    add hl,de ; stage*24
    ld a,[WaterfallColumnBase]
    ld e,a
    ld a,[WaterfallColumnBase+1]
    ld d,a
    add hl,de
    xor a
    REPT 24
        call NativeStoreIncrement
    ENDR
    ret

; A=row0..17. Replace only the entering offscreen tile-map cell.
NativePrepareRowDMG:
    ld b,a
    ld a,[WaterfallColumnBase]
    swap a
    and $0f
    ld e,a
    ld a,[WaterfallColumnBase+1]
    sub $80
    swap a
    or e
    add b
    push af
    ld l,b
    ld h,0
    REPT 5
        add hl,hl
    ENDR
    ld a,[NativeMapColumn]
    or l
    ld l,a
    ld a,h
    add $98
    ld h,a
    pop af
    jp NativeStore

WaterfallPixelMasks:
    db $80,$40,$20,$10,$08,$04,$02,$01
WaterfallPixelPointers:
    FOR head,WaterfallRingColumns
        dw WaterfallPatternBase + ((head + WaterfallColumns - 1) % WaterfallRingColumns) * 288
    ENDR
WaterfallColumnPointers:
    FOR head,WaterfallRingColumns
        dw WaterfallPatternBase + ((head + WaterfallColumns) % WaterfallRingColumns) * 288
    ENDR
