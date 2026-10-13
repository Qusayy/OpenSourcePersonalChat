import { Component, type ReactNode } from "react";
import type { CardData, CardKind } from "@/lib/types";
import { Position } from "./Figure";
import { Failure, KINDS } from "./kinds";

/** What the tool's step reported, shown in the card's caption. Absent when a
 *  card is replayed from a stored conversation that did not keep it. */
export interface ToolCardMeta {
  label?: string;
  ms?: number | null;
}

export interface ToolCardProps {
  /** Which figure to draw. An unknown kind draws nothing. */
  card: CardKind;
  /** The tool's result. Every field is treated as possibly missing. */
  data: CardData;
  /** Position among the cards of one answer; staggers the entrance. */
  index?: number;
  meta?: ToolCardMeta | null;
}

/**
 * One tool result, drawn as a figure.
 *
 * The data is whatever a tool sent, so each card is drawn inside its own
 * boundary: a payload that breaks one figure is replaced by a line saying so,
 * and the cards beside it, the answer under it and the rest of the page carry
 * on.
 */
export function ToolCard({ card, data, index = 0, meta = null }: ToolCardProps) {
  const Kind = Object.hasOwn(KINDS, card) ? KINDS[card] : null;
  if (!Kind) return null;
  return (
    <Position value={index}>
      <Guard kind={card}>
        <Kind data={data || {}} meta={meta} />
      </Guard>
    </Position>
  );
}

/** Catches a card that could not be drawn and says so in its place. */
class Guard extends Component<{ kind: string; children: ReactNode }, { failed: boolean }> {
  override state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  override componentDidCatch(error: unknown) {
    console.warn("[cards] failed to render", this.props.kind, error);
  }

  override render() {
    if (this.state.failed) {
      // Not marked with `data-card`: it is no longer the card that was asked for.
      return <Failure data={{ message: `Could not render the ${this.props.kind} card.` }} bare />;
    }
    return this.props.children;
  }
}
