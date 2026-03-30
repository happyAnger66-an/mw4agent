/**
 * 根据 orchestrate.get 返回的 status 推导「发送区是否应处于忙碌/禁用」。
 *
 * 约定：仅 `running` 表示编排任务进行中；`idle` / `error` / `aborted` / 空字符串等均视为可再次发送。
 * 空字符串在历史上曾导致旧逻辑 `if (r.status && …)` 永远不 `setBusy(false)`，使按钮永久禁用。
 */
export function busyFromOrchestrateStatus(status: unknown): boolean {
  const s = String(status ?? "").trim().toLowerCase();
  return s === "running";
}
