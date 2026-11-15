import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { HashRouter, Route, Routes } from "react-router-dom";
import Dashboard from "./pages/Dashboard";
import Home from "./pages/Home";
import Story from "./pages/Story";
import { LangContext, type Lang } from "./lib/i18n";
import "./styles.css";

function load<T>(k: string, d: T): T {
  try { const v = localStorage.getItem(k); return v ? (JSON.parse(v) as T) : d; } catch { return d; }
}
function save(k: string, v: unknown) {
  try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode: keep in memory only */ }
}

function App() {
  const [lang, setLang] = useState<Lang>(() => load("ee-lang", "en"));
  const [expert, setExpert] = useState<boolean>(() => load("ee-expert", false));
  useEffect(() => { save("ee-lang", lang); document.documentElement.lang = lang; }, [lang]);
  useEffect(() => save("ee-expert", expert), [expert]);
  return (
    <LangContext.Provider value={{ lang, setLang, expert, setExpert }}>
      <HashRouter>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/story" element={<Story />} />
        </Routes>
      </HashRouter>
    </LangContext.Provider>
  );
}

createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>);
