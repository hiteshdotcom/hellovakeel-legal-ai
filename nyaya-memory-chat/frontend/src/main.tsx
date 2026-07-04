import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { IconContext } from "@phosphor-icons/react";
import App from "./App";
import "./index.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {/* Global icon language: one weight, consistent sizing (design-taste-frontend). */}
    <IconContext.Provider value={{ weight: "regular", size: 18 }}>
      <App />
    </IconContext.Provider>
  </StrictMode>,
);
