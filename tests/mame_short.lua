local machine = manager.machine
local cpu = machine.devices[":maincpu"]
local space = cpu.spaces["program"]
local host_frame = 0
local started = false
local play_start_host = nil

emu.register_frame_done(function()
    host_frame = host_frame + 1

    local fault = space:read_u32(0x100040)
    if space:read_u32(0x100024) == 0x1A0000 and
       space:read_u32(0x100028) == 0x1A8C00 then
        started = true
    end

    if started and fault ~= 0 then
        print(string.format(
            "FMV_SMOKE_FAIL fault=%08X return_pc=%08X access=%08X pc=%06X frame=%d",
            fault, space:read_u32(0x100044), space:read_u32(0x100048),
            cpu.state["PC"].value, space:read_u16(0x10000A)))
        machine:exit()
        return
    end

    local playing = space:read_u8(0x100000)
    local current = space:read_u16(0x10000A)
    if playing == 1 and play_start_host == nil then
        play_start_host = host_frame
    end
    if started and space:read_u8(0x100004) ~= 0 then
        print(string.format(
            "FMV_SMOKE_FAIL ring_overflow frame=%d next=%d pc=%06X buffered=%d received=%d busy=%d paused=%d",
            current, space:read_u16(0x10000C), cpu.state["PC"].value,
            space:read_u16(0x100012), space:read_u32(0x100020),
            space:read_u8(0x10003C), space:read_u8(0x100008)))
        machine:exit()
        return
    end

    if playing == 1 and current >= 1800 then
        local palette = space:read_u16(0x400002)
        local sprite = space:read_u32(0xE00080)
        local visible_map = space:read_u32(0x10002C)
        local tile = space:read_u16(visible_map + 100 * 2)
        if palette == 0 or sprite == 0 or tile == 0 then
            print(string.format(
                "FMV_SMOKE_FAIL palette=%04X sprite=%08X tile=%04X",
                palette, sprite, tile))
        else
            print(string.format(
                "FMV_SMOKE_OK frame=%d playback_fields=%d late_fields=%d sectors=%d buffered=%d paused=%d audio_reply=%d audio_write=%d audio_upload=%d palette=%04X sprite=%08X tile=%04X",
                current, host_frame - play_start_host, space:read_u32(0x10004C),
                space:read_u32(0x100020), space:read_u16(0x100012),
                space:read_u8(0x100008), space:read_u8(0x100005),
                space:read_u32(0x100034), space:read_u32(0x100038),
                palette, sprite, tile))
            machine.video:snapshot()
        end
        machine:exit()
        return
    end

    if host_frame >= 14000 then
        print(string.format(
            "FMV_SMOKE_FAIL timeout pc=%06X playing=%02X current=%04X next=%04X",
            cpu.state["PC"].value, playing, current,
            space:read_u16(0x10000C)))
        machine:exit()
    end
end, "Neo Geo CD native-tile FMV smoke test")
