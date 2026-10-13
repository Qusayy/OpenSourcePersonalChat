/**
 * Put text on the clipboard; resolves to whether it got there.
 *
 * `navigator.clipboard` exists only in a secure context. Reached at a bare IP
 * over plain HTTP, which is how a small self-hosted box is often first opened,
 * it is undefined and every Copy button would fail. There, and wherever the
 * API refuses, the text is selected in an off-screen field and the document
 * is asked to copy it, which needs no secure context.
 */
export async function copyText(text: string): Promise<boolean> {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      /* refused; try the older route */
    }
  }
  const field = document.createElement("textarea");
  field.value = text;
  field.readOnly = true; // no on-screen keyboard on touch devices
  field.setAttribute("aria-hidden", "true");
  // 16px so iOS does not zoom to it; fixed so selecting it cannot scroll the page.
  field.style.cssText =
    "position:fixed;top:0;left:0;width:1px;height:1px;padding:0;border:0;opacity:0;font-size:16px;";
  const returnTo = document.activeElement;
  // Inside an open modal dialog everything outside it is inert, a field in
  // <body> included, so the field goes where focus is allowed to be.
  (document.querySelector("dialog[open]") || document.body).appendChild(field);
  field.focus({ preventScroll: true });
  field.select();
  field.setSelectionRange(0, text.length);
  let copied = false;
  try {
    copied = document.execCommand("copy");
  } catch {
    copied = false;
  }
  field.remove();
  if (returnTo instanceof HTMLElement) returnTo.focus({ preventScroll: true });
  return copied;
}

/**
 * Make every Copy button inside generated text work. The buttons are part of
 * HTML the markdown renderer wrote, not components, so one listener on the
 * document serves all of them: a click on `[data-copy]` copies the `data-code`
 * of the `.codeblock` it sits in. Returns a function that removes the listener.
 */
export function installCopyButtons(onBlocked: () => void): () => void {
  const onClick = async (event: MouseEvent) => {
    const btn = (event.target as Element | null)?.closest?.("[data-copy]");
    if (!(btn instanceof HTMLElement)) return;
    const block = btn.closest<HTMLElement>(".codeblock");
    const text = block?.dataset.code ?? "";
    if (await copyText(text)) {
      btn.textContent = "Copied";
      btn.classList.add("ok");
    } else {
      onBlocked();
    }
    setTimeout(() => {
      btn.textContent = "Copy";
      btn.classList.remove("ok");
    }, 1600);
  };
  document.addEventListener("click", onClick);
  return () => document.removeEventListener("click", onClick);
}
