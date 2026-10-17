import java.util.Map;

import ghidra.app.plugin.core.analysis.AutoAnalysisManager;
import ghidra.app.script.GhidraScript;

/** Print every auto-analyzer name known for the imported program and its current on/off state. */
public class ListAnalyzers extends GhidraScript {
    @Override
    public void run() throws Exception {
        Map<String, String> options = getCurrentAnalysisOptionsAndValues(currentProgram);
        AutoAnalysisManager manager = AutoAnalysisManager.getAnalysisManager(currentProgram);
        println("REA-ANALYZERS-BEGIN count=" + options.size());
        for (Map.Entry<String, String> entry : options.entrySet()) {
            String name = entry.getKey();
            boolean registered = manager.getAnalyzer(name) != null;
            println("REA-ANALYZER\t" + (registered ? "registered" : "options-only") + "\t"
                + entry.getValue() + "\t" + name);
        }
        println("REA-ANALYZERS-END");
    }
}
