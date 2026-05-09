import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { App } from "./App";
import { TablePage } from "./pages/TablePage";
import { LobbyPage } from "./pages/LobbyPage";
import { SpectatePage } from "./pages/SpectatePage";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<App />}>
          <Route index element={<LobbyPage />} />
          <Route path="table/:code" element={<TablePage />} />
          <Route path="spectate/:code" element={<SpectatePage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </React.StrictMode>
);
