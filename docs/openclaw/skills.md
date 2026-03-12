# OpenClaw Skills 体系分析

本文总结 OpenClaw 中 **skills（技能）** 的整体设计与使用方式，侧重于与智能体运行、会话状态以及 CLI 之间的关系，便于在 MW4Agent 中做类似能力的复刻或裁剪实现。

---

## 1. Skills 在整体配置中的位置

在 OpenClaw 的主配置类型中，`skills` 是一等公民配置块：

```ts
// src/config/types.openclaw.ts
export type OpenClawConfig = {
  // ...
  skills?: SkillsConfig;
  plugins?: PluginsConfig;
  models?: ModelsConfig;
  agents?: AgentsConfig;
  tools?: ToolsConfig;
  // ...
};
```

这一块（`SkillsConfig`）主要用于：

- 控制技能安装偏好（包管理器 / 是否 prefer brew）；
- 控制技能加载路径、额外目录、是否 watch 等；
- 为 workspace 的 skills 行为提供全局开关和策略。

高层入口在 `src/agents/skills.ts` 中统一 re-export：

```ts
// src/agents/skills.ts（节选）
export {
  hasBinary,
  isBundledSkillAllowed,
  isConfigPathTruthy,
  resolveBundledAllowlist,
  resolveConfigPath,
  resolveRuntimePlatform,
  resolveSkillConfig,
} from "./skills/config.js";
export {
  applySkillEnvOverrides,
  applySkillEnvOverridesFromSnapshot,
} from "./skills/env-overrides.js";
export type {
  OpenClawSkillMetadata,
  SkillEligibilityContext,
  SkillCommandSpec,
  SkillEntry,
  SkillInstallSpec,
  SkillSnapshot,
  SkillsInstallPreferences,
} from "./skills/types.js";
export {
  buildWorkspaceSkillSnapshot,
  buildWorkspaceSkillsPrompt,
  buildWorkspaceSkillCommandSpecs,
  filterWorkspaceSkillEntries,
  loadWorkspaceSkillEntries,
  resolveSkillsPromptForRun,
  syncSkillsToWorkspace,
} from "./skills/workspace.js";
```

---

## 2. Workspace Skills：目录结构与扫描

OpenClaw 将 skills 视为 **workspace 级别的能力集合**，典型目录布局为：

- `workspaceDir/skills/<skillName>/SKILL.md`
- `workspaceDir/.agents/skills/...`（内部或托管路径）
- 以及配置和插件指定的额外目录：
  - `CONFIG_DIR/skills`
  - `$HOME/.agents/skills`
  - `config.skills.load.extraDirs[]`
  - 插件附带的 skill 目录（`resolvePluginSkillDirs`）

技能 watcher 在 `src/agents/skills/refresh.ts` 中实现：

```ts
// src/agents/skills/refresh.ts（节选）
function resolveWatchPaths(workspaceDir: string, config?: OpenClawConfig): string[] {
  const paths: string[] = [];
  if (workspaceDir.trim()) {
    paths.push(path.join(workspaceDir, "skills"));
    paths.push(path.join(workspaceDir, ".agents", "skills"));
  }
  paths.push(path.join(CONFIG_DIR, "skills"));
  paths.push(path.join(os.homedir(), ".agents", "skills"));
  const extraDirsRaw = config?.skills?.load?.extraDirs ?? [];
  // ...
}
```

Watcher 只关心 `SKILL.md` 文件，以避免遍历和监听巨大数据树：

```ts
// src/agents/skills/refresh.ts（节选）
function resolveWatchTargets(workspaceDir: string, config?: OpenClawConfig): string[] {
  const targets = new Set<string>();
  for (const root of resolveWatchPaths(workspaceDir, config)) {
    const globRoot = toWatchGlobRoot(root);
    targets.add(`${globRoot}/SKILL.md`);
    targets.add(`${globRoot}/*/SKILL.md`);
  }
  return Array.from(targets).toSorted();
}
```

当 watcher 发现 `SKILL.md` 变化时，会 bump 一个全局或 per-workspace 的 **skillsSnapshot 版本号**：

```ts
export function bumpSkillsSnapshotVersion(params?: { workspaceDir?: string; ... }): number {
  // ...
  if (params?.workspaceDir) {
    const current = workspaceVersions.get(params.workspaceDir) ?? 0;
    const next = bumpVersion(current);
    workspaceVersions.set(params.workspaceDir, next);
    emit({ workspaceDir: params.workspaceDir, reason, changedPath });
    return next;
  }
  globalVersion = bumpVersion(globalVersion);
  emit({ reason, changedPath });
  return globalVersion;
}

export function getSkillsSnapshotVersion(workspaceDir?: string): number {
  if (!workspaceDir) {
    return globalVersion;
  }
  const local = workspaceVersions.get(workspaceDir) ?? 0;
  return Math.max(globalVersion, local);
}
```

---

## 3. SkillSnapshot：如何挂到 Session 上

技能信息最终会挂到 **会话（SessionEntry）** 上，以便：

- 让 LLM prompt 能看到“当前 workspace 具备哪些 skills”；
- 支持后续工具/命令根据 `skillsSnapshot` 做行为调整；
- 支持跨 turn 复用 / 刷新技能快照。

会话类型中有对应字段：

```ts
// src/config/sessions/types.ts（节选）
export type SessionEntry = {
  // ...
  skillsSnapshot?: SessionSkillSnapshot;
};

export type SessionSkillSnapshot = {
  prompt?: string;
  skills?: Array<{
    name: string;
    primaryEnv?: string;
    requiredEnv?: string[];
  }>;
  skillFilter?: string[];
  resolvedSkills?: unknown;
  version?: number;
};
```

生成与刷新逻辑在 `src/auto-reply/reply/session-updates.ts` 的 `ensureSkillSnapshot(...)` 中：

```ts
// src/auto-reply/reply/session-updates.ts（节选）
export async function ensureSkillSnapshot(params: {
  sessionEntry?: SessionEntry;
  sessionStore?: Record<string, SessionEntry>;
  sessionKey?: string;
  storePath?: string;
  sessionId?: string;
  isFirstTurnInSession: boolean;
  workspaceDir: string;
  cfg: OpenClawConfig;
  skillFilter?: string[];
}) { 
  // ...
  const remoteEligibility = getRemoteSkillEligibility();
  const snapshotVersion = getSkillsSnapshotVersion(workspaceDir);
  ensureSkillsWatcher({ workspaceDir, config: cfg });
  const shouldRefreshSnapshot =
    snapshotVersion > 0 && (nextEntry?.skillsSnapshot?.version ?? 0) < snapshotVersion;

  if (isFirstTurnInSession && sessionStore && sessionKey) {
    const current = nextEntry ??
      sessionStore[sessionKey] ?? {
        sessionId: sessionId ?? crypto.randomUUID(),
        updatedAt: Date.now(),
      };
    const skillSnapshot =
      isFirstTurnInSession || !current.skillsSnapshot || shouldRefreshSnapshot
        ? buildWorkspaceSkillSnapshot(workspaceDir, {
            config: cfg,
            skillFilter,
            eligibility: { remote: remoteEligibility },
            snapshotVersion,
          })
        : current.skillsSnapshot;
    nextEntry = {
      ...current,
      // ...
      skillsSnapshot: skillSnapshot,
    };
    sessionStore[sessionKey] = { ...sessionStore[sessionKey], ...nextEntry };
    // 持久化到会话文件
    if (storePath) {
      await updateSessionStore(storePath, (store) => {
        store[sessionKey] = { ...store[sessionKey], ...nextEntry };
      });
    }
    systemSent = true;
  }
  // 非首 Turn 时也会根据 snapshotVersion 刷新 skillsSnapshot
}
```

`buildWorkspaceSkillSnapshot` 则负责从实际 skill 目录与配置中构建一个结构化快照：

```ts
// src/agents/skills/workspace.ts（节选）
export function buildWorkspaceSkillSnapshot(
  workspaceDir: string,
  opts?: WorkspaceSkillBuildOptions & { snapshotVersion?: number },
): SkillSnapshot {
  const { eligible, prompt, resolvedSkills } = resolveWorkspaceSkillPromptState(workspaceDir, opts);
  const skillFilter = normalizeSkillFilter(opts?.skillFilter);
  return {
    prompt,
    skills: eligible.map((entry) => ({
      name: entry.skill.name,
      primaryEnv: entry.metadata?.primaryEnv,
      requiredEnv: entry.metadata?.requires?.env?.slice(),
    })),
    ...(skillFilter === undefined ? {} : { skillFilter }),
    resolvedSkills,
    version: opts?.snapshotVersion,
  };
}
```

---

## 4. Skills 在 LLM Prompt 中的使用

在真正调用 LLM 前，OpenClaw 会把 skills 信息编织进系统提示中，典型链路：

1. `ensureSkillSnapshot(...)` 为当前会话生成 / 刷新 `skillsSnapshot`；
2. Auto-reply / Agent runner 在构建最终 prompt 时调用：

   ```ts
   // src/agents/skills/workspace.ts（节选）
   export function resolveSkillsPromptForRun(params: {
     skillsSnapshot?: SkillSnapshot;
     entries?: SkillEntry[];
     config?: OpenClawConfig;
     workspaceDir: string;
   }): string {
     const snapshotPrompt = params.skillsSnapshot?.prompt?.trim();
     if (snapshotPrompt) {
       return snapshotPrompt;
     }
     if (params.entries && params.entries.length > 0) {
       const prompt = buildWorkspaceSkillsPrompt(params.workspaceDir, {
         entries: params.entries,
         config: params.config,
       });
       return prompt.trim() ? prompt : "";
     }
     return "";
   }
   ```

3. `buildWorkspaceSkillsPrompt` 会格式化一个可读的 skills 列表，并在过长时添加截断提示与审计建议：

   ```ts
   const { skillsForPrompt, truncated } = applySkillsPromptLimits({
     skills: resolvedSkills,
     config: opts?.config,
   });
   const truncationNote = truncated
     ? `⚠️ Skills truncated: included ${skillsForPrompt.length} of ${resolvedSkills.length}. Run \`openclaw skills check\` to audit.`
     : "";
   const prompt = [
     remoteNote,
     truncationNote,
     formatSkillsForPrompt(compactSkillPaths(skillsForPrompt)),
   ]
     .filter(Boolean)
     .join("\n");
   ```

最终，这段 prompt 会被拼到 LLM 的系统提示部分，使模型“知道”当前 workspace 拥有哪些可用技能、技能大致做什么（来自每个 skill 的 `SKILL.md` 元数据）。

---

## 5. Skills 与 CLI 的结合

OpenClaw 提供了一个完整的 `openclaw skills` 子命令，用于：

- 扫描并列出当前 workspace 中的 skills；
- 检查技能的安全性和依赖；
- 触发 skills 的安装 / 同步。

入口注册在 `src/cli/program/register.subclis.ts`：

```ts
// src/cli/program/register.subclis.ts（节选）
{
  name: "skills",
  description: "List and inspect available skills",
  hasSubcommands: true,
  register: async (program) => {
    const mod = await import("../skills-cli.js");
    mod.registerSkillsCli(program);
  },
},
```

配合 `skills` CLI，OpenClaw 的 skills 体系形成了一个闭环：

- **配置**：`openclaw.yaml` 中的 `skills` 段落（加载策略 / 安装偏好 / 额外目录）；
- **发现与更新**：`skills` watcher + `bumpSkillsSnapshotVersion`；
- **会话挂载**：`ensureSkillSnapshot` 把 `SkillSnapshot` 写入 `SessionEntry`；
- **提示注入**：`resolveSkillsPromptForRun` / `buildWorkspaceSkillsPrompt` 将技能信息注入 LLM 系统提示；
- **CLI 运维**：`openclaw skills ...` 命令提供可视化与维护入口。

---

## 6. 对 MW4Agent 的启发

从 OpenClaw 的实现可以抽出几条可复用的设计要点：

1. **Skill = Workspace 能力声明**：通过 `SKILL.md` + 元数据描述，不强绑具体运行逻辑；
2. **SkillSnapshot 与 Session 绑定**：会话是技能快照的天然挂载点，便于跨 turn 复用与刷新；
3. **Watcher + Versioning**：通过文件 watcher + 版本号机制，避免每次都全量扫描 skills；
4. **Prompt 作为集成点**：skills 与 LLM 的衔接点就是“系统提示中的技能列表”，无需修改模型调用接口；
5. **CLI 与配置闭环**：用 CLI 提供 `skills` 维度的可观测性与运维能力，避免 skills 变成“黑箱目录”。

在 MW4Agent 里，如果要逐步引入 skills，可以先实现：

- 简化版的 `SkillSnapshot`（放在 `SessionEntry` 上）；
- 一个从单一 `skills/` 目录扫描 `SKILL.md` 的最小实现；
- 一个把 skills 信息拼到 LLM system prompt 的 helper；
- 后续再考虑 watcher、CLI、安装/安全审计等高级特性。

