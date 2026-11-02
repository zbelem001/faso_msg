"use strict";

const $ = (id) => document.getElementById(id);
const secondes = (ms) => `${(ms / 1000).toLocaleString("fr-FR", { maximumFractionDigits: 1 })} s`;
const STATUTS = {
  actif: ["Actif", "ok"],
  en_attente: ["Première connexion en attente", "attente"],
  revoque: ["Révoqué", "off"],
};
const ACTIONS = { enrolement: "Enrôlement", revocation: "Révocation" };
let agents = [], moi = null, cible = null, identifiantModifie = false;

async function api(chemin, options = {}) {
  const rep = await fetch(chemin, { headers: { "Content-Type": "application/json" }, ...options });
  if (rep.status === 401) { location.reload(); throw new Error("Session expirée"); }
  const corps = await rep.json().catch(() => ({}));
  if (!rep.ok) {
    // Erreur de validation (422) : FastAPI renvoie une liste de messages
    const detail = Array.isArray(corps.detail) ? corps.detail.map((d) => d.msg).join(" ; ") : corps.detail;
    throw new Error(typeof detail === "string" && detail ? detail : `Erreur ${rep.status}`);
  }
  return corps;
}

function erreur(message) { $("erreur").textContent = message; $("erreur").hidden = !message; }

// Tout texte venant du serveur passe par textContent : aucun HTML n'est interprété.
function cellule(tr, texte, classe) {
  const td = tr.insertCell(); td.textContent = texte ?? ""; if (classe) td.className = classe; return td;
}
function badge(td, texte, type) {
  const s = document.createElement("span"); s.className = `badge ${type}`; s.textContent = texte; td.append(s);
}
function ligneVide(corps, texte, colonnes) { cellule(corps.insertRow(), texte, "vide").colSpan = colonnes; }

function afficherAgents() {
  const filtre = $("recherche").value.trim().toLowerCase();
  const corps = $("liste-agents"); corps.replaceChildren();
  const visibles = agents.filter((a) =>
    [a.nom, a.identifiant, a.ministere, a.fonction].join(" ").toLowerCase().includes(filtre));
  for (const a of visibles) {
    const tr = corps.insertRow();
    const nom = document.createElement("strong"); nom.textContent = a.nom || a.identifiant;
    const id = document.createElement("span"); id.className = "identifiant"; id.textContent = a.identifiant;
    tr.insertCell().append(nom, id);
    cellule(tr, a.ministere); cellule(tr, a.fonction);
    badge(tr.insertCell(), ...STATUTS[a.statut]);
    const action = cellule(tr, "", "action");
    if (a.statut !== "revoque" && a.identifiant !== moi?.identifiant) {
      const b = document.createElement("button");
      b.className = "danger petit"; b.textContent = "Révoquer";
      b.addEventListener("click", () => ouvrirRevocation(a));
      action.append(b);
    }
  }
  if (!visibles.length) ligneVide(corps, "Aucun agent ne correspond à la recherche.", 5);
  const nombre = (statut) => agents.filter((a) => a.statut === statut).length;
  $("compteurs").textContent =
    `${nombre("actif")} actif(s) · ${nombre("en_attente")} en attente · ${nombre("revoque")} révoqué(s)`;
}

async function chargerAgents() { agents = await api("/api/agents"); afficherAgents(); }

async function chargerJournal() {
  const lignes = await api("/api/journal");
  const corps = $("liste-journal"); corps.replaceChildren();
  for (const l of lignes) {
    const tr = corps.insertRow();
    cellule(tr, new Date(l.horodatage).toLocaleString("fr-FR"));
    cellule(tr, l.administrateur); cellule(tr, ACTIONS[l.action] ?? l.action);
    cellule(tr, l.agent); cellule(tr, l.motif, "motif");
    badge(tr.insertCell(), l.succes ? "Réussie" : "Incomplète", l.succes ? "ok" : "ko");
    cellule(tr, l.rapport.duration_ms === undefined ? "—" : secondes(l.rapport.duration_ms));
  }
  if (!lignes.length) ligneVide(corps, "Aucune opération pour l'instant.", 7);
}

// --- Révocation -------------------------------------------------------------

function ouvrirRevocation(agent) {
  cible = agent;
  $("d-nom").textContent = agent.nom || agent.identifiant;
  $("d-identifiant").textContent = [agent.identifiant, agent.ministere].filter(Boolean).join(" · ");
  $("motif").value = "";
  $("formulaire-revocation").hidden = false; $("resultat-revocation").hidden = true;
  $("confirmer").disabled = false; $("confirmer").textContent = "Révoquer l'agent";
  $("dialogue-revocation").showModal(); $("motif").focus();
}

function afficherRapport(r) {
  $("formulaire-revocation").hidden = true; $("resultat-revocation").hidden = false;
  $("r-titre").textContent = r.success ? `${cible.nom || cible.identifiant} : accès révoqué` : "Révocation incomplète";
  $("r-titre").className = r.success ? "succes" : "echec";
  const liste = $("r-etapes"); liste.replaceChildren();
  for (const e of r.steps) {
    const li = document.createElement("li"); li.className = e.ok ? "ok" : "ko";
    const icone = document.createElement("span"); icone.className = "icone";
    icone.textContent = e.ok ? "✓" : "✗"; icone.setAttribute("aria-label", e.ok ? "réussi" : "échec");
    const texte = document.createElement("span"); texte.textContent = e.name;
    li.append(icone, texte);
    if (e.detail) { const d = document.createElement("small"); d.textContent = e.detail; li.append(d); }
    liste.append(li);
  }
  $("r-meta").textContent = `Durée totale : ${secondes(r.duration_ms)}`;
  $("resultat-revocation").querySelector("[data-fermer]").focus();
}

$("formulaire-revocation").addEventListener("submit", async (evenement) => {
  evenement.preventDefault();
  $("confirmer").disabled = true; $("confirmer").textContent = "Révocation en cours…";
  try {
    afficherRapport(await api(`/api/agents/${encodeURIComponent(cible.identifiant)}/revoke`,
                              { method: "POST", body: JSON.stringify({ motif: $("motif").value }) }));
  } catch (err) {
    afficherRapport({ success: false, duration_ms: 0, steps: [{ name: "Révocation", ok: false, detail: err.message }] });
  }
});

// --- Enrôlement -------------------------------------------------------------

// « Jean-Marie Ouédraogo » → « jean-marie.ouedraogo » (contrat C1 : minuscules, point et tiret)
const normaliser = (texte) => texte.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase()
  .replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");

function afficherMxid() {
  $("e-mxid").textContent = `@${$("e-identifiant").value || "prenom.nom"}:${moi?.serveur ?? ""}`;
}

function proposerIdentifiant() {
  if (identifiantModifie) return;
  $("e-identifiant").value = [normaliser($("e-prenom").value), normaliser($("e-nom").value)].filter(Boolean).join(".");
  afficherMxid();
}

function ouvrirEnrolement() {
  $("formulaire-enrolement").reset(); identifiantModifie = false; afficherMxid();
  $("e-erreur").hidden = true;
  $("formulaire-enrolement").hidden = false; $("resultat-enrolement").hidden = true;
  $("e-confirmer").disabled = false; $("e-confirmer").textContent = "Enrôler l'agent";
  $("dialogue-enrolement").showModal(); $("e-prenom").focus();
}

$("formulaire-enrolement").addEventListener("submit", async (evenement) => {
  evenement.preventDefault();
  $("e-confirmer").disabled = true; $("e-confirmer").textContent = "Enrôlement en cours…";
  $("e-erreur").hidden = true;
  const demande = Object.fromEntries(["identifiant", "prenom", "nom", "ministere", "fonction"]
    .map((champ) => [champ, $(`e-${champ}`).value]));
  try {
    const r = await api("/api/agents", { method: "POST", body: JSON.stringify(demande) });
    $("formulaire-enrolement").hidden = true; $("resultat-enrolement").hidden = false;
    $("re-titre").textContent = `${demande.prenom} ${demande.nom} est enrôlé(e)`;
    $("re-mxid").textContent = r.mxid;
    $("re-mdp").textContent = r.mot_de_passe_provisoire;
    $("re-copier").textContent = "Copier";
    $("re-copier").focus();
  } catch (err) {
    $("e-erreur").textContent = err.message; $("e-erreur").hidden = false;
    $("e-confirmer").disabled = false; $("e-confirmer").textContent = "Enrôler l'agent";
  }
});

$("re-copier").addEventListener("click", async () => {
  await navigator.clipboard.writeText($("re-mdp").textContent);
  $("re-copier").textContent = "Copié";
});
$("e-prenom").addEventListener("input", proposerIdentifiant);
$("e-nom").addEventListener("input", proposerIdentifiant);
$("e-identifiant").addEventListener("input", () => { identifiantModifie = true; afficherMxid(); });
$("nouvel-agent").addEventListener("click", ouvrirEnrolement);

// --- Commun -----------------------------------------------------------------

for (const bouton of document.querySelectorAll("[data-fermer]")) {
  bouton.addEventListener("click", () => bouton.closest("dialog").close());
}
for (const dialogue of document.querySelectorAll("dialog")) {
  dialogue.addEventListener("close", () => {
    $("re-mdp").textContent = "";  // le mot de passe provisoire ne reste pas dans la page
    if (!dialogue.querySelector("[id^='resultat']").hidden) chargerAgents().catch((err) => erreur(err.message));
  });
}
$("recherche").addEventListener("input", afficherAgents);

for (const onglet of document.querySelectorAll("[data-onglet]")) {
  onglet.addEventListener("click", () => {
    for (const o of document.querySelectorAll("[data-onglet]")) {
      o.setAttribute("aria-selected", o === onglet);
      $(o.dataset.onglet).hidden = o !== onglet;
    }
    if (onglet.dataset.onglet === "journal") chargerJournal().catch((err) => erreur(err.message));
  });
}

(async () => {
  try {
    moi = await api("/api/moi");
    $("admin").textContent = moi.nom || moi.identifiant;
    $("deconnexion").href = moi.deconnexion;
    await chargerAgents();
  } catch (err) { erreur(err.message); }
})();
