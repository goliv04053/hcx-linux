/* The glue between Musashi and HCX: a flat memory for the emulated Mac,
 * and a stop when the CPU reaches one of the "stub" addresses where HCX
 * handles a Toolbox trap, a HyperCard callback or the end of an XCMD.
 *
 * Part of HCX for Linux (GPL-3.0 or later). Musashi is MIT licensed.
 */
#include <stdlib.h>
#include <string.h>
#include "musashi/m68k.h"

#define MEM_SIZE 0x01000000u          /* 16 MB, 24-bit addresses like a Mac in 24-bit mode */
#define ADDR(a) ((a) & (MEM_SIZE - 1))

static unsigned char *mem;
static unsigned int stub_lo, stub_hi;  /* [lo, hi): reaching these stops the CPU */
static unsigned int stop_pc;
static int stopped;
static unsigned long long icount;
static unsigned int history[1024];       /* the last instructions run, for debugging crashes */

unsigned char *hcx_mem(void) { return mem; }
unsigned int hcx_mem_size(void) { return MEM_SIZE; }

int hcx_init(void)
{
    if (!mem) {
        mem = calloc(MEM_SIZE, 1);
        if (!mem) return -1;
        m68k_init();
    }
    memset(mem, 0, MEM_SIZE);
    m68k_set_cpu_type(M68K_CPU_TYPE_68020);
    return 0;
}

void hcx_set_stubs(unsigned int lo, unsigned int hi) { stub_lo = lo; stub_hi = hi; }

void hcx_instr_hook(unsigned int pc)
{
    history[icount & 1023] = pc;
    icount++;
    pc = ADDR(pc);
    if (pc >= stub_lo && pc < stub_hi) {
        stop_pc = pc;
        stopped = 1;
        m68k_end_timeslice();
    }
}

/* Runs until a stub is reached (returns its address) or until about
 * `cycles` cycles have run (returns 0). */
unsigned int hcx_run(int cycles)
{
    stopped = 0;
    m68k_execute(cycles);
    return stopped ? stop_pc : 0;
}

unsigned long long hcx_icount(void) { return icount; }

/* The addresses of the last 1024 instructions run, oldest first. */
void hcx_history(unsigned int *out)
{
    for (int i = 0; i < 1024; i++)
        out[i] = history[(icount + i) & 1023];
}

/* Memory access for Musashi (big-endian). */
unsigned int m68k_read_memory_8(unsigned int a) { return mem[ADDR(a)]; }
unsigned int m68k_read_memory_16(unsigned int a)
{
    a = ADDR(a);
    return (mem[a] << 8) | mem[ADDR(a + 1)];
}
unsigned int m68k_read_memory_32(unsigned int a)
{
    return (m68k_read_memory_16(a) << 16) | m68k_read_memory_16(a + 2);
}
void m68k_write_memory_8(unsigned int a, unsigned int v) { mem[ADDR(a)] = v; }
void m68k_write_memory_16(unsigned int a, unsigned int v)
{
    mem[ADDR(a)] = v >> 8;
    mem[ADDR(a + 1)] = v;
}
void m68k_write_memory_32(unsigned int a, unsigned int v)
{
    m68k_write_memory_16(a, v >> 16);
    m68k_write_memory_16(a + 2, v & 0xFFFF);
}
unsigned int m68k_read_disassembler_8(unsigned int a) { return m68k_read_memory_8(a); }
unsigned int m68k_read_disassembler_16(unsigned int a) { return m68k_read_memory_16(a); }
unsigned int m68k_read_disassembler_32(unsigned int a) { return m68k_read_memory_32(a); }

/* Disassembly, for debugging. */
unsigned int hcx_disasm(unsigned int pc, char *buf)
{
    return m68k_disassemble(buf, pc, M68K_CPU_TYPE_68020);
}
