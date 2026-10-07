; Native song interpreter host. The locally recovered engine executes song
; data through its original shared sequencer; no performance trace is stored.
DEF NativeHardware EQU $cfff
DEF NativeLastFrame EQU $cffe
DEF NativePrepareIndex EQU $cc00
DEF NativeFrameReady EQU $cc01

SECTION "Native interpreter startup", ROM0[$0b54]
NativeStart::
    di
    ld sp,$fffe
    push af
    cp $11
    jr nz,.early_dmg
    NativeEarlyCGB
    jr .early_ready
.early_dmg
    NativeEarlyDMG
.early_ready
    xor a
    ldh [$ffff],a
    ldh [$ff07],a
    ld a,4
    ld [$2000],a
    ld hl,$4000
    ld de,$c000
    ld bc,$2000
    call NativeCopy
    pop af
    push af
    cp $11
    jr nz,.ram_ready
    ld hl,$6000
    ld de,$c000
    ld bc,$2000
    call NativeCopy
.ram_ready
    pop af
    ld [NativeHardware],a
    ld a,5
    ld [$2000],a
    ld hl,$4000
    ld a,[NativeHardware]
    cp $11
    jr nz,.hram_ready
    ld hl,$407f
.hram_ready
    ld de,$ff80
    ld bc,124 ; preserve the bootstrap's HRAM stack
    call NativeCopy
    ld sp,$dfff

    ; One32-KiB native song, copied to four MBC5 SRAM banks at startup.
    ld a,$0a
    ld [$0000],a
    ld a,1
    ld [$2000],a
    xor a
    ld [$4000],a
    ld hl,$4000
    ld de,$a000
    ld bc,$2000
    call NativeCopy
    ld a,1
    ld [$4000],a
    ld hl,$6000
    ld de,$a000
    ld bc,$2000
    call NativeCopy
    ld a,3
    ld [$2000],a
    ld a,2
    ld [$4000],a
    ld hl,$4000
    ld de,$a000
    ld bc,$2000
    call NativeCopy
    ld a,3
    ld [$4000],a
    ld hl,$6000
    ld de,$a000
    ld bc,$2000
    call NativeCopy
    xor a
    ld [$0000],a

    ld a,5
    ld [$2000],a
    ldh [$ff8e],a
    call ExactUIInit
    call WaterfallBegin
    NativeSongDelay
    ; The CGB speed-switch bootstrap enables interrupts. Re-establish the
    ; startup critical section before enabling any IRQ source on either model.
    di
    xor a
    ld [NativePrepareIndex],a
    ld [NativeFrameReady],a
    ld [$cba2],a ; tracker font animation is not part of the player screen
    ld [$cbd1],a ; prevent tracker DMA before song initialization as well
    ld [$c903],a ; tracker viewport scrolling is not part of the player
    ld a,2
    ld [$2000],a
    ldh [$ff8e],a
    ; Generated per-model timer values preserve the source startup state.
    ; Pending non-VBlank requests belong to music initialization; an old
    ; display request must not reset TIMA before the sequencer starts.
    NativeTimerInit
    ld a,1
    ldh [$ffff],a
    ei
    call $7b4b ; original shared song/instrument initialization
    xor a
    ld [$cbd1],a ; retain the standalone OAM instead of tracker sprites
    ld [$c903],a
    ld a,5
    ldh [$ffff],a
    xor a
    ld [$c56c],a
.loop
    call $158c ; original HALT/NOP/RET idle primitive
    ld a,[$c56c]
    or a
    jr z,.loop
    di
    xor a
    ld [$c56c],a ; consume the tracker's watchdog frame counter
    ld a,5
    ld [$2000],a
    ldh [$ff8e],a
    ei
    call NativeDisplayScroll
    call NativeDisplayFrame
    di
    ld a,2
    ld [$2000],a
    ldh [$ff8e],a
    ei
    jr .loop

NativeCopy:
    ld a,b
    or c
    ret z
    ld a,c
    or a
    jr nz,.byte
    ; Full SRAM/WRAM blocks use a page copy. Unrolling sixteen bytes keeps
    ; the bootstrap short enough to reproduce early song-start snapshots.
.page
    ld c,16
.chunk
    REPT 16
        ld a,[hl+]
        ld [de],a
        inc de
    ENDR
    dec c
    jr nz,.chunk
    dec b
    jr nz,.page
    xor a
    ret
.byte
    ld a,[hl+]
    ld [de],a
    inc de
    dec bc
    ld a,b
    or c
    jr nz,.byte
    ret

; The original VBlank handler owns audio scheduling without any display hook.
; Export its entry for capture breakpoints and state inspection.
DEF NativeVBlank EQU $183a
EXPORT NativeVBlank
NativeBootstrapEnd:
ASSERT NativeBootstrapEnd <= $1306
