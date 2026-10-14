# ═══════════════════════════════════════════════════════
# Grilles de preselection par poste (criteres, scores max, regles metier)
# Contenu repris verbatim de l ancien server.py monolithique
# (refactor purement structurel — aucune logique modifiee)
# ═══════════════════════════════════════════════════════
GRILLE = {
    "Chef de Division Local Corporate": {
        "eliminatoire": [
            "Aucune experience dans le secteur bancaire ou financier reglemente",
            "Niveau de diplome inferieur a Bac +4 (Master ou equivalent requis)",
            "Moins de 5 ans d'experience professionnelle, dont une partie significative en banque",
            "Aucune experience en gestion d'un portefeuille de clients SME (PME)/local corporate ou d'entreprises",
            "Aucune experience manageriale : ni encadrement d'equipe, ni pilotage d'une activite commerciale",
            "Aucune exposition a la gestion du risque de credit ou au suivi de la qualite d'un portefeuille (NPL, provisions)"
        ],
        "a_verifier": [
            "A pilote une activite Local Corporate/SME (PME) avec des objectifs chiffres (revenus, volumes, marges)",
            "A gere un portefeuille de clients Local Corporate/SME (PME) et demontre sa capacite a le developper",
            "A encadre et evalue une equipe commerciale ou bancaire",
            "A assure le suivi de la qualite du portefeuille de credit (NPL, CIR, provisions) et rendu compte a la direction",
            "A developpe des ventes croisees (cross-selling) ou des partenariats interdépartementaux",
            "A produit ou supervise des rapports de performance commerciale et financiere",
            "A une exposition a la reglementation bancaire locale (COBAC, BEAC) ou internationale"
        ],
        "signaux_forts": [
            "Pilotage d'une division ou d'une ligne Local Corporate/SME (PME) avec atteinte des objectifs de revenus et de portefeuille",
            "Gestion active du ratio NPL (creances douteuses) et du ratio cout/revenu (CIR) - resultats chiffres mentionnes",
            "Experience averee en cross-selling avec des equipes TSG, Trade Finance ou Cash Management",
            "Developpement reel du portefeuille Local Corporate : acquisition de clients, fidelisation, nombre de produits par client",
            "Leadership demontre : constitution d'equipe, developpement des collaborateurs, vivier de talents",
            "Certification Ecobank / Moody's ou ITB (Institut Technique de Banque) ou equivalent",
            "Connaissance du marche Local Corporate/SME (PME) tchadien ou de la zone CEMAC/UEMOA",
            "Exposition aux plateformes numeriques bancaires (OMNI, Cash Management ou equivalent)",
            "Resultats commerciaux quantifies et verifiables dans son CV (chiffres d'affaires, taux de croissance, NPS)"
        ],
        "points_attention": [
            "Parcours exclusivement back-office ou risques sans experience commerciale Local Corporate/SME (PME)",
            "Profil techniquement solide (credit, analyse) mais sans experience manageriale ni pilotage de P&L",
            "Experiences tres courtes (moins de 2 ans par poste) sans progression hierarchique visible",
            "CV sans resultats chiffres (missions decrites en responsabilites sans livrables ni indicateurs atteints)",
            "Mobilite geographique ou sectorielle excessive sans ancrage dans le secteur bancaire Local Corporate",
            "Trous inexpliques dans le parcours ou incoherences entre les postes declares"
        ],
        "scores_max": {
            "experience_corporate": 3,
            "management": 3,
            "risque_credit": 2,
            "cross_selling": 2,
            "coherence_parcours": 2,
            "qualite_cv": 1,
            "certification": 1
        },
        "regles_metier": [
            "Chef d'agence / Directeur d'agence : valide automatiquement le management, le portefeuille commercial et le risque de credit (NPL/provisions).",
            "Gestionnaire de portefeuille / Relationship Manager SME/Corporate : valide automatiquement l'exposition au risque de credit et la gestion des impayes.",
            "Bac+3 compensable par 10+ annees d'experience averee en SME/Local Corporate."
        ],
        "score_max_total": 14,
        "description": "Chef de Division Local Corporate - Pilotage d'une activite commerciale SME/Corporate"
    },
    "Data Analyst Finance": {
        "eliminatoire": [
            "Aucune formation en Finance, Comptabilite, Controle de gestion, Statistiques, Data Analytics ou Informatique decisionnelle",
            "Niveau de diplome inferieur a Bac +3",
            "Aucune experience en analyse financiere, reporting financier, controle de gestion, audit ou data analytics",
            "Aucune maitrise d'Excel (TCD, formules, Power Query) - competence incontournable et non negociable",
            "Aucune connaissance de la comptabilite ou des etats financiers (P&L, bilan, flux de tresorerie)"
        ],
        "a_verifier": [
            "A produit des rapports financiers periodiques (mensuels, trimestriels)",
            "A conçu ou maintenu des tableaux de bord financiers (Power BI, Excel ou autre outil BI)",
            "A realise des analyses Budget / Realise / N-1 avec identification des ecarts",
            "A travaille avec SQL pour extraire ou interroger des donnees financieres",
            "A assure la reconciliation de donnees multi-sources (comptabilite / systemes operationnels)",
            "A participe a l'elaboration d'un budget ou d'un forecast financier",
            "A une experience dans le secteur bancaire ou avec un Core Banking (FLEXCUBE, T24, Amplitude)"
        ],
        "signaux_forts": [
            "Maitrise explicite de Power BI (dashboards, DAX, Power Query) avec exemples de realisations concretes",
            "Experience averee en automatisation de reportings (Power Query, VBA, Python, outils ETL)",
            "Analyse d'ecarts Budget / Realise / N-1 avec presentation a la Direction Financiere ou a la DG",
            "Participation a la construction de modeles de prevision financiere ou d'analyses de scenarios",
            "Exposition aux donnees bancaires : PNB, NPL, cout du risque, rentabilite par agence ou produit",
            "Maitrise de SQL pour l'extraction et la manipulation de donnees en base relationnelle",
            "Connaissance de Python ou R pour des analyses statistiques avancees",
            "Mise en place de controles qualite sur les donnees et documentation des regles de calcul",
            "Resultats quantifies dans le CV : gains de productivite, delais reduits, anomalies detectees"
        ],
        "points_attention": [
            "Profil purement comptable sans exposition aux outils BI ou au reporting de gestion",
            "Profil exclusivement IT / developpeur sans connaissance financiere",
            "Experience uniquement academique ou stage sans production de reportings reels en environnement professionnel",
            "CV sans aucun outil cite nommement",
            "Missions decrites en termes generiques sans livrables precis ni resultats mesurables",
            "Trous inexpliques dans le parcours ou experiences tres courtes sans progression visible"
        ],
        "scores_max": {
            "experience_finance_data": 3,
            "outils_bi": 3,
            "sql": 2,
            "exposition_bancaire": 2,
            "coherence_parcours": 2,
            "qualite_cv": 1,
            "competences_avancees": 1
        },
        "regles_metier": [
            "Bac+3 compensable par 4-5 ans d'experience averee en analyse financiere et BI.",
            "La maitrise d'Excel (TCD, Power Query) est un pre-requis absolu et non negociable."
        ],
        "score_max_total": 14,
        "description": "Data Analyst Finance - Analyse de donnees financieres et reporting"
    }
}
GRILLE["Responsable Administration de Credit"] = {
    "eliminatoire": [
        "Aucune experience en administration de credit ou dans un service credit",
        "Niveau de diplome inferieur a Bac +3",
        "Moins de 3 ans d'experience dans le secteur bancaire",
        "Aucune connaissance des processus de montage et d'instruction de credit"
    ],
    "a_verifier": [
        "A instruit des dossiers de credit en respectant les procedures",
        "A assure la mise en place et le suivi des garanties",
        "A travaille avec un Core Banking (FLEXCUBE, T24)",
        "A participe a l'elaboration des rapports de suivi de portefeuille",
        "A une connaissance de la reglementation bancaire (COBAC)"
    ],
    "signaux_forts": [
        "Experience dans un service Administration de Credit",
        "Maitrise des outils de gestion de credit",
        "Connaissance des processus de recouvrement",
        "Certification en gestion de credit ou analyse financiere"
    ],
    "points_attention": [
        "Profil sans experience bancaire",
        "Experience uniquement en recouvrement sans instruction de credit",
        "CV sans precision sur le type de credits traites"
    ],
    "scores_max": {
        "experience_credit": 3,
        "management": 1,
        "risque_credit": 3,
        "cross_selling": 0,
        "coherence_parcours": 2,
        "qualite_cv": 1,
        "certification": 1
    },
    "regles_metier": [],
    "score_max_total": 11,
    "description": "Responsable Administration de Credit - Gestion des dossiers de credit"
}
GRILLE["Analyste Credit CCB"] = {
    "eliminatoire": [
        "Aucune experience en analyse de credit ou dans une banque",
        "Niveau de diplome inferieur a Bac +3",
        "Aucune connaissance de l'analyse financiere d'entreprises"
    ],
    "a_verifier": [
        "A realise des analyses financieres de dossiers de credit",
        "A produit des rapports d'analyse et des recommandations",
        "A travaille avec des outils d'analyse de credit",
        "A une connaissance des normes IFRS et de la reglementation"
    ],
    "signaux_forts": [
        "Experience en analyse de risque de credit",
        "Certification en analyse financiere",
        "Maitrise des ratios financiers et de l'analyse de bilan",
        "Connaissance du secteur SME/PME"
    ],
    "points_attention": [
        "Profil sans experience en analyse credit",
        "Experience uniquement en back-office sans analyse",
        "CV sans mention des methodes d'analyse utilisees"
    ],
    "scores_max": {
        "experience_credit": 3,
        "management": 0,
        "risque_credit": 3,
        "cross_selling": 0,
        "coherence_parcours": 2,
        "qualite_cv": 1,
        "certification": 1
    },
    "regles_metier": [],
    "score_max_total": 10,
    "description": "Analyste Credit CCB - Analyse de risque de credit"
}
GRILLE["Archiviste (Administration Credit)"] = {
    "eliminatoire": [
        "Aucune experience en archivage ou gestion documentaire",
        "Niveau de diplome inferieur a Bac +2"
    ],
    "a_verifier": [
        "A experience en gestion de dossiers physiques et electroniques",
        "Maitrise des outils de GED (Gestion Electronique de Documents)",
        "Connaissance des procedures de classification et d'archivage",
        "Experience dans le secteur bancaire ou financier"
    ],
    "signaux_forts": [
        "Certification en archivage ou GED",
        "Experience dans un service bancaire",
        "Connaissance des normes d'archivage (ISO)",
        "Maitrise de l'outil informatique"
    ],
    "points_attention": [
        "Experience uniquement en saisie sans gestion d'archives",
        "CV sans mention des outils utilises",
        "Parcours sans progression"
    ],
    "scores_max": {
        "experience_archivage": 3,
        "management": 0,
        "risque_credit": 0,
        "cross_selling": 0,
        "coherence_parcours": 2,
        "qualite_cv": 1,
        "certification": 1
    },
    "regles_metier": [],
    "score_max_total": 7,
    "description": "Archiviste - Gestion documentaire"
}
GRILLE["Senior Finance Officer"] = {
    "eliminatoire": [
        "Aucune experience en finance d'entreprise ou reporting financier",
        "Niveau de diplome inferieur a Bac +4",
        "Moins de 5 ans d'experience en finance"
    ],
    "a_verifier": [
        "A produit des rapports financiers periodiques",
        "A participe a l'elaboration des budgets",
        "A assure le suivi de la tresorerie",
        "A travaille avec des outils de gestion financiere (SAP, Oracle)",
        "A une experience en controle de gestion"
    ],
    "signaux_forts": [
        "Certification en finance (CFA, CPA)",
        "Experience en banque ou institution financiere",
        "Maitrise des normes IFRS",
        "Experience en audit ou controle interne"
    ],
    "points_attention": [
        "Profil sans experience bancaire",
        "Experience uniquement en comptabilite sans reporting",
        "CV sans mention des outils et methodes"
    ],
    "scores_max": {
        "experience_finance": 3,
        "management": 2,
        "risque_credit": 0,
        "cross_selling": 0,
        "coherence_parcours": 2,
        "qualite_cv": 1,
        "certification": 1
    },
    "regles_metier": [],
    "score_max_total": 9,
    "description": "Senior Finance Officer - Gestion financiere et reporting"
}
GRILLE["Market Risk Officer"] = {
    "eliminatoire": [
        "Aucune experience en gestion des risques de marche",
        "Niveau de diplome inferieur a Bac +4",
        "Aucune connaissance des produits financiers et des risques associes"
    ],
    "a_verifier": [
        "A evalue les risques de marche (taux, change, actions)",
        "A produit des rapports de risque (VaR, stress tests)",
        "A travaille avec des outils de risque (Murex, RiskMetrics)",
        "A une connaissance des normes Bâle III"
    ],
    "signaux_forts": [
        "Certification PRM (Professional Risk Manager)",
        "Experience en banque d'investissement",
        "Maitrise des modeles de risque avances",
        "Connaissance de la reglementation COBAC"
    ],
    "points_attention": [
        "Profil sans experience en risque de marche",
        "Experience uniquement en risque de credit",
        "CV sans mention des outils et methodes"
    ],
    "scores_max": {
        "experience_risque": 3,
        "management": 1,
        "risque_credit": 0,
        "cross_selling": 0,
        "coherence_parcours": 2,
        "qualite_cv": 1,
        "certification": 1
    },
    "regles_metier": [],
    "score_max_total": 8,
    "description": "Market Risk Officer - Gestion des risques de marche"
}
GRILLE["IT Reseau & Infrastructure"] = {
    "eliminatoire": [
        "Aucune experience en administration reseau ou infrastructure IT",
        "Niveau de diplome inferieur a Bac +3",
        "Aucune certification IT reconnue"
    ],
    "a_verifier": [
        "A administre des reseaux (Cisco, Juniper)",
        "A gere des infrastructures serveurs (Windows, Linux)",
        "A assure la securite des reseaux",
        "A experience en virtualisation (VMware, Hyper-V)",
        "A travaille dans le secteur bancaire ou financier"
    ],
    "signaux_forts": [
        "Certifications Cisco (CCNA, CCNP)",
        "Certifications Microsoft",
        "Experience en Cybersecurite",
        "Connaissance des normes de securite bancaire"
    ],
    "points_attention": [
        "Profil sans certification",
        "Experience uniquement en support sans administration",
        "CV sans mention des technologies maitrisees"
    ],
    "scores_max": {
        "experience_it": 3,
        "management": 2,
        "risque_credit": 0,
        "cross_selling": 0,
        "coherence_parcours": 2,
        "qualite_cv": 1,
        "certification": 1
    },
    "regles_metier": [],
    "score_max_total": 9,
    "description": "IT Reseau & Infrastructure - Gestion des infrastructures IT"
}
GRILLE["Auditeur interne"] = {
    "eliminatoire": [
        "Aucune experience en audit interne ou externe",
        "Niveau de diplome inferieur a Bac +4",
        "Aucune certification en audit"
    ],
    "a_verifier": [
        "A realise des missions d'audit",
        "A produit des rapports d'audit avec recommandations",
        "A une connaissance des normes d'audit",
        "A travaille dans le secteur bancaire",
        "A une connaissance de la reglementation"
    ],
    "signaux_forts": [
        "Certification CIA (Certified Internal Auditor)",
        "Experience en banque",
        "Connaissance des normes internationales d'audit",
        "Experience en audit des risques operationnels"
    ],
    "points_attention": [
        "Profil sans experience en audit",
        "Experience uniquement en comptabilite sans audit",
        "CV sans mention des missions d'audit"
    ],
    "scores_max": {
        "experience_audit": 3,
        "management": 2,
        "risque_credit": 0,
        "cross_selling": 0,
        "coherence_parcours": 2,
        "qualite_cv": 1,
        "certification": 1
    },
    "regles_metier": [],
    "score_max_total": 9,
    "description": "Auditeur interne - Audit et controle des risques"
}
GRILLE["Chef service controle des engagements"] = {
    "eliminatoire": [
        "Aucune experience en controle des engagements ou risque de credit",
        "Niveau de diplome inferieur a Bac +4",
        "Moins de 5 ans d'experience dans le secteur bancaire"
    ],
    "a_verifier": [
        "A supervise des activites de controle des engagements",
        "A assure le suivi du portefeuille de credits",
        "A produit des rapports de controle et de conformite",
        "A une connaissance de la reglementation bancaire",
        "A encadre une equipe"
    ],
    "signaux_forts": [
        "Experience en management d'equipe",
        "Connaissance des normes COBAC",
        "Certification en risque de credit",
        "Experience dans une banque internationale"
    ],
    "points_attention": [
        "Profil sans experience en controle",
        "Experience uniquement en analyse sans controle",
        "CV sans mention des indicateurs de suivi"
    ],
    "scores_max": {
        "experience_credit": 3,
        "management": 3,
        "risque_credit": 3,
        "cross_selling": 0,
        "coherence_parcours": 2,
        "qualite_cv": 1,
        "certification": 1
    },
    "regles_metier": [],
    "score_max_total": 13,
    "description": "Chef service controle des engagements - Supervision du controle credit"
}
GRILLE["Chef service IT (maintenance/support)"] = {
    "eliminatoire": [
        "Aucune experience en maintenance IT ou support",
        "Niveau de diplome inferieur a Bac +3",
        "Aucune experience en management d'equipe"
    ],
    "a_verifier": [
        "A supervise une equipe de maintenance IT",
        "A assure la gestion des incidents et des demandes",
        "A mis en place des procedures de maintenance",
        "A travaille avec des outils de ticketing",
        "A une connaissance des normes ITIL"
    ],
    "signaux_forts": [
        "Certification ITIL",
        "Experience en banque",
        "Connaissance des systemes bancaires (Core Banking)",
        "Management d'equipe IT"
    ],
    "points_attention": [
        "Profil sans experience en management",
        "Experience uniquement en developpement sans maintenance",
        "CV sans mention des outils de gestion"
    ],
    "scores_max": {
        "experience_it": 3,
        "management": 3,
        "risque_credit": 0,
        "cross_selling": 0,
        "coherence_parcours": 2,
        "qualite_cv": 1,
        "certification": 1
    },
    "regles_metier": [],
    "score_max_total": 10,
    "description": "Chef service IT - Supervision des activites IT"
}
GRILLE["Chef service finance"] = {
    "eliminatoire": [
        "Aucune experience en finance ou comptabilite",
        "Niveau de diplome inferieur a Bac +4",
        "Moins de 5 ans d'experience en finance",
        "Aucune experience en management"
    ],
    "a_verifier": [
        "A supervise les activites financieres",
        "A produit des rapports financiers",
        "A assure le reporting a la direction",
        "A participe a l'elaboration des budgets",
        "A une connaissance de la reglementation bancaire"
    ],
    "signaux_forts": [
        "Certification en finance (CFA, CPA)",
        "Experience en banque",
        "Maitrise des normes IFRS",
        "Connaissance des Core Banking",
        "Management d'equipe financiere"
    ],
    "points_attention": [
        "Profil sans experience en management",
        "Experience uniquement en comptabilite sans reporting",
        "CV sans mention des outils et methodes"
    ],
    "scores_max": {
        "experience_finance": 3,
        "management": 3,
        "risque_credit": 0,
        "cross_selling": 0,
        "coherence_parcours": 2,
        "qualite_cv": 1,
        "certification": 1
    },
    "regles_metier": [],
    "score_max_total": 10,
    "description": "Chef service finance - Supervision des activites financieres"
}
GRILLE["Chef service risques de marche"] = {
    "eliminatoire": [
        "Aucune experience en gestion des risques de marche",
        "Niveau de diplome inferieur a Bac +4",
        "Moins de 5 ans d'experience dans le secteur bancaire",
        "Aucune experience en management"
    ],
    "a_verifier": [
        "A supervise les activites de gestion des risques de marche",
        "A produit des rapports de risque (VaR, stress tests)",
        "A assure le reporting reglementaire",
        "A une connaissance des normes Bâle III",
        "A encadre une equipe"
    ],
    "signaux_forts": [
        "Certification PRM ou FRM",
        "Experience en banque d'investissement",
        "Connaissance de la reglementation COBAC",
        "Management d'equipe risque",
        "Experience en modelisation financiere"
    ],
    "points_attention": [
        "Profil sans experience en risque de marche",
        "Experience uniquement en risque de credit",
        "CV sans mention des methodes de calcul"
    ],
    "scores_max": {
        "experience_risque": 3,
        "management": 3,
        "risque_credit": 0,
        "cross_selling": 0,
        "coherence_parcours": 2,
        "qualite_cv": 1,
        "certification": 1
    },
    "regles_metier": [],
    "score_max_total": 10,
    "description": "Chef service risques de marche - Supervision des risques de marche"
}
GRILLE["Chef service reporting reglementaire"] = {
    "eliminatoire": [
        "Aucune experience en reporting reglementaire bancaire",
        "Niveau de diplome inferieur a Bac +4",
        "Moins de 5 ans d'experience dans le secteur bancaire",
        "Aucune experience en management"
    ],
    "a_verifier": [
        "A supervise la production de reportings reglementaires",
        "A assure la conformite aux normes COBAC/BEAC",
        "A produit des rapports pour les autorites de tutelle",
        "A une connaissance des normes prudentielles",
        "A encadre une equipe"
    ],
    "signaux_forts": [
        "Connaissance approfondie de la reglementation COBAC",
        "Experience en banque",
        "Certification en conformite bancaire",
        "Management d'equipe reporting",
        "Maitrise des outils de reporting"
    ],
    "points_attention": [
        "Profil sans experience en reporting reglementaire",
        "Experience uniquement en reporting interne",
        "CV sans mention des normes et reglementations"
    ],
    "scores_max": {
        "experience_reporting": 3,
        "management": 3,
        "risque_credit": 0,
        "cross_selling": 0,
        "coherence_parcours": 2,
        "qualite_cv": 1,
        "certification": 1
    },
    "regles_metier": [],
    "score_max_total": 10,
    "description": "Chef service reporting reglementaire - Supervision du reporting reglementaire"
}
GRILLE["Chef de Section Compensation"] = {
    "eliminatoire": [
        "Aucune experience en compensation ou operations bancaires",
        "Niveau de diplome inferieur a Bac +3",
        "Aucune experience en management"
    ],
    "a_verifier": [
        "A supervise les activites de compensation",
        "A assure la reconciliation des operations",
        "A travaille avec un Core Banking",
        "A une connaissance des procedures bancaires",
        "A encadre une equipe"
    ],
    "signaux_forts": [
        "Experience en banque",
        "Connaissance des systemes de compensation",
        "Management d'equipe operations",
        "Maitrise des outils bancaires"
    ],
    "points_attention": [
        "Profil sans experience en management",
        "Experience uniquement en back-office sans supervision",
        "CV sans mention des systemes utilises"
    ],
    "scores_max": {
        "experience_operations": 3,
        "management": 3,
        "risque_credit": 0,
        "cross_selling": 0,
        "coherence_parcours": 2,
        "qualite_cv": 1,
        "certification": 1
    },
    "regles_metier": [],
    "score_max_total": 10,
    "description": "Chef de Section Compensation - Supervision des activites de compensation"
}
GRILLE["Charge(e) d'Administration de Credit"] = {
    "eliminatoire": [
        "Aucune experience en administration de credit",
        "Niveau de diplome inferieur a Bac +3"
    ],
    "a_verifier": [
        "A instruit des dossiers de credit",
        "A assure le suivi administratif des dossiers",
        "A travaille avec un Core Banking",
        "A une connaissance des procedures de credit",
        "A assure le suivi des garanties"
    ],
    "signaux_forts": [
        "Experience dans un service credit bancaire",
        "Connaissance des processus de credit",
        "Maitrise des outils de gestion de credit",
        "Autonomie et rigueur demontrees"
    ],
    "points_attention": [
        "Profil sans experience bancaire",
        "Experience uniquement en recouvrement",
        "CV sans precision sur les types de credits traites"
    ],
    "scores_max": {
        "experience_credit": 3,
        "management": 0,
        "risque_credit": 2,
        "cross_selling": 0,
        "coherence_parcours": 2,
        "qualite_cv": 1,
        "certification": 1
    },
    "regles_metier": [],
    "score_max_total": 9,
    "description": "Charge(e) d'Administration de Credit - Gestion des dossiers de credit"
}
