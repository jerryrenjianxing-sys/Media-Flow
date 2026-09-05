// Loaded only in the owned OpenCode process. No imports or downloaded packages.
export const MediaFlowContext = async () => ({
  "tool.execute.before": async (input, output) => {
    if (!input.tool.startsWith("mediaflow_")) return;
    const config = JSON.parse(process.env.OPENCODE_CONFIG_CONTENT || "{}");
    const env = config.mcp?.mediaflow?.environment;
    if (!env) throw new Error("MediaFlow tool context unavailable");
    // Never accept a session identity supplied by the model.
    delete output.args._mediaflow_context;
    const response = await fetch(env.MEDIAFLOW_AGENT_BRIDGE_URL + "/tools/context", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: "Bearer " + env.MEDIAFLOW_AGENT_BRIDGE_TOKEN },
      signal: AbortSignal.timeout(10000),
      body: JSON.stringify({ session_id: input.sessionID, call_id: input.callID,
        name: input.tool.slice("mediaflow_".length), arguments: output.args }),
    });
    if (!response.ok) throw new Error("MediaFlow session stopped or tool context unavailable");
    const result = await response.json();
    output.args._mediaflow_context = result.token;
  },
});
