#!/usr/bin/env python3
"""Run the installed NeoCD libretro core headlessly and record player diagnostics."""

import argparse
import ctypes as C
import json
import shutil
import struct
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))


class Variable(C.Structure):
    _fields_ = [("key", C.c_char_p), ("value", C.c_char_p)]


class Game(C.Structure):
    _fields_ = [("path", C.c_char_p), ("data", C.c_void_p),
                ("size", C.c_size_t), ("meta", C.c_char_p)]


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
    args = parser.parse_args()
    if args.fields <= 0 or args.starve_fields <= 0 or args.max_mean_error < 0:
        parser.error("fields and starvation duration must be positive; error tolerance must be nonnegative")
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
    ram = core.retro_get_memory_data(2)
    if not ram or core.retro_get_memory_size(2) < 0x200000:
        raise RuntimeError("NeoCD did not expose program RAM")

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
        if playing_seen and current != last:
            if last >= 0 and current != last + 1:
                sequence_errors.append(dict(field=host, previous=last, frame=current))
            state = dict(field=host, frame=current, playing=playing,
                         next=word(0x10000C), buffered=word(0x100012),
                         sectors=long(0x100020), busy=byte(0x10003C),
                         ready=byte(0x100001), paused=byte(0x100008),
                         overflow=byte(0x100004), late=long(0x10004C),
                         fault=long(0x100040))
            events.append(state)
            if not current + 1 <= state["next"] <= current + 2:
                sequence_errors.append(dict(field=host, frame=current, next=state["next"]))
            if current % 100 == 0:
                print(json.dumps(state), flush=True)
            last, last_change, age = current, host, 0
        else:
            age += 1
        if playing_seen and age == 1 and (current % 50 == 0 or current >= 1100):
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
    raw = C.string_at(ram, core.retro_get_memory_size(2))
    (out / "ram.bin").write_bytes(raw)
    pc = core.m68k_get_reg(None, 16) if hasattr(core, "m68k_get_reg") else None
    report = dict(outcome=outcome, fields=host + 1, frame=last, pc=pc,
                  audio_frames=audio_frames, pixel_format=pixel_format, events=events,
                  sequence_errors=sequence_errors, errors=errors, checks=checks,
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
                    or not checks or outcome == "timeout"
                    or (args.starve_frame is not None and starvation_start is None)
                    or (expected_frames is not None and last != expected_frames - 1)))


if __name__ == "__main__":
    raise SystemExit(main())
