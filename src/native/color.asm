; CGB-only waterfall colors. A background tile has three foreground colors:
; retain PU1, PU2, WAV, then NOI, in that order, when all four visit a tile.
; The seen-channel mask is monotonic until the hidden column is recycled,
; so an evicted noise trace cannot recolor the surviving tonal history.
DEF NativeColorChannel EQU $cc50
DEF NativeColorTile EQU $cc51
DEF NativeColorOldPalette EQU $cc52
DEF NativeColorPalette EQU $cc53
DEF NativeColorOldMask EQU $cc54
DEF NativeColorMask EQU $cc55
DEF NativeColorAddress EQU $cc56
DEF NativeColorBit EQU $cc58
DEF NativeColorCode EQU $cc59
DEF NativeColorSelectLow EQU $cc5a
DEF NativeColorSelectHigh EQU $cc5b
DEF NativeColorRows EQU $cc5c
DEF NativeColorMetadata EQU $9000 ; bank 1, outside unsigned BG tile data

; Called with the LCD off, after the extra CGB label glyphs are installed.
NativeColorInit:
    ld a,$88
    ldh [$ff68],a
    ld hl,NativeColorPalettes
    ld b,7 * 8
.palette
    ld a,[hl+]
    ldh [$ff69],a
    dec b
    jr nz,.palette
    ld a,1
    ldh [$ff4f],a
    ld hl,NativeColorMetadata
    ld b,216
    xor a
.metadata
    ld [hl+],a
    dec b
    jr nz,.metadata
    ld hl,$9800
    ld d,2
.map
    ld e,18
.row
    ld b,11
    ld a,1
.cell
    ld [hl+],a
    dec b
    jr nz,.cell
    ld a,l
    add 21
    ld l,a
    jr nc,.next_row
    inc h
.next_row
    dec e
    jr nz,.row
    ld hl,$9c00
    dec d
    jr nz,.map
    ; Glyphs use color 3. Keep their existing tile-bank selection.
    FOR channel,4
        ld hl,$9911 + channel * 64
        ld b,8
.label{d:channel}
        ld a,[hl]
        and 8
        IF channel == 0
            or 5
        ELIF channel == 1
            or 6
        ELIF channel == 2
            or 1
        ELSE
            or 7
        ENDC
        ld [hl+],a
        dec b
        jr nz,.label{d:channel}
    ENDR
    xor a
    ldh [$ff4f],a
    ret

; Start of a new eight-pixel column. The next hidden column is offscreen.
NativeColorBegin:
    push af
    push bc
    push de
    push hl
    ld a,[WaterfallColumnBase]
    ld l,a
    ld a,[WaterfallColumnBase + 1]
    ld h,a
    call NativeColorTileIndex
    ld l,a
    ld h,HIGH(NativeColorMetadata)
    ld b,18
    xor a
.clear
    call NativeColorStore
    inc hl
    dec b
    jr nz,.clear
    pop hl
    pop de
    pop bc
    pop af
    ret

; A=row. Called after the original map-number preparation for that row.
NativeColorMapRow:
    push af
    push bc
    push de
    push hl
    ld l,a
    ld h,0
    REPT 5
        add hl,hl
    ENDR
    ld a,[WaterfallMapDestHigh]
    add h
    ld h,a
    ld b,11
.cell
    call NativeRead
    sub 40 ; native canvas starts at unsigned tile 40 ($8280)
    push hl
    ld l,a
    ld h,HIGH(NativeColorMetadata)
    call NativeColorRead
    call NativeColorPaletteForMask
    pop hl
    call NativeColorStore
    inc hl
    dec b
    jr nz,.cell
    pop hl
    pop de
    pop bc
    pop af
    ret

; HL=bank-0 low-plane address, B=pixel bit; channel is NativeColorChannel.
; Every entry preserves BC/DE/HL. Rendering never leaves bank 1 selected
; across an enabled interrupt, and long remaps remain audio-interruptible.
NativeColorPixel:
    push bc
    push de
    push hl
    ld a,l
    ld [NativeColorAddress],a
    ld a,h
    ld [NativeColorAddress + 1],a
    ld a,b
    ld [NativeColorBit],a
    call NativeColorTileIndex
    ld [NativeColorTile],a
    ld l,a
    ld h,HIGH(NativeColorMetadata)
    call NativeColorRead
    ld [NativeColorOldMask],a
    call NativeColorPaletteForMask
    ld [NativeColorOldPalette],a
    ld a,[NativeColorChannel]
    ld e,a
    ld d,0
    ld hl,NativeColorBits
    add hl,de
    ld a,[NativeColorOldMask]
    or [hl]
    ld [NativeColorMask],a
    ld b,a
    ld a,[NativeColorOldMask]
    cp b
    jr nz,.mask_changed
    ; Most pixels continue a channel already present in this tile. Its
    ; palette and historical metadata then need no further work.
    ld a,[NativeColorOldPalette]
    ld [NativeColorPalette],a
    jr .palette_ready
.mask_changed
    ld a,b
    call NativeColorPaletteForMask
    ld [NativeColorPalette],a
    ld b,a
    ld a,[NativeColorOldPalette]
    cp b
    jr z,.metadata
    ; There are no old pixels to translate when the first pen visits.
    ld a,[NativeColorOldMask]
    or a
    call nz,NativeColorRemap
.metadata
    ld a,[NativeColorTile]
    ld l,a
    ld h,HIGH(NativeColorMetadata)
    ld a,[NativeColorMask]
    call NativeColorStore
    ld a,[NativeColorOldPalette]
    ld b,a
    ld a,[NativeColorPalette]
    cp b
    call nz,NativeColorAttributes
.palette_ready
    ld a,[NativeColorPalette]
    dec a
    add a,a
    add a,a
    ld e,a
    ld a,[NativeColorChannel]
    add e
    ld e,a
    ld d,0
    ld hl,NativeColorCodes
    add hl,de
    ld a,[hl]
    or a
    jr z,.done ; the fourth channel must not erase a surviving note
    ld [NativeColorCode],a
    ld a,[NativeColorAddress]
    ld l,a
    ld a,[NativeColorAddress + 1]
    ld h,a
    ld a,[NativeColorBit]
    ld b,a
    call NativeColorReadPair
    ld a,d
    and b
    ld c,0
    jr z,.low_ready
    inc c
.low_ready
    ld a,e
    and b
    jr z,.high_ready
    set 1,c
.high_ready
    ld a,c
    or a
    jr z,.write
    ; Every palette assigns increasing codes in channel-priority order.
    ld a,[NativeColorCode]
    cp c
    jr nc,.done ; existing same/higher-priority channel wins
.write
    ld a,[NativeColorCode]
    ld c,a
    ld a,b
    cpl
    and e
    bit 1,c
    jr z,.high
    or b
.high
    ld e,a
    ld a,b
    cpl
    and d
    bit 0,c
    jr z,.low
    or b
.low
    ld d,a
    call NativeColorStorePair
.done
    pop hl
    pop de
    pop bc
    ret

; Convert a native canvas address into physical tile index 0..215.
NativeColorTileIndex:
    ld a,l
    swap a
    and $0f
    ld e,a
    ld a,h
    sub $82
    swap a
    and $f0
    or e
    sub 8
    ret

NativeColorPaletteForMask:
    ld e,a
    ld d,0
    ld hl,NativeColorMaskPalettes
    add hl,de
    ld a,[hl]
    ret

; Translate all eight pixel rows before publishing the new tile palette.
; Each selector has one bit per old nonzero color, avoiding pixel loops.
NativeColorRemap:
    ld a,[NativeColorOldPalette]
    dec a
    add a,a
    add a,a
    ld e,a
    ld a,[NativeColorPalette]
    dec a
    add e
    add a,a
    ld e,a
    ld d,0
    ld hl,NativeColorRemapSelectors
    add hl,de
    ld a,[hl+]
    ld [NativeColorSelectLow],a
    ld a,[hl]
    ld [NativeColorSelectHigh],a
    ld a,[NativeColorAddress]
    and $f0
    ld l,a
    ld a,[NativeColorAddress + 1]
    ld h,a
    ld a,8
    ld [NativeColorRows],a
.row
    call NativeColorReadPair
    ld a,e
    and d
    ld c,a ; old color 3 pixels
    ld a,e
    cpl
    and d
    ld d,a ; old color 1 pixels
    ld a,c
    cpl
    and e
    ld e,a ; old color 2 pixels
    ld a,[NativeColorSelectLow]
    call .select
    push af
    ld a,[NativeColorSelectHigh]
    call .select
    ld e,a
    pop af
    ld d,a
    call NativeColorStorePair
    inc hl
    inc hl
    ld a,[NativeColorRows]
    dec a
    ld [NativeColorRows],a
    jr nz,.row
    ret
.select
    ld b,a
    xor a
    bit 0,b
    jr z,.not_one
    or d
.not_one
    bit 1,b
    jr z,.not_two
    or e
.not_two
    bit 2,b
    ret z
    or c
    ret

; The plotted physical column is always active-map column 10 and future
; inactive-map column 9. This also corrects a row already prepared earlier.
NativeColorAttributes:
    ld a,[NativeColorTile]
.row
    cp 18
    jr c,.found
    sub 18
    jr .row
.found
    ld l,a
    ld h,0
    REPT 5
        add hl,hl
    ENDR
    ld a,l
    add 10
    ld l,a
    ld a,[WaterfallLCD]
    and $40
    swap a
    add $98
    add h
    ld h,a
    ld a,[NativeColorPalette]
    call NativeColorStore
    ld a,h
    xor 4
    ld h,a
    dec l
    ld a,[NativeColorPalette]
    jp NativeColorStore

; The bank switch and byte access are the only interrupt-masked work.
; Busy waits always restore interrupt availability before trying again.
NativeColorRead:
.wait
    di
    ldh a,[$ff41]
    and 2
    jr nz,.busy
    ld a,1
    ldh [$ff4f],a
    ld a,[hl]
    push af
    ld a,0
    ldh [$ff4f],a
    pop af
    ei
    ret
.busy
    ei
    jr .wait

NativeColorStore:
    push af
.wait
    di
    ldh a,[$ff41]
    and 2
    jr nz,.busy
    ld a,1
    ldh [$ff4f],a
    pop af
    ld [hl],a
    push af
    ld a,0
    ldh [$ff4f],a
    pop af
    ei
    ret
.busy
    ei
    jr .wait

NativeColorBits:
    db 1,2,4,8
NativeColorMaskPalettes:
    db 1,1,1,1,1,1,1,1,2,2,2,2,3,3,4,1
NativeColorCodes:
    db 1,2,3,0, 1,2,0,3, 1,0,2,3, 0,1,2,3
NativeColorRemapSelectors:
    ; Two three-bit selectors per old/new palette pair, low then high.
    db 5,6, 1,2, 1,4, 2,4
    db 1,2, 5,6, 5,4, 6,4
    db 3,2, 5,4, 5,6, 4,6
    db 2,3, 4,5, 4,6, 5,6
NativeColorPalettes:
    dw $2866,$7f68,$559f,$23d6 ; PU1, PU2, WAV
    dw $2866,$7f68,$559f,$1f3f ; PU1, PU2, NOI
    dw $2866,$7f68,$23d6,$1f3f ; PU1, WAV, NOI
    dw $2866,$559f,$23d6,$1f3f ; PU2, WAV, NOI
    dw $2866,$7f68,$7f68,$7f68 ; PU1 label
    dw $2866,$559f,$559f,$559f ; PU2 label
    dw $2866,$1f3f,$1f3f,$1f3f ; NOI label

; CGB runs at double speed. Read or write both adjacent bank-0 bitplanes
; after one availability check; the final VRAM access is within 52 CPU
; cycles of the STAT read, below the 160-cycle mode-2 safety window. Only
; this short access is masked; waiting and all pixel arithmetic allow IRQs.
; HL=low plane. Read returns D=low, E=high; both preserve BC and HL.
NativeColorReadPair:
.wait
    di
    ldh a,[$ff41]
    and 2
    jr nz,.busy
    ld a,[hl+]
    ld d,a
    ld a,[hl]
    ld e,a
    dec hl
    ei
    ret
.busy
    ei
    jr .wait

; HL=low plane, D=low byte, E=high byte. Preserves BC, DE and HL.
NativeColorStorePair:
.wait
    di
    ldh a,[$ff41]
    and 2
    jr nz,.busy
    ld a,d
    ld [hl+],a
    ld a,e
    ld [hl],a
    dec hl
    ei
    ret
.busy
    ei
    jr .wait
