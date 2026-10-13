import type { ComponentProps } from "react";
import { cx } from "@/lib/cx";

/** What holds an answer's cards: the figures stack with their own top rules,
 *  and this closes the last one underneath. With no cards in it, it is not
 *  there at all. */
export const CARD_STACK = "mb-4 border-b border-rule empty:hidden";

/** The cards of one answer, between the route line and the answer's text. */
export function CardStack({ className, ...rest }: ComponentProps<"div">) {
  return <div className={cx(CARD_STACK, className)} {...rest} />;
}
