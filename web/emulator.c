// SPDX-License-Identifier: GPL-2.0-or-later
// Browser-only startup capture. User ROM/save data never leave WASM memory.
#include <Core/gb.h>
#include <emscripten/emscripten.h>
#include <inttypes.h>
#include <stdio.h>
#include <string.h>

#include "sameboy_boot_data.h"

#ifndef SAMEBOY_REVISION
#define SAMEBOY_REVISION "unknown"
#endif

#define FRAME_TICKS UINT64_C(140448)
#define API EMSCRIPTEN_KEEPALIVE

static GB_gameboy_t *machine;
static uint32_t pixels[160 * 144];
static uint8_t io_snapshot[256];
static char metadata[1536];
static char error_message[256];
static unsigned selected_model;
static unsigned next_button;
static uint64_t requested_ticks;
static uint64_t boot_boundary_tick;
static uint64_t last_lcd_enable_tick;
static bool automatic_buttons;
static bool lcd_enabled;
static bool snapshot_ready;

static const struct {
    unsigned frame;
    GB_key_t key;
    bool down;
} startup_buttons[] = {
    {180, GB_KEY_B, true}, {188, GB_KEY_B, false},
    {196, GB_KEY_START, true}, {204, GB_KEY_START, false},
};

static void log_message(GB_gameboy_t *gb, const char *message, GB_log_attributes_t attributes)
{
    (void)gb;
    (void)attributes;
    snprintf(error_message, sizeof(error_message), "%s", message);
}

static uint32_t encode_rgb(GB_gameboy_t *gb, uint8_t r, uint8_t g, uint8_t b)
{
    (void)gb;
    return (uint32_t)r << 16 | (uint32_t)g << 8 | b;
}

static void discard_audio(GB_gameboy_t *gb, GB_sample_t *sample)
{
    (void)gb;
    (void)sample;
}

static bool observe_write(GB_gameboy_t *gb, uint16_t address, uint8_t value)
{
    if (address == 0xff40) {
        bool enabled = (value & 0x80) != 0;
        if (enabled && !lcd_enabled) last_lcd_enable_tick = gb->absolute_debugger_ticks;
        lcd_enabled = enabled;
    }
    return true;
}

API void lsdj_destroy(void)
{
    if (machine) GB_dealloc(machine);
    machine = NULL;
    snapshot_ready = false;
}

// Model 0=DMG-B, 1=CGB-E. Both buffers are copied by SameBoy before return.
// Return 0 on success, -1 for an invalid argument, -2 for allocation failure,
// or -3 when the cartridge cannot hold the working 32 KiB song.
API int lsdj_init(const uint8_t *rom, unsigned rom_size,
                  const uint8_t *save, unsigned save_size, unsigned model)
{
    lsdj_destroy();
    error_message[0] = 0;
    if (!rom || rom_size < 0x150 || rom_size > 0x800000 || !save ||
        (save_size != 0x10000 && save_size != 0x20000) || model > 1) {
        snprintf(error_message, sizeof(error_message), "Invalid ROM, save, or hardware model");
        return -1;
    }
    GB_random_set_enabled(false);
    machine = GB_alloc();
    if (!machine) {
        snprintf(error_message, sizeof(error_message), "Cannot allocate emulator");
        return -2;
    }
    GB_init(machine, model ? GB_MODEL_CGB_E : GB_MODEL_DMG_B);
    selected_model = model;
    next_button = 0;
    requested_ticks = 0;
    boot_boundary_tick = 0;
    last_lcd_enable_tick = 0;
    automatic_buttons = true;
    lcd_enabled = false;
    snapshot_ready = false;
    GB_set_log_callback(machine, log_message);
    GB_set_turbo_mode(machine, true, true);
    GB_set_rtc_mode(machine, GB_RTC_MODE_ACCURATE);
    GB_debugger_set_disabled(machine, true);
    GB_set_emulate_joypad_bouncing(machine, false);
    GB_set_sample_rate(machine, 48000);
    GB_set_highpass_filter_mode(machine, GB_HIGHPASS_ACCURATE);
    GB_set_interference_volume(machine, 0);
    GB_apu_set_sample_callback(machine, discard_audio);
    GB_set_border_mode(machine, GB_BORDER_NEVER);
    GB_set_pixels_output(machine, pixels);
    GB_set_rgb_encode_callback(machine, encode_rgb);
    GB_set_write_memory_callback(machine, observe_write);
    GB_load_rom_from_buffer(machine, rom, rom_size);
    if (machine->mbc_ram_size < 0x8000) {
        snprintf(error_message, sizeof(error_message), "Cartridge has no 32 KiB working-song memory");
        lsdj_destroy();
        return -3;
    }
    if (model) GB_load_boot_rom_from_buffer(machine, sameboy_cgb_boot, sizeof(sameboy_cgb_boot));
    else GB_load_boot_rom_from_buffer(machine, sameboy_dmg_boot, sizeof(sameboy_dmg_boot));
    GB_load_battery_from_buffer(machine, save, save_size);
    return 0;
}

API void lsdj_set_auto_buttons(unsigned enabled)
{
    automatic_buttons = enabled != 0;
}

// Bit order: right,left,up,down,A,B,select,start. Manual input is useful for
// diagnostics; disable automatic input first to replace the startup schedule.
API void lsdj_set_buttons(unsigned mask)
{
    if (machine) GB_set_key_mask(machine, (GB_key_mask_t)(mask & 255));
}

// Fixed-clock frames match host/sameboy_capture.c rather than VBlank counts.
// Each call is bounded; workers can run e.g. 8 frames then yield for progress.
// Return 1 immediately before bank 2:$5fe7, 0 while running, -1 for misuse.
API int lsdj_run_frames(unsigned frames)
{
    if (!machine || frames > 600) return -1;
    if (snapshot_ready) return 1;
    requested_ticks += FRAME_TICKS * frames;
    while (machine->absolute_debugger_ticks < requested_ticks) {
        if (machine->boot_rom_finished && machine->pc == 0x5fe7 && machine->mbc_rom_bank == 2) {
            snapshot_ready = true;
            return 1;
        }
        if (machine->boot_rom_finished && machine->pc == 0x100 && !boot_boundary_tick)
            boot_boundary_tick = machine->absolute_debugger_ticks;
        while (automatic_buttons && next_button < sizeof(startup_buttons) / sizeof(startup_buttons[0]) &&
               FRAME_TICKS * startup_buttons[next_button].frame <= machine->absolute_debugger_ticks) {
            GB_set_key_state(machine, startup_buttons[next_button].key, startup_buttons[next_button].down);
            ++next_button;
        }
        GB_run(machine);
    }
    // Do not require another worker turn when the breakpoint is exactly on
    // the requested frame boundary; the initializer has not executed yet.
    if (machine->boot_rom_finished && machine->pc == 0x5fe7 && machine->mbc_rom_bank == 2)
        snapshot_ready = true;
    return snapshot_ready ? 1 : 0;
}

API int lsdj_read(unsigned address)
{
    return machine && address <= 0xffff ? GB_safe_read_memory(machine, address) : -1;
}

API int lsdj_pc(void) { return machine ? machine->pc : -1; }
API int lsdj_rom_bank(void) { return machine ? (machine->pc < 0x4000 ? 0 : machine->mbc_rom_bank) : -1; }
API double lsdj_ticks(void) { return machine ? (double)machine->absolute_debugger_ticks : 0; }
API const char *lsdj_error(void) { return error_message; }

// Snapshot views are valid until the next run/init/destroy. WRAM includes
// the first physical 8 KiB, exactly the bytes used by the native ROM builder.
API const uint8_t *lsdj_snapshot_wram(void) { return machine ? machine->ram : NULL; }
API const uint8_t *lsdj_snapshot_hram(void) { return machine ? machine->hram : NULL; }
API const uint8_t *lsdj_snapshot_song(void) { return machine ? machine->mbc_ram : NULL; }

// First 128 bytes are raw SameBoy IO registers, matching native .io.bin.
// The remaining 128 bytes contain HRAM followed by IE, for a full FF00 page.
API const uint8_t *lsdj_snapshot_io(void)
{
    if (!machine) return NULL;
    memcpy(io_snapshot, machine->io_registers, 128);
    memcpy(io_snapshot + 128, machine->hram, 127);
    io_snapshot[255] = machine->interrupt_enable;
    return io_snapshot;
}

API const char *lsdj_metadata(void)
{
    if (!machine) return "null";
    snprintf(metadata, sizeof(metadata),
        "{\"model\":\"%s\",\"tick\":%" PRIu64 ",\"pc\":%u,\"rom_bank\":%u,"
        "\"ram_bank\":%u,\"wram_bank\":%u,\"af\":%u,\"bc\":%u,\"de\":%u,\"hl\":%u,"
        "\"sp\":%u,\"ie\":%u,\"ime\":%u,\"double_speed\":%s,\"boot_rom_finished\":%s,"
        "\"boot_boundary_tick\":%" PRIu64 ",\"last_lcd_enable_tick\":%" PRIu64 ","
        "\"tima\":%u,\"tma\":%u,\"tac\":%u,\"if\":%u,\"snapshot_ready\":%s,"
        "\"wram_bytes\":8192,\"hram_bytes\":127,\"song_bytes\":32768,\"io_bytes\":256,"
        "\"sameboy_revision\":\"%s\"}",
        selected_model ? "CGB-E" : "DMG-B", machine->absolute_debugger_ticks,
        machine->pc, machine->mbc_rom_bank, machine->mbc_ram_bank, machine->cgb_ram_bank,
        machine->af, machine->bc, machine->de, machine->hl, machine->sp,
        machine->interrupt_enable, machine->ime, machine->cgb_double_speed ? "true" : "false",
        machine->boot_rom_finished ? "true" : "false", boot_boundary_tick, last_lcd_enable_tick,
        machine->io_registers[GB_IO_TIMA], machine->io_registers[GB_IO_TMA],
        machine->io_registers[GB_IO_TAC], machine->io_registers[GB_IO_IF],
        snapshot_ready ? "true" : "false", SAMEBOY_REVISION);
    return metadata;
}
