; Minimal Neo Geo CD ADPCM-A streamer.
; Two 512 KiB channels alternate across the 1 MiB PCM RAM.  The 68k reads
; the monotonically increasing reply byte to know which half became inactive.

    cpu z80

    org 0000h
    jp Start

    org 0038h
    ei
    reti

    org 0066h
    jp NMIHandler

    org 0100h

Start:
    ld sp,0F7FFh
    xor a
    ld (Requested),a
    ld (Playing),a
    ld (CurrentHalf),a
    ld (ReplyCounter),a
    out (0Ch),a
    out (08h),a                 ; enable NMIs

    ; Clear and unmask all ADPCM-A end flags.
    ld d,1Ch
    ld e,3Fh
    call WriteA
    ld e,00h
    call WriteA

    ; Loud master level and centered, loud channels 1 and 2.
    ld d,01h
    ld e,3Fh
    call WriteB
    ld d,08h
    ld e,0DFh
    call WriteB
    ld d,09h
    ld e,0DFh
    call WriteB

    ; Channel 1: $00000-$7FFFF.
    ld d,10h
    ld e,00h
    call WriteB
    ld d,18h
    ld e,00h
    call WriteB
    ld d,20h
    ld e,0FFh
    call WriteB
    ld d,28h
    ld e,07h
    call WriteB

    ; Channel 2: $80000-$FFFFF.
    ld d,11h
    ld e,00h
    call WriteB
    ld d,19h
    ld e,08h
    call WriteB
    ld d,21h
    ld e,0FFh
    call WriteB
    ld d,29h
    ld e,0Fh
    call WriteB
    ei

.loop:
    ld a,(Requested)
    or a
    jr z,.poll
    cp 02h
    jr z,.stop
    xor a
    ld (Requested),a
    ld (CurrentHalf),a
    ld a,01h
    ld (Playing),a
    ld d,00h
    ld e,01h
    call WriteB
    jr .poll

.stop:
    xor a
    ld (Requested),a
    ld (Playing),a
    ld d,00h
    ld e,0BFh
    call WriteB

.poll:
    ld a,(Playing)
    or a
    jr z,.loop
    in a,(06h)
    ld b,a
    ld a,(CurrentHalf)
    or a
    jr nz,.second
    bit 0,b
    jr z,.loop
    ld d,1Ch
    ld e,01h
    call WriteA
    ld e,00h
    call WriteA
    ld d,00h
    ld e,02h
    call WriteB
    ld a,01h
    ld (CurrentHalf),a
    jr .announce

.second:
    bit 1,b
    jr z,.loop
    ld d,1Ch
    ld e,02h
    call WriteA
    ld e,00h
    call WriteA
    ld d,00h
    ld e,01h
    call WriteB
    xor a
    ld (CurrentHalf),a

.announce:
    ld a,(ReplyCounter)
    inc a
    ld (ReplyCounter),a
    out (0Ch),a
    jr .loop

NMIHandler:
    push af
    in a,(00h)
    cp 01h
    jr nz,.request_stop
    ld a,01h
    ld (Requested),a
    jr .ack
.request_stop:
    ld a,02h
    ld (Requested),a
.ack:
    xor a
    out (00h),a
    pop af
    retn

; D=register, E=data. Delays exceed the YM2610's documented minima.
WriteA:
    di
    ld a,d
    out (04h),a
    nop
    nop
    nop
    nop
    nop
    nop
    ld a,e
    out (05h),a
    call YmDataDelay
    ei
    ret

WriteB:
    di
    ld a,d
    out (06h),a
    nop
    nop
    nop
    nop
    nop
    nop
    ld a,e
    out (07h),a
    call YmDataDelay
    ei
    ret

YmDataDelay:
    push bc
    ld b,12
.wait:
    djnz .wait
    pop bc
    ret

Requested:      db 0
Playing:        db 0
CurrentHalf:    db 0
ReplyCounter:   db 0
