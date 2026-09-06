import { API } from "../components/workbench-types";
import { fetchLocalApi } from "./local-api";

export async function agentRequest<T>(path: string, body?: object): Promise<T> {
  try {
    const response = await fetchLocalApi(`${API}/api/agent/${path}`, body ? {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    } : { cache: "no-store" }, 20_000);
    const result = await response.json();
    if (!response.ok) {
      const message = result.user_message || result.error || (response.status === 404 ? "请求的会话或操作不存在，请刷新原会话检查" : "对话服务暂时不可用，请刷新状态");
      throw new AgentRequestError(message, response.status, result.reason_code || "request_rejected");
    }
    return result as T;
  } catch (error) {
    if (error instanceof TypeError) throw new Error("无法连接本机对话服务，请检查后台是否运行后点击刷新状态");
    if (error instanceof SyntaxError) throw new Error("对话服务返回格式异常，请刷新状态；不会重复发送消息");
    throw error;
  }
}

export class AgentRequestError extends Error {
  constructor(message:string, public status:number, public reasonCode:string){super(message);}
}

export type AuthPrompt = {
  type: string; key: string; message: string; placeholder?: string;
  options?: { label: string; value: string; hint?: string }[];
  when?: { key: string; op: string; value: string };
};
export type AuthMethod = { type: string; label: string; prompts?: AuthPrompt[] };
export type AgentProvider = {
  id: string; name: string; connected: boolean; models: { id: string; name: string }[];
  auth_methods?: AuthMethod[];
  auth_state?: { source: string; revision: number; test_status: string };
};
