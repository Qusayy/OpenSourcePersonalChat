import type { ComponentProps } from "react";
import { cx } from "@/lib/cx";

/* The four controls every page draws from.

   Sizes here are literals, not spacing tokens, where the design gives a
   control a size of its own (36px tall, 32px square); and 44px on a touch
   screen, as explicit minimums rather than padding arithmetic: padding that
   happens to add up to 44px on one font stack quietly falls short on another.

   Each variant is a complete list. Two utilities for the same property on one
   element are not resolved by the order they are written in, so nothing here
   is "the outline button, but filled": what differs between variants is kept
   out of what they share. */

const BOXED = cx(
  "inline-flex items-center justify-center gap-2 min-h-[36px] px-4",
  "border border-ink rounded-1 text-2 font-semibold no-underline",
  "cursor-pointer transition-[background,color,border-color] duration-(--dur-1)",
  "active:translate-y-px motion-reduce:active:translate-none",
  "disabled:border-rule disabled:text-ink-2 disabled:cursor-not-allowed disabled:translate-none",
  "coarse:min-h-[44px]",
);

const OUTLINE = cx(BOXED, "bg-transparent text-ink hover:bg-sheet-2");
const PRIMARY = cx(BOXED, "bg-ink text-sheet hover:bg-ink-hover disabled:bg-rule");

/* A button that reads as a word. Used where a box would add chrome and the
   position already says it is a control: the top bar, a footer, a caption. */
const TEXT = cx(
  "appearance-none inline-flex items-center gap-2 m-0 py-1 px-2",
  "border-0 rounded-1 bg-transparent font-medium no-underline",
  "cursor-pointer whitespace-nowrap transition-[background,color] duration-(--dur-1)",
  "hover:bg-sheet-2 disabled:text-ink-2 disabled:cursor-not-allowed",
  "coarse:min-h-[44px] coarse:px-3",
);

const ICON = cx(
  "appearance-none grid place-items-center size-[32px] p-0",
  "border-0 rounded-1 bg-transparent text-ink-2",
  "cursor-pointer transition-[background,color] duration-(--dur-1)",
  "hover:bg-sheet-2 hover:text-ink",
  "coarse:size-[44px]",
);

export type ButtonVariant = "outline" | "primary" | "text" | "icon";

export interface ButtonStyle {
  variant?: ButtonVariant;
  /** A text button in the second ink until it is pointed at. */
  quiet?: boolean;
  /** A text button at the smallest size. */
  small?: boolean;
}

/** The classes for a control, for when the element is not a `<button>` (a link
 *  that looks like one). */
export function buttonClass({ variant = "outline", quiet, small }: ButtonStyle = {}): string {
  if (variant === "primary") return PRIMARY;
  if (variant === "icon") return ICON;
  if (variant === "text") {
    return cx(TEXT, quiet ? "text-ink-2 hover:text-ink" : "text-ink", small ? "text-1" : "text-2");
  }
  return OUTLINE;
}

export type ButtonProps = ComponentProps<"button"> & ButtonStyle;

export function Button({
  variant,
  quiet,
  small,
  className,
  type = "button",
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      className={cx(buttonClass({ variant, quiet, small }), className)}
      {...rest}
    />
  );
}
