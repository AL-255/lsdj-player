// Local ROM analysis; does not distribute the supplied LSDj executable.
#include <Core/gb.h>
#include <Core/sm83_cpu.h>
#include <stdio.h>
#include <stdlib.h>
static void log_line(GB_gameboy_t *gb, const char *s, GB_log_attributes_t a) { (void)gb; (void)a; fputs(s, stdout); }
int main(int argc, char **argv) {
    if (argc != 5) return 2;
    GB_gameboy_t gb;
    GB_init(&gb, GB_MODEL_CGB_E);
    GB_set_log_callback(&gb, log_line);
    if (GB_load_rom(&gb, argv[1])) return 1;
    gb.boot_rom_finished = true;
    unsigned bank = strtoul(argv[2], NULL, 0);
    GB_write_memory(&gb, 0x2000, bank & 255);
    GB_write_memory(&gb, 0x3000, bank >> 8);
    GB_cpu_disassemble(&gb, strtoul(argv[3], NULL, 0), strtoul(argv[4], NULL, 0));
    GB_free(&gb);
    return 0;
}
