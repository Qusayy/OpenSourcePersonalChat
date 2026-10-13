/* Every block on the chat page is the same measure, centred. Centred by auto
   margins rather than viewport arithmetic: inside a scroller the available
   width is not the window's. */
export const COL =
  "w-full max-w-[calc(var(--col)+var(--edge)*2)] mx-auto " +
  "pl-[max(var(--edge),env(safe-area-inset-left))] " +
  "pr-[max(var(--edge),env(safe-area-inset-right))]";
