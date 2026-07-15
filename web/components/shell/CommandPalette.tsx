"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { AnimatePresence, motion } from "framer-motion";
import { ACTIONS, CUSTOMERS, CUSTOMER_BY_ID, RULES } from "@/lib/fixtures";
import { usdShort } from "@/lib/format";
import { EffectGlyph } from "../Effect";
import { IconAudit, IconQueue, IconRules, IconSearch, IconUser } from "../icons";
import styles from "./palette.module.css";

type Item = {
  id: string;
  group: "Go to" | "Actions" | "Customers" | "Rules";
  label: string;
  hint?: string;
  href: string;
  render?: React.ReactNode;
  terms: string;
};

/**
 * ⌘K / "/" palette.
 *
 * Searches the three things an operator actually goes looking for by id: an
 * action, a customer, a rule. Arrow keys move, Enter opens, Escape closes, and
 * the list is a real listbox so a screen reader announces the active option
 * rather than silently teleporting.
 */
export function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [cursor, setCursor] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);

  const items = useMemo<Item[]>(() => {
    const nav: Item[] = [
      { id: "n1", group: "Go to", label: "Approval queue", href: "/queue", terms: "queue approvals pending", render: <IconQueue size={14} /> },
      { id: "n2", group: "Go to", label: "Audit trail", href: "/audit", terms: "audit log trail history", render: <IconAudit size={14} /> },
      { id: "n3", group: "Go to", label: "Policy rules", href: "/rules", terms: "rules policy code", render: <IconRules size={14} /> },
      { id: "n4", group: "Go to", label: "Guard on/off", href: "/ablation", terms: "ablation guard off bypass contrast", render: <IconRules size={14} /> },
    ];
    const acts: Item[] = ACTIONS.map((a) => {
      const c = CUSTOMER_BY_ID[a.customerId];
      return {
        id: a.id,
        group: "Actions" as const,
        label: a.id,
        hint: `${a.tool} · ${c?.company ?? ""} · ${usdShort(a.valueUsd)}`,
        href: `/queue/${a.id}`,
        render: <EffectGlyph effect={a.effect} />,
        terms: `${a.id} ${a.tool} ${c?.name ?? ""} ${c?.company ?? ""} ${a.state} ${a.effect}`.toLowerCase(),
      };
    });
    const custs: Item[] = CUSTOMERS.map((c) => ({
      id: c.id,
      group: "Customers" as const,
      label: c.name,
      hint: `${c.company} · ${c.id}`,
      href: `/queue?customer=${c.id}`,
      render: <IconUser size={14} />,
      terms: `${c.name} ${c.company} ${c.email} ${c.id}`.toLowerCase(),
    }));
    const rules: Item[] = RULES.map((r) => ({
      id: r.id,
      group: "Rules" as const,
      label: r.id,
      hint: r.description,
      href: `/rules#${r.id}`,
      render: <IconRules size={14} />,
      terms: `${r.id} ${r.title} ${r.description}`.toLowerCase(),
    }));
    return [...nav, ...acts, ...custs, ...rules];
  }, []);

  const results = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return items.filter((i) => i.group === "Go to").concat(items.filter((i) => i.group === "Actions").slice(0, 6));
    return items.filter((i) => i.terms.includes(needle) || i.label.toLowerCase().includes(needle)).slice(0, 24);
  }, [q, items]);

  useEffect(() => {
    setCursor(0);
  }, [q]);

  useEffect(() => {
    if (open) {
      setQ("");
      // rAF: the input does not exist until framer has mounted the panel.
      const r = requestAnimationFrame(() => inputRef.current?.focus());
      return () => cancelAnimationFrame(r);
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      } else if (e.key === "ArrowDown" || (e.ctrlKey && e.key === "n")) {
        e.preventDefault();
        setCursor((c) => Math.min(c + 1, results.length - 1));
      } else if (e.key === "ArrowUp" || (e.ctrlKey && e.key === "p")) {
        e.preventDefault();
        setCursor((c) => Math.max(c - 1, 0));
      } else if (e.key === "Enter") {
        e.preventDefault();
        const target = results[cursor];
        if (target) {
          router.push(target.href);
          onClose();
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, results, cursor, router, onClose]);

  useEffect(() => {
    listRef.current?.querySelector('[data-active="true"]')?.scrollIntoView({ block: "nearest" });
  }, [cursor]);

  let lastGroup = "";

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className={styles.overlay}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.13 }}
          onClick={onClose}
        >
          <motion.div
            className={styles.panel}
            role="dialog"
            aria-modal="true"
            aria-label="Command palette"
            initial={{ opacity: 0, y: -8, scale: 0.985 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -6, scale: 0.99 }}
            transition={{ duration: 0.17, ease: [0.16, 1, 0.3, 1] }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className={styles.inputRow}>
              <IconSearch size={15} />
              <input
                ref={inputRef}
                className={styles.input}
                placeholder="Search actions, customers, rules…"
                value={q}
                onChange={(e) => setQ(e.target.value)}
                role="combobox"
                aria-expanded
                aria-controls="palette-list"
                aria-activedescendant={results[cursor] ? `opt-${results[cursor].id}` : undefined}
                autoComplete="off"
                spellCheck={false}
              />
              <kbd className={styles.esc}>esc</kbd>
            </div>

            {results.length === 0 ? (
              <div className={styles.empty}>
                <p>
                  Nothing matches <span className="mono">&ldquo;{q}&rdquo;</span>
                </p>
                <span>Try an action id, a company, or a rule name.</span>
              </div>
            ) : (
              <ul className={styles.list} id="palette-list" role="listbox" ref={listRef}>
                {results.map((item, i) => {
                  const header = item.group !== lastGroup ? item.group : null;
                  lastGroup = item.group;
                  return (
                    <li key={item.id}>
                      {header && <div className={styles.group}>{header}</div>}
                      <button
                        type="button"
                        id={`opt-${item.id}`}
                        role="option"
                        aria-selected={i === cursor}
                        data-active={i === cursor}
                        className={styles.opt}
                        onMouseMove={() => setCursor(i)}
                        onClick={() => {
                          router.push(item.href);
                          onClose();
                        }}
                      >
                        <span className={styles.optIcon}>{item.render}</span>
                        <span className={styles.optLabel}>{item.label}</span>
                        {item.hint && <span className={styles.optHint}>{item.hint}</span>}
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}

            <footer className={styles.foot}>
              <span>
                <kbd>↑</kbd>
                <kbd>↓</kbd> move
              </span>
              <span>
                <kbd>↵</kbd> open
              </span>
              <span>
                <kbd>esc</kbd> close
              </span>
            </footer>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
