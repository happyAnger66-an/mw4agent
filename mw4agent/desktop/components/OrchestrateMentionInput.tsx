"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type KeyboardEvent,
} from "react";
import {
  getMentionQueryAtCursor,
  insertMentionParticipant,
} from "@/lib/orchestrateMention";

const MAX_SUGGESTIONS = 20;

type OrchestrateMentionInputProps = {
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  /** When true, blocks Enter-to-submit only; input stays editable (e.g. compose next message while orchestration runs). */
  busy?: boolean;
  placeholder: string;
  participants: string[];
  /** Shown under the field when non-empty (e.g. @ syntax hint). */
  hintBelow?: string | null;
  noMatchLabel: string;
};

export function OrchestrateMentionInput({
  value,
  onChange,
  onSubmit,
  busy = false,
  placeholder,
  participants,
  hintBelow,
  noMatchLabel,
}: OrchestrateMentionInputProps) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);
  const [mentionOpen, setMentionOpen] = useState(false);
  const [mentionFiltered, setMentionFiltered] = useState<string[]>([]);
  const [mentionIndex, setMentionIndex] = useState(0);
  /** Inclusive start, exclusive end — slice to replace is [start, end) */
  const mentionRangeRef = useRef<{ start: number; end: number } | null>(null);

  const uniqParticipants = useMemo(
    () => [...new Set(participants.map((p) => p.trim()).filter(Boolean))],
    [participants]
  );

  const refreshMention = useCallback(
    (val: string, cursor: number) => {
      if (!uniqParticipants.length) {
        setMentionOpen(false);
        mentionRangeRef.current = null;
        return;
      }
      const qi = getMentionQueryAtCursor(val, cursor);
      if (!qi) {
        setMentionOpen(false);
        mentionRangeRef.current = null;
        return;
      }
      const q = qi.query.toLowerCase();
      const filtered =
        q === ""
          ? [...uniqParticipants].slice(0, MAX_SUGGESTIONS)
          : uniqParticipants
              .filter((p) => p.toLowerCase().startsWith(q))
              .slice(0, MAX_SUGGESTIONS);
      mentionRangeRef.current = { start: qi.start, end: qi.end };
      setMentionFiltered(filtered);
      setMentionOpen(true);
      setMentionIndex(0);
    },
    [uniqParticipants]
  );

  const closeMention = useCallback(() => {
    setMentionOpen(false);
    mentionRangeRef.current = null;
  }, []);

  const applyPick = useCallback(
    (id: string) => {
      const range = mentionRangeRef.current;
      if (!range) return;
      const { value: next, caret } = insertMentionParticipant(value, range, id);
      onChange(next);
      closeMention();
      requestAnimationFrame(() => {
        const el = inputRef.current;
        if (el) {
          el.focus();
          el.setSelectionRange(caret, caret);
        }
      });
    },
    [value, onChange, closeMention]
  );

  const handleChange = useCallback(
    (e: ChangeEvent<HTMLInputElement>) => {
      const v = e.target.value;
      onChange(v);
      const c = e.target.selectionStart ?? v.length;
      refreshMention(v, c);
    },
    [onChange, refreshMention]
  );

  const handleSelect = useCallback(() => {
    const el = inputRef.current;
    if (!el) return;
    refreshMention(value, el.selectionStart ?? value.length);
  }, [value, refreshMention]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLInputElement>) => {
      if (mentionOpen && mentionFiltered.length > 0) {
        if (e.key === "ArrowDown") {
          e.preventDefault();
          setMentionIndex((i) =>
            i + 1 >= mentionFiltered.length ? 0 : i + 1
          );
          return;
        }
        if (e.key === "ArrowUp") {
          e.preventDefault();
          setMentionIndex((i) =>
            i <= 0 ? mentionFiltered.length - 1 : i - 1
          );
          return;
        }
        if (e.key === "Enter" || e.key === "Tab") {
          e.preventDefault();
          applyPick(mentionFiltered[mentionIndex]!);
          return;
        }
        if (e.key === "Escape") {
          e.preventDefault();
          closeMention();
          return;
        }
      }
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        if (!busy) onSubmit();
      }
    },
    [applyPick, busy, closeMention, mentionFiltered, mentionIndex, mentionOpen, onSubmit]
  );

  useEffect(() => {
    if (!mentionOpen) return;
    const row = listRef.current?.querySelector<HTMLElement>(
      `[data-mention-idx="${mentionIndex}"]`
    );
    row?.scrollIntoView({ block: "nearest" });
  }, [mentionIndex, mentionOpen]);

  useEffect(() => {
    if (!mentionOpen) return;
    const onDoc = (ev: MouseEvent) => {
      const t = ev.target as Node | null;
      if (!t) return;
      if (inputRef.current?.contains(t)) return;
      if (listRef.current?.contains(t)) return;
      closeMention();
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [mentionOpen, closeMention]);

  useEffect(() => {
    if (!uniqParticipants.length) closeMention();
  }, [uniqParticipants.length, closeMention]);

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-1 relative">
      <input
        ref={inputRef}
        role="combobox"
        className="w-full min-w-0 rounded-lg border border-[var(--border)] bg-[var(--bg)] px-3 py-2 text-sm"
        value={value}
        onChange={handleChange}
        onSelect={handleSelect}
        onKeyUp={handleSelect}
        onClick={handleSelect}
        placeholder={placeholder}
        onKeyDown={handleKeyDown}
        autoComplete="off"
        aria-expanded={mentionOpen}
        aria-controls={mentionOpen ? "orch-mention-suggestions" : undefined}
        aria-autocomplete={mentionOpen ? "list" : undefined}
      />
      {mentionOpen && uniqParticipants.length > 0 ? (
        <div
          ref={listRef}
          id="orch-mention-suggestions"
          role="listbox"
          className="absolute left-0 right-0 bottom-full z-30 mb-1 max-h-48 overflow-y-auto rounded-lg border border-[var(--border)] bg-[var(--panel)] shadow-lg py-1"
        >
          {mentionFiltered.length === 0 ? (
            <div className="px-3 py-2 text-xs text-[var(--muted)]">{noMatchLabel}</div>
          ) : (
            mentionFiltered.map((id, idx) => (
              <button
                key={id}
                type="button"
                role="option"
                data-mention-idx={idx}
                aria-selected={idx === mentionIndex}
                className={`w-full text-left px-3 py-1.5 text-xs font-mono hover:bg-[var(--accent)]/15 ${
                  idx === mentionIndex ? "bg-[var(--accent)]/20" : ""
                }`}
                onMouseDown={(e) => e.preventDefault()}
                onMouseEnter={() => setMentionIndex(idx)}
                onClick={() => applyPick(id)}
              >
                @{id}
              </button>
            ))
          )}
        </div>
      ) : null}
      {hintBelow ? (
        <p className="text-[10px] text-[var(--muted)] px-0.5 leading-snug">{hintBelow}</p>
      ) : null}
    </div>
  );
}
