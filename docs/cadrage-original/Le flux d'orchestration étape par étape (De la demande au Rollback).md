### **Le flux d'orchestration étape par étape (De la demande au Rollback)**

Pour comprendre comment toutes les briques s'articulent sans rien coder en dur, suivez le cycle de vie d'une action à travers les **5 couches** de l'infographie :

**1.1. Design Visuel, Marketplace & Estimation FinOps (Couche 1):**

* **Le Marketplace de Plugins :** Les administrateurs ou la communauté publient des plugins (Nginx, Apache, stack Front/Back, règles pfSense, VLAN Cisco) sous forme de dossiers Git standardisés. Avant d'apparaître dans le catalogue self-service, chaque plugin passe par un scan de sécurité automatique (*Checkov / Ansible-Lint*).  
* **Le Canvas Drag & Drop :** L'utilisateur glisse ses briques sur l'interface web (React Flow) et les relie entre elles.  
* **Calcul FinOps en temps réel :** Dès qu'un bloc est posé sur le diagramme, l'interface interroge *Infracost* (pour le Cloud) ou votre grille tarifaire interne (pour le On-Premise / VMware) afin d'afficher instantanément le coût mensuel estimé et d'éviter le sur-provisionnement.

**2.2. Garde-fous DevSecOps, Secrets & Validation ChatOps (Couches 2 & 3):**

* **Vérification des droits (RBAC) et Gouvernance (OPA) :** Lorsque l'utilisateur clique sur *Déployer*, le cœur **FastAPI** intercepte le schéma JSON et vérifie les droits d'accès (Admin / Utilisateur). Le moteur de règles (*Open Policy Agent*) bloque immédiatement toute action non autorisée (ex. : ouverture d'un port critique ou dépassement de quota).  
* **Approbation via Messageries (ChatOps) :** Si la règle exige une validation managériale ou concerne la production, FastAPI envoie une notification interactive sur **Teams, Discord, Google Chat ou WhatsApp Business** (via *Apprise*). Le responsable valide en un clic depuis sa messagerie.  
* **Injection des Secrets (SOTA) :** Juste avant l'exécution, le backend récupère à la volée les clés SSH et tokens API chiffrés dans le coffre-fort (**OpenBao / Vault**) sans jamais les stocker en clair dans le code ou la base de données.

**3.3. Versionning GitOps & Déploiement Phasé avec Rollback (Couches 4 & 5):**

* **Versionning GitOps automatique :** La topologie validée est automatiquement traduite en fichiers de configuration et commitée dans le dépôt **Git** du client (traçabilité totale de *qui* a modifié *quoi* et *quand*).  
* **Phase A — Provisionnement (OpenTofu) :** Si de nouvelles machines ou conteneurs sont requis, **OpenTofu** provisionne l'infrastructure sur les cibles hybrides (VMware, Proxmox, Cloud public, Docker ou Bare-metal).  
* **Phase B — Configuration en masse phasée (Ansible) :** Pour configurer les serveurs web (Apache/Nginx) ou pousser des règles réseau en masse sur **Cisco et pfSense**, le worker lance **Ansible** en mode progressif (*Canary / Rolling Update*) :  
  1. **Snapshot / Backup automatique** de la configuration actuelle du switch Cisco ou du pfSense.  
  2. **Déploiement sur 1 équipement pilote** (Phase 1), suivi d'un test de connectivité automatique (*Healthcheck*).  
  3. **Propagation au reste du parc** (Phase 2 \-\> 100 %) si le test réussit, ou **Rollback automatique** immédiat (restauration du backup de l'étape 1\) si l'équipement pilote ne répond plus.

**4.4. Observabilité, BI & Agent IA en Lecture Seule (Boucle de rétroaction):**

* **Monitoring & Logs centralisés :** Durant tout le déploiement, les journaux d'exécution sont streamés en direct dans l'interface web et archivés dans **Prometheus / Loki / Grafana** pour assurer le suivi et la traçabilité.  
* **Agent IA Read-Only (Diagnostic & FinOps) :** Connecté exclusivement en **lecture seule** aux logs d'exécution, à l'état Git/OpenTofu et aux métriques de consommation, le plugin d'IA agentique intervient sur deux plans :  
  * **En cas d'échec ou de Rollback :** Il lit la trace d'erreur Ansible/OpenTofu, diagnostique la cause racine en langage clair et propose le correctif exact à appliquer.  
  * **En continu (Audit & BI) :** Il repère les anomalies de sécurité ou les ressources sous-utilisées (« serveurs fantômes ») et génère des recommandations d'optimisation soumises à validation humaine.

### **Synthèse des flux de données entre les briques**

| Composant de l'infographie | Entrée (Ce qu'il reçoit) | Sortie (Ce qu'il produit) | Garantie Sécurité / Gouvernance |
| :---- | :---- | :---- | :---- |
| **Portail & Canvas (Next.js)** | Glisser-déposer de plugins par l'utilisateur | Graphe JSON de l'infrastructure souhaitée \+ Devis FinOps | Formulaires typés empêchant les erreurs de saisie |
| **Cœur FastAPI \+ OPA** | Graphe JSON \+ Identité de l'utilisateur | Demande validée ou rejetée \+ Notification ChatOps | RBAC strict \+ Blocage des configurations interdites |
| **OpenBao / Vault** | Demande de jeton temporaire par FastAPI | Clés SSH / API injectées uniquement en mémoire vive | Secrets protégés, zéro clé stockée dans Git |
| **Moteur GitOps & IaC** | Code OpenTofu \+ Playbooks Ansible \+ Secrets | Déploiement phasé sur VMware, Cloud, Docker, Cisco, pfSense | Sauvegarde pré-déploiement \+ Rollback auto si perte réseau |
| **Agent IA (Plugin)** | Logs d'erreurs, états .tfstate et métriques (**Read-Only**) | Diagnostic de panne, alertes Teams/Discord et recommandations FinOps | Désactivable et toujours soumis à validation humaine |

Souhaitez-vous que l'on zoom sur l'un des sous-systèmes (par exemple **le schéma de base de données et d'API de FastAPI**, ou **le fonctionnement exact du Rollback Ansible sur Cisco/pfSense**) pour préparer vos spécifications techniques ?

