// Names and types the Mac trap stubs that ATRAP instructions call
// (0xF00000 + (trap & 0xFFF) * 4), from traps.json (Multiversal Interfaces).
// Pascal traps take their arguments on the stack, pushed left to right,
// with the result's space pushed first; the trap pops the arguments.
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.*;
import ghidra.program.model.data.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.SourceType;
import ghidra.program.model.mem.*;
import java.nio.file.*;
import java.util.*;
import com.google.gson.*;

public class MacTraps extends GhidraScript {
    MacTypes types;

    DataType base(int size, String t) {
        if (types != null) {
            DataType d = types.get(t);
            if (d.getLength() == size) return d;
            if (size == 4 && d.getLength() > 4) return new PointerDataType(d, 4, currentProgram.getDataTypeManager());
        }
        return base0(size, t);
    }

    DataType base0(int size, String t) {
        DataType b;
        boolean ptr = t.endsWith("*") || t.endsWith("Ptr") || t.endsWith("Handle") || t.equals("Handle");
        if (size == 1) b = t.equals("Boolean") ? BooleanDataType.dataType : ByteDataType.dataType;
        else if (size == 2) b = ShortDataType.dataType;
        else b = ptr ? PointerDataType.dataType : IntegerDataType.dataType;
        String n = t.replaceAll("[^A-Za-z0-9_]", "_");
        if (n.isEmpty() || n.equals("int") || n.equals("short")) return b;
        return new TypedefDataType(n, b);
    }

    @Override
    public void run() throws Exception {
        String path = getScriptArgs().length > 0 ? getScriptArgs()[0] : System.getenv().getOrDefault("HCX_GHIDRA_DATA", "/opt/ghx") + "/traps.json";
        types = new MacTypes(currentProgram, System.getenv().getOrDefault("HCX_GHIDRA_DATA", "/opt/ghx") + "/types.json");
        JsonObject traps = JsonParser.parseString(new String(Files.readAllBytes(Paths.get(path)))).getAsJsonObject();
        Memory mem = currentProgram.getMemory();
        Address start = toAddr(0xF00000);
        if (mem.getBlock(start) == null)
            mem.createInitializedBlock("TRAPS", start, 0x4000, (byte) 0, monitor, false);
        FunctionManager fm = currentProgram.getFunctionManager();
        Set<String> used = new HashSet<>();
        // stubs only for the trap words that occur in the code
        Set<Integer> slots = new TreeSet<>();
        for (MemoryBlock b : mem.getBlocks()) {
            if (b.getStart().getOffset() >= 0xF00000L || !b.isInitialized()) continue;
            byte[] buf = new byte[(int) b.getSize()];
            b.getBytes(b.getStart(), buf);
            for (int i = 0; i + 1 < buf.length; i += 2)
                if ((buf[i] & 0xF0) == 0xA0) slots.add(((buf[i] & 0x0F) << 8) | (buf[i + 1] & 0xFF));
        }
        for (int s : slots) {
            int key = (s & 0x800) != 0 ? 0xA800 | (s & 0x3FF) : 0xA000 | (s & 0xFF);
            Address a = toAddr(0xF00000 + s * 4);
            JsonObject e = null;
            int[] tries = (s & 0x800) != 0 ? new int[] {0xA000 | s, 0xA800 | (s & 0x3FF)}
                : new int[] {0xA000 | s, 0xA000 | (s & 0x1FF), 0xA000 | (s & 0xFF), 0xA100 | (s & 0xFF)};
            for (int t : tries) {
                if (traps.has(String.format("%04X", t))) { e = traps.getAsJsonObject(String.format("%04X", t)); key = t; break; }
            }
            String name = e != null ? e.get("name").getAsString() : String.format("ATrap_%04X", 0xA000 | s);
            if (!used.add(name)) name = name + String.format("_%04X", 0xA000 | s);
            Function f = fm.getFunctionAt(a);
            if (f == null) f = fm.createFunction(name, a, new AddressSet(a), SourceType.USER_DEFINED);
            boolean os = (s & 0x800) == 0;
            String cc = os ? "mac_os" : "mac_pascal";
            if (e != null && e.has("ret") && !e.get("ret").isJsonNull()) {
                JsonElement rr = e.getAsJsonObject("ret").get("reg");
                if (rr != null && !rr.isJsonNull() && rr.getAsString().startsWith("A0")) cc = "mac_os_a0";
            }
            if (e == null) { f.setCallingConvention(cc); continue; }
            if (e.has("sels")) {   // a dispatcher: the selector in D0 (or on the stack, left untyped)
                String sel = e.get("sel").isJsonNull() ? "" : e.get("sel").getAsString();
                if (sel.startsWith("D0")) {
                    // most register dispatchers take a parameter block in A0
                    List<Variable> ps = new ArrayList<>();
                    ps.add(new ParameterImpl("pb", e.get("name").getAsString().equals("FSDispatch") ? types.get("HParamBlockRec*") : PointerDataType.dataType, currentProgram.getRegister("A0"), currentProgram));
                    ps.add(new ParameterImpl("selector", IntegerDataType.dataType, currentProgram.getRegister("D0"), currentProgram));
                    f.setCustomVariableStorage(true);
                    f.updateFunction(cc, new ReturnParameterImpl(ShortDataType.dataType, currentProgram.getRegister("D0w"), currentProgram),
                        ps, Function.FunctionUpdateType.CUSTOM_STORAGE, true, SourceType.USER_DEFINED);
                    f.setStackPurgeSize(0);
                }
                continue;
            }
            JsonArray args = e.getAsJsonArray("args");
            int total = 0;
            for (JsonElement x : args) {
                JsonObject o = x.getAsJsonObject();
                if (o.get("reg").isJsonNull()) total += Math.max(2, o.get("size").getAsInt());
            }
            List<Variable> ps = new ArrayList<>();
            int off = total;
            for (JsonElement x : args) {
                JsonObject o = x.getAsJsonObject();
                int size = o.get("size").getAsInt();
                DataType dt = base(size, o.get("t").getAsString());
                String pn = o.get("n").getAsString();
                if (!o.get("reg").isJsonNull()) {
                    String r = o.get("reg").getAsString();
                    if (size == 2) r = r + "w"; else if (size == 1) r = r + "b";
                    ps.add(new ParameterImpl(pn, dt, currentProgram.getRegister(r), currentProgram));
                } else {
                    off -= Math.max(2, size);
                    ps.add(new ParameterImpl(pn, dt, new VariableStorage(currentProgram, off, size), currentProgram));
                }
            }
            ReturnParameterImpl ret;
            if (e.get("ret").isJsonNull()) {
                ret = new ReturnParameterImpl(VoidDataType.dataType, VariableStorage.VOID_STORAGE, currentProgram);
            } else {
                JsonObject r = e.getAsJsonObject("ret");
                int size = r.get("size").getAsInt();
                DataType dt = base(size, r.get("t").getAsString());
                if (!r.get("reg").isJsonNull()) {
                    String rr = r.get("reg").getAsString();
                    if (size == 2) rr = rr + "w"; else if (size == 1) rr = rr + "b";
                    ret = new ReturnParameterImpl(dt, currentProgram.getRegister(rr), currentProgram);
                } else {
                    ret = new ReturnParameterImpl(dt, new VariableStorage(currentProgram, total, size), currentProgram);
                }
            }
            f.setCustomVariableStorage(true);
            try {
                f.updateFunction(cc, ret, ps, Function.FunctionUpdateType.CUSTOM_STORAGE, true, SourceType.USER_DEFINED);
            } catch (Exception ex) {
                printerr(name + ": " + ex);
            }
            f.setStackPurgeSize(total);
        }
    }
}
