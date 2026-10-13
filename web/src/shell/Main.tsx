import type { ComponentProps } from "react";
import { cx } from "@/lib/cx";

/**
 * The sheet between the top bar and the status line: the middle row of the
 * page grid. Every page renders exactly one. It can take focus (the skip link
 * and a change of page both send focus here) but draws no ring for it: it is a
 * region, not a control.
 */
export function Main({ className, ...rest }: ComponentProps<"main">) {
  return (
    <main
      id="main"
      tabIndex={-1}
      className={cx("flex flex-col min-w-0 min-h-0 focus:outline-none", className)}
      {...rest}
    />
  );
}
