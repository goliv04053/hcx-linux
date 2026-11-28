// Mac Toolbox data types from types.json (Multiversal Interfaces), shared by the scripts.
import ghidra.program.model.data.*;
import ghidra.program.model.listing.Program;
import com.google.gson.*;
import java.nio.file.*;
import java.util.*;

public class MacTypes {
    JsonObject typedefs, aggs;
    DataTypeManager dtm;
    Map<String, DataType> done = new HashMap<>();
    CategoryPath cat = new CategoryPath("/Mac");

    MacTypes(Program p, String path) throws Exception {
        JsonObject j = JsonParser.parseString(new String(Files.readAllBytes(Paths.get(path)))).getAsJsonObject();
        typedefs = j.getAsJsonObject("typedefs");
        aggs = j.getAsJsonObject("aggs");
        dtm = p.getDataTypeManager();
    }

    static String norm(String t) {
        return t.replace("const ", "").replace("struct ", "").replace("volatile ", "").trim();
    }

    DataType base(String t) {
        switch (t) {
            case "int8_t": case "char": return CharDataType.dataType;
            case "uint8_t": case "unsigned char": return ByteDataType.dataType;
            case "bool": return BooleanDataType.dataType;
            case "int16_t": case "short": return ShortDataType.dataType;
            case "uint16_t": return UnsignedShortDataType.dataType;
            case "int32_t": case "long": case "int": return IntegerDataType.dataType;
            case "uint32_t": case "unsigned": return UnsignedIntegerDataType.dataType;
            case "float": return FloatDataType.dataType;
            case "double": return DoubleDataType.dataType;
            case "void": return VoidDataType.dataType;
        }
        return null;
    }

    DataType get(String t) { return get(t, 0); }

    DataType get(String t, int depth) {
        t = norm(t);
        if (depth > 30) return Undefined4DataType.dataType;
        if (done.containsKey(t)) return done.get(t);
        DataType r;
        if (t.endsWith("*")) {
            DataType in = get(t.substring(0, t.length() - 1), depth + 1);
            r = new PointerDataType(in instanceof VoidDataType ? null : in, 4, dtm);
        } else if (t.matches(".*\\[\\d+\\]$")) {
            int i = t.lastIndexOf('[');
            DataType el = get(t.substring(0, i), depth + 1);
            int n = Integer.parseInt(t.substring(i + 1, t.length() - 1));
            r = el.getLength() > 0 ? new ArrayDataType(el, n, el.getLength(), dtm) : Undefined4DataType.dataType;
        } else if (t.contains("(")) {
            r = new PointerDataType(null, 4, dtm);
        } else if ((r = base(t)) != null) {
        } else if (typedefs.has(t)) {
            done.put(t, Undefined4DataType.dataType);   // a cycle stops here
            DataType in = get(typedefs.get(t).getAsString(), depth + 1);
            r = dtm.addDataType(new TypedefDataType(cat, t, in, dtm), DataTypeConflictHandler.REPLACE_HANDLER);
        } else if (aggs.has(t)) {
            JsonObject a = aggs.getAsJsonObject(t);
            boolean union = a.get("kind").getAsString().equals("union");
            int size = a.get("size").getAsInt();
            Composite c = union ? new UnionDataType(cat, t, dtm) : new StructureDataType(cat, t, size, dtm);
            c = (Composite) dtm.addDataType(c, DataTypeConflictHandler.REPLACE_HANDLER);
            done.put(t, c);
            for (JsonElement e : a.getAsJsonArray("members")) {
                JsonObject m = e.getAsJsonObject();
                DataType mt = get(m.get("type").getAsString(), depth + 1);
                int off = m.get("offset").getAsInt();
                int ms = m.get("size").getAsInt();
                if (mt.getLength() != ms || mt.getLength() <= 0) mt = ms > 0 ? new ArrayDataType(ByteDataType.dataType, ms, 1, dtm) : null;
                if (mt == null) continue;
                try {
                    if (union) ((Union) c).add(mt, m.get("name").getAsString(), null);
                    else if (off + mt.getLength() <= size)
                        ((Structure) c).replaceAtOffset(off, mt, mt.getLength(), m.get("name").getAsString(), null);
                } catch (Exception ex) { /* overlapping member: left out */ }
            }
            r = c;
        } else {
            r = Undefined4DataType.dataType;
        }
        done.put(t, r);
        return r;
    }
}
