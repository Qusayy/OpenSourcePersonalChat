import type { CardData, CardKind } from "@/lib/types";

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

/** Placeholder: the nine cards are built in their own stream. */
export function ToolCard({ card }: ToolCardProps) {
  return <figure data-card={card} />;
}
