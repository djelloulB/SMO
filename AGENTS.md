# Agent Strategy — SMO

Ce document décrit la stratégie des agents internes et externes dans SMO, et comment ils interagissent à travers le plan de contrôle MCP.

---

## Agents Internes (LangGraph)

Les agents internes sont des nœuds déterministes dans le pipeline de création de contenu. Ils sont implémentés avec LangGraph.

### Pipeline de contenu

```
trend_analyst → strategist → creator → moderator → human_gate → scheduler
```

### Agent par nœud

| Nœud | Rôle | Entrée | Sortie |
|---|---|---|---|
| **trend_analyst** | Résume les tendances | Sources (RSS, fichiers locaux, API news) | Résumé de tendances |
| **strategist** | Sélectionne les plateformes et la stratégie de timing | Résumé de tendances, historique d'engagement | Plan de contenu |
| **creator** | Rédige le contenu | Plan de contenu, ton de marque | `ContentPiece` avec variantes par plateforme |
| **moderator** | Vérifie la conformité et la sécurité | Contenu généré | Contenu approuvé ou bloqué |
| **human_gate** | Validation humaine (par défaut) | Contenu modéré | Contenu validé ou rejeté |
| **scheduler** | Planifie la publication | Contenu validé | Job APScheduler |

### Mémoire

- **Court terme** : LangGraph checkpointer (SQLite) — maintient l'état du workflow actuel.
- **Long terme** : Posts passés et métriques (SQLite) — utilisés pour sélectionner les meilleurs fenêtres de publication (few-shot) et pour l'apprentissage automatique.

---

## Agents Externes (MCP)

Les agents externes (Hermes, Claude Desktop, Cursor, etc.) sont des opérateurs qui pilotent SMO via le serveur MCP. Ils **ne sont pas** des nœuds dans le graphe LangGraph.

### Stratégie de séparations

- **Agents internes** → pipeline déterministe, contrôlé par le code (LangGraph).
- **Agents externes** → opérations via MCP, intervention humaine ou agent externe au dessus.

Cette séparation garantit que l'orchestrateur reste déterministe et traçable.

---

## Contrôle des agents (MCP)

### Scopes (permissifs)

| Scope | Outils |
|---|---|
| `read` (défaut) | `get_health`, `get_queue`, `list_drafts`, `get_draft`, `get_metrics` (`list_accounts` et `get_last_trend_report` ne sont pas encore implémentés) |
| `content` | `generate_content`, `approve_draft`, `reject_draft`, `schedule_post` |
| `control` | `publish_now`, `pause_platform`, `resume_platform`, `trigger_metrics_poll` |
| `admin` | `set_config`, `rotate_tokens` (très restreint, voir immutabilité ci-dessous) |

### Contrôles immuables (REC-002)

Les opérations `set_config` sont **non-sécuritaires** et ne peuvent pas modifier :
- `dry_run` (mode dry-run)
- `human_gate` (validation humaine)
- `budget_per_day_usd` (budget journalier)
- Les scopes MCP

Même un agent au scope `admin` ne peut désactiver ces garde-fous.

### Audit des actions (REC-008)

Toute action mutante est enregistrée dans `audit_actions` :
- Acteur (identifiant du clé agent, pas la clé elle-même)
- Outil utilisé
- Payload (haché pour la confidentialité)
- Résultat

Cela permet la traçabilité totale, même en cas de mauvaise action d'un agent externe.

---

## Workflow typique avec Hermes

1. **Hermes** appelle `generate_content(topic, platforms)`
2. **SMO** génère un brouillon (dry)
3. **Hermes** appelle `list_drafts` pour voir les options
4. **Hermes** appelle `approve_draft(id)`
5. **Hermes** appelle `schedule_post(id, strategy)`
6. **SMO** planifie le post selon la stratégie

Si `human_gate=true` (défaut), Hermès doit appeler `approve_draft` explicitement pour valider le contenu.

---

## Avenir

- **Phase 2** : Agents internes plus sophistiqués (automatisation plus avancée).
- **Phase 3** : Agents externes multi-agents (Hermes coordonnant plusieurs agents internes SMO).
- **Webhooks** (Phase 3) : Possibilité d'agir sur des événements en temps réel (répondre à mentions via l'interface de chaque plateforme).

---

## Références

- `SPECIFICATION.md` §4 — Agent Strategy
- `SECURITY.md` REC-002 — Couche immuable
- `SECURITY.md` REC-008 — Audit
