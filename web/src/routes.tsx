import { createBrowserRouter } from "react-router";
import { AboutPage } from "@/features/about/AboutPage";
import { BenchPage } from "@/features/bench/BenchPage";
import { ChatPage } from "@/features/chat/ChatPage";
import { Layout } from "@/shell/Layout";

/* Three pages in one frame. Every page is in the one bundle: a page loaded on
   demand is a file that a later release deletes, and a tab left open across
   that release would then fail on the first click of Benchmark.

   An address that is none of these is the chat page; the server sends the
   same page with a 404 for it. */
export const router = createBrowserRouter([
  {
    element: <Layout />,
    children: [
      { path: "/", element: <ChatPage /> },
      { path: "/bench", element: <BenchPage /> },
      { path: "/about", element: <AboutPage /> },
      { path: "*", element: <ChatPage /> },
    ],
  },
]);
