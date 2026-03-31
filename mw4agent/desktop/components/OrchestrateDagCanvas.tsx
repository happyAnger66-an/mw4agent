"use client";

import {
  createContext,
  memo,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  addEdge,
  useNodesState,
  useEdgesState,
  useReactFlow,
  Handle,
  Position,
  type Connection,
  type Node,
  type NodeProps,
} from "@xyflow/react";

import type { ListedAgent, OrchestrateDagSpec } from "@/lib/gateway";
import {
  DAG_AGENT_NODE_TYPE,
  type DagNodeData,
  flowToSpec,
  specToFlow,
} from "@/lib/orchestrateDagFlow";

const ListedAgentsCtx = createContext<ListedAgent[]>([]);

function useListedAgents(): ListedAgent[] {
  return useContext(ListedAgentsCtx);
}

function newNodeId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return `n-${crypto.randomUUID().slice(0, 8)}`;
  }
  return `n-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;
}

const DagAgentNode = memo(function DagAgentNode(props: NodeProps<Node<DagNodeData>>) {
  const { id, data, selected } = props;
  const { setNodes } = useReactFlow();
  const listedAgents = useListedAgents();
  const agents = listedAgents.length ? listedAgents : [{ agentId: "main" } as ListedAgent];

  const onAgentChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const v = e.target.value;
    setNodes((nds) =>
      nds.map((n) => (n.id === id ? { ...n, data: { ...n.data, agentId: v } } : n))
    );
  };

  const onTitleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const v = e.target.value;
    setNodes((nds) =>
      nds.map((n) => (n.id === id ? { ...n, data: { ...n.data, title: v } } : n))
    );
  };

  return (
    <div
      className={`rounded-lg border px-2 py-1.5 min-w-[168px] max-w-[240px] shadow-sm ${
        selected
          ? "border-[var(--accent)] bg-[var(--panel)]"
          : "border-[var(--border)] bg-[var(--bg)]"
      }`}
    >
      <Handle type="target" position={Position.Top} className="!bg-zinc-400 !w-2 !h-2" />
      <div className="text-[9px] text-[var(--muted)] font-mono truncate mb-1">{id}</div>
      <input
        className="w-full text-[11px] border border-[var(--border)] rounded px-1.5 py-0.5 mb-1 bg-[var(--panel)] text-[var(--text)]"
        value={data.title}
        onChange={onTitleChange}
        placeholder="title"
      />
      <select
        className="w-full text-[10px] font-mono border border-[var(--border)] rounded px-1 py-0.5 bg-[var(--panel)] text-[var(--text)]"
        value={(data.agentId || "main").trim() || "main"}
        onChange={onAgentChange}
      >
        {agents.map((a) => (
          <option key={a.agentId} value={a.agentId}>
            {a.agentId}
          </option>
        ))}
      </select>
      <Handle type="source" position={Position.Bottom} className="!bg-zinc-400 !w-2 !h-2" />
    </div>
  );
});

const nodeTypes = { [DAG_AGENT_NODE_TYPE]: DagAgentNode };

function FitViewOnMount() {
  const { fitView } = useReactFlow();
  useEffect(() => {
    const id = requestAnimationFrame(() => {
      fitView({ padding: 0.15 });
    });
    return () => cancelAnimationFrame(id);
  }, [fitView]);
  return null;
}

export type OrchestrateDagCanvasProps = {
  initialSpec: OrchestrateDagSpec;
  listedAgents: ListedAgent[];
  onSpecChange: (spec: OrchestrateDagSpec) => void;
  t: (key: string, params?: Record<string, string | number | undefined>) => string;
};

export function OrchestrateDagCanvas({
  initialSpec,
  listedAgents,
  onSpecChange,
  t,
}: OrchestrateDagCanvasProps) {
  const { nodes: seedNodes, edges: seedEdges } = specToFlow(initialSpec);

  const [nodes, setNodes, onNodesChange] = useNodesState(seedNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(seedEdges);

  const [parallelism, setParallelism] = useState(() =>
    Math.max(1, Math.min(32, Math.floor(initialSpec.parallelism ?? 4)))
  );

  const skipEmitRef = useRef(true);
  useEffect(() => {
    if (skipEmitRef.current) {
      skipEmitRef.current = false;
      return;
    }
    const timer = window.setTimeout(() => {
      onSpecChange(flowToSpec(nodes as Node<DagNodeData>[], edges, parallelism));
    }, 280);
    return () => window.clearTimeout(timer);
  }, [nodes, edges, parallelism, onSpecChange]);

  const onConnect = useCallback(
    (c: Connection) => {
      setEdges((eds) => {
        if (!c.source || !c.target || c.source === c.target) return eds;
        const dup = eds.some((e) => e.source === c.source && e.target === c.target);
        if (dup) return eds;
        return addEdge({ ...c, type: "smoothstep" }, eds);
      });
    },
    [setEdges]
  );

  const addNode = useCallback(() => {
    const nid = newNodeId();
    setNodes((nds) => [
      ...nds,
      {
        id: nid,
        type: DAG_AGENT_NODE_TYPE,
        position: { x: 80 + (nds.length % 5) * 24, y: 60 + (nds.length % 3) * 18 },
        data: { agentId: "main", title: "", listedAgents: [] },
      },
    ]);
  }, [setNodes]);

  const onParallelismInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    const n = Number(e.target.value);
    if (!Number.isFinite(n)) return;
    setParallelism(Math.max(1, Math.min(32, Math.floor(n))));
  };

  return (
    <ListedAgentsCtx.Provider value={listedAgents}>
      <div className="flex flex-col gap-2 w-full min-h-0">
        <div className="flex flex-wrap items-center gap-2 text-[10px] text-[var(--muted)]">
          <button
            type="button"
            className="rounded-lg border border-[var(--border)] bg-[var(--panel)] px-2 py-1 text-[11px] text-[var(--text)] hover:opacity-90"
            onClick={addNode}
          >
            {t("orchestrateDagAddNode")}
          </button>
          <label className="inline-flex items-center gap-1 font-mono">
            <span>{t("orchestrateDagParallelism")}</span>
            <input
              type="number"
              min={1}
              max={32}
              className="w-14 rounded border border-[var(--border)] bg-[var(--panel)] px-1 py-0.5 text-[11px] text-[var(--text)]"
              value={parallelism}
              onChange={onParallelismInput}
            />
          </label>
          <span className="flex-1 min-w-[120px]">{t("orchestrateDagEdgeHint")}</span>
        </div>
        <div className="h-[min(52vh,400px)] w-full min-h-[280px] rounded-lg border border-[var(--border)] bg-[var(--panel)] overflow-hidden">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            nodeTypes={nodeTypes}
            fitView
            deleteKeyCode={["Backspace", "Delete"]}
            minZoom={0.35}
            maxZoom={1.5}
          >
            <FitViewOnMount />
            <Background gap={16} size={1} />
            <Controls />
            <MiniMap
              className="!bg-[var(--bg)] !border !border-[var(--border)]"
              maskColor="rgba(0,0,0,0.12)"
            />
          </ReactFlow>
        </div>
        <p className="text-[10px] text-[var(--muted)] leading-relaxed">{t("orchestrateDagCanvasHint")}</p>
      </div>
    </ListedAgentsCtx.Provider>
  );
}
