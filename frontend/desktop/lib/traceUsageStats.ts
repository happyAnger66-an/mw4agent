import type { OrchTraceEvent } from "@/lib/gateway";

export type TraceUsageStats = {
  toolCounts: Array<{ name: string; count: number }>;
  skillCounts: Array<{ name: string; count: number }>;
};

function getPayloadObject(ev: OrchTraceEvent): Record<string, unknown> | null {
  const p = ev.payload;
  if (p && typeof p === "object" && !Array.isArray(p)) return p as Record<string, unknown>;
  return null;
}

function unescapeJsonStrFragment(s: string): string {
  return s
    .replace(/\\n/g, "\n")
    .replace(/\\r/g, "\r")
    .replace(/\\t/g, "\t")
    .replace(/\\"/g, '"')
    .replace(/\\\\/g, "\\");
}

/** Best-effort path from truncated JSON preview (tool_start.arguments_preview). */
function extractPathFromPreview(preview: string): string {
  const t = preview.trim();
  if (!t) return "";
  try {
    const o = JSON.parse(t) as Record<string, unknown>;
    for (const k of ["path", "file_path", "target_file"]) {
      const v = o[k];
      if (typeof v === "string" && v.trim()) return v.trim();
    }
  } catch {
    /* truncated JSON — try regex */
  }
  const m =
    t.match(/"path"\s*:\s*"((?:\\.|[^"\\])*)"/) ||
    t.match(/"file_path"\s*:\s*"((?:\\.|[^"\\])*)"/);
  if (m?.[1]) return unescapeJsonStrFragment(m[1]).trim();
  return "";
}

/**
 * If read/write targets a path under workspace skills dirs, return skill id (first path segment after skills/).
 */
export function extractSkillIdFromFsTool(toolName: string, argumentsPreview: string): string | null {
  const tn = (toolName || "").trim().toLowerCase();
  if (tn !== "read" && tn !== "write") return null;
  const pathStr = extractPathFromPreview(argumentsPreview);
  if (!pathStr) return null;
  const norm = pathStr.replace(/\\/g, "/");
  const re = /(?:^|\/)\.agents\/skills\/([^/]+)|(?:^|\/)skills\/([^/]+)/i;
  const m = norm.match(re);
  const seg = (m?.[1] || m?.[2] || "").trim();
  if (!seg) return null;
  try {
    return decodeURIComponent(seg);
  } catch {
    return seg;
  }
}

function sortCounts(m: Map<string, number>): Array<{ name: string; count: number }> {
  return [...m.entries()]
    .map(([name, count]) => ({ name, count }))
    .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
}

/**
 * Aggregates from persisted trace rows in the given window (e.g. time-filtered).
 * Tools: one count per ``tool_start`` (actual invocation).
 * Skills: heuristics — ``read``/``write`` whose path includes ``skills/<id>/`` or ``.agents/skills/<id>/``.
 */
export function computeTraceUsageStats(events: OrchTraceEvent[]): TraceUsageStats {
  const tools = new Map<string, number>();
  const skills = new Map<string, number>();

  for (const ev of events) {
    if (String(ev.type ?? "") !== "tool_start") continue;
    const pl = getPayloadObject(ev);
    if (!pl) continue;
    const name = String(pl.tool_name ?? "").trim();
    if (!name) continue;
    tools.set(name, (tools.get(name) || 0) + 1);
    const prev = typeof pl.arguments_preview === "string" ? pl.arguments_preview : "";
    const sk = extractSkillIdFromFsTool(name, prev);
    if (sk) skills.set(sk, (skills.get(sk) || 0) + 1);
  }

  return {
    toolCounts: sortCounts(tools),
    skillCounts: sortCounts(skills),
  };
}
