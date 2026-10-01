import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./app.css";

const BeliefGraphPage = React.lazy(() => import("./BeliefGraphPage"));
const page = window.location.pathname === "/graph"
  ? <React.Suspense fallback={<div className="route-loading">Loading belief graph…</div>}><BeliefGraphPage /></React.Suspense>
  : <App />;

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    {page}
  </React.StrictMode>,
);
