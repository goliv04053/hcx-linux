/* The same glue as hcxcpu.c, on Dirk W. Hoffmann's Moira core instead of
 * Musashi, to cross-check the CPU when an external behaves oddly:
 *
 *   sh tools/xemu/cpu/build-moira.sh MOIRA_FOLDER
 *   HCX_CPU_LIB=tools/xemu/_hcxmoira.so python3.12 -m tools.xemu.compare ...
 *
 * It exports the functions mac68k.py uses, with Musashi's register numbers.
 * Part of HCX for Linux (GPL-3.0 or later). Moira is MIT licensed.
 */
#include <cstdlib>
#include <cstring>
#include <string>
#include "Moira.h"

using namespace moira;

#define MEM_SIZE 0x01000000u
#define ADDR(a) ((a) & (MEM_SIZE - 1))

static unsigned char *mem;
static unsigned int stub_lo, stub_hi;
static unsigned long long icount;
static unsigned int history[1024];
static unsigned int cur_pc;                 /* the instruction being run (PPC) */

static unsigned int watch_lo, watch_hi;
#define WATCH_MAX 4096
static unsigned int watch_log[WATCH_MAX][4];
static int watch_n;

static void watched(unsigned int a, unsigned int v, unsigned int size)
{
    if (a + size > watch_lo && a < watch_hi) {
        if (watch_n < WATCH_MAX) {
            watch_log[watch_n][0] = cur_pc;
            watch_log[watch_n][1] = a;
            watch_log[watch_n][2] = v;
            watch_log[watch_n][3] = size;
        }
        watch_n++;
    }
}

static inline unsigned int rd16(unsigned int a) { a = ADDR(a); return (mem[a] << 8) | mem[ADDR(a + 1)]; }
static inline void wr16(unsigned int a, unsigned int v) { mem[ADDR(a)] = v >> 8; mem[ADDR(a + 1)] = v; }

class Cpu : public Moira {
public:
    u8 read8(u32 a) const override { return mem[ADDR(a)]; }
    u16 read16(u32 a) const override { return rd16(a); }
    u32 read32(u32 a) const override { return (rd16(a) << 16) | rd16(a + 2); }
    void write8(u32 a, u8 v) const override { if (watch_hi) watched(ADDR(a), v, 1); mem[ADDR(a)] = v; }
    void write16(u32 a, u16 v) const override { if (watch_hi) watched(ADDR(a), v, 2); wr16(a, v); }
    void write32(u32 a, u32 v) const override
    {
        if (watch_hi) watched(ADDR(a), v, 4);
        wr16(a, v >> 16);
        wr16(a + 2, v & 0xFFFF);
    }
};

static Cpu *cpu;

extern "C" {

unsigned char *hcx_mem(void) { return mem; }
unsigned int hcx_mem_size(void) { return MEM_SIZE; }

int hcx_init(void)
{
    if (!mem) {
        mem = (unsigned char *)calloc(MEM_SIZE, 1);
        if (!mem) return -1;
        cpu = new Cpu();
        cpu->setModel(CPUModel::M68020);    /* no FPU, like the Musashi build */
    }
    memset(mem, 0, MEM_SIZE);
    return 0;
}

void hcx_set_stubs(unsigned int lo, unsigned int hi) { stub_lo = lo; stub_hi = hi; }

void m68k_pulse_reset(void) { cpu->reset(); }

/* Runs until a stub is reached (returns its address) or until about
 * `cycles` cycles have run (returns 0). The stub's own instruction is not
 * run; mac68k.py sets the PC before going on. */
unsigned int hcx_run(int cycles)
{
    i64 end = cpu->getClock() + cycles;
    while (cpu->getClock() < end) {
        unsigned int pc = cpu->getPC0();
        history[icount & 1023] = pc;
        icount++;
        pc = ADDR(pc);
        if (pc >= stub_lo && pc < stub_hi)
            return pc;
        cur_pc = pc;
        cpu->execute();
        if (cpu->isHalted())
            return 0xFFFFFF;     /* a double fault: mac68k reports it as a crash */
    }
    return 0;
}

unsigned long long hcx_icount(void) { return icount; }

void hcx_history(unsigned int *out)
{
    for (int i = 0; i < 1024; i++)
        out[i] = history[(icount + i) & 1023];
}

void hcx_watch(unsigned int lo, unsigned int hi) { watch_lo = ADDR(lo); watch_hi = hi ? ADDR(hi) : 0; watch_n = 0; }

int hcx_watch_log(unsigned int *out, int max)
{
    int n = watch_n < max ? watch_n : max;
    n = n < WATCH_MAX ? n : WATCH_MAX;
    memcpy(out, watch_log, n * sizeof watch_log[0]);
    return watch_n;
}

/* Musashi's numbering: D0-D7, A0-A7, PC, SR, SP, USP, ISP, MSP, ..., PPC (29). */
unsigned int m68k_get_reg(void *, int n)
{
    if (n < 8) return cpu->getD(n);
    if (n < 16) return cpu->getA(n - 8);
    switch (n) {
    case 16: return cpu->getPC0();
    case 17: return cpu->getSR();
    case 18: return cpu->getSP();
    case 19: return cpu->getUSP();
    case 20: return cpu->getISP();
    case 21: return cpu->getMSP();
    case 24: return cpu->getVBR();
    case 29: return cur_pc;
    }
    return 0;
}

void m68k_set_reg(int n, unsigned int v)
{
    if (n < 8) cpu->setD(n, v);
    else if (n < 16) cpu->setA(n - 8, v);
    else switch (n) {
    case 16: cpu->jump(v); break;
    case 17: cpu->setSR(v); break;
    case 18: cpu->setSP(v); break;
    case 19: cpu->setUSP(v); break;
    case 20: cpu->setISP(v); break;
    case 21: cpu->setMSP(v); break;
    case 24: cpu->setVBR(v); break;
    }
}

unsigned int hcx_disasm(unsigned int pc, char *buf)
{
    int len = 2;
    std::string s = cpu->disassemble("%i", pc, &len);
    strncpy(buf, s.c_str(), 99);
    buf[99] = 0;
    return len;
}

}
