import { useLocation } from "react-router";

export type PageName = "chat" | "bench" | "about";

/** Which page a path is. Anything that is not Benchmark or About is Chat,
 *  including an address that does not exist: the server answers those with
 *  the chat page and a 404. */
export function pageOf(pathname: string): PageName {
  const path = pathname.replace(/\/+$/, "");
  if (path === "/bench") return "bench";
  if (path === "/about") return "about";
  return "chat";
}

export function usePage(): PageName {
  return pageOf(useLocation().pathname);
}
