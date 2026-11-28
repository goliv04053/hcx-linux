// Decompiles a raw XCMD/XFCN code resource: entry at offset 0, every function found.
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.address.*;
import ghidra.app.cmd.function.CreateFunctionCmd;
import ghidra.app.cmd.disassemble.DisassembleCommand;
import java.io.*;
import java.util.*;
import java.nio.file.*;
import com.google.gson.*;
import ghidra.program.model.data.*;
import ghidra.program.model.symbol.SourceType;

public class DecompileAll extends GhidraScript {
    @Override
    public void run() throws Exception {
        String out = getScriptArgs().length > 0 ? getScriptArgs()[0] : "/tmp/out.c";
        Address entry = toAddr(0);
        new DisassembleCommand(entry, null, true).applyTo(currentProgram, monitor);
        if (getFunctionAt(entry) == null)
            new CreateFunctionCmd(entry).applyTo(currentProgram, monitor);
        analyzeAll(currentProgram);
        // code the calls did not reach: functions starting with LINK A6 or MOVEM.L regs,-(SP)
        ghidra.program.model.mem.Memory mem = currentProgram.getMemory();
        ghidra.program.model.mem.MemoryBlock blk = mem.getBlock(entry);
        byte[] code = new byte[(int) blk.getSize()];
        blk.getBytes(blk.getStart(), code);
        for (int pass = 0; pass < 3; pass++) {
            int added = 0;
            for (int i = 0; i + 4 <= code.length; i += 2) {
                int w = ((code[i] & 0xFF) << 8) | (code[i + 1] & 0xFF);
                if (w != 0x4E56 && w != 0x48E7) continue;
                Address a = toAddr(i);
                if (currentProgram.getListing().getInstructionContaining(a) != null) continue;
                if (currentProgram.getListing().getDefinedDataContaining(a) != null) continue;
                // only after the end of other code (a return or MacsBug name), or at a word boundary of free bytes
                new DisassembleCommand(a, null, true).applyTo(currentProgram, monitor);
                if (getFunctionAt(a) == null) new CreateFunctionCmd(a).applyTo(currentProgram, monitor);
                added++;
            }
            if (added == 0) break;
            analyzeChanges(currentProgram);
        }
        // MacsBug names after a function's last instruction (RTS, RTD, JMP (A0))
        int named = 0;
        for (int i = 0; i + 3 < code.length; i += 2) {
            int w = ((code[i] & 0xFF) << 8) | (code[i + 1] & 0xFF);
            int nameAt = i + 2;
            if (w == 0x4E74) nameAt = i + 4; // rtd #n
            else if (w != 0x4E75 && w != 0x4ED0 && w != 0x4ED1) continue;
            if (nameAt >= code.length) continue;
            int b = code[nameAt] & 0xFF;
            int len, start;
            if (b >= 0x81 && b <= 0x9F) { len = b & 0x7F; start = nameAt + 1; }
            else if (b == 0x80 && nameAt + 1 < code.length) { len = code[nameAt + 1] & 0xFF; start = nameAt + 2; }
            else continue;
            if (len < 2 || start + len > code.length) continue;
            StringBuilder sb = new StringBuilder();
            boolean ok = true;
            for (int k = 0; k < len; k++) {
                char c = (char) (code[start + k] & 0xFF);
                if (!(Character.isLetterOrDigit(c) || c == '_' || c == '%' || c == '.' || c == ' ')) { ok = false; break; }
                sb.append(c);
            }
            if (!ok) continue;
            Function f = getFunctionContaining(toAddr(i));
            if (f == null) continue;
            String nm = sb.toString().trim().replace(' ', '_').replace('%', '_');
            try { f.setName(nm, SourceType.IMPORTED); named++; } catch (Exception ex) { }
        }
        println("MacsBug names: " + named);
        // Pascal-style returns: move.l (sp)+,An ... jmp (An) is a return
        int fixed = 0;
        Map<Function, Integer> purge = new HashMap<>();
        for (Instruction ins : currentProgram.getListing().getInstructions(true)) {
            String m = ins.getMnemonicString().toLowerCase();
            if (!m.equals("jmp") || ins.getNumOperands() != 1) continue;
            String op = ins.getDefaultOperandRepresentation(0);
            if (!op.matches("A[0-6]")) continue;
            String reg = op;
            Instruction p = ins;
            for (int k = 0; k < 6 && p != null; k++) {
                p = p.getPrevious();
                if (p == null) break;
                String t = p.toString().toLowerCase();
                if (t.startsWith("movea.l (sp)+," + reg.toLowerCase()) || t.startsWith("move.l (sp)+," + reg.toLowerCase())) {
                    ins.setFlowOverride(ghidra.program.model.listing.FlowOverride.RETURN);
                    // the arguments popped between the return address and the jump
                    int pop = 0;
                    for (Instruction q = p.getNext(); q != null && q != ins; q = q.getNext()) {
                        java.util.regex.Matcher mm = java.util.regex.Pattern.compile("^(?:lea \\((0x[0-9a-f]+|\\d+),sp\\),sp|addq?\\.[wl] #(0x[0-9a-f]+|\\d+),sp|adda\\.[wl] #(0x[0-9a-f]+|\\d+),sp)").matcher(q.toString().toLowerCase());
                        if (mm.find()) {
                            String v = mm.group(1) != null ? mm.group(1) : mm.group(2) != null ? mm.group(2) : mm.group(3);
                            pop += v.startsWith("0x") ? Integer.parseInt(v.substring(2), 16) : Integer.parseInt(v);
                        }
                    }
                    Function pf = getFunctionContaining(ins.getAddress());
                    if (pf != null) purge.put(pf, pop + 4);
                    fixed++;
                    break;
                }
            }
        }
        if (fixed > 0) analyzeChanges(currentProgram);
        for (Map.Entry<Function, Integer> e : purge.entrySet()) {
            Function pf = getFunctionAt(e.getKey().getEntryPoint());
            if (pf != null) pf.setStackPurgeSize(e.getValue());
        }
        // let the decompiler work out each function's stack parameters
        DecompInterface pd = new DecompInterface();
        pd.openProgram(currentProgram);
        for (Function f : currentProgram.getFunctionManager().getFunctions(true)) {
            if (f.getEntryPoint().getOffset() >= 0xF00000L) continue;
            DecompileResults r = pd.decompileFunction(f, 60, monitor);
            if (r.decompileCompleted())
                ghidra.program.model.pcode.HighFunctionDBUtil.commitParamsToDatabase(r.getHighFunction(), true,
                    ghidra.program.model.pcode.HighFunctionDBUtil.ReturnCommitOption.COMMIT, SourceType.ANALYSIS);
        }
        pd.dispose();
        println("pascal returns: " + fixed);
        MacTypes types = new MacTypes(currentProgram, System.getenv().getOrDefault("HCX_GHIDRA_DATA", "/opt/ghx") + "/types.json");
        // the XCMD's main(XCmdPtr paramPtr)
        Function main = getFunctionAt(entry);
        if (main != null && main.isThunk()) main = main.getThunkedFunction(false);
        if (main != null) {
            main.setName("main", SourceType.USER_DEFINED);
            List<Variable> ps = new ArrayList<>();
            ps.add(new ParameterImpl("paramPtr", types.get("XCmdPtr"), 4, currentProgram));
            main.updateFunction("__stdcall", null, ps, Function.FunctionUpdateType.DYNAMIC_STORAGE_ALL_PARAMS, true, SourceType.USER_DEFINED);
        }
        // HyperCard callback glue: sets paramPtr->request (offset 0x4C) to a constant
        String[] cb = new String[60];
        String names = "1 SendCardMessage 2 EvalExpr 3 StringLength 4 StringMatch 5 SendHCMessage 6 ZeroBytes 7 PasToZero 8 ZeroToPas 9 StrToLong 10 StrToNum 11 StrToBool 12 StrToExt 13 LongToStr 14 NumToStr 15 NumToHex 16 BoolToStr 17 ExtToStr 18 GetGlobal 19 SetGlobal 20 GetFieldByName 21 GetFieldByNum 22 GetFieldByID 23 SetFieldByName 24 SetFieldByNum 25 SetFieldByID 26 StringEqual 27 ReturnToPas 28 ScanToReturn 29 GetMaskAndData 30 ChangedMaskAndData 32 ZeroTermHandle 34 SendHCEvent 36 BeginXSound 37 EndXSound 38 RunHandler 39 ScanToZero 40 GetXResInfo 41 GetFilePath 42 FrontDocWindow 43 PointToStr 44 RectToStr 45 StrToPoint 46 StrToRect 47 GetFieldTE 48 SetFieldTE 53 Notify 54 ShowHCAlert";
        String[] w = names.split(" ");
        for (int i = 0; i + 1 < w.length; i += 2) cb[Integer.parseInt(w[i])] = w[i + 1];
        for (Function f : currentProgram.getFunctionManager().getFunctions(true)) {
            if (f.getEntryPoint().getOffset() >= 0xF00000L || f.getSymbol().getSource() == SourceType.USER_DEFINED || f.getSymbol().getSource() == SourceType.IMPORTED) continue;
            for (Instruction ins : currentProgram.getListing().getInstructions(f.getBody(), true)) {
                String t = ins.toString();
                java.util.regex.Matcher m = java.util.regex.Pattern.compile("^move\\.w #0x([0-9a-f]+),\\(0x4c,A\\d\\)").matcher(t);
                if (m.find()) {
                    int n = Integer.parseInt(m.group(1), 16);
                    if (n < cb.length && cb[n] != null) {
                        try { f.setName("HC_" + cb[n], SourceType.USER_DEFINED); } catch (Exception ex) { }
                    }
                    break;
                }
            }
        }
        // dispatcher selectors: a comment naming the call
        JsonObject traps = JsonParser.parseString(new String(Files.readAllBytes(Paths.get(System.getenv().getOrDefault("HCX_GHIDRA_DATA", "/opt/ghx") + "/traps.json")))).getAsJsonObject();
        for (Instruction ins : currentProgram.getListing().getInstructions(true)) {
            if (!ins.getMnemonicString().equals("ATRAP")) continue;
            int word = ((ins.getByte(0) & 0xFF) << 8) | (ins.getByte(1) & 0xFF);
            int key = (word & 0x800) != 0 ? 0xA800 | (word & 0x3FF) : 0xA000 | (word & 0xFF);
            String k = String.format("%04X", key);
            if (!traps.has(k)) k = String.format("%04X", word);
            if (!traps.has(k) && (word & 0x800) == 0) k = String.format("%04X", 0xA000 | (word & 0x1FF));
            if (!traps.has(k) || !traps.getAsJsonObject(k).has("sels")) continue;
            JsonObject sels = traps.getAsJsonObject(k).getAsJsonObject("sels");
            Instruction p = ins.getPrevious();
            for (int i = 0; i < 4 && p != null; i++, p = p.getPrevious()) {
                java.util.regex.Matcher m = java.util.regex.Pattern.compile("^(?:moveq|move\\.[wl]) #(-?0x[0-9a-f]+|-?\\d+),(D0|-\\(SP\\))").matcher(p.toString());
                if (m.find()) {
                    String v = m.group(1);
                    long n = v.contains("0x") ? Long.parseLong(v.replace("0x", ""), 16) * (v.startsWith("-") ? -1 : 1) : Long.parseLong(v);
                    String sn = sels.has(String.valueOf(n & 0xFF)) ? sels.getAsJsonObject(String.valueOf(n & 0xFF)).get("name").getAsString()
                        : sels.has(String.valueOf(n)) ? sels.getAsJsonObject(String.valueOf(n)).get("name").getAsString() : null;
                    if (sn != null) currentProgram.getListing().setComment(ins.getAddress(), CodeUnit.PRE_COMMENT, sn);
                    break;
                }
            }
        }
        try (PrintWriter pw = new PrintWriter(new FileWriter(out.replaceAll("\\.c$", "") + ".asm"))) {
            for (Instruction ins : currentProgram.getListing().getInstructions(true)) {
                if (ins.getAddress().getOffset() >= 0xF00000L) break;
                Function f = getFunctionAt(ins.getAddress());
                if (f != null) pw.println("\n; ---- " + f.getName());
                String c = currentProgram.getListing().getComment(CodeUnit.PRE_COMMENT, ins.getAddress());
                String extra = "";
                for (ghidra.program.model.symbol.Reference r : ins.getReferencesFrom()) {
                    Function t = getFunctionAt(r.getToAddress());
                    if (t != null && r.getReferenceType().isCall()) extra = "  ; " + t.getName();
                }
                pw.println(String.format("%06x  %-40s%s%s", ins.getAddress().getOffset(), ins.toString(), extra, c != null ? "  ; " + c : ""));
            }
        }
        DecompInterface d = new DecompInterface();
        d.openProgram(currentProgram);
        try (PrintWriter pw = new PrintWriter(new FileWriter(out))) {
            for (Function f : currentProgram.getFunctionManager().getFunctions(true)) {
                if (f.getEntryPoint().getOffset() >= 0xF00000L) continue;   // trap stubs
                DecompileResults r = d.decompileFunction(f, 120, monitor);
                pw.println("// ---- " + f.getName() + " @ " + f.getEntryPoint());
                pw.println(r.decompileCompleted() ? r.getDecompiledFunction().getC() : "// failed: " + r.getErrorMessage());
            }
        }
    }
}
