import { useContext } from "react";
import { Link } from "react-router-dom";
import { LangContext } from "../lib/i18n";

export function Brand() {
  return (
    <Link to="/" className="brand" aria-label="EarthEcho home">
      <img src="logo-64.png" width="32" height="32" alt="" />
      EarthEcho
    </Link>
  );
}

export function LangSwitch() {
  const { lang, setLang } = useContext(LangContext);
  return (
    <div className="toggle" role="group" aria-label="Language">
      <button aria-pressed={lang === "en"} onClick={() => setLang("en")}>English</button>
      <button aria-pressed={lang === "hi"} onClick={() => setLang("hi")} lang="hi">हिन्दी</button>
    </div>
  );
}

export function LevelSwitch() {
  const { expert, setExpert } = useContext(LangContext);
  return (
    <div className="toggle" role="group" aria-label="Detail level">
      <button aria-pressed={!expert} onClick={() => setExpert(false)}>Simple</button>
      <button aria-pressed={expert} onClick={() => setExpert(true)}>Expert</button>
    </div>
  );
}
