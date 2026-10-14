# ═══════════════════════════════════════════════════════
# Moteur d analyse des CV par IA (prompt, appels multi-modeles, scoring)
# Contenu repris verbatim de l ancien server.py monolithique
# (refactor purement structurel — aucune logique modifiee)
# ═══════════════════════════════════════════════════════
import json
import re
import datetime
from ..config import (logger, GRILLE, ACTIVE_MODELS, IA_ANALYSE_ACTIVE,
                     _ia_semaphore, OPENROUTER_REASONING_ENABLED)
from ..data.grille import GRILLE as _GRILLE_CHECK  # noqa: F401  (garde la coherence des donnees)

def parse_json_robust(result_text):
    import re as re_json
    if not result_text:
        return None
    try:
        analyse = json.loads(result_text.strip())
        logger.info("✅ JSON parse avec succes")
        return analyse
    except json.JSONDecodeError as e:
        logger.warning(f"⚠️ Erreur parsing JSON direct: {e}")
        cleaned_text = result_text
        cleaned_text = re_json.sub(r'//.*?$', '', cleaned_text, flags=re_json.MULTILINE)
        cleaned_text = re_json.sub(r',(\s*[}\]])', r'\1', cleaned_text)
        cleaned_text = re_json.sub(r'[\x00-\x1F\x7F]', '', cleaned_text)
        cleaned_text = re_json.sub(r'```json\s*', '', cleaned_text)
        cleaned_text = re_json.sub(r'\s*```', '', cleaned_text)
        cleaned_text = re_json.sub(r'^[^{]*', '', cleaned_text)
        cleaned_text = re_json.sub(r'[^}]*$', '', cleaned_text)
        try:
            analyse = json.loads(cleaned_text)
            logger.info("✅ JSON parse apres nettoyage")
            return analyse
        except json.JSONDecodeError as e2:
            logger.warning(f"⚠️ Erreur parsing JSON apres nettoyage: {e2}")
            json_patterns = [
                r'(?:```json\s*)?(\{[\s\S]*?\})(?:\s*```)?',
                r'(\{[\s\S]*\})',
                r'(\[[\s\S]*\])'
            ]
            for pattern in json_patterns:
                matches = re_json.findall(pattern, cleaned_text)
                if matches:
                    for match in matches:
                        try:
                            parsed = json.loads(match.strip())
                            if isinstance(parsed, (dict, list)):
                                logger.info("✅ JSON extrait avec regex")
                                return parsed
                        except json.JSONDecodeError:
                            continue
            logger.error("❌ Aucun JSON trouve dans la reponse")
            return None
def get_recommandation_from_score(score, flags_elim=None, score_max=14):
    try:
        s = float(score) if score is not None else 0
    except (ValueError, TypeError):
        s = 0
    if flags_elim and len(flags_elim) > 0:
        return "Rejet - Critere(s) eliminatoire(s) non satisfait(s)"
    if s >= 11:
        return "Entretien prioritaire"
    elif s >= 7:
        return "Potentiel a evaluer en entretien"
    else:
        return "Rejet"
def get_statut_from_decision(decision, flags_elim=None):
    if flags_elim and len(flags_elim) > 0:
        return "rejete"
    if not decision:
        return "en_attente"
    if "Entretien prioritaire" in decision or "Shortlist" in decision:
        return "retenu"
    elif "Potentiel" in decision or "considerer" in decision or "Faible" in decision:
        return "entretien"
    else:
        return "rejete"
def sort_candidats(candidats):
    """
    Trie les candidats par :
    1. Statut (retenu en premier, puis entretien, puis en_attente, puis rejete)
    2. Score decroissant (plus eleve d'abord)
    """
    statut_order = {'retenu': 0, 'entretien': 1, 'en_attente': 2, 'rejete': 3}
    def get_score(candidat):
        try:
            return float(candidat.get('score', 0))
        except (ValueError, TypeError):
            return 0.0
    return sorted(candidats, key=lambda x: (
        statut_order.get(x.get('statut', 'en_attente'), 99),
        -get_score(x)
    ))
def build_ia_prompt(poste, cv_text, lettre_text, attestation_texts_list):
    grille = GRILLE.get(poste, {})
    if not grille:
        return None, None
    regles_interpretation = """
🔴 REGLES D'INTERPRETATION DES POSTES (A RESPECTER ABSOLUMENT) :
1. "Acting Branch Manager" ou "Chef d'Agence" = CHEF D'AGENCE → MANAGEMENT VALIDE (3/3)
2. "Profit Center Manager" = MANAGEMENT D'EQUIPE AVEC P&L → MANAGEMENT VALIDE (3/3)
3. "Chef de service" + "Supervision de l'equipe" = MANAGEMENT D'EQUIPE VALIDE (2-3/3)
4. "Supervision des activites commerciales" = MANAGEMENT VALIDE
5. "Encadrer, diriger et motiver les gestionnaires" = MANAGEMENT D'EQUIPE VALIDE
🔴 REGLES D'INTERPRETATION DES CERTIFICATIONS :
1. "Frankfurt School" = CERTIFICATION BANCAIRE VALABLE (+1)
2. "Moody's" = CERTIFICATION RISQUE VALABLE (+1)
3. "ITB" = CERTIFICATION BANCAIRE VALABLE (+1)
4. "Certificat d'Expert en Financement des PME" = CERTIFICATION VALABLE (+1)
🔴 CAS PARTICULIERS :
1. Chef d'agence (meme interimaire) = EXEMPTE des criteres eliminatoires 4, 5, 6
2. Bac+3 + 10+ ans d'experience = DIPLOME VALIDE (compensation)
3. Les postes de management avec supervision d'equipe sont VALIDES meme sans chiffres
4. Un profil avec 17 ans d'experience bancaire est CONSIDERE comme senior
5. La gestion de portefeuille Local Corporate valide le critere d'experience corporate
6. L'analyse et montage de dossiers de credit valide le critere risque de credit
"""
    system_prompt = f"""Tu es un consultant senior en recrutement bancaire avec 20 ans d'experience en Afrique centrale et de l'Ouest (CEMAC/UEMOA).
POSTE ANALYSE : {poste}
DESCRIPTION : {grille.get('description', 'Poste bancaire')}
Tu dois analyser STRICTEMENT selon la grille officielle fournie. Tu es le SEUL moteur d'analyse.
{regles_interpretation}
REGLES ABSOLUES D'ANALYSE :
1. Tu DOIS comprendre le SENS des phrases et le CONTEXTE METIER.
2. Tu ne JAMAIS inventer des faits qui ne sont PAS dans les documents.
3. Si une information n'est PAS mentionnee, elle est ABSENTE.
4. Les STAGES, BENEVOLATS et FORMATIONS ne comptent PAS comme experience pro.
5. Tu JUSTIFIES chaque evaluation avec des CITATIONS du CV/lettre.
6. Tu utilises le contexte CEMAC/UEMOA (COBAC, BEAC, reglementation locale).
7. Une interpretation RAISONNABLE est attendue : un profil avec 17 ans d'experience bancaire est senior.
8. Les postes de management (Acting Branch Manager, Profit Center Manager, Chef de service) sont consideres comme de l'experience manageriale VALIDE.
🔴 CRITERES ELIMINATOIRES (rejet immediat si non satisfaits) :
{chr(10).join(f"- {c}" for c in grille.get('eliminatoire', []))}
🟠 POINTS A VERIFIER dans le CV :
{chr(10).join(f"- {c}" for c in grille.get('a_verifier', []))}
🟡 SIGNAUX FORTS - candidat prioritaire :
{chr(10).join(f"- {c}" for c in grille.get('signaux_forts', []))}
⚠️ POINTS D'ATTENTION :
{chr(10).join(f"- {c}" for c in grille.get('points_attention', []))}
📊 GRILLE DE SCORING (MAX {grille.get('score_max_total', 14)}) :
{chr(10).join(f"- {k}: {v}/max" for k, v in grille.get('scores_max', {}).items())}
REGLES METIER SPECIFIQUES :
{chr(10).join(f"- {r}" for r in grille.get('regles_metier', []))}
FORMAT DE SORTIE : UNIQUEMENT du JSON valide.
{{
  "flags_eliminatoires": ["liste des criteres eliminatoires non satisfaits"],
  "points_forts": ["liste des points forts avec justifications"],
  "points_vigilance": ["liste des points de vigilance avec justifications"],
  "sous_scores": {{
    {', '.join(f'"{k}": 0-{v}' for k, v in grille.get('scores_max', {}).items())}
  }},
  "synthese_recruteur": "synthese professionnelle pour le recruteur (max 200 mots)",
  "checklist": {{
    "elim_0": true/false,
    "elim_1": true/false,
    "elim_2": true/false,
    "elim_3": true/false,
    "elim_4": true/false,
    "elim_5": true/false,
    "verif_0": true/false,
    "verif_1": true/false,
    "verif_2": true/false,
    "verif_3": true/false,
    "verif_4": true/false,
    "verif_5": true/false,
    "verif_6": true/false
  }},
  "profils_detectes": {{
    "chef_agence": false,
    "gestionnaire_portefeuille": false,
    "local_corporate": false,
    "cross_selling": false
  }},
  "banking_years": 0,
  "diplome_niveau": "Bac+3/Bac+4/Bac+5/Doctorat/Autre"
}}
IMPORTANT : La somme des sous_scores donne le score total. Les flags_eliminatoires doivent etre remplis si des criteres eliminatoires ne sont pas satisfaits. Sois precis et professionnel."""
    user_message = f"""=== DOCUMENTS A ANALYSER ===
CV DU CANDIDAT :
{cv_text[:12000]}
LETTRE DE MOTIVATION :
{lettre_text[:3000] if lettre_text else '(Aucune)'}
ATTESTATIONS :
{''.join(attestation_texts_list)[:3000] if attestation_texts_list else '(Aucune)'}
=== INSTRUCTIONS ===
Analyse strictement selon la grille. Verifie chaque critere eliminatoire.
Le score_total est la SOMME des sous-scores.
Fournis une checklist complete avec true/false pour chaque critere.
Sois rigoureux et professionnel.
INTERPRETE RAISONNABLEMENT les postes de management et les certifications."""
    return system_prompt, user_message
def call_ia_model(client, model, system_prompt, user_message):
    try:
        is_nemotron = "nemotron" in model.lower()
        supports_reasoning = is_nemotron
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            "temperature": 0.1,
            "max_tokens": 4096,
            "extra_headers": {
                "HTTP-Referer": "https://recrutment.onrender.com",
                "X-Title": "RecrutBank CV Analyzer"
            }
        }
        if OPENROUTER_REASONING_ENABLED and supports_reasoning:
            payload["reasoning"] = {"enabled": True}
            logger.info(f"🧠 Reasoning activé pour {model}")
        else:
            payload["response_format"] = {"type": "json_object"}
        response = client.chat.completions.create(**payload)
        if response is None:
            return None
        if not hasattr(response, 'choices') or response.choices is None or len(response.choices) == 0:
            return None
        if response.choices[0].message is None:
            return None
        content = response.choices[0].message.content
        if not content or len(content.strip()) < 10:
            return None
        if supports_reasoning and OPENROUTER_REASONING_ENABLED:
            import re as re_json
            json_match = re_json.search(r'\{[\s\S]*\}', content)
            if json_match:
                try:
                    json.loads(json_match.group(1))
                    return json_match.group(1)
                except:
                    pass
        return content
    except Exception as e:
        logger.warning(f"⚠️ Erreur avec ce modele: {e}")
        return None
def analyze_cv_with_ia_only(cv_text, lettre_text, attestation_texts_list, poste):
    if not IA_ANALYSE_ACTIVE or not ACTIVE_MODELS:
        logger.error("❌ Aucun modele IA disponible")
        return None
    if not cv_text or len(cv_text.strip()) < 50:
        logger.error("❌ CV trop court")
        return None
    if poste not in GRILLE:
        logger.error(f"❌ Poste non trouve dans la grille: {poste}")
        return None
    grille = GRILLE.get(poste, {})
    system_prompt, user_message = build_ia_prompt(poste, cv_text, lettre_text, attestation_texts_list)
    if not system_prompt or not user_message:
        return None
    result_text = None
    used_model = None
    for model_config in ACTIVE_MODELS:
        with _ia_semaphore:
            logger.info(f"🔄 Tentative avec {model_config['name']}...")
            result_text = call_ia_model(
                model_config["client"],
                model_config["model"],
                system_prompt,
                user_message
            )
            if result_text and len(result_text.strip()) > 50:
                used_model = model_config["name"]
                logger.info(f"✅ Analyse reussie avec {used_model}")
                break
            else:
                logger.warning(f"⚠️ {model_config['name']} n'a pas retourne de reponse valide")
    if not result_text or len(result_text.strip()) < 50:
        logger.error("❌ Tous les modeles IA ont echoue")
        return None
    logger.info(f"✅ Analyse IA terminee: {len(result_text)} caracteres avec {used_model}")
    analyse = parse_json_robust(result_text)
    if analyse is None:
        logger.error("❌ Echec de l'extraction JSON")
        return None
    sous_scores = analyse.get('sous_scores', {})
    max_scores = grille.get('scores_max', {})
    score_max_total = grille.get('score_max_total', 14)
    score_total = 0
    for key, max_val in max_scores.items():
        val = sous_scores.get(key, 0)
        if val > max_val:
            val = max_val
        if val < 0:
            val = 0
        sous_scores[key] = val
        score_total += val
    score_total = min(score_max_total, max(0, score_total))
    flags_elim = analyse.get('flags_eliminatoires', [])
    if not isinstance(flags_elim, list):
        flags_elim = []
    veritable_eliminatoire = grille.get('eliminatoire', [])
    flags_elim_corriges = []
    for flag in flags_elim:
        for critere in veritable_eliminatoire:
            if critere.lower() in flag.lower() or flag.lower() in critere.lower():
                flags_elim_corriges.append(flag)
                break
    flags_elim = flags_elim_corriges
    checklist = analyse.get('checklist', {})
    if not checklist or len(checklist) == 0:
        keys_list = list(max_scores.keys())
        checklist = {
            "elim_0": len(flags_elim) == 0,
            "elim_1": len(flags_elim) == 0,
            "elim_2": len(flags_elim) == 0,
            "elim_3": len(flags_elim) == 0,
            "elim_4": len(flags_elim) == 0,
            "elim_5": len(flags_elim) == 0,
            "verif_0": sous_scores.get(keys_list[0], 0) >= 2 if keys_list else False,
            "verif_1": sous_scores.get(keys_list[1], 0) >= 2 if len(keys_list) > 1 else False,
            "verif_2": sous_scores.get(keys_list[2], 0) >= 1 if len(keys_list) > 2 else False,
            "verif_3": sous_scores.get(keys_list[3], 0) >= 1 if len(keys_list) > 3 else False,
            "verif_4": sous_scores.get(keys_list[4], 0) >= 1 if len(keys_list) > 4 else False,
            "verif_5": sous_scores.get(keys_list[5], 0) >= 1 if len(keys_list) > 5 else False,
            "verif_6": sous_scores.get(keys_list[6], 0) >= 1 if len(keys_list) > 6 else False
        }
    decision = get_recommandation_from_score(score_total, flags_elim, score_max_total)
    statut = get_statut_from_decision(decision, flags_elim)
    profils = analyse.get('profils_detectes', {})
    banking_years = analyse.get('banking_years', 0)
    result = {
        'poste': poste,
        'score': score_total,
        'score_max': score_max_total,
        'decision': decision,
        'statut': statut,
        'flags_eliminatoires': flags_elim,
        'sous_scores': sous_scores,
        'checklist': checklist,
        'points_forts': analyse.get('points_forts', []),
        'points_vigilance': analyse.get('points_vigilance', []),
        'synthese': analyse.get('synthese_recruteur', ''),
        'score_breakdown': {
            'score_final': score_total,
            'score_max': score_max_total,
            'decision': decision,
            'statut': statut,
            'sous_scores': sous_scores,
            'chef_agence_detecte': profils.get('chef_agence', False),
            'gestionnaire_portefeuille_detecte': profils.get('gestionnaire_portefeuille', False),
            'local_corporate_detecte': profils.get('local_corporate', False),
            'cross_selling_detecte': profils.get('cross_selling', False),
            'banking_years_detecte': banking_years,
            'nb_eliminatoires': len(flags_elim),
            'eliminatoires_passes': len(flags_elim) == 0,
            'grille_version': 'v13.2-rapports-ameliiores',
            'moteur': 'IA_100%',
            'model_used': used_model
        },
        'analyse_details': {
            'moteur': 'IA_100%',
            'provider': used_model,
            'model_used': used_model,
            'sous_scores': sous_scores,
            'flags_eliminatoires': flags_elim,
            'points_forts': analyse.get('points_forts', []),
            'points_vigilance': analyse.get('points_vigilance', []),
            'synthese_recruteur': analyse.get('synthese_recruteur', ''),
            'profils_detectes': profils,
            'banking_years': banking_years,
            'diplome_niveau': analyse.get('diplome_niveau', 'Non specifie')
        }
    }
    return result
def run_analysis_for_candidat(token, cv_filename, lettre_filename, attestation_filenames, poste, force=False):
    try:
        if not force and not is_poste_actif(poste):
            logger.info(f"Analyse ignoree pour {token} — poste cloture : {poste}")
            if supabase:
                supabase.table('candidats').update({
                    "analyse_status": "skipped_closed_post",
                    "analyse_auto_date": datetime.datetime.now().isoformat()
                }).eq('token', token).execute()
            return
        if isinstance(attestation_filenames, str):
            try:
                attestation_filenames = json.loads(attestation_filenames) if attestation_filenames else []
            except Exception:
                attestation_filenames = [attestation_filenames] if attestation_filenames else []
        cv_text = ""
        if cv_filename:
            cv_bytes = download_file_from_supabase_robust(cv_filename)
            if cv_bytes:
                cv_text = extract_text_robust_from_bytes(cv_bytes, cv_filename)
                if len(cv_text) > MAX_TEXT_SIZE:
                    cv_text = cv_text[:MAX_TEXT_SIZE]
                logger.info(f"CV extrait pour {token}: {len(cv_text)} caracteres")
        if not cv_text or len(cv_text.strip()) < 30:
            logger.warning(f"CV manquant ou vide pour {token}")
            if supabase:
                supabase.table('candidats').update({
                    "score": "0",
                    "decision": "Rejet - CV manquant",
                    "statut": "rejete",
                    "analyse_status": "error"
                }).eq('token', token).execute()
            return
        lm_text = ""
        if lettre_filename:
            lm_bytes = download_file_from_supabase_robust(lettre_filename)
            if lm_bytes:
                lm_text = extract_text_robust_from_bytes(lm_bytes, lettre_filename)
                if len(lm_text) > MAX_TEXT_SIZE:
                    lm_text = lm_text[:MAX_TEXT_SIZE]
                logger.info(f"Lettre extraite pour {token}: {len(lm_text)} caracteres")
        att_texts = []
        for fn in (attestation_filenames or []):
            if fn:
                att_bytes = download_file_from_supabase_robust(fn)
                if att_bytes:
                    t = extract_text_robust_from_bytes(att_bytes, fn)
                    if t and len(t) > MAX_TEXT_SIZE:
                        t = t[:MAX_TEXT_SIZE]
                    if t:
                        att_texts.append(t)
        if not IA_ANALYSE_ACTIVE or not ACTIVE_MODELS:
            logger.error("❌ Aucun modele IA disponible - impossible d'analyser")
            if supabase:
                supabase.table('candidats').update({
                    "score": "0",
                    "decision": "Erreur - IA non disponible",
                    "statut": "rejete",
                    "analyse_status": "error"
                }).eq('token', token).execute()
            return
        result = analyze_cv_with_ia_only(cv_text, lm_text, att_texts, poste)
        if not result:
            logger.error(f"❌ Analyse IA echouee pour {token} (tous les modeles)")
            if supabase:
                supabase.table('candidats').update({
                    "analyse_status": "error",
                    "analyse_error": "L'analyse IA a echoue avec tous les modeles"
                }).eq('token', token).execute()
            return
        score = result.get('score', 0)
        score_max = result.get('score_max', 14)
        decision = result.get('decision', 'Rejet')
        statut = result.get('statut', 'rejete')
        flags_elim = result.get('flags_eliminatoires', [])
        sous_scores = result.get('sous_scores', {})
        score_breakdown = result.get('score_breakdown', {})
        checklist = result.get('checklist', {})
        points_forts = result.get('points_forts', [])
        points_vigilance = result.get('points_vigilance', [])
        synthese = result.get('synthese', '')
        analyse_details = result.get('analyse_details', {})
        if supabase:
            update_data = {
                "score": str(score),
                "decision": decision,
                "statut": statut,
                "analyse_status": "completed",
                "analyse_auto_date": datetime.datetime.now().isoformat(),
                "flags_eliminatoires": json.dumps(flags_elim, ensure_ascii=False),
                "score_breakdown": json.dumps(score_breakdown, ensure_ascii=False),
                "analyse_details": json.dumps(analyse_details, ensure_ascii=False),
                "checklist": json.dumps(checklist, ensure_ascii=False)
            }
            supabase.table('candidats').update(update_data).eq('token', token).execute()
        model_used = analyse_details.get('model_used', 'Inconnu')
        logger.info(f"[{decision}] Score {token}: {score}/{score_max} → statut: {statut} (modele: {model_used}, flags: {len(flags_elim)})")
    except Exception as e:
        import traceback
        traceback.print_exc()
        logger.error(f"Erreur analyse {token}: {str(e)}")
        if supabase:
            try:
                supabase.table('candidats').update({
                    "analyse_status": "error",
                    "analyse_error": str(e)
                }).eq('token', token).execute()
            except:
                pass
