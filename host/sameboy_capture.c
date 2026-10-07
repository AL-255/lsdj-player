// SPDX-License-Identifier: GPL-2.0-or-later
// Deterministic CGB-E audio and cycle-accurate bus-write capture using SameBoy.
// GB_INTERNAL is deliberate: the public GB_run counter cannot timestamp a bus
// callback inside the current instruction. absolute_debugger_ticks can.
#include <Core/gb.h>
#include <errno.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifndef SAMEBOY_REVISION
#define SAMEBOY_REVISION "unknown"
#endif
#ifndef SAMEBOY_BOOT_ROM
#define SAMEBOY_BOOT_ROM "reference/SameBoy/build/bin/BootROMs/cgb_boot.bin"
#endif
#ifndef SAMEBOY_DMG_BOOT_ROM
#define SAMEBOY_DMG_BOOT_ROM "reference/SameBoy/build/bin/BootROMs/dmg_boot.bin"
#endif

typedef struct {
    uint64_t frame;
    GB_key_t key;
    bool down;
    unsigned order;
} Button;

typedef struct {
    FILE *wav, *trace, *events, *reads, *audio_events, *timing_events, *sweep_events, *pitch_events;
    uint64_t frames, samples, writes, apu_writes, triggers, audio_energy;
    unsigned peak;
    uint64_t first_nonzero_sample, first_trigger_tick, boot_boundary_tick;
    bool has_nonzero, has_trigger, saw_boot_boundary;
    uint16_t extra_addresses[32];
    unsigned extra_address_count;
    uint64_t audio_event_from, audio_event_to;
    uint64_t timing_tick_from, timing_tick_to;
    uint64_t advance_start_tick, advance_end_tick;
    unsigned advance_cpu_cycles;
    bool in_cpu_advance;
    uint64_t pitch_step, pitch_origin, next_pitch_tick, pitch_snapshots;
    const uint32_t *pixels;
    uint32_t *screen_pixels;
    uint64_t screen_tick, screen_frame;
    bool screen_skip_partial;
    uint32_t *execution_hits;
    uint16_t previous_pc;
    uint8_t previous_opcode;
    uint64_t entry_keys[4096];
    uint32_t entry_counts[4096];
} Capture;

// Optional read-only instruction coverage for isolating the native interpreter.
static void execution_profile(GB_gameboy_t *gb, uint16_t address, uint8_t opcode) {
    Capture *c = GB_get_user_data(gb);
    if (!gb->boot_rom_finished) return;
    if (address < 0x8000) {
        unsigned bank = address < 0x4000 ? 0 : gb->mbc_rom_bank;
        if (bank < 64) ++c->execution_hits[bank * 0x4000 + (address & 0x3fff)];
        if (bank == 2 && c->previous_pc < 0x4000 &&
            (c->previous_opcode == 0xcd || c->previous_opcode == 0xc3 || c->previous_opcode == 0xe9)) {
            uint64_t key = ((uint64_t)c->previous_pc << 16) | address;
            unsigned slot = (key ^ (key >> 12)) & 4095;
            while (c->entry_counts[slot] && c->entry_keys[slot] != key) slot = (slot + 1) & 4095;
            c->entry_keys[slot] = key;
            ++c->entry_counts[slot];
        }
    }
    c->previous_pc = address;
    c->previous_opcode = opcode;
}

static void fail(const char *message);

// Optional observation hook used only by a separately compiled diagnostic
// copy of Core/timing.c and Core/apu.c. It never mutates emulator state.
void GB_capture_timing_event(GB_gameboy_t *gb, const char *kind, unsigned first, unsigned second) {
    Capture *c = GB_get_user_data(gb);
    if (!c || (!c->timing_events && !c->sweep_events)) return;
    if (!strcmp(kind, "advance_begin")) {
        c->advance_start_tick = gb->absolute_debugger_ticks;
        c->advance_end_tick = c->advance_start_tick + first * (gb->cgb_double_speed ? 1 : 2);
        c->advance_cpu_cycles = first;
        c->in_cpu_advance = true;
    }
    else if (!strcmp(kind, "advance_apu_begin")) c->advance_end_tick = gb->absolute_debugger_ticks;
    else if (!strcmp(kind, "advance_end")) c->in_cpu_advance = false;
    if (c->sweep_events && !strcmp(kind, "sweep_overflow")) {
        uint16_t bank = 0;
        GB_get_direct_access(gb, gb->pc < 0x4000 ? GB_DIRECT_ACCESS_ROM0 : GB_DIRECT_ACCESS_ROM, NULL, &bank);
        fprintf(c->sweep_events,
            "%" PRIu64 "\t%" PRIu64 "\t%04x\t%u\t%" PRIu64 "\t%" PRIu64 "\t%u\t%u\t%u\t%u\t%u\t%u\t%u\t%u\t%u\t%u\n",
            gb->absolute_debugger_ticks, c->samples, gb->pc, bank,
            c->advance_start_tick, c->advance_end_tick, c->advance_cpu_cycles,
            c->in_cpu_advance, gb->cgb_double_speed, first, second,
            gb->io_registers[GB_IO_NR10], gb->apu.is_active[0], gb->apu.samples[0],
            gb->apu.square_channels[0].sample_countdown, gb->apu.wave_channel.enable);
    }
    if (!c->timing_events || gb->absolute_debugger_ticks < c->timing_tick_from ||
        (c->timing_tick_to && gb->absolute_debugger_ticks >= c->timing_tick_to)) return;
    fprintf(c->timing_events,
        "%s\t%" PRIu64 "\t%" PRIu64 "\t%u\t%u\t%u\t%u\t%u\t%u\t%u\t%u\t%u\t%u\t%u\t%u\t%u\t%u\t%d\t%d\n",
        kind, gb->absolute_debugger_ticks, c->samples, first, second,
        gb->apu.apu_cycles, gb->apu_output.sample_cycles, gb->apu_output.sample_fraction,
        gb->apu_output.cycles_since_render, gb->io_registers[GB_IO_NR10],
        gb->apu.lf_div, gb->apu.square_channels[0].sample_countdown,
        gb->apu.square_channels[0].sample_length, gb->apu.square_channels[0].current_sample_index,
        gb->apu.is_active[0], gb->apu.samples[0], gb->apu.wave_channel.enable,
        gb->apu_output.band_limited[0].input.left, gb->apu_output.band_limited[0].output.left);
}

static void put16(FILE *f, uint16_t v) {
    fputc(v & 255, f); fputc(v >> 8, f);
}
static void put32(FILE *f, uint32_t v) {
    put16(f, v & 65535); put16(f, v >> 16);
}
static void wav_header(FILE *f, uint32_t samples, unsigned rate) {
    rewind(f);
    fwrite("RIFF", 1, 4, f); put32(f, 36 + samples * 4);
    fwrite("WAVEfmt ", 1, 8, f); put32(f, 16); put16(f, 1); put16(f, 2);
    put32(f, rate); put32(f, rate * 4); put16(f, 4); put16(f, 16);
    fwrite("data", 1, 4, f); put32(f, samples * 4);
}
static void audio(GB_gameboy_t *gb, GB_sample_t *sample) {
    Capture *c = GB_get_user_data(gb);
    if (c->wav) { put16(c->wav, sample->left); put16(c->wav, sample->right); }
    unsigned l = abs((int)sample->left), r = abs((int)sample->right);
    c->audio_energy += l + r;
    if (l > c->peak) c->peak = l;
    if (r > c->peak) c->peak = r;
    if ((l || r) && !c->has_nonzero) {
        c->has_nonzero = true; c->first_nonzero_sample = c->samples;
    }
    if (c->audio_events && c->samples >= c->audio_event_from &&
        (!c->audio_event_to || c->samples < c->audio_event_to)) {
        fprintf(c->audio_events,
            "%" PRIu64 "\t%" PRIu64 "\t%d\t%d\t%u\t%u\t%u\t%u\t%.17g\t%.17g\t%.17g\t%.17g\t%.17g\t%.17g",
            c->samples, gb->absolute_debugger_ticks, sample->left, sample->right,
            gb->apu_output.cycles_since_render, gb->apu.apu_cycles,
            gb->apu_output.sample_cycles, gb->apu_output.sample_fraction,
            gb->apu_output.dac_discharge[0], gb->apu_output.dac_discharge[1],
            gb->apu_output.dac_discharge[2], gb->apu_output.dac_discharge[3],
            gb->apu_output.highpass_diff.left, gb->apu_output.highpass_diff.right);
        for (unsigned i = 0; i < 4; ++i) {
            GB_band_limited_t *band = &gb->apu_output.band_limited[i];
            uint64_t hash = UINT64_C(1469598103934665603);
            const unsigned char *buffer = (const unsigned char *)band->buffer;
            for (unsigned byte = 0; byte < sizeof(band->buffer); ++byte)
                hash = (hash ^ buffer[byte]) * UINT64_C(1099511628211);
            fprintf(c->audio_events, "\t%u\t%d\t%d\t%d\t%d\t%u\t%u\t%u\t%" PRIu64,
                band->pos, band->input.left, band->input.right,
                band->output.left, band->output.right, band->silence_detection,
                gb->apu.is_active[i], gb->apu.samples[i], hash);
        }
        fprintf(c->audio_events, "\t%u\t%u\t%u\t%u\t%u\t%u\t%u\t%u\t%u\t%u\t%u",
            gb->pc, gb->address_bus, gb->div_counter,
            gb->io_registers[GB_IO_NR12], gb->io_registers[GB_IO_NR22],
            gb->io_registers[GB_IO_NR30], gb->io_registers[GB_IO_NR42],
            gb->apu.wave_channel.sample_countdown, gb->apu.wave_channel.current_sample_index,
            gb->apu.wave_channel.current_sample_byte, gb->apu.wave_channel.bugged_read_countdown);
        for (unsigned i = 0; i < 2; ++i)
            fprintf(c->audio_events, "\t%u\t%u\t%u\t%u\t%u\t%u\t%u",
                gb->apu.square_channels[i].sample_countdown, gb->apu.square_channels[i].sample_length,
                gb->apu.square_channels[i].current_sample_index, gb->apu.square_channels[i].current_volume,
                gb->apu.square_channels[i].delay, gb->apu.square_channels[i].did_tick,
                gb->apu.square_channels[i].just_reloaded);
        fprintf(c->audio_events, "\t%u\t%u\t%u\t%u\t%u\t%u\t%u\t%u\n",
            gb->io_registers[GB_IO_NR10], gb->apu.lf_div, gb->apu.div_divider,
            gb->apu.square_sweep_countdown, gb->apu.square_sweep_calculate_countdown,
            gb->apu.square_sweep_calculate_countdown_reload_timer,
            gb->apu.channel_1_restart_hold, gb->apu.shadow_sweep_sample_length);
    }
    ++c->samples;
}
static uint8_t bus_read(GB_gameboy_t *gb, uint16_t address, uint8_t value) {
    Capture *c = GB_get_user_data(gb);
    if (c->reads && ((address >= 0xff10 && address <= 0xff3f) || address == 0xff76 || address == 0xff77)) {
        uint16_t bank = 0;
        GB_get_direct_access(gb, gb->pc < 0x4000 ? GB_DIRECT_ACCESS_ROM0 : GB_DIRECT_ACCESS_ROM, NULL, &bank);
        fprintf(c->reads, "%" PRIu64 "\t%" PRIu64 "\t%04x\t%u\t%04x\t%02x\n",
                gb->absolute_debugger_ticks, c->frames, gb->pc, bank, address, value);
    }
    return value;
}

// Snapshot effective oscillator periods without running the APU or reading an
// emulated I/O address. Scheduled timestamps stay fixed while the LCD is off.
// Observed timestamps disclose the instruction-boundary sampling latency.
static void pitch_due(GB_gameboy_t *gb) {
    Capture *c = GB_get_user_data(gb);
    if (!c->pitch_events) return;
    while (c->next_pitch_tick <= gb->absolute_debugger_ticks) {
        unsigned active = 0, dac = 0;
        for (unsigned i = 0; i < 4; ++i) active |= (unsigned)gb->apu.is_active[i] << i;
        dac |= !!(gb->io_registers[GB_IO_NR12] & 0xf8);
        dac |= !!(gb->io_registers[GB_IO_NR22] & 0xf8) << 1;
        dac |= (unsigned)gb->apu.wave_channel.enable << 2;
        dac |= !!(gb->io_registers[GB_IO_NR42] & 0xf8) << 3;
        unsigned noise = gb->io_registers[GB_IO_NR43];
        fprintf(c->pitch_events,
            "%" PRIu64 "\t%" PRIu64 "\t%" PRIu64 "\t%u\t%u\t%u\t%u\t%u\t%u"
            "\t%u\t%u\t%u\t%u\t%u\t%u\t%u\t%u\t%u\t%u\t%u\t%u\t%u\t%u\t%u\t%u\t%u\n",
            c->next_pitch_tick, gb->absolute_debugger_ticks, c->samples,
            gb->apu.apu_cycles, gb->apu.global_enable, active, dac,
            gb->io_registers[GB_IO_NR50], gb->io_registers[GB_IO_NR51],
            gb->apu.square_channels[0].sample_length, gb->apu.square_channels[1].sample_length,
            gb->apu.wave_channel.sample_length, noise, noise & 7, noise >> 4,
            gb->apu.noise_channel.narrow, gb->apu.square_channels[0].current_volume,
            gb->apu.square_channels[1].current_volume, (gb->io_registers[GB_IO_NR32] >> 5) & 3,
            gb->apu.noise_channel.current_volume, gb->io_registers[GB_IO_NR11] >> 6,
            gb->io_registers[GB_IO_NR21] >> 6, gb->io_registers[GB_IO_NR10],
            gb->io_registers[GB_IO_NR12], gb->io_registers[GB_IO_NR22], gb->io_registers[GB_IO_NR42]);
        ++c->pitch_snapshots;
        if (UINT64_MAX - c->next_pitch_tick < c->pitch_step) fail("Pitch schedule exceeds tick range");
        c->next_pitch_tick += c->pitch_step;
    }
}
static void vblank(GB_gameboy_t *gb, GB_vblank_type_t type) {
    Capture *c = GB_get_user_data(gb);
    ++c->frames;
    // The live output buffer is overwritten one scanline at a time. Preserve
    // a presented frame so an arbitrary tick limit cannot mix two frames.
    if (c->screen_pixels && type != GB_VBLANK_TYPE_REPEAT && type != GB_VBLANK_TYPE_SKIPPED_FRAME) {
        // Save states omit the host pixel buffer. A state loaded during
        // scanout cannot supply a whole image until the following frame.
        if (c->screen_skip_partial && type == GB_VBLANK_TYPE_NORMAL_FRAME) {
            c->screen_skip_partial = false;
            return;
        }
        c->screen_skip_partial = false;
        memcpy(c->screen_pixels, c->pixels, 160 * 144 * sizeof(*c->pixels));
        c->screen_tick = gb->absolute_debugger_ticks;
        c->screen_frame = c->frames;
    }
}
static void event_log(GB_gameboy_t *gb, const char *name) {
    Capture *c = GB_get_user_data(gb);
    if (c->events) fprintf(c->events,
        "%" PRIu64 "\t%" PRIu64 "\t%s\t%04x\t%u\t%d\t%u\t%u\t%u\t%u\t%u\n",
        gb->absolute_debugger_ticks, c->frames, name, gb->pc, gb->div_counter,
        gb->cgb_double_speed, gb->ime, gb->interrupt_enable,
        GB_safe_read_memory(gb, 0xff00), gb->apu.div_divider, gb->apu.lf_div);
}
static bool bus_write(GB_gameboy_t *gb, uint16_t address, uint8_t value) {
    Capture *c = GB_get_user_data(gb);
    bool apu = address >= 0xff10 && address <= 0xff3f;
    bool extra = false;
    for (unsigned i = 0; i < c->extra_address_count; ++i)
        if (address == c->extra_addresses[i]) extra = true;
    if (apu || (address >= 0xff04 && address <= 0xff07) || address == 0xff4d || extra) {
        ++c->writes;
        if (apu) ++c->apu_writes;
        if ((address == 0xff14 || address == 0xff19 || address == 0xff1e || address == 0xff23) && (value & 0x80)) {
            if (!c->has_trigger) { c->has_trigger = true; c->first_trigger_tick = gb->absolute_debugger_ticks; }
            ++c->triggers;
        }
        if (c->trace) {
            uint16_t bank = 0;
            GB_get_direct_access(gb, gb->pc < 0x4000 ? GB_DIRECT_ACCESS_ROM0 : GB_DIRECT_ACCESS_ROM, NULL, &bank);
            fprintf(c->trace, "%" PRIu64 "\t%" PRIu64 "\t%04x\t%u\t%04x\t%02x\n",
                    gb->absolute_debugger_ticks, c->frames, gb->pc, bank, address, value);
        }
    }
    return true;
}
static uint32_t rgb(GB_gameboy_t *gb, uint8_t r, uint8_t g, uint8_t b) {
    (void)gb;
    return (uint32_t)r << 16 | (uint32_t)g << 8 | b;
}
static void log_message(GB_gameboy_t *gb, const char *message, GB_log_attributes_t attributes) {
    (void)gb; (void)attributes;
    fputs(message, stderr);
}
static void fail(const char *message) { fprintf(stderr, "%s\n", message); exit(2); }
static uint64_t integer(const char *text) {
    char *end;
    errno = 0;
    uint64_t n = strtoull(text, &end, 0);
    if (errno || !*text || *end || *text == '-') fail("Invalid nonnegative integer");
    return n;
}
static GB_key_t key(const char *name) {
    const char *names[] = {"RIGHT", "LEFT", "UP", "DOWN", "A", "B", "SELECT", "START"};
    for (unsigned i = 0; i < 8; ++i) if (!strcmp(name, names[i])) return (GB_key_t)i;
    fail("Unknown button name (use uppercase RIGHT LEFT UP DOWN A B SELECT START)");
    return GB_KEY_START;
}
static int compare_button(const void *a_, const void *b_) {
    const Button *a = a_, *b = b_;
    if (a->frame != b->frame) return a->frame > b->frame ? 1 : -1;
    if (a->order == b->order) return 0;
    return a->order > b->order ? 1 : -1;
}
static size_t buttons(const char *schedule, Button out[4096]) {
    size_t count = 0;
    char *copy = strdup(schedule), *save;
    for (char *entry = strtok_r(copy, ",", &save); entry; entry = strtok_r(NULL, ",", &save)) {
        char *colon = strchr(entry, ':'), *second = colon ? strchr(colon + 1, ':') : NULL;
        if (!second || count == 4096) fail("Invalid button schedule; use frame:START:down,frame:START:up");
        *colon = *second = 0;
        bool down = !strcmp(second + 1, "down");
        if (!down && strcmp(second + 1, "up")) fail("Button state must be down or up");
        out[count] = (Button){integer(entry), key(colon + 1), down, (unsigned)count};
        ++count;
    }
    free(copy);
    qsort(out, count, sizeof(*out), compare_button);
    return count;
}
static void usage(void) {
    puts("sameboy-capture --rom FILE --frames N [--sav FILE] [--wav FILE] [--trace FILE]\n"
         "  [--buttons '180:B:down,188:B:up,196:START:down,204:START:up']\n"
         "  [--sample-rate 48000] [--boot-rom FILE] [--highpass accurate|off|remove-dc]\n"
         "  [--screen FILE.ppm] [--events FILE.tsv] [--state-in FILE] [--state-out FILE]\n"
         "  [--memory-out FILE] [--ticks N] [--force-cgb] [--vblank-buttons]\n"
         "  [--trace-address ff03] (repeat to trace extra bus addresses)\n"
         "  [--reads FILE.tsv] [--audio-events FILE.tsv]\n"
         "  [--audio-event-from SAMPLE] [--audio-event-to SAMPLE]\n"
         "  [--timing-events FILE] [--timing-tick-from TICK] [--timing-tick-to TICK]\n"
         "  [--sweep-events FILE] (requires build_sameboy.py --observe-sweeps)\n"
         "  [--pitch-events FILE] [--pitch-step-ticks N] [--pitch-origin-tick N]\n"
         "  [--model cgb|dmg] (default CGB-E; dmg selects original DMG-B hardware)\n"
         "WAV is stereo signed 16-bit PCM. Trace timestamps are\n"
         "exact 8 MHz ticks; scheduled frames each mean 140448 ticks from reset.\n"
         "Screenshots save the latest completed frame without extending the tick limit.\n"
         "--force-cgb changes only the loaded cartridge header bit/checksum in memory.\n"
         "Randomness, joypad bouncing, clock throttling, and analog interference are off.");
}

int main(int argc, char **argv) {
    const char *rom = NULL, *sav = NULL, *wav = NULL, *trace = NULL, *screen = NULL;
    const char *state_in = NULL, *state_out = NULL, *memory_out = NULL, *event_path = NULL, *native_dump = NULL;
    unsigned break_bank = 65535, break_pc = 65535;
    const char *read_path = NULL, *audio_path = NULL;
    const char *timing_path = NULL, *sweep_path = NULL, *pitch_path = NULL, *execution_path = NULL;
    const char *boot = NULL, *schedule = "", *filter = "accurate", *model_name = "CGB-E";
    GB_model_t model = GB_MODEL_CGB_E;
    uint64_t limit_frames = 0, limit_ticks = 0;
    unsigned rate = 48000;
    bool force_cgb = false, vblank_buttons = false;
    Capture c = {.pitch_step = 140448};
    for (int i = 1; i < argc; ++i) {
        if (!strcmp(argv[i], "--help")) { usage(); return 0; }
        if (!strcmp(argv[i], "--force-cgb")) { force_cgb = true; continue; }
        if (!strcmp(argv[i], "--vblank-buttons")) { vblank_buttons = true; continue; }
        if (!strcmp(argv[i], "--clock-frames")) continue;
        if (i + 1 == argc) fail("Missing argument");
        const char *v = argv[++i], *option = argv[i - 1];
        if (!strcmp(option, "--rom")) rom = v;
        else if (!strcmp(option, "--sav")) sav = v;
        else if (!strcmp(option, "--wav")) wav = v;
        else if (!strcmp(option, "--trace")) trace = v;
        else if (!strcmp(option, "--screen")) screen = v;
        else if (!strcmp(option, "--events")) event_path = v;
        else if (!strcmp(option, "--reads")) read_path = v;
        else if (!strcmp(option, "--audio-events")) audio_path = v;
        else if (!strcmp(option, "--audio-event-from")) c.audio_event_from = integer(v);
        else if (!strcmp(option, "--audio-event-to")) c.audio_event_to = integer(v);
        else if (!strcmp(option, "--timing-events")) timing_path = v;
        else if (!strcmp(option, "--sweep-events")) sweep_path = v;
        else if (!strcmp(option, "--pitch-events")) pitch_path = v;
        else if (!strcmp(option, "--execution-profile")) execution_path = v;
        else if (!strcmp(option, "--pitch-step-ticks")) c.pitch_step = integer(v);
        else if (!strcmp(option, "--pitch-origin-tick")) c.pitch_origin = integer(v);
        else if (!strcmp(option, "--model")) {
            if (!strcmp(v, "cgb") || !strcmp(v, "cgb-e") || !strcmp(v, "CGB-E")) {
                model = GB_MODEL_CGB_E; model_name = "CGB-E";
            }
            else if (!strcmp(v, "dmg") || !strcmp(v, "dmg-b") || !strcmp(v, "DMG-B")) {
                model = GB_MODEL_DMG_B; model_name = "DMG-B";
            }
            else fail("Model must be cgb or dmg");
        }
        else if (!strcmp(option, "--timing-tick-from")) c.timing_tick_from = integer(v);
        else if (!strcmp(option, "--timing-tick-to")) c.timing_tick_to = integer(v);
        else if (!strcmp(option, "--state-in")) state_in = v;
        else if (!strcmp(option, "--state-out")) state_out = v;
        else if (!strcmp(option, "--native-dump")) native_dump = v;
        else if (!strcmp(option, "--break-pc")) {
            char *end;
            break_bank = strtoul(v, &end, 0);
            if (*end != ':') fail("Expected bank:address for --break-pc");
            break_pc = strtoul(end + 1, &end, 0);
            if (*end || break_bank > 511 || break_pc > 65535) fail("Invalid breakpoint");
        }
        else if (!strcmp(option, "--memory-out")) memory_out = v;
        else if (!strcmp(option, "--boot-rom")) boot = v;
        else if (!strcmp(option, "--buttons")) schedule = v;
        else if (!strcmp(option, "--highpass")) filter = v;
        else if (!strcmp(option, "--frames")) limit_frames = integer(v);
        else if (!strcmp(option, "--ticks")) limit_ticks = integer(v);
        else if (!strcmp(option, "--trace-address")) {
            char *end;
            unsigned long address = strtoul(v, &end, 16);
            if (!*v || *end || address > 0xffff || c.extra_address_count == 32) fail("Invalid trace address");
            c.extra_addresses[c.extra_address_count++] = (uint16_t)address;
        }
        else if (!strcmp(option, "--sample-rate")) { uint64_t r = integer(v); if (r < 8000 || r > 8388608) fail("Sample rate must be 8000..8388608"); rate = (unsigned)r; }
        else fail("Unknown option");
    }
    if (!rom || (!limit_frames && !limit_ticks)) { usage(); return 2; }
    if (!c.pitch_step || c.pitch_step % 4) fail("Pitch step must be a positive multiple of four ticks");
    if (force_cgb && model != GB_MODEL_CGB_E) fail("--force-cgb requires CGB hardware");
    if (!boot) boot = model == GB_MODEL_DMG_B ? SAMEBOY_DMG_BOOT_ROM : SAMEBOY_BOOT_ROM;
    GB_highpass_mode_t highpass;
    if (!strcmp(filter, "accurate")) highpass = GB_HIGHPASS_ACCURATE;
    else if (!strcmp(filter, "off")) highpass = GB_HIGHPASS_OFF;
    else if (!strcmp(filter, "remove-dc")) highpass = GB_HIGHPASS_REMOVE_DC_OFFSET;
    else fail("Invalid highpass mode");

    Button events[4096];
    size_t count = buttons(schedule, events), event = 0;
    if (wav) { c.wav = fopen(wav, "wb"); if (!c.wav) fail("Cannot open WAV output"); wav_header(c.wav, 0, rate); }
    if (trace) {
        c.trace = fopen(trace, "w"); if (!c.trace) fail("Cannot open trace output");
        fputs("ticks_8mhz\tframe\tpc\trom_bank\taddress\tvalue\n", c.trace);
    }
    if (event_path) {
        c.events = fopen(event_path, "w"); if (!c.events) fail("Cannot open event output");
        fputs("ticks_8mhz\tframe\tevent\tpc\tdiv_counter\tdouble_speed\time\tie\tjoyp\tapu_divider\tapu_lf_div\n", c.events);
    }
    if (read_path) {
        c.reads = fopen(read_path, "w"); if (!c.reads) fail("Cannot open read trace");
        fputs("ticks_8mhz\tframe\tpc\trom_bank\taddress\tvalue\n", c.reads);
    }
    if (audio_path) {
        c.audio_events = fopen(audio_path, "w"); if (!c.audio_events) fail("Cannot open audio-event trace");
        fputs("sample_index\tticks_8mhz\tleft\tright\tcycles_since_render\tapu_cycles\tsample_cycles\tsample_fraction\tdac_0\tdac_1\tdac_2\tdac_3\thighpass_left\thighpass_right", c.audio_events);
        for (unsigned i = 0; i < 4; ++i)
            fprintf(c.audio_events, "\tch%u_pos\tch%u_input_left\tch%u_input_right\tch%u_output_left\tch%u_output_right\tch%u_silence\tch%u_active\tch%u_sample\tch%u_buffer_hash",
                    i, i, i, i, i, i, i, i, i);
        fputs("\tpc\taddress_bus\tdiv_counter\tnr12\tnr22\tnr30\tnr42\twave_countdown\twave_index\twave_byte\twave_bug_countdown", c.audio_events);
        for (unsigned i = 0; i < 2; ++i)
            fprintf(c.audio_events, "\tch%u_countdown\tch%u_length\tch%u_index\tch%u_volume\tch%u_delay\tch%u_did_tick\tch%u_just_reloaded", i, i, i, i, i, i, i);
        fputs("\tnr10\tapu_lf_div\tapu_div_divider\tsweep_countdown\tsweep_calculate_countdown\tsweep_reload_timer\tchannel_1_restart_hold\tshadow_sweep_length\n", c.audio_events);
    }
    if (timing_path) {
        c.timing_events = fopen(timing_path, "w"); if (!c.timing_events) fail("Cannot open timing-event trace");
        fputs("event\tticks_8mhz\tsample_index\targ0\targ1\tapu_cycles\tsample_cycles\tsample_fraction\tcycles_since_render\tnr10\tapu_lf_div\tch0_countdown\tch0_length\tch0_index\tch0_active\tch0_sample\twave_enable\tch0_input_left\tch0_output_left\n", c.timing_events);
    }
    if (sweep_path) {
#ifndef SAMEBOY_OBSERVE_SWEEP
        fail("--sweep-events requires an observation build: tools/build_sameboy.py --observe-sweeps");
#else
        c.sweep_events = fopen(sweep_path, "w"); if (!c.sweep_events) fail("Cannot open sweep-event trace");
        fputs("ticks_8mhz\tsample_index\tpc\trom_bank\tadvance_start_tick\tadvance_end_tick\tadvance_cpu_cycles\tin_cpu_advance\tdouble_speed\tapu_cycles\tcycles_offset\tnr10\tch0_active\tch0_sample\tch0_countdown\twave_enable\n", c.sweep_events);
#endif
    }
    if (pitch_path) {
        c.pitch_events = fopen(pitch_path, "w"); if (!c.pitch_events) fail("Cannot open pitch-event trace");
        fputs("ticks_8mhz\tobserved_ticks_8mhz\tsample_index\tapu_pending_cycles\tglobal_enable\tactive_mask\tdac_mask\tnr50\tnr51\tpulse1_frequency_raw\tpulse2_frequency_raw\twave_frequency_raw\tnoise_nr43\tnoise_divisor_code\tnoise_shift\tnoise_narrow\tpulse1_volume\tpulse2_volume\twave_level\tnoise_volume\tpulse1_duty\tpulse2_duty\tnr10\tpulse1_envelope_raw\tpulse2_envelope_raw\tnoise_envelope_raw\n", c.pitch_events);
    }
    GB_random_set_enabled(false);
    GB_gameboy_t *gb = GB_init(GB_alloc(), model);
    GB_set_user_data(gb, &c);
    GB_set_log_callback(gb, log_message);
    GB_set_turbo_mode(gb, true, true);
    GB_set_rtc_mode(gb, GB_RTC_MODE_ACCURATE);
    GB_debugger_set_disabled(gb, true);
    GB_set_emulate_joypad_bouncing(gb, false);
    GB_set_sample_rate(gb, rate);
    GB_set_highpass_filter_mode(gb, highpass);
    GB_set_interference_volume(gb, 0);
    GB_apu_set_sample_callback(gb, audio);
    GB_set_vblank_callback(gb, vblank);
    GB_set_write_memory_callback(gb, bus_write);
    if (c.reads) GB_set_read_memory_callback(gb, bus_read);
    uint32_t pixels[160 * 144] = {0};
    uint32_t screen_pixels[160 * 144];
    c.pixels = pixels;
    c.screen_pixels = screen ? screen_pixels : NULL;
    GB_set_border_mode(gb, GB_BORDER_NEVER);
    GB_set_pixels_output(gb, pixels);
    GB_set_rgb_encode_callback(gb, rgb);
    if (GB_load_rom(gb, rom)) fail("Cannot load ROM");
    uint8_t header_cgb = gb->rom[0x143];
    if (force_cgb && !(header_cgb & 0x80)) {
        gb->rom[0x143] |= 0x80;
        uint8_t checksum = 0;
        for (unsigned i = 0x134; i <= 0x14c; ++i) checksum -= gb->rom[i] + 1;
        gb->rom[0x14d] = checksum;
    }
    if (GB_load_boot_rom(gb, boot)) fail("Cannot load SameBoy boot ROM (run tools/build_sameboy.py)");
    if (sav && GB_load_battery(gb, sav)) fail("Cannot load save file");
    if (state_in && GB_load_state(gb, state_in)) fail("Cannot load state");
    c.screen_skip_partial = screen && state_in && (GB_safe_read_memory(gb, 0xff40) & 0x80) &&
                            GB_safe_read_memory(gb, 0xff44) < 144;
    if (execution_path) {
        c.execution_hits = calloc(64 * 0x4000, sizeof(uint32_t));
        if (!c.execution_hits) fail("Cannot allocate execution profile");
        GB_set_execution_callback(gb, execution_profile);
    }
    uint64_t initial_ticks = gb->absolute_debugger_ticks;
    c.next_pitch_tick = c.pitch_origin;
    if (initial_ticks > c.pitch_origin) {
        uint64_t elapsed = initial_ticks - c.pitch_origin;
        uint64_t steps = elapsed / c.pitch_step + !!(elapsed % c.pitch_step);
        if (steps > (UINT64_MAX - c.pitch_origin) / c.pitch_step) fail("Pitch schedule exceeds tick range");
        c.next_pitch_tick += steps * c.pitch_step;
    }
    pitch_due(gb);
    while ((!limit_frames || gb->absolute_debugger_ticks - initial_ticks < limit_frames * 140448) &&
           (!limit_ticks || gb->absolute_debugger_ticks - initial_ticks < limit_ticks)) {
        if (gb->boot_rom_finished && gb->pc == break_pc &&
            (gb->pc < 0x4000 ? 0 : gb->mbc_rom_bank) == break_bank) break;
        if (gb->pc == 0x100 && gb->boot_rom_finished && !c.saw_boot_boundary) {
            c.saw_boot_boundary = true; c.boot_boundary_tick = gb->absolute_debugger_ticks;
            event_log(gb, "boot_boundary");
        }
        while (event < count && (vblank_buttons ? events[event].frame <= c.frames :
                                 events[event].frame * 140448 <= gb->absolute_debugger_ticks - initial_ticks)) {
            GB_set_key_state(gb, events[event].key, events[event].down);
            char name[48];
            snprintf(name, sizeof(name), "button_%u_%s", events[event].key, events[event].down ? "down" : "up");
            event_log(gb, name);
            ++event;
        }
        bool old_speed = gb->cgb_double_speed;
        bool old_halted = gb->halted, old_stopped = gb->stopped;
        bool stop = !gb->halted && !gb->stopped && GB_safe_read_memory(gb, gb->pc) == 0x10;
        if (stop) event_log(gb, "stop_begin");
        GB_run(gb);
        if (stop) event_log(gb, "stop_end");
        if (old_speed != gb->cgb_double_speed) event_log(gb, "speed_change");
        if ((old_halted && !gb->halted) || (old_stopped && !gb->stopped)) event_log(gb, "halt_or_stop_wake");
        pitch_due(gb);
        if (c.samples > (UINT32_MAX - 36) / 4) fail("WAV exceeds RIFF limit");
    }
    if (screen) {
        if (!c.screen_frame) fail("No completed frame available for screenshot; increase the tick limit");
        FILE *f = fopen(screen, "wb"); if (!f) fail("Cannot open screenshot");
        fputs("P6\n160 144\n255\n", f);
        for (unsigned i = 0; i < 160 * 144; ++i) { fputc(screen_pixels[i] >> 16, f); fputc(screen_pixels[i] >> 8, f); fputc(screen_pixels[i], f); }
        if (ferror(f) || fclose(f)) fail("Screenshot write failed");
    }
    if (native_dump) {
        const char *suffixes[] = {".wram.bin", ".song.bin", ".hram.bin", ".io.bin"};
        const uint8_t *buffers[] = {gb->ram, gb->mbc_ram, gb->hram, gb->io_registers};
        size_t sizes[] = {gb->ram_size, gb->mbc_ram_size < 0x8000 ? gb->mbc_ram_size : 0x8000, sizeof(gb->hram), sizeof(gb->io_registers)};
        char path[4096];
        for (unsigned i = 0; i < 4; ++i) {
            if (snprintf(path, sizeof(path), "%s%s", native_dump, suffixes[i]) >= sizeof(path)) fail("Snapshot path too long");
            FILE *f = fopen(path, "wb"); if (!f) fail("Cannot open native snapshot");
            if (fwrite(buffers[i], 1, sizes[i], f) != sizes[i] || fclose(f)) fail("Native snapshot write failed");
        }
        snprintf(path, sizeof(path), "%s.json", native_dump);
        FILE *f = fopen(path, "w"); if (!f) fail("Cannot open native snapshot metadata");
        fprintf(f, "{\"model\":\"%s\",\"tick\":%" PRIu64 ",\"pc\":%u,\"rom_bank\":%u,\"ram_bank\":%u,\"wram_bank\":%u,\"af\":%u,\"bc\":%u,\"de\":%u,\"hl\":%u,\"sp\":%u,\"ie\":%u,\"ime\":%u,\"double_speed\":%s}\n", model_name, gb->absolute_debugger_ticks, gb->pc, gb->mbc_rom_bank, gb->mbc_ram_bank, gb->cgb_ram_bank, gb->af, gb->bc, gb->de, gb->hl, gb->sp, gb->interrupt_enable, gb->ime, gb->cgb_double_speed ? "true" : "false");
        if (ferror(f) || fclose(f)) fail("Native metadata write failed");
    }
    if (execution_path) {
        FILE *f = fopen(execution_path, "w"); if (!f) fail("Cannot open execution profile");
        fputs("bank\taddress\tcount\n", f);
        for (unsigned bank = 0; bank < 64; ++bank)
            for (unsigned offset = 0; offset < 0x4000; ++offset)
                if (c.execution_hits[bank * 0x4000 + offset])
                    fprintf(f, "%u\t%04x\t%u\n", bank, offset + (bank ? 0x4000 : 0), c.execution_hits[bank * 0x4000 + offset]);
        for (unsigned slot = 0; slot < 4096; ++slot)
            if (c.entry_counts[slot]) fprintf(f, "entry\t%04x:%04x\t%u\n", (unsigned)(c.entry_keys[slot] >> 16), (unsigned)(c.entry_keys[slot] & 65535), c.entry_counts[slot]);
        if (ferror(f) || fclose(f)) fail("Execution profile write failed");
        free(c.execution_hits);
    }
    if (state_out && GB_save_state(gb, state_out)) fail("Cannot save state");
    if (memory_out) {
        FILE *f = fopen(memory_out, "wb"); if (!f) fail("Cannot open memory output");
        for (unsigned a = 0; a <= 0xffff; ++a) fputc(GB_safe_read_memory(gb, a), f);
        if (ferror(f) || fclose(f)) fail("Memory write failed");
    }
    if (c.wav) { wav_header(c.wav, (uint32_t)c.samples, rate); if (ferror(c.wav) || fclose(c.wav)) fail("WAV write failed"); }
    if (c.trace && (ferror(c.trace) || fclose(c.trace))) fail("Trace write failed");
    if (c.events && (ferror(c.events) || fclose(c.events))) fail("Event write failed");
    if (c.reads && (ferror(c.reads) || fclose(c.reads))) fail("Read trace write failed");
    if (c.audio_events && (ferror(c.audio_events) || fclose(c.audio_events))) fail("Audio-event write failed");
    if (c.timing_events && (ferror(c.timing_events) || fclose(c.timing_events))) fail("Timing-event write failed");
    if (c.sweep_events && (ferror(c.sweep_events) || fclose(c.sweep_events))) fail("Sweep-event write failed");
    if (c.pitch_events && (ferror(c.pitch_events) || fclose(c.pitch_events))) fail("Pitch-event write failed");
    printf("{\"sameboy_revision\":\"%s\",\"model\":\"%s\",\"native_cgb_mode\":%s,"
           "\"original_header_cgb\":%u,\"force_cgb\":%s,\"sample_rate\":%u,\"highpass\":\"%s\",\"highpass_mode\":\"%s\","
           "\"interference_volume\":0,\"random_enabled\":false,\"timebase_hz\":8388608,"
           "\"requested_frames\":%" PRIu64 ",\"vblank_buttons\":%s,"
           "\"frames\":%" PRIu64 ",\"ticks_8mhz\":%" PRIu64 ",\"initial_ticks_8mhz\":%" PRIu64 ","
           "\"boot_boundary_tick\":%" PRIu64 ",\"audio_frames\":%" PRIu64 ",\"first_nonzero_sample\":%" PRIu64 ","
           "\"first_trigger_tick\":%" PRIu64 ",\"writes\":%" PRIu64 ",\"apu_writes\":%" PRIu64 ","
           "\"triggers\":%" PRIu64 ",\"audio_energy\":%" PRIu64 ",\"peak\":%u,"
           "\"double_speed\":%s,\"boot_rom_finished\":%s,\"pc\":%u,"
           "\"pitch_snapshots\":%" PRIu64 ",\"pitch_step_ticks\":%" PRIu64 ",\"pitch_origin_tick\":%" PRIu64 ","
           "\"screen_ticks_8mhz\":%" PRIu64 ",\"screen_frame\":%" PRIu64 "}\n",
           SAMEBOY_REVISION, model_name, GB_is_cgb_in_cgb_mode(gb) ? "true" : "false", header_cgb,
           force_cgb ? "true" : "false", rate, filter, filter, limit_frames, vblank_buttons ? "true" : "false",
           c.frames, gb->absolute_debugger_ticks,
           initial_ticks, c.boot_boundary_tick, c.samples, c.first_nonzero_sample, c.first_trigger_tick,
           c.writes, c.apu_writes, c.triggers, c.audio_energy, c.peak,
           gb->cgb_double_speed ? "true" : "false", gb->boot_rom_finished ? "true" : "false", gb->pc,
           c.pitch_snapshots, c.pitch_step, c.pitch_origin, c.screen_tick, c.screen_frame);
    GB_dealloc(gb);
    return 0;
}
