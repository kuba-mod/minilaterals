import { useState, useMemo } from "react";

const C = {
  bg: "#0c0d0b", surface: "#141510", surface2: "#1c1d18",
  border: "#272820", borderLight: "#363728",
  text: "#d0d2ca", muted: "#636558", faint: "#3a3c30",
  accent: "#c4b240", accentDim: "#6e621e", accentFaint: "#2a2510",
  green: "#5c8c56", greenFaint: "#1a2818",
  red: "#8c4c44", redFaint: "#281410",
  amber: "#8c7040", amberFaint: "#241c08",
};

const ERAS = [
  { id: "founding",    label: "Founding Era",     years: "1991–1998", color: "#5a8a5a", desc: "Polish integration into Western structures; NATO candidacy; Franco-German reconciliation model applied to DE-PL axis" },
  { id: "accession",   label: "Accession Era",     years: "1999–2004", color: "#4a6a8a", desc: "NATO entry (1999); EU accession negotiations; Iraq war triggers first major trilateral split" },
  { id: "dormancy",    label: "Dormancy Era",       years: "2005–2013", color: "#7a6a3a", desc: "Founding mission complete; format loses purpose; Russia divergence; irregular meetings; 2011 Battlegroup anomaly" },
  { id: "crisis1",     label: "Crimea Response",   years: "2014–2016", color: "#8a6030", desc: "Temporary revival around Kyiv mediation and Minsk; PiS wins 2015, declares format 'irrelevant'" },
  { id: "limbo",       label: "Limbo Era",          years: "2017–2021", color: "#5a4a3a", desc: "PiS-era estrangement; rule-of-law conflict with EU; sparse meetings; COVID gap" },
  { id: "revival",     label: "Ukraine Revival",    years: "2022–2024", color: "#5a6a4a", desc: "Full-scale invasion triggers reactivation within days; Tusk election (Oct 2023) transforms dynamic" },
  { id: "renaissance", label: "Renaissance",        years: "2025–",     color: "#9a8a28", desc: "Most active period in format's history; Weimar+ expansion to UK/IT/ES; defence/Ukraine consensus" },
];

const MEETINGS = [
  { date: "1991-08-28", type: "FM",       era: "founding",    location: "Weimar, Germany",            topic: "Founding declaration; 10-point joint communiqué on European integration",              ministers: ["Genscher (DE)","Dumas (FR)","Skubiszewski (PL)"] },
  { date: "1992-04-23", type: "FM",       era: "founding",    location: "Bergerac, France",            topic: "WEU associate status for Poland secured",                                             ministers: ["Genscher (DE)","Dumas (FR)","Skubiszewski (PL)"] },
  { date: "1993-11-11", type: "FM",       era: "founding",    location: "Warsaw, Poland",              topic: "NATO candidacy; EU accession framework; Czech/Slovak aftermath",                      ministers: ["Kinkel (DE)","Juppé (FR)","Olechowski (PL)"] },
  { date: "1994-09-14", type: "FM",       era: "founding",    location: "Bamberg, Germany",            topic: "NATO enlargement framework; Partnership for Peace",                                   ministers: ["Kinkel (DE)","Juppé (FR)","Olechowski (PL)"] },
  { date: "1995-10-26", type: "FM",       era: "founding",    location: "Paris, France",               topic: "Partnership for Peace; Balkan crisis; Schengen",                                     ministers: ["Kinkel (DE)","de Charette (FR)","Bartoszewski (PL)"] },
  { date: "1996-12-19", type: "FM",       era: "founding",    location: "Warsaw, Poland",              topic: "NATO Study on Enlargement; IGC 1996 preparation",                                    ministers: ["Kinkel (DE)","de Charette (FR)","Rosati (PL)"] },
  { date: "1997-11-19", type: "FM",       era: "founding",    location: "Frankfurt/Oder, Germany",     topic: "Luxembourg European Council prep; enlargement criteria",                              ministers: ["Kinkel (DE)","Védrine (FR)","Rosati (PL)"] },
  { date: "1999-01-06", type: "FM",       era: "accession",   location: "Paris, France",               topic: "Post-NATO accession agenda; Kosovo crisis",                                          ministers: ["Fischer (DE)","Védrine (FR)","Geremek (PL)"] },
  { date: "1999-08-30", type: "FM",       era: "accession",   location: "Weimar, Germany",             topic: "EU accession negotiations; Agenda 2000",                                             ministers: ["Fischer (DE)","Védrine (FR)","Geremek (PL)"] },
  { date: "2000-06-07", type: "FM",       era: "accession",   location: "Kraków, Poland",              topic: "Nice Treaty; enlargement timeline",                                                  ministers: ["Fischer (DE)","Védrine (FR)","Bartoszewski (PL)"] },
  { date: "2001-03-16", type: "FM",       era: "accession",   location: "Paris, France",               topic: "Laeken agenda; CFSP development",                                                    ministers: ["Fischer (DE)","Védrine (FR)","Cimoszewicz (PL)"] },
  { date: "2001-09-06", type: "FM",       era: "accession",   location: "Weimar, Germany",             topic: "Accession timeline; constitutional convention",                                      ministers: ["Fischer (DE)","Védrine (FR)","Cimoszewicz (PL)"] },
  { date: "2002-06-28", type: "FM",       era: "accession",   location: "Warsaw, Poland",              topic: "Iraq tensions emerging; Copenhagen criteria",                                        ministers: ["Fischer (DE)","Védrine (FR)","Cimoszewicz (PL)"] },
  { date: "2003-05-09", type: "Summit",   era: "accession",   location: "Wrocław, Poland",             topic: "Post-Iraq split repair; Constitutional Treaty; Poland days before EU accession vote", ministers: ["Schröder (DE)","Chirac (FR)","Kwaśniewski (PL)"] },
  { date: "2003-05-26", type: "FM",       era: "accession",   location: "Warsaw, Poland",              topic: "Pre-accession final coordination",                                                   ministers: ["Fischer (DE)","de Villepin (FR)","Cimoszewicz (PL)"] },
  { date: "2004-10-22", type: "FM",       era: "accession",   location: "Warsaw, Poland",              topic: "Post-accession agenda; Constitutional Treaty ratification",                          ministers: ["Fischer (DE)","Haigneré (FR)","Pietras (PL)"] },
  { date: "2006-08-28", type: "FM",       era: "dormancy",    location: "Weimar (15th anniversary)",  topic: "Anniversary; EU reform Treaty",                                                      ministers: ["Steinmeier (DE)","Douste-Blazy (FR)","Fotyga (PL)"] },
  { date: "2008-11-07", type: "FM",       era: "dormancy",    location: "Paris, France",               topic: "Georgia war aftermath; Eastern Partnership initiative",                              ministers: ["Bury (DE)","Lellouche (FR)","Dowgielewicz (PL)"] },
  { date: "2010-02-01", type: "FM",       era: "dormancy",    location: "Warsaw, Poland",              topic: "Polish EU Council Presidency 2011 preparation",                                     ministers: ["Hoyer (DE)","Jouyet (FR)","Dowgielewicz (PL)"] },
  { date: "2011-02-07", type: "Summit",   era: "dormancy",    location: "Warsaw, Poland",              topic: "Eurozone crisis; Russia relations; Pact for Competitiveness",                        ministers: ["Merkel (DE)","Sarkozy (FR)","Komorowski (PL)"] },
  { date: "2011-07-05", type: "Defence",  era: "dormancy",    location: "Brussels",                    topic: "Weimar Battlegroup: 1,700 soldiers under Polish command signed",                     ministers: ["Westerwelle (DE)","Juppé (FR)","Sikorski (PL)"] },
  { date: "2011-09-22", type: "FM",       era: "dormancy",    location: "Berlin, Germany",             topic: "EU enlargement; Libya aftermath",                                                    ministers: ["Hoyer (DE)","Leonetti (FR)","Dowgielewicz (PL)"] },
  { date: "2012-03-16", type: "FM",       era: "dormancy",    location: "Antibes, France",             topic: "Syria; eurozone crisis",                                                             ministers: ["Link (DE)","Leonetti (FR)","Dowgielewicz (PL)"] },
  { date: "2012-10-01", type: "FM",       era: "dormancy",    location: "Warsaw, Poland",              topic: "Deeper EU integration; Fiscal Compact",                                             ministers: ["Link (DE)","Repentin (FR)","Serafin (PL)"] },
  { date: "2014-02-20", type: "FM",       era: "crisis1",     location: "Kyiv (emergency)",            topic: "Yanukovych–Maidan mediation; brokered Feb 21 agreement (collapsed same day)",       ministers: ["Steinmeier (DE)","Fabius (FR)","Sikorski (PL)"] },
  { date: "2014-03-01", type: "Parl.",    era: "crisis1",     location: "Kyiv",                        topic: "First-ever joint parliamentary visit to third country; Crimea/territorial integrity",ministers: ["Röttgen (DE)","Guigou (FR)","Schetyna (PL)"] },
  { date: "2015-08-01", type: "FM",       era: "crisis1",     location: "Weimar, Germany",             topic: "Minsk II implementation; migration crisis; refugee flows",                           ministers: ["Steinmeier (DE)","Fabius (FR)","Schetyna (PL)"] },
  { date: "2016-08-28", type: "FM",       era: "crisis1",     location: "Weimar (25th anniversary)",  topic: "Post-Brexit; migration; 'reinvigoration' pledge by all three FMs",                   ministers: ["Steinmeier (DE)","Ayrault (FR)","Waszczykowski (PL)"] },
  { date: "2017-06-01", type: "Finance",  era: "limbo",       location: "Berlin, Germany",             topic: "Capital markets union; fiscal rules (first Finance FM mtg since unknown)",           ministers: ["Gabriel (DE)","Le Drian (FR)","Waszczykowski (PL)"] },
  { date: "2021-08-01", type: "FM",       era: "limbo",       location: "Paris, France",               topic: "Afghanistan collapse; EU strategic autonomy post-US withdrawal",                    ministers: ["Maas (DE)","Le Drian (FR)","Rau (PL)"] },
  { date: "2022-02-08", type: "Summit",   era: "revival",     location: "Berlin, Germany",             topic: "Pre-invasion: Ukraine sovereignty; NATO unity; first HoG trilateral in years",      ministers: ["Scholz (DE)","Macron (FR)","Duda (PL)"] },
  { date: "2022-03-01", type: "FM",       era: "revival",     location: "Łódź, Poland",                topic: "Days after invasion: refugee crisis; military aid; SWIFT sanctions",                ministers: ["Baerbock (DE)","Le Drian (FR)","Rau (PL)"] },
  { date: "2023-06-12", type: "Summit",   era: "revival",     location: "Paris, France",               topic: "Ukraine aid; NATO membership path; arms supply; 'unwavering support'",             ministers: ["Scholz (DE)","Macron (FR)","Duda (PL)"] },
  { date: "2023-06-27", type: "Finance",  era: "revival",     location: "Weimar, Germany",             topic: "Capital markets union; Ukraine reconstruction finance (first Finance mtg since 2017)",ministers: ["Lindner (DE)","Le Maire (FR)","Rzeczkowska (PL)"] },
  { date: "2024-02-12", type: "Summit",   era: "revival",     location: "Berlin, Germany",             topic: "Avdiivka fall; European Sky Shield; Trump-NATO comments",                           ministers: ["Scholz (DE)","Macron (FR)","Tusk (PL)"] },
  { date: "2024-02-12", type: "FM",       era: "revival",     location: "La Celle-Saint-Cloud",        topic: "'Weimar of citizens/youth/culture'; Ukraine coordination",                          ministers: ["Baerbock (DE)","Séjourné (FR)","Sikorski (PL)"] },
  { date: "2024-03-15", type: "Summit",   era: "revival",     location: "Berlin, Germany",             topic: "Long-range artillery coalition for Ukraine announced",                               ministers: ["Scholz (DE)","Macron (FR)","Tusk (PL)"] },
  { date: "2024-11-07", type: "Statement",era: "revival",     location: "Joint statement",             topic: "Georgia 2024 election irregularities; democratic backsliding warning",              ministers: [] },
  { date: "2024-12-31", type: "Statement",era: "revival",     location: "Joint statement",             topic: "Georgia constitutional crisis; violence against protesters",                        ministers: [] },
  { date: "2025-02-12", type: "FM",       era: "renaissance", location: "Paris, France",               topic: "Trump-Putin call; Ukraine excluded from negotiations; emergency response",          ministers: ["Wadephul (DE)","Barrot (FR)","Sikorski (PL)"] },
  { date: "2025-03-31", type: "FM+",      era: "renaissance", location: "Madrid (Weimar+)",            topic: "European security architecture; ReArm Europe",                                       ministers: ["Wadephul (DE)","Barrot (FR)","Sikorski (PL) + UK/IT/ES"] },
  { date: "2025-05-12", type: "FM+",      era: "renaissance", location: "Lancaster House, London",     topic: "Ukraine / Euro-Atlantic security",                                                  ministers: ["Wadephul (DE)","Barrot (FR)","Sikorski (PL) + UK/IT/ES"] },
  { date: "2025-06-12", type: "FM+",      era: "renaissance", location: "Rome (Weimar+)",              topic: "Ukraine sanctions; NATO SG Rutte and Kallas present",                               ministers: ["Wadephul (DE)","Barrot (FR)","Sikorski (PL) + NATO/EU"] },
  { date: "2025-08-06", type: "FM+",      era: "renaissance", location: "Warsaw (Weimar+)",            topic: "Nawrocki inauguration; ReArm Europe; trilateral communiqué",                        ministers: ["Wadephul (DE)","Barrot (FR)","Sikorski (PL) + UK/IT/ES"] },
  { date: "2025-08-28", type: "Summit",   era: "renaissance", location: "Chișinău, Moldova",           topic: "Ukraine EU accession; Moldova solidarity; Merz first summit",                       ministers: ["Merz (DE)","Macron (FR)","Tusk (PL)"] },
  { date: "2026-03-09", type: "Sectoral", era: "renaissance", location: "Gniezno, Poland",             topic: "ETS-2; electrification; first env. ministers trilateral in 12 years",               ministers: ["(Environment ministers)"] },
];

const MILESTONES = [
  { year: 1991, event: "Founded in Weimar; 10-point declaration on European integration", era: "founding" },
  { year: 1992, event: "Poland wins WEU associate status at Bergerac meeting", era: "founding" },
  { year: 1997, event: "Poland invited to join NATO at Madrid summit (Weimar diplomacy credited)", era: "founding" },
  { year: 1999, event: "Poland joins NATO — primary founding mission delivered", era: "accession" },
  { year: 2003, event: "Iraq war: Poland sides with US ('New Europe'); first major trilateral fracture", era: "accession" },
  { year: 2004, event: "Poland joins EU — second founding mission complete; format loses raison d'être", era: "accession" },
  { year: 2011, event: "Weimar Battlegroup signed; HoG summit urges Russia engagement (in hindsight: naïve)", era: "dormancy" },
  { year: 2014, event: "Kyiv FM mediation during Maidan; Crimea annexation — temporary format revival", era: "crisis1" },
  { year: 2015, event: "PiS wins Polish election — rule-of-law crisis begins, Triangle cools sharply", era: "limbo" },
  { year: 2016, event: "Polish FM publicly declares format 'irrelevant' (April); Aug. reinvigoration pledge", era: "crisis1" },
  { year: 2022, event: "Full-scale Russian invasion (Feb 24) — format reactivated within 5 days of outbreak", era: "revival" },
  { year: 2023, event: "Tusk wins Polish election — single most transformative event for Triangle cohesion since 2004", era: "revival" },
  { year: 2024, event: "Two HoG summits in 5 weeks; long-range artillery coalition announced", era: "revival" },
  { year: 2025, event: "Weimar+ format launched in response to Trump-Putin call; Chișinău summit peak activity", era: "renaissance" },
  { year: 2026, event: "Environment ministers meet for first time in 12 years (Gniezno, March)", era: "renaissance" },
];

const ANNUAL = [
  { year: 1991, meetings: 1, score: 0.82, era: "founding",    note: "Founding year; high ambition, genuine novelty" },
  { year: 1992, meetings: 1, score: 0.78, era: "founding",    note: "WEU associate status secured; active" },
  { year: 1993, meetings: 1, score: 0.72, era: "founding",    note: "NATO candidacy building; regular rhythm" },
  { year: 1994, meetings: 1, score: 0.73, era: "founding",    note: "NATO framework; steady" },
  { year: 1995, meetings: 1, score: 0.70, era: "founding",    note: "PfP; Balkan crisis adds urgency" },
  { year: 1996, meetings: 1, score: 0.72, era: "founding",    note: "NATO Study on Enlargement" },
  { year: 1997, meetings: 1, score: 0.82, era: "founding",    note: "Luxembourg EC prep; strongest founding year" },
  { year: 1998, meetings: 0, score: 0.52, era: "founding",    note: "No FM meeting recorded; transition year" },
  { year: 1999, meetings: 2, score: 0.88, era: "accession",   location: "NATO entry + Kosovo; 2 meetings", note: "NATO entry + Kosovo; 2 FM meetings" },
  { year: 2000, meetings: 1, score: 0.74, era: "accession",   note: "Nice Treaty; steady" },
  { year: 2001, meetings: 2, score: 0.75, era: "accession",   note: "9/11 disrupts but 2 meetings held" },
  { year: 2002, meetings: 1, score: 0.68, era: "accession",   note: "Iraq tensions emerging" },
  { year: 2003, meetings: 2, score: 0.62, era: "accession",   note: "Iraq split: New vs Old Europe" },
  { year: 2004, meetings: 1, score: 0.64, era: "accession",   note: "EU accession achieved; format identity crisis begins" },
  { year: 2005, meetings: 0, score: 0.36, era: "dormancy",    note: "No FM meeting; Constitutional Treaty failure" },
  { year: 2006, meetings: 1, score: 0.44, era: "dormancy",    note: "15th anniversary; mainly symbolic" },
  { year: 2007, meetings: 0, score: 0.38, era: "dormancy",    note: "No FM meeting; Tusk I replaces PiS" },
  { year: 2008, meetings: 1, score: 0.50, era: "dormancy",    note: "Georgia war; Eastern Partnership impulse" },
  { year: 2009, meetings: 0, score: 0.40, era: "dormancy",    note: "No trilateral meeting on record" },
  { year: 2010, meetings: 1, score: 0.48, era: "dormancy",    note: "Polish EU presidency preparation" },
  { year: 2011, meetings: 2, score: 0.62, era: "dormancy",    note: "Summit + Battlegroup signed; modest revival" },
  { year: 2012, meetings: 2, score: 0.52, era: "dormancy",    note: "2 FM meetings; routine" },
  { year: 2013, meetings: 0, score: 0.36, era: "dormancy",    note: "No meeting; Merkel re-election transition" },
  { year: 2014, meetings: 2, score: 0.80, era: "crisis1",     note: "Kyiv mediation; Crimea response" },
  { year: 2015, meetings: 1, score: 0.72, era: "crisis1",     note: "Minsk II; migration crisis; PiS wins Oct" },
  { year: 2016, meetings: 1, score: 0.54, era: "crisis1",     note: "PiS: 'irrelevant'; reinvigoration pledge" },
  { year: 2017, meetings: 1, score: 0.44, era: "limbo",       note: "Finance ministers only; rule-of-law tension" },
  { year: 2018, meetings: 0, score: 0.30, era: "limbo",       note: "No FM meeting; PiS-EU conflict deepens" },
  { year: 2019, meetings: 0, score: 0.28, era: "limbo",       note: "No meeting; PL-EU Article 7 procedure" },
  { year: 2020, meetings: 0, score: 0.25, era: "limbo",       note: "COVID; no meetings; format at low ebb" },
  { year: 2021, meetings: 1, score: 0.40, era: "limbo",       note: "FM Paris; Afghanistan; minimal output" },
  { year: 2022, meetings: 2, score: 0.84, era: "revival",     note: "Invasion trigger; format reborn in days" },
  { year: 2023, meetings: 3, score: 0.88, era: "revival",     note: "Summit + Finance + FM; Tusk wins Oct" },
  { year: 2024, meetings: 4, score: 0.91, era: "revival",     note: "Most active since 1990s; Tusk effect clear" },
  { year: 2025, meetings: 6, score: 0.94, era: "renaissance", note: "All-time peak; Weimar+ launched; Chișinău" },
  { year: 2026, meetings: 1, score: 0.76, era: "renaissance", note: "Jan–Apr only; Gniezno env. ministers" },
];

const eraOf = (id) => ERAS.find(e => e.id === id);
const eraColor = (id) => eraOf(id)?.color || "#666";
const scoreColor = (s) => s >= 0.75 ? C.green : s >= 0.5 ? C.amber : C.red;

function ScoreBar({ score, height = 5 }) {
  return (
    <div style={{ width: "100%", background: C.border, borderRadius: 2, height, overflow: "hidden" }}>
      <div style={{ width: `${Math.round(score * 100)}%`, height: "100%", background: scoreColor(score) }} />
    </div>
  );
}

function Tag({ children, color, bg }) {
  return (
    <span style={{ fontFamily: "monospace", fontSize: 9, letterSpacing: 1.5, color, background: bg || color + "18", border: `1px solid ${color}44`, padding: "1px 6px", borderRadius: 2, whiteSpace: "nowrap" }}>
      {children}
    </span>
  );
}

function EraTag({ eraId }) {
  const era = eraOf(eraId);
  if (!era) return null;
  return <Tag color={era.color}>{era.label.replace(" Era","").replace(" Response","").toUpperCase()}</Tag>;
}

const TYPE_COLORS = { FM: C.accent, "FM+": "#9a8a28", Summit: "#5a8a9a", Defence: "#6a5a9a", Finance: "#5a8a5a", "Parl.": "#666", Sectoral: "#6a7a5a", Statement: "#7a5a3a" };
function TypeTag({ type }) {
  const c = TYPE_COLORS[type] || C.muted;
  return <Tag color={c}>{type.toUpperCase()}</Tag>;
}

const TABS = ["Timeline", "Meetings", "Milestones", "Eras"];

export default function WeimrTracker() {
  const [tab, setTab] = useState("Timeline");
  const [eraFilter, setEraFilter] = useState("all");
  const [typeFilter, setTypeFilter] = useState("all");

  const filtered = useMemo(() =>
    MEETINGS.filter(m =>
      (eraFilter === "all" || m.era === eraFilter) &&
      (typeFilter === "all" || m.type === typeFilter)
    ), [eraFilter, typeFilter]);

  const s = {
    root: { background: C.bg, color: C.text, minHeight: "100vh", fontFamily: "'Georgia','Times New Roman',serif", fontSize: 13, lineHeight: 1.5 },
    hdr: { borderBottom: `1px solid ${C.border}`, padding: "22px 28px 18px" },
    title: { fontFamily: "monospace", fontSize: 19, letterSpacing: 5, color: C.accent, textTransform: "uppercase", margin: 0 },
    sub: { fontFamily: "monospace", fontSize: 9, letterSpacing: 3, color: C.muted, textTransform: "uppercase", marginTop: 5 },
    stats: { display: "flex", gap: 28, marginTop: 16, flexWrap: "wrap" },
    stat: { textAlign: "center" },
    statV: { fontFamily: "monospace", fontSize: 22, color: C.accent, lineHeight: 1 },
    statL: { fontFamily: "monospace", fontSize: 8, letterSpacing: 2, color: C.muted, textTransform: "uppercase", marginTop: 3 },
    tabBar: { display: "flex", borderBottom: `1px solid ${C.border}`, padding: "0 28px", overflowX: "auto" },
    tabBtn: (a) => ({ fontFamily: "monospace", fontSize: 9, letterSpacing: 2.5, textTransform: "uppercase", padding: "10px 18px", background: "none", border: "none", borderBottom: a ? `2px solid ${C.accent}` : "2px solid transparent", color: a ? C.accent : C.muted, cursor: "pointer", whiteSpace: "nowrap" }),
    body: { padding: "24px 28px" },
    sHead: { fontFamily: "monospace", fontSize: 9, letterSpacing: 3, color: C.muted, textTransform: "uppercase", borderBottom: `1px solid ${C.border}`, paddingBottom: 8, marginBottom: 16 },
    th: { fontFamily: "monospace", fontSize: 8, letterSpacing: 2, color: C.muted, textTransform: "uppercase", padding: "6px 10px", textAlign: "left", borderBottom: `1px solid ${C.border}` },
    td: (i) => ({ padding: "7px 10px", borderBottom: `1px solid ${C.border}`, verticalAlign: "top", background: i % 2 ? C.surface + "70" : "transparent", fontSize: 12 }),
    card: { background: C.surface, border: `1px solid ${C.border}`, borderRadius: 3, padding: "14px 18px", marginBottom: 14 },
    fbar: { display: "flex", gap: 6, marginBottom: 14, flexWrap: "wrap", alignItems: "center" },
    fbtn: (a, c = C.accent) => ({ fontFamily: "monospace", fontSize: 9, letterSpacing: 1.2, padding: "4px 10px", background: a ? c + "20" : "transparent", border: `1px solid ${a ? c : C.border}`, color: a ? c : C.muted, borderRadius: 2, cursor: "pointer", whiteSpace: "nowrap" }),
  };

  return (
    <div style={s.root}>
      <div style={s.hdr}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: 10 }}>
          <div>
            <h1 style={s.title}>Weimar Triangle</h1>
            <div style={s.sub}>Coordination Intelligence · France · Germany · Poland · 1991–2026</div>
          </div>
          <span style={{ fontSize: 26 }}>🇫🇷🇩🇪🇵🇱</span>
        </div>
        <div style={s.stats}>
          {[
            { v: 36, l: "Years" },
            { v: MEETINGS.length, l: "Meetings" },
            { v: MEETINGS.filter(m=>m.type==="Summit").length, l: "HoG Summits" },
            { v: ERAS.length, l: "Eras" },
            { v: ANNUAL.filter(y=>y.meetings===0).length, l: "Silent Years" },
          ].map(x => (
            <div key={x.l} style={s.stat}>
              <div style={s.statV}>{x.v}</div>
              <div style={s.statL}>{x.l}</div>
            </div>
          ))}
        </div>
      </div>

      <div style={s.tabBar}>
        {TABS.map(t => <button key={t} style={s.tabBtn(tab===t)} onClick={()=>setTab(t)}>{t}</button>)}
      </div>

      <div style={s.body}>

        {tab === "Timeline" && (
          <>
            <div style={s.sHead}>Activity Score by Year — 1991 to 2026</div>
            {/* Bar chart */}
            <div style={{ display: "flex", gap: 2, alignItems: "flex-end", height: 100, marginBottom: 4 }}>
              {ANNUAL.map(y => (
                <div key={y.year} style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center" }}
                  title={`${y.year}: ${y.note} (${Math.round(y.score*100)}%)`}>
                  <div style={{ width: "100%", height: Math.max(3, y.score * 96), background: eraColor(y.era), borderRadius: "2px 2px 0 0", opacity: 0.88 }} />
                </div>
              ))}
            </div>
            {/* Year axis */}
            <div style={{ display: "flex", gap: 2, marginBottom: 10 }}>
              {ANNUAL.map(y => (
                <div key={y.year} style={{ flex: 1, fontFamily: "monospace", fontSize: 7, color: y.year % 5 === 1 ? C.muted : "transparent", textAlign: "center" }}>
                  {y.year % 5 === 1 ? y.year : ""}
                </div>
              ))}
            </div>
            {/* Era legend */}
            <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 28 }}>
              {ERAS.map(e => (
                <div key={e.id} style={{ display: "flex", alignItems: "center", gap: 5 }}>
                  <div style={{ width: 9, height: 9, background: e.color, borderRadius: 2 }} />
                  <span style={{ fontFamily: "monospace", fontSize: 9, color: C.muted }}>{e.label} ({e.years})</span>
                </div>
              ))}
            </div>

            <div style={s.sHead}>Year-by-Year Record</div>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr>{["Year","Era","Meetings","Score","Signal","Notes"].map(h=><th key={h} style={s.th}>{h}</th>)}</tr>
              </thead>
              <tbody>
                {[...ANNUAL].reverse().map((y, i) => (
                  <tr key={y.year}>
                    <td style={{ ...s.td(i), fontFamily: "monospace", color: C.accent }}>{y.year}</td>
                    <td style={s.td(i)}><EraTag eraId={y.era} /></td>
                    <td style={{ ...s.td(i), fontFamily: "monospace", textAlign: "center" }}>{y.meetings || "–"}</td>
                    <td style={{ ...s.td(i), fontFamily: "monospace", color: scoreColor(y.score) }}>{Math.round(y.score*100)}%</td>
                    <td style={{ ...s.td(i), width: 80 }}><ScoreBar score={y.score} height={6} /></td>
                    <td style={{ ...s.td(i), color: C.muted, fontSize: 11 }}>{y.note}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}

        {tab === "Meetings" && (
          <>
            <div style={s.fbar}>
              <span style={{ fontFamily: "monospace", fontSize: 9, color: C.muted, letterSpacing: 2 }}>ERA:</span>
              {["all",...ERAS.map(e=>e.id)].map(f => (
                <button key={f} style={s.fbtn(eraFilter===f)} onClick={()=>setEraFilter(f)}>
                  {f==="all" ? "ALL" : (eraOf(f)?.label||f).replace(" Era","").replace(" Response","").toUpperCase()}
                </button>
              ))}
            </div>
            <div style={{ ...s.fbar, marginTop: -6 }}>
              <span style={{ fontFamily: "monospace", fontSize: 9, color: C.muted, letterSpacing: 2 }}>TYPE:</span>
              {["all","FM","FM+","Summit","Defence","Finance","Sectoral","Statement","Parl."].map(f => {
                const c = TYPE_COLORS[f] || C.muted;
                return <button key={f} style={s.fbtn(typeFilter===f, c)} onClick={()=>setTypeFilter(f)}>{f.toUpperCase()}</button>;
              })}
            </div>
            <div style={{ fontFamily: "monospace", fontSize: 10, color: C.muted, marginBottom: 12 }}>{filtered.length} of {MEETINGS.length} meetings</div>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr>{["Date","Type","Era","Location","Topic","Participants"].map(h=><th key={h} style={s.th}>{h}</th>)}</tr>
              </thead>
              <tbody>
                {filtered.map((m, i) => (
                  <tr key={i}>
                    <td style={{ ...s.td(i), fontFamily: "monospace", fontSize: 11, color: C.muted, whiteSpace: "nowrap" }}>{m.date}</td>
                    <td style={s.td(i)}><TypeTag type={m.type} /></td>
                    <td style={s.td(i)}><EraTag eraId={m.era} /></td>
                    <td style={{ ...s.td(i), fontSize: 11 }}>{m.location}</td>
                    <td style={s.td(i)}>{m.topic}</td>
                    <td style={{ ...s.td(i), fontSize: 10, color: C.muted }}>{m.ministers.join(", ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div style={{ marginTop: 12, fontFamily: "monospace", fontSize: 10, color: C.muted, lineHeight: 1.8 }}>
              FM = Foreign Ministers · FM+ = Weimar+ format (includes UK/IT/ES) · SUMMIT = Heads of Government<br/>
              DEFENCE/FINANCE/SECTORAL = Sectoral ministers · PARL. = Parliamentary delegation · STMT = Joint statement only
            </div>
          </>
        )}

        {tab === "Milestones" && (
          <>
            <div style={s.sHead}>Key Inflection Points 1991–2026</div>
            <div style={{ position: "relative", paddingLeft: 20 }}>
              <div style={{ position: "absolute", left: 4, top: 8, bottom: 8, width: 1, background: C.border }} />
              {MILESTONES.map((m, i) => {
                const ec = eraColor(m.era);
                return (
                  <div key={i} style={{ position: "relative", marginBottom: 20, paddingLeft: 22 }}>
                    <div style={{ position: "absolute", left: -12, top: 6, width: 10, height: 10, borderRadius: "50%", background: ec, border: `2px solid ${C.bg}`, zIndex: 1 }} />
                    <div style={{ display: "flex", gap: 14, alignItems: "flex-start" }}>
                      <span style={{ fontFamily: "monospace", fontSize: 13, color: C.accent, minWidth: 36, paddingTop: 1 }}>{m.year}</span>
                      <div>
                        <div style={{ fontSize: 13 }}>{m.event}</div>
                        <div style={{ marginTop: 4 }}><EraTag eraId={m.era} /></div>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </>
        )}

        {tab === "Eras" && (
          <>
            <div style={s.sHead}>The Seven Eras of the Weimar Triangle</div>
            {ERAS.map((era, i) => {
              const eraYears = ANNUAL.filter(y => y.era === era.id);
              const eraMeetings = MEETINGS.filter(m => m.era === era.id);
              const summits = eraMeetings.filter(m => m.type === "Summit").length;
              const avg = eraYears.length ? eraYears.reduce((a,b)=>a+b.score,0)/eraYears.length : 0;
              const eraMilestones = MILESTONES.filter(m => m.era === era.id);
              return (
                <div key={era.id} style={{ ...s.card, borderLeft: `4px solid ${era.color}` }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 8, flexWrap: "wrap", gap: 8 }}>
                    <div>
                      <span style={{ fontFamily: "monospace", fontSize: 11, color: era.color, marginRight: 8 }}>0{i+1}.</span>
                      <strong style={{ fontSize: 14 }}>{era.label}</strong>
                      <span style={{ fontFamily: "monospace", fontSize: 10, color: C.muted, marginLeft: 10 }}>{era.years}</span>
                    </div>
                    <div style={{ display: "flex", gap: 20 }}>
                      {[
                        { v: eraMeetings.length, l: "Meetings" },
                        { v: summits, l: "Summits" },
                        { v: Math.round(avg*100)+"%", l: "Avg Score" },
                      ].map(x => (
                        <div key={x.l} style={{ textAlign: "center" }}>
                          <div style={{ fontFamily: "monospace", fontSize: 16, color: era.color }}>{x.v}</div>
                          <div style={{ fontFamily: "monospace", fontSize: 8, color: C.muted, letterSpacing: 1 }}>{x.l}</div>
                        </div>
                      ))}
                    </div>
                  </div>
                  <ScoreBar score={avg} height={4} />
                  <div style={{ fontSize: 12, color: C.muted, marginTop: 10, lineHeight: 1.6 }}>{era.desc}</div>
                  {eraMilestones.length > 0 && (
                    <div style={{ marginTop: 10, borderTop: `1px solid ${C.border}`, paddingTop: 10 }}>
                      {eraMilestones.map((m, mi) => (
                        <div key={mi} style={{ display: "flex", gap: 12, fontSize: 12, marginBottom: 5 }}>
                          <span style={{ fontFamily: "monospace", color: era.color, minWidth: 36 }}>{m.year}</span>
                          <span style={{ color: C.text }}>{m.event}</span>
                        </div>
                      ))}
                    </div>
                  )}
                  {/* Year tiles */}
                  <div style={{ display: "flex", gap: 5, flexWrap: "wrap", marginTop: 12 }}>
                    {eraYears.map(y => (
                      <div key={y.year} title={y.note} style={{ background: era.color+"18", border: `1px solid ${era.color}44`, borderRadius: 3, padding: "4px 8px", cursor: "default" }}>
                        <div style={{ fontFamily: "monospace", fontSize: 9, color: era.color }}>{y.year}</div>
                        <div style={{ fontFamily: "monospace", fontSize: 11, color: scoreColor(y.score) }}>{Math.round(y.score*100)}%</div>
                        <div style={{ fontFamily: "monospace", fontSize: 8, color: C.muted }}>{y.meetings}m</div>
                      </div>
                    ))}
                  </div>
                </div>
              );
            })}
            <div style={{ ...s.card, marginTop: 8 }}>
              <div style={{ fontFamily: "monospace", fontSize: 9, letterSpacing: 2.5, color: C.muted, textTransform: "uppercase", marginBottom: 8 }}>Data & Methodology</div>
              <div style={{ fontSize: 11, color: C.muted, lineHeight: 1.7 }}>
                Meeting dates and participants drawn from Wikipedia Weimar Triangle article (March 2026 version), France Diplomatie, Bundesregierung, and Genshagen Foundation records.
                Annual scores are editorial composites: meeting frequency (40%), joint statement output (30%), reported policy alignment from PISM/DGAP/IFRI analyses (30%).
                <strong style={{ color: C.text }}> Pre-2014 Council voting data is not available in machine-readable form</strong>; alignment is qualitative.
                Score confidence: 2004–2026 = moderate–high; 1991–2003 = low–moderate. Silent years (8 total) are scored on context alone.
              </div>
            </div>
          </>
        )}

      </div>
    </div>
  );
}
