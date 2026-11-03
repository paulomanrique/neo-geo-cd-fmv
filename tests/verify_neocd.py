#!/usr/bin/env python3
"""Run the installed NeoCD libretro core headlessly and record player diagnostics."""

import argparse
import ctypes as C
import json
import shutil
import struct
import sys
import wave
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))


class Variable(C.Structure):
    _fields_ = [("key", C.c_char_p), ("value", C.c_char_p)]


class Game(C.Structure):
    _fields_ = [("path", C.c_char_p), ("data", C.c_void_p),
                ("size", C.c_size_t), ("meta", C.c_char_p)]


class Geometry(C.Structure):
    _fields_ = [("base_width", C.c_uint), ("base_height", C.c_uint),
                ("max_width", C.c_uint), ("max_height", C.c_uint), ("aspect", C.c_float)]


class Timing(C.Structure):
    _fields_ = [("fps", C.c_double), ("sample_rate", C.c_double)]


class AVInfo(C.Structure):
    _fields_ = [("geometry", Geometry), ("timing", Timing)]


class NeoCDMemory(C.Structure):
    """Read-only b1e04c7 Memory prefix; verified against libretro RAM pointers."""
    _fields_ = [("zoom", C.c_void_p), ("backup", C.c_void_p),
                ("vectors", C.c_bool), ("dma_config", C.c_uint16 * 9),
                ("dma_source", C.c_uint32), ("dma_destination", C.c_uint32),
                ("dma_length", C.c_uint32), ("dma_pattern", C.c_uint16),
                ("sprite_bank", C.c_uint32), ("pcm_bank", C.c_uint32),
                ("bus_request", C.c_uint32), ("area", C.c_uint32),
                ("ram", C.c_void_p), ("rom", C.c_void_p),
                ("sprites", C.c_void_p), ("fix", C.c_void_p),
                ("pcm", C.c_void_p), ("video", C.c_void_p)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cue", type=Path)
    parser.add_argument("--core", type=Path, required=True)
    parser.add_argument("--system-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stream", type=Path, required=True)
    parser.add_argument("--max-mean-error", type=float, default=0.0)
    parser.add_argument("--starve-frame", type=int)
    parser.add_argument("--starve-fields", type=int, default=12)
    parser.add_argument("--fields", type=int, default=7000)
    parser.add_argument("--bios", default="front-sp1.bin (Front Loader)")
    parser.add_argument("--capture-every", type=int, default=50)
    parser.add_argument("--audio-output", type=Path)
    parser.add_argument("--check-pcm", action="store_true",
                        help="validate PCM banks using the installed b1e04c7 core's internal memory layout")
    args = parser.parse_args()
    if (args.fields <= 0 or args.starve_fields <= 0 or args.max_mean_error < 0
            or args.capture_every < 0):
        parser.error("fields and starvation duration must be positive; tolerance and capture interval must be nonnegative")
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    bios = out / "system" / "neocd"
    bios.mkdir(parents=True, exist_ok=True)
    for name in ("front-sp1.bin", "top-sp1.bin", "neocd.bin", "000-lo.lo"):
        source = args.system_dir / "neocd" / name
        if source.exists():
            shutil.copyfile(source, bios / name)
    system = str(bios.parent).encode()
    save = str(out).encode()
    core = C.CDLL(str(args.core.resolve()))
    options = {"neocd_loadskip": b"Off", "neocd_overscan_h": b"0",
               "neocd_region": b"Europe", "neocd_cpu_overclock": b"100%",
               "neocd_cdspeedhack": b"Off", "neocd_bios": args.bios.encode(),
               "neocd_per_content_saves": b"Off"}
    pixel_format = 2
    latest = None
    audio_frames = 0
    audio_writer = None
    host = 0
    playing_seen = False
    defaults = {}
    ENV = C.CFUNCTYPE(C.c_bool, C.c_uint, C.c_void_p)
    VIDEO = C.CFUNCTYPE(None, C.c_void_p, C.c_uint, C.c_uint, C.c_size_t)
    AUDIO = C.CFUNCTYPE(C.c_size_t, C.c_void_p, C.c_size_t)
    POLL = C.CFUNCTYPE(None)
    INPUT = C.CFUNCTYPE(C.c_int16, C.c_uint, C.c_uint, C.c_uint, C.c_uint)

    @ENV
    def environment(command, data):
        nonlocal pixel_format
        if command in (9, 31):
            C.cast(data, C.POINTER(C.c_char_p))[0] = system if command == 9 else save
            return True
        if command == 10:
            pixel_format = C.cast(data, C.POINTER(C.c_uint))[0]
            return pixel_format in (0, 1, 2)
        if command == 15:
            var = C.cast(data, C.POINTER(Variable)).contents
            key = var.key.decode()
            value = options.get(key, defaults.get(key))
            var.value = value
            return value is not None
        if command == 16:
            variables = C.cast(data, C.POINTER(Variable))
            index = 0
            while variables[index].key:
                var = variables[index]
                defaults[var.key.decode()] = var.value.split(b";", 1)[-1].strip().split(b"|")[0]
                index += 1
            return True
        if command == 17:
            C.cast(data, C.POINTER(C.c_bool))[0] = False
            return True
        if command in (39, 52):
            C.cast(data, C.POINTER(C.c_uint))[0] = 0
            return True
        return command in (11, 18, 35, 37, 42)

    @VIDEO
    def video(data, width, height, pitch):
        nonlocal latest
        if data:
            latest = (C.string_at(data, pitch * height), width, height, pitch, pixel_format)

    @AUDIO
    def audio(data, count):
        nonlocal audio_frames
        audio_frames += count
        if audio_writer is not None:
            audio_writer.writeframesraw(C.string_at(data, count * 4))
        return count

    @POLL
    def poll():
        pass

    @INPUT
    def input_state(port, device, index, button):
        return int(port == 0 and device == 1 and button == 3
                   and not playing_seen and 20 <= host % 120 < 24)

    callbacks = [environment, video, audio, poll, input_state]
    for name, callback in zip(("environment", "video_refresh", "audio_sample_batch",
                               "input_poll", "input_state"), callbacks):
        method = getattr(core, "retro_set_" + name)
        method.argtypes = [type(callback)]
        method(callback)
    core.retro_get_memory_data.argtypes = [C.c_uint]
    core.retro_get_memory_data.restype = C.c_void_p
    core.retro_get_memory_size.argtypes = [C.c_uint]
    core.retro_get_memory_size.restype = C.c_size_t
    core.retro_load_game.argtypes = [C.POINTER(Game)]
    core.retro_load_game.restype = C.c_bool
    if hasattr(core, "m68k_get_reg"):
        core.m68k_get_reg.argtypes = [C.c_void_p, C.c_int]
        core.m68k_get_reg.restype = C.c_uint
    core.retro_init()
    game = Game(str(args.cue.resolve()).encode(), None, 0, None)
    if not core.retro_load_game(C.byref(game)):
        raise RuntimeError("NeoCD could not load the disc")
    av = AVInfo()
    core.retro_get_system_av_info.argtypes = [C.POINTER(AVInfo)]
    core.retro_get_system_av_info(C.byref(av))
    if args.audio_output:
        args.audio_output.parent.mkdir(parents=True, exist_ok=True)
        audio_writer = wave.open(str(args.audio_output), "wb")
        audio_writer.setnchannels(2)
        audio_writer.setsampwidth(2)
        audio_writer.setframerate(round(av.timing.sample_rate))
    ram = core.retro_get_memory_data(2)
    if not ram or core.retro_get_memory_size(2) < 0x200000:
        raise RuntimeError("NeoCD did not expose program RAM")
    pcm_pointer = None
    pcm_checks = []
    checked_pcm_replies = set()
    expected_pcm = b""
    if args.check_pcm:
        machine = C.c_void_p.in_dll(core, "neocd").value
        memory = NeoCDMemory.from_address(machine)
        if memory.ram != ram or memory.video != core.retro_get_memory_data(3):
            raise RuntimeError("PCM check requires the supported NeoCD Memory layout")
        pcm_pointer = memory.pcm
        expected_pcm = bytearray((args.stream.parent / "AUDIO0.PCM").read_bytes())
        with args.stream.open("rb") as source:
            while block := source.read(10240):
                count = struct.unpack_from(">H", block, 8198)[0]
                expected_pcm.extend(block[8204:8204 + count])
        expected_pcm = bytes(expected_pcm)

    def word(offset):
        return int.from_bytes(C.string_at(ram + offset, 2), "big")

    def long(offset):
        return (word(offset) << 16) | word(offset + 2)

    def byte(offset):
        return C.c_uint8.from_address(ram + offset).value

    def captured_rgb():
        if latest is None:
            return None
        raw, width, height, pitch, fmt = latest
        if fmt == 1:
            words = np.frombuffer(raw, dtype="<u4").reshape(height, pitch // 4)[:, :width]
            rgb = np.stack(((words >> 16) & 255, (words >> 8) & 255, words & 255), axis=-1)
        else:
            words = np.frombuffer(raw, dtype="<u2").reshape(height, pitch // 2)[:, :width]
            if fmt == 2:
                channels = ((words >> 11) & 31, (words >> 5) & 63, words & 31)
                maximum = (31, 63, 31)
            else:
                channels = ((words >> 10) & 31, (words >> 5) & 31, words & 31)
                maximum = (31, 31, 31)
            rgb = np.stack([np.rint(c.astype(float) * 255 / m) for c, m in zip(channels, maximum)], axis=-1)
        return rgb.astype(np.uint8)

    def capture(name):
        rgb = captured_rgb()
        if rgb is not None:
            Image.fromarray(rgb).save(out / name)

    reference_state = {}
    reference_frame = -1
    reference_rgb = None
    expected_frames = None
    oracle = None
    if args.stream:
        from export_native_video import frames

        def inspect(number, tile_map, tiles, valid):
            reference_state.update(number=number, tile_map=tile_map, tiles=tiles, valid=valid)

        oracle = iter(frames(args.stream, inspect=inspect, palette_model="neocd"))
        expected_frames = args.stream.stat().st_size // 10240
    errors = []
    sequence_errors = []
    checks = []
    starvation_start = None
    hidden_sectors = 0

    events = []
    last = -1
    last_change = 0
    age = 0
    outcome = "timeout"
    for host in range(args.fields):
        if (args.starve_frame is not None and playing_seen and starvation_start is None
                and word(0x10000A) >= args.starve_frame):
            starvation_start = host
        if starvation_start is not None and host < starvation_start + args.starve_fields:
            hidden_sectors += word(0x100012)
            if hidden_sectors >= 180:
                raise RuntimeError("starvation injection would overflow the real sector ring")
            C.memmove(ram + 0x100012, b"\0\0", 2)
        elif hidden_sectors:
            C.memmove(ram + 0x100012, struct.pack(">H", hidden_sectors + word(0x100012)), 2)
            hidden_sectors = 0
        core.retro_run()
        playing = byte(0x100000)
        current = word(0x10000A)
        if (playing == 1 and long(0x100024) in (0x1A0000, 0x1A8C00)
                and long(0x100028) in (0x1A0000, 0x1A8C00)):
            playing_seen = True
        if playing_seen and pcm_pointer:
            reply = byte(0x100005)
            complete = reply == 0 or (not byte(0x100006) and long(0x100038) == 524288)
            if complete and reply not in checked_pcm_replies:
                checked_pcm_replies.add(reply)
                start = 0 if reply == 0 else 1048576 + (reply - 1) * 524288
                expected = expected_pcm[start:start + (1048576 if reply == 0 else 524288)]
                bank = 0 if reply == 0 else (reply - 1) & 1
                if expected:
                    actual = C.string_at(pcm_pointer + bank * 524288, len(expected))
                    mismatch = np.flatnonzero(np.frombuffer(actual, np.uint8)
                                              != np.frombuffer(expected, np.uint8))
                    pcm_checks.append(dict(frame=word(0x10000A), reply=reply, bank=bank,
                                           bytes=len(expected), mismatched_bytes=len(mismatch),
                                           first_mismatch=int(mismatch[0]) if len(mismatch) else None))
        if playing_seen and current != last:
            if last >= 0 and current != last + 1:
                sequence_errors.append(dict(field=host, previous=last, frame=current))
            state = dict(field=host, frame=current, playing=playing,
                         next=word(0x10000C), buffered=word(0x100012),
                         sectors=long(0x100020), busy=byte(0x10003C),
                         ready=byte(0x100001), paused=byte(0x100008),
                         overflow=byte(0x100004), late=long(0x10004C),
                         audio_reply=byte(0x100005), audio_write=long(0x100034),
                         audio_upload=long(0x100038), audio_active=byte(0x100006),
                         audio_bank=byte(0x100007), audio_sample=audio_frames,
                         fault=long(0x100040))
            events.append(state)
            if not current + 1 <= state["next"] <= current + 2:
                sequence_errors.append(dict(field=host, frame=current, next=state["next"]))
            if current % 100 == 0:
                print(json.dumps(state), flush=True)
            last, last_change, age = current, host, 0
        else:
            age += 1
        if (playing_seen and age == 1 and args.capture_every > 0
                and (current % args.capture_every == 0 or current >= expected_frames - 10)):
            capture(f"frame-{current}.png")
        if playing_seen and age == 1 and oracle is not None:
            while reference_frame < current:
                reference_rgb = next(oracle, None)
                reference_frame += 1
                if reference_rgb is None:
                    break
            if reference_rgb is not None:
                map_pointer = long(0x10002C)
                tile_pointer = long(0x100024)
                valid_pointer = long(0x100050)
                map_data = C.string_at(ram + map_pointer, 560)
                map_ok = map_data == struct.pack(">280H", *reference_state["tile_map"])
                bad_tiles = [p for p, valid in enumerate(reference_state["valid"])
                             if valid and C.string_at(ram + tile_pointer + p * 128, 128)
                             != reference_state["tiles"][p]]
                valid_ok = C.string_at(ram + valid_pointer, 280) == bytes(reference_state["valid"])
                rgb = captured_rgb()
                error = float(np.abs(rgb.astype(np.int16) - reference_rgb.astype(np.int16)).mean())
                check = dict(field=host, frame=current, map_ok=map_ok,
                             cpu_tiles_ok=not bad_tiles, valid_ok=valid_ok, image_error=error)
                checks.append(check)
                if not map_ok or bad_tiles or not valid_ok or error > args.max_mean_error:
                    if len(errors) < 20:
                        errors.append(dict(**check, bad_tiles=bad_tiles))
                    if len(errors) == 1:
                        capture("first-error.png")
                        Image.fromarray(reference_rgb).save(out / "first-error-reference.png")
        if playing_seen and host - last_change > 240:
            outcome = "stalled" if playing else "stopped"
            break
    capture("last.png")
    if audio_writer is not None:
        audio_writer.close()
    raw = C.string_at(ram, core.retro_get_memory_size(2))
    (out / "ram.bin").write_bytes(raw)
    pc = core.m68k_get_reg(None, 16) if hasattr(core, "m68k_get_reg") else None
    required_pcm_replies = set(range(1 + max(0, len(expected_pcm) - 1048576 + 524287) // 524288))
    missing_pcm_replies = sorted(required_pcm_replies - checked_pcm_replies) if args.check_pcm else []
    playback_timing = None
    if len(events) > 1:
        authored_seconds = (events[-1]["frame"] - events[0]["frame"]) / 15
        audio_seconds = (events[-1]["audio_sample"] - events[0]["audio_sample"]) / av.timing.sample_rate
        playback_timing = dict(authored_seconds=authored_seconds, audio_seconds=audio_seconds,
                               duration_error_seconds=audio_seconds - authored_seconds)
    report = dict(outcome=outcome, fields=host + 1, frame=last, pc=pc,
                  audio_frames=audio_frames, refresh_hz=av.timing.fps,
                  sample_rate=av.timing.sample_rate, pixel_format=pixel_format, events=events,
                  sequence_errors=sequence_errors, errors=errors, checks=checks,
                  pcm_checks=pcm_checks, missing_pcm_replies=missing_pcm_replies,
                  playback_timing=playback_timing,
                  starvation_start=starvation_start,
                  final_state={"playing": byte(0x100000), "busy": byte(0x10003C),
                               "next": word(0x10000C), "ready": byte(0x100001),
                               "buffered": word(0x100012), "fault": long(0x100040)})
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    summary = {k: v for k, v in report.items()
               if k not in ("events", "checks", "errors", "sequence_errors")}
    summary.update(checked_frames=len(checks), recorded_errors=len(errors),
                   sequence_errors=len(sequence_errors))
    if errors:
        summary["first_error"] = {k: v for k, v in errors[0].items() if k != "bad_tiles"}
    print(json.dumps(summary), flush=True)
    core.retro_unload_game()
    core.retro_deinit()
    return int(bool(sequence_errors or errors or report["final_state"]["fault"]
                    or any(check["mismatched_bytes"] for check in pcm_checks)
                    or missing_pcm_replies
                    or not checks or outcome == "timeout"
                    or (args.starve_frame is not None and starvation_start is None)
                    or (expected_frames is not None and last != expected_frames - 1)))


if __name__ == "__main__":
    raise SystemExit(main())
