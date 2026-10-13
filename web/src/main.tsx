import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./styles/app.css";

const root = document.getElementById("root");
if (!root) throw new Error("index.html has no #root");

createRoot(root).render(<StrictMode>{null}</StrictMode>);
