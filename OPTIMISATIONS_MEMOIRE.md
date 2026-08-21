# 🛡️ Optimisations Mémoire pour Render.com (512 MB)

## Problème
Votre application Flask RecrutBank crashait à cause de la limite de mémoire de 512 MB sur Render.com.

## Solutions Implémentées

### 1. **Désactivation de spacy par défaut** (~50-100 MB économisés)
Spacy est une bibliothèque NLP lourde qui charge des modèles de langue en mémoire.

**Solution :** Ajoutez cette variable d'environnement sur Render :
```
SPACY_ENABLED=false
```

**Impact :** 
- ✅ L'application utilise moins de mémoire au démarrage
- ✅ Le moteur mots-clés reste fonctionnel
- ⚠️ L'enrichissement NLP (extraction d'entités) est désactivé

**Pour réactiver spacy** (si vous passez à 1GB+) :
```
SPACY_ENABLED=true
```

---

### 2. **Réduction du parallélisme** (~30-50 MB économisés)
Les ThreadPoolExecutor créent plusieurs threads qui consomment de la mémoire.

**Variables à configurer sur Render :**
```bash
MAX_PARALLEL_WORKERS=1        # 1 worker = traitement séquentiel
IA_MAX_CONCURRENCY=1          # 1 appel IA à la fois
ZIP_DOWNLOAD_WORKERS=4        # Limite pour les téléchargements ZIP
```

**Impact :**
- ✅ Moins de pression mémoire pendant les analyses
- ✅ Évite les crashes OOM (Out Of Memory)
- ⚠️ Les analyses sont plus lentes (séquentielles)

---

### 3. **Configuration Recommandée pour Render 512MB**

Dans le dashboard Render.com, ajoutez ces Environment Variables :

| Variable | Valeur | Description |
|----------|--------|-------------|
| `SPACY_ENABLED` | `false` | Désactive NLP lourd |
| `MAX_PARALLEL_WORKERS` | `1` | 1 analyse à la fois |
| `IA_MAX_CONCURRENCY` | `1` | 1 appel Claude à la fois |
| `ZIP_DOWNLOAD_WORKERS` | `4` | Max 4 téléchargements ZIP |

---

## Comment Déployer

1. **Allez sur votre dashboard Render.com**
2. **Sélectionnez votre service RecrutBank**
3. **Cliquez sur "Environment"**
4. **Ajoutez les variables ci-dessus**
5. **Redéployez l'application**

---

## Monitoring

Après déploiement, surveillez :
- **Memory Usage** dans le dashboard Render
- **Logs** pour vérifier qu'il n'y a pas d'erreurs OOM
- **Performance** des analyses (plus lentes mais stables)

---

## Alternative : Passer à 1GB+

Si les performances sont trop lentes, envisagez de passer au plan supérieur :
- **Render Standard ($7/mois)** : 1GB RAM
- Vous pourrez alors activer `SPACY_ENABLED=true` et `MAX_PARALLEL_WORKERS=2`

---

## Fichiers Modifiés

- `/workspace/server.py` : Optimisations mémoire implémentées
- `/workspace/.env.example` : Exemple de configuration
- `/workspace/OPTIMISATIONS_MEMOIRE.md` : Ce document

