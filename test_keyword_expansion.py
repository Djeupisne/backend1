#!/usr/bin/env python3
"""Test des nouvelles fonctionnalités : mots-clés élargis et alertes de rejet"""

import sys
sys.path.insert(0, '/workspace')

from server import KEYWORD_MAPPING, check_criterion_semantic, normalize_for_matching

print("=" * 60)
print("TEST 1: Vérification des mots-clés élargis (FR + EN + synonymes)")
print("=" * 60)

# Tester quelques critères clés
test_criteria = [
    "Expérience en banque",
    "IFRS 9",
    "Management",
    "Compensation",
    "Cycle de crédit"
]

for criterion in test_criteria:
    keywords = KEYWORD_MAPPING.get(criterion, [])
    print(f"\n{criterion}:")
    print(f"  Nombre de mots-clés: {len(keywords)}")
    print(f"  Exemples: {keywords[:5]}")

print("\n" + "=" * 60)
print("TEST 2: Fonction check_criterion_semantic avec fallback robuste")
print("=" * 60)

# Simuler des textes de CV avec différentes formulations
test_cases = [
    {
        "criterion": "Expérience en banque",
        "cv_text": "J'ai travaillé 5 ans dans une institution financière comme analyste crédit.",
        "expected": True
    },
    {
        "criterion": "IFRS 9",
        "cv_text": "Connaissance des normes comptables internationales, notamment IFRS9 et le calcul des ECL.",
        "expected": True
    },
    {
        "criterion": "Management",
        "cv_text": "Responsable d'une équipe de 8 personnes, j'ai assuré l'encadrement et la formation des collaborateurs.",
        "expected": True
    },
    {
        "criterion": "Compensation",
        "cv_text": "Gestion des opérations de clearing et règlement-livraison à la BEAC.",
        "expected": True
    },
    {
        "criterion": "Cycle de crédit",
        "cv_text": "Maîtrise du loan process, de l'instruction dossier jusqu'au suivi post-déblocage.",
        "expected": True
    },
    {
        "criterion": "COBAC / conformité",
        "cv_text": "Expérience en regulatory compliance et lutte anti-blanchiment (AML/KYC).",
        "expected": True
    }
]

passed = 0
failed = 0

for i, test in enumerate(test_cases, 1):
    result, confidence, elements = check_criterion_semantic(
        test["criterion"],
        test["cv_text"],
        "",
        "Chargé(e) d'Administration de Crédit"
    )
    
    status = "✅ PASS" if result == test["expected"] else "❌ FAIL"
    if result == test["expected"]:
        passed += 1
    else:
        failed += 1
    
    print(f"\nTest {i}: {status}")
    print(f"  Critère: {test['criterion']}")
    print(f"  Texte: {test['cv_text'][:60]}...")
    print(f"  Résultat: {result} (confiance: {confidence:.2f})")
    print(f"  Éléments trouvés: {elements}")

print("\n" + "=" * 60)
print(f"RÉSUMÉ: {passed}/{len(test_cases)} tests réussis")
print("=" * 60)

if failed > 0:
    print(f"⚠️ {failed} test(s) échoué(s)")
    sys.exit(1)
else:
    print("🎉 Tous les tests sont passés avec succès !")
    sys.exit(0)
